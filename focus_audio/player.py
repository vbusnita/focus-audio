"""Local audio playback via afplay (macOS)."""

from __future__ import annotations

import shutil
import signal
import subprocess
import threading
from pathlib import Path
from typing import Optional

from .paths import cache_dir


class Player:
    """Play one mp3 at a time. Pause = stop (afplay has no pause); restart from start."""

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._current: Optional[Path] = None
        self._afplay = shutil.which("afplay")

    @property
    def current(self) -> Optional[Path]:
        return self._current

    def is_playing(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def play(self, path: Path, *, block: bool = False) -> None:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(path)
        if not self._afplay:
            raise RuntimeError("afplay not found — Focus Audio requires macOS afplay")

        self.stop()
        with self._lock:
            self._current = path
            self._proc = subprocess.Popen(
                [self._afplay, str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            proc = self._proc
        if block and proc is not None:
            proc.wait()

    def stop(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except ProcessLookupError:
                pass
        # Orphan cleanup: multi-daemon / race can leave extra afplay processes.
        _stop_orphan_focus_afplay()

    def pause(self) -> None:
        """afplay cannot pause mid-file; stop is the practical equivalent."""
        self.stop()

    def resume(self) -> bool:
        """Replay current file from the start (no true resume with afplay)."""
        if self._current and self._current.is_file():
            self.play(self._current)
            return True
        return False

    def restart(self) -> bool:
        return self.resume()

    def set_current(self, path: Path) -> None:
        """Point restart/resume at a finished full-file (after chunked playback)."""
        with self._lock:
            self._current = Path(path)

    def toggle(self) -> str:
        if self.is_playing():
            self.pause()
            return "paused"
        if self.resume():
            return "playing"
        return "idle"


def _stop_orphan_focus_afplay() -> None:
    """Best-effort: stop afplay instances playing Focus Audio cache files."""
    try:
        cache = str(cache_dir())
    except Exception:
        return
    try:
        # macOS: kill only afplay whose args mention our cache dir.
        subprocess.run(
            ["pkill", "-f", f"afplay.*{cache}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        pass
