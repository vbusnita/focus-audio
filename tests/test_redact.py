"""Tests for credential redaction before chat/TTS."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from focus_audio.redact import PLACEHOLDER, redact_secrets  # noqa: E402


def test_redacts_xai_key():
    key = "xai-" + ("a" * 40)
    out = redact_secrets(f"use {key} please")
    assert key not in out
    assert PLACEHOLDER in out


def test_redacts_openai_style_and_github():
    sk = "sk-" + ("b" * 48)
    ghp = "ghp_" + ("c" * 36)
    out = redact_secrets(f"keys {sk} and {ghp}")
    assert sk not in out
    assert ghp not in out
    assert out.count(PLACEHOLDER) >= 2


def test_redacts_pem_block():
    pem = (
        "-----BEGIN PRIVATE KEY-----\n"
        "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7\n"
        "-----END PRIVATE KEY-----"
    )
    out = redact_secrets(f"here\n{pem}\ndone")
    assert "BEGIN PRIVATE KEY" not in out
    assert PLACEHOLDER in out


def test_redacts_assignment_and_bearer():
    out = redact_secrets(
        'api_key="supersecrettokenvalue" and Bearer abcdefghijklmnop.qrstuv'
    )
    assert "supersecrettokenvalue" not in out
    assert "abcdefghijklmnop.qrstuv" not in out
    assert PLACEHOLDER in out


def test_preserves_service_name_and_prose():
    # Short / non-key shapes should survive (Keychain service name, docs).
    src = "Store in Keychain service xai-api-key for account $USER."
    assert redact_secrets(src) == src


def test_empty_passthrough():
    assert redact_secrets("") == ""
