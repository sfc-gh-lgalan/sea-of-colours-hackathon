"""Every season of a match must be played in its own process.

This exists because of one bug, in the first best-of-seven final ever
played. ``play_match_wide`` runs the seasons a match is *certain* to
play at once and the rest in turn, and the "in turn" half used to call
the season straight from the parent process.

That looked harmless and was not. The store backend resolves at **import
time**, and the parent has already imported most of the tree to work out
the fixtures — so the env var the worker path sets had no effect there
and the backend fell back to ``memory``. Games four and five of the
final came back with scores, got written into the match file, and their
sessions evaporated with the process. The match read as played, 4-1, and
two of its five seasons had no replay, no cards and no stats behind
them.

The module docstring already warned about exactly this ("a season
evaporates when its worker exits, so every replay link on the site would
404") and the runner already refuses a non-durable backend. Neither
helped, because neither was in the path the parent took.

So the rule is structural, not a matter of remembering: a season is only
ever played inside a pool, and these tests fail if that stops being
true.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from sea_of_colours.league import fixtures as fx
from sea_of_colours.league import runner


def test_only_the_worker_entry_point_plays_a_season():
    """``_play_game`` must be called from ``_season_job`` and nowhere else.

    A scan rather than a mock, because the failure is about *which
    process* the call happens in — something no amount of asserting on
    a return value can see.
    """
    tree = ast.parse(Path(inspect.getfile(runner)).read_text())
    callers = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_play_game"):
                callers.add(fn.name)

    assert callers, "_play_game is never called — has it been renamed?"
    assert callers == {"_season_job", "play_match"}, (
        "a season is being played outside the worker entry point "
        f"(callers: {sorted(callers)}). It will resolve the backend to "
        "memory and the session will not survive the process."
    )


def test_the_wide_path_never_plays_a_season_itself():
    """``play_match_wide`` must go through the pool for every season."""
    src = inspect.getsource(runner.play_match_wide)
    assert "_play_game" not in src, (
        "play_match_wide calls _play_game directly. That runs the season "
        "in the parent, where the backend has already resolved — the "
        "bug that cost two seasons of the first final their replays."
    )
    assert "_play_seasons" in src


def test_the_pool_helper_is_used_even_for_one_season():
    """The single-season case must not be special-cased back into the parent.

    It is the tempting optimisation ("why spawn for one?") and it is
    the exact shape of the original bug.
    """
    src = inspect.getsource(runner._play_seasons)
    assert "ProcessPoolExecutor" in src
    # No early return that hands a lone season back to the caller to run.
    assert "max(1, min(workers, len(payloads)))" in src


def test_a_wide_match_covers_every_season_it_might_need():
    """The two halves must partition the match, with no season missed.

    The heat takes the certain ones and the serial tail takes the rest;
    an off-by-one either replays a season or skips one, and skipping is
    silent.
    """
    for stage in ("playin", "r16", "qf", "sf", "final"):
        bo = fx.best_of(stage)
        heat = list(range(1, fx.unconditional_games(stage) + 1))
        tail = list(range(len(heat) + 1, bo + 1))
        assert heat + tail == list(range(1, bo + 1))
        assert not set(heat) & set(tail)


def test_the_runner_still_refuses_a_backend_that_cannot_be_replayed():
    assert "memory" not in runner.DURABLE
    for name in ("file", "snowflake"):
        assert name in runner.DURABLE


def test_a_partly_played_match_can_be_resumed_not_just_restarted(tmp_path):
    """``include_partial`` must offer back a match with seasons still to play.

    A match file exists from its first season onward, so "played" at
    match granularity is true the moment one season lands. Without this
    a best-of-seven stopped at three can only be finished by deleting
    its file and replaying three good seasons.
    """
    from dataclasses import asdict
    from sea_of_colours.league.fixtures import GameResult, MatchResult

    field = ["A", "B"]                       # smallest bracket: one final
    m = fx.next_round(field, [])[0]
    assert m.stage == "final"

    # Three of seven played, nobody on four.
    part = MatchResult(id=m.id, stage=m.stage, a=m.a, b=m.b, games=[
        GameResult(n=n, session_id=f"s{n}", seed=n, p1=m.a, p2=m.b,
                   scores={"p1": 10 if n != 2 else 1,
                           "p2": 1 if n != 2 else 10})
        for n in (1, 2, 3)
    ])
    assert not part.complete
    runner.write(part, tmp_path)

    strict, _ = runner.plan(field, root=tmp_path)
    assert strict == [], "the default must still treat the file as played"

    resumable, stage = runner.plan(field, root=tmp_path, include_partial=True)
    assert stage == "final"
    assert [x.id for x in resumable] == [m.id]

    # And once it is actually finished, it stops being offered. A is on
    # two wins from the three above (B took game two), so it takes two
    # more to reach the four a final needs — not one.
    assert part.wins(m.a) == 2 and part.wins(m.b) == 1
    for n in (4, 5):
        part.games.append(GameResult(n=n, session_id=f"s{n}", seed=n,
                                     p1=m.a, p2=m.b,
                                     scores={"p1": 10, "p2": 1}))
    assert part.wins(m.a) == 4
    assert part.complete and part.winner == m.a
    runner.write(part, tmp_path)
    again, _ = runner.plan(field, root=tmp_path, include_partial=True)
    assert again == [], "a complete match must not be offered again"
    assert asdict(part.games[0])["n"] == 1
