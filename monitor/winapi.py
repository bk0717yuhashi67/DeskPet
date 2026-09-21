"""Windows 原生 API 封装（纯 ctypes，不依赖 pywin32）。

只暴露本项目真正需要的能力：
  * 前台窗口 / 窗口标题 / 进程 PID
  * 空闲时长（GetLastInputInfo，零成本）
  * 光标位置
  * 全屏检测
  * 锁屏会话通知
  * 窗口点击穿透相关的窗口样式操作
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:
    wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
except OSError:  # pragma: no cover - 极端精简系统
    wtsapi32 = None

# ---------------------------------------------------------------- 常量
WM_NCHITTEST = 0x0084
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEACTIVATE = 0x0021
WM_POWERBROADCAST = 0x0218
WM_WTSSESSION_CHANGE = 0x02B1

HTTRANSPARENT = -1
HTCLIENT = 1
MA_NOACTIVATE = 3

PBT_APMSUSPEND = 0x0004
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_APMRESUMESUSPEND = 0x0007

WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
NOTIFY_FOR_THIS_SESSION = 0

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

# 样式位：用来区分"真全屏"和"最大化窗口"。
# 最大化窗口在 1920x1080 上会占满 100% 工作区，尺寸判据对它完全失效，
# 必须靠样式位识别（见 is_fullscreen）。
GWL_STYLE = -16
WS_MAXIMIZE = 0x01000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_BORDER = 0x00800000

MONITOR_DEFAULTTONEAREST = 2

# 这些窗口类名即使在"全屏"尺寸下也不算真正的全屏应用
SHELL_CLASSES = {
    "Progman",
    "WorkerW",
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow",
    "ForegroundStaging",
    "MultitaskingViewFrame",
    "XamlExplorerHostIslandWindow",
    "TaskListThumbnailWnd",
    "SysShadow",
    "NarratorHelperWindow",
}


# ---------------------------------------------------------------- 结构体
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class MSG(ctypes.Structure):
    """与 Win32 的 MSG 布局一致，用于解析 nativeEvent 传来的指针。"""
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt_x", wintypes.LONG),
        ("pt_y", wintypes.LONG),
    ]


_MSG_SIZE = ctypes.sizeof(MSG)


def read_msg(message) -> MSG | None:
    """把 nativeEvent 的 message 参数转成 MSG。

    PySide6 各版本对 void* 的暴露方式不一致（int / VoidPtr / bytes-like），
    所以这里做多路兼容：先当地址用，不行再当字节缓冲用。任何一条通路成功即可，
    全部失败返回 None —— 调用方据此降级，绝不崩溃。
    """
    addr = None
    if isinstance(message, int):
        addr = message
    else:
        try:
            addr = int(message)
        except Exception:
            addr = None

    if addr:
        try:
            return MSG.from_address(addr)
        except Exception:
            pass

    buf = None
    try:
        if isinstance(message, (bytes, bytearray, memoryview)):
            buf = bytes(message)
        elif hasattr(message, "toBytes"):
            buf = message.toBytes()
    except Exception:
        buf = None

    if buf:
        try:
            m = MSG()
            ctypes.memmove(ctypes.addressof(m), buf[:_MSG_SIZE], _MSG_SIZE)
            return m
        except Exception:
            return None
    return None


# ---------------------------------------------------------------- 签名
user32.GetForegroundWindow.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
user32.GetLastInputInfo.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
user32.OpenInputDesktop.restype = wintypes.HANDLE
user32.CloseDesktop.argtypes = [wintypes.HANDLE]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
kernel32.GetTickCount64.restype = ctypes.c_ulonglong
kernel32.GetTickCount64.argtypes = []
if wtsapi32 is not None:
    wtsapi32.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
    wtsapi32.WTSRegisterSessionNotification.restype = wintypes.BOOL
    wtsapi32.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]
    wtsapi32.WTSUnRegisterSessionNotification.restype = wintypes.BOOL


# ---------------------------------------------------------------- 基础查询
def foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0)


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    if not hwnd:
        return 0
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return int(pid.value)


def window_title(hwnd: int, limit: int = 512) -> str:
    if not hwnd:
        return ""
    n = user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(min(n + 1, limit))
    user32.GetWindowTextW(wintypes.HWND(hwnd), buf, len(buf))
    return buf.value


def window_class(hwnd: int, limit: int = 256) -> str:
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(limit)
    user32.GetClassNameW(wintypes.HWND(hwnd), buf, limit)
    return buf.value


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    if not hwnd:
        return None
    r = RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r)):
        return None
    return (r.left, r.top, r.right, r.bottom)


def idle_seconds() -> float:
    """自上次键鼠输入起的秒数。"""
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(lii)):
        return 0.0
    tick = int(kernel32.GetTickCount64())
    delta = tick - int(lii.dwTime)
    if delta < 0:
        delta = 0
    return delta / 1000.0


def cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return (0, 0)
    return (int(pt.x), int(pt.y))


def window_from_point(x: int, y: int) -> int:
    return int(user32.WindowFromPoint(wintypes.POINT(int(x), int(y))) or 0)


def is_visible(hwnd: int) -> bool:
    return bool(user32.IsWindowVisible(wintypes.HWND(hwnd)))


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(wintypes.HWND(hwnd)))


# ---------------------------------------------------------------- 全屏
def monitor_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    mon = user32.MonitorFromWindow(wintypes.HWND(hwnd), MONITOR_DEFAULTTONEAREST)
    if not mon:
        return None
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        return None
    r = mi.rcMonitor
    return (r.left, r.top, r.right, r.bottom)


def window_style(hwnd: int) -> int:
    return int(user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_STYLE))


def is_fullscreen(hwnd: int) -> bool:
    """窗口是否**真正**全屏占满一块显示器（排除桌面/任务栏外壳与最大化窗口）。

    关键点：不能用"尺寸 >= 92% 显示器"当唯一判据。
    实测本机 1920x1080，最大化的窗口占满 100% 工作区（含标题栏），
    尺寸判据会把它误判成全屏 —— 症状是"只要把窗口最大化，企鹅就消失了"。
    真正的全屏应用（视频播放器 / PPT 放映 / 游戏）会**移除标题栏与边框**，
    所以这里先用样式位把带边框的窗口排除掉，再要求几乎完全覆盖显示器。
    """
    if not hwnd or not is_visible(hwnd) or is_minimized(hwnd):
        return False
    cls = window_class(hwnd)
    if cls in SHELL_CLASSES:
        return False

    style = window_style(hwnd)
    # 最大化窗口有 WS_MAXIMIZE 标志：它不是全屏，企鹅不该隐藏
    if style & WS_MAXIMIZE:
        return False
    # 带标题栏或可调边框的是普通窗口（最大化/拖大），也不是全屏。
    # 真全屏会把这些位全部去掉。
    if style & (WS_CAPTION | WS_THICKFRAME | WS_BORDER):
        return False

    rect = window_rect(hwnd)
    mon = monitor_rect(hwnd)
    if rect is None or mon is None:
        return False
    w = rect[2] - rect[0]
    h = rect[3] - rect[1]
    mw = mon[2] - mon[0]
    mh = mon[3] - mon[1]
    if mw <= 0 or mh <= 0:
        return False
    # 真全屏基本是像素级铺满，留 1% 容差应对 DPI 取整
    return w >= mw * 0.99 and h >= mh * 0.99


# ---------------------------------------------------------------- 锁屏
def input_desktop_available() -> bool:
    """锁屏/切换用户时 OpenInputDesktop 会失败。作为锁屏的辅证。"""
    h = user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
    if not h:
        return False
    user32.CloseDesktop(h)
    return True


def register_session_notification(hwnd: int) -> bool:
    if wtsapi32 is None or not hwnd:
        return False
    return bool(
        wtsapi32.WTSRegisterSessionNotification(
            wintypes.HWND(hwnd), NOTIFY_FOR_THIS_SESSION
        )
    )


def unregister_session_notification(hwnd: int) -> None:
    if wtsapi32 is None or not hwnd:
        return
    wtsapi32.WTSUnRegisterSessionNotification(wintypes.HWND(hwnd))


# ---------------------------------------------------------------- 窗口样式（点击穿透降级方案）
def get_ex_style(hwnd: int) -> int:
    return int(user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE))


def set_ex_transparent(hwnd: int, on: bool) -> bool:
    """直接改 WS_EX_TRANSPARENT 位。不经 Qt 的 setWindowFlag，避免窗口重建闪烁。"""
    if not hwnd:
        return False
    style = get_ex_style(hwnd)
    new = (style | WS_EX_TRANSPARENT) if on else (style & ~WS_EX_TRANSPARENT)
    if new == style:
        return True
    user32.SetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE, ctypes.c_long(new))
    return True


def send_nchittest(hwnd: int, screen_x: int, screen_y: int) -> int:
    """自己发一条 WM_NCHITTEST，用于启动自检（返回 -1 表示穿透生效）。"""
    if not hwnd:
        return 0
    lparam = ((screen_y & 0xFFFF) << 16) | (screen_x & 0xFFFF)
    res = user32.SendMessageW(
        wintypes.HWND(hwnd), WM_NCHITTEST, 0, wintypes.LPARAM(lparam)
    )
    # 结果按有符号 16 位解释
    return ctypes.c_short(res & 0xFFFF).value if res is not None else 0
