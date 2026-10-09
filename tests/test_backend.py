"""Backend state, recovery, context isolation, and steer cancellation contracts."""
import asyncio
from pathlib import Path
import tempfile
import unittest

from backend.context import FullHistoryContextProvider
from backend.harness import AgentHarness, MockModel, ModelError, StreamPiece
from backend.service import ChatService, RpcError
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
        await self.request("chat/send", text="别推荐音乐，聊电影")
        new = self.service.active
        self.assertEqual(new.agent_id, old.agent_id)
        self.assertEqual(new.number, 2)
        self.assertTrue(old.task.cancelled())
        await new.task
        history = self.store.all_messages()
        self.assertEqual([m["status"] for m in history], ["completed", "superseded", "completed", "completed"])
        messages = self.context.build(1)
        self.assertEqual([m["role"] for m in messages], ["system", "user", "user", "assistant"])
        self.assertIn("下雨了 / 别推荐音乐", messages[-1]["content"])
        terminals = [e for e in self.events if e.get("method") == "chat/completed"]
        self.assertEqual(len(terminals), 1)
        self.assertEqual(terminals[0]["params"]["messageId"], new.message_id)
        # Accepted before reset; old generation emits no later fragments.
        reset = next(i for i, e in enumerate(self.events) if e.get("method") == "chat/started" and e["params"]["messageId"] == new.message_id)
        self.assertFalse(any(e.get("params", {}).get("messageId") == old.message_id for e in self.events[reset:]))

    async def test_failure_keeps_user_input_but_excludes_failed_draft(self):
        await self.request("chat/send", text="[test:error]")
        await self.service.active.task
        records = self.store.all_messages()
        self.assertEqual(records[-1]["status"], "failed")
        self.assertTrue(records[-1]["content"])
        self.assertEqual(self.context.build(1), [{"role": "system", "content": "test persona"}, {"role": "user", "content": "[test:error]"}])
        self.assertEqual(self.events[-1]["params"]["error"]["code"], "mock_error")

    async def test_shutdown_preserves_partial_without_client_cancel(self):
        await self.request("chat/send", text="讲个故事")
        current = self.service.active
        await asyncio.sleep(.08)
        await self.service.close()
        self.assertEqual(self.store.all_messages()[-1]["status"], "interrupted")
        self.assertTrue(self.store.all_messages()[-1]["content"])
        self.assertIsNone(self.service.active)
        await self.request("chat/send", text="第二次")
        await self.service.close()
        self.assertEqual(self.store.all_messages()[-1]["status"], "interrupted")

    async def test_restart_recovers_running_and_loads_completed_history(self):
        await self.request("chat/send", text="我叫小林")
        await self.service.active.task
        generation, message_id = self.store.begin("还没回复", "mock")
        self.store.checkpoint(message_id, "未完")
        self.store.close()
        self.store = HistoryStore(self.path)
        self.service.store = self.store
        context = FullHistoryContextProvider(self.store, "test persona")
        self.assertEqual(self.store.all_messages()[-1]["status"], "interrupted")
        self.assertEqual([m["content"] for m in context.build(1) if m["role"] == "user"], ["我叫小林", "还没回复"])

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

    async def test_slow_model_does_not_block_new_input(self):
        await self.request("chat/send", text="[test:slow]")
        current = self.service.active
        await asyncio.wait_for(self.request("chat/send", text="现在回答"), .5)
        await self.service.active.task
        self.assertEqual(self.events[-1]["params"]["status"], "completed")

    async def test_large_history_pages_are_bounded_without_losing_records(self):
        for _ in range(4):
            generation, message_id = self.store.begin("input", "mock")
            self.store.finish(1, generation, message_id, "字" * 200000, "completed")
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

    async def test_generation_is_agent_scoped_and_survives_restart(self):
        for expected in (1, 2):
            await self.request("chat/send", text="你好")
            self.assertEqual(self.service.active.agent_id, 1)
            self.assertEqual(self.service.active.number, expected)
            await self.service.active.task
        self.store.close()
        self.store = HistoryStore(self.path)
        self.service.store = self.store
        self.context.store = self.store
        await self.request("chat/send", text="继续")
        self.assertEqual(self.service.active.number, 3)
        await self.service.active.task
        messages = self.store.all_messages()
        self.assertTrue(all(m["generation"] is None for m in messages if m["role"] == "user"))
        self.assertEqual([m["generation"] for m in messages if m["role"] == "assistant"], [1, 2, 3])

    async def test_new_input_is_text_only_and_old_generation_cannot_commit(self):
        await self.request("chat/send", text="[test:slow]")
        old = self.service.active
        await self.request("chat/send", text="新内容")
        current = self.service.active
        for params in ({"generation": True}, {"agentId": 2}, {"messageId": "not-a-target"}):
            with self.assertRaises(RpcError) as error:
                await self.request("chat/send", text="错误目标", **params)
            self.assertEqual(error.exception.code, -32602)
        self.assertIs(self.service.active, current)
        self.assertNotIn("迟到消息", [m["content"] for m in self.store.all_messages()])
        self.assertFalse(self.store.finish(1, old.number, old.message_id, "迟到结果", "completed"))
        self.store.checkpoint(old.message_id, "迟到检查点")
        self.assertEqual(self.store.all_messages()[1]["status"], "superseded")
        await current.task

    async def test_histories_are_isolated_by_agent_and_parent(self):
        await self.request("chat/send", text="主对话")
        await self.service.active.task
        source = self.store.all_messages()[0]["id"]
        child = self.store.create_agent(source_message_id=source)
        nested = self.store.create_agent(parent_agent_id=child)
        self.assertEqual(self.store.get_agent(child)["parent_agent_id"], 1)
        self.assertEqual(self.store.get_agent(nested)["parent_agent_id"], child)
        generation, message_id = self.store.begin("子任务资料", "mock", agent_id=child)
        self.store.finish(child, generation, message_id, "子任务结果", "completed")
        self.context.refresh()
        main = self.context.build(1)
        sub = self.context.build(child)
        self.assertNotIn("子任务资料", [m["content"] for m in main])
        self.assertEqual([m["content"] for m in sub][1:], ["子任务资料", "子任务结果"])
        self.assertTrue(all(m["agent_id"] == child for m in self.store.list_messages(agent_id=child)["messages"]))
        self.assertTrue(all(m["agent_id"] == 1 for m in self.store.list_messages()["messages"]))
        self.assertEqual(self.store.get_agent(1)["generation"], 1)
        self.assertEqual(self.store.get_agent(child)["generation"], 1)

    async def test_agent_tree_and_message_role_constraints(self):
        import sqlite3
        root = self.store.get_agent(1)
        self.assertIsNone(root["parent_agent_id"])
        self.assertEqual(root["generation"], 0)
        await self.request("chat/send", text="保留原话")
        await self.service.active.task
        user, assistant = self.store.all_messages()
        child = self.store.create_agent(source_message_id=user["id"])
        with self.assertRaises(sqlite3.IntegrityError), self.store.db:
            self.store.db.execute("UPDATE agents SET parent_agent_id=? WHERE id=1", (child,))
        with self.assertRaises(sqlite3.IntegrityError), self.store.db:
            self.store.db.execute("UPDATE messages SET generation=1 WHERE id=?", (user["id"],))
        with self.assertRaises(sqlite3.IntegrityError), self.store.db:
            self.store.db.execute("UPDATE messages SET generation=NULL WHERE id=?", (assistant["id"],))
        self.assertIsNone(self.store.get_agent(1)["parent_agent_id"])
        self.assertEqual(self.store.get_agent(child)["parent_agent_id"], 1)

    async def test_send_before_and_after_completion_has_identical_contract(self):
        await self.request("chat/send", text="第一句")
        first = self.service.active
        await self.request("chat/send", text="第二句")
        second = self.service.active
        self.assertEqual(second.number, first.number + 1)
        await second.task
        await self.request("chat/send", text="已经说完了，再聊一句")
        third = self.service.active
        await third.task
        self.assertEqual(third.number, second.number + 1)
        acknowledgements = [e["result"] for e in self.events if "result" in e]
        self.assertEqual(acknowledgements, [{}, {}, {}])
        for event in self.events:
            params = event.get("params", {})
            self.assertNotIn("agentId", params)
            self.assertNotIn("generation", params)
        users = [m["content"] for m in self.store.all_messages() if m["role"] == "user"]
        self.assertEqual(users, ["第一句", "第二句", "已经说完了，再聊一句"])

    async def test_input_order_survives_rapid_sends_and_invalid_text(self):
        for text in ("一", "二", "三"):
            await self.request("chat/send", text=text)
        current = self.service.active
        with self.assertRaises(RpcError):
            await self.request("chat/send", text=" ")
        self.assertIs(self.service.active, current)
        await current.task
        users = [m["content"] for m in self.store.all_messages() if m["role"] == "user"]
        self.assertEqual(users, ["一", "二", "三"])
