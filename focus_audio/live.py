"""Experimental live verbatim: speak agent_message_chunk events as they land.

Tails ``updates.jsonl`` for a Grok session during an open turn and yields
cleaned speakable segments (no brief LLM rewrite).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, List, Optional

from .config import Config
from .transcript import (
    UpdatesTail,
    clean_for_audio,
    find_session_dir,
    updates_path,
)


@dataclass
class LiveSegment:
    text: str
    cleaned: str
    index: int


@dataclass
class LiveWatchResult:
    """Summary after a live watch ends."""

    segments_spoken: int = 0
    turn_completed: bool = False
    stopped: bool = False
    error: Optional[str] = None
    session_id: str = ""
    messages: List[str] = field(default_factory=list)


def resolve_updates_file(
    session_id: str,
    cwd: Optional[str] = None,
    root: Optional[Path] = None,
) -> Optional[Path]:
    session_dir = find_session_dir(session_id, cwd=cwd, root=root)
    if not session_dir:
        return None
    path = updates_path(session_dir)
    return path if path.is_file() or session_dir.is_dir() else None


def prepare_live_segment(raw: str, cfg: Config, *, index: int) -> Optional[LiveSegment]:
    """Clean a raw agent message chunk for TTS; drop short / empty noise."""
    if not raw or not raw.strip():
        return None
    cleaned = clean_for_audio(raw, mode="verbatim")
    min_chars = int(getattr(cfg, "live_min_chars", 40) or 0)
    if len(cleaned) < min_chars:
        return None
    # Strip leftover markdown emphasis that sounds bad spoken
    cleaned = cleaned.replace("**", "").replace("__", "").replace("`", "")
    cleaned = " ".join(cleaned.split())
    if len(cleaned) < min_chars:
        return None
    return LiveSegment(text=raw, cleaned=cleaned, index=index)


def iter_live_segments(
    session_id: str,
    cfg: Config,
    *,
    cwd: Optional[str] = None,
    still_active: Optional[Callable[[], bool]] = None,
    poll_s: Optional[float] = None,
    max_wait_s: float = 600.0,
    start_at_end: bool = True,
    root: Optional[Path] = None,
) -> Iterator[LiveSegment]:
    """Poll updates.jsonl and yield speakable agent_message segments until turn ends.

    Starts at EOF by default so only new content for the current turn is spoken.
    Stops on turn_completed, still_active() False, or max_wait_s.
    """
    path = resolve_updates_file(session_id, cwd=cwd, root=root)
    if path is None:
        return

    # Wait briefly for the file to appear (new sessions).
    deadline_appear = time.time() + 15.0
    while not path.is_file():
        if still_active is not None and not still_active():
            return
        if time.time() >= deadline_appear:
            return
        time.sleep(0.1)

    poll = poll_s
    if poll is None:
        poll = max(0.05, float(getattr(cfg, "live_poll_ms", 150) or 150) / 1000.0)

    tail = UpdatesTail(path, start_at_end=start_at_end)
    idx = 0
    # When a still_active callback is provided (daemon), trust it for lifetime
    # so long agent turns are not cut off by a fixed timeout.
    deadline = None if still_active is not None else (time.time() + max_wait_s)
    done = False

    while not done:
        if still_active is not None and not still_active():
            return
        if deadline is not None and time.time() >= deadline:
            return

        events = tail.poll()
        if not events:
            time.sleep(poll)
            continue

        for ev in events:
            if still_active is not None and not still_active():
                return
            if ev.kind == "turn_completed":
                done = True
                break
            if ev.kind != "message":
                continue
            seg = prepare_live_segment(ev.text, cfg, index=idx)
            if seg is None:
                continue
            idx += 1
            yield seg
