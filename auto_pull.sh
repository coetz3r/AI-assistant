#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

LOG_FILE="${LOG_FILE:-/var/log/AI-assistant/luna-autopull.log}"

SERVICE_NAME="${SERVICE_NAME:-}"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

git config fetch.unpackLimit 1

before_hash="$(git rev-parse HEAD)"

if ! git fetch --no-tags --depth=1 origin main >> "$LOG_FILE" 2>&1; then
  log "git fetch failed — leaving working tree as-is"
  exit 1
fi

git reset --hard origin/main >> "$LOG_FILE" 2>&1

after_hash="$(git rev-parse HEAD)"

if [ "$before_hash" = "$after_hash" ]; then
  log "No changes (already at ${after_hash:0:8})"
  exit 0
fi

log "Updated ${before_hash:0:8} -> ${after_hash:0:8} (any local edits discarded)"

if [ -n "$SERVICE_NAME" ]; then
  log "Restarting $SERVICE_NAME"
  sudo systemctl restart "$SERVICE_NAME"
fi
