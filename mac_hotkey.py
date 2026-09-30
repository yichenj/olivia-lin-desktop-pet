"""A macOS restore hotkey, registered only while the pet is hidden.

Uses the public Carbon hotkey API (HIToolbox SDK headers). No key event tap,
Accessibility permission, additional package, or background thread is needed.
"""
import ctypes as C

from PyQt5 import QtCore


class EventType(C.Structure):
    _fields_ = [("event_class", C.c_uint32), ("kind", C.c_uint32)]


class HotKeyID(C.Structure):
    _fields_ = [("signature", C.c_uint32), ("id", C.c_uint32)]


HANDLER = C.CFUNCTYPE(C.c_int32, C.c_void_p, C.c_void_p, C.c_void_p)


class MacRestoreHotkey(QtCore.QObject):
    activated = QtCore.pyqtSignal()

    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self.activated.connect(callback, QtCore.Qt.QueuedConnection)
        self.lib = C.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        signatures = {
            "GetApplicationEventTarget": (C.c_void_p, []),
            "InstallEventHandler": (C.c_int32, [C.c_void_p, HANDLER, C.c_uint32,
                C.POINTER(EventType), C.c_void_p, C.POINTER(C.c_void_p)]),
            "RegisterEventHotKey": (C.c_int32, [C.c_uint32, C.c_uint32, HotKeyID,
                C.c_void_p, C.c_uint32, C.POINTER(C.c_void_p)]),
            "GetEventParameter": (C.c_int32, [C.c_void_p, C.c_uint32, C.c_uint32,
                C.c_void_p, C.c_uint32, C.c_void_p, C.c_void_p]),
            "UnregisterEventHotKey": (C.c_int32, [C.c_void_p]),
            "RemoveEventHandler": (C.c_int32, [C.c_void_p]),
        }
        for name, (result, args) in signatures.items():
            function = getattr(self.lib, name)
            function.restype, function.argtypes = result, args
        self.handler = C.c_void_p()
        self.refs = []
        self.error = 0
        # Keep the callback alive for as long as Carbon holds its pointer.
        self.callback = HANDLER(self._handle)

    def _handle(self, next_handler, event, user_data):
        key = HotKeyID()
        status = self.lib.GetEventParameter(event, int.from_bytes(b"----", "big"),
            int.from_bytes(b"hkid", "big"), None, C.sizeof(key), None, C.byref(key))
        if status or key.signature != int.from_bytes(b"Olvp", "big") or key.id not in (1, 2):
            return -9874  # eventNotHandledErr: let other application handlers run.
        self.activated.emit()
        return 0

    def register(self):
        if self.refs:
            return True
        target = self.lib.GetApplicationEventTarget()
        event_type = EventType(int.from_bytes(b"keyb", "big"), 5)
        self.error = self.lib.InstallEventHandler(target, self.callback, 1,
            C.byref(event_type), None, C.byref(self.handler))
        if self.error:
            return False
        # cmdKey = 1 << 8; ANSI 0 and numeric keypad 0. Exclusive registration
        # detects conflicts rather than firing two apps for the same shortcut.
        for index, code in enumerate((0x1D, 0x52), 1):
            ref = C.c_void_p()
            self.error = self.lib.RegisterEventHotKey(code, 1 << 8,
                HotKeyID(int.from_bytes(b"Olvp", "big"), index), target, 1, C.byref(ref))
            if self.error:
                self.close()
                return False
            self.refs.append(ref)
        return True

    def close(self):
        for ref in self.refs:
            self.lib.UnregisterEventHotKey(ref)
        self.refs.clear()
        if self.handler.value:
            self.lib.RemoveEventHandler(self.handler)
            self.handler = C.c_void_p()
