"""Prompt assembly for tabula.

Combines four blocks into one prompt:

1. RULES_SUMMARY (from :mod:`.rules`, static)
2. YOUR STATE — day, vault_score (shipped), hoard_red_value (held/unshipped),
   harvesters_alive, probes_alive, visible RED cells
3. YOUR MEMORY — last 3 memory entries formatted as prose
4. HEURISTIC SUGGESTIONS — top-3 chain hints from :mod:`.heuristic_chains`
5. ACTION SCHEMA — strict JSON output contract

Phase 1 output schema (v5 with predict/reflect):
{
  "reflection_on_last_night": {                 -- null on night 1
    "predicted": "high|medium|low",
    "actual": <int>,
    "gap_reason": "one sentence"
  } | null,
  "plan_this_turn": "one sentence",
  "rationale": "one line on WHY",
  "predicted_outcome": {
    "banked_pts_estimate": "high|medium|low",
    "what_could_go_wrong": "one sentence"
  },
  "moves": [ ... wire-format moves ... ],
  "memory_note": "one sentence, past tense, what I did"
}
"""

from __future__ import annotations

import math
from typing import Any, List, Mapping, Sequence

from sea_of_colours.orchestrator_2.harnesses.sagar_cursor._v7.rules import (
    RULES_SUMMARY,
)
from sea_of_colours.orchestrator_2.harnesses.sagar_cursor._v7.strategies import (
    STRATEGIES_CORE,
    DOCTRINE_BLUE,
    DOCTRINE_REDSIGN,
    DOCTRINE_LASTDAY_SUPERSEDE,
    DOCTRINE_BEWARE_EMP,
    DOCTRINE_BEWARE_CHAFF,
)
from sea_of_colours.orchestrator_2.harnesses.sagar_cursor._v7.probe_hints import (
    _redsign_centers,
    _enemy_probe_cells,
)
from sea_of_colours.orchestrator_2.harnesses.sagar_cursor._v7.validators import (
    _synthetic_green_cells,
    _green_hazard_cells,
)
from sea_of_colours.orchestrator_2.harnesses.sagar_cursor._v7.orbit_wishlist import (
    Wishlist,
)


_ACTION_SCHEMA = """\
ACTION SCHEMA — output ONE JSON object starting with the open-brace
character. NO prose before the JSON. Fields:

  reflection_on_last_night: null ONLY on night 1; otherwise REQUIRED — an
    object with:
    predicted: "high" | "medium" | "low"   (what you said last night)
    actual: integer   (the EXACT pts the engine banked — copy it from the
                       REFLECT ON LAST NIGHT block; do not estimate)
    gap_reason: "one sentence — if banked < predicted, name the cause
                 (chaff/EMP/crush/crash/thin seam) from the REFLECT block"

  plan_this_turn: string, one sentence.

  rationale: string, one line — WHY this plan beats alternatives.

  predicted_outcome:
    banked_pts_estimate: "high" | "medium" | "low"
      high   ≈ ≥ 1500 points banked this night
      medium ≈ 500–1500 points banked
      low    ≈ < 500 points OR high-uncertainty chain
    what_could_go_wrong: "one sentence naming the biggest risk"

  moves: list of wire-format actions. Each is one of:
    {"a":"drop",   "unit":"harvester_p1", "at":[x,y]}
    {"a":"step",   "unit":"harvester_p1", "to":[x,y]}
    {"a":"pickup", "unit":"harvester_p1"}
    {"a":"probe",  "at":[x,y]}
  Chain grammar: drop → step* → pickup PER harvester. Probes anywhere.

  step "to" MUST be Manhattan-1 from the harvester's CURRENT cell:
  exactly ONE of (x+1,y), (x-1,y), (x,y+1), (x,y-1). NO diagonals.
  NO multi-cell jumps. Every illegal step is silently canceled by the
  engine and cascades into a crashed harvester at dawn — cargo AND
  stock lost. To travel further, list each intermediate cell as its
  own step. Path from (19,12) to (16,10) = 5 steps, not 1.

  memory_note: string, one sentence past tense, what you did this turn.
"""


_THINKER_SCHEMA = """\
=== YOUR TASK (STRATEGIST) ===
You are the STRATEGIST for this night. Do NOT emit any moves. Your ONE job is to
DECIDE the night's POSTURE and the 1-3 cells the mover should prioritise, using
LAST NIGHT / REFLECT / OPPONENT INTEL / hints above.

Return ONE JSON object with these fields, IN THIS EXACT ORDER — the DECISION
comes FIRST, a short rationale LAST:
  {
    "posture": "aggressive|defensive|redsign_race|final_convert",
    "targets": [[x,y], ...],
    "chaff_react": true|false,
    "avoid": [[x,y], ...],
    "note": "short phrase for the mover",
    "reasoning": "1-3 SHORT sentences — why this call. NOT a chain-of-thought."
  }

DECIDE FIRST, then justify — do NOT open with a long chain-of-thought. Emit the
decision fields at the very start of the object; keep "reasoning" to a couple of
sentences at the END. (A rambling reasoning-first answer gets truncated before
the decision lands and is thrown away — a decisive short answer always wins.)

The decision fields:
  * posture: pick ONE.
      - defensive / chaff_react=true → shorten chains, pick up early (an
        opponent has chaff or chaffed us).
      - redsign_race → a public pure-RED beacon is worth racing/contesting.
      - final_convert → final night: convert everything, no frontier probes.
      - aggressive → default: harvest the richest reachable RED.
  * targets / avoid: 0-3 [x,y] cells each (anchors, not full paths).

Weigh these BEFORE you write (in your head — put only the conclusion in
"reasoning"): did last night's prediction miss and why (chaff? held-not-lost
hoard?); which RED / redsign / hot-drop is worth the most REACHABLE points; is
an opponent EMP/chaff-capable near a target; if contested, short direct comb /
commit more harvesters / stage behind a fresh probe / SUPERSEDE the enemy probe
on the beacon to deny it. Then commit. The mover acts on your decision — make
the call decisive.
"""


