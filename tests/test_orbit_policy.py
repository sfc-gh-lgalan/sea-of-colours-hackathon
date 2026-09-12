"""The fork's own buying policy (v1.40).

``tabula_v12/orbit_policy.py`` was forked out of the shared heuristic so
each agent can own how it spends credits and BLUE. Two things have to
stay true for that fork to be safe:

1. **At the shipped dials it is the shared planner.** V12 is the baseline
   every attendee fork is measured against, so moving the code must not
   move the scores. The differential test below is the pin.
2. **The dials actually do something.** A knob that reads well and
   changes nothing is worse than no knob — an attendee edits it, sees no
   movement, and stops trusting the kit.

If you retune :class:`OrbitDials`, test 1 is *expected* to fail. That is
the signal that your agent has diverged from the baseline, which is the
whole point; update or drop it rather than reverting the retune.
"""

from __future__ import annotations

import os
import random

import pytest

os.environ.setdefault("SOC_BACKEND", "memory")

from sea_of_colours.agent.heuristic_agent import (  # noqa: E402
    plan_orbit_actions as shared_plan,
)
from sea_of_colours.orchestrator_2.harnesses.tabula_v12.orbit_policy import (  # noqa: E402
    DEFAULT_DIALS,
    OrbitDials,
    plan_orbit_actions as fork_plan,
)


def _view(
    *,
    credits: int = 1000,
    cap_used: int = 1,
    cap_max: int = 3,
    damaged: int = 0,
    healthy: int = 1,
    blue: int = 0,
    emp_stock: int = 0,
    chaff_stock: int = 0,
    probe_stock: int = 0,
    final_orbit: bool = False,
    day: int = 1,
    session_id: str = "diff-test",
) -> dict:
    """An orbit view with the knobs the buying policy actually reads."""
    mine = [
        {"id": f"h_dmg_{i}", "type": "harvester", "pos": None, "damaged": True}
        for i in range(damaged)
    ] + [
        {"id": f"h_ok_{i}", "type": "harvester", "pos": None, "damaged": False}
        for i in range(healthy)
    ]
    return {
        "phase": "orbit",
        "entities": {"mine": mine, "echoes": []},
        "orbit": {
            "phase_active": True,
            "final_orbit": final_orbit,
            "credits": credits,
            "harvester_cap_used": cap_used,
            "harvester_cap_max": cap_max,
            "probe_stock": probe_stock,
            "ship_prices": {
                "harvester_build": 1500,
                "probe_build": 250,
                "repair": 500,
            },
            "blue_purity_total": blue,
            "weapon_stock": {"emp": emp_stock, "chaff": chaff_stock},
            "weapon_prices": {
                "emp": {"blue": 200, "credits": 0},
                "chaff": {"blue": 255, "credits": 0},
            },
        },
        "meta": {"session_id": session_id, "player": "p1", "day": day},
        "hud": {"day": day, "player": "p1"},
    }


# ── 1. The fork is the shared planner, at shipped dials ───────────


def _sweep(n: int = 400):
    """A spread of orbit states wide enough to hit every branch."""
    rng = random.Random(20260901)
    for i in range(n):
        yield _view(
            credits=rng.choice([0, 200, 500, 900, 1500, 2000, 4000]),
            cap_used=rng.randint(0, 3),
            cap_max=3,
            damaged=rng.randint(0, 2),
            healthy=rng.randint(0, 2),
            # Straddle both thresholds (250 roll, 300 always) and the
            # 255 chaff price, so every weapons branch is reachable.
            blue=rng.choice([0, 100, 249, 251, 260, 299, 301, 400, 900]),
            emp_stock=rng.randint(0, 3),
            chaff_stock=rng.randint(0, 2),
            probe_stock=rng.randint(0, 5),
            final_orbit=(i % 37 == 0),
            day=rng.randint(1, 7),
            session_id=f"s{i}",
        )


@pytest.mark.parametrize("weapons_enabled", [True, False])
def test_the_fork_matches_the_shared_planner_exactly(weapons_enabled):
    """Same actions AND same rationale, across the whole sweep.

    The rationale is included on purpose: it lands on the agent card, so
    a drift there is a visible change to what the attendee reads.
    """
    for view in _sweep():
        want = shared_plan(dict(view), weapons_enabled=weapons_enabled)
        got = fork_plan(dict(view), weapons_enabled=weapons_enabled)
        assert got == want, (
            f"fork diverged from the shared planner\n"
            f"  orbit: {view['orbit']}\n"
            f"  shared: {want}\n"
            f"  fork:   {got}"
        )


def test_the_sweep_actually_reaches_every_branch():
    """A differential test over a sweep that misses branches proves little."""
    seen = set()
    for view in _sweep():
        actions, rationale = fork_plan(dict(view))
        seen.update(a["a"] for a in actions)
        if "nothing worth buying" in rationale:
            seen.add("final_orbit")
        if "roll missed" in rationale:
            seen.add("emp_roll_missed")
        if "unaffordable" in rationale:
            seen.add("weapon_unaffordable")
        if "magazine full" in rationale:
            seen.add("probes_full")
        if "fleet at cap" in rationale:
            seen.add("fleet_capped")
    for branch in (
        "repair", "build_harvester", "build_probe", "build_emp",
        "build_chaff", "final_orbit", "emp_roll_missed", "probes_full",
        "fleet_capped",
    ):
        assert branch in seen, f"sweep never exercised {branch!r}"


# ── 2. The dials are real levers ──────────────────────────────────


