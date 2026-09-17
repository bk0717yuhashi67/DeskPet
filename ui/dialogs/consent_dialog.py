"""首次运行的隐私同意弹窗。必须让用户清楚知道"采集什么、不采集什么、存哪里"。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ui.style import DIALOG_QSS, center_on_screen
from app.paths import DATA_DIR

CHOICE_FULL = 2
CHOICE_BASIC = 3

POINTS = [
    ("只存在你自己的电脑上",
     "所有统计都写进本机的一个数据文件，程序不联网，也没有任何上传代码。"),
    ("只数次数，不记内容",
     "键盘只统计「按了多少下」「退格多少次」和时间间隔；不取字符、不取密码、不截图。"),
    ("随时可以喊停",
     "托盘右键有「暂停统计」，设置里可以一键清空全部数据，也可以关掉窗口标题记录。"),
    ("为什么要这些数据",
     "企鹅靠它判断你是不是在长时间硬撑、是不是反复改同一段东西、是不是该歇一会儿，"
     "然后才决定要不要开口——没有数据，它就只能傻站着。"),
]


class ConsentDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("桌面企鹅 · 首次使用")
        self.setStyleSheet(DIALOG_QSS)
        self.setMinimumWidth(560)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 18)
        root.setSpacing(12)

        title = QLabel("先跟你说清楚我要看什么")
        title.setObjectName("H1")
        root.addWidget(title)

        desc = QLabel(
            "我想更好地陪着你，所以需要知道一点点你的电脑使用状态。"
            "下面四条是全部，不多要。"
        )
        desc.setWordWrap(True)
        desc.setObjectName("Hint")
        root.addWidget(desc)

        for head, body in POINTS:
            box = QFrame()
            box.setStyleSheet(
                "QFrame { background:#FFFFFF; border:1px solid #E4EAF4; border-radius:10px; }"
            )
            bl = QVBoxLayout(box)
            bl.setContentsMargins(13, 10, 13, 10)
            bl.setSpacing(3)
            h = QLabel(head)
            h.setObjectName("H2")
            b = QLabel(body)
            b.setWordWrap(True)
            b.setObjectName("Hint")
            bl.addWidget(h)
            bl.addWidget(b)
            root.addWidget(box)

        path = QLabel(f"数据文件位置：{DATA_DIR}")
        path.setObjectName("Hint")
        path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(path)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_basic = QPushButton("仅用基础功能（不采集输入）")
        self.btn_basic.clicked.connect(lambda: self.done(CHOICE_BASIC))
        self.btn_full = QPushButton("我同意")
        self.btn_full.setDefault(True)
        self.btn_full.clicked.connect(lambda: self.done(CHOICE_FULL))
        btns.addWidget(self.btn_basic)
        btns.addWidget(self.btn_full)
        root.addLayout(btns)

        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        center_on_screen(self)
