"""The turn lab plays real boards, and these tests pin that it stays real.

Two properties are worth defending here, and both were learned by
getting them wrong.

**A board is saved state.** Earlier versions let a human type a board
in, then read one back from a replay frame. The first invented nights;
the second could show one but never play it, because a frame is
rendered paint with no tile and no purity. So a board is a snapshot
session or it is not a board.

**Playing a board must not touch anything else.** That is not a
platitude: ``snapshot.clone_for_run`` re-keys the session row but not
the ``session_id`` *inside* the state blob, so a clone hydrates under
the original season's id and every save lands on the season instead of
the clone. It is silent, and it corrupts real games. The lab corrects
it, and :func:`test_a_clone_is_rekeyed_to_itself` is why it stays
corrected.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from turnlab import (  # noqa: E402
    boards, cast, isolation, readonly, store as lab_store, turn,
)


class FakeStore:
    """Just enough store for the parts that do not need an engine."""

    def __init__(self, sessions=None, policies=None):
        self._sessions = list(sessions or [])
        self._policies = dict(policies or {})
        self.saved = []

    def list_sessions(self):
        return list(self._sessions)

    def load_session(self, session_id):
        for row in self._sessions:
            if row.get("session_id") == session_id:
                return dict(row)
        return None

    def save_session(self, row):
        self.saved.append(dict(row))
        for i, existing in enumerate(self._sessions):
            if existing.get("session_id") == row.get("session_id"):
                self._sessions[i] = dict(row)
                return
        self._sessions.append(dict(row))

    def list_policies(self, session_id, day):
        return self._policies.get((session_id, day), {})


def _snap(sid, day, seat):
    return {
        "session_id": sid,
        "season_name": f"LAB:X:d{day}:{seat}",
        "day": day,
        "phase": "planning",
        "json_state": {"session_id": "dd86873300ab41c2", "day": day},
    }


# --------------------------------------------------------------------------
# What counts as a board
# --------------------------------------------------------------------------
def test_boards_are_discovered_from_the_store():
    store = FakeStore([
        _snap("LAB_dd868733_d1_p1", 1, "p1"),
        _snap("LAB_dd868733_d2_p2", 2, "p2"),
    ])
    found = boards.discover(store)
    assert [(b.day, b.seat) for b in found] == [(1, "p1"), (2, "p2")]
    assert all(b.source == "dd868733" for b in found)


def test_a_board_is_ordered_by_day_then_seat():
    store = FakeStore([
        _snap("LAB_aa_d2_p2", 2, "p2"),
        _snap("LAB_aa_d1_p2", 1, "p2"),
        _snap("LAB_aa_d2_p1", 2, "p1"),
    ])
    assert [b.label for b in boards.discover(store)] == [
        "day 1 · p2", "day 2 · p1", "day 2 · p2",
    ]


@pytest.mark.parametrize(
    "sid",
    [
        "LAB_nope",                 # too few parts
        "LAB_src_dX_p1",            # day is not a number
        "LAB_src_d2_seat",          # seat is not pN
        "dd86873300ab41c2",         # an ordinary season
    ],
)
def test_a_stray_session_cannot_become_a_board(sid):
    """Discovery scans the whole store, so it has to be picky."""
    assert boards.discover(FakeStore([_snap(sid, 2, "p1")])) == []


def test_boards_carry_no_position_data():
    """Regression: boards used to hand-declare terrain, and it was wrong.

    If a field appears here that could hold a cell, a fleet or a purity,
    the lab has started inventing nights again instead of pointing at
    state the engine wrote.
    """
    banned = {"terrain", "cells", "fleet", "fleets", "purity",
              "vision", "inferred", "grid", "hoard", "entities"}
    fields = set(boards.Board.__dataclass_fields__)
    assert not (fields & banned), f"boards must not declare {fields & banned}"


def test_unknown_board_is_an_error():
    with pytest.raises(KeyError, match="no lab board"):
        boards.get("nope", FakeStore([]))


# --------------------------------------------------------------------------
# Playing one must not touch anything else
# --------------------------------------------------------------------------
def test_a_clone_is_rekeyed_to_itself():
    """The bug that made the lab write into the season it was reading.

    ``clone_for_run`` copies the blob verbatim, so the clone's
    ``json_state.session_id`` still names the source season. The engine
    hydrates from the blob, so every ``save_session_full`` during the
    replay lands on the source. Re-keying is the whole fix.
    """
    store = FakeStore([{
        "session_id": "clone1",
        "json_state": {"session_id": "dd86873300ab41c2", "day": 2},
    }])
    turn._rekey(store, "clone1")
    assert store.load_session("clone1")["json_state"]["session_id"] == "clone1"


def test_rekeying_survives_a_double_encoded_blob():
    """Snapshots written before the encoding fix carry a JSON string."""
    store = FakeStore([{
        "session_id": "clone1",
        "json_state": json.dumps({"session_id": "other", "day": 2}),
    }])
    turn._rekey(store, "clone1")
    assert store.load_session("clone1")["json_state"]["session_id"] == "clone1"


def test_rekeying_an_already_correct_clone_writes_nothing():
    store = FakeStore([{
        "session_id": "clone1",
        "json_state": {"session_id": "clone1"},
    }])
    turn._rekey(store, "clone1")
    assert store.saved == [], "a no-op must not rewrite the row"


def test_the_source_season_is_read_off_the_snapshot_name():
    assert turn.source_of("LAB_dd868733_d2_p1") == "dd868733"
    assert turn.source_of("nonsense") == ""


def test_the_seat_is_read_off_the_snapshot_name():
    assert turn.seat_of("LAB_dd868733_d2_p2") == "p2"
    assert turn.seat_of("LAB_dd868733_d2_weird") == "p1"


def test_an_ambiguous_source_is_refused_rather_than_guessed():
    """Guessing would hand the opponent somebody else's orders."""
    store = FakeStore([
        {"session_id": "dd86873300ab41c2"},
        {"session_id": "dd868733ffffffff"},
    ])
    assert turn._widen("dd868733", store) == ""


def test_widening_ignores_the_snapshots_themselves():
    store = FakeStore([
        {"session_id": "dd86873300ab41c2"},
        {"session_id": "LAB_dd868733_d2_p1"},
    ])
    assert turn._widen("dd868733", store) == "dd86873300ab41c2"


# --------------------------------------------------------------------------
# Casting and isolation
# --------------------------------------------------------------------------
def test_roster_finds_the_shipped_agents():
    """Discovered, not listed — a fork appears by existing."""
    castable, _ = cast.roster()
    names = {a.label for a in castable}
    assert "tabula_v12" in names, "the baseline fork must be castable"
    assert "red_harvest" in names, "the offline heuristic must be castable"
    assert any(a.baseline for a in castable), "nothing marked as the baseline"


def test_a_brand_new_fork_is_castable_without_editing_anything(tmp_path):
    """The lab's cast list has to be a directory scan, not a list.

    A room of teams shares one repo. If casting a fork meant adding a
    line somewhere central, every team would be editing the same file
    all day and the last merge would win — so "register a fork" has to
    mean "create a directory". This walks the whole way: a manifest on
    disk, discovered, and offered by name.
    """
    from sea_of_colours.orchestrator_2 import agent_manifest

    # The directory is <team>_<name>, and the manifest has to agree with
    # it — that is what keeps two teams from both calling their fork
    # "v13" and colliding in a shared repo.
    fork = tmp_path / "harnesses" / "other_table_someone_elses_idea"
    fork.mkdir(parents=True)
    (fork / "agent.json").write_text(
        json.dumps({
            # Slugs, not prose: they become a package name and a menu id.
            "team": "other_table",
            "name": "someone_elses_idea",
            "participants": ["A. Nother"],
        }),
        encoding="utf-8",
    )

    found, problems = agent_manifest.discover(tmp_path / "harnesses")
    assert not problems, problems
    labels = {m.label for m in found}
    assert "other_table_someone_elses_idea" in labels, (
        f"a fork that exists on disk was not discovered: {labels}"
    )

    # And the day's real roster is the same mechanism, so whatever forks
    # a machine has are what the lab offers — including the shipped ones.
    castable, trouble = cast.roster()
    assert not trouble, trouble
    for entry in castable:
        assert entry.label, "an agent with no label cannot be cast"
        assert entry.display, f"{entry.label} has nothing to show in the picker"


def test_a_fork_is_credited_to_the_people_who_wrote_it():
    """The league is the day's public record (v1.41).

    An entrant nobody can be credited for, or chased about, is worse
    than no entrant — so a fork's manifest carries its team and its
    participants all the way through to the lab's picker.
    """
    castable, _ = cast.roster()
    forks = [a for a in castable if a.team]
    for fork in forks:
        assert fork.participants, (
            f"{fork.label} names a team but nobody in it"
        )


def test_resetting_caches_is_safe_to_call():
    """It must never refuse on a missing module: a fork may delete any."""
    isolation.reset_harness_caches(force=True)
    isolation.reset_harness_caches(force=True)


def test_every_board_has_a_frozen_v12_baseline():
    """The divergence view is useless without one, and it fails quietly.

    A missing baseline is a 404 the page turns into a small error in the
    drawer, which is easy to miss and easy to leave broken. Minting a new
    set of boards without re-recording is exactly how that happens, so
    the two are pinned together here.

    Only the seats a board is meant to be played from count. A note can
    narrow that (a seat a human held has no journal, so casting it
    compares a fork against a blank history), and a three-seat board has
    a p3 that the old hardcoded ``("p1", "p2")`` never asked about.
    """
    from turnlab import baseline, boards
    from turnlab import store as lab_store

    every = boards.discover(lab_store.store())
    if not every:
        pytest.skip("no boards minted in this checkout")

    def seats_of(board):
        if board.castable:
            return board.castable
        row = lab_store.store().load_session(board.id) or {}
        blob = row.get("json_state")
        if isinstance(blob, str):
            blob = json.loads(blob)
        return tuple((blob or {}).get("players") or ("p1", "p2"))

    missing = [
        f"{b.id} {seat}"
        for b in every for seat in seats_of(b)
        if not baseline.have(b.id, seat)
    ]
    assert not missing, (
        "no frozen V12 baseline for: " + ", ".join(missing)
        + " — record with: python -m turnlab.baseline"
    )


