# Weapons that fire — three verified recipes

Every play below has been observed on the wire: chosen by the model, compiled by
the packager, `fallback=False`, launching at the hour it declared. Copy the shape
rather than inventing one. A weapon play is easy to write and hard to get fired,
and the difference is almost never the prose.

**The pattern all three share.** A weapon competes against a harvest option whose
`yield:` line quotes a real number, so a play that cannot name a target and cannot
quote a figure loses — correctly. Each recipe below therefore does three things:
it aims at a cell the resolver *found* rather than guessed, it fires at H1 because
that is when a smash-and-grab lands, and it carries a denial figure on the same
scale as the alternative.

## A weapon play buys an HOUR. It should rarely buy anything else.

`take_the_ground` defaults to **False** and most plays should leave it there. A
denial-only play compiles to the launch and stops, which leaves the harvester free
for the grab option the thinker picked alongside it — and that option's geometry is
better than yours, because computing landings and combs is the entire job of
`seam_control`.

Getting this wrong does not look like a crash. It looks like this:

```
[plan=aggressive: CANCEL_DROP, BLIND_AND_GRAB, PR3, PR1]
{"a": "chaff_flare"} {"a": "wait"} {"a": "wait"}
{"a": "probe", ...} {"a": "probe", ...}
FAIL comb_gradient: no harvester walked, so nothing combed the beacon
```

The flare fired, both probes went out, and **the harvester never deployed at all** —
the weapon play and the grab each half-owned the follow-up and neither ran it. With
`take_the_ground=False` the same board gives the flare, the waits, and then the
grab landing on the beacon centre: 60% → 80% of checks.

Turn it on for **exactly one** case: an **EMP**, where the walkable cells are "the
smear minus our own blast", and only this play knows where our missiles landed. A
separate grab would route a harvester into our own cloud, and friendly fire disables
it hour by hour, so that comb has to be self-contained.

**A snap never takes its own ground.** The cell is hot for one hour — hot for *us*
too — so you physically cannot land on your own snap at H1; the earliest a harvester
touches it is H2 when it has gone cold. That means the grab is a *separate* play the
thinker picks alongside the snap (name it in `combines_with`), landing at H2 by
construction. Carrying your own harvester on the snap double-books the cell against
that paired grab and evicts the richer ring grab from the two-harvester budget. Fire
the snap denial-only and let the pairing land the take.

Chaff is never one of those cases either: it takes no cell, so it has nothing to hand
on.

---

## CHAFF — cancel their hour one

```python
WeaponPlay(
    play_id="CANCEL_DROP",
    weapon="chaff",
    when="redsign_theirs",
    hour="super_early",
    combines_with="blind_grab",
    why=("a rival that has just lit a pure will drop on it at hour one; "
         "cancelling that hour kills their smash-and-grab and leaves the "
         "pure sitting there for our follow-on grab"),
)
```

Observed: `H1 chaff_flare`, then `wait`, `wait` (the self-jam), then the walk-in.

**Why this is the strongest line in the game against a stock rival.** A stock V12's
move enum is `['drop','step','pickup','probe']` — it has no weapon verb of any
kind, so it *cannot answer ordnance*. Its scripted reply to a rival redsign is
`BLIND_GRAB` at H1: a covering probe on the finder, then a blind drop. Chaff
cancels exactly that. Chaff at H1, then blind-grab and comb from H4 when your own
jam lifts.

Chaff is the one weapon with no target cell — it is aimed at an HOUR. So its
`when` has to do the work: a rival's redsign, and nothing else.

**Do not add a quiet-night chaff play.** `when="no_redsign"` was tried and it is a
bad move: nothing is lit, so there is nothing concrete to deny, no target to name
and no number to quote. Measured at 10 offers, 0 choices in one season, while
crowding the play that mattered.

---

## EMP — take the smear and comb what you did not darken

```python
WeaponPlay(
    play_id="LIGHTS_DOWN",
    weapon="emp",
    when="redsign_theirs",
    hour="super_early",
    targets="redsign",
    probe_the_comb=True,
    take_the_ground=True,
    combines_with="blind_grab",
    why=("covering their smear at H1 locks them out of their own pure for "
         "eight hours; then we comb the ground they cannot reach — the "
         "exposed edge now, or the interior once our own cloud clears"),
)
```

Observed: `H1 emp_launch at [[17,10],[20,10],[18,7]]` — one move, one charge, a
LIST of cells — then a probe just outside the cloud, then the drop and comb.

**The comb adapts to the smear, and this is the part that was wrong before.** The
cloud darkens ~13 cells and friendly fire is on, so the harvester cannot walk into
it. Two cases:

