"""动作关键帧库。

这里只有数据，没有逻辑。每个 Clip 是一串关键帧（归一化时间 t ∈ [0,1]），
求值时由 animator 在相邻关键帧之间按缓动插值。

写法约定：
    raw_clip(name, dur, loop, [(t, {pitch overrides}, "easing"), ...])
未写出的 Pose 字段从默认姿势继承（Pose()），因此每个关键帧只需写变化的部分。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from . import easing as E
from .pose import (
    PUPIL_R,  # noqa: F401
    PROP_BALL,
    PROP_HEART,
    PROP_NONE,
    PROP_STAR,
    PROP_Z,
    Pose,
    pose_from,
)


@dataclass(slots=True)
class Key:
    t: float
    pose: Pose
    ease: Callable[[float], float]


@dataclass(slots=True)
class Clip:
    name: str
    dur: int                 # 毫秒
    loop: bool
    keys: list[Key]
    on_end: str | None = None
    prop: int = PROP_NONE    # 整个 clip 期间生效的道具
    rate: float = 1.0        # 播放速率（疲劳时整体放慢）


_CLIPS: dict[str, Clip] = {}


def raw_clip(
    name: str,
    dur: int,
    loop: bool,
    spec: list[tuple],
    on_end: str | None = None,
    prop: int = PROP_NONE,
) -> Clip:
    keys = [Key(float(t), pose_from(d), E.get(ease)) for t, d, ease in spec]
    keys.sort(key=lambda k: k.t)
    clip = Clip(name=name, dur=dur, loop=loop, keys=keys, on_end=on_end, prop=prop)
    _CLIPS[name] = clip
    return clip


# ---------------------------------------------------------------------------
# 眨眼
# ---------------------------------------------------------------------------
raw_clip("blink", 130, False, [
    (0.00, {"eye_open": 1.0}, "linear"),
    (0.30, {"eye_open": 0.0}, "in_quad"),
    (0.48, {"eye_open": 0.0}, "linear"),
    (1.00, {"eye_open": 1.0}, "out_quad"),
])

raw_clip("blink_double", 320, False, [
    (0.00, {"eye_open": 1.0}, "linear"),
    (0.14, {"eye_open": 0.0}, "in_quad"),
    (0.27, {"eye_open": 1.0}, "out_quad"),
    (0.42, {"eye_open": 0.0}, "in_quad"),
    (0.58, {"eye_open": 1.0}, "out_quad"),
    (1.00, {"eye_open": 1.0}, "linear"),
])

# ---------------------------------------------------------------------------
# 待机小动作
# ---------------------------------------------------------------------------
raw_clip("look_around", 1800, False, [
    (0.00, {"head_rot": 0.0, "pupil_x": 0.0}, "linear"),
    (0.17, {"head_rot": -12.0, "pupil_x": -2.4, "head_x": -1.5}, "out_quad"),
    (0.48, {"head_rot": -12.0, "pupil_x": -2.4, "head_x": -1.5}, "linear"),
    (0.70, {"head_rot": 14.0, "pupil_x": 2.6, "head_x": 1.5}, "in_out_cubic"),
    (0.88, {"head_rot": 14.0, "pupil_x": 2.6, "head_x": 1.5}, "linear"),
    (1.00, {"head_rot": 0.0, "pupil_x": 0.0, "head_x": 0.0}, "in_out_cubic"),
])

raw_clip("wing_flap", 760, False, [
    (0.00, {"wing_l": 12.0, "wing_r": -12.0}, "linear"),
    (0.13, {"wing_l": 58.0, "wing_r": -58.0}, "in_out_cubic"),
    (0.26, {"wing_l": 12.0, "wing_r": -12.0}, "in_out_cubic"),
    (0.39, {"wing_l": 58.0, "wing_r": -58.0}, "in_out_cubic"),
    (0.52, {"wing_l": 12.0, "wing_r": -12.0}, "in_out_cubic"),
    (0.65, {"wing_l": 58.0, "wing_r": -58.0}, "in_out_cubic"),
    (0.78, {"wing_l": 12.0, "wing_r": -12.0}, "in_out_cubic"),
    (1.00, {"wing_l": 12.0, "wing_r": -12.0}, "linear"),
])

raw_clip("hop", 640, False, [
    (0.00, {"body_sx": 1.0, "body_sy": 1.0, "body_y": 0.0, "wing_l": 12.0, "wing_r": -12.0}, "linear"),
    (0.19, {"body_sx": 1.12, "body_sy": 0.88, "body_y": 6.0, "wing_l": 20.0, "wing_r": -20.0}, "out_quad"),
    (0.61, {"body_sx": 0.94, "body_sy": 1.08, "body_y": -46.0, "wing_l": 70.0, "wing_r": -70.0}, "out_quad"),
    (1.00, {"body_sx": 1.0, "body_sy": 1.0, "body_y": 0.0, "wing_l": 12.0, "wing_r": -12.0}, "out_back"),
])

raw_clip("spin", 760, False, [
    (0.00, {"body_sx": 1.0, "body_rot": 0.0, "flip": 1.0, "wing_l": 20.0, "wing_r": -20.0}, "linear"),
    (0.25, {"body_sx": 0.38, "body_rot": -6.0, "flip": 1.0}, "in_out_cubic"),
    (0.50, {"body_sx": 0.16, "body_rot": 0.0, "flip": 1.0}, "in_out_cubic"),
    (0.75, {"body_sx": 0.38, "body_rot": 6.0, "flip": -1.0}, "in_out_cubic"),
    (1.00, {"body_sx": 1.0, "body_rot": 0.0, "flip": -1.0, "wing_l": 20.0, "wing_r": -20.0}, "out_quad"),
])

raw_clip("stretch", 1520, False, [
    (0.00, {"wing_l": 12.0, "wing_r": -12.0, "body_sy": 1.0, "head_rot": 0.0, "eye_open": 1.0}, "linear"),
    (0.35, {"wing_l": 158.0, "wing_r": -158.0, "body_sy": 1.10, "head_rot": 6.0, "eye_open": 0.35, "beak_open": 0.3}, "out_cubic"),
    (0.62, {"wing_l": 158.0, "wing_r": -158.0, "body_sy": 1.12, "head_rot": 6.0, "eye_open": 0.3, "beak_open": 0.3}, "linear"),
    (0.86, {"wing_l": 20.0, "wing_r": -20.0, "body_sy": 0.97, "head_rot": -2.0, "eye_open": 1.0}, "out_quad"),
    (1.00, {"wing_l": 12.0, "wing_r": -12.0, "body_sy": 1.0, "head_rot": 0.0, "eye_open": 1.0}, "out_back"),
])

raw_clip("yawn", 1420, False, [
    (0.00, {"eye_open": 1.0, "beak_open": 0.0, "head_rot": 0.0}, "linear"),
    (0.30, {"eye_open": 0.22, "beak_open": 1.0, "head_rot": -10.0, "body_sy": 1.02}, "in_out_cubic"),
    (0.68, {"eye_open": 0.20, "beak_open": 1.0, "head_rot": -10.0, "body_sy": 1.02}, "linear"),
    (0.88, {"eye_open": 0.75, "beak_open": 0.2, "head_rot": -3.0, "body_sy": 1.0}, "out_quad"),
    (1.00, {"eye_open": 1.0, "beak_open": 0.0, "head_rot": 0.0, "body_sy": 1.0}, "out_quad"),
])

raw_clip("waddle", 920, True, [
    (0.00, {"foot_l_y": -3.5, "foot_r_y": 3.5, "body_y": 0.0, "body_rot": -3.0, "wing_l": 22.0, "wing_r": -22.0}, "linear"),
    (0.25, {"foot_l_y": 0.0, "foot_r_y": 0.0, "body_y": -2.5, "body_rot": 0.0}, "in_out_sine"),
    (0.50, {"foot_l_y": 3.5, "foot_r_y": -3.5, "body_y": 0.0, "body_rot": 3.0}, "in_out_sine"),
    (0.75, {"foot_l_y": 0.0, "foot_r_y": 0.0, "body_y": -2.5, "body_rot": 0.0}, "in_out_sine"),
    (1.00, {"foot_l_y": -3.5, "foot_r_y": 3.5, "body_y": 0.0, "body_rot": -3.0, "wing_l": 22.0, "wing_r": -22.0}, "linear"),
])

raw_clip("peek", 1600, True, [
    (0.00, {"head_rot": -14.0, "pupil_x": -2.6, "body_rot": -2.0, "eye_open": 1.1}, "in_out_sine"),
    (0.50, {"head_rot": 15.0, "pupil_x": 2.6, "body_rot": 2.0, "eye_open": 0.85}, "in_out_sine"),
    (1.00, {"head_rot": -14.0, "pupil_x": -2.6, "body_rot": -2.0, "eye_open": 1.1}, "in_out_sine"),
])

# ---------------------------------------------------------------------------
# 被动互动
# ---------------------------------------------------------------------------
raw_clip("pat_belly", 540, False, [
    (0.00, {"body_sy": 1.0, "blush": 0.0, "beak_open": 0.0, "wing_l": 12.0, "wing_r": -12.0, "eye_open": 1.0}, "linear"),
    (0.16, {"body_sy": 1.10, "body_sx": 0.94, "blush": 0.85, "beak_open": 0.5, "wing_l": 36.0, "wing_r": -36.0, "eye_open": 0.35, "eye_squint": 0.5}, "spring"),
    (0.42, {"body_sy": 0.96, "body_sx": 1.03, "blush": 0.7}, "spring"),
    (0.70, {"body_sy": 1.03, "body_sx": 0.99, "blush": 0.5}, "spring"),
    (1.00, {"body_sy": 1.0, "body_sx": 1.0, "blush": 0.32, "beak_open": 0.06, "wing_l": 12.0, "wing_r": -12.0, "eye_open": 1.0, "eye_squint": 0.0}, "out_quad"),
])

raw_clip("struggle", 620, True, [
    (0.00, {"body_rot": -6.0, "foot_l_y": -6.0, "foot_r_y": 6.0, "eye_open": 1.15, "beak_open": 0.6, "wing_l": 42.0, "wing_r": -42.0}, "in_out_sine"),
    (0.50, {"body_rot": 6.0, "foot_l_y": 6.0, "foot_r_y": -6.0, "eye_open": 1.15, "beak_open": 0.6, "wing_l": 56.0, "wing_r": -56.0}, "in_out_sine"),
    (1.00, {"body_rot": -6.0, "foot_l_y": -6.0, "foot_r_y": 6.0, "eye_open": 1.15, "beak_open": 0.6, "wing_l": 42.0, "wing_r": -42.0}, "in_out_sine"),
])

raw_clip("throw_ball", 420, False, [
    (0.00, {"wing_r": -12.0, "body_rot": 0.0, "beak_open": 0.0}, "linear"),
    (0.45, {"wing_r": -152.0, "body_rot": 6.0, "beak_open": 0.45, "body_sx": 0.96, "body_sy": 1.04}, "out_cubic"),
    (1.00, {"wing_r": -12.0, "body_rot": 0.0, "beak_open": 0.0, "body_sx": 1.0, "body_sy": 1.0}, "out_quad"),
])

raw_clip("catch_ball", 380, False, [
    (0.00, {"wing_l": 12.0, "wing_r": -12.0, "body_y": 0.0, "blush": 0.0}, "linear"),
    (0.30, {"wing_l": 78.0, "wing_r": -78.0, "body_y": -7.0, "eye_open": 1.1}, "out_quad"),
    (0.60, {"wing_l": 62.0, "wing_r": -62.0, "body_sx": 1.09, "body_sy": 0.91, "blush": 0.6, "beak_open": 0.3}, "out_quad"),
    (1.00, {"wing_l": 12.0, "wing_r": -12.0, "body_y": 0.0, "body_sx": 1.0, "body_sy": 1.0, "blush": 0.25, "beak_open": 0.0}, "out_quad"),
])

raw_clip("dizzy", 900, True, [
    (0.00, {"body_rot": -10.0, "pupil_x": -2.4, "pupil_y": 1.4, "eye_open": 1.0}, "in_out_sine"),
    (0.50, {"body_rot": 10.0, "pupil_x": 2.4, "pupil_y": -1.4, "eye_open": 1.0}, "in_out_sine"),
    (1.00, {"body_rot": -10.0, "pupil_x": -2.4, "pupil_y": 1.4, "eye_open": 1.0}, "in_out_sine"),
], prop=PROP_STAR)

raw_clip("sad", 1200, False, [
    (0.00, {"head_y": 0.0, "head_rot": 0.0, "eye_open": 1.0, "wing_l": 12.0, "wing_r": -12.0, "eye_squint": 0.0}, "out_quad"),
    (0.30, {"head_y": 3.0, "head_rot": -6.0, "eye_open": 0.6, "eye_squint": 0.62, "wing_l": 26.0, "wing_r": -26.0}, "out_quad"),
    (1.00, {"head_y": 2.4, "head_rot": -5.0, "eye_open": 0.66, "eye_squint": 0.56, "wing_l": 25.0, "wing_r": -25.0}, "out_quad"),
])

raw_clip("celebrate", 1440, False, [
    (0.00, {"body_y": 0.0, "body_sx": 1.0, "body_sy": 1.0, "wing_l": 12.0, "wing_r": -12.0}, "linear"),
    (0.14, {"body_y": 5.0, "body_sx": 1.10, "body_sy": 0.88, "wing_l": 30.0, "wing_r": -30.0}, "out_quad"),
    (0.30, {"body_y": -40.0, "body_sx": 0.95, "body_sy": 1.06, "wing_l": 122.0, "wing_r": -122.0, "eye_open": 0.3, "blush": 0.5}, "out_quad"),
    (0.46, {"body_y": 0.0, "body_sx": 1.12, "body_sy": 0.90, "wing_l": 60.0, "wing_r": -60.0}, "out_quad"),
    (0.60, {"body_y": -28.0, "body_sx": 0.96, "body_sy": 1.04, "wing_l": 116.0, "wing_r": -116.0, "eye_open": 0.3}, "out_quad"),
    (0.76, {"body_y": 0.0, "body_sx": 1.10, "body_sy": 0.92, "wing_l": 40.0, "wing_r": -40.0}, "out_quad"),
    (1.00, {"body_y": 0.0, "body_sx": 1.0, "body_sy": 1.0, "wing_l": 12.0, "wing_r": -12.0, "eye_open": 1.0, "blush": 0.15}, "out_back"),
], prop=PROP_STAR)

# ---------------------------------------------------------------------------
# 睡眠 / 吃饭 / 仪式感
# ---------------------------------------------------------------------------
SLEEP_POSE = {
    "body_sy": 0.90,
    "body_y": 12.0,
    "head_rot": 20.0,
    "head_y": 5.0,
    "eye_open": 0.0,
    "wing_l": 30.0,
    "wing_r": -30.0,
}
raw_clip("sleep_loop", 3400, True, [
    (0.00, dict(SLEEP_POSE, body_sy=0.90), "in_out_sine"),
    (0.50, dict(SLEEP_POSE, body_sy=0.935, body_y=11.0), "in_out_sine"),
    (1.00, dict(SLEEP_POSE, body_sy=0.90), "in_out_sine"),
], prop=PROP_Z)

raw_clip("eat", 2640, False, [
    (0.00, {"head_rot": 12.0, "beak_open": 0.0, "eye_open": 1.0}, "linear"),
    (0.14, {"head_rot": 15.0, "beak_open": 1.0}, "in_out_cubic"),
    (0.28, {"head_rot": 10.0, "beak_open": 0.0}, "out_quad"),
    (0.44, {"head_rot": 15.0, "beak_open": 1.0}, "in_out_cubic"),
    (0.58, {"head_rot": 10.0, "beak_open": 0.0}, "out_quad"),
    (0.74, {"head_rot": 15.0, "beak_open": 1.0}, "in_out_cubic"),
    (0.86, {"head_rot": 11.0, "beak_open": 0.0}, "out_quad"),
    (1.00, {"head_rot": 0.0, "beak_open": 0.0, "eye_open": 0.3, "eye_squint": 0.55, "blush": 0.6}, "out_quad"),
])

raw_clip("scarf_put_on", 1820, False, [
    (0.00, {"scarf_on": 0.0, "head_rot": 0.0, "blush": 0.0, "eye_open": 1.0}, "linear"),
    (0.55, {"scarf_on": 1.0, "head_rot": -9.0, "eye_open": 0.4}, "out_cubic"),
    (0.72, {"scarf_on": 1.0, "head_rot": -4.0, "blush": 0.45, "body_sy": 1.03}, "out_elastic"),
    (0.86, {"body_sy": 0.98, "blush": 0.35}, "spring"),
    (1.00, {"scarf_on": 1.0, "head_rot": 0.0, "blush": 0.14, "body_sy": 1.0, "eye_open": 1.0}, "out_quad"),
])

raw_clip("wave", 1200, False, [
    (0.00, {"wing_r": -12.0, "head_rot": 0.0, "eye_open": 1.0, "blush": 0.0}, "linear"),
    (0.22, {"wing_r": -138.0, "head_rot": -5.0, "blush": 0.35}, "out_cubic"),
    (0.38, {"wing_r": -118.0}, "in_out_sine"),
    (0.54, {"wing_r": -142.0}, "in_out_sine"),
    (0.70, {"wing_r": -118.0}, "in_out_sine"),
    (0.86, {"wing_r": -140.0}, "in_out_sine"),
    (1.00, {"wing_r": -12.0, "head_rot": 0.0, "blush": 0.2, "eye_open": 1.0}, "out_quad"),
])

raw_clip("nod", 900, False, [
    (0.00, {"head_rot": 0.0, "eye_open": 1.0}, "linear"),
    (0.35, {"head_rot": 13.0, "eye_open": 0.3}, "out_cubic"),
    (0.65, {"head_rot": 4.0, "eye_open": 1.0}, "out_quad"),
    (1.00, {"head_rot": 0.0}, "out_quad"),
])


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def get(name: str) -> Clip | None:
    return _CLIPS.get(name)


def all_names() -> list[str]:
    return sorted(_CLIPS.keys())


# 待机时随机抽取的轻动作（安静、不打扰）
RANDOM_LIGHT = ["blink_double", "look_around", "wing_flap", "hop", "stretch", "yawn", "waddle", "nod"]

# 完全不会随机触发的动作
NON_RANDOM = {
    "blink", "pat_belly", "struggle", "sleep_loop", "eat",
    "throw_ball", "catch_ball", "celebrate", "sad", "dizzy", "peek",
    "scarf_put_on", "wave",
}

# 托盘"立即做点什么"菜单
MANUAL = [
    ("wave", "挥手"),
    ("spin", "转个圈"),
    ("hop", "跳一下"),
    ("stretch", "伸懒腰"),
    ("yawn", "打个哈欠"),
    ("nod", "点点头"),
    ("wing_flap", "扇翅膀"),
    ("sad", "委屈一下"),
]
