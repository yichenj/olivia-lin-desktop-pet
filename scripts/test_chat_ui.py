#!/usr/bin/env python3
"""Visible Qt + backend smoke test; --live exercises Ark and mid-stream steer."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PyQt5 import QtCore, QtGui, QtTest, QtWidgets
from backend_client import BackendClient
from pet import OliviaPet


def run(live=False, output=None):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    def wait(predicate, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            QtTest.QTest.qWait(15)
        raise AssertionError("UI/backend deadline exceeded")

    with tempfile.TemporaryDirectory(prefix="olivia-ui-test-") as directory:
        env = os.environ.copy()
        env.update(OLIVIA_DB_PATH=str(Path(directory) / "chat.sqlite3"), OLIVIA_PROVIDER="ark" if live else "mock")
        if not live:
            env.update(ARK_API_KEY="", OLIVIA_CONFIG=str(ROOT / ".nonexistent-test-config"))
        pet = OliviaPet()
        pet.move(420, 130)
        client = BackendClient(pet, env)
        pet.attach_backend(client, 30)
        completions = []
        timing = {}
        request_started = time.monotonic()
        def started(event):
            timing[event["generation"]] = {"started": time.monotonic(), "deltas": 0}
        def delta(event):
            record = timing[event["generation"]]
            record.setdefault("first_text", time.monotonic())
            record["deltas"] += 1
        def completed(event):
            timing[event["generation"]]["completed"] = time.monotonic()
        client.chat_started.connect(started)
        client.chat_delta.connect(delta)
        client.chat_completed.connect(completed)
        client.chat_completed.connect(completions.append)
        try:
            wait(lambda: client.ready)
            pet.message_input.setText("今天工作有点累，陪我聊聊音乐和雨天吧。请多说一点。")
            request_started = time.monotonic()
            pet.submit_message()
            wait(lambda: bool(pet.bubble.content) or bool(completions), 180)
            if completions and completions[-1]["status"] != "completed":
                raise AssertionError(completions[-1].get("error", {}).get("message", "Model failed"))
            if client.active:
                pet.message_input.setText("补充：请不要推荐曲目，用三小段日常聊天回应我。")
                pet.submit_message()
                wait(lambda: client.active and client.active["generation"] == 2 or bool(completions), 30)
            wait(lambda: bool(completions), 180)
            result = completions[-1]
            assert result["status"] == "completed", result.get("error", {}).get("message")
            assert result["generation"] == 2, "Model finished before steer could be exercised"
            assert pet.bubble.content == result["text"]
            assert pet.bubble.isVisible()
            if output:
                output = Path(output)
                output.parent.mkdir(parents=True, exist_ok=True)
                bounds = pet.geometry().united(pet.bubble.geometry()).adjusted(-15, -15, 15, 15)
                preview = QtGui.QPixmap(bounds.size())
                preview.fill(QtGui.QColor("#e8e5e1"))
                painter = QtGui.QPainter(preview)
                painter.drawPixmap(pet.pos() - bounds.topLeft(), pet.grab())
                painter.drawPixmap(pet.bubble.pos() - bounds.topLeft(), pet.bubble.grab())
                painter.end()
                assert preview.save(str(output))
            print("PASS: UI input → QProcess → streamed model → steer → bubble → completed")
            print("Provider:", "live Ark" if live else "mock", "| final characters:", len(result["text"]))
            for generation, record in timing.items():
                if "first_text" in record:
                    base = request_started if generation == 1 else record["started"]
                    print(f"Generation {generation}: first visible text {record['first_text'] - base:.3f}s; delta notifications {record['deltas']}")
            final_timing = timing[result["generation"]]
            assert final_timing["deltas"] > 1
            assert final_timing["first_text"] < final_timing["completed"]
        finally:
            pet.close()
            pet.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", default=str(ROOT / "output/previews/chat.png"))
    args = parser.parse_args()
    run(args.live, args.output)
