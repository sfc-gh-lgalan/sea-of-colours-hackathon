"""The remote is yours, and still not a fork (v1.47).

The near-miss that `soc push`'s ownership check cannot see. Making a
fresh empty repo and pointing a remote at it is what most people mean
when they say "I forked it", and locally there is nothing to tell them
apart: full history, their own copy, push works, ownership check happy.

What is missing is GitHub's parent link, and exactly one thing needs it.
`soc collect` finds the field with ``gh api repos/X/forks``, which lists
forks and nothing else — so this attendee works all day, publishes
successfully, and is simply absent from the league with no error raised
anywhere. It happened to a real entrant, which is why these exist.

`gh` is stubbed with a script on PATH rather than by patching
``subprocess``: the argument list is part of what can break, and a mock
that accepts anything would keep passing after the query stopped asking
the right question.
"""
from __future__ import annotations

import os
import pathlib
import stat
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts import soc  # noqa: E402


def _fake_gh(tmp_path: pathlib.Path, *, login: str, fork: str | None,
             fails: bool = False) -> pathlib.Path:
    """A `gh` that answers the two queries the checks actually make.

    ``fork`` of None means the repo lookup fails, which is what a repo
    that does not exist (or an unauthenticated `gh`) looks like.
    """
    binv = tmp_path / "bin"
    binv.mkdir(exist_ok=True)
    script = binv / "gh"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"fails = {fails!r}\n"
        f"login = {login!r}\n"
        f"fork = {fork!r}\n"
        "a = sys.argv[1:]\n"
        "if fails:\n"
        "    sys.exit(1)\n"
        "if a[:2] == ['api', 'user']:\n"
        "    print(login); sys.exit(0)\n"
        "if a[0] == 'api' and a[1].startswith('repos/'):\n"
        "    if fork is None:\n"
        "        sys.exit(1)\n"
        "    print(fork); sys.exit(0)\n"
        "sys.exit(1)\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return binv


@pytest.fixture
def gh(tmp_path, monkeypatch):
    def install(*, login="ada", fork="true", fails=False):
        binv = _fake_gh(tmp_path, login=login, fork=fork, fails=fails)
        monkeypatch.setenv("PATH", f"{binv}{os.pathsep}{os.environ['PATH']}")
    return install


@pytest.fixture
def origin(monkeypatch):
    """Point soc's git helper at a chosen set of remotes.

    ``upstream`` defaults to present because that is the attendee this
    check is about; the organiser's own clone (no upstream) is its own
    test, since it used to be a false positive.
    """
    def install(url: str, upstream: str | None = "https://github.com/org/soc.git"):
        remotes = {"origin": url, "upstream": upstream or ""}

        def fake(*a):
            if a[:2] == ("remote", "get-url"):
                return remotes.get(a[2], "")
            return "main"

        monkeypatch.setattr(soc, "_git", fake)
    return install


def test_a_real_fork_is_not_flagged(gh, origin):
    gh(login="ada", fork="true")
    origin("https://github.com/ada/sea-of-colours-hackathon.git")
    assert soc._not_a_real_fork("origin") is None


def test_your_own_unlinked_repo_is_flagged(gh, origin):
    """The whole point: it is yours, it works, and it is not a fork."""
    gh(login="ada", fork="false")
    origin("https://github.com/ada/sea-of-colours-hackathon.git")
    assert soc._not_a_real_fork("origin") == "ada/sea-of-colours-hackathon"


def test_a_renamed_fork_is_still_a_fork(gh, origin):
    """Forks get renamed and that is fine — the parent link survives it,
    and one real entrant is already running under a renamed fork."""
    gh(login="ada", fork="true")
    origin("https://github.com/ada/ada-sea-of-colours.git")
    assert soc._not_a_real_fork("origin") is None


def test_someone_elses_repo_is_left_to_the_other_check(gh, origin):
    """Two messages for one mistake is worse than one. Cloning upstream
    has its own error with its own fix, and this must not also fire."""
    gh(login="ada", fork="false")
    origin("https://github.com/sfc-gh-lgalan/sea-of-colours-hackathon.git")
    assert soc._not_a_real_fork("origin") is None
    assert soc._pushing_at_someone_elses_repo("origin") == (
        "sfc-gh-lgalan/sea-of-colours-hackathon"
    )


def test_no_gh_means_no_opinion(gh, origin):
    """Best-effort by design. Plenty of attendees will not have `gh`
    authenticated, and a check that cannot run must never block a push
    — being unable to verify is not evidence of a problem."""
    gh(login="ada", fork="true", fails=True)
    origin("https://github.com/ada/sea-of-colours-hackathon.git")
    assert soc._not_a_real_fork("origin") is None


def test_an_unreadable_repo_is_not_an_accusation(gh, origin):
    """A private or missing repo answers nothing, and silence must not
    be reported as 'not a fork'."""
    gh(login="ada", fork=None)
    origin("https://github.com/ada/sea-of-colours-hackathon.git")
    assert soc._not_a_real_fork("origin") is None


def test_a_remote_that_is_not_a_github_url_is_ignored(gh, origin):
    gh(login="ada", fork="false")
    origin("/srv/local/bare.git")
    assert soc._not_a_real_fork("origin") is None


def test_no_such_remote(gh, monkeypatch):
    gh(login="ada", fork="false")
    monkeypatch.setattr(soc, "_git", lambda *a: "")
    assert soc._not_a_real_fork("origin") is None


def test_the_event_repo_does_not_warn_about_itself(gh, origin):
    """The organiser's own clone is not a fork either, and used to be
    told so on every `soc doctor` — an alarm that fires on the person
    least able to act on it teaches everyone to ignore alarms.

    No `upstream` remote is what separates them: the event repo has
    nothing above it, which is what makes it the event repo.
    """
    gh(login="lgalan", fork="false")
    origin("https://github.com/lgalan/sea-of-colours-hackathon.git",
           upstream=None)
    assert soc._not_a_real_fork("origin") is None
