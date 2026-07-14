"""Best-effort redaction of credential-like strings before chat/TTS.

This is a safety net — not a guarantee. Prefer keeping secrets out of agent
output entirely. Patterns intentionally require high-entropy-looking values
so normal prose (e.g. "xai-api-key" service name) is not mangled.
"""

from __future__ import annotations

import re
from typing import List, Pattern, Tuple

# Speakable placeholder (TTS-friendly).
PLACEHOLDER = "redacted secret"

# (name, pattern) — applied in order.
_PATTERNS: List[Tuple[str, Pattern[str]]] = [
    (
        "pem_private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
            r"[\s\S]*?"
            r"-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
            re.MULTILINE,
        ),
    ),
    (
        "xai_key",
        re.compile(r"\bxai-[A-Za-z0-9_\-]{20,}\b"),
    ),
    (
        "openai_style_key",
        re.compile(r"\bsk-(?:proj-|live-|test-)?[A-Za-z0-9_\-]{20,}\b"),
    ),
    (
        "github_pat",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    ),
    (
        "github_fine_grained",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    ),
    (
        "aws_access_key",
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ),
    (
        "bearer_token",
        re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b", re.IGNORECASE),
    ),
    (
        "assignment_secret",
        re.compile(
            r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|password|passwd|pwd)"
            r"(\s*[=:]\s*)([\"']?)([^\s\"']{12,})(\3)"
        ),
    ),
]


def redact_secrets(text: str) -> str:
    """Replace credential-like substrings with a speakable placeholder."""
    if not text:
        return text
    out = text
    for name, pat in _PATTERNS:
        if name == "assignment_secret":
            out = pat.sub(
                lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{PLACEHOLDER}{m.group(3)}",
                out,
            )
        elif name == "bearer_token":
            out = pat.sub(f"Bearer {PLACEHOLDER}", out)
        else:
            out = pat.sub(PLACEHOLDER, out)
    return out
