"""Synthesize spoken scripts from assistant turns (brief or verbatim)."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .config import Config

BRIEF_SYSTEM = """You convert AI coding-agent replies into a short spoken focus brief for a developer who is listening, not reading.

Output ONLY a single JSON object. No markdown fences, no commentary, no keys beyond those listed:
{{
  "headline": "one sentence: what finished, failed, or is waiting",
  "changes": ["up to 3 short sentences of what changed"],
  "caveat": "optional one-sentence risk or note, or empty string",
  "next": "one imperative sentence telling the listener what to do (start with you / check / try / open …)"
}}

Hard rules:
- Spoken length after these fields are joined should stay under about {max_words} words.
- Never include code, diffs, tables, or placeholders like "code block" / "code sample" / "N lines of".
- Never use full filesystem paths. Basenames only (e.g. daemon.py) or a short product name (Focus Audio).
- Never spell out slashes or dots in paths (no "dot py", no "tilde slash").
- Ignore any leftover agent routing / attribution banners if still present (structured
  "Routed:" lines, fenced routing JSON, intent/pack attribution objects). Do not mention
  that bookkeeping aloud.
- Prefer outcomes over exploration: what landed, what broke, what is next.
- Do not invent facts that are not in the source.
- If there is no clear next step, set "next" to "Nothing you need to do next."
- Keep each string plain prose suitable for text-to-speech.
- Spell symbols as words: arrows as "to"/"from", "==" as "equals", "!=" as "is not equal to",
  "&" as "and", "%" as "percent", key chords as "control shift M" (never leave raw glyphs).
"""

VERBATIM_SYSTEM = """You prepare an AI coding-agent reply for text-to-speech Read Aloud.

Rules:
- Output ONLY the spoken script. Preserve the author's meaning and order.
- Remove or replace content that sounds terrible spoken: code samples become a short phrase like "code sample in Python" (never read bodies or line counts); long URLs become "link"; tables become a one-sentence summary.
- Use basenames only for files (daemon.py), never full paths or slash-spelling.
- Drop any remaining agent routing banners (structured "Routed:" lines or routing JSON) —
  never read that bookkeeping aloud.
- Spell symbols as words so TTS never stalls: →/=> as "to"/"then", == as "equals",
  != as "is not equal to", & as "and", | as a pause, % as "percent",
  Ctrl+Shift+M as "control shift M", snake_case as spaced words (live verbatim).
- Keep important decisions and next steps.
- Light TTS tags OK: [pause] between major sections. No markdown headings or bullet glyphs.
- If the source is a list, keep each item as its own short sentence ending with a period
  (never run list items into one comma-joined phrase).
