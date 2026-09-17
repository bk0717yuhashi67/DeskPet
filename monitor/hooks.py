"""全局键鼠监听（pynput 低级钩子）。

铁律：钩子回调里只做算术和入队，绝不碰 Qt、磁盘或任何会阻塞的东西。
按键只记录"次数 + 时间戳 + 是否退格"，**不记录内容**——连字符形式都不取。
"""
from __future__ import annotations

import math
import queue
import time

from app import logging_setup

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

    # ------------------------------------------------------------ 生命周期
    def start(self) -> bool:
        if self.running:
            return True
        if not PYNPUT_OK:
            self.degraded = True
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

        try:
            self._ms_listener = _ms.Listener(
                on_move=self._on_move, on_click=self._on_click
            )
            self._ms_listener.daemon = True
            self._ms_listener.start()
            started += 1
        except Exception as exc:
            log.warning("鼠标钩子安装失败: %s", exc)
            self._ms_listener = None

        self.running = started > 0
        self.degraded = self._kb_listener is None
        log.info(
            "输入钩子已启动（键盘=%s 鼠标=%s）",
            self._kb_listener is not None, self._ms_listener is not None,
        )
        return self.running

    def stop(self) -> None:
        for listener in (self._kb_listener, self._ms_listener):
            if listener is None:
                continue
            try:
                listener.stop()
            except Exception:
                pass
        self._kb_listener = None
        self._ms_listener = None
        self.running = False

    @property
    def keyboard_ok(self) -> bool:
        return self._kb_listener is not None

    # ------------------------------------------------------------ 回调
    def _on_press(self, key) -> None:  # noqa: ANN001
        try:
            is_back = key == _kb.Key.backspace
        except Exception:
            is_back = False
        now = time.time()
        # 只入队，不做任何其他事情
        self.events.put(("key", now, is_back))

    def _on_move(self, x: int, y: int) -> None:
        self.last_pos = (int(x), int(y))
        lx, ly = self._lx, self._ly
        self._lx, self._ly = float(x), float(y)
        if lx is None:
            return
        d = math.hypot(x - lx, y - ly)
        if d <= 0.0 or d > MAX_JUMP_PX:
            return
        self._moved_since_report += d
        if self._moved_since_report >= MOVE_REPORT:
            dist = self._moved_since_report
            self._moved_since_report = 0.0
            self.events.put(("move", time.time(), dist))

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
