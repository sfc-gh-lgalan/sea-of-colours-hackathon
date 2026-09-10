# Phase 3 — Your agent exists. Now what?

This is a **handoff moment**, not a task list. Stop, tell the team what they have,
and offer them the two things worth doing next. Do not pick for them.

## Say what was built

Report, in this order — short lines, no preamble:

- the **label**, and that `soc list` shows it
- each **declared play**: name, weapon, when it fires, what hour
- the **economy**: what `weapon_forge.economy_summary()` prints
- the **verification state**: `check_wiring.py` PASS count, and that it proves the
  plumbing is real but says nothing about whether the play is any good
- **what has not been tested** — be explicit. Synthetic-board checks are not a
  season, and nothing so far proves the agent would ever *buy* the weapon

Then offer the choice.

## The choice

> **A — share it.** Hand it to teammates so they can work in parallel, and
> publish it so there is a scoring entry and a fallback point.
>
> **B — run one improvement loop.** A headless season plus armed lab checks, to
> find out whether the weapon actually fires in play rather than in a fixture.

Most teams should do **A first and then B**, because a published fork is a fork
you can go back to and it takes five seconds. But say that as a recommendation,
not a rule — a team that is behind on the clock may reasonably want evidence
before they publish anything.

## A — sharing

Full detail in `references/publishing.md`. The three-way distinction teams
conflate:

```bash
# hand to a teammate, work in parallel right now
python scripts/soc.py share --agent <label>            # writes a .socfork
python scripts/soc.py grab <label>.socfork             # on their machine

# publish to your fork — the league, and a fallback point
python scripts/soc.py push --dry-run                   # always preview
python scripts/soc.py push -m "first armed version"
```

**Pushing is not entering.** The organiser collects the forks at the end; whatever
is on the fork at collection time is what plays.

Expect `push` to refuse if anything changed outside the agent directory — that is
divergence protection, not tidiness. Only that directory travels into the league,
so an out-of-folder change silently goes missing and the agent behaves differently
there than it does locally.

## B — one improvement loop

Two halves. **Launch the season first** because it is the long pole, then do the
lab checks while it runs.

### The season — background, opponent varied, never `--backend memory`

```bash
python scripts/soc.py season --p1 <label> --p2 tabula_v12 \
  --seed 4242 --days 7 --name COMMISSION &
```

It warns you honestly: *"at least one seat calls a model every night — this will
take real minutes."* So do not wait on it.

### The lab checks — armed, and a human can do these

**This is the half that needs a person, or the CLI equivalent below.** The point
is to equip the weapon the play declares and see whether the agent reaches for
it.

Pick the board that matches each play's `when`:

| `when` | board | why that one |
| --- | --- | --- |
| `no_redsign` | `plain_night_armed` | day 4, no redsign, no pure, rack loaded — ordnance and nothing to fear |
| `redsign_theirs` | `blind_grab_rival_seam` · `crowded_echo_seam` | a rival's pure is lit |
| `redsign_mine` | `early_solo_seam` · `two_pures_poker` | your own pure is lit |

**By hand, in the browser:**

```bash
python run_web.py          # then open /lab
```

1. Pick the board.
2. **Arm the seat from the ARMS control** with the weapon the play declares. This
   step is the one people miss — a lab board stamps a rack rather than playing an
   orbit phase, so an unarmed seat never offers the option, and that looks exactly
   like a broken build.
3. Cast the fork from the AGENT tab. ~20 seconds.
4. Hit **`[ vs V12 ]`** for the side-by-side. V12's answers are pre-frozen and
   recorded **unarmed by design** — its reply schema has no weapon verbs at all.
   So the gap is the exercise: does your fork fire where V12 cannot?
5. Download the take's card and check four things in order —

| Look for | If missing |
| --- | --- |
| the option id in the **MENU** block | never offered — `when`/`targets` did not match |
| the option id in the **plan** line | offered and refused — see `doctrine-conflicts.md` |
| the **wire verb** in the orders | never compiled — schema enum or `_DISPATCH` |
| `[fallback=True]` on the plan line | the model was never reached; the take is not evidence |

**Or headless, no browser:** `soc suite` runs the same constructed boards with
weapon loadouts and writes cards straight to disk.

```bash
python scripts/soc.py suite --agent <label> \
  --board plain_night_armed --loadout emp --cards /tmp/venom_lab
```

`--loadout` takes `empty` · `chaff` · `emp` · `both`, `--runs N` exposes
flakiness (an LLM is not deterministic — one take is an anecdote), and `--cards`
gives you the same artefact to grep:

```bash
grep -nE "MY_MOVE_ID|emp_launch|chaff_flare|snap_launch" /tmp/venom_lab/*.md
```

### Then read the season

```bash
python3 -c "import json;d=json.load(open('reports/seasons/<dir>/season.json'));\
print('fallbacks:',d.get('fallback_turns'),'scores:',d.get('scores'))"
```

**Check `fallback_turns` before the score.** Non-zero means part of that season
was the built-in heuristic, not your agent.

Then the offered-vs-fired count, which is the number that separates a plumbing
problem from a persuasion one:

```bash
grep -c "MY_MOVE_ID"  reports/seasons/<dir>/all-cards.md
grep -c "emp_launch"  reports/seasons/<dir>/all-cards.md
```

## Gate

Either a `.socfork` handed over and a push done, or a completed loop with a
report. Both, ideally. Then `phases/4-commission.md` for the full write-up, or
`phases/5-iterate.md` to act on what you found.