* **Big smear** — enough cells sit OUTSIDE the blast to comb ≥4 of them. Comb that
  exposed edge now, while the cloud still locks the rival out of the middle.
* **Small smear** — the blast covers most of it, so there is no worthwhile edge.
  The play then WAITS the eight hours out and combs the INTERIOR at H9, the first
  cool hour. Ground nobody else could enter while it burned is yours unopposed.
  The packer inserts the `wait` moves automatically (`wait_before_drop`), so the
  wire shows `emp_launch` at H1, seven `wait`s, then `drop` at H9.

The switch is automatic — `_MIN_EXPOSED_EDGE = 4` in `weapon_forge.py` is the
threshold. You do not choose it; the geometry does.

Three things that are still easy to get wrong:

* **A salvo is ONE move with a list of cells.** Not one move per missile, and not
  one charge per missile.
* **Friendly fire is on.** The safe-edge comb is the smear MINUS your own blast;
  the interior comb only runs after the cloud clears. `plan_comb` builds a
  contiguous serpentine — a filtered *set* of walkable cells is not a *path*.
* **The probe goes beside the landing, never on it.** A drop onto your own probe
  crushes it. (On the delayed interior comb there is no probe — every cell is
  inside the blast, so there is nowhere legal to place one; the drop lands cold at
  H9 regardless.)

`targets="redsign"` needs `scorch.py` in the fork. Without it the play degrades to
the pattern path and the error message will blame `when` instead.

`take_the_ground=True` is the ONLY legitimate use of that flag: the walkable
cells are defined BY our own blast, so no separate grab option could compute them.

---

## SNAP — two plays, split on whether you can see the pure

A SNAP guards one cell for one hour, so it only pays when you know where the rival
will be. There are exactly two readable cases, and the agent carries one play for
each:

* **You can see the pure** (both you and a rival have vision on it) → snap the
  square itself: `PURE_TRAP`.
* **You cannot see the pure**, but a rival lit one → snap the eye that found it:
  `BLIND_THE_FINDER`.

### PURE_TRAP — the pure is visible to both

```python
WeaponPlay(
    play_id="PURE_TRAP",
    weapon="snap",
    when="always",             # the target resolver is the real gate
    hour="super_early",        # H1 — the hour a smash-and-grab lands
    targets="contested_pure",
    min_targets=1,
    combines_with="smash_grab",
    why=("a pure we can see that a rival probe also watches is the one cell "
         "on the board whose occupation is predictable — they will smash-and-"
         "grab it at hour one; snapping it refuses that landing and damages "
         "the hull, then a smash-grab lands on the cold pure at hour two and "
         "banks it — pick ONE grab on that cell, not two"),
)
```

No `take_the_ground`. This play fires **denial-only** and names its pairing in
`combines_with`. The snap owns the cell at H1, so the paired `smash_grab` lands at H2
on its own — carrying a harvester on the snap would double-book the cell against that
grab.

Observed on a board with four rival eyes on one pure:

```
H1  {"a": "snap_launch", "at": [31, 18]}
H2  {"a": "drop", "unit": "harvester_p1", "at": [31, 18]}   # the PAIRED grab, not this play
H3  {"a": "pickup", "unit": "harvester_p1"}
```

`PASS take_pure` · `PASS pure_first`.

**Work backwards from the ground, not from their units.** A snap makes one cell
hot for one hour, so it only pays if you know where they will be. You cannot
predict a step, a probe or a pickup. You *can* predict a pure: it is the one
square worth a smash-and-grab, so that is where they land. Aiming a snap anywhere
else is a guess; aiming it at a contested pure is a read.

So the search is: every pure in **your** live vision, then which of those a rival
eye also covers. An enemy probe within vision range of a pure you hold means they
have the read, whether or not anyone lit a beacon — which is why `when="always"`
is right here and the resolver does the gating.

**You can see a pure.** The seat view strips `pure_cells`, but a pure is a
`red_tiles` row at `purity >= 255` and those arrive wherever you have live vision.

**You cannot land on your own snap.** The cell is hot for you too, for one hour, so
a landing at H1 is refused. Fire the snap denial-only at H1 and pair exactly ONE grab
(`SMASH_GRAB` / `GRAB1`): that grab lands at H2 on ground gone cold and banks the pure
for 100 blue. Do not stack a second grab on the cell — the later drop hits stripped
green. That one-hour window is what makes snap better than chaff here: chaff jams you
until H4 and costs 300 blue, snap costs 100 and hands the pairing the pure at H2.