def format_state_block(
    agent_view: Mapping[str, Any],
    *,
    day: int,
    day_cap: int,
    vault_score: int,
) -> str:
    """Compact YOUR STATE block. Only fields phase 1 uses."""
    entities = (agent_view.get("entities") or {}).get("mine") or []
    harvesters = [
        {
            "id": e.get("id"),
            "state": "surface" if e.get("pos") else "orbit",
            "at": e.get("pos"),
        }
        for e in entities
        if isinstance(e, Mapping) and str(e.get("type") or "") == "harvester"
    ]
    probes = [
        {
            "id": e.get("id"),
            "at": e.get("pos"),
            "nights_remaining": e.get("nights_remaining"),
        }
        for e in entities
        if isinstance(e, Mapping) and str(e.get("type") or "") == "probe"
    ]

    # Held-but-unshipped RED. `vault_score` counts SHIPPED parcels only;
    # a harvest banks to the HOARD first and scores 0 until the next orbit
    # settles. v1.13 — that settlement is automatic and unconditional, so
    # held RED is banked score in waiting, not a pending decision.
    # Surfacing it stops the agent from reading an unchanged vault_score
    # after a good harvest as "pickup failed / cargo lost" (the day-4
    # S2024 reflection bug).
    hoard = (agent_view.get("hud") or {}).get("hoard") or {}
    held_count = int(hoard.get("count") or 0)
    held_red_value = int(hoard.get("red_value") or 0)
    hoard_line = (
        f"  hoard_red_value: ~{held_red_value} pts held in {held_count} "
        f"parcel(s) — HARVESTED but not yet settled, so it counts 0 toward "
        f"vault_score until the next orbit. Settlement is AUTOMATIC: this "
        f"WILL ship and score, guaranteed. If vault_score did not move "
        f"after a harvest but the hoard grew, the harvest SUCCEEDED and "
        f"the cargo is HELD (not lost).\n"
    )

    return (
        f"YOUR STATE (day {day} of {day_cap}):\n"
        f"  vault_score: {int(vault_score)}  (SHIPPED parcels only — this is your score)\n"
        f"{hoard_line}"
        f"  harvesters_alive: {harvesters}\n"
        f"  probes_alive: {probes}\n"
    )


def format_visible_red_block(agent_view: Mapping[str, Any]) -> str:
    """Compact list of RED cells you can currently see, with purity+tier."""
    world = agent_view.get("world") or {}
    rows: List[str] = []
    sg_rows: List[str] = []
    for row in (world.get("live") or []):
        if not isinstance(row, Mapping):
            continue
        tile = str(row.get("tile") or "")
        try:
            x, y = int(row["x"]), int(row["y"])
        except (TypeError, KeyError, ValueError):
            continue
        if tile == "RED":
            p = int(row.get("purity") or 0)
            tier = _tier(p)
            rows.append(f"    ({x},{y}) {tier} purity={p}")
        elif tile == "GREEN" and row.get("lineage") == "synthetic":
            sg_rows.append(f"({x},{y})")
    if not rows and not sg_rows and isinstance(world.get("grid"), list):
        for y, r in enumerate(world["grid"]):
            if not isinstance(r, list):
                continue
            for x, cell in enumerate(r):
                if not isinstance(cell, Mapping):
                    continue
                if str(cell.get("tile") or "") == "RED":
                    p = int(cell.get("purity") or 0)
                    rows.append(f"    ({x},{y}) {_tier(p)} purity={p}")
                elif str(cell.get("tile") or "") == "GREEN" and cell.get("synthetic"):
                    sg_rows.append(f"({x},{y})")
    body = "VISIBLE RED CELLS:\n"
    body += ("\n".join(rows) if rows else "    (none in current LOS)") + "\n"
    body += "SYNTHETIC-GREEN (do not step): " + (
        ", ".join(sg_rows) if sg_rows else "(none)"
    ) + "\n"
    return body


def _tier(purity: int) -> str:
    # Canonical engine bands — must match sea_of_colours/game/orbit_resolver.py::_tier_label
    # and sea_of_colours/agent/heuristic_agent.py:143. ONLY purity==255 is pure.
    p = int(purity or 0)
    if p >= 255:
        return "pure"
    if p >= 151:
        return "mass"
    if p >= 51:
        return "vein"
    return "trace"


def format_chain_hints_block(hints: Sequence[Mapping[str, Any]]) -> str:
    """Render the heuristic chain hints WITHOUT numeric scores."""
    if not hints:
        return "HEURISTIC SUGGESTIONS: (compiler produced no chains this turn)\n"
    lines = ["HEURISTIC SUGGESTIONS (starting points — pick, alter, or ignore):"]
    for i, h in enumerate(hints):
        unit = h.get("unit")
        cells = h.get("cells") or []
        tiers = h.get("tiers") or []
        purities = h.get("purities") or []
        cell_summary = ", ".join(
            f"({c[0]},{c[1]}) {tiers[j] if j < len(tiers) else '?'} "
            f"p={purities[j] if j < len(purities) else '?'}"
            for j, c in enumerate(cells)
        )
        lines.append(f"  Chain {chr(ord('A')+i)}: unit={unit} cells=[{cell_summary}]")
    lines.append("  (YOU compute EV using the tier table in RULES; scores are NOT provided.)")
    return "\n".join(lines) + "\n"


