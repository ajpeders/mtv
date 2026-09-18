# MTV

A CRT-styled music-video channel at `mtv.thelunadog.com`. It behaves like
broadcast TV, not a video site: every viewer sees the same video at the same
moment, there is no "play" button, and nothing is stored per-viewer. You tune
in and join whatever is already playing.

Videos are mirrored locally from YouTube playlists, so playback does not depend
on YouTube at watch time.

## Quick start

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

## Containers

| Name | Image | Role |
|---|---|---|
| `mtv` | nginx | serves the player and the mirrored mp4s |
| `mtv-sync` | yt-dlp | mirrors each channel's playlist, builds `manifest.json` |
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
