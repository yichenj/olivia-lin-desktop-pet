"""Persistent diagnostics must survive desktop/backend restarts without content leaks."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
from backend.config import Config, ROOT
from backend.diagnostics import LOGGER, configure_logging, close_logging, event, log_context
from backend.harness import ArkModel, ModelError
from tests.test_ark_model import FakeStream, chunk, client_with


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / 'test.sqlite3'
        self.log = configure_logging(self.database)

    def tearDown(self):
        close_logging()
        self.directory.cleanup()

    def records(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    async def test_restart_appends_and_rotation_is_bounded(self):
        event('first_start')
        close_logging()
        configure_logging(self.database)
        event('second_start')
        self.assertEqual([r['event'] for r in self.records()], ['first_start', 'second_start'])
        handler = next(h for h in LOGGER.handlers if hasattr(h, 'maxBytes'))
        handler.maxBytes = 256
        for i in range(25):
            event('rotation_test', sequence=i)
        self.assertEqual(len(list(self.log.parent.glob('backend.log*'))), 4)
        self.assertEqual(self.records()[-1]['sequence'], 24)

    async def test_model_timing_and_errors_exclude_private_data(self):
        stream = FakeStream([chunk('PRIVATE_REPLY'), httpx.ReadTimeout('PRIVATE_PROVIDER_ERROR')])
        client = client_with(stream)
        config = Config(api_key='PRIVATE_KEY', base_url='https://example.invalid/PRIVATE_URL', model='PRIVATE_MODEL')
        with log_context(compilation_id='test-compilation', iteration=2):
            with patch('openai.AsyncOpenAI', return_value=client) as constructor:
                with self.assertRaises(ModelError):
                    _ = [p async for p in ArkModel(config).stream(
                        [{'role': 'user', 'content': 'PRIVATE_CHAT'}], timeout=300)]
        self.assertEqual(constructor.call_args.kwargs['timeout'], 300)
        text = self.log.read_text()
        self.assertNotIn('PRIVATE_', text)
        records = self.records()
        self.assertIn('model_first_chunk', [r['event'] for r in records])
        end = records[-1]
        self.assertEqual(end['outcome'], 'network_timeout')
        self.assertEqual(end['compilation_id'], 'test-compilation')
        self.assertEqual(end['iteration'], 2)
        self.assertEqual(end['text_chars'], len('PRIVATE_REPLY'))
        self.assertTrue(stream.closed)

    async def test_real_backend_writes_logs_without_shell_redirection(self):
        # A separate database keeps the parent test logger out of the child's file.
        database = Path(self.directory.name) / 'child.sqlite3'
        config = Path(self.directory.name) / 'config.json'
        config.write_text(json.dumps({'memory_interval_seconds': 1,
                                      'memory_recent_messages': 1,
                                      'memory_model_timeout_seconds': 7,
                                      'memory_wait_timeout_seconds': 20}))
        env = {**os.environ, 'OLIVIA_CONFIG': str(config), 'OLIVIA_DB_PATH': str(database),
               'OLIVIA_PROVIDER': 'mock', 'ARK_API_KEY': ''}
        process = await asyncio.create_subprocess_exec(sys.executable, '-u', '-m', 'backend',
            cwd=ROOT, env=env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            ready = json.loads(await asyncio.wait_for(process.stdout.readline(), 5))
            self.assertEqual(ready['method'], 'backend/ready')
            request = {'jsonrpc': '2.0', 'id': 1, 'method': 'chat/send', 'params': {'text': 'PRIVATE_TEST_CHAT'}}
            process.stdin.write((json.dumps(request) + '\n').encode())
            await process.stdin.drain()
            async def completed():
                while True:
                    notification = json.loads(await process.stdout.readline())
                    if notification.get('method') == 'chat/completed':
                        return
            await asyncio.wait_for(completed(), 5)
            # Age the first exchange out of the raw-history protection window.
            request['id'] = 2
            process.stdin.write((json.dumps(request) + '\n').encode())
            await process.stdin.drain()
            log = Path(str(database) + '.logs') / 'backend.log'

            async def published():
                while True:
                    records = [json.loads(line) for line in log.read_text().splitlines()]
                    if any(r['event'] == 'memory_published' for r in records):
                        return records
                    await asyncio.sleep(.02)

            records = await asyncio.wait_for(published(), 5)
            self.assertEqual(records[0]['memory_model_timeout_seconds'], 7)
            self.assertTrue(any(r['event'] == 'memory_tool_completed' for r in records))
            self.assertNotIn('PRIVATE_TEST_CHAT', log.read_text())
            process.stdin.close()
            stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
            self.assertEqual(process.returncode, 0, stderr.decode())
            self.assertTrue(all(json.loads(line)['jsonrpc'] == '2.0' for line in stdout.splitlines()))
            self.assertEqual(json.loads(log.read_text().splitlines()[-1])['event'], 'backend_stopped')
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