def format_setup_night_advisory(
    agent_view: Mapping[str, Any],
    day: int,
    day_cap: int,
) -> str:
    """Big fat advisory the prompt prepends when the agent has NO vision.

    Fires when the visible world is entirely fog — i.e. no visible RED
    cells AND no friendly probes on the surface. This is the "setup
    night" situation: the ONLY legal productive action is to launch
    probes. Drops fail because there is no live-vision cell to land on,
    steps do nothing without a harvester on the surface, and pickups
    are impossible without a harvester holding cargo. Say all of this
    to the agent so it doesn't burn hours trying illegal drops or
    passing the night.

    Returns an empty string when the agent DOES have vision (probes
    alive OR any red_tiles visible OR any friendly surface unit) so
    the advisory does not fire spuriously on days 2+.
    """
    red_visible = len(agent_view.get("red_tiles") or [])
    entities = (agent_view.get("entities") or {}).get("mine") or []
    friendly_probes = [
        e for e in entities
        if isinstance(e, Mapping) and str(e.get("type") or "") == "probe"
        and e.get("pos")  # on the surface
    ]
    friendly_surface_units = [
        e for e in entities
        if isinstance(e, Mapping)
        and str(e.get("type") or "") in ("harvester", "probe")
        and e.get("pos")
    ]
    if red_visible > 0 or friendly_probes or friendly_surface_units:
        return ""

    probe_stock = int((agent_view.get("orbit") or {}).get("probe_stock") or 0)
    world = agent_view.get("world") or {}
    fog_count = int(world.get("fog_count") or 0)

    lines = [
        "!!! SETUP NIGHT — YOU HAVE NO VISION YET !!!",
        "",
        f"  Every cell on the {world.get('width', '?')}x{world.get('height', '?')} "
        f"grid is fog. red_visible=0, no friendly probes are on the surface, "
        f"no harvester is on the surface. fog_count={fog_count}.",
        "",
        f"  You have probe_stock={probe_stock}. A probe reveals a Euclidean "
        f"radius-4 disk (~49 cells) for 3 nights.",
        "",
        "  A COLD drop (drop without a probe already down) will be REJECTED —"
        " the cell isn't in live vision. BUT a HOT DROP is legal on night 1:",
        "     hour 1: probe(at=[x,y])       → makes the 4-radius disk live",
        "     hour 2: drop(harvester at=[x',y']) where (x',y') is inside the disk",
        "     hour 3+: step chain + pickup",
        "  This is the setup-night harvest opportunity — do NOT skip it.",
        "",
        "  RECOMMENDED SETUP-NIGHT PLAN (attempt harvest, don't just probe):",
        "    * At LEAST 2 probes total. Setup night is the ONE night you"
        " have full probe magazine and no better use for hours — spread"
        " probes across DIFFERENT quadrants.",
        "    * ATTEMPT AT LEAST ONE HOT DROP. Best target: a bluesign"
        " hotspot (public intensity map, visible from day 1 in the BLUE"
        " HINTS block below — bright cells have real blue nearby). A"
        " bluesign hot drop is not blind — the intensity signal predicts"
        " where value is. Random-probe hot drops are also legal but blinder.",
        "    * Hot-drop sequence: probe(bluesign_cell) → drop(harvester"
        " into disk) → step 1-3 → pickup, all within the same night's 21"
        " hours.",
        "    * SUBMIT NON-EMPTY moves. Passing setup night wastes the season"
        " — nights 2, 3, 4 will have less to harvest.",
        "    * Cold drops (drop without a preceding probe covering the"
        " cell) WILL be rejected by the engine — always probe first if you"
        " want to drop somewhere.",
        "",
        "!!! End setup-night advisory !!!",
    ]
    return "\n".join(lines) + "\n"


def _euclidean_drop_rows(
    cx: int, cy: int, r: int, width: int, height: int,
) -> List[tuple]:
    """Row-by-row x-ranges of the Euclidean radius-``r`` disk around
    ``(cx, cy)``, clamped to the board.

    This MUST match the engine's live-vision disk
    (:func:`sea_of_colours.game.session._euclidean_disk`), which is what
    gates a legal drop (``tiles_visible_now`` membership). A cell is in
    the disk iff ``dx*dx + dy*dy <= r*r`` — i.e. for each row ``dy`` the
    legal x-span is ``[cx - floor(sqrt(r^2 - dy^2)), cx + ...]``. This is
    NARROWER than the Chebyshev 9x9 box: the four box corners are fog and
    a drop there is rejected.
    """
    rows: List[tuple] = []
    for dy in range(-r, r + 1):
        y = cy + dy
        if not (0 <= y < height):
            continue
        dx = int(math.isqrt(r * r - dy * dy))
        xmin = max(0, cx - dx)
        xmax = min(width - 1, cx + dx)
        if xmin > xmax:
            continue
        rows.append((y, xmin, xmax))
    return rows


def format_drop_legal_block(agent_view: Mapping[str, Any]) -> str:
    """Explicit list of where a drop is legal THIS turn.

    Engine rule: a drop cell must be (a) inside the **Euclidean radius-4
    disk** of an active friendly probe (the ~49-cell live-vision disk —
    NOT the 81-cell Chebyshev box; the four corners of the 9x9 bounding
    box are fog and the engine rejects a drop there), OR (b) on a
    friendly harvester's tile or one of its four Manhattan-1 neighbours
    (the "plus"). Echo-only cells are NOT legal.

    Reasoning about probe expiry + disk math is exactly the kind of
    thing the model gets wrong under time pressure (a night-1 hot-drop at
    (36,20) off a probe at (33,17) was rejected because (36,20) is a box
    corner outside the Euclidean disk, crashing the whole chain).
    Rendering the exact per-row legal x-ranges means the LLM only has to
    CHECK, not COMPUTE.
    """
    entities = (agent_view.get("entities") or {}).get("mine") or []
    world = agent_view.get("world") or {}
    width = int(world.get("width") or 40)
    height = int(world.get("height") or 28)
    lines: List[str] = []

    # Hazards to steer drops away from, and enemy vision to warn about.
    bad_cells = _synthetic_green_cells(agent_view) | _green_hazard_cells(agent_view)
    enemy_probes = [
        r["at"] for r in _enemy_probe_cells(agent_view)
        if isinstance(r.get("at"), tuple)
    ]

    def _under_enemy_vision(x: int, y: int) -> "tuple | None":
        for ex, ey in enemy_probes:
            if (x - ex) * (x - ex) + (y - ey) * (y - ey) <= 16:
                return (ex, ey)
        return None

    # Active probes.
    probe_lines: List[str] = []
    for e in entities:
        if not isinstance(e, Mapping):
            continue
        if str(e.get("type") or "") != "probe":
            continue
        nr = e.get("nights_remaining")
        if isinstance(nr, (int, float)) and int(nr) <= 0:
            continue
        pos = e.get("pos") or e.get("at")
        if not (isinstance(pos, (list, tuple)) and len(pos) == 2):
            continue
        try:
            cx, cy = int(pos[0]), int(pos[1])
        except (TypeError, ValueError):
            continue
        pid = str(e.get("id") or "probe")
        rows = _euclidean_drop_rows(cx, cy, 4, width, height)
        range_str = ", ".join(
            f"y={y}:[{xmin},{xmax}]" for (y, xmin, xmax) in rows
        )
        nr_txt = f" [{int(nr)}n]" if isinstance(nr, (int, float)) else ""
        # Terse per-probe line — the disk/crush rules live in the header.
        probe_lines.append(f"  {pid}@({cx},{cy}){nr_txt}: {range_str}")
        # Only surface annotations that actually apply (keeps the block short).
        disk_cells = [
            (x, y) for (y, xmin, xmax) in rows for x in range(xmin, xmax + 1)
        ]
        avoid_green = sorted(c for c in disk_cells if c in bad_cells)
        if avoid_green:
            probe_lines.append(f"      ! AVOID (green -100): {avoid_green}")
        watched = sorted(
            {c for c in disk_cells if _under_enemy_vision(c[0], c[1])}
        )
        if watched:
            probe_lines.append(f"      ! WATCHED by enemy probe: {watched}")

    # Surface harvesters — the plus (self + 4 Manhattan-1 neighbours) is
    # also drop-legal (rule of adjacency to a friendly unit).
    harv_lines: List[str] = []
    for e in entities:
        if not isinstance(e, Mapping):
            continue
        if str(e.get("type") or "") != "harvester":
            continue
        pos = e.get("pos") or e.get("at")
        if not (isinstance(pos, (list, tuple)) and len(pos) == 2):
            continue
        try:
            hx, hy = int(pos[0]), int(pos[1])
        except (TypeError, ValueError):
            continue
        hid = str(e.get("id") or "harvester")
        plus = [(hx, hy), (hx+1, hy), (hx-1, hy), (hx, hy+1), (hx, hy-1)]
        harv_lines.append(
            f"  {hid} plus @ {plus}"
        )

    if not probe_lines and not harv_lines:
        return (
            "DROP-LEGAL ZONES: NONE right now. No active probe and no "
            "harvester on the surface — but you CAN hot-drop: launch a "
            "probe at (cx,cy) at hour K, then at hour K+1 drop inside its "
            "Euclidean radius-4 disk (dx*dx+dy*dy<=16, ~49 cells; the 9x9 "
            "box corners are fog and get rejected). Land ADJACENT to the "
            "centre (an in-disk neighbour) — do NOT drop on (cx,cy) itself "
            "or you CRUSH the probe you just paid for. Sequence in moves[] "
            "matters (probe FIRST). Probes are always legal.\n"
        )

    # Rules stated ONCE here (not repeated per probe) to keep the block
    # short. Per-probe lines are just: id@(cx,cy)[Nn]: legal x-ranges.
    lines.append(
        "DROP-LEGAL ZONES — every drop.at MUST be inside AT LEAST ONE zone "
        "below or the engine rejects it. Each probe's legal cells are its "
        "Euclidean r4 disk (dx^2+dy^2<=16, ~49 cells; 9x9 box CORNERS are "
        "fog=rejected). Format: id@(cx,cy)[Nn left]: y=<row>:[xmin,xmax]. "
        "Dropping OR stepping on a probe's own (cx,cy) CRUSHES it (lose its "
        "remaining vision) — land ADJACENT unless the loot IS on (cx,cy). "
        "'! AVOID' = green/harvested (-100); '! WATCHED' = an enemy probe "
        "sees it (collision/EMP risk)."
    )
    if probe_lines:
        lines.append("  active probe disks:")
        lines.extend(probe_lines)
    if harv_lines:
        lines.append("  surface harvester plus-cells (drop on unit or its 4 neighbours):")
        lines.extend(harv_lines)
    lines.append(
        "  HOT DROP: a NEW probe you launch at hour K is a drop-legal disk "
        "from hour K+1 — probe FIRST in moves[], then drop ADJACENT to its "
        "centre."
    )
    return "\n".join(lines) + "\n"


