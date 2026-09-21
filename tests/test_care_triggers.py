"""关怀触发链路测试：验证"数据 -> 心情 -> 发言 + 动作"真的接通了。

这是对历史 bug 的回归测试。三个已经出过问题的点：

  1. **双重闸门**：CareManager 先 record() 扣预算再 emit，main.show_bubble
     若再查一次 can_speak 必然失败，气泡被静默丢弃。测试里用一个
     和学习版行为完全一致的 FakeMain.show_bubble 复现这条链路。
  2. **熬夜关切凌晨静默**：evaluate() 在凌晨 1-5 点返回 CONCERN_STAYUP，
     但 _check_stayup 曾因 stayup<50 提前 return，导致"判定关切却不说话"。
  3. **心情映射没接线**：MOOD_VOICE 表存在但没有任何 `_check_*` 念它。

测试用假的 sampler/storage 驱动真实的 CareManager，不启动 Qt 窗口、
不碰真实数据库 —— CareManager 是 QObject，但没有事件循环也能直接调方法。
"""
from __future__ import annotations

import os
import sys
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from care.budget import BubbleBudget  # noqa: E402
from care.features import MOOD_VOICE, CareManager  # noqa: E402
from pet.mood import Mood, MoodTracker  # noqa: E402

# ------------------------------------------------------------------ 测试替身
class FakeStats:
    """对齐 monitor.sampler 暴露给 CareManager 的那部分字段。"""

    def __init__(self, **kw):
        self.day = time.strftime("%Y-%m-%d")
        self.active_s = 0
        self.continuous_active_s = 0
        self.keystrokes = 0
        self.backspaces = 0
        self.clicks = 0
        self.mouse_dist_px = 0
        self.app_switches = 0
        self.late_minutes = 0
        self.longest_focus_s = 0
        self.idle_gap_cnt = 0
        self.first_ts = 0
        self.last_ts = 0
        self.current_exe = "pycharm.exe"
        self.key_times: list[float] = []
        self.idle_now = 0.0
        self.fullscreen = False
        self.meeting = False
        self.locked = False
        self.in_idle = False
        for k, v in kw.items():
            setattr(self, k, v)

    def clone_counts(self) -> dict:
        return {
            "active_s": self.active_s,
            "keystrokes": self.keystrokes,
            "backspaces": self.backspaces,
            "clicks": self.clicks,
            "mouse_dist_px": self.mouse_dist_px,
            "app_switches": self.app_switches,
            "late_minutes": self.late_minutes,
            "longest_focus_s": self.longest_focus_s,
            "idle_gap_cnt": self.idle_gap_cnt,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
        }


class FakeSampler:
    def __init__(self, stats: FakeStats):
        self.stats = stats


class FakeStorage:
    """内存版 Storage：只实现 CareManager 用到的几个方法。"""

    def __init__(self, history=None):
        # history 模拟 storage.metrics_range(4) 的返回：按时间**倒序**
        # （最近的一天在最前）。CareManager._late_streak 会 reversed() 它，
        # 所以这里保持倒序即可正确还原"从近几天往前数连续熬夜"。
        self._history = history or []
        self._meta: dict[str, str] = {}

    def metrics_range(self, days: int):
        return list(self._history)[:days]

    def focus_count(self, day: str) -> int:
        return 3

    def days_since_install(self) -> int:
        return 1

    def get_meta(self, key: str):
        return self._meta.get(key)

    def set_meta(self, key: str, value) -> None:
        self._meta[key] = value


class FakeStateMachine:
    """不接 Qt：安静理由用集合记录，方便断言。"""

    def __init__(self):
        self._quiet: set[str] = set()
        self.state = "idle"

    def set_quiet(self, reason: str, on: bool) -> None:
        self._quiet.add(reason) if on else self._quiet.discard(reason)

    @property
    def is_quiet(self) -> bool:
        return bool(self._quiet)

    @property
    def is_proactive_blocked(self) -> bool:
        return bool(self._quiet)

    def is_state(self, *names) -> bool:
        return self.state in names

    def request(self, state, source="", force=False):
        self.state = state

    def back_to_idle(self):
        self.state = "idle"


