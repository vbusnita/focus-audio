"""Background daemon: queue processing, playback, optional hotkeys."""

from __future__ import annotations

import json
import os
import queue
import signal
import socket
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config, ensure_default_config, load_config, save_config
from .ipc import is_daemon_alive
from .live import LiveSegment, LiveSegmentQueue, produce_live_segments
from .paths import (
    data_dir,
    harden_socket,
    last_brief_path,
    last_job_path,
    pid_path,
    secure_write_text,
    socket_path,
)
from .pipeline import (
    PreparedAudio,
    resolve_from_session,
    resolve_script,
    stream_synthesize_and_play,
)
from .player import Player


class FocusAudioDaemon:
    def __init__(self, cfg: Optional[Config] = None) -> None:
        self.cfg = cfg or ensure_default_config()
        self.player = Player()
        self._lock = threading.Lock()
        self._job_gen = 0
        self._active_gen = 0
        self._last_prepared: Optional[PreparedAudio] = None
        self._last_source: Optional[str] = None
        self._last_session: Optional[Dict[str, Any]] = None
        self._status = "idle"
        self._error: Optional[str] = None
        self._server: Optional[socket.socket] = None
        self._stop = threading.Event()
        # Live verbatim state (experimental mid-turn speech).
        self._live_gen = 0
        self._live_session_id: Optional[str] = None
        self._live_cwd: Optional[str] = None
        self._live_segments = 0  # accepted into queue (not only finished)
        self._live_spoken = 0  # fully played to completion
        self._live_word_count = 0  # words actually spoken mid-turn (for coverage)
        self._live_active = False
        self._live_queue: Optional[LiveSegmentQueue] = None
        # Cancel token for deferred post-live brief (live_then_brief).
        self._post_live_token = 0
        self._last_mode_toggle_at = 0.0

    # ----- job handling -----

    def enqueue_session(
        self,
        session_id: str,
        cwd: Optional[str] = None,
        *,
        force: bool = False,
        mode: Optional[str] = None,
        after_live: bool = False,
        transcript_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.cfg.enabled:
            return {"ok": True, "skipped": "disabled"}

        # Stop-hook: if live is covering this turn, let the segment queue drain
        # fully (never interrupt mid-message / drop a queued sibling message).
        with self._lock:
            live_active = self._live_active
            live_sid = self._live_session_id
            live_segs = self._live_segments
            live_q = self._live_queue
            skip_brief = bool(getattr(self.cfg, "live_skip_stop_brief", True))
            live_then = bool(getattr(self.cfg, "live_then_brief", False))
            live_spoken = int(self._live_spoken or 0)
            live_status = self._status
            # Post-turn path uses cfg.mode (or explicit override).
            post_mode = (mode or self.cfg.mode or "brief").lower()
        live_pending = bool(live_q and (not live_q.closed or live_q.pending() > 0))
        # Cover only when live has real work for this session — not bare
        # live_active. A watcher that never accepted/spoke a segment used to
        # return live_covered_* and silence the whole turn (no post-turn
        # fallback). Mid-first-clip is covered via live_segs (on_accepted) or
        # status live_playing / pending queue.
        mid_live_play = live_status == "live_playing"
        live_has_work = bool(
            live_spoken > 0 or live_segs > 0 or live_pending or mid_live_play
        )
        live_covers = bool(
            live_sid
            and session_id
            and live_sid == session_id
            and live_has_work
        )
        if not after_live and live_covers and skip_brief and not force:
            # Do not cancel live — drain the queue to the last word.
            #
            # live_then_brief is opt-in: "hear live progress, then a *brief* summary".
            # Default is off so a turn is not spoken twice (live verbatim + brief).
            # Never schedule when post mode is already verbatim (would re-read).
            # After-live playback always forces mode=brief and skips when live
            # already covered enough of the reply (see _run_session_job).
            if live_then and post_mode != "verbatim":
                self._schedule_after_live_brief(session_id, cwd)
                return {
                    "ok": True,
                    "deferred": "live_then_brief",
                    "segments": live_segs,
                    "live_active": live_active,
                    "spoken": live_spoken,
                    "mode": self.cfg.mode,
                    "effective": self.effective_label(),
                }
            reason = (
                "live_covered_verbatim"
                if post_mode == "verbatim"
                else "live_covered"
            )
            return {
                "ok": True,
                "skipped": reason,
                "segments": live_segs,
                "live_active": live_active,
                "spoken": live_spoken,
                "mode": self.cfg.mode,
            }

        # New post-turn job cancels any in-flight live watch / deferred brief.
        self._cancel_post_live()
        self._cancel_live()

        with self._lock:
            self._job_gen += 1
            gen = self._job_gen
            self._status = "queued"
            self._error = None
            self._last_session = {
                "session_id": session_id,
                "cwd": cwd,
                "transcript_path": transcript_path or "",
            }
        t = threading.Thread(
            target=self._run_session_job,
            args=(gen, session_id, cwd, force, mode, after_live, transcript_path),
            daemon=True,
            name=f"focus-audio-job-{gen}",
        )
        t.start()
        return {"ok": True, "job": gen}

    # ----- live verbatim (experimental) -----

    def live_start(
        self,
        session_id: str,
        cwd: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Begin tailing updates.jsonl and speaking agent_message_chunk events."""
        if not self.cfg.enabled:
            return {"ok": True, "skipped": "disabled"}
        if not getattr(self.cfg, "live_verbatim", False):
            return {"ok": True, "skipped": "live_verbatim_off"}
        if not session_id:
            return {"ok": False, "error": "missing session_id"}

        # Bump job gen so a previous brief/speak job is cancelled; drop deferred brief.
        self._cancel_post_live()
        live_q = LiveSegmentQueue()
        with self._lock:
            self._job_gen += 1
            self._live_gen += 1
            gen = self._live_gen
            job_gen = self._job_gen
            # Hard-cancel any prior live queue so its consumer exits.
            old_q = self._live_queue
            self._live_queue = live_q
            self._live_active = True
            self._live_session_id = session_id
            self._live_cwd = cwd
            self._live_segments = 0
            self._live_spoken = 0
            self._live_word_count = 0
            self._status = "live"
            self._error = None
            self._last_session = {"session_id": session_id, "cwd": cwd}
        if old_q is not None:
            old_q.clear()

        t = threading.Thread(
            target=self._run_live,
            args=(gen, job_gen, session_id, cwd, live_q),
            daemon=True,
            name=f"focus-audio-live-{gen}",
        )
        t.start()
        return {"ok": True, "live": gen, "session_id": session_id}

    def live_finish(self, reason: str = "finish") -> Dict[str, Any]:
        """Soft-stop: close the producer side; consumer drains the queue fully."""
        with self._lock:
            segs = self._live_segments
            spoken = self._live_spoken
            sid = self._live_session_id
            active = self._live_active
            live_q = self._live_queue
        # Do not bump live_gen / stop the player — remaining queued messages
        # must still play to the end.
        if live_q is not None:
            live_q.close()
        return {
            "ok": True,
            "action": "live_finish",
            "reason": reason,
            "was_active": active,
            "segments": segs,
            "spoken": spoken,
            "session_id": sid,
        }

    def _cancel_live(self) -> None:
        """Hard-cancel: abort producer, drop queue, mark inactive (caller stops player)."""
        with self._lock:
            self._live_gen += 1
            self._live_active = False
            live_q = self._live_queue
            self._live_queue = None
        if live_q is not None:
            live_q.clear()

    def _cancel_post_live(self) -> None:
        with self._lock:
            self._post_live_token += 1

    def _schedule_after_live_brief(
        self, session_id: str, cwd: Optional[str]
    ) -> None:
        """After live finishes speaking, play cfg.mode post-turn audio."""
        with self._lock:
            self._post_live_token += 1
            token = self._post_live_token
        t = threading.Thread(
            target=self._run_after_live_brief,
            args=(token, session_id, cwd),
            daemon=True,
            name=f"focus-audio-after-live-{token}",
        )
        t.start()

    def _run_after_live_brief(
        self,
        token: int,
        session_id: str,
        cwd: Optional[str],
    ) -> None:
        deadline = time.time() + 180.0
        # Wait until the live watcher for this session has gone idle.
        while time.time() < deadline:
            with self._lock:
                if token != self._post_live_token:
                    return
                live_active = self._live_active
                live_sid = self._live_session_id
            if not live_active or live_sid != session_id:
                break
            time.sleep(0.2)
        # Let the last live segment finish playing.
        while self.player.is_playing() and time.time() < deadline:
            with self._lock:
                if token != self._post_live_token:
                    return
            time.sleep(0.12)
        with self._lock:
            if token != self._post_live_token:
                return
        # Beat of silence so brief doesn't crash into the last live word.
        time.sleep(0.35)
        with self._lock:
            if token != self._post_live_token:
                return
        try:
            # Always brief for the post-live recap — never re-read verbatim.
            self.enqueue_session(
                session_id, cwd, force=False, mode="brief", after_live=True
            )
        except Exception as e:
            print(f"focus-audio live_then_brief error: {e}", file=sys.stderr)

    def effective_label(self) -> str:
        """Human-readable 'what audio path am I on' for status / mode replies."""
        with self._lock:
            live_active = self._live_active
            status = self._status
            mode = (self.cfg.mode or "brief").lower()
            live_on = bool(getattr(self.cfg, "live_verbatim", False))
            live_then = bool(getattr(self.cfg, "live_then_brief", False))
            skip_stop = bool(getattr(self.cfg, "live_skip_stop_brief", True))
            enabled = bool(self.cfg.enabled)
        if not enabled:
            return "off"
        if live_active or status in ("live", "live_playing"):
            return "live_verbatim"
        if live_on:
            # Verbatim post-turn after live is redundant — treat as live-only.
            if live_then and mode != "verbatim":
                return f"live+{mode}"
            if skip_stop or mode == "verbatim":
                return "live_verbatim"
        return mode

    def _live_still(self, live_gen: int, job_gen: int) -> bool:
        with self._lock:
            return (
                live_gen == self._live_gen
                and job_gen == self._job_gen
                and not self._stop.is_set()
            )

    def _run_live(
        self,
        live_gen: int,
        job_gen: int,
        session_id: str,
        cwd: Optional[str],
        live_q: LiveSegmentQueue,
    ) -> None:
        """Producer enqueues segments; consumer plays each to completion in order.

        Decoupling discovery from playback means a second (or final) message that
        lands while the first is still speaking is queued, not used to interrupt
        the player. Hard cancel (skip / new turn) clears the queue via live_gen.
        """
        spoken = 0
        scripts: List[str] = []

        def still() -> bool:
            return self._live_still(live_gen, job_gen)

        def on_accepted(_seg: LiveSegment, accepted: int) -> None:
            # Count as soon as a message is queued so Stop treats the turn as
            # live-covered even before the first clip finishes playing.
            with self._lock:
                if live_gen == self._live_gen:
                    self._live_segments = accepted

        prod = threading.Thread(
            target=produce_live_segments,
            kwargs={
                "out": live_q,
                "session_id": session_id,
                "cfg": self.cfg,
                "cwd": cwd,
                "still_active": still,
                "on_accepted": on_accepted,
            },
            daemon=True,
            name=f"focus-audio-live-prod-{live_gen}",
        )
        prod.start()

        try:
            while still():
                try:
                    seg = live_q.get(timeout=0.15)
                except queue.Empty:
                    # Producer finished and closed with nothing left?
                    if live_q.closed and live_q.pending() == 0:
                        # get() re-queues END; try once more for a clean None.
                        try:
                            seg = live_q.get(timeout=0.05)
                        except queue.Empty:
                            break
                    else:
                        continue
                if seg is None:
                    break
                if not still():
                    break

                # First segment only: soft chime so you know live speech started.
                if spoken == 0 and self.cfg.chime:
                    self._play_chime()

                with self._lock:
                    self._status = "live_playing"
                    self._error = None

                try:
                    ready = resolve_script(
                        seg.cleaned,
                        self.cfg,
                        mode="verbatim",
                        force=False,
                        already_cleaned=True,
                        skip_llm=True,  # never block live path on rewrite model
                    )
                except Exception as e:
                    print(f"focus-audio live script error: {e}", file=sys.stderr)
                    continue

                if not still():
                    break

                # Play this segment fully before taking the next from the queue.
                # stream_synthesize_and_play blocks until audio ends (or cancel).
                prepared = stream_synthesize_and_play(
                    ready,
                    self.cfg,
                    self.player,
                    still_current=still,
                    autoplay=self.cfg.autoplay,
                )
                if not still():
                    break

                spoken += 1
                scripts.append(prepared.script)
                seg_words = len((prepared.script or "").split())
                with self._lock:
                    self._live_spoken = spoken
                    self._live_word_count = int(self._live_word_count or 0) + seg_words
                    # Keep accepted count as the public "segments" metric; also
                    # never drop below spoken.
                    self._live_segments = max(self._live_segments, spoken)
                    self._last_prepared = prepared
                    secure_write_text(
                        last_job_path(),
                        json.dumps(
                            {
                                "mode": "live_verbatim",
                                "from_cache": prepared.from_cache,
                                "segment": spoken,
                                "accepted": self._live_segments,
                                "live_words": self._live_word_count,
                                "audio": str(prepared.entry.audio_path),
                                "script": str(prepared.entry.script_path),
                                "streaming": False,
                            },
                            indent=2,
                        ),
                    )

            if scripts:
                try:
                    header = (
                        f"# Focus Audio last brief\n\nmode: `live_verbatim` · "
                        f"segments: `{len(scripts)}`\n\n---\n\n"
                    )
                    secure_write_text(
                        last_brief_path(),
                        header + "\n\n".join(scripts) + "\n",
                    )
                except OSError:
                    pass

            if still():
                with self._lock:
                    if live_gen == self._live_gen:
                        self._live_active = False
                        if self._live_queue is live_q:
                            self._live_queue = None
                        if self._status in ("live", "live_playing"):
                            self._status = "idle"
        except Exception as e:
            if self._live_still(live_gen, job_gen):
                with self._lock:
                    self._status = "error"
                    self._error = f"live: {e}"
                    self._live_active = False
                    if self._live_queue is live_q:
                        self._live_queue = None
                traceback.print_exc(file=sys.stderr)
        finally:
            live_q.close()
            prod.join(timeout=2.0)

    def enqueue_text(
        self,
        text: str,
        *,
        force: bool = False,
        mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.cfg.enabled:
            return {"ok": True, "skipped": "disabled"}
        self._cancel_post_live()
        self._cancel_live()
        with self._lock:
            self._job_gen += 1
            gen = self._job_gen
            self._status = "queued"
            self._error = None
            self._last_source = text
        t = threading.Thread(
            target=self._run_text_job,
            args=(gen, text, force, mode),
            daemon=True,
            name=f"focus-audio-job-{gen}",
        )
        t.start()
        return {"ok": True, "job": gen}

    def _still_current(self, gen: int) -> bool:
        with self._lock:
            return gen == self._job_gen

    def _run_session_job(
        self,
        gen: int,
        session_id: str,
        cwd: Optional[str],
        force: bool,
        mode: Optional[str],
        after_live: bool = False,
        transcript_path: Optional[str] = None,
    ) -> None:
        try:
            if not self._still_current(gen):
                return
            with self._lock:
                self._status = "synthesizing"
                self._active_gen = gen
                live_spoken = int(self._live_spoken or 0)
                live_words = int(self._live_word_count or 0)
            # After-live recap is always a brief, never a second full read.
            job_mode = "brief" if after_live else mode
            # Prefer updates.jsonl (streamed mid-turn). History can lag Stop;
            # short retries cover that fallback only.
            ready = None
            last_err: Optional[str] = None
            for attempt in range(4):
                if not self._still_current(gen):
                    return
                try:
                    ready = resolve_from_session(
                        session_id,
                        self.cfg,
                        cwd=cwd,
                        mode=job_mode,
                        force=force,
                        transcript_path=transcript_path,
                    )
                except Exception as e:
                    last_err = str(e)
                    ready = None
                if ready is not None:
                    break
                # Backoff: 0.1s, 0.2s, 0.3s (was longer while waiting on history only).
                time.sleep(0.1 * (attempt + 1))
            if not self._still_current(gen):
                return
            if ready is None:
                with self._lock:
                    self._status = "idle"
                    self._error = last_err or "no suitable assistant turn (empty/short)"
                print(
                    f"focus-audio: no turn for session={session_id} cwd={cwd} err={last_err}",
                    file=sys.stderr,
                )
                return
            # After-live second pass: never double-speak what live already covered.
            # Skip when:
            #  - caller asked for verbatim (should not happen; we force brief)
            #  - brief path skipped LLM rewrite (script ≈ cleaned source live said)
            #  - live already spoke most of the cleaned source (coverage)
            use_mode = (job_mode or self.cfg.mode or "brief").lower()
            if after_live and live_spoken > 0 and not force:
                cleaned = getattr(ready, "cleaned", "") or ""
                cleaned_words = len(cleaned.split()) if cleaned.strip() else 0
                coverage = (
                    (live_words / cleaned_words) if cleaned_words > 0 else 1.0
                )
                brief_skipped = bool(getattr(ready, "brief_skipped", False))
                if (
                    use_mode == "verbatim"
                    or brief_skipped
                    or coverage >= 0.55
                ):
                    with self._lock:
                        self._status = "idle"
                        self._error = None
                    return
            if self.cfg.chime:
                self._play_chime()
            self._stream_play(gen, ready)
        except Exception as e:
            if self._still_current(gen):
                with self._lock:
                    self._status = "error"
                    self._error = str(e)
                traceback.print_exc(file=sys.stderr)

    def _run_text_job(
        self,
        gen: int,
        text: str,
        force: bool,
        mode: Optional[str],
    ) -> None:
        try:
            if not self._still_current(gen):
                return
            with self._lock:
                self._status = "synthesizing"
                self._active_gen = gen
            if self.cfg.chime:
                self._play_chime()
            ready = resolve_script(text, self.cfg, mode=mode, force=force)
            if not self._still_current(gen):
                return
            self._stream_play(gen, ready)
        except Exception as e:
            if self._still_current(gen):
                with self._lock:
                    self._status = "error"
                    self._error = str(e)
                traceback.print_exc(file=sys.stderr)

    def _stream_play(self, gen: int, ready) -> None:
        """Script ready → stream TTS chunks into the player; write job metadata."""
        with self._lock:
            if gen != self._job_gen:
                return
            self._status = "playing" if self.cfg.autoplay else "ready"
            self._error = None
            # Surface script path immediately (audio may still be streaming in).
            self._last_prepared = PreparedAudio(
                entry=ready.entry,
                script=ready.script,
                from_cache=ready.from_cache,
                mode=ready.mode,
                brief_skipped=ready.brief_skipped,
                brief_fallback=ready.brief_fallback,
            )
            secure_write_text(
                last_job_path(),
                json.dumps(
                    {
                        "mode": ready.mode,
                        "from_cache": ready.from_cache,
                        "brief_skipped": ready.brief_skipped,
                        "audio": str(ready.entry.audio_path),
                        "script": str(ready.entry.script_path),
                        "streaming": not ready.from_cache,
                    },
                    indent=2,
                ),
            )

        def still() -> bool:
            return self._still_current(gen)

        # stream_synthesize_and_play starts audio on first chunk, then blocks until done.
        prepared = stream_synthesize_and_play(
            ready,
            self.cfg,
            self.player,
            still_current=still,
            autoplay=self.cfg.autoplay,
        )
        if not still():
            return

        with self._lock:
            if gen != self._job_gen:
                return
            self._last_prepared = prepared
            secure_write_text(
                last_job_path(),
                json.dumps(
                    {
                        "mode": prepared.mode,
                        "from_cache": prepared.from_cache,
                        "brief_skipped": prepared.brief_skipped,
                        "audio": str(prepared.entry.audio_path),
                        "script": str(prepared.entry.script_path),
                        "streaming": False,
                    },
                    indent=2,
                ),
            )
            if self._status == "playing":
                self._status = "idle"

    def _play_chime(self) -> None:
        """Soft system sound so you know synthesis started."""
        # Glass is mild; ignore failures
        glass = Path("/System/Library/Sounds/Tink.aiff")
        if glass.is_file() and self.player._afplay:
            try:
                import subprocess

                subprocess.Popen(
                    [self.player._afplay, str(glass)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                pass

    def _announce_mode(self, mode: str) -> None:
        """Speak a short 'Brief mode' / 'Verbatim mode' cue (cached TTS, say fallback)."""
        phrase = "Brief mode." if mode == "brief" else "Verbatim mode."
        provider = self.cfg.effective_tts_provider()
        voice = self.cfg.effective_voice_id()
        ext = self.cfg.audio_suffix()
        cache = (
            data_dir()
            / "announce"
            / f"{provider}_{voice}_{self.cfg.speed}_{mode}{ext}"
        )
        played = False
        if not cache.is_file():
            try:
                from .tts import synthesize_speech

                synthesize_speech(phrase, cache, self.cfg)
            except Exception as e:
                print(f"focus-audio mode TTS announce failed: {e}", file=sys.stderr)
        if cache.is_file():
            try:
                self.player.play(cache)
                played = True
            except Exception as e:
                print(f"focus-audio mode announce play failed: {e}", file=sys.stderr)
        if not played:
            try:
                import subprocess

                say = Path("/usr/bin/say")
                cmd = [str(say) if say.is_file() else "say", phrase]
                subprocess.run(cmd, check=False, timeout=5)
            except Exception as e:
                print(f"focus-audio mode say fallback failed: {e}", file=sys.stderr)
                return
        deadline = time.time() + 4.0
        while self.player.is_playing() and time.time() < deadline:
            time.sleep(0.05)

    def _set_mode(self, requested: Optional[str] = None) -> Dict[str, Any]:
        """Toggle or set mode, announce, and re-speak last turn in the new mode."""
        now = time.time()
        if now - self._last_mode_toggle_at < 0.55:
            return {
                "ok": True,
                "mode": self.cfg.mode,
                "debounced": True,
                "effective": self.effective_label(),
            }
        self._last_mode_toggle_at = now

        previous = self.cfg.mode
        if requested in ("brief", "verbatim"):
            self.cfg.mode = requested
        else:
            self.cfg.toggle_mode()
        save_config(self.cfg)
        mode = self.cfg.mode

        # Cancel in-flight work so announce + rebrief own the player.
        self._cancel_post_live()
        with self._lock:
            self._job_gen += 1
        self._cancel_live()
        self.player.stop()

        with self._lock:
            has_session = bool(
                self._last_session and self._last_session.get("session_id")
            )
            has_source = bool(self._last_source)
        will_rebrief = has_session or has_source

        t = threading.Thread(
            target=self._mode_switch_job,
            args=(mode,),
            daemon=True,
            name=f"focus-audio-mode-{mode}",
        )
        t.start()
        return {
            "ok": True,
            "mode": mode,
            "previous": previous,
            "rebrief": will_rebrief,
            "effective": self.effective_label(),
        }

    def _mode_switch_job(self, mode: str) -> None:
        """Announce new mode, then replay last turn — cache-friendly (force=False).

        Explicit /audio-rebrief still uses force=True to regenerate. Mode switch
        only needs the other mode's clip; if it already exists, lookup hits and
        we skip rewrite + TTS.
        """
        try:
            self._announce_mode(mode)
        except Exception as e:
            print(f"focus-audio mode announce error: {e}", file=sys.stderr)
        try:
            result = self._replay_last_for_mode()
            if not result.get("ok") and result.get("error") == "nothing to rebrief":
                with self._lock:
                    self._status = "idle"
        except Exception as e:
            print(f"focus-audio mode rebrief error: {e}", file=sys.stderr)
            with self._lock:
                self._status = "error"
                self._error = f"mode rebrief: {e}"

    def _replay_last_for_mode(self) -> Dict[str, Any]:
        """Re-speak last turn in current cfg.mode, preferring cache (no force).

        Uses after_live=True so a prior live-covered turn does not skip/defer;
        mode toggle always wants the post-turn clip for the selected mode.
        """
        with self._lock:
            sess = self._last_session
            src = self._last_source
        if sess and sess.get("session_id"):
            return self.enqueue_session(
                sess["session_id"],
                sess.get("cwd"),
                force=False,
                after_live=True,
            )
        if src:
            return self.enqueue_text(src, force=False)
        return {"ok": False, "error": "nothing to rebrief"}

    def _last_audio_path(self) -> Optional[Path]:
        """Canonical path of the last prepared clip, if the file still exists."""
        with self._lock:
            prep = self._last_prepared
        if prep is None:
            return None
        path = Path(prep.entry.audio_path)
        return path if path.is_file() else None

    def _salvage_stream_chunks(self) -> Optional[Path]:
        """If skip cancelled mid-stream, stitch leftover ``.cN.mp3`` parts into the cache path.

        The job thread also tries this, but skip returns immediately — do it
        synchronously so toggle/restart never see a missing current file.
        """
        with self._lock:
            prep = self._last_prepared
        if prep is None:
            return None
        full = Path(prep.entry.audio_path)
        if full.is_file():
            return full
        key = prep.entry.key
        parent = full.parent
        parts = sorted(parent.glob(f"{key}.c*.mp3"))
        parts = [p for p in parts if p.is_file()]
        if not parts:
            return None
        try:
            if len(parts) == 1:
                sole = parts[0]
                if sole.resolve() != full.resolve():
                    full.write_bytes(sole.read_bytes())
            else:
                from .tts import concat_mp3

                concat_mp3(parts, full)
            return full if full.is_file() else None
        except Exception as e:
            print(f"focus-audio salvage chunks failed: {e}", file=sys.stderr)
            # Last resort: keep the newest playable part
            for p in reversed(parts):
                if p.is_file():
                    return p
            return None

    def _ensure_playable_current(self) -> None:
        """If player.current is missing (deleted stream chunk), re-point at last clip."""
        cur = self.player.current
        if cur is not None and Path(cur).is_file():
            return
        last = self._last_audio_path() or self._salvage_stream_chunks()
        if last is not None:
            self.player.set_current(last)

    def _play_last_clip(self) -> bool:
        """Play last prepared audio from the start. Used after skip leaves idle."""
        last = self._last_audio_path() or self._salvage_stream_chunks()
        if last is None:
            return False
        try:
            self.player.play(last)
            return True
        except Exception as e:
            print(f"focus-audio play last clip failed: {e}", file=sys.stderr)
            return False

    # ----- controls -----

    def handle(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        cmd = (msg.get("cmd") or "").lower()
        if cmd == "ping":
            return {"ok": True, "pid": os.getpid()}
        if cmd == "status":
            return self.status()
        if cmd == "enqueue":
            return self.enqueue_session(
                msg.get("session_id") or "",
                msg.get("cwd"),
                force=bool(msg.get("force")),
                mode=msg.get("mode"),
                transcript_path=msg.get("transcript_path")
                or msg.get("transcriptPath"),
            )
        if cmd == "live_start":
            return self.live_start(
                msg.get("session_id") or "",
                msg.get("cwd"),
            )
        if cmd == "live_finish" or cmd == "live_stop":
            self._cancel_post_live()
            self._cancel_live()
            self.player.stop()
            with self._lock:
                self._status = "idle"
            return {"ok": True, "status": "idle", "action": "live_stop"}
        if cmd == "speak":
            text = msg.get("text") or ""
            if not text.strip():
                return {"ok": False, "error": "empty text"}
            return self.enqueue_text(
                text, force=bool(msg.get("force")), mode=msg.get("mode")
            )
        if cmd == "pause":
            self.player.pause()
            with self._lock:
                self._status = "paused"
            return {"ok": True, "status": "paused"}
        if cmd == "resume":
            self._ensure_playable_current()
            ok = self.player.resume()
            with self._lock:
                self._status = "playing" if ok else self._status
            return {"ok": ok, "status": "playing" if ok else "idle"}
        if cmd == "toggle":
            self._ensure_playable_current()
            state = self.player.toggle()
            # After skip mid-stream, toggle used to return idle (chunk deleted).
            # Fall back to last full clip so play/pause keeps working.
            if state == "idle" and self._play_last_clip():
                state = "playing"
            with self._lock:
                self._status = state if state != "idle" else self._status
            return {"ok": True, "status": state}
        if cmd == "restart":
            self._ensure_playable_current()
            ok = self.player.restart()
            if not ok:
                ok = self._play_last_clip()
            with self._lock:
                if ok:
                    self._status = "playing"
            return {"ok": ok}
        if cmd == "skip" or cmd == "stop":
            with self._lock:
                self._job_gen += 1  # cancel in-flight synthesis
            self._cancel_post_live()
            self._cancel_live()
            self.player.stop()
            # Re-point player at last salvaged/full clip (chunks may be gone)
            self._ensure_playable_current()
            with self._lock:
                self._status = "idle"
            return {"ok": True, "status": "idle"}
        if cmd == "rebrief":
            return self._rebrief(force=True)
        if cmd == "mode":
            return self._set_mode(msg.get("mode"))
        if cmd == "reload_config":
            self.cfg = load_config()
            return {"ok": True, "config": self.status()["config"]}
        if cmd == "shutdown":
            self._cancel_post_live()
            self._cancel_live()
            threading.Thread(target=self._shutdown_soon, daemon=True).start()
            return {"ok": True, "status": "shutting_down"}
        return {"ok": False, "error": f"unknown cmd: {cmd}"}

    def _rebrief(self, force: bool = True) -> Dict[str, Any]:
        with self._lock:
            sess = self._last_session
            src = self._last_source
        if sess and sess.get("session_id"):
            return self.enqueue_session(
                sess["session_id"], sess.get("cwd"), force=force
            )
        if src:
            return self.enqueue_text(src, force=force)
        return {"ok": False, "error": "nothing to rebrief"}

    def status(self) -> Dict[str, Any]:
        with self._lock:
            prep = self._last_prepared
            live_active = self._live_active
            live_sid = self._live_session_id
            live_segs = self._live_segments
            live_spoken = self._live_spoken
            live_q = self._live_queue
            status = self._status
            err = self._error
            sess = self._last_session
            mode = self.cfg.mode
        pending = live_q.pending() if live_q is not None else 0
        return {
            "ok": True,
            "status": status,
            "error": err,
            "playing": self.player.is_playing() and not self.player.is_paused(),
            "paused": self.player.is_paused(),
            "mode": mode,
            "effective": self.effective_label(),
            "live": {
                "active": live_active,
                "session_id": live_sid,
                "segments": live_segs,
                "spoken": live_spoken,
                "pending": pending,
                "enabled": bool(getattr(self.cfg, "live_verbatim", False)),
                "then_brief": bool(getattr(self.cfg, "live_then_brief", False)),
                "skip_stop_brief": bool(
                    getattr(self.cfg, "live_skip_stop_brief", True)
                ),
            },
            "config": {
                "tts_provider": self.cfg.normalize_tts_provider(),
                "tts_provider_effective": self.cfg.effective_tts_provider(),
                "voice_id": self.cfg.voice_id,
                "macos_voice": getattr(self.cfg, "macos_voice", "") or "",
                "effective_voice_id": self.cfg.effective_voice_id(),
                "speed": self.cfg.speed,
                "autoplay": self.cfg.autoplay,
                "enabled": self.cfg.enabled,
                "model": self.cfg.model,
                "live_verbatim": bool(getattr(self.cfg, "live_verbatim", False)),
                "live_then_brief": bool(getattr(self.cfg, "live_then_brief", False)),
                "live_skip_stop_brief": bool(
                    getattr(self.cfg, "live_skip_stop_brief", True)
                ),
                "api_key_source": self.cfg.api_key_source(),
                "api_key_present": bool(self.cfg.api_key()),
            },
            "last": (
                {
                    "mode": prep.mode,
                    "from_cache": prep.from_cache,
                    "brief_skipped": getattr(prep, "brief_skipped", False),
                    "audio": str(prep.entry.audio_path),
                    "script": str(prep.entry.script_path),
                }
                if prep
                else None
            ),
            "session": sess,
        }

    def _shutdown_soon(self) -> None:
        time.sleep(0.15)
        self._stop.set()
        self.player.stop()
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass

    # ----- server loop -----

    def run(self) -> None:
        # Exclusive lock so only one daemon (and one hotkey listener) exists.
        lock_fd = _acquire_daemon_lock()
        if lock_fd is None:
            print("Focus Audio daemon already running (lock held)", file=sys.stderr)
            sys.exit(1)

        sock_path = socket_path()
        if sock_path.exists():
            if is_daemon_alive():
                print("Focus Audio daemon already running", file=sys.stderr)
                sys.exit(1)
            try:
                sock_path.unlink()
            except OSError:
                pass

        # Kill stale sibling processes that still own hotkeys but lost the socket.
        _kill_orphan_daemon_processes(except_pid=os.getpid())

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(sock_path))
        # Owner-only socket — other local users must not command the daemon.
        harden_socket(sock_path)
        server.listen(8)
        server.settimeout(0.5)
        self._server = server
        # Keep lock_fd referenced so flock is held for process lifetime.
        self._lock_fd = lock_fd  # type: ignore[attr-defined]
        secure_write_text(pid_path(), str(os.getpid()))
        print(f"Focus Audio daemon listening on {sock_path}", flush=True)

        def _sig(_signum, _frame):
            self._stop.set()

        signal.signal(signal.SIGTERM, _sig)
        signal.signal(signal.SIGINT, _sig)

        if self.cfg.hotkeys:
            self._start_hotkeys()

        try:
            while not self._stop.is_set():
                try:
                    conn, _ = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                threading.Thread(
                    target=self._serve_conn, args=(conn,), daemon=True
                ).start()
        finally:
            try:
                server.close()
            except OSError:
                pass
            if sock_path.exists():
                try:
                    sock_path.unlink()
                except OSError:
                    pass
            if pid_path().exists():
                try:
                    pid_path().unlink()
                except OSError:
                    pass
            print("Focus Audio daemon stopped", flush=True)

    def _serve_conn(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(10)
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
            if not buf:
                return
            msg = json.loads(buf.decode("utf-8").strip())
            resp = self.handle(msg)
            conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
        except Exception as e:
            try:
                conn.sendall(
                    (json.dumps({"ok": False, "error": str(e)}) + "\n").encode()
                )
            except OSError:
                pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _start_hotkeys(self) -> None:
        try:
            from .hotkeys import start_hotkey_listener
        except Exception as e:
            print(f"Hotkeys unavailable: {e}", file=sys.stderr)
            return

        def on_action(action: str) -> None:
            mapping = {
                "toggle": {"cmd": "toggle"},
                "restart": {"cmd": "restart"},
                "skip": {"cmd": "skip"},
                "rebrief": {"cmd": "rebrief"},
                "mode": {"cmd": "mode"},
            }
            msg = mapping.get(action)
            if msg:
                self.handle(msg)

        try:
            start_hotkey_listener(on_action)
            print(
                "Hotkeys: Ctrl+Shift+Space play/pause · R restart · . skip · B rebrief · M mode",
                flush=True,
            )
        except Exception as e:
            print(f"Hotkeys failed to start: {e}", file=sys.stderr)


def _acquire_daemon_lock():
    """Non-blocking exclusive lock; return open fd or None if another daemon holds it."""
    import fcntl

    data_dir().mkdir(parents=True, exist_ok=True)
    lock_path = data_dir() / "daemon.lock"
    fd = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fd.close()
        return None
    fd.seek(0)
    fd.truncate()
    fd.write(str(os.getpid()))
    fd.flush()
    return fd


def _kill_orphan_daemon_processes(*, except_pid: int) -> None:
    """Terminate other focus-audio daemon processes (stale multi-daemon bug)."""
    import subprocess

    try:
        out = subprocess.check_output(
            ["ps", "ax", "-o", "pid=,command="], text=True
        )
    except (OSError, subprocess.SubprocessError):
        return
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid == except_pid:
            continue
        cmd = parts[1]
        # Strict: real Python daemon entrypoints only (not shells that mention them).
        is_module = "focus_audio.cli" in cmd and cmd.rstrip().endswith("daemon")
        is_bin = "focus-audio" in cmd and cmd.rstrip().endswith("daemon") and "bin/" in cmd
        if not (is_module or is_bin):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"focus-audio: stopped orphan daemon pid={pid}", flush=True)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass


def run_daemon() -> None:
    data_dir().mkdir(parents=True, exist_ok=True)
    FocusAudioDaemon().run()
