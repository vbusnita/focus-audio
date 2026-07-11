"""Tests for live updates.jsonl tailing and segment prep (no live API)."""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from focus_audio.config import Config  # noqa: E402
from focus_audio.live import (  # noqa: E402
    LiveSegment,
    LiveSegmentQueue,
    prepare_live_segment,
    produce_live_segments,
)
from focus_audio.transcript import (  # noqa: E402
    UpdatesTail,
    classify_update,
    extract_agent_message_text,
    parse_update_line,
)


def _line(session_update: str, text=None) -> str:
    update = {"sessionUpdate": session_update}
    if text is not None:
        update["content"] = {"type": "text", "text": text}
    return json.dumps(
        {
            "timestamp": 1,
            "method": "_x.ai/session/update",
            "params": {"sessionId": "s1", "update": update},
        }
    )


def test_parse_agent_message_chunk():
    line = _line("agent_message_chunk", "Hello there, this is a status update.")
    upd = parse_update_line(line)
    assert upd is not None
    assert extract_agent_message_text(upd) == "Hello there, this is a status update."
    ev = classify_update(upd)
    assert ev.kind == "message"
    assert "Hello" in ev.text


def test_parse_turn_completed():
    line = _line("turn_completed")
    upd = parse_update_line(line)
    assert classify_update(upd).kind == "turn_completed"


def test_updates_tail_from_end(tmp_path: Path):
    path = tmp_path / "updates.jsonl"
    # Pre-existing content should be skipped when start_at_end=True
    path.write_text(_line("agent_message_chunk", "old message that is long enough") + "\n")
    tail = UpdatesTail(path, start_at_end=True)

    with path.open("a") as fh:
        fh.write(_line("agent_message_chunk", "new status message for the live path") + "\n")
        fh.write(_line("turn_completed") + "\n")

    events = tail.poll()
    kinds = [e.kind for e in events]
    assert kinds == ["message", "turn_completed"]
    assert "new status" in events[0].text
    assert tail.poll() == []


def test_updates_tail_partial_line(tmp_path: Path):
    path = tmp_path / "updates.jsonl"
    path.write_text("")
    tail = UpdatesTail(path, start_at_end=True)
    full = _line("agent_message_chunk", "partial then complete message body here")
    path.write_text(full[:40])
    assert tail.poll() == []
    with path.open("a") as fh:
        fh.write(full[40:] + "\n")
    events = tail.poll()
    assert len(events) == 1
    assert events[0].kind == "message"


def test_prepare_live_segment_min_chars():
    cfg = Config(live_min_chars=40)
    assert prepare_live_segment("short", cfg, index=0) is None
    long = "This is a longer agent status update that should pass the min char gate."
    seg = prepare_live_segment(long, cfg, index=1)
    assert seg is not None
    assert seg.index == 1
    assert "longer agent" in seg.cleaned


def test_prepare_live_strips_code_fences():
    cfg = Config(live_min_chars=20)
    raw = "Next I'll edit login.py:\n\n```python\nprint(1)\nprint(2)\n```\n\nThen tests."
    seg = prepare_live_segment(raw, cfg, index=0)
    assert seg is not None
    assert "print" not in seg.cleaned
    assert "code sample" in seg.cleaned.lower()
    assert "lines of" not in seg.cleaned.lower()


def test_live_segment_queue_fifo_and_eof():
    q = LiveSegmentQueue()
    a = LiveSegment(text="a", cleaned="first message that is long enough here", index=0)
    b = LiveSegment(text="b", cleaned="second message that is long enough too", index=1)
    assert q.put(a) is True
    assert q.put(b) is True
    assert q.accepted == 2
    assert q.pending() == 2

    assert q.get(timeout=0.2) is a
    assert q.pending() == 1
    assert q.get(timeout=0.2) is b
    assert q.pending() == 0

    q.close()
    assert q.closed is True
    assert q.get(timeout=0.2) is None
    # Closed queue rejects further puts
    assert q.put(a) is False


