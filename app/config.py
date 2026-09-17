"""用户配置：一个 JSON 文件，点号路径读写，改动自动落盘。

刻意保持简单——配置项不多，没必要上框架。
"""
from __future__ import annotations

import copy
import json
import threading
from typing import Any

from . import logging_setup
from .paths import CONFIG_PATH, ensure_dirs

log = logging_setup.get("config")

DEFAULTS: dict[str, Any] = {
    # 首次运行
    "consent": False,
    "builtin_removed": False,

    # 外观
    "pet_scale": 1.0,
    "pos": None,
    "click_through_mode": "auto",     # auto | nchittest | exstyle

    # 打扰强度（安静型默认）
    "bubble_per_day": 8,
    "bubble_min_gap_min": 8,
    "random_action_per_hour": 3,

    # 作息
    "sleep_enabled": True,
    "sleep_start": "23:30",
    "sleep_end": "07:00",
    "auto_sleep_min": 30,

    # 隐私
    "collect_input": True,
    "collect_window_title": True,
    "app_category_override": {},

    # 声音
    "sound_enabled": True,
    "sound_volume": 30,

    # 番茄钟
    "pomodoro_minutes": 25,

    # 系统
    "autostart": False,
}

_lock = threading.RLock()


class Config:
    def __init__(self) -> None:
        self._data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.load()

    # ------------------------------------------------------------ 读写
    def load(self) -> None:
        ensure_dirs()
        try:
            if CONFIG_PATH.exists():
                raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    merged = copy.deepcopy(DEFAULTS)
                    merged.update(raw)
                    self._data = merged
        except Exception as exc:
            log.warning("配置文件读取失败，使用默认值: %s", exc)

    def save(self) -> None:
        with _lock:
            try:
                ensure_dirs()
                tmp = CONFIG_PATH.with_suffix(".json.tmp")
                tmp.write_text(
                    json.dumps(self._data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                tmp.replace(CONFIG_PATH)
            except Exception as exc:
                log.warning("配置保存失败: %s", exc)

    # ------------------------------------------------------------ 访问
    def get(self, key: str, default: Any = None) -> Any:
        if key in self._data:
            return self._data[key]
        if default is not None:
            return default
        return DEFAULTS.get(key, default)

    def set(self, key: str, value: Any, save: bool = False) -> None:
        with _lock:
            if self._data.get(key) == value:
                return
            self._data[key] = value
        if save:
            self.save()

    def update(self, values: dict[str, Any], save: bool = True) -> None:
        with _lock:
            self._data.update(values)
        if save:
            self.save()

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    def reset(self) -> None:
        with _lock:
            self._data = copy.deepcopy(DEFAULTS)
        self.save()


config = Config()
