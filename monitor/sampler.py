"""采样器：把"前台窗口 / 空闲 / 锁屏 / 键鼠计数"整理成会话与日统计。

节流策略：
    1s   前台窗口 + 空闲时长（GetLastInputInfo，零成本）
    2s   全屏 / 会议检测（决定要不要自动静音）
    500ms 消费钩子队列
    30s  或前台切换时落库
"""
from __future__ import annotations

import queue
import time
from collections import deque
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QTimer, Signal

from app import logging_setup
from app.config import config
from . import appclass, winapi
from .storage import Storage, day_of

log = logging_setup.get("sampler")

IDLE_THRESHOLD = 60.0        # 秒，超过即视为离开
SLEEP_IDLE_THRESHOLD = 1800.0  # 秒，超过即视为长时间离开
FOCUS_MIN_S = 600            # 秒，专注块最小长度
FOCUS_IDLE_BREAK = 60.0      # 秒，专注块被空闲打断的阈值
FLUSH_INTERVAL = 30.0        # 秒
TIMER_TICK_MS = 1000
DRAIN_TICK_MS = 500


@dataclass
class LiveStats:
    """今日实时累计量。UI 与心情判断直接读它，不必反复查库。"""
    day: str = ""
    active_s: int = 0
    idle_s: int = 0
    locked_s: int = 0
    keystrokes: int = 0
    backspaces: int = 0
    clicks: int = 0
    mouse_dist_px: int = 0
    app_switches: int = 0
    first_ts: int = 0
    last_ts: int = 0
    late_minutes: int = 0
    idle_gap_cnt: int = 0
    sleep_gap_cnt: int = 0
    focus_block_cnt: int = 0
    longest_focus_s: int = 0

    # 会话 / 状态
    locked: bool = False
    idle_now: float = 0.0
    in_idle: bool = False
    fullscreen: bool = False
    meeting: bool = False
    current_exe: str = ""
    current_app: str = ""
    current_category: str = ""
    current_title: str = ""
    continuous_active_s: int = 0     # 自上次像样休息以来的连续活跃
    focus_run_s: int = 0             # 当前专注块已持续
    focus_run_exe: str = ""
    focus_run_app: str = ""

    key_times: deque = field(default_factory=lambda: deque(maxlen=4000))

    def clone_counts(self) -> dict:
        return {
            "active_s": self.active_s, "idle_s": self.idle_s, "locked_s": self.locked_s,
            "keystrokes": self.keystrokes, "backspaces": self.backspaces,
            "clicks": self.clicks, "mouse_dist_px": self.mouse_dist_px,
            "app_switches": self.app_switches, "late_minutes": self.late_minutes,
            "idle_gap_cnt": self.idle_gap_cnt, "sleep_gap_cnt": self.sleep_gap_cnt,
            "focus_block_cnt": self.focus_block_cnt,
            "longest_focus_s": self.longest_focus_s,
            "first_ts": self.first_ts or None, "last_ts": self.last_ts or None,
        }


