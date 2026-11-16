# Plan: Prism Aurora restyle

**Spec:** `docs/UI_PLAN.md` and `docs/ui-plan/prism-aurora-reference.png` (the
binding authority). `CLAUDE.md` says all UI work follows them.

**Goal:** restyle the whole UI in `ui/` to Prism Aurora. Restyle only: every
feature, element ID, keyboard shortcut and API call keeps working.

## Global Constraints

- Match `docs/UI_PLAN.md` exactly: color tokens, category colors, shapes, glass
  panels, glow, motion. No other themes.
- Restyle only. Keep every existing element ID, `data-*` hook, class that JS or
  tests query, keyboard shortcut and API call. Moving an element in the DOM is
  fine if its ID and behavior stay. Do not change user-facing copy unless a
  task says so.
- Do not touch `samplelib/`. No backend or API changes. New UI may only call
  existing endpoints with existing parameters.
- Offline app: no frameworks, CDNs, web fonts or libraries. Plain HTML, CSS,
  vanilla JS.
- `prefers-reduced-motion: reduce` turns off all motion. A "Reduce effects"
  toggle swaps glass for solid `#160c2c` panels and hides the background
  blobs and grid; `prefers-reduced-transparency: reduce` applies the same
  solid styles automatically.
- Tests must keep passing: `python3 tests/run_all.py`,
  `node tests/ui_smoke.js "http://127.0.0.1:8799/?fixture=1"`,
  `node tests/ui_flow.js`. The smoke test checks there is no horizontal page
  overflow at 1366x768 and that the focused element has a visible
  `box-shadow` focus ring. Do not edit tests to make them pass.
- Text on glass stays at `--text` or `--muted` contrast or higher.

### Tokens (verbatim from the spec)

`--bg #09051a`; `--panel rgba(255,255,255,.05)` with
`backdrop-filter: blur(26px) saturate(150%)`; `--raised rgba(255,255,255,.09)`;
`--field rgba(255,255,255,.045)`; `--line rgba(255,255,255,.10)`;
`--text #f6efff`; `--muted #a59cc4`; `--accent #ff3fd2`; `--accent-2 #2de2ff`;
`--on-accent #14041f`.

Category colors: Drums `#ff3fd2`, Bass `#2de2ff`, Synth `#a86bff`, Vocals
`#ffb13f`, FX `#ff5b6e`, Keys `#3ff2c2`.

Background: teal `#3ff2c2` blob top left and violet `#7b6cff` blob at right,
about 40% opacity, 110px blur; magenta glow along the bottom edge; faint
magenta perspective grid fading out toward the top.

Shapes: cards 24px radius, inner elements 14-16px, tags 8px, player bar and
search fields pill-shaped; 14px gap between cards. Font stack
`"Segoe UI Variable Display", "Segoe UI", system-ui, sans-serif`; tabular
monospace numbers for BPM, key, length, time. Page title 30px/800, body 13px.

## Rulings (controller decisions where the spec and the app differ)

1. **Category mapping.** The app has 10 categories, the spec has 6 colors.
   Map: `One-Shots/Drums`, `Loops/Drum Loops` → Drums; `One-Shots/Bass & 808s`,
   `Loops/Bass Loops` → Bass; `One-Shots/Instruments`, `Loops/Melody Loops` →
   Synth; `One-Shots/FX & Vocals`, `Loops/Vocal & FX Loops` → Vocals;
   `Presets & MIDI` → Keys; `Unsorted` and anything unknown → FX (red reads as
   "needs attention"). Implement once as a JS helper returning a slug
   (`drums|bass|synth|vocals|fx|keys`) and CSS classes `.cat-<slug>` that set
   `--cat` to the color.
2. **Stat tiles** show the app's real categories, not the reference's six
   labels: one tile per category with a count above 0, in `CATEGORY_LIST`
   order, labeled with `Fmt.catShort`. Counts come from the existing
   `/api/samples` endpoint with `category=<c>&limit=1` (read `total`), using
   the same pack and "show all files" scope as the current list; the progress
   bar is count / total. Clicking a tile sets the existing Category filter
   (same code path as the `#lib-filters [data-f="category"]` select, which
   stays and stays in sync); clicking the active tile clears it. Inactive
   tiles fade to 45% while one is active. The row scrolls horizontally inside
   itself if it overflows; it must never widen the page. Container id
   `lib-stats`.
