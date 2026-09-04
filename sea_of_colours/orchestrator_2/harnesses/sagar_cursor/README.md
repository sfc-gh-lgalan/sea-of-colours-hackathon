# sagar_cursor — Price of War

A fork of `tabula_v12`. Ten boards, `armed` rung, both loadouts, three runs per
cell, live `claude-haiku-4-5`:

```
stock V12 (this fork, unmodified)   83%   ·  5/20 clean  ·  16x no_wake_reentry
sagar_cursor (shipping default)     92%   · 10/20 clean  ·   0x no_wake_reentry
                                    confirmed on two independent samples
```

Every number here is a measured run that was verified to have reached the model.
That check is not a formality — see §5.

**Read §4 before quoting anything.** A four-model review panel went through the
claims in this file and required several of them to be withdrawn. What is left
is what the measurements actually support.

---

## 1. What ships

**One change is on by default: wake-aware routing.**

The suite's most frequent failure was a second harvester walking ground the
first had already stripped — 16 of 60 turns, 15 of them re-entering one cell.
The mechanism is an engine rule: harvest flips RED to GREEN, so the second unit
arrives at synthetic green, banks nothing, burns hours, and adds a parcel worth
−100 at season end.

Seam patterns already exclude their **own** earlier waves (`_value_drop(...,
exclude=wave1_wake)`; `UNBEATEN_FLANK` threads the same set into its comb after
OBS-54). Nothing did so **across two separately selected options**, because each
is built without knowing what else the agent will pick.

The fix never cuts a walk — route shape belongs to the agent. It flips the L:

```
direct   (32,18) -> (31,18) -> (31,19)     (31,18) stripped by an earlier wave
flipped  (32,18) -> (32,19) -> (31,19)     same length, same destination
```

Same hours, same cell reached, different corner. When neither corner is clean
the original route is kept and priced, as before.

The mediator is reported alongside the outcome: `no_wake_reentry` went from 16
occurrences to 1, and the suite score from 83% to 92%, on the tested ten-board
suite under recorded conditions. Whether that transfers to other boards is a
mechanical argument, not a statistical one — see §6.

## 2. What is built and switched off

| Variable | Default | What it enables |
| --- | --- | --- |
| `SAGAR_CURSOR_LEDGER_LABELS` | **off** | An expected-points number on every menu option |
| `SAGAR_CURSOR_LEDGER_ORDER` | **off** | Menu ordered by that number rather than by affordability |
| `SAGAR_CURSOR_ORDNANCE` | **on** | EMP and chaff options on the menu, EV-gated |

```bash
SAGAR_CURSOR_LEDGER_LABELS=1 SAGAR_CURSOR_ORDNANCE=1 \
    python scripts/soc.py suite --agent sagar_cursor --runs 3
```

**The enabled configuration is not validated.** It is an experiment with a
partially demonstrated capability (§3), and the flag defaults to off.

### Why the labels are off and the ordnance is on

**Ordnance is on because the control run showed it costs nothing.** With pricing
off it measured 92% / 10 of 20 clean / zero wake collisions, twice — the
highest-observed configuration, and the same one that occasionally fires. There
was no trade to make between scoring well and carrying a weapon; an earlier
draft inferred one from a confounded comparison.

**The labels are off on conservative grounds, not a proven ranking.** The
90–92% band across builds is **unpowered** — three runs per board cannot resolve
one- and two-point differences, and no paired uncertainty estimate has been
computed from the suite's own scoring function. Every build carrying labels
measured 90–91% and every build without them measured 92%, which is an
association worth acting on and not a mechanism worth asserting.

Turning the labels on buys **reliable** firing (12 selections per sample rather
than 2 and 0) at an apparent cost inside that unpowered band. That is a
reasonable trade for anyone who wants a demonstrably fighting agent, and it is
one environment variable away.

What remains unvalidated regardless of flags:

- the EMP has **never fired** in play;
- the salvo path is **compile-verified only**;
- the EMP is **unaimable on this suite** because rival probes are not available
  at plan time (§5);
- BLUE-denial behaviour and season-level scheduling are **unvalidated**.

## 3. What the ordnance work does and does not show

**Demonstrated:** chaff fired in live play — 12 selections across 7 boards with
the flag enabled — and the agent held fire on `plain_night_armed`, the control
board with a full rack where weapon use is deliberately unscored.

**Not demonstrated:** the EMP has never fired; the salvo compiles but is
untested in play; BLUE-denial behaviour and λ scheduling are unvalidated.

