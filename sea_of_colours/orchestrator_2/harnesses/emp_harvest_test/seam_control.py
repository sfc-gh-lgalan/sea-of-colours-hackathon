"""v10 Phase 2 — redsign seam-control PATTERN generator.

This is the heart of "agency-first" redsign poker. Instead of the harness
hard-coding ONE offset drop (Fix A) and hoping the agent copies it, we emit a
MENU of named, fully-resolved tactical PATTERNS around each live redsign. Each
pattern is a multi-wave campaign with pre-filled geometry (probe cells, drop
cells, comb walks), an hour cadence, and a "when to pick me" note. The THINKER
selects and orders pattern IDs by reasoning over ownership / players / weapons;
the resolver expands the chosen IDs back to this concrete geometry for the
mover. Geometry stays deterministic (reliable transcription); strategy moves
into the agent (taught by doctrine, not enforced here).

The two cases (from the user's poker book, weapon-free baseline):

  ``H<n>`` throughout means HOUR n of the night, never "harvester n" (R2.7).
  Units are named by ordinal — "the 2nd harvester" — because the two used to
  share a notation and a wave labelled "H2" actually opened at hour 9.

  CASE 1 — the redsign is MINE (I discovered it, engine-truth ``mine=True``):
    * ``SMASH_GRAB``     1st harvester, H01 — belly-flop ON the pure
                         (auto-harvest), pick up fast — the SURE play; I know
                         where the pure is, secure it.
    * ``FULL_SWEEP``     1st harvester, H01 — ALTERNATIVE: ride the WHOLE visible
                         seam in one outing (pures + mass): far more value, but a
                         one-harvester GAMBLE (a mid-walk collision/jam zeroes
                         unlifted cargo; risk rises with more players + later
                         season). The thinker weighs value vs risk vs SMASH_GRAB.
    * ``SECURE_MASS``    2nd harvester, H09 — a SHORT strip of the visible mass
                         ring from the far side (or re-hit the pure under
                         weapons — agent's call).
    * ``LATE_SWEEP``     3rd harvester, H12 — a late, bigger pattern over the
                         dense seam.
    Each is one wave = one harvester, so the thinker composes the fleet (and in
    the DUAL case can send later harvesters at a rival beacon instead).

  CASE 2 — NOT my redsign (a rival found it; ``mine`` False/unknown):
    * ``BLIND_GRAB``     supersede the finder's probe, then a short blind drop
                         with pickup ~H4-5 — imprecise smash-and-grab.
    * ``UNBEATEN_FLANK`` a fresh probe on the OPPOSITE axis to the contested
                         approach, longer chain post-EMP (H8-10) — the secured
                         bank away from the pile-up.
    * ``WALK_IN``        outer mop-up: walk in from slightly outside on a third
                         angle late (H11-16); pure red bleeds into good red.

  CASE 2 also has a PROBE-FREE strand for the rare rival seam that broadcasts
  within a few steps of ground we already light (the drop needs live coverage,
  the steps do not):
    * ``WALK_TO_CONTEST``     short reach, then comb the smear.
    * ``WALK_TO_CONTEST_FAR`` a second unit on a different bearing, spending the
                              hold on the crossing itself.

Cadence is nudged by observed weapons (defensive read only, per plan scope):
chaff seen -> keep a backup wave; EMP scar near the seam -> space the waves past
the cloud window and flank the probe OUTSIDE the blast radius.

Patterns are EXEMPT from the nomadic separation filter (they intentionally
cluster on the seam — that is the whole point of a redsign campaign).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test.comb_shapes import (
    comb_path as _comb_path,
)
from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test.hazard_memory import (
    view_green_cells as _view_green_cells,
)
from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test._v7.probe_hints import (
    _covers,
    _enemy_probe_cells,
    _grid_dims,
    _known_green_cells as _live_green_cells,
    _redsign_cells,
    _tier_name,
    _vision_disk,
    _visible_red,
    _PROBE_RADIUS,
    _TIER_MULT,
)
from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test._v7.validators import (
    _live_vision_cells,
)
# v13 — the EMP compound patterns share their geometry with the standalone
# scorch options (shaped salvo, probe/blast diamonds, rack introspection).
from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test import scorch

try:  # keep geometry honest against the real engine dials when available
    from sea_of_colours.game.weapons import (
        EMP_RADIUS as _EMP_RADIUS,
        EMP_CLOUD_HOURS as _EMP_CLOUD_HOURS,
    )
except Exception:  # pragma: no cover - defensive fallback
    _EMP_RADIUS, _EMP_CLOUD_HOURS = 4, 6

def _known_green_cells(agent_view: Mapping[str, Any]) -> Set[Tuple[int, int]]:
    """Ground a seam route must not touch: live green PLUS rival-stripped cells.

    OBS-43 was fixed in :func:`hazard_memory.view_green_cells`, whose docstring
    promises the trail cells reach "the seam geometry, the value pyramid, the
    packager's forbidden set and the card's own AVOID list". Three of those four
    were true. Seam geometry imported the v7 reader, which only sees cells
    reading GREEN in `world.live` right now, so a cell a rival harvester walked
    last night — stripped, and worth -100 to re-walk — stayed invisible to every
    drop and comb decision on this module.

    `SNAP_ac1c55bf_d6_p4` is the standing example: the card printed
    `enemy_harvester_trail at=[17,7] [17,8]` and `SECURE_MASS` routed straight
    over (17,7) anyway, while the echo block still advertised it at purity 158.

    Unioned rather than swapped. The two readers walk different fields and the
    v7 one is not provably a subset, so taking both can only ever GROW the set
    of ground we refuse — which is the safe direction to be wrong in.
    """
    return set(_live_green_cells(agent_view)) | set(_view_green_cells(agent_view))


# Chebyshev radius within which a redsign hot-drop / scar is attributed to a
# given beacon (the probe vision half-width).
_REGION_MATCH_RADIUS = 4

# Baseline cadence (hours) — the "poker" timing. Later waves clear the EMP
# window; chaff/EMP reads shift these at build time.
_H_SMASH = 1
_H_BLIND_PICKUP = 4
_H_POST_EMP = 9
_H_WALK_IN = 12

# I4/I6 (secure-the-pure short-grab): wave 1 LANDS on the richest reachable core
# cell (the drop cell is auto-harvested — F7) and takes only a short comb before
# the mover picks up, so the pure banks early instead of riding an exposed
# 6-cell chain. Later waves work the wider seam.
_SHORT_GRAB_STEPS = 2

# SMASH&GRAB+VALUE (fix 1.2): the pure PLUS at most two adjacent squares, and
# only when those squares are themselves MASS or PURE. Two is the whole point —
# a third step is a seam crawl wearing a tempo move's name, and it is the step
# that turns "in and out before anyone lands" into "still walking at H4".
_VALUE_TAIL_STEPS = 2

# FULL_SWEEP (CASE-1 alternative to SMASH_GRAB): one harvester rides the WHOLE
# visible seam (pures + mass ring) in a single outing — the greedy, high-value
# gamble the thinker weighs against the sure single-cell smash. Capped at the
# engine hold (drop + 5 steps = 6 cells); trimmed under weapons.
_SEAM_SWEEP_STEPS = 5

# SECURE_MASS (CASE-1 H2): a SHORT strip of the visible MASS ring around the
# pure — a secured second bank, not a seam crawl. Trimmed to the floor under
# danger. LATE_SWEEP (H3) is the only own-seam wave allowed a full-length comb.
_MASS_SHORT_STEPS = 3

# BLIND grab sweep (CASE 2, rival beacon we have NOT un-fogged): the broadcast is
# a JITTERED smear, so the single best-guess drop cell is rarely the exact pure.
# A redsign seam is MASS-RICH and — crucially — almost always exactly ONE night
# old (a frontier probe trips it, and the discoverer usually cannot harvest it
# until the NEXT day), so the pure is very likely still live and even a blind
# drop that MISSES the pure still banks good vein/mass. So a blind grab combs a
# FULL blind walk (up to the 6-parcel hold) across the smear to maximise the hit
# chance on a fresh, rich seam. Danger (chaff / EMP) trims it to the floor —
# never to zero (a 0-step blind grab is a wasted, exposed unit).
_BLIND_SWEEP_STEPS = 5
_BLIND_SWEEP_FLOOR = 2

# A3 — chaff-window pickup timing. Under weapons (a chaff jam last night or an
# EMP scar near the seam), even the "secured" later waves (UNBEATEN_FLANK /
# WALK_IN / LATE_SWEEP) keep their chain SHORT so the harvester banks and lifts
# INSIDE the safe window instead of riding a long serpentine into the jam and
# losing the hold at dawn. Floor of 2 keeps it a real bank, not a bare drop.
_CHAFF_CHAIN_STEPS = 2

_DIRS: Dict[str, Tuple[int, int]] = {
    "N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0),
    "NE": (1, -1), "NW": (-1, -1), "SE": (1, 1), "SW": (-1, 1),
}
_OPPOSITE = {
    "N": "S", "S": "N", "E": "W", "W": "E",
    "NE": "SW", "SW": "NE", "NW": "SE", "SE": "NW",
}
# A perpendicular "third angle" for WALK_IN, so all three CASE-2 waves attack
# from genuinely different bearings.
_PERP = {
    "N": "E", "S": "W", "E": "S", "W": "N",
    "NE": "SE", "SW": "NW", "NW": "NE", "SE": "SW",
}


def _value_tail(
    agent_view: Mapping[str, Any],
    start: Tuple[int, int],
    bad: Set[Tuple[int, int]],
    *,
    max_steps: int = _VALUE_TAIL_STEPS,
) -> List[List[int]]:
    """Up to ``max_steps`` adjacent steps that land ONLY on live MASS/PURE.

    Fix 1.2 — the tail of a SMASH&GRAB+VALUE. The ordinary comb
    (:func:`_comb_path`) heads for the value cluster but will happily spend a
    step on trace to get there; on a contested seam that is exposure bought for
    nothing, because trace is everywhere and worth almost nothing. This walk
    takes a step ONLY when the next cell is itself mass or pure, and stops the
    moment that stops being true — so the option is honestly "the pure plus the
    one or two big cells touching it", never a crawl.

    Steps are not gated on the probe disk: only the DROP needs live coverage,
    and every cell here is one we can already see (that is how we know it is
    mass).
    """
    live = _live_red(agent_view)
    visited = {tuple(start)}
    path: List[List[int]] = []
    cur = tuple(start)
    for _ in range(max_steps):
        best: Optional[Tuple[int, Tuple[int, int]]] = None
        for dx, dy in _NSEW_ORDER:
            n = (cur[0] + dx, cur[1] + dy)
            if n in visited or n in bad:
                continue
            p = int(live.get(n, 0))
            if _tier_name(p) not in ("mass", "pure"):
                continue
            if best is None or p > best[0]:
                best = (p, n)
        if best is None:
            break
        path.append([best[1][0], best[1][1]])
        visited.add(best[1])
        cur = best[1]
    return path


def _grab_steps(threat: Mapping[str, Any], *, contested: bool,
                emp: Optional[Tuple[int, int]]) -> int:
    """Danger-gated wave-1 length for a SMASH/BLIND grab.

    The grab is the DROP (auto-harvest); the walk is optional. Under any danger
    — chaff seen, an EMP scar near the seam, or a contested beacon — take ZERO
    extra steps and pick up immediately (secure the jackpot before it can be
    spilled). Only when the board is clearly quiet do we take the short 1-2 step
    tail. This is NOT a seam strip; the wider seam is a later wave / 2nd unit.
    """
    if threat.get("chaff_seen") or emp is not None or contested:
        return 0
    return _SHORT_GRAB_STEPS


def _blind_grab_steps(threat: Mapping[str, Any], *,
                      emp: Optional[Tuple[int, int]]) -> int:
    """Sweep length for a BLIND grab on a rival beacon we cannot see.

    Unlike a SMASH_GRAB (we KNOW the pure -> land straight on it, 0-step is fine),
    a blind grab's drop cell is a GUESS off the jittered smear. A redsign seam is
    MASS-RICH and usually only one night old (still unharvested), so a fuller
    blind walk across the smear both raises the chance of nailing the pure AND
    banks the surrounding vein/mass on a miss — worth the exposure on a fresh
    seam. So comb the full hold when quiet; danger (chaff / EMP) trims to the
    FLOOR but never to zero (a 0-step blind grab is a wasted, exposed unit).
    """
    if threat.get("chaff_seen") or emp is not None:
        return _BLIND_SWEEP_FLOOR
    return _BLIND_SWEEP_STEPS


# R3 anti-crowd: mirror seats compute the SAME finder-triangulated blind drop,
# so v10-vs-v10-vs-v10 dogpiles the exact cell (mirror d2/d6 mutual-kill). Seat 0
# keeps the triangulated primary; later seats shift both drop AND enabler probe
# by a distinct bearing (same delta -> the probe disk still covers the drop).
_SEAT_RING: List[Tuple[int, int]] = [
    (0, 0), (1, 0), (0, 1), (-1, 0), (0, -1),
    (1, 1), (-1, -1), (1, -1), (-1, 1),
]


def _seat_offset_geom(
    drop: Tuple[int, int],
    probe: Tuple[int, int],
    seat_index: int,
    width: int,
    height: int,
    green: "set",
) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    """Fan a CASE-2 blind grab by seat so mirror seats don't stack one cell.

    Applies the SAME bearing delta to ``drop`` and ``probe`` (coverage preserved),
    clamped in-bounds, skipping the shift if the drop would land on known green.
    Seat 0 (or any offset that resolves to no move) returns the inputs unchanged.
    """
    if seat_index <= 0:
        return drop, probe
    dx, dy = _SEAT_RING[seat_index % len(_SEAT_RING)]
    if (dx, dy) == (0, 0):
        return drop, probe
    ndrop = (_clamp(drop[0] + dx, 0, width - 1), _clamp(drop[1] + dy, 0, height - 1))
    if ndrop in green or ndrop == drop:
        return drop, probe
    nprobe = (
        _clamp(probe[0] + dx, 0, width - 1),
        _clamp(probe[1] + dy, 0, height - 1),
    )
    return ndrop, nprobe


def _sign(v: float) -> int:
    return (v > 0) - (v < 0)


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _dir_name(frm: Tuple[int, int], to: Tuple[int, int]) -> str:
    """8-way compass bearing from ``frm`` toward ``to`` (defaults to E)."""
    dx, dy = _sign(to[0] - frm[0]), _sign(to[1] - frm[1])
    for name, vec in _DIRS.items():
        if vec == (dx, dy):
            return name
    return "E"


@dataclass
class SeamWave:
    """One resolved wave of a seam pattern — ready for the mover to package."""

    wave: int
    earliest_hour: int
    drop_at: Tuple[int, int]
    comb_path: List[Tuple[int, int]] = field(default_factory=list)
    probe_at: Optional[Tuple[int, int]] = None
    supersede: Optional[Tuple[int, int]] = None
    direction: str = ""
    unit_ordinal: int = 0
    note: str = ""
    pickup_after: bool = False
    # Part B — a DENY-ONLY wave spends its probe/supersede but drops NO harvester
    # (the confirm-and-deny play on a FOGGED rival seam: blind the finder + light
    # the seam for a real strike tomorrow, never a blind harvester dive onto a
    # cell the rival may already have stripped to green — the seed-56 collapse).
    deny_only: bool = False
    # Part A2 — a CONTESTED wave (the rival blind grab) may only sweep across
    # cells confirmed live-red at plan time; the packager truncates the walk at
    # the first fogged neighbour rather than blind-step onto possible green.
    contested: bool = False
    # v11 CASE-2 attack — a BLIND WALK deliberately combs a FOGGED rival seam
    # (no live vision). Unlike ``contested`` (live-red only), a blind walk may
    # step onto FOG cells — it only refuses KNOWN stripped/green (hazard memory).
    # This is the accepted-risk attack on a fresh, mass-rich rival redsign.
    blind_walk: bool = False
    # v13 EMP-doctrine — a wave may FIRE an EMP salvo instead of / in addition
    # to spending a probe. Three shapes:
    #   * emp_launch_at set, emp_only=True  -> pure salvo wave (no harvester).
    #   * emp_launch_at set, emp_only=False -> salvo THEN drop the same wave
    #     (case C: drop lands on emp_hole after salvo lands the missiles).
    #   * emp_hole set                      -> the salvo is aimed to leave this
    #     cell OUTSIDE any blast (case C's "hole is the pure").
    # The packager compiles these in _pack_seam via _Packer.spend_emp().
    emp_launch_at: Optional[List[Tuple[int, int]]] = None
    emp_hole: Optional[Tuple[int, int]] = None
    emp_only: bool = False
    # A defer key — when set, the wave's drop+comb is held until the salvo's
    # cloud clears. Used by case A / case B / case C to spool the follow-up
    # harvester behind the previous wave's EMP without hand-sequencing hours.
    defer_until_clear: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "wave": self.wave,
            "earliest_hour": self.earliest_hour,
            "drop_at": [int(self.drop_at[0]), int(self.drop_at[1])],
            "comb_path": [[int(x), int(y)] for x, y in self.comb_path],
            "pickup_after": bool(self.pickup_after),
            "deny_only": bool(self.deny_only),
            "contested": bool(self.contested),
            "blind_walk": bool(self.blind_walk),
            "probe_at": (
                [int(self.probe_at[0]), int(self.probe_at[1])]
                if self.probe_at is not None else None
            ),
            "supersede": (
                [int(self.supersede[0]), int(self.supersede[1])]
                if self.supersede is not None else None
            ),
            "direction": self.direction,
            "unit_ordinal": self.unit_ordinal,
            "note": self.note,
            "emp_launch_at": (
                [[int(c[0]), int(c[1])] for c in self.emp_launch_at]
                if self.emp_launch_at is not None else None
            ),
            "emp_hole": (
                [int(self.emp_hole[0]), int(self.emp_hole[1])]
                if self.emp_hole is not None else None
            ),
            "emp_only": bool(self.emp_only),
            "defer_until_clear": bool(self.defer_until_clear),
        }


@dataclass
class SeamPattern:
    """A named, multi-wave redsign campaign the thinker can select by ID."""

    pattern_id: str
    kind: str
    beacon: Tuple[int, int]
    mine: Optional[bool]
    title: str
    when: str
    rationale: str
    waves: List[SeamWave] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "kind": self.kind,
            "beacon": [int(self.beacon[0]), int(self.beacon[1])],
            "mine": self.mine,
            "title": self.title,
            "when": self.when,
            "rationale": self.rationale,
            "waves": [w.to_dict() for w in self.waves],
        }

    def menu_line(self) -> str:
        """One compact line for the option menu the thinker reads."""
        bx, by = self.beacon
        return (
            f"  [{self.pattern_id}] {self.title} @({bx},{by}) — {self.when}"
        )

    def execute_block(self) -> str:
        """A verbatim EXECUTE recipe for the mover once this pattern is chosen."""
        bx, by = self.beacon
        lines = [f"{self.pattern_id} — {self.title} (redsign @({bx},{by})):"]
        for w in self.waves:
            head = f"  wave {w.wave} @H{w.earliest_hour}+"
            bits: List[str] = []
            if w.supersede is not None:
                bits.append(
                    f"SUPERSEDE probe at ({w.supersede[0]},{w.supersede[1]})"
                )
            if w.probe_at is not None:
                bits.append(f"probe ({w.probe_at[0]},{w.probe_at[1]})")
            if w.deny_only:
                # Part B — a confirm-and-deny wave commits NO harvester: it only
                # blinds the finder + lights the seam for tomorrow.
                bits.append("NO DROP — confirm the seam, strike it tomorrow")
                note = f"  [{w.note}]" if w.note else ""
                lines.append(f"{head} {'; '.join(bits)}{note}")
                continue
            bits.append(f"drop ({w.drop_at[0]},{w.drop_at[1]})")
            if w.comb_path:
                walk = " ".join(f"({x},{y})" for x, y in w.comb_path)
                bits.append(f"walk {walk}")
            elif w.pickup_after:
                bits.append("walk NONE")
            if w.pickup_after:
                bits.append("PICK UP NOW (fast grab — do not linger)")
            note = f"  [{w.note}]" if w.note else ""
            lines.append(f"{head} {'; '.join(bits)}{note}")
        return "\n".join(lines)


# ── redsign / weapon context ───────────────────────────────────────────
def _redsign_regions(agent_view: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in (agent_view.get("redsign") or []):
        if not isinstance(r, Mapping):
            continue
        c = r.get("center")
        if isinstance(c, (list, tuple)) and len(c) == 2:
            try:
                out.append({
                    "center": (int(round(float(c[0]))), int(round(float(c[1])))),
                    "mine": bool(r.get("mine")) if "mine" in r else None,
                })
            except (TypeError, ValueError):
                continue
    return out


def _threat_context(agent_view: Mapping[str, Any]) -> Dict[str, Any]:
    """Coarse, defensive-only weapon read from the last-night recap.

    ``emp_cells``  — live EMP scar centres (route/flank outside their blast).
    ``chaff_seen`` — a chaff jam hit us last night (keep a backup wave).
    """
    recap = agent_view.get("last_night") or {}
    emp_cells: List[Tuple[int, int]] = []
    for scar in (recap.get("emp_scars") or []):
        at = scar.get("at") if isinstance(scar, Mapping) else None
        if isinstance(at, (list, tuple)) and len(at) == 2:
            try:
                emp_cells.append((int(at[0]), int(at[1])))
            except (TypeError, ValueError):
                continue
    chaff_seen = False
    for atk in (recap.get("incoming_attacks") or []):
        if isinstance(atk, Mapping) and "chaff" in str(atk.get("type") or "").lower():
            chaff_seen = True
    # v12 fix 3.4 (OBS-44) — chaff is the ONLY thing that can deny an H1 landing,
    # so the repeat-the-pure play is gated on it. "It jammed US last night" is too
    # narrow a test: a flare fired at another seat proves the stock exists just as
    # well, and the launch is public (§5.1). Kept as a SEPARATE key so the cadence
    # rules already keyed on ``chaff_seen`` do not silently widen with it.
    chaff_in_play = chaff_seen
    for ev in (agent_view.get("combat_events") or []):
        if isinstance(ev, Mapping) and str(ev.get("type") or "") == "chaff":
            chaff_in_play = True
    return {
        "emp_cells": emp_cells,
        "chaff_seen": chaff_seen,
        "chaff_in_play": chaff_in_play,
    }


def _emp_near(beacon: Tuple[int, int], emp_cells: Sequence[Tuple[int, int]]) -> Optional[Tuple[int, int]]:
    """The closest EMP scar within blast+match range of the beacon, if any."""
    best: Optional[Tuple[float, Tuple[int, int]]] = None
    for c in emp_cells:
        d = max(abs(c[0] - beacon[0]), abs(c[1] - beacon[1]))
        if d <= _EMP_RADIUS + _REGION_MATCH_RADIUS and (best is None or d < best[0]):
            best = (d, c)
    return None if best is None else best[1]


# ── geometry ───────────────────────────────────────────────────────────
def enumerate_value_ring(
    agent_view: Mapping[str, Any],
    beacon: Tuple[int, int],
    probe_at: Tuple[int, int],
    *,
    max_cells: int = 8,
) -> List[Dict[str, Any]]:
    """Drop-legal value cells inside ``probe_at``'s disk, richest-first.

    value = (probe Euclidean disk) INTERSECT (visible red purity OR redsign
    smear). Ranked by ``purity x tier`` then proximity to the beacon (the pure
    seam is densest at the broadcast centre). Redsign smear cells with no
    visible purity are treated as pure(255) candidates — that IS what the
    beacon advertises, but only while the pure is still missing (below).

    OBS-57, two corrections, both from `SNAP_ac1c55bf_d6_p4`:

    * **A located pure retires the promise.** A smear advertises ONE pure. Once
      we can see it, the rest of the smear is ordinary fog, but every fogged
      smear cell still scored 765 — so (17,4), (14,8) and (15,3), none of which
      we can see at all, outranked the 240 MASS at (16,7) that we can. The
      second harvester was sent to gamble on a jackpot the first harvester was
      already standing on. Credit is now withdrawn as soon as a pure is visible
      inside that smear; on a rival's fogged seam, where the pure really is
      unlocated, nothing changes.
    * **Green is not value.** The ring never filtered stripped ground, so it
      went on offering (17,7)/(17,8) at 158/142 hours after a rival harvester
      cleared them — the same stale-echo cells the card's own OPPONENT INTEL
      block was reporting as a trail.
    """
    width, height = _grid_dims(agent_view)
    disk = set(_vision_disk(probe_at[0], probe_at[1], width, height))
    red = _visible_red(agent_view)
    smear = _redsign_cells(agent_view)
    green = _known_green_cells(agent_view)
    pure_located = any(p >= 255 for c, p in red.items() if c in smear)
    ranked: List[Tuple[float, int, Tuple[int, int]]] = []
    for cell in disk:
        if cell in green:
            continue
        purity = red.get(cell)
        if purity is None and cell in smear and not pure_located:
            purity = 255
        if not purity:
            continue
        tier = _tier_name(int(purity))
        score = float(purity) * _TIER_MULT.get(tier, 1.0)
        # nearer the beacon breaks ties (denser seam).
        prox = -(abs(cell[0] - beacon[0]) + abs(cell[1] - beacon[1]))
        ranked.append((score, prox, cell))
    ranked.sort(key=lambda t: (t[0], t[1]), reverse=True)
    out: List[Dict[str, Any]] = []
    for score, _prox, cell in ranked[:max_cells]:
        purity = red.get(cell) or 255
        out.append({
            "at": [int(cell[0]), int(cell[1])],
            "purity": int(purity),
            "tier": _tier_name(int(purity)),
            "score": round(score, 1),
        })
    return out


def _flank_probe(
    beacon: Tuple[int, int],
    direction: str,
    width: int,
    height: int,
    *,
    dist: int = 3,
    avoid_center: Optional[Tuple[int, int]] = None,
) -> Tuple[int, int]:
    """A fresh probe cell off the beacon along ``direction`` that COVERS it.

    Drop-legality is mandatory: the returned probe always keeps the beacon
    inside its Euclidean r4 disk (so a drop there is legal) and is never the
    beacon itself (anti-crush). A DIAGONAL direction can only reach ~2 cells
    before leaving the disk, so we cap the reach per direction and search from
    the farthest covering cell inward. Clearing an EMP blast is best-effort: we
    return the farthest covering cell that also clears the blast, else the
    farthest covering cell.
    """
    vec = _DIRS.get(direction, (1, 0))
    step_len = (vec[0] ** 2 + vec[1] ** 2) ** 0.5 or 1.0
    # Max whole steps that keep the beacon inside the Euclidean r4 disk.
    max_cover = max(1, int(_PROBE_RADIUS / step_len))
    hi = max(1, min(int(dist), max_cover))
    farthest_cover: Optional[Tuple[int, int]] = None
    for d in range(hi, 0, -1):
        px = _clamp(beacon[0] + vec[0] * d, 0, width - 1)
        py = _clamp(beacon[1] + vec[1] * d, 0, height - 1)
        cand = (px, py)
        if cand == beacon or not _covers(px, py, beacon[0], beacon[1]):
            continue
        if farthest_cover is None:
            farthest_cover = cand
        clear = (
            avoid_center is None
            or max(abs(px - avoid_center[0]), abs(py - avoid_center[1])) > _EMP_RADIUS
        )
        if clear:
            return cand
    if farthest_cover is not None:
        return farthest_cover
    # Last resort: one step along the direction (covers unless it lands on the
    # beacon after clamping, in which case nudge east).
    px = _clamp(beacon[0] + vec[0], 0, width - 1)
    py = _clamp(beacon[1] + vec[1], 0, height - 1)
    if (px, py) == beacon:
        px = _clamp(beacon[0] + 1, 0, width - 1)
    return (px, py)


def _drop_toward(
    beacon: Tuple[int, int],
    probe_at: Tuple[int, int],
    width: int,
    height: int,
    *,
    green: Optional[Set[Tuple[int, int]]] = None,
) -> Tuple[int, int]:
    """A drop cell one step off the beacon toward the probe (drop-legal, seam).

    Fix 1.6 (OBS-11) — never hand back a cell we KNOW is stripped. A landing
    auto-harvests whatever is under it, so a drop onto green banks a green
    parcel: nothing gained and -100 at season end, before the walk even starts.
    This is the last-resort geometric fallback, so when the obvious cell is
    green we widen to the rest of the probe's disk before conceding.
    """
    bad = green or set()
    dx, dy = _sign(probe_at[0] - beacon[0]), _sign(probe_at[1] - beacon[1])
    cand = (_clamp(beacon[0] + dx, 0, width - 1), _clamp(beacon[1] + dy, 0, height - 1))
    if (
        cand != probe_at
        and cand not in bad
        and _covers(probe_at[0], probe_at[1], cand[0], cand[1])
    ):
        return cand
    if beacon not in bad:
        return beacon
    # Both stock answers are stripped: take any drop-legal cell in the disk that
    # isn't, nearest the beacon, rather than knowingly opening on -100.
    best: Optional[Tuple[int, Tuple[int, int]]] = None
    for x in range(max(0, probe_at[0] - 4), min(width, probe_at[0] + 5)):
        for y in range(max(0, probe_at[1] - 4), min(height, probe_at[1] + 5)):
            cell = (x, y)
            if cell in bad or cell == probe_at:
                continue
            if not _covers(probe_at[0], probe_at[1], x, y):
                continue
            d = _cheb_to(cell, beacon)
            if best is None or d < best[0]:
                best = (d, cell)
    return best[1] if best is not None else beacon


def _value_drop(
    agent_view: Mapping[str, Any],
    beacon: Tuple[int, int],
    probe_at: Tuple[int, int],
    width: int,
    height: int,
    *,
    exclude: Optional[Set[Tuple[int, int]]] = None,
) -> Tuple[int, int]:
    """The RICHEST drop-legal cell in ``probe_at``'s disk, else geometric.

    ``exclude`` holds ground an EARLIER wave already takes. Without it a
    second-wave pattern opens on the richest cell in the disk — which is the
    PURE the first wave just lifted — so the "mass ring" strip lands on our own
    stripped green for -100 (OBS-42). The wave that follows must never be
    offered the cell the wave before it harvested.

    Flank/walk-in waves used to drop one geometric step off the JITTERED beacon
    (``_drop_toward``) — a blind guess that ignored the live purity the wave's
    own probe reveals ("blind walk of its own live"). When we actually have
    vision (own redsign) or a smear to triangulate (rival redsign),
    :func:`enumerate_value_ring` ranks the cells in the probe's disk by live
    value; land on the best one. We skip the probe centre so the wave never
    self-crushes the probe it just launched to enable the drop; if the only
    value sits on the centre we fall back to the geometric seam cell.
    """
    # Fix 1.6 (OBS-11) — known-stripped ground is excluded exactly like ground
    # an earlier wave takes. Both are cells whose value is already gone; the
    # only difference is who took it.
    taken = set(exclude or set()) | _known_green_cells(agent_view)
    for cand in enumerate_value_ring(agent_view, beacon, probe_at, max_cells=6):
        at = cand.get("at")
        if not isinstance(at, (list, tuple)) or len(at) != 2:
            continue
        cell = (int(at[0]), int(at[1]))
        if cell != probe_at and cell not in taken:
            return cell
    return _drop_toward(beacon, probe_at, width, height, green=taken)


def _own_active_probe_centers(agent_view: Mapping[str, Any]) -> List[Tuple[int, int]]:
    """Live friendly probe cells — each makes its Euclidean r4 disk drop-legal."""
    out: List[Tuple[int, int]] = []
    ents = (agent_view.get("entities") or {}).get("mine") or []
    for e in ents:
        if not isinstance(e, Mapping) or str(e.get("type") or "") != "probe":
            continue
        nr = e.get("nights_remaining")
        if isinstance(nr, (int, float)) and int(nr) <= 0:
            continue
        pos = e.get("pos") or e.get("at")
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            try:
                out.append((int(pos[0]), int(pos[1])))
            except (TypeError, ValueError):
                continue
    return out


def _drop_legal_now(agent_view: Mapping[str, Any], cell: Tuple[int, int]) -> bool:
    """True iff a live friendly probe already covers ``cell`` (drop-straight OK)."""
    return any(
        _covers(cx, cy, cell[0], cell[1])
        for (cx, cy) in _own_active_probe_centers(agent_view)
    )


def _trim_redundant_probe(
    agent_view: Mapping[str, Any],
    drop: Tuple[int, int],
    probe_at: Optional[Tuple[int, int]],
    supersede: Optional[Tuple[int, int]],
) -> Optional[Tuple[int, int]]:
    """R2.5 — drop a covering probe that covers nothing we do not already have.

    A wave asks for a probe for exactly one reason: to make its drop legal. Two
    things can already have done that — a friendly probe still burning from an
    earlier night, or the supersede probe this same wave is about to launch,
    whose own r4 disk usually swallows the drop cell. In both cases the extra
    launch buys no legality, and seed-56 night 2 is what that costs: a supersede
    at (29,8) and a "triangulation" probe at (29,9) for one drop at (30,9),
    which put the plan at three probes against a stock of two and silently shed
    the third option.

    The exception the geometry does earn: a probe LANDING ON a rival probe kills
    it, and that denial is worth a launch on its own. Within a wave the probe
    and the drop resolve in the same hour, so the landing is always the move
    immediately before the drop — the "next move" condition is structural here
    rather than something to re-check.
    """
    if probe_at is None:
        return None
    if _lands_on_enemy_probe(agent_view, probe_at):
        return probe_at
    if _drop_legal_now(agent_view, drop):
        return None
    if supersede is not None and _covers(
        supersede[0], supersede[1], drop[0], drop[1]
    ):
        return None
    return probe_at


def _lands_on_enemy_probe(
    agent_view: Mapping[str, Any], cell: Tuple[int, int],
) -> bool:
    """Would a probe at ``cell`` destroy a rival probe? (denial worth paying for)"""
    for e in _enemy_probe_cells(agent_view):
        at = e.get("at") if isinstance(e, Mapping) else None
        if not (isinstance(at, (list, tuple)) and len(at) == 2):
            continue
        try:
            if (int(at[0]), int(at[1])) == (int(cell[0]), int(cell[1])):
                return True
        except (TypeError, ValueError):
            continue
    return False


# ── walk-in-from-live geometry (no probe needed) ────────────────────────
# The engine gates only the INITIAL drop on live/echo coverage; every STEP
# afterward is unrestricted (it just cannot land on green). So an echo pure
# that sits OUTSIDE live coverage is still reachable on foot: drop on the
# nearest live-legal, non-green frontier cell and WALK IN through fog to the
# pure — no fresh probe required. This is the fix for the "0 moves" strand
# (probe_stock=0 + echo pure => menu offered only undroppable probe-drops).
_HOLD_CAP_STEPS = 5  # drop + up to 5 steps = 6-parcel hold (RULEBOOK §3)

# WALK_TO_CONTEST reach. Steps HARVEST as they go, so a long walk to a rival
# smear arrives with a hold full of fog trace and no capacity left to comb — and
# the comb is what actually finds a jittered pure. Cap the short variant at 3 so
# at least 2 parcels stay free for the sweep; the FAR variant deliberately
# spends the hold on the walk instead.
_WALK_CONTEST_NEAR = 3


def _live_cells(agent_view: Mapping[str, Any]) -> "set":
    try:
        return set(_live_vision_cells(agent_view))
    except Exception:  # pragma: no cover - defensive
        return set()


def _cells_of(
    drop: Tuple[int, int], comb: Sequence[Sequence[int]],
) -> List[Tuple[int, int]]:
    """A wave's full ground: the drop cell followed by every step."""
    return [(int(drop[0]), int(drop[1]))] + [(int(c[0]), int(c[1])) for c in comb]


