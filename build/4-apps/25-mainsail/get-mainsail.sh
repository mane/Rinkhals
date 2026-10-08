#!/bin/sh

set -eu

FILES_DIR="${FILES_DIR:-/files}"
VERSION="2.17.0"
APP_DIRECTORY="$FILES_DIR/4-apps/home/rinkhals/apps/25-mainsail"
mkdir -p "$APP_DIRECTORY"

# Stage on the destination filesystem so publishing uses quick renames.
WORK=$(mktemp -d "$APP_DIRECTORY/.download.XXXXXX")
OLD_MOVED=0
NEW_INSTALLED=0
COMPLETE=0
cleanup() {
    if [ "$COMPLETE" -eq 0 ]; then
        [ "$NEW_INSTALLED" -eq 0 ] || rm -rf "$APP_DIRECTORY/mainsail"
        [ "$OLD_MOVED" -eq 0 ] || mv "$WORK/previous" "$APP_DIRECTORY/mainsail"
    fi
    rm -rf "$WORK"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

echo "Downloading Mainsail..."
wget -O "$WORK/mainsail.zip" "https://github.com/mainsail-crew/mainsail/releases/download/v${VERSION}/mainsail.zip"
unzip -d "$WORK/mainsail" "$WORK/mainsail.zip"
test -s "$WORK/mainsail/index.html"
sed 's/"version": *"[^"]*"/"version": "'"$VERSION"'"/' "$APP_DIRECTORY/app.json" > "$WORK/app.json"

if [ -d "$APP_DIRECTORY/mainsail" ]; then
    mv "$APP_DIRECTORY/mainsail" "$WORK/previous"
    OLD_MOVED=1
fi
mv "$WORK/mainsail" "$APP_DIRECTORY/mainsail"
NEW_INSTALLED=1
mv "$WORK/app.json" "$APP_DIRECTORY/app.json"
COMPLETE=1
