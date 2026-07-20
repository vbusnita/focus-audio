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
    # Off by default so live does not also play a post-turn brief (double speak).
    assert cfg.live_then_brief is False
    assert cfg.live_skip_stop_brief is True
    # Free packaging: verbatim + macOS by default (smart brief is xAI opt-in).
    assert cfg.mode == "verbatim"
    assert cfg.tts_provider == "macos"


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
    # Verbatim after live would re-read the same reply — not live+verbatim.
    d.cfg.mode = "verbatim"
    assert d.effective_label() == "live_verbatim"


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


def test_enqueue_live_covers_mid_first_clip_before_spoken():
    """Stop mid-first-message must not cancel live once a segment is accepted."""
    d = FocusAudioDaemon(
        cfg=Config(mode="brief", live_verbatim=True, live_then_brief=True)
    )
    d._live_session_id = "s1"
    # on_accepted bumps segments before play finishes; spoken stays 0 mid-clip.
    d._live_segments = 1
    d._live_spoken = 0
    d._live_active = True
    d._status = "live_playing"
    scheduled: list = []
    d._schedule_after_live_brief = (  # type: ignore[method-assign]
        lambda sid, cwd: scheduled.append((sid, cwd))
    )
    # Must not start a post-turn job that hard-cancels live.
    with patch("focus_audio.daemon.threading.Thread") as thr:
        out = d.enqueue_session("s1", "/tmp", force=False)
    assert out.get("deferred") == "live_then_brief"
    assert scheduled == [("s1", "/tmp")]
    assert d._live_active is True
    # No cancel path: job thread for post-turn brief must not start here.
    thr.assert_not_called()


def test_enqueue_empty_live_watcher_falls_through_to_post_turn():
    """Bare live_active with no accepted/spoken/pending must not silence the turn."""
    d = FocusAudioDaemon(
        cfg=Config(
            mode="verbatim",
            live_verbatim=True,
            live_then_brief=False,
            live_skip_stop_brief=True,
        )
    )
    d._live_session_id = "s1"
    d._live_segments = 0
    d._live_spoken = 0
    d._live_active = True
    d._status = "live"
    d._live_queue = None
    with patch("focus_audio.daemon.threading.Thread") as thr:
        out = d.enqueue_session("s1", "/tmp", force=False)
    assert out.get("skipped") is None
    assert out.get("deferred") is None
    assert out.get("ok") is True
    assert "job" in out
    # Post-turn job thread starts (cancels empty live watcher).
    thr.assert_called()


def test_enqueue_live_covers_when_queue_has_pending():
    from focus_audio.live import LiveSegment, LiveSegmentQueue

    d = FocusAudioDaemon(
        cfg=Config(mode="verbatim", live_verbatim=True, live_then_brief=True)
    )
    d._live_session_id = "s1"
    d._live_segments = 0
    d._live_active = False  # edge: inactive but queue still draining
    q = LiveSegmentQueue()
    q.put(
        LiveSegment(
            text="queued",
            cleaned="queued live message that is long enough to speak aloud",
            index=0,
        )
    )
    d._live_queue = q
    out = d.enqueue_session("s1", force=False)
    assert out.get("skipped") == "live_covered_verbatim"
    assert d._live_queue is q  # not cleared
    assert q.pending() == 1


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


def test_enqueue_live_covered_skips_post_when_mode_verbatim():
    """live + mode=verbatim + live_then_brief must not schedule a second full read."""
    d = FocusAudioDaemon(
        cfg=Config(
            mode="verbatim",
            live_verbatim=True,
            live_then_brief=True,
            live_skip_stop_brief=True,
        )
    )
    d._live_session_id = "s1"
    d._live_segments = 2
    d._live_active = True
    scheduled: list = []
    d._schedule_after_live_brief = (  # type: ignore[method-assign]
        lambda sid, cwd: scheduled.append((sid, cwd))
    )
    out = d.enqueue_session("s1", "/tmp", force=False)
    assert out.get("skipped") == "live_covered_verbatim"
    assert scheduled == []
    assert d._live_active is True


