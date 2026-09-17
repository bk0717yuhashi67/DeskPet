"""界面共享样式。"""
from __future__ import annotations

DIALOG_QSS = """QDialog, QWidget#Panel { background: #F6F8FC; }

QLabel { color: #22314A; font-family: "Microsoft YaHei UI","微软雅黑",sans-serif; font-size: 12px; }
QLabel#H1 { font-size: 17px; font-weight: 600; }
QLabel#H2 { font-size: 13px; font-weight: 600; color: #2B3A55; }
QLabel#Hint { color: #8695AD; font-size: 11px; }

QPushButton {
    background: #FFFFFF; color: #22314A;
    border: 1px solid #DCE3EE; border-radius: 8px;
    padding: 6px 15px;
    font-family: "Microsoft YaHei UI","微软雅黑",sans-serif; font-size: 12px;
}
QPushButton:hover { background: #F2F7FF; border-color: #BFD4F2; }
QPushButton:pressed { background: #E7F0FF; }
QPushButton:default { background: #2B6FE0; color: #FFFFFF; border-color: #2B6FE0; }
QPushButton:default:hover { background: #3A7EEC; }
QPushButton:disabled { color: #A9B4C6; background: #F2F4F8; }

QLineEdit, QComboBox, QSpinBox, QTimeEdit, QDateEdit, QPlainTextEdit {
    background: #FFFFFF; border: 1px solid #DCE3EE; border-radius: 8px;
    padding: 5px 9px; color: #22314A;
    font-family: "Microsoft YaHei UI","微软雅黑",sans-serif; font-size: 12px;
    selection-background-color: #CFE0FF;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTimeEdit:focus, QPlainTextEdit:focus {
    border-color: #7FA9E8;
}
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background: #FFFFFF; border: 1px solid #DCE3EE;
    selection-background-color: #EAF0FA; selection-color: #10203A;
    outline: none; padding: 3px;
}

QListWidget {
    background: #FFFFFF; border: 1px solid #E4EAF4; border-radius: 10px;
    padding: 4px; outline: none;
    font-family: "Microsoft YaHei UI","微软雅黑",sans-serif; font-size: 12px;
}
QListWidget::item { padding: 7px 8px; border-radius: 7px; color: #2B3A55; }
QListWidget::item:selected { background: #EAF0FA; color: #10203A; }
QListWidget::item:hover { background: #F3F7FD; }

QCheckBox { color: #22314A; font-family: "Microsoft YaHei UI","微软雅黑",sans-serif; font-size: 12px; spacing: 6px; }
QCheckBox::indicator { width: 15px; height: 15px; border-radius: 4px; border: 1px solid #C6D2E4; background: #FFFFFF; }
QCheckBox::indicator:checked { background: #2B6FE0; border-color: #2B6FE0; }

QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 9px; margin: 2px; }
QScrollBar::handle:vertical { background: #D5DEEB; border-radius: 4px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: #C0CCDE; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar:horizontal { background: transparent; height: 9px; margin: 2px; }
QScrollBar::handle:horizontal { background: #D5DEEB; border-radius: 4px; min-width: 28px; }

QGroupBox {
    border: 1px solid #E4EAF4; border-radius: 10px; background: #FFFFFF;
    margin-top: 12px; padding: 12px 12px 8px 12px;
    font-family: "Microsoft YaHei UI","微软雅黑",sans-serif; font-size: 12px; font-weight: 600;
    color: #2B3A55;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }

QToolTip {
    background: #22314A; color: #FFFFFF; border: none;
    padding: 5px 8px; border-radius: 6px;
    font-family: "Microsoft YaHei UI","微软雅黑",sans-serif; font-size: 11px;
}
"""


def center_on_screen(widget, offset_y: int = 0) -> None:
    """把对话框摆到屏幕正中。

    没有父窗口时 Qt 默认会把对话框放在偏上的位置，第一眼看过去像"飘着的"。
    居中动作要延后到事件循环里执行——show 之前布局还没算好，height() 是不准的。
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QGuiApplication

    def _do() -> None:
        scr = QGuiApplication.primaryScreen()
        if scr is None:
            return
        av = scr.availableGeometry()
        w = max(widget.width(), widget.sizeHint().width())
        h = max(widget.height(), widget.sizeHint().height())
        x = av.center().x() - w // 2
        y = av.center().y() - h // 2 + offset_y
        x = max(av.left() + 12, min(x, av.right() - w - 12))
        y = max(av.top() + 12, min(y, av.bottom() - h - 12))
        widget.move(int(x), int(y))

    QTimer.singleShot(0, _do)

