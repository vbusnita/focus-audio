"""Health checks for Focus Audio install (public-friendly, never prints secrets)."""

from __future__ import annotations

import platform
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .config import ensure_default_config
from .ipc import is_daemon_alive, try_send
from .paths import config_path, data_dir, sessions_root, socket_path


@dataclass
class Check:
    id: str
    ok: bool
    level: str  # "ok" | "warn" | "fail"
    detail: str
    fix: Optional[str] = None


@dataclass
class DoctorReport:
    ok: bool
    version: str
    checks: List[Check] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "version": self.version,
            "checks": [asdict(c) for c in self.checks],
        }


def _check_python() -> Check:
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 9)
    return Check(
        id="python",
        ok=ok,
        level="ok" if ok else "fail",
        detail=f"Python {major}.{minor}.{sys.version_info[2]}",
        fix=None if ok else "Install Python 3.9+ (python3 on PATH)",
    )


def _check_platform() -> Check:
    system = platform.system()
    if system == "Darwin":
        return Check(
            id="platform",
            ok=True,
            level="ok",
            detail=f"macOS ({platform.mac_ver()[0] or 'unknown'})",
        )
    return Check(
        id="platform",
        ok=True,
        level="warn",
        detail=f"{system} — macOS is the primary supported host",
        fix="Playback and hotkeys are best supported on macOS today",
    )


def _check_plugin_layout(plugin_root: Path) -> Check:
    need = [
        plugin_root / "plugin.json",
        plugin_root / "focus_audio" / "__init__.py",
        plugin_root / "bin" / "focus-audio",
        plugin_root / "hooks" / "hooks.json",
    ]
    missing = [str(p.relative_to(plugin_root)) for p in need if not p.exists()]
    ok = not missing
    return Check(
        id="plugin_layout",
        ok=ok,
        level="ok" if ok else "fail",
        detail="plugin files present" if ok else f"missing: {', '.join(missing)}",
        fix=None
        if ok
        else "Re-clone or reinstall: grok plugin install . --trust",
    )


def _check_api_key() -> Check:
    cfg = ensure_default_config()
    present = bool(cfg.api_key())
    source = cfg.api_key_source()
    if present:
        return Check(
            id="api_key",
            ok=True,
            level="ok",
            detail=f"present via {source}",
        )
    return Check(
        id="api_key",
        ok=False,
        level="fail",
        detail="missing (never stored in Focus Audio config)",
        fix=(
            "Set your own xAI key: export XAI_API_KEY=… "
            "or macOS Keychain service `xai-api-key` for account $USER "
            '(security add-generic-password -a "$USER" -s "xai-api-key" -w "…")'
        ),
    )


def _check_config() -> Check:
    path = config_path()
    cfg = ensure_default_config()
    parts = [
        f"path={path}",
        f"enabled={cfg.enabled}",
        f"mode={cfg.mode}",
        f"live_verbatim={cfg.live_verbatim}",
        f"autoplay={cfg.autoplay}",
    ]
    if not cfg.enabled:
        return Check(
            id="config",
            ok=True,
            level="warn",
            detail="; ".join(parts),
            fix="Master is OFF — run: focus-audio on",
        )
    return Check(
        id="config",
        ok=True,
        level="ok",
        detail="; ".join(parts),
    )


def _check_daemon() -> Check:
    alive = is_daemon_alive()
    if not alive:
        return Check(
            id="daemon",
            ok=True,
            level="warn",
            detail=f"not running (socket {socket_path()})",
            fix="Open a new Grok session or run: focus-audio ensure -v",
        )
    status = try_send({"cmd": "status"}, timeout=1.0) or {}
    st = status.get("status") or status.get("daemon_status") or "up"
    return Check(
        id="daemon",
        ok=True,
        level="ok",
        detail=f"running ({st})",
    )


def _check_sessions_dir() -> Check:
    root = sessions_root()
    if root.is_dir():
        return Check(
            id="grok_sessions",
            ok=True,
            level="ok",
            detail=f"{root}",
        )
    return Check(
        id="grok_sessions",
        ok=True,
        level="warn",
        detail=f"{root} not found yet",
        fix="Start Grok Build once so ~/.grok/sessions is created",
    )


def _check_player_tools() -> Check:
    system = platform.system()
    if system != "Darwin":
        return Check(
            id="player",
            ok=True,
            level="warn",
            detail="non-macOS: playback backend may be limited",
            fix="Primary support is macOS (AVAudioPlayer / afplay)",
        )
    afplay = shutil.which("afplay")
    if afplay:
        return Check(
            id="player",
            ok=True,
            level="ok",
            detail=f"afplay available ({afplay})",
        )
    return Check(
        id="player",
        ok=True,
        level="warn",
        detail="afplay not on PATH (AVAudioPlayer may still work)",
        fix="Install Xcode CLT or ensure /usr/bin/afplay exists",
    )


def _check_hotkeys() -> Check:
    cfg = ensure_default_config()
    if not cfg.hotkeys:
        return Check(
            id="hotkeys",
            ok=True,
            level="ok",
            detail="disabled in config (hotkeys=false)",
        )
    try:
        import pynput  # noqa: F401
    except ImportError:
        return Check(
            id="hotkeys",
            ok=True,
            level="warn",
            detail="pynput not installed — global hotkeys unavailable",
            fix="pip3 install --user pynput  (+ grant Accessibility to the Python process)",
        )
    return Check(
        id="hotkeys",
        ok=True,
        level="ok",
        detail="pynput importable (grant Accessibility if hotkeys do not fire)",
        fix="System Settings → Privacy & Security → Accessibility → allow Python / Terminal",
    )


def _check_data_dir() -> Check:
    d = data_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".doctor_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as e:
        return Check(
            id="data_dir",
            ok=False,
            level="fail",
            detail=f"not writable: {d} ({e})",
            fix=f"Fix permissions on {d}",
        )
    return Check(
        id="data_dir",
        ok=True,
        level="ok",
        detail=str(d),
    )


def run_doctor(*, plugin_root: Optional[Path] = None) -> DoctorReport:
    root = plugin_root or Path(__file__).resolve().parent.parent
    checks = [
        _check_python(),
        _check_platform(),
        _check_plugin_layout(root),
        _check_data_dir(),
        _check_api_key(),
        _check_config(),
        _check_sessions_dir(),
        _check_player_tools(),
        _check_hotkeys(),
        _check_daemon(),
    ]
    hard_fail = any(c.level == "fail" for c in checks)
    return DoctorReport(ok=not hard_fail, version=__version__, checks=checks)


def format_doctor_text(report: DoctorReport) -> str:
    lines = [
        f"Focus Audio doctor v{report.version}",
        f"overall: {'OK' if report.ok else 'NEEDS ATTENTION'}",
        "",
    ]
    icons = {"ok": "✓", "warn": "!", "fail": "✗"}
    for c in report.checks:
        mark = icons.get(c.level, "?")
        lines.append(f"  [{mark}] {c.id}: {c.detail}")
        if c.fix and c.level != "ok":
            lines.append(f"      fix → {c.fix}")
    lines.append("")
    if report.ok:
        lines.append("Ready: open a Grok Build session (or focus-audio ensure -v) and talk.")
    else:
        lines.append("Fix the ✗ items above, then re-run: focus-audio doctor")
    return "\n".join(lines)
