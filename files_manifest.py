"""Files mode (MTV_SOURCE=files): build manifest.json from the mp4s on disk.

Run by sync.sh each pass instead of the YouTube mirror:

    python3 files_manifest.py <videos-dir> <channels.json>

- every `*.mp4` under the videos dir airs; its id is the path relative to the
  videos dir minus `.mp4` (`Artist - Song`, `80s/Artist - Song`), so the
  player's `/videos/<id>.mp4` contract still holds — clients percent-encode
  the id, nginx decodes it back to the file path
- every channels.json channel (channel 1 by default) airs all files
- each top-level subfolder is also its own channel, named after the folder
  and numbered after the channels.json lineup (sticky across passes, like the
  mediaDb playlist channels)
- title/artist/song come from `<name>.info.json` beside the file (yt-dlp's
  --write-info-json, or hand-written) if present, else the file name — the
  player parses "Artist - Song" out of it
- durations come from ffprobe, cached in `.durations.files.json` by size+mtime
Dot-files, dot-dirs and in-progress downloads/transcodes are ignored.
"""
import json
import os
import re
import subprocess
import sys

# stems of half-written files: yt-dlp format parts/merges, sync.sh transcodes
PARTIAL = re.compile(r"\.(?:f\d+|temp|part|transcoding)$")


def scan(root):
    """Sorted (id, path) for every airable mp4 under root."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            stem = name[:-4]
            if not name.endswith(".mp4") or name.startswith(".") or PARTIAL.search(stem):
                continue
            path = os.path.join(dirpath, name)
            found.append((os.path.relpath(path, root)[:-4].replace(os.sep, "/"), path))
    return sorted(found)


def describe(vid, path):
    """Manifest fields other than duration, from the sidecar or the name."""
    item = {"id": vid, "title": os.path.basename(vid)}
    try:
        with open(path[:-4] + ".info.json", encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        return item
    if not isinstance(info, dict):
        return item
    for key in ("title", "artist", "track", "album"):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            item[key] = value.strip()
    return item


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout.strip()
    try:
        return round(float(out), 2)
    except ValueError:
        return 0


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def build(root, channels_path, probe=probe):
    cachep = os.path.join(root, ".durations.files.json")
    cache = read_json(cachep, {})
    fresh = {}
    videos = []
    for vid, path in scan(root):
        st = os.stat(path)
        key = f"{vid}|{st.st_size}|{int(st.st_mtime)}"
        dur = cache.get(key)
        if dur is None:
            dur = probe(path)
        fresh[key] = dur
        if dur > 0:
            videos.append(dict(describe(vid, path), duration=dur))
    tmp = cachep + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(fresh, f)
    os.replace(tmp, cachep)

    ids = [v["id"] for v in videos]
    lineup = read_json(channels_path, None)
    nums = [c["num"] for c in lineup if isinstance(c, dict) and isinstance(c.get("num"), int)] \
        if isinstance(lineup, list) else []
    channels = {str(num): ids for num in nums or [1]}

    # one channel per top-level folder; a folder keeps its number from the
    # previous manifest so a viewer's remembered channel doesn't drift
    previous = read_json(os.path.join(root, "manifest.json"), {})
    previous = previous if isinstance(previous, dict) else {}
    sticky = {c["slug"]: c["num"] for c in previous.get("lineup") or [] if isinstance(c, dict)}
    taken = set(nums or [1])
    folders = {}
    for vid in ids:
        if "/" in vid:
            folders.setdefault(vid.split("/", 1)[0], []).append(vid)
    folder_lineup = []
    for folder in sorted(folders):
        num = sticky.get(folder)
        if num is None or num in taken:
            num = max(taken | set(sticky.values())) + 1
        taken.add(num)
        folder_lineup.append({"num": num, "name": folder, "slug": folder})
        channels[str(num)] = folders[folder]
    folder_lineup.sort(key=lambda c: c["num"])
    return {"videos": videos, "channels": channels, "lineup": folder_lineup}


def main(root, channels_path):
    manifest = build(root, channels_path)
    tmp = os.path.join(root, ".manifest.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f)
    os.replace(tmp, os.path.join(root, "manifest.json"))
    print(f"[sync] files manifest: {len(manifest['videos'])} videos,"
          f" {len(manifest['lineup'])} folder channels")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
