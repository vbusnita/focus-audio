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

from .paths import data_dir, secure_chmod_file, secure_mkdir, secure_write_text

# Stale refs accumulate when SessionEnd hooks miss (crashes, force-quit).
# Default TTL keeps ensure from counting long-dead Grok windows forever.
DEFAULT_SESSION_TTL_S = 6 * 3600  # 6 hours


def refs_path() -> Path:
    return data_dir() / "session_refs.json"


def _empty_state() -> dict:
    return {"sessions": {}, "updated_at": time.time()}


def _prune_sessions_inplace(
    sessions: Dict[str, float],
    *,
    max_age_s: float,
    now: Optional[float] = None,
) -> List[str]:
    """Drop entries older than max_age_s. Returns pruned session ids."""
    if max_age_s <= 0 or not sessions:
        return []
    t = float(now if now is not None else time.time())
    cutoff = t - float(max_age_s)
    pruned: List[str] = []
    for sid, ts in list(sessions.items()):
        try:
            age_anchor = float(ts)
        except (TypeError, ValueError):
            pruned.append(sid)
            del sessions[sid]
            continue
        if age_anchor < cutoff:
            pruned.append(sid)
            del sessions[sid]
    return pruned


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
    secure_mkdir(path.parent)
    tmp = path.with_suffix(".tmp")
    data = dict(data)
    data["updated_at"] = time.time()
    secure_write_text(tmp, json.dumps(data, indent=2) + "\n")
    tmp.replace(path)
    secure_chmod_file(path)


def _with_lock(fn: Callable[[dict], dict]) -> Tuple[dict, dict]:
    """Run fn(state) under exclusive lock; returns (result, state)."""
    path = refs_path()
    secure_mkdir(path.parent)
    lock_path = path.with_suffix(".lock")
    with lock_path.open("a+", encoding="utf-8") as lockf:
        secure_chmod_file(lock_path)
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            state = _read_unlocked(path)
            result = fn(state)
            _write_unlocked(path, state)
            return result, state
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


def acquire_session(
    session_id: Optional[str] = None,
    *,
    max_age_s: float = DEFAULT_SESSION_TTL_S,
) -> dict:
    """Register a Grok session. Returns summary for hooks/CLI.

    Also prunes refs older than max_age_s so missed SessionEnd hooks do not
    pin the daemon refcount forever.
    """
    sid = (session_id or "").strip() or f"anon-{os.getpid()}-{int(time.time())}"

    def mut(state: dict) -> dict:
        sessions: Dict[str, float] = state.setdefault("sessions", {})
        pruned = _prune_sessions_inplace(sessions, max_age_s=max_age_s)
        sessions[sid] = time.time()
        return {
            "session_id": sid,
            "count": len(sessions),
            "acquired": True,
            "pruned": pruned,
            "pruned_count": len(pruned),
        }

    result, state = _with_lock(mut)
    result["sessions"] = list(state.get("sessions", {}).keys())
    return result


def release_session(
    session_id: Optional[str] = None,
    *,
    max_age_s: float = DEFAULT_SESSION_TTL_S,
) -> dict:
    """Unregister a Grok session. count==0 means daemon should shut down."""
    sid = (session_id or "").strip()

    def mut(state: dict) -> dict:
        sessions: Dict[str, float] = state.setdefault("sessions", {})
        pruned = _prune_sessions_inplace(sessions, max_age_s=max_age_s)
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
            "pruned": pruned,
            "pruned_count": len(pruned),
        }

    result, state = _with_lock(mut)
    result["sessions"] = list(state.get("sessions", {}).keys())
    return result


def prune_stale_sessions(
    *,
    max_age_s: float = DEFAULT_SESSION_TTL_S,
    now: Optional[float] = None,
) -> dict:
    """Explicit prune (doctor / CLI). Does not acquire a session."""

    def mut(state: dict) -> dict:
        sessions: Dict[str, float] = state.setdefault("sessions", {})
        pruned = _prune_sessions_inplace(sessions, max_age_s=max_age_s, now=now)
        return {
            "count": len(sessions),
            "pruned": pruned,
            "pruned_count": len(pruned),
            "max_age_s": max_age_s,
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
