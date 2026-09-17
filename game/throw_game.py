"""抛球小游戏：玩法状态机 + 计分 + 难度曲线。

玩法：右键开始 -> 企鹅先扔一球 -> **把鼠标移到球上（碰到就行，不用点）** ->
球飞回企鹅 -> 企鹅接住并扔向场地的另一处 -> 循环。三次失误结束。

两个刻意的设计：

1. **触碰即出手，不要求点击。** 鼠标钩子拿到的是系统物理坐标，而球、场地、
   企鹅全在 Qt 逻辑坐标里；这台机器是 125% 缩放（物理 1920x1080 / 逻辑 1536x864），
   两者差 1.25 倍，用钩子坐标去判定命中永远打不中。所以这里统一读
   `QCursor.pos()`（与画面同一坐标系），钩子只负责统计。
   顺带也更好按——不用瞄准点击，鼠标扫过去就出手。
2. **落点故意不瞄准鼠标。** 既然碰到就出手，如果球被扔到鼠标底下，
   就会变成"自动来回打"，玩家什么都不做就能刷分。所以落点在一个网格里
   轮换，并且避开鼠标当前位置。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QRect, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication

from app import logging_setup
from monitor.storage import Storage
from . import physics
from .overlay import GameOverlay

log = logging_setup.get("game")

FRAME_MS = 16
SERVE_DELAY = 1.0
HOLD_TIME = 0.45
MAX_MISSES = 3
MAX_DURATION = 15 * 60
COOLDOWN_AFTER = 3.0
CATCH_RX, CATCH_RY = 56.0, 48.0

# 碰球判定：球半径 + 这个宽松量。给足冗余，蹭到边角也算。
TOUCH_SLACK = 18.0
# 发球后这么久之内不接受触碰。球刚出手时可能正好扫过鼠标，
# 不设这个闸门就会出现"球一出手就被自己接走"的怪现象。
# 0.7s 大约是典型飞行时间的一半，足够球飞出鼠标可能待的区域。
TOUCH_ARM_DELAY = 0.70
# 落点和鼠标至少隔这么远，否则等于白送
AIM_MIN_SEP = 230.0
# 球一停下来，玩家只有这么久可以去碰它，超时就掉一颗心。
# 这个值偏紧是有意的：慢了就没紧张感，球滚到脚边随手一碰很快。
PLAYER_WAIT = 1.5
# 球在玩家半场赖了这么久（在弹、在滚都算）-> 直接收回来
PLAYER_TIMEOUT = 11.0

PHASE_IDLE = "idle"
PHASE_SERVE = "serve"
PHASE_FREE = "free"        # 球在玩家半场，等玩家用鼠标去碰
PHASE_TO_PET = "to_pet"    # 球飞向企鹅
PHASE_HOLD = "hold"        # 企鹅抱着球
PHASE_OVER = "over"


def aim_index(serves: int, hits: int, total: int) -> int:
    """落点格子的下标（纯函数，便于单测）。

    系数必须和 `total` 互质，否则会踩一个模运算陷阱：在模 15 下
    `3*(i%5) == 3*i`、`5*(i%3) == 5*i`，用 3/5 当系数时各项会互相抵消，
    落点退化成一个固定位置（实测过：15 个格子最后只用上 1 个）。
    """
    if total <= 0:
        return 0
    return (serves * 7 + hits * 4) % total


@dataclass
class Popup:
    x: float
    y: float
    text: str
    life: float = 1.15
    age: float = 0.0

    def alpha(self) -> float:
        return max(0.0, min(1.0, 1.0 - self.age / self.life)) * 255.0


@dataclass
class Ring:
    x: float
    y: float
    r: float = 12.0
    age: float = 0.0
    life: float = 0.55

    def alpha(self) -> float:
        return max(0.0, 1.0 - self.age / self.life) * 190.0


class ThrowGame(QObject):
    speak = Signal(str)
    pet_clip = Signal(str)
    started = Signal()
    ended = Signal(dict)
    hud_changed = Signal()

    def __init__(
        self,
        overlay: GameOverlay,
        hooks,
        storage: Storage,
        pet_center_provider,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.overlay = overlay
        self.storage = storage
        self.pet_center = pet_center_provider   # () -> (x, y, scale)
        # 保留引用只是为了让调用方接口不变（主程序还要用同一个钩子做统计）。
        # 玩法本身**不再读钩子坐标**——它给的是物理像素，和画面坐标系差一个
        # 缩放倍率，用它做命中判定必然错位。见 `_pointer()`。
        self.hooks = hooks

        self.active = False
        self.phase = PHASE_IDLE
        self.ball = physics.Ball()
        self.bounds = physics.Bounds(0, 1920, 1000, 100)

        self.lives = MAX_MISSES
        self.max_lives = MAX_MISSES
        self.score = 0
        self.combo = 0
        self.best_combo = 0
        self.hits = 0
        self.misses = 0
        self.level = 1

        self.popups: list[Popup] = []
        self.rings: list[Ring] = []
        self._flight_t = 0.0
        self._phase_t = 0.0
        self._start_ts = 0.0
        self._cooldown_until = 0.0
        self._accum = 0.0
        self._last_frame = 0.0
        self._hud_dirty = True
        self._serves = 0
        self._pending_bonus = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._on_frame)

        overlay.game = self

    # ==================================================================
    # 生命周期
    # ==================================================================
    def can_start(self) -> bool:
        if self.active:
            return False
        return time.monotonic() >= self._cooldown_until

    def start(self) -> bool:
        if not self.can_start():
            return False
        rect, floor = self._play_area()
        left = rect.left() + 26.0
        right = rect.right() - 26.0
        # 天花板压到 HUD 下面，球不会飞过去糊住比分
        ceiling = rect.top() + 152.0

        # 活动区必须把企鹅包进来。否则企鹅被拖到贴边、贴着任务栏，
        # 或者被拖到另一块屏上时，球会被墙和地面挡住，永远飞不到它身上。
        px, py, _scale = self.pet_center()
        left = min(left, px - 90.0)
        right = max(right, px + 90.0)
        floor = max(floor, py + 60.0)

        self.bounds = physics.Bounds(
            left=left, right=right, floor=floor, ceiling=ceiling,
        )
        self.lives = MAX_MISSES
        self.score = 0
        self.combo = 0
        self.best_combo = 0
        self.hits = 0
        self.misses = 0
        self.level = 1
        self._serves = 0
        self.popups.clear()
        self.rings.clear()
        self.active = True
        self.phase = PHASE_SERVE
        self._phase_t = 0.0
        self._flight_t = 0.0
        self._start_ts = time.monotonic()
        self._last_frame = self._start_ts

        self._place_ball_in_hand()
        self.overlay.show_over(rect)
        self.overlay.update()
        self._timer.start()
        self.started.emit()
        self.speak.emit("来玩抛球吧！把鼠标移到皮球上，碰到就扔给我。")
        return True

    def stop(self, silent: bool = False) -> dict | None:
        if not self.active:
            return None
        result = self._result()
        self.active = False
        self.phase = PHASE_OVER
        self.ball.alive = False
        self._timer.stop()
        self.overlay.hide()
        self._cooldown_until = time.monotonic() + COOLDOWN_AFTER
        if not silent:
            self.ended.emit(result)
        return result

    # ==================================================================
    # 输入
    # ==================================================================
    def _pointer(self) -> tuple[float, float]:
        """鼠标位置（Qt 逻辑坐标）。

        必须用 QCursor 而不是全局钩子的坐标：钩子给的是系统物理像素，
        在一台 125% 缩放的屏上是逻辑坐标的 1.25 倍，拿它去和球的位置比
        会永远差一大截（以前"点了没反应"有一半是这个原因）。
        """
        p = QCursor.pos()
        return float(p.x()), float(p.y())

    def handle_click(self, x: float, y: float) -> bool:
        """外部（测试 / 手动）强制出手。传的是 Qt 逻辑坐标。"""
        return self._try_throw(x, y)

    def handle_move(self, x: float, y: float) -> bool:
        return self._try_throw(x, y)

    def _try_throw(self, x: float, y: float) -> bool:
        if not self.active or self.phase != PHASE_FREE:
            return False
        b = self.ball
        if not b.alive or not physics.hit_ball(b, x, y, slack=TOUCH_SLACK):
            return False
        if self._phase_t < TOUCH_ARM_DELAY:
            return False
        self._throw_from(x, y)
        return True

    def _throw_from(self, x: float, y: float) -> None:
        b = self.ball
        cx, cy, _s = self.pet_center()
        chest_y = cy - 18.0
        # 出手时机加成：球滚得越快、你追上去碰得越利落，加成越高。
        # （不用"离球心多近"来算——鼠标进入判定圈的那一帧必然在圈的边缘，
        #   那个指标在触碰式玩法里恒等于 0，是个死指标。）
        speed = math.hypot(b.vx, b.vy)
        bonus = max(0.0, min(1.0, speed / 420.0))

        flight = physics.flight_time_for(
            math.hypot(cx - b.x, chest_y - b.y), self._fly_speed()
        )
        b.vx, b.vy = physics.launch_velocity(b.x, b.y, cx, chest_y, flight, self.gravity)
        b.grounded = False
        b.rest_t = 0.0
        b.bounces = 0
        b.trail.clear()
        b.owner = "free"
        self._pending_bonus = bonus
        self.phase = PHASE_TO_PET
        self._phase_t = 0.0
        self._flight_t = 0.0

        self.pet_clip.emit("wing_flap")   # 让反馈即时：企鹅立刻准备
        self.overlay.update()

    # ==================================================================
    # 每帧
    # ==================================================================
    def _on_frame(self) -> None:
        now = time.monotonic()
        dt = min(0.05, now - self._last_frame)
        self._last_frame = now
        self._phase_t += dt

        if self._start_ts and now - self._start_ts > MAX_DURATION:
            self.speak.emit("玩了挺久啦，先歇一会儿？")
            self.stop()
            return

        self._accum += dt
        steps = 0
        while self._accum >= physics.FIXED_DT and steps < 5:
            self._physics_step(physics.FIXED_DT)
            self._accum -= physics.FIXED_DT
            steps += 1

        # 触碰判定：每帧把鼠标位置和球比一次。用"实时位置"而不是点击事件，
        # 是因为球会滚动——鼠标停着不动、球自己滚过来，也应该算碰到。
        if self.active and self.phase == PHASE_FREE and self.ball.alive:
            mx, my = self._pointer()
            self._try_throw(mx, my)

        self._update_effects(dt)
        self.overlay.refresh(self.ball if self.phase not in (PHASE_SERVE,) else None,
                             self.overlay.hud_rect() if self._hud_dirty else None)
        self._hud_dirty = bool(self.popups or self.rings)

    # ------------------------------------------------------------------
    def _physics_step(self, dt: float) -> None:
        if self.phase == PHASE_SERVE:
            if self._phase_t >= SERVE_DELAY:
                self._serve()
            return

        if self.phase == PHASE_HOLD:
            self._place_ball_in_hand()
            if self._phase_t >= HOLD_TIME:
                self._serve()
            return

        if self.phase == PHASE_IDLE or self.phase == PHASE_OVER:
            return

        b = self.ball
        if not b.alive:
            return
        self._flight_t += dt

        wind = 0.0
        if self.level >= 3 and not b.grounded:
            wind = 260.0 * math.sin(2.0 * math.pi * self._flight_t / 2.4)

        event = physics.step(b, dt, self.bounds, self.gravity, wind)

        if self.level >= 5 and event == "bounce" and b.bounces == 1:
            b.vx += 180.0 * (1.0 if (self.hits % 2 == 0) else -1.0)

        if self.phase == PHASE_TO_PET:
            self._check_catch()
        elif self.phase == PHASE_FREE:
            self._check_player_fail()

        if b.x < self.bounds.left + b.r * 0.4 or b.x > self.bounds.right - b.r * 0.4:
            if self.phase == PHASE_TO_PET:
                self._register_miss("出界了")

    def _check_catch(self) -> None:
        b = self.ball
        cx, cy, scale = self.pet_center()
        chest_y = cy - 18.0
        if physics.in_catch_ellipse(
            b.x, b.y, cx, chest_y, CATCH_RX * scale, CATCH_RY * scale
        ):
            self._on_catch(cx, chest_y)

    def _check_player_fail(self) -> None:
        b = self.ball
        # 判据只看"玩家有没有去碰"，不看弹跳次数。
        # 原来用 `bounces >= 6` 当代理指标，但正常一球落地本来就要弹好几下，
        # 于是几乎每球都先被判失误——代理指标和"玩家没响应"根本不是一回事。
        if b.rest_t > PLAYER_WAIT:
            self._register_miss("球等太久啦")
        elif self._phase_t > PLAYER_TIMEOUT:
            self._register_miss("这球我先收回来")

    # ------------------------------------------------------------------
    def _serve(self) -> None:
        b = self.ball
        b.alive = True
        b.trail.clear()
        b.grounded = False
        b.rest_t = 0.0
        b.bounces = 0
        b.r = self._ball_radius()
        b.owner = "free"
        self._serves += 1

        tx, ty = self._aim_point()
        start_x, start_y = b.x, b.y
        flight = physics.flight_time_for(
            math.hypot(tx - start_x, ty - start_y), self._fly_speed()
        )
        b.vx, b.vy = physics.launch_velocity(start_x, start_y, tx, ty, flight, self.gravity)

        self.phase = PHASE_FREE
        self._phase_t = 0.0
        self._flight_t = 0.0
        self.pet_clip.emit("throw_ball")
        self.overlay.update()

    def _aim_point(self) -> tuple[float, float]:
        """挑一个落点，**故意避开鼠标**。

        触碰式玩法下，如果把球扔到鼠标底下，球一落地就会被碰到、又飞回去，
        变成永动的自动回合，玩家什么都不用做就能刷分。所以落点在一个网格里
        轮换（保证每次落点不同），并且优先选离鼠标足够远的位置。
        """
        b = self.bounds
        mx, my = self._pointer()
        cols, rows = 5, 3
        total = cols * rows
        w = max(1.0, b.right - b.left)
        h = max(1.0, b.floor - b.ceiling)

        # 落点索引。系数必须和 total 互质，理由见 aim_index 的注释。
        start = aim_index(self._serves, self.hits, total)
        fallback = (0.0, (b.left + b.right) / 2.0, b.floor - 80.0)
        for k in range(total):
            idx = (start + k) % total
            ax = b.left + w * (0.16 + 0.68 * ((idx % cols) + 0.5) / cols)
            ay = b.ceiling + h * (0.26 + 0.50 * ((idx // cols) + 0.5) / rows)
            d = math.hypot(ax - mx, ay - my)
            if d >= AIM_MIN_SEP:
                return ax, ay
            if d > fallback[0]:
                fallback = (d, ax, ay)
        # 鼠标正好在场地正中：一组候选全都很近，就退回最远的那个
        return fallback[1], fallback[2]

    def _on_catch(self, cx: float, cy: float) -> None:
        b = self.ball
        b.alive = True
        b.owner = "pet"
        b.trail.clear()

        bonus = float(getattr(self, "_pending_bonus", 0.0) or 0.0)
        gain = (10 + 2 * min(self.combo, 15)) * (1.0 + 0.1 * (self.level - 1))
        gain *= (1.0 + 0.5 * bonus)
        catch_bonus = 12 + 3 * min(self.combo, 15)
        total = int(round(gain + catch_bonus))

        self.score += total
        self.combo += 1
        self.best_combo = max(self.best_combo, self.combo)
        self.hits += 1
        new_level = min(6, 1 + self.hits // 5)
        if new_level != self.level:
            self.level = new_level
            self.speak.emit(f"难度升到 Lv{self.level} 啦")

        self.popups.append(Popup(cx, cy - 36.0, f"+{total}"))
        self.pet_clip.emit("catch_ball")

        if self.combo in (5, 10, 20, 30):
            self.rings.append(Ring(cx, cy, 12.0))
            self.pet_clip.emit("celebrate")
            self.speak.emit(f"连击 x{self.combo}！")

        self.phase = PHASE_HOLD
        self._phase_t = 0.0
        self._hud_dirty = True
        self.overlay.update()

    def _place_ball_in_hand(self) -> None:
        cx, cy, scale = self.pet_center()
        self.ball.x = cx + 42.0 * scale
        self.ball.y = cy - 14.0 * scale
        self.ball.vx = self.ball.vy = 0.0
        self.ball.grounded = False
        self.ball.rest_t = 0.0
        self.ball.bounces = 0
        self.ball.r = self._ball_radius()
        self.ball.alive = True
        self.ball.owner = "pet"
        self.ball.trail.clear()

    def _register_miss(self, reason: str) -> None:
        self.misses += 1
        self.combo = 0
        self.lives -= 1
        self._hud_dirty = True
        cx, cy, _s = self.pet_center()
        self.popups.append(Popup(cx, cy - 60.0, "失误"))
        self.pet_clip.emit("sad")
        self.speak.emit(reason + "，再来一次。")
        if self.lives <= 0:
            self.stop()
            return

        self.phase = PHASE_HOLD
        self._phase_t = HOLD_TIME  # 立刻重新发球
        self.ball.alive = False
        self.overlay.update()

    # ------------------------------------------------------------------
    def _update_effects(self, dt: float) -> None:
        for p in self.popups:
            p.age += dt
            p.y -= 34.0 * dt
        self.popups = [p for p in self.popups if p.age < p.life]

        for r in self.rings:
            r.age += dt
            r.r += 320.0 * dt
        self.rings = [r for r in self.rings if r.age < r.life]

    # ==================================================================
    # 难度 / 结算
    # ==================================================================
    @property
    def gravity(self) -> float:
        return physics.GRAVITY * min(1.4, 1.0 + 0.07 * (self.level - 1))

    def _fly_speed(self) -> float:
        # 原来基准 1000 px/s，一局下来球快得像子弹。降到 620，
        # 配上更长的飞行时间，单次往返大约 1.3 秒——看得清、接得住。
        return 620.0 * min(1.55, 1.0 + 0.075 * (self.level - 1))

    def _ball_radius(self) -> float:
        # 皮球给大一点：体积大才好碰，也才像沙滩皮球
        if self.level >= 5:
            return 20.0
        if self.level >= 4:
            return 22.0
        return 24.0

    def _play_area(self) -> tuple[QRect, float]:
        cx, cy, _s = self.pet_center()
        scr = QGuiApplication.screenAt(_qpoint(cx, cy)) or QGuiApplication.primaryScreen()
        av = scr.availableGeometry() if scr else QRect(0, 0, 1920, 1080)
        floor = float(av.bottom()) - 8.0
        return av, floor

    def _result(self) -> dict:
        duration = int(max(0.0, time.monotonic() - self._start_ts)) if self._start_ts else 0
        attempts = self.hits + self.misses
        accuracy = (self.hits / attempts) if attempts else 0.0
        best = int(self.storage.get_meta("high_score", 0) or 0)
        new_record = self.score > best
        if new_record:
            self.storage.set_meta("high_score", int(self.score))
            self.storage.flush()
        self.storage.add_event("game", {
            "score": self.score, "combo": self.best_combo,
            "hits": self.hits, "misses": self.misses,
        })
        return {
            "score": self.score,
            "best_combo": self.best_combo,
            "hits": self.hits,
            "misses": self.misses,
            "accuracy": accuracy,
            "duration_s": duration,
            "new_record": new_record,
            "high_score": max(best, self.score),
        }


def _qpoint(x: float, y: float):
    from PySide6.QtCore import QPoint

    return QPoint(int(x), int(y))
