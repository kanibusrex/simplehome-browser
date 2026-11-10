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
sudo pacman -S mpv yt-dlp                        # optional: Play in mpv
./install.sh SimpleHome-Browser-x86_64.AppImage  # add --default to make it the default browser
```

`install.sh` puts the AppImage in `~/Applications`, adds a `simplehome-browser` command to `~/.local/bin` and adds an entry to the app launcher. With `--default` it also becomes the browser that links from other apps open in, and the one Omarchy's Super+Shift+B starts. Running the AppImage directly works too.

## What it does

**SimpleHome on every new tab.** Ctrl+T opens a tab with SimpleHome's clock, launcher, shortcuts, scratchpad, Drift and Horizon, with the cursor already in its search box. The page is served from `simplehome://home/`, so its shortcuts, theme and notes are saved and kept between restarts. Clicking a tile or pressing Enter in its search opens the site in that same tab, the way an address bar does. Ctrl-click or middle-click opens it in a new tab instead. Settings → Backup in SimpleHome can bring your shortcuts over from the Chrome extension.

**Auto-hide.** Turn on "Auto-hide toolbar and tabs" in the menu and the page gets the whole window. Push the pointer against the top edge of the window for the toolbar, or the left edge for the tabs. Each slides out over the page, so the page doesn't have to re-lay itself out, and slides away when the pointer moves back onto the page. The toolbar stays out while you're typing in the address bar or have one of its menus open. Ctrl+L brings it out with the cursor in the address bar, and opening or switching tabs shows the tab column for a moment.

**Sleeping tabs.** A page in a background tab keeps running: a hidden YouTube or news page used 400 to 470 MB and up to 85% of a core on this machine with nothing happening. So by default a tab goes to sleep as soon as you stop looking at it: 10 seconds after you switch away (or after a tab you opened behind has loaded), its page process is ended, and its memory and CPU go with it. The few seconds' grace mean glancing back at a tab, or Ctrl+Tab going past it, doesn't reload it every time. The tab keeps its title, icon and back/forward history, shows dimmed, and loads again when you click it (from the disk cache where it can); what you typed into a form on it and how far you'd scrolled are not kept. A tab playing sound, a pinned tab, the tab in front of each window, and a pop-up sharing its page process with another tab are never put to sleep. To keep background tabs running for a while instead, pick 5, 10 or 30 minutes, or Never, under "Sleep background tabs" in the menu. In a test with the home page, Wikipedia and CNN open, the page processes took 847 MB with all three awake and 386 MB a few seconds after leaving two of them.

**Pinned tabs.** For a site you keep going back to, like mail, right-click its tab and choose "Pin Tab" (or tick "Pin this tab" in the menu). A pinned tab moves to the top of the tabs and shows a pin where its close button was (just its icon when tabs are in a row on top), never goes to sleep, and comes back every time the browser starts, even with "Reopen last session's tabs" off. It loads in the background a few seconds after the window opens, once the page in front has loaded, so it's ready when you switch to it. Middle-clicking a pinned tab doesn't close it; Ctrl+W or "Close Tab" on its right-click menu does, and Ctrl+Shift+T brings it back pinned. On this machine Gmail takes about 8.5 s to show the inbox from scratch (Zen took 15–17 s on the same machine), so with sleeping tabs every return to it meant that wait; pinned, switching back shows the inbox in under a second.

A background tab that keeps using the CPU doesn't wait out a timed sleep (5, 10 or 30 minutes): one using at least 10% of a core for a minute (two checks, 30 s apart) is put to sleep then. A hidden CNN page used 40% of a core, so it sleeps after about a minute; a quiet page like mail or docs keeps the full wait. WebKit doesn't say which process belongs to which tab, so a small helper loaded into each page's process (`ext/simplehome-pid.so`, built from `ext/pidreport.c`) reports it. Without the helper, tabs sleep by time alone.

