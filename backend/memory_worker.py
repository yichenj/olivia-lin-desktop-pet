"""Independent asynchronous memory compilation; never speaks to the UI."""
import asyncio
from contextlib import aclosing
import json
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .agent_tools import tool, TEXT, validate_call
from .errors import ModelError
from .diagnostics import event, error_code, log_context
from .history_window import history_window, unprocessed_dialogue
from .memory import RAW_LINK, LINK, REQUIRED, SPECIAL, linked_path, memory_path, fingerprint, token_size

COMPILER_TOOLS = [
    tool('read_memory', '读取候选 Markdown 页面及其 backlinks。修改旧页前必须读取，并检查受影响的入链页面。', {'path': TEXT}),
    tool('read_raw', '核对主对话来源，停用来源不可读取。', {'message_ids': {'type': 'array', 'items': TEXT, 'minItems': 1, 'maxItems': 20}}),
    tool('write_memory', '写入完整 Markdown 页面；内容页必须引用 raw:message/<id>，索引必须链接内容页。hot.md 保持紧凑并链接原页面。', {'path': TEXT, 'content': {'type': 'string', 'maxLength': 16000}}),
    tool('remove_memory', '删除已合并的旧记忆页面，并修复所有引用；不删除 raw 或特殊入口文件。', {'path': TEXT}),
    tool('complete_memory', '核对页面、索引、关联和 hot 后提交加工回执；两个 ID 列表必须恰好覆盖本批 raw，保留的消息必须有内容页引用。', {
        'retained_message_ids': {'type': 'array', 'items': TEXT, 'maxItems': 1000},
        'ignored_message_ids': {'type': 'array', 'items': TEXT, 'maxItems': 1000},
        'summary': {'type': 'string', 'minLength': 1, 'maxLength': 2000},
    }),
]
COMPILER_PROMPT = (Path(__file__).resolve().parents[1] / 'prompts' / 'memory_compiler.md').read_text(encoding='utf-8')
PAGE_SECTIONS = ('当前信息', '变化与待确认', '关联', '来源')


def backlinks(files, target):
    return sorted(path for path, content in files.items() if path != target and
                  any(linked_path(path, link) == target for link in LINK.findall(content)))


def validate_compilation(memory, base, files, rows, read_pages, receipt, sources):
    """Structural completion gates; semantic quality is evaluated with real models."""
    retained, ignored = receipt['retained_message_ids'], receipt['ignored_message_ids']
    if (len(set(retained + ignored)) != len(retained + ignored) or
            set(retained + ignored) != {m['id'] for m in rows}):
        raise ValueError('incomplete_batch_coverage')
    memory.validate(files, sources, base.blocked_ids)
    referenced = set()
    for path, content in files.items():
        if path in SPECIAL or path.endswith('/index.md'):
            continue
        referenced.update(RAW_LINK.findall(content))
        if base.files.get(path) == content:
            continue
        if not re.match(r'^# [^\n]+\n', content) or any(
                not re.search(r'^## ' + section + r'\s*$', content, re.M) for section in PAGE_SECTIONS):
            raise ValueError('memory_page_sections_required: ' + path)
    if set(retained) - referenced:
        raise ValueError('retained_messages_missing_content_sources')
    changed = {p for p in set(base.files) | set(files) if base.files.get(p) != files.get(p)}
    for path in changed - SPECIAL:
        unread = set(backlinks(base.files, path)) - read_pages
        if unread:
            raise ValueError('read_affected_backlinks: ' + ', '.join(sorted(unread)))
    for line in files['hot.md'].splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        targets = [linked_path('hot.md', link) for link in LINK.findall(line)]
        if not any(p in files and p not in SPECIAL and not p.endswith('/index.md') for p in targets):
            raise ValueError('hot_entry_needs_content_page_link')


def compilation_log(previous, rows, receipt, changed, now):
    # One entry per line lets forget remove the whole entry with its sources.
    # Historical descriptions are plain text: renamed pages must not leave
    # navigation links that make a later publication impossible to validate.
    def plain(text):
        return (' '.join(text.splitlines()).replace('&', '&amp;').replace('<', '&lt;')
                .replace('[', '&#91;').replace(']', '&#93;').replace('raw:message/', 'raw&#58;message/'))
    summary = plain(receipt['summary'])
    sources = ' '.join(f"[来源](raw:message/{m['id']})" for m in rows)
    entry = f'- {now} | {summary} | files: {plain(", ".join(sorted(changed)))} | {sources}'
    entries = [line for line in previous.splitlines() if line.startswith('- ')] + [entry]
    while len(('# 加工记录\n' + '\n'.join(entries)).encode()) > 16000 and len(entries) > 1:
        entries.pop(0)
    return '# 加工记录\n' + '\n'.join(entries)


