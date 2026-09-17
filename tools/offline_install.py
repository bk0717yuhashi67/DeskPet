"""离线依赖下载器 / 安装器。

为什么需要它：PySide6 的 wheel 有 70MB+，在部分网络下 pip 会中途断流
（IncompleteRead），反复重试也未必成功。这个脚本用带重试与断点续传的方式
把 wheel 抓到本地，然后用 --no-index 从本地目录安装，全程不依赖网络稳定性。

用法：
    python tools/offline_install.py download   # 只下载到 wheels/
    python tools/offline_install.py install    # 从 wheels/ 安装到 .venv
    python tools/offline_install.py            # 下载 + 安装
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WHEEL_DIR = ROOT / "wheels"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"

# (包名, 版本 or None 表示最新)
PACKAGES: list[tuple[str, str | None]] = [
    ("PySide6-Essentials", "6.11.2"),
    ("shiboken6", "6.11.2"),
    ("pynput", "1.8.2"),
    ("psutil", None),
    ("six", None),
]

# 镜像前缀 -> 用于把 files.pythonhosted.org 的地址改写到镜像
MIRROR_PREFIXES = [
    "https://mirrors.aliyun.com/pypi/",
    "https://pypi.tuna.tsinghua.edu.cn/",
    "",   # 空串 = 保持官方原址
]

UA = {"User-Agent": "Mozilla/5.0 (deskpet-offline-installer)"}
CHUNK = 1 << 16


def log(msg: str) -> None:
    print(msg, flush=True)


# ----------------------------------------------------------------------
def pick_wheel(files: list[dict]) -> dict | None:
    """从发布文件里挑一个 Windows + 当前解释器能用的 wheel。

    坑点记录：
      * psutil 的 abi3 wheel 文件名是 cp37-abi3，不能按 "cp37" 一刀切排除；
      * cp313t / cp314t 是自由线程（no-GIL）构建，普通解释器装不了；
      * 必须先排除平台再按 abi3 / 具体版本 / 纯 Python 的优先级挑。
    """
    import re

    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    wheels = [f for f in files if f["filename"].endswith(".whl")]

    def ok(n: str) -> bool:
        low = n.lower()
        bad_plat = ("macosx", "manylinux", "musllinux", "aarch64", "arm64",
                    "win32", "i686", "ppc64", "s390x")
        if any(b in low for b in bad_plat):
            return False
        if re.search(r"cp\d+t", low):        # 自由线程构建
            return False
        if "abi3" in low or "none-any" in low:
            return True
        return tag in low                    # 必须是本解释器版本的专用 wheel

    for want in (
        lambda n: "win_amd64" in n and "abi3" in n,
        lambda n: "win_amd64" in n,
        lambda n: n.endswith("none-any.whl"),
    ):
        for f in wheels:
            n = f["filename"]
            if ok(n) and want(n):
                return f
    return None


def resolve(pkg: str, ver: str | None) -> dict | None:
    url = (f"https://pypi.org/pypi/{pkg}/json" if ver is None
           else f"https://pypi.org/pypi/{pkg}/{ver}/json")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=40
            ) as r:
                data = json.load(r)
            files = data.get("urls") or data["releases"].get(data["info"]["version"], [])
            f = pick_wheel(files)
            if f is None:
                log(f"  !! {pkg}: 找不到可用的 wheel")
                return None
            return {
                "package": pkg,
                "filename": f["filename"],
                "url": f["url"],
                "size": f.get("size", 0),
                "sha256": (f.get("digests") or {}).get("sha256", ""),
            }
        except Exception as exc:
            log(f"  .. {pkg} 元数据获取失败({attempt + 1}/4): {exc}")
            time.sleep(1.5)
    return None


def candidates(meta: dict) -> list[str]:
    out: list[str] = []
    tail = meta["url"].split("files.pythonhosted.org/", 1)[-1]
    for prefix in MIRROR_PREFIXES:
        out.append(meta["url"] if not prefix else prefix + tail)
    return out


def human(n: float) -> str:
    return f"{n / 1048576:.1f}MB"


def download(meta: dict) -> Path | None:
    dest = WHEEL_DIR / meta["filename"]
    want = meta["size"]

    # 已经下好且校验通过就直接用
    if dest.exists() and want and dest.stat().st_size == want:
        if verify(dest, meta["sha256"]):
            log(f"  == 已存在 {meta['filename']} ({human(want)})")
            return dest

    for url in candidates(meta):
        host = url.split("/")[2]
        for attempt in range(3):
            have = dest.stat().st_size if dest.exists() else 0
            headers = dict(UA)
            if have:
                headers["Range"] = f"bytes={have}-"
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=60) as r:
                    total = int(r.headers.get("Content-Length") or 0) + have
                    mode = "ab" if have and r.status == 206 else "wb"
                    if mode == "wb":
                        have = 0
                    log(f"  -> {host} 续传自 {human(have)} / {human(want or total)}")
                    done = have
                    last = time.time()
                    with open(dest, mode) as fh:
                        while True:
                            chunk = r.read(CHUNK)
                            if not chunk:
                                break
                            fh.write(chunk)
                            done += len(chunk)
                            if time.time() - last > 3:
                                last = time.time()
                                log(f"     {human(done)}"
                                    + (f" / {human(want)}" if want else ""))
                got = dest.stat().st_size
                if want and got != want:
                    log(f"  !! 大小不符 {got} != {want}，继续续传")
                    continue
                if verify(dest, meta["sha256"]):
                    log(f"  OK {meta['filename']} ({human(got)})")
                    return dest
                log("  !! 校验失败，重新下载")
                dest.unlink(missing_ok=True)
            except Exception as exc:
                log(f"  !! {host} 第 {attempt + 1} 次失败: {exc}")
                time.sleep(2)
    log(f"  XX 放弃 {meta['filename']}")
    return None


def verify(path: Path, expect: str) -> bool:
    if not expect:
        return True
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    ok = h.hexdigest() == expect
    if not ok:
        log(f"  !! sha256 不匹配: {h.hexdigest()[:16]} != {expect[:16]}")
    return ok


# ----------------------------------------------------------------------
def do_download() -> int:
    WHEEL_DIR.mkdir(parents=True, exist_ok=True)
    log(f"目标目录: {WHEEL_DIR}")
    metas = []
    for pkg, ver in PACKAGES:
        log(f"[{pkg}]")
        meta = resolve(pkg, ver)
        if meta:
            log(f"  {meta['filename']}  {human(meta['size'])}")
            metas.append(meta)
    if not metas:
        log("没有解析到任何 wheel")
        return 1
    ok = 0
    for meta in metas:
        if download(meta):
            ok += 1
    log(f"下载完成 {ok}/{len(metas)}")
    return 0 if ok == len(metas) else 1


def ensure_venv() -> bool:
    if VENV_PY.exists():
        return True
    log("创建虚拟环境 .venv …")
    base = _find_base_python()
    if not base:
        log("找不到可用的 Python 解释器来创建 venv")
        return False
    r = subprocess.run([base, "-m", "venv", str(ROOT / ".venv")])
    return r.returncode == 0 and VENV_PY.exists()


def _find_base_python() -> str | None:
    cands = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Python/Python313/python.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Python/Python312/python.exe",
        Path("C:/Python313/python.exe"),
        Path(sys.executable),
    ]
    for c in cands:
        try:
            if c.exists():
                return str(c)
        except OSError:
            continue
    return None


def do_install() -> int:
    if not ensure_venv():
        return 1

    # 只装"解析出来的那一批"，不要盲 glob —— 目录里可能残留版本不匹配的 wheel
    # （比如 cp313t 自由线程构建），直接喂给 pip 会整批失败。
    wanted: list[str] = []
    for pkg, ver in PACKAGES:
        meta = resolve(pkg, ver)
        if not meta:
            continue
        path = WHEEL_DIR / meta["filename"]
        if path.exists():
            wanted.append(path.name)
        else:
            log(f"  !! 缺少 {meta['filename']}")

    extra = [w.name for w in sorted(WHEEL_DIR.glob("*.whl")) if w.name not in wanted]
    if extra:
        log(f"（忽略目录里多余的 {len(extra)} 个 wheel：{', '.join(extra[:3])}）")
    if not wanted:
        log("wheels/ 里没有可用的 wheel，先执行 download")
        return 1

    log(f"从本地目录离线安装 {len(wanted)} 个 wheel")
    args = [
        str(VENV_PY), "-m", "pip", "install",
        "--no-index", "--find-links", str(WHEEL_DIR),
        "--no-warn-script-location",
    ] + wanted
    r = subprocess.run(args, cwd=str(WHEEL_DIR))
    if r.returncode != 0:
        log("安装失败")
        return r.returncode

    log("校验导入 …")
    code = (
        "import PySide6, PySide6.QtWidgets, PySide6.QtGui, PySide6.QtCore,"
        " pynput, psutil;"
        "print('PySide6', PySide6.__version__); print('psutil', psutil.__version__)"
    )
    return subprocess.run([str(VENV_PY), "-c", code]).returncode


def main() -> int:
    what = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    if what in ("download", "all"):
        if do_download() != 0 and what == "download":
            return 1
    if what in ("install", "all"):
        return do_install()
    return 0


if __name__ == "__main__":
    sys.exit(main())
