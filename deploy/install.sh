#!/bin/sh
# macOS only, and optional: install (or reinstall) Maxie's services as launchd user agents, rendered from the
# templates in this folder with this checkout's paths. On Linux, run `make run` under systemd or tmux instead.
#   dev.maxie.hub        the hub (FastAPI on 127.0.0.1:8780), restarts on crash, starts at login
#   dev.maxie.laya       the local Laya router server (only when `laya` is installed in .venv)
#   dev.maxie.logrotate  rotates ~/Library/Logs/maxie/*.log once a day (copy-truncate, keeps 7)
#   dev.maxie.backup     03:00 encrypted backup of ~/.maxie, then a test restore
#   dev.maxie.voice      "Hey Max" (mic, local VAD + Whisper, Kokoro); asks for the Microphone once
# Usage: deploy/install.sh [hub|laya|logrotate|backup|voice|all]   (default: all)
# If the checkout lives in ~/Documents, macOS protects it: the agents run the venv's real python and the first
# start of each shows one "python3.12 wants to access your Documents folder" prompt. Click Allow.
set -eu
[ "$(uname)" = Darwin ] || { echo "deploy/install.sh is for macOS (launchd). On Linux: make run."; exit 1; }
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$(cd "$REPO" && .venv/bin/python -c 'import sys; print(sys._base_executable)')"  # real binary, outside ~/Documents
SITE="$(cd "$REPO" && .venv/bin/python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$HOME/Library/Logs/maxie"   # outside ~/Documents: launchd can always open these
DOMAIN="gui/$(id -u)"
mkdir -p "$AGENTS" "$LOGS" "$HOME/.maxie"

install_one() {  # label
  sed -e "s#__REPO__#$REPO#g" -e "s#__PYTHON__#$PYTHON#g" -e "s#__SITE__#$SITE#g" -e "s#__LOGS__#$LOGS#g" -e "s#__HOME__#$HOME#g" \
      "$REPO/deploy/$1.plist" > "$AGENTS/$1.plist"
  launchctl bootout "$DOMAIN/$1" 2>/dev/null || true
  i=0  # bootout returns before the old job is gone; bootstrapping too soon fails with "5: Input/output error"
  while launchctl print "$DOMAIN/$1" >/dev/null 2>&1 && [ $i -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
  launchctl bootstrap "$DOMAIN" "$AGENTS/$1.plist"
  launchctl enable "$DOMAIN/$1"
  echo "installed $1"
}

what="${1:-all}"
case "$what" in
  hub|all) install_one dev.maxie.hub ;;
esac
case "$what" in
  laya|all)
    if "$REPO/.venv/bin/python" -c "import laya" 2>/dev/null; then install_one dev.maxie.laya
    else echo "skip laya: not installed (uv pip install -p .venv -e '.[laya]')"; fi ;;
esac
case "$what" in
  backup|all) install_one dev.maxie.backup ;;
esac
case "$what" in
  voice|all)
    if "$REPO/.venv/bin/python" -c "import mlx_whisper, silero_vad, sounddevice" 2>/dev/null; then install_one dev.maxie.voice
    else echo "skip voice: packages missing (uv pip install -p .venv mlx-whisper silero-vad sounddevice kokoro-onnx)"; fi ;;
esac
case "$what" in
  logrotate|all)
    cp "$REPO/deploy/rotate_logs.sh" "$LOGS/rotate_logs.sh"   # a copy outside ~/Documents, so launchd can run it
    install_one dev.maxie.logrotate ;;
esac
