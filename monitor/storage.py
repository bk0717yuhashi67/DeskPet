"""SQLite 存储层（WAL）。

为什么用 SQLite 而不是 JSON：本项目是"高频追加 + 按时间范围聚合 + 崩溃安全"，
JSON 每次都要重写整份文件（写放大），崩溃容易截断损坏，聚合还得全量扫描内存。
SQLite 单次写入微秒级、断电不损坏、GROUP BY 直接出报表。

表结构分工：
    sessions      会话级明细（不是每秒一行），保留 90 天
    daily_stat    每日总览，flush 时 UPSERT 累加，永久保留
    app_daily     应用维度日聚合，永久保留
    focus_block   ≥10 分钟无切换的专注块
    metrics_daily 指标缓存，避免每次开面板重算 30 天
    reminders     提醒配置
    events        成就与事件流水
    meta          安装日期 / 同意状态 / 最高分 / 上次检查时间
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from app import logging_setup
from app.paths import DB_PATH, ensure_dirs

log = logging_setup.get("storage")

SCHEMA_VERSION = 1
DETAIL_RETENTION_DAYS = 90

_DDL = """
CREATE TABLE IF NOT EXISTS sessions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    pid INTEGER, exe TEXT, app_name TEXT, category TEXT, title TEXT,
    start_ts INTEGER, end_ts INTEGER, duration_s INTEGER,
    idle_s INTEGER DEFAULT 0, locked_s INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_day ON sessions(day);