def _unseen_surround(
    agent_view: Mapping[str, Any],
    pure: Tuple[int, int],
    width: int,
    height: int,
) -> List[Tuple[int, int]]:
    """The cells TOUCHING the pure that we cannot currently see.

    Fix 1.2 — the target set for SWEEP_RING's tail. A redsign's value is packed
    around its pure, so the unlit half of that ring is the densest unknown on
    the board: it is not speculative frontier, it is the rest of a seam we have
    already confirmed is rich. Known-green is excluded — that ground is unseen
    only in the sense that we already emptied it.
    """
    live = _live_cells(agent_view)
    green = _known_green_cells(agent_view)
    out: List[Tuple[int, int]] = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            cell = (pure[0] + dx, pure[1] + dy)
            if cell == tuple(pure):
                continue
            if not (0 <= cell[0] < width and 0 <= cell[1] < height):
                continue
            if cell in live or cell in green:
                continue
            out.append(cell)
    return out


def _walk_path_to(
    start: Tuple[int, int],
    goal: Tuple[int, int],
    green: "set",
    width: int,
    height: int,
    max_steps: int,
) -> Optional[List[Tuple[int, int]]]:
    """Shortest NSEW path ``start``->``goal`` (<= ``max_steps`` steps) whose STEP
    cells avoid green. Returns the ordered step cells (excluding ``start``,
    INCLUDING ``goal``) or ``None`` if the goal is unreachable within budget.
    """
    if start == goal:
        return []
    from collections import deque
    prev: Dict[Tuple[int, int], Optional[Tuple[int, int]]] = {start: None}
    q = deque([start])
    while q:
        cur = q.popleft()
        for dx, dy in _NSEW_ORDER:
            nc = (cur[0] + dx, cur[1] + dy)
            if not (0 <= nc[0] < width and 0 <= nc[1] < height) or nc in prev:
                continue
            if nc != goal and nc in green:
                continue  # steps may not land on a green hazard
            prev[nc] = cur
            if nc == goal:
                path: List[Tuple[int, int]] = []
                node: Optional[Tuple[int, int]] = nc
                while node is not None and prev[node] is not None:
                    path.append(node)
                    node = prev[node]
                path.reverse()
                return path if len(path) <= max_steps else None
            q.append(nc)
    return None


