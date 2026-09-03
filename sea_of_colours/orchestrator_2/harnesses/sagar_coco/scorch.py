"""EMP doctrine — everything this fork knows about firing its own rack.

V12 buys weapons and never fires them. Not because the model is unwilling
but because four separate things had to be true before a salvo could
leave the rack, and none of them were (``soc weapons`` walks the ladder).
This module is the fork's answer to rungs 1-3: the geometry, the target
picking, the prose that goes on the menu line, and the guard that stops
the seat walking into its own cloud. Rung 4 — *when* to reach for it —
lives in :mod:`doctrine`, because that is judgement rather than geometry.

**What a salvo actually is** (RULEBOOK §4.9.3). One charge fires up to
:data:`MISSILES` missiles at once. Each spawns a cloud over a Manhattan
diamond of radius :data:`RADIUS` — 13 cells at r=2 — lasting
:data:`CLOUD_HOURS` hours. Anything the cloud touches:

* **probes are destroyed**, at formation and on every later tick, so a
  cloud is a standing minefield for the rest of the night;
* **harvesters are disabled** — the hour becomes a no-op and the slot is
  still spent;
* **friendly fire is on.** The launcher's own units are not immune, and
  this is the trap the fork spends most of its code avoiding.

**And it costs an hour.** A launch is one of the seat's 21 slots
(§3.10), pre-empted ahead of regular dispatch, so the seat does nothing
else that hour. That is the real price of a scorch, and it is why the
menu line quotes it: the BLUE was paid in orbit and is sunk, but the hour
is being spent right now against a harvester chain that would have banked
RED with it.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from sea_of_colours.game.weapons import (
    EMP_CLOUD_HOURS as _ENGINE_CLOUD_HOURS,
    EMP_MISSILES_PER_LAUNCH as _ENGINE_MISSILES,
    EMP_RADIUS as _ENGINE_RADIUS,
)

#: Engine defaults, used only when a stripped test view carries no
#: ``orbit.weapon_specs``. Live code should go through :func:`specs` so a
#: retune of the engine dials reaches the agent without a prompt edit.
RADIUS = int(_ENGINE_RADIUS)
MISSILES = int(_ENGINE_MISSILES)
CLOUD_HOURS = int(_ENGINE_CLOUD_HOURS)

Cell = Tuple[int, int]


def _cell(at: Any) -> Optional[Cell]:
    if isinstance(at, Mapping):
        at = (at.get("x"), at.get("y"))
    if not isinstance(at, (list, tuple)) or len(at) < 2:
        return None
    try:
        return (int(at[0]), int(at[1]))
    except (TypeError, ValueError):
        return None


def specs(agent_view: Mapping[str, Any]) -> Tuple[int, int, int]:
    """``(radius, missiles, cloud_hours)`` for this game.

    Read off the view rather than the constants above so a retune of
    ``game/weapons.py`` reaches the agent's arithmetic and its menu prose
    on the next turn, with no edit here.
    """
    spec = (
        ((agent_view.get("orbit") or {}).get("weapon_specs") or {}).get("emp")
        or {}
    )
    return (
        int(spec.get("radius", RADIUS) or RADIUS),
        int(spec.get("missiles_per_launch", MISSILES) or MISSILES),
        int(spec.get("cloud_hours", CLOUD_HOURS) or CLOUD_HOURS),
    )


def stock(agent_view: Mapping[str, Any]) -> Dict[str, int]:
    """The seat's own rack. Rung 1: the night phase has to be able to see this."""
    ws = (agent_view.get("orbit") or {}).get("weapon_stock") or {}
    return {
        "emp": int(ws.get("emp", 0) or 0),
        "chaff": int(ws.get("chaff", 0) or 0),
    }


def diamond(centre: Cell, radius: int, bounds: Optional[Tuple[int, int]] = None) -> List[Cell]:
    """Every cell one missile covers — Manhattan diamond, clipped to the grid."""
    cx, cy = centre
    out: List[Cell] = []
    for dx in range(-radius, radius + 1):
        span = radius - abs(dx)
        for dy in range(-span, span + 1):
            x, y = cx + dx, cy + dy
            if x < 0 or y < 0:
                continue
            if bounds is not None and (x >= bounds[0] or y >= bounds[1]):
                continue
            out.append((x, y))
    return out


def blast(
    targets: Iterable[Cell], radius: int,
    bounds: Optional[Tuple[int, int]] = None,
) -> set:
    """The union of every missile's diamond — the cells that go dark."""
    out: set = set()
    for t in targets:
        out.update(diamond(t, radius, bounds))
    return out


