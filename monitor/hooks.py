"""全局输入监听。

**鼠标走 Raw Input，不走钩子。**
`SetWindowsHookEx(WH_MOUSE_LL)` 要求系统把每一个鼠标事件**同步**交给我们的线程，
事件率高时（真实鼠标 500~1000 次/秒）输入通路的尾延迟会明显变长，表现为光标卡顿。
2026-09-22 实测：装钩子后输入往返 p99 从 3.2ms 涨到 6.8ms；换成 Raw Input 后回到基线。
Raw Input 只是把事件**投递**到窗口消息队列，处理慢一点也不会拖住输入线程。

键盘仍用 pynput 的钩子：按键频率在 10 次/秒量级，对输入通路的影响可以忽略，
而它给的按键语义（退格键等）比解析原始扫描码省事得多。

铁律：回调里只做算术和入队，绝不碰 Qt、磁盘或任何会阻塞的东西。
按键只记录"次数 + 时间戳 + 是否退格"，**不记录内容**——连字符形式都不取。
"""
from __future__ import annotations

import math
import queue
import time

from app import logging_setup
from monitor import winapi

log = logging_setup.get("hooks")

try:
    from pynput import keyboard as _kb
    from pynput import mouse as _ms
    PYNPUT_OK = True
except Exception as exc:  # pragma: no cover
    _kb = _ms = None
    PYNPUT_OK = False
    log.warning("pynput 不可用，输入统计将被禁用: %s", exc)

MAX_JUMP_PX = 500.0     # 超过这个距离视为坐标跳变（分辨率切换 / 锁屏复位），丢弃
MOVE_REPORT = 6         # 每累计多少像素上报一次位置（降低队列压力）

# Raw Input 的按钮位 -> 与 pynput 一致的按钮名（采样器只统计 left/right/middle）
_RAW_BUTTONS = (
    (winapi.RI_MOUSE_LEFT_BUTTON_DOWN, "left"),
    (winapi.RI_MOUSE_RIGHT_BUTTON_DOWN, "right"),
    (winapi.RI_MOUSE_MIDDLE_BUTTON_DOWN, "middle"),
    (winapi.RI_MOUSE_BUTTON_4_DOWN, "x1"),
    (winapi.RI_MOUSE_BUTTON_5_DOWN, "x2"),
)


