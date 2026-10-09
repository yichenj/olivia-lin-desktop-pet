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


class ArkModel:
    def __init__(self, config):
        self.config = config

    async def stream(self, messages):
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
        try:
            response = await client.chat.completions.create(
                model=self.config.model, messages=messages, stream=True,
                extra_body={"thinking": {"type": self.config.thinking}})
            async for chunk in response:
                if chunk.usage:
                    yield StreamPiece(usage=chunk.usage.model_dump())
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.delta.content:
                    yield StreamPiece(text=choice.delta.content)
                if choice.finish_reason:
                    if choice.finish_reason != "stop":
                        raise ModelError("incomplete_output", "本次回复未完整生成，请补充要求或重试。")
                    finished = True
            if not finished:
                raise ModelError("stream_interrupted", "连接提前结束，回复尚未完成。")
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
    async def stream(self, messages):
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

    async def stream(self, agent_id):
        async with aclosing(self.model.stream(self.context.build(agent_id))) as stream:
            async for piece in stream:
                yield piece
