# Roadmap

## Status (2026-09-20)

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
- Sync-time mediaDb integration adds canonical artist, song, album, and year to
  the on-air lower third without making playback depend on mediaDb.

## Backlog

None. Story points for future items (1/2/3/5/8): 1–2 = mechanical, safe
unattended; 3 = needs codebase context; 5–8 = design judgment or
cross-service work. The 2026-09 backlog is shipped — see History.

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

- Viewers panel covers ~24h (the Traefik access log rotates daily).
- Geolocation depends on an AdGuard allow rule for ip-api.com.
- Changing a channel's library reshuffles its schedule for everyone at once.
- The schedule math is implemented twice, in browser JavaScript and Python;
  `admin/test_schedule.py` executes both and prevents silent drift.

## History

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
