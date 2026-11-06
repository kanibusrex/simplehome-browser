#!/usr/bin/env bash
# Install SimpleHome Browser from an AppImage: puts it in ~/Applications, a `simplehome-browser`
# command in ~/.local/bin, and an entry in the app launcher. With --default it also becomes the
# default web browser (links from other apps, and Omarchy's Super+Shift+B, open in it).
#
#   ./install.sh [path/to/SimpleHome-Browser-x86_64.AppImage] [--default]
set -euo pipefail

src="$(cd "$(dirname "$0")" && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
image="" default=0
for a in "$@"; do
  case "$a" in
    --default) default=1 ;;
    *) image="$a" ;;
  esac
done
[[ -z $image ]] && for c in "$src/SimpleHome-Browser-x86_64.AppImage" "$src/dist/SimpleHome-Browser-x86_64.AppImage" "$PWD/SimpleHome-Browser-x86_64.AppImage"; do
  [[ -f $c ]] && { image="$c"; break; }
done
[[ -f $image ]] || { echo "Usage: ./install.sh [path/to/SimpleHome-Browser-x86_64.AppImage] [--default]" >&2; exit 1; }

if ! python3 -c 'import gi; gi.require_version("WebKit2", "4.1")' 2>/dev/null; then
  echo "SimpleHome Browser needs python-gobject and webkit2gtk-4.1:  sudo pacman -S python-gobject webkit2gtk-4.1" >&2
  exit 1
fi

mkdir -p "$HOME/Applications" "$HOME/.local/bin" "$data/applications" "$data/icons/hicolor/256x256/apps"
# copy then rename, so a running copy of the old AppImage isn't overwritten in place ("Text file busy")
install -m 755 "$image" "$HOME/Applications/.SimpleHome-Browser-x86_64.AppImage.new"
mv -f "$HOME/Applications/.SimpleHome-Browser-x86_64.AppImage.new" "$HOME/Applications/SimpleHome-Browser-x86_64.AppImage"
# a command named like the window class, so launchers that look the window up by command find it
cat >"$HOME/.local/bin/simplehome-browser" <<CMD
#!/bin/sh
exec "$HOME/Applications/SimpleHome-Browser-x86_64.AppImage" "\$@"
CMD
chmod 755 "$HOME/.local/bin/simplehome-browser"

# the launcher entry and icon: from beside this script (a clone of the repo), else out of the AppImage
desktop="$src/simplehome-browser.desktop" png="$src/simplehome-browser.png"
if [[ ! -f $desktop || ! -f $png ]]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  (cd "$tmp" && "$HOME/Applications/SimpleHome-Browser-x86_64.AppImage" --appimage-extract 'simplehome-browser.*' >/dev/null)
  desktop="$tmp/squashfs-root/simplehome-browser.desktop" png="$tmp/squashfs-root/simplehome-browser.png"
fi
cp "$png" "$data/icons/hicolor/256x256/apps/simplehome-browser.png"
gtk-update-icon-cache "$data/icons/hicolor" &>/dev/null || true

sed "s|^Exec=simplehome-browser|Exec=$HOME/.local/bin/simplehome-browser|" "$desktop" >"$data/applications/simplehome-browser.desktop"
update-desktop-database "$data/applications" &>/dev/null || true
echo "Installed SimpleHome Browser to ~/Applications and the app launcher (command: simplehome-browser)."

if (( default )); then
  xdg-settings set default-web-browser simplehome-browser.desktop
  for t in x-scheme-handler/http x-scheme-handler/https text/html application/xhtml+xml; do
    xdg-mime default simplehome-browser.desktop "$t"
  done
  echo "SimpleHome Browser is now the default web browser."
fi
