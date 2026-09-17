"""关怀功能包。

统一原则：所有主动行为都要过"打扰预算"，且在心流/睡眠/游戏/免打扰/编译中等情况下闭嘴。
宁可少说一句，也不要变成弹窗软件。
"""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal

from app import logging_setup
from app.config import config
from monitor import analyzer
from pet.state_machine import (
    R_BUSY,
    R_FOCUS,
    R_FULLSCREEN,
    R_MEETING,
    R_NIGHT,
    SLEEP,
)
from pet.mood import Mood
from . import lines

# 番茄钟自己的安静理由：结束番茄钟时只清这一条，
# 不会误清掉全屏/会议/心流这些由环境推导出来的安静理由。
R_POMODORO = "pomodoro"

log = logging_setup.get("care")

TICK_MS = 30_000
SIT_LIMIT = 50 * 60          # 连续活跃多久提醒起身
EYE_LIMIT = 45 * 60          # 护眼间隔（按屏幕时间）
WATER_LIMIT = 75 * 60        # 喝水间隔
IDLE_AWAY = 15 * 60          # 空闲多久算"离开"
AUTO_PLAY_AFTER = 30 * 60    # 多久没互动就自己玩
MILESTONES = (7, 30, 100, 365)

# ------------------------------------------------------------------ 指标 -> 台词/动作
# **这一张表是"统计数据 -> 企鹅态度"真正落地的地方。**
# 之前 mood 只改了动作频率和表情，台词库里那些按指标写好的场景
# （frustrated / distracted / cheerful / focused / sleepy / care_tired）
# 一条都没被念出来过：数据算了半天，企鹅一句话都没说。
# 每条心情配一个专属动作，保证"心情不同 -> 表情不同 + 动作不同 + 话不同"。
MOOD_VOICE: dict[Mood, tuple[str, str]] = {
    Mood.WORRIED_TIRED: ("care_tired", "yawn"),          # 疲劳度 > 75
    Mood.FRUSTRATED: ("frustrated", "sad"),              # 退格率 > 25%
    Mood.DISTRACTED: ("distracted", "look_around"),      # 分心指数 > 65
    Mood.FOCUSED: ("focused", "nod"),                    # 专注度 > 75
    Mood.CHEERFUL: ("cheerful", "celebrate"),            # 活跃度高、疲劳低
    Mood.SLEEPY: ("sleepy", "blink_double"),             # 长时间没什么动静
}
MOOD_VOICE_REPEAT = 25 * 60     # 同一种心情 25 分钟内不重复开口
MOOD_VOICE_MAX_IDLE = 300.0     # 人不在就别对着空椅子说话
FLOW_MIN_ANNOUNCE = 10 * 60     # 心流持续这么久，才值得在结束时提一句
BUSY_MIN_ANNOUNCE = 120.0       # 机器忙了这么久，才值得在忙完后提一句
ALONE_AFTER = 240.0             # 安静这么久，自言自语一句
MEAL_SKIP_WINDOWS = ((12 * 60 + 40, 14 * 60), (18 * 60 + 40, 20 * 60))


def _hm(text: str, fallback: tuple[int, int]) -> tuple[int, int]:
    try:
        hh, mm = (int(x) for x in str(text).split(":"))
        return (hh, mm)
    except Exception:
        return fallback


def _in_window(now: float, start: str, end: str) -> bool:
    """判断现在是否落在 [start, end) 时间窗内（支持跨零点）。"""
    sh, sm = _hm(start, (23, 30))
    eh, em = _hm(end, (7, 0))
    lt = time.localtime(now)
    cur = lt.tm_hour * 60 + lt.tm_min
    s = sh * 60 + sm
    e = eh * 60 + em
    if s == e:
        return False
    if s < e:
        return s <= cur < e
    return cur >= s or cur < e


