"""One background slot, durable control receipts, and parent-only result delivery."""
import asyncio
import json
import time

from .agent_tools import available_tools, validate_call
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
        context.runtime = self
        self.memory_reads = {}
        self.memory_snapshots = {}

    def tools(self, agent_id):
        return available_tools(agent_id, shell_enabled=self.local.shell_enabled,
                               memory_enabled=self.context.memory is not None and self.harness.compiler is not None)

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

    def context_state(self, agent_id, history, *, trigger=None, turn=None):
        if agent_id != 1:
            return {'recorded_calls': self.store.keyed_tool_calls(agent_id)}
        pending = self.store.events()
        users = [m for m in history if m['role'] == 'user']
        source_ids = {m['id'] for m in users}
        calls = self.store.keyed_tool_calls(1)
        if self.context.memory:
            calls = [c for c in calls if c['arguments'].get('source_message_id') in source_ids]
        children = self.store.children()
        if self.context.memory:
            active = [a for a in children if a['status'] in ('queued', 'running', 'waiting', 'cancelling')]
            recent = children[-8:]
            event_agents = {e['agent_id'] for e in pending}
            children = [a for a in children if a in active or a in recent or a['id'] in event_agents]
        return {
            'turn_trigger': trigger,
            'user_messages': [{'id': m['id'], 'text': m['content']} for m in users],
            'work': [self.subagent_info(a['id']) for a in children],
            'pending_results': pending,
            'result_decisions_this_reply': self.decisions.get(turn, {}),
            'recorded_calls': calls,
        }

    def observe(self, turn, context):
        self.observed_results.setdefault(turn, set()).update(context.observed_results)
        if context.memory_snapshot:
            self.memory_snapshots[turn] = context.memory_snapshot

    def release_context(self, turn):
        self.memory_snapshots.pop(turn, None)
        self.memory_reads.pop(turn, None)

    def valid(self, agent_id, turn):
        agent = self.store.get_agent(agent_id)
        return agent['turn'] == turn and agent['status'] == 'running'

    def validate(self, agent_id, call):
        return validate_call(call, self.tools(agent_id))

    async def execute(self, agent_id, turn, call):
        if not self.valid(agent_id, turn):
            raise asyncio.CancelledError()
        try:
            name, args = self.validate(agent_id, call)
        except (KeyError, TypeError, ValueError):
            with self.store.db:
                self.store.record_exchange(agent_id, turn, call, {'error': 'invalid_tool_arguments'})
            return
        if name in ('read_memory', 'read_raw', 'set_memory_policy'):
            with self.store.db:
                mid = self.store.record_tool_call(agent_id, turn, call)
            try:
                memory = self.context.memory
                if name == 'set_memory_policy':
                    result = await self.harness.compiler.set_policy(args)
                elif name == 'read_memory':
                    snapshot = self.memory_snapshots.get(turn, memory.snapshot())
                    result = {'path': args['path'], 'content': memory.read(snapshot, args['path'])}
                else:
                    result = memory.read_raw(args['message_ids'], args.get('archive_source_message_id'))
                if name != 'set_memory_policy':
                    size = len(json.dumps(result, ensure_ascii=False).encode())
                    used = self.memory_reads.get(turn, 0) + size
                    if used > 32000:
                        raise ValueError('memory_read_budget')
                    self.memory_reads[turn] = used
            except (ValueError, KeyError) as error:
                result = {'error': str(error)}
            with self.store.db:
                self.store.record_tool_result(mid, result)
            if not self.valid(agent_id, turn):
                raise asyncio.CancelledError()
            return result
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