More watchers is *better*, not worse. Killing one eye of four is one-in-many
denial, but snapping the GROUND does not care how many eyes are on it: the cell is
hot for everyone, so every landing into it is refused by the one charge.

### BLIND_THE_FINDER — the pure is lit but you cannot see it

```python
WeaponPlay(
    play_id="BLIND_THE_FINDER",
    weapon="snap",
    when="redsign_theirs",
    hour="super_early",
    targets="finder_probe",
    min_targets=1,
    combines_with="blind_grab",
    why=("when a rival has lit a pure we cannot see, the eye that found it is "
         "the only target we can name; snapping it at hour one refuses their "
         "drop for lack of live vision, then a paired blind-grab combs the "
         "smear they can no longer reach"),
)
```

No `take_the_ground` and no `probe_the_comb`. Like `PURE_TRAP`, this snap fires
denial-only; the paired `blind_grab` carries the probe that relights the seam and
combs the smear.

Observed on a single-eye board: `aim` = the finder probe. The sequence is snap the
eye at H1 → the paired blind-grab's probe relights the seam → its harvester combs the
smear the rival can no longer reach.

**Why the eye and not the ground here.** A snap resolves ABOVE the hour-start
vision snapshot (§3.9.7), so killing the sole finder refuses the rival's drop for
lack of live vision THAT night — the same effect a 300-blue chaff buys, for 100.
`finder_gate` only fires this when it is worth it: one covering eye is STRONG; two
is worth it only if we can also see the pure; three or more is one-in-many denial
and it holds the charge (that is the WEAK refusal you will see in the notes).

The smear comb is the **paired blind-grab's** job, not the snap's. The snap is one
cell for one hour, so from H2 the whole smear is cool and the grab's `plan_comb`
walks it with nothing to avoid.

**The two SNAP plays do not collide.** `PURE_TRAP` needs a pure in `red_tiles` at
purity 255 (visible to us); `BLIND_THE_FINDER` needs a rival redsign whose pure we
cannot see. On a board where both hold, the model picks between them — snapping the
square is stronger when available, snapping the eye is the fallback.

---

## The four things that stopped all of them

Fix these in any fork that predates them; a green `check_wiring` does not prove
they are fixed, because a synthetic fixture can feed a channel a real board does
not.

1. **`targets` modes must be in the dispatch tuple.** `finder_probe` was handled
   *inside* the `if p.targets in (...)` block but missing from the tuple, so it
   never entered: the play fell through to pattern geometry and looked healthy for
   weeks. `validate()` now rejects an unknown mode, and `TARGETS_CHOICES` is the
   list to extend.
2. **Rival probes do not live under `enemy_probes` or `rival_probes`.** Neither key
   exists. Use `weapon_forge.rival_eyes()`, which wraps the harness's own
   `_v7.probe_hints._enemy_probe_cells` and reads all four public channels. See
   `diagnosis.md`.
3. **Pack order is a legality question.** Position in `pk.moves` IS the hour, so an
   hour-locked launch compiled behind a juice chain is cut outright:
   `cut PLAY: wanted hour 1 and 4 move(s) are already queued`. That is the whole
   of the chosen-then-lost class — the card shows the play in `[plan=…]` and no
   launch reaches the wire. `order_for_weapon_hours` floats guns to the front
   without dropping or resequencing anything else.
4. **The denial figure has to be real.** `denial_yield_line` reads `red_pts` from
   `yield_breakdown` — not `red`, `value` or `total`, none of which exist. With the
   wrong key it scored 0 every time and every weapon fell through to a wordy
   no-number fallback, then lost the menu to a chain quoting `~+765`. Weighted the
   same way `packager._harvest_value` does, so the number is comparable.

## Testing them in the lab

The eval stack could not arm a snap at all until recently: `Loadout` had no `snap`
field, `give_weapon_stock` took only emp and chaff, and `stage.py` passed neither.
A snap agent ran every rung with an empty rack.

Rival probes were also placed but invisible — `place_probe(owner="p2")` sets the
entity position, and our seat learns about rival probes *only* through the public
channels. `stage.py` now calls `enemy_probe_launched` alongside, honouring §3.15.

```bash
# chaff and emp: a rival seam
python scripts/soc.py why blind_grab_rival_seam <label> --loadout chaff
python scripts/soc.py why blind_grab_rival_seam <label> --loadout emp

# snap: a visible pure with rival eyes on it. --rung siege puts four there.
python scripts/soc.py why two_pures_poker <label> --loadout snap --rung siege
```

Read the move list, not the summary. The launch must be the FIRST move and its
`at` must be the cell the resolver found.
