"""Text-to-speech backends: xAI cloud TTS and free macOS ``say``."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, List, Optional

from .config import Config
from .paths import secure_mkdir, secure_write_bytes

MAX_CHARS = 14000  # stay under 15k API limit with headroom
# macOS say is local; keep a generous cap so runaway scripts cannot fill disk.
MAX_MACOS_CHARS = 100_000
# Base speaking rate for speed=1.0 (words per minute). say -r uses WPM.
MACOS_BASE_WPM = 175


def speed_to_wpm(speed: float) -> int:
    """Map Focus Audio speed multiplier to macOS ``say -r`` words-per-minute."""
    try:
        s = float(speed)
    except (TypeError, ValueError):
        s = 1.0
    if s <= 0:
        s = 1.0
    wpm = int(round(MACOS_BASE_WPM * s))
    return max(90, min(400, wpm))


def synthesize_speech(
    text: str,
    out_path: Path,
    cfg: Config,
    *,
    voice_id: Optional[str] = None,
) -> Path:
    """Synthesize speech to out_path using the resolved TTS provider."""
    provider = cfg.effective_tts_provider()
    if provider == "macos":
        return _synthesize_macos(text, out_path, cfg, voice_id=voice_id)
    return _synthesize_xai(text, out_path, cfg, voice_id=voice_id)


def _synthesize_xai(
    text: str,
    out_path: Path,
    cfg: Config,
    *,
    voice_id: Optional[str] = None,
) -> Path:
    """Call POST /v1/tts and write audio bytes to out_path (mp3)."""
    api_key = cfg.api_key()
    if not api_key:
        raise RuntimeError(
            "xAI API key not found (tts_provider needs cloud TTS). Set your own key via "
            f"${cfg.api_key_env} or macOS Keychain service `xai-api-key` "
            "(account $USER), or set tts_provider = \"macos\" / \"auto\" for free "
            "local speech. Run: focus-audio doctor"
        )

    speak = text.strip()
    if len(speak) > MAX_CHARS:
        speak = speak[:MAX_CHARS] + " …"

    bit_rate = int(getattr(cfg, "tts_bit_rate", 96000) or 96000)
    # Clamp to common TTS-friendly range.
    if bit_rate < 32000:
        bit_rate = 32000
    if bit_rate > 192000:
        bit_rate = 192000

    url = cfg.api_base.rstrip("/") + "/tts"
    body = {
        "text": speak,
        "voice_id": voice_id or cfg.voice_id,
        "language": cfg.language,
        "speed": cfg.speed,
        "text_normalization": True,
        "output_format": {
            "codec": "mp3",
            "sample_rate": 24000,
            "bit_rate": bit_rate,
        },
    }
    import json

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            audio = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"TTS API error {e.code}: {detail}") from e

    if not audio:
        raise RuntimeError("Empty TTS response")

    secure_mkdir(out_path.parent)
    secure_write_bytes(out_path, audio)
    return out_path


def _synthesize_macos(
    text: str,
    out_path: Path,
    cfg: Config,
    *,
    voice_id: Optional[str] = None,
) -> Path:
    """Synthesize with macOS ``say`` into an AIFF (or other) file for local playback."""
    say = shutil.which("say") or "/usr/bin/say"
    if not Path(say).is_file():
        raise RuntimeError(
            "macOS `say` not found. Install Command Line Tools or use tts_provider = \"xai\"."
        )

    speak = (text or "").strip()
    if not speak:
        raise RuntimeError("Empty text for macOS TTS")
    if len(speak) > MAX_MACOS_CHARS:
        speak = speak[:MAX_MACOS_CHARS] + " …"

    out_path = Path(out_path)
    # say prefers AIFF; normalize odd suffixes so the player always has a real file type.
    if out_path.suffix.lower() not in (".aiff", ".aif", ".caf", ".wav", ".m4a"):
        out_path = out_path.with_suffix(".aiff")

    secure_mkdir(out_path.parent)
    # Remove stale partials so a failed run cannot leave a zero-byte playable path.
    try:
        if out_path.is_file():
            out_path.unlink()
    except OSError:
        pass

    voice = (voice_id if voice_id is not None else cfg.effective_voice_id()).strip()
    cmd: List[str] = [say, "-o", str(out_path), "-r", str(speed_to_wpm(cfg.speed))]
    if voice and voice.lower() not in ("system", "default"):
        cmd.extend(["-v", voice])

    # Write script to a temp file so long replies and quotes never break argv.
    fd, tmp_name = tempfile.mkstemp(prefix="focus-audio-say-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(speak)
        cmd.extend(["-f", tmp_name])
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                timeout=300,
                capture_output=True,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("macOS say timed out after 300s") from e
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"macOS say failed (exit {proc.returncode})"
                + (f": {err}" if err else "")
            )
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass

    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise RuntimeError("macOS say produced no audio file")
    return out_path


def concat_mp3(paths: Iterable[Path], out_path: Path) -> Path:
    """Concatenate same-encoder MP3 segments by simple byte join.

    xAI TTS returns consistent CBR-ish frames for a given format request, so
    frame-level concat is good enough for local playback / restart.
    """
    parts: List[Path] = [Path(p) for p in paths if Path(p).is_file()]
    if not parts:
        raise RuntimeError("No MP3 parts to concatenate")
    out_path = Path(out_path)
    secure_mkdir(out_path.parent)
    if len(parts) == 1:
        data = parts[0].read_bytes()
        secure_write_bytes(out_path, data)
        return out_path
    buf = bytearray()
    for p in parts:
        buf.extend(p.read_bytes())
    secure_write_bytes(out_path, bytes(buf))
    return out_path
