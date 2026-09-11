"""Unit tests for the tabula_v7 audit-fix batch.

Offline, no network. Covers the structural guardrails added to fix the
recurring failure modes: redsign region parsing, enemy-probe collection +
final-night supersede hints, Euclidean drop-legal parity, and the
mechanical move sanitizer (no-beacon drop, self-crush nuance, harvester
collision, crash-guard pickups).
"""

from __future__ import annotations

from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7 import probe_hints as ph
from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7 import move_sanitizer as ms
from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7.prompt import (
    _euclidean_drop_rows,
    format_supersede_hints_block,
    format_last_night_block,
    format_reflect_block,
    _format_combat_event,
    _my_jam_events,
)


# ── redsign parsing ───────────────────────────────────────────────────


def test_redsign_parses_region_cells():
    view = {
        "redsign": [
            {
                "id": "rs1",
                "center": [10, 12],
                "cells": [[10, 12, 0.9], [11, 12, 0.4], [10, 13, 0.3]],
                "day": 2,
                "hour": 5,
            }
        ]
    }
    cells = ph._redsign_cells(view)
    assert (10, 12) in cells
    assert (11, 12) in cells
    assert (10, 13) in cells
    assert cells[(10, 12)] == 5  # region hour recorded as freshness


def test_redsign_center_fallback_when_no_cells():
    view = {"redsign": [{"id": "rs2", "center": [3, 4], "hour": 1}]}
    cells = ph._redsign_cells(view)
    assert (3, 4) in cells


def test_redsign_legacy_flat_shapes_still_parse():
    view = {"redsign": [{"x": 7, "y": 8, "hour": 2}, [1, 2, 3]]}
    cells = ph._redsign_cells(view)
    assert (7, 8) in cells and (1, 2) in cells


def test_redsign_centers():
    view = {"redsign": [{"center": [10, 12], "cells": [[10, 12, 0.9]], "hour": 1}]}
    assert ph._redsign_centers(view) == [(10, 12)]


# ── enemy probe collection + supersede ────────────────────────────────


def test_enemy_probe_cells_dedup_and_order():
    view = {
        "competitor_intel": {
            "new_this_day": [
                {"kind": "enemy_probe_launch", "at": [5, 5], "day_seen": 6},
            ],
            "persistent_echoes": [
                {"kind": "enemy_probe", "at": [9, 9], "last_seen_day": 3},
                {"kind": "enemy_probe", "at": [5, 5], "last_seen_day": 2},
            ],
        }
    }
    cells = ph._enemy_probe_cells(view)
    coords = [c["at"] for c in cells]
    assert (5, 5) in coords and (9, 9) in coords
    # deduped: (5,5) appears once, freshest day (6) wins ordering-first.
    assert coords.count((5, 5)) == 1
    assert coords[0] == (5, 5)  # day_seen 6 is freshest


def test_supersede_hints_need_stock():
    view = {
        "orbit": {"probe_stock": 0},
        "competitor_intel": {
            "new_this_day": [{"kind": "enemy_probe_launch", "at": [5, 5], "day_seen": 6}]
        },
    }
    assert ph.top_supersede_hints(view) == []


def test_supersede_hints_target_enemy_probes():
    view = {
        "orbit": {"probe_stock": 3},
        "competitor_intel": {
            "new_this_day": [
                {"kind": "enemy_probe_launch", "at": [5, 5], "day_seen": 6},
                {"kind": "enemy_probe_launch", "at": [8, 2], "day_seen": 6},
            ]
        },
    }
    hints = ph.top_supersede_hints(view)
    targets = [tuple(h["probe_at"]) for h in hints]
    assert (5, 5) in targets and (8, 2) in targets


def test_supersede_skips_cell_we_already_occupy():
    view = {
        "orbit": {"probe_stock": 3},
        "competitor_intel": {
            "new_this_day": [{"kind": "enemy_probe_launch", "at": [5, 5], "day_seen": 6}]
        },
        "entities": {"mine": [{"type": "probe", "pos": [5, 5], "nights_remaining": 2}]},
    }
    assert ph.top_supersede_hints(view) == []


# ── Euclidean drop-legal parity ───────────────────────────────────────


def test_euclidean_drop_rows_excludes_box_corners():
    rows = _euclidean_drop_rows(20, 14, 4, 40, 28)
    disk = {(x, y) for (y, xmin, xmax) in rows for x in range(xmin, xmax + 1)}
    # centre in; the 9x9 box corner (16,10) is dist^2=32>16 -> excluded.
    assert (20, 14) in disk
    assert (16, 10) not in disk
    assert (24, 18) not in disk
    # an on-disk edge cell is included.
    assert (24, 14) in disk  # dx=4,dy=0 -> 16 <= 16


