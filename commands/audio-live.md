---
name: audio-live
description: "audio: toggle live mid-turn speech (live_verbatim). /audio-live"
allowed-tools: [Bash]
user-invocable: true
metadata:
  short-description: "Toggle live mid-turn speech"
---

# Focus Audio — live mid-turn toggle

Toggle experimental **live_verbatim** (speak agent chunks mid-turn from `updates.jsonl`).

Run this and report the JSON in one short line (`live_verbatim`, `previous`, `effective`):

```bash
ROOT="${GROK_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$HOME/.grok/plugins/focus-audio}}"
BIN="$ROOT/bin/focus-audio"
[ -x "$BIN" ] || BIN="$HOME/.grok/plugins/focus-audio/bin/focus-audio"
bash "$BIN" live
```

Optional explicit state (if the user said on/off):

```bash
bash "$BIN" live on    # or: live off
```

Notes:
- Live needs Focus Audio **enabled** (`/audio-on` if master was off).
- Turning live **off** stops any in-flight mid-turn speech.
- Takes effect on the **next** user prompt (UserPromptSubmit → live-start).