3. **Not added** (no data or feature behind them; restyle only): the waveform
   column in rows, favorite hearts, "Add to kit" in the inspector, and a
   Ctrl K command palette. The existing `?` keyboard-help button takes the
   bottom rail slot the reference gives Ctrl K.
4. **Row play button** (in the reference and spec): a square button tinted
   with the row's category color at the start of the name cell. Clicking it
   moves the cursor to that row and plays it through the existing player code
   path. It must not break click, Ctrl+click and Shift+click selection.
5. **Pack avatars:** initials (first letters of the first two words) on a
   gradient of a category color picked by a stable hash of the pack name.
6. **Header card** holds the eyebrow "LIBRARY", the existing `#lib-h` and
   `#lib-sub` (JS keeps setting their text), and the existing `#lib-q` search
   (moved into the card, still with its `/` hint). Pack tabs `#lib-tabs`,
   filters, chips and the table follow below.
7. **Accent picker:** four swatches in the rail. Presets: magenta/cyan
   `#ff3fd2`/`#2de2ff` (default), teal/violet `#3ff2c2`/`#7b6cff`,
   violet/magenta `#a86bff`/`#ff3fd2`, amber/pink `#ffb13f`/`#ff4f9a`. Sets
   `data-accent` on `<html>`, overriding `--accent`/`--accent-2` only;
   category colors never change. Persist in `localStorage` key `slo.accent`
   inside try/catch.
8. **Reduce effects toggle:** a rail button `#btn-effects` with
   `aria-pressed`, toggling class `reduce-fx` on `<html>`, persisted in
   `localStorage` key `slo.reduceFx` (try/catch).
9. **Status colors** not in the spec (move/link/keep/hold/exclude, ok/warn/err)
   are retuned onto the palette and stay distinct: move `#2de2ff`, link
   `#a86bff`, keep `--muted`, hold `#ffb13f`, exclude `#ff5b6e`, ok `#3ff2c2`,
   warn `#ffb13f`, err `#ff5b6e`.
10. **Focus ring:** `box-shadow` ring in `--accent` (keeps the smoke test's
    focus check meaningful).
11. **Player waveform and spectrum:** draw a real waveform on a canvas behind
    the scrubber by fetching the existing `/api/audio/<id>` URL and running
    `decodeAudioData` (skip files over 40 MB or formats that fail to decode;
    fall back to a gradient pill track). The spectrum uses an `AnalyserNode`
    on the existing `<audio>` element, created lazily on first play, with the
    `AudioContext` resumed on play; any failure falls back to a static bar
    graphic and must never stop playback. In fixture mode audio returns 404,
    so the fallbacks are what screenshots show.

## Screenshots

Every task ends by running
`NODE_PATH=$(npm root -g) node <scratchpad>/shots.js <outDir>` against
`python3 -m http.server 8799 -d ui` (fixture mode) and checking the images.
`01-library-1440.png` is 2880x1800, the same size as the reference PNG.

---

## Task 1: Foundation and shell

Files: `ui/app.css`, `ui/index.html`, `ui/app.js` (small additions only).

- Replace the `:root` tokens in `ui/app.css` with the spec tokens, category
  colors, radii (`--r-card 24px`, `--r-in 15px`, `--r-tag 8px`), the 14px gap
  and the font stacks. Keep or alias existing variable names other rules use
  so nothing goes unstyled; remove the old "console strip" header comment and
  describe Prism Aurora instead.
- Background layer: blobs, bottom glow and perspective grid as fixed,
  `pointer-events: none` pseudo-elements or a decorative element behind the
  app. No layout shift, no page overflow.
- App frame: rail, stage and footer become separate glass cards with 14px
  gaps and 14px page padding, 24px corners, 1px `--line` borders.
- Rail: brand mark, icon buttons; the active one filled with the accent
  gradient and glowing. Add the accent picker (ruling 7) and `#btn-effects`
  (ruling 8) above the `?` button. Keep `#rail-review-dot`, `#fixture-flag`,
  `#btn-keys` working.
