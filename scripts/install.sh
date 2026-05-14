#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
OS_NAME="$(uname -s)"

cd "$PROJECT_DIR"

if [ ! -f "$HOME/.conexgram/config.json" ]; then
  echo "No config found. Starting guided setup..."
  python3 -m conexgram --config "$HOME/.conexgram/config.json" setup
fi

python3 -m conexgram --config "$HOME/.conexgram/config.json" doctor --fix

case "$OS_NAME" in
  Darwin)
    exec "$SCRIPT_DIR/install_launch_agent.sh"
    ;;
  Linux)
    exec "$SCRIPT_DIR/install_linux_systemd.sh"
    ;;
  *)
    echo "Unsupported OS for automatic install: $OS_NAME" >&2
    echo "Run Conexgram manually with: python3 -m conexgram run" >&2
    exit 1
    ;;
esac
