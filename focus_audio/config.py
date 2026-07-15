"""Config load/save for Focus Audio (simple TOML subset)."""

from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Dict, Optional

from .paths import config_path, secure_write_text


DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    # Single global TTS voice today. Future: per-session / per-agent voices so
    # parallel agents are distinguishable by ear (see README → Future improvements).
    "voice_id": "ara",
    "speed": 1.1,
    "language": "en",
    "mode": "brief",  # brief | verbatim
    "autoplay": True,
    "min_chars": 80,
    "max_brief_words": 220,
    # Skip chat rewrite when cleaned source is already this short (words).
    "skip_brief_words": 80,
    # Stream TTS: synth first chunk ASAP, play while remaining chunks generate.
    "chunk_tts": True,
    "first_chunk_words": 35,
    "chunk_words": 90,
    # Slightly lower bitrate = smaller download / faster time-to-first-audio.
    "tts_bit_rate": 96000,
    "model": "grok-4-1-fast-non-reasoning",
    "api_key_env": "XAI_API_KEY",
    "api_base": "https://api.x.ai/v1",
    "chime": True,
    "hotkeys": True,
    # Experimental: speak agent_message_chunk events mid-turn from updates.jsonl.
    "live_verbatim": False,
    "live_min_chars": 40,
    "live_poll_ms": 150,
    # When live spoke at least one segment this turn, skip the immediate Stop-hook brief.
    "live_skip_stop_brief": True,
    # After live finishes, optionally play a *brief* recap (second pass).
    # Default off. When on, still skips if mode=verbatim, brief rewrite was
    # skipped (same short text), or live already covered most of the reply.
    "live_then_brief": False,
}


@dataclass
class Config:
    enabled: bool = True
    voice_id: str = "ara"
    speed: float = 1.1
    language: str = "en"
    mode: str = "brief"
    autoplay: bool = True
    min_chars: int = 80
    max_brief_words: int = 220
    skip_brief_words: int = 80
    chunk_tts: bool = True
    first_chunk_words: int = 35
    chunk_words: int = 90
    tts_bit_rate: int = 96000
    model: str = "grok-4-1-fast-non-reasoning"
    api_key_env: str = "XAI_API_KEY"
    api_base: str = "https://api.x.ai/v1"
    chime: bool = True
    hotkeys: bool = True
    live_verbatim: bool = False
    live_min_chars: int = 40
    live_poll_ms: int = 150
    live_skip_stop_brief: bool = True
    live_then_brief: bool = False

    def api_key(self) -> Optional[str]:
        """Resolve user API key (Keychain or env); never read/write secrets from config.toml."""
        from .secrets import get_api_key

        return get_api_key(self.api_key_env)

    def api_key_source(self) -> str:
        from .secrets import api_key_source

        return api_key_source(self.api_key_env)

    def toggle_mode(self) -> str:
        self.mode = "verbatim" if self.mode == "brief" else "brief"
        return self.mode


def _parse_value(raw: str) -> Any:
    s = raw.strip()
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    if s.startswith("'") and s.endswith("'"):
        return s[1:-1]
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return s


def _load_toml_flat(path: Path) -> Dict[str, Any]:
    """Parse a flat key = value TOML file (no tables needed for v1)."""
    out: Dict[str, Any] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = _parse_value(val)
    return out


def _dump_toml_flat(data: Dict[str, Any]) -> str:
    lines = [
        "# Focus Audio config — edit freely; restart daemon to pick up most changes.",
        "# mode: brief | verbatim  (Ctrl+Shift+M re-speaks last turn in the new mode)",
        "# live_verbatim: mid-turn speech",
        "# live_then_brief: after live, also play mode (opt-in second pass; off by default)",
        "",
    ]
    for key, value in data.items():
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key} = {value}")
        else:
            escaped = str(value).replace('"', '\\"')
            lines.append(f'{key} = "{escaped}"')
    lines.append("")
    return "\n".join(lines)


def load_config(path: Optional[Path] = None) -> Config:
    p = path or config_path()
    merged = deepcopy(DEFAULTS)
    if p.is_file():
        merged.update(_load_toml_flat(p))
    # Env overrides
    if os.environ.get("FOCUS_AUDIO", "").strip() in ("0", "false", "off", "no"):
        merged["enabled"] = False
    mode_env = os.environ.get("FOCUS_AUDIO_MODE", "").strip().lower()
    if mode_env in ("brief", "verbatim"):
        merged["mode"] = mode_env

    known = {f.name for f in fields(Config)}
    kwargs = {k: v for k, v in merged.items() if k in known}
    return Config(**kwargs)


def save_config(cfg: Config, path: Optional[Path] = None) -> Path:
    p = path or config_path()
    secure_write_text(p, _dump_toml_flat(asdict(cfg)))
    return p


def ensure_default_config() -> Config:
    p = config_path()
    if not p.is_file():
        cfg = Config()
        save_config(cfg, p)
        return cfg
    return load_config(p)
