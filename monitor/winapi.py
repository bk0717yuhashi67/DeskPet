"""Windows 原生 API 封装（纯 ctypes，不依赖 pywin32）。

只暴露本项目真正需要的能力：
  * 前台窗口 / 窗口标题 / 进程 PID
  * 空闲时长（GetLastInputInfo，零成本）
  * 光标位置
  * 鼠标原始输入（Raw Input，替代全局钩子）
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
WM_INPUT = 0x00FF

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

# ---- Raw Input：鼠标事件走"消息投递"而不是钩子，不占用输入通路 ----
# 这是屏幕取词、游戏里读鼠标的标准做法：SetWindowsHookEx(WH_MOUSE_LL)
# 要求系统把**每一个**鼠标事件同步交给我们的线程（事件率高时拖慢光标），
# 而 Raw Input 只是把事件投递到窗口消息队列，处理慢一点也不会卡住输入。
RIDEV_REMOVE = 0x00000001
RIDEV_INPUTSINK = 0x00000100      # 窗口不在前台也照收（桌宠必备）
RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
HID_USAGE_PAGE_GENERIC = 0x01
HID_USAGE_GENERIC_MOUSE = 0x02

RI_MOUSE_LEFT_BUTTON_DOWN = 0x0001
RI_MOUSE_LEFT_BUTTON_UP = 0x0002
RI_MOUSE_RIGHT_BUTTON_DOWN = 0x0004
RI_MOUSE_RIGHT_BUTTON_UP = 0x0008
RI_MOUSE_MIDDLE_BUTTON_DOWN = 0x0010
RI_MOUSE_MIDDLE_BUTTON_UP = 0x0020
RI_MOUSE_BUTTON_4_DOWN = 0x0040
RI_MOUSE_BUTTON_4_UP = 0x0080
RI_MOUSE_BUTTON_5_DOWN = 0x0100
RI_MOUSE_BUTTON_5_UP = 0x0200
RI_MOUSE_WHEEL = 0x0400

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


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class RAWMOUSE(ctypes.Structure):
    """注意 usFlags 后面的 union：原生布局是
    `union { ULONG ulButtons; struct { USHORT usButtonFlags; USHORT usButtonData; }; }`，
    这里按等价的 DWORD 读，按钮位取低 16 位（见 raw_mouse_flags）。
    """
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("ulButtons", wintypes.DWORD),
        ("ulRawButtons", wintypes.DWORD),
        ("lLastX", wintypes.LONG),
        ("lLastY", wintypes.LONG),
        ("ulExtraInformation", wintypes.DWORD),
    ]


class RAWINPUT(ctypes.Structure):
    _fields_ = [
        ("header", RAWINPUTHEADER),
        ("mouse", RAWMOUSE),
    ]


# 复用同一个缓冲：鼠标事件最频繁时可达 1000 次/秒，不值得每次分配
_RAW_BUF = RAWINPUT()


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
# 必须用 DWORD（无符号）读取样式：32 位样式里高位（如 WS_POPUP 0x80000000）
# 会让 c_long 变成负数（实测桌面窗口返回 -1778384896）。
# 按位与时虽仍能工作，但改成无符号更直观、也避免后续误用。
user32.GetWindowLongW.restype = wintypes.DWORD
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.RegisterRawInputDevices.argtypes = [
    ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT
]
user32.RegisterRawInputDevices.restype = wintypes.BOOL
user32.GetRawInputData.argtypes = [
    wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
    ctypes.POINTER(wintypes.UINT), wintypes.UINT,
]
user32.GetRawInputData.restype = wintypes.UINT
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

    判据有三条，缺一不可：

    1. 可见、未最小化，类名不在外壳白名单。
    2. **没有标题栏与边框**（无 `WS_CAPTION` / `WS_THICKFRAME` / `WS_BORDER`）。
       这是区分"全屏"与"普通最大化窗口"的关键：真全屏会移除这些装饰。
    3. 窗口矩形 ≥ 显示器的 99%。

    ### 为什么不看 WS_MAXIMIZE

    最初的修复曾加过"有 `WS_MAXIMIZE` 就返回 False"，结果**浏览器视频全屏被漏判**。
    2026-09-21 实测（Edge 播放 bilibili，视频切全屏）抓到的窗口形状：

        rect=(0,0,1536,864)  mon=(0,0,1536,864)   ->  ratio 1.0000 x 1.0000
        style=0x170B0000  MAX=True CAPTION=False THICK=False BORDER=False

    即 Chromium 系在进入视频全屏后，**仍保留 `WS_MAXIMIZE` 位**，
    同时把边框全部去掉、窗口精确铺满显示器。
    而普通"最大化窗口"的形状是（Edge 最大化时实测）：

        rect=(-7,-7,1543,831)  ->  比显示器更大（Win10+ 最大化会外扩约 7px）
        style=0x17CF0000  MAX=True CAPTION=True THICK=True BORDER=True

    两者都由 `WS_MAXIMIZE=True` 开头，所以**只能靠"有没有边框"来区分**：
    最大化窗口有边框（第 2 条挡掉），全屏窗口没有（放行）。
    因此这里刻意**不检查 `WS_MAXIMIZE`**。

    ### 坐标系注意

    `window_rect` 与 `monitor_rect` 取到的都是**同一空间**（本机实测均为
    1536x864 逻辑像素，与 GetSystemMetrics 一致），可以直接相除；
    Qt 侧 `main._on_flags` 只接收布尔结果，不涉及坐标换算。
    """
    if not hwnd or not is_visible(hwnd) or is_minimized(hwnd):
        return False
    cls = window_class(hwnd)
    if cls in SHELL_CLASSES:
        return False
    # 桌面窗口的类名是数字原子 '#32769'，不会出现在 SHELL_CLASSES 里，
    # 但它铺满整屏且无边框，会被误判成全屏 -> 单独挡掉。
    if cls.startswith("#3276"):
        return False

    style = window_style(hwnd)
    # 带标题栏或可调边框的是普通窗口（含最大化），不是全屏。
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


