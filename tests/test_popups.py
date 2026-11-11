"""popups.dismiss against real GTK pop-ups. Needs a display, so it's skipped without one;
on a desktop it shows a small window for a moment."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    HAVE_DISPLAY = Gtk.init_check(sys.argv)[0]
except (ImportError, ValueError):
    HAVE_DISPLAY = False

if HAVE_DISPLAY:
    import popups


def spin(ms=300):
    loop = GLib.MainLoop()
    GLib.timeout_add(ms, loop.quit)
    loop.run()


@unittest.skipUnless(HAVE_DISPLAY, "no display")
class DismissTest(unittest.TestCase):
    def setUp(self):
        self.win = Gtk.Window(title="popups test")
        self.btn = Gtk.Button(label="x")
        self.win.add(self.btn)
        self.win.set_default_size(200, 80)
        self.win.show_all()
        spin()

    def tearDown(self):
        self.win.destroy()
        spin(100)

    def menu(self, attach, items=1):
        m = Gtk.Menu()
        for i in range(items):
            m.append(Gtk.MenuItem(label="item %d" % i))
        m.attach_to_widget(attach, None)
        m.show_all()
        return m

    def test_menu_on_window_is_taken_down(self):
        m = self.menu(self.btn)
        m.popup_at_widget(self.btn, 8, 1, None)  # Gdk.Gravity.SOUTH_WEST / NORTH_WEST
        spin()
        self.assertTrue(m.get_toplevel().get_visible())
        self.assertEqual(popups.open_popups(self.win), [m.get_toplevel()])
        self.assertEqual(popups.dismiss(self.win), 1)
        self.assertFalse(m.get_toplevel().get_visible())
        self.assertEqual(popups.open_popups(self.win), [])

    def test_popup_on_a_popup_is_found_too(self):
        outer = self.menu(self.btn, 2)
        outer.popup_at_widget(self.btn, 8, 1, None)
        spin()
        item = outer.get_children()[0]
        inner = self.menu(item)
        inner.popup_at_widget(item, 8, 1, None)
        spin()
        found = popups.open_popups(self.win)
        self.assertIn(outer.get_toplevel(), found)
        self.assertIn(inner.get_toplevel(), found)
        popups.dismiss(self.win)
        self.assertFalse(outer.get_toplevel().get_visible())
        self.assertFalse(inner.get_toplevel().get_visible())

    def test_other_windows_popups_are_left_alone(self):
        other = Gtk.Window(title="popups test 2")
        ob = Gtk.Button(label="y")
        other.add(ob)
        other.show_all()
        spin()
        m = self.menu(ob)
        m.popup_at_widget(ob, 8, 1, None)
        spin()
        self.assertEqual(popups.dismiss(self.win), 0)
        self.assertTrue(m.get_toplevel().get_visible())
        m.popdown()
        other.destroy()

    def test_nothing_open(self):
        self.assertEqual(popups.dismiss(self.win), 0)


if __name__ == "__main__":
    unittest.main()
