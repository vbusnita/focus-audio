"""Lifecycle refcount tests (no daemon required)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from focus_audio import lifecycle  # noqa: E402


def test_acquire_release_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "refs_path", lambda: tmp_path / "session_refs.json")
    a = lifecycle.acquire_session("s1")
    assert a["count"] == 1
    b = lifecycle.acquire_session("s2")
    assert b["count"] == 2
    # re-acquire same id stays at 2
    c = lifecycle.acquire_session("s1")
    assert c["count"] == 2
    r1 = lifecycle.release_session("s1")
    assert r1["count"] == 1
    assert r1["released"] is True
    r2 = lifecycle.release_session("s2")
    assert r2["count"] == 0
    assert lifecycle.active_count() == 0


def test_release_unknown_is_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "refs_path", lambda: tmp_path / "session_refs.json")
    r = lifecycle.release_session("nope")
    assert r["count"] == 0
    assert r["released"] is False
