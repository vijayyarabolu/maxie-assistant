#!/bin/zsh
# Run once, by hand or by scripts/notch_build.sh when it is missing: a local, self-signed code-signing identity
# "Maxie Local Signing", so Maxie.app keeps one code identity across rebuilds and macOS privacy grants stick.
# To sign with a certificate of your own instead, set MAXIE_SIGN_IDENTITY (see scripts/notch_lib.zsh).
# It lives in its own keychain (maxie-signing), whose random password is kept in the login keychain, and its key
# allows codesign up front: signing never shows an "allow codesign to use this key?" dialog.
# Remove later with: security delete-keychain maxie-signing.keychain-db
set -euo pipefail
NAME="Maxie Local Signing"
KC="$HOME/Library/Keychains/maxie-signing.keychain-db"
SERVICE="maxie-signing-keychain"
if security find-identity -p codesigning | grep -q "\"$NAME\""; then
  print "Already there: $NAME"; exit 0
fi
TMP=$(mktemp -d); trap 'rm -rf $TMP' EXIT
cat > $TMP/ext.cnf <<CNF
[req]
distinguished_name = dn
x509_extensions = v3
prompt = no
[dn]
CN = $NAME
[v3]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
CNF
openssl req -x509 -newkey rsa:2048 -nodes -keyout $TMP/key.pem -out $TMP/cert.pem -days 3650 -config $TMP/ext.cnf 2>/dev/null
P12=$(openssl rand -hex 16)
# SHA1/3DES: OpenSSL 3 defaults to AES/PBKDF2, which `security import` rejects ("MAC verification failed")
openssl pkcs12 -export -certpbe PBE-SHA1-3DES -keypbe PBE-SHA1-3DES -macalg sha1 -inkey $TMP/key.pem -in $TMP/cert.pem -out $TMP/id.p12 -passout pass:$P12 -name "$NAME"

KCPASS=$(openssl rand -hex 24)
security add-generic-password -U -s $SERVICE -a maxie -w $KCPASS
[[ -e $KC ]] && security delete-keychain $KC
security create-keychain -p $KCPASS $KC
security set-keychain-settings $KC                       # never auto-locks
security unlock-keychain -p $KCPASS $KC
security import $TMP/id.p12 -k $KC -P $P12 -T /usr/bin/codesign >/dev/null
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k $KCPASS $KC >/dev/null
security list-keychains -d user -s ${(f)"$(security list-keychains -d user | tr -d ' "')"} $KC
print "Made $NAME. Rebuild Maxie (make notch-run), then allow its permissions once more; they stick from now on."
