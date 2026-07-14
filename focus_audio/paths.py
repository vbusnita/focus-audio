"""Filesystem layout for Focus Audio state (owner-only by default)."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Dict, List, Optional

# Owner-only modes for runtime data (conversation cache, logs, socket).
DIR_MODE = 0o700
FILE_MODE = 0o600
SOCKET_MODE = 0o600


def grok_home() -> Path:
    raw = os.environ.get("GROK_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".grok"


def secure_mkdir(path: Path) -> Path:
    """Create directory (and parents) with mode 0o700; re-chmod if it already exists."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    try:
        path.chmod(DIR_MODE)
    except OSError:
        pass
    return path


def secure_chmod_file(path: Path) -> None:
    """Best-effort set a regular file to 0o600."""
    try:
        if path.is_file() and not path.is_symlink():
            path.chmod(FILE_MODE)
    except OSError:
        pass


def secure_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> Path:
    """Write text and chmod 0o600. Parent dir is created owner-only."""
    path = Path(path)
    secure_mkdir(path.parent)
    path.write_text(text, encoding=encoding)
    secure_chmod_file(path)
    return path


def secure_write_bytes(path: Path, data: bytes) -> Path:
    """Write bytes and chmod 0o600. Parent dir is created owner-only."""
    path = Path(path)
    secure_mkdir(path.parent)
    path.write_bytes(data)
    secure_chmod_file(path)
    return path


def secure_open_append(path: Path):
    """Open for append; ensure parent + file mode 0o600 after open."""
    path = Path(path)
    secure_mkdir(path.parent)
    fh = path.open("a", encoding="utf-8")
    secure_chmod_file(path)
    return fh


def data_dir() -> Path:
    """Writable runtime data (queue, cache, socket, config) — mode 0o700."""
    return secure_mkdir(grok_home() / "focus-audio")


def cache_dir() -> Path:
    return secure_mkdir(data_dir() / "cache")


def config_path() -> Path:
    return data_dir() / "config.toml"


def socket_path() -> Path:
    return data_dir() / "daemon.sock"


def pid_path() -> Path:
    return data_dir() / "daemon.pid"


def last_brief_path() -> Path:
    return data_dir() / "last_brief.md"


def last_job_path() -> Path:
    return data_dir() / "last_job.json"


def sessions_root() -> Path:
    return grok_home() / "sessions"


def harden_socket(path: Path) -> None:
    """Restrict Unix socket to owner only (0o600)."""
    try:
        if path.exists():
            path.chmod(SOCKET_MODE)
    except OSError:
        pass


def _mode_str(mode: int) -> str:
    return stat.filemode(mode & 0o777)


def check_runtime_perms(root: Optional[Path] = None) -> Dict[str, object]:
    """Inspect data-dir permissions for doctor.

    Returns dict with ok, level, detail, fix.
    """
    d = Path(root) if root else data_dir()
    issues: List[str] = []
    try:
        st = d.stat()
        mode = stat.S_IMODE(st.st_mode)
        if mode & 0o077:
            issues.append(f"data dir mode {_mode_str(mode)} (want 700)")
    except OSError as e:
        return {
            "ok": False,
            "level": "fail",
            "detail": f"cannot stat {d}: {e}",
            "fix": f"Fix permissions on {d}",
        }

    sock = d / "daemon.sock"
    if sock.exists():
        try:
            sm = stat.S_IMODE(sock.stat().st_mode)
            # Socket mode bits vary; flag other-read/write/exec when present.
            if sm & 0o077:
                issues.append(f"socket mode {_mode_str(sm)} (want 600)")
        except OSError:
            pass

    if issues:
        return {
            "ok": True,  # warn only — still usable
            "level": "warn",
            "detail": "; ".join(issues),
            "fix": "focus-audio harden   # or reinstall ≥0.4.3 and restart daemon",
        }
    return {
        "ok": True,
        "level": "ok",
        "detail": f"{d} owner-only (700)",
        "fix": None,
    }


def harden_runtime_tree(root: Optional[Path] = None) -> Dict[str, int]:
    """chmod data dir 700 and regular files under it 600 (skip sockets/specials)."""
    d = Path(root) if root else data_dir()
    secure_mkdir(d)
    dirs = 1
    files = 0
    for dirpath, dirnames, filenames in os.walk(d):
        pdir = Path(dirpath)
        try:
            pdir.chmod(DIR_MODE)
            dirs += 1
        except OSError:
            pass
        for name in filenames:
            fp = pdir / name
            try:
                st = fp.lstat()
                if stat.S_ISSOCK(st.st_mode):
                    harden_socket(fp)
                    continue
                if stat.S_ISREG(st.st_mode):
                    fp.chmod(FILE_MODE)
                    files += 1
            except OSError:
                pass
    return {"dirs": dirs, "files": files}


def purge_runtime(
    *,
    cache: bool = True,
    logs: bool = False,
    last: bool = False,
    root: Optional[Path] = None,
) -> Dict[str, object]:
    """Delete local conversation residue. Never touches config.toml or API keys."""
    d = Path(root) if root else data_dir()
    removed: List[str] = []
    errors: List[str] = []

    def _unlink(path: Path) -> None:
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
                removed.append(str(path.name))
            elif path.is_dir():
                # only used for emptying cache children
                pass
        except OSError as e:
            errors.append(f"{path.name}: {e}")

    if cache:
        cdir = d / "cache"
        if cdir.is_dir():
            for child in cdir.iterdir():
                try:
                    if child.is_file() or child.is_symlink():
                        child.unlink()
                        removed.append(f"cache/{child.name}")
                    elif child.is_dir():
                        # unexpected nested dir — skip deep delete for safety
                        errors.append(f"cache/{child.name}: skipped directory")
                except OSError as e:
                    errors.append(f"cache/{child.name}: {e}")

    if logs:
        for name in ("hook.log", "daemon.log"):
            _unlink(d / name)

    if last:
        for name in ("last_brief.md", "last_job.json"):
            _unlink(d / name)

    return {
        "removed": removed,
        "removed_count": len(removed),
        "errors": errors,
        "data_dir": str(d),
    }
