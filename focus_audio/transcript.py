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
# Unclosed fence to EOF (streaming / truncated replies).
CODE_FENCE_OPEN_RE = re.compile(r"```([^\n`]*)\n([\s\S]*)$", re.MULTILINE)
URL_RE = re.compile(r"https?://\S+")
# Home / Users absolute paths (common in agent output).
ABS_HOME_PATH_RE = re.compile(
    r"(?:~|/Users/[\w.-]+)(?:/[\w.+@%-]+)+"
)
# Other absolute Unix paths with at least two segments after root.
ABS_UNIX_PATH_RE = re.compile(r"(?<![\w:@])/([\w.-]+/){1,}[\w.+-]+")
# Relative multi-segment paths ending in a file-ish name (a/b/c.py, plugins/x/y.md).
REL_FILE_PATH_RE = re.compile(
    r"(?<![\w/])(?:[\w.-]+/){1,}[\w.-]+\.[A-Za-z0-9]{1,12}\b"
)
# file.py:42 or path:12-34 line refs
FILE_LINE_RE = re.compile(
    r"\b([\w./+-]+\.[A-Za-z0-9]{1,12}):(\d{1,5})(?:-(\d{1,5}))?\b"
)
# Standalone "line 42" / "lines 10-20" / "L42-L58"
LINE_REF_RE = re.compile(
    r"\b(?:lines?\s+\d+(?:\s*[-–—]\s*\d+)?|L\d+(?:\s*[-–—]\s*L?\d+)?)\b",
    re.IGNORECASE,
)
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
MD_HEADING_RE = re.compile(r"(?m)^#{1,6}\s+")
MD_BULLET_RE = re.compile(r"(?m)^[ \t]*(?:[-*+]|\d+\.)\s+")
MD_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|_|~~)")
# Pipe table: header row + separator + body rows.
MD_TABLE_BLOCK_RE = re.compile(
    r"(?m)^\|.+\|\s*\n\|[-:\s|]+\|\s*\n(?:\|.*\|\s*\n?)+",
)


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


def path_basename(path: str) -> str:
    """Speakable label for a filesystem path (basename only)."""
    p = (path or "").strip().strip("`\"'")
    if not p:
        return ""
    # Drop trailing slashes; keep last non-empty segment.
    parts = [x for x in p.replace("\\", "/").split("/") if x and x != "~"]
    if not parts:
        return "home"
    base = parts[-1]
    # Skip pure ~ or empty after strip of dots
    return base or "folder"


def _fence_placeholder(lang: str, mode: str) -> str:
    """Short speakable stand-in for a fenced code block (no line counts)."""
    lang_tok = (lang or "").strip().split()[0] if (lang or "").strip() else ""
    # Common fence labels that aren't languages
    if lang_tok.lower() in ("", "code", "text", "txt", "plain"):
        return "\n" if mode == "brief" else "\n[code sample]\n"
    if mode == "brief":
        # Prefer silence over "N lines of X" — brief LLM should describe purpose.
        return "\n"
    return f"\n[code sample in {lang_tok}]\n"


def _replace_code_fences(text: str, mode: str) -> str:
    def repl(m: re.Match) -> str:
        return _fence_placeholder(m.group(1) or "", mode)

    out = CODE_FENCE_RE.sub(repl, text)
    # Truncated streaming fences
    out = CODE_FENCE_OPEN_RE.sub(repl, out)
    return out


def _replace_urls(text: str) -> str:
    def repl(m: re.Match) -> str:
        url = m.group(0)
        # Keep very short local-ish hosts somewhat recognizable; otherwise "link".
        if len(url) <= 40 and re.search(r":\d{2,5}(?:/|$)", url):
            # e.g. http://10.0.1.57:8787 → "local link"
            return "local link"
        if len(url) <= 32:
            return url
        return "link"

    return URL_RE.sub(repl, text)


