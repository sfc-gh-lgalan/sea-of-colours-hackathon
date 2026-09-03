"""The seat's buying policy — what to spend credits and BLUE on, in orbit.

**This file is yours to change.** It was forked out of the shared
``sea_of_colours.agent.heuristic_agent`` planner in v1.40 for one reason:
a fork could not previously edit how its agent spends money. The planner
lived in a module every seat in the room shares, so "buy weapons earlier"
was not a change any single team could make or push.

Everything below is now local. Two surfaces, deliberately separated:

* :class:`OrbitDials` — the numbers. Thresholds, stockpile caps, the
  fallback prices. Retuning the seat's economy should be an edit here and
  nowhere else, which is what makes it a safe change to hand to a coding
  assistant.
* :func:`plan_orbit_actions` — the priority order. Repair, then fleet,
  then weapons, then probes. Reordering these, or adding a priority, is
  the structural change; the dials are the cheap one.

At the shipped dials this is byte-identical to the shared planner — same
actions, same rationale string — so V12's baseline scores did not move
when it was forked. ``tests/test_orbit_policy.py`` pins that with a
differential test against the shared implementation; if you retune the
dials, that test is *expected* to fail and should be updated or dropped.

Known holes, left deliberately (they are the exercise):

* **Nothing here reads the board.** Buying is a function of credits,
  BLUE and fleet state only. A policy of the form "buy an EMP when a
  redsign is live" needs the night-phase view, which is on ``view`` and
  simply not consulted yet.
* **EMP is capped at a small stockpile** so the seat cannot hoard salvos
  it never fires. That cap is also why V12 buys weapons and leaves them
  in the rack — raising it without teaching the night phase to fire them
  makes the agent worse, not better.
"""

from __future__ import annotations

import random as _random
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Tuple


@dataclass(frozen=True)
class OrbitDials:
    """Every number the buying policy consults.

    Prices are *fallbacks only*. The engine sends real prices on the view
    (``orbit.ship_prices`` / ``orbit.weapon_prices``) and those win; these
    exist so a stripped-down test view still plans sanely.

    The thresholds are the interesting ones — they are the seat's
    economic doctrine expressed as three numbers.
    """

    #: Probe magazine the playbook tops up toward each turn. Sized for a
    #: night of hot-drops (one securing probe per uncovered vein,
    #: RULEBOOK §3.9.7) plus an exploration probe.
    probe_target_stock: int = 4

    #: BLUE above which a weapon is ALWAYS built: chaff first when the
    #: rack is empty (the hour 5-7 / 11-13 egress jam, RULEBOOK §5),
    #: else an EMP.
    blue_always_build: int = 300
    #: BLUE above which an EMP build is rolled for at
    #: :attr:`emp_roll_chance`. Between this and
    #: :attr:`blue_always_build` the build stays a coin flip.
    blue_emp_roll: int = 250
    #: Probability of the roll above landing.
    emp_roll_chance: float = 0.5

    #: Stop buying EMP at this many in stock. Deliberately low — see the
    #: module docstring on hoarding.
    emp_stockpile_cap: int = 2
    #: Stop buying chaff at this many in stock, in the always-build band.
    chaff_stockpile_cap: int = 1

    # Fallback prices — mirrors of the engine constants.
    repair_cost: int = 500
    probe_build_cost: int = 250
    harvester_build_cost: int = 1500
    harvester_cap: int = 3
    emp_blue_cost: int = 200
    emp_credit_cost: int = 0
    chaff_blue_cost: int = 255
    chaff_credit_cost: int = 0


#: The shipped economy. Fork-local, so retuning it cannot affect a rival.
DEFAULT_DIALS = OrbitDials()


# ── View readers ──────────────────────────────────────────────────
#
# Copied in rather than imported so the fork owns its whole orbit path
# and an attendee can follow it without leaving the directory. These are
# plumbing, not policy — they read the engine's view shape and nothing
# more. Changing them is almost never what you want.


