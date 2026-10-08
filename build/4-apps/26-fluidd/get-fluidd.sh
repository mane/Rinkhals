#!/bin/sh

set -eu

FILES_DIR="${FILES_DIR:-/files}"
VERSION="1.35.0"
APP_DIRECTORY="$FILES_DIR/4-apps/home/rinkhals/apps/26-fluidd"
mkdir -p "$APP_DIRECTORY"

# Stage on the destination filesystem so publishing uses quick renames.
WORK=$(mktemp -d "$APP_DIRECTORY/.download.XXXXXX")
OLD_MOVED=0
NEW_INSTALLED=0
COMPLETE=0
cleanup() {
    if [ "$COMPLETE" -eq 0 ]; then
        [ "$NEW_INSTALLED" -eq 0 ] || rm -rf "$APP_DIRECTORY/fluidd"
        [ "$OLD_MOVED" -eq 0 ] || mv "$WORK/previous" "$APP_DIRECTORY/fluidd"
    fi
    rm -rf "$WORK"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

echo "Downloading Fluidd..."
wget -O "$WORK/fluidd.zip" "https://github.com/fluidd-core/fluidd/releases/download/v${VERSION}/fluidd.zip"
unzip -d "$WORK/fluidd" "$WORK/fluidd.zip"
test -s "$WORK/fluidd/index.html"
sed 's/"version": *"[^"]*"/"version": "'"$VERSION"'"/' "$APP_DIRECTORY/app.json" > "$WORK/app.json"

if [ -d "$APP_DIRECTORY/fluidd" ]; then
    mv "$APP_DIRECTORY/fluidd" "$WORK/previous"
    OLD_MOVED=1
fi
mv "$WORK/fluidd" "$APP_DIRECTORY/fluidd"
NEW_INSTALLED=1
mv "$WORK/app.json" "$APP_DIRECTORY/app.json"
COMPLETE=1
