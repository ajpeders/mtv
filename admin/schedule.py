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


def _artist(it):
    """Return the artist string for an item, falling back to title parsing."""
    obj = it if isinstance(it, dict) else {}
    artist = obj.get("artist") or ""
    if artist:
        return artist
    match = _SPLIT.match(obj.get("title") or "")
    return match.group(1) if match else ""


def _separate_consecutive(lib):
    """Deterministic pass: rearrange to avoid consecutive same-artist pairs.

    Uses a greedy interleaving approach: always pick the artist with the most
    remaining items that isn't the previous artist, scanning in a fixed
    (count-desc, name-asc) order. Guarantees no consecutive same-artist pairs
    in the linear order when feasible (no artist has more than ceil(n/2) items).

    After building the linear order, checks the cyclic seam (last→first).
    If the seam repeats an artist, attempts a swap fix: tries swapping the
    last item with each interior item to find a valid cyclic arrangement.
    If no swap works, returns the linearly optimal result — the distribution
    may be cyclically impossible (e.g. AABBB with 5 items, B=3 > floor(5/2)=2).
    See regression tests for feasible/impossible examples.
    """
    from collections import defaultdict

    n = len(lib)
    if n < 3:
        return lib  # nothing to separate with < 3 items

    # Group items by artist (preserving insertion order within each group)
    groups = defaultdict(list)
    for item in lib:
        groups[_artist(item)].append(item)

    # Check linear feasibility: no artist can exceed ceil(n/2)
    max_count = max(len(g) for g in groups.values())
    if max_count > (n + 1) // 2:
        return lib  # impossible even linearly — return unchanged

    # Sort artists by count descending, ties by name ascending (deterministic)
    artists = sorted(groups.keys(), key=lambda a: (-len(groups[a]), a))

    result = []
    prev_artist = None

    while len(result) < n:
        chosen = None
        for artist in artists:
            if artist != prev_artist and groups[artist]:
                chosen = artist
                break
        if chosen is None:
            return lib  # shouldn't happen after feasibility check

        result.append(groups[chosen].pop(0))
        prev_artist = chosen

    # Check cyclic seam and try swap fix
    if _artist(result[0]) == _artist(result[-1]):
        fixed = _fix_cyclic_seam(result)
        if fixed is not None:
            return fixed

    return result


def _fix_cyclic_seam(result):
    """Try to fix a bad seam by swapping the last item with an interior item.

    Returns a new list with a clean cyclic seam, or None if no swap works.
    """
    n = len(result)
    last_artist = _artist(result[-1])

    for i in range(1, n - 1):
        if _artist(result[i]) == last_artist:
            continue
        # Swap result[i] and result[-1]
        new_result = list(result)
        new_result[i], new_result[-1] = new_result[-1], new_result[i]
        # Check all adjacencies
        clean = True
        for j in range(n - 1):
            if _artist(new_result[j]) == _artist(new_result[j + 1]):
                clean = False
                break
        if clean and _artist(new_result[0]) != _artist(new_result[-1]):
            return new_result

    return None


def schedule_for(manifest, num):
    """Deterministic play order for channel `num`: sort by id, seeded shuffle,
    then separate consecutive same-artist pairs."""
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
    return _separate_consecutive(items)


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
_RELEASE = {"single": "Single", "ep": "EP"}
_QUOTES = re.compile(r'^["“](.*)["”]$', re.S)


def credit(item):
    """Port of creditText: manifest artist/track fields take precedence."""
    raw = item if isinstance(item, str) else (item or {}).get("title") or ""
    clean = _SUFFIX.sub("", raw).strip()
    match = _SPLIT.match(clean)
    obj = item if isinstance(item, dict) else {}
    artist = obj.get("artist") or (match.group(1) if match else "")
    song = obj.get("track") or (match.group(2) if match else clean)
    album = obj.get("album") or ""
    return {
        "artist": artist,
        "song": _QUOTES.sub(r"\1", song),
        "album": album,
        "year": obj.get("year") or "",
        "featured": ", ".join(n for n in obj.get("featured") or [] if n.lower() not in artist.lower()),
        "genre": " · ".join((obj.get("genre") or [])[:2]),
        "release": "" if album else _RELEASE.get(obj.get("release_type") or "", ""),
    }
