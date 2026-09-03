"""v10 Fix A — seat-differentiated, ownership-aware hot-drop dispersion.

THE PROBLEM (measured, seed 42):
    ``probe_hints.top_hot_drop_hints`` is a PURE function of the board, and
    every agent version imports the same module. Given one public REDSIGN
    beacon, all seats compute the byte-identical ``drop_at`` AND the identical
    nearest-first ``alt_drops``. So even when the agent obediently "offsets onto
    the seam", every seat offsets onto the SAME seam cell -> SIMULTANEOUS DROP
    COLLISION -> 0 banked for everyone (observed n-way on the shared beacon, and
    still 2-way on the offset cells night 7 of the 1v1).

    You cannot break a symmetric collision by handing every player the same
    ranked menu in the same order. It needs either per-seat asymmetry or the
    mine/not-mine poker producing genuinely DIFFERENT primary plays.

THE FIX (v10-only, deterministic — NO hidden RNG on the final placement):
    * Attach engine-truth ownership (``mine``) to each redsign hot-drop by
      matching it to the redsign region it targets.
    * MY redsign  -> CASE 1: leave the primary drop on/adjacent the pure. I
      found it, I move first; smash-and-grab is correct.
    * NOT my redsign -> CASE 2: each seat is assigned a DIFFERENT seam offset,
      chosen by rotating the (already deterministic) ``alt_drops`` list by a
      stable index derived from the seat's own id. p1 leads with offset 0, p2
      with offset 1, p3 with offset 2 — so three rivals fan out onto three
      distinct seam cells instead of stacking. The assignment is a stable,
      explainable "approach angle", not a coin flip.

This only rewrites v10's hint payloads in-process; the shared compiler and the
frozen v6/v7/v8 harnesses are untouched.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from sea_of_colours.orchestrator_2.harnesses.sagar_coco._v7.probe_hints import (
    _grid_dims,
    _known_green_cells,
)

# Chebyshev radius within which a redsign hot-drop is considered to be
# "targeting" a given redsign region (the vision disk half-width).
_REGION_MATCH_RADIUS = 4

# Minimum Chebyshev gap between two seats' hot-drop cells. Must exceed the short
# blind-grab comb reach (1-2 steps) so a harvester never walks onto a rival
# seat's drop cell (the seed-69 day-1 adjacent-drop clip). Used by the fallback
# ring; the pincer below satisfies it by construction.
_HOTDROP_MIN_SEP = 3

# Distinct approach bearings for the flanking waves — cardinals first (cleanest
# disjoint spokes), then diagonals. The lead seat sits on the pure; flanker k
# takes bearing k-1.
_APPROACH_BEARINGS: List[Tuple[int, int]] = [
    (1, 0), (0, 1), (-1, 0), (0, -1),
    (1, 1), (-1, -1), (1, -1), (-1, 1),
]

# How far off the pure a flanking wave drops before sweeping inward. Chosen so
# two flankers on adjacent bearings are ≥3 apart (disjoint short combs) while
# still landing on the seam disk (radius ~4), not exiled to empty fog.
_PINCER_RADIUS = 3


def seat_index(player: str) -> int:
    """Stable per-seat rotation index.

    ``p1``/``p2``/``p3``/``p4`` map to 0/1/2/3 (the common arena seating), so
    the fan-out is trivially legible in a head-to-head. Any other id falls back
    to a stable hash so the property (distinct seats -> distinct index) holds.
    """
    s = str(player or "").strip().lower()
    if len(s) == 2 and s[0] == "p" and s[1].isdigit():
        return max(0, int(s[1]) - 1)
    return abs(hash(s)) % 97


# Seat fan-out ring around a public beacon, ordered so early seats stay closest
# to the pure and later seats spread to fresh bearings. Index 0 = stay put (the
# lead seat keeps the smash-and-grab primary); the rest are distinct cells.
_FANOUT_RING: List[Tuple[int, int]] = [
    (0, 0), (1, 0), (0, 1), (-1, 0), (0, -1),
    (1, 1), (-1, -1), (1, -1), (-1, 1),
    (2, 0), (0, 2), (-2, 0), (0, -2),
]

# WIDE ring for a hot-drop whose pure is NOT yet known (day-1 blind/bluesign
# play): the seats aren't racing a shared pure cell, so they should spread far
# enough that their short comb walks never overlap. A 1-cell offset (the tight
# ring) still let p1's comb step onto p2's adjacent drop (seed-69 day-1 clip);
# ≥4 apart keeps the whole 2-3 cell blind grab disjoint. Only used off-redsign.
_WIDE_FANOUT_RING: List[Tuple[int, int]] = [
    (0, 0), (4, 0), (0, 4), (-4, 0), (0, -4),
    (4, 4), (-4, -4), (4, -4), (-4, 4),
    (6, 0), (0, 6), (-6, 0), (0, -6),
]

# Probes want a WIDER seat nudge than drops — the drop must stay near the pure,
# but an exploration probe just needs fresh, non-overlapping fog per seat.
_SEAT_PROBE_RING: List[Tuple[int, int]] = [
    (0, 0), (2, 0), (0, 2), (-2, 0), (0, -2),
    (2, 2), (-2, -2), (2, -2), (-2, 2),
]


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _cheby(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _fanout_candidates(
    primary: Tuple[int, int],
    alts: Sequence[Tuple[int, int]],
    width: int,
    height: int,
    green: "set",
    ring: Sequence[Tuple[int, int]] = _FANOUT_RING,
    min_sep: int = 0,
) -> List[Tuple[int, int]]:
    """Ordered, deduped, well-SEPARATED drop cells around a public beacon.

    ``primary`` (the pure guess) leads so the FIRST seat keeps the smash-and-grab;
    then the compiler's ranked ``alt_drops`` (real seam cells); then a synthetic
    ``ring`` so even a single-guess day-1 beacon still offers a distinct cell per
    seat (the day-1 mirror pile-up had no alts to rotate). Green cells are skipped.

    ``min_sep`` (Chebyshev) guarantees each accepted cell is at least that far
    from EVERY already-accepted cell. Distinct-but-adjacent drops still clip
    because a harvester's short comb walk steps onto the neighbour's drop cell
    (seed-69 day-1: 35,21 vs 34,21 → p1 walked into p2). Separating by ≥ the comb
    reach keeps the translated walks disjoint. The lead cell (``primary``) is
    always kept; separation is only enforced on the followers.
    """
    out: List[Tuple[int, int]] = []

    def _add(c: Tuple[int, int]) -> None:
        if c in out or c in green:
            return
        if min_sep > 0 and any(_cheby(c, p) < min_sep for p in out):
            return
        out.append(c)

    _add(primary)
    for a in alts:
        _add(a)
    cx, cy = primary
    for dx, dy in ring:
        _add((_clamp(cx + dx, 0, width - 1), _clamp(cy + dy, 0, height - 1)))
    return out


def _region_center(region: Mapping[str, Any]) -> Optional[Tuple[float, float]]:
    c = region.get("center")
    if isinstance(c, (list, tuple)) and len(c) == 2:
        try:
            return float(c[0]), float(c[1])
        except (TypeError, ValueError):
            return None
    return None


def _hint_target(hint: Mapping[str, Any]) -> Optional[Tuple[int, int]]:
    """Best estimate of the beacon cell a hot-drop is chasing.

    The primary ``drop_at`` sits on or 1 cell off the beacon, so it is a
    reliable anchor for matching the hint back to its redsign region.
    """
    d = hint.get("drop_at")
    if isinstance(d, (list, tuple)) and len(d) == 2:
        try:
            return int(d[0]), int(d[1])
        except (TypeError, ValueError):
            return None
    return None


def _matched_center(
    primary: Tuple[int, int], regions: Sequence[Mapping[str, Any]],
) -> Tuple[int, int]:
    """The pure-seam anchor a flanking wave should pincer.

    Prefer the engine-truth redsign region centre closest to ``primary`` (the
    real seam, even when the compiler's blind drop guess is a few cells off);
    fall back to ``primary`` itself for a fog/bluesign play with no region yet.
    """
    tx, ty = primary
    best: Optional[Tuple[float, Tuple[int, int]]] = None
    for r in regions:
        if not isinstance(r, Mapping):
            continue
        ctr = _region_center(r)
        if ctr is None:
            continue
        dist = max(abs(tx - ctr[0]), abs(ty - ctr[1]))
        if dist <= _REGION_MATCH_RADIUS and (best is None or dist < best[0]):
            best = (dist, (int(round(ctr[0])), int(round(ctr[1]))))
    return best[1] if best else primary


def _pincer_wave(
    center: Tuple[int, int],
    bearing: Tuple[int, int],
    width: int,
    height: int,
    green: "set",
) -> Optional[Tuple[Tuple[int, int], List[List[int]]]]:
    """A flank drop + inward comb along one bearing (the smart-hawk pincer).

    The flanker drops ``_PINCER_RADIUS`` cells out from the pure on ``bearing``
    and sweeps back toward it, stopping ONE cell short of the exact centre — the
    contested prize the lead (or first arriver) takes. Rivals thus work the SAME
    seam from disjoint approach angles and their short spokes never share a cell,
    instead of stacking one drop and mutually crashing. Returns
    ``((drop_x, drop_y), comb_path)`` or ``None`` when the flank cell is unusable
    (off-board-collapsed onto the centre, or a known-green hazard).
    """
    cx, cy = center
    bx, by = bearing
    dropx = _clamp(cx + bx * _PINCER_RADIUS, 0, width - 1)
    dropy = _clamp(cy + by * _PINCER_RADIUS, 0, height - 1)
    if (dropx, dropy) == center or (dropx, dropy) in green:
        return None
    stopx = _clamp(cx + bx, 0, width - 1)
    stopy = _clamp(cy + by, 0, height - 1)
    comb = (
        [[stopx, stopy]]
        if (stopx, stopy) not in ((dropx, dropy), center) and (stopx, stopy) not in green
        else []
    )
    return (dropx, dropy), comb


def _match_ownership(
    hint: Mapping[str, Any], regions: Sequence[Mapping[str, Any]],
) -> Optional[bool]:
    """Return the ``mine`` flag of the redsign region this hint targets.

    ``None`` when no region is close enough to attribute (leave ownership
    unknown rather than guess).
    """
    target = _hint_target(hint)
    if target is None:
        return None
    tx, ty = target
    best: Optional[Tuple[float, bool]] = None
    for r in regions:
        if not isinstance(r, Mapping):
            continue
        ctr = _region_center(r)
        if ctr is None:
            continue
        dist = max(abs(tx - ctr[0]), abs(ty - ctr[1]))
        if dist <= _REGION_MATCH_RADIUS and (best is None or dist < best[0]):
            best = (dist, bool(r.get("mine")))
    return None if best is None else best[1]


def personalize_hot_drops(
    hints: Sequence[Mapping[str, Any]],
    agent_view: Mapping[str, Any],
    player: str,
) -> List[Dict[str, Any]]:
    """Return v10 hot-drop hints with ownership + seat-differentiated offsets.

    Non-destructive: operates on shallow copies so the shared compiler's cached
    output is never mutated. Non-redsign / non-contested hints pass through with
    only a ``mine`` annotation (when attributable).
    """
    regions = list(agent_view.get("redsign") or [])
    width, height = _grid_dims(agent_view)
    green = _known_green_cells(agent_view)
    idx = seat_index(player)

    out: List[Dict[str, Any]] = []
    for h in hints or []:
        if not isinstance(h, Mapping):
            continue
        hint = dict(h)
        is_redsign = str(hint.get("signal_type") or "") == "redsign"

        mine = _match_ownership(hint, regions) if is_redsign else None
        if mine is not None:
            hint["mine"] = bool(mine)

        # Already seat-distinct upstream (v10 speculative sampler picks a
        # per-seat cluster + drop cell) — the geometric pincer is a SAFETY NET
        # only, so leave a ``varied`` hint exactly where the sampler put it.
        if hint.get("varied"):
            out.append(hint)
            continue

        # SMART-HAWK PINCER (R3). A public signal (redsign broadcast, bluesign,
        # or a shared best fog cell) is computed identically by every seat, so a
        # mirror stacks the SAME drop + enabler probe and mutually crashes (the
        # seed-69 day-1 3-way pile-up, and the day-1 adjacent-drop clip after the
        # first fix). The tactical answer is NOT to shove rivals into empty fog:
        # it is the multi-wave doctrine as geometry. The lead seat keeps the
        # smash-and-grab on the pure; each flanking seat drops on a DISTINCT
        # bearing around the seam centre and sweeps INWARD, stopping one cell shy
        # of the contested pure. Rivals thus work the same seam from disjoint
        # approach angles — contesting the value, not abandoning it — and their
        # short spokes can never share a cell. CASE 1 (my own redsign) is left
        # untouched: I found it, I lead. Fires even on day 1 when the redsign is
        # not yet in the view (fog/bluesign play; centre falls back to the guess).
        if mine is not True and idx > 0:
            primary = _hint_target(hint)
            if primary is not None:
                center = _matched_center(primary, regions)
                bearing = _APPROACH_BEARINGS[(idx - 1) % len(_APPROACH_BEARINGS)]
                wave = _pincer_wave(center, bearing, width, height, green)
                if wave is not None:
                    (dropx, dropy), comb = wave
                    hint["drop_at"] = [dropx, dropy]
                    hint["seat_offset"] = [dropx, dropy]
                    hint["approach_bearing"] = [bearing[0], bearing[1]]
                    hint["comb_path"] = comb
                    # Enabler probe rides on the flank drop so its disk reveals
                    # the inward spoke and no two flankers' probes collide.
                    if hint.get("probe_at") is not None:
                        hint["probe_at"] = [dropx, dropy]
                else:
                    # Flank cell unusable (green / board edge): fall back to a
                    # separated ring cell so we still never stack the lead.
                    alts = [
                        (int(a[0]), int(a[1]))
                        for a in (hint.get("alt_drops") or [])
                        if isinstance(a, (list, tuple)) and len(a) == 2
                    ]
                    cands = _fanout_candidates(
                        primary, alts, width, height, green,
                        ring=_WIDE_FANOUT_RING, min_sep=_HOTDROP_MIN_SEP,
                    )
                    assigned = cands[idx % len(cands)]
                    if assigned != primary:
                        hint["drop_at"] = [assigned[0], assigned[1]]
                        hint["seat_offset"] = [assigned[0], assigned[1]]
                        hint["comb_path"] = []
                        if hint.get("probe_at") is not None:
                            hint["probe_at"] = [assigned[0], assigned[1]]
        out.append(hint)
    return out


def personalize_probes(
    hints: Sequence[Mapping[str, Any]],
    agent_view: Mapping[str, Any],
    player: str,
) -> List[Dict[str, Any]]:
    """Seat-shift the exploration probe cells so mirror seats don't stack them.

    The compiled probe hints are a pure function of the board, so every seat
    picks the SAME best fog cell (the day-1 (33,19) probe collision that mutually
    destroyed all three). The lead seat (index 0) keeps the ranked cells; later
    seats translate every probe ``at`` by a distinct bearing (clamped in-bounds,
    off known-green) — a 1-2 cell nudge that still reveals fresh fog but lands on
    a different cell. Non-destructive (operates on copies).
    """
    idx = seat_index(player)
    if idx <= 0:
        return [dict(h) for h in hints or [] if isinstance(h, Mapping)]
    width, height = _grid_dims(agent_view)
    green = _known_green_cells(agent_view)
    dx, dy = _SEAT_PROBE_RING[idx % len(_SEAT_PROBE_RING)]
    out: List[Dict[str, Any]] = []
    for h in hints or []:
        if not isinstance(h, Mapping):
            continue
        hint = dict(h)
        at = hint.get("at")
        if (dx, dy) != (0, 0) and isinstance(at, (list, tuple)) and len(at) == 2:
            nx = _clamp(int(at[0]) + dx, 0, width - 1)
            ny = _clamp(int(at[1]) + dy, 0, height - 1)
            if (nx, ny) not in green:
                hint["at"] = [nx, ny]
        out.append(hint)
    return out