def _bounds(agent_view: Mapping[str, Any]) -> Optional[Tuple[int, int]]:
    meta = agent_view.get("meta") or {}
    hud = agent_view.get("hud") or {}
    w = meta.get("width") or hud.get("width")
    h = meta.get("height") or hud.get("height")
    try:
        return (int(w), int(h))
    except (TypeError, ValueError):
        return None


# ── target picking ─────────────────────────────────────────────────────


def probe_targets(
    agent_view: Mapping[str, Any],
    enemy_probes: Sequence[Mapping[str, Any]],
    *,
    missiles: int,
) -> Tuple[List[Cell], List[str]]:
    """Rival probes to scorch, freshest first, plus leftover spread.

    Recency is the ranking because a probe's value to its owner is the
    vision it is about to convert. Yesterday's probe has already told
    them what is there; tonight's is the one still earning, and killing it
    costs them both the eye and the build.

    A charge fires ``missiles`` missiles whether or not there are that
    many probes, and a wasted missile is worse than a speculative one, so
    leftovers spread to cells adjacent to the last real target. Adjacent
    rather than anywhere: it widens the standing cloud around a place the
    rival has already shown interest in, which is the best guess available
    about where they will walk next.
    """
    notes: List[str] = []
    ranked = sorted(
        (r for r in (enemy_probes or []) if _cell(r.get("at")) is not None),
        key=lambda r: int(r.get("day_seen") or 0),
        reverse=True,
    )
    picked: List[Cell] = []
    for row in ranked:
        cell = _cell(row.get("at"))
        if cell is not None and cell not in picked:
            picked.append(cell)
        if len(picked) >= missiles:
            break
    if not picked:
        return [], notes

    notes.append(
        f"{len(picked)} rival probe(s) targeted, freshest first "
        f"(last seen day {ranked[0].get('day_seen')})"
    )

    if len(picked) < missiles:
        spare = missiles - len(picked)
        anchor = picked[-1]
        bounds = _bounds(agent_view)
        ring = [
            c for c in diamond(anchor, 2, bounds)
            if c not in picked and (abs(c[0] - anchor[0]) + abs(c[1] - anchor[1])) == 2
        ]
        for c in ring[:spare]:
            picked.append(c)
        if ring[:spare]:
            notes.append(
                f"{len(ring[:spare])} spare missile(s) spread beside "
                f"{list(anchor)} — a charge fires {missiles} whether you aim "
                "them or not, so they widen the cloud rather than going home"
            )
    return picked[:missiles], notes


def _coverage_targets(
    cells: Sequence[Cell], radius: int, missiles: int,
) -> List[Cell]:
    """Greedy max-coverage: pick ``missiles`` centres covering the most of ``cells``.

    Greedy rather than optimal because ``missiles`` is 3 and the region is
    a few dozen cells — the exact answer is not worth the code, and the
    greedy pick is what a person does by eye anyway.
    """
    want = set(cells)
    chosen: List[Cell] = []
    for _ in range(missiles):
        if not want:
            break
        best, best_hit = None, -1
        for cand in sorted(want):
            hit = len(want & set(diamond(cand, radius)))
            if hit > best_hit:
                best, best_hit = cand, hit
        if best is None:
            break
        chosen.append(best)
        want -= set(diamond(best, radius))
    return chosen


#: A salvo fired later than this hour is not worth its slot. See
#: :func:`enforce_early_salvo` for the argument.
LATEST_USEFUL_HOUR = 2


