"""截屏工具（纯 ctypes 调 GDI，不依赖 Pillow / Add-Type）。

用于把桌面上的企鹅拍下来，方便记录造型或排查问题。

    .venv\\Scripts\\python.exe tools/screenshot.py            # 截整个虚拟屏
    .venv\\Scripts\\python.exe tools/screenshot.py out.png    # 指定输出

顺带一提：本文件也是"用 ctypes 直接抓屏"的最小可用示例。
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def grab(rect: tuple[int, int, int, int] | None = None) -> bytes:
    """抓屏，返回 BGRA 像素缓冲（top-down）。"""
    if rect is None:
        x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    else:
        x, y, w, h = rect
    if w <= 0 or h <= 0:
        raise RuntimeError("屏幕尺寸异常，可能没有可用桌面会话")

    hdc_screen = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    gdi32.SelectObject(hdc_mem, hbmp)
    try:
        if not gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, x, y, SRCCOPY):
            raise RuntimeError("BitBlt 失败")

        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth = w
        bi.biHeight = -h            # 负数 = top-down，省得再翻转
        bi.biPlanes = 1
        bi.biBitCount = 32
        bi.biCompression = 0
        bi.biSizeImage = w * h * 4

        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS):
            raise RuntimeError("GetDIBits 失败")
        return buf.raw
    finally:
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)


def save(path: str, rect: tuple[int, int, int, int] | None = None) -> str:
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])  # noqa: F841

    if rect is None:
        w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    else:
        w, h = rect[2], rect[3]

    data = grab(rect)
    img = QImage(data, w, h, w * 4, QImage.Format_RGB32)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if not img.save(path, "PNG"):
        raise RuntimeError(f"保存失败: {path}")
    return path


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "shots", "screen.png")
    path = save(out)
    from PySide6.QtGui import QImage

    img = QImage(path)
    print(f"已保存 {path}  ({img.width()}x{img.height()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
