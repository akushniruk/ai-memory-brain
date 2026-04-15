#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT_ID="com.ai-memory-brain.gateway"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_AGENTS_DIR/$AGENT_ID.plist"
APP_HOME="${AI_MEMORY_BRAIN_HOME:-$HOME/Library/Application Support/ai-memory-brain}"
RUN_DIR="$APP_HOME/memory/logs"
START_SCRIPT="$ROOT_DIR/memory_gateway/start-server.sh"

mkdir -p "$LAUNCH_AGENTS_DIR"
mkdir -p "$RUN_DIR"
mkdir -p "$APP_HOME/config" "$APP_HOME/vault"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>$AGENT_ID</string>

    <key>ProgramArguments</key>
    <array>
      <string>$START_SCRIPT</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$ROOT_DIR</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>$RUN_DIR/server.log</string>

    <key>StandardErrorPath</key>
    <string>$RUN_DIR/server.error.log</string>
  </dict>
</plist>
PLIST

launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl load "$PLIST_PATH"

echo "Installed launch agent: $AGENT_ID"
echo "Plist: $PLIST_PATH"
echo "Logs:"
echo "  $RUN_DIR/server.log"
echo "  $RUN_DIR/server.error.log"
