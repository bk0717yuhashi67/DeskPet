"""指标 -> 心情。

心情决定三件事：
  1. 企鹅待机时的姿势微调（表情、动作快慢）
  2. 是否主动说话、说什么
  3. 随机动作的频率（心流中直接归零——"陪着你但不打扰你"）
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from monitor.analyzer import Metrics


class Mood(str, Enum):
    CONCERN_STAYUP = "concern_stayup"    # 熬夜关切
    WORRIED_TIRED = "worried_tired"      # 疲劳关切
    FOCUS_COMPANION = "focus_companion"  # 心流陪伴（安静）
    FRUSTRATED = "frustrated"            # 反复修改，可能卡住了
    DISTRACTED = "distracted"            # 分心 / 窗口太乱
    FOCUSED = "focused"                  # 状态不错
    CHEERFUL = "cheerful"                # 干劲十足
    SLEEPY = "sleepy"                    # 长时间没动
    CALM = "calm"                        # 默认
    SLEEPING = "sleeping"


LABELS = {
    Mood.CONCERN_STAYUP: "熬夜关切",
    Mood.WORRIED_TIRED: "疲劳关切",
    Mood.FOCUS_COMPANION: "专注陪伴",
    Mood.FRUSTRATED: "有点卡住",
    Mood.DISTRACTED: "有点分心",
    Mood.FOCUSED: "状态不错",
    Mood.CHEERFUL: "干劲十足",
    Mood.SLEEPY: "有点困",
    Mood.CALM: "平静",
    Mood.SLEEPING: "睡着了",
}


@dataclass
class MoodEffect:
    action_rate: float = 1.0     # 随机动作频率倍率，0 = 完全安静
    anim_rate: float = 1.0       # 动画播放速率（疲劳时整体放慢）
    proactive: bool = True       # 是否允许主动气泡
    face: dict = field(default_factory=dict)  # 强制表情微调（叠加在动作之上）


EFFECTS: dict[Mood, MoodEffect] = {
    Mood.CONCERN_STAYUP: MoodEffect(0.4, 0.9, True, {"eye_squint": 0.4}),
    Mood.WORRIED_TIRED: MoodEffect(0.35, 0.8, True, {}),
    Mood.FOCUS_COMPANION: MoodEffect(0.0, 0.95, False, {}),
    Mood.FRUSTRATED: MoodEffect(0.6, 1.0, True, {"eye_squint": 0.35}),
    Mood.DISTRACTED: MoodEffect(0.8, 1.0, True, {}),
    Mood.FOCUSED: MoodEffect(0.5, 1.0, True, {}),
    Mood.CHEERFUL: MoodEffect(1.4, 1.05, True, {"blush": 0.25}),
    Mood.SLEEPY: MoodEffect(0.3, 0.85, True, {"eye_open": 0.7}),
    Mood.CALM: MoodEffect(1.0, 1.0, True, {}),
    Mood.SLEEPING: MoodEffect(0.0, 1.0, False, {}),
}


def evaluate(m: Metrics, now: float | None = None, idle_now: float = 0.0) -> Mood:
    """按优先级依次判定，先命中先返回。"""
    now = now if now is not None else time.time()
    hour = time.localtime(now).tm_hour

    # 1. 深夜还在电脑前：最需要被关心的事情
    if m.stayup > 70 or 1 <= hour < 5:
        return Mood.CONCERN_STAYUP

    # 2. 重度疲劳
    if m.fatigue > 75:
        return Mood.WORRIED_TIRED

    # 3. 心流中：安静陪伴，一句话都不说
    if m.focus > 70 and m.flow > 0.6 and m.activity > 40:
        return Mood.FOCUS_COMPANION

    # 4. 反复修改，可能卡住了
    if m.br > 0.25 and m.activity > 40 and m.keystrokes > 300:
        return Mood.FRUSTRATED

    # 5. 窗口开得很乱
    if m.distraction > 65 and m.active_s > 1800:
        return Mood.DISTRACTED

    # 6. 状态好
    if m.focus > 75 and m.active_s > 1200:
        return Mood.FOCUSED

    if m.activity > 60 and m.fatigue < 45:
        return Mood.CHEERFUL

    if idle_now >= 300 or (m.active_s > 60 and m.active_s < 300 and m.keystrokes < 30):
        return Mood.SLEEPY

    return Mood.CALM


class MoodTracker:
    """带滞回的心情跟踪：跨档需要连续 2 次（默认 10 分钟）确认，防止临界反复横跳。"""

    def __init__(self, confirmations: int = 2) -> None:
        self.mood: Mood = Mood.CALM
        self.effect: MoodEffect = EFFECTS[Mood.CALM]
        self._candidate: Mood | None = None
        self._hits = 0
        self.confirmations = max(1, confirmations)

    def update(self, m: Metrics, now: float | None = None, idle_now: float = 0.0) -> Mood:
        target = evaluate(m, now, idle_now)
        if target == self.mood:
            self._candidate = None
            self._hits = 0
            return self.mood

        if self._candidate == target:
            self._hits += 1
        else:
            self._candidate = target
            self._hits = 1

        # 变好的心情（更轻松）可以更快生效；变差的（关切类）也允许快速生效，
        # 只有"需要安静/放松"的中性切换需要确认。
        urgent = target in (Mood.CONCERN_STAYUP, Mood.WORRIED_TIRED)
        if urgent or self._hits >= self.confirmations:
            self.mood = target
            self.effect = EFFECTS.get(target, EFFECTS[Mood.CALM])
            self._candidate = None
            self._hits = 0
        return self.mood

    def force(self, mood: Mood) -> None:
        self.mood = mood
        self.effect = EFFECTS.get(mood, EFFECTS[Mood.CALM])
        self._candidate = None
        self._hits = 0
