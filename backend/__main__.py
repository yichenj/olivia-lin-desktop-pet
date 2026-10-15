"""Run with python -m backend. stdout is exclusively JSON-RPC JSONL."""
import asyncio
import json
import sys

from .config import load_config
from .context import HistoryContextProvider
from .harness import AgentLoop, ArkModel, MockModel
from .service import ChatService, RpcError
from .storage import HistoryStore
from .runtime import AgentRuntime
from .local_tools import LocalTools
from .memory import MemoryStore
from .memory_worker import MemoryCompiler


def emit(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


async def serve():
    config = load_config()
    if config.provider not in ("ark", "mock"):
        raise ValueError("unknown_provider")
    prompt = config.prompt.read_text(encoding="utf-8")
    store = HistoryStore(config.database)
    service = None
    transport = None
    try:
        model = MockModel() if config.provider == "mock" else ArkModel(config)
        memory = MemoryStore(store)
        context = HistoryContextProvider(store, prompt, memory, recent_messages=config.memory_recent_messages)
        compiler = MemoryCompiler(store, memory, model, interval=config.memory_interval_seconds,
                                  threshold=config.memory_threshold_bytes, input_budget=config.context_token_budget)
        harness = AgentLoop(context, model, compiler=compiler, token_budget=config.context_token_budget)
        AgentRuntime(store, context, harness, LocalTools(config.workspace, config.shell_enabled, secrets=(config.api_key,)), config.model)
        service = ChatService(store, context, harness, config.model, emit)
        store.on_change = compiler.schedule
        compiler.schedule()
        reader = asyncio.StreamReader(limit=1024 * 1024)
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
        service.notify("backend/ready", {})
        service.schedule_results()
        while line := await reader.readline():
            request = None
            try:
                try:
                    request = json.loads(line)
                except (ValueError, UnicodeError):
                    raise RpcError(-32700, "JSON 解析失败") from None
                if (not isinstance(request, dict) or request.get("jsonrpc") != "2.0"
                        or not isinstance(request.get("method"), str)
                        or ("id" in request and type(request["id"]) not in (str, int, type(None)))):
                    raise RpcError(-32600, "无效的 JSON-RPC 请求")
                await service.handle(request)
            except RpcError as error:
                invalid = error.code in (-32700, -32600)
                if invalid or "id" in request:
                    request_id = request.get("id") if isinstance(request, dict) and not invalid else None
                    emit({"jsonrpc": "2.0", "id": request_id,
                          "error": {"code": error.code, "message": str(error)}})
            except Exception as error:
                print(f"Request failed: {type(error).__name__}", file=sys.stderr, flush=True)
                if isinstance(request, dict) and "id" in request:
                    emit({"jsonrpc": "2.0", "id": request["id"],
                          "error": {"code": -32603, "message": "后台处理失败"}})
                # A persistence failure can leave execution state inconsistent; fail closed.
                raise
    finally:
        if service:
            await service.close()
        if transport:
            transport.close()
        store.close()


def main():
    try:
        asyncio.run(serve())
    except (BrokenPipeError, KeyboardInterrupt):
        return 0
    except Exception as error:
        # Do not print raw config values, provider errors, or credentials.
        print(f"Backend startup/runtime failure: {type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
