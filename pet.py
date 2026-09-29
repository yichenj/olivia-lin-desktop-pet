#!/usr/bin/env python3
"""Olivia Lin fan-made desktop pet prototype (Linux / X11, PyQt5)."""
import math
import os
import random
import sys
import time
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

ROOT = Path(__file__).resolve().parent
W, H = 430, 690
INK = QtGui.QColor("#2B2527")
CREAM = QtGui.QColor("#FBF7F1")
ROSE = QtGui.QColor("#BD847F")


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
        notes_dir = Path.home() / ".local/share/olivia-desktop-pet"
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
    def __init__(self):
        super().__init__()
        flags = QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool
        self.setWindowFlags(flags)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.setMouseTracking(True)
        self.resize(W, H)
        self.setWindowTitle("Olivia Lin — fan-made desktop pet")

        self.idle = QtGui.QPixmap(str(ROOT / "olivia_idle.png"))
        self.wave = QtGui.QPixmap(str(ROOT / "olivia_smile.png"))
        self.movie = None
        gif = ROOT / "olivia_idle.gif"
        if gif.exists():
            movie = QtGui.QMovie(str(gif))
            movie.setCacheMode(QtGui.QMovie.CacheAll)
            movie.setSpeed(100)
            if movie.isValid():
                self.movie = movie
                self.movie.start()

        self.mood = "idle"
        self.mood_until = 0.0
        self.bubble = "嗨，今天想听点什么？"
        self.bubble_until = time.monotonic() + 9
        self.sleeping = False
        self.drag_offset = None
        self.notes_dialog = None
        self.last_clock = time.monotonic()
        self.phase = 0.0
        self.button_rects = {}
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(33)

        screen = QtWidgets.QApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            self.move(area.right() - W - 34, area.bottom() - H - 20)
        self.show()

    def tick(self):
        now = time.monotonic()
        self.phase += min(0.08, now - self.last_clock) * (2.5 if not self.sleeping else 0.42)
        self.last_clock = now
        if self.mood != "idle" and now > self.mood_until:
            self.mood = "idle"
        if self.bubble_until and now > self.bubble_until:
            self.bubble_until = 0
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        bob = math.sin(self.phase) * (3.0 if not self.sleeping else 1.0)

        # Floating musical accents
        p.setPen(QtCore.Qt.NoPen)
        for x, y, r, a in [(28, 225, 4, 90), (395, 196, 3, 70), (385, 330, 5, 85)]:
            p.setBrush(QtGui.QColor(239, 209, 173, int(a + 20 * math.sin(self.phase + x))))
            p.drawEllipse(QtCore.QPointF(x, y + math.sin(self.phase + y) * 5), r, r)
        p.setPen(QtGui.QColor(200, 173, 151, 145))
        f = p.font(); f.setPointSize(17); f.setWeight(QtGui.QFont.Light); p.setFont(f)
        p.drawText(QtCore.QPointF(30, 295 + math.sin(self.phase * .8) * 3), "♪")
        p.drawText(QtCore.QPointF(397, 262 + math.sin(self.phase * .8 + 1) * 3), "♫")

        # Main transparent illustration, with a real idle loop rendered in Blender when available.
        pix = self.idle
        if self.movie is not None and self.movie.currentPixmap().isNull() is False:
            pix = self.movie.currentPixmap()
        if self.mood == "wave" and not self.wave.isNull():
            pix = self.wave
        if not pix.isNull():
            scale = 1.0 + (0.008 * math.sin(self.phase * 0.75) if self.mood != "wave" else 0.014 * math.sin(self.phase * 2.2))
            target_w = 405 * scale
            target_h = 540 * scale
            x = (W - target_w) / 2
            y = -7 + bob - (target_h - 540) / 2
            p.drawPixmap(QtCore.QRectF(x, y, target_w, target_h), pix, QtCore.QRectF(pix.rect()))
        else:
            p.setBrush(QtGui.QColor(245, 238, 231, 235))
            p.setPen(QtGui.QPen(QtGui.QColor(211, 194, 183), 1))
            p.drawRoundedRect(QtCore.QRectF(70, 90, 290, 360), 38, 38)
            p.setPen(INK); p.drawText(QtCore.QRect(92, 220, 250, 50), QtCore.Qt.AlignCenter, "Portrait asset is missing")

        # Small fan-made label
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(255, 251, 246, 222))
        p.drawRoundedRect(QtCore.QRectF(275, 15, 135, 28), 14, 14)
        p.setPen(QtGui.QColor("#766360"))
        f = p.font(); f.setPointSize(8); f.setBold(True); p.setFont(f)
        p.drawText(QtCore.QRectF(275, 15, 135, 28), QtCore.Qt.AlignCenter, "AI FAN ART · REF-BASED")

        # Speech card
        if self.bubble_until:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(255, 251, 247, 240))
            p.drawRoundedRect(QtCore.QRectF(14, 47, 265, 66), 18, 18)
            p.setBrush(QtGui.QColor(255, 251, 247, 240))
            p.drawEllipse(QtCore.QRectF(35, 104, 13, 13))
            p.setPen(QtGui.QColor("#463b3b"))
            f = p.font(); f.setPointSize(11); f.setWeight(QtGui.QFont.Medium); p.setFont(f)
            p.drawText(QtCore.QRectF(29, 60, 238, 42), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, self.bubble)

        # Frosted companion card, overlapping the bottom of the cutout intentionally.
        card = QtCore.QRectF(24, 476, 382, 193)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 125), 1))
        p.setBrush(QtGui.QColor(250, 246, 241, 238))
        p.drawRoundedRect(card, 25, 25)
        p.setPen(QtGui.QColor("#302729"))
        f = p.font(); f.setPointSize(19); f.setWeight(QtGui.QFont.DemiBold); p.setFont(f)
        p.drawText(QtCore.QRectF(45, 490, 250, 30), QtCore.Qt.AlignVCenter, "Olivia Lin")
        p.setPen(QtGui.QColor("#8B7974"))
        f = p.font(); f.setPointSize(10); f.setWeight(QtGui.QFont.Normal); p.setFont(f)
        p.drawText(QtCore.QRectF(46, 520, 340, 21), QtCore.Qt.AlignVCenter, "钢琴 · 音乐与记忆 · 本地互动原型")
        p.setPen(QtGui.QColor("#9B7772"))
        f = p.font(); f.setPointSize(9); f.setWeight(QtGui.QFont.DemiBold); p.setFont(f)
        p.drawText(QtCore.QRectF(46, 545, 340, 20), QtCore.Qt.AlignVCenter, "点一点人物、拖动窗口，或试试下面的按钮")

        labels = [("打个招呼", "hello"), ("听一音", "piano"), ("小记事", "notes")]
        self.button_rects = {}
        for i, (label, key) in enumerate(labels):
            rect = QtCore.QRectF(42 + i * 119, 577, 109, 52)
            self.button_rects[key] = rect
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(65, 54, 55, 255) if key == "hello" else QtGui.QColor(239, 230, 221, 255))
            p.drawRoundedRect(rect, 15, 15)
            p.setPen(QtGui.QColor("#fffaf5") if key == "hello" else QtGui.QColor("#534548"))
            f = p.font(); f.setPointSize(10); f.setWeight(QtGui.QFont.DemiBold); p.setFont(f)
            p.drawText(rect, QtCore.Qt.AlignCenter, label)

        p.setPen(QtGui.QColor(124, 110, 105, 210))
        f = p.font(); f.setPointSize(8); p.setFont(f)
        p.drawText(QtCore.QRectF(55, 642, 320, 16), QtCore.Qt.AlignCenter, "右键可收起或退出  ·  拖动任意空白处移动")
        p.end()

    def say(self, text, duration=4.0, wave=False):
        self.bubble = text
        self.bubble_until = time.monotonic() + duration
        if wave:
            self.mood = "wave"
            self.mood_until = time.monotonic() + 3.5
        self.update()

    def hello(self):
        self.say(random.choice(["你好呀，愿今天有一段好听的旋律。", "我刚好在想一首钢琴曲。", "要不要一起记住今天的声音？", "嗨，见到你真好。"]), wave=True)

    def piano(self):
        # An intentionally local click cue, not a piano/MIDI performance engine.
        QtWidgets.QApplication.beep()
        self.say(random.choice(["叮——像雨落在窗边。", "这一个音，送给你。", "听见了吗？像一个小小的开场。"]), 3.2)

    def notes(self):
        self.notes_dialog = NotesDialog(self)
        self.notes_dialog.show()
        self.notes_dialog.raise_()
        self.say("把喜欢的旋律和记忆写下来吧。", 4.5)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.RightButton:
            self.context_menu(event.globalPos()); return
        if event.button() == QtCore.Qt.LeftButton:
            point = event.pos()
            for key, rect in self.button_rects.items():
                if rect.contains(point):
                    {"hello": self.hello, "piano": self.piano, "notes": self.notes}[key]()
                    return
            if 110 < point.y() < 475:
                self.hello(); return
            self.drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None and event.buttons() & QtCore.Qt.LeftButton:
            self.move(event.globalPos() - self.drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_offset = None
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self.sleeping = not self.sleeping
        self.say("休息一下……" if self.sleeping else "我回来啦。", 3)

    def context_menu(self, pos):
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("QMenu{background:#fffaf5;color:#382e30;border:1px solid #e5d8ce;padding:5px} QMenu::item{padding:7px 20px;border-radius:5px} QMenu::item:selected{background:#efe3d9}")
        move = menu.addAction("移到屏幕角落")
        sleep = menu.addAction("休息 / 唤醒")
        menu.addSeparator()
        about = menu.addAction("关于这个粉丝原型")
        hide = menu.addAction("收起窗口")
        quit_action = menu.addAction("退出")
        chosen = menu.exec_(pos)
        if chosen == move:
            screen = QtWidgets.QApplication.primaryScreen()
            if screen:
                area = screen.availableGeometry()
                self.move(area.left() + 18, area.bottom() - H - 16)
        elif chosen == sleep:
            self.sleeping = not self.sleeping
            self.say("休息一下……" if self.sleeping else "我回来啦。", 3)
        elif chosen == about:
            QtWidgets.QMessageBox.about(self, "关于 Olivia Lin 桌面宠物", "<b>Olivia Lin · fan-made desktop pet</b><br><br>这是一个本地运行的非官方互动原型，不隶属于 BSide 或其权利人。人物立绘由图像生成器根据公开 BSide 图片搜索参考图生成，和参考脸部高度相似；应视为参考条件 AI 同人图，不能称为独立原创设计或官方美术。<br><br>互动、记事本与钢琴提示音均为本机演示功能；完整立绘来源记录见项目的 PROVENANCE.md。")
        elif chosen == hide:
            self.hide()
        elif chosen == quit_action:
            QtWidgets.QApplication.quit()

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            self.hide()
        elif event.key() == QtCore.Qt.Key_Space:
            self.hello()
        else:
            super().keyPressEvent(event)


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Olivia Lin Fan Pet")
    app.setQuitOnLastWindowClosed(True)
    pet = OliviaPet()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
