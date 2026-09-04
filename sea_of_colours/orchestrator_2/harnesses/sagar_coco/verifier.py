"""SAGAR_COCO plan verifier and shape planner (view-only, deterministic).

The LLM proposes; this module verifies. Nothing here reads engine truth: every
input comes from ``agent_view`` (the same fog-limited picture every agent gets).

Two jobs:

* ``audit(moves, agent_view)`` scores any candidate order queue against the
  doctrine invariants that follow from the rulebook (pure first, never re-enter
  a wake, never step on rival-made GREEN, drops need live vision, one outing per
  harvester, probe order, slot cap) and returns the violations plus a
  fog-honest value estimate (purity x tier multiplier over first-visit RED
  cells, minus 100 per GREEN parcel).
* ``shape_plan(agent_view)`` builds a complete legal queue from doctrine alone:
  land ON every visible pure, decide the tail from contest, send spare units to
  the ring, blind the rival finder on a rival sign, comb warmer toward a sign we
  cannot see, take long chains on ordinary nights, probe after the fleet is down
  except the single probe that lights a fogged landing.

``choose`` picks between the LLM plan and the shape plan: a clean LLM plan wins
unless it leaves more than a fixed share of the value on the table; the choice
and every rejection reason are returned for the turn card.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

Cell = Tuple[int, int]
Move = Dict[str, Any]

MAX_MOVES = 21
MAX_STEPS = 5           # drop tile + 5 steps = 6 parcels, the hold cap
PURE = 255
GREEN_PENALTY = 100
UNCONTESTED_MIN_TAIL = 3
LLM_VALUE_TOLERANCE = 0.85   # LLM plan kept if it banks at least this share of the shape plan


def tier_mult(purity: int) -> float:
    if purity >= PURE:
        return 3.0
    if purity >= 151:
        return 1.5
    if purity >= 51:
        return 1.0
    if purity >= 1:
        return 0.75
    return 0.0


def red_value(purity: int) -> float:
    return float(purity) * tier_mult(int(purity))


# ── View readers ──────────────────────────────────────────────────────
def _xy(v: Any) -> Optional[Cell]:
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        try:
            return (int(v[0]), int(v[1]))
        except (TypeError, ValueError):
            return None
    if isinstance(v, Mapping) and "x" in v and "y" in v:
        try:
            return (int(v["x"]), int(v["y"]))
        except (TypeError, ValueError):
            return None
    return None


def my_player(agent_view: Mapping[str, Any]) -> str:
    hud = agent_view.get("hud") or {}
    meta = agent_view.get("meta") or {}
    return str(hud.get("player") or meta.get("player") or "p1")


def world_dims(agent_view: Mapping[str, Any]) -> Tuple[int, int]:
    w = agent_view.get("world") or {}
    g = agent_view.get("grid") or {}
    return int(w.get("width") or g.get("width") or 40), int(w.get("height") or g.get("height") or 28)


def probe_radius(agent_view: Mapping[str, Any]) -> int:
    rules = ((agent_view.get("meta") or {}).get("rules") or {})
    try:
        return int(rules.get("probe_radius") or 4)
    except (TypeError, ValueError):
        return 4


def red_map(agent_view: Mapping[str, Any]) -> Dict[Cell, int]:
    """Every RED cell the seat knows about (fresh or stale) with its purity."""
    out: Dict[Cell, int] = {}
    for t in agent_view.get("red_tiles") or []:
        c = _xy(t)
        if c is None:
            continue
        try:
            p = int(t.get("purity") or 0)
        except (TypeError, ValueError):
            continue
        if p > out.get(c, -1):
            out[c] = p
    # live rows carry the freshest truth; a cell the world says is GREEN now is not red
    for row in ((agent_view.get("world") or {}).get("live") or []):
        c = _xy(row)
        if c is None:
            continue
        tile = str(row.get("tile") or "")
        if tile == "RED":
            try:
                out[c] = max(out.get(c, 0), int(row.get("purity") or 0))
            except (TypeError, ValueError):
                pass
        elif c in out and tile in ("GREEN", "EMPTY", "BLUE"):
            del out[c]
    return out


def blue_map(agent_view: Mapping[str, Any]) -> Dict[Cell, int]:
    out: Dict[Cell, int] = {}
    for t in agent_view.get("blue_tiles") or []:
        c = _xy(t)
        if c is not None:
            try:
                out[c] = int(t.get("purity") or 0)
            except (TypeError, ValueError):
                pass
    return out


def green_cells(agent_view: Mapping[str, Any]) -> Tuple[Set[Cell], Set[Cell]]:
    """(synthetic GREEN, natural GREEN). Both are never stepped on; synthetic is the harder rule."""
    synth: Set[Cell] = set()
    natural: Set[Cell] = set()
    for t in agent_view.get("green_tiles") or []:
        c = _xy(t)
        if c is None:
            continue
        (synth if str(t.get("lineage") or "") == "synthetic" else natural).add(c)
    for row in ((agent_view.get("world") or {}).get("live") or []):
        if str(row.get("tile") or "") != "GREEN":
            continue
        c = _xy(row)
        if c is None:
            continue
        (synth if str(row.get("lineage") or "") == "synthetic" else natural).add(c)
    return synth, natural


def live_cells(agent_view: Mapping[str, Any]) -> Set[Cell]:
    out: Set[Cell] = set()
    for row in ((agent_view.get("world") or {}).get("live") or []):
        c = _xy(row)
        if c is not None:
            out.add(c)
    return out


def my_probe_cells(agent_view: Mapping[str, Any]) -> Set[Cell]:
    out: Set[Cell] = set()
    for e in ((agent_view.get("entities") or {}).get("mine") or []):
        if str(e.get("type") or "") == "probe":
            c = _xy(e.get("pos"))
            if c is not None:
                out.add(c)
    return out


def enemy_probe_cells(agent_view: Mapping[str, Any]) -> Dict[Cell, int]:
    """Enemy probe cell -> recency score (higher = fresher)."""
    me = my_player(agent_view)
    out: Dict[Cell, int] = {}
    for row in ((agent_view.get("world") or {}).get("live") or []):
        ent = row.get("entity")
        if isinstance(ent, Mapping) and str(ent.get("kind") or "") == "probe" and str(ent.get("owner") or "") != me:
            c = _xy(row)
            if c is not None:
                out[c] = max(out.get(c, 0), 3)
    ci = agent_view.get("competitor_intel") or {}
    for r in ci.get("new_this_day") or []:
        if str(r.get("kind") or "") == "enemy_probe_launch":
            c = _xy(r.get("at"))
            if c is not None:
                out[c] = max(out.get(c, 0), 2)
    for r in ci.get("persistent_echoes") or []:
        if str(r.get("kind") or "") == "enemy_probe":
            c = _xy(r.get("at"))
            if c is not None:
                out[c] = max(out.get(c, 0), 1)
    return out


def enemy_harvester_cells(agent_view: Mapping[str, Any]) -> Set[Cell]:
    me = my_player(agent_view)
    out: Set[Cell] = set()
    for row in ((agent_view.get("world") or {}).get("live") or []):
        ent = row.get("entity")
        if isinstance(ent, Mapping) and str(ent.get("kind") or "") == "harvester" and str(ent.get("owner") or "") != me:
            c = _xy(row)
            if c is not None:
                out.add(c)
    ci = agent_view.get("competitor_intel") or {}
    for r in ci.get("new_this_day") or []:
        if str(r.get("kind") or "") == "enemy_harvester_trail":
            c = _xy(r.get("at"))
            if c is not None:
                out.add(c)
    return out


def orbit_harvesters(agent_view: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    for a in agent_view.get("my_assets") or []:
        if isinstance(a, Mapping) and a.get("kind") == "harvester" and a.get("state") == "orbit":
            if isinstance(a.get("id"), str):
                out.append(a["id"])
    if not out:
        for e in ((agent_view.get("entities") or {}).get("mine") or []):
            if str(e.get("type") or "") == "harvester" and e.get("pos") is None and isinstance(e.get("id"), str):
                out.append(e["id"])
    return out


def probe_stock(agent_view: Mapping[str, Any]) -> int:
    try:
        return int(agent_view.get("probe_stock") or (agent_view.get("orbit") or {}).get("probe_stock") or 0)
    except (TypeError, ValueError):
        return 0


def redsigns(agent_view: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in agent_view.get("redsign") or []:
        if not isinstance(r, Mapping):
            continue
        c = r.get("center")
        if isinstance(c, (list, tuple)) and len(c) >= 2:
            try:
                out.append({"center": (int(round(float(c[0]))), int(round(float(c[1])))),
                            "mine": bool(r.get("mine"))})
            except (TypeError, ValueError):
                continue
    return out


def day_info(agent_view: Mapping[str, Any]) -> Tuple[int, int]:
    meta = agent_view.get("meta") or {}
    hud = agent_view.get("hud") or {}
    day = int(meta.get("day") or hud.get("day") or 1)
    cap = int(hud.get("season_day_cap") or 7)
    return day, cap


def weapon_stock(agent_view: Mapping[str, Any]) -> Dict[str, int]:
    ws = (agent_view.get("orbit") or {}).get("weapon_stock") or {}
    return {"emp": int(ws.get("emp", 0) or 0), "chaff": int(ws.get("chaff", 0) or 0)}


# ── Geometry ──────────────────────────────────────────────────────────
def cheb(a: Cell, b: Cell) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def in_disk(a: Cell, b: Cell, r: int) -> bool:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 <= r * r


def disk(c: Cell, r: int, dims: Tuple[int, int]) -> Set[Cell]:
    w, h = dims
    out: Set[Cell] = set()
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            if dx * dx + dy * dy <= r * r:
                x, y = c[0] + dx, c[1] + dy
                if 0 <= x < w and 0 <= y < h:
                    out.add((x, y))
    return out


def neighbours4(c: Cell, dims: Tuple[int, int]) -> List[Cell]:
    w, h = dims
    out = []
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        x, y = c[0] + dx, c[1] + dy
        if 0 <= x < w and 0 <= y < h:
            out.append((x, y))
    return out


def manhattan_path(a: Cell, b: Cell) -> List[Cell]:
    """Cells strictly after ``a`` up to and including ``b``, x first then y."""
    out: List[Cell] = []
    x, y = a
    while x != b[0]:
        x += 1 if b[0] > x else -1
        out.append((x, y))
    while y != b[1]:
        y += 1 if b[1] > y else -1
        out.append((x, y))
    return out


# ── Audit ─────────────────────────────────────────────────────────────
@dataclass
class Audit:
    violations: List[str] = field(default_factory=list)
    value: float = 0.0
    red_cells: int = 0
    green_hits: int = 0
    pures_taken: int = 0
    moves: int = 0
    cells: int = 0          # total ground committed (path cells across the fleet)
    paths: Dict[str, List[Cell]] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return not self.violations

    def summary(self) -> str:
        v = "clean" if self.clean else "; ".join(self.violations)
        return (f"value~{self.value:.0f} red={self.red_cells} pures={self.pures_taken} "
                f"green={self.green_hits} moves={self.moves} [{v}]")


def audit(moves: Sequence[Move], agent_view: Mapping[str, Any]) -> Audit:
    a = Audit(moves=len(moves))
    reds = red_map(agent_view)
    synth, natural = green_cells(agent_view)
    live = set(live_cells(agent_view))
    dims = world_dims(agent_view)
    r = probe_radius(agent_view)
    stock = probe_stock(agent_view)
    units = set(orbit_harvesters(agent_view))

    paths: Dict[str, List[Cell]] = {}
    cur: Dict[str, Cell] = {}
    dropped: Set[str] = set()
    picked: Set[str] = set()
    steps: Dict[str, int] = {}
    visited_all: Set[Cell] = set()
    probes_used = 0
    last_drop_idx = -1
    probe_before_drop = 0
    lighting_needed = False
    pure_cells_taken: Set[Cell] = set()

    if len(moves) > MAX_MOVES:
        a.violations.append(f"{len(moves)} orders exceeds the {MAX_MOVES} slot cap")

    drop_indices = [i for i, m in enumerate(moves) if isinstance(m, Mapping) and str(m.get("a")) == "drop"]
    last_drop_idx = drop_indices[-1] if drop_indices else -1

    for i, m in enumerate(moves):
        if not isinstance(m, Mapping):
            a.violations.append(f"order {i} is not an object")
            continue
        act = str(m.get("a") or "")
        if act == "probe":
            c = _xy(m.get("at"))
            if c is None:
                a.violations.append(f"probe {i} has no cell")
                continue
            probes_used += 1
            if probes_used > stock:
                a.violations.append(f"probe {i} exceeds stock {stock}")
            if i < last_drop_idx:
                probe_before_drop += 1
            live |= disk(c, r, dims)
        elif act == "drop":
            u = str(m.get("unit") or "")
            c = _xy(m.get("at"))
            if c is None:
                a.violations.append(f"drop {i} has no cell")
                continue
            if u in dropped:
                a.violations.append(f"{u} dropped twice (one outing per night)")
                continue
            if units and u not in units:
                a.violations.append(f"{u} is not a harvester in orbit")
            if c not in live:
                a.violations.append(f"{u} drop at {c} has no live vision")
            elif c not in live_cells(agent_view):
                lighting_needed = True
            dropped.add(u)
            cur[u] = c
            paths[u] = [c]
            steps[u] = 0
        elif act == "step":
            u = str(m.get("unit") or "")
            c = _xy(m.get("to"))
            if u not in cur or c is None:
                a.violations.append(f"{u} steps before dropping")
                continue
            if u in picked:
                a.violations.append(f"{u} steps after pickup")
                continue
            if abs(c[0] - cur[u][0]) + abs(c[1] - cur[u][1]) != 1:
                a.violations.append(f"{u} step {cur[u]}->{c} is not adjacent")
            steps[u] = steps.get(u, 0) + 1
            if steps[u] > MAX_STEPS:
                a.violations.append(f"{u} exceeds {MAX_STEPS} steps")
            cur[u] = c
            paths[u].append(c)
        elif act == "pickup":
            u = str(m.get("unit") or "")
            if u not in cur:
                a.violations.append(f"{u} pickup without drop")
            picked.add(u)
        elif act in ("wait", "emp_launch", "chaff_flare"):
            continue
        else:
            a.violations.append(f"unknown order '{act}' burns a slot")

    for u in dropped:
        if u not in picked:
            a.violations.append(f"{u} never picked up (destroyed at dawn)")

    # shape rules
    seen: Set[Cell] = set()
    pures_all = [c for c, p in reds.items() if p >= PURE]
    lone_unit = len(units) == 1 and len(pures_all) == 1
    tempo_night = len(pures_all) >= 2      # two pures in view: land and lift, no detours
    enemy_p = enemy_probe_cells(agent_view)
    rival_sign_centres = [g["center"] for g in redsigns(agent_view) if not g["mine"]]
    known = set(reds) | live_cells(agent_view) | set(blue_map(agent_view))
    # A cell inside a disk this plan lights is still UNKNOWN at planning time: the
    # probe reveals terrain when it lands, not when the queue is written. So the
    # blind-step rule cannot credit our own probes; only drop legality can.
    rival_presence = set(enemy_p) | enemy_harvester_cells(agent_view)

    def _away_from_rivals(c: Cell) -> int:
        if not rival_presence:
            return 0
        return min(cheb(c, rp) for rp in rival_presence)
    for u in sorted(paths):
        p = paths[u]
        touches_pure = [c for c in p if reds.get(c, 0) >= PURE]
        if touches_pure and reds.get(p[0], 0) < PURE:
            a.violations.append(f"{u} walks to a pure instead of landing on it")
        if touches_pure:
            last_pure_idx = max(i for i, c in enumerate(p) if reds.get(c, 0) >= PURE)
            tail = len(p) - 1 - last_pure_idx
            watched = any(in_disk(e, p[0], r) for e in enemy_p)
            if (tempo_night or watched) and tail > 0 and not lone_unit:
                a.violations.append(f"{u} detours {tail} step(s) past the pure on a contested night (land and lift)")
            if lone_unit and tail < UNCONTESTED_MIN_TAIL and not tempo_night:
                a.violations.append(f"{u} stops {tail} step(s) past the pure while alone on a graded halo (push at least {UNCONTESTED_MIN_TAIL})")
        for c in p:
            if c in synth:
                a.violations.append(f"{u} steps on rival-made GREEN {c}")
                a.green_hits += 1
            elif c in natural:
                a.green_hits += 1
        # Inside a rival's seam their GREEN is invisible to us and a step onto ground
        # they stripped costs 100 for nothing. One blind step is a fair gamble; two is
        # not, and the gamble must lead AWAY from where they have been working.
        if rival_sign_centres:
            blind = [(i, c) for i, c in enumerate(p[1:], start=1)
                     if c not in known and any(cheb(c, g) <= 3 for g in rival_sign_centres)]
            if len(blind) > 1:
                a.violations.append(f"{u} takes {len(blind)} blind steps inside a rival's seam (one is a gamble, two is careless)")
            elif len(blind) == 1:
                i, c = blind[0]
                on_centre = any(p[0] == g for g in rival_sign_centres)
                if not on_centre:
                    a.violations.append(
                        f"{u} gambles a blind step to {c} without standing on the sign centre "
                        "(only the unit on the centre has the graded halo in its favour)")
                elif rival_presence and _away_from_rivals(c) < _away_from_rivals(p[i - 1]):
                    a.violations.append(f"{u} steps blind toward the ground the rival has been working ({c})")
        for c in p:
            if c in seen:
                a.violations.append(f"{u} re-enters {c} already in another unit's path")
            if reds.get(c, 0) >= PURE:
                if c in pure_cells_taken:
                    a.violations.append(f"pure {c} taken twice")
                pure_cells_taken.add(c)
        seen |= set(p)
    if probe_before_drop > (1 if lighting_needed else 0):
        a.violations.append(f"{probe_before_drop} probes queued before the last drop (launches are public)")
    if units and len(dropped) < len(units):
        a.violations.append(f"only {len(dropped)} of {len(units)} harvesters deployed")

    # fog-honest value
    for c in seen:
        p = reds.get(c, 0)
        if p > 0:
            a.value += red_value(p)
            a.red_cells += 1
    a.value -= GREEN_PENALTY * a.green_hits
    a.pures_taken = len(pure_cells_taken)
    a.cells = sum(len(p) for p in paths.values())
    a.paths = paths
    return a


# ── Shape planner ─────────────────────────────────────────────────────
@dataclass
class Situation:
    reds: Dict[Cell, int]
    pures: List[Cell]
    synth: Set[Cell]
    natural: Set[Cell]
    live: Set[Cell]
    my_probes: Set[Cell]
    enemy_probes: Dict[Cell, int]
    enemy_harvesters: Set[Cell]
    units: List[str]
    stock: int
    signs: List[Dict[str, Any]]
    dims: Tuple[int, int]
    r: int
    day: int
    day_cap: int

    def contested(self, c: Cell) -> bool:
        """A rival HARVESTER can reach the cell tonight. Probes only watch; they cannot take it."""
        for h in self.enemy_harvesters:
            if abs(h[0] - c[0]) + abs(h[1] - c[1]) <= MAX_STEPS + 1:
                return True
        return False

    def watched(self, c: Cell) -> bool:
        return any(in_disk(e, c, self.r) for e in self.enemy_probes)


def situation(agent_view: Mapping[str, Any]) -> Situation:
    reds = red_map(agent_view)
    synth, natural = green_cells(agent_view)
    day, cap = day_info(agent_view)
    return Situation(
        reds=reds,
        pures=sorted([c for c, p in reds.items() if p >= PURE]),
        synth=synth, natural=natural,
        live=live_cells(agent_view),
        my_probes=my_probe_cells(agent_view),
        enemy_probes=enemy_probe_cells(agent_view),
        enemy_harvesters=enemy_harvester_cells(agent_view),
        units=orbit_harvesters(agent_view),
        stock=probe_stock(agent_view),
        signs=redsigns(agent_view),
        dims=world_dims(agent_view),
        r=probe_radius(agent_view),
        day=day, day_cap=cap,
    )


def _forbidden(s: Situation, taken: Set[Cell]) -> Set[Cell]:
    return s.synth | s.natural | taken


def _greedy_chain(s: Situation, start: Cell, taken: Set[Cell], *, max_steps: int,
                  min_steps: int = 0, prefer_pures: bool = True,
                  toward: Optional[Cell] = None) -> List[Cell]:
    """Walk from ``start`` over the richest unvisited RED neighbours.

    Stops when no RED neighbour remains unless ``min_steps`` is still owed, in
    which case it pushes into unvisited non-green cells (fog counts) away from
    the wake. Never enters a forbidden or already-taken cell.
    """
    path = [start]
    forb = _forbidden(s, taken)
    cur = start
    while len(path) - 1 < max_steps:
        cands = [n for n in neighbours4(cur, s.dims) if n not in forb and n not in path]
        red_c = [n for n in cands if s.reds.get(n, 0) > 0]
        if red_c:
            if prefer_pures and any(s.reds[n] >= PURE for n in red_c):
                red_c = [n for n in red_c if s.reds[n] >= PURE]
            if toward is not None:
                red_c.sort(key=lambda n: (cheb(n, toward), -s.reds[n]))
            else:
                red_c.sort(key=lambda n: -s.reds[n])
            nxt = red_c[0]
        elif len(path) - 1 < min_steps and cands:
            # owed steps: keep heading away from where we came from, into unknown ground
            prev = path[-2] if len(path) > 1 else None
            if toward is not None:
                cands.sort(key=lambda n: cheb(n, toward))
            elif prev is not None:
                cands.sort(key=lambda n: -(abs(n[0] - prev[0]) + abs(n[1] - prev[1])))
            nxt = cands[0]
        else:
            break
        path.append(nxt)
        cur = nxt
    return path


def _lighting_probe_for(s: Situation, target: Cell, avoid: Set[Cell]) -> Optional[Cell]:
    """Best probe cell that lights ``target`` without sitting on it or on our own probe."""
    best: Optional[Cell] = None
    best_key = None
    for c in disk(target, s.r, s.dims):
        if c == target or c in s.my_probes or c in avoid or c in s.synth:
            continue
        # prefer covering the most known red around the target, then closeness
        cover = sum(1 for rc in s.reds if in_disk(c, rc, s.r))
        key = (-cover, cheb(c, target))
        if best_key is None or key < best_key:
            best, best_key = c, key
    return best


def _finder_probe(s: Situation, centre: Cell) -> Optional[Cell]:
    """The rival probe closest to a sign centre: superseding it blinds their eye."""
    if not s.enemy_probes:
        return None
    ranked = sorted(s.enemy_probes.items(), key=lambda kv: (cheb(kv[0], centre), -kv[1]))
    c, _ = ranked[0]
    return c if cheb(c, centre) <= s.r + 2 else None


def _drop_cells(s: Situation, live: Set[Cell], taken: Set[Cell]) -> List[Cell]:
    forb = _forbidden(s, taken)
    return sorted([c for c in s.reds if c in live and c not in forb], key=lambda c: -s.reds[c])


def _best_chain(s: Situation, start: Cell, taken: Set[Cell], *, max_steps: int,
                toward: Optional[Cell] = None, stop_after_pure: bool = False,
                min_len: int = 0) -> Tuple[List[Cell], float]:
    """Bounded depth-first search for the richest self-avoiding walk from ``start``.

    Value = purity x tier over RED cells (first visit), plus a small bonus per
    step toward ``toward`` (comb warmer). ``stop_after_pure`` returns the walk
    the moment it has landed on a pure and the next best cell is not a pure.
    ``min_len`` forces the walk to keep going (into unknown ground if needed)
    so a lone unit on a free seam does not stop short.
    """
    forb = _forbidden(s, taken)
    best_path = [start]
    best_val = red_value(s.reds.get(start, 0))

    def rec(path: List[Cell], val: float) -> None:
        nonlocal best_path, best_val
        cur = path[-1]
        n_steps = len(path) - 1
        if (val, -n_steps) > (best_val, -(len(best_path) - 1)) and n_steps >= min_len:
            best_path, best_val = list(path), val
        if n_steps >= max_steps:
            return
        cands = [n for n in neighbours4(cur, s.dims) if n not in forb and n not in path and n not in s.my_probes]
        if stop_after_pure and s.reds.get(cur, 0) >= PURE:
            cands = [n for n in cands if s.reds.get(n, 0) >= PURE]
        scored = []
        for n in cands:
            p = s.reds.get(n, 0)
            v = red_value(p) + (1.0 if p > 0 else 0.0)
            if toward is not None:
                v += 40.0 * (cheb(cur, toward) - cheb(n, toward))
            if p == 0:
                known_empty = n in s.live
                owed = n_steps + 1 <= min_len
                if not (known_empty or owed or v > 0):
                    continue  # never wander into fog unless steps are owed or it gets warmer
            scored.append((v, n))
        scored.sort(reverse=True)
        for v, n in scored[:4]:
            rec(path + [n], val + max(v, 0.0))

    rec([start], best_val)
    return best_path, best_val


def _warm_centre_landing(s: Situation, centre: Cell, live: Set[Cell], taken: Set[Cell]) -> Optional[Cell]:
    """On a sign we cannot see into, the landing is the centre itself if lit, else the lit cell nearest it."""
    forb = _forbidden(s, taken)
    cands = [c for c in live if c not in forb]
    if not cands:
        return None
    return min(cands, key=lambda c: (cheb(c, centre), abs(c[0] - centre[0]) + abs(c[1] - centre[1])))


def shape_plan(agent_view: Mapping[str, Any]) -> Tuple[List[Move], List[str]]:
    """A complete legal queue built from doctrine alone, plus the reasoning."""
    s = situation(agent_view)
    log: List[str] = []
    moves: List[Move] = []
    taken: Set[Cell] = set()
    live = set(s.live)
    stock = s.stock
    units = list(s.units)
    if not units:
        log.append("no harvester in orbit")
    pre_probes: List[Cell] = []
    post_probes: List[Cell] = []
    final_night = s.day >= s.day_cap

    rival_signs = [g for g in s.signs if not g["mine"]]
    own_signs = [g for g in s.signs if g["mine"]]
    focus: Optional[Cell] = None
    if s.pures:
        focus = s.pures[0]
    elif s.signs:
        focus = s.signs[0]["center"]

    def light(target: Cell, *, offset_ok: bool = True) -> bool:
        nonlocal stock, live
        if stock <= 0:
            return False
        c = _lighting_probe_for(s, target, set(pre_probes)) if offset_ok else target
        if c is None:
            return False
        pre_probes.append(c)
        live |= disk(c, s.r, s.dims)
        stock -= 1
        return True

    assignments: List[Tuple[str, List[Cell]]] = []

    # 1. Sign we cannot see into (no red known): blind comb toward the centre.
    #    On a RIVAL sign, supersede their finder first: it blinds them and lights us.
    if s.signs and not s.reds and units:
        g = s.signs[0]
        centre = g["center"]
        if not g["mine"]:
            f = _finder_probe(s, centre)
            if f is not None and stock > 0:
                pre_probes.append(f)
                live |= disk(f, s.r, s.dims)
                stock -= 1
                log.append(f"rival sign at {centre}: supersede their finder probe {f} first")
        if centre not in live:
            # light the centre from one cell off it so the landing itself stays clear
            light(centre)
            log.append(f"sign at {centre} is dark: one probe beside the centre lights the landing")
        # Land as close to the centre as vision allows, then comb warmer. Prefer
        # landing ONE cell out and stepping onto the centre: inside a rival's seam
        # a blind step is only defensible when it gets warmer, and a comb is a walk.
        for u in list(units)[:2]:
            land = _warm_centre_landing(s, centre, live, taken)
            if land is None:
                break
            path = [land]
            # exactly one blind step, and only for the unit standing ON the centre:
            # the halo around a find is graded, so its neighbour is likelier mass than
            # ground the rival already stripped. Taken away from their own workings.
            rivals = set(s.enemy_probes) | s.enemy_harvesters
            cands = ([n for n in neighbours4(land, s.dims) if n not in _forbidden(s, taken)]
                     if land == centre else [])
            if cands:
                cands.sort(key=lambda n: (
                    cheb(n, centre),
                    -(min(cheb(n, rp) for rp in rivals) if rivals else 0),
                    -s.reds.get(n, 0), n[1], n[0],
                ))
                path.append(cands[0])
            taken |= set(path)
            assignments.append((u, path))
            units.remove(u)
            log.append(f"{u}: blind comb {path[0]} -> {path[-1]} warmer toward the sign centre {centre}")

    # 2. Pures: one unit each, landing ON the pure.
    pures_left = sorted(s.pures, key=lambda c: -sum(s.reds.get(n, 0) for n in neighbours4(c, s.dims)))
    lone_unit = len(units) == 1 and len(s.pures) == 1
    for u in list(units):
        if not pures_left:
            break
        p = pures_left[0]
        if p not in live:
            if not light(p):
                log.append(f"pure {p} is dark and no probe can light it")
                break
        pures_left.pop(0)
        contested = s.contested(p)
        if lone_unit:
            # The pure is banked on landing whoever is watching; the only question is the tail.
            # A lone unit with a fresh hold on a graded halo takes the halo.
            path, val = _best_chain(s, p, taken, max_steps=MAX_STEPS, min_len=UNCONTESTED_MIN_TAIL)
            log.append(f"{u}: alone on pure {p}: take it and push {len(path) - 1} steps into the halo (~{val:.0f})")
        else:
            path, val = _best_chain(s, p, taken, max_steps=MAX_STEPS, stop_after_pure=True)
            why = "rivals can see it" if contested else "the ring goes to the other unit"
            log.append(f"{u}: land ON pure {p}, {why}, lift with no tail (~{val:.0f})")
        taken |= set(path)
        assignments.append((u, path))
        units.remove(u)

    # 3. Remaining units: richest chain from any lit RED, near the focus if there is one.
    for u in list(units):
        cands = _drop_cells(s, live, taken)
        if not cands and stock > 0 and s.reds:
            target = max((c for c in s.reds if c not in taken), key=lambda c: s.reds[c], default=None)
            if target is not None and light(target):
                cands = _drop_cells(s, live, taken)
        if not cands:
            log.append(f"{u}: no landable RED, stays in orbit")
            continue
        toward = rival_signs[0]["center"] if (rival_signs and not s.pures) else None
        best_path: List[Cell] = []
        best_val = -1.0
        for start in cands[:8]:
            path, val = _best_chain(s, start, taken, max_steps=MAX_STEPS, toward=toward)
            if focus is not None and cheb(start, focus) <= 3:
                val += 25.0
            if val > best_val:
                best_val, best_path = val, path
        if not best_path:
            best_path = [cands[0]]
        taken |= set(best_path)
        assignments.append((u, best_path))
        units.remove(u)
        log.append(f"{u}: chain of {len(best_path)} cells from {best_path[0]} (~{best_val:.0f} pts)")

    # 4. Emit: lighting probes, then all drops/walks, then denial and frontier probes.
    for c in pre_probes:
        moves.append({"a": "probe", "at": [c[0], c[1]]})
    for u, path in assignments:
        moves.append({"a": "drop", "unit": u, "at": [path[0][0], path[0][1]]})
        cur = path[0]
        for c in path[1:]:
            for step in manhattan_path(cur, c):
                moves.append({"a": "step", "unit": u, "to": [step[0], step[1]]})
                cur = step
        moves.append({"a": "pickup", "unit": u})

    # denial: rival probes watching our pures or our sign, freshest first
    interest = list(s.pures) + [g["center"] for g in own_signs]
    for e, rec in sorted(s.enemy_probes.items(), key=lambda kv: -kv[1]):
        if stock <= 0:
            break
        if e in pre_probes:
            continue
        if any(in_disk(e, c, s.r) for c in interest):
            post_probes.append(e)
            stock -= 1
            log.append(f"deny: supersede rival probe {e} watching our ground")
    # frontier: one probe on the edge of what we know, unless it is the final night
    if stock > 0 and not final_night and (focus is not None or s.reds):
        anchor = focus or max(s.reds, key=lambda c: s.reds[c])
        best: Optional[Cell] = None
        best_key = None
        for c in disk(anchor, s.r + 3, s.dims):
            if c in live or c in s.my_probes or c in pre_probes or c in post_probes or c in s.synth:
                continue
            unseen = sum(1 for d in disk(c, s.r, s.dims) if d not in live)
            key = (-unseen, cheb(c, anchor))
            if best_key is None or key < best_key:
                best, best_key = c, key
        if best is not None:
            post_probes.append(best)
            stock -= 1
            log.append(f"frontier probe {best} after the fleet is down")
    for c in post_probes:
        moves.append({"a": "probe", "at": [c[0], c[1]]})

    return moves[:MAX_MOVES], log


def emergency_plan(agent_view: Mapping[str, Any]) -> List[Move]:
    """Used by the harness shield: whatever the shape planner can build, else nothing."""
    moves, _ = shape_plan(agent_view)
    return moves


def repair(moves: Sequence[Move], agent_view: Mapping[str, Any]) -> Tuple[List[Move], List[str]]:
    """Fix a plan's SHAPE while keeping the model's target selection.

    Replacing a plan throws away the model's judgement about WHERE to work, which
    in a real season is most of the value: the board is mostly fog and the model
    is the half of the system that decides what to explore. So the first response
    to a violation is surgery, not substitution:

    * a unit that walks to a pure is re-rooted to land ON it;
    * a detour past a pure on a contested night is trimmed;
    * a walk that exceeds the outing cap, re-enters a partner's wake, steps on
      GREEN or wanders unlit through a rival's seam is truncated at that cell;
    * probes queued before the drops are moved after the last pickup, except the
      one probe that lights a landing;
    * every dropped unit gets a pickup.
    """
    s = situation(agent_view)
    log: List[str] = []
    reds = s.reds
    synth, natural = s.synth, s.natural
    live0 = set(s.live)
    known = set(reds) | live0 | set(blue_map(agent_view))
    rival_presence = set(s.enemy_probes) | s.enemy_harvesters
    pures_all = [c for c, p in reds.items() if p >= PURE]
    lone_unit = len(s.units) == 1 and len(pures_all) == 1
    tempo_night = len(pures_all) >= 2
    rival_centres = [g["center"] for g in s.signs if not g["mine"]]

    probes: List[Cell] = []
    order: List[str] = []          # unit ids in drop order
    paths: Dict[str, List[Cell]] = {}
    for m in moves:
        if not isinstance(m, Mapping):
            continue
        act = str(m.get("a") or "")
        if act == "probe":
            c = _xy(m.get("at"))
            if c is not None and c not in probes:
                probes.append(c)
        elif act == "drop":
            u, c = str(m.get("unit") or ""), _xy(m.get("at"))
            if u and c is not None and u not in paths:
                paths[u] = [c]
                order.append(u)
        elif act == "step":
            u, c = str(m.get("unit") or ""), _xy(m.get("to"))
            if u in paths and c is not None:
                paths[u].append(c)

    # vision after every probe in the plan is spent
    live = set(live0)
    for c in probes:
        live |= disk(c, s.r, s.dims)

    # 1. re-root onto a pure the unit was walking to
    for u in order:
        p = paths[u]
        idx = [i for i, c in enumerate(p) if reds.get(c, 0) >= PURE]
        if idx and idx[0] > 0:
            first = idx[0]
            if p[first] in live:
                paths[u] = [p[first]] + p[first + 1:]
                log.append(f"repair: {u} re-rooted to land ON the pure {p[first]}")

    # 2. trim tails and truncate at the first illegal cell
    taken: Set[Cell] = set()
    for u in order:
        p = paths[u]
        out = [p[0]]
        for c in p[1:]:
            if len(out) - 1 >= MAX_STEPS:
                log.append(f"repair: {u} truncated at the {MAX_STEPS}-step outing cap")
                break
            if c in synth or c in natural:
                log.append(f"repair: {u} truncated before GREEN {c}")
                break
            if c in taken:
                log.append(f"repair: {u} truncated before {c}, already in another unit's path")
                break
            if rival_centres and c not in known and any(cheb(c, g) <= 3 for g in rival_centres):
                prev = out[-1]
                spent_blind = sum(1 for x in out[1:]
                                  if x not in known and any(cheb(x, g) <= 3 for g in rival_centres))
                closer = (rival_presence
                          and min((cheb(c, rp) for rp in rival_presence), default=0)
                          < min((cheb(prev, rp) for rp in rival_presence), default=0))
                on_centre = any(out[0] == g for g in rival_centres)
                if spent_blind >= 1 or closer or not on_centre:
                    why = ("a second blind step" if spent_blind >= 1
                           else "toward the ground they have been working" if closer
                           else "the unit is not standing on the sign centre")
                    log.append(f"repair: {u} truncated before blind {c} inside a rival's seam ({why})")
                    break
            out.append(c)
        idx = [i for i, c in enumerate(out) if reds.get(c, 0) >= PURE]
        if idx:
            last = idx[-1]
            watched = any(in_disk(e, out[0], s.r) for e in s.enemy_probes)
            if (tempo_night or watched) and not lone_unit and last < len(out) - 1:
                log.append(f"repair: {u} tail trimmed after the pure (contested night)")
                out = out[: last + 1]
            elif lone_unit and not tempo_night and len(out) - 1 - last < UNCONTESTED_MIN_TAIL:
                ext, _ = _best_chain(s, out[-1], taken | set(out), max_steps=MAX_STEPS - (len(out) - 1),
                                     min_len=UNCONTESTED_MIN_TAIL - (len(out) - 1 - last))
                if len(ext) > 1:
                    out = out + ext[1:]
                    log.append(f"repair: {u} extended {len(ext) - 1} step(s) into the halo (alone, nothing to punish it)")
        paths[u] = out
        taken |= set(out)

    # 3. probes: keep only what lights a landing before the drops
    lighting: List[Cell] = []
    for c in probes:
        d = disk(c, s.r, s.dims)
        if any(paths[u][0] not in live0 and paths[u][0] in d for u in order):
            lighting.append(c)
    rest = [c for c in probes if c not in lighting]
    if len(lighting) > 1:
        lighting = lighting[:1]
        log.append("repair: kept one lighting probe before the drops")

    out_moves: List[Move] = []
    for c in lighting:
        out_moves.append({"a": "probe", "at": [c[0], c[1]]})
    for u in order:
        p = paths[u]
        out_moves.append({"a": "drop", "unit": u, "at": [p[0][0], p[0][1]]})
        cur = p[0]
        for c in p[1:]:
            for step in manhattan_path(cur, c):
                out_moves.append({"a": "step", "unit": u, "to": [step[0], step[1]]})
                cur = step
        out_moves.append({"a": "pickup", "unit": u})
    if rest:
        log.append(f"repair: {len(rest)} probe(s) moved after the fleet is down (launches are public)")
    for c in rest:
        out_moves.append({"a": "probe", "at": [c[0], c[1]]})
    return out_moves[:MAX_MOVES], log


def final_gate(moves: Sequence[Move], agent_view: Mapping[str, Any]) -> Tuple[List[Move], List[str]]:
    """Last check before submit, AFTER ordnance has been injected.

    The tactical layer adds a salvo at the head and a flare after the last pickup
    once the verifier has already signed the harvest shape, so the signed queue is
    not the submitted queue. This gate re-checks only what that injection can
    break, and it trims ordnance and trailing probes rather than a walk, because
    a harvest chain that reaches the engine is worth more than a charge.
    """
    out = [dict(m) for m in moves if isinstance(m, Mapping)]
    log: List[str] = []
    rack = weapon_stock(agent_view)

    # one charge of each per night, and never more than the rack holds
    seen = {"emp_launch": 0, "chaff_flare": 0}
    keep: List[Move] = []
    for m in out:
        act = str(m.get("a") or "")
        if act in seen:
            allowed = rack.get("emp" if act == "emp_launch" else "chaff", 0)
            if seen[act] >= min(1, allowed):
                log.append(f"final gate: dropped a duplicate or unfunded {act}")
                continue
            seen[act] += 1
        keep.append(m)
    out = keep

    # a flare jams us too: it must come after the last pickup
    last_pick = max((i for i, m in enumerate(out) if str(m.get("a")) == "pickup"), default=-1)
    flares = [i for i, m in enumerate(out) if str(m.get("a")) == "chaff_flare"]
    if flares and last_pick >= 0 and flares[0] < last_pick:
        f = out.pop(flares[0])
        last_pick = max((i for i, m in enumerate(out) if str(m.get("a")) == "pickup"), default=-1)
        out.insert(last_pick + 1, f)
        log.append("final gate: moved the flare after the last pickup (it jams our own house)")

    # a salvo is worth its slot at hour 1 or 2 and little after
    for i, m in enumerate(list(out)):
        if str(m.get("a")) == "emp_launch" and i > 1:
            out.insert(0, out.pop(i))
            log.append("final gate: hoisted the salvo to hour 1")
            break

    # slot cap: shed ordnance first, then trailing probes, never a walk
    if len(out) > MAX_MOVES:
        for act in ("chaff_flare", "emp_launch"):
            while len(out) > MAX_MOVES:
                idx = next((i for i, m in enumerate(out) if str(m.get("a")) == act), None)
                if idx is None:
                    break
                out.pop(idx)
                log.append(f"final gate: dropped {act} to stay inside the {MAX_MOVES}-slot cap")
        while len(out) > MAX_MOVES:
            idx = next((i for i in range(len(out) - 1, -1, -1) if str(out[i].get("a")) == "probe"), None)
            if idx is None:
                break
            out.pop(idx)
            log.append(f"final gate: dropped a trailing probe to stay inside the {MAX_MOVES}-slot cap")
        if len(out) > MAX_MOVES:
            log.append(f"final gate: hard-truncated to {MAX_MOVES} orders")
            out = out[:MAX_MOVES]

    # probe stock is real money: never queue more probes than we own
    stock = probe_stock(agent_view)
    probes = [i for i, m in enumerate(out) if str(m.get("a")) == "probe"]
    if len(probes) > stock:
        for i in reversed(probes[stock:]):
            out.pop(i)
        log.append(f"final gate: dropped {len(probes) - stock} probe(s) beyond the stock of {stock}")
    return out, log


# ── Choice ────────────────────────────────────────────────────────────
@dataclass
class Choice:
    moves: List[Move]
    source: str            # "llm" | "llm_repaired" | "shape"
    llm_audit: Optional[Audit]
    shape_audit: Audit
    reasons: List[str]
    shape_log: List[str]


def choose(llm_moves: Sequence[Move], agent_view: Mapping[str, Any]) -> Choice:
    """Keep the model's plan wherever it can be made legal; fall back to doctrine."""
    reasons: List[str] = []
    la = audit(list(llm_moves), agent_view) if llm_moves else None

    if la is not None and la.clean:
        shape_moves, shape_log = shape_plan(agent_view)
        sa = audit(shape_moves, agent_view)
        # A clean plan stands unless the shape plan banks materially more KNOWN red.
        better_value = la.value >= LLM_VALUE_TOLERANCE * sa.value or la.red_cells >= sa.red_cells
        # With nothing known on the ground (a blind sign) value cannot separate the
        # plans, so the tie-break is how much ground each actually commits: a unit
        # that lands and never walks has combed nothing.
        enough_ground = la.cells >= sa.cells
        if (sa.value <= 0 and enough_ground) or (sa.value > 0 and better_value and enough_ground):
            reasons.append(f"LLM plan clean (value {la.value:.0f} vs shape {sa.value:.0f}, ground {la.cells} vs {sa.cells})")
            return Choice(list(llm_moves), "llm", la, sa, reasons, shape_log)
        reasons.append(f"LLM plan clean but weaker than doctrine (value {la.value:.0f} vs {sa.value:.0f}, ground {la.cells} vs {sa.cells})")
        reasons.append("shape plan submitted")
        return Choice(list(shape_moves), "shape", la, sa, reasons, shape_log)

    if la is not None:
        # Surgery first: keep WHERE the model chose to work, fix the shape.
        rep_moves, rep_log = repair(llm_moves, agent_view)
        ra = audit(rep_moves, agent_view)
        if ra.clean:
            shape_moves, shape_log = shape_plan(agent_view)
            sa = audit(shape_moves, agent_view)
            better_value = ra.value >= LLM_VALUE_TOLERANCE * sa.value or ra.red_cells >= sa.red_cells
            enough_ground = ra.cells >= sa.cells
            if (sa.value <= 0 and enough_ground) or (sa.value > 0 and better_value and enough_ground):
                reasons.append("LLM plan violated: " + "; ".join(la.violations[:3]))
                reasons.append("repaired in place: " + "; ".join(rep_log[:4]))
                return Choice(rep_moves, "llm_repaired", la, ra, reasons, rep_log)
            reasons.append(f"repaired plan clean but weaker than doctrine (value {ra.value:.0f} vs {sa.value:.0f}, ground {ra.cells} vs {sa.cells})")
            reasons.append("shape plan submitted")
            return Choice(list(shape_moves), "shape", la, sa, reasons, shape_log)
        reasons.append("repair could not make it clean: " + "; ".join(ra.violations[:3]))

    shape_moves, shape_log = shape_plan(agent_view)
    sa = audit(shape_moves, agent_view)
    if la is None:
        reasons.append("LLM produced no orders")
    if sa.clean:
        reasons.append("shape plan clean; submitted")
        return Choice(list(shape_moves), "shape", la, sa, reasons, shape_log)
    if la is not None and (len(la.violations), -la.value) <= (len(sa.violations), -sa.value):
        reasons.append("neither plan clean; LLM plan has fewer violations")
        return Choice(list(llm_moves), "llm", la, sa, reasons, shape_log)
    reasons.append("neither plan clean; shape plan has fewer violations")
    return Choice(list(shape_moves), "shape", la, sa, reasons, shape_log)
