# Architecture

Three containers run by `docker-compose.standalone.yml`: nginx serves the
player and proxies `/admin` to `mtv-admin` (`nginx.admin.conf`, pulled in by
the `include` in `nginx.conf`), and `mtv-sync` mirrors playlists and builds
the manifest. nginx publishes a host port and state and videos sit in
`./data/`; mediaDb enrichment is off unless `MTV_MEDIADB_URL` is set. There is
no database and no application state beyond two files: `channels.json` (the
lineup) and `manifest.json` (what is on disk).

```
YouTube ──► mtv-sync ──► /videos (mp4 + manifest.json) ──► mtv (nginx) ──► viewer
                 ▲  ▲                                           ▲
         mediaDb ┘  │ .sync-now                   channels.json │
                    └──────── mtv-admin ──────────────────────────┘
```

## The schedule is math, not a server

The single most important decision: **there is no broadcast process.** No
server streams, queues, or tracks position. Each browser computes what is on
air from the wall clock:

1. Take the channel's videos from `manifest.json`.
2. Sort by id, then shuffle with `mulberry32` seeded `1981 + channel number` —
   deterministic, so every device produces the identical running order.
3. `offset = (now - EPOCH) mod total_duration`, where `EPOCH` is 1981-08-01,
   MTV's sign-on. Walk the list to find the video and the seek position.

Consequences worth knowing:

- Every viewer is genuinely in sync, with zero coordination.
- Nothing runs while nobody is watching; the schedule advances on paper.
- Adding or removing a video reshuffles that channel — everyone jumps. This is
  acceptable because syncs are infrequent.
- `admin/schedule.py` is the server-side twin of the browser implementation.
  `admin/test_schedule.py` executes the real JavaScript functions under Node
  and compares ordering, wall-clock picks, and credits against Python fixtures.
- `GET /admin/api/now?ch=N` exposes the current item, offset, and next item to
  the admin panel and to external clients without adding a broadcast process.
  `&from=<id>` pins `now` to that video at offset 0: a remote control can walk
  the schedule order this way (the same "detour" as the player's `#remote`
  skip) and rejoin the clock on a channel change.

## Source modes

`MTV_SOURCE` picks what `mtv-sync` does each pass:

- `youtube` (default): mirror each channel's playlist — below.
- `files`: never mirror or prune. Download anything in
  `/config/playlists.txt` (optional; additive, see HOWTO), then
  `files_manifest.py` builds `manifest.json` from every `*.mp4` under
  `/videos`. The id is the relative path minus `.mp4`, so the `/videos/<id>.mp4`
  contract holds; the player (`encodeURIComponent`) and the Pi
  (`quote(id, safe="")`) percent-encode the whole id, `/` included, and nginx
  decodes it back to the path. Every `channels.json` channel airs everything;
  each top-level subfolder becomes a manifest `lineup` channel, numbered like
  the mediaDb playlist channels. Titles come from a `<name>.info.json` sidecar
  or the file name. Durations are cached in `.durations.files.json` keyed by
  path+size+mtime, so a replaced file is re-probed. `channels.json` is seeded
  with one playlist-less channel.

`sync.sh fetch [--into FOLDER] PLAYLIST...` is the one-shot downloader
(same yt-dlp format string as the mirror, partials kept in `/tmp`, writes
`<id>.info.json`); files mode also runs it over `playlists.txt` each pass.

The player only uses the YouTube-iframe fallback when some channel has a
playlist; with none and no manifest it shows NO SIGNAL and reloads every
minute.

## Sync (`sync.sh`, youtube mode)

Runs forever in `mtv-sync`, one pass per `SYNC_INTERVAL` (default 6h) or when
`/config/.sync-now` appears. Per pass:

1. Fetch each channel's playlist as `id<TAB>title`. A failed fetch keeps the
   last known list rather than treating the playlist as empty.
2. Publish the manifest of what is already local, so a long first download puts
   finished videos on air immediately.
3. Download the union of all playlists — h264 + aac mp4 ≤1080p (what YouTube
   serves; iOS Safari cannot play vp9/webm at the container's negotiation
   layer). A video in two channels is one file, keyed by video id.
4. Transcode each freshly-downloaded file to **HEVC** (libx265, CRF 23,
   AAC stream-copy, `hvc1` tag) in place — see *Codec* below. The H.264 source
   is only overwritten on a clean ffmpeg exit; a crash mid-transcode leaves
   the file untouched and the next pass retries.
5. Prune videos in no playlist — **skipped entirely if any fetch failed**, so a
   transient YouTube error cannot wipe the library.