def _my_harvesters(view: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """All harvesters the seat owns, in stable id order."""
    entities = (view.get("entities") or {}).get("mine") or []
    rows = [ent for ent in entities if ent.get("type") == "harvester"]
    rows.sort(key=lambda r: str(r.get("id") or ""))
    return rows


def _seat_of(view: Mapping[str, Any]) -> str:
    """The viewer's seat id, defaulting to ``p1`` on a stripped view."""
    meta = view.get("meta") or {}
    hud = view.get("hud") or {}
    return str(meta.get("player") or hud.get("player") or "p1")


def _seat_day_rng(
    view: Mapping[str, Any], seat: str, salt: str = "",
) -> _random.Random:
    """Seeded RNG for ``(session, seat, day, salt)``.

    Determinism matters here: the same view must replan to the same buy,
    or a transient retry silently changes what the seat owns.
    """
    meta = view.get("meta") or {}
    hud = view.get("hud") or {}
    session_id = str(meta.get("session_id") or "")
    day = int(hud.get("day") or meta.get("day") or 0)
    return _random.Random(f"{session_id}|{seat}|{day}|{salt}")


def _blue_purity_total(view: Mapping[str, Any]) -> int:
    """Rolled-up BLUE the seat is holding — the weapons currency."""
    orbit = view.get("orbit") or {}
    total = orbit.get("blue_purity_total")
    if total is not None:
        try:
            return int(total)
        except (TypeError, ValueError):
            pass
    blue = 0
    for p in orbit.get("hoard_parcels") or []:
        if str(p.get("colour", "")).upper() == "BLUE":
            try:
                blue += int(p.get("purity", 0) or 0)
            except (TypeError, ValueError):
                continue
    return blue


# ── The policy ────────────────────────────────────────────────────


def plan_orbit_actions(
    view: Dict[str, Any],
    *,
    weapons_enabled: bool = True,
    dials: OrbitDials = DEFAULT_DIALS,
) -> Tuple[List[Dict[str, Any]], str]:
    """Plan this seat's orbit submission (RULEBOOK §4).

    Returns ``(actions, rationale)`` — wire-format orbit actions ready for
    ``engine.submit_orbit_actions``, and the prose that lands on the card.

    Pure spending in priority order, with no slot cap: credits and BLUE
    are the only limit.

        1. Repair every damaged harvester — a dead rig earns nothing.
        2. Build a harvester if under the fleet cap and affordable.
        3. Build weapons from surplus BLUE (skipped when disabled).
        4. Top the probe magazine up toward :attr:`OrbitDials.probe_target_stock`.

    Probes come last on purpose. Shipping RED is automatic and free, so
    there is no bid to hold credits back for; probes soak up whatever the
    fleet did not need.

    ``weapons_enabled=False`` is the no-weapons tutorial opponent
    (RED_HARVEST_LITE) — it skips priority 3 and changes nothing else.
    """
    orbit = view.get("orbit") or {}
    credits = int(orbit.get("credits", 0))
    cap_used = int(orbit.get("harvester_cap_used", 0))
    cap_max = int(orbit.get("harvester_cap_max", dials.harvester_cap))
    prices = orbit.get("ship_prices") or {}
    repair_cost = int(prices.get("repair", dials.repair_cost))
    probe_cost = int(prices.get("probe_build", dials.probe_build_cost))
    harvester_cost = int(
        prices.get("harvester_build", dials.harvester_build_cost),
    )

    blue_total = _blue_purity_total(view)
    weapon_stock = orbit.get("weapon_stock") or {}
    emp_stock = int(weapon_stock.get("emp", 0) or 0)
    chaff_stock = int(weapon_stock.get("chaff", 0) or 0)
    weapon_prices = orbit.get("weapon_prices") or {}
    emp_price = weapon_prices.get("emp") or {}
    emp_blue_cost = int(emp_price.get("blue", dials.emp_blue_cost))
    emp_credit_cost = int(emp_price.get("credits", dials.emp_credit_cost))
    chaff_price = weapon_prices.get("chaff") or {}
    chaff_blue_cost = int(chaff_price.get("blue", dials.chaff_blue_cost))
    chaff_credit_cost = int(
        chaff_price.get("credits", dials.chaff_credit_cost),
    )

    actions: List[Dict[str, Any]] = []
    descriptors: List[str] = []
    remaining = credits

    # LEGACY PATH, KEPT ON PURPOSE (v1.30). A new season never reaches
    # here — the simulator settles the terminal orbit itself rather than
    # asking, precisely BECAUSE "nothing worth buying" was the only
    # answer anyone ever had. It still fires for a season persisted
    # mid-final-orbit by a pre-v1.30 build. Delete it only once no such
    # save can exist.
    if bool(orbit.get("final_orbit")):
        return [], (
            "final settlement orbit: RED ships and GREEN clears "
            "automatically — nothing worth buying"
        )

    # Priority 1: repair every damaged harvester.
    for harv in [h for h in _my_harvesters(view) if bool(h.get("damaged"))]:
        if remaining < repair_cost:
            descriptors.append(
                f"deferred repair on {harv.get('id')} (need {repair_cost}c, "
                f"have {remaining}c)",
            )
            continue
        actions.append({"a": "repair", "unit": str(harv.get("id"))})
        remaining -= repair_cost
        descriptors.append(f"repaired {harv.get('id')} ({repair_cost}c)")

    # Priority 2: build a new harvester if under cap and affordable.
    if cap_used >= cap_max:
        descriptors.append(
            f"skipped harvester build (fleet at cap {cap_used}/{cap_max})",
        )
    elif remaining >= harvester_cost:
        actions.append({"a": "build_harvester"})
        remaining -= harvester_cost
        descriptors.append(f"built harvester ({harvester_cost}c)")
    else:
        descriptors.append(
            f"deferred harvester build (need {harvester_cost}c, "
            f"have {remaining}c)",
        )

    # Priority 3: weapons, tiered on rolled-up BLUE. Chaff leads the
    # always-build band because an empty rack loses the egress jam, and
    # the jam is the cheapest denial in the game.
    def _afford_emp() -> bool:
        return blue_total >= emp_blue_cost and remaining >= emp_credit_cost

    def _afford_chaff() -> bool:
        return blue_total >= chaff_blue_cost and remaining >= chaff_credit_cost

    if weapons_enabled and blue_total > dials.blue_always_build:
        if chaff_stock < dials.chaff_stockpile_cap and _afford_chaff():
            actions.append({"a": "build_chaff", "count": 1})
            remaining -= chaff_credit_cost
            descriptors.append(
                f"built CHAFF for egress jam (blue {blue_total} > "
                f"{dials.blue_always_build})"
            )
        elif emp_stock < dials.emp_stockpile_cap and _afford_emp():
            actions.append({"a": "build_emp", "count": 1})
            remaining -= emp_credit_cost
            descriptors.append(
                f"built EMP (blue {blue_total} > {dials.blue_always_build})"
            )
        elif _afford_chaff():
            actions.append({"a": "build_chaff", "count": 1})
            remaining -= chaff_credit_cost
            descriptors.append("built CHAFF (blue surplus top-up)")
        elif _afford_emp():
            actions.append({"a": "build_emp", "count": 1})
            remaining -= emp_credit_cost
            descriptors.append("built EMP (blue surplus top-up)")
        else:
            descriptors.append(
                f"weapon build wanted (blue {blue_total}) but unaffordable "
                f"(have {remaining}c)"
            )
    elif (
        weapons_enabled
        and blue_total > dials.blue_emp_roll
        and emp_stock < dials.emp_stockpile_cap
        and _afford_emp()
    ):
        emp_rng = _seat_day_rng(view, _seat_of(view), salt="emp-build")
        if emp_rng.random() < dials.emp_roll_chance:
            actions.append({"a": "build_emp", "count": 1})
            remaining -= emp_credit_cost
            descriptors.append(
                f"built EMP (blue {blue_total} > {dials.blue_emp_roll}, "
                f"50% roll hit)"
            )
        else:
            descriptors.append(
                "skipped EMP build (blue surplus but 50% roll missed)"
            )

    # Priority 4: top the probe magazine up. A flat "build 2" ran dry and
    # left harvesters unable to hot-drop, so top up toward the target in
    # one batched build, bounded by credits and current stock.
    current_probe_stock = int(orbit.get("probe_stock", 0) or 0)
    want = max(0, dials.probe_target_stock - current_probe_stock)
    affordable = remaining // probe_cost if probe_cost > 0 else 0
    build_n = min(want, affordable)
    if build_n >= 1:
        actions.append({"a": "build_probe", "count": int(build_n)})
        remaining -= build_n * probe_cost
        descriptors.append(
            f"built {build_n} probe(s) ({build_n * probe_cost}c) — "
            f"stock {current_probe_stock}→{current_probe_stock + build_n}"
        )
    elif want <= 0:
        descriptors.append(
            f"probe magazine full (stock {current_probe_stock}≥"
            f"{dials.probe_target_stock})"
        )
    else:
        descriptors.append(
            f"deferred probe build (need {probe_cost}c, have {remaining}c)",
        )

    rationale = (
        f"orbit day plan ({credits}c available): "
        + "; ".join(descriptors)
        + f". Carryover {remaining}c."
    )
    return actions, rationale
