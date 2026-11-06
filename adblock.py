"""EasyList-style filter lists -> a WebKit content blocker.

WebKit has a built-in content blocker (the format Safari's blockers use): a JSON list of
trigger/action rules compiled once into a matcher that runs inside WebKit itself, on every
request and every page, with nothing in Python on the hot path. This module turns Adblock
Plus filter syntax into that JSON. It is a port of SimpleHome's Chrome extension parser
(extension/lib/parser.js) with the same rule of thumb: where a filter uses something WebKit
cannot express, the filter is dropped whole rather than approximated, because a rule that is
nearly right breaks pages in ways that are very hard to trace back to the line that caused it.

No GTK in here, so it can be tested on its own (tests/test_adblock.py).
"""

import json
import re

LISTS = {
    "easylist": {"name": "EasyList", "url": "https://easylist.to/easylist/easylist.txt",
                 "about": "Ads", "default": True},
    "easyprivacy": {"name": "EasyPrivacy", "url": "https://easylist.to/easylist/easyprivacy.txt",
                    "about": "Trackers and analytics", "default": True},
    "annoyances": {"name": "Annoyances", "url": "https://easylist.to/easylist/fanboy-annoyance.txt",
                   "about": "Cookie notices, pop-ups, social widgets", "default": False},
}

MAX_PATTERN = 500            # longer ones are machine-generated one-offs
SELECTORS_PER_RULE = 400     # generic hiding selectors grouped per rule

# ABP type options -> WebKit resource types. Subframes are "document" plus a load context.
TYPES = {
    "script": "script", "image": "image", "stylesheet": "style-sheet", "css": "style-sheet",
    "font": "font", "media": "media", "xmlhttprequest": "fetch", "xhr": "fetch",
    "websocket": "websocket", "ping": "ping", "beacon": "ping", "other": "other",
    "object": "other", "object-subrequest": "other", "popup": "popup",
    "subdocument": "subdocument", "frame": "subdocument", "document": "document", "doc": "document",
}
ALL_TYPES = ["script", "image", "style-sheet", "font", "media", "fetch", "websocket", "ping",
             "other", "svg-document", "subdocument"]

# Options that change what a rule does in ways WebKit cannot express; dropping the option
# would usually turn a narrow rule into a broad one, so the whole filter goes.
UNSUPPORTED = {
    "csp", "redirect", "redirect-rule", "removeparam", "removeheader", "replace", "popunder",
    "empty", "mp4", "inline-script", "inline-font", "genericblock", "generichide",
    "specifichide", "elemhide", "ehide", "stealth", "cookie", "app", "denyallow", "header",
    "method", "permissions", "urltransform", "uritransform", "hls", "jsonprune", "badfilter",
    "all", "to", "from", "ipaddress", "strict1p", "strict3p", "webrtc", "network", "extension",
    "content", "match-case-header", "referrerpolicy", "rewrite", "sitekey",
}

COSMETIC_SEPS = [("#@$?#", False, False), ("#@?#", False, False), ("#@$#", False, False),
                 ("#$?#", True, False), ("#@#", False, True), ("#?#", True, False),
                 ("#$#", True, False), ("#%#", True, False), ("##", True, True)]
PROCEDURAL = re.compile(
    r":(?:-abp-[a-z-]+|has-text|matches-css(?:-before|-after)?|matches-attr|matches-path|"
    r"matches-media|matches-property|xpath|style|remove|upward|watch-attr|contains|if|if-not|"
    r"min-text-length|nth-ancestor|others|shadow)\(", re.I)
DOMAIN = re.compile(r"^[a-z0-9._-]+$")
OPTION = re.compile(r"^~?[a-z0-9_-]+(=[^,]*)?$", re.I)
ASCII = re.compile(r"^[\x20-\x7e]+$")

# What '||' means: the start of the host or of one of its labels
HOST_START = r"^[a-z][a-z0-9.+-]*://([^/?#]*\.)?"
SEPARATOR = r"[^a-z0-9_.%-]"


