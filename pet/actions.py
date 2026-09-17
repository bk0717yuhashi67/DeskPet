"""动作调度：什么时候让企鹅"自己动一下"。

核心是克制——随机轻动作每小时有上限，免打扰/心流/睡眠/游戏中一律不触发。
"""
from __future__ import annotations

import math

from . import clips as C

MIN_GAP = 45.0            # 秒，两次随机动作的最小间隔
MAX_GAP = 120.0           # 秒，最大间隔
HISTORY_WINDOW = 3600.0   # 秒，统计窗口


class RandomScheduler:
    """决定下一个随机轻动作何时、以何种形式出现。"""

    def __init__(self, per_hour: int = 3) -> None:
        self.per_hour = max(0, per_hour)
        self._next_at: float = 0.0
        self._history: list[float] = []

    def reconfigure(self, per_hour: int) -> None:
        self.per_hour = max(0, per_hour)

    def arm(self, now: float) -> None:
        self._next_at = now + self._gap(now)

    def _gap(self, now: float) -> float:
        # 确定性伪随机（可复现，方便排查）
        f = _frac(math.sin((now * 7.919 + 1.234) * 12345.6789))
        base = MIN_GAP + f * (MAX_GAP - MIN_GAP)
        if self.per_hour <= 0:
            return HISTORY_WINDOW
        # 把每小时预算均匀铺开。否则前三下会扎堆出现然后安静一小时，
        # "突然连续动三次"比稀疏但均匀的节奏更烦人。
        floor = HISTORY_WINDOW / self.per_hour * 0.85
        return max(base, floor)

    def _trim(self, now: float) -> None:
        cutoff = now - HISTORY_WINDOW
        self._history = [t for t in self._history if t >= cutoff]

    def remaining(self, now: float) -> int:
        self._trim(now)
        return max(0, self.per_hour - len(self._history))

    def poll(self, now: float, blocked: bool) -> str | None:
        """到点且未被限制时返回一个动作名，否则 None。"""
        if self._next_at <= 0.0:
            self.arm(now)
            return None
        if now < self._next_at:
            return None

        # 被限制：推迟一小会儿再试，不要一直卡在到点状态
        if blocked:
            self._next_at = now + 20.0
            return None

        self._trim(now)
        if len(self._history) >= self.per_hour:
            self._next_at = now + 60.0
            return None

        pool = [n for n in C.RANDOM_LIGHT if C.get(n) is not None]
        if not pool:
            self._next_at = now + 60.0
            return None

        f = _frac(math.sin((now * 3.117 + 0.7) * 9876.5432))
        name = pool[int(f * len(pool)) % len(pool)]
        self._history.append(now)
        self.arm(now)
        return name


def _frac(x: float) -> float:
    return x - math.floor(x)
