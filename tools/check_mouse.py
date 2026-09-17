"""用真实的系统鼠标事件验证企鹅窗口的拖动 / 单击 / 右键菜单。

    .venv\\Scripts\\python.exe tools\\check_mouse.py

为什么不用 Qt 的 QTest 造事件：那条路会绕过 WM_NCHITTEST 命中测试，
而"窗口到底收不收得到鼠标按下"恰恰是这里最容易出问题的地方。
本脚本用 ctypes 调 SendInput 发真实输入，走完整链路。

跑完会把鼠标还原到原位（也会把企鹅摆回默认位置）。

注意：如果这时真人在用这台电脑，会偶发干扰（测出来的"单击"可能落到别的窗口上）。
所以拿它做结论时要连跑两次，两次都对才当通过。
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QCursor, QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

# ---------------------------------------------------------------- SendInput
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32 = ctypes.WinDLL("user32", use_last_error=True)


def _send(flags: int, x: int = 0, y: int = 0) -> None:
    inp = INPUT(type=INPUT_MOUSE)
    inp.u.mi = MOUSEINPUT(x, y, 0, flags, 0, None)
    if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) != 1:
        raise OSError(f"SendInput 失败: {ctypes.get_last_error()}")


def logical_to_physical(x: float, y: float) -> tuple[int, int]:
    """Qt 逻辑坐标 -> 系统物理坐标。

    又是一次坐标空间陷阱：SendInput 用的是**物理**像素，而窗口位置、
    控件几何是 Qt **逻辑**像素。这台机器 dpr=1.25，直接拿逻辑坐标发输入，
    光标会落在目标左上方 20% 的位置——看起来就像"点了没反应"。
    """
    scr = QGuiApplication.primaryScreen()
    dpr = scr.devicePixelRatio() if scr else 1.0
    return int(round(x * dpr)), int(round(y * dpr))


def move_to(x: int, y: int) -> None:
    """按绝对坐标（物理像素）移动（0..65535 归一化到整个虚拟屏）。"""
    vx = user32.GetSystemMetrics(76)
    vy = user32.GetSystemMetrics(77)
    vw = user32.GetSystemMetrics(78)
    vh = user32.GetSystemMetrics(79)
    ax = int((x - vx) * 65535 / max(1, vw - 1))
    ay = int((y - vy) * 65535 / max(1, vh - 1))
    _send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax, ay)


def click(x: int, y: int, right: bool = False) -> None:
    down = MOUSEEVENTF_RIGHTDOWN if right else MOUSEEVENTF_LEFTDOWN
    up = MOUSEEVENTF_RIGHTUP if right else MOUSEEVENTF_LEFTUP
    move_to(x, y)
    time.sleep(0.08)
    _send(down)
    time.sleep(0.08)
    _send(up)
    time.sleep(0.25)


def drag(x0: int, y0: int, x1: int, y1: int, steps: int = 14) -> None:
    move_to(x0, y0)
    time.sleep(0.10)
    _send(MOUSEEVENTF_LEFTDOWN)
    time.sleep(0.10)
    for i in range(1, steps + 1):
        move_to(int(x0 + (x1 - x0) * i / steps), int(y0 + (y1 - y0) * i / steps))
        time.sleep(0.022)
    time.sleep(0.10)
    _send(MOUSEEVENTF_LEFTUP)
    time.sleep(0.35)


def main() -> int:
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("桌面企鹅")
    app.setQuitOnLastWindowClosed(False)

    import main as app_main

    ok, _srv = app_main.acquire_single_instance()
    if not ok:
        print("已有实例在运行，先关掉再跑这个脚本")
        return 1

    pet_app = app_main.DeskPet(app)
    pet_app.start()

    # 记录信号触发次数（用真实回调，只是裹一层计数）
    hits = {"click": 0, "drag_start": 0, "drag_end": 0}
    orig_click = pet_app._on_pet_clicked
    orig_start = pet_app._on_drag_started
    orig_end = pet_app._on_drag_finished

    def on_click():
        hits["click"] += 1
        orig_click()

    def on_start():
        hits["drag_start"] += 1
        orig_start()

    def on_end():
        hits["drag_end"] += 1
        orig_end()

    pet_app.pet.clicked.disconnect()
    pet_app.pet.drag_started.disconnect()
    pet_app.pet.drag_finished.disconnect()
    pet_app.pet.clicked.connect(on_click)
    pet_app.pet.drag_started.connect(on_start)
    pet_app.pet.drag_finished.connect(lambda _p: on_end())

    def pump(seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.008)

    original = QCursor.pos()
    pump(1.2)
    win = pet_app.pet

    # 先把企鹅的位置重置成默认值再测。
    # 否则上次测试拖动后的位置会留在 config["pos"] 里，这一轮的"窗口中心"
    # 就落在别处（可能还在别的窗口/气泡底下），测量结果每次都不一样。
    from app.config import config as _cfg

    _cfg.set("pos", None, save=True)
    scr = QGuiApplication.primaryScreen()
    if scr is not None:
        av = scr.availableGeometry()
        win.move(av.right() - win.width() - 60, av.bottom() - win.height() + 6)
    pump(0.8)

    u = ctypes.WinDLL("user32")
    rect = wintypes.RECT()
    u.GetWindowRect(int(win.winId()), ctypes.byref(rect))

    # 点击点直接从**窗口的物理矩形**算，不经过 Qt 逻辑坐标、也不依赖当前姿势。
    # 企鹅身体大致在窗口高度的 62% 处，横向居中——这一段一定是实心的。
    bx = (rect.left + rect.right) // 2
    by = rect.top + int((rect.bottom - rect.top) * 0.62)

    print("=" * 66)
    print("企鹅鼠标交互诊断（真实 SendInput 事件）")
    print("=" * 66)
    print(f"窗口逻辑位置 {win.pos().x()},{win.pos().y()}  "
          f"物理矩形 {rect.left},{rect.top}..{rect.right},{rect.bottom}")
    print(f"点击点（物理） {bx},{by}")
    print(f"WM_NCHITTEST = {_nchittest(win, bx, by)}"
          f"  (1=HTCLIENT 可点, -1=HTTRANSPARENT 穿透)")
    print(f"WindowFromPoint 是企鹅窗口 = "
          f"{_window_from_point(bx, by) == int(win.winId())}")

    # 注意：SendInput 发出去的输入，事件到达窗口会比 pump() 晚一拍
    # （系统输入队列是异步的），所以**不要逐步清零计数器**再按步骤归属——
    # 那样测出来的结果会整体错位一步。这里统计全程累计值。
    click(bx, by)
    pump(0.8)
    t_click = dict(hits)
    print(f"\n[单击] clicked={t_click['click']}")

    before = win.pos()
    drag(bx, by, max(60, bx - 260), max(60, by - 200))
    pump(1.2)
    moved = win.pos() != before
    t_drag = {k: hits[k] - t_click[k] for k in hits}
    print(f"[拖动] drag_started={t_drag['drag_start']} drag_finished={t_drag['drag_end']} "
          f"clicked={t_drag['click']}  窗口移动={moved}")
    print(f"       窗口 {before.x()},{before.y()} -> {win.pos().x()},{win.pos().y()}")

    # ---- 3) 右键 ----
    # 拖动把窗口搬到了左上角（还会吸附到屏幕边），先把它摆回一个正常位置再测。
    # 这也是真实场景：用户不会拖着企鹅去点右键。
    if scr is not None:
        av = scr.availableGeometry()
        win.move(av.right() - win.width() - 60, av.bottom() - win.height() + 6)
    pump(1.0)
    u.GetWindowRect(int(win.winId()), ctypes.byref(rect))
    bx = (rect.left + rect.right) // 2
    by = rect.top + int((rect.bottom - rect.top) * 0.62)
    print(f"[右键] 复位后窗口逻辑位置 {win.pos().x()},{win.pos().y()}  点击点 {bx},{by}")
    # 数 menu_requested（右键的真正入口在 mouseReleaseEvent 里，
    # 这条 WS_POPUP 窗口收不到系统派的 WM_CONTEXTMENU），再确认菜单真的弹出来了。
    _CTX["n"] = 0
    pet_app.pet.menu_requested.connect(lambda: _CTX.__setitem__("n", _CTX["n"] + 1))
    click(bx, by, right=True)
    pump(0.6)
    shown = pet_app.tray.menu.isVisible()
    print(f"[右键] 菜单请求={_CTX['n']} 次  菜单已弹出={shown}")
    if shown:
        pet_app.tray.menu.close()
        pump(0.2)

    ox, oy = logical_to_physical(original.x(), original.y())
    move_to(ox, oy)
    pump(0.3)
    QCursor.setPos(original)
    click_ok = hits["click"] >= 1
    drag_ok = moved and hits["drag_start"] >= 1 and hits["drag_end"] >= 1
    menu_shown = bool(shown)
    verdict = [
        f"单击触发动作: {'通过' if click_ok else '失败'}",
        f"按住可拖动: {'通过' if drag_ok else '失败'}",
        f"右键弹菜单: {'通过' if _CTX['n'] > 0 and menu_shown else '失败'}",
    ]
    print(chr(10) + "结果汇总:")
    for v in verdict:
        print("  " + v)
    return 0 if all("通过" in v for v in verdict) else 1


_CTX = {"n": 0}


def _window_from_point(x: int, y: int) -> int:
    from monitor import winapi

    return int(winapi.window_from_point(x, y) or 0)


def _nchittest(win, x: int, y: int) -> int:
    from monitor import winapi

    hwnd = int(win.winId())
    return winapi.send_nchittest(hwnd, x, y)


if __name__ == "__main__":
    sys.exit(main())
