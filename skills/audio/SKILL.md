---
name: audio
description: "audio: Focus Audio help + hotkeys (Ctrl+Shift+Space play/pause, R restart, . skip, B rebrief, M mode). /audio"
allowed-tools: [Bash]
user-invocable: true
metadata:
  short-description: "Focus Audio help + hotkeys (Ctrl+Shift+Space/R/./B/M)"
---

# Focus Audio — help and status

Show Focus Audio playback shortcuts and current daemon status. Run these shell commands and paste the combined output back to the user as a short card (no extra prose):

```bash
ROOT="${GROK_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$HOME/.grok/plugins/focus-audio}}"
BIN="$ROOT/bin/focus-audio"
if [ ! -x "$BIN" ]; then BIN="$HOME/.grok/plugins/focus-audio/bin/focus-audio"; fi

echo "Focus Audio — playback controls"
echo ""
echo "  Global hotkeys (daemon + Accessibility):"
echo "    Ctrl+Shift+Space   play / pause"
echo "    Ctrl+Shift+R       restart from start"
echo "    Ctrl+Shift+.       skip / stop"
echo "    Ctrl+Shift+B       re-brief last turn"
echo "    Ctrl+Shift+M       toggle brief ↔ verbatim (announce + re-speak)"
echo ""
echo "  Slash commands (type /audio… — Grok palette: ? or Ctrl+P if not stolen by VS Code):"
echo "    /audio             this help + status"
echo "    /audio-toggle      play/pause              (Ctrl+Shift+Space)"
echo "    /audio-restart     restart                 (Ctrl+Shift+R)"
echo "    /audio-skip        skip                    (Ctrl+Shift+.)"
echo "    /audio-rebrief     re-brief                (Ctrl+Shift+B)"
echo "    /audio-mode        toggle brief/verbatim   (Ctrl+Shift+M)"
echo "    /audio-live        toggle live mid-turn speech"
echo "    /audio-off         master OFF (silence everything)"
echo "    /audio-on          master ON (re-enable after off)"
echo "    /listen            speak last assistant turn"
echo ""
echo "  CLI equivalents:"
echo "    focus-audio live [on|off]    focus-audio power [on|off]"
echo "    focus-audio off | on"
echo ""
echo "  Note: Grok shortcuts (Ctrl+. / Ctrl+X) are built-in only. In VS Code, Ctrl+P is host Quick Open unless keybindings free it for the terminal."
echo "  Global chords work system-wide when the Focus Audio daemon is running."
echo ""
if [ -x "$BIN" ]; then
  "$BIN" status 2>/dev/null || echo "Daemon not running — try: $BIN ensure -v"
else
  echo "focus-audio binary missing — install/enable the focus-audio plugin"
fi
```

If the daemon is not running and the binary exists, also run `bash "$BIN" ensure -v` once, then re-print status.
