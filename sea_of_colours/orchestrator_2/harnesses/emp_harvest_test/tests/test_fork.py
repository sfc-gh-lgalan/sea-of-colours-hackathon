"""EMP_HARVEST_TEST — the fork that actually fires its rack.

``soc weapons`` grades the fork by reading its source, which answers
"have you built this rung" and not "does it work". These tests are the
other half: they drive the real menu builder, the real compiler and the
real orbit planner, and they pin the four things that were wrong before.

The engine's own EMP semantics (RULEBOOK §4.9.3) are pinned elsewhere, in
``tests/test_emp_beacon_timing.py``. What is pinned here is the *agent's*
model of them — the arithmetic and the ordering that stop the seat
scorching its own night.
"""

from __future__ import annotations

import pytest

from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test import (
    agency, chat_schema, doctrine, last_night, orbit_policy, packager, prompt,
    scorch, weapon_plays,
)
from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test.orbit_policy import (
    OrbitDials, plan_orbit_actions,
)


def _view(**over):
    """A minimal agent view with a rack and a couple of rival probes."""
    base = {
        "meta": {"player": "p1", "session_id": "s", "width": 30, "height": 30},
        "hud": {"day": 3},
        "orbit": {
            "credits": 4000,
            "harvester_cap_used": 1,
            "harvester_cap_max": 3,
            "probe_stock": 2,
            "weapon_stock": {"emp": 1, "chaff": 0},
        },
        "competitor_intel": {
            "new_this_day": [
                {"kind": "enemy_probe_launch", "at": [10, 10], "day_seen": 3},
            ],
            "persistent_echoes": [
                {"kind": "enemy_probe", "at": [20, 20], "last_seen_day": 1},
            ],
        },
        # ``_orbit_harvester_ids`` reads my_assets, not entities.mine.
        "my_assets": [
            {"id": "harvester_p1_1", "kind": "harvester", "state": "orbit"},
            {"id": "harvester_p1_2", "kind": "harvester", "state": "orbit"},
        ],
        "entities": {"mine": []},
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


def _probes(view):
    from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test._v7 import (
        probe_hints,
    )
    return probe_hints._enemy_probe_cells(view)


# ── rung 2: the verb can be expressed at all ──────────────────────


def test_the_move_schema_allows_the_weapon_verb():
    """The original blocker, and the least obvious one.

    ``_MOVE_ITEM``'s enum is handed to the model as a strict structured
    output schema, so before this the model *could not* emit an
    ``emp_launch`` however hard the prompt pushed. Every "the model won't
    fire" diagnosis upstream of this was really the schema refusing.
    """
    verbs = chat_schema._MOVE_ITEM["properties"]["a"]["enum"]
    assert "emp_launch" in verbs
    assert {"drop", "step", "pickup", "probe"} <= set(verbs), (
        "widening must not drop the verbs the seat already used"
    )
    # A salvo's ``at`` is a list OF CELLS where every other verb's is one
    # cell; the v7 `array of integer` shape rejects the nested form.
    assert chat_schema._MOVE_ITEM["properties"]["at"] == {"type": "array"}


def test_the_frozen_v7_baseline_is_left_alone():
    from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test._v7 import (
        chat_schema as v7,
    )
    assert "emp_launch" not in v7._MOVE_ITEM["properties"]["a"]["enum"], (
        "_v7 is the regression baseline; fork changes belong in the live copy"
    )


# ── rung 2/3: the menu carries the play, and explains it ──────────


def test_a_salvo_is_offered_when_the_rack_is_loaded():
    view = _view()
    reg = agency.build_registry(
        agent_view=view, enemy_probes=_probes(view),
    )
    assert "EMP_SCORCH" in reg
    opt = reg["EMP_SCORCH"]
    assert opt.kind == "emp"
    # Rung 3 — geometry alone is not a decision.
    assert opt.detail and opt.rationale
    assert "hour" in opt.rationale.lower(), (
        "the menu must quote the real price: a launch spends one of 21 slots"
    )


def test_no_salvo_is_offered_on_an_empty_rack():
    view = _view(orbit={"weapon_stock": {"emp": 0, "chaff": 0}})
    reg = agency.build_registry(
        agent_view=view, enemy_probes=_probes(view),
    )
    assert not [o for o in reg.values() if o.kind == "emp"]


def test_targets_are_rival_probes_freshest_first():
    """Recency, because an old probe has already converted its vision."""
    view = _view()
    hits, notes = scorch.probe_targets(view, _probes(view), missiles=3)
    assert hits[0] == (10, 10), "day 3 sighting must outrank the day 1 one"
    assert (20, 20) in hits
    assert notes


def test_spare_missiles_spread_beside_the_last_target():
    """A charge fires three whether or not you aim three."""
    view = _view(competitor_intel={
        "new_this_day": [
            {"kind": "enemy_probe_launch", "at": [10, 10], "day_seen": 3},
        ],
        "persistent_echoes": [],
    })
    hits, notes = scorch.probe_targets(view, _probes(view), missiles=3)
    assert len(hits) == 3, "unaimed missiles are wasted, so they get aimed"
    assert hits[0] == (10, 10)
    for extra in hits[1:]:
        assert abs(extra[0] - 10) + abs(extra[1] - 10) == 2
    assert any("spare" in n for n in notes)


# ── the redsign rule ──────────────────────────────────────────────


def test_only_a_rivals_redsign_is_offered_for_scorching():
    """Scorching your own sign locks you out of your own pure for 8h."""
    mine = _view(redsign=[{"mine": True, "cells": [[5, 5], [5, 6], [6, 5]]}])
    reg = agency.build_registry(agent_view=mine, enemy_probes=[])
    assert "SCORCH_REDSIGN" not in reg

    theirs = _view(redsign=[{"mine": False, "cells": [[5, 5], [5, 6], [6, 5]]}])
    reg = agency.build_registry(agent_view=theirs, enemy_probes=[])
    assert "SCORCH_REDSIGN" in reg


def test_doctrine_ranks_a_certain_grab_above_scorching_your_own_sign():
    """Pinned as TEXT because that is how this decision is actually made.

    The option is deliberately still offered when the sign is the seat's
    own and a grab exists — filtering the menu would teach the agent
    nothing and would hide a play it is sometimes right to make. The
    ranking lives in doctrine instead, so it can be argued with.
    """
    text = doctrine.DOCTRINE_SCORCH.lower()
    assert "never scorch your own redsign" in text
    assert "certain red outranks speculative denial" in text


# ── the compiler: one slot, and don't bomb yourself ───────────────


def _opt(kind, option_id, payload):
    return agency.Option(
        option_id=option_id, kind=kind, title=option_id, detail="",
        payload=payload,
    )


def test_a_three_missile_salvo_is_one_move_and_one_hour():
    """Three missiles, one charge, ONE of the seat's 21 slots (§4.9.3)."""
    view = _view()
    moves, log = packager.pack_recipe(
        [_opt("emp", "EMP_SCORCH", {"targets": [[10, 10], [12, 10], [8, 10]]})],
        view, complete=False,
    )
    salvos = [m for m in moves if m["a"] == "emp_launch"]
    assert len(salvos) == 1, "a salvo is one move, not one move per missile"
    assert salvos[0]["at"] == [[10, 10], [12, 10], [8, 10]]
    assert any("hour 1" in line for line in log)


def test_a_salvo_cannot_be_fired_off_an_empty_rack():
    view = _view(orbit={"weapon_stock": {"emp": 0, "chaff": 0}})
    moves, log = packager.pack_recipe(
        [_opt("emp", "EMP_SCORCH", {"targets": [[10, 10]]})],
        view, complete=False,
    )
    assert not [m for m in moves if m["a"] == "emp_launch"]
    assert any("rack is empty" in line for line in log)


def test_the_salvo_is_resequenced_to_hour_one():
    """A cloud is worth what it denies over the following 8 hours."""
    view = _view()
    moves, _log = packager.pack_recipe(
        [
            _opt("probe", "PR1", {"at": [2, 2]}),
            _opt("emp", "EMP_SCORCH", {"targets": [[25, 25]]}),
        ],
        view, complete=False,
    )
    assert moves[0]["a"] == "emp_launch", (
        "fired last, a cloud denies almost nothing"
    )


def test_plays_clear_of_the_blast_go_before_plays_inside_it():
    """Free clock for the affected plays, and it costs the agent nothing."""
    view = _view()
    inside = _opt("probe", "PR_IN", {"at": [10, 11]})   # in the r=2 diamond
    outside = _opt("probe", "PR_OUT", {"at": [2, 2]})
    moves, log = packager.pack_recipe(
        [inside, outside, _opt("emp", "EMP_SCORCH", {"targets": [[10, 10]]})],
        view, complete=False,
    )
    probes = [m["at"] for m in moves if m["a"] == "probe"]
    assert probes.index([2, 2]) < probes.index([10, 11])
    assert any("blast diamond" in line for line in log)


def test_walking_into_your_own_cloud_is_warned_about_and_kept():
    """House rule: price the agent's plan, never delete it behind its back.

    Friendly fire is real — the unit sits 'empd' and banks nothing — but
    the engine allows the move, so it stays the agent's call. What the
    agent is owed is finding out.
    """
    view = _view()
    moves, log = packager.pack_recipe(
        [
            _opt("emp", "EMP_SCORCH", {"targets": [[10, 10]]}),
            _opt("chain", "CH1", {"drop_at": [10, 10], "cells": [[10, 11]]}),
        ],
        view, complete=False,
    )
    assert any(m["a"] == "drop" and m["at"] == [10, 10] for m in moves), (
        "the run must survive — warned, not cut"
    )
    ff = [line for line in log if "FRIENDLY FIRE" in line]
    assert len(ff) == 1, "warn once, with the hour it clears"
    assert "hour 9" in ff[0], "launched at hour 1, an 8h cloud clears at 9"


def test_the_blast_diamond_matches_the_engines_geometry():
    cells = scorch.diamond((10, 10), 2)
    assert len(cells) == 13, "2r(r+1)+1 at r=2 (RULEBOOK §4.9.3)"
    assert (10, 12) in cells and (11, 11) in cells
    assert (12, 11) not in cells, "Manhattan, not Chebyshev"


def test_geometry_follows_the_engine_dials_not_a_hardcoded_copy():
    """A retune of ``game/weapons.py`` must reach the agent with no edit."""
    view = _view(orbit={"weapon_specs": {"emp": {
        "radius": 3, "missiles_per_launch": 5, "cloud_hours": 4,
    }}})
    assert scorch.specs(view) == (3, 5, 4)


# ── orbit: arm on the first day you can ───────────────────────────


def _orbit_view(blue: int, emp_stock: int = 0):
    return {
        "meta": {"player": "p1", "session_id": "s"},
        "hud": {"day": 2},
        "orbit": {
            "credits": 600,
            "harvester_cap_used": 3,
            "harvester_cap_max": 3,
            "probe_stock": 4,
            "blue_purity_total": blue,
            "weapon_stock": {"emp": emp_stock, "chaff": 0},
        },
        "entities": {"mine": []},
    }


def test_an_emp_is_bought_the_first_day_it_is_affordable():
    """V12 waits for 300 BLUE, or 250 and a coin flip. This fork does not.

    200 BLUE is the price itself, and it is well under either of V12's
    gates — which is the whole divergence: an EMP bought on day five is
    a rack that mostly goes home full.
    """
    actions, why = plan_orbit_actions(_orbit_view(blue=200))
    assert {"a": "build_emp", "count": 1} in actions
    assert "on sight" in why


def test_no_emp_is_bought_below_its_price():
    actions, why = plan_orbit_actions(_orbit_view(blue=199))
    assert not [a for a in actions if a["a"] == "build_emp"]
    assert "could not afford" in why


def test_the_rack_is_still_capped():
    """Hoarding salvos it never fires is the failure this fork replaces."""
    cap = OrbitDials().emp_stockpile_cap
    actions, why = plan_orbit_actions(_orbit_view(blue=900, emp_stock=cap))
    assert not [a for a in actions if a["a"] == "build_emp"]
    assert "rack full" in why


def test_weapons_disabled_still_buys_nothing():
    """RED_HARVEST_LITE's no-weapons tutorial seat must be unaffected."""
    actions, _ = plan_orbit_actions(
        _orbit_view(blue=900), weapons_enabled=False,
    )
    assert not [a for a in actions if a["a"].startswith("build_emp")]
    assert not [a for a in actions if a["a"].startswith("build_chaff")]


# ── rung 1: the seat is told, in prose, what it holds ─────────────


def test_the_rack_block_says_what_a_charge_does():
    block = prompt.format_rack_block(_view())
    assert "EMP x1" in block
    assert "FRIENDLY FIRE" in block
    assert "21 hour-slots" in block


def test_the_rack_block_is_silent_when_there_is_nothing_to_fire():
    empty = _view(orbit={"weapon_stock": {"emp": 0, "chaff": 0}})
    assert prompt.format_rack_block(empty) == "", (
        "a rack readout of zeroes invites reasoning about a weapon the "
        "seat cannot fire"
    )


@pytest.mark.parametrize("emp,expect", [(1, True), (0, False)])
def test_scorch_doctrine_is_gated_on_owning_a_charge(emp, expect):
    from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test._v7 import (
        orbit_wishlist,
    )
    view = _view(orbit={"weapon_stock": {"emp": emp, "chaff": 0}})
    text = prompt._assemble_doctrine(
        agent_view=view, day=3, day_cap=7, hot_drop_hints=[],
        wishlist=orbit_wishlist.Wishlist(), opponent_weapon_estimates={},
        supersede_hints=[], is_setup_night=False,
    )
    assert ("FIRE AN EMP" in text) is expect


# ── the shaped scorch: deny the ground and STAND in it ────────────


def _smear_view(**over):
    """A rival redsign smear big enough to hold a hole, and two harvesters."""
    view = _view(
        redsign=[{
            "mine": False,
            "cells": [[x, y] for x in range(8, 14) for y in range(8, 13)],
        }],
        my_assets=[
            {"id": "harvester_p1_1", "kind": "harvester", "state": "orbit"},
            {"id": "harvester_p1_2", "kind": "harvester", "state": "orbit"},
        ],
        orbit={"probe_stock": 3, "weapon_stock": {"emp": 1, "chaff": 0}},
    )
    view.update(over)
    return view


def test_the_salvo_can_be_shaped_to_leave_a_hole():
    region = [(x, y) for x in range(8, 14) for y in range(8, 13)]
    shape = scorch.shaped_salvo(region, radius=2, missiles=3)
    assert shape is not None
    hole = shape["hole"]
    # The whole trick: every centre far enough out that the blast misses.
    for centre in shape["targets"]:
        assert abs(centre[0] - hole[0]) + abs(centre[1] - hole[1]) > 2
    assert hole not in scorch.blast(shape["targets"], 2)
    assert len(shape["covered"]) > len(region) / 2, (
        "a hole is only worth leaving if the rest actually goes dark"
    )
    assert hole in shape["open_cells"]


def test_a_region_too_tight_for_a_hole_says_so():
    """``None`` is a real answer — on a tight smear the plain salvo is honest."""
    assert scorch.shaped_salvo([(5, 5)], radius=2, missiles=3) is None


def test_the_shaped_play_probes_the_hole_then_drops_into_it():
    view = _smear_view()
    reg = agency.build_registry(agent_view=view, enemy_probes=[])
    assert "BLIND_SCORCH" in reg
    moves, _log = packager.pack_recipe(
        [reg["BLIND_SCORCH"]], view, complete=False,
    )
    hole = tuple(reg["BLIND_SCORCH"].payload["hole"])
    kinds = [m["a"] for m in moves]
    # Probe BEFORE drop: without live vision at hour start the landing is
    # illegal (§3.9.7), so the order is legality, not taste.
    assert kinds[:3] == ["emp_launch", "probe", "drop"]
    assert tuple(moves[2]["at"]) == hole
    assert hole not in scorch.blast(
        [tuple(t) for t in moves[0]["at"]], scorch.RADIUS,
    ), "the harvester must land in the cell the salvo deliberately missed"


def test_the_held_walk_starts_the_hour_the_cloud_lifts():
    view = _smear_view()
    reg = agency.build_registry(agent_view=view, enemy_probes=[])
    moves, _log = packager.pack_recipe(
        [reg["BLIND_SCORCH"]], view, complete=False,
    )
    first_step = next(
        i for i, m in enumerate(moves, 1)
        if m["a"] == "step" and m["unit"] == "harvester_p1_1"
    )
    assert first_step == 1 + scorch.CLOUD_HOURS, (
        "launched at hour 1, an 8h cloud clears at hour 9 — earlier is a "
        "wasted hour AND a lost cell, later is tempo given away"
    )
    assert moves[-1]["a"] == "pickup", "a harvester not lifted is destroyed"


def test_other_work_fills_the_cloud_hours_instead_of_waiting():
    """The user-visible point: a scorch night is not a night spent waiting.

    One action per hour across the WHOLE fleet (§3.10), so the second
    harvester's chain is SPLIT around the held walk — it runs during the
    cloud, the held unit combs the instant it lifts, and the chain
    finishes behind it.
    """
    view = _smear_view()
    reg = agency.build_registry(agent_view=view, enemy_probes=[])
    elsewhere = _opt("chain", "CH1", {
        "drop_at": [25, 25],
        "cells": [[25, 26], [25, 27], [25, 28], [26, 28], [27, 28]],
    })
    moves, log = packager.pack_recipe(
        [reg["BLIND_SCORCH"], elsewhere], view, complete=False,
    )
    assert not [m for m in moves if m["a"] == "wait"], (
        "there was real work for those hours; waiting is the fallback"
    )
    held = [i for i, m in enumerate(moves, 1)
            if m.get("unit") == "harvester_p1_1" and m["a"] == "step"]
    other = [i for i, m in enumerate(moves, 1)
             if m.get("unit") == "harvester_p1_2"]
    assert min(other) < min(held), "the other unit works during the cloud"
    assert max(other) > max(held), "and finishes after the held walk"
    assert any("moved to AFTER" in line for line in log)


def test_a_thin_night_waits_and_is_told_it_was_thin():
    """Waits are honest, not silent — they mean the plan was too small."""
    view = _smear_view()
    reg = agency.build_registry(agent_view=view, enemy_probes=[])
    moves, log = packager.pack_recipe(
        [reg["BLIND_SCORCH"]], view, complete=False,
    )
    assert [m for m in moves if m["a"] == "wait"]
    assert any("nothing else to do" in line for line in log)


def test_the_lift_is_protected_when_the_night_runs_out_of_hours():
    """A pickup pushed past hour 21 is a harvester lost at Aurora."""
    view = _smear_view()
    reg = agency.build_registry(agent_view=view, enemy_probes=[])
    long_chain = _opt("chain", "CH1", {
        "drop_at": [25, 2],
        "cells": [[25, 3], [25, 4], [25, 5], [25, 6], [25, 7], [25, 8]],
    })
    # A short night makes the squeeze reachable without a contrived board;
    # the cap is a parameter precisely so this branch is testable.
    moves, log = packager.pack_recipe(
        [reg["BLIND_SCORCH"], long_chain], view,
        complete=False, max_moves=14,
    )
    assert len(moves) <= 14
    assert any(
        m["a"] == "pickup" and m.get("unit") == "harvester_p1_1"
        for m in moves
    ), "the held unit must still be lifted"
    assert any("walk was cut" in line for line in log)


def test_the_shaped_play_gives_its_harvester_back_if_it_cannot_probe():
    """Drawn-and-unused units must return to the pool, or they idle in orbit."""
    view = _smear_view(orbit={
        "probe_stock": 0, "weapon_stock": {"emp": 1, "chaff": 0},
    })
    reg = agency.build_registry(agent_view=view, enemy_probes=[])
    moves, log = packager.pack_recipe(
        [reg["BLIND_SCORCH"],
         _opt("chain", "CH1", {"drop_at": [25, 25], "cells": [[25, 26]]})],
        view, complete=False,
    )
    assert any(m["a"] == "emp_launch" for m in moves), "the scorch still flies"
    assert not [m for m in moves if m["a"] == "drop" and m["at"] == [10, 10]]
    assert len([m for m in moves if m["a"] == "drop"]) == 1, (
        "the unit the shape could not use must be free for the other chain"
    )
    assert any("occupation does not" in line for line in log)


# ── the EMP gets first call on the change ─────────────────────────


def _buy_view(credits, blue, *, probes=0, emp=0, chaff=0, snap=0):
    return {
        "orbit": {
            "credits": credits, "probe_stock": probes,
            "weapon_stock": {"emp": emp, "chaff": chaff, "snap": snap},
            "weapons_enabled": True, "blue_purity_total": blue,
        },
        "my_assets": [{"id": "h0", "kind": "harvester", "state": "orbit"}],
        "hud": {"day": 2}, "meta": {"player": "p1"},
    }


def test_the_fallback_prices_are_the_engines_prices():
    """This fork shipped ``emp_credit_cost = 0`` while the engine charged 250.

    Fallbacks are consulted whenever a view arrives without
    ``weapon_prices``, so a stale copy here makes the planner believe an
    EMP is blue-only and budget for a salvo it cannot buy.
    """
    from sea_of_colours.game import weapons as engine_weapons
    d = orbit_policy.DEFAULT_DIALS
    assert d.emp_blue_cost == engine_weapons.EMP_COST_BLUE_PURITY
    assert d.emp_credit_cost == engine_weapons.EMP_COST_CREDITS
    assert d.emp_credit_cost > 0, "an EMP costs credits as well as blue"
    assert d.chaff_blue_cost == engine_weapons.CHAFF_COST_BLUE_PURITY
    assert d.chaff_credit_cost == engine_weapons.CHAFF_COST_CREDITS


def test_a_probe_is_given_up_before_the_salvo_is():
    actions, why = orbit_policy.plan_orbit_actions(_buy_view(1000, 200))
    kinds = [a["a"] for a in actions]
    assert "build_emp" in kinds
    # 1000c would buy four probes; the EMP's 250 comes off the top first.
    probe = next(a for a in actions if a["a"] == "build_probe")
    assert probe["count"] == 3, "the marginal probe is what pays for the rack"
    assert "first call on the change" in why


def test_the_harvester_build_waits_a_day_rather_than_eat_the_salvo():
    """The boundary case that left the rack empty with the blue banked."""
    actions, why = orbit_policy.plan_orbit_actions(_buy_view(1500, 200))
    kinds = [a["a"] for a in actions]
    assert "build_emp" in kinds
    assert "build_harvester" not in kinds
    assert "held one day" in why


def test_a_harvester_is_still_built_when_both_fit():
    """The fence withholds 250c — it must not block a build that can afford it."""
    actions, _why = orbit_policy.plan_orbit_actions(_buy_view(1750, 200))
    kinds = [a["a"] for a in actions]
    assert kinds[:2] == ["build_harvester", "build_emp"]


def test_nothing_is_reserved_when_the_blue_is_not_there():
    """No blue means no charge, so the credits must flow to probes as before.

    The blue here is 50 rather than the 150 this test used to carry. 150 was
    "too poor for a weapon" while the EMP at 200 was the only thing the seat
    could buy; a snap is 100, so 150 now buys one and the test was measuring
    a seat that had gone shopping. Below the cheapest charge on the board is
    the condition it was always trying to describe.
    """
    actions, why = orbit_policy.plan_orbit_actions(_buy_view(1000, 50))
    assert [a["a"] for a in actions] == ["build_probe"]
    assert next(a for a in actions if a["a"] == "build_probe")["count"] == 4
    assert "blue 50/200" in why, "say which side fell short"


def test_the_shortfall_message_names_the_side_that_actually_failed():
    _actions, why = orbit_policy.plan_orbit_actions(_buy_view(200, 200))
    assert "credits 200/250" in why
    assert "blue 200 < 200" not in why, "blue was sufficient — do not blame it"


def test_a_full_rack_reserves_nothing():
    """A rack with nothing left to buy hands its whole wallet to the probes.

    Every weapon is at cap, not just the EMP. With three weapons declared, an
    EMP-only full rack no longer means "buying is finished" — the seat would
    correctly go on to a chaff and a snap, and the probe count this test is
    really about would drop for a reason that has nothing to do with reserves.
    """
    d = orbit_policy.DEFAULT_DIALS
    actions, _why = orbit_policy.plan_orbit_actions(
        _buy_view(
            1000, 400,
            emp=d.emp_stockpile_cap,
            chaff=d.chaff_stockpile_cap,
            snap=d.snap_stockpile_cap,
        ),
    )
    assert not [a for a in actions if a["a"].startswith("build_")
                and a["a"] != "build_probe"]
    assert next(a for a in actions if a["a"] == "build_probe")["count"] == 4


# ── the "fire it first or not at all" guard ───────────────────────


def test_a_late_salvo_is_hoisted_to_the_front():
    """The fallback path has no ordering pass, so this is where it lands.

    Fired late, a salvo denies almost nothing: the rival's probes have
    already shown them the board, their landings are made, and in a
    21-hour Nox most of an 8-hour cloud lit at hour 15 never happens.
    """
    late = [
        {"a": "drop", "unit": "h1", "at": [1, 1]},
        {"a": "step", "unit": "h1", "to": [1, 2]},
        {"a": "step", "unit": "h1", "to": [1, 3]},
        {"a": "emp_launch", "at": [[9, 9]]},
        {"a": "pickup", "unit": "h1"},
    ]
    out, notes = scorch.enforce_early_salvo(late)
    assert out[0]["a"] == "emp_launch"
    assert notes and "hour 4" in notes[0]
    # Hoisting pushes the rest back by one and preserves their order, so
    # nothing that depended on running after something else breaks.
    assert [m["a"] for m in out[1:]] == ["drop", "step", "step", "pickup"]


def test_a_salvo_already_at_the_front_is_left_alone():
    for slot in (0, 1):
        moves = [{"a": "probe", "at": [1, 1]}] * slot
        moves = moves + [{"a": "emp_launch", "at": [[9, 9]]},
                         {"a": "drop", "unit": "h1", "at": [2, 2]}]
        out, notes = scorch.enforce_early_salvo(moves)
        assert out == moves, f"hour {slot + 1} is already early enough"
        assert not notes


def test_the_guard_is_a_no_op_on_a_night_with_no_salvo():
    moves = [{"a": "drop", "unit": "h1", "at": [1, 1]}] * 5
    out, notes = scorch.enforce_early_salvo(moves)
    assert out == moves and not notes


def test_the_packagers_own_salvos_never_need_the_guard():
    """Belt and braces: the two mechanisms must agree, not fight."""
    view = _smear_view()
    reg = agency.build_registry(agent_view=view, enemy_probes=[])
    moves, _log = packager.pack_recipe(
        [_opt("chain", "CH1", {"drop_at": [25, 25], "cells": [[25, 26]]}),
         reg["BLIND_SCORCH"]],
        view, complete=False,
    )
    _out, notes = scorch.enforce_early_salvo(moves)
    assert not notes, (
        "_order_for_emp_cloud already fires at hour 1; if the guard has "
        "something to say here, the two passes disagree"
    )


# ── against the real engine ───────────────────────────────────────


def test_the_whole_shape_survives_its_own_cloud_in_the_simulator():
    """The claim, checked against the engine rather than against my reading.

    Everything above pins the agent's *model* of EMP. This drives the real
    :class:`NightSimulator`: if the cloud geometry, the 8-hour lifetime or
    the decay-before-disable ordering were even one hour off, the
    harvester sitting in the hole would come back disabled and the comb
    would bank nothing.
    """
    from sea_of_colours.game.policy import parse_moves
    from sea_of_colours.game.session import GameSession, Phase
    from sea_of_colours.game.simulator import NightSimulator
    from sea_of_colours.generator import Cell, Tile

    sess = GameSession.new(30, 20, seed=11, players=["p1", "p2"])
    sess.phase = Phase.PLANNING
    sess.weapon_stock["p1"] = {"emp": 1, "chaff": 0}

    region = [(x, y) for x in range(8, 14) for y in range(8, 13)]
    for x, y in region:
        sess.grid[y][x] = Cell(Tile.RED, 200)
    shape = scorch.shaped_salvo(region, radius=2, missiles=3)
    hole = shape["hole"]

    queue = [
        {"a": "emp_launch", "at": [list(c) for c in shape["targets"]]},
        {"a": "probe", "at": list(hole)},
        {"a": "drop", "unit": "harvester_p1", "at": list(hole)},
    ]
    queue += [{"a": "wait"} for _ in range(scorch.CLOUD_HOURS - 2)]
    walk = [(hole[0] + 1, hole[1]), (hole[0] + 2, hole[1])]
    queue += [{"a": "step", "unit": "harvester_p1", "to": list(c)}
              for c in walk]
    queue.append({"a": "pickup", "unit": "harvester_p1"})

    parsed, errs = parse_moves(queue)
    assert not errs, errs
    NightSimulator().run(sess, {"p1": parsed, "p2": []})
    frames = list(sess.last_night_replay or [])

    mine = [f for f in frames if f.get("owner") == "p1"]
    assert any(f.get("tag") == "emp_launch" for f in mine), "the salvo flew"
    # The hole is the whole point: a unit standing in it is never 'empd'.
    assert not [f for f in mine if f.get("tag") == "empd"], (
        "the harvester sat in the cell the salvo deliberately missed and "
        "must never be disabled by its own side's cloud"
    )
    # Asserted on replay frames rather than session state because the
    # frames are what the engine says happened, hour by hour — which is
    # the thing in question here.
    drop = next(f for f in mine if f.get("tag") == "drop")
    assert "auto-harvested" in str(drop.get("caption") or ""), (
        "the hole is outside the cloud, so the landing must harvest "
        "normally — if it does not, the salvo covered its own hole"
    )
    steps = [f for f in mine if f.get("tag") == "step"]
    assert len(steps) == len(walk), (
        "every step of the comb must land — the cloud is gone by then"
    )
    assert all("harvested RED" in str(f.get("caption") or "") for f in steps), (
        "a step into a STANDING cloud lands but banks nothing (§4.9.3); "
        "these run after it lifts, so every one must harvest"
    )
    lift = next(f for f in mine if f.get("tag") == "pickup")
    assert "banked 3 parcel" in str(lift.get("caption") or "")


# ── three weapons, one budget ─────────────────────────────────────
#
# This seat bought chaff from the day it was written and had no way to fire
# it: `chaff_flare` appeared nowhere but the last-night log reader, so every
# purchase was 300 blue — half the arsenal cap — on a charge with no exit from
# the rack. Adding the snap and chaff plays fixed that and introduced a new
# risk in its place, which is what most of these pin: three weapons sharing
# one 600-blue ceiling, where over-buying any of them prices out another.


def test_one_of_each_weapon_fits_the_arsenal_cap_exactly():
    """EMP 200 + chaff 300 + snap 100 is 600, and the cap is 600.

    There is no slack anywhere in this budget, which is why every stockpile
    cap is 1. Pinned against the engine's own prices so a rebalance fails here
    rather than silently in a season.
    """
    from sea_of_colours.game.weapons import (
        BLUE_COST_BY_KIND, WEAPONISED_BLUE_CAP,
    )
    d = orbit_policy.DEFAULT_DIALS
    held = (
        d.emp_stockpile_cap * BLUE_COST_BY_KIND["emp"]
        + d.chaff_stockpile_cap * BLUE_COST_BY_KIND["chaff"]
        + d.snap_stockpile_cap * BLUE_COST_BY_KIND["snap"]
    )
    assert held <= WEAPONISED_BLUE_CAP, (
        f"the caps want {held} blue of ordnance against a {WEAPONISED_BLUE_CAP}"
        " ceiling — the engine will refuse the last build and the seat will "
        "never learn which weapon it lost"
    )

    actions, _why = plan_orbit_actions(_buy_view(2000, WEAPONISED_BLUE_CAP))
    bought = {a["a"] for a in actions}
    assert {"build_emp", "build_chaff", "build_snap"} <= bought


def test_the_orbital_never_commits_the_same_blue_twice():
    """Each weapon must be priced against the blue the others already took.

    The forge's own add_procurement re-reads the bank off the view, so it
    could not see what the branches before it had spent: on a 200-blue night
    it watched the EMP take all 200 and then bought a snap with the same
    money. The engine refuses the second build, so the cost is not a crash —
    it is a seat that believes it is armed and is not.
    """
    from sea_of_colours.game.weapons import BLUE_COST_BY_KIND

    for blue in (100, 150, 200, 300, 400, 500, 600, 900):
        actions, _why = plan_orbit_actions(_buy_view(3000, blue))
        spent = sum(
            BLUE_COST_BY_KIND[a["a"][len("build_"):]]
            for a in actions
            if a["a"][len("build_"):] in BLUE_COST_BY_KIND
        )
        assert spent <= blue, (
            f"planned {spent} blue of ordnance out of a {blue} bank"
        )


def test_a_snap_is_not_bought_when_only_the_credits_are_missing():
    """Name the side that actually fell short, as the EMP branch does."""
    actions, why = plan_orbit_actions(_buy_view(0, 600))
    assert not [a for a in actions if a["a"] == "build_snap"]
    assert "credits 0/250" in why


# ── the plays are wired all the way to the wire ───────────────────


def test_the_fork_keeps_its_own_emp_compiler():
    """weapon_plays.py must never declare an EMP play.

    `weapon_forge.packers()` returns one packer per declared weapon and the
    dispatch hook is a dict update, so declaring an EMP play replaces this
    fork's four-beat BLIND_SCORCH — salvo, probe, drop into the hole, comb
    deferred to hour nine — with the forge's generic one-beat salvo. The rack
    would still empty and the log would still show a salvo, so the loss would
    only ever show up as a lower score.
    """
    assert "emp" not in {p.weapon for p in weapon_plays.PLAYS}
    assert packager._DISPATCH["emp"] is packager._pack_emp


def test_the_model_is_allowed_to_emit_every_weapon_verb():
    """The move enum is a hard wall: no verb, no play, and no error either."""
    enum = chat_schema._MOVE_ITEM["properties"]["a"]["enum"]
    for verb in ("emp_launch", "snap_launch", "chaff_flare"):
        assert verb in enum, f"the model physically cannot emit {verb}"


def test_widening_the_schema_never_drops_the_emp_verb():
    """The regression the union in chat_schema.py exists to prevent.

    `weapon_forge.widen_schema` REBUILDS the enum as the four base verbs plus
    the declared ones. This fork declares no EMP play, so calling it directly
    would delete `emp_launch` and silently disarm the fallback mover on the
    one weapon the seat was built around.
    """
    from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test import (
        weapon_forge,
    )
    naive = weapon_forge.widen_schema(chat_schema._V7_MOVE_ITEM)
    assert "emp_launch" not in naive["properties"]["a"]["enum"], (
        "if this starts passing, weapon_plays.py has grown an EMP play and "
        "test_the_fork_keeps_its_own_emp_compiler is the one to read"
    )
    assert "emp_launch" in chat_schema._MOVE_ITEM["properties"]["a"]["enum"]


def test_every_weapon_the_seat_holds_is_named_in_the_rack_block():
    """A weapon absent here is one the night phase plans as if it lacked."""
    block = prompt.format_rack_block(
        {"orbit": {"weapon_stock": {"emp": 1, "chaff": 1, "snap": 1}}},
    ).lower()
    for weapon in ("emp", "snap", "chaff"):
        assert weapon in block
    assert prompt.format_rack_block(
        {"orbit": {"weapon_stock": {"emp": 0, "chaff": 0, "snap": 0}}},
    ) == "", "an empty rack still says nothing"


def _weapon_board(weapon: str) -> dict:
    """A board rich enough for the snap and chaff targeting modes to fire.

    Three shapes are load-bearing, each added after a play was reported broken
    by a board that merely looked complete: a redsign with real ``cells`` (the
    smear is read off the view, so a region without them returns no targets),
    a rival eye close enough to the smear centre to be its finder, and a PURE
    inside that smear for ``contested_pure`` to contest.
    """
    smear = [[x, y] for x in range(18, 25) for y in range(18, 24)]
    return {
        "orbit": {
            "weapon_stock": {"emp": 0, "chaff": 0, "snap": 0, weapon: 1},
            "harvesters": [{"id": "harvester_p2"}],
            "probes": 4,
        },
        "my_assets": [
            {"kind": "harvester", "state": "orbit", "id": "harvester_p2"},
        ],
        "grid": {"width": 32, "height": 32},
        "probe_stock": 4,
        "redsign": [
            {"cells": smear, "mine": False, "center": [21, 20], "x": 21, "y": 20},
        ],
        "red_tiles": [{"x": 21, "y": 20, "purity": 255}],
        "enemy_probes": [{"x": 22, "y": 20}],
        "hud": {"day": 2, "season_day_cap": 7},
        "meta": {"player": "p1"},
    }


@pytest.mark.parametrize("weapon", ["snap", "chaff"])
def test_a_declared_play_compiles_to_a_move_the_engine_accepts(weapon):
    """The whole chain in one assertion: rack -> menu -> payload -> wire.

    Every rung above this one can pass while the seat still fires nothing.
    `check_wiring` proves `_DISPATCH` has a key for the kind; it does not call
    what it finds there, so a packer whose signature does not match the call
    site passes every check and raises the first time the model picks the play.

    The wire shapes are pinned against `game/policy.py`, which accepts
    `snap_launch` with `at` as a single `[x, y]` and `chaff_flare` with no
    `at` at all. A salvo's nested `[[x, y], ...]` is refused here by name.
    """
    from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test import (
        weapon_forge,
    )
    view = _weapon_board(weapon)
    options = weapon_forge.build_options(
        view, (), option_cls=agency.Option, present=[],
    )
    assert options, f"a {weapon} in the rack offered no play"

    for oid, opt in options.items():
        pk = packager._Packer(view)
        packager._DISPATCH[opt.kind](pk, opt.payload or {})
        fired = [m for m in pk.moves if m.get("a") != "wait"]
        assert fired, f"{oid} compiled to no move at all"
        shot = fired[0]
        if weapon == "snap":
            assert shot["a"] == "snap_launch"
            at = shot["at"]
            assert len(at) == 2 and all(isinstance(v, int) for v in at), (
                f"{oid} aimed at {at!r} — SNAP hits ONE cell and the engine "
                "refuses a nested list by name"
            )
        else:
            assert shot["a"] == "chaff_flare"
            assert "at" not in shot, "chaff takes no target"


def test_a_weapon_option_is_priced_in_what_the_rival_loses():
    """The reason the seat bought snaps for six seasons and fired none.

    The menu prints a structured `yield:` line and tells the model to rank on
    it. A weapon banks nothing, so the ordinary line reads
    `red ~+0 · blue 0 · green 0` — and next to a grab worth several hundred
    red, a denial play loses every comparison it is in. The rationale above it
    can be a thousand words of correct argument and it changes nothing,
    because the argument is not the axis being ranked.

    Live evidence before the fix: the seat held a snap on 21 planning turns,
    was offered one on 9 of them, and fired it 0 times.
    """
    from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test import (
        weapon_forge,
    )
    view = _weapon_board("snap")
    options = weapon_forge.build_options(
        view, (), option_cls=agency.Option, present=[],
    )
    assert options
    for oid, opt in options.items():
        line = (opt.payload or {}).get("denial_yield") or ""
        assert line, f"{oid} carries no denial yield, so it renders as ~+0"
        # Render it the way the prompt does, or the payload could carry a
        # perfectly good denial line that the menu never prints — which is
        # exactly the bug this pins.
        econ = {"yield": {}, "crush": {}, "risk": ("LOW", "nothing in view"),
                "walk": 0}
        text = "\n".join(agency._econ_detail_lines(opt, econ))
        assert "red ~+0" not in text, (
            f"{oid} still prices at zero on the line the model ranks on"
        )
        assert "DENIAL" in text or "RIVAL" in text, (
            f"{oid} renders no denial framing: {text!r}"
        )


def test_an_empty_rack_offers_no_weapon_play():
    """The seat must not be invited to fire a charge it does not hold."""
    from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test import (
        weapon_forge,
    )
    view = _weapon_board("snap")
    view["orbit"]["weapon_stock"] = {"emp": 0, "chaff": 0, "snap": 0}
    assert not weapon_forge.build_options(
        view, (), option_cls=agency.Option, present=[],
    )


def test_a_snap_that_fires_appears_in_the_seats_own_log():
    """`snap_launch` was in neither tag set, so the hour simply went missing.

    A seat that cannot see its own shot reads back a night in which it never
    fired, and re-plans the play it already made.
    """
    assert "snap_launch" in last_night._OWN_ACTION_TAGS
    assert "snap_launch" in last_night._PUBLIC_ORBITAL_TAGS
    assert "chaff_flare" in last_night._OWN_ACTION_TAGS
