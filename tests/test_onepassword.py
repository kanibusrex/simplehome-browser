"""1Password matching and `op` handling: python3 -m unittest discover tests  (no GTK, no real op)"""
import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import onepassword as op  # noqa: E402


class Matching(unittest.TestCase):
    def test_same_site_and_subdomains(self):
        self.assertTrue(op.host_matches("github.com", "https://github.com/login"))
        self.assertTrue(op.host_matches("www.github.com", "github.com"))
        self.assertTrue(op.host_matches("gist.github.com", "https://github.com"))

    def test_never_look_alikes(self):
        for page in ("github.com.evil.net", "notgithub.com", "github.co", "evil.net"):
            self.assertFalse(op.host_matches(page, "https://github.com"), page)

    def test_parent_doesnt_get_a_subdomains_login(self):
        self.assertFalse(op.host_matches("example.com", "https://login.example.com"))

    def test_empty(self):
        self.assertFalse(op.host_matches("", "https://a.com"))
        self.assertFalse(op.host_matches("a.com", ""))

    def test_local(self):
        self.assertTrue(op.is_local("192.168.0.1"))
        self.assertTrue(op.is_local("nas.local"))
        self.assertFalse(op.is_local("example.com"))

    def test_ordering_exact_host_first(self):
        logins = [{"id": "1", "title": "A parent", "username": "", "urls": ["example.com"]},
                  {"id": "2", "title": "Z exact", "username": "", "urls": ["https://mail.example.com"]},
                  {"id": "3", "title": "Other", "username": "", "urls": ["https://other.com"]}]
        got = op.OnePassword(exe="/bin/false").matching("mail.example.com", logins)
        self.assertEqual([g["id"] for g in got], ["2", "1"])


class Errors(unittest.TestCase):
    def test_friendly(self):
        self.assertEqual(op.friendly("[ERROR] 2026/11/06 No accounts configured for use with 1Password CLI.")[1], "setup")
        self.assertEqual(op.friendly("[ERROR] 2026/11/06 authorization prompt dismissed, please try again")[1], "cancelled")
        msg, kind = op.friendly("[ERROR] 2026/11/06 17:00:00 something odd happened")
        self.assertEqual((msg, kind), ("something odd happened", "other"))

    def test_missing(self):
        with self.assertRaises(op.OPError) as c:
            op.OnePassword(exe="/nonexistent/op").run(["x"])
        self.assertEqual(c.exception.kind, "missing")


class FakeOp(unittest.TestCase):
    """Runs the real code against a tiny fake `op` that records its arguments."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.log = os.path.join(self.dir, "log")
        self.exe = os.path.join(self.dir, "op")
        with open(self.exe, "w") as f:
            f.write(f"""#!{sys.executable}
import json, sys
a = sys.argv[1:]
open({self.log!r}, "a").write(json.dumps(a) + "\\n")
if a[:2] == ["item", "list"]:
    print(json.dumps([{{"id": "i1", "title": "GitHub", "additional_information": "me", "urls": [{{"href": "https://github.com"}}]}}]))
elif a[:2] == ["item", "get"]:
    print(json.dumps({{"fields": [{{"purpose": "USERNAME", "value": "me"}}, {{"purpose": "PASSWORD", "value": "pw1"}},
                                  {{"type": "OTP", "totp": "654321"}}], "urls": [{{"href": "https://github.com"}}]}}))
elif a[:3] == ["item", "create", "-"]:
    open({self.log!r} + ".stdin", "w").write(sys.stdin.read()); print("{{}}")
""")
        os.chmod(self.exe, stat.S_IRWXU)
        self.op = op.OnePassword(exe=self.exe)

    def test_list_get_save(self):
        self.assertEqual([i["title"] for i in self.op.matching("github.com")], ["GitHub"])
        self.assertEqual(self.op.matching("gitlab.com"), [])
        c = self.op.credentials("i1")
        self.assertEqual((c["username"], c["password"], c["totp"]), ("me", "pw1", "654321"))
        self.op.save("https://new.example/", "newuser", "TOPSECRET")
        with open(self.log) as f:
            self.assertNotIn("TOPSECRET", f.read())  # never on the command line
        with open(self.log + ".stdin") as f:
            self.assertIn("TOPSECRET", f.read())
        self.assertFalse(self.op.has_list)  # a save forgets the cached list

    def test_known(self):
        self.assertIsNone(self.op.known("github.com", "me"))
        self.op.logins()
        self.assertTrue(self.op.known("github.com", "ME"))
        self.assertFalse(self.op.known("github.com", "someone"))


if __name__ == "__main__":
    unittest.main()
