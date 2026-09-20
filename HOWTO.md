# HOWTO

## Add or change a channel

1. Open `https://mtv.thelunadog.com/admin` (LAN, WireGuard or Tailscale).
2. `+ CHANNEL`, set the number, name (≤24 chars) and the YouTube playlist id —
   the `PL…` value from the playlist URL's `list=` parameter.
3. `SAVE`, then `SYNC NOW`.

A channel may be saved with an empty playlist; it is skipped by the sync and
shows "Off air" until you fill it in. Viewers pick up lineup changes on their
next page load.

## Force a sync without the admin page

```sh
docker exec mtv-sync touch /config/.sync-now   # starts a pass within ~15s
docker compose logs -f mtv-sync
```

## Check what is on air

The schedule endpoint is available through the LAN/VPN-only admin route:

```sh
curl -s 'https://mtv.thelunadog.com/admin/api/now?ch=1' | python3 -m json.tool
```

It reports the current item and offset, the next item, and the public video URL
base. A 404 means the channel is unknown, empty, or not synced yet.

## See who is watching

The admin page has a VIEWERS table (last ~24h of page loads, since the Traefik
log rotates daily). From the host:

```sh
bin/mtv-viewers        # grouped by IP, with location
bin/mtv-viewers -f     # live, one line per join
```

Single hits from hosting providers (Censys, Shodan, Fastly probes) are internet
background noise on a public route, not viewers.

## Geolocation says "geo lookup failed"

AdGuard blocks most IP-geolocation APIs. Allow the one this uses:

1. AdGuard → Filters → Custom filtering rules → add `@@||ip-api.com^`
2. `docker restart adguard` (brief LAN-wide DNS blip)

Verify: `docker exec mtv-admin python3 -c "import socket;
print(socket.getaddrinfo('ip-api.com',80)[0][4][0])"` — anything other than
`0.0.0.0` is working.

## Change the player's look

Everything visual is in `app/index.html` (markup and the inline CRT styles)
and `app/mtv.js` (player logic). The admin page is a **separate container** built
from `admin/` — reverting the player does not touch it. Do not restore an older
`docker-compose.yml` over the current one to revert a look: pre-`mtv-admin`
versions have no admin service and will delete it.

## Rebuild after changing the admin app

```sh
cd services && docker compose up -d --build mtv-admin
```

From the homelab `services/` directory, not `apps/mtv/` — the containers belong
to the `services` compose project, and running compose from `apps/mtv` fails
with a container-name conflict.

## Deploy

Push to `main` on `alex/mtv`. CI must pass, then `bin/deploy` (every 3 min)
fast-forwards and recreates the containers, health-checks through the real
ingress, and rolls back on failure.

```sh
tail -f state/deploy/deploy.log   # decisions
cat state/deploy/mtv.log          # build/deploy output
```

It defers while anyone is watching (any `/videos/` fetch in the last 5 min), so
a deploy can sit pending during a listening session — that is intended.

If a deploy fails health, its commit is recorded in `state/deploy/mtv.failed-sha`
and **not retried until a new push**, and a red `deploy/homelab` commit status
makes the combined status fail, which blocks the CI gate as well. The clean fix
is a new commit; clearing the marker alone is not enough.

## Nothing plays / "NO MANIFEST YET"

In order:

```sh
docker compose logs --tail 50 mtv-sync     # fetch failures? disk full?
ls /mnt/storage/mtv-videos/*.mp4 | wc -l   # anything mirrored?
cat /mnt/storage/mtv-videos/manifest.json | head -c 200
```

A video needs a non-zero `ffprobe` duration to be scheduled; zero-duration
files are dropped from the manifest on purpose.
