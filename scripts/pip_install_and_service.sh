#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${CONEXGRAM_PYTHON_BIN:-python3}"
PACKAGE_SPEC="${CONEXGRAM_PACKAGE:-conexgram}"
FALLBACK_PACKAGE_SPEC="${CONEXGRAM_FALLBACK_PACKAGE:-git+https://github.com/aanoval/conexgram.git}"
CONFIG_PATH="${CONEXGRAM_CONFIG:-$HOME/.conexgram/config.json}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Error: Python executable not found: $PYTHON_BIN" >&2
  exit 1
fi

echo "Installing/upgrade Conexgram package: $PACKAGE_SPEC"
if ! "$PYTHON_BIN" -m pip install --upgrade "$PACKAGE_SPEC"; then
  echo "Primary package install failed. Falling back to: $FALLBACK_PACKAGE_SPEC"
  "$PYTHON_BIN" -m pip install --upgrade "$FALLBACK_PACKAGE_SPEC"
fi

if [ ! -f "$CONFIG_PATH" ]; then
  echo "Config not found. Starting guided setup..."
  "$PYTHON_BIN" -m conexgram --config "$CONFIG_PATH" setup
fi

echo "Repairing local prerequisites..."
"$PYTHON_BIN" -m conexgram --config "$CONFIG_PATH" doctor --fix

echo "Installing launch service..."
"$PYTHON_BIN" -m conexgram --config "$CONFIG_PATH" install-service

echo "Done. Conexgram is installed and auto-start has been configured."
