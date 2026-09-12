"""Minting a fork is the first thing an attendee does (v1.12).

``scripts/new_agent.py`` copies V12, renames its identity, and declares
it in an ``agent.json`` inside the new directory. If it half-works the
failure lands on someone with an hour to spend, so the contract is
pinned here: validation rejects bad names *before* writing, and minting
touches nothing outside the fork.

The copy itself is exercised through ``--dry-run``. Running it for real
would mutate the repo mid-suite, and the interesting failure modes
(refusing bad input, leaving no partial state) are all pre-write.
"""

from __future__ import annotations

import os
import shutil

os.environ.setdefault("SOC_BACKEND", "memory")

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app import app

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "new_agent.py"
_REGISTRY = _REPO / "sea_of_colours" / "orchestrator_2" / "binding_registry.py"
_HARNESSES = _REPO / "sea_of_colours" / "orchestrator_2" / "harnesses"

client = TestClient(app)


def _run(*args: str) -> subprocess.CompletedProcess:
    """Mint, supplying a roster unless the test is about the roster.

    ``--participants`` became required in v1.41. Defaulting it here
    keeps every test below about the thing it was written for; the
    requirement itself is pinned in test_agent_participants.py and in
    the one test that overrides this.
    """
    argv = list(args)
    if "--participants" not in argv:
        argv += ["--participants", "Ada Lovelace"]
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *argv],
        capture_output=True, text=True, cwd=_REPO,
    )


def test_minting_without_a_roster_is_refused() -> None:
    """You cannot create an agent nobody is credited for."""
    r = subprocess.run(
        [sys.executable, str(_SCRIPT),
         "--team", "unittest", "--name", "probe", "--dry-run"],
        capture_output=True, text=True, cwd=_REPO,
    )
    assert r.returncode != 0
    assert "participants" in (r.stderr + r.stdout)


def test_dry_run_reports_without_writing() -> None:
    before = sorted(p.name for p in _HARNESSES.iterdir())
    registry_before = _REGISTRY.read_text(encoding="utf-8")

    r = _run("--team", "unittest", "--name", "probe", "--dry-run")

    assert r.returncode == 0, r.stderr
    assert "would create" in r.stdout
    assert "unittest_probe" in r.stdout
    assert sorted(p.name for p in _HARNESSES.iterdir()) == before
    assert _REGISTRY.read_text(encoding="utf-8") == registry_before


