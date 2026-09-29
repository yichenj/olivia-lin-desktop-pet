import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt5 import QtCore, QtTest, QtWidgets
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

    def test_blink_frame_is_available_and_button_triggers_short_blink(self):
        widget = self.make_pet()
        self.assertFalse(widget.blink_pose.isNull())
        rect = widget.button_rects["blink"]
        point = QtCore.QPoint(round(rect.center().x()), round(rect.center().y()))
        QtTest.QTest.mouseClick(widget, QtCore.Qt.LeftButton, pos=point)
        self.assertGreater(widget.blink_until, time.monotonic())
        self.assertEqual(widget.bubble_until, 0.0)
        self.assertFalse(widget.grab().isNull())

    def test_wave_pose_button_uses_existing_smile_art(self):
        widget = self.make_pet()
        rect = widget.button_rects["hello"]
        point = QtCore.QPoint(round(rect.center().x()), round(rect.center().y()))
        QtTest.QTest.mouseClick(widget, QtCore.Qt.LeftButton, pos=point)
        self.assertEqual(widget.mood, "wave")
        self.assertFalse(widget.wave.isNull())

    def test_b_key_triggers_blink(self):
        widget = self.make_pet()
        QtTest.QTest.keyClick(widget, QtCore.Qt.Key_B)
        self.assertGreater(widget.blink_until, time.monotonic())
        self.assertEqual(widget.bubble_until, 0.0)

    def test_automatic_blink_starts_when_idle_and_bubble_is_clear(self):
        widget = self.make_pet()
        now = time.monotonic()
        widget.bubble_until = 0.0
        widget.next_blink = now - 1
        widget.next_idle_reaction = now + 100
        with patch.object(pet.time, "monotonic", return_value=now):
            widget.tick()
        self.assertAlmostEqual(widget.blink_until, now + 0.18)

    def test_idle_response_occurs_after_55_seconds_without_input(self):
        widget = self.make_pet()
        now = time.monotonic()
        widget.last_interaction = now - 56
        widget.next_idle_reaction = now - 1
        widget.next_blink = now + 100
        with patch.object(pet.time, "monotonic", return_value=now):
            widget.tick()
        self.assertIn(widget.bubble, {
            "安静的时候，也适合听一段旋律。",
            "我在这里陪你歇一会儿。",
            "要不要记下一首今天想到的歌？",
        })
        self.assertEqual(widget.next_idle_reaction, now + 55.0)

    def test_sleeping_pet_suppresses_automatic_blink_and_idle_response(self):
        widget = self.make_pet()
        now = time.monotonic()
        initial_bubble = widget.bubble
        widget.sleeping = True
        widget.last_interaction = now - 120
        widget.next_idle_reaction = now - 1
        widget.next_blink = now - 1
        with patch.object(pet.time, "monotonic", return_value=now):
            widget.tick()
        self.assertEqual(widget.blink_until, 0.0)
        self.assertEqual(widget.bubble, initial_bubble)

    def test_reading_pose_button_loads_transparent_static_art_and_idle_returns(self):
        widget = self.make_pet()
        self.assertIn("reading", widget.poses)
        image = widget.poses["reading"].toImage()
        self.assertGreater(image.width(), image.height() // 2)
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        rect = widget.pose_button_rects["reading"]
        QtTest.QTest.mouseClick(widget, QtCore.Qt.LeftButton,
                                pos=QtCore.QPoint(round(rect.center().x()), round(rect.center().y())))
        self.assertEqual(widget.pose, "reading")
        self.assertFalse(widget.grab().isNull())
        rect = widget.button_rects["return"]
        QtTest.QTest.mouseClick(widget, QtCore.Qt.LeftButton,
                                pos=QtCore.QPoint(round(rect.center().x()), round(rect.center().y())))
        self.assertEqual(widget.pose, "idle")

    def test_each_pose_button_selects_its_static_pose(self):
        widget = self.make_pet()
        self.assertEqual(set(widget.pose_button_rects), {"standing", "reading", "piano", "daydream"})
        for name, rect in widget.pose_button_rects.items():
            QtTest.QTest.mouseClick(widget, QtCore.Qt.LeftButton,
                                    pos=QtCore.QPoint(round(rect.center().x()), round(rect.center().y())))
            self.assertEqual(widget.pose, name)
            self.assertFalse(widget.grab().isNull())

    def test_pose_shortcuts_switch_and_i_returns_to_idle(self):
        widget = self.make_pet()
        for key, name in ((QtCore.Qt.Key_S, "standing"), (QtCore.Qt.Key_R, "reading"),
                          (QtCore.Qt.Key_P, "piano"), (QtCore.Qt.Key_D, "daydream")):
            if name not in widget.poses:
                continue
            QtTest.QTest.keyClick(widget, key)
            self.assertEqual(widget.pose, name)
            QtTest.QTest.keyClick(widget, QtCore.Qt.Key_I)
            self.assertEqual(widget.pose, "idle")


if __name__ == "__main__":
    unittest.main()
