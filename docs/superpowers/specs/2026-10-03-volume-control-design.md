# Volume control for the MTV web player

Date: 2026-10-03. Repo: `alex/mtv`, files `app/mtv.js`, `app/index.html`.

## Goal

Viewers can turn the sound up and down from the on-screen TV controls and the
keyboard, in both playback backends, and the level survives a reload on that
device. Today the player only has mute/unmute.

## Non-goals

- No slider, no fine-grained control: 10% steps only.
- No change to the mute semantics ("MUTED / TAP FOR SOUND" on first load).
- No admin, sync, or Pi (outpost) changes. TV-set volume via CEC is a
  separate project.

## Design

### State

- `volume`: integer 0–100, module-level in `mtv.js`, default 100.
- Loaded once at startup from `localStorage["mtv-volume"]`, parsed with
  `parseInt`, clamped to 0–100; anything unparsable falls back to 100.
  Saved on every change. Access is wrapped in `try/catch` exactly like the
  existing `mtv-channel` key (private mode can throw).
- `muted` is unchanged and still not persisted.

### Deck interface

Both backends gain one method:

- `vol(level)` — apply `level` (0–100) to the currently playing element.

Local backend (`startLocal`):

- `vol(level)`: `active.volume = level / 100`.
- `swapToPreloaded()` copies the level to the incoming element next to the
  existing `active.muted = old.muted` line, so a track change keeps the level.
- `makeScreen()` creates elements at `volume / 100` so the first video starts
  at the stored level.

YouTube backend:

- `vol(level)`: `if (!player.isMuted()) player.setVolume(level)`. The iframe
  API does not document whether `setVolume` implicitly unmutes, so never call
  it while muted; the stored level is applied at unmute instead.
- `toggleMute()`'s unmute branch calls `player.setVolume(volume)` instead of
  the hard-coded 100. That covers first unmute, so `onReady` needs no change.

### Controls

- Two pads in the `#pads` strip, after SKIP and before FULL:
  `<button id="pad-voldn" aria-label="volume down">VOL −</button>` and
  `<button id="pad-volup" aria-label="volume up">VOL +</button>`.
  Styling is inherited from the existing `#pads button` rule; no new CSS.
- Keys: `-` lowers, `+` and `=` raise. Same guard as the other keys (ignored
  with modifiers or in editable fields).
- Keys call `e.preventDefault()` like the other handled keys.
- `volStep(d)` in the shared chrome section:
  1. `volume = Math.max(0, Math.min(100, volume + 10 * d))` (no clamp helper
     exists; inline it), save to localStorage.
  2. If `d > 0` and `muted`, run the same unmute path as the sound tap
     (`muted = deck.toggleMute()`, update `#osd-mute` and the catcher
     aria-label). This runs before step 3 so the YouTube `vol` guard sees
     the player unmuted.
  3. `deck.vol(volume)`.
  4. Show the level in `#osd-ch`: set `textContent` to `VOL ` followed by ten
     blocks (`▮` per filled 10%, `▯` for the rest), add `show`,
     `clearTimeout(osdTimer)`, `osdTimer = setTimeout(remove show, 3500)`.
     `showOsd()` cannot be reused because it overwrites the text with the
     channel label.
- `volStep` has no pad chrome in it. The two pad click handlers call
  `e.stopPropagation()`, `volStep(±1)`, `showPads()`, mirroring the CH pads;
  the keys call `volStep` only, like ArrowUp/Down.
- Before `deck` exists, presses are ignored (same as `chStep`).
- Intended edge behaviour, not to be "fixed": VOL + at 100 while muted still
  unmutes (level unchanged). Volume 0 while unmuted is silent with no MUTED
  overlay, and a tap does nothing audible; that follows from the mute
  semantics being untouched.

### iOS

iOS Safari ignores `HTMLMediaElement.volume` and the YouTube iframe's
`setVolume`; hardware buttons are the only volume control there.

- `IOS = /iP(hone|ad|od)/.test(navigator.platform) || (navigator.platform ===
  "MacIntel" && navigator.maxTouchPoints > 1)` (the second clause is iPadOS
  pretending to be a Mac).
- When `IOS` is true, both VOL pads get the `hidden` attribute at startup
  (module-level, next to the existing `pad-skip` display rule in the remote
  control section) and `volStep` returns immediately. Everything else is
  unchanged.

### Cache

`index.html` loads `mtv.js?v=10`; bump to `v=11`.

## Error handling

- `localStorage` unavailable: swallowed, volume works for the session only.
- `deck.vol` missing (should not happen, both backends implement it): guarded
  with `deck.vol &&` like `deck.pip`.
- Out-of-range stored value: clamped on load.

## Verification (manual; there is no JS test harness)

Desktop Chrome, local backend:
1. Tap for sound, press VOL − three times: OSD shows `VOL ▮▮▮▮▮▮▮▯▯▯`,
   audio is audibly quieter, `localStorage["mtv-volume"]` is `70`.
2. Reload: first video plays at 70% once unmuted.
3. Wait for a track change (or skip via `/#remote`): level stays 70%.
4. Press `m` to mute, then VOL +: sound returns at 80%, MUTED overlay gone.
5. VOL + to 100 and VOL − to 0 stop at the ends.

YouTube fallback (empty manifest or `MTV_SOURCE` unset on a fresh standalone
instance): steps 1 and 4 behave the same.

iPhone Safari: the strip shows CH/SKIP/FULL/PIP/CAST but no VOL pads.

## Docs

- ROADMAP.md: status bullet under "Status" for the player controls.
- README.md: no change (it documents URLs, not keys).
