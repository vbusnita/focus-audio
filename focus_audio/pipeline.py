"""End-to-end: source text → script → audio (with cache + chunked TTS)."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from . import brief as brief_mod
from .cache import CacheEntry, entry_for, cache_key, lookup, write_meta
from .config import Config
from .paths import last_brief_path
from .tts import concat_mp3, synthesize_speech
from .transcript import clean_for_audio


@dataclass
class PreparedAudio:
    entry: CacheEntry
    script: str
    from_cache: bool
    mode: str
    brief_skipped: bool = False
    brief_fallback: bool = False


@dataclass
class ScriptReady:
    """Script resolved; audio may still need synthesis."""

    entry: CacheEntry
    script: str
    from_cache: bool
    mode: str
    cleaned: str
    brief_skipped: bool = False
    brief_fallback: bool = False


def resolve_script(
    source_text: str,
    cfg: Config,
    *,
    mode: Optional[str] = None,
    force: bool = False,
    already_cleaned: bool = False,
    skip_llm: bool = False,
) -> ScriptReady:
    """Clean source, hit cache or produce spoken script (no TTS yet)."""
    use_mode = (mode or cfg.mode or "brief").lower()
    cleaned = source_text if already_cleaned else clean_for_audio(source_text, use_mode)
    # Live mode uses a distinct cache namespace so post-turn briefs stay separate.
    cache_mode = f"{use_mode}+live" if skip_llm and use_mode == "verbatim" else use_mode

    if not force:
        hit = lookup(cleaned, cache_mode, cfg.voice_id, cfg.speed, cfg.model)
        if hit:
            script = hit.script_path.read_text(encoding="utf-8")
            _write_last_brief(script, cache_mode, from_cache=True)
            return ScriptReady(
                entry=hit,
                script=script,
                from_cache=True,
                mode=cache_mode,
                cleaned=cleaned,
            )

    key = cache_key(cleaned, cache_mode, cfg.voice_id, cfg.speed, cfg.model)
    ent = entry_for(key, cache_mode, len(cleaned))

    brief_err: Optional[str] = None
    brief_skipped = skip_llm or brief_mod.should_skip_llm(cleaned, cfg, use_mode)
    try:
        script = brief_mod.synthesize_script(
            cleaned, cfg, mode=use_mode, skip_llm=skip_llm
        )
    except Exception as e:
        brief_err = str(e)
        brief_skipped = False
        if use_mode == "verbatim":
            script = brief_mod.fallback_script(cleaned, max_words=2000 if skip_llm else 400)
        else:
            script = brief_mod.fallback_script(cleaned, max_words=cfg.max_brief_words)

    ent.script_path.parent.mkdir(parents=True, exist_ok=True)
    ent.script_path.write_text(script, encoding="utf-8")
    _write_last_brief(script, cache_mode, from_cache=False)
    return ScriptReady(
        entry=ent,
        script=script,
        from_cache=False,
        mode=cache_mode,
        cleaned=cleaned,
        brief_skipped=brief_skipped,
        brief_fallback=bool(brief_err),
    )


def prepare_audio(
    source_text: str,
    cfg: Config,
    *,
    mode: Optional[str] = None,
    force: bool = False,
    already_cleaned: bool = False,
) -> PreparedAudio:
    """Full sync path: script + complete audio file (used by CLI)."""
    ready = resolve_script(
        source_text,
        cfg,
        mode=mode,
        force=force,
        already_cleaned=already_cleaned,
    )
    if ready.from_cache:
        return PreparedAudio(
            entry=ready.entry,
            script=ready.script,
            from_cache=True,
            mode=ready.mode,
        )

    try:
        _synthesize_full(ready.script, ready.entry, cfg)
    except Exception as e:
        hint = " (brief also failed)" if ready.brief_fallback else ""
        raise RuntimeError(f"TTS failed: {e}{hint}") from e

    write_meta(
        ready.entry,
        {
            "mode": ready.mode,
            "brief_fallback": ready.brief_fallback,
            "brief_skipped": ready.brief_skipped,
            "chunked": bool(getattr(cfg, "chunk_tts", True)),
        },
    )
    return PreparedAudio(
        entry=ready.entry,
        script=ready.script,
        from_cache=False,
        mode=ready.mode,
        brief_skipped=ready.brief_skipped,
        brief_fallback=ready.brief_fallback,
    )


def _chunk_list(script: str, cfg: Config) -> List[str]:
    if not getattr(cfg, "chunk_tts", True):
        return [script]
    return brief_mod.split_for_tts(
        script,
        first_words=int(getattr(cfg, "first_chunk_words", 35) or 35),
        chunk_words=int(getattr(cfg, "chunk_words", 90) or 90),
    )


def _synthesize_full(script: str, entry: CacheEntry, cfg: Config) -> Path:
    """Synthesize all chunks (if any) and write the final cached mp3."""
    chunks = _chunk_list(script, cfg)
    if len(chunks) <= 1:
        return synthesize_speech(script, entry.audio_path, cfg)

    part_paths: List[Path] = []
    try:
        for i, text in enumerate(chunks):
            part = entry.audio_path.parent / f"{entry.key}.c{i}.mp3"
            synthesize_speech(text, part, cfg)
            part_paths.append(part)
        concat_mp3(part_paths, entry.audio_path)
    finally:
        for p in part_paths:
            try:
                p.unlink(missing_ok=True)  # type: ignore[call-arg]
            except TypeError:
                # py3.7 compat
                if p.is_file():
                    p.unlink()
            except OSError:
                pass
    return entry.audio_path


def stream_synthesize_and_play(
    ready: ScriptReady,
    cfg: Config,
    player,
    *,
    still_current: Callable[[], bool],
    autoplay: bool = True,
) -> PreparedAudio:
    """Synthesize audio; when autoplay, start speaking as soon as the first chunk is ready.

    Remaining chunks generate on a background thread while earlier audio plays.
    Final concatenated file is written to the cache path for restart.
    """
    entry = ready.entry
    if ready.from_cache:
        prepared = PreparedAudio(
            entry=entry,
            script=ready.script,
            from_cache=True,
            mode=ready.mode,
        )
        if autoplay and still_current():
            player.play(entry.audio_path)
            _wait_player(player, still_current)
        return prepared

    chunks = _chunk_list(ready.script, cfg)
    # Single-chunk path: one TTS call, then play.
    if len(chunks) <= 1:
        synthesize_speech(ready.script, entry.audio_path, cfg)
        write_meta(
            entry,
            {
                "mode": ready.mode,
                "brief_fallback": ready.brief_fallback,
                "brief_skipped": ready.brief_skipped,
                "chunked": False,
            },
        )
        prepared = PreparedAudio(
            entry=entry,
            script=ready.script,
            from_cache=False,
            mode=ready.mode,
            brief_skipped=ready.brief_skipped,
            brief_fallback=ready.brief_fallback,
        )
        if autoplay and still_current():
            player.play(entry.audio_path)
            _wait_player(player, still_current)
        return prepared

    # Multi-chunk: producer synths, consumer plays ASAP.
    part_q: queue.Queue = queue.Queue(maxsize=2)
    errors: List[BaseException] = []
    part_paths: List[Path] = []
    parts_lock = threading.Lock()

    def producer() -> None:
        try:
            for i, text in enumerate(chunks):
                if not still_current():
                    break
                part = entry.audio_path.parent / f"{entry.key}.c{i}.mp3"
                synthesize_speech(text, part, cfg)
                with parts_lock:
                    part_paths.append(part)
                part_q.put(part)
            part_q.put(None)
        except BaseException as e:  # noqa: BLE001 — surface to job thread
            errors.append(e)
            part_q.put(None)

    prod = threading.Thread(target=producer, daemon=True, name="focus-audio-tts")
    prod.start()

    played_any = False
    try:
        while True:
            if not still_current():
                player.stop()
                break
            try:
                path = part_q.get(timeout=0.15)
            except queue.Empty:
                continue
            if path is None:
                break
            if not autoplay:
                # Still drain / wait for full synth without playing mid-stream.
                continue
            if not still_current():
                player.stop()
                break
            player.play(path)
            played_any = True
            _wait_player(player, still_current)
            if not still_current():
                break
    finally:
        # Let producer finish or abandon after cancel.
        if still_current():
            prod.join(timeout=180)
        else:
            prod.join(timeout=0.5)

    if errors and still_current():
        raise RuntimeError(f"TTS failed: {errors[0]}") from errors[0]

    with parts_lock:
        finished = list(part_paths)

    if finished and still_current():
        try:
            concat_mp3(finished, entry.audio_path)
            # Prefer full file for restart after streaming finishes.
            if hasattr(player, "set_current"):
                player.set_current(entry.audio_path)
            else:
                player._current = entry.audio_path  # noqa: SLF001
            write_meta(
                entry,
                {
                    "mode": ready.mode,
                    "brief_fallback": ready.brief_fallback,
                    "brief_skipped": ready.brief_skipped,
                    "chunked": True,
                    "chunks": len(finished),
                },
            )
        except Exception:
            # If concat fails but we already played parts, still report partial success.
            if not played_any:
                raise
        finally:
            _cleanup_parts(finished, entry.audio_path)
    else:
        # Cancelled or empty — drop partial segment files.
        _cleanup_parts(finished, entry.audio_path)
        if not finished and still_current():
            raise RuntimeError("TTS produced no audio chunks")

    return PreparedAudio(
        entry=entry,
        script=ready.script,
        from_cache=False,
        mode=ready.mode,
        brief_skipped=ready.brief_skipped,
        brief_fallback=ready.brief_fallback,
    )


def _wait_player(player, still_current: Callable[[], bool]) -> None:
    while player.is_playing():
        if not still_current():
            player.stop()
            break
        time.sleep(0.08)


def _cleanup_parts(parts: List[Path], keep: Path) -> None:
    for p in parts:
        try:
            if p.is_file() and p.resolve() != keep.resolve():
                p.unlink()
        except OSError:
            pass


def _write_last_brief(script: str, mode: str, from_cache: bool) -> None:
    path = last_brief_path()
    header = f"# Focus Audio last brief\n\nmode: `{mode}` · cache: `{from_cache}`\n\n---\n\n"
    path.write_text(header + script + "\n", encoding="utf-8")


def prepare_from_session(
    session_id: str,
    cfg: Config,
    *,
    cwd: Optional[str] = None,
    mode: Optional[str] = None,
    force: bool = False,
) -> Optional[PreparedAudio]:
    from .transcript import load_turn

    turn = load_turn(session_id, cwd=cwd, min_chars=cfg.min_chars)
    if not turn:
        return None
    return prepare_audio(
        turn.cleaned,
        cfg,
        mode=mode,
        force=force,
        already_cleaned=True,
    )


def resolve_from_session(
    session_id: str,
    cfg: Config,
    *,
    cwd: Optional[str] = None,
    mode: Optional[str] = None,
    force: bool = False,
) -> Optional[ScriptReady]:
    from .transcript import load_turn

    turn = load_turn(session_id, cwd=cwd, min_chars=cfg.min_chars)
    if not turn:
        return None
    return resolve_script(
        turn.cleaned,
        cfg,
        mode=mode,
        force=force,
        already_cleaned=True,
    )
