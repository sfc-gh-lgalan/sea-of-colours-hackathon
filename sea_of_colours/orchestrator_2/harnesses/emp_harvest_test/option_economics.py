"""v11 option economics — the numbers behind every menu option.

The v10/v11 OPTION MENU used to render an option as bare coordinates + a probe
cost. The model then had to reconstruct the walk, guess the yield, and cross-
reference a separate DROP-LEGAL block by eye to learn a cell was watched. This
module computes, deterministically, the facts that decision needs so the menu
can carry them inline — making the menu the single source of truth:

  * WALK      — the ordered cells a harvester would drop on + step through.
  * YIELD     — expected score contribution, broken out by colour, using the
                ENGINE's real scoring model (not the harness proxy):
                  RED   score = purity x MULT[tier(purity)]
                  BLUE  scores 0 (it is the fissile spend surface, not standings)
                  GREEN costs GREEN_ENDGAME_PENALTY (-100) per banked parcel
                v1.13 — shipping is automatic and free, so a banked RED parcel
                is worth exactly this. There is no longer a transit charge or a
                catapult row to guess at, so the figure is exact rather than a
                best-case upper bound.
  * CRUSH     — probe centres the maneuver lands on: YOUR probe (bad, lose your
                own vision) vs an ENEMY probe (good, a supersede/denial).
  * COLLISION — how exposed the maneuver is to a rival crash, per the user's
                model: a cell an enemy probe SEES, weighted by how VALUABLE it is
                (mass/pure under enemy vision = a magnet, so HIGH), bumped when a
                rival holds EMP/chaff (timing/weapon risk on the pickup).

Pure functions over ``agent_view`` + an option ``payload``. Reuses the frozen
v7 geometry helpers (``_vision_disk`` / ``_enemy_probe_cells`` / ``_grid_dims``)
so the disk math matches the engine exactly.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test._v7.probe_hints import (
    _enemy_probe_cells,
    _grid_dims,
    _redsign_cells,
    _vision_disk,
)

Cell = Tuple[int, int]

# ── engine scoring constants (mirrored from sea_of_colours/game/session.py) ──
# RED tier-quality multiplier applied at ship time.
RED_QUALITY_MULTIPLIER: Dict[str, float] = {
    "trace": 0.75,
    "vein": 1.0,
    "mass": 1.5,
    "pure": 3.0,
}
# v1.13 — the catapult's four transit rows (10/25/50/100) are gone; every RED
# parcel ships free at settlement. Kept at 0 rather than deleted because forks
# of this harness reference it, and 0 makes the formula below read as the
# identity it now is.
BEST_ROW_TRANSIT = 0
# Standing liability per undisposed vault-GREEN parcel.
GREEN_ENDGAME_PENALTY = 100
# Engine outing budget: a drop banks its own cell + up to 5 steps.
HOLD_CAPACITY = 6


def _tier(purity: int) -> str:
    """Engine tier bands (session._tier_for_purity): trace<=50, vein<=150,
    mass<=254, pure=255."""
    p = max(0, min(255, int(purity or 0)))
    if p <= 0:
        return "empty"
    if p <= 50:
        return "trace"
    if p <= 150:
        return "vein"
    if p <= 254:
        return "mass"
    return "pure"


def _red_ship_points(purity: int) -> float:
    """Ship points for a RED cell of ``purity`` — the engine formula
    ``purity x MULT[tier(purity)]`` (v1.13: no transit charge, so this is
    exact rather than a best case)."""
    p = int(purity or 0)
    mult = RED_QUALITY_MULTIPLIER.get(_tier(p), 1.0)
    effective = max(0, p - BEST_ROW_TRANSIT)
    return effective * mult


def pure_ship_points() -> int:
    """What a pure(255) banks — the stake on every redsign, ours or theirs."""
    return int(round(_red_ship_points(255)))


def view_day(agent_view: Mapping[str, Any]) -> Optional[int]:
    """Tonight's day number, wherever the view happens to carry it.

    The engine publishes it under ``hud.day`` / ``meta.day`` and NOT at the top
    level, so every reader here that asked for ``agent_view["day"]`` silently got
    nothing on a real board — and each swallowed it differently, which is why a
    datum that was dead in production stayed green in the suite. Single-sourced
    so the next key move breaks one function, not three.
    """
    for value in (
        agent_view.get("day"),
        (agent_view.get("hud") or {}).get("day"),
        (agent_view.get("meta") or {}).get("day"),
    ):
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def sign_found_day(row: Mapping[str, Any]) -> Optional[int]:
    """The night a redsign was broadcast, whatever the row calls it.

    ``view._redsign_for_seat`` copies the engine's region through verbatim, and
    the engine names this ``day`` — the ``found_day`` / ``day_found`` spellings
    the readers used exist only in hand-built test fixtures.
    """
    for key in ("found_day", "day_found", "day"):
        try:
            return int(row[key])
        except (KeyError, TypeError, ValueError):
            continue
    return None


def sign_ages_by_beacon(agent_view: Mapping[str, Any]) -> Dict[Cell, int]:
    """``(x, y) -> nights since broadcast``, keyed by the sign's centre cell."""
    day = view_day(agent_view)
    if day is None:
        return {}
    out: Dict[Cell, int] = {}
    for row in (agent_view.get("redsign") or []):
        if not isinstance(row, Mapping):
            continue
        centre = row.get("center", row.get("centre"))
        if not (isinstance(centre, (list, tuple)) and len(centre) >= 2):
            continue
        found = sign_found_day(row)
        if found is None:
            continue
        try:
            cell = (int(round(float(centre[0]))), int(round(float(centre[1]))))
        except (TypeError, ValueError):
            continue
        out[cell] = max(0, day - found)
    return out


# ── board index ─────────────────────────────────────────────────────────
def _enemy_trail_cells(agent_view: Mapping[str, Any]) -> Set[Cell]:
    """Cells a rival harvester was seen walking — stripped, whatever else says.

    Imported lazily to keep this module free of harness-layer imports.
    """
    from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test import hazard_memory
    return hazard_memory.enemy_trail_cells(agent_view)


def _echo_only_cells(agent_view: Mapping[str, Any]) -> Dict[Cell, int]:
    """RED cells known ONLY from a stale reading, with how old that reading is.

    Fix 0.2 (OBS-43). ``_cell_index`` projects ``red_tiles`` in as a fallback,
    and that list carries ECHO rows as well as live ones — so a two-night-old
    sighting is priced identically to a cell a probe is lighting right now. The
    yield says nothing to separate them, and "counted into the total" is exactly
    how a sweep came to advertise +1533 over ground that was partly gone.

    Returns ``{cell: nights_stale}``; 0 when the view does not date the reading.
    """
    live: Set[Cell] = set()
    for row in ((agent_view.get("world") or {}).get("live") or []):
        if isinstance(row, Mapping):
            try:
                live.add((int(row["x"]), int(row["y"])))
            except (TypeError, KeyError, ValueError):
                continue
    day = int(agent_view.get("day") or 0)
    out: Dict[Cell, int] = {}
    for row in (agent_view.get("red_tiles") or []):
        if not isinstance(row, Mapping):
            continue
        if str(row.get("freshness") or "") == "fresh":
            continue
        try:
            cell = (int(row["x"]), int(row["y"]))
        except (TypeError, KeyError, ValueError):
            continue
        if cell in live:
            continue
        seen = row.get("day_seen") or row.get("last_seen_day")
        age = max(0, day - int(seen)) if isinstance(seen, (int, float)) else 0
        out[cell] = age
    return out


