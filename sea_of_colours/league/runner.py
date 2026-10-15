"""Playing the fixtures — several matches at once, resumably.

Three decisions, each learned from something that goes wrong when you
do the obvious thing instead.

**A job is a match, not a season.** The best-of-three rule is
sequential by nature: whether a decider gets played depends on the first
two. Handing seasons to the pool would mean the pool deciding when to
stop, which puts the competition rule inside the concurrency code. A
match per worker keeps the rule in :mod:`fixtures` and still leaves
plenty to parallelise, since matches are independent by construction.

**Spawn, not fork.** Copied from ``scripts/run_matchup_v12.py``, which
uses spawn because Snowpark connections are not fork-safe — a forked
child inherits a connection it does not own and the failures are
intermittent and awful to diagnose.

**One file per match, not one shared log.** Several processes appending
to a results file is a corruption waiting for a match card long enough
to cross the atomic-write boundary. A file per match is crash-safe,
makes resuming a directory listing, and lets the website read a league
that is still running.

The store must be durable — ``file`` or ``snowflake``. On ``memory`` a
season evaporates when its worker exits, so every replay link on the
site would 404. The runner refuses rather than discovering that later.
"""

from __future__ import annotations

import json
import os
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from multiprocessing import get_context
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

from sea_of_colours.league import ROOT, fixtures
from sea_of_colours.league.fixtures import Game, Match, MatchResult, GameResult

#: Where a played match is written. One JSON file, named for the match.
MATCH_DIR = ROOT / "matches"

#: Backends that survive the worker exiting. Anything else cannot be
#: replayed, which makes the match unreviewable and the site's links dead.
DURABLE = ("file", "snowflake", "multi")


def match_path(mid: str, root: Path | str = MATCH_DIR) -> Path:
    # Match ids carry agent labels, which are already constrained to
    # ``[a-z0-9_]`` by the manifest — but a stray separator would write
    # outside the directory, so the join is defensive rather than trusting.
    safe = mid.replace("/", "_").replace("..", "_")
    return Path(root) / f"{safe}.json"


def already_played(mid: str, root: Path | str = MATCH_DIR) -> bool:
    return match_path(mid, root).is_file()


# ── one match, inside a worker ─────────────────────────────────────


def _play_game(match: Match, game: Game, *, days: Optional[int], name: str) -> GameResult:
    """Run one season and reduce it to the match's vocabulary."""
    from sea_of_colours.evals import seasons

    out = GameResult(n=game.n, session_id="", seed=game.seed,
                     p1=game.p1, p2=game.p2)
    try:
        res = seasons.run_season(
            {"p1": game.p1, "p2": game.p2},
            seed=game.seed,
            days=days,
            season_name=name,
        )
    except Exception as exc:
        out.error = f"{type(exc).__name__}: {exc}"
        return out

    out.session_id = res.session_id
    out.scores = dict(res.scores)
    out.seconds = res.seconds
    out.fallback_turns = res.fallback_turns
    out.aborted = res.aborted
    return out


def play_match(
    match: Match,
    *,
    days: Optional[int] = None,
    root: Path | str = MATCH_DIR,
) -> MatchResult:
    """Play a match to a decision and write it down.

    Stops at 2-0 — which is not a shortcut but the *balanced* outcome,
    since games one and two are played from opposite sides. A decider
    only happens when the first two did not settle it.
    """
    result = MatchResult(id=match.id, stage=match.stage, a=match.a,
                         b=match.b, bye=match.bye)
    if match.bye:
        write(result, root)
        return result

    for n in range(1, fixtures.best_of(match.stage) + 1):
        game = match.game(n)
        label = f"{match.stage} {match.a} v {match.b} g{n}"
        result.games.append(_play_game(match, game, days=days, name=label))
        # Written after every season, not just at the end. A match takes
        # a quarter of an hour, and writing once meant the site showed
        # nothing at all until it finished — so a round in progress looked
        # identical to a round nobody had started. The write is atomic
        # (temp file, rename) and readers already handle an incomplete
        # match, so a partial file costs nothing and buys a live page.
        write(result, root)
        if not fixtures.needs_decider(result):
            break

    return result


def _apply_backend(payload: dict) -> None:
    """Point this process at the chosen store, before any engine import.

    The backend resolves at import time and an explicit value is
    strict, which is what we want — a worker that cannot reach the
    chosen store must fail, not quietly write a season into memory
    where nobody will ever find it.
    """
    os.environ["SOC_BACKEND"] = payload["backend"]
    if payload.get("store_dir"):
        os.environ["SOC_STORE_DIR"] = payload["store_dir"]
    # Cards are written by the site from the store; a worker writing its
    # own season record as well would triple the disk for no reader.
    os.environ.setdefault("SOC_QUIET_DEPRECATED", "1")