### What the visibility control settled

The review panel required one experiment before any attribution could stand:
renderer registration fixed, ordnance **on**, pricing **off**. Two samples:

| | Score | Clean | Wake | Fired |
| --- | --- | --- | --- | --- |
| v10 | 92% | 10/20 | 0 | 2 |
| v11 | 92% | 10/20 | 0 | 0 |

Three things follow, and they are not the things an earlier draft claimed.

**Menu visibility is necessary.** Nothing fired in any run while the option was
absent from the rendered menu — that is the renderer defect in §5, not a
judgement the model made.

**Visibility alone is not sufficient in practice.** Two shots and then zero,
across 120 turns, is at or below noise. The capability is present and it is not
reliably exercised.

**Pricing is what makes firing reliable, not what makes it possible.** With
numeric labels the count is 12 in one sample and 12 in another (v3, v4) — a
large and repeatable increase over 2-and-0. So the numbers *amplify* selection
rather than enable it.

**And the weapon itself is free.** Ordnance on with pricing off measured
92%/10/0 twice, against 92%/9/1 with ordnance off. Every build that lost ground
carried the labels. The apparent cost sits with presentation, not with the
weapon and not with the firing — though the differences involved are inside the
unpowered band and are stated as an association, not a mechanism.

### The claim that was withdrawn

`docs/TEACHING_WEAPONS.md` builds all four firing rungs and concludes the
residual problem is *persuasion*. An earlier draft of this README claimed the
real cause was **units** — that pricing options in a common currency is what
made the weapon selectable.

**That claim is not supported and has been withdrawn.** Enabling the Ledger
also fixed a renderer defect that had been dropping the weapon option before the
model ever saw it (§5, finding 2). Menu *visibility* and menu *pricing* changed
in the same step and were never separated, so the firing cannot be attributed to
either.

What can be stated is the bare association:

> No firing occurred while the weapon option was absent from the rendered menu.
> Roughly 12 chaff selections occurred after rendered, priced options appeared.
> Renderer kind-registration, numeric labels, pricing salience and other
> presentation effects remain **unseparated**.

A second withdrawn claim: an earlier draft argued the Ledger *costs* score,
citing v8 (ordnance enabled, gate held on every board, nothing fired, 90%). What
v8 supports is narrower — **executed firing is excluded as a necessary cause
within that run set** — and no cost or alternative mechanism is established.
Route search, option combinations, turn usage and sampling noise remain
unseparated.

## 4. Build-by-flag matrix

Every mechanism sentence above is gated on controlled rows of this table.

| Build | leg flip | route search | renderer | labels | EV order | EV gate | ordnance | Score | Clean | Fired | Wake |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v0 | – | – | – | – | – | – | – | 83% | 5/20 | 0 | 16 |
| v2 | ✓ | – | – | – | – | – | – | 92% | 10/20 | 0 | 0 |
| v3 | ✓ | – | ✓ | ✓ | ✓ | – | ✓ | 91% | 8/20 | 12 | 3 |
| v4 | ✓ | – | ✓ | ✓ | – | – | ✓ | 91% | 7/20 | 12 | 3 |
| v5 | ✓ | ✓ | ✓ | ✓ | ✓ | – | ✓ | 90% | 5/20 | 13 | 5 |
| v6 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 90% | 6/20 | 6 | 4 |
| v7 | ✓ | ✓ | ✓ | – | – | – | – | 91% | 8/20 | 0 | 4 |
| v8 | ✓ | – | ✓ | ✓ | ✓ | ✓ | ✓ | 90% | 7/20 | 0 | 4 |
| v9 | ✓ | – | ✓ | – | – | – | – | 92% | 9/20 | 0 | 1 |
| **v10** | ✓ | – | ✓ | – | – | ✓ | ✓ | **92%** | **10/20** | 2 | **0** |
| **v11** | ✓ | – | ✓ | – | – | ✓ | ✓ | **92%** | **10/20** | 0 | **0** |

Notes, because the table invites over-reading:

- **Clean-board count is a diagnostic, not a ranking metric.** The league ranks
  on suite predicate score. The 5→10 change across the wake comparison is worth
  leading with; the 7–10 spread among later builds is not a ranking.
- **v9 is not yet a controlled replication of v2.** Credential state, model
  version, prompt, sampling settings, kit version and fallback mode have not been
  confirmed equivalent. Recorded as open.
