#!/usr/bin/env python3
"""Olivia Lin fan-made desktop pet (macOS and Linux/X11, PyQt5)."""
import math
import random
import sys
import time
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
W, H = 450, 620
INK = QtGui.QColor("#2B2527")
CREAM = QtGui.QColor("#FBF7F1")


def notes_directory():
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Olivia Lin Fan Pet"
    return Path.home() / ".local/share/olivia-desktop-pet"


class NotesDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Olivia's little notebook · fan pet")
        self.setMinimumSize(400, 310)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("A little space for music and memories")
        title.setStyleSheet("font-size:17px;font-weight:600;color:#33282a;padding:5px")
        layout.addWidget(title)
        self.text = QtWidgets.QPlainTextEdit()
        self.text.setPlaceholderText("Write down a song, a thought, or something you want to remember…")
        self.text.setStyleSheet("QPlainTextEdit{background:#fffaf5;border:1px solid #eadbd2;border-radius:10px;padding:10px;font-size:14px;color:#33282a}")
        notes_dir = notes_directory()
        notes_dir.mkdir(parents=True, exist_ok=True)
        self.note_path = notes_dir / "notes.txt"
        if self.note_path.exists():
            self.text.setPlainText(self.note_path.read_text(encoding="utf-8"))
        layout.addWidget(self.text, 1)
        row = QtWidgets.QHBoxLayout()
        hint = QtWidgets.QLabel("Saved only on this computer")
        hint.setStyleSheet("color:#8a7b76;font-size:11px")
        row.addWidget(hint)
        row.addStretch(1)
        save = QtWidgets.QPushButton("Save note")
        save.setStyleSheet("QPushButton{background:#45393a;color:white;border:0;border-radius:9px;padding:9px 16px} QPushButton:hover{background:#705451}")
        save.clicked.connect(self.save_note)
        row.addWidget(save)
        layout.addLayout(row)

    def save_note(self):
        self.note_path.write_text(self.text.toPlainText(), encoding="utf-8")
        QtWidgets.QMessageBox.information(self, "Saved", "Your note is saved locally.")


