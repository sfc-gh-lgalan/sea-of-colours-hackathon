# Improvements, changes and debugging — the running list

The standing backlog for the game itself, opened after the first
hackathon (2026-09-19). `docs/OUTSTANDING_ISSUES.md` remains the **bug
log of record** — a forensic archive, one long entry per defect with root
cause and fix. This is the lighter thing next to it: what we intend to
*build*, what we intend to *change*, and which known defects are still
live, in a form you can scan in a minute and append to in ten seconds.

Nothing here is a decision until it has an owner and a version.

**IDs are permanent.** `BUG-n` / `IMP-n` / `CHG-n` / `NEW-n`, never
reused, never renumbered — they get quoted in commits and comments.

**Status:** `open` · `speccing` · `building` · `shipped vX.Y` · `dropped`

---

## NEW — components that do not exist yet

### NEW-1 · Decoy: a fake pure with a mass cluster — `open`

A droppable that makes a patch of ground *read* to a rival as a pure RED
cell ringed by mass, to bait them into committing a harvester and an
outing to nothing.

**Why.** Every deception in the game today is about *withholding* —
fog, echo decay, probe expiry. Nothing lets you actively assert a
falsehood, so a seat that has read the board correctly can never be
punished for trusting it. The decoy adds the first bluff, and it makes
the existing intel surfaces (redsign, blue-sign, echo) interesting to
reason about rather than simply true.

**The hard question, before any code.** There is currently *no hook for
an object that lies about purity.* A rival's reading comes from one of
two places and both are honest:

- **Live LOS** — `agent_dense_view` reads the real `grid[y][x]` purity.
- **Echo** — `_probe_tile_snapshot` froze the real purity at sighting.

So the decoy needs a new interception point. Favour overriding **at the
per-seat view boundary** rather than mutating the grid: ground truth
stays true, the omniscient observer view stays honest, and replay is
unaffected. Mutating the grid would make the lie real, which is a
different (and much worse) feature.

**Open design questions.**
- Does it also raise a **fake redsign**? This is the whole value
  question. A decoy that only fools a cell inspection is a curiosity; one
  that lights a public jackpot smear is a weapon. It is also the most
  dangerous to balance.
- How is it seen through — adjacency, a probe directly on it, time?
- Can it be destroyed, and does destroying it reveal the bluff?
- Price against `WEAPONISED_BLUE_CAP = 600`. Note one of each existing
  weapon is *exactly* 600, so a fourth kind forces a real choice for the
  first time.
- Does it occupy a surface slot like a probe, and can it be crushed?

**Fan-out.** Full checklist in `docs/ADDING_A_WEAPON.md` — tables in
`game/weapons.py`, six move + six orbit touch points in `policy.py`,
stock/apply/persist/replay in `session.py`, pre-hour placement in
`simulator.py`, then view, agents, UI, RULEBOOK, tests. Attendee forks
copy V12 wholesale, so anything hardcoded here replicates into every
fork in the room.

---

## CHG — rule and mechanic changes

### CHG-1 · SNAP becomes a free slot, and outranks chaff — `1a shipped v1.57, 1b open`

Two separable halves. **Do them in this order**, because the first is
small and self-contained and the second is engine surgery.

#### CHG-1a · Chaff no longer cancels a SNAP — `shipped v1.57`

A round outruns a flare **going up**; it does not outrun a window
already **standing**. So a SNAP declared for the hour a flare is fired
lands and spends its charge, and one declared for the carry-over hours
is cancelled as `chaffed` with the charge intact, like any other jammed
launch. Chaff still answers a SNAP — it has to be up first, which makes
it a read rather than a reaction.

**The fix was one word**, and that is worth recording. The engine
already drew exactly this distinction for the flares themselves:
`already_jammed` is a standing window, `jammed_now` is that *or* a
flare fired on this hour. The SNAP stage was gated on the second and
now gates on the first. Pricing needed no code at all — a round that
fires spends itself, and one that does not never reaches
`apply_snap_launch`, so it drops to the main dispatch and keeps its
charge by the existing path.

**Nothing pinned the old rule.** `tests/test_chaff_precedence.py` had
no SNAP case of any kind — SNAP shipped in v1.36 and never got a line
there — so the behaviour asserted in §4.9.4 could have flipped either
way unnoticed. Verified by reverting the engine and watching the new
tests fail, which is the only thing that distinguishes a regression
test from a comment.

