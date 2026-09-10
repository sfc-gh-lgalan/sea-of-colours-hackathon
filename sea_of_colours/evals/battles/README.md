# The redsign battles

> **DEPRECATED (v1.42) — superseded by the turn lab (`turnlab/`).**
> Nothing here has been removed and every command below still runs.
> But the boards on this page are *constructed*: a `WorldBuilder`
> arranges a seam, a rival and a rack into the situation we wanted to
> ask about. The lab freezes turns that were actually played, so the
> position an agent is handed is one a season really produced, with
> the memories, the journal and last night's replay that came with it.
> That is a better question to ask a fork, and it is a great deal less
> to keep true as the rules move.
>
> Start at `python run_web.py` → `/lab`, or `python scripts/soc.py lab`
> to see what is in it. Two things here have no lab equivalent yet —
> `soc weapons` (the static "is your fork wired to fire" scan) and
> `soc league` (ranking every submitted fork) — so this suite stays
> until they do. See `turnlab/README.md`.

Nine nights where a PURE was on the table and the decision was hard,
plus one ordinary working night with no jackpot at all, each run
against five rungs of escalating trouble.

```bash
python scripts/soc.py suite --agent my_agent      # score it
python scripts/soc.py why two_pures_poker         # what was the right play?
python scripts/soc.py why two_pures_poker my_agent  # ...and what did mine do?
```

Everything runs offline on `SOC_BACKEND=memory`. The whole suite is 50
turns in about a second against the heuristic; an LLM agent takes as
long as it takes.

**One board is not a redsign night.** `plain_night_armed` has no pure
and no sign — an ordinary seam, worked with ordnance in the rack. It is
the control group: an agent that only performs when there is a jackpot
on the table is not actually good. Weapon use there is reported and
never scored, because when to spend a charge is doctrine and doctrine
belongs to whoever wrote the agent.

## What is here

| file | what it holds |
|---|---|
| `boards.py` | the ten boards, each with its question, its canonical play in prose, and its predicates |
| `baseline/` | stock V12's own frozen run, for side-by-side comparison |
| `ladder.py` | the five difficulty rungs and the four weapon loadouts |
| `stage.py` | builds a playable night from a board + rung + loadout |
| `predicates.py` | the checks — shape predicates over engine truth |
| `runner.py` | plays a battle N times and scores it |
| `report.py` | renders results, ordered by what to fix |
| `recorder.py` | freezes a run to disk as a **bake** so it can be replayed |
| `room/room.html` | the **battle room** — replays a turn beside its card |
| `readiness.py` | `soc weapons` — why an agent is not firing |

## The three ideas worth knowing

**The boards are constructed, not restored.** The originals were frozen
Snowflake rows — exact, and useless for this: you cannot run them
without an account, and a frozen blob has exactly one difficulty. These
are rebuilt from their documented geometry, so the dials can move.

**Scoring is by shape, never by a stored move list.** A better line than
the canonical passes; a lucky cell-for-cell match with the ordering
wrong does not. This is why boards ship prose rather than answers.

**The ladder is what stops this being ten memorised boards.** Rungs
change the opposition, not the geometry. Where your agent stops tells
you what to teach it:

| rung | it teaches |
|---|---|
| `quiet` | that a free extension must be taken — here, stopping short is the failure |
| `watched` | tempo: they can land on the pure before you |
| `armed` | that a plan can be cancelled, so value must come first |
| `crowded` | that you do not hold hour one and must go anyway |
| `siege` | whether your agent can actually fight |

The `--loadout` axis is separate and answers a different question. Hold
the rung fixed, hand the agent a full rack, and see whether anything
changes. Stock V12 buys weapons and never fires them, so there is a
known answer to calibrate against.

## Reading a failure

`soc suite` leads with where on the ladder you stopped and which
predicate fails most often — fourteen scattered failures usually have
one cause. Then `soc why <board>` prints the question, the canonical
play, and what stock V12 did on the same board.

Two flags to take seriously:

- **`FLAKY`** — passed some runs, failed others. Usually more
  informative than a clean fail, and invisible at `--runs 1`. Use
  `--runs 3` before believing any result from an LLM agent.
- **the fallback warning** — the harness never reached its model and
  played its safety net. That share of the score is not your agent's.

## Watching the turn back: the battle room

A percentage tells you *that* a board was misplayed. The room tells you
why. Add `--record` and every turn is frozen to disk with the position,
the orders, and the agent's card:

