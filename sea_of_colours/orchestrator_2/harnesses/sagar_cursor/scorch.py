"""Rung 1 — tell the night phase what is in its own rack.

Stock V12 buys ordnance and then plans every night as if unarmed. The reason is
not subtle once you look: the only module in the whole harness that reads
``weapon_stock`` is ``orbit_policy.py``, which is the *buying* code. The night
phase is never told. An agent cannot choose a weapon it does not know it owns,
so no amount of doctrine or menu work matters until this exists.

The stock is already sitting on the view at ``orbit.weapon_stock`` — it just was
never read at night. That is the whole of rung 1.

**Specs are imported, never copied.** ``docs/TEACHING_WEAPONS.md`` records a
fork that shipped ``emp_credit_cost = 0`` while the engine charged 250; the
wrong constant survived because the view normally supplies prices and the
literal only bit when a view arrived without them. A hardcoded literal in a fork
is worse than one in the engine, because forks are copied wholesale and it
replicates into every agent in the room. So this module reads the view first and
falls back to ``game.weapons`` itself.
"""

from __future__ import annotations

from typing import (
    Any, Collection, Dict, List, Mapping, Optional, Sequence, Set, Tuple,
)

from sea_of_colours.game.weapons import (
    CHAFF_COST_BLUE_PURITY,
    CHAFF_COST_CREDITS,
    CHAFF_DURATION_HOURS,
    EMP_CLOUD_HOURS,
    EMP_COST_BLUE_PURITY,
    EMP_COST_CREDITS,
    EMP_MISSILES_PER_LAUNCH,
    EMP_RADIUS,
)

#: Cells one EMP cloud covers. Manhattan radius, so ``2r(r+1)+1`` — 13 at r=2.
#: Quoted rather than recomputed in prose so the number cannot drift from the
#: geometry it describes.
EMP_CELLS_PER_CLOUD = 2 * EMP_RADIUS * (EMP_RADIUS + 1) + 1


def stock(agent_view: Mapping[str, Any]) -> Dict[str, int]:
    """What the seat actually holds tonight, off the view."""
    orbit = (agent_view.get("orbit") or {}) if agent_view else {}
    weapons = orbit.get("weapon_stock") or {}
    return {
        "emp": int(weapons.get("emp", 0) or 0),
        "chaff": int(weapons.get("chaff", 0) or 0),
    }


def specs(agent_view: Mapping[str, Any] | None = None) -> Dict[str, Dict[str, int]]:
    """Engine dials for both weapons, preferring anything the view supplies.

    A retune of ``game/weapons.py`` therefore reaches this agent's arithmetic
    *and* its prose with no edit here.
    """
    orbit = (agent_view.get("orbit") or {}) if agent_view else {}
    supplied = orbit.get("weapon_specs") or {}
    emp = supplied.get("emp") or {}
    chaff = supplied.get("chaff") or {}
    return {
        "emp": {
            "blue": int(emp.get("blue", EMP_COST_BLUE_PURITY)),
            "credits": int(emp.get("credits", EMP_COST_CREDITS)),
            "radius": int(emp.get("radius", EMP_RADIUS)),
            "missiles": int(emp.get("missiles", EMP_MISSILES_PER_LAUNCH)),
            "cloud_hours": int(emp.get("cloud_hours", EMP_CLOUD_HOURS)),
            "cells_per_cloud": EMP_CELLS_PER_CLOUD,
        },
        "chaff": {
            "blue": int(chaff.get("blue", CHAFF_COST_BLUE_PURITY)),
            "credits": int(chaff.get("credits", CHAFF_COST_CREDITS)),
            "hours": int(chaff.get("hours", CHAFF_DURATION_HOURS)),
        },
    }


def is_armed(agent_view: Mapping[str, Any]) -> bool:
    """Does the seat hold anything it could fire tonight?"""
    held = stock(agent_view)
    return held["emp"] > 0 or held["chaff"] > 0


