#!/usr/bin/env python3
"""Opt-in, synthetic multi-batch evaluation of the memory maintenance policy."""
import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import load_config
from backend.context import HistoryContextProvider
from backend.harness import AgentLoop, ArkModel
from backend.local_tools import LocalTools
from backend.memory import LINK, RAW_LINK, SPECIAL, MemoryStore, linked_path
from backend.memory_worker import MemoryCompiler
from backend.runtime import AgentRuntime
from backend.service import ChatService
from backend.storage import HistoryStore


def section(content, name):
    marker = '## ' + name + '\n'
    return content.split(marker, 1)[1].split('\n## ', 1)[0] if marker in content else ''


class RecordingModel:
    def __init__(self, model):
        self.model, self.calls = model, []

    async def stream(self, messages, **options):
        record = {'messages': deepcopy(messages), 'thinking': options.get('thinking'), 'output': [], 'elapsed_seconds': None}
        self.calls.append(record)
        started = time.monotonic()
        try:
            async for piece in self.model.stream(messages, **options):
                record['output'].append({'text': piece.text, 'tool_calls': piece.tool_calls, 'usage': piece.usage})
                yield piece
        finally:
            record['elapsed_seconds'] = round(time.monotonic() - started, 3)


async def evaluate(output):
    config = load_config()
    if not all((config.api_key, config.base_url, config.model)):
        raise RuntimeError('missing_local_model_configuration')
    output.mkdir(parents=True, exist_ok=False)
    report = {'checks': [], 'stages': [], 'status': 'running'}
    model = RecordingModel(ArkModel(config))
    def check(name, passed):
        report['checks'].append({'name': name, 'passed': bool(passed)})
        print(('PASS: ' if passed else 'FAIL: ') + name, flush=True)
    with tempfile.TemporaryDirectory(prefix='olivia-memory-eval-') as directory:
        store = HistoryStore(Path(directory) / 'chat.sqlite3')
        memory = MemoryStore(store)
        compiler = MemoryCompiler(store, memory, model, interval=3600)
        context = HistoryContextProvider(store, '你是 Olivia。按记忆与原文回答，分清已确认事实和猜测。', memory, recent_messages=1)
        loop = AgentLoop(context, model, compiler=compiler)
        AgentRuntime(store, context, loop, LocalTools(Path(directory), False), 'synthetic-evaluation')
        events = []
        service = ChatService(store, context, loop, 'synthetic-evaluation', events.append)
        def say(text, answer='知道了。'):
            turn, draft = store.begin(text, 'synthetic-evaluation')
            store.finish(1, turn, draft, answer, 'completed')
            return store.latest_user_id()
        async def compile_stage(name):
            print('Compiling: ' + name, flush=True)
            started, before = time.monotonic(), len(model.calls)
            while compiler.pending():
                await compiler.wait()
            snapshot = memory.snapshot()
            stage_dir = output / name
            for path, text in snapshot.files.items():
                target = stage_dir / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding='utf-8')
            report['stages'].append({'name': name, 'seconds': round(time.monotonic() - started, 3),
                                     'requests': len(model.calls) - before, 'files': sorted(snapshot.files)})
            return dict(snapshot.files)
        def pages_with(files, source, prefix):
            return [p for p, body in files.items() if p.startswith(prefix) and source in RAW_LINK.findall(body)]
        try:
            preference = say('以后讨论技术方案，请先给结论，再给风险，细节等我追问。这是我长期的沟通偏好，请记住。')
            project = say('青禾项目是我最近最重要的工作，目标是上线桌面助手。负责人是同事林岚，她也叫小岚。')
            meeting = say('青禾项目的第一次评审定在2026年11月12日，林岚主持。希望你记住这次安排。')
            first = await compile_stage('01-ingest')
            people = [p for p in pages_with(first, project, 'people/') if p != 'people/user.md']
            topics = pages_with(first, project, 'topics/')
            event_pages = pages_with(first, meeting, 'events/')
            check('person_topic_event_pages', bool(people and topics and event_pages))
            check('preference_in_user_page', preference in first.get('people/user.md', ''))
            check('hot_links_to_preference', 'people/user.md' in first['hot.md'])
            check('cross_category_connections', any(linked_path(p, link) in people for p in topics
                  for link in LINK.findall(first[p])) and any(linked_path(p, link) in topics for p in people
                  for link in LINK.findall(first[p])))

            reschedule = say('小岚把青禾评审改到2026年11月19日了，之前的11月12日不再有效；主持人仍是林岚。')
            riding = say('林岚喜欢骑行。', '我猜她可能也喜欢攀岩，但这只是我的猜测。')
            say('哈哈，收到。')
            second = await compile_stage('02-update')
            current_people = [p for p in second if p.startswith('people/') and p != 'people/user.md']
            check('alias_reuses_person_page', bool(people) and set(current_people) == set(people))
            check('meeting_keeps_identity', bool(event_pages) and all(p in second for p in event_pages))
            check('reschedule_updates_current', any(reschedule in second.get(p, '') and
                  ('11月19' in section(second[p], '当前信息') or '11-19' in section(second[p], '当前信息'))
                  for p in event_pages))
            check('old_schedule_preserved_as_history', any('12' in section(second.get(p, ''), '变化与待确认') for p in event_pages))
            check('assistant_guess_not_promoted', '攀岩' not in second['hot.md'] and all(
                  '攀岩' not in section(body, '当前信息') or '推断' in section(body, '当前信息') or '猜测' in section(body, '当前信息')
                  for p, body in second.items() if p not in SPECIAL))

            correction = say('纠正一下长期沟通偏好：技术方案请先解释背景和推导，再给结论，不用再遵循之前的先结论后风险。')
            completed = say('青禾项目已经完成，11月19日的评审也结束了，后面不需要再提醒这个安排。')
            third = await compile_stage('03-correct-and-retire')
            check('correction_reuses_user_page', correction in third.get('people/user.md', '') and
                  preference in third.get('people/user.md', ''))
            check('hot_uses_new_preference', '背景' in third['hot.md'] and '推导' in third['hot.md'] and
                  '先给结论' not in third['hot.md'])
            check('old_preference_is_history', preference in section(third.get('people/user.md', ''), '变化与待确认'))
            check('completion_updates_project', any(completed in third.get(p, '') and
                  any(word in section(third[p], '当前信息') for word in ('完成', '结束')) for p in topics))
            check('retired_meeting_not_active_in_hot', all(
                  '评审' not in line or any(word in line for word in ('结束', '完成', '不再', '无需'))
                  for line in third['hot.md'].splitlines()))
            check('no_duplicate_content_pages', {p for p in third if p not in SPECIAL} ==
                  {p for p in second if p not in SPECIAL})

            memory = MemoryStore(store)
            context.memory = compiler.memory = memory
            recall_start = len(model.calls)
            await service.handle({'method': 'chat/send', 'params': {'text':
                '请查阅记忆页面并核对原话：我现在希望你怎样讨论技术方案？小岚是谁？青禾评审最后改到了哪一天，是否还需要提醒？我是否确认过她喜欢攀岩？'}})
            active = service.active
            await asyncio.wait_for(active.task, 300)
            (output / 'recall.txt').write_text(active.text, encoding='utf-8')
            check('recall_streamed_after_reload', store.message(active.message_id)['status'] == 'completed' and
                  any(e['method'] == 'chat/delta' for e in events))
            check('recall_contains_updated_facts', all(term in active.text for term in ('背景', '推导', '林岚', '19')))
            recall_tools = {call['function']['name'] for request in model.calls[recall_start:]
                            for piece in request['output'] for call in piece['tool_calls'] or []}
            history = store.agent_history(1)
            raw_calls = {m['id'] for m in history if m['turn'] == active.number and m['payload'] and
                         any(c['function']['name'] == 'read_raw' for c in m['payload'].get('tool_calls', []))}
            raw_results = [json.loads(m['content']) for m in history if m['tool_call_message_id'] in raw_calls]
            check('recall_reads_pages_and_raw', {'read_memory', 'read_raw'} <= recall_tools and
                  any(isinstance(result, list) and result for result in raw_results))
            # These checks are intentionally narrow. The full answer and pages are retained for human review.
            report['manual_review'] = ['否定攀岩是否确认', '已结束评审是否不再提醒', '关联说明是否有证据', '事实修订是否遗漏其他有效信息']
            source = say('不要再记住林岚喜欢骑行这件事。')
            await compiler.set_policy({'source_message_id': source, 'action': 'forget', 'instruction': '不再使用林岚的骑行偏好',
                                       'message_ids': [riding], 'paths': []})
            check('forget_preserves_raw', '骑行' in store.message(riding)['content'])
            check('forget_removes_disabled_sources', all(riding not in RAW_LINK.findall(body)
                  for p, body in memory.snapshot().files.items() if p != 'policies.md'))
            report['status'] = 'passed' if all(c['passed'] for c in report['checks']) else 'failed'
        except Exception as error:
            report['status'] = 'error'
            report['error'] = getattr(error, 'code', type(error).__name__)
            raise
        finally:
            await service.close()
            (output / 'raw.json').write_text(json.dumps(store.agent_history(1), ensure_ascii=False, indent=2), encoding='utf-8')
            (output / 'requests.json').write_text(json.dumps(model.calls, ensure_ascii=False, indent=2), encoding='utf-8')
            (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            store.close()
    return report['status'] == 'passed'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Explicitly enable real model requests')
    parser.add_argument('--output', type=Path, default=ROOT / 'output' / 'reviews' / ('memory-eval-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')))
    args = parser.parse_args()
    if not args.live:
        parser.error('Use --live to run the synthetic real-model evaluation')
    try:
        passed = asyncio.run(evaluate(args.output))
    except Exception as error:
        print('FAIL: ' + getattr(error, 'code', type(error).__name__), file=sys.stderr)
        passed = False
    print('Artifacts: ' + str(args.output), flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
