"""Filling and saving logins with 1Password (see onepassword.py for the `op` side).

Filling only ever happens when the person asks (Ctrl+Shift+L or the key in the address bar),
only into the tab's top-level page, and only when that page's host is the login's own site or a
subdomain of it, checked once here and again inside the page just before the values go in.
The page scripts run in an isolated JavaScript world, so the page's own scripts can't see or
wrap them; they still see the filled fields, as they would if the person had typed.
"""

import json
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, GLib, Gtk, Pango, WebKit2  # noqa: E402

import onepassword  # noqa: E402

WORLD = "simplehome-1password"

# Fills the page's login form. Called with {host, username, password, totp}.
FILL_JS = r"""
(function (d) {
  if (location.hostname.toLowerCase() !== d.host) return 'moved';
  const shown = (e) => {
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && !e.disabled && !e.readOnly;
  };
  const all = [...document.querySelectorAll('input')].filter(shown);
  const words = (i) => [i.name, i.id, i.placeholder, i.getAttribute('aria-label'), i.autocomplete].join(' ');
  const textish = (i) => ['text', 'email', 'tel', ''].includes(i.type);
  const isOtp = (i) => i.autocomplete === 'one-time-code' ||
    (textish(i) || i.type === 'number') && /\b(otp|totp|2fa|mfa|one.?time|verification|security.?code|auth(entication)?.?code|code)\b/i.test(words(i));
  const set = (el, v) => {
    el.focus();
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };
  const done = [];
  const pw = all.find((i) => i.type === 'password' && !/new-password/.test(i.autocomplete || '')) ||
             all.find((i) => i.type === 'password');
  const otp = all.filter(isOtp);
  let user = null;
  if (pw) {
    const near = [...(pw.form || document).querySelectorAll('input')].filter(shown)
      .filter((i) => textish(i) && !isOtp(i) && (i.compareDocumentPosition(pw) & Node.DOCUMENT_POSITION_FOLLOWING));
    user = near.pop() || null;
  } else if (!(otp.length && d.totp)) {
    user = all.find((i) => /username|email/.test(i.autocomplete || '')) || all.find((i) => i.type === 'email') ||
           all.find((i) => textish(i) && !isOtp(i) && /user|email|login|account|identifier|e-mail/i.test(words(i)));
  }
  if (user && d.username) { set(user, d.username); done.push('username'); }
  if (pw && d.password) { set(pw, d.password); done.push('password'); }
  if (!pw && otp.length && d.totp) {
    const boxes = otp.filter((i) => i.maxLength === 1);
    if (boxes.length >= d.totp.length) d.totp.split('').forEach((c, n) => set(boxes[n], c));
    else set(otp[0], d.totp);
    done.push('code');
  }
  if (pw && done.includes('password')) pw.focus();
  return done.join(',') || 'nothing';
})(%s)
"""

# Watches for a login being sent, to offer saving it. Top frame only, isolated world.
WATCH_JS = r"""
(() => {
  if (window.__shLoginWatch) return;
  window.__shLoginWatch = true;
  let last = '';
  const post = (m) => { try { window.webkit.messageHandlers.shLogin.postMessage(JSON.stringify(m)); } catch (e) {} };
  const textish = (i) => ['text', 'email', 'tel'].includes(i.type);
  const look = (root) => {
    root = root && root.querySelectorAll ? root : document;
    const pw = [...root.querySelectorAll('input[type=password]')].find((i) => i.value);
    if (!pw) {
      // the first step of a two-step sign-in: remember who, for when the password comes
      const u = [...root.querySelectorAll('input')].find((i) => textish(i) && i.value &&
        (/username|email/.test(i.autocomplete || '') || i.type === 'email' || /user|email|login|identifier/i.test(i.name + ' ' + i.id)));
      if (u) post({ kind: 'user', host: location.hostname, username: u.value });
      return;
    }
    const before = [...(pw.form || document).querySelectorAll('input')]
      .filter((i) => textish(i) && i.value && (i.compareDocumentPosition(pw) & Node.DOCUMENT_POSITION_FOLLOWING));
    const u = before.pop();
    const key = (u ? u.value : '') + '\n' + pw.value;
    if (key === last) return;
    last = key;
    post({ kind: 'login', host: location.hostname, url: location.origin + '/', username: u ? u.value : '', password: pw.value });
  };
  addEventListener('submit', (e) => look(e.target), true);
  addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('button, input[type=submit], input[type=button], [role=button]');
    if (b) look(b.form || document);
  }, true);
  addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target && e.target.tagName === 'INPUT') look(e.target.form || document); }, true);
})();
"""