def is_comment(line):
    return (not line or line.startswith("!") or line.startswith("[Adblock")
            or (line.startswith("#") and not any(line.startswith(s) for s, _, _ in COSMETIC_SEPS)))


def split_options(text):
    start = 0
    while True:
        at = text.find("$", start)
        if at == -1:
            return text, ""
        tail = text[at + 1:]
        if tail and all(OPTION.match(t) for t in tail.split(",")):
            return text[:at], tail
        start = at + 1


def domain_list(value, sep):
    inc, exc = [], []
    for raw in value.split(sep):
        d = raw.strip().lower()
        if not d:
            continue
        neg = d.startswith("~")
        d = d[1:] if neg else d
        if d.startswith("*."):
            d = d[2:]
        if not d or not DOMAIN.match(d) or d.endswith("."):
            return None  # entity domains ('example.*'), regexes, IPv6: nothing WebKit can match
        (exc if neg else inc).append(d)
    return inc, exc


def pattern_regex(pattern):
    """An ABP URL pattern as the regex subset WebKit's url-filter accepts, or None."""
    if not pattern or pattern in ("*", "|", "||") or len(pattern) > MAX_PATTERN:
        return None
    if not ASCII.match(pattern) or pattern.startswith("||*"):
        return None
    out = ""
    if pattern.startswith("||"):
        out, pattern = HOST_START, pattern[2:]
    elif pattern.startswith("|"):
        out, pattern = "^", pattern[1:]
    end = ""
    if pattern.endswith("|"):
        end, pattern = "$", pattern[:-1]
    if not pattern or "|" in pattern:
        return None
    pattern = re.sub(r"\*+", "*", pattern)
    for ch in pattern:
        if ch == "*":
            out += ".*"
        elif ch == "^":
            out += SEPARATOR
        elif ch in ".+?()[]{}\\$":
            if ch in "{}":
                return None
            out += "\\" + ch
        else:
            out += ch.lower()
    out += end
    # a leading/trailing '.*' adds nothing to an unanchored match and only slows WebKit down
    if out.startswith(".*"):
        out = out[2:]
    if out.endswith(".*"):
        out = out[:-2]
    return out or None


def parse_network(line):
    """One network filter -> a dict, or a skip reason (str)."""
    text = line
    exception = text.startswith("@@")
    if exception:
        text = text[2:]
    if text.startswith("/") and (text.endswith("/") or "/$" in text):
        return "regex"  # ABP regex filters lean on |, {} and \d, none of which WebKit has
    pattern, opts = split_options(text)
    f = {"exception": exception, "pattern": pattern, "types": set(), "not_types": set(),
         "party": None, "domains": [], "not_domains": [], "case": False, "important": False}
    for tok in opts.split(",") if opts else []:
        neg = tok.startswith("~")
        body = tok[1:] if neg else tok
        name, _, value = body.partition("=")
        name = name.lower()
        if name in UNSUPPORTED:
            return "unsupported-option"
        if name in TYPES:
            (f["not_types"] if neg else f["types"]).add(TYPES[name])
        elif name in ("third-party", "3p"):
            f["party"] = "first-party" if neg else "third-party"
        elif name in ("first-party", "1p"):
            f["party"] = "third-party" if neg else "first-party"
        elif name == "domain":
            dl = domain_list(value, "|")
            if dl is None or not (dl[0] or dl[1]):
                return "unsupported-domain"
            f["domains"], f["not_domains"] = dl
        elif name == "match-case":
            f["case"] = not neg
        elif name == "important":
            f["important"] = True
        else:
            return "unknown-option"
    return f


def plain_domain(f):
    """'||example.com^' and nothing else -> 'example.com'."""
    m = re.match(r"^\|\|([a-z0-9-]+(?:\.[a-z0-9-]+)+)\^$", f["pattern"].lower())
    return m.group(1) if m else None


