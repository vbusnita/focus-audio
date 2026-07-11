---
name: audio-rebrief
description: "audio: re-brief last turn (Ctrl+Shift+B). /audio-rebrief"
allowed-tools: [Bash]
user-invocable: true
metadata:
  short-description: "Audio re-brief (Ctrl+Shift+B)"
---

# Focus Audio — re-brief

Force re-synthesize the last Focus Audio brief. Run this and report the JSON result in one short line:

```bash
ROOT="${GROK_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$HOME/.grok/plugins/focus-audio}}"
BIN="$ROOT/bin/focus-audio"
[ -x "$BIN" ] || BIN="$HOME/.grok/plugins/focus-audio/bin/focus-audio"
[ -x "$BIN" ] || BIN="$HOME/.grok/plugins/focus-audio/bin/focus-audio"
bash "$BIN" ensure >/dev/null 2>&1 || true
bash "$BIN" rebrief
```
