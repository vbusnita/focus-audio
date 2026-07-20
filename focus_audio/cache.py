"""Content-hash cache for spoken briefs and audio files."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .paths import cache_dir, secure_write_text


@dataclass
class CacheEntry:
    key: str
    mode: str
    script_path: Path
    audio_path: Path
    source_chars: int

    def exists(self) -> bool:
        return self.script_path.is_file() and self.audio_path.is_file()


# Bump when synthesis layout / defaults change so old clips are not reused.
CACHE_VERSION = "v3-provider"


def cache_key(
    source: str,
    mode: str,
    voice_id: str,
    speed: float,
    model: str,
    provider: str = "xai",
) -> str:
    h = hashlib.sha256()
    h.update(CACHE_VERSION.encode())
    h.update(b"|")
    h.update((provider or "xai").encode())
    h.update(b"|")
    h.update(mode.encode())
    h.update(b"|")
    h.update(voice_id.encode())
    h.update(b"|")
    h.update(f"{speed:.2f}".encode())
    h.update(b"|")
    h.update(model.encode())
    h.update(b"|")
    h.update(source.encode("utf-8"))
    return h.hexdigest()[:24]


def entry_for(
    key: str,
    mode: str,
    source_chars: int,
    *,
    audio_suffix: str = ".mp3",
) -> CacheEntry:
    base = cache_dir() / key
    suffix = audio_suffix if audio_suffix.startswith(".") else f".{audio_suffix}"
    return CacheEntry(
        key=key,
        mode=mode,
        script_path=base.with_suffix(".txt"),
        audio_path=base.with_suffix(suffix),
        source_chars=source_chars,
    )


def lookup(
    source: str,
    mode: str,
    voice_id: str,
    speed: float,
    model: str,
    provider: str = "xai",
    *,
    audio_suffix: str = ".mp3",
) -> Optional[CacheEntry]:
    key = cache_key(source, mode, voice_id, speed, model, provider)
    ent = entry_for(key, mode, len(source), audio_suffix=audio_suffix)
    if ent.exists():
        return ent
    return None


def write_meta(ent: CacheEntry, extra: Optional[dict] = None) -> None:
    meta = {
        "key": ent.key,
        "mode": ent.mode,
        "source_chars": ent.source_chars,
        "script": str(ent.script_path),
        "audio": str(ent.audio_path),
    }
    if extra:
        meta.update(extra)
    path = cache_dir() / f"{ent.key}.json"
    secure_write_text(path, json.dumps(meta, indent=2))


def read_script(ent: CacheEntry) -> str:
    return ent.script_path.read_text(encoding="utf-8")