def _walkin_from_live(
    agent_view: Mapping[str, Any],
    pure: Tuple[int, int],
    green: "set",
    width: int,
    height: int,
    *,
    avoid_drops: "set" = frozenset(),
    max_steps: int = _HOLD_CAP_STEPS,
) -> Optional[Tuple[Tuple[int, int], List[Tuple[int, int]]]]:
    """Nearest live-legal, non-green frontier cell that can WALK to ``pure``.

    Returns ``(drop_cell, path_to_pure)`` where the path ends ON the pure
    (auto-harvested on arrival) or ``None`` when the pure is not walkable from
    any live cell within the hold budget. ``avoid_drops`` lets a second wave
    pick a DIFFERENT frontier so the two harvesters approach on disjoint paths.
    """
    live = _live_cells(agent_view)
    cands = [
        c for c in live
        if c not in green and c != pure and c not in avoid_drops
    ]
    # nearest first (short walk = fewer hours exposed), deterministic tie-break.
    cands.sort(key=lambda c: (abs(c[0] - pure[0]) + abs(c[1] - pure[1]), c[1], c[0]))
    for drop in cands[:32]:
        path = _walk_path_to(drop, pure, green, width, height, max_steps)
        if path is not None:
            return drop, path
    return None


def _mass_tail(
    agent_view: Mapping[str, Any],
    frm: Tuple[int, int],
    green: "set",
    width: int,
    height: int,
    used: "set",
    n: int,
) -> List[Tuple[int, int]]:
    """Extend a walk past the pure into the surrounding MASS (up to ``n`` steps).

    Mass usually rings the pure, so after banking the pure we keep walking to
    grab it: prefer visible-red neighbours (richest first), else blind-walk the
    ring (any non-green neighbour) since the halo is usually there. Contiguous
    NSEW steps only; never revisits a used/green cell.
    """
    red = _visible_red(agent_view)
    tail: List[Tuple[int, int]] = []
    cur = frm
    seen = set(used) | {frm}
    for _ in range(max(0, n)):
        nbrs = [
            (cur[0] + dx, cur[1] + dy) for dx, dy in _NSEW_ORDER
        ]
        nbrs = [
            c for c in nbrs
            if 0 <= c[0] < width and 0 <= c[1] < height
            and c not in green and c not in seen
        ]
        if not nbrs:
            break
        # richest visible red first; else the ring cell nearest to staying put
        nbrs.sort(key=lambda c: (-int(red.get(c, 0)), abs(c[0] - frm[0]) + abs(c[1] - frm[1])))
        nxt = nbrs[0]
        tail.append(nxt)
        seen.add(nxt)
        cur = nxt
    return tail


_NSEW_ORDER = ((1, 0), (-1, 0), (0, 1), (0, -1))


def _best_mass_target(
    agent_view: Mapping[str, Any],
    pure: Tuple[int, int],
    green: "set",
    exclude: "set",
    *,
    radius: int = 3,
) -> Optional[Tuple[int, int]]:
    """The richest visible RED cell ringing ``pure`` that no earlier wave took.

    The objective for a follow-up walk-in when there is no reason to re-enter
    the pure: the points live in the mass halo, and walking to the halo directly
    banks it without paying -100 to cross ground we already stripped (OBS-42).
    """
    red = _visible_red(agent_view)
    best: Optional[Tuple[int, int]] = None
    best_key = (-1, 99)
    for (cx, cy), purity in red.items():
        if (cx, cy) == pure or (cx, cy) in green or (cx, cy) in exclude:
            continue
        d = max(abs(cx - pure[0]), abs(cy - pure[1]))
        if d > radius or purity <= 0:
            continue
        key = (int(purity), -d)
        if key > best_key:
            best_key, best = key, (cx, cy)
    return best


def _known_core(
    agent_view: Mapping[str, Any],
    beacon: Tuple[int, int],
    *,
    radius: int = 6,
) -> Optional[Tuple[Tuple[int, int], int]]:
    """The pure you already REVEALED: richest VISIBLE-red cell near the beacon.

    A redsign that is mine (or otherwise in our LOS) means a friendly probe has
    already un-fogged the pure — so we know its exact cell and do not need to
    re-probe blindly toward the jittered smear. Returns ``(cell, purity)`` of the
    top purity x tier cell within Chebyshev ``radius`` of the beacon, or ``None``
    when nothing red is visible there yet (pure still fogged -> blind geometry).
    """
    red = _visible_red(agent_view)
    best: Optional[Tuple[float, int, Tuple[int, int]]] = None
    for cell, purity in red.items():
        if max(abs(cell[0] - beacon[0]), abs(cell[1] - beacon[1])) > radius:
            continue
        tier = _tier_name(int(purity))
        score = float(purity) * _TIER_MULT.get(tier, 1.0)
        if best is None or score > best[0]:
            best = (score, int(purity), cell)
    return None if best is None else (best[2], best[1])


def _live_red(agent_view: Mapping[str, Any]) -> Dict[Tuple[int, int], int]:
    """RED cells under LIVE coverage right now (``red_tiles[].freshness=='fresh'``).

    Part B confirm gate: on a RIVAL seam only LIVE red is trustworthy — an ECHO
    (stale) sighting may already have been stripped to green by the discoverer,
    which is exactly how a blind contest lands on green (seed-56). So the rival
    contest is gated on LIVE red, never echo.
    """
    out: Dict[Tuple[int, int], int] = {}
    for row in (agent_view.get("red_tiles") or []):
        if not isinstance(row, Mapping):
            continue
        if str(row.get("freshness") or "") != "fresh":
            continue
        try:
            x, y = int(row["x"]), int(row["y"])
            p = int(row.get("purity") or 0)
        except (TypeError, KeyError, ValueError):
            continue
        if p > 0:
            out[(x, y)] = p
    return out


def _live_core(
    agent_view: Mapping[str, Any],
    beacon: Tuple[int, int],
    *,
    radius: int = 6,
) -> Optional[Tuple[Tuple[int, int], int]]:
    """The richest LIVE-red cell within ``radius`` of the beacon, or ``None``.

    ``None`` means the seam is FOGGED to us (no live vision) — Part B then denies
    (probe + supersede) instead of blind-dropping a harvester onto it.
    """
    live = _live_red(agent_view)
    best: Optional[Tuple[float, int, Tuple[int, int]]] = None
    for cell, purity in live.items():
        if max(abs(cell[0] - beacon[0]), abs(cell[1] - beacon[1])) > radius:
            continue
        tier = _tier_name(int(purity))
        score = float(purity) * _TIER_MULT.get(tier, 1.0)
        if best is None or score > best[0]:
            best = (score, int(purity), cell)
    return None if best is None else (best[2], best[1])


def _live_pure_core(
    agent_view: Mapping[str, Any],
    beacon: Tuple[int, int],
    *,
    radius: int = 6,
) -> Optional[Tuple[int, int]]:
    """A PURE(255) cell near the beacon that we can see RIGHT NOW, or ``None``.

    Fix 1.4 (OBS-39). The rival family decided "can we see the pure?" with
    :func:`_known_core`, which reads ``red_tiles`` WITHOUT the freshness filter
    and therefore counts two-night-old echoes as sight. Two lies came out of
    that one call: an option labelled ``BLIND_GRAB`` for a pure sitting in plain
    view, and — worse — the note "you can SEE the pure here" printed over an
    echo. Deciding on LIVE red only keeps both honest, and the echo case then
    falls through to the blind framing, which is exactly what it is.
    """
    best: Optional[Tuple[int, int]] = None
    for cell, purity in _live_red(agent_view).items():
        if int(purity) < 255:
            continue
        if max(abs(cell[0] - beacon[0]), abs(cell[1] - beacon[1])) > radius:
            continue
        if best is None or _cheb_to(cell, beacon) < _cheb_to(best, beacon):
            best = cell
    return best