def test_a_baseline_carries_the_whole_exchange():
    """Not just the moves — the diff view compares prompts and reasoning."""
    from turnlab import baseline, boards
    from turnlab import store as lab_store

    every = boards.discover(lab_store.store())
    if not every or not baseline.have(every[0].id, "p1"):
        pytest.skip("no baselines recorded in this checkout")

    take = baseline.load(every[0].id, "p1")
    assert take["moves"], "a baseline with no orders is not a baseline"
    for key in ("think", "plan", "mover"):
        assert take["prompts"][key], f"the {key} prompt was not captured"
    assert take["thinking"]["reasoning"], "no reasoning captured"
    assert take["thinking"]["option_menu"], "no option menu captured"
    assert take["baseline"]["agent"] == baseline.AGENT
    # Recorded unarmed on purpose: stock V12 has no weapon verb, so a
    # rack would change its percept and never its plan.
    assert take["baseline"]["armed"] is False


def test_a_missing_baseline_is_none_rather_than_a_crash():
    from turnlab import baseline

    assert baseline.load("LAB_does_not_exist", "p1") is None
    assert baseline.have("LAB_does_not_exist", "p1") is False


def test_a_rack_stamps_stock_and_nothing_else():
    from turnlab import arms

    blob = {"weapon_stock": {"p1": {"emp": 0, "chaff": 0}}, "hoard_squares": {}}
    rack = arms.arm(blob, "p1", "both")
    assert rack.emp == 1 and rack.chaff == 1
    assert blob["weapon_stock"]["p1"] == {"emp": 1, "chaff": 1}
    assert not blob["hoard_squares"].get("p1")


def test_arming_a_seat_does_not_also_make_it_richer():
    """Armed vs unarmed has to differ by the weapon and by nothing else.

    Racks used to arrive with build fuel, which a frozen night can never
    spend — weapons are bought in orbit — so it only sat in the hoard
    moving the score. A fork that shipped more then looked like a fork
    that used its EMP well.
    """
    from turnlab import arms

    from sea_of_colours.game.weapons import (
        BLUE_COST_BY_KIND,
        WEAPONISED_BLUE_CAP,
    )

    def opened(rack_id):
        # Stamped with today's economy, because an UNstamped blob is a
        # pre-v1.36 board and ``arm`` rightly refuses to pose a weapon
        # that board's season never sold (v1.36).
        blob = {
            "weapon_stock": {},
            "hoard_squares": {"p1": [{"site_id": "kept"}]},
            "weapon_blue_costs": dict(BLUE_COST_BY_KIND),
            "weapon_blue_cap": WEAPONISED_BLUE_CAP,
        }
        arms.arm(blob, "p1", rack_id)
        return blob["hoard_squares"]["p1"]

    for rack in arms.RACKS:
        assert rack.blue == 0, f"{rack.id} hands out build fuel it cannot spend"
        assert opened(rack.id) == opened("empty"), (
            f"opening with {rack.id} changes the hoard as well as the rack"
        )


def test_a_rack_cannot_overfill_a_hoard():
    """An overfull hoard is not a state the engine can reach on its own.

    No shipped rack grants BLUE any more, so this exercises ``_give_blue``
    directly — it stays because closing the procurement gap would bring
    the grant back, and the cap is the part that would be forgotten.
    """
    from sea_of_colours.game.session import HOARD_CAPACITY
    from turnlab import arms

    blob = {"weapon_stock": {}, "hoard_squares": {"p1": []}}
    for _ in range(20):
        arms._give_blue(blob, "p1", 455)
    assert len(blob["hoard_squares"]["p1"]) <= HOARD_CAPACITY


def test_arming_also_corrects_the_inherited_arsenal_history():
    """The clone must hold ONE answer to "what is this seat carrying".

    A clone inherits ``station_obs_by_day`` from the season it was cut
    from, and each entry carries that season's public arsenal reading
    (§4.9.8). Arming wrote ``weapon_stock`` and left those alone, so
    the two disagreed — and the station bar picks between them on the
    scrubber. Live it drew the rack; one nudge of the cursor and it
    drew the source season's instead. The wrong number was a plausible
    one, which is why this went unnoticed: a seat opened with one SNAP
    read as three pips of ordnance while the orbit panel still offered
    the SNAP.
    """
    from turnlab import arms

    from sea_of_colours.game.weapons import (
        BLUE_COST_BY_KIND,
        WEAPONISED_BLUE_CAP,
    )

    blob = {
        "weapon_stock": {},
        "hoard_squares": {},
        "weapon_blue_costs": dict(BLUE_COST_BY_KIND),
        "weapon_blue_cap": WEAPONISED_BLUE_CAP,
        "station_obs_by_day": {
            "1": {"pre":  {"p1": {"arms": {"blue": 300, "cap": 600}},
                           "p2": {"arms": {"blue": 200, "cap": 600}}}},
            "2": {"post": {"p1": {"arms": {"blue": 500, "cap": 600}}}},
        },
    }
    arms.arm(blob, "p1", "snap")

    want = BLUE_COST_BY_KIND["snap"]
    by_day = blob["station_obs_by_day"]
    assert by_day["1"]["pre"]["p1"]["arms"]["blue"] == want
    assert by_day["2"]["post"]["p1"]["arms"]["blue"] == want
    # The seat that was not armed keeps its own history, which for it
    # is genuinely true: the clone IS that season up to the freeze.
    assert by_day["1"]["pre"]["p2"]["arms"]["blue"] == 200


def test_arming_does_not_invent_an_arsenal_on_a_weapons_off_season():
    """No ``arms`` key means the season published no arsenal at all.

    Writing one in would put ordnance on the public readout of a board
    whose engine cannot fire it — a different lie from the one above,
    and a worse one.
    """
    from turnlab import arms

    from sea_of_colours.game.weapons import (
        BLUE_COST_BY_KIND,
        WEAPONISED_BLUE_CAP,
    )

    blob = {
        "weapon_stock": {},
        "hoard_squares": {},
        "weapon_blue_costs": dict(BLUE_COST_BY_KIND),
        "weapon_blue_cap": WEAPONISED_BLUE_CAP,
        "station_obs_by_day": {"1": {"pre": {"p1": {"fullness": {}}}}},
    }
    arms.arm(blob, "p1", "snap")
    assert "arms" not in blob["station_obs_by_day"]["1"]["pre"]["p1"]


def test_an_unknown_rack_is_refused_by_name():
    from turnlab import arms

    with pytest.raises(KeyError) as exc:
        arms.get("nukes")
    assert "nukes" in str(exc.value) and "empty" in str(exc.value)


def test_the_empty_rack_changes_nothing():
    from turnlab import arms

    blob = {"weapon_stock": {"p1": {"emp": 3, "chaff": 0}}, "hoard_squares": {}}
    arms.arm(blob, "p1", "empty")
    assert blob["weapon_stock"]["p1"] == {"emp": 0, "chaff": 0}
    assert not blob["hoard_squares"].get("p1")


def test_evicting_a_session_leaves_every_other_session_alone():
    """The property that stops the lab damaging a live game.

    The harness caches are process globals shared with any season
    running in the same server. The lab used to clear them wholesale to
    isolate its own run, which would have taken a live agent's hazard
    memory with it — an agent that suddenly forgot the board, with
    nothing in any log to say why.

    It does not need to. Every cache is keyed by session, so a lab run
    in a fresh scratch clone starts empty anyway, and cleaning up after
    itself is a matter of dropping its own keys.
    """
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import hazard_memory

    live = "dd86873300ab41c2::p1"
    lab = "LABRUN_try99::p1"
    hazard_memory._CACHE[live] = {"mine": [(3, 4)]}
    hazard_memory._CACHE[lab] = {"mine": [(9, 9)]}
    try:
        dropped = isolation.evict_session("LABRUN_try99")
        assert dropped >= 1, "eviction matched nothing — the key shape changed"
        assert live in hazard_memory._CACHE, "a live game's memory was evicted"
        assert lab not in hazard_memory._CACHE, "the lab's own entry survived"
    finally:
        hazard_memory._CACHE.pop(live, None)
        hazard_memory._CACHE.pop(lab, None)


def test_evicting_is_harmless_when_nothing_matches():
    assert isolation.evict_session("LABRUN_nothing_here") == 0
    assert isolation.evict_session("") == 0


def test_nothing_in_the_lab_clears_the_caches_wholesale():
    """A source scan, because ``force=True`` silences the guard.

    The web-server guard below is the intended protection, and passing
    ``force`` walks straight past it. The lab did exactly that for a
    while. Eviction is the supported route now, so the wholesale clear
    must not reappear on a request path.
    """
    import pathlib as _pl

    root = _pl.Path(__file__).resolve().parents[2]
    offenders = []
    for path in sorted((root / "turnlab").rglob("*.py")):
        if "tests" in path.parts or path.name == "isolation.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "reset_harness_caches" in line and not line.lstrip().startswith("#"):
                offenders.append(f"{path.relative_to(root).as_posix()}:{n}")
    assert not offenders, (
        f"use evict_session; a wholesale clear can erase a live game's "
        f"agent memory: {offenders}"
    )


def test_resetting_caches_refuses_inside_the_web_server():
    """Not a store write, but it does touch a live game's memory.

    The caches are module-level globals in the harness, shared with any
    season running in this process. Clearing them mid-game would erase
    a live agent's hazard memory with nothing in the logs to say why.
    """
    import server.app  # noqa: F401  (importing IS the condition under test)

    with pytest.raises(isolation.LabIsolationError, match="live game"):
        isolation.reset_harness_caches()


# --------------------------------------------------------------------------
# The read-only handle
# --------------------------------------------------------------------------
def test_read_only_store_forwards_reads():
    store = readonly.wrap(FakeStore([{"session_id": "x"}]))
    assert store.list_sessions()


@pytest.mark.parametrize(
    "method",
    [
        "save_session", "write_frame", "put_state", "upsert_row",
        "delete_session", "create_session", "get_or_create_session",
        "record_invocation", "commit", "register_session_backend",
        "apply_moves", "run_night", "set_policy", "advance_day",
    ],
)
def test_read_only_store_refuses_every_way_of_writing(method):
    store = readonly.wrap(FakeStore())
    with pytest.raises(readonly.LabWriteBlocked):
        getattr(store, method)


def test_an_unrecognised_method_fails_closed():
    """A name nobody predicted is refused, not assumed harmless."""
    store = readonly.wrap(FakeStore())
    with pytest.raises(readonly.LabWriteBlocked, match="not a recognised read"):
        store.frobnicate


