"""Unit tests for transcript extraction and cleaning."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from focus_audio.transcript import (  # noqa: E402
    clean_for_audio,
    encode_cwd,
    expand_for_speech,
    extract_assistant_contents,
    find_session_dir,
    last_assistant_text,
    last_assistant_text_from_history,
    last_assistant_text_from_updates,
    load_turn,
    path_basename,
    session_dir_from_transcript_path,
    strip_harness_metadata,
)


def test_encode_cwd_url_style():
    assert encode_cwd("/Users/example/projects") == "%2FUsers%2Fexample%2Fprojects"


def test_path_basename():
    assert path_basename("/Users/example/projects/focus-audio/daemon.py") == "daemon.py"
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
        "Edited /Users/example/projects/focus-audio/focus_audio/brief.py "
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
    # Headings and bullets become sentences (periods) for TTS pauses.
    assert "Status." in out
    assert "Fixed login." in out
    assert "Shipped v1." in out or "Shipped" in out


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


def test_clean_bullets_become_separate_sentences():
    """List items must not collapse into one run-on TTS phrase."""
    src = """Done:
- path collapse
- symbol expansion
- harness silence

Also:
1. Restart daemon
2. Run tests
3. Commit when ready
"""
    out = clean_for_audio(src, mode="verbatim")
    # Markers gone
    assert re.search(r"(?m)^[-*+]\s", out) is None
    assert re.search(r"(?m)^\d+\.\s", out) is None
    # Each item is its own sentence (period) — survives newline→space collapse.
    for phrase in (
        "Path collapse.",
        "Symbol expansion.",
        "Harness silence.",
        "Restart daemon.",
        "Run tests.",
        "Commit when ready.",
    ):
        assert phrase in out
    # Whitespace-collapsed form (as TTS chunker does) still has sentence breaks.
    collapsed = " ".join(out.split())
    assert "Path collapse. Symbol expansion. Harness silence." in collapsed
    assert "Restart daemon. Run tests. Commit when ready." in collapsed


def test_clean_unicode_bullets_and_heading_sentence():
    src = """## Next steps
