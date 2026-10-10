"""Tool messages are the durable record, including crashes between effect and reply."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from backend.context import FullHistoryContextProvider
from backend.harness import AgentHarness
from backend.local_tools import LocalTools
from backend.runtime import AgentRuntime
from backend.storage import HistoryStore


def call(identifier, name='run_shell', **args):
    return {'id': identifier, 'type': 'function', 'function': {
        'name': name, 'arguments': json.dumps(args)}}


class ToolMessageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.path = self.root / 'history.sqlite3'
        self.open()

    def open(self):
        self.store = HistoryStore(self.path)
        self.context = FullHistoryContextProvider(self.store, 'persona')
        self.harness = AgentHarness(self.context, None)
        self.runtime = AgentRuntime(self.store, self.context, self.harness, LocalTools(self.root, True), 'test')

    async def asyncTearDown(self):
        await self.runtime.close()
        self.store.close()
        self.directory.cleanup()

    def start_child(self):
        child = self.store.create_agent()
        turn, draft = self.store.begin('执行本地检查', 'test', child)
        return child, turn, draft

    async def test_known_result_replays_after_restart_and_conflicting_key_is_rejected(self):
        child, turn, draft = self.start_child()
        args = dict(command='printf once >> marker.txt; exit 7', operation_key='write')
        result = await self.runtime.execute(child, turn, call('first', **args))
        self.assertEqual(result['exit_code'], 7)
        self.store.finish(child, turn, draft, '已记录命令失败', 'completed')
        await self.runtime.close()
        self.store.close()
        self.open()
        turn, draft = self.store.begin('继续核对', 'test', child)
        replay = await self.runtime.execute(child, turn, call('new-provider-id', **args))
        self.assertEqual(replay, result)
        conflict = await self.runtime.execute(child, turn, call('conflict',
            command='printf wrong >> marker.txt', operation_key='write'))
        self.assertEqual(conflict, {'error': 'operation_key_conflict'})
        wrong_tool = await self.runtime.execute(child, turn, call('wrong-tool', 'write_file',
            path='marker.txt', content='wrong', operation_key='write'))
        self.assertEqual(wrong_tool, {'error': 'operation_key_conflict'})
        self.assertEqual((self.root / 'marker.txt').read_text(), 'once')
        calls = self.store.keyed_tool_calls(child)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['result'], result)
        self.assertEqual(calls[0]['turn'], 1)
        self.context.refresh()
        replayed = self.context.build(child)
        call_ids = []
        for index, message in enumerate(replayed):
            if message.get('tool_calls'):
                identifier = message['tool_calls'][0]['id']
                call_ids.append(identifier)
                self.assertEqual(replayed[index + 1]['tool_call_id'], identifier)
        self.assertEqual(call_ids, ['first', 'new-provider-id', 'conflict', 'wrong-tool'])

    async def test_process_crash_after_real_side_effect_preserves_unknown_call(self):
        await self.runtime.close()
        self.store.close()
        script = '''
import asyncio, os, sys
from pathlib import Path
from backend.context import FullHistoryContextProvider
from backend.harness import AgentHarness
from backend.local_tools import LocalTools
from backend.runtime import AgentRuntime
from backend.storage import HistoryStore
root = Path(sys.argv[1])
store = HistoryStore(root / 'history.sqlite3')
child = store.create_agent()
turn, draft = store.begin('执行', 'test', child)
context = FullHistoryContextProvider(store, 'persona')
local = LocalTools(root, True)
execute = local.execute
async def crash_after_effect(name, args):
    await execute(name, args)
    os._exit(23)
local.execute = crash_after_effect
runtime = AgentRuntime(store, context, AgentHarness(context, None), local, 'test')
import json
call = {'id': 'before-crash', 'type': 'function', 'function': {'name': 'run_shell',
    'arguments': json.dumps({'command': 'printf once >> marker.txt', 'operation_key': 'write'})}}
asyncio.run(runtime.execute(child, turn, call))
'''
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', script, str(self.root),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
        self.open()
        self.assertEqual(process.returncode, 23, stderr.decode())
        child = self.store.children()[0]['id']
        original = self.store.find_tool_call(f'{child}:write')
        self.assertIsNone(original['result_message_id'])
        self.assertEqual((self.root / 'marker.txt').read_text(), 'once')
        turn, draft = self.store.begin('先核对', 'test', child)
        result = await self.runtime.execute(child, turn, call('after-crash',
            command='printf once >> marker.txt', operation_key='write'))
        self.assertEqual(result, {'error': 'outcome_unknown_check_before_retry'})
        self.assertEqual((self.root / 'marker.txt').read_text(), 'once')
        self.assertIsNone(self.store.find_tool_call(f'{child}:write')['result_message_id'])
        self.context.refresh()
        messages = self.context.build(child)
        self.assertFalse(any(c['id'] == 'before-crash' for m in messages for c in m.get('tool_calls', [])))
        self.assertTrue(any(m['role'] == 'system' and 'before-crash' in m['content'] for m in messages))
        history = self.runtime.inspect_subagent(child)['history']
        self.assertEqual(next(m['execution_status'] for m in history if m['id'] == original['message_id']), 'outcome_unknown')
        # A refusal to retry belongs to the new call, never a fabricated outcome of the old one.
        latest_result = next(m for m in reversed(history) if m['tool_call_message_id'])
        self.assertNotEqual(latest_result['tool_call_message_id'], original['message_id'])

    async def test_late_result_pairs_with_its_call_across_steer_input(self):
        child, turn, draft = self.start_child()
        command = call('late', command='read-state', operation_key='check')
        with self.store.db:
            mid = self.store.record_tool_call(child, turn, command, f'{child}:check')
        new_turn, new_draft = self.store.steer(child, turn, draft, '', '新的限制')
        with self.store.db:
            self.store.record_tool_result(mid, {'output': '实际结果'})
        self.context.refresh()
        messages = self.context.build(child)
        index = next(i for i, m in enumerate(messages) if m.get('tool_calls'))
        self.assertEqual(messages[index + 1]['tool_call_id'], 'late')
        self.assertEqual(messages[index + 2], {'role': 'user', 'content': '新的限制'})
        history = self.store.agent_history(child)
        self.assertGreater(history[-1]['seq'], next(m['seq'] for m in history if m['content'] == '新的限制'))
        self.assertEqual(history[-1]['turn'], turn)
        self.assertEqual(self.store.get_agent(child)['turn'], new_turn)

    async def test_control_effect_and_messages_rollback_together_on_result_failure(self):
        turn, draft = self.store.begin('委派', 'test')
        source = self.store.latest_user_id()
        original = self.store.record_tool_result
        failed = False
        def fail_once(mid, result):
            nonlocal failed
            if not failed:
                failed = True
                raise ValueError('injected_failure')
            return original(mid, result)
        with patch.object(self.store, 'record_tool_result', side_effect=fail_once):
            result = await self.runtime.execute(1, turn, call('delegate', 'create_subagent',
                instructions='整理', source_message_id=source, operation_key='create'))
        self.assertEqual(result, {'error': 'injected_failure'})
        self.assertFalse(self.store.children())
        self.assertFalse(self.store.keyed_tool_calls(1))
        self.context.refresh()
        messages = self.context.build(1)
        self.assertEqual(len([m for m in messages if m.get('tool_calls')]), 1)
        self.assertEqual(json.loads(messages[-1]['content']), result)