class OliviaPet(QtWidgets.QWidget):
    message_submitted = QtCore.pyqtSignal(str)
    POSE_NAMES = {"idle": "待机", "standing": "站一站", "reading": "读一会",
                  "piano": "弹琴", "daydream": "发发呆"}
    DWELL_SECONDS = {"idle": (30, 55), "standing": (35, 65), "reading": (80, 140),
                     "piano": (65, 110), "daydream": (45, 85)}
    EYES = ((.397, .178, .078, .041), (.505, .147, .078, .041))

    def __init__(self):
        super().__init__()
        kind = QtCore.Qt.Window if sys.platform == "darwin" else QtCore.Qt.Tool
        # Cocoa can cache an alpha-shaped shadow of the first portrait. Disable
        # that system shadow: the transparent character is the entire surface.
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint
                            | QtCore.Qt.NoDropShadowWindowHint | kind)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setFixedSize(W, H)
        self.setWindowTitle("Olivia Lin — fan-made desktop pet")
        self.idle = QtGui.QPixmap(str(ASSETS / "portraits/idle.png"))
        self.blink_pose = QtGui.QPixmap(str(ASSETS / "portraits/blink.png"))
        self.poses = {name: QtGui.QPixmap(str(ASSETS / "poses" / f"{name}.png"))
                      for name in self.POSE_NAMES if name != "idle"}
        self.poses = {name: pix for name, pix in self.poses.items() if not pix.isNull()}
        # Always use the same PNG and transform for the base, including blinks.
        # The old GIF contains a differently framed portrait and is not loaded.
        self.pose = "idle"
        self.automatic = True
        self.sleeping = False
        self.drag_offset = None
        self.notes_dialog = None
        self.tray = None
        self.menu_bar = None
        self.restore_hotkey = None
        self.menu_open = False
        self.phase = 0.0
        now = time.monotonic()
        self.last_clock = self.last_interaction = now
        self.next_activity = now + random.uniform(*self.DWELL_SECONDS[self.pose])
        self.next_blink = now + random.uniform(9, 15)
        self.blink_until = 0.0
        self.last_message = ""
        self.setup_input()
        self.context_shortcut = QtWidgets.QShortcut(QtGui.QKeySequence("Shift+F10"), self)
        self.context_shortcut.activated.connect(
            lambda: self.open_context_menu(self.mapToGlobal(QtCore.QPoint(210, 470))))
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(33)
        self.move_to_corner()
        self.show()

    def setup_input(self):
        self.message_input = QtWidgets.QLineEdit(self)
        self.message_input.setGeometry(42, 524, 310, 42)
        self.message_input.setPlaceholderText("聊点什么，或输入想听的歌…")
        self.message_input.setAccessibleName("聊天与点歌输入")
        self.message_input.setMaxLength(1000)
        self.message_input.setStyleSheet("QLineEdit{background:#fffdf9;color:#382e30;"
            "border:1px solid #dfd4cb;border-radius:12px;padding:0 11px;font-size:13px}"
            "QLineEdit:focus{border:1px solid #a77e76}")
        self.message_input.textEdited.connect(self.note_interaction)
        self.message_input.returnPressed.connect(self.submit_message)
        self.send_button = QtWidgets.QPushButton("↑", self)
        self.send_button.setGeometry(361, 524, 46, 42)
        self.send_button.setAccessibleName("暂存输入")
        self.send_button.setToolTip("暂存本次输入；聊天和播放服务尚未接入")
        self.send_button.setStyleSheet("QPushButton{background:#755e5c;color:#fffaf5;"
            "border:0;border-radius:12px;font-size:22px} QPushButton:disabled{background:#c9bbb4}")
        self.send_button.setEnabled(False)
        self.message_input.textChanged.connect(
            lambda text: self.send_button.setEnabled(bool(text.strip())))
        self.send_button.clicked.connect(self.submit_message)
        self.input_status = QtWidgets.QLabel("聊天与点歌待接入 · 右键切换状态", self)
        self.input_status.setGeometry(43, 574, 364, 22)
        self.input_status.setStyleSheet("color:#887570;font-size:11px;background:transparent")
        self.input_status.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)

    def submit_message(self):
        text = self.message_input.text().strip()
        if not text:
            return
        self.note_interaction()
        self.last_message = text
        self.message_input.clear()
        self.message_submitted.emit(text)
        self.input_status.setText("已暂存本次输入 · 聊天与点歌尚未接入")
        self.input_status.setToolTip(text)

    def setup_desktop_controls(self):
        """Install a persistent show/hide/quit menu; called only by the app entry point."""
        app = QtWidgets.QApplication.instance()
        self.quit_action = QtWidgets.QAction("退出 Olivia", self)
        self.quit_action.setShortcut(QtGui.QKeySequence.Quit)
        self.quit_action.setMenuRole(QtWidgets.QAction.QuitRole)
        self.quit_action.triggered.connect(app.quit)
        self.addAction(self.quit_action)
        self.show_action = QtWidgets.QAction("显示 Olivia", self)
        self.show_action.setShortcut(QtGui.QKeySequence("Ctrl+0"))
        self.show_action.setShortcutContext(QtCore.Qt.ApplicationShortcut)
        self.addAction(self.show_action)
        self.show_action.triggered.connect(self.restore_window)
        if sys.platform == "darwin":
            from mac_hotkey import MacRestoreHotkey
            try:
                self.restore_hotkey = MacRestoreHotkey(self.restore_window, self)
                app.aboutToQuit.connect(self.restore_hotkey.close)
            except (OSError, AttributeError) as error:
                self.input_status.setText("快捷键不可用，可用菜单栏音符恢复窗口")
                print(f"Cannot initialize macOS restore shortcut: {error}", file=sys.stderr)
            self.menu_bar = QtWidgets.QMenuBar()
            menu = self.menu_bar.addMenu("Olivia")
            menu.addAction(self.show_action)
            menu.addAction(self.quit_action)
        if QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            # A template icon remains readable in either macOS menu-bar theme.
            pix = QtGui.QPixmap(44, 44)
            pix.fill(QtCore.Qt.transparent)
            painter = QtGui.QPainter(pix)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtCore.Qt.black)
            painter.drawEllipse(QtCore.QRectF(7, 27, 17, 12))
            painter.drawRect(QtCore.QRectF(20, 6, 4, 27))
            painter.drawPolygon(QtGui.QPolygonF([
                QtCore.QPointF(24, 6), QtCore.QPointF(36, 12),
                QtCore.QPointF(36, 19), QtCore.QPointF(24, 13)]))
            painter.end()
            icon = QtGui.QIcon(pix)
            icon.setIsMask(True)
            self.tray = QtWidgets.QSystemTrayIcon(icon, self)
            self.tray.setToolTip("Olivia Lin · 桌面宠物")
            menu = QtWidgets.QMenu(self)
            menu.addAction(self.show_action)
            menu.addAction("收起 Olivia", self.hide_to_tray)
            menu.addSeparator()
            menu.addAction(self.quit_action)
            self.tray.setContextMenu(menu)
            self.tray.show()
            app.aboutToQuit.connect(self.tray.hide)

    def restore_window(self):
        if self.restore_hotkey:
            self.restore_hotkey.close()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def hide_to_tray(self):
        if self.restore_hotkey and not self.restore_hotkey.register():
            # Keep a Dock-visible recovery route if another app owns the hotkey.
            self.input_status.setText("⌘0 注册失败，请通过 Dock 或菜单栏恢复")
            self.showMinimized()
            return
        if self.tray is not None and self.tray.isVisible():
            self.hide()
        else:
            self.showMinimized()

    def showEvent(self, event):
        if self.restore_hotkey and not self.isMinimized():
            self.restore_hotkey.close()
        super().showEvent(event)

    def changeEvent(self, event):
        if (event.type() == QtCore.QEvent.WindowStateChange and self.isVisible()
                and not self.isMinimized() and self.restore_hotkey):
            # Dock restoration does not necessarily send another showEvent.
            self.restore_hotkey.close()
        super().changeEvent(event)

    def closeEvent(self, event):
        if self.restore_hotkey:
            self.restore_hotkey.close()
        if self.tray:
            self.tray.hide()
        if self.menu_bar:
            self.menu_bar.deleteLater()
        super().closeEvent(event)

    def tick(self):
        now = time.monotonic()
        if not self.sleeping:
            self.phase += max(0, min(.08, now - self.last_clock)) * 1.4
        self.last_clock = now
        if now >= self.blink_until:
            self.blink_until = 0.0
        if not self.sleeping and self.isVisible() and not self.isMinimized():
            if self.pose == "idle" and not self.blink_pose.isNull() and now >= self.next_blink:
                self.blink_until = now + .18
                self.next_blink = now + random.uniform(9, 17)
            busy = (self.drag_offset is not None or self.menu_open
                    or bool(self.message_input.text())
                    or (self.notes_dialog is not None and self.notes_dialog.isVisible()))
            if (self.automatic and not busy and now >= self.next_activity
                    and now - self.last_interaction >= 8):
                candidates = [name for name in ("idle", *self.poses) if name != self.pose]
                if candidates:
                    self.set_pose(random.choice(candidates), manual=False)
        self.update()

    def portrait_target(self):
        pix = self.idle if self.pose == "idle" else self.poses[self.pose]
        size = pix.size().scaled(330, 434, QtCore.Qt.KeepAspectRatio)
        return QtCore.QRectF((W - size.width()) / 2, 6 + math.sin(self.phase),
                             size.width(), size.height())

    def eye_regions(self):
        target = self.portrait_target()
        return [QtCore.QRectF(target.x() + x * target.width(), target.y() + y * target.height(),
                              w * target.width(), h * target.height()) for x, y, w, h in self.EYES]

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        # Clear the backing pixels on every paint, including newly transparent
        # areas after a pose switch. Never retain the previous portrait layer.
        p.setCompositionMode(QtGui.QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), QtCore.Qt.transparent)
        p.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        pix = self.idle if self.pose == "idle" else self.poses[self.pose]
        if not pix.isNull():
            target = self.portrait_target()
            p.drawPixmap(target, pix, QtCore.QRectF(pix.rect()))
            if (self.pose == "idle" and not self.sleeping
                    and self.blink_until > time.monotonic() and not self.blink_pose.isNull()):
                clip = QtGui.QPainterPath()
                for rect in self.eye_regions():
                    clip.addEllipse(rect)
                p.save()
                p.setClipPath(clip)
                p.drawPixmap(target, self.blink_pose, QtCore.QRectF(self.blink_pose.rect()))
                p.restore()
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(CREAM)
        p.drawRoundedRect(QtCore.QRectF(20, 450, 410, 157), 23, 23)
        p.setPen(INK)
        p.setFont(QtGui.QFont("Arial", 18, QtGui.QFont.DemiBold))
        p.drawText(QtCore.QRectF(42, 466, 245, 28), QtCore.Qt.AlignVCenter, "Olivia Lin")
        p.setFont(QtGui.QFont("Arial", 9))
        p.setPen(QtGui.QColor("#907b74"))
        mode = "已暂停" if self.sleeping else ("自在活动" if self.automatic else "保持姿势")
        p.drawText(QtCore.QRectF(43, 497, 364, 18), QtCore.Qt.AlignVCenter,
                   f"{mode} · {self.POSE_NAMES[self.pose]}")
        p.setFont(QtGui.QFont("Arial", 8))
        p.drawText(QtCore.QRectF(276, 473, 129, 18), QtCore.Qt.AlignRight, "AI 同人 · 非官方")
        p.end()

    def note_interaction(self, *_):
        self.last_interaction = time.monotonic()

    def set_pose(self, name, manual=True):
        if name != "idle" and name not in self.poses:
            return
        now = time.monotonic()
        if manual:
            self.note_interaction()
            self.automatic = False
        self.pose = name
        self.blink_until = 0.0
        self.next_blink = now + random.uniform(9, 15)
        self.next_activity = now + random.uniform(*self.DWELL_SECONDS[name])
        self.update()

    def resume_automatic(self):
        self.automatic = True
        self.sleeping = False
        self.note_interaction()
        self.next_activity = time.monotonic() + random.uniform(*self.DWELL_SECONDS[self.pose])
        self.update()

    def toggle_rest(self):
        self.sleeping = not self.sleeping
        self.blink_until = 0.0
        self.next_blink = time.monotonic() + random.uniform(9, 15)
        self.next_activity = time.monotonic() + random.uniform(*self.DWELL_SECONDS[self.pose])
        self.update()

    def notes(self):
        self.note_interaction()
        if self.notes_dialog is None:
            self.notes_dialog = NotesDialog(self)
        self.notes_dialog.show()
        self.notes_dialog.raise_()

    def move_to_corner(self):
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            self.move(max(area.left(), area.right() - W - 34),
                      max(area.top(), area.bottom() - H - 20))

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.note_interaction()
            self.drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        self.open_context_menu(event.globalPos())
        event.accept()

    def open_context_menu(self, position):
        self.note_interaction()
        menu = self.build_context_menu()
        self.menu_open = True
        try:
            menu.exec_(position)
        finally:
            self.menu_open = False
            menu.deleteLater()

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None and event.buttons() & QtCore.Qt.LeftButton:
            self.move(event.globalPos() - self.drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_offset = None
        event.accept()

    def build_context_menu(self):
        menu = QtWidgets.QMenu(self)
        automatic = menu.addAction("自动活动")
        automatic.setCheckable(True)
        automatic.setChecked(self.automatic)
        automatic.triggered.connect(self.resume_automatic)
        menu.addSeparator()
        for name in ("idle", *self.poses):
            action = menu.addAction(self.POSE_NAMES[name] + " · 保持")
            action.setCheckable(True)
            action.setChecked(not self.automatic and self.pose == name)
            action.triggered.connect(lambda checked=False, pose=name: self.set_pose(pose))
        menu.addSeparator()
        menu.addAction("移到屏幕角落", self.move_to_corner)
        menu.addSeparator()
        menu.addAction("收起窗口", self.hide_to_tray)
        menu.addAction("退出", QtWidgets.QApplication.quit)
        return menu

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            self.hide_to_tray()
        else:
            super().keyPressEvent(event)


def main():
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Olivia Lin Fan Pet")
    app.setQuitOnLastWindowClosed(True)
    pet = OliviaPet()
    pet.setup_desktop_controls()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
