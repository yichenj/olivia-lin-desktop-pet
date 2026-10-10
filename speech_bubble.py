"""Content-sized oval speech bubble, owned by the pet's UI process."""
import math
import time
from PyQt5 import QtCore, QtGui, QtWidgets


class SpeechBubble(QtWidgets.QWidget):
    MIN_WIDTH, MIN_HEIGHT = 140, 88
    MAX_WIDTH, MAX_HEIGHT = 360, 320
    TEXT_WIDTH_RATIO, TEXT_HEIGHT_RATIO = .76, .58

    def __init__(self, owner, auto_hide_seconds=30):
        super().__init__(owner, QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.NoDropShadowWindowHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("Olivia 的回复")
        self.owner = owner
        self.tail_right = True
        self.streaming = False
        self.dismissed = False
        self.manually_dismissed = False
        self.pending_display = None
        self.content = ""
        self.auto_hide_seconds = auto_hide_seconds
        self.remaining = float(auto_hide_seconds)
        self.last_tick = time.monotonic()
        self.text = QtWidgets.QTextBrowser(self)
        self.text.setAcceptRichText(False)
        self.text.setOpenExternalLinks(False)
        font = QtGui.QFont()
        font.setPixelSize(14)
        self.text.setFont(font)
        self.text.document().setDocumentMargin(0)
        self.text.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.text.setStyleSheet("""
            QTextBrowser { border:0; padding:0; background:transparent; color:#382e30; }
            QScrollBar:vertical { width:5px; margin:0; background:transparent; }
            QScrollBar::handle:vertical { background:#c2afa3; border-radius:2px; min-height:18px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
        """)
        self.text.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.text.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.text.setWordWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.text.setAccessibleName("Olivia 的聊天回复")
        self.setFixedSize(self.MIN_WIDTH, self.MIN_HEIGHT)
        self._layout_content()
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(160)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.text.verticalScrollBar().valueChanged.connect(self.reading_activity)
        self.text.selectionChanged.connect(self.reading_activity)
        self.hide()

    def body_rect(self):
        return QtCore.QRectF(12, 2, self.width() - 24, self.height() - 4)

    def _layout_content(self):
        body = self.body_rect()
        height = min(round(body.height() * self.TEXT_HEIGHT_RATIO),
                     getattr(self, "_text_natural_height", round(body.height() * self.TEXT_HEIGHT_RATIO)))
        self.text.setGeometry(round(body.left() + body.width() * .12),
                              round(body.center().y() - height / 2 + body.height() * .01),
                              round(body.width() * self.TEXT_WIDTH_RATIO),
                              height)

    def _fit_content(self):
        if not self.content:
            self._text_natural_height = self.MIN_HEIGHT
            self.setFixedSize(self.MIN_WIDTH, self.MIN_HEIGHT)
            self._layout_content()
            return
        metrics = QtGui.QFontMetricsF(self.text.font())
        longest = max((metrics.horizontalAdvance(line) for line in self.content.splitlines()), default=0)
        area = max(1, metrics.horizontalAdvance(self.content.replace("\n", ""))) * metrics.lineSpacing()
        max_text_width = (self.MAX_WIDTH - 24) * self.TEXT_WIDTH_RATIO
        target_width = min(max_text_width, max(72, min(longest + 8, math.sqrt(area * 1.8))))
        width = min(self.MAX_WIDTH, max(self.MIN_WIDTH, math.ceil(target_width / self.TEXT_WIDTH_RATIO) + 24))
        # Grow smoothly through a reply, and reset for each new reply/steer.
        width = max(self.width(), width)
        measure = QtGui.QTextDocument()
        measure.setDefaultFont(self.text.font())
        measure.setDocumentMargin(0)
        option = measure.defaultTextOption()
        option.setWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)
        measure.setDefaultTextOption(option)
        measure.setPlainText(self.content)
        text_width = round((width - 24) * self.TEXT_WIDTH_RATIO)
        measure.setTextWidth(text_width)
        text_height = math.ceil(measure.size().height()) + 4
        height = max(self.MIN_HEIGHT, math.ceil(text_height / self.TEXT_HEIGHT_RATIO) + 4)
        # Use the available width before resorting to a scrollable long reply.
        if height > self.MAX_HEIGHT:
            width = self.MAX_WIDTH
            text_width = round((width - 24) * self.TEXT_WIDTH_RATIO)
            measure.setTextWidth(text_width)
            text_height = math.ceil(measure.size().height()) + 4
            height = max(self.MIN_HEIGHT, math.ceil(text_height / self.TEXT_HEIGHT_RATIO) + 4)
        height = min(self.MAX_HEIGHT, max(self.height(), height))
        self._text_natural_height = text_height
        self.setFixedSize(width, height)
        self._layout_content()
        overflow = text_height > self.text.height()
        self.text.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded if overflow else QtCore.Qt.ScrollBarAlwaysOff)
        self.reposition()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        body = self.body_rect()
        path = QtGui.QPainterPath()
        path.addEllipse(body)
        tail = QtGui.QPainterPath()
        direction = 1 if self.tail_right else -1
        edge = body.right() if self.tail_right else body.left()
        cy = body.center().y()
        tail.moveTo(edge - direction * 12, cy - 11)
        tail.quadTo(edge + direction * 2, cy + 1, edge + direction * 11, cy + 8)
        tail.quadTo(edge, cy + 12, edge - direction * 12, cy + 11)
        tail.closeSubpath()
        painter.setBrush(QtGui.QColor("#fffaf2"))
        painter.setPen(QtGui.QPen(QtGui.QColor("#b89b90"), 1.3))
        painter.drawPath(path.united(tail))
        if self.streaming and not self.content:
            # A small conversational pause, without task controls or a status header.
            phase = int(time.monotonic() * 3) % 3
            painter.setPen(QtCore.Qt.NoPen)
            for index in range(3):
                painter.setBrush(QtGui.QColor("#9f8274" if index == phase else "#dac8bb"))
                painter.drawEllipse(QtCore.QPointF(body.center().x() + (index - 1) * 12, body.center().y()), 2.8, 2.8)

    def reposition(self):
        screen = self.owner.screen() or QtWidgets.QApplication.primaryScreen()
        if not screen:
            return
        area = screen.availableGeometry()
        head = self.owner.mapToGlobal(QtCore.QPoint(self.owner.width() // 2, 90))
        left = head.x() - 65 - self.width()
        right = head.x() + 65
        self.tail_right = left >= area.left() or right + self.width() > area.right()
        x = left if self.tail_right else right
        x = max(area.left(), min(x, area.right() - self.width() + 1))
        y = max(area.top(), min(head.y() - round(self.body_rect().center().y()) - 8, area.bottom() - self.height() + 1))
        self.move(x, y)
        self.update()

    def reveal(self):
        if self.pending_display is not None:
            self._show_pending(force=True)
        self.dismissed = False
        self.manually_dismissed = False
        self.remaining = float(self.auto_hide_seconds)
        self.last_tick = time.monotonic()
        self.sync_visibility()

    def sync_visibility(self):
        visible = bool(self.content or self.streaming) and not self.dismissed and self.owner.isVisible() and not self.owner.isMinimized()
        if visible:
            self.reposition()
            self.show()
            self.raise_()
        else:
            self.hide()

    def begin(self, gentle=False):
        if gentle and self.isVisible() and self.content and self.is_reading():
            self.pending_display = {'content': '', 'completion': None}
            return
        self.pending_display = None
        self.streaming = True
        self.content = ""
        self.text.clear()
        self.text.hide()
        self.text.setToolTip("")
        self.text.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._fit_content()
        if gentle and self.manually_dismissed:
            self.sync_visibility()
        else:
            self.reveal()

    def append(self, text):
        if not text:
            return
        if self.pending_display is not None:
            self.pending_display['content'] += text
            return
        self.content += text
        scrollbar = self.text.verticalScrollBar()
        following = scrollbar.value() >= scrollbar.maximum() - 3 and not self.text.textCursor().hasSelection()
        cursor = QtGui.QTextCursor(self.text.document())
        cursor.movePosition(QtGui.QTextCursor.End)
        cursor.insertText(text)
        self.text.show()
        self._fit_content()
        if following:
            scrollbar.setValue(scrollbar.maximum())

    def finish(self, params):
        if self.pending_display is not None:
            self.pending_display['completion'] = params
            return
        final = params.get("text", self.content)
        if final != self.content:
            self.content = final
            self.text.setPlainText(final)
        self.streaming = False
        message = params.get("error", {}).get("message", "")
        self.text.setToolTip(message)
        if not self.content and message:
            self.content = message
            self.text.setPlainText(message)
        self.text.show()
        self._fit_content()
        self.remaining = float(self.auto_hide_seconds)
        self.last_tick = time.monotonic()
        self.sync_visibility()

    def dismiss(self, manual=True):
        self.dismissed = True
        self.manually_dismissed = manual
        self.hide()

    def is_reading(self):
        scrollbar = self.text.verticalScrollBar()
        return (self.underMouse() or self.text.hasFocus() or self.text.textCursor().hasSelection()
                or scrollbar.value() < scrollbar.maximum() - 3)

    def _show_pending(self, force=False):
        pending = self.pending_display
        self.pending_display = None
        self.begin(gentle=not force)
        self.append(pending['content'])
        if pending['completion'] is not None:
            self.finish(pending['completion'])

    def reading_activity(self):
        self.remaining = float(self.auto_hide_seconds)

    def tick(self):
        if self.pending_display is not None and (not self.isVisible() or not self.is_reading()):
            self._show_pending()
        now = time.monotonic()
        elapsed = now - self.last_tick
        self.last_tick = now
        if self.streaming and not self.content:
            self.update()
        if not self.isVisible() or self.streaming:
            return
        if self.is_reading():
            self.remaining = float(self.auto_hide_seconds)
            return
        self.remaining -= elapsed
        if self.remaining <= 0:
            self.dismiss(manual=False)