6. Publish the manifest again.

A background watcher in the same container also republishes the manifest,
independent of the pass: every `PLAYLIST_POLL` seconds (60) it hashes
mediaDb's `/api/playlists` response and reruns the manifest step when the
hash changes. The manifest step holds an `fcntl` lock on
`/videos/.manifest.lock`, so the pass and the watcher never write
`manifest.json` or the durations cache at the same time.

Before each manifest publish, `mtv-sync` reads enriched `music-video` items
from mediaDb over the internal Docker network. Matching is by YouTube video id;
artist, canonical track title, album, year, featured artists, genres (the
top two are shown) and release type are copied into `manifest.json`. The
player's credits and `/admin/api/now` (which external clients render) share
one parser, `creditText` in `app/mtv.js`, mirrored by `credit()` in
`admin/schedule.py`: Artist / "Song" feat. X / Album (or Single/EP) · Year /
genres.
The request is best-effort, so a down or incomplete mediaDb falls back to the
YouTube playlist title without interrupting downloads or playback. The browser
never calls mediaDb directly, preserving public access to MTV while mediaDb
remains LAN-only.

Data also flows the other way. mediaDb can't fetch YouTube metadata itself
(YouTube demands sign-in), so the same manifest step writes an
`<id>.info.json` sidecar next to each video with the playlist's title and
channel, marked `"_source": "mtv-playlist"`. mediaDb reads sidecars before
trying YouTube, then matches MusicBrainz for album and year, which come back
here on the next sync. A sidecar MTV didn't write is never overwritten, and
pruning a video removes its sidecar.

### Playlist channels

Each mediaDb playlist is also a channel. The manifest step reads
`/api/playlists` and adds one channel per playlist that has local videos. The
channel number and name go in the manifest's `lineup`, and its videos go in
`channels`. `source` playlists are skipped because they mirror a YouTube
playlist that a `channels.json` channel already airs. These channels are
filters over the library that is already downloaded, so they never download
or prune anything. The player and `/admin/api/now` append them to
`channels.json`. `channels.json` wins a number clash.

Playlist channels are numbered after the admin lineup. A playlist keeps its
number from the previous manifest, so the channel a viewer remembers doesn't
drift as other playlists come and go. A deleted playlist's number isn't
reused. It moves only when `channels.json` takes it. If mediaDb is
unreachable, the previous pass's playlist channels are kept.

A playlist created, edited or deleted in mediaDb is in the manifest within a
minute (the watcher above). The player reads the lineup at page load, so a
new or removed channel shows on the TV after a reload; channel contents
refresh between videos. If the channel a viewer is on empties between
refreshes, the player falls to the nearest channel with content at the next
tune instead of going to dead air.

Durations come from `ffprobe`, cached in `.durations.json`; they are the only
input the schedule needs.

### Codec

Library files can be stored as **HEVC in MP4** (`hvc1`). iOS Safari ≥11 plays
HEVC MP4 natively, so the iOS-browser audience is unaffected. The reason for
the round-trip: some client hardware decodes HEVC but **not H.264** in
hardware, so H.264 streams pin a CPU core and visibly stutter, while HEVC
decodes for free and is smaller (CRF 23 ≈ 60% of the H.264 size for this
source). The transcode is software `libx265` inside the `sync.sh` container —
slow per file, but a one-time cost amortised across all future downloads. It
is off by default in the standalone deploy (`MTV_HEVC_TRANSCODE=1` turns it
on). `sync.sh transcode-library` walks an existing library once and is
idempotent: already-HEVC files are probed and skipped.

## Routing

The standalone deploy has no external proxy: nginx publishes a host port and
`nginx.admin.conf` proxies `/admin` to `mtv-admin` on the shared container
network. To front the app with your own reverse proxy instead, drop the
`include` in `nginx.conf`.

- The **player** is served to anyone who reaches the port.
- The **admin** route has no auth of its own. Keep the port on your LAN or put
  your own authenticating proxy in front. Never expose `/admin` publicly.

## Configuration lives in state, not the repo

`channels.json` is served from the mounted config dir (nginx aliases
`/channels.json` to the mount), seeded once from `app/channels.default.json`.
The admin page edits the state copy, so runtime lineup edits never touch the
repo checkout.

Writes are atomic (tmp file + `os.replace`) and chmod 0644, because the
separate nginx container must read the file after replacement.

## Caching

Set in `nginx.conf` and load-bearing: `.html/.js/.json` are `no-cache` so a
phone cannot run a stale player for days (this happened, 2026-08-29), while
`/videos/` is `immutable` for a year since a given video id never changes.
