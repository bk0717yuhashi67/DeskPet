"""桌面企鹅 · 程序入口。

装配所有模块，把它们接在一起，然后交给 Qt 事件循环。
"""
from __future__ import annotations

import sys
import time

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket   # 单实例锁用的是命名管道
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from app import logging_setup, paths
from app.config import config
from care import lines
from care.budget import ActionBudget, BubbleBudget
from care.features import CareManager
from game.overlay import GameOverlay
from game.throw_game import ThrowGame
from monitor import analyzer
from monitor.hooks import InputHooks
from monitor.sampler import Sampler
from monitor.storage import Storage
from pet import clips
from pet.actions import RandomScheduler
from pet.animator import Animator
from pet.mood import LABELS, Mood, MoodTracker
from pet.state_machine import ACT, DRAG, GAME, IDLE, REMIND, SLEEP, StateMachine
from remind.model import ACTION_AUTO, ACTION_SLEEP
from remind.scheduler import ReminderScheduler, action_to_clip
from remind.store import ReminderStore
from ui.bubble import Bubble
from ui.dialogs.consent_dialog import CHOICE_FULL, ConsentDialog
from ui.dialogs.reminder_dialog import ReminderDialog
from ui.dialogs.result_dialog import ResultDialog
from ui.dialogs.settings_dialog import SettingsDialog
from ui.dialogs.stats_panel import StatsPanel
from ui.pet_window import PetWindow
from ui.tray import Tray

LOCK_NAME = "DeskPetPenguin-SingleInstance-v1"
log = logging_setup.get("main")

# 左键单击企鹅时轮流播放的动作（顺序轮换，见 _on_pet_clicked）
CLICK_CLIPS = ("pat_belly", "nod", "wing_flap", "peek", "hop")


