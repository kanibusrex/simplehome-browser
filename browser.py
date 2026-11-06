#!/usr/bin/env python3
"""SimpleHome Browser: a small tabbed web browser whose new tab page is SimpleHome.

GTK 3 and WebKitGTK 4.1 from the system, Python on top. One process holds every window
(Gtk.Application), so a second launch, or a link opened from another app, becomes a tab here.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, WebKit2  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import adblock  # noqa: E402
import passwords  # noqa: E402

VERSION = "1.3.0"
APP_ID = "dev.simplehome.Browser"
NAME = "SimpleHome Browser"
DESKTOP_ID = "simplehome-browser.desktop"
HOME_URI = "simplehome://home/"
# SIMPLEHOME_DATA / SIMPLEHOME_CACHE point a test run somewhere other than the real profile
DATA = os.environ.get("SIMPLEHOME_DATA") or os.path.join(GLib.get_user_data_dir(), "simplehome-browser")
CACHE = os.environ.get("SIMPLEHOME_CACHE") or os.path.join(GLib.get_user_cache_dir(), "simplehome-browser")
OMARCHY_STATE = os.path.join(GLib.get_user_state_dir(), "omarchy", "current")
DEBUG = bool(os.environ.get("SIMPLEHOME_DEBUG"))

ENGINES = {
    "google": ("Google", "https://www.google.com/search?q="),
    "duckduckgo": ("DuckDuckGo", "https://duckduckgo.com/?q="),
    "bing": ("Bing", "https://www.bing.com/search?q="),
}
ZOOMS = [0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]


def log(*a):
    if DEBUG:
        print("[simplehome]", *a, file=sys.stderr, flush=True)


def host_of(uri):
    try:
        return (urllib.parse.urlsplit(uri or "").hostname or "").lower()
    except ValueError:
        return ""


def is_web(uri):
    return bool(uri) and uri.startswith(("http://", "https://"))


def site_of(host):
    """'www.example.com' -> 'example.com': what "on this site" means for the shield."""
    return host[4:] if host.startswith("www.") else host


# ---------------------------------------------------------------- stored state

class Store:
    """A JSON file, written a moment after the last change."""

    def __init__(self, name, default):
        self.path = os.path.join(DATA, name)
        self.data = default
        self._t = 0
        try:
            with open(self.path) as f:
                loaded = json.load(f)
            if isinstance(loaded, type(default)):
                if isinstance(default, dict):
                    default.update(loaded)
                    self.data = default
                else:
                    self.data = loaded
        except (OSError, ValueError):
            pass

    def changed(self, delay=800):
        if self._t:
            GLib.source_remove(self._t)
        self._t = GLib.timeout_add(delay, self.flush)

    def flush(self):
        if self._t:
            GLib.source_remove(self._t)
        self._t = 0
        os.makedirs(DATA, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)
        return False


class History:
    """Sites visited (never in private windows), ranked by how often and how lately."""
    LIMIT = 5000

    def __init__(self):
        self.store = Store("history.json", [])
        self.by_url = {h["u"]: h for h in self.store.data if isinstance(h, dict) and "u" in h}
        self.model = Gtk.ListStore(str, str, str)  # url, title, url shown without the scheme
        self._model_t = 0
        self.rebuild_model()

    def score(self, h):
        age_days = (time.time() - h.get("t", 0)) / 86400
        return h.get("n", 1) / (1 + age_days / 7)

    def add(self, url, title=""):
        if not is_web(url):
            return
        h = self.by_url.get(url)
        if h:
            h["n"] = h.get("n", 0) + 1
            h["t"] = time.time()
            if title:
                h["ti"] = title
        else:
            self.by_url[url] = {"u": url, "ti": title, "n": 1, "t": time.time()}
        self._save()

    def set_title(self, url, title):
        h = self.by_url.get(url)
        if h and title and h.get("ti") != title:
            h["ti"] = title
            self._save()

    def _save(self):
        if len(self.by_url) > self.LIMIT:
            keep = sorted(self.by_url.values(), key=self.score, reverse=True)[: self.LIMIT]
            self.by_url = {h["u"]: h for h in keep}
        self.store.data = list(self.by_url.values())
        self.store.changed(2000)
        if not self._model_t:
            self._model_t = GLib.timeout_add(3000, self.rebuild_model)

    def rebuild_model(self):
        self._model_t = 0
        self.model.clear()
        for h in sorted(self.by_url.values(), key=self.score, reverse=True)[:1500]:
            shown = re.sub(r"^https?://(www\.)?", "", h["u"]).rstrip("/")
            self.model.append([h["u"], h.get("ti") or "", shown])
        return False

    def clear(self):
        self.by_url = {}
        self.store.data = []
        self.store.flush()
        self.rebuild_model()


# ---------------------------------------------------------------- ad blocking

class Blocker:
    """Keeps the compiled WebKit content blocker up to date and hands it to every tab."""
    ID = "simplehome-lists"
    MAX_AGE = 24 * 3600

    def __init__(self, app):
        self.app = app
        self.prefs = app.prefs
        self.filter = None
        self.busy = False
        self.status = "Loading filter lists…"
        self.store = WebKit2.UserContentFilterStore.new(os.path.join(CACHE, "filters"))
        self.lists_dir = os.path.join(CACHE, "lists")
        self.listeners = []

    @property
    def enabled(self):
        return self.prefs.data.get("block", True)

    def wanted(self):
        on = self.prefs.data.get("lists")
        if not isinstance(on, list):
            on = [k for k, v in adblock.LISTS.items() if v["default"]]
        return [k for k in on if k in adblock.LISTS]

    def is_off_for(self, host):
        allow = self.prefs.data.get("allow", [])
        h = site_of(host)
        return any(h == a or h.endswith("." + a) for a in allow)

    def applies(self, uri):
        return bool(self.filter) and self.enabled and is_web(uri) and not self.is_off_for(host_of(uri))

    def apply_to(self, ucm, uri):
        """Add or remove the filter on one tab's content manager for the page it is going to."""
        want = self.applies(uri)
        if getattr(ucm, "_sh_filter", None) is self.filter and getattr(ucm, "_sh_on", False) == want:
            return
        ucm.remove_all_filters()
        if want:
            ucm.add_filter(self.filter)
        ucm._sh_filter, ucm._sh_on = self.filter, want

    def notify(self):
        for cb in list(self.listeners):
            cb()

    def start(self):
        self.store.load(self.ID, None, self._loaded)
        GLib.timeout_add_seconds(6 * 3600, lambda: (self.update(), True)[1])

    def _loaded(self, store, res):
        try:
            self.filter = store.load_finish(res)
        except GLib.Error:
            self.filter = None
        info = self.prefs.data.get("blockinfo") or {}
        if self.filter:
            self.status = self.describe(info)
            self.app.refilter()
        stale = time.time() - info.get("when", 0) > self.MAX_AGE
        if not self.filter or stale or info.get("lists") != sorted(self.wanted()):
            # give the first page a head start before the heavy lifting
            GLib.timeout_add_seconds(3 if self.filter else 1, lambda: self.update() and False)
        self.notify()

    def describe(self, info):
        if not info.get("rules"):
            return "No lists turned on" if not self.wanted() else "Not set up yet"
        when = info.get("when", 0)
        ago = time.time() - when
        age = ("just now" if ago < 90 else f"{int(ago // 60)} min ago" if ago < 3600
               else f"{int(ago // 3600)} h ago" if ago < 86400 else f"{int(ago // 86400)} d ago")
        return f"{info['rules']:,} rules · lists updated {age}"

    def update(self, force=False):
        if self.busy:
            return False
        self.busy = True
        self.status = "Updating filter lists…"
        self.notify()
        keys = self.wanted()
        threading.Thread(target=self._fetch_and_build, args=(keys, force), daemon=True).start()
        return True

    def _fetch_and_build(self, keys, force):
        os.makedirs(self.lists_dir, exist_ok=True)
        texts, failed = [], []
        for k in keys:
            path = os.path.join(self.lists_dir, k + ".txt")
            fresh = os.path.exists(path) and time.time() - os.path.getmtime(path) < self.MAX_AGE
            if force or not fresh:
                try:
                    req = urllib.request.Request(adblock.LISTS[k]["url"], headers={"User-Agent": f"SimpleHome-Browser/{VERSION}"})
                    with urllib.request.urlopen(req, timeout=40) as r:
                        body = r.read().decode("utf-8", "replace")
                    if "[Adblock" not in body[:200] and "!" not in body[:50]:
                        raise ValueError("not a filter list")
                    with open(path + ".tmp", "w") as f:
                        f.write(body)
                    os.replace(path + ".tmp", path)
                except (OSError, ValueError) as e:
                    log("list fetch failed", k, e)
                    failed.append(adblock.LISTS[k]["name"])
            try:
                with open(path) as f:
                    texts.append(f.read())
            except OSError:
                pass
        if keys and not texts:
            GLib.idle_add(self._done, None, "Couldn't download the filter lists" + (f" ({', '.join(failed)})" if failed else ""))
            return
        t = time.time()
        rules, stats = adblock.build(texts)
        log("built", stats["rules"], "rules in", round(time.time() - t, 1), "s")
        GLib.idle_add(self._validate, rules, keys, failed)

    def _validate(self, rules, keys, failed):
        """WebKit skips a whole selector list over one selector it can't parse, so find those
        first with WebKit's own parser, in a page no one sees."""
        sels = set()
        for s in adblock.selectors_of(rules):
            sels.update(adblock.split_selectors(s))
        view = WebKit2.WebView(web_context=self.app.context)
        script = ("(() => { const bad = []; for (const s of %s) { try { document.querySelector(s) }"
                  " catch (e) { bad.push(s) } } return JSON.stringify(bad) })()" % json.dumps(sorted(sels)))

        def done(v, res):
            try:
                bad = set(json.loads(v.evaluate_javascript_finish(res).to_string()))
            except (GLib.Error, ValueError):
                bad = set()
            log("invalid selectors", len(bad))
            v.destroy()
            threading.Thread(target=self._compile, args=(rules, bad, keys, failed), daemon=True).start()

        def loaded(v, ev):
            if ev == WebKit2.LoadEvent.FINISHED:
                v.disconnect_by_func(loaded)
                v.evaluate_javascript(script, -1, None, None, None, done)
        view.connect("load-changed", loaded)
        view.load_html("<!doctype html><title>check</title>", "about:blank")
        view._keep = view  # kept alive until done
        self._check_view = view
        return False

    def _compile(self, rules, bad, keys, failed):
        rules = adblock.drop_selectors(rules, bad)
        data = GLib.Bytes.new(json.dumps(rules, separators=(",", ":")).encode())
        GLib.idle_add(self._save, data, len(rules), keys, failed)

    def _save(self, data, count, keys, failed):
        t = time.time()

        def saved(store, res):
            try:
                self.filter = store.save_finish(res)
            except GLib.Error as e:
                self._done(None, "Couldn't compile the filter lists: " + e.message)
                return
            log("compiled in", round(time.time() - t, 1), "s")
            info = {"when": time.time(), "rules": count, "lists": sorted(keys)}
            self.prefs.data["blockinfo"] = info
            self.prefs.changed()
            self._done(self.filter, self.describe(info) + (f" · couldn't refresh {', '.join(failed)}" if failed else ""))
        if not keys:
            self.store.remove(self.ID, None, lambda s, r: None)
            self.prefs.data["blockinfo"] = {"when": time.time(), "rules": 0, "lists": []}
            self.filter = None
            self._done(None, "No lists turned on")
            return False
        self.store.save(self.ID, data, None, saved)
        return False

    def _done(self, flt, status):
        self.busy = False
        self.status = status
        self.app.refilter()
        self.notify()
        return False