# ---------------------------------------------------------------- Raw Input（鼠标）
def register_raw_mouse(hwnd: int, enable: bool = True) -> bool:
    """注册 / 注销鼠标的原始输入。

    `RIDEV_INPUTSINK` 让窗口在**不是前台**时也能收到 `WM_INPUT` —— 这正是
    桌宠需要的。注销时必须带 `RIDEV_REMOVE` 且目标窗口为 NULL（MSDN 要求）。
    """
    if enable:
        if not hwnd:
            return False
        rid = RAWINPUTDEVICE(
            HID_USAGE_PAGE_GENERIC, HID_USAGE_GENERIC_MOUSE,
            RIDEV_INPUTSINK, wintypes.HWND(hwnd),
        )
    else:
        rid = RAWINPUTDEVICE(
            HID_USAGE_PAGE_GENERIC, HID_USAGE_GENERIC_MOUSE, RIDEV_REMOVE, None
        )
    return bool(
        user32.RegisterRawInputDevices(
            ctypes.byref(rid), 1, ctypes.sizeof(RAWINPUTDEVICE)
        )
    )


def raw_mouse_input(lparam: int) -> tuple[int, int, int] | None:
    """解析 `WM_INPUT` 的 lParam，返回 `(dx, dy, 按钮位)`。

    `dx/dy` 是**原始增量**（鼠标计数单位，非屏幕像素），负值表示反向移动；
    某次事件里增量可能为 0（例如只按了键）。按钮位见 `RI_MOUSE_*` 常量。
    非鼠标事件或解析失败返回 None。
    """
    size = wintypes.UINT(ctypes.sizeof(RAWINPUT))
    got = user32.GetRawInputData(
        wintypes.HANDLE(lparam), RID_INPUT,
        ctypes.byref(_RAW_BUF), ctypes.byref(size),
        ctypes.sizeof(RAWINPUTHEADER),
    )
    if got <= 0 or _RAW_BUF.header.dwType != RIM_TYPEMOUSE:
        return None
    m = _RAW_BUF.mouse
    return (int(m.lLastX), int(m.lLastY), int(m.ulButtons) & 0xFFFF)