# ── move sanitizer ────────────────────────────────────────────────────


def _view(*, width=20, height=20, live=None, probes=None, harvesters=None,
          red=None, green=None):
    """Minimal agent_view for sanitizer tests."""
    live_rows = []
    for (x, y) in (live or []):
        live_rows.append({"x": x, "y": y, "tile": "RED"})
    for (x, y) in (green or []):
        live_rows.append({"x": x, "y": y, "tile": "GREEN", "lineage": "natural"})
    mine = []
    my_assets = []
    for p in (probes or []):
        mine.append({"type": "probe", "pos": list(p[:2]),
                     "nights_remaining": (p[2] if len(p) > 2 else 3)})
    for hv in (harvesters or []):
        mine.append({"type": "harvester", "id": hv[0],
                     "pos": (list(hv[1]) if hv[1] else None)})
        # Orbit harvesters (pos None) also surface in my_assets — this is
        # what _orbit_harvester_ids (deploy-all guard) reads.
        my_assets.append({
            "id": hv[0], "kind": "harvester",
            "state": "surface" if hv[1] else "orbit",
        })
    red_rows = [{"x": x, "y": y, "purity": p} for (x, y, p) in (red or [])]
    return {
        "world": {"width": width, "height": height, "live": live_rows},
        "entities": {"mine": mine},
        "my_assets": my_assets,
        "red_tiles": red_rows,
    }


def test_sanitizer_reroutes_illegal_no_vision_drop():
    # (5,5) not in live vision; (5,6) is. Drop should reroute to a legal
    # adjacent cell rather than being submitted as a doomed drop.
    view = _view(live=[(5, 6), (5, 7)],
                 harvesters=[("h1", None)])
    moves = [
        {"a": "drop", "unit": "h1", "at": [5, 5]},
        {"a": "pickup", "unit": "h1"},
    ]
    out, log = ms.sanitize_moves(moves, view)
    drop = next(m for m in out if m["a"] == "drop")
    assert tuple(drop["at"]) == (5, 6)
    assert any("rerouted drop" in s for s in log)


def test_sanitizer_removes_drop_with_no_legal_landing():
    view = _view(live=[], harvesters=[("h1", None)])
    moves = [
        {"a": "drop", "unit": "h1", "at": [5, 5]},
        {"a": "step", "unit": "h1", "to": [5, 6]},
        {"a": "pickup", "unit": "h1"},
    ]
    out, log = ms.sanitize_moves(moves, view)
    assert not any(m["a"] == "drop" for m in out)
    assert not any(m["a"] == "step" for m in out)
    assert any("removed drop chain" in s for s in log)


def test_sanitizer_preserves_probe_when_no_loot():
    # Probe at (5,5) with 3 nights, no red on it, legal adjacent (6,5).
    view = _view(live=[(5, 5), (6, 5)],
                 probes=[(5, 5, 3)],
                 harvesters=[("h1", None)])
    moves = [
        {"a": "drop", "unit": "h1", "at": [5, 5]},
        {"a": "pickup", "unit": "h1"},
    ]
    out, log = ms.sanitize_moves(moves, view)
    drop = next(m for m in out if m["a"] == "drop")
    assert tuple(drop["at"]) != (5, 5)
    assert any("spare a probe" in s for s in log)


def test_sanitizer_absorbs_first_step_to_spare_probe_with_chain():
    # Drop lands on our own probe (>=2 nights, no loot on the exact cell) AND
    # a harvest chain follows. The probe centre is empty, so we ABSORB the
    # first step: land directly on the chain's first real cell (6,5), spare
    # the probe, and keep the whole seam. Strictly better than crushing.
    view = _view(live=[(5, 5), (6, 5), (7, 5), (8, 5)],
                 probes=[(5, 5, 3)],
                 red=[(6, 5, 90), (7, 5, 80), (8, 5, 70)],
                 harvesters=[("h1", None)])
    moves = [
        {"a": "drop", "unit": "h1", "at": [5, 5]},   # on own probe, no loot
        {"a": "step", "unit": "h1", "to": [6, 5]},
        {"a": "step", "unit": "h1", "to": [7, 5]},
        {"a": "step", "unit": "h1", "to": [8, 5]},
        {"a": "pickup", "unit": "h1"},
    ]
    out, log = ms.sanitize_moves(moves, view)
    drop = next(m for m in out if m["a"] == "drop")
    assert tuple(drop["at"]) == (6, 5)  # probe (5,5) spared, land on first cell
    steps = [tuple(m["to"]) for m in out if m["a"] == "step"]
    assert steps == [(7, 5), (8, 5)]  # first step absorbed; rest banked
    assert any("absorbed first step" in s for s in log)


