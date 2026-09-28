"""The collector, exercised against real git repositories.

v1.44. These tests build actual repos in a tmpdir and fetch between them
over local paths, because the interesting failures here are all git
failures — a checkout that writes where it should not, a reset that does
not clear what the last run left, a fetch that silently gets nothing.
A mocked ``git`` would pass while every one of those was broken.

Nothing here touches the network or the real repo.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from sea_of_colours.orchestrator_2 import fork_collect


@pytest.fixture(autouse=True)
def _no_dev_machine_git_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run against stock git, not the organiser's (v1.56).

    These fixtures build throwaway repos, so anything in a personal
    ``~/.gitconfig`` leaks straight into them — ``commit.gpgsign = true``
    being the one that bit, since the commits below then fail outright,
    or worse, block on a passphrase prompt pytest has captured and
    nobody can see. Hooks and commit templates get in the same way.
    ``test_soc_push_race.py`` already does this; the two should match.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")


# ── building repos to collect from ────────────────────────────────────

def _run(cwd: Path, *args: str) -> None:
    done = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, f"{' '.join(args)} → {done.stderr}"


def _agent(root: Path, label: str, *, participants=("Ada",)) -> None:
    """Write a harness that discovery would accept."""
    team, _, name = label.partition("_")
    where = root / fork_collect.HARNESS_PREFIX / label
    where.mkdir(parents=True, exist_ok=True)
    (where / "agent.json").write_text(json.dumps({
        "team": team,
        "name": name,
        "participants": list(participants),
    }), encoding="utf-8")
    (where / "harness.py").write_text("def run(*a, **k):\n    return {}\n",
                                      encoding="utf-8")


def _commit(root: Path, message: str) -> None:
    _run(root, "git", "add", "-A")
    # Signing is off explicitly (v1.56). An organiser with
    # `commit.gpgsign = true` in their global config inherits it here,
    # and these throwaway repos then fail to commit — on a machine where
    # the key needs a passphrase the suite hangs on a pinentry prompt
    # nobody can see. The fixture's identity is already overridden for
    # the same reason; this is the other half of that.
    _run(root, "git", "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "commit.gpgsign=false",
         "commit", "-q", "--no-gpg-sign", "-m", message)


@pytest.fixture()
def upstream(tmp_path: Path) -> Path:
    """The repo the day runs from. Ships one agent; owns no entrants."""
    root = tmp_path / "upstream"
    root.mkdir()
    _run(root, "git", "init", "-q", "-b", "main")
    _agent(root, "tabula_v12", participants=("the kit",))
    _commit(root, "ship")
    return root


def _fork_of(upstream: Path, owner: str) -> fork_collect.Fork:
    clone = upstream.parent / f"fork-{owner}"
    _run(upstream.parent, "git", "clone", "-q", str(upstream), str(clone))
    return fork_collect.Fork(
        owner=owner,
        full_name=f"{owner}/sea-of-colours-hackathon",
        clone_url=str(clone),
        default_branch="main",
    )


def _push_agent(fork: fork_collect.Fork, label: str, **kw) -> None:
    root = Path(fork.clone_url)
    _agent(root, label, **kw)
    _commit(root, f"add {label}")


def _staging(tmp_path: Path, upstream: Path) -> Path:
    return fork_collect.prepare_staging(
        tmp_path / "league", "org/soc", clone_url=str(upstream)
    )


# ── the happy path ────────────────────────────────────────────────────

def test_two_forks_become_one_field(tmp_path, upstream):
    one = _fork_of(upstream, "ada")
    _push_agent(one, "redwatch_reaper", participants=("Ada", "Grace"))
    two = _fork_of(upstream, "linus")
    _push_agent(two, "bluewatch_owl", participants=("Linus",))

    root = _staging(tmp_path, upstream)
    report = fork_collect.collect(root, [one, two])

    assert report.ok
    assert {t.label for t in report.taken} == {"redwatch_reaper",
                                               "bluewatch_owl"}
    for label in ("redwatch_reaper", "bluewatch_owl"):
        assert (root / fork_collect.HARNESS_PREFIX / label /
                "agent.json").is_file()

    # The people are carried across, because the league has to be able to
    # credit somebody and the fork name is an account, not a person.
    who = {t.label: t.participants for t in report.taken}
    assert who["redwatch_reaper"] == ("Ada", "Grace")


def test_the_repo_the_forks_pull_from_is_never_written_to(tmp_path, upstream):
    """The whole reason for a staging area."""
    fork = _fork_of(upstream, "ada")
    _push_agent(fork, "redwatch_reaper")

    before = sorted(p.name for p in
                    (upstream / fork_collect.HARNESS_PREFIX).iterdir())
    root = _staging(tmp_path, upstream)
    fork_collect.collect(root, [fork])
    after = sorted(p.name for p in
                   (upstream / fork_collect.HARNESS_PREFIX).iterdir())

    assert before == after == ["tabula_v12"]
    assert root != upstream


def test_the_roster_says_which_fork_each_agent_came_out_of(tmp_path, upstream):
    fork = _fork_of(upstream, "ada")
    _push_agent(fork, "redwatch_reaper", participants=("Ada",))

    root = _staging(tmp_path, upstream)
    report = fork_collect.collect(root, [fork])
    roster = fork_collect.write_roster(root, report, "org/soc")

    text = roster.read_text(encoding="utf-8")
    assert "redwatch_reaper" in text
    assert "ada/sea-of-colours-hackathon" in text
    assert "Ada" in text


# ── what it refuses ───────────────────────────────────────────────────

def test_a_fork_cannot_hand_back_the_baseline(tmp_path, upstream):
    """Taking a fork's tabula_v12 would replace what everyone is scored on.

    Every fork carries a copy of the baseline, so this must be quiet
    rather than an error — but it must never be the copy that gets used.
    """
    fork = _fork_of(upstream, "ada")
    root = Path(fork.clone_url)
    (root / fork_collect.HARNESS_PREFIX / "tabula_v12"
     / "harness.py").write_text("def run(*a, **k):\n    return 'nobbled'\n",
                                encoding="utf-8")
    _push_agent(fork, "redwatch_reaper")

    staged = _staging(tmp_path, upstream)
    report = fork_collect.collect(staged, [fork])

    assert [t.label for t in report.taken] == ["redwatch_reaper"]
    body = (staged / fork_collect.HARNESS_PREFIX / "tabula_v12"
            / "harness.py").read_text(encoding="utf-8")
    assert "nobbled" not in body
    assert report.ok, "a fork carrying the baseline is normal, not a clash"


def test_two_forks_claiming_one_name_is_reported_not_resolved(tmp_path,
                                                              upstream):
    one = _fork_of(upstream, "ada")
    _push_agent(one, "redwatch_reaper", participants=("Ada",))
    two = _fork_of(upstream, "linus")
    _push_agent(two, "redwatch_reaper", participants=("Linus",))

    root = _staging(tmp_path, upstream)
    report = fork_collect.collect(root, [one, two])

    assert len(report.taken) == 1
    clash = [s for s in report.skipped if s.what == "redwatch_reaper"]
    assert len(clash) == 1
    # Not just "taken" — the organiser has to be able to go and ask them.
    assert "ada/sea-of-colours-hackathon" in clash[0].why
    assert not report.ok, "a name clash must fail the run, not pass quietly"


def test_a_broken_manifest_is_a_skipped_entrant_not_a_crash(tmp_path,
                                                            upstream):
    fork = _fork_of(upstream, "ada")
    root = Path(fork.clone_url)
    where = root / fork_collect.HARNESS_PREFIX / "redwatch_reaper"
    where.mkdir(parents=True)
    (where / "agent.json").write_text('{"team": "redwatch",}',
                                      encoding="utf-8")
    _commit(root, "oops")

    staged = _staging(tmp_path, upstream)
    report = fork_collect.collect(staged, [fork])

    assert not report.taken
    assert any("manifest" in s.why for s in report.skipped)
    # And it does not stay behind poisoning everyone else's discovery.
    assert not (staged / fork_collect.HARNESS_PREFIX
                / "redwatch_reaper").exists()


def test_a_fork_with_no_agent_is_noted_and_skipped(tmp_path, upstream):
    fork = _fork_of(upstream, "ada")
    root = _staging(tmp_path, upstream)
    report = fork_collect.collect(root, [fork])

    assert not report.taken
    assert any("no agent of its own" in s.why for s in report.skipped)
    assert report.ok, "an empty fork is not an error, just an absence"


def test_an_owner_can_be_skipped(tmp_path, upstream):
    fork = _fork_of(upstream, "ada")
    _push_agent(fork, "redwatch_reaper")
    root = _staging(tmp_path, upstream)

    report = fork_collect.collect(root, [fork], skip_owners=["ADA"])
    assert not report.taken, "matching an owner must not be case-sensitive"


# ── the staging area itself ───────────────────────────────────────────

def test_withdrawing_an_agent_removes_it_from_the_next_field(tmp_path,
                                                             upstream):
    """A reset, not an accumulation. This is why prepare_staging cleans."""
    fork = _fork_of(upstream, "ada")
    _push_agent(fork, "redwatch_reaper")

    root = _staging(tmp_path, upstream)
    fork_collect.collect(root, [fork])
    assert (root / fork_collect.HARNESS_PREFIX / "redwatch_reaper").is_dir()

    forked = Path(fork.clone_url)
    _run(forked, "git", "rm", "-r", "-q",
         f"{fork_collect.HARNESS_PREFIX}/redwatch_reaper")
    _commit(forked, "withdraw")

    root = _staging(tmp_path, upstream)  # same path, refreshed
    report = fork_collect.collect(root, [fork])

    assert not report.taken
    assert not (root / fork_collect.HARNESS_PREFIX
                / "redwatch_reaper").exists()


def test_it_will_not_reset_a_directory_it_did_not_make(tmp_path, upstream):
    mine = tmp_path / "important"
    mine.mkdir()
    (mine / "thesis.txt").write_text("four years", encoding="utf-8")

    with pytest.raises(fork_collect.CollectError) as caught:
        fork_collect.prepare_staging(mine, "org/soc",
                                     clone_url=str(upstream))

    assert "not a staging area" in str(caught.value)
    assert (mine / "thesis.txt").is_file()


def test_a_staging_area_belongs_to_one_upstream(tmp_path, upstream):
    root = _staging(tmp_path, upstream)
    with pytest.raises(fork_collect.CollectError) as caught:
        fork_collect.prepare_staging(root, "someone/else",
                                     clone_url=str(upstream))
    assert "staging area for" in str(caught.value)


# ── path handling ─────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "sea_of_colours/orchestrator_2/harnesses/../../../etc/agent.json",
    "sea_of_colours/orchestrator_2/harnesses/a/b/agent.json",
    "harnesses/redwatch_reaper/agent.json",
    "sea_of_colours/orchestrator_2/harnesses/Redwatch/agent.json",
    "sea_of_colours/orchestrator_2/harnesses/redwatch_reaper/sub/agent.json",
])
def test_only_a_real_harness_directory_is_a_candidate(path):
    assert fork_collect.candidate_dirs([path]) == []


def test_a_real_harness_directory_is_found():
    good = (f"{fork_collect.HARNESS_PREFIX}/redwatch_reaper/agent.json")
    assert fork_collect.candidate_dirs([good, "README.md"]) == [
        "redwatch_reaper"
    ]


@pytest.mark.parametrize("url,expect", [
    ("https://github.com/ada/soc.git", "ada/soc"),
    ("git@github.com:ada/soc.git", "ada/soc"),
    ("ssh://git@github.com/ada/soc", "ada/soc"),
    ("https://github.com/ada/soc", "ada/soc"),
])
def test_a_remote_is_read_however_it_is_spelled(url, expect):
    assert fork_collect.upstream_of(url) == expect
