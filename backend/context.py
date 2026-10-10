"""Replace ContextProvider to add memory retrieval without changing UI or RPC."""
import json
from typing import Protocol


class ContextProvider(Protocol):
    def refresh(self) -> None: ...
    def build(self, agent_id: int) -> list[dict]: ...


class FullHistoryContextProvider:
    def __init__(self, store, system_prompt):
        self.store = store
        self.system_prompt = system_prompt
        self.refresh()

    def refresh(self):
        # Keep the complete persisted transcript loaded, including audit records.
        self.history = self.store.all_messages()
        self.payloads = self.store.payloads()

    def build(self, agent_id):
        result = [{"role": "system", "content": self.system_prompt}]
        history = [m for m in self.history if m['agent_id'] == agent_id and m['status'] == 'completed']
        # Visible drafts are allocated before their tool calls but become final text afterwards.
        last_seq = {}
        for message in history:
            if message['turn']:
                last_seq[message['turn']] = message['seq']
        history.sort(key=lambda m: (last_seq[m['turn']], 1) if m['role'] == 'assistant'
                     and m['id'] not in self.payloads else (m['seq'], 0))
        replies = {m['tool_call_message_id']: m for m in history if m['tool_call_message_id']}
        for message in history:
            if message['tool_call_message_id']:
                continue  # Replayed immediately after its call, even if recorded after a new input.
            if message['content'] or message['id'] in self.payloads:
                if message['id'] in self.payloads:
                    payload = self.payloads[message['id']]
                    if payload.get('tool_calls'):
                        reply = replies.get(message['id'])
                        if reply:
                            result.extend([payload, self.payloads[reply['id']]])
                        else:
                            # Preserve the uncertainty without inventing a provider tool response.
                            result.append({'role': 'system', 'content':
                                '以下工具调用已保存，但没有已记录的结果。不要自动重做，先核对实际状态：' +
                                json.dumps({'message_id': message['id'], 'turn': message['turn'],
                                            'operation_key': message['operation_key'],
                                            'tool_calls': payload['tool_calls']}, ensure_ascii=False)})
                    else:
                        result.append(payload)
                else:
                    # Runtime facts are not provider tool responses without a tool_call_id.
                    role = 'system' if message['role'] == 'tool' else message['role']
                    result.append({"role": role, "content": message["content"]})
        return result
