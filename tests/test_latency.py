"""Tests for short-reply brief skip and TTS chunking (no live API)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from focus_audio.brief import (  # noqa: E402
    should_skip_llm,
    split_for_tts,
    synthesize_script,
    word_count,
)
from focus_audio.config import Config  # noqa: E402
from focus_audio.tts import concat_mp3  # noqa: E402


def test_word_count():
    assert word_count("") == 0
    assert word_count("  one two  three ") == 3


def test_should_skip_llm_short():
    cfg = Config(skip_brief_words=80)
    short = " ".join(f"w{i}" for i in range(50))
    long = " ".join(f"w{i}" for i in range(120))
    assert should_skip_llm(short, cfg) is True
    assert should_skip_llm(long, cfg) is False


def test_should_skip_llm_disabled():
    cfg = Config(skip_brief_words=0)
    assert should_skip_llm("hello world", cfg) is False


def test_synthesize_script_skips_chat_for_short():
    cfg = Config(skip_brief_words=80, max_brief_words=220)
    text = "We fixed login.py and added unit tests. Next, deploy to staging."
    with patch("focus_audio.brief._chat_complete") as mock_chat:
        out = synthesize_script(text, cfg, mode="brief")
        mock_chat.assert_not_called()
    assert "login.py" in out
    assert "staging" in out


def test_synthesize_script_calls_chat_for_long():
    cfg = Config(skip_brief_words=80)
    text = " ".join(f"word{i}" for i in range(100))
    with patch("focus_audio.brief._chat_complete", return_value="briefed") as mock_chat:
        out = synthesize_script(text, cfg, mode="brief")
        mock_chat.assert_called_once()
    assert out == "briefed"


def test_split_for_tts_short_is_single():
    script = "We fixed the bug. Tests pass."
    chunks = split_for_tts(script, first_words=35, chunk_words=90)
    assert chunks == [script] or len(chunks) == 1


def test_split_for_tts_prefers_sentences():
    sents = [
        "First sentence has some words here for padding.",
        "Second sentence continues the story with more detail.",
        "Third sentence wraps up the idea carefully.",
        "Fourth sentence adds another thought for length.",
        "Fifth sentence makes sure we cross the first-chunk threshold.",
        "Sixth sentence gives us more room so chunking actually triggers.",
        "Seventh sentence is the last bit of spoken script content.",
    ]
    script = " ".join(sents)
    assert word_count(script) > 40
    chunks = split_for_tts(script, first_words=20, chunk_words=40)
    assert len(chunks) >= 2
    # First chunk should be shorter than the full script
    assert word_count(chunks[0]) < word_count(script)
    # Rejoin should preserve content
    rejoined = " ".join(chunks)
    for token in ("First", "Seventh", "threshold"):
        assert token in rejoined


def test_split_for_tts_word_fallback():
    # No punctuation — still chunks by words
    words = " ".join(f"w{i}" for i in range(120))
    chunks = split_for_tts(words, first_words=30, chunk_words=40)
    assert len(chunks) >= 2
    assert sum(word_count(c) for c in chunks) == 120


def test_concat_mp3(tmp_path: Path):
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.mp3"
    a.write_bytes(b"AAA")
    b.write_bytes(b"BBB")
    out = tmp_path / "out.mp3"
    concat_mp3([a, b], out)
    assert out.read_bytes() == b"AAABBB"
