"""日志：滚动文件 + 控制台（仅源码运行时）。"""
from __future__ import annotations

import logging
import logging.handlers
import sys
import traceback

from .paths import LOG_DIR, ensure_dirs

_LOGGER_NAME = "deskpet"
_initialized = False


def setup(level: int = logging.INFO, console: bool = True) -> logging.Logger:
    global _initialized
    logger = logging.getLogger(_LOGGER_NAME)
    if _initialized:
        return logger

    ensure_dirs()
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    fh = logging.handlers.RotatingFileHandler(
        LOG_DIR / "pet.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    if console and sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(level)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    _initialized = True
    return logger


def get(name: str = "") -> logging.Logger:
    return logging.getLogger(f"{_LOGGER_NAME}.{name}" if name else _LOGGER_NAME)


def install_excepthook() -> None:
    """未捕获异常写日志，而不是静默死掉。"""
    logger = get("excepthook")

    def hook(exc_type, exc, tb):  # noqa: ANN001
        if issubclass(exc_type, KeyboardInterrupt):
            return
        logger.critical("未捕获异常:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))

    sys.excepthook = hook
