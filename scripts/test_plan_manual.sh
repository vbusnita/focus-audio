#!/usr/bin/env bash
# Focus Audio — release / PR manual test plan (privacy + local hardening).
#
# Exercises the steps that pytest alone does not cover: harden, doctor
# runtime_perms, purge, daemon socket modes, and kill-switches.
#
# Default: fully isolated under a temp GROK_HOME (never touches your
# ~/.grok/focus-audio cache or config).
#
# Usage (from repo root):
#   ./scripts/test_plan_manual.sh
#   ./scripts/test_plan_manual.sh --real          # also harden + restart real daemon
#   ./scripts/test_plan_manual.sh --skip-pytest
#   ./scripts/test_plan_manual.sh --keep-tmp      # leave temp dir for inspection
#
# Exit code: number of failed checks (0 = all green).
set -euo pipefail

SCRIPT="${BASH_SOURCE[0]:-$0}"
while [ -L "$SCRIPT" ]; do
  link="$(readlink "$SCRIPT")"
  case "$link" in
    /*) SCRIPT="$link" ;;
    *) SCRIPT="$(cd "$(dirname "$SCRIPT")" && pwd)/$link" ;;
  esac
done
REPO="$(cd "$(dirname "$SCRIPT")/.." && pwd)"
FA="$REPO/bin/focus-audio"
export PATH="$REPO/bin:${PATH:-}"
export PYTHONPATH="${REPO}${PYTHONPATH:+:$PYTHONPATH}"

DO_REAL=0
DO_PYTEST=1
KEEP_TMP=0
for arg in "$@"; do
  case "$arg" in
    --real) DO_REAL=1 ;;
    --skip-pytest) DO_PYTEST=0 ;;
    --keep-tmp) KEEP_TMP=1 ;;
    -h|--help)
      sed -n '2,25p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown flag: $arg (try --help)" >&2
      exit 2
      ;;
  esac
done

PASS=0
FAIL=0
NOTE=0

ok()   { echo "  PASS: $*"; PASS=$((PASS + 1)); }
bad()  { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }
note() { echo "  NOTE: $*"; NOTE=$((NOTE + 1)); }
hdr()  { echo; echo "### $*"; }

mode_oct() {
  # portable-ish: prefer python for exact mode bits
  python3 -c "import os,stat; print(oct(stat.S_IMODE(os.stat('$1').st_mode)))"
}

is_owner_only() {
  python3 -c "
import os, stat, sys
p = sys.argv[1]
m = stat.S_IMODE(os.stat(p).st_mode)
sys.exit(0 if (m & 0o077) == 0 else 1)
" "$1"
}

echo "=============================================="
echo "Focus Audio manual test plan"
echo "repo: $REPO"
echo "cli:  $FA"
echo "=============================================="

if [ ! -x "$FA" ]; then
  echo "bin/focus-audio missing or not executable" >&2
  exit 1
fi

# ── 1. pytest ────────────────────────────────────────────────────────────
if [ "$DO_PYTEST" = "1" ]; then
  hdr "[1/6] pytest (full suite)"
  if (cd "$REPO" && python3 -m pytest tests/ -q); then
    ok "pytest suite green"
  else
    bad "pytest suite failed"
  fi
else
  hdr "[1/6] pytest (skipped)"
  note "passed --skip-pytest"
fi

# ── 2. install / version path ────────────────────────────────────────────
hdr "[2/6] Install path / version"
ver="$("$FA" --version 2>&1 || true)"
echo "  version: $ver"
if echo "$ver" | grep -qE 'focus-audio [0-9]+\.[0-9]+\.[0-9]+'; then
  ok "CLI --version works ($ver)"
else
  bad "CLI --version unexpected: $ver"
fi
if command -v grok >/dev/null 2>&1; then
  if grok plugin list 2>/dev/null | grep -qi focus-audio; then
    ok "grok plugin list includes focus-audio"
  else
    note "focus-audio not in grok plugin list (run: grok plugin install . --trust)"
  fi
else
  note "grok CLI not on PATH — checkout binary only"
fi

# ── isolated runtime root ────────────────────────────────────────────────
TEST_HOME="$(mktemp -d "${TMPDIR:-/tmp}/focus-audio-tp.XXXXXX")"
export GROK_HOME="$TEST_HOME/grok"
mkdir -p "$GROK_HOME"
cleanup() {
  if [ "$KEEP_TMP" = "1" ]; then
    echo "  kept temp: $TEST_HOME"
  else
    rm -rf "$TEST_HOME"
  fi
}
trap cleanup EXIT

# Seed "legacy" open perms like a pre-hardening install
mkdir -p "$GROK_HOME/focus-audio/cache"
chmod 755 "$GROK_HOME/focus-audio" "$GROK_HOME/focus-audio/cache"
printf '%s\n' 'enabled = true' 'mode = "brief"' >"$GROK_HOME/focus-audio/config.toml"
chmod 644 "$GROK_HOME/focus-audio/config.toml"
echo "script" >"$GROK_HOME/focus-audio/cache/old.txt"
chmod 644 "$GROK_HOME/focus-audio/cache/old.txt"

echo "  GROK_HOME=$GROK_HOME (isolated)"

# ── 3. harden + doctor ───────────────────────────────────────────────────
hdr "[3/6] harden + doctor (isolated)"
echo "  before: dir=$(stat -f '%Sp' "$GROK_HOME/focus-audio" 2>/dev/null || stat -c '%A' "$GROK_HOME/focus-audio") file=$(stat -f '%Sp' "$GROK_HOME/focus-audio/config.toml" 2>/dev/null || true)"
hout="$("$FA" harden 2>&1)"
echo "  $hout" | head -3
if echo "$hout" | grep -qi hardened; then
  ok "harden ran"
else
  bad "harden unexpected output"
fi
if is_owner_only "$GROK_HOME/focus-audio"; then
  ok "data dir owner-only ($(mode_oct "$GROK_HOME/focus-audio"))"
else
  bad "data dir still has group/other bits"
fi
if is_owner_only "$GROK_HOME/focus-audio/config.toml"; then
  ok "config.toml owner-only ($(mode_oct "$GROK_HOME/focus-audio/config.toml"))"
else
  bad "config.toml still has group/other bits"
fi

doc="$("$FA" doctor 2>&1)"
echo "$doc" | sed 's/^/  /' | head -25
if echo "$doc" | grep -q 'runtime_perms' && echo "$doc" | grep -E 'runtime_perms:.*owner-only|runtime_perms:.*700' >/dev/null; then
  ok "doctor runtime_perms ok"
else
  bad "doctor runtime_perms missing or not owner-only"
  echo "$doc" | grep runtime_perms || true
fi

# ── 4. purge ─────────────────────────────────────────────────────────────
hdr "[4/6] purge (isolated; never real cache)"
echo "more" >"$GROK_HOME/focus-audio/cache/a.txt"
echo "logline" >"$GROK_HOME/focus-audio/hook.log"
echo "brief" >"$GROK_HOME/focus-audio/last_brief.md"

set +e
"$FA" purge >/tmp/fa-tp-purge-dry.txt 2>&1
rc=$?
set -e
if [ "$rc" = "2" ]; then
  ok "purge without --yes exits 2"
else
  bad "purge without --yes exit=$rc (want 2)"
fi

set +e
"$FA" purge --yes >/tmp/fa-tp-purge.txt 2>&1
rc=$?
set -e
echo "  $(cat /tmp/fa-tp-purge.txt)"
if [ -e "$GROK_HOME/focus-audio/cache/a.txt" ] || [ -e "$GROK_HOME/focus-audio/cache/old.txt" ]; then
  bad "cache files remain after purge --yes"
else
  ok "purge --yes cleared cache"
fi
if [ -f "$GROK_HOME/focus-audio/config.toml" ]; then
  ok "config.toml preserved"
else
  bad "config.toml was deleted"
fi

echo "x" >"$GROK_HOME/focus-audio/cache/b.txt"
echo "log" >"$GROK_HOME/focus-audio/hook.log"
echo "daemon" >"$GROK_HOME/focus-audio/daemon.log"
echo "last" >"$GROK_HOME/focus-audio/last_brief.md"
echo '{}' >"$GROK_HOME/focus-audio/last_job.json"
"$FA" purge --all --yes >/tmp/fa-tp-purge-all.txt 2>&1
echo "  $(cat /tmp/fa-tp-purge-all.txt)"
still=0
for f in cache/b.txt hook.log daemon.log last_brief.md last_job.json; do
  if [ -e "$GROK_HOME/focus-audio/$f" ]; then
    echo "  still exists: $f"
    still=1
  fi
done
if [ "$still" = "0" ] && [ -f "$GROK_HOME/focus-audio/config.toml" ]; then
  ok "purge --all --yes removed cache/logs/last; kept config"
else
  bad "purge --all incomplete or removed config"
fi

# ── 5. daemon socket 600 ─────────────────────────────────────────────────
hdr "[5/6] daemon socket owner-only (isolated)"
"$FA" shutdown >/dev/null 2>&1 || true
sleep 0.3
"$FA" ensure -v 2>&1 | sed 's/^/  /' | head -5

sock="$GROK_HOME/focus-audio/daemon.sock"
for _ in $(seq 1 20); do
  [ -S "$sock" ] && break
  sleep 0.25
done

if [ -S "$sock" ]; then
  echo "  socket: $(stat -f '%Sp %OLp' "$sock" 2>/dev/null || ls -l "$sock") ($(mode_oct "$sock"))"
  if is_owner_only "$sock"; then
    ok "daemon socket owner-only"
  else
    bad "daemon socket has group/other bits"
  fi
  "$FA" status 2>&1 | head -8 | sed 's/^/  /'
else
  bad "daemon.sock not created under isolated GROK_HOME"
fi
"$FA" shutdown >/dev/null 2>&1 || true
sleep 0.3

# ── 6. kill-switches ─────────────────────────────────────────────────────
hdr "[6/6] FOCUS_AUDIO=0 and focus-audio off (isolated)"
"$FA" on >/dev/null 2>&1 || true

export FOCUS_AUDIO=0
set +e
"$FA" ensure -v >/tmp/fa-tp-fa0-ensure.txt 2>&1
"$FA" enqueue -v >/tmp/fa-tp-fa0-enq.txt 2>&1
set -e
if [ -f "$GROK_HOME/focus-audio/hook.log" ] && grep -qiE 'disabled|skipped|FOCUS_AUDIO' "$GROK_HOME/focus-audio/hook.log"; then
  ok "FOCUS_AUDIO=0 skips ensure/enqueue (hook.log)"
else
  # Some builds log only on enqueue; accept empty stdout + no daemon spawn
  if ! [ -S "$GROK_HOME/focus-audio/daemon.sock" ]; then
    ok "FOCUS_AUDIO=0: no daemon socket after ensure/enqueue"
  else
    bad "FOCUS_AUDIO=0 did not skip (see hook.log)"
  fi
fi
unset FOCUS_AUDIO

off_out="$("$FA" off 2>&1)"
echo "  $off_out" | head -3
if grep -qi 'enabled = false' "$GROK_HOME/focus-audio/config.toml"; then
  ok "focus-audio off sets enabled=false"
else
  bad "off did not set enabled=false"
fi
set +e
"$FA" enqueue -v >/tmp/fa-tp-off-enq.txt 2>&1
set -e
if [ -f "$GROK_HOME/focus-audio/hook.log" ] && grep -qiE 'enabled=false|skipped' "$GROK_HOME/focus-audio/hook.log"; then
  ok "enqueue skipped when power off"
else
  bad "enqueue not skipped after off"
fi
"$FA" on >/dev/null 2>&1 || true

# ── bonus: secret scrub ──────────────────────────────────────────────────
hdr "[bonus] secret scrub before TTS/cache (no network)"
if python3 <<'PY'
from focus_audio.config import Config
from focus_audio.pipeline import resolve_script

key = "xai-" + ("q" * 40)
cfg = Config(mode="verbatim", skip_brief_words=9999, min_chars=1)
ready = resolve_script(f"Deployed with {key}", cfg, mode="verbatim", skip_llm=True)
assert key not in ready.script and key not in ready.cleaned, (ready.script, ready.cleaned)
print("scrubbed ok")
PY
then
  ok "credential-like xai- key redacted in cleaned+script"
else
  bad "secret scrub failed"
fi

# ── optional real home ───────────────────────────────────────────────────
if [ "$DO_REAL" = "1" ]; then
  hdr "[--real] harden + daemon restart on ~/.grok/focus-audio"
  unset GROK_HOME
  unset FOCUS_AUDIO
  # harden is safe (chmod only); never purge real cache here
  rh="$("$FA" harden 2>&1)"
  echo "  $rh" | head -4
  if is_owner_only "${HOME}/.grok/focus-audio" 2>/dev/null; then
    ok "real data dir owner-only"
  else
    bad "real data dir not owner-only (missing?)"
  fi
  if [ -f "${HOME}/.grok/focus-audio/config.toml" ] && is_owner_only "${HOME}/.grok/focus-audio/config.toml"; then
    ok "real config.toml owner-only"
  else
    note "real config.toml missing or not owner-only"
  fi

  "$FA" shutdown >/dev/null 2>&1 || true
  sleep 0.4
  "$FA" ensure -v 2>&1 | sed 's/^/  /' | head -3
  sleep 0.6
  rsock="${HOME}/.grok/focus-audio/daemon.sock"
  if [ -S "$rsock" ] && is_owner_only "$rsock"; then
    ok "real daemon socket owner-only ($(mode_oct "$rsock"))"
  else
    bad "real daemon socket missing or not owner-only"
  fi
  "$FA" doctor 2>&1 | grep -E 'overall:|runtime_perms|daemon:' | sed 's/^/  /'
else
  hdr "[--real] skipped (pass --real to harden + restart live daemon)"
  note "isolated run only — real cache not purged"
fi

# ── summary ──────────────────────────────────────────────────────────────
echo
echo "=============================================="
echo "RESULTS: pass=$PASS fail=$FAIL notes=$NOTE"
if [ "$KEEP_TMP" = "1" ]; then
  echo "temp GROK_HOME parent: $TEST_HOME"
fi
echo "=============================================="

if [ "$FAIL" -gt 0 ]; then
  exit "$FAIL"
fi
exit 0
