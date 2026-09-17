"""桌面企鹅 · 启动器。

它存在的唯一理由：把"能不能跑起来"这件事变得**确定且可诊断**。

  1. 定位虚拟环境；没有就创建
  2. 校验依赖；缺失先联网装，失败再走本地 wheel 离线装
  3. 用 pythonw 启动主程序（不留控制台黑框）
  4. 启动后做存活检查：进程立刻死掉就把真实错误抓出来弹窗告诉用户

为什么要第 4 步：pythonw 没有控制台，程序如果启动即崩，用户看到的现象就是
"双击了但什么都没发生"，完全无从下手。这里把错误显式呈现出来。

注意：start.bat / start.vbs 故意只写 ASCII，中文一律走这里的 Unicode 弹窗。
因为 cmd.exe 会用系统代码页（中文 Windows 上是 GBK）解析 .bat，
文件里若有 UTF-8 中文字节会被错解并吃掉后面的引号/换行，直接破坏脚本结构。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
VENV_PY = VENV_DIR / "Scripts" / "python.exe"
VENV_PYW = VENV_DIR / "Scripts" / "pythonw.exe"
MAIN = ROOT / "main.py"
LOG_DIR = ROOT / "data" / "logs"
LAUNCH_LOG = LOG_DIR / "launch.log"
APP_ERR_LOG = LOG_DIR / "launch_stderr.log"

# 启动后等多久判断"是否成功起来了"
ALIVE_TIMEOUT = 12.0
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

CONSOLE = "--console" in sys.argv


# ----------------------------------------------------------------------
# 输出与报错
# ----------------------------------------------------------------------
def say(msg: str) -> None:
    """往控制台打一句。控制台编码可能是 cp936，编码失败不要炸。"""
    try:
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()
    except Exception:
        try:
            sys.stdout.buffer.write((msg + "\n").encode("utf-8", "replace"))
            sys.stdout.buffer.flush()
        except Exception:
            pass


def log(msg: str) -> None:
    say(msg)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LAUNCH_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def error_box(title: str, text: str) -> None:
    """用 Unicode 弹窗提示，避免任何代码页问题。"""
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 | 0x40000)
    except Exception:
        say(text)


def fail(title: str, detail: str, hint: str = "") -> int:
    log(f"[失败] {title}: {detail}")
    text = f"{title}\n\n{detail}"
    if hint:
        text += f"\n\n{hint}"
    text += f"\n\n日志：{LAUNCH_LOG}"
    error_box("桌面企鹅 · 启动失败", text)
    return 1


# ----------------------------------------------------------------------
# 环境准备
# ----------------------------------------------------------------------
def find_bootstrap_python() -> str | None:
    """找不到 venv 时，用来创建 venv 的一个系统 Python。"""
    cands: list[str] = []
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        for ver in ("Python313", "Python312", "Python311", "Python310"):
            cands.append(str(Path(local) / "Programs" / "Python" / ver / "python.exe"))
    cands.append(r"C:\Python313\python.exe")
    cands.append(r"C:\Python312\python.exe")
    for c in cands:
        if Path(c).exists():
            return c
    # 退而求其次：交给系统解析
    for probe in (["py", "-3"], ["python"]):
        try:
            r = subprocess.run(probe + ["-c", "import sys"],
                               capture_output=True, timeout=20)
            if r.returncode == 0:
                return " ".join(probe)
        except Exception:
            continue
    return None


def ensure_venv() -> bool:
    if VENV_PY.exists():
        return True
    base = find_bootstrap_python()
    if not base:
        return False
    log(f"未找到虚拟环境，正在用 {base} 创建 …")
    say("正在创建运行环境（首次运行，约需十几秒）…")
    cmd = base.split() if " " in base else [base]
    try:
        r = subprocess.run(cmd + ["-m", "venv", str(VENV_DIR)], timeout=300)
    except Exception as exc:
        log(f"创建 venv 异常: {exc}")
        return False
    if r.returncode != 0 or not VENV_PY.exists():
        log(f"创建 venv 失败，返回码 {r.returncode}")
        return False
    log("虚拟环境已创建")
    return True


def deps_ok() -> bool:
    try:
        r = subprocess.run(
            [str(VENV_PY), "-c",
             "import PySide6, PySide6.QtWidgets, PySide6.QtNetwork, pynput, psutil"],
            capture_output=True, timeout=90,
        )
        return r.returncode == 0
    except Exception:
        return False


def ensure_deps() -> bool:
    if deps_ok():
        return True

    say("依赖缺失，正在安装（约 80MB，请稍候）…")
    log("依赖缺失，尝试联网安装")
    try:
        r = subprocess.run(
            [str(VENV_PY), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")],
            timeout=1800,
        )
        if r.returncode == 0 and deps_ok():
            log("联网安装成功")
            return True
        log(f"联网安装失败，返回码 {r.returncode}")
    except Exception as exc:
        log(f"联网安装异常: {exc}")

    say("联网安装失败，改用本地 wheel 离线安装 …")
    log("改用离线安装")
    try:
        r = subprocess.run(
            [str(VENV_PY), str(ROOT / "tools" / "offline_install.py"), "install"],
            timeout=1800,
        )
        if r.returncode == 0 and deps_ok():
            log("离线安装成功")
            return True
        log(f"离线安装失败，返回码 {r.returncode}")
    except Exception as exc:
        log(f"离线安装异常: {exc}")
    return False


# ----------------------------------------------------------------------
# 启动
# ----------------------------------------------------------------------
def already_running() -> bool:
    """已经有实例在跑吗（主程序有单实例保护，会直接静默退出）。"""
    try:
        import psutil
    except Exception:
        return False
    me = os.getpid()
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if p.info["pid"] == me:
                continue
            name = (p.info.get("name") or "").lower()
            if not name.startswith("python"):
                continue
            argv = p.info.get("cmdline") or []
            if any(os.path.basename(a).lower() == "main.py" for a in argv):
                return True
        except Exception:
            continue
    return False


def launch() -> tuple[bool, str]:
    """启动主程序并做存活检查。返回 (是否成功, 说明)。"""
    if already_running():
        return True, "已经有企鹅在桌面上了，我没有重复启动。"

    exe = str(VENV_PY if CONSOLE else VENV_PYW)
    if not Path(exe).exists():
        return False, f"找不到解释器：{exe}"

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        err_fh = open(APP_ERR_LOG, "w", encoding="utf-8", errors="replace")
    except Exception:
        err_fh = None

    creation = 0 if CONSOLE else (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
    try:
        proc = subprocess.Popen(
            [exe, str(MAIN)],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=err_fh or subprocess.DEVNULL,
            creationflags=creation,
            close_fds=True,
        )
    except Exception as exc:
        if err_fh:
            err_fh.close()
        return False, f"无法启动进程：{exc}"
    finally:
        if err_fh:
            try:
                err_fh.close()
            except Exception:
                pass

    log(f"已启动 PID={proc.pid}（{Path(exe).name}）")

    # 存活检查：进程一直在，或者单实例管道已被占用（说明另一个实例在跑）
    deadline = time.time() + ALIVE_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            rc = proc.returncode
            if already_running():
                return True, "已经有企鹅在桌面上了。"
            detail = _read_tail(APP_ERR_LOG) or _read_app_log_tail()
            return False, f"主程序启动后立刻退出（退出码 {rc}）。\n\n{detail}"
        time.sleep(0.25)

    return True, ""


def _read_tail(path: Path, limit: int = 2500) -> str:
    try:
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        return text[-limit:] if text else ""
    except Exception:
        return ""


def _read_app_log_tail(limit: int = 1800) -> str:
    return _read_tail(ROOT / "data" / "logs" / "pet.log", limit)


# ----------------------------------------------------------------------
def main() -> int:
    log("=" * 46)
    log(f"启动请求：{ROOT}")

    if already_running():
        log("检测到已有实例，退出本次启动")
        return 0

    if not ensure_venv():
        return fail(
            "没有找到可用的 Python 运行环境",
            "需要在电脑上安装 Python 3.11 或更高版本，然后重新运行 start.bat。",
            "安装时请勾选 “Add python.exe to PATH”。\n"
            "下载地址：https://www.python.org/downloads/",
        )

    if not ensure_deps():
        return fail(
            "依赖安装失败",
            "PySide6 / pynput / psutil 没能装上。",
            "先试 start.bat（会显示进度）；如果网络不稳，\n"
            r"命令行执行：  .venv\Scripts\python.exe tools\offline_install.py",
        )

    ok, detail = launch()
    if not ok:
        return fail("企鹅没能启动", detail)
    if detail:
        say(detail)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # 兜底：启动器自己崩了也要看得见
        import traceback

        tb = traceback.format_exc()
        try:
            (LOG_DIR).mkdir(parents=True, exist_ok=True)
            with open(LAUNCH_LOG, "a", encoding="utf-8") as fh:
                fh.write(tb + "\n")
        except Exception:
            pass
        error_box("桌面企鹅 · 启动器异常", f"{type(exc).__name__}: {exc}\n\n{tb[-1200:]}")
        sys.exit(1)
