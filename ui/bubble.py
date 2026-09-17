"""聊天气泡：独立的透明无边框窗口。

为什么不做在主窗口里：气泡最宽 240px、最高 90px，会超出 200x200 的企鹅画布；
同窗绘制就得放大窗口，又要同步扩大"反向命中排除"逻辑，耦合严重。
独立窗口还让淡入淡出（windowOpacity）和跟随移动都变得非常简单。
"""
from __future__ import annotations

import html as _html

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
    QTextDocument,
)
from PySide6.QtWidgets import QWidget

PAD = 13
RADIUS = 13
TAIL_W = 16
TAIL_H = 9
MAX_TEXT_W = 226
BTN_H = 30
BTN_W = 62

BG = QColor(255, 255, 255, 250)
BORDER = QColor("#D9E0EC")
TEXT = QColor("#1F2A3D")
SHADOW = QColor(20, 32, 56, 26)
BTN_BG = QColor("#F0F4FA")
BTN_BG_HOVER = QColor("#E3EBF8")
BTN_TEXT = QColor("#3A4A63")
BTN_TEXT_HOVER = QColor("#1B2A4A")
ACCENT = QColor("#2B6FE0")

FONT_FAMILY = "Microsoft YaHei UI, Microsoft YaHei, Segoe UI, sans-serif"


def _font(size: int = 10, bold: bool = False) -> QFont:
    f = QFont("Microsoft YaHei UI", size)
    f.setFamilies(["Microsoft YaHei UI", "微软雅黑", "Segoe UI", "sans-serif"])
    f.setBold(bold)
    f.setHintingPreference(QFont.PreferFullHinting)
    return f