def _season_job(payload: dict) -> dict:
    """Pool entry point for ONE season. Picklable, so top-level.

    Used only for the seasons a match is certain to play — see
    :func:`fixtures.unconditional_games`. The worker returns the season
    and nothing else: the parent merges and writes, because several
    workers writing one match file is the corruption this module's
    one-file-per-match rule exists to avoid.
    """
    _apply_backend(payload)
    match = Match(id=payload["id"], stage=payload["stage"], a=payload["a"],
                  b=payload["b"], bye=False)
    n = int(payload["n"])
    game = match.game(n)
    label = f"{match.stage} {match.a} v {match.b} g{n}"
    return asdict(_play_game(match, game, days=payload.get("days"), name=label))


def _play_seasons(
    payloads: Sequence[dict],
    *,
    workers: int,
    each: Optional[Callable[[int, GameResult], None]] = None,
) -> dict[int, GameResult]:
    """Play the given seasons in fresh processes, keyed by season number.

    Always a pool, even for a single season, and that is the point
    rather than an oversight. The backend resolves at *import* time, so
    only a process that has not yet imported the engine can be pointed
    at a store — which the parent, having already imported half the
    tree to work out the fixtures, cannot be. Playing a season in the
    parent instead silently resolved to ``memory``: the scores came
    back and were written down, and the session evaporated with the
    process, so the match looked played and had no replay behind it.
    A spawn costs a second against a season's five minutes.
    """
    out: dict[int, GameResult] = {}
    if not payloads:
        return out
    with ProcessPoolExecutor(
        max_workers=max(1, min(workers, len(payloads))),
        mp_context=get_context("spawn"),
    ) as pool:
        futures = {pool.submit(_season_job, p): int(p["n"]) for p in payloads}
        for fut in as_completed(futures):
            n = futures[fut]
            try:
                out[n] = GameResult(**fut.result())
            except Exception as exc:
                out[n] = GameResult(n=n, session_id="", seed=0, p1="", p2="",
                                    error=f"worker died: {exc}")
            if each is not None:
                each(n, out[n])       # as it lands, not once all have
    return out


def play_match_wide(
    match: Match,
    *,
    days: Optional[int] = None,
    root: Path | str = MATCH_DIR,
    jobs: int = 3,
    backend: str = "file",
    store_dir: Optional[str] = None,
    on_game: Optional[Callable[[GameResult], None]] = None,
) -> MatchResult:
    """Play one match, its certain seasons at once and the rest in turn.

    For a single long match — the final — where "a job is a match"
    leaves the whole machine idle behind one season at a time. The
    first :func:`fixtures.unconditional_games` seasons go out together
    because no result among them can stop another being played; after
    that it is strictly one at a time, since whether game five happens
    depends on game four.

    Resumable at season granularity: seasons already in the match file
    are kept and not replayed.
    """
    bo = fixtures.best_of(match.stage)
    result = MatchResult(id=match.id, stage=match.stage, a=match.a,
                         b=match.b, bye=match.bye)
    if match.bye:
        write(result, root)
        return result

    # Resume: keep what is already on disk, in season order.
    existing = {g.n: g for r in read_all(root) if r.id == match.id
                for g in r.games if not (g.error or g.aborted)}
    result.games = [existing[n] for n in sorted(existing)]

    base = {"id": match.id, "stage": match.stage, "a": match.a,
            "b": match.b, "days": days, "backend": backend,
            "store_dir": store_dir}

    def record(games: dict[int, GameResult]) -> None:
        """Merge seasons in and write. Written as each lands, not at the
        end: a final takes half an hour, and writing once meant the site
        showed a round in progress exactly as it showed one nobody had
        started."""
        existing.update(games)
        result.games = [existing[k] for k in sorted(existing)]
        write(result, root)
        for n in sorted(games):
            if on_game is not None:
                on_game(games[n])

    heat = [n for n in range(1, fixtures.unconditional_games(match.stage) + 1)
            if n not in existing]
    if heat:
        _play_seasons([dict(base, n=n) for n in heat], workers=jobs,
                      each=lambda n, g: record({n: g}))

    # The rest, strictly in turn — each one's existence depends on the
    # last, so there is nothing here to overlap.
    for n in range(1, bo + 1):
        if n in existing:
            continue
        if not fixtures.needs_decider(result):
            break
        record(_play_seasons([dict(base, n=n)], workers=1))

    return result


def _job(payload: dict) -> dict:
    """Pool entry point for a whole match. Dict-shaped so spawn can pickle it."""
    _apply_backend(payload)
    match = Match(id=payload["id"], stage=payload["stage"], a=payload["a"],
                  b=payload["b"], bye=payload["bye"])
    started = time.time()
    try:
        result = play_match(match, days=payload.get("days"),
                            root=payload["root"])
        return {
            "id": match.id, "ok": True, "winner": result.winner,
            "scoreline": result.scoreline, "decided_by": result.decided_by,
            "games": len(result.games), "seconds": round(time.time() - started, 1),
            "fallback_turns": result.fallback_turns,
        }
    except Exception as exc:
        return {
            "id": match.id, "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc(limit=6),
            "seconds": round(time.time() - started, 1),
        }


