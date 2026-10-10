"""Timer cancellation never consumes results; reply commit determines delivery."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest

from backend.context import FullHistoryContextProvider
from backend.harness import AgentHarness, ModelError, StreamPiece
from backend.local_tools import LocalTools
from backend.runtime import AgentRuntime
from backend.service import ChatService, RpcError
from backend.storage import HistoryStore


class ResultModel:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.action = None
        self.fail = False
        self.snapshots = []

    async def stream(self, messages, **options):
        snapshot = json.loads(messages[-1]['content'].split('：', 1)[1])
        self.snapshots.append(snapshot)
        if self.action and not snapshot['result_decisions_this_reply']:
            yield StreamPiece(tool_calls=[{'id': 'result-choice', 'type': 'function', 'function': {
                'name': 'handle_subagent_results', 'arguments': json.dumps({
                    'message_ids': [snapshot['pending_results'][0]['message_id']],
                    'action': self.action, 'delay_seconds': 60})}}])
            return
        self.entered.set()
        await self.release.wait()
        if self.fail:
            raise ModelError('fixture_failure', '测试回复失败')
        yield StreamPiece(text='结果已经核对好了。')


class ResultTimerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = HistoryStore(Path(self.directory.name) / 'history.sqlite3')
        self.context = FullHistoryContextProvider(self.store, 'persona')
        self.model = ResultModel()
        harness = AgentHarness(self.context, self.model)
        self.runtime = AgentRuntime(self.store, self.context, harness, LocalTools(self.directory.name), 'test')
        self.events = []
        self.service = ChatService(self.store, self.context, harness, 'test', self.events.append)

    async def asyncTearDown(self):
        await self.service.close()
        self.store.close()
        self.directory.cleanup()

    def result(self, after=60):
        child = self.store.create_agent()
        turn, message = self.store.begin('整理资料', 'test', child)
        self.store.complete_with_events(child, turn, message, '后台结果', 'completed')
        with self.store.db:
            self.store.db.execute('UPDATE result_events SET available_at=? WHERE message_id=?', (time.time() + after, message))
        return next(e for e in self.store.events() if e['message_id'] == message)

    async def start_input(self):
        await self.service.handle({'method': 'chat/send', 'params': {'text': '刚才的结果呢？'}})
        await asyncio.wait_for(self.model.entered.wait(), 1)
        return self.service.active

    async def test_early_report_cancels_timer_but_consumes_event_only_after_reply_commit(self):
        event = self.result()
        self.service.schedule_results()
        old_timer = self.service.wakeup
        self.model.action = 'report'
        current = await self.start_input()
        self.assertTrue(old_timer.cancelled())
        self.assertIsNone(self.service.wakeup)
        self.assertEqual(self.store.events()[0]['message_id'], event['message_id'])
        self.assertIn(event['message_id'], self.runtime.decisions[current.number])
        self.model.release.set()
        await current.task
        self.assertFalse(self.store.events())
        self.assertIsNone(self.service.wakeup)
        self.service.wake_results()  # Even a late callback cannot create a duplicate reply.
        self.assertIsNone(self.service.active)

    async def test_unrelated_input_preserves_absolute_deadline_and_rearms_timer(self):
        event = self.result()
        self.service.schedule_results()
        old_timer = self.service.wakeup
        current = await self.start_input()
        self.assertTrue(old_timer.cancelled())
        self.assertEqual(self.store.events()[0]['available_at'], event['available_at'])
        self.service.schedule_results()  # A worker callback must not arm a timer while the parent is busy.
        self.assertIsNone(self.service.wakeup)
        self.model.release.set()
        await current.task
        self.assertEqual(self.store.events()[0]['available_at'], event['available_at'])
        self.assertIsNotNone(self.service.wakeup)
        self.assertIsNot(self.service.wakeup, old_timer)

    async def test_failed_early_report_keeps_result_and_rearms_timer(self):
        event = self.result()
        self.service.schedule_results()
        self.model.action = 'report'
        self.model.fail = True
        current = await self.start_input()
        self.model.release.set()
        await current.task
        self.assertEqual(self.store.events()[0]['message_id'], event['message_id'])
        self.assertIsNotNone(self.service.wakeup)

    async def test_partial_report_retargets_single_timer_to_remaining_event(self):
        first = self.result(after=60)
        second = self.result(after=120)
        self.service.schedule_results()
        old_timer = self.service.wakeup
        self.model.action = 'report'
        current = await self.start_input()
        self.model.release.set()
        await current.task
        self.assertTrue(old_timer.cancelled())
        self.assertEqual([e['message_id'] for e in self.store.events()], [second['message_id']])
        self.assertNotEqual(first['message_id'], second['message_id'])
        delay = self.service.wakeup.when() - asyncio.get_running_loop().time()
        self.assertGreater(delay, 115)
        self.assertLessEqual(delay, 120)

    async def test_no_pending_events_cancel_timer_and_retry_does_not_delay_future_event(self):
        self.result(after=-1)
        self.result(after=5)
        self.service.schedule_results(delay=30)
        delay = self.service.wakeup.when() - asyncio.get_running_loop().time()
        self.assertGreater(delay, 0)
        self.assertLessEqual(delay, 5)
        old_timer = self.service.wakeup
        with self.store.db:
            self.store.db.execute("UPDATE result_events SET status='suppress'")
        self.service.schedule_results()
        self.assertTrue(old_timer.cancelled())
        self.assertIsNone(self.service.wakeup)

    async def test_invalid_input_does_not_cancel_scheduled_wakeup(self):
        self.result()
        self.service.schedule_results()
        timer = self.service.wakeup
        with self.assertRaises(RpcError):
            await self.service.handle({'method': 'chat/send', 'params': {'text': ''}})
        self.assertIs(self.service.wakeup, timer)
        self.assertFalse(timer.cancelled())

    async def test_timer_first_then_user_input_supersedes_only_parent_reply(self):
        event = self.result(after=-1)
        self.service.schedule_results()
        self.service.wakeup.cancel()
        self.service.wake_results()
        previous = self.service.active
        await asyncio.wait_for(self.model.entered.wait(), 1)
        self.model.entered.clear()
        self.model.action = 'report'
        current = await self.start_input()
        self.assertEqual(self.model.snapshots[0]['turn_trigger'], 'subagent_results')
        self.assertTrue(all(s['turn_trigger'] == 'user_input' for s in self.model.snapshots[1:]))
        self.assertEqual(previous.number, 1)
        self.assertEqual(current.number, 2)
        self.assertEqual(self.store.get_agent(event['agent_id'])['turn'], event['turn'])
        self.assertTrue(previous.task.cancelled())
        self.assertEqual(self.store.events()[0]['message_id'], event['message_id'])
        self.assertEqual(self.store.get_agent(event['agent_id'])['status'], 'completed')
        self.model.release.set()
        await current.task
        self.assertFalse(self.store.events())
        self.assertIsNone(self.service.wakeup)

    async def test_timer_reports_without_any_new_user_input(self):
        event = self.result(after=-1)
        self.model.action = 'report'
        self.service.schedule_results()  # Allow the actual timer to fire; do not call wake_results directly.
        await asyncio.wait_for(self.model.entered.wait(), 1)
        current = self.service.active
        self.assertTrue(current.proactive)
        self.assertEqual(len(self.model.snapshots), 2, 'Tool call and following model request share the trigger')
        self.assertTrue(all(s['turn_trigger'] == 'subagent_results' for s in self.model.snapshots))
        self.assertFalse(any(m['agent_id'] == 1 and m['role'] == 'user' for m in self.store.all_messages()))
        self.model.release.set()
        await current.task
        self.assertFalse(self.store.events())
        self.assertEqual(self.store.get_agent(event['agent_id'])['turn'], event['turn'])
        self.assertEqual(self.store.get_agent(1)['turn'], current.number)
        self.assertEqual([e['method'] for e in self.events], ['chat/started', 'chat/delta', 'chat/completed'])
        self.assertEqual(self.events[0]['params']['presentation'], 'when_idle')

    async def test_due_callback_during_user_turn_never_advances_or_interrupts_it(self):
        current = await self.start_input()
        event = self.result(after=-1)  # Result arrives after the current model request began.
        self.service.schedule_results()
        self.service.wake_results()  # A callback already queued at input acceptance is harmless.
        self.assertIs(self.service.active, current)
        self.assertEqual(self.store.get_agent(1)['turn'], current.number)
        self.assertFalse(current.task.done())
        self.assertIsNone(self.service.wakeup)
        self.assertEqual(self.store.events()[0]['message_id'], event['message_id'])
        self.model.release.set()
        await current.task
        self.assertIsNotNone(self.service.wakeup)
        self.assertEqual(self.store.get_agent(1)['turn'], current.number)
        self.model.action = 'report'
        self.service.wakeup.cancel()
        self.service.wake_results()
        proactive = self.service.active
        self.assertEqual(proactive.number, current.number + 1)
        await proactive.task
        self.assertFalse(self.store.events())
        self.assertEqual(self.store.get_agent(event['agent_id'])['turn'], event['turn'])

    async def test_timer_with_no_due_result_does_not_allocate_turn(self):
        self.result(after=60)
        self.service.wake_results()
        self.assertEqual(self.store.get_agent(1)['turn'], 0)
        self.assertIsNone(self.service.active)
        self.assertIsNotNone(self.service.wakeup)
        with self.store.db:
            self.store.db.execute("UPDATE result_events SET status='suppress'")
        self.service.cancel_result_timer()
        self.service.wake_results()
        self.assertEqual(self.store.get_agent(1)['turn'], 0)
        self.assertIsNone(self.service.wakeup)

    async def test_replaced_result_decision_cannot_be_committed_by_old_turn(self):
        event = self.result()
        self.model.action = 'report'
        previous = await self.start_input()
        self.assertIn(event['message_id'], self.runtime.decisions[previous.number])
        self.model.entered.clear()
        self.model.action = None
        current = await self.start_input()
        self.assertNotIn(previous.number, self.runtime.decisions)
        self.assertFalse(self.store.complete_with_events(1, previous.number, previous.message_id,
            '迟到转述', 'completed', decisions={event['message_id']: ('report', 1)}))
        self.service.finish(previous, 'completed')
        self.assertIs(self.service.active, current)
        self.model.release.set()
        await current.task
        self.assertEqual(self.store.events()[0]['message_id'], event['message_id'])
        self.assertEqual(self.store.get_agent(1)['turn'], current.number)

    async def test_zero_delay_is_valid_for_report_and_suppress_but_not_defer(self):
        for action in ('report', 'suppress', 'defer'):
            with self.subTest(action=action):
                event = self.result(after=60)
                turn, draft = self.store.begin('处理结果', 'test')
                tool_call = {'id': action, 'type': 'function', 'function': {
                    'name': 'handle_subagent_results', 'arguments': json.dumps({
                        'message_ids': [event['message_id']], 'action': action, 'delay_seconds': 0})}}
                await self.runtime.execute(1, turn, tool_call)
                decisions = self.runtime.decisions.pop(turn, {})
                self.store.complete_with_events(1, turn, draft, '结果说明', 'completed', decisions=decisions)
                status = self.store.db.execute('SELECT status FROM result_events WHERE message_id=?',
                                               (event['message_id'],)).fetchone()[0]
                self.assertEqual(status, 'pending' if action == 'defer' else action)
                if action == 'defer':
                    self.assertFalse(decisions)
                    self.assertIn('invalid_tool_arguments', self.store.all_messages()[-1]['content'])
