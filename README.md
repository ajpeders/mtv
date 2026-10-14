# MTV

A CRT-styled music-video channel. It behaves like broadcast TV, not a video
site: every viewer sees the same video at the same moment, there is no "play"
button, and nothing is stored per-viewer. You tune in and join whatever is
already playing.

Videos are mirrored locally from YouTube playlists, so playback does not depend
on YouTube at watch time.

During each sync, MTV reads enriched music-video metadata from mediaDb and puts
artist, song, album, and year into the local manifest. Playback remains
independent: if mediaDb is unavailable, playlist titles are used instead.
Each mediaDb playlist also airs as its own channel, numbered after the admin
lineup. Playlist changes in mediaDb reach the channel list within a minute;
the TV shows a new or removed channel after a page reload.

## Run it yourself

Any Docker host, one command. `docker-compose.standalone.yml` publishes the
player **and** `/admin` on one port (default 8080) and keeps state in
`./data/config` and videos in `./data/videos`.

> **`/admin` has no auth.** Anyone who can reach the port can edit the lineup.
> Keep it on your LAN (`MTV_PORT=192.168.1.10:8080` binds one address) or put
> your own authenticating proxy in front.

Create the state and video directories as yourself first. The containers run as
uid 1000, so if Docker creates these dirs for you they end up root-owned and the
sync and `/admin` fail to write:

```sh
mkdir -p data/config data/videos
```

**Files mode** — air a folder of mp4s, no YouTube involved:

```sh
cp ~/Music\ Videos/*.mp4 data/videos/     # "Artist - Song.mp4" names give credits
echo MTV_SOURCE=files > .env
docker compose -f docker-compose.standalone.yml up -d --build
```

Every file airs on channel 1; each subfolder of `data/videos` is also its own
channel. Open `http://<host>:8080/`.

**YouTube mode** (default) — mirror playlists:

```sh
docker compose -f docker-compose.standalone.yml up -d --build
```

Then set each channel's playlist id at `http://<host>:8080/admin` and press
`SYNC NOW`.

> A fresh install has an empty lineup, so the TV shows **NO SIGNAL** until you
> add at least one playlist id.

To download YouTube playlists into files mode instead:

```sh
docker compose -f docker-compose.standalone.yml run --rm mtv-sync fetch PLxxxxxxxx
```

See [HOWTO.md](HOWTO.md) for folders-as-channels, `playlists.txt` and the other
settings (`.env.example`). If your user isn't uid 1000, set `MTV_UID`/`MTV_GID`.

## Containers

| Name | Image | Role |
|---|---|---|
| `mtv` | nginx | serves the player and the mirrored mp4s |
| `mtv-sync` | yt-dlp | mirrors each channel's playlist (or scans the files), enriches and builds `manifest.json` |
| `mtv-admin` | FastAPI (built here) | `/admin` — lineup, sync trigger, now-playing |

## Key commands

```sh
docker compose -f docker-compose.standalone.yml logs -f mtv-sync   # watch a mirror pass
docker exec mtv-sync touch /config/.sync-now                       # force a sync within ~15s
```

See [HOWTO.md](HOWTO.md) for common tasks and
[ARCHITECTURE.md](ARCHITECTURE.md) for how the schedule and sync work.

## License

[MIT](LICENSE)