**Two stale documents found on the way, both fixed:** §4.9.3's
`emp_first` ordering list — the canonical one, cited by that tag in
code comments — was missing **two stages**, SNAP pre-emption and the
hour-start vision snapshot, because v1.36 wrote them into §4.9.4 as an
"amended" copy instead. And §4.9.5's "chaff cancels launches too"
bullet enumerated only `chaff_flare` and `emp_launch`; it was written
in v1.14, two versions before SNAP existed, which is most likely how
SNAP came to be jammed in the first place — inheritance, not a ruling.

Fanned out to: `simulator.py`, RULEBOOK §4.9.3/§4.9.4/§4.9.5 plus the
changelog, `tests/test_chaff_precedence.py`, V12's `prompt.py` weapon
briefing (both the SNAP and CHAFF blocks), `guide/weapons.html` and
`manual/index.html`.

*(Note for whoever writes 1b: SNAP is **§4.9.4**. §4.10 is Blue-sign.)*

#### CHG-1b · A SNAP hour can also carry a normal action — large

Fire a SNAP and still do something real in the same hour: a second SNAP,
an EMP, a probe, a harvester step.

**This breaks a guarded invariant — budget for that.** One action per
seat per hour is not merely convention. `MAX_MOVES = 21` *equals*
`HOURS_PER_NIGHT = 21` by design, and the simulator actively raises if a
seat ever carries two replay frames in one hour:

```
# Defensive check ... each seat must carry at most ONE replay frame
# per hour-of-night (RULEBOOK §3.10). ...
#   "this violates the one-action-per-seat-per-hour contract"
```

Everything downstream leans on that identity:

- the planetary clock, `current_hour = max(applied) + 1`
- hour restamping, which defines an hour as "the Nth row this seat consumed"
- the replay frame model and the UI's hour transport
- the harness packager, which assumes list position maps to hour

**Suggested shape** (cheapest honest version): SNAP resolves in its own
pre-empt band and does **not** draw from the 21; the paired action takes
the hour and owns the frame. That keeps one *dispatched* action per hour
while letting the SNAP ride alongside, which is closer to how the
pre-empt phases already work than to a genuine second slot.

Needs: RULEBOOK §3.10 and §4.9.4, the duplicate-frame guard, replay
rendering of two events on one hour, agent prompts and the packager, and
the seat-order audit harness re-run — this touches exactly the machinery
the six seat-order bugs lived in.

---

## IMP — improvements to what exists

### IMP-1 · Turn lab: a gallery of turns, not a dropdown — `open`

`/lab` is currently a single `<select>`. Replace it with a grid of
thumbnails, each showing the board as it stands, selectable and playable.

**The constraint that shapes the whole design.** `turnlab/README.md` and
`turnlab/tests/test_lab.py` forbid a page-local board renderer — the lab
renders nothing and simulates nothing, and a second renderer drifting
from the real one is precisely what that rule exists to prevent.

So thumbnails are **photographs of the real UI**, generated offline by a
Playwright batch (the pattern already exists in `turnlab/probe.py`) and
checked in beside the boards. The real UI stays the only renderer;
clicking a tile keeps the existing flow, `POST /api/lab/open` then
`/?session=…&lab=…`.

Also wanted: the hand-written `NOTES` blurb on each tile, the seat and
day, and whether a fork baseline exists for it.

### IMP-2 · Anatomy of a turn — `shipped`

Animated reconstruction of one real agent turn: state arrives, compilers
run, the prompt assembles section by section, THINK, PLAN, packager, the
turn fires. Text-drawn machinery, unlit until the line reaches it.

Driven by a real captured turn — `weaponx_chaff` on the dual-discovery
redsign board, armed with one chaff, taking `CANCEL_H1 +
BLIND_GRAB_THEIRS`: erase the rival's hour-one grab on the pure they
just lit, absorb its own two jammed hours, then comb the pure itself.

- `guide/anatomy.html` — the page. Self-contained, opens off disk.
- `scripts/export_anatomy_data.py` — `--search` / `--capture` / `--build`.
- `backstage/probes/_probe_anatomy.py` — run after touching the page.
- `backstage/films/make_anatomy_film.py` — shoots `anatomy.webm`.