class MemoryCompiler:
    def __init__(self, store, memory, model, *, interval=60, threshold=16000,
                 batch_bytes=16000, input_budget=64000, timeout=300, retries=2, recent_messages=50):
        self.store, self.memory, self.model = store, memory, model
        self.interval, self.threshold, self.batch_bytes = interval, threshold, batch_bytes
        self.input_budget, self.timeout, self.retries = input_budget, timeout, retries
        self.recent_messages = recent_messages
        self.task = None
        self.timer = None
        self.closed = False
        self.last_error = None
        self.retry_at = 0
        self.lock = asyncio.Lock()

    def pending(self):
        snapshot = self.memory.snapshot()
        payloads = self.store.payloads()
        window = history_window(self.store.all_messages(), payloads, self.recent_messages, snapshot.blocked_ids)
        return unprocessed_dialogue(window.older, payloads, snapshot)

    def schedule(self):
        if self.closed or (self.task and not self.task.done()):
            return
        pending = self.pending()
        if not pending:
            return
        delay = 0 if token_size(pending) >= self.threshold else self.interval
        delay = max(delay, self.retry_at - time.monotonic())
        if self.timer:
            if self.timer.when() <= asyncio.get_running_loop().time() + delay:
                return
            self.timer.cancel()
        self.timer = asyncio.get_running_loop().call_later(delay, self.request)
        event('memory_scheduled', pending_messages=len(pending), delay_seconds=round(delay, 3))

    def request(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None
        if self.closed:
            raise ModelError('memory_closed', '记忆维护已停止。')
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self._run())
            self.task.add_done_callback(self._done)
        return self.task

    def _done(self, task):
        if not task.cancelled():
            error = task.exception()
            if error:
                self.last_error = self.last_error or getattr(error, 'code', type(error).__name__)
                self.retry_at = time.monotonic() + max(self.interval, 60)
        if not self.closed:
            self.schedule()

    async def wait(self):
        # Cancelling a foreground waiter must not cancel the shared compiler.
        try:
            return await asyncio.shield(self.request())
        except (ValueError, OSError):
            raise ModelError('memory_compile_failed', '记忆整理暂未完成，原始对话已保存。') from None

    async def _publish(self, base, files, processed, policies, sources):
        task = asyncio.create_task(asyncio.to_thread(self.memory.publish, base, files, processed, policies, sources))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Complete a publication already in progress before releasing the writer lock.
            await task
            raise

    async def _run(self):
        with log_context(compilation_id=uuid.uuid4().hex):
            try:
                return await self._compile_batch()
            except asyncio.CancelledError:
                event('memory_cancelled')
                raise
            except Exception as error:
                event('memory_failed', reason=error_code(error))
                raise

    async def _compile_batch(self):
        rows, size = [], 0
        for row in self.pending():
            cost = token_size(row)
            if rows and size + cost > self.batch_bytes:
                break
            rows.append(row)
            size += cost
        if not rows:
            return False
        event('memory_batch_started', messages=len(rows), input_bytes=size,
              model_timeout_seconds=self.timeout)
        for attempt in range(self.retries):
            base = self.memory.snapshot()
            eligible = {m['id'] for m in self.pending()}
            rows = [m for m in rows if m['id'] in eligible]
            if not rows:
                return False
            started = time.monotonic()
            event('memory_attempt_started', attempt=attempt + 1, messages=len(rows))
            try:
                with log_context(attempt=attempt + 1):
                    files, sources = await self.compile(base, rows)
                async with self.lock:
                    if base.version != self.memory.snapshot().version:
                        event('memory_candidate_stale', reason='version_changed')
                        continue
                    if any(not self.store.message(m['id']) or fingerprint(self.store.message(m['id'])) != fingerprint(m) for m in rows):
                        event('memory_candidate_stale', reason='source_changed')
                        continue
                    eligible = {m['id'] for m in self.pending()}
                    if any(m['id'] not in eligible for m in rows):
                        event('memory_candidate_stale', reason='retention_boundary_changed')
                        continue
                    processed = dict(base.processed)
                    processed.update({m['id']: fingerprint(m) for m in rows})
                    await self._publish(base, files, processed, base.policies, sources)
                event('memory_published', attempt=attempt + 1, messages=len(rows),
                      page_count=len(files), elapsed_seconds=round(time.monotonic() - started, 3),
                      version=self.memory.snapshot().version)
                self.last_error, self.retry_at = None, 0
                return True
            except asyncio.CancelledError:
                raise
            except (ValueError, OSError, ModelError, asyncio.TimeoutError) as error:
                reason = error_code(error)
                self.last_error = reason
                event('memory_attempt_failed', attempt=attempt + 1, reason=reason,
                      elapsed_seconds=round(time.monotonic() - started, 3))
                print(f'Memory compilation attempt {attempt + 1} failed: {reason}', file=sys.stderr, flush=True)
                if attempt + 1 == self.retries:
                    raise ModelError('memory_compile_failed', '记忆整理暂未完成，原始对话已保存。') from None
        raise ModelError('memory_conflict', '记忆约定正在更新，请稍后继续。')

    async def _model_calls(self, messages):
        calls, length = [], 0
        async with aclosing(self.model.stream(messages, tools=COMPILER_TOOLS,
                                              thinking='enabled', timeout=self.timeout)) as stream:
            async for piece in stream:
                length += len(piece.text)
                if length > 20000:
                    raise ValueError('memory_compiler_output_limit')
                calls.extend(piece.tool_calls or [])
        return calls

    async def compile(self, base, rows):
        files = dict(base.files)
        sources = set(base.processed) | {m['id'] for m in rows}
        sources.update(mid for content in files.values() for mid in RAW_LINK.findall(content))
        sources.update(mid for p in base.policies for mid in p['message_ids'])
        sources.update(p['source_message_id'] for p in base.policies)
        read_pages = set(REQUIRED)
        read_sources = {m['id'] for m in rows}
        read_sources.update(mid for p in REQUIRED for mid in RAW_LINK.findall(files[p]))
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        messages = [{'role': 'system', 'content': COMPILER_PROMPT},
                    {'role': 'user', 'content': json.dumps({'raw': [
                        {key: row[key] for key in ('id', 'seq', 'agent_id', 'turn', 'role', 'status', 'content', 'created_at')}
                        for row in rows], 'index': files['index.md'],
                       'hot': files['hot.md'], 'policies': files['policies.md'], 'now': now}, ensure_ascii=False)}]
        for iteration in range(1, 17):
            if token_size(messages) + token_size(COMPILER_TOOLS) > self.input_budget:
                raise ValueError('memory_compiler_context_limit')
            # A read in this response is not evidence seen by the model that
            # produced its other calls. Adopt those results on the next request.
            visible_pages, visible_sources = set(read_pages), set(read_sources)
            started = time.monotonic()
            with log_context(iteration=iteration):
                event('memory_model_call_started')
                try:
                    calls = await asyncio.wait_for(self._model_calls(messages), self.timeout)
                except asyncio.TimeoutError:
                    event('memory_model_call_timeout', timeout_seconds=self.timeout)
                    raise ModelError('memory_model_call_timeout', '记忆加工的本次模型调用超时。') from None
                finally:
                    event('memory_model_call_finished', elapsed_seconds=round(time.monotonic() - started, 3))
            if not calls:
                messages.append({'role': 'system', 'content':
                    '加工尚未提交。请完成文件维护并调用 complete_memory；普通结束文本不会推进加工进度。'})
                continue
            for call in calls:
                name = 'invalid'
                try:
                    name, args = validate_call(call, COMPILER_TOOLS)
                    event('memory_tool_started', iteration=iteration, tool=name,
                          content_bytes=len(args.get('content', '').encode()))
                    if name == 'complete_memory':
                        if call is not calls[-1]:
                            raise ValueError('complete_memory_must_be_last')
                        validate_compilation(self.memory, base, files, rows, visible_pages, args, sources)
                        changed = {p for p in set(base.files) | set(files) if base.files.get(p) != files.get(p)}
                        files['log.md'] = compilation_log(files.get('log.md', ''), rows, args, changed, now)
                        self.memory.validate(files, sources, base.blocked_ids)
                        event('memory_tool_completed', iteration=iteration, tool=name)
                        return files, sources
                    elif name == 'read_memory':
                        path = memory_path(args['path'])
                        if path in base.blocked_paths:
                            raise ValueError('memory_disabled')
                        result = {'path': path, 'content': files[path], 'backlinks': backlinks(files, path)}
                        read_pages.add(path)
                        read_sources.update(RAW_LINK.findall(files[path]))
                    elif name == 'read_raw':
                        result = self.memory.read_raw(args['message_ids'])
                        if any(m['status'] != 'completed' for m in result):
                            raise ValueError('unstable_memory_source')
                        sources.update(m['id'] for m in result)
                        read_sources.update(m['id'] for m in result)
                    else:
                        path = memory_path(args['path'])
                        if path in ('policies.md', 'log.md') or path in base.blocked_paths:
                            raise ValueError('protected_memory_path')
                        if path in base.files and path not in visible_pages:
                            raise ValueError('read_before_edit: ' + path)
                        if name == 'write_memory':
                            if len(args['content'].encode()) > 16000:
                                raise ValueError('memory_page_too_large')
                            if set(RAW_LINK.findall(args['content'])) - visible_sources:
                                raise ValueError('read_source_before_citing')
                            files[path] = args['content']
                        else:
                            if path in SPECIAL:
                                raise ValueError('protected_memory_path')
                            files.pop(path, None)
                        result = {'saved': path}
                    event('memory_tool_completed', iteration=iteration, tool=name)
                except (ValueError, KeyError, TypeError) as error:
                    event('memory_tool_rejected', iteration=iteration, tool=name, reason=error_code(error))
                    result = {'error': str(error) if isinstance(error, ValueError) else 'invalid_memory_operation',
                              'hint': '依据错误修复候选文件后重试；未提交前不会推进加工进度。'}
                messages.extend([{'role': 'assistant', 'content': None, 'tool_calls': [call]},
                                 {'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps(result, ensure_ascii=False)}])
        raise ValueError('memory_compiler_tool_limit')

    async def set_policy(self, args):
        source = self.store.message(args['source_message_id'])
        if not source or source['agent_id'] != 1 or source['role'] != 'user':
            raise ValueError('invalid_source_message')
        if len(args['instruction'].encode()) > 4000:
            raise ValueError('memory_policy_too_large')
        async with self.lock:
            base = self.memory.snapshot()
            files = dict(base.files)
            ids = set(args['message_ids'])
            paths = {memory_path(p) for p in args['paths']}
            if paths & SPECIAL or any(p not in files for p in paths):
                raise ValueError('invalid_memory_policy_path')
            for mid in ids:
                row = self.store.message(mid)
                if not row or row['agent_id'] != 1:
                    raise ValueError('invalid_memory_source')
            if args['action'] == 'forget':
                if not ids and not paths:
                    raise ValueError('memory_policy_target_required')
                for path in paths:
                    ids.update(RAW_LINK.findall(files[path]))
                removed = {p for p, content in files.items() if p not in SPECIAL and
                           (p in paths or set(RAW_LINK.findall(content)) & ids)}
                paths.update(removed)
                for path in removed:
                    files.pop(path)
                # Discard hot prose rather than risk retaining an unlinked paraphrase.
                files['hot.md'] = ''
                for path, content in list(files.items()):
                    files[path] = '\n'.join(line for line in content.splitlines()
                        if not any(linked_path(path, target) in removed for target in LINK.findall(line))
                        and not set(RAW_LINK.findall(line)) & ids)
            policy = {**args, 'message_ids': sorted(ids), 'paths': sorted(paths)}
            policies = [dict(p) for p in base.policies if not (p['source_message_id'] == source['id'] and
                        p['action'] == args['action'] and p['instruction'] == args['instruction'])] + [policy]
            files['policies.md'] = '# 用户明确的记忆约定\n' + '\n'.join(
                f"- {p['action']}: {p['instruction']} [用户原话](raw:message/{p['source_message_id']})" for p in policies)
            # Rebuild the directory mechanically after removal, so remaining pages stay reachable.
            if args['action'] == 'forget':
                files['index.md'] = '# 记忆索引\n' + '\n'.join(
                    f"- [{p}]({p})" for p in sorted(files) if p not in SPECIAL)
            sources = {m['id'] for m in self.store.all_messages() if m['agent_id'] == 1}
            await self._publish(base, files, dict(base.processed), policies, sources)
        self.schedule()
        return {'saved': True, 'raw_preserved': True}

    async def close(self):
        self.closed = True
        if self.timer:
            self.timer.cancel()
            self.timer = None
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
