"""Parent inspection sees full child history without changing child execution."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from backend.context import FullHistoryContextProvider
from backend.harness import AgentHarness, StreamPiece
from backend.local_tools import LocalTools
from backend.runtime import AgentRuntime
from backend.service import ChatService
from backend.storage import HistoryStore


def call(call_id, name, **arguments):
    return {'id': call_id, 'type': 'function', 'function': {
        'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = HistoryStore(self.root / 'history.sqlite3')
        self.context = FullHistoryContextProvider(self.store, 'persona')
        self.harness = AgentHarness(self.context, None)
        self.runtime = AgentRuntime(self.store, self.context, self.harness, LocalTools(self.root, True), 'test')
        self.service = ChatService(self.store, self.context, self.harness, 'test', lambda e: None)
        turn, message = self.store.begin('整理文件', 'test')
        self.store.finish(1, turn, message, '开始整理。', 'completed')
        self.child = self.store.create_agent(source_message_id=self.store.latest_user_id())

    async def asyncTearDown(self):
        await self.service.close()
        self.store.close()
        self.directory.cleanup()

    def inspect(self, agent_id=None):
        return self.runtime.control(self.store.get_agent(1)['turn'], 'get_subagent',
                                    {'agent_id': agent_id or self.child})

    async def test_full_history_contains_all_turns_payloads_and_audit_states(self):
        turn, draft = self.store.begin('初始要求', 'test', self.child)
        tool_call = call('read-call', 'read_file', path='input.txt')
        with self.store.db:
            self.store.record_exchange(self.child, turn, tool_call, {'text': '实际读取的材料'})
        turn, draft = self.store.steer(self.child, turn, draft, '旧草稿', '追加限制')
        self.store.finish(self.child, turn, draft, '失败草稿', 'failed', 'test_failure')
        turn, draft = self.store.begin('再次处理', 'test', self.child)
        self.store.finish(self.child, turn, draft, '中断草稿', 'interrupted', 'process_exit')
        turn, draft = self.store.begin('继续核对', 'test', self.child)
        self.store.checkpoint(draft, '正在核对的正文检查点')
        before = self.store.get_agent(self.child)
        messages_before = self.store.all_messages()
        result = self.inspect()
        history = result['history']
        self.assertEqual([m['id'] for m in history], [m['id'] for m in messages_before if m['agent_id'] == self.child])
        self.assertEqual({m['status'] for m in history}, {'completed', 'superseded', 'failed', 'interrupted', 'streaming'})
        self.assertIn('正在核对的正文检查点', [m['content'] for m in history])
        payloads = [m['payload'] for m in history if m['payload']]
        self.assertEqual(payloads[0]['tool_calls'], [tool_call])
        self.assertEqual(payloads[1]['tool_call_id'], 'read-call')
        self.assertEqual(json.loads(payloads[1]['content']), {'text': '实际读取的材料'})
        self.assertEqual(self.store.get_agent(self.child), before)
        self.assertEqual(self.store.all_messages(), messages_before)

    async def test_history_is_not_limited_to_latest_page_or_copied_to_default_snapshot(self):
        for i in range(65):
            turn, draft = self.store.begin(f'输入{i}', 'test', self.child)
            self.store.finish(self.child, turn, draft, f'结果{i}', 'completed')
        result = self.inspect()
        self.assertEqual(len(result['history']), 130)
        self.assertEqual(result['history'][0]['content'], '输入0')
        self.assertEqual(result['history'][-1]['content'], '结果64')
        self.context.refresh()
        decorated = self.runtime.decorate(1, self.context.build(1))
        snapshot = json.loads(decorated[-1]['content'].split('：', 1)[1])
        self.assertNotIn('history', snapshot['work'][0])
        self.assertNotIn('结果0', json.dumps(snapshot, ensure_ascii=False))

    async def test_parent_only_inspects_its_own_children(self):
        other = self.store.create_agent()
        turn, draft = self.store.begin('其他工作的私有材料', 'test', other)
        self.store.finish(other, turn, draft, '其他结果', 'completed')
        nested = self.store.create_agent(parent_agent_id=other)
        self.assertNotIn('其他工作的私有材料', json.dumps(self.inspect(), ensure_ascii=False))
        for target in (1, nested, 999999):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.inspect(target)

    async def test_inflight_call_is_visible_and_cancelled_receipt_becomes_unknown(self):
        turn, draft = self.store.begin('执行检查', 'test', self.child)
        entered = asyncio.Event()
        async def wait_tool(name, args):
            entered.set()
            await asyncio.Event().wait()
        self.runtime.local.execute = wait_tool
        tool_call = call('waiting-call', 'run_shell', command='long command', operation_key='long')
        executing = asyncio.create_task(self.runtime.execute(self.child, turn, tool_call))
        await asyncio.wait_for(entered.wait(), 1)
        try:
            result = self.inspect()
            self.assertEqual(result['active_call']['call'], tool_call)
            self.assertEqual(result['active_call']['turn'], turn)
            message = next(m for m in result['history'] if m['id'] == result['active_call']['message_id'])
            self.assertEqual(message['execution_status'], 'running')
            self.assertEqual(message['payload']['tool_calls'], [tool_call])
            self.assertFalse(any(m['tool_call_message_id'] == message['id'] for m in result['history']))
            self.assertFalse(executing.done())
            self.assertEqual(self.store.get_agent(self.child)['turn'], turn)
        finally:
            executing.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await executing
        result = self.inspect()
        self.assertIsNone(result['active_call'])
        self.assertEqual(result['history'][-1]['execution_status'], 'outcome_unknown')
        self.store.finish(self.child, turn, draft, '', 'interrupted')
        self.store.close()
        self.store = HistoryStore(self.root / 'history.sqlite3')
        self.context.store = self.store
        self.runtime.store = self.store
        self.service.store = self.store
        restored = next(m for m in self.inspect()['history'] if m['id'] == message['id'])
        self.assertEqual(restored['execution_status'], 'outcome_unknown')

    async def test_main_answers_using_actual_tool_result_while_child_keeps_running(self):
        child_turn, draft = self.store.begin('执行 shell 检查', 'test', self.child)
        done_call = call('done-call', 'run_shell', command='printf "已检查 3 个文件"; exit 7', operation_key='checked')
        await self.runtime.execute(self.child, child_turn, done_call)
        child_before = self.store.get_agent(self.child)
        observed = []
        target = self.child
        class InspectingModel:
            async def stream(self, messages, **options):
                observed.append(messages)
                latest = next(m for m in reversed(messages) if m['role'] != 'system')
                if latest['role'] != 'tool':
                    yield StreamPiece(tool_calls=[call('inspection', 'get_subagent', agent_id=target)])
                else:
                    info = json.loads(latest['content'])
                    results = [json.loads(m['payload']['content']) for m in info['history']
                               if m['payload'] and m['role'] == 'tool']
                    result = results[-1]
                    yield StreamPiece(text=f"{result['output']}，命令退出码为 {result['exit_code']}，还不能说全部成功。")
        self.harness.model = InspectingModel()
        await self.service.handle({'method': 'chat/send', 'params': {'text': '现在进展如何？'}})
        current = self.service.active
        await asyncio.wait_for(current.task, 1)
        self.assertIn('已检查 3 个文件，命令退出码为 7', current.text)
        self.assertEqual(self.store.get_agent(self.child), child_before)
        self.assertEqual(len(observed), 2)
        self.store.finish(self.child, child_turn, draft, '', 'interrupted')
