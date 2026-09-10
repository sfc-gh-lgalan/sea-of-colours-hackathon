# The three weapons, and the five archetypes

## Two different costs, and they rank in opposite directions

This is the thing to get straight before advising a team. "Easiest" is
ambiguous, and the two readings give opposite answers.

| | blue | credits | geometry to write | duration | ceiling |
| --- | --- | --- | --- | --- | --- |
| **CHAFF** | 300 | 0 | **none — no target cell at all** | 3h | high |
| **SNAP** | 100 | 250 | one cell (radius 0) | 1h | low |
| **EMP** | 200 | 250 | up to 3 cells, radius 2 (13 each) | 8h | highest |

**Easiest to build: CHAFF.** `ChaffFlareMove` carries no fields beyond its tag.
No cell, no target, no aiming. The only decision is *which hour*, so there is no
geometry code and no targeting predicate — the option builder is a handful of
lines. Nothing else in the game is this cheap to wire.

**Easiest to afford: SNAP.** 100 blue, and it is the only weapon that costs more
credits than blue. That asymmetry is deliberate, per the engine's own comment:
*"blue is the scarce thing, so SNAP is the weapon a poor seat can still field,
and the credits are what stop it being free to spam."*

So the choice depends on which constraint bites first:

- **Code time is the binding constraint** → CHAFF. Fewest moving parts by a wide
  margin.
- **Blue is the binding constraint** → SNAP. Fireable within V12's existing
  behaviour, no economy work needed.
- **You want the highest ceiling and have the time** → EMP.

The trap is that CHAFF is easiest to *wire* and hardest to *fund*: 300 blue
means the five blue gates start to matter, so a team that wires chaff in ten
minutes may still never fire it. Read `references/blue-economy.md` in that case —
but only after the weapon fires.

## What each one actually does

**CHAFF** — at the hour it resolves, and for two hours after, **every other
seat's action that hour is cancelled.** Probes, drops, steps, pickups, weapon
launches, even other chaffs. Your own chaff succeeds, and your own later hours
are untouched. It is a global timing weapon: you are not aiming at a place, you
are deleting a stretch of everyone else's night.

**SNAP** — one cell, one hour. Any probe on it is destroyed and any harvester on
it is damaged, **including one that arrives during the hour.** That last clause
is the whole point: it lets a SNAP *guard* a square rather than merely punish
one. It resolves above the hour-start vision snapshot, so the landing it denies
is refused tonight, not delayed.

**EMP** — one launch fires up to 3 missiles at once, each darkening a radius-2
diamond of 13 cells for 8 hours. Probes inside die; harvesters are disabled hour
by hour. Rivals cannot drop into cells they cannot see, so a well-placed cloud
refuses a landing outright. Friendly fire is on.

## The five archetypes

Present these by what they *do*. None is the right answer; the arguing is the
point.

### The all-in — CHAFF, and the fastest thing to build
Chaff on the hour a rival is committed. Everything they had planned for that
hour simply does not happen.

- **Trigger:** you can afford it and a rival is mid-commitment. Or, at its
  simplest, the lift hour, every time.
- **Build cost:** lowest of any weapon. No cell to choose.
- **Risk:** 300 blue, so you may wire it and never fire it. And it hits *every*
  other seat, which in a four-player game makes you three enemies at once.

### The tempo tax — SNAP, and the fastest thing to fire
A cheap SNAP most nights, aimed at whichever single eye is lighting their best
target. Never spectacular, always annoying.

- **Trigger:** their freshest probe covers a cell they were going to use. Or
  simply: every night you can afford it.
- **Build cost:** one targeting predicate over rival probes.
- **Risk:** low ceiling. You win small and often.

### The insurance policy — SNAP, defensive
Never fire offensively. Spend everything guarding your own landings — which
works because a SNAP catches a harvester that *arrives* during the hour.

- **Trigger:** you are about to land on a pure and a rival can see it.
- **Why it's good:** boring, surprisingly hard to beat, and the easiest doctrine
  to write because the condition is about your own plan rather than a read of
  theirs.
- **Risk:** protects your grab and does nothing to their tempo.

### The bonanza — EMP, highest ceiling
Wait for clustered rival probes, blind the lot with one launch.

- **Trigger:** two or more rival probes inside one radius-2 diamond.
- **Why it's good:** one cloud can refuse a landing for 8 hours — refused, not
  delayed.
- **Risk:** needs patience and the window closes fast. Three probes in one radius
  tonight will not be tomorrow. Costs real geometry.

### The bluff — any weapon, fires nothing
Buy a full 600 of arsenal and never use it.

- **Trigger:** none. That is the point.
- **Why it's interesting:** every player's *total* weaponised blue is public and
  its composition is not, so 600 could be two chaff, three EMPs or six SNAPs.
  Rivals play around a rack you are not using.
- **Risk:** it costs 600 blue, and it is the one archetype where the baseline's
  existing behaviour nearly suffices — so the team learns least. V12's own README
  warns that buying without firing makes the agent *worse*.
- **If a team picks this**, push back once: pair it with a cheap SNAP so the rack
  is not pure theatre. Then let them have it.

## A note on SNAP and EMP sharing code

SNAP's radius is 0, and the engine's comment notes that the same
`2*r*(r+1)+1` cloud formula gives exactly one cell — *"so nothing
special-cases it."* If a team builds EMP geometry, SNAP is nearly free
afterwards, and vice versa. Worth saying to a team that wants two weapons: pick
SNAP and EMP, not SNAP and chaff, because the second one is then much cheaper.

## If they want free text instead

Take their sentence. If it is vague, do not ask them to be more specific — offer
two concrete readings and let them pick.

Vague: *"use the EMP aggressively."*
Offer: *"Do you mean (a) blind their probes so they cannot land on your pure, or
(b) blind the ground you are about to walk so they cannot watch you work?"*

Different plays, different triggers, different geometry — and the team almost
certainly has one of them in mind.

## The question that matters most

Whatever they choose, get this out of them in one sentence:

> "We kill the eye that lights their pure, the night they were going to land
> on it."

If they cannot say it in one sentence, the doctrine they write will not say it
either. That sentence becomes the `when` field on the spec and the spine of the
doctrine block.
