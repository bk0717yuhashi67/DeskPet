"""提醒调度：单定时器 + 整分对齐 + 错过补偿。

为什么不用"一个提醒一个定时器"：N 个定时器在系统睡眠唤醒后会产生大量补偿触发与
时钟漂移，而且每一分钟都要各自判断，容易重复。单 tick 天然幂等，去重键一挡就不会重复。

错过补偿：电脑关机 / 睡了一觉 / 休眠恢复后，落在宽限期内（饭点 90 分钟、休息 60 分钟、
自定义 30 分钟）的提醒补一次，并把文案改写成"刚才 12:00 是饭点，补一句"；
超出宽限期就不打扰了，只在下次和用户说话时轻描淡写提一句。
"""
from __future__ import annotations

import time
from datetime import date, datetime, time as dtime, timedelta

from PySide6.QtCore import QObject, QTimer, Signal

from app import logging_setup
from monitor.storage import Storage
from .model import (
    ACTION_NONE,
    KIND_ONCE,
    KIND_WEEKLY,
    Reminder,
)
from .store import ReminderStore

log = logging_setup.get("remind.scheduler")

TICK_MS = 20000          # 20 秒兜底轮询
ALIGN_SLACK_MS = 250


def _hhmm(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


# ---------------------------------------------------------------------------
# 纯函数：日期匹配与发生时刻枚举（不依赖 Qt，便于单测）
# ---------------------------------------------------------------------------
def matches_day(r: Reminder, day: str) -> bool:
    """这条提醒在 day（YYYY-MM-DD）该不该响。"""
    if r.kind == KIND_ONCE:
        return r.date_key == day if r.date_key else True
    if r.kind == KIND_WEEKLY:
        try:
            wd = date.fromisoformat(day).weekday()   # 0 = 周一
        except ValueError:
            return False
        return bool(r.weekdays >> wd & 1)
    return True


def occurrences(r: Reminder, start_ts: float, end_ts: float) -> list[float]:
    """列出这条提醒在 [start_ts, end_ts] 区间内所有应该响的时刻。"""
    out: list[float] = []
    try:
        hh, mm = (int(x) for x in r.time_hhmm.split(":"))
    except Exception:
        return out
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        return out
    d = datetime.fromtimestamp(start_ts).date()
    end_d = datetime.fromtimestamp(end_ts).date()
    guard = 0
    while d <= end_d and guard < 400:
        guard += 1
        if matches_day(r, d.isoformat()):
            ts = datetime.combine(d, dtime(hh, mm)).timestamp()
            if start_ts <= ts <= end_ts:
                out.append(ts)
        d += timedelta(days=1)
    return out


class ReminderScheduler(QObject):
    fired = Signal(object, bool, str)     # (提醒, 是否补提醒, 展示文案)
    missed_report = Signal(int)           # 超出宽限期、被跳过的数量

    def __init__(self, storage: Storage, store: ReminderStore, parent=None) -> None:
        super().__init__(parent)
        self.storage = storage
        self.store = store
        self._temps: list[Reminder] = []
        self._missed_pending = 0

        self._tick = QTimer(self)
        self._tick.setInterval(TICK_MS)
        self._tick.timeout.connect(self._on_tick)

        self._align = QTimer(self)
        self._align.setSingleShot(True)
        self._align.timeout.connect(self._on_align)

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        self._tick.start()
        self._schedule_align()

    def stop(self) -> None:
        self._tick.stop()
        self._align.stop()

    def _schedule_align(self) -> None:
        now = time.time()
        delay = (60.0 - (now % 60.0)) * 1000.0 + ALIGN_SLACK_MS
        self._align.start(int(max(200.0, delay)))

    def _on_align(self) -> None:
        self.check()
        self._schedule_align()

    def _on_tick(self) -> None:
        self.check()

    # ------------------------------------------------------------ 检查
    def check(self, now: float | None = None) -> int:
        now = now if now is not None else time.time()
        cur = _hhmm(now)
        day = _day(now)
        fired = 0

        for r in self._items():
            if not self._matches_day(r, day):
                continue
            if r.time_hhmm != cur:
                continue
            key = r.dedup_key(day)
            if r.last_fired_key == key:
                continue
            self._fire(r, day, key, catch_up=False)
            fired += 1

        self._remember_check(now)
        return fired

    def catch_up(self, start_ts: float | None = None, end_ts: float | None = None) -> int:
        """开机 / 唤醒 / 解锁后补一次；宽限期内的补提醒，超出的只计数。"""
        now = end_ts if end_ts is not None else time.time()
        if start_ts is None:
            last = self.storage.get_meta("last_check_ts")
            start_ts = float(last) if isinstance(last, (int, float)) else now - 3600.0
        if start_ts >= now:
            self._remember_check(now)
            return 0

        missed = 0
        fired = 0
        cut = start_ts + 1.0   # 避免重复处理边界那一分钟

        for r in self._items():
            for occ in self._occurrences(r, cut, now):
                day = _day(occ)
                key = r.dedup_key(day)
                if r.last_fired_key == key:
                    continue
                age_min = (now - occ) / 60.0
                if age_min <= r.grace_minutes():
                    self._fire(r, day, key, catch_up=True, scheduled_ts=occ)
                    fired += 1
                else:
                    missed += 1

        if missed:
            self._missed_pending += missed
            log.info("错过 %d 条提醒（超出宽限期，不打扰）", missed)
        self._remember_check(now)
        return fired

    def take_missed(self) -> int:
        n = self._missed_pending
        self._missed_pending = 0
        return n

    # ------------------------------------------------------------ 内部
    def _items(self) -> list[Reminder]:
        out = [r for r in self.store.enabled() if r.id is not None and r.id > 0]
        out.extend(self._temps)
        return out

    def _matches_day(self, r: Reminder, day: str) -> bool:
        return matches_day(r, day)

    def _occurrences(self, r: Reminder, start_ts: float, end_ts: float) -> list[float]:
        return occurrences(r, start_ts, end_ts)

    def _fire(
        self,
        r: Reminder,
        day: str,
        key: str,
        catch_up: bool,
        scheduled_ts: float | None = None,
    ) -> None:
        from care import lines

        text = r.message or "时间到了"
        if r.action == "eat" and catch_up:
            text = lines.pick("remind_eat_late")
        elif catch_up:
            when = time.strftime("%H:%M", time.localtime(scheduled_ts or time.time()))
            text = f"刚才 {when} 我提醒过一次：{text}"

        if r.id and r.id > 0:
            self.store.set_fired(r, key)
        elif r in self._temps:
            self._temps.remove(r)

        log.info("触发提醒 %s %s catch_up=%s", r.time_hhmm, text[:20], catch_up)
        self.fired.emit(r, catch_up, text)

    def _remember_check(self, now: float) -> None:
        self.storage.set_meta("last_check_ts", float(now))
        self.storage.flush()

    # ------------------------------------------------------------ 稍后再提醒
    def add_temp(self, r: Reminder) -> None:
        self._temps.append(r)

    def clear_temps(self) -> None:
        self._temps.clear()

    def temp_count(self) -> int:
        return len(self._temps)


def action_to_clip(action: str) -> str | None:
    """提醒动作 -> 动画名。"""
    return {
        "eat": "eat",
        "stretch": "stretch",
        "wave": "wave",
        "yawn": "yawn",
        "sleep": "yawn",
        "none": None,
    }.get(action, None)


def should_sleep_after(action: str) -> bool:
    return action in ("sleep",)
