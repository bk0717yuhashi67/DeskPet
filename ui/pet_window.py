"""宠物主窗口：透明、置顶、无边框、不抢焦点、轮廓外点击穿透。

点击穿透有两条路线：
  主方案  nativeEvent 拦 WM_NCHITTEST，轮廓外返回 HTTRANSPARENT(-1)，
          系统会继续向 Z 序下方的窗口做命中测试，点击自然落到桌面。
  降级   启动自检未通过时，改用动态 WS_EX_TRANSPARENT 开关（ctypes 直改扩展样式，
          不经 Qt 的 setWindowFlag，避免窗口重建闪烁），由光标位置驱动。
无论哪条路线，鼠标按下期间都强制保持可命中，否则拖动中移出轮廓会丢事件。
"""
from __future__ import annotations

import ctypes
import time

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from app import logging_setup
from app.config import config
from monitor import winapi
from pet import rig
from pet.animator import Animator
from pet.pose import BODY_CX, BODY_CY, HEAD_PIVOT_Y, WIN_H, WIN_W, Pose
from pet.state_machine import DRAG, GAME, SLEEP, StateMachine

log = logging_setup.get("pet_window")

FRAME_MS = 16
IDLE_FRAME_MS = 40
SLEEP_FRAME_MS = 100
EX_POLL_MS = 33
EDGE_MARGIN = 24
CLICK_MOVE_TOL = 4
POSE_EPS = 0.002

_POSE_KEYS = (
    "body_x", "body_y", "body_sx", "body_sy", "body_rot",
    "head_x", "head_y", "head_rot", "wing_l", "wing_r",
    "eye_open", "eye_squint", "pupil_x", "pupil_y", "beak_open",
    "foot_l_y", "foot_r_y", "scarf_wave", "scarf_on", "blush", "alpha",
)


