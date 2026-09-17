# mtv-admin — integrator notes

Lineup editor + sync trigger + now-playing preview for MTV. All routes live
under `/admin` (no stripprefix) so it slots straight onto the existing
`mtv.thelunadog.com` router with a path rule.

## Expected compose service block

```yaml
  mtv-admin:
    build: ./admin
    container_name: mtv-admin
    restart: unless-stopped
    volumes:
      - ../../state/mtv/config:/config          # channels.json + .sync-now (rw)
      - ../../state/mtv/videos:/videos:ro       # manifest.json + mp4s (ro)
    networks:
      - web
    labels:
      - traefik.enable=true
      - traefik.http.routers.mtv-admin.rule=Host(`mtv.thelunadog.com`) && PathPrefix(`/admin`)
      - traefik.http.routers.mtv-admin.entrypoints=websecure
      - traefik.http.routers.mtv-admin.tls.certresolver=letsencrypt
      - traefik.http.routers.mtv-admin.middlewares=local-only@file
      - traefik.http.routers.mtv-admin.priority=10   # must beat the player's catch-all route
      - traefik.http.services.mtv-admin.loadbalancer.server.port=8000
```

Adjust the two volume host paths to wherever the sync container's config and
videos dirs actually live — the *container* paths (`/config`, `/videos`) are
hardcoded in `main.py`.

## Must-knows

- **No auth in the app.** `local-only@file` on the Traefik router is the only
  gate — do not expose this router without it. The main MTV route is public;
  the `/admin` PathPrefix route must carry the middleware and higher priority.
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
- The NOW PLAYING panel fetches `/videos/manifest.json` from the browser —
  i.e. through the *player's* nginx route on the same host. It works only when
  the admin page is served under `mtv.thelunadog.com`; the panel degrades to
  "NO MANIFEST YET" if that fetch fails.
- Skip is per-device (`#remote` in the player); the admin page links to the
  remote but cannot skip other people's screens — by design, not a gap.
