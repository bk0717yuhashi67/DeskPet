"""把抛球游戏的覆盖层离屏渲染出来，目视检查皮球 / 提示环 / HUD。

    .venv\\Scripts\\python.exe tools\\preview_game.py

为什么要离屏渲染：现场截图会被"人真的在动鼠标"干扰（这恰恰说明触碰判定
在工作，但没法稳定复现某一张画面）。离屏渲染可以精确指定球在哪、什么相位，
每次结果都一样，适合迭代外观。

输出 shots/preview_game.png
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from game import physics  # noqa: E402
from game.overlay import GameOverlay  # noqa: E402
from game.throw_game import PHASE_FREE, PHASE_HOLD, PHASE_TO_PET, Popup, Ring  # noqa: E402

CELL_W, CELL_H = 480, 400


class FakeGame:
    """覆盖层只读这几个字段，给个替身就够了（不用真的起一局游戏）。"""

    def __init__(self, phase: str, ball: physics.Ball, **kw) -> None:
        self.phase = phase
        self.ball = ball
        self.bounds = physics.Bounds(left=60.0, right=CELL_W - 60.0,
                                     floor=CELL_H - 40.0, ceiling=90.0)
        self.rings: list[Ring] = []
        self.popups: list[Popup] = []
        self.score = kw.get("score", 0)
        self.combo = kw.get("combo", 0)
        self.lives = kw.get("lives", 3)
        self.max_lives = 3


def main() -> int:
    app = QApplication(sys.argv)  # noqa: F841

    overlay = GameOverlay()
    overlay.resize(CELL_W, CELL_H)

    cells = []

    b = physics.Ball(x=250.0, y=300.0, r=24.0, alive=True, owner="free", rot=25.0)
    b.grounded = True
    cells.append(("球停在地上 · 该你出手（外面是触碰提示环）",
                  FakeGame(PHASE_FREE, b, score=195, combo=5)))

    b = physics.Ball(x=250.0, y=120.0, r=24.0, alive=True, owner="pet", rot=200.0)
    b.vx, b.vy = -520.0, -160.0
    for i in range(10):
        b.trail.append((250.0 + i * 14.0, 120.0 + i * 4.0))
    cells.append(("球飞向企鹅（自旋 + 拖尾 + 落点虚影）",
                  FakeGame(PHASE_TO_PET, b, score=195, combo=5)))

    b = physics.Ball(x=430.0, y=300.0, r=20.0, alive=True, owner="pet", rot=310.0)
    cells.append(("企鹅抱着球 · Lv5（球变小）",
                  FakeGame(PHASE_HOLD, b, score=1240, combo=12, lives=2)))

    b = physics.Ball(x=250.0, y=300.0, r=24.0, alive=True, owner="free", rot=0.0)
    b.grounded = True
    g = FakeGame(PHASE_FREE, b, score=760, combo=3, lives=1)
    g.popups = [Popup(300.0, 210.0, "+36", life=1.15, age=0.25)]
    g.rings = [Ring(250.0, 300.0, 46.0, age=0.18)]
    cells.append(("连击飘字 + 庆祝圆环", g))

    cols = 2
    rows = (len(cells) + cols - 1) // cols
    PAD = 14
    W = cols * CELL_W + (cols + 1) * PAD
    H = rows * (CELL_H + 20) + (rows + 1) * PAD
    out = QImage(W, H, QImage.Format_ARGB32)
    out.fill(QColor("#F4F7FC"))
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)

    for i, (label, game) in enumerate(cells):
        cx = PAD + (i % cols) * (CELL_W + PAD)
        cy = PAD + (i // cols) * (CELL_H + 20 + PAD)
        p.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
        p.setPen(QColor("#22314A"))
        p.drawText(cx, cy + 14, label)
        # 给这一格铺一层"桌面"底色 + 地面线，覆盖层画在上面
        p.fillRect(cx, cy + 20, CELL_W, CELL_H, QColor("#E9EEF6"))
        p.setPen(QColor("#CDD7E6"))
        p.drawLine(cx, cy + 20 + CELL_H - 40, cx + CELL_W, cy + 20 + CELL_H - 40)
        p.setPen(QColor("#B7C4D6"))
        p.drawRect(cx, cy + 20, CELL_W - 1, CELL_H - 1)
        p.end()

        # 用 QWidget.render(device, offset) 把覆盖层画进同一张图。
        # 注意别在 QPainter 活着的时候 render，也不要自己传 painter。
        overlay.game = game
        overlay.resize(CELL_W, CELL_H)
        overlay.render(out, QPoint(cx, cy + 20))

        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing, True)

    p.end()
    out_path = os.path.join(ROOT, "shots", "preview_game.png")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    out.save(out_path, "PNG")
    print(f"已生成 {out_path} ({out.width()}x{out.height()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
