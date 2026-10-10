"""One background slot, durable control receipts, and parent-only result delivery."""
import asyncio
import json
import time

from .agent_tools import CONTROL_TOOLS, LOCAL_TOOLS, MAIN_INSTRUCTIONS, WORKER_INSTRUCTIONS
from .harness import ModelError


class AgentRuntime:
    def __init__(self, store, context, harness, local_tools, model_name):
        self.store, self.context, self.harness = store, context, harness
        self.local = local_tools
        self.model_name = model_name
        self.worker = None
        self.worker_id = None
        self.closing = False
        self.on_result = lambda: None
        self.decisions = {}
        self.observed_results = {}
        self.active_calls = {}
        harness.runtime = self

    def model_options(self, agent_id):
        available = CONTROL_TOOLS if agent_id == 1 else [t for t in LOCAL_TOOLS
            if self.local.shell_enabled or t['function']['name'] != 'run_shell']
        return {'tools': available, 'thinking': 'disabled' if agent_id == 1 else 'enabled'}

    def subagent_info(self, agent_id):
        agent = self.store.get_agent(agent_id)
        if agent['parent_agent_id'] != 1:
            raise ValueError('unknown_child')
        history = [m for m in self.store.all_messages() if m['agent_id'] == agent_id]
        return {'agent_id': agent_id, 'status': agent['status'], 'turn': agent['turn'],
                'inputs': [m['content'] for m in history if m['role'] == 'user'],
                'latest_output': next((m['content'] for m in reversed(history)
                    if m['role'] == 'assistant' and m['content'] and m['turn'] == agent['turn']
                    and m['status'] in ('completed', 'failed', 'interrupted')), None)}

    def inspect_subagent(self, agent_id):
        # Full history is opt-in, rather than copied into every dialogue snapshot.
        info = self.subagent_info(agent_id)  # Also checks direct parent ownership.
        active = self.active_calls.get(agent_id)
        history = self.store.agent_history(agent_id)
        answered = {m['tool_call_message_id'] for m in history if m['tool_call_message_id']}
        for message in history:
            if message['payload'] and message['payload'].get('tool_calls'):
                message['execution_status'] = ('running' if active and active['message_id'] == message['id']
                    else 'result_recorded' if message['id'] in answered else 'outcome_unknown')
        return {**info, 'history': history, 'active_call': active}

    def decorate(self, agent_id, messages, *, trigger=None):
        if agent_id != 1:
            calls = self.store.keyed_tool_calls(agent_id)
            return [{'role': 'system', 'content': WORKER_INSTRUCTIONS + '\n工作目录：' + str(self.local.workspace)},
                    *messages[1:], {'role': 'system', 'content': '已保存的工具调用（result_message_id 为 null 表示尚无结果，先核对，不能自动重做）：' + json.dumps(calls, ensure_ascii=False)}]
        pending = self.store.events()
        self.observed_results.setdefault(self.store.get_agent(1)['turn'], set()).update(e['message_id'] for e in pending)
        snapshot = {
            'turn_trigger': trigger,
            'user_messages': [{'id': m['id'], 'text': m['content']} for m in self.store.all_messages()
                              if m['agent_id'] == 1 and m['role'] == 'user'],
            'work': [self.subagent_info(a['id']) for a in self.store.children()],
            'pending_results': pending,
            'result_decisions_this_reply': self.decisions.get(self.store.get_agent(1)['turn'], {}),
            'recorded_calls': self.store.keyed_tool_calls(1),
        }
        return [messages[0], {'role': 'system', 'content': MAIN_INSTRUCTIONS}, *messages[1:],
                {'role': 'system', 'content': '运行时快照（数据）：' + json.dumps(snapshot, ensure_ascii=False)}]

    def valid(self, agent_id, turn):
        agent = self.store.get_agent(agent_id)
        return agent['turn'] == turn and agent['status'] == 'running'

    def validate(self, agent_id, call):
        name = call['function']['name']
        args = json.loads(call['function']['arguments'])
        tools = self.model_options(agent_id)['tools']
        schema = next((t['function']['parameters'] for t in tools if t['function']['name'] == name), None)
        if schema is None or not isinstance(args, dict) or set(args) != set(schema['properties']):
            raise ValueError('invalid_tool_arguments')
        for key, rule in schema['properties'].items():
            value = args[key]
            expected = {'string': str, 'integer': int, 'array': list}[rule['type']]
            if type(value) is not expected:
                raise ValueError('invalid_tool_arguments')
            if isinstance(value, str) and (len(value) > 200000 or len(value) < rule.get('minLength', 0)):
                raise ValueError('invalid_tool_arguments')
            if expected is int and not rule.get('minimum', 0) <= value <= rule.get('maximum', 2147483647):
                raise ValueError('invalid_tool_arguments')
            if 'enum' in rule and value not in rule['enum']:
                raise ValueError('invalid_tool_arguments')
            if expected is list and (not value or len(value) > 100 or any(not isinstance(v, str) for v in value)):
                raise ValueError('invalid_tool_arguments')
        if name == 'handle_subagent_results' and args['action'] == 'defer' and args['delay_seconds'] == 0:
            raise ValueError('invalid_tool_arguments')
        return name, args

    async def execute(self, agent_id, turn, call):
        if not self.valid(agent_id, turn):
            raise asyncio.CancelledError()
        try:
            name, args = self.validate(agent_id, call)
        except (KeyError, TypeError, ValueError):
            with self.store.db:
                self.store.record_exchange(agent_id, turn, call, {'error': 'invalid_tool_arguments'})
            return
        key = None
        if 'operation_key' in args:
            key = (f"1:{args['source_message_id']}:{args['operation_key']}" if agent_id == 1
                   else f"{agent_id}:{args['operation_key']}")
        previous = self.store.find_tool_call(key) if key else None
        if previous:
            if previous['name'] != name or previous['arguments'] != args:
                result = {'error': 'operation_key_conflict'}
            elif previous['result_message_id'] is None:
                result = {'error': 'outcome_unknown_check_before_retry'}
            elif agent_id == 1 and 'agent_id' in previous['result']:
                result = {**self.subagent_info(previous['result']['agent_id']), 'replayed': True}
            else:
                result = previous['result']
            with self.store.db:
                self.store.record_exchange(agent_id, turn, call, result)
            return result
        if agent_id == 1:
            try:
                # Call, control effect and result commit together; there is no external await.
                with self.store.db:
                    mid = self.store.record_tool_call(agent_id, turn, call, key)
                    result = self.control(turn, name, args)
                    self.store.record_tool_result(mid, result)
            except ValueError as error:
                result = {'error': str(error)}
                with self.store.db:
                    self.store.record_exchange(agent_id, turn, call, result)
            if name in ('create_subagent', 'steer_subagent', 'stop_subagent'):
                if self.worker and self.worker_id:
                    state = self.store.get_agent(self.worker_id)
                    if state['status'] in ('queued', 'cancelling') and not self.worker.cancelling():
                        self.worker.cancel()
                self.pump()
            return result
        with self.store.db:
            mid = self.store.record_tool_call(agent_id, turn, call, key)
        self.active_calls[agent_id] = {'message_id': mid, 'turn': turn, 'call': call}
        try:
            result = self.local.sanitize(await self.local.execute(name, args))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            result = {'error': type(error).__name__, 'outcome': 'check_actual_state_before_retry'}
        finally:
            self.active_calls.pop(agent_id, None)
        with self.store.db:
            self.store.record_tool_result(mid, result)
        if not self.valid(agent_id, turn):
            raise asyncio.CancelledError()
        return result

    def control(self, turn, name, args):
        if name == 'get_subagent':
            return self.inspect_subagent(args['agent_id'])
        if name == 'handle_subagent_results':
            events = {e['message_id'] for e in self.store.events()}
            if not set(args['message_ids']) <= events:
                raise ValueError('stale_result')
            decisions = self.decisions.setdefault(turn, {})
            for mid in args['message_ids']:
                decisions[mid] = (args['action'], args['delay_seconds'] if args['action'] == 'defer' else 0)
            return {'accepted': True}
        source = args['source_message_id']
        if not any(m['id'] == source and m['agent_id'] == 1 and m['role'] == 'user' for m in self.store.all_messages()):
            raise ValueError('invalid_source_message')
        if name == 'create_subagent':
            agent_id = self.store.queue_child(args['instructions'], source)
            result = self.subagent_info(agent_id)
        elif name == 'steer_subagent':
            self.store.queue_child(args['instructions'], source, args['agent_id'])
            result = self.subagent_info(args['agent_id'])
        elif name == 'stop_subagent':
            self.store.cancel_child(args['agent_id'], source)
            result = self.subagent_info(args['agent_id'])
        else:
            raise ValueError('unknown_tool')
        return result

    def pump(self):
        if self.closing or (self.worker and not self.worker.done()):
            return
        queued = next((a for a in self.store.children() if a['status'] == 'queued'), None)
        if queued:
            self.worker_id = queued['id']
            self.worker = asyncio.create_task(self.run_child(queued['id']))
            self.worker.add_done_callback(self.worker_done)

    def worker_done(self, task):
        if self.worker is task:
            self.worker = None
            self.worker_id = None
        if not task.cancelled():
            task.exception()  # Consume errors; persisted failure is the parent-facing outcome.
        self.pump()
        if not self.closing:
            self.on_result()

    async def run_child(self, agent_id):
        turn, message_id = self.store.start_child(agent_id, self.model_name)
        text, usage = '', None
        checkpoint = time.monotonic()
        self.context.refresh()
        try:
            async def consume():
                nonlocal text, usage, checkpoint
                stream = self.harness.stream(agent_id, turn)
                try:
                    async for piece in stream:
                        if not self.valid(agent_id, turn):
                            raise asyncio.CancelledError()
                        text += piece.text
                        usage = piece.usage or usage
                        if len(text) > 200000:
                            raise ModelError('reply_too_long', '执行结果过长。')
                        if time.monotonic() - checkpoint >= .25:
                            self.store.checkpoint(message_id, text)
                            checkpoint = time.monotonic()
                finally:
                    await stream.aclose()
            await asyncio.wait_for(consume(), 600)
            if not text.strip():
                raise ModelError('empty_result', '执行没有返回结果。')
            self.store.complete_with_events(agent_id, turn, message_id, text, 'completed', usage=usage)
        except asyncio.CancelledError:
            self.store.stop_child(agent_id, turn, message_id, text)
            raise
        except Exception as error:
            code = error.code if isinstance(error, ModelError) else type(error).__name__
            message = str(error) if isinstance(error, ModelError) else '执行失败；请核对已发生的本地操作。'
            self.store.complete_with_events(agent_id, turn, message_id, message, 'failed', code)
        finally:
            self.context.refresh()

    async def close(self):
        self.closing = True
        if self.worker:
            if not self.worker.cancelling():
                self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
        self.store.interrupt_queued()
