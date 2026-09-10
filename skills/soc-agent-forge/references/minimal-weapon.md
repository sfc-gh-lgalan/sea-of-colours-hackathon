# The minimal firing weapon

## Do not use the reference fork as the template

`emp_harvest_test` is the in-repo weapons fork and it is the *maximal* example.
Measured against the baseline it copies:

| File | V12 | fork | delta |
| --- | --- | --- | --- |
| `scorch.py` | — | 386 | **new module** |
| `seam_control.py` | 2900 | 3475 | +575 |
| `agency.py` | 1557 | 2130 | +573 |
| `packager.py` | 1009 | 1541 | +532 |
| `doctrine.py` | 843 | 1064 | +221 |
| `orbit_policy.py` | 382 | 505 | +123 |
| `prompt.py` | 1308 | 1367 | +59 |
| `chat_schema.py` | 97 | 131 | +34 |
| `option_economics.py` | 1393 | 1415 | +22 |
| `last_night.py` | 888 | 902 | +14 |
| `harness.py` | 1138 | 1149 | +11 |

**~2,550 lines across 11 files** — because it carries two weapons, five named
plays, compound seam patterns, telemetry bands, EMP cloud-ordering and a deferred
walk-flush. Reading that as "what it takes to arm an agent" is what makes teams
budget a day for weapons.

## The minimum is six files and about 150 lines

| # | File | Edit | ~lines | Rung |
| --- | --- | --- | --- | --- |
| 1 | `prompt.py` | `format_rack_block()` + call site | 25 | 1 |
| 2 | `chat_schema.py` | widen enum + `at` | 8 | 2a |
| 3 | `agency.py` | option builder + `_KIND_HEADERS` + `_KIND_BLURB` + registry loop | 45 | 2b, 3 |
| 4 | `packager.py` | `_pack_<kind>()` + `_DISPATCH` entry | 40 | 2c |
| 5 | `doctrine.py` | one `DOCTRINE_<NAME>` + gate in `_assemble_doctrine` | 30 | 4 |
| 6 | `last_night.py` | tag sets + caption map | 6 | render |

No new modules. Every one of those is a template with the move's name, trigger
and prose substituted in.

## All three weapons are viable first picks. Rank by the right axis.

"Easiest" is ambiguous and the two readings give **opposite** answers:

| | blue | credits | geometry to write | duration | ceiling |
| --- | --- | --- | --- | --- | --- |
| **CHAFF** | 300 | 0 | **none — no target cell** | 3h | high |
| **SNAP** | 100 | 250 | one cell (radius 0) | 1h | low |
| **EMP** | 200 | 250 | up to 3 cells, radius 2 | 8h | highest |

**Easiest to build: CHAFF.** `ChaffFlareMove` has no fields at all beyond its
tag — no cell, no target, no aiming. The only decision is which hour. So there
is no geometry helper and no targeting predicate, and the option builder is a
handful of lines. Nothing else is this cheap to wire.

**Easiest to afford: SNAP.** 100 blue, and the only weapon costing more credits
than blue. The engine says why: *"blue is the scarce thing, so SNAP is the weapon
a poor seat can still field, and the credits are what stop it being free to
spam."* It is fireable inside V12's existing behaviour with no economy work.

**Highest ceiling: EMP.** One launch, 3 missiles, 13 cells each, 8 hours. But
the 386-line `scorch.py` in the reference fork exists almost entirely for this —
the radius-2 diamond and the salvo shaping.

So advise on which constraint bites first:

- code time is short → **CHAFF**
- blue is short, or you want to fire tonight → **SNAP**
- you have time and want the ceiling → **EMP**

The trap worth naming: chaff is the easiest to *wire* and the hardest to *fund*.
At 300 blue the five gates start to matter, so a team can wire it in ten minutes
and still never fire it.

**If a team wants two weapons, suggest SNAP + EMP.** SNAP's radius is 0 and the
engine's own comment notes the same `2*r*(r+1)+1` cloud formula yields exactly
one cell — *"so nothing special-cases it."* Build either one's geometry and the
other is nearly free. SNAP + chaff shares nothing.

## Rung 1 is a print statement

The fork's own docstring, which is the clearest statement of the problem
anywhere in the repo:

> V12 carries the same numbers on the view — `orbit.weapon_stock` has always
> been there — and prints them nowhere, so its night phase plans as though the
> rack were empty. The fix is this small: say it out loud.

Keep the block **silent on an empty rack**. A line reading "EMP 0, chaff 0" is
noise on most nights and invites the model to reason about a weapon it cannot
fire.

## Rung 2a — the schema wall

