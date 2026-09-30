# HOWTO

## Add or change a channel

1. Open `https://mtv.thelunadog.com/admin` (LAN, WireGuard or Tailscale).
2. `+ CHANNEL`, set the number, name (≤24 chars) and the YouTube playlist id —
   the `PL…` value from the playlist URL's `list=` parameter.
3. `SAVE`, then `SYNC NOW`.

A channel may be saved with an empty playlist; it is skipped by the sync and
shows "Off air" until you fill it in. Viewers pick up lineup changes on their
next page load.

## Files mode (standalone)

Commands below assume `docker-compose.standalone.yml`; alias it:
`alias mtvc='docker compose -f docker-compose.standalone.yml'`.

### Add videos

1. Copy `.mp4` files (lower-case extension) into `data/videos/`. H.264 + AAC
   plays in every browser; HEVC only in Safari/some Chrome.
2. Name them `Artist - Song.mp4` for credits, or put a `<name>.info.json`
   beside the file with `title` (and optionally `artist`, `track`, `album`).
3. `SYNC NOW` on `/admin` (or `mtvc exec mtv-sync touch /config/.sync-now`).
   Otherwise the folder is rescanned every `MTV_SYNC_INTERVAL` (6h).

A file's id is its path under `data/videos` minus `.mp4` — renaming or
editing one reshuffles the channels it's on. Replace a file under a *new*
name: browsers cache `/videos/` for a year.

### Channels from folders

Channel 1 (and any other admin-lineup channel) airs every file. Each
top-level subfolder is also a channel, named after the folder and numbered
after the lineup (`CH 02 80s`, ...). Files in deeper folders belong to their
top-level folder's channel. A folder keeps its number across rescans.

### Download YouTube playlists into the folder

```sh
mtvc run --rm mtv-sync fetch PLxxxxxxxx                    # into data/videos
mtvc run --rm mtv-sync fetch --into 80s PLyyyy PLzzzz      # into data/videos/80s = its own channel
mtvc run --rm mtv-sync fetch 'https://www.youtube.com/watch?v=...'
```

Each video lands as `<youtube-id>.mp4` (≤1080p H.264+AAC, same format choice
as the mirror) plus `<youtube-id>.info.json` for the title. Nothing is ever
deleted; a per-folder `.archive` remembers what was fetched, so a file you
delete stays deleted (remove its line from `.archive` to fetch it again).

To re-run on every sync pass, list playlists in `data/config/playlists.txt`,
one per line, optional folder after it:

```
# playlist-or-url   [folder]
PLxxxxxxxx
PLyyyyyyyy          80s
```

`mtvc run --rm mtv-sync fetch` with no arguments fetches the file once.

## Add a channel from a mediaDb playlist

Nothing to do in mtv. Every mediaDb playlist (except `source` imports) becomes
a channel on the next manifest publish. `SYNC NOW` runs one, but see the
ROADMAP note on long passes. Its number and video count appear under
"MEDIADB PLAYLISTS" on the admin page. To remove the channel, delete the
playlist in mediaDb.

## Force a sync without the admin page

```sh
docker exec mtv-sync touch /config/.sync-now   # starts a pass within ~15s
docker compose logs -f mtv-sync
```

The sync log reports how many music videos received metadata from mediaDb. A
`mediadb unavailable` message is non-fatal; MTV still publishes playlist titles.
Set `MTV_MEDIADB_URL=` to disable lookup or point it at another internal URL.

## Re-encode the existing library to HEVC

Run once after deploying a sync.sh change that adds the HEVC transcode step
(see ARCHITECTURE.md → *Codec*). The script is idempotent: already-HEVC files
are probed and skipped, so it is safe to interrupt and rerun.

```sh
docker exec mtv-sync /sync.sh transcode-library    # one-pass, ~h/lib
docker compose logs -f mtv-sync                    # watch progress
```

The wall-clock cost depends on the library size — `libx265` software encode
on the mtv-sync container runs roughly at half-realtime per core. If the
container has 4 cores, a 100-video library averages a few minutes per video
but they run sequentially. Let it run overnight on a large library.

The original H.264 byte stream is overwritten only on a clean ffmpeg exit; a
crash mid-transcode (`docker restart mtv-sync`, host OOM, etc.) leaves the
source intact and the next invocation resumes from where it left off.

## Check what is on air

The schedule endpoint is available through the LAN/VPN-only admin route:

```sh
curl -s 'https://mtv.thelunadog.com/admin/api/now?ch=1' | python3 -m json.tool
```

It reports the current item and offset, the next item, mediaDb-enriched credits
(including album and year when available), and the public video URL base. A 404
means the channel is unknown, empty, or not synced yet.

## See who is watching

Homelab only: the panel reads Traefik's access log. The standalone deploy has
none, so the panel says "no viewer log" — that's expected.

The admin page has a VIEWERS table (last ~24h of page loads, since the Traefik
log rotates daily). From the host:

```sh
bin/mtv-viewers        # grouped by IP, with location
bin/mtv-viewers -f     # live, one line per join
```

Single hits from hosting providers (Censys, Shodan, Fastly probes) are internet
background noise on a public route, not viewers.

## Geolocation says "geo lookup failed"

AdGuard blocks most IP-geolocation APIs. Allow the one this uses:

1. AdGuard → Filters → Custom filtering rules → add `@@||ip-api.com^`
2. `docker restart adguard` (brief LAN-wide DNS blip)

Verify: `docker exec mtv-admin python3 -c "import socket;
print(socket.getaddrinfo('ip-api.com',80)[0][4][0])"` — anything other than
`0.0.0.0` is working.

## Change the player's look

Everything visual is in `app/index.html` (markup and the inline CRT styles)
and `app/mtv.js` (player logic). The admin page is a **separate container** built
from `admin/` — reverting the player does not touch it. Do not restore an older
`docker-compose.yml` over the current one to revert a look: pre-`mtv-admin`
versions have no admin service and will delete it.

## Rebuild after changing the admin app

```sh
cd services && docker compose up -d --build mtv-admin
```

From the homelab `services/` directory, not `apps/mtv/` — the containers belong
to the `services` compose project, and running compose from `apps/mtv` fails
with a container-name conflict.

## Deploy

Push to `main` on `alex/mtv`. CI must pass, then `bin/deploy` (every 3 min)
fast-forwards and recreates the containers, health-checks through the real
ingress, and rolls back on failure.

```sh
tail -f state/deploy/deploy.log   # decisions
cat state/deploy/mtv.log          # build/deploy output
```

It defers while anyone is watching (any `/videos/` fetch in the last 5 min), so
a deploy can sit pending during a listening session — that is intended.

If a deploy fails health, its commit is recorded in `state/deploy/mtv.failed-sha`
and **not retried until a new push**, and a red `deploy/homelab` commit status
makes the combined status fail, which blocks the CI gate as well. The clean fix
is a new commit; clearing the marker alone is not enough.

## Nothing plays / "NO MANIFEST YET"

In order:

```sh
docker compose logs --tail 50 mtv-sync     # fetch failures? disk full?
ls /mnt/storage/mtv-videos/*.mp4 | wc -l   # anything mirrored?
cat /mnt/storage/mtv-videos/manifest.json | head -c 200
```

A video needs a non-zero `ffprobe` duration to be scheduled; zero-duration
files are dropped from the manifest on purpose.
