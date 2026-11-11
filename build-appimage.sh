#!/bin/sh
# Builds dist/SimpleHome-Browser-x86_64.AppImage (or into $OUT). Needs appimagetool on PATH, or APPIMAGETOOL=/path/to/it.
set -e
SRC="$(dirname "$(readlink -f "$0")")"
OUT="${OUT:-$SRC/dist}"; mkdir -p "$OUT"
APPDIR="$SRC/build/SimpleHome-Browser.AppDir"
rm -rf "$APPDIR"; mkdir -p "$APPDIR/usr/share/simplehome-browser/home"
cp "$SRC/browser.py" "$SRC/adblock.py" "$SRC/onepassword.py" "$SRC/passwords.py" "$SRC/mpvplay.py" "$SRC/bookmarks.py" "$SRC/onscreen.py" "$SRC/popups.py" "$SRC/simplehome-browser.png" "$APPDIR/usr/share/simplehome-browser/"
cp "$SRC/home/index.html" "$APPDIR/usr/share/simplehome-browser/home/"
"$SRC/ext/build.sh"
mkdir -p "$APPDIR/usr/share/simplehome-browser/ext"
cp "$SRC/ext/simplehome-pid.so" "$APPDIR/usr/share/simplehome-browser/ext/"
cp "$SRC/AppRun" "$SRC/simplehome-browser.desktop" "$SRC/simplehome-browser.png" "$APPDIR/"
ln -s simplehome-browser.png "$APPDIR/.DirIcon"
ARCH="${ARCH:-x86_64}" "${APPIMAGETOOL:-appimagetool}" "$APPDIR" "$OUT/SimpleHome-Browser-x86_64.AppImage"
echo "Built $OUT/SimpleHome-Browser-x86_64.AppImage"