class Passwords:
    def __init__(self, app):
        self.app = app
        self.op = onepassword.OnePassword(account=app.prefs.data.get("op_account") or None)
        self.users = {}  # host -> (username, when): the first step of a two-step sign-in

    @property
    def prefs(self):
        return self.app.prefs.data

    @property
    def enabled(self):
        return self.op.available and self.prefs.get("op_fill", True)

    @property
    def offer_save(self):
        return self.enabled and self.prefs.get("op_save", True)

    def worker(self, fn, done):
        """fn() off the main thread (it may sit behind 1Password's unlock prompt), done(result, error) on it."""
        def run():
            try:
                res, err = fn(), None
            except onepassword.OPError as e:
                res, err = None, e
            GLib.idle_add(lambda: done(res, err) and False)
        threading.Thread(target=run, daemon=True).start()

    # -- every tab
    def attach(self, ucm, tab):
        ucm.add_script(WebKit2.UserScript.new_for_world(
            WATCH_JS, WebKit2.UserContentInjectedFrames.TOP_FRAME, WebKit2.UserScriptInjectionTime.END, WORLD, None, None))
        ucm.connect("script-message-received::shLogin", self.on_message, tab)
        ucm.register_script_message_handler_in_world("shLogin", WORLD)

    def on_message(self, ucm, value, tab):
        if hasattr(value, "get_js_value"):  # WebKit2 4.1 wraps it in a JavascriptResult
            value = value.get_js_value()
        try:
            m = json.loads(value.to_string())
        except (ValueError, AttributeError):
            return
        host = (m.get("host") or "").lower()
        # the message must come from the page the tab is actually showing
        if not host or host != onepassword.bare(onepassword.host_of(tab.uri)) and host != onepassword.host_of(tab.uri):
            return
        if m.get("kind") == "user":
            self.users[host] = (m.get("username", ""), time.time())
            return
        if m.get("kind") != "login" or not m.get("password") or not self.offer_save:
            return
        user = m.get("username") or ""
        if not user:
            u, when = self.users.get(host, ("", 0))
            if time.time() - when < 600:
                user = u
        site = onepassword.bare(host)
        if site in self.prefs.get("op_never", []) or getattr(tab, "filled_host", None) == host:
            return
        if self.op.known(host, user):
            return
        tab.win.offer_save(tab, {"host": host, "url": m.get("url") or f"https://{host}/",
                                 "username": user, "password": m["password"], "at": time.time()})

    # -- filling
    def fill(self, win):
        tab = win.current
        if not tab:
            return
        if not self.op.available:
            win.pw_message("The 1Password command-line tool isn't installed.", "sudo pacman -S 1password-cli")
            return
        uri = tab.uri
        host = onepassword.host_of(uri)
        if not uri.startswith(("https://", "http://")) or not host:
            win.pw_message("There's nothing to fill on this page.")
            return
        if uri.startswith("http://") and not onepassword.is_local(host):
            win.pw_message(f"{host} isn't using a secure connection, so 1Password won't fill it.")
            return
        win.pw_message("Looking in 1Password…", busy=True)
        refresh = getattr(win, "_pw_retry", False)
        win._pw_retry = False

        def done(found, err):
            if err:
                win.pw_error(err)
                return
            if tab.uri != uri:
                win.pw_close()
                return
            if not found:
                win.pw_message(f"No 1Password logins for {onepassword.bare(host)}.",
                               action=("Look again", lambda: (setattr(win, "_pw_retry", True), self.fill(win))))
            elif len(found) == 1:
                self.fill_item(win, tab, found[0])
            else:
                win.pw_choose(found, lambda it: self.fill_item(win, tab, it))
        self.worker(lambda: self.op.matching(host, self.op.logins(refresh=refresh)), done)

    def fill_item(self, win, tab, item):
        uri = tab.uri
        host = onepassword.host_of(uri)
        if not any(onepassword.host_matches(host, u) for u in item["urls"]):
            win.pw_message("That login isn't for this site.")
            return
        win.pw_message(f"Filling {item['title']}…", busy=True)

        def done(cred, err):
            if err:
                win.pw_error(err)
                return
            # check again: the tab may have moved on while 1Password was unlocking
            if tab.uri != uri or not any(onepassword.host_matches(onepassword.host_of(tab.uri), u) for u in cred["urls"] or item["urls"]):
                win.pw_message("The page changed before 1Password answered, so nothing was filled.")
                return
            data = {"host": host, "username": cred["username"], "password": cred["password"], "totp": cred["totp"]}
            script = FILL_JS % json.dumps(data)

            def filled(view, res):
                try:
                    what = view.evaluate_javascript_finish(res).to_string()
                except GLib.Error:
                    what = "nothing"
                if what in ("nothing", "moved"):
                    win.pw_message("There's no login form on this page to fill." if what == "nothing"
                                   else "The page changed before 1Password answered, so nothing was filled.")
                    return
                tab.filled_host = host
                win.pw_close()
                if cred["totp"] and "code" not in what:
                    Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(cred["totp"], -1)
                    win.toast("One-time code copied, for the next step")
            tab.view.evaluate_javascript(script, -1, WORLD, None, None, filled)
        self.worker(lambda: self.op.credentials(item["id"]), done)

    # -- saving
    def save(self, win, pending, done_cb):
        def work():
            # a list (it may already hold this login) is worth the extra second
            if self.op.matching(pending["host"], self.op.logins(refresh=True)) and \
                    self.op.known(pending["host"], pending["username"]):
                return "known"
            self.op.save(pending["url"], pending["username"], pending["password"])
            return "saved"

        def done(res, err):
            pending["password"] = ""
            done_cb(res, err)
        self.worker(work, done)

    def never(self, host):
        never = self.prefs.setdefault("op_never", [])
        site = onepassword.bare(host)
        if site not in never:
            never.append(site)
            self.app.prefs.changed()