def format_fog_and_echo_block(agent_view: Mapping[str, Any]) -> str:
    """Expose the fog map + echo hints so the LLM can reason about what
    it CAN'T see yet — the prerequisite for making probe decisions.

    Shows fog_count, up to 2 largest fog_clusters (centroid + nearest
    visible edge + size), and up to 5 best_red_echo cells if any.
    """
    world = agent_view.get("world") or {}
    fog_count = int(world.get("fog_count") or 0)
    probe_stock = int((agent_view.get("orbit") or {}).get("probe_stock") or 0)

    clusters = [c for c in (agent_view.get("fog_clusters") or []) if isinstance(c, Mapping)]
    clusters_sorted = sorted(clusters, key=lambda c: int(c.get("size") or 0), reverse=True)[:2]

    echo = [
        row for row in (((agent_view.get("navigation") or {}).get("best_red_echo")) or [])
        if isinstance(row, Mapping)
    ][:5]

    lines = ["FOG + ECHO (what you CAN'T see):"]
    lines.append(f"  fog_count: {fog_count}  probe_stock: {probe_stock}")
    if clusters_sorted:
        lines.append("  top_fog_clusters:")
        for c in clusters_sorted:
            centroid = c.get("centroid") or [None, None]
            nve = c.get("nearest_visible_edge") or [None, None]
            size = c.get("size")
            lines.append(
                f"    centroid=({centroid[0]},{centroid[1]}) size={size} "
                f"nearest_visible_edge=({nve[0]},{nve[1]})"
            )
    else:
        lines.append("  top_fog_clusters: (none — you see everything)")
    if echo:
        lines.append("  best_red_echo (traces beyond LOS — a probe here reveals real cells):")
        for row in echo:
            lines.append(
                f"    ({row.get('x')},{row.get('y')}) purity_est={row.get('purity') or row.get('value')}"
            )
    else:
        lines.append("  best_red_echo: (no echoes surfaced this turn)")
    return "\n".join(lines) + "\n"


def format_probe_hints_block(hints: Sequence[Mapping[str, Any]]) -> str:
    """Render probe-placement candidates the LLM can consider.

    Two ints per candidate: ``area_gain`` (new fog cells the disk would
    reveal) and ``edge_promise`` (purity-weighted seam-extension signal
    plus any echo cells inside the disk). NO combined score — the LLM
    picks its own tradeoff between raw territory gain and seam extension.
    """
    if not hints:
        return "PROBE PLACEMENT HINTS: (no fog to un-cover — probes not useful this turn)\n"
    lines = ["PROBE PLACEMENT HINTS (candidates — you may alter, reorder, or ignore):"]
    for i, h in enumerate(hints):
        at = h.get("at") or [None, None]
        lines.append(
            f"  Probe {chr(ord('A')+i)}: at=({at[0]},{at[1]}) "
            f"area_gain={h.get('area_gain')} "
            f"edge_promise={h.get('edge_promise')} "
            f"seed={h.get('extends_from')}"
        )
    lines.append(
        "  (area_gain = new cells revealed. edge_promise = purity-weighted "
        "seam extension + echo inside disk. YOU trade them off.)"
    )
    return "\n".join(lines) + "\n"