def test_wrapping_is_idempotent():
    once = readonly.wrap(FakeStore())
    assert readonly.wrap(once) is once


# --------------------------------------------------------------------------
# Source scans — so a future edit cannot quietly undo any of this
# --------------------------------------------------------------------------
#: The three modules allowed to drive the engine, and why.
#:
#: ``mint`` plays a season of its OWN making to freeze boards. ``turn``
#: plays a THROWAWAY CLONE of a snapshot. ``routes`` invokes an agent
#: into one of those clones over HTTP. ``rewalk`` replays a finished
#: season's archived orders into a NEW session in another store — it
#: reads the original and never writes it, which
#: ``test_the_source_is_only_ever_read`` pins directly. All four write,
#: none may reach a session it did not create, and naming them here
#: keeps the exception an exception.
_MAY_DRIVE_THE_ENGINE = {"mint.py", "turn.py", "routes.py", "rewalk.py"}


def test_the_rest_of_the_lab_contains_no_writes():
    import pathlib as _pl

    root = _pl.Path(__file__).resolve().parents[2]
    pkg = root / "turnlab"
    banned = (
        "store.save", "store.write", "store.put", "store.upsert",
        "store.delete", "store.create", "run_night", "apply_moves",
        "register_session_backend", "soc_engine.", "WorldBuilder",
    )
    offenders = []
    for path in sorted(pkg.rglob("*.py")):
        # readonly.py names the verbs in order to forbid them, and this
        # file names them in order to scan for them.
        if path.name == "readonly.py" or path.name in _MAY_DRIVE_THE_ENGINE:
            continue
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in banned:
            if needle in text:
                offenders.append(f"{path.relative_to(root).as_posix()}: {needle}")
    assert not offenders, f"the lab must not drive a real season: {offenders}"


def test_the_engine_drivers_only_touch_their_own_sessions():
    """The exception has a shape: create your own, or clone a snapshot."""
    import pathlib as _pl

    pkg = _pl.Path(__file__).resolve().parents[2] / "turnlab"

    mint_src = (pkg / "mint.py").read_text(encoding="utf-8")
    assert "init_session" in mint_src, "mint must make its own season"

    turn_src = (pkg / "turn.py").read_text(encoding="utf-8")
    assert "clone_for_run" in turn_src, "turn must play a throwaway clone"
    assert "_rekey(" in turn_src, (
        "a clone must be re-keyed, or the engine writes to the source season"
    )
    assert "snapshot.take" not in turn_src, (
        "playing a turn must never re-freeze the board it was handed"
    )

    routes_src = (pkg / "routes.py").read_text(encoding="utf-8")
    assert "lab_store.is_run(session_id)" in routes_src, (
        "the invoke route must refuse anything that is not a throwaway clone"
    )


# --------------------------------------------------------------------------
# The lab keeps its own data, locally
# --------------------------------------------------------------------------
def test_the_lab_store_lives_in_this_folder():
    """Not seasons/, not Snowflake, not wherever SOC_BACKEND points.

    The lab clones real games and lets agents scribble on the copies, so
    "which store am I on" must not be a question the environment gets to
    answer. It lives here, the same on every machine, deletable without
    consequence.
    """
    import pathlib as _pl

    here = _pl.Path(__file__).resolve().parents[1]
    assert lab_store.DATA_DIR.parent == here
    assert lab_store.DATA_DIR.name == "data"


def test_the_lab_never_reads_the_process_backend():
    """A source scan, because this is the kind of thing that creeps back.

    One ``get_store()`` slipped in for convenience and the lab is back to
    writing wherever the server happens to point.
    """
    import pathlib as _pl

    root = _pl.Path(__file__).resolve().parents[2]
    # Code-shaped needles, so prose explaining the rule does not trip it.
    banned = ("get_store()", "store_for_session(", 'environ.get("SOC_BACKEND"')
    offenders = []
    for path in sorted((root / "turnlab").rglob("*.py")):
        if path.name == "store.py":  # the one place allowed to open a store
            continue
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in banned:
            if needle in text:
                offenders.append(f"{path.relative_to(root).as_posix()}: {needle}")
    assert not offenders, f"the lab must use its own store only: {offenders}"


@pytest.mark.parametrize(
    "sid,owned,run",
    [
        ("LAB_dd868733_d2_p1", True, False),   # a board: readable, not playable
        ("LABRUN_abc123", True, True),         # a clone: the only playable thing
        ("dd86873300ab41c2", False, False),    # somebody's real season
        ("", False, False),
    ],
)
def test_the_lab_recognises_only_its_own_sessions(sid, owned, run):
    """Routing is by name, because it must answer before the store opens."""
    assert lab_store.owns(sid) is owned
    assert lab_store.is_run(sid) is run


def test_a_board_is_not_playable():
    """Boards are cloned, never played.

    If a board could be played it would stop being a fixed point, and
    the second agent handed it would be answering a different question
    from the first — which is the entire value of the thing.
    """
    assert lab_store.owns("LAB_dd868733_d2_p1")
    assert not lab_store.is_run("LAB_dd868733_d2_p1")


def test_the_lab_page_is_only_a_launcher():
    """The page must not grow a board renderer or an hour strip. Again.

    It has had both. They drifted from the engine, and the hour strip
    actively misled — it drew the two seats acting in sequence when a
    night resolves them together. The page's whole job now is to pick a
    board and hand off to the real interface, so anything here that
    paints a game is a regression.
    """
    import pathlib as _pl

    root = _pl.Path(__file__).resolve().parents[2]
    page = (root / "turnlab" / "static" / "lab.html").read_text(encoding="utf-8")

    assert "/api/lab/open" in page, "the launcher must hand off to the game UI"
    for regression in ("labmap.js", "map-grid", "<canvas", "cell--fog", "iframe"):
        assert regression not in page, (
            f"the page is drawing a game again ({regression}); it is a launcher"
        )


def test_the_divergence_view_splits_on_markers_v12_actually_writes():
    """The prompt breakdown is only as good as its boundaries.

    The viewer takes an 84KB prompt apart on the banner lines the
    harness writes, so that a reader sees THE GAME, THE BOARD NOW and
    LAST NIGHT rather than one undifferentiated wall. Rename a banner in
    the harness and the split does not fail — it silently degrades back
    into that wall, which is the thing it exists to replace. So the two
    ends are pinned against each other here.
    """
    import re

    from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import prompt as v12
    from turnlab import baseline, boards
    from turnlab import store as lab_store

    banners = [v12._SECTION_1, v12._SECTION_2, v12._SECTION_3]

    # The viewer's own rule, kept in step: equals on both ends with words
    # between them. The "with words" half matters — the redsign playbook
    # rules off with sixty bare equals signs, and reading those as
    # banners shattered SECTION 1 into eight nameless fragments.
    for banner in banners:
        assert re.match(r"^={3,}.*={3,}$", banner.strip()), banner
        assert banner.replace("=", "").strip(), banner
    assert not re.sub("=", "", "=" * 60).strip(), (
        "a bare rule must not read as a banner"
    )

    every = boards.discover(lab_store.store())
    recorded = [b for b in every if baseline.have(b.id, b.seat)]
    if not recorded:
        pytest.skip("no baselines recorded in this checkout")
    take = baseline.load(recorded[0].id, recorded[0].seat)
    for pass_name in ("think", "plan", "mover"):
        text = take["prompts"][pass_name]
        for banner in banners:
            assert banner in text, (
                f"the {pass_name} prompt no longer carries {banner!r} — the "
                f"divergence view will show it as one 84KB block"
            )
    # The menu gets a table of its own rather than being diffed as prose.
    assert "OPTION MENU" in take["prompts"]["think"]


def test_the_play_url_carries_everything_a_reset_needs():
    """RESET rebuilds the turn from this string and nothing else.

    Board, seat and racks all have to be in it. Drop the racks and a
    reset silently disarms the seats, which does not fail — it just
    quietly compares two different nights.
    """
    from turnlab.routes import play_url

    bare = play_url("LABRUN_beef", "p2", "LAB_dd868733_d2_p1")
    assert "session=LABRUN_beef" in bare
    assert "player=p2" in bare
    assert "lab=LAB_dd868733_d2_p1" in bare
    assert "arms=" not in bare, "an unarmed turn should not claim a rack"

    armed = play_url(
        "LABRUN_beef", "p1", "LAB_dd868733_d2_p1", {"p2": "chaff", "p1": "both"}
    )
    # Sorted, so the same pair of racks always produces the same address
    # and two runs of one board stay comparable by eye.
    assert "arms=p1:both,p2:chaff" in armed


def test_the_lab_ui_locks_the_turn_once_it_has_been_played():
    """One board, one night, then RESET — enforced in the real UI.

    The lab exists to watch a single frozen decision. Letting a second
    night run on top of the first turns the board into an ordinary
    little season and the comparison is gone, so the interface has to
    close down after praxis rather than merely discourage another turn.
    """
    import pathlib as _pl

    root = _pl.Path(__file__).resolve().parents[2]
    app_js = (root / "server" / "static" / "app.js").read_text(encoding="utf-8")
    css = (root / "server" / "static" / "styles.css").read_text(encoding="utf-8")

    # A committed seat cannot be invoked again, and says so.
    assert "[ SELECTED ]" in app_js
    # Spent means spent: the class the CSS hangs the lockout off.
    assert "cc-lab-spent" in app_js and "cc-lab-spent" in css
    assert "labResetTurn" in app_js, "no way back to a fresh copy of the board"
    # A reload has no memory of the plans, so the calendar is the only
    # witness left that the night was already played.
    assert "LAB_DAY" in app_js

    # ORBIT buys for a tomorrow this board does not have; INTEL reads a
    # history it does not carry.
    for tab in ("orbit", "intel"):
        assert f'body.cc-mode--lab .cc-tab[data-cc-tab="{tab}"]' in css, (
            f"the {tab.upper()} tab is back in the lab"
        )

    # The AGENT pane's season feed replays whoever held the seat in the
    # run this board was cut from. Sitting under the take you just asked
    # for, it reads as that agent's thinking, and it is not.
    for stale in ("#agent-seat-tabs", "#agent-feed", "#agent-cards-read"):
        assert f"body.cc-mode--lab {stale}" in css, (
            f"{stale} shows the source season's thinking on a frozen turn"
        )


