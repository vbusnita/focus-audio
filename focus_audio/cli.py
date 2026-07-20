"""CLI entry for Focus Audio."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import __version__
from .config import ensure_default_config, load_config, save_config
from .ipc import is_daemon_alive, send_command, try_send
from .lifecycle import acquire_session, active_count, clear_all, list_sessions, release_session
from .paths import (
    config_path,
    data_dir,
    harden_runtime_tree,
    last_brief_path,
    purge_runtime,
    secure_open_append,
    socket_path,
)
from .pipeline import prepare_audio, prepare_from_session
from .player import Player
from .transcript import load_turn


def _public_config_dict(cfg) -> dict:
    """Settings for ``config --show`` — never the raw API key.

    Config only stores non-secret fields (including ``api_key_env`` as a name
    and ``hotkeys`` as a bool). Do not filter names containing the substring
    ``key``; that incorrectly omitted ``hotkeys``.
    """
    return {k: v for k, v in cfg.__dict__.items() if not str(k).startswith("_")}


def _plugin_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _focus_audio_disabled() -> bool:
    """Env kill-switch (FOCUS_AUDIO=0) — strongest, process-wide."""
    return os.environ.get("FOCUS_AUDIO", "").strip().lower() in ("0", "false", "off", "no")


def _config_disabled() -> bool:
    """Persistent config.toml enabled=false (slash /audio-off)."""
    try:
        return not bool(load_config().enabled)
    except Exception:
        return False


def _parse_on_off(value: Optional[str], *, current: bool) -> bool:
    """Map on|off|true|false|toggle|None → bool. None/toggle flips current."""
    if value is None or str(value).strip().lower() in ("", "toggle", "flip"):
        return not current
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "on", "enable", "enabled"):
        return True
    if s in ("0", "false", "no", "off", "disable", "disabled"):
        return False
    raise ValueError(f"expected on|off|toggle, got {value!r}")


def _hook_session_id(args_session_id: Optional[str] = None) -> str:
    """Resolve session id from CLI args, env, or stdin JSON (hook payload)."""
    session_id = (
        args_session_id
        or os.environ.get("GROK_SESSION_ID")
        or os.environ.get("CLAUDE_SESSION_ID")
        or ""
    )
    # stdin may already have been consumed by caller — optional second chance only
    # when still empty and stdin is a pipe with unread data is handled by callers.
    return session_id.strip()


def _read_hook_payload() -> dict:
    if sys.stdin.isatty():
        return {}
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _hook_log(msg: str) -> None:
    """Append a line to ~/.grok/focus-audio/hook.log (best-effort)."""
    try:
        path = data_dir() / "hook.log"
        with secure_open_append(path) as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def _active_session_fallback(cwd: Optional[str] = None) -> Optional[str]:
    """Last-resort: if hooks omit session id, pick from ~/.grok/active_sessions.json."""
    path = Path.home() / ".grok" / "active_sessions.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, list) or not raw:
        return None
    if cwd:
        for entry in raw:
            if isinstance(entry, dict) and entry.get("cwd") == cwd and entry.get("session_id"):
                return str(entry["session_id"])
    # Prefer most recently opened
    best = None
    best_ts = ""
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("session_id"):
            continue
        ts = str(entry.get("opened_at") or "")
        if ts >= best_ts:
            best_ts = ts
            best = str(entry["session_id"])
    return best


# Grok Stop reasons that should auto-speak (from open-source turn completion).
# Skip cancelled/error so we do not burn TTS on interrupted turns.
_SKIP_STOP_REASONS = frozenset(
    {
        "cancelled",
        "canceled",
        "error",
        "interrupted",
        "max_turns",
        "max_turns_reached",
        "channel_closed",
    }
)
_SPEAK_STOP_REASONS = frozenset(
    {
        "end_turn",
        "completed",
        "stop",
        "success",
    }
)


def _payload_transcript_path(payload: dict) -> str:
    return str(
        payload.get("transcriptPath")
        or payload.get("transcript_path")
        or ""
    ).strip()


def _payload_stop_reason(payload: dict) -> str:
    return str(payload.get("reason") or "").strip().lower()


def _should_auto_speak_stop(payload: dict, *, force: bool = False) -> bool:
    """Whether Stop should enqueue speech.

    Prefer ``end_turn`` only. Missing reason (manual CLI / older payloads) still
    speaks. Known cancel/error reasons never auto-speak unless force.
    """
    if force:
        return True
    reason = _payload_stop_reason(payload)
    if not reason:
        return True
    if reason in _SKIP_STOP_REASONS:
        return False
    if reason in _SPEAK_STOP_REASONS:
        return True
    # Unknown future reasons: speak (fail-open) rather than silently drop.
    return True


def _ensure_daemon() -> bool:
    if is_daemon_alive():
        return True
    # Avoid stampeding: only one spawner should race; lock is also taken by daemon.
    root = _plugin_root()
    bin_path = root / "bin" / "focus-audio"
    cmd: List[str]
    if bin_path.is_file():
        cmd = [str(bin_path), "daemon"]
    else:
        cmd = [sys.executable, "-m", "focus_audio.cli", "daemon"]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    log = data_dir() / "daemon.log"
    with secure_open_append(log) as fh:
        fh.write(f"\n--- spawn {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        fh.flush()
        subprocess.Popen(
            cmd,
            stdout=fh,
            stderr=fh,
            start_new_session=True,
            env=env,
            cwd=str(root),
        )
    for _ in range(50):
        time.sleep(0.1)
        if is_daemon_alive():
            return True
    return False


def cmd_daemon(_args: argparse.Namespace) -> int:
    from .daemon import run_daemon

    run_daemon()
    return 0


def cmd_ensure(args: argparse.Namespace) -> int:
    """SessionStart hook: register session + start daemon. Fail-open."""
    if _focus_audio_disabled():
        _hook_log("ensure skipped: FOCUS_AUDIO disabled")
        return 0
    payload = _read_hook_payload()
    session_id = (
        args.session_id
        or os.environ.get("GROK_SESSION_ID")
        or os.environ.get("CLAUDE_SESSION_ID")
        or payload.get("sessionId")
        or payload.get("session_id")
        or ""
    )
    if not session_id:
        cwd = (
            os.environ.get("GROK_WORKSPACE_ROOT")
            or os.environ.get("CLAUDE_PROJECT_DIR")
            or payload.get("workspaceRoot")
            or payload.get("cwd")
            or ""
        )
        session_id = _active_session_fallback(cwd or None) or ""
    try:
        ref = acquire_session(session_id or None)
        ok = _ensure_daemon()
        out = {
            "ok": ok,
            "action": "ensure",
            "daemon": "running" if ok else "failed_to_start",
            "refs": ref,
        }
        _hook_log(f"ensure session={session_id or ref.get('session_id')} ok={ok} refs={ref.get('count')}")
        if args.verbose:
            print(json.dumps(out))
        elif not ok:
            print("focus-audio: could not start daemon", file=sys.stderr)
    except Exception as e:
        print(f"focus-audio ensure error: {e}", file=sys.stderr)
        _hook_log(f"ensure error: {e}")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    """SessionEnd hook: drop ref; shut down daemon if last session. Fail-open."""
    if _focus_audio_disabled():
        return 0
    payload = _read_hook_payload()
    session_id = (
        args.session_id
        or os.environ.get("GROK_SESSION_ID")
        or os.environ.get("CLAUDE_SESSION_ID")
        or payload.get("sessionId")
        or payload.get("session_id")
        or ""
    )
    try:
        ref = release_session(session_id or None)
        shut = False
        if ref.get("count", 0) == 0:
            # Last Grok session left — stop daemon
            if is_daemon_alive():
                try_send({"cmd": "shutdown"}, timeout=2.0)
                # Wait briefly for clean exit
                for _ in range(20):
                    if not is_daemon_alive():
                        break
                    time.sleep(0.1)
            clear_all()
            shut = True
        out = {
            "ok": True,
            "action": "release",
            "shutdown": shut,
            "refs": ref,
        }
        if args.verbose:
            print(json.dumps(out))
    except Exception as e:
        print(f"focus-audio release error: {e}", file=sys.stderr)
    return 0


def _resolve_hook_session(args: argparse.Namespace, payload: dict) -> tuple:
    """Return (session_id, cwd, transcript_path) — envelope-first.

    Grok's hook runner always injects ``GROK_SESSION_ID`` and
    ``GROK_WORKSPACE_ROOT`` for real hooks, and the stdin envelope carries
    ``sessionId``, ``cwd``/``workspaceRoot``, and often ``transcriptPath``
    (session ``updates.jsonl``). Prefer those over disk scans.
    """
    payload = payload if isinstance(payload, dict) else {}
    transcript_path = _payload_transcript_path(payload)

    session_id = str(
        getattr(args, "session_id", None)
        or os.environ.get("GROK_SESSION_ID")
        or os.environ.get("CLAUDE_SESSION_ID")
        or payload.get("sessionId")
        or payload.get("session_id")
        or ""
    ).strip()
    cwd = str(
        getattr(args, "cwd", None)
        or os.environ.get("GROK_WORKSPACE_ROOT")
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or payload.get("workspaceRoot")
        or payload.get("cwd")
        or ""
    ).strip()

    # O(1) recovery from transcriptPath when session id was omitted.
    if (not session_id or not cwd) and transcript_path:
        try:
            from .transcript import session_dir_from_transcript_path

            sdir = session_dir_from_transcript_path(transcript_path)
            if sdir is not None:
                if not session_id and sdir.name:
                    session_id = sdir.name
                    _hook_log(f"session_id from transcriptPath: {session_id}")
                if not cwd and sdir.parent is not None:
                    # Parent dir name is URL-encoded cwd in Grok's layout.
                    from urllib.parse import unquote

                    decoded = unquote(sdir.parent.name)
                    if decoded.startswith("/"):
                        cwd = decoded
                        _hook_log(f"cwd from transcriptPath parent: {cwd}")
        except Exception as e:
            _hook_log(f"transcriptPath resolve error: {e}")

    if not cwd:
        cwd = os.getcwd()

    # Last-resort only — real Grok hooks should not need these.
    if not session_id:
        session_id = _active_session_fallback(cwd) or ""
        if session_id:
            _hook_log(f"session_id from active_sessions.json: {session_id}")

    if not session_id:
        try:
            from .transcript import encode_cwd, sessions_root

            group = sessions_root() / encode_cwd(str(cwd))
            if group.is_dir():
                newest = None
                newest_m = 0.0
                for child in group.iterdir():
                    # Prefer updates.jsonl (always present mid-turn) over history.
                    marker = child / "updates.jsonl"
                    if not marker.is_file():
                        marker = child / "chat_history.jsonl"
                    if child.is_dir() and marker.is_file():
                        m = marker.stat().st_mtime
                        if m > newest_m:
                            newest_m = m
                            newest = child.name
                if newest:
                    session_id = newest
                    _hook_log(f"session_id from newest transcript: {session_id}")
        except Exception as e:
            _hook_log(f"newest-session fallback error: {e}")

    return str(session_id or ""), str(cwd or ""), str(transcript_path or "")


def cmd_enqueue(args: argparse.Namespace) -> int:
    """Called by Grok Stop hook — always fail-open (exit 0)."""
    if _focus_audio_disabled():
        _hook_log("enqueue skipped: FOCUS_AUDIO disabled")
        return 0
    if _config_disabled():
        _hook_log("enqueue skipped: enabled=false")
        return 0

    payload = _read_hook_payload()
    force = bool(getattr(args, "force", False))
    if not _should_auto_speak_stop(payload, force=force):
        reason = _payload_stop_reason(payload)
        _hook_log(f"enqueue skipped: stop reason={reason!r} (not end_turn)")
        return 0

    session_id, cwd, transcript_path = _resolve_hook_session(args, payload)

    if not session_id:
        _hook_log(
            "enqueue skipped: no session_id "
            f"(env={bool(os.environ.get('GROK_SESSION_ID'))} "
            f"payload_keys={list(payload.keys())} cwd={cwd} "
            f"transcriptPath={bool(transcript_path)})"
        )
        return 0

    # Safety: if SessionStart was missed, still bring daemon up
    try:
        acquire_session(session_id)
    except Exception as e:
        _hook_log(f"enqueue acquire_session error: {e}")

    if not _ensure_daemon():
        print("focus-audio: could not start daemon", file=sys.stderr)
        _hook_log("enqueue failed: could not start daemon")
        return 0

    try:
        msg = {
            "cmd": "enqueue",
            "session_id": session_id,
            "cwd": cwd,
            "force": force,
            "mode": args.mode,
        }
        if transcript_path:
            msg["transcript_path"] = transcript_path
        resp = send_command(msg, timeout=3.0)
        _hook_log(
            f"enqueue ok session={session_id} reason={_payload_stop_reason(payload)!r} "
            f"transcript={bool(transcript_path)} resp={resp}"
        )
        if args.verbose:
            print(json.dumps(resp))
    except Exception as e:
        print(f"focus-audio enqueue error: {e}", file=sys.stderr)
        _hook_log(f"enqueue error: {e}")
    return 0


def cmd_live_start(args: argparse.Namespace) -> int:
    """UserPromptSubmit hook: start mid-turn live verbatim tail. Fail-open."""
    if _focus_audio_disabled():
        _hook_log("live-start skipped: FOCUS_AUDIO disabled")
        return 0

    cfg = load_config()
    if not cfg.enabled:
        _hook_log("live-start skipped: enabled=false")
        return 0
    if not getattr(cfg, "live_verbatim", False):
        _hook_log("live-start skipped: live_verbatim=false")
        return 0

    payload = _read_hook_payload()
    session_id, cwd, transcript_path = _resolve_hook_session(args, payload)
    if not session_id:
        _hook_log(
            "live-start skipped: no session_id "
            f"payload_keys={list(payload.keys())} cwd={cwd}"
        )
        return 0

    try:
        acquire_session(session_id)
    except Exception as e:
        _hook_log(f"live-start acquire_session error: {e}")

    if not _ensure_daemon():
        _hook_log("live-start failed: could not start daemon")
        return 0

    try:
        msg = {
            "cmd": "live_start",
            "session_id": session_id,
            "cwd": cwd,
        }
        if transcript_path:
            msg["transcript_path"] = transcript_path
        resp = send_command(msg, timeout=3.0)
        _hook_log(f"live-start ok session={session_id} resp={resp}")
        if getattr(args, "verbose", False):
            print(json.dumps(resp))
    except Exception as e:
        print(f"focus-audio live-start error: {e}", file=sys.stderr)
        _hook_log(f"live-start error: {e}")
    return 0


def cmd_speak(args: argparse.Namespace) -> int:
    text = ""
    if args.text:
        text = args.text
    elif args.path and args.path != "-":
        text = Path(args.path).read_text(encoding="utf-8")
    else:
        if not sys.stdin.isatty():
            text = sys.stdin.read()
        else:
            print("Provide text via argument, file path, or stdin", file=sys.stderr)
            return 2

    if not text.strip():
        print("Empty input", file=sys.stderr)
        return 2

    cfg = load_config()
    if args.mode:
        cfg.mode = args.mode

    if args.no_daemon or not _ensure_daemon():
        # Inline path
        prepared = prepare_audio(text, cfg, force=args.force)
        print(prepared.script)
        print(f"\n# audio: {prepared.entry.audio_path}", file=sys.stderr)
        if not args.no_play:
            Player().play(prepared.entry.audio_path, block=True)
        return 0

    resp = send_command(
        {
            "cmd": "speak",
            "text": text,
            "force": bool(args.force),
            "mode": args.mode or cfg.mode,
        }
    )
    print(json.dumps(resp, indent=2))
    return 0 if resp.get("ok") else 1


def cmd_speak_session(args: argparse.Namespace) -> int:
    session_id = args.session_id
    cwd = args.cwd or os.getcwd()
    cfg = load_config()
    if args.mode:
        cfg.mode = args.mode

    if args.no_daemon:
        prepared = prepare_from_session(
            session_id, cfg, cwd=cwd, force=args.force, mode=args.mode
        )
        if not prepared:
            print("No suitable assistant turn found", file=sys.stderr)
            return 1
        print(prepared.script)
        if not args.no_play:
            Player().play(prepared.entry.audio_path, block=True)
        return 0

    if not _ensure_daemon():
        print("Could not start daemon", file=sys.stderr)
        return 1
    resp = send_command(
        {
            "cmd": "enqueue",
            "session_id": session_id,
            "cwd": cwd,
            "force": bool(args.force),
            "mode": args.mode,
        }
    )
    print(json.dumps(resp, indent=2))
    return 0 if resp.get("ok") else 1


def _control(cmd: str, extra: Optional[dict] = None) -> int:
    payload = {"cmd": cmd}
    if extra:
        payload.update(extra)
    resp = try_send(payload)
    if resp is None:
        print("Daemon not running. Start with: focus-audio daemon", file=sys.stderr)
        return 1
    print(json.dumps(resp, indent=2))
    return 0 if resp.get("ok") else 1


def cmd_status(_args: argparse.Namespace) -> int:
    daemon = try_send({"cmd": "status"}, timeout=1.0)
    out = {
        "daemon": daemon if daemon is not None else {"ok": False, "running": False},
        "lifecycle": {
            "active_sessions": active_count(),
            "session_ids": list_sessions(),
        },
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_pause(_args: argparse.Namespace) -> int:
    return _control("pause")


def cmd_resume(_args: argparse.Namespace) -> int:
    return _control("resume")


def cmd_toggle(_args: argparse.Namespace) -> int:
    return _control("toggle")


def cmd_restart(_args: argparse.Namespace) -> int:
    return _control("restart")


def cmd_skip(_args: argparse.Namespace) -> int:
    return _control("skip")


def cmd_rebrief(_args: argparse.Namespace) -> int:
    return _control("rebrief")


def cmd_mode(args: argparse.Namespace) -> int:
    extra = {}
    if args.mode:
        extra["mode"] = args.mode
    return _control("mode", extra)


def _apply_config_and_reload(cfg) -> Optional[dict]:
    save_config(cfg)
    return try_send({"cmd": "reload_config"})


def cmd_live(args: argparse.Namespace) -> int:
    """Toggle or set live_verbatim (mid-turn speech)."""
    cfg = ensure_default_config()
    prev = bool(cfg.live_verbatim)
    try:
        new = _parse_on_off(getattr(args, "state", None), current=prev)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    cfg.live_verbatim = new
    reload_resp = _apply_config_and_reload(cfg)
    # Turning live off: cancel any in-flight mid-turn speech.
    stopped = False
    if not new and is_daemon_alive():
        try_send({"cmd": "skip"}, timeout=1.0)
        stopped = True
    out = {
        "ok": True,
        "live_verbatim": new,
        "previous": prev,
        "changed": new != prev,
        "stopped_playback": stopped,
        "enabled": bool(cfg.enabled),
        "mode": cfg.mode,
        "effective": (
            "off"
            if not cfg.enabled
            else (
                "live+brief"
                if new and cfg.live_then_brief and cfg.mode != "verbatim"
                else ("live_verbatim" if new else cfg.mode)
            )
        ),
        "reload": reload_resp,
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_power(args: argparse.Namespace) -> int:
    """Master switch: enabled on/off — silences brief, verbatim, and live."""
    cfg = ensure_default_config()
    prev = bool(cfg.enabled)
    try:
        new = _parse_on_off(getattr(args, "state", None), current=prev)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    cfg.enabled = new
    reload_resp = _apply_config_and_reload(cfg)
    stopped = False
    if not new and is_daemon_alive():
        # Hard stop: cancel live, deferred brief, and current clip.
        try_send({"cmd": "skip"}, timeout=1.0)
        stopped = True
    out = {
        "ok": True,
        "enabled": new,
        "previous": prev,
        "changed": new != prev,
        "stopped_playback": stopped,
        "live_verbatim": bool(cfg.live_verbatim),
        "mode": cfg.mode,
        "note": (
            "Focus Audio OFF — no live, brief, or verbatim until power on"
            if not new
            else "Focus Audio ON — hooks and autoplay resume"
        ),
        "reload": reload_resp,
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    cfg = ensure_default_config()
    if args.show or not any(
        [
            args.voice,
            args.speed is not None,
            args.mode,
            args.autoplay is not None,
            args.model,
            args.live is not None,
            getattr(args, "live_then_brief", None) is not None,
            getattr(args, "enabled", None) is not None,
            getattr(args, "tts_provider", None) is not None,
            getattr(args, "macos_voice", None) is not None,
        ]
    ):
        print(f"config: {config_path()}")
        print(json.dumps(_public_config_dict(cfg), indent=2))
        print(f"tts_provider_effective: {cfg.effective_tts_provider()}")
        print(f"effective_voice_id: {cfg.effective_voice_id()}")
        print(f"api_key_source: {cfg.api_key_source()}")
        print(f"api_key_present: {bool(cfg.api_key())}")
        print(f"last_brief: {last_brief_path()}")
        print(f"socket: {socket_path()}")
        return 0

    if args.voice:
        cfg.voice_id = args.voice
    if getattr(args, "tts_provider", None) is not None:
        raw = str(args.tts_provider).strip().lower()
        if raw in ("say", "local", "system", "os"):
            raw = "macos"
        if raw not in ("auto", "xai", "macos"):
            print(
                f"error: tts_provider must be auto|xai|macos (got {args.tts_provider!r})",
                file=sys.stderr,
            )
            return 2
        cfg.tts_provider = raw
    if getattr(args, "macos_voice", None) is not None:
        cfg.macos_voice = str(args.macos_voice).strip()
    if args.speed is not None:
        cfg.speed = float(args.speed)
    if args.mode:
        cfg.mode = args.mode
    if args.autoplay is not None:
        cfg.autoplay = args.autoplay
    if args.model:
        cfg.model = args.model
    if args.live is not None:
        cfg.live_verbatim = bool(args.live)
    if getattr(args, "live_then_brief", None) is not None:
        cfg.live_then_brief = bool(args.live_then_brief)
    if getattr(args, "enabled", None) is not None:
        cfg.enabled = bool(args.enabled)
    save_config(cfg)
    try_send({"cmd": "reload_config"})
    if getattr(args, "enabled", None) is False or (
        args.live is False and is_daemon_alive()
    ):
        try_send({"cmd": "skip"}, timeout=1.0)
    print(f"Saved {config_path()}")
    return 0


def cmd_shutdown(args: argparse.Namespace) -> int:
    if getattr(args, "clear_refs", False):
        clear_all()
    code = _control("shutdown")
    # If daemon already gone, still success after clear
    if code != 0 and not is_daemon_alive():
        return 0
    return code


def cmd_doctor(args: argparse.Namespace) -> int:
    """Install / credential / daemon health check (never prints secrets)."""
    from .doctor import format_doctor_text, run_doctor

    report = run_doctor(plugin_root=_plugin_root())
    if getattr(args, "json", False):
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_doctor_text(report))
    return 0 if report.ok else 1


def cmd_purge(args: argparse.Namespace) -> int:
    """Delete local cache / logs / last-brief (never touches config or API keys)."""
    if args.all:
        do_cache, do_logs, do_last = True, True, True
    elif args.cache or args.logs or args.last:
        do_cache, do_logs, do_last = bool(args.cache), bool(args.logs), bool(args.last)
    else:
        # Default: speech cache only
        do_cache, do_logs, do_last = True, False, False

    targets = []
    if do_cache:
        targets.append("cache")
    if do_logs:
        targets.append("logs")
    if do_last:
        targets.append("last brief/job")
    summary = ", ".join(targets) or "nothing"
    if not getattr(args, "yes", False):
        print(f"Will purge from {data_dir()}: {summary}")
        print("Pass --yes to confirm (config.toml and API keys are never deleted).")
        return 2

    result = purge_runtime(cache=do_cache, logs=do_logs, last=do_last)
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
    else:
        print(f"purged {result['removed_count']} item(s) under {result['data_dir']}")
        for err in result.get("errors") or []:
            print(f"  warn: {err}", file=sys.stderr)
    return 1 if result.get("errors") else 0


def cmd_harden(args: argparse.Namespace) -> int:
    """chmod runtime data dir 700 and files 600 (best-effort)."""
    stats = harden_runtime_tree()
    # Restarting the daemon re-applies socket 600; optional nudge.
    if is_daemon_alive() and not getattr(args, "no_reload", False):
        try_send({"cmd": "reload_config"}, timeout=1.0)
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, **stats, "data_dir": str(data_dir())}, indent=2))
    else:
        print(
            f"hardened {data_dir()}: {stats['dirs']} dir(s), {stats['files']} file(s) "
            f"(dirs 700, files 600)"
        )
        print("Restart the daemon to re-bind the socket with mode 600 if needed:")
        print("  focus-audio shutdown && focus-audio ensure -v")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="focus-audio",
        description="Smart focus audio for Grok Build — spoken briefs via xAI TTS",
    )
    p.add_argument("--version", action="version", version=f"focus-audio {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("daemon", help="Run the background player + IPC server")
    d.set_defaults(func=cmd_daemon)

    en = sub.add_parser(
        "ensure",
        help="SessionStart: register session + start daemon if needed",
    )
    en.add_argument("--session-id", default=None)
    en.add_argument("-v", "--verbose", action="store_true")
    en.set_defaults(func=cmd_ensure)

    rel = sub.add_parser(
        "release",
        help="SessionEnd: drop session ref; stop daemon if last session",
    )
    rel.add_argument("--session-id", default=None)
    rel.add_argument("-v", "--verbose", action="store_true")
    rel.set_defaults(func=cmd_release)

    e = sub.add_parser("enqueue", help="Enqueue current Grok session turn (hook entry)")
    e.add_argument("--session-id", default=None)
    e.add_argument("--cwd", default=None)
    e.add_argument("--force", action="store_true")
    e.add_argument("--mode", choices=["brief", "verbatim"], default=None)
    e.add_argument("-v", "--verbose", action="store_true")
    e.set_defaults(func=cmd_enqueue)

    ls = sub.add_parser(
        "live-start",
        help="UserPromptSubmit: start live verbatim tail of updates.jsonl",
    )
    ls.add_argument("--session-id", default=None)
    ls.add_argument("--cwd", default=None)
    ls.add_argument("-v", "--verbose", action="store_true")
    ls.set_defaults(func=cmd_live_start)

    s = sub.add_parser("speak", help="Speak text from stdin/file/arg")
    s.add_argument("path", nargs="?", default="-", help="File path or - for stdin")
    s.add_argument("-t", "--text", default=None)
    s.add_argument("--mode", choices=["brief", "verbatim"], default=None)
    s.add_argument("--force", action="store_true")
    s.add_argument("--no-daemon", action="store_true")
    s.add_argument("--no-play", action="store_true")
    s.set_defaults(func=cmd_speak)

    ss = sub.add_parser("speak-session", help="Speak last assistant turn of a session")
    ss.add_argument("session_id")
    ss.add_argument("--cwd", default=None)
    ss.add_argument("--mode", choices=["brief", "verbatim"], default=None)
    ss.add_argument("--force", action="store_true")
    ss.add_argument("--no-daemon", action="store_true")
    ss.add_argument("--no-play", action="store_true")
    ss.set_defaults(func=cmd_speak_session)

    for name, fn, help_ in [
        ("status", cmd_status, "Daemon status"),
        ("pause", cmd_pause, "Pause/stop playback"),
        ("resume", cmd_resume, "Replay current from start"),
        ("toggle", cmd_toggle, "Play/pause toggle"),
        ("restart", cmd_restart, "Restart current brief"),
        ("skip", cmd_skip, "Stop and cancel current job"),
        ("rebrief", cmd_rebrief, "Force-regenerate last turn"),
    ]:
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(func=fn)

    sh = sub.add_parser("shutdown", help="Stop the daemon")
    sh.add_argument(
        "--clear-refs",
        action="store_true",
        help="Also clear Grok session refcounts (orphan cleanup)",
    )
    sh.set_defaults(func=cmd_shutdown)

    m = sub.add_parser(
        "mode",
        help="Toggle/set brief|verbatim, announce, and re-speak last turn",
    )
    m.add_argument("mode", nargs="?", choices=["brief", "verbatim"])
    m.set_defaults(func=cmd_mode)

    live_p = sub.add_parser(
        "live",
        help="Toggle/set live mid-turn speech (live_verbatim)",
    )
    live_p.add_argument(
        "state",
        nargs="?",
        default=None,
        help="on|off|toggle (default: toggle)",
    )
    live_p.set_defaults(func=cmd_live)

    power_p = sub.add_parser(
        "power",
        help="Master on/off — disables live + brief + verbatim when off",
    )
    power_p.add_argument(
        "state",
        nargs="?",
        default=None,
        help="on|off|toggle (default: toggle)",
    )
    power_p.set_defaults(func=cmd_power)

    # Convenience aliases used by slash skills
    off_p = sub.add_parser("off", help="Alias for: power off")
    off_p.set_defaults(func=lambda _a: cmd_power(argparse.Namespace(state="off")))
    on_p = sub.add_parser("on", help="Alias for: power on")
    on_p.set_defaults(func=lambda _a: cmd_power(argparse.Namespace(state="on")))

    def _bool_arg(x: str) -> bool:
        return str(x).lower() in ("1", "true", "yes", "on")

    c = sub.add_parser("config", help="Show or update config")
    c.add_argument("--show", action="store_true")
    c.add_argument(
        "--tts-provider",
        dest="tts_provider",
        default=None,
        help="macos (default, free) | xai (cloud voice + smart brief) | auto",
    )
    c.add_argument(
        "--macos-voice",
        dest="macos_voice",
        default=None,
        help="macOS say -v voice name (empty string = system default); list: say -v '?'",
    )
    c.add_argument("--voice", default=None, help="xAI voice_id (e.g. ara)")
    c.add_argument("--speed", type=float, default=None)
    c.add_argument("--mode", choices=["brief", "verbatim"], default=None)
    c.add_argument("--autoplay", type=_bool_arg, default=None)
    c.add_argument(
        "--enabled",
        type=_bool_arg,
        default=None,
        help="Master switch (same as power on/off)",
    )
    c.add_argument("--model", default=None)
    c.add_argument(
        "--live",
        type=_bool_arg,
        default=None,
        help="Enable experimental live_verbatim (mid-turn speech from updates.jsonl)",
    )
    c.add_argument(
        "--live-then-brief",
        dest="live_then_brief",
        type=_bool_arg,
        default=None,
        help="After live mid-turn speech, also play post-turn brief/verbatim (mode)",
    )
    c.set_defaults(func=cmd_config)

    doc = sub.add_parser(
        "doctor",
        help="Check install, API key presence, daemon, and hotkeys (no secrets printed)",
    )
    doc.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable report",
    )
    doc.set_defaults(func=cmd_doctor)

    pur = sub.add_parser(
        "purge",
        help="Delete local speech cache / logs / last brief (never config or API keys)",
    )
    pur.add_argument(
        "--cache",
        action="store_true",
        help="Delete ~/.grok/focus-audio/cache/* (default if no other target flags)",
    )
    pur.add_argument(
        "--logs",
        action="store_true",
        help="Delete hook.log and daemon.log",
    )
    pur.add_argument(
        "--last",
        action="store_true",
        help="Delete last_brief.md and last_job.json",
    )
    pur.add_argument(
        "--all",
        action="store_true",
        help="Cache + logs + last brief/job",
    )
    pur.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Confirm deletion (required)",
    )
    pur.add_argument("--json", action="store_true", help="Machine-readable result")
    pur.set_defaults(func=cmd_purge)

    hd = sub.add_parser(
        "harden",
        help="Set owner-only permissions on ~/.grok/focus-audio (dirs 700, files 600)",
    )
    hd.add_argument("--json", action="store_true")
    hd.add_argument(
        "--no-reload",
        action="store_true",
        help="Do not nudge a running daemon",
    )
    hd.set_defaults(func=cmd_harden)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
