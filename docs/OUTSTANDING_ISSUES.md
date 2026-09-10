# Outstanding issues / bug log

Running log of known bugs and misbehaviours to triage and fix. **Documentation
only — no fixes applied yet.** Logged 2026-07-01.

Severity legend: 🔴 critical (breaks play / multi-client) · 🟠 high · 🟡 medium · ⚪ low.

---

## 1. ✅ (DONE, v1.8) Phantom harvester trails in echo-only areas

**Status (v1.8):** Fixed. Trails are now a **fog-gated observation** rather than
a live overlay.

**Root cause:** `player_dense_view` / `agent_dense_view` re-derived the trail from
the **live** ledger (`_trail_summary` / `_trail_markup`) and stamped it onto
*every* visible cell — including `echo` (stale) and `memory` cells. Because the
ledger updates every night, an echo tile showed traffic that grew in real time
without the seat having current LOS (a genuine fog leak — it affected the AI
world view too, not just the human map).

**Fix:**
- Echo snapshots (`_probe_tile_snapshot`) and memory tiles (`_blend_memory`) now
  **freeze** `trail` + `trail_markup` at sighting time.
- The view builders serve the **live** ledger only on cells with current LOS;
  echo/memory cells serve the **frozen** copy. Fog cells still carry nothing;
  the omniscient observer view is unchanged.
- Edge case: when a harvester crushes a probe by landing on it, `consume_probes_at`
  now freezes a final echo (with the just-laid trail) into the owner's intel — the
  probe "witnesses its killer" — since the dying probe can't re-pulse.

**Tests:** existing `test_player_view_trails_survive_into_stale_memory` now passes
on correct semantics (it previously relied on the phantom leak).

---

## 2. ✅ (DONE, v1.7) Vault reflects 15-square capacity + capacity warning

**Status (v1.7):** Fixed. The VAULT denominator is now uniformly the canonical
**15-slot** capacity (RULEBOOK §3.12), and the VAULT tab pill surfaces a
persistent, at-a-glance capacity warning.

**Root cause (display):** The backend was already correct — `HOARD_CAPACITY = 15`
is enforced in `deposit_haul_to_hoard` (tier-priority displacement, §3.14) and
`inventory_pack` ships `hoard_capacity: 15` (confirmed live: `6/15`). But the
client had **stale capacity fallbacks** scattered across the render paths — `|| 50`
(`fmtInventory`, `_emptyInventory`, INTEL meta), `|| 25` (`renderVault`, INTEL
meta), `|| 0` (`fullVaultPickupWarning`) — so any path that arrived without an
explicit `hoard_capacity` (empty inventory, replay reconstruction, INTEL tab)
rendered a wrong 25/50 denominator.

**Fix (`server/static/app.js`, `styles.css`):**
- Added one shared `HOARD_CAP_FALLBACK = 15` constant and routed every fallback
  through it, so the vault never shows a stale 25/50.
- Added a **persistent capacity warning** on the VAULT tab meta pill in
  `renderVault`: amber `cc-tab-meta--warn` at ≥ 90 % (14/15) and a pulsing red
  `cc-tab-meta--full` at 15/15, each with a hover tooltip explaining that an
  incoming square displaces the lowest-tier parcel (§3.14) and to ship/refine to
  free slots. This complements the existing TRANSMIT-time overflow guard
  (`fullVaultPickupWarning`), which only fires when a pickup is queued into an
  already-full vault.

**Symptom (original):** The vault didn't clearly reflect the 15-square capacity
and issued no standing warning as it approached/exceeded the limit.

**Repro (original):** Fill a vault toward/over 15 squares and watch the count +
warning behaviour.

---

## 3. ✅ (DONE, v1.7) Multiplayer: praxis submission bounced back to the action menu

**Status (v1.7):** Fixed. A multi-human submit whose night did **not** resolve
on our click (other humans still plotting) now holds the player in a committed
"LOCKED IN — waiting for pN" frame instead of tearing the resolving overlay
down and re-enabling the composer.

**Root cause:** `submitSoloNight` (`server/static/app.js`) always ran
`pullAllMaps({playFx:true})` then `endResolvingFrame()` + re-enabled the
TRANSMIT button in its `finally`. In a 2-human game the first submitter's
`POST /policy` returns `night_resolved:false` (the engine only resolves once
`both_ready()`), so there was no cinematic — the overlay just dropped and the
composer came back, reading as "nothing happened" even though the seat was
locked (tick + waiting strip were correct).

