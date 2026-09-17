"""抛球物理：纯函数 + 定点积分，可单测。

用固定步长 dt=1/60 的半隐式欧拉（先更新速度再更新位置），比显式欧拉稳定，
弹跳不会发散。所有单位是逻辑像素与秒。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

GRAVITY = 1200.0            # px/s^2
# 重力同时决定"抛物线有多高"：飞行时间固定时，顶点高度 = g*t^2/8。
# 原来 g=2200 配长飞行时间会让球冲到天上 600+ 像素，看着夸张也不好预判；
# 降到 1200 之后最远一抛的顶点大约 360px，是皮球那种又慢又平的弧线。
AIR_DAMPING = 0.11          # 每秒速度衰减比例（皮球轻，空气阻力更明显）
RESTITUTION = 0.45          # 落地反弹系数（皮球瘪一点，弹两下就该停）
FRICTION = 0.78             # 落地水平摩擦
WALL_RESTITUTION = 0.72     # 撞左右墙反弹系数
REST_SPEED = 70.0           # /s，低于这个竖直速度就判定为停下。

# 这几个常数是调出来的：原来 RESTITUTION=0.58 / REST_SPEED=40 时，
# 一球落地要弹 6~7 次、耗时 2 秒才算"停下"，而失误判据恰好是"弹满 6 次"，
# 结果几乎每一球都先被判成失误（玩家还没走到球跟前就已经扣命了）。
# 现在弹 3~4 下就进入静止，判据才和实际手感对得上。
MAX_SPEED = 3400.0
FIXED_DT = 1.0 / 60.0

# 飞行时间的上下限。下限别太小——太小的意思是"一瞬间就到了"，
# 玩家根本跟不上；上限让远距离的抛掷变得慢悠悠、看得清。
MIN_FLIGHT = 0.55
MAX_FLIGHT = 1.65

# 自旋：纯视觉量。按"滚动的线速度 / 半径"折算成角度，
# 再乘一个小于 1 的系数——真实滚动会让皮球转得让人头晕。
SPIN_FACTOR = 0.16
DEG_PER_RAD = 57.29577951308232


@dataclass
class Ball:
    x: float = 0.0
    y: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    r: float = 22.0
    grounded: bool = False
    rest_t: float = 0.0
    bounces: int = 0
    rot: float = 0.0          # 自旋角度（度，纯视觉）
    trail: list[tuple[float, float]] = field(default_factory=list)
    owner: str = "none"       # none | pet | free
    alive: bool = False

    def pos(self) -> tuple[float, float]:
        return (self.x, self.y)


@dataclass
class Bounds:
    """球的活动范围（逻辑像素）。"""
    left: float
    right: float
    floor: float
    ceiling: float


def launch_velocity(
    sx: float, sy: float, tx: float, ty: float, t: float, g: float = GRAVITY
) -> tuple[float, float]:
    """给定飞行时间 t，算出从 (sx,sy) 打到 (tx,ty) 所需的初速度（标准抛物线解）。"""
    t = max(0.08, t)
    vx = (tx - sx) / t
    vy = (ty - sy) / t - 0.5 * g * t
    return vx, vy


def flight_time_for(distance: float, speed: float) -> float:
    """距离越远飞得越久；speed 越大飞得越快（难度用）。"""
    speed = max(120.0, speed)
    return max(MIN_FLIGHT, min(MAX_FLIGHT, distance / speed))


def spin_delta(vx: float, r: float, dt: float) -> float:
    """本帧的自旋增量（度）。纯视觉，不参与物理。"""
    return (vx / max(8.0, r)) * dt * DEG_PER_RAD * SPIN_FACTOR


def step(
    b: Ball,
    dt: float,
    bounds: Bounds,
    g: float = GRAVITY,
    wind_ax: float = 0.0,
) -> str:
    """推进一帧。返回本帧发生的事件：'' | 'bounce' | 'wall' | 'out'。"""
    if not b.alive:
        return ""

    event = ""
    b.vy += (g + 0.0) * dt
    b.vx += wind_ax * dt

    # 空气阻尼
    damp = max(0.0, 1.0 - AIR_DAMPING * dt)
    b.vx *= damp
    b.vy *= damp

    # 限速，避免极端参数导致穿透
    sp = math.hypot(b.vx, b.vy)
    if sp > MAX_SPEED:
        k = MAX_SPEED / sp
        b.vx *= k
        b.vy *= k

    b.x += b.vx * dt
    b.y += b.vy * dt
    b.rot = (b.rot + spin_delta(b.vx, b.r, dt)) % 360.0

    # 地面
    if b.y + b.r >= bounds.floor:
        b.y = bounds.floor - b.r
        if b.vy > 0:
            b.vy = -b.vy * RESTITUTION
        b.vx *= FRICTION
        b.bounces += 1
        event = "bounce"
        if abs(b.vy) < REST_SPEED:
            b.vy = 0.0
            b.vx *= 0.90
            b.grounded = True
        else:
            b.grounded = False

    if b.grounded:
        b.rest_t += dt
    else:
        b.rest_t = 0.0

    # 天花板
    if b.y - b.r <= bounds.ceiling:
        b.y = bounds.ceiling + b.r
        if b.vy < 0:
            b.vy = -b.vy * 0.5

    # 左右墙
    if b.x - b.r <= bounds.left:
        b.x = bounds.left + b.r
        b.vx = abs(b.vx) * WALL_RESTITUTION
        event = event or "wall"
    elif b.x + b.r >= bounds.right:
        b.x = bounds.right - b.r
        b.vx = -abs(b.vx) * WALL_RESTITUTION
        event = event or "wall"

    # 掉出活动区（一般不可能，兜底）
    if b.y - b.r > bounds.floor + 400:
        b.alive = False
        return "out"

    # 拖尾
    b.trail.append((b.x, b.y))
    if len(b.trail) > 12:
        b.trail.pop(0)

    return event


def predict_landing_x(b: Ball, bounds: Bounds, g: float = GRAVITY, steps: int = 240) -> float:
    """预测落点横坐标，用于在地面画虚影（"预判落点"是抛球乐趣的核心）。"""
    x, y, vx, vy = b.x, b.y, b.vx, b.vy
    dt = FIXED_DT
    for _ in range(steps):
        vy += g * dt
        damp = max(0.0, 1.0 - AIR_DAMPING * dt)
        vx *= damp
        vy *= damp
        x += vx * dt
        y += vy * dt
        if vy > 0 and y + b.r >= bounds.floor:
            return x
        if x < bounds.left or x > bounds.right:
            return x
    return x


def hit_ball(b: Ball, px: float, py: float, slack: float = 10.0) -> bool:
    return math.hypot(px - b.x, py - b.y) <= b.r + slack


def in_catch_ellipse(px: float, py: float, cx: float, cy: float, rx: float, ry: float) -> bool:
    dx = (px - cx) / max(1.0, rx)
    dy = (py - cy) / max(1.0, ry)
    return dx * dx + dy * dy <= 1.0