def _cell_index(agent_view: Mapping[str, Any]) -> Dict[Cell, Tuple[str, int]]:
    """Map ``(x, y) -> (tile, purity)`` for every cell in LIVE vision.

    Sourced from ``world.live`` (RED/BLUE/GREEN with purity), with a fallback to
    the top-level ``red_tiles`` / ``blue_tiles`` lists so fixtures that only
    project those still score. Cells NOT in this index are fog/unknown — a hot
    drop into them is blind, and we report the yield as unknown rather than
    inventing a number.
    """
    idx: Dict[Cell, Tuple[str, int]] = {}
    for row in ((agent_view.get("world") or {}).get("live") or []):
        if not isinstance(row, Mapping):
            continue
        tile = str(row.get("tile") or "").upper()
        if tile not in ("RED", "BLUE", "GREEN"):
            continue
        try:
            x, y = int(row["x"]), int(row["y"])
            p = int(row.get("purity") or 0)
        except (TypeError, KeyError, ValueError):
            continue
        idx[(x, y)] = (tile, p)
    # Fallback projections (fixtures / older shapes). Never overwrite a live row.
    for row in (agent_view.get("red_tiles") or []):
        if not isinstance(row, Mapping):
            continue
        try:
            x, y = int(row["x"]), int(row["y"])
            p = int(row.get("purity") or 0)
        except (TypeError, KeyError, ValueError):
            continue
        idx.setdefault((x, y), ("RED", p))
    # Fix 0.1 (OBS-43) — a cell a RIVAL harvester walked is STRIPPED, whatever
    # the red lists still say about it. This overwrite is deliberate and is the
    # only one here: on SNAP_ac1c55bf_d6_p4 the echo rows for (17,7)/(17,8) were
    # two nights old and a rival cleared both last night, yet the loop above
    # projected them straight back in as RED at full purity — so FULL_SWEEP
    # counted them INTO its advertised +1533 and would have paid -100 each on
    # arrival.
    for cell in _enemy_trail_cells(agent_view):
        idx[cell] = ("GREEN", 0)
    for row in (agent_view.get("blue_tiles") or []):
        if not isinstance(row, Mapping):
            continue
        try:
            x, y = int(row["x"]), int(row["y"])
            p = int(row.get("purity") or row.get("value") or 0)
        except (TypeError, KeyError, ValueError):
            continue
        idx.setdefault((x, y), ("BLUE", p))
    return idx


# ── walk extraction ──────────────────────────────────────────────────────
def _as_cell(v: Any) -> Optional[Cell]:
    if isinstance(v, (list, tuple)) and len(v) == 2:
        try:
            return int(v[0]), int(v[1])
        except (TypeError, ValueError):
            return None
    return None


def walk_cells(payload: Mapping[str, Any]) -> List[Cell]:
    """The ordered cells a HARVESTER would bank on this option (drop + steps).

    Empty for probe/supersede options (they bank nothing). Handles every deploy
    shape: chain/grab (``cells`` — full path incl. drop), hot drop (``drop_at`` +
    ``comb_path``), and seam patterns (each non-deny wave's ``drop_at`` +
    ``comb_path``). Deduped, order-preserving.
    """
    out: List[Cell] = []
    seen: Set[Cell] = set()

    def _push(c: Optional[Cell]) -> None:
        if c is not None and c not in seen:
            seen.add(c)
            out.append(c)

    # Seam patterns: walk every wave that actually drops a harvester.
    waves = payload.get("waves")
    if isinstance(waves, list) and waves:
        for w in waves:
            if not isinstance(w, Mapping) or w.get("deny_only"):
                continue
            # v13 — emp_only waves fire a salvo, no harvester lands, so they
            # bank nothing and must not be walked here (mirrors deny_only).
            # snap_only waves are the same shape for the SNAP round.
            if w.get("emp_only") or w.get("snap_only"):
                continue
            _push(_as_cell(w.get("drop_at")))
            for c in (w.get("comb_path") or []):
                _push(_as_cell(c))
        return out

    # v13 — BLIND_SCORCH (shape=="occupy"): the harvester lands on ``hole``
    # and combs the cells under ``comb`` when the cloud lifts. Prior to this
    # branch, walk_cells returned empty for BLIND_SCORCH and the option's
    # yield line rendered "red ~+0", which the LLM read as "worth nothing"
    # — the seed-4242/p3-lab-d6 failure mode (the doctrine warning against
    # the +0 anchor could not override the anchor itself). Surfacing the
    # walk here lets ``blind_estimate`` price the halo, and ``_fmt_yield``
    # then renders a real "expected red over the whole route ~+X" number
    # in the option line.
    if str(payload.get("shape") or "") == "occupy":
        hole = _as_cell(payload.get("hole"))
        if hole is not None:
            _push(hole)
            for c in (payload.get("comb") or []):
                _push(_as_cell(c))
            return out

    # Chain / grab: ``cells`` already includes the drop as its first entry.
    cells = payload.get("cells")
    if isinstance(cells, list) and cells:
        _push(_as_cell(payload.get("drop_at")))
        for c in cells:
            _push(_as_cell(c))
        return out

    # Hot drop / frontier: drop cell then the precompiled comb.
    drop = _as_cell(payload.get("drop_at"))
    if drop is not None:
        _push(drop)
        for c in (payload.get("comb_path") or []):
            _push(_as_cell(c))
    return out


def probe_cells(payload: Mapping[str, Any]) -> List[Cell]:
    """The cells this option launches a PROBE onto (for crush/supersede math).

    Covers a plain probe/supersede (``at`` / ``probe_at``), a hot drop's own
    reveal probe (``probe_at`` + any ``supersede``), and seam waves' probes.
    """
    out: List[Cell] = []
    seen: Set[Cell] = set()

    def _push(c: Optional[Cell]) -> None:
        if c is not None and c not in seen:
            seen.add(c)
            out.append(c)

    waves = payload.get("waves")
    if isinstance(waves, list) and waves:
        for w in waves:
            if not isinstance(w, Mapping):
                continue
            _push(_as_cell(w.get("probe_at")))
            _push(_as_cell(w.get("supersede")))
        return out

    _push(_as_cell(payload.get("at")))
    _push(_as_cell(payload.get("probe_at")))
    _push(_as_cell(payload.get("supersede")))
    return out


