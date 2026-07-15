#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
PYTHON_BIN="$(command -v python3)"
PLIST_PATH="$HOME/Library/LaunchAgents/com.conexgram.agent.plist"
STATE_DIR="$HOME/.conexgram"

mkdir -p "$HOME/Library/LaunchAgents" "$STATE_DIR"

if [ ! -f "$STATE_DIR/config.json" ]; then
  echo "No config found. Starting guided setup..."
  python3 -m conexgram --config "$STATE_DIR/config.json" setup
fi

python3 -m conexgram --config "$STATE_DIR/config.json" doctor --fix

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.conexgram.agent</string>

  <key>ProgramArguments</key>
  <array>
PLIST

cat >> "$PLIST_PATH" <<PLIST
    <string>$PYTHON_BIN</string>
    <string>-m</string>
    <string>conexgram</string>
    <string>--config</string>
    <string>$STATE_DIR/config.json</string>
    <string>run</string>
PLIST

cat >> "$PLIST_PATH" <<PLIST
  </array>

  <key>WorkingDirectory</key>
  <string>$PROJECT_DIR</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>$STATE_DIR/launchd.out.log</string>

  <key>StandardErrorPath</key>
  <string>$STATE_DIR/launchd.err.log</string>
</dict>
</plist>
PLIST

echo "Installed LaunchAgent plist:"
echo "$PLIST_PATH"
echo
launchctl bootout "gui/$(id -u)/com.conexgram.agent" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "gui/$(id -u)/com.conexgram.agent"
launchctl kickstart -k "gui/$(id -u)/com.conexgram.agent"

echo "Started LaunchAgent:"
echo "  com.conexgram.agent"
echo
echo "Stop:"
echo "  launchctl bootout \"gui/$(id -u)/com.conexgram.agent\""
