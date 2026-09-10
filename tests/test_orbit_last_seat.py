"""The last seat to submit is not a seat that missed (v1.47).

A harness seat's orbit turn was reconciled by asking the engine "is this
seat still pending?". That question is phase-aware — it reports orbit
stashes during ORBIT and policies during PLANNING — and the last seat's
submit is the one that *completes* the orbit phase. So by the time the
runtime asked, the engine had advanced, cleared the stashes and started
answering about policies, and the one seat known to have submitted read
as the one seat that had not. The runtime then logged a spurious
``[fallback]`` over the harness's own rationale and re-submitted.

Nothing failed loudly, which is why it lasted: the duplicate submit is
refused as wrong-phase so scores are untouched, the season runner's
fallback counter does not match the orbit rationale's wording so it
reported zero, and a heuristic seat returns before this code — meaning
the ordinary fork-vs-RED_HARVEST pairing only shows it when the
heuristic sits in p1. It took three forks playing each other to surface.

These run entirely offline: the harness orbit path is its own heuristic
buying policy plus an engine submit, with no model call in it.
"""

from __future__ import annotations

import pytest

from sea_of_colours.evals import dispatch
from sea_of_colours.snowpark import engine as soc_engine


HARNESS = "tabula_v12"


@pytest.fixture(autouse=True)
def real_orbit_phase(monkeypatch):
    """Opt this module back in to the orbit phase.

    ``tests/conftest.py`` patches the whole suite to skip it: the suite
    predates orbit and expects every night to flip straight back to
    PLANNING. That is a fair shim for the legacy fixtures, and it is
    also the reason this bug survived — with orbit patched out, no test
    under ``tests/`` has ever driven a harness orbit turn, so the one
    path where the reconciliation is always exercised had no coverage
    at all.
    """
    import conftest

    from sea_of_colours.game import session as session_mod
    from sea_of_colours.game import simulator as simulator_mod
    from sea_of_colours.snowpark import engine as engine_mod

    monkeypatch.setattr(
        session_mod.GameSession, "new",
        classmethod(conftest._orig_new.__func__),
    )
    monkeypatch.setattr(engine_mod, "init_session", conftest._orig_init)
    monkeypatch.setattr(
        simulator_mod.NightSimulator, "run", conftest._orig_night_run,
    )


@pytest.fixture()
def store(monkeypatch):
    monkeypatch.setenv("SOC_BACKEND", "memory")
    from sea_of_colours.snowpark import backend

    return backend.get_store()


def _session_in_orbit(store, agents: list[str]) -> tuple[str, dict[str, str]]:
    """A session parked in ORBIT on day 2, one seat per agent.

    Day 1 opens in PLANNING, so an empty policy from every seat is the
    shortest way to the phase under test.
    """
    seats = [f"p{i}" for i in range(1, len(agents) + 1)]
    lineup = dict(zip(seats, agents))
    info = soc_engine.init_session(
        store, seed=7, width=24, height=18, season_day_cap=3,
        players=seats,
        agents={s: dispatch.strategy_slug(a) for s, a in lineup.items()},
    )
    sid = info["session_id"]
    for seat in seats:
        soc_engine.submit_policy(store, sid, seat, [])
    assert soc_engine.get_session_status(store, sid)["phase"] == "orbit"
    return sid, lineup


def _orbit_rationales(store, sid: str, lineup: dict[str, str]) -> dict[str, str]:
    return {
        seat: str((dispatch.play_turn(store, sid, seat, agent) or {}).get(
            "rationale") or "")
        for seat, agent in lineup.items()
    }


@pytest.mark.parametrize("seat_count", [2, 3, 4])
def test_no_seat_is_called_a_miss_for_going_last(store, seat_count):
    """Every harness seat owns its orbit turn, including the closing one."""
    sid, lineup = _session_in_orbit(store, [HARNESS] * seat_count)

    rationales = _orbit_rationales(store, sid, lineup)

    missed = [s for s, r in rationales.items() if r.startswith("[fallback]")]
    assert not missed, f"spurious orbit miss on {missed}: {rationales}"
    assert all(r.startswith("[orbit heuristic]") for r in rationales.values())


def test_the_heuristic_in_p1_used_to_hide_it(store):
    """The pairing that surfaced it, pinned so it cannot come back.

    A heuristic seat returns before the reconciliation path entirely, so
    with RED_HARVEST last there was nothing to see and with RED_HARVEST
    first the fork ate a fallback it had not earned.
    """
    sid, lineup = _session_in_orbit(store, ["red_harvest", HARNESS])

    rationales = _orbit_rationales(store, sid, lineup)

    assert rationales["p2"].startswith("[orbit heuristic]")


def test_a_real_miss_is_still_reported(store, monkeypatch):
    """The safety net still fires when a harness genuinely does not submit.

    Worth pinning separately: a reconciliation that answered "landed"
    on phase movement alone could paper over a true miss by the closing
    seat, which is the failure this whole path exists to catch.
    """
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import orbit as orbit_mod

    monkeypatch.setattr(
        orbit_mod, "submit_orbit",
        lambda *a, **k: {"ok": False, "rationale": "did nothing"},
    )
    sid, lineup = _session_in_orbit(store, [HARNESS, HARNESS])

    rationales = _orbit_rationales(store, sid, lineup)

    assert rationales["p1"].startswith("[fallback]")
    assert rationales["p2"].startswith("[fallback]")
