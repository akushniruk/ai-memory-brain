#!/usr/bin/env bash
set -euo pipefail

BIN_DIR="$HOME/.local/bin"
CONFIG_PATH="$HOME/.config/ai-memory-brain/config.env"
MARKER="AI_MEMORY_BRAIN_WRAPPER"

remove_wrapper() {
  local tool="$1"
  local wrapper_path="$BIN_DIR/$tool"

  if [ -f "$wrapper_path" ] && grep -q "$MARKER" "$wrapper_path" 2>/dev/null; then
    rm -f "$wrapper_path"
  fi
}

remove_wrapper codex
remove_wrapper claude
remove_wrapper ollama

restore_real() {
  local tool="$1"
  local preserved="$BIN_DIR/$tool.ai-memory-brain-real"
  if [ -x "$preserved" ] && [ ! -e "$BIN_DIR/$tool" ]; then
    mv "$preserved" "$BIN_DIR/$tool"
  fi
}

restore_real codex
restore_real claude
restore_real ollama

rm -f "$CONFIG_PATH"

echo "Removed CLI wrappers."
