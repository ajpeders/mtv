import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "manifest.json").read_text())
LINEUP = [
    {"num": 1, "name": "01", "playlist": ""},
    {"num": 2, "name": "02", "playlist": ""},
]


class NowEndpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "videos").mkdir()
        main.CONFIG_DIR = root
        main.CHANNELS = root / "channels.json"
        main.VIDEOS = root / "videos"
        main.MANIFEST = root / "videos" / "manifest.json"
        main.CHANNELS.write_text(json.dumps(LINEUP))
        main.MANIFEST.write_text(json.dumps(FIXTURE))
        self.client = TestClient(main.app)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_channel_shape(self):
        response = self.client.get("/admin/api/now")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["channel"], 1)
        self.assertEqual(body["url_base"], "/videos/")
        for key in ("id", "title", "artist", "song", "offset", "remaining", "duration"):
            self.assertIn(key, body["now"])
        for key in ("id", "title", "artist", "song", "duration"):
            self.assertIn(key, body["next"])
        self.assertAlmostEqual(body["now"]["offset"] + body["now"]["remaining"],
                               body["now"]["duration"], places=3)
        self.assertNotEqual(body["now"]["id"], "ggg777")

    def test_next_follows_now_in_schedule(self):
        body = self.client.get("/admin/api/now?ch=2").json()
        order = [item["id"] for item in main.schedule.schedule_for(FIXTURE, 2)]
        index = order.index(body["now"]["id"])
        self.assertEqual(body["next"]["id"], order[(index + 1) % len(order)])

    def test_unknown_channel_404(self):
        self.assertEqual(self.client.get("/admin/api/now?ch=9").status_code, 404)

    def test_missing_manifest_404(self):
        main.MANIFEST.unlink()
        response = self.client.get("/admin/api/now")
        self.assertEqual(response.status_code, 404)
        self.assertIn("error", response.json())

    def test_missing_lineup_404(self):
        main.CHANNELS.unlink()
        self.assertEqual(self.client.get("/admin/api/now").status_code, 404)

    def test_empty_channel_404(self):
        manifest = dict(FIXTURE, channels={"1": []})
        main.MANIFEST.write_text(json.dumps(manifest))
        self.assertEqual(self.client.get("/admin/api/now?ch=1").status_code, 404)


if __name__ == "__main__":
    unittest.main()
