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
from .paths import config_path, data_dir, last_brief_path, socket_path
from .pipeline import prepare_audio, prepare_from_session
from .player import Player
from .transcript import load_turn


def _plugin_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _focus_audio_disabled() -> bool:
    return os.environ.get("FOCUS_AUDIO", "").strip().lower() in ("0", "false", "off", "no")


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
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def _active_session_fallback(cwd: Optional[str] = None) -> Optional[str]:
    """If hooks omit session id, pick from ~/.grok/active_sessions.json."""
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
    data_dir().mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
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
    """Return (session_id, cwd) from args/env/payload/fallbacks."""
    session_id = (
        getattr(args, "session_id", None)
        or os.environ.get("GROK_SESSION_ID")
        or os.environ.get("CLAUDE_SESSION_ID")
        or payload.get("sessionId")
        or payload.get("session_id")
        or ""
    )
    cwd = (
        getattr(args, "cwd", None)
        or os.environ.get("GROK_WORKSPACE_ROOT")
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or payload.get("workspaceRoot")
        or payload.get("cwd")
        or os.getcwd()
    )

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
                    hist = child / "chat_history.jsonl"
                    if child.is_dir() and hist.is_file():
                        m = hist.stat().st_mtime
                        if m > newest_m:
                            newest_m = m
                            newest = child.name
                if newest:
                    session_id = newest
                    _hook_log(f"session_id from newest transcript: {session_id}")
        except Exception as e:
            _hook_log(f"newest-session fallback error: {e}")

    return str(session_id or ""), str(cwd or "")


def cmd_enqueue(args: argparse.Namespace) -> int:
    """Called by Grok Stop hook — always fail-open (exit 0)."""
    if _focus_audio_disabled():
        _hook_log("enqueue skipped: FOCUS_AUDIO disabled")
        return 0

    payload = _read_hook_payload()
    session_id, cwd = _resolve_hook_session(args, payload)

    if not session_id:
        _hook_log(
            "enqueue skipped: no session_id "
            f"(env={bool(os.environ.get('GROK_SESSION_ID'))} "
            f"payload_keys={list(payload.keys())} cwd={cwd})"
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
        resp = send_command(
            {
                "cmd": "enqueue",
                "session_id": session_id,
                "cwd": cwd,
                "force": bool(args.force),
                "mode": args.mode,
            },
            timeout=3.0,
        )
        _hook_log(f"enqueue ok session={session_id} resp={resp}")
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
    if not getattr(cfg, "live_verbatim", False):
        _hook_log("live-start skipped: live_verbatim=false")
        return 0

    payload = _read_hook_payload()
    session_id, cwd = _resolve_hook_session(args, payload)
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
        resp = send_command(
            {
                "cmd": "live_start",
                "session_id": session_id,
                "cwd": cwd,
            },
            timeout=3.0,
        )
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
        ]
    ):
        print(f"config: {config_path()}")
        # Never dump secrets — only non-secret settings
        public = {k: v for k, v in cfg.__dict__.items() if "key" not in k.lower() or k == "api_key_env"}
        print(json.dumps(public, indent=2))
        print(f"api_key_source: {cfg.api_key_source()}")
        print(f"api_key_present: {bool(cfg.api_key())}")
        print(f"last_brief: {last_brief_path()}")
        print(f"socket: {socket_path()}")
        return 0

    if args.voice:
        cfg.voice_id = args.voice
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
    save_config(cfg)
    try_send({"cmd": "reload_config"})
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

    def _bool_arg(x: str) -> bool:
        return str(x).lower() in ("1", "true", "yes", "on")

    c = sub.add_parser("config", help="Show or update config")
    c.add_argument("--show", action="store_true")
    c.add_argument("--voice", default=None)
    c.add_argument("--speed", type=float, default=None)
    c.add_argument("--mode", choices=["brief", "verbatim"], default=None)
    c.add_argument("--autoplay", type=_bool_arg, default=None)
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

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
