"""鼠标原始输入（Raw Input）与输入监听装配的测试。

背景（2026-09-22 实测）：鼠标原来走 pynput 的 WH_MOUSE_LL 钩子，而钩子要求
系统把每一个鼠标事件**同步**交给我们的线程；真实鼠标 500~1000 次/秒时，
输入通路的往返延迟 p99 从 3.2ms 涨到 6.8ms，表现为光标卡顿。
改成 Raw Input 后回到基线。这里用假的 winapi/pynput 只测纯逻辑，
不装任何真实钩子、不注册任何真实设备。
"""
from __future__ import annotations

import queue
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor import hooks, winapi  # noqa: E402


# ---------------------------------------------------------------- 假对象
class FakeListener:
    """假的 pynput Listener，只记录是否被启动。"""

    made: list["FakeListener"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.daemon = False
        FakeListener.made.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class FakeKb:
    class Key:
        backspace = "VK_BACK"

    Listener = FakeListener


class FakeMs:
    Listener = FakeListener


def _fake_env(register_ok=True, cursor_seq=None, raw_result=(10, 0, 0)):
    """把 winapi 的取数函数与 pynput 换成假的，返回还原函数。"""
    saved = {}

    def patch(obj, name, value):
        saved[(obj, name)] = getattr(obj, name)
        setattr(obj, name, value)

    calls = {"cursor": 0, "register": []}

    def fake_register(hwnd, enable=True):
        calls["register"].append((int(hwnd), bool(enable)))
        return register_ok

    seq = list(cursor_seq or [])

    def fake_cursor():
        calls["cursor"] += 1
        if seq:
            return seq.pop(0)
        return (0, 0)

    patch(winapi, "register_raw_mouse", fake_register)
    patch(winapi, "cursor_pos", fake_cursor)
    patch(winapi, "raw_mouse_input", lambda lp: raw_result)
    patch(hooks, "_kb", FakeKb)
    patch(hooks, "_ms", FakeMs)
    patch(hooks, "PYNPUT_OK", True)
    FakeListener.made = []

    def undo():
        for (obj, name), value in saved.items():
            setattr(obj, name, value)

    return undo, calls


def _set_raw(lx, ly, buttons=0):
    """让 feed_raw_input 读到指定的原始数据。"""
    winapi.raw_mouse_input = lambda lp: (lx, ly, buttons)


class TestDistanceAccumulation(unittest.TestCase):
    """距离累计与节流。"""

    def setUp(self):
        self.undo, self.calls = _fake_env(cursor_seq=[(0, 0), (3, 0), (6, 0)])
        self.h = hooks.InputHooks()
        self.addCleanup(self.undo)

    def test_no_report_below_threshold(self):
        self.h.feed_raw_input(0)                     # 首个点只做基准
        self.h.feed_raw_input(0)                     # 移动 3px，未达 6px
        self.assertTrue(self.h.events.empty())

    def test_report_after_threshold(self):
        self.h.feed_raw_input(0)
        self.h.feed_raw_input(0)
        self.h.feed_raw_input(0)                     # 累计 6px
        kind, _ts, dist = self.h.events.get_nowait()
        self.assertEqual(kind, "move")
        self.assertAlmostEqual(dist, 6.0, places=3)

    def test_jump_discarded(self):
        self.undo()
        self.undo, self.calls = _fake_env(cursor_seq=[(0, 0), (2000, 0), (2006, 0)])
        h = hooks.InputHooks()
        h.feed_raw_input(0)
        h.feed_raw_input(0)                          # 2000px 跳变 -> 丢弃
        self.assertTrue(h.events.empty())
        h.feed_raw_input(0)                          # 从 2000 起算 6px -> 上报
        self.assertEqual(h.events.get_nowait()[0], "move")

    def test_pure_button_packet_does_not_move(self):
        self.undo()
        self.undo, self.calls = _fake_env()
        h = hooks.InputHooks()
        _set_raw(0, 0, winapi.RI_MOUSE_LEFT_BUTTON_DOWN)
        h.feed_raw_input(0)
        self.assertEqual(self.calls["cursor"], 0)


class TestClicks(unittest.TestCase):
    """按钮位到按钮名的映射。"""

    def setUp(self):
        self.undo, self.calls = _fake_env()
        self.h = hooks.InputHooks()
        self.addCleanup(self.undo)

    def _click(self, flags):
        _set_raw(0, 0, flags)
        self.assertFalse(self.h.feed_raw_input(0) is None)
        return self.h.events.get_nowait()

    def test_left(self):
        self.assertEqual(self._click(winapi.RI_MOUSE_LEFT_BUTTON_DOWN)[2], "left")

    def test_right(self):
        self.assertEqual(self._click(winapi.RI_MOUSE_RIGHT_BUTTON_DOWN)[2], "right")

    def test_middle(self):
        self.assertEqual(self._click(winapi.RI_MOUSE_MIDDLE_BUTTON_DOWN)[2], "middle")

    def test_extra_buttons(self):
        self.assertEqual(self._click(winapi.RI_MOUSE_BUTTON_4_DOWN)[2], "x1")
        self.assertEqual(self._click(winapi.RI_MOUSE_BUTTON_5_DOWN)[2], "x2")

    def test_button_up_is_ignored(self):
        _set_raw(0, 0, winapi.RI_MOUSE_LEFT_BUTTON_UP)
        self.h.feed_raw_input(0)
        self.assertTrue(self.h.events.empty())

    def test_two_buttons_in_one_packet(self):
        _set_raw(0, 0,
                 winapi.RI_MOUSE_LEFT_BUTTON_DOWN | winapi.RI_MOUSE_RIGHT_BUTTON_DOWN)
        self.h.feed_raw_input(0)
        names = {self.h.events.get_nowait()[2] for _ in range(2)}
        self.assertEqual(names, {"left", "right"})

    def test_last_click_recorded(self):
        self._click(winapi.RI_MOUSE_LEFT_BUTTON_DOWN)
        self.assertIsNotNone(self.h.last_click)
        self.assertEqual(self.h.last_click[2], "left")


class TestMalformedInput(unittest.TestCase):
    """解析失败必须安静返回，绝不抛异常。"""

    def setUp(self):
        self.undo, _ = _fake_env()
        self.h = hooks.InputHooks()
        self.addCleanup(self.undo)

    def test_not_mouse_event(self):
        winapi.raw_mouse_input = lambda lp: None
        self.assertFalse(self.h.feed_raw_input(0))
        self.assertTrue(self.h.events.empty())

    def test_returns_true_on_success(self):
        _set_raw(5, 0, 0)
        self.assertTrue(self.h.feed_raw_input(0))


class TestMouseSourceSelection(unittest.TestCase):
    """Raw Input 优先；只有它不可用才退回钩子。"""

    def setUp(self):
        self.undo, self.calls = _fake_env(register_ok=True)
        self.addCleanup(self.undo)

    def test_raw_preferred_and_no_hook(self):
        h = hooks.InputHooks()
        h.set_raw_window(1234)
        h.start()
        self.assertTrue(h._raw_ok)
        self.assertEqual(h.mouse_mode, "raw")
        self.assertIsNone(h._ms_listener)          # 绝不能同时装钩子
        self.assertEqual(self.calls["register"], [(1234, True)])

    def test_deferred_until_window_ready(self):
        h = hooks.InputHooks()
        h.start()                                   # 窗口还没就绪
        self.assertEqual(h.mouse_mode, "none")
        self.assertIsNone(h._ms_listener)           # 也不能先退回钩子
        h.set_raw_window(99)
        self.assertEqual(h.mouse_mode, "raw")

    def test_fallback_to_hook_when_raw_fails(self):
        self.undo()
        self.undo, self.calls = _fake_env(register_ok=False)
        h = hooks.InputHooks()
        h.set_raw_window(7)
        h.start()
        self.assertFalse(h._raw_ok)
        self.assertEqual(h.mouse_mode, "hook")
        self.assertIsNotNone(h._ms_listener)
        self.assertTrue(h._ms_listener.started)

    def test_stop_unregisters(self):
        h = hooks.InputHooks()
        h.set_raw_window(55)
        h.start()
        h.stop()
        self.assertIn((55, False), self.calls["register"])
        self.assertFalse(h._raw_ok)
        self.assertEqual(h.mouse_mode, "none")

    def test_mouse_desc_waits_for_window(self):
        """日志里的鼠标链路描述必须能区分"等窗口"和"坏了"。

        实测踩过：只打 `mouse_mode` 时，启动瞬间的 `鼠标=none`
        被当成"鼠标统计没生效"，其实只是窗口还没 show。
        """
        h = hooks.InputHooks()
        self.assertIn("等待窗口", h.mouse_desc())
        h.start()
        self.assertIn("等待窗口", h.mouse_desc())
        h.set_raw_window(1)
        self.assertEqual(h.mouse_desc(), "Raw Input")
        h.stop()
        self.assertEqual(h.mouse_desc(), "不可用")

    def test_mouse_desc_reports_fallback(self):
        self.undo()
        self.undo, self.calls = _fake_env(register_ok=False)
        h = hooks.InputHooks()
        h.set_raw_window(7)
        h.start()
        self.assertIn("钩子", h.mouse_desc())


class TestKeyboardCounterRegression(unittest.TestCase):
    """键盘回调曾经每次按键都抛 AttributeError（属性没初始化），
    被 pynput 静默吞掉，导致统计永远是 0。这里锁住这个行为。"""

    def setUp(self):
        self.undo, _ = _fake_env()
        self.addCleanup(self.undo)

    def test_press_enqueues_key_event(self):
        h = hooks.InputHooks()
        h._on_press("VK_BACK")                      # 不应抛异常
        kind, _ts, is_back = h.events.get_nowait()
        self.assertEqual(kind, "key")
        self.assertTrue(is_back)

    def test_first_event_ts_set(self):
        h = hooks.InputHooks()
        self.assertIsNone(h.first_event_ts)
        h._on_press("A")
        self.assertIsNotNone(h.first_event_ts)

    def test_sampler_read_attributes_exist(self):
        """采样器会直接读这几个属性，缺一个就是一个 AttributeError。"""
        h = hooks.InputHooks()
        self.assertIsInstance(h.start_ts, float)
        self.assertFalse(h.warned_no_event)
        self.assertIsNone(h.first_event_ts)

    def test_non_backspace(self):
        h = hooks.InputHooks()
        h._on_press("A")
        self.assertFalse(h.events.get_nowait()[2])


class TestRawStructLayout(unittest.TestCase):
    """ctypes 结构体布局错了会静默读出垃圾数据，这里锁死尺寸与按钮位。"""

    def test_device_struct_size(self):
        if __import__("ctypes").sizeof(__import__("ctypes").c_void_p) == 8:
            self.assertEqual(__import__("ctypes").sizeof(winapi.RAWINPUTDEVICE), 16)
            self.assertEqual(__import__("ctypes").sizeof(winapi.RAWINPUTHEADER), 24)

    def test_button_flags_are_low_word(self):
        ri = winapi.RAWINPUT()
        flags, data = winapi.RI_MOUSE_WHEEL, 120
        ri.mouse.ulButtons = flags | (data << 16)
        self.assertEqual(ri.mouse.ulButtons & 0xFFFF, flags)

    def test_relative_offsets(self):
        self.assertLess(winapi.RAWMOUSE.usFlags.offset, winapi.RAWMOUSE.lLastX.offset)


class TestQueueInterfaceUnchanged(unittest.TestCase):
    """采样器依赖的队列协议不能变：("move", ts, dist) / ("click", ts, name)。"""

    def setUp(self):
        self.undo, _ = _fake_env(cursor_seq=[(0, 0), (8, 0)])
        self.addCleanup(self.undo)

    def test_event_shapes(self):
        h = hooks.InputHooks()
        self.assertIsInstance(h.events, queue.Queue)
        h.feed_raw_input(0)
        h.feed_raw_input(0)
        ev = h.events.get_nowait()
        self.assertEqual(len(ev), 3)
        self.assertEqual(ev[0], "move")
        _set_raw(0, 0, winapi.RI_MOUSE_LEFT_BUTTON_DOWN)
        h.feed_raw_input(0)
        ev = h.events.get_nowait()
        self.assertEqual(len(ev), 3)
        self.assertEqual(ev[0], "click")
        self.assertIn(ev[2], ("left", "right", "middle", "x1", "x2"))


if __name__ == "__main__":
    unittest.main()