class CareManager(QObject):
    """把所有"主动关怀"集中在一处裁决。"""

    speak = Signal(str, bool)      # (文案, 是否紧急/穿透安静模式)
    pet_clip = Signal(str)
    set_scale = Signal(float)      # 未使用，保留扩展位

    def __init__(self, sampler, storage, sm, bubble_budget, mood_tracker, parent=None) -> None:
        super().__init__(parent)
        self.sampler = sampler
        self.storage = storage
        self.sm = sm
        self.budget = bubble_budget
        self.mood = mood_tracker

        now = time.time()
        self._last_sit = now
        self._last_eye = now
        self._last_water = now
        self._last_play = now
        self._away_since = 0.0
        self._was_away = False

        self._greeted_day = ""
        self._night_said_day = ""
        self._stayup_day = ""
        self._milestone_day = ""
        self._summary_day = ""

        self._idle_prev = 0.0
        self._metrics = None

        # 指标驱动的行为状态
        self._mood_voice_at: dict[Mood, float] = {}
        self._last_mood: Mood | None = None
        self._flow_since = 0.0
        self._busy_now = False
        self._busy_since = 0.0
        self._meal_skip_day = ""
        self._alone_said = False

        self.pomodoro_until = 0.0
        self.pomodoro_active = False

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self.tick)

    # ==================================================================
    def start(self) -> None:
        self._timer.start()
        self.tick()

    def stop(self) -> None:
        self._timer.stop()

    # ==================================================================
    def tick(self) -> None:
        now = time.time()
        st = self.sampler.stats

        self._update_quiet(now, st)
        self._track_away(now, st)
        self._update_mood(now, st)
        self._check_sleep_window(now)
        self._check_greeting(now, st)
        self._check_health(now, st)
        self._check_stayup(now)
        self._check_milestone(now)
        self._check_auto_play(now)
        self._check_pomodoro(now)
        # 以下是"按统计数据说话/做动作"的部分
        self._check_mood_voice(now, st)
        self._check_flow(now, st)
        self._check_busy_done(now)
        self._check_meal_skip(now, st)
        self._check_alone(now, st)

    # ------------------------------------------------------------ 免打扰
    def _update_quiet(self, now: float, st) -> None:
        self.sm.set_quiet(R_FULLSCREEN, bool(st.fullscreen))
        self.sm.set_quiet(R_MEETING, bool(st.meeting))
        # 心流中：由 mood 驱动，企鹅主动保持安静
        in_flow = (
            self._metrics is not None
            and self._metrics.focus > 70
            and self._metrics.flow > 0.6
            and self._metrics.active_s > 1200
        )
        self.sm.set_quiet(R_FOCUS, in_flow)
        self._busy_now = self._system_busy()
        self.sm.set_quiet(R_BUSY, self._busy_now)

    def _system_busy(self) -> bool:
        """正在编译 / 打包 / 渲染时不要打扰。

        用整机 CPU 占用判断而不是进程名白名单——按进程名会把长期挂着的
        node / git 误判成"在忙"，导致企鹅永久闭嘴。
        """
        try:
            import psutil

            self._cpu = psutil.cpu_percent(interval=None)
            return self._cpu >= 60.0
        except Exception:
            return False

    # ------------------------------------------------------------ 心情
    def _update_mood(self, now: float, st) -> None:
        counts = st.clone_counts()
        rows = self.storage.metrics_range(4)
        prev = [r for r in reversed(rows) if r.get("day") != st.day][:3]
        m = analyzer.compute(
            day=st.day,
            counts=counts,
            key_times=list(st.key_times),
            focus_count=self.storage.focus_count(st.day),
            continuous_s=st.continuous_active_s,
            prev_metrics=prev,
        )
        self._metrics = m
        self.mood.update(m, now, st.idle_now)

    @property
    def metrics(self):
        return self._metrics

    # ------------------------------------------------------------ 睡眠时段
    def _check_sleep_window(self, now: float) -> None:
        if not bool(config.get("sleep_enabled", True)):
            self.sm.set_quiet(R_NIGHT, False)
            return

        start = config.get("sleep_start", "23:30")
        end = config.get("sleep_end", "07:00")
        should = _in_window(now, start, end)

        if should:
            # 顺序很重要：晚安必须赶在"进入安静/睡眠"之前说，
            # 否则 is_proactive_blocked 已经把嘴堵上了。
            if not self.sm.is_state(SLEEP):
                day = time.strftime("%Y-%m-%d", time.localtime(now))
                if self._night_said_day != day:
                    self._night_said_day = day
                    self._say("night", gentle=True)
                self.sm.set_quiet(R_NIGHT, True)
                self.sm.request(SLEEP, source="schedule", force=True)
            else:
                self.sm.set_quiet(R_NIGHT, True)
        else:
            self.sm.set_quiet(R_NIGHT, False)
            if self.sm.is_state(SLEEP):
                self.sm.back_to_idle()

    # ------------------------------------------------------------ 问候
    def _check_greeting(self, now: float, st) -> None:
        if st.active_s < 60 or not st.current_exe:
            return
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if self._greeted_day == day:
            return
        self._greeted_day = day
        hour = time.localtime(now).tm_hour
        if hour < 11:
            self._say("morning", gentle=True)

    # ------------------------------------------------------------ 离开 / 回来
    def _track_away(self, now: float, st) -> None:
        if st.idle_now >= IDLE_AWAY:
            if not self._was_away:
                self._was_away = True
                self._away_since = now
            return
        if self._was_away and st.idle_now < 5.0:
            self._was_away = False
            away_min = int((now - self._away_since) / 60.0) if self._away_since else 0
            if self._away_since and away_min >= 15 and not self.sm.is_proactive_blocked:
                self._say("welcome_back", gentle=True)
                missed = self._missed_from_scheduler()
                if missed:
                    self._speak_forced(lines.pick("missed", n=missed))
            self._last_play = now
            # 回来即重置健康计时，避免刚回来就催你起身
            self._last_sit = now
            self._last_eye = now

    def _missed_from_scheduler(self) -> int:
        cb = getattr(self, "missed_provider", None)
        return int(cb() if cb else 0)

    # ------------------------------------------------------------ 指标驱动的发言
    def _check_mood_voice(self, now: float, st) -> None:
        """心情（完全由统计指标算出）变了就说一句配套的话、做一个配套的动作。"""
        if self._metrics is None or self.sm.is_proactive_blocked:
            return
        mood = self.mood.mood
        spec = MOOD_VOICE.get(mood)
        if spec is None:
            return
        if st.idle_now >= MOOD_VOICE_MAX_IDLE:
            return
        if now - self._mood_voice_at.get(mood, 0.0) < MOOD_VOICE_REPEAT:
            return
        if not self.budget.can_speak(now):
            return
        self._mood_voice_at[mood] = now
        self.budget.record(now)
        scene, clip = spec
        self.pet_clip.emit(clip)
        self.speak.emit(lines.pick(scene), False)
        log.debug("按指标开口：心情=%s 场景=%s 动作=%s", mood.value, scene, clip)

    def _check_flow(self, now: float, st) -> None:
        """进入/离开心流各说一句，中间一声不吭。

        心流中刻意完全安静是这个企鹅最核心的设计，所以只在**两个瞬间**开口：
        刚进入时说一句"我不打扰你"，长段专注结束时说一句"刚才那段很稳"。
        """
        mood = self.mood.mood
        prev = self._last_mood
        self._last_mood = mood

        if mood == Mood.FOCUS_COMPANION and prev != Mood.FOCUS_COMPANION:
            self._flow_since = now
            if not self.sm.is_proactive_blocked and self.budget.can_speak(now):
                self.budget.record(now)
                self.speak.emit(lines.pick("focus_start"), False)
            return

        if prev == Mood.FOCUS_COMPANION and mood != Mood.FOCUS_COMPANION:
            held = now - self._flow_since if self._flow_since else 0.0
            self._flow_since = 0.0
            if (held >= FLOW_MIN_ANNOUNCE and not self.sm.is_proactive_blocked
                    and self.budget.can_speak(now)):
                self.budget.record(now)
                self.speak.emit(lines.pick("focus_end"), False)

    def _check_busy_done(self, now: float) -> None:
        """机器刚忙完（编译/渲染/打包）时补一句。

        忙的时候企鹅是闭嘴的（R_BUSY），所以那句话只能等忙完再说——
        这也顺带解释了"刚才为什么没理你"。
        """
        if self._busy_now:
            if not self._busy_since:
                self._busy_since = now
            return
        if not self._busy_since:
            return
        held = now - self._busy_since
        self._busy_since = 0.0
        if held < BUSY_MIN_ANNOUNCE or self.sm.is_proactive_blocked:
            return
        if not self.budget.can_speak(now):
            return
        self.budget.record(now)
        self.speak.emit(lines.pick("busy_no_disturb"), False)

    def _check_meal_skip(self, now: float, st) -> None:
        """饭点过去快一小时了人还在敲键盘 -> 提醒一次，每天最多一次。"""
        if self._metrics is None or self.sm.is_proactive_blocked or st.in_idle:
            return
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if self._meal_skip_day == day:
            return
        lt = time.localtime(now)
        cur = lt.tm_hour * 60 + lt.tm_min
        if not any(a <= cur < b for a, b in MEAL_SKIP_WINDOWS):
            return
        # 得是"已经干了一阵子"才说，否则刚开机就被催饭很烦
        if st.active_s < 3600 or st.continuous_active_s < 40 * 60:
            return
        self._meal_skip_day = day
        self._care("care_meal_skip", "eat")

    def _check_alone(self, now: float, st) -> None:
        """安静了一阵子（人还在，只是没敲键盘）就自言自语一句。"""
        if st.idle_now >= ALONE_AFTER and not self._alone_said:
            self._alone_said = True
            if self.sm.is_proactive_blocked or not self.budget.can_speak(now):
                return
            self.budget.record(now)
            self.speak.emit(lines.pick("nobody_here"), False)
        elif st.idle_now < 60.0:
            self._alone_said = False

    # ------------------------------------------------------------ 健康包
    def _check_health(self, now: float, st) -> None:
        if self.sm.is_proactive_blocked or st.active_s < 300:
            return
        if st.in_idle or st.locked:
            return
        # 起身
        if st.continuous_active_s >= SIT_LIMIT and now - self._last_sit >= SIT_LIMIT:
            self._last_sit = now
            self._care("care_sit_long", "stretch")
            return
        # 护眼（按屏幕时间）
        if now - self._last_eye >= EYE_LIMIT and st.active_s >= EYE_LIMIT:
            self._last_eye = now
            self._care("care_eyes", "look_around")
            return
        # 喝水
        if now - self._last_water >= WATER_LIMIT and st.active_s >= WATER_LIMIT:
            self._last_water = now
            self._care("care_water", "nod")

    def _care(self, scene: str, clip: str) -> None:
        if not self.budget.can_speak():
            return
        self.budget.record()
        self.pet_clip.emit(clip)
        self.speak.emit(lines.pick(scene), False)

    # ------------------------------------------------------------ 熬夜关切
    def _check_stayup(self, now: float) -> None:
        m = self._metrics
        if m is None or self.sm.is_proactive_blocked:
            return
        if m.stayup < 50:
            return
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if self._stayup_day == day:
            return
        self._stayup_day = day
        streak = self._late_streak()
        if streak >= 3:
            text = lines.pick("care_stayup_hard", n=streak)
        elif m.stayup > 70:
            text = lines.pick("care_stayup")
        else:
            text = lines.pick("care_stayup")
        if not self.budget.can_speak(now, urgent=m.stayup > 70):
            return
        self.budget.record(now)
        self.pet_clip.emit("sad")
        self.speak.emit(text, m.stayup > 70)

    def _late_streak(self) -> int:
        rows = self.storage.metrics_range(4)
        streak = 0
        for r in reversed(rows):
            if float(r.get("stayup") or 0) >= 50:
                streak += 1
            else:
                break
        return streak

    # ------------------------------------------------------------ 里程碑
    def _check_milestone(self, now: float) -> None:
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if self._milestone_day == day:
            return
        self._milestone_day = day
        days = self.storage.days_since_install()
        if days in MILESTONES and not self.sm.is_proactive_blocked:
            if self.budget.can_speak(now):
                self.budget.record(now)
                self.pet_clip.emit("celebrate")
                self.speak.emit(lines.pick("milestone", n=days), False)
        self.storage.set_meta("days_with_pet", days)

    # ------------------------------------------------------------ 无聊自娱
    def _check_auto_play(self, now: float, ) -> None:
        if self.sm.is_proactive_blocked or self.pomodoro_active:
            return
        if now - self._last_play < AUTO_PLAY_AFTER:
            return
        self._last_play = now
        self.pet_clip.emit("wing_flap")
        if self.budget.can_speak(now):
            self.budget.record(now)
            self.speak.emit(lines.pick("self_play"), False)

    def note_interaction(self) -> None:
        """用户点了企鹅 / 拖了它 / 说了话，重置"无聊"计时。"""
        self._last_play = time.time()

    # ------------------------------------------------------------ 番茄钟
    def start_pomodoro(self, minutes: int = 25) -> None:
        self.pomodoro_active = True
        self.pomodoro_until = time.time() + max(5, int(minutes)) * 60
        self.sm.set_quiet(R_POMODORO, True)
        self.pet_clip.emit("scarf_put_on")
        self.speak.emit(lines.pick("pomodoro_start"), False)
        self.budget.record()

    def stop_pomodoro(self, announce: bool = True) -> None:
        if not self.pomodoro_active:
            return
        self.pomodoro_active = False
        self.pomodoro_until = 0.0
        self.sm.set_quiet(R_POMODORO, False)
        if announce:
            self.speak.emit(lines.pick("pomodoro_stop"), False)

    def _check_pomodoro(self, now: float) -> None:
        if not self.pomodoro_active:
            return
        if now >= self.pomodoro_until:
            self.pomodoro_active = False
            self.sm.set_quiet(R_POMODORO, False)
            self.pet_clip.emit("celebrate")
            self.speak.emit(lines.pick("pomodoro_end"), True)
            self.budget.record()

    def pomodoro_remaining(self) -> int:
        if not self.pomodoro_active:
            return 0
        return max(0, int(self.pomodoro_until - time.time()))

    # ------------------------------------------------------------ 小结
    def daily_summary_text(self) -> str:
        m = self._metrics
        day = self.sampler.stats.day
        if m is None or m.active_s < 600:
            return ""
        apps = self.storage.top_apps(day, 1)
        top = apps[0]["app_name"] if apps else "还没有"
        text = lines.pick(
            "summary",
            active=analyzer.fmt_duration(m.active_s),
            top_app=top,
            n=self.storage.focus_count(day),
            longest=analyzer.fmt_duration(m.longest_focus_s),
        )
        return text

    # ------------------------------------------------------------ 工具
    def _say(self, scene: str, gentle: bool = True) -> None:
        if self.sm.is_proactive_blocked and gentle:
            return
        if gentle and not self.budget.can_speak():
            return
        if gentle:
            self.budget.record()
        self.speak.emit(lines.pick(scene), False)

    def _speak_forced(self, text: str) -> None:
        self.budget.record()
        self.speak.emit(text, False)
