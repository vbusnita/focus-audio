"""Filesystem layout for Focus Audio state."""

from __future__ import annotations

import os
from pathlib import Path


def grok_home() -> Path:
    raw = os.environ.get("GROK_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".grok"


def data_dir() -> Path:
    """Writable runtime data (queue, cache, socket, config)."""
    d = grok_home() / "focus-audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_dir() -> Path:
    d = data_dir() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return data_dir() / "config.toml"


def socket_path() -> Path:
    return data_dir() / "daemon.sock"


def pid_path() -> Path:
    return data_dir() / "daemon.pid"


def last_brief_path() -> Path:
    return data_dir() / "last_brief.md"


def last_job_path() -> Path:
    return data_dir() / "last_job.json"


def sessions_root() -> Path:
    return grok_home() / "sessions"
