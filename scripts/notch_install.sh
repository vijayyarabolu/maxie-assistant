#!/bin/zsh
# Put a verified Maxie.app in place of the installed one: only when you ask (make notch-install), never after
# every build. It is copied next to the installed app, verified again, then renamed into place, so the installed
# app is never half-written and a running Maxie keeps its own files (the old copy is deleted only after the swap).
# Usage: scripts/notch_install.sh <verified Maxie.app> <installed Maxie.app>
set -euo pipefail
NOTCH_SCRIPTS=${0:A:h}
source "$NOTCH_SCRIPTS/notch_lib.zsh"
SRC=$1
DEST=$2
verify_app "$SRC"
NEW="${DEST:r}.new.app"
OLD="${DEST:r}.old.app"
rm -rf "$NEW" "$OLD"
mkdir -p "${DEST:h}"
ditto "$SRC" "$NEW"
verify_app "$NEW"
[[ -e $DEST ]] && mv "$DEST" "$OLD"
mv "$NEW" "$DEST"
rm -rf "$OLD"
"$LSREGISTER" -f "${DEST:A}"  # the Dock, Spotlight and `open` all mean this one
print "notch: installed $DEST"
