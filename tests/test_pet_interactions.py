import os
import sys
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt5 import QtCore, QtGui, QtTest, QtWidgets
import pet


class PetInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def make_pet(self):
        widget = pet.OliviaPet()
        widget.timer.stop()
        widget.show()
        QtTest.QTest.qWait(20)
        return widget

    def tearDown(self):
        for widget in self.app.topLevelWidgets():
            if isinstance(widget, pet.OliviaPet):
                widget.close()
                widget.deleteLater()
        self.app.processEvents()

    def capture(self, widget):
        widget.repaint()
        self.app.processEvents()
        return widget.grab().toImage().convertToFormat(QtGui.QImage.Format_ARGB32)

    def overdue(self, widget):
        now = time.monotonic()
        widget.next_activity = now - 1
        widget.last_interaction = now - 60
        return now

    def test_blink_changes_only_eyes_without_resizing_body(self):
        widget = self.make_pet()
        widget.message_input.clearFocus()
        self.assertFalse(widget.idle.isNull())
        self.assertFalse(widget.blink_pose.isNull())
        before = self.capture(widget)
        widget.next_blink = time.monotonic() - 1
        widget.last_clock = time.monotonic()
        with patch.object(pet.time, "monotonic", return_value=widget.last_clock):
            widget.tick()
            after = self.capture(widget)
        # Native Retina grabs are physical pixels, while eye rectangles are logical.
        ratio = before.devicePixelRatio()
        eyes = [QtCore.QRectF(r.x()*ratio-2, r.y()*ratio-2,
                             r.width()*ratio+4, r.height()*ratio+4) for r in widget.eye_regions()]
        changed = 0
        for y in range(before.height()):
            for x in range(before.width()):
                if before.pixel(x, y) != after.pixel(x, y):
                    changed += 1
                    self.assertTrue(any(r.contains(x, y) for r in eyes), (x, y))
        self.assertGreater(changed, 0)
        widget.blink_until = 0
        self.assertEqual(before, self.capture(widget))

    def test_pose_switch_clears_previous_silhouette(self):
        used = self.make_pet()
        fresh = self.make_pet()
        for name in used.poses:
            used.set_pose("idle")
            self.capture(used)
            used.set_pose(name)
            fresh.set_pose(name)
            a, b = self.capture(used), self.capture(fresh)
            # Ignore the editable field's focus/caret, which belongs to only one window.
            self.assertEqual(a.copy(0, 0, a.width(), int(450*a.devicePixelRatio())),
                             b.copy(0, 0, b.width(), int(450*b.devicePixelRatio())), name)
        image = self.capture(used)
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        ratio = image.devicePixelRatio()
        self.assertEqual(image.pixelColor(int(100*ratio), int(490*ratio)).alpha(), 255)
        self.assertTrue(used.windowFlags() & QtCore.Qt.NoDropShadowWindowHint)

    def test_automatic_activity_excludes_current_pose_and_greeting(self):
        widget = self.make_pet()
        self.assertTrue(widget.automatic)
        self.assertEqual(set(widget.poses), {"standing", "reading", "piano", "daydream"})
        for old in ("idle", *widget.poses):
            widget.set_pose(old, manual=False)
            now = self.overdue(widget)
            with patch.object(pet.random, "choice", side_effect=lambda choices: choices[0]):
                widget.tick()
            self.assertNotEqual(widget.pose, old)
            self.assertIn(widget.pose, widget.POSE_NAMES)
            low, high = widget.DWELL_SECONDS[widget.pose]
            self.assertGreaterEqual(widget.next_activity, now + low)
            self.assertLessEqual(widget.next_activity, time.monotonic() + high)

    def test_right_click_menu_locks_each_pose_until_auto_resumed(self):
        widget = self.make_pet()
        menu = widget.build_context_menu()
        for name, label in widget.POSE_NAMES.items():
            next(a for a in menu.actions() if a.text() == label + " · 保持").trigger()
            self.overdue(widget)
            widget.tick()
            self.assertEqual(widget.pose, name)
            self.assertFalse(widget.automatic)
        menu.actions()[0].trigger()
        self.assertTrue(widget.automatic)
        self.assertGreater(widget.next_activity, time.monotonic())
        menu.deleteLater()

    def test_native_context_event_opens_the_activity_menu(self):
        widget = self.make_pet()
        observed = []

        def select_reading():
            menu = self.app.activePopupWidget()
            if isinstance(menu, QtWidgets.QMenu):
                observed.append(widget.menu_open)
                next(a for a in menu.actions() if a.text() == "读一会 · 保持").trigger()
                menu.close()

        QtCore.QTimer.singleShot(30, select_reading)
        # A safety timeout prevents a broken popup from hanging the test suite.
        timeout = QtCore.QTimer(widget)
        timeout.setSingleShot(True)
        timeout.timeout.connect(lambda: [m.close() for m in widget.findChildren(QtWidgets.QMenu)])
        timeout.start(1000)
        event = QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Mouse,
            QtCore.QPoint(210, 470), widget.mapToGlobal(QtCore.QPoint(210, 470)))
        self.app.sendEvent(widget, event)
        timeout.stop()
        self.assertEqual(observed, [True])
        self.assertEqual(widget.pose, "reading")
        self.assertFalse(widget.menu_open)

    def test_activity_waits_while_typing_dragging_or_menu_open(self):
        widget = self.make_pet()
        for kind in ("typing", "dragging", "menu"):
            widget.message_input.setText("未完成的输入" if kind == "typing" else "")
            widget.drag_offset = QtCore.QPoint(1, 1) if kind == "dragging" else None
            widget.menu_open = kind == "menu"
            self.overdue(widget)
            widget.tick()
            self.assertEqual(widget.pose, "idle")

    def test_rest_pauses_blink_and_activity_and_resumes_with_delay(self):
        widget = self.make_pet()
        widget.toggle_rest()
        self.overdue(widget)
        widget.next_blink = 0
        phase = widget.phase
        widget.tick()
        self.assertEqual(widget.pose, "idle")
        self.assertEqual(widget.blink_until, 0)
        self.assertEqual(widget.phase, phase)
        widget.toggle_rest()
        self.assertGreater(widget.next_activity, time.monotonic())
        self.assertGreater(widget.next_blink, time.monotonic())

    def test_blink_never_interrupts_other_poses(self):
        widget = self.make_pet()
        for name in widget.poses:
            widget.set_pose(name)
            widget.next_blink = 0
            widget.tick()
            self.assertEqual(widget.blink_until, 0)
            self.assertEqual(widget.pose, name)

    def test_input_accepts_old_shortcut_letters_and_emits_once(self):
        widget = self.make_pet()
        received = QtTest.QSignalSpy(widget.message_submitted)
        widget.message_input.setFocus()
        QtTest.QTest.keyClicks(widget.message_input, "B S R P D I space")
        self.assertEqual(widget.pose, "idle")
        self.assertEqual(widget.blink_until, 0)
        QtTest.QTest.keyClick(widget.message_input, QtCore.Qt.Key_Return)
        self.assertEqual(list(received), [["B S R P D I space"]])
        self.assertEqual(widget.last_message, "B S R P D I space")
        self.assertEqual(widget.message_input.text(), "")
        self.assertIn("尚未接入", widget.input_status.text())
        widget.submit_message()
        self.assertEqual(len(received), 1)
        self.assertEqual(widget.findChildren(QtWidgets.QPushButton), [widget.send_button])

    def test_character_click_does_not_trigger_greeting_or_change_pose(self):
        widget = self.make_pet()
        widget.set_pose("reading")
        QtTest.QTest.mouseClick(widget, QtCore.Qt.LeftButton, pos=QtCore.QPoint(220, 220))
        self.assertEqual(widget.pose, "reading")
        self.assertFalse(widget.automatic)

    def test_escape_registers_hidden_restore_shortcut_and_restore_releases_it(self):
        widget = self.make_pet()
        widget.tray = Mock()
        widget.tray.isVisible.return_value = True
        widget.restore_hotkey = Mock()
        widget.restore_hotkey.register.return_value = True
        widget.set_pose("reading")
        QtTest.QTest.keyClick(widget.message_input, QtCore.Qt.Key_Escape)
        self.assertFalse(widget.isVisible())
        widget.restore_hotkey.register.assert_called_once()
        widget.restore_window()
        self.app.processEvents()
        self.assertTrue(widget.isVisible())
        self.assertFalse(widget.isMinimized())
        self.assertTrue(widget.restore_hotkey.close.called)
        self.assertEqual(widget.pose, "reading")

    def test_hotkey_conflict_keeps_dock_recovery_available(self):
        widget = self.make_pet()
        widget.tray = Mock()
        widget.tray.isVisible.return_value = True
        widget.restore_hotkey = Mock()
        widget.restore_hotkey.register.return_value = False
        widget.hide_to_tray()
        self.assertTrue(widget.isMinimized())
        self.assertIn("注册失败", widget.input_status.text())

    def test_hide_without_tray_minimizes_instead_of_losing_window(self):
        widget = self.make_pet()
        widget.hide_to_tray()
        self.assertTrue(widget.isMinimized())
        widget.restore_window()
        self.assertFalse(widget.isMinimized())

    def test_dock_restore_releases_the_global_shortcut(self):
        widget = self.make_pet()
        widget.restore_hotkey = Mock()
        widget.restore_hotkey.register.return_value = True
        widget.hide_to_tray()
        self.assertTrue(widget.isMinimized())
        widget.restore_hotkey.close.reset_mock()
        widget.showNormal()
        self.app.processEvents()
        self.assertTrue(widget.restore_hotkey.close.called)

    def test_mac_uses_normal_floating_shadowless_window(self):
        with patch.object(pet.sys, "platform", "darwin"):
            widget = self.make_pet()
        self.assertEqual(widget.windowType(), QtCore.Qt.Window)
        self.assertTrue(widget.windowFlags() & QtCore.Qt.WindowStaysOnTopHint)
        self.assertTrue(widget.testAttribute(QtCore.Qt.WA_TranslucentBackground))

    def test_notes_platform_paths_and_save_reload(self):
        with patch.object(pet.sys, "platform", "darwin"):
            self.assertEqual(pet.notes_directory(), Path.home() / "Library/Application Support/Olivia Lin Fan Pet")
        with patch.object(pet.sys, "platform", "linux"):
            self.assertEqual(pet.notes_directory(), Path.home() / ".local/share/olivia-desktop-pet")
        with tempfile.TemporaryDirectory() as directory, patch.object(pet, "notes_directory", return_value=Path(directory)):
            dialog = pet.NotesDialog()
            dialog.text.setPlainText("Mac 本地记事测试 ♪")
            with patch.object(QtWidgets.QMessageBox, "information"):
                dialog.save_note()
            restored = pet.NotesDialog()
            self.assertEqual(restored.text.toPlainText(), "Mac 本地记事测试 ♪")
            dialog.close()
            restored.close()


if __name__ == "__main__":
    unittest.main()
