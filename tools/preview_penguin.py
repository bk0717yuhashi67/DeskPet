"""把企鹅的各种姿势渲染成一张联系表（contact sheet），用于目视检查造型与动画。

不开窗口、不需要桌面环境，纯离屏渲染：

    .venv\\Scripts\\python.exe tools\\preview_penguin.py

输出 preview_penguin.png（默认在项目根目录）。
红色椭圆是命中判定区（身体椭圆 / 头部椭圆），用来检查"点在企鹅身上"是否合理。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pet import clips, rig  # noqa: E402
from pet.animator import _eval  # noqa: E402
from pet.pose import (  # noqa: E402
    BODY_CX,
    BODY_CY,
    BODY_RX,
    BODY_RY,
    HEAD_CY,
    HEAD_RX,
    HEAD_RY,
    OFF_X,
    OFF_Y,
    Pose,
)

CELL = 250
PAD = 12
COLS = 6

SHOW_HIT = "--hit" in sys.argv

# (标题, clip名, t) —— t=None 表示用默认姿势
CASES: list[tuple[str, str | None, float]] = [
    ("待机", None, 0.0),
    ("眨眼", "blink", 0.35),
    ("张望", "look_around", 0.5),
    ("扇翅膀", "wing_flap", 0.13),
    ("跳起", "hop", 0.61),
    ("转圈", "spin", 0.5),
    ("伸懒腰", "stretch", 0.5),
    ("打哈欠", "yawn", 0.5),
    ("被戳肚子", "pat_belly", 0.16),
    ("挣扎", "struggle", 0.5),
    ("抛球", "throw_ball", 0.45),
    ("接球", "catch_ball", 0.6),
    ("吃饭", "eat", 0.44),
    ("戴围脖", "scarf_put_on", 0.6),
    ("睡觉", "sleep_loop", 0.25),
    ("庆祝", "celebrate", 0.30),
    ("委屈", "sad", 0.4),
    ("挥手", "wave", 0.22),
    ("点头", "nod", 0.35),
    ("摇晃走", "waddle", 0.25),
    ("头晕", "dizzy", 0.0),
    ("探头", "peek", 0.0),
]


def pose_for(name: str | None, t: float) -> Pose:
    if name is None:
        return Pose()
    clip = clips.get(name)
    if clip is None:
        print(f"  !! 找不到动作 {name}")
        return Pose()
    p = _eval(clip, t)
    if clip.prop:
        p.prop = clip.prop
    return p


def main() -> int:
    app = QApplication(sys.argv)  # noqa: F841  Qt 绘图需要一个 app 实例
    rows = (len(CASES) + COLS - 1) // COLS
    w = COLS * CELL + PAD * 2
    h = rows * (CELL + 20) + PAD * 2

    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor("#F4F7FC"))

    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    font = QFont("Microsoft YaHei UI", 8)
    font.setFamilies(["Microsoft YaHei UI", "微软雅黑", "Segoe UI", "sans-serif"])
    p.setFont(font)

    for i, (label, name, t) in enumerate(CASES):
        cx = PAD + (i % COLS) * CELL
        cy = PAD + (i // COLS) * (CELL + 20)

        # 单元格底
        p.setPen(QPen(QColor("#E4EAF4"), 1))
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(QRect(cx, cy, CELL, CELL), 10, 10)

        p.save()
        p.translate(cx, cy)
        # 地面参考线
        p.setPen(QPen(QColor("#EEF2F8"), 1, Qt.DashLine))
        p.drawLine(6, int(OFF_Y + 193), CELL - 6, int(OFF_Y + 193))

        rig.paint_penguin(p, pose_for(name, t))

        if SHOW_HIT:
            pose = pose_for(name, t)
            p.setPen(QPen(QColor(220, 60, 60, 170), 1.2))
            p.setBrush(Qt.NoBrush)
            bx, by = rig.transform_point(pose, BODY_CX, BODY_CY)
            p.drawEllipse(QPointF(bx, by), BODY_RX * pose.body_sx + 6,
                          BODY_RY * pose.body_sy + 6)
            # 头部椭圆会随头部动画平移/旋转，这里用同一套变换算中心
            hcx, hcy = rig.head_transform_point(pose, HEAD_CX, HEAD_CY)
            hx, hy = rig.transform_point(pose, hcx, hcy)
            p.drawEllipse(QPointF(hx, hy), HEAD_RX + 6, HEAD_RY + 6)
        p.restore()

        p.setPen(QColor("#7C8AA3"))
        p.drawText(QRect(cx, cy + CELL, CELL, 18), Qt.AlignCenter, label)

    p.end()

    out = os.path.join(ROOT, "preview_penguin.png")
    if not img.save(out, "PNG"):
        print("保存失败")
        return 1
    print(f"已生成 {out}  ({w}x{h})")
    print(f"共 {len(CASES)} 个姿势；加 --hit 参数可叠加命中区显示")
    return 0


if __name__ == "__main__":
    sys.exit(main())
