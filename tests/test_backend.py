"""Backend state, recovery, context isolation, and steer cancellation contracts."""
import asyncio
from pathlib import Path
import tempfile
import unittest

from backend.context import FullHistoryContextProvider
from backend.harness import AgentHarness, MockModel, ModelError, StreamPiece
from backend.service import ChatService
from backend.storage import HistoryStore


class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "history.sqlite3"
        self.store = HistoryStore(self.path)
        self.context = FullHistoryContextProvider(self.store, "test persona")
        self.events = []
        self.service = ChatService(self.store, self.context, AgentHarness(self.context, MockModel()), "mock", self.events.append)

    async def asyncTearDown(self):
        await self.service.close()
        self.store.close()
        self.directory.cleanup()

    async def request(self, method, **params):
        await self.service.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})

    async def test_steer_retains_inputs_and_excludes_old_draft(self):
        await self.request("chat/send", text="下雨了")
        old = self.service.active
        await asyncio.sleep(.09)
        self.assertTrue(old.text)
        await self.request("chat/steer", chatId=old.chat_id, text="别推荐音乐，聊电影")
        new = self.service.active
        self.assertEqual(new.chat_id, old.chat_id)
        self.assertEqual(new.number, 2)
        self.assertTrue(old.task.cancelled())
        await new.task
        history = self.store.all_messages()
        self.assertEqual([m["status"] for m in history], ["completed", "superseded", "completed", "completed"])
        messages = self.context.build("next")
        self.assertEqual([m["role"] for m in messages], ["system", "user", "user", "assistant"])
        self.assertIn("下雨了 / 别推荐音乐", messages[-1]["content"])
        terminals = [e for e in self.events if e.get("method") == "chat/completed"]
        self.assertEqual(len(terminals), 1)
        self.assertEqual(terminals[0]["params"]["generation"], 2)
        # Accepted before reset; old generation emits no later fragments.
        reset = next(i for i, e in enumerate(self.events) if e.get("method") == "chat/started" and e["params"]["generation"] == 2)
        self.assertFalse(any(e.get("params", {}).get("generation") == 1 for e in self.events[reset:]))

    async def test_failure_persists_partial_but_context_excludes_failed_run(self):
        await self.request("chat/send", text="[test:error]")
        await self.service.active.task
        records = self.store.all_messages()
        self.assertEqual(records[-1]["status"], "failed")
        self.assertTrue(records[-1]["content"])
        self.assertEqual(self.context.build("next"), [{"role": "system", "content": "test persona"}])
        self.assertEqual(self.events[-1]["params"]["error"]["code"], "mock_error")

    async def test_cancel_and_eof_preserve_partial(self):
        await self.request("chat/send", text="讲个故事")
        current = self.service.active
        await asyncio.sleep(.08)
        await self.request("chat/cancel", chatId=current.chat_id)
        self.assertEqual(self.events[-1]["params"]["status"], "interrupted")
        self.assertIsNone(self.service.active)
        await self.request("chat/send", text="第二次")
        await self.service.close()
        self.assertEqual(self.store.all_messages()[-1]["run_status"], "interrupted")

    async def test_restart_recovers_running_and_loads_completed_history(self):
        await self.request("chat/send", text="我叫小林")
        await self.service.active.task
        chat_id, message_id = self.store.begin("还没回复", "mock")
        self.store.checkpoint(message_id, "未完")
        self.store.close()
        self.store = HistoryStore(self.path)
        self.service.store = self.store
        context = FullHistoryContextProvider(self.store, "test persona")
        self.assertEqual(self.store.all_messages()[-1]["status"], "interrupted")
        self.assertEqual([m["content"] for m in context.build("next") if m["role"] == "user"], ["我叫小林"])

    async def test_pagination_and_single_owner(self):
        with self.assertRaises(RuntimeError):
            HistoryStore(self.path)
        for text in ("甲", "乙", "丙"):
            await self.request("chat/send", text=text)
            await self.service.active.task
        page = self.store.list_messages(limit=2)
        second = self.store.list_messages(before=page["nextCursor"], limit=2)
        self.assertEqual([m["seq"] for m in page["messages"]], [5, 6])
        self.assertEqual([m["seq"] for m in second["messages"]], [3, 4])

    async def test_slow_model_does_not_block_history_or_steer(self):
        await self.request("chat/send", text="[test:slow]")
        current = self.service.active
        await asyncio.wait_for(self.request("history/list", limit=10), .5)
        await asyncio.wait_for(self.request("chat/steer", chatId=current.chat_id, text="现在回答"), .5)
        await self.service.active.task
        self.assertEqual(self.events[-1]["params"]["status"], "completed")

    async def test_large_history_pages_are_bounded_without_losing_records(self):
        for _ in range(4):
            chat_id, message_id = self.store.begin("input", "mock")
            self.store.finish(chat_id, message_id, "字" * 200000, "completed")
        import json
        before, collected = None, []
        while True:
            page = self.store.list_messages(before, 200)
            self.assertLess(len(json.dumps(page, ensure_ascii=False).encode("utf-8")), 1100000)
            collected.extend(m["seq"] for m in page["messages"])
            before = page["nextCursor"]
            if before is None:
                break
        self.assertEqual(sorted(collected), list(range(1, 9)))

    async def test_delta_is_sent_before_model_finishes(self):
        release = asyncio.Event()
        class PausedModel:
            async def stream(self, messages):
                yield StreamPiece(text='第一句')
                await release.wait()
                yield StreamPiece(text='，第二句。')
        self.service.harness.model = PausedModel()
        await self.request('chat/send', text='你好')
        current = self.service.active
        for _ in range(20):
            if any(e.get('method') == 'chat/delta' for e in self.events):
                break
            await asyncio.sleep(.01)
        self.assertEqual([e['params']['text'] for e in self.events if e.get('method') == 'chat/delta'], ['第一句'])
        self.assertFalse(current.task.done())
        self.assertFalse(any(e.get('method') == 'chat/completed' for e in self.events))
        release.set()
        await current.task
        self.assertEqual(self.events[-1]['params']['text'], '第一句，第二句。')
