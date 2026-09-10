# Phase 4 — Iterate

The loop, once the build lands:

```
  scout finds a move ──► build implements ──► QA runs seasons + lab
        ▲                                              │
        └──────────────  ranked feedback  ◄────────────┘
                    (leader keeps the list)
                              │
                          push between
                          every round
```

## Take the cards as the input

Every season writes a card per turn and one combined file:

```
reports/seasons/<NAME>_<id>/all-cards.md     (paste this in)
reports/seasons/<NAME>_<id>/cards/           (one file per turn)
```

A heuristic season's combined file is around 550 lines — small enough to hand
over whole. When a team pastes one in, or points at a path, read it and ask
*why*, not *what*. The useful question is the specific one: "on night 3 we
declined `EMP_BONANZA` with three probes in radius — read the rationale and tell
me which argument lost."

Geometry faults fall out of this fast. An agent firing at a cell one off the
cluster, or insuring a landing it was never going to make, is obvious in a
transcript and nearly invisible in a score.

## Classify before fixing

"It didn't fire" has five distinct causes with five different fix sites. Work out
which one before editing anything — full detail in `references/diagnosis.md`.

| What the cards show | Cause | Fix site |
| --- | --- | --- |
| Option absent from the menu | never offered | `agency.py` builder, trigger predicate |
| On the menu, chosen, no move issued | schema or packager gap | `chat_schema.py` enum, `packager._DISPATCH` |
| Move issued, that hour blank in the log | render gap | `last_night.py` tag sets |
| On the menu with rationale, refused | lost the argument | the `rationale`, `_KIND_BLURB` framing |
| Never offered, rack empty | never afforded | `orbit_policy.py`, the five blue gates |

The fourth is the interesting one and the most common after the plumbing works.
A recorded bake had the option in the prompt five times with full rationale and
the model chose a harvest chain 3/3. Menu *position* was not the problem — the
weapon block is first. The suspects are the blurb's own framing (a blurb that
says "never over a CERTAIN pure/mass grab" has taught the model to always defer)
and prompt length.

So when an option is losing the argument, **rewrite the sentence that lost it**
rather than adding a new paragraph. That loop is much faster than tuning numbers,
and it is the one the cards actually support.

## Run three seasons at once, and vary the opponent

Seasons are separate processes writing to separate stores, so they genuinely run
in parallel. Three heuristic seasons measured at 19 seconds wall clock, 241% CPU.
Seasons with an LLM seat are dominated by inference latency rather than CPU —
which is precisely why parallel matters *more* there, not less. Three LLM seasons
cost roughly what one costs.

```bash
python scripts/soc.py season --p1 <label> --p2 red_harvest  --seed 11 --name A --quiet &
python scripts/soc.py season --p1 <label> --p2 tabula_v12   --seed 22 --name B --quiet &
python scripts/soc.py season --p1 <label> --p2 <label>_v1   --seed 33 --name C --quiet &
wait
```

Vary the **opponent**, not the seed. One against the armed heuristic tells you
whether you are competent. One against V12 tells you whether you beat the
baseline the whole field is built from. One against your previous version tells
you whether today was worth it.

When re-testing a change, hold the seed. Changing the agent and the seed at once
tells you nothing.

## Fill the wait — do not let anyone watch a progress bar

An LLM season takes minutes. Launch the fan-out, then immediately give the team
something to think about, and only surface results when they land.

The best filler is scouting, and the reason is structural: **every agent in the
room is descended from V12**, including everyone else's. So a weakness found in
V12 while playing it is, on the day, a weakness in most of the field. Scouting
needs no code, no credentials and nobody's permission.

Good questions to hand over while seasons run:

- Play V12 and find one thing it does badly. Can your move punish it?
- Your weapon fires on the trigger you chose. Name the board where that trigger
  is *wrong* — where firing costs you the night.
- Your rival can see your total weaponised blue but not its composition. 600
  could be two chaff or six SNAPs. What do you want them to believe?
- What is your last-night play? Denial is worth more on night 7 than night 1.

## Collate before round two

By now four people have opinions and three contradict. Turn it into one ranked
list before anyone edits.

1. **Write the complaint as an observation, not a fix.** "It bought chaff on day
   2 with no rival in vision" beats "make it buy chaff later".
2. **Attach evidence** — a lab board and seat, or a turn number in a card. An
   unevidenced complaint costs an hour and finds nothing.
3. **Rank ruthlessly.** You will implement two or three, not nine.
4. **Cut game-rules disagreements.** Those are for the organisers, not the
   afternoon.

## Stop earlier than feels comfortable

The last hour is verification, not features.

```bash
python scripts/soc.py doctor
python scripts/soc.py list
python scripts/soc.py season --p1 <label> --p2 tabula_v12 --days 3
python scripts/soc.py push -m "final"
```

The failure this prevents: a team whose final refactor broke discovery, pushed at
the buzzer, and enters the league as a directory the loader skips.

## Gate

A ranked evidenced list, a change implemented, and a re-run on the same seed that
tests it. Then round the loop again, or go to final polish.