class PasswordUI:
    """The parts of a window that belong to 1Password: the popover under the address bar's key
    and the "Save to 1Password?" bar. Mixed into Window."""

    def build_pw(self):
        self.pw_pop = Gtk.Popover(relative_to=self.url, position=Gtk.PositionType.BOTTOM)
        self.pw_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.pw_box.get_style_context().add_class("sh-pop")
        self.pw_pop.add(self.pw_box)
        self.pw_box.show()

        self.savebar = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.NONE)
        bar = Gtk.Box(spacing=8)
        bar.get_style_context().add_class("sh-savebar")
        self.save_label = Gtk.Label(xalign=0, wrap=True)
        bar.pack_start(Gtk.Image.new_from_icon_name("dialog-password-symbolic", Gtk.IconSize.BUTTON), False, False, 0)
        bar.pack_start(self.save_label, True, True, 0)
        for label, cb in (("Never for this site", self.save_never), ("Not now", self.save_dismiss), ("Save", self.save_yes)):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda *_, cb=cb: cb())
            if label == "Save":
                b.get_style_context().add_class("suggested-action")
            bar.pack_start(b, False, False, 0)
        self.savebar.add(bar)
        bar.show_all()
        self.pending = None

    def pw_anchor(self):
        r = self.url.get_icon_area(Gtk.EntryIconPosition.SECONDARY)
        if r.width == 0:
            r = Gdk.Rectangle()
            r.x, r.y, r.width, r.height = self.url.get_allocated_width() - 30, 0, 24, self.url.get_allocated_height()
        self.pw_pop.set_pointing_to(r)

    def pw_clear(self):
        for c in self.pw_box.get_children():
            self.pw_box.remove(c)

    def pw_message(self, text, detail=None, busy=False, action=None):
        self.pw_clear()
        row = Gtk.Box(spacing=10)
        if busy:
            sp = Gtk.Spinner()
            sp.start()
            row.pack_start(sp, False, False, 0)
        lab = Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=44)
        row.pack_start(lab, True, True, 0)
        self.pw_box.pack_start(row, False, False, 0)
        if detail:
            d = Gtk.Label(label=detail, xalign=0, selectable=True, wrap=True, max_width_chars=44)
            d.get_style_context().add_class("dim-label")
            self.pw_box.pack_start(d, False, False, 0)
        if action:
            b = Gtk.Button(label=action[0])
            b.connect("clicked", lambda *_: action[1]())
            self.pw_box.pack_start(b, False, False, 0)
        self.pw_box.show_all()
        self.pw_anchor()
        self.pw_pop.popup()

    def pw_error(self, err):
        if err.kind == "cancelled":
            self.pw_close()
            self.toast(str(err))
            return
        self.pw_message(str(err), action=("Try again", lambda: self.app.passwords.fill(self)) if err.kind == "setup" else None)

    def pw_choose(self, items, pick):
        self.pw_clear()
        head = Gtk.Label(label="Fill with", xalign=0)
        head.get_style_context().add_class("dim-label")
        self.pw_box.pack_start(head, False, False, 0)
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE)
        lb.set_activate_on_single_click(True)
        for it in items[:12]:
            row = Gtk.ListBoxRow()
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            v.set_margin_top(4)
            v.set_margin_bottom(4)
            t = Gtk.Label(label=it["title"], xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=40)
            u = Gtk.Label(label=it["username"] or "(no username)", xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=40)
            u.get_style_context().add_class("dim-label")
            v.pack_start(t, False, False, 0)
            v.pack_start(u, False, False, 0)
            row.add(v)
            row._item = it
            lb.add(row)
        lb.connect("row-activated", lambda _lb, row: pick(row._item))
        self.pw_box.pack_start(lb, False, False, 0)
        self.pw_box.show_all()
        self.pw_anchor()
        self.pw_pop.popup()
        first = lb.get_row_at_index(0)
        if first:
            lb.select_row(first)
            first.grab_focus()

    def pw_close(self):
        self.pw_pop.popdown()

    # -- the save bar
    def offer_save(self, tab, pending):
        if tab is not self.current:
            return
        self.pending = dict(pending, tab=tab)
        who = f" as {pending['username']}" if pending["username"] else ""
        self.save_label.set_text(f"Save your login for {onepassword.bare(pending['host'])}{who} to 1Password?")
        self.savebar.set_reveal_child(True)
        # forget it if left unanswered
        GLib.timeout_add_seconds(120, self._save_expire, self.pending)

    def _save_expire(self, p):
        if self.pending is p:
            self.save_dismiss()
        return False

    def save_dismiss(self):
        if self.pending:
            self.pending["password"] = ""
        self.pending = None
        self.savebar.set_reveal_child(False)

    def save_never(self):
        if self.pending:
            self.app.passwords.never(self.pending["host"])
        self.save_dismiss()

    def save_yes(self):
        p = self.pending
        if not p:
            return
        self.pending = None
        self.savebar.set_reveal_child(False)
        self.toast("Saving to 1Password…")

        def done(res, err):
            if err:
                self.toast(str(err), 8)
            else:
                self.toast("Already in 1Password" if res == "known" else f"Saved to 1Password: {onepassword.bare(p['host'])}")
        self.app.passwords.save(self, p, done)

    def hide_save_for(self, tab):
        """A tab that moves to another site takes its pending save offer with it."""
        if self.pending and self.pending.get("tab") is tab:
            if onepassword.bare(onepassword.host_of(tab.uri)) != onepassword.bare(self.pending["host"]):
                self.save_dismiss()
