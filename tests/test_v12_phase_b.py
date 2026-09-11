"""Phase B — the card stops lying. Truth contracts for what v12 is told."""

from __future__ import annotations

from typing import Any, Dict

import random

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import hazard_memory as hm
from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import last_night as ln
from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import option_economics as oe
from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import out_of_grid as og
from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import speculative as spec


# ── OBS-43 / fix 0.1 — a rival's trail strips the ground it crosses ────
def _view_with_trail() -> Dict[str, Any]:
    """The shape of `SNAP_ac1c55bf_d6_p4`: a trail across cells that are FOGGED
    to us, so nothing in `world.live` can ever report them."""
    return {
        "day": 6,
        "world": {"width": 40, "height": 40, "live": [
            {"x": 5, "y": 5, "tile": "GREEN", "lineage": "synthetic"},
            {"x": 6, "y": 5, "tile": "GREEN", "lineage": "natural"},
        ]},
        "competitor_intel": {
            "new_this_day": [
                {"kind": "enemy_harvester_trail", "owner": "p1",
                 "at": [17, 7], "day_seen": 5},
                {"kind": "enemy_harvester_trail", "owner": "p1",
                 "at": [17, 8], "day_seen": 5},
                {"kind": "enemy_probe_launch", "owner": "p2",
                 "at": [12, 4], "day_seen": 5},
            ],
            "persistent_echoes": [
                {"kind": "enemy_harvester_trail", "owner": "p2",
                 "at": [11, 10], "day_seen": 4},
            ],
        },
    }


def test_a_rival_trail_makes_its_cells_green_even_through_fog():
    """Every step harvests, so a trail means the cell is stripped. Both existing
    green readers walk `world.live`, i.e. what we can SEE — so a trail over
    fogged ground was never learned, and the echo block went on advertising
    `(17,7) purity_est=158` hours after a rival cleared it (OBS-43)."""
    green = hm.view_green_cells(_view_with_trail())

    assert (17, 7) in green and (17, 8) in green, "witnessed trail must be green"
    assert (11, 10) in green, "an older trail is still stripped ground"
    assert (5, 5) in green and (6, 5) in green, "the existing readers still work"
    assert (12, 4) not in green, "a probe launch strips nothing"


# ── OBS-43 / fix 0.2 — an echo is evidence, not a live reading ─────────
def _sweep_view() -> Dict[str, Any]:
    """`SNAP_ac1c55bf_d6_p4` in miniature: a seam known from a 2-night echo,
    two of whose cells a rival cleared last night."""
    return {
        "day": 6,
        "world": {"width": 40, "height": 40, "live": [
            {"x": 16, "y": 7, "tile": "RED", "purity": 240},
        ]},
        "red_tiles": [
            {"x": 16, "y": 6, "purity": 255, "freshness": "stale"},
            {"x": 16, "y": 7, "purity": 240, "freshness": "fresh"},
            {"x": 17, "y": 7, "purity": 158, "freshness": "stale"},
            {"x": 17, "y": 8, "purity": 142, "freshness": "stale"},
        ],
        "competitor_intel": {"new_this_day": [
            {"kind": "enemy_harvester_trail", "at": [17, 7], "day_seen": 5},
            {"kind": "enemy_harvester_trail", "at": [17, 8], "day_seen": 5},
        ]},
    }


def test_a_trail_cell_is_priced_as_green_not_as_the_red_the_echo_remembers():
    """The heart of OBS-43. `_cell_index` projects `red_tiles` in as a fallback,
    echo rows included, so a cell a rival stripped last night was still counted
    INTO the advertised yield at full purity — and would then cost -100 on
    arrival. Both halves of the lie, in one number."""
    yb = oe.yield_breakdown(
        [(16, 6), (16, 7), (17, 7), (17, 8)], _sweep_view(),
    )
    assert yb["green_cells"] == 2, "both trail cells must price as stripped"
    assert yb["green_penalty"] == -200
    assert yb["red_tiers"].get("vein") is None, (
        "the stripped veins must not appear in the red breakdown"
    )