def test_a_proposed_plan_is_drawn_by_the_ordinary_order_renderer():
    """A proposal and a queued policy have to be the same picture.

    The lab used to draw plans with a layer of its own — a coloured ring
    per cell. Two costs, both paid: you could not compare an agent's
    plan against your own by eye, because they did not look alike; and
    it was a second overlay to keep in step with the engine, which it
    already was not, drawing a bare marker for an EMP and never the
    35-cell footprint under it.

    So the proposal is fed through `paintPlannedOrdersOverlay` instead,
    via one seam — `_plannedQueue()` — which every call site of that
    renderer reads. Pin the seam, because the failure mode of losing it
    is silent: the overlay simply reverts to your own empty queue and
    the board goes blank.
    """
    import pathlib as _pl
    import re as _re

    root = _pl.Path(__file__).resolve().parents[2]
    app_js = (root / "server" / "static" / "app.js").read_text(encoding="utf-8")
    css = (root / "server" / "static" / "styles.css").read_text(encoding="utf-8")

    assert "function _plannedQueue()" in app_js
    # Everything that draws queued orders must go through it, or a lab
    # plan vanishes the moment that reader repaints (a zoom, a resize).
    for reader in ("_unitWaypoints", "_aoeShapes", "paintPlannedOrdersOverlay"):
        body = _re.search(
            rf"function {reader}\([^)]*\) \{{(.*?)\n  \}}", app_js, _re.S,
        )
        assert body, f"{reader} not found — has it been renamed?"
        assert "soloQueue" not in body.group(1), (
            f"{reader} still reads soloQueue directly, so it will draw your "
            f"own queue over a seat's proposal"
        )

    # The bespoke layer is gone, not merely unused.
    assert "cell--lab-plan" not in app_js and "cell--lab-plan" not in css
    # One plan at a time — both at once cannot work on this renderer,
    # since each seat's path is numbered from hour one.
    assert "lab-plan-filter" not in app_js, "the BOTH/OFF filter is back"
    assert "cc-lab-seat--shown" in app_js and "cc-lab-seat--shown" in css


def test_a_board_offers_every_seat_the_position_has():
    """A frozen turn is a position, and a position needs all of it.

    Freezing a night to test one seat's attack is only worth anything if
    the seat being attacked also moves — on Vetus Lantern p1 can see the
    redsign, so a night where it sits still is a night nobody would have
    played. Two separate places used to assume two seats (the launcher
    inferred the roster from how many board rows shared a day, which is
    wrong for a re-walked board, and the baseline recorder hardcoded
    p1/p2), so a three-seat night quietly became a smaller game.
    """
    from turnlab import boards as lab_boards
    from turnlab import store as lab_store

    every = lab_boards.discover(lab_store.store())
    assert every, "no boards to check"

    for board in every:
        # Read off the session, never guessed from the id.
        assert board.players, f"{board.id} reports no roster"
        # castable is what every consumer asks, so it must answer with
        # the whole position unless a note deliberately narrows it.
        narrowed = board.note is not None and board.note.seats
        if not narrowed:
            assert board.castable == board.players, (
                f"{board.id} offers {board.castable} of {board.players}"
            )
        assert set(board.castable) <= set(board.players)
        assert "players" in board.as_dict(), "the launcher cannot see the roster"

    three = [b for b in every if len(b.players) > 2]
    assert three, (
        "no board with more than two seats — this test cannot see the bug "
        "it exists for; grab a 3-seat night"
    )
    for board in three:
        assert len(board.castable) > 2, f"{board.id} lost a seat"


def test_the_invoke_rows_are_rebuilt_when_the_roster_arrives():
    """The panel is built before the first poll knows how many seats there are.

    ``_activeSeatsForView()`` defaults to ``["p1", "p2"]`` until a status
    poll lands, and the boot call ran first. A boolean "already built"
    guard therefore froze that default in place, and the later correct
    call returned early — so a 3-seat board lost p3 for the life of the
    page. Key the guard on the seats instead.
    """
    import pathlib as _pl

    root = _pl.Path(__file__).resolve().parents[2]
    app_js = (root / "server" / "static" / "app.js").read_text(encoding="utf-8")

    assert "_labInvokeBuilt" not in app_js, "the boolean guard is back"
    assert "_labInvokeSeats" in app_js
    # Learning the real roster has to reach the lab, or the rebuild never
    # fires. Every site that stashes the seat list calls this one.
    body = app_js.split("function renderReplayViewButtons()", 1)
    assert len(body) == 2, "renderReplayViewButtons has been renamed"
    assert "renderLabInvoke()" in body[1][:600], (
        "nothing rebuilds the invoke rows when the canonical seat list "
        "updates, so the boot-time guess is final again"
    )


def test_the_lab_takes_no_hand_typed_turn():
    """Every seat is cast; the composer is not a feature here.

    The board's one claim is that what you watched resolve is what the
    agents asked for, and a hand-typed order blends into the same policy
    and quietly breaks it. Three ways in had to close, because shutting
    one leaves the other two: the tab, the ``[1]``..``[8]`` captions that
    activate a tab by index whatever its state, and the post itself.
    """
    import pathlib as _pl

    root = _pl.Path(__file__).resolve().parents[2]
    app_js = (root / "server" / "static" / "app.js").read_text(encoding="utf-8")
    css = (root / "server" / "static" / "styles.css").read_text(encoding="utf-8")
    launcher = (root / "turnlab" / "static" / "lab.html").read_text(encoding="utf-8")

    # 1. The tab is not on screen.
    assert 'body.cc-mode--lab .cc-tab[data-cc-tab="orders"]' in css
    # 2. Nothing can select it — not a click, not a number key, not the
    #    phase snap that used to put you back on ORDERS after orbit.
    assert "LAB_SHUT_TABS" in app_js
    for shut in ("orbit", "intel", "orders"):
        assert f'"{shut}"' in app_js.split("LAB_SHUT_TABS", 1)[1][:200], (
            f"{shut} is not in the shut list"
        )
    # 3. The post refuses outright, not merely once a seat is sealed.
    body = _re_search_function(app_js, "postPolicy")
    assert "if (LAB_MODE) {" in body, (
        "postPolicy no longer refuses every lab order — a composer reached "
        "by any route would submit a human turn"
    )
    assert "committed" not in body.split("if (LAB_MODE) {", 1)[1][:400], (
        "the refusal is conditional on the seat being sealed again"
    )
    # 4. And the lab lands somewhere that exists.
    assert 'activateCcTab("log")' in app_js

    # The launcher's seat picker went with it: it bound the page to a
    # seat, which only decided which fog you opened on once no human
    # could compose a turn.
    assert "seatpick" not in launcher and "seatPick" not in launcher


def _re_search_function(src: str, name: str) -> str:
    """The body of a top-level ``function name(...)`` in a big IIFE."""
    import re as _re

    m = _re.search(rf"\n  (?:async )?function {name}\([^)]*\) \{{(.*?)\n  \}}",
                   src, _re.S)
    assert m, f"{name} not found — has it been renamed?"
    return m.group(1)


def test_a_fallback_take_says_so_loudly():
    """An unreachable model must not read as a boring fork.

    When Cortex cannot be reached the harness answers with its
    deterministic chain rather than raising, which is right for a live
    season and a trap in the lab: the panel shows a perfectly plausible
    plan that is not the agent's, is identical on every invoke, and
    never fires a weapon. That is indistinguishable from "my fork
    ignores its rack" unless the panel says otherwise, and it cost an
    afternoon once already.
    """
    import pathlib as _pl

    root = _pl.Path(__file__).resolve().parents[2]
    app_js = (root / "server" / "static" / "app.js").read_text(encoding="utf-8")
    css = (root / "server" / "static" / "styles.css").read_text(encoding="utf-8")

    assert "cc-lab-fallback" in app_js, "the fallback banner is gone"
    assert "cc-lab-fallback" in css, "the banner has no styling to be loud in"
    # It has to key off the flag the harness actually sets, not off prose
    # in the rationale, which is free text and gets reworded.
    assert "fallback_used" in app_js
    # The two facts that turn a confusing plan into an obvious one.
    assert "never fires a weapon" in app_js
    assert "same every invoke" in app_js


def test_the_divergence_view_does_not_invent_a_third_v12_pass():
    """V12 makes two model calls a night, not three.

    THINK writes prose, PLAN commits option ids, and the moves are then
    compiled from those ids by the *packager* — ordinary Python that asks
    the model nothing. The MOVER is a third call that only fires when the
    packager has no recipe.

    The trap is in the audit: `mover_prompt` is built and recorded on
    *every* turn regardless, so a take from a normal night carries a full
    mover prompt no model ever read. Shown beside the two real passes it
    invented a stage V12 does not have and sent readers hunting for their
    fork's bug in a prompt with no effect on anything.
    """
    import pathlib as _pl

    root = _pl.Path(__file__).resolve().parents[2]
    js = (root / "turnlab" / "static" / "diff.js").read_text(encoding="utf-8")
    harness = (
        root / "sea_of_colours" / "orchestrator_2" / "harnesses" / "tabula_v12"
        / "harness.py"
    ).read_text(encoding="utf-8")

    # The claim this rests on: the mover is invoked only when the
    # packager did not run, and its prompt is built either way.
    assert "if not packager_used:" in harness
    assert harness.index("prompt_text = prompt_mod.build_prompt(") < harness.index(
        "if not packager_used:"
    ), "the mover prompt is no longer built unconditionally — re-check the rule"

    src = js.split("function moverRan")[1].split("\n  }")[0]
    assert "packager_used === false" in src, (
        "the view no longer keys the mover off the executor the harness used"
    )
    assert "prompts || {}).mover" in src, (
        "the flag alone reads a heuristic — which reports packager_used false "
        "quite truthfully, having no packager — as having run a mover"
    )
    # And the header has to say which executor produced the moves, since
    # the packager is most of the pipeline and shows up nowhere else.
    assert "packager (deterministic, no third model call)" in js


