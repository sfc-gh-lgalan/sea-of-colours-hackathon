"""R2.8 / R2.9 / R2.10 / R2.11 — what a redsign is worth, honestly.

Three separate defects converged on the same wrong answer: the agent declining a
redsign it should contest.

* R2.8  a blind comb's expectation was reported next to a per-CELL probability,
        so a 6-cell comb over a 25-cell smear read as a one-in-twenty-five
        raffle ticket — and was then compared against a juice chain's number,
        which carries no discount at all.
* R2.9  echo MASS minted no option anywhere, so the richest known ground on the
        board could be named in OUT-OF-GRID and be unreachable from the menu.
* R2.11 declining was priced at zero, when it hands a rival the pure.
* R2.10 a harvester was protected as if it had value; on the last night it has
        none at all.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import (
    agency, option_economics as oe, prompt as pr, value_pyramid as vp,
)

_W = _H = 40
_BEACON = (16, 6)


def _smear_view(*, comb: List[Tuple[int, int]]) -> Dict[str, Any]:
    """A fogged rival smear: 25 weighted cells, none of them visible."""
    cells = [
        {"x": _BEACON[0] + dx, "y": _BEACON[1] + dy, "intensity": 1.0}
        for dx in range(-2, 3) for dy in range(-2, 3)
    ]
    return {
        "day": 5,
        "world": {"width": _W, "height": _H, "live": []},
        "red_tiles": [],
        "redsign": [{"center": list(_BEACON), "mine": False, "found_day": 4,
                     "cells": cells}],
        "entities": {"mine": []},
        "my_assets": [],
        "probe_stock": 2,
        "last_night": {"incoming_attacks": [], "emp_scars": []},
    }


# ── R2.8 — pooled odds, not a per-cell fraction ─────────────────────────────
def _comb(n: int) -> List[Tuple[int, int]]:
    ring = [(_BEACON[0] + dx, _BEACON[1] + dy)
            for dx in range(-2, 3) for dy in range(-2, 3)]
    return ring[:n]


def test_the_odds_reported_are_for_the_whole_comb():
    """One cell of twenty-five is 4%; six cells is ~24%, and that is the bet."""
    cells = _comb(6)
    est = oe.blind_estimate(cells, _smear_view(comb=cells))
    assert est is not None
    assert est["pure_odds"] > est["best_pure_odds"] * 3
    assert 0.15 < est["pure_odds"] < 0.40


def test_a_longer_comb_reports_better_odds():
    view = _smear_view(comb=[])
    short = oe.blind_estimate(_comb(2), view)
    long = oe.blind_estimate(_comb(8), view)
    assert long["pure_odds"] > short["pure_odds"]


def test_the_jackpot_is_named_undiscounted():
    est = oe.blind_estimate(_comb(4), _smear_view(comb=[]))
    assert est["unclaimed_pure_pts"] == 765   # v1.13: 255 x 3.0, no transit


def test_the_line_says_the_two_numbers_are_not_like_for_like():
    est = oe.blind_estimate(_comb(6), _smear_view(comb=[]))
    line = agency._fmt_blind(est)
    assert "CROSSES the pure" in line
    assert "ALREADY discounted" in line
    assert "not discounted at all" in line


# ── R2.11 — the counterfactual ──────────────────────────────────────────────
def _seam_opt(beacon: Tuple[int, int], mine: bool) -> agency.Option:
    return agency.Option(
        option_id="BLIND_GRAB", kind="seam", title="t", detail="d",
        execute_lines=["x"],
        payload={"beacon": list(beacon), "mine": mine, "waves": []},
    )


def _fresh_view() -> Dict[str, Any]:
    """A sign broadcast TONIGHT — nobody has had a turn on it yet."""
    v = _smear_view(comb=[])
    v["redsign"][0]["found_day"] = 5
    return v


def test_declining_is_priced_as_a_swing_not_a_zero():
    lines = agency._ceding_lines([_seam_opt(_BEACON, False)], _fresh_view())
    assert len(lines) == 1
    text = lines[0]
    assert "IF YOU DO NOT CONTEST (16,6)" in text
    assert "765" in text          # what a rival banks
    assert "1530" in text         # the swing (2 x 765)
    assert "not against 0" in text


# ── OBS-53 — a sign the finder has held is not a jackpot any more ───────────
def test_a_night_old_rival_sign_is_not_sold_as_a_live_jackpot():
    """`V12_V11_R2_s56` night 5: the seat read "+765 to whoever banks it" about a
    seam the finder had owned for a night, walked five cells and found every one
    stripped — 7 points of trace against four -100 penalties."""
    text = agency._ceding_lines([_seam_opt(_BEACON, False)], _smear_view(comb=[]))[0]
    assert "PROBABLY ALREADY BANKED" in text
    assert "broadcast 1 night(s) ago" in text
    assert "UNWORKED HALO" in text
    assert "-100 a cell" in text


def test_our_own_sign_is_never_discounted():
    """If our pure had been taken, we took it — the cell would read green."""
    text = agency._ceding_lines([_seam_opt(_BEACON, True)], _smear_view(comb=[]))[0]
    assert "PROBABLY ALREADY BANKED" not in text


def test_a_visible_beacon_is_never_discounted_for_age():
    """v14 — ``_pure_survival`` is 1.0 always, and that is the ENGINE's rule.

    This used to assert the opposite: a rival sign held for a night priced
    its jackpot at 35%, two nights at 10%. The decay was guessing at
    something the engine already decides for us.
    ``GameSession._retire_redsign_if_spent`` flips ``region["live"]`` off the
    moment the last pure cell of a seam is harvested, and ``view.py`` ships
    only regions with ``live`` true — a spent beacon LEAVES the seat view
    rather than going grey in it. So a redsign you can see has a pure on it,
    by construction (RULEBOOK §4.11).

    The old model therefore cut the expected yield of every redsign attack
    by two thirds or more and made contesting a rival's seam read as a bad
    bet when it was the best play on the board.
    """
    fresh = oe.blind_estimate(_comb(6), _fresh_view())
    stale = oe.blind_estimate(_comb(6), _smear_view(comb=[]))
    assert fresh["pure_survival"] == 1.0
    assert stale["pure_survival"] == 1.0
    assert stale["pure_odds"] == fresh["pure_odds"]   # geometry is unchanged


def test_the_halo_still_decays_even_though_the_pure_does_not():
    """What a night of rival work costs is the RING, not the jackpot.

    The pure is guaranteed by the beacon still being visible; the mass
    around it is not, because that is exactly what the finder has been
    walking. That term is measured off the seam's own observed
    density/purity rather than assumed, so it stays honest without
    double-counting the pure.
    """
    line = agency._fmt_blind(oe.blind_estimate(_comb(6), _smear_view(comb=[])))
    assert "FINDER has held exact vision" in line
    assert "UNWORKED HALO" in line


def test_your_own_seam_is_named_as_yours():
    lines = agency._ceding_lines([_seam_opt(_BEACON, True)], _smear_view(comb=[]))
    assert "YOUR seam" in lines[0]


def test_each_beacon_is_priced_once():
    opts = [_seam_opt(_BEACON, False), _seam_opt(_BEACON, False),
            _seam_opt((30, 20), False)]
    assert len(agency._ceding_lines(opts, _smear_view(comb=[]))) == 2


# ── R2.9 — echo mass reaches the menu ───────────────────────────────────────
def _echo_view(*, purity: int, walkable: bool) -> Dict[str, Any]:
    """An echo cell out of live vision. ``walkable`` puts a live frontier beside
    it so a probe-free walk-in exists; otherwise only a hot drop can reach it."""
    live = (
        [{"x": 17, "y": 13, "tile": "RED", "purity": 60}] if walkable else []
    )
    return {
        "day": 5,
        "world": {"width": _W, "height": _H, "live": live},
        "red_tiles": (
            [{"x": 19, "y": 13, "purity": purity, "freshness": "stale"}]
            + [{"x": 17, "y": 13, "purity": 60, "freshness": "fresh"}
               if walkable else {}]
        ),
        "redsign": [],
        "entities": {"mine": (
            [{"type": "probe", "pos": [17, 13], "nights_remaining": 2}]
            if walkable else []
        )},
        "my_assets": [
            {"kind": "harvester", "state": "orbit", "id": "harvester_p1"},
            {"kind": "harvester", "state": "orbit", "id": "harvester_p1_2"},
        ],
        "probe_stock": 2,
        "last_night": {"incoming_attacks": [], "emp_scars": []},
    }


def test_echo_mass_no_walk_can_reach_still_gets_an_option():
    """s69 night 5: 578-710 points of echo mass at (19,13), named in OUT-OF-GRID
    and takeable by no id on the menu."""
    hints = vp.force_surface_echo_hotdrops(_echo_view(purity=200, walkable=False))
    assert hints, "echo mass minted no option"
    assert hints[0]["exact_cell"] == [19, 13]


def test_walkable_echo_mass_is_taken_on_foot_rather_than_with_a_probe():
    specs = vp.force_surface_grabs(_echo_view(purity=200, walkable=True))
    masses = [s for s in specs if s.tier == "mass"]
    assert masses, [s.action for s in specs]
    assert masses[0].provenance == "ECHO"
    assert (19, 13) in masses[0].cells or masses[0].target == (19, 13)


# ── R2.10 — what a unit is worth ────────────────────────────────────────────
def test_the_final_night_says_a_harvester_is_worth_nothing():
    line = pr._harvester_worth_line(day=7, day_cap=7, vault_score=1200)
    assert "WORTH TONIGHT: NOTHING" in line
    assert "Deploy EVERY alive harvester" in line


def test_earlier_nights_price_the_unit_off_the_seats_own_record():
    """1200 banked over 4 played nights, 2 nights left after tonight -> ~600."""
    line = pr._harvester_worth_line(day=5, day_cap=7, vault_score=1200)
    assert "~600 points" in line
    assert "idle unit banks zero" in line


def test_a_seat_that_has_banked_nothing_still_gets_the_shape_of_the_answer():
    line = pr._harvester_worth_line(day=3, day_cap=7, vault_score=0)
    assert "only the harvests it has left" in line
    assert "4 more night(s)" in line
