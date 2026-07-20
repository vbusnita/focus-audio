"""macOS free TTS path: provider selection, say backend, cache keys, doctor."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from focus_audio.cache import cache_key, entry_for  # noqa: E402
from focus_audio.config import Config  # noqa: E402
from focus_audio.doctor import run_doctor  # noqa: E402
from focus_audio.pipeline import resolve_script  # noqa: E402
from focus_audio.tts import speed_to_wpm, synthesize_speech  # noqa: E402


def test_speed_to_wpm_mapping():
    assert speed_to_wpm(1.0) == 175
    assert speed_to_wpm(1.1) == int(round(175 * 1.1))
    assert speed_to_wpm(2.0) == 350
    assert speed_to_wpm(0) == 175  # invalid → treat as 1.0


def test_speed_to_wpm_clamped():
    assert speed_to_wpm(0.1) == 90
    assert speed_to_wpm(0.5) == 90  # 87.5 → clamp min
    assert speed_to_wpm(10.0) == 400


def test_effective_provider_auto_without_key(monkeypatch):
    cfg = Config(tts_provider="auto")
    monkeypatch.setattr(cfg, "api_key", lambda: None)
    assert cfg.effective_tts_provider() == "macos"
    assert cfg.uses_cloud_tts() is False
    assert cfg.uses_cloud_brief() is False
    assert cfg.audio_suffix() == ".aiff"
    assert cfg.effective_voice_id() == "system"


def test_effective_provider_auto_with_key(monkeypatch):
    cfg = Config(tts_provider="auto", voice_id="ara")
    monkeypatch.setattr(cfg, "api_key", lambda: "xai-test")
    assert cfg.effective_tts_provider() == "xai"
    assert cfg.uses_cloud_tts() is True
    assert cfg.audio_suffix() == ".mp3"
    assert cfg.effective_voice_id() == "ara"


def test_effective_provider_force_macos_even_with_key(monkeypatch):
    cfg = Config(tts_provider="macos", macos_voice="Samantha")
    monkeypatch.setattr(cfg, "api_key", lambda: "xai-test")
    assert cfg.effective_tts_provider() == "macos"
    assert cfg.effective_voice_id() == "Samantha"
    # Brief rewrite can still use the key when present.
    assert cfg.uses_cloud_brief() is True
    assert cfg.uses_cloud_tts() is False


def test_normalize_aliases():
    assert Config(tts_provider="say").normalize_tts_provider() == "macos"
    assert Config(tts_provider="local").normalize_tts_provider() == "macos"
    assert Config(tts_provider="weird").normalize_tts_provider() == "auto"


def test_cache_key_includes_provider():
    a = cache_key("hello", "verbatim", "system", 1.1, "m", "macos")
    b = cache_key("hello", "verbatim", "system", 1.1, "m", "xai")
    c = cache_key("hello", "verbatim", "ara", 1.1, "m", "xai")
    assert a != b
    assert b != c
    ent = entry_for(a, "verbatim", 5, audio_suffix=".aiff")
    assert ent.audio_path.suffix == ".aiff"


def test_synthesize_macos_invokes_say(tmp_path: Path, monkeypatch):
    cfg = Config(tts_provider="macos", macos_voice="Samantha", speed=1.1)
    monkeypatch.setattr(cfg, "api_key", lambda: None)
    out = tmp_path / "clip.aiff"
    # Pretend say wrote the file.
    def fake_run(cmd, **kwargs):
        # say -o <path> ...
        o_idx = cmd.index("-o")
        Path(cmd[o_idx + 1]).write_bytes(b"AIFF-fake")
        m = MagicMock()
        m.returncode = 0
        m.stderr = b""
        return m

    with patch("focus_audio.tts.shutil.which", return_value="/usr/bin/say"), patch(
        "focus_audio.tts.subprocess.run", side_effect=fake_run
    ) as run:
        path = synthesize_speech("Hello from free path.", out, cfg)
    assert path == out
    assert out.read_bytes() == b"AIFF-fake"
    cmd = run.call_args[0][0]
    assert cmd[0] == "/usr/bin/say"
    assert "-v" in cmd and "Samantha" in cmd
    assert "-r" in cmd and str(speed_to_wpm(1.1)) in cmd
    assert "-f" in cmd


def test_resolve_script_skips_llm_without_key(tmp_path: Path, monkeypatch):
    cfg = Config(tts_provider="macos", mode="verbatim", skip_brief_words=5)
    monkeypatch.setattr(cfg, "api_key", lambda: None)
    # Long enough that should_skip_llm would be false if a key existed.
    long_text = " ".join(["word"] * 120)
    with patch("focus_audio.cache.cache_dir", return_value=tmp_path), patch(
        "focus_audio.pipeline.last_brief_path", return_value=tmp_path / "last.md"
    ), patch("focus_audio.brief._chat_complete") as chat:
        ready = resolve_script(long_text, cfg, mode="verbatim", already_cleaned=True)
    chat.assert_not_called()
    assert ready.brief_skipped is True
    assert "word" in ready.script
    assert ready.entry.audio_path.suffix == ".aiff"


def test_doctor_api_key_ok_on_macos_without_key(monkeypatch):
    cfg = Config(tts_provider="macos")
    monkeypatch.setattr(cfg, "api_key", lambda: None)
    monkeypatch.setattr(cfg, "api_key_source", lambda: "none")
    with patch("focus_audio.doctor.ensure_default_config", return_value=cfg), patch(
        "focus_audio.doctor.shutil.which", return_value="/usr/bin/say"
    ), patch("focus_audio.doctor.Path.is_file", return_value=True):
        # run_doctor still needs real plugin root layout
        report = run_doctor(plugin_root=ROOT)
    api = next(c for c in report.checks if c.id == "api_key")
    assert api.level == "ok"
    assert api.ok is True
    tts = next(c for c in report.checks if c.id == "tts")
    assert tts.ok is True
    assert "macos" in tts.detail.lower() or "say" in tts.detail.lower()
