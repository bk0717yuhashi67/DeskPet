"""系统托盘图标与右键菜单。

图标不用外部素材：直接把 rig 画的企鹅头裁成 64x64，再按状态叠一点标记。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from pet import rig
from pet.pose import Pose

MENU_QSS = """
QMenu {
    background: #FFFFFF;
    border: 1px solid #DCE3EE;
    border-radius: 10px;
    padding: 6px 4px;
}
QMenu::item {
    padding: 6px 22px 6px 14px;
    border-radius: 6px;
    color: #22314A;
    font-family: "Microsoft YaHei UI", "微软雅黑", sans-serif;
    font-size: 12px;
}
QMenu::item:selected { background: #EAF0FA; color: #10203A; }
QMenu::item:disabled { color: #98A4B8; }
QMenu::separator { height: 1px; background: #E8EDF5; margin: 5px 8px; }
QMenu::indicator { width: 14px; height: 14px; }
"""

STATUS_COLORS = {
    "normal": None,
    "sleep": QColor(120, 140, 180, 150),
    "game": None,
    "quiet": QColor(150, 160, 175, 165),
    "paused": QColor(170, 175, 185, 150),
}


def _z_mark(p: QPainter, size: int) -> None:
    p.setPen(QPen(QColor("#8FA3C8"), max(1.4, size / 28), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    s = size * 0.14
    x = size * 0.70
    y = size * 0.20
    path = QPainterPath()
    path.moveTo(x - s, y - s)
    path.lineTo(x + s, y - s)
    path.lineTo(x - s, y + s)
    path.lineTo(x + s, y + s)
    p.drawPath(path)


def _ball_mark(p: QPainter, size: int) -> None:
    r = size * 0.15
    p.setPen(QPen(QColor(11, 18, 32, 170), 1.2))
    p.setBrush(QColor("#FF8A3D"))
    p.drawEllipse(QPoint(size * 0.76, size * 0.76), r, r)


def make_icon(kind: str = "normal", size: int = 64) -> QIcon:
    """用代码画出托盘图标，零素材。"""
    pose = Pose()
    if kind == "sleep":
        pose.eye_open = 0.0
        pose.head_rot = 14.0
        pose.body_y = 6.0
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    # 只画头部区域：把设计画布裁到头部并放大
    p.translate(size * 0.5, size * 0.42)
    k = size / 118.0
    p.scale(k, k)
    p.translate(-100.0, -74.0)
    rig.paint_penguin(p, pose)
    p.resetTransform()

    tint = STATUS_COLORS.get(kind)
    if tint is not None:
        p.setCompositionMode(QPainter.CompositionMode_SourceAtop)
        p.fillRect(0, 0, size, size, tint)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

    if kind == "sleep":
        _z_mark(p, size)
    elif kind == "game":
        _ball_mark(p, size)
    p.end()
    return QIcon(pm)


class Tray(QObject):
    toggle_window = Signal()
    start_game = Signal()
    stop_game = Signal()
    manual_action = Signal(str)
    open_stats = Signal()
    open_settings = Signal()
    toggle_sleep = Signal(bool)
    toggle_pomodoro = Signal(bool)
    open_about = Signal()
    create_shortcut = Signal()
    quit_app = Signal()
    set_scale = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.icon = QSystemTrayIcon(make_icon("normal"), parent)
        self.icon.setToolTip("桌面企鹅")
        self.menu = QMenu()
        self.menu.setStyleSheet(MENU_QSS)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._on_activated)

        self._acts: dict[str, QAction] = {}
        self._build()
        self.icon.show()

    # ------------------------------------------------------------ 构建
    def _build(self) -> None:
        self._acts["status"] = self.menu.addAction("今天还没开始呢")
        self._acts["status"].setEnabled(False)

        self.menu.addSeparator()
        group = QActionGroup(self.menu)
        group.setExclusive(True)
        for name, label, val in (("s1", "小一点", 0.75), ("s2", "标准", 1.0), ("s3", "大一点", 1.35)):
            a = self.menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(abs(val - 1.0) < 1e-6)
            group.addAction(a)
            a.triggered.connect(lambda _=False, v=val: self.set_scale.emit(v))

        self.menu.addSeparator()
        self._acts["game"] = self.menu.addAction("开始抛球小游戏")
        self._acts["game"].triggered.connect(self._on_game_clicked)

        # 可勾选动作必须从 QAction 自己读勾选状态再转发。
        # 不能写 triggered.connect(self.toggle_x.emit)：
        # PySide6 会把 triggered 解析成无参重载，于是调用 emit() 时少了那个 bool，
        # 直接抛 "toggle_x(bool) needs 1 argument(s), 0 given"，菜单看上去"点了没反应"。
        self._acts["pomodoro"] = self.menu.addAction("专注 25 分钟（番茄钟）")
        self._acts["pomodoro"].setCheckable(True)
        self._acts["pomodoro"].triggered.connect(
            lambda _=False, a=self._acts["pomodoro"]: self.toggle_pomodoro.emit(a.isChecked())
        )
        self._acts["sleep"] = self.menu.addAction("让它睡觉")
        self._acts["sleep"].setCheckable(True)
        self._acts["sleep"].triggered.connect(
            lambda _=False, a=self._acts["sleep"]: self.toggle_sleep.emit(a.isChecked())
        )

        act_menu = self.menu.addMenu("立即做点什么")
        act_menu.setStyleSheet(MENU_QSS)
        for clip, label in _manual_items():
            a = act_menu.addAction(label)
            a.triggered.connect(lambda _=False, c=clip: self.manual_action.emit(c))

        self.menu.addSeparator()
        self._add("stats", "数据面板…", self.open_stats)

        # 提醒设置 / 暂停统计 / 清除今日数据 都收进「设置」窗口了，
        # 菜单里只留高频操作，避免一屏塞满。
        self._add("settings", "设置…", self.open_settings)
        self._add("lnk", "创建桌面快捷方式", self.create_shortcut)

        self.menu.addSeparator()
        self._add("about", "关于", self.open_about)
        self._add("quit", "退出", self.quit_app)

    def _add(self, key: str, label: str, sig) -> None:
        a = self.menu.addAction(label)
        a.triggered.connect(lambda _=False: sig.emit())
        self._acts[key] = a

    # ------------------------------------------------------------ 交互
    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.toggle_window.emit()

    def _on_game_clicked(self) -> None:
        if self._acts["game"].text().startswith("开始"):
            self.start_game.emit()
        else:
            self.stop_game.emit()

    # ------------------------------------------------------------ 刷新
    def set_status_line(self, text: str) -> None:
        self._acts["status"].setText(text)

    def set_game_running(self, running: bool, score: int = 0) -> None:
        self._acts["game"].setText(f"结束游戏（得分 {score}）" if running else "开始抛球小游戏")

    def set_icon(self, kind: str) -> None:
        self.icon.setIcon(make_icon(kind))

    def set_check(self, key: str, value: bool) -> None:
        act = self._acts.get(key)
        if act is not None and act.isCheckable():
            act.blockSignals(True)
            act.setChecked(value)
            act.blockSignals(False)

    def set_sleep_label(self, sleeping: bool) -> None:
        self._acts["sleep"].setText("叫醒它" if sleeping else "让它睡觉")

    def popup_at(self, global_pos: QPoint) -> None:
        """在指定屏幕位置弹出这个菜单（右键点企鹅时用）。

        和托盘图标的右键菜单是**同一个** QMenu 实例——两处入口、一份菜单，
        不会出现"两个地方菜单不一致"的维护地狱。
        """
        self.icon.setContextMenu(self.menu)   # 保险：确认关联还在
        self.menu.popup(global_pos)

    def notify(self, title: str, text: str) -> None:
        try:
            self.icon.showMessage(title, text, self.icon.icon(), 4000)
        except Exception:
            pass

    def hide(self) -> None:
        self.icon.hide()


def _manual_items():
    from pet import clips

    return clips.MANUAL