def test_sanitizer_keeps_crush_when_first_step_not_landable():
    # Chain follows but the first step cell is itself a probe (can't absorb
    # onto it), so we fall back to keeping the crush rather than orphaning
    # the chain.
    view = _view(live=[(5, 5), (6, 5), (7, 5)],
                 probes=[(5, 5, 3), (6, 5, 3)],
                 red=[(7, 5, 90)],
                 harvesters=[("h1", None)])
    moves = [
        {"a": "drop", "unit": "h1", "at": [5, 5]},   # on own probe, no loot
        {"a": "step", "unit": "h1", "to": [6, 5]},   # first cell is ALSO a probe
        {"a": "step", "unit": "h1", "to": [7, 5]},
        {"a": "pickup", "unit": "h1"},
    ]
    out, log = ms.sanitize_moves(moves, view)
    drop = next(m for m in out if m["a"] == "drop")
    assert tuple(drop["at"]) == (5, 5)  # crush kept (no legal first-step)
    assert any("kept crush" in s and "chain follows" in s for s in log)


def test_sanitizer_still_spares_probe_for_lone_drop():
    # Same probe-on-cell, but NO chain (lone drop+pickup) -> reroute to spare
    # the probe (nothing to orphan).
    view = _view(live=[(5, 5), (6, 5)], probes=[(5, 5, 3)],
                 harvesters=[("h1", None)])
    moves = [
        {"a": "drop", "unit": "h1", "at": [5, 5]},
        {"a": "pickup", "unit": "h1"},
    ]
    out, log = ms.sanitize_moves(moves, view)
    drop = next(m for m in out if m["a"] == "drop")
    assert tuple(drop["at"]) != (5, 5)
    assert any("lone drop" in s for s in log)


def test_sanitizer_rethreads_chain_after_drop_reroute():
    # Illegal drop at (5,5) (no vision); (6,5) is legal. The reroute shifts
    # the drop +1 in x; the following steps (planned from (5,5)) must be
    # translated by the same delta so the chain stays contiguous instead of
    # truncating. Live vision covers the shifted path.
    view = _view(live=[(6, 5), (7, 5), (7, 6), (7, 7)],
                 red=[(7, 5, 90), (7, 6, 80), (7, 7, 70)],
                 harvesters=[("h1", None)])
    moves = [
        {"a": "drop", "unit": "h1", "at": [5, 5]},   # not in live -> reroute (6,5)
        {"a": "step", "unit": "h1", "to": [6, 5]},   # +delta -> (7,5)
        {"a": "step", "unit": "h1", "to": [6, 6]},   # +delta -> (7,6)
        {"a": "step", "unit": "h1", "to": [6, 7]},   # +delta -> (7,7)
        {"a": "pickup", "unit": "h1"},
    ]
    out, log = ms.sanitize_moves(moves, view)
    drop = next(m for m in out if m["a"] == "drop")
    assert tuple(drop["at"]) == (6, 5)
    steps = [tuple(m["to"]) for m in out if m["a"] == "step"]
    assert steps == [(7, 5), (7, 6), (7, 7)]  # re-threaded, full chain kept
    assert any("re-threading chain" in s for s in log)


def test_sanitizer_allows_crush_when_loot_underneath():
    # Red purity on the probe cell -> justified crush, keep the drop.
    view = _view(live=[(5, 5), (6, 5)],
                 probes=[(5, 5, 3)],
                 red=[(5, 5, 200)],
                 harvesters=[("h1", None)])
    moves = [
        {"a": "drop", "unit": "h1", "at": [5, 5]},
        {"a": "pickup", "unit": "h1"},
    ]
    out, _ = ms.sanitize_moves(moves, view)
    drop = next(m for m in out if m["a"] == "drop")
    assert tuple(drop["at"]) == (5, 5)


def test_sanitizer_allows_crush_when_probe_expiring():
    view = _view(live=[(5, 5), (6, 5)],
                 probes=[(5, 5, 1)],
                 harvesters=[("h1", None)])
    moves = [{"a": "drop", "unit": "h1", "at": [5, 5]},
             {"a": "pickup", "unit": "h1"}]
    out, _ = ms.sanitize_moves(moves, view)
    drop = next(m for m in out if m["a"] == "drop")
    assert tuple(drop["at"]) == (5, 5)


