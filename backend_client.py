"""Qt-side process client. No model SDK or history database access."""
from collections import deque
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
        self.process.started.connect(self._initialize)
        self.process.finished.connect(lambda *_: self._lost("聊天后台已退出，可重新连接后再发送。"))
        self.process.errorOccurred.connect(self._process_error)
        self.pending = {}
        self.queue = deque()
        self.buffer = bytearray()
        self.next_id = 0
        self.active = None
        self.ready = False
        self.closing = False
        self.chat_pending = False
        self.watchdog = QtCore.QTimer(self)
        self.watchdog.setInterval(500)
        self.watchdog.timeout.connect(self._check_deadlines)

    def start(self):
        if self.process.state() != QtCore.QProcess.NotRunning:
            return
        self.closing = False
        self.buffer.clear()
        self.process.start(sys.executable, ["-u", "-m", "backend"])
        self.watchdog.start()

    def _initialize(self):
        self.request("initialize", {})

    def request(self, method, params, text=None):
        self.next_id += 1
        request_id = self.next_id
        self.pending[request_id] = {"method": method, "text": text, "deadline": time.monotonic() + 10}
        payload = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}, ensure_ascii=False)
        self.process.write((payload + "\n").encode("utf-8"))
        return request_id

    def send(self, text):
        if not self.ready:
            self.request_failed.emit({"text": text, "message": "后台尚未连接，请稍后发送或重新连接。"})
            return
        self.queue.append(text)
        self._send_next()

    def _send_next(self):
        if self.chat_pending or not self.queue or not self.ready:
            return
        text = self.queue.popleft()
        self.chat_pending = True
        params = {"text": text}
        method = "chat/send"
        if self.active:
            method = "chat/steer"
            params["chatId"] = self.active["chatId"]
        self.request(method, params, text)

    def cancel(self):
        if self.active and self.ready and not self.chat_pending:
            self.request("chat/cancel", {"chatId": self.active["chatId"]})

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
            method = pending["method"]
            if method in ("chat/send", "chat/steer"):
                self.chat_pending = False
            if "error" in message:
                self.request_failed.emit({"text": pending["text"], "message": message["error"]["message"]})
            else:
                result = message["result"]
                if method == "initialize":
                    if result.get("protocolVersion") != 1:
                        raise ValueError("Unsupported protocol")
                    self.ready = True
                    self.ready_changed.emit(True)
                elif method in ("chat/send", "chat/steer"):
                    self.active = result
                self.response_received.emit(method, result)
            self._send_next()
            return
        method, params = message["method"], message.get("params", {})
        if method not in ("chat/started", "chat/delta", "chat/completed"):
            return
        if not self.active or any(params.get(key) != self.active.get(key) for key in ("chatId", "generation", "messageId")):
            return  # Late fragments from a superseded generation must not reach the UI.
        if method == "chat/started":
            self.chat_started.emit(params)
        elif method == "chat/delta":
            self.chat_delta.emit(params)
        else:
            self.active = None
            self.chat_completed.emit(params)

    def _protocol_failure(self):
        self._lost("后台通信异常，请重新连接。")
        self.process.kill()

    def _process_error(self, error):
        if error == QtCore.QProcess.FailedToStart:
            self._lost("无法启动聊天后台，请检查 Python 环境。")

    def _check_deadlines(self):
        if any(p["deadline"] < time.monotonic() for p in self.pending.values()):
            self._lost("后台请求超时，请重新连接；消息不会自动重发。")
            self.process.kill()

    def _lost(self, message):
        had_work = self.ready or self.pending or self.queue or self.active
        self.ready = False
        self.active = None
        self.chat_pending = False
        self.watchdog.stop()
        unsent = [p["text"] for p in self.pending.values() if p["text"]] + list(self.queue)
        self.pending.clear()
        self.queue.clear()
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
