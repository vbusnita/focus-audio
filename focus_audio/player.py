"""Local audio playback on macOS.

Primary backend: AVAudioPlayer (true pause/resume with sample position).
Fallback: afplay subprocess (SIGSTOP pause — may drift; used only if AVAudio fails).
"""

from __future__ import annotations

import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .paths import cache_dir


def _load_avaudio_player() -> Any:
    """Return the AVAudioPlayer ObjC class, or None if unavailable."""
    try:
        import objc
        from Foundation import NSBundle

        bundle = NSBundle.bundleWithPath_(
            "/System/Library/Frameworks/AVFoundation.framework"
        )
        if bundle is not None:
            bundle.load()
        cls = objc.lookUpClass("AVAudioPlayer")
        return cls
    except Exception:
        return None


_AVAudioPlayer = _load_avaudio_player()


class Player:
    """Play one audio file at a time with true mid-file pause/resume."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: Optional[Path] = None
        self._paused = False
        # AVAudioPlayer instance (preferred)
        self._av: Any = None
        # afplay fallback
        self._proc: Optional[subprocess.Popen] = None
        self._afplay = shutil.which("afplay")
        self._backend: Optional[str] = None  # "avaudio" | "afplay"

    @property
    def current(self) -> Optional[Path]:
        return self._current

    def is_playing(self) -> bool:
        """True while a clip is active (playing or paused — wait loops must not advance)."""
        with self._lock:
            return self._active_locked()

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused and self._active_locked()

    def _active_locked(self) -> bool:
        if self._backend == "avaudio" and self._av is not None:
            if self._paused:
                return True
            try:
                if self._av.isPlaying():
                    return True
            except Exception:
                pass
            # Finished naturally
            self._av = None
            self._backend = None
            self._paused = False
            return False

        if self._backend == "afplay" or self._proc is not None:
            if self._proc is None:
                self._paused = False
                self._backend = None
                return False
            if self._proc.poll() is not None:
                self._proc = None
                self._paused = False
                self._backend = None
                return False
            return True

        self._paused = False
        return False

    def play(self, path: Path, *, block: bool = False) -> None:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(path)

        self.stop()

        if _AVAudioPlayer is not None and self._play_avaudio(path):
            pass
        else:
            self._play_afplay(path)

        if block:
            while self.is_playing():
                time.sleep(0.05)

    def _play_avaudio(self, path: Path) -> bool:
        try:
            from Foundation import NSURL

            url = NSURL.fileURLWithPath_(str(path.resolve()))
            av = _AVAudioPlayer.alloc().initWithContentsOfURL_error_(url, None)
            if av is None:
                # Some PyObjC builds prefer NSData
                from Foundation import NSData

                data = NSData.dataWithContentsOfFile_(str(path.resolve()))
                if data is None:
                    return False
                av = _AVAudioPlayer.alloc().initWithData_error_(data, None)
            if av is None:
                return False
            if not av.prepareToPlay():
                return False
            if not av.play():
                return False
            with self._lock:
                self._current = path
                self._paused = False
                self._av = av
                self._backend = "avaudio"
                self._proc = None
            return True
        except Exception:
            return False

    def _play_afplay(self, path: Path) -> None:
        if not self._afplay:
            raise RuntimeError(
                "No audio backend available (AVAudioPlayer failed and afplay not found)"
            )
        with self._lock:
            self._current = path
            self._paused = False
            self._av = None
            self._backend = "afplay"
            self._proc = subprocess.Popen(
                [self._afplay, str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def stop(self) -> None:
        with self._lock:
            av = self._av
            proc = self._proc
            was_paused = self._paused
            backend = self._backend
            self._av = None
            self._proc = None
            self._paused = False
            self._backend = None

        if av is not None:
            try:
                av.stop()
            except Exception:
                pass

        if proc is not None and proc.poll() is None:
            try:
                if was_paused and backend == "afplay":
                    try:
                        proc.send_signal(signal.SIGCONT)
                    except ProcessLookupError:
                        pass
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except ProcessLookupError:
                pass

        _stop_orphan_focus_afplay()

    def pause(self) -> None:
        """Pause mid-file. AVAudioPlayer keeps sample position; afplay uses SIGSTOP."""
        with self._lock:
            if not self._active_locked() or self._paused:
                return

            if self._backend == "avaudio" and self._av is not None:
                try:
                    self._av.pause()
                    self._paused = True
                except Exception:
                    self._paused = False
                return

            if self._proc is not None:
                try:
                    self._proc.send_signal(signal.SIGSTOP)
                    self._paused = True
                except ProcessLookupError:
                    self._proc = None
                    self._backend = None
                    self._paused = False

    def resume(self) -> bool:
        """Continue a paused clip, or replay the last file from the start if stopped."""
        with self._lock:
            if self._paused and self._active_locked():
                if self._backend == "avaudio" and self._av is not None:
                    try:
                        if self._av.play():
                            self._paused = False
                            return True
                    except Exception:
                        pass
                    # Fall through to full replay
                    self._av = None
                    self._backend = None
                    self._paused = False
                elif self._proc is not None:
                    try:
                        self._proc.send_signal(signal.SIGCONT)
                        self._paused = False
                        return True
                    except ProcessLookupError:
                        self._proc = None
                        self._backend = None
                        self._paused = False
            current = self._current

        if current and current.is_file():
            self.play(current)
            return True
        return False

    def restart(self) -> bool:
        """Always replay the current file from the beginning."""
        with self._lock:
            current = self._current
        if current and current.is_file():
            self.play(current)
            return True
        return False

    def set_current(self, path: Path) -> None:
        """Point restart/resume at a finished full-file (after chunked playback)."""
        with self._lock:
            self._current = Path(path)

    def toggle(self) -> str:
        if self.is_paused():
            return "playing" if self.resume() else "idle"
        if self.is_playing():
            self.pause()
            return "paused"
        if self.resume():
            return "playing"
        return "idle"

    def position_seconds(self) -> Optional[float]:
        """Current playback position in seconds, if known (AVAudio only)."""
        with self._lock:
            if self._backend == "avaudio" and self._av is not None:
                try:
                    return float(self._av.currentTime())
                except Exception:
                    return None
            return None


def _stop_orphan_focus_afplay() -> None:
    """Best-effort: stop afplay instances playing Focus Audio cache files."""
    try:
        cache = str(cache_dir())
    except Exception:
        return
    try:
        subprocess.run(
            ["pkill", "-f", f"afplay.*{cache}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        pass
