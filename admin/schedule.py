"""Python port of the player's broadcast schedule (app/mtv.js `startLocal`).

Must stay bit-for-bit identical to the JavaScript: admin/test_schedule.py runs
both and compares. Change this file and mtv.js together, never one alone.
"""
import re

EPOCH = 365472000
SEED = 1981
_MASK = 0xFFFFFFFF


def _imul(a, b):
    return ((a & _MASK) * (b & _MASK)) & _MASK


def mulberry32(seed):
    """Same sequence as the JS mulberry32, with arithmetic on uint32 bits."""
    a = seed & _MASK

    def rnd():
        nonlocal a
        a = (a + 0x6D2B79F5) & _MASK
        t = a
        t = _imul(t ^ (t >> 15), t | 1)
        t = ((t + _imul(t ^ (t >> 7), t | 61)) ^ t) & _MASK
        return ((t ^ (t >> 14)) & _MASK) / 4294967296

    return rnd


def schedule_for(manifest, num):
    """Deterministic play order for channel `num`: sort by id, seeded shuffle."""
    vids = manifest.get("videos") if isinstance(manifest, dict) else manifest
    vids = vids or []
    chans = manifest.get("channels") if isinstance(manifest, dict) else None
    ids = chans.get(str(num)) if chans else None
    items = [it for it in vids
             if isinstance(it, dict) and (it.get("duration") or 0) > 0
             and (ids is None or it.get("id") in ids)]
    items.sort(key=lambda it: it["id"])
    rnd = mulberry32(SEED + num)
    for i in range(len(items) - 1, 0, -1):
        j = int(rnd() * (i + 1))
        items[i], items[j] = items[j], items[i]
    return items


def on_air(lib, now_sec):
    """Return the scheduled item and offset at `now_sec`, or None if empty."""
    total = sum(it["duration"] for it in lib)
    if not total:
        return None
    offset = (now_sec - EPOCH) % total
    for index, item in enumerate(lib):
        if offset < item["duration"]:
            return {"index": index, "item": item, "offset": offset}
        offset -= item["duration"]
    return {"index": 0, "item": lib[0], "offset": 0.0}


_SUFFIX = re.compile(
    r"\s*[\[(](?:(?:official\s+)?(?:music\s+|lyric\s+)?video|official\s+audio|visuali[sz]er|lyrics?)[\])]\s*$",
    re.I)
_SPLIT = re.compile(r"^(.+?)\s+[-–—]\s+(.+)$", re.S)
_QUOTES = re.compile(r'^["“](.*)["”]$', re.S)


def credit(item):
    """Port of creditText: manifest artist/track fields take precedence."""
    raw = item if isinstance(item, str) else (item or {}).get("title") or ""
    clean = _SUFFIX.sub("", raw).strip()
    match = _SPLIT.match(clean)
    obj = item if isinstance(item, dict) else {}
    artist = obj.get("artist") or (match.group(1) if match else "")
    song = obj.get("track") or (match.group(2) if match else clean)
    return {"artist": artist, "song": _QUOTES.sub(r"\1", song)}