def test_our_own_stripped_ground_is_priced_as_green_even_through_fog():
    """v1.48 — the mirror of the test above, for the wake WE left.

    A rival's trail survives fog because it arrives as intel. Ours did not:
    the view only publishes green it can currently SEE, so a seam we combed
    two nights ago went dark and fell out of the index entirely, scoring as
    "unknown" — which the yield line renders as free. Observed on wpn_s3 day
    6, where a chain crossed three of its own stripped cells and advertised
    "green 0" one line above a HAZARD warning naming all three. Priced
    honestly the walk is -38, not +262, and the agent declines it.
    """
    view = _sweep_view()
    # Nothing in `live` and nothing in the intel block — only the harness's
    # own monotonic memory of ground it stripped itself.
    view["stripped_memory"] = [[16, 6], [15, 6]]
    yb = oe.yield_breakdown([(16, 6), (15, 6), (16, 7)], view)
    assert yb["green_cells"] == 2
    assert yb["green_penalty"] == -200
    assert yb["unknown_cells"] == 0, (
        "a cell we remember stripping is not unknown ground"
    )
    assert yb["red_tiers"].get("vein") is None, (
        "the stale echo must not resurrect (16,6) as red we can still take"
    )


def test_a_live_reading_beats_our_stale_memory_of_stripping_it():
    """`setdefault`, not overwrite. Green is monotonic, but the memory is not
    evidence about a cell a probe is lighting right now — if the view can see
    RED there, the view wins and we do not veto a real mass on a stale note."""
    view = _sweep_view()
    view["stripped_memory"] = [[16, 7]]  # but (16,7) is RED in `live`
    yb = oe.yield_breakdown([(16, 7)], view)
    assert yb["green_cells"] == 0
    assert yb["red_pts"] > 0


def test_the_yield_says_how_much_of_the_red_is_only_an_echo():
    """An echo still counts — it is real evidence — but a total that blends it
    with live readings reads more confident than the board warrants."""
    yb = oe.yield_breakdown([(16, 6), (16, 7)], _sweep_view())

    assert yb["echo_cells"] == 1, "the stale pure is the only echo here"
    assert 0 < yb["echo_pts"] < yb["red_pts"], (
        f"echo slice {yb['echo_pts']} of total {yb['red_pts']}"
    )


def test_a_live_reading_is_never_tagged_as_an_echo():
    yb = oe.yield_breakdown([(16, 7)], _sweep_view())
    assert yb["echo_pts"] == 0 and yb["echo_cells"] == 0


def test_the_trail_reader_survives_a_malformed_intel_block():
    for intel in (None, [], {"new_this_day": None},
                  {"new_this_day": [{"kind": "enemy_harvester_trail"}]},
                  {"new_this_day": [{"kind": "enemy_harvester_trail",
                                     "at": ["x", 3]}]}):
        assert hm.enemy_trail_cells({"competitor_intel": intel}) == set()


# ── OBS-30 / fix 0.3 — the same pure is sold by every option that crosses it ──
def _two_seam_view() -> Dict[str, Any]:
    """`SNAP_408ddd46_d5_p1` in miniature. OUR seam at (31,17) is partly visible;
    the RIVAL seam at (16,6) is wholly fogged — the board that made the attack
    unpriceable while every option on our own seam quoted the same pure."""
    return {
        "day": 5,
        "world": {"width": 40, "height": 40, "live": [
            {"x": 31, "y": 18, "tile": "RED", "purity": 255},
            {"x": 32, "y": 17, "tile": "RED", "purity": 200},
            {"x": 32, "y": 18, "tile": "RED", "purity": 60},
            {"x": 33, "y": 17, "tile": "RED", "purity": 40},
        ]},
        "redsign": [
            {"id": "own", "center": [31, 17], "hour": 3, "cells": [
                [31, 18, 0.9], [32, 17, 0.6], [32, 18, 0.5],
                [33, 17, 0.4], [30, 17, 0.3],
            ]},
            {"id": "rival", "center": [16, 6], "hour": 4, "cells": [
                [16, 6, 0.88], [16, 5, 0.5], [17, 6, 0.4], [17, 5, 0.3],
            ]},
        ],
    }


