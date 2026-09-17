"""端到端可视化验证：真正启动桌宠、真开一局抛球，并**真的移动鼠标**去打。

    .venv\\Scripts\\python.exe tools\\shot_game.py

产出 shots/game_*.png。这个脚本的价值在于：
它走的是和玩家完全一样的路径——真的 QApplication、真的透明窗口、
真的全局鼠标位置、真的触碰判定，而不是直接调用内部函数。

会临时把系统鼠标移到球上（不然没法验证"碰到就出手"），跑完会还原。
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QGuiApplication, QCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT = os.path.join(ROOT, "shots")
STEPS: list[str] = []


def grab(name: str, note: str = "") -> str:
    """抓一张全屏图，裁到右下角（企鹅常驻的位置）。"""
    from ctypes import windll

    from PySide6.QtGui import QImage

    import screenshot

    data = screenshot.grab()
    w = windll.user32.GetSystemMetrics(78)
    h = windll.user32.GetSystemMetrics(79)
    img = QImage(data, w, h, w * 4, QImage.Format_RGB32)
    path = os.path.join(OUT, f"game_{name}.png")
    # 场地里球可能跑得到处都是，所以整屏存一份，另存一份右下角特写
    img.scaled(int(w * 0.66), int(h * 0.66)).save(path, "PNG")
    crop = img.copy(int(w * 0.30), int(h * 0.34), int(w * 0.70), int(h * 0.66))
    crop.scaled(int(crop.width() * 1.15), int(crop.height() * 1.15)).save(
        os.path.join(OUT, f"game_{name}_zoom.png"), "PNG"
    )
    STEPS.append(f"{name}: {note}")
    return path


def main() -> int:
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("桌面企鹅")
    app.setQuitOnLastWindowClosed(False)

    import main as app_main
    from game.throw_game import PHASE_FREE, PHASE_TO_PET

    ok, _server = app_main.acquire_single_instance()
    if not ok:
        print("已有实例在运行，先把它关掉再跑这个脚本")
        return 1

    pet = app_main.DeskPet(app)
    pet.start()
    os.makedirs(OUT, exist_ok=True)

    original = QCursor.pos()
    log: list[str] = []

    def note(text: str) -> None:
        # 立刻打印：断言失败时也要能看到前面每一步的真实数值
        log.append(text)
        print(text, flush=True)

    def pump(seconds: float) -> None:
        end_t = time.time() + seconds
        while time.time() < end_t:
            app.processEvents()
            time.sleep(0.008)

    def wait_until(pred, timeout: float) -> bool:
        end_t = time.time() + timeout
        while time.time() < end_t:
            app.processEvents()
            time.sleep(0.008)
            if pred():
                return True
        return False

    def dist_to_ball() -> float:
        b = pet.game.ball
        c = QCursor.pos()
        return ((c.x() - b.x) ** 2 + (c.y() - b.y) ** 2) ** 0.5

    # ---------- 严格串行，不用 singleShot 链 ----------
    # 之前用一串 singleShot 调度，每一步又各自阻塞 pump，
    # 结果定时器和外层 pump 互相重入，时序全乱（游戏直接跑成 over）。
    pet._start_game()
    note(f"[1] 开局 active={pet.game.active} phase={pet.game.phase}")
    assert pet.game.active, "游戏没开起来"
    pump(1.5)
    grab("1_serve", "企鹅发球瞬间")
    note(f"[1] 发球后 phase={pet.game.phase} serves={pet.game._serves}")

    # 等球真的停稳（rest_t 累积）再动鼠标，否则 setPos 的目标位置
    # 是读取瞬间的旧坐标，鼠标会落到球后面去
    landed = wait_until(
        lambda: (pet.game.phase == PHASE_FREE and pet.game.ball.grounded
                 and pet.game.ball.rest_t > 0.15), 14.0
    )
    b = pet.game.ball
    note(f"[2] 落地={landed} phase={pet.game.phase} 球=({b.x:.0f},{b.y:.0f}) "
               f"r={b.r:.0f} rot={b.rot:.0f}deg 静止{b.rest_t:.2f}s")
    note(f"[2] 鼠标到球距离 = {dist_to_ball():.0f}px（发球特意避开鼠标）")
    grab("2_landed", "球落地，外面套着触碰提示环")

    # ---- 真的把系统鼠标移到球上：玩家就是这么打的 ----
    before = pet.game.phase
    note(f"[3] 触碰前: 鼠标={QCursor.pos().x()},{QCursor.pos().y()} "
         f"球={b.x:.0f},{b.y:.0f} 距离={dist_to_ball():.0f}px "
         f"arm闸门={pet.game._phase_t:.2f}s 球半径={b.r} "
         f"hit={abs(dist_to_ball() - 0) < b.r + 18}")
    QCursor.setPos(int(b.x), int(b.y))
    pump(0.05)
    note(f"[3] setPos 后: 鼠标={QCursor.pos().x()},{QCursor.pos().y()} "
         f"距离={dist_to_ball():.0f}px")
    pump(0.30)
    note(f"[3] 再等 0.3s: 鼠标={QCursor.pos().x()},{QCursor.pos().y()} "
         f"球={b.x:.0f},{b.y:.0f} 距离={dist_to_ball():.0f}px "
         f"phase={pet.game.phase}")
    after = pet.game.phase
    note(f"[3] 鼠标碰到球: phase {before} -> {after} (期望 {PHASE_TO_PET})")
    assert after == PHASE_TO_PET, f"碰到球却没出手！phase={after}"
    grab("3_touched", "鼠标碰到球 -> 已出手")

    pump(0.45)
    b = pet.game.ball
    spd = (b.vx ** 2 + b.vy ** 2) ** 0.5
    note(f"[4] 飞行中 phase={pet.game.phase} 速度={spd:.0f}px/s "
               f"（目标手感 ~600-1000）")
    grab("4_inflight", "皮球在空中（自旋 + 拖尾）")

    caught = wait_until(lambda: pet.game.hits > 0 or not pet.game.active, 6.0)
    note(f"[5] 接球={caught} hits={pet.game.hits} score={pet.game.score} "
               f"phase={pet.game.phase}")
    assert pet.game.hits > 0, "球没被接住"

    # ---- 第二回合：确认可以连续玩（不是只能出手一次）----
    served = wait_until(lambda: pet.game.phase == PHASE_FREE, 6.0)
    wait_until(
        lambda: (pet.game.phase == PHASE_FREE and pet.game.ball.alive
                 and pet.game._phase_t > 0.75), 6.0
    )
    b = pet.game.ball
    QCursor.setPos(int(b.x), int(b.y))
    pump(0.30)
    note(f"[6] 第二回合 served={served} 出手后 phase={pet.game.phase} "
               f"hits={pet.game.hits} serves={pet.game._serves}")
    assert pet.game.phase == PHASE_TO_PET, "第二回合没能再次出手"
    assert pet.game._serves >= 3, f"发球次数异常: {pet.game._serves}"
    grab("5_second", "第二回合（连打正常）")

    pet.game.stop(silent=True)
    pump(0.5)
    note(f"[7] 结束 active={pet.game.active}")
    QCursor.setPos(original)

    print(chr(10).join(log))
    print(chr(10) + "--- 截图 ---")
    print(chr(10).join(STEPS))
    print(chr(10) + "全部断言通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
