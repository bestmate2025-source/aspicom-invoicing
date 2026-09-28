"""Centralized logging setup."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

_CONFIGURED_LOGGERS: set[str] = set()


def get_logger(name: str, config: dict[str, Any], verbose: bool = False) -> logging.Logger:
    """Return a configured logger that writes to both console and a daily log file."""
    logger = logging.getLogger(name)

    if name in _CONFIGURED_LOGGERS:
        return logger

    log_cfg = config.get("logging", {}) if isinstance(config, dict) else {}
    level_name = log_cfg.get("level", "INFO")
    base_level = getattr(logging, level_name.upper(), logging.INFO)
    logger.setLevel(logging.DEBUG)

    fmt = log_cfg.get("format", "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    date_fmt = log_cfg.get("date_format", "%Y-%m-%d %H:%M:%S")
    formatter = logging.Formatter(fmt, datefmt=date_fmt)

    logs_dir = Path(log_cfg.get("dir", "logs"))
    logs_dir.mkdir(parents=True, exist_ok=True)

    filename_pattern = log_cfg.get("filename_pattern", "invoice_%Y%m%d.log")
    log_filename = datetime.now().strftime(filename_pattern)
    file_path = logs_dir / log_filename

    file_handler = logging.FileHandler(file_path, encoding="utf-8")
    file_handler.setLevel(base_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else base_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    logger.propagate = False
    _CONFIGURED_LOGGERS.add(name)

    return logger