# sagar_cursor ⇄ game engine — the interface, labelled

The single most important property of this harness: **it is a client of the
engine, not part of it.** It reads a fogged view, thinks, and hands back a list
of moves. It never mutates game state directly, never reaches around the view to
read ground truth, and never edits anything under `sea_of_colours/game/`.

This file states exactly where the boundary is, so a change on either side can
be checked against it. If you find harness code that violates one of the
invariants in §5, that is a bug, not a shortcut.

---

## 1. The boundary in one picture

```
          ┌──────────────────────── ENGINE (authoritative) ──────────────────────┐
          │  game/session.py · game/simulator.py · snowpark/engine.py            │
          └──────────┬──────────────────────────────────────────┬────────────────┘
                     │                                          ▲
       READ  (1 way in)                                WRITE  (2 ways out)
                     │                                          │
        build_agent_view(session, seat)              submit_policy(...)   NIGHT
        → the fogged `view` dict                     submit_orbit_actions(...) ORBIT
                     │                                          │
          ┌──────────▼──────────────────────────────────────────┴────────────────┐
          │                      sagar_cursor  (this package)                      │
          │                                                                      │
          │   view ─▶ derive ─▶ OPTION MENU ─▶ [ LLM picks IDs ] ─▶ packager ─▶  │
          │                                                        sanitizer     │
          └──────────────────────────────────────────────────────────────────────┘
                     │                                          ▲
        list_replay_frames(...)                     memory / journal rows
        (engine truth, last night)                  (harness-owned, see §4)
```

Everything the agent believes about the board comes through `view`. There is no
second channel. The harness is handed the view by the dispatcher — it does not
build it and cannot widen it.

---

## 2. READ — what comes in from the engine

### 2.1 The night view (the only board input)

`run()` receives `view`, the dispatcher's envelope, whose `agent_view` key holds
the seat's fogged board as built by `snowpark/engine.py:build_agent_view`. The
top-level keys v12 consumes:

| key | what the harness takes from it |
|---|---|
| `meta` | day, phase, and the live rules (`drop_mode`, `probe_radius`, `probe_lifetime_nights`) — read dynamically, never hardcoded |
| `hud` | day, `season_day_cap`, `scores`, `season_name` |
| `world` | `live` (current vision) and `echo` (cells seen before, with `last_seen_day`) |
| `red_tiles` / `blue_tiles` | visible resource cells and their purity |
| `redsign` / `blue_sign` | public smeared beacons over pure-red / fissile-blue seams |
| `entities` | harvesters, probes, stations — mine and any rival's that are visible |
| `probe_stock` | probes in inventory, the hard cap on how many the plan may spend |
| `competitor_intel` | rival probe sightings, harvester trails, `new_this_day` events |
| `station_intel`, `opponents` | seat identities and station positions |
| `last_night` | the engine's own recap of the resolved night |
| `combat_events` | EMP / chaff resolutions (an archived season may also carry mine events — see §3.1) |
| `orbit` | ORBIT-phase economy state (credits, build options) |

**Fog is respected as given.** If a cell is not in `world.live`, the harness
treats it as unseen even when it can infer what is probably there. The one
nuance worth knowing is that a *sign* is deliberately smeared by the engine —
you learn an area, never a square — and `out_of_grid.py` exists specifically to
present that as an area and not let the model read it as a coordinate.

### 2.2 Engine truth about last night

`store.list_replay_frames(...)` — the per-hour execution log the engine wrote
when it resolved the night (`SOC_REPLAY_FRAME`). Read in two places:

- `last_night.py` — to reconstruct what *actually* happened (what landed, what
  banked, which probes were crushed, which harvesters were lost) rather than
  what the agent hoped would happen.
- `digest.py` — to narrate incoming attacks and collisions.

This is the anti-confabulation anchor. The agent's own reflection is reconciled
against these frames, so it cannot claim a night went well when the frames say
nothing reached the board.

### 2.3 Constants imported from the engine (never copied)

Per the repo's fan-out rule, tuning values are imported rather than duplicated:

| import | used by |
|---|---|
| `game.tuning.probe_vision_radius` | `packager.py`, `out_of_grid.py` |
| `game.tuning.probe_lifetime_nights` | `option_economics.py`, `supersede.py` |
| `game.weapons` (EMP/chaff dials) | `prompt.py`, `seam_control.py` |

If a dial moves in the engine, these follow automatically. **Adding a hardcoded
copy of an engine constant to this package is the failure mode this table
exists to prevent.**

---

## 3. WRITE — what goes out to the engine

Exactly two calls, and nothing else in the package writes game state:

| phase | call | site |
|---|---|---|
| NIGHT | `snowpark.engine.submit_policy(store, session_id, player, moves)` | `harness.py` step 8 |
| ORBIT | `snowpark.engine.submit_orbit_actions(store, session_id, player, actions)` | `orbit.py` |

### 3.1 The wire move vocabulary