def format_hot_drop_hints_block(hints: Sequence[Mapping[str, Any]]) -> str:
    """Render probe-then-drop pairings that can execute in ONE night.

    Empty when the compiler found no bluesign / redsign signals + probe
    stock + harvester. Rendered as its own block after PROBE PLACEMENT
    HINTS so the LLM sees it as a distinct option, not a sub-case.

    Signal-type-first ordering: redsign hints (pure-red hunt, essential)
    render before bluesign hints (blue fallback, wishlist-gated).
    """
    if not hints:
        return ""
    lines = ["HOT DROP HINTS (probe hour K, then drop harvester at hour K+1 onto target):"]
    for i, h in enumerate(hints):
        pat = h.get("probe_at") or [None, None]
        dat = h.get("drop_at") or [None, None]
        sig = h.get("signal_type", "?")
        intensity = h.get("signal_intensity", 0)
        signal_label = (
            f"REDSIGN broadcast (pure(255) here — race the opponent)"
            if sig == "redsign"
            else f"bluesign intensity={intensity:.2f} (dense blue somewhere in this cluster)"
        )
        fog_note = (
            " [beacon is in FOG — the hot drop is the ONLY way to reach it "
            "this night]"
            if h.get("target_in_fog")
            else " [beacon already VISIBLE — you may skip the probe and drop "
            "directly if a live disk already covers it]"
        )
        lines.append(
            f"  Combo {chr(ord('A')+i)}: {signal_label}{fog_note}"
        )
        lines.append(
            f"    probe at=({pat[0]},{pat[1]}) hour=1, then "
            f"drop {h.get('unit')} at=({dat[0]},{dat[1]}) hour=2  "
            f"(area_gain={h.get('area_gain')})"
        )
        comb = h.get("comb_path") or []
        if comb:
            comb_str = " -> ".join(f"({c[0]},{c[1]})" for c in comb)
            lines.append(
                f"    READY COMB (copy these steps after the drop, then "
                f"pickup): {comb_str}"
            )
        note = str(h.get("note") or "")
        if note:
            lines.append(f"    note: {note}")
    lines.append(
        "  (Probe move MUST come BEFORE the drop in moves[]. The drop_at "
        "above is deliberately NOT the probe centre — landing on the "
        "centre would crush the probe you just paid for. The READY COMB is "
        "a blind serpentine over the fresh disk (a hot drop is blind — you "
        "commit all steps up front): copy it verbatim as your step chain, "
        "then pickup. Do NOT shorten it to 1-3 steps — a hot drop that "
        "walks only 2 cells wastes both the probe and the harvester's "
        "night. Shorten ONLY if a BEWARE_EMP/CHAFF block is live.)"
    )
    return "\n".join(lines) + "\n"


def format_supersede_hints_block(hints: Sequence[Mapping[str, Any]]) -> str:
    """FINAL-NIGHT ONLY — enemy probe cells worth superseding with spare
    probe stock. Caller passes [] on non-final nights."""
    if not hints:
        return ""
    lines = [
        "SUPERSEDE HINTS (FINAL NIGHT — after your harvest chains are "
        "placed, spend any LEFTOVER probe stock landing ON these enemy "
        "probe cells to destroy their vision; your probe survives):"
    ]
    for i, h in enumerate(hints):
        at = h.get("probe_at") or [None, None]
        lines.append(
            f"  Target {chr(ord('A')+i)}: probe at=({at[0]},{at[1]}) "
            f"— supersedes an enemy probe (seen day {h.get('day_seen', '?')})"
        )
    return "\n".join(lines) + "\n"


def format_blue_hints_block(hints: Sequence[Mapping[str, Any]]) -> str:
    """Render blue-harvest chain hints. Caller decides when to include."""
    if not hints:
        return ""
    lines = ["BLUE HARVEST HINTS (secondary priority — funds orbit weapons/repairs):"]
    for i, h in enumerate(hints):
        cells = h.get("cells") or []
        purities = h.get("purities") or []
        cell_summary = ", ".join(
            f"({c[0]},{c[1]}) blue p={purities[j] if j < len(purities) else '?'}"
            for j, c in enumerate(cells)
        )
        lines.append(f"  Blue Chain {chr(ord('A')+i)}: unit={h.get('unit')} cells=[{cell_summary}]")
    return "\n".join(lines) + "\n"


def format_opponent_block(agent_view: Mapping[str, Any]) -> str:
    """Show what the opponent did / is doing. Empty on solo scenarios."""
    ci = agent_view.get("competitor_intel") or {}
    new_events = list(ci.get("new_this_day") or [])
    persistent = list(ci.get("persistent_echoes") or [])
    opponents = ((agent_view.get("station_intel") or {}).get("opponents") or {})

    if not new_events and not persistent and not opponents:
        return ""

    lines = ["OPPONENT INTEL (what they revealed to you — you don't see their queue):"]
    if new_events:
        lines.append(f"  new_this_day ({len(new_events)} events):")
        for ev in new_events[:5]:
            if not isinstance(ev, Mapping):
                continue
            kind = ev.get("kind") or ev.get("type") or "event"
            at = ev.get("at") or ev.get("pos") or ""
            hour = ev.get("hour")
            hour_s = f" hour={hour}" if hour is not None else ""
            lines.append(f"    - {kind}{hour_s} at={at}")
    if persistent:
        lines.append(f"  persistent_echoes ({len(persistent)}): still hearing prior activity")
    if isinstance(opponents, Mapping) and opponents:
        # station_intel.opponents is a dict keyed by seat id.
        for seat, info in list(opponents.items())[:2]:
            if not isinstance(info, Mapping):
                continue
            blue = (info.get("blue") or {}).get("total", "?")
            green = (info.get("green") or {}).get("total", "?")
            act = info.get("activity") or {}
            probes = act.get("probes", "?")
            dropped = act.get("dropped", "?")
            lines.append(
                f"  {seat}: blue_banked={blue} green_banked={green} "
                f"probes_launched={probes} harvesters_dropped={dropped}"
            )
    return "\n".join(lines) + "\n"