def _replace_paths(text: str) -> str:
    """Collapse absolute/relative multi-segment paths to basenames."""

    def abs_repl(m: re.Match) -> str:
        return path_basename(m.group(0))

    def rel_repl(m: re.Match) -> str:
        return path_basename(m.group(0))

    # Order: home paths first (more specific), then other abs, then relative files.
    out = ABS_HOME_PATH_RE.sub(abs_repl, text)
    out = ABS_UNIX_PATH_RE.sub(abs_repl, out)
    out = REL_FILE_PATH_RE.sub(rel_repl, out)
    return out


def _replace_file_line_refs(text: str) -> str:
    def repl(m: re.Match) -> str:
        return path_basename(m.group(1))

    out = FILE_LINE_RE.sub(repl, text)
    # Drop bare line-number phrases that sound bad spoken.
    out = LINE_REF_RE.sub("", out)
    return out


def _replace_inline_code(text: str) -> str:
    def repl(m: re.Match) -> str:
        inner = m.group(1).strip()
        if not inner:
            return ""
        # Path-like inline code → basename
        if "/" in inner or inner.startswith("~"):
            return path_basename(inner)
        # Long tokens (hashes, keys) → drop or shorten
        if len(inner) > 48 and " " not in inner:
            return "identifier"
        return inner

    return INLINE_CODE_RE.sub(repl, text)


def _collapse_markdown_tables(text: str, mode: str) -> str:
    placeholder = "\n" if mode == "brief" else "\n[table summarized]\n"
    return MD_TABLE_BLOCK_RE.sub(placeholder, text)


def _flatten_markdown(text: str) -> str:
    out = MD_HEADING_RE.sub("", text)
    out = MD_BULLET_RE.sub("", out)
    out = MD_EMPHASIS_RE.sub("", out)
    return out


def _cap_path_list_spam(text: str, max_names: int = 4) -> str:
    """If many bare basenames with extensions appear in a row, compress the list.

    Heuristic for agent file inventories: \"a.py, b.py, c.py, d.py, e.py\".
    """
    # Comma-separated list of 5+ file-like tokens
    list_re = re.compile(
        r"((?:[\w.-]+\.[A-Za-z0-9]{1,12})(?:\s*,\s*[\w.-]+\.[A-Za-z0-9]{1,12}){4,})"
    )

    def repl(m: re.Match) -> str:
        names = [x.strip() for x in m.group(1).split(",")]
        if len(names) <= max_names:
            return m.group(1)
        kept = ", ".join(names[:max_names])
        more = len(names) - max_names
        return f"{kept}, and {more} more files"

    return list_re.sub(repl, text)


def clean_for_audio(text: str, mode: str = "brief") -> str:
    """Local pre-filter so TTS and brief models hear speakable prose.

    Mode-aware:
    - brief: drop code bodies and tables aggressively (no line counts).
    - verbatim: keep short placeholders (\"code sample in python\") so structure
      survives without reading dumps.
    """
    if not text:
        return ""
    use_mode = (mode or "brief").lower()
    if use_mode not in ("brief", "verbatim"):
        use_mode = "brief"

    cleaned = text
    cleaned = _replace_code_fences(cleaned, use_mode)
    cleaned = _collapse_markdown_tables(cleaned, use_mode)
    cleaned = _replace_urls(cleaned)
    cleaned = _replace_inline_code(cleaned)
    cleaned = _replace_file_line_refs(cleaned)
    cleaned = _replace_paths(cleaned)
    cleaned = _flatten_markdown(cleaned)
    cleaned = _cap_path_list_spam(cleaned)
    # Collapse leftover fence ticks and empty brackets noise
    cleaned = cleaned.replace("```", "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +([,.;:!?])", r"\1", cleaned)
    return cleaned.strip()


def load_turn(
    session_id: str,
    cwd: Optional[str] = None,
    min_chars: int = 80,
    root: Optional[Path] = None,
    mode: str = "brief",
) -> Optional[TurnText]:
    session_dir = find_session_dir(session_id, cwd=cwd, root=root)
    if not session_dir:
        return None
    raw = last_assistant_text(session_dir)
    if not raw:
        return None
    cleaned = clean_for_audio(raw, mode=mode)
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
