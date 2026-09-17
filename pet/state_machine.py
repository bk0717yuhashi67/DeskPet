"""状态机：状态与优先级仲裁，决定"谁能打断谁"。

状态：
    IDLE   待机
    ACT    正在播放一个主动/被动动作
    DRAG   被鼠标拖动
    GAME   抛球小游戏中
    REMIND 提醒播报中
    SLEEP  睡眠

免打扰（QUIET）不是一个独立状态，而是一组"理由"的集合，可叠加在任意状态上：
    手动安静 / 全屏程序 / 会议进程 / 深夜时段 / 心流中
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

IDLE = "idle"
ACT = "act"
DRAG = "drag"
GAME = "game"
REMIND = "remind"
SLEEP = "sleep"

PRIORITY = {
    DRAG: 100,
    GAME: 80,
    REMIND: 60,
    ACT: 40,
    IDLE: 20,
    SLEEP: 30,
}

# 这些状态下不应该被随机动作/主动气泡打扰
BLOCKING_FOR_PROACTIVE = {DRAG, GAME, REMIND, SLEEP}

# 免打扰理由
R_FULLSCREEN = "fullscreen"  # 全屏程序（视频/游戏/演示）
R_MEETING = "meeting"        # 会议进程
R_FOCUS = "focus"            # 用户正在心流中
R_BUSY = "busy"              # 正在编译/下载/渲染
R_NIGHT = "night"            # 深夜睡眠时段


class StateMachine(QObject):
    changed = Signal(str)          # 状态变化（新状态名）
    quiet_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._state = IDLE
        self._quiet_reasons: set[str] = set()
        self._pending: list[tuple[str, str]] = []
        self.paused = False

    # ---------------- 状态 ----------------
    @property
    def state(self) -> str:
        return self._state

    def is_state(self, *names: str) -> bool:
        return self._state in names

    def request(self, state: str, source: str = "", force: bool = False) -> bool:
        """请求进入某状态。优先级不足时排队，等当前状态结束后补播。"""
        if state == self._state:
            return True
        if not force and PRIORITY.get(state, 0) < PRIORITY.get(self._state, 0):
            if state == REMIND:
                self._pending.append((state, source))
            return False
        old = self._state
        self._state = state
        if old != state:
            self.changed.emit(state)
        return True

    def back_to_idle(self) -> None:
        self.request(IDLE, force=True)

    def pop_pending(self) -> tuple[str, str] | None:
        if self._pending:
            return self._pending.pop(0)
        return None

    # ---------------- 免打扰 ----------------
    def set_quiet(self, reason: str, on: bool) -> None:
        before = self.is_quiet
        if on:
            self._quiet_reasons.add(reason)
        else:
            self._quiet_reasons.discard(reason)
        if before != self.is_quiet:
            self.quiet_changed.emit(self.is_quiet)

    @property
    def quiet_reasons(self) -> set[str]:
        return set(self._quiet_reasons)

    @property
    def is_quiet(self) -> bool:
        if self._state == SLEEP:
            return True
        # 抓球时用户正在玩，不算被打扰
        if self._state == GAME:
            return False
        return bool(self._quiet_reasons)

    @property
    def is_proactive_blocked(self) -> bool:
        """是否禁止随机动作与主动气泡。"""
        if self.paused or self._state in BLOCKING_FOR_PROACTIVE:
            return True
        return self.is_quiet
