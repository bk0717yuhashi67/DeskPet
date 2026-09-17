"""打扰预算：决定"现在能不能主动说句话"。

宁可少说，也不要烦人。所以把限制放在一个地方统一裁决，而不是散落在各个功能里。
"""
from __future__ import annotations

import time

from app.config import config
from . import lines


class BubbleBudget:
    """主动气泡的每日上限 + 最小间隔。免打扰由调用方先判断，这里只管"说多少"。"""

    def __init__(self) -> None:
        self.day = time.strftime("%Y-%m-%d")
        self.used = 0
        self.last_at = 0.0
        self._cfg_per_day = int(config.get("bubble_per_day", 8) or 8)
        self._cfg_gap = float(config.get("bubble_min_gap_min", 8) or 8) * 60.0

    def reconfigure(self) -> None:
        self._cfg_per_day = int(config.get("bubble_per_day", 8) or 8)
        self._cfg_gap = float(config.get("bubble_min_gap_min", 8) or 8) * 60.0

    def _rollover(self, now: float) -> None:
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if day != self.day:
            self.day = day
            self.used = 0
            self.last_at = 0.0

    def remaining(self, now: float | None = None) -> int:
        now = now if now is not None else time.time()
        self._rollover(now)
        return max(0, self._cfg_per_day - self.used)

    def can_speak(self, now: float | None = None, urgent: bool = False) -> bool:
        now = now if now is not None else time.time()
        self._rollover(now)
        if urgent:
            # 紧急提醒不受每日上限约束，但仍有 60 秒的防抖
            return now - self.last_at >= 60.0
        if self.used >= self._cfg_per_day:
            return False
        return now - self.last_at >= self._cfg_gap

    def record(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        self._rollover(now)
        self.used += 1
        self.last_at = now

    def next_available_in(self, now: float | None = None) -> float:
        now = now if now is not None else time.time()
        self._rollover(now)
        if self.used >= self._cfg_per_day:
            return max(0.0, 86400 - (now % 86400))
        return max(0.0, self._cfg_gap - (now - self.last_at))

    def summary(self, now: float | None = None) -> str:
        now = now if now is not None else time.time()
        return f"今日主动消息 {self.used}/{self._cfg_per_day}"


class ActionBudget:
    """每小时随机动作上限。"""

    def __init__(self) -> None:
        self.per_hour = int(config.get("random_action_per_hour", 3) or 3)
        self._history: list[float] = []

    def reconfigure(self) -> None:
        self.per_hour = int(config.get("random_action_per_hour", 3) or 3)

    def allowed(self, now: float) -> bool:
        cutoff = now - 3600.0
        self._history = [t for t in self._history if t >= cutoff]
        return len(self._history) < self.per_hour

    def record(self, now: float) -> None:
        self._history.append(now)
