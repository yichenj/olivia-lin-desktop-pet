"""Application service. Requests are serialized; generation is cancellable work."""
import asyncio
from dataclasses import dataclass
import sys
import time

from .harness import ModelError


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass
class Generation:
    chat_id: str
    number: int
    message_id: str
    text: str = ""
    task: asyncio.Task | None = None
    usage: dict | None = None

    def identity(self):
        return {"chatId": self.chat_id, "generation": self.number, "messageId": self.message_id}


class ChatService:
    def __init__(self, store, context, harness, model_name, emit):
        self.store, self.context, self.harness = store, context, harness
        self.model_name, self.emit = model_name, emit
        self.active = None

    def notify(self, method, params):
        self.emit({"jsonrpc": "2.0", "method": method, "params": params})

    def result(self, request, value):
        if "id" in request:
            self.emit({"jsonrpc": "2.0", "id": request["id"], "result": value})

    async def handle(self, request):
        method, params = request["method"], request.get("params", {})
        if not isinstance(params, dict):
            raise RpcError(-32602, "params 必须是对象")
        if method == "initialize":
            self.result(request, {"protocolVersion": 1, "capabilities": ["streaming", "steer", "history", "cancel"]})
        elif method in ("chat/send", "chat/steer"):
            text = params.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 10000:
                raise RpcError(-32602, "请输入 1–10000 字的消息")
            text = text.strip()
            if method == "chat/send":
                if self.active:
                    raise RpcError(-32001, "正在回复，请使用补充消息")
                chat_id, message_id = self.store.begin(text, self.model_name)
                current = Generation(chat_id, 1, message_id)
            else:
                previous = self.active
                if not previous or params.get("chatId") != previous.chat_id:
                    raise RpcError(-32002, "上一条回复已结束，请重新发送这条消息")
                await self.stop_task(previous)
                number = previous.number + 1
                message_id = self.store.steer(previous.chat_id, number, previous.message_id, previous.text, text)
                current = Generation(previous.chat_id, number, message_id)
            self.active = current
            self.context.refresh()
            self.result(request, current.identity())
            self.notify("chat/started", {**current.identity(), "reset": True})
            current.task = asyncio.create_task(self.generate(current))
        elif method == "chat/cancel":
            current = self.active
            if not current or params.get("chatId") != current.chat_id:
                raise RpcError(-32002, "没有正在进行的回复")
            await self.stop_task(current)
            self.result(request, {})
            self.finish(current, "interrupted", "cancelled", "已停止回复")
        elif method == "history/list":
            limit, before = params.get("limit", 50), params.get("before")
            if (type(limit) is not int or not 1 <= limit <= 200 or
                    (before is not None and (type(before) is not int or not 0 < before <= 9223372036854775807))):
                raise RpcError(-32602, "分页参数不合法")
            self.result(request, self.store.list_messages(before, limit))
        else:
            raise RpcError(-32601, "不支持的方法")

    async def generate(self, current):
        checkpoint = time.monotonic()
        try:
            # Total deadline prevents an upstream stream from keeping the UI busy forever.
            await asyncio.wait_for(self.consume(current, checkpoint), timeout=300)
            if not current.text.strip():
                raise ModelError("empty_reply", "模型没有返回文字，请重试。")
            self.finish(current, "completed")
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self.finish(current, "failed", "generation_timeout", "本次回复超时，请重试。")
        except ModelError as error:
            self.finish(current, "failed", error.code, str(error))
        except Exception as error:
            print(f"Generation failed: {type(error).__name__}", file=sys.stderr, flush=True)
            self.finish(current, "failed", "internal_error", "回复过程中发生错误，请重试。")

    async def consume(self, current, checkpoint):
        stream = self.harness.stream(current.chat_id)
        try:
            async for piece in stream:
                if self.active is not current:
                    return
                if piece.usage:
                    current.usage = piece.usage
                if piece.text:
                    current.text += piece.text
                    if len(current.text) > 200000:
                        raise ModelError("reply_too_long", "回复过长，已停止生成。")
                    self.notify("chat/delta", {**current.identity(), "text": piece.text})
                if time.monotonic() - checkpoint >= .25:
                    self.store.checkpoint(current.message_id, current.text)
                    checkpoint = time.monotonic()
        finally:
            await stream.aclose()

    def finish(self, current, status, code=None, message=None):
        if self.active is not current:
            return
        self.store.finish(current.chat_id, current.message_id, current.text, status, code, current.usage)
        self.context.refresh()
        self.active = None
        params = {**current.identity(), "status": status, "text": current.text}
        if code:
            params["error"] = {"code": code, "message": message}
        self.notify("chat/completed", params)

    async def stop_task(self, current):
        if current.task:
            current.task.cancel()
            try:
                await current.task
            except asyncio.CancelledError:
                pass

    async def close(self):
        if self.active:
            current = self.active
            await self.stop_task(current)
            # EOF means there is no client to receive a terminal notification.
            self.store.finish(current.chat_id, current.message_id, current.text, "interrupted", "process_exit")
            self.active = None