class InputHooks:
    """键盘 + 鼠标监听。所有原始事件推入 queue，由主线程消费聚合。"""

    def __init__(self, events: "queue.Queue | None" = None) -> None:
        self.events: queue.Queue = events or queue.Queue()
        self._kb_listener = None
        self._ms_listener = None
        self.running = False
        # 实时坐标：供抛球游戏读取（元组赋值在 CPython 里是原子的）
        self.last_pos: tuple[int, int] = (0, 0)
        self.last_click: tuple[int, int, str, float] | None = None
        self._lx: float | None = None
        self._ly: float | None = None
        self._moved_since_report = 0.0
        self.degraded = False
        # 鼠标事件来源："raw"（Raw Input，默认）/ "hook"（钩子兜底）/ "none"
        self.mouse_mode = "none"
        self._raw_hwnd = 0
        self._raw_ok = False
        # 下面三个必须在这里初始化：它们分别在按键回调、采样器告警分支里被读取，
        # 而采样器与回调都只做"读"，不会先建属性。曾经因为漏了 first_event_ts，
        # 每次按键都在回调里抛 AttributeError（被 pynput 静默吞掉），
        # 结果键盘统计一整年都是 0 而没有任何报错。
        self.first_event_ts: float | None = None
        self.start_ts: float = 0.0
        self.warned_no_event = False

    # ------------------------------------------------------------ 生命周期
    def start(self) -> bool:
        if self.running:
            return True
        if not PYNPUT_OK:
            self.degraded = True
            self.start_ts = time.time()
            return False
        started = 0
        try:
            self._kb_listener = _kb.Listener(
                on_press=self._on_press, on_release=None
            )
            self._kb_listener.daemon = True
            self._kb_listener.start()
            started += 1
        except Exception as exc:
            log.warning("键盘钩子安装失败: %s", exc)
            self._kb_listener = None

        # 鼠标：优先 Raw Input。窗口句柄可能还没就绪（窗口尚未 show），
        # 这时先不动鼠标——等 set_raw_window() 被调用时再装，
        # 避免为了"先用上"而退回钩子、把光标又拖慢。
        started += self._setup_mouse()

        self.running = True
        self.degraded = self._kb_listener is None
        self.start_ts = time.time()
        log.info(
            "输入监听已启动（键盘=%s 鼠标=%s）",
            self._kb_listener is not None, self.mouse_desc(),
        )
        return self.running

    def mouse_desc(self) -> str:
        """鼠标链路的人话描述——只写 `mouse_mode` 会让人把"等待窗口"读成"功能坏了"。

        实测过这个误会：日志里 `鼠标=none` 被当成"鼠标统计没生效"，
        实际只是窗口还没 show、Raw Input 尚未注册。
        """
        if self.mouse_mode == "raw":
            return "Raw Input"
        if self.mouse_mode == "hook":
            return "全局钩子兜底"
        if not self._raw_hwnd:
            return "等待窗口句柄"
        return "不可用"

    def _setup_mouse(self) -> int:
        """装鼠标监听。返回成功装载的数量（0 或 1）。"""
        if self._raw_ok or self._ms_listener is not None:
            return 1
        if not self._raw_hwnd:
            # 窗口句柄还没就绪（尚未 show）：等 set_raw_window() 再装。
            # 这里刻意不退回钩子——否则每次启动都会先用钩子跑一段，白白拖慢光标。
            return 0
        if winapi.register_raw_mouse(self._raw_hwnd, True):
            self._raw_ok = True
            self.mouse_mode = "raw"
            return 1
        try:
            self._ms_listener = _ms.Listener(
                on_move=self._on_move, on_click=self._on_click
            )
            self._ms_listener.daemon = True
            self._ms_listener.start()
            self.mouse_mode = "hook"
            log.warning("Raw Input 注册失败，鼠标退回全局钩子（可能影响光标跟手度）")
            return 1
        except Exception as exc:
            log.warning("鼠标钩子安装失败: %s", exc)
            self._ms_listener = None
            self.mouse_mode = "none"
            return 0

    def set_raw_window(self, hwnd: int) -> bool:
        """窗口句柄就绪后调用（Raw Input 需要一个窗口来接收 WM_INPUT）。"""
        if not hwnd:
            return False
        self._raw_hwnd = int(hwnd)
        if not self.running:
            return False
        if self._setup_mouse():
            if self._raw_ok:
                # 这行日志是"鼠标到底走哪条链路"的唯一凭证，必须有：
                # 出问题时第一件事就是翻它，而不是猜。
                log.info("鼠标输入走 Raw Input（窗口句柄 %d）", self._raw_hwnd)
            return self._raw_ok
        return False

    def stop(self) -> None:
        if self._raw_ok and self._raw_hwnd:
            try:
                winapi.register_raw_mouse(self._raw_hwnd, False)
            except Exception:
                pass
            self._raw_ok = False
        for listener in (self._kb_listener, self._ms_listener):
            if listener is None:
                continue
            try:
                listener.stop()
            except Exception:
                pass
        self._kb_listener = None
        self._ms_listener = None
        self.mouse_mode = "none"
        self.running = False

    @property
    def keyboard_ok(self) -> bool:
        return self._kb_listener is not None

    # ------------------------------------------------------------ Raw Input 入口
    def feed_raw_input(self, lparam: int) -> bool:
        """处理一条 WM_INPUT（由宠物窗口的 nativeEvent 转发过来）。

        读的是**光标绝对位置**来累计距离，所以这个函数即使被延迟调用也不会丢数据：
        消息堆着也会在事后一次性把总位移算出来。
        """
        data = winapi.raw_mouse_input(lparam)
        if data is None:
            return False
        dx, dy, buttons = data

        if buttons:
            now = time.time()
            for bit, name in _RAW_BUTTONS:
                if buttons & bit:
                    self.last_click = (*self.last_pos, name, now)
                    self.events.put(("click", now, name))

        # 有位移才更新；纯按键事件不产生距离
        if dx or dy:
            self._track_cursor(winapi.cursor_pos())
        return True

    def _track_cursor(self, pos: tuple[int, int]) -> None:
        px, py = int(pos[0]), int(pos[1])
        self.last_pos = (px, py)
        lx, ly = self._lx, self._ly
        self._lx, self._ly = float(px), float(py)
        if lx is None:
            return
        d = math.hypot(px - lx, py - ly)
        if d <= 0.0 or d > MAX_JUMP_PX:
            return
        self._moved_since_report += d
        if self._moved_since_report >= MOVE_REPORT:
            dist = self._moved_since_report
            self._moved_since_report = 0.0
            self.events.put(("move", time.time(), dist))

    # ------------------------------------------------------------ 钩子回调（键盘 / 兜底鼠标）
    def _on_press(self, key) -> None:  # noqa: ANN001
        try:
            is_back = key == _kb.Key.backspace
        except Exception:
            is_back = False
        now = time.time()
        # 只入队，不做任何其他事情
        if self.first_event_ts is None:
            self.first_event_ts = now
        self.events.put(("key", now, is_back))

    def _on_move(self, x: int, y: int) -> None:
        self._track_cursor((int(x), int(y)))

    def _on_click(self, x: int, y: int, button, pressed: bool) -> None:
        if not pressed:
            return
        try:
            name = getattr(button, "name", str(button))
        except Exception:
            name = "unknown"
        self.last_pos = (int(x), int(y))
        self.last_click = (int(x), int(y), name, time.time())
        self.events.put(("click", time.time(), name))
