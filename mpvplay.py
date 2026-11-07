"""Play YouTube videos in mpv (which looks them up with yt-dlp) instead of on the page.

YouTube's watch page runs 10-20 s of script on a slow CPU before the video settles; mpv skips the
page entirely and decodes on the GPU. No GTK in here, so it can be tested on its own.
"""
import os
import re
import shutil
import subprocess
import urllib.parse

# H.264 first (hardware-decoded nearly everywhere), then VP9; never AV1, which older GPUs can't decode
FORMAT = ("bv*[height<=1080][vcodec^=avc1]+ba[acodec^=mp4a]/bv*[height<=1080][vcodec^=vp09]+ba/"
          "bv*[height<=1080][vcodec!^=av01]+ba/b[height<=1080]/b")
HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
         "youtube-nocookie.com", "www.youtube-nocookie.com"}
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def parse_time(t):
    """YouTube's t= / start= ('90', '90s', '1h2m3s') -> seconds, or None."""
    if not t:
        return None
    if t.isdigit():
        return int(t)
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", t)
    if not m or not any(m.groups()):
        return None
    h, mi, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + s


def video_of(uri):
    """A YouTube video address -> (plain watch URL, start seconds or None); anything else -> None."""
    try:
        u = urllib.parse.urlsplit(uri or "")
    except ValueError:
        return None
    if u.scheme not in ("http", "https"):
        return None
    host = (u.hostname or "").lower()
    q = urllib.parse.parse_qs(u.query)
    parts = [p for p in u.path.split("/") if p]
    vid = None
    if host == "youtu.be" and parts:
        vid = parts[0]
    elif host in HOSTS:
        if u.path == "/watch":
            vid = (q.get("v") or [None])[0]
        elif len(parts) >= 2 and parts[0] in ("shorts", "live", "embed", "v"):
            vid = parts[1]
    if not vid or not VIDEO_ID.match(vid):
        return None
    start = parse_time((q.get("t") or q.get("start") or [""])[0].strip())
    return f"https://www.youtube.com/watch?v={vid}", start


def available():
    return shutil.which("mpv") is not None


def command(url, start=None):
    cmd = ["mpv", "--force-window=immediate", "--hwdec=auto-safe", "--profile=fast", f"--ytdl-format={FORMAT}"]
    if start and start >= 1:
        cmd.append(f"--start={int(start)}")
    return cmd + ["--", url]


def launch(url, start=None, log_path=None):
    """Start mpv on its own (it outlives the browser). Returns the Popen."""
    out = open(log_path, "w") if log_path else subprocess.DEVNULL
    try:
        return subprocess.Popen(command(url, start), stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True, env=os.environ.copy())
    finally:
        if log_path:
            out.close()


def failure(log_text):
    """The line worth showing from mpv's output when it gave up, or ''."""
    lines = [ln.strip() for ln in log_text.splitlines() if ln.strip()]
    for ln in reversed(lines):
        if "ERROR:" in ln:
            return ln.split("ERROR:", 1)[1].strip()
    for ln in reversed(lines):
        if "fail" in ln.lower() or "error" in ln.lower():
            return ln
    return lines[-1] if lines else ""
