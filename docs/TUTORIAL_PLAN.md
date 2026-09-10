# Tutorial plan — landing menu, manual embeds, first-turn tooltips

Status: **BASIC SHIPPED (v1.32, 2026-08-28).** Written 2026-08-28 as a
plan; kept as the design record, with the sections below still describing
the intent rather than the code. Where the two disagree the code wins —
see §9 for what actually landed and what is still open.

Replaces the landing page's `Quick game` button with a `TUTORIAL` chooser
offering three entries. Every entry runs on the memory backend. Basic and
Quick last three nights; Advanced runs four (v1.36 — see §11).

---

## 1. What already exists (and therefore costs nothing)

Worth stating up front, because it makes most of this feature cheap and
concentrates the real work in exactly two places.

- **Programmatic game spawn.** `newGame({players, agents, backend})` in
  `server/static/app.js` is already the entry point the landing page uses
  for `?new=quick`, and it already pins `backend: "memory"` (v1.14 — so a
  machine with Snowflake credentials doesn't make the "just let me play"
  button the slowest path in the app). Tutorial presets are more of the
  same call.
- **Board size and night count are first-class.** The New Game modal
  already exposes `ngm-width`, `ngm-height` and `ngm-cap`, and
  `GameSession.new` takes `width`, `height` and `season_day_cap`. "Smaller
  map, three nights" needs no engine change.
- **Manual deep links work, and so does re-pointing them.**
  `manual/manual.js` parses `#tab=<name>` and listens for `hashchange`, so
  a single iframe can be stepped through sections **without reloading** —
  set `iframe.contentWindow.location.hash` and the manual switches tab in
  place. This is what makes a stepper feel instant rather than flashing
  white between pages.
- **Available tabs:** `tiles`, `harvesters`, `probes`, `orders`, `vision`,
  `signs`, `emp`, `chaff`, `orbital`, `loop`, `season`, `maps`,
  `opponents`.

## 2. What does not exist

### 2.1 Turning weapons and signs off — the main body of work

There is **no flag anywhere** that disables weapons or signs.
`RED_HARVEST_LITE` is only a *bot* that declines to use weapons; it does
not stop a human buying an EMP, and nothing suppresses sign minting. Basic
mode needs both genuinely off.

Two constraints to hold to:

- **Per game, not per process.** Same rule as the storage backend
  (`AGENTS.md`, v1.14): the flags live on the session and are persisted, so
  a tutorial and a real season can coexist on one server. Nothing may ask
  "is this process in tutorial mode?".
- **Agents read the flags; they are not silently filtered.** If V12 keeps
  proposing EMPs and the sanitiser eats them, the agent burns its whole
  turn budget on illegal moves and looks broken. The flags belong in
  `_active_rules()` (`sea_of_colours/snowpark/view.py:65`) next to
  `drop_mode` / `probe_radius`, which is already the channel prompts are
  told to read dynamically.

This is a rules change, so it is a **fan-out edit** per `AGENTS.md`:
engine enforcement → `RULEBOOK.md` prose + Canonical Configuration +
changelog → V12 prompt (`orchestrator_2/harnesses/tabula_v12/`) →
`agent/heuristic_agent.py` → UI (`server/static/`) → `guide/` and
`manual/` → tests → `docs/OUTSTANDING_ISSUES.md`.

Note the UI half is *hide*, not *ignore*. A greyed EMP button in a mode
where weapons do not exist still teaches the player that weapons exist and
that they are broken.

### 2.2 A fixed tutorial board

`GameSession.new` only generates from a seed — it cannot be handed a
prebuilt grid. So "a thick seam down the middle with a pure" cannot be a
curated seed: no seed reliably produces that shape, and the v1.29 halo
grading would perturb it anyway.

**Recommended:** a named terrain preset in
`sea_of_colours/generator.py` (`terrain_preset="tutorial_seam"`) rather
than grid injection. The grid is packed and persisted *after* generation,
so a preset saves, loads and replays through every existing path with no
special-casing anywhere downstream.

## 3. The three entries

`server/static/landing.html:44` (`#btn-quick`) becomes `TUTORIAL`, opening
a chooser with three cards. `server/static/landing.js:330` currently sends
`?new=quick`; the chooser sends `?new=tut-basic`, `?new=tut-advanced` or
`?new=quick`, all handled in the same `app.js` block that reads the `new`
param today (~line 22185).

| Entry | Board | Nights | Weapons | Signs | Opponent |
|---|---|---|---|---|---|
| Basic | 24x16, `tutorial_seam` | 3 | off | off | `RED_HARVEST_LITE` |
| Advanced | 24x16, `tutorial_seam` (same board) | 3 | on | on | `RED_HARVEST_LITE` |
| Quick game | 40x28, normal generation | 3 | on | on | `RED_HARVEST_LITE` |

Basic pairs naturally with `RED_HARVEST_LITE`, which already never fires
anything — so the weapons-off rule and the bot's behaviour agree instead of
the bot being visibly hobbled.