def enforce_early_salvo(
    moves: Sequence[Mapping[str, Any]], *, latest: int = LATEST_USEFUL_HOUR,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Hoist any salvo to the front of the night. Last line of defence.

    An EMP's whole value is what it denies over the ``CLOUD_HOURS`` after
    it forms, and almost all of that value is concentrated in the early
    hours of the night:

    * **Probes die on contact, and vision is spent early.** A rival's
      probe is worth what it shows them *before* they commit a harvester.
      Killing it at hour 1 costs them the night; killing it at hour 15
      destroys information they have already used.
    * **Landings happen early.** The denial that matters — a rival unable
      to drop on ground you have darkened — only bites while they still
      have hours left to drop in.
    * **A late cloud runs out of night.** Fired at hour 15 in a 21-hour
      Nox, five of its eight hours fall after Aurora and simply never
      happen. You paid a full charge and a full hour for a fraction of a
      cloud.

    The packager already sequences its own salvos to hour 1
    (``_order_for_emp_cloud``), so on the normal path this changes
    nothing. It exists for the LLM-mover fallback, which writes raw moves
    with no such pass and where an ``emp_launch`` can land anywhere in
    the queue — and for any future edit that reorders the compiler.

    Hoisting is safe for everything else: pulling a move forward pushes
    the rest back by one and preserves their relative order, so a drop
    still follows the probe that lights it.
    """
    rows = [dict(m) for m in moves if isinstance(m, Mapping)]
    late = [
        i for i, m in enumerate(rows)
        if str(m.get("a")) == "emp_launch" and i >= latest
    ]
    if not late:
        return rows, []
    salvos = [rows[i] for i in late]
    rest = [m for i, m in enumerate(rows) if i not in set(late)]
    notes = [
        f"{len(salvos)} EMP salvo(s) were moved to the front of the night: "
        f"queued at hour {', '.join(str(i + 1) for i in late)}, and a salvo "
        f"after hour {latest} is close to wasted. Probes die on contact and "
        "rival vision is spent early, landings happen early, and a cloud "
        "fired late runs out of night before it runs out of hours. Same "
        "charge, same hour-slot, a fraction of the denial."
    ]
    return salvos + rest, notes


def _manhattan(a: Cell, b: Cell) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def shaped_salvo(
    region: Sequence[Cell], *, radius: int, missiles: int,
    hole: Optional[Cell] = None,
) -> Optional[Dict[str, Any]]:
    """Aim a salvo so it deliberately leaves ONE cell of ``region`` open.

    This is the play the plain :func:`redsign_targets` cannot express. A
    salvo that blankets a rival's smear denies it to them AND to you, so
    the best you can do afterwards is go and work somewhere else. But the
    three missiles are yours to place, and a cell is only covered when a
    centre is within ``radius`` of it — so pick every centre at
    ``radius + 1`` or more from one chosen cell and that cell stays clear
    while everything around it goes dark.

    What that buys is an island. A probe there is not swept, a harvester
    there is not disabled, and for the eight hours the cloud stands the
    rival cannot come and contest it. When the cloud lifts, the harvester
    is already standing in the middle of the seam with a full walk ahead
    of it, and the rival is still in orbit.

    Returns ``None`` when the geometry does not work — a region too small
    to hold a hole with three centres ``radius + 1`` away from it. That is
    a real answer, not a failure: on a tight smear the honest play is the
    plain salvo.
    """
    cells = [c for c in region if c is not None]
    if len(cells) < 2:
        return None
    want = set(cells)

    # Centre-most cell by default: the hole is where the harvester starts
    # its walk, so a hole on the rim wastes half the comb.
    if hole is None:
        hole = min(
            cells, key=lambda c: (sum(_manhattan(c, o) for o in cells), c),
        )

    # Every centre must be far enough away to leave the hole uncovered.
    # Candidates are drawn from the region and its immediate surround, so
    # a smear narrower than the diamond can still be worked from outside.
    surround: set = set()
    for c in cells:
        surround.update(diamond(c, 1))
    candidates = sorted(
        c for c in (want | surround) if _manhattan(c, hole) > radius
    )
    if not candidates:
        return None

    chosen: List[Cell] = []
    remaining = want - {hole}
    for _ in range(missiles):
        if not remaining:
            break
        best, best_hit = None, 0
        for cand in candidates:
            if cand in chosen:
                continue
            hit = len(remaining & set(diamond(cand, radius)))
            if hit > best_hit:
                best, best_hit = cand, hit
        if best is None:
            break
        chosen.append(best)
        remaining -= set(diamond(best, radius))
    if not chosen:
        return None

    covered = blast(chosen, radius)
    assert hole not in covered, "the hole must survive its own salvo"
    return {
        "targets": chosen,
        "hole": hole,
        # Every cell of the region the salvo does NOT reach. The hole is
        # the one we aimed for; the rest are honest leakage and the agent
        # is told about them, because a rival can use them too.
        "open_cells": sorted(want - covered),
        "covered": sorted(want & covered),
    }


def redsign_targets(
    agent_view: Mapping[str, Any], *, radius: int, missiles: int,
) -> Tuple[List[Cell], Optional[Mapping[str, Any]], List[str]]:
    """Cover the most of a RIVAL's redsign smear with one charge.

    Only a rival's. Scorching a redsign you found yourself denies your own
    harvesters the ground for eight hours to deny a rival who may not even
    be coming — the doctrine calls that out explicitly, and the option is
    simply not built for an own sign.
    """
    notes: List[str] = []
    for region in (agent_view.get("redsign") or []):
        if not isinstance(region, Mapping) or region.get("mine"):
            continue
        cells = [c for c in (_cell(c) for c in (region.get("cells") or [])) if c]
        if not cells:
            continue
        picked = _coverage_targets(cells, radius, missiles)
        covered = len(set(cells) & blast(picked, radius))
        notes.append(
            f"covers {covered}/{len(cells)} of the rival smear with "
            f"{len(picked)} missile(s)"
        )
        return picked, region, notes
    return [], None, notes
