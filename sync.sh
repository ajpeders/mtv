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
#   1. fetch each channel playlist's id<TAB>title<TAB>channel list (kept
#      per-channel in /tmp/pl.<num>.tsv; a failed fetch keeps the last known list)
#   2. publish a manifest of what's already local (so a long first download
#      still puts finished videos on air immediately), and an <id>.info.json
#      sidecar per video so mediaDb can enrich it without asking YouTube
#   3. download anything new across the union of all playlists (≤1080p mp4,
#      transcoded to HEVC after download — see HEVC_TRANSCODE below); a
#      video in two playlists is one file, keyed by video id
#   4. delete local videos no longer in ANY playlist (and unarchive them so a
#      re-added video gets re-downloaded) — skipped if any fetch failed
#   5. publish the manifest again
# A background watcher also republishes the manifest within a minute of a
# mediaDb playlist changing (see watch_playlists).
#
# One-shot mode: `sync.sh transcode-library` walks every mp4 under $DIR and
# transcodes any that are still H.264 (probed first, so the operation is
# idempotent — already-HEVC files are skipped). Used to convert an existing
# library after switching to HEVC. Run once per host upgrade; resumes
# safely across restarts.
#
# Files mode (MTV_SOURCE=files): no channel playlists are mirrored or pruned.
# Each pass downloads anything listed in /config/playlists.txt (optional) and
# then builds the manifest from whatever mp4s are in $DIR (files_manifest.py).
#
# One-shot mode: `sync.sh fetch [--into FOLDER] PLAYLIST...` downloads
# playlists (URL or id) into $DIR (or $DIR/FOLDER, which files mode airs as
# its own channel) as <id>.mp4 + <id>.info.json. Nothing is ever deleted.
# With no PLAYLIST it fetches everything in /config/playlists.txt.
set -u

DIR=/videos
CHANNELS=/config/channels.json
TRIGGER=/config/.sync-now
INTERVAL="${SYNC_INTERVAL:-6h}"
# how often the mediaDb playlist list is checked for changes (seconds)
PLAYLIST_POLL="${PLAYLIST_POLL:-60}"
SOURCE="${MTV_SOURCE:-youtube}"
PLAYLISTS=/config/playlists.txt
# ≤1080p H.264+AAC so the mp4 plays everywhere, Safari included
FORMAT="bv*[vcodec^=avc1][height<=1080]+ba[acodec^=mp4a]/bv*[height<=1080]+ba/b"
MEDIADB_URL="${MEDIADB_URL-http://mediadb:8090}"
ARCHIVE="$DIR/.archive"
UNION=/tmp/union.tsv
TAB=$(printf '\t')

# Disable the HEVC transcode step by setting HEVC_TRANSCODE=0. Default: on.
# The Pi5 client (mpv) has a hardware HEVC decoder but no H.264 one, so the
# living-room TV stutters on H.264 software decode. Keeping library files as
# HEVC gives the Pi zero-cost decode and a smaller download.
HEVC_TRANSCODE="${HEVC_TRANSCODE:-1}"
# libx265 preset / quality. CRF 23 is "visually lossless" for this source
# (1080p music video) and ~60% of the original H.264 size. -tag:v hvc1 is
# what iOS Safari reads; the default (hev1) sometimes plays, sometimes
# doesn't. -c:a copy keeps AAC without re-encoding.
X265_CRF="${X265_CRF:-23}"
X265_PRESET="${X265_PRESET:-medium}"

# transcoded_path <input.mp4>
#   Transcode to HEVC in place. Atomic via temp file + mv. The original H.264
#   bytes are only overwritten on a successful exit, so a crash mid-transcode
#   leaves the source intact (ffmpeg would still be reading from the inode).
transcoded_path() {
    src="$1"
    [ -f "$src" ] || return 0
    # Skip if already HEVC — keeps the script idempotent for re-runs.
    cur=$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name \
              -of default=nw=1:nk=1 "$src" 2>/dev/null)
    case "$cur" in
        hevc|h265) echo "[x265] $(basename "$src"): already HEVC, skip"; return 0 ;;
    esac
    tmp="$src.transcoding.mp4"
    if ffmpeg -hide_banner -loglevel error -nostdin -y \
            -i "$src" \
            -c:v libx265 -preset "$X265_PRESET" -crf "$X265_CRF" -tag:v hvc1 \
            -c:a copy -movflags +faststart \
            "$tmp"; then
        # Preserve mtime so the manifest's saved_at ordering is unaffected.
        touch -r "$src" "$tmp" 2>/dev/null || :
        mv "$tmp" "$src"
        echo "[x265] $(basename "$src"): H.264 -> HEVC done"
    else
        rm -f "$tmp"
        echo "[x265] $(basename "$src"): FAILED (H.264 source preserved)" >&2
        return 1
    fi
}

