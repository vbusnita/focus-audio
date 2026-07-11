"""Mode toggle + effective label + live_then_brief scheduling (no live API)."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from focus_audio.config import Config  # noqa: E402
from focus_audio.daemon import FocusAudioDaemon  # noqa: E402


def test_config_live_then_brief_default():
    cfg = Config()
    assert cfg.live_then_brief is True
    assert cfg.live_skip_stop_brief is True
    assert cfg.mode == "brief"


def test_effective_label_plain_modes():
    d = FocusAudioDaemon(cfg=Config(mode="brief", live_verbatim=False))
    assert d.effective_label() == "brief"
    d.cfg.mode = "verbatim"
    assert d.effective_label() == "verbatim"


def test_effective_label_live_plus_mode():
    d = FocusAudioDaemon(
        cfg=Config(mode="brief", live_verbatim=True, live_then_brief=True)
    )
    assert d.effective_label() == "live+brief"
    d.cfg.mode = "verbatim"
    assert d.effective_label() == "live+verbatim"


def test_effective_label_live_only_when_then_brief_off():
    d = FocusAudioDaemon(
        cfg=Config(
            mode="brief",
            live_verbatim=True,
            live_then_brief=False,
            live_skip_stop_brief=True,
        )
    )
    assert d.effective_label() == "live_verbatim"


def test_effective_label_during_live_active():
    d = FocusAudioDaemon(
        cfg=Config(mode="brief", live_verbatim=True, live_then_brief=True)
    )
    d._live_active = True
    d._status = "live_playing"
    assert d.effective_label() == "live_verbatim"


def test_mode_toggle_saves_and_announces(tmp_path: Path, monkeypatch):
    cfg = Config(mode="brief", live_verbatim=False, live_then_brief=True)
    d = FocusAudioDaemon(cfg=cfg)
    d._last_session = {"session_id": "sess-1", "cwd": str(tmp_path)}
    d._last_source = None

    saved: list = []
    announced: list = []
    replays: list = []

    monkeypatch.setattr(
        "focus_audio.daemon.save_config",
        lambda c: saved.append(c.mode),
    )
    d._announce_mode = lambda mode: announced.append(mode)  # type: ignore[method-assign]
    d._replay_last_for_mode = lambda: replays.append("ok") or {  # type: ignore[method-assign]
        "ok": True,
        "job": 1,
    }
    d.player.stop = MagicMock()  # type: ignore[method-assign]

    out = d.handle({"cmd": "mode"})
    assert out["ok"] is True
    assert out["mode"] == "verbatim"
    assert out["previous"] == "brief"
    assert out["rebrief"] is True
    assert saved == ["verbatim"]

    # Background job runs announce then cache-friendly replay
    deadline = time.time() + 2.0
    while (not announced or not replays) and time.time() < deadline:
        time.sleep(0.02)
    assert announced == ["verbatim"]
    assert replays == ["ok"]


def test_mode_set_explicit():
    d = FocusAudioDaemon(cfg=Config(mode="brief"))
    with patch("focus_audio.daemon.save_config"), patch.object(
        d, "_announce_mode"
    ), patch.object(
        d, "_replay_last_for_mode", return_value={"ok": False, "error": "nothing to rebrief"}
    ):
        d.player.stop = MagicMock()  # type: ignore[method-assign]
        out = d.handle({"cmd": "mode", "mode": "verbatim"})
    assert out["mode"] == "verbatim"
    # Second set same mode still ok
    with patch("focus_audio.daemon.save_config"), patch.object(
        d, "_announce_mode"
    ), patch.object(
        d, "_replay_last_for_mode", return_value={"ok": False, "error": "nothing to rebrief"}
    ):
        d._last_mode_toggle_at = 0.0  # clear debounce
        out2 = d.handle({"cmd": "mode", "mode": "brief"})
    assert out2["mode"] == "brief"


def test_mode_debounce():
    d = FocusAudioDaemon(cfg=Config(mode="brief"))
    with patch("focus_audio.daemon.save_config"), patch.object(
        d, "_announce_mode"
    ), patch.object(
        d, "_replay_last_for_mode", return_value={"ok": False, "error": "nothing"}
    ):
        d.player.stop = MagicMock()  # type: ignore[method-assign]
        first = d.handle({"cmd": "mode"})
        second = d.handle({"cmd": "mode"})
    assert first["mode"] == "verbatim"
    assert second.get("debounced") is True
    assert second["mode"] == "verbatim"  # not flipped again


def test_replay_last_for_mode_uses_cache_not_force():
    d = FocusAudioDaemon(cfg=Config(mode="verbatim"))
    d._last_source = "hello world this is long enough source text for a brief"
    d._last_session = None
    calls: list = []

    def capture_text(text, force=False, mode=None):
        calls.append({"text": text, "force": force, "mode": mode})
        return {"ok": True, "job": 9}

    d.enqueue_text = capture_text  # type: ignore[method-assign]
    out = d._replay_last_for_mode()
    assert out["ok"] is True
    assert calls == [
        {
            "text": "hello world this is long enough source text for a brief",
            "force": False,
            "mode": None,
        }
    ]


def test_replay_last_for_mode_session_after_live_flag():
    d = FocusAudioDaemon(cfg=Config(mode="brief"))
    d._last_session = {"session_id": "s1", "cwd": "/w"}
    d._last_source = "ignored when session present"
    calls: list = []

    def capture_sess(sid, cwd=None, force=False, mode=None, after_live=False):
        calls.append(
            {
                "sid": sid,
                "cwd": cwd,
                "force": force,
                "after_live": after_live,
            }
        )
        return {"ok": True, "job": 3}

    d.enqueue_session = capture_sess  # type: ignore[method-assign]
    out = d._replay_last_for_mode()
    assert out["ok"] is True
    assert calls == [
        {"sid": "s1", "cwd": "/w", "force": False, "after_live": True}
    ]


def test_enqueue_live_covered_defers_when_live_then_brief():
    d = FocusAudioDaemon(
        cfg=Config(mode="brief", live_verbatim=True, live_then_brief=True)
    )
    d._live_session_id = "s1"
    d._live_segments = 2
    d._live_active = True
    scheduled: list = []
    d._schedule_after_live_brief = (  # type: ignore[method-assign]
        lambda sid, cwd: scheduled.append((sid, cwd))
    )
    out = d.enqueue_session("s1", "/tmp", force=False)
    assert out.get("deferred") == "live_then_brief"
    assert scheduled == [("s1", "/tmp")]
    assert d._live_active is True  # must not cancel live


def test_enqueue_live_covered_skips_when_then_brief_off():
    d = FocusAudioDaemon(
        cfg=Config(
            mode="brief",
            live_verbatim=True,
            live_then_brief=False,
            live_skip_stop_brief=True,
        )
    )
    d._live_session_id = "s1"
    d._live_segments = 1
    d._live_active = True
    out = d.enqueue_session("s1", force=False)
    assert out.get("skipped") == "live_covered"


def test_enqueue_after_live_bypasses_skip():
    d = FocusAudioDaemon(
        cfg=Config(mode="brief", live_verbatim=True, live_then_brief=True)
    )
    d._live_session_id = "s1"
    d._live_segments = 3
    d._live_active = False
    jobs: list = []

    def fake_thread(*args, **kwargs):
        jobs.append(kwargs.get("args") or args)
        m = MagicMock()
        m.start = MagicMock()
        return m

    with patch("focus_audio.daemon.threading.Thread", side_effect=fake_thread):
        out = d.enqueue_session("s1", "/w", force=False, after_live=True)
    assert out.get("ok") is True
    assert out.get("job") is not None
    assert len(jobs) == 1


def test_status_includes_effective():
    d = FocusAudioDaemon(
        cfg=Config(mode="brief", live_verbatim=True, live_then_brief=True)
    )
    st = d.status()
    assert st["mode"] == "brief"
    assert st["effective"] == "live+brief"
    assert st["live"]["then_brief"] is True
    assert st["config"]["live_then_brief"] is True
