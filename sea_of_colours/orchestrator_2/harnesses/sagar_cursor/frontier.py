"""v10 frontier-probe placement — fuzzy, enemy-aware, edge-seeking exploration.

THE PROBLEM (measured, every seat, every seed):
    The shared probe compiler ranks a placement almost entirely by ``area_gain``
    — how many fog cells the disk reveals — and on an all-fog day-1 board
    ``_seed_candidates`` yields essentially ONE seed: the fog centroid, i.e. the
    map CENTRE. An interior disk reveals the full ~49 cells; an edge disk is
    clipped by the wall and reveals fewer. So the deterministic argmax is always
    the middle, and v1–v9 all open by probing the same central cell — no spread,
    no enemy avoidance, and (in a mirror) three probes stacked on one square.

THE PRINCIPLE (the user's, generalised):
    EXPLORATION is speculative — one open sector is about as good as another, and
    the marginal fog cell in an UNCONTESTED corner is worth more than a duplicate
    cell in the crowded centre. So a frontier probe should be chosen with
    VARIATION, not a single deterministic answer. Assured value (a redsign/echo
    enabler probe) stays deterministic and lives in the shared compiler; this
    module only places the speculative fog-exploration probes.

THE MECHANISM:
    * Generate a RICH candidate set — a coarse grid of disk centres inset one
      probe-radius from every wall, so edge/quadrant placements each still reveal
      a near-full disk (grabbing the sides costs almost no coverage).
    * Score = coverage-BAND (anything revealing ≥70% of the best is treated as
      coverage-equivalent, so a side cell isn't hard-beaten by the centre)
      − enemy-proximity penalty (steer away from ground rivals have worked;
        enemy probe launches are PUBLIC, accumulated season-long in memory)
      − own-history penalty (nomadic doctrine, soft)
      + a light per-seat SECTOR bias (day-1 seats open in different quadrants
        before any enemy data exists).
    * WEIGHTED per-seat sampling (not argmax) with a min-separation between a
      seat's own picks — fuzziness so there is rarely one answer, weighting so a
      genuinely better cell is still likelier, and it always offers something
      while fog remains.

Hermetic: only the v10 harness calls this; v6/v7/v8/v9 are untouched.
"""

from __future__ import annotations

import json as _json
import random
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from sea_of_colours.orchestrator_2.harnesses.sagar_cursor._v7.probe_hints import (
    _PROBE_RADIUS,
    _enemy_probe_cells,
    _friendly_probe_positions,
    _grid_dims,
    _los_cells,
    _vision_disk,
)

# ── scoring knobs ────────────────────────────────────────────────────
# Anything revealing at least this fraction of the best candidate's fog is
# "good enough" on coverage — the band that lets the sampler reach side cells.
_COVERAGE_BAND_FRAC = 0.70
# Enemy avoidance: within this Chebyshev radius of a known enemy landing a probe
# is penalised, ramping to full weight ON the cell. Strong — the whole point.
_ENEMY_RADIUS = 7
_ENEMY_W = 0.70
# Own-history avoidance (nomadic doctrine), softer than enemy.
_OWN_RADIUS = 6
_OWN_W = 0.40
# Per-seat quadrant pull (day-1 spread before enemy data exists).
_SECTOR_W = 0.30
# Candidate grid spacing (~probe diameter → tiled disks with slight overlap).
_GRID_SPACING = 6
# A seat never stacks its OWN picks closer than this; exact-dup hard floor is 4.
_MIN_PICK_SEP = 5
_HARD_DUP = 4


# ── season-long enemy-probe-landing memory ───────────────────────────────
# Enemy probe launches are PUBLIC (§3.15); each seat's view carries them with a
# ``day_seen`` but the echo window can age out. We accumulate the union across
# the season so the scorer steers away from ground rivals have already worked —
# a tidy per-seat record of "who saw what, and which fog is likely already
# mined". Durable: mirrors PILOT_V2's proven ``SOC_AGENT_MEMORY`` row
# (kind='enemy_probe_disk_history', payload {cells:{"x,y":{first_day,last_day,
# count}}, last_updated_day}) so a resumed process re-hydrates it. Process-local
# cache is the fast path; Snowflake is best-effort and never crashes a turn.
_MEM_KIND = "enemy_probe_disk_history"

# key = "session::player" -> {(x,y): {"first_day","last_day","count"}}
_ENEMY_LANDINGS: Dict[str, Dict[Tuple[int, int], Dict[str, int]]] = {}


def _mem_key(session_id: str, player: str) -> str:
    return f"{session_id}::{player}"


def _sf_session(store: Optional[Any]):
    """Live Snowpark session, or None when this game isn't on Snowflake."""
    try:
        from sea_of_colours.snowpark.backend import snowpark_session_for
        return snowpark_session_for(store)
    except Exception:
        return None


