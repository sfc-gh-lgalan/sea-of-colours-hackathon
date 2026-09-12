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

Measured at **0.24s**. It copies in two files and inserts eighteen one-line hooks
across seven:

| File | Hook | Rung |
| --- | --- | --- |
| `chat_schema.py` | `widen_schema()` on the move enum | 2a |
| `agency.py` | `build_options()` into the registry; weapon groups first | 2b, 3 |
| `packager.py` | `_DISPATCH.update(packers())` | 2c |
| `prompt.py` | `format_rack_block()` + `doctrine_for()` | 1, 4 |
| `last_night.py` | `frame_tags()` / `public_tags()` into the tag sets | render |
| `orbit_policy.py` | `tune_dials(OrbitDials())` — buy-ASAP and stockpile caps | economy |
| `orbit_policy.py` | `add_procurement()` — buys ordnance the stock orbital cannot | **0** |
| `value_pyramid.py` | `strong_chain_red_min()` on `_STRONG_CHAIN_RED_MIN` | economy |
| `orbit_policy.py` ×2 | cap the bare `elif _afford_emp()/_afford_chaff()` surplus branches, so `never_buy_what_you_cannot_fire` is real | economy |

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
rival-redsign board and inspects it. Every check. It is the only thing here
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

## `check_wiring.py` needs a live model — unless you say otherwise

The first check makes a **real model call**, because a 401 makes every other
result meaningless: the seat silently falls back to a heuristic that passes the
night, so nothing harvests, nothing banks blue and nothing fires. An afternoon
went into diagnosing that as an agent bug.

If you have no PAT, or you are looping fast, skip just that one:

```bash
python skills/soc-agent-forge/scripts/check_wiring.py <label> --skip-llm
```

Every other check is static and still runs. But do not ship on `--skip-llm`
alone — a season under a 401 produces artefacts that look exactly like a broken
weapon.

## Real targeting — the fields that make a weapon AIM

Three builds in a row lost time here, and the EMP build lost five minutes to it,
because these live only in the `WeaponPlay` dataclass source. If a brief says
"hit their probes" or "cover their smear", you need them.

| field | default | what it does |
| --- | --- | --- |
| `targets="pattern"` | ✔ | borrow the seam pattern's coordinates — fine for chaff, which needs no cell |
| `targets="rival_probes"` | | REAL geometry: aim at their freshest probes, newest first |
| `targets="redsign"` | | REAL geometry: cover a rival's smear, and comb what survives |
| `min_targets=1` | ✔ | refuse to fire below this many real aim points — `2` means "not worth a charge for one eye" |
| `probe_the_comb=False` | | launch a covering probe ADJACENT to the landing, so the walk is lit |
| `comb_max_steps=6` | | cap the follow-up walk; the salvo, probe, drop, steps and pickup all share `MAX_MOVES = 21` |

`targets="rival_probes"` and `targets="redsign"` need **`scorch.py`**, which
`forge_install.py` now copies in automatically. If it is ever missing,
`check_wiring.py` names it as the cause rather than blaming your `when`.

**`combines_with` still matters when `targets` is set** — this trips people. It
stops driving the geometry (`scorch.py` does that now) but it still selects which
rival move the `COMPARE:` clause argues against, via `_resolve_rival()`.

```python
WeaponPlay(
    play_id="NIGHTFALL", weapon="emp",
    when="no_redsign", hour="super_early",
    targets="rival_probes", min_targets=2,      # refuse for a single eye
    combines_with="standalone",
    why="their vision is their plan, so killing the eyes they just built ...",
)
```

## The economy dials — you WILL need these

Three builders in a row had to read `weapon_forge.py` source to find them, so
they belong here. `ECONOMY = EconomyPolicy(...)` in the same file:

| field | what it does |
| --- | --- |
| `buy_asap=True` | drop the build threshold to just under your cheapest weapon |
| `hold_at={"emp": 2}` | stockpile cap per weapon — "buy up to two" |
| `seek_blue_always=True` | seek blue EVERY night, not only when the rack is empty |
| `seek_blue_when_rack_empty` | the default; seek blue when you cannot fire |
| `never_buy_what_you_cannot_fire` | caps undeclared weapons at 0 |
| `strong_chain_red_min=300` | divert a harvester to blue unless a chain banks more red than this |

Print what you actually got — it is the fastest way to catch a typo:

```bash
python -c "import sys; sys.path.insert(0,'.');
from sea_of_colours.orchestrator_2.harnesses.<label> import weapon_forge as f
print('\n'.join(f.economy_summary()))"
```

## Gate

`check_wiring.py` all-PASS, including **`doctrine CORRECTS the BEWARE_<weapon>
framing`**. That last line is not decoration: without the correction, V12's
survivor-side doctrine outvotes the offensive block and the weapon stays in the
rack. See `references/doctrine-conflicts.md`.

Then `phases/3-prove.md`.
