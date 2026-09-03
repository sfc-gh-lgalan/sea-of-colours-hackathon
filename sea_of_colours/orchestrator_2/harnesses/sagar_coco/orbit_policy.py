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

**This fork has diverged from V12 here.** V12's priority 3 makes an EMP
wait for 300 BLUE, or 250 and a coin flip, and puts chaff in front of it.
``SAGAR_COCO`` buys an EMP the first day it can pay for one. The
reasoning is a tempo argument: a salvo is worth most while the board is
still being scouted, because a probe killed on day two is vision the
rival never gets to act on, and a rack bought on day five is a rack that
mostly goes home full. See priority 3 in :func:`plan_orbit_actions`.

That divergence is deliberate and it breaks
``tests/test_orbit_policy.py``'s differential test against the shared
planner *for this fork* — that test targets V12, which is unchanged.

Known holes, left deliberately (they are the exercise):

* **Nothing here reads the board.** Buying is a function of credits,
  BLUE and fleet state only. A policy of the form "buy a SECOND EMP when
  a rival redsign is live" needs the night-phase view, which is on
  ``view`` and simply not consulted yet.
* **EMP is still capped at a small stockpile** so the seat cannot hoard
  salvos it never fires. Unlike V12, this fork has earned the right to
  raise that cap: its night phase does fire them.
