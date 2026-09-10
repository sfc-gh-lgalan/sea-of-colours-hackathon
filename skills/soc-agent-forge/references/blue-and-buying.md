# The blue economy — conditional advisory

**Two conditions before opening this with a team:**

1. The chosen weapon costs **≥200 blue** (EMP or chaff). SNAP at 100 is
   affordable within V12's existing behaviour — do not send a team here for a
   SNAP.
2. **The weapon already fires.** This is not optional ordering.

The second condition comes from the baseline's own README, and it is the opposite
of most teams' instinct:

> Buying more weapons without closing the firing half makes the agent **worse**,
> because BLUE spent on an unused rack is BLUE not spent elsewhere. The two halves
> have to move together.

So: get a fired weapon observed in a replay, *then* fund it. A team that tunes
economics first spends the morning making the agent worse in a way that is
invisible until the score comes in.

## Why V12 will not bank blue for you

BLUE funds the weapons economy, which makes this partly *why* the not-firing
problem stays unexploited. V12 will grab blue, but only through a narrow gate, and
**five separate mechanisms push in the same direction:**

| Lever | Where | Current setting |
| --- | --- | --- |
| Purity floor before blue is even offered | `value_pyramid.py:63` | `_BLUE_GRAB_MIN = 192` |
| Blue must not cost a harvester a strong red chain | `value_pyramid.py:354` | `_STRONG_CHAIN_RED_MIN = 150` |
| Blue only "requested" when the vault is short **and** ≥2 harvesters live | `prompt.py:412` | `blue_is_requested()` |
| Blue chain hints suppressed unless requested | `harness.py:521` | `want_blue` gate |
| Doctrine explicitly ranks blue below red | `_v7/strategies.py:87` | "RED always outranks blue for a scarce harvester" |

**Loosening one lever alone usually does nothing, because another still gates it.**
That is the interesting part of the problem, and it is the reason a team that
edits one number reports "my change did nothing".

## How blue is actually earned

Blue is fissile pockets. A harvester walking onto a blue tile auto-harvests it —
same grammar as red: `drop → step* → pickup`. Purity tiers: shallow (0–50), mid
(51–150), sink (151–254), and **pure blue at 255**. Starting bank is 250.

Note what `_BLUE_GRAB_MIN = 192` does: it makes shallow and mid blue *invisible*
to the option menu entirely. Only sink and pure are ever offered.

**Bluesign** is a radiative smear from every pocket. It is visible regardless of
fog and never fades — so unlike a redsign, you do not need a probe to see where
blue is. That makes bluesign-seeking unusually cheap: the information is free.

**"In echo"** means a probe that once covered the cell has since expired
(`freshness != "fresh"`). You know what is there; you just cannot currently see
it.

## The four predicates, all available from `agent_view`

Every condition a team is likely to want is expressible from the live view with no
new state:

```python
# "pure blue is sitting in echo"
echo_pure_blue = any(
    int(row.get("purity", 0)) >= 255 and str(row.get("freshness", "")) != "fresh"
    for row in (agent_view.get("blue_tiles") or [])
)

# "a bright bluesign is reachable" — always visible, no probe needed
has_bright_bluesign = any(
    any(float(c[2]) >= BRIGHT for c in cluster.get("cells", []) if len(c) >= 3)
    for cluster in (agent_view.get("blue_sign") or [])
)

# "nothing better to do" — no redsign to contest
no_redsign = not (agent_view.get("redsign") or [])

# "the rack is empty"
rack_empty = not any(
    int(v or 0) > 0
    for v in (agent_view.get("orbit") or {}).get("weapon_stock", {}).values()
)
```

## The gate worth building

The conservative, high-value version — divert a harvester to blue **only when
there is nothing better to do and you cannot fight**:

```
seek blue  IF  (rack below cap OR rack empty)
           AND blue on hand < the weapon's price
           AND no redsign to contest
           AND (pure blue in echo OR a bright bluesign is reachable)
```

That composition matters. Without `no_redsign` you will pull a harvester off a
contested pure to go fetch currency, which loses more than the weapon gains.
Without the price check you bank blue you have no use for.

## Where to edit

| Change | File | Lines |
| --- | --- | --- |
| Lower the purity floor | `value_pyramid.py` | 63 |
| Add the echo-pure-blue gate | `value_pyramid.py` | ~354, in `force_surface_grabs()` |
| Relax the request gate | `prompt.py` | 412–425 |
| Add a `want_bluesign` flag | `harness.py` | 521–525 |
| Doctrine addendum | `doctrine.py` | — |
| Packaging | — | **none.** Blue grabs already compile as chain moves |