def format_last_night_block(agent_view: Mapping[str, Any]) -> str:
    """Show what actually happened last night (from the engine's perspective).

    Renders the engine's ground-truth record so the agent can reflect on
    plan vs actual. The engine's ``last_night`` block includes:
      * ``my_orders``: each order you submitted with outcome ("ok" or
        "illegal") and a reason if illegal — this is the single source
        of truth for "did my move even land?"
      * ``my_parcels_banked``: which cells actually banked, with tile
        and purity — this tells you what harvest ACTUALLY yielded.
      * ``my_assets_destroyed``: probes / harvesters killed by opponent
        weapons, with reason (e.g. ``emp_p2``). Read this to decide
        whether to trigger the RECOVERY RE-PROBE exception.
      * ``combat_events``: opponent actions that affected you.
    """
    ln = agent_view.get("last_night") or {}
    if int(ln.get("day_ended") or 0) == 0:
        return ""

    orders = ln.get("my_orders") or []
    destroyed = ln.get("my_assets_destroyed") or []
    banked = ln.get("my_parcels_banked") or []
    combat = ln.get("combat_events") or []

    if not (orders or destroyed or banked or combat):
        return ""

    lines = [f"LAST NIGHT (day {ln.get('day_ended')} — engine record):"]

    # my_orders: per-order truth. The engine tags each with outcome.
    if orders:
        ok = sum(1 for o in orders if isinstance(o, Mapping) and o.get("outcome") == "ok")
        illegal = sum(1 for o in orders if isinstance(o, Mapping) and o.get("outcome") == "illegal")
        lines.append(f"  my_orders ({len(orders)} submitted, {ok} ok, {illegal} illegal):")
        for o in orders[:12]:  # cap to keep prompt bounded
            if not isinstance(o, Mapping):
                continue
            outcome = o.get("outcome", "?")
            text = str(o.get("text") or "").strip()
            # engine's text is free-form ("p1 deployed probe at (7,4)"),
            # keep it short so 12 lines fit comfortably in the prompt.
            if len(text) > 90:
                text = text[:87] + "..."
            line = f"    - {text} → {outcome}"
            if outcome == "illegal" and o.get("reason"):
                rsn = str(o.get("reason") or "").strip()
                if rsn != text and len(rsn) < 100:
                    line += f" (reason: {rsn})"
            lines.append(line)
        if len(orders) > 12:
            lines.append(f"    ... ({len(orders) - 12} more orders truncated)")

    # my_parcels_banked: per-parcel detail so the agent knows exactly
    # which cells produced value. Aggregated by tier for context.
    if banked:
        by_tier: Dict[str, int] = {}
        for b in banked:
            if isinstance(b, Mapping):
                t = str(b.get("tier") or b.get("tile") or "?")
                by_tier[t] = by_tier.get(t, 0) + 1
        lines.append(f"  my_parcels_banked ({len(banked)}) by tier: {by_tier}")
        for b in banked[:8]:
            if not isinstance(b, Mapping):
                continue
            frm = b.get("from") or [None, None]
            lines.append(
                f"    - ({frm[0]},{frm[1]}) {b.get('tile','?')} "
                f"purity={b.get('purity','?')} tier={b.get('tier','?')} "
                f"by={b.get('harvester_id','?')}"
            )
        if len(banked) > 8:
            lines.append(f"    ... ({len(banked) - 8} more parcels)")

    # my_assets_destroyed: opponent-caused losses. Critical for the
    # RECOVERY RE-PROBE exception — if a probe died before you got
    # value from its disk, re-probing that area IS allowed.
    if destroyed:
        lines.append(f"  my_assets_destroyed ({len(destroyed)}):")
        for d in destroyed[:8]:
            if isinstance(d, Mapping):
                at = d.get("at") or []
                at_str = f" at ({at[0]},{at[1]})" if at and at[0] is not None else ""
                lines.append(
                    f"    - {d.get('id') or d.get('kind') or '?'}"
                    f"{at_str} reason={d.get('reason', '?')}"
                )

    if combat:
        lines.append(f"  combat_events ({len(combat)}):")
        for ev in combat[:8]:
            lines.append("    - " + _format_combat_event(ev))

    return "\n".join(lines) + "\n"


def _format_combat_event(ev: Mapping[str, Any]) -> str:
    """Human-readable one-liner for a single last-night combat event.

    Renders the event types that actually reach the agent view
    (:func:`snowpark.view._last_night_recap`): the VICTIM-private
    ``chaff_jam`` / ``emp_hit`` (these mean YOU were hit) and the PUBLIC
    ``chaff`` flare / ``emp`` salvo. Anything else is shown as-is.
    """
    if not isinstance(ev, Mapping):
        return str(ev)
    etype = str(ev.get("type") or "?")
    hours = ev.get("hours") or []
    hrs = f" at hours {list(hours)}" if hours else ""
    if etype == "chaff_jam":
        by = ev.get("by") or []
        units = ev.get("units") or []
        who = f" by {list(by)}" if by else ""
        unit_txt = f" (jammed: {list(units)})" if units else ""
        return (
            f"YOU WERE CHAFFED{who}{hrs}{unit_txt} — those action-slots were "
            f"CANCELLED (a pickup in that window is lost -> dawn-crash risk)."
        )
    if etype == "emp_hit":
        by = ev.get("by") or []
        at = ev.get("at") or []
        at_txt = f" near ({at[0]},{at[1]})" if at and at[0] is not None else ""
        who = f" by {list(by)}" if by else ""
        return (
            f"YOU WERE EMP'd{who}{at_txt}{hrs} — a unit was disabled ~8h "
            f"(it keeps its haul; only a dawn crash kills it)."
        )
    if etype == "chaff":
        return f"chaff flare by {ev.get('owner','?')}{hrs} (public)."
    if etype == "emp":
        return f"EMP salvo{hrs} (public)."
    return f"{etype} {dict(ev)}"