**Two rules it exists to keep.** Every word on screen is real and comes
from the capture; no timing on screen is real, because the compilers run
in one pass and the prompt assembles in a single call. The page says so
itself — an exploded diagram that did not admit it was exploded would be
teaching the wrong model of the machine.

**The rig is a commit graph, not a row of boxes.** The trunk is the
turn; each prompt-writing module is a branch that forks at `PROMPT
ASSEMBLY`, takes a commit per block it writes, and merges its text back
in at `THINK`. That shape was chosen because boxes-and-arrows drew ten
things in a row and so implied ten things *happen* in a row, which is
the one fact about this pipeline that is false — and because a module
that writes three separate blocks reads as one long-running branch
rather than three unrelated modules. Rails are drawn cold and inked as
the turn crosses them; a marching-ants dash and a bright head carry the
flow. `_probe_anatomy.py` checks every rail is inked by the end, since
"nothing inked" and "everything inked from the start" photograph
identically to a working page.

**The plan is drawn on the position pane, in the same language.** An
SVG registered to the board's own coordinate space: a gutter along the
bottom carrying one commit per ordered move, and branches rising off it
to the cells where something lands. A unit that walks is a route of
connected commits, because a walk is the only thing in a plan that is a
path rather than an event; a pickup has no cell of its own and is drawn
wherever its unit got to. The probe draws its real vision radius and
the redsign is marked, so the schematic shows what the plan was aimed
at as well as what it did.
What earns the gutter its space is the two hours that branch to
nothing: the chaff at h1 and the two hours it jams itself for. A move
list can only say `WAIT (own jam)`; the schematic shows a weapon hour
as an hour spent. `9 of 21 hours` is imported from `HOURS_PER_NIGHT`
rather than typed, since a hardcoded 21 in a guide page is a copy of an
engine constant nobody would think to grep for.
Unlike the rig, the plan's cold track starts *hidden* — up there a
drawn-but-unlit rail lets you see where the turn is going, down here
the same thing shows the route before the model has chosen it. Worth
knowing why that was hard to spot: `vector-effect: non-scaling-stroke`
makes the browser measure the dash pattern in screen pixels while
`pathLength="1"` normalises the path to one user unit, so the draw-on
reveal silently degrades into a permanently visible 1px dashed line.
The plan's strokes are therefore sized in board units, and the probe
now counts inked rails *at open* as well as at the end, because a
spoiler is invisible in a final-frame screenshot — by then the route is
supposed to be there.

**Every node deep-links into `manual/agent.html`.** Stage and module
both carry the chapter (`s03` vision, `s04` the smear, `s06` the option
menu, `s09` the packager…) that explains them at length, and the CLI
readout under the graph prints it — a tooltip would be invisible in a
film. The links are relative (`../manual/agent.html#s03`) so they work
off disk and under `/guide/` alike, and the probe resolves every
fragment against the real file, because a renamed section there leaves
this page full of links that scroll to the top of the manual and look
perfectly fine.

**The assembly act is per-module, and the attribution is derived, not
declared.** The prompt is not typed out in one go: each block is stamped
with the module that wrote it, that module's pill fires in the rig while
the block compiles, and a handover to a different module buys a longer
dwell. Ownership is found by searching the fork's own source for the
verbatim header (`=== SELECT ===`, not `SELECT` — the short form is in
half the tree), which is why it survives a fork reorganising its prompt;
the seven writers found in the shipped capture include two modules
`weaponx_chaff` invented, `strategy_menu.py` and `tactics.py`. The rig's
prompt pod is populated from that same list, so a pill can never light
for a module that wrote nothing. `_probe_anatomy.py` fails if the blocks
come back attributed to fewer than four modules — a page that renders
beautifully while claiming one module wrote everything is the exact lie
this act exists to prevent.

**What it is coupled to.** Any V12 *descendant* — which is all 33
harnesses in the tree. Two hard dependencies, both on V12's shape
rather than its identity: `_split_sections` keys off `=== HEADER ===`
in the rendered prompt, and the exporter reads V12's envelope extras
(`thinker_prompt`, `plan_prompt`, `thinker_reasoning`,
`option_menu_block`, `selected_option_ids`). A harness written from
scratch that kept neither would produce one giant panel, or an empty
page. The nine non-prompt pods are also a hand-written candidate list,
now filtered against the captured harness's own tree — it had already
rotted, naming four files that did not exist. And a capture naming a
harness that is not on disk is now **refused**: most harnesses here are
untracked working copies, and without the source every block silently
fell back to `prompt.py (guess)` with a zero line count and the rig
drew one lane.

