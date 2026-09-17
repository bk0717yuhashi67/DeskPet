"""数据面板用的自绘图表：横向条形图 / 迷你折线 / 24 小时热力条 / 环形占比。

不用 QtCharts —— 自绘更轻、风格也更好统一。
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .card import SUB, TITLE, VALUE, ui_font


def fmt_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    if h and m:
        return f"{h}h{m:02d}m"
    if h:
        return f"{h}h"
    if m:
        return f"{m}m"
    return f"{seconds}s"


class BarChart(QWidget):
    """横向条形图：应用时长排行。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.items: list[tuple[str, int, str]] = []   # (名称, 秒数, 颜色)
        self.setMinimumHeight(60)

    def set_items(self, items: list[tuple[str, int, str]]) -> None:
        self.items = items[:8]
        self.setMinimumHeight(max(60, 26 * len(self.items) + 8))
        self.updateGeometry()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if not self.items:
            p.setFont(ui_font(9))
            p.setPen(QPen(SUB))
            p.drawText(self.rect(), Qt.AlignCenter, "今天还没有数据")
            p.end()
            return

        top = max(1, max(v for _n, v, _c in self.items))
        row_h = 26
        label_w = 108
        val_w = 58
        bar_x = label_w + 8
        bar_w = max(40, self.width() - label_w - val_w - 16)

        for i, (name, secs, color) in enumerate(self.items):
            y = 4 + i * row_h
            p.setFont(ui_font(9))
            p.setPen(QPen(VALUE))
            fm = p.fontMetrics()
            p.drawText(
                QRectF(0, y, label_w, row_h - 6),
                Qt.AlignLeft | Qt.AlignVCenter,
                fm.elidedText(name, Qt.ElideRight, int(label_w)),
            )

            track = QRectF(bar_x, y + 5, bar_w, row_h - 14)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor("#F0F4FA")))
            p.drawRoundedRect(track, 4, 4)

            w = track.width() * (secs / top)
            if w > 1:
                grad = QLinearGradient(track.topLeft(), track.topRight())
                c = QColor(color)
                grad.setColorAt(0.0, c)
                c2 = QColor(color)
                c2.setAlpha(180)
                grad.setColorAt(1.0, c2)
                p.setBrush(QBrush(grad))
                p.drawRoundedRect(QRectF(track.x(), track.y(), w, track.height()), 4, 4)

            p.setFont(ui_font(9))
            p.setPen(QPen(SUB))
            p.drawText(
                QRectF(self.width() - val_w, y, val_w - 2, row_h - 6),
                Qt.AlignRight | Qt.AlignVCenter,
                fmt_duration(secs),
            )
        p.end()


