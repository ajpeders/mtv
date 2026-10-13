# mtv-admin — integrator notes

Lineup editor + sync trigger + now-playing preview for MTV. All routes live
under `/admin` (no stripprefix) so it slots straight onto the existing player
nginx proxy (`nginx.admin.conf`).

## Expected compose service block

```yaml
  mtv-admin:
    build: ./admin
    restart: unless-stopped
    user: ${MTV_UID:-1000}:${MTV_GID:-1000}      # so /config stays yours
    volumes:
      - ${MTV_CONFIG_PATH:-./data/config}:/config   # channels.json + .sync-now (rw)
      - ${MTV_VIDEOS_PATH:-./data/videos}:/videos:ro # manifest.json + mp4s (ro)
```

Adjust the two volume host paths to wherever the sync container's config and
videos dirs actually live — the *container* paths (`/config`, `/videos`) are
hardcoded in `main.py`.

## Must-knows

- **No auth in the app.** Keep the published port on your LAN or put your own
  authenticating proxy in front — do not expose `/admin` to the internet.
- **`/config` must be writable by uid 1000** (the container's `mtv` user):
  lineup saves write a tmp file + `os.replace`, and SYNC NOW touches
  `/config/.sync-now`.
  For a host-owned config directory, grant uid 1000 write access with a
  directory ACL. Saved lineups use mode 0644 so the separate nginx player
  can read them after an atomic replacement.
- The player reads `channels.json` at page load only — a lineup change shows
  up for viewers on their next reload, that's expected.
- The sync container is the other consumer of `/config/.sync-now`: it starts a
  pass within ~15s and deletes the file. `sync_pending` in `/admin/api/status`
  is just "does the flag file exist".
- The NOW PLAYING panel calls `/admin/api/now` once per lineup channel. The
  endpoint reads the mounted manifest and uses `schedule.py`, whose output is
  parity-tested against the player's JavaScript schedule.
- Skip is per-device (`#remote` in the player); the admin page links to the
  remote but cannot skip other people's screens — by design, not a gap.
