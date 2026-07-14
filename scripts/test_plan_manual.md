# Manual test plan (`test_plan_manual.sh`)

This script is the **privacy and local-hardening smoke test** for Focus Audio. Unit tests (`pytest`) cover logic in isolation. This script covers the things that only show up when you drive the real CLI: file permissions, purge behavior, the daemon socket, and the kill-switches that keep the plugin quiet on sensitive work.

If you cloned or forked this repository, you can run it after install (or even before a full Grok session) to confirm the checkout you are about to trust behaves the way the README and SECURITY policy claim—without spending xAI credits and, by default, without touching your live speech cache.

## Quick start

From the **repository root** (not from inside `scripts/`):

```bash
chmod +x scripts/test_plan_manual.sh   # once, if needed
./scripts/test_plan_manual.sh
```

A green run ends with `fail=0`. The process exit code is the number of failed checks (`0` means success).

### Flags

| Flag | Effect |
|------|--------|
| *(none)* | Full plan under a temporary `GROK_HOME`, plus `pytest` |
| `--skip-pytest` | Manual steps only (faster iteration) |
| `--keep-tmp` | Leave the temporary data directory on disk so you can inspect it |
| `--real` | After the isolated plan, also harden and restart the **live** daemon under `~/.grok/focus-audio` |
| `--help` | Print the header comment from the script |

`--real` never runs `purge` on your real cache. Hardening only changes modes (chmod); restarting the daemon re-binds the Unix socket with owner-only permissions.

## What “isolated” means

Focus Audio stores runtime state under Grok’s home directory (`~/.grok/focus-audio` by default). The script sets:

```bash
export GROK_HOME=/some/temp/dir/grok
```

so every command it runs during the main plan writes config, cache, logs, and sockets under that temporary tree instead of your real home. When the script exits, it deletes the temp directory unless you passed `--keep-tmp`.

That design is intentional for **public consumers of the repo**: contributors, reviewers, and people evaluating the plugin can exercise purge and daemon lifecycle safely. Your production speech cache is not collateral damage.

## How the script finds the code under test

It does not assume a global `focus-audio` on your PATH is correct. It resolves its own location, sets:

- `FA` → `$REPO/bin/focus-audio`
- `PATH` so that binary wins
- `PYTHONPATH` so `python -m focus_audio.cli` loads **this clone’s** package

So after `git pull` or a PR checkout, the plan validates the tree you are looking at, not an older install that happens to share the same command name.

## Step-by-step (what each phase is for)

### 1. pytest

Runs `python3 -m pytest tests/ -q` unless `--skip-pytest` is set. This is the fast logic suite (including redaction and path helpers). The manual plan is complementary, not a replacement.

### 2. Version and install path

Runs `focus-audio --version` and, if the Grok CLI is available, checks that `focus-audio` appears in `grok plugin list`. Missing Grok is only a **note**, not a failure—so pure clone-and-test still works on a machine without Grok Build yet.

### 3. Harden + doctor

Before calling `harden`, the script seeds a deliberately loose layout (directories `755`, files `644`) to simulate a pre-hardening or careless install. It then runs `focus-audio harden` and asserts that the data directory and `config.toml` are owner-only (no group/other permission bits).

It also runs `focus-audio doctor` and expects the `runtime_perms` check to report owner-only / `700`. That is how end users discover mis-permissioned state after upgrade; the script verifies the check exists and agrees with reality inside the sandbox.

Doctor may still warn that sessions or the daemon are missing in the temp home. Those warnings are expected here and do not fail the plan unless `runtime_perms` itself is wrong.

### 4. Purge

Still inside the sandbox, it plants fake cache, log, and last-brief files, then checks three behaviors:

1. `purge` without `--yes` exits with code `2` (must confirm).
2. `purge --yes` removes the speech cache but **keeps** `config.toml`.
3. `purge --all --yes` also removes logs and last-brief/job metadata, still without deleting config.

This encodes the product promise for privacy-conscious users: you can wipe conversation residue on disk without wiping your settings or API key setup.

### 5. Daemon socket

Starts a daemon with `ensure` under the temp home, waits for `daemon.sock`, and asserts the socket is owner-only (`0600`-style). It samples `status` and shuts the daemon down.

Focus Audio’s control channel is a local Unix socket. If that socket were world-accessible, another local account could command TTS or shut the daemon down. This step is the regression check for that hardening.

### 6. Kill-switches

Exercises two ways to stay silent on sensitive work:

- Environment: `FOCUS_AUDIO=0` during `ensure` / `enqueue` (skip should appear in the sandbox `hook.log`, or no daemon should start).
- Persistent: `focus-audio off` writes `enabled = false` to config and keeps enqueue from speaking.

It turns power back on afterward so a `--keep-tmp` inspection tree is left in a normal state.

### Bonus: secret scrub (no network)

Feeds a fake long `xai-…` string through `resolve_script` with `skip_llm=True` so nothing hits the xAI API. Asserts the raw key does not survive into cleaned text or the spoken script. That is the same pipeline path real turns use before chat/TTS.

### Optional `--real`

Only if requested: unsets the temporary `GROK_HOME`, runs `harden` on the real data directory, restarts the live daemon, and checks real socket and permission modes. Use this when you maintain a daily install and want “my machine right now,” not when you are only reviewing a clone.

## Interpreting results

Lines look like:

```text
  PASS: …
  FAIL: …
  NOTE: …
```

Notes are informational (for example, Grok not installed, or `--real` skipped). Failures count toward the exit code. The footer prints totals:

```text
RESULTS: pass=N fail=M notes=K
```

## What this script does *not* do

- It does **not** call xAI chat or TTS, so it does not prove end-to-end audio quality or billing.
- It does **not** purge `~/.grok/focus-audio/cache` unless you run `focus-audio purge` yourself outside this script.
- It does **not** replace a full Grok session test (hooks firing on a real turn). After a green plan, a short live session with `/audio` or `doctor` is still the best final smoke for integration.

## Relationship to other docs

| Doc | Role |
|-----|------|
| [README → Privacy](../README.md#privacy) | What the plugin sends and stores for users |
| [README → Tests](../README.md#tests) | One-liner how to invoke this script |
| [SECURITY.md](../SECURITY.md) | Vulnerability reporting and secret handling policy |
| `tests/` | Automated unit coverage, including redaction helpers |

## For maintainers

Before merging privacy- or daemon-related changes, a full green run is the intended bar:

```bash
./scripts/test_plan_manual.sh
# optional on the machine that actually runs Focus Audio:
./scripts/test_plan_manual.sh --real
```

If you change default modes, purge targets, doctor check IDs, or kill-switch logging, update **both** this document and the assertions in `test_plan_manual.sh` so clone-and-verify stays trustworthy for outsiders.
