#!/usr/bin/env python3
"""Render reproducible app-only previews of the adaptive bubble (no API calls)."""
from pathlib import Path
import os
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt5 import QtGui, QtWidgets, QtTest
from pet import OliviaPet
from backend_client import BackendClient

app = QtWidgets.QApplication([])
pet = OliviaPet()
pet.timer.stop()
pet.move(460, 180)
directory = tempfile.TemporaryDirectory(prefix='olivia-bubble-preview-')
environment = os.environ.copy()
environment.update(OLIVIA_PROVIDER='mock', ARK_API_KEY='', OLIVIA_CONFIG=str(Path(directory.name) / 'no-config.json'), OLIVIA_DB_PATH=str(Path(directory.name) / 'chat.sqlite3'))
client = BackendClient(pet, environment)
pet.attach_backend(client)
deadline = time.monotonic() + 10
while not client.ready and time.monotonic() < deadline:
    app.processEvents()
    QtTest.QTest.qWait(10)
assert client.ready, 'Preview backend failed to start'
output = Path(__file__).resolve().parents[1] / 'output/previews'
output.mkdir(parents=True, exist_ok=True)
paragraph = '今天就先歇一会儿吧。窗外还在下雨，正好给自己留一点不用赶路的时间。\n\n想说什么都可以，我在听。'
for name, text in [('thinking', ''), ('short', '嗯，我在。'), ('medium', paragraph), ('long', (paragraph + '\n\n') * 10)]:
    bubble = pet.bubble
    bubble.begin()
    if text:
        bubble.append(text)
        bubble.finish({'status': 'completed', 'text': text})
    app.processEvents()
    bubble.text.verticalScrollBar().setValue(0)
    app.processEvents()
    bounds = pet.geometry().united(bubble.geometry()).adjusted(-16, -16, 16, 16)
    preview = QtGui.QPixmap(bounds.size())
    preview.fill(QtGui.QColor('#e8e5e1'))
    painter = QtGui.QPainter(preview)
    painter.drawPixmap(pet.pos() - bounds.topLeft(), pet.grab())
    painter.drawPixmap(bubble.pos() - bounds.topLeft(), bubble.grab())
    painter.end()
    preview.save(str(output / f'bubble-{name}.png'))
    print(name, f'{bubble.width()}x{bubble.height()}', 'scroll:', bubble.text.verticalScrollBar().isVisible())
pet.close()
directory.cleanup()
