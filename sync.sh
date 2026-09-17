#!/bin/sh
# Mirror every channel's YouTube playlist into /videos and keep it in sync.
# Runs forever inside the mtv-sync container; one pass every SYNC_INTERVAL,
# or sooner when the admin page touches /config/.sync-now.
# The channel lineup lives in /config/channels.json (state/mtv, admin-editable
# at runtime — NOT in the repo, so admin edits can't dirty the deploy
# checkout); it is seeded from app/channels.default.json on first start.
# Channels with an empty "playlist" are skipped, so the lineup can ship ahead
# of the playlists existing.
#
# Each pass:
#   1. fetch each channel playlist's id<TAB>title list (kept per-channel in
#      /tmp/pl.<num>.tsv; a failed fetch keeps the last known list)
#   2. publish a manifest of what's already local (so a long first download
#      still puts finished videos on air immediately)
#   3. download anything new across the union of all playlists (h264+aac mp4
#      ≤1080p — iOS Safari can't do vp9/webm); a video in two playlists is
#      one file, keyed by video id
#   4. delete local videos no longer in ANY playlist (and unarchive them so a
#      re-added video gets re-downloaded) — skipped if any fetch failed
#   5. publish the manifest again
set -u

DIR=/videos
CHANNELS=/config/channels.json
TRIGGER=/config/.sync-now
INTERVAL="${SYNC_INTERVAL:-6h}"
ARCHIVE="$DIR/.archive"
UNION=/tmp/union.tsv
TAB=$(printf '\t')

[ -f "$CHANNELS" ] || cp /app/channels.default.json "$CHANNELS"

# SYNC_INTERVAL in seconds, for the trigger-aware sleep below
case "$INTERVAL" in
  *h) INTERVAL_S=$(( ${INTERVAL%h} * 3600 )) ;;
  *m) INTERVAL_S=$(( ${INTERVAL%m} * 60 )) ;;
  *s) INTERVAL_S=${INTERVAL%s} ;;
  *)  INTERVAL_S=$INTERVAL ;;
esac

# emit "num<TAB>playlist-id" for every channel that has a playlist configured
lineup() {
  python3 - "$CHANNELS" <<'EOF'
import json, sys
for ch in json.load(open(sys.argv[1])):
    pl = (ch.get("playlist") or "").strip()
    if pl:
        print(f"{ch['num']}\t{pl}")
EOF
}

manifest() {
  # per-video duration (ffprobe, cached in .durations.json) — the player's
  # broadcast schedule is pure clock math over these durations — plus which
  # channels each video belongs to (the player filters client-side)
  set -- /tmp/pl.*.tsv
  [ -e "$1" ] || return 0
  python3 - "$DIR" "$@" <<'EOF'
import json, os, subprocess, sys
d = sys.argv[1]
cachep = os.path.join(d, ".durations.json")
try:
    cache = json.load(open(cachep))
except (OSError, ValueError):
    cache = {}
videos = {}    # id -> {id, title, duration}
channels = {}  # channel num -> [ids]
for tsv in sys.argv[2:]:
    num = os.path.basename(tsv).split(".")[1]   # pl.<num>.tsv
    ids = channels.setdefault(num, [])
    seen = set(ids)   # a playlist can contain the same video twice
    for line in open(tsv, encoding="utf-8"):
        vid, _, title = line.rstrip("\n").partition("\t")
        path = os.path.join(d, vid + ".mp4")
        if not vid or not os.path.exists(path):
            continue
        if vid not in videos:
            dur = cache.get(vid)
            if dur is None:
                out = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip()
                try:
                    dur = round(float(out), 2)
                except ValueError:
                    dur = 0
                cache[vid] = dur
            if dur <= 0:
                continue
            videos[vid] = {"id": vid, "title": title, "duration": dur}
        if vid in videos and vid not in seen:
            seen.add(vid)
            ids.append(vid)
json.dump(cache, open(cachep + ".tmp", "w"))
os.replace(cachep + ".tmp", cachep)
tmp = os.path.join(d, ".manifest.tmp")
with open(tmp, "w", encoding="utf-8") as f:
    json.dump({"videos": list(videos.values()), "channels": channels}, f)
os.replace(tmp, os.path.join(d, "manifest.json"))
print(f"[sync] manifest: {len(videos)} local videos across {len(channels)} channels")
EOF
}

while :; do
  ok=1
  lineup > /tmp/lineup.tsv
  while IFS="$TAB" read -r num pl; do
    if yt-dlp --flat-playlist --print "%(id)s	%(title)s" \
         "https://www.youtube.com/playlist?list=$pl" > "/tmp/pl.$num.new" 2>/tmp/sync.err \
       && [ -s "/tmp/pl.$num.new" ]; then
      mv "/tmp/pl.$num.new" "/tmp/pl.$num.tsv"
      echo "[sync] ch$num: $(wc -l < "/tmp/pl.$num.tsv") videos in playlist"
    else
      ok=0
      rm -f "/tmp/pl.$num.new"
      echo "[sync] ch$num: playlist fetch failed, keeping last known list:"
      tail -2 /tmp/sync.err
    fi
  done < /tmp/lineup.tsv

  # union of every channel's list, deduped by video id (first title wins)
  awk -F'\t' '!seen[$1]++' /tmp/pl.*.tsv > "$UNION" 2>/dev/null || : > "$UNION"

  if [ -s "$UNION" ]; then
    manifest
    awk -F'\t' '{print "https://youtu.be/" $1}' "$UNION" > /tmp/dl.txt
    yt-dlp \
      -f "bv*[vcodec^=avc1][height<=1080]+ba[acodec^=mp4a]/bv*[height<=1080]+ba/b" \
      --merge-output-format mp4 \
      -o "$DIR/%(id)s.%(ext)s" \
      --download-archive "$ARCHIVE" \
      --no-progress --ignore-errors \
      -a /tmp/dl.txt

    if [ "$ok" = 1 ]; then
      for f in "$DIR"/*.mp4; do
        [ -e "$f" ] || continue
        id=$(basename "$f" .mp4)
        if ! grep -q "^$id$TAB" "$UNION"; then
          echo "[sync] pruning $id (in no channel playlist)"
          rm -f "$f"
          [ -f "$ARCHIVE" ] && sed -i "/ $id\$/d" "$ARCHIVE"
        fi
      done
    else
      echo "[sync] skipping prune (a playlist fetch failed this pass)"
    fi
    manifest
  else
    echo "[sync] no playlist data at all, keeping current library"
  fi
  echo "[sync] sleeping $INTERVAL (or until $TRIGGER appears)"
  slept=0
  while [ "$slept" -lt "$INTERVAL_S" ]; do
    if [ -e "$TRIGGER" ]; then
      rm -f "$TRIGGER"
      echo "[sync] sync-now trigger, starting a pass"
      break
    fi
    sleep 15
    slept=$((slept + 15))
  done
done
