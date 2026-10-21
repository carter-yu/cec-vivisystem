#!/usr/bin/env bash
# Install cec-vivisystem LaunchAgents into Carter's GUI session (Phase 29).
#
# Renders deploy/launchd/*.plist.template, lints, backs up existing plists,
# then bootout + bootstrap into gui/$(id -u). Only the listener is kickstarted:
# kicking the 07:00/10:00 jobs would post family messages outside schedule.
# The control app (Phase 31) starts through RunAtLoad; it is not kickstarted.
#
# bootout is asynchronous: an immediate bootstrap of the same label can fail
# with "Bootstrap failed: 5: Input/output error" (seen twice on the Mini). The
# script waits until the label is gone (up to ~10 s), retries bootstrap up to
# 3 times, keeps going with the other labels on a final failure, and exits 1
# at the end so a half-installed state is never reported as success.
#
# FileVault stays on (ADR 0013): these agents start only after a manual login.
# This script does not enable auto-login or install LaunchDaemons.
#
# Usage:
#   scripts/install_launchagents.sh --dry-run            # render + print, no launchctl
#   scripts/install_launchagents.sh                      # install on the Mini (macOS)
# Options:
#   --dry-run          render and lint only; print the launchctl commands
#   --uv PATH          absolute uv path (default: command -v uv)
#   --render-dir DIR   keep rendered plists in DIR (default: temporary dir)
set -euo pipefail

DRY_RUN=0
UV_BIN=""
RENDER_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --uv) UV_BIN="${2:?--uv needs a path}"; shift 2 ;;
    --render-dir) RENDER_DIR="${2:?--render-dir needs a path}"; shift 2 ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 64 ;;
  esac
done

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE_DIR="$REPO/deploy/launchd"
HOME_DIR="${HOME:?HOME must be set}"
AGENTS_DIR="$HOME_DIR/Library/LaunchAgents"
LOG_DIR="$HOME_DIR/Library/Logs/cec-vivisystem"
DOMAIN="gui/$(id -u)"
LISTENER_LABEL="com.cec.vivisystem.listener"

if [[ -z "$UV_BIN" ]]; then
  # launchd has a minimal PATH, so the plist needs uv's absolute path.
  UV_BIN="$(command -v uv || true)"
