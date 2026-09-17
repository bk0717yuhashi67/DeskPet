"""数据面板：今日状态、应用排行、构成、作息热力、7 日趋势、输入画像。"""
from __future__ import annotations

import os
import subprocess
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui.style import DIALOG_QSS, center_on_screen
from ui.widgets.card import Card, Divider, MetricTile, SectionTitle, ui_font
from ui.widgets.charts import BarChart, Donut, HeatStrip, Legend, Sparkline, fmt_duration
from app.paths import DATA_DIR
from monitor import analyzer
from monitor.appclass import CATEGORY_COLORS
from monitor.analyzer import Metrics

EXTRA_STATS_HINT = (
    "想把状态判断得更准，还可以再补充这些统计：空闲碎片化分布、最长连续专注时长、"
    "全屏（视频/游戏）时长、正在编译或下载时自动静默、音频占用（在听歌/开会）、"
    "按键类型粗分类（方向键多=写代码、纯字母=写作，同样不含内容）、"
    "每日首次输入时间（推断起床作息）、连续使用天数、7 日打字速度趋势、专注中被打断的次数。"
)


class StatsPanel(QDialog):
    def __init__(self, storage, sampler, mood_tracker=None, parent=None) -> None:
        super().__init__(parent)
        self.storage = storage
        self.sampler = sampler
        self.mood_tracker = mood_tracker

        self.setWindowTitle("今日状态")
        self.setStyleSheet(DIALOG_QSS)
        self.resize(720, 720)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        head = QWidget()
        head.setObjectName("Panel")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(20, 16, 20, 12)
        self.title = QLabel("今日状态")
        self.title.setObjectName("H1")
        hl.addWidget(self.title)
        hl.addStretch(1)
        self.mood_label = QLabel("")
        self.mood_label.setObjectName("H2")
        self.mood_label.setStyleSheet("color:#2B6FE0;")
        hl.addWidget(self.mood_label)
        root.addWidget(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("Panel")
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(20, 4, 20, 20)
        self.body.setSpacing(14)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        self._build_body()
        center_on_screen(self)

        foot = QWidget()
        foot.setObjectName("Panel")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(20, 10, 20, 14)
        self.path_label = QLabel(f"数据位置：{DATA_DIR}")
        self.path_label.setObjectName("Hint")
        fl.addWidget(self.path_label)
        fl.addStretch(1)
        btn_dir = QPushButton("打开数据目录")
        btn_dir.clicked.connect(self._open_dir)
        btn_close = QPushButton("关闭")
        btn_close.setDefault(True)
        btn_close.clicked.connect(self.accept)
        fl.addWidget(btn_dir)
        fl.addWidget(btn_close)
        root.addWidget(foot)

    # ------------------------------------------------------------ 构建
    def _build_body(self) -> None:
        # 指标卡
        self.tiles: dict[str, MetricTile] = {}
        row = QHBoxLayout()
        row.setSpacing(10)
        for key, label, color in (
            ("active", "今日活跃", "#4C8DFF"),
            ("focus", "专注度", "#2B6FE0"),
            ("fatigue", "疲劳度", "#FF7A59"),
            ("activity", "活跃度", "#38C2A6"),
            ("stayup", "熬夜指数", "#9B7BFF"),
        ):
            t = MetricTile(label, accent=QColor(color))
            self.tiles[key] = t
            row.addWidget(t)
        self.body.addLayout(row)

        # 小结
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(
            "background:#FFFFFF;border:1px solid #E4EAF4;border-radius:10px;"
            "padding:11px 13px;color:#3A4A63;"
        )
        self.body.addWidget(self.summary)

        # 应用排行
        self.body.addWidget(SectionTitle("应用时长排行"))
        self.app_card = Card()
        ac = QVBoxLayout(self.app_card)
        ac.setContentsMargins(13, 11, 13, 11)
        self.bar = BarChart()
        ac.addWidget(self.bar)
        self.body.addWidget(self.app_card)

        # 构成
        self.body.addWidget(SectionTitle("时间都去哪儿了"))
        self.donut_card = Card()
        dc = QHBoxLayout(self.donut_card)
        dc.setContentsMargins(13, 11, 13, 11)
        dc.setSpacing(16)
        self.donut = Donut()
        self.donut.setFixedWidth(150)
        dc.addWidget(self.donut)
        right = QVBoxLayout()
        right.setSpacing(8)
        self.legend = Legend()
        right.addWidget(self.legend)
        self.donut_hint = QLabel("")
        self.donut_hint.setObjectName("Hint")
        self.donut_hint.setWordWrap(True)
        right.addWidget(self.donut_hint)
        right.addStretch(1)
        dc.addLayout(right, 1)
        self.body.addWidget(self.donut_card)

        # 作息热力
        self.body.addWidget(SectionTitle("今天的作息分布"))
        self.heat_card = Card()
        hc = QVBoxLayout(self.heat_card)
        hc.setContentsMargins(13, 11, 13, 8)
        self.heat = HeatStrip()
        hc.addWidget(self.heat)
        self.heat_hint = QLabel("")
        self.heat_hint.setObjectName("Hint")
        hc.addWidget(self.heat_hint)
        self.body.addWidget(self.heat_card)

        # 7 日趋势
        self.body.addWidget(SectionTitle("最近 7 天活跃时长"))
        self.trend_card = Card()
        tc = QVBoxLayout(self.trend_card)
        tc.setContentsMargins(13, 11, 13, 9)
        self.spark = Sparkline()
        tc.addWidget(self.spark)
        self.body.addWidget(self.trend_card)

        # 输入画像
        self.body.addWidget(SectionTitle("输入画像"))
        self.io_card = Card()
        ic = QVBoxLayout(self.io_card)
        ic.setContentsMargins(14, 12, 14, 12)
        ic.setSpacing(7)
        self.io_rows: dict[str, QLabel] = {}
        for key, label in (
            ("keys", "按键次数"),
            ("backspace", "退格率"),
            ("clicks", "鼠标点击"),
            ("mouse", "鼠标移动"),
            ("switch", "应用切换"),
            ("focus_block", "专注块"),
            ("span", "首次 / 最后活跃"),
            ("flow", "打字节奏"),
        ):
            r = QHBoxLayout()
            left = QLabel(label)
            left.setStyleSheet("color:#7C8AA3;")
            val = QLabel("—")
            val.setStyleSheet("color:#1B2A4A;font-weight:600;")
            r.addWidget(left)
            r.addStretch(1)
            r.addWidget(val)
            ic.addLayout(r)
            self.io_rows[key] = val
        self.io_hint = QLabel("")
        self.io_hint.setObjectName("Hint")
        self.io_hint.setWordWrap(True)
        ic.addSpacing(2)
        ic.addWidget(Divider())
        ic.addWidget(self.io_hint)
        self.body.addWidget(self.io_card)

        # 还能更准
        self.body.addWidget(SectionTitle("还能统计什么"))
        hint_card = Card()
        hc2 = QVBoxLayout(hint_card)
        hc2.setContentsMargins(13, 11, 13, 11)
        lab = QLabel(EXTRA_STATS_HINT)
        lab.setWordWrap(True)
        lab.setObjectName("Hint")
        hc2.addWidget(lab)
        self.body.addWidget(hint_card)

        self.body.addStretch(1)

    # ------------------------------------------------------------ 刷新
    def refresh(self) -> None:
        st = self.sampler.stats
        day = st.day
        counts = st.clone_counts()
        rows = self.storage.metrics_range(4)
        prev = [r for r in reversed(rows) if r.get("day") != day][:3]

        m = analyzer.compute(
            day=day,
            counts=counts,
            key_times=list(st.key_times),
            focus_count=self.storage.focus_count(day),
            continuous_s=st.continuous_active_s,
            prev_metrics=prev,
        )

        self.title.setText(f"今日状态 · {day}")
        if self.mood_tracker is not None:
            from pet.mood import LABELS

            mood = self.mood_tracker.mood
            self.mood_label.setText(f"企鹅看来你：{LABELS.get(mood, '平静')}")

        self._fill_tiles(m)
        self._fill_summary(m)
        self._fill_apps(day)
        self._fill_categories(day)
        self._fill_heat(day)
        self._fill_trend()
        self._fill_io(m)
        self._fill_hint(st)

        # 缓存当天指标，避免下次开面板重算
        try:
            self.storage.set_metrics(day, {
                "focus": m.focus, "fatigue": m.fatigue, "activity": m.activity,
                "stayup": m.stayup, "distraction": m.distraction,
                "backspace_rate": m.br, "flow": m.flow,
                "mood": self.mood_tracker.mood.value if self.mood_tracker else "",
            })
            self.storage.flush()
        except Exception:
            pass

    def _fill_tiles(self, m: Metrics) -> None:
        self.tiles["active"].set_data(
            fmt_duration(m.active_s),
            analyzer.activity_band(m.activity),
            min(1.0, m.active_s / (8 * 3600.0)),
        )
        self.tiles["focus"].set_data(
            f"{m.focus:.0f}", analyzer.focus_band(m.focus), m.focus / 100.0
        )
        self.tiles["fatigue"].set_data(
            f"{m.fatigue:.0f}", analyzer.fatigue_band(m.fatigue), m.fatigue / 100.0
        )
        self.tiles["activity"].set_data(
            f"{m.activity:.0f}", analyzer.activity_band(m.activity), m.activity / 100.0
        )
        self.tiles["stayup"].set_data(
            f"{m.stayup:.0f}", analyzer.stayup_band(m.stayup), m.stayup / 100.0
        )

    def _fill_summary(self, m: Metrics) -> None:
        apps = self.storage.top_apps(m.day, 1)
        top = apps[0]["app_name"] if apps else "还没有"
        parts = [
            f"今天活跃 {fmt_duration(m.active_s)}",
            f"最长一段专注 {fmt_duration(m.longest_focus_s)}（共 {self.storage.focus_count(m.day)} 段）",
            f"最常开的是 {top}",
        ]
        cats = self.storage.category_totals(m.day)
        leisure = sum(v for k, v in cats.items() if k in ("视频", "游戏"))
        if leisure > 600:
            parts.append(f"休闲 {fmt_duration(leisure)}")
        if m.active_s < 60:
            parts = ["今天还没怎么开始呢，不急。"]
        self.summary.setText("　·　".join(parts))

    def _fill_apps(self, day: str) -> None:
        rows = self.storage.top_apps(day, 8)
        items = []
        for r in rows:
            color = CATEGORY_COLORS.get(r["category"] or "", "#8C9AAE")
            items.append((r["app_name"] or r["exe"] or "未知", int(r["seconds"] or 0), color))
        self.bar.set_items(items)

    def _fill_categories(self, day: str) -> None:
        cats = self.storage.category_totals(day)
        ordered = sorted(cats.items(), key=lambda kv: -kv[1])
        items = [
            (name, secs, CATEGORY_COLORS.get(name, "#8C9AAE"))
            for name, secs in ordered
        ]
        total = sum(cats.values())
        self.donut.set_items(items, fmt_duration(total))
        self.legend.set_items(items)

        work = sum(v for k, v in cats.items() if k in ("工作", "学习"))
        if total > 0:
            ratio = work / total
            if ratio > 0.7:
                text = "今天绝大部分时间都在正事上，很稳。"
            elif ratio > 0.4:
                text = "正事和休闲差不多各占一半。"
            else:
                text = "今天偏放松，休息也是必要的。"
            self.donut_hint.setText(text)
        else:
            self.donut_hint.setText("")

    def _fill_heat(self, day: str) -> None:
        sessions = self.storage.sessions_of(day)
        self.heat.set_values(analyzer.hourly_histogram(sessions))
        first, last = analyzer.hour_span(sessions)
        if first or last:
            self.heat_hint.setText(f"约 {first:02d}:00 开始，{last:02d}:00 之后还在。")
        else:
            self.heat_hint.setText("")

    def _fill_trend(self) -> None:
        rows = self.storage.daily_range(7)
        vals = [float(r.get("active_s") or 0) / 3600.0 for r in rows]
        labels = []
        for r in rows:
            d = str(r.get("day") or "")
            labels.append(d[5:].replace("-", "/") if len(d) >= 10 else "")
        while len(vals) < 7:
            vals.insert(0, 0.0)
            labels.insert(0, "")
        self.spark.suffix = "h"
        self.spark.set_values(vals, labels)

    def _fill_io(self, m: Metrics) -> None:
        self.io_rows["keys"].setText(
            f"{m.keystrokes} 次" + (f"（退格 {m.backspaces}）" if m.backspaces else "")
        )
        self.io_rows["backspace"].setText(
            f"{m.br * 100:.1f}%　{analyzer.backspace_band(m.br)}"
        )
        self.io_rows["clicks"].setText(f"{m.clicks} 次")
        self.io_rows["mouse"].setText(f"{m.mouse_px / 1000.0:.1f} 千像素")
        self.io_rows["switch"].setText(
            f"{m.sr:.0f} 次/小时　{analyzer.switch_band(m.sr)}"
        )
        self.io_rows["focus_block"].setText(
            f"{self.storage.focus_count(m.day)} 段，最长 {fmt_duration(m.longest_focus_s)}"
        )
        if m.first_ts and m.last_ts:
            self.io_rows["span"].setText(
                f"{time.strftime('%H:%M', time.localtime(m.first_ts))}"
                f"　/　{time.strftime('%H:%M', time.localtime(m.last_ts))}"
            )
        else:
            self.io_rows["span"].setText("—")
        self.io_rows["flow"].setText(
            f"{m.flow * 100:.0f}　"
            + ("心流中" if m.flow > 0.7 else ("正常" if m.flow > 0.4 else "偏慢"))
        )

    def _fill_hint(self, st) -> None:
        if not self.sampler.consent:
            self.io_hint.setText("尚未开启输入统计，所以按键与鼠标数据为空。可在首次弹窗或设置里开启。")
        elif getattr(self.sampler.hooks, "degraded", False):
            self.io_hint.setText(
                "检测到键盘统计可能不完整：以管理员身份运行其他程序时，"
                "普通权限的低级钩子收不到它们的按键。这是 Windows 的限制，"
                "不是数据错误——应用时长与窗口切换不受影响。"
            )
        elif not self.sampler.enabled:
            self.io_hint.setText("统计已被暂停（托盘「暂停统计」）。应用时长仍在记录，键鼠计数已停止。")
        else:
            self.io_hint.setText(
                "键盘只统计次数与时间间隔，不记录任何字符内容；窗口标题可在设置中关闭。"
            )

    def _open_dir(self) -> None:
        try:
            os.startfile(str(DATA_DIR))  # type: ignore[attr-defined]
        except Exception:
            try:
                subprocess.Popen(["explorer", str(DATA_DIR)])
            except Exception:
                pass