def test_mid_turn_speak_clears_live_so_stop_can_speak():
    """CLI sample speak hard-cancels live; Stop must not stay silenced by stale coverage."""
    d = FocusAudioDaemon(
        cfg=Config(
            mode="verbatim",
            live_verbatim=True,
            live_skip_stop_brief=True,
            live_then_brief=False,
        )
    )
    d._live_session_id = "s1"
    d._live_segments = 2
    d._live_spoken = 1
    d._live_word_count = 40
    d._live_active = True
    d._live_queue = MagicMock()

    jobs: list = []

    def fake_thread(*args, **kwargs):
        jobs.append(kwargs.get("args") or args)
        m = MagicMock()
        m.start = MagicMock()
        return m

    with patch("focus_audio.daemon.threading.Thread", side_effect=fake_thread):
        speak_out = d.enqueue_text("sample voice check only", force=True)

    assert speak_out.get("ok") is True
    assert d._live_active is False
    assert d._live_session_id is None
    assert d._live_segments == 0
    assert d._live_spoken == 0

    with patch("focus_audio.daemon.threading.Thread", side_effect=fake_thread):
        stop_out = d.enqueue_session("s1", force=False)

    assert "skipped" not in stop_out
    assert stop_out.get("ok") is True
    assert stop_out.get("job") is not None


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
    # after_live is forwarded into the session job so it can skip redundant plays.
    # args: (gen, session_id, cwd, force, mode, after_live, transcript_path)
    job_args = jobs[0]
    assert job_args[5] is True  # after_live flag


def test_after_live_skips_when_brief_would_repeat_live():
    """Short replies skip the LLM rewrite → same text live already spoke; do not replay."""
    d = FocusAudioDaemon(
        cfg=Config(mode="brief", live_verbatim=True, live_then_brief=True)
    )
    d._live_spoken = 2
    d._job_gen = 1
    ready = MagicMock()
    ready.brief_skipped = True
    ready.mode = "brief"
    ready.entry = MagicMock()
    ready.script = "same short text"
    ready.from_cache = False
    ready.brief_fallback = False
    played: list = []
    d._stream_play = lambda gen, r: played.append(r)  # type: ignore[method-assign]
    d._play_chime = lambda: None  # type: ignore[method-assign]

    with patch("focus_audio.daemon.resolve_from_session", return_value=ready):
        d._run_session_job(1, "s1", "/w", False, None, after_live=True)
    assert played == []


def test_after_live_plays_distinct_brief_when_llm_ran():
    d = FocusAudioDaemon(
        cfg=Config(mode="brief", live_verbatim=True, live_then_brief=True)
    )
    d._live_spoken = 2
    # Live only spoke a little of a long reply → recap is useful.
    d._live_word_count = 20
    d._job_gen = 1
    ready = MagicMock()
    ready.brief_skipped = False  # real summary rewrite
    ready.mode = "brief"
    ready.cleaned = " ".join(["word"] * 200)  # long source; coverage low
    ready.entry = MagicMock()
    ready.script = "short summary of the turn"
    ready.from_cache = False
    ready.brief_fallback = False
    played: list = []
    d._stream_play = lambda gen, r: played.append(r)  # type: ignore[method-assign]
    d._play_chime = lambda: None  # type: ignore[method-assign]

    with patch("focus_audio.daemon.resolve_from_session", return_value=ready):
        d._run_session_job(1, "s1", "/w", False, None, after_live=True)
    assert played == [ready]


def test_after_live_skips_when_live_coverage_high():
    """Live already spoke most of the cleaned source → no second pass."""
    d = FocusAudioDaemon(
        cfg=Config(mode="brief", live_verbatim=True, live_then_brief=True)
    )
    d._live_spoken = 3
    d._live_word_count = 90
    d._job_gen = 1
    ready = MagicMock()
    ready.brief_skipped = False
    ready.mode = "brief"
    ready.cleaned = " ".join(["word"] * 100)  # coverage 0.9
    ready.entry = MagicMock()
    ready.script = "a rewritten brief"
    ready.from_cache = False
    ready.brief_fallback = False
    played: list = []
    d._stream_play = lambda gen, r: played.append(r)  # type: ignore[method-assign]
    d._play_chime = lambda: None  # type: ignore[method-assign]

    with patch("focus_audio.daemon.resolve_from_session", return_value=ready) as res:
        d._run_session_job(1, "s1", "/w", False, "verbatim", after_live=True)
    assert played == []
    # After-live always requests brief even if caller passed verbatim.
    assert res.call_args.kwargs["mode"] == "brief"


def test_live_on_defaults_to_live_only_effective():
    """live_verbatim without live_then_brief → live_verbatim, not live+brief."""
    d = FocusAudioDaemon(
        cfg=Config(mode="brief", live_verbatim=True)  # live_then_brief default False
    )
    assert d.cfg.live_then_brief is False
    assert d.effective_label() == "live_verbatim"


def test_status_includes_effective():
    d = FocusAudioDaemon(
        cfg=Config(mode="brief", live_verbatim=True, live_then_brief=True)
    )
    st = d.status()
    assert st["mode"] == "brief"
    assert st["effective"] == "live+brief"
    assert st["live"]["then_brief"] is True
    assert st["config"]["live_then_brief"] is True
