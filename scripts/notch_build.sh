#!/bin/zsh
# Build Maxie.app signed by xcodebuild itself with the STABLE identity into a staging folder, and
# verify it. The installed Maxie.app is never written here: only scripts/notch_install.sh swaps a verified build in.
# The staging folder ends in .noindex so Spotlight never offers it, and LaunchServices forgets it once built: with
# the same bundle id, macOS once opened this copy as "Maxie", and the next build rewrote it while it was in use.
# Usage: scripts/notch_build.sh <derivedDataPath, relative to mac/ClaudeNotch>
set -euo pipefail
NOTCH_SCRIPTS=${0:A:h}
source "$NOTCH_SCRIPTS/notch_lib.zsh"
STAGE=$1
pick_identity
cd "$NOTCH_SCRIPTS/../mac/ClaudeNotch"
APP="$STAGE/Build/Products/Debug/Maxie.app"
if pgrep -f "${APP:A}/Contents/MacOS/" >/dev/null; then
  print "notch: ERROR Maxie is running from mac/ClaudeNotch/$APP; quit it and open the installed Maxie first." >&2
  exit 1
fi
rm -rf "$APP"  # a failed build must never leave an older one to verify
xcodegen >/dev/null
if ! xcodebuild -project ClaudeNotch.xcodeproj -scheme ClaudeNotch -configuration Debug -derivedDataPath "$STAGE" \
    CODE_SIGN_STYLE=Manual CODE_SIGN_IDENTITY="$ID" PROVISIONING_PROFILE_SPECIFIER= \
    OTHER_CODE_SIGN_FLAGS="--timestamp=none $KCFLAG" build > "$STAGE.log" 2>&1; then
  grep -E "error:" "$STAGE.log" | head -20 >&2
  print "notch: ERROR build failed (mac/ClaudeNotch/$STAGE.log)." >&2
  exit 1
fi
print "notch: built with \"$NAME\""
verify_app "$APP"
"$LSREGISTER" -u "${APP:A}" 2>/dev/null || true
