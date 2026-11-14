#!/usr/bin/env bash
# Build "S2M Console.app": an app to double-click instead of `swift run`.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: apps/mac/scripts/make-app.sh [--install]

Builds the console in release mode and wraps it in apps/mac/dist/S2M Console.app.
--install also copies it to /Applications (to ~/Applications when that is not writable).

The app finds session2memory by itself: the checkout it was built from, else the
`session2memory` command on your PATH. Nothing has to be set up in it.
USAGE
}

install=0
case "${1:-}" in
  --install) install=1 ;;
  -h|--help) usage; exit 0 ;;
  "") ;;
  *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
esac

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
package_dir="$(CDPATH= cd -- "$script_dir/.." && pwd)"
checkout="$(CDPATH= cd -- "$package_dir/../.." && pwd)"
app="$package_dir/dist/S2M Console.app"

cd "$package_dir"
swift build -c release
binary="$(swift build -c release --show-bin-path)/S2MConsole"

rm -rf "$app"
mkdir -p "$app/Contents/MacOS"
cp "$binary" "$app/Contents/MacOS/S2MConsole"

xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }
cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>S2MConsole</string>
  <key>CFBundleIdentifier</key>
  <string>com.session2memory.console</string>
  <key>CFBundleName</key>
  <string>S2M Console</string>
  <key>CFBundleDisplayName</key>
  <string>S2M Console</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>S2MBuiltFrom</key>
  <string>$(xml "$checkout")</string>
</dict>
</plist>
PLIST
plutil -lint "$app/Contents/Info.plist" >/dev/null
# Ad hoc signature: enough to run on the Mac that built it.
codesign --force --sign - "$app" >/dev/null 2>&1 || echo "warning: could not sign the app" >&2

echo "built: $app"
if [ "$install" -eq 1 ]; then
  target="/Applications"
  if [ ! -w "$target" ]; then
    target="$HOME/Applications"
    mkdir -p "$target"
  fi
  rm -rf "$target/S2M Console.app"
  cp -R "$app" "$target/"
  echo "installed: $target/S2M Console.app"
fi
