"""指标计算：把原始计数翻译成"用户现在什么状态"。

设计原则：
  * 每个指标都有明确的输入、归一化方式和分档阈值，不做玄学。
  * 全部是纯函数，便于用构造数据断言（见 tests/test_analyzer.py）。
  * 单日数值偏噪声，所以疲劳与熬夜用滚动多日平滑后再判档。
"""
from __future__ import annotations

import statistics
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

BURST_GAP = 1.5          # 秒，按键间隔小于它算同一段连打
MIN_BURST_KEYS = 3


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else (hi if v > hi else v)


def scale(v: float, full: float) -> float:
    """把 v 映射到 0..1，v >= full 时封顶。"""
    if full <= 0:
        return 0.0
    return clamp(v / full)


@dataclass
class Metrics:
    day: str = ""
    active_s: int = 0
    keystrokes: int = 0
    backspaces: int = 0
    clicks: int = 0
    mouse_px: int = 0
    switches: int = 0
    late_minutes: int = 0
    longest_focus_s: int = 0
    focus_count: int = 0
    idle_gap_cnt: int = 0
    continuous_s: int = 0
    first_ts: int = 0
    last_ts: int = 0

    # 派生
    br: float = 0.0            # 退格率
    sr: float = 0.0            # 应用切换率（次/小时）
    ipm: float = 0.0           # 每分钟输入密度
    wpm: float = 0.0           # 粗算词/分钟
    burst_med: float = 0.0     # 连打长度中位数

    focus: float = 0.0         # 专注度 0-100
    activity: float = 0.0      # 活跃度 0-100
    fatigue: float = 0.0       # 疲劳度 0-100
    stayup: float = 0.0        # 熬夜指数 0-100
    flow: float = 0.0          # 心流强度 0-1
    distraction: float = 0.0   # 分心指数 0-100
    mood: str = ""

    def as_row(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("continuous_s", None)
        return d


# ---------------------------------------------------------------- 基础量
def backspace_rate(keystrokes: int, backspaces: int) -> float:
    return backspaces / max(1, keystrokes)


def switch_rate(switches: int, active_s: int) -> float:
    hours = max(active_s / 3600.0, 0.1)
    return switches / hours


def burst_stats(key_times: Sequence[float]) -> tuple[float, int]:
    """返回 (连打长度中位数, 段数)。用时间戳把按键切成一段段连续输入。"""
    ts = sorted(key_times)
    if len(ts) < MIN_BURST_KEYS:
        return 0.0, 0
    lengths: list[int] = []
    cur = 1
    for a, b in zip(ts, ts[1:]):
        if b - a <= BURST_GAP:
            cur += 1
        else:
            lengths.append(cur)
            cur = 1
    lengths.append(cur)
    lengths = [n for n in lengths if n >= 1]
    if not lengths:
        return 0.0, 0
    return float(statistics.median(lengths)), len(lengths)


# ---------------------------------------------------------------- 五维指标
def focus_score(longest_focus_s: int, sr: float, br: float) -> float:
    """专注度 = 最长专注块 50% + 少切换 30% + 少退格 20%。"""
    f_block = scale(longest_focus_s, 2700.0)          # 45 分钟满分
    f_switch = clamp(1.0 - sr / 40.0)
    f_back = clamp(1.0 - (br - 0.08) / 0.22)
    return 100.0 * (0.50 * f_block + 0.30 * f_switch + 0.20 * f_back)


def activity_score(keystrokes: int, clicks: int, mouse_px: int, active_s: int) -> tuple[float, float]:
    """返回 (活跃度, 每分钟输入密度)。"""
    minutes = max(active_s / 60.0, 1.0)
    ipm = (keystrokes + 1.5 * clicks + mouse_px / 60.0) / minutes
    return 100.0 * clamp((ipm - 15.0) / (220.0 - 15.0)), ipm


def fatigue_score(
    active_s: int,
    continuous_s: int,
    late_minutes: int,
    idle_gap_cnt: int,
    prev_rolling: float | None = None,
) -> float:
    """疲劳度：用满 10 小时 / 连轴 3.5 小时 / 深夜 3 小时 / 空闲碎片化为满分参照。"""
    h_active = active_s / 3600.0
    h_cont = continuous_s / 3600.0
    frag = idle_gap_cnt / max(h_active, 0.5)
    raw = (
        0.30 * scale(h_active, 10.0)
        + 0.25 * scale(h_cont, 3.5)
        + 0.25 * scale(late_minutes, 180.0)
        + 0.20 * scale(max(0.0, frag - 2.0), 8.0)
    )
    today = 100.0 * clamp(raw)
    if prev_rolling is None or prev_rolling <= 0:
        return today
    # 与历史滚动值做指数平滑，避免单日噪声决定心情
    return 0.65 * today + 0.35 * prev_rolling


def stayup_score(
    late_minutes: int,
    last_ts: int,
    first_ts: int,
    prev_rolling: float | None = None,
    prev_two: Iterable[float] = (),
) -> float:
    """熬夜指数：深夜活跃分钟数为主，加"凌晨还在用"的附加惩罚。"""
    today = 100.0 * scale(late_minutes, 240.0)   # 4 小时封顶

    if last_ts:
        h = time.localtime(last_ts).tm_hour
        if 0 <= h < 5:
            today = min(100.0, today + 15.0)
    if first_ts and prev_rolling is not None and prev_rolling >= 60:
        if time.localtime(first_ts).tm_hour < 7:
            today = max(0.0, today - 10.0)

    if prev_rolling is None:
        return today
    prevs = [prev_rolling] + [p for p in prev_two]
    weights = (0.5, 0.3, 0.2)
    total = 0.5 * today
    for w, p in zip(weights[1:], prevs):
        total += w * p
    return total


def flow_score(burst_med: float, wpm: float, br: float) -> float:
    """心流强度：连打越长、速度越稳、退格越少越接近 1。"""
    a = scale(max(0.0, burst_med - 8.0), 40.0)
    b = scale(wpm, 60.0)
    c = 1.0 - scale(br, 0.30)
    return clamp(0.5 * a + 0.3 * b + 0.2 * c)


def distraction_score(sr: float, longest_focus_s: int) -> float:
    f_block = scale(longest_focus_s, 2700.0)
    return 100.0 * clamp(0.6 * scale(sr, 40.0) + 0.4 * (1.0 - f_block))


# ---------------------------------------------------------------- 汇总
def compute(
    day: str,
    counts: dict[str, Any],
    key_times: Sequence[float] = (),
    focus_count: int = 0,
    continuous_s: int = 0,
    prev_metrics: Sequence[dict[str, Any]] = (),
) -> Metrics:
    """由原始计数算出全部指标。prev_metrics 按时间倒序（[昨天, 前天, ...]）。"""
    m = Metrics(day=day)
    m.active_s = int(counts.get("active_s", 0) or 0)
    m.keystrokes = int(counts.get("keystrokes", 0) or 0)
    m.backspaces = int(counts.get("backspaces", 0) or 0)
    m.clicks = int(counts.get("clicks", 0) or 0)
    m.mouse_px = int(counts.get("mouse_dist_px", 0) or 0)
    m.switches = int(counts.get("app_switches", 0) or 0)
    m.late_minutes = int(counts.get("late_minutes", 0) or 0)
    m.longest_focus_s = int(counts.get("longest_focus_s", 0) or 0)
    m.idle_gap_cnt = int(counts.get("idle_gap_cnt", 0) or 0)
    m.first_ts = int(counts.get("first_ts", 0) or 0)
    m.last_ts = int(counts.get("last_ts", 0) or 0)
    m.focus_count = int(focus_count)
    m.continuous_s = int(continuous_s)

    m.br = backspace_rate(m.keystrokes, m.backspaces)
    m.sr = switch_rate(m.switches, m.active_s)
    m.activity, m.ipm = activity_score(m.keystrokes, m.clicks, m.mouse_px, m.active_s)

    m.burst_med, _burst_cnt = burst_stats(key_times)
    minutes = max(m.active_s / 60.0, 1.0)
    m.wpm = (m.keystrokes / 5.0) / minutes

    m.focus = focus_score(m.longest_focus_s, m.sr, m.br)
    m.flow = flow_score(m.burst_med, m.wpm, m.br)
    m.distraction = distraction_score(m.sr, m.longest_focus_s)

    prev_fatigue = float(prev_metrics[0].get("fatigue") or 0) if prev_metrics else None
    m.fatigue = fatigue_score(
        m.active_s, m.continuous_s, m.late_minutes, m.idle_gap_cnt, prev_fatigue
    )

    prev_stayup = float(prev_metrics[0].get("stayup") or 0) if prev_metrics else None
    prev_two = [float(p.get("stayup") or 0) for p in prev_metrics[1:3]]
    m.stayup = stayup_score(m.late_minutes, m.last_ts, m.first_ts, prev_stayup, prev_two)

    return m


# ---------------------------------------------------------------- 分档描述
def focus_band(v: float) -> str:
    if v >= 75:
        return "深度专注"
    if v >= 50:
        return "比较稳"
    if v >= 25:
        return "有点散"
    return "很碎片"


def fatigue_band(v: float) -> str:
    if v >= 75:
        return "很累"
    if v >= 55:
        return "偏累"
    if v >= 30:
        return "还行"
    return "精神"


def activity_band(v: float) -> str:
    if v >= 60:
        return "高强度"
    if v >= 20:
        return "正常"
    return "偏静"


def stayup_band(v: float) -> str:
    if v >= 75:
        return "严重熬夜"
    if v >= 50:
        return "连续晚睡"
    if v >= 20:
        return "偶有晚睡"
    return "作息正常"


def switch_band(sr: float) -> str:
    if sr > 40:
        return "严重碎片化"
    if sr > 20:
        return "偏分心"
    if sr > 8:
        return "正常"
    return "深度专注"


def backspace_band(br: float) -> str:
    if br > 0.25:
        return "反复改"
    if br > 0.15:
        return "偏多"
    if br > 0.06:
        return "正常"
    return "很流畅"


def fmt_duration(seconds: int) -> str:
    """把秒数写成"3h20m"这样好读的形式。"""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    if h and m:
        return f"{h}h{m:02d}m"
    if h:
        return f"{h}h"
    if m:
        return f"{m}m"
    return f"{seconds}s"


def hourly_histogram(sessions: Iterable[dict]) -> list[float]:
    """把会话明细摊到 24 个小时格里，返回每格相对峰值的高度（0..1）。

    用于"一眼看出作息分布"的热力条——比总时长更能说明问题。
    """
    from datetime import datetime, timedelta

    buckets = [0.0] * 24
    for s in sessions:
        try:
            a = int(s.get("start_ts") or 0)
            b = int(s.get("end_ts") or 0)
        except (TypeError, ValueError):
            continue
        if b <= a:
            continue
        cur = a
        guard = 0
        while cur < b and guard < 200:
            guard += 1
            dt = datetime.fromtimestamp(cur)
            nxt = (dt.replace(minute=0, second=0, microsecond=0)
                   + timedelta(hours=1)).timestamp()
            seg_end = min(b, nxt)
            buckets[dt.hour] += max(0.0, seg_end - cur)
            cur = int(seg_end)
    peak = max(buckets) if buckets else 0.0
    if peak <= 0:
        return [0.0] * 24
    return [v / peak for v in buckets]


def hour_span(sessions: Iterable[dict]) -> tuple[int, int]:
    """首次 / 最后一次活跃的小时数，用于描述作息。"""
    first, last = None, None
    for s in sessions:
        a = s.get("start_ts")
        b = s.get("end_ts")
        if a:
            first = a if first is None else min(first, a)
        if b:
            last = b if last is None else max(last, b)
    if first is None or last is None:
        return (0, 0)
    return (time.localtime(first).tm_hour, time.localtime(last).tm_hour)