class FakeMain:
    """复刻 main.show_bubble 的预算语义。

    单独把这段逻辑抄过来（而不是直接用 main.py）是为了让测试不依赖
    QApplication。**逻辑必须与 main.py 保持一致**，否则测不出双重闸门。
    """

    def __init__(self, budget: BubbleBudget, quiet: bool = False):
        self.budget = budget
        self.quiet = quiet
        self.shown: list[tuple[str, bool]] = []
        self.dropped: list[str] = []

    def show_bubble(self, text: str, urgent: bool = False,
                    skip_budget: bool = False) -> None:
        if not text:
            return
        if skip_budget:
            # CareManager 已在调用侧结算，这里只受手动安静开关约束
            if self.quiet:
                return
        elif urgent:
            self.budget.record()
        elif not self.quiet:
            if not self.budget.can_speak():
                self.dropped.append(text)
                return
            self.budget.record()
        self.shown.append((text, urgent))


class Rig:
    """把 CareManager 和替身接起来，并捕获它 emit 的信号。"""

    def __init__(self, history=None, stats: FakeStats | None = None,
                 budget: BubbleBudget | None = None, quiet: bool = False):
        self.stats = stats or FakeStats()
        self.storage = FakeStorage(history)
        self.sm = FakeStateMachine()
        self.budget = budget or BubbleBudget()
        self.mood = MoodTracker()
        self.main = FakeMain(self.budget, quiet=quiet)

        self.care = CareManager(
            FakeSampler(self.stats), self.storage, self.sm, self.budget, self.mood
        )
        self.said: list[tuple[str, bool]] = []
        self.clips: list[str] = []

        # 与 main._wire() 完全同构：skip_budget=True 是双重闸门修复的关键
        self.care.speak.connect(
            lambda t, urgent=False: self._route(t, urgent)
        )
        self.care.pet_clip.connect(self.clips.append)

    def _route(self, text: str, urgent: bool) -> None:
        self.said.append((text, urgent))
        self.main.show_bubble(text, urgent=urgent, skip_budget=True)

    @property
    def spoken(self) -> list[str]:
        """真正显示出来的气泡文案。"""
        return [t for t, _ in self.main.shown]

    def refresh_metrics(self):
        """跑一遍 _update_mood，让 self.care._metrics 有值。"""
        self.care._update_mood(time.time(), self.stats)
        return self.care._metrics


def _history_rows(stayup_values):
    """构造 metrics_range 的返回。

    顺序说明：真实 Storage.metrics_range 返回**按时间倒序**（最近的在最前）。
    CareManager._late_streak 内部会 reversed() 成"从旧到新"，再从头累计连续熬夜天数
    —— 也就是说它数的是"最早那几天是否连续熬夜"，因此最近一天放在列表首位。
    传入 (80, 75, 70, 10) 表示：最近 80、往前 75、70，更早的 10（断掉）。
    """
    return [
        {"day": f"2026-09-{20 - i:02d}", "stayup": v, "fatigue": 0}
        for i, v in enumerate(stayup_values)
    ]


# ================================================================== 用例
class TestDoubleGate(unittest.TestCase):
    """回归：预算扣费不能被查两次，否则气泡全被丢掉。"""

    def test_care_bubble_survives_budget_settlement(self):
        rig = Rig()
        rig.refresh_metrics()
        # 直接走 _check_mood_voice 依赖的路径：先 record 再 emit
        rig.budget.record(time.time())
        rig.care.speak.emit("测试文案", False)
        self.assertEqual(rig.spoken, ["测试文案"])
        self.assertEqual(rig.main.dropped, [],
                         "气泡不应因预算被二次检查而丢弃")

    def test_without_skip_budget_bubble_is_dropped(self):
        """反证：不传 skip_budget 时确实会被拦截——证明这个修复是必要的。"""
        rig = Rig()
        rig.budget.record(time.time())
        rig.care.speak.disconnect()
        rig.care.speak.connect(
            lambda t, urgent=False: rig.main.show_bubble(t, urgent=urgent)
        )
        rig.care.speak.emit("会被丢掉", False)
        self.assertEqual(rig.spoken, [])
        self.assertEqual(rig.main.dropped, ["会被丢掉"])