def test_the_divergence_view_offers_the_whole_take_in_one_diff():
    """Taking a take apart is what makes two of them hard to compare.

    The structured view answers "what differs, and where" by splitting
    the take into sections, passes and roll-ups. That is the right shape
    for a fork hunting one change and the wrong shape for the other
    question — "show me both, in full, with the changes marked" — which
    wants one document and one set of rules. So it is a switch.
    """
    import pathlib as _pl

    root = _pl.Path(__file__).resolve().parents[2]
    js = (root / "turnlab" / "static" / "diff.js").read_text(encoding="utf-8")
    css = (root / "turnlab" / "static" / "diff.html").read_text(encoding="utf-8")

    assert "function rawDoc" in js and "function renderRaw" in js
    assert 'data-mode="raw"' in css or 'btn("raw"' in js
    assert ".modes button" in css, "the switch has no styling to be visible with"

    # Both views must paint a changed line the same way, or the same
    # diff read twice tells two stories.
    assert "function renderRows" in js
    assert js.count("moved, text unchanged") == 1, (
        "the row painter has been duplicated — raw and structured will drift"
    )

    # "Everything" has to mean everything: the raw view exists because
    # the structured one already elides, so a default fold would leave
    # nowhere on the page that shows the whole prompt.
    assert "RAW_ALL" in js and "let rawContext = RAW_ALL" in js


def test_an_emp_salvo_survives_the_trip_to_the_order_renderer():
    """The one wire shape that is not a plain ``[x, y]``.

    `policy.move_to_wire` overloads `at` for an EMP: a single missile
    sends one pair, a salvo sends a LIST of pairs. The composer's rows
    always want the list, under `ats`. Reading the salvo as a pair put
    ``[[31,6],[34,6]]`` where an x-coordinate belongs, and the missiles
    and their no-drop zones silently did not draw — which is the worst
    possible thing for this overlay to be wrong about, because a fork
    that just learned to fire looks exactly like one that did not.
    """
    import pathlib as _pl

    root = _pl.Path(__file__).resolve().parents[2]
    app_js = (root / "server" / "static" / "app.js").read_text(encoding="utf-8")

    src = app_js.split("function _labQueueRows")[1].split("\n  }")[0]
    assert "ats" in src, "the salvo never reaches the renderer's key"
    assert "filter(pair)" in src, (
        "a salvo's list-of-pairs is not being unpacked — one missile and a "
        "salvo take different branches of the same field"
    )

    # And the wire really does say `at` for both, so the branch is load
    # bearing rather than defensive.
    wire = (root / "sea_of_colours" / "game" / "policy.py").read_text("utf-8")
    assert '"a": "emp_launch",\n                "at": [list(t) for t in m.ats],' in wire
    assert '{"a": "emp_launch", "at": list(m.at)}' in wire


# ── remembering what the season knew ──────────────────────────────────
def test_no_query_filters_agent_memory_with_a_range_on_kind():
    """``kind LIKE 'arena:day%'`` returns the next seat's rows. Really.

    ``SOC_AGENT_MEMORY`` is a hybrid table keyed
    ``(session_id, player, kind)``. Add a range predicate on that last
    column and the scan stops honouring the equality on ``player``:
    verified against the standard-table backup of the same rows, where
    ``player='p1' AND kind LIKE 'arena:day%'`` returns p1's seven
    entries, while the hybrid table returns p1's seven *and* p2's seven.

    The damage was quiet. Entries are filed in a dict keyed by day, so a
    rival's day-3 row simply overwrote your own and the agent reflected
    on a night it had never played — reading, in effect, the opponent's
    private notes as its own.

    This is a source scan rather than a live query because pytest has no
    Snowflake, and because the check has to cover every fork: each one
    carries its own copy of ``_v7/memory.py``, so the bug is re-issued
    with every new agent unless the whole tree is held to it.
    """
    import ast
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = []
    for path in list(root.glob("turnlab/**/*.py")) + list(
        (root / "sea_of_colours").rglob("*.py")
    ):
        src = path.read_text(encoding="utf-8", errors="ignore")
        if "SOC_AGENT_MEMORY" not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:  # pragma: no cover - not our problem here
            continue

        # Docstrings talk *about* the bug, at length, on purpose. Only
        # strings that are actually handed to a database can commit it.
        prose = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            )
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in prose:
                continue
            sql = node.value
            if "SOC_AGENT_MEMORY" not in sql or "SELECT" not in sql.upper():
                continue
            if re.search(r"\bkind\s+LIKE\b|STARTSWITH\s*\(\s*kind", sql, re.I):
                offenders.append(path.relative_to(root).as_posix())

    assert not offenders, (
        "these read SOC_AGENT_MEMORY with a range predicate on `kind`, which "
        "silently returns another player's memory: " + ", ".join(sorted(set(offenders)))
    )


def test_a_forks_journal_is_seeded_into_the_forks_own_module():
    """V12's copy of the memory module is not the fork's copy.

    ``_v7/memory.py`` is duplicated wholesale into every fork, each with
    its own module-level dict. Seeding V12's dict when the turn is being
    played by a fork succeeds, changes nothing, and leaves the fork
    planning from an empty journal while the report claims otherwise.
    """
    from turnlab import recall

    v12 = recall.memory_module("tabula_v12")
    fork = recall.memory_module("emp_harvest_test")
    assert v12 is not None and fork is not None
    assert v12 is not fork, "a fork must not share V12's memory module"
    assert v12._IN_MEMORY_STORE is not fork._IN_MEMORY_STORE

    # A heuristic agent has no harness and no journal; that is not a failure.
    assert recall.memory_module("red_harvest") is None


def test_a_frozen_turn_cannot_read_its_own_nights_reflection(monkeypatch):
    """Only nights *before* the board are seeded.

    The entry for the frozen day carries the reflection written the
    morning after. Seeding it would hand the agent tomorrow's paper and
    quietly turn the board into a different test.
    """
    from turnlab import recall

    mem = recall.memory_module("tabula_v12")
    written: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        mem, "save_entry",
        lambda s, p, e, **kw: written.append((s, p, int(e["day"]))),
    )
    monkeypatch.setattr(
        recall, "source_of", lambda _b: ("SEASON", object()),
    )
    monkeypatch.setattr(
        recall, "read_journal",
        lambda *_a, **_k: [{"day": d} for d in range(1, 8)],
    )

    got = recall.hydrate(
        "LABRUN_x", "p2", "tabula_v12",
        board_id="LAB_abcd1234_d6_p2", day=6, store=object(),
    )
    assert [d for _s, _p, d in written] == [1, 2, 3, 4, 5]
    assert got.days == [1, 2, 3, 4, 5]
    assert got.error == ""
    assert all(sid == "LABRUN_x" for sid, _p, _d in written), (
        "memory was filed under something other than the throwaway clone"
    )


def test_hydration_reports_a_failure_instead_of_planning_blind(monkeypatch):
    """Silence is the failure mode this whole exercise was about.

    An agent handed no memory plans a visibly different turn, and if the
    page cannot tell that from an agent that simply had nothing to
    remember, the board is lying about what it tested.
    """
    from turnlab import recall

    monkeypatch.setattr(recall, "source_of", lambda _b: ("", None))
    blind = recall.hydrate(
        "LABRUN_x", "p2", "tabula_v12",
        board_id="LAB_abcd1234_d6_p2", day=6, store=object(),
    )
    assert blind.entries == 0 and blind.error, "a failed recall must say so"

    monkeypatch.setattr(recall, "source_of", lambda _b: ("SEASON", object()))
    monkeypatch.setattr(recall, "read_journal", lambda *_a, **_k: [])
    empty = recall.hydrate(
        "LABRUN_x", "p2", "tabula_v12",
        board_id="LAB_abcd1234_d6_p2", day=6, store=object(),
    )
    assert empty.entries == 0 and not empty.error and empty.note, (
        "a seat that genuinely kept no journal must not look like a failure"
    )


#: The one board in the library whose seats were played by an LLM that
#: kept a journal, so the only one with memory worth shipping.
_DIARIST_BOARD = "LAB_cfa9912d_d6_p1"


def _no_season_anywhere(monkeypatch):
    """Stand in for a fresh clone: boards on disk, no season behind them."""
    from turnlab import recall

    monkeypatch.setenv("SOC_LAB_SOURCE", "/nonexistent")
    recall.forget()
    assert recall.source_of(_DIARIST_BOARD)[0] == "", (
        "this test is meaningless if the season is still reachable"
    )


def test_a_board_carries_the_memory_its_night_had(monkeypatch):
    """The journal has to survive the trip into somebody else's clone.

    v1.43 — the boards travel in the repo and their seasons do not, so
    recovering memory by reading the source season worked only on the
    machine that made the board. Every attendee got a night-six race
    planned by an agent that had been told it kept no notes, and — worse
    — diffed it against a baseline recorded here, with the notes. The
    divergence view would have opened on a fabricated disagreement.
    """
    from turnlab import recall

    _no_season_anywhere(monkeypatch)

    mem = recall.memory_module("tabula_v12")
    seen: list[tuple[str, int]] = []
    monkeypatch.setattr(
        mem, "save_entry",
        lambda s, p, e, **kw: seen.append((p, int(e["day"]))),
    )

    got = recall.hydrate(
        "LABRUN_test", "p2", "tabula_v12",
        board_id=_DIARIST_BOARD, day=6, store=lab_store.store(),
    )
    assert got.error == "", got.error
    assert got.days == [1, 2, 3, 4, 5], (
        "the five nights before the frozen turn should have been recovered "
        "from the board itself"
    )
    assert [d for _p, d in seen] == [1, 2, 3, 4, 5]
    assert all(p == "p2" for p, _d in seen), "another seat's journal leaked in"


def test_a_shipped_journal_is_preferred_to_a_live_season(monkeypatch):
    """Whoever has the account must get the same turn as whoever does not.

    The board is the fixed point. If the memory came from the season
    when one was reachable and from the file otherwise, the same board
    would pose two different questions depending on whose laptop it was
    opened on — and the baselines only match one of them.
    """
    from turnlab import recall

    recall.forget()
    monkeypatch.setattr(
        recall, "source_of", lambda _b: ("A_LIVE_SEASON", object()),
    )
    monkeypatch.setattr(
        recall, "read_journal",
        lambda *_a, **_k: [{"day": d, "plan_this_turn": "from the season"}
                           for d in range(1, 6)],
    )
    mem = recall.memory_module("tabula_v12")
    seen: list[dict] = []
    monkeypatch.setattr(
        mem, "save_entry", lambda s, p, e, **kw: seen.append(dict(e)),
    )

    recall.hydrate(
        "LABRUN_test", "p2", "tabula_v12",
        board_id=_DIARIST_BOARD, day=6, store=lab_store.store(),
    )
    assert seen, "nothing was seeded at all"
    assert not any(e.get("plan_this_turn") == "from the season" for e in seen), (
        "the live season won over the journal frozen into the board"
    )


