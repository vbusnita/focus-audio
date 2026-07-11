---
name: audio-toggle
description: "audio: play/pause Focus Audio (Ctrl+Shift+Space). /audio-toggle"
allowed-tools: [Bash]
user-invocable: true
metadata:
  short-description: "Audio play/pause (Ctrl+Shift+Space)"
---

# Focus Audio — play/pause

Toggle Focus Audio play/pause immediately. Run this and report the JSON result in one short line (ok/status or error):

```bash
ROOT="${GROK_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$HOME/.grok/plugins/focus-audio}}"
BIN="$ROOT/bin/focus-audio"
[ -x "$BIN" ] || BIN="$HOME/.grok/plugins/focus-audio/bin/focus-audio"
[ -x "$BIN" ] || BIN="$HOME/token-tracker/plugins/focus-audio/bin/focus-audio"
bash "$BIN" ensure >/dev/null 2>&1 || true
bash "$BIN" toggle
```
