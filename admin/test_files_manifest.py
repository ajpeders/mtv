"""Files mode: files_manifest.py output airs through the admin /now endpoint."""
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import files_manifest  # noqa: E402
import schedule  # noqa: E402


def fake_probe(path):
    return 0 if "broken" in path else 60.0


class FilesManifest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "videos"
        self.channels = Path(self.tmp.name) / "channels.json"
        self.channels.write_text(json.dumps([{"num": 1, "name": "01", "playlist": ""}]))
        for rel in ["Blondie - Atomic.mp4", "dQw4w9WgXcQ.mp4", "80s/a-ha - Take On Me.mp4",
                    "90s/Blur - Song 2.mp4", "90s/deep/Pulp - Disco 2000.mp4",
                    # never aired: sidecars, partial downloads, hidden, zero duration
                    "dQw4w9WgXcQ.info.json", "x.f137.mp4", "x.temp.mp4",
                    "y.mp4.transcoding.mp4", ".hidden.mp4", ".archive/z.mp4", "broken.mp4",
                    "clip.MP4"]:
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
        (self.root / "dQw4w9WgXcQ.info.json").write_text(json.dumps(
            {"id": "dQw4w9WgXcQ", "title": "Rick Astley - Never Gonna Give You Up",
             "artist": "Rick Astley", "track": "Never Gonna Give You Up", "album": None}))

    def tearDown(self):
        self.tmp.cleanup()

    def build(self):
        return files_manifest.build(str(self.root), str(self.channels), probe=fake_probe)

    def test_ids_titles_and_skips(self):
        m = self.build()
        self.assertEqual([v["id"] for v in m["videos"]], [
            "80s/a-ha - Take On Me", "90s/Blur - Song 2", "90s/deep/Pulp - Disco 2000",
            "Blondie - Atomic", "dQw4w9WgXcQ"])
        by_id = {v["id"]: v for v in m["videos"]}
        self.assertEqual(by_id["Blondie - Atomic"]["title"], "Blondie - Atomic")
        self.assertEqual(by_id["80s/a-ha - Take On Me"]["title"], "a-ha - Take On Me")
        self.assertEqual(by_id["dQw4w9WgXcQ"], {
            "id": "dQw4w9WgXcQ", "title": "Rick Astley - Never Gonna Give You Up",
            "artist": "Rick Astley", "track": "Never Gonna Give You Up", "duration": 60.0})
        self.assertEqual(schedule.credit(by_id["Blondie - Atomic"])["artist"], "Blondie")

    def test_channels_from_lineup_and_folders(self):
        m = self.build()
        self.assertEqual(len(m["channels"]["1"]), 5)
        self.assertEqual(m["lineup"], [{"num": 2, "name": "80s", "slug": "80s"},
                                       {"num": 3, "name": "90s", "slug": "90s"}])
        self.assertEqual(m["channels"]["3"], ["90s/Blur - Song 2", "90s/deep/Pulp - Disco 2000"])

    def test_folder_numbers_are_sticky(self):
        (self.root / "manifest.json").write_text(json.dumps(
            {"lineup": [{"num": 7, "name": "90s", "slug": "90s"}]}))
        m = self.build()
        self.assertEqual([(c["slug"], c["num"]) for c in m["lineup"]], [("90s", 7), ("80s", 8)])

    def test_durations_cached_by_size_and_mtime(self):
        calls = []
        files_manifest.build(str(self.root), str(self.channels),
                             probe=lambda p: calls.append(p) or 60.0)
        self.assertEqual(len(calls), 6)
        calls.clear()
        files_manifest.build(str(self.root), str(self.channels),
                             probe=lambda p: calls.append(p) or 60.0)
        self.assertEqual(calls, [])
        (self.root / "Blondie - Atomic.mp4").write_bytes(b"new")
        files_manifest.build(str(self.root), str(self.channels),
                             probe=lambda p: calls.append(p) or 60.0)
        self.assertEqual([Path(p).name for p in calls], ["Blondie - Atomic.mp4"])

    def test_admin_now_airs_files(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("fastapi not installed")
        import main
        (self.root / "manifest.json").write_text(json.dumps(self.build()))
        main.CONFIG_DIR = self.channels.parent
        main.CHANNELS = self.channels
        main.VIDEOS = self.root
        main.MANIFEST = self.root / "manifest.json"
        client = TestClient(main.app)
        body = client.get("/admin/api/now?ch=1").json()
        self.assertTrue((self.root / (body["now"]["id"] + ".mp4")).is_file())
        body = client.get("/admin/api/now?ch=2").json()
        self.assertEqual(body["now"]["id"], "80s/a-ha - Take On Me")
        self.assertEqual((body["now"]["artist"], body["now"]["song"]), ("a-ha", "Take On Me"))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
class RealFiles(unittest.TestCase):
    """sync.sh's actual invocation, against real (tiny) mp4s."""

    def test_script_on_generated_videos(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "videos"
            (root / "Rock").mkdir(parents=True)
            for rel, secs in [("Artist One - First.mp4", 2), ("Rock/Artist Two - Second.mp4", 3)]:
                subprocess.run(
                    ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc=d={secs}:s=64x48",
                     "-pix_fmt", "yuv420p", str(root / rel)], check=True)
            channels = Path(tmp) / "channels.json"
            channels.write_text('[{"num": 1, "name": "01", "playlist": ""}]')
            subprocess.run([sys.executable, str(HERE.parent / "files_manifest.py"),
                            str(root), str(channels)], check=True, capture_output=True)
            m = json.loads((root / "manifest.json").read_text())
            durs = {v["id"]: v["duration"] for v in m["videos"]}
            self.assertEqual(set(durs), {"Artist One - First", "Rock/Artist Two - Second"})
            self.assertAlmostEqual(durs["Artist One - First"], 2, delta=0.2)
            self.assertEqual(m["channels"]["2"], ["Rock/Artist Two - Second"])
            self.assertIsNotNone(schedule.on_air(schedule.schedule_for(m, 1), time.time()))


if __name__ == "__main__":
    unittest.main()