Advanced deliberately reuses the *same* board as Basic. The second run is
about what signs and weapons add to terrain you have already read, so
re-rolling the map would throw away the only thing the player knows.

**Build the presets as overrides on the New Game modal's default config,
not as independent literals.** Otherwise any field added to that modal
later silently fails to reach the three tutorial paths — a drift bug that
would surface months later as "the tutorial ignores X".

## 4. Teaching surface: mini-films first, manual second

Eight sections of prose before a first move is a wall. The primary
teaching surface should be **short silent films of real turns**, with the
manual embed demoted to "read more" behind them.

### 4.1 How the films get made — generated, never hand-recorded

**Hard rule: no hand-recorded video.** The ORDERS and ORBIT panels are
mid-redesign and mines are being removed; a screen capture taken today is
wrong within a week, and nobody re-records by hand. Films must be
reproducible by running a script.

The machinery is close to already built. `backstage/probes/_fx_*.py` — sixteen
harnesses today — already boot a memory-backend server, seed a season,
drive the real UI and take screenshots. Playwright is installed and its
`new_context(record_video_dir=...)` is available, so the same harness
pattern records `webm` instead of stills.

**Proposed:** `backstage/films/make_tutorial_films.py`, one function per film,
sharing the `_fx_*` seeding helpers. Output committed to
`server/static/films/*.webm`. Re-run after any UI change that a film
shows.

Two known gaps:

- **No cursor in the recording.** Playwright doesn't paint a pointer into
  video. Inject a small fake cursor element that the script moves to each
  click target before clicking — without it the UI appears to operate
  itself, which teaches nothing about *where to click*.
- **Pacing.** Real automation clicks faster than a human can follow.
  Films need deliberate dwell before and after each click.

### 4.2 Why not the alternatives

- **Replaying a canned session through the existing cinematic**
  (`paintReplayFrameOntoMain` / `runReplayAnimationsTick`, fed by a
  pre-seeded session on the memory backend) is attractive — zero video
  files, and it can never look stale because it renders through the live
  renderer. But it only shows *the night resolving*, never *how to give
  the order*, which is precisely the thing a first-timer is stuck on. Good
  candidate for the "what happens at night" beat specifically.
- **A hand-built self-contained animation** in the style of
  `docs/scatter_fx.html` gives total control and drifts fastest. Not worth
  it for turn mechanics.

### 4.3 The films

Silent, looping, autoplay-muted, roughly 8–15s each — one idea per film.

**Basic**

1. **You start blind** — the whole board is fog; hover a cell, the tooltip
   says `terrain unseen`.
2. **Launch a probe** — the vision border opens up and terrain appears.
3. **Read a cell** — hover inside the lit area: colour, tier, value, score.
4. **Drop a harvester** — `DROP`, pick a cell *you can see*, the queued
   annotation appears on the row.
5. **Walk, harvest, and lift** — queue steps and a `LIFT`, `TRANSMIT`, the
   night resolves, cargo fills, the vault and score move.

**Probe before drop is not a stylistic choice.** `drop_mode` is
`live_only` (`game/tuning.py`), so on night one there is nowhere legal to
land — the board is 100% fog until a probe goes up. A film that opens by
dropping a harvester is teaching an order the engine will refuse. The
opening lesson *is* the blindness.

Likewise **the lift belongs in the same film as the walk**, not a later
one: a harvester left on the surface at the end of your queue is destroyed
at Aurora (§3.11.2), so a film that drops and walks without lifting has
quietly taught the player to lose the unit.

**Advanced**

6. **Signs** — what a rival's sign gives away.
7. **EMP and chaff** — the area goes down; a harvester dies.

That covers the ground the eight prose sections were covering, in the
order a turn actually happens.

### 4.4 The manual embed (secondary)

Still worth building, now as "read more" rather than the front door. A
modal iframe over `/manual/#tab=<name>`, stepped with Next / Back by
writing the hash.

**Embed mode.** Add `#embed` (or `?embed=1`) handling to `manual/` that
hides the manual's own sidebar and tab bar, so the modal shows one section
of content rather than a whole nested application. Only the sections listed
below need to be embed-safe; the rest of the manual is unaffected.

**Always link out.** Every embedded section carries an "open the full
manual" link to `/manual/#tab=<current>` in a new tab, dropping embed mode.
Nothing in the embed is a dead end.

**Section order.**

- **Basic (8):** `tiles` → `harvesters` → `probes` → `vision` → `orders` →
  `loop` → `maps` → `opponents`. Board, then what you own, then how you
  see, then how you give orders, then how a night resolves, then the wider
  map and rivals.
- **Advanced (3):** `signs` → `emp` → `chaff`.

**Nothing here gates play.** With films carrying the front door, these
eight are opt-in reading reachable from the film stepper and from the
in-game manual link — not a wall to climb before the first move. Keep the
`START PLAYING` button live throughout regardless.

## 5. First-turn tooltips

