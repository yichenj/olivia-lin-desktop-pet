"""Exercise execution, lifecycle races and durable delivery with real local tools."""
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


def call(name, **args):
    return {'id': name + str(call.counter()), 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}

import itertools
call.counter = iter(range(10000)).__next__


class ScenarioModel:
    """Test fixture alone chooses tools; production runtime never routes user text."""
    def __init__(self):
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()
        self.contexts = []
        self.defer_once = False
        self.defer_seconds = 1
        self.deferred = False
        self.worker_calls = 0
        self.pause_acceptance = False

    async def stream(self, messages, **options):
        self.contexts.append((messages, options))
        if options['thinking'] == 'enabled':
            self.worker_calls += 1
            users = [m['content'] for m in messages if m['role'] == 'user']
            if not any(m['role'] == 'tool' for m in messages):
                yield StreamPiece(tool_calls=[call('run_shell', command='printf started > marker.txt', operation_key='marker')])
                return
            self.entered.set()
            await self.gate.wait()
            yield StreamPiece(text='执行结果：' + ' / '.join(users))
            return
        snapshot = json.loads(messages[-1]['content'].split('：', 1)[1])
        source = snapshot['user_messages'][-1]
        text = source['text']
        action = {'开始': 'create_subagent', '补充': 'steer_subagent', '撤回': 'stop_subagent'}.get(text)
        op = 'intent'
        accepted = any(o['operation_key'] == f"1:{source['id']}:{op}" for o in snapshot['recorded_calls'])
        if action and not accepted:
            args = {'source_message_id': source['id'], 'operation_key': op}
            if action != 'create_subagent':
                args['agent_id'] = snapshot['work'][0]['agent_id']
            if action != 'stop_subagent':
                args['instructions'] = '整理资料' if action == 'create_subagent' else '不包括产品会'
            yield StreamPiece(tool_calls=[call(action, **args)])
            return
        pending = snapshot['pending_results']
        if pending:
            ids = [e['message_id'] for e in pending]
            # A tool call in this reply appears after the last completed visible reply/user.
            last = next((m for m in reversed(messages[:-1]) if m['role'] != 'system'), {})
            is_decision = bool(snapshot['result_decisions_this_reply'])
            if not is_decision:
                decision = 'defer' if self.defer_once and not self.deferred else 'report'
                self.deferred |= decision == 'defer'
                yield StreamPiece(tool_calls=[call('handle_subagent_results', message_ids=ids, action=decision, delay_seconds=self.defer_seconds)])
                return
            last_call = next(m for m in reversed(messages[:-1]) if m.get('tool_calls'))['tool_calls'][0]
            if json.loads(last_call['function']['arguments'])['action'] == 'defer':
                return
            yield StreamPiece(text='Olivia 转述：' + pending[0]['content'])
            return
        if self.pause_acceptance and text == '开始':
            await asyncio.Event().wait()
        yield StreamPiece(text='我在听。' if text == '今天累死了' else '好，已受理。')


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.model = ScenarioModel()
        self.open()

    def open(self):
        self.store = HistoryStore(self.root / 'test.sqlite3')
        self.context = FullHistoryContextProvider(self.store, 'persona')
        self.harness = AgentHarness(self.context, self.model)
        self.runtime = AgentRuntime(self.store, self.context, self.harness, LocalTools(self.root, True), 'test')
        self.events = []
        self.service = ChatService(self.store, self.context, self.harness, 'test', self.events.append)

    async def asyncTearDown(self):
        await self.service.close()
        self.store.close()
        self.directory.cleanup()

    async def send(self, text):
        await self.service.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'chat/send', 'params': {'text': text}})
        current = self.service.active
        await asyncio.wait_for(current.task, 2)
        return current

    async def until(self, predicate, timeout=3):
        async def wait():
            while not predicate():
                await asyncio.sleep(.01)
        await asyncio.wait_for(wait(), timeout)

    async def test_chat_continues_during_real_execution_then_proactive_result(self):
        await self.send('开始')
        await asyncio.wait_for(self.model.entered.wait(), 2)
        child = self.store.children()[0]
        self.assertEqual((self.root / 'marker.txt').read_text(), 'started')
        await self.send('今天累死了')
        self.assertEqual(self.store.get_agent(child['id'])['status'], 'running')
        self.assertNotIn('今天累死了', str(self.context.build(child['id'])))
        self.model.gate.set()
        await self.until(lambda: any('Olivia 转述' in e.get('params', {}).get('text', '') for e in self.events))
        await self.until(lambda: not self.store.events())
        self.assertEqual(self.store.get_agent(child['id'])['status'], 'completed')
        self.assertTrue(all('agentId' not in e.get('params', {}) and 'turn' not in e.get('params', {}) for e in self.events))
        self.assertEqual([m['content'] for m in self.store.all_messages() if m['agent_id'] == 1 and m['role'] == 'user'], ['开始', '今天累死了'])
        # Provider replay contains paired calls BEFORE the final assistant text.
        context = self.context.build(1)
        first_tool = next(i for i, m in enumerate(context) if m.get('tool_calls'))
        first_reply = next(i for i, m in enumerate(context) if m.get('content') == '好，已受理。')
        self.assertLess(first_tool, first_reply)

    async def test_update_preserves_agent_and_invalidates_old_result(self):
        await self.send('开始')
        await self.model.entered.wait()
        child = self.store.children()[0]
        old = next(m for m in self.store.all_messages() if m['agent_id'] == child['id'] and m['status'] == 'streaming')
        await self.send('今天累死了')
        await self.send('补充')
        self.assertEqual(len(self.store.children()), 1)
        self.assertEqual(self.store.get_agent(child['id'])['turn'], child['turn'] + 1)
        self.assertFalse(self.store.complete_with_events(child['id'], child['turn'], old['id'], '旧结果', 'completed'))
        self.model.gate.set()
        await self.until(lambda: self.store.get_agent(child['id'])['status'] == 'completed')
        self.assertIn('不包括产品会', self.store.events()[0]['content'])
        self.assertEqual(len(self.store.keyed_tool_calls(child['id'])), 1)

    async def test_multiple_model_requests_and_tool_calls_share_one_child_turn(self):
        await self.send('今天累死了')
        with self.store.db:
            child = self.store.queue_child('写入并核对文件', self.store.latest_user_id())
        requests = []

        class ToolModel:
            async def stream(self, messages, **options):
                requests.append(messages)
                if len(requests) == 1:
                    yield StreamPiece(tool_calls=[
                        call('write_file', path='round.txt', content='verified', operation_key='write'),
                        call('read_file', path='round.txt')])
                elif len(requests) == 2:
                    yield StreamPiece(tool_calls=[call('run_shell', command='cat round.txt', operation_key='check')])
                else:
                    yield StreamPiece(text='已写入并核对')

        self.harness.model = ToolModel()
        await self.runtime.run_child(child)
        history = self.store.agent_history(child)
        self.assertEqual(len(requests), 3)
        self.assertEqual(len([m for m in history if m['role'] == 'tool']), 3)
        self.assertEqual({m['turn'] for m in history if m['role'] != 'user'}, {1})
        self.assertEqual(self.store.get_agent(child)['turn'], 1)
        self.assertEqual(self.store.get_agent(1)['turn'], 1)
        self.assertEqual(self.store.events()[0]['turn'], 1)
        self.assertEqual((self.root / 'round.txt').read_text(), 'verified')

    async def test_queued_steer_allocates_once_and_replay_start_stop_do_not_advance(self):
        turn, draft = self.store.begin('测试控制', 'test')
        source = self.store.latest_user_id()
        create = dict(instructions='初始要求', source_message_id=source, operation_key='create')
        first = await self.runtime.execute(1, turn, call('create_subagent', **create))
        child = first['agent_id']
        replay = await self.runtime.execute(1, turn, call('create_subagent', **create))
        steer = dict(agent_id=child, instructions='补充要求', source_message_id=source, operation_key='steer')
        updated = await self.runtime.execute(1, turn, call('steer_subagent', **steer))
        updated_replay = await self.runtime.execute(1, turn, call('steer_subagent', **steer))
        self.assertEqual((first['turn'], replay['turn'], updated['turn'], updated_replay['turn']), (1, 1, 2, 2))
        turn, message = self.store.start_child(child, 'test')
        self.assertEqual(turn, 2)
        await self.runtime.execute(1, 1, call('stop_subagent', agent_id=child, source_message_id=source, operation_key='stop'))
        self.assertFalse(self.runtime.valid(child, turn))
        self.store.stop_child(child, turn, message, '')
        self.assertEqual(self.store.get_agent(child)['turn'], 2)
        self.assertEqual(self.store.get_agent(child)['status'], 'cancelled')
        self.assertEqual(self.store.get_agent(1)['turn'], 1)

    async def test_late_tool_call_after_user_input_is_discarded_without_execution(self):
        entered = asyncio.Event()
        requests = []
        observed_turns = []
        store = self.store

        class LateModel:
            async def stream(self, messages, **options):
                requests.append(messages)
                if len(requests) == 1:
                    snapshot = json.loads(messages[-1]['content'].split('：', 1)[1])
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        observed_turns.append(store.get_agent(1)['turn'])
                        yield StreamPiece(tool_calls=[call('create_subagent', instructions='过期委派',
                            source_message_id=snapshot['user_messages'][-1]['id'], operation_key='late')])
                else:
                    yield StreamPiece(text='收到新话语')

        self.harness.model = LateModel()
        await self.service.handle({'method': 'chat/send', 'params': {'text': '旧话语'}})
        previous = self.service.active
        await asyncio.wait_for(entered.wait(), 1)
        current = await self.send('新话语')
        self.assertEqual(observed_turns, [current.number])
        self.assertEqual(len(requests), 2)
        self.assertEqual(current.number, previous.number + 1)
        self.assertFalse(self.store.children())
        self.assertFalse(self.store.keyed_tool_calls(1))
        self.assertIsNone(self.service.active)

    async def test_late_external_result_remains_in_original_turn_without_continuing(self):
        await self.send('今天累死了')
        source = self.store.latest_user_id()
        with self.store.db:
            child = self.store.queue_child('核对外部操作', source)
        turn, message = self.store.start_child(child, 'test')
        entered = asyncio.Event()

        async def late_result(name, args):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return {'output': '已发生的动作', 'exit_code': 0}

        self.runtime.local.execute = late_result
        command = call('run_shell', command='external-action', operation_key='external')
        executing = asyncio.create_task(self.runtime.execute(child, turn, command))
        await asyncio.wait_for(entered.wait(), 1)
        with self.store.db:
            self.store.queue_child('补充要求', source, child)
        executing.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await executing
        history = self.store.agent_history(child)
        result = next(m for m in history if m['role'] == 'tool')
        self.assertEqual(result['turn'], turn)
        self.assertIn('已发生的动作', result['content'])
        self.assertEqual(self.store.get_agent(child)['turn'], turn + 1)
        self.assertEqual(self.store.get_agent(child)['status'], 'queued')
        self.assertFalse(self.store.complete_with_events(child, turn, message, '旧结论', 'completed'))
        self.assertFalse(self.store.events())
        self.assertIsNotNone(self.store.find_tool_call(f'{child}:external')['result'])

    async def test_cancel_running_and_queued_children(self):
        await self.send('开始')
        await self.model.entered.wait()
        first = self.store.children()[0]['id']
        with self.store.db:
            second = self.store.queue_child('第二项', self.store.latest_user_id())
            self.store.cancel_child(second, self.store.latest_user_id())
        await self.send('撤回')
        await self.until(lambda: self.store.get_agent(first)['status'] == 'cancelled')
        self.assertEqual(self.store.get_agent(second)['status'], 'cancelled')
        await self.until(lambda: self.runtime.worker is None)
        self.assertEqual((self.root / 'marker.txt').read_text(), 'started', 'Cancellation does not roll back completed effects')

    async def test_control_receipt_survives_draft_replacement(self):
        self.model.pause_acceptance = True
        await self.service.handle({'method': 'chat/send', 'params': {'text': '开始'}})
        old = self.service.active
        await self.until(lambda: bool(self.store.children()))
        source = self.store.latest_user_id()
        args = dict(source_message_id=source, operation_key='intent', instructions='整理资料')
        await self.send('今天累死了')
        self.assertTrue(old.task.cancelled())
        self.assertEqual(next(m['status'] for m in self.store.all_messages() if m['id'] == old.message_id), 'superseded')
        count = len(self.store.children())
        turn, draft = self.store.begin('再次核对', 'test')
        result = await self.runtime.execute(1, turn, call('create_subagent', **args))
        self.assertEqual(len(self.store.children()), count)
        self.assertEqual(result['agent_id'], self.store.children()[0]['id'])

    async def test_result_survives_restart_without_repeating_command(self):
        await self.send('开始')
        await self.model.entered.wait()
        self.runtime.on_result = lambda: None
        self.model.gate.set()
        await self.until(lambda: self.store.children()[0]['status'] == 'completed')
        result_id = self.store.events()[0]['message_id']
        await self.service.close()
        self.store.close()
        self.open()
        self.assertEqual(self.store.events()[0]['message_id'], result_id)
        self.service.schedule_results()
        await self.until(lambda: not self.store.events())
        self.assertEqual(self.store.children()[0]['status'], 'completed')
        self.assertIsNone(self.runtime.worker)

    async def test_silent_deferral_has_deadline_and_no_empty_ui_stream(self):
        self.model.defer_once = True
        await self.send('开始')
        await self.model.entered.wait()
        before = len([e for e in self.events if e.get('method') == 'chat/started'])
        self.model.gate.set()
        await self.until(lambda: self.model.deferred and self.service.active is None)
        self.assertEqual(len([e for e in self.events if e.get('method') == 'chat/started']), before)
        await self.until(lambda: not self.store.events())
        self.assertTrue(any('Olivia 转述' in e.get('params', {}).get('text', '') for e in self.events))

    async def test_unknown_external_operation_is_not_replayed(self):
        await self.send('开始')
        await self.model.entered.wait()
        child = self.store.children()[0]
        command = call('run_shell', command='printf duplicate >> marker.txt', operation_key='uncertain')
        with self.store.db:
            self.store.record_tool_call(child['id'], child['turn'], command, f"{child['id']}:uncertain")
        await self.runtime.execute(child['id'], child['turn'], command)
        self.assertEqual((self.root / 'marker.txt').read_text(), 'started')
        self.assertIn('outcome_unknown', self.store.all_messages()[-1]['content'])

    async def test_deferred_result_and_deadline_survive_restart(self):
        self.model.defer_once = True
        self.model.defer_seconds = 60
        await self.send('开始')
        await self.model.entered.wait()
        self.model.gate.set()
        await self.until(lambda: self.model.deferred and self.service.active is None)
        event = self.store.events()[0]
        self.assertEqual(event['status'], 'pending')
        self.assertFalse(self.store.events(due_only=True))
        source = next(m for m in self.store.all_messages() if m['id'] == event['message_id'])
        self.assertEqual(source['content'], event['content'])
        self.assertGreater(source['agent_id'], 1)
        await self.service.close()
        self.store.close()
        self.open()
        restored = self.store.events()[0]
        self.assertEqual(restored, event)
        self.service.schedule_results()
        self.assertIsNotNone(self.service.wakeup)
        self.service.wakeup.cancel()  # Simulate the timer callback without leaving a second timer alive.
        self.service.wake_results()
        self.assertIsNone(self.service.active, 'Not due: do not generate or display a reply')
        self.assertTrue(self.store.events(), 'Deferral must not consume the result')


    async def test_cancel_completed_work_suppresses_unreported_success(self):
        await self.send('开始')
        await self.model.entered.wait()
        self.runtime.on_result = lambda: None
        self.model.gate.set()
        await self.until(lambda: self.store.children()[0]['status'] == 'completed')
        child = self.store.children()[0]['id']
        await self.send('撤回')
        self.assertEqual(self.store.get_agent(child)['status'], 'completed')
        self.assertFalse(self.store.events())
        self.assertEqual((self.root / 'marker.txt').read_text(), 'started')

    async def test_restart_interrupted_work_reports_once_without_resuming(self):
        await self.send('开始')
        await self.model.entered.wait()
        await self.service.close()
        self.store.close()
        self.open()
        self.assertEqual(self.store.children()[0]['status'], 'interrupted')
        self.assertEqual(len(self.store.events()), 1)
        self.assertIsNone(self.runtime.worker)
        await self.service.close()
        self.store.close()
        self.open()
        self.assertEqual(len(self.store.events()), 1)

    async def test_background_failure_reaches_parent_as_failure(self):
        original = self.model.stream
        async def fail_worker(messages, **options):
            if options['thinking'] == 'enabled':
                from backend.harness import ModelError
                raise ModelError('fixture_failure', '测试执行失败，未完成工作。')
            async for piece in original(messages, **options):
                yield piece
        self.model.stream = fail_worker
        await self.send('开始')
        await self.until(lambda: self.store.children()[0]['status'] == 'failed')
        await self.until(lambda: not self.store.events())
        self.assertTrue(any('未完成工作' in e.get('params', {}).get('text', '') for e in self.events))

    async def test_new_input_during_result_reply_does_not_lose_event(self):
        original = self.model.stream
        entered = asyncio.Event()
        block = True
        async def pause_report(messages, **options):
            async for piece in original(messages, **options):
                if block and piece.text.startswith('Olivia 转述'):
                    entered.set()
                    await asyncio.Event().wait()
                yield piece
        self.model.stream = pause_report
        await self.send('开始')
        await self.model.entered.wait()
        self.model.gate.set()
        await asyncio.wait_for(entered.wait(), 2)
        previous = self.service.active
        self.assertTrue(self.store.events())
        block = False
        await self.send('今天累死了')
        self.assertTrue(previous.task.cancelled())
        self.assertFalse(self.store.events())
        self.assertEqual(len(self.store.children()), 1)


class LocalToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_files_shell_bounds_and_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            local = LocalTools(directory, True)
            await local.execute('write_file', {'path': 'note.txt', 'content': '你好'})
            self.assertEqual((await local.execute('read_file', {'path': 'note.txt'}))['text'], '你好')
            result = await local.execute('run_shell', {'command': 'cat note.txt; exit 7'})
            self.assertEqual(result['exit_code'], 7)
            self.assertEqual(result['output'], '你好')
            with self.assertRaises(ValueError):
                await local.execute('read_file', {'path': '../outside'})
            local.shell_enabled = False
            with self.assertRaises(ValueError):
                await local.execute('run_shell', {'command': 'pwd'})

    async def test_cancel_kills_process_group_before_delayed_write(self):
        with tempfile.TemporaryDirectory() as directory:
            local = LocalTools(directory, True)
            task = asyncio.create_task(local.execute('run_shell', {'command': '(sleep 0.3; printf late > late.txt) & wait'}))
            await asyncio.sleep(.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.sleep(.4)
            self.assertFalse((Path(directory) / 'late.txt').exists())

    async def test_timeout_and_output_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            local = LocalTools(directory, True, timeout=.1)
            result = await local.execute('run_shell', {'command': 'sleep 5'})
            self.assertTrue(result['timed_out'])
            local.timeout = 2
            result = await local.execute('run_shell', {'command': "head -c 100000 /dev/zero"})
            self.assertTrue(result['truncated'])
            self.assertEqual(len(result['output']), 65536)
