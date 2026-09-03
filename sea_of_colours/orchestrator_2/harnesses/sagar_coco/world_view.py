"""sagar_coco — the WORLD VIEW: a JSON snapshot of everything you can SEE.

Replaces v11's two narrow Section-2 blocks (``VISIBLE RED CELLS`` +
``SYNTHETIC-GREEN``) with a single JSON list of the live board.

Design (option C — "contents only, visibility via DROP-LEGAL"):
  * We list ONLY cells that carry contents (RED / GREEN / BLUE) or a PROBE.
  * Plain empty-but-visible cells are NOT listed — a probe's r4 disk IS its
    visible area, and those disks are already enumerated verbatim in the
    ``DROP-LEGAL ZONES`` block. Re-listing ~80 ``{"c":"none"}`` objects per
    turn would be pure token filler and a second source of truth for "what
    can I see". So the header tells the agent: not-listed = empty-in-view
    (see DROP-LEGAL) OR fog / old vision (still walkable + probe-targetable).
  * ``tier`` drives the RED read; ``val`` (purity) rides along on RED only,
    where the within-tier gradient matters for EV. GREEN / BLUE carry colour
    alone. The only entity that persists between turns is the PROBE, so it is
    the only occupant surfaced here (owner = "mine" or the rival seat label);
    harvesters, EMP detonations and destroyed assets live in LAST NIGHT.

Pure function over the engine ``agent_view``; no state, no persistence.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Set, Tuple

Cell = Tuple[int, int]


def _tier(purity: int) -> str:
    # Canonical engine bands — ONLY purity==255 is pure.
    p = int(purity or 0)
    if p >= 255:
        return "pure"
    if p >= 151:
        return "mass"
    if p >= 51:
        return "vein"
    return "trace"


def _my_probe_cells(agent_view: Mapping[str, Any]) -> Set[Cell]:
    """Cells occupied by one of MY probes (from ``entities.mine``)."""
    out: Set[Cell] = set()
    mine = (agent_view.get("entities") or {}).get("mine") or []
    for e in mine:
        if not isinstance(e, Mapping):
            continue
        if str(e.get("type") or "") != "probe":
            continue
        pos = e.get("pos")
        if (
            isinstance(pos, (list, tuple))
            and len(pos) == 2
            and pos[0] is not None
            and pos[1] is not None
        ):
            out.add((int(pos[0]), int(pos[1])))
    return out


def _live_rows(agent_view: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    """Visible cells from ``world.live`` (2D ``grid`` fallback)."""
    world = agent_view.get("world") or {}
    rows: List[Mapping[str, Any]] = []
    for row in (world.get("live") or []):
        if isinstance(row, Mapping) and "x" in row and "y" in row:
            rows.append(row)
    if rows or not isinstance(world.get("grid"), list):
        return rows
    for y, r in enumerate(world["grid"]):
        if not isinstance(r, list):
            continue
        for x, cell in enumerate(r):
            if isinstance(cell, Mapping) and cell.get("tile"):
                rows.append({**cell, "x": x, "y": y})
    return rows


_HEADER = (
    "WORLD VIEW — everything you can SEE right now, as a JSON list.\n"
    "  A cell NOT listed below is one of:\n"
    "    (a) EMPTY but in view — you see it, there's just nothing on it. Your\n"
    "        live vision is exactly your probe disks; those cells are the\n"
    "        DROP-LEGAL ZONES below. Anything inside a disk and not listed\n"
    "        here is empty, walkable ground.\n"
    "    (b) FOG / old vision — outside your current line of sight. Fogged\n"
    "        cells are STILL walkable and probe-targetable; you just have no\n"
    "        direct vision of what is on them right now.\n"
    "  Fields: at = (x,y) — same coords as every other block · c =\n"
    "  red|blue|green|none · RED carries tier (pure/mass/vein/trace) · BLUE\n"
    "  carries val (fissile purity 0-255; blue has no tier) · probe = who\n"
    "  owns a probe on that cell (\"mine\" or the rival seat). Probes are the\n"
    "  ONLY board entity that persists between nights; harvesters / EMPs /\n"
    "  destroyed assets are in LAST NIGHT, not here."
)


def format_world_view_block(agent_view: Mapping[str, Any]) -> str:
    """Render the WORLD VIEW JSON block (option C — contents + probes only)."""
    rows = _live_rows(agent_view)
    my_probes = _my_probe_cells(agent_view)

    reds: List[Tuple[int, Dict[str, Any]]] = []
    blues: List[Dict[str, Any]] = []
    greens: List[Dict[str, Any]] = []
    probe_only: List[Dict[str, Any]] = []

    for row in rows:
        try:
            x, y = int(row["x"]), int(row["y"])
        except (TypeError, KeyError, ValueError):
            continue
        tile = str(row.get("tile") or "")

        probe_owner: str | None = None
        ent = row.get("entity")
        if isinstance(ent, Mapping) and str(ent.get("kind") or "") == "probe":
            probe_owner = (
                "mine" if (x, y) in my_probes
                else str(ent.get("owner") or "enemy")
            )

        at = f"({x},{y})"
        if tile == "RED":
            p = int(row.get("purity") or 0)
            cell: Dict[str, Any] = {"at": at, "c": "red", "tier": _tier(p)}
            if probe_owner:
                cell["probe"] = probe_owner
            reds.append((p, cell))
        elif tile == "BLUE":
            cell = {"at": at, "c": "blue", "val": int(row.get("purity") or 0)}
            if probe_owner:
                cell["probe"] = probe_owner
            blues.append(cell)
        elif tile == "GREEN":
            cell = {"at": at, "c": "green"}
            if probe_owner:
                cell["probe"] = probe_owner
            greens.append(cell)
        elif probe_owner:
            # Empty ground that nonetheless holds a probe — the one
            # "c":"none" row worth emitting.
            probe_only.append({"at": at, "c": "none", "probe": probe_owner})

    reds.sort(key=lambda t: -t[0])
    ordered: List[Dict[str, Any]] = (
        [c for _, c in reds] + blues + greens + probe_only
    )

    lines: List[str] = [_HEADER, ""]
    if not ordered:
        lines.append("cells: []   (only empty ground / fog in view)")
        return "\n".join(lines) + "\n"

    lines.append("cells:")
    lines.append("[")
    for i, cell in enumerate(ordered):
        tail = "," if i < len(ordered) - 1 else ""
        lines.append("  " + json.dumps(cell, separators=(",", ":")) + tail)
    lines.append("]")
    return "\n".join(lines) + "\n"
