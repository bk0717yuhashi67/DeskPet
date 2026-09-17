"""生成企鹅图标 assets/penguin.ico（多尺寸 PNG 内嵌的 ICO）。

Qt 在 Windows 上不一定能写 ICO，所以这里按 ICO 规范手写文件头，
每个尺寸的负载直接塞一张 PNG（Vista 以后的 ICO 都支持 PNG 内嵌）。

    .venv\\Scripts\\python.exe tools\\make_icon.py
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SIZES = (16, 24, 32, 48, 64, 128, 256)


def build_ico(out_path: str, sizes: tuple[int, ...] = SIZES) -> str:
    from PySide6.QtCore import QBuffer, QByteArray, Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])  # noqa: F841
    from pet import rig  # noqa: E402
    from pet.pose import Pose  # noqa: E402

    pngs: list[tuple[int, bytes]] = []
    for size in sizes:
        # 高倍超采样后缩小，边缘更干净
        ss = 4
        img = QImage(size * ss, size * ss, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # 小尺寸下整只企鹅太糊，直接放大头部（托盘图标也是这个思路）
        pose = Pose()
        if size <= 32:
            # 只画头 + 肩膀，撑满画布
            scale = (size * ss) / 150.0
            p.translate(size * ss * 0.5, size * ss * 0.58)
            p.scale(scale, scale)
            p.translate(-100.0, -84.0)
        else:
            scale = (size * ss) / 232.0
            p.translate(size * ss * 0.5, size * ss * 0.5)
            p.scale(scale, scale)
            p.translate(-120.0, -122.0)
        rig.paint_penguin(p, pose)
        p.end()

        small = img.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QBuffer.WriteOnly)
        small.save(buf, "PNG")
        buf.close()
        pngs.append((size, bytes(ba.data())))

    # ---- 组装 ICO ----
    count = len(pngs)
    header = struct.pack("<HHH", 0, 1, count)
    offset = len(header) + 16 * count
    entries = b""
    blobs = b""
    for size, data in pngs:
        w = 0 if size >= 256 else size
        h = 0 if size >= 256 else size
        entries += struct.pack(
            "<BBBBHHII", w, h, 0, 0, 1, 32, len(data), offset
        )
        blobs += data
        offset += len(data)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(header + entries + blobs)
    return out_path


def main() -> int:
    out = os.path.join(ROOT, "assets", "penguin.ico")
    build_ico(out)
    size = os.path.getsize(out)
    print(f"已生成 {out}（{len(SIZES)} 个尺寸，{size} 字节）")

    if "--install" not in sys.argv:
        return 0

    # 一键把新图标装到桌面快捷方式上。
    # 只重新生成 .ico 是不够的：Windows 的图标缓存按路径命中，
    # 不重建快捷方式、不刷缓存，资源管理器上还是旧图标。
    sys.path.insert(0, ROOT)
    from app import shortcut

    for lnk in shortcut.recreate_shortcuts():
        print(f"已重建快捷方式 {lnk}")
    info = shortcut.read_shortcut(
        (shortcut.desktop_dir() or Path(ROOT)) / "桌面企鹅.lnk"
    )
    if info:
        print(f"  图标指向 {info.get('icon')}")
    if shortcut.refresh_icon_cache():
        print("已请求 Windows 重建图标缓存（ie4uinit -show）")
    else:
        print("图标缓存刷新失败（不影响使用：重启资源管理器后会自动更新）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