The single hardest blocker, and it fails silently. `chat_schema.py` constrains
the verb with a strict structured-output `enum`, so the model is *physically
unable* to emit a weapon move. No error. The play just never appears.

```python
_MOVE_ITEM = {**_V7_MOVE_ITEM, "properties": {
    **_V7_MOVE_ITEM["properties"],
    "a": {"type": "string",
          "enum": ["drop", "step", "pickup", "probe", "<verb>"]},
    # A salvo's `at` is a LIST of cells where every other verb's is one cell.
    # `_CELL` is `array of integer` and rejects the nested form.
    "at": {"type": "array"},
}}
```

Two things to get right:

- Widen the fork's own `chat_schema.py`. **Never `_v7/`** — that is the frozen
  regression baseline and a test pins it untouched.
- This schema governs only the LLM **mover**, which runs on *fallback* nights.
  The normal path is the packager compiling straight off the option menu and
  never touches it. Both have to know the verb, or the fork fires fine on normal
  nights and is silently disarmed on fallback ones.

Known live example: `emp_harvest_test` widened the enum for `emp_launch` but
never added `snap`, despite shipping `_pack_snap` and two SNAP options.

## Rung 2b — one option, and where it lands in the menu

`_KIND_HEADERS` is a plain list iterated in literal order, and **that order is
the menu's group order.** The reference fork put `emp` and `snap` first, ahead of
`grab`, deliberately. A new kind needs one tuple there (position = print order)
and one `_KIND_BLURB` entry.

Menu burial is therefore *not* the reason a weapon gets refused — the weapon
block is first. `value_pyramid.py` has nothing to do with weapon ranking; it only
orders RED/BLUE grab candidates.

## Rung 3 — the offer has to argue

`Option.detail` and `Option.rationale` are the whole of rung 3. The shape worth
copying:

```python
Option(
    option_id="EMP_BONANZA",
    kind="emp",
    title="EMP BONANZA — three of their eyes, one cloud, (14,9)",
    detail=(
        "One EMP, radius 2, 8-hour cloud. Catches rival probes at (13,8), "
        "(14,9) and (15,10) — every eye they have on the north seam. "
        "Costs 200 blue and one hour-slot."
    ),
    execute_lines=["EMP_BONANZA: emp at [14, 9]"],
    rationale=(
        "Their whole northern read is three probes in a 2-cell huddle, which "
        "is a filing error, not a formation. One cloud blinds all of it for "
        "8 hours. They cannot drop into cells they cannot see (§3.9.7), so "
        "tonight's landing on your pure at (15,11) is refused — not delayed. "
        "Compare: SNAP is 100 blue and kills exactly one of the three, which "
        "leaves the drop legal from the other two."
    ),
)
```

Four things it does deliberately, all worth copying: the **title** leads with the
outcome not the coordinates; the **detail** is honest arithmetic; the
**rationale argues against the alternative**; and the name is silly while the
content is exact.

That third one is the highest-value habit in the harness. The model is choosing
*between* options, so an option that only praises itself is competing badly.

## Rung 4 — doctrine, and the gate polarity trap

One constant, one gated `text +=` in `prompt._assemble_doctrine`. Gate on **your
own** rack:

```python
if stock(agent_view).get("<kind>", 0) > 0:
    text += "\n\n" + doctrine.DOCTRINE_<NAME>
```

V12 ships only the `BEWARE_*` blocks, which gate on the **rival's** estimated
arsenal. So on a quiet board — precisely the cheapest night to fire — no weapon
doctrine enters the prompt at all.

Keep it under about 40 lines. Doctrine speaks nouns, not wire verbs; the
reference fork's `doctrine.py` contains zero occurrences of `emp_launch`,
`snap_launch` or `chaff_flare`.

## Packager invariants, learned the hard way

- A salvo is **one move and one hour**, not three, and consumes one of the 21
  move slots.
- **Friendly fire is on**, and the house rule is *warn without cutting* — price a
  bad play, do not forbid it.
- Fire early or not at all. Past roughly hour 2 a cloud has no night left to
  exploit.
- A geometry helper returning `None` is a real answer. An option that lies about
  its own geometry is worse than no option.

## Name collisions

Never bare find-and-replace a weapon noun. `snap` also appears as `snapshot`,
`_snap_payload`, replay scene snaps, and UI "snap to live". The same trap
destroyed an agent once via `mine` — which is simultaneously own fleet
(`entities.mine`), a discovered redsign (`situational.mine`), and a stat
(`cells_mined`). Prefer a qualified name like `snap_hot_cells` over `hot_cells`.