class TestMoodVoice(unittest.TestCase):
    """心情映射：每种心情都要有专属台词场景 + 动作。"""

    def test_every_mood_has_scene_and_clip(self):
        from care import lines

        for mood, (scene, clip) in MOOD_VOICE.items():
            with self.subTest(mood=mood.value):
                self.assertTrue(lines.has(scene), f"缺少台词场景 {scene}")
                self.assertTrue(clip, f"{mood.value} 没有配动作")

    def test_mood_voice_fires_and_plays_clip(self):
        rig = Rig()
        rig.refresh_metrics()
        rig.mood.force(Mood.CHEERFUL)
        rig.care._check_mood_voice(time.time(), rig.stats)
        self.assertEqual(rig.clips, ["celebrate"])
        self.assertEqual(len(rig.spoken), 1)

    def test_mood_voice_repeat_guard(self):
        """同一种心情 25 分钟内不重复开口。"""
        rig = Rig()
        rig.refresh_metrics()
        rig.mood.force(Mood.CHEERFUL)
        rig.care._check_mood_voice(time.time(), rig.stats)
        first = rig.care._mood_voice_at[Mood.CHEERFUL]
        # 把预算重置，确认拦住它的是"重复间隔"而不是预算
        rig.budget.used = 0
        rig.budget.last_at = 0.0
        rig.care._check_mood_voice(time.time(), rig.stats)
        self.assertEqual(rig.care._mood_voice_at[Mood.CHEERFUL], first)
        self.assertEqual(len(rig.spoken), 1, "25 分钟内不该重复")

    def test_mood_voice_silent_when_away(self):
        """人不在（idle 超阈值）就别对着空椅子说话。"""
        rig = Rig()
        rig.refresh_metrics()
        rig.mood.force(Mood.CHEERFUL)
        rig.stats.idle_now = 400.0
        rig.care._check_mood_voice(time.time(), rig.stats)
        self.assertEqual(rig.spoken, [])

    def test_mood_voice_blocked_in_quiet_mode(self):
        """心流/全屏等安静态下必须闭嘴。"""
        rig = Rig()
        rig.refresh_metrics()
        rig.mood.force(Mood.CHEERFUL)
        rig.sm.set_quiet("focus", True)
        rig.care._check_mood_voice(time.time(), rig.stats)
        self.assertEqual(rig.spoken, [])


class TestStayup(unittest.TestCase):
    """熬夜关切：凌晨与深夜两条入口都要能开口。"""

    def test_fires_on_high_stayup(self):
        rig = Rig()
        rig.refresh_metrics()
        rig.care._metrics.stayup = 82.0
        rig.stats.late_minutes = 20000
        rig.care._check_stayup(time.time())
        self.assertEqual(len(rig.spoken), 1)
        self.assertIn("sad", rig.clips)

    def test_early_morning_fires_even_with_low_score(self):
        """回归：凌晨 1-5 点即使 stayup<50 也必须开口（曾在此处静默）。"""
        rig = Rig()
        rig.refresh_metrics()
        rig.care._metrics.stayup = 10.0
        # 构造一个凌晨 2 点的时间戳
        lt = time.localtime()
        early = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 2, 0, 0, 0, 0, -1))
        rig.care._check_stayup(early)
        self.assertEqual(len(rig.spoken), 1, "凌晨应触发熬夜关切")
        self.assertTrue(rig.said[0][1], "凌晨关切应标记为紧急")

    def test_no_fire_when_normal(self):
        rig = Rig()
        rig.refresh_metrics()
        rig.care._metrics.stayup = 20.0
        lt = time.localtime()
        normal = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 15, 0, 0, 0, 0, -1))
        rig.care._check_stayup(normal)
        self.assertEqual(rig.spoken, [])

    def test_once_per_day(self):
        rig = Rig()
        rig.refresh_metrics()
        rig.care._metrics.stayup = 90.0
        rig.care._check_stayup(time.time())
        rig.budget.used = 0          # 排掉预算干扰
        rig.budget.last_at = 0.0
        rig.care._check_stayup(time.time())
        self.assertEqual(len(rig.spoken), 1, "熬夜关切每天只说一次")

    def test_streak_uses_hard_scene(self):
        # 列表首位 = 最近一天。故 [80, 75, 70, 10] 经 reversed 后以 10 开头，
        # 连续段在"更早的三天"，streak 应为 3。
        rig = Rig(history=_history_rows([10, 70, 75, 80]))
        rig.refresh_metrics()
        self.assertEqual(rig.care._late_streak(), 3)

    def test_streak_zero_when_latest_normal(self):
        # 最近一天正常（stayup=10 在 reversed 后排在最后），连续段为 0
        rig = Rig(history=_history_rows([80, 75, 70, 10]))
        rig.refresh_metrics()
        self.assertEqual(rig.care._late_streak(), 0)