class DeskPet:
    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.storage = Storage()
        self.hooks = InputHooks()
        self.sm = StateMachine()
        self.animator = Animator()
        self.pet = PetWindow(self.animator, self.sm)
        self.bubble = Bubble()
        self.overlay = GameOverlay()

        self.sampler = Sampler(self.storage, self.hooks)
        self.store = ReminderStore(self.storage)
        self.store.ensure_builtins()
        self.scheduler = ReminderScheduler(self.storage, self.store)
        self.budget = BubbleBudget()
        self.action_budget = ActionBudget()
        self.mood = MoodTracker()
        self.care = CareManager(self.sampler, self.storage, self.sm, self.budget, self.mood)
        self.game = ThrowGame(
            self.overlay, self.hooks, self.storage, self._pet_center
        )
        self.tray = Tray()

        self.action_scheduler = RandomScheduler(int(config.get("random_action_per_hour", 3) or 3))
        self._current_reminder = None
        self._degraded_notified = False
        self._last_raise = 0.0
        self._mood_cache = Mood.CALM
        self._clicks_since_speak = 0
        self._icon_kind = ""
        self._server: QLocalServer | None = None

        self._wire()

    # ==================================================================
    def _wire(self) -> None:
        self.animator.on_finish = self._on_clip_finished

        # 企鹅
        self.pet.clicked.connect(self._on_pet_clicked)
        self.pet.drag_started.connect(self._on_drag_started)
        self.pet.drag_finished.connect(lambda _p: self._on_drag_finished())
        self.pet.drag_blocked_by_game.connect(
            lambda: self.show_bubble(lines.pick("game_cannot_drag"))
        )
        self.pet.session_locked.connect(self._on_locked)
        self.pet.session_unlocked.connect(self._on_unlocked)
        self.pet.power_suspend.connect(self._on_suspend)
        self.pet.power_resume.connect(self._on_resume)
        self.pet.screen_geometry_changed.connect(lambda: None)
        # 右键点企鹅本体也弹菜单（和托盘图标用的是同一个 QMenu 实例）
        self.pet.menu_requested.connect(self._show_menu_at_pet)

        # 鼠标事件走 Raw Input：系统把 WM_INPUT 投递到宠物窗口，这里转交监听器。
        # 不走全局钩子是为了不占用输入通路（钩子会让光标在高事件率下卡顿）。
        self.pet.raw_input_sink = self.hooks.feed_raw_input
        self.pet.native_ready.connect(self.hooks.set_raw_window)

        # 采样
        self.sampler.state_flags.connect(self._on_flags)

        # 提醒
        self.scheduler.fired.connect(self._on_reminder)

        # 气泡
        self.bubble.ack.connect(self._on_bubble_ack)
        self.bubble.snooze.connect(self._on_bubble_snooze)
        self.bubble.closed.connect(self._on_bubble_closed)

        # 小游戏
        self.game.speak.connect(lambda t: self.show_bubble(t))
        self.game.pet_clip.connect(self.play)
        self.game.started.connect(self._on_game_started)
        self.game.ended.connect(self._on_game_ended)

        # 关怀
        # 预算已在 CareManager 各 _check_* 里先 can_speak 再 record 后才 emit，
        # 这里必须 skip_budget，否则刚 record 完 last_at=now，show_bubble 再查一次
        # can_speak 必失败，气泡被丢弃——这是"企鹅除了提醒从不主动说话"的真正根因。
        self.care.speak.connect(
            lambda t, urgent=False: self.show_bubble(t, urgent=urgent, skip_budget=True)
        )
        self.care.pet_clip.connect(self.play)
        self.care.missed_provider = self.scheduler.take_missed

        # 托盘
        t = self.tray
        t.toggle_window.connect(self._toggle_window)
        t.start_game.connect(self._start_game)
        t.stop_game.connect(lambda: self.game.stop())
        t.manual_action.connect(self._manual_action)
        # 提醒设置 / 暂停统计 / 清除今日数据 已收进设置窗口，托盘不再有这三项
        t.open_stats.connect(self._open_stats)
        t.open_settings.connect(self._open_settings)
        t.toggle_sleep.connect(self._toggle_sleep)
        t.toggle_pomodoro.connect(self._toggle_pomodoro)
        t.open_about.connect(self._open_about)
        t.create_shortcut.connect(self._create_shortcut)
        t.quit_app.connect(self.app.quit)
        t.set_scale.connect(self.pet.set_scale)

        # 屏幕变化
        for scr in QGuiApplication.screens():
            scr.geometryChanged.connect(self.pet.on_screens_changed)
        self.app.screenAdded.connect(lambda _s: self.pet.on_screens_changed())
        self.app.screenRemoved.connect(lambda _s: self.pet.on_screens_changed())

        # 秒级调度
        self._sec = QTimer()
        self._sec.setInterval(1000)
        self._sec.timeout.connect(self._on_second)
        self._sec.start()

        self.app.aboutToQuit.connect(self._shutdown)

    # ==================================================================
    # 启动
    # ==================================================================
    def start(self) -> None:
        if not config.get("consent"):
            self._ask_consent()
        else:
            self._apply_privacy()
        self.pet.show()
        self.tray.set_check("pause", not self.sampler.enabled)
        self.sampler.start()
        self.scheduler.start()
        # 开机 / 唤醒后的错过补偿
        QTimer.singleShot(3000, self.scheduler.catch_up)
        QTimer.singleShot(2500, self.care.start)
        QTimer.singleShot(6000, self._first_hello)
        log.info("桌宠已启动")

    def _ask_consent(self) -> None:
        dlg = ConsentDialog()
        choice = dlg.exec()
        # 关掉弹窗（Esc / 关闭）视同"仅用基础功能"——不采集输入是最保险的默认值
        config.set("consent", True)
        config.set("collect_input", choice == CHOICE_FULL)
        # 必须立刻落盘：否则下次启动会再问一遍，用户会以为自己的选择没生效
        config.save()
        log.info("隐私同意：%s", "完整统计" if choice == CHOICE_FULL else "仅基础功能")
        self._apply_privacy()

    def _apply_privacy(self) -> None:
        collect = bool(config.get("collect_input", True))
        self.sampler.set_enabled(collect)
        self.sampler.set_consent(bool(config.get("consent", False)))
        if collect:
            self.hooks.start()

    def _first_hello(self) -> None:
        # 只在「第一次启动」时打招呼；见过面后写 meta 标记，之后每次重启都不再弹
        if self.storage.get_meta("met_before"):
            return
        if not self.sm.is_proactive_blocked:
            self.show_bubble(lines.pick("first_meet"))
        self.storage.set_meta("met_before", True)

    # ==================================================================
    # 动画 / 动作
    # ==================================================================
    def play(self, name: str | None, blend_ms: float = 120.0) -> None:
        if not name or clips.get(name) is None:
            return
        self.animator.rate = self.mood.effect.anim_rate
        self.animator.play(name, time.monotonic(), blend_ms=blend_ms)

    def _manual_action(self, name: str) -> None:
        self.care.note_interaction()
        if self.sm.is_state(SLEEP):
            self._wake()
        self.play(name)
        if not self.sm.is_state(GAME):
            self.sm.request(ACT, source="manual", force=True)

    def _on_clip_finished(self, name: str) -> None:
        if self.sm.is_state(DRAG) or self.sm.is_state(GAME):
            return
        if name in ("throw_ball", "catch_ball", "sleep_loop"):
            return
        self.animator.stop(time.monotonic())
        if self.sm.is_state(ACT, REMIND):
            self.sm.back_to_idle()

    # ==================================================================
    # 秒级调度
    # ==================================================================
    def _on_second(self) -> None:
        now_m = time.monotonic()
        st = self.sampler.stats

        # 心情变化时调整动作频率与动画速度
        if self.mood.mood != self._mood_cache:
            self._mood_cache = self.mood.mood
            rate = max(0.0, self.mood.effect.action_rate)
            base = int(config.get("random_action_per_hour", 3) or 3)
            self.action_scheduler.reconfigure(int(round(base * rate)))
            self.animator.rate = self.mood.effect.anim_rate
            log.info("心情切换 -> %s", LABELS.get(self.mood.mood, ""))

        # 随机轻动作
        if (
            self.sm.is_state(IDLE)
            and self.animator.clip is None
            and not self.sm.paused
        ):
            blocked = self.sm.is_proactive_blocked or self.mood.effect.action_rate <= 0
            name = self.action_scheduler.poll(now_m, blocked)
            if name:
                self.play(name)
                self.sm.request(ACT, source="random")

        # 气泡跟随企鹅
        if self.bubble.isVisible():
            self.bubble.move_to(self.pet.head_global())

        # 每 5 秒把企鹅抬到我们自己的窗口之上（球层要压在企鹅下面）
        if now_m - self._last_raise > 5.0:
            self._last_raise = now_m
            self.pet.raise_()

        # 托盘状态
        active = analyzer.fmt_duration(st.active_s)
        keys = st.keystrokes
        suffix = ""
        if self.care.pomodoro_active:
            left = self.care.pomodoro_remaining()
            suffix = f" · 专注剩 {left // 60:02d}:{left % 60:02d}"
        self.tray.set_status_line(self._status_text(active, keys, suffix))
        self._refresh_tray_icon()

        # 钩子降级只提醒一次
        if self.hooks.degraded and not self._degraded_notified and st.active_s > 900:
            self._degraded_notified = True
            self.show_bubble(lines.pick("hook_degraded"))

    @staticmethod
    def _status_text(active: str, keys: int, suffix: str = "") -> str:
        return f"今日已陪伴 {active} · 按键 {keys}{suffix}"

    def _refresh_tray_icon(self) -> None:
        if self.game.active:
            kind = "game"
        elif self.sm.is_state(SLEEP):
            kind = "sleep"
        elif not self.sampler.enabled:
            kind = "paused"
        elif self.sm.is_quiet:
            kind = "quiet"
        else:
            kind = "normal"
        if getattr(self, "_icon_kind", None) != kind:
            self._icon_kind = kind
            self.tray.set_icon(kind)

    # ==================================================================
    # 交互
    # ==================================================================
    def _on_pet_clicked(self) -> None:
        self.care.note_interaction()
        if self.sm.is_state(SLEEP):
            self._wake()
            self.show_bubble(lines.pick("welcome_back"))
            return
        if self.sm.is_state(GAME):
            return
        # 单击反应**按顺序轮换**而不是纯随机：连戳几下能明显感到每次都不一样，
        # 纯随机反而容易连着抽到同一个动作，看起来像"没反应"。
        idx = getattr(self, "_click_idx", 0)
        self._click_idx = idx + 1
        self.play(CLICK_CLIPS[idx % len(CLICK_CLIPS)], blend_ms=60)
        self.sm.request(ACT, source="click", force=True)
        if self.budget.can_speak() and getattr(self, "_clicks_since_speak", 0) >= 3:
            self._clicks_since_speak = 0
            self.show_bubble(lines.pick("click_fun"))
        else:
            self._clicks_since_speak = getattr(self, "_clicks_since_speak", 0) + 1

    def _on_drag_started(self) -> None:
        self.care.note_interaction()
        if self.sm.is_state(SLEEP):
            self._wake()
        self.sm.request(DRAG, source="drag", force=True)
        self.play("struggle")

    def _on_drag_finished(self) -> None:
        self.animator.stop(time.monotonic())
        self.sm.back_to_idle()

    def _wake(self) -> None:
        self.sm.back_to_idle()
        self.play("stretch")

    def _toggle_window(self) -> None:
        if self.pet.isVisible():
            self.pet.hide()
        else:
            self.pet.show()
            self.pet.raise_()

    def _set_pause_stats(self, on: bool) -> None:
        """暂停/恢复统计。入口在设置窗口里（原来是托盘菜单项）。"""
        if bool(on) == (not self.sampler.enabled):
            return
        if on:
            self.sampler.set_enabled(False)
            if self.hooks.running:
                self.hooks.stop()
        else:
            self.sampler.set_enabled(True)
            if config.get("collect_input", True) and not self.hooks.running:
                self.hooks.start()
        self.show_bubble("统计已暂停。" if on else "统计恢复了。")

    def _toggle_sleep(self, on: bool) -> None:
        if on:
            self.sm.request(SLEEP, source="manual", force=True)
            self.animator.play("sleep_loop", time.monotonic(), blend_ms=200)
        else:
            self._wake()
        self.tray.set_sleep_label(on)

    def _toggle_pomodoro(self, on: bool) -> None:
        if on:
            self.care.start_pomodoro(int(config.get("pomodoro_minutes", 25) or 25))
        else:
            self.care.stop_pomodoro()
        self.tray.set_check("pomodoro", self.care.pomodoro_active)

    # ==================================================================
    # 系统事件
    # ==================================================================
    def _on_flags(self, locked: bool, fullscreen: bool, meeting: bool) -> None:
        self.pet.set_fullscreen_hidden(bool(fullscreen))

    def _on_locked(self) -> None:
        self.sampler.stats.locked = True
        self.sampler.stats.sleep_gap_cnt += 1
        self.sampler.flush()
        log.info("会话已锁定")

    def _on_unlocked(self) -> None:
        self.sampler.stats.locked = False
        QTimer.singleShot(1500, self.scheduler.catch_up)
        log.info("会话已解锁")

    def _on_suspend(self) -> None:
        self.sampler.flush()
        log.info("系统即将休眠")

    def _on_resume(self) -> None:
        QTimer.singleShot(2000, self.scheduler.catch_up)
        log.info("系统已唤醒")

    # ==================================================================
    # 气泡
    # ==================================================================
    def show_bubble(self, text: str, urgent: bool = False, buttons: bool = False,
                    sticky: bool = False, skip_budget: bool = False) -> None:
        if not text:
            return
        quiet = bool(config.get("manual_quiet", False))
        if skip_budget:
            # 预算已在调用侧结算（CareManager 先 can_speak 再 record 后才 emit；
            # 游戏/设置等是用户操作引起的回应，本就不该占打扰预算）。
            # 这里若再查一次 can_speak，刚 record 完 last_at=now，间隔必然不满足，
            # 气泡永远发不出去——2026-09-21 修复的正是这个双重闸门。
            if quiet:
                return
        elif urgent:
            self.budget.record()
        elif not quiet:
            if not self.budget.can_speak():
                log.debug("打扰预算已用尽，跳过气泡：%s", text[:16])
                return
            self.budget.record()
        self.bubble.show_message(
            text, self.pet.head_global(), buttons=buttons, sticky=sticky
        )

    def _on_bubble_closed(self) -> None:
        self._current_reminder = None

    def _on_bubble_ack(self) -> None:
        r = self._current_reminder
        if r is not None and r.action in ("sleep",):
            self.sm.request(SLEEP, source="reminder", force=True)
            self.animator.play("sleep_loop", time.monotonic(), blend_ms=200)
            self.tray.set_sleep_label(True)
        self.show_bubble(lines.pick("remind_ack"))

    def _on_bubble_snooze(self) -> None:
        r = self._current_reminder
        if r is not None:
            self.scheduler.add_temp(self.store.make_snooze(r, r.snooze_min))
        self.show_bubble(lines.pick("remind_snooze"), skip_budget=True)

    # ==================================================================
    # 提醒
    # ==================================================================
    def _on_reminder(self, r, catch_up: bool, text: str) -> None:
        self._current_reminder = r

        if r.action == ACTION_SLEEP:
            summary = self.care.daily_summary_text()
            if summary:
                text = f"{text}\n{summary}"

        clip = action_to_clip(r.action)
        if r.action == ACTION_AUTO:
            clip = self._auto_clip_for_now()
        if clip and not self.sm.is_state(GAME):
            self.animator.rate = self.mood.effect.anim_rate
            self.animator.play(clip, time.monotonic(), blend_ms=140)
            self.sm.request(ACT, source="reminder", force=True)

        if int(r.sound or 0):
            self._play_sound()

        urgent = bool(r.urgent) or r.action == ACTION_SLEEP
        self.show_bubble(text, urgent=urgent, buttons=True, sticky=urgent or r.action == ACTION_SLEEP)

    def _auto_clip_for_now(self) -> str:
        h = time.localtime().tm_hour
        if h in (7, 8, 12, 13, 18, 19):
            return "eat"
        if h >= 22 or h < 6:
            return "yawn"
        return "nod"

    def _play_sound(self) -> None:
        if not config.get("sound_enabled", True):
            return
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass

    # ==================================================================
    # 小游戏
    # ==================================================================
    def _pet_center(self) -> tuple[float, float, float]:
        p = self.pet.body_global()
        return (float(p.x()), float(p.y()), float(self.pet.scale))

    def _start_game(self) -> None:
        if self.sm.is_state(SLEEP):
            self._wake()
        if self.game.start():
            self.sm.request(GAME, source="game", force=True)

    def _on_game_started(self) -> None:
        self.pet.raise_()
        self.tray.set_game_running(True, 0)

    def _on_game_ended(self, result: dict) -> None:
        self.sm.back_to_idle()
        self.tray.set_game_running(False)
        if result.get("new_record"):
            self.play("celebrate")
        elif result.get("hits", 0) + result.get("misses", 0) >= 6:
            self.play("nod")
        else:
            self.play("sad")
        dlg = ResultDialog(result)
        if dlg.exec() == 2:
            QTimer.singleShot(400, self._start_game)

    # ==================================================================
    # 面板
    # ==================================================================
    def _open_reminders(self) -> None:
        dlg = ReminderDialog(self.store)
        dlg.test_requested.connect(lambda r: self._on_reminder(r, False, r.message or "测试提醒"))
        dlg.exec()

    def _open_stats(self) -> None:
        panel = StatsPanel(self.storage, self.sampler, self.mood)
        panel.refresh()
        panel.exec()

    def _show_menu_at_pet(self) -> None:
        """右键点企鹅：把托盘那份菜单弹在光标处。

        先刷一遍状态行/勾选项，否则菜单里显示的还是上次的旧值。
        """
        self._refresh_tray()
        self.tray.set_sleep_label(self.sm.is_state(SLEEP))
        self.tray.set_game_running(self.game.active, self.game.score)
        self.tray.popup_at(QCursor.pos())

    def _refresh_tray(self) -> None:
        """弹菜单前把状态行/图标刷成当前值。"""
        try:
            st = self.sampler.stats
            suffix = ""
            if self.care.pomodoro_active:
                left = self.care.pomodoro_remaining()
                suffix = f" · 专注剩 {left // 60:02d}:{left % 60:02d}"
            self.tray.set_status_line(
                self._status_text(analyzer.fmt_duration(st.active_s), st.keystrokes, suffix)
            )
            self._refresh_tray_icon()
        except Exception as exc:
            log.debug("刷新托盘状态失败: %s", exc)

    def _open_settings(self) -> None:
        dlg = SettingsDialog()
        dlg.scale_changed.connect(self.pet.set_scale)
        dlg.clear_today.connect(self._clear_today)
        dlg.clear_all.connect(self._clear_all)
        dlg.open_reminders.connect(self._open_reminders)
        dlg.pause_stats.connect(self._set_pause_stats)
        dlg.set_paused(not self.sampler.enabled)
        if dlg.exec():
            self.budget.reconfigure()
            self.action_budget.reconfigure()
            self.care.tick()
            self.show_bubble("设置已保存。")

    def _clear_today(self) -> None:
        self.storage.clear_today()
        self.show_bubble("今天的记录清掉了。")

    def _clear_all(self) -> None:
        self.storage.clear_all()
        self.show_bubble("全部数据已清空。", skip_budget=True)

    def _create_shortcut(self) -> None:
        from app import shortcut

        lnk = shortcut.create_desktop_shortcut()
        if lnk is None:
            self.show_bubble("桌面快捷方式没建成，可以直接双击 start.bat 启动。")
            return
        self.show_bubble(f"已经在桌面放好快捷方式啦（{lnk.name}），以后双击它就行。")

    def _open_about(self) -> None:
        days = self.storage.days_since_install()
        box = QMessageBox()
        box.setWindowTitle("关于")
        box.setText(
            f"桌面企鹅 · 第 {days} 天\n\n"
            f"数据目录：{paths.DATA_DIR}\n"
            "程序不联网，所有统计只存在本机。\n"
            "键盘只记录次数与间隔，不记录任何字符内容。"
        )
        box.setStandardButtons(QMessageBox.Ok)
        box.exec()

    # ==================================================================
    def _shutdown(self) -> None:
        log.info("正在退出…")
        try:
            self._sec.stop()
            self.scheduler.stop()
            self.care.stop()
            self.game.stop(silent=True)
            self.sampler.stop()
            self.hooks.stop()
            self.storage.flush()
            self.storage.close()
            config.save()      # 把位置、缩放、开关等偏好落盘
        except Exception as exc:
            log.warning("退出清理异常: %s", exc)