# ── yield ─────────────────────────────────────────────────────────────────
def yield_breakdown(
    cells: Sequence[Cell], agent_view: Mapping[str, Any],
) -> Dict[str, Any]:
    """Expected banked yield over ``cells``, broken out by colour.

    Returns:
      red_pts        — ship points from RED cells (float, rounded int)
      red_tiers      — {tier: count} of harvested RED cells
      blue_fissile   — summed BLUE purity (fissile budget; 0 score)
      green_penalty  — -100 per harvested GREEN cell (negative int)
      green_cells    — count of harvested GREEN cells
      unknown_cells  — harvested cells not in live vision (blind fog steps)
      banked         — coloured cells that actually bank (<= HOLD_CAPACITY)
      over_hold      — banked coloured cells beyond the 6-parcel hold (won't bank)
    """
    idx = _cell_index(agent_view)
    echo_age = _echo_only_cells(agent_view)
    red_pts = 0.0
    red_tiers: Dict[str, int] = {}
    blue_fissile = 0
    green_cells = 0
    unknown = 0
    coloured = 0  # RED/BLUE/GREEN cells (each banks a parcel, hold-capped)
    echo_pts = 0.0   # the slice of red_pts resting on a STALE reading
    echo_cells = 0
    oldest_echo = 0
    for c in cells:
        entry = idx.get(c)
        if entry is None:
            unknown += 1
            continue
        tile, purity = entry
        coloured += 1
        if tile == "RED":
            pts = _red_ship_points(purity)
            red_pts += pts
            t = _tier(purity)
            red_tiers[t] = red_tiers.get(t, 0) + 1
            # Fix 0.2 — the points are still counted (an echo is real evidence),
            # but how much of the total is a GUESS is now visible instead of
            # being blended into one confident number.
            if c in echo_age:
                echo_pts += pts
                echo_cells += 1
                oldest_echo = max(oldest_echo, echo_age[c])
        elif tile == "BLUE":
            blue_fissile += int(purity)
        elif tile == "GREEN":
            green_cells += 1
    return {
        "red_pts": int(round(red_pts)),
        "red_tiers": red_tiers,
        "blue_fissile": int(blue_fissile),
        "green_penalty": -GREEN_ENDGAME_PENALTY * green_cells,
        "green_cells": green_cells,
        "unknown_cells": unknown,
        "banked": min(coloured, HOLD_CAPACITY),
        "over_hold": max(0, coloured - HOLD_CAPACITY),
        "echo_pts": int(round(echo_pts)),
        "echo_cells": echo_cells,
        "echo_age": oldest_echo,
    }


# ── blind pricing (fix 0.3, OBS-30) ──────────────────────────────────────
def _redsign_smear_regions(
    agent_view: Mapping[str, Any],
) -> List[Dict[Cell, float]]:
    """One ``{cell: intensity}`` map PER live redsign, never merged.

    ``_redsign_cells`` keys the same smears by hour (a freshness signal) and
    THROWS THE INTENSITY AWAY, which is the datum a blind comb is actually
    gambling on. Engine shape is ``cells: [[x, y, intensity], ...]``; older
    fixtures carry bare ``[x, y]``, which we read as full confidence.

    Kept SEPARATE because each redsign independently promises its own pure. A
    merged map divides one seam's odds by another seam's weight and prices its
    halo off cells belonging to a seam on the far side of the board — on
    ``SNAP_408ddd46_d5_p1`` that had the unseen rival seam at (16,6) inheriting
    the 53-purity trace visible on OUR seam at (31,17).
    """
    out: List[Dict[Cell, float]] = []
    for row in (agent_view.get("redsign") or []):
        if not isinstance(row, Mapping):
            continue
        region: Dict[Cell, float] = {}
        for c in (row.get("cells") or []):
            if isinstance(c, Mapping):
                xf, yf, w = c.get("x"), c.get("y"), c.get("intensity", 1.0)
            elif isinstance(c, (list, tuple)) and len(c) >= 2:
                xf, yf = c[0], c[1]
                w = c[2] if len(c) >= 3 else 1.0
            else:
                continue
            try:
                cell = (int(round(float(xf))), int(round(float(yf))))
                weight = float(w)
            except (TypeError, ValueError):
                continue
            region[cell] = max(region.get(cell, 0.0), weight)
        if region:
            out.append(region)
    return out


# Fallbacks for a seam we can see NOTHING of, used only when no OTHER live seam
# is measurable either. Both are calibrated against real boards rather than
# picked: across SNAP_408ddd46_d5_p1 / SNAP_ac1c55bf_d6_p4 / SNAP_6432003f_d7_p1
# the visible slice of a smear ran 75% / 91% / 100% RED at a mean non-pure purity
# of 53 / 54 / 63. The first draft of this guessed 180 (mid-mass) and priced a
# 6-cell blind comb at +1641, which beat every real option on the card — swapping
# "the attack always loses" for "the attack always wins" is the same defect
# pointing the other way.
#
# MEASURED v1.29, over 28 offline seasons (14 graded / 14 not, same seeds),
# sampling every real smear a seat could see — `scripts/_v12_halo_trace.py`.
# Three things came out of it, and the first is the one that matters:
#
#  1. THIS CONSTANT IS ALMOST NEVER REACHED — 1.0% of priced seams on graded
#     boards, 2.1% ungraded. `blind_estimate` measures each seam it can see
#     and pools across seams first, and that path reads the new terrain by
#     itself. So the terrain change did NOT quietly break redsign pricing.
#
#  2. 55 was never the right number. Real smears measure a median non-pure
#     purity of 30 on UNGRADED boards and 107 on graded ones (n=1095/1525).
#     The original 53/54/63 came from three hand-picked snapshots that were
#     richer than a typical board, so the constant ran ~1.8x HIGH before
#     v1.29 and now runs ~1.9x LOW. It has simply always been off.
#
#  3. The `mass` cliff is not in play. The multiplier steps 1.0 -> 1.5 at
#     151, which is what made an early guess of 180 beat every option on
#     the card (a 6-cell comb prices 281 at purity 55, 602 at 107, 1377 at
#     180). Across 1525 graded samples the measured mean never exceeded
#     **146** — real halos stay in `vein`. A value near 107 is therefore
#     safe in a way 180 never was.
#
# Set from that measurement: 105 is the graded median (107) rounded down, and
# 0.95 sits just under the measured 0.98 — both deliberately a shade
# conservative, since the failure mode this constant has actually produced in
# the past is over-valuing a blind attack, never under-valuing one.
#
# Re-run `scripts/_v12_halo_trace.py` before moving these again, and derive
# from real smears rather than a proxy ring on the pure — the two disagree.
# If the terrain is ever reverted (SOC_MAP_HALO=off) the honest value drops
# back to ~30, though at a 1% reach it is not worth chasing.
_ASSUMED_HALO_PURITY = 105
_ASSUMED_RED_DENSITY = 0.95

# ── halo telemetry (opt-in, off in play) ─────────────────────────────
#
# Exists to answer the question the constant above cannot answer from a
# desk: HOW OFTEN is it actually reached? `blind_estimate` measures the
# board and pools across seams before falling back, so the constant only
# bites when a seam shows no non-pure RED — and whether that is one night
# in three or one in three hundred decides whether it is worth retuning at
# all. Collected over real seasons by `scripts/_v12_halo_trace.py`.
#
# Costs nothing unless SOC_V12_HALO_TRACE is set, and never changes what
# blind_estimate returns.
_HALO_TRACE: List[Dict[str, Any]] = []


def halo_trace_enabled() -> bool:
    return bool(os.environ.get("SOC_V12_HALO_TRACE"))


def halo_trace() -> List[Dict[str, Any]]:
    """Records collected since the last reset. See `_HALO_TRACE`."""
    return _HALO_TRACE


def reset_halo_trace() -> None:
    _HALO_TRACE.clear()


