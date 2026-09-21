"""全屏判定的回归测试。

这些用例的形状数据全部来自 2026-09-21 在真机上抓取的真实窗口，
不是凭空构造的——每一行都能对应到一次实际观测。

背景：需求是"只有真全屏隐藏企鹅，窗口化（含最大化）不隐藏"。
这个判定出过两次错，方向正好相反：

  1. 最初只看尺寸（>= 显示器 92%）。最大化窗口占满工作区 -> 误判全屏
     -> 症状"窗口一最大化企鹅就消失"。
  2. 修 (1) 时加了"有 WS_MAXIMIZE 就返回 False"。但 Chromium 系
     **视频全屏时仍保留 WS_MAXIMIZE**，于是真全屏被漏判
     -> 症状"浏览器看视频全屏，企鹅不隐藏"。

结论：WS_MAXIMIZE 完全不能作为判据，只能靠"有没有标题栏/边框"来区分。
本文件把这两个方向的反例都钉死。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitor import winapi  # noqa: E402

MON = (0, 0, 1536, 864)          # 本机显示器（125% 缩放下 1536x864 逻辑像素）
MW = MON[2] - MON[0]
MH = MON[3] - MON[1]

# 逐个"伪造"窗口，验证纯逻辑（不依赖真实窗口是否存在）
def _fake(rect, style, cls="Chrome_WidgetWin_1", visible=True, minimized=False):
    """把 winapi 里用到的取数函数临时替换成固定值，跑完自动还原。"""
    saved = {}

    def patch(name, value):
        saved[name] = getattr(winapi, name)
        setattr(winapi, name, value)

    patch("window_class", lambda h: cls)
    patch("is_visible", lambda h: visible)
    patch("is_minimized", lambda h: minimized)
    patch("window_rect", lambda h: rect)
    patch("monitor_rect", lambda h: MON)
    patch("window_style", lambda h: style)

    def restore():
        for k, v in saved.items():
            setattr(winapi, k, v)

    return restore


class TestIsFullscreen(unittest.TestCase):
    """形状 -> 判定。rect 为 None 代表取不到窗口矩形。"""

    # ------------------------------------------------------------ 真全屏
    def test_browser_video_fullscreen_is_detected(self):
        """回归：Chromium 视频全屏保留 WS_MAXIMIZE，但仍须判为全屏。

        真机抓到的形状（Edge / bilibili，t=47.6s）：
            rect=(0,0,1536,864)  ratio 1.0000x1.0000
            style=0x170B0000  MAX=True CAP=False THICK=False BORDER=False
        """
        style = winapi.WS_MAXIMIZE  # 只有 MAXIMIZE，没有边框类位
        restore = _fake((0, 0, 1536, 864), style)
        try:
            self.assertTrue(
                winapi.is_fullscreen(12345),
                "视频全屏必须判为全屏（WS_MAXIMIZE 不能否决它）",
            )
        finally:
            restore()

    def test_browser_video_fullscreen_1px_short(self):
        """NVIDIA Overlay 那种 1535x864（少 1 像素）也算全屏（1% 容差）。"""
        restore = _fake((0, 0, 1535, 864), 0x94000000)
        try:
            self.assertTrue(winapi.is_fullscreen(1))
        finally:
            restore()

    def test_f11_fullscreen_is_detected(self):
        """F11 全屏：无边框 + 精确铺满。"""
        restore = _fake((0, 0, 1536, 864), 0x170B0000)
        try:
            self.assertTrue(winapi.is_fullscreen(1))
        finally:
            restore()

    # ------------------------------------------------------------ 最大化
    def test_maximized_window_is_not_fullscreen(self):
        """回归：最大化窗口带边框，必须判为非全屏（否则企鹅会消失）。

        真机抓到的形状（Edge 最大化）：
            rect=(-7,-7,1543,831)  比屏幕还大（Win10+ 最大化外扩约 7px）
            style=0x17CF0000  MAX=True CAP=True THICK=True BORDER=True
        """
        style = (winapi.WS_MAXIMIZE | winapi.WS_CAPTION
                 | winapi.WS_THICKFRAME | winapi.WS_BORDER)
        restore = _fake((-7, -7, 1543, 831), style)
        try:
            self.assertFalse(winapi.is_fullscreen(1))
        finally:
            restore()

    def test_maximized_window_exactly_covering_monitor(self):
        """即便最大化窗口尺寸正好等于显示器，只要有边框就不算全屏。"""
        style = (winapi.WS_MAXIMIZE | winapi.WS_CAPTION
                 | winapi.WS_THICKFRAME | winapi.WS_BORDER)
        restore = _fake((0, 0, 1536, 864), style)
        try:
            self.assertFalse(winapi.is_fullscreen(1))
        finally:
            restore()

    def test_bordered_window_without_maximize(self):
        """普通带边框的大窗口（拖到很大）也不算全屏。"""
        style = winapi.WS_CAPTION | winapi.WS_THICKFRAME | winapi.WS_BORDER
        restore = _fake((0, 0, 1536, 864), style)
        try:
            self.assertFalse(winapi.is_fullscreen(1))
        finally:
            restore()

    # ------------------------------------------------------------ 外壳/桌面
    def test_desktop_atom_class_is_excluded(self):
        """回归：桌面窗口类名是数字原子 '#32769'，铺满整屏且无边框，
        会绕过 SHELL_CLASSES，必须由 '#3276' 前缀单独挡掉。"""
        restore = _fake((0, 0, 1536, 864), 0x96000000, cls="#32769")
        try:
            self.assertFalse(winapi.is_fullscreen(1))
        finally:
            restore()

    def test_shell_classes_excluded(self):
        for cls in ("Progman", "WorkerW", "Shell_TrayWnd"):
            with self.subTest(cls=cls):
                restore = _fake((0, 0, 1536, 864), 0x96000000, cls=cls)
                try:
                    self.assertFalse(winapi.is_fullscreen(1))
                finally:
                    restore()

    # ------------------------------------------------------------ 边界
    def test_invisible_and_minimized(self):
        restore = _fake((0, 0, 1536, 864), 0x94000000, visible=False)
        try:
            self.assertFalse(winapi.is_fullscreen(1))
        finally:
            restore()

        restore = _fake((0, 0, 1536, 864), 0x94000000, minimized=True)
        try:
            self.assertFalse(winapi.is_fullscreen(1))
        finally:
            restore()

    def test_zero_hwnd(self):
        self.assertFalse(winapi.is_fullscreen(0))

    def test_half_screen_not_fullscreen(self):
        """半屏窗口显然不是全屏。"""
        restore = _fake((0, 0, 768, 864), 0x170B0000)
        try:
            self.assertFalse(winapi.is_fullscreen(1))
        finally:
            restore()

    def test_rect_unavailable(self):
        """取不到矩形时要安全返回 False，不能抛异常。"""
        restore = _fake(None, 0x170B0000)
        try:
            self.assertFalse(winapi.is_fullscreen(1))
        finally:
            restore()


class TestStyleConstants(unittest.TestCase):
    """样式位常量值必须与 Win32 头文件一致（写错会静默误判）。"""

    def test_values(self):
        self.assertEqual(winapi.GWL_STYLE, -16)
        self.assertEqual(winapi.WS_MAXIMIZE, 0x01000000)
        self.assertEqual(winapi.WS_CAPTION, 0x00C00000)
        self.assertEqual(winapi.WS_THICKFRAME, 0x00040000)
        self.assertEqual(winapi.WS_BORDER, 0x00800000)

    def test_style_read_is_unsigned(self):
        """GetWindowLongW 的 restype 必须是无符号，否则高位为 1 时读出负数。"""
        import ctypes
        from ctypes import wintypes

        rt = winapi.user32.GetWindowLongW.restype
        self.assertIn(rt, (wintypes.DWORD, ctypes.c_ulong),
                      f"restype 应为无符号，实际 {rt}")

    def test_shell_classes_include_common_shell(self):
        for cls in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            self.assertIn(cls, winapi.SHELL_CLASSES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
