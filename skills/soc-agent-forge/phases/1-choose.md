# Phase 1 — Choose the weapons, then name the moves

No code is written in this phase.

## Ask HOW they want to do this, first

Before any weapon question, offer the two modes. This costs one question and can
save five, because a team that already knows what it wants does not need to be
walked through a menu.

**Mode A — step by step.** One question at a time with concrete options: which
weapons, then per move a name, a WHEN, an HOUR, what it COMBINES WITH, and the
WHY. Right for a team that has not decided, or has not played enough to know
what a chaff is for. Costs a round trip per question.

**Mode B — one paragraph.** They describe the whole agent in prose and you parse
it. One round trip for everything. Right for a team that knows its plan.

Show them this worked example, verbatim — it is a real brief that produced a
working agent, and it is the fastest way to convey what "enough detail" means:

> mint a new agent called mr_giggles for lucas that wants to get chaff asap, and
> hold it until there is an enemy redsign and shoot the chaff h1 and then back it
> up with a smash and grab. it will need lots of blue, so it should seek blue
> whenever it is not holding a chaff

That paragraph carries everything the spec needs: the name, the owner, the
weapon, the buy urgency, the hold condition, the fire hour, the follow-up play,
and the blue policy. Point out which phrase mapped to which field once you have
parsed theirs — it teaches the shape for the second move.

**If a paragraph is missing something, ask only for the gap.** Do not restart the
interview. And do not invent the missing field: a `why` you wrote yourself is a
rationale the team does not believe, and the `why` is what the model reads when
it chooses.

## Mode A — the questions

Use `ask_user_question` so they can click rather than type. Keep option previews
SHORT — two or three lines. Long previews are slow to produce and mostly restate
the option label, which is a cost paid on every question.

### Q1 — which weapons?

Multi-select. All three are viable and they are **not** ranked the same way on
cost as on effort:

| | blue | credits | geometry to write | duration |
| --- | --- | --- | --- | --- |
| **CHAFF** | 300 | 0 | **none — no target cell** | 3h |
| **SNAP** | 100 | 250 | one cell (radius 0) | 1h |
| **EMP** | 200 | 250 | up to 3 cells, radius 2 | 8h |

**Do not crown a default.** Ask which constraint bites first:

- *short on code time* → **chaff**. It aims at nothing, so there is no geometry
  and no targeting predicate.
- *short on blue, or want to fire tonight* → **snap**. 100 blue, fireable inside
  V12's existing behaviour with no economy work.
- *time to spend, want the ceiling* → **emp**. 8 hours of refused ground.

Two things to say out loud when they pick:

- Chaff is easiest to **wire** and hardest to **fund**. At 300 blue the five
  blue gates start to matter, so they can build it in ten minutes and never
  fire it. That is what Q3 is for.
- If they want two weapons, suggest **snap + emp**. SNAP's radius is 0 and the
  engine notes the same `2*r*(r+1)+1` cloud formula gives one cell, "so nothing
  special-cases it" — build either geometry and the other is nearly free.
  Snap + chaff shares nothing.

Full detail in `references/archetypes.md`.

---

## Q2 — For each weapon, the moves

One move at a time. Each needs four things, and all four go in the spec.

### NAME

Offer three suggestions so they have something to react to. Names are public —
they show up in the game log as the night resolves, in the lab's frozen-turn
journals and in the season cards.

Be as silly as you like about the **tone**, never about the **content**.
`SMASHBURGER` for a smash on your own pure seam is perfect; `SMASHBURGER` for a
defensive probe is a bug they will spend an hour not finding.

**Your name is printed beside V12's own 18 named seam patterns and 8 numbered
families** — `SMASH_GRAB`, `BLIND_GRAB`, `UNBEATEN_FLANK`, `CONTEST_DENY`,
`GRAB1`, `CH2`… The model picks between them by id, so the neighbours matter.
`references/v12-vocabulary.md` has the full list and the three naming rules:
don't collide, don't borrow an established verb (`SWEEP`, `BLIND`, `CONTEST`,
`SECURE`, `WALK_IN`), silly tone and exact content.

Then check it before accepting:

```bash
grep -rn "<PROPOSED_NAME>" sea_of_colours/orchestrator_2/harnesses/<label>/
```

### WHEN — the board condition

| Choice | Means |
| --- | --- |
| `always` | every night we hold the weapon — right when `targets` is the real gate |
| `redsign_mine` | a pure **we** found is live — we are defending it |
| `no_redsign` | no pure is lit anywhere — see the warning below before using this |
| `redsign_theirs` | a pure a **rival** found is live — we are attacking it |
| `other` | a custom predicate, which you write |

> **Do not reach for `no_redsign` to give the agent "something to do on a quiet
> night."** This row used to read *"good for pre-emptive strikes on vision"* and
> that advice produced two bad plays in three agents. A quiet night has no pure
> lit, so there is nothing concrete to deny: the play can name no target and
> quote no number, and the model correctly refuses it. Measured: one such chaff
> play was offered 10 times in a single season and chosen 0 times, while taking
> up menu space beside the play that mattered.
>
> A weapon's case is a specific board fact, not an empty calendar. If you cannot
> name what a play denies, it is not a play. Prefer `when="always"` with a
> `targets` mode that refuses on its own when the board does not offer the
> shape — that way the option disappears honestly instead of arguing weakly.

### HOUR — which slot it takes

Position in the move list *is* the hour, so this is a hard constraint.

`super_early` (H1) · `early` (H1–2) · `mid` · `late` · `last_night`