```bash
python scripts/soc.py suite --agent my_agent --runs 3 --record
open reports/battles/index.html        # or http://127.0.0.1:8000/battles/
```

The room is one screen in three parts. On the **left**, every turn in
the run, grouped by board, with a pass/fail dot — so a rung where things
fall apart is visible before you click anything. In the **middle**, the
board as the agent found it, with a transport under it: step an hour at
a time, or press play and watch the night. The walked path draws behind
the harvester, and `FOG` swaps ground truth for what the seat could
actually see when it planned. On the **right**, the card:

| panel | the question it answers |
|---|---|
| Situation | what the board was asking |
| The canonical play | what a good house does here, in prose |
| Scored *n*% | every predicate, pass or fail, with its reason |
| Orders issued | what reached the engine — click a line to jump the board to it |
| Corrections | where the compiler rewrote or dropped an order |
| Options | the menu it chose from, and what it picked |
| Reasoning | what the model said before it committed |
| The prompt it was given | the whole thing, verbatim |
| What stock V12 did | the baseline, for comparison |

The order matters. The panels run from *what was asked* down to *what it
was told*, because that is the order to debug in: a bad play with sound
reasoning is a compiler problem, sound reasoning from a bad menu is an
options problem, and only when the menu and the prompt are both right is
the model itself the thing to blame. This is the same "check the menu
before blaming the model" rule the hackathon guide states — the room is
what makes it checkable in ten seconds.

Two things the room shows that nothing else does. **Fog**: half the
failures here are an agent playing sensibly on information it did not
have, and you cannot tell that from a misread without the mask.
**Corrections**: an agent blamed for a stupid move quite often issued a
sensible one that the packager rewrote on the way to the engine.

### The loop

```
soc suite --record        bake a run
open the room             find the board, watch the turn, read the card
edit dials / doctrine     in your fork
soc suite --record        bake again
switch bakes in the room  same board, before and after
```

Bakes accumulate — the `BAKE` dropdown lists every run on disk, newest
first — so the last step is the useful one: open the same board in the
old bake and the new one and see whether the change did what you meant.
Delete a bake's `.json` and re-run any recorded suite to drop it from
the list.

### The bake format

```
reports/battles/
  index.html          the room, copied here so the directory stands alone
  index.js            window.SOC_BATTLE_INDEX — every bake, newest first
  data/
    <bake-id>.json    the readable truth
    <bake-id>.js      the same, minified, assigning to a global
```

Three constraints shaped this, and all three are load-bearing:

**It is JavaScript, not JSON.** A browser will not `fetch` a sibling
file over `file://`, so a page you open by double-clicking cannot read
JSON off disk. A `<script>` tag can. That is what lets the whole
directory be zipped, mailed and opened on a machine with no repo — and
it is why there is a test asserting the room contains no `fetch(`.

**Terrain and board prose are pooled.** The same board is staged once
per rung, per loadout, per run, and the ground does not change between
them. Inline, a full suite is ~500 copies of the same grid and about
20MB; pooled it is a fraction of that, and turns carry keys into
`grids` and `boards`. Cells are `[x, y, code, purity]` with a one-letter
code, for the same reason.

**It never touches a real game.** Bakes are their own root under
`reports/`, hold no session the normal UI can reach, and are regenerable
— so a review can never surface in someone's season list, and deleting
the directory loses nothing.

### If the room is empty

`No bakes yet` means no recorded run. `--record` is opt-in; a plain
`soc suite` writes nothing. If the board draws blank after a change to
the recorder, run `python backstage/probes/_probe_battle_room.py` — it opens the
room in a real browser over `file://`, clicks a failing turn, works the
transport and the fog toggle, and fails loudly on a console error. A
static page cannot report its own breakage, which is the entire reason
that probe exists.

## Adding a board

Copy the closest entry in `boards.py`, change the geometry, and **write
the canonical in prose first**. A canonical you cannot state in a
sentence is one you do not understand yet, and a predicate written
before the prose tends to encode the implementation you happen to have
rather than the play you want.

Then add the canonical as moves to `CANONICAL` in `tests/test_battles.py`.
That test asserts every board is winnable by its own prescribed play — a
suite nobody can pass is worse than no suite. It is not a formality: it
has already caught a predicate that forbade a probe two boards require,
a check that let a unit hide behind its partner, and a ladder rung that
was easier than the one below it.