def test_a_pure_two_options_both_walk_is_flagged_on_both_of_them():
    """OBS-30's core. Every yield is honest alone and the SET of them is not: an
    agent picking two options reads two four-figure numbers and adds, when the
    second wave arrives to ground the first already stripped."""
    claims = oe.overlap_claims(
        {"GRAB1": [(31, 18), (32, 17)],
         "WALKIN_GRAB": [(32, 18), (31, 18)],
         "CH2": [(33, 17)]},
        _two_seam_view(),
    )
    assert "CH2" not in claims, "a cell only one option takes is not a conflict"
    grab1 = {cell: others for cell, _tier, others in claims["GRAB1"]}
    assert grab1[(31, 18)] == ["WALKIN_GRAB"]
    assert (32, 17) not in grab1, "the mass is GRAB1's alone here"
    assert dict(
        (c, o) for c, _t, o in claims["WALKIN_GRAB"]
    )[(31, 18)] == ["GRAB1"]


def test_one_shared_trace_is_still_not_worth_the_ink():
    """The original instinct survives for the case it was right about: one
    cheap cell changes no decision, and flagging it would bury the pure."""
    claims = oe.overlap_claims(
        {"A": [(33, 17)], "B": [(33, 17)]},   # trace 40 -> 30 + 100 = 130
        _two_seam_view(),
    )
    assert claims == {}, "a single trace share must stay silent"


def test_enough_cheap_cells_add_up_to_worth_the_ink():
    """OBS-54. Cheap PER CELL is not cheap in aggregate, because each shared
    cell costs the double count AND the -100 it becomes AND the hour."""
    claims = oe.overlap_claims(
        {"A": [(33, 17), (32, 18)], "B": [(33, 17), (32, 18)]},
        _two_seam_view(),                      # 30+100 plus 60+100 = 290
    )
    assert set(claims) == {"A", "B"}, (
        "two shared cells put 290 points at stake — that earns a line"
    )


def test_a_shared_mass_always_warns_however_lonely():
    """The pre-v1.48 guarantee, pinned: this change may only ADD warnings."""
    claims = oe.overlap_claims(
        {"A": [(32, 17)], "B": [(32, 17)]},   # mass 200, on its own
        _two_seam_view(),
    )
    assert set(claims) == {"A", "B"}


def test_two_chains_running_one_seam_from_opposite_ends_are_flagged():
    """The duel_s4001 day-4 night, in miniature. CH1 walks the seam one way
    and CH2 walks it back; they share three VEIN cells, which the old
    mass/pure filter waved through. The model then added 698 and 587, and the
    second harvester spent three hours re-walking its own fresh green."""
    view = {
        "day": 4,
        "world": {"width": 40, "height": 40, "live": [
            {"x": x, "y": y, "tile": "RED", "purity": 131}
            for x, y in [(11, 17), (10, 17), (10, 18), (10, 19),
                         (11, 19), (12, 19), (9, 18), (9, 17), (8, 17)]
        ]},
    }
    claims = oe.overlap_claims(
        {"CH1": [(11, 17), (10, 17), (10, 18), (10, 19), (11, 19), (12, 19)],
         "CH2": [(11, 19), (10, 19), (10, 18), (9, 18), (9, 17), (8, 17)]},
        view,
    )
    assert set(claims) == {"CH1", "CH2"}, "both ends of the seam must be told"
    for oid in ("CH1", "CH2"):
        assert {c for c, _t, _o in claims[oid]} == {(10, 18), (10, 19), (11, 19)}, (
            "exactly the three cells whichever chain runs second finds stripped"
        )


def test_a_blind_comb_over_a_smear_is_priced_not_called_unknown():
    """The attack used to read the literal word "unknown" against a rival option
    quoting +1248, and no agent picks that. A redsign is a public promise of a
    pure and the smear ships per-cell odds on where it is."""
    est = oe.blind_estimate([(16, 6), (16, 5), (17, 6)], _two_seam_view())

    assert est is not None and est["cells"] == 3
    assert est["expected_pts"] > 0
    assert est["halo_measured"] is False, (
        "we can see none of the rival seam, so its halo is borrowed"
    )
    # Borrowed from OUR seam (200/60/40 non-pure -> 100), never from the
    # 255 pure, and never from a constant while a measurable seam exists.
    assert est["halo_purity"] == 100


