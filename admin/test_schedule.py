"""Parity test: admin/schedule.py must match app/mtv.js exactly."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MTV_JS = HERE.parent / "app" / "mtv.js"
FIXTURE = HERE / "fixtures" / "manifest.json"
sys.path.insert(0, str(HERE))
import schedule  # noqa: E402

TIMESTAMPS = [1_000_000_000.0, 1_758_300_000.25, 2_000_000_000.5]
CHANNELS = [1, 2, 3, 4]


def js_function(name):
    """Slice `function name(...) {...}` out of mtv.js by brace matching."""
    source = MTV_JS.read_text()
    start = source.index(f"function {name}(")
    depth, index = 0, source.index("{", start)
    while True:
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
        index += 1


HARNESS = r"""
%(mulberry32)s
%(creditText)s
var EPOCH = 365472000, SEED = 1981;
function itemArtist(it) {
  var artist = (it && it.artist) || "";
  if (artist) return artist;
  var title = it && it.title || "";
  var split = title.match(/^(.+?)\s+[-–—]\s+(.+)$/);
  return split ? split[1] : "";
}
function separateConsecutive(items) {
  var n = items.length;
  if (n < 3) return items;
  for (var i = 0; i < n - 1; i++) {
    if (itemArtist(items[i]) !== itemArtist(items[i + 1])) continue;
    var best = null;
    for (var k = i + 2; k < n; k++) {
      if (itemArtist(items[k]) !== itemArtist(items[i])) { best = k; break; }
    }
    if (best !== null) {
      var tmp = items[i + 1]; items[i + 1] = items[best]; items[best] = tmp;
      continue;
    }
    for (var k = i - 1; k >= 0; k--) {
      if (itemArtist(items[k]) !== itemArtist(items[i])) {
        var tmp2 = items[i]; items[i] = items[k]; items[k] = tmp2;
        break;
      }
    }
  }
  return items;
}
function scheduleFor(manifest, num) {
  var vids = manifest.videos || manifest;
  var chans = manifest.channels || null;
  var ids = chans && chans[String(num)];
  var items = vids.filter(function (it) {
    return it.duration > 0 && (!ids || ids.indexOf(it.id) !== -1);
  });
  items.sort(function (a, b) { return a.id < b.id ? -1 : 1; });
  var rnd = mulberry32(SEED + num);
  for (var i = items.length - 1; i > 0; i--) {
    var j = Math.floor(rnd() * (i + 1));
    var t = items[i]; items[i] = items[j]; items[j] = t;
  }
  return separateConsecutive(items);
}
function onAir(lib, nowSec) {
  var total = lib.reduce(function (s, it) { return s + it.duration; }, 0);
  if (!total) return null;
  var off = (nowSec - EPOCH) %% total;
  for (var i = 0; i < lib.length; i++) {
    if (off < lib[i].duration) return { id: lib[i].id, off: off };
    off -= lib[i].duration;
  }
  return { id: lib[0].id, off: 0 };
}
var input = JSON.parse(require("fs").readFileSync(0, "utf8"));
var out = { order: {}, onair: {}, credits: {} };
input.channels.forEach(function (num) {
  var lib = scheduleFor(input.manifest, num);
  out.order[num] = lib.map(function (it) { return it.id; });
  out.onair[num] = input.timestamps.map(function (t) { return onAir(lib, t); });
});
input.manifest.videos.forEach(function (it) { out.credits[it.id] = creditText(it); });
process.stdout.write(JSON.stringify(out));
"""


class Parity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(FIXTURE.read_text())
        harness = HARNESS % {
            "mulberry32": js_function("mulberry32"),
            "creditText": js_function("creditText"),
        }
        payload = json.dumps({
            "manifest": cls.manifest,
            "channels": CHANNELS,
            "timestamps": TIMESTAMPS,
        })
        result = subprocess.run(["node", "-e", harness], input=payload, text=True,
                                capture_output=True, check=True)
        cls.js = json.loads(result.stdout)

    def test_order_matches_js(self):
        for num in CHANNELS:
            library = schedule.schedule_for(self.manifest, num)
            self.assertEqual([item["id"] for item in library],
                             self.js["order"][str(num)], f"ch {num}")

    def test_on_air_matches_js(self):
        for num in CHANNELS:
            library = schedule.schedule_for(self.manifest, num)
            for timestamp, expected in zip(TIMESTAMPS, self.js["onair"][str(num)]):
                slot = schedule.on_air(library, timestamp)
                if expected is None:
                    self.assertIsNone(slot)
                    continue
                self.assertEqual(slot["item"]["id"], expected["id"],
                                 f"ch {num} @ {timestamp}")
                self.assertAlmostEqual(slot["offset"], expected["off"], places=6)

    def test_credits_match_js(self):
        for item in self.manifest["videos"]:
            self.assertEqual(schedule.credit(item), self.js["credits"][item["id"]],
                             item["id"])
        self.assertEqual(self.js["credits"]["fff666"], {
            "artist": "Dominic Fike", "song": "Wallflower", "album": "Sunburn", "year": 2023,
            "featured": "Kacy Hill, Someone Else", "genre": "indie pop · alternative rock",
            "release": "",
        })
        # No album: say what it is instead, so the detail line isn't just a year.
        self.assertEqual(self.js["credits"]["eee555"]["release"], "Single")
        self.assertEqual(self.js["credits"]["aaa111"]["featured"], "")
        # Already credited as an artist: don't repeat them after the song.
        self.assertEqual(self.js["credits"]["ddd444"]["featured"], "Conway")

    def test_zero_duration_and_unknown_channel(self):
        ids = [item["id"] for item in schedule.schedule_for(self.manifest, 1)]
        self.assertNotIn("ggg777", ids)
        self.assertEqual(len(schedule.schedule_for(self.manifest, 4)), 8)
        self.assertIsNone(schedule.on_air([], 0))

    def test_no_consecutive_same_artist(self):
        """After separation, no two neighbours should share an artist."""
        for num in CHANNELS:
            lib = schedule.schedule_for(self.manifest, num)
            if len(lib) < 2:
                continue
            for i in range(len(lib) - 1):
                a = schedule._artist(lib[i])
                b = schedule._artist(lib[i + 1])
                self.assertNotEqual(a, b,
                                    f"ch {num}: consecutive '{a}' at pos {i},{i+1}")

    def test_separation_small_lib(self):
        """Libraries with < 3 items should pass through unchanged."""
        small = {"videos": [
            {"id": "a", "title": "A - Song A", "duration": 60},
            {"id": "b", "title": "B - Song B", "duration": 60},
        ], "channels": {"1": ["a", "b"]}}
        lib = schedule.schedule_for(small, 1)
        self.assertEqual(len(lib), 2)

    def test_separation_all_same_artist(self):
        """If every item shares an artist, the list is unchanged."""
        same = {"videos": [
            {"id": "x1", "title": "Drake - Song 1", "duration": 60, "artist": "Drake"},
            {"id": "x2", "title": "Drake - Song 2", "duration": 60, "artist": "Drake"},
            {"id": "x3", "title": "Drake - Song 3", "duration": 60, "artist": "Drake"},
        ], "channels": {"1": ["x1", "x2", "x3"]}}
        lib = schedule.schedule_for(same, 1)
        self.assertEqual(len(lib), 3)  # still 3, nothing removed

    def test_determinism_across_runs(self):
        """schedule_for must be stable: same input → same output."""
        lib1 = schedule.schedule_for(self.manifest, 2)
        lib2 = schedule.schedule_for(self.manifest, 2)
        self.assertEqual([it["id"] for it in lib1],
                         [it["id"] for it in lib2])

    def test_library_addition_includes_all_items(self):
        """Adding a video must keep every existing item in the schedule."""
        ch1_ids = self.manifest["channels"]["1"]
        base_lib = schedule.schedule_for(self.manifest, 1)
        base_ids = set(it["id"] for it in base_lib)
        # Add a new video to channel 1
        extra = {"id": "zzz999", "title": "New Artist - New Song", "duration": 120,
                 "artist": "New Artist"}
        new_videos = self.manifest["videos"] + [extra]
        new_channels = dict(self.manifest["channels"])
        new_channels["1"] = ch1_ids + ["zzz999"]
        new_manifest = {"videos": new_videos, "channels": new_channels}
        new_lib = schedule.schedule_for(new_manifest, 1)
        new_ids = set(it["id"] for it in new_lib)
        # All base items plus the new one must be present
        self.assertIn("zzz999", new_ids)
        self.assertEqual(base_ids, new_ids - {"zzz999"})


if __name__ == "__main__":
    unittest.main()
