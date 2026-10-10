#!/usr/bin/env python3
"""Offline lifecycle regression; --live tests actual Ark + JSONL + shell in a temporary directory."""
import argparse
import json
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_backend import BackendProbe
from backend.config import load_config


def live(followup=True):
    config = load_config()
    if not all((config.api_key, config.base_url, config.model)):
        raise RuntimeError('Local model configuration is incomplete')
    with tempfile.TemporaryDirectory(prefix='olivia-agent-live-') as directory:
        root = Path(directory)
        # Temporary local config, never printed and removed together with the test history.
        local_config = root / 'config.json'
        local_config.touch(mode=0o600)
        local_config.write_text(json.dumps({'api_key': config.api_key, 'base_url': config.base_url,
            'model': config.model, 'shell_enabled': True, 'workspace': str(root)}))
        database = root / 'history.sqlite3'
        probe = BackendProbe(database, live=True, extra_env={'OLIVIA_CONFIG': str(local_config), 'OLIVIA_WORKSPACE': str(root)})
        db = sqlite3.connect(database)
        try:
            probe.response(probe.send('chat/send', {'text':
                '请交给后台执行者做一个本地测试：在工作目录用 shell 执行 sleep 8，然后创建 result.txt，'
                '内容严格为 olivia-agent-smoke（不带换行）。只准操作这个文件，不访问其他文件或网络。'
                '接下后先简单回复我，完成后再告诉我实际结果。'}))
            probe.completed(timeout=180)
            rows = db.execute('SELECT id,status FROM agents WHERE parent_agent_id=1').fetchall()
            assert len(rows) == 1, 'Expected one accepted background agent'
            was_running = rows[0][1] in ('running', 'queued')
            if followup:
                probe.response(probe.send('chat/send', {'text': '今天有点累，陪我随便聊一句吧。刚才的工作继续。'}))
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                try:
                    event = probe.queue.get(timeout=.2)
                    assert not event.get('eof'), 'Backend unexpectedly exited'
                    probe.events.append(event)
                    if event.get('method') == 'chat/completed':
                        assert event['params']['status'] == 'completed', 'Main turn failed'
                except queue.Empty:
                    pass
                reported = db.execute("SELECT count(*) FROM result_events WHERE status='report'").fetchone()[0]
                if reported:
                    break
            else:
                raise AssertionError('No delivered background result before deadline')
            assert (root / 'result.txt').read_text() == 'olivia-agent-smoke', 'Actual file content mismatch'
            assert db.execute('SELECT count(*) FROM agents WHERE parent_agent_id=1').fetchone()[0] == 1
            assert any(e.get('method') == 'chat/delta' for e in probe.events)
            assert all('agentId' not in e.get('params', {}) and 'turn' not in e.get('params', {}) for e in probe.events)
            commands = db.execute("SELECT count(*) FROM messages c JOIN message_payloads p ON p.message_id=c.id JOIN messages r ON r.tool_call_message_id=c.id WHERE json_extract(p.payload, '$.tool_calls[0].function.name')='run_shell'").fetchone()[0]
            assert commands >= 1, 'Expected an actual shell execution'
            if followup:
                print('PASS: live model delegation, real shell/file, continued text-only chat, persisted result report, SSE/JSONL output')
                print('Background still active when follow-up sent:', was_running)
            else:
                users = db.execute("SELECT count(*) FROM messages WHERE agent_id=1 AND role='user'").fetchone()[0]
                assert users == 1, 'No follow-up user input should be needed for result delivery'
                print('PASS: live delegation, real shell/file, result reported without any follow-up user input, SSE/JSONL output')
        except Exception:
            print('Diagnostic:', json.dumps({
                'agents': db.execute('SELECT id,status,turn FROM agents').fetchall(),
                'errors': db.execute('SELECT error_code FROM messages WHERE error_code IS NOT NULL').fetchall(),
                'tool_messages': db.execute('SELECT agent_id,turn,tool_call_message_id IS NOT NULL FROM messages WHERE operation_key IS NOT NULL OR tool_call_message_id IS NOT NULL').fetchall(),
                'events': db.execute('SELECT status FROM result_events').fetchall(),
            }), flush=True)
            raise
        finally:
            db.close()
            probe.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--no-followup', action='store_true', help='With --live, wait for the result without sending another user message')
    args = parser.parse_args()
    if args.no_followup and not args.live:
        parser.error('--no-followup requires --live')
    if args.live:
        try:
            live(followup=not args.no_followup)
        except Exception as error:
            # No provider payload, credentials, command output or real chat history in diagnostics.
            print('FAIL:', type(error).__name__, str(error) if isinstance(error, AssertionError) else '', file=sys.stderr)
            raise SystemExit(1)
    else:
        raise SystemExit(subprocess.call([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_agent*.py', '-v'], cwd=ROOT))
