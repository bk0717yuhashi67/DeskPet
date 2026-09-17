"""数据面板用的基础卡片控件（全部自绘，保持平面卡通风格一致）。"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

CARD_BG = QColor("#FFFFFF")
CARD_BORDER = QColor("#E4EAF4")
TITLE = QColor("#7C8AA3")
VALUE = QColor("#1B2A4A")
SUB = QColor("#93A0B6")
ACCENT = QColor("#2B6FE0")


def ui_font(size: int = 10, bold: bool = False) -> QFont:
    f = QFont("Microsoft YaHei UI", size)
    f.setFamilies(["Microsoft YaHei UI", "微软雅黑", "Segoe UI", "sans-serif"])
    f.setBold(bold)
    f.setHintingPreference(QFont.PreferFullHinting)
    return f


class Card(QWidget):
    """圆角白卡片容器，子控件用布局往里放。"""

    def __init__(self, parent=None, radius: int = 12) -> None:
        super().__init__(parent)
        self.radius = radius
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        shadow = QColor(20, 32, 56, 12)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(shadow))
        p.drawRoundedRect(rect.adjusted(0, 2, 0, 2), self.radius, self.radius)
        p.setPen(QPen(CARD_BORDER, 1.0))
        p.setBrush(QBrush(CARD_BG))
        p.drawRoundedRect(rect, self.radius, self.radius)
        p.end()


class MetricTile(QWidget):
    """一个指标：标题 + 大数字 + 一行说明 + 可选进度条。"""

    def __init__(self, title: str, parent=None, accent: QColor | None = None) -> None:
        super().__init__(parent)
        self.title = title
        self.value_text = "—"
        self.sub_text = ""
        self.ratio = 0.0
        self.accent = accent or ACCENT
        self.setMinimumHeight(84)

    def set_data(self, value: str, sub: str = "", ratio: float = 0.0) -> None:
        self.value_text = value
        self.sub_text = sub
        self.ratio = max(0.0, min(1.0, ratio))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        rect = QRectF(0.5, 0.5, w - 1, h - 1)
        p.setPen(QPen(CARD_BORDER, 1.0))
        p.setBrush(QBrush(QColor("#FBFCFE")))
        p.drawRoundedRect(rect, 11, 11)

        p.setFont(ui_font(9))
        p.setPen(QPen(TITLE))
        p.drawText(QRectF(13, 9, w - 26, 16), Qt.AlignLeft | Qt.AlignVCenter, self.title)

        p.setFont(ui_font(17, bold=True))
        p.setPen(QPen(VALUE))
        p.drawText(QRectF(12, 24, w - 24, 28), Qt.AlignLeft | Qt.AlignVCenter, self.value_text)

        # 进度条
        bar = QRectF(13, h - 21, w - 26, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor("#EDF1F8")))
        p.drawRoundedRect(bar, 3, 3)
        if self.ratio > 0:
            fill = QRectF(bar.x(), bar.y(), bar.width() * self.ratio, bar.height())
            col = QColor(self.accent)
            col.setAlpha(225)
            p.setBrush(QBrush(col))
            p.drawRoundedRect(fill, 3, 3)

        p.setFont(ui_font(8))
        p.setPen(QPen(SUB))
        p.drawText(
            QRectF(w - 140, h - 42, 127, 16),
            Qt.AlignRight | Qt.AlignVCenter,
            self.sub_text,
        )
        p.end()


class SectionTitle(QWidget):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self.text = text
        self.setFixedHeight(26)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        # 左侧小色条
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor("#2B6FE0")))
        p.drawRoundedRect(QRectF(0, self.height() / 2 - 6, 3, 12), 1.5, 1.5)
        p.setFont(ui_font(10, bold=True))
        p.setPen(QPen(VALUE))
        p.drawText(
            QRectF(12, 0, self.width() - 12, self.height()),
            Qt.AlignLeft | Qt.AlignVCenter,
            self.text,
        )
        p.end()


class Divider(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(1)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setPen(QPen(QColor("#EDF1F8"), 1))
        p.drawLine(0, 0, self.width(), 0)
        p.end()
