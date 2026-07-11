"""Tests for live updates.jsonl tailing and segment prep (no live API)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from focus_audio.config import Config  # noqa: E402
from focus_audio.live import prepare_live_segment  # noqa: E402
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
