"""Minimal streaming harness and a deterministic offline test provider."""
import asyncio
from contextlib import aclosing
from dataclasses import dataclass
from .context import ContextProvider


class ModelError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass
class StreamPiece:
    text: str = ""
    usage: dict | None = None
    tool_calls: list | None = None


class ArkModel:
    def __init__(self, config):
        self.config = config

    async def stream(self, messages, *, tools=None, thinking=None):
        if not self.config.api_key.strip():
            raise ModelError("missing_api_key", "请在本地配置中填写 api_key 后重新启动桌宠。")
        missing = [name for name in ("base_url", "model") if not getattr(self.config, name).strip()]
        if missing:
            raise ModelError("missing_model_config", "请在本地配置中填写 " + "、".join(missing) + " 后重新启动桌宠。")
        from openai import AsyncOpenAI, APIConnectionError, APITimeoutError, APIStatusError
        client = AsyncOpenAI(api_key=self.config.api_key, base_url=self.config.base_url,
                             timeout=60.0, max_retries=0)
        response = None
        finished = False
        calls = {}
        try:
            response = await client.chat.completions.create(
                model=self.config.model, messages=messages, stream=True,
                extra_body={"thinking": {"type": thinking or self.config.thinking}},
                **({'tools': tools} if tools else {}))
            async for chunk in response:
                if chunk.usage:
                    yield StreamPiece(usage=chunk.usage.model_dump())
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.delta.content:
                    yield StreamPiece(text=choice.delta.content)
                for delta in getattr(choice.delta, 'tool_calls', None) or []:
                    call = calls.setdefault(delta.index, {'id': '', 'type': 'function',
                                                         'function': {'name': '', 'arguments': ''}})
                    if delta.id:
                        call['id'] += delta.id
                    if delta.function:
                        call['function']['name'] += delta.function.name or ''
                        call['function']['arguments'] += delta.function.arguments or ''
                    if sum(len(c['function']['arguments']) for c in calls.values()) > 250000 or len(calls) > 16:
                        raise ModelError('tool_calls_too_large', '模型工具调用过大。')
                if choice.finish_reason:
                    if choice.finish_reason not in ("stop", "tool_calls"):
                        raise ModelError("incomplete_output", "本次回复未完整生成，请补充要求或重试。")
                    if choice.finish_reason == 'tool_calls' and not calls:
                        raise ModelError('invalid_tool_calls', '模型返回了空工具调用。')
                    finished = True
            if not finished:
                raise ModelError("stream_interrupted", "连接提前结束，回复尚未完成。")
            if calls:
                yield StreamPiece(tool_calls=[calls[i] for i in sorted(calls)])
        except APITimeoutError:
            raise ModelError("model_timeout", "模型响应超时，请稍后重试。") from None
        except APIConnectionError:
            raise ModelError("model_connection", "暂时连接不上模型，请检查网络后重试。") from None
        except APIStatusError as error:
            status = error.status_code
            if status in (401, 403):
                message = "模型认证或访问权限失败，请检查 API Key 和接入点配置。"
            elif status == 429:
                message = "模型暂时限流或额度不足，请稍后重试。"
            else:
                message = f"模型请求失败（HTTP {status}），请检查配置或稍后重试。"
            # Never forward provider exception strings, request headers or credentials.
            raise ModelError(f"model_http_{status}", message) from None
        finally:
            if response is not None:
                await response.close()
            await client.close()


class MockModel:
    async def stream(self, messages, **options):
        users = [m["content"] for m in messages if m["role"] == "user"]
        latest = users[-1]
        if latest == "[test:error]":
            yield StreamPiece(text="这是一段未完成的回复")
            raise ModelError("mock_error", "模拟连接中断。")
        if latest == "[test:slow]":
            await asyncio.sleep(60)
        text = "我听到了：" + " / ".join(users) + "。\n我们慢慢聊，不着急。"
        if latest == "[test:long]":
            text += ("\n窗外的雨很轻。你可以慢慢读，也可以先停在这里。" * 30)
        for offset in range(0, len(text), 6):
            await asyncio.sleep(.025)
            yield StreamPiece(text=text[offset:offset + 6])


class AgentHarness:
    def __init__(self, context: ContextProvider, model):
        self.context = context
        self.model = model
        self.runtime = None

    async def stream(self, agent_id, turn, *, trigger=None):
        for _ in range(12):
            if self.runtime and not self.runtime.valid(agent_id, turn):
                raise asyncio.CancelledError()
            messages = self.context.build(agent_id)
            options = self.runtime.model_options(agent_id) if self.runtime else {}
            if self.runtime:
                messages = self.runtime.decorate(agent_id, messages, trigger=trigger)
            calls = []
            async with aclosing(self.model.stream(messages, **options)) as stream:
                async for piece in stream:
                    if self.runtime and not self.runtime.valid(agent_id, turn):
                        raise asyncio.CancelledError()
                    if piece.tool_calls:
                        calls.extend(piece.tool_calls)
                    else:
                        yield piece
            if self.runtime and not self.runtime.valid(agent_id, turn):
                raise asyncio.CancelledError()
            if not calls:
                return
            if not self.runtime:
                raise ModelError('tools_unavailable', '工具运行器未启用。')
            for call in calls:
                await self.runtime.execute(agent_id, turn, call)
            self.context.refresh()
        raise ModelError('tool_limit', '本次工具调用达到上限，工作尚未完整完成。')
