"""xAI Text-to-Speech client."""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, List, Optional

from .config import Config
from .paths import secure_mkdir, secure_write_bytes

MAX_CHARS = 14000  # stay under 15k API limit with headroom


def synthesize_speech(
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
            "xAI API key not found. Set your own key via "
            f"${cfg.api_key_env} or macOS Keychain service `xai-api-key` "
            "(account $USER). Run: focus-audio doctor"
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
