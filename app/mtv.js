/* MTV — a broadcast channel, not a video site. Every viewer sees the same
   thing at the same moment: the schedule is deterministic (seeded shuffle of
   the synced library) and "what's on now" is wall-clock math — no backend,
   no shared state. Nothing streams while nobody watches; the schedule just
   marches on paper and you rejoin it live.
   Local files come from the mtv-sync mirror (/videos/); a YouTube-iframe
   fallback covers the window before the first sync produces a manifest. */

(function () {
  "use strict";

  var deck = null; // backend: {next, ch, toggleMute (→muted), pause, resume, ...}
  // skip is remote-holder-only: open the page as /#remote to get it
  var CAN_SKIP = location.hash === "#remote";
  var muted = true;
  var osdTimer = null;
  var padsTimer = null;
  var creditsTimer = null;
  var credits = null;

  var $ = function (id) { return document.getElementById(id); };

  // ---- channel lineup (channels.json) ----
  // channel choice is per-device, like a real TV: remembered across reloads,
  // but the broadcast on each channel is the same for every viewer
  var LINEUP = [{ num: 1, name: "", playlist: "" }];
  var chIdx = 0;

  function currentCh() { return LINEUP[chIdx]; }
  function chLabel() {
    var c = currentCh();
    return "CH " + String(c.num).padStart(2, "0");
  }
  function saveCh() {
    try { localStorage.setItem("mtv-channel", String(currentCh().num)); } catch (e) {}
  }
  function restoreCh() {
    var n = null;
    try { n = localStorage.getItem("mtv-channel"); } catch (e) {}
    for (var i = 0; i < LINEUP.length; i++) {
      if (String(LINEUP[i].num) === n) { chIdx = i; return; }
    }
  }
  // next lineup index in direction d whose channel satisfies `usable`;
  // stays put if no other channel qualifies
  function scanCh(d, usable) {
    var n = LINEUP.length;
    for (var s = 1; s < n; s++) {
      var j = (((chIdx + d * s) % n) + n) % n;
      if (usable(j)) return j;
    }
    return chIdx;
  }

  function shuffle(a) {
    a = a.slice();
    for (var i = a.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = a[i]; a[i] = a[j]; a[j] = t;
    }
    return a;
  }

  // Broadcast credits use explicit music metadata when available. Older
  // manifests only have YouTube titles, usually "Artist - Song". Never
  // invent an artist when the title has no clear separator.
  function creditText(item) {
    var raw = typeof item === "string" ? item : (item && item.title) || "";
    var clean = raw.replace(/\s*[\[(](?:(?:official\s+)?(?:music\s+|lyric\s+)?video|official\s+audio|visuali[sz]er|lyrics?)[\])]\s*$/i, "").trim();
    var split = clean.match(/^(.+?)\s+[-–—]\s+(.+)$/);
    var artist = (item && typeof item === "object" && item.artist) || (split ? split[1] : "");
    var song = (item && typeof item === "object" && item.track) || (split ? split[2] : clean);
    return { artist: artist, song: song.replace(/^["“](.*)["”]$/, "$1") };
  }

  function showCredits() {
    if (!credits || !credits.song) return;
    $("track-artist").textContent = credits.artist;
    $("track-artist").hidden = !credits.artist;
    $("track-song").textContent = "“" + credits.song + "”";
    $("osd-track").classList.add("show");
    clearTimeout(creditsTimer);
    creditsTimer = setTimeout(function () { $("osd-track").classList.remove("show"); }, 8000);
  }

  function clearCredits() {
    clearTimeout(creditsTimer);
    credits = null;
    $("osd-track").classList.remove("show");
  }

  // ---- shared chrome ----
  function showOsd(title) {
    $("osd-ch").textContent = chLabel();
    $("osd-ch").classList.add("show");
    if (title) {
      credits = creditText(title);
    }
    showCredits();
    $("osd-mute").classList.toggle("show", muted);
    clearTimeout(osdTimer);
    osdTimer = setTimeout(function () {
      $("osd-ch").classList.remove("show");
    }, 3500);
  }

  function staticBurst(ms) {
    var el = $("static");
    el.classList.add("on");
    setTimeout(function () { el.classList.remove("on"); }, ms || 350);
  }

  function showPads() {
    $("pads").classList.add("show");
    $("pads").inert = false;
    $("pads").setAttribute("aria-hidden", "false");
    clearTimeout(padsTimer);
    padsTimer = setTimeout(function () {
      $("pads").classList.remove("show");
      $("pads").inert = true;
      $("pads").setAttribute("aria-hidden", "true");
    }, 4000);
  }

  function skip() {
    if (!deck || !CAN_SKIP) return;
    staticBurst(250);
    $("osd-ch").textContent = "▶▶";
    $("osd-ch").classList.add("show");
    deck.next();
  }

  // ---- local backend: deterministic broadcast over the synced files ----
  function startLocal(manifest0) {
    var EPOCH = 365472000; // 1981-08-01, MTV sign-on. Any fixed constant works.
    var SEED = 1981;

    // identical library → identical schedule on every device: sort by id for
    // a canonical base order, then shuffle with a fixed-seed PRNG
    function mulberry32(a) {
      return function () {
        a |= 0; a = (a + 0x6D2B79F5) | 0;
        var t = Math.imul(a ^ (a >>> 15), 1 | a);
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
      };
    }

    var lib = [];      // current channel's scheduled items, deterministic order
    var bad = {};      // ids that failed to play this session
    var vids = [];     // full local library (all channels)
    var chans = null;  // channel num -> [ids]; null = pre-channels manifest
    // accepts both manifest shapes: the old flat array and the new
    // {videos, channels} — with the old one every channel airs the full library
    function adopt(m) {
      vids = m.videos || m;
      chans = m.channels || null;
      buildLib();
    }
    function libFor(i) {
      var ids = chans && chans[String(LINEUP[i].num)];
      return vids.filter(function (it) {
        return it.duration > 0 && (!ids || ids.indexOf(it.id) !== -1);
      });
    }
    function buildLib() {
      var items = libFor(chIdx);
      items.sort(function (a, b) { return a.id < b.id ? -1 : 1; });
      // per-channel seed: each channel gets its own schedule, still identical
      // on every device
      var rnd = mulberry32(SEED + currentCh().num);
      for (var i = items.length - 1; i > 0; i--) {
        var j = Math.floor(rnd() * (i + 1));
        var t = items[i]; items[i] = items[j]; items[j] = t;
      }
      lib = items;
    }
    adopt(manifest0);
    // a remembered channel can be empty (playlist not filled in yet, or its
    // videos not downloaded yet) — fall to the nearest channel with content
    if (!lib.length) {
      chIdx = scanCh(1, function (j) { return libFor(j).length > 0; });
      saveCh();
      buildLib();
    }

    function playable() {
      return lib.filter(function (it) { return !bad[it.id]; });
    }
    // what the channel is showing at this wall-clock moment
    function onAir() {
      var l = playable();
      var total = l.reduce(function (s, it) { return s + it.duration; }, 0);
      if (!total) return null;
      var off = (Date.now() / 1000 - EPOCH) % total;
      for (var i = 0; i < l.length; i++) {
        if (off < l[i].duration) return { it: l[i], off: off };
        off -= l[i].duration;
      }
      return { it: l[0], off: 0 };
    }
    // the library grows as mtv-sync downloads; refetch so all viewers converge
    function refreshLib(done) {
      fetch("/videos/manifest.json", { cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .catch(function () { return null; })
        .then(function (m) { if (m && (m.videos || m).length) adopt(m); done(); });
    }

    // double-buffer: `active` is on screen, `standby` silently preloads the
    // next scheduled video so the boundary swap is gapless. AirPlay/PiP pin
    // their session to one element, so in detached mode we never swap and
    // fall back to src-replacement on the active element.
    function makeScreen(first) {
      var v = document.createElement("video");
      v.className = "screen" + (first ? " on" : "");
      // real attributes, not just JS properties — iOS Safari's autoplay policy
      // only honors the attribute form; property-only muted stalls the video
      if (first) v.setAttribute("autoplay", "");
      v.setAttribute("muted", "");
      v.setAttribute("playsinline", "");
      v.muted = true;
      v.preload = "auto";
      v.setAttribute("x-webkit-airplay", "allow");
      return v;
    }
    var active = makeScreen(true);
    var standby = makeScreen(false);
    var holder = $("player");
    holder.replaceWith(active);
    active.parentNode.insertBefore(standby, active);
    $("boot").remove();
    $("pad-pip").hidden = !(document.pictureInPictureEnabled || (active.webkitSupportsPresentationMode && active.webkitSupportsPresentationMode("picture-in-picture")));
    $("pad-cast").hidden = !active.webkitShowPlaybackTargetPicker;

    var currentId = null;
    var outroShown = false;
    var prep = null; // id currently loaded into standby
    // #remote skip puts this screen on a "detour": it follows the schedule
    // in order, each video from the top, ignoring the live clock. Rejoins
    // the live broadcast on reload or when the tab comes back to front.
    var detour = false;

    function kickPlay(v) {
      // during AirPlay a src swap can get the immediate play() rejected;
      // retry once the video is actually playable
      var p = v.play();
      if (p && p.catch) {
        p.catch(function () {
          v.addEventListener("canplay", function once() {
            v.removeEventListener("canplay", once);
            v.play().catch(function () {});
          });
        });
      }
    }

    function playItem(it, off) {
      currentId = it.id;
      outroShown = false;
      prep = null;
      active.src = "/videos/" + it.id + ".mp4";
      active.load();
      if (off > 1) {
        active.addEventListener("loadedmetadata", function seek() {
          active.removeEventListener("loadedmetadata", seek);
          if (off < active.duration - 2) active.currentTime = off;
        });
      }
      kickPlay(active);
      showOsd(it);
    }

    // join the broadcast at its live position
    function tune() {
      var slot = onAir();
      if (!slot) return;
      if (slot.it.id !== currentId) {
        playItem(slot.it, slot.off);
      } else {
        // same video: nudge only if we've drifted well off the live point
        if (Math.abs(active.currentTime - slot.off) > 10) active.currentTime = slot.off;
        active.play().catch(function () {});
      }
    }

    function isDetached() {
      return active.webkitPresentationMode === "picture-in-picture"
        || document.pictureInPictureElement === active
        || !!active.webkitCurrentPlaybackTargetIsWireless;
    }

    // the video scheduled after the one airing now
    function expectedNext() {
      var l = playable();
      if (!l.length) return null;
      for (var i = 0; i < l.length; i++) {
        if (l[i].id === currentId) return l[(i + 1) % l.length];
      }
      return l[0];
    }

    function maybePreload() {
      if (prep || isDetached()) return;
      if (!active.duration || active.duration - active.currentTime > 20) return;
      var nx = expectedNext();
      if (!nx || nx.id === currentId) return;
      prep = nx.id;
      standby.src = "/videos/" + nx.id + ".mp4";
      standby.load();
    }

    function swapToPreloaded() {
      var old = active;
      active = standby;
      standby = old;
      active.muted = old.muted;
      active.classList.add("on");
      old.classList.remove("on");
      old.pause();
      currentId = prep;
      outroShown = false;
      prep = null;
      var nowPlaying = null;
      playable().forEach(function (it) { if (it.id === currentId) nowPlaying = it; });
      kickPlay(active);
      showOsd(nowPlaying);
      refreshLib(function () {}); // keep the library fresh in the background
    }

    function onScreenEvent(v, type, fn) { v.addEventListener(type, fn); }
    [active, standby].forEach(function (v) {
      onScreenEvent(v, "ended", function (e) {
        if (e.target !== active) return;
        staticBurst(250);
        // AirPlay/PiP: any dead air between videos (like an async manifest
        // refetch) makes iOS tear the session down — chain the next video
        // synchronously, from the top (no seek, one less thing to upset it).
        // A skip detour advances the same way: next in order, from the top.
        if (isDetached() || detour) {
          var nx = expectedNext();
          if (nx) { playItem(nx, 0); return; }
        }
        // gapless path only when the preloaded video is still what's due next
        if (!isDetached() && prep && expectedNext() && prep === expectedNext().id) {
          swapToPreloaded();
        } else {
          refreshLib(tune); // rejoins the schedule — also after a #remote detour
        }
      });
      onScreenEvent(v, "timeupdate", function (e) {
        if (e.target !== active) return;
        maybePreload();
        // Bring back the corner credits as the video signs off.
        if (!outroShown && active.duration > 30 && active.currentTime >= active.duration - 12) {
          outroShown = true;
          showCredits();
        }
      });
      // advance past genuinely broken files only — a src swap aborts the
      // previous load and that abort also lands here
      onScreenEvent(v, "error", function (e) {
        if (v.error && v.error.code === 1) return; // MEDIA_ERR_ABORTED
        if (e.target !== active) { prep = null; return; } // bad preload: just discard
        var now = Date.now();
        if (now - lastErrAdvance < 1500) return;
        lastErrAdvance = now;
        if (currentId) bad[currentId] = 1;
        tune();
      });
    });
    var lastErrAdvance = 0;

    deck = {
      // #remote skip: start (or continue) the detour — next scheduled video,
      // from the top. No snapping back mid-video afterwards.
      next: function () {
        var nx = expectedNext();
        if (!nx) return;
        detour = true;
        playItem(nx, 0);
      },
      // channel up/down: retune to that channel's live broadcast position.
      // Empty channels are skipped; a skip detour ends — changing channel
      // always lands you on the new channel's live clock.
      ch: function (d) {
        var j = scanCh(d, function (i) { return libFor(i).length > 0; });
        if (j === chIdx) return;
        chIdx = j;
        saveCh();
        detour = false;
        currentId = null; // force a retune even if the same video is on
        prep = null;
        staticBurst(350);
        buildLib();
        tune();
      },
      toggleMute: function () { active.muted = !active.muted; return active.muted; },
      pause: function () { active.pause(); },
      // a plain tap must never yank the channel — only restart a paused video.
      // Returning to the tab (visibilitychange) ends any detour and rejoins live.
      resume: function (fromVisibility) {
        // a detour (skip) must survive app-switching — rejoining the live
        // clock happens only on a fresh page load, never behind your back
        if (fromVisibility && !detour) { refreshLib(tune); return; }
        if (active.paused) kickPlay(active);
      },
      fullscreen: function () {
        // iPhone Safari only allows fullscreen on the <video> itself
        if (document.documentElement.requestFullscreen) {
          document.documentElement.requestFullscreen().catch(function () {});
        } else if (active.webkitEnterFullscreen) {
          active.webkitEnterFullscreen();
        }
      },
      pip: function () {
        if (active.webkitSupportsPresentationMode
            && active.webkitSupportsPresentationMode("picture-in-picture")) {
          active.webkitSetPresentationMode(
            active.webkitPresentationMode === "picture-in-picture" ? "inline" : "picture-in-picture");
        } else if (document.pictureInPictureElement) {
          document.exitPictureInPicture().catch(function () {});
        } else if (active.requestPictureInPicture) {
          active.requestPictureInPicture().catch(function () {});
        }
      },
      cast: function () {
        // real AirPlay (stream handoff, not screen mirroring) — survives
        // leaving Safari for another app
        if (active.webkitShowPlaybackTargetPicker) active.webkitShowPlaybackTargetPicker();
      },
      detached: isDetached,
    };
    tune();
  }

  // ---- fallback backend: YouTube iframe (pre-first-sync only) ----
  function startYouTube() {
    var player = null;
    var queue = null; // harvested + shuffled playlist ids; null while bootstrapping
    var cursor = 0;
    var videoId = null;
    var outroShown = false;

    // a remembered channel may have no playlist configured yet
    if (!currentCh().playlist) {
      chIdx = scanCh(1, function (j) { return !!LINEUP[j].playlist; });
      saveCh();
    }

    function play() {
      if (queue) {
        player.loadVideoById(queue[cursor % queue.length]);
      } else {
        player.loadPlaylist({ listType: "playlist", list: currentCh().playlist });
      }
    }
    // the iframe API's setShuffle() is unreliable: load the playlist once to
    // harvest its ids (getPlaylist), then drive our own shuffled queue
    function harvest() {
      var ids = player.getPlaylist();
      if (!ids || !ids.length) return; // not ready yet, retry on next PLAYING
      queue = shuffle(ids);
      cursor = 0;
      player.loadVideoById(queue[0]);
    }

    window.onYouTubeIframeAPIReady = function () {
      player = new YT.Player("player", {
        width: "100%",
        height: "100%",
        playerVars: {
          autoplay: 1, mute: 1, controls: 0, disablekb: 1, fs: 0,
          iv_load_policy: 3, modestbranding: 1, rel: 0, playsinline: 1,
        },
        events: {
          onReady: function () {
            $("boot").remove();
            deck = {
              next: function () {
                if (!queue) { player.nextVideo(); return; }
                cursor++; play();
              },
              ch: function (d) {
                var j = scanCh(d, function (i) { return !!LINEUP[i].playlist; });
                if (j === chIdx) return;
                chIdx = j;
                saveCh();
                staticBurst(350);
                queue = null; // re-harvest + reshuffle the new playlist
                cursor = 0;
                videoId = null;
                clearCredits();
                play();
                showOsd(null);
              },
              toggleMute: function () {
                if (player.isMuted()) { player.unMute(); player.setVolume(100); return false; }
                player.mute(); return true;
              },
              pause: function () { player.pauseVideo(); },
              resume: function () { player.playVideo(); },
              fullscreen: function () {
                if (document.documentElement.requestFullscreen) {
                  document.documentElement.requestFullscreen().catch(function () {});
                }
              },
            };
            play();
            showOsd(null);
          },
          onStateChange: function (e) {
            if (e.data === YT.PlayerState.ENDED) {
              staticBurst(250);
              if (queue) { cursor++; play(); } // native playlist advances itself while bootstrapping
            } else if (e.data === YT.PlayerState.PLAYING) {
              if (!queue) harvest();
              var d = player.getVideoData();
              var id = d && (d.video_id || d.title);
              if (id && id !== videoId) {
                videoId = id;
                outroShown = false;
                showOsd(d.title);
              }
            }
          },
          // 2/5: player error; 100/101/150: removed or not embeddable
          onError: function () { deck && deck.next(); },
        },
      });
    };
    var tag = document.createElement("script");
    tag.src = "https://www.youtube.com/iframe_api";
    document.head.appendChild(tag);
    setInterval(function () {
      if (!player || !videoId || outroShown || document.hidden) return;
      var duration = player.getDuration();
      if (duration > 30 && player.getCurrentTime() >= duration - 12) {
        outroShown = true;
        showCredits();
      }
    }, 1000);
  }

  // ---- pick backend ----
  function getJson(url) {
    return fetch(url, { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }
  Promise.all([getJson("channels.json"), getJson("/videos/manifest.json")])
    .then(function (rs) {
      if (rs[0] && rs[0].length) LINEUP = rs[0];
      restoreCh();
      saveCh();
      $("pad-chup").hidden = LINEUP.length < 2;
      $("pad-chdn").hidden = LINEUP.length < 2;
      var m = rs[1];
      var vids = m && (m.videos || m);
      var withDur = vids && vids.filter(function (it) { return it.duration > 0; });
      if (withDur && withDur.length) { startLocal(m); } else { startYouTube(); }
    });

  // ---- remote control ----
  if (!CAN_SKIP) $("pad-skip").style.display = "none";
  $("catcher").addEventListener("click", function () {
    if (!deck) return;
    if (muted) {
      muted = deck.toggleMute();
      $("osd-mute").classList.toggle("show", muted);
      $("catcher").setAttribute("aria-label", "Show song credits and TV controls");
    }
    showOsd(null);
    // the tap is a user gesture: also kick playback, so a device that refused
    // autoplay (iOS Low Power Mode etc.) starts on the same press that unmutes
    deck.resume();
    showPads();
  });
  function chStep(d) { if (deck && deck.ch) deck.ch(d); }
  $("pad-skip").addEventListener("click", function (e) { e.stopPropagation(); skip(); showPads(); });
  $("pad-chup").addEventListener("click", function (e) { e.stopPropagation(); chStep(1); showPads(); });
  $("pad-chdn").addEventListener("click", function (e) { e.stopPropagation(); chStep(-1); showPads(); });
  $("pad-full").addEventListener("click", function (e) { e.stopPropagation(); deck && deck.fullscreen(); });
  $("pad-pip").addEventListener("click", function (e) { e.stopPropagation(); deck && deck.pip && deck.pip(); });
  $("pad-cast").addEventListener("click", function (e) { e.stopPropagation(); deck && deck.cast && deck.cast(); });
  $("catcher").addEventListener("dblclick", function () { deck && deck.fullscreen(); });
  document.addEventListener("keydown", function (e) {
    if (e.altKey || e.ctrlKey || e.metaKey || e.target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
    if (e.key === "ArrowRight") { e.preventDefault(); skip(); }
    if (e.key === "ArrowUp" && LINEUP.length > 1) { e.preventDefault(); chStep(1); }
    if (e.key === "ArrowDown" && LINEUP.length > 1) { e.preventDefault(); chStep(-1); }
    if (e.key.toLowerCase() === "f") { e.preventDefault(); if (deck) deck.fullscreen(); }
    if (e.key.toLowerCase() === "m" && deck) {
      e.preventDefault();
      muted = deck.toggleMute();
      $("osd-mute").classList.toggle("show", muted);
      $("catcher").setAttribute("aria-label", muted ? "Turn on sound and show TV controls" : "Show song credits and TV controls");
    }
  });

  // only run the stream when someone is actually watching: a hidden tab
  // (switched app, TV browser in the background) pauses, coming back rejoins
  // the live broadcast. EXCEPT when the video is detached (PiP / AirPlay) —
  // that's the one case where leaving the page and keeping the stream going
  // is the whole point
  document.addEventListener("visibilitychange", function () {
    if (!deck) return;
    if (deck.detached && deck.detached()) return;
    if (document.hidden) { deck.pause(); } else { deck.resume(true); }
  });
})();
