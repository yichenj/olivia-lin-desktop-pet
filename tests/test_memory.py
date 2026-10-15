"""File memory publication, grounding, bounded context and continuous input."""
import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from backend.context import HistoryContextProvider, ModelContext, NeedsCompaction
from backend.errors import ModelError
from backend.harness import AgentLoop, StreamPiece
from backend.local_tools import LocalTools
from backend.memory import MemoryStore, fingerprint, token_size
from backend.memory_worker import MemoryCompiler
from backend.runtime import AgentRuntime
from backend.service import ChatService
from backend.storage import HistoryStore


def call(name, **arguments):
    return {'id': 'call-' + name, 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}


class CompilingModel:
    """Produces a small grounded page; can pause compilation independently of chat."""
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = None
        self.batches = []

    async def stream(self, messages, **options):
        if any(t['function']['name'] == 'write_memory' for t in options.get('tools', [])):
            if messages[-1]['role'] == 'tool':
                yield StreamPiece(text='加工完成')
                return
            data = json.loads(messages[1]['content'])
            self.batches.append(data['raw'])
            self.entered.set()
            if self.release:
                await self.release.wait()
            mid = data['raw'][0]['id']
            page = f'events/{mid}.md'
            yield StreamPiece(tool_calls=[
                call('write_memory', path=page, content=f'# 工作偏好\n先讨论边界。\n[原话](raw:message/{mid})'),
                call('write_memory', path='index.md', content=data['index'] + f'\n- [工作偏好]({page})'),
                call('write_memory', path='hot.md', content=f'用户希望先明确边界。见[工作偏好]({page})')])
        else:
            yield StreamPiece(text='收到最新的话。')


class MemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = HistoryStore(self.root / 'chat.sqlite3')
        self.memory = MemoryStore(self.store)
        self.model = CompilingModel()
        self.compiler = MemoryCompiler(self.store, self.memory, self.model, interval=1000)
        self.context = HistoryContextProvider(self.store, 'persona', self.memory, recent_messages=2)
        self.loop = AgentLoop(self.context, self.model, compiler=self.compiler)
        self.runtime = AgentRuntime(self.store, self.context, self.loop, LocalTools(self.root, False), 'test')
        self.events = []
        self.service = ChatService(self.store, self.context, self.loop, 'test', self.events.append)

    async def asyncTearDown(self):
        await self.service.close()
        self.store.close()
        self.directory.cleanup()

    def dialogue(self, text='讨论技术方案时先明确边界', response='好的。'):
        turn, mid = self.store.begin(text, 'test')
        self.store.finish(1, turn, mid, response, 'completed')
        return self.store.all_messages()[-2:]

    async def publish_all(self):
        while self.compiler.pending():
            await self.compiler.wait()

    async def test_compile_links_raw_recall_and_restart(self):
        rows = self.dialogue()
        await self.publish_all()
        snapshot = self.memory.snapshot()
        self.assertIn('工作偏好', snapshot.files['index.md'])
        page = f"events/{rows[0]['id']}.md"
        self.assertIn(rows[0]['id'], self.memory.read(snapshot, page))
        self.assertEqual(self.memory.read_raw([rows[0]['id']])[0]['content'], rows[0]['content'])
        self.assertFalse(self.compiler.pending())
        reopened = MemoryStore(self.store)
        self.assertEqual(reopened.snapshot().version, snapshot.version)
        self.assertEqual(dict(reopened.snapshot().processed), dict(snapshot.processed))
        self.assertEqual(len(self.store.all_messages()), 2)

    async def test_only_completed_main_dialogue_is_compiled(self):
        self.dialogue()
        child = self.store.create_agent()
        turn, mid = self.store.begin('执行侧秘密资料', 'test', child)
        self.store.finish(child, turn, mid, '子结果', 'completed')
        turn, mid = self.store.begin('保留用户原话', 'test')
        with self.store.db:
            self.store.record_exchange(1, turn, call('read_memory', path='index.md'), {'content': '旧记忆'})
        self.store.finish(1, turn, mid, '失败草稿', 'failed')
        await self.publish_all()
        compiled = [m for batch in self.model.batches for m in batch]
        self.assertEqual([m['content'] for m in compiled], ['讨论技术方案时先明确边界', '好的。', '保留用户原话'])
        self.assertTrue(all(m['agent_id'] == 1 for m in compiled))

    async def test_late_draft_is_not_skipped_by_processing_cursor(self):
        turn, draft = self.store.begin('开始', 'test')
        await self.publish_all()
        self.store.finish(1, turn, draft, '后来才完成', 'completed')
        self.assertEqual([m['id'] for m in self.compiler.pending()], [draft])
        await self.publish_all()
        self.assertIn(draft, self.memory.snapshot().processed)

    async def test_new_input_does_not_restart_fixed_batch(self):
        original = self.dialogue()
        self.model.release = asyncio.Event()
        task = self.compiler.request()
        await self.model.entered.wait()
        later = self.dialogue('后来的新话')
        self.assertIs(self.compiler.request(), task)
        self.model.release.set()
        await task
        self.assertEqual({m['id'] for m in self.model.batches[0]}, {m['id'] for m in original})
        self.assertEqual({m['id'] for m in self.compiler.pending()}, {m['id'] for m in later})

    async def test_cancel_foreground_waiter_keeps_compiler_running(self):
        self.dialogue()
        self.model.release = asyncio.Event()
        waiter = asyncio.create_task(self.compiler.wait())
        await self.model.entered.wait()
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertFalse(self.compiler.task.done())
        self.model.release.set()
        await self.compiler.task
        self.assertFalse(self.compiler.pending())

    async def test_failure_before_pointer_switch_preserves_version_and_progress(self):
        self.dialogue()
        await self.publish_all()
        old = self.memory.snapshot()
        self.dialogue('新增约定')
        from backend.memory import atomic_text
        def fail_pointer(path, content):
            if path.name == 'CURRENT':
                raise OSError('simulated interrupted publication')
            return atomic_text(path, content)
        with patch('backend.memory.atomic_text', side_effect=fail_pointer):
            with self.assertRaises(ModelError):
                await self.compiler.wait()
        self.assertEqual(self.memory.snapshot().version, old.version)
        self.assertEqual(MemoryStore(self.store).snapshot().version, old.version)
        self.assertEqual(len(self.compiler.pending()), 2)

    async def test_invalid_links_and_paths_cannot_publish(self):
        rows = self.dialogue()
        base = self.memory.snapshot()
        for path, body in (('../escape.md', 'bad'), ('topics/a.md', 'no source'),
                           ('index.md', '[missing](topics/missing.md)')):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.memory.publish(base, {**base.files, path: body}, {}, [], {m['id'] for m in rows})
        self.assertFalse((self.memory.root / 'CURRENT').exists())
        self.assertEqual(self.memory.snapshot().version, '')

    async def test_failure_after_pointer_switch_keeps_live_and_disk_versions_equal(self):
        rows = self.dialogue()
        base = self.memory.snapshot()
        from backend.memory import atomic_text
        def fail_after_pointer(path, content):
            atomic_text(path, content)
            if path.name == 'CURRENT':
                raise OSError('simulated post-rename fsync failure')
        with patch('backend.memory.atomic_text', side_effect=fail_after_pointer):
            with self.assertRaises(OSError):
                self.memory.publish(base, dict(base.files), {m['id']: fingerprint(m) for m in rows}, [],
                                    {m['id'] for m in rows})
        self.assertNotEqual(self.memory.snapshot().version, base.version)
        self.assertEqual(self.memory.snapshot().version, MemoryStore(self.store).snapshot().version)
        self.assertFalse(self.compiler.pending())

    async def test_threshold_advances_existing_timer(self):
        self.compiler.threshold = 2000
        self.dialogue('短消息')
        self.compiler.schedule()
        timer = self.compiler.timer
        self.dialogue('更多内容' * 1000)
        self.compiler.schedule()
        self.assertTrue(timer.cancelled())
        await asyncio.wait_for(self.model.entered.wait(), 1)
        await self.compiler.task

    async def test_bound_to_database_and_checksums(self):
        self.dialogue()
        await self.publish_all()
        other = HistoryStore(self.root / 'other.sqlite3')
        try:
            with self.assertRaises(ModelError):
                MemoryStore(other, self.memory.root)
        finally:
            other.close()
        current = self.memory.snapshot()
        (self.memory.root / 'versions' / current.version / 'hot.md').write_text('changed')
        with self.assertRaises(ModelError):
            MemoryStore(self.store)

    async def test_forget_preserves_raw_and_blocks_old_snapshot_and_recompilation(self):
        rows = self.dialogue()
        await self.publish_all()
        old = self.memory.snapshot()
        request = self.dialogue('忘掉这个偏好')[0]
        await self.compiler.set_policy({'source_message_id': request['id'], 'action': 'forget',
            'instruction': '不再使用之前的工作偏好', 'message_ids': [rows[0]['id']], 'paths': []})
        with self.assertRaises(ValueError):
            self.memory.read(old, f"events/{rows[0]['id']}.md")
        with self.assertRaises(ValueError):
            self.memory.read_raw([rows[0]['id']])
        self.assertEqual(self.store.message(rows[0]['id'])['content'], rows[0]['content'])
        self.assertNotIn(rows[0]['id'], [m['id'] for m in self.compiler.pending()])
        recalled = self.memory.read_raw([rows[0]['id']], request['id'])
        self.assertEqual(recalled[0]['content'], rows[0]['content'])
        self.assertEqual(self.memory.snapshot().files['hot.md'], '')
        self.assertNotIn(rows[0]['content'], str(self.context.build(1).messages))

    async def test_correction_overrides_pinned_snapshot_and_stale_batch(self):
        self.dialogue()
        self.model.release = asyncio.Event()
        task = self.compiler.request()
        await self.model.entered.wait()
        source = self.dialogue('技术讨论需要详细解释')[0]
        await self.compiler.set_policy({'source_message_id': source['id'], 'action': 'correct',
            'instruction': '技术讨论需要详细解释', 'message_ids': [], 'paths': []})
        self.model.release.set()
        await task
        self.assertIn('技术讨论需要详细解释', self.memory.snapshot().files['policies.md'])
        self.assertEqual(len(self.model.batches), 2, 'Stale candidate must be regenerated against the correction')

    async def test_complete_context_budget_and_no_mutation_on_build(self):
        self.dialogue()
        before = dict(self.runtime.observed_results)
        full = self.context.build(1)
        self.assertEqual(self.runtime.observed_results, before)
        self.assertIn('read_memory', [t['function']['name'] for t in full.tools])
        budget = token_size(full.messages) + token_size(full.tools) - 1
        self.assertIsInstance(self.context.build(1, token_budget=budget), NeedsCompaction)
        child = self.store.create_agent()
        self.assertNotIn('read_memory', [t['function']['name'] for t in self.context.build(child).tools])

    async def test_processed_old_raw_is_replaced_by_index_not_runtime_duplicate(self):
        for i in range(4):
            self.dialogue(f'旧对话{i}' + '详细描述' * 100)
        await self.publish_all()
        self.dialogue('新的话语')
        context = self.context.build(1)
        self.assertIsInstance(context, ModelContext)
        self.assertNotIn('旧对话0', str(context.messages))
        self.assertIn('新的话语', str(context.messages))
        self.assertIn('记忆索引', str(context.messages))

    async def test_tool_recall_enters_next_model_request_and_has_source_payload(self):
        rows = self.dialogue()
        await self.publish_all()
        page = f"events/{rows[0]['id']}.md"
        seen = []
        class RecallModel:
            async def stream(self, messages, **options):
                seen.append(messages)
                if len(seen) == 1:
                    yield StreamPiece(tool_calls=[call('read_memory', path=page)])
                elif len(seen) == 2:
                    yield StreamPiece(tool_calls=[call('read_raw', message_ids=[rows[0]['id']])])
                else:
                    yield StreamPiece(text='你希望先明确边界。')
        self.loop.model = RecallModel()
        await self.service.handle({'method': 'chat/send', 'params': {'text': '我之前怎么说的？'}})
        current = self.service.active
        await current.task
        self.assertEqual(current.text, '你希望先明确边界。')
        self.assertEqual(len(seen), 3)
        tools = [m for m in seen[-1] if m['role'] == 'tool']
        self.assertIn(rows[0]['content'], tools[-1]['content'])
        calls = [m for m in self.store.agent_history(1) if m['payload'] and m['payload'].get('tool_calls')]
        self.assertEqual(len(calls), 2)
        raw = self.memory.read_raw([calls[0]['id']])[0]
        self.assertEqual(raw['payload']['tool_calls'][0]['function']['name'], 'read_memory')

    async def test_capacity_wait_accepts_new_input_and_continues_only_latest_turn(self):
        for _ in range(4):
            self.dialogue('很长的待加工资料' * 400)
        self.loop.token_budget = 20000
        self.model.release = asyncio.Event()
        await self.service.handle({'method': 'chat/send', 'params': {'text': '旧问题'}})
        old = self.service.active
        await asyncio.wait_for(self.model.entered.wait(), 1)
        await asyncio.wait_for(self.service.handle({'method': 'chat/send', 'params': {'text': '新的问题'}}), 1)
        latest = self.service.active
        self.assertTrue(old.task.cancelled())
        self.assertFalse(self.compiler.task.done())
        self.model.release.set()
        await asyncio.wait_for(latest.task, 5)
        self.assertEqual(latest.text, '收到最新的话。')
        self.assertEqual(self.store.message(old.message_id)['status'], 'superseded')
        self.assertEqual([m['content'] for m in self.store.all_messages() if m['role'] == 'user'][-2:], ['旧问题', '新的问题'])
        self.assertEqual(len([e for e in self.events if e['method'] == 'chat/completed']), 1)

    async def test_fixed_context_overflow_fails_without_infinite_compaction(self):
        self.context.system_prompt = '固定内容' * 1000
        with self.assertRaises(ModelError) as failure:
            self.context.build(1, token_budget=100)
        self.assertEqual(failure.exception.code, 'context_limit')
        self.assertIsNone(self.compiler.task)

    async def test_read_limits_and_tool_allowlist_enforced(self):
        self.dialogue()
        await self.publish_all()
        turn, mid = self.store.begin('查记忆', 'test')
        bad = await self.runtime.execute(1, turn, call('read_memory', path='../outside.md'))
        self.assertIn('error', bad)
        child = self.store.create_agent()
        child_turn, draft = self.store.begin('执行', 'test', child)
        result = await self.runtime.execute(child, child_turn, call('read_memory', path='index.md'))
        self.assertIsNone(result)
        self.assertIn('invalid_tool_arguments', self.store.all_messages()[-1]['content'])
        self.store.finish(child, child_turn, draft, '结束', 'completed')
        self.store.finish(1, turn, mid, '结束', 'completed')

    async def test_forgotten_old_tool_result_is_not_replayed(self):
        rows = self.dialogue('不再保留的合成信息')
        await self.publish_all()
        turn, draft = self.store.begin('查看之前信息', 'test')
        with self.store.db:
            self.store.record_exchange(1, turn, call('read_raw', message_ids=[rows[0]['id']]),
                                       self.memory.read_raw([rows[0]['id']]))
        self.store.finish(1, turn, draft, '已经核对', 'completed')
        source = self.dialogue('忘掉这个')[0]
        await self.compiler.set_policy({'source_message_id': source['id'], 'action': 'forget',
            'instruction': '停止使用该信息', 'message_ids': [rows[0]['id']], 'paths': []})
        self.context.recent_messages = 12
        messages = self.context.build(1).messages
        self.assertNotIn(rows[0]['content'], str(messages))
        self.assertIn('memory_disabled', str(messages))
        self.assertIn(rows[0]['content'], str(self.store.agent_history(1)), 'Audit raw remains intact')

    async def test_timer_compiles_only_when_pending_and_does_not_speak(self):
        self.compiler.interval = .01
        self.dialogue()
        self.compiler.schedule()
        await asyncio.wait_for(self.model.entered.wait(), 1)
        await self.compiler.task
        await asyncio.sleep(.02)
        self.assertFalse(self.compiler.pending())
        self.assertIsNone(self.compiler.timer)
        self.assertFalse(self.events)
        self.assertEqual(self.store.get_agent(1)['turn'], 1)

    async def test_memory_wait_is_separate_from_active_model_deadline(self):
        for _ in range(3):
            self.dialogue('待整理的对话' * 500)
        self.loop.token_budget = 20000
        self.loop.active_timeout = .08
        self.model.release = asyncio.Event()
        await self.service.handle({'method': 'chat/send', 'params': {'text': '继续说'}})
        current = self.service.active
        await asyncio.wait_for(self.model.entered.wait(), 1)
        await asyncio.sleep(.12)
        self.assertFalse(current.task.done())
        self.model.release.set()
        await asyncio.wait_for(current.task, 5)
        self.assertEqual(self.store.message(current.message_id)['status'], 'completed')

    async def test_context_rejection_retries_model_without_repeating_tool(self):
        requests = []
        class RejectOnce:
            async def stream(self, messages, **options):
                requests.append(messages)
                if len(requests) == 1:
                    yield StreamPiece(tool_calls=[call('read_memory', path='index.md')])
                elif len(requests) == 2:
                    raise ModelError('context_limit', 'synthetic overflow')
                else:
                    yield StreamPiece(text='已核对')
        self.loop.model = RejectOnce()
        await self.service.handle({'method': 'chat/send', 'params': {'text': '查一下'}})
        current = self.service.active
        await current.task
        self.assertEqual(current.text, '已核对')
        self.assertEqual(len(requests), 3)
        self.assertEqual(len([m for m in self.store.agent_history(1) if m['payload'] and m['payload'].get('tool_calls')]), 1)

    async def test_shutdown_during_compile_retains_pending_raw(self):
        self.dialogue()
        self.model.release = asyncio.Event()
        task = self.compiler.request()
        await self.model.entered.wait()
        await self.compiler.close()
        self.assertTrue(task.cancelled())
        reopened = MemoryStore(self.store)
        self.assertFalse(reopened.snapshot().processed)
        self.assertEqual(len(self.compiler.pending()), 2)

    async def test_shutdown_waits_for_inflight_publication(self):
        self.dialogue()
        entered = asyncio.Event()
        release = threading.Event()
        event_loop = asyncio.get_running_loop()
        original = self.memory.publish
        def delayed_publish(*args):
            event_loop.call_soon_threadsafe(entered.set)
            release.wait(3)
            return original(*args)
        with patch.object(self.memory, 'publish', side_effect=delayed_publish):
            self.compiler.request()
            await asyncio.wait_for(entered.wait(), 1)
            closing = asyncio.create_task(self.compiler.close())
            await asyncio.sleep(.02)
            self.assertFalse(closing.done())
            release.set()
            await asyncio.wait_for(closing, 2)
        self.assertTrue(MemoryStore(self.store).snapshot().processed)

    async def test_pinned_read_snapshot_and_current_publication_are_complete(self):
        self.dialogue()
        await self.publish_all()
        old = self.memory.snapshot()
        self.dialogue('新增信息')
        await self.publish_all()
        self.assertNotEqual(self.memory.snapshot().version, old.version)
        self.assertEqual(self.memory.read(old, 'index.md'), old.files['index.md'])
        self.assertNotEqual(self.memory.read(old, 'index.md'), self.memory.snapshot().files['index.md'])

    async def test_compiler_does_not_take_business_slot_or_block_normal_chat(self):
        self.dialogue()
        self.model.release = asyncio.Event()
        task = self.compiler.request()
        await self.model.entered.wait()
        self.runtime.on_result = lambda: None
        with self.store.db:
            child = self.store.queue_child('独立执行', self.store.latest_user_id())
        self.runtime.pump()
        worker = self.runtime.worker
        await asyncio.wait_for(worker, 1)
        self.assertEqual(self.store.get_agent(child)['status'], 'completed')
        await self.service.handle({'method': 'chat/send', 'params': {'text': '随便聊一句'}})
        current = self.service.active
        await asyncio.wait_for(current.task, 1)
        self.assertEqual(current.text, '收到最新的话。')
        self.assertFalse(task.done())
        self.model.release.set()
        await task

    async def test_capacity_check_adopts_completed_background_version(self):
        self.dialogue()
        compiler = self.compiler
        original = self.context.build
        def build(*args, **kwargs):
            snapshot = kwargs.get('memory_snapshot')
            if snapshot and snapshot.version != self.memory.snapshot().version:
                return NeedsCompaction(['already-processed'])
            return original(*args, **kwargs)
        class PublishDuringRequest:
            count = 0
            async def stream(model, messages, **options):
                model.count += 1
                if model.count == 1:
                    await compiler.wait()
                    yield StreamPiece(tool_calls=[call('read_memory', path='index.md')])
                else:
                    yield StreamPiece(text='使用最新版本继续')
        self.loop.model = PublishDuringRequest()
        with patch.object(self.context, 'build', side_effect=build):
            await self.service.handle({'method': 'chat/send', 'params': {'text': '继续查'}})
            current = self.service.active
            await asyncio.wait_for(current.task, 2)
        self.assertEqual(current.text, '使用最新版本继续')