def test_sanitizer_allows_step_into_green():
    """v1.48 — walking over green is legal, so the chain survives.

    This test used to assert the opposite. Nothing in the engine refuses a
    step onto stripped ground: it costs -100 at settlement, which is a price
    the agent is entitled to pay to reach what is behind it. The sanitizer
    was truncating real chains on a rule that did not exist.
    """
    view = _view(live=[(5, 5), (5, 6)], green=[(6, 5)],
                 harvesters=[("h1", None)])
    moves = [
        {"a": "drop", "unit": "h1", "at": [5, 5]},
        {"a": "step", "unit": "h1", "to": [6, 5]},  # green — legal, just costly
        {"a": "pickup", "unit": "h1"},
    ]
    out, log = ms.sanitize_moves(moves, view)
    step = next(m for m in out if m["a"] == "step" and m["unit"] == "h1")
    assert tuple(step["to"]) == (6, 5)
    assert any(m["a"] == "pickup" and m["unit"] == "h1" for m in out)
    assert not any("truncated chain" in s for s in log)


def test_sanitizer_still_refuses_a_drop_onto_green():
    """The step veto went; the DROP veto stays.

    Landing on stripped ground banks nothing and still pays -100 — self-harm
    with no upside, and distinct from crossing it to reach a mass. Pins that
    v1.48 removed one guard and not both.
    """
    view = _view(live=[(5, 5), (5, 6), (6, 5)], green=[(6, 5)],
                 harvesters=[("h1", None)])
    moves = [
        {"a": "drop", "unit": "h1", "at": [6, 5]},
        {"a": "pickup", "unit": "h1"},
    ]
    out, log = ms.sanitize_moves(moves, view)
    drop = next((m for m in out if m["a"] == "drop" and m["unit"] == "h1"), None)
    assert drop is None or tuple(drop["at"]) != (6, 5)


def test_sanitizer_deconflicts_harvester_collision():
    view = _view(live=[(5, 5), (6, 5), (5, 6)],
                 harvesters=[("h1", None), ("h2", None)])
    moves = [
        {"a": "drop", "unit": "h1", "at": [5, 5]},
        {"a": "pickup", "unit": "h1"},
        {"a": "drop", "unit": "h2", "at": [5, 5]},  # collides with h1
        {"a": "pickup", "unit": "h2"},
    ]
    out, log = ms.sanitize_moves(moves, view)
    h2_drop = next(m for m in out if m["a"] == "drop" and m["unit"] == "h2")
    assert tuple(h2_drop["at"]) != (5, 5)
    assert any("collision" in s for s in log)


def test_sanitizer_inserts_missing_pickup_crash_guard():
    view = _view(live=[(5, 5)], harvesters=[("h1", None)])
    moves = [{"a": "drop", "unit": "h1", "at": [5, 5]}]  # no pickup!
    out, log = ms.sanitize_moves(moves, view)
    assert any(m["a"] == "pickup" and m["unit"] == "h1" for m in out)
    assert any("crash guard" in s for s in log)


def test_supersede_block_empty_when_no_hints():
    assert format_supersede_hints_block([]) == ""
    block = format_supersede_hints_block([{"probe_at": [5, 5], "day_seen": 6}])
    assert "SUPERSEDE HINTS" in block and "(5,5)" in block


# ── hot-drop hint never crushes its own probe ─────────────────────────


def _fog_view(*, width=40, height=28, redsign=None, blue_sign=None):
    """All-fog night-1 view with a probe in stock + a harvester in orbit."""
    return {
        "orbit": {"probe_stock": 3},
        "my_assets": [
            {"id": "harvester_p1", "kind": "harvester", "state": "orbit"},
        ],
        "world": {"width": width, "height": height, "live": []},
        "meta": {"rules": {"probe_radius": 4, "probe_lifetime_nights": 3}},
        "redsign": redsign or [],
        "blue_sign": blue_sign or [],
        "blue_tiles": [],
    }


def test_hot_drop_hint_never_equals_probe_and_drop_night1():
    # All-fog board: every candidate ties on area_gain. The old bug picked
    # the target as the probe centre AND the drop -> guaranteed crush.
    view = _fog_view(redsign=[
        {"center": [20, 14], "cells": [[20, 14, 0.9]], "hour": 1},
    ])
    hints = ph.top_hot_drop_hints(view, max_hints=2)
    assert hints, "expected a redsign hot-drop hint"
    for h in hints:
        assert h["probe_at"] != h["drop_at"], (
            f"drop lands on probe centre (crush): {h}"
        )
        # drop must still be within the probe's Euclidean r4 disk.
        px, py = h["probe_at"]
        dx, dy = h["drop_at"]
        assert (px - dx) ** 2 + (py - dy) ** 2 <= 16


