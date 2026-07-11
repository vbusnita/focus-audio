"""Player pause/resume — AVAudioPlayer keeps sample position."""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from focus_audio.player import Player  # noqa: E402


def _make_tone_mp3(path: Path, seconds: float = 3.0) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not available")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-q:a",
            "9",
            "-acodec",
            "libmp3lame",
            str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return path


@pytest.fixture
def mp3(tmp_path: Path) -> Path:
    return _make_tone_mp3(tmp_path / "clip.mp3", seconds=4.0)


def test_toggle_pauses_and_resumes_without_skip(mp3: Path) -> None:
    p = Player()
    p.play(mp3)
    time.sleep(0.7)
    assert p.is_playing()
    assert not p.is_paused()

    pos_before = p.position_seconds()
    assert p.toggle() == "paused"
    assert p.is_paused()
    assert p.is_playing()  # still active for wait loops

    pos_paused = p.position_seconds()
    # Stay paused — wall clock advances; sample position must not.
    time.sleep(0.9)
    pos_still = p.position_seconds()
    if pos_paused is not None and pos_still is not None:
        assert abs(pos_still - pos_paused) < 0.08, (pos_paused, pos_still)

    assert p.toggle() == "playing"
    assert not p.is_paused()
    assert p.is_playing()
    time.sleep(0.2)
    pos_after = p.position_seconds()

    # Must not jump by the ~0.9s wall-clock pause (the original SIGSTOP bug).
    if pos_before is not None and pos_after is not None:
        assert pos_after >= (pos_before - 0.15)
        assert pos_after < pos_before + 0.9, (
            f"skipped ahead: before={pos_before} after={pos_after}"
        )

    p.stop()
    assert not p.is_playing()
    assert not p.is_paused()


def test_restart_replays_from_start(mp3: Path) -> None:
    p = Player()
    p.play(mp3)
    time.sleep(0.5)
    assert p.restart() is True
    time.sleep(0.15)
    assert p.is_playing()
    pos = p.position_seconds()
    if pos is not None:
        assert pos < 0.6
    p.stop()


def test_resume_after_stop_replays(mp3: Path) -> None:
    p = Player()
    p.play(mp3)
    time.sleep(0.2)
    p.stop()
    assert not p.is_playing()
    assert p.resume() is True
    assert p.is_playing()
    p.stop()


def test_toggle_idle_without_file() -> None:
    p = Player()
    assert p.toggle() == "idle"


def test_pause_noop_when_idle() -> None:
    p = Player()
    p.pause()
    assert not p.is_paused()


def test_stop_while_paused(mp3: Path) -> None:
    p = Player()
    p.play(mp3)
    time.sleep(0.25)
    p.pause()
    assert p.is_paused()
    p.stop()
    assert not p.is_playing()
    assert not p.is_paused()


def test_natural_finish(mp3: Path) -> None:
    short = mp3.parent / "short.mp3"
    _make_tone_mp3(short, seconds=0.6)
    p = Player()
    p.play(short)
    deadline = time.time() + 5
    while p.is_playing() and time.time() < deadline:
        time.sleep(0.05)
    assert not p.is_playing()
    assert not p.is_paused()


def test_toggle_logic_with_mocks() -> None:
    """Unit-level toggle state machine without real audio (afplay path)."""
    p = Player()
    proc = MagicMock()
    proc.poll.return_value = None
    p._proc = proc
    p._backend = "afplay"
    p._current = Path("/tmp/fake.mp3")
    p._paused = False

    assert p.toggle() == "paused"
    proc.send_signal.assert_called()
    assert p._paused is True

    proc.send_signal.reset_mock()
    assert p.toggle() == "playing"
    assert p._paused is False
