#!/bin/sh
# One step from a fresh clone: the Python env, the checks, and on a Mac (optionally) the launchd services.
#   git clone <this repo> maxie-assistant && cd maxie-assistant && ./install.sh
# Needs Python 3.11+ and uv (https://docs.astral.sh/uv/). The notch app is separate: see mac/ClaudeNotch/README.md.
set -eu
cd "$(dirname "$0")"
command -v uv >/dev/null || { echo "missing: uv (curl -LsSf https://astral.sh/uv/install.sh | sh)"; exit 1; }
make install
make check
if [ "$(uname)" = Darwin ] && [ "${MAXIE_SERVICES:-0}" = 1 ]; then
  deploy/install.sh all
  echo "Services installed. Open http://127.0.0.1:8780 and set a passcode."
else
  echo "Ready. Start the hub with: make run    (or try the demo first: make demo)"
  echo "Then open http://127.0.0.1:8780, set a passcode, and follow Settings."
fi
