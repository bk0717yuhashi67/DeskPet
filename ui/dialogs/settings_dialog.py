"""通用设置：外观、打扰强度、作息、隐私、提示音、开机自启。"""
from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import QTime, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ui.style import DIALOG_QSS, center_on_screen
from app.config import config
from app.paths import DATA_DIR

AUTOSTART_KEY = "DeskPetPenguin"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _read_time(text: str, fallback: str = "23:30") -> QTime:
    try:
        hh, mm = (int(x) for x in str(text).split(":"))
        return QTime(hh, mm)
    except Exception:
        h, m = (int(x) for x in fallback.split(":"))
        return QTime(h, m)


class SettingsDialog(QDialog):
    clear_today = Signal()
    clear_all = Signal()
    scale_changed = Signal(float)
    open_reminders = Signal()
    pause_stats = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setStyleSheet(DIALOG_QSS)
        self.resize(520, 700)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self._paused = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        head = QWidget()
        head.setObjectName("Panel")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(20, 16, 20, 8)
        t = QLabel("设置")
        t.setObjectName("H1")
        hl.addWidget(t)
        hl.addStretch(1)
        root.addWidget(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("Panel")
        body = QVBoxLayout(content)
        body.setContentsMargins(20, 4, 20, 16)
        body.setSpacing(12)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        body.addWidget(self._appearance_group())
        body.addWidget(self._reminder_group())
        body.addWidget(self._quiet_group())
        body.addWidget(self._sleep_group())
        body.addWidget(self._privacy_group())
        body.addWidget(self._sound_group())
        body.addWidget(self._system_group())
        body.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        wrap = QWidget()
        wrap.setObjectName("Panel")
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(20, 8, 20, 14)
        wl.addStretch(1)
        wl.addWidget(buttons)
        root.addWidget(wrap)
        center_on_screen(self)

    # ------------------------------------------------------------ 分组
    def _appearance_group(self) -> QGroupBox:
        g = QGroupBox("外观")
        f = QFormLayout(g)
        f.setSpacing(9)
        self.scale_combo = QComboBox()
        for label, val in (("小一点", 0.75), ("标准", 1.0), ("大一点", 1.35)):
            self.scale_combo.addItem(label, val)
        cur = float(config.get("pet_scale", 1.0) or 1.0)
        best = min(range(self.scale_combo.count()),
                   key=lambda i: abs(self.scale_combo.itemData(i) - cur))
        self.scale_combo.setCurrentIndex(best)
        f.addRow("企鹅大小", self.scale_combo)

        self.click_mode = QComboBox()
        for label, val in (("自动（推荐）", "auto"), ("强制 WM_NCHITTEST", "nchittest"),
                           ("强制扩展样式（兜底）", "exstyle")):
            self.click_mode.addItem(label, val)
        idx = self.click_mode.findData(config.get("click_through_mode", "auto"))
        self.click_mode.setCurrentIndex(max(0, idx))
        f.addRow("点击穿透", self.click_mode)
        hint = QLabel("若发现点不到企鹅、或点桌面会被挡住，在这里换一种方式（重启生效）。")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        f.addRow("", hint)
        return g

    def _reminder_group(self) -> QGroupBox:
        """提醒设置原来在托盘菜单里，现在收进设置窗口。"""
        g = QGroupBox("提醒")
        v = QVBoxLayout(g)
        v.setSpacing(8)
        self.btn_reminders = QPushButton("打开提醒设置…")
        self.btn_reminders.clicked.connect(self._on_open_reminders)
        v.addWidget(self.btn_reminders)
        hint = QLabel("添加或修改吃饭、喝水、休息这类定时提醒，也可以在这里试听一次提醒。")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        v.addWidget(hint)
        return g

    def _quiet_group(self) -> QGroupBox:
        g = QGroupBox("打扰强度")
        f = QFormLayout(g)
        f.setSpacing(9)
        self.per_day = QSpinBox()
        self.per_day.setRange(1, 40)
        self.per_day.setSuffix(" 次/天")
        self.per_day.setValue(int(config.get("bubble_per_day", 8) or 8))
        f.addRow("主动消息上限", self.per_day)

        self.gap = QSpinBox()
        self.gap.setRange(1, 120)
        self.gap.setSuffix(" 分钟")
        self.gap.setValue(int(config.get("bubble_min_gap_min", 8) or 8))
        f.addRow("两次消息间隔", self.gap)

        self.per_hour = QSpinBox()
        self.per_hour.setRange(0, 20)
        self.per_hour.setSuffix(" 次/小时")
        self.per_hour.setValue(int(config.get("random_action_per_hour", 3) or 3))
        f.addRow("随机小动作上限", self.per_hour)

        hint = QLabel("全屏、会议、游戏、夜间睡眠、以及你正处在心流中时，一律自动静默。")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        f.addRow("", hint)
        return g

    def _sleep_group(self) -> QGroupBox:
        g = QGroupBox("作息")
        f = QFormLayout(g)
        f.setSpacing(9)
        self.sleep_enabled = QCheckBox("夜间自动睡觉")
        self.sleep_enabled.setChecked(bool(config.get("sleep_enabled", True)))
        f.addRow("", self.sleep_enabled)

        self.sleep_start = QTimeEdit()
        self.sleep_start.setDisplayFormat("HH:mm")
        self.sleep_start.setTime(_read_time(config.get("sleep_start", "23:30")))
        f.addRow("入睡时间", self.sleep_start)

        self.sleep_end = QTimeEdit()
        self.sleep_end.setDisplayFormat("HH:mm")
        self.sleep_end.setTime(_read_time(config.get("sleep_end", "07:00"), "07:00"))
        f.addRow("起床时间", self.sleep_end)

        self.auto_sleep = QSpinBox()
        self.auto_sleep.setRange(5, 240)
        self.auto_sleep.setSuffix(" 分钟")
        self.auto_sleep.setValue(int(config.get("auto_sleep_min", 30) or 30))
        f.addRow("空闲多久入睡", self.auto_sleep)
        return g

    def _privacy_group(self) -> QGroupBox:
        g = QGroupBox("隐私与数据")
        v = QVBoxLayout(g)
        v.setSpacing(8)

        self.collect_input = QCheckBox("统计按键与鼠标（只计数，不记录内容）")
        self.collect_input.setChecked(bool(config.get("collect_input", True)))
        v.addWidget(self.collect_input)

        self.collect_title = QCheckBox("记录窗口标题（关掉后浏览器只能归到「浏览器-未分类」）")
        self.collect_title.setChecked(bool(config.get("collect_window_title", True)))
        v.addWidget(self.collect_title)

        # 「暂停统计」原来在托盘菜单里，现在收进设置窗口。
        # 控件名不能和上面的信号同名（pause_stats），否则实例属性会把信号覆盖掉，
        # 外部 connect 到的是一个 QCheckBox —— 这是个真实踩过的坑。
        self.pause_stats_box = QCheckBox("暂停统计（出去忙一阵时临时关掉采集）")
        self.pause_stats_box.setChecked(self._paused)
        v.addWidget(self.pause_stats_box)

        path = QLabel(f"数据目录：{DATA_DIR}")
        path.setObjectName("Hint")
        path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path.setWordWrap(True)
        v.addWidget(path)

        row = QHBoxLayout()
        btn_dir = QPushButton("打开数据目录")
        btn_dir.clicked.connect(self._open_dir)
        btn_today = QPushButton("清除今日数据")
        btn_today.clicked.connect(self._on_clear_today)
        btn_all = QPushButton("清空全部数据")
        btn_all.clicked.connect(self._on_clear_all)
        row.addWidget(btn_dir)
        row.addStretch(1)
        row.addWidget(btn_today)
        row.addWidget(btn_all)
        v.addLayout(row)

        note = QLabel("程序零联网：不请求任何网络接口，数据不会离开这台电脑。")
        note.setObjectName("Hint")
        note.setWordWrap(True)
        v.addWidget(note)
        return g

    def _sound_group(self) -> QGroupBox:
        g = QGroupBox("提示音")
        f = QFormLayout(g)
        f.setSpacing(9)
        self.sound_enabled = QCheckBox("提醒时发出声音")
        self.sound_enabled.setChecked(bool(config.get("sound_enabled", True)))
        f.addRow("", self.sound_enabled)

        row = QHBoxLayout()
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(int(config.get("sound_volume", 30) or 30))
        self.volume_label = QLabel(f"{self.volume.value()}%")
        self.volume.valueChanged.connect(lambda v: self.volume_label.setText(f"{v}%"))
        row.addWidget(self.volume, 1)
        row.addWidget(self.volume_label)
        w = QWidget()
        w.setLayout(row)
        f.addRow("音量", w)
        return g

    def _system_group(self) -> QGroupBox:
        g = QGroupBox("系统")
        v = QVBoxLayout(g)
        v.setSpacing(8)
        self.autostart = QCheckBox("开机自动启动")
        self.autostart.setChecked(self._autostart_enabled())
        v.addWidget(self.autostart)
        hint = QLabel("写的是当前用户的启动项，不需要管理员权限，关掉即可移除。")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        v.addWidget(hint)
        return g

    # ------------------------------------------------------------ 操作
    def _open_dir(self) -> None:
        try:
            os.startfile(str(DATA_DIR))  # type: ignore[attr-defined]
        except Exception:
            try:
                subprocess.Popen(["explorer", str(DATA_DIR)])
            except Exception:
                pass

    def set_paused(self, paused: bool) -> None:
        """由主程序告知当前是否已暂停统计（决定复选框初始勾选）。"""
        self._paused = bool(paused)
        if hasattr(self, "pause_stats_box"):
            self.pause_stats_box.setChecked(self._paused)

    def _on_open_reminders(self) -> None:
        self.open_reminders.emit()

    def _on_clear_today(self) -> None:
        if self._confirm("清除今日数据？", "会删掉今天的会话明细、应用时长与指标缓存。") == QMessageBox.Yes:
            self.clear_today.emit()

    def _on_clear_all(self) -> None:
        if self._confirm(
            "清空全部数据？",
            "所有使用记录都会被删除，无法恢复。提醒设置与偏好会保留。",
        ) == QMessageBox.Yes:
            self.clear_all.emit()

    def _confirm(self, title: str, text: str) -> int:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Warning)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.button(QMessageBox.Yes).setText("确定")
        box.button(QMessageBox.No).setText("取消")
        box.setDefaultButton(QMessageBox.No)
        return box.exec()

    # ------------------------------------------------------------ 自启
    def _autostart_enabled(self) -> bool:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                val, _ = winreg.QueryValueEx(k, AUTOSTART_KEY)
                return bool(val)
        except FileNotFoundError:
            return bool(config.get("autostart", False))
        except Exception:
            return False

    def _set_autostart(self, on: bool) -> bool:
        from app.paths import launch_command

        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as k:
                if on:
                    winreg.SetValueEx(k, AUTOSTART_KEY, 0, winreg.REG_SZ, launch_command())
                else:
                    try:
                        winreg.DeleteValue(k, AUTOSTART_KEY)
                    except FileNotFoundError:
                        pass
            return True
        except Exception as exc:
            from app import logging_setup

            logging_setup.get("settings").warning("设置开机自启失败: %s", exc)
            return False

    # ------------------------------------------------------------ 保存
    def _on_ok(self) -> None:
        scale = float(self.scale_combo.currentData() or 1.0)
        want_autostart = self.autostart.isChecked()
        if want_autostart != self._autostart_enabled():
            self._set_autostart(want_autostart)

        config.update({
            "pet_scale": scale,
            "click_through_mode": self.click_mode.currentData() or "auto",
            "bubble_per_day": int(self.per_day.value()),
            "bubble_min_gap_min": int(self.gap.value()),
            "random_action_per_hour": int(self.per_hour.value()),
            "sleep_enabled": self.sleep_enabled.isChecked(),
            "sleep_start": self.sleep_start.time().toString("HH:mm"),
            "sleep_end": self.sleep_end.time().toString("HH:mm"),
            "auto_sleep_min": int(self.auto_sleep.value()),
            "collect_input": self.collect_input.isChecked(),
            "collect_window_title": self.collect_title.isChecked(),
            "sound_enabled": self.sound_enabled.isChecked(),
            "sound_volume": int(self.volume.value()),
            "autostart": want_autostart,
        })
        self.scale_changed.emit(scale)
        self.pause_stats.emit(self.pause_stats_box.isChecked())
        self.accept()
