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
    strip_harness_metadata,
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


_SAMPLE_HARNESS = """Routed: `focus_harness` → `network-ops` + `focus-harness`

```harness-signal
{
  "harness_intent": "focus_harness",
  "agent": "network-ops",
  "packs_loaded": ["focus-harness"],
  "tool": null,
  "attribution_source": "router",
  "deep_loaded": false,
  "confidence": "high"
}
```

We shipped the live CLI and stripped routing noise from speech.
"""


def test_strip_harness_metadata_removes_routed_and_fence():
    out = strip_harness_metadata(_SAMPLE_HARNESS)
    assert "Routed:" not in out
    assert "harness-signal" not in out.lower()
    assert "harness_intent" not in out
    assert "packs_loaded" not in out
    assert "attribution_source" not in out
    assert "network-ops" not in out  # only appeared in routing metadata
    assert "shipped the live CLI" in out


def test_clean_for_audio_strips_harness_both_modes():
    for mode in ("brief", "verbatim"):
        out = clean_for_audio(_SAMPLE_HARNESS, mode=mode)
        assert "Routed:" not in out
        assert "harness_intent" not in out
        assert "code sample" not in out.lower()  # must not become fence placeholder
        assert "shipped the live CLI" in out


def test_strip_unclosed_harness_fence_streaming():
    """Live chunks may cut mid-fence before the closing ticks."""
    partial = (
        "Routed: `network_ops` → `network-ops` + `topology`\n\n"
        "```harness-signal\n"
        '{\n  "harness_intent": "network_ops",\n  "agent": "network-ops"\n'
    )
    out = strip_harness_metadata(partial)
    assert "Routed:" not in out
    assert "harness_intent" not in out
    assert out.strip() == ""


def test_strip_bare_harness_json_without_fence():
    src = (
        'Here is status.\n'
        '{"harness_intent": "focus_harness", "agent": "network-ops", '
        '"packs_loaded": ["focus-harness"], "attribution_source": "router"}\n'
        "Then the real answer continues."
    )
    out = clean_for_audio(src, mode="verbatim")
    assert "harness_intent" not in out
    assert "real answer continues" in out


def test_strip_residual_harness_json_field_lines():
    """Second live chunk may be only field lines after the fence opener was stripped."""
    chunk = (
        '  "harness_intent": "focus_harness",\n'
        '  "packs_loaded": ["focus-harness"],\n'
        '  "attribution_source": "router",\n'
        '  "confidence": "high"\n'
        "}\n\n"
        "Actual work: commit landed."
    )
    out = clean_for_audio(chunk, mode="verbatim")
    assert "harness_intent" not in out
    assert "packs_loaded" not in out
    assert "commit landed" in out


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
