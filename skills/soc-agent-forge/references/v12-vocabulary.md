# The V12 play vocabulary — what your move will be named beside

A weapon move never appears alone. It is printed in a menu next to V12's own
plays, the model picks between them by id, and your rationale argues against one
of them by name. So the existing vocabulary is not trivia — it is the context
your name is read in.

**Grep this list before naming anything.**

## The eighteen named seam patterns

These are `kind="seam"`, built by `seam_control.py`, and which ones appear
depends entirely on the night.

**On a pure YOU found (CASE 1):**

| id | what it does |
| --- | --- |
| `SMASH_GRAB` | land on the pure, comb the halo |
| `SMASH_GRAB_VALUE` | take the richest cell of the seam first |
| `SEEN_GRAB` | take a pure already in live vision — no guessing |
| `SECURE_MASS` | bank the mass halo behind a covering probe |
| `FULL_SWEEP` | comb the whole seam with one unit |
| `LATE_SWEEP` | the same, started late |
| `SWEEP_RING` | work the ring rather than the centre |
| `CHAFF_INSURANCE` | hedge the pickup against a rival's chaff |

**On a pure a RIVAL found (CASE 2):**

| id | what it does |
| --- | --- |
| `BLIND_GRAB` | one covering probe on the finder, then a short blind drop, pickup ~H4-5 |
| `BLIND_AND_GRAB` | the long form — blind the finder and comb the whole seam |
| `UNBEATEN_FLANK` | a SECOND harvester and its own probe work a different sector |
| `CONTEST_DENY` | confirm and deny without landing; keeps the pure for tomorrow |
| `WALK_IN` · `WALKIN_GRAB` · `WALKIN_LATE` · `WALKIN_SECURE` | reach the seam on foot, no probe spent |
| `WALK_TO_CONTEST` · `WALK_TO_CONTEST_FAR` | walk in purely to contest |

## The eight numbered families

Generated with an index, so the real id is `GRAB1`, `CH2`, `PR3`. A team cannot
put a custom name into these without editing the f-string prefix in `agency.py`.

| prefix | kind | what it is |
| --- | --- | --- |
| `GRAB{n}` | grab | visible pure/mass RED — the most certain points on the menu |
| `BL{n}` | blue_grab | rich blue you can see |
| `HD{n}` | hotdrop | probe + drop into fresh fog |
| `PR{n}` | probe | bare probe placement |
| `PRSNAP{n}` | snap_cover | a second probe insuring a landing against a SNAP |
| `CH{n}` | chain | walk known red, no probe |
| `SS{n}` | supersede | spend a probe to blind a rival's probe |
| `FR1` | frontier | last resort, blind-sample the best echo |

## Why this matters to a weapon move

**Your rationale must name a competitor that is really there.** The COMPARE
clause is resolved at build time against the live registry — `_RIVAL_IDS` maps
each `combines_with` to the ids it might realistically compete with, and
`_resolve_rival()` picks whichever is present. When nothing matches it describes
the *class* instead. It never invents an id, because the model picks moves by id
and a recommendation pointing at a move that is not on the menu is either ignored
or hallucinated.

That is the bug the team leader's guide describes: *"if your doctrine still says
'prefer PRSNAP' after you renamed the option to BODYGUARD, you have told the
model to pick a move that is not on the menu… nothing crashes; the agent just
quietly stops taking a play you thought you had recommended."*

**If a team renames V12's ids, the weapon rationale must follow.** Renaming is
encouraged — it gives an agent identity in a shared replay and makes a season
greppable. But `_RIVAL_IDS` and `_RIVAL_PROSE` in `weapon_forge.py` key on the
stock names. Rename `BLIND_GRAB` to `POLITE_ROBBERY` and the weapon rationale
silently falls back to the generic clause.

Two ways to handle it, in order of preference:

1. **Rename the weapon plays too, and update `_RIVAL_IDS`** — keeps the argument
   specific.
2. **Set `rationale=` explicitly on the `WeaponPlay`** — bypasses composition
   entirely, so you own the whole argument including the id it names.

## Naming your own move beside these

Three rules that fall out of the list above.

**Do not collide, and do not near-collide.** `SMASH_GRAB_PLUS` next to
`SMASH_GRAB` reads as a variant of it, and the model will treat it as one.
`grep -rn "<NAME>"` your fork before committing to it.

**Do not borrow a verb that already means something.** `SWEEP`, `WALK_IN`,
`CONTEST`, `SECURE` and `BLIND` all carry established meanings in this
vocabulary. A weapon move called `BLIND_STRIKE` will be read as a
`BLIND_*` seam play — which is a landing, not a strike.

**Silly tone, exact content.** The whole point of naming is identity in the
league, so be as funny as you like. But `SMASHBURGER` for a smash on your own
pure seam is perfect and `SMASHBURGER` for a defensive probe is a bug you will
spend an hour not finding — because the name is part of the prompt, and the model
reads it as a description.
