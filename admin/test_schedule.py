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
%(artistKey)s
%(separateArtists)s
var EPOCH = 365472000, SEED = 1981;
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
  return separateArtists(items);
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
        cls.crowded = crowded_manifest()
        harness = HARNESS % {
            "mulberry32": js_function("mulberry32"),
            "creditText": js_function("creditText"),
            "artistKey": js_function("artistKey"),
            "separateArtists": js_function("separateArtists"),
        }
        payload = json.dumps({
            "manifest": cls.manifest,
            "channels": CHANNELS,
            "timestamps": TIMESTAMPS,
        })
        cls.js = run_js(harness, payload)
        cls.js_crowded = run_js(harness, json.dumps({
            "manifest": cls.crowded, "channels": [1], "timestamps": TIMESTAMPS}))

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

    def test_crowded_order_matches_js(self):
        library = schedule.schedule_for(self.crowded, 1)
        self.assertEqual([item["id"] for item in library], self.js_crowded["order"]["1"])


def run_js(harness, payload):
    result = subprocess.run(["node", "-e", harness], input=payload, text=True,
                            capture_output=True, check=True)
    return json.loads(result.stdout)


def crowded_manifest():
    """60 videos, 7 artists (one with 14 songs), case and dash variants, and
    videos with no artist: the shuffle alone puts artists back to back."""
    names = ["Drake", "DRAKE ", "Muse", "Blur", "Cher", "Abba", "Toto", "Kiss"]
    videos = []
    for i in range(60):
        name = names[i % len(names)] if i % 9 else names[0]
        title = f"{name} – Song {i}" if i % 5 else f"{name} - Song {i} (Official Video)"
        if i % 13 == 0:
            title = f"untitled clip {i}"
        videos.append({"id": f"v{i:02d}", "title": title, "duration": 100 + i})
    return {"videos": videos, "channels": {"1": [v["id"] for v in videos]}}


def make_lib(artists):
    return [{"id": f"{a}{i}", "title": f"{a} - Song {i}", "duration": 60}
            for i, a in enumerate(artists)]


def repeats(lib, cyclic=True):
    """Indexes i where lib[i] and the next song share an artist."""
    keys = [schedule._artist_key(it) for it in lib]
    pairs = len(keys) if cyclic and len(keys) > 2 else len(keys) - 1
    return [i for i in range(max(pairs, 0)) if keys[i] == keys[(i + 1) % len(keys)]]


class Separation(unittest.TestCase):
    def test_crowded_channel_has_no_repeats(self):
        lib = schedule.schedule_for(crowded_manifest(), 1)
        self.assertEqual(len(lib), 60)
        self.assertEqual(repeats(lib), [])

    def test_case_and_whitespace_count_as_the_same_artist(self):
        self.assertEqual(schedule._artist_key({"id": "a", "title": "DRAKE  - X"}),
                         schedule._artist_key({"id": "b", "title": "x", "artist": "drake"}))

    def test_unknown_artists_never_count_as_repeats(self):
        lib = [{"id": f"u{i}", "title": f"clip {i}", "duration": 60} for i in range(4)]
        self.assertEqual(schedule._separate_artists(list(lib)), lib)

    def test_no_repeats_keeps_the_shuffle(self):
        lib = make_lib("ABCDEFGH")
        self.assertEqual(schedule._separate_artists(list(lib)), lib)

    def test_moves_as_little_as_possible(self):
        ids = [it["id"] for it in schedule._separate_artists(make_lib("ABBCD"))]
        self.assertEqual(ids, ["A0", "B1", "C3", "B2", "D4"])

    def test_old_swap_pass_failure(self):
        # the first version left this back to back
        lib = schedule._separate_artists(make_lib("CABAA"))
        self.assertEqual(repeats(lib, cyclic=False), [])

    def test_seam(self):
        lib = schedule._separate_artists(make_lib("AABC"))
        self.assertEqual(repeats(lib), [])

    def test_impossible_keeps_every_song(self):
        lib = schedule._separate_artists(make_lib("AAAAB"))
        self.assertEqual(sorted(it["id"] for it in lib), ["A0", "A1", "A2", "A3", "B4"])

    def test_every_small_feasible_library(self):
        """Every order of every artist mix up to 7 songs: no repeats in a row
        when that's possible, and none across the seam when that is too."""
        import itertools
        for n in range(1, 8):
            for combo in itertools.product("ABCD", repeat=n):
                top = max(combo.count(a) for a in set(combo))
                lib = schedule._separate_artists(make_lib(combo))
                self.assertEqual(len(lib), n)
                if top <= (n + 1) // 2:
                    self.assertEqual(repeats(lib, cyclic=False), [], combo)
                if top <= n // 2:
                    self.assertEqual(repeats(lib), [], combo)


if __name__ == "__main__":
    unittest.main()
