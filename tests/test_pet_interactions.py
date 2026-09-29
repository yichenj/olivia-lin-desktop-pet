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


if __name__ == "__main__":
    unittest.main()
