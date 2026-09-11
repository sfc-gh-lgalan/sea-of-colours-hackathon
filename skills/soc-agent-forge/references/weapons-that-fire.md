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

Turn it on only when the follow-up is *defined by* the shot rather than merely
enabled by it. Two legitimate cases, both below:

* **EMP** — the walkable cells are "the smear minus our own blast", and only this
  play knows where our missiles landed. A separate grab would route a harvester
  into our own cloud, and friendly fire disables it hour by hour.
* **SNAP** — the cell is hot for one hour and hot for us too, so no separate option
  can help: a landing at H1 is refused. The two-beat timing *is* the play.

Chaff is never one of those cases: it takes no cell, so it has nothing to hand on.

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
    combines_with="blind_grab",
    why=("covering their smear at H1 locks them out of their own pure for "
         "eight hours; the probe outside the blast lights the walk so we "
         "can comb the exposed edge while they wait the cloud out"),
)
```

Observed: `H1 emp_launch at [[17,10],[20,10],[18,7]]` — one move, one charge, a
LIST of cells — then a probe just outside the cloud, then the drop and comb.

Three things that are easy to get wrong here:

* **A salvo is ONE move with a list of cells.** Not one move per missile, and not
  one charge per missile.
* **Friendly fire is on.** Walking into your own cloud disables the harvester hour
  by hour, so the comb must be the smear MINUS your own blast. `plan_comb` does
  this and returns a contiguous serpentine — a filtered *set* of walkable cells is
  not a *path*, and feeding one straight to `emit_chain` produced a walk that
  backtracked and ran to 27 moves against a cap of 21.
* **The probe goes beside the landing, never on it.** A drop onto your own probe
  crushes it.

`targets="redsign"` needs `scorch.py` in the fork. Without it the play degrades to
the pattern path and the error message will blame `when` instead.

---

## SNAP — refuse the landing on a contested pure, then take it

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
         "the hull, and because the cell goes cold after the hour we drop "
         "onto the same pure at hour two and take it ourselves"),
)
```

Observed on a board with four rival eyes on one pure:

```
H1  {"a": "snap_launch", "at": [31, 18]}
H2  {"a": "drop", "unit": "harvester_p1", "at": [31, 18]}
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

**The cell is hot for you too — for one hour only.** You cannot snap and land in
the same hour. Fire at H1 and the landing queued behind it arrives at H2 on ground
gone cold. That one-hour window is what makes snap better than chaff here: chaff
jams you until H4 and costs 300 blue, snap costs 100 and hands you the pure at H2.

More watchers is *better*, not worse. Killing one eye of four is one-in-many
denial, but snapping the GROUND does not care how many eyes are on it: the cell is
hot for everyone, so every landing into it is refused by the one charge.

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
