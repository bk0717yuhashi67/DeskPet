"""统一路径常量。所有运行时数据都落在项目目录下，方便用户随时找到和清理。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LOG_DIR = DATA_DIR / "logs"
DB_PATH = DATA_DIR / "pet.db"
CONFIG_PATH = DATA_DIR / "config.json"
ASSETS_DIR = ROOT / "assets"
SOUND_DIR = ASSETS_DIR / "sounds"


def ensure_dirs() -> None:
    for d in (DATA_DIR, LOG_DIR, SOUND_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def launch_command() -> str:
    """开机自启要写的命令行。打包后写 exe，源码运行时写 python + main.py。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pyw = _pythonw()
    return f'"{pyw}" "{ROOT / "main.py"}"'


# ----------------------------------------------------------------------
# 开机自启（注册表 HKCU\...\Run）
#
# 这条命令里存的是**绝对路径快照**，所以项目一旦被移动/改名，
# 注册表里的路径就失效了 —— 开机时 Windows 执行不存在的路径会静默失败，
# 而 config.json 里 autostart 还是 true，界面上看起来"已开启"，实际不起作用。
# 因此启动时要主动自检一次，发现失效就改写成当前路径。
# ----------------------------------------------------------------------
AUTOSTART_KEY = "DeskPetPenguin"
AUTOSTART_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_value() -> str | None:
    """读注册表里的自启命令；没配置或读不到返回 None。"""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, AUTOSTART_KEY)
            return val or None
    except Exception:
        return None


def _command_paths_exist(cmdline: str) -> bool:
    """命令里用引号括起来的路径是否都真实存在。"""
    import re

    paths = re.findall(r'"([^"]+)"', cmdline)
    if not paths:
        return False
    return all(os.path.exists(p) for p in paths)


def autostart_is_valid() -> bool:
    """自启已配置，且命令里的路径都还在。

    注意不能只看"注册表值非空"：项目搬家后值仍然非空，
    但路径已经不存在了。只看非空会得到"已开启"的假象。
    """
    val = autostart_value()
    if not val:
        return False
    return _command_paths_exist(val)


def sync_autostart() -> str:
    """自启自检：已开启但路径失效（项目搬家）时，自动改写成当前路径。

    返回：
      "off"      未配置自启（用户本来就没开，不动）
      "ok"       已配置且路径有效
      "repaired" 已配置但路径失效，已改写为当前路径
      "failed"   已配置但路径失效，改写失败
    """
    val = autostart_value()
    if not val:
        return "off"
    if _command_paths_exist(val):
        return "ok"
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as k:
            winreg.SetValueEx(k, AUTOSTART_KEY, 0, winreg.REG_SZ, launch_command())
        return "repaired"
    except Exception:
        return "failed"


def set_autostart(on: bool) -> bool:
    """开启/关闭开机自启。成功返回 True。"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as k:
            if on:
                winreg.SetValueEx(k, AUTOSTART_KEY, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(k, AUTOSTART_KEY)
                except FileNotFoundError:
                    pass
        return True
    except Exception as exc:
        try:
            from . import logging_setup

            logging_setup.get("settings").warning("设置开机自启失败: %s", exc)
        except Exception:
            pass
        return False


def _pythonw() -> str:
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    if cand.exists():
        return str(cand)
    return str(exe)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def open_in_explorer(path: Path) -> None:
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        import subprocess

        try:
            subprocess.Popen(["explorer", str(path)])
        except Exception:
            pass
