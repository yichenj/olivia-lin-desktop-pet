#!/usr/bin/env python3
"""Memory regressions, or an explicitly requested real-model synthetic smoke."""
import argparse
import asyncio
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def live():
    from backend.config import load_config
    from backend.context import HistoryContextProvider
    from backend.harness import ArkModel, AgentLoop
    from backend.local_tools import LocalTools
    from backend.memory import MemoryStore
    from backend.memory_worker import MemoryCompiler
    from backend.runtime import AgentRuntime
    from backend.service import ChatService
    from backend.storage import HistoryStore
    config = load_config()
    if not all((config.api_key, config.base_url, config.model)):
        raise RuntimeError('Local model configuration is incomplete')
    with tempfile.TemporaryDirectory(prefix='olivia-memory-live-') as directory:
        root = Path(directory)
        store = HistoryStore(root / 'chat.sqlite3')
        memory = MemoryStore(store)
        model = ArkModel(config)
        # Exercise compilation/recall with a fixed fixture, without waiting for it to age out.
        compiler = MemoryCompiler(store, memory, model, interval=3600, retries=2, recent_messages=0)
        context = HistoryContextProvider(store, '你是 Olivia，和用户持续交流。根据工具证据回答，不编造记忆。', memory, recent_messages=1)
        loop = AgentLoop(context, model, compiler=compiler)
        AgentRuntime(store, context, loop, LocalTools(root, False), config.model)
        events = []
        service = ChatService(store, context, loop, config.model, events.append)
        try:
            for text in ('我的周报固定包含风险摘要和下周行动两部分，请记住。',
                         '蓝鹭项目的合成测试口令是“松塔七号”，只用于这个测试，请记住。',
                         '今天聊到这里。'):
                turn, draft = store.begin(text, config.model)
                store.finish(1, turn, draft, '收到。', 'completed')
            print('Compiling synthetic dialogue into Markdown...', flush=True)
            await compiler.wait()
            assert not compiler.pending(), 'Synthetic dialogue not fully processed'
            snapshot = memory.snapshot()
            assert len(snapshot.files) > 3, 'No grounded content pages generated'
            assert 'raw:message/' in '\n'.join(snapshot.files.values()), 'No source links'
            # Reload from disk before the recall, as after a restart.
            memory = MemoryStore(store)
            context.memory = memory
            compiler.memory = memory
            assert memory.snapshot().version == snapshot.version
            print('Published and reloaded; checking model-directed recall...', flush=True)
            await service.handle({'method': 'chat/send', 'params': {'text':
                '我之前给蓝鹭项目定的测试口令是什么？请查记忆索引并核对原始消息，再回答。'}})
            current = service.active
            await asyncio.wait_for(current.task, 300)
            assert store.message(current.message_id)['status'] == 'completed', 'Recall did not complete'
            assert '松塔七号' in current.text, 'Incorrect recalled value'
            calls = [m['payload']['tool_calls'][0]['function']['name'] for m in store.agent_history(1)
                     if m['payload'] and m['payload'].get('tool_calls')]
            assert 'read_memory' in calls and 'read_raw' in calls, 'Expected page navigation and raw verification'
            assert any(e['method'] == 'chat/delta' for e in events), 'No streaming output'
            print('PASS: real-model file compilation, reload, page navigation, raw verification and streaming recall', flush=True)
        finally:
            await service.close()
            store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if args.live:
        try:
            asyncio.run(live())
        except Exception as error:
            # No credentials, provider body, or real user data in the output.
            print(f'FAIL: {type(error).__name__}: {getattr(error, "code", "memory_smoke_failed")}', file=sys.stderr)
            return 1
    else:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_memory*.py'))
        return 0 if result.wasSuccessful() else 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
