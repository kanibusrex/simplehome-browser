# SimpleHome Browser

A small tabbed web browser for Linux whose new tab page is [SimpleHome](https://github.com/kanibusrex/SimpleHome). It is Python on top of the system's GTK 3 and WebKitGTK 4.1. The AppImage is about 1.4 MB, because the engine comes from your distribution and so do its security updates.

```
browser.py      the browser: windows, tabs, address bar, downloads, permissions, theme
adblock.py      EasyList → WebKit content blocker (no GTK; tested in tests/)
onepassword.py  1Password through its `op` tool: which logins belong to which site (no GTK; tested)
passwords.py    filling and the save bar
home/           SimpleHome's index.html, bundled as the new tab page (home/SOURCE = its commit)
```

## Install

```sh
sudo pacman -S python-gobject webkit2gtk-4.1     # if they aren't there already
sudo pacman -S gst-plugin-va                     # video decoded by the graphics chip instead of the CPU
./install.sh SimpleHome-Browser-x86_64.AppImage  # add --default to make it the default browser
```

`install.sh` puts the AppImage in `~/Applications`, adds a `simplehome-browser` command to `~/.local/bin` and adds an entry to the app launcher. With `--default` it also becomes the browser that links from other apps open in, and the one Omarchy's Super+Shift+B starts. Running the AppImage directly works too.

## What it does

**SimpleHome on every new tab.** Ctrl+T opens a tab with SimpleHome's clock, launcher, shortcuts, scratchpad, Drift and Horizon, with the cursor already in its search box. The page is served from `simplehome://home/`, so its shortcuts, theme and notes are saved and kept between restarts. Clicking a tile or pressing Enter in its search opens the site in that same tab, the way an address bar does. Ctrl-click or middle-click opens it in a new tab instead. Settings → Backup in SimpleHome can bring your shortcuts over from the Chrome extension.

**Fast starts.** Restored tabs wait to load until you open them, so only the one in front loads at startup.

**Tabs.** Tabs sit in a column on the left, under the toolbar, with New tab at the top. Long titles get the room a row of tabs never has. To put them back in a row along the top, turn off "Tabs on the side" in the menu. You can drag tabs to reorder them, middle-click one to close it, and scroll the list when it overflows. Your tabs come back the next time you open the browser (you can turn that off in the menu). Links from other apps, and a second launch, open as tabs in the window you used last.

**Address bar.** Type an address or a search. `localhost:3000`, IP addresses and `.local` hosts go over http; anything with a space, or without a domain, is searched. Suggestions come from the sites you've visited, ranked by how often and how recently. The address bar uses the same search engine as SimpleHome's search box: change one and the other follows. A lock shows when the connection is secure.

**Ad and tracker blocking.** EasyList and EasyPrivacy, plus Fanboy's Annoyances if you want it, are turned into WebKit's built-in content blocker, the format Safari's blockers use. The blocking runs inside WebKit itself, so pages pay nothing extra for it. The first conversion (about 115,000 rules) takes about half a minute in the background. After that it loads instantly, and the lists refresh once a day. From the shield button you can turn blocking off for the site you're on (this takes effect at once, without recompiling), switch lists, or update now. The converter is a port of the SimpleHome extension's parser and works the same way: a filter that uses something WebKit can't express is dropped whole rather than approximated.

**1Password.** Press Ctrl+Shift+L, or click the key at the end of the address bar, to fill the login for the site you're on from 1Password. With one match it fills straight away; with several it lists them. On a page that asks for a one-time code, it fills the code instead. When it fills a password for a login that also has a one-time code, it copies the code for the next step. After you sign in somewhere with a login 1Password doesn't have, a bar offers to save it (Save, Not now, or Never for this site).

It works through 1Password's command-line tool, `op`, which needs the 1Password app installed and Settings → Developer → **Integrate with 1Password CLI** turned on. The first time, and again whenever 1Password has locked, 1Password asks to **allow SimpleHome Browser to get CLI access**: click Authorize. If 1Password is locked, its own unlock prompt appears; the browser never sees your 1Password password.

That prompt names whichever program is asking. The browser runs on Python, so the AppImage runs it under a copy of the system's `python3` named `SimpleHome Browser`, set up like a Python virtual environment, in `~/.local/share/simplehome-browser/runtime/`. It is refreshed when the system's Python changes, and the browser falls back to plain `python3` if anything is wrong with it. Run from source, the prompt names `/usr/bin/python3` instead.

- **When it fills:** only when you ask, only into the page itself (not into frames inside it), and only when the page's site is the login's own site or a subdomain of it, so `github.com.evil.net` gets nothing. This is checked again inside the page just before filling. Sites on plain http aren't filled, except ones on your own network.
- **How it fills:** the fill and save scripts run in their own JavaScript world, so the page's scripts can't intercept them. Passwords are given to `op` on its standard input, never on its command line, and a login waiting to be saved is held in memory for two minutes at most.
- **More than one account:** if you're signed in to several 1Password accounts, set `"op_account"` in `prefs.json` to the one to use.

If filling fails, `~/.cache/simplehome-browser/1password.log` records each call to `op` (when, which command, how it ended, and which programs started the browser). It never records titles, usernames or passwords. Starting the browser with `SIMPLEHOME_SELFTEST_1P=1` asks for the login list once, 5 seconds after starting, and logs the result there.

Both parts can be turned off in the menu under 1Password. Passkeys, cards and addresses aren't filled.

**Private windows.** Ctrl+Shift+N opens a window that keeps nothing: no history, no cookies, no site data, no saved permissions.

**Downloads.** Files go to `~/Downloads`. A name that's already taken gets `(2)` added. A finished download sends a notification, and the downloads button lists recent downloads with Open and Show in folder.

**Site permissions.** Camera, microphone, location, notifications and clipboard are each asked once per site and remembered. Click the lock in the address bar to forget a site's answers. SimpleHome itself may always show notifications, so Horizon's alerts work.

**Omarchy.** The tab strip, toolbar and popovers take the current Omarchy theme's colours and font, and change with it live. Pages get dark mode when the theme is dark.

## Keys

| | |
|---|---|
| Ctrl+T / Ctrl+W | New tab / close tab |
| Ctrl+Shift+T | Reopen the last closed tab |
| Ctrl+Tab, Ctrl+PgDn / Ctrl+Shift+Tab, Ctrl+PgUp | Next / previous tab |
| Ctrl+1…8, Ctrl+9 | Go to tab 1…8, last tab |
| Ctrl+Shift+PgUp / PgDn | Move the tab up / down (left / right in a row) |
| Ctrl+L, Alt+D, F6 | Address bar |
| Ctrl+R, F5 / Ctrl+Shift+R | Reload / reload without the cache |
| Alt+Left / Alt+Right, mouse back/forward | Back / forward |
| Alt+Home | SimpleHome |
| Ctrl+F, Ctrl+G / Ctrl+Shift+G | Find, next / previous match |
| Ctrl+Shift+L | Fill from 1Password |
| Ctrl+= / Ctrl+- / Ctrl+0 | Zoom in / out / reset (remembered per site) |
| Ctrl+N / Ctrl+Shift+N | New window / new private window |
| Ctrl+P, Ctrl+S | Print, save the page to Downloads |
| F11, F12 | Full screen, developer tools |
| Ctrl+Q | Quit (the session is kept) |

SimpleHome's own keys still work on the new tab page: Alt+1–9 opens a tile, and F, Z, H, `/` work as usual.

## Limits

- **Video needs `gst-plugin-va`.** Without it, WebKit decodes video on the CPU, which on a low-power machine is slow and makes everything else slow while a video plays. With it, H.264, VP9 and HEVC are decoded by Intel and AMD graphics chips through VA-API.
- **No DRM.** WebKitGTK has no Widevine, so Netflix, Disney+ and Spotify's web player won't play.
- **Site compatibility.** WebKit is Safari's engine, so a site that only tests in Chrome sometimes has rough edges.
- **No extensions.** Chrome extensions don't run here. 1Password is built in instead (see above).

## Where things are kept

- `~/.local/share/simplehome-browser/` — `prefs.json` (session, zoom, permissions, blocking choices), `history.json`, and `web/` (cookies and site storage, including SimpleHome's).
- `~/.cache/simplehome-browser/` — downloaded filter lists, the compiled blocker, favicons and the web cache.

Set `SIMPLEHOME_DEBUG=1` to print what the blocker is doing. Set `SIMPLEHOME_OP` to use a different `op` binary (the tests use a stand-in). Set `SIMPLEHOME_DATA`, `SIMPLEHOME_CACHE` and `SIMPLEHOME_APP_ID` to run a separate test copy next to your real one.

## Building

```sh
python3 -m unittest discover tests   # the filter converter and the 1Password matching
./build-appimage.sh                  # → dist/SimpleHome-Browser-x86_64.AppImage (needs appimagetool)
```

To update the bundled home page, copy SimpleHome's `index.html` into `home/` and write its commit into `home/SOURCE`.