Reconciling two things said at different times: no scripted overlay, and
tooltip overlays for first-turn play. The line between them:

**Build:** contextual, anchored, dismissible tooltips pointing at real
controls, driven by a small declarative list
(`{anchor: <selector>, text, showWhen: <predicate>}`), that never block
input and never advance the game. First turn only. Dismissed on
interaction with the thing they point at, plus a "don't show again"
persisted in `localStorage`.

**Do not build:** a wizard that greys the board, takes the wheel, and
marches through numbered steps.

**Scope:** tutorial games only (Basic and Advanced), not Quick game.

The declarative list matters more than it looks — it is what stops these
becoming thirty hardcoded `if` branches scattered through `app.js`, and it
is what lets a stale tooltip be found when a control is renamed.

## 6. Suggested order

1. **Mines removal first** (already queued). It shrinks the weapons surface
   *before* a toggle is added across it — otherwise that work is done twice.
2. **Per-game `weapons_enabled` / `signs_enabled`**, with the full fan-out
   sweep. This is the big one.
3. **`tutorial_seam` terrain preset.**
4. **Landing chooser + the three presets** — mostly existing machinery.
5. **First-turn tooltips.**
6. **Films last, deliberately.** Every film shows the UI as it is on the
   day it is shot, so shooting before the mines removal and the
   ORDERS/ORBIT redesign have settled means shooting twice. The films are
   also the cheapest step once `make_tutorial_films.py` exists, so there is
   no schedule reason to pull them forward.
7. **Manual embed mode + iframe modal stepper** — optional, "read more".

## 7. Risks

- **The rot risk on the flags.** An engine that honours `weapons_enabled`
  while the V12 prompt does not know about it produces an agent that spends
  every turn proposing illegal moves. Prompt and heuristic must land in the
  same change as the engine, not after it.
- **Preset drift** — mitigated by building presets as overrides on modal
  defaults (§3).
- **Embed-mode scope creep.** Only the eleven listed sections need to be
  embed-safe. Making the whole manual embeddable is a much larger job with
  no payoff here.
- **Film staleness is the whole ball game.** A film is a screenshot of a
  moving target. The mitigation is not discipline, it is that
  `make_tutorial_films.py` regenerates all of them in one run — so the
  script is the deliverable and the `.webm` files are build output. If
  films ever become hand-recorded, they will be wrong and stay wrong.
- **Film review has no test — and this is not hypothetical.** The spike
  (`scripts/_film_drop.py`, run 2026-08-28) exited `PASS`, produced a clean
  11.6s film, and was pedagogically wrong twice over: it dropped onto fog
  (illegal under `live_only`, so the order dies at resolution) and left the
  harvester with a `STRANDED` sticker. Every automated check it had was
  green. A film can only be signed off by watching it.

## 8. Spike result (2026-08-28)

`scripts/_film_drop.py` proved the pipeline end to end. Output:
11.6s, 1.4 MB `webm`. The script itself is **deleted** — everything it
established was folded into `backstage/films/make_tutorial_films.py`, and a
second, diverging copy of the cursor kit was the obvious way for the two
to drift. This section is the record of what it taught.

What it establishes:

- **The generation approach works.** Real session, real selectors, real
  clicks, real animations, ~16s to shoot. Re-running after a UI change is
  free.
- **The injected cursor reads correctly.** A lime ring that glides on
  `requestAnimationFrame` with an `easeInOutQuad` and pulses on press.
  Critically, the script moves the *real* pointer to the same place, or
  nothing hovers and half the UI never lights up.
- **A caption bar is worth having** and costs nothing — plain DOM, picked
  up by the recording for free.

What it exposed, all now folded into §4.3 above:

- Night one is **100% fog** (384/384 cells), so "pick an unfogged cell"
  finds nothing and the shoot stalls. Fixed by selecting the centre cell
  by coordinate.
- The ORDERS panel is an **annotator, not a gate** (v1.24) — clicking fog
  queues the order happily. Convenient for filming, and exactly why the
  film could teach an illegal move without anything complaining.
- The picker is **sticky** — a landing chains into steps, so the blinking
  banner is still up when the film ends unless `Escape` is pressed.

---

## 9. What shipped (v1.32, 2026-08-28)

### Landed

- **Engine flags.** `weapons_enabled`, `signs_enabled`, `tutorial` on
  `GameSession`, persisted, published on `meta.rules`, guarded at
  `_apply_build_weapon` / `apply_emp_launch` / `apply_chaff_flare` /
  `_compute_blue_sign` / `_register_redsign`. RULEBOOK §changelog v1.32.
- **Presets, server-side.** `sea_of_colours/game/tutorial.py` owns
  `basic` / `advanced` / `quick`; `server/app.py` resolves the NAME the
  client sends and forces `memory` + `red_harvest_lite`. The client
  cannot disagree with the engine about what Basic means.