# transcode_one_per_id <id>
#   After yt-dlp writes <id>.mp4, transcode it if HEVC_TRANSCODE=1.
transcode_one_per_id() {
    id="$1"
    [ "$HEVC_TRANSCODE" = 1 ] || return 0
    transcoded_path "$DIR/$id.mp4"
}

if [ ! -f "$CHANNELS" ]; then
  if [ "$SOURCE" = files ]; then
    # one channel, no playlist: it airs every file
    printf '[\n  { "num": 1, "name": "01", "playlist": "" }\n]\n' > "$CHANNELS"
  else
    cp /app/channels.default.json "$CHANNELS"
  fi
fi || { echo "[sync] cannot write $CHANNELS - is /config writable?" >&2; exit 1; }

# fetch <folder> <playlist>...
#   Download playlists (URL or id) into $DIR/<folder> ("" = $DIR itself).
#   Additive only: a per-folder download archive skips what's already been
#   fetched, even if you've since deleted the file.
fetch() {
  folder="$1"; shift
  case "$folder" in
    */*|.*) echo "[fetch] bad folder '$folder' (one plain name, no dots first)" >&2; return 1 ;;
  esac
  dest="$DIR${folder:+/$folder}"
  mkdir -p "$dest" || return 1
  for pl in "$@"; do
    case "$pl" in
      http*) url="$pl" ;;
      *) url="https://www.youtube.com/playlist?list=$pl" ;;
    esac
    echo "[fetch] $pl -> $dest"
    # partial downloads live in /tmp, so a half-fetched video never shows up
    # in $DIR; the info.json gives files mode (and mediaDb) the title
    yt-dlp -f "$FORMAT" --merge-output-format mp4 \
      -P "$dest" -P temp:/tmp/mtv-fetch -o "%(id)s.%(ext)s" \
      --write-info-json --no-write-playlist-metafiles \
      --download-archive "$dest/.archive" \
      --no-progress --ignore-errors "$url" < /dev/null
  done
  if [ "$HEVC_TRANSCODE" = 1 ]; then
    for f in "$dest"/*.mp4; do
      [ -e "$f" ] && transcoded_path "$f"
    done
  fi
  return 0
}

# fetch everything in playlists.txt: one "PLAYLIST [FOLDER]" per line
fetch_list() {
  [ -f "$PLAYLISTS" ] || return 0
  grep -v -e '^[[:space:]]*#' -e '^[[:space:]]*$' "$PLAYLISTS" |
    while read -r pl folder _rest; do
      fetch "$folder" "$pl"
    done
}

files_manifest() {
  python3 "$(dirname "$0")/files_manifest.py" "$DIR" "$CHANNELS"
}

# --- one-shot: transcode-library -----------------------------------------
# Walks every local mp4 and transcodes any that are still H.264 (idempotent:
# already-HEVC files are skipped via ffprobe). Useful after switching the
# library to HEVC for the Pi5 client. Prints progress, exits non-zero if any
# file failed (so callers can tell partial success apart from full).
if [ "${1:-}" = "transcode-library" ]; then
    n=0; failed=0
    for f in "$DIR"/*.mp4; do
        [ -e "$f" ] || continue
        n=$((n + 1))
        transcoded_path "$f" || failed=$((failed + 1))
    done
    echo "[x265] transcode-library: $((n - failed))/$n succeeded"
    [ "$failed" = 0 ]
    exit $?
fi

# --- one-shot: fetch -------------------------------------------------------
if [ "${1:-}" = "fetch" ]; then
  shift
  folder=""
  if [ "${1:-}" = "--into" ]; then
    folder="${2:-}"
    shift 2 || exit 2
  fi
  if [ $# = 0 ]; then
    [ -f "$PLAYLISTS" ] || { echo "[fetch] no playlists given and no $PLAYLISTS" >&2; exit 2; }
    fetch_list
  else
    fetch "$folder" "$@"
  fi
  [ "$SOURCE" = files ] && files_manifest
  exit 0
fi

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
  MEDIADB_URL="$MEDIADB_URL" CHANNELS="$CHANNELS" python3 - "$DIR" "$@" <<'EOF'
import fcntl, json, os, subprocess, sys
from urllib.parse import quote, urlencode
from urllib.request import urlopen

d = sys.argv[1]
# the pass and the playlist watcher (below) both publish; one writer at a time
# so manifest.json and the durations cache never interleave. Held until exit.
lock = open(os.path.join(d, ".manifest.lock"), "w")
fcntl.flock(lock, fcntl.LOCK_EX)
cachep = os.path.join(d, ".durations.json")
try:
    cache = json.load(open(cachep))
except (OSError, ValueError):
    cache = {}

def youtube_id(item):
    detail = item.get("metadata") or {}
    return detail.get("source_id") or (item.get("external_ids") or {}).get("youtube")

def mediadb(path):
    with urlopen(base_url + path, timeout=5) as response:
        return json.load(response)

metadata = {}
base_url = os.environ.get("MEDIADB_URL", "").rstrip("/")
if base_url:
    try:
        offset = 0
        while True:
            query = urlencode({"type": "music-video", "limit": 500, "offset": offset})
            page = mediadb(f"/api/items?{query}")
            items = page.get("items", [])
            for item in items:
                if item.get("enrichment_status") != "enriched":
                    continue
                detail = item.get("metadata") or {}
                source_id = youtube_id(item)
                if source_id:
                    metadata[str(source_id)] = {
                        "artist": detail.get("artist"),
                        "track": item.get("title"),
                        "album": detail.get("album"),
                        "year": item.get("year"),
                        "featured": detail.get("featured_artists"),
                        # yt-dlp's category "Music" isn't a genre
                        "genre": [g for g in detail.get("genre") or [] if g.casefold() != "music"],
                        "release_type": detail.get("release_type"),
                    }
            offset += len(items)
            if not items or offset >= page.get("total", 0):
                break
        print(f"[sync] mediadb: metadata for {len(metadata)} music videos")
    except Exception as error:
        print(f"[sync] mediadb unavailable, using playlist titles: {error}", file=sys.stderr)

def write_sidecar(vid, title, channel):
    # mediaDb reads <id>.info.json before asking YouTube, which now demands
    # sign-in from the homelab. The playlist listing already has the title
    # ("Artist - Song") and channel, so hand them over. Never overwrite a
    # sidecar we didn't write (a full yt-dlp --write-info-json is richer).
    if not title or title == "NA":
        return
    data = {"id": vid, "title": title, "_source": "mtv-playlist"}
    if channel and channel != "NA":
        data["channel"] = channel
    path = os.path.join(d, vid + ".info.json")
    try:
        with open(path, encoding="utf-8") as f:
            existing = json.load(f)
        if existing.get("_source") != "mtv-playlist" or existing == data:
            return
    except (OSError, ValueError):
        pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


videos = {}    # id -> {id, title, duration}
channels = {}  # channel num -> [ids]
for tsv in sys.argv[2:]:
    num = os.path.basename(tsv).split(".")[1]   # pl.<num>.tsv
    ids = channels.setdefault(num, [])
    seen = set(ids)   # a playlist can contain the same video twice
    for line in open(tsv, encoding="utf-8"):
        vid, title, channel = (line.rstrip("\n").split("\t") + ["", ""])[:3]
        path = os.path.join(d, vid + ".mp4")
        if not vid or not os.path.exists(path):
            continue
        write_sidecar(vid, title, channel)
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
            videos[vid].update({key: value for key, value in metadata.get(vid, {}).items() if value})
        if vid in videos and vid not in seen:
            seen.add(vid)
            ids.append(vid)
json.dump(cache, open(cachep + ".tmp", "w"))
os.replace(cachep + ".tmp", cachep)

# Every mediaDb playlist is a channel too, airing its videos that are local.
# They're listed in the manifest's "lineup" and numbered after the admin
# lineup (channels.json). A playlist keeps its number from the previous
# manifest, so a viewer's remembered channel doesn't drift as playlists come
# and go. "source" playlists are skipped: they mirror a YouTube playlist that
# a channels.json channel already airs.
try:
    previous = json.load(open(os.path.join(d, "manifest.json"), encoding="utf-8"))
except (OSError, ValueError):
    previous = {}
if not isinstance(previous, dict):   # the old flat-array manifest
    previous = {}
playlists = None   # stays None if mediaDb is unreachable: keep last pass's channels
if base_url:
    try:
        playlists = []
        for p in mediadb("/api/playlists").get("playlists", []):
            if p.get("kind") == "source":
                continue
            items = mediadb("/api/playlists/" + quote(p["slug"], safe="")).get("items", [])
            playlists.append((p["slug"], p["name"], [youtube_id(item) for item in items]))
    except Exception as error:
        playlists = None
        print(f"[sync] mediadb playlists unavailable, keeping last channels: {error}", file=sys.stderr)
if playlists is None:
    playlists = [(c["slug"], c["name"], (previous.get("channels") or {}).get(str(c["num"]), []))
                 for c in previous.get("lineup") or []]
try:
    taken = {c["num"] for c in json.load(open(os.environ["CHANNELS"])) if isinstance(c, dict)}
except (OSError, ValueError, KeyError, TypeError):
    taken = set()
taken |= {int(num) for num in channels}
sticky = {c["slug"]: c["num"] for c in previous.get("lineup") or []}
lineup = []
for slug, name, ids in playlists:
    ids = list(dict.fromkeys(i for i in ids if i in videos))
    if not ids:
        continue
    num = sticky.get(slug)
    if num is None or num in taken:
        num = max(taken | set(sticky.values()), default=0) + 1
    taken.add(num)
    lineup.append({"num": num, "name": name, "slug": slug})
    channels[str(num)] = ids
lineup.sort(key=lambda c: c["num"])

tmp = os.path.join(d, ".manifest.tmp")
with open(tmp, "w", encoding="utf-8") as f:
    json.dump({"videos": list(videos.values()), "channels": channels, "lineup": lineup}, f)
os.replace(tmp, os.path.join(d, "manifest.json"))
print(f"[sync] manifest: {len(videos)} local videos across {len(channels)} channels"
      f" ({len(lineup)} from mediaDb playlists)")
EOF
}

# Playlist channels come from mediaDb at manifest time, which used to mean
# waiting for a pass (6h, or hours while a transcode runs). Watch the
# playlist list on a short timer instead and republish the manifest when it
# changes: new, edited and deleted playlists are on air within a minute.
# Downloads and pruning still belong to the pass. The manifest is cheap to
# redo (durations cached, sidecars skipped when unchanged).
playlists_fingerprint() {
  python3 - "$MEDIADB_URL" <<'EOF'
import hashlib, sys
from urllib.request import urlopen
with urlopen(sys.argv[1].rstrip("/") + "/api/playlists", timeout=5) as response:
    print(hashlib.sha256(response.read()).hexdigest())
EOF
}

watch_playlists() {
  echo "[sync] watching mediadb playlists every ${PLAYLIST_POLL}s"
  last=""
  while :; do
    sleep "$PLAYLIST_POLL"
    now=$(playlists_fingerprint 2>/dev/null) || continue
    [ "$now" = "$last" ] && continue
    [ -n "$last" ] && echo "[sync] mediadb playlists changed, refreshing the manifest"
    manifest && last="$now"
  done
}

# sleep SYNC_INTERVAL, or until the admin page touches the trigger
nap() {
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
}

if [ "$SOURCE" = files ]; then
  echo "[sync] files mode: airing the mp4s in $DIR"
  while :; do
    files_manifest
    if [ -f "$PLAYLISTS" ]; then
      fetch_list
      files_manifest
    fi
    nap
  done
fi

[ -n "$MEDIADB_URL" ] && watch_playlists &

while :; do
  ok=1
  lineup > /tmp/lineup.tsv
  while IFS="$TAB" read -r num pl; do
    if yt-dlp --flat-playlist --print "%(id)s	%(title)s	%(channel)s" \
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
      -f "$FORMAT" \
      --merge-output-format mp4 \
      -o "$DIR/%(id)s.%(ext)s" \
      --download-archive "$ARCHIVE" \
      --no-progress --ignore-errors \
      -a /tmp/dl.txt
    # Transcode every freshly-downloaded H.264 file to HEVC. Idempotent: files
    # that are already HEVC from a prior pass are skipped (probed by
    # transcoded_path). Runs after yt-dlp so a transient download error leaves
    # the source untouched — the H.264 stays in place until a clean retry.
    if [ "$HEVC_TRANSCODE" = 1 ]; then
      while IFS="$TAB" read -r id _rest; do
        [ -n "$id" ] || continue
        transcode_one_per_id "$id"
      done < "$UNION"
    fi

    if [ "$ok" = 1 ]; then
      for f in "$DIR"/*.mp4; do
        [ -e "$f" ] || continue
        id=$(basename "$f" .mp4)
        if ! grep -q "^$id$TAB" "$UNION"; then
          echo "[sync] pruning $id (in no channel playlist)"
          rm -f "$f" "$DIR/$id.info.json"
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
  nap
done