def _redsign_smear_meta(agent_view: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Per-sign ``{mine, nights_held}``, in the SAME ORDER as the smear regions.

    OBS-53. Everything downstream priced a beacon as though its pure were still
    sitting there, because a redsign is minted once and never retracted. On
    `V12_V11_R2_s56` night 5 the seat blind-walked a rival seam broadcast the
    night before, crossed five cells, and found every one of them already
    stripped: 7 points of trace against four -100 green penalties, a -393 night
    that decided a 252-1366 loss. The finder had held exact vision of that pure
    for a full night, which is all anyone needs to take one.
    """
    day = view_day(agent_view)
    out: List[Dict[str, Any]] = []
    for row in (agent_view.get("redsign") or []):
        if not isinstance(row, Mapping):
            continue
        if not (row.get("cells") or []):
            continue  # matches _redsign_smear_regions, which skips empty smears
        found = sign_found_day(row)
        nights = 0 if (day is None or found is None) else max(0, day - found)
        out.append({"mine": bool(row.get("mine")), "nights_held": nights})
    return out


def _pure_survival(meta: Mapping[str, Any]) -> float:
    """Odds a broadcast pure is STILL on the board, by who has been watching it.

    The finder holds the exact cell and a landing there auto-harvests, so each
    night they have held it is a near-certain chance to have banked it. Our own
    sign is the exception: if it were taken, we took it, and the cell would read
    green rather than echo. Deliberately coarse — the point is to stop pricing a
    week-old rival beacon at full jackpot, not to model a rival's schedule.
    """
    if meta.get("mine"):
        return 1.0
    nights = int(meta.get("nights_held") or 0)
    if nights <= 0:
        return 1.0        # broadcast tonight — nobody has had a turn on it yet
    return max(0.10, 0.35 ** nights)


def blind_estimate(
    cells: Sequence[Cell], agent_view: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    """Expected red points over the FOG cells of ``cells``, or ``None``.

    Fix 0.3 (OBS-30). A blind comb used to price at the literal word "unknown"
    against a rival option carrying a four-figure number, and no agent picks
    "unknown" over "+1248" — so the attack lost by default rather than on its
    merits. It is not unknowable: a redsign is a public promise that a pure(255)
    sits somewhere in the smear, and the smear ships per-cell intensities.

    Model, stated on the card so the agent can discount it. A smear cell is
    worth the pure if it IS the pure — odds equal to its share of the seam's
    total intensity — and otherwise worth an ordinary halo cell, which is
    ``density x purity`` measured off the part of THAT seam we can see. Not
    every smear cell is even red, and a model that assumed so was the thing that
    inflated the first draft.

    ``None`` when no fog cell touches a smear — a blind walk through open fog
    really is unknown, and inventing a number there would be the opposite lie.
    """
    regions = _redsign_smear_regions(agent_view)
    if not regions:
        return None
    metas = _redsign_smear_meta(agent_view)
    if len(metas) != len(regions):        # shapes disagreed — price at full odds
        metas = [{"mine": False, "nights_held": 0}] * len(regions)
    idx = _cell_index(agent_view)
    pure_pts = _red_ship_points(255)

    # Measure each seam, then pool. A seam we can see nothing of borrows from
    # the seams we can — same board, same generator — before any constant.
    stats: List[Optional[Tuple[float, int]]] = []
    pool_purity: List[int] = []
    pool_density: List[float] = []
    sources: List[str] = []       # telemetry only — see _HALO_TRACE
    for weights in regions:
        seen = [(c, idx[c]) for c in weights if c in idx]
        if not seen:
            stats.append(None)
            sources.append("blind")       # will take the pooled/constant fallback
            continue
        red = [p for _, (t, p) in seen if t == "RED"]
        nonpure = [p for p in red if p < 255]
        density = len(red) / len(seen)
        purity = int(sum(nonpure) / len(nonpure)) if nonpure else _ASSUMED_HALO_PURITY
        sources.append("measured" if nonpure else "constant_purity")
        stats.append((density, purity))
        pool_density.append(density)
        pool_purity.append(purity)
    fallback = (
        (sum(pool_density) / len(pool_density), int(sum(pool_purity) / len(pool_purity)))
        if pool_density else (_ASSUMED_RED_DENSITY, _ASSUMED_HALO_PURITY)
    )

    expected = 0.0
    total_cells = 0
    best_p = 0.0
    coverage = 0.0        # R2.8 — POOLED odds the comb crosses the pure at all
    smear_cells = 0
    reported: Tuple[float, int] = fallback
    reported_measured = False
    widest = -1
    survival = 1.0
    nights_held = 0
    for weights, stat, meta in zip(regions, stats, metas):
        fog = [c for c in cells if c not in idx and c in weights]
        if not fog:
            continue
        density, purity = stat if stat is not None else fallback
        halo_pts = _red_ship_points(purity) * density
        total_w = sum(weights.values()) or 1.0
        alive = _pure_survival(meta)
        for c in fog:
            p_pure = min(1.0, weights[c] / total_w)
            # OBS-53 — the jackpot term is only worth anything if the jackpot is
            # still there. The halo term is not discounted: a stripped seam
            # still holds its unworked mass, and denial is worth the trip.
            expected += p_pure * pure_pts * alive + (1.0 - p_pure) * halo_pts
            best_p = max(best_p, p_pure)
            coverage += p_pure
        total_cells += len(fog)
        smear_cells = max(smear_cells, len(weights))
        # A walk spanning two seams is rare; describe the one it leans on most.
        if len(fog) > widest:
            widest = len(fog)
            reported = (density, purity)
            reported_measured = stat is not None
            survival = alive
            nights_held = int(meta.get("nights_held") or 0)

    if not total_cells:
        return None

    if halo_trace_enabled():
        # Resolve "blind" seams to what they ACTUALLY used, which is the
        # distinction the constant's fate turns on: borrowing measured stats
        # from another seam on the same board is self-correcting on graded
        # terrain, whereas reaching the constant is not.
        pooled = bool(pool_density)
        _HALO_TRACE.append({
            "day": view_day(agent_view),
            "regions": len(regions),
            "sources": [
                ("pooled" if pooled else "constant_both") if s == "blind" else s
                for s in sources
            ],
            "measured_purities": list(pool_purity),
            "measured_densities": [round(d, 3) for d in pool_density],
            "reported_purity": reported[1],
            "reported_density": round(reported[0], 3),
            "reported_measured": reported_measured,
            "fog_cells": total_cells,
            "expected_pts": int(round(expected)),
        })

    return {
        "expected_pts": int(round(expected)),
        "cells": total_cells,
        "best_pure_odds": round(best_p, 2),
        # R2.8 — the number that actually describes the bet. The per-cell odds
        # made a guaranteed jackpot read like a raffle ticket; what the agent is
        # buying is the chance that ANY cell on the comb is the pure, which is
        # the sum over the cells it walks. Printing only the best single cell is
        # how a 6-cell comb over a 25-cell smear got argued down to "one in
        # twenty-five" and lost to a fully-discounted chain.
        "pure_odds": round(min(1.0, coverage), 2),
        "smear_cells": smear_cells,
        # What a rival banks if nobody contests. The swing, not our gain.
        "unclaimed_pure_pts": int(round(pure_pts)),
        "pure_survival": round(survival, 2),
        "nights_held": nights_held,
        "halo_purity": reported[1],
        "halo_density": round(reported[0], 2),
        "halo_measured": reported_measured,
    }


def overlap_claims(
    walks: Mapping[str, Sequence[Cell]], agent_view: Mapping[str, Any],
) -> Dict[str, List[Tuple[Cell, str, List[str]]]]:
    """Which HIGH-VALUE cells more than one option is quietly selling.

    Fix 0.3 (OBS-30/OBS-4). Every yield on the menu is computed in isolation,
    which is correct per option and a lie in combination: on
    ``SNAP_408ddd46_d5_p1`` the pure at (31,18) was counted into `WALKIN_GRAB`,
    `WALKIN_SECURE` (+1248), `WALKIN_LATE` (+1108), `GRAB1` (+1144) and `CH2`.
    An agent picking two of those reads two four-figure numbers and adds them,
    when the second wave arrives to find the cell already stripped — worth 0,
    and -100 if it steps on the green it became.

    Restricted to mass/pure RED, which is where the distortion is worth the ink;
    a shared trace changes nothing. Returns ``{option_id: [(cell, tier,
    [other_ids])]}``, empty for options that share nothing.
    """
    idx = _cell_index(agent_view)
    owners: Dict[Cell, List[str]] = {}
    for oid, walk in walks.items():
        for c in walk:
            tile, purity = idx.get(c, ("", 0))
            if tile != "RED" or _tier(purity) not in ("mass", "pure"):
                continue
            if oid not in owners.setdefault(c, []):
                owners[c].append(oid)

    out: Dict[str, List[Tuple[Cell, str, List[str]]]] = {}
    for cell, ids in owners.items():
        if len(ids) < 2:
            continue
        tier = _tier(idx[cell][1])
        for oid in ids:
            out.setdefault(oid, []).append(
                (cell, tier, [o for o in ids if o != oid])
            )
    return out


# ── crush ───────────────────────────────────────────────────────────────
def _friendly_probe_centres(agent_view: Mapping[str, Any]) -> Set[Cell]:
    out: Set[Cell] = set()
    for e in ((agent_view.get("entities") or {}).get("mine") or []):
        if not isinstance(e, Mapping) or str(e.get("type") or "") != "probe":
            continue
        pos = e.get("pos") or e.get("at")
        c = _as_cell(pos)
        if c is not None:
            out.add(c)
    return out


def _enemy_probe_centres(agent_view: Mapping[str, Any]) -> Set[Cell]:
    out: Set[Cell] = set()
    for row in _enemy_probe_cells(agent_view):
        c = _as_cell(row.get("at"))
        if c is not None:
            out.add(c)
    return out


def _friendly_probe_nights(agent_view: Mapping[str, Any]) -> Dict[Cell, Optional[int]]:
    """Map each friendly probe centre -> its ``nights_remaining`` (``None`` when
    the field is absent). Used to judge whether crushing a probe actually costs
    any FUTURE vision."""
    out: Dict[Cell, Optional[int]] = {}
    for e in ((agent_view.get("entities") or {}).get("mine") or []):
        if not isinstance(e, Mapping) or str(e.get("type") or "") != "probe":
            continue
        c = _as_cell(e.get("pos") or e.get("at"))
        if c is None:
            continue
        nr = e.get("nights_remaining")
        out[c] = int(nr) if isinstance(nr, (int, float)) else None
    return out


# A probe with <= this many nights left will not survive to the NEXT planning
# night, so crushing it costs no future vision.
_NEAR_EXPIRY_NIGHTS = 1


def self_crush_verdicts(
    self_hits: Sequence[Cell],
    agent_view: Mapping[str, Any],
    *,
    walk: Optional[Sequence[Cell]] = None,
    day: Optional[int] = None,
    day_cap: Optional[int] = None,
) -> List[str]:
    """A per-cell verdict for each of YOUR probes this maneuver lands on.

    Crushing your own probe is WORTHWHILE when EITHER gate clears (it is an OR,
    not an AND):
      * LOOT TIER — the cell under the probe is high value (vein/mass/pure). A
        pure is worth far more than a probe's future vision, so grabbing it
        justifies the crush on its own. (A bare trace does NOT.)
      * FUTURE PROBE UTILITY — you will not need this disk's vision next night:
        it is about to EXPIRE, it is the FINAL night, or you are EXTRACTING the
        SEAM it covers this very outing (the walk already banks mass/pure here,
        so the disk is being consumed anyway).

    Only when NEITHER clears — a bare trace pass-over of a still-useful probe —
    is the verdict AVOID (reroute / land adjacent). ``day``/``day_cap`` are
    optional; without them the final-night signal is simply unused (expiry still
    applies via ``nights_remaining``).
    """
    idx = _cell_index(agent_view)
    nights = _friendly_probe_nights(agent_view)
    final_night = (
        day is not None and day_cap is not None and int(day) >= int(day_cap)
    )
    # "Extracting the seam now": this outing already banks a mass/pure RED cell,
    # so the probe's coverage is being consumed this night regardless.
    extracting_seam = any(
        idx.get(c, ("", 0))[0] == "RED" and _tier(idx[c][1]) in ("mass", "pure")
        for c in (walk or [])
    )
    notes: List[str] = []
    for c in self_hits:
        tile, purity = idx.get(c, ("", 0))
        tier = _tier(purity) if tile == "RED" else "empty"
        high_value = tile == "RED" and tier in ("vein", "mass", "pure")
        nr = nights.get(c)
        # The reason the disk is NOT needed next night (None -> still useful).
        if final_night:
            reason = "it is the FINAL night — its vision is worthless now"
        elif nr is not None and nr <= _NEAR_EXPIRY_NIGHTS:
            reason = f"it is about to expire ({nr} night(s) left) — its vision is nearly spent"
        elif extracting_seam:
            reason = "you strip the seam it covers this outing, so its coverage is already being consumed"
        else:
            reason = None
        not_needed = reason is not None
        cell_s = f"({c[0]},{c[1]})"
        if high_value:
            tail = (
                f"; {reason}, so TAKE it"
                if not_needed
                else " — a WORTHWHILE trade (a pure/mass/high vein outweighs the "
                "disk's future vision; land ADJACENT only if a neighbour holds "
                "equal value and you will re-work this seam)"
            )
            notes.append(
                f"lands on YOUR probe {cell_s} to bank {tier}({purity}){tail}"
            )
        elif not_needed:
            notes.append(
                f"passes over YOUR probe {cell_s} — {reason}, so no live vision is lost"
            )
        else:
            notes.append(
                f"CRUSHES YOUR probe {cell_s} for only {tier or 'empty'} — AVOID: "
                f"reroute / land ADJACENT and keep the disk's vision for next night"
            )
    return notes


def crush_report(
    payload: Mapping[str, Any], agent_view: Mapping[str, Any],
    *, harvest_cells: Optional[Sequence[Cell]] = None,
) -> Dict[str, List[Cell]]:
    """Probe centres this maneuver lands on.

    ``self`` — a harvester drop/step OR a new probe landing on YOUR OWN probe
        centre: you lose that probe's remaining vision (bad).
    ``enemy`` — a probe of yours landing on an ENEMY probe centre: a SUPERSEDE
        (their vision dies, yours survives) — cheap denial (good).
    """
    friendly = _friendly_probe_centres(agent_view)
    enemy = _enemy_probe_centres(agent_view)
    hcells = list(harvest_cells if harvest_cells is not None else walk_cells(payload))
    pcells = probe_cells(payload)

    self_hits: List[Cell] = []
    enemy_hits: List[Cell] = []
    for c in hcells:
        if c in friendly and c not in self_hits:
            self_hits.append(c)
    for c in pcells:
        if c in enemy and c not in enemy_hits:
            enemy_hits.append(c)
        if c in friendly and c not in self_hits:
            self_hits.append(c)
    return {"self": self_hits, "enemy": enemy_hits}


# ── collision risk ─────────────────────────────────────────────────────────
def _enemy_vision(agent_view: Mapping[str, Any]) -> Set[Cell]:
    """Union of every enemy probe's live-vision disk."""
    width, height = _grid_dims(agent_view)
    seen: Set[Cell] = set()
    for (ex, ey) in _enemy_probe_centres(agent_view):
        seen.update(_vision_disk(ex, ey, width, height))
    return seen


def _enemy_armed(weapon_estimates: Optional[Mapping[str, Any]]) -> bool:
    """Is anyone holding anything?

    v1.38 — asked ``emps_max or chaff_max``, which called a seat holding
    100 blue of SNAP unarmed and priced every option as if the night
    were safe. ``has_any`` reads the public total, so it covers whatever
    the game prices rather than the two kinds that existed when this was
    written.
    """
    for e in (weapon_estimates or {}).values():
        probe = getattr(e, "has_any", None)
        if callable(probe):
            if probe():
                return True
            continue
        if getattr(e, "emps_max", 0) > 0 or getattr(e, "chaff_max", 0) > 0:
            return True
    return False


# Chebyshev dilation of the broadcast smear so the PUBLIC footprint covers the
# exact pure + its immediate ring even though the smear is a jittered smear.
_REDSIGN_PUBLIC_RADIUS = 2


def _redsign_public_cells(agent_view: Mapping[str, Any]) -> Set[Cell]:
    """The PUBLIC redsign footprint — contested REGARDLESS of enemy probe vision.

    A redsign is broadcast to EVERY seat (§3.15): the moment a pure(255) is minted
    the whole map gets the warning, so probe vision is irrelevant to whether a
    rival knows to race that seam. We take the broadcast smear cells and dilate
    them a little to cover the exact pure and the mass ring the fight is really
    over. Empty until a redsign is live.
    """
    base = _redsign_cells(agent_view)
    if not base:
        return set()
    width, height = _grid_dims(agent_view)
    out: Set[Cell] = set()
    r = _REDSIGN_PUBLIC_RADIUS
    for (x, y) in base:
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    out.add((nx, ny))
    return out


# A sign this old or newer still DOMINATES the menu: the pure is very likely
# unharvested, so seam options should not be competing for space with ordinary
# chains over the same ground. Past it the seam has been fought over for a while
# and the rest of the board deserves the room back.
_SIGN_FRESH_NIGHTS = 2


def sign_age_nights(agent_view: Mapping[str, Any]) -> Optional[int]:
    """Nights since the FRESHEST live redsign was broadcast, or ``None``.

    Sign age has three consumers — the blind-attack expectation, the
    rival-knowledge label, and (fix 1.5) menu pressure — and they were each
    about to derive it themselves. One definition, read via ``sign_found_day``
    against ``view_day``; ``None`` when no sign is live or none carries a date,
    which callers must treat as "do not ease".
    """
    day = view_day(agent_view)
    if day is None:
        return None
    ages: List[int] = []
    for r in (agent_view.get("redsign") or []):
        if not isinstance(r, Mapping):
            continue
        found = sign_found_day(r)
        if found is not None:
            ages.append(max(0, day - found))
    return min(ages) if ages else None


def redsign_footprint(agent_view: Mapping[str, Any]) -> Set[Cell]:
    """Public alias for the dilated broadcast footprint (fix 1.5)."""
    return _redsign_public_cells(agent_view)


# How far around the touched cell counts as "this seam" when we describe what
# the halo holds. Matches the public footprint dilation, so the claim covers the
# pure + the ring the fight is actually over.
_SEAM_HALO_RADIUS = _REDSIGN_PUBLIC_RADIUS


def _seam_halo(agent_view: Mapping[str, Any], cell: Cell) -> Dict[str, int]:
    """What we can actually SEE of the seam around ``cell``.

    Fix 0.4 (OBS-38). The risk stanza used to assert MASS-RICH on every redsign
    without looking. Counts only cells we can price — fog is reported as unseen
    rather than assumed rich or assumed empty.
    """
    idx = _cell_index(agent_view)
    r = _SEAM_HALO_RADIUS
    pure = mass = seen = 0
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            entry = idx.get((cell[0] + dx, cell[1] + dy))
            if entry is None or entry[0] != "RED":
                continue
            seen += 1
            t = _tier(entry[1])
            if t == "pure":
                pure += 1
            elif t == "mass":
                mass += 1
    return {"pure": pure, "mass": mass, "seen": seen}


def rival_knowledge(
    agent_view: Mapping[str, Any], cell: Cell, day: Optional[int] = None,
) -> Dict[str, Any]:
    """How precisely a RIVAL knows ``cell`` — the three tiers, measured.

    Fix 0.5 (OBS-32), sharpened. The redsign broadcast means every seat is
    COMING whatever this returns; what varies is whether they arrive holding a
    coordinate or an area:

      * ``live``  — a rival probe covers it RIGHT NOW. They have the exact cell,
        what is on it, and the hour you land.
      * ``echo``  — a rival probe covered it and has since expired. No current
        sight, but they wrote the coordinate down: they can drop straight on it.
      * ``blind`` — nobody has ever had eyes on it. They hold the SMEAR and must
        search it.

    ``_enemy_probe_cells`` mixes fresh launches with historical echoes and never
    filters by lifetime, so a naive count over it asserts live sight that may
    have expired days ago. Probes are static once landed, so a probe whose disk
    covers the cell covered it for its whole life.
    """
    width, height = _grid_dims(agent_view)
    lifetime = 0
    try:
        from sea_of_colours.game.tuning import probe_lifetime_nights
        lifetime = int(probe_lifetime_nights() or 0)
    except Exception:
        lifetime = 0

    live = 0
    echo_last = 0
    for row in _enemy_probe_cells(agent_view):
        at = _as_cell(row.get("at"))
        if at is None or cell not in _vision_disk(at[0], at[1], width, height):
            continue
        seen = int(row.get("day_seen") or 0)
        # No lifetime configured (expiry disabled) or no day to measure against
        # -> we cannot prove expiry, so do not claim it either way.
        if not lifetime or day is None:
            live += 1
            continue
        if lifetime - (int(day) - seen) > 0:
            live += 1
        else:
            echo_last = max(echo_last, seen)
    if live:
        return {"tier": "live", "watchers": live}
    if echo_last:
        return {"tier": "echo", "watchers": 0, "last_day": echo_last}
    return {"tier": "blind", "watchers": 0}


# ── tempo: who can reach the pure, and in which hour (v12 fix 3.3) ─────────
def _seat_probes(agent_view: Mapping[str, Any]) -> List[Tuple[str, Cell, int]]:
    """``(owner, centre, day_seen)`` for every rival probe we know of.

    ``_enemy_probe_cells`` dedupes by cell and drops the owner, which is exactly
    the field the tempo line is about. Both intel channels carry it, so read
    them directly rather than widening the shared helper (v7 is frozen).
    """
    out: List[Tuple[str, Cell, int]] = []
    seen: Set[Tuple[str, Cell]] = set()

    def _add(owner: Any, at: Any, day_seen: Any) -> None:
        c = _as_cell(at)
        if c is None or not owner:
            return
        key = (str(owner), c)
        if key in seen:
            return
        seen.add(key)
        try:
            d = int(day_seen) if day_seen is not None else 0
        except (TypeError, ValueError):
            d = 0
        out.append((str(owner), c, d))

    ci = agent_view.get("competitor_intel") or {}
    for row in (ci.get("new_this_day") or []):
        if isinstance(row, Mapping) and row.get("kind") == "enemy_probe_launch":
            _add(row.get("owner"), row.get("at"), row.get("day_seen"))
    for row in (ci.get("persistent_echoes") or []):
        if isinstance(row, Mapping) and row.get("kind") == "enemy_probe":
            _add(row.get("owner"), row.get("at"), row.get("last_seen_day"))
    return out


def time_to_pure(
    agent_view: Mapping[str, Any], cell: Cell, *, day: Optional[int] = None,
) -> Dict[str, str]:
    """Earliest hour each seat could LAND on ``cell`` — ``{"you": "H1", ...}``.

    Fix 3.3 (OBS-44 item 2). This is a restatement of probe disks the card
    already prints, not tempo modelling: a drop needs live coverage at the hour
    it happens, vision is fixed at night start, and a probe launched at H1 opens
    its disk from H2. So a seat covering the cell right now holds H1 and every
    other seat is at H2 or later. Reporting it per seat turns "contested" into
    the one question that decides a redsign night.

    Seats with no known probe are still listed at H2 — they can launch one, and
    the broadcast tells them roughly where. Absence of intel is not absence of
    a rival.
    """
    width, height = _grid_dims(agent_view)
    lifetime = 0
    try:
        from sea_of_colours.game.tuning import probe_lifetime_nights
        lifetime = int(probe_lifetime_nights() or 0)
    except Exception:
        lifetime = 0

    def _alive(day_seen: int) -> bool:
        if not lifetime or day is None:
            return True
        return lifetime - (int(day) - day_seen) > 0

    out: Dict[str, str] = {}
    ours = _friendly_probe_centres(agent_view)
    out["you"] = "H1" if any(
        cell in _vision_disk(px, py, width, height) for (px, py) in ours
    ) else "H2"

    seats: Set[str] = set()
    for row in (agent_view.get("opponents") or []):
        if isinstance(row, Mapping):
            name = row.get("player") or row.get("id") or row.get("name")
            if name:
                seats.add(str(name))
    holders: Set[str] = set()
    for owner, (px, py), seen_day in _seat_probes(agent_view):
        seats.add(owner)
        if _alive(seen_day) and cell in _vision_disk(px, py, width, height):
            holders.add(owner)
    me = str((agent_view.get("meta") or {}).get("player") or "")
    for seat in sorted(seats):
        if seat and seat != me:
            out[seat] = "H1" if seat in holders else "H2"
    return out


def _rival_seat_count(agent_view: Mapping[str, Any]) -> int:
    """Live opponents. Falls back to the seats we have probe intel on, so a view
    without an ``opponents`` roster still escalates rather than silently reading
    as a one-on-one."""
    seats = {
        str(r.get("player") or r.get("id") or r.get("name") or "")
        for r in (agent_view.get("opponents") or [])
        if isinstance(r, Mapping)
    }
    seats |= {owner for owner, _c, _d in _seat_probes(agent_view)}
    seats.discard("")
    seats.discard(str((agent_view.get("meta") or {}).get("player") or ""))
    return len(seats)


def _late_season(agent_view: Mapping[str, Any], day: Optional[int]) -> bool:
    """Final third of the season — where the hold is fullest and there is no
    next night to save a harvester for."""
    if day is None:
        day = (agent_view.get("meta") or {}).get("day")
    cap = (agent_view.get("hud") or {}).get("season_day_cap")
    try:
        d, c = int(day), int(cap)
    except (TypeError, ValueError):
        return False
    return c > 0 and d >= (c * 2) // 3


def collision_risk(
    cells: Sequence[Cell],
    agent_view: Mapping[str, Any],
    weapon_estimates: Optional[Mapping[str, Any]] = None,
    *,
    day: Optional[int] = None,
) -> Tuple[str, str]:
    """(level, reason) for a maneuver over ``cells``.

    Per the user's model: risk is a function of whether a rival KNOWS the cell
    AND how VALUABLE it is (a mass/pure cell a rival can reach is a magnet for a
    contesting drop -> a mutual-kill collision). A rival holding EMP/chaff bumps
    the level and adds a timing warning (the pickup can be jammed).

    A rival "knows" a cell in TWO ways:
      * probe VISION — an enemy probe disk covers it, OR
      * a PUBLIC REDSIGN — the pure was broadcast to every seat (§3.15), so the
        seam is contested even with NO enemy probe on it. "No enemy vision" is a
        TRAP on a redsign: everyone got the warning and is racing you to it.
        Probe vision does not change WHETHER they come, only whether they
        arrive holding a coordinate or an area — see :func:`rival_knowledge`.

    LOW  — nothing you touch is watched OR on a public redsign.
    MED  — a watched cell you touch is only trace/vein/blue value.
    HIGH — a public-redsign cell, a watched mass/pure cell, or a weapon bump.
    """
    watched = _enemy_vision(agent_view)
    public = _redsign_public_cells(agent_view)
    vision_hit = [c for c in cells if c in watched]
    public_hit = [c for c in cells if c in public]
    if not vision_hit and not public_hit:
        return "LOW", "no cell you touch is under enemy vision"

    idx = _cell_index(agent_view)
    armed = _enemy_armed(weapon_estimates)

    if public_hit:
        # The redsign is public, so every seat is racing this pure and the risk
        # floor is HIGH whatever the probes say. What this stanza must NOT do is
        # invent the rest of the picture, which is what it used to do:
        #
        #   fix 0.5 (OBS-32) — it said "CONTESTED regardless of probe vision",
        #   collapsing two very different boards into one sentence. Broadcast
        #   knowledge tells a rival WHERE to look; a probe on the cell tells them
        #   what is there and when you arrive. Both are contested, one far more
        #   urgently, and the agent could not tell them apart.
        #
        #   fix 0.4 (OBS-38) — it asserted "the seam is MASS-RICH" as a fact, on
        #   every redsign, having checked nothing. On a mass-free seam the agent
        #   read that and sized its commitment to a halo that was not there.
        #
        # Both are now measured off the board and reported as what they are.
        level = "HIGH"
        cell = public_hit[0]
        cell_s = f"({cell[0]},{cell[1]})"
        know = rival_knowledge(agent_view, cell, day)
        if know["tier"] == "live":
            n = know["watchers"]
            vision_s = (
                f"and worse, {n} rival probe {'disks light' if n > 1 else 'disk lights'} "
                f"{cell_s} RIGHT NOW — they are not searching for it, they have "
                "the EXACT cell, what is on it, and the hour you land"
            )
        elif know["tier"] == "echo":
            vision_s = (
                f"and a rival probe watched {cell_s} up to day {know['last_day']} "
                "before expiring — nothing lights it now, but they WROTE THE "
                "COORDINATE DOWN and can drop straight onto it"
            )
        else:
            vision_s = (
                f"but no rival has ever had eyes on {cell_s} — they are coming "
                "to the SMEAR AREA, not to this cell, so they must search it "
                "blind and cannot see the hour you land"
            )

        # v1.29 — the two fog branches used to conclude the halo was THIN or
        # UNKNOWN. Both were fair readings of a v1.28 board, where a jackpot
        # was usually an isolated 255 in trace (median 2 mass squares on the
        # whole map). They are wrong now: RULEBOOK §2.2 grades a deposit
        # around every pure, so a jackpot carries a median of 2 mass cells
        # within 1.5 and 5 within 4.5, and "I cannot see mass" no longer
        # licenses "there is no mass".
        #
        # Note what has NOT changed: this still reports fog as fog. OBS-38
        # was asserting MASS-RICH as measured fact having checked nothing,
        # and the fix below is not a return to that — it names the
        # GENERATION PRIOR, explicitly as a prior, and keeps the measured
        # branch above as the only one that claims to have seen anything.
        halo = _seam_halo(agent_view, cell)
        if halo["pure"] or halo["mass"]:
            rich_s = (
                f"you can SEE {halo['pure']} pure + {halo['mass']} mass on this "
                "seam (high reward + denial)"
            )
        elif halo["seen"]:
            rich_s = (
                f"what you can SEE of this seam is {halo['seen']} cell(s) of "
                "trace/vein — but every jackpot is generated inside a deposit, "
                "so the mass is most likely in the cells you CANNOT see rather "
                "than absent"
            )
        else:
            rich_s = (
                "you can see NONE of this seam — but every jackpot is generated "
                "inside a deposit, so the broadcast implies mass around it too, "
                "you just cannot confirm which cells"
            )

        reason = (
            f"PUBLIC redsign — EVERY seat got the broadcast and IS COMING for "
            f"this pure, {vision_s}; {rich_s}, so weigh a fast smash-and-lift "
            "(sure) against sweeping the whole seam (more points, but a longer "
            "walk risks a collision/jam that zeroes unlifted cargo — a risk "
            "that rises with more rivals and later in the season)"
        )
        # v12 fix 3.5 (OBS-37/33) — above HIGH the level stops being an adjective
        # and starts naming a SHAPE. Every redsign floors at HIGH because a public
        # pure is worth contesting; what lifts it further is the number of seats
        # that can actually punish you, and each step up has a different answer.
        rivals = _rival_seat_count(agent_view)
        late = _late_season(agent_view, day)
        # The HIGH floor exists to make the seat GO, and it was being read as a
        # reason to flinch: on a blind, unarmed, one-rival board the agent took
        # the bare two-hour grab in six runs out of six while the full sweep sat
        # beside it. Nobody can punish a walk they cannot see, so say that here,
        # on the line the agent actually reads, rather than only in doctrine.
        if know["tier"] == "blind" and not armed and rivals <= 2:
            reason += (
                "; NOTE — this HIGH is the redsign floor and nothing else: no "
                "rival has ever had eyes on this cell and no weapon is in play, "
                f"so with {rivals} opponent(s) there is nothing here that can "
                "punish a longer walk. The level is telling you to GO, not to "
                "cut the chain — take the full sweep"
            )
        if know["tier"] == "live" and armed and rivals > 1:
            level = "VERY HIGH"
            reason += (
                "; VERY HIGH — a rival can SEE this cell, holds a weapon, and is "
                "not alone. One unit arriving once is not enough here: put TWO "
                "harvesters on the pure, either staged (grab, then a late second "
                "bite) or DROP BLOCK (below)"
            )
        if late and rivals >= 2 and know["tier"] == "live":
            level = "ULTRA HIGH"
            reason += (
                "; ULTRA HIGH — late season, several seats, and live rival eyes "
                "on the cell. Commit everything you have to this pure, "
                "repeatedly; there is no tomorrow to save a unit for"
            )
        if know["tier"] == "live":
            # OBS-33 — the DROP BLOCK. Live rival vision means they can LAND on
            # the cell, not merely race toward it, and the engine answers that
            # specifically: two drops on one square in one hour means NOBODY
            # lands (the pure survives intact for your second unit an hour
            # later), and dropping onto a healthy enemy harvester spills its
            # hold outright. The cost is a damaged unit at 500 credits to
            # repair, so it is a late/high-value play, not a default.
            reason += (
                f"; DROP BLOCK is available: put a harvester on {cell_s} itself. "
                "If you both drop the same hour NEITHER lands, the cell keeps "
                "its value, and your SECOND harvester takes it an hour later. If "
                "they are already standing on it, dropping in damages them and "
                "SPILLS their hold — their pure banks nothing (destroyed, not "
                "transferred to you). Either way it costs you a damaged unit "
                "(500 credits to repair), so spend it on a pure or on the "
                "leader, never on a vein"
            )
    else:
        high_value = [c for c in vision_hit if idx.get(c, ("", 0))[0] == "RED"
                      and _tier(idx[c][1]) in ("mass", "pure")]
        if high_value:
            level = "HIGH"
            reason = (
                f"{len(vision_hit)} cell(s) under enemy vision incl. mass/pure "
                f"{high_value[0]} — a rival can see the value and contest the drop"
            )
        else:
            level = "MED"
            reason = (
                f"{len(vision_hit)} cell(s) under enemy vision (trace/vein/blue value)"
            )

    if armed:
        if level == "MED":
            level = "HIGH"
        reason += "; a rival holds EMP/chaff — stagger the wave and lift EARLY (pickup can be jammed)"
    return level, reason


# ── one-call annotation ────────────────────────────────────────────────────
def annotate(
    payload: Mapping[str, Any],
    agent_view: Mapping[str, Any],
    weapon_estimates: Optional[Mapping[str, Any]] = None,
    *,
    day: Optional[int] = None,
    day_cap: Optional[int] = None,
) -> Dict[str, Any]:
    """Compute the full economics dict for one option payload.

    Returns ``{walk, length, yield, crush, crush_self_notes, risk}`` where
    ``risk`` is a ``(level, reason)`` tuple and ``crush_self_notes`` are the
    per-cell verdicts for any of YOUR probes the maneuver lands on (tier +
    future-utility aware). Deploy options get a real walk/yield; probe-only
    options get an empty walk (they bank nothing) but still carry crush/risk.
    """
    walk = walk_cells(payload)
    yb = yield_breakdown(walk, agent_view)
    crush = crush_report(payload, agent_view, harvest_cells=walk)
    self_notes = self_crush_verdicts(
        crush.get("self") or [], agent_view, walk=walk, day=day, day_cap=day_cap,
    )
    risk = collision_risk(
        walk or probe_cells(payload), agent_view, weapon_estimates, day=day,
    )
    return {
        "walk": walk,
        "length": len(walk),
        "yield": yb,
        "blind": blind_estimate(walk, agent_view),
        "crush_self_notes": self_notes,
        "crush": crush,
        "risk": risk,
    }