**Off-screen windows.** Hyprland keeps a window on a workspace you've left running as if it were on screen, so its page never knows it's hidden and keeps animating: SimpleHome's galaxy theme used half a core there. The browser listens to Hyprland, and two seconds after its window leaves the screen (another workspace, a closed special workspace, a hidden group tab, or another window full screen over it) it hides the page, so the page stops its timers and animations. SimpleHome's background went from 52% of a core to 0%. The page is shown again as soon as the window is back. A page playing sound is left alone. This only happens on Hyprland.

**Memory.** Each page's process is told to give memory back sooner than WebKit's default, which only starts at 1 GB, a size a single tab on an 8 GB machine never reaches. Here the limit is a sixth of the RAM (768 MB to 3 GB): caches are dropped from a third of it, harder from half, and nothing is ever killed. A CNN tab settled at 567 MB instead of 621 MB.

**Fast starts.** Restored tabs wait to load until you open them, so only the one in front loads at startup. A web page opened at launch (a link from another app) waits for the stored ad-block filter, at most 1.5 s, so it doesn't load unfiltered.

**Connections ready before you click.** Hold the pointer on a link for a moment, or type an address, and the browser gets a connection to that site ready (name lookup, TCP and TLS) before you open it. Measured here, the first byte of a site came back 35–150 ms after opening it this way, against 190–380 ms from cold. Clicking into the address bar does the same for the search engine. Only the connection is made; nothing is requested from the site and no cookies are sent until you actually go there.

**Tabs.** Tabs sit in a column on the left, under the toolbar, with New tab at the top. Long titles get the room a row of tabs never has. To put them back in a row along the top, turn off "Tabs on the side" in the menu. You can drag tabs to reorder them, middle-click one to close it, and scroll the list when it overflows. Your tabs come back the next time you open the browser (you can turn that off in the menu). Links from other apps, and a second launch, open as tabs in the window you used last.

**Address bar.** Type an address or a search. `localhost:3000`, IP addresses and `.local` hosts go over http; anything with a space, or without a domain, is searched. Suggestions come from the sites you've visited, ranked by how often and how recently. The address bar uses the same search engine as SimpleHome's search box: change one and the other follows. A lock shows when the connection is secure. The copy button at the end of the address box copies the page's full address, and so does "Copy Page Address" when you right-click a page.

**Ad and tracker blocking.** EasyList and EasyPrivacy, plus Fanboy's Annoyances if you want it, are turned into WebKit's built-in content blocker, the format Safari's blockers use. The blocking runs inside WebKit itself, with nothing in the browser's own code on the path. The first conversion (about 115,000 rules) takes about half a minute in the background. After that it loads instantly, and the lists refresh once a day. From the shield button you can turn blocking off for the site you're on (this takes effect at once, without recompiling), switch lists, or update now.

The lists' hiding rules meant for every site (`##.ad-box`) are left out by default. WebKit puts them into one style sheet that every page and frame has to read and check its elements against: about 13,600 selectors with EasyList, about 40,800 with Annoyances added. On this machine (an Atom E3950), with all three lists, dropping them made pages appear about a second sooner: Wikipedia's first paint went from 2.7–3.0 s to 2.0–2.1 s, GitHub's from 2.9 s to 2.1 s. BBC's home page, which seemed to fight the hiding for over ten seconds, finished in 3.5 s instead of 12. The ads and trackers themselves are still blocked, since that happens at the network level, and the hiding written for particular sites stays. What you may see is an empty box where a blocked ad would have been on some sites. To have every-site hiding back, tick "Also hide leftover ad spaces on every site" in the shield menu. The converter is a port of the SimpleHome extension's parser and works the same way: a filter that uses something WebKit can't express is dropped whole rather than approximated.

**1Password.** Press Ctrl+Shift+L, or click the key at the end of the address bar, to fill the login for the site you're on from 1Password. With one match it fills straight away; with several it lists them. On a page that asks for a one-time code, it fills the code instead. When it fills a password for a login that also has a one-time code, it copies the code for the next step. After you sign in somewhere with a login 1Password doesn't have, a bar offers to save it (Save, Not now, or Never for this site).

It works through 1Password's command-line tool, `op`, which needs the 1Password app installed and Settings → Developer → **Integrate with 1Password CLI** turned on. The first time, and again whenever 1Password has locked, 1Password asks to **allow SimpleHome Browser to get CLI access**: click Authorize. If 1Password is locked, its own unlock prompt appears; the browser never sees your 1Password password.

