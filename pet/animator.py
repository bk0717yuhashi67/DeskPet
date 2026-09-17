"""动画引擎：关键帧插值 + 分层叠加 + 打断混合。

三层结构：
    base    当前动作（跳跃 / 吃饭 / 睡眠…）
    overlay 常驻小动作，永远在跑，让待机也有生命感（呼吸 / 眨眼 / 注视 / 围脖波动）
    noise   极轻微抖动，只在待机与挣扎时启用，消除机械感

打断时用 120ms 从"当前合成姿势"混到新动作，杜绝姿势跳变。
"""
from __future__ import annotations

import math

from . import clips as C
from . import easing as E
from .pose import Pose, pose_lerp, pose_from

BREATH_CYCLE = 2.6          # 秒
BREATH_CYCLE_SLEEP = 4.2
BLINK_MIN, BLINK_MAX = 2.4, 6.5
GAZE_LIMIT = 2.2
GAZE_SKIP_CLIPS = {"look_around", "dizzy", "peek", "sad"}


def _eval(clip: C.Clip, t: float) -> Pose:
    """在关键帧之间插值。t 已归一化到 [0,1]。"""
    keys = clip.keys
    if not keys:
        return Pose()
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)

    lo, hi = 0, len(keys) - 1
    if t <= keys[0].t:
        return keys[0].pose.copy()
    if t >= keys[hi].t:
        return keys[hi].pose.copy()
    # 关键帧数量很少，线性扫描足够
    for i in range(len(keys) - 1):
        a, b = keys[i], keys[i + 1]
        if a.t <= t <= b.t:
            span = b.t - a.t
            local = 0.0 if span <= 1e-9 else (t - a.t) / span
            return pose_lerp(a.pose, b.pose, a.ease(local))
    return keys[hi].pose.copy()


