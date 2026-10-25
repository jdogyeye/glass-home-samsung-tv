#!/usr/bin/env bash
# Builds Glass Home for Android TV into android-tv/launcher/build/glass-home.apk
# with the plain Android SDK tools (no Gradle, no libraries).
#
# Needs a JDK (javac, keytool) and one of:
#   - the Android SDK: ANDROID_HOME (or ANDROID_SDK_ROOT) with build-tools and
#     any platform >= 23 (Android Studio installs both), or
#   - Debian/Ubuntu: sudo apt install aapt apksigner zipalign dalvik-exchange android-sdk-platform-23
#
# Signed with a key made on first build in android-tv/state/ (git-ignored).
# Keep it: Android only installs an update signed with the same key.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
STATE="$HERE/../state"
OUT="$HERE/build"
VERSION_CODE="${VERSION_CODE:-1}"
VERSION_NAME="${VERSION_NAME:-1.0}"
MIN_SDK=21      # Android 5 (the first Android TV)
TARGET_SDK=34   # Android 14; Android 15 TVs refuse apps targeting below 24

die() { echo "build.sh: $*" >&2; exit 1; }
# Runs a Java tool, hiding the JVM's "Picked up JAVA_TOOL_OPTIONS" line but keeping its exit status.
java_tool() { "$@" 2>&1 | { grep -v '^Picked up JAVA_TOOL_OPTIONS' || true; }; return "${PIPESTATUS[0]}"; }

sdk="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
# shellcheck disable=SC2012  # SDK folder names are plain (android-34, 35.0.0)
newest() { ls -d "$@" 2>/dev/null | sort -V | tail -1; }

# Tools: the SDK's newest build-tools first, then PATH.
bt=""
[ -n "$sdk" ] && bt="$(newest "$sdk"/build-tools/*)"
tool() {
  local name
  for name in "$@"; do
    if [ -n "$bt" ] && [ -x "$bt/$name" ]; then echo "$bt/$name"; return; fi
    if command -v "$name" >/dev/null 2>&1; then command -v "$name"; return; fi
  done
}
AAPT2="$(tool aapt2)";            [ -n "$AAPT2" ] || die "aapt2 not found"
ZIPALIGN="$(tool zipalign)";      [ -n "$ZIPALIGN" ] || die "zipalign not found"
APKSIGNER="$(tool apksigner)";    [ -n "$APKSIGNER" ] || die "apksigner not found"
D8="$(tool d8)"
DX="$(tool dalvik-exchange dx)"
[ -n "$D8$DX" ] || die "neither d8 nor dx found"
command -v javac >/dev/null || die "javac not found (install a JDK)"

ANDROID_JAR="${ANDROID_JAR:-}"
if [ -z "$ANDROID_JAR" ]; then
  [ -n "$sdk" ] && ANDROID_JAR="$(newest "$sdk"/platforms/android-*/android.jar)"
  [ -n "$ANDROID_JAR" ] || ANDROID_JAR="$(newest /usr/lib/android-sdk/platforms/android-*/android.jar)"
fi
[ -f "$ANDROID_JAR" ] || die "android.jar not found (set ANDROID_JAR)"

rm -rf "$OUT"
mkdir -p "$OUT"/{res,classes,dex,assets/wallpapers} "$STATE"

echo "• photos"
cp "$REPO"/app/wallpapers/*.jpg "$REPO"/app/wallpapers/manifest.json "$OUT/assets/wallpapers/"

echo "• resources ($(basename "$(dirname "$ANDROID_JAR")"))"
"$AAPT2" compile --dir "$HERE/res" -o "$OUT/res/res.zip"
"$AAPT2" link -o "$OUT/unsigned.apk" -I "$ANDROID_JAR" \
  --manifest "$HERE/AndroidManifest.xml" -A "$OUT/assets" \
  --min-sdk-version "$MIN_SDK" --target-sdk-version "$TARGET_SDK" \
  --version-code "$VERSION_CODE" --version-name "$VERSION_NAME" \
  -0 jpg \
  "$OUT/res/res.zip"
cp "$OUT/unsigned.apk" "$OUT/resources.ap_"   # resources only, for the Robolectric render test

echo "• java"
# Java 8 bytecode, no lambdas: works with both d8 and the older dx.
find "$HERE/src" -name '*.java' > "$OUT/sources.txt"
java_tool javac -nowarn -Xlint:-options -source 8 -target 8 -encoding UTF-8 -bootclasspath "$ANDROID_JAR" \
  -classpath "$ANDROID_JAR" -d "$OUT/classes" @"$OUT/sources.txt" || die "javac failed"

echo "• dex"
if [ -n "$D8" ]; then
  java_tool jar cf "$OUT/classes.jar" -C "$OUT/classes" . || die "jar failed"
  java_tool "$D8" --release --min-api "$MIN_SDK" --lib "$ANDROID_JAR" --output "$OUT/dex" "$OUT/classes.jar"
else
  java_tool "$DX" --dex --min-sdk-version="$MIN_SDK" --output="$OUT/dex/classes.dex" "$OUT/classes"
fi

# Add classes.dex to the APK (python: zip isn't installed everywhere).
python3 - "$OUT/unsigned.apk" "$OUT/dex/classes.dex" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], 'a', zipfile.ZIP_DEFLATED) as z:
    z.write(sys.argv[2], 'classes.dex')
PY

echo "• sign"
KEYSTORE="$STATE/glass-home-release.keystore"
PASSFILE="$STATE/glass-home-release.pass"
if [ ! -f "$KEYSTORE" ]; then
  umask 077
  python3 -c 'import secrets; print(secrets.token_urlsafe(24))' > "$PASSFILE"
  java_tool keytool -genkeypair -keystore "$KEYSTORE" -storetype PKCS12 \
    -storepass:file "$PASSFILE" -keypass:file "$PASSFILE" \
    -alias glass-home -keyalg RSA -keysize 3072 -validity 36500 \
    -dname "CN=Glass Home (local build)" >/dev/null || die "keytool failed"
  echo "  made a signing key in android-tv/state/ (keep it to install updates)"
fi
"$ZIPALIGN" -f -p 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"
# PKCS12: the key's password is the keystore's, so one --ks-pass covers both.
java_tool "$APKSIGNER" sign --ks "$KEYSTORE" --ks-pass "file:$PASSFILE" \
  --ks-key-alias glass-home --out "$OUT/glass-home.apk" "$OUT/aligned.apk" || die "signing failed"
java_tool "$APKSIGNER" verify "$OUT/glass-home.apk" || die "the signed APK doesn't verify"
rm -f "$OUT/unsigned.apk" "$OUT/aligned.apk"

echo "✓ $OUT/glass-home.apk ($(( $(wc -c < "$OUT/glass-home.apk") / 1024 )) KB)"