def test_lowering_the_threshold_buys_weapons_earlier():
    """The headline knob: buy at 150 blue instead of 300."""
    view = _view(credits=2000, blue=200, cap_used=3)

    stock, _ = fork_plan(dict(view))
    assert not [a for a in stock if a["a"].startswith("build_emp")], (
        "shipped dials should not buy at 200 blue"
    )

    eager = OrbitDials(blue_always_build=150, blue_emp_roll=100)
    got, rationale = fork_plan(dict(view), dials=eager)
    assert {"a": "build_emp", "count": 1} in got
    assert "built EMP" in rationale


def test_raising_the_stockpile_cap_keeps_buying():
    """Stock at the shipped cap blocks the buy; a higher cap allows it.

    v1.34 — this used to hold 2 EMP and a chaff, which is 655 blue of
    ordnance and no longer a state the engine can be in (§4.9.8 caps it
    at 600). Two EMP is 400 and leaves room for a third, so the dial is
    still the only thing under test.

    Sat in the middle (roll) band with the flip pinned, because in the
    always-build band the surplus top-up would buy the EMP regardless of
    this dial — which would make the test pass for the wrong reason.
    """
    view = _view(credits=2000, blue=260, cap_used=3, chaff_stock=0, emp_stock=2)

    shipped = OrbitDials(emp_roll_chance=1.0)
    stock, _ = fork_plan(dict(view), dials=shipped)
    assert not [a for a in stock if a["a"] == "build_emp"]

    deep = OrbitDials(emp_stockpile_cap=5, emp_roll_chance=1.0)
    got, _ = fork_plan(dict(view), dials=deep)
    assert {"a": "build_emp", "count": 1} in got


def test_the_arsenal_cap_declines_instead_of_proposing_a_doomed_build():
    """A seat at the ceiling says so, rather than ordering a refusal.

    The engine would reject the build anyway (§4.9.8), so this is about
    not spending the move on it — and about the rationale naming the
    real reason, since "unaffordable" would be a lie with 2000c in hand.
    """
    view = _view(credits=2000, blue=900, cap_used=3, chaff_stock=0, emp_stock=3)

    actions, rationale = fork_plan(dict(view))
    assert not [a for a in actions if a["a"].startswith("build_emp")]
    assert not [a for a in actions if a["a"].startswith("build_chaff")]
    assert "arsenal cap" in rationale, rationale
    assert "unaffordable" not in rationale


def test_the_probe_target_sets_the_magazine():
    view = _view(credits=4000, cap_used=3, probe_stock=0)
    shipped, _ = fork_plan(dict(view))
    assert {"a": "build_probe", "count": DEFAULT_DIALS.probe_target_stock} in shipped

    lean, _ = fork_plan(dict(view), dials=OrbitDials(probe_target_stock=2))
    assert {"a": "build_probe", "count": 2} in lean


def test_the_roll_can_be_made_certain():
    """emp_roll_chance=1.0 removes the coin flip from the middle band."""
    always = OrbitDials(emp_roll_chance=1.0)
    for day in range(1, 8):
        view = _view(credits=2000, blue=260, cap_used=3, day=day)
        got, _ = fork_plan(dict(view), dials=always)
        assert {"a": "build_emp", "count": 1} in got, f"day {day} missed the roll"


# ── 3. The fork stays a fork ──────────────────────────────────────


def test_the_orbit_path_does_not_call_the_shared_planner():
    """The point of the fork: an attendee edit must actually take effect.

    If ``orbit.py`` reverts to importing the shared planner, every fork
    in the room silently shares one buying policy again and nothing an
    attendee writes in ``orbit_policy.py`` runs.
    """
    from pathlib import Path

    orbit_py = (
        Path(__file__).resolve().parents[1]
        / "sea_of_colours/orchestrator_2/harnesses/tabula_v12/orbit.py"
    )
    src = orbit_py.read_text(encoding="utf-8")
    assert "heuristic_agent import plan_orbit_actions" not in src, (
        "tabula_v12/orbit.py is importing the shared planner again — "
        "the fork no longer owns its buying policy"
    )
    assert "orbit_policy import" in src


def test_the_policy_module_borrows_no_policy_from_outside_the_fork():
    """A copied directory must keep working — and keep its OWN doctrine.

    v14 — this used to forbid ``from sea_of_colours`` outright, which
    reads as isolation but was really a ban on single-sourcing. The prices
    were retyped as literals under a "mirrors of the engine constants"
    comment, and two of them stopped mirroring: ``emp_credit_cost`` said 0
    where ``game/weapons.py`` says 250, and there were no SNAP dials at
    all. AGENTS.md is explicit that this is the expensive direction to get
    wrong — attendee forks copy V12 wholesale, so a stale literal here is
    replicated into every fork in the room and cannot be fixed centrally.

    So the rule is narrowed to what actually matters. A fork may read
    ENGINE constants, which are the same numbers for everyone and are not
    anybody's policy. It may not import another harness's code or the
    shared heuristic planner, because that is how a room of forks quietly
    ends up sharing one buying policy again.
    """
    from pathlib import Path

    policy = (
        Path(__file__).resolve().parents[1]
        / "sea_of_colours/orchestrator_2/harnesses/tabula_v12/orbit_policy.py"
    )
    # Parsed, not grepped: the module docstring names the shared planner it
    # was carved out of, and a substring scan reads that history as a
    # dependency.
    import ast

    allowed = {"sea_of_colours.game.session", "sea_of_colours.game.weapons"}
    modules = set()
    for node in ast.walk(ast.parse(policy.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
    reaching_out = {m for m in modules if m.startswith("sea_of_colours")}
    assert reaching_out <= allowed, (
        "orbit_policy.py may only reach outside the fork for ENGINE "
        f"constants, never for behaviour — found: {sorted(reaching_out - allowed)}"
    )
