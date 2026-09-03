"""Move-legality validators for tabula.

Each function returns ``(ok, reason)``. On invalid, the harness either
retries the LLM once with the specific error injected, or falls back
to the heuristic-recommended chain.

Phase 1 rules the validators enforce:

* ``drop`` cell must be in LIVE vision (probe disk OR occupied by a
  friendly harvester right now). Echo-only cells reject.
* ``drop`` cell must not be synthetic-green (harvested previously) or
  a GREEN hazard tile.
* Drops onto own-probe cells are ALLOWED (crush rule) — the recorder
  logs the crush event separately.
* ``step`` destination must be Manhattan-1 from the harvester's
  current cell, and not sg/green/oob.
* ``pickup`` must target a harvester that is on the surface (has been
  dropped this turn or was already deployed).
* ``probe`` targets can be fog OR live — probes are what dispel fog.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


def validate_moves(
    moves: Sequence[Mapping[str, Any]],
    agent_view: Mapping[str, Any],
) -> List[Tuple[bool, str, Mapping[str, Any]]]:
    """Run every move through the phase-1 validators.

    Returns a parallel list of ``(ok, reason, move)`` — callers walk
    the list to decide whether to accept the full queue or reject and
    fall back.
    """
    live_cells = _live_vision_cells(agent_view)
    sg_cells = _synthetic_green_cells(agent_view)
    green_cells = _green_hazard_cells(agent_view)
    width, height = _world_dims(agent_view)
    my_probes = _friendly_probe_cells(agent_view)

    # Track harvester position as chain steps mutate it turn-by-turn.
    harvester_pos: Dict[str, Optional[Tuple[int, int]]] = _initial_harvester_positions(
        agent_view
    )

    out: List[Tuple[bool, str, Mapping[str, Any]]] = []
    for m in moves:
        if not isinstance(m, Mapping):
            out.append((False, "move is not a dict", m))
            continue
        act = str(m.get("a") or "")
        if act == "drop":
            ok, reason = _validate_drop(
                m, harvester_pos, live_cells, sg_cells, green_cells, width, height,
                my_probes,
            )
        elif act == "step":
            ok, reason = _validate_step(
                m, harvester_pos, sg_cells, green_cells, width, height,
            )
        elif act == "pickup":
            ok, reason = _validate_pickup(m, harvester_pos)
        elif act == "probe":
            ok, reason = _validate_probe(m, width, height)
            # Hot drop rule (§3.10 simultaneous resolution): a probe
            # launched at hour K makes its live-vision disk available for
            # moves at hour K+1 onward. Extend the live-vision set so a
            # subsequent drop inside that disk validates. Previously we
            # froze live_cells at turn-start and rejected legal hot-drop
            # chains ("drop @ (15,10) not in live vision" even when the
            # probe at (15,10) was one hour earlier).
            #
            # CRITICAL: use the engine's **Euclidean radius-4 disk**
            # (dx*dx+dy*dy<=16, ~49 cells) — NOT the Chebyshev 9x9 box
            # (81 cells). Marking the four box corners as live let corner
            # hot-drops pass validation and then get rejected by the
            # engine (`tiles_visible_now`), stranding the harvester and
            # crashing the whole chain (see night-1 (36,20) off probe
            # (33,17): a box corner, dist 4.24 > 4, rejected).
            if ok:
                at = m.get("at")
                if isinstance(at, (list, tuple)) and len(at) == 2:
                    try:
                        px, py = int(at[0]), int(at[1])
                    except (TypeError, ValueError):
                        px = py = None
                    if px is not None:
                        for dx in range(-4, 5):
                            for dy in range(-4, 5):
                                if dx * dx + dy * dy > 16:
                                    continue
                                cx, cy = px + dx, py + dy
                                if 0 <= cx < width and 0 <= cy < height:
                                    live_cells.add((cx, cy))
        else:
            ok, reason = False, f"unknown action '{act}' in phase 1"
        out.append((ok, reason, m))
    return out


# ── Per-action validators ─────────────────────────────────────────────


def _validate_drop(
    move: Mapping[str, Any],
    harvester_pos: Dict[str, Optional[Tuple[int, int]]],
    live_cells: set,
    sg_cells: set,
    green_cells: set,
    width: int,
    height: int,
    my_probes: set,
) -> Tuple[bool, str]:
    unit = str(move.get("unit") or "")
    at = move.get("at")
    if not (isinstance(at, (list, tuple)) and len(at) == 2):
        return False, "drop.at must be [x, y]"
    try:
        x, y = int(at[0]), int(at[1])
    except (TypeError, ValueError):
        return False, "drop.at coords not integers"
    if not (0 <= x < width and 0 <= y < height):
        return False, f"drop.at ({x},{y}) out of grid bounds"
    if (x, y) in sg_cells:
        return False, f"drop.at ({x},{y}) is synthetic-green"
    if (x, y) in green_cells:
        return False, f"drop.at ({x},{y}) is a GREEN hazard tile"
    if (x, y) not in live_cells:
        return False, (
            f"drop.at ({x},{y}) is not in live vision — must be inside a probe "
            f"disk or on a friendly surface unit's tile"
        )
    if unit not in harvester_pos:
        return False, f"drop.unit '{unit}' is not a known harvester"
    if harvester_pos[unit] is not None:
        return False, f"drop.unit '{unit}' is already on the surface at {harvester_pos[unit]}"
    # Crush rule: drop on friendly probe cell is legal; log via recorder.
    harvester_pos[unit] = (x, y)
    return True, "ok" if (x, y) not in my_probes else "ok (crushes friendly probe)"


def _validate_step(
    move: Mapping[str, Any],
    harvester_pos: Dict[str, Optional[Tuple[int, int]]],
    sg_cells: set,
    green_cells: set,
    width: int,
    height: int,
) -> Tuple[bool, str]:
    unit = str(move.get("unit") or "")
    to = move.get("to")
    if not (isinstance(to, (list, tuple)) and len(to) == 2):
        return False, "step.to must be [x, y]"
    try:
        tx, ty = int(to[0]), int(to[1])
    except (TypeError, ValueError):
        return False, "step.to coords not integers"
    if not (0 <= tx < width and 0 <= ty < height):
        return False, f"step.to ({tx},{ty}) out of grid bounds"
    if (tx, ty) in sg_cells:
        return False, f"step.to ({tx},{ty}) is synthetic-green"
    if (tx, ty) in green_cells:
        return False, f"step.to ({tx},{ty}) is a GREEN hazard tile"
    if unit not in harvester_pos or harvester_pos[unit] is None:
        return False, f"step.unit '{unit}' is not currently on the surface"
    cx, cy = harvester_pos[unit]  # type: ignore[misc]
    # Engine's ``_adj`` uses Manhattan-1 (N/S/E/W only — NO diagonals).
    # Prior versions of this validator used Chebyshev-1 which passed
    # diagonal steps that the engine then rejected as "not adjacent".
    if abs(tx - cx) + abs(ty - cy) != 1:
        return False, (
            f"step.to ({tx},{ty}) is not Manhattan-1 (N/S/E/W only) "
            f"from current ({cx},{cy})"
        )
    harvester_pos[unit] = (tx, ty)
    return True, "ok"


def _validate_pickup(
    move: Mapping[str, Any],
    harvester_pos: Dict[str, Optional[Tuple[int, int]]],
) -> Tuple[bool, str]:
    unit = str(move.get("unit") or "")
    if unit not in harvester_pos or harvester_pos[unit] is None:
        return False, f"pickup.unit '{unit}' is not on the surface"
    # Pickup keeps the harvester where it is, then lifts it to orbit for
    # the resolve step; from a validation standpoint the unit is done.
    harvester_pos[unit] = None
    return True, "ok"


def _validate_probe(
    move: Mapping[str, Any],
    width: int,
    height: int,
) -> Tuple[bool, str]:
    at = move.get("at")
    if not (isinstance(at, (list, tuple)) and len(at) == 2):
        return False, "probe.at must be [x, y]"
    try:
        x, y = int(at[0]), int(at[1])
    except (TypeError, ValueError):
        return False, "probe.at coords not integers"
    if not (0 <= x < width and 0 <= y < height):
        return False, f"probe.at ({x},{y}) out of grid bounds"
    return True, "ok"


# ── State-projection helpers ─────────────────────────────────────────


def _world_dims(agent_view: Mapping[str, Any]) -> Tuple[int, int]:
    world = agent_view.get("world") or {}
    try:
        return int(world.get("width") or 0), int(world.get("height") or 0)
    except (TypeError, ValueError):
        return 0, 0


def _live_vision_cells(agent_view: Mapping[str, Any]) -> set:
    """Cells the seat can currently see (either from a probe disk or a
    friendly unit's tile). Reads ``world.live[]`` when present; falls
    back to walking ``world.grid`` non-None cells.
    """
    world = agent_view.get("world") or {}
    live: set = set()
    for row in (world.get("live") or []):
        if isinstance(row, Mapping):
            try:
                live.add((int(row["x"]), int(row["y"])))
            except (TypeError, KeyError, ValueError):
                continue
    if not live and isinstance(world.get("grid"), list):
        for y, r in enumerate(world["grid"]):
            if not isinstance(r, list):
                continue
            for x, cell in enumerate(r):
                if cell is not None:
                    live.add((x, y))
    return live


def _synthetic_green_cells(agent_view: Mapping[str, Any]) -> set:
    """Cells the seat has already harvested (lineage=synthetic on GREEN).

    Reads ``track_harvests.<player>`` if surfaced on the view; otherwise
    walks the grid looking for cells flagged synthetic.
    """
    world = agent_view.get("world") or {}
    out: set = set()
    for row in (world.get("live") or []):
        if not isinstance(row, Mapping):
            continue
        if str(row.get("tile") or "") == "GREEN" and row.get("lineage") == "synthetic":
            try:
                out.add((int(row["x"]), int(row["y"])))
            except (TypeError, KeyError, ValueError):
                continue
    if isinstance(world.get("grid"), list):
        for y, r in enumerate(world["grid"]):
            if not isinstance(r, list):
                continue
            for x, cell in enumerate(r):
                if isinstance(cell, Mapping) and cell.get("synthetic"):
                    out.add((x, y))
    return out


def _green_hazard_cells(agent_view: Mapping[str, Any]) -> set:
    """Natural (non-synthetic) GREEN cells — the phase-1 rules say no-step."""
    world = agent_view.get("world") or {}
    out: set = set()
    for row in (world.get("live") or []):
        if not isinstance(row, Mapping):
            continue
        if str(row.get("tile") or "") == "GREEN" and row.get("lineage") != "synthetic":
            try:
                out.add((int(row["x"]), int(row["y"])))
            except (TypeError, KeyError, ValueError):
                continue
    if isinstance(world.get("grid"), list):
        for y, r in enumerate(world["grid"]):
            if not isinstance(r, list):
                continue
            for x, cell in enumerate(r):
                if (
                    isinstance(cell, Mapping)
                    and str(cell.get("tile") or "") == "GREEN"
                    and not cell.get("synthetic")
                ):
                    out.add((x, y))
    return out


def _friendly_probe_cells(agent_view: Mapping[str, Any]) -> set:
    """Cells occupied by our own probes right now — for crush detection."""
    entities = (agent_view.get("entities") or {}).get("mine") or []
    out: set = set()
    for e in entities:
        if not isinstance(e, Mapping):
            continue
        if str(e.get("type") or "") != "probe":
            continue
        pos = e.get("pos")
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            try:
                out.add((int(pos[0]), int(pos[1])))
            except (TypeError, ValueError):
                continue
    return out


def _initial_harvester_positions(
    agent_view: Mapping[str, Any],
) -> Dict[str, Optional[Tuple[int, int]]]:
    """Snapshot ``harvester_id -> (x, y) or None`` at turn start.

    Non-orbit harvesters (surface state at scenario start) keep their
    position; orbit-state harvesters map to ``None`` so a subsequent
    ``drop`` transitions them to the surface.
    """
    entities = (agent_view.get("entities") or {}).get("mine") or []
    positions: Dict[str, Optional[Tuple[int, int]]] = {}
    for e in entities:
        if not isinstance(e, Mapping):
            continue
        if str(e.get("type") or "") != "harvester":
            continue
        uid = str(e.get("id") or "")
        if not uid:
            continue
        pos = e.get("pos")
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            try:
                positions[uid] = (int(pos[0]), int(pos[1]))
            except (TypeError, ValueError):
                positions[uid] = None
        else:
            positions[uid] = None
    return positions