- Base components: buttons (`.btn`, `.btn-primary` gradient with magenta
  shadow, `.btn-go`, `.btn-danger`, `.btn-ghost`, `.btn-small`), inputs,
  selects, pill search fields, `.seg`, `.tab`, `.pill`, `.chip`, `kbd`,
  checkboxes, scrollbars, focus ring (ruling 10).
- Footer: the transport becomes a floating frosted pill at the bottom center
  with the gradient play button and glow ring; keep every `tp-*` ID. The
  status bar stays visible and readable below or beside it.
- Reduce effects and `prefers-reduced-transparency`: solid `#160c2c` panels,
  no `backdrop-filter`, blobs and grid hidden.
- `prefers-reduced-motion`: a global rule that disables transitions and
  animations.

## Task 2: Library screen (must match the reference image)

Files: `ui/app.css`, `ui/index.html`, `ui/library.js`, `ui/core.js` if a
shared helper belongs there.

- Category helper and classes (ruling 1).
- Packs sidebar: "Packs" title with count, pill search, rows with avatars
  (ruling 5), name, maker and count; "All" row with the library icon; the
  selected row gets a `--raised` fill and an accent left edge.
- Header card (ruling 6): 120° gradient magenta → `#a86bff` → cyan, white
  text, 30px/800 title, dark frosted pill search with `/` hint.
- Stat tiles (ruling 2): category gradient, dark `--on-accent` text, count
  26px/800, small progress bar.
- Filters and chips restyled as glass pills under the tiles.
- Table: transparent header with uppercase muted labels; 50px rounded,
  borderless rows (update the row height constant the virtual table uses so
  scrolling math stays right); row play button (ruling 4); category shown as
  an 8px-radius tag colored by category; tabular mono numbers; the cursor /
  selected row gets a 4px left edge in its category color and a soft gradient
  wash. Keep every existing column and the status badge.
- Inspector: category tag and type label, title, pack · maker line, a
  waveform card (Task 4 fills it with the player's decoded waveform when this
  sample is the loaded one; until then it shows a flat gradient centerline,
  never invented bars), gradient play/stop button, meta boxes on
  `--field` in a 2-column grid, tags as pills, path in mono. Keep every
  existing field, provenance badge, edit control and button.
- Selection bar and kits tab restyled to match.

## Task 3: Scan, Review and Move Log

Files: `ui/app.css`, `ui/index.html`, `ui/review.js`, `ui/app.js`.

- Each view gets a header card in the same component as the Library header
  (gradient, eyebrow, 30px/800 title, subtitle), holding its existing title
  ID and its primary actions.
- Scan: form card, path field, gradient start button, the read-only promise
  line, progress card with a gradient meter, warning/error pills, issue list,
  summary cards as stat-tile style cards.
- Review: stale banner, plan cards as stat tiles (map action colors per
  ruling 9), tabs, segmented group control, filters, operations table rows in
  the Library row style (50px, rounded), operation detail panel, kits list
  and kit detail.
- Move Log: verification and reports panels as glass cards, status segmented
  control, table rows in the same style.

## Task 4: Dialogs, empty and error states, and the player

Files: `ui/app.css`, `ui/index.html`, `ui/vtable.js` (markup classes only if
needed), `ui/core.js` (player).

- All `<dialog class="dlg">`: glass card, 24px corners, dimmed blurred
  backdrop, header, table and actions restyled; error boxes in `--err`.
- Toasts, loading state, the virtual table's empty and error states
  ("Couldn't load this list", "Try again"), `.side-msg`, banners and the
  "Nothing selected" inspector state: designed, centered, on-palette.
- Player: waveform scrubber and spectrum visualizer (ruling 11), loop button
  active state in `--accent-2`, volume slider with the accent gradient.

## Task 5: Animations

Files: `ui/app.css`, plus JS only to set a stagger index or play state.

- Rows and cards fade and slide in, staggered about 20ms each (cap the
  stagger so long lists don't wait; only on first render of a list, never on
  virtual-scroll re-renders).
- The play button pulses its glow while playing; the playing row shows
  animated equalizer bars in its play button.
- The playhead sweeps across the waveforms in the player and inspector.
- Spectrum reacts to audio (from Task 4).
- Hover transitions about 150ms ease.
- All of it off under `prefers-reduced-motion` and with Reduce effects on
  where it involves blur or glow.