**Captures live in slots, and the rig is solved from whichever one is
built.** `--slot NAME` freezes a capture beside the others; `--build
--slot NAME` swaps which agent stars, with no re-capture and no model
call. The film is `cadogan_sinopec_finally_came_up_with_agent_name`;
`weaponx_chaff` keeps the unnamed slot and is one `--build` away:

| slot | agent | panels | lanes | moves |
|---|---|---|---|---|
| `cadogan` | `cadogan_sinopec_finally…` | 51 | 13 | 13 |
| *(default)* | `weaponx_chaff` | 20 | 7 | 9 |

That spread is why the rig's geometry is now *solved* rather than
fixed: lane spacing, band height, branch-label type size and the
fork/merge skew are all derived from the lane count, and stage gaps are
pinned to the width a caption needs with the remainder given to the
assembly stretch. V12's shape hardcoded drew 13 lanes off the top of
the frame and squeezed the stage captions to 50px. The film script
likewise waits for the timeline to report itself finished instead of a
fixed duration — 20 panels is ~16s, 51 panels is ~31s.

**Still open:** the page is not linked from `guide/index.html` or the
landing page yet, so nothing discovers it.

### IMP-3 · GIF-based camera graphics — `open`

Pre-rendered frame assets for the graphics a camera sees, instead of
photographing the live rAF/CSS animations the player sees.

**Why, with numbers.** Measured 2026-09-21 chasing judder in the
zoom-out clips. Chromium hands Playwright **25fps and nothing changes
that** — the 50fps delivery is a `zoompan` crop advancing twice per
browser frame, which smooths the *camera* and can do nothing for the
*board*. Against that ceiling the orbital lifter is the one animation
that falls apart: it covers **42 cells in 1100ms**, eased to be fast at
the edges ("high orbital velocity") and slow at the target ("burn"), so
the approach lands in frames spaced **8.6, 5.0 then 3.7 cells apart**.
It never travels — it appears in four scattered places and snaps onto
the harvester. For contrast the harvester's own step moves a third of a
cell per frame and reads fine, so this is one animation, not a general
smoothness problem.

**Why this entry exists rather than a fix.** The remedies all land in
`app.js` — `DURATION` and the easing are closure constants, not CSS, so
there is nothing the film rig can inject. Reshaping the game's feel to
flatter a trailer shot was considered and **rejected as the tail
wagging the dog**. That rejection is what leaves the gap: there is
currently no way to give the camera different graphics from the ones
the player gets.

**Open questions, none settled.**
- **Scope** — film-only overlay, or does the game render from the same
  assets? Film-only changes nothing for players and is the cheap
  version; shared assets is a rendering change with real fan-out.
- **Authoring** — where do frames come from? Shot off the real UI (the
  `turnlab/probe.py` / `_probe_*` pattern), or drawn? Anything drawn by
  hand is a second renderer that will drift from the first, which is
  the trap `turnlab/README.md` already forbids for board thumbnails.
- **Format** — GIF caps at 256 colours and 8-bit alpha; APNG/WebP or a
  sprite sheet may be the honest answer under the same idea.
- **Registration** — a lifter arc is positioned per event, so an asset
  has to be placed and scaled against board coordinates, not dropped in
  as a fixed overlay.

**Prior art in the tree.** `backstage/probes/_probe_replay_motion.py`
measures per-frame sprite travel in cells and is how the numbers above
were taken — start there to re-check any of this, and note its two
recorded traps (`open_season` parks the strip at the end of the season,
so playback halts instantly and looks exactly like "nothing animates";
and whole-frame differencing cannot see a single sprite move).

### IMP-4 · A session boots rather than appears — `shipped v1.53`

A game or a replay no longer arrives mid-sentence. The page opens on a
black curtain carrying the `sea_of_colours` wordmark and a loading bar
fed by the real fetch stages; when the board and seats are in hand the
curtain lifts, fog spreads outward from the centre of the map in a
slightly ragged ring, and the stations teleport in one after another —
each hull ramping through the unicode density blocks `░▒▓█` in hot red
before cooling to its seat colour. Then the ordinary float resumes and
it is a normal session.