def test_live_segment_queue_clear_drops_pending():
    q = LiveSegmentQueue()
    q.put(LiveSegment(text="x", cleaned="pending message long enough to speak", index=0))
    q.put(LiveSegment(text="y", cleaned="another pending message long enough", index=1))
    assert q.pending() == 2
    q.clear()
    assert q.pending() == 0
    assert q.get(timeout=0.2) is None
    assert q.put(LiveSegment(text="z", cleaned="after clear should be rejected", index=2)) is False


def test_batch_yields_all_messages_before_turn_completed(tmp_path: Path):
    """Messages in the same poll as turn_completed must all be queued, not dropped."""
    from focus_audio import live as live_mod

    session_root = tmp_path / "sessions"
    session_dir = session_root / "proj" / "sid-batch"
    session_dir.mkdir(parents=True)
    updates = session_dir / "updates.jsonl"
    updates.write_text("")

    msgs = [
        "Status update one: looking at the auth module carefully now.",
        "Status update two: found the bug in the token refresh path.",
    ]
    with updates.open("a") as fh:
        for m in msgs:
            fh.write(_line("agent_message_chunk", m) + "\n")
        fh.write(_line("turn_completed") + "\n")

    cfg = Config(live_min_chars=40, live_poll_ms=50)
    segs = list(
        live_mod.iter_live_segments(
            "sid-batch",
            cfg,
            start_at_end=False,
            root=session_root,
            max_wait_s=2.0,
        )
    )
    assert len(segs) == 2
    assert "auth module" in segs[0].cleaned
    assert "token refresh" in segs[1].cleaned


def test_produce_live_segments_fills_queue(tmp_path: Path):
    session_root = tmp_path / "sessions"
    session_dir = session_root / "proj" / "sid-prod"
    session_dir.mkdir(parents=True)
    updates = session_dir / "updates.jsonl"
    updates.write_text("")

    with updates.open("a") as fh:
        fh.write(
            _line(
                "agent_message_chunk",
                "First live status: starting work on the queue fix now.",
            )
            + "\n"
        )
        fh.write(
            _line(
                "agent_message_chunk",
                "Second live status: queue drain logic is in place.",
            )
            + "\n"
        )
        fh.write(_line("turn_completed") + "\n")

    cfg = Config(live_min_chars=40, live_poll_ms=40)
    q = LiveSegmentQueue()
    accepted: list = []

    def on_acc(seg, n):
        accepted.append((seg.index, n))

    produce_live_segments(
        q,
        "sid-prod",
        cfg,
        start_at_end=False,
        root=session_root,
        max_wait_s=3.0,
        on_accepted=on_acc,
    )
    assert q.closed
    assert q.accepted == 2
    assert len(accepted) == 2
    got = []
    while True:
        try:
            seg = q.get(timeout=0.3)
        except queue.Empty:
            break
        if seg is None:
            break
        got.append(seg.cleaned)
    assert len(got) == 2
    assert "queue fix" in got[0]
    assert "drain logic" in got[1]


def test_queue_consumer_plays_in_order_without_drop():
    """Simulate slow consumer: later segments stay queued until prior finishes."""
    q = LiveSegmentQueue()
    order: list = []

    def producer():
        for i, text in enumerate(
            [
                "alpha message body that is long enough for live",
                "bravo message body that is long enough for live",
                "charlie message body that is long enough for live",
            ]
        ):
            q.put(LiveSegment(text=text, cleaned=text, index=i))
            time.sleep(0.02)
        q.close()

    def consumer():
        while True:
            try:
                seg = q.get(timeout=0.5)
            except queue.Empty:
                continue
            if seg is None:
                break
            # "play" delay — must not prevent later segments from being queued
            time.sleep(0.05)
            order.append(seg.index)

    t_prod = threading.Thread(target=producer)
    t_cons = threading.Thread(target=consumer)
    t_prod.start()
    t_cons.start()
    t_prod.join(timeout=2)
    t_cons.join(timeout=2)
    assert order == [0, 1, 2]

