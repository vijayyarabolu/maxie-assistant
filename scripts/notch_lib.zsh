# Shared by notch_build.sh and notch_install.sh: which identity signs Maxie, and whether a Maxie.app is fit to
# be installed. Ad-hoc signing gives a new code identity every build and macOS forgets every privacy grant
# while System Settings still shows the switch on. Never ad-hoc.

KC="$HOME/Library/Keychains/maxie-signing.keychain-db"  # where make_signing_identity.sh keeps the local identity
LOCAL_IDENTITY="Maxie Local Signing"
# Which Maxie.app macOS opens for the bundle id: only the installed one (a staging copy has the same id).
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister

# Sets ID (the certificate's SHA-1), NAME and KCFLAG (codesign's keychain). The identity is MAXIE_SIGN_IDENTITY
# when set (the start of any code-signing certificate's name in your keychains), else the local
# "Maxie Local Signing", which make_signing_identity.sh makes the first time.
pick_identity() {
  local want=${MAXIE_SIGN_IDENTITY:-$LOCAL_IDENTITY} line="" try
  [[ -e $KC ]] && security unlock-keychain -p "$(security find-generic-password -s maxie-signing-keychain -w)" $KC
  for try in 1 2; do
    KCFLAG=""
    line=$(security find-identity -p codesigning 2>/dev/null | grep -m1 -F "\"$want" || true)
    [[ -n $line && $want == "$LOCAL_IDENTITY" && -e $KC ]] && KCFLAG="--keychain $KC"
    [[ -n $line || $try == 2 || $want != "$LOCAL_IDENTITY" ]] && break
    print "notch: no signing identity yet; making the local one ($LOCAL_IDENTITY)." >&2
    "$NOTCH_SCRIPTS/make_signing_identity.sh" >&2
  done
  if [[ -z $line ]]; then
    print "notch: ERROR no \"$want\" signing identity, refusing to build ad-hoc (macOS would forget every permission)." >&2
    return 1
  fi
  ID=${${line#*) }%% *}
  NAME=${${line#*\"}%\"*}
}

# Fit to be installed: a strict-valid signature, a stable designated requirement (cdhash = ad-hoc), Maxie's bundle id.
verify_app() {
  local app=$1 req id
  if ! codesign -v --strict --deep "$app" >/dev/null 2>&1; then
    print "notch: ERROR $app fails codesign -v --strict." >&2
    return 1
  fi
  req=$(codesign -d -r- "$app" 2>&1 | sed -n 's/^designated => //p')
  if [[ -z $req || $req == cdhash* ]]; then
    print "notch: ERROR $app is not stably signed; macOS would forget every permission." >&2
    return 1
  fi
  id=$(/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$app/Contents/Info.plist" 2>/dev/null || true)
  if [[ $id != dev.maxie.notch ]]; then
    print "notch: ERROR $app is \"$id\", not the Maxie app (dev.maxie.notch)." >&2
    return 1
  fi
  print "notch: verified $app: $req"
}
