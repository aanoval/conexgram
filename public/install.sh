#!/usr/bin/env bash
set -euo pipefail

APP_NAME="conexgram"
PACKAGE_SPEC="${CONEXGRAM_PACKAGE:-conexgram}"
FALLBACK_PACKAGE_SPEC="${CONEXGRAM_FALLBACK_PACKAGE:-git+https://github.com/aanoval/conexgram.git}"
PREFIX="${CONEXGRAM_HOME:-$HOME/.conexgram}"
VENV_DIR="$PREFIX/venv"
BIN_DIR="$HOME/.local/bin"
BIN_PATH="$BIN_DIR/conexgram"
CONFIG_PATH="$PREFIX/config.json"

info() {
  printf '%s\n' "$*"
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

command -v python3 >/dev/null 2>&1 || fail "python3 is required."

mkdir -p "$PREFIX" "$BIN_DIR"

info "Creating virtual environment: $VENV_DIR"
python3 -m venv "$VENV_DIR"

info "Upgrading pip..."
"$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null

info "Installing Conexgram..."
if ! "$VENV_DIR/bin/python" -m pip install --upgrade "$PACKAGE_SPEC"; then
  info "Primary package install failed. Falling back to: $FALLBACK_PACKAGE_SPEC"
  "$VENV_DIR/bin/python" -m pip install --upgrade "$FALLBACK_PACKAGE_SPEC"
fi

ln -sf "$VENV_DIR/bin/conexgram" "$BIN_PATH"
export PATH="$BIN_DIR:$PATH"

info "Installed: $("$BIN_PATH" --help | sed -n '1p')"

if [ ! -f "$CONFIG_PATH" ]; then
  info "Starting guided setup..."
  "$BIN_PATH" --config "$CONFIG_PATH" setup
fi

"$BIN_PATH" --config "$CONFIG_PATH" doctor --fix

info "Installing auto-start service..."
"$BIN_PATH" --config "$CONFIG_PATH" install-service

cat <<DONE

Conexgram is installed.

Try:
  conexgram --config "$CONFIG_PATH" doctor --fix
  conexgram --config "$CONFIG_PATH" run

If the command is not found in a new terminal, add this to your shell profile:
  export PATH="$BIN_DIR:\$PATH"

DONE
