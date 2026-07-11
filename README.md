# Focus Audio

Smart **read-aloud companion** for [Grok Build](https://x.ai). When an agent turn finishes, Focus Audio rewrites the reply into a **spoken focus brief** (or cleaned **verbatim** audio) using xAI chat + TTS — so you can listen instead of staring at a wall of text.

## Why

Grok Build has voice **input**, not spoken agent replies. This plugin fills that gap with audio optimized for focus:

- **Brief mode (default):** what happened → what changed → what to do next (~45–90s)
- **Verbatim mode:** cleaned full reply, code dumps replaced with short placeholders
- **Mode toggle re-speaks:** `Ctrl+Shift+M` announces the new mode, then re-synthesizes the last turn
- Global **play / pause / restart / skip / rebrief / mode** controls
- Content-hash **cache** so restart is free
- **Fast start:** short replies skip the rewrite model; longer ones stream TTS in chunks
- **Experimental live verbatim:** speak mid-turn chunks; with **`live_then_brief`** (default on) still play a post-turn brief after live finishes

## Install

```bash
# 1. Clone (private) and install as a trusted Grok plugin
git clone git@github.com:vbusnita/focus-audio.git
cd focus-audio
chmod +x bin/focus-audio
grok plugin install . --trust
grok plugin list
grok plugin details focus-audio

# 2. API key — reuses ara-agent's macOS Keychain entry (nothing new to store)
#    service: xai-api-key  ·  account: $USER
#    Optional override only: export XAI_API_KEY=...
#    Check: focus-audio config --show  → api_key_source / api_key_present

# 3. (Optional) global hotkeys — needs Accessibility permission on macOS
pip3 install --user pynput

# 4. Daemon lifecycle is automatic — no need to start manually:
#    SessionStart → ensure (start daemon)
#    SessionEnd   → release (stop if last Grok session)
#    Stop         → enqueue spoken brief
```

After editing the plugin source, reinstall or update:

```bash
grok plugin install . --trust
# or
grok plugin update focus-audio
```

For live development you can also symlink into `~/.grok/plugins/focus-audio` (user plugin dir); the CLI install path above is the packaged, supported flow.

### Lifecycle (gold standard)

| Grok event | Focus Audio |
|------------|-------------|
| Session starts | `ensure` — refcount +1, spawn daemon if needed |
| User submits prompt | `live-start` — if `live_verbatim`, tail `updates.jsonl` |
| Turn ends | `enqueue` — synthesize + play brief (skipped if live already covered) |
| Session ends / quit | `release` — refcount −1; **stop daemon when last session exits** |

Multiple Grok windows share one daemon; only the last quit tears it down. Idle cost is tiny (Unix socket + accept loop). If Grok crashes without `SessionEnd`, clean up with:

```bash
focus-audio shutdown --clear-refs
```

Disable anytime:

```bash
export FOCUS_AUDIO=0
# or
./plugins/focus-audio/bin/focus-audio config --autoplay false
# or
grok plugin disable focus-audio
```

## How to start (quick)

**Option A — automatic (preferred)**  
Open a **new** Grok Build session after the plugin is installed.  
`SessionStart` runs `ensure` and the daemon comes up by itself.

If this session was already open when you installed the plugin, auto-start never ran for it. Either:

```bash
focus-audio ensure -v
```

…or start a fresh Grok session (`/new` or quit and relaunch).

**Option B — manual**

```bash
focus-audio ensure -v          # start daemon + register a session ref
focus-audio status             # should show daemon.ok = true
# optional smoke test:
echo "We fixed login.py and added tests. Next deploy staging." | focus-audio speak -
```

CLI path (if not already on PATH):

```bash
# already linked for this machine:
~/.local/bin/focus-audio
# or
~/token-tracker/plugins/focus-audio/bin/focus-audio
```

In the Grok TUI: `/hooks` → confirm **focus-audio** SessionStart / SessionEnd / Stop are listed and enabled. Project may need `/hooks-trust` once.

## Usage

| Command | Purpose |
|---------|---------|
| `focus-audio ensure` | SessionStart — start daemon + register session |
| `focus-audio release` | SessionEnd — drop ref; stop if last session |
| `focus-audio daemon` | Run player + IPC + hotkeys (foreground) |
| `focus-audio enqueue` | Stop hook — queue current session turn |
| `focus-audio speak -` | Speak stdin as brief |
| `focus-audio speak-session <id>` | Speak last assistant turn of a session |
| `focus-audio toggle` / `pause` / `restart` / `skip` | Playback control |
| `focus-audio mode [brief\|verbatim]` | Switch/toggle mode, speak confirmation, re-speak last turn |
| `focus-audio rebrief` | Force regenerate last job |
| `focus-audio status` | Daemon + lifecycle refs |
| `focus-audio shutdown [--clear-refs]` | Stop daemon (optional orphan cleanup) |
| `focus-audio config` | Show/edit `~/.grok/focus-audio/config.toml` |

In Grok Build, after each turn the **Stop** hook enqueues automatically. Manual: skill **`/listen`**.

### Hotkeys (daemon + `pynput` + Accessibility permission)

| Shortcut | Action | Slash command |
|----------|--------|---------------|
| `Ctrl+Shift+Space` | Play / pause | `/audio-toggle` |
| `Ctrl+Shift+R` | Restart current clip | `/audio-restart` |
| `Ctrl+Shift+.` | Skip / stop | `/audio-skip` |
| `Ctrl+Shift+B` | Re-brief last turn | `/audio-rebrief` |
| `Ctrl+Shift+M` | Toggle brief ↔ verbatim (announce + re-speak last turn) | `/audio-mode` |
| — | Speak last turn | `/listen` |
| — | Help + status + key list | `/audio` |

**Discovery limitations (Grok Build):**

- `Ctrl+.` / `Ctrl+X` only lists *built-in* Grok bindings — plugins cannot inject into that cheatsheet.
- **`Ctrl+P` does not reliably list Focus Audio skills** even when they are loaded and invocable. Use slash commands or global hotkeys instead.

**How to use Focus Audio:**

1. **Slash (works):** type `/audio` for the key list + status; `/audio-toggle`, `/listen`, etc.
2. **Global hotkeys:** `Ctrl+Shift+…` system-wide when the daemon is running (macOS Accessibility for Terminal/Python).
3. Optional check: `grok inspect` lists `audio` / `audio-toggle` under Skills.

## API key (no extra secret storage)

Same resolution order as **ara-agent** — the key is **never** written to Focus Audio config or cache:

1. macOS Keychain via `keyring` — service `xai-api-key`, account `$USER`
2. macOS Keychain via `security find-generic-password … -w`
3. Env `XAI_API_KEY` (optional override)

If you already use ara-agent, you are done. Verify:

```bash
./plugins/focus-audio/bin/focus-audio config --show
# api_key_source: keychain:xai-api-key (…)
# api_key_present: true
```

## Config

`~/.grok/focus-audio/config.toml` (created on first run) — settings only, **no secrets**:

```toml
enabled = true
voice_id = "ara"
speed = 1.1
language = "en"
mode = "brief"
autoplay = true
min_chars = 80
max_brief_words = 220
# Latency: skip chat rewrite when cleaned reply is already short
skip_brief_words = 80
# Stream TTS — first ~35 words speak ASAP while the rest synthesizes
chunk_tts = true
first_chunk_words = 35
chunk_words = 90
tts_bit_rate = 96000
model = "grok-4-1-fast-non-reasoning"
chime = true
hotkeys = true
# Experimental mid-turn speech from session updates.jsonl
live_verbatim = false
live_min_chars = 40
live_poll_ms = 150
live_skip_stop_brief = true
# After live finishes, still play post-turn audio in `mode` (brief|verbatim)
live_then_brief = true
```

### Mode vs live (mental model)

| Setting | What you hear |
|---------|----------------|
| `mode = brief` (default) | Post-turn / rebrief / mode-toggle: structured focus brief |
| `mode = verbatim` | Post-turn: cleaned full reply |
| `live_verbatim = true` | Mid-turn: stream agent chunks as they land |
| `live_then_brief = true` (default) | After live covers a turn, **also** play `mode` once live is done |
| `live_then_brief = false` + `live_skip_stop_brief` | Live only — skip post-turn when live already spoke |

`focus-audio status` reports **`effective`**: `brief`, `verbatim`, `live_verbatim`, or `live+brief` / `live+verbatim`.

### Playback latency

Time-to-first-audio is optimized by:

1. **Skip brief LLM** when the cleaned turn is ≤ `skip_brief_words` (default 80)
2. **Chunked TTS** — synthesize a short first segment, start `afplay`, generate the rest in parallel
3. **Slightly lower TTS bitrate** (`tts_bit_rate`, default 96k) for a smaller first download

Cached replays and `Ctrl+Shift+R` remain near-instant.

### Experimental live verbatim

When `live_verbatim = true`, Focus Audio tails the session’s `updates.jsonl` after each
user prompt and speaks **agent message** chunks as Grok writes them (status lines during
tools + the final reply). This is *not* token-level streaming — Grok flushes narrative
chunks periodically — but it starts audio **during** the turn instead of only on `Stop`.

Enable:

```bash
focus-audio config --live true
# daemon reloads config; or restart: focus-audio shutdown && focus-audio ensure -v
```

Behavior:

| Event | Action |
|-------|--------|
| `UserPromptSubmit` | `live-start` — tail from EOF, speak new `agent_message_chunk`s |
| Mid-turn | Verbatim TTS per chunk (no brief rewrite model) |
| `Stop` | If live already spoke ≥1 segment: with `live_then_brief` wait for live to finish then play `mode`; otherwise skip post-turn |

Disable with `focus-audio config --live false`.  
Post-live brief: `focus-audio config --live-then-brief true|false`.  
**Ctrl+Shift+.** still skips (cancels deferred post-live too).

## Layout

| Path | Role |
|------|------|
| `~/.grok/plugins/focus-audio/` | Installed plugin (this package) |
| `~/.grok/focus-audio/` | Runtime data (config, cache, socket, last_brief.md) |
| `hooks/hooks.json` | `Stop` → enqueue |
| `skills/listen/` | `/listen` skill |

## Tests

```bash
cd plugins/focus-audio
python3 -m pytest tests/ -q
```

## Notes

- macOS first (`afplay`). Pause stops the clip; resume/restart plays from the start.
- Hooks are **fail-open** — audio never blocks coding.
- New turns **cancel** in-flight synthesis so you always hear the latest state.