Say why it differs by weapon: an 8h EMP cloud fired past H2 has no night left to
exploit, so `mid`/`late` is rejected by the validator. A chaff has to land on the
hour they were going to act. A SNAP lasts an hour and can be aimed at any beat.

### COMBINES WITH — what it borrows geometry from

`smash_grab` · `blind_grab` · `probe` · `chain` · `standalone`

This is the field that stops a weapon option being an orphan with invented
coordinates. `CANCEL_SMASH` combines with `blind_grab` and takes that pattern's
wave-1 drop cell and comb path, so the landing after the flare is real ground the
harness already computed.

### TARGETS — where the aim points come from

The most under-used field, and it was undocumented until a play shipped dead
because of it. `combines_with` supplies *geometry*; `targets` decides *what the
warhead points at*, and for a snap or an EMP that is the whole play.

| Choice | Aims at | Refuses when |
| --- | --- | --- |
| `pattern` | the seam pattern's wave-1 drop (default) | no pattern matches `when` |
| `rival_probes` | the freshest rival eyes, as a salvo | fewer real eyes than `min_targets` |
| `redsign` | the rival smear, plus a comb of what you did not darken | the smear is yours, or empty |
| `finder_probe` | the ONE eye lighting a rival's beacon | 2+ eyes cover it and you cannot see the pure |
| `contested_pure` | a pure **you can see** that a rival eye also covers | nothing is contested |

**`contested_pure` is the strong snap target and the reasoning generalises.** A
snap makes one cell hot for one hour, so it only pays if you know where they will
be. You cannot predict a step, a probe or a pickup — but you *can* predict a
pure, because it is the one square worth a smash-and-grab. So work backwards from
the ground, not from their units: find the pures in your own live vision, then ask
which of them a rival can also see. An enemy probe within vision range of a pure
you hold means they have the read, whether or not anyone lit a beacon.

You **can** see a pure. The seat view strips `pure_cells`, but a pure is a
`red_tiles` row at `purity >= 255`, and those arrive wherever you have live
vision. Concluding otherwise from the stripped key costs you this entire class of
play — it is the single most expensive wrong belief in this skill's history.

The cell is hot for you too, but only for that hour: fire at H1 and the landing
queued behind it arrives at H2 on ground gone cold. Their smash-and-grab is
refused and the hull damaged; you take the pure one hour later for 100 blue.

> Add a mode to `TARGETS_CHOICES` **and** to the dispatch tuple in
> `_aim_points`, or it is dead on arrival. `finder_probe` was handled inside that
> block but missing from the tuple, so it never entered: the play fell through to
> the pattern path, borrowed seam geometry, and looked healthy for weeks. Nothing
> raised, nothing logged. `validate()` now rejects an unknown mode for this
> reason.

### TAKE THE GROUND — almost always no

`take_the_ground=False` is the default and most plays should keep it. **A weapon
play buys an HOUR; it should rarely buy anything else.** Leave the walk-in to the
grab option the thinker picks alongside it, whose landing and comb `seam_control`
computes properly.

When a weapon play carries its own comb *and* a grab option is chosen too, the two
half-own the follow-up and neither runs it: the flare fires, the probes go out, and
the harvester never deploys. Only turn it on when the follow-up is *defined by* the
shot — an EMP whose own cloud decides which cells are walkable, or a snap whose
one-hour window is the timing of the landing. Never for chaff, which takes no cell.

`probe_the_comb` requires it, and the validator says so.

### WHY — one sentence, theirs

*What does firing this buy?* In their words.

This is the field that matters most, because the rationale is what the model
reads when choosing. Keep it to one sentence and keep it concrete. "Deny them"
is not a why; "a seat that has just found a pure drops on it at hour 1, so
cancelling that hour takes their whole opening" is.

**You compose the rationale from it, not them.** The full argument needs the
weapon's mechanics, the honest cost, and *the alternative it beats* — and that
last part depends on what else is on tonight's menu, which nobody can know up
front. `weapon_forge.compose_rationale()` assembles all four parts. Read the
result back to them and check they recognise their own read in it.

---

## Q3 and Q4 — the economy

Both answers land in a single `ECONOMY = EconomyPolicy(...)` line in
`weapon_plays.py`, applied by three hooks. **The defaults are already the
sensible answer**, so for most teams this is a confirmation rather than a
decision — show them what it will do and move on:

```
buy ASAP: blue_always_build -> 299 (cheapest declared weapon is 300 blue)
never buy (no declared play): emp, snap
seek blue whenever the rack cannot fire
but never divert the last harvester
```

That output comes from `weapon_forge.economy_summary()`. Read it back verbatim.

**What the defaults do, and why each one:**

- **`buy_asap=True`** — the stock branch tests `blue_total > blue_always_build`
  and *then* affordability at the weapon's price, so a threshold equal to the
  price makes the first purchase wait for price+1. This sets it to price−1 so the
  two agree and the weapon is bought the turn it can be.
- **`never_buy_what_you_cannot_fire=True`** — stockpile cap 0 for any weapon with
  no declared play. Blue spent on ordnance you cannot fire is blue not spent on
  the one you can, and V12's README warns this makes the agent *worse*.
- **`seek_blue_when_rack_empty=True`** — the stock gate asks whether the *vault*
  is short, which is the wrong question: a "medium" vault can hold 150 and still
  be 150 short of a charge. This also asks whether the rack can fire.
- **The last harvester is never diverted.** There is deliberately no flag for
  this — the baseline's own `blue_is_requested` ends with
  `len(harvesters) >= 2`, and that check runs downstream of everything the forge
  does. A flag would have been a knob that could not be turned off, which is
  worse than no knob. Sending your only unit to fetch currency loses more than
  the weapon gains, so this is the right default to be stuck with.