- **The chooser** on the landing page, replacing `Quick game`.
- **The modal** — `server/static/tutorial.js`, ~440 lines, loaded after
  `app.js` and coupled to it by exactly one DOM event. A missing film
  degrades to its prose; a broken modal cannot break a season.
- **Nine Basic films**, in `server/static/films/`, shot by
  `backstage/films/make_tutorial_films.py` and committed.
- **`backstage/films/_fx_tutorial.py`** — the end-to-end check. Asserts the
  preset reaches the board, that no weapon control is *visible*, that
  the film actually plays (rather than 404ing into the placeholder),
  and that the reel **refreshes on the next turn**, which is the part
  most likely to rot.

### Deliberately not done

- **Advanced and Quick have no reels.** Both modes run; neither opens a
  modal, because `resolveReel` finds no key and the button hides itself.
  The signs/EMP/chaff films in §4.3 are the remaining content.
- **First-turn tooltips (§5)** are untouched. The films cover the same
  ground and the tooltips would land on a UI that is still moving.
- **The manual embed (§4.4)** is not wired. The prose under each film
  turned out to carry the lesson on its own, and an iframe of the manual
  is a much heavier promise to keep in sync.

### Notes for the next person

- **Films are build output that is checked in** (~15 MB). An attendee
  clones and plays; nobody needs ffmpeg unless they re-shoot. The
  `reports/films/` ignore rule is scratch space, not these.
- **The shoot needs a running server** and takes ~6 minutes for all
  nine, including the ffmpeg boot-trim pass.
- **Reel keys are `<preset>:<phase>:<day>` and the day numbers bite.**
  The night rolls the calendar before Orbit, so the first orbit anyone
  sees is `orbit:2`. A reel written under `basic:orbit:1` never opens
  and nothing warns you.
- **`expect_slots`, not row counts.** The queue consolidates a walk into
  one row, so counting `.solo-queue-row` under-reports; the films assert
  against the `N/21 slots` counter instead.

---

## 10. Outcome films (v1.33, 2026-08-29)

Three of the reels described a mechanic over a board where it was not
happening. `basic_collision` talked about crashes on a quiet night;
`basic_stranded` showed the STRANDED sticker and stopped before the
consequence; nothing at all covered where the score comes from. These
three now play the real thing.

### The duel setup

The bot in the other seat will not take direction, so a scripted crash
was impossible. `_setup_duel` opens **both seats as human** and posts
the rival's whole night over the API before the camera rolls, so the
resolve is genuine simultaneous play — the explosions are the engine's.
The film is handed the squares setup chose (`Film.plan`) rather than
picking its own, because the rival is already committed to them.

This is also why `server/app.py` honours explicit `weapons_enabled` /
`signs_enabled` in the new-game body: the films want Basic's *look*
without Basic's forced bot seat.

- **`basic_crash`** — both shapes in one night: two Houses landing on
  one square at H01, then a walk into an occupied square at H03. They
  look nothing alike on screen and a player who has seen one does not
  recognise the other.
- **`basic_stranded`** — no lift, the guard's second-click warning
  ignored on purpose, the dawn wave, then a close-up of the wreck with
  its tooltip (`†` and `lost day N`).
- **`basic_score`** — one load from ground to hold to station to
  catapult to scoreboard, across three phases, without cutting. Runs
  ~80s and that length is the point: the gap between harvesting and the
  number moving is the lesson.

### The camera, and why it fought back

> **Superseded by §12 (v1.35).** Every numbered point below is a patch on
> the same underlying mistake — scaling a live element inside a fixed
> layout — and the camera is now a post-process crop instead. Kept as
> written because it is the case FOR that change, and because anyone who
> proposes transforming the interface again should read what it costs.

Films that turn on a collision need a close-up — a burst is a handful of
pixels for under a second, and the product's own zoom dragger tops out
at 127% of fit-to-width, which is a legibility control, not a camera.
`Film.push_in` is a CSS transform on `.cc-map-viewport`. Four things
about it are load-bearing, each learned by shooting a film that lied:

1. **Frame a SET of squares and solve for the scale.** A hand-picked
   2.1x framed two crash sites five squares apart with the second one
   off the bottom edge, and nothing failed.
2. **Translate explicitly; do not lean on `transform-origin`.** Origin
   on the subject only guarantees the subject does not *move*. On a
   board in the top half of a tall viewport that aims the close-up at
   empty ground.
3. **Re-aim on every hour.** The cinematic rebuilds the grid between
   hours and the rebuilt grid can sit at a different offset; the scale
   survives, the translate does not.
4. **Host `#collision-fx-layer` outside the scaled subtree.** Every
   effect in `app.js` places itself with `cellRect.left -
   hostRect.left` — a *screen* delta written as a *local* offset. That
   identity breaks the moment an ancestor is scaled: the first zoomed
   crash film drew its collision X several hundred pixels off the board.