def test_hot_drop_hint_carries_note():
    view = _fog_view(blue_sign=[
        {"cells": [[10, 10, 0.9], [11, 10, 0.8]]},
    ])
    hints = ph.top_hot_drop_hints(view, max_hints=1)
    assert hints and "note" in hints[0]


def test_hot_drop_hint_carries_comb_path_and_fog_flag():
    # Redsign beacon in an all-fog board: the combo must carry a multi-step
    # comb (the "comb, don't snatch" guarantee) and flag the beacon as in fog.
    view = _fog_view(redsign=[
        {"center": [20, 14], "cells": [[20, 14, 0.9]], "hour": 1},
    ])
    hints = ph.top_hot_drop_hints(view, max_hints=1)
    assert hints
    h = hints[0]
    assert h.get("target_in_fog") is True  # beacon not in any live disk
    comb = h.get("comb_path")
    assert isinstance(comb, list) and len(comb) >= 3, (
        f"expected a multi-step comb, got {comb}"
    )
    # Every comb cell is inside the probe's Euclidean r4 disk (drop-legal
    # once the probe lands) and the walk is contiguous Manhattan-1.
    px, py = h["probe_at"]
    chain = [tuple(h["drop_at"])] + [tuple(c) for c in comb]
    for (x, y) in chain:
        assert (px - x) ** 2 + (py - y) ** 2 <= 16
    for a, b in zip(chain, chain[1:]):
        assert abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1
    # No cell is revisited (avoid banking a self-made -100 green wake).
    assert len(set(chain)) == len(chain)


def test_comb_path_avoids_known_green():
    # A known green cell inside the disk must never appear in the comb.
    green_cell = (21, 14)
    view = _fog_view(redsign=[
        {"center": [20, 14], "cells": [[20, 14, 0.9]], "hour": 1},
    ])
    view["world"]["live"] = [
        {"x": green_cell[0], "y": green_cell[1], "tile": "GREEN",
         "lineage": "natural"},
    ]
    hints = ph.top_hot_drop_hints(view, max_hints=1)
    assert hints
    comb = [tuple(c) for c in hints[0].get("comb_path") or []]
    assert green_cell not in comb


# ── sanitizer: same-turn probe + step crush ───────────────────────────


def test_sanitizer_catches_same_turn_probe_then_drop_crush():
    # probe X then drop X in the SAME plan must be rerouted off X.
    view = _view(width=40, height=28, live=[], harvesters=[("h1", None)])
    view["meta"] = {"rules": {"probe_lifetime_nights": 3}}
    moves = [
        {"a": "probe", "at": [20, 14]},
        {"a": "drop", "unit": "h1", "at": [20, 14]},  # would crush same-turn probe
        {"a": "pickup", "unit": "h1"},
    ]
    out, log = ms.sanitize_moves(moves, view)
    drop = next(m for m in out if m["a"] == "drop")
    assert tuple(drop["at"]) != (20, 14)
    assert any("spare a probe" in s for s in log)


def test_sanitizer_truncates_step_onto_own_probe():
    view = _view(live=[(5, 5), (6, 5)], probes=[(6, 5, 3)],
                 harvesters=[("h1", None)])
    moves = [
        {"a": "drop", "unit": "h1", "at": [5, 5]},
        {"a": "step", "unit": "h1", "to": [6, 5]},  # steps onto own probe (no loot)
        {"a": "pickup", "unit": "h1"},
    ]
    out, log = ms.sanitize_moves(moves, view)
    assert not any(m["a"] == "step" for m in out)
    assert any(m["a"] == "pickup" and m["unit"] == "h1" for m in out)
    assert any("crush own probe" in s for s in log)


def test_sanitizer_allows_step_onto_probe_with_loot():
    view = _view(live=[(5, 5), (6, 5)], probes=[(6, 5, 3)],
                 red=[(6, 5, 200)], harvesters=[("h1", None)])
    moves = [
        {"a": "drop", "unit": "h1", "at": [5, 5]},
        {"a": "step", "unit": "h1", "to": [6, 5]},  # loot on cell -> allowed crush
        {"a": "pickup", "unit": "h1"},
    ]
    out, _ = ms.sanitize_moves(moves, view)
    assert any(m["a"] == "step" and tuple(m["to"]) == (6, 5) for m in out)


# ── chaff / EMP surfacing ─────────────────────────────────────────────