fi
if [[ -z "$UV_BIN" || "$UV_BIN" != /* ]]; then
  echo "error: absolute uv path not found; install uv or pass --uv /abs/path/uv" >&2
  exit 1
fi
UV_DIR="$(dirname "$UV_BIN")"

# Values are substituted into XML via sed; refuse characters that would need
# XML or sed escaping rather than half-escaping them.
for value in "$UV_BIN" "$REPO" "$HOME_DIR"; do
  if [[ "$value" == *[\&\<\>\"\|\\]* ]]; then
    echo "error: unsupported character in path: $value" >&2
    exit 1
  fi
done

if [[ -z "$RENDER_DIR" ]]; then
  RENDER_DIR="$(mktemp -d)"
  trap 'rm -rf "$RENDER_DIR"' EXIT
else
  mkdir -p "$RENDER_DIR"
fi

shopt -s nullglob
# Listener first so the heartbeat starts before anything checks it.
templates=("$TEMPLATE_DIR/$LISTENER_LABEL.plist.template")
for template in "$TEMPLATE_DIR"/*.plist.template; do
  [[ "$template" == "${templates[0]}" ]] || templates+=("$template")
done
if [[ ! -f "${templates[0]}" || ${#templates[@]} -lt 2 ]]; then
  echo "error: listener or other templates missing in $TEMPLATE_DIR" >&2
  exit 1
fi

rendered=()
for template in "${templates[@]}"; do
  name="$(basename "$template" .template)"
  out="$RENDER_DIR/$name"
  sed -e "s|__UV_DIR__|$UV_DIR|g" \
      -e "s|__UV__|$UV_BIN|g" \
      -e "s|__REPO__|$REPO|g" \
      -e "s|__HOME__|$HOME_DIR|g" \
      "$template" > "$out"
  if grep -q '__[A-Z_]*__' "$out"; then
    echo "error: unrendered placeholder in $name" >&2
    exit 1
  fi
  if command -v plutil >/dev/null 2>&1; then
    plutil -lint "$out"
  else
    echo "plutil unavailable; lint skipped for $name"
  fi
  rendered+=("$out")
done

run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    printf 'would run:'; printf ' %q' "$@"; printf '\n'
  else
    "$@"
  fi
}

UNLOAD_POLLS=20        # x 0.5 s = about 10 s
BOOTSTRAP_ATTEMPTS=3

# Wait until launchd no longer knows the label (bootout finished).
wait_unloaded() {
  local label="$1" i
  for ((i = 0; i < UNLOAD_POLLS; i++)); do
    if ! launchctl print "$DOMAIN/$label" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

# Bootstrap with retries; errno 5 (EIO) usually means bootout is still running.
bootstrap_with_retry() {
  local target="$1" attempt
  for ((attempt = 1; attempt <= BOOTSTRAP_ATTEMPTS; attempt++)); do
    if launchctl bootstrap "$DOMAIN" "$target"; then
      return 0
    fi
    echo "warning: bootstrap attempt $attempt/$BOOTSTRAP_ATTEMPTS failed for $target" >&2
    if ((attempt < BOOTSTRAP_ATTEMPTS)); then
      sleep 2
    fi
  done
  return 1
}

echo "repo=$REPO uv=$UV_BIN domain=$DOMAIN rendered=$RENDER_DIR"
if [[ $DRY_RUN -eq 0 && "$(uname -s)" != "Darwin" ]]; then
  echo "error: install requires macOS launchd; use --dry-run elsewhere" >&2
  exit 1
fi

run mkdir -p "$AGENTS_DIR" "$LOG_DIR"
BACKUP_DIR="$AGENTS_DIR/backup-cec-vivisystem-$(date +%Y%m%d-%H%M%S)"
failed=()
for out in "${rendered[@]}"; do
  name="$(basename "$out")"
  label="${name%.plist}"
  target="$AGENTS_DIR/$name"
  if [[ -e "$target" ]]; then
    run mkdir -p "$BACKUP_DIR"
    run cp -p "$target" "$BACKUP_DIR/$name"
  fi
  # bootout fails when the job is not loaded yet; that is expected.
  if [[ $DRY_RUN -eq 1 ]]; then
    run launchctl bootout "$DOMAIN/$label"
    echo "would wait up to 10 s for $label to unload"
  else
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    if ! wait_unloaded "$label"; then
      echo "warning: $label is still loaded after 10 s; trying bootstrap anyway" >&2
    fi
  fi
  run cp "$out" "$target"
  run launchctl enable "$DOMAIN/$label"
  if [[ $DRY_RUN -eq 1 ]]; then
    run launchctl bootstrap "$DOMAIN" "$target"
  elif ! bootstrap_with_retry "$target"; then
    echo "error: could not load $label; it is NOT running. Rerun the installer or run:" >&2
    echo "  launchctl bootstrap $DOMAIN $target" >&2
    failed+=("$label")
  fi
done
listener_failed=0
for label in "${failed[@]+"${failed[@]}"}"; do
  if [[ "$label" == "$LISTENER_LABEL" ]]; then
    listener_failed=1
  fi
done
if [[ $listener_failed -eq 0 ]]; then
  run launchctl kickstart -k "$DOMAIN/$LISTENER_LABEL"
fi

if [[ $DRY_RUN -eq 1 ]]; then
  echo "dry run: no files copied and launchctl not invoked"
elif [[ ${#failed[@]} -gt 0 ]]; then
  echo "error: not loaded: ${failed[*]}" >&2
  exit 1
else
  echo "installed; verify with: launchctl print $DOMAIN/$LISTENER_LABEL"
fi
