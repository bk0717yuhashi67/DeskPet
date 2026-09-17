"""功能测试：把需要交互的模块真正跑一遍。

    .venv\\Scripts\\python.exe tools\\interactive_test.py

覆盖：数据面板刷新、提醒设置增删改、设置面板构建、气泡按钮、
抛球小游戏完整一回合（发球 -> 接球 -> 计分）、状态机驱动的托盘状态。
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from game import physics  # noqa: E402
from game.throw_game import TOUCH_ARM_DELAY  # noqa: E402

from PySide6.QtCore import QPoint, QTime, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
QT_MESSAGES: list[str] = []


def install_qt_message_capture() -> None:
    """捕获 Qt 内部消息。

    重要：Qt 会吞掉 paintEvent 里抛出的 Python 异常（只在控制台打一行），
    测试不会失败。装了消息捕获之后，绘制层的 bug 才能被真正发现。
    """
    from PySide6.QtCore import qInstallMessageHandler

    def handler(mode, context, message):  # noqa: ANN001
        QT_MESSAGES.append(str(message))

    qInstallMessageHandler(handler)


def run(name: str, fn) -> None:
    t0 = time.time()
    try:
        info = fn()
        RESULTS.append((name, True, f"{info or ''} ({time.time() - t0:.2f}s)"))
    except Exception as exc:
        import traceback

        RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
        print(f"\n--- {name} 失败 ---")
        traceback.print_exc()


def pump(app, seconds: float, tick: float = 0.012) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(tick)


def main() -> int:
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    install_qt_message_capture()
    print("=" * 66)
    print("桌面企鹅 功能测试")
    print("=" * 66)

    tmp = Path(tempfile.mkdtemp())

    def _storage():
        from monitor.storage import Storage

        st = Storage(tmp / "it.db")
        day = "2026-09-17"
        st.bump_daily(day, active_s=12000, keystrokes=8000, backspaces=700,
                      clicks=500, mouse_dist_px=250000, app_switches=70,
                      longest_focus_s=2400, first_ts=1700000000, last_ts=1700043200)
        for exe, name, cat, secs in (
            ("code", "VS Code", "工作", 7200),
            ("chrome", "Chrome", "浏览器-未分类", 2400),
            ("wechat", "微信", "社交", 900),
            ("potplayer", "PotPlayer", "视频", 1500),
        ):
            st.bump_app(day, exe, name, cat, secs, 4)
        st.add_focus_block(day, 1700000000, 1700002400, "code", "VS Code", 2000)
        st.add_session(day, 1, "code", "VS Code", "工作", "main.py", 1700000000, 1700002400, 2400)
        return st

    storage = _storage()

    # ------------------------------------------------------------------ 面板
    def _stats_panel():
        from monitor.sampler import Sampler
        from monitor.hooks import InputHooks
        from pet.mood import MoodTracker
        from ui.dialogs.stats_panel import StatsPanel

        hooks = InputHooks()
        sampler = Sampler(storage, hooks)
        sampler._adopt_today()
        sampler.stats.active_s = 12000
        sampler.stats.keystrokes = 8000
        sampler.stats.backspaces = 700
        sampler.stats.clicks = 500
        sampler.stats.mouse_dist_px = 250000
        sampler.stats.app_switches = 70
        sampler.stats.longest_focus_s = 2400
        sampler.stats.first_ts = 1700000000
        sampler.stats.last_ts = 1700043200

        panel = StatsPanel(storage, sampler, MoodTracker())
        panel.resize(720, 720)
        panel.show()
        pump(app, 0.4)
        panel.refresh()
        pump(app, 0.4)

        shots = tmp / "panel.png"
        panel.grab().save(str(shots), "PNG")
        # 抽查几个控件是否真的被填了数据
        active_txt = panel.tiles["active"].value_text
        focus_txt = panel.tiles["focus"].value_text
        assert active_txt != "—", "活跃时长没有填充"
        assert focus_txt != "—", "专注度没有填充"
        assert panel.bar.items, "应用排行是空的"
        assert panel.legend.items, "分类图例是空的"
        assert sum(panel.heat.values) > 0, "作息热力条是空的"
        assert panel.spark.values, "7 日趋势是空的"
        assert "8000" in panel.io_rows["keys"].text(), panel.io_rows["keys"].text()
        panel.close()
        return f"活跃={active_txt} 专注={focus_txt} 应用 {len(panel.bar.items)} 项"

    def _reminder_dialog():
        from remind.store import ReminderStore
        from ui.dialogs.reminder_dialog import ReminderDialog

        store = ReminderStore(storage)
        store.ensure_builtins()
        dlg = ReminderDialog(store)
        dlg.show()
        pump(app, 0.3)

        assert dlg.list.count() == 3, f"内置提醒应显示 3 条，实际 {dlg.list.count()}"
        # 选中第一条并检查表单被填充
        dlg.list.setCurrentRow(0)
        pump(app, 0.1)
        assert dlg.time_edit.time().toString("HH:mm") == "12:00"
        assert "吃饭" in dlg.msg_edit.text()

        # 新增一条
        dlg._on_add()
        dlg.time_edit.setTime(QTime.fromString("08:30", "HH:mm"))
        dlg.msg_edit.setText("吃药")
        dlg._on_apply()
        pump(app, 0.1)
        assert dlg.list.count() == 4, f"新增后应有 4 条，实际 {dlg.list.count()}"

        # 测试信号
        got: list = []
        dlg.test_requested.connect(lambda r: got.append(r))
        dlg._on_test()
        pump(app, 0.1)
        assert got and got[0].message == "吃药", "测试提醒没有发出正确的提醒对象"

        # 删除刚加的
        dlg.list.setCurrentRow(dlg.list.count() - 1)
        pump(app, 0.1)
        dlg._on_delete()
        pump(app, 0.1)
        assert dlg.list.count() == 3, f"删除后应有 3 条，实际 {dlg.list.count()}"
        dlg.close()
        return f"列表 {dlg.list.count()} 条，增删改与测试提醒均正常"

    def _settings_dialog():
        from ui.dialogs.settings_dialog import SettingsDialog

        dlg = SettingsDialog()
        dlg.show()
        pump(app, 0.3)
        assert dlg.per_day.value() >= 1
        assert dlg.gap.value() >= 1
        assert dlg.click_mode.count() == 3
        assert dlg.scale_combo.count() == 3
        assert dlg.sleep_start.time().isValid()
        # 不要调用 _on_ok —— 那会真的写配置和注册表
        dlg.close()
        return (f"打扰上限 {dlg.per_day.value()} 条/天，"
                f"间隔 {dlg.gap.value()} 分，随机动作 {dlg.per_hour.value()} 次/时")

    def _consent_dialog():
        from ui.dialogs.consent_dialog import CHOICE_BASIC, CHOICE_FULL, ConsentDialog

        dlg = ConsentDialog()
        dlg.show()
        pump(app, 0.3)
        assert CHOICE_FULL != CHOICE_BASIC
        texts = [w.text() for w in dlg.findChildren(type(dlg.btn_full))]
        assert any("同意" in t for t in texts)
        dlg.close()
        return "同意弹窗可构建，两个选项齐全"

    def _bubble_buttons():
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent

        from ui.bubble import Bubble

        b = Bubble()
        fired: list[str] = []
        b.ack.connect(lambda: fired.append("ack"))
        b.snooze.connect(lambda: fired.append("snooze"))

        b.show_message("该吃饭啦～好好吃一顿，别对着屏幕扒饭哦", QPoint(900, 700), buttons=True)
        pump(app, 0.6)
        assert b.isVisible(), "气泡没有显示"
        assert b.height() > 50, f"带按钮的气泡太矮: {b.height()}"

        rects = b._btn_rects()
        assert "ack" in rects and "snooze" in rects, "按钮区域缺失"
        # 直接向 ack 按钮中心派发一次鼠标按下
        center = rects["ack"].center()
        ev = QMouseEvent(
            QEvent.MouseButtonPress,
            center,
            b.mapToGlobal(center.toPoint()),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        b.mousePressEvent(ev)
        pump(app, 0.2)
        assert fired == ["ack"], f"点击『知道啦』没有触发 ack: {fired}"
        pump(app, 1.0)
        b.close()
        return f"气泡尺寸 {b.width()}x{b.height()}，按钮回调正常"

    def _throw_game():
        from game.overlay import GameOverlay
        from game.throw_game import PHASE_FREE, ThrowGame
        from monitor.hooks import InputHooks

        hooks = InputHooks()
        overlay = GameOverlay()
        # 用一个真实合理的位置：贴着屏幕右下（也就是企鹅默认待的地方）
        av = QGuiApplication.primaryScreen().availableGeometry()
        pet_pos = {"x": float(av.right() - 120), "y": float(av.bottom() - 60)}

        game = ThrowGame(overlay, hooks, storage,
                         lambda: (pet_pos["x"], pet_pos["y"], 1.0))
        started = game.start()
        assert started, "游戏没能启动"
        assert game.bounds.left <= pet_pos["x"] <= game.bounds.right, (
            f"企鹅不在活动区横向范围内: {game.bounds} vs x={pet_pos['x']}")
        assert pet_pos["y"] + 60 <= game.bounds.floor + 1e-6, (
            f"企鹅在活动区地面之下，球永远飞不到: floor={game.bounds.floor}")

        clicks = 0
        armed_at = None
        for _ in range(900):        # 约 15 秒
            pump(app, 0.018)
            if game.phase == PHASE_FREE and game.ball.alive and game.active:
                # 一定要在出手**之前**取相位时间：_throw_from 会把 _phase_t 归零，
                # 出手后再读永远是 0（这个坑我先踩了一次）。
                t_before = game._phase_t
                # 模拟"把鼠标移到球上"：触碰判定必须在闸门打开后立刻成立
                if game.handle_move(game.ball.x, game.ball.y):
                    clicks += 1
                    if armed_at is None:
                        armed_at = t_before
            if not game.active:
                break

        result = game.stop(silent=True) or {}
        assert clicks > 0, "一次都没能碰到球（触碰判定或状态机有问题）"
        assert result.get("hits", 0) > 0, f"没有接到任何球: {result}"
        assert result.get("score", 0) > 0, f"得分为 0: {result}"
        assert armed_at is None or armed_at >= TOUCH_ARM_DELAY - 0.02, (
            f"发球闸门没生效：{armed_at:.2f}s 就出手了，应 >= {TOUCH_ARM_DELAY}")
        return (f"碰到球 {clicks} 次，接到 {result.get('hits')} 次，"
                f"得分 {result.get('score')}，最高连击 {result.get('best_combo')}")

    def _aim_avoids_mouse():
        """落点必须避开鼠标。

        触碰式玩法下，如果球被扔到鼠标底下，球一落地就被碰到、又飞回去，
        就变成永动的自动回合，玩家什么都不用做也能刷分。这是玩法成立的前提。
        """
        from PySide6.QtGui import QCursor
        from game.overlay import GameOverlay
        from game.throw_game import ThrowGame
        from monitor.hooks import InputHooks

        game = ThrowGame(GameOverlay(), InputHooks(), storage, lambda: (900.0, 700.0, 1.0))
        game.bounds = physics.Bounds(left=100.0, right=1500.0, floor=820.0, ceiling=120.0)

        pts = []
        for i in range(40):
            game._serves = i
            game.hits = i % 5
            game.misses = i % 3
            ax, ay = game._aim_point()
            assert game.bounds.left <= ax <= game.bounds.right, f"落点出界 x={ax}"
            assert game.bounds.ceiling <= ay <= game.bounds.floor, f"落点出界 y={ay}"
            pts.append((round(ax), round(ay)))

        distinct = len(set(pts))
        assert distinct >= 8, f"落点太集中，只有 {distinct} 个不同位置"

        # 鼠标就在场地正中：也不能掉在鼠标上
        # 注意 QCursor.pos() 返回 QPoint，PySide6 里不能直接解包成两个变量
        _c = QCursor.pos()
        mx, my = float(_c.x()), float(_c.y())
        game2 = ThrowGame(GameOverlay(), InputHooks(), storage, lambda: (900.0, 700.0, 1.0))
        game2.bounds = physics.Bounds(left=float(mx) - 400, right=float(mx) + 400,
                                      floor=float(my) + 200, ceiling=float(my) - 300)
        worst = 1e9
        for i in range(20):
            game2._serves = i
            ax, ay = game2._aim_point()
            worst = min(worst, math.hypot(ax - mx, ay - my))
            assert worst > 1.0, "落点正好压在鼠标上"
        return f"{distinct} 个不同落点；最贴近鼠标的一次仍隔 {worst:.0f}px"

    def _beach_ball():
        """沙滩皮球：多色分瓣 + 真的会自旋。"""
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QColor, QImage, QPainter
        from game.overlay import BEACH_PANELS, draw_beach_ball

        def render(rot: float, size: int = 96):
            img = QImage(size, size, QImage.Format_ARGB32)
            img.fill(QColor(0, 0, 0, 0))
            pp = QPainter(img)
            pp.setRenderHint(QPainter.Antialiasing, True)
            draw_beach_ball(pp, size / 2.0, size / 2.0, size * 0.42, rot)
            pp.end()
            return img

        img = render(0.0)
        cols = set()
        opaque = 0
        for y in range(0, img.height()):
            for x in range(0, img.width()):
                c = img.pixelColor(x, y)
                if c.alpha() < 200:
                    continue
                opaque += 1
                if c.lightness() < 235:
                    cols.add((c.red() // 32, c.green() // 32, c.blue() // 32))
        assert opaque > 2000, f"球几乎没画出来（不透明像素 {opaque}）"
        # 白 + 红 + 黄 + 蓝 至少各占一块
        assert len(cols) >= 3, f"颜色瓣太少，看不出是沙滩皮球: {sorted(cols)}"
        reds = [c for c in cols if c[0] - c[1] >= 2 and c[0] - c[2] >= 2]
        blues = [c for c in cols if c[2] - c[0] >= 2]
        assert reds, f"没有红瓣: {sorted(cols)}"
        assert blues, f"没有蓝瓣: {sorted(cols)}"

        # 自旋：转 30 度画出来的图案必须和 0 度不一样
        a = render(0.0)
        b = render(30.0)
        diff = sum(
            1
            for y in range(0, a.height(), 3)
            for x in range(0, a.width(), 3)
            if a.pixelColor(x, y) != b.pixelColor(x, y)
        )
        assert diff > 20, f"球旋转后画面几乎没变（差异点 {diff}）"

        # 自旋角度由物理层推进，且和横向速度同向
        ball = physics.Ball(x=300.0, y=300.0, r=24.0, alive=True)
        ball.vx, ball.vy = 500.0, 0.0
        bounds = physics.Bounds(0.0, 2000.0, 800.0, 50.0)
        for _ in range(30):
            physics.step(ball, physics.FIXED_DT, bounds)
        assert ball.rot > 0.0, "球在飞却不自旋"
        return f"{len(cols)} 组色瓣，转动 30° 有 {diff} 个采样点变化，自旋 {ball.rot:.0f}°"

    def _penguin_look():
        """把"头全黑 / 单眼白黑点 / 单层宽喙 / 矮圆"这几条要求钉成断言。

        这几条都是主观的外观要求，很容易在后续改动里悄悄回退，
        所以直接对着渲染像素做检查。
        """
        from PySide6.QtGui import QColor
        from pet import rig
        from pet.pose import (
            EYE_CY,
            EYE_DX,
            EYE_RX,
            EYE_RY,
            HEAD_CY,
            OFF_X,
            OFF_Y,
            Pose,
        )

        N = 480
        K = N / 240.0

        def px(design_x: float, design_y: float) -> tuple[int, int]:
            return (int((design_x + OFF_X) * K), int((design_y + OFF_Y) * K))

        def render(pose):
            return rig.render_to_pixmap(pose, size=N).toImage()

        def count(img, x0, y0, x1, y1, pred):
            n = 0
            for y in range(y0, y1):
                for x in range(x0, x1):
                    if pred(img.pixelColor(x, y)):
                        n += 1
            return n

        def is_dark(c):
            return c.alpha() > 200 and (c.red() + c.green() + c.blue()) < 300

        def is_white(c):
            return c.alpha() > 200 and c.red() > 225 and c.green() > 225 and c.blue() > 225

        def is_orange(c):
            return (c.alpha() > 200 and c.red() > 190
                    and 110 < c.green() < 215 and c.blue() < 130)

        base = Pose()

        # ---- 1. 头全黑：眼睛上方那一整片必须是黑的，不能有白脸 ----
        img = render(base)
        eye_top = EYE_CY - EYE_RY
        x0, y0 = px(44.0, 40.0)
        x1, y1 = px(156.0, min(eye_top - 1.5, HEAD_CY))
        opaque = 0
        light = []
        for y in range(y0, y1):
            for x in range(x0, x1):
                c = img.pixelColor(x, y)
                if c.alpha() > 200:
                    opaque += 1
                    if c.red() + c.green() + c.blue() > 300:
                        light.append((x, y, c.name()))
        assert opaque > 1500, f"额头区域几乎没内容（不透明像素 {opaque}）"
        assert not light, f"额头有浅色像素，头不是全黑的！例如 {light[:3]}"

        # ---- 2. 眼睛：白眼底 + 黑点 ----
        e0 = px(100.0 - EYE_DX - EYE_RX, EYE_CY - EYE_RY)
        e1 = px(100.0 + EYE_DX + EYE_RX, EYE_CY + EYE_RY)
        eyes_open = (count(img, e0[0], e0[1], e1[0], e1[1], is_white),
                     count(img, e0[0], e0[1], e1[0], e1[1], is_dark))
        assert eyes_open[0] > 400, f"看不见白色眼白（白像素 {eyes_open[0]}）"
        assert eyes_open[1] > 60, f"看不见黑色瞳孔（黑像素 {eyes_open[1]}）"

        # 闭眼：黑点变成弯钩 —— 黑像素还在（弯钩），但眼白被压扁了
        closed = render(Pose(eye_open=0.0))
        eyes_shut = (count(closed, e0[0], e0[1], e1[0], e1[1], is_white),
                     count(closed, e0[0], e0[1], e1[0], e1[1], is_dark))
        assert eyes_shut[0] < eyes_open[0], "闭眼后眼白没有变扁"
        assert eyes_shut[1] > 40, f"闭眼后看不见弯钩（黑像素 {eyes_shut[1]}）"

        # ---- 3. 喙：单层、宽 ----
        # 采样框从 rig 的常量算，别写死像素坐标——写死过一次，
        # 后来调整喙的位置，测试就假失败了（"喙太小：橙色像素 24"）。
        from pet import rig as _rig

        b0 = px(100.0 - _rig.BEAK_W / 2.0 - 3.0, _rig.BEAK_TOP - 3.0)
        b1 = px(100.0 + _rig.BEAK_W / 2.0 + 3.0,
                _rig.BEAK_TOP + _rig.BEAK_H + 12.0)
        beak_closed = count(img, b0[0], b0[1], b1[0], b1[1], is_orange)
        assert beak_closed > 250, f"喙太小（橙色像素 {beak_closed}）"

        # 张开时只能是同一个三角形被拉长。两条判据：
        # 1) 橙色区域必须**连通成一块**——旧版上下两层喙各画一个三角形，
        #    中间会露出暗红口腔，橙色会裂成两块；
        # 2) 橙色面积必须随张嘴变大。
        def orange_blobs(im):
            mask = {}
            for y in range(b0[1], b1[1]):
                for x in range(b0[0], b1[0]):
                    if is_orange(im.pixelColor(x, y)):
                        mask[(x, y)] = False
            blobs, biggest = 0, 0
            for seed in list(mask):
                if mask[seed]:
                    continue
                stack = [seed]
                mask[seed] = True
                size = 0
                while stack:
                    cx, cy = stack.pop()
                    size += 1
                    for nb in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                        if nb in mask and not mask[nb]:
                            mask[nb] = True
                            stack.append(nb)
                if size >= 40:      # 忽略抗锯齿碎点
                    blobs += 1
                    biggest = max(biggest, size)
            return blobs, biggest

        blobs_closed, area_closed = orange_blobs(img)
        assert blobs_closed == 1, f"闭嘴时喙不是一块（连通块数 {blobs_closed}）"

        wide = render(Pose(beak_open=1.0))
        blobs_open, area_open = orange_blobs(wide)
        assert blobs_open == 1, (
            f"张嘴后喙裂成了 {blobs_open} 块——喙又变回两层了（单层喙不可能裂开）")
        beak_open_n = count(wide, b0[0], b0[1], b1[0], b1[1], is_orange)
        assert beak_open_n > beak_closed, "张嘴后喙没有变化"

        # ---- 4. 矮圆 ----
        w = img.width()
        h = img.height()
        xs, ys = [], []
        for y in range(h):
            for x in range(w):
                if img.pixelColor(x, y).alpha() > 150:
                    xs.append(x)
                    ys.append(y)
        bw = max(xs) - min(xs) + 1
        bh = max(ys) - min(ys) + 1
        ratio = bh / bw
        assert ratio <= 1.45, f"企鹅还不够矮圆：高/宽 = {ratio:.2f}（要求 <= 1.45）"

        return (f"额头 {opaque} 点全黑；眼白 {eyes_open[0]} / 黑点 {eyes_open[1]}；"
                f"喙 {beak_closed}->{beak_open_n} 橙色像素且无第二层；高宽比 {ratio:.2f}")

    def _tray_menu_clean():
        """菜单瘦身：安静模式下线，三项收进设置窗口。"""
        from ui.tray import Tray

        t = Tray()
        texts = []
        for a in t.menu.actions():
            if a.isSeparator():
                continue
            texts.append(a.text())
            if a.menu():
                texts.extend(x.text() for x in a.menu().actions())
        joined = " | ".join(texts)

        gone = ["安静模式", "提醒设置", "暂停统计", "清除今日数据"]
        for g in gone:
            assert g not in joined, f"菜单里还有「{g}」：{joined}"
        for keep in ["开始抛球小游戏", "设置…", "数据面板…", "立即做点什么", "创建桌面快捷方式",
                     "关于", "退出", "让它睡觉", "番茄钟", "小一点", "标准", "大一点"]:
            assert keep in joined, f"菜单里少了「{keep}」：{joined}"
        t.hide()
        return f"{len(texts)} 个菜单项；已下线 {len(gone)} 项"

    def _settings_absorbed():
        """设置窗口必须接手提醒设置 / 暂停统计 / 清除今日数据。"""
        from ui.dialogs.settings_dialog import SettingsDialog

        dlg = SettingsDialog()
        assert hasattr(dlg, "btn_reminders"), "设置里没有提醒设置入口"
        assert hasattr(dlg, "pause_stats_box"), "设置里没有暂停统计"
        assert dlg.pause_stats_box.isChecked() is False, "暂停统计默认应该是关闭的"
        dlg.set_paused(True)
        assert dlg.pause_stats_box.isChecked(), "set_paused 没生效"

        fired = {"reminders": 0, "pause": []}
        dlg.open_reminders.connect(lambda: fired.__setitem__("reminders", fired["reminders"] + 1))
        dlg.pause_stats.connect(lambda v: fired["pause"].append(v))
        dlg.btn_reminders.click()
        dlg.pause_stats_box.setChecked(False)
        dlg._on_ok()
        assert fired["reminders"] == 1, "点提醒设置没发信号"
        assert fired["pause"] and fired["pause"][-1] is False, f"暂停统计没转发: {fired['pause']}"
        # 隐私分组里必须有"清除今日数据"
        labels = [w.text() for w in dlg.findChildren(type(dlg.btn_reminders))]
        assert any("清除今日数据" in x for x in labels), f"没有清除今日数据按钮: {labels}"
        return f"提醒入口 + 暂停统计 + 清除今日数据 都在；按钮 {len(labels)} 个"

    def _right_click_menu():
        """右键企鹅 -> 弹出与托盘同一份菜单。"""
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from ui.tray import Tray
        from ui.pet_window import PetWindow

        from pet.animator import Animator
        from pet.state_machine import StateMachine

        tray = Tray()
        pet = PetWindow(Animator(), StateMachine())
        fired = {"n": 0}
        pet.menu_requested.connect(lambda: fired.__setitem__("n", fired["n"] + 1))

        # 直接构造右键"抬起"事件喂给处理函数（真实鼠标链路由 tools/check_mouse.py 验证）
        ev = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, QPointF(50.0, 50.0),
                         QPointF(400.0, 400.0), Qt.RightButton, Qt.NoButton, Qt.NoModifier)
        pet.mouseReleaseEvent(ev)
        assert fired["n"] == 1, f"右键没触发菜单请求: {fired}"
        # 去重：紧接着再来一次不应该重复触发
        pet.mouseReleaseEvent(ev)
        assert fired["n"] == 1, "右键菜单没有去重（会弹两遍）"

        # 菜单本身是同一份实例
        tray.popup_at(QPoint(300, 300))
        app.processEvents()
        assert tray.menu.isVisible(), "popup_at 没能把菜单弹出来"
        tray.menu.close()
        pet.hide()
        tray.hide()
        return "右键触发 + 去重 + 复用同一份菜单，全部正常"

    def _mood_voice():
        """指标 -> 发言/动作：每种心情必须给出**不同**的台词场景和不同动作。

        这是"数据算了半天却一句话不说"的直接防线：以前 mood 只影响动作频率，
        台词库里按指标写好的那些场景从来没被念出来过。
        """
        import time as _t

        from app.config import config as _cfg
        from care.budget import BubbleBudget
        from care.features import MOOD_VOICE, CareManager
        from monitor import analyzer
        from pet.mood import Mood, MoodTracker
        from pet.state_machine import StateMachine

        _cfg.set("bubble_per_day", 40)
        _cfg.set("bubble_min_gap_min", 1)
        from monitor.hooks import InputHooks
        from monitor.sampler import Sampler

        sampler = Sampler(storage, InputHooks())
        sm = StateMachine()
        care = CareManager(sampler, storage, sm, BubbleBudget(), MoodTracker())
        care._metrics = analyzer.compute(
            day="2025-01-01", counts={}, key_times=[], focus_count=0, continuous_s=0,
        )

        spoken: dict[str, tuple[str, str]] = {}

        class St:
            idle_now = 0.0
            active_s = 4000
            continuous_active_s = 2000
            in_idle = False
            locked = False

        base = _t.time()
        for i, mood in enumerate(MOOD_VOICE):
            got = {}
            care.speak.connect(lambda text, urgent=False, g=got: g.setdefault("text", text))
            care.pet_clip.connect(lambda clip, g=got: g.setdefault("clip", clip))
            care.mood.force(mood)
            # 每次往后挪一小时，绕开"同心情 25 分钟不重复"和日预算
            care._check_mood_voice(base + i * 3600.0, St())
            assert "text" in got, f"{mood.value} 没有发言"
            assert "clip" in got, f"{mood.value} 没有配套动作"
            spoken[mood.value] = (got["text"], got["clip"])
            # 断开这一轮的回调，避免下一轮重复收集
            care.speak.disconnect()
            care.pet_clip.disconnect()

        scenes = {m: MOOD_VOICE[m][0] for m in MOOD_VOICE}
        clips = [c for _t2, c in spoken.values()]
        assert len(set(scenes.values())) == len(scenes), f"有心情共用了同一个场景: {scenes}"
        assert len(set(clips)) == len(clips), f"有心情共用了同一个动作: {clips}"
        assert len(set(t for t, _c in spoken.values())) >= len(spoken) - 1, (
            f"台词重复度过高: {spoken}")
        # 台词必须真的取自对应场景，而不是随便一条
        from care import lines

        for mood, (scene, _clip) in MOOD_VOICE.items():
            text = spoken[mood.value][0]
            assert text in lines.SCENES[scene], (
                f"{mood.value} 的台词不属于场景 {scene}: {text}")
        return (f"{len(spoken)} 种心情各有专属台词与动作："
                + "、".join(f"{k}->{v[1]}" for k, v in spoken.items()))

    def _sampler_flags():
        from monitor.sampler import Sampler
        from monitor.hooks import InputHooks
        from monitor import appclass, winapi

        hooks = InputHooks()
        s = Sampler(storage, hooks)
        hwnd = winapi.foreground_window()
        flags: list = []
        s.state_flags.connect(lambda *a: flags.append(a))
        s._adopt_today()
        for _ in range(3):
            s._on_tick()
        # 直接测全屏与会议的判定本身
        assert winapi.is_fullscreen(0) is False
        assert appclass.is_meeting("wemeetapp", "") is True
        assert s._passive_consuming(hwnd) in (True, False)
        return f"轮询 3 次正常，当前应用 {s.stats.current_app or '未识别'}"

    def _tray_icon():
        from ui.tray import make_icon

        sizes = {}
        for kind in ("normal", "sleep", "game", "quiet", "paused"):
            ic = make_icon(kind, 64)
            assert not ic.isNull(), kind
            pm = ic.pixmap(64, 64)
            assert not pm.isNull() and pm.width() == 64, kind
            sizes[kind] = pm.toImage().pixelColor(32, 32).name()
        return f"5 种状态图标均可生成（中心色 {sizes['normal']}）"

    def _tray_actions():
        """托盘里每个可勾选项都要能真的触发。

        这里曾经有个隐蔽 bug：triggered.connect(sig.emit) 会走 PySide6 的
        无参重载，emit() 少一个 bool 直接抛 TypeError，
        表现就是"点了菜单没反应"，只在日志里留一行异常。
        """
        from ui.tray import Tray

        tray = Tray()
        got: dict = {}
        tray.toggle_sleep.connect(lambda v: got.__setitem__("sleep", v))
        tray.toggle_pomodoro.connect(lambda v: got.__setitem__("pomodoro", v))
        tray.manual_action.connect(lambda n: got.__setitem__("manual", n))
        tray.set_scale.connect(lambda s: got.__setitem__("scale", s))

        for key in ("sleep", "pomodoro"):
            act = tray._acts[key]
            act.setChecked(True)
            act.trigger()
            assert got.get(key) is not None, f"{key} 没有转发任何值（信号没接上）"
            assert got[key] == act.isChecked(), (
                f"{key} 转发值 {got[key]} 与菜单勾选状态 {act.isChecked()} 不一致")
            act.setChecked(False)
            act.trigger()
            assert got[key] == act.isChecked(), f"{key} 取消勾选后转发值不对"

        scale_act = None
        for a in tray.menu.actions():
            if a.text() == "大一点":
                scale_act = a
                break
        assert scale_act is not None, "找不到缩放入口"
        scale_act.trigger()
        assert got.get("scale") == 1.35, f"缩放没有正确转发: {got.get('scale')}"

        sub = None
        for a in tray.menu.actions():
            if a.menu() is not None and "立即" in a.text():
                sub = a.menu()
                break
        assert sub is not None, "找不到「立即做点什么」子菜单"
        acts = sub.actions()
        assert acts, "子菜单是空的"
        acts[0].trigger()
        assert got.get("manual"), "手动动作没有转发"

        tray.set_icon("sleep")
        tray.set_status_line("测试")
        tray.set_game_running(True, 120)
        assert "120" in tray._acts["game"].text()
        tray.set_game_running(False)
        tray.hide()
        return f"{len(acts)} 个手动动作 + 4 个勾选项 + 缩放 全部转发正常"

    def _shortcut():
        """快捷方式必须真的可用：目标、参数、工作目录、图标、描述一个都不能错。

        这里踩过一个坑：IShellLinkW 的虚表不是按字母序排的，
        下标错位时调用"成功"但写出来的 .lnk 内容全乱（SetPath 打到 SetIconLocation 上）。
        所以必须双向校验：扫字节 + COM 读回。
        """
        from app import shortcut as S

        lnk = S.create_project_shortcut()
        assert lnk and lnk.exists(), "快捷方式没有创建成功"

        res = S.inspect_shortcut(lnk, S.expected_content())
        missing = [k for k, v in res.items() if not v]
        assert not missing, f"这些字段没写进 .lnk: {missing}"

        info = S.read_shortcut(lnk)
        assert info, "COM 读回失败"
        assert info["target"].lower().endswith("pythonw.exe"), f"目标不对: {info['target']}"
        assert "launch.py" in info["arguments"], f"参数不对: {info['arguments']}"
        assert info["working_dir"].lower() == str(S.ROOT).lower(), info["working_dir"]
        assert info["icon"].lower().endswith("penguin.ico"), info["icon"]
        assert info["description"] == "桌面企鹅", info["description"]
        return f"{lnk.name} 双向校验通过（目标/参数/工作目录/图标/描述）"

    def _launcher_failure_report():
        """启动器必须能诊断「启动即崩」，而不是静默什么都不发生。"""
        import importlib
        import tempfile as _tf

        lp = importlib.import_module("launch")
        assert lp.deps_ok(), "依赖自检失败"

        broken = Path(_tf.mkdtemp()) / "boom.py"
        broken.write_text(
            "import sys\n"
            "sys.stderr.write('BOOM: deliberate startup failure\\n')\n"
            "sys.exit(3)\n",
            encoding="utf-8",
        )
        orig_main = lp.MAIN
        orig_running = lp.already_running
        lp.MAIN = broken
        lp.already_running = lambda: False
        try:
            ok, detail = lp.launch()
        finally:
            lp.MAIN = orig_main
            lp.already_running = orig_running
        assert not ok, "故意崩溃的脚本竟然被判成启动成功"
        assert "BOOM" in detail, f"没有把真实错误带出来: {detail}"
        return "启动即崩能被捕获并带出 stderr 原文"

    run("数据面板（刷新 + 图表填充）", _stats_panel)
    run("托盘菜单已瘦身（删安静模式 + 三项下移）", _tray_menu_clean)
    run("设置窗口接手提醒/暂停统计/清除数据", _settings_absorbed)
    run("右键企鹅弹出同一份菜单", _right_click_menu)
    run("指标驱动发言与动作的区分度", _mood_voice)
    run("提醒设置（增删改 + 测试提醒）", _reminder_dialog)
    run("设置面板构建", _settings_dialog)
    run("首次同意弹窗", _consent_dialog)
    run("气泡按钮回调", _bubble_buttons)
    run("抛球小游戏完整回合（触碰出手）", _throw_game)
    run("落点避开鼠标（不白送分）", _aim_avoids_mouse)
    run("沙滩皮球绘制与自旋", _beach_ball)
    run("新造型：头全黑 / 单层宽喙 / 白眼底黑点", _penguin_look)
    run("采样轮询与全屏/会议判定", _sampler_flags)
    run("托盘图标 5 种状态", _tray_icon)
    run("托盘菜单动作全部能触发", _tray_actions)
    run("快捷方式内容正确（双向校验）", _shortcut)
    run("启动器能诊断启动失败", _launcher_failure_report)

    # 绘制层异常会被 Qt 吞掉，这里把它揪出来
    noisy = [
        m for m in QT_MESSAGES
        if "Traceback" in m or "paintEvent" in m or "Error" in m or "error" in m
    ]
    if noisy:
        RESULTS.append(("Qt 绘制层无异常", False, noisy[0].strip().splitlines()[-1][:120]))
    else:
        RESULTS.append(("Qt 绘制层无异常", True, f"捕获 {len(QT_MESSAGES)} 条 Qt 消息，无错误"))

    print()
    ok = sum(1 for _n, g, _i in RESULTS if g)
    for name, good, info in RESULTS:
        print(f"{'  OK  ' if good else '  !!  '}{name:<30} {info}")
    print("-" * 66)
    print(f"结果：{ok}/{len(RESULTS)} 通过")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
