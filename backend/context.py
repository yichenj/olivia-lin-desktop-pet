"""Replace ContextProvider to add memory retrieval without changing UI or RPC."""
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

    def build(self, agent_id):
        result = [{"role": "system", "content": self.system_prompt}]
        for message in self.history:
            if message["agent_id"] == agent_id and message["status"] == "completed":
                result.append({"role": message["role"], "content": message["content"]})
        return result