- **v5 is a negative result kept on purpose.** Searching every same-length
  staircase was predicted better by its own design criterion and measured worse
  on this sample. It is not "strictly better"; the reasoning is kept in
  `packager.py` beside the code that won.
- **v10 and v11 are the same configuration, run twice.** They are the shipping
  default. Score, clean count and wake collisions reproduce exactly; the firing
  count does not (2, then 0), which is the honest picture of a capability that
  is present but rarely selected without pricing.

## 5. Three findings about the kit, not this agent

**Finding 1 — a credential-less run scores higher, silently.** With no
`sf_config` and no `SNOWFLAKE_PAT`, the `packager_used` branch in `harness.py`
returns *before* the mover is invoked, and `fallback_used` is only set when an
*attempted* call fails to parse. So `fallback_rate` stays 0 and every surface
keyed on it goes quiet.

Reproduced on one board, `final_night_ring@armed+both`, one run each:

| | credentials present | credentials absent |
| --- | --- | --- |
| wall clock | ~16 s/turn | **0.11 s/turn** |
| score | 57% | **86%** |
| fallback warning | n/a | **none** |

A *rejected* token behaves correctly and loudly (90% fallback rate, unmissable
warning). Only an *absent* one is silent. The honest discriminator is
`thinker=none` with an empty `plan=`, which no surface reports.

*Status:* the timing and score are **verified** on that denominator — one board,
one run per condition. Unmarked league admission is **inferred from source**
(`report.py` marks on `fallback_rate > 0`), not yet exercised through the
submission path.

*Suggested remedy:* fail closed for credential-required execution, or record an
explicit execution mode that marks or rejects runs when credentials are absent,
zero model calls were attempted or completed, or fallback was used. Do not mark
a run valid merely because attempted calls exceed zero. Near-zero wall time is a
**screening heuristic** needing corroboration — not grounds on its own to
invalidate a historical entry.

**Finding 2 — a renderer completeness defect.** `soc weapons` reported all four
rungs PASS while the menu carried no weapon. `_KIND_HEADERS` is what the
renderer iterates, and an option whose kind is absent from it is registered and
then dropped in silence.

**Finding 3 — a suite observability limitation.** The boards place rival probes
in engine truth, but the seat's fog-limited view carries none of them at plan
time (`competitor_intel` empty, no echoes, `_enemy_probes() == []`). An EMP is
unaimable on this suite however many rungs are built.

## 6. Limitations

- **All builds share one evaluation set** — the same ten boards at one rung.
  There is **no held-out board and no cross-rung evidence**. Per-option-kind
  calibration is a design guard against memorising the suite; it does not
  demonstrate generalisation, which remains unresolved.
- **The 90–92% band is unpowered.** No comparison inside it carries a paired
  uncertainty estimate, so none of it supports a mechanism or a ranking.
- **No season has been played.** The suite scores one night at a time and cannot
  say whether the agent holds a season together.
- **λ is fitted** from realised denial per BLUE spent and is exactly zero on the
  final night; it never supplies the denial credit. Its residuals, sample size,
  ranking sensitivity, held-out fit and season behaviour are **unreported**, so
  no validation claim is made for it.

## 7. Files

| File | What it is |
| --- | --- |
| `packager.py` | Wake-aware routing (`_steps_avoiding`). **The shipping change.** |
| `ledger.py` | Expected-points pricing, BLUE shadow price, menu ranking (off) |
| `calibration.py` | Per-kind predicted-vs-actual; local JSON required, Snowflake mirror opt-in |
| `scorch.py` | Reads the rack off the view; salvo geometry; the prompt block |
| `agency.py` | `_emp_option`, `_chaff_option`, the EV gate, the `weapon` kind header |
| `chat_schema.py` | Widened move enum — `emp_launch`, `chaff_flare`, `extra_ats` |
| `doctrine.py` | `DOCTRINE_SCORCH` — when to spend a charge, gated on holding one |

Nothing outside this directory is modified. `_v7/` is untouched.

## 8. Reproducing

```bash
export SF_CONFIG_FILE=/path/to/your/sf_config
python scripts/soc.py suite --agent sagar_cursor --rung armed \
    --loadout empty,both --runs 3 --cards /tmp/cards

# ALWAYS check the run was real before believing the number:
python design/provenance.py /tmp/cards
```

`provenance.py` reports how many turns never called the model, who authored each
route, and every wake collision with its cell. It caught two invalid runs during
this build before their numbers reached a report.
