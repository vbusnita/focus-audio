"""Brief prompt, structured render, and fallback tests (no live API)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from focus_audio.brief import (  # noqa: E402
    BRIEF_SYSTEM,
    VERBATIM_SYSTEM,
    fallback_script,
    finalize_brief_script,
    parse_brief_response,
    render_brief_struct,
    trim_source_for_brief,
)
from focus_audio.config import Config  # noqa: E402
from focus_audio.cache import cache_key  # noqa: E402


def test_brief_system_is_structured_json():
    text = BRIEF_SYSTEM.format(max_words=220)
    assert "headline" in text
    assert "changes" in text
    assert "next" in text
    assert "json" in text.lower()
    assert "basename" in text.lower() or "basenames" in text.lower()
    # Explicit ban on speaking fence placeholders / line counts
    assert "never" in text.lower() and "code block" in text.lower()


def test_verbatim_system_preserves_order():
    assert "order" in VERBATIM_SYSTEM.lower()
    assert "basename" in VERBATIM_SYSTEM.lower() or "basenames" in VERBATIM_SYSTEM.lower()
    assert "lines of" not in VERBATIM_SYSTEM.lower() or "never" in VERBATIM_SYSTEM.lower()


def test_render_brief_struct_order():
    script = render_brief_struct(
        {
            "headline": "Login fix shipped",
            "changes": ["Updated auth middleware", "Added regression test"],
            "caveat": "Staging only for now",
            "next": "You should deploy staging",
        }
    )
    assert script.startswith("Login fix shipped")
    assert "auth middleware" in script
    assert "Staging only" in script
    assert "deploy staging" in script
    # Sentences get terminal punctuation
    assert script.count(".") >= 3


def test_parse_brief_response_raw_json():
    raw = json.dumps(
        {
            "headline": "Done",
            "changes": ["Touched brief.py"],
            "caveat": "",
            "next": "You can rebrief",
        }
    )
    data = parse_brief_response(raw)
    assert data is not None
    assert data["headline"] == "Done"


def test_parse_brief_response_fenced_json():
    raw = """```json
{"headline": "OK", "changes": [], "caveat": "", "next": "Nothing you need to do next."}
```"""
    data = parse_brief_response(raw)
    assert data is not None
    assert data["headline"] == "OK"


def test_finalize_prefers_json_render():
    raw = json.dumps(
        {
            "headline": "Brief quality improved",
            "changes": ["Speakable pre-pass", "Structured JSON brief"],
            "caveat": "",
            "next": "You should try the next turn with brief mode",
        }
    )
    out = finalize_brief_script(raw, max_words=220)
    assert "Brief quality improved" in out
    assert "{" not in out
    assert "Speakable pre-pass" in out


def test_finalize_prose_fallback():
    out = finalize_brief_script(
        "## Title\n\nWe fixed the bug. Next deploy.",
        max_words=50,
    )
    assert "##" not in out
    assert "fixed the bug" in out.lower()


def test_finalize_clamps_words():
    changes = [f"Change number {i} was applied carefully" for i in range(40)]
    raw = json.dumps(
        {
            "headline": "Many changes",
            "changes": changes,
            "caveat": "",
            "next": "Review carefully",
        }
    )
    out = finalize_brief_script(raw, max_words=30)
    assert len(out.split()) <= 30


def test_trim_source_keeps_head_mid_tail():
    # Distinct markers so we can see all three regions survive.
    head = "HEAD " * 200
    mid = " MIDTOKEN "
    tail = " TAIL" * 200
    # Build long text with mid in the center
    left = "L" * 8000
    right = "R" * 8000
    text = head + left + mid + right + tail
    assert len(text) > 12000
    out = trim_source_for_brief(text, max_chars=4000)
    assert "HEAD" in out
    assert "TAIL" in out or "TAIL" in out.replace(" ", "")
    assert "middle sample" in out.lower() or "toward end" in out.lower()
    assert len(out) < len(text)


def test_fallback_truncates():
    words = " ".join(f"w{i}" for i in range(500))
    out = fallback_script(words, max_words=50)
    assert len(out.split()) <= 55
    assert "excerpt" in out or "…" in out


def test_cache_key_stable():
    a = cache_key("hello", "brief", "ara", 1.1, "m1")
    b = cache_key("hello", "brief", "ara", 1.1, "m1")
    c = cache_key("hello", "verbatim", "ara", 1.1, "m1")
    assert a == b
    assert a != c


def test_config_defaults():
    cfg = Config()
    assert cfg.mode == "verbatim"
    assert cfg.tts_provider == "macos"
    assert cfg.autoplay is True
    assert cfg.toggle_mode() == "brief"
    assert cfg.toggle_mode() == "verbatim"
