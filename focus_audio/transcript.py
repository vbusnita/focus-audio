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
MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
# -, *, +, unicode bullets, or 1. / 1) ordered items.
MD_LIST_ITEM_RE = re.compile(
    r"^[ \t]*(?:[-*+•▪▸►‣∙]|\d{1,3}[.)])\s+(.+?)\s*$"
)
# Markdown links: [label](url) — keep label only (URL already often replaced).
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
# Pipe table: header row + separator + body rows.
MD_TABLE_BLOCK_RE = re.compile(
    r"(?m)^\|.+\|\s*\n\|[-:\s|]+\|\s*\n(?:\|.*\|\s*\n?)+",
)
# Key chords: Ctrl+Shift+M, Cmd+C, Option+Space, …
KEY_CHORD_RE = re.compile(
    r"\b("
    r"(?:Ctrl|Control|Cmd|Command|Alt|Option|Shift|Meta|Super|Win|Windows)"
    r"(?:\s*\+\s*(?:Ctrl|Control|Cmd|Command|Alt|Option|Shift|Meta|Super|"
    r"Win|Windows|Space|Tab|Enter|Return|Esc|Escape|Delete|Backspace|"
    r"Up|Down|Left|Right|[A-Za-z0-9]|F\d{1,2}))+"
    r")\b",
    re.IGNORECASE,
)
_KEY_NAMES = {
    "ctrl": "control",
    "control": "control",
    "cmd": "command",
    "command": "command",
    "alt": "alt",
    "option": "option",
    "shift": "shift",
    "meta": "meta",
    "super": "super",
    "win": "windows",
    "windows": "windows",
    "esc": "escape",
    "escape": "escape",
    "enter": "enter",
    "return": "return",
    "tab": "tab",
    "space": "space",
    "delete": "delete",
    "backspace": "backspace",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
}
# Agent routing / attribution banners — strip before TTS when present.
# Patterns match common "Routed:" lines and fenced/bare routing JSON; no-ops
# for agents that never emit them.
ROUTED_LINE_RE = re.compile(r"(?m)^[ \t]*Routed:\s*.+(?:\n|$)")
# ```harness-signal ... ``` (closed) or unclosed to EOF (streaming chunks).
HARNESS_SIGNAL_FENCE_RE = re.compile(
    r"```+\s*harness-signal\b[^\n]*\n[\s\S]*?(?:```+|(?=\Z))",
    re.IGNORECASE,
)
# Bare JSON object containing harness_intent (agent forgot the fence).
HARNESS_JSON_OBJ_RE = re.compile(
    r"\{[^{}]*\"harness_intent\"[^{}]*\}",
    re.DOTALL,
)
# Residual JSON field lines if a multi-chunk stream split the object.
HARNESS_JSON_FIELD_LINE_RE = re.compile(
    r"(?m)^[ \t]*\"(?:"
    r"harness_intent|agent|packs_loaded|tool|attribution_source|"
    r"deep_loaded|token_window|confidence"
    r")\"\s*:\s*.+$"
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


def session_dir_from_transcript_path(
    transcript_path: Optional[str],
) -> Optional[Path]:
    """Map Grok hook ``transcriptPath`` (usually ``…/updates.jsonl``) → session dir.

    Open-source Grok sets ``transcript_path`` on the hook envelope to the session's
    ``updates.jsonl`` when that file exists. Parent directory is the session dir.
    """
    if not transcript_path:
        return None
    try:
        p = Path(str(transcript_path)).expanduser()
    except (TypeError, ValueError):
        return None
    if p.is_dir():
        return p
    # File path (updates.jsonl / chat_history.jsonl) or not-yet-created file.
    if p.suffix.lower() == ".jsonl" or p.name.endswith(".jsonl"):
        parent = p.parent
        if parent.is_dir() or parent.exists():
            return parent
        # Parent may exist after a short race; still return the path for callers
        # that only need a stable location.
        return parent if str(parent) not in ("", ".") else None
    if p.is_file():
        return p.parent
    return None


def find_session_dir(
    session_id: str,
    cwd: Optional[str] = None,
    root: Optional[Path] = None,
    *,
    transcript_path: Optional[str] = None,
) -> Optional[Path]:
    """Find ~/.grok/sessions/<encoded-cwd>/<session_id>/.

    Prefer ``transcript_path`` from the Grok hook envelope when present (O(1));
    then cwd-scoped lookup; then a workspace scan.
    """
    from_tx = session_dir_from_transcript_path(transcript_path)
    if from_tx is not None and from_tx.is_dir():
        # If session_id is known, require the basename to match when possible.
        if not session_id or from_tx.name == session_id:
            return from_tx

    base = root or sessions_root()
    if not base.is_dir():
        # transcript path may still be valid even if default sessions root differs
        if from_tx is not None and from_tx.is_dir():
            return from_tx
        return None

    if session_id and cwd:
        candidate = base / encode_cwd(cwd) / session_id
        if candidate.is_dir():
            return candidate
        # Also try raw path segments if already encoded
        candidate2 = base / cwd / session_id
        if candidate2.is_dir():
            return candidate2

    if session_id:
        # Fallback: scan all workspaces for this session id
        for group in base.iterdir():
            if not group.is_dir():
                continue
            hit = group / session_id
            if hit.is_dir():
                return hit

    if from_tx is not None and from_tx.is_dir():
        return from_tx
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


def last_assistant_text_from_history(session_dir: Path) -> Optional[str]:
    """Last assistant message from ``chat_history.jsonl`` (may lag Stop slightly)."""
    history = session_dir / "chat_history.jsonl"
    contents = extract_assistant_contents(history)
    if not contents:
        return None
    return contents[-1]


def last_assistant_text_from_updates(session_dir: Path) -> Optional[str]:
    """Rebuild the last assistant turn from ``updates.jsonl`` agent_message_chunk lines.

    Chunks are appended during the turn (often before ``chat_history.jsonl`` is
    rewritten). Group by ``turn_completed``; if Stop races the completion marker,
    return the in-progress assembly when it has text.
    """
    path = updates_path(session_dir)
    if not path.is_file():
        return None

    current_parts: List[str] = []
    last_complete: Optional[str] = None

    for obj in _iter_jsonl(path):
        update = None
        params = obj.get("params")
        if isinstance(params, dict) and isinstance(params.get("update"), dict):
            update = params["update"]
        elif isinstance(obj.get("update"), dict):
            update = obj["update"]
        elif obj.get("sessionUpdate"):
            update = obj
        if not isinstance(update, dict):
            continue

        su = update.get("sessionUpdate")
        if su == "agent_message_chunk":
            text = _text_from_content(update.get("content"))
            if text:
                current_parts.append(text)
            continue
        if su == "turn_completed":
            if current_parts:
                joined = "".join(current_parts)
                if joined.strip():
                    last_complete = joined
                current_parts = []
            continue
        # A new user message starts a turn; flush any open assistant assembly.
        if su == "user_message_chunk" and current_parts:
            joined = "".join(current_parts)
            if joined.strip():
                last_complete = joined
            current_parts = []

    if current_parts:
        joined = "".join(current_parts)
        if joined.strip():
            return joined
    return last_complete


def last_assistant_text(
    session_dir: Path,
    *,
    prefer_updates: bool = True,
) -> Optional[str]:
    """Last assistant turn text for speech.

    Prefer ``updates.jsonl`` (streamed during the turn) over ``chat_history.jsonl``
    (often rewritten slightly after Stop). Fall back to history when updates are
    missing or empty.
    """
    if prefer_updates:
        from_updates = last_assistant_text_from_updates(session_dir)
        if from_updates and from_updates.strip():
            return from_updates
    return last_assistant_text_from_history(session_dir)


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
            # e.g. http://127.0.0.1:8787 → "local link"
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


def _ensure_spoken_sentence(text: str) -> str:
    """Make a list item / heading a real sentence so TTS can pause."""
    t = " ".join((text or "").split()).strip()
    if not t:
        return ""
    # Drop a trailing colon on list items ("Shipped:" → "Shipped.")
    if len(t) > 1 and t.endswith(":") and not t.endswith("::"):
        t = t[:-1].rstrip()
    if not t:
        return ""
    if t[0].islower():
        t = t[0].upper() + t[1:]
    if t[-1] not in ".!?":
        t += "."
    return t


def _flatten_markdown(text: str) -> str:
    """Strip markdown chrome and turn lists/headings into spoken sentences.

    Bullet markers used to be deleted while leaving bare phrases on separate
    lines. Downstream TTS chunking collapses newlines to spaces, so
    ``- Fixed login\\n- Shipped v1`` became one run-on phrase. Each list item
    and heading is now a terminal sentence (period) so prosody can pause.

    Only *paired* emphasis markers are stripped — bare ``_`` in snake_case
    must survive until ``expand_for_speech``.
    """
    if not text:
        return ""

    lines_out: List[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        if not line.strip():
            lines_out.append("")
            continue

        hm = MD_HEADING_RE.match(line)
        if hm:
            spoken = _ensure_spoken_sentence(hm.group(2))
            if spoken:
                lines_out.append(spoken)
            continue

        lm = MD_LIST_ITEM_RE.match(line)
        if lm:
            spoken = _ensure_spoken_sentence(lm.group(1))
            if spoken:
                lines_out.append(spoken)
            continue

        lines_out.append(line)

    out = "\n".join(lines_out)
    out = MD_LINK_RE.sub(r"\1", out)
    # Paired emphasis only (non-greedy, single-line).
    out = re.sub(r"\*\*(.+?)\*\*", r"\1", out)
    out = re.sub(r"__(.+?)__", r"\1", out)
    out = re.sub(r"~~(.+?)~~", r"\1", out)
    # Single *italic* / _italic_ when not mid-token (protect snake_case / *glob*).
    out = re.sub(r"(?<!\w)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\w)", r"\1", out)
    out = re.sub(r"(?<!\w)_(?!\s)([^_\n]+?)(?<!\s)_(?!\w)", r"\1", out)
    return out


def _speak_key_chord(match: re.Match) -> str:
    raw = match.group(1)
    parts = re.split(r"\s*\+\s*", raw)
    spoken: List[str] = []
    for part in parts:
        key = part.strip()
        if not key:
            continue
        low = key.lower()
        if low in _KEY_NAMES:
            spoken.append(_KEY_NAMES[low])
        elif re.fullmatch(r"F\d{1,2}", key, re.I):
            spoken.append(key.upper())
        elif len(key) == 1:
            spoken.append(key.upper())
        else:
            spoken.append(key)
    return " ".join(spoken)


def expand_for_speech(text: str) -> str:
    """Rewrite symbols and tokens that TTS mangles into speakable prose.

    Applied after structural cleaners (paths, fences, markdown) so both
    brief and verbatim modes — including LLM-skip and live paths — hear
    arrows, operators, key chords, and snake_case as words rather than
    silence or glitches.
    """
    if not text:
        return ""
    out = text

    # Keyboard chords before bare "+" handling.
    out = KEY_CHORD_RE.sub(_speak_key_chord, out)

    # Multi-char arrows / comparisons (order: longer tokens first).
    multi = (
        ("<=>", " is equivalent to "),
        ("<->", " to "),
        ("===", " is identical to "),
        ("!==", " is not identical to "),
        ("==", " equals "),
        ("!=", " is not equal to "),
        ("<>", " is not equal to "),
        ("<=", " less than or equal to "),
        (">=", " greater than or equal to "),
        ("=>", " then "),
        ("->", " to "),
        ("<-", " from "),
        ("±", " plus or minus "),
        ("≠", " is not equal to "),
        ("≤", " less than or equal to "),
        ("≥", " greater than or equal to "),
        ("≈", " approximately "),
        ("→", " to "),
        ("⇒", " then "),
        ("⟶", " to "),
        ("←", " from "),
        ("⇐", " from "),
        ("↔", " and "),
        ("⇔", " if and only if "),
        ("…", ", "),
        ("...", ", "),
    )
    for src, dst in multi:
        if src in out:
            out = out.replace(src, dst)

    # Key=value pairs common in status lines (enabled=true, mode=verbatim).
    out = re.sub(
        r"\b([A-Za-z][\w.-]{0,40})=(true|false|on|off|yes|no|null|none)\b",
        r"\1 is \2",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"\b([A-Za-z][\w.-]{0,40})=([A-Za-z][\w.-]{0,40})\b",
        r"\1 is \2",
        out,
    )

    # Numbers with units / money / percent before stripping bare symbols.
    out = re.sub(r"\$(\d+(?:\.\d+)?)\b", r"\1 dollars", out)
    out = re.sub(r"\b(\d+(?:\.\d+)?)%", r"\1 percent", out)
    out = re.sub(r"\b(\d+(?:\.\d+)?)x\b", r"\1 times", out, flags=re.IGNORECASE)

    # @mentions and #tags (keep the token, drop the sigil as a spoken glyph).
    out = re.sub(r"@([A-Za-z][\w.-]{0,40})", r"at \1", out)
    out = re.sub(r"#([A-Za-z][\w.-]{0,40})", r"tag \1", out)

    # Snake_case identifiers → spaced words (after path collapse).
    out = re.sub(r"(?<=[A-Za-z0-9])_(?=[A-Za-z0-9])", " ", out)

    # Slash between tokens (residual paths, and/or) — not :// in URLs.
    out = re.sub(r"(?<=[\w.])/(?=[\w.])", " ", out)

    # Spaced asterisk as multiply; drop leftover bare stars.
    out = re.sub(r"\s\*\s", " times ", out)
    out = out.replace("*", " ")

    # Single-char symbols that often block or glitch TTS.
    singles = (
        ("·", ", "),
        ("•", ", "),
        ("—", ", "),
        ("–", ", "),
        ("―", ", "),
        ("&", " and "),
        ("@", " at "),
        ("#", " number "),
        ("~", " approximately "),
        ("^", " "),
        ("|", ", "),
        ("\\", " "),
        ("{", " "),
        ("}", " "),
        ("[", " "),
        ("]", " "),
        ("<", " less than "),
        (">", " greater than "),
        ("=", " equals "),
        ("+", " plus "),
    )
    for src, dst in singles:
        if src in out:
            out = out.replace(src, dst)

    # Collapse whitespace introduced by expansions; keep paragraph breaks.
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r" *\n *", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r" +([,.;:!?])", r"\1", out)
    out = re.sub(r"([,.;:!?]){2,}", r"\1", out)
    return out.strip()


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


def strip_harness_metadata(text: str) -> str:
    """Drop agent routing / attribution banners from speakable text when present.

    Implementation detail: strips common ``Routed: …`` lines, fenced routing
    JSON blocks, bare ``harness_intent`` objects, and residual field lines from
    split streams. Safe no-op for agents that never emit those patterns — most
    users never notice this path. Applied before other cleaners so we never
    TTS a placeholder about the routing fence itself.
    """
    if not text:
        return ""
    out = text
    out = HARNESS_SIGNAL_FENCE_RE.sub("\n", out)
    out = ROUTED_LINE_RE.sub("\n", out)
    out = HARNESS_JSON_OBJ_RE.sub("\n", out)
    out = HARNESS_JSON_FIELD_LINE_RE.sub("", out)
    # Orphan fence *openers* only (partial stream). Do not strip bare ``` lines —
    # those are normal code-fence closers and must stay for _replace_code_fences.
    out = re.sub(r"(?m)^[ \t]*```+\s*harness-signal\b[^\n]*$", "", out, flags=re.I)
    # Lone braces that only wrapped a stripped signal object.
    out = re.sub(r"(?m)^[ \t]*[{}]\s*$", "", out)
    return out


def clean_for_audio(text: str, mode: str = "brief") -> str:
    """Local pre-filter so TTS and brief models hear speakable prose.

    Mode-aware:
    - brief: drop code bodies and tables aggressively (no line counts).
    - verbatim: keep short placeholders (\"code sample in python\") so structure
      survives without reading dumps.

    Always strips agent routing banners when present (before other transforms)
    so live and post-turn speech do not read that bookkeeping aloud.
    """
    if not text:
        return ""
    use_mode = (mode or "brief").lower()
    if use_mode not in ("brief", "verbatim"):
        use_mode = "brief"

    cleaned = strip_harness_metadata(text)
    cleaned = _replace_code_fences(cleaned, use_mode)
    cleaned = _collapse_markdown_tables(cleaned, use_mode)
    cleaned = _replace_urls(cleaned)
    cleaned = _replace_inline_code(cleaned)
    cleaned = _replace_file_line_refs(cleaned)
    cleaned = _replace_paths(cleaned)
    cleaned = _flatten_markdown(cleaned)
    cleaned = _cap_path_list_spam(cleaned)
    # Collapse leftover fence ticks before symbol expansion.
    cleaned = cleaned.replace("```", "")
    # Symbols / operators / key chords → words (both brief and verbatim).
    cleaned = expand_for_speech(cleaned)
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
    *,
    transcript_path: Optional[str] = None,
) -> Optional[TurnText]:
    session_dir = find_session_dir(
        session_id, cwd=cwd, root=root, transcript_path=transcript_path
    )
    if not session_dir:
        return None
    # Prefer updates.jsonl (ready at Stop more often) over chat_history.
    raw = last_assistant_text(session_dir, prefer_updates=True)
    if not raw:
        return None
    cleaned = clean_for_audio(raw, mode=mode)
    if len(cleaned) < min_chars:
        return None
    sid = session_id or session_dir.name
    return TurnText(
        session_id=sid,
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