def record_enemy_landings(
    session_id: str,
    player: str,
    agent_view: Mapping[str, Any],
    *,
    day: int = 0,
    store: Optional[Any] = None,
    season_name: Optional[str] = None,
) -> Set[Tuple[int, int]]:
    """Fold this turn's public enemy probe markers into the seat's season record
    (in-process + best-effort Snowflake)."""
    key = _mem_key(session_id, player)
    if key not in _ENEMY_LANDINGS:
        _ENEMY_LANDINGS[key] = _hydrate_enemy(session_id, player, store=store)
    cells = _ENEMY_LANDINGS[key]
    changed = False
    for row in _enemy_probe_cells(agent_view):
        at = row.get("at")
        if not (isinstance(at, tuple) and len(at) == 2):
            continue
        try:
            xy = (int(at[0]), int(at[1]))
        except (TypeError, ValueError):
            continue
        rec = cells.get(xy)
        if rec is None:
            cells[xy] = {"first_day": int(day), "last_day": int(day), "count": 1}
        else:
            rec["last_day"] = int(day)
            rec["count"] = int(rec.get("count", 0)) + 1
        changed = True
    if changed:
        _persist_enemy(session_id, player, cells, day, store, season_name)
    return set(cells.keys())


def enemy_landings(
    session_id: str, player: str = "", *, store: Optional[Any] = None,
) -> Set[Tuple[int, int]]:
    """All enemy probe cells this seat has ever seen (hydrates on cold cache)."""
    key = _mem_key(session_id, player)
    if key not in _ENEMY_LANDINGS:
        _ENEMY_LANDINGS[key] = _hydrate_enemy(session_id, player, store=store)
    return set(_ENEMY_LANDINGS[key].keys())


def reset(session_id: Optional[str] = None) -> None:
    """Clear the process-local cache (whole store, or one session's seats)."""
    if session_id is None:
        _ENEMY_LANDINGS.clear()
    else:
        for k in [k for k in _ENEMY_LANDINGS if k.startswith(f"{session_id}::")]:
            _ENEMY_LANDINGS.pop(k, None)


def _persist_enemy(
    session_id: str,
    player: str,
    cells: Mapping[Tuple[int, int], Mapping[str, int]],
    day: int,
    store: Optional[Any],
    season_name: Optional[str],
) -> None:
    session = _sf_session(store)
    if session is None:
        return
    try:
        payload = _json.dumps({
            "cells": {f"{x},{y}": dict(v) for (x, y), v in cells.items()},
            "last_updated_day": int(day),
        })
        session.sql(
            """
            MERGE INTO SOC_AGENT_MEMORY t
            USING (SELECT ? AS session_id, ? AS season_name, ? AS player,
                          ? AS kind, PARSE_JSON(?) AS payload) s
            ON t.session_id = s.session_id AND t.player = s.player AND t.kind = s.kind
            WHEN MATCHED THEN UPDATE SET payload = s.payload,
                                         season_name = s.season_name,
                                         updated_at = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (session_id, season_name, player, kind, payload, updated_at)
                VALUES (s.session_id, s.season_name, s.player, s.kind, s.payload, CURRENT_TIMESTAMP())
            """,
            params=[session_id, season_name or "", player, _MEM_KIND, payload],
        ).collect()
    except Exception:
        pass


def _hydrate_enemy(
    session_id: str, player: str, *, store: Optional[Any] = None,
) -> Dict[Tuple[int, int], Dict[str, int]]:
    out: Dict[Tuple[int, int], Dict[str, int]] = {}
    session = _sf_session(store)
    if session is None:
        return out
    try:
        rows = session.sql(
            """
            SELECT PAYLOAD FROM SOC_AGENT_MEMORY
            WHERE session_id = ? AND player = ? AND kind = ?
            """,
            params=[session_id, player, _MEM_KIND],
        ).collect()
        for r in rows:
            payload = r["PAYLOAD"]
            if isinstance(payload, str):
                payload = _json.loads(payload)
            if not isinstance(payload, dict):
                continue
            for cell_key, meta in (payload.get("cells") or {}).items():
                try:
                    xs, ys = str(cell_key).split(",")
                    xy = (int(xs), int(ys))
                except (ValueError, AttributeError):
                    continue
                out[xy] = {
                    "first_day": int((meta or {}).get("first_day", 0)),
                    "last_day": int((meta or {}).get("last_day", 0)),
                    "count": int((meta or {}).get("count", 1)),
                }
    except Exception:
        return out
    return out


