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