# ---------------------------------------------------------------- Omarchy theme

def omarchy_palette():
    exe = shutil.which("omarchy-theme-color")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--all"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    pal = dict(ln.split("\t", 1) for ln in out.splitlines() if "\t" in ln)
    pal = {k.strip(): v.strip() for k, v in pal.items()}
    if not pal.get("background") or not pal.get("foreground"):
        return None
    try:
        pal["font"] = subprocess.run(["fc-match", "monospace", "-f", "%{family}"], capture_output=True,
                                     text=True, timeout=5).stdout.split(",")[0].strip()
    except (OSError, subprocess.TimeoutExpired):
        pal["font"] = ""
    return pal


def luminance(hexcol):
    h = hexcol.lstrip("#")
    if len(h) < 6:
        return 0.0
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


BASE_CSS = """
.sh-tabs > header { padding: 4px 4px 0 4px; }
.sh-tabs > header tab { padding: 4px 6px 4px 10px; min-height: 22px; min-width: 60px; }
.sh-tabs > header tab button { padding: 0; min-height: 18px; min-width: 18px; }
.sh-tabs stack { min-height: 0; }
.sh-tabs { border: none; }
.sh-tabs.sh-side > header { padding: 6px 4px 6px 6px; }
.sh-tabs.sh-side > header tab { padding: 6px 6px 6px 10px; margin: 1px 0; min-height: 24px; }
.sh-tabs.sh-side stack { min-width: 0; }
.sh-newtab { padding: 6px 10px; margin-bottom: 4px; }
.sh-savebar { padding: 6px 10px; }
.sh-bar { padding: 4px 6px 6px 6px; }
.sh-bar button { padding: 4px 6px; min-height: 22px; }
.sh-url { min-height: 30px; padding-left: 10px; border-radius: 8px; }
.sh-find { padding: 4px 8px; }
.sh-zoom { padding: 2px 8px; font-size: 90%; }
.sh-status { padding: 3px 8px; margin: 0 0 4px 4px; border-radius: 6px; font-size: 90%; }
.sh-private-label { padding: 0 8px; font-weight: bold; }
.sh-pop { padding: 12px; }
.sh-pop .dim-label { font-size: 90%; }
"""


def theme_css(p, private=False):
    bg, fg = p["background"], p["foreground"]
    acc = p.get("accent") or p.get("blue") or fg
    priv = p.get("magenta") or p.get("purple") or acc
    font = f'font-family: "{p["font"]}";' if p.get("font") else ""
    return f"""
@define-color sh_bg {bg};
@define-color sh_fg {fg};
@define-color sh_acc {acc};
@define-color sh_priv {priv};
@define-color sh_surface mix({bg}, {fg}, 0.07);
@define-color sh_raised mix({bg}, {fg}, 0.13);
@define-color sh_border mix({bg}, {fg}, 0.18);
@define-color sh_muted mix({bg}, {fg}, 0.6);
.sh-window, .sh-window .sh-bar, .sh-window .sh-find, .sh-tabs > header {{ background: @sh_bg; color: @sh_fg; {font} }}
.sh-window popover, .sh-window popover.background {{ background: @sh_surface; color: @sh_fg; border: 1px solid @sh_border; {font} }}
.sh-window popover modelbutton, .sh-window popover label {{ color: @sh_fg; }}
.sh-window popover modelbutton:hover {{ background: @sh_raised; }}
.sh-window popover separator {{ background: @sh_border; }}
.sh-tabs > header {{ border: none; box-shadow: none; }}
.sh-tabs > header tab {{ background: transparent; color: @sh_muted; border: none; border-radius: 8px 8px 0 0; box-shadow: none; }}
.sh-tabs > header tab:hover {{ background: @sh_surface; color: @sh_fg; }}
.sh-tabs > header tab:checked {{ background: @sh_surface; color: @sh_fg; box-shadow: inset 0 2px @sh_acc; }}
.sh-tabs.sh-side > header {{ box-shadow: inset -1px 0 @sh_border; }}
.sh-tabs.sh-side > header tab {{ border-radius: 8px; }}
.sh-tabs.sh-side > header tab:checked {{ box-shadow: inset 3px 0 @sh_acc; }}
.sh-newtab, .sh-newtab label {{ color: @sh_muted; }}
.sh-savebar {{ background: @sh_surface; color: @sh_fg; box-shadow: inset 0 -1px @sh_border; }}
.sh-savebar button {{ background: @sh_raised; color: @sh_fg; border: none; box-shadow: none; }}
.sh-savebar button.suggested-action {{ background: @sh_acc; color: @sh_bg; }}
.sh-window popover list, .sh-window popover row {{ background: transparent; color: @sh_fg; }}
.sh-window popover row:selected {{ background: @sh_raised; }}
.sh-newtab:hover, .sh-newtab:hover label {{ color: @sh_fg; }}
.sh-tabs > header tab label {{ color: inherit; }}
.sh-tabs > header button, .sh-bar button, .sh-find button {{ background: transparent; color: @sh_fg; border: none; box-shadow: none; text-shadow: none; -gtk-icon-shadow: none; }}
.sh-tabs > header button:hover, .sh-bar button:hover, .sh-find button:hover {{ background: @sh_raised; }}
.sh-bar button:checked {{ background: @sh_raised; color: @sh_acc; }}
.sh-bar button:disabled {{ color: alpha(@sh_fg, 0.3); }}
.sh-bar {{ background: @sh_surface; }}
.sh-window entry {{ {font} background: @sh_bg; color: @sh_fg; border: 1px solid @sh_border; box-shadow: none; caret-color: @sh_acc; }}
.sh-window entry:focus {{ border-color: @sh_acc; }}
.sh-window entry selection {{ background: @sh_acc; color: @sh_bg; }}
.sh-window entry image {{ color: @sh_muted; }}
.sh-window entry progress {{ border: none; border-bottom: 2px solid @sh_acc; background: none; }}
.sh-window entry.sh-nomatch {{ border-color: {p.get('red', '#e06c75')}; }}
.sh-shield-on, .sh-shield-on image {{ color: @sh_acc; }}
.sh-status {{ background: @sh_surface; color: @sh_muted; border: 1px solid @sh_border; }}
.sh-window.sh-private .sh-bar {{ box-shadow: inset 0 -2px @sh_priv; }}
.sh-private-label {{ color: @sh_priv; }}
.sh-window switch:checked {{ background: @sh_acc; }}
.sh-window checkbutton check:checked {{ background: @sh_acc; color: @sh_bg; }}
.sh-window .sh-dl progressbar progress {{ background: @sh_acc; }}
"""


# ---------------------------------------------------------------- a tab

