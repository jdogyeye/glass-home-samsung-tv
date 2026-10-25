#!/usr/bin/env bash
# Builds the launcher, then runs it on Robolectric (Android 14 framework on the
# JVM) and saves screenshots to android-tv/state/render/. Needs Maven and JDK 17+.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LAUNCHER="$HERE/../launcher"
"$LAUNCHER/build.sh"
CFG="$HERE/robolectric/target/generated-test-config/com/android/tools"
mkdir -p "$CFG"
cat > "$CFG/test_config.properties" <<PROPS
android_resource_apk=$LAUNCHER/build/resources.ap_
android_merged_assets=$LAUNCHER/build/assets
android_custom_package=app.glasshome.tv
PROPS
cd "$HERE/robolectric"
mvn -q -B test "$@"
echo "✓ screenshots in $(cd "$HERE/../state/render" && pwd)"
