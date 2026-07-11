---
name: listen
description: "audio: speak last assistant turn as Focus Audio brief. /listen. Hotkeys: Ctrl+Shift+Space"
user-invocable: true
allowed-tools: [Bash]
metadata:
  short-description: "Speak last reply — then Ctrl+Shift+Space/R/./B/M"
---

# Listen (Focus Audio)

Enqueue the current session's last assistant reply for smart audio playback.

## Steps

1. Run this command (do not invent another path):

```bash
ROOT="${GROK_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$HOME/.grok/plugins/focus-audio}}"
BIN="$ROOT/bin/focus-audio"
[ -x "$BIN" ] || BIN="$HOME/.grok/plugins/focus-audio/bin/focus-audio"
[ -x "$BIN" ] || BIN="$HOME/.grok/plugins/focus-audio/bin/focus-audio"
bash "$BIN" ensure >/dev/null 2>&1 || true
bash "$BIN" enqueue --verbose
```

2. Tell the user briefly:
   - Synthesis started (or was queued)
   - **Ctrl+Shift+Space** play/pause
   - **Ctrl+Shift+R** restart
   - **Ctrl+Shift+.** skip
   - **Ctrl+Shift+B** re-brief
   - **Ctrl+Shift+M** toggle brief/verbatim (announce + re-speak last turn)
   - Slash help: `/audio` (type `/audio` or open Ctrl+P and search `audio`)
   - Last script: `~/.grok/focus-audio/last_brief.md`

3. If the daemon fails to start, suggest:

```bash
~/.grok/plugins/focus-audio/bin/focus-audio ensure -v
```

and check the API key (`focus-audio config --show` → `api_key_present`).