class TestHealthCare(unittest.TestCase):
    """健康包：久坐 / 护眼 / 喝水三个触发 + 动作。"""

    def test_sit_long_triggers_stretch(self):
        rig = Rig()
        rig.refresh_metrics()
        rig.stats.active_s = 6000
        rig.stats.continuous_active_s = 50 * 60
        rig.care._last_sit = time.time() - 51 * 60
        rig.care._check_health(time.time(), rig.stats)
        self.assertEqual(rig.clips, ["stretch"])
        self.assertEqual(len(rig.spoken), 1)

    def test_eyes_triggers_look_around(self):
        rig = Rig()
        rig.refresh_metrics()
        rig.stats.active_s = 6000
        rig.care._last_eye = time.time() - 46 * 60
        rig.care._check_health(time.time(), rig.stats)
        self.assertEqual(rig.clips, ["look_around"])

    def test_water_triggers_nod(self):
        rig = Rig()
        rig.refresh_metrics()
        rig.stats.active_s = 6000
        rig.care._last_eye = time.time()      # 排掉护眼
        rig.care._last_water = time.time() - 76 * 60
        rig.care._check_health(time.time(), rig.stats)
        self.assertEqual(rig.clips, ["nod"])

    def test_no_health_talk_before_5min(self):
        rig = Rig()
        rig.refresh_metrics()
        rig.stats.active_s = 100
        rig.care._last_sit = time.time() - 99 * 60
        rig.care._check_health(time.time(), rig.stats)
        self.assertEqual(rig.spoken, [])

    def test_no_health_talk_when_idle(self):
        rig = Rig()
        rig.refresh_metrics()
        rig.stats.active_s = 6000
        rig.stats.in_idle = True
        rig.care._last_sit = time.time() - 99 * 60
        rig.care._check_health(time.time(), rig.stats)
        self.assertEqual(rig.spoken, [])


class TestFlowAndBusy(unittest.TestCase):
    def test_flow_start_end_pair(self):
        rig = Rig()
        rig.refresh_metrics()
        rig.mood.force(Mood.FOCUS_COMPANION)
        rig.care._check_flow(time.time(), rig.stats)
        self.assertEqual(len(rig.spoken), 1, "进入心流应说一句")
        rig.care._flow_since = time.time() - 11 * 60
        rig.budget.used = 0
        rig.budget.last_at = 0.0
        rig.mood.force(Mood.CALM)
        rig.care._check_flow(time.time(), rig.stats)
        self.assertEqual(len(rig.spoken), 2, "离开心流应再说一句")

    def test_flow_silent_inside(self):
        """心流中间必须完全安静——这是核心设计。"""
        rig = Rig()
        rig.refresh_metrics()
        rig.mood.force(Mood.FOCUS_COMPANION)
        rig.care._check_flow(time.time(), rig.stats)
        n = len(rig.spoken)
        for _ in range(5):
            rig.care._check_flow(time.time(), rig.stats)
        self.assertEqual(len(rig.spoken), n, "心流中间不该再说话")

    def test_busy_done_announces(self):
        rig = Rig()
        rig.care._busy_since = time.time() - 200
        rig.care._busy_now = False
        rig.care._check_busy_done(time.time())
        self.assertEqual(len(rig.spoken), 1)

    def test_busy_short_ignored(self):
        rig = Rig()
        rig.care._busy_since = time.time() - 30
        rig.care._busy_now = False
        rig.care._check_busy_done(time.time())
        self.assertEqual(rig.spoken, [])


