#!/bin/bash
set -euo pipefail

# ──────── Config ────────
CONFIG_DIR="local"
CONFIG_EXAMPLE="$CONFIG_DIR/config.example.json"
CONFIG_TARGET="$CONFIG_DIR/config.json"

# ──────── .env ────────
ENV_EXAMPLE=".env.example"
ENV_TARGET=".env"

# ──────── Helper ────────
copy_if_missing() {
  local src="$1" dst="$2" name="$3"
  if [ ! -f "$dst" ]; then
    echo "No $name found – copying example..."
    cp "$src" "$dst"
    echo "Created $dst – edit it!"
  else
    echo "$name already exists – skipping."
  fi
}

# ──────── Main ────────
# 1. config.json
copy_if_missing "$CONFIG_EXAMPLE" "$CONFIG_TARGET" "$CONFIG_TARGET"

# 2. .env
copy_if_missing "$ENV_EXAMPLE" "$ENV_TARGET" "$ENV_TARGET"

echo "First-run bootstrap complete."