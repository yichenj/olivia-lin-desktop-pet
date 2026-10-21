"""The raw retention boundary is shared by context and asynchronous memory."""
from pathlib import Path
import tempfile
import unittest

from backend.config import Config
from backend.context import HistoryContextProvider, NeedsCompaction
from backend.errors import ModelError
from backend.memory import MemoryStore, token_size
from backend.memory_worker import MemoryCompiler
from backend.storage import HistoryStore
from tests.test_memory import CompilingModel, call


class HistoryWindowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = HistoryStore(Path(self.directory.name) / 'chat.sqlite3')
        self.memory = MemoryStore(self.store)
        self.model = CompilingModel()
        self.compiler = MemoryCompiler(self.store, self.memory, self.model)
        self.context = HistoryContextProvider(self.store, 'persona', self.memory)

    async def asyncTearDown(self):
        await self.compiler.close()
        self.store.close()
        self.directory.cleanup()

    def retain(self, count):
        self.context.recent_messages = self.compiler.recent_messages = count

    def dialogue(self, text, reply='好的', tools=0):
        turn, mid = self.store.begin(text, 'test')
        for index in range(tools):
            with self.store.db:
                self.store.record_exchange(1, turn, call('get_subagent', agent_id=index + 2), {'status': 'completed'})
        self.store.finish(1, turn, mid, reply, 'completed')
        return [m for m in self.store.all_messages() if m['turn'] == turn or
                (m['role'] == 'user' and m['seq'] == self.store.message(mid)['seq'] - 1)]

    async def test_default_fifty_messages_age_out_once_and_survive_restart(self):
        self.assertEqual(Config().memory_recent_messages, 50)
        self.assertEqual(self.context.recent_messages, 50)
        for index in range(25):
            self.dialogue(f'原话-{index}', f'回答-{index}')
        original = self.store.all_messages()
        self.compiler.schedule()
        self.assertIsNone(self.compiler.timer)
        self.assertFalse(await self.compiler.wait())
        self.assertFalse(await self.compiler.wait())
        self.assertEqual(self.model.batches, [])
        self.assertFalse(self.memory.snapshot().version)
        self.assertIn('原话-0', str(self.context.build(1).messages))

        self.dialogue('原话-25', '回答-25')
        self.assertEqual([m['id'] for m in self.compiler.pending()], [m['id'] for m in original[:2]])
        self.assertTrue(await self.compiler.wait())
        self.assertFalse(self.compiler.pending())
        self.assertEqual(len(self.model.batches), 1)
        self.assertEqual(self.store.all_messages()[:50], original)
        self.assertNotIn('原话-0', str(self.context.build(1).messages))
        self.assertIn('原话-1', str(self.context.build(1).messages))

        await self.compiler.close()
        self.store.close()
        self.store = HistoryStore(Path(self.directory.name) / 'chat.sqlite3')
        self.memory = MemoryStore(self.store)
        self.compiler = MemoryCompiler(self.store, self.memory, self.model)
        self.compiler.schedule()
        self.assertIsNone(self.compiler.timer)
        self.assertFalse(await self.compiler.wait())
        self.assertEqual(len(self.model.batches), 1)

    async def test_tools_do_not_count_and_boundary_keeps_whole_exchange(self):
        self.retain(3)
        older = self.dialogue('较早原话', '较早回答')
        protected = self.dialogue('调用工具的原话', '工具之后的回答', tools=4)
        self.dialogue('最新原话', '最新回答')
        # Three dialogue messages cut at the middle exchange's final reply;
        # keep its initiating user plus all four tool pairs as well.
        self.assertEqual([m['id'] for m in self.compiler.pending()], [m['id'] for m in older])
        await self.compiler.wait()
        messages = self.context.build(1).messages
        self.assertIn('调用工具的原话', str(messages))
        self.assertNotIn('较早原话', str(messages))
        for index, message in enumerate(messages):
            if message.get('tool_calls'):
                self.assertEqual(messages[index + 1]['tool_call_id'], message['tool_calls'][0]['id'])
        self.assertEqual(sum(m['role'] == 'tool' for m in messages), 4)
        tool_end = max(i for i, m in enumerate(messages) if m['role'] == 'tool')
        reply = next(i for i, m in enumerate(messages) if m.get('content') == '工具之后的回答')
        self.assertLess(tool_end, reply)
        self.assertFalse(set(m['id'] for m in protected) & set(self.memory.snapshot().processed))

    async def test_superseded_draft_assigns_late_tool_result_to_original_exchange(self):
        self.retain(3)
        older = self.dialogue('可加工原话')
        turn, draft = self.store.begin('正在查询的原话', 'test')
        with self.store.db:
            tool_id = self.store.record_tool_call(1, turn, call('get_subagent', agent_id=2))
        new_turn, new_draft = self.store.steer(1, turn, draft, '不该进入上下文的草稿', '补充的新话语')
        with self.store.db:
            self.store.record_tool_result(tool_id, {'result': '迟到的结果'})
        self.store.finish(1, new_turn, new_draft, '最新回答', 'completed')
        self.assertEqual([m['id'] for m in self.compiler.pending()], [m['id'] for m in older])
        await self.compiler.wait()
        messages = self.context.build(1).messages
        self.assertIn('正在查询的原话', str(messages))
        self.assertIn('迟到的结果', str(messages))
        self.assertNotIn('不该进入上下文的草稿', str(messages))
        self.assertEqual(sum(m['role'] == 'tool' for m in messages), 1)

        # Once the original exchange ages out, the late result must not be
        # reassigned to the newer user merely because its seq is larger.
        self.retain(2)
        self.assertEqual([m['content'] for m in self.compiler.pending()], ['正在查询的原话'])
        await self.compiler.wait()
        self.assertNotIn('迟到的结果', str(self.context.build(1).messages))

    async def test_children_and_failed_drafts_do_not_use_retention_quota(self):
        self.retain(3)
        self.dialogue('需要继续保留的原话')
        child = self.store.create_agent()
        turn, mid = self.store.begin('子任务', 'test', child)
        self.store.finish(child, turn, mid, '子任务结果', 'completed')
        turn, mid = self.store.begin('最新原话', 'test')
        self.store.finish(1, turn, mid, '失败的草稿', 'failed')
        self.assertFalse(self.compiler.pending())
        messages = self.context.build(1).messages
        self.assertIn('需要继续保留的原话', str(messages))
        self.assertNotIn('子任务结果', str(messages))
        self.assertNotIn('失败的草稿', str(messages))

    async def test_oversized_protected_context_fails_without_compilation(self):
        self.retain(2)
        self.dialogue('大段原话' * 100, '大段回答' * 100)
        full = self.context.build(1)
        with self.assertRaises(ModelError) as failure:
            self.context.build(1, token_budget=token_size(full.messages) - 1)
        self.assertEqual(failure.exception.code, 'context_limit')
        self.assertFalse(self.compiler.pending())
        self.assertEqual(self.model.batches, [])

    async def test_previously_processed_recent_raw_is_never_shrunk(self):
        self.retain(4)
        self.dialogue('较早但仍在保护窗口里的原话' * 20)
        self.dialogue('最新原话')
        self.compiler.recent_messages = 0  # Simulate a version published before retention existed.
        await self.compiler.wait()
        self.compiler.recent_messages = 4
        full = self.context.build(1)
        self.assertIn('较早但仍在保护窗口里的原话', str(full.messages))
        with self.assertRaises(ModelError) as failure:
            self.context.build(1, token_budget=token_size(full.messages) - 1)
        self.assertEqual(failure.exception.code, 'context_limit')

    async def test_capacity_only_requests_eligible_older_dialogue(self):
        self.retain(2)
        older = self.dialogue('较早的长对话' * 200)
        protected = self.dialogue('最新原话')
        full = self.context.build(1)
        budget = token_size(full.messages) - 1
        request = self.context.build(1, token_budget=budget)
        self.assertIsInstance(request, NeedsCompaction)
        self.assertEqual(request.message_ids, [m['id'] for m in older])
        await self.compiler.wait()
        self.assertIn('最新原话', str(self.context.build(1, token_budget=budget).messages))
        self.assertFalse(set(m['id'] for m in protected) & set(self.memory.snapshot().processed))