def test_the_estimate_prices_each_seam_off_its_own_evidence():
    """A first draft pooled every smear into one map, which divided one seam's
    odds by another seam's weight and priced an unseen seam off cells on the far
    side of the board."""
    view = _two_seam_view()
    own = oe.blind_estimate([(30, 17)], view)       # fogged cell, OUR seam
    assert own is not None and own["halo_measured"] is True

    # (30,17) carries 0.3 of our seam's 2.7 total, not of both seams' 5.3.
    assert own["best_pure_odds"] == round(0.3 / 2.7, 2)


def test_open_fog_outside_every_smear_stays_honestly_unknown():
    """Inventing a number for a walk with no broadcast behind it would be the
    same defect pointing the other way."""
    assert oe.blind_estimate([(2, 2), (3, 3)], _two_seam_view()) is None
    assert oe.blind_estimate([(16, 6)], {"world": {"live": []}}) is None


# ── OBS-38 / fix 0.4 + OBS-32 / fix 0.5 — the redsign stanza checks the board ──
def test_the_risk_line_never_claims_mass_rich_about_a_seam_it_cannot_see():
    """OBS-38. The stanza asserted "the seam is MASS-RICH" on every redsign,
    having looked at nothing — so the agent sized its commitment to a halo that
    was not there."""
    _level, reason = oe.collision_risk([(16, 6)], _two_seam_view())

    assert "MASS-RICH" not in reason
    assert "you can see NONE of this seam" in reason


def test_the_risk_line_reports_the_halo_it_can_actually_count():
    _level, reason = oe.collision_risk([(31, 18)], _two_seam_view())
    assert "you can SEE 1 pure + 1 mass on this seam" in reason


def test_a_trace_only_sighting_is_reported_without_concluding_the_halo_is_empty():
    """v1.29 rewrote this line, and the reason matters.

    It used to end "the halo is thin", which was a fair reading of a v1.28
    board — a jackpot was usually an isolated 255 with a median of 2 mass
    squares on the WHOLE map. RULEBOOK §2.2 now grades a deposit around
    every pure, so seeing only trace no longer licenses concluding there is
    no mass; the mass is most likely in the fog.

    What must still hold is the OBS-38 discipline: report what was SEEN as
    seen. The line still names the trace/vein sighting and still does not
    claim to have counted mass it cannot see.
    """
    view = _two_seam_view()
    view["world"]["live"] = [{"x": 33, "y": 17, "tile": "RED", "purity": 40}]
    _level, reason = oe.collision_risk([(33, 17)], view)
    assert "trace/vein" in reason
    assert "cannot see" in reason.lower()
    assert "the halo is thin" not in reason, (
        "the v1.28 conclusion is back — a graded board does not support it"
    )
    assert "MASS-RICH" not in reason, "OBS-38: never assert mass it has not counted"


def test_every_redsign_line_says_the_rivals_are_coming():
    """The broadcast is the point: probe vision changes whether a rival arrives
    holding a COORDINATE or an AREA, never WHETHER they arrive. Dropping that
    framing in favour of a bare probe count would understate every seam."""
    view = _two_seam_view()
    for cell in ((31, 18), (16, 6)):
        reason = oe.collision_risk([cell], view, day=5)[1]
        assert "EVERY seat got the broadcast and IS COMING" in reason
        assert "regardless of probe vision" not in reason


def test_a_live_rival_probe_means_they_hold_the_exact_cell():
    view = _two_seam_view()
    view["competitor_intel"] = {"new_this_day": [
        {"kind": "enemy_probe_launch", "owner": "p2",
         "at": [31, 18], "day_seen": 5},
    ]}
    reason = oe.collision_risk([(31, 18)], view, day=5)[1]

    assert "RIGHT NOW" in reason and "the EXACT cell" in reason
    assert "not searching for it" in reason


def test_an_expired_rival_probe_still_leaves_them_the_coordinate():
    """The third tier. Their probe is gone, so nothing lights the cell — but
    they saw it while it lived and can drop straight onto it. Reading that as
    "blind" would badly understate the race."""
    view = _two_seam_view()
    view["competitor_intel"] = {"persistent_echoes": [
        {"kind": "enemy_probe", "owner": "p2",
         "at": [31, 18], "last_seen_day": 1},
    ]}
    reason = oe.collision_risk([(31, 18)], view, day=5)[1]

    assert "WROTE THE COORDINATE DOWN" in reason
    assert "up to day 1" in reason
    assert "RIGHT NOW" not in reason, "an expired probe does not light anything"


