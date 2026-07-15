# Focus Audio

**Listen to your coding agent instead of reading every wall of text.**

Focus Audio is a plugin for [Grok Build](https://docs.x.ai/build/overview) (macOS). When the agent finishes a turn—or while it is still writing, if you turn that on—it speaks a short summary (or a cleaned full read) using **your own** [xAI API key](https://console.x.ai/team/default/api-keys). Nothing is billed through this project; you pay xAI the same way you would for any other API use.

If you already use Grok in the terminal and want to keep working with your ears free, this is for you. If you only want a general “read this page aloud” button, this is probably more than you need.

---

## What you get

| Mode | What you hear |
|------|----------------|
| **Brief** (default) | A short spoken recap: what happened, what changed, what to do next (roughly under two minutes) |
| **Verbatim** | The full reply, cleaned up for speech (big code blocks become short placeholders) |
| **Live** (optional) | Chunks of the agent’s message **while the turn is still running** |

You also get play/pause/skip, a master on/off switch, optional keyboard shortcuts, and a small local cache so restarting the same clip does not call the API again.

Audio problems never block Grok. If Focus Audio fails, coding continues.

---

## What you need

1. **macOS** (primary platform today)
2. **[Grok Build](https://docs.x.ai/build/overview)** installed and working ([product page](https://x.ai))
3. **Python 3.9+** (`python3` on your PATH)
4. An **xAI account** with an [API key](https://console.x.ai/team/default/api-keys) (see [Quickstart](https://docs.x.ai/developers/quickstart)), a positive balance in the [xAI Cloud Console](https://console.x.ai/), and access to [Text to Speech](https://docs.x.ai/developers/model-capabilities/audio/text-to-speech)

> Tip: `focus-audio doctor` can look fine while speech fails if the key is set but credits are empty or TTS is not enabled for your account. Check the [xAI Cloud Console](https://console.x.ai/) and the [TTS docs](https://docs.x.ai/developers/model-capabilities/audio/text-to-speech) if you hear nothing.

### Official xAI docs (handy bookmarks)

| Topic | Link |
|-------|------|
| Get started (account + first API call) | [Quickstart](https://docs.x.ai/developers/quickstart) |
| Create / manage API keys | [API Keys in Console](https://console.x.ai/team/default/api-keys) |
| Grok Build (the coding agent this plugin attaches to) | [Grok Build overview](https://docs.x.ai/build/overview) |
| xAI Cloud Console (keys, usage, models) | [console.x.ai](https://console.x.ai/) |
| Text to Speech (what Focus Audio uses for audio) | [TTS guide](https://docs.x.ai/developers/model-capabilities/audio/text-to-speech) |
| Voice overview (TTS, STT, realtime) | [Voice APIs](https://docs.x.ai/developers/model-capabilities/audio/voice) |
| Text generation / chat (used for brief rewrite) | [Generate text](https://docs.x.ai/developers/model-capabilities/text/generate-text) |
| REST API reference | [Inference API](https://docs.x.ai/developers/rest-api-reference/inference) |
| Pricing (including voice) | [Pricing](https://docs.x.ai/developers/pricing) |
| TTS playground (try voices in the browser) | [Console TTS playground](https://console.x.ai/team/default/voice/text-to-speech) |
| Docs home | [docs.x.ai](https://docs.x.ai/docs/overview) |

Focus Audio’s defaults point at `https://api.x.ai/v1` ([chat completions](https://docs.x.ai/developers/model-capabilities/text/generate-text) + [`/v1/tts`](https://docs.x.ai/developers/model-capabilities/audio/text-to-speech)). If a link moves, start from the [docs overview](https://docs.x.ai/docs/overview) or [Quickstart](https://docs.x.ai/developers/quickstart).

---

## Install (about five minutes)

### 1. Clone and install the plugin

```bash
git clone https://github.com/vbusnita/focus-audio.git
cd focus-audio
chmod +x bin/focus-audio
grok plugin install . --trust
grok plugin list
```

You should see `focus-audio` in the list.

Optional — put the CLI on your PATH:

```bash
mkdir -p ~/.local/bin
ln -sf "$(pwd)/bin/focus-audio" ~/.local/bin/focus-audio
```

### 2. Add your xAI API key

Create a key on the [API Keys page](https://console.x.ai/team/default/api-keys) (walkthrough: [Quickstart → Generate an API key](https://docs.x.ai/developers/quickstart#step-2-generate-an-api-key)). Focus Audio **never** saves the key into its own config file, cache, or logs.

**Recommended (macOS Keychain):**

```bash
security add-generic-password -a "$USER" -s "xai-api-key" -w "paste-your-key-here"
```

**Or** for the current terminal session:

```bash
export XAI_API_KEY="xai-…"
```

### 3. Check the install

```bash
focus-audio doctor
```

You want overall **OK**, and `api_key: present…`. The doctor never prints the secret.

### 4. Optional: global hotkeys

```bash
pip3 install --user pynput
```

Then: **System Settings → Privacy & Security → Accessibility** → allow the Terminal (or Python) app that runs Grok.

### 5. Start a new Grok session

Open a **new** Grok Build session so hooks load. If Grok was already open:

```bash
focus-audio ensure -v
focus-audio status
```

In Grok, run `/hooks` and confirm **focus-audio** is listed. You may need `/hooks-trust` once.

**Quick smoke test** — speaks a sample line. This hits the TTS API and uses a small amount of API credit:

```bash
echo "We fixed login and added tests. Next deploy staging." | focus-audio speak -
```

Use `--no-play` if you only want to exercise synthesis (still uses API credit; skips local playback):

```bash
echo "We fixed login and added tests. Next deploy staging." | focus-audio speak - --no-play
```

### Update later

```bash
cd focus-audio && git pull
grok plugin install . --trust
```

---

## Day-to-day use

Most of the time you do nothing. When a turn finishes, Focus Audio speaks automatically.

| I want to… | Do this |
|------------|---------|
| Health check / troubleshoot | `focus-audio doctor` |
| Pause / resume | `Ctrl+Shift+Space` or `/audio-toggle` |
| Restart from the start | `Ctrl+Shift+R` or `/audio-restart` |
| Skip this clip | `Ctrl+Shift+.` or `/audio-skip` |
| Hear a fresh brief of the last turn | `Ctrl+Shift+B` or `/audio-rebrief` |
| Switch brief ↔ full read | `Ctrl+Shift+M` or `/audio-mode` |
| Hear the agent *while* it writes | `/audio-live` (or `focus-audio live on`) |
| Silence everything | `/audio-off` or `focus-audio off` |
| Turn it back on | `/audio-on` or `focus-audio on` |
| Speak the last turn manually | `/listen` |
| See status / help | `/audio` or `focus-audio status` |

Grok’s built-in keyboard cheatsheet does **not** list plugin shortcuts. Use the table above or the slash commands.

**Brief vs live (short version):**

- **Brief only** — quieter, less data sent: good default.
- **Live only** (default with live on) — mid-turn speech; no second pass after the turn.
- **Live + brief** — hear progress during the turn, then a short recap after (opt-in: `live_then_brief = true`).

`focus-audio status` shows an `effective` field: `brief`, `verbatim`, `live_verbatim`, `live+brief`, or `off`.

---

## Turn it off (when you need quiet or privacy)

```bash
focus-audio off                 # stays off until you turn it on again
export FOCUS_AUDIO=0            # off for this terminal process only
grok plugin disable focus-audio # remove the plugin from Grok
```

---

## Your API key and cost

| Lookup order | Source |
|--------------|--------|
| 1 | macOS Keychain: service `xai-api-key`, account = your macOS username |
| 2 | Environment variable `XAI_API_KEY` |

Check without revealing the secret:

```bash
focus-audio doctor
# or
focus-audio config --show
# look for api_key_present / api_key_source
```

**Who gets billed?** Only **your** xAI account. This project does not ship a shared key.

**What costs money?** Roughly:

- Sometimes a **[text generation / chat](https://docs.x.ai/developers/model-capabilities/text/generate-text)** call to rewrite a short brief (skipped when the cleaned text is already short)
- A **[text-to-speech](https://docs.x.ai/developers/model-capabilities/audio/text-to-speech)** call to turn text into audio (including live chunks)

Restarting the **same** clip from cache does not call the API again. Prices and free tiers change; see [xAI pricing](https://docs.x.ai/developers/pricing) and your usage in the [xAI Cloud Console](https://console.x.ai/).

---

## Privacy (read this once)

Focus Audio runs on **your Mac**. It does **not** send data to the Focus Audio author, and it has no analytics of its own. It **does** talk to **xAI** using **your** key, because speech and brief rewriting need that API.

### What can leave your machine

| Call | What is sent |
|------|----------------|
| Chat (for briefs) | Cleaned text of the agent’s reply (up to about 12k characters; often skipped for short replies) |
| Text-to-speech | The spoken script (up to about 14k characters), including live mid-turn chunks if live is on |

Traffic goes to your configured API base (default `https://api.x.ai/v1`) over HTTPS. Endpoints and behavior are defined by xAI’s [inference REST reference](https://docs.x.ai/developers/rest-api-reference/inference) and [TTS docs](https://docs.x.ai/developers/model-capabilities/audio/text-to-speech).

### What stays only on your Mac

Under `~/.grok/focus-audio/` (locked down to your user by default):

| Path | What it is |
|------|------------|
| `cache/` | Recent spoken scripts and audio files |
| `last_brief.md` | Last script text |
| `hook.log` / `daemon.log` | Debug metadata (session ids, paths) — not API keys |
| `config.toml` | Settings only — **no secrets** |
| `daemon.sock` | Local control socket (your user only) |

Anyone logged in as **you** on this Mac can read that cache. It is not in the git repo.

### Limits of automatic scrubbing

Before speaking, Focus Audio cleans code blocks, long paths, and some agent “routing banner” noise, and it tries to redact common secret *shapes* (for example long `xai-…` / `sk-…` tokens). That is a **safety net, not a guarantee**. Secrets or private prose written in normal sentences can still be sent to xAI if they appear in the agent reply.

For sensitive work:

```bash
focus-audio off
# or
export FOCUS_AUDIO=0
```

Prefer **brief** over **live + verbatim** when you want less raw text leaving the machine. To wipe local speech residue:

```bash
focus-audio purge --yes           # speech cache only
focus-audio purge --all --yes     # cache + logs + last brief
focus-audio harden                # re-apply owner-only file permissions
```

`purge` never deletes your config or Keychain credentials. More detail: [SECURITY.md](SECURITY.md).

---

## Common commands (reference)

| Command | What it does |
|---------|----------------|
| `focus-audio doctor` | Health check (install, key present, daemon, permissions) |
| `focus-audio status` | Is the daemon up? What mode is effective? |
| `focus-audio config --show` | Show settings (includes `hotkeys`; never the raw API key) |
| `focus-audio speak -` | Speak text from stdin |
| `focus-audio speak-session <id>` | Speak the last assistant turn of a session |
| `focus-audio mode brief` / `verbatim` | Change mode and re-speak the last turn |
| `focus-audio live on` / `off` | Mid-turn speech |
| `focus-audio on` / `off` | Master power |
| `focus-audio toggle` / `pause` / `restart` / `skip` | Playback |
| `focus-audio rebrief` | Force a new brief of the last turn |
| `focus-audio purge --yes` | Delete local speech cache |
| `focus-audio harden` | Owner-only permissions on the data directory |
| `focus-audio ensure` / `release` | Start/stop session bookkeeping (usually automatic) |
| `focus-audio shutdown [--clear-refs]` | Stop the background daemon |

Hooks normally start and stop the daemon for you. If Grok crashed and left things messy:

```bash
focus-audio shutdown --clear-refs
```

---

## Settings file

All options live in `~/.grok/focus-audio/config.toml` (created on first run). **No secrets** belong in this file.

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
live_then_brief = false   # set true only if you want a recap *after* live
```

Change settings from the CLI without editing the file by hand. Examples:

```bash
focus-audio config --voice ara
focus-audio config --speed 1.2
focus-audio config --mode brief
focus-audio config --live false
focus-audio config --show
```

---

## How it fits together (optional reading)

| When Grok… | Focus Audio… |
|------------|----------------|
| Opens a session | Starts a small background helper if needed |
| You send a prompt | If live is on, begins listening for mid-turn text |
| A turn finishes | Builds brief or verbatim audio and plays it (unless live already covered it) |
| Session ends | Stops tracking that session; shuts down when nothing is left |

Where things live:

| Path | Role |
|------|------|
| This repo / your clone | Plugin source (`plugin.json`, hooks, skills) |
| `~/.grok/plugins/…` | Where Grok often keeps installed plugins |
| `~/.grok/focus-audio/` | Your personal runtime data (config, cache, logs) |

Playback prefers macOS AVAudioPlayer (true pause/resume); `afplay` is a fallback. A new turn cancels in-flight speech so you hear the latest state. Optional dependency: `pynput` for global hotkeys only.

---

## For contributors

```bash
python3 -m pytest tests/ -q

# Privacy / hardening smoke (uses a temporary data dir by default — safe)
./scripts/test_plan_manual.sh
./scripts/test_plan_manual.sh --real   # also check live ~/.grok/focus-audio perms
```

Details: [scripts/test_plan_manual.md](scripts/test_plan_manual.md).

---

## License and security

[MIT](LICENSE) — use it, fork it, ship it. Bring **your** API key.

Report security issues privately: [SECURITY.md](SECURITY.md).

### Ideas for later

- Different voices per agent/session  
- Linux / Windows playback  
- A small macOS app so Accessibility hotkeys have a stable identity  
