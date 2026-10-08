# Roadmap

## Status (2026-09-29)

Running in production at `mtv.thelunadog.com`. Player, mirror and admin page
are all live and stable.

- Player: deterministic clock-driven schedule, per-device channel memory,
  local mp4 playback. Before the first sync produces a manifest (or if the
  manifest is empty), it falls back to the channel's YouTube playlist through
  the iframe API, so the channel is never dead air.
- Mirror: 6-hourly playlist sync, prune-safe on fetch failure.
- Admin (`/admin`, LAN-only): lineup editor, sync trigger, now-playing
  monitor, viewers panel with geolocation, and `/admin/api/now` schedule API.
- Own repo (`alex/mtv`) with CI, deployed by `bin/deploy`.
- Live lineup is a single channel (`channels.json`: num 1, name "01").
  Multi-channel restore is deliberately deferred — see below.
- Sync-time mediaDb integration adds canonical artist, song, album, year,
  featured artists, genre and release type to the on-air lower third without
  making playback depend on mediaDb (2026-09-21/22). Sync writes `<id>.info.json`
  sidecars so mediaDb can enrich without YouTube.
- Playlist channels (2026-09-29): every mediaDb playlist airs as its own
  channel, numbered after the admin lineup and kept at the same number between
  syncs. Channels 2+ now exist without YouTube playlist ids.
- Live playlist sync (2026-10-03): `mtv-sync` watches mediaDb's playlist
  list every minute and republishes the manifest on change, so a playlist
  created, edited or deleted in mediaDb is a channel within a minute instead
  of at the next pass. The player hops off a channel that empties under it.

- Volume control + CRT toggle (2026-10-03): `VOL −`/`VOL +` pads and `-`/`+`
  keys in 10% steps, remembered per device, shown as an old-school
  bottom-right bar of ten rising green segments; `VOL +` while muted unmutes.
  Hidden on iOS, where pages cannot set media volume. A `CRT ON/OFF` pad
  (key `c`) switches to a plain picture: no scanlines, vignette, hum or
  overscan zoom (the YouTube iframe keeps its zoom to crop YouTube's chrome).
  Spec: `docs/superpowers/specs/2026-10-03-volume-control-design.md`.
- Artist spacing (2026-10-06): the shuffle never plays one artist twice in
  a row, nor across the loop seam, while keeping the shuffled order wherever
  it can. Same pass in `mtv.js` and `admin/schedule.py`, parity-tested.
- Standalone deploy + files mode (live 2026-10-02, 5b50050):
  `docker-compose.standalone.yml` runs anywhere on one port; `MTV_SOURCE=files`
  airs a folder of mp4s with subfolders as channels; `sync.sh fetch` downloads
  YouTube playlists into it. Homelab compose unchanged.

## Backlog

Story points for future items (1/2/3/5/8): 1–2 = mechanical, safe
unattended; 3 = needs codebase context; 5–8 = design judgment or
cross-service work.

- **(3) `.sync-now` is ignored during long passes.** The trigger is only read
  in the sleep between passes, and the one-time HEVC transcode makes a pass
  take hours, so "Sync now" in the admin page does nothing until it finishes
  (seen 2026-09-21: had to run the manifest step by hand to publish new
  mediaDb metadata). Check the trigger between transcodes and, when set,
  republish the manifest (cheap) before carrying on, or run manifest refresh
  on its own short timer independent of downloads. Playlist changes no longer
  need the trigger (2026-10-03 watcher); `channels.json` edits and new
  mediaDb metadata still wait for the pass.

- **(1) Verify standalone files mode in a real browser.** The 2026-09-30
  check was curl-only (player, manifest, `/admin/api/now`, video URLs incl.
  `%2F` subfolder ids). Play a subfolder channel with `&`/`#` in a filename.
- **(1) Verify `fetch` with a bare playlist id.** Only a single-video URL
  was tested; the playlist URL format is the one the mirror already uses.
- **(1) favicon.** `/favicon.ico` 404s on every page load (console noise).

## Considered and deliberately not done

- **Restoring the genre channels (2–4).** The single-channel lineup is
  working fine, and the extra channels need three YouTube playlist ids that
  aren't worth chasing right now. Revisit if HIP HOP / GIRL POP / ALT are
  actually wanted.
- **Auth on the admin page.** `local-only@file` is the gate. Adding app-level
  auth would duplicate the Traefik middleware and add a credential to manage.
- **Skipping other people's screens.** Skip is remote-holder-only and
  per-device (`/#remote`). A global skip would let one viewer yank the channel
  out from under everyone.
- **Live concurrent-viewer count.** The viewers panel counts page loads from
  the access log. True concurrency needs either a heartbeat from the player or
  segment-level log parsing; the value did not justify either.
- **qBittorrent-style download notifications.** Nothing here is user-initiated;
  a sync pass finishing is not an event worth a push.
- **4K streaming.** Downloads are capped at 1080p h264+aac (`sync.sh`) because
  iOS Safari cannot play vp9/webm. Going to 2160p means either 4K h264 (rare on
  YouTube) or shipping a second, non-Safari format and picking per client — plus
  ~4× the disk and a safe 4K source per id. 1080p is fine for a CRT-style
  channel; not worth the pipeline complexity today.

## Known limits

- Standalone `/admin` has no auth (LAN or own proxy only) and no viewers log.
- Files mode: a file replaced under the same name stays browser-cached
  (`/videos/` is immutable for a year); only lower-case `.mp4` is aired.
- Viewers panel covers ~24h (the Traefik access log rotates daily).
- Geolocation depends on an AdGuard allow rule for ip-api.com.
- Changing a channel's library reshuffles its schedule for everyone at once.
- The schedule math is implemented twice, in browser JavaScript and Python;
  `admin/test_schedule.py` executes both and prevents silent drift.

## History

- 2026-09-30 — standalone deploy (`docker-compose.standalone.yml`, `/admin`
  proxied by nginx), files mode (`MTV_SOURCE=files`, `files_manifest.py`) and
  the `sync.sh fetch` playlist downloader, so others can run MTV without the
  homelab.

- 2026-09-30 — idle compute: the admin page stops polling while its tab is
  hidden, and the player neither starts in a background tab nor keeps
  preloading the next video while paused. The server side was already idle
  (static files; the schedule is clock math in the browser).
- 2026-09 — mediaDb integration: `mtv-sync` now enriches the manifest from
  mediaDb by YouTube id. The lower third adds album and year when available and
  falls back to playlist-title parsing when mediaDb is unavailable.
- 2026-09 — persistent now-playing credits: the artist/song lower-third no
  longer fades after 8s; it stays pinned while the video is on air and is
  swapped on the next track. The sign-off re-show and its `outroShown`
  bookkeeping were removed as redundant.
- 2026-09 — CRT player, playlist mirror, admin + viewers panel shipped; own
  repo (`alex/mtv`) with CI wired up; deploy health budget widened to 12
  tries × 10s after a false rollback (mtv-admin briefly 502s post-recreate);
  `/admin/api/now` added as the schedule authority for the living-room Pi.
- 2026-09 — backlog burn-down: dropped inline `mem_limit`/`memswap_limit`
  lines (the `apps-mem-limits` overlay wins), removed the `#station-bug`
  logo, and scaled the now-playing credits with `vmin` for 4K screens.
- 2026-10 — `/admin/api/now?from=<id>` so the living-room Pi can skip songs
  (a detour along the schedule order, like the player's `#remote` skip).
