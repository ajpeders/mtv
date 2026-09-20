# MTV admin — edits the channel lineup and pokes the sync container.
# No auth here: Traefik's local-only middleware is the gate (see NOTES.md).
# Runtime contract (container mounts, hardcoded):
#   /config/channels.json  rw  — the lineup this app edits
#   /config/.sync-now      rw  — touch to trigger a sync pass (~15s)
#   /config/geoip.json     rw  — IP→location cache, shared with bin/mtv-viewers
#   /videos/               ro  — manifest.json + <id>.mp4 library
#   /traefik-logs/         ro  — Traefik JSON access log (viewer IPs)

import ipaddress
import json
import os
import re
import tempfile
import time
import urllib.request
from pathlib import Path

from fastapi import FastAPI, Request
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
def now_playing(ch: int = 1):
    """Return what channel `ch` is airing at this instant."""
    def unavailable(message):
        return JSONResponse({"error": message}, status_code=404)

    if not CHANNELS.exists():
        return unavailable("no channels.json yet")
    lineup = read_json(CHANNELS)
    if not isinstance(lineup, list):
        return unavailable("channels.json is unreadable")
    if not any(isinstance(channel, dict) and channel.get("num") == ch for channel in lineup):
        return unavailable(f"no channel {ch} in the lineup")
    manifest = read_json(MANIFEST)
    if manifest is None:
        return unavailable("no manifest yet - nothing synced")
    library = schedule.schedule_for(manifest, ch)
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
    lineup = read_json(CHANNELS) or []
    manifest = read_json(MANIFEST) or {}
    vids = manifest.get("videos") or []
    chans = manifest.get("channels") or {}
    dur = {v.get("id"): v.get("duration") or 0 for v in vids if isinstance(v, dict)}

    channels = []
    for ch in lineup:
        if not isinstance(ch, dict):
            continue
        ids = chans.get(str(ch.get("num"))) or []
        channels.append({
            "num": ch.get("num"),
            "name": ch.get("name"),
            "playlist": ch.get("playlist"),
            "local_count": len(ids),
            "total_duration_s": sum(dur.get(i, 0) for i in ids),
        })

    disk_bytes = 0
    video_count = 0
    try:
        for p in VIDEOS.iterdir():
            if p.suffix == ".mp4":
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


# ---- viewers: who loaded the player page, from where ----
# Same log parsing as bin/mtv-viewers. The access log rotates daily (03:30
# UTC), so this covers roughly the last day — the UI says so.

ACCESS_LOG = Path("/traefik-logs/access.log")
GEO_CACHE = CONFIG_DIR / "geoip.json"


def private_label(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "?"
    if addr in ipaddress.ip_network("100.64.0.0/10"):
        return "tailscale (you)"
    if addr in ipaddress.ip_network("10.8.0.0/24"):
        return "wireguard (you)"
    if addr in ipaddress.ip_network("192.168.0.0/16"):
        return "LAN"
    if addr in ipaddress.ip_network("172.16.0.0/12") or addr.is_loopback:
        return "host/docker"
    return None  # public → geo lookup


def geolocate(ips, cache):
    """Resolve up to one ip-api.com batch (100 IPs) of cache misses."""
    todo = [ip for ip in ips if ip not in cache][:100]
    if not todo:
        return
    body = json.dumps(
        [{"query": ip, "fields": "query,country,city,isp"} for ip in todo]
    ).encode()
    req = urllib.request.Request(
        "http://ip-api.com/batch", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            resp = json.load(r)
    except (OSError, ValueError):
        return
    if not isinstance(resp, list):
        return
    for d in resp:
        if not isinstance(d, dict) or "query" not in d:
            continue
        cache[d["query"]] = ", ".join(
            x for x in (d.get("city"), d.get("country"), d.get("isp")) if x
        ) or "?"


@app.get("/admin/api/viewers")
def viewers():
    if not ACCESS_LOG.exists():
        return JSONResponse(
            {"detail": "no access log mounted — is /traefik-logs wired up?"},
            status_code=404)
    rows = {}
    try:
        with open(ACCESS_LOG, errors="replace") as f:
            for line in f:
                # cheap prefilter before json.loads — the log has every
                # request to every service, mtv page loads are a sliver
                if '"RequestPath":"/"' not in line or "mtv" not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if "mtv" not in (d.get("RouterName") or ""):
                    continue
                if d.get("RequestPath") != "/":
                    continue
                ip = d.get("ClientHost")
                status = d.get("DownstreamStatus")
                when = (d.get("time") or "")[:19]
                r = rows.setdefault((ip, status), [0, when, when])
                r[0] += 1
                r[2] = when
    except OSError:
        return JSONResponse({"detail": "access log unreadable"}, status_code=500)

    cache = read_json(GEO_CACHE) or {}
    before = len(cache)
    geolocate([ip for ip, _ in rows if ip and not private_label(ip)], cache)
    if len(cache) != before:
        # same atomic-rename pattern as the lineup; bin/mtv-viewers shares
        # this file, so never leave it half-written
        fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".geoip.")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(cache, f)
                os.fchmod(f.fileno(), 0o644)
            os.replace(tmp, GEO_CACHE)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    out = []
    for (ip, status), (n, first, last) in rows.items():
        where = private_label(ip or "") or cache.get(ip, "geo lookup failed")
        out.append({
            "ip": ip,
            "where": where,
            "watched": status == 200,
            "status": status,
            "hits": n,
            "first": first,
            "last": last,
        })
    out.sort(key=lambda v: v["last"], reverse=True)
    return {"viewers": out}