**Why.** Opening a season showed a half-built page assembling itself:
an empty grid, then stations popping in, then scores. The load was
visible and the arrival was not, which is the wrong way round for the
one moment the player is definitely looking at the screen.

**What it is built from, and the two constraints that shaped it.**

- The curtain is **static markup in `index.html`**, not built by
  `app.js`. A curtain the script creates is one the player has already
  watched the page assemble behind — it has to be up for the first
  paint. It sits at `z-index: 100000`, well clear of the highest thing
  in the sheet (the results pill at 12000), because one layer poking
  through is the whole feature failing.
- The fog reveal is **CSS per-cell `animation-delay`** off distance to
  the map centre, corrected for the cell's aspect ratio so the wave is
  a circle and not an ellipse. It animates `filter: opacity()`, not
  `opacity`, so it composes with the dimming an observer view has
  already applied rather than overriding it and snapping back.
- The platforms arrive as a **relay, not a rank**: each starts part
  way into the one before it, so the first is settling while the last
  is still coming up. Both dials are shares of a platform's own run
  rather than millisecond literals, which is the only reason the
  stagger survives a retune — and the share is tuned against a
  *measurement*, because the arithmetic is badly wrong here. The dials
  add up to a ~1080ms arrival; the diamond's rows bunch the wave and
  the real figure is ~700ms, so spacing off the sum left the trailing
  platform 16% materialised when the leader landed, which reads as
  queueing. The probe prints the fill curve — read it before touching
  either number.
- The teleport needed the hull to stop being one text node, so
  `_osRebuildDiamond` now emits **one `<span>` per block glyph**. That
  is a metrics risk and is pinned as one: the probe A/Bs the hull's
  box against the collapsed text and holds it to a quarter-pixel,
  because `_osVaultAnchor` and the catapult arcs measure off it.

**Replay entry changed with it.** A finished season now opens at day 1
and plays the animation, rather than jumping to the last day and
throwing the SEASON VICTOR sweep at you on arrival. The results are
still one button away and still fire if you play through to the end.

**`?boot=0` turns the whole thing off**, and the film rig sets
`window.__SOC_NO_BOOT__` at document start for the same reason — a
trailer clip that opens on two seconds of curtain is a wasted clip.
`backstage/probes/_probe_boot_animation.py` covers the lot, `--strip N`
shoots a filmstrip of the sequence.

**Except the four season-start stills, which open on it.** A
locked-off wide of a season's first night is the one shot the boot
belongs in, and it turned out not to be filmable in place: the real
boot fires when the maps arrive, which on a Snowflake season is
minutes before the take (the shoot waits on the replay strip, which
lands much later), on the seat view rather than the observer's, and on
whichever day the session opened at. So `app.js` exposes
`window.__socBootReplay(holdMs)` — `_bootRearm` + `_bootReady`, the
pair NEW GAME already goes through — and the rig calls it at the top
of the take. It is the shipped animation on the shipped path; the only
invented thing is the progress bar's pace, which walks the real stage
names in the real order. See `backstage/films/README.md`; the shoot
fails loudly if the hook goes away, rather than quietly delivering
four stills of a board that was already there.

---

## BUG — known defects, still live

Triaged out of `docs/OUTSTANDING_ISSUES.md`, split by how much we
believe them. Anything fixed moves to that log with a root cause, not
to here.

### Probably real — worth reproducing first

