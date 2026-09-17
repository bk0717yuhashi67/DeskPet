"""小游戏结算卡片。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.style import DIALOG_QSS, center_on_screen
from ui.widgets.card import ui_font


class ResultDialog(QDialog):
    def __init__(self, result: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("这局结束啦")
        self.setStyleSheet(DIALOG_QSS)
        self.setFixedWidth(360)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 16)
        root.setSpacing(10)

        head = QLabel("新纪录！" if result.get("new_record") else "这局结束啦")
        head.setObjectName("H1")
        root.addWidget(head)

        score = QLabel(str(result.get("score", 0)))
        score.setStyleSheet("font-size:40px;font-weight:700;color:#2B6FE0;")
        root.addWidget(score)

        sub = QLabel(f"最高纪录 {result.get('high_score', 0)} 分")
        sub.setObjectName("Hint")
        root.addWidget(sub)

        rows = (
            ("最高连击", f"x{result.get('best_combo', 0)}"),
            ("接到球", f"{result.get('hits', 0)} 次"),
            ("失误", f"{result.get('misses', 0)} 次"),
            ("命中率", f"{result.get('accuracy', 0) * 100:.0f}%"),
            ("用时", _fmt(int(result.get("duration_s", 0)))),
        )
        for label, value in rows:
            r = QHBoxLayout()
            left = QLabel(label)
            left.setStyleSheet("color:#7C8AA3;")
            right = QLabel(value)
            right.setStyleSheet("color:#1B2A4A;font-weight:600;")
            r.addWidget(left)
            r.addStretch(1)
            r.addWidget(right)
            wrap = QWidget()
            wrap.setLayout(r)
            root.addWidget(wrap)

        btns = QHBoxLayout()
        btns.addStretch(1)
        again = QPushButton("再玩一局")
        again.clicked.connect(lambda: self.done(2))
        ok = QPushButton("好")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        btns.addWidget(again)
        btns.addWidget(ok)
        root.addLayout(btns)
        center_on_screen(self)

    def wants_replay(self) -> bool:
        return self.result() == 2


def _fmt(seconds: int) -> str:
    m, s = divmod(max(0, int(seconds)), 60)
    return f"{m} 分 {s} 秒" if m else f"{s} 秒"