def _cloud(cell: Tuple[int, int], radius: int) -> Set[Tuple[int, int]]:
    """Every cell a missile landing on ``cell`` darkens (Manhattan disk)."""
    x, y = cell
    return {
        (x + dx, y + dy)
        for dx in range(-radius, radius + 1)
        for dy in range(-radius + abs(dx), radius - abs(dx) + 1)
    }


def salvo_targets(
    rival_probes: Sequence[Tuple[int, int]],
    *,
    radius: int,
    missiles: int,
    keep_clear: Collection[Tuple[int, int]] = (),
) -> List[Tuple[int, int]]:
    """Aim points that darken the most rival probes, greedily, worst-covered first.

    Deliberately simple: aim AT probes rather than at clever offsets. A missile
    centred on a probe always kills that probe, and the Manhattan r2 cloud
    usually catches its neighbours too, so the greedy pick is close to the best
    available and — more importantly — is explainable on a menu line.

    ``keep_clear`` is ground this seat intends to work tonight. A cloud does not
    distinguish friend from enemy: our own probes inside it die and our own
    harvesters are disabled. An aim point that would darken our own night is
    dropped rather than quietly shipped, because a salvo that costs us the seam
    we were about to mine is a loss even when it lands perfectly.
    """
    remaining = {tuple(p) for p in rival_probes}
    protect = {tuple(c) for c in keep_clear}
    chosen: List[Tuple[int, int]] = []

    while remaining and len(chosen) < missiles:
        best: Optional[Tuple[int, int]] = None
        best_kill: Set[Tuple[int, int]] = set()
        for candidate in sorted(remaining):
            blast = _cloud(candidate, radius)
            if blast & protect:
                continue  # would darken our own working ground
            killed = blast & remaining
            if len(killed) > len(best_kill):
                best, best_kill = candidate, killed
        if best is None:
            break
        chosen.append(best)
        remaining -= best_kill
    return chosen


def salvo_kill_count(
    targets: Sequence[Tuple[int, int]],
    rival_probes: Sequence[Tuple[int, int]],
    *,
    radius: int,
) -> int:
    """How many rival probes the salvo actually destroys."""
    blast: Set[Tuple[int, int]] = set()
    for t in targets:
        blast |= _cloud(tuple(t), radius)
    return len({tuple(p) for p in rival_probes} & blast)


def prompt_block(agent_view: Mapping[str, Any]) -> str:
    """The rack, in the terms the model needs to reason about spending it.

    Deliberately states what a charge DOES, not merely how many there are. A
    bare count is not actionable: "emp: 1" tells the model nothing about whether
    firing beats harvesting. Silent when the rack is empty, so an unarmed night
    pays no tokens for it.
    """
    held = stock(agent_view)
    if held["emp"] <= 0 and held["chaff"] <= 0:
        return ""

    dials = specs(agent_view)
    lines = ["YOUR RACK — ordnance you own tonight and may spend:"]

    if held["emp"] > 0:
        emp = dials["emp"]
        lines.append(
            f"  EMP x{held['emp']} — one launch fires {emp['missiles']} missiles "
            f"at {emp['missiles']} cells you choose. Each raises a "
            f"{emp['cells_per_cloud']}-cell cloud (Manhattan r{emp['radius']}) "
            f"lasting {emp['cloud_hours']}h. Harvesters in a cloud are disabled; "
            f"probes in a cloud are DESTROYED."
        )
        lines.append(
            "    One launch = ONE move = ONE of your 21 hours, and the whole "
            "salvo lands that hour."
        )
        lines.append(
            "    Friendly fire is ON: your own probes in your own cloud die and "
            "your own harvesters are disabled. Aim so your night still works."
        )
    if held["chaff"] > 0:
        chaff = dials["chaff"]
        lines.append(
            f"  CHAFF x{held['chaff']} — cancels every OTHER seat's action for "
            f"{chaff['hours']}h across the whole board. Your own units act "
            f"normally through it."
        )

    lines.append(
        "    The BLUE was already spent in orbit and is sunk. What a shot costs "
        "you NOW is the hour, measured against the best harvest chain that hour "
        "would otherwise have banked."
    )
    return "\n".join(lines)