- Do not invent new facts. Do not dramatically shorten unless the source is mostly code or tables.
"""

# Optional prose fallback if the model ignores JSON (still structured).
_BRIEF_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _chat_complete(cfg: Config, system: str, user: str) -> str:
    api_key = cfg.api_key()
    if not api_key:
        raise RuntimeError(
            "xAI API key not found. Set your own key via "
            f"${cfg.api_key_env} or macOS Keychain service `xai-api-key` "
            '(account $USER). Example: security add-generic-password -a "$USER" '
            '-s "xai-api-key" -w "…". Run: focus-audio doctor'
        )

    url = cfg.api_base.rstrip("/") + "/chat/completions"
    body = {
        "model": cfg.model,
        "temperature": 0.4,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Chat API error {e.code}: {detail}") from e

    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Unexpected chat response: {payload!r}") from e
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("Empty brief from model")
    return text.strip()


def word_count(text: str) -> int:
    return len(text.split()) if text and text.strip() else 0


def should_skip_llm(cleaned_source: str, cfg: Config, mode: Optional[str] = None) -> bool:
    """True when the cleaned reply is short enough to speak without a rewrite call."""
    threshold = int(getattr(cfg, "skip_brief_words", 80) or 0)
    if threshold <= 0:
        return False
    return word_count(cleaned_source) <= threshold


def _ensure_sentence(text: str) -> str:
    t = " ".join((text or "").split()).strip()
    if not t:
        return ""
    if t[-1] not in ".!?":
        t += "."
    return t


def render_brief_struct(data: Dict[str, Any]) -> str:
    """Turn structured brief fields into a single spoken script."""
    parts: List[str] = []

    headline = _ensure_sentence(str(data.get("headline") or ""))
    if headline:
        parts.append(headline)

    changes = data.get("changes") or []
    if isinstance(changes, str):
        changes = [changes]
    if not isinstance(changes, list):
        changes = []
    for item in changes[:3]:
        s = _ensure_sentence(str(item or ""))
        if s:
            parts.append(s)

    caveat = _ensure_sentence(str(data.get("caveat") or ""))
    if caveat:
        parts.append(caveat)

    nxt = _ensure_sentence(str(data.get("next") or ""))
    if nxt:
        parts.append(nxt)

    return " ".join(parts).strip()


def _strip_code_fence_wrapper(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json|JSON)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def parse_brief_response(raw: str) -> Optional[Dict[str, Any]]:
    """Parse model output into a brief struct, or None if not valid JSON object."""
    if not raw or not raw.strip():
        return None
    text = _strip_code_fence_wrapper(raw)
    candidates = [text]
    m = _BRIEF_JSON_RE.search(text)
    if m:
        candidates.append(m.group(0))

    for cand in candidates:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        # Accept if at least one expected key is present.
        keys = {"headline", "changes", "caveat", "next"}
        if keys.intersection(data.keys()):
            return data
    return None


def finalize_brief_script(raw: str, *, max_words: int = 220) -> str:
    """Normalize chat model output into a speakable brief script.

    Prefers structured JSON → rendered speech; falls back to cleaned prose.
    """
    data = parse_brief_response(raw)
    if data is not None:
        script = render_brief_struct(data)
        if script:
            return _clamp_words(script, max_words)

    # Prose fallback: strip accidental markdown chrome, clamp length.
    prose = _strip_code_fence_wrapper(raw)
    prose = re.sub(r"(?m)^#{1,6}\s+", "", prose)
    prose = re.sub(r"(?m)^[ \t]*[-*+]\s+", "", prose)
    prose = " ".join(prose.split())
    return _clamp_words(prose, max_words)


def _clamp_words(text: str, max_words: int) -> str:
    words = text.split()
    if max_words > 0 and len(words) > max_words:
        return " ".join(words[:max_words]).rstrip(".,;:") + "."
    return text


def trim_source_for_brief(text: str, max_chars: int = 12000) -> str:
    """Cap model input: head + middle sample + tail (keeps decisions and wrap-up)."""
    if not text or len(text) <= max_chars:
        return text
    # 40% head, 20% mid, 40% tail
    head_n = int(max_chars * 0.4)
    mid_n = int(max_chars * 0.2)
    tail_n = max_chars - head_n - mid_n
    center = len(text) // 2
    mid_start = max(0, center - mid_n // 2)
    mid_end = min(len(text), mid_start + mid_n)
    return (
        text[:head_n]
        + "\n\n[… middle sample for briefing …]\n\n"
        + text[mid_start:mid_end]
        + "\n\n[… toward end …]\n\n"
        + text[-tail_n:]
    )


def synthesize_script(
    cleaned_source: str,
    cfg: Config,
    mode: Optional[str] = None,
    *,
    skip_llm: bool = False,
) -> str:
    """Return a TTS-ready spoken script for the given cleaned assistant text."""
    use_mode = (mode or cfg.mode or "brief").lower()

    # Live / forced local path: never wait on a rewrite model.
    if skip_llm:
        if use_mode == "verbatim":
            return fallback_script(cleaned_source, max_words=2000)
        return fallback_script(cleaned_source, max_words=cfg.max_brief_words)

    # Fast path: short replies skip the chat rewrite (biggest latency win).
    if should_skip_llm(cleaned_source, cfg, use_mode):
        if use_mode == "verbatim":
            return fallback_script(cleaned_source, max_words=400)
        return fallback_script(cleaned_source, max_words=cfg.max_brief_words)

    if use_mode == "verbatim":
        system = VERBATIM_SYSTEM
        source = trim_source_for_brief(cleaned_source, 12000)
        user = (
            "Prepare this agent reply for listening:\n\n---\n"
            + source
            + "\n---\n"
        )
        return _chat_complete(cfg, system, user)

    system = BRIEF_SYSTEM.format(max_words=cfg.max_brief_words)
    source = trim_source_for_brief(cleaned_source, 12000)
    user = (
        "Convert this agent reply into the focus-brief JSON object:\n\n---\n"
        + source
        + "\n---\n"
    )
    raw = _chat_complete(cfg, system, user)
    return finalize_brief_script(raw, max_words=cfg.max_brief_words)


def fallback_script(cleaned_source: str, max_words: int = 220) -> str:
    """Offline fallback: first N words after cleaning (no LLM)."""
    words = cleaned_source.split()
    if len(words) <= max_words:
        return cleaned_source
    return " ".join(words[:max_words]) + " … end of excerpt."


def split_for_tts(
    script: str,
    *,
    first_words: int = 35,
    chunk_words: int = 90,
) -> List[str]:
    """Split a spoken script into TTS chunks (first chunk small for fast start).

    Prefers sentence boundaries so prosody stays natural across chunk joins.
    """
    text = " ".join(script.split()).strip()
    if not text:
        return []

    total = word_count(text)
    # Single request when the whole script already fits the first-chunk budget.
    # (Tiny tails are also merged later so we don't pay an extra TTS call for crumbs.)
    if total <= first_words:
        return [text]

    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) <= 1:
        # No sentence breaks — fall back to word windows.
        words = text.split()
        chunks: List[str] = []
        i = 0
        target = max(8, first_words)
        while i < len(words):
            end = min(len(words), i + target)
            chunks.append(" ".join(words[i:end]))
            i = end
            target = max(20, chunk_words)
        return _merge_tiny_tail(chunks)

    chunks = []
    current: List[str] = []
    current_words = 0
    target = max(8, first_words)

    for part in parts:
        w = word_count(part)
        # Flush when adding this sentence would overshoot and we already have enough.
        if (
            current
            and current_words + w > target
            and current_words >= max(8, target // 2)
        ):
            chunks.append(" ".join(current))
            current = [part]
            current_words = w
            target = max(20, chunk_words)
        else:
            current.append(part)
            current_words += w

    if current:
        chunks.append(" ".join(current))

    return _merge_tiny_tail(chunks)


def _merge_tiny_tail(chunks: List[str], min_words: int = 12) -> List[str]:
    if len(chunks) >= 2 and word_count(chunks[-1]) < min_words:
        chunks[-2] = chunks[-2] + " " + chunks[-1]
        chunks.pop()
    return chunks
