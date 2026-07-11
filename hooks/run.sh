#!/usr/bin/env bash
# Focus Audio hook wrapper.
# - Resolves plugin root without relying on Grok's ${VAR:-default} expansion
# - Logs every invocation so silent fail-open paths are debuggable
# - Forwards stdin (hook JSON payload) to the CLI
set -euo pipefail

HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="${GROK_PLUGIN_ROOT:-}"
if [ -z "$ROOT" ] || [ ! -x "$ROOT/bin/focus-audio" ]; then
  ROOT="${CLAUDE_PLUGIN_ROOT:-}"
fi
if [ -z "$ROOT" ] || [ ! -x "$ROOT/bin/focus-audio" ]; then
  ROOT="$(cd "$HOOK_DIR/.." && pwd)"
fi

DATA="${GROK_PLUGIN_DATA:-${CLAUDE_PLUGIN_DATA:-$HOME/.grok/focus-audio}}"
mkdir -p "$DATA"
LOG="$DATA/hook.log"

# Capture stdin once so we can log and still pass it to the CLI.
PAYLOAD="$(cat || true)"
PAYLOAD_LEN=${#PAYLOAD}

{
  echo "--- $(date '+%Y-%m-%d %H:%M:%S') ---"
  echo "argv: $*"
  echo "event=${GROK_HOOK_EVENT:-?} name=${GROK_HOOK_NAME:-?}"
  echo "session=${GROK_SESSION_ID:-?} cwd=${GROK_WORKSPACE_ROOT:-?}"
  echo "ROOT=$ROOT DATA=$DATA stdin_len=$PAYLOAD_LEN"
} >>"$LOG" 2>/dev/null || true

if [ ! -x "$ROOT/bin/focus-audio" ]; then
  echo "focus-audio missing at $ROOT/bin/focus-audio" >>"$LOG" 2>/dev/null || true
  exit 0
fi

# Debounce enqueue / live-start: plugin + global hooks can both fire for one turn.
# Skip a second identical action for the same session within ~4s.
if [ "${1:-}" = "enqueue" ] || [ "${1:-}" = "live-start" ]; then
  sid="${GROK_SESSION_ID:-unknown}"
  stamp="$DATA/.last_${1}"
  now="$(date +%s)"
  if [ -f "$stamp" ]; then
    # shellcheck disable=SC2034
    read -r last_ts last_sid <"$stamp" || true
    if [ "${last_sid:-}" = "$sid" ] && [ -n "${last_ts:-}" ]; then
      delta=$((now - last_ts))
      if [ "$delta" -ge 0 ] && [ "$delta" -lt 4 ]; then
        echo "${1} debounced session=$sid delta=${delta}s" >>"$LOG" 2>/dev/null || true
        exit 0
      fi
    fi
  fi
  printf '%s %s\n' "$now" "$sid" >"$stamp" 2>/dev/null || true
fi

# Fail-open: never block the Grok turn.
set +e
printf '%s' "$PAYLOAD" | bash "$ROOT/bin/focus-audio" "$@" >>"$LOG" 2>&1
rc=$?
set -e
echo "exit=$rc" >>"$LOG" 2>/dev/null || true
exit 0
