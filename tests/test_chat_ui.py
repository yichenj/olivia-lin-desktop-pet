"""Real QProcess + backend + SQLite + Qt bubble, with a deterministic model."""
import os
from pathlib import Path
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5 import QtCore, QtTest, QtWidgets
from backend_client import BackendClient, ROOT
from pet import OliviaPet


class ChatUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        env = os.environ.copy()
        env.update(OLIVIA_DB_PATH=str(Path(self.directory.name) / "test.sqlite3"), OLIVIA_PROVIDER="mock",
                   ARK_API_KEY="", OLIVIA_CONFIG=str(ROOT / ".nonexistent-test-config"))
        self.pet = OliviaPet()
        self.pet.timer.stop()
        self.client = BackendClient(self.pet, env)
        self.pet.attach_backend(self.client, auto_hide_seconds=1)
        self.until(lambda: self.client.ready)

    def tearDown(self):
        self.pet.close()
        self.pet.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def until(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            QtTest.QTest.qWait(10)
        self.fail("Timed out waiting for Qt/backend state")

    def send(self, text):
        self.pet.message_input.setText(text)
        self.pet.submit_message()

    def test_stream_steer_and_stale_fragments(self):
        self.send("先聊音乐")
        self.until(lambda: bool(self.pet.bubble.content))
        old = dict(self.client.active)
        self.send("改成聊电影")
        self.until(lambda: self.client.active and self.client.active["generation"] == 2)
        self.client._dispatch({"jsonrpc": "2.0", "method": "chat/delta", "params": {**old, "text": "STALE"}})
        self.until(lambda: self.client.active is None and not self.client.chat_pending)
        self.assertNotIn("STALE", self.pet.bubble.content)
        self.assertIn("先聊音乐 / 改成聊电影", self.pet.bubble.content)
        self.assertEqual(self.pet.bubble.text.toPlainText(), self.pet.bubble.content)
        self.assertFalse(self.pet.bubble.streaming)
        self.assertEqual(self.pet.size(), QtCore.QSize(450, 620))

    def test_long_reply_scroll_hide_restore_and_edges(self):
        self.send("[test:long]")
        self.until(lambda: self.client.active is None and bool(self.pet.bubble.content) and not self.client.chat_pending)
        bubble = self.pet.bubble
        self.assertGreater(bubble.text.verticalScrollBar().maximum(), 0)
        area = self.pet.screen().availableGeometry()
        for x in (area.left(), area.right() - self.pet.width()):
            self.pet.move(x, area.top())
            bubble.reposition()
            self.assertTrue(area.contains(bubble.geometry()))
        bubble.text.verticalScrollBar().setValue(0)
        bubble.remaining = .01
        bubble.last_tick -= 2
        bubble.tick()
        self.assertTrue(bubble.isVisible(), "Reading must pause auto-hide")
        bubble.text.verticalScrollBar().setValue(bubble.text.verticalScrollBar().maximum())
        bubble.text.clearFocus()
        # Simulate the mouse being elsewhere without relying on native pointer position.
        from unittest.mock import patch
        with patch.object(bubble, "underMouse", return_value=False):
            bubble.remaining = .01
            bubble.last_tick -= 2
            bubble.tick()
        self.assertFalse(bubble.isVisible())
        bubble.reveal()
        self.assertTrue(bubble.isVisible())
        self.pet.hide()
        self.assertFalse(bubble.isVisible())
        self.pet.show()
        self.until(bubble.isVisible)

    def test_disconnect_and_reconnect(self):
        self.send("[test:slow]")
        self.until(lambda: self.pet.bubble.streaming)
        self.client.process.kill()
        self.until(lambda: not self.client.ready and not self.pet.bubble.streaming)
        self.assertIn("退出", self.pet.input_status.text())
        self.client.start()
        self.until(lambda: self.client.ready)
        self.send("恢复了")
        self.until(lambda: self.client.active is None and not self.client.chat_pending)
        self.assertIn("恢复了", self.pet.bubble.content)

    def test_failure_cancel_and_rapid_supplements(self):
        self.send("[test:error]")
        self.until(lambda: self.client.active is None and not self.client.chat_pending)
        self.assertIn("模拟连接中断", self.pet.input_status.text())
        self.send("[test:slow]")
        self.until(lambda: self.client.active is not None)
        self.client.cancel()
        self.until(lambda: self.client.active is None)
        self.assertFalse(self.pet.bubble.streaming)
        self.send("一")
        self.send("二")
        self.send("三")
        self.until(lambda: not self.client.active and not self.client.chat_pending and not self.client.queue)
        self.assertIn("一 / 二 / 三", self.pet.bubble.content)

    def test_compact_oval_grows_and_has_no_task_stop_controls(self):
        bubble = self.pet.bubble
        bubble.begin()
        waiting = bubble.size()
        self.assertLess(waiting.width(), 170)
        self.assertLess(waiting.height(), 110)
        self.assertFalse(hasattr(bubble, 'stop_button'))
        menu = self.pet.build_context_menu()
        self.assertFalse(any('停止' in action.text() for action in menu.actions()))
        menu.deleteLater()
        bubble.append('嗯，我在。')
        self.app.processEvents()
        short = bubble.size()
        self.assertLess(short.width(), 220)
        self.assertLess(short.height(), 160)
        self.assertEqual(bubble.text.verticalScrollBar().maximum(), 0)
        self.assertFalse(bubble.text.verticalScrollBar().isVisible())
        bubble.append('今天想聊什么？窗外的雨让我想起了一部老电影。' * 3)
        self.app.processEvents()
        self.assertGreater(bubble.width(), short.width())
        self.assertGreater(bubble.height(), short.height())
        bubble.append('我们慢慢聊。' * 100)
        self.app.processEvents()
        self.assertEqual(bubble.width(), bubble.MAX_WIDTH)
        self.assertEqual(bubble.height(), bubble.MAX_HEIGHT)
        self.assertTrue(bubble.text.verticalScrollBar().isVisible())
        bubble.begin()
        self.assertEqual(bubble.size(), waiting, 'New reply must reset to a compact pause')
        self.assertEqual(bubble.findChildren(QtWidgets.QAbstractButton), [])
        menu = self.pet.build_context_menu()
        self.assertEqual(menu.actions()[0].text(), '关闭对话')
        menu.actions()[0].trigger()
        menu.deleteLater()
        self.assertTrue(bubble.streaming)
        self.assertFalse(bubble.isVisible())
        bubble.append('继续说完这句话。')
        bubble.finish({'status': 'completed', 'text': bubble.content})
        self.assertFalse(bubble.isVisible(), 'Hidden reply must stay hidden on completion')
        menu = self.pet.build_context_menu()
        self.assertEqual(menu.actions()[0].text(), '展开对话')
        menu.actions()[0].trigger()
        menu.deleteLater()
        self.assertTrue(bubble.isVisible())
        QtTest.QTest.mouseClick(self.pet.reply_button, QtCore.Qt.LeftButton)
        self.assertFalse(bubble.isVisible())
        QtTest.QTest.mouseClick(self.pet.reply_button, QtCore.Qt.LeftButton)
        self.assertTrue(bubble.isVisible())

    def test_text_is_visible_and_renderable_before_completion(self):
        self.send('[test:long]')
        self.until(lambda: len(self.pet.bubble.content) >= 20)
        bubble = self.pet.bubble
        self.assertTrue(bubble.streaming)
        self.assertIsNotNone(self.client.active)
        self.assertEqual(bubble.text.toPlainText(), bubble.content)
        self.assertTrue(bubble.text.isVisible())
        before = bubble.content
        self.assertFalse(bubble.grab().isNull())
        self.until(lambda: len(bubble.content) > len(before))
        self.assertTrue(bubble.streaming, 'Rendering must not wait for chat/completed')
