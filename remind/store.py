"""提醒的增删改查。"""
from __future__ import annotations

import time

from app import logging_setup
from monitor.storage import Storage
from .model import ALL_WEEKDAYS, ACTION_EAT, ACTION_NONE, ACTION_SLEEP, KIND_DAILY, Reminder

log = logging_setup.get("remind.store")

_FIELDS = (
    "kind", "time_hhmm", "weekdays", "date_key", "message", "action",
    "sound", "snooze_min", "enabled", "urgent", "builtin",
    "last_fired_key", "created_ts",
)

# 三条内置提醒：到点就该被关心的事
BUILTINS = [
    Reminder(
        kind=KIND_DAILY, time_hhmm="12:00",
        message="该吃饭啦～好好吃一顿，别对着屏幕扒饭哦",
        action=ACTION_EAT, sound=1, builtin=True, snooze_min=15,
    ),
    Reminder(
        kind=KIND_DAILY, time_hhmm="18:00",
        message="六点了，该吃饭啦！今天辛苦了",
        action=ACTION_EAT, sound=1, builtin=True, snooze_min=15,
    ),
    Reminder(
        kind=KIND_DAILY, time_hhmm="22:00",
        message="该休息了，早点睡，明天再战～",
        action=ACTION_SLEEP, sound=1, builtin=True, snooze_min=20,
    ),
]


class ReminderStore:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    # ------------------------------------------------------------ 读
    def all(self) -> list[Reminder]:
        cur = self.storage.conn.execute(
            "SELECT * FROM reminders ORDER BY time_hhmm, id"
        )
        return [Reminder.from_row(dict(r)) for r in cur.fetchall()]

    def enabled(self) -> list[Reminder]:
        return [r for r in self.all() if r.enabled]

    def get(self, rid: int) -> Reminder | None:
        row = self.storage.conn.execute(
            "SELECT * FROM reminders WHERE id=?", (int(rid),)
        ).fetchone()
        return Reminder.from_row(dict(row)) if row else None

    def count(self) -> int:
        row = self.storage.conn.execute("SELECT COUNT(*) AS c FROM reminders").fetchone()
        return int(row["c"] or 0)

    # ------------------------------------------------------------ 写
    def add(self, r: Reminder) -> int:
        r.clamp_time()
        if not r.created_ts:
            r.created_ts = int(time.time())
        row = r.to_row()
        cols = ", ".join(_FIELDS)
        ph = ", ".join(["?"] * len(_FIELDS))
        cur = self.storage.conn.execute(
            f"INSERT INTO reminders({cols}) VALUES({ph})",
            tuple(row[c] for c in _FIELDS),
        )
        self.storage.conn.commit()
        r.id = int(cur.lastrowid or 0)
        return r.id

    def update(self, r: Reminder) -> None:
        if r.id is None:
            self.add(r)
            return
        r.clamp_time()
        row = r.to_row()
        sets = ", ".join(f"{c}=?" for c in _FIELDS)
        self.storage.conn.execute(
            f"UPDATE reminders SET {sets} WHERE id=?",
            tuple(row[c] for c in _FIELDS) + (int(r.id),),
        )
        self.storage.conn.commit()

    def set_fired(self, r: Reminder, key: str) -> None:
        r.last_fired_key = key
        if r.id is not None:
            self.storage.conn.execute(
                "UPDATE reminders SET last_fired_key=? WHERE id=?", (key, int(r.id))
            )
            self.storage.conn.commit()

    def delete(self, rid: int, mark_removed: bool = False) -> None:
        self.storage.conn.execute("DELETE FROM reminders WHERE id=?", (int(rid),))
        self.storage.conn.commit()
        if mark_removed:
            # 记住"用户删过内置提醒"，避免下次启动又给他塞回来
            self.storage.set_meta("builtin_removed", True)

    def ensure_builtins(self) -> int:
        """首次运行写入三条内置提醒。用户删过就不再重建。"""
        if self.storage.get_meta("builtin_removed"):
            return 0
        if self.count() > 0:
            return 0
        for r in BUILTINS:
            self.add(Reminder(**{**r.__dict__}))
        log.info("已写入 %d 条内置提醒", len(BUILTINS))
        return len(BUILTINS)

    def restore_builtins(self) -> int:
        """用户手动恢复预设。已存在的时间点不重复添加。"""
        existing = {r.time_hhmm for r in self.all()}
        added = 0
        for r in BUILTINS:
            if r.time_hhmm in existing:
                continue
            self.add(Reminder(**{**r.__dict__}))
            added += 1
        self.storage.set_meta("builtin_removed", False)
        return added

    def make_snooze(self, r: Reminder, minutes: int) -> Reminder:
        """生成一个"稍后再提醒"，不落库，避免把提醒列表搞乱。"""
        now = time.localtime()
        total = now.tm_hour * 60 + now.tm_min + max(1, int(minutes))
        hh = (total // 60) % 24
        mm = total % 60
        s = Reminder(
            kind=KIND_DAILY,
            time_hhmm=f"{hh:02d}:{mm:02d}",
            message=r.message,
            action=ACTION_NONE if r.action == ACTION_EAT else r.action,
            sound=0,
            snooze_min=r.snooze_min,
            builtin=False,
            id=-1,   # -1 表示临时项，不写库
        )
        return s

    def new_draft(self) -> Reminder:
        r = Reminder(kind=KIND_DAILY, time_hhmm="09:00", message="", action="auto")
        r.weekdays = ALL_WEEKDAYS
        return r
