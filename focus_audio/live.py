"""Experimental live verbatim: speak agent_message_chunk events as they land.

Tails ``updates.jsonl`` for a Grok session during an open turn and yields
cleaned speakable segments (no brief LLM rewrite).

Segments are meant to be **queued** by the daemon: discovery (tail) and
playback are decoupled so a second message never interrupts the first, and
Stop/post-turn brief waits until every queued segment has finished speaking.
"""

from __future__ import annotations

import queue
import threading
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

# Sentinel placed on LiveSegmentQueue when the producer is done.
_LIVE_QUEUE_END = object()


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


class LiveSegmentQueue:
    """Thread-safe FIFO of speakable live segments.

    Producer (tailer) enqueues segments as they land; consumer (player) dequeues
    and speaks each one to completion. Closing the queue unblocks the consumer
    after all prior segments have been taken.
    """

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._accepted = 0
        self._pending = 0
        self._closed = False

    @property
    def accepted(self) -> int:
        """Number of segments ever enqueued (not including the end sentinel)."""
        with self._lock:
            return self._accepted

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def pending(self) -> int:
        """Segments waiting to be dequeued (excludes end sentinel)."""
        with self._lock:
            return self._pending

    def put(self, seg: LiveSegment) -> bool:
        """Enqueue a segment. Returns False if the queue was already closed."""
        with self._lock:
            if self._closed:
                return False
            self._accepted += 1
            self._pending += 1
        self._q.put(seg)
        return True

    def close(self) -> None:
        """Signal end-of-stream (idempotent). Consumer receives None after drain."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._q.put(_LIVE_QUEUE_END)

    def clear(self) -> None:
        """Drop all pending segments (hard cancel). Still closes the stream."""
        with self._lock:
            self._closed = True
            self._accepted = 0
            self._pending = 0
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        # Unblock any waiter
        try:
            self._q.put_nowait(_LIVE_QUEUE_END)
        except queue.Full:
            pass

    def get(self, timeout: float = 0.15) -> Optional[LiveSegment]:
        """Return next segment, None on end-of-stream, or raise queue.Empty."""
        item = self._q.get(timeout=timeout)
        if item is _LIVE_QUEUE_END:
            # Re-queue end so further gets also see EOF (multiple waiters / retries).
            self._q.put(_LIVE_QUEUE_END)
            return None
        with self._lock:
            self._pending = max(0, self._pending - 1)
        return item  # type: ignore[return-value]


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

    When multiple messages appear in one poll batch, they are all yielded before
    ``turn_completed`` is honored (messages are never dropped for the end marker
    in the same batch). Callers should queue yields and play them sequentially.
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

        # Collect speakable segments from this poll first; honor turn_completed
        # only after every message in the batch has been yielded. That way a
        # final reply flushed in the same write as turn_completed is never lost.
        batch: List[LiveSegment] = []
        for ev in events:
            if still_active is not None and not still_active():
                return
            if ev.kind == "turn_completed":
                done = True
                # Keep scanning: unlikely, but messages after the marker in a
                # mis-ordered batch would otherwise be dropped.
                continue
            if ev.kind != "message":
                continue
            seg = prepare_live_segment(ev.text, cfg, index=idx)
            if seg is None:
                continue
            idx += 1
            batch.append(seg)

        for seg in batch:
            if still_active is not None and not still_active():
                return
            yield seg


def produce_live_segments(
    out: LiveSegmentQueue,
    session_id: str,
    cfg: Config,
    *,
    cwd: Optional[str] = None,
    still_active: Optional[Callable[[], bool]] = None,
    poll_s: Optional[float] = None,
    max_wait_s: float = 600.0,
    start_at_end: bool = True,
    root: Optional[Path] = None,
    on_accepted: Optional[Callable[[LiveSegment, int], None]] = None,
) -> None:
    """Fill ``out`` from the session tail; always close the queue when finished."""
    try:
        for seg in iter_live_segments(
            session_id,
            cfg,
            cwd=cwd,
            still_active=still_active,
            poll_s=poll_s,
            max_wait_s=max_wait_s,
            start_at_end=start_at_end,
            root=root,
        ):
            if still_active is not None and not still_active():
                break
            if not out.put(seg):
                break
            if on_accepted is not None:
                try:
                    on_accepted(seg, out.accepted)
                except Exception:
                    pass
    finally:
        out.close()
