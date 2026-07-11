"""Synthesize spoken scripts from assistant turns (brief or verbatim)."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import List, Optional

from .config import Config

BRIEF_SYSTEM = """You rewrite AI coding-agent replies into a spoken focus brief for someone who struggles to read long on-screen text.

Rules:
- Output ONLY the spoken script. No title, no markdown headings, no bullet markers.
- Length: roughly {max_words} words max (about 45–90 seconds spoken).
- Structure naturally as speech: what happened → what changed (files/symbols if useful) → what they should do next.
- NEVER read code line-by-line. Summarize intent of code/diffs. Code fences appear as placeholders like "[code block: ~40 lines of Python]" — mention purpose, not contents.
- Skip tool chrome, raw paths spam, and filler.
- Use short sentences. Occasional TTS tags are OK: [pause] for a beat. Do not overuse tags.
- Speak to "you" (the developer listening).
- If the reply is already short and clear, tighten it slightly but keep the meaning.
"""

VERBATIM_SYSTEM = """You prepare an AI coding-agent reply for text-to-speech Read Aloud.

Rules:
- Output ONLY the spoken script. Preserve the author's meaning and order.
- Remove or replace content that sounds terrible spoken: long code blocks become one short phrase like "code block, about N lines of Python"; long URLs become "link"; tables become a one-sentence summary.
- Keep important file names, decisions, and next steps.
- Light TTS tags OK: [pause] between major sections. No markdown headings or bullets.
- Do not invent new facts. Do not dramatically shorten unless the source is mostly code.
"""


def _chat_complete(cfg: Config, system: str, user: str) -> str:
    api_key = cfg.api_key()
    if not api_key:
        raise RuntimeError(
            "xAI API key not found. Focus Audio reuses ara-agent's Keychain entry "
            f"(service `xai-api-key`) or ${cfg.api_key_env}. "
            "Store with: security add-generic-password -a \"$USER\" -s \"xai-api-key\" -w \"…\""
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
        user = (
            "Prepare this agent reply for listening:\n\n---\n"
            + cleaned_source
            + "\n---\n"
        )
    else:
        system = BRIEF_SYSTEM.format(max_words=cfg.max_brief_words)
        user = (
            "Rewrite this agent reply into a focus brief:\n\n---\n"
            + cleaned_source
            + "\n---\n"
        )

    # Hard cap input size for cost; head + tail keeps start/end context
    max_in = 12000
    if len(cleaned_source) > max_in:
        half = max_in // 2
        user = user.replace(
            cleaned_source,
            cleaned_source[:half]
            + "\n\n[… middle truncated for briefing …]\n\n"
            + cleaned_source[-half:],
        )

    return _chat_complete(cfg, system, user)


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
