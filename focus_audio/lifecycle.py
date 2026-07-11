"""Grok session lifecycle: start daemon with Grok, stop when last session exits.

Uses a small ref file of active session IDs so multiple concurrent Grok
windows share one daemon and only the last quit tears it down.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .paths import data_dir


def refs_path() -> Path:
    return data_dir() / "session_refs.json"


def _empty_state() -> dict:
    return {"sessions": {}, "updated_at": time.time()}


def _read_unlocked(path: Path) -> dict:
    if not path.is_file():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_state()
        sessions = data.get("sessions") or {}
        if not isinstance(sessions, dict):
            sessions = {}
        data["sessions"] = sessions
        return data
    except (json.JSONDecodeError, OSError):
        return _empty_state()


def _write_unlocked(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    data = dict(data)
    data["updated_at"] = time.time()
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _with_lock(fn: Callable[[dict], dict]) -> Tuple[dict, dict]:
    """Run fn(state) under exclusive lock; returns (result, state)."""
    path = refs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with lock_path.open("a+", encoding="utf-8") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            state = _read_unlocked(path)
            result = fn(state)
            _write_unlocked(path, state)
            return result, state
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


def acquire_session(session_id: Optional[str] = None) -> dict:
    """Register a Grok session. Returns summary for hooks/CLI."""
    sid = (session_id or "").strip() or f"anon-{os.getpid()}-{int(time.time())}"

    def mut(state: dict) -> dict:
        sessions: Dict[str, float] = state.setdefault("sessions", {})
        sessions[sid] = time.time()
        return {"session_id": sid, "count": len(sessions), "acquired": True}

    result, state = _with_lock(mut)
    result["sessions"] = list(state.get("sessions", {}).keys())
    return result


def release_session(session_id: Optional[str] = None) -> dict:
    """Unregister a Grok session. count==0 means daemon should shut down."""
    sid = (session_id or "").strip()

    def mut(state: dict) -> dict:
        sessions: Dict[str, float] = state.setdefault("sessions", {})
        released = False
        used: Optional[str] = sid or None
        if sid and sid in sessions:
            del sessions[sid]
            released = True
        elif not sid and sessions:
            # Best-effort if hook omitted session id
            used = min(sessions.items(), key=lambda kv: kv[1])[0]
            del sessions[used]
            released = True
        return {
            "session_id": used,
            "count": len(sessions),
            "released": released,
        }

    result, state = _with_lock(mut)
    result["sessions"] = list(state.get("sessions", {}).keys())
    return result


def active_count() -> int:
    state = _read_unlocked(refs_path())
    return len(state.get("sessions") or {})


def list_sessions() -> List[str]:
    state = _read_unlocked(refs_path())
    return list((state.get("sessions") or {}).keys())


def clear_all() -> None:
    def mut(state: dict) -> dict:
        state["sessions"] = {}
        return {"count": 0}

    _with_lock(mut)
