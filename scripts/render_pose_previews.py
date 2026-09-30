#!/usr/bin/env python3
"""Render pose contact sheets and real Qt window previews without changing sprites."""
import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5 import QtCore, QtGui, QtWidgets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
POSES = {"standing": "站立 · Standing", "reading": "读书 · Reading",
         "piano": "弹琴 · Piano", "daydream": "发呆 · Daydream"}


def sheet(source, output, before=None):
    width, height = (1440, 1080) if before else (1160, 1540)
    canvas = QtGui.QImage(width, height, QtGui.QImage.Format_ARGB32)
    canvas.fill(QtGui.QColor("#f8f5f0"))
    p = QtGui.QPainter(canvas)
    p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
    p.setPen(QtGui.QColor("#393333"))
    p.setFont(QtGui.QFont("Arial", 25))
    p.drawText(32, 45, "Olivia · Before / After" if before else "Olivia · Updated action poses")
    p.setFont(QtGui.QFont("Arial", 12))
    p.drawText(32, 73, "Dior Boy-inspired platform loafers · shorter denim shorts · slimmer leg contours")
    for i, (name, label) in enumerate(POSES.items()):
        x, y = ((i % 2) * 704 + 24, (i // 2) * 482 + 104) if before else ((i % 2) * 558 + 24, (i // 2) * 710 + 105)
        p.setPen(QtGui.QColor("#393333"))
        p.setFont(QtGui.QFont("Arial", 16))
        p.drawText(x + 8, y + 23, label)
        cells = [(before / f"{name}.png", "Before"), (source / f"{name}.png", "After")] if before else [(source / f"{name}.png", "")]
        for j, (path, caption) in enumerate(cells):
            cx, cy, cw, ch = (x + j * 340, y + 52, 328, 408) if before else (x, y + 40, 530, 640)
            for yy in range(cy, cy + ch, 18):
                for xx in range(cx, cx + cw, 18):
                    color = "#ebe8e2" if ((xx-cx)//18 + (yy-cy)//18) % 2 else "#d7d3cd"
                    p.fillRect(xx, yy, min(18, cx+cw-xx), min(18, cy+ch-yy), QtGui.QColor(color))
            pix = QtGui.QPixmap(str(path))
            if pix.isNull():
                raise ValueError(f"Cannot load {path}")
            size = pix.size().scaled(cw, ch, QtCore.Qt.KeepAspectRatio)
            p.drawPixmap(QtCore.QRect(cx+(cw-size.width())//2, cy, size.width(), size.height()), pix)
            if caption:
                p.setFont(QtGui.QFont("Arial", 11))
                p.drawText(cx + 8, cy - 6, caption)
    p.end()
    if not canvas.save(str(output)):
        raise OSError(f"Cannot save {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "assets/poses")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output/previews")
    parser.add_argument("--before-dir", type=Path)
    parser.add_argument("--windows", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    sheet(args.source, args.output_dir / "preview-action-poses.png")
    if args.before_dir:
        sheet(args.source, args.output_dir / "preview-pose-before-after.png", args.before_dir)
    if args.windows:
        import pet
        widget = pet.OliviaPet()
        widget.timer.stop()
        widget.phase = 0
        widget.poses = {name: QtGui.QPixmap(str(args.source / f"{name}.png")) for name in POSES}
        for name in POSES:
            widget.pose = name
            widget.update()
            app.processEvents()
            image = QtGui.QImage(pet.W, pet.H, QtGui.QImage.Format_ARGB32)
            image.fill(QtGui.QColor("#e3e5e8"))
            painter = QtGui.QPainter(image)
            painter.drawPixmap(0, 0, widget.grab())
            painter.end()
            image.save(str(args.output_dir / f"preview-window-{name}-final.png"))
        widget.close()


if __name__ == "__main__":
    main()
