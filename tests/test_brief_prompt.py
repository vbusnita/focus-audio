"""Brief prompt and fallback tests (no live API)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from focus_audio.brief import BRIEF_SYSTEM, VERBATIM_SYSTEM, fallback_script  # noqa: E402
from focus_audio.config import Config, load_config  # noqa: E402
from focus_audio.cache import cache_key  # noqa: E402


def test_brief_system_mentions_structure():
    text = BRIEF_SYSTEM.format(max_words=220)
    assert "what happened" in text.lower() or "What happened" in text
    assert "code" in text.lower()


def test_verbatim_system_preserves_order():
    assert "order" in VERBATIM_SYSTEM.lower()
    assert "code" in VERBATIM_SYSTEM.lower()


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
    assert cfg.mode == "brief"
    assert cfg.autoplay is True
    assert cfg.toggle_mode() == "verbatim"
    assert cfg.toggle_mode() == "brief"
