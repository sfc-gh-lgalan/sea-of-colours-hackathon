# Doctrine conflicts — why a correctly-wired weapon still won't fire

This is the failure mode that cost the most time to find, and it is invisible to
every readiness check. The plumbing is perfect, the option is on the menu with a
full rationale, and the agent refuses — **because another doctrine block has
already taught it not to.**

Adding offensive doctrine alongside an uncorrected `BEWARE_*` block leaves the
model holding two frames. The incumbent is older, longer and more specific, and
it wins. So every weapon must ship a **correction**, not just an addition.

## The measured case

A chaff agent was asked to fire at hour 1 to cancel a rival's opening drop. It
refused, in its own words:

> "The chaff is not worth burning here — their H1 drop still lands; better to
> contest hard with harvesters."

That is factually wrong. Cancelling their H1 action *is* what chaff does. But
the model was not being stupid — it was applying V12's own doctrine correctly.

## Suppressor 1 — `DOCTRINE_BEWARE_CHAFF`

Defined in `_v7/strategies.py:310`, loaded whenever chaff is in play. It says:

> "Chaff cancels all OTHER seats' actions for 3 CONSECUTIVE hours"
> "Mitigation — avoid PREDICTABLE pickup windows (chaff is **blind-fired** at
> the hours opponents most naturally pick up)"
> "Avoid HOUR 6-9 … Avoid HOUR 12-16 … **Prefer pickup at hour ≤ 4**"

Three things this teaches, all of which sabotage an offensive chaff:

1. **Chaff is "blind-fired"** — a speculative punt, not a read. So a
   deliberately-aimed flare reads as a category error.
2. **Chaff is a *pickup*-killer.** The whole block is about pickups. It never
   once mentions cancelling a *drop*, so a drop is not in the model's picture of
   what chaff can hit.
3. **Hour 1 is in the SAFE zone.** "Prefer pickup at hour ≤ 4" marks early
   hours as where chaff *doesn't* reach.

Put those together and "a H1 chaff accomplishes nothing" is the correct
conclusion *from that text*. The offensive block saying "fire at H1" is simply
outvoted.

## Suppressor 2 — the menu blurb

`emp_harvest_test/agency.py:1507`, the `_KIND_BLURB["emp"]` entry, ends:

> "…so take it when denial is worth more than the chain you drop for it, **and
> never over a CERTAIN pure/mass grab**."

That final clause is an unconditional instruction to defer to grabs, printed
directly above the weapon options. The recorded bake for that fork: the option
appeared in the prompt five times with full rationale and was refused 3/3 in
favour of a harvest chain. Menu *position* was not the problem — the weapon
block prints first. The blurb had pre-lost the argument.

**So `weapon_forge` omits that clause.** State the trade honestly, then let the
rationale argue it on the night. A group header is the wrong place to settle a
per-night judgement.

## The correction pattern

`weapon_forge.doctrine_for()` emits, per weapon held: the offensive block, then
a `CORRECTION` block. Order matters — the correction lands *after* the
`BEWARE_*` text in the assembled prompt, so recency favours it.

The chaff correction, which is the one that had to work:

```
CORRECTION TO THE CHAFF BLOCK ABOVE — YOU ARE HOLDING ONE.
That block is about SURVIVING a rival's chaff, and it describes chaff as
blind-fired at likely PICKUP hours. That is the defender's view and it
does not describe the weapon in your rack.
Fired deliberately, a chaff cancels a SPECIFIC hour you have read: any
action, not only a pickup. An early flare is not wasted — a drop is an
action, so an hour-1 chaff cancels an hour-1 DROP. The "safe hours" in
that block are safe for YOUR pickups; they are not hours where your own
chaff does nothing.
```

Note what it does: names the block it is answering, says *why* that block reads
the way it does, and corrects the three specific false inferences. It does not
say "ignore the above" — the defensive advice is still true when a rival fires
at us. Both are needed; only the scope was wrong.

## Why not just suppress the BEWARE_ block?

Tempting, and wrong. We can be chaffed too, and the pickup-spacing advice is
genuinely good. Deleting it trades one blind spot for another.

`_v7/strategies.py` is also the **frozen regression baseline** and a test pins
it, so it cannot be edited in place regardless. Correcting downstream is both
the safer and the more honest move.

## Check it

`check_wiring.py` asserts the correction is present:

```
PASS  doctrine CORRECTS the BEWARE_chaff framing
```

If that line FAILs, expect the weapon to sit in the rack no matter how good the
rationale is.

## The general rule

**Before adding doctrine for a weapon, read what the baseline already says
about that weapon from the receiving end.** All three `BEWARE_*` blocks are
written from the survivor's side — `soc weapons` says so itself when it reports
rung 4 unbuilt: *"doctrine covers surviving chaff, emp, snap, never using
them."* That sentence is describing a suppressor, not just a gap.