def test_the_frozen_journal_stops_before_the_night_it_is_for():
    """Night N's own entry is written the morning after.

    Cut at hydration too, so this is belt and braces — but the file is
    what ships, and a reflection on tonight sitting inside tonight's
    board is the kind of thing nobody would think to check for.
    """
    from turnlab import freeze, recall

    for board in boards.discover(lab_store.store()):
        blob = recall.frozen_journal(board.id)
        if blob is None:
            continue
        day = freeze.frozen_day(board.id)
        assert blob.get("day") == day
        for seat, entries in (blob.get("seats") or {}).items():
            days = [int(e.get("day") or 0) for e in entries]
            assert all(0 < d < day for d in days), (
                f"{board.id} {seat} ships a journal entry for night "
                f"{max(days)}, which the turn cannot have read"
            )
            assert days == sorted(days), f"{board.id} {seat} is out of order"


def test_every_board_ships_a_journal_even_when_it_is_empty():
    """An empty file is a claim; a missing file is a shrug.

    Both are legitimate answers, and the difference matters: with a file
    present the lab can say "this seat kept no notes" without going
    looking for a season, which is the message a minted board should
    give. Without one it falls back, fails, and reports a fault.
    """
    from turnlab import recall

    recall.forget()
    for board in boards.discover(lab_store.store()):
        blob = recall.frozen_journal(board.id)
        assert blob is not None, (
            f"{board.id} ships no journal.json — run python -m turnlab.freeze"
        )
        assert set(blob.get("seats") or {}) == set(recall.seats_of(board.id)), (
            f"{board.id}'s frozen journal does not cover the cast it froze"
        )


def test_freezing_never_empties_a_board_it_cannot_place(tmp_path, monkeypatch):
    """A missing account is not evidence that a seat kept no journal.

    The freezer runs on whichever machine can still see the seasons, and
    that will not usually be the machine with all of them. Rewriting a
    good journal to an empty one because today's laptop cannot reach the
    account would destroy the only copy.
    """
    from turnlab import freeze, recall

    monkeypatch.setattr(recall, "sources_of", lambda _b: [])
    report = freeze.freeze_board(_DIARIST_BOARD, write=True)
    assert report["skipped"], "a board with no reachable season must be skipped"

    recall.forget()
    blob = recall.frozen_journal(_DIARIST_BOARD)
    assert blob and len(blob["seats"]["p2"]) == 5, (
        "the existing journal was clobbered by a run that found nothing"
    )


def test_a_run_remembers_which_board_it_came_from(tmp_path, monkeypatch):
    """Provenance is what makes memory recoverable without the client.

    Taking the board from the page's URL instead would mean a stale link
    produced a turn planned from nothing — which looks exactly like a
    seat that had nothing to remember.
    """
    monkeypatch.setattr(lab_store, "DATA_DIR", tmp_path)
    lab_store.remember_board("LABRUN_abc", "LAB_dd868733_d2_p1")
    assert lab_store.board_of("LABRUN_abc") == "LAB_dd868733_d2_p1"
    assert lab_store.board_of("LABRUN_never_opened") == ""


