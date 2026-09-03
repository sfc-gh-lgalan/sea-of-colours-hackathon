"""Probe placement hint compiler for Tabula v7.

Answers: given the current fog of war, where should we drop a probe?

Design goal
-----------
Probes are cheap information. A probe launched onto ``(x, y)`` reveals a
Euclidean radius-4 vision disk (~49 cells) for the next 3 nights; that
same disk is where a harvester may drop (drop-legal == live-vision). For
probe PLACEMENT ranking, ``area_gain`` is estimated over the Chebyshev-4
box (9x9, 81 cells) as a cheap proxy — it slightly over-counts the true
Euclidean reveal, but it is only used to ORDER candidates, never shown as
a promise. HOT-DROP hints, by contrast, use the exact Euclidean disk
(:func:`_vision_disk` / :func:`_covers`) so a suggested drop_at is always
truly drop-legal. Two scoring signals tell us whether a candidate is
worth the hour:

1. **AREA_GAIN**
   How many currently-fog cells the disk would reveal. Bigger = more
   information gained. Overlapping with existing LOS is wasted budget.

2. **EDGE_PROMISE**
   Extra value when the disk extends outward from LOS edges that
   already show high-purity RED (seams tend to continue past visible
   boundaries), or when the disk covers cells the engine has flagged
   in ``navigation.best_red_echo``.

The LLM sees ``area_gain`` and ``edge_promise`` as separate ints — no
combined score field, mirroring the "candidates as hints" rule. It's
free to rank, alter, or ignore.

Self-contained
--------------
Reads ``agent_view`` directly. No reach into pilot_v4 / other harnesses.
Keeps Tabula v2 on the same clean-base architecture v1 established.
"""

from __future__ import annotations

import random
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

# Probe disk radius (Chebyshev). Must match the engine's probe FOV rule.
_PROBE_RADIUS = 4

# v9 seeded-variability knobs (Phase 1). These are ONLY consulted when a caller
# passes an ``rng`` (the v9 harness does; frozen v6/v7/v8 pass nothing, so their
# output is byte-identical to before). ``_TIE_EPS`` is the "near-tie" band:
# candidates whose internal rank is within this of a run leader are treated as
# equivalent and shuffled, so different seats fan out onto different equally-good
# cells instead of all diving the single deterministic best. It is small enough
# that a materially-stronger candidate is never demoted (never-trade-down).
_PROBE_TIE_EPS = 3.0
_HOTDROP_TIE_EPS = 3.0


def _tie_shuffle(
    items: List[Any],
    key_of: Callable[[Any], float],
    rng: "random.Random",
    epsilon: float,
) -> List[Any]:
    """Shuffle only the near-tied RUNS of an already-desc-sorted list.

    ``items`` MUST be pre-sorted by ``key_of`` descending. Consecutive items
    whose key is within ``epsilon`` of the current RUN LEADER's key form a group
    that is shuffled in place; a new run starts the moment an item falls more
    than ``epsilon`` below the leader. So an item can be promoted at most
    ``epsilon`` above where its raw score would place it, and a materially
    stronger candidate is never demoted below a materially weaker one (the
    "never trade down past epsilon" guardrail). Only ever called on the v9 path
    (``rng`` provided); the frozen deterministic path never touches this.
    """
    out: List[Any] = []
    i, n = 0, len(items)
    while i < n:
        top = key_of(items[i])
        j = i + 1
        while j < n and (top - key_of(items[j])) <= epsilon:
            j += 1
        run = items[i:j]
        rng.shuffle(run)
        out.extend(run)
        i = j
    return out

# Extra weight per unit of edge promise, used ONLY for our internal
# ranking of candidates (the LLM never sees this). Set high enough that
# a 200-purity mass neighbour tips a candidate over one that only wins
# on raw area. Purely a candidate-ordering knob.
_EDGE_WEIGHT = 0.05

_TIER_MULT = {"trace": 0.75, "vein": 1.0, "mass": 1.5, "pure": 3.0}

# Diversity cap: at most this many bluesign-seeded probes in the PROBE
# PLACEMENT HINTS. A cluster of bright bluesign cells all earn the
# ``+100`` signal bonus below, so without a cap they fill every slot and
# hide the fog-exploration probes the nomadic doctrine depends on. The
# cap reserves the remaining slots for fog_centroid / los_edge / seam
# candidates; a second pass tops up from deferred bluesign only if the
# board genuinely has nothing else worth probing. Redsign is never
# capped (rare + top priority). Bluesign still has its own dedicated
# surface in HOT DROP HINTS, so capping it here loses no information.
_MAX_BLUESIGN_PROBE_HINTS = 1


def _tier_name(purity: int) -> str:
    p = int(purity or 0)
    if p >= 255:
        return "pure"
    if p >= 151:
        return "mass"
    if p >= 51:
        return "vein"
    return "trace"


def _grid_dims(agent_view: Mapping[str, Any]) -> Tuple[int, int]:
    world = agent_view.get("world") or {}
    try:
        return int(world.get("width") or 40), int(world.get("height") or 28)
    except (TypeError, ValueError):
        return 40, 28


def _los_cells(agent_view: Mapping[str, Any]) -> Set[Tuple[int, int]]:
    """Cells currently in live vision (a probe disk or a friendly unit).

    Reads ``world.live[]`` which each cell inside LOS is enumerated in.
    """
    out: Set[Tuple[int, int]] = set()
    for row in ((agent_view.get("world") or {}).get("live") or []):
        if not isinstance(row, Mapping):
            continue
        try:
            out.add((int(row["x"]), int(row["y"])))
        except (TypeError, KeyError, ValueError):
            continue
    return out