def network_rules(f):
    """The WebKit rules for one parsed network filter ([] when it cannot be expressed)."""
    trig = {}
    if f["exception"] and "document" in f["types"]:
        # '@@||site^$document': nothing is blocked on pages of that site
        d = plain_domain(f)
        if not d:
            return []
        return [{"trigger": {"url-filter": ".*", "if-domain": ["*" + d]},
                 "action": {"type": "ignore-previous-rules"}}]
    rx = pattern_regex(f["pattern"])
    if not rx:
        return []
    trig["url-filter"] = rx
    if f["case"]:
        trig["url-filter-is-case-sensitive"] = True
    if f["party"]:
        trig["load-type"] = [f["party"]]
    # if-domain and unless-domain cannot be combined; keeping the includes is the narrower rule
    if f["domains"]:
        trig["if-domain"] = ["*" + d for d in f["domains"]]
    elif f["not_domains"]:
        trig["unless-domain"] = ["*" + d for d in f["not_domains"]]
    types = set(f["types"]) or (set(ALL_TYPES) - f["not_types"] if f["not_types"] else set())
    if len(types) > 1:
        types.discard("document")  # never block whole pages because a list named them alongside
    action = {"type": "ignore-previous-rules" if f["exception"] else "block"}
    out = []
    plain = sorted(t for t in types if t != "subdocument")
    if "subdocument" in types:
        out.append({"trigger": dict(trig, **{"resource-type": ["document"],
                                             "load-context": ["child-frame"]}), "action": action})
    if plain == ["document"]:
        out.append({"trigger": dict(trig, **{"resource-type": ["document"],
                                             "load-context": ["top-frame"]}), "action": action})
    elif plain:
        out.append({"trigger": dict(trig, **{"resource-type": plain}), "action": action})
    elif not types:
        out.append({"trigger": trig, "action": action})
    return out


def parse_cosmetic(line):
    """'domains##selector' -> dict, a skip reason, or None when it is not cosmetic at all."""
    h = line.find("#")
    if h == -1:
        return None
    sep = next((s for s in COSMETIC_SEPS if line.startswith(s[0], h)), None)
    if not sep:
        return None
    sepstr, hide, plain = sep
    inc, exc, dropped = [], [], 0
    for raw in line[:h].split(","):
        d = raw.strip().lower()
        if not d:
            continue
        neg = d.startswith("~")
        d = d[1:] if neg else d
        if "*" in d or d.startswith("/"):
            dropped += 1
            continue
        if not DOMAIN.match(d):
            return None  # the '#' belonged to a URL: a network filter
        (exc if neg else inc).append(d)
    if not plain:
        return "procedural"
    sel = line[h + len(sepstr):].strip()
    if not sel:
        return "empty-selector"
    if sel.startswith("+js(") or "{" in sel or PROCEDURAL.search(sel):
        return "procedural"
    if not ASCII.match(sel):
        return "non-ascii"
    scoped = bool(inc or exc)
    if not scoped and dropped:
        return "unsupported-domain"
    if not scoped and not re.search(r"[.#\[:]", sel):
        return "unsafe-generic"  # '##div' on every site takes down far too much
    return {"hide": hide, "selector": sel, "domains": inc, "not_domains": exc}