def _cheb_to(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _wave1_grab_geometry(
    agent_view: Mapping[str, Any],
    beacon: Tuple[int, int],
    hint: Mapping[str, Any],
    *,
    force_probe: bool = False,
) -> Tuple[Tuple[int, int], Tuple[int, int], Optional[Tuple[int, int]], List[Tuple[int, int]]]:
    """Resolve wave-1 grab geometry: (drop, probe_context, wave1_probe, value_cells).

    The GRAB is the DROP — it must LAND ON the pure. If a friendly probe has
    already revealed the pure (``_known_core``), we drop STRAIGHT on that exact
    cell (no fresh probe) whenever it is currently drop-legal; only add a covering
    probe when the pure is revealed but no longer disk-covered. When the pure is
    still fogged we fall back to the hint's blind fog geometry (a probe near the
    smear, drop on the best in-disk cell). ``force_probe`` keeps a wave-1 probe
    for the contested rival case (we stage behind our own fresh probe).
    """
    width, height = _grid_dims(agent_view)
    core = _known_core(agent_view, beacon)
    if core is not None:
        drop1 = core[0]
        covering = [
            p for p in _own_active_probe_centers(agent_view)
            if _covers(p[0], p[1], drop1[0], drop1[1])
        ]
        if covering and not force_probe:
            wave1_probe: Optional[Tuple[int, int]] = None
            probe_ctx = covering[0]
        else:
            probe_ctx = _flank_probe(drop1, "E", width, height)
            wave1_probe = probe_ctx
        ring = enumerate_value_ring(agent_view, beacon, probe_ctx)
    else:
        probe_ctx = tuple(hint.get("probe_at") or _flank_probe(beacon, "E", width, height))
        ring = enumerate_value_ring(agent_view, beacon, probe_ctx)
        drop1 = _best_core_cell(
            ring, probe_ctx, beacon, green=_known_green_cells(agent_view),
        )
        wave1_probe = None if tuple(drop1) in _visible_red(agent_view) else probe_ctx
    value_cells = [tuple(c["at"]) for c in ring] or list(_redsign_cells(agent_view).keys())
    return drop1, probe_ctx, wave1_probe, value_cells


def _freshest_enemy_probe_in_smear(
    agent_view: Mapping[str, Any], beacon: Tuple[int, int],
) -> Optional[Tuple[int, int]]:
    """The freshest enemy probe cell sitting INSIDE the redsign smear (M3).

    A rival beacon is minted BY a rival probe that landed on/near the pure, and
    that launch is PUBLIC (§3.15 — surfaced via ``_enemy_probe_cells`` now that
    the seed-69 E2 view/merge bug is fixed). So the discoverer's probe is the
    single best triangulation anchor we have for the fogged pure: the pure is
    within the probe's r4 disk. Returns the freshest such cell within the beacon
    match radius, or ``None`` when no enemy probe is known there (true blind).
    """
    for e in _enemy_probe_cells(agent_view):  # freshest first
        at = e.get("at") if isinstance(e, Mapping) else None
        if not (isinstance(at, (list, tuple)) and len(at) == 2):
            continue
        try:
            c = (int(at[0]), int(at[1]))
        except (TypeError, ValueError):
            continue
        if max(abs(c[0] - beacon[0]), abs(c[1] - beacon[1])) <= _REGION_MATCH_RADIUS:
            return c
    return None


def _triangulated_blind_geometry(
    agent_view: Mapping[str, Any],
    beacon: Tuple[int, int],
    finder: Tuple[int, int],
) -> Tuple[Tuple[int, int], Tuple[int, int], List[Tuple[int, int]]]:
    """CASE-2 blind grab geometry anchored to the FINDER's probe (M3).

    Instead of guessing off the jittered beacon centre, we approach from the
    finder's bearing (our fresh probe covers the beacon from their side, so it
    un-fogs the same pure pocket) and DROP on the smear/red candidate closest to
    the finder's probe — that is where the discoverer's own drop revealed the
    pure. Returns ``(drop, probe, value_cells)``.
    """
    width, height = _grid_dims(agent_view)
    approach_dir = _dir_name(beacon, finder)
    probe1 = _flank_probe(beacon, approach_dir, width, height)
    ring = enumerate_value_ring(agent_view, beacon, probe1)
    # Drop on the in-disk candidate NEAREST the finder's probe (highest pure
    # likelihood), never the probe cell itself (anti-crush).
    green = _known_green_cells(agent_view)  # fix 1.6 — never triangulate onto -100
    best: Optional[Tuple[int, Tuple[int, int]]] = None
    for c in ring:
        at = c.get("at")
        if not (isinstance(at, (list, tuple)) and len(at) == 2):
            continue
        cell = (int(at[0]), int(at[1]))
        if cell == tuple(probe1) or cell in green:
            continue
        d = abs(cell[0] - finder[0]) + abs(cell[1] - finder[1])
        if best is None or d < best[0]:
            best = (d, cell)
    drop1 = (
        best[1] if best is not None
        else _drop_toward(beacon, probe1, width, height, green=green)
    )
    value_cells = [tuple(c["at"]) for c in ring] or list(_redsign_cells(agent_view).keys())
    return drop1, probe1, value_cells


def _best_core_cell(
    ring: Sequence[Mapping[str, Any]],
    probe_at: Tuple[int, int],
    beacon: Tuple[int, int],
    *,
    green: Optional[Set[Tuple[int, int]]] = None,
) -> Tuple[int, int]:
    """The richest cell to LAND ON for wave 1 (I4/I6 + F7 auto-harvest).

    ``ring`` is already ranked purity x tier descending and every cell sits in
    ``probe_at``'s disk (drop-legal). We pick the top cell that isn't the probe
    cell itself (anti-crush) so the harvester drops directly ON the pure/mass
    core and banks it as free parcel #1, rather than landing on a trace edge and
    walking in (the F5 zero-pures signature). Falls back to the beacon when the
    ring is empty (no visible/broadcast value in the disk).

    Fix 1.6 (OBS-11) — ``green`` cells are skipped. On a blind attack the ring
    is built from SMEAR weights rather than live purity, so it happily ranks a
    cell we personally stripped on an earlier night as a top candidate.
    """
    bad = green or set()
    for c in ring:
        at = c.get("at")
        if not (isinstance(at, (list, tuple)) and len(at) == 2):
            continue
        cell = (int(at[0]), int(at[1]))
        if cell != tuple(probe_at) and cell not in bad:
            return cell
    return beacon


# ── pattern builders ───────────────────────────────────────────────────
def _hint_beacon(hint: Mapping[str, Any]) -> Optional[Tuple[int, int]]:
    d = hint.get("drop_at")
    if isinstance(d, (list, tuple)) and len(d) == 2:
        try:
            return int(d[0]), int(d[1])
        except (TypeError, ValueError):
            return None
    return None


def _match_hint(
    beacon: Tuple[int, int], hints: Sequence[Mapping[str, Any]],
) -> Optional[Mapping[str, Any]]:
    """The redsign hot-drop hint nearest ``beacon`` within the match radius.

    A live region uses its matching hint for richer geometry (probe_at,
    supersede, contested, live drop targeting). A fogged region with no hint
    falls back to a synthesised centre hint in :func:`build_seam_menu`.
    """
    best: Optional[Tuple[int, Mapping[str, Any]]] = None
    for h in hints:
        b = _hint_beacon(h)
        if b is None:
            continue
        d = max(abs(b[0] - beacon[0]), abs(b[1] - beacon[1]))
        if d <= _REGION_MATCH_RADIUS and (best is None or d < best[0]):
            best = (d, h)
    return None if best is None else best[1]


def _match_mine(
    beacon: Tuple[int, int], regions: Sequence[Mapping[str, Any]],
) -> Optional[bool]:
    best: Optional[Tuple[float, Optional[bool]]] = None
    for r in regions:
        ctr = r.get("center")
        if not isinstance(ctr, tuple):
            continue
        d = max(abs(beacon[0] - ctr[0]), abs(beacon[1] - ctr[1]))
        if d <= _REGION_MATCH_RADIUS and (best is None or d < best[0]):
            best = (d, r.get("mine"))
    return None if best is None else best[1]


def _walkin_mine_patterns(
    agent_view: Mapping[str, Any],
    beacon: Tuple[int, int],
    pure: Tuple[int, int],
    first: Tuple[Tuple[int, int], List[Tuple[int, int]]],
    threat: Mapping[str, Any],
    *,
    width: int,
    height: int,
    green: "set",
    emp: Optional[Tuple[int, int]],
    spacing: int,
    weapons: bool,
) -> List[SeamPattern]:
    """CASE 1, WALK-IN mode — the (echo) pure is known but sits OUTSIDE live
    coverage, and there is a live frontier cell we can WALK in from (no fresh
    probe needed; steps are not vision-gated).

    A direct smash-grab is EMP-proof (the drop resolves in one hour) — but a
    WALK to the pure spans hours, so EMP can interdict it. The pure is too
    valuable to trust to one interdictable chain, so we DOUBLE-WALK it:

      * ``WALKIN_GRAB``   H1 — drop on the nearest live frontier, walk straight
                          onto the pure, grab, pick up FAST (secure it first).
      * ``WALKIN_SECURE`` staggered — a SECOND harvester walks the SAME pure from
                          a different frontier (EMP hedge: if H1 was jammed this
                          still banks the jackpot; if H1 succeeded you knowingly
                          take a green — worth it) then reaches into the MASS.
      * ``WALKIN_LATE``   late — a third, staggered walk-in to the pure that then
                          sweeps the surrounding mass halo.
    """
    drop1, path1 = first
    dir1 = _dir_name(drop1, pure)
    grab = SeamPattern(
        pattern_id="WALKIN_GRAB",
        kind="WALKIN_GRAB",
        beacon=beacon,
        mine=True,
        title="Walk-in grab your pure (1st harvester, H01)",
        when=f"drop live @({drop1[0]},{drop1[1]}), walk onto the pure, grab fast",
        rationale=(
            "The pure is yours but sits outside a live probe disk. The initial "
            "drop needs live coverage; steps do NOT — so land on the nearest "
            "live frontier cell and WALK straight onto the pure (auto-harvest), "
            "then pick up immediately. No probe needed. THE priority move."
        ),
        waves=[SeamWave(
            1, _H_SMASH, drop1, list(path1), probe_at=None,
            direction=dir1, unit_ordinal=0, pickup_after=True,
            note=(
                "drop on the LIVE frontier cell, then walk the fog steps onto "
                "your pure and PICK UP FAST — a walk-in can be EMP-jammed, so "
                "WALKIN_SECURE doubles it for safety"
            ),
        )],
    )
    patterns = [grab]

    used = {drop1, *path1}
    # WAKE GUARD (OBS-42, fix 1.1). Re-walking the pure is a HEDGE, and a hedge
    # is only worth its premium when something could have voided wave 1. A
    # walk-in spans hours, so chaff/EMP genuinely can interdict it — but on a
    # weapons-free board nothing can, and the second unit then pays -100 to
    # cross ground its own H1 just stripped, for nothing. So the objective
    # forks: the SAME pure under weapons, the MASS HALO otherwise.
    target2 = pure if weapons else (
        _best_mass_target(agent_view, pure, green, used) or pure
    )
    hedge = target2 == pure
    # When the objective is the halo, H1's WHOLE ROUTE joins the blocked set,
    # not just its landing cell. ``avoid_drops`` only picks a different frontier
    # to open on; without blocking the path too, wave 2 simply walked back along
    # wave 1's trail to reach the halo and stripped green the entire way — which
    # is why `own_seam_d4` still failed `no_wake_reentry` after the first cut of
    # this guard. Blocking the wake can make the halo unreachable, so fall back
    # to the pure alone rather than dropping the option entirely.
    # OBS-59. The hedge branch blocked nothing but known green, because only the
    # DESTINATION was ever reasoned about, never the APPROACH. Asked for a route
    # to the pure with wave 1's trail wide open, the pathfinder returns the
    # cheapest one — which IS that trail. On `own_seam_d4` wave 2 re-walked all
    # five of wave 1's cells to reach the pure: about -500 spent on a jackpot
    # the hedge argument prices at a single green.
    #
    # So the wake is blocked MINUS the pure itself: the one cell the hedge is
    # genuinely buying stays reachable, and the walk in has to find its own way.
    # If it cannot, the hedge is NOT re-priced and re-offered — it is withdrawn.
    # That is the part worth stating, because the earlier fallback here quietly
    # restored the -500 route: a hedge whose only approach retraces the wake is
    # not a hedge, it is a 5-cell green tax on a maybe. Break-even is around a
    # 40% chance of wave 1 being jammed, and nothing on these boards is close.
    # Wave 2 falls back to the mass halo, and if the halo is unreachable too the
    # pattern is dropped and the unit spends its night somewhere that pays.
    block2 = green | (used - {pure}) if hedge else green | used
    second = _walkin_from_live(
        agent_view, target2, block2, width, height, avoid_drops=used,
    )
    if second is None and hedge:
        halo = _best_mass_target(agent_view, pure, green, used)
        if halo is not None:
            target2, hedge = halo, False
            block2 = green | used
            second = _walkin_from_live(
                agent_view, target2, block2, width, height, avoid_drops=used,
            )
    elif second is None:
        block2 = green | {pure}
        second = _walkin_from_live(
            agent_view, target2, block2, width, height, avoid_drops=used,
        )
    if second is not None:
        drop2, path2 = second
        tail_budget = max(0, _HOLD_CAP_STEPS - len(path2))
        tail_n = _SHORT_GRAB_STEPS if weapons else tail_budget
        tail2 = _mass_tail(
            agent_view, target2, block2, width, height,
            used | {drop2, *path2}, min(tail_budget, tail_n),
        )
        route2 = list(path2) + tail2
        if hedge:
            note2 = (
                "SECOND walk-in onto the SAME pure from a different frontier — "
                "the EMP hedge. If the first harvester's grab was jammed, this banks the "
                "jackpot; if it already landed, re-hitting takes a green (worth "
                "it to be SURE). After the pure, walk on into the surrounding MASS"
            )
            title2 = "Double-walk the pure + mass (EMP hedge)"
            when2 = "2nd harvester: re-walk the pure from another angle, then mass"
            why2 = (
                "A walk-in is interdictable and weapons ARE in play, so the pure "
                "is too valuable to trust to one chain. A second harvester walks "
                "the same pure from a disjoint frontier and, once it is banked, "
                "reaches into the mass ring around it."
            )
        else:
            note2 = (
                "SECOND unit takes the MASS HALO, NOT the pure — the first unit already has "
                "the jackpot and no weapon is in play to jam it, so re-walking "
                "would only auto-harvest your own stripped green (-100 a cell). "
                "This route stays clear of the first harvester's wake"
            )
            title2 = "Take the mass halo (2nd harvester, H09)"
            when2 = "2nd harvester: walk the mass ring, clear of the first unit's wake"
            why2 = (
                "With no weapon able to interdict a first-hour landing, its pure is as good as "
                "banked, so the hedge premium buys nothing and the points now "
                "live in the halo. Under chaff or EMP this option becomes a "
                "second walk onto the pure instead."
            )
        patterns.append(SeamPattern(
            pattern_id="WALKIN_SECURE",
            kind="WALKIN_SECURE",
            beacon=beacon,
            mine=True,
            title=title2,
            when=when2,
            rationale=why2,
            waves=[SeamWave(
                1, _H_POST_EMP + spacing, drop2, route2,
                probe_at=None, direction=_dir_name(drop2, target2),
                unit_ordinal=1, pickup_after=True, note=note2,
            )],
        ))
        wave2_cells = {drop2, *route2}
    else:
        wave2_cells = set()

    # The third wave lands after two units have already crossed the seam, so its
    # wake set is the widest — and by now the pure is doubly spoken for. It only
    # goes back for it as the LAST resort of a weapons hedge.
    # ``wave2_cells`` is the whole wave-2 ROUTE, tail included — the earlier
    # version tracked only its approach path, so the late sweep happily opened
    # on a cell wave 2 had stripped on its way out.
    prior = used | wave2_cells
    target3 = pure if weapons else (
        _best_mass_target(agent_view, pure, green, prior) or pure
    )
    # OBS-59, same correction as wave 2 and it bites harder here: by the third
    # wave there are TWO wakes available to retrace, so an unblocked approach to
    # the pure was reliably the most expensive route on the board.
    block3 = green | (prior - {pure}) if target3 == pure else green | prior
    third = _walkin_from_live(
        agent_view, target3, block3, width, height, avoid_drops=prior,
    )
    if third is None and target3 == pure:
        halo = _best_mass_target(agent_view, pure, green, prior)
        if halo is not None:
            target3 = halo
            block3 = green | prior
            third = _walkin_from_live(
                agent_view, target3, block3, width, height, avoid_drops=prior,
            )
    elif third is None:
        block3 = green | {pure}
        third = _walkin_from_live(
            agent_view, target3, block3, width, height, avoid_drops=prior,
        )
    if third is not None:
        drop3, path3 = third
        tail3 = _mass_tail(
            agent_view, target3, block3, width, height,
            prior | {drop3, *path3}, max(0, _HOLD_CAP_STEPS - len(path3)),
        )
        late_pure = target3 == pure
        patterns.append(SeamPattern(
            pattern_id="WALKIN_LATE",
            kind="WALKIN_LATE",
            beacon=beacon,
            mine=True,
            title=(
                "Late walk-in the seam (pure + mass halo)" if late_pure
                else "Late sweep of the wider halo (3rd harvester, H12)"
            ),
            when=(
                "3rd harvester / late: walk the pure then sweep the mass halo"
                if late_pure
                else "3rd harvester / late: sweep the outer mass, clear of both wakes"
            ),
            rationale=(
                "A late third-bearing walk-in scoops the pure and the wider mass "
                "once the early grabs are committed, and leaves great vision for "
                "tomorrow." if late_pure else
                "Two units have already crossed this seam and nothing can jam "
                "them, so a third pass over the pure would only strip green. "
                "This one reaches past both wakes into the outer mass and leaves "
                "vision on the seam for tomorrow."
            ),
            waves=[SeamWave(
                1, _H_WALK_IN + spacing, drop3, list(path3) + tail3,
                probe_at=None, direction=_dir_name(drop3, target3),
                unit_ordinal=2, pickup_after=True,
                note=(
                    "late, staggered; walk the pure then blind-walk the halo"
                    if late_pure else
                    "late, staggered; stays OFF the pure and both earlier routes "
                    "— pure ground your own units already stripped pays -100"
                ),
            )],
        ))
    return patterns


def _walkin_rival_patterns(
    agent_view: Mapping[str, Any],
    beacon: Tuple[int, int],
    threat: Mapping[str, Any],
    *,
    width: int,
    height: int,
    green: "set",
    spacing: int,
    weapons: bool,
    have_probes: bool,
) -> List[SeamPattern]:
    """CASE 2, WALK-IN mode — a RIVAL seam close enough to reach on foot from our
    own live frontier, with NO fresh probe.

    Normally a rival seam can only be attacked with a probe (blind the finder,
    light the fogged pure), so with an empty magazine there is nothing to do and
    the seam is correctly ignored. The exception is the rare seam that broadcasts
    within a few steps of ground we already light: the initial DROP needs live
    coverage but STEPS do not, so we can land on our own frontier and walk in.

      * ``WALK_TO_CONTEST``     H1 — a SHORT reach (<=3 steps) that keeps most of
                                the 6-parcel hold free to COMB the smear on
                                arrival. The comb is what actually finds the pure.
      * ``WALK_TO_CONTEST_FAR`` a SECOND harvester entering from a DIFFERENT
                                frontier, spending the whole hold on the walk
                                itself — it crosses more of the smear (coverage /
                                denial) rather than combing one spot.

    Both cost ZERO probes, so they stack with the probe-funded attacks rather
    than replacing them.
    """
    near = _walkin_from_live(
        agent_view, beacon, green, width, height, max_steps=_WALK_CONTEST_NEAR,
    )
    if near is None:
        return []

    stacking = (
        " You still have probes: dropping VISION on a redsign is usually the "
        "stronger play (BLIND_AND_GRAB / BLIND_GRAB lights the seam AND blinds "
        "their finder). "
        "But this walk-in is FREE — with a spare harvester, run it ALONGSIDE "
        "the probe attack so two units pressure the same seam from different "
        "ground."
        if have_probes else
        " You have NO probes tonight, so this is the only way onto this seam."
    )

    drop1, path1 = near
    used = {drop1, *path1}
    budget = max(0, _HOLD_CAP_STEPS - len(path1))
    comb_n = _CHAFF_CHAIN_STEPS if weapons else budget
    tail1 = _mass_tail(
        agent_view, beacon, green, width, height, used, min(budget, comb_n),
    )
    patterns = [SeamPattern(
        pattern_id="WALK_TO_CONTEST",
        kind="WALK_TO_CONTEST",
        beacon=beacon,
        mine=False,
        title="Walk in and contest the rival seam (1st harvester, H01, no probe)",
        when=(
            f"drop live @({drop1[0]},{drop1[1]}), walk {len(path1)} onto the "
            "smear, then comb it"
        ),
        rationale=(
            "Their seam is close enough to REACH ON FOOT. The drop needs live "
            "coverage but steps do not, so land on your own frontier and walk "
            "in — no probe, no launch. The smear is jittered so arriving is not "
            "enough: the COMB across the neighbouring candidates is what banks "
            "the pure. A hit takes their jackpot AND denies it; a miss still "
            "banks the vein/mass around it." + stacking
        ),
        waves=[SeamWave(
            1, _H_SMASH, drop1, list(path1) + tail1, probe_at=None,
            direction=_dir_name(drop1, beacon), unit_ordinal=0,
            pickup_after=True, blind_walk=True, contested=True,
            note=(
                "land on the LIVE frontier, walk the fog steps to the smear, "
                "then COMB the candidates and PICK UP FAST"
            ),
        )],
    )]

    # Second entry from a different BEARING: merely picking another frontier
    # cell tends to return the same line one step back, which puts both units on
    # one track. Bar every lit cell that approaches from the same compass
    # direction, then REJECT any route that still shares ground with the first.
    #
    # OBS-55 (OPEN) — the beacon is exempt from the overlap check, and on
    # `V12_HEUR_R2_s2` night 3 that cost a banked pure: wave 1 walked THROUGH
    # the beacon and out the far side, wave 2 crossed the same cell in passing,
    # and the night banked ZERO against 858 harvested. Simply un-exempting it
    # is worse — on any board where wave 1 transits the beacon there is then no
    # second entry at all, and deleting the second unit is the ceding this whole
    # option exists to prevent. The real answer is to stop wave 1 AT the beacon
    # when a second entry exists, leaving the far side to wave 2, which is a
    # budget change rather than a filter and wants measuring before it ships.
    near_cells = {drop1, *path1, *tail1}
    near_dir = _dir_name(drop1, beacon)
    blocked = set(near_cells) | {
        c for c in _live_cells(agent_view) if _dir_name(c, beacon) == near_dir
    }
    far = None
    for _ in range(3):
        cand = _walkin_from_live(
            agent_view, beacon, green, width, height,
            avoid_drops=blocked, max_steps=_HOLD_CAP_STEPS,
        )
        if cand is None:
            break
        if not (({cand[0], *cand[1]} - {beacon}) & near_cells):
            far = cand
            break
        blocked.add(cand[0])  # this entry re-treads the first unit's track
    if far is None:
        return patterns
    drop2, path2 = far
    tail2 = _mass_tail(
        agent_view, beacon, green, width, height,
        near_cells | {drop2, *path2},
        max(0, _HOLD_CAP_STEPS - len(path2)),
    )
    patterns.append(SeamPattern(
        pattern_id="WALK_TO_CONTEST_FAR",
        kind="WALK_TO_CONTEST_FAR",
        beacon=beacon,
        mine=False,
        title="Long walk across the rival seam (2nd harvester, no probe)",
        when=(
            f"second unit: drop live @({drop2[0]},{drop2[1]}), cross the smear "
            "on a different bearing"
        ),
        rationale=(
            "A SECOND free walk-in, entering from a DIFFERENT BEARING. This one "
            "spends the hold on the WALK rather than a comb, so it CROSSES more "
            "of the smear — wider coverage of where the pure might be, and a "
            "second body on ground they want. Staggered to a later hour so the "
            "two entries do not arrive together."
        ),
        waves=[SeamWave(
            1, _H_WALK_IN + spacing, drop2, list(path2) + tail2, probe_at=None,
            direction=_dir_name(drop2, beacon), unit_ordinal=1,
            pickup_after=True, blind_walk=True, contested=True,
            note=(
                "different frontier, later hour — cross the smear rather than "
                "comb one spot"
            ),
        )],
    ))
    return patterns


# ── EMP compound patterns (v13 — the doctrine matrix) ─────────────────
#
# Three redsign states, three named plays. Each fires a salvo AND spools a
# harvester wave behind the cloud, so the option carries a real yield number
# rather than the +0 the plain BLIND/EMP/SCORCH salvos render. The LLM picks
# one compound id and the packager emits every hour in the right order.


def _fmt_cell_short(c: Tuple[int, int]) -> str:
    return f"({c[0]},{c[1]})"


def _count_opponents(agent_view: Mapping[str, Any]) -> int:
    """Best-effort opponent count from the entities/probes view."""
    seen: Set[str] = set()
    for e in (agent_view.get("entities", {}) or {}).values():
        if isinstance(e, list):
            for row in e:
                if isinstance(row, Mapping):
                    owner = str(row.get("owner") or "")
                    if owner and owner != "mine":
                        seen.add(owner)
    for r in (agent_view.get("redsign") or []):
        if isinstance(r, Mapping):
            owner = str(r.get("owner") or "")
            if owner and owner != "mine":
                seen.add(owner)
    return max(1, len(seen))


def _classify_redsign_case(
    agent_view: Mapping[str, Any],
    hint: Mapping[str, Any],
    beacon: Tuple[int, int],
    region: Mapping[str, Any],
) -> str:
    """Return one of 'mine_alone', 'mine_contested', 'shared_vision',
    'rival_blind', 'rival_sighted', or 'unclassified'.

    Only the first three drive EMP compound patterns; the last two fall
    through to the ordinary CASE-1/CASE-2 book (mine/rival_patterns).
    """
    mine = hint.get("mine") if hint.get("mine") is not None else region.get("mine")
    core = _known_core(agent_view, beacon)
    my_probes = set(_live_vision_cells(agent_view))
    # ``_enemy_probe_cells`` returns a list of DICTS ({"at": (x,y), ...}),
    # not tuples — passing that straight into set() raises TypeError on the
    # unhashable dict. Normalise to (x, y) tuples for membership testing.
    enemy_probes: Set[Tuple[int, int]] = set()
    for row in (_enemy_probe_cells(agent_view) or []):
        at = row.get("at") if isinstance(row, Mapping) else row
        if isinstance(at, (list, tuple)) and len(at) >= 2:
            try:
                enemy_probes.add((int(at[0]), int(at[1])))
            except (TypeError, ValueError):
                continue
    opps = _count_opponents(agent_view)

    if mine is True:
        return "mine_alone" if opps <= 1 else "mine_contested"
    if core is not None and core[0] in my_probes and core[0] in enemy_probes:
        return "shared_vision"
    if core is None:
        return "rival_blind"
    return "rival_sighted"


def _pattern_smash_then_lock(
    agent_view: Mapping[str, Any],
    beacon: Tuple[int, int],
    hint: Mapping[str, Any],
    threat: Mapping[str, Any],
) -> Optional[SeamPattern]:
    """CASE B — MY redsign, alone or dominant.

    H1 SMASH the pure (auto-harvest + pickup), then H3 EMP shaped so the
    pure stays clear but the halo darkens for 8h. Wave 3 (if a 2nd unit is
    alive) works the visible mass ring at cloud-lift.
    """
    rack = scorch.stock(agent_view)
    if rack.get("emp", 0) <= 0:
        return None
    core = _known_core(agent_view, beacon)
    if core is None:
        return None
    pure_cell = core[0]
    if not _drop_legal_now(agent_view, pure_cell):
        return None

    radius, missiles, cloud_hours = scorch.specs(agent_view)
    width, height = _grid_dims(agent_view)
    halo_pool = [
        c for c in scorch.diamond(pure_cell, _PROBE_RADIUS, (width, height))
        if c != pure_cell
    ]
    shape = scorch.shaped_salvo(
        halo_pool, radius=radius, missiles=missiles, hole=pure_cell,
    )
    if shape is None:
        return None
    targets = [tuple(t) for t in shape["targets"]]

    visible = _visible_red(agent_view)
    ring_cells = sorted(
        (c for c in halo_pool if visible.get(c, 0) >= 100),
        key=lambda c: (
            -_TIER_MULT.get(_tier_name(int(visible.get(c, 0))), 1.0)
            * visible.get(c, 0),
            abs(c[0] - pure_cell[0]) + abs(c[1] - pure_cell[1]),
        ),
    )
    ring_start = ring_cells[0] if ring_cells else None
    ring_comb = ring_cells[1:1 + _MASS_SHORT_STEPS]

    waves = [
        SeamWave(
            wave=1,
            earliest_hour=_H_SMASH,
            drop_at=pure_cell,
            unit_ordinal=0,
            pickup_after=True,
            note=(
                "H1 smash the pure (auto-harvest), H2 lift. Beats EMP and "
                "collision — the certain grab."
            ),
        ),
        SeamWave(
            wave=2,
            earliest_hour=3,
            drop_at=pure_cell,
            emp_launch_at=targets,
            emp_hole=pure_cell,
            emp_only=True,
            unit_ordinal=-1,
            note=(
                f"H3 salvo -> {targets}. Cloud saturates the halo, "
                f"pure @{_fmt_cell_short(pure_cell)} stays clear. Rivals "
                "locked out of the halo for 8h."
            ),
        ),
    ]
    if ring_start is not None:
        waves.append(SeamWave(
            wave=3,
            earliest_hour=3 + cloud_hours,
            drop_at=ring_start,
            comb_path=list(ring_comb),
            unit_ordinal=1,
            pickup_after=True,
            defer_until_clear=True,
            note=(
                "second harvester works the halo mass at cloud-lift — the "
                "ground the salvo denied to them is now denied to nobody but us."
            ),
        ))

    return SeamPattern(
        pattern_id="SMASH_THEN_LOCK",
        kind="SMASH_THEN_LOCK",
        beacon=beacon,
        mine=True,
        title=(
            "Smash the pure @H1, LOCK the halo @H3 (case B) — pure banked, "
            "8h denial, 2nd unit combs at cloud-lift"
        ),
        when=(
            "Your redsign, quiet board (0-1 rivals). H1 smash the pure, H3 "
            "EMP the halo AROUND the pure (hole = pure), 2nd harvester "
            "combs the mass ring when the cloud lifts."
        ),
        rationale=(
            "SMASH_GRAB alone banks 765 and leaves the halo — worth 400-600 "
            "in mass — for anyone who arrives after you. Adding the salvo "
            "denies that halo for 8h, and shaping the missiles so the pure "
            "stays clear means your OWN second harvester lands on it if you "
            "have one. The hour cost is H3 — the smash-and-lift takes H1-H2, "
            "so nothing else was scheduled to run in H3 anyway. If you have "
            "no 2nd unit, run this as the 2-wave lock: the seam is still "
            "yours next night, and the rival lost a night on it."
        ),
        waves=waves,
    )


def _pattern_race_crash_emp(
    agent_view: Mapping[str, Any],
    beacon: Tuple[int, int],
    hint: Mapping[str, Any],
    threat: Mapping[str, Any],
    *,
    harvesters_alive: int,
) -> Optional[SeamPattern]:
    """CASE C — I and >=1 rival both see the pure.

    Drop@H1 expecting a crash (aerial denial does NOT consume the pure).
    EMP@H3 with the hole aimed at the pure. Second harvester drops on the
    pure at cloud-lift and lifts. Skipped when chaff is in play.
    """
    rack = scorch.stock(agent_view)
    if rack.get("emp", 0) <= 0:
        return None
    if threat.get("chaff_in_play"):
        return None
    if harvesters_alive < 2:
        return None
    core = _known_core(agent_view, beacon)
    if core is None:
        return None
    pure_cell = core[0]
    if not _drop_legal_now(agent_view, pure_cell):
        return None

    radius, missiles, cloud_hours = scorch.specs(agent_view)
    width, height = _grid_dims(agent_view)
    halo_pool = [
        c for c in scorch.diamond(pure_cell, _PROBE_RADIUS, (width, height))
        if c != pure_cell
    ]
    shape = scorch.shaped_salvo(
        halo_pool, radius=radius, missiles=missiles, hole=pure_cell,
    )
    if shape is None:
        return None
    targets = [tuple(t) for t in shape["targets"]]

    return SeamPattern(
        pattern_id="RACE_CRASH_EMP",
        kind="RACE_CRASH_EMP",
        beacon=beacon,
        mine=None,
        title=(
            f"Race + crash + EMP (case C) — H1 drop, H2 lift, H3 salvo "
            f"(hole=pure), 2nd unit drops @{_fmt_cell_short(pure_cell)} at cloud-lift"
        ),
        when=(
            "You and a rival both see the pure. Race the H1 drop — expect "
            "collision, aerial denial does NOT consume the pure. Then EMP "
            "with the hole ON the pure so YOUR second harvester can drop "
            "back onto it after cloud-lift while rivals cannot walk in."
        ),
        rationale=(
            "H1 racing is unavoidable when both sides can drop on the pure — "
            "either you land and bank, or you crash and both spill. Crashing "
            "keeps the pure on the board (§4.9: aerial denial does NOT "
            "consume the cell), so a second unit re-landing under an EMP "
            "shield is a legal path to the same jackpot with the rival "
            "locked out for 8 hours. Do NOT run this if chaff is in play: "
            "chaff cancels the H1 landing outright and the second-unit "
            "re-drop cannot follow a landing that never happened. Two "
            "harvesters minimum."
        ),
        waves=[
            SeamWave(
                wave=1,
                earliest_hour=_H_SMASH,
                drop_at=pure_cell,
                unit_ordinal=0,
                pickup_after=True,
                note="H1 drop @ pure; auto-harvest if we land, deny if we crash",
            ),
            SeamWave(
                wave=2,
                earliest_hour=3,
                drop_at=pure_cell,
                emp_launch_at=targets,
                emp_hole=pure_cell,
                emp_only=True,
                unit_ordinal=-1,
                note=(
                    "H3 salvo shaped so the pure stays clear — halo denied "
                    "to rivals, pure walkable for us"
                ),
            ),
            SeamWave(
                wave=3,
                earliest_hour=3 + cloud_hours,
                drop_at=pure_cell,
                unit_ordinal=1,
                pickup_after=True,
                defer_until_clear=True,
                note=(
                    "second harvester drops on the pure at cloud-lift — if "
                    "wave 1 crashed, the pure was still on the board, and "
                    "the salvo has locked every rival out"
                ),
            ),
        ],
    )


# ── end EMP compound patterns ─────────────────────────────────────────


def _mine_patterns(
    agent_view: Mapping[str, Any],
    hint: Mapping[str, Any],
    beacon: Tuple[int, int],
    threat: Mapping[str, Any],
    *,
    probe_stock: int = 99,
) -> List[SeamPattern]:
    """CASE 1 — I found it. THREE per-harvester options, chosen by how the pure
    is REACHABLE this night:

      * pure is drop-legal NOW (a live probe covers it) -> the classic EMP-proof
        ``SMASH_GRAB`` (belly-flop) + ``SECURE_MASS`` + ``LATE_SWEEP``.
      * pure is fogged/echo but WALKABLE from a live frontier (no probe) -> the
        ``WALKIN_*`` trio (drop live, walk in; double-walk the pure vs EMP).
      * pure is fogged/echo, not walkable, but we hold a probe -> the probe-
        flanked trio (existing geometry).
      * pure is fogged/echo, not walkable, and NO probe -> [] (starvation; the
        thinker spends the fleet elsewhere rather than on undroppable geometry).

    Each pattern is ONE wave = ONE harvester, so the thinker composes the fleet
    itself (and, in the DUAL case, can send later harvesters at a rival beacon).
    """
    width, height = _grid_dims(agent_view)
    green = _known_green_cells(agent_view)
    emp = _emp_near(beacon, threat.get("emp_cells") or [])
    spacing = _EMP_CLOUD_HOURS if emp else 0
    contested = bool(hint.get("contested"))
    weapons = bool(threat.get("chaff_seen")) or emp is not None

    # Reachability fork: a KNOWN pure (live or echo) that is NOT drop-legal now
    # is still walkable from the live frontier — prefer that over burning a probe
    # (and it is the ONLY play when probe_stock is 0, the "0 moves" strand).
    core = _known_core(agent_view, beacon)
    if core is not None and not _drop_legal_now(agent_view, core[0]):
        walk = _walkin_from_live(agent_view, core[0], green, width, height)
        if walk is not None:
            return _walkin_mine_patterns(
                agent_view, beacon, core[0], walk, threat,
                width=width, height=height, green=green, emp=emp,
                spacing=spacing, weapons=weapons,
            )
        if probe_stock < 1:
            # Echo pure, no live frontier to walk from, no probe to light it —
            # genuinely unreachable this night. Offer nothing rather than a
            # bare drop the sanitizer will silently delete.
            return []
    elif core is None and probe_stock < 1:
        # Own redsign still fully fogged (no live/echo pure to walk to) and no
        # probe to light it — the flanked trio below would be all probe-drops
        # the packager can't emit. Starvation: offer nothing.
        return []

    # ── H1 SMASH_GRAB — the GRAB is the DROP: LAND on the pure/mass core
    # (auto-harvested, F7) and SECURE it FAST. Length is DANGER-GATED (0 steps
    # under threat/contest). DROP-FIRST: this redsign is MINE, so a friendly probe
    # already revealed the pure — land on that EXACT cell and drop STRAIGHT when it
    # is already covered (never re-probe to a fog cell short of the pure).
    drop1, probe1, wave1_probe, value_cells = _wave1_grab_geometry(
        agent_view, beacon, hint,
    )
    # Fix 1.2 — SMASH_GRAB is now the PURE, and nothing else, every time. It
    # used to carry a danger-gated 0-2 step tail, which made it two different
    # plays sharing one name: the agent asking for the certainty of a two-hour
    # in-and-out got a walk instead whenever the board happened to look quiet.
    # The tail is now its own option (SMASH_GRAB_VALUE) so the choice is the
    # agent's and the guarantee is real.
    comb1: List[List[int]] = []
    core_visioned = wave1_probe is None
    approach = _dir_name(probe1, beacon)
    # Fix 1.3 — the FOGGED variant is a THREE-hour play, and must say so. When
    # the pure is only an echo we have to light it first, so the shape is
    # probe → drop → lift rather than drop → lift. Calling that "two hours" (as
    # the 1.2 wording did) understates the exposure by a full hour on exactly
    # the boards where the seam is least certain. The support probe is placed
    # OFF the pure by ``_wave1_grab_geometry`` so our own landing does not
    # crush the thing that made the landing legal.
    note1 = (
        "you know the pure — DROP ON the core cell (it auto-harvests = the grab) "
        "and PICK UP FAST to bank it before rivals arrive. ZERO steps, and it is "
        "the ONLY thing on a redsign nobody can take off you — the landing beats "
        "every rival's walk, survives an EMP, and only CHAFF can deny it"
    )
    if core_visioned:
        note1 += (
            "; it is already in live vision, so drop STRAIGHT (no probe) — two "
            "hours, drop and lift"
        )
    else:
        note1 += (
            f"; the pure is an ECHO, so this is a THREE-hour play: probe "
            f"({probe1[0]},{probe1[1]}) lights it, THEN the harvester lands on "
            "it, THEN lift. The probe sits OFF the pure so your own drop does "
            "not crush it. That extra hour is the cost of not having eyes there"
        )
    if threat.get("chaff_seen"):
        note1 += " (chaff about: it CAN deny this drop — keep a later wave as backup)"
    smash = SeamPattern(
        pattern_id="SMASH_GRAB",
        kind="SMASH_GRAB",
        beacon=beacon,
        mine=True,
        title=(
            "Smash-and-grab your pure (1st harvester, H01) — pure only, two hours"
            if core_visioned else
            "Smash-and-grab your ECHO pure — pure only, three hours (probe first)"
        ),
        when=(
            "H1: land ON your pure, auto-harvest the jackpot, pick up at H2"
            if core_visioned else
            "probe lights the echo pure, land ON it, lift — three hours"
        ),
        rationale=(
            "Engine-truth says this beacon is yours, so you already know where "
            "the pure sits. Drop on it, auto-harvest, lift — no walk, no "
            "exposure beyond the landing. In redsign poker this is the one "
            "CERTAIN move on the board: it cannot be beaten to the cell, cannot "
            "be crashed mid-walk, and cannot be EMP'd. Only chaff denies it. "
            "Everything richer than this trades that certainty for points — "
            "which is often right, but it IS the trade you are making."
        ) + ("" if core_visioned else (
            " Here the pure is remembered, not seen, so it costs a probe and an "
            "extra hour to light before you can land — and the echo itself may "
            "be stale, which is the one uncertainty this play cannot remove."
        )),
        waves=[SeamWave(
            1, _H_SMASH, drop1, comb1, probe_at=wave1_probe,
            direction=approach, unit_ordinal=0, note=note1, pickup_after=True,
        )],
    )

    # ── H1 (alternative) FULL_SWEEP — the GREEDY play: ride the WHOLE visible
    # seam in one outing (pures + mass ring), banking far more than the single
    # smash. Same first harvester as SMASH_GRAB (unit_ordinal 0) — they are
    # MUTUALLY EXCLUSIVE uses of H1; the thinker picks one (or, with a 2nd
    # harvester, smashes with H1 and sweeps/secures with H2). It is an HONEST
    # gamble: one harvester means a mid-walk collision/jam zeroes UNLIFTED cargo,
    # a risk that rises with more rivals and later in the season — offered so the
    # thinker can weigh that against the sheer value, not decided deterministically.
    patterns: List[SeamPattern] = [smash]

    # ── H1 (alternative) SMASH_GRAB+VALUE — the pure plus the one or two BIG
    # cells touching it. Offered only when such cells actually exist: on a seam
    # whose pure sits alone in trace this is the same play as SMASH_GRAB, and a
    # duplicate under a richer name is worse than no option at all.
    tail = _value_tail(agent_view, drop1, green)
    if tail:
        tail_cells = ", ".join(f"({c[0]},{c[1]})" for c in tail)
        note_val = (
            f"drop on the pure, then take {len(tail)} step(s) onto adjacent "
            f"MASS/PURE only — {tail_cells} — and lift. You are trading the "
            "certainty of the two-hour grab for real points: the H1 landing is "
            "still guaranteed, but every step after it is a cell a rival can "
            "reach, an EMP can catch, and a collision can zero"
        )
        if threat.get("chaff_seen") or emp is not None:
            note_val += (
                ". WEAPONS ARE IN PLAY — the walk is what they punish; the bare "
                "SMASH_GRAB is not"
            )
        patterns.append(SeamPattern(
            pattern_id="SMASH_GRAB_VALUE",
            kind="SMASH_GRAB_VALUE",
            beacon=beacon,
            mine=True,
            title="Smash-and-grab + VALUE (1st harvester, H01) — pure plus two big squares",
            when="H1: the pure, then 1-2 steps onto touching mass/pure, then lift",
            rationale=(
                "The pure is one cell, and the mass touching it is worth nearly "
                "as much again. Two steps collect it for a couple of extra hours "
                "of exposure — a good trade on a quiet board with one rival, a "
                "bad one when chaff, EMPs or several rivals can punish the walk. "
                "It never runs past two squares and never spends a step on "
                "trace: trace is everywhere and is not worth being caught for."
            ),
            waves=[SeamWave(
                1, _H_SMASH, drop1, tail, probe_at=wave1_probe,
                direction=approach, unit_ordinal=0, note=note_val,
                pickup_after=True,
            )],
        ))

    # ── STAGED SECOND BITE at the SAME pure — chaff insurance (fix 3.4, OBS-44
    # correcting OBS-42). Two plays on one cell is normally the worst thing on
    # the menu, and it is right exactly once: chaff is the only mechanic that can
    # cancel an H1 landing, and a cancelled landing leaves the pure ON THE BOARD.
    # So the second bite is only offered where the first bite was a CERTAINTY
    # that chaff could take away — i.e. we hold H1 (the pure is drop-legal now)
    # and chaff is in play. Without an H1 drop we are already in contingent
    # territory and the insurance argument does not apply; the old rule keyed
    # this on player count, which insures against the wrong thing entirely.
    if core_visioned and threat.get("chaff_in_play"):
        patterns.append(SeamPattern(
            pattern_id="CHAFF_INSURANCE",
            kind="CHAFF_INSURANCE",
            beacon=beacon,
            mine=True,
            title="Second bite at the same pure (late) — chaff insurance",
            when="after H1 has lifted: re-drop on the pure in case chaff cancelled it",
            rationale=(
                "Chaff is the one thing that can deny an H1 landing, and a "
                "denied landing leaves the pure sitting on the board. This is a "
                "SECOND harvester re-dropping on the SAME cell hours later, and "
                "the asymmetry is the whole argument: if chaff killed the first "
                "wave you take the jackpot you would otherwise have lost; if the "
                "first wave landed you pay one green penalty for walking your "
                "own stripped ground. Roughly +765 against about -100. Do not "
                "run it on a chaff-free board — there it is the -100 and nothing "
                "else. It claims the second harvester, so it competes with "
                "SECURE_MASS: take the insurance when the pure is the night, and "
                "the ring when the pure is already safe."
            ),
            waves=[SeamWave(
                1, _H_SMASH + 4, drop1, [], probe_at=None,
                direction=approach, unit_ordinal=1, pickup_after=True,
                note=(
                    "re-drop on the pure cell itself, several hours after the "
                    "first grab lifted. If chaff cancelled that grab this banks "
                    "the pure; if it did not, this lands on your own green for "
                    "about -100. Insurance, priced accordingly"
                ),
            )],
        ))

    sweep_steps = _CHAFF_CHAIN_STEPS if weapons else _SEAM_SWEEP_STEPS
    comb_full = _comb_path(
        probe1[0], probe1[1], drop1, width, height, green, value_cells,
        max_steps=sweep_steps,
    )
    if len(comb_full) >= 2 and len(comb_full) > len(comb1):
        note_sweep = (
            "GAMBLE — bank the WHOLE visible seam in ONE outing (its pures + the "
            "mass ring), far more points than the single smash. But ONE harvester "
            "means a mid-walk collision or chaff jam can ZERO the unlifted cargo, "
            "and that risk RISES with more rivals racing this public pure and "
            "later in the season. Take it when the sheer value outweighs the risk; "
            "take SMASH_GRAB when you must be SURE of the jackpot. With a 2nd "
            "harvester you can do BOTH — the FIRST unit smashes, the SECOND sweeps."
        )
        if core_visioned:
            note_sweep += " Already in live vision -> drop STRAIGHT (no probe)."
        if weapons:
            note_sweep += (
                " Weapons about -> this sweep is trimmed SHORT; lean SMASH_GRAB."
            )
        sweep = SeamPattern(
            pattern_id="FULL_SWEEP",
            kind="FULL_SWEEP",
            beacon=beacon,
            mine=True,
            title="Grab the whole seam (1st harvester, H01) — the greedy gamble",
            when="H1: ride the visible seam (pures + mass) in one outing",
            rationale=(
                "The pure is one cell but its seam is dense with more pure/mass. "
                "One harvester CAN scoop the lot in a single walk for far more than "
                "the smash — but it is a gamble: a collision or jam mid-walk loses "
                "the unlifted cargo, and the danger grows with more players and as "
                "the season ends. Weigh the value against the risk; SMASH_GRAB is "
                "the sure alternative, and a 2nd harvester lets you take both."
            ),
            waves=[SeamWave(
                1, _H_SMASH, drop1, comb_full, probe_at=wave1_probe,
                direction=approach, unit_ordinal=0, note=note_sweep,
                pickup_after=True,
            )],
        )
        patterns.append(sweep)

    # ── H2 SECURE_MASS — a SHORT strip of the visible MASS ring around the pure.
    # The pure is one cell; the points live in the mass around it. Under weapons
    # the agent may instead RE-HIT the pure for safety (doctrine — geometry stays
    # the mass strip so the two harvesters never share the pure cell).
    # WAKE GUARD (OBS-42): wave 1 takes the pure, so wave 2 must open on the
    # RING. Excluded from both the drop choice and the comb, or the "mass strip"
    # re-enters our own stripped ground for -100 a cell.
    wave1_cells: Set[Tuple[int, int]] = {(int(drop1[0]), int(drop1[1]))}
    wave1_cells |= {(int(c[0]), int(c[1])) for c in comb1}
    # Fix 1.3 — come in from the far side of where wave 1's probe SITS, not the
    # opposite of its approach bearing. ``approach`` runs probe→beacon, so its
    # opposite points straight back at the probe: whenever the pure sits on the
    # beacon centre (an unjittered smear) both waves resolved to the same cell
    # and the second probe destroyed the first. Real boards mostly dodged this
    # by luck, because wave 1 anchors on the pure and wave 2 on the beacon.
    d2 = _OPPOSITE.get(_dir_name(beacon, probe1), "W")
    probe2 = _flank_probe(beacon, d2, width, height, avoid_center=emp)
    if wave1_probe is not None and probe2 == tuple(wave1_probe):
        for alt in (_PERP.get(d2, "S"), _OPPOSITE.get(_PERP.get(d2, "S"), "N")):
            cand = _flank_probe(beacon, alt, width, height, avoid_center=emp)
            if cand != tuple(wave1_probe):
                d2, probe2 = alt, cand
                break
    drop2 = _value_drop(agent_view, beacon, probe2, width, height,
                        exclude=wave1_cells)
    mass_steps = _SHORT_GRAB_STEPS if (contested or weapons) else _MASS_SHORT_STEPS
    comb2 = _comb_path(
        probe2[0], probe2[1], drop2, width, height, green | wave1_cells,
        value_cells, max_steps=mass_steps,
    )
    note2 = (
        "harvest the visible MASS ringing the pure — keep it SHORT (a secured "
        "second bank from the far side, not a seam crawl); land on the richest "
        "mass cell and lift"
    )
    if weapons:
        note2 += (
            "; weapons about — you MAY instead re-hit the pure for safety "
            "(worth banking a green to be SURE the jackpot is yours)"
        )
    secure = SeamPattern(
        pattern_id="SECURE_MASS",
        kind="SECURE_MASS",
        beacon=beacon,
        mine=True,
        title="Secure the mass ring (2nd harvester, H09)",
        when="2nd harvester: SHORT strip of the mass around your pure",
        rationale=(
            "The pure is a single cell; the seam's points are in the mass ring. A "
            "second harvester banks that ring on a short secured chain from the "
            "opposite angle. If weapons threaten the grab, re-hitting the pure is "
            "the safe alternative — the agent decides."
        ),
        waves=[SeamWave(
            1, _H_POST_EMP + spacing, drop2, comb2, probe_at=probe2,
            direction=d2, unit_ordinal=1, note=note2,
        )],
    )

    # ── H2 (alternative) SWEEP_RING — the GREEDY follow-up. Where SECURE_MASS
    # takes a short secured strip, this rides the whole visible mass ring and
    # then pushes into the UNLIT half of the pure's surround. That tail is the
    # point: a seam's value packs around its pure, so the cells we have never
    # seen touching it are the densest unknown on the board — not speculative
    # frontier. It is only a "blind" step in name: the wave's own probe is
    # placed on the unseen bearing, so the disk lights that ground before the
    # harvester walks it. Wave 1 already banked the pure, so a jam here costs
    # ring mass and never the jackpot — which is exactly why this unit can be
    # greedier than the grabber ever should be.
    h2_alt: List[SeamPattern] = []
    unseen = _unseen_surround(agent_view, drop1, width, height)
    if unseen:
        # Aim the probe at the fogged side so its disk covers the tail.
        cx = sum(c[0] for c in unseen) / float(len(unseen))
        cy = sum(c[1] for c in unseen) / float(len(unseen))
        d_sweep = _dir_name(drop1, (int(round(cx)), int(round(cy))))
        probe_s = _flank_probe(drop1, d_sweep, width, height, avoid_center=emp)
        # When the pure is fogged on EVERY side the centroid collapses onto the
        # pure itself and the bearing falls back to the default — which is the
        # bearing wave 1 already used, so both would launch a probe onto one
        # cell and destroy each other. Only WAVE 1 matters here: SECURE_MASS is
        # this option's alternative on the same harvester, so the two can never
        # run together and sharing a cell with it costs nothing.
        for alt in (d_sweep, _OPPOSITE.get(d_sweep, "W"), _PERP.get(d_sweep, "S")):
            cand = _flank_probe(drop1, alt, width, height, avoid_center=emp)
            if wave1_probe is None or cand != tuple(wave1_probe):
                d_sweep, probe_s = alt, cand
                break
        # Open on lit ground that TOUCHES the fog. Ranking the disk purely by
        # value picks the richest cell, which sits deep on the lit side and can
        # be at the far edge of the probe's reach — the comb then has nowhere to
        # go and the option quietly degrades into a worse SECURE_MASS. Landing
        # IN the fog is the other failure: it throws away the "start in mass"
        # half of the play and gambles the drop itself.
        drop_s = _value_drop(agent_view, beacon, probe_s, width, height,
                             exclude=wave1_cells)
        edge = [
            (int(c["at"][0]), int(c["at"][1]))
            for c in enumerate_value_ring(agent_view, beacon, probe_s, max_cells=16)
        ]
        edge = [
            c for c in edge
            if c != probe_s and c not in wave1_cells and c not in green
            and c not in unseen
        ]
        for reach in (1, 2):
            hit = next(
                (c for c in edge if min(_cheb_to(c, u) for u in unseen) == reach),
                None,
            )
            if hit is not None:
                drop_s = hit
                break
        # Mass ring FIRST, then the unlit surround — ``_comb_path`` heads for the
        # nearest value cell, so listing both lets it mop the visible mass on
        # the way out to the fog rather than bolting straight for the unknown.
        sweep_targets = list(value_cells) + unseen
        comb_s = _comb_path(
            probe_s[0], probe_s[1], drop_s, width, height, green | wave1_cells,
            sweep_targets, max_steps=_SEAM_SWEEP_STEPS,
        )
        # Offer it when the route actually REACHES the unlit ground — that tail
        # is the whole difference from SECURE_MASS. Gating on "longer than
        # SECURE_MASS" instead let a long walk that never leaves lit ground
        # qualify, and killed a short one that did.
        reaches = set(unseen) & {(int(c[0]), int(c[1])) for c in comb_s}
        if reaches and _cells_of(drop_s, comb_s) != _cells_of(drop2, comb2):
            unseen_txt = ", ".join(f"({c[0]},{c[1]})" for c in unseen[:4])
            note_s = (
                "GREEDY follow-up: work the visible mass ring, then push into "
                f"the UNLIT side of the pure — {unseen_txt}. Your wave probe is "
                "placed on that bearing, so the disk lights the ground before "
                "you walk it. The pure is already banked, so the worst case "
                "here is losing ring mass"
            )
            if weapons:
                note_s += (
                    ". WEAPONS ARE IN PLAY — this is a long chain; SECURE_MASS "
                    "is the short secured alternative for the same harvester"
                )
            h2_alt.append(SeamPattern(
                pattern_id="SWEEP_RING",
                kind="SWEEP_RING",
                beacon=beacon,
                mine=True,
                title="Sweep the ring + the unlit side (2nd harvester, H09) — the greedy follow-up",
                when="2nd harvester: all the visible mass, then into the pure's fog",
                rationale=(
                    "SECURE_MASS is the careful version of this wave and this is "
                    "the greedy one — same harvester, so pick one. A seam's "
                    "points sit in the ring around the pure, and the half of "
                    "that ring you have never lit is the best unknown on the "
                    "board: value clusters on the pure, so there is no reason to "
                    "be shy about it once the visible mass is in hand. The "
                    "jackpot is already banked by wave 1, which is what makes "
                    "the extra length affordable here and not on the first wave."
                ),
                waves=[SeamWave(
                    1, _H_POST_EMP + spacing, drop_s, comb_s, probe_at=probe_s,
                    direction=d_sweep, unit_ordinal=1, note=note_s,
                    blind_walk=True,
                )],
            ))

    # ── H3 LATE_SWEEP — a late, bigger pattern from a third bearing over the
    # dense seam (the only own-seam wave allowed a full-length comb).
    d3 = _PERP.get(approach, "S")
    probe3 = _flank_probe(beacon, d3, width, height, dist=4, avoid_center=emp)
    # Same wake guard, now against BOTH earlier waves.
    wave12_cells = wave1_cells | {(int(drop2[0]), int(drop2[1]))}
    wave12_cells |= {(int(c[0]), int(c[1])) for c in comb2}
    drop3 = _value_drop(agent_view, beacon, probe3, width, height,
                        exclude=wave12_cells)
    # A3 — under weapons keep even the late sweep short so it lifts inside the
    # safe window rather than riding a full comb into the jam.
    comb3 = _comb_path(
        probe3[0], probe3[1], drop3, width, height, green | wave12_cells,
        value_cells,
        **({"max_steps": _CHAFF_CHAIN_STEPS} if weapons else {}),
    )
    note3 = (
        "late, bigger pattern from a third bearing — mop the dense seam. With "
        "MULTIPLE weapons in play, repeat the mass+pure for redundancy; else "
        "chase the juiciest separated vein/mass"
    )
    if weapons:
        note3 += (
            "; weapons about — keep this chain SHORT and lift inside the safe "
            "window (A3), do not ride a long comb into the jam"
        )
    late = SeamPattern(
        pattern_id="LATE_SWEEP",
        kind="LATE_SWEEP",
        beacon=beacon,
        mine=True,
        title="Late sweep the seam (3rd harvester, H12)",
        when="3rd harvester / late: bigger pattern over the dense seam",
        rationale=(
            "Pure red bleeds into a dense halo. A late third-bearing sweep scoops "
            "the wider seam once the pure and near-mass are secured, and leaves "
            "great vision for tomorrow."
        ),
        waves=[SeamWave(
            1, _H_WALK_IN + spacing, drop3, comb3, probe_at=probe3,
            direction=d3, unit_ordinal=2, note=note3, pickup_after=weapons,
        )],
    )

    # SWEEP_RING sits beside SECURE_MASS: they are the careful and greedy uses
    # of the SAME second harvester, so the menu shows them as a pair.
    patterns.extend([secure, *h2_alt, late])
    return patterns


def _rival_deny_pattern(
    agent_view: Mapping[str, Any],
    hint: Mapping[str, Any],
    beacon: Tuple[int, int],
    threat: Mapping[str, Any],
    *,
    width: int,
    height: int,
    seat_index: int = 0,
) -> List[SeamPattern]:
    """Part B — the FOGGED-rival contest: CONFIRM + DENY, never a blind dive.

    We cannot see the rival's seam, so a harvester drop is a blind guess that
    lands on green if they have already stripped the pure (seed-56). Instead a
    single DENY-ONLY wave: supersede the finder's probe (blind them, deny their
    next-night harvest) and drop a fresh probe that COVERS the beacon (so we see
    the pure tomorrow and can strike it for real). No harvester is committed —
    the fleet works our own confirmed value tonight.
    """
    finder = _freshest_enemy_probe_in_smear(agent_view, beacon)
    sup = hint.get("supersede")
    supersede: Optional[Tuple[int, int]] = (
        (int(sup[0]), int(sup[1]))
        if isinstance(sup, (list, tuple)) and len(sup) == 2 else finder
    )
    # Confirm probe: cover the beacon from the finder's bearing when known (their
    # probe is nearest the pure), else from the east. Guaranteed to cover the
    # beacon so tomorrow's menu sees the pure.
    approach_dir = _dir_name(beacon, finder) if finder is not None else "E"
    probe1 = _flank_probe(beacon, approach_dir, width, height)
    # If the supersede cell IS the confirm-probe cell, one probe does both.
    if supersede is not None and tuple(supersede) == tuple(probe1):
        probe_at: Optional[Tuple[int, int]] = None
    else:
        probe_at = probe1
    note = (
        "FOGGED rival seam — you have NO live vision of the pure, and the rival "
        "may already have stripped it to green. Do NOT blind-drop a harvester "
        "here (that is how you land on green). Instead SUPERSEDE the finder's "
        "probe to blind + deny them, and drop your OWN probe to COVER the beacon "
        "so you SEE the pure tomorrow and strike it for real. Your harvesters "
        "stay on value you can actually see tonight."
    )
    if finder is None:
        note = (
            "FOGGED rival seam, finder's probe not visible — light it with a "
            "covering probe to confirm the pure for a real strike tomorrow "
            "rather than blind-dropping a harvester onto possible green."
        )
    deny = SeamPattern(
        pattern_id="CONTEST_DENY",
        kind="CONTEST_DENY",
        beacon=beacon,
        mine=False,
        title="Confirm & deny the rival seam (no blind drop)",
        when="you cannot SEE this seam; blind the finder + confirm for tomorrow",
        rationale=(
            "A rival's fogged beacon is a trap for a blind harvester: the pure "
            "may already be stripped to green, so a blind drop banks a penalty. "
            "The tempo-honest contest is to deny the finder (supersede) and "
            "confirm the seam (probe) tonight, then strike it with vision "
            "tomorrow — aggression without the self-harm."
        ),
        waves=[SeamWave(
            1, _H_SMASH, beacon, [], probe_at=probe_at, supersede=supersede,
            direction=approach_dir, unit_ordinal=0, deny_only=True, note=note,
        )],
    )
    return [deny]


def _rival_blind_attack_patterns(
    agent_view: Mapping[str, Any],
    hint: Mapping[str, Any],
    beacon: Tuple[int, int],
    threat: Mapping[str, Any],
    *,
    width: int,
    height: int,
    seat_index: int = 0,
) -> List[SeamPattern]:
    """CASE 2, FOGGED rival seam — ATTACK by default (v11 doctrine).

    A rival redsign is almost always exactly ONE night old and still unharvested
    (a frontier probe trips it; the finder usually cannot harvest until the next
    day), so the pure is very likely still LIVE — and the seam is MASS-RICH, so
    even a blind drop that misses the pure banks good vein/mass, while a hit is a
    double win (huge points + huge denial). So we OFFER a blind attack scaled by
    probes instead of only confirming for tomorrow:

      * ``BLIND_GRAB``     H1 — one covering probe (landed ON the finder to BLIND
                           it when the finder is known + near the pure, so one
                           probe both denies and enables the drop) + a full BLIND
                           WALK across the mass-rich smear, pick up fast.
      * ``UNBEATEN_FLANK`` a SECOND harvester + its own probe combs a DIFFERENT
                           sector of the seam from the opposite bearing (the two-
                           probe / two-harvester play). The first probe is likely
                           to be contested, so the second unit works its own probe.

    The caller appends the demoted :func:`_rival_deny_pattern` (CONTEST_DENY) as
    the "ahead / want-certainty" alternative, and — whatever the stock —
    :func:`_walkin_rival_patterns` when the seam is close enough to reach on
    foot. Zero probes is handled upstream: none of the patterns BELOW are
    offered, because you cannot land on a fogged seam without lighting it.
    """
    green = _known_green_cells(agent_view)
    emp = _emp_near(beacon, threat.get("emp_cells") or [])
    spacing = _EMP_CLOUD_HOURS if emp else 0
    weapons = bool(threat.get("chaff_seen")) or emp is not None
    finder = _freshest_enemy_probe_in_smear(agent_view, beacon)
    steps = _blind_grab_steps(threat, emp=emp)

    # ── BLIND_GRAB (H1): a covering probe off the beacon enables the blind drop.
    # Bias the covering bearing toward the finder so the grab aims at the pure's
    # most likely quadrant; the drop lands on the richest smear guess in the
    # probe's disk (still fogged — that is the accepted risk).
    approach = _dir_name(beacon, finder) if finder is not None else "E"
    probe1 = _flank_probe(beacon, approach, width, height, avoid_center=emp)
    ring = enumerate_value_ring(agent_view, beacon, probe1)
    drop1 = _best_core_cell(ring, probe1, beacon, green=green)
    value_cells = (
        [tuple(c["at"]) for c in ring] or list(_redsign_cells(agent_view).keys())
    )
    drop1, probe1 = _seat_offset_geom(
        drop1, probe1, seat_index, width, height, green,
    )
    # Free denial: when the finder's probe sits OFF the drop but still covers it,
    # land our ONE covering probe ON the finder — it blinds them AND makes the
    # drop legal (one probe does double duty). Otherwise keep the flank probe and
    # skip the supersede so the H1 attack stays a single-probe play.
    if (
        finder is not None
        and tuple(finder) != tuple(drop1)
        and tuple(finder) != tuple(probe1)
        and _covers(finder[0], finder[1], drop1[0], drop1[1])
    ):
        supersede1: Optional[Tuple[int, int]] = finder
        wave1_probe: Optional[Tuple[int, int]] = None
        cover_center = finder
    else:
        supersede1 = None
        wave1_probe = probe1
        cover_center = probe1
    approach = _dir_name(cover_center, beacon)

    # ── Fix 1.2 — TWO comb lengths, both always on the menu.
    # The length used to be chosen for the agent by the threat read (full hold
    # when quiet, floor under chaff), so a single id meant two different plays
    # and the agent could never ask for the other one. They are now separate
    # options; the threat read moved into the rationale, where it argues instead
    # of deciding. Under weapons the long comb is still SHOWN — it is a real
    # choice with a real downside, not an illegal move.
    def _blind_option(
        pid: str, title: str, max_steps: int, *, long_form: bool,
    ) -> SeamPattern:
        comb = _comb_path(
            cover_center[0], cover_center[1], drop1, width, height, green,
            value_cells, max_steps=max_steps,
        )
        note = (
            "BLIND attack on a FRESH, MASS-RICH rival seam — a redsign is usually "
            "one night old and unharvested, so the pure is very likely still "
            "there. The smear is jittered, so DROP on the best guess and "
        )
        note += (
            f"BLIND-WALK {len(comb)} cells across the seam: more ground means a "
            "real chance of crossing the pure, and a miss still banks the "
            "vein/mass you walk over. But you are out there for hours"
            if long_form else
            f"take just {len(comb)} step(s) and LIFT: you are betting on the drop "
            "cell itself and leaving fast. Lower odds of finding the pure, far "
            "less time exposed"
        )
        if supersede1 is not None:
            note += (
                f"; your covering probe lands ON the finder "
                f"({finder[0]},{finder[1]}) — it BLINDS them AND makes the drop "
                "legal (one probe, free denial)"
            )
        if long_form and (threat.get("chaff_seen") or emp is not None):
            note += (
                ". WEAPONS ARE IN PLAY: a jam mid-walk zeroes everything you have "
                "not lifted, so this long comb is the option they punish — the "
                "short grab is the hedge"
            )
        return SeamPattern(
            pattern_id=pid,
            kind=pid,
            beacon=beacon,
            mine=False,
            title=title,
            when=(
                "fogged but fresh + rich; blind the finder and comb the seam"
                if long_form else
                "fogged but fresh; blind the finder, take the drop cell and leave"
            ),
            rationale=(
                (
                    "A rival redsign is nearly always one night old and "
                    "unharvested, and the seam is mass-rich. The long comb buys "
                    "the best odds of actually crossing the pure — the smear is "
                    "jittered, so one cell rarely nails it — and banks whatever "
                    "it walks over on the way. You pay for that in hours on a "
                    "seam the owner is also racing for."
                ) if long_form else (
                    "Same bet, smaller stake. You take the single best guess and "
                    "lift, so a jam or a collision can cost you one cell instead "
                    "of a full hold. Prefer this when weapons are about, when "
                    "several rivals are converging, or when you simply cannot "
                    "afford to have a harvester tied up all night."
                )
            ),
            waves=[SeamWave(
                1, _H_SMASH, drop1, comb, probe_at=wave1_probe,
                supersede=supersede1, direction=approach, unit_ordinal=0,
                note=note, pickup_after=True, blind_walk=True,
            )],
        )

    blind_long = _blind_option(
        "BLIND_AND_GRAB",
        "Blind & grab — comb the whole seam (1st harvester, H01)",
        _BLIND_SWEEP_STEPS,
        long_form=True,
    )
    blind = _blind_option(
        "BLIND_GRAB",
        "Blind grab — one guess, in and out (1st harvester, H01)",
        _BLIND_SWEEP_FLOOR,
        long_form=False,
    )

    # ── UNBEATEN_FLANK (2nd harvester, H09): its OWN probe combs a
    # DIFFERENT sector from the opposite bearing (the 2-probe / 2-harvester play).
    #
    # OBS-54 — the flank blocked KNOWN green but not the first wave's OWN wake,
    # and the two waves are aimed at the same seam from opposite sides, so their
    # combs meet in the middle. On `V12_V11_R2_s56` night 5 the flank re-walked
    # four of the five cells BLIND_GRAB had just stripped, in reverse order, and
    # paid -100 on every one. The rationale promises "coverage, not a repeat";
    # this is what makes that true. Both blind lengths are candidates, so block
    # the union — whichever the agent picks, the flank stays off it.
    d_flank = _OPPOSITE.get(approach, "W")
    probe_f = _flank_probe(beacon, d_flank, width, height, avoid_center=emp)
    wave1_wake = set(green)
    for w in (blind.waves[0], blind_long.waves[0]):
        wave1_wake.add(tuple(w.drop_at))
        wave1_wake.update(tuple(c) for c in w.comb_path)
    drop_f = _value_drop(
        agent_view, beacon, probe_f, width, height, exclude=wave1_wake,
    )
    flank_steps = _CHAFF_CHAIN_STEPS if weapons else steps
    comb_f = _comb_path(
        probe_f[0], probe_f[1], drop_f, width, height, wave1_wake, value_cells,
        max_steps=flank_steps,
    )
    flank = SeamPattern(
        pattern_id="UNBEATEN_FLANK",
        kind="UNBEATEN_FLANK",
        beacon=beacon,
        mine=False,
        title="Unbeaten flank (2nd harvester, H09, own probe)",
        when="second unit: comb a DIFFERENT sector of the seam from the far side",
        rationale=(
            "The first probe on a contested seam is likely to be superseded or "
            "crashed, so a SECOND harvester brings its OWN probe and works a "
            "different sector of the mass-rich seam from the opposite bearing — "
            "coverage, not a repeat. Spend this only when you hold a spare "
            "harvester AND a spare probe."
        ),
        waves=[SeamWave(
            1, _H_POST_EMP + spacing, drop_f, comb_f, probe_at=probe_f,
            direction=d_flank, unit_ordinal=1, pickup_after=weapons,
            blind_walk=True,
            note=(
                "second bearing, own probe — blind-walk a different sector"
                if not weapons else
                "second bearing, own probe — weapons about, keep it SHORT and "
                "lift inside the safe window (A3)"
            ),
        )],
    )
    # Long comb first: on a fresh fogged seam it is still the default attack,
    # with the short grab beside it as the hedge rather than beneath it. Drop
    # the short one when the geometry makes the two identical (a cramped disk
    # can cut the long comb down to the floor), since a duplicate under a second
    # name reads as a real choice and is not one.
    if len(blind.waves[0].comb_path) >= len(blind_long.waves[0].comb_path):
        return [blind_long, flank]
    return [blind_long, blind, flank]


def _rival_patterns(
    agent_view: Mapping[str, Any],
    hint: Mapping[str, Any],
    beacon: Tuple[int, int],
    threat: Mapping[str, Any],
    *,
    seat_index: int = 0,
) -> List[SeamPattern]:
    """CASE 2 — a rival found it. Part B splits on whether we can SEE the seam:

      * LIVE-confirmed (a live probe of ours already reveals red on the seam):
        contest AGGRESSIVELY — blind-grab / flank / walk-in land on KNOWN value,
        offset + staggered by seat.
      * FOGGED to us (no live red near the beacon — the usual rival case): the
        seam is almost always ONE night old + mass-rich, so ATTACK by default —
        offer a BLIND attack (blind the finder + blind-walk the seam) scaled by
        probes, PLUS a demoted CONTEST_DENY (supersede finder + confirm for a
        guaranteed-vision strike tomorrow) as the "ahead / want-certainty"
        alternative. Known-stripped GREEN is still refused at compile time, so
        the accepted risk is only FOG, not a known penalty.
    """
    width, height = _grid_dims(agent_view)
    green = _known_green_cells(agent_view)
    emp = _emp_near(beacon, threat.get("emp_cells") or [])
    spacing = _EMP_CLOUD_HOURS if emp else 0
    # A3 — weapons read: a chaff jam last night or an EMP scar near the seam
    # shortens the later secured waves so they lift inside the safe window.
    weapons = bool(threat.get("chaff_seen")) or emp is not None

    # ── Part B confirm gate ────────────────────────────────────────────────
    # FOGGED rival seam: ATTACK by default (fresh + mass-rich), then offer the
    # demoted CONTEST_DENY alternative. The blind attack accepts FOG risk but the
    # packager still refuses KNOWN stripped/green drops + steps (hazard memory).
    if _live_core(agent_view, beacon) is None:
        attack = _rival_blind_attack_patterns(
            agent_view, hint, beacon, threat,
            width=width, height=height, seat_index=seat_index,
        )
        deny = _rival_deny_pattern(
            agent_view, hint, beacon, threat,
            width=width, height=height, seat_index=seat_index,
        )
        return attack + deny

    # BLIND_GRAB: overwrite the finder's probe, then a short blind drop. We stage
    # behind our OWN fresh probe (force_probe) — but if we happen to already see
    # the pure, land on that exact cell rather than a blind in-disk guess.
    #
    # M3: anchor CASE-2 geometry to the discoverer's PROBE, not the jittered
    # beacon. The finder's probe (now visible after the E2 fix) is the best pure
    # triangulation we have — supersede THAT cell and drop toward it.
    finder = _freshest_enemy_probe_in_smear(agent_view, beacon)
    sup = hint.get("supersede")
    supersede = (
        (int(sup[0]), int(sup[1]))
        if isinstance(sup, (list, tuple)) and len(sup) == 2 else finder
    )
    # Fix 1.4 (OBS-39) — LIVE sight only. An echo is not sight, and calling it
    # sight produced both a mislabelled option and a false note (see
    # ``_live_pure_core``).
    seen_pure = _live_pure_core(agent_view, beacon)
    core_visible = seen_pure is not None
    if not core_visible and finder is not None:
        # Triangulate the fogged pure from the finder's bearing.
        drop1, probe1, value_cells = _triangulated_blind_geometry(
            agent_view, beacon, finder,
        )
    else:
        drop1, probe1, _wave1_probe, value_cells = _wave1_grab_geometry(
            agent_view, beacon, hint, force_probe=True,
        )
    # R3: fan the blind grab by seat so mirror seats attack distinct cells.
    drop1, probe1 = _seat_offset_geom(
        drop1, probe1, seat_index, width, height, green,
    )
    approach = _dir_name(probe1, beacon)
    # Two sub-cases. If we can already SEE the pure (rare for a rival beacon), it
    # is a KNOWN target -> snatch it like a smash (danger-gated, 0 ok). If it is
    # still fogged (the usual case), the drop is a GUESS off the jittered smear,
    # so comb a SHORT SWEEP across the top candidates to actually bank the pure —
    # "a bit more than one move" — never zero.
    core_seen = core_visible
    if core_seen:
        grab_steps = _grab_steps(threat, contested=True, emp=emp)
        # Land ON the pure we can see. ``_wave1_grab_geometry`` aims at
        # ``_known_core``, which may be an ECHO cell somewhere else entirely;
        # when we hold LIVE sight of the pure, that cell is the target and there
        # is nothing to guess about.
        drop1 = seen_pure
    else:
        grab_steps = _blind_grab_steps(threat, emp=emp)
    comb1 = _comb_path(
        probe1[0], probe1[1], drop1, width, height, green, value_cells,
        max_steps=grab_steps,
    )
    # R2.5 — the coverage rule. Keep the launch only if it is buying legality
    # the wave does not already have, or a rival probe's life.
    wave1_probe = _trim_redundant_probe(agent_view, drop1, probe1, supersede)
    if core_seen:
        note_blind = (
            f"you can SEE the pure at ({drop1[0]},{drop1[1]}) RIGHT NOW — this is "
            "NOT a gamble. DROP ON it (the landing auto-harvests = the grab) and "
            "PICK UP IMMEDIATELY; there is nothing to sweep for"
        )
    else:
        note_blind = (
            "blind snatch — the beacon is a JITTERED smear, so one drop cell "
            "rarely IS the pure: DROP on the richest guess, then COMB a SHORT "
            "sweep (2-3 cells) across the neighbouring candidates to actually "
            "bank the pure, and PICK UP right after. Deny them + take the core "
            "fast — do NOT ride a long chain, a contested seam gets crashed"
        )
        if finder is not None:
            note_blind += (
                f"; the finder's probe is at ({finder[0]},{finder[1]}) — the pure "
                "is near IT, so this grab is aimed at the finder's cell (not the "
                "beacon centre) and supersedes it to blind them"
            )
    if threat.get("chaff_seen"):
        note_blind += " (chaff about — keep the sweep to the floor + flank backup)"
    # Fix 1.4 (OBS-39) — the NAME has to match the play. Selling a snatch at a
    # pure in plain sight as a "blind grab" made the agent price a certainty as
    # a gamble and fold to safer chains; the two cases are different plays and
    # now carry different ids, titles and rationales.
    blind = SeamPattern(
        pattern_id="SEEN_GRAB" if core_seen else "BLIND_GRAB",
        kind="SEEN_GRAB" if core_seen else "BLIND_GRAB",
        beacon=beacon,
        mine=False,
        title=(
            "Seen grab (the rival's pure is in YOUR sight)" if core_seen
            else "Blind grab (contest the finder)"
        ),
        when=(
            "you can SEE their pure and reach it — land on it and lift"
            if core_seen
            else "you can reach the beacon now; blind the finder's probe and snatch"
        ),
        rationale=(
            (
                "Nothing about this is a gamble: their pure is under YOUR live "
                "vision, so you know the exact cell. Drop on it, auto-harvest, "
                "and lift — the first-hour landing is the one thing on a "
                "contested seam that cannot be taken off you."
            ) if core_seen else (
                "The rival is piggybacking their own probe. Supersede it to blind "
                "them, then take a blind drop and a SHORT SWEEP (2-3 cells) — the "
                "smear is jittered, so a single cell rarely nails the pure; the "
                "sweep raises the hit chance — then pick up early. You deny them and "
                "bank the core even if the exact pure eludes the first cell."
            )
        ),
        waves=[SeamWave(
            1, _H_SMASH, drop1, comb1, probe_at=wave1_probe, supersede=supersede,
            direction=approach, unit_ordinal=0, note=note_blind,
            pickup_after=True,
            # Part A2 — the blind sweep is the classic "step onto a fogged
            # neighbour that may be stripped green" risk; the packager keeps the
            # sweep on cells confirmed live-red at plan time.
            contested=not core_seen,
        )],
    )

    # UNBEATEN_FLANK: fresh probe on the OPPOSITE axis, secured bank post-EMP.
    # OBS-54 — block wave 1's whole route, not just known green: two waves aimed
    # at one seam from opposite bearings meet in the middle, and the second then
    # pays -100 a cell on ground the first just stripped.
    d_flank = _OPPOSITE.get(approach, "W")
    probe_f = _flank_probe(beacon, d_flank, width, height, avoid_center=emp)
    wave1_wake = set(green) | {tuple(drop1)} | {tuple(c) for c in comb1}
    drop_f = _value_drop(
        agent_view, beacon, probe_f, width, height, exclude=wave1_wake,
    )
    comb_f = _comb_path(
        probe_f[0], probe_f[1], drop_f, width, height, wave1_wake, value_cells,
        **({"max_steps": _CHAFF_CHAIN_STEPS} if weapons else {}),
    )
    flank = SeamPattern(
        pattern_id="UNBEATEN_FLANK",
        kind="UNBEATEN_FLANK",
        beacon=beacon,
        mine=False,
        title="Unbeaten flank (2nd harvester, H09, secured bank)",
        when="the beacon cell is a pile-up; come in from the opposite axis, later",
        rationale=(
            "Everyone crashes the advertised cell. A fresh probe on the far side "
            "reaches the same seam from an uncontested angle after the EMP "
            "window — the wave nobody is fighting for."
        ),
        waves=[SeamWave(
            1, _H_POST_EMP + spacing, drop_f, comb_f, probe_at=probe_f,
            direction=d_flank, unit_ordinal=1, pickup_after=weapons,
            note=(
                "uncontested approach; longer chain into the dense seam"
                if not weapons else
                "uncontested approach; weapons about — keep it SHORT and lift "
                "inside the safe window (A3)"
            ),
        )],
    )

    # WALK_IN: outer mop-up from a third bearing, late. OBS-54 — it follows BOTH
    # earlier waves, so it blocks both wakes.
    d_walk = _PERP.get(approach, "S")
    probe_w = _flank_probe(beacon, d_walk, width, height, dist=4, avoid_center=emp)
    wake_12 = wave1_wake | {tuple(drop_f)} | {tuple(c) for c in comb_f}
    drop_w = _value_drop(
        agent_view, beacon, probe_w, width, height, exclude=wake_12,
    )
    comb_w = _comb_path(
        probe_w[0], probe_w[1], drop_w, width, height, wake_12, value_cells,
        **({"max_steps": _CHAFF_CHAIN_STEPS} if weapons else {}),
    )
    walk = SeamPattern(
        pattern_id="WALK_IN",
        kind="WALK_IN",
        beacon=beacon,
        mine=False,
        title="Walk-in (outer mop-up)",
        when="third harvester / late night; sweep the good red around the pure",
        rationale=(
            "Pure red bleeds into plenty of good red. A late walk-in from a "
            "third bearing scoops that halo even if the pure itself is contested "
            "— and leaves you great vision for tomorrow."
        ),
        waves=[SeamWave(
            1, _H_WALK_IN + spacing, drop_w, comb_w, probe_at=probe_w,
            direction=d_walk, unit_ordinal=2, pickup_after=weapons,
            note=(
                "slightly outside, different angle — bank the halo"
                if not weapons else
                "slightly outside, different angle — weapons about, keep it "
                "SHORT and lift inside the safe window (A3)"
            ),
        )],
    )
    return [blind, flank, walk]


def _pattern_probe_cost(p: SeamPattern) -> int:
    """How many fresh probe launches a pattern consumes (probe_at + supersede)."""
    cost = 0
    for w in p.waves:
        if w.probe_at is not None:
            cost += 1
        if w.supersede is not None:
            cost += 1
    return cost


def planned_probe_cells(
    patterns: Sequence[SeamPattern],
) -> List[Tuple[int, int]]:
    """Every cell the offered seam menu commits a PROBE to this night.

    The union of each wave's ``probe_at`` and ``supersede`` across all offered
    patterns — the vision the redsign attack already buys. The harness feeds
    these to the frontier probe sampler as no-go ground so an exploration probe
    can never stack next to a probe the blind attack already drops there.
    """
    out: List[Tuple[int, int]] = []
    seen: Set[Tuple[int, int]] = set()
    for p in patterns or []:
        for w in getattr(p, "waves", None) or []:
            for c in (w.probe_at, w.supersede):
                if isinstance(c, (list, tuple)) and len(c) == 2:
                    cell = (int(c[0]), int(c[1]))
                    if cell not in seen:
                        seen.add(cell)
                        out.append(cell)
    return out


def planned_harvest_cells(
    patterns: Sequence[SeamPattern],
) -> Set[Tuple[int, int]]:
    """Every cell the offered seam menu already puts a HARVESTER on tonight.

    The union of each wave's ``drop_at`` and ``comb_path``. Unlike beacon
    PROXIMITY, this is the honest test for "is this cell already covered by an
    option" — a redsign is minted on first sighting and persists all season, so
    proximity to a smear says nothing about whether anything actually walks the
    cell. Deny-only waves are excluded: they spend a probe and drop no unit.
    """
    out: Set[Tuple[int, int]] = set()
    for p in patterns or []:
        for w in getattr(p, "waves", None) or []:
            if getattr(w, "deny_only", False):
                continue
            for c in [w.drop_at, *(w.comb_path or [])]:
                if isinstance(c, (list, tuple)) and len(c) == 2:
                    out.add((int(c[0]), int(c[1])))
    return out


def planned_supersede_cells(
    patterns: Sequence[SeamPattern],
) -> List[Tuple[int, int]]:
    """Every ENEMY-probe cell the offered seam menu already BLINDS this night.

    Only each wave's ``supersede`` cell (a cell where the seam maneuver lands ON a
    rival's probe to destroy its vision) — NOT the covering ``probe_at`` cells. A
    stand-alone SUPERSEDE (SS) option targeting one of these is redundant: the
    chosen seam pattern already denies it, so a second probe on the same cell buys
    nothing. The harness feeds these to ``top_supersede_hints`` as an exclusion so
    the SS menu never offers a blind the seam attack already performs for free.
    """
    out: List[Tuple[int, int]] = []
    seen: Set[Tuple[int, int]] = set()
    for p in patterns or []:
        for w in getattr(p, "waves", None) or []:
            c = w.supersede
            if isinstance(c, (list, tuple)) and len(c) == 2:
                cell = (int(c[0]), int(c[1]))
                if cell not in seen:
                    seen.add(cell)
                    out.append(cell)
    return out


def build_seam_menu(
    agent_view: Mapping[str, Any],
    hot_drop_hints: Sequence[Mapping[str, Any]],
    *,
    max_beacons: int = 2,
    seat_index: int = 0,
    probe_stock: Optional[int] = None,
    harvesters_alive: Optional[int] = None,
) -> List[SeamPattern]:
    """Build the redsign pattern menu — REGION-driven so a rival's fogged beacon
    is on the menu too (the dual-redsign gap).

    The old menu was driven only by the ranked hot-drop HINTS, so a redsign that
    never produced a drop hint (a rival's discovery still in fog) generated NO
    pattern — the thinker could SEE the second beacon but had nothing to select
    for it, and both harvesters converged on the one beacon that did. We now
    enumerate the authoritative live regions (``agent_view['redsign']``), pair
    each with its nearest redsign hint for richer geometry (fogged regions get a
    synthesised centre hint), then FOLD IN any redsign hint a region did not
    already cover so nothing the old path produced is lost.

    Ordering: MINE first (smash-grab is THE priority), then rivals. IDs of the
    second source are suffixed ``#2`` so they stay unique (``SMASH_GRAB`` for the
    own seam, ``BLIND_GRAB#2`` for the rival's). Empty when no redsign is in play.
    """
    regions = _redsign_regions(agent_view)
    threat = _threat_context(agent_view)
    # Probe budget for this night (view carries it; tests without the key get a
    # generous default so existing fixtures keep offering the full trio).
    ps = (
        probe_stock if probe_stock is not None
        else int((agent_view.get("probe_stock", 99)) or 0)
    )

    red_hints = [
        h for h in (hot_drop_hints or [])
        if isinstance(h, Mapping)
        and str(h.get("signal_type") or "") == "redsign"
        and _hint_beacon(h) is not None
    ]

    # Assemble ordered SOURCES = {beacon, mine, hint}, de-duped by region so two
    # smear cells of the SAME beacon never spawn a duplicate pattern set.
    sources: List[Dict[str, Any]] = []
    seen: List[Tuple[int, int]] = []

    def _covered(b: Tuple[int, int]) -> bool:
        return any(
            max(abs(b[0] - s[0]), abs(b[1] - s[1])) <= _REGION_MATCH_RADIUS
            for s in seen
        )

    for r in regions:
        beacon = r.get("center")
        if not isinstance(beacon, tuple) or _covered(beacon):
            continue
        seen.append(beacon)
        hint = _match_hint(beacon, red_hints)
        mine = r.get("mine")
        if mine is None and hint is not None:
            mine = hint.get("mine")
        if mine is None:
            mine = _match_mine(beacon, regions)
        sources.append({
            "beacon": beacon,
            "mine": mine,
            "hint": hint or {"drop_at": [beacon[0], beacon[1]], "mine": mine},
        })

    # Fold in hint-only beacons the regions did not cover (keeps the old
    # hint-driven behaviour for fixtures/views without a ``redsign`` region list).
    for hint in red_hints:
        beacon = _hint_beacon(hint)
        if beacon is None or _covered(beacon):
            continue
        seen.append(beacon)
        mine = hint.get("mine")
        if mine is None:
            mine = _match_mine(beacon, regions)
        sources.append({"beacon": beacon, "mine": mine, "hint": hint})

    # MINE first (priority), then rivals; stable within each group.
    sources.sort(key=lambda s: 0 if s.get("mine") is True else 1)

    patterns: List[SeamPattern] = []
    emitted = 0  # count NON-EMPTY groups so suffixing tracks what's shown, not
    #             the raw source index (a starved own-seam must not push the
    #             rival group to a misleading "#2").
    # v13 — resolve harvesters_alive once for the case-C guard; the caller may
    # not have wired the argument through yet, in which case we assume >=2 (the
    # pattern's own two-harvester guard is redundant with the packager's
    # ``next_harvester()`` return-None branch, so a wrong-side call here is
    # safe: the pattern will simply drop its third wave).
    n_alive = int(harvesters_alive) if harvesters_alive is not None else 99

    def _region_for(beacon: Tuple[int, int]) -> Mapping[str, Any]:
        # Return the RAW agent_view region (with its ``cells`` list intact),
        # not the summarised one from ``_redsign_regions`` above. The compound
        # EMP patterns need the smear geometry, which the summariser strips.
        #
        # Widen the match to Chebyshev ``_REGION_MATCH_RADIUS`` (same window
        # the region summariser uses to attribute hot-drops to a beacon) —
        # a hint-driven beacon may be a rounded float that no longer equals
        # the region's own center exactly, and a strict equality miss silently
        # drops every compound pattern for the seat.
        best: Optional[Tuple[int, Mapping[str, Any]]] = None
        for r in (agent_view.get("redsign") or []):
            if not isinstance(r, Mapping):
                continue
            c = r.get("center")
            if isinstance(c, (list, tuple)) and len(c) == 2:
                try:
                    cx, cy = int(round(float(c[0]))), int(round(float(c[1])))
                except (TypeError, ValueError):
                    continue
                d = max(abs(cx - beacon[0]), abs(cy - beacon[1]))
                if d <= _REGION_MATCH_RADIUS and (best is None or d < best[0]):
                    best = (d, r)
        return best[1] if best is not None else {}

    for src in sources[:max_beacons]:
        beacon, hint, mine = src["beacon"], src["hint"], src.get("mine")
        region = _region_for(beacon)
        if mine is True:
            group = _mine_patterns(agent_view, hint, beacon, threat, probe_stock=ps)
        else:
            # A rival's seam normally needs a fresh probe (blind the finder +
            # light the fogged pure); with an empty magazine those patterns are
            # undroppable, so we offer none rather than geometry the packager
            # can't emit. The exception is a seam close enough to WALK into from
            # our own live frontier — that costs no probe, so it both rescues
            # the zero-stock night and stacks with the probe attacks when we do
            # have stock.
            group = [] if ps < 1 else _rival_patterns(
                agent_view, hint, beacon, threat, seat_index=seat_index,
            )
            w, h = _grid_dims(agent_view)
            emp_r = _emp_near(beacon, threat.get("emp_cells") or [])
            group = group + _walkin_rival_patterns(
                agent_view, beacon, threat,
                width=w, height=h, green=_known_green_cells(agent_view),
                spacing=_EMP_CLOUD_HOURS if emp_r else 0,
                weapons=bool(threat.get("chaff_seen")) or emp_r is not None,
                have_probes=ps >= 1,
            )

        # v13 — EMP COMPOUND PATTERNS. Classified by redsign state; prepended
        # so they appear ABOVE the base case-1/case-2 book when applicable.
        # The pattern builders each return None when their preconditions are
        # not met (no charge in the rack, no known core, chaff in play for
        # case C, etc.), so this loop is safely additive.
        #
        # Defensive: any exception raised here (a fixture shape we did not
        # anticipate, a scorch geometry corner case) MUST NOT propagate out
        # — the compound patterns are an extra layer, and dropping them for
        # this seat is strictly better than crashing the whole harness turn.
        # A crash here is what routes the seat to _heuristic_fallback, which
        # is the worst outcome available on the board.
        emp_extras: List[SeamPattern] = []
        emp_trace: List[str] = []
        # Case starts as ``unknown`` and is overwritten the moment the classifier
        # returns successfully. That way an exception in the classifier itself
        # leaves the diagnostic sink with a meaningful reason string, and the
        # try/except cleanup does not have to defend a downstream index.
        case = "unknown"
        # ``rival_blind`` is handled by the existing BLIND_SCORCH option in
        # _emp_options (agency.py) — its H3 hole-drop dominates any delayed
        # cloud-lift landing (see doctrine). No compound pattern is emitted
        # for that case; the case-A doctrine block points at BLIND_SCORCH
        # by name. Compound plays exist only for case B (mine_alone /
        # mine_contested — SMASH_THEN_LOCK) and case C (shared_vision /
        # mine_contested — RACE_CRASH_EMP) where BLIND_SCORCH cannot
        # express the required timing.
        try:
            case = _classify_redsign_case(
                agent_view, hint or {}, beacon, region,
            )
            emp_trace.append(f"case={case}")
            if case == "mine_alone":
                p_b = _pattern_smash_then_lock(
                    agent_view, beacon, hint or {}, threat,
                )
                if p_b is not None:
                    emp_extras.append(p_b)
            elif case == "shared_vision" or case == "mine_contested":
                p_c = _pattern_race_crash_emp(
                    agent_view, beacon, hint or {}, threat,
                    harvesters_alive=n_alive,
                )
                if p_c is not None:
                    emp_extras.append(p_c)
                if case == "mine_contested":
                    p_b = _pattern_smash_then_lock(
                        agent_view, beacon, hint or {}, threat,
                    )
                    if p_b is not None:
                        emp_extras.append(p_b)
        except Exception as e:  # pragma: no cover — defensive; surface on card
            import logging
            logging.getLogger(__name__).exception(
                "EMP compound pattern generation failed for beacon %r; "
                "skipping and falling through to the base pattern book",
                beacon,
            )
            emp_trace.append(f"EXCEPTION: {type(e).__name__}: {e}")
            emp_extras = []

        # v13 diagnostic — surface a single-line reason on the CARD when a
        # compound was EXPECTED (redsign live, charge in the rack, case B/C)
        # but did not fire. This is a triage tool while the patterns are new;
        # drop the block once the code stabilises. Never emit for case A
        # (rival_blind) — BLIND_SCORCH covers it, no compound is expected.
        _expects_compound = {"mine_alone", "mine_contested", "shared_vision"}
        if (not emp_extras
                and case in _expects_compound
                and scorch.stock(agent_view).get("emp", 0) > 0):
            trace_text = " | ".join(emp_trace) if emp_trace else f"case={case}"
            emp_extras.append(SeamPattern(
                pattern_id="EMP_COMPOUND_DIAG",
                kind="diag",
                beacon=beacon,
                mine=None,
                title="[diag] EMP compound not offered — why",
                when=f"beacon @({beacon[0]},{beacon[1]}) — {trace_text}",
                rationale=(
                    "This is a diagnostic entry, not a play. The compound EMP "
                    "patterns (SMASH_THEN_LOCK / RACE_CRASH_EMP) are offered "
                    "when a redsign is live, a matching case applies, and you "
                    "hold a charge. If you see this line, one of the "
                    "preconditions failed — the reason is in the ``when`` "
                    "line above. Ignore this ID; do NOT select it."
                ),
                waves=[],
            ))
        group = emp_extras + group

        if not group:
            continue
        suffix = "" if emitted == 0 else f"#{emitted + 1}"
        for p in group:
            if suffix:
                p.pattern_id = f"{p.pattern_id}{suffix}"
            patterns.append(p)
        emitted += 1

    # PROBE BUDGET (the user's "only one probe -> you can do only one of these"
    # guard). Walk the assembled patterns in menu order and drop any probe-gated
    # HARVESTER-committing pattern once the night's probe stock is spent — walk-in
    # patterns cost 0 and always survive, so the fleet is never stranded on
    # undroppable geometry.
    #
    # DENY-ONLY patterns (CONTEST_DENY) are the demoted ALTERNATIVE to the blind
    # attack on the SAME seam, not an additional consumer — so they do NOT draw
    # from the cumulative attack budget. Keep one whenever its own cost fits the
    # night's stock, so the "ahead / want-certainty" option stays on the menu
    # alongside the attack it competes with.
    budget = ps
    kept: List[SeamPattern] = []
    for p in patterns:
        cost = _pattern_probe_cost(p)
        if cost == 0:
            kept.append(p)
            continue
        deny_only = bool(p.waves) and all(w.deny_only for w in p.waves)
        if deny_only:
            if ps >= cost:
                kept.append(p)
            continue
        if budget >= cost:
            budget -= cost
            kept.append(p)
        # else: no probes left for this probe-gated attack pattern -> drop it.
    return kept