def acquire_single_instance() -> tuple[bool, QLocalServer | None]:
    QLocalServer.removeServer(LOCK_NAME)
    server = QLocalServer()
    if not server.listen(LOCK_NAME):
        sock = QLocalSocket()
        sock.connectToServer(LOCK_NAME)
        if sock.waitForConnected(500):
            sock.write(b"show")
            sock.flush()
        return False, None
    return True, server


def main() -> int:
    paths.ensure_dirs()
    logging_setup.setup()
    logging_setup.install_excepthook()

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("桌面企鹅")
    app.setQuitOnLastWindowClosed(False)
    # 注：Qt6 里 AA_UseHighDpiPixmaps 已默认开启且被标记废弃，不要再设。

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.warning(None, "桌面企鹅", "系统托盘不可用，程序无法正常使用。")
        return 1

    ok, server = acquire_single_instance()
    if not ok:
        log.info("已有实例在运行，退出本次启动")
        return 0

    # 开机自启的命令里存的是绝对路径快照，项目搬家后会失效。
    # 这里自检一次：只在"用户确实开了自启、但路径已指不到当前程序"时才改写，
    # 避免出现"设置里显示已开启、开机却起不来"的情况（用户没开自启则不动）。
    state = paths.sync_autostart()
    if state == "repaired":
        log.info("开机自启指向的路径已失效，已自动更新为当前路径")
    elif state == "failed":
        log.warning("开机自启指向的路径已失效，且自动修复失败")

    pet_app = DeskPet(app)
    pet_app._server = server      # 必须持有引用，否则监听会被回收
    pet_app.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
