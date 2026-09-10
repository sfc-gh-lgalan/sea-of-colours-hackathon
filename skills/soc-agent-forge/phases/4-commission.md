# Phase 4 — Commission

**Yes, this is a distinct phase, and it belongs after sharing.** Phases 0–2 build
the agent and Phase 3 hands it over. This one answers a different question:
*does the thing we just published actually work?* — and it ends in a written
report rather than a fix.

Keep it separate from Phase 5 (iterate) for a reason. Commissioning asks "is the
build sound"; iterating asks "is the play good". Teams that merge them start
tuning doctrine while the wiring is still broken, and then cannot tell which
change moved the score.

Run it in this order. Each step costs more than the last, and each can fail in a
way that makes the next one meaningless.

## 1 · Lab, armed — does the option appear and fire?

The acid test, and it comes first because it is 20 seconds rather than minutes.

```bash
python scripts/soc.py lab            # list the boards, no server needed
python run_web.py                    # then open /lab
```

Pick the board that matches the play's `when`:

| `when` | board |
| --- | --- |
| `no_redsign` | **`plain_night_armed`** — day 4, no redsign, no pure, rack loaded |
| `redsign_theirs` | `blind_grab_rival_seam` · `crowded_echo_seam` |
| `redsign_mine` | `early_solo_seam` · `two_pures_poker` |
| `always` | any of them |

**Arm the seat from the ARMS control with the weapon the play declares.** A lab
board stamps a rack rather than playing an orbit phase, so an unarmed seat will
never offer the option no matter how well it is wired — and that reads exactly
like a broken build.

Then cast the fork from the AGENT tab. A take comes back in ~20s.

## 2 · Pull the plan and read it — the wiring check that matters

Download the take's card from the lab (the same artefact the season writes per
turn). Then look for four things, in order. Each one failing means something
different:

| Look for | If missing |
| --- | --- |
| the option id in the **MENU** block | never offered — `when`/`targets` did not match this board |
| the option id in the **plan** line | offered and refused — go to `references/doctrine-conflicts.md` |
| the **wire verb** in the orders | never compiled — schema enum or `_DISPATCH` |
| the hour blank in **next** night's execution log | never rendered — `last_night` tag sets |

Grep the card rather than skimming it:

```bash
grep -nE "MY_MOVE_ID|emp_launch|chaff_flare|snap_launch" <card>.md
```

**Check `[fallback=` on the plan line.** `fallback=True` means the model was not
reached and the built-in heuristic played that turn — nothing about the take is
evidence of your agent.

## 3 · Season in the background — does it hold up over an arc?

Launch it and do not wait on it. Vary the opponent, never the seed, and never
pass `--backend memory`.

```bash
python scripts/soc.py season --p1 <label> --p2 red_harvest  --seed 11 --name COMMISSION_A --quiet &
python scripts/soc.py season --p1 <label> --p2 tabula_v12   --seed 22 --name COMMISSION_B --quiet &
python scripts/soc.py season --p1 <label> --p2 <label>_v1   --seed 33 --name COMMISSION_C --quiet &
wait
```

Separate processes, separate stores, so they genuinely run at once — and LLM
seasons are latency-bound, so three cost about what one costs.

**Fill the wait.** An LLM season is minutes. Hand the team a scouting question
while it runs — every agent in the room descends from V12, so a weakness found
playing V12 is a weakness in most of the field. See `phases/5-iterate.md`.

## 4 · Read `season.json` before the cards

Two numbers decide whether the cards are worth reading at all:

```bash
python3 -c "import json;d=json.load(open('reports/seasons/<dir>/season.json'));\
print('fallbacks:',d.get('fallback_turns'),'scores:',d.get('scores'))"
```

**`fallback_turns` non-zero means part of that season was not your agent.** Fix
credentials before interpreting any score.

Then count how often the weapon actually fired:

```bash
grep -c "MY_MOVE_ID" reports/seasons/<dir>/all-cards.md
grep -c "emp_launch\|chaff_flare\|snap_launch" reports/seasons/<dir>/all-cards.md
```

Offered-vs-fired is the number that matters. An option offered fourteen times and
fired twice is a persuasion problem, not a plumbing one.

## 5 · The report

Write it down. The deliverable is a short document a team can act on, split
honestly into what works and what does not. Use
`templates/commission-report.md`.

Every claim needs its evidence inline — a board name, a turn number, a count, a
quoted line from a card. A report that says "the EMP works" teaches nothing; one
that says "fired 3/7 nights, refused 4 with the rationale losing to GRAB1 on
certainty" tells the team exactly what to change next.

**State what you did not test.** The lab stamps a rack rather than playing an
orbit phase, so nothing here proves the agent would ever *buy* the weapon —
`soc season` is the only surface that does, and only if the season ran long
enough to bank the blue.

## Gate

A written report naming: the boards tested, whether each declared play fired,
offered-vs-fired counts, `fallback_turns`, the season scores, and an explicit
list of what remains unverified. Then `phases/5-iterate.md`.
