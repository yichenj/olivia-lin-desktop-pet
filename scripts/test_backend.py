#!/usr/bin/env python3
"""Exercise a real JSONL backend process. --live uses Ark; default is offline."""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


class BackendProbe:
    def __init__(self, database, live=False, extra_env=None):
        env = os.environ.copy()
        env.update(OLIVIA_DB_PATH=str(database), OLIVIA_PROVIDER="ark" if live else "mock")
        if not live:
            env.update(ARK_API_KEY="", OLIVIA_CONFIG=str(ROOT / ".nonexistent-test-config"))
        env.update(extra_env or {})
        self.process = subprocess.Popen([sys.executable, "-u", "-m", "backend"], cwd=ROOT, env=env,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True, encoding="utf-8")
        self.queue = queue.Queue()
        self.events = []
        self.request_id = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.wait(lambda event: event.get("method") == "backend/ready")

    def _read(self):
        for line in self.process.stdout:
            try:
                self.queue.put(json.loads(line))
            except ValueError:
                self.queue.put({"protocol_error": True})
        self.queue.put({"eof": True})

    def send(self, method, params):
        self.request_id += 1
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params}, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        return self.request_id

    def wait(self, predicate, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            event = self.queue.get(timeout=max(.01, deadline - time.monotonic()))
            if event.get("eof") or event.get("protocol_error"):
                raise AssertionError("Backend exited or emitted invalid JSON")
            self.events.append(event)
            if predicate(event):
                return event
        raise TimeoutError("Backend response deadline")

    def response(self, request_id):
        result = self.wait(lambda e: e.get("id") == request_id)
        if "error" in result:
            raise AssertionError(result["error"])
        return result["result"]

    def completed(self, timeout=20):
        event = self.wait(lambda e: e.get("method") == "chat/completed", timeout)
        if event["params"]["status"] != "completed":
            raise AssertionError(event["params"].get("error", {}).get("message", "Turn failed"))
        return event["params"]

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        elif not self.process.stdin.closed:
            self.process.stdin.close()
        self.reader.join(timeout=2)
        self.process.stdout.close()


def run(live=False):
    with tempfile.TemporaryDirectory(prefix="olivia-backend-test-") as directory:
        database = Path(directory) / "history.sqlite3"
        probe = BackendProbe(database, live)
        try:
            request_id = probe.send("chat/send", {"text": "请记住我的测试暗号是蓝色雨伞。用一句话确认。"})
            assert probe.response(request_id) == {}
            if not live:
                probe.wait(lambda e: e.get("method") == "chat/delta")
                assert probe.response(probe.send("chat/send", {"text": "补充：别提钢琴。"})) == {}
            reply = probe.completed(180 if live else 20)
            assert reply["text"]
            assert any(e.get("method") == "chat/delta" for e in probe.events)
            assert all("turn" not in e.get("params", {}) and "agentId" not in e.get("params", {}) for e in probe.events)
            print("PASS: backend ready, text-only sends, streamed reply" + (", continued input" if not live else " (live Ark)"))
        finally:
            probe.close()
        probe = BackendProbe(database, live)
        try:
            assert probe.response(probe.send("chat/send", {"text": "刚才我说的测试暗号是什么？请只回答暗号。"})) == {}
            result = probe.completed(180 if live else 20)
            assert "蓝色雨伞" in result["text"], "Restarted context did not retain the test phrase"
            print("PASS: SQLite persistence, restart, second-turn context")
        finally:
            probe.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use configured Ark credentials (makes real API calls)")
    args = parser.parse_args()
    run(args.live)
