"""Simple JSON-line Unix socket protocol for the Focus Audio daemon."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any, Dict, Optional

from .paths import socket_path


def send_command(cmd: Dict[str, Any], timeout: float = 5.0) -> Dict[str, Any]:
    """Send a command to the daemon; raise ConnectionError if not running."""
    path = str(socket_path())
    if not Path(path).exists():
        raise ConnectionError("Focus Audio daemon is not running (no socket)")

    payload = (json.dumps(cmd) + "\n").encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(path)
        sock.sendall(payload)
        # Read one JSON line response
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
    if not buf:
        return {"ok": False, "error": "empty response"}
    try:
        return json.loads(buf.decode("utf-8").strip())
    except json.JSONDecodeError:
        return {"ok": False, "error": f"bad response: {buf[:200]!r}"}


def try_send(cmd: Dict[str, Any], timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    try:
        return send_command(cmd, timeout=timeout)
    except (ConnectionError, OSError, socket.timeout):
        return None


def is_daemon_alive() -> bool:
    resp = try_send({"cmd": "ping"}, timeout=1.0)
    return bool(resp and resp.get("ok"))