5. **Re-anchor the pixel-positioned overlays on EVERY frame of the
   glide.** Vision borders, planned orders, blue sign and redsign
   snapshot cell rects rather than living in the grid, so a camera move
   strands all four (issue 29). `app.js` already fixed this for its own
   zoom; the fix just was not reachable, and is now
   `window._osReanchorOverlays`. Calling it at both ends of the move is
   not enough — the overlays measure the cells *as they are now*, so one
   call pins them to a size the board is only passing through, and the
   whole 700ms plays with a small border sitting still on swelling
   terrain. That is what "the zoom breaks the vision areas" was.

`push_in` measures the result and fails if the subject is outside the
frame, if the squares did not actually grow, or if the vision border has
drifted more than 8px from the grid — because a close-up is the one
effect that fails silently. The film still runs, the caption still says
"watch this square", and you get a wide shot with a lie over it.

### Other traps paid for here

- **Steps are 4-way orthogonal.** `_adj` is Manhattan. The harness had
  a diagonal `STEP_RING`, the engine dropped the illegal steps without
  complaint, and the film walked a harvester that never moved.
- **A phase resolves only when EVERY seat commits.** On a duel board
  nobody is playing the other one, so `submit_orbit(..., "p2")` is not
  tidiness — without it the film sits on a spinning board until timeout.
- **`park()` must land on nothing.** It used to park mid-board, which
  left a stale cell readout sitting over the map for the whole next
  beat. It now aims for the dead strip under the grid and asserts the
  card actually closed.
- **The ORDERS roster is gone once the night resolves.** Damage shows
  up in the ORBIT panel (`[data-orbit-damaged-count]`), not on a fleet
  row sticker.
- **Assert what the beat CLAIMS, not that the beat happened.** The
  first `basic_crash` cut showed one orblift bouncing off an empty
  square: the rival's craft was suppressed as a private landing, with
  the caption naming the House it hit right underneath (issue 30). No
  check could see it, and neither could I from the video — the craft
  are one glyph wide for under a second. `Film.watch_lifters` counts
  distinct seat colours among in-flight arcs. Note that the *loose*
  version of that census passed against the broken build, because every
  lift at dawn is also an orbital arc; it only bites once narrowed to
  bounce arcs. **Back out the fix and confirm the check goes red** —
  this one silently did not, twice.
- **The "already shown" memory is scoped per GAME**
  (`"<session>|<reel>"`), not per reel. Reel keys repeat in every Basic
  game, so keying on the reel alone meant the modal auto-opened exactly
  once per browser, ever — see issue 28. `_fx_tutorial.py` now plays a
  second tutorial in the same profile to hold that line, because a
  fresh Playwright context cannot see this class of bug at all.

## 11. Advanced (v1.34, 2026-08-29)

Basic teaches the machine — probe, drop, walk, lift, and the ways a night
goes wrong on its own. **Advanced teaches the other House**: what you can
read about them without seeing them, and what you can do to them without
touching them. Same 24×16, weapons and signs on — and, since v1.36, one
night longer than Basic, because three weapons do not fit in three
nights (§11).

### The arc is the economy

The seven films are one storyline and the spine of it is blue, because
the numbers already force it: you start a season with **250 blue**, an
EMP is **200**, and chaff is **300** (v1.36, was 255). So the stipend
buys exactly one weapon and never a flare, and everything after that is
blue you went and dug up. Night one's hot drop onto the blue pocket is
*literally* what pays for the EMP in orbit two and the chaff in orbit
three. Nothing in the reels has to assert that; the prices do it.

SNAP, at **100**, is the exception the ladder created: it is the one
weapon the opening stipend leaves change from. It is also the reason the
mode grew a fourth night — one weapon per orbit, fired the night after,
and three of those do not fit in three nights.

### The board is pinned, and Basic's is not

`advanced` is the only preset with a `seed` (`ADVANCED_TUTORIAL_SEED`,
2351). Basic's lessons land on any terrain, so a fresh map each time
costs nothing. Advanced's do not: it needs one bright blue smear to
hot-drop into and two pure seams far enough apart to be one each, and
the generator supplies that combination on roughly **10 seeds in 400**
(`backstage/films/_probe_advseed.py` searches for it). An Advanced game that
happens not to have it does not teach a slightly worse lesson — it
teaches that the mode is broken.

Pinning buys a second thing worth more than the first: **the films are
shot on the player's own board.** The blue smear in the video is the
blue smear on their map, at the same coordinates.

`test_the_advanced_board_can_teach_what_advanced_teaches` re-derives the
requirements from the seed, so a generator retune fails there rather
than in a player's tutorial.

### What each turn teaches

Re-laid for the fourth night in v1.36. **One weapon bought per orbit,
fired the night after**, which is what made the extra night necessary
once there were three of them.