def test_a_probe_nobody_ever_placed_leaves_them_searching_the_smear():
    reason = oe.collision_risk([(31, 18)], _two_seam_view(), day=5)[1]
    assert "no rival has ever had eyes on (31,18)" in reason
    assert "SMEAR AREA, not to this cell" in reason


def test_a_probe_still_inside_its_lifetime_is_not_written_off_as_an_echo():
    """`_enemy_probe_cells` mixes fresh launches with historical echoes and
    never filters by lifetime, so the tiers hang entirely on getting the expiry
    arithmetic right against the configured `probe_lifetime_nights`."""
    view = _two_seam_view()
    view["competitor_intel"] = {"persistent_echoes": [
        {"kind": "enemy_probe", "at": [31, 18], "last_seen_day": 4},
    ]}
    assert oe.rival_knowledge(view, (31, 18), day=5)["tier"] == "live"
    assert oe.rival_knowledge(view, (31, 18), day=99)["tier"] == "echo"


def test_without_a_day_we_never_claim_a_probe_expired():
    """No day to measure against means no expiry proof, so the safe read is
    that they can still see it."""
    view = _two_seam_view()
    view["competitor_intel"] = {"persistent_echoes": [
        {"kind": "enemy_probe", "at": [31, 18], "last_seen_day": 1},
    ]}
    assert oe.rival_knowledge(view, (31, 18), day=None)["tier"] == "live"


# ── OBS-45 / fix 0.6 — a beacon that goes dark says someone banked a pure ──
def test_a_redsign_that_vanished_since_last_plan_is_reported():
    """Retirement is global and SILENT: the view drops a spent region, so a seat
    that committed two harvesters to a seam finds it simply absent today with
    nothing explaining why. The absence is free intelligence."""
    prior = {"live_redsigns": [[31, 17], [16, 6]]}
    today = {"redsign": [{"center": [31, 17], "cells": [[31, 17, 0.9]]}]}

    said = ln._dark_beacons(today, prior)
    assert len(said) == 1
    assert "~(16,6) WENT DARK" in said[0]
    assert "somebody banked it" in said[0]


def test_smear_jitter_does_not_read_as_a_retirement():
    """The centre is deliberately noisy, so a beacon can shift a cell or two
    between nights without anything having happened to it."""
    prior = {"live_redsigns": [[31, 17]]}
    assert ln._dark_beacons({"redsign": [{"center": [32, 18]}]}, prior) == []


def test_no_prior_record_means_nothing_is_claimed():
    """Day 1, an older session, or a rebuilt memory — silence beats a false
    'the seam is spent' on a beacon that was never recorded."""
    assert ln._dark_beacons({"redsign": []}, None) == []
    assert ln._dark_beacons({"redsign": []}, {}) == []


def test_the_centres_we_persist_are_the_ones_we_can_diff():
    view = {"redsign": [{"center": [31, 17]}, {"center": [16.4, 5.7]}]}
    assert ln.live_redsign_centres(view) == [[31, 17], [16, 6]]


# ── OBS-45 / fix 0.7 — blue has no retirement mechanism, so give it one ────
def _bluesign_view(live_rows):
    return {
        "world": {"width": 40, "height": 40, "live": live_rows},
        "blue_sign": [
            {"id": "mined", "cells": [[5, 5, 0.9], [5, 6, 0.8], [6, 5, 0.7]]},
            {"id": "virgin", "cells": [[30, 30, 0.9], [30, 31, 0.8]]},
        ],
    }


def test_a_bluesign_cell_seen_empty_is_remembered_as_spent():
    """A mined blue collapses to EMPTY, not green (RULEBOOK §3.12), so the
    existing green union cannot carry this."""
    view = _bluesign_view([
        {"x": 5, "y": 5, "tile": "EMPTY"},
        {"x": 5, "y": 6, "tile": "BLUE", "purity": 120},
        {"x": 30, "y": 30, "tile": "EMPTY"},
    ])
    spent = hm.observed_spent_blue(view)
    assert (5, 5) in spent and (30, 30) in spent
    assert (5, 6) not in spent, "a cell still holding blue is not spent"
    assert (6, 5) not in spent, "a FOGGED cell proves nothing either way"


