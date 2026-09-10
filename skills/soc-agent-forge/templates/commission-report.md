# Commission report — `<AGENT_LABEL>`

Date · commit · who ran it.

Every claim below needs its evidence inline: a board name, a turn number, a
count, or a quoted line. Delete any row you did not actually test rather than
guessing at it — an unverified row that reads as verified is worse than a gap.

## Verdict in one line

> e.g. "Both plays fire and compile; EMPRESSURE is refused 4/7 nights on
> certainty grounds; procurement untested."

## The declared plays

| Play | Weapon | WHEN | Offered? | Fired? | Evidence |
| --- | --- | --- | --- | --- | --- |
| | | | | | board / turn / count |

## Wiring — the four silent failures

Each is invisible until the one before it works, and none of them error.

| Check | Result | Evidence |
| --- | --- | --- |
| option appears in the MENU block | | card + line |
| option appears in the chosen plan | | `[plan=...]` line |
| wire verb appears in the orders | | quoted order |
| the hour renders in the NEXT night's execution log | | H-number present, no gap |

## Offered vs fired

The number that separates a plumbing problem from a persuasion problem.

```
option id offered : N
wire verb emitted : M
```

- `M == 0` and `N > 0` → never compiled. Schema enum or `_DISPATCH`.
- `M` well below `N` → losing the argument. Read the model's own reasoning on the
  card and rewrite the sentence that lost, rather than adding a paragraph.
- `N == 0` → never offered. The `when`/`targets` did not match any board tested.

## Seasons

| Name | Opponent | Seed | Score (us–them) | `fallback_turns` |
| --- | --- | --- | --- | --- |

**If `fallback_turns` is non-zero, that season is not a measurement of this
agent** — the model was not reached and the built-in heuristic played those
turns. Say so rather than quoting the score.

## Economy — did it fund itself?

| Question | Answer | Evidence |
| --- | --- | --- |
| Did it ever hold a charge? | | rack line in a card |
| On which day did it first arm? | | |
| Did blue ever gate the plan? | | orbit descriptor, e.g. `blue 50/200` |
| Did it buy ordnance it has no play for? | | orbit actions |

## What is NOT working

Be specific and name the fix site. One line each.

## What is NOT tested

The honest half, and the one teams skip. Known blind spots:

- **Procurement.** A lab board stamps a rack rather than playing an orbit phase,
  so no lab take proves the agent would ever buy the weapon.
- **Boards not tried.** List them.
- **Non-determinism.** One lab take is an anecdote. If a decision matters, take
  it three times — and if the three disagree, that instability is the finding.
- **Multi-seat.** A 4-player board behaves differently from a duel, especially
  for a weapon that hits every other seat.

## Next two changes

Ranked, with the evidence each rests on. Two, not nine.

1.
2.
