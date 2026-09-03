# sagar_cursor — Price of War

A fork of `tabula_v12`. Ten boards, armed rung, both loadouts, three runs,
live `claude-haiku-4-5`:

```
stock V12 (this fork, unmodified)   83%   ·  5/20 clean  ·  16x no_wake_reentry
sagar_cursor (shipping default)     92%   ·  9/20 clean  ·   1x no_wake_reentry
```

Every number in this file is a measured run that was verified to have reached
the model. That check is not a formality — see §4.

---

## 1. What actually ships

**One change is on by default: wake-aware routing.** It is the whole of the
+9 points.

The suite's most frequent failure was a second harvester walking ground the
first had already stripped — 16 of 60 turns, 15 of them re-entering a single
cell. The engine turns RED into GREEN on harvest, so the second unit arrives at
synthetic green: it burns hours, banks nothing, and adds a parcel worth −100 at
season end.

Seam patterns already exclude their **own** earlier waves (`_value_drop(...,
exclude=wave1_wake)`, and `UNBEATEN_FLANK` threads the same set into its comb
after OBS-54). Nothing did so **across two separately selected options**, because
each option is built without knowing what else the agent will pick.

The fix respects the packager's house rule that route *shape* belongs to the
agent. It never cuts a walk. It flips the L:

```
direct   (32,18) -> (31,18) -> (31,19)     (31,18) was stripped by wave 1
flipped  (32,18) -> (32,19) -> (31,19)     same length, same destination
```

Same number of hours, same cell reached, different corner. When neither corner
is clean the original route is kept and priced, exactly as before.

> **A negative result worth keeping.** Searching *every* same-length staircase
> instead of just the two corners looks strictly better and measured worse — 4
> wake collisions against 0. This is a sequencing problem wearing a geometry
> problem's clothes: re-planning each leg in isolation changes the ground that
> wave strips and hands the next unit a board it did not expect. The reasoning
> is kept in `packager.py` beside the code that won.

## 2. What is built, tested, and off by default

Three environment variables. All default off, because each measured *worse* on
the league metric — and the league ranks purely on suite score
(`sorted(results, key=lambda r: (-r.score, r.agent))`).

| Variable | Default | What it turns on |
| --- | --- | --- |
| `SAGAR_CURSOR_LEDGER_LABELS` | off | An expected-points number on every menu option |
| `SAGAR_CURSOR_LEDGER_ORDER` | off | Menu ordered by that number instead of by affordability |
| `SAGAR_CURSOR_ORDNANCE` | off | EMP and chaff options on the menu, EV-gated |

```bash
# the agent that fires
SAGAR_CURSOR_LEDGER_LABELS=1 SAGAR_CURSOR_ORDNANCE=1 \
    python scripts/soc.py suite --agent sagar_cursor --runs 3
```

They are off, not deleted, because the experiment they enabled answered the
question the kit left open. See §3.

## 3. The finding

`docs/TEACHING_WEAPONS.md` records that building all four firing rungs does not
make an agent fire, and concludes the residual problem is *persuasion*. It is
not. It is **units**.

Every harvest option is quoted as a number and every weapon option as prose:

```
[GRAB1] SMASH the pure (16,6)
     yield: red ~+1425 (1 pure, 2 mass) · blue 0 · green 0

[JAM] Chaff flare — freeze every rival for 3h
     cancels EVERY other seat's action for 3h board-wide ...
```

A small model under an 800-token plan budget cannot compare those, so it takes
the number. Price both in expected points and it fires. **Measured: 0 shots
before, 12 after.**

The ablation attributes it precisely. Twelve flares with EV ordering on and
twelve with it off — **the numbers cause the firing, not the ranking.**

And where it declines matters as much. On `plain_night_armed` — the control
board, no jackpot, full rack, weapon use deliberately unscored — it held fire
in every run, because the ledger priced the shot at ~+38 against a grab worth
~+1425.

**Why it is still off:** with ordnance enabled and the EV gate holding on every
board, so that *nothing fired*, the suite still scored 90% against 92%. The cost
is not the firing. It is the menu — relabelling and reordering changes which
option *combinations* get picked, and the new ones collide where the old ones
did not. The capability is real; the league metric declines to pay for it.

## 4. Three things about the kit, not this agent

**A credential-less run scores higher, silently.** With no `sf_config` and no
`SNOWFLAKE_PAT`, a turn completes in 0.11s instead of 16s, scores 86% instead of
57% on `final_night_ring`, prints no fallback warning, and would enter the league
**unmarked** — the asterisk is keyed on `fallback_rate`, which stays zero because
the `packager_used` branch returns before any call is attempted. A *rejected*
token is handled correctly and loudly; an *absent* one is silent. The honest
discriminator is `thinker=none` with an empty `plan=`, which no surface reports.

**There is a fifth rung above the four.** `soc weapons` reported all four rungs
PASS while the menu carried no weapon at all. `_KIND_HEADERS` is what the
renderer iterates, so an option whose kind is missing from it is registered and
then dropped in silence.

**You cannot aim at what you cannot see.** The battle boards place rival probes
in engine truth, but the seat's fog-limited view carries none of them at plan
time (`competitor_intel` empty, no echoes, `_enemy_probes() == []`). An EMP is
unaimable on this suite regardless of how many rungs are built, which is why the
weapon that fired was the one needing no target.

## 5. Files

| File | What it is |
| --- | --- |
| `packager.py` | Wake-aware routing (`_steps_avoiding`). **The shipping change.** |
| `ledger.py` | Expected-points pricing, BLUE shadow price, menu ranking |
| `calibration.py` | Per-kind predicted-vs-actual, local JSON, optional Snowflake mirror |
| `scorch.py` | Reads the rack off the view; salvo geometry; the prompt block |
| `agency.py` | `_emp_option`, `_chaff_option`, the EV gate, `weapon` kind header |
| `chat_schema.py` | Widened move enum — `emp_launch`, `chaff_flare`, `extra_ats` |
| `doctrine.py` | `DOCTRINE_SCORCH` — when to spend a charge, gated on holding one |

Nothing outside this directory is modified. `_v7/` is untouched.

## 6. Reproducing

```bash
export SF_CONFIG_FILE=/path/to/your/sf_config
python scripts/soc.py suite --agent sagar_cursor --rung armed \
    --loadout empty,both --runs 3 --cards /tmp/cards

# then ALWAYS check the run was real before believing the number:
python design/provenance.py /tmp/cards
```

`provenance.py` reports how many turns never called the model, who authored each
route, and every wake collision with its cell. It caught two invalid runs during
this build before their numbers reached a report.

## 7. Honest limitations

- The EMP has never fired. Every shot is chaff; the salvo path compiles but is
  untested in play.
- Ten boards, one rung. Per-kind calibration defends against memorising them; it
  does not prove they were not memorised.
- The armed-versus-unarmed gap peaked at 92% vs 91%, which three runs cannot
  separate from noise. It is reported as the first time they differed at all,
  not as a win.
- No full season has been played. The suite scores one night at a time and
  cannot tell you whether an agent holds a season together.
