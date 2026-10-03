# Volume control for the MTV web player

Date: 2026-10-03. Repo: `alex/mtv`, files `app/mtv.js`, `app/index.html`.

## Goal

Viewers can turn the sound up and down from the on-screen TV controls and the
keyboard, in both playback backends, and the level survives a reload on that
device. Today the player only has mute/unmute. The level is shown as an
old-school TV volume bar, and a toggle switches the picture between the CRT
look and a plain ("regular") picture.

## Non-goals

- No slider, no fine-grained control: 10% steps only.
- No change to the mute semantics ("MUTED / TAP FOR SOUND" on first load).
- Regular mode keeps the green VT323 OSD, the static burst on channel change
  and the volume bar. It is a clean picture, not a different player.
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
  4. `showVol()`: fill the bar (below), add `show` to `#osd-vol`,
     `clearTimeout(volTimer)`, `volTimer = setTimeout(remove show, 2000)`.
     `#osd-ch` and `showOsd()` are not touched.
- `volStep` has no pad chrome in it. The two pad click handlers call
  `e.stopPropagation()`, `volStep(±1)`, `showPads()`, mirroring the CH pads;
  the keys call `volStep` only, like ArrowUp/Down.
- Before `deck` exists, presses are ignored (same as `chStep`).
- Intended edge behaviour, not to be "fixed": VOL + at 100 while muted still
  unmutes (level unchanged). Volume 0 while unmuted is silent with no MUTED
  overlay, and a tap does nothing audible; that follows from the mute
  semantics being untouched.

### Volume bar OSD

Markup in `index.html`, after `#osd-mute`:

```html
<div class="osd" id="osd-vol" aria-hidden="true">
  <span id="vol-label">VOLUME</span>
  <span id="vol-bar"><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></span>
</div>
```

- Ten `<i>` segments, one per 10% step. `showVol()` toggles class `lit` on
  the first `volume / 10` of them.
- Position: fixed, same left edge and bottom offset as `#osd-track` so it
  sits where the song credits sit (they are hidden by the bar's `z-index`
  only while it shows; no logic change to credits). On narrow screens it
  follows the same `@media (max-width: 600px)` offsets as `#osd-track`.
- Look: `#vol-label` in VT323 phosphor green with the existing text-shadow
  glow, letter-spaced. `#vol-bar` is a flex row, gap 4px. Each `<i>` is a
  block about 3.2vw wide (clamped 18–44px) and 1.6vw tall (clamped 10–22px),
  `border: 1px solid var(--phosphor-dim)`, transparent fill. `.lit` fills
  with `var(--phosphor)` and `box-shadow: 0 0 8px var(--phosphor-dim)`.
- Fades with the shared `.osd` opacity transition; identical in both
  picture modes.

### CRT / regular picture toggle

- `crt`: boolean, default `true`, stored as `"1"`/`"0"` in
  `localStorage["mtv-crt"]` with the same try/catch pattern. Loaded at
  startup before the boot screen is removed, so there is no flash.
- `applyCrt()`: `document.body.classList.toggle("plain", !crt)` and sets the
  pad label to `CRT ON` / `CRT OFF`.
- CSS, next to the CRT layers:
  - `.plain #scanlines, .plain #vignette, .plain #hum { display: none; }`
  - `.plain .screen { transform: translate(-50%, -50%); }` removes the 1.12
    overscan zoom for local video. `#player` (the YouTube iframe) keeps its
    zoom: that crop is what hides YouTube's logo and title bar.
  - `#static`, `.osd`, fonts and colours are unchanged in `.plain`.
- Pad `<button id="pad-crt" aria-label="toggle CRT picture">CRT ON</button>`
  after FULL. Click: `e.stopPropagation()`, `toggleCrt()`, `showPads()`.
  Key `c` calls `toggleCrt()` with `preventDefault()`.
- `toggleCrt()`: flip `crt`, save, `applyCrt()`, `staticBurst(250)` so the
  switch feels like a set being retuned.
- Works before `deck` exists (pure CSS), so no deck guard.

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

iPhone Safari: the strip shows CH/SKIP/FULL/CRT/PIP/CAST but no VOL pads.

Volume bar and CRT toggle, desktop Chrome:
6. VOL − shows the bar with 7 of 10 segments lit, label `VOLUME`, gone
   after 2 s; the song credits are back afterwards.
7. Press `CRT ON`: scanlines, vignette and hum disappear, the picture is no
   longer zoomed, the pad reads `CRT OFF`, a static burst plays. Reload: still
   plain, `localStorage["mtv-crt"]` is `"0"`. Press again: CRT look returns.
8. Force the YouTube fallback in plain mode: the iframe is still zoomed (no
   YouTube logo visible).

## Docs

- ROADMAP.md: status bullet under "Status" for the player controls.
- README.md: no change (it documents URLs, not keys).
