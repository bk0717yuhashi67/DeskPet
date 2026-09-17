"""提醒数据结构。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

KIND_ONCE = "once"
KIND_DAILY = "daily"
KIND_WEEKLY = "weekly"

ACTION_AUTO = "auto"
ACTION_EAT = "eat"
ACTION_STRETCH = "stretch"
ACTION_WAVE = "wave"
ACTION_YAWN = "yawn"
ACTION_SLEEP = "sleep"
ACTION_NONE = "none"

ACTION_LABELS = [
    (ACTION_AUTO, "自动挑一个"),
    (ACTION_EAT, "吃东西"),
    (ACTION_STRETCH, "伸懒腰"),
    (ACTION_WAVE, "挥手"),
    (ACTION_YAWN, "打哈欠"),
    (ACTION_SLEEP, "去睡觉"),
    (ACTION_NONE, "不做动作"),
]

WEEKDAY_LABELS = ["一", "二", "三", "四", "五", "六", "日"]
ALL_WEEKDAYS = 0b1111111


@dataclass
class Reminder:
    id: int | None = None
    kind: str = KIND_DAILY
    time_hhmm: str = "12:00"
    weekdays: int = ALL_WEEKDAYS   # bit0=周一 … bit6=周日
    date_key: str | None = None    # 仅 once 使用
    message: str = ""
    action: str = ACTION_AUTO
    sound: int = 0
    snooze_min: int = 10
    enabled: bool = True
    urgent: bool = False
    builtin: bool = False
    last_fired_key: str | None = None
    created_ts: int = 0

    # ---------------- 序列化 ----------------
    def to_row(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "time_hhmm": self.time_hhmm,
            "weekdays": int(self.weekdays),
            "date_key": self.date_key,
            "message": self.message,
            "action": self.action,
            "sound": int(self.sound),
            "snooze_min": int(self.snooze_min),
            "enabled": 1 if self.enabled else 0,
            "urgent": 1 if self.urgent else 0,
            "builtin": 1 if self.builtin else 0,
            "last_fired_key": self.last_fired_key,
            "created_ts": int(self.created_ts),
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Reminder":
        return cls(
            id=int(row["id"]) if row.get("id") is not None else None,
            kind=row.get("kind") or KIND_DAILY,
            time_hhmm=row.get("time_hhmm") or "12:00",
            weekdays=int(row.get("weekdays") or ALL_WEEKDAYS),
            date_key=row.get("date_key"),
            message=row.get("message") or "",
            action=row.get("action") or ACTION_AUTO,
            sound=int(row.get("sound") or 0),
            snooze_min=int(row.get("snooze_min") or 10),
            enabled=bool(row.get("enabled", 1)),
            urgent=bool(row.get("urgent", 0)),
            builtin=bool(row.get("builtin", 0)),
            last_fired_key=row.get("last_fired_key"),
            created_ts=int(row.get("created_ts") or 0),
        )

    # ---------------- 展示 ----------------
    def time_text(self) -> str:
        return self.time_hhmm

    def repeat_text(self) -> str:
        if self.kind == KIND_ONCE:
            return f"仅一次 {self.date_key or ''}".strip()
        if self.kind == KIND_WEEKLY:
            days = [WEEKDAY_LABELS[i] for i in range(7) if self.weekdays >> i & 1]
            return "每周" + "".join(days) if days else "每周（未选）"
        return "每天"

    def summary(self) -> str:
        flag = "" if self.enabled else "（已关）"
        return f"{self.time_hhmm}  {self.message or '(无内容)'}{flag}"

    def dedup_key(self, day: str) -> str:
        return f"{self.id}:{day}:{self.time_hhmm}"

    def is_rest_related(self) -> bool:
        return self.action in (ACTION_SLEEP, ACTION_YAWN) or self.urgent

    def grace_minutes(self) -> int:
        if self.action == ACTION_EAT:
            return 90
        if self.action in (ACTION_SLEEP, ACTION_YAWN):
            return 60
        return 30

    def clamp_time(self) -> None:
        try:
            hh, mm = self.time_hhmm.split(":")
            h = max(0, min(23, int(hh)))
            m = max(0, min(59, int(mm)))
            self.time_hhmm = f"{h:02d}:{m:02d}"
        except Exception:
            self.time_hhmm = "12:00"