# ── candidate generation + scoring ───────────────────────────────────
def _grid_seeds(width: int, height: int) -> List[Tuple[int, int]]:
    """Disk centres on a coarse grid, inset one probe-radius from each wall.

    Insetting keeps the whole Euclidean disk on-board, so an edge/quadrant
    candidate still reveals a near-full disk — "grab the sides" without paying a
    coverage tax for a clipped disk.
    """
    inset = _PROBE_RADIUS
    xs = list(range(inset, max(inset + 1, width - inset), _GRID_SPACING)) or [width // 2]
    ys = list(range(inset, max(inset + 1, height - inset), _GRID_SPACING)) or [height // 2]
    return [(x, y) for x in xs for y in ys]


def _seat_quadrant_center(
    seat_index: int, width: int, height: int,
) -> Tuple[float, float]:
    """Centre of this seat's preferred opening quadrant (TL, TR, BL, BR…)."""
    q = int(seat_index) % 4
    left = q in (0, 2)
    top = q in (0, 1)
    cx = width * 0.25 if left else width * 0.75
    cy = height * 0.25 if top else height * 0.75
    return cx, cy


def _coverage(
    at: Tuple[int, int], width: int, height: int, los: Set[Tuple[int, int]],
) -> int:
    return sum(1 for c in _vision_disk(at[0], at[1], width, height) if c not in los)


def _nearest(at: Tuple[int, int], cells: Set[Tuple[int, int]]) -> Optional[int]:
    if not cells:
        return None
    return min(max(abs(at[0] - x), abs(at[1] - y)) for (x, y) in cells)


def _falloff(dist: Optional[int], radius: int, weight: float) -> float:
    if dist is None or dist >= radius:
        return 0.0
    return weight * (radius - dist) / radius


def select_frontier_probes(
    agent_view: Mapping[str, Any],
    *,
    session_id: str,
    seat_index: int,
    rng: "random.Random",
    max_hints: int = 3,
    own_history: Sequence[Tuple[int, int]] = (),
    avoid_cells: Sequence[Tuple[int, int]] = (),
    player: str = "",
    store: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Fuzzy, enemy-aware frontier probe hints for this seat/night.

    Returns hints in the shared ``top_probe_hints`` schema
    (``at``/``area_gain``/``edge_promise``/``extends_from``/``contested``) so the
    agency layer and packager consume them unchanged. Empty only when there is no
    fog worth revealing.

    ``avoid_cells`` are cells THIS night's plan already commits a probe to (the
    seam-attack's supersede/cover probes). They are folded into the ``own`` set so
    a frontier (exploration) probe is STRUCTURALLY barred from stacking next to a
    probe the blind attack already drops there — that vision is already bought.
    """
    width, height = _grid_dims(agent_view)
    fog_count = int(((agent_view.get("world") or {}).get("fog_count")) or 0)
    if fog_count == 0:
        return []
    los = _los_cells(agent_view)
    enemy = enemy_landings(session_id, player, store=store)
    own = (
        set(own_history)
        | set(_friendly_probe_positions(agent_view))
        | {(int(x), int(y)) for (x, y) in avoid_cells}
    )

    cands = _grid_seeds(width, height)
    covs = {c: _coverage(c, width, height, los) for c in cands}
    max_cov = max(covs.values(), default=0)
    if max_cov <= 0:
        return []
    band = max(1.0, _COVERAGE_BAND_FRAC * max_cov)
    qcx, qcy = _seat_quadrant_center(seat_index, width, height)
    half_diag = 0.5 * ((width ** 2 + height ** 2) ** 0.5)

    scored: List[Tuple[Tuple[int, int], float, int]] = []
    for c in cands:
        cov = covs[c]
        if cov <= 0:
            continue
        # Hard floor: never a near-duplicate of an own probe (active or historic).
        if any(max(abs(c[0] - p[0]), abs(c[1] - p[1])) < _HARD_DUP for p in own):
            continue
        cov_s = min(1.0, cov / band)
        en = _falloff(_nearest(c, enemy), _ENEMY_RADIUS, _ENEMY_W)
        ow = _falloff(_nearest(c, own), _OWN_RADIUS, _OWN_W)
        dq = max(abs(c[0] - qcx), abs(c[1] - qcy))
        sec = _SECTOR_W * max(0.0, 1.0 - dq / max(1.0, half_diag))
        scored.append((c, cov_s - en - ow + sec, cov))
    if not scored:
        return []

    # Weighted sampling WITHOUT replacement, min-separated picks.
    picks: List[Tuple[Tuple[int, int], int]] = []
    pool = list(scored)
    while pool and len(picks) < max_hints:
        weights = [max(0.01, s) for (_c, s, _cov) in pool]
        j = rng.choices(range(len(pool)), weights=weights, k=1)[0]
        c, _s, cov = pool.pop(j)
        if any(max(abs(c[0] - p[0][0]), abs(c[1] - p[0][1])) < _MIN_PICK_SEP
               for p in picks):
            continue
        picks.append((c, cov))

    return [
        {
            "at": [c[0], c[1]],
            "area_gain": int(cov),
            "edge_promise": 0,
            "extends_from": "frontier",
            "contested": (_nearest(c, enemy) or 99) <= _PROBE_RADIUS,
        }
        for (c, cov) in picks
    ]
