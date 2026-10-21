"""Shared raw-history boundary for context assembly and memory compilation."""
from dataclasses import dataclass

from .memory import fingerprint


@dataclass
class HistoryWindow:
    groups: list[list[dict]]
    recent_start: int

    @property
    def older(self):
        return [m for group in self.groups[:self.recent_start] for m in group]


def history_window(messages, payloads, recent_messages, blocked_ids=()):
    """Protect the last N dialogue messages, extending to whole exchanges.

    A user message starts an in-memory group. Drafts establish turn ownership
    even if superseded, so a late tool result belongs with its original call.
    Tools, drafts and disabled rows do not consume the retention quota. No grouping
    identity is stored, and user messages still have no turn.
    """
    groups, owners, turns = [], {}, {}
    completed = []
    for message in messages:
        if message['agent_id'] != 1:
            continue
        if message['role'] == 'user' or not groups:
            groups.append([])
        owner = len(groups) - 1
        if message['turn'] is not None:
            owner = turns.setdefault(message['turn'], owner)
        owner = owners.get(message['tool_call_message_id'], owner)
        owners[message['id']] = owner
        if message['status'] == 'completed' and message['id'] not in blocked_ids:
            groups[owner].append(message)
            if message['role'] in ('user', 'assistant') and message['id'] not in payloads:
                completed.append(message)
    start = len(groups)
    if recent_messages > 0 and completed:
        start = min(owners[m['id']] for m in completed[-recent_messages:])
    return HistoryWindow(groups, start)


def unprocessed_dialogue(rows, payloads, snapshot):
    return [m for m in rows if m['role'] in ('user', 'assistant') and m['id'] not in payloads
            and snapshot.processed.get(m['id']) != fingerprint(m)]
