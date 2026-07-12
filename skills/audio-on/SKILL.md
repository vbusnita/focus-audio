---
name: audio-on
description: "audio: master ON — re-enable Focus Audio after /audio-off. /audio-on"
allowed-tools: [Bash]
user-invocable: true
metadata:
  short-description: "Master unmute: Focus Audio on"
---

# Focus Audio — master ON

Re-enable Focus Audio after `/audio-off`. Restores hooks for brief/verbatim (and live if `live_verbatim` was already on).

Run this and report the JSON in one short line (`enabled`, `live_verbatim`, `mode`, `note`):

```bash
ROOT="${GROK_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$HOME/.grok/plugins/focus-audio}}"
BIN="$ROOT/bin/focus-audio"
[ -x "$BIN" ] || BIN="$HOME/.grok/plugins/focus-audio/bin/focus-audio"
[ -x "$BIN" ] || BIN="$HOME/.grok/plugins/focus-audio/bin/focus-audio"
bash "$BIN" on
```

Does **not** change mode or live preference — only the master `enabled` flag.
