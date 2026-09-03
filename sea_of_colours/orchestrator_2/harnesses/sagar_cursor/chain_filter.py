"""v10 chain-hint post-filter — dedupe by BODY overlap + a minimum-value floor.

The shared v7 :func:`heuristic_chains.top_chain_hints` dedupes candidate chains
only by their START cell, so two chains that begin one cell apart but then walk
the SAME dense seam both survive — the agent then "double-digs" one small
cluster with two harvesters (A8, the seed-69 day-5 wart), wasting a unit. It
also happily offers pure-TRACE chains (peak purity ~3) when nothing better is
around, which the agent mistakes for real red.

This v10-side filter (kept OUT of the shared v7 module so v7/v8 are untouched):
  * VALUE FLOOR — drop chains whose richest cell is below the vein floor
    (trace-only junk). Never strips to empty: if EVERY chain is sub-floor the
    original set is returned so the heuristic safety-net still has something.
  * BODY-OVERLAP DEDUPE — walking richest-first, drop a chain that shares more
    than ``overlap_max`` of its cells with an already-kept (richer) chain.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence, Set, Tuple

from sea_of_colours.orchestrator_2.harnesses.sagar_cursor._v7.heuristic_chains import (
    _TIER_MULT,
    _tier_name,
)

# Vein floor (matches ``_tier_name``: purity >= 51 is a real vein). A chain
# whose BEST cell is below this is trace-only.
_FLOOR_PURITY = 51
# Two chains sharing more than half their cells are "the same dig".
_OVERLAP_MAX = 0.5


def _chain_ev(hint: Mapping[str, Any]) -> float:
    ev = 0.0
    for p in hint.get("purities") or []:
        try:
            pi = int(p)
        except (TypeError, ValueError):
            continue
        ev += pi * _TIER_MULT.get(_tier_name(pi), 1.0)
    return ev


def _peak_purity(hint: Mapping[str, Any]) -> int:
    best = 0
    for p in hint.get("purities") or []:
        try:
            best = max(best, int(p))
        except (TypeError, ValueError):
            continue
    return best


def _cells(hint: Mapping[str, Any]) -> Set[Tuple[int, int]]:
    out: Set[Tuple[int, int]] = set()
    for c in hint.get("cells") or []:
        if isinstance(c, (list, tuple)) and len(c) == 2:
            try:
                out.add((int(c[0]), int(c[1])))
            except (TypeError, ValueError):
                continue
    return out


def dedupe_and_floor(
    chain_hints: Sequence[Mapping[str, Any]],
    *,
    floor_purity: int = _FLOOR_PURITY,
    overlap_max: float = _OVERLAP_MAX,
) -> List[Dict[str, Any]]:
    """Filter chain hints: apply the value floor, then body-overlap dedupe.

    Returns a NEW list (richest-first). Never returns empty when the input was
    non-empty — the harness relies on chain hints as a safety net.
    """
    hints = [dict(h) for h in (chain_hints or []) if isinstance(h, Mapping)]
    if not hints:
        return []

    above = [h for h in hints if _peak_purity(h) >= floor_purity]
    pool = above or hints  # never strip to empty

    pool.sort(key=_chain_ev, reverse=True)
    kept: List[Dict[str, Any]] = []
    kept_cells: List[Set[Tuple[int, int]]] = []
    for h in pool:
        cells = _cells(h)
        if not cells:
            continue
        duplicate = False
        for kc in kept_cells:
            inter = len(cells & kc)
            if inter and inter / len(cells) > overlap_max:
                duplicate = True
                break
        if duplicate:
            continue
        kept.append(h)
        kept_cells.append(cells)
    return kept or pool