def build(texts, allowlist=()):
    """Filter list texts -> (WebKit rules, stats). allowlist: hosts where nothing is blocked."""
    blocks, important, allows = [], [], []
    domains = set()
    generic, generic_except = set(), {}
    scoped = {}  # frozenset(domains) -> set(selectors)
    seen = set()
    stats = {"lines": 0, "rules": 0, "skipped": 0, "why": {}}

    def skip(why):
        stats["skipped"] += 1
        stats["why"][why] = stats["why"].get(why, 0) + 1

    for text in texts:
        for line in text.splitlines():
            line = line.strip()
            if is_comment(line):
                continue
            stats["lines"] += 1
            c = parse_cosmetic(line)
            if isinstance(c, str):
                skip(c)
                continue
            if c:
                if not c["hide"]:
                    for d in c["domains"]:
                        generic_except.setdefault(c["selector"], set()).add(d)
                elif not c["domains"]:
                    generic.add(c["selector"])
                    for d in c["not_domains"]:
                        generic_except.setdefault(c["selector"], set()).add(d)
                else:
                    scoped.setdefault(frozenset(c["domains"]), set()).add(c["selector"])
                continue
            f = parse_network(line)
            if isinstance(f, str):
                skip(f)
                continue
            simple = (not f["exception"] and not f["important"] and not f["case"] and not f["types"]
                      and not f["not_types"] and not f["party"] and not f["domains"]
                      and not f["not_domains"])
            d = plain_domain(f) if simple else None
            if d and not re.match(r"^\d+(\.\d+){3}$", d):
                domains.add(d)
                continue
            rules = network_rules(f)
            if not rules:
                skip("unsupported-pattern")
                continue
            key = json.dumps(rules, sort_keys=True)
            if key in seen:
                skip("duplicate")
                continue
            seen.add(key)
            (allows if f["exception"] else important if f["important"] else blocks).extend(rules)

    # a domain already blocked by a parent ('ads.example.com' under 'example.com') adds nothing
    def covered(d):
        parts = d.split(".")
        return any(".".join(parts[i:]) in domains for i in range(1, len(parts) - 1))
    for d in sorted(d for d in domains if not covered(d)):
        blocks.append({"trigger": {"url-filter": HOST_START + re.escape(d).replace("\\-", "-") + SEPARATOR},
                       "action": {"type": "block"}})

    hide = []
    plain_generic = sorted(s for s in generic if s not in generic_except)
    for i in range(0, len(plain_generic), SELECTORS_PER_RULE):
        hide.append({"trigger": {"url-filter": ".*"},
                     "action": {"type": "css-display-none",
                                "selector": ", ".join(plain_generic[i:i + SELECTORS_PER_RULE])}})
    for s in sorted(s for s in generic if s in generic_except):
        hide.append({"trigger": {"url-filter": ".*",
                                 "unless-domain": sorted("*" + d for d in generic_except[s])},
                     "action": {"type": "css-display-none", "selector": s}})
    for doms, sels in sorted(scoped.items(), key=lambda kv: sorted(kv[0])):
        hide.append({"trigger": {"url-filter": ".*", "if-domain": sorted("*" + d for d in doms)},
                     "action": {"type": "css-display-none", "selector": ", ".join(sorted(sels))}})

    own = [{"trigger": {"url-filter": ".*", "if-domain": ["*" + h]},
            "action": {"type": "ignore-previous-rules"}} for h in sorted(set(allowlist))]
    # order matters: a later ignore-previous-rules cancels everything matched before it, so
    # exceptions follow blocks and hiding, '$important' blocks follow exceptions, and the
    # person's own "not on this site" list comes last of all so nothing overrides it
    rules = blocks + hide + allows + important + own
    stats["rules"] = len(rules)
    stats["domains"] = len(domains)
    stats["hidden"] = len(generic) + sum(len(s) for s in scoped.values())
    return rules, stats


def selectors_of(rules):
    """Every css-display-none rule's selector list, for validating them before compiling."""
    return [r["action"]["selector"] for r in rules if r["action"]["type"] == "css-display-none"]


def split_selectors(sel):
    """'a, b[x=","], c' -> ['a', 'b[x=","]', 'c'] (commas inside brackets/quotes stay)."""
    out, depth, quote, cur = [], 0, None, ""
    for ch in sel:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
            continue
        cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def drop_selectors(rules, bad):
    """The rules without the selectors WebKit's CSS parser refused (one bad selector in a
    list would make the browser skip that whole list)."""
    if not bad:
        return rules
    out = []
    for r in rules:
        if r["action"]["type"] == "css-display-none":
            keep = [s for s in split_selectors(r["action"]["selector"]) if s not in bad]
            if not keep:
                continue
            r = {"trigger": r["trigger"], "action": {"type": "css-display-none", "selector": ", ".join(keep)}}
        out.append(r)
    return out
