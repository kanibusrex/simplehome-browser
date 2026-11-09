"""Filter conversion tests: python3 -m unittest discover tests  (no GTK needed)"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import adblock  # noqa: E402


def matches(rule, url):
    """Rough stand-in for WebKit's matcher: the url-filter as a case-insensitive regex."""
    return re.search(rule["trigger"]["url-filter"], url, re.I) is not None


class Patterns(unittest.TestCase):
    def test_host_anchor(self):
        rx = adblock.pattern_regex("||ads.example.com^")
        r = {"trigger": {"url-filter": rx}}
        self.assertTrue(matches(r, "https://ads.example.com/x.js"))
        self.assertTrue(matches(r, "https://cdn.ads.example.com/x.js"))
        self.assertFalse(matches(r, "https://notads.example.com/x.js"))
        self.assertFalse(matches(r, "https://ads.example.community/"))

    def test_wildcards_and_separators(self):
        r = {"trigger": {"url-filter": adblock.pattern_regex("/banner/*/img^")}}
        self.assertTrue(matches(r, "https://x.com/banner/big/img?x=1"))
        self.assertFalse(matches(r, "https://x.com/banner/big/imgs"))

    def test_rejects_what_webkit_cannot_do(self):
        for p in ("", "*", "||", "a|b", "x{2}", "||*.example.com", "é.com"):
            self.assertIsNone(adblock.pattern_regex(p), p)

    def test_end_anchor(self):
        self.assertTrue(adblock.pattern_regex("swf|").endswith("$"))


class Filters(unittest.TestCase):
    def build(self, *lines):
        return adblock.build(["\n".join(lines)])[0]

    def test_plain_domains_fold_and_dedupe_subdomains(self):
        rules = self.build("||example.com^", "||ads.example.com^", "||other.net^")
        blocks = [r for r in rules if r["action"]["type"] == "block"]
        self.assertEqual(len(blocks), 2)

    def test_options(self):
        rules = self.build("||t.com^$script,third-party,domain=a.com|b.com")
        trig = rules[0]["trigger"]
        self.assertEqual(trig["resource-type"], ["script"])
        self.assertEqual(trig["load-type"], ["third-party"])
        self.assertEqual(trig["if-domain"], ["*a.com", "*b.com"])

    def test_subdocument_is_child_frame(self):
        trig = self.build("||f.com^$subdocument")[0]["trigger"]
        self.assertEqual(trig["resource-type"], ["document"])
        self.assertEqual(trig["load-context"], ["child-frame"])

    def test_unsupported_options_are_dropped_whole(self):
        rules, stats = adblock.build(["||x.com^$redirect=noop.js\n||y.com^$csp=script-src 'none'"])
        self.assertEqual(rules, [])
        self.assertEqual(stats["skipped"], 2)

    def test_order_blocks_hiding_exceptions_important(self):
        rules = self.build("@@||good.com^", "||bad.com^$important", "##.ad-banner", "/ads/x.js")
        kinds = [r["action"]["type"] for r in rules]
        self.assertEqual(kinds, ["block", "css-display-none", "ignore-previous-rules", "block"])

    def test_document_exception(self):
        r = self.build("@@||site.org^$document")[0]
        self.assertEqual(r["trigger"]["if-domain"], ["*site.org"])
        self.assertEqual(r["action"]["type"], "ignore-previous-rules")

    def test_cosmetic(self):
        rules = self.build("##.ad", "example.com##.promo", "example.com#@#.ad", "##div", "x.com##:has-text(Ad)")
        hide = [r for r in rules if r["action"]["type"] == "css-display-none"]
        by_sel = {r["action"]["selector"]: r["trigger"] for r in hide}
        self.assertEqual(by_sel[".ad"].get("unless-domain"), ["*example.com"])
        self.assertEqual(by_sel[".promo"]["if-domain"], ["*example.com"])
        self.assertNotIn("div", by_sel)  # bare element selectors are too broad for every site
        self.assertEqual(len(hide), 2)

    def test_every_site_hiding_can_be_left_out(self):
        text = "||ads.com^\n##.ad\n##.banner\nexample.com##.promo\nexample.com#@#.ad"
        rules, stats = adblock.build([text], generic_hide=False)
        hide = [r for r in rules if r["action"]["type"] == "css-display-none"]
        self.assertEqual([r["action"]["selector"] for r in hide], [".promo"])  # per-site hiding stays
        self.assertTrue(all("if-domain" in r["trigger"] for r in hide))
        self.assertTrue(any(r["action"]["type"] == "block" for r in rules))  # blocking is untouched
        self.assertEqual(stats["generic_left_out"], 2)
        self.assertEqual(adblock.build([text])[1]["generic_left_out"], 0)

    def test_allowlist_comes_last(self):
        rules = adblock.build(["||bad.com^\n##.ad"], allowlist=["mine.com"])[0]
        self.assertEqual(rules[-1]["trigger"]["if-domain"], ["*mine.com"])

    def test_split_and_drop_selectors(self):
        self.assertEqual(adblock.split_selectors('a, b[x=","], c:not(.d, .e)'), ["a", 'b[x=","]', "c:not(.d, .e)"])
        rules = [{"trigger": {"url-filter": ".*"}, "action": {"type": "css-display-none", "selector": ".a, ::bad, .b"}}]
        out = adblock.drop_selectors(rules, {"::bad"})
        self.assertEqual(out[0]["action"]["selector"], ".a, .b")
        self.assertEqual(adblock.drop_selectors(rules, {".a", "::bad", ".b"}), [])

    def test_comments_and_headers(self):
        self.assertEqual(self.build("[Adblock Plus 2.0]", "! Title: x", "", "# not a filter"), [])


if __name__ == "__main__":
    unittest.main()