- **BUG-1** · Reloading after you have already submitted gives you an
  **enabled TRANSMIT** again; the committed lock is never restored from
  `pending[MY_SEAT]`. Multiplayer. *(OUTSTANDING #50 tail.)*
- **BUG-2** · ✅ **shipped v1.56.** `check_wiring.py`'s procurement rung
  called `orbit_policy.plan_orbit_actions(view, weapons_enabled=True)`
  directly. Forcing the flag asks "could this policy buy a weapon if
  something asked it to", but the question an attendee needs answered
  is "does this fork buy one" — and those come apart at exactly one
  line. `orbit.py` passes `weapons_enabled=False` in the baseline,
  deliberately; it is one of V12's two gaps and the comment there calls
  it "the line to change". So the check bypassed the very line the
  exercise is about, and a fork that had wired its weapon correctly
  everywhere else got a green rung and then played every season with an
  empty rack.
  Not hypothetical: of the 24 forks on disk passing `False`, three wire
  a weapon verb, and **`glaxosmithkline_reaper`** is the case in full —
  the old check reports "can buy chaff ✓" where its real orbit phase
  buys nothing at all.
  The rung now drives the fork's own `orbit.submit_orbit()` with a spy
  substituted for the module's `soc_engine`, so nothing is written
  anywhere and what is measured is what the game would do. That also
  picks up `apply_economy_gate`, which runs after the policy and can
  strip a buy on its own — equally invisible to the old direct call. If
  the fork's orbit phase cannot be run at all the rung **fails**; it
  does not fall back to the easier question, since quietly degrading is
  how this got shipped. *(OUTSTANDING #55 tail.)*
- **BUG-3** · Replay pieces render the **wrong colour during animation**
  and correct themselves when paused. *(REMAINING_WORK #1 — logged at
  v0.9.18, re-verify before chasing.)*
- **BUG-4** · Agents plan drops onto **expired probe coverage**, having
  not modelled the 3-night probe lifetime. *(REMAINING_WORK #3, same
  caveat.)*
- **BUG-5** · `orchestrator_2/evals/cli.py` refuses the `cortex` runtime
  unless `SOC_BACKEND=snowflake`, while the matchup scripts run on
  `memory`. A footgun rather than a crash.
- **BUG-14** · **"CHAFF is the sole mechanic that cancels an H1
  landing" is false, and a play is keyed on it.** Found alongside
  CHG-1a and deliberately kept out of that diff; it is wrong today and
  was wrong before the rules change.
  `tabula_v12/doctrine.py:392` tells every agent that chaff alone can
  cancel an H1 landing and leave the pure on the board, then offers
  CHAFF_INSURANCE — the second wave at the same cell — "KEYED ON CHAFF
  ALONE", in those words. But §4.9.4 says a harvester landing on a
  SNAP-hot cell "never touches down: the drop is refused, the hull is
  damaged in orbit", and "a well-timed SNAP leaves the ore in the
  ground". That is the same two properties. §3.6 is arguably a third
  route, since a drop onto an occupied cell is refused too, and
  §4.9.4 cites it as the shape SNAP copied.
  So a rival who SNAPs your target at H1 beats the grab and V12 sends
  no insurance. The text most likely just predates the weapon — SNAP
  shipped in v1.36 — and **every fork in the tree copied it**, which
  is the argument for fixing it centrally rather than per fork.
  `manual/index.html` carries the same error in its own words: chaff
  can "deny an H1 drop" among "two things chaff can do that nothing
  else can".
  Not yet verified in code — the claim above is read off the RULEBOOK,
  and the honest first step is to play a SNAP-refused H1 landing and
  check the pure survives.
- **BUG-12** · ✅ **shipped v1.51.** The station score never subtracted
  GREEN, all season — a wrong number, not just the missing `-100` line.
  One function (`_osGetCumulativeScore` in `app.js`) summed shipped RED
  only, so 75 of 88 seats across the 44 finished seasons read high,
  worst +1000; one seat finished on −94 while its station showed 206.
  The per-night charge was already on the wire and ignored. Root cause,
  the audit and the fix: `docs/OUTSTANDING_ISSUES.md` #57. Instruments
  kept: `backstage/probes/_audit_green_accounting.py` (engine vs
  parcels, every season) and `_probe_green_lines.py` (the browser).

### Deferred on purpose — cost is understood, appetite is not there

- **BUG-6** · Four-player Snowflake seasons are slow. The night sim is
  in memory; the cost is persistence payload. P1 and P3 shipped; P2
  (four dense per-seat grids per replay frame), P4 (`json_state` growth)
  and P5 (redundant hydrate after submit) remain. *(OUTSTANDING #11.)*
- **BUG-7** · The option menu still prices **colliding chains
  independently**, so two options that cannot both be taken are ranked
  as if they could. Re-pricing after each selection is the fix.
  *(OUTSTANDING #54 tail.)*
- **BUG-8** · Two harvesters converging on an occupant **who has their
  own move queued** is left alone. Known gap in the collision pre-pass.
  *(OUTSTANDING #56.)*

### Rule design, open

- **BUG-9** · GREEN jettison is a tax and a chore with no upside. Needs
  green-heavy seasons to validate a redesign.
  *(`docs/RULES_PENDING_REVISION.md` §2.)*

### Local hygiene

- **BUG-10** · ✅ **shipped v1.56.** Logged as housekeeping — "collected
  league forks are sitting in this working tree" — and that was the
  wrong diagnosis. Both failures trace to a *single* directory,
  `redwatch_reaper`, and one of them is a defect in the guard itself.
  Neither would have been fixed by deleting anything.
  `test_a_forks_tests_stay_inside_the_fork` searched shared tests for
  the **substring** `harnesses.<fork>`, which cannot tell an import
  from a string that merely looks like one. `test_agent_manifest.py`
  asserts on an expected locator *value* in a `tmp_path` fixture and
  imports nothing, so a correct test went red the moment somebody
  minted an agent with a name the suite had already used as an example.
  The guard now parses the file and matches real imports, including
  `importlib.import_module` with a literal. A guard that punishes the
  wrong file teaches people to work around it, so the distinction is
  pinned by `test_the_fork_guard_reads_imports_not_prose`.
  The second failure was `test_capitalisation_is_normalised_not_rejected`
  minting `Redwatch`/`Reaper`: a dry run correctly refuses a name that
  is taken, which turned a real fork on disk into a failure about
  capitalisation that had nothing to do with capitalisation. It now
  uses `unittest_capitals`, matching the throwaway name the dry-run
  test beside it already used.
- **BUG-11** · ✅ **shipped v1.56.** All 11 tests in
  `tests/test_fork_collect.py` **errored in
  setup** on any machine with git commit-signing enabled. The fixture
  builds throwaway repos with `git commit`, which picks up the
  developer's global `commit.gpgsign`, and a locked signing key takes
  the whole file out:

  ```
  error: gpg failed to sign the data ... device is locked
  fatal: failed to write commit object
  ```

  A fixture repo is scaffolding and should never be signed, and this
  read as eleven broken tests rather than one missing flag.
  Fixed both ways. `-c commit.gpgsign=false --no-gpg-sign` now sits
  alongside the `user.email` / `user.name` the fixture already passed,
  and the file also nulls `GIT_CONFIG_GLOBAL` / `GIT_CONFIG_SYSTEM` in
  an autouse fixture — signing was the symptom, but hooks and commit
  templates leak in by the same route, and `test_soc_push_race.py` was
  already doing this. The two now match.

- **BUG-13** · ✅ **shipped v1.52.** The results card could account for
  42% of the weapons a season fired — 50 fired across the 44 finished
  seasons, 21 printable. SNAP had no row at all despite being counted
  since v1.38, 16 seats showed a kill with no weapon beside it, and no
  effectiveness figure existed (41 of 70 SNAPs bought were never
  fired). Root cause was one fact with two sources: kills from
  `combat_attrib` written at kill time, fires re-derived from replay
  frame tags. Now off the `weapons_used` ledger, with an `// ordnance`
  fired/bought block, a `// losses` block for the four loss counts that
  were computed and discarded, and pre-v1.50 EMP/SNAP misattribution
  reconciled at render. Root cause and audit:
  `docs/OUTSTANDING_ISSUES.md` #58. Instrument kept:
  `backstage/probes/_audit_weapon_reporting.py`, whose `CARD AS SHIPPED`
  section is the live regression check (88/88 clean).
  **Left open on purpose:** a kill by a *decaying* EMP cloud is filed
  under `owner="emp_field"` and lands on no card (latent, 0 in 44
  seasons). Crediting it means deciding whether a delayed kill is the
  firer's — a rules question, not a reporting one.

### Tracker debt — the docs are wrong, not the code

Fixed as part of opening this list, recorded so the corrections are not
silently re-introduced:

- `two_seams_choose_one` is logged red in three places and **passes at
  HEAD**.
- Triage row #36 still says "flagged, not resolved" while its own full
  entry says done in v1.33.
- `docs/HACKATHON_BUILD_PLAN.md` phase 7.5 reads `pending`, but the turn
  lab, `soc.py`, `soc season` and the league all shipped.
