import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import onscreen  # noqa: E402

MON = [{"activeWorkspace": {"id": 1}, "specialWorkspace": {"id": 0}},
       {"activeWorkspace": {"id": 4}, "specialWorkspace": {"id": -98}}]


def win(addr, ws, title="T", pid=7, **kw):
    return dict(address=addr, workspace={"id": ws}, title=title, pid=pid, fullscreen=0, hidden=False, mapped=True, **kw)


class ShownTitles(unittest.TestCase):
    def test_workspace_showing_or_not(self):
        got = onscreen.shown_titles([win("a", 1, "one"), win("b", 2, "two"), win("c", 4, "three")], MON, 7)
        self.assertEqual(got, {"one": True, "two": False, "three": True})

    def test_special_workspace_open(self):
        self.assertEqual(onscreen.shown_titles([win("a", -98, "s")], MON, 7), {"s": True})
        self.assertEqual(onscreen.shown_titles([win("a", -99, "s")], MON, 7), {"s": False})

    def test_other_process_ignored(self):
        self.assertEqual(onscreen.shown_titles([win("a", 1, pid=8)], MON, 7), {})

    def test_full_screen_window_covers(self):
        other = win("z", 1, "game", pid=9)
        other["fullscreen"] = 2
        self.assertEqual(onscreen.shown_titles([win("a", 1), other], MON, 7), {"T": False})
        mine = win("a", 1)
        mine["fullscreen"] = 2
        self.assertEqual(onscreen.shown_titles([mine], MON, 7), {"T": True})
        other["fullscreen"] = 1  # maximised doesn't hide the rest
        self.assertEqual(onscreen.shown_titles([win("a", 1), other], MON, 7), {"T": True})

    def test_hidden_group_tab(self):
        w = win("a", 1)
        w["hidden"] = True
        self.assertEqual(onscreen.shown_titles([w], MON, 7), {"T": False})

    def test_same_title_any_shown(self):
        self.assertEqual(onscreen.shown_titles([win("a", 2), win("b", 1)], MON, 7), {"T": True})


if __name__ == "__main__":
    unittest.main()