| Reel | Films | The point |
|---|---|---|
| `planning:1` | `adv_hotdrop` | The fog is not blank. A blue sign is static, season-old and vague; the hot drop commits a landing into a disk the probe has not cut yet. |
| `orbit:2` | `adv_buy_emp` | Two currencies that do not convert. Credits arrive; blue is mined; only blue buys weapons. |
| `planning:2` | `adv_redsign`, `adv_redsign_rival` | A pure seam mints a **public** beacon the moment anyone sees it — from both sides, and anonymously. |
| `orbit:3` | `adv_buy_snap` | The price ladder: 1 : 2 : 3 into a rack of six. SNAP is the only rung the EMP left you change for. |
| `planning:3` | `adv_smash_grab`, `adv_blind_grab`, `adv_snap` | What to *do* about a beacon. Smash-and-grab prices the greedy line and throws it away for certainty; blind-and-grab attacks the situation instead of the square; SNAP refuses the whole exchange by arriving above the hour's vision note. |
| `orbit:4` | `adv_buy_chaff`, `adv_arms_bar` | A second hull, and the weapon that costs more blue than a season hands you — then what buying it TELLS everybody. |
| `planning:4` | `adv_emp`, `adv_chaff` | EMP takes the clock, not the ore — and only if you wait for it. Chaff denies three hours, which is worth having; aimed at one hour it is a kill. |

Chapter load is `1 · 1 · 2 · 1 · 3 · 2 · 2`. Keep it flat: night two was
four chapters before the split and was the heaviest turn in either
tutorial.

**The fourth night paid for itself twice over.** Beyond fitting SNAP, it
repaired a mismatch nobody had noticed: `adv_chaff` is *shot* on night
four, because the rival needs a whole night to land, work and be caught
reaching for the lift — but on a three-night cap the reel had to play on
night three, so the player was being shown a sequence their own game
could no longer produce.

**Both subsidies moved with it.** `TUTORIAL_BLUE_GRANT_WEAPON` now pays
one SNAP at orbit 3 and one chaff at orbit 4, priced off the weapons
rather than written as numbers. That also moved `adv_buy_chaff` and
`adv_arms_bar` from rig turn 3 to turn 5 — at 300 blue and no subsidy,
orbit 3 is an orbit on which the flare genuinely cannot be bought, so
shooting it there would have filmed a purchase the seat could not make.

