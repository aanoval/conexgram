#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
PYTHON_BIN="$(command -v python3)"
CONEXGRAM_BIN="$(command -v conexgram || true)"
STATE_DIR="$HOME/.conexgram"
SYSTEMD_DIR="$HOME/.config/systemd/user"
SERVICE_PATH="$SYSTEMD_DIR/conexgram.service"

mkdir -p "$STATE_DIR" "$SYSTEMD_DIR"

if [ ! -f "$STATE_DIR/config.json" ]; then
  echo "No config found. Starting guided setup..."
  python3 -m conexgram --config "$STATE_DIR/config.json" setup
fi

python3 -m conexgram --config "$STATE_DIR/config.json" doctor --fix

if [ -n "$CONEXGRAM_BIN" ]; then
  EXEC_START="$CONEXGRAM_BIN --config $STATE_DIR/config.json run"
else
  EXEC_START="$PYTHON_BIN $PROJECT_DIR/gateway.py run"
fi

cat > "$SERVICE_PATH" <<SERVICE
[Unit]
Description=Conexgram Telegram connector for Codex CLI
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR
ExecStart=$EXEC_START
Restart=always
RestartSec=5
Environment=PATH=$PATH

[Install]
WantedBy=default.target
SERVICE

systemctl --user daemon-reload
systemctl --user enable --now conexgram.service

echo "Installed and started user systemd service:"
echo "  conexgram.service"
echo
echo "Status:"
echo "  systemctl --user status conexgram.service"
echo
echo "Stop:"
echo "  systemctl --user disable --now conexgram.service"