# ── driving the pool ───────────────────────────────────────────────


def run(
    matches: Sequence[Match],
    *,
    jobs: int = 4,
    days: Optional[int] = None,
    backend: str = "file",
    store_dir: Optional[str] = None,
    root: Path | str = MATCH_DIR,
    resume: bool = True,
    on_done: Optional[Callable[[dict], None]] = None,
) -> list[dict]:
    """Play a round. Returns one summary dict per match attempted.

    ``jobs`` is a concurrency ceiling on *matches*, so an LLM field puts
    ``2 x jobs`` seats against the model at once. There is no rate
    limiter anywhere in the Cortex path (``orchestrator_2/cortex_chat.py``
    posts straight out), so this number is the only thing standing
    between a league and an account-wide 429.
    """
    if backend not in DURABLE:
        raise ValueError(
            f"backend {backend!r} does not persist — a league on it would "
            f"produce dead replay links. Use one of {', '.join(DURABLE)}."
        )
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    todo = [m for m in matches if not (resume and already_played(m.id, root))]
    if not todo:
        return []

    payloads = [
        {
            "id": m.id, "stage": m.stage, "a": m.a, "b": m.b, "bye": m.bye,
            "days": days, "backend": backend, "store_dir": store_dir,
            "root": str(root),
        }
        for m in todo
    ]

    done: list[dict] = []
    # max_workers is clamped to the work available so a small round does
    # not pay to spawn interpreters it will never use — spawn is not
    # cheap, and a one-match round is the common case when resuming.
    with ProcessPoolExecutor(
        max_workers=max(1, min(jobs, len(payloads))),
        mp_context=get_context("spawn"),
    ) as pool:
        futures = {pool.submit(_job, p): p["id"] for p in payloads}
        for fut in as_completed(futures):
            try:
                summary = fut.result()
            except Exception as exc:  # a worker died outright
                summary = {"id": futures[fut], "ok": False,
                           "error": f"worker died: {exc}"}
            done.append(summary)
            if on_done is not None:
                on_done(summary)
    return done


# ── reading matches back ───────────────────────────────────────────


def write(result: MatchResult, root: Path | str = MATCH_DIR) -> Path:
    """Write one match, atomically.

    Via a temp file and a rename because the website reads this
    directory while the league is still playing, and a half-written
    match would render as a broken card rather than an absent one.
    """
    path = match_path(result.id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": result.id, "stage": result.stage, "a": result.a, "b": result.b,
        "bye": result.bye,
        "games": [asdict(g) for g in result.games],
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(path)
    return path


def read_all(root: Path | str = MATCH_DIR) -> list[MatchResult]:
    """Every match played so far, in whatever order the disk gives."""
    root = Path(root)
    if not root.is_dir():
        return []
    out: list[MatchResult] = []
    for path in sorted(root.glob("*.json")):
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        out.append(MatchResult(
            id=str(raw.get("id") or path.stem),
            stage=str(raw.get("stage") or ""),
            a=str(raw.get("a") or ""),
            b=str(raw.get("b") or ""),
            bye=bool(raw.get("bye")),
            games=[GameResult(**g) for g in raw.get("games") or []],
        ))
    return out


def read_stage(stage: str, root: Path | str = MATCH_DIR) -> list[MatchResult]:
    return [r for r in read_all(root) if r.stage == stage]


def plan(
    agents: Sequence[str],
    *,
    root: Path | str = MATCH_DIR,
    include_partial: bool = False,
) -> tuple[list[Match], str]:
    """The next matches to play, and what stage the league is at.

    Called repeatedly, and does no bookkeeping of its own: ``fixtures``
    derives the whole bracket from the seed order plus whatever results
    are on disk, so a crashed round resumes into exactly the fixtures
    it was halfway through and a round that cannot exist yet simply
    is not offered.

    Returns ``([], "done")`` once the final is in, and ``([], stage)``
    when a stage is recorded but unfinished — a worker died partway,
    and nothing new should go out until those files are replayed or
    removed.

    ``include_partial`` relaxes that last rule for callers that can
    resume a match rather than restart it. A match file exists from the
    first season onward, so "played" at match granularity is too coarse
    for :func:`play_match_wide`, which resumes at *season* granularity —
    without this, a final stopped after three seasons of seven can only
    be finished by deleting its file and replaying seasons that are
    already on disk and perfectly good.
    """
    played = read_all(root)
    pending = fixtures.next_round(agents, played)
    if pending:
        done = {r.id for r in played if r.complete} if include_partial else set()
        return ([m for m in pending
                 if (m.id not in done if include_partial
                     else not already_played(m.id, root))],
                pending[0].stage)

    if fixtures.champion(agents, played):
        return [], "done"

    # Nothing to hand out and no champion: either the bracket is stuck
    # on an unfinished match or on a genuine draw, which needs a human.
    walked = fixtures.rounds(agents, played)
    return [], (walked[-1][0].stage if walked else "empty")
