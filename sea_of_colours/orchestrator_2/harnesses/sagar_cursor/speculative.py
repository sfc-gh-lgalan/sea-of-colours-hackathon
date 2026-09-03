"""v10 speculative hot-drop picker — vary WHICH bluesign + WHICH drop, per seat.

THE PROBLEM (the user's night-1 observation):
    "the entire board is up, and surely there are multiple equally viable
     bluesigns — per bluesign there are multiple drop points. Why pick the exact
     same thing?" The shared ``top_hot_drop_hints`` flattens every bright cluster
     cell into one list and takes the raw-intensity ARGMAX, so the single
     brightest cell wins for every seat — three harvesters onto one square.

THE PRINCIPLE:
    A bluesign drop is SPECULATIVE (purity is unknown until you land). Among
    clusters of comparable brightness, one is about as good as another, so the
    pick should VARY. Assured red (live LOS / echo-backed) is NOT routed here —
    it stays greedy in the shared chain compiler.

THE MECHANISM (two weighted samples, per-seat rng):
    1. WHICH cluster — sample among clusters weighted by peak brightness, so a
       genuinely brighter cluster is likelier but not guaranteed.
    2. WHICH drop cell — within the chosen cluster, sample a bright cell weighted
       by intensity. Different seats (different rng) land on different cells of
       the same seam instead of stacking the peak.

The probe is launched ON the drop cell (its disk covers the drop for the K+1
landing). Comb SHAPE is left to the agency layer (STRETCH/SWEEP/SAMPLE menu);
these hints carry an empty ``comb_path`` and ``varied=True`` so the geometric
pincer safety-net leaves them alone (they are already per-seat distinct).
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from sea_of_colours.orchestrator_2.harnesses.sagar_cursor._v7.probe_hints import (
    _BLUESIGN_BRIGHT_THRESHOLD,
    _grid_dims,
    _los_cells,
)


def _clusters(agent_view: Mapping[str, Any]) -> List[List[Tuple[int, int, float]]]:
    """Bright cells grouped BY cluster (keeps seam identity, unlike the shared
    flattener which merges every cluster into one dict)."""
    out: List[List[Tuple[int, int, float]]] = []
    for cluster in (agent_view.get("blue_sign") or []):
        if not isinstance(cluster, Mapping):
            continue
        cells: List[Tuple[int, int, float]] = []
        for cell in (cluster.get("cells") or []):
            if not isinstance(cell, (list, tuple)) or len(cell) < 3:
                continue
            try:
                x, y, inten = int(cell[0]), int(cell[1]), float(cell[2])
            except (TypeError, ValueError):
                continue
            if inten >= _BLUESIGN_BRIGHT_THRESHOLD:
                cells.append((x, y, inten))
        if cells:
            out.append(cells)
    return out


def _weighted_pop(
    pool: List[Any], weight: "callable", rng: "random.Random",
) -> Any:
    weights = [max(0.01, float(weight(x))) for x in pool]
    j = rng.choices(range(len(pool)), weights=weights, k=1)[0]
    return pool.pop(j)


def sample_bluesign_hotdrops(
    agent_view: Mapping[str, Any],
    *,
    rng: "random.Random",
    max_hints: int = 2,
    spent: Optional[Set[Tuple[int, int]]] = None,
) -> List[Dict[str, Any]]:
    """Per-seat sampled bluesign hot-drop hints (schema-compatible with
    ``top_hot_drop_hints``). Empty when there is no bright bluesign to gamble on.

    ``spent`` is the seat's remembered union of bluesign cells PROVEN empty
    (:func:`hazard_memory.load_spent_blue`). Fix 0.7 (OBS-45): the bluesign is
    generation-time geometry that never retires, and this sampler PREFERS fog —
    so a pocket the seat mined out itself was promoted straight back up the
    moment its probe moved on and the cell re-fogged. Red retires on exhaustion;
    this is blue's missing mechanism.
    """
    clusters = _clusters(agent_view)
    if not clusters:
        return []
    width, height = _grid_dims(agent_view)
    los = _los_cells(agent_view)
    dead = set(spent or ())

    # Drop cells we know are empty, and clusters that are nothing BUT those.
    # Done before sampling so a spent pocket cannot win the cluster draw and
    # then quietly fall back to its own dead cells below.
    clusters = [
        live for live in (
            [c for c in cluster if (c[0], c[1]) not in dead]
            for cluster in clusters
        ) if live
    ]
    if not clusters:
        return []

    hints: List[Dict[str, Any]] = []
    pool = list(clusters)
    while pool and len(hints) < max_hints:
        cluster = _weighted_pop(pool, lambda c: max(i for _x, _y, i in c), rng)
        # Prefer drop cells still in FOG (an in-LOS bluesign is already seen —
        # no probe needed); fall back to any bright cell if all are lit.
        fog_cells = [c for c in cluster if (c[0], c[1]) not in los] or cluster
        drop_pool = list(fog_cells)
        drop = _weighted_pop(drop_pool, lambda c: c[2], rng)
        dx, dy = int(drop[0]), int(drop[1])
        # Alt drops = the other bright cells of THIS seam (drop-offset options).
        alts = [
            [c[0], c[1]] for c in cluster
            if (c[0], c[1]) != (dx, dy)
        ][:3]
        hints.append({
            "probe_at": [dx, dy],
            "drop_at": [dx, dy],
            "signal_type": "blue_sign",
            "comb_path": [],
            "alt_drops": alts,
            "mine": False,
            "contested": False,
            "varied": True,
        })
    return hints