def test_combat_event_chaff_jam_is_explicit():
    ev = {"type": "chaff_jam", "victim": "p1", "by": ["p2"],
          "units": ["harvester_p1"], "hours": [6, 7, 8]}
    text = _format_combat_event(ev)
    assert "CHAFFED" in text and "[6, 7, 8]" in text


def test_combat_event_emp_hit_is_explicit():
    ev = {"type": "emp_hit", "by": ["p2"], "at": [12, 9], "hours": [3, 4]}
    text = _format_combat_event(ev)
    assert "EMP" in text and "(12,9)" in text


def test_my_jam_events_filters_to_hits():
    view = {"last_night": {"combat_events": [
        {"type": "chaff", "owner": "p2", "hours": [6]},          # public, not a hit
        {"type": "chaff_jam", "victim": "p1", "hours": [6, 7, 8]},  # a hit
    ]}}
    jams = _my_jam_events(view)
    assert len(jams) == 1 and jams[0]["type"] == "chaff_jam"


def test_last_night_block_renders_chaff_detail():
    view = {"last_night": {
        "day_ended": 3,
        "combat_events": [
            {"type": "chaff_jam", "victim": "p1", "by": ["p2"],
             "units": ["harvester_p1"], "hours": [6, 7, 8]},
        ],
    }}
    block = format_last_night_block(view)
    assert "CHAFFED" in block and "opponent action affected you" not in block


# ── (B) sanitizer probe guard: self-supersede + final-night frontier ──


def test_sanitizer_drops_self_supersede_probe():
    # A probe launched onto a cell where we ALREADY have a live probe is a
    # self-supersede — destroys our own vision for nothing. Drop it.
    view = _view(width=40, height=28, live=[(10, 9)], probes=[(10, 9, 1)],
                 harvesters=[("h1", None)])
    moves = [{"a": "probe", "at": [10, 9]}]
    out, log = ms.sanitize_moves(moves, view)
    assert not any(m["a"] == "probe" for m in out)
    assert any("OWN" in s for s in log)


def test_sanitizer_drops_final_night_frontier_probe():
    view = _view(width=40, height=28, live=[], harvesters=[("h1", None)])
    view["meta"] = {"rules": {"probe_lifetime_nights": 3}}
    moves = [{"a": "probe", "at": [7, 4]}]
    out, log = ms.sanitize_moves(moves, view, is_final_night=True)
    assert not any(m["a"] == "probe" for m in out)
    assert any("final-night probe" in s for s in log)


def test_sanitizer_keeps_final_night_hotdrop_probe():
    # A final-night probe whose disk contains a friendly drop is a HOT-DROP
    # enabler (harvest THIS night), NOT a wasteful frontier scout. It must
    # be kept, or the drop it enables loses its vision and gets stranded.
    view = _view(width=40, height=28, live=[], harvesters=[("h1", None)])
    view["meta"] = {"rules": {"probe_lifetime_nights": 3}}
    moves = [
        {"a": "probe", "at": [3, 8]},                 # hot-drop probe
        {"a": "drop", "unit": "h1", "at": [1, 10]},   # lands in (3,8)'s disk
        {"a": "step", "unit": "h1", "to": [2, 10]},
        {"a": "pickup", "unit": "h1"},
    ]
    out, log = ms.sanitize_moves(moves, view, is_final_night=True)
    assert any(m["a"] == "probe" and tuple(m["at"]) == (3, 8) for m in out)
    # and the harvester it enables survives (drop kept).
    assert any(m["a"] == "drop" and tuple(m["at"]) == (1, 10) for m in out)
    assert not any("frontier scout" in s for s in log)


def test_sanitizer_still_drops_final_night_scout_with_far_drop():
    # A final-night probe with NO drop in its disk is still a wasteful
    # scout even if the plan has an (unrelated, far-away) drop.
    view = _view(width=40, height=28, live=[(30, 20)], harvesters=[("h1", None)])
    moves = [
        {"a": "probe", "at": [3, 8]},                  # far from the drop
        {"a": "drop", "unit": "h1", "at": [30, 20]},   # not in (3,8)'s disk
        {"a": "pickup", "unit": "h1"},
    ]
    out, log = ms.sanitize_moves(moves, view, is_final_night=True)
    assert not any(m["a"] == "probe" for m in out)
    assert any("frontier scout" in s for s in log)


def test_sanitizer_keeps_final_night_supersede_probe():
    # A final-night probe onto a known ENEMY probe cell is a legit denial
    # play — keep it.
    view = _view(width=40, height=28, live=[], harvesters=[("h1", None)])
    moves = [{"a": "probe", "at": [12, 5]}]
    out, log = ms.sanitize_moves(
        moves, view, is_final_night=True, enemy_probe_cells={(12, 5)},
    )
    assert any(m["a"] == "probe" and tuple(m["at"]) == (12, 5) for m in out)


