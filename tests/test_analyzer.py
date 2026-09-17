"""指标计算与提醒调度的测试。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitor import analyzer  # noqa: E402
from remind import scheduler as sched  # noqa: E402
from remind.model import (  # noqa: E402
    ACTION_EAT,
    ACTION_NONE,
    ALL_WEEKDAYS,
    KIND_DAILY,
    KIND_ONCE,
    KIND_WEEKLY,
    Reminder,
)


class TestAnalyzer(unittest.TestCase):
    def test_backspace_rate(self):
        self.assertAlmostEqual(analyzer.backspace_rate(1000, 100), 0.1)
        self.assertEqual(analyzer.backspace_rate(0, 0), 0.0)

    def test_switch_rate(self):
        # 1 小时活跃、20 次切换 => 20 次/小时
        self.assertAlmostEqual(analyzer.switch_rate(20, 3600), 20.0)

    def test_focus_extremes(self):
        # 满分级：45 分钟专注、几乎不切换、退格正常
        high = analyzer.focus_score(2700, 0.0, 0.08)
        # 极差：没有专注块、疯狂切换、退格率高
        low = analyzer.focus_score(0, 200.0, 0.40)
        self.assertGreater(high, 95.0)
        self.assertLess(low, 5.0)
        self.assertLess(low, high)

    def test_focus_monotonic_in_block(self):
        prev = -1.0
        for secs in (0, 600, 1200, 1800, 2400, 2700, 3600):
            v = analyzer.focus_score(secs, 5.0, 0.08)
            self.assertGreaterEqual(v, prev)
            prev = v

    def test_activity_band(self):
        low, _ = analyzer.activity_score(0, 0, 0, 3600)
        mid, ipm = analyzer.activity_score(3000, 500, 200000, 3600)
        self.assertLess(low, 20.0)
        self.assertGreater(mid, 20.0)
        self.assertGreater(ipm, 15.0)

    def test_fatigue_grows(self):
        light = analyzer.fatigue_score(1800, 600, 0, 1)
        heavy = analyzer.fatigue_score(9 * 3600, 4 * 3600, 180, 12)
        self.assertLess(light, heavy)
        self.assertGreater(heavy, 60.0)

    def test_fatigue_smoothing(self):
        raw = analyzer.fatigue_score(3600, 3600, 0, 2)
        smoothed = analyzer.fatigue_score(3600, 3600, 0, 2, prev_rolling=90.0)
        self.assertGreater(smoothed, raw)

    def test_stayup(self):
        self.assertEqual(analyzer.stayup_score(0, 0, 0), 0.0)
        # 4 小时深夜活跃 => 100
        self.assertAlmostEqual(analyzer.stayup_score(240, 0, 0), 100.0)
        # 只有 1 小时 => 25
        self.assertAlmostEqual(analyzer.stayup_score(60, 0, 0), 25.0)

    def test_flow(self):
        weak = analyzer.flow_score(2.0, 5.0, 0.4)
        strong = analyzer.flow_score(60.0, 80.0, 0.05)
        self.assertLess(weak, 0.3)
        self.assertGreater(strong, 0.8)

    def test_burst_stats(self):
        # 两段连打：5 个键紧挨着，间隔 3 秒后又是 7 个键
        ts = [0.0, 0.2, 0.4, 0.6, 0.8, 4.0, 4.2, 4.4, 4.6, 4.8, 5.0, 5.2]
        med, cnt = analyzer.burst_stats(ts)
        self.assertEqual(cnt, 2)
        self.assertAlmostEqual(med, 6.0)

    def test_compute_smoke(self):
        m = analyzer.compute(
            day="2026-09-17",
            counts={
                "active_s": 14400, "keystrokes": 9000, "backspaces": 900,
                "clicks": 1200, "mouse_dist_px": 500000, "app_switches": 60,
                "late_minutes": 0, "longest_focus_s": 2400, "idle_gap_cnt": 24,
                "first_ts": 0, "last_ts": 0,
            },
            key_times=[i * 0.25 for i in range(400)],
            focus_count=4,
            continuous_s=1800,
        )
        for name in ("focus", "activity", "fatigue", "stayup", "distraction"):
            v = getattr(m, name)
            self.assertGreaterEqual(v, 0.0, name)
            self.assertLessEqual(v, 100.0, name)
        self.assertGreaterEqual(m.flow, 0.0)
        self.assertLessEqual(m.flow, 1.0)

    def test_fmt_duration(self):
        self.assertEqual(analyzer.fmt_duration(0), "0s")
        self.assertEqual(analyzer.fmt_duration(90), "1m")
        self.assertEqual(analyzer.fmt_duration(3600), "1h")
        self.assertEqual(analyzer.fmt_duration(3600 + 20 * 60), "1h20m")

    def test_hourly_histogram(self):
        import time

        base = time.mktime(time.strptime("2026-09-17 09:00:00", "%Y-%m-%d %H:%M:%S"))
        sessions = [{"start_ts": int(base), "end_ts": int(base + 1800)}]
        hist = analyzer.hourly_histogram(sessions)
        self.assertEqual(len(hist), 24)
        self.assertAlmostEqual(max(hist), 1.0)
        self.assertTrue(all(v <= 1.0 for v in hist))


class TestReminderModel(unittest.TestCase):
    def test_clamp(self):
        r = Reminder(time_hhmm="25:99")
        r.clamp_time()
        self.assertEqual(r.time_hhmm, "23:59")
        r2 = Reminder(time_hhmm="bad")
        r2.clamp_time()
        self.assertEqual(r2.time_hhmm, "12:00")

    def test_snooze(self):
        r = Reminder(time_hhmm="12:00", message="吃饭", action=ACTION_EAT, snooze_min=10)
        s = sched.ReminderScheduler.__dict__
        self.assertEqual(r.grace_minutes(), 90)
        self.assertTrue(r.is_rest_related() is False)
        r3 = Reminder(action=ACTION_NONE)
        self.assertEqual(r3.grace_minutes(), 30)


class TestSchedulerLogic(unittest.TestCase):
    def test_matches_day_daily(self):
        r = Reminder(kind=KIND_DAILY)
        for day in ("2026-09-17", "2026-01-01"):
            self.assertTrue(sched.matches_day(r, day))

    def test_matches_day_weekly(self):
        # 2026-09-17 是周四 => weekday() == 3 => bit3
        r = Reminder(kind=KIND_WEEKLY, weekdays=1 << 3)
        self.assertTrue(sched.matches_day(r, "2026-09-17"))
        self.assertFalse(sched.matches_day(r, "2026-09-18"))
        # 全选就该每天都匹配
        r2 = Reminder(kind=KIND_WEEKLY, weekdays=ALL_WEEKDAYS)
        self.assertTrue(sched.matches_day(r2, "2026-09-17"))

    def test_matches_day_once(self):
        r = Reminder(kind=KIND_ONCE, date_key="2026-09-17")
        self.assertTrue(sched.matches_day(r, "2026-09-17"))
        self.assertFalse(sched.matches_day(r, "2026-09-18"))

    def test_occurrences_single_day(self):
        import time

        base = time.mktime(time.strptime("2026-09-17 00:00:00", "%Y-%m-%d %H:%M:%S"))
        r = Reminder(kind=KIND_DAILY, time_hhmm="12:00")
        occ = sched.occurrences(r, int(base), int(base + 86400))
        self.assertEqual(len(occ), 1)
        self.assertEqual(time.strftime("%H:%M", time.localtime(occ[0])), "12:00")
        # 只覆盖 11:00-12:30 这段，仍然应该找到 12:00
        occ2 = sched.occurrences(r, int(base + 11 * 3600), int(base + 12.5 * 3600))
        self.assertEqual(len(occ2), 1)
        # 13:00 之后就不会再找到了
        occ3 = sched.occurrences(r, int(base + 13 * 3600), int(base + 23 * 3600))
        self.assertEqual(occ3, [])

    def test_occurrences_spans_days(self):
        import time

        base = time.mktime(time.strptime("2026-09-15 00:00:00", "%Y-%m-%d %H:%M:%S"))
        r = Reminder(kind=KIND_DAILY, time_hhmm="12:00")
        occ = sched.occurrences(r, int(base), int(base + 3 * 86400))
        self.assertEqual(len(occ), 3)

    def test_occurrences_weekly_only(self):
        import time

        base = time.mktime(time.strptime("2026-09-14 00:00:00", "%Y-%m-%d %H:%M:%S"))
        # 2026-09-14 是周一
        r = Reminder(kind=KIND_WEEKLY, time_hhmm="09:00", weekdays=1)
        occ = sched.occurrences(r, int(base), int(base + 7 * 86400))
        self.assertEqual(len(occ), 1)
        self.assertEqual(time.strftime("%Y-%m-%d", time.localtime(occ[0])), "2026-09-14")


class TestGameScoreMath(unittest.TestCase):
    """计分公式的边界（不启动窗口，只验算）。"""

    def test_score_increases_with_combo(self):
        def score(combo: int, level: int = 1, acc: float = 1.0) -> float:
            gain = (10 + 2 * min(combo, 15)) * (1.0 + 0.1 * (level - 1))
            gain *= (1.0 + 0.5 * acc)
            return gain + (12 + 3 * min(combo, 15))

        # 连击加成在 15 处封顶，之前应严格递增
        values = [score(c) for c in range(1, 16)]
        for a, b in zip(values, values[1:]):
            self.assertLess(a, b)
        # 封顶之后不再变化，避免得分无限膨胀
        self.assertAlmostEqual(score(15), score(30), places=6)
        # 难度与精准度都应提升得分
        self.assertGreater(score(3, level=5), score(3, level=1))
        self.assertGreater(score(3, acc=1.0), score(3, acc=0.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
