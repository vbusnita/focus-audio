---
name: audio-restart
description: "audio: restart Focus Audio clip (Ctrl+Shift+R). /audio-restart"
allowed-tools: [Bash]
user-invocable: true
metadata:
  short-description: "Audio restart (Ctrl+Shift+R)"
---

# Focus Audio — restart

Restart the current Focus Audio clip from the beginning. Run this and report the JSON result in one short line:

```bash
ROOT="${GROK_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$HOME/.grok/plugins/focus-audio}}"
BIN="$ROOT/bin/focus-audio"
[ -x "$BIN" ] || BIN="$HOME/.grok/plugins/focus-audio/bin/focus-audio"
[ -x "$BIN" ] || BIN="$HOME/.grok/plugins/focus-audio/bin/focus-audio"
bash "$BIN" ensure >/dev/null 2>&1 || true
bash "$BIN" restart
```