**Fix:** When `_multiHumanGame && !body.night_resolved`, `submitSoloNight`
(and `submitSoloOrbit`) now call `enterHumanWaitFrame(...)`: it freezes the
transmit feed on a persistent "you locked in · waiting for other players" line,
repaints the overlay headline to `LOCKED IN — waiting for pN`, ensures the
live-sync poller is running, and returns early. The `finally` keeps the frame up
and the button disabled. The standing `pollLiveSync` clears the frame + drives
the reveal/cinematic when the night resolves (see #4).

**Symptom (original):** In human multiplayer, submitting praxis dimmed the menu,
showed the progress list, then **cleared and returned the player to the action
menu** while a tick appeared next to their name and the UI showed "waiting for
players" — the submission was registered but the flow implied it hadn't happened.

---

## 4. ✅ (DONE, v1.7) Multiplayer: praxis animation broke for every player except the last

**Status (v1.7):** Fixed. All connected clients now play the same clean
resolution cinematic when the night resolves, regardless of submit order.

**Root cause:** The **last** submitter's client resolves the night inside its own
`submitSoloNight`, and its `pullAllMaps({playFx:true})` cinematic runs while
`inFlightSubmit === true`, which suppresses the standing `pollLiveSync` poller —
so it played uninterrupted. **Earlier** submitters had already torn down (bug #3)
and relied on `pollLiveSync` to drive the cinematic; but that poller (a) fired a
full `pullAllMaps({playFx:true})` on *every* status-signature change (including a
bare opponent-lock / pending flip), and (b) had no `_liveFxPlaying` guard, so a
2.5s poll tick landing mid-cinematic re-pulled and half-painted / cancelled the
in-flight animation → "breaks for everyone but the last".

**Fix (`pollLiveSync`, `server/static/app.js`):**
- Skip entirely while `_liveFxPlaying` (never re-pull over a playing cinematic).
- Distinguish a real **resolution** (new replay window, day roll, or phase flip)
  from a mere **opponent-lock** (pending flip, same day/phase): only a real
  resolution drives `pullAllMaps({playFx:true})`; a lock just refreshes the
  waiting strip + phase line via a light `refreshStatus()`.
- On resolution, clear the committed-wait frame (#3) and let the shared cinematic
  path play + drop the overlay (`pullAllMaps` still frame-gates via
  `newDayLanded`, so the resolver never double-plays).
- The resolver re-seeds the poller baseline (`liveSyncSig = ""`) on teardown so
  its own next tick doesn't replay the night as a redundant reveal.

**Symptom (original):** The full praxis animation began for all players (menu
dims, orbital commands appear) but the sequence broke for everyone except the
last to submit; only the last player got the intact animation → orbital flow.

---

## 5. ✅ (DONE) "LAUNCH DELAY" debug button leaks into the replay toolbar

**Status (v1.4):** Removed. The debug button is gone; the orbit→surface
sequencing it hinted at is now a proper **Settings** toggle (`toggle-orbit-seq`,
"sequence orbit launch → surface", default ON), persisted to localStorage. Drops
and probes lead their board effect behind the orbital launch animation.

**Symptom:** A `LAUNCH DELAY: ON/OFF` button appears in the replay toolbar. It
is an undocumented debug control — the user doesn't know what it does.

**What it does:** Injected by `_osInjectSetting()` in
`server/static/station.js` (only when the optional station UI is active). It
toggles `window._osLaunchDelay` between 0 and 220ms, which staggers the map's
orbital arc animation ~220ms behind the station-panel canvas animation (see the
launch-delay branches in `server/static/app.js` around the `drop` / `probe`
delta handlers).

**Expected:** It should not live in the replay strip. Move it into the Settings
panel alongside the other orbit-report toggles (auto-pop reports, last-turn FX,
cinematic title cards), persisted to localStorage so the choice survives
reloads, and gated to when the station UI is active.

**Repro:** Open `/watch.html?station=1&...`, load a replay — the button appears
in `.cc-replay-toolbar`.

---

## 6. ✅ (DONE, v1.7) Board state pops before its animation lands (drop / probe / harvest / trail)

**Status (v1.7):** Fixed across all four action effects — the board now holds its
pre-action look until the corresponding sprite actually lands, in the live
"PRAXIS BEGINS" cinematic, replay autoplay, and forward-step, in any perspective
(OBS or a single seat's fog view).

**Root cause:** `paintReplayFrameOntoMain` paints the fully-resolved end-state
frame first, then flies the delta animations over the top. Only a narrow
`step`-destination recolor (3 hard-coded colours, read from the *observer* cells)
was deferred; everything else painted instantly:
- **Harvester drop landing square** recolored to harvested (green/red → dark) the
  moment the frame painted — the drop branch deferred only the *revealed vision*,
  never the landing cell's own recolor (confirmed on a live session: landing
  `(12,11)` `rgb(63,185,80)` → `rgb(14,11,22)`).
- **Probe vision** popped its whole disk instantly: the reveal mask
  (`collectNewlyRevealedCells`) used a Euclidean **radius-2** gate, but a probe
  opens a **Chebyshev-4** square (~49 cells) — the entire outer ring was never
  masked.
- **Harvester walk** recolor + the freshly-laid **trail** glyph popped before the
  sprite arrived.

**Fix (`server/static/app.js`):**
- Added a general `_layTerrainStandin(cell, beforeCell)` that overlays a tile with
  its pre-action look (bg / fg / trail / terrain char) read from the **viewing
  seat's own** dense cells (`_frameSeatCells`), removed at the landing beat.
- `step`: stand-in on both the destination (recolor) and the departed cell (new
  trail), lifted on the 160ms step land.
- `drop`: stand-in on the landing tile, lifted at the lifter's deposit beat
  (`_onDeposit`, arc midpoint t=0.5), alongside the existing vision reveal.
- `collectNewlyRevealedCells`: dropped the radius gate — every fog→visible cell on
  the frame is masked (each frame is one unit's action), so the full probe disk
  (and drop reveal) stays dark until the streak/lifter lands.

**Symptom (original):** For a split second harvesters rendered in their landed
positions, resources showed as harvested, and probe vision opened before the
animations played — the board "moved" a beat before the sprites.

**Repro (original):** Watch a replay/live night with drops + probes and step/play
forward onto those frames.

---

## 7. ✅ (DONE) End-game report combat detail with per-player colour tallies

**Status (v1.6):** Full **attacker→victim kill-feed** shipped — the earlier v1.4
victim-side-only limitation is resolved. The engine now accumulates a per-season
`combat_attrib[stat][attacker][victim]` matrix at each event source during the
night sim (persisted in `json_state`; tiny) and `get_endgame_summary` exposes a
per-player `killfeed` (aligned to a fixed `combat_seats` order) plus a `personal`
ops block. The results screen renders the kill-feed **under each agent card**:
one row per stat with N slash-separated counts, each number in the **victim's**
seat colour (FPS-style; a player's own column is included since you can crush
your own probe). Stats: probes crushed, probes superseded, EMP'd probes, EMP'd
harvesters (distinct), chaff jams, harvesters damaged, harvesters lost (only
when your chaff cancelled the egress). Personal: probes launched, harvesters
dropped/recovered, red/green/blue harvested, EMPs/chaff/mines fired, and the new
**moves cancelled** (queued moves that resolved invalid/cancelled at execution).
Covered by `tests/test_v16_killfeed.py`. Attribution only populates for games
run after v1.6.

**Status (v1.4, superseded):** Implemented as a per-seat "// combat log" (see
#8). Each attrition category showed every seat's season total in that seat's
colour (the mine/enemy split). Limitation (now fixed): attacker attribution
(whose unit killed whose) wasn't recorded, so the coloured counts were
victim-side / own actions rather than a true attacker/victim breakdown.

**Symptom:** The end-game report is too thin. It doesn't break down what actually
happened in combat/attrition over the season: harvesters crashed, EMPed, and
destroyed (and the same for probes).

**Expected:** Add richer end-game stats. For each attrition category
(harvesters crashed, probes crashed, EMPed, destroyed, …) show **coloured
numbers per player** describing who did it to whom.

- Format like `1/1` where the split is by owner colour: the **first** number in
  *my* colour, the **second** in the **enemy** colour.
  - Example: if I crashed my harvester against the red player once, it reads
    `1/1` — the first `1` in my colour (my harvester lost), the second `1` in
    the enemy's (red) colour (their involvement / kill).
- Apply the same coloured-tally treatment across the categories: harvesters
  crashed, probes crushed, EMPed, (units) destroyed.

**Notes / to investigate:**
- Where end-game stats are aggregated (session/season summary) and what
  attrition events are already recorded per seat (crash / EMP / destroy).
- Decide the exact semantics of the `mine/enemy` split for each category so the
  two coloured numbers are unambiguous (attacker vs. victim vs. mutual).
- The end-game report renderer (client) needs per-seat colour styling for each
  number.

**Repro:** Finish a season with combat (crashes / EMPs / destroys) and open the
end-game report — the attrition breakdown + coloured per-player tallies are
missing.

---

## 8. ✅ (DONE, replay) Final game-resolve simulation step (last catapult launch + vault cleaning)

**Status (v1.4):** Implemented for the **replay** path + winner celebration.
- Backend already resolves the final settlement orbit on `day = cap + 1`
  (`catapult_by_day[cap+1]`, `station_obs_by_day[cap+1].post`, `SEASON_COMPLETE`).
- `buildReplayTicks` now appends a terminal **`resolve`** tick when the season is
  complete and `catapultByDay[cap+1]` has activity.
- `paintReplayFrameOntoMain` handles the `resolve` slot (sunset film + "SEASON
  RESOLVES" card), and `station.js` `osOnReplayTick` runs the DUSK stage then
  FIRES inline (no night frame follows): catapults launch, `+X` folds into the
  score, blue consumes, vaults settle to `post[cap+1]`.
- A full-bleed **winner celebration** overlay (`#cc-victor`, winner-colour sweep +
  "SEASON VICTOR") plays after the resolve beat, then the results modal opens.
  Results auto-pop is deferred until the finale completes (`triggerSeasonFinale`).
- **#7 combat detail** shipped alongside and was later upgraded in **v1.6** to a
  full attacker→victim kill-feed rendered under each agent card (see #7 above).
  The v1.4 "// combat log" (victim-side season totals in seat colour) is
  superseded by the per-card kill-feed + personal ops block.

**Follow-ups (v1.5 update):**
- ✅ **Live path DONE.** `osOnLiveResolve` (station.js) now plays the inline
  final-settlement animation (last launch + `+X` fold + blue burn + vault
  settle) on season-complete, and the winner celebration is deferred until it
  finishes (`handleLiveSeasonState`). The confetti-before-resolve ordering bug
  (both replay and live) is fixed — the celebration lands after the final scores
  are on the board.
- ✅ **Refine-preview UX DONE (part b).** Queuing a refine now optimistically
  removes consumed inputs and mints a white-bordered **"pending"** parcel across
  the vault tab, both composers, and the orbital vault; pending outputs are
  locked out of ship / re-refine. See RULEBOOK v1.5.
- ✅ **Final-orbit dead-refine RESOLVED (part a) — via the Final Refinery, v1.7.**
  The tier-weighted fire-sale plan was **rejected** (shipping is the game; the
  fire-sale stays `0.5 × purity`, no tier weight). Instead the **terminal orbit**
  now runs an uncapped **Final Refinery** (RULEBOOK §4.3.1): the 3-action and
  5-parcel refine caps are lifted, same-turn trace→vein→mass cascade is allowed,
  and refined parcels can ship the SAME turn via a new auto bid
  (`ship_catapult{auto:true}`) plus a one-click `refine_cascade{target_tier}`.
  So leftover BLUE finally has an aggressive use and the last-orbit refine is no
  longer dead. Engine + parser + resolver + UI + agent-view + heuristic/pilot_v2
  doctrine + `tests/test_final_refinery.py`. See `docs/RULES_PENDING_REVISION.md`
  #1 (marked shipped).

<details><summary>Original report</summary>

**Symptom:** At the very end of the game there's no dedicated resolve/cinematic
step, so the **final** orbital resolve isn't shown: the last catapult launch and
the vault cleaning (final depletion) aren't played out.

**Expected:** Add a final "game resolve" simulation step as the last beat of the
season that plays the closing orbital resolve — fire the **last catapult** and
run the **vault cleaning** (empty the vaults) — so the season ends on a complete,
visible resolution rather than cutting off before the final launch.

**Notes / to investigate:**
- The replay/cinematic pipeline (`buildReplayTicks` / `_playNightCinematic` in
  `server/static/app.js`, plus the station DUSK/praxis beats in
  `server/static/station.js`) — add a terminal resolve tick after the last night.
- Ensure it composes with the DUSK→praxis catapult ghost model (final launch
  fires, then vaults clean to empty).

**Repro:** Play/replay a full season to the end — the last catapult + vault
cleaning are never shown.

</details>

---

## 9. ✅ (DONE) H0/DUSK step + live-return bugs (v1.5)

**Fixed:**
- **LIVE after orbit landed on the previous dawn ("H25"), stuck.** LIVE now
  re-stages the DUSK/RESOLVE beat (loaded catapults, `+X` lines, blue lift) when
  the committed orbit's night is unplayed (`planning`) or the season closed,
  instead of snapping to the stale dawn tick and clearing the staging.
- **`+X` score sometimes never folded into the total.** `_praxisPending` is now
  armed on every DUSK staging path (forward, scrub-onto-DUSK, and live
  `osOnLiveDusk`), so the pending gain always folds at the next praxis frame.
- **Enemy blue bar always read "full."** Rescaled to ~300/pip fractional (see
  RULEBOOK v1.5) so 139 / 279 / 941 / 1358 blue read as distinct levels rather
  than all pinning to 5 pips. (Root cause: it counts the invisible blue bank; a
  4P game genuinely had seats holding 900–1350 banked blue.)

## 12. ✅ (DONE, v1.6) Probe trails missing in the live "PRAXIS BEGINS" cinematic

**Symptom:** Probe flight streaks didn't render during the live night cinematic
("praxis begins"), but scrubbing/replaying the same night showed them fine. EMP
streaks were unaffected.

**Root cause:** Probe launches are held back by the orbit→surface sequencing lead
(`_osOrbitLeadMs("probe")` = 1600ms, Option B default ON), so the streak is
scheduled via `setTimeout(_probeGo, 1600)`. The live cinematic loops held each
tick only ~620ms before painting the next tick (which repaints the board), so the
deferred streak fired against a stale/detached cell → nothing drew. EMP streaks
spawn immediately (no lead), so they were fine. The replay ticker already waited
`window._osPendingDwellMs` (`_maxLead + 1300`), which is why scrub/replay worked.

**Fix:** `_playNightCinematic` and `_playLiveTurnAnimations` (`server/static/app.js`)
now reset `window._osPendingDwellMs` before each tick paint and hold
`max(620ms, pendingDwell)` after — mirroring the replay ticker — so the deferred
probe streak completes before the next repaint.

## 10. ✅ (DONE, v1.9) Vault ↔ Orbital Vault ↔ actual content can still disagree

**Symptom (user, v1.4):** The VAULT tab, the orbital-station vault, and the true
game content disagreed at times, especially around the H0/DUSK transition.

**Root cause:** All three surfaces reconcile to the same authoritative hoard **at
rest** (tab + station both read the live inventory in play, or the per-tick frame
hoard reconstruction in replay) — *except* across a DUSK shed. The DUSK **forward**
beat renders the pre-orbital vault and then animates it down by popping only the
**red/green catapult parcels** (`_scheduleDepart`). Its settle callback re-synced
**only the rival bars** to the post-orbital snapshot; the observed vault was left
at whatever the shed produced. Anything the shed doesn't model — **burned BLUE
fissile, a tier-priority overflow eviction, or a refine's byproducts** — left the
station diamond over-counting versus the VAULT tab (which reconstructs
authoritative POST on the next tick). Live DUSK (`osOnLiveDusk`) had the identical
gap and no reconcile at all.

**Fix (`server/static/station.js`):**
- New `_osReconcileObservedVaults(preferLive)` snaps every **observed** vault back
  to the authoritative post-orbital hoard (rivals still get the bar estimate).
- Replay DUSK settle now calls it (was rival-bars only), and live DUSK schedules
  it once the shed dwell has played (`preferLive` reads the server's true
  post-orbital `lastLiveInventory`, since no night frame exists yet to
  reconstruct from). Both reconciles ride `_vaultSettleTimers`, so a scrub/resolve
  clears them cleanly.

**Audit tool:** `window._socVaultAudit()` (in `app.js`) prints, per active seat,
`tab / station / authoritative` counts and warns on any drift — a one-call repro
capture (`[vault-audit] replay day 3 (tick 42): diamond disagrees …`) for any
residual desync.

## 11. ⏳ (INVESTIGATED) 4-player Snowflake praxis is slow

**Finding:** The praxis/night simulation itself is fully **in-memory** — there
are **no per-hour / per-seat Snowflake writes** in the sim loop, and the
`auto_fire_bot_seats` per-seat full-save regression was already fixed (v0.9.8,
one batched save per TRANSMIT). The 4P slowdown is **persistence payload
amplification**, not un-batched per-step sim writes. Prioritised culprits:
- **P1:** ✅ **DONE (v1.5).** `last_night_replay` was serialized **twice** every
  night — inside the `json_state` MERGE (`session.to_dict()`) *and* into
  `SOC_REPLAY_FRAME`. Fattest on 4P. `_save_session_row` now pops
  `last_night_replay` from the persisted blob (frame table stays authoritative;
  `to_dict()` and its in-memory round-trip test are unchanged). Verified nothing
  reads it back after a reload — `get_replay` and evals both read the frame
  table.
- **P2:** Replay frames carry **four** dense per-seat cell grids
  (`cells_player_p1..p4`) → ~2× SQL text / PARSE_JSON vs 2P. Fix: store one
  shared grid + per-seat fog deltas. *(Deferred — schema/format change, not
  batching; the HTTP wire already strips these aliases.)*
- **P3:** ✅ **DONE (v1.5).** `append_agent_invocation` was **unbatched** (SELECT
  MAX(seq) + INSERT per bot → ~6 round-trips for 3 bots). Added
  `append_agent_invocations([...])` to every store (Snowpark = one MAX + one
  multi-row INSERT with `PARSE_JSON` in the outer SELECT; file store batches the
  disk write; composite groups by session). `_persist_bot_fanout` now calls it
  once → 4-seat fan-out drops from ~6 round-trips to 2.
- **P4:** `json_state` grows with players × days (`station_obs_by_day`,
  `orbital_activity_by_day`, memory/probe maps) and is re-MERGED wholesale.
  *(Deferred — blob restructure, larger change.)*
- **P5:** extra `get_session_status` hydrate after every submit. *(Deferred —
  changes the server response contract.)*

P1 + P3 (the highest value / lowest risk batching wins) are shipped. P2/P4/P5
remain — they're structural (schema, blob layout, HTTP contract) rather than
batching, so they're tracked separately.

## 13. ✅ (DONE, v1.17) Multiplayer: the night cinematic depended on which seat you were, and on your tab

The v1.7 work (#3, #4) made the *waiting* seat resolve at all. These are the
three ways it still ended up watching a different turn from the seat that
happened to resolve the phase.

**Symptom (reported):** "when players submit orbital orders … the moves
cycle, and they are allowed to submit moves, but the night begins message
and the heat dissipating animation never trigger, so the image is still
shimmering and the view of the map hasn't advanced to the night stage."
Separately: "in some multiplayer games the end state of the turn is shown
before the simulation animation."

**Root cause A — the orbit night-opening was inline in the submit
handler.** `NOX n BEGINS` + the sunset sweep lived inside the orbit-submit
success path, *after* the `!body.orbit_resolved` early return that parks a
non-resolving seat in the committed-wait frame. So only the seat whose
submit resolved the phase ever ran them. The waiting seat's poller went
straight to `pullAllMaps({playFx, stageOrbitBeat})`, and an orbit resolve
emits no night frames, so there was no cinematic either — it just snapped
to the resolved board. Worse, the no-cinematic branch re-applies heat with
`_heatApplyInstant(host, 0.52)` whenever `_heatPhase === "hold"`, and only
the sunset sweep clears that phase — hence "still shimmering" on a board
that had supposedly moved to night. This was **always**, not just when
backgrounded; the player's "or maybe always" was right.

**Root cause B — the cinematic trigger was a frame diff, so any refresh
could eat it.** `refreshNightReplay` computed `newDayLanded` by comparing
incoming frames against the ones the client already held, which makes the
animation a side effect of *fetch order*. `closeReport()` fires a plain
`pullAllMaps()` on close specifically to pick up "state that landed while
it was up" — including the opponent's just-resolved night. Once it had,
the real cinematic pull saw nothing new and fell into the hard-cut branch.
Two seats closing the orbit report in a different order therefore saw
different turns, which is the "end state before the animation" report.

**Root cause C — a backgrounded tab lost the night outright.**
`pollLiveSync` returns early while `document.hidden` (correctly — rAF is
throttled, so a cinematic started there would stall half-played), and it
treats its first sample as a baseline it never acts on. A player who
submitted and switched away had *no* baseline, because every poll since
had returned before seeding. The first poll after they came back sampled
the already-resolved state, recorded that as the baseline, and returned.
The transition was consumed without ever being played.

**Fix (`server/static/app.js`):**

- `playOrbitNightOpening(day)` — extracted, and called by **both** the
  resolving seat and the live-sync poller, in the same order (card, sweep,
  then the pull). The poller runs it only on an orbit resolve
  (`prevPhase === "orbit"`), since a night resolve is carried by the
  cinematic proper.
- The cinematic gate is now `_lastCinematicDay`: day N animates exactly
  once per seat, whoever refreshed in between. Loads that must *not*
  animate (new game, joining a seat, opening a saved season) pass
  `claimCinematic: true`; the incidental `closeReport()` refresh
  deliberately does not, so it can no longer steal a pending night.
- The start tick comes from `findFirstCinematicTickOfDay(day)` rather than
  a held tick count, which was only correct if nothing else had fetched
  first. It also matches the synthetic DUSK tick (day lives on the tick,
  not a frame) so the orbital-resolve beat isn't silently skipped.
- `seedLiveSyncBaseline()` records the pre-resolve state when the wait
  starts, ignoring `document.hidden` because it only records and never
  paints. A hidden tab now *defers* the night instead of losing it, and a
  `visibilitychange` listener polls on refocus so it plays immediately.

**Tests:** `tests/test_live_cinematic.py` (18) pins each mechanism — there
is no JS runner, so these scan `app.js` the way `tests/test_seat_links.py`
does.

## 14. ✅ (DONE, v1.19) A crushed probe left TWO ghosts of the harvester that killed it

**Symptom (reported):** "if a harvester runs over your probe you are left
with two ghost images of the harvester, not one, as the probe loses life."

Reproduced exactly: the victim's echo showed `harvester_p1` at both `10:8`
(where it stood) and `9:8` (where it had stepped from). Both render as
echo cells, both carry a harvester glyph, and nothing distinguishes the
real one — so the probe's owner is handed a board that lies about where
the enemy is, in the one moment they paid a probe to find out.

**Root cause (`sea_of_colours/game/session.py`).** A live probe refreshes
its **whole vision disk** on every hourly pulse (`_pulse_vision_intel`),
which is what normally retires a sighting: the harvester moves on, the
next pulse re-snapshots the vacated cell as empty, and the old glyph is
gone. The death path (v1.8, "the probe witnesses its own killer") only
refreshed the **single cell the probe died on**. So the echo was stitched
from two different instants — the death cell as of the crush, every other
cell as of the last pulse before it — and because the observer is now
dead, no later pulse could ever reconcile them. The stale glyph became
permanent for the rest of the night.

Worth noting the bug needed *both* halves to appear: the sighting on the
approach (correct at the time) and the fresh sighting at the grave. That
is why it only shows up when a harvester walks in from inside the probe's
own disk — i.e. exactly the crossing the probe existed to watch.

**Fix.** `_freeze_final_probe_echo(probe)` takes the probe's last look
across its entire vision disk, which is precisely the work the hourly
pulse would have done had it survived the hour. One coherent instant, one
harvester. The final echoes are also frozen *after* every probe crushed in
that step is off the board, so two probes dying together can no longer
record each other as still standing.

**Tests:** `tests/test_probe_death_echo.py` (5). They pin the invariant
("the killer appears exactly once"), not the mechanism, plus a guard that
the setup really does sight the harvester on approach — otherwise the main
test would pass vacuously. Verified against the old behaviour in place:
two ghosts before, one after.

## 15. ✅ (DONE, v1.19) A weapon launch did not cost the launcher its hour

**Found by audit,** not by report: the question was "what happens when two
chaffs execute in the same hour?" (§4.9.5). The rule and the engine agree on
the chaff *window* — but the audit found a hole underneath it that needs no
chaff at all.

**Symptom.** A seat could fire a weapon **and** act again in the same hour,
and the extra action damaged harvesters. Minimal case: p1 queues
`emp_launch` then `step`, p2 queues the crossing `step`. Hour 1 emitted
*both* an `emp_launch` frame and a `collision_swap` — and **both**
harvesters came out `damaged`. So it wasn't cosmetic: fire an EMP and still
cripple the harvester walking past you, for one slot.

**Root cause (`sea_of_colours/game/simulator.py`).** EMP and chaff resolve
in the pre-hour phase, which consumes the launcher's slot and records the
seat in `_preempted_seats_this_hour` so the main dispatch skips it. The two
collision pre-passes — pass-through swap (§3.6) and simultaneous drop
(v0.9.10) — run between those two points and peek every seat's *next*
queued move, guarded only by `applied[p] >= MAX_MOVES`. For a pre-empted
seat that next move belongs to the **following** hour, so the pre-pass both
granted a second action and staged a collision between two moves that were
never simultaneous.

**The chaff case is the worse one.** When *every* seat flares in the same
hour they are all launch-hour immune, which is the sole condition that opens
the pre-pass gate (`all_chaff_immune`) while chaff is active. The swap that
leaked through then consumed the very moves the carry-over hours existed to
jam, so hours N+1/N+2 emitted **no `chaffed` frames at all** and the
launchers dodged the self-jam §4.9.5 makes them pay:

> If two seats chaff in the same hour, both fire (each pays cost) and both
> are immune for that launch hour, then both are jammed for the carry-over
> hours.

In other words a mutual flare was strictly *cheaper* than a solo one — the
opposite of the intended trade, and exactly the kind of thing an agent
harness will find and exploit.

**Fix.** Both pre-passes take `skip_seats` and ignore seats already
pre-empted this hour. The invariant is the one §3.10 already states — one
applied action per seat per hour — it just wasn't enforced on this path. The
gate itself is left alone: with the skip in place `all_chaff_immune` can no
longer resolve anything (triggerers are always a subset of the pre-empted),
so no collision semantics change for seats that acted normally.

**Tests:** `tests/test_preempt_slot_integrity.py` (5), including a guard
that a plain weaponless swap still collides — without it the others would
pass by simply disabling the mechanic. Verified against the old behaviour by
stubbing the skip back out: 4 fail, the guard passes.

## 16. ✅ (DONE, v1.19 / RULEBOOK v1.14) Chaff could not stop a weapon, and could be chained into a lock

Same audit as #15, and the same underlying shape: the pre-hour phase
resolved launches before the chaff gate was ever consulted. Here it was not
just a slot-accounting slip — it changed who wins a weapon exchange.

**The RULEBOOK contradicted itself,** which is why this survived so long.
§4.9.5: on a covered hour "**every** seat's action ... is cancelled". A
launch is an action. But §4.9.3's within-hour ordering list put *EMP-launch
pre-emption before chaff pre-emption*, which grants a salvo priority over a
same-hour flare. The engine implemented the ordering list. Ruled in favour
of §4.9.5 (see RULEBOOK v1.14).

**Symptom A — a salvo flew out of a fully jammed house.** Both launch types
were pre-empted in one seat-ordered pass, so whichever seat the loop reached
first simply fired. Measured: p1 flares at H1 (window 1..3), p2's EMP
queued at H2 launches anyway and spends its charge. "Answer their chaff with
an EMP" was a reliable counter that no rule granted.

**Symptom B — chaff chained into a lock.** A flare fired *inside its own
window* fired anyway: it re-armed the window (1..3, then 2..4, then 3..5)
**and** re-entered `chaff_triggerers_by_hour`, which re-granted the launcher
launch-hour immunity. So `N` flares jammed the opponent for
`N + CHAFF_DURATION_HOURS - 1` consecutive hours while the chaffer was never
jammed once. With enough blue that is a whole night of denial, and it is the
kind of edge an agent harness finds by search long before a human does.

**Fix.** `_pre_hour_phase` resolves flares first, then salvos, and skips
both on an hour already covered by a window opened earlier. A cancelled
launch falls through to the main dispatch: slot burned, `tag="chaffed"`, and
crucially **the munition is not spent** — a house that never fired has not
spent the round, so the flare or charge stays in `weapon_stock`.

**What deliberately did NOT change.** Two flares on the *same* hour still
both fire and both pay, so one is wasted: neither seat is inside a window
when the hour opens, and the effect is global and identical. That symmetry
is what keeps mutual chaff strictly worse than solo chaff, so the weapon
can't be spammed as a safe mutual stall. Now stated explicitly in §4.9.5
rather than left as an emergent accident.

**Fan-out:** RULEBOOK §4.9.3 (ordering) + §4.9.5 (prose) + v1.14 changelog
and header bump; `manual/manual.js` and `manual/index.html` chaff panes. V12
was left alone on purpose — its doctrine only reads chaff *defensively* ("a
jam cancelled your slots, re-time it"), which is still true, and
`tabula_v12` is the baseline forks are measured against. The offensive
reading is the hackathon's own exercise, and this rule is now the thing
attendees' weapon logic has to respect.

**Tests:** `tests/test_chaff_precedence.py` (8). Verified against the
original simulator with a restore trap: 9 of the 13 new tests across both
files fail there, and the guards (unopposed salvo still flies, two flares
still both fly) pass — so the suite is not just asserting "weapons are
broken".

## 17. ✅ (DONE, v1.19) Probe-launch markers could outlive their probe forever

Raised as "markers should be removed after three nights no matter what", and
the useful part of the answer is that **the rule already existed** — v1.2
specifies exactly that, so no rulebook change was needed. What was missing
was the sweep actually running:

> **Aurora sweep added to `decay_probes`.** At **every Aurora**, after
> expiring live probes by lifetime, the engine also sweeps `probe_intel` for
> any `via="probe_launch"` marker whose probe is no longer alive but has
> reached its natural expiry date. (RULEBOOK v1.2)

Design intent recap: a probe killed by crush / collision / supersede stays
in the non-witnesses' intel deliberately (they saw it land, not die), and the
probe's scheduled expiry — `SOC_PROBE_LIFETIME_NIGHTS`, default **3** — is
the *only* bound on that. So if the sweep misfires there is no other backstop
and the marker is simply permanent.

**Hole A — the sweep was behind an early return.** `decay_probes` bailed with
`if not expired: return []` *before* the sweep, so markers only cleared on an
Aurora where some **unrelated** live probe also hit its lifetime. Measured
with a single secretly-killed probe and nothing else on the board: marker
still present on day 6 and every day after. Add one decoy probe that expires
on day 3 and the same marker cleared correctly on day 3 — the cleanup worked,
it just fired on someone else's schedule.

**Hole B — merged markers were skipped.** The sweep matched
`via == "probe_launch"`. A launch onto a cell the viewer had *already*
scouted is merged onto that richer terrain echo (v1.11) and leaves only a
`probe_launch_glyph` overlay with `via` untouched, so the sweep walked past
it. That is precisely the case the v1.11 comment predicted would "linger
forever as a permanent ghost": it was fixed for the death path
(`_clear_probe_launch_markers`) and never for the scheduled path. Measured:
glyph still rendering on day 5 even though the sweep had run on day 3.

**Fix.** The sweep is now `_expire_stale_launch_markers(k)`, called
unconditionally at every Aurora, and it recognises both marker shapes: a bare
`via="probe_launch"` entry is deleted, while a merged entry has only the
overlay stripped (glyph, launch day, the dead probe's occupant row) so the
viewer keeps the terrain snapshot they earned. Marker dating prefers the
`asset_records` deploy day and falls back to the marker's own
`probe_launch_day` / `day_seen` for hydrated state, instead of the old
"no ledger row → treat as stale" shortcut.

**Tests:** `tests/test_launch_marker_expiry.py` (8), including the v1.2
guards that must NOT regress — the marker survives every night *before* its
scheduled dawn, a live probe's marker is never retired, and with decay
switched off markers stand forever because there is no schedule to clear
against. Verified against the committed engine: 4 fail there, the 4 guards
pass.

## 18. ✅ (DONE, v1.19 / RULEBOOK v1.15) A probe could die with no graphic at all, and an incumbent died with the wrong name on it

Two problems on one square, found from "there is currently no graphic for
superseding a probe".

**The graphics.** `crushed_probes` is the replay payload that drives the
pixel-splash — eight squares flying out of the cell in the dead probe's seat
colour. It was only ever filled by `consume_probes_at`, the harvester crush
it was built for. All three §3.16 paths in `spawn_probe` wrote a log line and
nothing else, so:

- a **superseded** probe blinked out with no cue that the arriving probe had
  killed it; and
- a **mutual annihilation** rendered *nothing whatsoever* — not even the
  incoming streak. The animator derives a landing by diffing frame entity
  snapshots, and a probe destroyed on arrival is created and destroyed inside
  one move, so it never appears in one. Two seats could burn a probe each on
  the same square and the board would not flicker.

Fixed by filing a record on every path (`_queue_probe_death_fx`, carrying a
`reason`), and by synthesising the landing delta from that record when no
surviving probe can be diffed out. No new visuals — the existing streak,
splash and ripple, with the streak's existing per-probe random stagger doing
the "series of landings" look. A doomed probe skips the ripple only, because
the ripple is the vision pop and an annihilated probe grants no vision.

**Then the splash timing, which was wrong for every crush, not just the new
ones.** The tick-level FX pass fires the instant a tick paints, while the
unit that did the crushing is still visibly in flight — so the burst popped
in isolation with nothing arriving to explain it. The first pass at this
moved only the probe-on-probe case into the streak's landing callback and
left harvester crushes where they were, on the false grounds that "the
crushing unit's own animation already puts it on the beat". It does not:
`runReplayAnimationsTick` only *schedules* the ghosts, so the FX loop right
after it still runs at t=0 — ahead of a step slide, a lifter's deposit, and
(with orbit sequencing on) a ~2.6s launch lead.

**And two faults in the pre-landing mask itself**, both of which broke the
one rule the mask exists to enforce — the board must not move before the
sprite arrives.

- *The mask leaked the tile under it.* Matching it to `--fog-bg` fixed the
  black hole but that colour is **96% alpha** — fine on a fog cell, which
  has nothing beneath it but the board, and wrong as an overlay on a cell
  the frame has already painted revealed. The remaining 4% let a RED seam
  glow faintly through before the probe had found it: the sign bleeding a
  beat early, which is exactly the intel the mask withholds. Now stacked as
  the fog tint over `--bg`, which composites to the same colour a fog cell
  shows with no transparency left.
- *One probe's landing opened another probe's vision.* The probe branch
  revealed the shared `probeMaskedCells` set rather than its own cells, so
  the first streak down lifted every in-flight probe's mask. `spawnProbeTrail`
  jitters each start by up to 200ms precisely so simultaneous probes land
  staggered, so the second probe's ground opened while it was still in the
  air — most visibly with two seats onto one square (§3.16). It now calls
  `revealMaskedCells(pendingMasks)`, the local-array helper the harvester
  drop already used; the shared set stays as the cancellation ledger.

**The reveal mask also un-saw ground the viewer already held.**
`collectNewlyRevealedCells` asked "was this cell *fresh* before?", which puts
a remembered tile in the same bucket as pitch fog — so every stale tile and
probe echo inside an incoming probe's disc got the fog block dropped on it
and blinked out, as though the probe were un-seeing the ground on its way in.
Only a genuine fog → visible flip is fog-masked now. A stale tile is instead
*held at its remembered look* (dimmed to `--opa-stale`, since the dimming
normally comes from `.cell--stale` on a cell that is fresh in this frame and
would otherwise show the memory at full live brightness), and promoted to
live on the landing beat with everything else.

**The victim also had to still be standing when its killer arrived.** The
replay paints the resolved end-state frame and animates deltas over it, so a
doomed probe was already erased the instant the tick painted — it blinked out
while the harvester was still sliding toward it, and the splash then went off
over a square that had been empty for a second. Fixing *when* the splash
fires did nothing for this on its own. `_layDoomedProbeStandins` now holds
each victim on its cell from the moment the delta is scheduled, and the same
landing hook that fires the splash removes it, so the probe is gone in the
beat it bursts rather than long before. It only stands in for a probe that
was on the board in the previous frame — a probe born and destroyed inside
one move (the arriving side of a §3.16 annihilation) was never visible, and
its arrival is already the incoming streak. Verified against the reel: 7 of
its 8 probe deaths take a stand-in, and the one that doesn't is exactly that
case.

A third, separate hole: **a seat could not see its own probe die.** A probe
killed on arrival grants no vision, so the cell paints as fog for the seat
that launched it, and `playProbeCrushFx`'s fog gate then swallowed the only
feedback that seat had — streak in, nothing. The gate now yields when the
viewer owns the dead probe. Nothing leaks: that seat picked the target and
knows the probe is gone. Another House's probe dying on a cell you cannot
see stays hidden, because that *would* tell you they had a probe there.

Every branch now fires its own splash from its own landing hook — `step` on
the ghost's `transitionend`, `drop` at `_onDeposit`, `probe` at the streak's
touchdown — scoped to the cell it landed on, since two units can crush two
different probes in the same hour and an unscoped call from whichever landed
first detonated the other's victim early. Hooks stake their cell in
`crushClaimedCells` as they are scheduled, and the tick-level pass now fires
only what nobody claimed: a scrub (no animations run) or a delta skipped
outright, e.g. an enemy action the viewing seat cannot see. Those have no
beat to be in time with. `_onDeposit` is also installed unconditionally now
— it used to be skipped when a drop revealed nothing and recoloured nothing,
which is exactly the case where a crush was the only thing left to sequence.

**The rule.** §3.16 was explicit that two or more probes landing on a square
in one hour all die, and **silent** on an older probe already standing there.
The engine answered by accident: resolving seat by seat, the first arrival
superseded the incumbent, then the second annihilated with the first. Body
count right, attribution wrong — the incumbent was ledgered
`probe_superseded` and the `probes_superseded` kill-feed credit went to
whichever seat the resolver reached first, a seat that lost its own probe in
the same instant.

Ruled (RULEBOOK v1.15, new §3.16(c)): the incumbent dies as a **collision
casualty** and **nobody** is credited a supersede — supersession is an act by
a surviving newcomer, and when the arrivals wipe each other out nobody took
the cell. Two rivals clearing an established probe therefore pay both their
own probes for it. `_finalise_probe_contest` re-files the provisional
supersede once a second same-stamp arrival proves the cell was never held, so
ledger and kill-feed no longer depend on seat order. Also written down at
last: the same-hour latecomer sweep (new §3.16(d)) shipped in v0.9.17 citing
a "§3.16 E4" that had never existed in the RULEBOOK.

**Tests:** `tests/test_probe_death_fx.py` — 14 cases covering all three
paths, the incumbent ruling (2-, 3- and 4-way piles), the guard that a lone
newcomer *does* still earn its supersede, and the guard that an unopposed
launch splashes nothing.

## 19. ✅ (DONE, v1.19) A dead probe kept marking a square you could see was empty

**Symptom:** a probe glyph sat on a cell that the owner had clear sight of
and that plainly held nothing. It never aged out on its own; only a later
event on the same cell cleared it.

**Root cause:** two rules that are each correct on their own, colliding.
§3.15 stamps a **launch marker** on the target cell so a seat can see where
its own probe went, and that marker is deliberately sticky (it survives
losing sight of the cell — that's the point of it, and it has its own
3-night expiry). §3.16 kills probes on collision and supersession. Nothing
connected the two: when a seat **witnessed** its own probe die, the death
was drawn, but the launch marker underneath it was left in
`probe_intel`. The seat was therefore looking at a marker for a probe it
had just watched be destroyed.

The earlier crush path didn't show this because `_freeze_final_probe_echo`
already rewrites the victim's echo on that route. Widening that helper to
cover the §3.16 deaths was the first attempt and was wrong — it re-froze
the whole vision disk, handing the dead probe's owner a free snapshot of
everything the probe *could* have seen, which is unearned vision.

**Fix (`game/session.py`):** new `_forget_probe_marks_at`, which takes a
cell and a set of just-died probe ids and removes **only** those probes'
traces (occupant entries, `probe_launch_glyph`, `via`, and the glyph
fields when nothing else claims them) from every player's `probe_intel`.
It grants no vision — it only retracts a claim the engine now knows to be
false. Called from `spawn_probe` on both the collision and the supersede
paths. `_freeze_final_probe_echo` stays where it was, on harvester crush.

**Tests:** `tests/test_probe_death_fx.py` — a superseded probe stops being
painted on its square; a multi-way pile-up leaves no mark anywhere; a
probe that *survives* keeps its launch mark (the guard against
over-retracting); a death elsewhere doesn't touch an unrelated square.

---

## 20. ✅ (DONE, v1.19) The day-2 harvester collision exploded three seconds before impact

**Symptom:** on a simultaneous drop, one harvester detonated and lifted off
while the other was still visibly falling — the explosion, damage numbers
and lift-off all played before the two craft met.

**Root cause — two faults, not one.** Both are sequencing, not logic; the
engine ordering was right throughout.

1. *The explosion.* Orbital arrivals animate with a long run-in
   (`_OS_LEAD_MS` — 2600ms for a drop) so the craft has somewhere to fall
   from, but `playCollisionFx` fired from the tick loop at frame-paint
   time. Detonation and contact ran ~3s apart.
2. *The departure.* `pickup` had **no lead at all**, so a recovery began
   at tick paint while the inbound drop was still held by its 2600ms
   lead: the damaged harvester lifted off and was clear of the board two
   seconds before the impact that damaged it was drawn.

Fixing (1) alone left the reported symptom untouched, because the thing
being complained about was (2). Worse, the check that would have caught
it looked in the wrong place: a replay **tick bundles several frames**
(here hour 3's collision and hour 4's recovery), and they all animate
concurrently, each on its own lead. A pickup asking its own
`curFrame.collisions` sees nothing, because the collision belongs to a
different frame of the same tick.

**Fix (`server/static/app.js`):**
- The collision ring is **claimed** by the arrival animation rather than
  raced by the tick loop — `runOrbitalArcAnimation` installs
  `_fireCollision` on `delta._onDeposit` so it goes off at the bounce
  midpoint, with `collisionClaimedCells` preventing a double fire.
- `runReplayAnimationsTick` now censuses collisions across **every frame
  in the tick** into `tickCollisionCells`. A pickup off a tile in that set
  is held until `_osCollisionContactMs()` (inbound lead + the 550ms arc
  midpoint), so the impact is drawn before the recovery starts.
- The held unit is stood back onto its tile from paint time
  (`_layPickupStandin`, handed to the deferred arc via `_preStandin`).
  Without it the harvester blinked out at paint and the collision landed
  on an empty square — the first attempt at this fix did exactly that.

**Verification:** `backstage/probes/_fx_census.py`, which supersedes the two
narrower harnesses that missed this. Tick 10 of the reel now reads:
inbound ghost +2652ms, impact +3183ms, unit lifted +3766ms — cause before
effect, with no blink-out.

**Note on the earlier harnesses.** `_fx_collision.py` timed only the
explosion and never asked when the harvester sprite left; `_fx_probe.py`
sampled a hardcoded cell list, so anything happening elsewhere was
invisible. Both reported "fixed"/"not reproducible" on faults that were
live. `_fx_census.py` diffs every sprite on the board, by glyph **and
colour** (a probe superseded by a rival is `·` before and after, so a
glyph-only diff calls a change of owner "no change").

---

## 21. ✅ (DONE, v1.19) The doomed probe vanished the instant the hour opened

**Symptom:** a harvester dropping onto an occupied square crushed the probe
on the right beat — the burst was correctly timed by #20's fix — but the
probe *itself* left the square the moment the hour started, so the crush
detonated on ground that had already been empty for three seconds.

**Why the census said it was fine.** `_layDoomedProbeStandins` was doing
its job: the stand-in node was in the DOM, attached to the right cell, and
`getComputedStyle` reported it visible for the full hold. Every DOM-level
check passed. `.harvest-terrain-standin` — the layer that holds the tile's
*terrain* still while a drop is in the air — is `position:absolute;
inset:0; z-index:4` with an opaque background, and `.entity-overlay`
carries no `z-index` at all. The terrain hold was painted straight over
the probe. Present, visible, and behind an opaque rectangle.

This is the third time in this bug family that a harness reported "not
reproducible" on a live fault (see the note under #20). The lesson is
narrower than "the harness was too narrow": **presence in the DOM is not
presence on screen.** Anything asserting a sprite was seen has to end in
pixels.

**Fix (`server/static/app.js`, `server/static/styles.css`):**
- `.entity-overlay--doomed` at `z-index: 5`, so a unit deliberately held on
  its tile sits above the terrain hold rather than under it.
- The stand-in is now built from the probe the board **was actually
  painting** a beat ago — `entityOverlayHtml(before.entity)` off the
  previous frame's cells — instead of a hand-rolled `·`. The old dot had no
  lifetime rings and no profile colour, so even unoccluded it read as some
  other, fainter thing appearing rather than the probe staying put.

The supersede path (`§3.16(a)`) lays no terrain stand-in, so it was never
occluded; it picks up the markup half of the fix only.

**Verification:** `backstage/probes/_fx_pixels.py` — screenshots one cell every
250ms through a tick and contact-sheets the result, deliberately ignoring
the DOM. Before: the cell is bare from +0ms. After: the probe is on screen
at +0ms in its real markup, holds to +2000ms, and is replaced by the
harvester and its crush burst at +3000ms.

---

## 22. ✅ (DONE, v1.19) V12 played every Snowflake season with no cross-night memory

**Status (v1.19):** Fixed. `SOC_AGENT_MEMORY` and `SOC_AGENT_BINDING` are now
part of the standard deploy and part of the session wipe.

**Symptom:** V12 played visibly worse in the browser than in the headless
matchup runner it was tuned against — repeating plans it had already seen fail,
re-picking cells it had just been denied, and losing the thread between nights.
Its own THINK text would name a cause one night and ignore it the next.

**Root cause:** two independent gaps that hid each other.

- When orchestrator_2 landed, `SOC_AGENT_MEMORY` was **relocated** out of
  `snowflake/soc_schema.sql` into
  `sea_of_colours/orchestrator_2/snowflake/orchestrator_v2_schema.sql`. That
  file's header says to deploy it via `scripts/deploy_soc_schema.py` — but the
  deployer only ever shipped three files, all from `snowflake/`. Nothing
  created the table, on any account. `SOC_AGENT_BINDING` was missing for the
  same reason. Confirmed against the live account: 12 `SOC_*` tables, neither
  of these among them.
- All three of V12's memory modules — the strategy journal
  (`tabula_v12/_v7/memory.py`), `hazard_memory.py` and `frontier.py` — keep a
  **process-local dict** and mirror it to that table **best-effort inside bare
  `except` blocks**, with the rehydrate-on-empty read equally forgiving. A
  missing table therefore produced no error, no log line, and no visible
  degradation in any test.

**Why only live play suffered:** `scripts/run_matchup_v12.py` does
`os.environ.setdefault("SOC_BACKEND", "memory")` and plays a whole season
inside one process, so the process-local dict carries the full journal and the
mirror is irrelevant — which is exactly the configuration V12 was validated in.
A live season runs for hours on Snowflake, where that dict is the *only* copy;
the moment the server process is replaced the memory is gone for good, because
rehydration hits a table that does not exist and swallows the failure.

**Fix:**
- `scripts/deploy_soc_schema.py` now deploys `orchestrator_v2_schema.sql`
  between the schema and the views (it is `CREATE TABLE IF NOT EXISTS`, so it
  is non-destructive and safe to re-run).
- `SnowparkSocStore._SESSION_TABLES` gains both tables, as the SQL file's own
  comment had been asking for. Both key on `session_id`, so the existing wipe
  predicates work unchanged; without this a reused session id would inherit a
  dead game's journal.

**Verification:** `scripts/_check_agent_memory.py` writes a journal entry
through V12's own `memory.save_entry`, clears the in-process store to force the
restarted-server path, and reads it back through `read_recent` — 0 rows before
the fix, round-trip PASS after. Full suite 1051 passed / 1 failed, the failure
being the pre-existing `two_seams_choose_one` eval debt.

**Verified in play (session `46615fbd`, V12 vs RED_HARVEST):** the server was
killed at day 6 with the journal held only in-process, restarted empty, and the
season resumed and completed. Days 1–6 hydrated back out of Snowflake and day 7
plus `hazard_cells` / `spent_blue` / `enemy_probe_disk_history` were written by
the new process. Before this fix that restart erased the season's history
silently. `read_recent` was also confirmed against the real season data from a
fresh process with an empty cache.

**Not established:** whether this is what collapsed the day-3 plan on session
`e75dbdc` (`sanitized=9 … moves=1`). That night has not been isolated. Note
also that a season played on one uninterrupted process was never affected —
the process-local cache covered it — so this fix changes behaviour only across
a restart.

---

## 23. ✅ (DONE, v1.20) The Snowflake season picker crowned the loser

**Status (v1.20):** Fixed. `SnowparkSocStore.bulk_session_scores` folds the
persisted parcels through `compute_player_score` like every other store; nothing
user-facing scores off the SQL view any more.

**Symptom:** the season picker and the end-of-season card disagreed about the
score, and on at least one finished season they disagreed about **who won**.
Serpens_Lattice (`46615fbd`, V12 vs RED_HARVEST) read p1 1436 / p2 1853 in the
picker against a true p1 1527 / p2 1418 on the results screen — opposite
winners.

**Root cause:** the scoring rules existed in two places. `compute_player_score`
(`game/session.py`) is the canonical one, and its docstring promises "the
standings, the HUD's shipped-score line, and the end-of-game results screen can
never disagree". The Snowflake store did not call it: `bulk_session_scores`
selected a precomputed `score` column out of the `SOC_SESSION_STANDINGS` view,
which reimplements scoring in SQL as

```sql
SUM(LEAST(255, GREATEST(0, COALESCE(origin_purity, 0))))
```

That is a raw purity sum, and it diverges twice:

- **No RED tier multiplier.** The real score is tier-weighted, which is why
  engine scores carry fractions (`1526.75`) and the view's never did.
- **GREEN is credited instead of charged.** Since v1.13 green is auto-disposed
  each orbit and the disposed parcels are appended to `SOC_SHIPPED_PARCEL`
  carrying their GREEN origin tile — deliberately, so the bulk scoreboard can
  charge them with no extra column (§4.7). Green is always purity 255, so the
  view paid **+255** for each one where the scorer charges
  **−100**: a 355-point error per parcel. RED_HARVEST had jettisoned four, so
  55% of its phantom 1853 was green it should have been penalised for.

**Why it went unnoticed:** only the Snowflake store had the duplicate. The
memory and file stores both call `compute_player_score`, so every offline
season, every headless matchup and the entire test suite agreed with the results
screen. The invariant only broke on a live Snowflake game, where the two
surfaces are rarely read side by side.

**Fix:** `bulk_session_scores` now issues three grouped reads (phases, shipped,
hoard) and scores them in Python through `compute_player_score`, unwrapping each
row's VARIANT `payload` so the scorer sees the same parcel dict the live session
holds — including the catapult-stamped `effective_purity` / `score_tier`. Still
no per-session hydration. Seats come from the parcel `owner` column, so it stays
N-seat aware. The view is left in the schema for ad-hoc SQL.

**Verification:** `tests/test_snowflake_standings_parity.py` pins the parity
offline against a fake session, asserts a disposed GREEN costs
`GREEN_ENDGAME_PENALTY` rather than paying its purity, and fails if anything
reads `SOC_SESSION_STANDINGS` on the score path again. Against the live account
the picker now matches `score_for` exactly on Serpens_Lattice and on four other
sessions including a single-seat one. Full suite 1056 passed / 1 failed, the
failure being the pre-existing `two_seams_choose_one` eval debt.

---

## 24. ✅ (DONE, v1.28) A same-hour EMP voided a rival's landing

**Status (v1.28):** Fixed. The hour-start visibility snapshot is taken inside
`NightSimulator._pre_hour_phase`, beside the established-cloud snapshot and
immediately after the decay sweep, instead of in the hour loop after that
function has already fired the hour's salvos.

**Symptom:** reported from a live season (Terra_Kestrel, day 6, hour 1). p1
fired an EMP salvo onto (2,14); it destroyed p2's probe; p2's harvester drop
on (2,14), queued for that **same hour**, was refused with `drop: (2,14) has
no live sensor beacon`. The landing should have succeeded *and* auto-harvested.

**Root cause:** the engine had the right mechanism and consulted it a beat too
late. `try_drop_unit` correctly honours a `live_override` snapshot, but the
simulator computed that snapshot *after* `_pre_hour_phase` returned — and
`_pre_hour_phase` is where this hour's EMP launches are pre-empted and
resolved. So "what this seat could see at hour start" was recorded with the
rival's beacon already destroyed by an event that had not happened yet at hour
start.

The tell is that **supersede obeyed the rule and EMP did not**, despite
§3.9.7 listing them as equivalent. That is purely an artefact of where each
resolves: a supersede is a normal dispatch action and lands *after* the
snapshot, a weapon launch is pre-empted and lands *before* it.

**Why it survived so long:** the rule is stated in four places and the engine
was the only dissenter — RULEBOOK §3.9.7 ("a beacon a rival destroys,
supersedes, or EMPs *later in the same hour* still validates that hour's
landing"), §3.9.8 restating it, §4.9.3 (a cloud spawned this same hour is not
"established", so the landing keeps its auto-harvest), and `manual/manual.js`,
which scripts this exact scenario for attendees. Meanwhile §3.9.8's own risk
list recommended "launching an EMP at your probe location to destroy it before
your drop resolves" as a counter to the hot-drop — four lines below the
sentence saying that cannot work. The bug had been written up as a feature.

Test coverage existed and could not have caught it:
`test_vision_rework.test_live_only_honours_hour_start_override` passes
`try_drop_unit` an override by hand, so it pins the plumbing, not the *timing*.

**Regression:** `tests/test_emp_beacon_timing.py`, four tests, all driving the
real `NightSimulator`. Two reproduce the report (the landing, and the harvest)
and fail without the fix with the reported error string verbatim. Two are
counterweights that pass either way, so the fix cannot be over-applied: a cloud
established on an *earlier* hour must still forfeit the landing's auto-harvest
(§4.9.3), and the one-hour reprieve must not persist — a beacon destroyed on
hour 1 must not validate a drop on hour 2, which is exactly what a "cache the
first snapshot" implementation would have done.

**Note for the next reader:** the snapshot instant is deliberate. It sits after
the decay sweep, not at the very top of the hour, because a probe swept by a
cloud that has been standing since an earlier hour is killed by a weapon
already on the board — not by something happening "later in the same hour".

---

## 25. ✅ (DONE, v1.29) A jackpot sat in poor ground, so "warmer" was not a readable signal

**Symptom (design, not a crash).** A `pure` cell — the jackpot the whole
redsign mechanic is built around — was usually an isolated 255 in trace. The
median board carried **2** `mass` squares out of 1120, and **79% of jackpots
had no `mass` at all within 4.5 cells**.

**Root cause.** `_spread_pure_red`'s top-up promotes the richest RED cell that
clears the 12-cell separation, and separated `mass` barely exists, so it
routinely lifted a cell of purity 80–150 straight to 255. Nothing had ever
decided what should surround a pure; the count and spacing rules only decided
*where* they go.

**Why it mattered.** It broke the read prospecting should give a player —
*thickening ground means you are getting warmer*. A pure surrounded by trace
can only be found by landing on it, making the search a lottery rather than a
deduction.

**Fix.** New `_grade_pure_red` pass (RULEBOOK §2.2) lays a deposit around every
pure: `mass` chunks clinging to it plus a scatter further out, through a `vein`
shoulder. Bounded by three rules — only ever raises purity, never touches GREEN
or BLUE, never writes 255 (so it cannot mint a pure beside the one it is
decorating). Runs last on its own RNG stream. Median `mass` per board 2 → 15,
jackpots with no mass 79% → 1.4%.

**⚠ THIS MOVES SCORES, AND THE FOLLOW-UPS MATTER MORE THAN THE FIX:**

1. **Total RED on a board roughly doubled** (median map value 8,844 → 15,394 at
   two seats). Seasons score higher, the `EXTRACTED %` denominator moves, and
   **a score from before v1.29 is not comparable to one after it** — including
   agent benchmarks and the RED_HARVEST floor.
2. **V12's `_ASSUMED_HALO_PURITY = 55` — investigated and largely a false
   alarm.** It prices the blind comb of an unseen redsign halo, which is the
   ground grading enriched, so it looked like a serious knock-on. Measured with
   `scripts/_v12_halo_trace.py` over 28 offline seasons (14 graded, 14 not, same
   seeds), sampling every real smear a seat could see:

   * **The constant is reached on 1.0% of priced seams** (2.1% ungraded).
     `blind_estimate` measures each visible seam and pools across seams first,
     and that path reads the new terrain by itself. The terrain change did not
     quietly break redsign pricing.
   * **55 was never right.** Real smears measure a median non-pure purity of
     **30** ungraded and **107** graded (n=1095 / 1525). The original 53/54/63
     came from three hand-picked snapshots richer than a typical board, so the
     constant ran ~1.8× *high* before v1.29 and ~1.9× *low* now.
   * **The `mass` cliff at 151 is not in play.** That step (×1.0 → ×1.5) is what
     made an early 180 guess beat every option on the card — a 6-cell comb
     prices 281 at purity 55, 602 at 107, 1377 at 180. Across 1525 graded
     samples the measured mean never exceeded **146**; real halos stay in
     `vein`, so a value near 107 is safe in a way 180 was not.

   **Applied (v1.29):** `_ASSUMED_HALO_PURITY` 55 → **105** (graded median 107,
   rounded down) and `_ASSUMED_RED_DENSITY` 0.85 → **0.95** (measured 0.98).
   Both nudged slightly conservative, because the failure this constant has
   historically produced is over-valuing a blind attack, never under-valuing
   one. Safe to apply alongside the terrain change precisely because of the 1%
   reach — it cannot meaningfully confound a score comparison. Re-run the
   tracer before moving them again.
3. **Revert is a server restart, not a code change.** `SOC_MAP_HALO=off`
   restores byte-identical v1.28 terrain (pinned by
   `test_soc_map_halo_off_restores_the_v128_board_exactly`); a fraction thins
   the `mass` without moving jackpots. Safe mid-season — sessions persist their
   grid, not just their seed.

**Reproduce:** `python scripts/_mapgen_pure_census.py 200 40 28 2` prints both
sides over the same seeds.

**Loose end — `manual/agent-data.js` still carries the v1.28 prompt wording**
("the halo is thin"). It could not be regenerated here:
`scripts/export_agent_guide_data.py` needs `SOC_BACKEND=snowflake` to read
snapshot `SNAP_408ddd46_d4_p1`, and its other input,
`reports/turn_suite/cards/post_phaseD/own_seam_d4.txt`, is not in the repo.
Worth a thought before regenerating: that file is an **autopsy of one real
historical turn**, so the prompt it shows is the prompt genuinely sent that
night. Rewriting it to today's wording would falsify the record — the choice is
between re-capturing a fresh turn on a graded board (better teaching material)
and leaving the old turn intact and dating it. Not a product bug either way.

---

## 26. ✅ (DONE, v1.31) A stale `mine_lay` burned a slot and said nothing

**Status (v1.31):** Resolved by retiring the weapon. The caltrop mine is gone
(RULEBOOK §4.9.4) and both of its tags are now **refused by name** instead of
falling through: `mine_lay` via the new `_RETIRED_MOVE_TAGS` in
`game/policy.py`, `build_mine` via the existing `_RETIRED_ORBIT_TAGS`.

**Symptom:** an agent that asked for a mine it could not use lost an hour and
was told nothing it could learn from. The row resolved as a generic waste, so
the next night's prompt carried no reason and the model asked again.

**Root cause:** two gaps lined up. V12's `_v7/move_sanitizer.py` never filtered
`mine_lay`, so a stale model reply reached engine policy intact; and policy's
fall-through for an unrecognised tag is the bare "unknown action" waste, which
consumes a slot (§3.10, every queued row burns one) without naming what went
wrong. The same shape as the v1.13 `ship_catapult` problem, and it is fixed the
same way: a dead tag maps to a *reason*, not to silence.

**Fix:** `_RETIRED_MOVE_TAGS` as the night-move mirror of the orbit dict, both
tags answering with "the caltrop mine was retired in v1.31" and what to use
instead. The slot is still consumed — that part is the rule, not the bug — but
the seat now sees why on the card.

**Deliberately not fixed, because it is not broken:** replay frames, the
Snowflake replay columns, the minelayer flight animation and the `mine_lay`
entry in the orbital activity tally all stay. The tally classifies *stored
frames*, so dropping the tag would have blanked the orbital silhouette of every
archived season that used caltrops.

**Migration:** a pre-v1.31 save has its armed caltrops cleared on load (a
retired weapon must not keep damaging harvesters mid-season) and its unspent
stock refunded as blue purity at the price paid, 100 each; credits are not
refunded. A `[mineRetired]` log line reports both counts.

**Tests:** `tests/test_v09_weapons.py` —
`test_retired_mine_lay_says_why_rather_than_shrugging`,
`test_retired_build_mine_says_why_rather_than_shrugging`, and
`test_stale_mine_stock_refunds_as_blue_on_load`.

---

## 27. 🔴 (OPEN) The heuristic stopped choosing a seam in `two_seams_choose_one`

**Symptom:** `pytest` is one red at HEAD —
`tests/test_eval_scenarios.py::test_heuristic_passes_known_scenarios[two_seams_choose_one]`,
failing `HarvesterChainHits: hit 0/1 of 3 target cells`. The agent still
plans a sane-looking night: it walks `harvester_p1` from `[12,13]` out to
`RED[15,15]` and drops both probes on fog clusters. It simply walks the
*other* seam from the one the scenario is asserting on.

**Not from the tutorial work (v1.32/v1.33).** Confirmed by running the file
in a clean worktree at HEAD: it fails there too, with none of the film or
preset changes present. Everything else is green — 1209 passed.

**Where to start:** the three most recent heuristic commits are the
suspects, and all three moved exactly the number this scenario measures —
`9f555e5` (grade the ground around a jackpot, scale jackpots with the
table), `d33b710` (price a blind seam from what seams actually measure),
`92c0534` (last-night settlement). The question to answer first is whether
the scenario's expected cells are still the *better* seam under the new
pricing. If they are not, the fixture is stale and should be re-pinned
with a note; if they are, the seam scorer is the bug. Do not "fix" it by
loosening the assertion until that is settled — this scenario exists to
catch the heuristic wandering.

---

## 28. ✅ (DONE, v1.33) The tutorial stopped teaching after you had played it once

**Symptom:** start Basic a second time and no film modal ever appears. Nothing
errors, the `[ TUTORIAL ]` button is still there, and the reels still resolve —
the modal simply never opens itself. A first-timer on a borrowed laptop that
had already been demoed would get a teaching mode with no teaching in it.

**Root cause:** the auto-open is suppressed once a reel has been shown, so the
modal does not reappear on every four-second poll. That memory
(`soc.tutorial.seen.v1` in `localStorage`) was keyed on the **reel name
alone** — and reel names are `basic:planning:1`, `basic:orbit:2` and so on,
identical in every Basic game. One tutorial filled the set; every later
tutorial read as already seen. The original comment ("keyed by reel, not by
turn number, so replaying the tutorial does not re-nag") had it exactly
backwards: **starting a fresh teaching game is a request to be taught.**

A second, quieter half: `onState` decided "is this a new turn?" by comparing
bare reel keys too, so a new game reached without a page reload looked like
the same turn as before.

**Fix:** `app.js` puts the session id on the `soc:tutorial-state` event, and
`tutorial.js` scopes both the seen-set and the changed-check to
`"<game>|<reel>"`. Mute (`soc.tutorial.muted.v1`) is untouched and is still
the real "stop showing me these" control — it silences the auto-open without
removing the button. Old un-scoped entries are ignored rather than migrated;
worst case someone mid-game sees one reel a second time. The list is capped
so a browser that plays many tutorials does not grow it forever.

**Why no test caught it:** `backstage/films/_fx_tutorial.py` gets a clean browser
profile on every run, and this bug only exists on the *second* game in one
profile. It now plays a second tutorial in the same context and asserts the
modal opens again, plus a third with mute set to assert mute still wins. Both
were confirmed to fail against the old code before the fix went in.

## 29. ✅ (DONE, v1.33) A camera move left the vision border on the old board

**Symptom:** found while watching `basic_crash`. Push the camera in on a tile
and the terrain magnifies while the white vision border stays exactly where
and how big it was — a small outline stapled across the middle of a zoomed
map, tracing cells that are no longer under it. Nothing errors, and it is
easy to mistake for a border that is merely mis-drawn rather than one that is
describing the previous frame.

**Root cause:** four overlays (planned orders, vision borders, blue sign,
redsign) do not live in the grid — they snapshot cell rects and write absolute
pixels. `app.js` already knew this and repainted all four after a zoom, but
that repaint was buried inside `applyMapZoom`, so the only camera in the
building that was not a zoom — the film harness's CSS transform on
`.cc-map-viewport` — had no way to ask for it.

**Fix:** `reanchorOverlays()` is factored out of `applyMapZoom` and exposed as
`window._osReanchorOverlays`; `push_in` / `pull_out` call it on **every frame
of the glide**, not just at both ends. Both ends is not enough: the overlays
measure the cells as they are now, so a single call pins them to a size the
board is only passing through, and the 700ms move plays with the border
sitting still on top of swelling terrain.

Measured with a scratch probe (before: a 433px-wide border on a 1299px board;
after: both 1299px), and `Film.expect_overlays_anchored` now fails a shoot if
the layer drifts more than 8px from the grid. The probe cost an hour to a
scratch-script bug, not a product one — see issue 30.

**Superseded by issue 34 (v1.35).** All of this was scaffolding around the
transform camera. That camera is gone — the zoom is now a crop over the
recorded frame, where an overlay cannot be left behind at all — so the
re-anchor hook, the per-frame chase, `expect_overlays_anchored` and the probe
that measured them have all been deleted.

## 30. ✅ (DONE, v1.33) Only one lifter showed up to a two-House pile-up

**Symptom:** in the crash film, two harvesters are dropped on the same square
and one orblift flies in, bounces off an apparently empty tile and goes home
damaged — with the caption underneath reading `SIMULTANEOUS DROP COLLISION at
(9,7) — (p1+p2)`. The beat teaches nothing, because the thing it collided
with is not on screen.

**Root cause:** `describeReplayDelta` fans out one bounce per harvester in the
pile-up, then marked every bounce that was not the viewer's own `stationOnly`
— drawn on the rival's platform, never on the board — on the grounds that a
landing cell is private (§3.15). But §3.15 lists **mutual destruction** as one
of the ways a rival's position is legitimately learnt, and the viewer here
dropped on that tile themselves. The suppression protected nothing the engine
was not already printing in the log. A step-into collision never had this
gate, so the two collision shapes disagreed about the same fact.

**Fix:** the rival's bounce is drawn when the viewer is one of the Houses in
the pile-up; a collision between two other seats stays station-only.

**Why no test caught it, twice:** a film harness that only checks the crash
*happened* cannot see that half of it is invisible, and neither could I from
the video — the craft are one glyph wide for under a second. `Film.watch_lifters`
now counts distinct seat colours among in-flight arcs. The first version of
that census passed against the broken build: every lift at dawn is also an
orbital arc, so a count over the whole cinematic reaches two on the
recoveries alone. Narrowed to bounce arcs only (`.replay-anim-ghost--bounce`,
a new marker class) it fails the backout correctly.

## 31. ✅ (DONE, v1.33) A misspelled field made a turn silently do nothing

**Symptom:** `POST /api/game/{id}/policy` with `{"player": "p1", "policy":
[...]}` returns `200 {"ok": true}`, the night resolves, and the log reads
`p1 locked policy (0 move(s))`. The queue is dropped without a word. Cost an
hour of debugging a camera probe that was actually staging an empty board.

**Root cause:** the endpoint read `payload.get("moves", payload.get("commands"))`
and treated a miss as "this seat is passing", which is a legal thing to do —
so a typo and a deliberate pass were indistinguishable.

**Fix:** `_queue_field` in `server/app.py` still allows an omitted queue (pass
is real) but rejects a payload that carries some *other* list field with a 400
naming the right one. Applied to `/policy` and `/orbit` alike. This matters
past our own scratch scripts: attendees drive these endpoints by hand, and a
submit API that accepts a misspelling by throwing the turn away is a bad hour
to hand someone at a hackathon.

## 32. ✅ (DONE, v1.34) The board sat in the top-left corner of its own frame

**Symptom:** on any layout wider than the grid — most obviously the
season-complete screen, where the ORDERS panel is gone — the map parked against
the top-left of the map frame with all the dead space pooled to the right and
below. It read as a rendering accident rather than a layout.

**Root cause:** `.cc-map-area .map-host--main` is a flex scroll container with
`justify-content: flex-start` and `align-items: flex-start`, and nothing ever
overrode it.

**Fix:** `margin: auto` on the grid, not `justify-content: center` on the host.
On a scroll container those are not the same rule: centred content that
outgrows its box overflows equally in both directions, and the half that goes
off the START edge cannot be scrolled back to — a zoomed-in map would lose its
left column with no way to reach it. Auto margins collapse to zero the moment
the child stops fitting, so the board centres while it is small and pins to the
corner when it is not. Measured both ways: at 289px in a 751px host the gaps
are 231/231; at 809px in the same host the left gap is the 6px padding and all
the overflow is on the right.

## 33. ✅ (DONE, v1.34) The landing collision was shot at a zoom that hid it

**Symptom:** reported off the v1.33 films — "both harvesters landing on the same
square are not showing both orblifters, and the zoom looks weird". Issue 30
fixed the missing lifter; the framing was a second, separate fault in the same
beat.

**Root cause:** `basic_crash` pushed in on the landing square before PRAXIS. The
two collision shapes want opposite cameras and were being given the same one. A
walk-in is one sprite arriving at another and reads fine tight. A simultaneous
drop is two orbital arcs converging from opposite corners of the board — punch
in on the destination and both arcs begin off-screen, so the whole event is a
flash in a hole.

**Fix:** hour one plays wide, with both platforms in frame from launch; the
close-up is kept for the walk-in on hour three, which is what it suits. Then
the landing is shown a second time, hand-cranked: `Film.replay_rewind_to` /
`Film.replay_step` drive the replay bar an hour a click, and stepping forward
re-fires that hour's animations, so the same collision plays at a speed a
first-timer can follow. No product speed control was added — the film crew
should not get to slow the game down for everybody.

## 34. ✅ (DONE, v1.35) Every close-up in the tutorial films read as a layout bug

**Symptom:** reported after watching all sixteen v1.34 films — "almost all the
zooms look bad because we're trying to zoom in the interface". Not one bad
film: the same wrongness in every close-up, and the reason issues 29 and 33
kept looking like separate faults.

**Root cause:** `Film.push_in` was a CSS transform on `.cc-map-viewport`, and
scaling one live element inside a fixed layout is not a camera move. The board
swelled while the ORDERS panel, both station rails and the replay bar stayed
exactly where they were, which the eye reads as a rendering fault rather than
a move toward something. The grid's dotted background magnified into a pale
grey slab along with it. Issue 29 (overlays stranded on the old geometry), the
FX-layer re-parenting, and the per-frame `_osReanchorOverlays` chase were all
patches on this one decision rather than bugs in their own right.

It also had a ceiling that no amount of patching would lift: the camera could
only ever look at the map, because the map viewport was the element being
scaled. `basic_score` exists to show a load becoming a number, and both halves
of its last beat — a catapult throwing, a score climbing — live in the station
rail, permanently out of shot.

**Fix:** the camera moved out of the browser. `push_in` measures a rectangle
and banks a keyframe; the zoom is a time-varying crop applied afterwards in the
ffmpeg pass that already trims the boot. The whole frame moves together because
it is one image by then, so an overlay cannot fall off a board that is a
photograph — the re-anchor chase, the FX re-parenting and
`expect_overlays_anchored` were all deleted. `push_in_on(selector...)` frames
anything on the page, which is what let the score beat finally be shot.

`zoompan`, not `crop`: `crop` evaluates its width and height once at
configuration and only x/y per frame, so a crop that changes size over time —
a push-in — cannot be expressed with it. Full design note, including why
shooting at 2x for sharpness does not work and what `FILM_CAM=1` prints, in
`docs/TUTORIAL_PLAN.md` §12.

---

## 35. ✅ (DONE, v1.36) Moving the camera out of the browser cropped every caption away

**Symptom:** found while re-cutting the redsign films — the close-ups played
silent. Not one film: every push-in in all sixteen, at once, from the moment
issue 34 shipped.

**Root cause:** the caption is a `position:fixed; bottom:28px` element, which
was exactly right when the camera was a transform on `.cc-map-viewport` — the
board scaled underneath it and the caption stayed put. A post-process crop is
not that. It keeps a 492×308 window out of a 1280×800 frame, and a caption
pinned to the bottom of the *page* is simply not inside it. So the beats the
films push in on for emphasis — the grave, the catapult, the pure seam — were
the exact beats whose narration got thrown away.

Nothing caught it, and nothing could have: the shoot passed, the file was the
right length, the beat was in shot. Only the line explaining it was gone, and
no check knew captions were supposed to be anywhere in particular.

**Fix:** the caption tracks the crop. `window.__film.frame(win)` re-places it
along the bottom of the current window and scales it by `1/z`, floored at
`0.65` so it never renders below the game's own 13px — text smaller than that
turns to mush when the crop magnifies it back up. Order matters in each
direction: a push-in re-places *before* the ramp (the window only shrinks
toward its target, so the caption is in shot for every frame of the move), a
pull-out *after* it (the window grows away from the caption, so it stays in
shot until it is wide again).

And `say()` now asserts it, comparing the caption's box against the live crop
and failing the shoot if it is over the edge. That is the check that was
missing, not the placement.

---

## 36. ✅ (DONE, v1.33) RULEBOOK §4.11 says a redsign never clears; the engine retires it

**Status (v1.33):** Resolved in the engine's favour — the beacon retires when
its seam is spent, and the prose now says so. No behaviour changed.

**Root cause:** documentation drift, not a code defect. The engine has retired
spent beacons since v9 (I12) and `tests/test_redsign.py` has pinned it the
whole time — retirement on harvest, exit from every seat view, staying live
until the last pure cell goes. The tests were testing the engine, so nothing
ever went red while the prose said the opposite.

**Which way it was settled:** a blue-sign is radiative physics and shines
whether the seam is worth anything or not; a redsign is an *announcement about
a jackpot*, and once the jackpot is banked there is nothing to announce. A
beacon that outlives its seam broadcasts "pure in FOG" forever and lures every
seat into chasing nothing.

**Fix (documentation fan-out):**
- `RULEBOOK.md` §4.11 — the persistence bullet rewritten as a retirement
  bullet, the map-smear line corrected, `### v1.33` changelog entry added,
  header bumped to 1.33.
- `sea_of_colours/game/session.py` — the `redsign` field docstring repeated the
  stale claim *directly above* `_retire_redsign_if_spent`. Corrected.
- `sea_of_colours/snowpark/view.py` — same again on the view builder's
  `redsign` block, above the `live` filter that implements the retirement.
  Now also records why that filter is what keeps retirement anonymous:
  `spent_by` is engine ground truth and never reaches a seat.
- The other two surfaces named below needed nothing. The V12 prompt already
  branches on `_has_live_redsign` and the view hands it only live regions;
  the Advanced tutorial's night-two card was already silent on the question.

**Why it was worth doing before the day:** the fork guide sends people to the
RULEBOOK to learn the mechanic, and a room of them was hours from writing
agents against a rule that does not exist.

**Found:** while checking the redsign mechanic against the tutorial copy, not
from a play report — so nobody has been bitten by it yet that we know of.

**The disagreement:** §4.11 says the beacon "does **not** clear when the seam is
harvested out (mirroring blue-sign's persistence)", and makes a strategic point
of it: "Depletion must be inferred from observed rival activity, not from the
sign." The engine does the opposite. `_retire_redsign_if_spent` flips
`region["live"] = False`, stamps `spent_day` / `spent_by` and logs `RED SIGN
spent — the pure seam near (~x,~y) is exhausted`, with
`_sweep_redsign_liveness` as a backstop.

That is not a small difference in play. Under the prose a stale beacon is bait
and reading it is a skill; under the engine the board tells you for free when a
jackpot is gone.

**Not resolved here** because it is a genuine design question, not a typo —
either the prose is stale and §4.11 needs rewriting plus a changelog entry, or
the retirement is the regression. Whoever picks it up: the surfaces are §4.11,
the agent view's `redsign` block, the map smear in `app.js`, and the Advanced
tutorial's night-two card, which currently follows the engine by staying silent
on the question.

## 37. ✅ (DONE, v1.37) The cell card told you a harvested square banks 0, right under the −100

**Found:** by shooting it. `basic_drop` now ends on a close-up of a square the
harvester just harvested, and the frame contains both of these lines, three
apart:

```
-100        charged per parcel at settlement
harvested (synthetic green — banking scores 0)
```

**Root cause:** the trail note in `_trailBits` (`server/static/app.js`) predates
the green penalty being surfaced in the tooltip at all. When the only thing the
card said about a farmed square was that note, "banking scores 0" was a rough
way of saying "there is no RED left here to bank". Once the score block started
pricing GREEN properly the note stopped being a simplification and became a
contradiction — and the wrong half is the one a player is likelier to believe,
because it is the one written in words.

It is also wrong on its own terms. A GREEN parcel is charged
`GREEN_ENDGAME_PENALTY` (100) per parcel at settlement (§4.7,
`orbit_resolver._settle_green_auto`), and entry harvests are not optional
(§3.4), so a harvested square is not inert — it is a liability for whoever
walks it next, its author included.

**Fix:** the note now reads `harvested (synthetic green — costs whoever picks it
up)`, which agrees with the score block instead of arguing with it.

**Worth noting for the next person:** no test caught this and no test easily
could — both strings were individually correct-looking and the bug only exists
when they are read together. It surfaced because a film pointed a camera at the
card and held it there for four seconds.

## 38. ✅ (DONE, v1.42) V12 read its opponent's journal as its own, on Snowflake only

**Status (v1.42):** Fixed in `_v7/memory.py` (both the V12 copy and the
`emp_harvest_test` fork's copy), and pinned by a repo-wide test.

**Symptom:** none, which is the point. The agent's STRATEGY JOURNAL simply
contained nights it had not played, phrased in the first person, and it
reflected on them as its own. On a file store nothing was wrong; only
Snowflake seasons were affected.

**Root cause:** `SOC_AGENT_MEMORY` is a **hybrid (Unistore) table** with
primary key `(SESSION_ID, PLAYER, KIND)`. `_hydrate_from_snowflake` loaded
a seat's journal with

```sql
WHERE session_id = ? AND player = ? AND kind LIKE 'arena:day%'
```

and a **range predicate on a primary-key column makes the scan stop
honouring the equality on the preceding key column**. Proven against the
standard-table backup of the same rows that already exists in the account:

| table | `player='p1' AND kind LIKE 'arena:day%'` |
|---|---|
| `SOC_AGENT_MEMORY_FDN_BAK` (standard) | 7 rows, all p1 — correct |
| `SOC_AGENT_MEMORY` (hybrid) | 14 rows — p1's 7 **and p2's 7** |

Scope is narrow and worth remembering: a range predicate on a **key**
column breaks, on a **non-key** column it does not. That is why the only
other `LIKE` in the repo — `season_name LIKE 'eval:%'` against the equally
hybrid `SOC_GAME_SESSION` — is unaffected, verified by counting.

The damage compounded quietly. Entries are filed into a dict keyed by day,
so a rival's day-3 row overwrote the agent's own rather than appearing
beside it. Nothing logged, nothing duplicated, no error — just the wrong
memories, and prose plausible enough to read as your own.

**Fix:** select on the two equality columns only and filter `kind` in
Python, re-checking `PLAYER` on every row on the way through, since
nothing in the result can be trusted to satisfy a predicate the scan
dropped. At most ~10 rows per (session, seat), so there is no cost to it.

**Tests:** `turnlab/tests/test_lab.py::test_no_query_filters_agent_memory_with_a_range_on_kind`
parses every file's real SQL string literals (skipping docstrings, which
discuss the bug at length) and fails on any `kind LIKE` / `STARTSWITH(kind`
against this table. It is deliberately repo-wide rather than scoped to
V12: `_v7/memory.py` is copied wholesale into every fork, so the bug
ships again with each new agent unless the whole tree is held to it.

**Not done:** finished seasons were not re-scored. The contamination is
in what agents *read*, not in the engine's record of what happened, so
standings stand; but a pre-v1.42 Snowflake season's agent reasoning
should be read with this in mind.

---

## 39. ✅ (DONE, v1.34) A page opened mid-season captioned day 1 as the latest news

**Status (v1.34):** Fixed. Found while verifying the arsenal bar, but it
is older than that feature and had nothing to do with it.

**Root cause:** `station.js` resolves the day its hover cards read with
`_osDay()`, which prefers `_currentDay` — the day of the last rendered
tick. The replay rewind that runs at boot deliberately parks
`_currentDay` on the *first* day of the season, so that the reel's
opening DUSK renders what it should. Nothing moved it forward again
until a night actually played. So on a page opened in the middle of a
season, every station hover card served day-1 readings — a rival's hold,
fissile grade and green estimate as they were on the opening night,
under a heading that says "end of Nox".

Nobody had reported it because the fuzzy rows degrade quietly: "hold
empty · fissile medium · ~0 green" is a plausible reading of almost any
day. The arsenal row is what made it legible — an exact `0/600b` sitting
next to a station whose own bar showed two lit cyan pips is a
contradiction on one screen, and that is how it was caught.

**Fix:** `app.js` exposes `window._osLatestObsDay()` (the newest day with
`pre` readings, the same resolver the RECAP button uses), and `_osDay()`
prefers it whenever the UI is not showing the past. Replay is untouched:
`_showingPast` gates the new branch, so a scrub still reads the day under
the cursor.

**Verified:** `backstage/probes/_probe_arms_bar.py` hovers a rival mid-season and
prints the card; it read `arsenal 0/600b` against a true 200 before, and
`200/600b` after.

---

## 40. ✅ (DONE, v1.34) Queueing an orbit action never re-checked what you could afford

**Status (v1.34):** Fixed. Also pre-existing, also found while verifying
the arsenal cap.

**Root cause:** `_updateOrbitAffordability()` ran only from the orbit
panel's render, which is driven by the status poll and only redraws when
the *server's* view changes. The queue is client-side, so adding a row
never triggered it. The dimming and the "short 205b" notes therefore
described the opening wallet for as long as you stood at the desk, even
though `projectOrbitBudget()` — which the same function calls — exists
precisely to account for the queue. The v1.25 comment above it ("what you
can afford is a question about the remainder, not the opening balance")
was describing an intent the wiring did not deliver.

Harmless-ish before v1.34, because a queue the wallet cannot cover is
part-filled in declared order and the queue rows say so. It stops being
harmless with the arsenal ceiling: the cap is the one limit a queue can
reach entirely on its own — three EMPs queued against an empty rack is a
legal-looking order the engine will refuse on the third — and the
projection meant to dim it never ran.

**Fix:** `renderOrbitQueue()` now redoes the affordability pass against
`lastOrbitView`, with the damaged-harvester tally cached from the last
render (`_lastDamagedCount`) so it does not need a full redraw.

**Verified:** `backstage/probes/_probe_arms_bar.py` pins the cap at the seat's
current holding and reads the buttons; they now say `rack full 200/200b`.

---

## 41. ✅ (DONE, v1.45) A page opened at VESPERA showed no score until you pressed LIVE

**Status (v1.45):** Fixed. Pre-existing — reproduced unchanged on the
commit before the arsenal work, so the weapons feature only made it
easier to notice.

**Symptom:** open a game mid-season and both station readouts sit on
their `—` placeholder next to a live board that plainly has a score in
it. Pressing LIVE fills them in. The reporter's own diagnosis was the
useful one: *"it doesn't work if you don't have any red"*.

**Root cause:** every other thing that writes a station score is a
*beat* — a replay tick (`osOnReplayTick`), the orbital DUSK stage
(`osOnLiveDusk`), or the LIVE button (`osOnLive`). `_osInitPanels()`
built the skeleton, pre-populated `_stationObs`, refreshed the vaults
and stopped, so a page that had just opened had run no beat and the
readouts kept the `—` they are born with in `_osBuildStation`.

Why it hid for so long is the reporter's red: a seat that ships RED
resolves an orbit, `_stageLiveOrbitBeat()` sees activity in the
catapult blob and stages a DUSK beat, and that paints the scores. Play
a normal game from the New Game modal and the placeholder is gone
before you ever look at it. Ship nothing — or reload the page
mid-season, which runs no beat either — and there is nothing to paint
them.

**Fix:** `_osInitPanels()` now settles the readouts to the present as
its last act, using the same latest-day-across-`catapultByDay`
computation `osOnLive` uses, and immediately rather than tweened
(this is the cold open, not a scoring moment). Before the replay
payload exists — day one, or the skeleton built from the live player
list — it writes 0 rather than leaving `—`, because nobody has shipped
anything and zero is the true reading.

**Verified:** driven in a real browser against three games on a
scratch server. Mid-season with nothing shipped: `—,—` → `0,0` on
load, matching post-LIVE. Mid-season with a real score: `0, 217.75`
plus the leader highlight on load, identical to post-LIVE, where
before it was `—,—`. Day one: `0,0`. Scrubbing still tracks the tick
and LIVE still restores, so the init settle pins nothing.
`backstage/probes/_probe_arms_bar.py` passes all three scenes with no console
errors.

---

## 42. ✅ (DONE, v1.36) The turn lab offered two racks that every shipped board refuses

**Status (v1.36):** Fixed on the day it was created — found while
verifying SNAP rather than reported.

**Symptom:** pick the `snap` or `arsenal` rack on any board in `/lab`
and opening it fails with *"rack 'snap' needs snap, which this board's
season does not stock"*.

**Root cause:** not the refusal — that is correct and deliberate. Weapon
prices are stamped per game (§4.9.8), so a night frozen before SNAP
existed has no SNAP in its economy, and `arm()` declines rather than
writing a rack that board's own engine could never have sold. The bug
was upstream of it: `catalogue()` listed every rack unconditionally,
and all ten shipped boards were minted before v1.36. So SNAP arrived in
the lab as two menu items that could not be chosen on any board in the
library — a menu whose items were traps.

**Fix:** `catalogue()` takes the board's blob and marks each rack
`available`, with a reason when it is not; `/api/lab/racks` accepts a
`board` query and `lab.html` re-fetches per board, rendering the
unavailable ones disabled and labelled *n/a on this board*.

Marked rather than filtered, deliberately: "that weapon is younger than
this board" is worth telling an attendee, whereas a list that is quietly
shorter on some boards than others just reads as a second bug.

**~~Left open on purpose~~ — closed in v1.40.** No board in the library
could exercise SNAP, so the racks were correct but unreachable. Three
V12-vs-V12 seasons were played at the 1:2:3 ladder (seeds 7301–7303,
`scripts/_mint_snap_boards.py`), and four nights out of the twenty-one
were kept: `83e44557_d6`, `fad99794_d3`, `30890438_d2` and
`30890438_d6`. The other seventeen were deleted rather than shipped —
a library is a set of nights somebody chose, and twenty-one
undifferentiated ones is a haystack. All four price a SNAP, so the
`snap` and `arsenal` racks are live on them; `arsenal` on one of these
puts a seat at exactly the 600 cap, which is also the most ambiguous
total a rival can be shown (three EMPs, two chaff, or one of each), and
therefore the sharpest test of the v1.39 arsenal read.

Each carries the editorial note the machine could not write, and every
figure in those notes was read off the saved session rather than
remembered. The clause that decides how a night is played is marked
`[[like this]]` and the launcher renders it in yellow — a blurb is five
sentences of even grey otherwise, and the one fact that matters reads
exactly like the scene-setting around it. Escaping runs before the
markers are converted, so `<mark>` is the only tag a note can ever
produce.

---

## 43. ✅ (DONE, v1.36) You could fire a SNAP and watch nothing happen

**Status (v1.36):** fixed. Reported by a player on the first night they
fired one: *"no graphics play from the snap — nothing comes out of the
station and no missile hits the board."*

**Symptom:** exactly that. The launch resolved, the probe died, the drop
was refused, the arsenal bar dropped a pip, the log said so — and the
screen showed none of it.

**Root cause:** three faults that happened to add up to zero.

1. **The platform had no branch.** `station.js`'s arrival dispatcher
   knows `probe_emit`, `emp_launch`, `mine_emit`, `chaff_flare` — and
   never had one for `snap_launch`. `playSnapFx` called the hook
   correctly and the hook fell through every arm of the `if` and
   returned. Nothing was broken; the case was simply never written.
2. **The board FX was two orders of magnitude too faint.** SNAP flies
   1.5× faster than an EMP over the same distance, so its one-pixel
   trail was on screen about a third as long as an EMP's — and where an
   EMP answers its missiles with `_playEmpExpansion`, SNAP had no impact
   beat at all. Measured with `backstage/films/ink_check.py`, the strike
   peaked at **61 px** of amber against the EMP cloud's **12,180 px**.
   Every individual piece was present and correct. Together they were
   invisible.
3. **The scorch mark pre-empted its own missile.** `paintSnapCloudOverlay`
   runs synchronously when the tick paints, so the mark appeared ~0.4s
   before the round arrived and the missile then flew into a square that
   had visibly already been hit — the exact "board moves before the
   sprite lands" trap AGENTS.md warns about. The EMP is defended from
   this by `_empExpansionActive`; SNAP had no equivalent.

**Fix:** a `✚` launch glyph in amber on the platform, at 1470ms so the
weapon does not read as slow in the one place it is meant to read as
fast; an opt-in `weight`/`headSize` on `spawnProbeTrail` (probes and
EMPs keep their exact previous behaviour) so SNAP flies a heavier round;
a new `_playSnapImpact` — a cross rather than a ring, because a ring
would claim ground a single-square weapon never touched; a scorch mark
at usable opacity; and a `_snapImpactActive` gate, with a watchdog,
holding the mark until the round lands.

**Why nothing caught it:** `tests/test_snap.py` pins the engine and
`backstage/probes/_probe_snap.py` pins the DOM, and both were green throughout —
the launch *did* resolve and the frame *did* carry the payload. The FX
is canvas-drawn, so there is nothing in the DOM to assert on, and the
one film of it went out as a minute of narration over an empty board.
The gap was that no check ever asked whether a thing was **drawn**, only
whether it **happened**. `backstage/films/ink_check.py` is that check, and
`backstage/probes/_probe_snap.py` scene 5 shoots the platform in isolation
against the EMP as a control.

**Redrawn in v1.40, because the fix over-corrected.** Everything above
made the strike visible; it made it visible in the wrong idiom. The
cross was drawn with `shadowBlur` and the scorch wore a border and two
box-shadows, so a SNAP became the only glowing object on a board that
is otherwise entirely flat fills and glyphs — it read as a lit sprite
from a different game. The cross also threw bars 2.1 cells out, which
claimed ground a single-square weapon never touches.

So it strikes in the EMP's own alphabet now. `_EMP_CHARS` became
`_ORDNANCE_CHARS` and SNAP shares it: the same block ramp
(`░░ ░▒ ▒▒ ▒▓ ▓▓ ██`), the same tile geometry, the SNAP amber. The two
weapons use it differently and that difference is the picture — the EMP
walks the ramp *across* a 13-cell rhombus so the field rolls, while
SNAP walks it *down* one square over time, landing on `██` and cooling
to a scorch that breathes between the two sparse rungs. Nothing leaves
the cell, no shadow anywhere, and the strike tile is the same
`.snap-cloud-cell` the hour sits on, so the mark arrives hot rather
than handing over to a different-looking one.

Measured the same way it was caught: **268 px** peak against the old
cross's 323 px, so it is flatter without being fainter in any way that
matters (the invisible first cut was 61 px). `adv_snap.webm` was
re-shot — a film is build output, and leaving it would have taught a
visual the game no longer draws. `backstage/probes/_probe_snap_ink.py` is the
new companion check: it asks what the mark *looks like* rather than
whether it happened, asserting the glyph comes from the shared ramp and
that the computed style carries no box-shadow, text-shadow or filter.
It also pins the property the redraw must not lose — it re-watches the
same round from the seat whose probe was destroyed, where the cell is
`cell--stale`, and confirms the mark still renders. A SNAP is fired
from an orbital station, so RULEBOOK §4.9.4 makes it open: every seat
sees it regardless of who can see the ground.

---

## 44. ✅ (DONE, v1.36) Three tutorial films were teaching the wrong day

**Status (v1.36):** fixed. Found by reading the day header out of every
Advanced film and comparing it against the reel that plays it.

**Symptom:** on the Advanced tutorial's last two turns, the film inside
the modal disagreed with the game behind it. The player was on day 4;
the recording said `# day 3 · orbit` and `NOX 03` in their own header
font. `adv_buy_chaff` was worse than stale-looking — it quoted **255
blue** for a flare that had cost 300 since v1.36, showed a
`build CHAFF ×1 (255b)` button, and closed on *"if you cannot afford it
this turn, that is not a mistake"*, advice the training subsidy had
made false. Three generations of drift in one clip.

**Root cause:** a film is a screen recording, so it carries the day it
was shot on burnt into the game chrome — and adding Advanced's fourth
night moved reels without re-shooting them. The source was corrected at
the time: `_ADV_TURN` moved `adv_buy_chaff` and `adv_arms_bar` to rig
turn 5, and `_adv_buy_chaff`'s narration was rewritten for 300 and the
subsidy. Only the `.webm` files on disk were not re-made, and nothing
anywhere compares the two. `adv_emp` was a third case with a different
shape: its reel moved to night 4 while `_ADV_TURN` correctly kept it on
turn 4, so source and film agreed with each other and both disagreed
with the reel.

**Fix:** `adv_buy_chaff` and `adv_arms_bar` re-shot as they stood.
`adv_emp` needed the storyline changed before it could be shot on night
four at all — the canonical night 3 spends the EMP, orbit 4 buys a
second hull the film's fleet-row selector cannot disambiguate, and the
rival hot-drops onto the square the walk-in walks at. `_EMP_TAKES` in
`make_tutorial_films.py` is the three-branch detour that leaves those
two takes an unspent weapon, one hull and a rival holding still. The
lesson survives the move: rushing still banks nothing, waiting still
banks the jackpot (1078).

**Why nothing caught it, and what does now:** the existing tests check
that every reel names a film that exists and that no film is an orphan
— both were true throughout. `test_a_film_is_shown_on_the_day_it_was_
shot_on` now compares each reel's day against its film's shoot turn,
which catches the `adv_emp` shape. The stale-`.webm` shape **cannot**
be tested, because the only evidence is inside the video; the rule is
that moving a reel between days means re-shooting its films, and
`docs/TUTORIAL_PLAN.md` §9 says so where a reader will meet it.

---

## 45. ✅ (DONE, v1.38) Every agent was handed every seat's night log

**Symptom:** none, which is why it lasted. Nothing misbehaved, no test
failed, and no season looked wrong. The defect was in what the percept
*offered*, not in what anything did with it.

`agent_view.recent_log` carried the raw shared feed. On a plain
heuristic-vs-heuristic season, 11 of the 20 rows in p1's own
`recent_log` were about p2 — including exact landing coordinates, which
§3.15 has always made private, and this, which is worse than
coordinates:

```
[RED_HARVEST] [heuristic] (p2) harvester_p2 HOT-DROP:
    secure probe@[11, 1] + drop@[11, 2]→RED@[11, 1]
```

That is the opponent's plan, in prose, in the percept, a turn before it
happened.

**Root cause:** the log is written for the watcher. One feed per
session, `store.list_log` takes no seat, and a row is `(day, seq, level,
text)` with no owner column to filter on. `get_view` fetched twenty rows
and `build_agent_view` passed them straight through to the emitted key.
The *derived* blocks were always careful — `last_night` filters to the
asking seat with `_is_my_row`, `competitor_intel` lifts only
`probe_launch` — so the fog rule was understood and implemented one
layer above the hole.

**Why it was never a live cheat:** no harness reads the key. Grep
`recent_log` across `orchestrator_2/` and there are no hits: V12, `_v7`
and `emp_harvest_test` all build their prompts from the structured
blocks. It is fixed anyway, because a fork is a directory anyone can
write, `recent_log` is a documented key, and an attendee whose fork
reads it has not broken a rule anyone wrote down — which makes it our
problem to adjudicate, on the day, in front of the room.

**Fix:** `_log_for_seat` in `snowpark/view.py` cuts the emitted list to
rows naming no other seat, matching on seat id plus display name and tag
so a season with named Houses redacts like an anonymous one. Two choices
inside that are worth keeping:

- **By mention, not by subject.** A row naming two seats is withheld
  from both. Blunter than parsing the subject out of free text, and
  chosen because that parse is exactly what is already being done
  crudely upstream — this one fails toward fog instead. Cost is the odd
  line about your own unit (`expired probe_p1_3, probe_p2_5`), which the
  seat learns from `my_assets` regardless.
- **Rows naming nobody are kept.** Settlement and phase transitions are
  the clock, and a seat unable to date its own log is a worse bug than
  the one being fixed.

The filter lands on the emitted key **only**. `_last_night_recap` and
`_competitor_intel` still receive the unfiltered list, because their
entitlement to it is the point — tidy the filter upstream and a seat
silently stops seeing rival probe launches it is lawfully owed.
`tests/test_log_stays_in_its_seat.py` pins that direction as well as the
leak itself.

**Still open, deliberately:** the twenty-row window is cut *before*
redaction, so a seat now gets fewer than twenty of its own rows —
cosmetic while nothing reads the key, and not worth coupling the fetch
in `engine.py` to the filter in `view.py` to fix. Separately, the human
LOG panel is a different feed (`get_session_status`) and was not
touched; whether a player should read a rival's coordinates there is a
UI fog question, not this one.

---

| # | Area | Severity | Blocking multiplayer? |
|---|------|----------|-----------------------|
| 1 | Vision / trails (echo coverage — now fog-frozen) | ✅ done (v1.8) | no |
| 2 | Vault capacity (15) + capacity warning | ✅ done | no |
| 3 | Multiplayer praxis submit UX (committed waiting frame) | ✅ done | no |
| 4 | Multiplayer praxis animation (non-last clients) | ✅ done | no |
| 5 | Station UI: LAUNCH DELAY button placement | ✅ done | no |
| 6 | Board state pops before animation (drop/probe/harvest/trail) | ✅ done | no |
| 7 | End-game report: attacker→victim kill-feed + personal ops (v1.6) | ✅ done | no |
| 8 | Final game-resolve step (last catapult + vault cleaning) | ✅ done | no |
| 9 | H0/DUSK step + LIVE-return bugs (dawn lock, `+X` fold, blue bar) | ✅ done | no |
| 10 | Vault ↔ Orbital Vault ↔ actual disagreement (DUSK shed reconcile) | ✅ done (v1.9) | no |
| 11 | 4-player Snowflake praxis slowness (persistence payload) | 🟠 high (P1+P3 batched; P2/P4/P5 open) | no |
| 12 | Probe trails missing in live PRAXIS cinematic | ✅ done | no |
| 13 | Night cinematic differed per seat / lost by a background tab | ✅ done (v1.17) | no |
| 14 | Crushed probe echoed its killer twice (stale disk, dead observer) | ✅ done (v1.19) | no |
| 15 | Weapon launch + a second action in the same hour (collision pre-passes ignored pre-empted seats) | ✅ done (v1.19) | no |
| 16 | Chaff couldn't stop a launch; chaff chained into a lock (RULEBOOK §4.9.3 ⇄ §4.9.5 contradiction) | ✅ done (v1.19 / RULEBOOK v1.14) | no |
| 17 | Probe-launch markers never expired (sweep behind an early return; merged glyphs skipped) | ✅ done (v1.19) | no |
| 18 | Supersede/annihilation had no graphic at all; incumbent mis-ledgered by seat order (§3.16 silent on it) | ✅ done (v1.19 / RULEBOOK v1.15) | no |
| 19 | §3.15 launch marker outlived a death its owner watched happen | ✅ done (v1.19) | no |
| 20 | Collision: ring fired ~3s early, and the damaged harvester lifted off before the impact (cross-frame sequencing within a tick) | ✅ done (v1.19) | no |
| 21 | Doomed probe held in the DOM but painted under the opaque terrain stand-in, so it vanished at hour open | ✅ done (v1.19) | no |
| 22 | V12 had no cross-night memory on Snowflake: `SOC_AGENT_MEMORY` was never deployed and every write/read swallowed the failure | ✅ done (v1.19) | no |
| 23 | Snowflake picker scored off a SQL view that credited disposed GREEN at +255 instead of charging −100 and applied no tier multiplier, inverting a finished season's winner | ✅ done (v1.20) | no |
| 24 | A same-hour EMP voided a rival's queued landing: the hour-start vision snapshot was taken after the pre-hour phase had already fired the salvo and destroyed the beacon (§3.9.7 ⇄ engine) | ✅ done (v1.28) | no |
| 25 | A jackpot sat in trace (79% had no mass within 4.5), so "richer ground means warmer" was not a readable signal — new graded-deposit pass (§2.2). **Doubles map RED: pre-v1.29 scores are not comparable.** V12's `_ASSUMED_HALO_PURITY` fallback measured (reached on only 1% of seams, so never the knock-on it looked like) and retuned 55 → 105 | ✅ done (v1.29) | no |
| 26 | A stale `mine_lay` reached the engine unfiltered (the v7 sanitiser never rejected it) and wasted a slot as a nameless "unknown action" — resolved by retiring the caltrop and refusing both tags by name (§4.9.4); old saves clear armed caltrops and refund unspent stock as blue | ✅ done (v1.31) | no |
| 27 | `two_seams_choose_one` red at HEAD: the heuristic walks the other seam, so `HarvesterChainHits` sees 0/1. Predates the tutorial work; suspects are the three recent seam/jackpot pricing commits | 🔴 open | no |
| 28 | The tutorial modal never auto-opened again after one play: the shown-already set was keyed on reel names that repeat in every Basic game, so a returning player got a teaching mode that taught nothing. Now scoped per game id | ✅ done (v1.33) | no |
| 29 | A camera move stranded the four pixel-anchored overlays on the previous board's geometry — `reanchorOverlays` was locked inside `applyMapZoom`. Now exposed and called every frame of the glide | ✅ done (v1.33) | no |
| 30 | A rival's bounce in a simultaneous-drop pile-up was suppressed as a private landing (§3.15), so the viewer watched one lifter hit an empty square while the caption named the House it hit. §3.15 already discloses mutual destruction | ✅ done (v1.33) | no |
| 31 | `POST /policy` and `/orbit` silently discarded a queue sent under the wrong field name and reported `ok` — a typo and a deliberate pass were indistinguishable | ✅ done (v1.33) | no |
| 32 | The board parked top-left in a frame wider than itself. Centred via `margin: auto` on the grid rather than `justify-content` on the scroll container, which would have made a zoomed map's left column unreachable | ✅ done (v1.34) | no |
| 33 | `basic_crash` pushed in before the landing collision, so two converging orbital arcs both started off-screen. Hour one now plays wide, and the beat is repeated hand-cranked off the replay bar | ✅ done (v1.34) | no |
| 34 | Every film close-up was a CSS transform on one live element inside a fixed layout, which reads as a rendering fault, drags four overlays out of alignment (issue 29) and can only ever point at the map. Replaced by a post-process crop over the recorded frame | ✅ done (v1.35) | no |
| 35 | Fallout from 34: the caption is pinned to the bottom of the page, so a crop that keeps 492×308 of a 1280×800 frame left every close-up unnarrated. Caption now tracks the crop, and `say()` fails the shoot if it lands off the edge | ✅ done (v1.36) | no |
| 36 | RULEBOOK §4.11 says a redsign persists after its seam is harvested out and depletion must be inferred; the engine retires it as `spent`. Design question, not a typo — flagged, not resolved | 🔴 open | no |
| 37 | The cell card called a harvested square "banking scores 0" three lines under its own `-100` score block. Stale note from before green was priced in the tooltip; green costs 100/parcel at settlement and entry harvests are compulsory | ✅ done (v1.37) | no |
| 38 | On Snowflake, V12 loaded its opponent's STRATEGY JOURNAL as its own: `SOC_AGENT_MEMORY` is a hybrid table and a range predicate on the key column `kind` makes the scan drop the equality on `player`. Filter `kind` in Python instead; a non-key column is unaffected | ✅ done (v1.42) | no |
| 39 | Station hover cards read day 1 all season on a page opened mid-game: the boot rewind parks `_currentDay` on the reel's first day and only a played night moved it. Live play now resolves the newest day with readings; replay still reads the cursor | ✅ done (v1.34) | no |
| 40 | Queueing an orbit action never re-ran the affordability pass, so the dimming described the opening wallet rather than what the queue left of it — and the arsenal ceiling, which a queue can hit on its own, could never dim anything | ✅ done (v1.34) | no |
## 46. ✅ (DONE, v1.39) The prompt described a rack twice the size of the cap

**Symptom:** a seat that had weaponised the full 600 blue appeared in
V12's prompt as:

```
  p2: emp=[0..3] chaff=[0..2]
```

Two independent ranges, printed adjacently, with nothing saying they are
alternatives. The natural reading — and, on the evidence of the plans
that came back, the model's reading — is "up to three EMPs *and* up to
two chaff". That is 1200 blue against a cap of 600. Of the seven racks
that actually fit 600, exactly one holds both an EMP and a chaff.

**Root cause:** `WeaponEstimate` stored only per-weapon marginals over
the decoded rack set, and the renderer printed them. Marginals are the
right answer to "could this seat have any chaff" and a false answer to
"what could this seat have", and the block was asking the second
question with a structure that only supports the first. The set was
never kept — `decode_rack` returned it, `update_estimates` reduced it to
min/max per kind, and the racks were discarded on the spot.

Two more defects sat in the same place:

* **A seat holding 100 blue rendered no block at all.** The filter was
  `emps_max > 0 or chaff_max > 0`, which a lone SNAP satisfies neither
  of — so the weapon that refuses a landing outright was the one a rival
  could hold invisibly. `WeaponEstimate` had no SNAP field to begin
  with, so this was structural rather than a stale condition.
* **`snap_hit` was not in the "this hit me" list**, so a seat could be
  SNAPped nightly and never react; and `_format_combat_event` had no
  branch for it, so when it did arrive it printed as a Python dict.

**Fix:** the estimate carries `racks` — the decoded set — and everything
written for a reader states it: "600 blue of ordnance — exactly ONE of
these 7", followed by the racks, the price ladder and the cap. The
marginals stay for the ~47 call sites that ask the one question they
answer honestly, now behind `could_hold(kind)`. Because a rack holding a
chaff costs at least the 300 a chaff costs, that method *is* the minimum
spend for a warning, with no threshold written anywhere.

**Engine half:** `snap_launch` was not an orbital event tag and the
activity tally had no `snaps` counter, so a fired SNAP left the public
silhouette flat. The strike is already public; the omission only cost
agents an inference a human reads off the board.

**Also found here:** the example fork `emp_harvest_test` never received
the v1.36 fix that decodes against the board's own price table, so it
read archived seasons at today's prices and saw unarmed seats where
there were armed ones. Both shipped harnesses are now pinned to one
reading of a public total by `tests/test_arsenal_reaches_the_agent.py`.
What a fork *does* with the arsenal is still entirely its own business.

**Near miss worth recording:** giving `card.py` an arsenal line tripped
rung 1 of the weapons-readiness ladder ("the agent knows it owns a
rack"), because that rung greps for a `weapon_stock` read outside the
buying code. A diagnostic render is not the night phase knowing
anything, and a fork handed a green bottom rung by a debug line would be
sent up the ladder with the rung missing. `card.py` joined the excluded
set, with a test.

| 41 | A page opened mid-season showed `—` for every station score until you pressed LIVE. Only a *beat* ever wrote a score, and a fresh page has run none; shipping RED staged a DUSK beat, which is why it looked like "no red breaks it". `_osInitPanels` now settles to the present | ✅ done (v1.45) | no |
| 42 | The lab's rack picker offered `snap` / `arsenal` on boards frozen before SNAP existed, so choosing either always failed. The refusal was right; listing them was not. `catalogue()` is board-aware and the picker disables what a board cannot hold. The four SNAP-priced nights that were missing were minted and noted in v1.40 | ✅ done (v1.36, v1.40) | no |
| 43 | Firing a SNAP showed nothing: `station.js` had no `snap_launch` branch, the board FX measured 61 px of ink against the EMP's 12,180, and the scorch mark painted before its own missile arrived. Launch glyph added, heavier round + a cross-shaped impact beat, and the mark now gated behind the landing | ✅ done (v1.36) | no |
| 44 | Adding Advanced's fourth night moved three reels between days without re-shooting their films, and a film carries its day burnt into the recorded chrome — so the modal said `NOX 03` over a day-4 board, and `adv_buy_chaff` still quoted 255 blue and pre-subsidy advice. All three re-shot (`adv_emp` needed `_EMP_TAKES` to leave it a spendable weapon on night four), and the reel-day/shoot-turn agreement is now a test | ✅ done (v1.36) | no |
| 45 | The engine's night log is one shared feed with no owner column, and `get_view` put all twenty rows on every agent's percept: a rival's landing coordinates (private, §3.15) and a rival bot's rationale line in full. Nothing shipped read it. `recent_log` is now cut to rows naming no other seat; the blocks entitled to the whole log still get it | ✅ done (v1.38) | no |
| 46 | V12's prompt rendered a rival's 600-blue arsenal as `emp=[0..3] chaff=[0..2]` — independent marginals that read as a joint range, describing 1200 blue under a 600 cap. A lone SNAP (100 blue) rendered no block at all, and `snap_hit` reached neither the reaction path nor the event renderer. The estimate now carries the decoded rack set and states it as "exactly ONE of these N"; warnings gate on `could_hold`, which carries its own minimum spend. Engine: `snap_launch` now counts in the public activity tally | ✅ done (v1.39) | no |

## 47. ✅ (DONE, v1.40) The SNAP doctrine named a move the menu could not offer

`DOCTRINE_BEWARE_SNAP` (v1.39) told the agent that a grab resting on one
probe could be insured by putting a second probe over the same cell. That
was correct advice and an unreachable move: the agent plans by picking ids
off the option menu, and no id for it could ever be generated.

**Root cause.** `_v7/probe_hints.py` answers one question — where do I open
fresh ground — and rejects a re-cover in three independent places.
`_seed_candidates` only seeds cells outside live vision. `_MIN_PROBE_SEPARATION`
(5) drops any seed within Chebyshev 5 of a probe this seat has *ever*
launched, and a probe covering the same cell as another is by construction
within 4 of it. Then any survivor with no fresh fog is dropped outright,
under a comment stating that re-probing a cell already in a friendly disk
"wastes an hour and a probe slot".

Each of those is right about the question it answers, and the last one was
right about SNAP too — until SNAP existed. A cell a rival can delete for
100 blue makes redundancy insurance rather than waste.

**Fix.** A separate generator, `_v7/snap_cover.py`, asking a different
question: which landing tonight rests on a single probe, and where else
could a probe stand that sees the same cell. It runs only when a rival
`could_hold("snap")`, the seat has a probe to spend, and the menu offers a
landing worth insuring (a redsign beacon, or visible red at 128+), and it
offers at most two. Registered last in `build_registry` so it can name the
options it backs, as `PRSNAP*` under its own menu heading, with `kind`
still `"probe"` so the packager needs no new verb — `Option.menu_group`
is the seam that lets those differ.

Two geometric rules it exists to keep. `SNAP_RADIUS` is 0, so difference of
cell is what buys the insurance and the cover may never share a cell with
the probe it backs. And the cover may never stand ON the contested cell: a
SNAP aimed there denies the landing whatever else is true, so cover parked
on it fails in the one case it was bought for.

**Wording.** The option names SNAP in its title and again in its rationale,
because the doctrine that explains the danger and the option that answers
it are read pages apart. Nothing about it is binding: it states the cost,
states that the risk is unknowable, and says in as many words that an
unarmed rival makes it a wasted probe. `DOCTRINE_BEWARE_SNAP` and the
`beware_snap` wishlist tag were reworded to match — they now point at
`PRSNAP*` as something to weigh, where before they read as an instruction.

**One bug the unit tests could not see.** The gate took `estimates` and
iterated it directly. The harness holds them as `{seat: WeaponEstimate}`,
so iterating yielded seat *strings*, whose missing `could_hold` raised the
same `AttributeError` the pre-v38 fallback swallows — the gate answered
"nobody could be holding a SNAP" against a seat holding all three, and the
module was dead in the live pipeline while every test passed. The tests
passed a list; only driving `turnlab.turn.plan_only` with a stubbed model
on a real frozen board exposed it. `_estimates()` now normalises the shape,
and a test pins that a mapping and a list give the same answer. Verified
firing end-to-end afterwards: 4 of 40 armed board/seat combinations offer a
cover, which is about the rate a rare play should have.

Pinned by `tests/test_snap_cover_probe.py` (20 tests), which covers the
geometry, every gate that keeps it silent, and the non-binding framing.
Ported to `emp_harvest_test`. Deleting `snap_cover.py` and its one harness
call site retires the whole thing, which is the isolation SNAP was built
with.

## 48. ✅ (DONE, v1.40) A third of the v7 prompt was dead, and the dead part answered weapons questions wrongly

`_v7/prompt.py` is live code with a misleading name: V12 forked v7's
*feeding* layer (prompt assembly) but deliberately kept reusing its
*computation* layer, so thirteen of its formatters run on every turn. What
was not obvious is that the other nine functions — 470 lines of 1304 — had
been unreachable for two versions.

**Root cause.** `build_prompt` (188 lines) was superseded when v10 wrote its
own sectioned assembler; the standalone hint formatters were retired when
v11 made the option menu the single source of truth for actionable plays.
Neither removal deleted the originals, and `build_prompt` had three tests in
`test_tabula_v7_split.py` written against it, so it stayed green forever and
nothing ever flagged it — code whose only caller was its own test suite.

**Why it mattered rather than just being untidy.** The dead `build_prompt`
still carried the pre-SNAP weapons gating: a raw `getattr(e, "emps_max", 0)
> 0` check, no `could_hold`, no SNAP block. That is precisely the bug class
v1.39 fixed, sitting in a plausible-looking place — a grep for "where does
the EMP doctrine get attached" finds it before the live one in
`tabula_v12/prompt.py`. And since attendees fork V12 wholesale, every fork
in the room carries a copy of the wrong answer.

**Fix.** Deleted the nine unreachable functions, the `_ACTION_SCHEMA` nothing
imports, and the imports orphaned by their removal (1304 → 841 lines, both
harnesses, code byte-identical modulo import paths). The module docstring
now says what the file is and states plainly that nothing in it builds a
prompt.

The three `build_prompt` tests were **retargeted, not deleted**: the
contract they check (reasoning-first in thinker mode, the directive riding
high in mover mode) is real and still shipped, and nothing else asserted it
against v12's live assembler — the v12 tests pass `mode="thinker"` but never
check what it does. Pointing them at `tabula_v12.prompt` preserved the
coverage and removed the last reason the dead copy existed.

## 49. ✅ (DONE, v1.45) Credentials came from a file no other Snowflake tool reads

**Symptom.** An attendee with a working Snowflake setup — `snow connection
add` run, Cortex Code connected, the VS Code extension happy — still had to
configure the same account a second time, in a format nothing else
understands, before this kit would talk to Snowflake.

**Root cause.** The kit read a bespoke `key=value` file at `~/.ssh/sf_config`,
inherited from an internal tool. Three separate modules re-implemented the
same line-splitting parser and each hardwired that path. It was not broken —
it was consistent, documented and worked — but it was disconnected from the
Snowflake developer ecosystem: an existing named connection could not be
selected, key paths were managed twice, and the location under `~/.ssh`
implies an SSH config it is not. It also invited a PAT into a plaintext file
that has no business holding one.

**Fix.** `sea_of_colours/snowpark/sfconn.py` is now the single door to a
credential, and it reads the standard store: `~/.snowflake/connections.toml`,
then `config.toml`, honouring `SNOWFLAKE_HOME` and `default_connection_name`
so we never disagree with `snow connection test`. `SOC_SNOWFLAKE_CONNECTION`
picks a section for the game alone, leaving a shell default untouched. The
legacy file still works, read last, deprecated rather than deleted — breaking
an install the week of the event is worse than twenty lines of compatibility.

Three things worth keeping in mind if this is ever revisited:

- **A name that does not resolve is refused, not swapped.** Falling back to
  a different connection when someone typos `SOC_SNOWFLAKE_CONNECTION` is how
  you spend an afternoon debugging permissions on a warehouse you were never
  pointed at. The error lists the names that do exist.
- **The PAT resolves separately, and carries its account with it.** A
  key-pair connection is complete and correct and still has no bearer token
  in it, so the Cortex path falls through to the legacy file when the
  resolved connection has none — which is exactly the shipped dev machine.
  Account and token travel together because a PAT is scoped to the account
  that issued it, and mixing them fails as an unexplained 401.
- **`soc doctor` now names the file and section it read.** "No credentials"
  and "the right file, the wrong one of five connections" used to print
  identically, and they have completely different fixes.

**Caught in passing.** The first draft of the test module let one case fall
through to the developer's real `~/.ssh/sf_config`, and the failing assertion
printed a live PAT into the terminal. The fixture now redirects the legacy
path for every test in the module: a suite must not be able to read, let
alone echo, a credential belonging to the machine it happens to run on.

## 50. ✅ (DONE, v1.46) A multiplayer game "went out of sync" — eight faults, one outage

**Symptom.** A three-player game behind the Multiplayer tunnel became
unplayable twice in two hours. Invite links stopped working with no error
anywhere; players who had already submitted sat watching "LOCKED IN —
waiting for <names>" forever; each of them concluded they were waiting on
someone else. The game itself was never damaged — Snowflake-backed,
server-authoritative, and it advanced from day 4 to day 6 across the whole
episode — so what actually went out of sync was *addresses and beliefs*,
never state.

**Root cause.** Four independent defects, each of which alone would have
been survivable. Four more came out of the post-mortem — three from
asking why the *first* provider lost, and one from noticing that the fix
for the fourth watched for the wrong kind of death.

1. **The provider downgrade was silent.** `server/tunnel.py` tries
   providers strictly in order — Cloudflare first, because its hostname
   survives the whole process, then localhost.run, whose anonymous names
   rotate. Falling through is therefore a *positive failure* of the
   provider above, never a race. But `start()` built a `failures` list and
   returned the instant a later provider won, dropping it; and
   `_scrape_url` drained each provider's output hunting for a URL and
   discarded every other line. So a successful start looked identical
   whichever provider answered, and this host — with `cloudflared`
   installed and working, as a later quick tunnel confirmed — spent the
   session on the rotating fallback with nothing to say so.
2. **The one warning that did fire misdiagnosed itself.** The share card
   correctly warns when `rotates` is set, then advised "Installing
   cloudflared avoids it" — to a host who had cloudflared installed. It
   read as "unavoidable on your setup" when the true answer was "press it
   again".
3. **A hung poll retired live-sync permanently.** This is the one that
   made it feel like a freeze rather than an outage. `pollLiveSync` set
   `liveSyncBusy = true`, awaited `fetch` with **no deadline**, and
   released the latch in `finally` — i.e. only if the promise settled. A
   tunnel edge that accepts a connection and never answers produces
   exactly that. Measured in a browser: **one poll attempted in fourteen
   seconds of outage**, against the five the 2.5s interval owes. So the
   page did not degrade, it stopped, and could not recover on its own even
   when the connection came back.
4. **And every failure was swallowed.** `catch (_e) { /* transient */ }`,
   plus a bare `return` on `!res.ok` that never even reached it — which is
   precisely where a 503 from a dead tunnel edge landed. "Transient" was
   an assumption with nothing to test it against. Because the committed
   wait frame deliberately locks the button until the poller sees a phase
   change, a player in it had no button, no error, and a headline naming a
   teammate.

**Fix.**

- `tunnel.py` keeps the skip record (`status()["skipped"]`: provider,
  reason, and a bounded tail of what it actually printed), through the
  winning return and through tunnel reuse, cleared on stop.
- The share-card warning derives its remedy from `providers` and
  `skipped` instead of assuming, so an installed-but-lost provider is
  reported as "tried, failed because X, try again" .
- The poll takes an `AbortController` deadline, so it always settles and
  the latch always clears.
- Offline is declared on **two** conditions together — consecutive
  failures *and* time since the last good poll — because the two outage
  shapes fail on different clocks: a refusal or a 503 fails instantly, a
  hang only fails when its deadline expires.
- A locked-in player's headline is replaced with `CONNECTION LOST`, which
  outranks every "waiting for" branch beneath it.

**The part that nearly shipped half-fixed.** The headline alone was not
enough. The committed-wait overlay is built by the submit handler and does
**not survive a reload** — so the very player most likely to be affected,
the one who has just been handed a fresh invite link after a rotation, has
no overlay to write into, already-submitted orders on the server, and a
screen painted entirely from the last successful poll that simply freezes.
Hence `#cc-offline-banner`, drawn from our own counter and nothing else:
it is the one element that can still speak when the payload cannot. It
says the sent orders are safe, deliberately — the instinct it exists to
prevent is reloading, re-planning and re-sending a turn already committed.

**Verify with `backstage/probes/_probe_connection_lost.py`.** Four scenes: the
three outage shapes (refused, 503, hung) and the reloaded player. Each
asserts the page names the connection *and* clears afterwards — a warning
that cannot clear is its own false alarm. `tests/test_tunnel_providers.py`
covers the skip record, including the starved-provider case (out of shared
budget is not the same as failed) and the bounded tail.

**Three more, found by asking why the *first* provider lost.** The four
above are all about surviving the downgrade. These are about not taking
it in the first place, and the last one about not staying down.

5. **The preferred provider got exactly one attempt.** Its failure modes
   are dominated by timing — a URL printed a second after we stopped
   waiting, a DNS record not yet published — and those are coin flips,
   not verdicts. Losing one bought the whole session on a provider that
   rotates. It now gets a second attempt (`_ATTEMPTS_PREFERRED`), *funded
   from spare budget only*, so a provider that fails fast is retried and
   one that fails slowly has already spent its second chance. The last
   provider is never retried: nothing behind it needs protecting and a
   second try only delays an honest failure.
6. **The DNS gate spent the fallback's time.** The gate ran *after* the
   per-provider polling loop, so it fell outside that provider's `wait_s`
   while still consuming the shared deadline. Worst case, Cloudflare
   could publish slowly, fail the gate and walk off with 48s of a 60s
   budget, leaving localhost.run 12s against the 25 it needs merely to
   negotiate SSH — "no tunnel at all" caused purely by accounting.
   Latent: never observed, found by adding the numbers up. Each provider
   is now costed at publish + gate (`_provider_cost`) and each attempt
   holds back a *floor* for the providers behind it (`_provider_floor` —
   enough to succeed, deliberately not enough to fail slowly in, because
   reserving two worst cases exceeds any budget a human waits through).
   A squeezed gate sheds retries and never its grace, since being early
   is the failure that poisons the resolver for half an hour (see the
   v1.18 note above). The endpoint budget is 60s → **75s**, which is the
   point where Cloudflare keeps its full 20s publish window and three
   lookups while localhost.run keeps its 37s floor: at 60s the honest
   accounting squeezed the *stable* provider hardest, which would have
   pushed the room onto the rotating one by arithmetic.
7. **Nothing restarted a dead tunnel.** The watchdog noticed the
   subprocess had exited and returned, so a game went off the air
   permanently and silently at whatever hour its provider gave up. It now
   relaunches, bounded (`_MAX_REVIVALS`), and reports `revivals` /
   `reviving` / `expected` from `status()` so the four states a host has
   to tell apart — never started, up, briefly down, down for good — are
   finally distinguishable. Two details matter more than the restart:
   death is now detected in ≤0.5s rather than at the next 30s probe tick
   (`_settle` — an HTTP round trip is expensive, `poll()` is free, and
   conflating them cost half a minute of guests staring at a dead link);
   and the new URL is **printed to the server console**, because every
   browser including the host's is stranded on a hostname that no longer
   exists and cannot be told the new one by a server it can no longer
   reach. The console is the only channel left. `stop()` clears the
   intent that `_stop_locked()` deliberately does not, so a human closing
   a tunnel is never undone by its own supervisor.

8. **And the restart watched for the wrong death.** Fault 7 fires on the
   subprocess *exiting* — but the outage at the top of this entry was the
   opposite shape: `ssh` stayed alive and healthy while the edge served
   503 for twelve minutes on a hostname localhost.run had already moved
   on from. `proc.poll()` is `None` throughout, so the revival would
   never have fired on the very incident that motivated it. The liveness
   watchdog had detected it correctly since v1.16 and written
   `serving: false`, and nothing ever read that field — the one failure
   we had genuinely observed was the one we did not repair. A dead route
   now restarts the tunnel too, after `_ROUTE_DEAD_AFTER` consecutive
   failures (~90s at the 30s probe interval) rather than the ≤0.5s a
   process exit gets, because a false positive changes the hostname and
   costs everyone their links. A tunnel that has **never** answered is
   deliberately excluded: with no prior success we cannot separate a dead
   edge from a probe path that never worked from this machine, and
   restarting on that would loop over a tunnel guests can reach fine.

**What the players see.** A restart splits the room, and the halves
cannot help each other: whoever is still connected holds the only working
copy of the new address, and everyone else is on a hostname that no
longer resolves and cannot be *told* anything, because reaching the
server is precisely what they have lost. There is no push that fixes
that. So the recovery is a human passing one URL along and the UI's job
is to make that the only step — `showTunnelDownModal` is one component
with two faces: `restarted` (fresh per-seat links and QRs, for whoever
can still reach us) and `stranded` (a box to paste the new address into).
It works because only the *origin* of a seat link changes: session and
seat are already in the stranded page's own URL, so it rebuilds its own
link and the host never has to work out who needs which one.

That also fixed a promise we should not have made. The offline banner
ended "this page catches up on its own when it returns", which is true
for a LAN blip and false after a restart — that address never returns,
and a player who believes it waits forever. It is now conditional on
`_looksLikeTunnelOrigin()`, itself a negative test (not loopback, not a
private range) rather than a list of the two providers we ship, so an
unknown third provider fails safe instead of being classified as a LAN
game and told to sit tight.

**Verify with `backstage/probes/_probe_tunnel_down.py`** (both halves in a real
browser; uses Chromium's `--host-resolver-rules` to put the page on a
genuine tunnel-looking hostname, since the origin classifier is the thing
under test and `127.0.0.1` would not exercise it). The load-bearing
assertion is that pasting the new address lands the player back in
*their own* seat — it joins as `p2` deliberately, because a bug that
rebuilt the link from a default seat would pass as `p1` and be invisible.

**Still open, deliberately.** A reloaded player who has already submitted
gets an *enabled* TRANSMIT button, because nothing on load restores the
committed lock from `pending[MY_SEAT]`. Worth doing; not this bug.
