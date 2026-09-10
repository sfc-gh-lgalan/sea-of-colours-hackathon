# Phase 2 — Generate

Target: **under a minute.** Two commands and one dict.

Hand-wiring one compound play across six files was measured at **7m42s** and
introduced two bugs — a broken `if/elif` chain in `prompt.py`, and two calls to
helper functions that did not exist. Do not repeat that. Install the forge once
and declare the move as data.

## Step 1 — install the forge (once per fork)

```bash
python skills/soc-agent-forge/scripts/forge_install.py <label>          # dry run
python skills/soc-agent-forge/scripts/forge_install.py <label> --apply
```

Measured at **0.24s**. It copies in two files and inserts ten one-line hooks:

| File | Hook | Rung |
| --- | --- | --- |
| `chat_schema.py` | `widen_schema()` on the move enum | 2a |
| `agency.py` | `build_options()` into the registry; weapon groups first | 2b, 3 |
| `packager.py` | `_DISPATCH.update(packers())` | 2c |
| `prompt.py` | `format_rack_block()` + `doctrine_for()` | 1, 4 |
| `last_night.py` | `frame_tags()` / `public_tags()` into the tag sets | render |

Every fresh mint is a byte-identical `tabula_v12` copy, so those anchors are
deterministic. The installer **refuses** if any anchor is missing or ambiguous,
naming which — a fork that has been hand-edited is not safe to patch blind. It
is idempotent, and it never touches `_v7/` (the frozen regression baseline, which
a test pins).

## Step 2 — declare the move

Edit **one file**: `harnesses/<label>/weapon_plays.py`.

```python
PLAYS = (
    WeaponPlay(
        play_id="CANCEL_SMASH",
        weapon="chaff",
        when="redsign_theirs",
        hour="super_early",
        combines_with="blind_grab",
        why="a seat that has just found a pure drops on it at hour 1, so "
            "cancelling that one hour takes their whole opening and leaves "
            "the pure sitting there for us to walk onto",
    ),
)
```

That is the whole edit. A second or third move is one more entry — not another
forty-five edit sites. `weapon_forge.py` derives the rest: the option and its
rationale, the wire move, the menu group and blurb, the replay tags, the schema
enum, and the doctrine.

Overrides exist if a team wants them — set `title` or `rationale` to hand-write
either. Leave them empty and both are composed.

## Step 3 — check it, behaviourally

```bash
python skills/soc-agent-forge/scripts/check_wiring.py <label>
```

This **imports the fork and interrogates the live objects** — the enum, the
dispatch table, the tag sets — then builds an option from a synthetic
rival-redsign board and inspects it. Eleven checks. It is the only thing here
that proves the menu path end to end.

**`soc weapons` will under-report a forged agent, and that is expected.** Its
rungs are substring greps over *named files*: rung 2 looks for a weapon verb
inside `agency.py` / `chat_schema.py` / `packager.py`. The forge derives verbs
from data in `weapon_forge.py`, so those files no longer contain the literal and
rung 2 reads FAIL on a working weapon. Say this to the team when they see it,
and trust `check_wiring.py` instead. (`soc weapons` remains useful on
hand-wired forks.)

## What the validator catches for you

`weapon_forge.validate_all()`, run as part of `check_wiring`, rejects:

- a `why` that is empty — without it the rationale cannot be composed, and an
  option that cannot argue for itself does not get picked
- `when="other"` with no `trigger` function
- an EMP at `hour="mid"` or `"late"` — an 8h cloud fired then has no night left
- an unknown weapon, when, hour or combines_with value
- the same `play_id` declared twice

## The two things the forge does that a team would miss

**The replay tag is not always the wire verb.** `snap_launch` on the wire arrives
as `snap` in the replay frame. A tag set keyed on the verb silently drops every
frame, the hour goes missing from the execution log, and the agent then journals
that its own move never executed — corrupting the next night's reasoning. The
forge keys both correctly per weapon.

**Chaff jams its own house.** The simulator is explicit: *"firing it costs the
launcher the full duration window."* So the packer holds hours 2–3 with `wait`
after a flare, rather than queueing moves that would simply be cancelled and
read on the card as the play failing. Note the `ChaffFlareMove` docstring claims
the opposite — the simulator is authoritative.

## Gate

`check_wiring.py` all-PASS, including **`doctrine CORRECTS the BEWARE_<weapon>
framing`**. That last line is not decoration: without the correction, V12's
survivor-side doctrine outvotes the offensive block and the weapon stays in the
rack. See `references/doctrine-conflicts.md`.

Then `phases/3-prove.md`.
