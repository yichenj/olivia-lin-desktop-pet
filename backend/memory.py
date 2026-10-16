"""Versioned Markdown memory. Raw messages remain in HistoryStore."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
from types import MappingProxyType
from typing import Mapping
from urllib.parse import unquote
import uuid

from .errors import ModelError

RAW_LINK = re.compile(r'raw:message/([a-f0-9]{32})')
LINK = re.compile(r'\[[^\]\n]*\]\(([^)\s]+)\)')
REQUIRED = {'hot.md', 'index.md', 'policies.md'}
SPECIAL = REQUIRED | {'log.md'}
MAX_PAGE_BYTES = 16000
MAX_FILES = 1000


def fingerprint(message):
    values = {key: message.get(key) for key in ('id', 'agent_id', 'turn', 'role', 'status', 'content')}
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def token_size(value):
    # Conservative UTF-8 byte estimate; no endpoint-specific tokenizer assumed.
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def memory_path(path):
    if not isinstance(path, str) or not path or '\\' in path or '\x00' in path:
        raise ValueError('invalid_memory_path')
    parts = PurePosixPath(path)
    if parts.is_absolute() or any(p in ('.', '..') or p.startswith('.') for p in path.split('/')):
        raise ValueError('invalid_memory_path')
    if parts.suffix != '.md' or len(path) > 200:
        raise ValueError('invalid_memory_path')
    return str(parts)


def linked_path(source, target):
    target = unquote(target.split('#', 1)[0])
    if not target or re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', target):
        return None
    if target.startswith('/') or '\\' in target:
        raise ValueError('invalid_memory_link')
    parts = list(PurePosixPath(source).parent.parts)
    for part in target.split('/'):
        if part == '..':
            if not parts:
                raise ValueError('invalid_memory_link')
            parts.pop()
        elif part not in ('', '.'):
            parts.append(part)
    return memory_path('/'.join(parts))


def atomic_text(path, text):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fsync_directory(path.parent)


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True)
class MemorySnapshot:
    version: str
    files: Mapping[str, str]
    processed: Mapping[str, str]
    policies: tuple[Mapping, ...]

    @property
    def blocked_ids(self):
        return frozenset(mid for p in self.policies if p['action'] == 'forget' for mid in p['message_ids'])

    @property
    def blocked_paths(self):
        return frozenset(path for p in self.policies if p['action'] == 'forget' for path in p['paths'])


class MemoryStore:
    def __init__(self, history, root=None):
        self.history = history
        self.root = Path(root) if root else history.path.with_name(history.path.name + '.memory')
        self.root.mkdir(parents=True, exist_ok=True)
        self.binding = hashlib.sha256((str(history.path) + ':' + history.get_agent(1)['created_at']).encode()).hexdigest()
        self._current = self._load()

    def _snapshot(self, version, files, processed, policies):
        frozen = tuple(MappingProxyType({**p, 'message_ids': tuple(p['message_ids']), 'paths': tuple(p['paths'])}) for p in policies)
        return MemorySnapshot(version, MappingProxyType(dict(files)), MappingProxyType(dict(processed)), frozen)

    def _load(self):
        pointer = self.root / 'CURRENT'
        if not pointer.exists():
            return self._snapshot('', {'hot.md': '', 'index.md': '# 记忆索引\n', 'policies.md': ''}, {}, [])
        try:
            version = pointer.read_text().strip()
            if not re.fullmatch(r'[a-f0-9]{32}', version):
                raise ValueError('invalid_memory_version')
            directory = self.root / 'versions' / version
            state = json.loads((directory / '.state.json').read_text())
            if state['database'] != self.binding:
                raise ValueError('memory_database_mismatch')
            files = {}
            for path in directory.rglob('*.md'):
                if path.is_symlink() or path.resolve().is_relative_to(directory.resolve()) is False:
                    raise ValueError('invalid_memory_path')
                files[path.relative_to(directory).as_posix()] = path.read_text(encoding='utf-8')
            if set(files) != set(state['checksums']):
                raise ValueError('memory_file_set_mismatch')
            for path, content in files.items():
                if hashlib.sha256(content.encode()).hexdigest() != state['checksums'][path]:
                    raise ValueError('memory_checksum_mismatch')
            self.validate(files, set(state['sources']), set(state['blocked_ids']))
            return self._snapshot(version, files, state['processed'], state['policies'])
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ModelError('memory_load_failed', '记忆文件不完整或与当前历史库不匹配，请核对本地记忆目录；原始记录未删除。') from error

    def snapshot(self):
        return self._current

    def read(self, snapshot, path):
        path = memory_path(path)
        current = self.snapshot()
        content = snapshot.files.get(path)
        if content is None:
            raise ValueError('memory_page_not_found')
        if (path in current.blocked_paths or RAW_LINK.findall(content) and
                set(RAW_LINK.findall(content)) & current.blocked_ids):
            raise ValueError('memory_disabled')
        # Corrections and removals published after a pinned snapshot take priority.
        if snapshot.policies != current.policies and path in REQUIRED:
            return current.files[path]
        return content

    @staticmethod
    def validate(files, sources, blocked_ids=frozenset()):
        if not REQUIRED <= set(files) or len(files) > MAX_FILES:
            raise ValueError('invalid_memory_files')
        edges = {}
        for path, content in files.items():
            memory_path(path)
            if not isinstance(content, str) or len(content.encode()) > MAX_PAGE_BYTES:
                raise ValueError('memory_page_too_large')
            if path in ('hot.md', 'index.md') and len(content.encode()) > 6000:
                raise ValueError('memory_entry_too_large')
            refs = set(RAW_LINK.findall(content))
            if refs - sources or (path != 'policies.md' and refs & blocked_ids):
                raise ValueError('invalid_memory_source')
            if path not in SPECIAL and not path.endswith('/index.md') and not refs:
                raise ValueError('memory_page_missing_source')
            edges[path] = []
            for target in LINK.findall(content):
                linked = linked_path(path, target)
                if linked:
                    if linked not in files:
                        raise ValueError('broken_memory_link')
                    edges[path].append(linked)
        reached, queue = set(), ['index.md']
        while queue:
            path = queue.pop()
            if path not in reached:
                reached.add(path)
                queue.extend(edges[path])
        if set(files) - SPECIAL - reached:
            raise ValueError('orphan_memory_page')

    def publish(self, base, files, processed, policies, sources):
        """Called by the single publisher in a worker thread; switch only after fsync."""
        if base.version != self.snapshot().version:
            raise ValueError('stale_memory_version')
        blocked = {mid for p in policies if p['action'] == 'forget' for mid in p['message_ids']}
        self.validate(files, sources, blocked)
        version = uuid.uuid4().hex
        versions = self.root / 'versions'
        versions.mkdir(exist_ok=True)
        directory = versions / version
        directory.mkdir()
        try:
            for name, content in files.items():
                path = directory / memory_path(name)
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_text(path, content)
            state = {'database': self.binding, 'processed': processed, 'sources': sorted(sources),
                     'blocked_ids': sorted(blocked), 'policies': [dict(p) for p in policies],
                     'checksums': {p: hashlib.sha256(c.encode()).hexdigest() for p, c in files.items()}}
            atomic_text(directory / '.state.json', json.dumps(state, ensure_ascii=False))
            for folder in sorted((p for p in directory.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
                fsync_directory(folder)
            fsync_directory(directory)
            fsync_directory(versions)
            atomic_text(self.root / 'CURRENT', version + '\n')
        except BaseException:
            # If the pointer already switched, this directory is committed.
            if not (self.root / 'CURRENT').exists() or (self.root / 'CURRENT').read_text().strip() != version:
                shutil.rmtree(directory, ignore_errors=True)
            else:
                # A post-rename fsync failure cannot roll back the visible pointer.
                # Keep live readers consistent with what a restart would load.
                self._current = self._snapshot(version, files, processed, policies)
            raise
        self._current = self._snapshot(version, files, processed, policies)
        return self._current

    def read_raw(self, ids, archive_source=None):
        if archive_source:
            source = self.history.message(archive_source)
            if not source or source['agent_id'] != 1 or source['role'] != 'user' or source['id'] != self.history.latest_user_id():
                raise ValueError('invalid_archive_source')
        rows = []
        payloads = self.history.payloads()
        for mid in ids:
            row = self.history.message(mid)
            if not row or row['agent_id'] != 1:
                raise ValueError('unknown_memory_source')
            if mid in self.snapshot().blocked_ids and not archive_source:
                raise ValueError('memory_disabled')
            rows.append({k: row[k] for k in ('id', 'agent_id', 'turn', 'role', 'status', 'content', 'created_at')})
            if mid in payloads:
                rows[-1]['payload'] = payloads[mid]
        if token_size(rows) > 24000:
            raise ValueError('memory_read_too_large')
        return rows
