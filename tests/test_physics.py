"""物理与缓动的纯函数测试。用标准库 unittest，不需要额外依赖。

运行：
    .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""
from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game import physics  # noqa: E402
from pet import easing  # noqa: E402


class TestEasing(unittest.TestCase):
    def test_endpoints(self):
        for name, fn in easing.BY_NAME.items():
            with self.subTest(ease=name):
                self.assertAlmostEqual(fn(0.0), 0.0, places=6)
                self.assertAlmostEqual(fn(1.0), 1.0, places=6)

    def test_monotonic_enough(self):
        # 这几个应该是单调递增的（弹簧/回弹类允许过冲，不在此列）
        for name in ("linear", "in_quad", "out_quad", "in_out_cubic", "out_cubic"):
            fn = easing.BY_NAME[name]
            prev = -1.0
            for i in range(21):
                v = fn(i / 20.0)
                self.assertGreaterEqual(v + 1e-9, prev, f"{name} 在 i={i} 处回退了")
                prev = v

    def test_clamped_range(self):
        for name in ("in_quad", "out_quad", "in_out_cubic", "out_cubic", "in_out_sine"):
            fn = easing.BY_NAME[name]
            for i in range(101):
                v = fn(i / 100.0)
                self.assertGreaterEqual(v, -1e-9, name)
                self.assertLessEqual(v, 1.0 + 1e-9, name)


class TestPhysics(unittest.TestCase):
    def setUp(self):
        self.bounds = physics.Bounds(left=0.0, right=1000.0, floor=800.0, ceiling=100.0)

    def test_launch_reaches_target(self):
        """按解析解算出的初速度，飞行 t 秒后应该正好落在目标点。"""
        for t in (0.4, 0.7, 1.0):
            with self.subTest(t=t):
                vx, vy = physics.launch_velocity(100.0, 700.0, 800.0, 600.0, t)
                # 用解析公式反推位置（无阻尼）
                x = 100.0 + vx * t
                y = 700.0 + vy * t + 0.5 * physics.GRAVITY * t * t
                self.assertAlmostEqual(x, 800.0, places=3)
                self.assertAlmostEqual(y, 600.0, places=3)

    def test_bounce_decays(self):
        """连续弹跳高度必须单调衰减，不能发散。"""
        b = physics.Ball(x=500.0, y=400.0, r=18.0, alive=True)
        b.vx, b.vy = 0.0, 0.0
        peaks: list[float] = []
        last_y = b.y
        rising = False
        for _ in range(2000):
            physics.step(b, physics.FIXED_DT, self.bounds)
            if b.y < last_y - 1e-9:
                rising = True
            elif rising and b.y > last_y + 1e-9:
                rising = False
                peaks.append(last_y)
            last_y = b.y
            if b.grounded and b.rest_t > 0.5:
                break
        self.assertGreater(len(peaks), 2, f"应该发生多次弹跳，实际 {peaks}")
        # y 轴向下，所以"弹得越来越低"意味着峰值 y 越来越大
        for a, c in zip(peaks, peaks[1:]):
            self.assertGreater(c, a + 1e-6, f"弹跳高度没有衰减: {a} -> {c}")

    def test_never_escapes_floor(self):
        b = physics.Ball(x=500.0, y=400.0, r=18.0, alive=True)
        b.vx, b.vy = 900.0, -1500.0
        for _ in range(600):
            physics.step(b, physics.FIXED_DT, self.bounds)
            self.assertLessEqual(b.y + b.r, self.bounds.floor + 1e-6)

    def test_walls_keep_ball_inside(self):
        b = physics.Ball(x=500.0, y=400.0, r=18.0, alive=True)
        b.vx, b.vy = 3000.0, -600.0
        for _ in range(900):
            physics.step(b, physics.FIXED_DT, self.bounds)
            self.assertGreaterEqual(b.x - b.r, self.bounds.left - 1e-6)
            self.assertLessEqual(b.x + b.r, self.bounds.right + 1e-6)

    def test_settles_eventually(self):
        b = physics.Ball(x=500.0, y=600.0, r=18.0, alive=True)
        for _ in range(1800):
            physics.step(b, physics.FIXED_DT, self.bounds)
            if b.grounded and abs(b.vx) < 1.0:
                break
        self.assertTrue(b.grounded, "球最终应该落地静止")
        self.assertLess(math.hypot(b.vx, b.vy), 60.0)

    def test_hit_ball(self):
        b = physics.Ball(x=100.0, y=100.0, r=18.0, alive=True)
        self.assertTrue(physics.hit_ball(b, 110.0, 108.0))
        self.assertFalse(physics.hit_ball(b, 200.0, 100.0))

    def test_catch_ellipse(self):
        self.assertTrue(physics.in_catch_ellipse(0.0, 0.0, 0.0, 0.0, 48.0, 42.0))
        self.assertTrue(physics.in_catch_ellipse(47.0, 0.0, 0.0, 0.0, 48.0, 42.0))
        self.assertFalse(physics.in_catch_ellipse(60.0, 0.0, 0.0, 0.0, 48.0, 42.0))
        self.assertFalse(physics.in_catch_ellipse(0.0, 50.0, 0.0, 0.0, 48.0, 42.0))

    def test_flight_time_bounds(self):
        # 用常量断言，改手感时只动一个地方，不会漏改测试
        self.assertAlmostEqual(physics.flight_time_for(10.0, 5000.0), physics.MIN_FLIGHT)
        self.assertAlmostEqual(physics.flight_time_for(5000.0, 100.0), physics.MAX_FLIGHT)
        # 手感要求：最慢的一抛也不能超过 2 秒，最快的一抛不能快过 0.4 秒
        self.assertLess(physics.MAX_FLIGHT, 2.0)
        self.assertGreater(physics.MIN_FLIGHT, 0.40)

    def test_speed_is_slow_enough(self):
        """球不能快得像子弹——这是玩家的明确反馈，锁死一个上限。"""
        self.assertLessEqual(physics.MAX_SPEED, 3600.0)

    def test_aim_grid_covers_every_cell(self):
        """落点格子必须被走遍。

        这里钉的是一个踩过的坑：系数如果和总格数不互质，取模后会互相抵消，
        落点会退化成永远同一个位置（玩家每局都在同一个点捡球，毫无变化）。
        """
        from game.throw_game import aim_index

        total = 15
        for hits in (0, 1, 7, 40):
            seen = {aim_index(s, hits, total) for s in range(total)}
            self.assertEqual(
                len(seen), total,
                f"hits={hits} 时只走到 {len(seen)}/{total} 个落点格子",
            )
        # 相邻两次发球必须换格子，不能原地不动
        self.assertNotEqual(aim_index(0, 0, total), aim_index(1, 0, total))
        # 总格数为 0 之类的退化输入不能炸
        self.assertEqual(aim_index(3, 2, 0), 0)

    def test_spin_matches_travel(self):
        """自旋必须和移动方向相关：向右飞时角度增长，向左飞时回退。"""
        b = physics.Ball(x=500.0, y=400.0, r=22.0, alive=True)
        b.vx, b.vy = 600.0, 0.0
        b.rot = 0.0
        for _ in range(10):
            physics.step(b, physics.FIXED_DT, self.bounds)
        self.assertGreater(b.rot, 0.0, "向右滚却没自旋")
        b2 = physics.Ball(x=500.0, y=400.0, r=22.0, alive=True)
        b2.vx, b2.vy = -600.0, 0.0
        b2.rot = 100.0
        for _ in range(10):
            physics.step(b2, physics.FIXED_DT, self.bounds)
        self.assertLess(b2.rot, 100.0, "向左滚却没反向自旋")
        # 纯视觉量，不该影响位置计算
        self.assertLess(abs(b.rot), 360.0)


class TestPoseInterpolation(unittest.TestCase):
    def test_lerp_endpoints(self):
        from pet.pose import Pose, pose_lerp

        a = Pose(body_y=0.0, wing_l=10.0)
        b = Pose(body_y=-40.0, wing_l=80.0)
        mid = pose_lerp(a, b, 0.5)
        self.assertAlmostEqual(mid.body_y, -20.0)
        self.assertAlmostEqual(mid.wing_l, 45.0)
        self.assertAlmostEqual(pose_lerp(a, b, 0.0).body_y, 0.0)
        self.assertAlmostEqual(pose_lerp(a, b, 1.0).body_y, -40.0)

    def test_clip_eval_boundaries(self):
        from pet import clips
        from pet.animator import _eval

        for name in clips.all_names():
            clip = clips.get(name)
            with self.subTest(clip=name):
                p0 = _eval(clip, 0.0)
                p1 = _eval(clip, 1.0)
                self.assertIsNotNone(p0)
                self.assertIsNotNone(p1)
                # 中间取值不能抛异常，也不能出现离谱数值
                for i in range(1, 20):
                    p = _eval(clip, i / 20.0)
                    self.assertLess(abs(p.body_y), 400.0, name)
                    self.assertLess(abs(p.body_rot), 90.0, name)
                    self.assertGreaterEqual(p.alpha, 0.0, name)
                    self.assertLessEqual(p.alpha, 1.0, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