**A film carries its day number, so moving a reel means re-shooting.**
This is the trap the fourth night set, and it caught three films before
anyone watched them. A film is a screen recording of a real session, so
the game chrome inside it reads `# day 3 · orbit` and `NOX 03` in the
player's own header font. Change `_ADV_TURN` and the *next* shoot is
correct; the `.webm` already on disk is not, and nothing complains —
the reel plays whatever file it is handed, and both tables read right
in isolation. `adv_buy_chaff` was three generations stale this way
(day 3, chaff at 255, and the pre-subsidy "if you cannot afford it this
turn, that is not a mistake" line the grant had made false).

Two things now guard it. `tests/test_tutorial_mode.py` compares each
reel's day against its film's shoot turn, which catches the source half.
The `.webm` half cannot be tested — it needs a shoot — so the rule is
simply: **if you move a reel between days, re-shoot its films.** For
`adv_emp` that meant more than re-running the camera, because the
storyline did not leave a spendable EMP on night four; `_EMP_TAKES` in
the rig is the three-branch detour that does (an unspent weapon, one
hull, and a rival holding still).

### Things learned shooting these

- **Both redsign paths are the same code.** `_register_redsign` does not
  care who looked; the beacon is anonymous and public either way. That
  makes the pair *better* than "yours vs theirs" — the mirror film's
  subject is that a beacon can arrive with no probe of yours near it.
- **A one-order night is one hour long.** Both redsign films first shot
  with a single probe, so the `H02` cue never fired and the reveal had
  nowhere to land. Queue position is the hour: two probes, jackpot
  second.
- **Aim the chaff at the LIFT, not the walk.** The first cut flared an
  hour early, ate the rival's last step as well, and killed them in the
  wrong square — which reads as "chaff stops movement" when the lesson
  is that it stops the exit. `tests/test_chaff_strands.py` pins the
  whole path (land → work → jammed pickup → dawn wave →
  `harv_lost_chaff`) and carries the counterweight: an early flare must
  let them get away, or "chaff kills" is satisfiable by a chaff that
  simply kills.
- **On a duel board, post the rival's night before the camera commits
  one.** Two human seats means the first to transmit goes into "waiting
  for the other House", which locks TRANSMIT — indistinguishable from a
  hang, thirty seconds into a shoot. Every `_setup_advanced` branch that
  hands over a PLANNING turn posts p2's night on the way out.
- **Keep the rival's early probes away from both jackpots.** A decoy
  probe within radius 4 of a pure mints its beacon a night early and
  quietly steals the only beat a redsign film has.
- **Land BESIDE a cloud, never in one.** §4.9.3: a cloud already
  standing at hour start denies the landing's auto-harvest, while one
  spawned that same hour does not. The EMP film's second half depends on
  the difference and would look like a bug if it got it wrong.

### Per-card "do this turn" badges

Every chapter in both modes now carries a `todo` — one imperative line,
rendered as a green strip. It is a **sibling of** `.soc-tut-body`, not
the last thing inside it: the body scrolls, and on a long card the one
line the player most needs was sitting below the fold.

## 12. The camera moved out of the browser (v1.35, 2026-08-30)

The verdict on §10's camera, after watching all sixteen films end to
end: **almost every zoom looked wrong, and they looked wrong in the same
way.** Not mistimed and not mis-aimed — §10's assertions had those
covered — but plainly *not a camera move*. A push-in swelled the board
inside a frame that stayed put while the ORDERS panel, both station
rails and the replay bar sat there at their original size, so the eye
read a rendering fault rather than a move toward something.

### What was actually wrong

Scaling one element inside a fixed layout is not a camera. Everything in
§10's numbered list is a consequence of pretending otherwise:

- the board clips against a frame that did not move with it;
- the grid's dotted background magnifies into a pale grey slab;
- four pixel-anchored overlays have to be chased and re-anchored on
  every frame of the glide or they draw the previous board's geometry;
- `#collision-fx-layer` has to be re-parented out of the transform,
  because effects place themselves with a screen-space delta written
  back as a local offset;
- and the camera can only ever look at the map, because the map viewport
  is the element being scaled.

That last one is not a bug, it is a ceiling — and it is the one that
mattered most. `basic_score` exists to show a load becoming a number,
and the two things it is finally about, an arm throwing and a score
climbing, both live in the station rail. The old camera could not point
at either.

### What it is now

`Film.push_in` no longer touches the page. It **measures**: a moment, a
rectangle in page pixels, a ramp. The shoot records keyframes and the
zoom is applied afterwards, in the same ffmpeg pass that already trims
the boot, as a time-varying crop and rescale.

Because the frame is one image by then, nothing can be left behind — an
overlay cannot fall off a board that is a photograph. The re-anchor
chase, the FX re-parenting and the `expect_overlays_anchored` assertion
all went with it. `push_in_on(selector...)` frames anything on the page,
which is what unlocked the score beat.

Three things worth knowing before touching it:

- **`zoompan`, not `crop`.** `crop` evaluates its width and height once,
  at configuration; only x and y are per-frame. A crop that changes size
  over time — which is what a push-in is — cannot be expressed with it.
- **Keep the piecewise expression flat.** Between keyframes the value is
  a constant, the previous target, so each one adds a fixed amount of
  expression. Writing it as "previous expression, then ramp from it"
  instead doubles the string at every keyframe; seven of them produce a
  filter megabytes wide.
- **The trim and the keyframes share a clock origin** (`Film.t0`, set to
  page creation). If the recorder starts a few frames late, both slip by
  the same amount and the cues still land. No sync marker is needed, and
  one was built and then deleted on realising this.

### Do not try to shoot at 2x

The camera crops into the finished frame, so magnification now costs
resolution, and the obvious fix is to capture at 2x and deliver at 1x.
It does not work. Playwright's screencast is captured at CSS resolution
and `record_video_size` only ever scales **down** to fit: ask for
2560x1600 with `device_scale_factor=2` and you get a 2560x1600 canvas
with the 1280x800 page pasted in one corner and mid-grey over the other
three quarters, which the camera then crops into believing it has twice
the resolution it has. Raising `VIEWPORT` would genuinely work, but it
is a different film — the app lays itself out against the window, so a
2560-wide viewport shows attendees a product they will not see.

So the picture softens when it is enlarged. That is the trade, and it
is why the magnifications came down at the same time (`push_in` caps at
2.6x, `push_in_on` at 3.2x).

### The caption lives inside the crop (v1.36)

The first cut of this camera shipped every close-up unnarrated, and the
mistake is worth keeping written down because it is the shape of mistake
this change invites. The caption is `position:fixed; bottom:28px`, which
was correct under the transform camera — the board scaled underneath a
caption that stayed put. Under a crop it is not: the delivered frame is
a 492x308 window out of 1280x800, and the bottom of the *page* is not in
it. The beats worth pushing in on were exactly the beats whose narration
was thrown away.

So the caption now tracks the window. `window.__film.frame(win)` puts it
along the bottom of the current crop and scales it by `1/z`, floored at
`0.65`: below the game's own 13px, glyphs render to mush that magnifying
back up cannot recover, so a close-up caption is allowed to come out
somewhat larger than a wide one rather than soft.

Order differs by direction, and both are load-bearing. A push-in
re-places the caption **before** the ramp — the window only ever shrinks
toward its target, so a caption sitting in the target is in shot for
every frame of the move. A pull-out re-places it **after** — the window
grows away from where the caption is, so it stays in shot the whole way
out, and resetting early would drop it for the length of the move.

`say()` asserts it: the caption's box is compared against the live crop
and the shoot fails if it is over the edge. That assertion, not the
placement, is the real fix — the placement bug was invisible to a shoot
that passed, produced a file of the right length, and had the subject
correctly in shot.

### Framing is the part no assertion can judge

"Is the subject on screen?" is checkable and is checked. "Is this a good
shot?" is not. `FILM_CAM=1` prints, for every move, the subject size,
the solved scale, where the crop lands once clamped to the frame, and
what fraction of the window the subject fills. Two things fell straight
out of it that eyeballing exported frames had got wrong:

- **A subject near an edge never gets centred.** The station rail is a
  122px column hard against the left, so its close-ups clamp at x=0 and
  sit in the left third whatever scale you ask for. The only lever is
  how much of the rest of the screen comes along.
- **Aim for the subject filling 50–80% of the window.** The wreck
  close-ups read at 78%; the first cut of the score beat framed the
  readout alone at 14% and looked like a wide shot with a caption on it.
  The fix was to frame the number *with the station it hangs off* —
  which is also how the beat is described.

### The score beat, since it is the fiddly one

The throw and the fold are about half a second apart and no single
framing holds both: fitting the catapult and the score in together drops
the pair to a fifth of the frame width. So it is two shots — leave the
catapult late (1900ms after VESPERA, by which time the arm has thrown)
and travel fast (a 380ms pan), which lands on the readout while it is
still climbing. Arriving after it settles shows a total; the lesson is a
payment arriving.

## 13. Tactics, and a film in two takes (v1.37, 2026-08-30)

### Prove it headless before you point a camera at it

Every film added here is an argument about numbers — "waiting five
hours is worth more than five hours of walking" — and an argument about
numbers is a thing you can check for free. `backstage/films/_probe_tactics.py`
runs each scenario through the real engine over HTTP and prints the
hold, the harvester's state and the hour-by-hour log. A take costs two
or three minutes; the probe costs one second.

It earned its keep immediately. The EMP walk-in was designed as "wait,
then walk in and harvest a bit more than you would otherwise". The
control run — the same salvo, the same squares, no waits — banked
**nothing at all**, because a hull inside a live cloud is not
merely denied its harvest, it is *disabled* and cannot move either. The
film that got made is a much better one than the film that was planned,
and the difference came out of a probe, not a screening.

`_probe_advboard.py` is the companion: it prints the board around a
coordinate with tiers, purities, four-way comb paths scored, and the
Manhattan-2 diamonds an EMP salvo would cover. Choreography for the
blind grab (which squares to comb, where to put the wall so it does not
cover them) came straight off it.

### Splices

`SPLICES` in `make_tutorial_films.py` maps a delivered clip to the takes
it is cut from. A take is a whole session and a whole night, so
**anything that has to show one move going two ways cannot be one
take** — and for a timing rule, a before/after is often the only honest
way to teach it. `adv_emp` is `adv_emp_rush` + `adv_emp_wait`: identical
salvo, identical harvester, identical five squares, and the only
difference is five WAITs.

The join is a stream copy (`-c copy`), which is only safe because both
takes come out of `_post` with the same codec, size and rate. If the
concat is ever refused, that is a real signal — do not add a re-encode
fallback, find out why the takes diverged. Parts are deleted after the
join and `--only adv_emp` expands to them, so nothing outside the module
knows they exist.

One thing to watch: put queue padding **in front of** the landing, not
between the landing and the walk. Same hours either way, but it keeps
drop/walk/lift in one uninterrupted picker session, which is the
sequence the ORDERS panel is built around.

### `basic_drop` pins its board, and the others still do not

§11 says Basic's lessons land on any terrain. That stopped being true
when `basic_drop` grew a price list: it now hovers four named squares
and says "255 × 3.0 = 765" out loud, then walks a named six-square seam.
So it pins seed 14 (`BASIC_DROP_SEED`) while every other Basic film
still runs on the batch seed.

`backstage/films/_probe_basicseed.py` found it, searching for the one
combination the generator will not promise: one of each RED tier close
enough together to tour with a cursor, beside a six-square *orthogonal*
RED chain. Seed 14 puts the quartet in a 2-square spread and ends the
chain **on the pure**, so the walk pays off the lesson that preceded it.

Two traps, both paid for:

- **The rival wants that seam.** The first cut ran on the Basic preset,
  and RED_HARVEST_LITE dropped onto the walk and rammed the harvester
  head-on at hour five: both hulls damaged, nothing banked. The seam
  this film needs is by construction the richest thing on the board, so
  the bot is *drawn* to it. It is a duel now, rival parked in the far
  corner, like every other outcome film.
- **Do not put the probe on the walk.** A harvester entering a square
  crushes the probe in it, and the film's last beat reads the tooltip of
  a square the harvester walked over. With the eye on the seam that beat
  plays over echo and the −100 never appears. `BASIC_TIER_EYE` sits one
  square south.

### Say the number the tooltip will actually print

The narration quotes scores, so the film asserts them (`read()` checks
the card before the caption claims it). Watch the rounding: the client
does `Math.round`, which takes halves **up**, and Python's `round` does
not. 203 × 1.5 is 305 on screen and 304 in a Python probe. Three of the
four tier squares land on a half.