class Sampler(QObject):
    """采样主循环。所有写库都发生在这里（主线程），钩子线程只负责入队。"""

    tick = Signal()                 # 每秒一次，UI 可据此刷新
    session_closed = Signal(dict)   # 一段前台会话结束
    state_flags = Signal(bool, bool, bool)  # (locked, fullscreen, meeting)

    def __init__(self, storage: Storage, hooks, parent=None) -> None:
        super().__init__(parent)
        self.storage = storage
        self.hooks = hooks
        self.stats = LiveStats()
        self.enabled = bool(config.get("collect_input", True))
        self.consent = bool(config.get("consent", False))

        self._pending_daily: dict[str, int] = {}
        self._pending_apps: dict[tuple[str, str, str], list[int]] = {}
        self._last_flush = time.time()
        self._last_pid = 0
        self._last_hwnd = 0
        self._session_start = 0.0
        self._session_switches = 0
        self._last_key_ts = time.time()
        self._session_day = day_of(time.time())
        self._focus_start = 0.0
        self._passive_hwnd = 0
        self._passive_cached = False

        self._timer = QTimer(self)
        self._timer.setInterval(TIMER_TICK_MS)
        self._timer.timeout.connect(self._on_tick)

        self._drain = QTimer(self)
        self._drain.setInterval(DRAIN_TICK_MS)
        self._drain.timeout.connect(self._drain_events)

        self._rollover_guard = ""

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        self._adopt_today()
        self._timer.start()
        self._drain.start()
        log.info("采样器已启动，今日已有数据: active=%ss keys=%s",
                 self.stats.active_s, self.stats.keystrokes)

    def stop(self) -> None:
        self._timer.stop()
        self._drain.stop()
        self._close_session(time.time())
        self.flush()

    def set_enabled(self, on: bool) -> None:
        """暂停 / 恢复输入统计（暂停期间仍统计应用时长，只是不记键鼠）。"""
        self.enabled = bool(on)

    def set_consent(self, on: bool) -> None:
        self.consent = bool(on)

    # ------------------------------------------------------------ 初始化
    def _adopt_today(self) -> None:
        """进程重启后接着今天的累计量继续算，不从零开始。"""
        now = time.time()
        self.stats = LiveStats(day=day_of(now))
        self._session_day = self.stats.day
        row = self.storage.daily(day_of(now))
        if row:
            for k in (
                "active_s", "idle_s", "locked_s", "keystrokes", "backspaces",
                "clicks", "mouse_dist_px", "app_switches", "late_minutes",
                "idle_gap_cnt", "sleep_gap_cnt", "focus_block_cnt", "longest_focus_s",
            ):
                setattr(self.stats, k, int(row.get(k) or 0))
            self.stats.first_ts = int(row.get("first_ts") or 0)
        self._session_start = now
        self._rollover_guard = self.stats.day

    # ------------------------------------------------------------ 事件消费
    def _drain_events(self) -> None:
        q: queue.Queue = self.hooks.events
        got_keys = False
        for _ in range(4000):
            try:
                ev = q.get_nowait()
            except queue.Empty:
                break
            kind = ev[0]
            if kind == "key":
                if not self.enabled:
                    continue
                _, ts, is_back = ev
                self.stats.keystrokes += 1
                if is_back:
                    self.stats.backspaces += 1
                self.stats.key_times.append(ts)
                got_keys = True
            elif kind == "click":
                if not self.enabled:
                    continue
                _, _ts, name = ev
                if name in ("left", "right", "middle"):
                    self.stats.clicks += 1
            elif kind == "move":
                if not self.enabled:
                    continue
                _, _ts, dist = ev
                self.stats.mouse_dist_px += int(dist)

        if got_keys:
            self._last_key_ts = time.time()

        # 钩子降级探测：前台一直在变但两分钟一个按键都没有
        if (
            self.enabled
            and self.consent
            and self.hooks.keyboard_ok
            and time.time() - self._last_key_ts > 120
            and self.stats.active_s > 300
        ):
            if not self.hooks.degraded:
                self.hooks.degraded = True
                log.info("键盘统计可能不完整（管理员权限程序内的输入收不到）")

    # ------------------------------------------------------------ 主 tick
    def _on_tick(self) -> None:
        now = time.time()
        self._check_rollover(now)

        hwnd = winapi.foreground_window()
        idle = winapi.idle_seconds()
        self.stats.idle_now = idle

        # 锁屏：以 WTS 通知为主，OpenInputDesktop 为辅（两者都要去抖）
        locked = self.stats.locked
        if not winapi.input_desktop_available() and hwnd == 0:
            locked = True
        self.stats.locked = locked

        if locked:
            self.stats.locked_s += 1
            self._pending_daily["locked_s"] = self._pending_daily.get("locked_s", 0) + 1
            self._close_session(now)
            return

        if idle >= IDLE_THRESHOLD and not self._passive_consuming(hwnd):
            # 人不在：计空闲，专注块被打断
            if not self.stats.in_idle:
                self.stats.in_idle = True
                self.stats.idle_gap_cnt += 1
                self._pending_daily["idle_gap_cnt"] = self._pending_daily.get("idle_gap_cnt", 0) + 1
                self._break_focus_run(now)
            self.stats.idle_s += 1
            self._pending_daily["idle_s"] = self._pending_daily.get("idle_s", 0) + 1
            self.stats.continuous_active_s = 0
            self._close_session(now)
            return

        # 看视频 / 玩游戏时没有键鼠输入，但人是在的，不能算"离开"
        passive = self._passive_consuming(hwnd)

        self.stats.in_idle = False
        self.stats.active_s += 1
        self._pending_daily["active_s"] = self._pending_daily.get("active_s", 0) + 1
        self.stats.last_ts = int(now)
        if not self.stats.first_ts:
            self.stats.first_ts = int(now)

        hour = time.localtime(now).tm_hour
        if hour >= 23 or hour < 5:
            self.stats.late_minutes += 1
            self._pending_daily["late_minutes"] = self._pending_daily.get("late_minutes", 0) + 1

        self._track_foreground(hwnd, now)

        if passive:
            # 被动消费内容不算"连续伏案"，也不算专注块，
            # 否则看一场电影会被统计成一段 100 分钟的深度专注。
            self.stats.continuous_active_s = 0
            self._break_focus_run(now)
        else:
            self.stats.continuous_active_s += 1
            self._track_focus_run(now)

        if self._last_flush + FLUSH_INTERVAL <= now:
            self.flush()

        self.tick.emit()

    # ------------------------------------------------------------ 被动消费判定
    def _passive_consuming(self, hwnd: int) -> bool:
        """前台是在看视频 / 玩游戏 / 全屏演示吗？

        这类场景人对着屏幕但不动键鼠，若按"无输入即离开"处理，
        看两小时电影会被记成"离开两小时"，视频与游戏时长全部丢失。
        按窗口句柄缓存判定结果，避免每秒重复分类。
        """
        if not hwnd:
            return False
        if hwnd == self._passive_hwnd:
            return self._passive_cached

        pid = winapi.window_pid(hwnd)
        exe = appclass.exe_of(pid) or ""
        title = winapi.window_title(hwnd) if config.get("collect_window_title", True) else ""
        overrides = config.get("app_category_override", {}) or {}
        category, _name = appclass.classify(exe, title, overrides)

        passive = category in appclass.LEISURE_CATEGORIES or winapi.is_fullscreen(hwnd)
        self._passive_hwnd = hwnd
        self._passive_cached = passive
        return passive

    # ------------------------------------------------------------ 前台会话
    def _track_foreground(self, hwnd: int, now: float) -> None:
        collect_title = bool(config.get("collect_window_title", True))
        overrides = config.get("app_category_override", {}) or {}

        if hwnd and hwnd != self._last_hwnd:
            self._last_hwnd = hwnd
            pid = winapi.window_pid(hwnd)
            if pid:
                self._last_pid = pid
                exe = appclass.exe_of(pid) or "unknown"
                title = winapi.window_title(hwnd) if collect_title else ""
                category, app_name = appclass.classify(exe, title, overrides)

                if exe != self.stats.current_exe:
                    # 先给旧应用记一次切换，再关会话（_close_session 会清空 current_*）
                    if self.stats.current_exe:
                        old_key = (
                            self.stats.current_exe,
                            self.stats.current_app,
                            self.stats.current_category,
                        )
                        self._pending_apps.setdefault(old_key, [0, 0])[1] += 1
                    self._close_session(now)
                    self.stats.app_switches += 1
                    self._pending_daily["app_switches"] = (
                        self._pending_daily.get("app_switches", 0) + 1
                    )
                    self.stats.current_exe = exe
                    self.stats.current_app = app_name
                    self.stats.current_category = category
                self.stats.current_title = title

        # 每个 tick 都累计当前应用的时长（不能只在切换时累加）
        if self.stats.current_exe:
            key = (
                self.stats.current_exe,
                self.stats.current_app,
                self.stats.current_category,
            )
            self._pending_apps.setdefault(key, [0, 0])[0] += 1

        # 全屏 / 会议：2 秒查一次就够
        if hwnd and int(now) % 2 == 0:
            fs = winapi.is_fullscreen(hwnd)
            mt = appclass.is_meeting(self.stats.current_exe, self.stats.current_title)
            if (fs, mt) != (self.stats.fullscreen, self.stats.meeting):
                self.stats.fullscreen = fs
                self.stats.meeting = mt
                self.state_flags.emit(self.stats.locked, fs, mt)

    def _close_session(self, now: float) -> None:
        if not self.stats.current_exe or not self._session_start:
            self._session_start = now
            return
        dur = int(now - self._session_start)
        if dur >= 1:
            self.storage.add_session(
                day=self.stats.day,
                pid=self._last_pid,
                exe=self.stats.current_exe,
                app_name=self.stats.current_app,
                category=self.stats.current_category,
                title=self.stats.current_title if config.get("collect_window_title", True) else "",
                start_ts=int(self._session_start),
                end_ts=int(now),
                duration_s=dur,
            )
            self.session_closed.emit({
                "exe": self.stats.current_exe,
                "app": self.stats.current_app,
                "category": self.stats.current_category,
                "duration_s": dur,
            })
        self.stats.current_exe = ""
        self.stats.current_app = ""
        self.stats.current_category = ""
        self.stats.current_title = ""
        self._session_start = now

    # ------------------------------------------------------------ 专注块
    def _track_focus_run(self, now: float) -> None:
        if not self.stats.current_exe:
            return
        if self.stats.focus_run_s > 0 and self.stats.focus_run_exe != self.stats.current_exe:
            # 换应用即专注块结束
            self._end_focus_run(now)
        if self.stats.focus_run_s == 0:
            self.stats.focus_run_exe = self.stats.current_exe
            self.stats.focus_run_app = self.stats.current_app
            self._focus_start = now
        self.stats.focus_run_s += 1

    def _end_focus_run(self, now: float) -> None:
        dur = int(self.stats.focus_run_s)
        if dur >= FOCUS_MIN_S:
            start = int(now - dur)
            self.storage.add_focus_block(
                day=self.stats.day,
                start_ts=start,
                end_ts=int(now),
                top_exe=self.stats.focus_run_exe,
                top_app=self.stats.focus_run_app,
                keystrokes=min(self.stats.keystrokes, 9999),
            )
            self.stats.focus_block_cnt += 1
            self._pending_daily["focus_block_cnt"] = self._pending_daily.get("focus_block_cnt", 0) + 1
            if dur > self.stats.longest_focus_s:
                self.stats.longest_focus_s = dur
        self.stats.focus_run_s = 0
        self.stats.focus_run_exe = ""
        self.stats.focus_run_app = ""

    def _break_focus_run(self, now: float) -> None:
        if self.stats.focus_run_s > 0:
            self._end_focus_run(now)

    # ------------------------------------------------------------ 落库
    def flush(self) -> None:
        if not self._pending_daily and not self._pending_apps:
            self._last_flush = time.time()
            return
        try:
            if self._pending_daily:
                self.storage.bump_daily(self.stats.day, **self._pending_daily)
                self._pending_daily.clear()
            for (exe, app_name, category), (secs, sw) in self._pending_apps.items():
                if secs or sw:
                    self.storage.bump_app(
                        self.stats.day, exe, app_name, category, secs, sw
                    )
            self._pending_apps.clear()
            self.storage.set_meta("total_active_s", self.storage.total_active_seconds())
            self.storage.flush()
        except Exception as exc:
            log.warning("落库失败: %s", exc)
        self._last_flush = time.time()

    def _check_rollover(self, now: float) -> None:
        day = day_of(now)
        if day == self._rollover_guard:
            return
        log.info("跨天，切换统计日期 %s -> %s", self._rollover_guard, day)
        self._close_session(now)
        self._end_focus_run(now)
        self.flush()
        prev = self.stats
        self.stats = LiveStats(day=day)
        self.stats.first_ts = int(now)
        prev.day = self._rollover_guard
        self._rollover_guard = day
        self._session_start = now

    # ------------------------------------------------------------ 对外查询
    def hook_degraded(self) -> bool:
        return bool(self.hooks.degraded)
