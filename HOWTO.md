# HOWTO

## Add or change a channel

1. Open `http://<host>:8080/admin` (LAN only; there's no auth).
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
a channel on the next manifest publish. `SYNC NOW` runs one. Its number and
video count appear under "MEDIADB PLAYLISTS" on the admin page. To remove the
channel, delete the playlist in mediaDb.

## Force a sync without the admin page

```sh
docker exec mtv-sync touch /config/.sync-now   # starts a pass within ~15s
docker compose logs -f mtv-sync
```

The sync log reports how many music videos received metadata from mediaDb. A
`mediadb unavailable` message is non-fatal; MTV still publishes playlist titles.
Set `MTV_MEDIADB_URL=` to disable lookup or point it at another internal URL.

mediaDb playlists become channels within a minute of a change: `mtv-sync`
polls `/api/playlists` every `MTV_PLAYLIST_POLL` seconds (60) and republishes
the manifest when the list changes (`mediadb playlists changed` in the log).
Reload the TV page to see a new or removed channel.

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

The admin app exposes the schedule as JSON:

```sh
curl -s 'http://<host>:8080/admin/api/now?ch=1' | python3 -m json.tool
```

It reports the current item and offset, the next item, mediaDb-enriched credits
(including album and year when available), and the public video URL base. A 404
means the channel is unknown, empty, or not synced yet.

## Use the TV controls

Tap (or click) the picture to bring up the remote: a column of icons flush to
the top-right corner that hides again after four seconds.

| Icon | Does | Key |
|---|---|---|
| ▲ ▼ | channel up / down (multi-channel only) | `↑` `↓` |
| ⏭ | skip (only on `/#remote`) | `→` |
| speaker | mute / unmute | `M` |
| speaker − / + | volume in 10% steps (not on iOS) | `-` `+` |
| corners | fullscreen | `F` |
| TV | CRT picture on / off | `C` |
| headphones | play in background, lit when on | `B` |
| PiP / AirPlay | shown only where the browser supports them | |

While the remote is up, tap the channel number (top left, `CH 01 ▾`) for the
channel list; arrow keys walk it, Enter tunes, Esc closes. Volume, CRT and
background play are remembered per device.

Background play is off by default: hiding the tab pauses and coming back
rejoins live. With it on the video keeps going in a hidden tab (desktop,
Android). iPhone Safari still pauses page video when locked or switched away;
use PiP or AirPlay there.

Lock screens and media controls show `[mtv] Artist – Song`. Play/pause there
work too (play rejoins live), and next track skips on `/#remote`.

## Change the player's look

Everything visual is in `app/index.html` (markup and the inline CRT styles)
and `app/mtv.js` (player logic). The admin page is a **separate container** built
from `admin/` — reverting the player does not touch it. Do not restore an older
compose file over the current one to revert a look: pre-`mtv-admin` versions
have no admin service and will delete it.

## Rebuild after changing the admin app

```sh
docker compose -f docker-compose.standalone.yml up -d --build mtv-admin
```

## Nothing plays / "NO MANIFEST YET"

In order:

```sh
docker compose -f docker-compose.standalone.yml logs --tail 50 mtv-sync
ls data/videos/*.mp4 | wc -l               # anything mirrored?
cat data/videos/manifest.json | head -c 200
```

A video needs a non-zero `ffprobe` duration to be scheduled; zero-duration
files are dropped from the manifest on purpose.