def test_sanitizer_normal_night_probe_untouched():
    view = _view(width=40, height=28, live=[], harvesters=[("h1", None)])
    moves = [{"a": "probe", "at": [7, 4]}]
    out, _ = ms.sanitize_moves(moves, view, is_final_night=False)
    assert any(m["a"] == "probe" and tuple(m["at"]) == (7, 4) for m in out)


# ── (C) deploy-all-harvesters guard ───────────────────────────────────


def test_sanitizer_deploys_idle_harvester_on_unused_chain():
    # Two harvesters in orbit; the plan only deploys h1. The unused chain
    # for h2 must be appended (drop -> steps -> pickup) so it isn't idle.
    view = _view(live=[(5, 5), (6, 5), (10, 10), (11, 10)],
                 red=[(10, 10, 94), (11, 10, 53)],
                 harvesters=[("h1", None), ("h2", None)])
    chain_hints = [
        {"unit": "h2", "drop_at": [10, 10],
         "cells": [[10, 10], [11, 10]], "tiers": ["vein", "vein"],
         "purities": [94, 53], "length": 2},
    ]
    moves = [
        {"a": "drop", "unit": "h1", "at": [5, 5]},
        {"a": "pickup", "unit": "h1"},
    ]
    out, log = ms.sanitize_moves(moves, view, chain_hints=chain_hints)
    h2_drop = [m for m in out if m["a"] == "drop" and m["unit"] == "h2"]
    assert h2_drop and tuple(h2_drop[0]["at"]) == (10, 10)
    assert any(m["a"] == "pickup" and m["unit"] == "h2" for m in out)
    assert any("deployed idle harvester" in s for s in log)


def test_sanitizer_deploy_all_noop_when_all_deployed():
    view = _view(live=[(5, 5), (10, 10)],
                 red=[(10, 10, 94)],
                 harvesters=[("h1", None)])
    chain_hints = [{"unit": "h1", "drop_at": [10, 10],
                    "cells": [[10, 10]], "tiers": ["vein"],
                    "purities": [94], "length": 1}]
    moves = [{"a": "drop", "unit": "h1", "at": [5, 5]},
             {"a": "pickup", "unit": "h1"}]
    out, log = ms.sanitize_moves(moves, view, chain_hints=chain_hints)
    # h1 already deployed -> no extra drop appended for it.
    assert sum(1 for m in out if m["a"] == "drop") == 1
    assert not any("deployed idle harvester" in s for s in log)


def test_sanitizer_deploy_all_truncates_noncontiguous_chain():
    # Chain hint has a non-contiguous jump; deploy-all must bank the legal
    # prefix and stop rather than emit an illegal step.
    view = _view(live=[(10, 10), (11, 10)],
                 red=[(10, 10, 94), (11, 10, 53), (13, 10, 200)],
                 harvesters=[("h1", None)])
    chain_hints = [{"unit": "h1", "drop_at": [10, 10],
                    "cells": [[10, 10], [11, 10], [13, 10]],  # 11->13 jump
                    "tiers": ["vein", "vein", "mass"],
                    "purities": [94, 53, 200], "length": 3}]
    out, _ = ms.sanitize_moves([], view, chain_hints=chain_hints)
    steps = [tuple(m["to"]) for m in out if m["a"] == "step"]
    assert (13, 10) not in steps  # illegal jump dropped
    assert (11, 10) in steps      # contiguous prefix kept


# ── (D) finisher-plan value + low-value swap ──────────────────────────


def test_plan_red_value_counts_only_visible_red():
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7 import harness as hn
    view = _view(live=[(10, 10), (11, 10)], red=[(10, 10, 94)])
    moves = [
        {"a": "drop", "unit": "h1", "at": [10, 10]},   # p94 vein -> 94
        {"a": "step", "unit": "h1", "to": [11, 10]},   # not red -> 0
        {"a": "step", "unit": "h1", "to": [99, 99]},   # off-map fog -> 0
        {"a": "pickup", "unit": "h1"},
    ]
    assert hn._plan_red_value(moves, view) == 94.0


def test_plan_red_value_zero_for_unmapped_wander():
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7 import harness as hn
    view = _view(live=[(10, 10)], red=[(10, 10, 94)])
    moves = [
        {"a": "drop", "unit": "h1", "at": [20, 20]},   # not red
        {"a": "step", "unit": "h1", "to": [21, 20]},   # not red
        {"a": "pickup", "unit": "h1"},
    ]
    assert hn._plan_red_value(moves, view) == 0.0


