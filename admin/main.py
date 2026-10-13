# MTV admin — edits the channel lineup and pokes the sync container.
# No auth here: keep it on your LAN or behind your own authenticating proxy
# (see NOTES.md).
# Runtime contract (container mounts, hardcoded):
#   /config/channels.json  rw  — the lineup this app edits
#   /config/.sync-now      rw  — touch to trigger a sync pass (~15s)
#   /videos/               ro  — manifest.json + <id>.mp4 library

import json
import os
import re
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse

import schedule

CONFIG_DIR = Path("/config")
CHANNELS = CONFIG_DIR / "channels.json"
SYNC_FLAG = CONFIG_DIR / ".sync-now"
VIDEOS = Path("/videos")
MANIFEST = VIDEOS / "manifest.json"
UI = Path(__file__).parent / "index.html"

PLAYLIST_RE = re.compile(r"^[A-Za-z0-9_-]*$")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def validate_lineup(data):
    """Return the normalized lineup or raise ValueError with a human message."""
    if not isinstance(data, list):
        raise ValueError("lineup must be a JSON array of channel objects")
    seen = set()
    out = []
    for i, ch in enumerate(data):
        where = f"channel[{i}]"
        if not isinstance(ch, dict):
            raise ValueError(f"{where}: must be an object")
        extra = set(ch) - {"num", "name", "playlist"}
        if extra:
            raise ValueError(f"{where}: unknown keys {sorted(extra)}")
        num = ch.get("num")
        # bool is an int subclass — reject it explicitly
        if isinstance(num, bool) or not isinstance(num, int) or num < 1:
            raise ValueError(f"{where}: num must be a positive integer")
        if num in seen:
            raise ValueError(f"{where}: duplicate num {num}")
        seen.add(num)
        name = ch.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 24:
            raise ValueError(f"{where}: name must be a non-empty string of at most 24 chars")
        playlist = ch.get("playlist")
        if not isinstance(playlist, str) or not PLAYLIST_RE.match(playlist):
            raise ValueError(
                f"{where}: playlist must contain only letters, digits, _ and - (empty allowed)")
        out.append({"num": num, "name": name, "playlist": playlist})
    return out


def read_json(path):
    """Parsed JSON or None if the file is absent/unreadable/corrupt."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def playlist_channels(manifest, lineup):
    """The manifest's mediaDb playlist channels, minus any number channels.json
    already uses (it wins a clash, as in the player)."""
    extra = manifest.get("lineup") if isinstance(manifest, dict) else None
    used = {c.get("num") for c in lineup if isinstance(c, dict)}
    return [c for c in extra or [] if isinstance(c, dict) and c.get("num") not in used]


@app.get("/admin")
def ui():
    return FileResponse(UI, media_type="text/html")


@app.get("/admin/api/lineup")
def get_lineup():
    if not CHANNELS.exists():
        return JSONResponse(
            {"detail": "no channels.json yet — save a lineup to create it"}, status_code=404)
    data = read_json(CHANNELS)
    if data is None:
        return JSONResponse({"detail": "channels.json is unreadable or not valid JSON"},
                            status_code=404)
    return data


@app.get("/admin/api/now")
def now_playing(ch: int = 1, start: str | None = Query(None, alias="from")):
    """Return what channel `ch` is airing at this instant. With `from=<id>`,
    that video from the top instead, ignoring the clock: a skip "detour"
    (the player's #remote skip) walks the schedule order this way."""
    def unavailable(message):
        return JSONResponse({"error": message}, status_code=404)

    if not CHANNELS.exists():
        return unavailable("no channels.json yet")
    lineup = read_json(CHANNELS)
    if not isinstance(lineup, list):
        return unavailable("channels.json is unreadable")
    manifest = read_json(MANIFEST)
    channels = lineup + playlist_channels(manifest, lineup)
    if not any(isinstance(channel, dict) and channel.get("num") == ch for channel in channels):
        return unavailable(f"no channel {ch} in the lineup")
    if manifest is None:
        return unavailable("no manifest yet - nothing synced")
    library = schedule.schedule_for(manifest, ch)
    if start is not None:
        index = next((i for i, item in enumerate(library) if item["id"] == start), None)
        if index is None:
            return unavailable(f"no video {start} on channel {ch}")
        slot = {"index": index, "item": library[index], "offset": 0.0}
    else:
        slot = schedule.on_air(library, time.time())
    if slot is None:
        return unavailable(f"channel {ch} has no playable videos")
    current = slot["item"]
    following = library[(slot["index"] + 1) % len(library)]

    def describe(item):
        credits = schedule.credit(item)
        return {
            "id": item["id"],
            "title": item.get("title", ""),
            "artist": credits["artist"],
            "song": credits["song"],
            "album": credits["album"],
            "year": credits["year"],
            "featured": credits["featured"],
            "genre": credits["genre"],
            "release": credits["release"],
            "duration": float(item["duration"]),
        }

    body = describe(current)
    body["offset"] = float(slot["offset"])
    body["remaining"] = float(current["duration"]) - body["offset"]
    return {"channel": ch, "now": body, "next": describe(following),
            "url_base": "/videos/"}


@app.put("/admin/api/lineup")
async def put_lineup(request: Request):
    try:
        data = await request.json()
    except ValueError:
        return JSONResponse({"detail": "body is not valid JSON"}, status_code=422)
    try:
        lineup = validate_lineup(data)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    # atomic: tmp in the same dir, then rename over the original
    fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".channels.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(lineup, f, indent=2)
            f.write("\n")
            # mkstemp defaults to 0600; nginx in the separate player
            # container must be able to read this public lineup after save.
            os.fchmod(f.fileno(), 0o644)
        os.replace(tmp, CHANNELS)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return {"ok": True, "channels": len(lineup)}


@app.get("/admin/api/status")
def status():
    lineup = read_json(CHANNELS)
    lineup = lineup if isinstance(lineup, list) else []
    manifest = read_json(MANIFEST) or {}
    vids = manifest.get("videos") or []
    chans = manifest.get("channels") or {}
    dur = {v.get("id"): v.get("duration") or 0 for v in vids if isinstance(v, dict)}

    channels = []
    for ch in lineup + playlist_channels(manifest, lineup):
        if not isinstance(ch, dict):
            continue
        ids = chans.get(str(ch.get("num"))) or []
        channels.append({
            "num": ch.get("num"),
            "name": ch.get("name"),
            "playlist": ch.get("playlist"),
            "slug": ch.get("slug"),
            "local_count": len(ids),
            "total_duration_s": sum(dur.get(i, 0) for i in ids),
        })

    disk_bytes = 0
    video_count = 0
    try:
        # files mode keeps videos in subfolders too
        for p in VIDEOS.rglob("*.mp4"):
            if p.is_file():
                try:
                    disk_bytes += p.stat().st_size
                    video_count += 1
                except OSError:
                    pass
    except OSError:
        pass

    manifest_age_s = None
    try:
        manifest_age_s = max(0, int(time.time() - MANIFEST.stat().st_mtime))
    except OSError:
        pass

    return {
        "channels": channels,
        "library": {
            "video_count": video_count,
            "disk_bytes": disk_bytes,
            "manifest_age_s": manifest_age_s,
        },
        "sync_pending": SYNC_FLAG.exists(),
    }


@app.post("/admin/api/sync")
def sync_now():
    SYNC_FLAG.touch()
    return {"ok": True, "pending": True}

