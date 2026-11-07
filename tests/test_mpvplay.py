import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import mpvplay  # noqa: E402

W = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


class VideoOf(unittest.TestCase):
    def test_watch_pages(self):
        self.assertEqual(mpvplay.video_of(W), (W, None))
        self.assertEqual(mpvplay.video_of("https://m.youtube.com/watch?v=aqz-KE-bpKQ&list=PL1&index=3"), (W, None))
        self.assertEqual(mpvplay.video_of("https://music.youtube.com/watch?v=aqz-KE-bpKQ"), (W, None))

    def test_short_forms(self):
        self.assertEqual(mpvplay.video_of("https://youtu.be/aqz-KE-bpKQ?si=x"), (W, None))
        self.assertEqual(mpvplay.video_of("https://www.youtube.com/shorts/aqz-KE-bpKQ"), (W, None))
        self.assertEqual(mpvplay.video_of("https://www.youtube.com/live/aqz-KE-bpKQ?feature=share"), (W, None))
        self.assertEqual(mpvplay.video_of("https://www.youtube-nocookie.com/embed/aqz-KE-bpKQ"), (W, None))

    def test_start_time(self):
        self.assertEqual(mpvplay.video_of(W + "&t=90")[1], 90)
        self.assertEqual(mpvplay.video_of(W + "&t=90s")[1], 90)
        self.assertEqual(mpvplay.video_of("https://youtu.be/aqz-KE-bpKQ?t=1h2m3s")[1], 3723)
        self.assertEqual(mpvplay.video_of(W + "&t=junk")[1], None)

    def test_not_videos(self):
        for u in ("https://www.youtube.com/", "https://www.youtube.com/@blender", "https://www.youtube.com/results?search_query=x",
                  "https://www.youtube.com/watch?v=short", "https://evil.example/watch?v=aqz-KE-bpKQ",
                  "https://youtube.com.evil.example/watch?v=aqz-KE-bpKQ", "javascript:alert(1)", "", None,
                  "https://www.youtube.com/watch?v=aqz-KE-bpKQ;rm"):
            self.assertIsNone(mpvplay.video_of(u), u)


class Command(unittest.TestCase):
    def test_url_after_double_dash(self):
        cmd = mpvplay.command(W, 42.7)
        self.assertEqual(cmd[0], "mpv")
        self.assertEqual(cmd[-2:], ["--", W])
        self.assertIn("--start=42", cmd)
        self.assertNotIn("--start", " ".join(mpvplay.command(W, None)))

    def test_failure_line(self):
        log = "[ytdl_hook] \n[ytdl_hook] ERROR: [youtube] abc: Sign in to confirm your age\nExiting... (Errors when loading file)\n"
        self.assertEqual(mpvplay.failure(log), "[youtube] abc: Sign in to confirm your age")
        self.assertEqual(mpvplay.failure(""), "")


if __name__ == "__main__":
    unittest.main()
