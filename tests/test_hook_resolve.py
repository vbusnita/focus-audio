"""Tests for envelope-first session resolve and Stop reason filtering."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from focus_audio import cli as focus_cli  # noqa: E402


def _ns(**kwargs) -> argparse.Namespace:
    defaults = {
        "session_id": None,
        "cwd": None,
        "force": False,
        "mode": None,
        "verbose": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_resolve_prefers_env_and_payload_over_fallbacks():
    payload = {
        "sessionId": "from-payload",
        "workspaceRoot": "/Users/example/ws",
        "transcriptPath": "/tmp/ignored/updates.jsonl",
    }
    env = {
        "GROK_SESSION_ID": "from-env",
        "GROK_WORKSPACE_ROOT": "/Users/example/from-env",
    }
    with patch.dict(os.environ, env, clear=False):
        # Clear CLAUDE aliases so GROK_* win unambiguously.
        os.environ.pop("CLAUDE_SESSION_ID", None)
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        sid, cwd, tx = focus_cli._resolve_hook_session(_ns(), payload)
    assert sid == "from-env"
    assert cwd == "/Users/example/from-env"
    assert tx.endswith("updates.jsonl")


def test_resolve_payload_when_env_missing():
    payload = {
        "sessionId": "sid-payload",
        "cwd": "/work/proj",
        "transcriptPath": "/work/proj/sessions/sid-payload/updates.jsonl",
    }
    with patch.dict(
        os.environ,
        {
            "GROK_SESSION_ID": "",
            "CLAUDE_SESSION_ID": "",
            "GROK_WORKSPACE_ROOT": "",
            "CLAUDE_PROJECT_DIR": "",
        },
        clear=False,
    ):
        for k in (
            "GROK_SESSION_ID",
            "CLAUDE_SESSION_ID",
            "GROK_WORKSPACE_ROOT",
            "CLAUDE_PROJECT_DIR",
        ):
            os.environ.pop(k, None)
        sid, cwd, tx = focus_cli._resolve_hook_session(_ns(), payload)
    assert sid == "sid-payload"
    assert cwd == "/work/proj"
    assert "updates.jsonl" in tx


def test_resolve_session_from_transcript_path(tmp_path: Path):
    sid = "019f-from-tx"
    sess = tmp_path / "%2FUsers%2Fexample%2Fws" / sid
    sess.mkdir(parents=True)
    updates = sess / "updates.jsonl"
    updates.write_text("{}\n", encoding="utf-8")
    payload = {"transcriptPath": str(updates)}
    with patch.dict(os.environ, {}, clear=False):
        for k in (
            "GROK_SESSION_ID",
            "CLAUDE_SESSION_ID",
            "GROK_WORKSPACE_ROOT",
            "CLAUDE_PROJECT_DIR",
        ):
            os.environ.pop(k, None)
        sid_out, cwd, tx = focus_cli._resolve_hook_session(_ns(), payload)
    assert sid_out == sid
    assert cwd == "/Users/example/ws"
    assert tx == str(updates)


def test_should_auto_speak_end_turn():
    assert focus_cli._should_auto_speak_stop({"reason": "end_turn"}) is True
    assert focus_cli._should_auto_speak_stop({"reason": "END_TURN"}) is True


def test_should_skip_cancelled_and_error():
    assert focus_cli._should_auto_speak_stop({"reason": "cancelled"}) is False
    assert focus_cli._should_auto_speak_stop({"reason": "error"}) is False
    assert focus_cli._should_auto_speak_stop({"reason": "channel_closed"}) is False


def test_should_speak_missing_reason_and_force():
    assert focus_cli._should_auto_speak_stop({}) is True
    assert (
        focus_cli._should_auto_speak_stop({"reason": "cancelled"}, force=True) is True
    )


def test_enqueue_skips_cancelled(monkeypatch):
    monkeypatch.setattr(focus_cli, "_focus_audio_disabled", lambda: False)
    monkeypatch.setattr(focus_cli, "_config_disabled", lambda: False)
    monkeypatch.setattr(
        focus_cli,
        "_read_hook_payload",
        lambda: {"reason": "cancelled", "sessionId": "x"},
    )
    called = []

    def _no_send(*_a, **_k):
        called.append(1)
        return {}

    monkeypatch.setattr(focus_cli, "send_command", _no_send)
    rc = focus_cli.cmd_enqueue(_ns())
    assert rc == 0
    assert called == []
