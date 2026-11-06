"""1Password through its command-line tool, `op`, unlocked by the 1Password desktop app.

The browser never sees the 1Password account password: `op` asks the desktop app, which shows
its own unlock prompt (system password or fingerprint). This module only runs `op` and decides
which logins belong to which site; it has no GTK, so it can be tested on its own. Every call
blocks, so the browser runs them off the main thread.

Turn on Settings → Developer → "Integrate with 1Password CLI" in the 1Password app first.
"""

import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse

LIST_TTL = 300  # seconds a list of logins (titles, usernames, websites: no secrets) is reused


class OPError(Exception):
    """kind: missing | setup | retry | cancelled | other; raw: op's own words"""

    def __init__(self, message, kind="other", raw=""):
        super().__init__(message)
        self.kind = kind
        self.raw = raw


def friendly(stderr):
    """`op`'s error output -> (message for a person, kind). kind: setup | retry | cancelled | other."""
    s = (stderr or "").strip()
    low = s.lower()
    # first: a slow answer. op words its timeout like the integration being off ("connecting to
    # desktop app timed out, make sure it is installed, running and CLI integration is enabled"),
    # which it usually isn't: the app was busy, or the machine was
    if "timed out" in low or "timeout" in low and "authorization" not in low:
        return ("1Password didn't answer in time. Make sure the 1Password app is open (unlock it if "
                "it's locked), then try again.", "retry")
    if "dismiss" in low or "cancel" in low or "authorization timeout" in low or "user did not" in low:
        return ("1Password stayed locked.", "cancelled")
    if ("no accounts configured" in low or "turn on the 1password desktop app integration" in low
            or "desktop app integration" in low):
        return ("1Password isn't connected yet. In the 1Password app, turn on Settings → Developer → "
                "Integrate with 1Password CLI, then try again.", "setup")
    if ("connecting to desktop app" in low or "cannot connect to 1password" in low
            or "not currently signed in" in low or "account is not signed in" in low):
        return ("Couldn't reach the 1Password app. Make sure it's open and signed in, then try again.", "retry")
    if "multiple accounts" in low:
        return ("More than one 1Password account is signed in. Set \"op_account\" in prefs.json to the one to use.", "setup")
    first = re.sub(r"^\[ERROR\]\s*\S+\s+\S+\s*", "", s.splitlines()[0]) if s else "1Password didn't answer."
    return (first, "other")


def raw_error(stderr):
    """op's own words, without its '[ERROR] date time' prefix, shown under the friendly message."""
    s = (stderr or "").strip()
    return re.sub(r"^\[ERROR\]\s*\S+\s+\S+\s*", "", s.splitlines()[0]) if s else ""


def host_of(url):
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def bare(host):
    return host[4:] if host.startswith("www.") else host


def host_matches(page_host, item_url):
    """Whether a login saved for item_url may be filled on page_host.

    Strict on purpose, since this is what stops a look-alike site getting a password: the page
    must be the login's own host or a subdomain of it ('example.com' fills on 'login.example.com',
    never the other way round, and never on 'example.com.evil.net' or 'notexample.com')."""
    ih, ph = bare(host_of(item_url)), bare((page_host or "").lower().rstrip("."))
    if not ih or not ph:
        return False
    return ph == ih or ph.endswith("." + ih)


def is_local(host):
    return (host in ("localhost", "127.0.0.1", "::1") or host.endswith((".local", ".lan", ".home.arpa", ".localhost"))
            or bool(re.match(r"^(10|127)\.|^192\.168\.|^172\.(1[6-9]|2\d|3[01])\.", host)))


class OnePassword:
    def __init__(self, exe=None, account=None):
        self.exe = exe or os.environ.get("SIMPLEHOME_OP") or shutil.which("op")
        self.account = account
        self._logins = None
        self._logins_at = 0

    @property
    def available(self):
        return bool(self.exe)

    @property
    def has_list(self):
        return self._logins is not None

    def run(self, args, stdin=None, timeout=120, _retried=False):
        if not self.exe:
            raise OPError("The 1Password command-line tool (op) isn't installed:  sudo pacman -S 1password-cli", "missing")
        cmd = [self.exe] + args + (["--account", self.account] if self.account else [])
        env = dict(os.environ, OP_FORMAT="json")
        try:
            r = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            raise OPError("1Password took too long to answer.", "cancelled")
        except OSError as e:
            raise OPError(str(e), "missing")
        if r.returncode != 0:
            err = r.stderr or r.stdout
            if "timed out" in err.lower() and not _retried:
                return self.run(args, stdin, timeout, _retried=True)  # a busy machine: once more
            raise OPError(*friendly(err), raw=raw_error(err))
        return r.stdout

    def logins(self, refresh=False):
        """Every Login item as {id, title, username, urls, vault}. May show 1Password's unlock prompt."""
        if not refresh and self._logins is not None and time.time() - self._logins_at < LIST_TTL:
            return self._logins
        out = self.run(["item", "list", "--categories", "Login", "--format", "json"])
        try:
            items = json.loads(out or "[]")
        except ValueError:
            raise OPError("1Password's answer couldn't be read.")
        self._logins = [{
            "id": it.get("id", ""),
            "title": it.get("title", ""),
            "username": it.get("additional_information", ""),
            "urls": [u.get("href", "") for u in it.get("urls", []) if u.get("href")],
            "vault": (it.get("vault") or {}).get("name", ""),
        } for it in items if it.get("id")]
        self._logins_at = time.time()
        return self._logins

    def matching(self, page_host, logins=None):
        logins = self.logins() if logins is None else logins
        found = [it for it in logins if any(host_matches(page_host, u) for u in it["urls"])]
        # the login saved for exactly this host first, then by title
        found.sort(key=lambda it: (not any(bare(host_of(u)) == bare(page_host) for u in it["urls"]), it["title"].lower()))
        return found

    def credentials(self, item_id):
        """{username, password, totp, urls} for one item, with the secrets revealed."""
        out = self.run(["item", "get", item_id, "--reveal", "--format", "json"])
        try:
            it = json.loads(out)
        except ValueError:
            raise OPError("1Password's answer couldn't be read.")
        user = pw = totp = ""
        for f in it.get("fields", []):
            purpose, ftype = f.get("purpose"), f.get("type")
            if purpose == "USERNAME" and not user:
                user = f.get("value") or ""
            elif purpose == "PASSWORD" and not pw:
                pw = f.get("value") or ""
            elif ftype == "OTP" and not totp:
                totp = f.get("totp") or ""
        return {"username": user, "password": pw, "totp": totp,
                "urls": [u.get("href", "") for u in it.get("urls", []) if u.get("href")]}

    def save(self, url, username, password, title=None):
        """A new Login item. The password goes in on stdin, never on the command line where
        every other program could read it from the process list."""
        host = bare(host_of(url))
        item = {
            "title": title or host,
            "category": "LOGIN",
            "fields": [
                {"id": "username", "type": "STRING", "purpose": "USERNAME", "label": "username", "value": username},
                {"id": "password", "type": "CONCEALED", "purpose": "PASSWORD", "label": "password", "value": password},
            ],
        }
        self.run(["item", "create", "-", "--url", url, "--format", "json"], stdin=json.dumps(item))
        self._logins = None  # the next lookup sees the new item

    def known(self, page_host, username):
        """Whether a cached list already holds a login for this site and username (None: no list yet)."""
        if self._logins is None:
            return None
        return any(it["username"].lower() == (username or "").lower() for it in self.matching(page_host, self._logins))