class PetWindow(QWidget):
    clicked = Signal()
    drag_started = Signal()
    drag_finished = Signal(QPoint)
    drag_blocked_by_game = Signal()

    session_locked = Signal()
    session_unlocked = Signal()
    power_suspend = Signal()
    power_resume = Signal()
    screen_geometry_changed = Signal()
    menu_requested = Signal()          # 右键点企鹅：请主程序弹菜单

    def __init__(self, animator: Animator, sm: StateMachine, parent=None) -> None:
        super().__init__(
            parent,
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setMouseTracking(True)
        self.setWindowTitle("DeskPet")

        self.animator = animator
        self.sm = sm
        self._menu_emitted_at = 0.0
        self._scale = max(0.5, float(config.get("pet_scale", 1.0) or 1.0))
        self.resize(int(WIN_W * self._scale), int(WIN_H * self._scale))

        self._pose: Pose = Pose()
        self._painted: Pose | None = None
        self._dragging = False
        self._pressed = False
        self._press_global = QPoint()
        self._drag_offset = QPoint()
        self._moved = 0
        self._press_t = 0.0
        self._drag_lock = False
        self._fade = 0.0
        self._frame_count = 0

        self._hwnd = 0
        self._ex_style_mode = False
        self._ex_transparent: bool | None = None
        self._probed = False
        self._positioned = False
        self._hidden_for_fullscreen = False

        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._on_frame)
        self._timer.start()

        self._ex_timer = QTimer(self)
        self._ex_timer.setInterval(EX_POLL_MS)
        self._ex_timer.timeout.connect(self._poll_cursor_click_through)

    # ==================================================================
    # 生命周期
    # ==================================================================
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._hwnd = int(self.winId())
        if not self._probed:
            self._probed = True
            QTimer.singleShot(250, self._probe_click_through)
        if not self._positioned:
            self._positioned = True
            self._restore_position()

    def closeEvent(self, event) -> None:
        self._timer.stop()
        self._ex_timer.stop()
        if self._hwnd:
            winapi.unregister_session_notification(self._hwnd)
        super().closeEvent(event)

    def _restore_position(self) -> None:
        pos = config.get("pos")
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            self.move(int(pos[0]), int(pos[1]))
            return
        # 默认停在主屏右下角，避开任务栏
        scr = QGuiApplication.primaryScreen()
        if scr is None:
            return
        av = scr.availableGeometry()
        self.move(
            av.right() - self.width() - 60,
            av.bottom() - self.height() + 6,
        )

    # ==================================================================
    # 点击穿透
    # ==================================================================
    def _probe_click_through(self, attempt: int = 0) -> None:
        mode = str(config.get("click_through_mode", "auto") or "auto")
        if mode == "exstyle":
            self._enable_ex_style_mode("配置指定")
            return
        ok, detail = self._selftest_click_through()
        if mode == "nchittest":
            log.info("按配置强制使用 WM_NCHITTEST 穿透（自检 %s: %s）", ok, detail)
            return
        if ok:
            log.info("点击穿透自检通过：%s", detail)
            winapi.register_session_notification(self._hwnd)
            return
        if attempt < 2:
            # 自检可能撞上窗口还没映射好、或恰好有瞬时状态，先重试几次。
            # 一次没测准就永久降级的话，整个会话都会用兜底方案（整窗不响应点击）。
            log.debug("点击穿透自检第 %d 次未通过（%s），稍后重试", attempt + 1, detail)
            QTimer.singleShot(500, lambda: self._probe_click_through(attempt + 1))
            return
        self._enable_ex_style_mode(detail)

    def _selftest_click_through(self) -> tuple[bool, str]:
        """发两条 WM_NCHITTEST 验证"轮廓外穿透、轮廓内可点"，再用 WindowFromPoint 交叉验证。"""
        if not self._hwnd:
            return False, "窗口句柄未就绪"
        # 必须用**物理**坐标发这条消息：系统发给窗口的 WM_NCHITTEST 就是物理坐标，
        # 用逻辑坐标自检只能测出"两边错得一样"，测不出真实点击能不能通。
        rect = winapi.window_rect(self._hwnd)
        if rect is None:
            return False, "取不到窗口矩形"
        left, top, _r, _b = rect
        dpr = self.devicePixelRatioF() or 1.0
        s = self._scale

        # 透明点：窗口左上角内 5px（逻辑）-> 物理，必定在企鹅轮廓之外
        tx = left + int(5 * dpr)
        ty = top + int(5 * dpr)
        r_trans = winapi.send_nchittest(self._hwnd, tx, ty)

        # 实体点：企鹅身体中心
        bx, by = rig.transform_point(self._pose, BODY_CX, BODY_CY)
        cx = left + int(bx * s * dpr)
        cy = top + int(by * s * dpr)
        r_client = winapi.send_nchittest(self._hwnd, cx, cy)

        wf = winapi.window_from_point(tx, ty)
        below_ok = wf != self._hwnd

        detail = (
            f"轮廓外 HT={r_trans}(期望 -1) 轮廓内 HT={r_client}(期望 1) "
            f"WindowFromPoint={'其他窗口' if below_ok else '本窗口'}"
        )
        if r_trans != winapi.HTTRANSPARENT:
            return False, "轮廓外未返回 HTTRANSPARENT，" + detail
        if r_client != winapi.HTCLIENT:
            return False, "轮廓内未返回 HTCLIENT，" + detail
        if not below_ok:
            # 命中测试通了但系统没把点击交给下层 —— 用最保守的方案兜底
            return False, "HTTRANSPARENT 未被系统采纳，" + detail
        return True, detail

    def _enable_ex_style_mode(self, reason: str) -> None:
        self._ex_style_mode = True
        log.warning("启用点击穿透降级方案（动态 WS_EX_TRANSPARENT）：%s", reason)
        self._ex_transparent = None
        self._ex_timer.start()
        winapi.register_session_notification(self._hwnd)

    def _poll_cursor_click_through(self) -> None:
        """降级方案：光标在企鹅身上就恢复可点击，离开就整窗穿透。"""
        if not self._hwnd:
            return
        if self._drag_lock or self._dragging:
            want = False
        else:
            # 游戏中也不整窗可点：玩法已经不吃点击了，没理由挡住下面的桌面
            x, y = winapi.cursor_pos()          # 物理像素
            want = not self._accepts_physical(x, y)
        if want != self._ex_transparent:
            winapi.set_ex_transparent(self._hwnd, want)
            self._ex_transparent = want

    # ------------------------------------------------------------------
    def nativeEvent(self, event_type, message):  # noqa: N802
        et = event_type
        if isinstance(et, (bytes, bytearray)):
            is_generic = bytes(et) == b"windows_generic_MSG"
        else:
            try:
                is_generic = bytes(et) == b"windows_generic_MSG"
            except Exception:
                is_generic = False
        if not is_generic:
            return super().nativeEvent(event_type, message)

        msg = winapi.read_msg(message)
        if msg is None:
            return super().nativeEvent(event_type, message)

        try:
            m = int(msg.message)
            if m == winapi.WM_NCHITTEST:
                return self._handle_nchittest(msg)
            if m == winapi.WM_MOUSEACTIVATE:
                # 绝不因为被点一下就抢走用户的输入焦点
                return True, winapi.MA_NOACTIVATE
            if m == winapi.WM_WTSSESSION_CHANGE:
                w = int(msg.wParam)
                if w == winapi.WTS_SESSION_LOCK:
                    self.session_locked.emit()
                elif w == winapi.WTS_SESSION_UNLOCK:
                    self.session_unlocked.emit()
                return super().nativeEvent(event_type, message)
            if m == winapi.WM_POWERBROADCAST:
                w = int(msg.wParam)
                if w == winapi.PBT_APMSUSPEND:
                    self.power_suspend.emit()
                elif w in (winapi.PBT_APMRESUMEAUTOMATIC, winapi.PBT_APMRESUMESUSPEND):
                    self.power_resume.emit()
                return super().nativeEvent(event_type, message)
        except Exception as exc:
            log.debug("nativeEvent 处理异常: %s", exc)

        return super().nativeEvent(event_type, message)

    def _handle_nchittest(self, msg) -> tuple[bool, int]:
        if self._drag_lock or self._ex_style_mode:
            # 拖动期间必须持续可命中；降级模式下命中由扩展样式控制
            return True, winapi.HTCLIENT
        # 这里曾经有一条"游戏中整窗可点"的特例（当年玩法要点球，怕丢点击）。
        # 现在玩法是"鼠标碰到球就算"，再也不需要吃点击，那条特例只剩副作用：
        #  1) 游戏期间企鹅窗口会吃掉整块区域的桌面点击；
        #  2) 更要命的是启动自检如果撞上游戏状态，两条 WM_NCHITTEST 都会返回
        #     HTCLIENT，自检会误判成"主方案失效"并永久降级。已删除。

        lp = int(msg.lParam)
        # lParam 是"有符号"的屏幕坐标：副屏在主屏左侧时 x 为负，
        # 必须按 16 位有符号解释，这是最常见的坐标错乱来源。
        x = ctypes.c_short(lp & 0xFFFF).value
        y = ctypes.c_short((lp >> 16) & 0xFFFF).value
        # 这里的 x/y 是**物理**像素，必须交给物理坐标版判定（见 _accepts_physical）
        if self._accepts_physical(x, y):
            return True, winapi.HTCLIENT
        return True, winapi.HTTRANSPARENT

    # ==================================================================
    # 命中判定
    # ==================================================================
    def hit_margin(self) -> float:
        return 12.0 if self.sm.is_state(GAME) else 6.0

    def _accepts_physical(self, px: int, py: int) -> bool:
        """屏幕**物理像素**坐标是否落在企鹅身上。

        为什么必须收物理坐标：`WM_NCHITTEST` 的 lParam 和 `GetCursorPos` 给的
        都是物理像素，而 Qt 的 `mapFromGlobal` / 控件几何是逻辑像素。
        这台机器 dpr=1.25，用逻辑坐标去 mapFromGlobal 会算出偏小 20% 的局部坐标，
        于是**轮廓内的点被算到轮廓外**，窗口自己回报 HTTRANSPARENT ——
        结果是企鹅既点不动也拖不动，而且不报任何错。

        这里改用窗口的物理矩形做减法，两端都在物理空间里，再按 dpr 折回逻辑像素，
        不依赖任何"坐标到底是哪种"的假设。

        （注意：启动自检原来也传逻辑坐标，两边错得一致，所以自检显示"通过"，
        真实点击却完全不工作。自检现在也改成传物理坐标了。）
        """
        rect = winapi.window_rect(self._hwnd)
        if rect is None:
            return False
        left, top, _right, _bottom = rect
        dpr = self.devicePixelRatioF() or 1.0
        s = self._scale or 1.0
        return rig.hit_test(
            self._pose,
            (px - left) / dpr / s,
            (py - top) / dpr / s,
            self.hit_margin(),
        )

    def _accepts_global(self, x: int, y: int) -> bool:
        """逻辑坐标版本（供 Qt 内部使用）。外部系统输入请用 _accepts_physical。"""
        local = self.mapFromGlobal(QPoint(int(x), int(y)))
        s = self._scale
        return rig.hit_test(
            self._pose, local.x() / s, local.y() / s, self.hit_margin()
        )

    # ==================================================================
    # 绘制 / 帧循环
    # ==================================================================
    def _on_frame(self) -> None:
        now = time.monotonic()
        self._update_gaze()
        pose = self.animator.tick(now)
        if self._fade < 1.0:
            self._fade = min(1.0, self._fade + 0.055)
            pose.alpha *= self._fade
        self._pose = pose

        if self._pose_differs(pose):
            self.update()
            self._painted = pose.copy()

        self._frame_count += 1
        if self._frame_count % 45 == 0:
            self._adapt_frame_rate()

    def _update_gaze(self) -> None:
        """瞳孔跟随光标；靠得很近时企鹅会转过头来看你。"""
        if self.sm.is_state(SLEEP):
            self.animator.gaze_x = 0.0
            self.animator.gaze_y = 0.0
            self.animator.look_strength = 0.0
            return
        # mapToGlobal 给的是逻辑坐标，所以光标也必须取逻辑坐标（QCursor），
        # 用 winapi.cursor_pos()（物理）会在缩放屏上把眼神带偏。
        cur = QCursor.pos()
        x, y = cur.x(), cur.y()
        hx, hy = rig.transform_point(self._pose, BODY_CX, HEAD_PIVOT_Y)
        ghx = self.mapToGlobal(QPoint(int(hx * self._scale), int(hy * self._scale)))
        dx = x - ghx.x()
        dy = y - ghx.y()
        dist = (dx * dx + dy * dy) ** 0.5
        self.animator.gaze_x = max(-1.0, min(1.0, dx / 260.0))
        self.animator.gaze_y = max(-1.0, min(1.0, dy / 220.0))
        # 160px 内开始"注意到你"，60px 内完全转过来
        strength = 0.0
        if dist < 160.0:
            strength = min(1.0, (160.0 - dist) / 100.0)
        self.animator.look_strength = strength

    def _pose_differs(self, pose: Pose) -> bool:
        prev = self._painted
        if prev is None:
            return True
        for k in _POSE_KEYS:
            if abs(getattr(pose, k) - getattr(prev, k)) > POSE_EPS:
                return True
        return pose.prop != prev.prop

    def _adapt_frame_rate(self) -> None:
        if self.sm.is_state(SLEEP):
            want = SLEEP_FRAME_MS
        elif self.animator.current_name is None:
            want = IDLE_FRAME_MS
        else:
            want = FRAME_MS
        if self._timer.interval() != want:
            self._timer.setInterval(want)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.scale(self._scale, self._scale)
        rig.paint_penguin(p, self._pose)
        p.end()

    # ==================================================================
    # 鼠标交互
    # ==================================================================
    def contextMenuEvent(self, event) -> None:  # noqa: N802
        """系统级上下文菜单事件（部分环境下才会来）。"""
        self._emit_menu_once()
        event.accept()

    def _emit_menu_once(self) -> None:
        """右键菜单只触发一次。

        这条窗口是 WS_POPUP + Tool + NoActivate，实测系统不会派发
        WM_CONTEXTMENU，所以主要入口是 mouseReleaseEvent 里的右键分支；
        但别的环境可能会派发，这里做去重，避免一次右键弹两遍
        （菜单刚弹出来就被第二个 popup 顶掉）。

        这里只发信号、不自己持有菜单：托盘图标和企鹅本体共用同一份 QMenu，
        避免两处菜单各改一半最后不一致。
        """
        now = time.monotonic()
        if now - self._menu_emitted_at < 0.4:
            return
        self._menu_emitted_at = now
        self.menu_requested.emit()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.RightButton:
            event.accept()
            return
        if event.button() != Qt.LeftButton:
            event.ignore()
            return
        self._pressed = True
        self._drag_lock = True
        self._press_global = event.globalPosition().toPoint()
        self._drag_offset = self._press_global - self.frameGeometry().topLeft()
        self._moved = 0
        self._press_t = time.monotonic()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._pressed:
            event.ignore()
            return
        cur = event.globalPosition().toPoint()
        delta = cur - self._press_global
        self._moved = max(self._moved, abs(delta.x()) + abs(delta.y()))

        if not self._dragging:
            if self._moved <= CLICK_MOVE_TOL:
                return
            if self.sm.is_state(GAME):
                # 游戏中不允许拖走企鹅，否则球都不知道往哪飞
                self._pressed = False
                self._drag_lock = False
                self.drag_blocked_by_game.emit()
                return
            self._dragging = True
            self.drag_started.emit()

        target = cur - self._drag_offset
        self.move(self._clamp_position(target))
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.RightButton:
            self._emit_menu_once()
            event.accept()
            return
        if event.button() != Qt.LeftButton or not self._pressed:
            event.ignore()
            return
        self._pressed = False
        duration = time.monotonic() - self._press_t
        was_dragging = self._dragging
        self._dragging = False
        self._drag_lock = False

        if was_dragging:
            self.move(self._snap_position(self.pos()))
            config.set("pos", [self.x(), self.y()], save=True)
            self.drag_finished.emit(self.pos())
        elif self._moved <= CLICK_MOVE_TOL and duration < 0.45:
            self.clicked.emit()
        event.accept()

    # ==================================================================
    # 位置约束
    # ==================================================================
    def _virtual_geometry(self) -> QRect:
        rect = QRect()
        for scr in QGuiApplication.screens():
            rect = rect.united(scr.geometry())
        return rect if not rect.isNull() else QRect(0, 0, 1920, 1080)

    def _clamp_position(self, pos: QPoint) -> QPoint:
        """拖动时限制在虚拟屏范围内，留出边缘余量。"""
        virt = self._virtual_geometry()
        x = max(virt.left() - EDGE_MARGIN, min(pos.x(), virt.right() - self.width() + EDGE_MARGIN))
        y = max(virt.top() - EDGE_MARGIN, min(pos.y(), virt.bottom() - self.height() + EDGE_MARGIN))
        return QPoint(x, y)

    def _snap_position(self, pos: QPoint) -> QPoint:
        """松手时吸附到最近的屏幕可用区域内，避免卡在屏缝里。"""
        center = QPoint(pos.x() + self.width() // 2, pos.y() + self.height() // 2)
        best = None
        best_d = None
        for scr in QGuiApplication.screens():
            av = scr.availableGeometry()
            if av.contains(center):
                return pos
            d = abs(av.center().x() - center.x()) + abs(av.center().y() - center.y())
            if best_d is None or d < best_d:
                best_d, best = d, av
        if best is None:
            return pos
        x = max(best.left() - EDGE_MARGIN, min(pos.x(), best.right() - self.width() + EDGE_MARGIN))
        y = max(best.top(), min(pos.y(), best.bottom() - self.height() + 10))
        return QPoint(x, y)

    def on_screens_changed(self) -> None:
        self.move(self._snap_position(self.pos()))
        config.set("pos", [self.x(), self.y()], save=True)
        self.screen_geometry_changed.emit()

    # ==================================================================
    # 对外接口
    # ==================================================================
    @property
    def scale(self) -> float:
        return self._scale

    def head_global(self) -> QPoint:
        """企鹅头顶的屏幕坐标，气泡锚点用。

        头顶位置从 rig 的轮廓算，不再写死某个常量——否则一改造型，
        气泡就会挂到脸上去。
        """
        hx, hy = rig.transform_point(self._pose, *rig.outline_top(self._pose))
        return self.mapToGlobal(
            QPoint(int(hx * self._scale), int((hy - 14.0) * self._scale))
        )

    def body_global(self) -> QPoint:
        bx, by = rig.transform_point(self._pose, BODY_CX, BODY_CY)
        return self.mapToGlobal(
            QPoint(int(bx * self._scale), int(by * self._scale))
        )

    def set_scale(self, scale: float) -> None:
        self._scale = max(0.5, float(scale))
        old_center = self.geometry().center()
        self.resize(int(WIN_W * self._scale), int(WIN_H * self._scale))
        self.move(old_center - QPoint(self.width() // 2, self.height() // 2))
        config.set("pet_scale", self._scale)

    def set_fullscreen_hidden(self, hidden: bool) -> None:
        """独占全屏程序会盖住 topmost 窗口，干脆主动躲起来（顺便也是最好的不打扰）。"""
        if hidden == self._hidden_for_fullscreen:
            return
        self._hidden_for_fullscreen = hidden
        if hidden:
            self.hide()
        else:
            self.show()
