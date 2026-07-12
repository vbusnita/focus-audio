"""CLI live + power switches (config only; no real daemon required)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from focus_audio import cli  # noqa: E402
from focus_audio.config import Config, load_config, save_config  # noqa: E402
from focus_audio.daemon import FocusAudioDaemon  # noqa: E402


def test_parse_on_off():
    assert cli._parse_on_off("on", current=False) is True
    assert cli._parse_on_off("off", current=True) is False
    assert cli._parse_on_off("toggle", current=True) is False
    assert cli._parse_on_off(None, current=False) is True
    with pytest.raises(ValueError):
        cli._parse_on_off("maybe", current=True)


def test_effective_label_off_when_disabled():
    d = FocusAudioDaemon(cfg=Config(enabled=False, mode="brief", live_verbatim=True))
    assert d.effective_label() == "off"


def test_cmd_live_toggles_config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    monkeypatch.setattr("focus_audio.cli.config_path", lambda: cfg_path)
    monkeypatch.setattr("focus_audio.config.config_path", lambda: cfg_path)
    monkeypatch.setattr("focus_audio.cli.ensure_default_config", lambda: Config(live_verbatim=False))
    monkeypatch.setattr("focus_audio.cli.is_daemon_alive", lambda: False)

    saved: list = []

    def _save(cfg, path=None):
        saved.append(cfg.live_verbatim)
        return cfg_path

    monkeypatch.setattr("focus_audio.cli.save_config", _save)
    monkeypatch.setattr("focus_audio.cli.try_send", lambda *a, **k: {"ok": True})

    rc = cli.cmd_live(type("A", (), {"state": None})())
    assert rc == 0
    assert saved == [True]

    monkeypatch.setattr(
        "focus_audio.cli.ensure_default_config",
        lambda: Config(live_verbatim=True),
    )
    rc = cli.cmd_live(type("A", (), {"state": "off"})())
    assert rc == 0
    assert saved[-1] is False


def test_cmd_power_off_stops_and_disables(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "focus_audio.cli.ensure_default_config",
        lambda: Config(enabled=True, live_verbatim=True, mode="verbatim"),
    )
    monkeypatch.setattr("focus_audio.cli.is_daemon_alive", lambda: True)
    sent: list = []

    def _send(payload, timeout=5.0):
        sent.append(payload)
        return {"ok": True}

    monkeypatch.setattr("focus_audio.cli.try_send", _send)
    monkeypatch.setattr("focus_audio.cli.save_config", lambda cfg, path=None: path)

    rc = cli.cmd_power(type("A", (), {"state": "off"})())
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["enabled"] is False
    assert out["stopped_playback"] is True
    assert out["previous"] is True
    assert any(p.get("cmd") == "reload_config" for p in sent)
    assert any(p.get("cmd") == "skip" for p in sent)


def test_cmd_power_on(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "focus_audio.cli.ensure_default_config",
        lambda: Config(enabled=False),
    )
    monkeypatch.setattr("focus_audio.cli.is_daemon_alive", lambda: False)
    monkeypatch.setattr("focus_audio.cli.try_send", lambda *a, **k: {"ok": True})
    monkeypatch.setattr("focus_audio.cli.save_config", lambda cfg, path=None: path)

    rc = cli.cmd_power(type("A", (), {"state": "on"})())
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["enabled"] is True
    assert out["stopped_playback"] is False
