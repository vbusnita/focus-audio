"""Unit tests for transcript extraction and cleaning."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from focus_audio.transcript import (  # noqa: E402
    clean_for_audio,
    encode_cwd,
    extract_assistant_contents,
    find_session_dir,
    last_assistant_text,
)


def test_encode_cwd_url_style():
    assert encode_cwd("/Users/example/projects") == "%2FUsers%2Fexample%2Fprojects"


def test_clean_strips_code_fences():
    src = "Here is the fix:\n\n```python\nprint('hi')\nprint('bye')\n```\n\nDone."
    out = clean_for_audio(src)
    assert "print" not in out
    assert "code block" in out
    assert "Done." in out


def test_clean_shortens_long_urls():
    long = "https://example.com/" + ("a" * 80)
    out = clean_for_audio(f"see {long} please")
    assert "…" in out
    assert len(out) < len(long) + 20


def test_extract_assistant_from_fixture(tmp_path: Path):
    history = tmp_path / "chat_history.jsonl"
    lines = [
        {"type": "user", "content": "hi"},
        {"type": "assistant", "content": "short"},
        {"type": "tool_result", "tool_call_id": "x", "content": "ok"},
        {
            "type": "assistant",
            "content": "This is a longer assistant reply that should be selected last.",
        },
    ]
    history.write_text(
        "\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8"
    )
    contents = extract_assistant_contents(history)
    assert len(contents) == 2
    assert contents[-1].startswith("This is a longer")
    assert last_assistant_text(tmp_path) == contents[-1]


def test_find_session_dir(tmp_path: Path):
    cwd = "/Users/example/projects"
    sid = "019f-test-session"
    group = tmp_path / encode_cwd(cwd)
    sess = group / sid
    sess.mkdir(parents=True)
    (sess / "chat_history.jsonl").write_text("{}\n", encoding="utf-8")

    found = find_session_dir(sid, cwd=cwd, root=tmp_path)
    assert found == sess

    found2 = find_session_dir(sid, cwd=None, root=tmp_path)
    assert found2 == sess
