"""Locate Grok sessions and extract the last assistant turn."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import quote

from .paths import sessions_root


CODE_FENCE_RE = re.compile(r"```([^\n`]*)\n([\s\S]*?)```", re.MULTILINE)
URL_RE = re.compile(r"https?://\S+")


@dataclass
class TurnText:
    session_id: str
    session_dir: Path
    raw: str
    cleaned: str
    char_count: int


def encode_cwd(cwd: str) -> str:
    """Match Grok's URL-encoded session group directory name."""
    return quote(cwd, safe="")


def find_session_dir(
    session_id: str,
    cwd: Optional[str] = None,
    root: Optional[Path] = None,
) -> Optional[Path]:
    """Find ~/.grok/sessions/<encoded-cwd>/<session_id>/."""
    base = root or sessions_root()
    if not base.is_dir():
        return None

    if cwd:
        candidate = base / encode_cwd(cwd) / session_id
        if candidate.is_dir():
            return candidate
        # Also try raw path segments if already encoded
        candidate2 = base / cwd / session_id
        if candidate2.is_dir():
            return candidate2

    # Fallback: scan all workspaces for this session id
    for group in base.iterdir():
        if not group.is_dir():
            continue
        hit = group / session_id
        if hit.is_dir():
            return hit
    return None


def _iter_jsonl(path: Path) -> Iterable[dict]:
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def extract_assistant_contents(chat_history: Path) -> List[str]:
    """Return assistant message contents in order (string content only)."""
    out: List[str] = []
    for obj in _iter_jsonl(chat_history):
        if obj.get("type") != "assistant":
            continue
        content = obj.get("content")
        if isinstance(content, str) and content.strip():
            out.append(content)
        elif isinstance(content, list):
            parts: List[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    if block.get("type") in ("text", "output_text") and block.get("text"):
                        parts.append(str(block["text"]))
                    elif "text" in block:
                        parts.append(str(block["text"]))
            joined = "\n".join(parts).strip()
            if joined:
                out.append(joined)
    return out


def last_assistant_text(session_dir: Path) -> Optional[str]:
    history = session_dir / "chat_history.jsonl"
    contents = extract_assistant_contents(history)
    if not contents:
        return None
    return contents[-1]


def clean_for_audio(text: str, mode: str = "brief") -> str:
    """Local pre-filter: replace code fences, shorten URLs, drop empty noise."""

    def _fence_repl(m: re.Match) -> str:
        lang = (m.group(1) or "").strip() or "code"
        body = m.group(2) or ""
        lines = body.count("\n") + (1 if body.strip() else 0)
        return f"\n[code block: ~{lines} lines of {lang}]\n"

    cleaned = CODE_FENCE_RE.sub(_fence_repl, text)
    cleaned = URL_RE.sub(lambda m: m.group(0)[:48] + "…" if len(m.group(0)) > 50 else m.group(0), cleaned)
    # Collapse excessive blank lines
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def load_turn(
    session_id: str,
    cwd: Optional[str] = None,
    min_chars: int = 80,
    root: Optional[Path] = None,
) -> Optional[TurnText]:
    session_dir = find_session_dir(session_id, cwd=cwd, root=root)
    if not session_dir:
        return None
    raw = last_assistant_text(session_dir)
    if not raw:
        return None
    cleaned = clean_for_audio(raw)
    if len(cleaned) < min_chars:
        return None
    return TurnText(
        session_id=session_id,
        session_dir=session_dir,
        raw=raw,
        cleaned=cleaned,
        char_count=len(cleaned),
    )


def updates_path(session_dir: Path) -> Path:
    return session_dir / "updates.jsonl"


def _text_from_content(content: object) -> str:
    """Extract plain text from ACP content payloads."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if content.get("type") in ("text", "output_text") and content.get("text"):
            return str(content["text"])
        if "text" in content:
            return str(content["text"])
        return ""
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            t = _text_from_content(block)
            if t:
                parts.append(t)
        return "".join(parts)
    return ""


def parse_update_line(line: str) -> Optional[dict]:
    """Parse one updates.jsonl line → the inner update dict, or None."""
    line = (line or "").strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    params = obj.get("params")
    if isinstance(params, dict) and isinstance(params.get("update"), dict):
        return params["update"]
    if isinstance(obj.get("update"), dict):
        return obj["update"]
    if obj.get("sessionUpdate"):
        return obj
    return None


def extract_agent_message_text(update: dict) -> Optional[str]:
    """If update is an agent_message_chunk, return its text (may be empty)."""
    if not isinstance(update, dict):
        return None
    if update.get("sessionUpdate") != "agent_message_chunk":
        return None
    return _text_from_content(update.get("content"))


def is_turn_completed(update: dict) -> bool:
    return isinstance(update, dict) and update.get("sessionUpdate") == "turn_completed"


@dataclass
class LiveEvent:
    """One event from a live updates.jsonl tail."""

    kind: str  # "message" | "turn_completed" | "other"
    text: str = ""
    raw: Optional[dict] = None


def classify_update(update: dict) -> LiveEvent:
    if is_turn_completed(update):
        return LiveEvent(kind="turn_completed", raw=update)
    text = extract_agent_message_text(update)
    if text is not None:
        return LiveEvent(kind="message", text=text, raw=update)
    return LiveEvent(kind="other", raw=update)


class UpdatesTail:
    """Byte-offset tail over updates.jsonl (append-only)."""

    def __init__(self, path: Path, *, start_at_end: bool = True) -> None:
        self.path = Path(path)
        self.offset = 0
        self._partial = ""
        if start_at_end and self.path.is_file():
            try:
                self.offset = self.path.stat().st_size
            except OSError:
                self.offset = 0

    def poll(self) -> List[LiveEvent]:
        """Read any newly appended complete lines and classify them."""
        if not self.path.is_file():
            return []
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        # File truncated / rotated — restart from beginning of new content.
        if size < self.offset:
            self.offset = 0
            self._partial = ""
        if size == self.offset and not self._partial:
            return []

        try:
            with self.path.open("r", encoding="utf-8") as fh:
                fh.seek(self.offset)
                data = fh.read()
                self.offset = fh.tell()
        except OSError:
            return []

        if not data:
            return []

        buf = self._partial + data
        if "\n" not in buf:
            self._partial = buf
            return []

        parts = buf.split("\n")
        if buf.endswith("\n"):
            # split yields trailing empty string after final newline
            self._partial = ""
            complete = parts[:-1]
        else:
            self._partial = parts[-1]
            complete = parts[:-1]

        events: List[LiveEvent] = []
        for line in complete:
            update = parse_update_line(line)
            if update is None:
                continue
            events.append(classify_update(update))
        return events
