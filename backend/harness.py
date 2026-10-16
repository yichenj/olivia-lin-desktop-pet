"""Minimal streaming harness and a deterministic offline test provider."""
import asyncio
import json
import time
from contextlib import aclosing
from dataclasses import dataclass
from .context import ContextProvider, NeedsCompaction
from .errors import ModelError


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
        from httpx import TimeoutException, TransportError
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
        # Once SSE headers arrive, httpx stream errors can bypass SDK wrapping.
        except (APITimeoutError, TimeoutException):
            raise ModelError("model_timeout", "模型响应超时，请稍后重试。") from None
        except (APIConnectionError, TransportError):
            raise ModelError("model_connection", "暂时连接不上模型，请检查网络后重试。") from None
        except APIStatusError as error:
            status = error.status_code
            body = str(getattr(error, 'body', '')).lower()
            if status in (400, 413) and any(code in body for code in
                    ('context_length', 'context window', 'maximum context', 'max_tokens', 'input too long')):
                raise ModelError('context_limit', '模型上下文超限，原始对话已保存。') from None
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
        if any(t['function']['name'] == 'write_memory' for t in options.get('tools', [])):
            # Deterministic file compilation for offline wiring tests only.
            data = json.loads(messages[1]['content'])
            if messages[-1]['role'] == 'tool':
                yield StreamPiece(tool_calls=[{'id': 'mock-memory-complete', 'type': 'function', 'function': {
                    'name': 'complete_memory', 'arguments': json.dumps({
                        'retained_message_ids': [m['id'] for m in data['raw']], 'ignored_message_ids': [],
                        'summary': '确定性模拟编译，仅验证调用链路。'})}}])
                return
            page = 'events/' + data['raw'][0]['id'] + '.md'
            content = '# 模拟记忆\n\n## 当前信息\n' + '\n'.join(
                m['content'][:120] + f" [来源](raw:message/{m['id']})" for m in data['raw'])
            content += '\n\n## 变化与待确认\n无\n\n## 关联\n无\n\n## 来源\n见上文来源。\n'
            edits = [(page, content), ('index.md', data['index'] + f'\n- [模拟对话]({page})')]
            yield StreamPiece(tool_calls=[{'id': f'mock-memory-{i}', 'type': 'function', 'function': {
                'name': 'write_memory', 'arguments': json.dumps({'path': path, 'content': text}, ensure_ascii=False)}}
                for i, (path, text) in enumerate(edits)])
            return
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


class AgentLoop:
    def __init__(self, context: ContextProvider, model, *, compiler=None,
                 token_budget=64000, active_timeout=300, memory_wait_timeout=370):
        self.context, self.model, self.compiler = context, model, compiler
        self.token_budget, self.active_timeout = token_budget, active_timeout
        self.memory_wait_timeout = memory_wait_timeout
        self.runtime = None

    def schedule_memory(self):
        if self.compiler:
            self.compiler.schedule()

    async def stream(self, agent_id, turn, *, trigger=None):
        deadline = time.monotonic() + (600 if agent_id != 1 else self.active_timeout)
        waited = 0
        budget = self.token_budget
        retried = False
        memory_snapshot = None
        try:
            for _ in range(12):
                while True:
                    if self.runtime and not self.runtime.valid(agent_id, turn):
                        raise asyncio.CancelledError()
                    context = self.context.build(agent_id, token_budget=budget, turn=turn,
                                                 trigger=trigger, memory_snapshot=memory_snapshot)
                    if not isinstance(context, NeedsCompaction):
                        break
                    current_version = self.context.memory.snapshot().version if self.context.memory else None
                    if memory_snapshot and memory_snapshot.version != current_version:
                        # Background compilation may have finished since the last
                        # model request. Adopt it before asking for more work.
                        memory_snapshot = None
                        continue
                    if not self.compiler or waited >= self.memory_wait_timeout:
                        raise ModelError('memory_timeout', '等待记忆整理超时，原始对话已保存。')
                    started = time.monotonic()
                    try:
                        progressed = await asyncio.wait_for(self.compiler.wait(), self.memory_wait_timeout - waited)
                    except asyncio.TimeoutError:
                        raise ModelError('memory_timeout', '等待记忆整理超时，原始对话已保存。') from None
                    elapsed = time.monotonic() - started
                    deadline += elapsed
                    waited += elapsed
                    memory_snapshot = None
                    if not progressed and self.context.memory.snapshot().version == current_version:
                        raise ModelError('memory_no_progress', '记忆整理未取得进展，原始对话已保存。')
                memory_snapshot = context.memory_snapshot
                if self.runtime and agent_id == 1:
                    self.runtime.observe(turn, context)
                options = {'tools': context.tools} if context.tools else {}
                if self.runtime:
                    options['thinking'] = 'disabled' if agent_id == 1 else 'enabled'
                calls, emitted = [], False
                try:
                    async with aclosing(self.model.stream(context.messages, **options)) as stream:
                        while True:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise asyncio.TimeoutError()
                            try:
                                piece = await asyncio.wait_for(stream.__anext__(), remaining)
                            except StopAsyncIteration:
                                break
                            if self.runtime and not self.runtime.valid(agent_id, turn):
                                raise asyncio.CancelledError()
                            if piece.tool_calls:
                                calls.extend(piece.tool_calls)
                            else:
                                emitted = emitted or bool(piece.text)
                                yield piece
                except ModelError as error:
                    if error.code == 'context_limit' and self.compiler and agent_id == 1 and not retried and not emitted:
                        retried = True
                        budget = int(budget * .75)
                        memory_snapshot = None
                        continue
                    raise
                if self.runtime and not self.runtime.valid(agent_id, turn):
                    raise asyncio.CancelledError()
                if not calls:
                    return
                if not self.runtime:
                    raise ModelError('tools_unavailable', '工具运行器未启用。')
                for call in calls:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise asyncio.TimeoutError()
                    await asyncio.wait_for(self.runtime.execute(agent_id, turn, call), remaining)
            raise ModelError('tool_limit', '本次工具调用达到上限，工作尚未完整完成。')
        finally:
            if self.runtime and agent_id == 1:
                self.runtime.release_context(turn)

    async def close(self):
        if self.compiler:
            await self.compiler.close()
