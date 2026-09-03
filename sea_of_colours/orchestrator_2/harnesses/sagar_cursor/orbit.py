"""Orbit submission — the fleet-recovery gate over the seat's buying policy.

The buying policy itself lives in :mod:`.orbit_policy` and is **fork-local**
(v1.40): each agent owns what it spends credits and BLUE on. This module is
the submission path plus one gate that sits on top of it.

The gate (R4): v9's audit found v10-lineage seats ran a whole 7-night season
on 1–2 harvesters (the mirror ran on ONE). The buying policy only builds a
harvester once credits clear the full 1500c target — so after a harvester
dies, or early when the seat is still on one unit, credits get nibbled by
speculative probe builds and the fleet never recovers. A single harvester
caps banked RED hard.

So ONLY in the fleet-short window (day ≤4 on a single harvester, or a
harvester died last night) this trims probe builds back to a hot-drop floor
and either injects the recovery harvester when it is affordable this turn or
conserves the freed credits toward it next turn. It NEVER fabricates an
unaffordable build (the engine charges the real cost at resolution) and NEVER
drops below the probe floor the multiprobe doctrine needs.

Hermetic: only this fork's orbit path calls it; v6/v7/v8/v9 are untouched.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Mapping, Tuple

from sea_of_colours.snowpark import engine as soc_engine

INNER_AGENT_LABEL = "SAGAR_CURSOR"

# Keep at least this many probes in the magazine even while conserving for a
# harvester — a hot drop needs an enabler probe, so we never starve to zero.
_PROBE_FLOOR = 2
# Emergency-recovery day window: on/under this day a single-harvester seat should
# prioritise a second unit over speculative probes 3-4.
_EARLY_DAY = 4


def _died_last_night(agent_view: Mapping[str, Any]) -> int:
    ln = agent_view.get("last_night") or {}
    return sum(
        1 for r in (ln.get("my_assets_destroyed") or [])
        if isinstance(r, Mapping) and str(r.get("kind")) == "harvester"
    )


def apply_economy_gate(
    agent_view: Mapping[str, Any],
    actions: List[Dict[str, Any]],
    *,
    day: int,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Return (actions, notes) with the R4 fleet-recovery gate applied.

    No-op unless the seat is fleet-short (early single-harvester, or lost one
    last night) and below the harvester cap and the base plan didn't already
    build one.
    """
    orbit = agent_view.get("orbit") or {}
    credits = int(orbit.get("credits", 0) or 0)
    cap_used = int(orbit.get("harvester_cap_used", 0) or 0)
    cap_max = int(orbit.get("harvester_cap_max", 3) or 3)
    actions_max = int(orbit.get("actions_max", 3) or 3)
    probe_stock = int(orbit.get("probe_stock", 0) or 0)
    prices = orbit.get("ship_prices") or {}
    harvester_cost = int(prices.get("harvester_build", 1500) or 1500)
    probe_cost = int(prices.get("probe_build", 500) or 500)

    alive = cap_used
    died = _died_last_night(agent_view)
    has_build = any(a.get("a") == "build_harvester" for a in actions)

    fleet_short = (int(day) <= _EARLY_DAY and alive <= 1) or died >= 1
    if not fleet_short or cap_used >= cap_max or has_build:
        return actions, []

    notes: List[str] = []
    out: List[Dict[str, Any]] = []
    projected_stock = probe_stock
    freed = 0
    for a in actions:
        if a.get("a") == "build_probe":
            want = int(a.get("count", 0) or 0)
            room = max(0, _PROBE_FLOOR - projected_stock)
            keep = min(want, room)
            if keep > 0:
                out.append({"a": "build_probe", "count": keep})
                projected_stock += keep
            trimmed = want - keep
            if trimmed > 0:
                freed += trimmed * probe_cost
                notes.append(
                    f"R4: trimmed {trimmed} speculative probe build(s) "
                    f"(floor {_PROBE_FLOOR}) to prioritise the fleet"
                )
            continue
        out.append(a)

    # Try to seat the recovery harvester THIS turn if it now fits.
    if credits >= harvester_cost and len(out) < actions_max:
        # Insert after repairs so a damaged unit is still fixed first.
        insert_at = 0
        for i, a in enumerate(out):
            if a.get("a") == "repair":
                insert_at = i + 1
        out.insert(insert_at, {"a": "build_harvester"})
        notes.append(
            f"R4: built recovery harvester ({harvester_cost}c; "
            f"{'lost one last night' if died else 'fleet-short early'})"
        )
    elif freed:
        notes.append(
            f"R4: conserving {freed}c toward a harvester next turn "
            f"({credits}/{harvester_cost}c, fleet {alive}/{cap_max})"
        )

    return out, notes


def submit_orbit(
    store: Any, session_id: str, player: str, view: Mapping[str, Any],
) -> Mapping[str, Any]:
    """v10 orbit submission: shared playbook + R4 economy gate.

    Falls back to an empty orbit on any exception so a broken gate can never
    block a season (mirrors the shared stub's safety contract). Honors
    ``TABULA_V7_ORBIT_MODE=empty`` for the regression-baseline path.
    """
    if os.environ.get("TABULA_V7_ORBIT_MODE", "heuristic").strip().lower() == "empty":
        from sea_of_colours.orchestrator_2.harnesses.sagar_cursor._v7 import (
            orbit_stub as v7_orbit,
        )
        env = v7_orbit.submit_empty_orbit(store, session_id, player)
        env["agent_id"] = INNER_AGENT_LABEL
        return env
    started = time.time()
    try:
        from sea_of_colours.orchestrator_2.harnesses.sagar_cursor.orbit_policy import (
            plan_orbit_actions,
        )
        agent_view = view.get("agent_view") or view
        actions, rationale = plan_orbit_actions(agent_view)
        hud = agent_view.get("hud") or {}
        meta = agent_view.get("meta") or {}
        day = int(meta.get("day") or hud.get("day") or 0)
        actions, notes = apply_economy_gate(agent_view, list(actions), day=day)
        if notes:
            rationale = rationale + " | " + " ".join(notes)
        result = soc_engine.submit_orbit_actions(
            store, session_id, player, list(actions),
        )
        ms_elapsed = int((time.time() - started) * 1000)
        return {
            "ok": True,
            "agent_id": INNER_AGENT_LABEL,
            "runtime": "harness_in_process",
            "rationale": f"[orbit heuristic] {rationale}",
            "orbit_actions_count": len(actions),
            "ms_elapsed": ms_elapsed,
            "submit_result": result,
        }
    except Exception as exc:  # pragma: no cover — defensive fallback
        from sea_of_colours.orchestrator_2.harnesses.sagar_cursor._v7 import (
            orbit_stub as v7_orbit,
        )
        env = v7_orbit.submit_empty_orbit(store, session_id, player)
        env["agent_id"] = INNER_AGENT_LABEL
        env["rationale"] = (
            f"[orbit v10 economy FAILED: {type(exc).__name__}: {exc}] "
            "fell back to empty orbit — parcels will not ship this turn"
        )
        return env
