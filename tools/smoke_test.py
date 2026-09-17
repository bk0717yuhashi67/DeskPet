"""冒烟测试：把整条装配链跑一遍，尽早抓出接口错误。

    .venv\\Scripts\\python.exe tools\\smoke_test.py          # 只建对象，不显示窗口
    .venv\\Scripts\\python.exe tools\\smoke_test.py --gui    # 额外显示窗口并跑穿透自检
    .venv\\Scripts\\python.exe tools\\smoke_test.py -v       # 失败时打印完整堆栈

会检查：模块能否导入、对象能否构造、渲染能否出图、穿透自检结果、数据库能否读写。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

GUI = "--gui" in sys.argv
VERBOSE = "-v" in sys.argv

CHECKS: list[tuple[str, object]] = []
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str):
    """注册一个检查项。所有检查按定义顺序在最后统一执行。"""
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


def run_all() -> None:
    for name, fn in CHECKS:
        t0 = time.time()
        try:
            info = fn()
            RESULTS.append((name, True, f"{info or ''} ({time.time() - t0:.2f}s)"))
        except Exception as exc:
            RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
            if VERBOSE or GUI:
                print(f"\n--- {name} 失败 ---")
                traceback.print_exc()


def check_imports() -> bool:
    t0 = time.time()
    try:
        import PySide6
        import psutil
        import pynput

        RESULTS.append((
            "导入 PySide6 / pynput / psutil",
            True,
            f"PySide6 {PySide6.__version__}, psutil {psutil.__version__} ({time.time() - t0:.2f}s)",
        ))
        return True
    except Exception as exc:
        RESULTS.append(("导入 PySide6 / pynput / psutil", False, f"{type(exc).__name__}: {exc}"))
        return False


# ======================================================================
# 各检查项定义
# ======================================================================
@check("配色 / 姿势 / 缓动 / 动作库")
def _pet_core():
    from pet import clips, easing, palette, pose  # noqa: F401
    from pet.actions import RandomScheduler
    from pet.animator import Animator

    a = Animator()
    a.play("hop", 0.0)
    for i in range(80):
        a.tick(i * 0.016)
    names = clips.all_names()

    s = RandomScheduler(3)
    s.arm(0.0)
    fires: list[int] = []
    for t in range(0, 8000, 3):
        if s.poll(float(t), blocked=False):
            fires.append(t)
    # 真正该保证的不变式：任意 3600 秒滑动窗口内不超过 per_hour 次
    for i, t in enumerate(fires):
        within = sum(1 for u in fires[: i + 1] if u >= t - 3600)
        assert within <= 3, f"t={t} 时过去一小时内已触发 {within} 次"
    assert 4 <= len(fires) <= 9, f"8000 秒内触发 {len(fires)} 次，数量不合理: {fires}"

    assert len(names) >= 18, f"动作库太小: {len(names)}"
    assert len(easing.BY_NAME) >= 10
    return (f"{len(names)} 个动作 / {len(easing.BY_NAME)} 个缓动；"
            f"8000 秒内随机动作 {len(fires)} 次（上限 3 次/小时）")


@check("企鹅离屏渲染")
def _render():
    from pet import rig
    from pet.pose import WIN_H, WIN_W, Pose

    pm = rig.render_to_pixmap(Pose(), size=WIN_W)
    assert not pm.isNull(), "离屏渲染失败"
    img = pm.toImage()
    opaque = 0
    for y in range(0, img.height(), 7):
        for x in range(0, img.width(), 7):
            if img.pixelColor(x, y).alpha() > 10:
                opaque += 1
    assert opaque > 40, f"渲染结果几乎是空的（不透明采样点 {opaque}）"
    return f"{WIN_W}x{WIN_H}，不透明采样点 {opaque}"


@check("命中判定几何")
def _hit():
    from pet import rig
    from pet.pose import BODY_CX, BODY_CY, HEAD_CX, HEAD_CY, OFF_X, OFF_Y, Pose

    p = Pose()
    bx, by = rig.transform_point(p, BODY_CX, BODY_CY)
    assert rig.hit_test(p, bx, by, 6.0), "身体中心竟然没命中"
    # 头部中心用坐标算，别写死像素差——改造型时那个差值会变，
    # 写死过一次，改完造型测试就假失败了。
    hx, hy = rig.transform_point(p, HEAD_CX, HEAD_CY)
    assert rig.hit_test(p, hx, hy, 6.0), "头部中心竟然没命中"
    assert not rig.hit_test(p, 3.0, 3.0, 6.0), "左上角空白区竟然命中了"
    assert not rig.hit_test(p, OFF_X + 3, OFF_Y + 190, 6.0), "脚边地面竟然命中了"
    # 头顶正上方必须是空的（气泡锚点就在那附近，不能被判成"点在身上"）
    tx, ty = rig.transform_point(p, HEAD_CX, HEAD_CY - 90.0)
    assert not rig.hit_test(p, tx, ty, 6.0), "头顶上方竟然命中了"
    # 身体旋转后判定区要跟着转（旧版忽略旋转，翻跟头时判定会错位）
    spun = Pose(body_rot=90.0)
    sx, sy = rig.transform_point(spun, BODY_CX, BODY_CY - 40.0)
    assert rig.hit_test(spun, sx, sy, 6.0), "旋转 90° 后身体上方竟然没命中"
    return "身体/头部命中，空白区穿透，旋转后判定跟随"


@check("存储层（SQLite 建表与读写）")
def _storage():
    from monitor.storage import Storage

    tmp = Path(tempfile.mkdtemp()) / "smoke.db"
    st = Storage(tmp)
    day = "2026-09-17"
    st.bump_daily(day, active_s=120, keystrokes=500, backspaces=40, first_ts=1000)
    st.bump_daily(day, active_s=60, keystrokes=100, last_ts=9000)
    st.bump_app(day, "code", "VS Code", "工作", 90, 1)
    st.bump_app(day, "code", "VS Code", "工作", 30, 2)
    st.add_focus_block(day, 1000, 3100, "code", "VS Code", 300)
    st.add_session(day, 1, "code", "VS Code", "工作", "main.py", 1000, 3100, 2100)
    st.set_metrics(day, {"focus": 50, "fatigue": 20, "mood": "focused"})
    row = st.daily(day)
    assert row["active_s"] == 180, row["active_s"]
    assert row["keystrokes"] == 600
    assert row["first_ts"] == 1000 and row["last_ts"] == 9000
    assert st.app_seconds(day, "code") == 120
    assert st.focus_count(day) == 1
    assert st.top_apps(day)[0]["app_name"] == "VS Code"
    assert st.category_totals(day).get("工作") == 120
    assert len(st.sessions_of(day)) == 1
    st.set_meta("high_score", 42)
    assert st.get_meta("high_score") == 42
    st.clear_all()
    assert st.daily(day) is None
    st.close()
    return "UPSERT 累加 / min-max 时间戳 / 聚合 / 清空 均正常"


@check("指标计算")
def _analyzer():
    from monitor import analyzer

    m = analyzer.compute(
        day="2026-09-17",
        counts={
            "active_s": 18000, "keystrokes": 12000, "backspaces": 2400,
            "clicks": 900, "mouse_dist_px": 300000, "app_switches": 120,
            "late_minutes": 30, "longest_focus_s": 1500, "idle_gap_cnt": 40,
            "first_ts": 0, "last_ts": 0,
        },
        key_times=[i * 0.3 for i in range(300)],
        focus_count=3,
        continuous_s=5400,
    )
    for k in ("focus", "fatigue", "activity", "stayup", "distraction"):
        v = getattr(m, k)
        assert 0.0 <= v <= 100.0, f"{k}={v} 越界"
    assert 0.0 <= m.flow <= 1.0
    assert m.br > 0.19, m.br
    return (f"专注 {m.focus:.0f} 疲劳 {m.fatigue:.0f} 活跃 {m.activity:.0f} "
            f"熬夜 {m.stayup:.0f} 心流 {m.flow:.2f}")


@check("心情映射")
def _mood():
    from monitor.analyzer import Metrics
    from pet.mood import Mood, MoodTracker, evaluate

    assert evaluate(Metrics(stayup=90, active_s=3600)) == Mood.CONCERN_STAYUP
    assert evaluate(Metrics(fatigue=90, active_s=3600)) == Mood.WORRIED_TIRED
    assert evaluate(Metrics(focus=85, flow=0.8, activity=50)) == Mood.FOCUS_COMPANION
    assert evaluate(Metrics()) == Mood.CALM
    t = MoodTracker()
    for _ in range(5):
        t.update(Metrics(), None, 0.0)
    return f"优先级判定正确，当前心情 {t.mood.value}"


@check("应用分类")
def _appclass():
    from monitor import appclass

    cases = [
        ("chrome", "bilibili - Google Chrome", "视频"),
        ("code", "main.py - Visual Studio Code", "工作"),
        ("chrome", "baidu.com - Google Chrome", "浏览器-未分类"),
        ("wechat", "微信", "社交"),
        ("wemeetapp", "腾讯会议", "会议"),
        ("winword", "会议纪要.docx - Word", "工作"),
    ]
    for exe, title, want in cases:
        got, _ = appclass.classify(exe, title)
        assert got == want, f"{exe}/{title} => {got}, 期望 {want}"
    assert appclass.is_meeting("wemeetapp", "")
    assert appclass.is_meeting("wechat", "正在共享屏幕")
    assert not appclass.is_meeting("code", "main.py")
    return f"{len(cases)} 条分类 + 会议检测 通过"


@check("提醒数据与调度")
def _remind():
    from monitor.storage import Storage
    from remind.model import Reminder
    from remind.scheduler import ReminderScheduler, matches_day, occurrences
    from remind.store import ReminderStore

    tmp = Path(tempfile.mkdtemp()) / "smoke2.db"
    st = Storage(tmp)
    store = ReminderStore(st)
    n = store.ensure_builtins()
    assert n == 3, n
    items = store.all()
    assert [r.time_hhmm for r in items] == ["12:00", "18:00", "22:00"]
    assert [r.action for r in items] == ["eat", "eat", "sleep"]

    rid = store.add(Reminder(time_hhmm="07:05", message="喝水", action="none"))
    assert store.get(rid).message == "喝水"
    d = store.get(rid)
    d.enabled = False
    store.update(d)
    assert not store.get(rid).enabled
    store.delete(rid)
    assert len(store.all()) == 3

    # 周三那一天
    wk = Reminder(kind="weekly", time_hhmm="09:00", weekdays=1 << 2)
    assert matches_day(wk, "2026-09-16")
    assert not matches_day(wk, "2026-09-17")

    # 12:00 在 11:00-13:00 之间应该只出现一次
    import time as _t
    base = _t.mktime(_t.strptime("2026-09-17 11:00:00", "%Y-%m-%d %H:%M:%S"))
    occ = occurrences(items[0], int(base), int(base + 7200))
    assert len(occ) == 1, occ

    sched = ReminderScheduler(st, store)
    snz = store.make_snooze(items[0], 10)
    assert snz.id == -1
    st.close()
    return "内置 3 条 + 增删改 + 星期匹配 + 稍后提醒"


@check("打扰预算")
def _budget():
    from care.budget import ActionBudget, BubbleBudget

    b = BubbleBudget()
    b.reconfigure()
    fired = 0
    for t in range(0, 7200, 30):
        if b.can_speak(float(t)):
            b.record(float(t))
            fired += 1
    assert fired >= 1, "两小时内一条都不让说，设置太紧了"
    a = ActionBudget()
    a.reconfigure()
    return f"两小时窗口内允许 {fired} 条主动消息"


@check("台词库")
def _lines():
    from care import lines

    total = sum(len(v) for v in lines.SCENES.values())
    for scene in lines.SCENES:
        for _ in range(6):
            assert lines.pick(scene), scene
    assert lines.pick("summary", active="3h", top_app="VS Code", n=2, longest="52m")
    assert lines.pick("remind_generic", msg="喝水")
    return f"{len(lines.SCENES)} 个场景 / {total} 条文案"


@check("抛球物理")
def _physics():
    from game import physics

    b = physics.Ball(x=400, y=300, r=18, alive=True)
    bounds = physics.Bounds(0, 1000, 800, 100)
    for _ in range(1200):
        physics.step(b, physics.FIXED_DT, bounds)
    assert b.grounded, "球最终没停下来"
    assert b.y + b.r <= bounds.floor + 1e-6
    vx, vy = physics.launch_velocity(100, 700, 800, 600, 0.7)
    assert vx > 0 and vy < 0
    # 飞行时间必须落在手感区间里
    assert physics.MIN_FLIGHT <= physics.flight_time_for(900.0, 620.0) <= physics.MAX_FLIGHT
    return f"最终静止于 x={b.x:.0f}, y={b.y:.0f}，飞行 {physics.flight_time_for(900.0, 620.0):.2f}s"


# ======================================================================
# 需要 Qt 的检查
# ======================================================================
KEEP: dict = {}


@check("导入应用主模块（能抓出写错的 Qt 导入）")
def _import_main():
    import importlib

    mod = importlib.import_module("main")
    assert hasattr(mod, "main") and callable(mod.main)
    assert hasattr(mod, "DeskPet")
    assert hasattr(mod, "acquire_single_instance")
    return "main / DeskPet / acquire_single_instance 均可用"


@check("构建窗口与全链路装配")
def _windows():
    from app.config import config
    from care.budget import ActionBudget, BubbleBudget
    from game.overlay import GameOverlay
    from game.throw_game import ThrowGame
    from monitor.hooks import InputHooks
    from monitor.sampler import Sampler
    from monitor.storage import Storage
    from pet.animator import Animator
    from pet.mood import MoodTracker
    from pet.state_machine import ACT, IDLE, StateMachine
    from ui.bubble import Bubble
    from ui.pet_window import PetWindow

    tmp = Path(tempfile.mkdtemp()) / "smoke3.db"
    storage = Storage(tmp)
    hooks = InputHooks()
    sm = StateMachine()
    anim = Animator()
    pet = PetWindow(anim, sm)
    bubble = Bubble()
    overlay = GameOverlay()
    sampler = Sampler(storage, hooks)
    game = ThrowGame(overlay, hooks, storage,
                     lambda: (float(pet.x()), float(pet.y()), pet.scale))

    pet.resize(240, 240)
    pet.move(400, 300)
    assert sm.state == IDLE
    assert sm.request(ACT, source="smoke")
    assert sm.state == ACT
    sm.back_to_idle()
    assert sm.state == IDLE

    bubble.show_message("测试气泡", pet.head_global())
    app = _app()
    app.processEvents()
    assert bubble.isVisible(), "气泡没有显示"
    assert bubble.height() > 30, f"气泡尺寸异常: {bubble.size()}"
    bubble.hide_now()

    assert game.handle_click(0, 0) is False, "游戏未开始却消费了点击"
    config.set("random_action_per_hour", 3)

    KEEP.update(dict(storage=storage, hooks=hooks, sm=sm, anim=anim, pet=pet,
                     bubble=bubble, overlay=overlay, sampler=sampler, game=game,
                     budget=BubbleBudget(), abudget=ActionBudget(), mood=MoodTracker()))
    return "PetWindow / Bubble / Overlay / Sampler / ThrowGame 均构建成功"


@check("采样器真实读前台窗口")
def _sample():
    s = KEEP
    s["sampler"]._adopt_today()
    for _ in range(3):
        s["sampler"]._on_tick()
    st = s["sampler"].stats
    assert (st.active_s + st.idle_s + st.locked_s) >= 1 or st.locked, (
        f"活跃/空闲/锁屏都没涨: active={st.active_s} idle={st.idle_s} locked={st.locked}"
    )
    s["sampler"].flush()
    row = s["storage"].daily(st.day)
    assert row is not None, "首轮 flush 没有落库"
    return (f"活跃 {st.active_s}s 空闲 {st.idle_s}s 锁屏 {st.locked_s}s "
            f"当前应用 {st.current_app or '未识别'}")


@check("看视频不算离开（被动消费判定）")
def _passive():
    from monitor import appclass, winapi

    s = KEEP
    sampler = s["sampler"]
    assert appclass.C_VIDEO in appclass.LEISURE_CATEGORIES
    assert appclass.C_GAME in appclass.LEISURE_CATEGORIES
    assert appclass.C_WORK not in appclass.LEISURE_CATEGORIES
    assert appclass.C_STUDY not in appclass.LEISURE_CATEGORIES

    hwnd = winapi.foreground_window()
    sampler._passive_hwnd = 0
    r = sampler._passive_consuming(hwnd)
    assert sampler._passive_hwnd == hwnd, "判定结果没有被缓存"
    assert sampler._passive_consuming(hwnd) == r
    return f"判据 = 分类∈{sorted(appclass.LEISURE_CATEGORIES)} 或 全屏；当前前台={r}"


@check("动画推进 260 帧")
def _animate():
    s = KEEP
    pet, anim = s["pet"], s["anim"]
    pet._on_frame()
    for name in ("hop", "pat_belly", "yawn", "sleep_loop", "celebrate", "sad",
                 "throw_ball", "catch_ball", "struggle", "spin"):
        anim.play(name, time.monotonic())
        for _ in range(26):
            pet._on_frame()
    anim.stop(time.monotonic())
    pet._on_frame()
    return "10 个动作各推进 26 帧无异常"


@check("状态机与免打扰仲裁")
def _statemachine():
    from pet.state_machine import (
        ACT, DRAG, GAME, IDLE, REMIND, R_BUSY, R_FULLSCREEN, SLEEP, StateMachine,
    )

    sm = StateMachine()
    assert sm.request(DRAG, source="t", force=True)
    # 低优先级不能打断高优先级
    assert not sm.request(ACT, source="t")
    assert sm.state == DRAG
    # 游戏期间来的提醒要排队
    assert sm.request(GAME, source="t", force=True)
    assert not sm.request(REMIND, source="t")
    assert sm.pop_pending() == (REMIND, "t")
    sm.back_to_idle()

    assert not sm.is_quiet
    sm.set_quiet(R_FULLSCREEN, True)
    assert sm.is_quiet and sm.is_proactive_blocked
    # 多个理由叠加：撤掉一个，只要还有别的理由就该继续安静
    sm.set_quiet(R_BUSY, True)
    sm.set_quiet(R_FULLSCREEN, False)
    assert sm.is_quiet, "还有别的安静理由时不该解除安静"
    sm.set_quiet(R_BUSY, False)
    assert not sm.is_quiet

    sm.request(SLEEP, source="t", force=True)
    assert sm.is_quiet and sm.is_proactive_blocked
    sm.back_to_idle()
    return "优先级 / 排队 / 多理由免打扰 均正确"


if GUI:
    @check("显示窗口 + 点击穿透自检")
    def _gui():
        from PySide6.QtCore import QPoint, QTimer

        from monitor import winapi

        s = KEEP
        pet = s["pet"]
        app = _app()
        pet.show()
        for _ in range(40):
            app.processEvents()
            time.sleep(0.02)

        out: dict = {}

        def run():
            try:
                pet._probe_click_through()
                for _ in range(30):
                    app.processEvents()
                    time.sleep(0.02)
                hwnd = int(pet.winId())
                # 系统发给窗口的 WM_NCHITTEST 用的是**物理**像素，
                # 而 mapToGlobal 给的是逻辑像素；dpr=1.25 时两者差 1.25 倍。
                # 这里统一从窗口的物理矩形出发，测的才是真实点击会走的那条路。
                rect = winapi.window_rect(hwnd)
                assert rect is not None, "取不到窗口矩形"
                left, top, _r, _b = rect
                dpr = pet.devicePixelRatioF() or 1.0
                from pet import rig
                from pet.pose import BODY_CX, BODY_CY

                out["mode"] = "exstyle" if pet._ex_style_mode else "nchittest"
                tx = left + int(5 * dpr)
                ty = top + int(5 * dpr)
                bx, by = rig.transform_point(pet._pose, BODY_CX, BODY_CY)
                cx = left + int(bx * pet.scale * dpr)
                cy = top + int(by * pet.scale * dpr)
                out["transparent_ht"] = winapi.send_nchittest(hwnd, tx, ty)
                out["client_ht"] = winapi.send_nchittest(hwnd, cx, cy)
                out["wf"] = winapi.window_from_point(tx, ty)
                out["hwnd"] = hwnd
            except Exception as exc:
                out["error"] = f"{type(exc).__name__}: {exc}"
            app.quit()

        QTimer.singleShot(200, run)
        app.exec()
        if "error" in out:
            raise RuntimeError(out["error"])

        mode = out["mode"]
        passed = out["wf"] != out["hwnd"]
        if mode == "exstyle":
            return f"降级方案生效（WS_EX_TRANSPARENT），轮廓外 HT={out['transparent_ht']}"
        assert out["transparent_ht"] == -1, (
            f"轮廓外 WM_NCHITTEST 返回 {out['transparent_ht']}，期望 -1")
        assert out["client_ht"] == 1, (
            f"轮廓内 WM_NCHITTEST 返回 {out['client_ht']}，期望 1")
        assert passed, f"WindowFromPoint 仍命中自身窗口 hwnd={out['hwnd']}"
        return "主方案生效：轮廓外 HTTRANSPARENT 且点击已交给下层窗口"


_APP = None


def _app():
    return _APP


def main() -> int:
    global _APP
    print("=" * 66)
    print("桌面企鹅 冒烟测试")
    print("=" * 66)

    if not check_imports():
        return report()

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    _APP = QApplication(sys.argv)
    _APP.setQuitOnLastWindowClosed(False)

    run_all()
    return report()


def report() -> int:
    print()
    ok = sum(1 for _n, good, _i in RESULTS if good)
    total = len(RESULTS)
    for name, good, info in RESULTS:
        mark = "  OK  " if good else "  !!  "
        print(f"{mark}{name:<32} {info}")
    print("-" * 66)
    print(f"结果：{ok}/{total} 通过")
    if ok != total:
        print("（失败项见上，加 -v 参数可看完整堆栈）")
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