class Tab:
    def __init__(self, win, view):
        self.win = win
        self.view = view
        self.private = win.private
        self.title = "New tab"
        self.zoom_set = False
        # the notebook only draws the tab strip; the page itself lives in the window's stack
        self.slot = Gtk.Box()
        self.label = Gtk.Label(label=self.title, ellipsize=Pango.EllipsizeMode.END, xalign=0)
        self.set_vertical(win.app.prefs.data.get("vertical_tabs", True))
        self.icon = Gtk.Image.new_from_icon_name("web-browser-symbolic", Gtk.IconSize.MENU)
        self.spinner = Gtk.Spinner()
        self.lead = Gtk.Stack()
        self.lead.add_named(self.icon, "icon")
        self.lead.add_named(self.spinner, "spin")
        close = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.set_tooltip_text("Close tab (Ctrl+W)")
        close.connect("clicked", lambda *_: win.close_tab(self))
        box = Gtk.Box(spacing=6)
        box.pack_start(self.lead, False, False, 0)
        box.pack_start(self.label, True, True, 0)
        box.pack_start(close, False, False, 0)
        self.head = Gtk.EventBox()
        self.head.add(box)
        self.head.connect("button-press-event", self.on_head_press)
        self.head.show_all()
        self.slot.show()
        view.show()

        view.connect("notify::title", self.on_title)
        view.connect("notify::uri", self.on_uri)
        view.connect("notify::favicon", self.on_favicon)
        view.connect("notify::estimated-load-progress", self.on_progress)
        view.connect("load-changed", self.on_load)
        view.connect("create", self.on_create)
        view.connect("decide-policy", self.on_policy)
        view.connect("close", lambda *_: win.close_tab(self))
        view.connect("enter-fullscreen", lambda *_: win.set_page_fullscreen(True) or False)
        view.connect("leave-fullscreen", lambda *_: win.set_page_fullscreen(False) or False)
        view.connect("permission-request", self.on_permission)
        view.connect("load-failed-with-tls-errors", self.on_tls)
        view.connect("web-process-terminated", self.on_crash)
        view.connect("context-menu", self.on_context_menu)
        view.connect("mouse-target-changed", self.on_hover)
        view.connect("button-press-event", self.on_button)
        view.connect("show-notification", lambda *_: False)
        fc = view.get_find_controller()
        fc.connect("counted-matches", lambda c, n: win.find_count(self, n))
        fc.connect("found-text", lambda c, n: win.find_count(self, n, found=True))
        fc.connect("failed-to-find-text", lambda c: win.find_count(self, 0))

    @property
    def uri(self):
        return self.view.get_uri() or ""

    def set_vertical(self, vertical):
        # a side column is as wide as its widest tab, so every tab gets the same fixed width there
        self.label.set_width_chars(22 if vertical else 16)
        self.label.set_max_width_chars(22)

    def on_head_press(self, w, ev):
        if ev.button == 2:
            self.win.close_tab(self)
            return True
        return False

    def on_title(self, *_):
        t = self.view.get_title() or ""
        if self.uri.startswith(HOME_URI):
            t = "New tab"
        self.title = t or self.display_uri() or "New tab"
        self.label.set_text(self.title)
        self.head.set_tooltip_text(self.title)
        if not self.private:
            self.win.app.history.set_title(self.uri, t)
        self.win.tab_changed(self)

    def display_uri(self):
        u = self.uri
        return "" if u.startswith(HOME_URI) or u == "about:blank" else u

    def on_uri(self, *_):
        self.win.app.blocker.apply_to(self.view.get_user_content_manager(), self.uri)
        self.win.hide_save_for(self)
        if getattr(self, "filled_host", None) and host_of(self.uri) != self.filled_host:
            self.filled_host = None
        self.win.tab_changed(self, uri=True)
        self.win.save_session()

    def on_favicon(self, *_):
        surf = self.view.get_favicon()
        if surf is None:
            self.icon.set_from_icon_name("web-browser-symbolic", Gtk.IconSize.MENU)
            return
        try:
            pb = Gdk.pixbuf_get_from_surface(surf, 0, 0, surf.get_width(), surf.get_height())
            scale = self.icon.get_scale_factor()
            pb = pb.scale_simple(16 * scale, 16 * scale, 2)  # GdkPixbuf.InterpType.BILINEAR
            self.icon.set_from_surface(Gdk.cairo_surface_create_from_pixbuf(pb, scale, None))
        except (TypeError, AttributeError, GLib.Error):
            pass

    def on_progress(self, *_):
        self.win.tab_changed(self, progress=True)

    def on_load(self, view, ev):
        if ev == WebKit2.LoadEvent.STARTED:
            self.lead.set_visible_child_name("spin")
            self.spinner.start()
        elif ev == WebKit2.LoadEvent.COMMITTED:
            self.apply_zoom()
            if self.uri.startswith(HOME_URI):
                self.read_engine()
        elif ev == WebKit2.LoadEvent.FINISHED:
            self.spinner.stop()
            self.lead.set_visible_child_name("icon")
            if not self.private:
                self.win.app.history.add(self.uri, view.get_title() or "")
            if self.uri.startswith(HOME_URI) and self.win.current is self and not self.win.url.has_focus():
                # a new tab types straight into SimpleHome's own search, like an address bar
                view.grab_focus()
                view.evaluate_javascript("var i = document.getElementById('searchInput'); i && i.focus()",
                                         -1, None, None, None, None)
        self.win.tab_changed(self, load=True)

    def read_engine(self):
        """The address bar searches with whatever engine SimpleHome is set to."""
        def got(v, res):
            try:
                val = v.evaluate_javascript_finish(res).to_string()
            except GLib.Error:
                return
            for key, (_, url) in ENGINES.items():
                if val == url and self.win.app.prefs.data.get("engine") != key:
                    self.win.app.prefs.data["engine"] = key
                    self.win.app.prefs.changed()
                    self.win.app.engine_action.set_state(GLib.Variant("s", key))
        self.view.evaluate_javascript("localStorage.getItem('homepage.engine') || ''", -1, None, None, None, got)

    def apply_zoom(self):
        z = self.win.app.prefs.data.get("zoom", {}).get(host_of(self.uri))
        self.view.set_zoom_level(z or self.win.app.prefs.data.get("default_zoom", 1.0))
        self.win.tab_changed(self)

    # links that want a new tab
    def wants_new_tab(self, action):
        mods = action.get_modifiers()
        return action.get_mouse_button() == 2 or bool(mods & Gdk.ModifierType.CONTROL_MASK)

    def on_policy(self, view, decision, kind):
        if kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            action = decision.get_navigation_action()
            uri = action.get_request().get_uri()
            if (action.get_navigation_type() == WebKit2.NavigationType.LINK_CLICKED
                    and self.wants_new_tab(action) and is_web(uri)):
                self.win.new_tab(uri, background=True)
                decision.ignore()
                return True
            if action.get_frame_name() is None:
                self.win.app.blocker.apply_to(view.get_user_content_manager(), uri)
        elif kind == WebKit2.PolicyDecisionType.RESPONSE:
            if not decision.is_mime_type_supported() and decision.is_main_frame_main_resource():
                decision.download()
                return True
        return False

    def on_create(self, view, action):
        uri = action.get_request().get_uri()
        # from SimpleHome itself a plain click goes to the site in this tab, like an address bar
        if self.uri.startswith(HOME_URI) and not self.wants_new_tab(action) and is_web(uri):
            GLib.idle_add(lambda: self.view.load_uri(uri) and False)
            return None
        bg = action.get_mouse_button() == 2 or bool(action.get_modifiers() & Gdk.ModifierType.CONTROL_MASK)
        tab = self.win.new_tab(None, related=view, background=bg)
        return tab.view

    def on_hover(self, view, hit, mods):
        self.win.show_link(hit.get_link_uri() if hit.context_is_link() else "")

    def on_button(self, view, ev):
        if ev.type == Gdk.EventType.BUTTON_PRESS and ev.button in (8, 9):
            (view.go_back if ev.button == 8 else view.go_forward)()
            return True
        return False

    def on_context_menu(self, view, menu, event, hit):
        if hit.context_is_link():
            link = hit.get_link_uri()
            for i, item in enumerate(list(menu.get_items())):
                if item.get_stock_action() == WebKit2.ContextMenuAction.OPEN_LINK_IN_NEW_WINDOW:
                    menu.remove(item)
                    act = Gio.SimpleAction.new("open-link-tab", None)
                    act.connect("activate", lambda *_: self.win.new_tab(link, background=True))
                    menu.insert(WebKit2.ContextMenuItem.new_from_gaction(act, "Open Link in New _Tab", None), i)
                    act2 = Gio.SimpleAction.new("open-link-window", None)
                    act2.connect("activate", lambda *_: self.win.app.new_window([link], private=self.private))
                    menu.insert(WebKit2.ContextMenuItem.new_from_gaction(act2, "Open Link in New _Window", None), i + 1)
                    break
        return False

    def on_permission(self, view, req):
        return self.win.ask_permission(self, req)

    def on_tls(self, view, uri, cert, errors):
        host = host_of(uri)
        d = Gtk.MessageDialog(transient_for=self.win, modal=True, message_type=Gtk.MessageType.WARNING,
                              text=f"{host} doesn't have a certificate this browser trusts",
                              secondary_text="Someone could be pretending to be this site or reading what you send it. "
                                             "Continue only if you know why (a device on your own network, say).")
        d.add_buttons("Continue anyway", 1, "Go back", 0)
        d.set_default_response(0)
        r = d.run()
        d.destroy()
        if r == 1:
            self.win.app.context_for(self.private).allow_tls_certificate_for_host(cert, host)
            view.load_uri(uri)
        elif view.can_go_back():
            view.go_back()
        else:
            view.load_uri(HOME_URI)
        return True

    def on_crash(self, view, reason):
        uri = self.uri
        log("web process ended", uri, reason)
        # a one-off: load it again by itself; a page that keeps dying gets the message instead
        now = time.time()
        if reason != WebKit2.WebProcessTerminationReason.EXCEEDED_MEMORY_LIMIT and now - getattr(self, "_crashed_at", 0) > 60:
            self._crashed_at = now
            GLib.idle_add(lambda: view.load_uri(uri) and False)
            return
        why = "ran out of memory" if reason == WebKit2.WebProcessTerminationReason.EXCEEDED_MEMORY_LIMIT else "stopped"
        html = (f"<!doctype html><meta charset=utf-8><title>Page {why}</title><body style='font:16px system-ui;"
                f"padding:15vh 10vw;color:#999;background:#111'><h2>This page {why}.</h2>"
                f"<p>Press Ctrl+R to load it again.</p>")
        view.load_alternate_html(html, uri, uri)


# ---------------------------------------------------------------- a window

