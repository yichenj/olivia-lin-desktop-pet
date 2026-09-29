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
W, H = 450, 760
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
        self.blink_pose = QtGui.QPixmap(str(ROOT / "olivia_blink.png"))
        self.poses = {
            name: QtGui.QPixmap(str(ROOT / "assets" / "poses" / f"{name}.png"))
            for name in ("standing", "reading", "piano", "daydream")
        }
        self.poses = {name: pix for name, pix in self.poses.items() if not pix.isNull()}
        self.pose = "idle"
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
        now = time.monotonic()
        self.last_interaction = now
        self.next_blink = now + random.uniform(9.0, 15.0)
        self.next_idle_reaction = now + 55.0
        self.blink_until = 0.0
        self.bubble = "嗨，今天想听点什么？"
        self.bubble_until = time.monotonic() + 9
        self.sleeping = False
        self.drag_offset = None
        self.notes_dialog = None
        self.last_clock = time.monotonic()
        self.phase = 0.0
        self.button_rects = {}
        self.pose_button_rects = {}
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
        if now >= self.blink_until:
            self.blink_until = 0.0
        if not self.sleeping and self.mood == "idle":
            if (self.pose == "idle" and not self.blink_pose.isNull()
                    and not self.bubble_until and now >= self.next_blink):
                self.blink_until = now + 0.18
                self.next_blink = now + random.uniform(14.0, 24.0)
            if now - self.last_interaction >= 55.0 and now >= self.next_idle_reaction:
                self.say(random.choice([
                    "安静的时候，也适合听一段旋律。",
                    "我在这里陪你歇一会儿。",
                    "要不要记下一首今天想到的歌？",
                ]), 4.5)
                self.next_idle_reaction = now + 55.0
        if self.bubble_until and now > self.bubble_until:
            self.bubble_until = 0
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        # Start from transparent pixels so cut-outs and overlays composite cleanly.
        p.setCompositionMode(QtGui.QPainter.CompositionMode_Clear)
        p.fillRect(event.rect(), QtCore.Qt.transparent)
        p.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
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

        # New action illustrations are single static pose images, not frame animations.
        blink_active = (self.pose == "idle" and self.blink_until > time.monotonic()
                        and self.mood == "idle" and not self.sleeping)
        pix = self.idle
        action_pix = self.poses.get(self.pose)
        if action_pix is not None:
            pix = action_pix
        elif self.pose == "idle" and not blink_active and self.movie is not None and self.movie.currentPixmap().isNull() is False:
            pix = self.movie.currentPixmap()
        if self.pose == "idle" and self.mood == "wave" and not self.wave.isNull():
            pix = self.wave
        if not pix.isNull():
            if action_pix is not None:
                scale = min(298 / pix.width(), 448 / pix.height())
                target_w = pix.width() * scale
                target_h = pix.height() * scale
                x = (W - target_w) / 2
                y = -2 + bob * 0.35
            else:
                scale = 1.0 + (0.008 * math.sin(self.phase * 0.75) if self.mood != "wave" else 0.014 * math.sin(self.phase * 2.2))
                target_w = 405 * scale
                target_h = 540 * scale
                x = (W - target_w) / 2
                y = -7 + bob - (target_h - 540) / 2
            target = QtCore.QRectF(x, y, target_w, target_h)
            p.drawPixmap(target, pix, QtCore.QRectF(pix.rect()))
            if blink_active and not self.blink_pose.isNull():
                # Soft oval clips hide the generated frame's unrelated pixel changes.
                clip = QtGui.QPainterPath()
                for nx, ny, nw, nh in ((0.397, 0.178, 0.078, 0.041), (0.505, 0.147, 0.078, 0.041)):
                    clip.addEllipse(QtCore.QRectF(x + nx * target_w, y + ny * target_h,
                                                  nw * target_w, nh * target_h))
                p.save()
                p.setClipPath(clip)
                p.drawPixmap(target, self.blink_pose, QtCore.QRectF(self.blink_pose.rect()))
                p.restore()
        else:
            p.setBrush(QtGui.QColor(245, 238, 231, 235))
            p.setPen(QtGui.QPen(QtGui.QColor(211, 194, 183), 1))
            p.drawRoundedRect(QtCore.QRectF(70, 90, 290, 360), 38, 38)
            p.setPen(INK); p.drawText(QtCore.QRect(92, 220, 250, 50), QtCore.Qt.AlignCenter, "Portrait asset is missing")

        # Small fan-made label
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(255, 251, 246, 222))
        p.drawRoundedRect(QtCore.QRectF(8, 15, 135, 28), 14, 14)
        p.setPen(QtGui.QColor("#766360"))
        f = p.font(); f.setPointSize(8); f.setBold(True); p.setFont(f)
        p.drawText(QtCore.QRectF(8, 15, 135, 28), QtCore.Qt.AlignCenter, "AI FAN ART · REF-BASED")

        # Speech card
        if self.bubble_until and self.pose == "idle":
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(255, 251, 247, 240))
            p.drawRoundedRect(QtCore.QRectF(310, 47, 130, 66), 18, 18)
            p.setBrush(QtGui.QColor(255, 251, 247, 240))
            p.drawEllipse(QtCore.QRectF(300, 102, 13, 13))
            p.setPen(QtGui.QColor("#463b3b"))
            f = p.font(); f.setPointSize(9); f.setWeight(QtGui.QFont.Medium); p.setFont(f)
            p.drawText(QtCore.QRectF(318, 54, 114, 51),
                       QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft | QtCore.Qt.TextWordWrap, self.bubble)

        # Frosted companion card with an explicit pose row and a separate utility row.
        card = QtCore.QRectF(20, 448, 410, 292)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 125), 1))
        p.setBrush(QtGui.QColor(250, 246, 241, 238))
        p.drawRoundedRect(card, 25, 25)
        p.setPen(QtGui.QColor("#302729"))
        f = p.font(); f.setPointSize(19); f.setWeight(QtGui.QFont.DemiBold); p.setFont(f)
        p.drawText(QtCore.QRectF(42, 458, 250, 29), QtCore.Qt.AlignVCenter, "Olivia Lin")
        p.setPen(QtGui.QColor("#8B7974"))
        f = p.font(); f.setPointSize(10); f.setWeight(QtGui.QFont.Normal); p.setFont(f)
        p.drawText(QtCore.QRectF(43, 487, 360, 20), QtCore.Qt.AlignVCenter, "钢琴 · 音乐与记忆 · 本地互动原型")
        p.setPen(QtGui.QColor("#9B7772"))
        f = p.font(); f.setPointSize(8); f.setWeight(QtGui.QFont.DemiBold); p.setFont(f)
        pose_names = {"standing": "站立", "reading": "读书", "piano": "弹琴", "daydream": "发呆"}
        pose_hint = (f"当前：{pose_names.get(self.pose, '动作')} · 按 I 或‘回 idle’返回"
                     if self.pose != "idle" else "动作姿势为单张静帧 · 可用按键 S / R / P / D 切换")
        p.drawText(QtCore.QRectF(43, 509, 360, 18), QtCore.Qt.AlignVCenter, pose_hint)

        pose_labels = [("站一站", "standing"), ("读一会", "reading"),
                       ("弹琴", "piano"), ("发发呆", "daydream")]
        pose_labels = [(label, key) for label, key in pose_labels if key in self.poses]
        self.pose_button_rects = {}
        if pose_labels:
            action_w, action_gap, action_y, action_h = 84, 7, 533, 42
            action_left = (W - (len(pose_labels) * action_w + (len(pose_labels) - 1) * action_gap)) / 2
            for i, (label, key) in enumerate(pose_labels):
                rect = QtCore.QRectF(action_left + i * (action_w + action_gap), action_y, action_w, action_h)
                self.pose_button_rects[key] = rect
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(QtGui.QColor("#755E5C") if self.pose == key else QtGui.QColor(239, 230, 221, 255))
                p.drawRoundedRect(rect, 13, 13)
                p.setPen(QtGui.QColor("#fffaf5") if self.pose == key else QtGui.QColor("#534548"))
                f = p.font(); f.setPointSize(9); f.setWeight(QtGui.QFont.DemiBold); p.setFont(f)
                p.drawText(rect, QtCore.Qt.AlignCenter, label)

        labels = [("打招呼", "hello")]
        if not self.blink_pose.isNull():
            labels.append(("眨眨眼", "blink"))
        labels.extend([("听一音", "piano"), ("小记事", "notes"), ("回 idle", "return")])
        self.button_rects = {}
        button_w = 68 if len(labels) == 5 else 80
        gap = 6
        left = (W - (len(labels) * button_w + (len(labels) - 1) * gap)) / 2
        for i, (label, key) in enumerate(labels):
            rect = QtCore.QRectF(left + i * (button_w + gap), 585, button_w, 46)
            self.button_rects[key] = rect
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(65, 54, 55, 255) if key == "hello" else QtGui.QColor(239, 230, 221, 255))
            p.drawRoundedRect(rect, 15, 15)
            p.setPen(QtGui.QColor("#fffaf5") if key == "hello" else QtGui.QColor("#534548"))
            f = p.font(); f.setPointSize(9); f.setWeight(QtGui.QFont.DemiBold); p.setFont(f)
            p.drawText(rect, QtCore.Qt.AlignCenter, label)

        p.setPen(QtGui.QColor(124, 110, 105, 210))
        f = p.font(); f.setPointSize(8); p.setFont(f)
        p.drawText(QtCore.QRectF(45, 645, 360, 18), QtCore.Qt.AlignCenter, "空格打招呼  ·  B 眨眼  ·  I 回待机  ·  右键更多")
        p.end()

    def say(self, text, duration=4.0, wave=False):
        self.bubble = text
        self.bubble_until = time.monotonic() + duration
        if wave:
            self.mood = "wave"
            self.mood_until = time.monotonic() + 3.5
        self.update()

    def note_interaction(self):
        now = time.monotonic()
        self.last_interaction = now
        self.next_idle_reaction = now + 55.0
        self.next_blink = now + random.uniform(8.0, 14.0)

    def hello(self):
        self.note_interaction()
        self.pose = "idle"
        self.say(random.choice(["你好呀，愿今天有一段好听的旋律。", "我刚好在想一首钢琴曲。", "要不要一起记住今天的声音？", "嗨，见到你真好。"]), wave=True)

    def set_pose(self, name):
        if name not in self.poses:
            return
        self.note_interaction()
        self.pose = name
        self.mood = "idle"
        self.blink_until = 0.0
        self.bubble_until = 0.0
        self.update()

    def return_idle(self):
        self.note_interaction()
        self.pose = "idle"
        self.mood = "idle"
        self.blink_until = 0.0
        self.say("回到待机啦。", 2.8)

    def blink(self):
        if self.blink_pose.isNull() or self.sleeping or self.mood != "idle" or self.pose != "idle":
            return
        self.note_interaction()
        self.blink_until = time.monotonic() + 0.18
        self.bubble_until = 0.0
        self.update()

    def piano(self):
        self.note_interaction()
        # An intentionally local click cue, not a piano/MIDI performance engine.
        QtWidgets.QApplication.beep()
        self.say(random.choice(["叮——像雨落在窗边。", "这一个音，送给你。", "听见了吗？像一个小小的开场。"]), 3.2)

    def notes(self):
        self.note_interaction()
        self.notes_dialog = NotesDialog(self)
        self.notes_dialog.show()
        self.notes_dialog.raise_()
        self.say("把喜欢的旋律和记忆写下来吧。", 4.5)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.RightButton:
            self.note_interaction()
            self.context_menu(event.globalPos()); return
        if event.button() == QtCore.Qt.LeftButton:
            self.note_interaction()
            point = event.pos()
            for key, rect in self.pose_button_rects.items():
                if rect.contains(point):
                    self.set_pose(key)
                    return
            for key, rect in self.button_rects.items():
                if rect.contains(point):
                    {"hello": self.hello, "blink": self.blink,
                     "piano": self.piano, "notes": self.notes,
                     "return": self.return_idle}[key]()
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
        self.note_interaction()
        self.sleeping = not self.sleeping
        self.say("休息一下……" if self.sleeping else "我回来啦。", 3)

    def context_menu(self, pos):
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("QMenu{background:#fffaf5;color:#382e30;border:1px solid #e5d8ce;padding:5px} QMenu::item{padding:7px 20px;border-radius:5px} QMenu::item:selected{background:#efe3d9}")
        pose_actions = {}
        for name, label in (("standing", "站一站"), ("reading", "读一会"),
                            ("piano", "弹琴姿势"), ("daydream", "发发呆")):
            if name in self.poses:
                pose_actions[menu.addAction(label)] = name
        return_pose = menu.addAction("回到 idle 姿势")
        menu.addSeparator()
        move = menu.addAction("移到屏幕角落")
        sleep = menu.addAction("休息 / 唤醒")
        menu.addSeparator()
        about = menu.addAction("关于这个粉丝原型")
        hide = menu.addAction("收起窗口")
        quit_action = menu.addAction("退出")
        chosen = menu.exec_(pos)
        if chosen in pose_actions:
            self.set_pose(pose_actions[chosen])
        elif chosen == return_pose:
            self.return_idle()
        elif chosen == move:
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
        elif event.key() == QtCore.Qt.Key_B:
            self.blink()
        elif event.key() == QtCore.Qt.Key_S:
            self.set_pose("standing")
        elif event.key() == QtCore.Qt.Key_R:
            self.set_pose("reading")
        elif event.key() == QtCore.Qt.Key_P:
            self.set_pose("piano")
        elif event.key() == QtCore.Qt.Key_D:
            self.set_pose("daydream")
        elif event.key() == QtCore.Qt.Key_I:
            self.return_idle()
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
