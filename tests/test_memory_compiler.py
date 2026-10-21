"""Compiler publication requires an explicit, grounded maintenance receipt."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from backend.errors import ModelError
from backend.harness import StreamPiece
from backend.memory import MemoryStore, fingerprint
from backend.memory_worker import MemoryCompiler
from backend.storage import HistoryStore


def call(name, **args):
    return {'id': name, 'type': 'function', 'function': {
        'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}


def page(title, ids, *, current='已确认事实', related='无'):
    refs = ' '.join(f'[原话](raw:message/{mid})' for mid in ids)
    return f'# {title}\n\n## 当前信息\n{current} {refs}\n\n## 变化与待确认\n无\n\n## 关联\n{related}\n\n## 来源\n{refs}\n'


def complete(rows, retained=()):
    return call('complete_memory', retained_message_ids=list(retained),
                ignored_message_ids=[m['id'] for m in rows if m['id'] not in retained],
                summary='合并新资料，忽略简单确认。')


class ScriptedModel:
    def __init__(self, *steps):
        self.steps = iter(steps)

    async def stream(self, messages, **options):
        step = next(self.steps)
        if callable(step):
            step = step(messages)
        yield StreamPiece(tool_calls=step)


class CompilerContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = HistoryStore(Path(self.directory.name) / 'chat.sqlite3')
        self.memory = MemoryStore(self.store)
        self.compiler = MemoryCompiler(self.store, self.memory, None, interval=3600, retries=1, recent_messages=0)

    async def asyncTearDown(self):
        await self.compiler.close()
        self.store.close()
        self.directory.cleanup()

    def say(self, text):
        turn, draft = self.store.begin(text, 'test')
        self.store.finish(1, turn, draft, '收到。', 'completed')
        return self.store.all_messages()[-2:]

    def seed(self, rows, pages, root, hot=''):
        files = {**self.memory.snapshot().files, **pages, 'index.md': root, 'hot.md': hot}
        self.memory.publish(self.memory.snapshot(), files,
                            {m['id']: fingerprint(m) for m in rows}, [], {m['id'] for m in rows})

    def after_error(self, error, calls):
        def step(messages):
            self.assertIn(error, json.loads(messages[-1]['content'])['error'])
            return calls
        return step

    async def run_steps(self, *steps):
        self.compiler.model = ScriptedModel(*steps)
        await self.compiler.wait()
        self.assertFalse(self.compiler.pending())
        return self.memory.snapshot()

    async def test_plain_text_stop_cannot_advance_progress(self):
        rows = self.say('请记住我的偏好。')
        class TextOnly:
            async def stream(self, *args, **kwargs):
                yield StreamPiece(text='已整理完毕。')
        self.compiler.model = TextOnly()
        with self.assertRaises(ModelError):
            await self.compiler.wait()
        self.assertEqual(self.memory.snapshot().version, '')
        self.assertEqual(self.compiler.pending(), rows)

    async def test_each_model_call_has_its_own_timeout(self):
        rows = self.say('先说结论。')
        requests = []

        class SlowCalls:
            async def stream(self, messages, **options):
                requests.append(options)
                await asyncio.sleep(.09)
                if len(requests) == 1:
                    yield StreamPiece(tool_calls=[call('read_memory', path='index.md')])
                else:
                    yield StreamPiece(tool_calls=[complete(rows)])

        self.compiler.model = SlowCalls()
        self.compiler.timeout = .15
        await self.compiler.wait()
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(o['timeout'] == .15 for o in requests))
        self.assertEqual(set(self.memory.snapshot().processed), {m['id'] for m in rows})
        self.assertEqual(self.store.all_messages(), rows)

    async def test_timeout_closes_stream_and_retries_the_same_batch(self):
        rows = self.say('先说结论。') + self.say('再解释理由。')
        batches, closed = [], []

        class SlowFirstCall:
            async def stream(self, messages, **options):
                batch = json.loads(messages[1]['content'])['raw']
                batches.append([m['id'] for m in batch])
                try:
                    if len(batches) == 1:
                        # Continuous chunks must not reset the call deadline.
                        for _ in range(100):
                            await asyncio.sleep(.005)
                            yield StreamPiece(text='加工中')
                    yield StreamPiece(tool_calls=[complete(batch)])
                finally:
                    closed.append(True)

        self.compiler.model = SlowFirstCall()
        self.compiler.timeout = .05
        self.compiler.retries = 2
        await self.compiler.wait()
        self.assertEqual(batches, [[m['id'] for m in rows]] * 2)
        self.assertEqual(len(closed), 2)
        self.assertEqual(self.compiler.batch_bytes, 16000)
        self.assertFalse(self.compiler.pending())

    async def test_timeout_preserves_all_pending_rows(self):
        rows = self.say('先说结论。')

        class Stalled:
            async def stream(self, messages, **options):
                await asyncio.sleep(1)
                yield StreamPiece()

        self.compiler.model = Stalled()
        self.compiler.timeout = .02
        with self.assertRaises(ModelError):
            await self.compiler.wait()
        self.assertEqual(self.compiler.last_error, 'memory_model_call_timeout')
        self.assertFalse(self.memory.snapshot().version)
        self.assertEqual(self.compiler.pending(), rows)
        self.assertEqual(self.compiler.batch_bytes, 16000)

    async def test_incomplete_receipt_is_repaired_before_publication(self):
        rows = self.say('哈哈，收到。')
        snapshot = await self.run_steps(
            [complete(rows[:1])],
            self.after_error('incomplete_batch_coverage', [complete(rows)]))
        self.assertEqual(set(snapshot.processed), {m['id'] for m in rows})
        self.assertTrue(all(m['id'] in snapshot.files['log.md'] for m in rows))
        self.assertEqual(self.store.all_messages(), rows)

    async def test_retained_fact_needs_a_content_page_and_hot_needs_its_link(self):
        rows = self.say('技术方案先说结论。')
        mid = rows[0]['id']
        snapshot = await self.run_steps(
            [complete(rows, [mid])],
            self.after_error('retained_messages_missing_content_sources', [
                call('write_memory', path='people/user.md', content=page('用户', [mid])),
                call('write_memory', path='index.md', content='[用户](people/user.md)'),
                call('write_memory', path='hot.md', content='先说结论'), complete(rows, [mid])]),
            self.after_error('hot_entry_needs_content_page_link', [
                call('write_memory', path='hot.md', content='先说结论。[用户](people/user.md)'), complete(rows, [mid])]))
        self.assertIn(mid, snapshot.files['people/user.md'])
        self.assertEqual(MemoryStore(self.store).snapshot().version, snapshot.version)

    async def test_existing_page_and_affected_backlinks_must_be_read(self):
        old = self.say('林岚负责青禾。')
        self.seed(old, {'people/lin.md': page('林岚', [old[0]['id']]),
                        'topics/qinghe.md': page('青禾', [old[0]['id']], related='[负责人](../people/lin.md)')},
                  '[青禾](topics/qinghe.md)')
        rows = self.say('林岚的别名是小岚。')
        edit = call('write_memory', path='people/lin.md', content=page('林岚', [old[0]['id'], rows[0]['id']]))
        snapshot = await self.run_steps(
            [edit], self.after_error('read_before_edit', [call('read_memory', path='people/lin.md')]),
            lambda messages: self.assert_backlink(messages, 'topics/qinghe.md', [edit, complete(rows, [rows[0]['id']])]),
            self.after_error('read_affected_backlinks', [call('read_memory', path='topics/qinghe.md')]),
            [complete(rows, [rows[0]['id']])])
        self.assertIn(rows[0]['id'], snapshot.files['people/lin.md'])

    async def test_same_response_read_does_not_authorize_blind_overwrite(self):
        old = self.say('旧的项目事实。')
        self.seed(old, {'topics/project.md': page('项目', [old[0]['id']])}, '[项目](topics/project.md)')
        rows = self.say('新的项目事实。')
        edit = call('write_memory', path='topics/project.md', content=page('项目', [old[0]['id'], rows[0]['id']]))
        snapshot = await self.run_steps([call('read_memory', path='topics/project.md'), edit],
            self.after_error('read_before_edit', [edit, complete(rows, [rows[0]['id']])]))
        self.assertIn(rows[0]['id'], snapshot.files['topics/project.md'])

    def assert_backlink(self, messages, path, calls):
        self.assertIn(path, json.loads(messages[-1]['content'])['backlinks'])
        return calls

    async def test_merge_repairs_inbound_links_and_preserves_raw(self):
        old = self.say('林岚参加青禾，小岚参加评审。')
        mid = old[0]['id']
        self.seed(old, {'people/lin.md': page('林岚', [mid]), 'people/xiao.md': page('小岚', [mid]),
                        'topics/qinghe.md': page('青禾', [mid], related='[小岚](../people/xiao.md)')},
                  '[林岚](people/lin.md)\n[青禾](topics/qinghe.md)')
        rows = self.say('林岚和小岚是同一人。')
        snapshot = await self.run_steps(
            [call('read_memory', path=p) for p in ('people/lin.md', 'people/xiao.md', 'topics/qinghe.md')],
            [call('write_memory', path='people/lin.md', content=page('林岚（小岚）', [mid, rows[0]['id']])),
             call('remove_memory', path='people/xiao.md'), complete(rows, [rows[0]['id']])],
            self.after_error('broken_memory_link', [
                call('write_memory', path='topics/qinghe.md', content=page('青禾', [mid], related='[林岚（小岚）](../people/lin.md)')),
                complete(rows, [rows[0]['id']])]))
        self.assertNotIn('people/xiao.md', snapshot.files)
        self.assertIn('people/lin.md', snapshot.files['topics/qinghe.md'])
        self.assertEqual(len(self.store.all_messages()), 4)

    async def test_split_keeps_old_entry_and_cross_category_navigation(self):
        old = self.say('青禾项目包括桌面助手和语音助手。')
        mid = old[0]['id']
        self.seed(old, {'topics/qinghe.md': page('青禾', [mid])}, '[青禾](topics/qinghe.md)')
        rows = self.say('请把两个方向分别整理，桌面助手由林岚负责。')
        mid2 = rows[0]['id']
        snapshot = await self.run_steps(
            [call('read_memory', path='topics/qinghe.md')],
            [call('write_memory', path='topics/qinghe.md', content=page('青禾', [mid], related='[桌面](desktop.md)\n[语音](voice.md)')),
             call('write_memory', path='topics/desktop.md', content=page('桌面', [mid, mid2], related='[负责人](../people/lin.md)')),
             call('write_memory', path='topics/voice.md', content=page('语音', [mid])),
             call('write_memory', path='people/lin.md', content=page('林岚', [mid2], related='[负责桌面](../topics/desktop.md)')),
             complete(rows, [mid2])])
        self.assertEqual(snapshot.files['index.md'], '[青禾](topics/qinghe.md)')
        self.assertIn('voice.md', snapshot.files['topics/qinghe.md'])
        self.assertIn(mid, snapshot.files['topics/voice.md'])

    async def test_old_source_must_be_read_before_reusing_it(self):
        old = self.say('需要保留的旧事实。')
        mid = old[0]['id']
        self.seed(old, {'topics/old.md': page('旧事实', [mid])}, '[旧事实](topics/old.md)')
        rows = self.say('补充一条新信息。')
        edit = call('write_memory', path='topics/new.md', content=page('新信息', [mid, rows[0]['id']]))
        await self.run_steps([edit], self.after_error('read_source_before_citing', [call('read_raw', message_ids=[mid])]),
            [edit, call('write_memory', path='index.md', content='[旧事实](topics/old.md)\n[新信息](topics/new.md)'),
             complete(rows, [rows[0]['id']])])

    async def test_log_cannot_be_model_edited_and_forget_removes_its_sources(self):
        rows = self.say('林岚喜欢骑行。')
        mid = rows[0]['id']
        await self.run_steps([call('write_memory', path='log.md', content='伪造加工结果')],
            self.after_error('protected_memory_path', [complete(rows)]))
        source = self.say('忘掉骑行偏好。')[0]['id']
        await self.compiler.set_policy({'source_message_id': source, 'action': 'forget',
            'instruction': '不再使用骑行偏好', 'message_ids': [mid], 'paths': []})
        self.assertNotIn(mid, self.memory.snapshot().files['log.md'])
        self.assertEqual(self.store.message(mid)['content'], rows[0]['content'])

    async def test_receipt_description_does_not_create_links_to_removed_pages(self):
        rows = self.say('简单确认。')
        receipt = complete(rows)
        args = json.loads(receipt['function']['arguments'])
        args['summary'] = '已检查 [旧页](people/removed.md)，无需保留新信息。'
        receipt['function']['arguments'] = json.dumps(args)
        snapshot = await self.run_steps([receipt])
        self.assertIn('&#91;旧页&#93;', snapshot.files['log.md'])
        self.assertEqual(MemoryStore(self.store).snapshot().version, snapshot.version)
