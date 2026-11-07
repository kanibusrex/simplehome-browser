"""Which of the browser's windows Hyprland is actually showing.

Hyprland keeps a window on a workspace you've left running as if it were on screen, so a page
there never sees document.hidden and keeps animating (SimpleHome's galaxy theme: half a core,
measured). The browser asks Hyprland which windows are showing, and hides the page in a window
that isn't, which is what WebKit needs to throttle it. Off Hyprland this does nothing.

Uses Hyprland's two sockets directly: .socket2.sock streams events, and .socket.sock answers
questions ("j/clients"), so nothing is spawned.
"""

import json
import os
import socket

from gi.repository import Gio, GLib

# events after which a window may have come into or gone out of view
EVENTS = {
    "workspace", "workspacev2", "focusedmon", "focusedmonv2", "activespecial", "activespecialv2",
    "movewindow", "movewindowv2", "fullscreen", "openwindow", "closewindow", "moveworkspace",
    "moveworkspacev2", "monitoradded", "monitoraddedv2", "monitorremoved", "monitorremovedv2",
    "changefloatingmode", "togglegroup", "moveintogroup", "moveoutofgroup", "activewindowv2",
}


def hypr_dir():
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    run = os.environ.get("XDG_RUNTIME_DIR")
    if not sig or not run:
        return None
    path = os.path.join(run, "hypr", sig)
    return path if os.path.exists(os.path.join(path, ".socket2.sock")) else None


def shown_titles(clients, monitors, pid):
    """{title: on screen} for this process's windows. A window is on screen when its workspace
    is showing on some monitor (or is the special workspace open there), it isn't a hidden tab
    of a group, and no other window is full screen over it. Two windows with one title count as
    on screen if either is."""
    showing = set()
    for m in monitors:
        for key in ("activeWorkspace", "specialWorkspace"):
            ws = (m.get(key) or {}).get("id")
            if ws:
                showing.add(ws)
    covered = {}  # workspace -> the window full screen on it
    for c in clients:
        fs = c.get("fullscreen")
        if fs is True or (type(fs) is int and fs == 2):  # 1 is maximised; True is older Hyprland
            covered[(c.get("workspace") or {}).get("id")] = c.get("address")
    out = {}
    for c in clients:
        if c.get("pid") != pid:
            continue
        ws = (c.get("workspace") or {}).get("id")
        on = (ws in showing and not c.get("hidden") and c.get("mapped", True)
              and covered.get(ws, c.get("address")) == c.get("address"))
        title = c.get("title", "")
        out[title] = out.get(title, False) or on
    return out


class Watcher:
    """Calls changed() (coalesced) whenever Hyprland reports something that can move a window
    into or out of view."""

    def __init__(self, changed):
        self.dir = hypr_dir()
        self.changed = changed
        self._idle = 0
        self.conn = None
        if not self.dir:
            return
        try:
            addr = Gio.UnixSocketAddress.new(os.path.join(self.dir, ".socket2.sock"))
            self.conn = Gio.SocketClient().connect(addr, None)
        except GLib.Error:
            self.conn = None
            return
        self.stream = Gio.DataInputStream.new(self.conn.get_input_stream())
        self._read()

    @property
    def active(self):
        return self.conn is not None

    def _read(self):
        self.stream.read_line_async(GLib.PRIORITY_LOW, None, self._line)

    def _line(self, stream, res):
        try:
            line, _n = stream.read_line_finish_utf8(res)
        except GLib.Error:
            line = None
        if line is None:  # Hyprland went away
            self.conn = None
            return
        if line.split(">>", 1)[0] in EVENTS and not self._idle:
            self._idle = GLib.idle_add(self._fire)
        self._read()

    def _fire(self):
        self._idle = 0
        self.changed()
        return False

    def ask(self, what):
        """j/clients, j/monitors: Hyprland's answer as JSON, or None."""
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.connect(os.path.join(self.dir, ".socket.sock"))
                s.sendall(what.encode())
                data = b""
                while True:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    data += chunk
            return json.loads(data)
        except (OSError, ValueError):
            return None

    def titles(self, pid):
        clients, monitors = self.ask("j/clients"), self.ask("j/monitors")
        if clients is None or monitors is None:
            return None
        return shown_titles(clients, monitors, pid)
