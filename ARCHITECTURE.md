# Architecture

Three containers on the shared `web` network, all behind Traefik. There is no
database and no application state beyond two files: `channels.json` (the
lineup) and `manifest.json` (what is on disk).

```
YouTube ──► mtv-sync ──► /videos (mp4 + manifest.json) ──► mtv (nginx) ──► viewer
                 ▲                                              ▲
                 │ .sync-now                      channels.json │
                 └────────── mtv-admin ───────────────────────────┘
                                  ▲
              Traefik access log ─┘   (viewers panel, read-only)
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
  the admin panel and the living-room Pi without adding a broadcast process.

## Sync (`sync.sh`)

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

Durations come from `ffprobe`, cached in `.durations.json`; they are the only
input the schedule needs.

### Codec

Library files are stored as **HEVC in MP4** (`hvc1`). iOS Safari ≥11 plays
HEVC MP4 natively, so the iOS-browser audience is unaffected. The reason
for the round-trip: the living-room Pi (mpv on Pi5) has a hardware HEVC
decoder (`rpi_hevc_dec` via V4L2 m2m) and **no H.264 hardware decoder**, so
H.264 streams pin a CPU core at ~95% and visibly stutter. Storing HEVC gives
the Pi zero-cost decode (~5% CPU) and a smaller file (CRF 23 ≈ 60% of the
H.264 size for this source). The transcode is software `libx265` inside the
`sync.sh` container — slow per file, but a one-time cost amortised across
all future downloads. `sync.sh transcode-library` walks the existing library
once and is idempotent: already-HEVC files are probed and skipped.

## Routing

Both routers match `Host(MTV_DOMAIN)`; `/admin` also matches `PathPrefix`, and
Traefik prefers it because its rule is longer (default priority = rule length).

- The **player route is deliberately public** — no `local-only`. Friends watch
  it. iCloud Private Relay viewers therefore appear as Fastly/Cloudflare IPs.
- The **admin route carries `local-only@file`**, which is the only access
  control that exists. `mtv-admin` has no auth of its own. Never expose that
  router without the middleware.

## Configuration lives in state, not the repo

`channels.json` is served from `state/mtv` (nginx aliases `/channels.json` to
the mount), seeded once from `app/channels.default.json`. The admin page edits
the state copy. This is what keeps runtime lineup edits from dirtying the
deploy checkout — a dirty tree makes `bin/deploy` skip the app.

Writes are atomic (tmp file + `os.replace`) and chmod 0644, because the
separate nginx container must read the file after replacement.

## Viewers panel

`/admin/api/viewers` parses Traefik's JSON access log, mounted read-only at
`/traefik-logs`. That log is the only place real client IPs exist: nginx in the
player sees only Traefik's address. Private sources are labelled by range
(LAN / WireGuard / Tailscale / host); public IPs are geolocated via ip-api.com
and cached in `/config/geoip.json`, shared with the `bin/mtv-viewers` CLI.

Two limits are inherent: the log rotates daily, so the panel covers roughly one
day; and it counts **page loads**, not concurrent watchers — a tab open for
three hours is one row.

AdGuard's blocklists resolve most geolocation APIs to `0.0.0.0`; ip-api.com
needs an explicit allow rule (`@@||ip-api.com^`) or every public IP reports
"geo lookup failed".

## Caching

Set in `nginx.conf` and load-bearing: `.html/.js/.json` are `no-cache` so a
phone cannot run a stale player for days (this happened, 2026-08-29), while
`/videos/` is `immutable` for a year since a given video id never changes.