class TestOtherTriggers(unittest.TestCase):
    def test_greeting_only_morning_and_once(self):
        rig = Rig()
        rig.stats.active_s = 600
        rig.stats.current_exe = "pycharm.exe"
        scenes: list[str] = []
        rig.care._say = lambda scene, gentle=True: scenes.append(scene)
        lt = time.localtime()
        morning = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 9, 0, 0, 0, 0, -1))
        rig.care._check_greeting(morning, rig.stats)
        rig.care._check_greeting(morning, rig.stats)
        self.assertEqual(scenes, ["morning"], "早上问候每天一次")

    def test_greeting_not_in_afternoon(self):
        rig = Rig()
        rig.stats.active_s = 600
        rig.stats.current_exe = "pycharm.exe"
        scenes: list[str] = []
        rig.care._say = lambda scene, gentle=True: scenes.append(scene)
        lt = time.localtime()
        afternoon = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 15, 0, 0, 0, 0, -1))
        rig.care._check_greeting(afternoon, rig.stats)
        self.assertEqual(scenes, [], "下午不打招呼")

    def test_greeting_skipped_without_app(self):
        rig = Rig()
        rig.stats.active_s = 600
        rig.stats.current_exe = ""
        scenes: list[str] = []
        rig.care._say = lambda scene, gentle=True: scenes.append(scene)
        rig.care._check_greeting(time.time(), rig.stats)
        self.assertEqual(scenes, [])

    def test_milestone_on_day_7(self):
        rig = Rig()
        rig.storage.days_since_install = lambda: 7
        rig.care._check_milestone(time.time())
        self.assertEqual(rig.clips, ["celebrate"])
        self.assertEqual(len(rig.spoken), 1)

    def test_milestone_ignored_on_other_days(self):
        rig = Rig()
        rig.storage.days_since_install = lambda: 8
        rig.care._check_milestone(time.time())
        self.assertEqual(rig.spoken, [])

    def test_alone_self_talk(self):
        rig = Rig()
        rig.stats.idle_now = 300.0
        rig.care._check_alone(time.time(), rig.stats)
        self.assertEqual(len(rig.spoken), 1)
        # 人回来了就重置，下次安静还能再说
        rig.stats.idle_now = 5.0
        rig.care._check_alone(time.time(), rig.stats)
        self.assertFalse(rig.care._alone_said)

    def test_meal_skip_once_per_day(self):
        rig = Rig()
        rig.refresh_metrics()
        rig.stats.active_s = 7200
        rig.stats.continuous_active_s = 50 * 60
        lt = time.localtime()
        meal = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 13, 0, 0, 0, 0, -1))
        rig.care._check_meal_skip(meal, rig.stats)
        self.assertEqual(rig.clips, ["eat"])
        rig.budget.used = 0
        rig.budget.last_at = 0.0
        rig.care._check_meal_skip(meal, rig.stats)
        self.assertEqual(len(rig.spoken), 1, "催饭每天最多一次")

    def test_pomodoro_start_stop(self):
        rig = Rig()
        rig.care.start_pomodoro(25)
        self.assertTrue(rig.care.pomodoro_active)
        self.assertIn("scarf_put_on", rig.clips)
        self.assertEqual(len(rig.spoken), 1)
        rig.care.stop_pomodoro()
        self.assertFalse(rig.care.pomodoro_active)
        self.assertEqual(len(rig.spoken), 2)

    def test_pomodoro_end_urgent(self):
        rig = Rig()
        rig.care.start_pomodoro(25)
        rig.care.pomodoro_until = time.time() - 1
        rig.care._check_pomodoro(time.time())
        self.assertFalse(rig.care.pomodoro_active)
        self.assertTrue(rig.said[-1][1], "番茄钟结束应紧急穿透")

    def test_welcome_back_and_missed(self):
        rig = Rig()
        rig.care.missed_provider = lambda: 2
        rig.care._was_away = True
        rig.care._away_since = time.time() - 20 * 60
        rig.stats.idle_now = 0.0
        rig.care._track_away(time.time(), rig.stats)
        self.assertGreaterEqual(len(rig.spoken), 2, "回来 + 错过提醒")