class Window(Gtk.ApplicationWindow, passwords.PasswordUI):
    def __init__(self, app, private=False):
        super().__init__(application=app, title=NAME)
        self.app = app
        self.private = private
        self.tabs = []
        self.closed = []
        self.current = None
        self.editing = False
        self.page_fullscreen = False
        self.set_default_size(1280, 820)
        self.set_icon_name("simplehome-browser")
        self.get_style_context().add_class("sh-window")
        if private:
            self.get_style_context().add_class("sh-private")

        self.notebook = Gtk.Notebook(scrollable=True, show_border=False)
        self.notebook.get_style_context().add_class("sh-tabs")
        self.notebook.set_vexpand(False)
        self.notebook.connect("switch-page", self.on_switch)
        self.notebook.connect("page-reordered", lambda *_: self.save_session())
        # "+" sits after the tabs in a row, and at the top of the column when tabs are on the side
        self.add_btns = {}
        for pack in (Gtk.PackType.START, Gtk.PackType.END):
            add = Gtk.Button.new_from_icon_name("list-add-symbolic", Gtk.IconSize.MENU)
            add.set_relief(Gtk.ReliefStyle.NONE)
            add.set_tooltip_text("New tab (Ctrl+T)")
            add.connect("clicked", lambda *_: self.new_tab())
            if pack == Gtk.PackType.START:
                add.set_always_show_image(True)
                add.set_label("New tab")
                add.get_style_context().add_class("sh-newtab")
                add.get_child().set_halign(Gtk.Align.START)
            add.set_no_show_all(True)
            self.notebook.set_action_widget(add, pack)
            self.add_btns[pack] = add

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)

        self.bar = self.build_bar()
        self.findbar = self.build_find()

        self.status = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=90)
        sbox = Gtk.Box()
        sbox.get_style_context().add_class("sh-status")
        sbox.add(self.status)
        sbox.set_halign(Gtk.Align.START)
        sbox.set_valign(Gtk.Align.END)
        sbox.set_no_show_all(True)
        self.status.show()
        self.status_rev = sbox
        self.overlay = Gtk.Overlay()
        self.overlay.add(self.stack)
        self.overlay.add_overlay(self.status_rev)
        self.overlay.set_overlay_pass_through(self.status_rev, True)

        self.build_pw()
        self._toast_t = 0
        self.vertical = None
        self.layout(app.prefs.data.get("vertical_tabs", True))
        self.connect("key-press-event", self.on_key)
        self.connect("delete-event", self.on_delete)
        self.connect("notify::is-active", lambda *_: self.is_active() and app.focused(self))
        self.app.blocker.listeners.append(self.update_shield)
        self.findbar.set_reveal_child(False)

    def layout(self, vertical):
        """Tabs on the side (a column left of the page, under the toolbar) or in a row on top.
        Only the tab strip moves: GTK 3 entries keep a stale font after being re-parented."""
        if vertical == self.vertical:
            return
        self.vertical = vertical
        if self.get_child() is None:
            self.top_slot = Gtk.Box()
            self.side_slot = Gtk.Box()
            body = Gtk.Box()
            body.pack_start(self.side_slot, False, False, 0)
            body.pack_start(self.overlay, True, True, 0)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.pack_start(self.top_slot, False, False, 0)
            box.pack_start(self.bar, False, False, 0)
            box.pack_start(self.savebar, False, False, 0)
            box.pack_start(body, True, True, 0)
            box.pack_start(self.findbar, False, False, 0)
            self.add(box)
            box.show_all()
        nb = self.notebook
        if nb.get_parent():
            nb.get_parent().remove(nb)
        nb.set_tab_pos(Gtk.PositionType.LEFT if vertical else Gtk.PositionType.TOP)
        ctx = nb.get_style_context()
        (ctx.add_class if vertical else ctx.remove_class)("sh-side")
        self.add_btns[Gtk.PackType.START].set_visible(vertical)
        self.add_btns[Gtk.PackType.END].set_visible(not vertical)
        for t in self.tabs:
            t.set_vertical(vertical)
        (self.side_slot if vertical else self.top_slot).pack_start(nb, True, True, 0)
        nb.show()
        if self.page_fullscreen:
            nb.hide()

    # -- toolbar
    def build_bar(self):
        bar = Gtk.Box(spacing=2)
        bar.get_style_context().add_class("sh-bar")

        def btn(icon, tip, cb):
            b = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda *_: cb())
            bar.pack_start(b, False, False, 0)
            return b
        self.back = btn("go-previous-symbolic", "Back (Alt+Left)", lambda: self.view and self.view.go_back())
        self.fwd = btn("go-next-symbolic", "Forward (Alt+Right)", lambda: self.view and self.view.go_forward())
        self.reload = btn("view-refresh-symbolic", "Reload (Ctrl+R)", self.reload_or_stop)
        btn("go-home-symbolic", "Home (Alt+Home)", lambda: self.view and self.view.load_uri(HOME_URI))

        if self.private:
            lab = Gtk.Label(label="Private")
            lab.get_style_context().add_class("sh-private-label")
            lab.set_tooltip_text("Nothing from this window is kept: no history, cookies or site data")
            bar.pack_start(lab, False, False, 0)

        self.url = Gtk.Entry(hexpand=True, placeholder_text="Search or enter an address")
        self.url.get_style_context().add_class("sh-url")
        self.url.connect("activate", self.on_url_activate)
        self.url.connect("changed", lambda *_: self.url.has_focus() and setattr(self, "editing", True))
        self.url.connect("focus-in-event", self.on_url_focus)
        self.url.connect("focus-out-event", lambda *_: (setattr(self, "editing", False), self.show_uri())[0] and False)
        self.url.connect("icon-press", lambda e, pos, ev: self.show_site_info() if pos == Gtk.EntryIconPosition.PRIMARY
                         else self.app.passwords.fill(self))
        comp = Gtk.EntryCompletion(model=self.app.history.model, minimum_key_length=1, popup_set_width=True)
        cell_t = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END)
        cell_u = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END, foreground_rgba=Gdk.RGBA(0.55, 0.6, 0.65, 1))
        comp.pack_start(cell_u, True)
        comp.pack_start(cell_t, True)
        comp.add_attribute(cell_u, "text", 2)
        comp.add_attribute(cell_t, "text", 1)
        comp.set_match_func(self.match_history, None)
        comp.connect("match-selected", self.on_completion)
        if not self.private:
            self.url.set_completion(comp)
        bar.pack_start(self.url, True, True, 4)

        self.zoom_btn = Gtk.Button(label="100%")
        self.zoom_btn.get_style_context().add_class("sh-zoom")
        self.zoom_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.zoom_btn.set_tooltip_text("Reset zoom (Ctrl+0)")
        self.zoom_btn.connect("clicked", lambda *_: self.zoom(0))
        self.zoom_btn.set_no_show_all(True)
        bar.pack_start(self.zoom_btn, False, False, 0)

        self.shield = Gtk.MenuButton()
        self.shield.set_relief(Gtk.ReliefStyle.NONE)
        self.shield.set_tooltip_text("Ad and tracker blocking")
        self.shield.add(Gtk.Image.new_from_icon_name("security-high-symbolic", Gtk.IconSize.BUTTON))
        self.shield.set_popover(self.build_shield_popover())
        self.shield.connect("toggled", lambda b: b.get_active() and self.update_shield())
        bar.pack_start(self.shield, False, False, 0)

        self.dl_btn = Gtk.MenuButton()
        self.dl_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.dl_btn.set_tooltip_text("Downloads")
        dl_img = Gtk.Image.new_from_icon_name("folder-download-symbolic", Gtk.IconSize.BUTTON)
        dl_img.show()  # the button is no-show-all, so show_all() never reaches its icon
        self.dl_btn.add(dl_img)
        self.dl_btn.set_popover(self.app.downloads.popover_for(self))
        self.dl_btn.set_no_show_all(True)
        self.dl_btn.set_visible(bool(self.app.downloads.items))
        bar.pack_start(self.dl_btn, False, False, 0)

        menu = Gtk.MenuButton()
        menu.set_relief(Gtk.ReliefStyle.NONE)
        menu.set_tooltip_text("Menu")
        menu.add(Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON))
        menu.set_menu_model(self.app.menu)
        bar.pack_start(menu, False, False, 0)
        return bar

    def build_shield_popover(self):
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.get_style_context().add_class("sh-pop")
        row = Gtk.Box(spacing=12)
        row.pack_start(Gtk.Label(label="Block ads and trackers", xalign=0), True, True, 0)
        self.sh_switch = Gtk.Switch()
        self.sh_switch.connect("notify::active", self.on_block_switch)
        row.pack_start(self.sh_switch, False, False, 0)
        box.pack_start(row, False, False, 0)
        self.sh_site = Gtk.Button()
        self.sh_site.connect("clicked", self.on_site_toggle)
        box.pack_start(self.sh_site, False, False, 0)
        box.pack_start(Gtk.Separator(), False, False, 0)
        lists = Gtk.Label(label="Lists", xalign=0)
        lists.get_style_context().add_class("dim-label")
        box.pack_start(lists, False, False, 0)
        self.sh_checks = {}
        for k, v in adblock.LISTS.items():
            c = Gtk.CheckButton(label=f"{v['name']} — {v['about']}")
            c.connect("toggled", self.on_list_toggle, k)
            self.sh_checks[k] = c
            box.pack_start(c, False, False, 0)
        self.sh_status = Gtk.Label(xalign=0, wrap=True, max_width_chars=40)
        self.sh_status.get_style_context().add_class("dim-label")
        box.pack_start(self.sh_status, False, False, 0)
        upd = Gtk.Button(label="Update lists now")
        upd.connect("clicked", lambda *_: self.app.blocker.update(force=True))
        box.pack_start(upd, False, False, 0)
        box.show_all()
        pop.add(box)
        return pop

    def update_shield(self):
        b = self.app.blocker
        self._shield_busy = True
        self.sh_switch.set_active(b.enabled)
        wanted = b.wanted()
        for k, c in self.sh_checks.items():
            c.set_active(k in wanted)
            c.set_sensitive(not b.busy)
        self._shield_busy = False
        host = site_of(host_of(self.view.get_uri() if self.view else ""))
        if host and b.enabled:
            self.sh_site.set_label(f"Turn back on for {host}" if b.is_off_for(host) else f"Turn off on {host}")
            self.sh_site.show()
        else:
            self.sh_site.hide()
        self.sh_status.set_text(b.status)
        on = b.enabled and b.filter is not None and not (host and b.is_off_for(host))
        ctx = self.shield.get_style_context()
        (ctx.add_class if on else ctx.remove_class)("sh-shield-on")
        img = self.shield.get_child()
        img.set_from_icon_name("security-high-symbolic" if on else "security-low-symbolic", Gtk.IconSize.BUTTON)

    def on_block_switch(self, sw, _p):
        if getattr(self, "_shield_busy", False):
            return
        self.app.prefs.data["block"] = sw.get_active()
        self.app.prefs.changed()
        self.app.refilter(reload_current=self)

    def on_site_toggle(self, *_):
        host = site_of(host_of(self.view.get_uri()))
        allow = [a for a in self.app.prefs.data.get("allow", []) if a != host]
        if not self.app.blocker.is_off_for(host):
            allow.append(host)
        else:
            allow = [a for a in allow if not (host == a or host.endswith("." + a))]
        self.app.prefs.data["allow"] = allow
        self.app.prefs.changed()
        self.app.refilter(reload_current=self)

    def on_list_toggle(self, c, key):
        if getattr(self, "_shield_busy", False):
            return
        on = set(self.app.blocker.wanted())
        (on.add if c.get_active() else on.discard)(key)
        self.app.prefs.data["lists"] = sorted(on)
        self.app.prefs.changed()
        self.app.blocker.update()

    # -- find bar
    def build_find(self):
        rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.NONE)
        box = Gtk.Box(spacing=4)
        box.get_style_context().add_class("sh-find")
        self.find_entry = Gtk.SearchEntry(width_chars=30)
        self.find_entry.connect("search-changed", lambda e: self.find(e.get_text()))
        self.find_entry.connect("activate", lambda e: self.find_step(1))
        self.find_entry.connect("stop-search", lambda e: self.close_find())
        self.find_label = Gtk.Label(xalign=0)
        self.find_label.get_style_context().add_class("dim-label")
        box.pack_start(self.find_entry, False, False, 0)
        for icon, d, tip in (("go-up-symbolic", -1, "Previous (Shift+Enter)"), ("go-down-symbolic", 1, "Next (Enter)")):
            b = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda _b, d=d: self.find_step(d))
            box.pack_start(b, False, False, 0)
        box.pack_start(self.find_label, True, True, 8)
        x = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.BUTTON)
        x.connect("clicked", lambda *_: self.close_find())
        box.pack_end(x, False, False, 0)
        rev.add(box)
        return rev

    def open_find(self):
        self.findbar.set_reveal_child(True)
        sel = ""
        self.find_entry.grab_focus()
        self.find_entry.select_region(0, -1)
        if self.find_entry.get_text():
            self.find(self.find_entry.get_text())
        return sel

    def close_find(self):
        self.findbar.set_reveal_child(False)
        if self.view:
            self.view.get_find_controller().search_finish()
            self.view.grab_focus()

    def find(self, text):
        if not self.view:
            return
        fc = self.view.get_find_controller()
        if not text:
            fc.search_finish()
            self.find_label.set_text("")
            self.find_entry.get_style_context().remove_class("sh-nomatch")
            return
        opts = WebKit2.FindOptions.CASE_INSENSITIVE | WebKit2.FindOptions.WRAP_AROUND
        fc.count_matches(text, opts, 1000)
        fc.search(text, opts, 1000)

    def find_step(self, d):
        if not self.view or not self.find_entry.get_text():
            return
        fc = self.view.get_find_controller()
        (fc.search_next if d > 0 else fc.search_previous)()

    def find_count(self, tab, n, found=False):
        if tab is not self.current:
            return
        if found:
            return
        ctx = self.find_entry.get_style_context()
        (ctx.add_class if n == 0 and self.find_entry.get_text() else ctx.remove_class)("sh-nomatch")
        self.find_label.set_text("No matches" if n == 0 else f"{n:,} match" + ("" if n == 1 else "es")
                                 if n < 1000 else "1,000+ matches")

    # -- tabs
    @property
    def view(self):
        return self.current.view if self.current else None

    def new_tab(self, uri=HOME_URI, related=None, background=False, index=None):
        app = self.app
        ucm = WebKit2.UserContentManager()
        if related is not None:
            view = WebKit2.WebView(related_view=related, user_content_manager=ucm, settings=app.settings)
        else:
            view = WebKit2.WebView(web_context=app.context_for(self.private), user_content_manager=ucm,
                                   settings=app.settings, is_controlled_by_automation=False)
        tab = Tab(self, view)
        app.passwords.attach(ucm, tab)
        self.tabs.append(tab)  # before insert_page, whose switch-page looks the tab up
        self.stack.add(view)
        if index is None:
            index = self.notebook.page_num(self.current.slot) + 1 if self.current else -1
        self.notebook.insert_page(tab.slot, tab.head, index)
        self.notebook.set_tab_reorderable(tab.slot, True)
        if uri:
            app.blocker.apply_to(ucm, uri)
            view.load_uri(uri)
        if not background or self.current is None:
            self.notebook.set_current_page(self.notebook.page_num(tab.slot))
            if uri == HOME_URI:
                view.grab_focus()
        self.notebook.set_show_tabs(True)
        return tab

    def tab_at(self, i):
        slot = self.notebook.get_nth_page(i)
        return next((t for t in self.tabs if t.slot is slot), None)

    def on_switch(self, nb, slot, i):
        tab = next((t for t in self.tabs if t.slot is slot), None)
        if not tab:
            return
        self.current = tab
        self.stack.set_visible_child(tab.view)
        self.editing = False
        self.show_uri()
        self.tab_changed(tab, load=True, progress=True)
        self.update_shield()
        if self.findbar.get_reveal_child():
            self.find(self.find_entry.get_text())
        if not self.url.has_focus():
            tab.view.grab_focus()

    def close_tab(self, tab):
        if tab not in self.tabs:
            return
        if not self.private and tab.uri and not tab.uri.startswith(HOME_URI):
            self.closed.append((tab.uri, self.notebook.page_num(tab.slot)))
            self.closed = self.closed[-25:]
            self.app.closed_any = (self, self.closed)
        self.tabs.remove(tab)
        if len(self.tabs) == 0:
            self.save_session(final=True)
            self.destroy()
            return
        self.notebook.remove_page(self.notebook.page_num(tab.slot))
        self.stack.remove(tab.view)
        tab.view.destroy()
        self.save_session()

    def reopen_closed(self):
        if self.closed:
            uri, idx = self.closed.pop()
            self.new_tab(uri, index=min(idx, self.notebook.get_n_pages()))

    def cycle(self, d):
        n = self.notebook.get_n_pages()
        if n:
            self.notebook.set_current_page((self.notebook.get_current_page() + d) % n)

    def move_tab(self, d):
        if not self.current:
            return
        n = self.notebook.get_n_pages()
        i = self.notebook.page_num(self.current.slot)
        self.notebook.reorder_child(self.current.slot, max(0, min(n - 1, i + d)))
        self.save_session()

    def tab_changed(self, tab, uri=False, load=False, progress=False):
        if tab is not self.current:
            return
        v = tab.view
        if uri:
            self.show_uri()
            self.update_shield()
        if load:
            self.back.set_sensitive(v.can_go_back())
            self.fwd.set_sensitive(v.can_go_forward())
            loading = v.is_loading()
            self.reload.get_image().set_from_icon_name(
                "process-stop-symbolic" if loading else "view-refresh-symbolic", Gtk.IconSize.BUTTON)
            self.reload.set_tooltip_text("Stop (Esc)" if loading else "Reload (Ctrl+R)")
            self.update_security()
        if progress or load:
            p = v.get_estimated_load_progress()
            self.url.set_progress_fraction(0 if not v.is_loading() or p >= 1 else p)
        t = tab.title if tab.title != "New tab" or not tab.uri.startswith(HOME_URI) else "New tab"
        self.set_title(f"{t} — {NAME}" + (" (Private)" if self.private else ""))
        z = v.get_zoom_level()
        self.zoom_btn.set_label(f"{round(z * 100)}%")
        self.zoom_btn.set_visible(abs(z - self.app.prefs.data.get("default_zoom", 1.0)) > 0.001)

    def show_uri(self):
        if self.editing or not self.current:
            return
        self.url.set_text(self.current.display_uri())

    def update_security(self):
        v = self.view
        uri = v.get_uri() or ""
        icon, tip = None, None
        if uri.startswith("https://"):
            ok, _cert, flags = v.get_tls_info()
            if ok and not flags:
                icon, tip = "channel-secure-symbolic", "Connection is secure"
            elif ok:
                icon, tip = "channel-insecure-symbolic", "Certificate not trusted"
        elif uri.startswith("http://"):
            icon, tip = "channel-insecure-symbolic", "Connection isn't secure"
        self.url.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, icon)
        self.url.set_icon_tooltip_text(Gtk.EntryIconPosition.PRIMARY, tip)
        key = self.app.passwords.enabled and is_web(uri)
        self.url.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "dialog-password-symbolic" if key else None)
        self.url.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, "Fill from 1Password (Ctrl+Shift+L)" if key else None)

    def show_site_info(self):
        host = site_of(host_of(self.view.get_uri() if self.view else ""))
        if not host:
            return
        perms = self.app.prefs.data.get("perms", {}).get(host, {})
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.INFO,
                              text=host, secondary_text=(self.url.get_icon_tooltip_text(Gtk.EntryIconPosition.PRIMARY) or "")
                              + ("\n\nPermissions: " + ", ".join(f"{k} {'allowed' if v else 'blocked'}" for k, v in perms.items())
                                 if perms else ""))
        d.add_button("Close", 0)
        if perms:
            d.add_button("Forget permissions", 1)
        if d.run() == 1:
            self.app.prefs.data.get("perms", {}).pop(host, None)
            self.app.prefs.changed()
        d.destroy()

    def show_link(self, link):
        if self._toast_t:
            return  # a toast is showing; links can wait
        if link:
            self.status.set_text(link)
            self.status_rev.show()
        else:
            self.status_rev.hide()

    def toast(self, text, seconds=4):
        """A short note in the corner where link addresses show."""
        if self._toast_t:
            GLib.source_remove(self._toast_t)
        self.status.set_text(text)
        self.status_rev.show()

        def end():
            self._toast_t = 0
            self.status_rev.hide()
            return False
        self._toast_t = GLib.timeout_add_seconds(seconds, end)

    # -- address bar
    def on_url_focus(self, *_):
        GLib.idle_add(lambda: self.url.select_region(0, -1) and False)
        return False

    def match_history(self, comp, key, it, _data):
        key = self.url.get_text().lower().strip()
        if not key:
            return False
        m = self.app.history.model
        hay = (m[it][2] + " " + m[it][1]).lower()
        if key.startswith(("http://", "https://")):
            hay = m[it][0].lower()
        return all(w in hay for w in key.split())

    def on_completion(self, comp, model, it):
        self.go(model[it][0])
        return True

    def on_url_activate(self, *_):
        self.go(self.url.get_text())

    def go(self, text):
        uri = self.app.resolve(text)
        self.editing = False
        if not self.view:
            self.new_tab(uri)
        else:
            self.app.blocker.apply_to(self.view.get_user_content_manager(), uri)
            self.view.load_uri(uri)
            self.view.grab_focus()
        self.show_uri()

    def focus_url(self):
        if self.page_fullscreen:
            return
        self.url.grab_focus()
        self.url.select_region(0, -1)

    def reload_or_stop(self):
        if not self.view:
            return
        (self.view.stop_loading if self.view.is_loading() else self.view.reload)()

    # -- zoom
    def zoom(self, d):
        v = self.view
        if not v:
            return
        default = self.app.prefs.data.get("default_zoom", 1.0)
        z = v.get_zoom_level()
        if d == 0:
            z = default
        else:
            steps = [s for s in ZOOMS if (s > z + 0.001 if d > 0 else s < z - 0.001)]
            if not steps:
                return
            z = steps[0] if d > 0 else steps[-1]
        v.set_zoom_level(z)
        host = host_of(v.get_uri())
        if host and not self.private:
            zooms = self.app.prefs.data.setdefault("zoom", {})
            if abs(z - default) < 0.001:
                zooms.pop(host, None)
            else:
                zooms[host] = z
            self.app.prefs.changed()
        self.tab_changed(self.current)

    # -- fullscreen
    def set_page_fullscreen(self, on):
        self.page_fullscreen = on
        self.notebook.set_visible(not on)
        self.bar.set_visible(not on)
        (self.fullscreen if on else self.unfullscreen)()

    def toggle_fullscreen(self):
        st = self.get_window().get_state() if self.get_window() else 0
        if st & Gdk.WindowState.FULLSCREEN:
            self.unfullscreen()
            self.notebook.show()
            self.bar.show()
        else:
            self.fullscreen()

    # -- keys
    def on_key(self, w, ev):
        st = ev.state & Gtk.accelerator_get_default_mod_mask()
        ctrl = Gdk.ModifierType.CONTROL_MASK
        shift = Gdk.ModifierType.SHIFT_MASK
        alt = Gdk.ModifierType.MOD1_MASK
        k = Gdk.keyval_to_lower(ev.keyval)
        name = Gdk.keyval_name(k) or ""
        v = self.view

        if st == ctrl:
            act = {
                "t": lambda: self.new_tab(), "w": lambda: self.current and self.close_tab(self.current),
                "l": self.focus_url, "r": lambda: v and v.reload(), "f": self.open_find,
                "n": lambda: self.app.new_window(), "q": self.app.quit_all, "p": self.print_page,
                "g": lambda: self.find_step(1), "k": self.focus_url, "e": self.focus_url,
                "equal": lambda: self.zoom(1), "plus": lambda: self.zoom(1), "KP_Add": lambda: self.zoom(1),
                "minus": lambda: self.zoom(-1), "KP_Subtract": lambda: self.zoom(-1),
                "0": lambda: self.zoom(0), "KP_0": lambda: self.zoom(0),
                "Tab": lambda: self.cycle(1), "Page_Down": lambda: self.cycle(1), "Page_Up": lambda: self.cycle(-1),
                "s": self.save_page,
            }.get(name)
            if act:
                act()
                return True
            if name in "12345678" and len(name) == 1:
                if int(name) <= self.notebook.get_n_pages():
                    self.notebook.set_current_page(int(name) - 1)
                return True
            if name == "9":
                self.notebook.set_current_page(self.notebook.get_n_pages() - 1)
                return True
        elif st == ctrl | shift:
            act = {
                "t": self.reopen_closed, "n": lambda: self.app.new_window(private=True),
                "l": lambda: self.app.passwords.fill(self),
                "r": lambda: v and v.reload_bypass_cache(), "i": self.inspect, "j": self.inspect,
                "Tab": lambda: self.cycle(-1), "ISO_Left_Tab": lambda: self.cycle(-1),
                "g": lambda: self.find_step(-1), "w": self.close,
                "Page_Up": lambda: self.move_tab(-1), "Page_Down": lambda: self.move_tab(1),
                "plus": lambda: self.zoom(1), "equal": lambda: self.zoom(1),
            }.get(name)
            if act:
                act()
                return True
        elif st == alt:
            act = {"Left": lambda: v and v.go_back(), "Right": lambda: v and v.go_forward(),
                   "Home": lambda: v and v.load_uri(HOME_URI), "d": self.focus_url}.get(name)
            if act:
                act()
                return True
        elif st == 0:
            act = {"F5": lambda: v and v.reload(), "F6": self.focus_url, "F11": self.toggle_fullscreen,
                   "F12": self.inspect}.get(name)
            if act:
                act()
                return True
            if name == "Escape":
                if self.url.has_focus():
                    self.editing = False
                    self.show_uri()
                    v and v.grab_focus()
                    return True
                if self.findbar.get_reveal_child() and self.find_entry.has_focus():
                    self.close_find()
                    return True
                if v and v.is_loading():
                    v.stop_loading()
        elif st == shift:
            if name == "F5":
                v and v.reload_bypass_cache()
                return True
            if name in ("Return", "KP_Enter") and self.find_entry.has_focus():
                self.find_step(-1)
                return True
        return False

    def inspect(self):
        if self.view:
            self.view.get_inspector().show()

    def print_page(self):
        if self.view:
            WebKit2.PrintOperation.new(self.view).run_dialog(self)

    def save_page(self):
        if not self.view or not is_web(self.view.get_uri()):
            return
        self.app.context_for(self.private).download_uri(self.view.get_uri())

    # -- permissions
    KINDS = [
        ("GeolocationPermissionRequest", "location", "know your location"),
        ("NotificationPermissionRequest", "notifications", "show notifications"),
        ("PointerLockPermissionRequest", None, None),
        ("DeviceInfoPermissionRequest", None, None),
        ("ClipboardPermissionRequest", "clipboard", "read your clipboard"),
        ("WebsiteDataAccessPermissionRequest", "storage", "use its cookies while you're on another site"),
        ("MediaKeySystemPermissionRequest", "drm", "play protected (DRM) media"),
    ]

    def ask_permission(self, tab, req):
        name = type(req).__name__
        host = site_of(host_of(tab.uri))
        kind = what = None
        if isinstance(req, WebKit2.UserMediaPermissionRequest):
            cam, mic = req.props.is_for_video_device, req.props.is_for_audio_device
            kind = "camera+mic" if cam and mic else "camera" if cam else "microphone" if mic else "screen"
            what = {"camera+mic": "use your camera and microphone", "camera": "use your camera",
                    "microphone": "use your microphone", "screen": "see your screen"}[kind]
        else:
            for n, k, w in self.KINDS:
                if n == name:
                    kind, what = k, w
                    break
            else:
                return False
            if kind is None:  # pointer lock, listing devices: harmless and expected
                req.allow()
                return True
        if tab.uri.startswith(HOME_URI) and kind == "notifications":
            req.allow()
            return True
        if kind == "drm":
            req.deny()  # WebKitGTK has no Widevine, so there is nothing to allow
            return True
        saved = self.app.prefs.data.get("perms", {}).get(host, {}).get(kind)
        if saved is not None:
            (req.allow if saved else req.deny)()
            return True
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                              text=f"{host or 'This page'} wants to {what}")
        d.add_buttons("Block", 0, "Allow", 1)
        remember = Gtk.CheckButton(label="Remember for this site", active=True)
        d.get_message_area().pack_start(remember, False, False, 0)
        remember.show()
        r = d.run()
        keep = remember.get_active()
        d.destroy()
        allow = r == 1
        (req.allow if allow else req.deny)()
        if keep and host and r in (0, 1) and not self.private:
            self.app.prefs.data.setdefault("perms", {}).setdefault(host, {})[kind] = allow
            self.app.prefs.changed()
        return True

    # -- session
    def save_session(self, final=False):
        if self.private:
            return
        self.app.save_session(self, final)

    def session(self):
        uris = []
        for i in range(self.notebook.get_n_pages()):
            t = self.tab_at(i)
            if t:
                uris.append(t.uri or HOME_URI)
        return {"tabs": uris, "active": max(0, self.notebook.get_current_page())}

    def on_delete(self, *_):
        self.save_session(final=True)
        return False


