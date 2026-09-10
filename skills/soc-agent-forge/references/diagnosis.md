# Diagnosis — "my agent won't fire"

Six distinct causes, six different fix sites. Classify before editing. Nearly
every hour lost to weapons work is an hour spent fixing the wrong one.

The reason this is hard: **each rung is invisible until the one before it works,
and none of them error.** An option that emits a verb the schema forbids does not
crash — it silently never appears.

## The decision table

| Evidence in the cards / lab take | Cause | Fix site |
| --- | --- | --- |
| Option id never appears in the menu block | **never offered** | `agency.py` builder, or the trigger predicate never true |
| On the menu, named in the plan, no move in the orders | **never compiled** | `chat_schema.py` enum, `packager._DISPATCH` |
| Move in the orders, that hour blank in the execution log | **never rendered** | `last_night.py` tag sets + caption map |
| On the menu with full rationale, plan chose something else | **lost the argument** | the `rationale` text, `_KIND_BLURB` framing, prompt length |
| Option never offered and the rack shows zero | **never afforded** | `orbit_policy.py`, the five blue gates |
| Refused with reasoning that is *factually wrong about the weapon* | **contradicted by threat-side doctrine** | emit a `CORRECTION` after the `BEWARE_*` block — see `doctrine-conflicts.md` |

The sixth is the one that is easiest to misdiagnose as the fourth, and the tell
is specific: **read what the agent said.** "Lost the argument" sounds like a
trade-off — *"the grab banks more tonight"*. Contradiction sounds like a false
belief about mechanics — *"their H1 drop still lands"*, when cancelling their H1
action is precisely what the weapon does.

If the refusal contains a claim about the weapon that is untrue, do not touch
the rationale. Go and read what `BEWARE_<weapon>` says, because the model is
probably quoting it correctly.

## How to tell them apart, concretely

Read the planning card top to bottom. It carries, in order: the assembled prompt
(including the menu), the plan the model chose, the orders issued, and the
previous night's execution log.

**never offered** — grep the card for the option id. Absent from the menu block
means the builder did not produce it. Either the trigger predicate was false on
this board (fine, try `plain_night_armed`) or the builder is not wired into
`build_registry`.

**never compiled** — the id appears in `[plan=...: MYMOVE, PR2]` but no
corresponding verb appears in the orders. The packager had no `_DISPATCH` entry
for the kind, or the schema rejected the verb on a fallback night. Check whether
the card says `[fallback=True]`: if fires work on normal nights and fail on
fallback ones, it is the schema enum.

**never rendered** — this is the nastiest because the agent then lies to itself.
The orders show the move, and the next night's execution log has a *gap* where
that hour should be:

```
YOU ORDERED:  ... snap_launch @(11,22) ...

EXECUTION LOG:  H04 pickup            ok
                H06 probe (11,14)     ok      ← H05 simply missing
```

The agent's own reflection then reads "I ordered a SNAP that never executed",
which corrupts the journal and therefore tomorrow's reasoning. Stock
`tabula_v12/last_night.py` ships with exactly this gap for SNAP — the tag sets
omit `snap` and `snapped`, and the caption map has no entry. Compounding cause:
the wire verb is `snap_launch` but the replay frame tag is `snap`, so any tag set
keyed on the verb drops the frames.

**lost the argument** — the most common state once plumbing works, and the most
interesting. The option is on the menu, first in the group, with detail and
rationale, and the model picks a harvest chain anyway. A recorded bake had the
option in the prompt five times and it was refused 3/3.

Rule out the mechanical explanations first, because they are cheap:
- It is *not* menu position. `_KIND_HEADERS` order is the menu order and weapons
  go first. `value_pyramid.py` does not rank weapons at all.
- It is *not* affordability, if the rack shows stock.

Then look at two things. First, the blurb's own framing: a `_KIND_BLURB` that
says "never over a CERTAIN pure/mass grab" has explicitly taught the model to
defer to grabs, and it will. Second, prompt length — the model mirrors the style
of what it reads, and a long analytical prompt produces diffuse choices.

**The fix is to rewrite the sentence that lost, not to add a paragraph.** Read
the model's own reasoning on the card; it usually names the trade it made. That
loop is far faster than tuning numbers.

**never afforded** — the rack is empty all season. This is `orbit_policy.py` and
the blue gates, and it is outside the four-rung ladder entirely, which is why it
gets missed. A recorded case: orbit blocked at `blue 50/200` six times while the
seat sat on 1000+ credits. See `references/blue-economy.md` — but only after the
weapon fires, because funding a rack you cannot use is strictly worse than not
funding it.

## Do not trust `soc weapons` as a verdict

It is a source scan and all four rungs can PASS with no working weapon:

- rung 1 = the literal `weapon_stock` appearing in any live file outside the
  buying code
- rung 2 = files *named* `*chat_schema*` / `*agency*` / `packager.py` each
  containing a verb — **filename-based**, so renaming a module breaks the scan
- rung 3 = `rationale=` and `detail=` merely co-occurring
- rung 4 = a regex over doctrine prose

It is a genuinely useful smoke test — it runs in a second, needs no credentials
or model, and it names the next action. It answers "have you built this rung",
never "is it any good". Say that to the team when the four PASSes appear.

## The falsest of false trails

A season that "ran fine" may have been the built-in heuristic the whole way. A
missing or bad PAT does not error — the harness silently falls back per turn.
A headless season does say so:

```
WARNING: 14 turn(s) fell back to the built-in heuristic — the model was
not reached, so that part of this season is not your agent's.
```

If that appears, stop. Nothing in the result is a measurement of the agent until
credentials are fixed. Check `season.json` → `fallback_turns` before interpreting
any score.
