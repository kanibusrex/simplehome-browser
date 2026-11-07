import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import bookmarks  # noqa: E402


class BookmarksTest(unittest.TestCase):
    def test_add_newest_first_and_no_duplicates(self):
        b = bookmarks.Bookmarks()
        b.add("https://a.example/", "A")
        b.add("https://b.example/x", "B")
        b.add("https://a.example", "again")
        self.assertEqual([x["u"] for x in b.items], ["https://b.example/x", "https://a.example/"])
        self.assertEqual(b.find("https://A.example/#top")["ti"], "A")

    def test_has_ignores_fragment_and_bare_slash_only(self):
        b = bookmarks.Bookmarks([{"u": "https://ex.com/page?q=1", "ti": "P"}])
        self.assertTrue(b.has("https://ex.com/page?q=1#s"))
        self.assertFalse(b.has("https://ex.com/page?q=2"))
        self.assertFalse(b.has("http://ex.com/page?q=1"))

    def test_remove_and_rename(self):
        b = bookmarks.Bookmarks()
        b.add("https://ex.com/", "")
        self.assertEqual(b.title_of(b.items[0]), "ex.com")
        b.rename("https://ex.com", "  Example ")
        self.assertEqual(b.items[0]["ti"], "Example")
        self.assertTrue(b.remove("https://ex.com/#x"))
        self.assertFalse(b.remove("https://ex.com/"))
        self.assertEqual(b.items, [])

    def test_search(self):
        b = bookmarks.Bookmarks()
        b.add("https://www.youtube.com/feed/subscriptions", "Subscriptions - YouTube")
        b.add("https://github.com/kanibusrex", "kanibusrex")
        self.assertEqual(len(b.search("")), 2)
        self.assertEqual([x["ti"] for x in b.search("you subs")], ["Subscriptions - YouTube"])
        self.assertEqual([x["ti"] for x in b.search("GITHUB")], ["kanibusrex"])
        self.assertEqual(b.search("nothing"), [])

    def test_bad_data_skipped(self):
        b = bookmarks.Bookmarks([{"u": "https://ok/"}, {"ti": "no url"}, "junk", None])
        self.assertEqual(len(b.items), 1)


if __name__ == "__main__":
    unittest.main()
