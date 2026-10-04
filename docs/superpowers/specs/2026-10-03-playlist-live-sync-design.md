# Live sync of mediaDb playlists into MTV channels

Date: 2026-10-03. Repo: `alex/mtv`, files `sync.sh`, `app/mtv.js`,
`docker-compose.yml`.

## Goal

A playlist created, edited or deleted in mediaDb becomes, changes or stops
being an MTV channel within about a minute. Today that only happens when
`mtv-sync` rebuilds the manifest at the start and end of a pass: every 6 h,
or hours later when the HEVC transcode makes a pass long. The admin page's
"Sync now" is ignored during a pass, so there is no way to hurry it.

## Non-goals

- No change to mediaDb. mtv polls; mediaDb does not notify.
- No live lineup in the player. A new or deleted channel shows up on the TV
  after a page reload, as agreed. Channel contents already refresh between
  videos.
- Downloads, HEVC transcodes and pruning stay with the pass. A newly
  downloaded video still goes on air at the end of the pass, as today.
- The ignored "Sync now" trigger is not fixed here. Playlist changes no longer
  need it.

## Design

### Playlist watcher in `mtv-sync`

`sync.sh` starts a background loop next to the download pass, in YouTube mode
only and only when `MEDIADB_URL` is set. Every `PLAYLIST_POLL` seconds
(default 60; compose exposes it as `MTV_PLAYLIST_POLL`) it fetches
`/api/playlists` from mediaDb and hashes the response body. When the hash
differs from the last one it ran the manifest step for, it reruns the existing
`manifest` function and logs one line. The first successful tick after
container start runs the manifest once (the hash starts empty); this is cheap
and harmless. An unreachable mediaDb or a failed manifest leaves the stored
hash alone, so the next tick retries.

Hashing the whole body is deliberate. Every write path in mediaDb bumps
`updated_at` or `item_count`, and an extra rebuild on an unrelated field
change costs a few HTTP calls and one file write.

The manifest step is cheap on repeat: ffprobe durations are cached in
`.durations.json`, sidecars are only written when their content changes, and
mediaDb item metadata is a handful of paged requests on the LAN.

### Manifest lock

The pass and the watcher can both call `manifest`. Its Python block takes an
exclusive `fcntl` lock on `/videos/.manifest.lock` for its lifetime, so
`manifest.json` and `.durations.json` always see one writer. The loser waits,
then runs with the winner's output as its "previous" manifest, which keeps the
sticky channel numbers correct.

### Player: a channel that vanishes mid-session

When a background manifest refresh leaves the current channel with no videos
(its playlist was deleted or emptied), the player hops to the nearest channel
with content, using the same scan as startup and channel up/down. Without
this, the player goes to dead air at the end of the current video until
someone reloads. The hop takes effect at the next tune: the video already
playing finishes first.

### Documentation

README, ARCHITECTURE (sync pass and playlist channels), HOWTO (the new
variable) and ROADMAP (status, backlog note on the trigger) say that playlist
changes arrive within a minute.

## Verification

On isis after deploy:

1. `docker logs -f mtv-sync` shows the watcher start line.
2. Create a manual playlist in mediaDb with a few downloaded videos. Within a
   minute the log shows `mediadb playlists changed, refreshing the manifest`
   and `manifest.json`'s `lineup` lists it.
3. Delete it. Within a minute it is gone from `lineup`.
4. Reload the TV page: the channel appears and disappears accordingly.

No automated test covers the manifest step today (it is a shell heredoc) and
this change adds none. `sh -n sync.sh` and `node --check app/mtv.js` run
before commit.