## The buying side

`orbit_policy.py`, and its dials are the cheapest experiment in the kit:

```python
blue_always_build: int = 300     # decides whether the seat is ever armed
                                 # before night three
blue_emp_roll:     int = 250
emp_stockpile_cap: int = 2
chaff_stockpile_cap: int = 1
```

Two things to know. Weapons cost **blue**; harvesters cost **credits** (1500c) —
so they draw on different pools and do not directly compete. And the orbit reads
no board state at all, so it *cannot* condition a purchase on redsign presence or
vault grade. If a team wants "only buy when threatened", that is a change to what
orbit is allowed to see, which is a bigger job than a dial.

**Import prices from `game/weapons.py`; never hardcode them.** A literal in a fork
replicates into every agent descended from it and cannot be fixed centrally. The
reference fork shipped `emp_credit_cost = 0` against an engine charging 250 and
survived only because view prices normally win.

## Remember this is deterministic

Orbit is a heuristic — no LLM call. So all of the above is testable with `pytest`
in seconds, with no PAT. It is the right work to do while a season is running.

---

# The buy cadence — how often orbit buys which weapon

## What it does today

`orbit_policy.plan_orbit_actions()`. One condition, and an `if/elif`:

```python
elif weapons_enabled and blue_total > dials.blue_always_build:      # 300
    if chaff_stock < dials.chaff_stockpile_cap and _afford_chaff():
        actions.append({"a": "build_chaff", "count": 1})
    elif emp_stock < dials.emp_stockpile_cap and _afford_emp():
        actions.append({"a": "build_emp", "count": 1})
```

Three consequences worth telling a team before they choose:

1. **One weapon per orbit turn.** It is an `if/elif`, not two `if`s. A seat
   never buys a chaff and an EMP on the same day.
2. **Chaff always wins the tie.** It is tested first, so on any day both are
   affordable and both under cap, the chaff is what gets built.
3. **Between 250 and 300 blue an EMP build is a coin flip** —
   `blue_emp_roll = 250`, `emp_roll_chance = 0.5`.

## The dials

| Dial | Default | Gates |
| --- | --- | --- |
| `blue_always_build` | 300 | blue above which a build always happens |
| `blue_emp_roll` | 250 | blue above which an EMP build is *rolled* for |
| `emp_roll_chance` | 0.5 | probability that roll lands |
| `emp_stockpile_cap` | 2 | stop buying EMP at this many in stock |
| `chaff_stockpile_cap` | 1 | stop buying chaff at this many |
| `snap_stockpile_cap` | 2 | fork only |

Retuning these is the cheapest experiment in the kit, and
`blue_always_build` alone decides whether the seat is ever armed before night
three. But heed the module's own warning:

> That cap is also why V12 buys weapons and leaves them in the rack — **raising
> it without teaching the night phase to fire them makes the agent worse, not
> better.**

## The four policies to offer

**Always when affordable** — the default. Correct for a single weapon, and the
least code. Just raise or lower `blue_always_build`.

**Alternate** — round-robin so a two-weapon rack fills evenly. Convert the
`if/elif` into a rotation keyed on which weapon is furthest below its cap. This
is the fix for "we bought four chaff and no EMPs".

**Cheapest first** — bank a SNAP at 100 now rather than idle waiting for chaff
money. Sort the candidate builds by blue cost ascending and take the first
affordable. Best when a team wants *something* in the rack early.

**Board-conditional** — e.g. only buy chaff while a rival redsign is live.

## Board-conditional buying is available, and it is not plumbing

The buying code reads no board state today, and the docstring is explicit that
this is a deliberate gap rather than a limitation:

> **Nothing here reads the board.** Buying is a function of credits, BLUE and
> fleet state only. A policy of the form "buy an EMP when a redsign is live"
> needs the night-phase view, which is **on `view` and simply not consulted
> yet**.

So the data is already in the caller's hand. This is a parameter to thread, not
a system to build — which makes it a realistic afternoon change rather than a
stretch goal.

## Credits versus blue

Weapons cost **blue**. Harvesters cost **credits** (1500c). They draw on
different pools, so buying ordnance does not directly starve the fleet — the
real cost of an unused rack is the blue itself, which also scores.

**Import prices from `game/weapons.py`; never hardcode them.** A literal in a
fork replicates into every agent descended from it and cannot be fixed
centrally. The reference fork shipped `emp_credit_cost = 0` against an engine
charging 250 and survived only because view prices normally win.

## Remember this half is deterministic

Orbit is a heuristic — no LLM call, no PAT, no latency. Everything above is
testable with `pytest` in seconds. It is the right work to hand someone while a
season is running.