# ---------------------------------------------------------------- downloads

class Downloads:
    def __init__(self, app):
        self.app = app
        self.items = []
        self.boxes = []

    def popover_for(self, win):
        pop = Gtk.Popover()
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.get_style_context().add_class("sh-pop")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.pack_start(box, False, False, 0)
        folder = Gtk.Button(label="Open Downloads folder")
        folder.connect("clicked", lambda *_: self.open_uri(GLib.filename_to_uri(self.folder(), None)))
        outer.pack_start(folder, False, False, 0)
        outer.show_all()
        pop.add(outer)
        self.boxes.append((win, box))
        for it in self.items:
            box.pack_start(self.row(it), False, False, 0)
        return pop

    def folder(self):
        d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or os.path.expanduser("~/Downloads")
        os.makedirs(d, exist_ok=True)
        return d

    def started(self, ctx, dl):
        it = {"dl": dl, "name": "", "path": "", "rows": [], "state": "Starting…"}
        dl.connect("decide-destination", self.decide, it)
        dl.connect("notify::estimated-progress", lambda *_: self.refresh(it))
        dl.connect("finished", self.finished, it)
        dl.connect("failed", self.failed, it)
        self.items.insert(0, it)
        self.items = self.items[:30]
        for win, box in list(self.boxes):
            if win.get_window() is None and not win.get_visible():
                continue
            r = self.row(it)
            box.pack_start(r, False, False, 0)
            box.reorder_child(r, 0)
            win.dl_btn.show()
        w = self.app.get_active_window()
        if isinstance(w, Window):
            w.dl_btn.show()
            w.dl_btn.set_active(True)

    def decide(self, dl, suggested, it):
        name = os.path.basename(suggested or "") or "download"
        base, ext = os.path.splitext(name)
        path = os.path.join(self.folder(), name)
        n = 1
        while os.path.exists(path):
            n += 1
            path = os.path.join(self.folder(), f"{base} ({n}){ext}")
        it["name"], it["path"] = os.path.basename(path), path
        dl.set_destination(GLib.filename_to_uri(path, None) if WebKit2.get_minor_version() < 40 else path)
        self.refresh(it)
        return True

    def row(self, it):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        box.get_style_context().add_class("sh-dl")
        top = Gtk.Box(spacing=8)
        name = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=34)
        open_b = Gtk.Button.new_from_icon_name("document-open-symbolic", Gtk.IconSize.BUTTON)
        open_b.set_tooltip_text("Open")
        open_b.connect("clicked", lambda *_: it["path"] and self.open_uri(GLib.filename_to_uri(it["path"], None)))
        show_b = Gtk.Button.new_from_icon_name("folder-open-symbolic", Gtk.IconSize.BUTTON)
        show_b.set_tooltip_text("Show in folder")
        show_b.connect("clicked", lambda *_: self.show_in_folder(it["path"]))
        stop_b = Gtk.Button.new_from_icon_name("process-stop-symbolic", Gtk.IconSize.BUTTON)
        stop_b.set_tooltip_text("Cancel")
        stop_b.connect("clicked", lambda *_: it["dl"].cancel())
        top.pack_start(name, True, True, 0)
        for b in (stop_b, open_b, show_b):
            b.set_relief(Gtk.ReliefStyle.NONE)
            top.pack_start(b, False, False, 0)
        bar = Gtk.ProgressBar()
        state = Gtk.Label(xalign=0)
        state.get_style_context().add_class("dim-label")
        box.pack_start(top, False, False, 0)
        box.pack_start(bar, False, False, 0)
        box.pack_start(state, False, False, 0)
        box.show_all()
        it["rows"].append((name, bar, state, open_b, show_b, stop_b))
        self.refresh(it)
        return box

    def refresh(self, it):
        dl = it["dl"]
        p = dl.get_estimated_progress()
        done = it["state"] in ("Done", "Failed", "Cancelled")
        if not done:
            got = dl.get_received_data_length()
            total = dl.get_response().get_content_length() if dl.get_response() else 0
            it["state"] = (f"{got / 1e6:.1f} of {total / 1e6:.1f} MB" if total > 0 else f"{got / 1e6:.1f} MB")
        for name, bar, state, open_b, show_b, stop_b in it["rows"]:
            name.set_text(it["name"] or "…")
            bar.set_fraction(1 if done else p)
            bar.set_visible(not done)
            state.set_text(it["state"])
            open_b.set_visible(it["state"] == "Done")
            show_b.set_visible(it["state"] == "Done")
            stop_b.set_visible(not done)

    def finished(self, dl, it):
        if it["state"] not in ("Failed", "Cancelled"):
            it["state"] = "Done"
            n = Gio.Notification.new("Download complete")
            n.set_body(it["name"])
            self.app.send_notification(None, n)
        self.refresh(it)

    def failed(self, dl, err, it):
        it["state"] = "Cancelled" if err.matches(WebKit2.download_error_quark(), WebKit2.DownloadError.CANCELLED_BY_USER) else "Failed"
        self.refresh(it)

    def open_uri(self, uri):
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error as e:
            log("open failed", e)

    def show_in_folder(self, path):
        if not path:
            return
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call_sync("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                          "org.freedesktop.FileManager1", "ShowItems",
                          GLib.Variant("(ass)", ([GLib.filename_to_uri(path, None)], "")),
                          None, Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error:
            self.open_uri(GLib.filename_to_uri(os.path.dirname(path), None))


# ---------------------------------------------------------------- the app

class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.prefs = None
        self.closed_any = None
        self.last_normal = None
        self._contexts = {}
        GLib.set_application_name(NAME)

    # -- startup
    def do_startup(self):
        Gtk.Application.do_startup(self)
        os.makedirs(DATA, exist_ok=True)
        os.makedirs(CACHE, exist_ok=True)
        self.prefs = Store("prefs.json", {"engine": "google", "restore": True, "block": True,
                                          "allow": [], "zoom": {}, "perms": {}, "session": []})
        self.history = History()
        self.downloads = Downloads(self)
        self.settings = WebKit2.Settings(
            enable_developer_extras=True, enable_back_forward_navigation_gestures=True,
            enable_media_stream=True, enable_webrtc=True, enable_smooth_scrolling=True,
            javascript_can_open_windows_automatically=False, enable_encrypted_media=False)
        self.blocker = Blocker(self)
        self.passwords = passwords.Passwords(self)
        self.build_actions()
        self.home_html = self.read_home()
        self.css = Gtk.CssProvider()
        base = Gtk.CssProvider()
        base.load_from_data(BASE_CSS.encode())
        scr = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(scr, base, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.StyleContext.add_provider_for_screen(scr, self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.apply_theme()
        self.theme_mon = None
        self._theme_t = 0
        if os.path.isdir(OMARCHY_STATE):
            self.theme_mon = Gio.File.new_for_path(OMARCHY_STATE).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
            self.theme_mon.connect("changed", self.on_theme_files)
        self.blocker.start()

    def read_home(self):
        for p in (os.path.join(HERE, "home", "index.html"), os.path.join(HERE, "index.html")):
            try:
                with open(p, "rb") as f:
                    return f.read()
            except OSError:
                continue
        return b"<!doctype html><title>New tab</title><p>SimpleHome's index.html is missing.</p>"

    def context_for(self, private):
        key = bool(private)
        if key in self._contexts:
            return self._contexts[key]
        if private:
            dm = WebKit2.WebsiteDataManager.new_ephemeral()
        else:
            dm = WebKit2.WebsiteDataManager(base_data_directory=os.path.join(DATA, "web"),
                                            base_cache_directory=os.path.join(CACHE, "web"))
        dm.set_itp_enabled(True)
        ctx = WebKit2.WebContext.new_with_website_data_manager(dm)
        if not private:
            ctx.get_cookie_manager().set_persistent_storage(os.path.join(DATA, "web", "cookies.sqlite"),
                                                            WebKit2.CookiePersistentStorage.SQLITE)
            ctx.set_favicon_database_directory(os.path.join(CACHE, "favicons"))
        else:
            ctx.set_favicon_database_directory(None)
        ctx.register_uri_scheme("simplehome", self.serve_home)
        sec = ctx.get_security_manager()
        sec.register_uri_scheme_as_secure("simplehome")
        sec.register_uri_scheme_as_cors_enabled("simplehome")
        langs = [l.split(".")[0].replace("_", "-") for l in GLib.get_language_names() if l not in ("C", "POSIX") and "." not in l and "@" not in l]
        if langs:
            ctx.set_preferred_languages(langs[:4])
            ctx.set_spell_checking_languages([l.replace("-", "_") for l in langs[:1]])
            ctx.set_spell_checking_enabled(True)
        ctx.connect("download-started", self.downloads.started)
        self._contexts[key] = ctx
        return ctx

    @property
    def context(self):
        return self.context_for(False)

    def serve_home(self, req):
        path = req.get_path() or "/"
        if path in ("/", "/index.html", ""):
            stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(self.home_html))
            req.finish(stream, len(self.home_html), "text/html")
        else:
            req.finish_error(GLib.Error.new_literal(Gio.io_error_quark(), "Not found", Gio.IOErrorEnum.NOT_FOUND))

    # -- menu
    def build_actions(self):
        def act(name, cb, accel=None):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_: cb())
            self.add_action(a)
        win = lambda: self.get_active_window() if isinstance(self.get_active_window(), Window) else None  # noqa: E731
        act("new-tab", lambda: win() and win().new_tab())
        act("new-window", lambda: self.new_window())
        act("private-window", lambda: self.new_window(private=True))
        act("reopen", lambda: win() and win().reopen_closed())
        act("find", lambda: win() and win().open_find())
        act("zoom-in", lambda: win() and win().zoom(1))
        act("zoom-out", lambda: win() and win().zoom(-1))
        act("zoom-reset", lambda: win() and win().zoom(0))
        act("print", lambda: win() and win().print_page())
        act("inspect", lambda: win() and win().inspect())
        act("downloads", lambda: self.downloads.open_uri(GLib.filename_to_uri(self.downloads.folder(), None)))
        act("clear-history", self.clear_history)
        act("clear-data", self.clear_data)
        act("make-default", self.make_default)
        act("about", self.about)
        act("quit", self.quit_all)
        self.engine_action = Gio.SimpleAction.new_stateful("engine", GLib.VariantType.new("s"),
                                                           GLib.Variant("s", self.prefs.data.get("engine", "google")))
        self.engine_action.connect("activate", self.on_engine)
        self.add_action(self.engine_action)
        for name, key in (("op-fill", "op_fill"), ("op-save", "op_save")):
            a = Gio.SimpleAction.new_stateful(name, None, GLib.Variant("b", self.prefs.data.get(key, True)))
            a.connect("activate", self.on_pref_toggle, key)
            a.set_enabled(self.passwords.op.available)
            self.add_action(a)
        side = Gio.SimpleAction.new_stateful("vertical-tabs", None, GLib.Variant("b", self.prefs.data.get("vertical_tabs", True)))
        side.connect("activate", self.on_vertical_tabs)
        self.add_action(side)
        restore = Gio.SimpleAction.new_stateful("restore", None, GLib.Variant("b", self.prefs.data.get("restore", True)))
        restore.connect("activate", self.on_restore)
        self.add_action(restore)

        m = Gio.Menu()
        s1 = Gio.Menu()
        s1.append("New tab\tCtrl+T", "app.new-tab")
        s1.append("New window\tCtrl+N", "app.new-window")
        s1.append("New private window\tCtrl+Shift+N", "app.private-window")
        s1.append("Reopen closed tab\tCtrl+Shift+T", "app.reopen")
        m.append_section(None, s1)
        s2 = Gio.Menu()
        s2.append("Find on page\tCtrl+F", "app.find")
        zoom = Gio.Menu()
        zoom.append("Zoom in\tCtrl+=", "app.zoom-in")
        zoom.append("Zoom out\tCtrl+-", "app.zoom-out")
        zoom.append("Actual size\tCtrl+0", "app.zoom-reset")
        s2.append_submenu("Zoom", zoom)
        s2.append("Print…\tCtrl+P", "app.print")
        s2.append("Downloads folder", "app.downloads")
        m.append_section(None, s2)
        s3 = Gio.Menu()
        eng = Gio.Menu()
        for k, (label, _) in ENGINES.items():
            eng.append(label, f"app.engine::{k}")
        s3.append_submenu("Address bar searches with", eng)
        op = Gio.Menu()
        op.append("Fill logins (Ctrl+Shift+L)", "app.op-fill")
        op.append("Offer to save new logins", "app.op-save")
        s3.append_submenu("1Password", op)
        s3.append("Tabs on the side", "app.vertical-tabs")
        s3.append("Reopen last session's tabs", "app.restore")
        s3.append("Clear history…", "app.clear-history")
        s3.append("Clear cookies and site data…", "app.clear-data")
        s3.append("Make default browser", "app.make-default")
        m.append_section(None, s3)
        s4 = Gio.Menu()
        s4.append("Developer tools\tF12", "app.inspect")
        s4.append("About", "app.about")
        s4.append("Quit\tCtrl+Q", "app.quit")
        m.append_section(None, s4)
        self.menu = m

    def on_engine(self, a, v):
        a.set_state(v)
        self.prefs.data["engine"] = v.get_string()
        self.prefs.changed()
        # tell SimpleHome too, so its search box and the address bar agree
        url = ENGINES[v.get_string()][1]
        for w in self.windows():
            for t in w.tabs:
                if t.uri.startswith(HOME_URI):
                    t.view.evaluate_javascript(
                        f"localStorage.setItem('homepage.engine', {json.dumps(url)});"
                        f"var s=document.getElementById('engineSelect'); if (s) s.value={json.dumps(url)};",
                        -1, None, None, None, None)

    def on_pref_toggle(self, a, _v, key):
        on = not a.get_state().get_boolean()
        a.set_state(GLib.Variant("b", on))
        self.prefs.data[key] = on
        self.prefs.changed()
        for w in self.windows():
            if w.view:
                w.update_security()

    def on_vertical_tabs(self, a, _v):
        on = not a.get_state().get_boolean()
        a.set_state(GLib.Variant("b", on))
        self.prefs.data["vertical_tabs"] = on
        self.prefs.changed()
        for w in self.windows():
            w.layout(on)

    def on_restore(self, a, _v):
        on = not a.get_state().get_boolean()
        a.set_state(GLib.Variant("b", on))
        self.prefs.data["restore"] = on
        self.prefs.changed()

    def confirm(self, text, detail, ok):
        d = Gtk.MessageDialog(transient_for=self.get_active_window(), modal=True,
                              message_type=Gtk.MessageType.QUESTION, text=text, secondary_text=detail)
        d.add_buttons("Cancel", 0, ok, 1)
        r = d.run()
        d.destroy()
        return r == 1

    def clear_history(self):
        if self.confirm("Clear browsing history?", "The address bar forgets every site it has seen. "
                        "SimpleHome's own launcher history is kept (clear it under its Settings → History).", "Clear"):
            self.history.clear()

    def clear_data(self):
        if self.confirm("Clear cookies and site data?", "You'll be signed out of websites. SimpleHome's shortcuts "
                        "and settings are kept.", "Clear"):
            dm = self.context.get_website_data_manager()

            def got(m, res):
                items = [d for d in m.fetch_finish(res) if d.get_name() not in ("home",)]
                m.remove(WebKit2.WebsiteDataTypes.ALL, items, None, None)
            dm.fetch(WebKit2.WebsiteDataTypes.ALL, None, got)
            self.context.get_website_data_manager().clear(WebKit2.WebsiteDataTypes.DISK_CACHE
                                                          | WebKit2.WebsiteDataTypes.MEMORY_CACHE, 0, None, None)

    def make_default(self):
        app = Gio.DesktopAppInfo.new(DESKTOP_ID)
        if not app:
            self.message("Install it first", "Run ./install.sh from the download so the app launcher knows about it, "
                         "then try again (or run ./install.sh --default).")
            return
        try:
            subprocess.run(["xdg-settings", "set", "default-web-browser", DESKTOP_ID], timeout=10, check=True)
            for t in ("x-scheme-handler/http", "x-scheme-handler/https", "text/html"):
                app.set_as_default_for_type(t)
            self.message("SimpleHome Browser is now your default browser", "Links from other apps open here.")
        except (OSError, subprocess.SubprocessError, GLib.Error) as e:
            self.message("Couldn't change the default browser", str(e))

    def message(self, text, detail):
        d = Gtk.MessageDialog(transient_for=self.get_active_window(), modal=True,
                              message_type=Gtk.MessageType.INFO, text=text, secondary_text=detail)
        d.add_button("OK", 0)
        d.run()
        d.destroy()

    def about(self):
        d = Gtk.AboutDialog(transient_for=self.get_active_window(), modal=True, program_name=NAME, version=VERSION,
                            logo_icon_name="simplehome-browser", website="https://github.com/kanibusrex/simplehome-browser",
                            comments="A small tabbed browser with SimpleHome as its new tab page.\n"
                                     f"WebKitGTK {WebKit2.get_major_version()}.{WebKit2.get_minor_version()}.{WebKit2.get_micro_version()}")
        d.run()
        d.destroy()

    # -- theme
    def apply_theme(self):
        p = omarchy_palette()
        if not p:
            self.css.load_from_data(b"")
            return
        self.css.load_from_data(theme_css(p).encode())
        Gtk.Settings.get_default().props.gtk_application_prefer_dark_theme = luminance(p["background"]) < 0.5

    def on_theme_files(self, *_):
        if self._theme_t:
            GLib.source_remove(self._theme_t)
        self._theme_t = GLib.timeout_add(500, self._retheme)

    def _retheme(self):
        self._theme_t = 0
        self.apply_theme()
        return False

    # -- addresses
    def resolve(self, text):
        t = (text or "").strip()
        if not t:
            return HOME_URI
        if re.match(r"^(https?|file|about|simplehome|data|blob|ftp):", t, re.I):
            return t
        if t.startswith("/") or t.startswith("~/"):
            return GLib.filename_to_uri(os.path.expanduser(t), None)
        if " " not in t:
            host = re.split(r"[/?#]", t, 1)[0]
            bare = host.rsplit(":", 1)[0] if re.search(r":\d+$", host) else host
            local = bare == "localhost" or re.match(r"^\d{1,3}(\.\d{1,3}){3}$", bare) or bare.endswith((".local", ".lan", ".home.arpa"))
            if local:
                return "http://" + t
            if re.match(r"^([a-z0-9-]+\.)+[a-z][a-z0-9-]{1,62}$", bare, re.I):
                return "https://" + t
        key = self.prefs.data.get("engine", "google")
        return ENGINES.get(key, ENGINES["google"])[1] + urllib.parse.quote_plus(t)

    def refilter(self, reload_current=None):
        """Give every tab the current filter (or take it away) for the page it shows."""
        for w in self.windows():
            for t in w.tabs:
                self.blocker.apply_to(t.view.get_user_content_manager(), t.uri)
            w.update_shield()
        if reload_current and reload_current.view and is_web(reload_current.view.get_uri()):
            reload_current.view.reload()

    # -- windows
    def windows(self):
        return [w for w in self.get_windows() if isinstance(w, Window)]

    def focused(self, w):
        if not w.private:
            self.last_normal = w

    def new_window(self, uris=None, private=False, restore=None):
        w = Window(self, private=private)
        if restore:
            for u in restore.get("tabs", []):
                w.new_tab(u, background=True)
            if w.tabs:
                w.notebook.set_current_page(min(restore.get("active", 0), len(w.tabs) - 1))
        for u in uris or []:
            w.new_tab(self.resolve(u))
        if not w.tabs:
            w.new_tab()
        w.present()
        return w

    def save_session(self, win, final=False):
        if not self.prefs:
            return
        open_wins = [w for w in self.windows() if not w.private and w.tabs and (w is not win or not final)]
        if final and not open_wins:
            sess = [win.session()] if win.tabs else []  # the last window closing: keep it for next time
        else:
            sess = [w.session() for w in open_wins]
        self.prefs.data["session"] = sess
        self.prefs.changed()

    def quit_all(self):
        normal = [w for w in self.windows() if not w.private]
        self.prefs.data["session"] = [w.session() for w in normal if w.tabs]
        self.prefs.flush()
        self.history.store.flush()
        self.quit()

    def do_command_line(self, cmd):
        args = cmd.get_arguments()[1:]
        private = any(a in ("--private-window", "--incognito", "--private", "-private") for a in args)
        new_window = any(a in ("--new-window", "-new-window") for a in args)
        uris = [a for a in args if not a.startswith("-")]
        uris = [GLib.filename_to_uri(os.path.abspath(u), None) if os.path.exists(u) else u for u in uris]
        wins = self.windows()
        if not wins:
            sess = self.prefs.data.get("session") or []
            if self.prefs.data.get("restore", True) and sess and not private:
                first = None
                for s in sess:
                    w = self.new_window(restore=s)
                    first = first or w
                if uris:
                    for u in uris:
                        first.new_tab(self.resolve(u))
                    first.present()
            else:
                self.new_window(uris, private=private)
        elif private or new_window or not uris:
            self.new_window(uris, private=private)
            self.focus_hypr()
        else:
            w = self.last_normal if self.last_normal in wins else next((x for x in wins if not x.private), None)
            if not w:
                w = self.new_window(uris)
            else:
                for u in uris:
                    w.new_tab(self.resolve(u))
                w.present()
            self.focus_hypr()
        return 0

    def focus_hypr(self):
        """Hyprland doesn't let a window raise itself, so ask it to focus ours."""
        if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("omarchy-hyprland-focus-app"):
            GLib.timeout_add(150, lambda: subprocess.Popen(
                ["omarchy-hyprland-focus-app", "^" + re.escape(APP_ID) + "$"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) and False)

    def do_shutdown(self):
        if self.prefs:
            self.prefs.flush()
            self.history.store.flush()
        Gtk.Application.do_shutdown(self)


def main():
    if "--version" in sys.argv[1:]:
        print(f"{NAME} {VERSION}")
        return 0
    if any(a in ("--help", "-h", "--help-all") for a in sys.argv[1:]):
        # answered here, without starting the app: launchers (Omarchy's among them) probe with --help
        print(f"Usage: simplehome-browser [--new-window] [--private-window | --incognito] [URL…]\n\n{NAME} {VERSION}")
        return 0
    GLib.set_prgname(APP_ID)
    app = App()
    if os.environ.get("SIMPLEHOME_APP_ID"):  # a test instance beside the real one
        app.set_application_id(os.environ["SIMPLEHOME_APP_ID"])
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