class Bubble(QWidget):
    """同一时刻只有一个气泡。show_message 会复用本窗口。"""

    ack = Signal()          # 用户点了"知道了"
    snooze = Signal()       # 用户点了"稍后"
    closed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(
            parent,
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setMouseTracking(True)

        self._text = ""
        self._doc = QTextDocument()
        self._doc.setDefaultFont(_font())
        self._content_w = MAX_TEXT_W
        self._content_h = 40

        self._tail_up = False
        self._buttons = False
        self._hover_btn: str | None = None

        self._opacity = 0.0
        self._phase = "idle"     # idle | in | hold | out
        self._remain_ms = 0
        self._hover = False
        self.setWindowOpacity(0.0)

        self._timer = QTimer(self)
        self._timer.setInterval(28)
        self._timer.timeout.connect(self._on_tick)

        self._anchor = QPoint(0, 0)

    # ==================================================================
    # 对外接口
    # ==================================================================
    def show_message(
        self,
        text: str,
        anchor: QPoint,
        duration_ms: int | None = None,
        buttons: bool = False,
        sticky: bool = False,
    ) -> None:
        """anchor 是企鹅头顶的屏幕坐标；气泡尾巴会指向它。"""
        self._text = text or ""
        self._buttons = buttons
        self._anchor = QPoint(anchor)

        self._doc.setHtml(
            f'<div style="color:{TEXT.name()};line-height:150%;'
            f'font-family:{FONT_FAMILY};">{_html.escape(self._text)}</div>'
        )
        self._doc.setTextWidth(MAX_TEXT_W)
        size = self._doc.size()
        self._content_w = max(90.0, float(size.width()))
        self._content_h = max(18.0, float(size.height()))

        if duration_ms is None:
            duration_ms = int(max(2500, min(9000, 1500 + 55 * len(self._text))))
        self._remain_ms = 0 if (sticky or buttons) else duration_ms

        self._layout_at(anchor, sticky)
        self._phase = "in"
        self._opacity = 0.0
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._timer.start()

    def hide_now(self) -> None:
        self._phase = "out"

    def is_busy(self) -> bool:
        return self.isVisible()

    def move_to(self, anchor: QPoint) -> None:
        """企鹅移动时跟随。"""
        if not self.isVisible():
            return
        self._anchor = QPoint(anchor)
        self._layout_at(anchor, self._phase == "hold" and self._remain_ms == 0)

    # ==================================================================
    # 布局
    # ==================================================================
    def _bubble_size(self) -> tuple[int, int]:
        w = int(self._content_w) + 2 * PAD
        h = int(self._content_h) + 2 * PAD + (BTN_H + 4 if self._buttons else 0)
        return max(96, w), max(44, h)

    def _layout_at(self, anchor: QPoint, sticky: bool) -> None:
        bw, bh = self._bubble_size()
        total_h = bh + TAIL_H
        self.resize(bw, total_h)

        scr = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        av = scr.availableGeometry() if scr else QRect(0, 0, 1920, 1080)

        upward = anchor.y() - total_h >= av.top() + 4
        self._tail_up = not upward
        if upward:
            y = anchor.y() - total_h
        else:
            y = anchor.y() + 12

        # 尾巴尽量对准锚点，但别跑出圆角矩形
        tail_x = bw // 2
        x = anchor.x() - tail_x
        if x < av.left() + 4:
            x = av.left() + 4
        if x + bw > av.right() - 4:
            x = av.right() - 4 - bw
        self._tail_x = max(RADIUS + TAIL_W // 2, min(bw - RADIUS - TAIL_W // 2,
                                                    anchor.x() - x))
        self.move(int(x), int(y))

    # ==================================================================
    # 计时 / 淡入淡出
    # ==================================================================
    def _on_tick(self) -> None:
        step = 0.14
        if self._phase == "in":
            self._opacity = min(1.0, self._opacity + step)
            if self._opacity >= 1.0:
                self._phase = "hold"
        elif self._phase == "hold":
            if not self._hover and self._remain_ms > 0:
                self._remain_ms -= self._timer.interval()
                if self._remain_ms <= 0:
                    self._phase = "out"
        elif self._phase == "out":
            self._opacity = max(0.0, self._opacity - step * 0.85)
            if self._opacity <= 0.0:
                self._timer.stop()
                self.hide()
                self.setWindowOpacity(0.0)
                self.closed.emit()
                return
        self.setWindowOpacity(self._opacity)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self._hover_btn = None
        self.update()
        super().leaveEvent(event)

    # ==================================================================
    # 绘制
    # ==================================================================
    def _body_rect(self) -> QRectF:
        if self._tail_up:
            return QRectF(0, TAIL_H, self.width(), self.height() - TAIL_H)
        return QRectF(0, 0, self.width(), self.height() - TAIL_H)

    def _btn_rects(self) -> dict[str, QRectF]:
        if not self._buttons:
            return {}
        body = self._body_rect()
        y = body.bottom() - PAD - BTN_H + 4
        ack = QRectF(body.right() - PAD - BTN_W, y, BTN_W, BTN_H)
        snooze = QRectF(ack.left() - 8 - BTN_W, y, BTN_W, BTN_H)
        return {"ack": ack, "snooze": snooze}

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        body = self._body_rect()
        path = QPainterPath()
        path.addRoundedRect(body, RADIUS, RADIUS)

        # 尾巴
        tip_y = body.top() - TAIL_H if self._tail_up else body.bottom() + TAIL_H
        base_y = body.top() if self._tail_up else body.bottom()
        tx = self._tail_x
        tail = QPainterPath()
        tail.moveTo(tx - TAIL_W / 2, base_y)
        tail.lineTo(tx, tip_y)
        tail.lineTo(tx + TAIL_W / 2, base_y)
        tail.closeSubpath()
        full = path.united(tail)

        # 三层递进 alpha 的伪阴影
        for grow, alpha in ((7, 10), (4, 16), (2, 22)):
            col = QColor(SHADOW)
            col.setAlpha(alpha)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(col))
            sp = QPainterPath()
            sp.addRoundedRect(body.adjusted(-grow, -grow + 1, grow, grow + 1),
                              RADIUS + grow, RADIUS + grow)
            if self._tail_up:
                t = QPainterPath()
                t.moveTo(tx - TAIL_W / 2 - grow, base_y)
                t.lineTo(tx, tip_y - grow)
                t.lineTo(tx + TAIL_W / 2 + grow, base_y)
                t.closeSubpath()
                sp = sp.united(t)
            p.drawPath(sp)

        p.setPen(QPen(BORDER, 1.2))
        p.setBrush(QBrush(BG))
        p.drawPath(full)

        # 文本
        p.setPen(QPen(TEXT))
        p.translate(PAD, PAD + (TAIL_H if self._tail_up else 0))
        ctx = p.transform()
        self._doc.drawContents(p)
        p.setTransform(ctx)

        # 按钮
        for name, rect in self._btn_rects().items():
            hovered = self._hover_btn == name
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(BTN_BG_HOVER if hovered else BTN_BG))
            p.drawRoundedRect(rect, 8, 8)
            label = "知道啦" if name == "ack" else "稍后"
            p.setFont(_font(9, bold=hovered))
            p.setPen(QPen(BTN_TEXT_HOVER if hovered else BTN_TEXT))
            p.drawText(rect, Qt.AlignCenter, label)
        p.end()

    # ==================================================================
    # 交互
    # ==================================================================
    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        hit = None
        for name, rect in self._btn_rects().items():
            if rect.contains(pos):
                hit = name
                break
        if hit != self._hover_btn:
            self._hover_btn = hit
            self.update()
        self.setCursor(Qt.PointingHandCursor if hit else Qt.ArrowCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        for name, rect in self._btn_rects().items():
            if rect.contains(pos):
                if name == "ack":
                    self.ack.emit()
                else:
                    self.snooze.emit()
                self.hide_now()
                return
        # 点空白处：提前收起
        self.hide_now()