class HistoryCacheTests(unittest.TestCase):
    def test_transaction_changes_are_incremental_and_rollback_never_publishes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = HistoryStore(Path(directory) / 'chat.sqlite3')
            try:
                turn, mid = store.begin('原文', 'test')
                published = store.revision
                old = store.message(mid)
                with self.assertRaises(RuntimeError), store.db:
                    store.db.execute('UPDATE messages SET content=? WHERE id=?', ('未提交', mid))
                    self.assertEqual(store.message(mid)['content'], '未提交')
                    self.assertEqual(store._messages[mid], old)
                    raise RuntimeError('rollback')
                self.assertEqual(store.revision, published)
                self.assertEqual(store.message(mid), old)
                queries = []
                store.db.set_trace_callback(queries.append)
                store.finish(1, turn, mid, '完成', 'completed')
                self.assertEqual(store.all_messages()[-1]['content'], '完成')
                store.payloads()
                self.assertFalse(any(q.upper().startswith('SELECT * FROM MESSAGES ORDER') or
                                     q.upper() == 'SELECT * FROM MESSAGE_PAYLOADS' for q in queries))
                changed = store.all_messages()
                changed[-1]['content'] = '外部修改'
                self.assertEqual(store.message(mid)['content'], '完成')
            finally:
                store.close()
