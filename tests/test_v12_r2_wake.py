"""OBS-54 — two waves on one seam must not walk the same ground.

`V12_V11_R2_s56` night 5 is the case. The seat picked `BLIND_GRAB` +
`UNBEATEN_FLANK` — the sanctioned two-harvester attack — and the engine log
shows the flank re-walking four of the five cells the blind grab had just
stripped, in reverse order, paying -100 on every one:

    H04 step (32,9)  stepped GREEN -100      H10 drop (31,7)  stepped GREEN -100
    H05 step (32,8)  stepped GREEN -100      H11 step (32,7)  stepped GREEN -100
    H06 step (32,7)  stepped GREEN -100      H12 step (32,8)  stepped GREEN -100
    H07 step (31,7)  stepped GREEN -100      H13 step (32,9)  stepped GREEN -100

Half of those (-400) were the rival's leavings, which is OBS-53. The other half
were OUR OWN, which is this: `_comb_path` was blocking KNOWN green but not the
first wave's route, and two combs aimed at one beacon from opposite bearings
meet in the middle. `UNBEATEN_FLANK`'s own rationale promises "coverage, not a
repeat".
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import seam_control as sc

_W = _H = 40
_BEACON = (31, 7)


def _fogged_rival_seam(*, probe_stock: int = 4) -> Dict[str, Any]:
    """A rival beacon we can reach but cannot see into — the blind-attack case."""
    smear = [
        [_BEACON[0] + dx, _BEACON[1] + dy, 1.0]
        for dx in range(-2, 3) for dy in range(-2, 3)
        if 0 <= _BEACON[0] + dx < _W and 0 <= _BEACON[1] + dy < _H
    ]
    # One friendly disk in reach of the seam, so the geometry is buildable.
    own = [(29, 10)]
    return {
        "day": 5,
        "world": {"width": _W, "height": _H, "live": [
            {"x": 29, "y": 10, "tile": "RED", "purity": 40},
        ]},
        "red_tiles": [{"x": 29, "y": 10, "purity": 40, "freshness": "fresh"}],
        "redsign": [{"center": list(_BEACON), "mine": False, "found_day": 4,
                     "cells": smear}],
        "competitor_intel": {"new_this_day": [
            {"kind": "enemy_probe_launch", "at": [29, 10], "day_seen": 4},
        ]},
        "entities": {"mine": [
            {"type": "probe", "pos": [p[0], p[1]], "nights_remaining": 2}
            for p in own
        ]},
        "my_assets": [
            {"kind": "probe", "state": "deployed", "x": 29, "y": 10,
             "nights_left": 2},
            {"kind": "harvester", "state": "orbit", "id": "harvester_p1"},
            {"kind": "harvester", "state": "orbit", "id": "harvester_p1_2"},
        ],
        "probe_stock": probe_stock,
        "last_night": {"incoming_attacks": [], "emp_scars": []},
    }


def _route(p: sc.SeamPattern) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    for w in p.waves:
        if not w.deny_only:
            out.append(tuple(w.drop_at))
            out += [tuple(c) for c in w.comb_path]
    return out


def _by_id(view: Dict[str, Any]) -> Dict[str, sc.SeamPattern]:
    return {p.pattern_id: p for p in sc.build_seam_menu(view, [], probe_stock=4)}


def _seam(beacon: Tuple[int, int], own: Tuple[int, int],
          vals: List[Tuple[Tuple[int, int], int]]) -> Dict[str, Any]:
    smear = [
        [beacon[0] + dx, beacon[1] + dy, 1.0]
        for dx in range(-2, 3) for dy in range(-2, 3)
        if 0 <= beacon[0] + dx < _W and 0 <= beacon[1] + dy < _H
    ]
    live = [{"x": c[0], "y": c[1], "tile": "RED", "purity": p} for c, p in vals]
    live.append({"x": own[0], "y": own[1], "tile": "RED", "purity": 40})
    return {
        "day": 5,
        "world": {"width": _W, "height": _H, "live": live},
        "red_tiles": [{"x": r["x"], "y": r["y"], "purity": r["purity"],
                       "freshness": "fresh"} for r in live],
        "redsign": [{"center": list(beacon), "mine": False, "found_day": 4,
                     "cells": smear}],
        "competitor_intel": {"new_this_day": [
            {"kind": "enemy_probe_launch", "at": list(own), "day_seen": 4}]},
        "entities": {"mine": [
            {"type": "probe", "pos": list(own), "nights_remaining": 2}]},
        "my_assets": [
            {"kind": "probe", "state": "deployed", "x": own[0], "y": own[1],
             "nights_left": 2},
            {"kind": "harvester", "state": "orbit", "id": "harvester_p1"},
            {"kind": "harvester", "state": "orbit", "id": "harvester_p1_2"},
        ],
        "probe_stock": 4,
        "last_night": {"incoming_attacks": [], "emp_scars": []},
    }


def test_the_flank_never_re_walks_the_first_waves_route():
    """Swept rather than spot-checked, because the collision depends on where
    the value pulls both combs. Before the block this overlapped on 84 of 84
    configurations — i.e. the two-harvester attack ate its own wake every time
    it was played, not occasionally."""
    beacons = [(31, 7), (16, 6), (20, 20), (12, 9), (25, 14), (8, 30), (33, 5)]
    checked = 0
    for bx, by in beacons:
        for own in ((bx - 2, by + 3), (bx + 3, by + 2), (bx, by + 4),
                    (bx - 3, by - 2)):
            for vals in ([], [((bx + 1, by + 1), 200), ((bx - 1, by - 1), 180)],
                         [((bx, by), 240)]):
                pats = _by_id(_seam((bx, by), own, vals))
                flank = pats.get("UNBEATEN_FLANK")
                if flank is None:
                    continue
                flank_cells = set(_route(flank))
                for first in ("BLIND_GRAB", "BLIND_AND_GRAB", "SEEN_GRAB"):
                    wave1 = pats.get(first)
                    if wave1 is None:
                        continue
                    checked += 1
                    overlap = flank_cells & set(_route(wave1))
                    assert not overlap, (
                        f"beacon={(bx, by)} own={own}: {first} and "
                        f"UNBEATEN_FLANK share {sorted(overlap)}"
                    )
    assert checked >= 40, f"sweep degenerated to {checked} comparisons"


def test_the_flank_still_reaches_the_seam():
    """Blocking the wake must not empty the option — that would trade one
    failure for the ceding it was built to prevent."""
    flank = _by_id(_fogged_rival_seam()).get("UNBEATEN_FLANK")
    assert flank is not None
    assert flank.waves[0].comb_path, "flank lost its whole route to the block"


def test_a_stale_rival_sign_is_aged_but_its_jackpot_is_not_discounted():
    """v14 — the age is still tracked and still shown; the DISCOUNT is gone.

    OBS-53 read the age correctly and then drew the wrong conclusion from
    it. A sign a night old means the finder has had a turn on the halo, not
    on the pure: the engine retires a seam the instant its last pure is
    harvested (``_retire_redsign_if_spent``) and ``view.py`` ships only live
    regions, so a beacon you can still SEE still has its jackpot. The age
    belongs on the card as context for the ring; it does not belong in the
    jackpot term.
    """
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import (
        option_economics as oe,
    )
    view = _fogged_rival_seam()
    assert oe.sign_ages_by_beacon(view) == {_BEACON: 1}
    est = oe.blind_estimate([(32, 9), (32, 8), (32, 7)], view)
    assert est is not None
    assert est["pure_survival"] == 1.0
