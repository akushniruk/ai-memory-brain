#!/usr/bin/env bash
set -euo pipefail

AGENT_ID="com.ai-memory-brain.gateway"
PLIST_PATH="$HOME/Library/LaunchAgents/$AGENT_ID.plist"

if [ -f "$PLIST_PATH" ]; then
  launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
  rm -f "$PLIST_PATH"
  echo "Removed launch agent: $AGENT_ID"
else
  echo "Launch agent not installed: $AGENT_ID"
fi
