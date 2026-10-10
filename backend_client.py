"""Qt transport and text rendering signals; no agent or turn control."""
import json
from pathlib import Path
import sys
import time

from PyQt5 import QtCore

ROOT = Path(__file__).resolve().parent


class BackendClient(QtCore.QObject):
    ready_changed = QtCore.pyqtSignal(bool)
    chat_started = QtCore.pyqtSignal(dict)
    chat_delta = QtCore.pyqtSignal(dict)
    chat_completed = QtCore.pyqtSignal(dict)
    request_failed = QtCore.pyqtSignal(dict)
    disconnected = QtCore.pyqtSignal(str)
    response_received = QtCore.pyqtSignal(str, object)

    def __init__(self, parent=None, environment=None):
        super().__init__(parent)
        self.process = QtCore.QProcess(self)
        self.process.setWorkingDirectory(str(ROOT))
        if environment is not None:
            env = QtCore.QProcessEnvironment()
            for key, value in environment.items():
                env.insert(key, str(value))
            self.process.setProcessEnvironment(env)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.readyReadStandardError.connect(lambda: self.process.readAllStandardError())
        self.process.finished.connect(lambda *_: self._lost("聊天后台已退出，可重新连接后再发送。"))
        self.process.errorOccurred.connect(self._process_error)
        self.pending = {}
        self.buffer = bytearray()
        self.next_id = 0
        self.current_message_id = None
        self.ready = False
        self.closing = False
        self.startup_deadline = None
        self.watchdog = QtCore.QTimer(self)
        self.watchdog.setInterval(500)
        self.watchdog.timeout.connect(self._check_deadlines)

    @property
    def streaming(self):
        """Whether a displayed message is receiving text, not agent execution state."""
        return self.current_message_id is not None

    def start(self):
        if self.process.state() != QtCore.QProcess.NotRunning:
            return
        self.closing = False
        self.buffer.clear()
        self.startup_deadline = time.monotonic() + 10
        self.process.start(sys.executable, ["-u", "-m", "backend"])
        self.watchdog.start()

    def send(self, text):
        if not self.ready:
            self.request_failed.emit({"text": text, "message": "后台尚未连接，请稍后发送或重新连接。"})
            return
        self.next_id += 1
        request_id = self.next_id
        self.pending[request_id] = {"text": text, "deadline": time.monotonic() + 10}
        payload = json.dumps({"jsonrpc": "2.0", "id": request_id,
                              "method": "chat/send", "params": {"text": text}}, ensure_ascii=False)
        self.process.write((payload + "\n").encode("utf-8"))

    def _read(self):
        self.buffer.extend(bytes(self.process.readAllStandardOutput()))
        if len(self.buffer) > 4 * 1024 * 1024:
            self._protocol_failure()
            return
        while b"\n" in self.buffer:
            line, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            try:
                message = json.loads(line)
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise ValueError("Invalid envelope")
                self._dispatch(message)
            except (ValueError, TypeError, KeyError):
                self._protocol_failure()
                return

    def _dispatch(self, message):
        if "id" in message:
            pending = self.pending.pop(message["id"], None)
            if pending is None:
                return
            if "error" in message:
                self.request_failed.emit({"text": pending["text"], "message": message["error"]["message"]})
            else:
                self.response_received.emit("chat/send", message["result"])
            return
        method, params = message["method"], message.get("params", {})
        if not isinstance(params, dict):
            raise ValueError("Invalid notification")
        if method == "backend/ready":
            self.startup_deadline = None
            self.ready = True
            self.ready_changed.emit(True)
            return
        if method not in ("chat/started", "chat/delta", "chat/completed") or not self.ready:
            return
        message_id = params.get("messageId")
        if not isinstance(message_id, str) or not message_id:
            raise ValueError("Missing message identity")
        if method == "chat/started":
            if message_id != self.current_message_id:
                self.current_message_id = message_id
                self.chat_started.emit(params)
        elif message_id == self.current_message_id:
            if method == "chat/delta":
                self.chat_delta.emit(params)
            else:
                self.current_message_id = None
                self.chat_completed.emit(params)
        # Only match the message being displayed. Backend owns turn fencing.

    def _protocol_failure(self):
        self._lost("后台通信异常，请重新连接。")
        self.process.kill()

    def _process_error(self, error):
        if error == QtCore.QProcess.FailedToStart:
            self._lost("无法启动聊天后台，请检查 Python 环境。")

    def _check_deadlines(self):
        now = time.monotonic()
        if ((self.startup_deadline is not None and self.startup_deadline < now)
                or any(p["deadline"] < now for p in self.pending.values())):
            self._lost("后台请求超时，请重新连接；消息不会自动重发。")
            self.process.kill()

    def _lost(self, message):
        had_work = self.ready or self.pending or self.streaming
        self.ready = False
        self.current_message_id = None
        self.startup_deadline = None
        self.watchdog.stop()
        unsent = [p["text"] for p in self.pending.values() if p["text"]]
        self.pending.clear()
        self.ready_changed.emit(False)
        if not self.closing:
            for text in unsent:
                self.request_failed.emit({"text": text, "message": message})
            if had_work or self.process.error() == QtCore.QProcess.FailedToStart:
                self.disconnected.emit(message)

    def close(self):
        self.closing = True
        self.watchdog.stop()
        if self.process.state() != QtCore.QProcess.NotRunning:
            self.process.closeWriteChannel()
            if not self.process.waitForFinished(1500):
                self.process.terminate()
                if not self.process.waitForFinished(500):
                    self.process.kill()
                    self.process.waitForFinished(500)
        self.ready = False
