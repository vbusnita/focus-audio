---
name: audio-mode
description: "audio: toggle brief/verbatim mode (Ctrl+Shift+M). /audio-mode"
allowed-tools: [Bash]
user-invocable: true
metadata:
  short-description: "Audio brief/verbatim (Ctrl+Shift+M)"
---

# Focus Audio — mode toggle

Toggle Focus Audio mode between brief and verbatim. The daemon **announces** the new mode and **re-speaks the last turn** in that mode.

Run this and report the JSON (mode, previous, rebrief, effective) in one short line:

```bash
ROOT="${GROK_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$HOME/.grok/plugins/focus-audio}}"
BIN="$ROOT/bin/focus-audio"
[ -x "$BIN" ] || BIN="$HOME/.grok/plugins/focus-audio/bin/focus-audio"
bash "$BIN" ensure >/dev/null 2>&1 || true
bash "$BIN" mode
```