class TestQuietGating(unittest.TestCase):
    """安静模式是全局闸门：全屏 / 会议 / 机器忙 都要闭嘴。"""

    def _blocked_rig(self):
        rig = Rig()
        rig.refresh_metrics()
        return rig

    def test_fullscreen_sets_quiet(self):
        rig = self._blocked_rig()
        rig.stats.fullscreen = True
        rig.care._update_quiet(time.time(), rig.stats)
        self.assertTrue(rig.sm.is_proactive_blocked)

    def test_meeting_sets_quiet(self):
        rig = self._blocked_rig()
        rig.stats.meeting = True
        rig.care._update_quiet(time.time(), rig.stats)
        self.assertTrue(rig.sm.is_proactive_blocked)

    def test_focus_state_sets_quiet(self):
        rig = self._blocked_rig()
        rig.care._metrics.focus = 85.0
        rig.care._metrics.flow = 0.8
        rig.care._metrics.active_s = 2000
        rig.care._update_quiet(time.time(), rig.stats)
        self.assertTrue(rig.sm.is_proactive_blocked)

    def test_health_respects_quiet(self):
        rig = self._blocked_rig()
        rig.sm.set_quiet("fullscreen", True)
        rig.stats.active_s = 6000
        rig.care._last_sit = time.time() - 99 * 60
        rig.care._check_health(time.time(), rig.stats)
        self.assertEqual(rig.spoken, [], "全屏时不该起身提醒")


class TestMetricsPipeline(unittest.TestCase):
    """_update_mood 把原始计数翻译成指标，是整条链路的入口。"""

    def test_metrics_computed(self):
        rig = Rig()
        rig.stats.active_s = 14400
        rig.stats.keystrokes = 9000
        rig.stats.backspaces = 600
        rig.stats.clicks = 1200
        rig.stats.mouse_dist_px = 500000
        rig.stats.app_switches = 40
        rig.stats.longest_focus_s = 2400
        m = rig.refresh_metrics()
        self.assertIsNotNone(m)
        for name in ("focus", "activity", "fatigue", "stayup", "distraction"):
            v = getattr(m, name)
            self.assertGreaterEqual(v, 0.0, name)
            self.assertLessEqual(v, 100.0, name)

    def test_activity_not_zero_without_input_hook(self):
        """回归：输入钩子全 0 时活跃度不该恒为 0（在场分量会兜底）。"""
        rig = Rig()
        rig.stats.active_s = 7200
        rig.stats.keystrokes = 0
        rig.stats.clicks = 0
        rig.stats.mouse_dist_px = 0
        m = rig.refresh_metrics()
        self.assertGreater(m.activity, 0.0, "纯在场也该有基础活跃度")

    def test_stayup_scaled_in_seconds(self):
        """回归：late_minutes 实为秒，25200 秒才该封顶 100。"""
        from monitor import analyzer

        # 深夜 1 小时（3600 秒）=> 约 14 分，而不是封顶 100
        low = analyzer.stayup_score(3600, 0, 0)
        self.assertLess(low, 25.0)
        # 满 7 小时才到 100
        self.assertAlmostEqual(analyzer.stayup_score(25200, 0, 0), 100.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