def _my_jam_events(agent_view: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    """Last-night events that actually HIT me (chaff_jam / emp_hit).

    combat_events is already victim-filtered by the view builder, so the
    presence of a chaff_jam / emp_hit here means I was the victim. Used to
    force the relevant BEWARE doctrine + a concrete pickup-window warning
    even when the inference tracker is cold.
    """
    out: List[Mapping[str, Any]] = []
    ln = agent_view.get("last_night") or {}
    for ev in (ln.get("combat_events") or []):
        if isinstance(ev, Mapping) and str(ev.get("type") or "") in ("chaff_jam", "emp_hit"):
            out.append(ev)
    return out


def format_reflect_block(
    agent_view: Mapping[str, Any],
    prior_day_entry: "Mapping[str, Any] | None",
    day: int,
) -> str:
    """Force a GROUNDED reflection by handing the agent the engine's real
    prior-night numbers instead of letting it narrate its own plan.

    The prior audit found ``reflection_on_last_night`` was frequently null
    or hallucinated (it "banked ~medium" when the engine banked 0, never
    acknowledged a chaff loss, etc.). The fix is mechanical: surface the
    exact prediction-vs-actual delta and every named loss (chaff/EMP/crush/
    destroyed asset) so the LLM has nothing to guess — it just echoes the
    number and explains the gap. Empty on night 1 (no prior night).
    """
    if int(day) <= 1 or not prior_day_entry:
        return ""
    prior_day = int(prior_day_entry.get("day") or (int(day) - 1))
    predicted = str(
        (prior_day_entry.get("predicted_outcome") or {}).get(
            "banked_pts_estimate"
        )
        or "?"
    )
    actual = prior_day_entry.get("actual_banked")

    # Collect every concrete loss the engine attributes to last night.
    losses: List[str] = []
    for ev in _my_jam_events(agent_view):
        losses.append(_format_combat_event(ev))
    for c in (prior_day_entry.get("probe_crushes") or []):
        losses.append(str(c))
    ln = agent_view.get("last_night") or {}
    for d in (ln.get("my_assets_destroyed") or []):
        if isinstance(d, Mapping):
            at = d.get("at") or []
            at_s = f" at ({at[0]},{at[1]})" if at and at[0] is not None else ""
            losses.append(
                f"{d.get('id') or d.get('kind') or 'asset'}{at_s} destroyed "
                f"(reason={d.get('reason', '?')})"
            )

    # Parcels the engine actually harvested into the HOARD last night, with
    # a tier-weighted RED value estimate. This is the "did my harvest land?"
    # signal — separate from SHIPPED-score change, because a fresh haul sits
    # in the hoard (scoring 0) until the next orbit settles it. Surfacing
    # it stops the agent from reading a still vault_score as "pickup failed".
    _TIER_MULT = {"trace": 0.75, "vein": 1.0, "mass": 1.5, "pure": 3.0}
    banked = ln.get("my_parcels_banked") or []
    red_banked = [b for b in banked if isinstance(b, Mapping)
                  and str(b.get("tile") or "").upper() == "RED"]
    hoard_red_value = 0
    for b in red_banked:
        try:
            pur = int(b.get("purity") or 0)
        except (TypeError, ValueError):
            pur = 0
        hoard_red_value += int(round(pur * _TIER_MULT.get(
            str(b.get("tier") or "").lower(), 1.0)))

    lines = [f"REFLECT ON LAST NIGHT (day {prior_day}) — engine ground truth:"]
    if actual is not None:
        lines.append(
            f"  you predicted \"{predicted}\"; SHIPPED-score change last "
            f"night = {int(actual)} pts (this is the only thing that scores)."
        )
    else:
        lines.append(
            f"  you predicted \"{predicted}\"; shipped-score change not yet "
            f"resolved."
        )
    if banked:
        lines.append(
            f"  you HARVESTED {len(banked)} parcel(s) into the hoard "
            f"(~{hoard_red_value} pts of RED). These are HELD, not scored — "
            f"they count at the next orbit, which settles AUTOMATICALLY. A 0 "
            f"shipped-change with a healthy harvest is a SUCCESS awaiting "
            f"settlement, NOT a lost pickup — do NOT invent an EMP/chaff loss."
        )
    if losses:
        lines.append("  losses the engine recorded (you MUST acknowledge these):")
        for lz in losses[:5]:
            lines.append(f"    - {lz}")
    lines.append(
        "  -> Fill reflection_on_last_night: set actual to the EXACT number "
        "above — the harvested hoard value if you harvested (even if it has "
        "NOT shipped yet), else the shipped-score change. gap_reason must "
        "name any loss; if the harvest banked but vault_score did not move, "
        "say 'held in hoard, not yet shipped' — do NOT report it as lost "
        "cargo. Do NOT leave it null."
    )
    return "\n".join(lines) + "\n"


def format_wishlist_block(wishlist: Wishlist) -> str:
    """Render the deterministic orbit → tactical hand-off wishlist."""
    if not wishlist.entries:
        return ""
    return "\n".join(wishlist.as_prompt_lines()) + "\n"


def format_opponent_weapons_block(
    estimates: "Mapping[str, Any] | None",
) -> str:
    """Render the per-opponent weapon-stock uncertainty ranges.

    Only shows opponents where EMP or CHAFF ``max > 0``.

    v1.31 — this used to filter out a third, mine row that the estimator
    tracked but the doctrine never acted on. The estimator no longer
    tracks it, so the filter here is just "is there anything to warn
    about", not a curation of what to hide.

    Each estimate carries an ``inferences`` audit trail (last few
    entries) so the LLM can see WHY we think they're armed.
    """
    if not estimates:
        return ""
    # Only render seats where we have an actionable weapon (EMP or chaff).
    interesting = [
        est for est in estimates.values()
        if getattr(est, "emps_max", 0) > 0 or getattr(est, "chaff_max", 0) > 0
    ]
    if not interesting:
        return ""
    lines = [
        "OPPONENT WEAPON ESTIMATES (inferred from station_intel + activity — [min..max] ranges):",
    ]
    for est in interesting:
        lines.append(
            f"  {est.seat}: "
            f"emp=[{est.emps_min}..{est.emps_max}] "
            f"chaff=[{est.chaff_min}..{est.chaff_max}]"
        )
        audit = list(getattr(est, "inferences", []) or [])
        for a in audit[-2:]:
            lines.append(f"    · {a}")
    return "\n".join(lines) + "\n"


def build_prompt(
    *,
    agent_view: Mapping[str, Any],
    day: int,
    day_cap: int,
    vault_score: int,
    memory_replay: str,
    chain_hints: Sequence[Mapping[str, Any]],
    probe_hints: Sequence[Mapping[str, Any]] = (),
    hot_drop_hints: Sequence[Mapping[str, Any]] = (),
    blue_hints: Sequence[Mapping[str, Any]] = (),
    supersede_hints: Sequence[Mapping[str, Any]] = (),
    wishlist: "Wishlist | None" = None,
    opponent_weapon_estimates: "Mapping[str, Any] | None" = None,
    prior_day_entry: "Mapping[str, Any] | None" = None,
    mode: str = "mover",
    strategist_directive_block: str = "",
) -> str:
    """Assemble the full Tabula v7 prompt.

    ``mode`` selects the closing contract (the board-fact blocks are shared):
      * ``"mover"``   — the default v6 contract: emit the moves-first JSON
        object. When ``strategist_directive_block`` is non-empty it is injected
        high in the prompt as top-priority guidance (the v7 two-call split).
      * ``"thinker"`` — the reasoning pass: reason on the page, then FINISH
        with a single ``DECISION:`` line. No moves are emitted.

    Order of blocks (top to bottom):
      1. SETUP NIGHT advisory (only when the agent has zero vision)
      2. RULES (engine mechanics — physics)
      3. STRATEGIES — lean CORE always shown, plus STATE-TRIGGERED
         appendices appended only when the situation calls for them:
           * REDSIGN doctrine  — a pure-RED beacon is broadcast.
           * BLUE doctrine     — setup night OR wishlist raised grab_blue.
           * BEWARE_EMP / BEWARE_CHAFF — the weapon tracker flags stock.
      4. YOUR STATE
      5. VISIBLE RED
      6. DROP-LEGAL ZONES (explicit list of where drops are legal)
      7. FOG + ECHO
      8. LAST NIGHT (what the engine recorded)
      9. OPPONENT INTEL (what they revealed to you)
     10. TACTICAL PRIORITY FROM ORBIT (wishlist)
     11. YOUR MEMORY (agent-authored narrative from prior nights)
     12. HEURISTIC RED chain hints
     13. PROBE placement hints
     14. HOT DROP hints (probe+drop pairings)
     15. BLUE chain hints (only when wishlist asks for it)
     16. ACTION SCHEMA + prompt-to-emit
    """
    setup_advisory = format_setup_night_advisory(agent_view, day, day_cap)
    wl = wishlist if wishlist is not None else Wishlist()

    # ── STATE-TRIGGERED doctrine assembly ──────────────────────────────
    # The always-on CORE stays lean; each situational appendix is added
    # ONLY when the live state makes it actionable, so haiku never burns
    # attention on doctrine it can't use tonight.
    strategies_text = STRATEGIES_CORE

    # REDSIGN — a pure-RED beacon is public. Fire when the view carries a
    # redsign broadcast OR the compiler surfaced a redsign hot-drop combo.
    redsign_present = bool(agent_view.get("redsign")) or any(
        isinstance(h, Mapping) and h.get("signal_type") == "redsign"
        for h in (hot_drop_hints or ())
    )
    if redsign_present:
        centers = _redsign_centers(agent_view)
        if centers:
            coord_str = ", ".join(f"(~{cx},~{cy})" for cx, cy in centers[:4])
            concrete = (
                f"REDSIGN LIVE near {coord_str} — a pure(255) RED seam is "
                f"PUBLIC (every seat sees it). Check VISIBLE RED for any "
                f"unfogged pure/mass cells near there FIRST (chain them "
                f"directly if drop-legal); otherwise use the redsign HOT "
                f"DROP hints to race it. Then apply:\n"
            )
            strategies_text += "\n\n" + concrete + DOCTRINE_REDSIGN
        else:
            strategies_text += "\n\n" + DOCTRINE_REDSIGN

    # BLUE — the agent never decides its own blue-need. Fire on setup
    # night (zero vision → the advisory is non-empty) OR when the orbit
    # wishlist raised a ``grab_blue`` priority.
    is_setup_night = bool(setup_advisory.strip())
    want_blue = any(
        getattr(e, "tag", "") == "grab_blue" for e in (wl.entries or [])
    )
    if is_setup_night or want_blue:
        strategies_text += "\n\n" + DOCTRINE_BLUE

    # BEWARE_EMP / BEWARE_CHAFF — gated on the opponent-weapon tracker OR
    # on a jam that ACTUALLY hit us last night. The tracker infers likely
    # stock; but if we were demonstrably chaffed/EMP'd we must react even
    # when the tracker is cold (it previously never fired on a real jam).
    jam_events = _my_jam_events(agent_view)
    was_chaffed = any(str(e.get("type")) == "chaff_jam" for e in jam_events)
    was_empd = any(str(e.get("type")) == "emp_hit" for e in jam_events)
    if jam_events:
        # Concrete, unmissable warning with the exact hours to avoid.
        jam_lines = "; ".join(
            _format_combat_event(e) for e in jam_events[:3]
        )
        strategies_text += (
            "\n\nTHREAT LAST NIGHT (react NOW): " + jam_lines +
            " Do NOT schedule a pickup in the jammed hours again — the "
            "opponent blind-fires the same predictable window. Pick up "
            "EARLY (hour <=4) or shift the window."
        )
    opp_has_emp = opp_has_chaff = False
    if opponent_weapon_estimates:
        opp_has_emp = any(
            getattr(e, "emps_max", 0) > 0
            for e in opponent_weapon_estimates.values()
        )
        opp_has_chaff = any(
            getattr(e, "chaff_max", 0) > 0
            for e in opponent_weapon_estimates.values()
        )
    if opp_has_emp or was_empd:
        strategies_text += "\n\n" + DOCTRINE_BEWARE_EMP
    if opp_has_chaff or was_chaffed:
        strategies_text += "\n\n" + DOCTRINE_BEWARE_CHAFF

    # FINAL NIGHT — supersede enemy probes. Only when it's the last day
    # AND we actually have enemy-probe targets + spare stock to act on.
    is_last_day = int(day) >= int(day_cap)
    if is_last_day and supersede_hints:
        strategies_text += "\n\n" + DOCTRINE_LASTDAY_SUPERSEDE

    # Two-call split: the strategist directive (from call 1) rides high in
    # the mover prompt, right after doctrine, so it frames everything below.
    directive_part = (
        ("\n" + strategist_directive_block + "\n")
        if (mode == "mover" and strategist_directive_block)
        else ""
    )

    parts: List[str] = [
        setup_advisory,
        RULES_SUMMARY,
        "\n",
        strategies_text,
        "\n",
        directive_part,
        format_state_block(agent_view, day=day, day_cap=day_cap, vault_score=vault_score),
        "\n",
        format_visible_red_block(agent_view),
        "\n",
        format_drop_legal_block(agent_view),
        "\n",
        format_fog_and_echo_block(agent_view),
        "\n",
        format_last_night_block(agent_view),
        "\n" if format_last_night_block(agent_view) else "",
        format_reflect_block(agent_view, prior_day_entry, day),
        "\n" if format_reflect_block(agent_view, prior_day_entry, day) else "",
        format_opponent_block(agent_view),
        "\n" if format_opponent_block(agent_view) else "",
        format_opponent_weapons_block(opponent_weapon_estimates),
        "\n" if format_opponent_weapons_block(opponent_weapon_estimates) else "",
        format_wishlist_block(wl),
        "\n" if format_wishlist_block(wl) else "",
        f"YOUR MEMORY (agent-authored, oldest first):\n{memory_replay}\n\n",
        format_chain_hints_block(chain_hints),
        "\n",
        format_probe_hints_block(probe_hints),
        "\n",
        format_hot_drop_hints_block(hot_drop_hints),
        "\n" if format_hot_drop_hints_block(hot_drop_hints) else "",
        format_blue_hints_block(blue_hints),
        "\n" if format_blue_hints_block(blue_hints) else "",
        format_supersede_hints_block(supersede_hints if is_last_day else ()),
        "\n" if (is_last_day and supersede_hints) else "",
    ]

    if mode == "thinker":
        parts.append(_THINKER_SCHEMA)
        parts.append(
            "\nOUTPUT ONE JSON OBJECT NOW — DECISION FIRST (\"posture\" is the "
            "first key), a SHORT \"reasoning\" LAST. Start with the open-brace "
            "character. GO:\n"
        )
    else:
        parts.append(_ACTION_SCHEMA)
        parts.append(
            "\nOUTPUT THE JSON OBJECT NOW. Start with the open-brace "
            "character. GO:\n"
        )
    return "".join(parts)