That prompt names whichever program is asking. The browser runs on Python, so the AppImage runs it under a copy of the system's `python3` named `SimpleHome Browser`, set up like a Python virtual environment, in `~/.local/share/simplehome-browser/runtime/`. It is refreshed when the system's Python changes, and the browser falls back to plain `python3` if anything is wrong with it. Run from source, the prompt names `/usr/bin/python3` instead.

- **When it fills:** only when you ask, only into the page itself (not into frames inside it), and only when the page's site is the login's own site or a subdomain of it, so `github.com.evil.net` gets nothing. This is checked again inside the page just before filling. Sites on plain http aren't filled, except ones on your own network.
- **How it fills:** the fill and save scripts run in their own JavaScript world, so the page's scripts can't intercept them. Passwords are given to `op` on its standard input, never on its command line, and a login waiting to be saved is held in memory for two minutes at most.
- **More than one account:** if you're signed in to several 1Password accounts, set `"op_account"` in `prefs.json` to the one to use.

If filling fails, `~/.cache/simplehome-browser/1password.log` records each call to `op` (when, which command, how it ended, and which programs started the browser). It never records titles, usernames or passwords. Starting the browser with `SIMPLEHOME_SELFTEST_1P=1` asks for the login list once, 5 seconds after starting, and logs the result there.

Both parts can be turned off in the menu under 1Password. Passkeys, cards and addresses aren't filled.

**Play in mpv.** On a slow computer YouTube's watch page can spend 10–20 seconds running its own script (building the suggestions and comments) before the video settles. Right-click a YouTube link and choose Play Link in mpv, or press Ctrl+Shift+M on a video's page, and the video opens in mpv instead, in a few seconds, decoded by the graphics chip. From the page it picks up where the video was and pauses the page's copy, so the tab stays open for the comments. It asks for H.264 (or VP9) up to 1080p, never AV1, which older graphics chips can't decode. If mpv can't play a video (age-restricted or members-only, say) the corner of the window says why; mpv's output is in `~/.cache/simplehome-browser/mpv.log`. Needs `mpv` and `yt-dlp`.

**Bookmarks.** Press Ctrl+D or click the star at the end of the address bar to bookmark the page. A small box opens where you can rename it or remove it again; the star is filled on any page you've bookmarked. The bookmarks button next to the shield (or Ctrl+Shift+O) lists them newest first, with a search box: click one to open it, middle-click or Ctrl+click to open it in a new tab, or click × to delete it. Bookmarks also come first in the address bar's suggestions, marked with ★. Private windows can save bookmarks too, since saving one is something you do on purpose.

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
| Ctrl+D / Ctrl+Shift+O | Bookmark this page / show bookmarks |
| Ctrl+Shift+L | Fill from 1Password |
| Ctrl+Shift+M | Play this YouTube video in mpv |
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

- `~/.local/share/simplehome-browser/` — `prefs.json` (session, zoom, permissions, blocking choices), `history.json`, `bookmarks.json`, and `web/` (cookies and site storage, including SimpleHome's).
- `~/.cache/simplehome-browser/` — downloaded filter lists, the compiled blocker, favicons and the web cache.

Set `SIMPLEHOME_DEBUG=1` to print what the blocker is doing. Set `SIMPLEHOME_OP` to use a different `op` binary (the tests use a stand-in). Set `SIMPLEHOME_DATA`, `SIMPLEHOME_CACHE` and `SIMPLEHOME_APP_ID` to run a separate test copy next to your real one.

## Building

```sh
python3 -m unittest discover tests   # filter converter, 1Password matching, mpv links, bookmarks, Hyprland visibility
ext/build.sh                         # the page-process helper (needs gcc and webkit2gtk-4.1's headers)
./build-appimage.sh                  # → dist/SimpleHome-Browser-x86_64.AppImage (needs appimagetool; builds the helper too)
```

To update the bundled home page, copy SimpleHome's `index.html` into `home/` and write its commit into `home/SOURCE`.
