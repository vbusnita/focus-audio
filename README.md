# Focus Audio

Smart **read-aloud companion** for [Grok Build](https://x.ai). When an agent turn finishes, Focus Audio rewrites the reply into a **spoken focus brief** (or cleaned **verbatim** audio) using **your** xAI chat + TTS keys — so you can listen instead of staring at a wall of text.

**Requirements:** macOS (primary), [Grok Build](https://x.ai), Python 3.9+, an [xAI API key](https://console.x.ai/) with TTS access.

## Why

Grok Build has voice **input**, not spoken agent replies. This plugin fills that gap:

- **Brief mode (default):** what happened → what changed → what to do next (~45–90s)
- **Verbatim mode:** cleaned full reply; code dumps become short placeholders
- **Live mid-turn speech (optional):** hear agent chunks while the turn is still running
- **Mode toggle re-speaks:** `Ctrl+Shift+M` announces the new mode, then re-synthesizes the last turn
- Global **play / pause / restart / skip / rebrief / mode** controls
- Master **on / off** (`/audio-on`, `/audio-off`)
- Content-hash **cache** so restart is free
- **Harness noise silence:** never speaks agent `Routed:` lines or `harness-signal` JSON blocks
- Hooks are **fail-open** — audio never blocks coding

## Install

```bash
# 1. Clone and install as a trusted Grok plugin
git clone https://github.com/vbusnita/focus-audio.git
cd focus-audio
chmod +x bin/focus-audio
grok plugin install . --trust
grok plugin list

# Optional: put CLI on PATH
mkdir -p ~/.local/bin
ln -sf "$(pwd)/bin/focus-audio" ~/.local/bin/focus-audio

# 2. Your xAI API key (Focus Audio never stores it in its config)
export XAI_API_KEY="xai-…"
# or, once on macOS Keychain (recommended):
# security add-generic-password -a "$USER" -s "xai-api-key" -w "xai-…"

# 3. Sanity check (prints no secrets)
focus-audio doctor

# 4. Optional global hotkeys
pip3 install --user pynput
# Then: System Settings → Privacy & Security → Accessibility → allow Python/Terminal
```

Open a **new** Grok Build session after install. SessionStart starts the daemon automatically.

```bash
# Already had Grok open? Either start a new session, or:
focus-audio ensure -v
focus-audio status
```

Smoke test without a session:

```bash
echo "We fixed login and added tests. Next deploy staging." | focus-audio speak -
```

### Update

```bash
cd focus-audio && git pull
grok plugin install . --trust
# or: grok plugin update focus-audio
```

### Disable

```bash
focus-audio off              # master silence (config)
# or
export FOCUS_AUDIO=0         # process env kill-switch
# or
grok plugin disable focus-audio
```

## API key (your credentials only)

Focus Audio **never** writes the key to `config.toml`, cache, or logs.

| Priority | Source |
|----------|--------|
| 1 | macOS Keychain service `xai-api-key`, account `$USER` |
| 2 | Env `XAI_API_KEY` (or `api_key_env` in config) |

Verify without revealing the secret:

```bash
focus-audio doctor
# or
focus-audio config --show
# → api_key_present: true
# → api_key_source: keychain:… or env:XAI_API_KEY
```

You pay for **your** xAI chat + TTS usage. There is no shared key and no account of ours involved.

## Lifecycle

| Grok event | Focus Audio |
|------------|-------------|
| Session starts | `ensure` — refcount +1, spawn daemon if needed |
| User submits prompt | `live-start` — if `live_verbatim`, tail `updates.jsonl` |
| Turn ends | `enqueue` — synthesize + play (skipped if live already covered) |
| Session ends | `release` — refcount −1; stop daemon when last session exits |

Multiple Grok windows share one daemon. If Grok crashes without SessionEnd:

```bash
focus-audio shutdown --clear-refs
```

In the Grok TUI: `/hooks` → confirm **focus-audio** hooks are listed. You may need `/hooks-trust` once.

## Usage

| Command | Purpose |
|---------|---------|
| `focus-audio doctor` | Install + key + daemon health check |
| `focus-audio ensure` | SessionStart — start daemon + register session |
| `focus-audio release` | SessionEnd — drop ref; stop if last session |
| `focus-audio speak -` | Speak stdin as brief |
| `focus-audio speak-session <id>` | Speak last assistant turn of a session |
| `focus-audio toggle` / `pause` / `restart` / `skip` | Playback control |
| `focus-audio mode [brief\|verbatim]` | Switch mode, announce, re-speak last turn |
| `focus-audio live [on\|off]` | Mid-turn speech |
| `focus-audio on` / `off` | Master power |
| `focus-audio rebrief` | Force regenerate last job |
| `focus-audio status` | Daemon + lifecycle refs |
| `focus-audio config` | Show/edit `~/.grok/focus-audio/config.toml` |
| `focus-audio shutdown [--clear-refs]` | Stop daemon |

In Grok Build, the **Stop** hook enqueues automatically. Manual: **`/listen`**.

### Hotkeys & slash commands

| Shortcut | Action | Slash |
|----------|--------|-------|
| `Ctrl+Shift+Space` | Play / pause | `/audio-toggle` |
| `Ctrl+Shift+R` | Restart clip | `/audio-restart` |
| `Ctrl+Shift+.` | Skip / stop | `/audio-skip` |
| `Ctrl+Shift+B` | Re-brief | `/audio-rebrief` |
| `Ctrl+Shift+M` | Toggle brief ↔ verbatim | `/audio-mode` |
| — | Live mid-turn on/off | `/audio-live` |
| — | Master off / on | `/audio-off` · `/audio-on` |
| — | Speak last turn | `/listen` |
| — | Help + status | `/audio` |

**Note:** Grok’s built-in cheatsheet does not list plugin bindings. Use slash commands or global hotkeys.

## Config

`~/.grok/focus-audio/config.toml` — settings only, **no secrets**:

```toml
enabled = true
voice_id = "ara"
speed = 1.1
language = "en"
mode = "brief"              # brief | verbatim
autoplay = true
min_chars = 80
max_brief_words = 220
skip_brief_words = 80
chunk_tts = true
first_chunk_words = 35
chunk_words = 90
tts_bit_rate = 96000
model = "grok-4-1-fast-non-reasoning"
chime = true
hotkeys = true
live_verbatim = false
live_min_chars = 40
live_poll_ms = 150
live_skip_stop_brief = true
live_then_brief = true
```

### Mode vs live

| Setting | What you hear |
|---------|----------------|
| `mode = brief` | Post-turn structured focus brief |
| `mode = verbatim` | Cleaned full reply |
| `live_verbatim = true` | Mid-turn agent chunks |
| `live` + `mode = brief` + `live_then_brief` | Live, then a short brief after the turn |
| `live` + `mode = verbatim` | Live only (no double full read) |

`focus-audio status` reports **`effective`**: `brief`, `verbatim`, `live_verbatim`, `live+brief`, or `off`.

### Live mid-turn

```bash
focus-audio live on    # or /audio-live in Grok
```

Tails `updates.jsonl` and speaks **agent message** chunks as Grok flushes them (not token-level streaming). The speakable pre-pass drops code dumps, long paths, and agent routing metadata (`Routed:`, `harness-signal`) so TTS stays listenable.

## Layout

| Path | Role |
|------|------|
| Install / clone | This package (`plugin.json`, hooks, skills) |
| `~/.grok/plugins/focus-audio/` | Typical Grok install location |
| `~/.grok/focus-audio/` | Runtime: config, cache, socket, logs |

## Tests

```bash
python3 -m pytest tests/ -q
```

## License

[MIT](LICENSE) — use it, fork it, ship it. Bring **your** API key.

## Future ideas

- Per-session / per-agent TTS voices so parallel agents sound distinct
- Linux / Windows playback backends
- Optional macOS app bundle for a stable Accessibility identity (hotkeys)

## Notes

- macOS first via **AVAudioPlayer** (true mid-file pause/resume); `afplay` fallback
- New turns cancel in-flight synthesis so you hear the latest state
- Optional dependency: `pynput` for global hotkeys only
