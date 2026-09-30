"""Real Carbon registration and event dispatch; run with QT_QPA_PLATFORM=cocoa."""
import ctypes as C
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt5 import QtTest, QtWidgets
from mac_hotkey import HotKeyID, MacRestoreHotkey


@unittest.skipUnless(sys.platform == "darwin" and os.environ["QT_QPA_PLATFORM"] == "cocoa",
                     "Requires a native macOS Cocoa session")
class MacHotkeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_registration_conflict_and_release(self):
        first = MacRestoreHotkey(lambda: None)
        second = MacRestoreHotkey(lambda: None)
        try:
            self.assertTrue(first.register(), first.error)
            self.assertFalse(second.register())
            self.assertEqual(second.refs, [])
            self.assertFalse(second.handler.value)
            first.close()
            self.assertTrue(second.register(), second.error)
        finally:
            first.close()
            second.close()

    def test_carbon_dispatch_delivers_restore_and_releases_registration(self):
        received = []
        hotkey = MacRestoreHotkey(lambda: (received.append(True), hotkey.close()))
        lib = hotkey.lib
        lib.CreateEvent.argtypes = [C.c_void_p, C.c_uint32, C.c_uint32,
                                    C.c_double, C.c_uint32, C.POINTER(C.c_void_p)]
        lib.CreateEvent.restype = C.c_int32
        lib.SetEventParameter.argtypes = [C.c_void_p, C.c_uint32, C.c_uint32,
                                          C.c_uint32, C.c_void_p]
        lib.SetEventParameter.restype = C.c_int32
        lib.SendEventToEventTarget.argtypes = [C.c_void_p, C.c_void_p]
        lib.SendEventToEventTarget.restype = C.c_int32
        lib.ReleaseEvent.argtypes = [C.c_void_p]
        lib.ReleaseEvent.restype = None
        event = C.c_void_p()
        try:
            self.assertTrue(hotkey.register(), hotkey.error)
            self.assertEqual(lib.CreateEvent(None, int.from_bytes(b"keyb", "big"),
                                             5, 0, 0, C.byref(event)), 0)
            key = HotKeyID(int.from_bytes(b"Olvp", "big"), 1)
            self.assertEqual(lib.SetEventParameter(event, int.from_bytes(b"----", "big"),
                int.from_bytes(b"hkid", "big"), C.sizeof(key), C.byref(key)), 0)
            self.assertEqual(lib.SendEventToEventTarget(event, lib.GetApplicationEventTarget()), 0)
            QtTest.QTest.qWait(20)
            self.assertEqual(received, [True])
            self.assertEqual(hotkey.refs, [])
        finally:
            if event.value:
                lib.ReleaseEvent(event)
            hotkey.close()


if __name__ == "__main__":
    unittest.main()
