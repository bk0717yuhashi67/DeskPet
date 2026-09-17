"""创建 Windows 快捷方式（.lnk），不依赖 pywin32。

直接通过 ctypes 调 COM 的 IShellLinkW + IPersistFile。
这样就有了一个"双击即启动、不留控制台黑框、也不依赖 WSH"的入口，
比 .bat / .vbs 都更省事（.bat 会闪一下黑框，.vbs 依赖 Windows Script Host 没被安全软件关掉）。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import POINTER, byref, c_void_p, c_wchar_p
from pathlib import Path

from . import logging_setup
from .paths import ASSETS_DIR, ROOT

log = logging_setup.get("shortcut")

CLSID_ShellLink = "{00021401-0000-0000-C000-000000000046}"
IID_IShellLinkW = "{000214F9-0000-0000-C000-000000000046}"
IID_IPersistFile = "{0000010B-0000-0000-C000-000000000046}"

COINIT_APARTMENTTHREADED = 0x2
CLSCTX_INPROC_SERVER = 0x1
SW_SHOWNORMAL = 1
SW_SHOWMINNOACTIVE = 7

# IShellLinkW 虚表下标（IUnknown 占 0..2）。
#
# 坑（踩过）：这批方法在 vtable 里**不是按字母序**，而是按"get/set 成对、功能分组"排的。
# 想当然地按字母排会整体错位——表现是调用"成功"（返回值被忽略）但写出来的 .lnk 内容错乱，
# 比如 SetPath 实际打到了 SetIconLocation，于是目标路径变成了解释器路径。
# 正确的顺序来自 shlobj.h 的 IShellLinkW 声明：
#   GetPath 3, GetIDList 4, SetIDList 5,
#   GetDescription 6, SetDescription 7,
#   GetWorkingDirectory 8, SetWorkingDirectory 9,
#   GetArguments 10, SetArguments 11,
#   GetHotkey 12, SetHotkey 13,
#   GetShowCmd 14, SetShowCmd 15,
#   GetIconLocation 16, SetIconLocation 17,
#   SetRelativePath 18, Resolve 19, SetPath 20
VT_GET_PATH = 3
VT_GET_DESCRIPTION = 6
VT_SET_DESCRIPTION = 7
VT_GET_WORKING_DIR = 8
VT_SET_WORKING_DIR = 9
VT_GET_ARGUMENTS = 10
VT_SET_ARGUMENTS = 11
VT_GET_SHOW_CMD = 14
VT_SET_SHOW_CMD = 15
VT_GET_ICON = 16
VT_SET_ICON = 17
VT_SET_PATH = 20

# IPersistFile（同样是 IUnknown 占 0..2，之后按声明顺序）
VT_QUERY_INTERFACE = 0
VT_LOAD = 5
VT_SAVE = 6


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, s: str) -> "GUID":
        s = s.strip("{}")
        p = s.split("-")
        g = cls()
        g.Data1 = int(p[0], 16)
        g.Data2 = int(p[1], 16)
        g.Data3 = int(p[2], 16)
        tail = p[3] + p[4]
        for i in range(8):
            g.Data4[i] = int(tail[i * 2:i * 2 + 2], 16)
        return g


def _vtable_call(ptr: c_void_p, index: int, argtypes: tuple, *args, restype=ctypes.c_long):
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return proto(vtbl[index])(ptr, *args)


def _release(ptr: c_void_p) -> None:
    try:
        _vtable_call(ptr, 2, (), restype=ctypes.c_ulong)
    except Exception:
        pass


def create_shortcut(
    lnk_path: str | Path,
    target: str | Path,
    arguments: str = "",
    working_dir: str | Path | None = None,
    icon: str | Path | None = None,
    description: str = "",
    minimized: bool = False,
) -> bool:
    """在 lnk_path 处创建指向 target 的快捷方式。成功返回 True。"""
    lnk_path = str(Path(lnk_path))
    target = str(Path(target))

    ole32 = ctypes.OleDLL("ole32")
    ole32.CoInitialize(None)
    punk = c_void_p()
    try:
        clsid = GUID.parse(CLSID_ShellLink)
        iid = GUID.parse(IID_IShellLinkW)
        hr = ole32.CoCreateInstance(
            byref(clsid), None, CLSCTX_INPROC_SERVER, byref(iid), byref(punk)
        )
        if hr != 0 or not punk:
            log.warning("CoCreateInstance(ShellLink) 失败 hr=0x%08X", hr & 0xFFFFFFFF)
            return False

        _vtable_call(punk, VT_SET_PATH, (c_wchar_p,), c_wchar_p(target))
        if arguments:
            _vtable_call(punk, VT_SET_ARGUMENTS, (c_wchar_p,), c_wchar_p(arguments))
        if working_dir:
            _vtable_call(punk, VT_SET_WORKING_DIR, (c_wchar_p,), c_wchar_p(str(working_dir)))
        if description:
            _vtable_call(punk, VT_SET_DESCRIPTION, (c_wchar_p,), c_wchar_p(description))
        if icon:
            _vtable_call(
                punk, VT_SET_ICON, (c_wchar_p, ctypes.c_int), c_wchar_p(str(icon)), 0
            )
        _vtable_call(
            punk, VT_SET_SHOW_CMD, (ctypes.c_int,),
            SW_SHOWMINNOACTIVE if minimized else SW_SHOWNORMAL,
        )

        # 拿到 IPersistFile 才能落盘
        pf = c_void_p()
        iid_pf = GUID.parse(IID_IPersistFile)
        hr = _vtable_call(
            punk, VT_QUERY_INTERFACE, (POINTER(GUID), POINTER(c_void_p)),
            byref(iid_pf), byref(pf), restype=ctypes.c_long,
        )
        if hr != 0 or not pf:
            log.warning("QueryInterface(IPersistFile) 失败 hr=0x%08X", hr & 0xFFFFFFFF)
            return False

        Path(lnk_path).parent.mkdir(parents=True, exist_ok=True)
        hr = _vtable_call(
            pf, VT_SAVE, (c_wchar_p, ctypes.c_int), c_wchar_p(lnk_path), 1
        )
        _release(pf)
        if hr != 0:
            log.warning("IPersistFile::Save 失败 hr=0x%08X", hr & 0xFFFFFFFF)
            return False
        log.info("已创建快捷方式 %s -> %s", lnk_path, target)
        return True
    except Exception as exc:
        log.warning("创建快捷方式异常: %s", exc)
        return False
    finally:
        _release(punk)
        try:
            ole32.CoUninitialize()
        except Exception:
            pass


def _load_lnk(lnk_path: str | Path):
    """打开一个 .lnk，返回 (IShellLinkW 指针, IPersistFile 指针)。失败返回 (None, None)。"""
    ole32 = ctypes.OleDLL("ole32")
    ole32.CoInitialize(None)
    punk = c_void_p()
    clsid = GUID.parse(CLSID_ShellLink)
    iid = GUID.parse(IID_IShellLinkW)
    hr = ole32.CoCreateInstance(
        byref(clsid), None, CLSCTX_INPROC_SERVER, byref(iid), byref(punk)
    )
    if hr != 0 or not punk:
        return None, None
    pf = c_void_p()
    iid_pf = GUID.parse(IID_IPersistFile)
    hr = _vtable_call(
        punk, VT_QUERY_INTERFACE, (POINTER(GUID), POINTER(c_void_p)),
        byref(iid_pf), byref(pf), restype=ctypes.c_long,
    )
    if hr != 0 or not pf:
        _release(punk)
        return None, None
    hr = _vtable_call(pf, VT_LOAD, (c_wchar_p, ctypes.c_int), c_wchar_p(str(lnk_path)), 0)
    if hr != 0:
        _release(pf)
        _release(punk)
        return None, None
    return punk, pf


def read_shortcut(lnk_path: str | Path) -> dict | None:
    """读回快捷方式的内容，用于自检。读不到返回 None。"""
    punk, pf = _load_lnk(lnk_path)
    if punk is None:
        return None
    try:
        def text(vt: int, size: int = 1024) -> str:
            buf = ctypes.create_unicode_buffer(size)
            _vtable_call(punk, vt, (c_wchar_p, ctypes.c_int), buf, size)
            return buf.value

        # 注意 GetPath 是 4 个参数（多一个 WIN32_FIND_DATAW* 和 fFlags）。
        # 少传参数会让被调方从栈上读到垃圾指针，直接 access violation。
        path_buf = ctypes.create_unicode_buffer(1024)
        _vtable_call(
            punk, VT_GET_PATH,
            (c_wchar_p, ctypes.c_int, c_void_p, ctypes.c_ulong),
            path_buf, 1024, None, 0,
        )

        icon_buf = ctypes.create_unicode_buffer(1024)
        icon_idx = ctypes.c_int()
        _vtable_call(
            punk, VT_GET_ICON,
            (c_wchar_p, ctypes.c_int, POINTER(ctypes.c_int)),
            icon_buf, 1024, byref(icon_idx),
        )
        return {
            "target": path_buf.value,
            "arguments": text(VT_GET_ARGUMENTS),
            "working_dir": text(VT_GET_WORKING_DIR),
            "description": text(VT_GET_DESCRIPTION),
            "icon": icon_buf.value,
        }
    except Exception as exc:
        log.warning("读取快捷方式失败: %s", exc)
        return None
    finally:
        _release(pf)
        _release(punk)


def inspect_shortcut(lnk_path: str | Path, expect: dict[str, str]) -> dict[str, bool]:
    """直接扫描 .lnk 文件字节来做自检，不走 COM。

    .lnk 会把目标路径、参数、图标路径、描述等以 UTF-16LE 字符串原样存在文件里，
    所以扫字节就足以确认"内容确实写对了"，而且不会因为 COM 调用出错把进程搞崩。
    """
    try:
        data = Path(lnk_path).read_bytes()
    except OSError:
        return {k: False for k in expect}

    out: dict[str, bool] = {}
    for key, needle in expect.items():
        variants = [
            needle.encode("utf-16-le"),
            needle.replace("\\", "/").encode("utf-16-le"),
        ]
        out[key] = any(v in data for v in variants)
    return out


def expected_content() -> dict[str, str]:
    """桌面/项目快捷方式里应该出现的关键字符串。"""
    exe, args, cwd = launcher_targets()
    return {
        "解释器": Path(exe).name,
        "主程序": "launch.py",
        "工作目录": cwd,
        "图标": "penguin.ico",
        "描述": "桌面企鹅",
    }


def notify_shell_icon_changed() -> bool:
    """通知 shell：图标资源变了，请重建图标缓存。

    `SHChangeNotify(SHCNE_ASSOCCHANGED)` 是官方给的做法——安装程序改了
    文件关联/图标之后都要调它。它会让资源管理器**丢弃并重建**图标缓存，
    比去动 explorer 进程安全得多。
    """
    try:
        shell32 = ctypes.WinDLL("shell32")
        shell32.SHChangeNotify.argtypes = [
            ctypes.c_long, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p
        ]
        shell32.SHChangeNotify.restype = None
        shell32.SHChangeNotify(0x08000000, 0, None, None)      # SHCNE_ASSOCCHANGED
        return True
    except Exception as exc:
        log.debug("SHChangeNotify 失败: %s", exc)
        return False


def refresh_icon_cache() -> bool:
    """让 Windows 重新读取 assets/penguin.ico。

    改了 .ico 之后资源管理器还显示旧图标，是**图标缓存**造成的：
    缓存条目按图标文件路径命中，原地改写文件并不会让它失效。
    所以这里做两件事，能中一件就够：

      1) `SHChangeNotify(SHCNE_ASSOCCHANGED)` —— 让 shell 重建图标缓存；
      2) `ie4uinit.exe -show` —— 官方提供的"刷新图标"入口（Win10+）。

    两者都失败也不影响使用：重启资源管理器或重启系统后同样会更新。
    """
    import subprocess

    ok = notify_shell_icon_changed()
    try:
        subprocess.run(
            ["ie4uinit.exe", "-show"],
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        ok = True
    except Exception as exc:
        log.debug("ie4uinit 失败: %s", exc)
    return ok


def recreate_shortcuts() -> list[Path]:
    """重建两处快捷方式。新建的 .lnk 会重新去取一次图标。

    创建失败要**记录下来**：之前这里是静默吞异常，导致桌面快捷方式
    实际上没建成功也没人知道。
    """
    out = []
    for fn in (create_project_shortcut, create_desktop_shortcut):
        try:
            p = fn()
        except Exception as exc:
            log.warning("创建快捷方式失败（%s）: %s", fn.__name__, exc)
            p = None
        if p is None:
            log.warning("快捷方式没建成：%s", fn.__name__)
            continue
        if not p.exists():
            log.warning("快捷方式返回成功但文件不存在：%s", p)
            continue
        out.append(p)
    return out


def desktop_dir() -> Path | None:
    """桌面目录。OneDrive 重定向过的系统也能拿到真实路径。"""
    for probe in (
        lambda: Path(os.environ["USERPROFILE"]) / "Desktop",
        lambda: Path(os.environ["OneDrive"]) / "桌面",
        lambda: Path(os.environ["OneDrive"]) / "Desktop",
        lambda: Path(os.environ["USERPROFILE"]) / "桌面",
    ):
        try:
            p = probe()
            if p.is_dir():
                return p
        except Exception:
            continue
    return None


def launcher_targets() -> tuple[str, str, str]:
    """返回 (解释器, 参数, 工作目录)。用 pythonw 可以不留黑框。"""
    import sys

    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    if not pyw.exists():
        pyw = exe
    return str(pyw), f'"{ROOT / "launch.py"}"', str(ROOT)


def create_desktop_shortcut(name: str = "桌面企鹅") -> Path | None:
    d = desktop_dir()
    if d is None:
        return None
    exe, args, cwd = launcher_targets()
    icon = ASSETS_DIR / "penguin.ico"
    lnk = d / f"{name}.lnk"
    ok = create_shortcut(
        lnk, exe, arguments=args, working_dir=cwd,
        icon=icon if icon.exists() else None, description="桌面企鹅",
    )
    return lnk if ok else None


def create_project_shortcut(name: str = "启动企鹅") -> Path | None:
    exe, args, cwd = launcher_targets()
    icon = ASSETS_DIR / "penguin.ico"
    lnk = ROOT / f"{name}.lnk"
    ok = create_shortcut(
        lnk, exe, arguments=args, working_dir=cwd,
        icon=icon if icon.exists() else None, description="桌面企鹅",
    )
    return lnk if ok else None
