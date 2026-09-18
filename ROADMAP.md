# Roadmap

## Status

Running in production at `mtv.thelunadog.com`. Player, mirror and admin page
are all live and stable.

- Player: deterministic clock-driven schedule, per-device channel memory,
  local mp4 playback with a YouTube-iframe fallback before the first sync.
- Mirror: 6-hourly playlist sync, prune-safe on fetch failure.
- Admin (`/admin`, LAN-only): lineup editor, sync trigger, now-playing
  monitor, and a viewers panel with geolocation.
- Own repo (`alex/mtv`) with CI, deployed by `bin/deploy`.

## Next

- **Fill in the genre channels.** Channels 2–4 (HIP HOP, GIRL POP, ALT) were
  drafted but have no playlist ids, and a revert left the live lineup with a
  single channel named "01". Needs real playlist ids, then a SYNC NOW.
- **Restore the channel names** lost in that revert.

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
