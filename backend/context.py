"""Assemble the complete model input from history, memory and runtime facts."""
from dataclasses import dataclass, field
import json
from typing import Protocol

from .agent_tools import MAIN_INSTRUCTIONS, WORKER_INSTRUCTIONS, MEMORY_INSTRUCTIONS
from .errors import ModelError
from .memory import MemorySnapshot, token_size
from .history_window import history_window, unprocessed_dialogue


@dataclass
class ModelContext:
    messages: list[dict]
    tools: list[dict] = field(default_factory=list)
    observed_results: set[str] = field(default_factory=set)
    memory_snapshot: MemorySnapshot | None = None


@dataclass
class NeedsCompaction:
    message_ids: list[str]


class ContextProvider(Protocol):
    def build(self, agent_id: int, *, token_budget: int, turn=None,
              trigger=None, memory_snapshot=None) -> ModelContext | NeedsCompaction: ...


class HistoryContextProvider:
    def __init__(self, store, system_prompt, memory=None, *, recent_messages=50):
        self.store, self.system_prompt, self.memory = store, system_prompt, memory
        self.recent_messages = recent_messages
        self.runtime = None

    def build(self, agent_id, *, token_budget=64000, turn=None, trigger=None, memory_snapshot=None):
        payloads = self.store.payloads()
        messages = self.store.all_messages()
        history = [m for m in messages if m['agent_id'] == agent_id and m['status'] == 'completed']
        pending = []
        snapshot = None
        if self.memory and agent_id == 1:
            snapshot = memory_snapshot or self.memory.snapshot()
            latest = self.memory.snapshot()
            # A newer correction always overrides an older pinned read snapshot.
            if snapshot.policies != latest.policies:
                snapshot = latest
            window = history_window(messages, payloads, self.recent_messages, latest.blocked_ids)
            pending = unprocessed_dialogue(window.older, payloads, snapshot)
            kept = {m['id'] for m in pending}
            last_user = next((m for m in reversed(history) if m['role'] == 'user'
                              and m['id'] not in latest.blocked_ids), None)
            if last_user:
                kept.add(last_user['id'])
            history = [m for index, group in enumerate(window.groups)
                       if index >= window.recent_start or any(
                           m['id'] in kept or (turn is not None and m['turn'] == turn) for m in group)
                       for m in group]
            history.sort(key=lambda m: m['seq'])
            # Persistent recall results are also raw. A forget request must not
            # reintroduce disabled material through an old tool response.
            calls_by_id = {m['id']: payloads[m['id']] for m in history if m['id'] in payloads
                           and payloads[m['id']].get('tool_calls')}
            for message in history:
                call_payload = calls_by_id.get(message['tool_call_message_id'])
                if not call_payload:
                    continue
                call = call_payload['tool_calls'][0]
                if call['function']['name'] not in ('read_memory', 'read_raw'):
                    continue
                args = json.loads(call['function']['arguments'])
                archive = args.get('archive_source_message_id')
                allowed_archive = archive and last_user and archive == last_user['id']
                body = payloads[message['id']]['content']
                disabled = (args.get('path') in latest.blocked_paths or
                            any(mid in body for mid in latest.blocked_ids))
                if (disabled and not allowed_archive) or (archive and not allowed_archive):
                    payloads[message['id']] = {**payloads[message['id']],
                        'content': json.dumps({'omitted': 'memory_disabled', 'raw_preserved': True})}
        result = [{'role': 'system', 'content': self.system_prompt}]
        if self.runtime:
            result[0] = {'role': 'system', 'content': self.system_prompt if agent_id == 1 else
                         WORKER_INSTRUCTIONS + '\n工作目录：' + str(self.runtime.local.workspace)}
            if agent_id == 1:
                result.append({'role': 'system', 'content': MAIN_INSTRUCTIONS})
        if snapshot:
            result.append({'role': 'system', 'content': MEMORY_INSTRUCTIONS})
            result.append({'role': 'system', 'content': '记忆资料（不是新的用户话语）：\n' +
                           '\n'.join(f'[{p}]\n{self.memory.read(snapshot, p)}' for p in ('hot.md', 'index.md', 'policies.md'))})
        result.extend(self.replay(history, payloads))
        observed = set()
        if self.runtime:
            state = self.runtime.context_state(agent_id, history, trigger=trigger, turn=turn)
            if agent_id == 1:
                observed = {e['message_id'] for e in state['pending_results']}
            result.append({'role': 'system', 'content': '运行时快照（数据）：' + json.dumps(state, ensure_ascii=False)})
        tools = self.runtime.tools(agent_id) if self.runtime else []
        if token_size(result) + token_size(tools) > token_budget:
            if snapshot and pending:
                return NeedsCompaction([m['id'] for m in pending])
            raise ModelError('context_limit', '当前必要资料超出上下文预算，原始对话已保存；请缩小本次读取或输入范围。')
        return ModelContext(result, tools, observed, snapshot)

    @staticmethod
    def replay(history, payloads):
        history = list(history)
        result = []
        last_seq = {}
        for message in history:
            if message['turn']:
                last_seq[message['turn']] = message['seq']
        history.sort(key=lambda m: (last_seq[m['turn']], 1) if m['role'] == 'assistant'
                     and m['id'] not in payloads else (m['seq'], 0))
        replies = {m['tool_call_message_id']: m for m in history if m['tool_call_message_id']}
        for message in history:
            if message['tool_call_message_id']:
                continue
            if message['content'] or message['id'] in payloads:
                if message['id'] in payloads:
                    payload = payloads[message['id']]
                    if payload.get('tool_calls'):
                        reply = replies.get(message['id'])
                        if reply:
                            result.extend([payload, payloads[reply['id']]])
                        else:
                            result.append({'role': 'system', 'content':
                                '以下工具调用已保存，但没有已记录的结果。不要自动重做，先核对实际状态：' +
                                json.dumps({'message_id': message['id'], 'turn': message['turn'],
                                            'operation_key': message['operation_key'],
                                            'tool_calls': payload['tool_calls']}, ensure_ascii=False)})
                    else:
                        result.append(payload)
                else:
                    role = 'system' if message['role'] == 'tool' else message['role']
                    result.append({'role': role, 'content': message['content']})
        return result