def _fog_clusters(agent_view: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [c for c in (agent_view.get("fog_clusters") or []) if isinstance(c, Mapping)]


def _echo_cells(agent_view: Mapping[str, Any]) -> Dict[Tuple[int, int], int]:
    """Best-red echo readings (cells hinted at beyond LOS). Keyed by
    (x, y) to purity estimate."""
    out: Dict[Tuple[int, int], int] = {}
    for row in (((agent_view.get("navigation") or {}).get("best_red_echo")) or []):
        if not isinstance(row, Mapping):
            continue
        try:
            out[(int(row["x"]), int(row["y"]))] = int(row.get("purity") or row.get("value") or 0)
        except (TypeError, KeyError, ValueError):
            continue
    return out


# Bright cells inside a bluesign are near-certain to contain real blue.
# Intensity threshold picked so a "well-lit" cluster interior qualifies
# but faint outer cells don't. Tune here if the scoring feels off.
_BLUESIGN_BRIGHT_THRESHOLD = 0.6


def _blue_sign_bright_cells(agent_view: Mapping[str, Any]) -> Dict[Tuple[int, int], float]:
    """Cells inside blue_sign clusters with intensity >= threshold.

    Blue sign is a PUBLIC static map (every seat sees the same data).
    Cluster ``cells`` are ``[[x, y, intensity], ...]`` where intensity
    is 0.0-1.0. High intensity → higher confidence real blue lives at
    (or very near) that cell. Purity is unknown until probed / walked.
    """
    out: Dict[Tuple[int, int], float] = {}
    for cluster in (agent_view.get("blue_sign") or []):
        if not isinstance(cluster, Mapping):
            continue
        for cell in (cluster.get("cells") or []):
            if not isinstance(cell, (list, tuple)) or len(cell) < 3:
                continue
            try:
                x, y, intensity = int(cell[0]), int(cell[1]), float(cell[2])
            except (TypeError, ValueError):
                continue
            if intensity >= _BLUESIGN_BRIGHT_THRESHOLD:
                # Keep the strongest intensity if a cell shows up in
                # multiple clusters (rare but possible on cluster edges).
                out[(x, y)] = max(out.get((x, y), 0.0), intensity)
    return out


def _redsign_cells(agent_view: Mapping[str, Any]) -> Dict[Tuple[int, int], int]:
    """Redsign broadcast cells — public "someone found pure(255) HERE"
    announcements. Empty at start; populates as pure red is discovered.

    The engine stores each broadcast as a REGION object
    ``{"id", "center": [cx, cy], "cells": [[x, y, intensity], ...],
    "day", "hour"}`` (a fuzzy smear over the pure seam — see
    ``session._mint_redsign_region``). We flatten every smear cell (and
    the region centre as a fallback) keyed by (x, y), storing the region
    ``hour`` as a freshness signal. Also tolerates the legacy flat
    ``{"x","y"}`` / ``[x, y, hour]`` shapes so old fixtures still parse.
    """
    out: Dict[Tuple[int, int], int] = {}

    def _put(xf: Any, yf: Any, hour: Any) -> None:
        try:
            out[(int(round(float(xf))), int(round(float(yf))))] = int(hour or 0)
        except (TypeError, ValueError):
            pass

    for row in (agent_view.get("redsign") or []):
        if isinstance(row, Mapping):
            hour = row.get("hour") or 0
            cells = row.get("cells")
            if isinstance(cells, (list, tuple)) and cells:
                for c in cells:
                    if isinstance(c, Mapping):
                        _put(c.get("x"), c.get("y"), hour)
                    elif isinstance(c, (list, tuple)) and len(c) >= 2:
                        _put(c[0], c[1], hour)
                continue
            if "x" in row and "y" in row:  # legacy flat shape
                _put(row.get("x"), row.get("y"), hour)
                continue
            center = row.get("center")
            if isinstance(center, (list, tuple)) and len(center) >= 2:
                _put(center[0], center[1], hour)
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            _put(row[0], row[1], row[2] if len(row) >= 3 else 0)
    return out


def _redsign_centers(agent_view: Mapping[str, Any]) -> List[Tuple[int, int]]:
    """Region centres of live redsign broadcasts, for the concrete
    'REDSIGN LIVE near (cx,cy)' line the prompt surfaces. Falls back to
    the mean of a region's smear cells when no centre is present."""
    centers: List[Tuple[int, int]] = []
    for row in (agent_view.get("redsign") or []):
        if not isinstance(row, Mapping):
            continue
        center = row.get("center")
        if isinstance(center, (list, tuple)) and len(center) >= 2:
            try:
                centers.append((int(round(float(center[0]))), int(round(float(center[1])))))
                continue
            except (TypeError, ValueError):
                pass
        cells = row.get("cells")
        if isinstance(cells, (list, tuple)) and cells:
            xs, ys = [], []
            for c in cells:
                pt = c if isinstance(c, (list, tuple)) else None
                if pt and len(pt) >= 2:
                    try:
                        xs.append(float(pt[0]))
                        ys.append(float(pt[1]))
                    except (TypeError, ValueError):
                        continue
            if xs:
                centers.append((int(round(sum(xs) / len(xs))), int(round(sum(ys) / len(ys)))))
    return centers


def _enemy_probe_cells(agent_view: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Known enemy probe positions, freshest first.

    An enemy probe launch is PUBLIC (§3.15) — the orbital station sees
    where it landed even through fog. We stitch together every channel
    the view exposes and dedup by cell, keeping the most recent sighting:
      * ``competitor_intel.new_this_day``  — yesterday's launches (freshest)
      * ``competitor_intel.persistent_echoes`` — older last-seen enemy probes
      * ``world.echo`` rows with ``via='probe_launch'`` (fog probe markers)
      * ``entities.echoes`` — opponent entity last-seen positions

    Returns ``[{"at": (x, y), "day_seen": int, "source": str}, ...]``
    ordered by ``day_seen`` descending (most recent first). ``day_seen``
    defaults to 0 when a channel doesn't carry it.
    """
    best: Dict[Tuple[int, int], Dict[str, Any]] = {}

    def _add(at: Any, day_seen: Any, source: str) -> None:
        if not (isinstance(at, (list, tuple)) and len(at) >= 2):
            return
        try:
            cell = (int(at[0]), int(at[1]))
        except (TypeError, ValueError):
            return
        try:
            d = int(day_seen) if day_seen is not None else 0
        except (TypeError, ValueError):
            d = 0
        prev = best.get(cell)
        if prev is None or d >= int(prev.get("day_seen") or 0):
            best[cell] = {"at": cell, "day_seen": d, "source": source}

    ci = agent_view.get("competitor_intel") or {}
    for row in (ci.get("new_this_day") or []):
        if isinstance(row, Mapping) and row.get("kind") == "enemy_probe_launch":
            _add(row.get("at"), row.get("day_seen"), "launch")
    for row in (ci.get("persistent_echoes") or []):
        if isinstance(row, Mapping) and row.get("kind") == "enemy_probe":
            _add(row.get("at"), row.get("last_seen_day"), "echo")

    for row in ((agent_view.get("world") or {}).get("echo") or []):
        if not isinstance(row, Mapping) or row.get("via") != "probe_launch":
            continue
        for occ in (row.get("occupants") or []):
            if isinstance(occ, Mapping) and occ.get("type") == "probe":
                _add([row.get("x"), row.get("y")], row.get("last_seen_day"), "world_echo")

    for row in ((agent_view.get("entities") or {}).get("echoes") or []):
        if isinstance(row, Mapping) and row.get("type") == "probe":
            pos = row.get("last_seen_pos") or row.get("at")
            _add(pos, row.get("last_seen_day"), "entity_echo")

    return sorted(best.values(), key=lambda r: int(r.get("day_seen") or 0), reverse=True)


def _visible_red(agent_view: Mapping[str, Any]) -> Dict[Tuple[int, int], int]:
    """Every RED cell currently in LOS with its purity."""
    out: Dict[Tuple[int, int], int] = {}
    for row in (agent_view.get("red_tiles") or []):
        if not isinstance(row, Mapping):
            continue
        try:
            x, y = int(row["x"]), int(row["y"])
            p = int(row.get("purity") or 0)
        except (TypeError, KeyError, ValueError):
            continue
        if p > 0:
            out[(x, y)] = p
    return out


def _disk(cx: int, cy: int, width: int, height: int) -> List[Tuple[int, int]]:
    """Chebyshev 9x9 box — a CHEAP proxy for candidate ranking / overlap
    only. NOT the drop-legal shape; use :func:`_vision_disk` for anything
    that decides where a harvester may actually land."""
    r = _PROBE_RADIUS
    return [
        (x, y)
        for x in range(max(0, cx - r), min(width, cx + r + 1))
        for y in range(max(0, cy - r), min(height, cy + r + 1))
    ]


def _vision_disk(cx: int, cy: int, width: int, height: int) -> List[Tuple[int, int]]:
    """Euclidean radius-4 disk (~49 cells) — the exact live-vision /
    drop-legal shape the engine uses (``session._euclidean_disk``)."""
    r = _PROBE_RADIUS
    return [
        (x, y)
        for x in range(max(0, cx - r), min(width, cx + r + 1))
        for y in range(max(0, cy - r), min(height, cy + r + 1))
        if (x - cx) * (x - cx) + (y - cy) * (y - cy) <= r * r
    ]


def _covers(cx: int, cy: int, tx: int, ty: int) -> bool:
    """True iff a probe at (cx,cy) makes (tx,ty) drop-legal (Euclidean)."""
    return (tx - cx) * (tx - cx) + (ty - cy) * (ty - cy) <= _PROBE_RADIUS * _PROBE_RADIUS


# ── v9 additive: contested detection + ranked seam alternatives ─────────
#
# These helpers add COMPETITIVE-CHOICE metadata to the hint payloads without
# changing the primary pick — the first probe_at/drop_at every existing caller
# reads is byte-identical to before, so frozen v6/v7/v8 are unaffected. v9
# renders the extra keys (``contested`` / ``alt_drops`` / ``supersede``) via
# its own tactics block; older formatters ignore unknown keys.
#
# Stance (per the v9 plan): expose DIVERSITY OF OPTIONS for the agent to
# choose among; NO hidden RNG on the final placement. The offsets are the
# deterministic seam cells around a beacon, ranked nearest-first.

def _target_contested(
    tx: int, ty: int, signal_type: str,
    enemy_cells: Sequence[Tuple[int, int]],
) -> bool:
    """A drop target is CONTESTED when everyone can see/reach it.

    True when it is a public REDSIGN beacon (every seat races the same cell)
    or when a known enemy probe already covers it (a rival can drop there too
    -> a shared cell risks a mutual-kill collision).
    """
    if signal_type == "redsign":
        return True
    return any(_covers(ex, ey, tx, ty) for (ex, ey) in enemy_cells)


def _seam_alternatives(
    chosen_probe: Tuple[int, int],
    primary_drop: Tuple[int, int],
    tx: int, ty: int,
    width: int, height: int,
    *,
    exclude: Set[Tuple[int, int]],
    max_alts: int = 3,
) -> List[List[int]]:
    """Ranked OFFSET seam cells to drop on instead of the advertised beacon.

    Returns drop-legal cells (inside ``chosen_probe``'s disk) at Manhattan
    distance 1-2 from the beacon, nearest-first, excluding the beacon itself,
    the primary drop, and any ``exclude`` cell already committed. These are the
    "drop offset onto the seam" options the redsign-poker book calls for.
    """
    cands: List[Tuple[int, Tuple[int, int]]] = []
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            dist = abs(dx) + abs(dy)
            if dist == 0 or dist > 2:
                continue
            cx, cy = tx + dx, ty + dy
            cell = (cx, cy)
            if not (0 <= cx < width and 0 <= cy < height):
                continue
            if cell == (tx, ty) or cell == primary_drop or cell in exclude:
                continue
            if not _covers(chosen_probe[0], chosen_probe[1], cx, cy):
                continue
            cands.append((dist, cell))
    cands.sort(key=lambda t: (t[0], t[1]))
    return [[c[0], c[1]] for _d, c in cands[:max_alts]]


def _known_green_cells(agent_view: Mapping[str, Any]) -> Set[Tuple[int, int]]:
    """GREEN cells currently in live vision — the only green a comb path can
    KNOW to avoid. Fog green is invisible at plan time (a hot drop is blind),
    so this is a best-effort hazard filter, not a guarantee."""
    out: Set[Tuple[int, int]] = set()
    for row in ((agent_view.get("world") or {}).get("live") or []):
        if not isinstance(row, Mapping):
            continue
        if str(row.get("tile") or "") != "GREEN":
            continue
        try:
            out.add((int(row["x"]), int(row["y"])))
        except (TypeError, KeyError, ValueError):
            continue
    return out


def _comb_path(
    cx: int,
    cy: int,
    start: Tuple[int, int],
    width: int,
    height: int,
    bad: Set[Tuple[int, int]],
    value_cells: Sequence[Tuple[int, int]],
    *,
    max_steps: int = 6,
) -> List[List[int]]:
    """Precompute a blind space-filling comb for a hot drop.

    A hot drop is BLIND: the probe reveals the disk at hour K, the harvester
    drops at K+1, but the whole move chain is submitted up front — the agent
    cannot react to what the probe uncovers. So the only way to bank the
    value a signal promises is to WALK a serpentine that covers as much of
    the probe's disk as possible, hugging the value cluster.

    Returns an ordered list of ``[x, y]`` STEP targets (excluding ``start``,
    which is the drop cell) forming a contiguous Manhattan walk that:
      * stays inside the probe's Euclidean r4 disk (every cell is live
        vision once the probe lands, so each step is legal),
      * never revisits a cell (avoids banking its own -100 green wake),
      * avoids KNOWN green (fog green is unknowable at plan time),
      * greedily heads toward the nearest ``value_cell`` (the signal
        cluster), then spreads outward to comb fresh ground.

    This is the mechanical "comb, don't snatch" guarantee: the LLM (or the
    sanitizer) can copy it verbatim instead of improvising a 1-2 step chain.
    """
    disk = set(_vision_disk(cx, cy, width, height))
    vcells = [v for v in value_cells if v in disk]
    visited = {start}
    path: List[List[int]] = []
    cur = start
    for _ in range(max_steps):
        best: Optional[Tuple[int, int]] = None
        best_key: Optional[Tuple[int, int]] = None
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = cur[0] + dx, cur[1] + dy
            n = (nx, ny)
            if n not in disk or n in visited or n in bad:
                continue
            # Prefer the neighbour nearest an unbanked value cell; tie-break
            # by MORE distance from the probe centre so we fan out and comb
            # a wider area rather than circling the middle.
            if vcells:
                dv = min((nx - vx) ** 2 + (ny - vy) ** 2 for vx, vy in vcells)
            else:
                dv = 0
            spread = (nx - cx) ** 2 + (ny - cy) ** 2
            key = (dv, -spread)
            if best_key is None or key < best_key:
                best_key = key
                best = n
        if best is None:
            break
        path.append([best[0], best[1]])
        visited.add(best)
        cur = best
    return path


def _seed_candidates(
    agent_view: Mapping[str, Any],
    width: int,
    height: int,
    los: Set[Tuple[int, int]],
) -> Dict[Tuple[int, int], str]:
    """Return ``{(x, y) -> label}`` of candidate probe placements.

    Labels indicate the SOURCE of the seed (rendered as ``extends_from``
    in the hint payload) so the agent can weigh the signal quality:
      * ``"redsign"``      — public "pure(255) here" broadcast (strongest)
      * ``"blue_sign"``    — public bright bluesign cell (strong)
      * ``"echo"``         — this seat's own red echo (weak, often empty)
      * ``"fog_centroid"`` — center of largest fog cluster (neutral)
      * ``"los_edge"``     — nearest_visible_edge to a fog cluster (neutral)
      * ``"seam_extension"`` — one step past a high-purity visible red cell

    Cells fully inside LOS are dropped except when they're echoes (echoes
    live in fog by definition and can still be worth revisiting).
    """
    seeds: Dict[Tuple[int, int], str] = {}

    def _in_bounds(xy: Tuple[int, int]) -> bool:
        return 0 <= xy[0] < width and 0 <= xy[1] < height

    # Strongest signals first — bluesign and redsign are PUBLIC data.
    # (Bounds-checked because external signal data has been observed
    # to reference cells outside the current grid — real bug in v3.)
    for xy in _redsign_cells(agent_view):
        if _in_bounds(xy):
            seeds[xy] = "redsign"
    for xy in _blue_sign_bright_cells(agent_view):
        if _in_bounds(xy):
            seeds.setdefault(xy, "blue_sign")

    # Fog structure — where to un-cover territory blindly.
    for cluster in _fog_clusters(agent_view):
        centroid = cluster.get("centroid")
        if isinstance(centroid, (list, tuple)) and len(centroid) == 2:
            try:
                cxy = (int(centroid[0]), int(centroid[1]))
            except (TypeError, ValueError):
                cxy = None
            if cxy is not None and _in_bounds(cxy):
                seeds.setdefault(cxy, "fog_centroid")
        nve = cluster.get("nearest_visible_edge")
        if isinstance(nve, (list, tuple)) and len(nve) == 2:
            try:
                nxy = (int(nve[0]), int(nve[1]))
            except (TypeError, ValueError):
                nxy = None
            if nxy is not None and _in_bounds(nxy):
                seeds.setdefault(nxy, "los_edge")

    for xy in _echo_cells(agent_view):
        if _in_bounds(xy):
            seeds.setdefault(xy, "echo")

    # Seam extension — one step out from every high-purity visible red.
    red = _visible_red(agent_view)
    ranked_reds = sorted(red.items(), key=lambda kv: kv[1], reverse=True)[:6]
    for (rx, ry), _ in ranked_reds:
        for dx in (-4, -3, 0, 3, 4):
            for dy in (-4, -3, 0, 3, 4):
                if dx == 0 and dy == 0:
                    continue
                cx, cy = rx + dx, ry + dy
                if 0 <= cx < width and 0 <= cy < height:
                    seeds.setdefault((cx, cy), "seam_extension")

    # Filter obviously silly seeds (fully in LOS), except signal cells
    # which we ALWAYS keep even if lit — signals point to real value
    # you want to walk onto, not just un-fog.
    echoes = set(_echo_cells(agent_view).keys())
    signal_cells = set(_redsign_cells(agent_view)) | set(_blue_sign_bright_cells(agent_view))
    return {
        xy: label for xy, label in seeds.items()
        if xy in echoes or xy in signal_cells or xy not in los
    }


def _score_candidate(
    at: Tuple[int, int],
    width: int,
    height: int,
    los: Set[Tuple[int, int]],
    red: Mapping[Tuple[int, int], int],
    echoes: Mapping[Tuple[int, int], int],
) -> Tuple[int, int, List[Tuple[int, int]]]:
    """Return (area_gain, edge_promise, disk_cells) for a candidate."""
    disk_cells = _disk(at[0], at[1], width, height)
    area_gain = sum(1 for c in disk_cells if c not in los)

    # Edge promise: (a) visible red immediately outside the disk (seam
    # extension), plus (b) echo cells inside the disk that would resolve
    # to real red once probed. Both weighted by tier_mult × purity so
    # a pure hint dominates a trace hint.
    edge = 0.0
    disk_set = set(disk_cells)
    # (a) Visible red just outside the disk boundary (Chebyshev-adjacent
    # to any disk cell but not inside the disk itself).
    for (rx, ry), purity in red.items():
        if (rx, ry) in disk_set:
            continue
        # Chebyshev-1 to any disk cell? Cheap check: dist to (at[0],at[1])
        # in (_PROBE_RADIUS, _PROBE_RADIUS+1].
        d = max(abs(rx - at[0]), abs(ry - at[1]))
        if d == _PROBE_RADIUS + 1:
            edge += purity * _TIER_MULT.get(_tier_name(purity), 1.0)
    # (b) Echo cells inside the disk.
    for (ex, ey), purity in echoes.items():
        if (ex, ey) in disk_set:
            edge += purity * _TIER_MULT.get(_tier_name(purity), 1.0)

    return area_gain, int(round(edge)), disk_cells


def top_probe_hints(
    agent_view: Mapping[str, Any],
    *,
    max_hints: int = 3,
    historical_probe_positions: Optional[Sequence[Tuple[int, int]]] = None,
    rng: "Optional[random.Random]" = None,
) -> List[Dict[str, Any]]:
    """Return up to ``max_hints`` probe-placement candidates.

    Each hint carries:
      * ``at`` — [x, y] of the probe drop
      * ``area_gain`` — # fog cells the disk would reveal
      * ``edge_promise`` — purity-weighted seam-extension value
      * ``extends_from`` — short label describing why this cell was seeded
        ("fog_centroid", "los_edge", "echo", "seam_extension")

    NO combined score. LLM ranks by re-reading area_gain + edge_promise
    against its own doctrine (RULES probe strategy section).

    ``historical_probe_positions`` — every (x,y) this player has probed
    earlier in the season, including probes that have already expired.
    Without this, the anti-clustering filter is myopic: it only sees
    ACTIVE probes and happily re-suggests the exact same seed once the
    original probe ages out (observed at day 3 of arena_solo_normal
    where probe A came back to (7,4) — same cell as night 1). The
    caller (harness) collects these from ``_SNAPSHOTS.moves_by_day``.

    Empty return means the board has no useful probes right now (either
    no fog at all, or every candidate has area_gain == 0). The LLM is
    trusted to spend its probe elsewhere or skip.
    """
    width, height = _grid_dims(agent_view)
    los = _los_cells(agent_view)
    # NOTE: empty LOS is a legitimate state (day 1 no probes yet). We
    # used to bail here — that was wrong; every cell has max area_gain
    # in that case, and public signals (bluesign, redsign) can still
    # point at useful probes even without any pre-existing LOS.

    fog_count = int(((agent_view.get("world") or {}).get("fog_count")) or 0)
    if fog_count == 0:
        return []

    red = _visible_red(agent_view)
    echoes = _echo_cells(agent_view)

    seeds = _seed_candidates(agent_view, width, height, los)
    if not seeds:
        return []

    # v9 additive: cells that make a placement CONTESTED (a public beacon a
    # rival also races, or a known enemy probe nearby). Used only to tag the
    # emitted hint; the ranking/selection is unchanged.
    _contested_redsign = set(_redsign_cells(agent_view).keys())
    _contested_enemy = [
        row["at"] for row in _enemy_probe_cells(agent_view)
        if isinstance(row.get("at"), tuple) and len(row["at"]) == 2
    ]

    # Filter out seeds too close to a currently-active friendly probe OR
    # to any cell where a probe was launched EARLIER this season (even
    # if it has since expired). Active-only filtering was myopic: once a
    # night-1 probe aged out, the compiler happily re-suggested the same
    # cell on night 3, and the LLM followed. History-aware filtering
    # enforces the nomadic doctrine STRUCTURALLY across the whole
    # season, not just within the currently-visible board.
    #
    # We measure "too close" by disk-center Chebyshev distance:
    #   4 = perfect overlap, 8 = no overlap.
    # Threshold=4 kills exact-duplicate coords; threshold=6 kills
    # significant overlap. We use 5 as a middle ground.
    active_probes = _friendly_probe_positions(agent_view)
    prior_probes = list(historical_probe_positions or [])
    all_probes = list({*active_probes, *prior_probes})
    _MIN_PROBE_SEPARATION = 5
    if all_probes:
        filtered_seeds: Dict[Tuple[int, int], str] = {}
        for xy, label in seeds.items():
            too_close = any(
                max(abs(xy[0] - px), abs(xy[1] - py)) < _MIN_PROBE_SEPARATION
                for (px, py) in all_probes
            )
            if not too_close:
                filtered_seeds[xy] = label
        seeds = filtered_seeds
        if not seeds:
            return []

    scored: List[Tuple[float, int, int, Tuple[int, int], str]] = []
    for at, label in seeds.items():
        area_gain, edge_promise, _ = _score_candidate(at, width, height, los, red, echoes)
        # Probes MUST reveal new fog to earn a slot. Removed the previous
        # "signal cells always earn a slot" carve-out — signals are for
        # WALKING (via chain hints / hot drop hints), not for re-probing
        # cells already in LOS. If a bluesign cell is in a friendly
        # probe's disk, we already see it; a new probe there wastes an
        # hour and a probe slot.
        if area_gain <= 0 and edge_promise <= 0:
            continue
        internal_rank = area_gain + _EDGE_WEIGHT * edge_promise
        # Modest bonuses for signal-associated seeds still apply — they
        # bias the compiler toward probes that BOTH reveal new fog AND
        # extend toward a signal cluster. But signals alone (area_gain=0)
        # no longer surface here.
        if label == "redsign":
            internal_rank += 500
        elif label == "blue_sign":
            internal_rank += 100
        scored.append((internal_rank, area_gain, edge_promise, at, label))

    scored.sort(key=lambda t: t[0], reverse=True)
    # v9: break near-ties with the seeded rng so seats fan out onto different
    # equally-good placements instead of all taking the deterministic best.
    if rng is not None:
        scored = _tie_shuffle(scored, lambda t: t[0], rng, _PROBE_TIE_EPS)

    # De-duplicate near-identical placements (candidates whose disks
    # overlap >75%) so the LLM sees genuinely distinct options, and cap
    # bluesign so the menu isn't all-bluesign (see _MAX_BLUESIGN_PROBE_HINTS).
    hints: List[Dict[str, Any]] = []
    taken_disks: List[Set[Tuple[int, int]]] = []
    deferred_bluesign: List[Tuple[Tuple[int, int], int, int, str, Set[Tuple[int, int]]]] = []
    bluesign_taken = 0

    def _overlaps(disk_set: Set[Tuple[int, int]]) -> bool:
        return any(
            len(disk_set & taken) / max(1, len(disk_set)) > 0.75
            for taken in taken_disks
        )

    def _emit(at: Tuple[int, int], area_gain: int, edge_promise: int,
              label: str, disk_set: Set[Tuple[int, int]]) -> None:
        contested = (
            label == "redsign"
            or any(c in _contested_redsign for c in disk_set)
            or any((ex, ey) in disk_set for (ex, ey) in _contested_enemy)
        )
        hints.append({
            "at": [int(at[0]), int(at[1])],
            "area_gain": int(area_gain),
            "edge_promise": int(edge_promise),
            "extends_from": label,
            # v9-only key (ignored by v6/v7/v8 formatters):
            "contested": bool(contested),
        })
        taken_disks.append(disk_set)

    # First pass — respect the bluesign cap so fog-exploration probes get
    # slots. Bluesign candidates beyond the cap are held back (deferred)
    # rather than dropped, so we can top up if nothing else qualifies.
    for _rank, area_gain, edge_promise, at, label in scored:
        disk_set = set(_disk(at[0], at[1], width, height))
        if _overlaps(disk_set):
            continue
        if label == "blue_sign" and bluesign_taken >= _MAX_BLUESIGN_PROBE_HINTS:
            deferred_bluesign.append((at, area_gain, edge_promise, label, disk_set))
            continue
        _emit(at, area_gain, edge_promise, label, disk_set)
        if label == "blue_sign":
            bluesign_taken += 1
        if len(hints) >= max_hints:
            return hints

    # Second pass — the cap left us short (board was mostly bluesign);
    # top up from the deferred bluesign candidates rather than waste slots.
    for at, area_gain, edge_promise, label, disk_set in deferred_bluesign:
        if _overlaps(disk_set):
            continue
        _emit(at, area_gain, edge_promise, label, disk_set)
        if len(hints) >= max_hints:
            break

    return hints


# ═══════════════════════════════════════════════════════════════════════
# HOT DROP HINTS — probe at hour K, drop at hour K+1 onto probe's disk
# ═══════════════════════════════════════════════════════════════════════
#
# The engine refreshes live-vision each hour (game/simulator.py:245), so
# a probe applied at hour K becomes live-vision for a drop at hour K+1.
# This lets a single harvester reach a mystery cell in ONE night rather
# than waiting until the next dawn.
#
# A hot drop is only worth suggesting when we have STRONG public reason
# to believe the disk contains harvestable value. The strongest signals:
#   * ``redsign``   — public "pure(255) here" broadcast. Fires when any
#                     seat discovers a pure cell. Whoever probes+drops
#                     first wins ~765 pts × tier_mult. Race the opponent.
#   * ``blue_sign`` — public static map of blue clusters. Bright cells
#                     (>=0.6 intensity) have real blue nearby; the probe
#                     confirms exactly WHERE and the drop crawls onto it.
#                     Purity is unknown until landed, but bluesign
#                     guarantees dense blue is SOMEWHERE in the cluster.
#
# Suppression: bluesign hot drops are dropped when the visible board
# already has >= 3 blue cells in LOS. The agent should prefer KNOWN
# blue over a bluesign gamble when a better option exists.


def top_hot_drop_hints(
    agent_view: Mapping[str, Any],
    *,
    max_hints: int = 3,
    rng: "Optional[random.Random]" = None,
) -> List[Dict[str, Any]]:
    """Return up to ``max_hints`` probe-then-drop pairings for this night.

    Each hint carries:
      * ``signal_type``       — "redsign" or "blue_sign"
      * ``signal_intensity``  — float for bluesign (0.6-1.0), 1.0 for redsign
      * ``probe_at``          — [x, y] to launch the probe at hour 1
      * ``drop_at``           — [x, y] the harvester should land on
      * ``area_gain``         — # fog cells the probe reveals (context)
      * ``unit``              — harvester id the drop is scoped to

    Returns [] when:
      * No probes in stock, OR
      * No harvesters in orbit, OR
      * No bluesign bright cells AND no redsign broadcasts
    """
    stock = int((agent_view.get("orbit") or {}).get("probe_stock") or 0)
    if stock <= 0:
        return []

    harvesters = _orbit_harvester_ids(agent_view)
    if not harvesters:
        return []

    width, height = _grid_dims(agent_view)
    los = _los_cells(agent_view)
    green = _known_green_cells(agent_view)
    # v9 additive: known enemy probe cells, for contested detection + the
    # supersede tactical alternative (blind the finder's probe).
    enemy_cells = [
        row["at"] for row in _enemy_probe_cells(agent_view)
        if isinstance(row.get("at"), tuple) and len(row["at"]) == 2
    ]

    # Signal collection. Redsign always ranks first (pure red is worth
    # more per cell than any blue). Blue only surfaces if the visible
    # board isn't already blue-rich — 3+ known blue cells in LOS means
    # bluesign is a suboptimal use of the harvester's night.
    redsign = _redsign_cells(agent_view)
    visible_blue_count = sum(
        1 for row in (agent_view.get("blue_tiles") or [])
        if isinstance(row, Mapping)
    )
    if visible_blue_count >= 3:
        bluesign_bright: Dict[Tuple[int, int], float] = {}
    else:
        bluesign_bright = _blue_sign_bright_cells(agent_view)

    # Build (score, target_cell, signal_type, intensity) triples.
    # redsign gets a fixed high score; bluesign scales by intensity so
    # a 0.9 cell wins over a 0.6 cell.
    targets: List[Tuple[float, Tuple[int, int], str, float]] = []
    for xy, _hour in redsign.items():
        targets.append((10.0, xy, "redsign", 1.0))
    for xy, intensity in bluesign_bright.items():
        targets.append((float(intensity), xy, "blue_sign", float(intensity)))
    if not targets:
        return []
    if rng is None:
        # Frozen path — deterministic, byte-identical to before.
        targets.sort(key=lambda t: t[0], reverse=True)
    else:
        # v9 path — rank by (signal score, then fog-coverage), then seeded-shuffle
        # near-ties so seats stop stacking the SAME smear/bluesign cell. Weight so
        # the signal score always dominates coverage (redsign >> bluesign; a higher
        # intensity always beats a lower one), with coverage only ordering cells of
        # equal signal. Combined key: score*1000 + min(cov,49); eps in cov units.
        def _cov(tx: int, ty: int) -> int:
            return sum(
                1 for c in _vision_disk(tx, ty, width, height) if c not in los
            )

        decorated = [
            (float(t[0]) * 1000.0 + float(min(_cov(t[1][0], t[1][1]), 49)), t)
            for t in targets
        ]
        decorated.sort(key=lambda d: d[0], reverse=True)
        decorated = _tie_shuffle(
            decorated, lambda d: d[0], rng, _HOTDROP_TIE_EPS,
        )
        targets = [t for _k, t in decorated]

    hints: List[Dict[str, Any]] = []
    used_probe_positions: Set[Tuple[int, int]] = set()
    used_drop_positions: Set[Tuple[int, int]] = set()

    for _score, (tx, ty), signal_type, intensity in targets:
        if (tx, ty) in used_drop_positions:
            continue
        # Find the probe placement that covers this target AND maximises
        # area_gain. CRITICAL: we try OFFSET probe centres FIRST and only
        # fall back to centring the probe on the target as a last resort.
        # Why: the drop lands on the target, so if the probe is ALSO
        # centred there (probe_at == drop_at) the drop CRUSHES the probe it
        # just paid for. On an all-fog board (night 1) every candidate
        # reveals ~49 cells so ``gain`` ties — with the target listed first
        # the old code kept it (strict ``>``) and crushed EVERY night-1 hot
        # drop. Listing offsets first + target last means a tied offset
        # wins and the probe survives. Coverage + gain use the EUCLIDEAN
        # disk (the real drop-legal shape).
        offset_probes = [
            (tx + dx, ty + dy)
            for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2)
            if not (dx == 0 and dy == 0)
        ]
        candidate_probes = offset_probes + [(tx, ty)]
        chosen_probe = None
        best_gain = -1
        for cx, cy in candidate_probes:
            if not (0 <= cx < width and 0 <= cy < height):
                continue
            if (cx, cy) in used_probe_positions:
                continue
            if not _covers(cx, cy, tx, ty):
                continue
            disk = _vision_disk(cx, cy, width, height)
            gain = sum(1 for c in disk if c not in los)
            if gain > best_gain:
                best_gain = gain
                chosen_probe = (cx, cy)
        if chosen_probe is None:
            continue

        # Drop lands on the target. If the ONLY legal probe was the target
        # itself, move the drop OFF the centre to a drop-legal in-disk
        # neighbour so we don't crush the fresh probe.
        drop_at: Tuple[int, int] = (tx, ty)
        note = "drop lands on the target; probe centre stays intact"
        if chosen_probe == (tx, ty):
            neighbour = None
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = tx + dx, ty + dy
                if not (0 <= nx < width and 0 <= ny < height):
                    continue
                if (nx, ny) in used_drop_positions:
                    continue
                if _covers(chosen_probe[0], chosen_probe[1], nx, ny):
                    neighbour = (nx, ny)
                    break
            if neighbour is not None:
                drop_at = neighbour
                note = ("probe had to centre on the target; drop lands "
                        "ADJACENT to keep the probe alive")
            else:
                note = ("no adjacent landing available — dropping on the "
                        "centre WILL crush the probe")

        unit = harvesters[len(hints) % len(harvesters)]

        # FOG-GATE: is the beacon already inside live vision (a prior probe
        # disk / friendly unit)? If so a hot drop is REDUNDANT — the agent
        # can drop directly onto the visible cell (see it in VISIBLE RED /
        # DROP-LEGAL) and keep the probe for fresh fog. The genuine hot-drop
        # win is when the beacon sits in FOG; that is the case the earlier
        # audit found v5 never actually exploited (it only ever rode STALE
        # disks that already covered the seam). We annotate rather than
        # suppress so the race option is never lost.
        target_in_fog = (tx, ty) not in los

        # Precompute the blind comb the harvester should walk once it lands.
        # value_cells = the signal cluster (redsign smear / bluesign bright)
        # so the serpentine hugs where the value is.
        if signal_type == "redsign":
            value_cells = list(redsign.keys())
        else:
            value_cells = list(bluesign_bright.keys())
        comb = _comb_path(
            chosen_probe[0], chosen_probe[1], drop_at,
            width, height, green, value_cells, max_steps=6,
        )

        # v9 additive: competitive-choice metadata (primary pick unchanged).
        contested = _target_contested(tx, ty, signal_type, enemy_cells)
        alt_drops = _seam_alternatives(
            chosen_probe, drop_at, tx, ty, width, height,
            exclude=used_drop_positions,
        )
        supersede = next(
            ([int(ex), int(ey)] for (ex, ey) in enemy_cells
             if _covers(ex, ey, tx, ty)),
            None,
        )

        hints.append({
            "signal_type": signal_type,
            "signal_intensity": round(intensity, 2),
            "probe_at": [int(chosen_probe[0]), int(chosen_probe[1])],
            "drop_at": [int(drop_at[0]), int(drop_at[1])],
            "area_gain": int(best_gain),
            "unit": unit,
            "note": note,
            "target_in_fog": bool(target_in_fog),
            "comb_path": comb,
            # v9-only keys (ignored by v6/v7/v8 formatters):
            "contested": bool(contested),
            "alt_drops": alt_drops,
            "supersede": supersede,
        })
        used_probe_positions.add(chosen_probe)
        used_drop_positions.add(drop_at)
        if len(hints) >= max_hints:
            break

    return hints


def _orbit_harvester_ids(agent_view: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    for a in (agent_view.get("my_assets") or []):
        if isinstance(a, Mapping) and a.get("kind") == "harvester" and a.get("state") == "orbit":
            uid = a.get("id")
            if isinstance(uid, str):
                out.append(uid)
    return out


def _friendly_probe_positions(agent_view: Mapping[str, Any]) -> List[Tuple[int, int]]:
    """Return the (x, y) center of every currently-active friendly probe.

    Reads from ``agent_view.entities.mine`` (probes are entities of type
    ``probe`` at their launch coord). Excludes probes whose position is
    None (defensive — shouldn't happen for deployed probes) or whose
    remaining nights is 0 (expired but not yet cleared from the view).

    Used by :func:`top_probe_hints` to enforce the nomadic doctrine
    structurally — seeds within :data:`_MIN_PROBE_SEPARATION` of any
    active probe are dropped, so the compiler never suggests a probe
    that duplicates existing vision.
    """
    positions: List[Tuple[int, int]] = []
    for e in ((agent_view.get("entities") or {}).get("mine") or []):
        if not isinstance(e, Mapping):
            continue
        if e.get("type") != "probe":
            continue
        pos = e.get("pos") or e.get("at")
        nr = e.get("nights_remaining")
        if isinstance(nr, (int, float)) and int(nr) <= 0:
            continue
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            try:
                positions.append((int(pos[0]), int(pos[1])))
            except (TypeError, ValueError):
                continue
    return positions


def top_supersede_hints(
    agent_view: Mapping[str, Any],
    *,
    max_hints: int = 4,
) -> List[Dict[str, Any]]:
    """FINAL-NIGHT denial play — probe onto the latest enemy probe cells
    to destroy + supersede them (RULEBOOK §3.16).

    On the last night a probe buys no future vision (there is no next
    night), so spare probe stock is better spent LANDING ON the enemy's
    existing probes: a probe dropped onto a cell holding a PRIOR enemy
    probe destroys it and takes the cell, blinding the opponent's final
    harvest. Your probe survives.

    Gating (last day only) is the caller's job; this just returns the
    targets. Each hint: ``{"probe_at": [ex, ey], "supersedes": <source>,
    "day_seen": <int>}``, freshest enemy probe first, capped at spare
    probe stock. Returns [] when there is no spare stock or no known
    enemy probe.
    """
    stock = int((agent_view.get("orbit") or {}).get("probe_stock") or 0)
    if stock <= 0:
        return []

    enemy = _enemy_probe_cells(agent_view)
    if not enemy:
        return []

    friendly = set(_friendly_probe_positions(agent_view))
    hints: List[Dict[str, Any]] = []
    for row in enemy:
        at = row.get("at")
        if not (isinstance(at, tuple) and len(at) == 2):
            continue
        if at in friendly:
            # We already sit on that cell — nothing to supersede.
            continue
        hints.append({
            "probe_at": [int(at[0]), int(at[1])],
            "supersedes": str(row.get("source") or "enemy_probe"),
            "day_seen": int(row.get("day_seen") or 0),
        })
        if len(hints) >= min(max_hints, stock):
            break
    return hints