• first thing
• second thing
"""
    out = clean_for_audio(src, mode="brief")
    assert "Next steps." in out
    assert "First thing." in out
    assert "Second thing." in out
    assert "•" not in out


def test_clean_preserves_snake_case_as_words():
    """Bare underscores must not glue identifiers (old MD_EMPHASIS bug)."""
    out = clean_for_audio(
        "Toggle live_verbatim and check skip_llm in focus_audio.",
        mode="verbatim",
    )
    low = out.lower()
    # Normalize trailing punctuation so "audio." still counts as audio.
    tokens = [t.strip(".,;:!?") for t in low.split()]
    # Old bug stripped "_" and produced one glued token.
    assert "liveverbatim" not in tokens
    assert "skipllm" not in tokens
    assert "focusaudio" not in tokens
    assert "live" in tokens and "verbatim" in tokens
    assert "skip" in tokens and "llm" in tokens
    assert "focus" in tokens and "audio" in tokens


def test_expand_arrows_and_operators():
    src = "Exit non-zero → retry; a == b and x != y; n <= 10; m >= 5; f => g"
    out = expand_for_speech(src)
    assert "→" not in out
    assert "==" not in out
    assert "!=" not in out
    assert "<=" not in out
    assert ">=" not in out
    assert "=>" not in out
    low = out.lower()
    assert "to" in low
    assert "equals" in low
    assert "not equal" in low
    assert "less than or equal" in low
    assert "greater than or equal" in low
    assert "then" in low


def test_expand_key_chords():
    out = expand_for_speech("Press Ctrl+Shift+M or Cmd+C then Ctrl+Shift+Space.")
    low = out.lower()
    assert "control" in low and "shift" in low
    assert "command" in low
    assert "space" in low
    # Chord pluses should be gone (not "control plus shift").
    assert "control+shift" not in low
    assert "ctrl+shift" not in low


def test_expand_status_kv_money_percent():
    out = expand_for_speech(
        "Status: enabled=true · mode=verbatim · cost $12 (50%) — done."
    )
    low = out.lower()
    assert "enabled is true" in low
    assert "mode is verbatim" in low
    assert "·" not in out
    assert "—" not in out
    assert "12 dollars" in low
    assert "50 percent" in low


def test_clean_symbols_end_to_end_verbatim():
    src = (
        "Use Ctrl+Shift+R after failure.\n"
        "Status: enabled=true · mode=verbatim\n"
        "Compare a == b; if broken → retry.\n"
        "See [docs](https://example.com/very/long/path/here) and **bold** live_verbatim."
    )
    out = clean_for_audio(src, mode="verbatim")
    low = out.lower()
    assert "→" not in out
    assert "==" not in out
    assert "·" not in out
    assert "**" not in out
    assert "https://" not in out
    assert "](" not in out
    assert "control shift" in low
    assert "enabled is true" in low
    assert "live verbatim" in low
    assert "docs" in low
    assert "equals" in low


def test_expand_for_speech_idempotent_enough():
    once = expand_for_speech("a == b → c & d")
    twice = expand_for_speech(once)
    assert "equals" in twice.lower()
    assert "and" in twice.lower()
    # Should not explode into doubled phrases on second pass.
    assert twice.lower().count("equals") <= once.lower().count("equals") + 1


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


def _updates_line(session_update: str, text: str = "") -> str:
    update: dict = {"sessionUpdate": session_update}
    if session_update == "agent_message_chunk":
        update["content"] = {"type": "text", "text": text}
    return json.dumps(
        {
            "timestamp": 1,
            "method": "session/update",
            "params": {"sessionId": "s", "update": update},
        }
    )


def test_session_dir_from_transcript_path(tmp_path: Path):
    sess = tmp_path / "019f-sid"
    sess.mkdir()
    updates = sess / "updates.jsonl"
    updates.write_text("{}\n", encoding="utf-8")
    assert session_dir_from_transcript_path(str(updates)) == sess
    assert session_dir_from_transcript_path(str(sess)) == sess
    assert session_dir_from_transcript_path("") is None


def test_find_session_dir_via_transcript_path(tmp_path: Path):
    sid = "019f-via-tx"
    sess = tmp_path / "workspace" / sid
    sess.mkdir(parents=True)
    updates = sess / "updates.jsonl"
    updates.write_text("{}\n", encoding="utf-8")
    found = find_session_dir(
        sid, cwd=None, root=tmp_path, transcript_path=str(updates)
    )
    assert found == sess


def test_last_assistant_from_updates_prefers_stream(tmp_path: Path):
    updates = tmp_path / "updates.jsonl"
    history = tmp_path / "chat_history.jsonl"
    # History is stale / lagging; updates has the real last turn.
    history.write_text(
        json.dumps(
            {
                "type": "assistant",
                "content": "Old history reply that should not win when updates exist.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    lines = [
        _updates_line("agent_message_chunk", "First turn message long enough. "),
        _updates_line("turn_completed"),
        _updates_line("user_message_chunk", "next"),
        _updates_line("agent_message_chunk", "Second turn "),
        _updates_line("agent_message_chunk", "assembled from chunks for speech."),
        _updates_line("turn_completed"),
    ]
    updates.write_text("\n".join(lines) + "\n", encoding="utf-8")

    from_u = last_assistant_text_from_updates(tmp_path)
    assert from_u is not None
    assert "Second turn" in from_u
    assert "assembled from chunks" in from_u
    assert "First turn" not in from_u

    # Prefer updates over history.
    assert last_assistant_text(tmp_path) == from_u
    assert "Old history" in (last_assistant_text_from_history(tmp_path) or "")


def test_last_assistant_from_updates_open_turn_before_completed(tmp_path: Path):
    """Stop can race turn_completed — still use open agent_message assembly."""
    updates = tmp_path / "updates.jsonl"
    lines = [
        _updates_line(
            "agent_message_chunk",
            "We finished the login fix and added tests for the edge case.",
        ),
        # no turn_completed yet
    ]
    updates.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = last_assistant_text_from_updates(tmp_path)
    assert out is not None
    assert "login fix" in out


def test_load_turn_updates_first(tmp_path: Path):
    cwd = "/Users/example/proj"
    sid = "019f-load-turn"
    sess = tmp_path / encode_cwd(cwd) / sid
    sess.mkdir(parents=True)
    (sess / "updates.jsonl").write_text(
        _updates_line(
            "agent_message_chunk",
            "This is a sufficiently long assistant reply for the min_chars gate to pass easily.",
        )
        + "\n"
        + _updates_line("turn_completed")
        + "\n",
        encoding="utf-8",
    )
    turn = load_turn(sid, cwd=cwd, root=tmp_path, min_chars=20, mode="brief")
    assert turn is not None
    assert "sufficiently long" in turn.raw
    assert turn.session_dir == sess


def test_load_turn_via_transcript_path_only(tmp_path: Path):
    sid = "019f-tx-only"
    sess = tmp_path / "other" / sid
    sess.mkdir(parents=True)
    updates = sess / "updates.jsonl"
    body = (
        "Reply that is long enough for load_turn min_chars without scanning sessions root."
    )
    updates.write_text(
        _updates_line("agent_message_chunk", body) + "\n",
        encoding="utf-8",
    )
    turn = load_turn(
        "",
        cwd=None,
        root=tmp_path,
        min_chars=20,
        transcript_path=str(updates),
    )
    assert turn is not None
    assert body in turn.raw
    assert turn.session_id == sid
