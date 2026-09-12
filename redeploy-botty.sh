#!/usr/bin/env bash
# Redeploy botty from this workspace to the installed Omarchy plugin,
# then restart the shell. Run this after editing Panel.qml / botty_backend.py.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_DIR="$HOME/.config/omarchy/plugins/meviusisback.botty"

if [[ ! -d "$DEST_DIR" ]]; then
  echo "ERROR: plugin dir not found: $DEST_DIR" >&2
  exit 1
fi

for f in Panel.qml botty_backend.py; do
  if [[ -f "$SRC_DIR/$f" ]]; then
    cp "$SRC_DIR/$f" "$DEST_DIR/$f"
    echo "copied $f"
  else
    echo "skip $f (not in $SRC_DIR)"
  fi
done

omarchy restart shell
echo "botty redeployed + shell restarted"
