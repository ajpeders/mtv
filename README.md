# MTV

A CRT-styled music-video channel at `mtv.thelunadog.com`. It behaves like
broadcast TV, not a video site: every viewer sees the same video at the same
moment, there is no "play" button, and nothing is stored per-viewer. You tune
in and join whatever is already playing.

Videos are mirrored locally from YouTube playlists, so playback does not depend
on YouTube at watch time.

During each sync, MTV reads enriched music-video metadata from mediaDb and puts
artist, song, album, and year into the local manifest. Playback remains
independent: if mediaDb is unavailable, playlist titles are used instead.
Each mediaDb playlist also airs as its own channel, numbered after the admin
lineup.

## Run it yourself

Any Docker host, one command, no Traefik. `docker-compose.standalone.yml`
publishes the player **and** `/admin` on one port (default 8080) and keeps
state in `./data/config` and videos in `./data/videos`.

> **`/admin` has no auth.** Anyone who can reach the port can edit the lineup.
> Keep it on your LAN (`MTV_PORT=192.168.1.10:8080` binds one address) or put
> your own authenticating proxy in front.

**Files mode** — air a folder of mp4s, no YouTube involved:

```sh
mkdir -p data/config data/videos          # create them as yourself (uid 1000 by default)
cp ~/Music\ Videos/*.mp4 data/videos/     # "Artist - Song.mp4" names give credits
echo MTV_SOURCE=files > .env
docker compose -f docker-compose.standalone.yml up -d --build
```

Every file airs on channel 1; each subfolder of `data/videos` is also its own
channel. Open `http://<host>:8080/`.

**YouTube mode** (default) — mirror playlists like the homelab does: leave
`MTV_SOURCE` unset, start the same way, then set each channel's playlist id at
`http://<host>:8080/admin` and press `SYNC NOW`.

To download YouTube playlists into files mode instead:

```sh
docker compose -f docker-compose.standalone.yml run --rm mtv-sync fetch PLxxxxxxxx
```

See [HOWTO.md](HOWTO.md) for folders-as-channels, `playlists.txt` and the other
settings (`.env.example`). If your user isn't uid 1000, set `MTV_UID`/`MTV_GID`.

## Homelab deploy

```sh
cp .env.example .env          # set MTV_DOMAIN, optionally MTV_VIDEOS_PATH
docker compose up -d --build  # from the homelab services/ dir in production
```

On the homelab this app is included by `services/docker-compose.yml` and
deployed by `bin/deploy` (conf: `bin/deploy.d/mtv.conf`), so a push to `main`
that passes CI deploys itself.

| URL | What |
|---|---|
| `https://mtv.thelunadog.com/` | the player — **public** |
| `https://mtv.thelunadog.com/#remote` | player with the skip button enabled |
| `https://mtv.thelunadog.com/admin` | lineup editor, sync, viewers — **LAN/VPN only** |
| `GET /admin/api/now?ch=N` | current and next item for channel N — **LAN/VPN only** |
| `GET /admin/api/now?ch=N&from=ID` | video `ID` from the top plus its successor, ignoring the clock (the Pi's skip detour) — **LAN/VPN only** |

## Containers

| Name | Image | Role |
|---|---|---|
| `mtv` | nginx | serves the player and the mirrored mp4s |
| `mtv-sync` | yt-dlp | mirrors each channel's playlist (or scans the files), enriches and builds `manifest.json` |
| `mtv-admin` | FastAPI (built here) | `/admin` — lineup, sync trigger, now-playing, viewers |

## Key commands

```sh
docker compose logs -f mtv-sync            # watch a mirror pass
docker exec mtv-sync touch /config/.sync-now   # force a sync within ~15s
bin/mtv-viewers                            # who watched, from where (host CLI)
bin/mtv-viewers -f                         # live joins
```

See [HOWTO.md](HOWTO.md) for common tasks, [ARCHITECTURE.md](ARCHITECTURE.md)
for how the schedule and sync work, and [ROADMAP.md](ROADMAP.md) for status.