def test_capitalisation_is_normalised_not_rejected() -> None:
    """``--team Redwatch`` is a reasonable thing to type; lowercase it
    rather than making someone read an error to learn the convention."""
    r = _run("--team", "Redwatch", "--name", "Reaper", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "redwatch_reaper" in r.stdout


@pytest.mark.parametrize(
    "team,name",
    [
        ("red watch", "reaper"),  # spaces are not importable
        ("red-watch", "reaper"),  # hyphens are not importable
        ("9team", "reaper"),      # cannot start a Python module with a digit
        ("redwatch", "reaper!"),  # punctuation
        ("redwatch_", "reaper"),  # would produce a double underscore
    ],
)
def test_unusable_names_are_refused(team: str, name: str) -> None:
    """The label becomes a package directory and a dict key, so anything
    that isn't a plain identifier has to fail loudly and early."""
    r = _run("--team", team, "--name", name, "--dry-run")
    assert r.returncode != 0, f"{team}/{name} should have been refused"
    assert "error:" in r.stderr


def test_existing_name_is_refused_before_any_copy() -> None:
    """tabula_v12 is already registered; the guard must fire on the
    registry check, not after copying 44 files over the original."""
    r = _run("--team", "tabula", "--name", "v12", "--dry-run")
    assert r.returncode != 0
    assert "already" in r.stderr


def test_minting_leaves_the_shared_registry_alone() -> None:
    """The property the whole submission model rests on (v1.39).

    Registration used to mean inserting two lines here, which meant
    every team's push conflicted with every other team's. A fork now
    declares itself inside its own directory, so minting must not touch
    this file at all — if it starts doing so again, forty people find
    out at once and late in the day.
    """
    before = _REGISTRY.read_text(encoding="utf-8")
    r = _run("--team", "unittest", "--name", "probe", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert _REGISTRY.read_text(encoding="utf-8") == before
    assert "discovery" in r.stdout


def test_roster_endpoint_serves_the_registry() -> None:
    """The New Game dropdown is built from this, so a fork appears
    without a frontend edit."""
    from sea_of_colours.orchestrator_2.binding_registry import selectable_agents

    r = client.get("/api/meta/agents")
    assert r.status_code == 200
    served = r.json()["agents"]
    assert served == selectable_agents()
    assert {a["value"] for a in served} >= {"human", "tabula_v12"}


def test_frontend_no_longer_hardcodes_the_roster() -> None:
    """A hardcoded list is allowed to survive *as a fallback*, but the
    render path must read the fetched roster or forks stay invisible."""
    src = (_REPO / "server" / "static" / "app.js").read_text(encoding="utf-8")
    assert "/api/meta/agents" in src
    assert "agentRoster()" in src


def test_a_minted_fork_reaches_both_the_game_and_the_lab() -> None:
    """Minting is the only step. There is no second one.

    A fork that exists on disk has to turn up in two places without
    anyone doing anything else: the New Game seat picker, and the lab's
    cast list. They are separate code paths — ``selectable_agents`` for
    the modal, ``turnlab.cast.roster`` for the lab — and the failure
    they can each have is the same and is silent. The fork works, the
    tests pass, and it simply is not in the menu, which reads to the
    person who built it as "my agent is broken".

    Checked against whatever this machine actually has rather than a
    fixed name, so it holds in a room where every laptop has a
    different fork on it.
    """
    from sea_of_colours.orchestrator_2 import agent_manifest
    from turnlab import cast

    manifests, problems = agent_manifest.discover()
    assert not problems, f"a fork on disk failed to load: {problems}"

    served = {a["value"] for a in client.get("/api/meta/agents").json()["agents"]}
    castable = {a.label for a in cast.roster()[0]}

    for man in manifests:
        assert man.label in served, (
            f"{man.label} is on disk but not in the New Game seat picker"
        )
        assert man.label in castable, (
            f"{man.label} is on disk but cannot be cast in the lab"
        )


def test_a_forks_tests_stay_inside_the_fork() -> None:
    """A fork may not put its tests in the shared suite (v1.43).

    ``tests/test_emp_harvest_fork.py`` did, and the consequence showed
    up the first time its author edited the fork's doctrine: the repo's
    only red belonged to one team's work in progress. With a room of
    them that is everybody's suite, all day.

    The rule is the same one registration follows — a fork is one
    directory, and everything it owns lives in it. Its tests still run,
    on request: ``pytest <the fork's directory>``.
    """
    import configparser

    from sea_of_colours.orchestrator_2 import binding_registry as br

    cfg = configparser.ConfigParser()
    cfg.read(_REPO / "pytest.ini")
    shared = cfg["pytest"]["testpaths"].split()

    # Shipped agents are exempt: V12 is the baseline the whole repo is
    # about, and the shared suite is exactly where it should be tested.
    harnesses = _REPO / "sea_of_colours" / "orchestrator_2" / "harnesses"
    forks = {
        d.name for d in harnesses.iterdir()
        if d.is_dir() and (d / "agent.json").exists()
        and d.name not in br.SHIPPED_AGENT_LABELS
    }

    # Reaching into a fork's *package* is the tell. Naming one in a
    # parametrize list is the opposite — that is the shared suite
    # checking a property every fork inherits, which is a thing we want
    # more of, not less.
    strays = sorted(
        str(p.relative_to(_REPO))
        for path in shared
        for p in (_REPO / path).rglob("test_*.py")
        if any(f"harnesses.{fork}" in p.read_text(encoding="utf-8",
                                                  errors="ignore")
               for fork in forks)
    )
    assert not strays, (
        "these live in the shared suite but import a fork directly; move "
        f"them into that fork's own directory: {strays}"
    )


def test_a_minted_fork_can_buy_weapons() -> None:
    """The mint arms the fork, whatever the baseline does (v1.48).

    V12 passes ``weapons_enabled=False`` to its orbital planner, which is
    right for the baseline and wrong for everyone downstream of it: the
    mint is a wholesale copy, so forks inherited the off-switch, skipped
    weapon purchase for entire seasons and never said why — the
    descriptor that would have explained it is gated on the same flag.

    Pinned end to end on a real mint rather than by grepping the script,
    because the failure was never in the intent. It was that nothing
    checked what the copy came out as.
    """
    label = "unittest_armed"
    dest = _HARNESSES / label
    assert not dest.exists(), f"{label} left over from an earlier run"
    baseline = _HARNESSES / "tabula_v12" / "orbit.py"
    before = baseline.read_text(encoding="utf-8")
    try:
        r = _run("--team", "unittest", "--name", "armed")
        assert r.returncode == 0, r.stderr

        orbit = (dest / "orbit.py").read_text(encoding="utf-8")
        assert "plan_orbit_actions(agent_view)" in orbit, (
            "the minted fork does not call the orbital planner armed"
        )
        code = "\n".join(
            line.split("#", 1)[0] for line in orbit.splitlines()
        )
        assert "weapons_enabled=False" not in code, (
            "the minted fork still disables weapon buying"
        )
    finally:
        shutil.rmtree(dest, ignore_errors=True)

    # The baseline is what forks are measured against, so arming the copy
    # must not have been done by editing the original.
    assert baseline.read_text(encoding="utf-8") == before, (
        "minting modified tabula_v12/orbit.py"
    )
