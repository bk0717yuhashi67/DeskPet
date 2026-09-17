"""缓动函数表。全部是 t ∈ [0,1] -> [0,1] 的纯函数，方便单测。"""
from __future__ import annotations

import math


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else (hi if v > hi else v)


def linear(t: float) -> float:
    return t


def ease_in_quad(t: float) -> float:
    return t * t


def ease_out_quad(t: float) -> float:
    return 1.0 - (1.0 - t) ** 2


def ease_in_cubic(t: float) -> float:
    return t ** 3


def ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def ease_in_out_cubic(t: float) -> float:
    if t < 0.5:
        return 4.0 * t ** 3
    return 1.0 - (-2.0 * t + 2.0) ** 3 / 2.0


def ease_in_out_sine(t: float) -> float:
    return -(math.cos(math.pi * t) - 1.0) / 2.0


def ease_out_back(t: float, s: float = 1.9) -> float:
    return 1.0 + (s + 1.0) * (t - 1.0) ** 3 + s * (t - 1.0) ** 2


def ease_out_elastic(t: float) -> float:
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return 2.0 ** (-10.0 * t) * math.sin((t * 10.0 - 0.75) * (2.0 * math.pi / 3.0)) + 1.0


def ease_out_bounce(t: float) -> float:
    n1, d1 = 7.5625, 2.75
    if t < 1.0 / d1:
        return n1 * t * t
    if t < 2.0 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    if t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    t -= 2.625 / d1
    return n1 * t * t + 0.984375


def spring(t: float, zeta: float = 0.28, freq: float = 3.2) -> float:
    """阻尼弹簧：0 起步、过冲、回落。用于"被拍""接球"这类有惯性的反馈。"""
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    w = 2.0 * math.pi * freq
    wd = w * math.sqrt(max(0.0, 1.0 - zeta * zeta))
    return 1.0 - math.exp(-zeta * w * t) * math.cos(wd * t)


def soft_bounce(t: float) -> float:
    """轻微回弹，落地用。"""
    return 1.0 - math.cos(t * math.pi * 1.5) * (1.0 - t) ** 2


BY_NAME = {
    "linear": linear,
    "in_quad": ease_in_quad,
    "out_quad": ease_out_quad,
    "in_cubic": ease_in_cubic,
    "out_cubic": ease_out_cubic,
    "in_out_cubic": ease_in_out_cubic,
    "in_out_sine": ease_in_out_sine,
    "out_back": ease_out_back,
    "out_elastic": ease_out_elastic,
    "out_bounce": ease_out_bounce,
    "spring": spring,
    "soft_bounce": soft_bounce,
}


def get(name: str):
    return BY_NAME.get(name, linear)
