"""Resolve the xAI API key the same way ara-agent does — Keychain first, never store it.

ara-agent stores the key in macOS Keychain:
  service: xai-api-key
  account: $USER
  via: keyring.get_password("xai-api-key", getpass.getuser())

Focus Audio reuses that entry. We never write the key to config, cache, or logs.
"""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
from typing import Optional

# Same service name as ara-agent (voice_agent.get_api_key)
KEYCHAIN_SERVICE = "xai-api-key"


def _from_keyring() -> Optional[str]:
    try:
        import keyring
    except ImportError:
        return None
    try:
        key = keyring.get_password(KEYCHAIN_SERVICE, getpass.getuser())
        if key and key.strip():
            return key.strip()
    except Exception:
        return None
    return None


def _from_security_cli() -> Optional[str]:
    """macOS Keychain without the keyring package (stdlib + security)."""
    if not shutil.which("security"):
        return None
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                getpass.getuser(),
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        return None
    return None


def _from_env(api_key_env: str = "XAI_API_KEY") -> Optional[str]:
    for name in (api_key_env, "XAI_API_KEY"):
        if not name:
            continue
        val = os.environ.get(name)
        if val and val.strip():
            return val.strip()
    return None


def get_api_key(api_key_env: str = "XAI_API_KEY") -> Optional[str]:
    """Return the xAI API key, or None if not found.

    Lookup order (matches ara-agent, with a pure-CLI Keychain fallback):
      1. macOS Keychain via keyring (service ``xai-api-key``)
      2. macOS Keychain via ``security`` CLI
      3. Environment variable (``XAI_API_KEY`` / configured name)
    """
    return _from_keyring() or _from_security_cli() or _from_env(api_key_env)


def api_key_source(api_key_env: str = "XAI_API_KEY") -> str:
    """Human-readable source label for status (never includes the secret)."""
    if _from_keyring():
        return f"keychain:{KEYCHAIN_SERVICE} (keyring)"
    if _from_security_cli():
        return f"keychain:{KEYCHAIN_SERVICE} (security)"
    if _from_env(api_key_env):
        return f"env:{api_key_env}"
    return "missing"