`submit_policy` takes a flat, ordered list of moves, capped at **21**
(`_MAX_MOVES`, inherited from v7). The packager emits four verbs and no others:

| verb | meaning |
|---|---|
| `drop` | land a harvester on a cell (requires live coverage of that cell) |
| `step` | move a landed harvester one cell (auto-harvests on arrival; **not** vision-gated) |
| `pickup` | lift a harvester back to orbit, banking its hold |
| `probe` | place a probe |

Order matters: the engine resolves moves in the order given, on an hour cadence,
so the packager's sequencing is part of the plan's meaning — this is why
`_order_for_probe_support` reorders runs so a probe that grants drop legality
lands before the drop that needs it.

The wider night grammar also holds `wait`, `emp_launch` and `chaff_flare`; a
fork that reaches for weapons emits those. It must **not** emit `mine_lay`.

**Retired verbs are refused, not ignored (v1.31).** `mine_lay` — and its orbit
half `build_mine` — are rejected by `game/policy.py` with a named reason ("the
caltrop mine was retired in v1.31 — EMP and chaff are the remaining weapons"),
and the row still burns one of the 21 slots. A fork that carries a stale tag
therefore loses a slot per occurrence and sees the reason on the card, which is
the whole point of refusing by name rather than dropping silently. Note the v7
sanitizer never filtered `mine_lay`, so nothing upstream of the engine will
catch it for you. RULEBOOK §4.9.4 records the retirement.

### 3.2 The read-only path

`run(..., submit=False)` runs the whole THINK → PLAN → PACKAGE → SANITIZE
pipeline and returns the trace **without calling the engine and without writing
memory or snapshots**. This is what `scripts/advise_v12.py` and the turn suite
use, and it is why a human can play a seat while asking "what would v12 do
here?" without disturbing the game.

---

## 4. Harness-owned state (NOT engine state)

These persist across turns and belong to the harness. The engine neither reads
nor validates them, so a bug here can mislead the agent but can never corrupt a
game.

| store | module | contents |
|---|---|---|
| `SOC_AGENT_MEMORY` | `memory.py` (v7), `journal.py` | per session+seat+day: agent-authored `intent`/`reflection`, plus engine truth folded in (`happened`, `actual_banked`, `probe_crushes`, chosen option IDs) |
| hazard memory | `hazard_memory.py` | the fog-surviving union of stripped/GREEN cells — monotonic, so it is a safe permanent "never drop or step here" set |
| frontier memory | `frontier.py` | which frontier cells have already been mined for exploration probes |
| weapon estimates | `opponent_weapons.py` (v7) | inferred rival EMP/chaff stocks, in-process per session |
| turn snapshots | `harness.py` / v7 recorder | turn-start score and probe counts, the anchor next turn's reflection is measured against |

**Why hazard memory is harness-side and not a view field:** the engine tells you
a cell is green only while you can see it. Green is monotonic — a stripped cell
never un-strips — so remembering it is sound inference from past views, not a
fog violation.

---

## 5. Invariants

1. **No engine edits.** Nothing in this package modifies `sea_of_colours/game/`
   or `snowpark/engine.py`. Behaviour changes happen by choosing different
   moves, never by changing what a move does.
2. **One board input.** Everything the agent knows arrives via `agent_view` or
   is derived from it plus this seat's own history. No reading another seat's
   view, no reading `session` directly.
3. **Two writes, both explicit.** `submit_policy` and `submit_orbit_actions`.
   Both are skipped entirely when `submit=False`.
4. **Constants are imported, not copied** (§2.3).
5. **The packager is legality-only.** It compiles chosen option IDs into wire
   moves and may refuse what the engine would reject — dropping without live
   coverage, walking onto known-green, spending probes or harvesters that do not
   exist, exceeding the hold cap. It must **not** author strategy: it does not
   shorten a route because it looks risky, and when it disagrees with a plan it
   *reports* and keeps rather than silently rewriting. Every intervention is
   surfaced in the packager log and appears on the next turn's card.
6. **Engine truth wins over agent belief.** Where the two disagree — banked
   totals, crushes, losses — `last_night.py` renders the frames, not the plan.

Invariant 5 is the one that has been broken most often and costs the most when
it is: a compiler that quietly edits plans makes the agent's reasoning
unfalsifiable, because you can no longer tell a bad decision from a good
decision that was overwritten.

---

## 6. Where to look when something is wrong

| symptom | first place to look |
|---|---|
| agent acted on something it could not see | `world_view.py` / `out_of_grid.py` — is a sign being rendered as a coordinate? |
| plan did not reach the board | packager log on the card; then `move_sanitizer` (v7) |
| memory says a night went well when it did not | `last_night.py` against `list_replay_frames` |
| repeated self-harm on green | `hazard_memory.py` union, and whether the caller used `_known_green_cells` |
| a rule/constant looks stale in the prompt | §2.3 — something hardcoded a copy |