"""

from __future__ import annotations

import random as _random
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Tuple

# Imported rather than copied. A hardcoded price here is the exact drift
# AGENTS.md warns about, and this fork had it: the credit cost was
# written as 0 while the engine charged 250, so the planner believed an
# EMP was blue-only whenever the view arrived without weapon_prices.
from sea_of_colours.game.weapons import (
    CHAFF_COST_BLUE_PURITY,
    CHAFF_COST_CREDITS,
    EMP_COST_BLUE_PURITY,
    EMP_COST_CREDITS,
)


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

    #: BLUE above which CHAFF is built (the hour 5-7 / 11-13 egress jam,
    #: RULEBOOK §5). EMP no longer waits for this band — see the fork's
    #: priority-3 note.
    blue_always_build: int = 300
    #: RETIRED IN THIS FORK, kept so the diff against V12 reads clearly.
    #: V12 rolls a coin for an EMP between this and
    #: :attr:`blue_always_build`; this seat buys one as soon as it can
    #: pay the price, so neither dial is consulted any more.
    blue_emp_roll: int = 250
    #: RETIRED IN THIS FORK. See :attr:`blue_emp_roll`.
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
    emp_blue_cost: int = EMP_COST_BLUE_PURITY
    emp_credit_cost: int = EMP_COST_CREDITS
    chaff_blue_cost: int = CHAFF_COST_BLUE_PURITY
    chaff_credit_cost: int = CHAFF_COST_CREDITS


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


def _board_signals(view: Mapping[str, Any]) -> Dict[str, Any]:
    """SAGAR_COCO: what the orbit phase reads off the board before it spends."""
    meta = view.get("meta") or {}
    hud = view.get("hud") or {}
    day = int(meta.get("day") or hud.get("day") or 1)
    cap = int(hud.get("season_day_cap") or 7)
    me = str(hud.get("player") or meta.get("player") or "p1")
    rival_probes = 0
    for row in ((view.get("world") or {}).get("live") or []):
        ent = row.get("entity") if isinstance(row, Mapping) else None
        if isinstance(ent, Mapping) and str(ent.get("kind")) == "probe" and str(ent.get("owner")) != me:
            rival_probes += 1
    ci = view.get("competitor_intel") or {}
    rival_probes += sum(1 for r in (ci.get("new_this_day") or []) if str(r.get("kind")) == "enemy_probe_launch")
    rival_probes += sum(1 for r in (ci.get("persistent_echoes") or []) if str(r.get("kind")) == "enemy_probe")
    redsign_live = bool(view.get("redsign"))
    pure_known = any(int(t.get("purity") or 0) >= 255 for t in (view.get("red_tiles") or []) if isinstance(t, Mapping))
    rival_armed = False
    for opp in ((view.get("station_intel") or {}).get("opponents") or []):
        blue = (opp.get("blue") or {}) if isinstance(opp, Mapping) else {}
        if str(blue.get("grade") or "") in ("high",):
            rival_armed = True
        act = (opp.get("activity") or {}) if isinstance(opp, Mapping) else {}
        if int(act.get("emps") or 0) or int(act.get("chaff") or 0):
            rival_armed = True
    return {"day": day, "day_cap": cap, "rival_probes": rival_probes, "redsign_live": redsign_live,
            "pure_known": pure_known, "rival_armed": rival_armed}


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

    # RING-FENCE THE SALVO'S CREDITS BEFORE ANYTHING DISCRETIONARY.
    #
    # Ordering weapons ahead of probes is not enough on its own. An EMP
    # costs BLUE *and* credits, and the harvester build below asks for
    # 1500c in one bite — so on any day where credits land near that
    # boundary the build takes them, the EMP is reported as unaffordable,
    # and the rack the whole fork exists to fill stays empty. The blue is
    # sitting right there; the seat just spent the change.
    #
    # So the EMP's credits are withheld up front whenever the blue is
    # already banked, and released at priority 3. What actually gets
    # given up is the marginal purchase at the bottom of the list — a
    # probe, or a harvester delayed by one day at the boundary — which
    # is the trade this fork wants: 250c is a rounding error against a
    # salvo that removes a rival's eye on the night it matters.
    #
    # Repair is deliberately NOT behind the fence. A damaged harvester is
    # a unit already bought and about to be lost; that is not a
    # discretionary buy competing with a weapon.
    # SAGAR_COCO board-aware gate. A charge is bought when the board says
    # there will be something to shoot at, not merely when it is affordable:
    # a rival probe has been seen, a redsign is live, a rival is thought to
    # be armed, or the season has reached its middle (day 3+) and the rack is
    # empty. Weapons are not bought on the eve of the final settlement orbit,
    # because a charge that cannot be fired is BLUE that could have shipped.
    board = _board_signals(view)
    want_fight = (
        board["rival_probes"] > 0 or board["redsign_live"] or board["rival_armed"]
        or (board["day"] >= 3 and emp_stock == 0)
    )
    nights_left = max(0, board["day_cap"] - board["day"] + 1)
    emp_wanted = (
        weapons_enabled
        and emp_stock < dials.emp_stockpile_cap
        and blue_total >= emp_blue_cost
        and want_fight
        and nights_left >= 1
    )
    emp_reserve = emp_credit_cost if emp_wanted else 0
    emp_took_change = False

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
    elif remaining - emp_reserve >= harvester_cost:
        actions.append({"a": "build_harvester"})
        remaining -= harvester_cost
        descriptors.append(f"built harvester ({harvester_cost}c)")
    elif emp_reserve and remaining >= harvester_cost:
        descriptors.append(
            f"harvester build held one day so the EMP keeps its "
            f"{emp_reserve}c (had {remaining}c, build needs "
            f"{harvester_cost}c) — the blue is already banked and a rack "
            "bought late is a rack that goes home full"
        )
    else:
        descriptors.append(
            f"deferred harvester build (need {harvester_cost}c, "
            f"have {remaining}c)",
        )

    # Priority 3: weapons. THE FORK'S WHOLE POINT — see the module note
    # on EMP doctrine.
    #
    # V12 ships three gates in front of an EMP: a BLUE floor of 300 (or
    # 250 with a coin flip), and chaff ahead of it in the always-build
    # band. On a normal season that means the first EMP lands around day
    # four if it lands at all, which is far too late to matter: an EMP is
    # worth most when the board is still being scouted, because a probe
    # killed on day two is vision the rival never gets to act on.
    #
    # So this seat buys an EMP the FIRST day it can afford one — no BLUE
    # threshold beyond the price itself, no roll. Chaff drops behind it
    # and keeps the surplus band it always had.
    def _afford_emp() -> bool:
        return blue_total >= emp_blue_cost and remaining >= emp_credit_cost

    def _afford_chaff() -> bool:
        return blue_total >= chaff_blue_cost and remaining >= chaff_credit_cost

    if weapons_enabled and emp_wanted and _afford_emp():
        actions.append({"a": "build_emp", "count": 1})
        remaining -= emp_credit_cost
        emp_reserve = 0          # spent — the rest of the list may have it
        emp_took_change = True
        blue_total -= emp_blue_cost
        descriptors.append(
            f"built EMP on sight (blue {blue_total + emp_blue_cost} >= "
            f"{emp_blue_cost}, rack {emp_stock} < {dials.emp_stockpile_cap}) "
            "— this seat arms the first day it can, so the rack is never "
            "the reason a scorch did not fly"
        )
    elif weapons_enabled and emp_stock >= dials.emp_stockpile_cap:
        descriptors.append(
            f"EMP rack full ({emp_stock}/{dials.emp_stockpile_cap}) — "
            "spend one tonight before buying another"
        )
    elif weapons_enabled and not want_fight:
        descriptors.append(
            f"no EMP bought: nothing on the board to shoot at yet (day {board['day']}, "
            "no rival probes seen, no live sign, rivals unarmed)"
        )
    elif weapons_enabled:
        # Name the side that actually fell short. "blue 200 < 200" when
        # the blue was fine and the credits were not sends whoever reads
        # the card looking for the wrong problem.
        short = (
            f"blue {blue_total}/{emp_blue_cost}"
            if blue_total < emp_blue_cost
            else f"credits {remaining}/{emp_credit_cost}"
        )
        descriptors.append(f"wanted an EMP and could not afford it ({short})")

    # Chaff: SAGAR_COCO buys a flare only when the jam has a target: a pure is
    # known and rivals are watching it, or we are within two nights of the end
    # and hold a sign. Otherwise the BLUE stays banked for a second EMP.
    chaff_wanted = weapons_enabled and chaff_stock < dials.chaff_stockpile_cap and (
        (board["pure_known"] and board["rival_probes"] > 0) or (nights_left <= 2 and board["redsign_live"])
    )
    if chaff_wanted and _afford_chaff():
        actions.append({"a": "build_chaff", "count": 1})
        remaining -= chaff_credit_cost
        blue_total -= chaff_blue_cost
        descriptors.append(
            f"built CHAFF: a jam has a target (pure known={board['pure_known']}, "
            f"rival probes={board['rival_probes']}, nights left={nights_left})"
        )
    elif weapons_enabled and blue_total > dials.blue_always_build and emp_stock >= dials.emp_stockpile_cap and _afford_chaff():
        actions.append({"a": "build_chaff", "count": 1})
        remaining -= chaff_credit_cost
        blue_total -= chaff_blue_cost
        descriptors.append("built CHAFF (blue surplus with a full EMP rack)")

    # Priority 4: top the probe magazine up. A flat "build 2" ran dry and
    # left harvesters unable to hot-drop, so top up toward the target in
    # one batched build, bounded by credits and current stock.
    current_probe_stock = int(orbit.get("probe_stock", 0) or 0)
    want = max(0, dials.probe_target_stock - current_probe_stock)
    # ``emp_reserve`` is still standing here only when the EMP wanted its
    # credits and could not be bought this orbit; in that case the probes
    # must not eat the money it is still waiting on.
    spendable = max(0, remaining - emp_reserve)
    affordable = spendable // probe_cost if probe_cost > 0 else 0
    build_n = min(want, affordable)
    if build_n >= 1:
        actions.append({"a": "build_probe", "count": int(build_n)})
        remaining -= build_n * probe_cost
        # Say it in the case that actually happens. The reserve is zero by
        # now precisely BECAUSE the salvo was bought, so keying the note
        # off the reserve alone would only ever explain the miss.
        gave_up = (
            " (one fewer than credits allow — the EMP has first call on the "
            "change)" if (emp_reserve or emp_took_change) else ""
        )
        descriptors.append(
            f"built {build_n} probe(s) ({build_n * probe_cost}c) — "
            f"stock {current_probe_stock}→{current_probe_stock + build_n}"
            f"{gave_up}"
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
