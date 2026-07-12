---
name: audio-off
description: "audio: master OFF — silence live + brief + verbatim. /audio-off"
allowed-tools: [Bash]
user-invocable: true
metadata:
  short-description: "Master mute: all Focus Audio off"
---

# Focus Audio — master OFF

Turn Focus Audio **completely off**: no live mid-turn speech, no end-of-turn brief, no verbatim. Stops any clip playing now. Preference is saved in `config.toml` (`enabled = false`).

Run this and report the JSON in one short line (`enabled`, `note`):

```bash
ROOT="${GROK_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$HOME/.grok/plugins/focus-audio}}"
BIN="$ROOT/bin/focus-audio"
[ -x "$BIN" ] || BIN="$HOME/.grok/plugins/focus-audio/bin/focus-audio"
[ -x "$BIN" ] || BIN="$HOME/.grok/plugins/focus-audio/bin/focus-audio"
bash "$BIN" off
```

To re-enable later: `/audio-on` (or `focus-audio on`).
