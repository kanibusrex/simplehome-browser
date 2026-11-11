"""Taking a window's pop-ups down before the window itself goes.

On Wayland, GTK 3 keeps a pointer from each pop-up (a menu, the address bar's suggestions,
WebKit's right-click menu or a page's <select> list) to the window it sits on. If that window
is destroyed while a pop-up is still up, Hyprland's next "here is where I put you" message for
the pop-up makes GTK read the freed window, and the browser segfaults. This happened on
2026-11-10 (xdg_popup_configure -> calculate_moved_to_rect_result ->
gdk_window_get_effective_parent on a freed GdkWindow). Hiding the pop-ups first ends their
Wayland surfaces, so that message can't arrive.
"""
from gi.repository import Gtk


def _parent(popup):
    """The window a pop-up sits on: what it's transient for, or, for a menu GTK didn't mark
    that way (a submenu), the window holding the widget it's attached to."""
    parent = popup.get_transient_for()
    if parent is None:
        child = popup.get_child()
        if isinstance(child, Gtk.Menu) and child.get_attach_widget() is not None:
            parent = child.get_attach_widget().get_toplevel()
    return parent


def open_popups(win):
    """The pop-ups showing on win, directly or on top of another of its pop-ups,
    outermost first."""
    candidates = [t for t in Gtk.Window.list_toplevels()
                  if t is not win and t.get_window_type() == Gtk.WindowType.POPUP and t.get_visible()]
    owners, found = [win], []
    grew = True
    while grew:
        grew = False
        for p in candidates:
            if p not in found and _parent(p) in owners:
                found.append(p)
                owners.append(p)
                grew = True
    return found


def dismiss(win):
    """Take down every pop-up showing on win, innermost first. Returns how many there were."""
    found = open_popups(win)
    for p in reversed(found):
        child = p.get_child()
        if isinstance(child, Gtk.Menu):
            child.popdown()  # also lets go of the pointer and keyboard grab
        else:
            p.hide()
    return len(found)