class Sparkline(QWidget):
    """7 日趋势迷你折线。"""

    def __init__(self, parent=None, color: str = "#4C8DFF") -> None:
        super().__init__(parent)
        self.values: list[float] = []
        self.labels: list[str] = []
        self.color = QColor(color)
        self.suffix = ""
        self.setMinimumHeight(76)

    def set_values(self, values: list[float], labels: list[str], suffix: str = "") -> None:
        self.values = list(values)
        self.labels = list(labels)
        self.suffix = suffix
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        pad_l, pad_r, pad_t, pad_b = 6, 6, 12, 20
        plot = QRectF(pad_l, pad_t, max(1, w - pad_l - pad_r), max(1, h - pad_t - pad_b))

        if len(self.values) < 2 or max(self.values) <= 0:
            p.setFont(ui_font(9))
            p.setPen(QPen(SUB))
            p.drawText(self.rect(), Qt.AlignCenter, "数据还不够，多用几天就有趋势啦")
            p.end()
            return

        vmax = max(self.values)
        n = len(self.values)
        pts = []
        for i, v in enumerate(self.values):
            x = plot.left() + plot.width() * (i / (n - 1))
            y = plot.bottom() - plot.height() * (v / vmax if vmax else 0)
            pts.append(QPointF(x, y))

        # 面积
        area = QPainterPath()
        area.moveTo(pts[0].x(), plot.bottom())
        for pt in pts:
            area.lineTo(pt)
        area.lineTo(pts[-1].x(), plot.bottom())
        area.closeSubpath()
        grad = QLinearGradient(plot.topLeft(), plot.bottomLeft())
        c = QColor(self.color)
        c.setAlpha(70)
        grad.setColorAt(0.0, c)
        c2 = QColor(self.color)
        c2.setAlpha(6)
        grad.setColorAt(1.0, c2)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawPath(area)

        # 折线
        line = QPainterPath(pts[0])
        for pt in pts[1:]:
            line.lineTo(pt)
        p.setPen(QPen(self.color, 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(line)

        # 数据点 + 末点数值
        p.setBrush(QBrush(QColor("#FFFFFF")))
        p.setPen(QPen(self.color, 1.8))
        for pt in pts:
            p.drawEllipse(pt, 2.4, 2.4)

        p.setFont(ui_font(8, bold=True))
        p.setPen(QPen(VALUE))
        last = pts[-1]
        text = f"{vmax:.0f}{self.suffix}"
        fm = p.fontMetrics()
        p.drawText(
            QRectF(last.x() - fm.horizontalAdvance(text) - 6, last.y() - 16,
                   fm.horizontalAdvance(text) + 6, 14),
            Qt.AlignRight | Qt.AlignVCenter,
            text,
        )

        # 横轴标签
        p.setFont(ui_font(8))
        p.setPen(QPen(SUB))
        if self.labels:
            step = max(1, n // 4)
            for i in range(0, n, step):
                x = plot.left() + plot.width() * (i / (n - 1))
                fm = p.fontMetrics()
                lab = self.labels[i]
                p.drawText(
                    QRectF(x - 22, plot.bottom() + 3, 44, 14),
                    Qt.AlignHCenter | Qt.AlignVCenter,
                    lab,
                )
        p.end()


class HeatStrip(QWidget):
    """24 小时活跃热力条：一眼看出作息分布。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.values: list[float] = [0.0] * 24     # 0..1
        self.setMinimumHeight(46)

    def set_values(self, values: list[float]) -> None:
        v = list(values)[:24]
        while len(v) < 24:
            v.append(0.0)
        self.values = v
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w = self.width()
        cell_w = w / 24.0
        top = 4
        bar_h = max(14, self.height() - 22)

        for i, v in enumerate(self.values):
            v = max(0.0, min(1.0, v))
            x = i * cell_w
            rect = QRectF(x + 1.0, top, cell_w - 2.0, bar_h)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor("#F0F4FA")))
            p.drawRoundedRect(rect, 3, 3)
            if v > 0.02:
                c = QColor("#4C8DFF") if v < 0.72 else QColor("#FF9A4D")
                c.setAlpha(int(90 + 165 * v))
                fh = max(3.0, bar_h * v)
                p.setBrush(QBrush(c))
                p.drawRoundedRect(
                    QRectF(rect.x(), rect.y() + (bar_h - fh), rect.width(), fh), 3, 3
                )

        p.setFont(ui_font(8))
        p.setPen(QPen(SUB))
        for hour in (0, 6, 12, 18, 23):
            x = hour * cell_w + cell_w / 2
            p.drawText(
                QRectF(x - 16, top + bar_h + 2, 32, 14),
                Qt.AlignHCenter | Qt.AlignVCenter,
                f"{hour}",
            )
        p.end()


class Donut(QWidget):
    """环形占比：应用分类构成。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.items: list[tuple[str, int, str]] = []
        self.center_text = ""
        self.setMinimumHeight(120)

    def set_items(self, items: list[tuple[str, int, str]], center: str = "") -> None:
        self.items = [(n, v, c) for n, v, c in items if v > 0]
        self.center_text = center
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        total = sum(v for _n, v, _c in self.items)
        side = min(self.width(), self.height() - 4)
        cx = side / 2 + 4
        cy = self.height() / 2
        r = side / 2 - 10
        thickness = max(11.0, r * 0.34)

        if total <= 0:
            p.setPen(QPen(QColor("#EDF1F8"), thickness))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), r, r)
            p.setFont(ui_font(9))
            p.setPen(QPen(SUB))
            p.drawText(self.rect(), Qt.AlignCenter, "暂无")
            p.end()
            return

        start = 90 * 16
        for _name, val, color in self.items:
            span = int(-360 * 16 * (val / total))
            p.setPen(QPen(QColor(color), thickness, Qt.SolidLine, Qt.FlatCap))
            p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2), start, span)
            start += span

        p.setFont(ui_font(12, bold=True))
        p.setPen(QPen(VALUE))
        p.drawText(
            QRectF(cx - r, cy - 12, r * 2, 20), Qt.AlignCenter, self.center_text
        )
        p.setFont(ui_font(8))
        p.setPen(QPen(TITLE))
        p.drawText(QRectF(cx - r, cy + 7, r * 2, 16), Qt.AlignCenter, "总时长")
        p.end()


class Legend(QWidget):
    """颜色图例。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.items: list[tuple[str, int, str]] = []
        self.setMinimumHeight(24)

    def set_items(self, items: list[tuple[str, int, str]]) -> None:
        self.items = [(n, v, c) for n, v, c in items if v > 0]
        rows = max(1, (len(self.items) + 2) // 3)
        self.setMinimumHeight(rows * 20 + 4)
        self.updateGeometry()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setFont(ui_font(8))
        col_w = max(80, self.width() // 3)
        for i, (name, secs, color) in enumerate(self.items):
            cx = (i % 3) * col_w
            cy = (i // 3) * 20 + 4
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(color)))
            p.drawRoundedRect(QRectF(cx, cy + 4, 8, 8), 2, 2)
            p.setPen(QPen(SUB))
            p.drawText(
                QRectF(cx + 13, cy, col_w - 16, 17),
                Qt.AlignLeft | Qt.AlignVCenter,
                f"{name} {fmt_duration(secs)}",
            )
        p.end()
