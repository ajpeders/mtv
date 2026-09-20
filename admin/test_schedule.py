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
  return items;
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

    def test_zero_duration_and_unknown_channel(self):
        ids = [item["id"] for item in schedule.schedule_for(self.manifest, 1)]
        self.assertNotIn("ggg777", ids)
        self.assertEqual(len(schedule.schedule_for(self.manifest, 4)), 8)
        self.assertIsNone(schedule.on_air([], 0))


if __name__ == "__main__":
    unittest.main()