# ── (A) continuation prompt carries board state ───────────────────────


def test_continuation_prompt_includes_board_facts():
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7 import harness as hn
    prompt = hn._continuation_prompt(
        "...analysis with no committed moves...",
        day=7, day_cap=7,
        orbit_harvesters=["harvester_p1", "harvester_p1_2"],
        chain_hints=[{"unit": "harvester_p1", "cells": [[14, 5], [14, 6]],
                      "tiers": ["vein", "vein"]}],
        supersede_hints=[],
    )
    assert "FINAL NIGHT" in prompt
    assert "harvester_p1_2" in prompt          # must deploy both
    assert "(14,5)" in prompt                   # real chain, not a guess
    assert "launch NO probes" in prompt         # final-night probe rule


# ── grounded reflection block ─────────────────────────────────────────


def test_reflect_block_empty_on_night_one():
    assert format_reflect_block({}, None, 1) == ""
    assert format_reflect_block({}, {"day": 0}, 1) == ""


def test_reflect_block_surfaces_predicted_vs_actual():
    prior = {"day": 2, "predicted_outcome": {"banked_pts_estimate": "high"},
             "actual_banked": 320, "probe_crushes": []}
    block = format_reflect_block({}, prior, 3)
    assert "REFLECT ON LAST NIGHT (day 2)" in block
    assert "\"high\"" in block
    assert "320 pts" in block
    assert "EXACT number" in block


def test_reflect_block_names_chaff_and_crush_losses():
    prior = {"day": 4, "predicted_outcome": {"banked_pts_estimate": "high"},
             "actual_banked": 0,
             "probe_crushes": ["harvester h1 crushed friendly probe at [4,10]"]}
    view = {"last_night": {"combat_events": [
        {"type": "chaff_jam", "victim": "p1", "by": ["p2"],
         "units": ["harvester_p1"], "hours": [6, 7, 8]},
    ]}}
    block = format_reflect_block(view, prior, 5)
    assert "0 pts" in block
    assert "CHAFFED" in block
    assert "crushed friendly probe at [4,10]" in block
    assert "MUST acknowledge" in block


def test_continuation_prompt_lists_supersede_targets():
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7 import harness as hn
    prompt = hn._continuation_prompt(
        "draft", day=7, day_cap=7,
        orbit_harvesters=["harvester_p1"],
        chain_hints=[],
        supersede_hints=[{"probe_at": [5, 5], "day_seen": 6}],
    )
    assert "supersede enemy probes at: (5,5)" in prompt


# ── outing hold cap clip (RULEBOOK §3, 6-parcel hold) ─────────────────


def test_sanitizer_clips_chain_past_outing_hold_cap():
    """A chain with more than 6 steps banks nothing extra (hold = 6) — the
    sanitizer trims the 7th+ step so the plan carries no dead moves."""
    row = [(x, 5) for x in range(3, 12)]  # a live RED row 3..11
    view = _view(live=row, harvesters=[("h1", None)],
                 red=[(x, 5, 200) for x in range(3, 12)])
    moves = [
        {"a": "drop", "unit": "h1", "at": [3, 5]},
        *[{"a": "step", "unit": "h1", "to": [x, 5]} for x in range(4, 11)],  # 7 steps
        {"a": "pickup", "unit": "h1"},
    ]
    out, log = ms.sanitize_moves(moves, view)
    steps = [m for m in out if m["a"] == "step" and m.get("unit") == "h1"]
    assert len(steps) == ms._MAX_STEPS_PER_OUTING == 6
    # Pickup survives so the unit still comes home.
    assert any(m["a"] == "pickup" and m.get("unit") == "h1" for m in out)
    assert any("outing cap" in s for s in log)


def test_sanitizer_leaves_legal_chain_untouched():
    """A drop + 5-step chain (6 parcels) is exactly at the hold cap — no clip."""
    row = [(x, 5) for x in range(3, 9)]
    view = _view(live=row, harvesters=[("h1", None)],
                 red=[(x, 5, 200) for x in range(3, 9)])
    moves = [
        {"a": "drop", "unit": "h1", "at": [3, 5]},
        *[{"a": "step", "unit": "h1", "to": [x, 5]} for x in range(4, 9)],  # 5 steps
        {"a": "pickup", "unit": "h1"},
    ]
    out, log = ms.sanitize_moves(moves, view)
    steps = [m for m in out if m["a"] == "step" and m.get("unit") == "h1"]
    assert len(steps) == 5
    assert not any("outing cap" in s for s in log)