class Animator:
    """把"时间 + 当前动作 + 外部输入"合成为一个 Pose。"""

    def __init__(self) -> None:
        self.clip: C.Clip | None = None
        self.clip_start: float = 0.0
        self.rate: float = 1.0

        self._blend_from: Pose | None = None
        self._blend_start: float = 0.0
        self._blend_ms: float = 120.0

        self._next_blink: float = 0.0
        self._blink_until: float = -1.0

        self.gaze_x: float = 0.0      # -1..1
        self.gaze_y: float = 0.0
        self.look_strength: float = 0.0   # 0..1，鼠标靠近时转头看过来的强度

        self.just_finished: str | None = None
        self.on_finish = None          # Callable[[str], None]，一次性动作播完时回调
        self._finish_reported = False
        self._last_pose: Pose | None = None
        self._noise_on: bool = True

    # ---------------- 播放控制 ----------------
    @property
    def current_name(self) -> str | None:
        return self.clip.name if self.clip else None

    def play(
        self,
        name: str | C.Clip | None,
        now: float,
        blend_ms: float = 120.0,
        snapshot: Pose | None = None,
    ) -> None:
        """切换到新动作。snapshot 用于传入"当前正在显示的姿势"作为混合起点。"""
        clip = name if isinstance(name, C.Clip) else (C.get(name) if name else None)
        if clip is None:
            self.clip = None
            return
        if self.clip is not None and clip.name == self.clip.name and clip.loop:
            return  # 已在循环同一动作，不重头开始

        if blend_ms > 0:
            base = snapshot if snapshot is not None else self._last_pose
            self._blend_from = base.copy() if base is not None else None
            self._blend_start = now
            self._blend_ms = blend_ms
        else:
            self._blend_from = None

        self.clip = clip
        self.clip_start = now
        self.just_finished = None
        self._finish_reported = False

    def stop(self, now: float, blend_ms: float = 120.0, snapshot: Pose | None = None) -> None:
        base = snapshot if snapshot is not None else self._last_pose
        if self.clip is not None and blend_ms > 0 and base is not None:
            self._blend_from = base.copy()
            self._blend_start = now
            self._blend_ms = blend_ms
        else:
            self._blend_from = None
        self.clip = None

    def progress(self, now: float) -> float:
        if self.clip is None:
            return 1.0
        elapsed = (now - self.clip_start) * 1000.0 * self.rate
        return min(1.0, elapsed / max(1, self.clip.dur))

    # ---------------- 每帧求值 ----------------
    def tick(self, now: float) -> Pose:
        self.just_finished = None
        base = self._tick_base(now)
        target = self._apply_blend(base, now)
        self._apply_breath(target, now)
        self._apply_blink(target, now)
        self._apply_gaze(target)
        self._apply_noise(target, now)
        self._last_pose = target
        return target

    # ---------------- 内部 ----------------
    def _tick_base(self, now: float) -> Pose:
        clip = self.clip
        if clip is None:
            return Pose()

        elapsed_ms = (now - self.clip_start) * 1000.0 * self.rate
        dur = max(1, clip.dur)
        if clip.loop:
            t = (elapsed_ms % dur) / dur
        else:
            if elapsed_ms >= dur:
                t = 1.0
                if not self._finish_reported:
                    self._finish_reported = True
                    self.just_finished = clip.name
                    if self.on_finish is not None:
                        try:
                            self.on_finish(clip.name)
                        except Exception:
                            pass
            else:
                t = elapsed_ms / dur

        pose = _eval(clip, t)
        if clip.prop:
            pose.prop = clip.prop
        return pose

    def _apply_blend(self, base: Pose, now: float) -> Pose:
        if self._blend_from is None:
            return base
        u = (now - self._blend_start) * 1000.0 / max(1.0, self._blend_ms)
        if u >= 1.0:
            self._blend_from = None
            return base
        w = E.ease_out_quad(max(0.0, u))
        return pose_lerp(self._blend_from, base, w)

    def _apply_breath(self, pose: Pose, now: float) -> None:
        sleeping = self.current_name == "sleep_loop"
        cycle = BREATH_CYCLE_SLEEP if sleeping else BREATH_CYCLE
        phase = (now / cycle) % 1.0
        s = math.sin(2.0 * math.pi * phase)
        up = max(0.0, s)
        amp = 0.040 if sleeping else 0.026
        pose.body_sy += amp * s
        pose.body_y += -(2.4 if sleeping else 1.3) * up
        pose.head_y += -(1.4 if sleeping else 0.8) * up
        pose.scarf_wave = (now * 0.42) % 1.0

    def _apply_blink(self, pose: Pose, now: float) -> None:
        if self._blink_until < 0.0:
            self._schedule_blink(now)
        if now < self._blink_until:
            u = 1.0 - (self._blink_until - now) / 0.13
            # 0 -> 1 -> 0 的三角波，两端用缓动磨圆
            k = 1.0 - abs(2.0 * min(1.0, max(0.0, u)) - 1.0)
            pose.eye_open *= max(0.0, 1.0 - E.ease_out_quad(k))
        elif self._blink_until > 0.0 and now >= self._blink_until:
            self._schedule_blink(now)

    def _schedule_blink(self, now: float) -> None:
        # 用确定性伪随机，避免引入 random 依赖导致行为不可复现
        h = math.sin((now * 12.9898 + 3.14159) * 43758.5453)
        frac = h - math.floor(h)
        self._blink_until = now + BLINK_MIN + frac * (BLINK_MAX - BLINK_MIN)

    def _apply_gaze(self, pose: Pose) -> None:
        if self.current_name in GAZE_SKIP_CLIPS:
            return
        gx = max(-1.0, min(1.0, self.gaze_x))
        gy = max(-1.0, min(1.0, self.gaze_y))
        pose.pupil_x = max(-GAZE_LIMIT * 1.2, min(GAZE_LIMIT * 1.2, pose.pupil_x + gx * GAZE_LIMIT))
        pose.pupil_y = max(-GAZE_LIMIT, min(GAZE_LIMIT, pose.pupil_y + gy * GAZE_LIMIT * 0.7))

        # 鼠标靠得近时，头也跟着转过来一点——这条是"活物感"的关键
        s = max(0.0, min(1.0, self.look_strength))
        if s > 0.01:
            pose.head_x += gx * 1.8 * s
            pose.head_rot += gx * 4.5 * s
            pose.eye_open = min(1.1, pose.eye_open + 0.06 * s)

    def _apply_noise(self, pose: Pose, now: float) -> None:
        if not self._noise_on:
            return
        if self.current_name not in (None, "struggle", "waddle", "peek"):
            return
        pose.body_x += math.sin(now * 2.7) * 0.28 + math.sin(now * 1.31 + 1.7) * 0.18
        pose.body_y += math.sin(now * 3.4 + 0.6) * 0.16


class PoseSnapshotter:
    """保存最近一帧的姿势，供打断混合使用（让 Animator 保持无 Qt 依赖）。"""

    def __init__(self) -> None:
        self.pose: Pose = Pose()

    def update(self, pose: Pose) -> None:
        self.pose = pose.copy()