def test_a_clone_reads_last_nights_frames_off_its_board(tmp_path, monkeypatch):
    """``YOU ORDERED`` and ``EXECUTION LOG`` are rebuilt from replay frames.

    ``clone_for_run`` copies the session row and the state blob and
    nothing else, so a clone of a day-6 board had no day-5 frames and the
    harness reported that the agent had issued no orders on a night it
    had visibly played. Copying the frames across would work and is the
    wrong shape — they carry a dense per-seat percept per cell, so a
    board's sidecar runs to tens of megabytes and a turn would spend
    itself moving bytes that never change. The clone borrows instead.
    """
    monkeypatch.setattr(lab_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(lab_store, "_store", None)
    store = lab_store.store()

    board, run = "LAB_seed1234_d6_p1", "LABRUN_borrower"
    store.append_replay_frames(board, 5, [{"day": 5, "hour": 1, "tag": "open"}])
    lab_store.remember_board(run, board)

    assert [f["tag"] for f in store.list_replay_frames(run, 5, 5)] == ["open"], (
        "a clone cannot see the night its board played"
    )

    # Only where the clone has nothing of its own. Once a lab night
    # resolves, the clone owns that day and must answer with its own.
    store.append_replay_frames(run, 6, [{"day": 6, "hour": 1, "tag": "mine"}])
    assert [f["tag"] for f in store.list_replay_frames(run, 6, 6)] == ["mine"]

    # And a board never borrows: it is the fixed point everything else
    # is cut from, so it has to answer for itself or not at all.
    assert store.list_replay_frames(board, 9, 9) == []
    monkeypatch.setattr(lab_store, "_store", None)


def test_a_frozen_turn_states_the_arsenal_instead_of_making_it_guessable():
    """Arming a seat has to be visible to the seats it threatens.

    The lab used to guarantee this itself, by reaching past the engine
    and seeding each fork's estimator (``arms.disclose``). Since v1.34 it
    does not have to: the engine broadcasts weaponised blue off
    ``weapon_stock`` (§4.9.8), which is the field ``arm()`` writes. So
    the property is unchanged and the mechanism is now the real one —
    which is what this test pins. If it fails, a frozen turn has gone
    back to posing a rack nobody can see.
    """
    from sea_of_colours.game.session import GameSession
    from turnlab import arms

    sess = GameSession.new(width=12, height=8, seed=7)
    blob = sess.to_dict()

    arms.arm(blob, "p2", "both")
    revived = GameSession.from_dict(blob)

    # Survives the round trip the lab actually performs — arm() writes a
    # blob, open_board saves it, plan_only clones and hydrates it. The
    # counters are dense over the game's price list, so ask about the
    # two kinds "both" arms rather than the whole dict.
    assert revived.weapon_stock["p2"]["emp"] == 1
    assert revived.weapon_stock["p2"]["chaff"] == 1

    # And the rival reading is a number, not a grade. Priced off the
    # game's own table (v1.36) rather than a literal, because the point
    # of the assertion is that the broadcast happens at all — not what
    # this week's chaff costs.
    from sea_of_colours.game.weapons import weaponised_blue

    expected = weaponised_blue({"emp": 1, "chaff": 1}, revived.weapon_prices())
    seen_by_p1 = revived._station_observation("p2", fuzzy=True)
    assert seen_by_p1["arms"]["blue"] == expected
    assert "total" not in seen_by_p1["blue"], (
        "the vault stays a silhouette — only the arsenal went public"
    )

    # An unarmed seat reads as holding nothing, rather than as unknown.
    assert revived._station_observation("p1", fuzzy=True)["arms"]["blue"] == 0


def test_the_lab_cannot_pose_a_rack_the_engine_could_never_hold():
    """A rack over the arsenal cap is a position no season can reach."""
    from sea_of_colours.game.weapons import WEAPONISED_BLUE_CAP, weaponised_blue
    from turnlab import arms

    for rack in arms.RACKS:
        held = weaponised_blue({"emp": rack.emp, "chaff": rack.chaff})
        assert held <= WEAPONISED_BLUE_CAP, (
            f"{rack.id} is {held} blue of ordnance, over the cap"
        )

    # And the guard bites if someone adds one that is not.
    monkey = arms.Rack("overloaded", emp=3, chaff=3)
    arms.BY_ID[monkey.id] = monkey
    try:
        with pytest.raises(ValueError) as exc:
            arms.arm({}, "p1", "overloaded")
        assert "arsenal cap" in str(exc.value)
    finally:
        arms.BY_ID.pop(monkey.id, None)


def test_a_fork_reads_the_rack_off_the_public_percept():
    """The estimator decodes the broadcast total back to a loadout.

    This is the half ``disclose`` used to fake. It runs against the
    fork's own module, because a fork may replace it — and if it does,
    this is the seam that tells the attendee their replacement still
    answers the question the lab asks.

    v1.36 — the assertion is that the true rack lies inside the reported
    bounds, NOT that the bounds are tight. At 1-2-3 pricing a total can
    name several loadouts, and an estimator that collapses one of them
    to a single confident answer is the failure mode, not the goal.
    """
    from sea_of_colours.game.weapons import weaponised_blue
    from turnlab import recall

    mod = recall.fork_module("tabula_v12", "_v7.opponent_weapons")
    mod.clear_store()
    try:
        armed = weaponised_blue({"emp": 1, "chaff": 1})
        view = {
            "station_intel": {
                "opponents": [
                    {"seat": "p2", "arms": {"blue": armed, "cap": 600}},
                    {"seat": "p3", "arms": {"blue": 0, "cap": 600}},
                ]
            }
        }
        seen = mod.update_estimates("LABRUN_arsenal", "p1", view)

        assert seen["p2"].emps_min <= 1 <= seen["p2"].emps_max
        assert seen["p2"].chaff_min <= 1 <= seen["p2"].chaff_max
        assert seen["p2"].has_any()
        assert not seen["p3"].has_any()
        assert "not inferred" in " ".join(seen["p2"].inferences), (
            "the agent should be able to tell a stated fact from a deduced one"
        )
    finally:
        mod.clear_store()


def test_the_estimator_prices_a_rack_at_the_games_own_table():
    """v1.36 — prices are stamped per game, and the view publishes the
    stamp. A fork reading an archived season must decode against the
    prices that season was played at, or it invents ordnance: 455 blue
    is a real rack under the old table and impossible under the new one,
    which would read as "holding nothing" rather than as a mistake."""
    from turnlab import recall

    mod = recall.fork_module("tabula_v12", "_v7.opponent_weapons")
    mod.clear_store()
    try:
        view = {
            "meta": {"rules": {"weapon_blue_costs": {"emp": 200, "chaff": 255}}},
            "station_intel": {
                "opponents": [{"seat": "p2", "arms": {"blue": 455, "cap": 600}}]
            },
        }
        seen = mod.update_estimates("LABRUN_legacy", "p1", view)
        assert (seen["p2"].emps_min, seen["p2"].emps_max) == (1, 1)
        assert (seen["p2"].chaff_min, seen["p2"].chaff_max) == (1, 1)
    finally:
        mod.clear_store()


def test_a_rack_is_one_of_each_at_most():
    """Pairs turned every run into a question about salvo economics.

    The question the lab asks is simpler — given a weapon, does this
    agent fire it? A second round only lets a fork look decisive by
    spending twice.

    v1.36 — spelled as the rule rather than as the list of racks, so
    adding a weapon means adding a rack, not editing this test into
    agreeing with whatever was added.
    """
    from turnlab import arms

    for rack in arms.RACKS:
        for kind, n in rack.counts.items():
            assert n <= 1, f"{rack.id} hands out a pair of {kind}"

    # One rack per single weapon, so every weapon can be asked about on
    # its own — that is the comparison the lab exists for.
    singles = {
        next(k for k, n in r.counts.items() if n)
        for r in arms.RACKS if sum(r.counts.values()) == 1
    }
    assert singles == set(arms.KINDS), (
        "every weapon needs a rack that hands out only that weapon"
    )


def test_the_picker_never_offers_a_rack_the_board_would_refuse():
    """v1.36 — a menu whose items are traps is worse than a short menu.

    ``arm()`` refuses a weapon the board's own season does not price,
    which is right: a night frozen before SNAP existed has no SNAP in
    its economy. But every board shipped today predates SNAP, so
    without this the launcher offered two racks that always failed.

    Marked unavailable rather than filtered out, deliberately. "That
    weapon is younger than this board" is worth telling an attendee; a
    list that is quietly shorter on some boards than others reads as a
    bug in the lab.
    """
    from turnlab import arms
    from sea_of_colours.game.weapons import (
        BLUE_COST_BY_KIND, WEAPONISED_BLUE_CAP,
    )

    legacy = arms.catalogue({})
    modern = arms.catalogue({
        "weapon_blue_costs": dict(BLUE_COST_BY_KIND),
        "weapon_blue_cap": WEAPONISED_BLUE_CAP,
    })

    # Whatever a board says is available must actually arm, and whatever
    # it says is not must actually refuse. That equivalence is the point;
    # the specific weapon that happens to be new is not.
    for board_blob, listing in (({}, legacy),
                                ({"weapon_blue_costs": dict(BLUE_COST_BY_KIND),
                                  "weapon_blue_cap": WEAPONISED_BLUE_CAP},
                                 modern)):
        for entry in listing:
            blob = dict(board_blob)
            if entry["available"]:
                arms.arm(blob, "p1", entry["id"])
            else:
                with pytest.raises(ValueError):
                    arms.arm(blob, "p1", entry["id"])
                assert entry["unavailable_because"], (
                    f"{entry['id']} is refused with no reason given"
                )

    # And no board is ever left with nothing to pick.
    assert any(e["available"] for e in legacy)

    # Asked without a board, nothing is marked unavailable — the caller
    # has not said which board, so the lab must not guess.
    assert all(e["available"] for e in arms.catalogue())

    # The default still opens a board exactly as it was frozen. Anything
    # else would arm every board by default and quietly diff an armed
    # fork against the unarmed V12 baseline.
    assert arms.DEFAULT == "empty"
    assert not any(arms.get(arms.DEFAULT).counts.values())


# ── the divergence view's diff ────────────────────────────────────────
def _run_in_node(script: str) -> str:
    """Execute a snippet against the real ``diff.js``, or skip.

    The diff is the one piece of the lab whose correctness cannot be
    argued from the source: it either reproduces both inputs or it does
    not. Asserting on the text of the file would pass just as happily
    with the algorithm broken, which is how the last one survived.
    """
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:  # pragma: no cover - depends on the machine
        pytest.skip("node is not installed")
    root = pathlib.Path(__file__).resolve().parents[2]
    prelude = (
        "const fs=require('fs');"
        f"const src=fs.readFileSync({str(root / 'turnlab/static/diff.js')!r},'utf8');"
        "const body=src.slice(src.indexOf('function esc'),"
        "  src.indexOf('/** Whether a diff is even'));"
        "const M=new Function(body+'\\nreturn {diffLines, withFolds};')();"
    )
    done = subprocess.run(
        [node, "-e", prelude + script],
        capture_output=True, text=True, timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def test_the_diff_reproduces_both_takes_it_was_given():
    """An edit script that cannot rebuild its inputs is not a diff.

    This is the property the old implementation quietly abandoned: past
    six million cells it returned a single apology row, and since the
    page counts changed rows to print "N lines, M changed", the apology
    counted as zero. A take is about 4,100 lines a side, so *every* raw
    view was over the line and every one of them reported two visibly
    different agents as identical.
    """
    out = _run_in_node(r"""
      const cases = [
        ["", ""],
        ["a", ""],
        ["", "a\nb"],
        ["a\nb\nc", "a\nB\nc"],
        ["a\nb\nc", "a\nx\ny\nb\nc"],
      ];
      // A realistic pair: 4,100 lines with a scattering of edits.
      const base = Array.from({length:4100},(_,i)=>`line ${i} :: value=${i*7%991}`);
      const mine = base.slice();
      for (let i=0;i<40;i++) mine[i*97] = mine[i*97].replace("value=","VALUE=");
      mine.splice(2000,0,"a line only the fork has");
      cases.push([base.join("\n"), mine.join("\n")]);
      // And a pair with nothing whatever in common.
      cases.push([
        Array.from({length:2200},(_,i)=>`alpha ${i}`).join("\n"),
        Array.from({length:2200},(_,i)=>`beta ${i}`).join("\n")]);

      let bad = 0, bailed = 0;
      for (const [a,b] of cases) {
        const rows = M.diffLines(a,b);
        if (rows.some(r=>r.op==="note")) bailed++;
        const ra = rows.filter(r=>r.op!=="add").map(r=>r.text).join("\n");
        const rb = rows.filter(r=>r.op!=="del").map(r=>r.text).join("\n");
        if (ra!==a || rb!==b) bad++;
      }
      console.log(JSON.stringify({cases: cases.length, bad, bailed}));
    """)
    import json as _json

    got = _json.loads(out)
    assert got["bad"] == 0, f"{got['bad']} of {got['cases']} diffs could not rebuild their inputs"
    assert got["bailed"] == 0, "the diff still gives up on large inputs"


def test_the_change_count_is_never_stood_in_for():
    """The page must not print a number it did not compute."""
    out = _run_in_node(r"""
      const base = Array.from({length:4100},(_,i)=>`line ${i} :: value=${i*7%991}`);
      const mine = base.slice();
      let edits = 0;
      for (let i=0;i<40;i++) { mine[i*97] = mine[i*97]+"  # fork"; edits++; }
      const rows = M.withFolds(M.diffLines(base.join("\n"), mine.join("\n")), null);
      // Exactly what renderRaw counts.
      const changed = rows.filter(r=>(r.op==="add"||r.op==="del") && !r.moved).length;
      console.log(JSON.stringify({edits, changed}));
    """)
    import json as _json

    got = _json.loads(out)
    assert got["changed"] > 0, (
        "40 edits over 4,100 lines and the page would still report 0 changed"
    )
    # Each rewritten line is one deletion and one addition.
    assert got["changed"] == got["edits"] * 2, (
        f"expected {got['edits'] * 2} changed rows, page would show {got['changed']}"
    )


def test_every_night_in_the_library_says_what_it_is_for():
    """A board nobody can describe is a board nobody will pick.

    Keyed per night rather than per board id, because a minted night
    writes one row per seat and a re-walked one writes a single row for
    the whole position — so keying on the id meant typing a minted
    night's blurb twice, and it is always the second copy that rots.
    """
    store = lab_store.store()
    nights = {}
    for board in boards.discover(store):
        nights.setdefault((board.source, board.day), []).append(board)

    for (source, day), group in sorted(nights.items()):
        for board in group:
            assert board.note is not None, (
                f"{source} day {day} has no note — name it or bin it"
            )
            assert board.note.tests, (
                f"{source} day {day} has no one-line 'what it tests'"
            )
        # Every seat-row of one night must describe the same night.
        assert len({b.note.name for b in group}) == 1


def test_a_highlight_that_never_closes_would_reach_the_reader_as_brackets():
    """``[[key phrase]]`` marks the load-bearing clause of a note.

    The launcher escapes a note and *then* turns the markers into
    ``<mark>``, which is what stops a blurb smuggling in markup. The cost
    of that ordering is that an unbalanced marker degrades silently: the
    regex simply does not match, and the reader gets literal brackets in
    the middle of a sentence. Nothing else in the stack would notice, so
    it is checked here, where the prose actually lives.
    """
    import re

    for key, note in boards.NOTES.items():
        for field in ("name", "tests", "state", "why"):
            text = getattr(note, field, "") or ""
            opens, closes = text.count("[["), text.count("]]")
            assert opens == closes, (
                f"{key}.{field} has {opens} '[[' and {closes} ']]' — an "
                f"unclosed highlight reaches the reader as brackets"
            )
            # Balanced but interleaved ("[[a]] b]] c[[") counts as broken
            # too: the pairs have to nest the way the regex reads them.
            assert len(re.findall(r"\[\[(.+?)\]\]", text)) == opens, (
                f"{key}.{field} has highlight markers that do not pair up"
            )


def test_seat_count_and_seed_are_read_not_written():
    """The two facts that make a board reproducible.

    Kept out of the prose deliberately. A hand-typed seed is a seed that
    will eventually disagree with the board it names, and a board you
    cannot re-create is an anecdote rather than a fixture.
    """
    store = lab_store.store()
    import dataclasses

    for board in boards.discover(store):
        blob = _blob(store, board.id)
        assert board.players == tuple(blob.get("players") or ())
        assert board.seed == blob.get("seed"), board.id

        shown = board.as_dict()
        assert shown["seats_n"] == len(blob.get("players") or ())
        assert shown["seed"] == blob.get("seed")

        # And the prose must not restate them, or the two will drift.
        # Matched as the phrase "seed <n>" rather than as a bare number:
        # a seed is often two digits, and "500 credits" contains "50".
        # A check that fires on the board's own honest prose gets the
        # prose reworded around it, which is worse than not checking.
        prose = " ".join(
            str(getattr(board.note, f.name))
            for f in dataclasses.fields(board.note)
            if isinstance(getattr(board.note, f.name), str)
        )
        assert not re.search(rf"\bseeds?\s+{board.seed}\b", prose, re.I), (
            f"{board.id} writes its seed into the blurb — it is derived"
        )


def _blob(store, session_id):
    row = store.load_session(session_id) or {}
    blob = row.get("json_state")
    while isinstance(blob, (str, bytes)):
        blob = json.loads(blob)
    return blob or {}


def test_a_packed_board_reads_back_exactly_what_it_shipped(
    tmp_path, monkeypatch,
):
    """The library travels compressed, and must not travel lossily.

    107MB of replay frames does not go in a repo, and 1.1MB does, so the
    boards ship trimmed to the one night they can read and gzipped. That
    is only acceptable if a packed board is indistinguishable from an
    unpacked one at the store boundary — otherwise everyone who clones
    the repo is testing against subtly different turns from the person
    who recorded the baselines.
    """
    from turnlab import pack

    monkeypatch.setattr(lab_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(lab_store, "_store", None)
    store = lab_store.store()

    board = "LAB_seed99_d4_p1"
    # Night 3 is the one a day-4 turn reads; 1 and 2 are dead weight.
    for day in (1, 2, 3):
        store.append_replay_frames(board, day, [
            {"day": day, "hour": h, "tag": f"d{day}h{h}"} for h in (1, 2)
        ])
    was = store.list_replay_frames(board, 3, 3)
    assert [f["tag"] for f in was] == ["d3h1", "d3h2"]

    report = pack.pack_board(tmp_path / board, write=True)
    assert report["after"] < report["before"]
    assert not (tmp_path / board / "frames.jsonl").exists(), (
        "the raw frames were left behind — the board did not actually shrink"
    )

    # A cold store, so nothing is answered out of the RAM copy.
    monkeypatch.setattr(lab_store, "_store", None)
    cold = lab_store.store()
    assert cold.list_replay_frames(board, 3, 3) == was, (
        "a packed board gave back different frames than it was given"
    )
    assert cold.list_replay_frames(board, 1, 2) == [], (
        "nights the turn cannot read should not have survived packing"
    )
    monkeypatch.setattr(lab_store, "_store", None)


def test_a_seat_no_diarist_ever_held_is_not_a_missing_journal(
    tmp_path, monkeypatch,
):
    """"There is no journal" and "I could not find the journal" differ.

    Minted boards were played by heuristics, and their source season is
    24MB of frames that only ever existed so the boards could be cut
    from it — so it does not ship. Without asking the board who played
    the seat, that absent season read as a fault: a red NO MEMORY banner
    on every seed-50 turn, pointing at storage when the real answer is
    that RED_HARVEST keeps no diary.
    """
    from turnlab import recall

    monkeypatch.setattr(lab_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(lab_store, "_store", None)
    store = lab_store.store()

    board = "LAB_seed77_d3_p1"
    store.save_session({
        "session_id": board,
        "json_state": json.dumps({
            "agents": {"p1": "red_harvest", "p2": "tabula_v12"},
        }),
    })

    # The season is deliberately absent, as it will be for every clone.
    got = recall.hydrate(
        "LABRUN_x", "p1", "tabula_v12",
        board_id=board, day=3, store=store,
    )
    assert not got.error, f"a heuristic seat reported a fault: {got.error}"
    assert "keeps no journal" in (got.note or "")
    assert "RED_HARVEST" in (got.note or ""), (
        "the note should name who actually played the seat"
    )

    # A seat a diarist DID hold still goes looking, and still complains
    # when it cannot get there — that one is a real fault.
    monkeypatch.setattr(recall, "source_of", lambda _b: ("", None))
    missing = recall.hydrate(
        "LABRUN_x", "p2", "tabula_v12",
        board_id=board, day=3, store=store,
    )
    assert missing.error, "a lost V12 journal must still be reported"
    monkeypatch.setattr(lab_store, "_store", None)


def test_packing_never_drops_the_night_a_turn_actually_reads():
    """The one rule the trimmer has, stated where it can fail loudly."""
    from turnlab import pack

    assert pack.frozen_day("LAB_cfa9912d_d6_p1") == 6
    assert pack._needed_nights("LAB_cfa9912d_d6_p1") == {5}
    # A day-1 board has no history at all, and must not ask for night 0.
    assert pack._needed_nights("LAB_dd868733_d1_p1") == set()
    # An id that does not say is treated as "keep nothing", which is safe
    # only because it can never match a real board.
    assert pack.frozen_day("nonsense") == 0


def test_the_day_runs_on_through_its_orbit_and_stops_at_the_next_vespera(
    monkeypatch,
):
    """A night watched to its last hour is watched one beat too few.

    RED is still sitting in the vault when the night resolves; it only
    becomes score, and GREEN only becomes a penalty, when the orbit
    settles. So the frozen turn runs on through the orbit — and stops
    dead at the next planning phase, because a frozen turn is one night
    and its consequences, not the start of a season.
    """
    from sea_of_colours.snowpark import engine as soc_engine

    phases = iter(["orbit", "planning"])
    submitted = []

    def _status(_store, _sid):
        try:
            phase = next(phases)
        except StopIteration:  # pragma: no cover - defensive
            phase = "planning"
        return {
            "phase": phase, "day": 4, "players": ["p1", "p2"],
            "scores": {"p1": 2240, "p2": 1342},
        }

    monkeypatch.setattr(soc_engine, "get_session_status", _status)
    monkeypatch.setattr(
        soc_engine, "submit_orbit_actions",
        lambda _st, _sid, seat, actions: submitted.append((seat, actions)),
    )

    out = turn.settle("LABRUN_x", store=object())

    assert out["settled"] is True
    assert out["phase"] == "planning", "the turn must stop at the next vespera"
    assert out["scores"] == {"p1": 2240, "p2": 1342}
    # Empty baskets: settlement ships RED by itself, so buying anything
    # here would be the lab making a decision the human never accepted.
    assert submitted == [("p1", []), ("p2", [])], (
        f"the lab spent credits on somebody's behalf: {submitted}"
    )


def test_settling_a_day_that_is_not_in_orbit_does_nothing(monkeypatch):
    """Idempotent, because the page calls it from a poll.

    ``_labSettleDay`` fires from the status handler and the status
    handler runs on a timer, so this route gets asked repeatedly once a
    turn is spent. Answering "already done" has to be cheap and must
    not advance anything.
    """
    from sea_of_colours.snowpark import engine as soc_engine

    fired = []
    monkeypatch.setattr(
        soc_engine, "get_session_status",
        lambda _s, _i: {"phase": "planning", "day": 4, "players": ["p1"]},
    )
    monkeypatch.setattr(
        soc_engine, "submit_orbit_actions",
        lambda *a, **k: fired.append(a),
    )

    out = turn.settle("LABRUN_x", store=object())
    assert out["settled"] is False
    assert not fired, "a settled day was settled again"


def test_old_runs_are_reclaimed_but_live_ones_are_not(tmp_path, monkeypatch):
    """An opened run cannot be discarded when the turn ends.

    The page is still holding it, so nothing was ever reclaiming them:
    one directory per "open this turn", kept forever, mostly replay
    frames belonging to turns nobody had looked at in weeks. Opening
    each turn in its own tab — now the intended way to use the launcher
    — makes them accumulate faster.

    Two guards, because the failure modes are not symmetric. Age alone
    would reap a run somebody left open over lunch; count alone would
    reap the third turn of an afternoon spent comparing four.
    """
    import os
    import time

    monkeypatch.setattr(lab_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(lab_store, "_store", None)
    store = lab_store.store()

    now = time.time()
    old = now - (48 * 3600)

    def put(sid, mtime):
        store.save_session({"session_id": sid, "day": 1, "json_state": {}})
        os.utime(tmp_path / sid, (mtime, mtime))

    put("LAB_board_d1_p1", old)          # a board, however old
    for i in range(4):
        put(f"LABRUN_stale{i}", old)     # long finished
    put("LABRUN_openrightnow", now)      # somebody's live tab

    dropped = lab_store.sweep_runs(keep=2, hours=12)

    assert "LAB_board_d1_p1" not in dropped, (
        "a board was reclaimed — boards are the fixed points, and checked in"
    )
    assert "LABRUN_openrightnow" not in dropped, "a live run was reclaimed"
    # Five runs, keep=2: the newest two survive on position (the live one
    # and one stale), and the remaining three are old enough to go.
    assert len(dropped) == 3, f"expected the 3 oldest, got {dropped}"
    assert set(dropped) < {f"LABRUN_stale{i}" for i in range(4)}
    assert store.load_session("LAB_board_d1_p1") is not None
    assert store.load_session("LABRUN_openrightnow") is not None
    for sid in dropped:
        assert store.load_session(sid) is None, f"{sid} survived its own deletion"

    monkeypatch.setattr(lab_store, "_store", None)


def test_a_board_only_accepts_a_source_season_that_corroborates_it(monkeypatch, tmp_path):
    """Eight hex characters is not an identity.

    A board records only the first eight characters of the season it was
    cut from, and the lab used to treat any session starting with them
    as the source. It found one in the wild: a stray empty session in
    `seasons/` beginning `dd868733` was being reported as the origin of
    the `dd868733` boards, which came from somewhere else entirely. It
    was harmless only because the impostor had no journal to hand over.
    The next collision would seed a frozen turn with another game's
    INTENT and REFLECTION, and an agent reasoning off those is wrong in
    a way nobody would think to check.
    """
    from sea_of_colours.snowpark.file_store import FileSocStore
    from turnlab import recall

    lab = FileSocStore(str(tmp_path / "lab"))
    other = FileSocStore(str(tmp_path / "seasons"))

    def put(store, sid, *, season, seed):
        store.save_session({
            "session_id": sid,
            "json_state": {
                "session_id": sid, "season_name": season, "seed": seed,
                "players": ["p1"], "day": 2,
            },
        })

    # The board, and a decoy that shares its stub but nothing else.
    put(lab, "LAB_feed1234_d2_p1", season="RealSeason", seed=99)
    put(other, "feed1234ffffffffffffffffffffffff", season="Coincidence", seed=7)

    monkeypatch.setattr(lab_store, "_store", lab)
    monkeypatch.setattr(recall, "_candidate_stores", lambda: [("other", other)])
    recall.forget()

    found, _ = recall.source_of("LAB_feed1234_d2_p1")
    assert found == "", (
        f"matched {found!r} on a prefix alone — it disagrees on both "
        "season name and seed, so it is a different game"
    )

    # And the same stub, corroborated, must still resolve — a guard that
    # rejects everything is not a guard.
    put(other, "feed1234ffffffffffffffffffffffff",
        season="RealSeason", seed=99)
    recall.forget()
    found, _ = recall.source_of("LAB_feed1234_d2_p1")
    assert found == "feed1234ffffffffffffffffffffffff", (
        "rejected the genuine source season"
    )