CREATE TABLE IF NOT EXISTS daily_stat(
    day TEXT PRIMARY KEY,
    active_s INTEGER DEFAULT 0, idle_s INTEGER DEFAULT 0, locked_s INTEGER DEFAULT 0,
    keystrokes INTEGER DEFAULT 0, backspaces INTEGER DEFAULT 0,
    clicks INTEGER DEFAULT 0, mouse_dist_px INTEGER DEFAULT 0,
    app_switches INTEGER DEFAULT 0,
    first_ts INTEGER, last_ts INTEGER,
    focus_block_cnt INTEGER DEFAULT 0, longest_focus_s INTEGER DEFAULT 0,
    late_minutes INTEGER DEFAULT 0, sleep_gap_cnt INTEGER DEFAULT 0,
    idle_gap_cnt INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS app_daily(
    day TEXT NOT NULL, exe TEXT NOT NULL,
    app_name TEXT, category TEXT,
    seconds INTEGER DEFAULT 0, switches INTEGER DEFAULT 0,
    PRIMARY KEY(day, exe)
);
CREATE INDEX IF NOT EXISTS idx_app_daily_day ON app_daily(day);

CREATE TABLE IF NOT EXISTS focus_block(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL, start_ts INTEGER, end_ts INTEGER, duration_s INTEGER,
    top_exe TEXT, top_app TEXT, keystrokes INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_focus_day ON focus_block(day);

CREATE TABLE IF NOT EXISTS metrics_daily(
    day TEXT PRIMARY KEY,
    focus REAL DEFAULT 0, fatigue REAL DEFAULT 0, activity REAL DEFAULT 0,
    stayup REAL DEFAULT 0, distraction REAL DEFAULT 0,
    backspace_rate REAL DEFAULT 0, flow REAL DEFAULT 0, mood TEXT
);

CREATE TABLE IF NOT EXISTS reminders(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'daily',
    time_hhmm TEXT NOT NULL,
    weekdays INTEGER DEFAULT 127,
    date_key TEXT,
    message TEXT DEFAULT '',
    action TEXT DEFAULT 'auto',
    sound INTEGER DEFAULT 0,
    snooze_min INTEGER DEFAULT 10,
    enabled INTEGER DEFAULT 1,
    urgent INTEGER DEFAULT 0,
    builtin INTEGER DEFAULT 0,
    last_fired_key TEXT,
    created_ts INTEGER
);

CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER, type TEXT, payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type, ts);

CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


def _dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def _loads(s: str | None, default: Any = None) -> Any:
    if s is None:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


class Storage:
    def __init__(self, path: Path = DB_PATH) -> None:
        ensure_dirs()
        self.path = Path(path)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.batch = 0
        self._migrate()
        self._bootstrap_meta()

    # ------------------------------------------------------------ 初始化
    def _migrate(self) -> None:
        self.conn.executescript(_DDL)
        self.conn.commit()

    def _bootstrap_meta(self) -> None:
        if self.get_meta("schema_version") is None:
            self.set_meta("schema_version", SCHEMA_VERSION)
            self.set_meta("install_date", _today())
        if self.get_meta("total_active_s") is None:
            self.set_meta("total_active_s", 0)

    def close(self) -> None:
        try:
            self.conn.commit()
            self.conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------ meta
    def get_meta(self, key: str, default: Any = None) -> Any:
        cur = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,))
        row = cur.fetchone()
        return default if row is None else _loads(row["value"], default)

    def set_meta(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, _dumps(value)),
        )
        self._touch()

    # ------------------------------------------------------------ 写入
    def _touch(self) -> None:
        self.batch += 1
        if self.batch >= 20:
            self.conn.commit()
            self.batch = 0

    def flush(self) -> None:
        try:
            self.conn.commit()
            self.batch = 0
        except Exception as exc:
            log.warning("flush 失败: %s", exc)

    def add_session(
        self,
        day: str,
        pid: int,
        exe: str,
        app_name: str,
        category: str,
        title: str,
        start_ts: int,
        end_ts: int,
        duration_s: int,
        idle_s: int = 0,
        locked_s: int = 0,
    ) -> None:
        self.conn.execute(
            "INSERT INTO sessions(day, pid, exe, app_name, category, title,"
            " start_ts, end_ts, duration_s, idle_s, locked_s)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (day, pid, exe, app_name, category, (title or "")[:300],
             start_ts, end_ts, duration_s, idle_s, locked_s),
        )
        self._touch()

    def bump_daily(self, day: str, **deltas: int) -> None:
        """按天累加。first_ts / last_ts 单独处理，取最小 / 最大。"""
        allowed = (
            "active_s", "idle_s", "locked_s", "keystrokes", "backspaces",
            "clicks", "mouse_dist_px", "app_switches", "focus_block_cnt",
            "longest_focus_s", "late_minutes", "sleep_gap_cnt", "idle_gap_cnt",
        )
        cols = [k for k in allowed if k in deltas]
        vals = [int(deltas[k]) for k in cols]
        first_ts = deltas.get("first_ts")
        last_ts = deltas.get("last_ts")

        col_sql = ", ".join(cols + ["first_ts", "last_ts"]) if cols else "first_ts, last_ts"
        ph = ", ".join(["?"] * (len(cols) + 2))
        sql = f"INSERT INTO daily_stat(day, {col_sql}) VALUES(?, {ph})"
        params: list[Any] = [day] + vals + [first_ts, last_ts]

        upd = [f"{c} = {c} + excluded.{c}" for c in cols]
        upd.append(
            "first_ts = CASE WHEN daily_stat.first_ts IS NULL THEN excluded.first_ts"
            " WHEN excluded.first_ts IS NULL THEN daily_stat.first_ts"
            " ELSE MIN(daily_stat.first_ts, excluded.first_ts) END"
        )
        upd.append(
            "last_ts = CASE WHEN daily_stat.last_ts IS NULL THEN excluded.last_ts"
            " WHEN excluded.last_ts IS NULL THEN daily_stat.last_ts"
            " ELSE MAX(daily_stat.last_ts, excluded.last_ts) END"
        )
        sql += " ON CONFLICT(day) DO UPDATE SET " + ", ".join(upd)
        self.conn.execute(sql, params)
        self._touch()

    def bump_app(
        self, day: str, exe: str, app_name: str, category: str,
        seconds: int, switches: int = 0,
    ) -> None:
        self.conn.execute(
            "INSERT INTO app_daily(day, exe, app_name, category, seconds, switches)"
            " VALUES(?,?,?,?,?,?)"
            " ON CONFLICT(day, exe) DO UPDATE SET"
            " seconds = seconds + excluded.seconds,"
            " switches = switches + excluded.switches,"
            " app_name = excluded.app_name, category = excluded.category",
            (day, exe or "unknown", app_name, category, int(seconds), int(switches)),
        )
        self._touch()

    def add_focus_block(
        self, day: str, start_ts: int, end_ts: int,
        top_exe: str, top_app: str, keystrokes: int = 0,
    ) -> None:
        dur = max(0, int(end_ts - start_ts))
        self.conn.execute(
            "INSERT INTO focus_block(day, start_ts, end_ts, duration_s, top_exe, top_app, keystrokes)"
            " VALUES(?,?,?,?,?,?,?)",
            (day, int(start_ts), int(end_ts), dur, top_exe, top_app, int(keystrokes)),
        )
        self._touch()

    def set_metrics(self, day: str, m: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO metrics_daily(day, focus, fatigue, activity, stayup,"
            " distraction, backspace_rate, flow, mood)"
            " VALUES(?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(day) DO UPDATE SET focus=excluded.focus,"
            " fatigue=excluded.fatigue, activity=excluded.activity,"
            " stayup=excluded.stayup, distraction=excluded.distraction,"
            " backspace_rate=excluded.backspace_rate, flow=excluded.flow,"
            " mood=excluded.mood",
            (
                day,
                float(m.get("focus", 0)), float(m.get("fatigue", 0)),
                float(m.get("activity", 0)), float(m.get("stayup", 0)),
                float(m.get("distraction", 0)), float(m.get("backspace_rate", 0)),
                float(m.get("flow", 0)), str(m.get("mood", "")),
            ),
        )
        self._touch()

    def add_event(self, type_: str, payload: Any = None, ts: int | None = None) -> None:
        self.conn.execute(
            "INSERT INTO events(ts, type, payload) VALUES(?,?,?)",
            (int(ts if ts is not None else time.time()), type_, _dumps(payload)),
        )
        self._touch()

    # ------------------------------------------------------------ 读取
    def daily(self, day: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM daily_stat WHERE day=?", (day,)).fetchone()
        return dict(row) if row else None

    def daily_range(self, days: int, end_day: str | None = None) -> list[dict[str, Any]]:
        end = end_day or _today()
        cur = self.conn.execute(
            "SELECT * FROM daily_stat WHERE day <= ? ORDER BY day DESC LIMIT ?",
            (end, int(days)),
        )
        return [dict(r) for r in cur.fetchall()][::-1]

    def metrics(self, day: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM metrics_daily WHERE day=?", (day,)).fetchone()
        return dict(row) if row else None

    def metrics_range(self, days: int) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM metrics_daily ORDER BY day DESC LIMIT ?", (int(days),)
        )
        return [dict(r) for r in cur.fetchall()][::-1]

    def top_apps(self, day: str, limit: int = 8) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT app_name, exe, category, seconds, switches FROM app_daily"
            " WHERE day=? ORDER BY seconds DESC LIMIT ?",
            (day, int(limit)),
        )
        return [dict(r) for r in cur.fetchall()]

    def app_seconds(self, day: str, exe: str) -> int:
        row = self.conn.execute(
            "SELECT seconds FROM app_daily WHERE day=? AND exe=?", (day, exe)
        ).fetchone()
        return int(row["seconds"]) if row else 0

    def all_apps(self, day: str) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT app_name, exe, category, seconds FROM app_daily"
            " WHERE day=? ORDER BY seconds DESC",
            (day,),
        )
        return [dict(r) for r in cur.fetchall()]

    def category_totals(self, day: str) -> dict[str, int]:
        cur = self.conn.execute(
            "SELECT category, SUM(seconds) AS s FROM app_daily WHERE day=? GROUP BY category",
            (day,),
        )
        return {r["category"] or "其他": int(r["s"] or 0) for r in cur.fetchall()}

    def sessions_of(self, day: str, limit: int = 400) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM sessions WHERE day=? ORDER BY start_ts LIMIT ?", (day, int(limit))
        )
        return [dict(r) for r in cur.fetchall()]

    def recent_titles(self, day: str, limit: int = 200) -> list[str]:
        cur = self.conn.execute(
            "SELECT title FROM sessions WHERE day=? AND title != '' ORDER BY id DESC LIMIT ?",
            (day, int(limit)),
        )
        return [r["title"] for r in cur.fetchall()]

    def focus_blocks_of(self, day: str) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM focus_block WHERE day=? ORDER BY start_ts", (day,)
        )
        return [dict(r) for r in cur.fetchall()]

    def longest_focus(self, day: str) -> int:
        row = self.conn.execute(
            "SELECT MAX(duration_s) AS d FROM focus_block WHERE day=?", (day,)
        ).fetchone()
        return int(row["d"] or 0) if row else 0

    def focus_count(self, day: str, min_s: int = 600) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM focus_block WHERE day=? AND duration_s>=?",
            (day, int(min_s)),
        ).fetchone()
        return int(row["c"] or 0) if row else 0

    def total_active_seconds(self) -> int:
        row = self.conn.execute("SELECT SUM(active_s) AS s FROM daily_stat").fetchone()
        return int(row["s"] or 0) if row else 0

    def distinct_days(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM daily_stat").fetchone()
        return int(row["c"] or 0) if row else 0

    def install_date(self) -> str:
        v = self.get_meta("install_date")
        return v if isinstance(v, str) else _today()

    def streak_days(self) -> int:
        """从今天往前连续有多少天打开过。"""
        cur = self.conn.execute("SELECT day FROM daily_stat ORDER BY day DESC LIMIT 400")
        days = [r["day"] for r in cur.fetchall()]
        if not days:
            return 0
        streak = 0
        expect = _today()
        for d in days:
            if d == expect:
                streak += 1
                expect = _shift_day(expect, -1)
            elif d < expect:
                break
        return streak

    def days_since_install(self) -> int:
        cur = self.conn.execute(
            "SELECT CAST(julianday(?) - julianday(?) AS INTEGER) AS d",
            (_today(), self.install_date()),
        )
        row = cur.fetchone()
        return max(0, int(row["d"] or 0)) + 1 if row else 1

    # ------------------------------------------------------------ 维护
    def purge_old(self, days: int = DETAIL_RETENTION_DAYS) -> None:
        cutoff = _shift_day(_today(), -int(days))
        try:
            self.conn.execute(
                "DELETE FROM sessions WHERE day < ?", (cutoff,)
            )
            self.conn.execute("DELETE FROM focus_block WHERE day < ?", (cutoff,))
            self.conn.commit()
        except Exception as exc:
            log.warning("清理历史明细失败: %s", exc)

    def vacuum(self) -> None:
        try:
            self.conn.execute("VACUUM")
            self.conn.commit()
        except Exception as exc:
            log.warning("VACUUM 失败: %s", exc)

    def clear_all(self, keep_meta: bool = True) -> None:
        """一键清除所有使用数据（提醒配置默认保留）。"""
        for t in ("sessions", "daily_stat", "app_daily", "focus_block", "metrics_daily", "events"):
            self.conn.execute(f"DELETE FROM {t}")
        if not keep_meta:
            self.conn.execute("DELETE FROM reminders")
            self.conn.execute("DELETE FROM meta")
        self.conn.commit()
        self.vacuum()

    def clear_today(self) -> None:
        day = _today()
        for t in ("sessions", "app_daily", "focus_block", "metrics_daily"):
            self.conn.execute(f"DELETE FROM {t} WHERE day=?", (day,))
        self.conn.execute("DELETE FROM daily_stat WHERE day=?", (day,))
        self.conn.commit()

    def db_size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0


# ---------------------------------------------------------------- 日期工具
def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _shift_day(day: str, delta: int) -> str:
    try:
        t = time.mktime(time.strptime(day, "%Y-%m-%d")) + delta * 86400
        return time.strftime("%Y-%m-%d", time.localtime(t))
    except Exception:
        return day


def day_of(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def iter_days(days: Iterable[str]) -> list[str]:
    return list(days)
