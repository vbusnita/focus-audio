---
name: audio-skip
description: "audio: skip/stop Focus Audio (Ctrl+Shift+.). /audio-skip"
allowed-tools: [Bash]
user-invocable: true
metadata:
  short-description: "Audio skip/stop (Ctrl+Shift+.)"
---

# Focus Audio — skip/stop

Skip / stop the current Focus Audio job. Run this and report the JSON result in one short line:

```bash
ROOT="${GROK_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$HOME/.grok/plugins/focus-audio}}"
BIN="$ROOT/bin/focus-audio"
[ -x "$BIN" ] || BIN="$HOME/.grok/plugins/focus-audio/bin/focus-audio"
[ -x "$BIN" ] || BIN="$HOME/.grok/plugins/focus-audio/bin/focus-audio"
bash "$BIN" ensure >/dev/null 2>&1 || true
bash "$BIN" skip
```
