"""抛球游戏的透明覆盖层。

全虚拟屏尺寸、置顶、但 `WA_TransparentForMouseEvents`（等价
WS_EX_TRANSPARENT|WS_EX_LAYERED）——它永远不吞点击，所以桌面操作完全不受影响。
玩家的输入来自全局鼠标钩子，不依赖这个窗口。

性能要点：每帧只重绘"球 + 拖尾 + 落点虚影 + 飘字"的包围盒，不整窗重绘。
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from . import physics

# 沙滩皮球：6 瓣、白/红/黄/蓝（白多色少，才像街边那种塑料玩具球）
BEACH_PANELS = (
    QColor("#FAFCFF"),
    QColor("#E8524B"),
    QColor("#F2C33C"),
    QColor("#FAFCFF"),
    QColor("#3E8FE0"),
    QColor("#FAFCFF"),
)
BALL_EDGE = QColor(20, 32, 52, 180)
BALL_SEAM = QColor(22, 34, 54, 62)
TRAIL_COL = QColor(140, 190, 240)
SHADOW_COL = QColor(0, 0, 0, 46)
HUD_TEXT = QColor("#22314A")
HEART = QColor("#FF6B81")
HEART_EMPTY = QColor(255, 107, 129, 60)
RING_COL = QColor(255, 200, 90)
TOUCH_HINT = QColor(255, 255, 255, 165)
TOUCH_FILL = QColor(255, 255, 255, 34)

# 复刻 throw_game 的状态字符串。不能 import：throw_game 反过来依赖本模块，
# 直接导入会成环。
PHASE_FREE = "free"


def draw_beach_ball(p: QPainter, cx: float, cy: float, r: float, rot: float = 0.0) -> None:
    """画一颗沙滩皮球。rot 是自旋角度（度）。"""
    r = max(2.0, float(r))
    p.save()
    p.translate(cx, cy)
    p.rotate(rot)

    rect = QRectF(-r, -r, r * 2.0, r * 2.0)
    p.setPen(Qt.NoPen)
    for i, col in enumerate(BEACH_PANELS):
        p.setBrush(QBrush(col))
        p.drawPie(rect, int(i * 60 * 16), int(60 * 16))

    # 分缝
    p.setPen(QPen(BALL_SEAM, max(0.9, r * 0.075)))
    for i in range(len(BEACH_PANELS)):
        a = math.radians(i * 60.0)
        p.drawLine(QPointF(0.0, 0.0), QPointF(math.cos(a) * r, math.sin(a) * r))

    # 高光：从左上扫过来的软亮面，塑料感全靠它
    p.setPen(Qt.NoPen)
    grad = QRadialGradient(QPointF(-r * 0.34, -r * 0.38), r * 1.12)
    grad.setColorAt(0.0, QColor(255, 255, 255, 210))
    grad.setColorAt(0.52, QColor(255, 255, 255, 42))
    grad.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setBrush(QBrush(grad))
    p.drawEllipse(QPointF(0.0, 0.0), r, r)
    p.restore()

    # 外描边
    p.setPen(QPen(BALL_EDGE, max(1.2, r * 0.085)))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(cx, cy), r, r)


def _font(size: int, bold: bool = True) -> QFont:
    f = QFont("Microsoft YaHei UI", size)
    f.setFamilies(["Microsoft YaHei UI", "微软雅黑", "Segoe UI", "sans-serif"])
    f.setBold(bold)
    return f


class GameOverlay(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(
            parent,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.game = None      # ThrowGame，绘制时读取
        self._origin = (0.0, 0.0)

    # ------------------------------------------------------------ 生命周期
    def show_over(self, rect: QRect) -> None:
        self.setGeometry(rect)
        self._origin = (float(rect.x()), float(rect.y()))
        self.show()
        self.raise_()

    def to_local(self, x: float, y: float) -> tuple[float, float]:
        return (x - self._origin[0], y - self._origin[1])

    def to_screen(self, x: float, y: float) -> tuple[float, float]:
        return (x + self._origin[0], y + self._origin[1])

    # ------------------------------------------------------------ 重绘范围
    def refresh(self, ball: physics.Ball | None, extra: QRect | None = None) -> None:
        rect = QRect()
        if ball is not None and ball.alive:
            r = int(ball.r) + 26
            bx, by = self.to_local(ball.x, ball.y)
            rect = QRect(int(bx) - r, int(by) - r, r * 2, r * 2)
            if ball.trail:
                xs = [self.to_local(t[0], t[1])[0] for t in ball.trail]
                ys = [self.to_local(t[0], t[1])[1] for t in ball.trail]
                rect = rect.united(
                    QRect(int(min(xs)) - r, int(min(ys)) - r,
                          int(max(xs) - min(xs)) + r * 2, int(max(ys) - min(ys)) + r * 2)
                )
        if extra is not None and not extra.isNull():
            rect = rect.united(extra) if not rect.isNull() else extra
        if rect.isNull():
            self.update()
        else:
            self.update(rect)

    def hud_rect(self) -> QRect:
        # 必须覆盖到爱心那一行（y≈120），否则只重绘"球"的时候爱心会残留
        return QRect(0, 0, self.width(), 152)

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, event) -> None:  # noqa: N802
        g = self.game
        if g is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        ball = g.ball
        if ball is not None and ball.alive:
            # 落点虚影
            if not ball.grounded and not ball.owner == "pet":
                lx = physics.predict_landing_x(ball, g.bounds)
                sxx, syy = self.to_local(lx, g.bounds.floor)
                w = 16 + 10 * min(1.0, abs(ball.x - lx) / 400.0)
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(SHADOW_COL))
                p.drawEllipse(QPointF(sxx, syy - 3), w, w * 0.28)

            # 拖尾
            n = len(ball.trail)
            p.setPen(Qt.NoPen)
            for i, (tx, ty) in enumerate(ball.trail):
                k = (i + 1) / max(1, n)
                col = QColor(TRAIL_COL)
                col.setAlpha(int(88 * k * k))
                p.setBrush(QBrush(col))
                rr = ball.r * (0.26 + 0.52 * k)
                lx, ly = self.to_local(tx, ty)
                p.drawEllipse(QPointF(lx, ly), rr, rr)

            # 球本体（沙滩皮球，带自旋）
            bx, by = self.to_local(ball.x, ball.y)
            draw_beach_ball(p, bx, by, ball.r, ball.rot)

            # 该玩家出手了：给一圈会呼吸的虚线环 + 淡淡的填充，
            # 明确告诉玩家"把鼠标碰上去就行"（不用点）
            if g.phase == PHASE_FREE:
                k = 0.5 + 0.5 * math.sin(time.monotonic() * 4.2)
                rr = ball.r + 8.0 + 5.0 * k
                fill = QColor(TOUCH_FILL)
                fill.setAlpha(int(26 + 16 * k))
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(fill))
                p.drawEllipse(QPointF(bx, by), rr, rr)
                pen = QPen(TOUCH_HINT, max(1.6, ball.r * 0.11))
                pen.setStyle(Qt.DashLine)
                pen.setDashPattern([2.6, 2.0])
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(bx, by), rr, rr)

        # 扩散圆环（rings 里装的是 Ring 对象）
        for ring in g.rings:
            alpha = ring.alpha()
            if alpha <= 4:
                continue
            col = QColor(RING_COL)
            col.setAlpha(int(alpha))
            p.setPen(QPen(col, 3.0))
            p.setBrush(Qt.NoBrush)
            lx, ly = self.to_local(ring.x, ring.y)
            p.drawEllipse(QPointF(lx, ly), ring.r, ring.r * 0.82)

        # 飘字（popups 里装的是 Popup 对象，不是元组）。
        # 用"白字 + 深描边"：飘字会落在任意壁纸上，纯白字在浅色墙纸上看不见。
        p.setFont(_font(11))
        for item in g.popups:
            alpha = item.alpha()
            if alpha <= 4:
                continue
            fm = p.fontMetrics()
            pos = self.to_local(item.x, item.y)
            lx = pos[0] - fm.horizontalAdvance(item.text) / 2.0
            col = QColor("#FFFFFF")
            col.setAlpha(int(alpha))
            edge = QColor(24, 34, 54)
            edge.setAlpha(int(alpha * 0.85))
            self._hud_text(
                p, QRect(int(lx), int(pos[1] - fm.ascent()), fm.horizontalAdvance(item.text), 20),
                Qt.AlignLeft, item.text, col, halo=edge, halo_w=3.2,
            )

        self._draw_hud(p, g)
        p.end()

    def _hud_text(
        self,
        p: QPainter,
        rect: QRect,
        align,
        text: str,
        fill: QColor,
        halo: QColor | None = None,
        halo_w: float | None = None,
    ) -> None:
        """带一圈描边的 HUD 文字。

        HUD 是直接画在桌面上的（覆盖层透明），壁纸什么颜色都可能。
        原来"得分"两个字用白色 alpha=210，浅色壁纸上基本看不见——
        所以统一改成"深色字 + 白色描边"，深浅壁纸都能读。
        """
        from PySide6.QtGui import QPainterPath

        fm = p.fontMetrics()
        w = fm.horizontalAdvance(text)
        x = rect.x()
        if align & Qt.AlignHCenter:
            x = rect.x() + (rect.width() - w) / 2.0
        elif align & Qt.AlignRight:
            x = rect.right() - w
        baseline = rect.y() + (rect.height() + fm.ascent() - fm.descent()) / 2.0

        path = QPainterPath()
        path.addText(QPointF(x, baseline), p.font(), text)

        halo_col = halo if halo is not None else QColor(255, 255, 255, 165)
        width = halo_w if halo_w is not None else max(2.5, p.font().pointSizeF() * 0.30)
        pen = QPen(halo_col, width)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(fill))
        p.drawPath(path)

    def _draw_hud(self, p: QPainter, g) -> None:
        # 分数 / 连击
        p.setFont(_font(13))
        self._hud_text(p, QRect(0, 12, self.width(), 26), Qt.AlignHCenter,
                       "得分", QColor("#3A4A66"))
        p.setFont(_font(21))
        self._hud_text(p, QRect(0, 34, self.width(), 32), Qt.AlignHCenter,
                       str(g.score), HUD_TEXT)
        if g.combo >= 2:
            p.setFont(_font(11))
            self._hud_text(p, QRect(0, 66, self.width(), 22), Qt.AlignHCenter,
                           f"连击 x{g.combo}", QColor("#D2601A"))

        # 生命（爱心）
        p.setPen(Qt.NoPen)
        total_w = g.max_lives * 26
        start = self.width() // 2 - total_w // 2 + 13
        y = 104
        for i in range(g.max_lives):
            alive = i < g.lives
            p.setBrush(QBrush(HEART if alive else HEART_EMPTY))
            self._heart(p, QPointF(start + i * 26, y), 9.0)

        # 操作提示：触碰式玩法不写在屏幕上，玩家根本猜不到
        p.setFont(_font(12))
        tip = ("把鼠标移上去，碰到皮球就扔出去（不用点）"
               if g.phase == PHASE_FREE else "看准落点——球一落地就只剩 1.5 秒")
        self._hud_text(p, QRect(0, 124, self.width(), 24), Qt.AlignHCenter,
                       tip, QColor(34, 49, 74))

    def _heart(self, p: QPainter, c: QPointF, r: float) -> None:
        path = QPainterPath()
        path.moveTo(c.x(), c.y() + r * 0.85)
        path.cubicTo(
            c.x() - r * 1.5, c.y() - r * 0.35,
            c.x() - r * 0.5, c.y() - r * 1.3,
            c.x(), c.y() - r * 0.45,
        )
        path.cubicTo(
            c.x() + r * 0.5, c.y() - r * 1.3,
            c.x() + r * 1.5, c.y() - r * 0.35,
            c.x(), c.y() + r * 0.85,
        )
        path.closeSubpath()
        p.drawPath(path)
