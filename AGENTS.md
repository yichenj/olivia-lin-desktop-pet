# Product principles

- This pet is a continuous, natural conversation with Olivia, not a task-control interface. There is no user-facing thread or session management.
- Do not add Stop/Cancel generation buttons or menu items. Further user input steers the current reply; cancellation is an internal mechanism for steer, shutdown, and tests.
- The speech bubble is content-sized and rounded/oval. Short replies stay compact; scrollbars appear only after the maximum size is reached. Closing the bubble only hides it.
- Keep the bubble free of controls, including close crosses. Close/reopen belongs to one context-menu toggle (关闭对话 / 展开对话); the card's 对话 entry uses the same behavior. Avoid technical execution status, task headers, or native button chrome in the character's speech.
- Model text must stream from upstream SSE through JSON-RPC notifications to the UI without waiting for completion. Default conversational mode disables deep thinking to reduce first-text latency.
- Preserve the portrait artwork and geometry when changing chat UI. Document UI decisions in docs/AGENT_BACKEND.md and protocol/storage contracts in their separate documents.
