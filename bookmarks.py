"""Bookmarks for SimpleHome Browser: a plain list kept in bookmarks.json, newest first.

No GTK in here, so it can be tested on its own. Each bookmark is {"u": url, "ti": title, "t": added}.
"""

import re
import time
import urllib.parse


def normal(url):
    """The form two addresses are compared in: no fragment, no trailing slash on a bare host."""
    url = (url or "").strip()
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    path = p.path if p.path not in ("", "/") else ""
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), path, p.query, ""))


def shown(url):
    """'https://www.example.com/a/' -> 'example.com/a'"""
    return re.sub(r"^https?://(www\.)?", "", url or "").rstrip("/")


class Bookmarks:
    def __init__(self, data=None):
        self.items = [b for b in (data or []) if isinstance(b, dict) and b.get("u")]

    def find(self, url):
        key = normal(url)
        for b in self.items:
            if normal(b["u"]) == key:
                return b
        return None

    def has(self, url):
        return self.find(url) is not None

    def add(self, url, title=""):
        """Adds it at the top; an address already saved keeps its place. Returns the bookmark."""
        b = self.find(url)
        if b:
            if title and not b.get("ti"):
                b["ti"] = title
            return b
        b = {"u": url, "ti": (title or "").strip(), "t": time.time()}
        self.items.insert(0, b)
        return b

    def remove(self, url):
        key = normal(url)
        before = len(self.items)
        self.items = [b for b in self.items if normal(b["u"]) != key]
        return len(self.items) != before

    def rename(self, url, title):
        b = self.find(url)
        if b:
            b["ti"] = (title or "").strip()
        return b

    def title_of(self, b):
        return b.get("ti") or shown(b["u"]) or b["u"]

    def search(self, query):
        """Every word must appear in the title or the address."""
        words = (query or "").lower().split()
        if not words:
            return list(self.items)
        out = []
        for b in self.items:
            hay = (b.get("ti", "") + " " + shown(b["u"])).lower()
            if all(w in hay for w in words):
                out.append(b)
        return out
