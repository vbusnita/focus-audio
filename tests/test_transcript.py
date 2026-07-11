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
    path_basename,
)


def test_encode_cwd_url_style():
    assert encode_cwd("/Users/example/projects") == "%2FUsers%2Fexample%2Fprojects"


def test_path_basename():
    assert path_basename("/Users/example/projects/plugins/focus-audio/daemon.py") == "daemon.py"
    assert path_basename("~/.grok/focus-audio/last_brief.md") == "last_brief.md"
    assert path_basename("plugins/focus-audio/focus_audio/brief.py") == "brief.py"


def test_clean_strips_code_fences_no_line_counts():
    src = "Here is the fix:\n\n```python\nprint('hi')\nprint('bye')\n```\n\nDone."
    out = clean_for_audio(src, mode="brief")
    assert "print" not in out
    assert "lines of" not in out.lower()
    assert "code block" not in out.lower()
    assert "Done." in out


def test_clean_verbatim_keeps_language_placeholder():
    src = "See:\n\n```python\nx = 1\n```\n\nOK."
    out = clean_for_audio(src, mode="verbatim")
    assert "x = 1" not in out
    assert "code sample" in out.lower()
    assert "python" in out.lower()
    assert "lines of" not in out.lower()


def test_clean_shortens_long_urls():
    long = "https://example.com/" + ("a" * 80)
    out = clean_for_audio(f"see {long} please")
    assert "link" in out
    assert long not in out


def test_clean_collapses_absolute_paths():
    src = (
        "Edited /Users/example/projects/plugins/focus-audio/focus_audio/brief.py "
        "and ~/.grok/focus-audio/config.toml. Also plugins/focus-audio/README.md."
    )
    out = clean_for_audio(src, mode="brief")
    assert "/Users/" not in out
    assert "~/" not in out
    assert "brief.py" in out
    assert "config.toml" in out
    assert "README.md" in out
    # Full multi-segment relative path should not survive
    assert "plugins/focus-audio/README.md" not in out


def test_clean_drops_file_line_refs():
    src = "Bug in focus_audio/daemon.py:142 and lines 10-20 of the helper."
    out = clean_for_audio(src, mode="brief")
    assert "daemon.py" in out
    assert ":142" not in out
    assert "lines 10-20" not in out.lower()


def test_clean_flattens_markdown_and_tables():
    src = """## Status

| Area | Note |
|------|------|
| API keys | Safe |
| Disk | Open |

- Fixed login
- **Shipped** v1
"""
    out = clean_for_audio(src, mode="brief")
    assert "|" not in out
    assert "##" not in out
    assert "**" not in out
    assert "Fixed login" in out
    assert "Shipped" in out
    assert "table" not in out.lower()  # brief drops tables silently


def test_clean_verbatim_table_placeholder():
    src = """| A | B |
|---|---|
| 1 | 2 |
"""
    out = clean_for_audio(src, mode="verbatim")
    assert "table" in out.lower()


def test_clean_caps_long_file_lists():
    names = ", ".join(f"file{i}.py" for i in range(8))
    out = clean_for_audio(f"Touched {names}.", mode="brief")
    assert "more files" in out
    assert "file7.py" not in out


def test_clean_inline_code_paths():
    out = clean_for_audio("See `plugins/focus-audio/focus_audio/tts.py` next.", mode="brief")
    assert "tts.py" in out
    assert "plugins/" not in out


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