def test_the_sampler_never_targets_a_pocket_it_watched_itself_empty():
    """OBS-45's sharpest edge: the sampler PREFERS fog, so a pocket we mined out
    is promoted the moment our probe moves on and the cell re-fogs. Nothing in
    the view can catch that — the bluesign is generation-time geometry — so the
    memory has to."""
    view = _bluesign_view([])            # everything fogged again
    spent = {(5, 5), (5, 6), (6, 5)}     # the whole first pocket, mined by us

    for seed in range(12):
        hints = spec.sample_bluesign_hotdrops(
            view, rng=random.Random(seed), max_hints=2, spent=spent,
        )
        cells = {tuple(h["drop_at"]) for h in hints}
        assert cells and not (cells & spent), f"seed {seed} targeted spent blue"
        assert cells <= {(30, 30), (30, 31)}


def test_a_partly_worked_pocket_still_offers_the_cells_that_remain():
    view = _bluesign_view([])
    hints = spec.sample_bluesign_hotdrops(
        view, rng=random.Random(0), max_hints=2, spent={(5, 5), (5, 6)},
    )
    assert (6, 5) in {tuple(h["drop_at"]) for h in hints}


def test_the_sampler_is_unchanged_when_nothing_is_known_spent():
    view = _bluesign_view([])
    before = spec.sample_bluesign_hotdrops(view, rng=random.Random(3), max_hints=2)
    after = spec.sample_bluesign_hotdrops(
        view, rng=random.Random(3), max_hints=2, spent=set(),
    )
    assert before == after


# ── OBS-45 / fix 0.8 — a picked-over seam must not read like a virgin one ──
def _seam_view(live_rows):
    return {
        "world": {"width": 40, "height": 40, "live": live_rows},
        "redsign": [{"center": [16, 6], "day": 3, "cells": [
            [16, 6, 0.9], [16, 7, 0.7], [17, 6, 0.5], [17, 7, 0.3],
        ]}],
    }


def test_a_live_beacon_states_the_guarantee_it_has_always_carried():
    """A redsign retires only on total exhaustion, so a beacon still on the card
    is a HARD guarantee that a pure survives — never once said out loud."""
    note = og._strength_note(_seam_view([
        {"x": 16, "y": 6, "tile": "RED", "purity": 255},
    ]), _seam_view([])["redsign"][0], day=6)

    assert "at least one pure SURVIVES" in note
    assert "1 pure + 0 mass visible" in note


def test_a_seam_we_have_worked_reports_what_is_gone():
    note = og._strength_note(_seam_view([
        {"x": 16, "y": 6, "tile": "GREEN", "purity": 0},
        {"x": 16, "y": 7, "tile": "EMPTY", "purity": 0},
        {"x": 17, "y": 6, "tile": "RED", "purity": 200},
    ]), _seam_view([])["redsign"][0], day=6)

    assert "2 cell(s) already stripped" in note
    assert "the surviving pure is in the part you CANNOT see" in note


def test_a_fogged_seam_reports_its_echo_rather_than_claiming_it_never_looked():
    """The first cut of this printed "NEVER seen inside this smear" on
    `SNAP_ac1c55bf_d6_p4`, a seam with 29 remembered RED cells — no live sight
    is not the same as no knowledge, and `red_tiles` carries no date to
    distinguish them by."""
    view = _seam_view([])
    view["red_tiles"] = [
        {"x": 16, "y": 6, "purity": 255},
        {"x": 16, "y": 7, "purity": 200},
        {"x": 17, "y": 6, "purity": 40},
    ]
    note = og._strength_note(view, view["redsign"][0], day=6)

    assert "NEVER" not in note
    assert "1 pure + 1 mass over 3 remembered cell(s)" in note
    assert "undated" in note, "we must not invent an age the view never gave us"


def test_a_seam_never_looked_into_says_exactly_that():
    view = _seam_view([])
    note = og._strength_note(view, view["redsign"][0], day=6)
    assert "NEVER seen inside this smear" in note
