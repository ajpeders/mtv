# Roadmap

## Status (2026-09-19)

Running in production at `mtv.thelunadog.com`. Player, mirror and admin page
are all live and stable.

- Player: deterministic clock-driven schedule, per-device channel memory,
  local mp4 playback. Pre-first-sync (or an empty manifest) shows a "NO
  SIGNAL" card that polls every 30s and tunes in on its own — the old
  YouTube-iframe fallback was removed.
- Mirror: 6-hourly playlist sync, prune-safe on fetch failure.
- Admin (`/admin`, LAN-only): lineup editor, sync trigger, now-playing
  monitor, and a viewers panel with geolocation.
- Own repo (`alex/mtv`) with CI, deployed by `bin/deploy`.
- Live lineup is currently a single channel (`channels.json`: num 1, name
  "01") — the genre channels from a prior revert are still not restored.

## Backlog

Story points (1/2/3/5/8): 1–2 = mechanical, safe unattended; 3 = needs
codebase context; 5–8 = design judgment or cross-service work.

| pts | item |
|---|---|
| 1 | ✅ Delete the inline `mem_limit`/no-`memswap_limit` lines for `mtv`, `mtv-sync`, `mtv-admin` from `docker-compose.yml` — `services/apps-mem-limits.yml` already sets and wins all three (128m/512m/128m); the inline copies are dead weight. Acceptance: values removed from this repo's compose, `docker compose config` on the homelab still shows the same effective limits from the overlay. |
| 2 [human-assisted] | Restore channels 2–4 (HIP HOP, GIRL POP, ALT) in `channels.json` with real YouTube playlist ids and their names, then trigger `SYNC NOW`. Blocked on the user supplying the three playlist ids — the admin-page edit itself is mechanical. Acceptance: `/admin` lineup shows 4 named channels, mirror pulls all four playlists, player shows non-"01" names on channels 2–4. |
| 1 | ✅ Remove the `#station-bug` MTV logo (bottom-right, `app/index.html`). Delete the element and its CSS (`#station-bug`, `.station-m`, `.station-tv`, `.station-caption`). Acceptance: no logo on the player at any viewport; credits and channel OSD unchanged. |
| 2 | ✅ Scale the now-playing credits (`#osd-track`) for 4K/large screens. The `clamp()` px ceilings cap it: `#track-artist` 47px, `#track-song` 36px, `max-width: min(70vw, 850px)` — on a 3840px-wide screen that reads as a tiny corner caption. Raise or drop the px maxima (and the 850px width cap) so the block scales with `vmin`; keep the phone minima. Acceptance: at 3840x2160 the artist line is roughly 3.8vmin (~82px) tall; unchanged on phones. |

## Considered and deliberately not done

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

## Known limits

- Viewers panel covers ~24h (the Traefik access log rotates daily).
- Geolocation depends on an AdGuard allow rule for ip-api.com.
- Changing a channel's library reshuffles its schedule for everyone at once.
- The schedule math is implemented twice — `app/mtv.js` and the admin's
  NOW PLAYING panel — and must be kept identical.

## History

- 2026-09 — CRT player, playlist mirror, admin + viewers panel shipped; own
  repo (`alex/mtv`) with CI wired up; deploy health budget widened to 12
  tries × 10s after a false rollback (mtv-admin briefly 502s post-recreate);
  YouTube-iframe fallback replaced with a polling "NO SIGNAL" state.
