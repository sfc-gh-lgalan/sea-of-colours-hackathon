"""Assembling what the ``/league/`` page draws.

The page is static and dumb on purpose. Everything it shows is computed
here and handed over as one blob, so the league can be rebuilt from the
match files at any time and the page never has to know how a Swiss
round is paired or what beats what.

Registered as ``window.SOC_LEAGUE`` in a ``<script>`` rather than
fetched, which is the same trick ``battles/room.html`` uses: a page that
registers its data works when you double-click the file, and one that
fetches it does not. That matters more than it sounds like it should —
the organiser wanting to show somebody the standings is often the
person whose server is mid-restart.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from sea_of_colours.league import ROOT, fixtures, roster as roster_mod, runner, stats

#: The generated blob. Lives with the other league output, served at
#: ``/league/league.js``.
DATA_PATH = ROOT / "league.js"

#: Matches played *beside* the bracket rather than in it — a late entry
#: contesting a qualifier's slot after the draw is already under way.
#:
#: They live in their own directory for one reason: ``fixtures`` derives
#: the entire bracket from the roster plus whatever results are on disk,
#: so a match filed among the bracket's own would either be ignored or
#: shift the slots under a round that is still being played. Kept apart,
#: a late entry cannot perturb a single tie; the cost is that its winner
#: is seeded into the next round by hand.
CHALLENGER_DIR = ROOT / "challenger"

#: Challengers that have been *commissioned* but have no result yet.
#:
#: Deliberately not a stub file inside :data:`CHALLENGER_DIR`: the runner
#: treats any match file there as already played and would skip the match
#: on a resume, so an empty placeholder is the one shape that can silently
#: cancel the very match it was meant to advertise. A declaration outside
#: the glob cannot do that. Entries are dropped automatically once a real
#: result lands, so nothing has to clean it up.
PENDING_PATH = ROOT / "challenger_pending.json"

#: How a season is linked. Relative so the page works behind a tunnel or
#: on whatever port the host happened to use.
REPLAY_URL = "/play?session={session_id}"


def build(
    *,
    backend: str = "file",
    store_dir: Optional[str] = None,
    root: Path | str = runner.MATCH_DIR,
    roster_path: Path | str = roster_mod.ROSTER_PATH,
    with_stats: bool = True,
) -> dict:
    """Everything the page needs, as one JSON-ready dict."""
    field = roster_mod.load(roster_path)
    played = runner.read_all(root)
    agents = field.labels

    store = _store(backend, store_dir) if with_stats else None
    seat_rows: list[dict] = []

    matches = _rows(played, store, seat_rows)

    # Late entries, read from their own directory. Same shape as a bracket
    # match so the page can draw one with the same code, but kept out of
    # `matches` so the figures and the health banner keep describing the
    # cup rather than the cup plus whatever was played alongside it.
    late_rows: list[dict] = []
    challengers = _rows(runner.read_all(CHALLENGER_DIR), store, late_rows)
    challengers += _pending(PENDING_PATH, {m["id"] for m in challengers})

    table = fixtures.progress(agents, played)
    all_rounds = fixtures.rounds(agents, played)

    return {
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "format": {
            "best_of": fixtures.BEST_OF,
            # Per stage, because the final is longer. The page reads
            # this rather than saying "three seasons, first to two" in
            # prose, which would be wrong on exactly one match — the
            # only one most people will read.
            "best_of_by_stage": {
                s: fixtures.best_of(s) for s in fixtures.STAGE_LABEL},
            "wins_needed_by_stage": {
                s: fixtures.wins_needed(s) for s in fixtures.STAGE_LABEL},
            "shape": "knockout",
            "bracket_size": fixtures.bracket_size(len(agents)),
            "play_ins": len(fixtures.play_in_matches(agents)),
            "stage_labels": dict(fixtures.STAGE_LABEL),
            # The page renders links and stat names from these rather
            # than hardcoding them, so adding a weapon or renaming a
            # combat stat does not need a second edit in the HTML.
            "replay_url": REPLAY_URL.replace("{session_id}", ""),
            "kill_labels": {k: label for k, label in stats.KILL_STATS},
            "weapon_kinds": list(stats.weapon_kinds()),
        },
        "champion": fixtures.champion(agents, played),
        "roster": {
            "cutoff": field.cutoff,
            "note": field.note,
            "entrants": [
                {
                    "label": e.label,
                    "team": e.team,
                    "participants": list(e.participants),
                    "fork": e.fork,
                    "first_commit": e.first_commit,
                }
                for e in field.entrants
            ],
            "bench": [{"label": b.label, "reason": b.reason} for b in field.bench],
        },
        # Not a league table — a knockout has none. This is how far
        # each agent got and what its seasons looked like getting there.
        "progress": [
            {
                "agent": p.agent,
                "seed": p.seed,
                "played": p.played,
                "won": p.won,
                "drawn": p.drawn,
                "lost": p.lost,
                "season_wins": p.season_wins,
                "season_losses": p.season_losses,
                "season_draws": p.season_draws,
                "margin": p.margin,
                "fallback_turns": p.fallback_turns,
                "reached": p.reached,
                "out_at": p.out_at,
                "beaten_by": p.beaten_by,
            }
            for p in table
        ],
        "matches": matches,
        "challengers": challengers,
        "bracket": _bracket_rounds(all_rounds, played, agents),
        # Weapon careers span both, and the label list is widened to take
        # in anyone who only ever appeared in a challenger — otherwise a
        # late entry plays a full match and shows nothing.
        "weapons": _weapons(
            seat_rows + late_rows,
            agents + [a for a in _labels(challengers) if a not in agents],
        ),
        "health": _health(matches),
    }


def _labels(rows: list[dict]) -> list[str]:
    """Every agent named in these matches, in first-seen order."""
    out: list[str] = []
    for m in rows:
        for who in (m.get("a"), m.get("b")):
            if who and who not in out:
                out.append(who)
    return out


#: The shareable page, and the sentinels bounding its generated block.
#: Source file, not output — it is hand-written except for the one
#: block, which is why this rewrites in place rather than emitting it.
ANNOUNCE_PATH = Path("league/announce.html")
_MARK_OPEN = "// <<<SOC_RESULTS"
_MARK_CLOSE = "// >>>SOC_RESULTS"

#: How a stage reads in a sentence rather than as a slug.
_STAGE_WORDS = {
    "playin": "the play-in",
    "r16": "the round of 16",
    "qf": "the quarter-finals",
    "sf": "the semi-finals",
    "final": "the final",
}


def write_announce(data: dict, path: Path | str = ANNOUNCE_PATH) -> bool:
    """Bake the state of the competition into the shareable page.

    Rewrites only the sentinel-delimited block, leaving the rest of the
    file — which is prose, and hand-written — alone. Refuses rather than
    guesses if the sentinels are missing or out of order, because the
    alternative is silently truncating a page somebody is about to share.

    Returns True if the file changed.
    """
    p = Path(path)
    if not p.is_file():
        return False
    text = p.read_text()
    i, j = text.find(_MARK_OPEN), text.find(_MARK_CLOSE)
    if i < 0 or j < 0 or j < i:
        return False

    rounds = []
    for r in data.get("bracket") or []:
        ties = [{
            "a": t.get("a") or "",
            "b": t.get("b") or "",
            "seed_a": t.get("seed_a") or "",
            "seed_b": t.get("seed_b") or "",
            "score": t.get("scoreline") or "",
            "winner": t.get("winner") or "",
            "games": t.get("games") or 0,
        } for t in (r.get("ties") or [])]
        n = len(ties)
        # Spelled out on the first column only, a bare count after.
        # The bracket columns are ~170px and the headers do not wrap,
        # so "4 matches" on every one of them runs the labels into each
        # other — which is how the hand-written version had it too.
        rounds.append({
            "h": r.get("label") or "",
            "i": (f"{n} match{'es' if n != 1 else ''}" if not rounds else str(n)),
            "ties": ties,
        })

    labels = (data.get("format") or {}).get("stage_labels") or {}
    fate = {}
    for row in data.get("progress") or []:
        if not row.get("played"):
            continue
        if row["agent"] == data.get("champion"):
            fate[row["agent"]] = "champion"
        elif row.get("out_at"):
            fate[row["agent"]] = f"out &middot; {labels.get(row['out_at'], row['out_at'])}"
        else:
            fate[row["agent"]] = "still in"

    played = sum(1 for m in data.get("matches") or [] if m.get("complete"))
    total = ((data.get("format") or {}).get("bracket_size") or 0) - 1 \
        + ((data.get("format") or {}).get("play_ins") or 0)

    block = {
        "snapshot": _snapshot(data, played, total),
        "played": played,
        "total": total,
        "champion": data.get("champion") or "",
        "rounds": rounds,
        "fate": fate,
    }

    body = (f"{_MARK_OPEN}\nconst RESULTS =\n"
            f"{json.dumps(block, indent=2)};\n")
    out = text[:i] + body + text[j:]
    if out == text:
        return False
    p.write_text(out)
    return True


def _snapshot(data: dict, played: int, total: int) -> str:
    """The staleness line: a time, and which round the reader is in."""
    when = datetime.now().strftime("%a %-d %b, %H:%M")
    if data.get("champion"):
        return f"{when} &middot; final result"
    if not played:
        return f"{when} &middot; before round one"
    # The next incomplete round is the one a reader is waiting on, and
    # is more use than the last finished one.
    for r in data.get("bracket") or []:
        ties = r.get("ties") or []
        if ties and not all(t.get("winner") for t in ties):
            stage = (r.get("label") or "").lower()
            return f"{when} &middot; {played}/{total} played, at {stage}"
    return f"{when} &middot; {played}/{total} played"


def _pending(path: Path | str, have: set) -> list[dict]:
    """Commissioned challengers with no result on disk yet.

    Shaped exactly like a played match so the page draws it with the same
    code — just empty, which every reader already treats as "not played".
    """
    p = Path(path)
    if not p.is_file():
        return []
    try:
        rows = json.loads(p.read_text()) or []
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for r in rows:
        mid = str(r.get("id") or "")
        if not mid or mid in have:
            continue                      # a real result supersedes it
        out.append({
            "id": mid,
            "stage": str(r.get("stage") or "challenger"),
            "a": str(r.get("a") or ""),
            "b": str(r.get("b") or ""),
            "bye": False,
            "winner": "", "scoreline": "", "margin": 0,
            "decided_by": "unplayed", "complete": False,
            "fallback_turns": 0, "games": [],
            "for_slot": str(r.get("for_slot") or ""),
            "note": str(r.get("note") or ""),
        })
    return out


def _rows(played: list, store, seat_rows: list[dict]) -> list[dict]:
    """Matches as the page wants them, newest stage last.

    Appends every seat's stats to ``seat_rows`` as a side effect, which is
    what :func:`_weapons` later rolls up into per-agent careers.
    """
    out: list[dict] = []
    for r in sorted(played, key=lambda m: (m.stage, m.id)):
        games: list[dict] = []
        for g in r.games:
            row = {
                "n": g.n,
                "seed": g.seed,
                "p1": g.p1,
                "p2": g.p2,
                "scores": g.scores,
                "score_a": g.score_of(r.a),
                "score_b": g.score_of(r.b),
                "winner": g.winner,
                "seconds": g.seconds,
                "fallback_turns": g.fallback_turns,
                "aborted": g.aborted,
                "error": g.error,
                "session_id": g.session_id,
                "replay": REPLAY_URL.format(session_id=g.session_id)
                if g.session_id else "",
            }
            if store is not None and g.session_id:
                seats = {"p1": g.p1, "p2": g.p2}
                per_seat = stats.for_session(store, g.session_id, seats)
                row["stats"] = {s: v.to_json() for s, v in per_seat.items()}
                seat_rows.extend(v.to_json() for v in per_seat.values())
            games.append(row)

        out.append({
            "id": r.id,
            "stage": r.stage,
            "a": r.a,
            "b": r.b,
            "bye": r.bye,
            "winner": r.winner,
            "scoreline": r.scoreline,
            "margin": r.margin,
            "decided_by": r.decided_by,
            "complete": r.complete,
            "fallback_turns": r.fallback_turns,
            "games": games,
        })
    return out


def _store(backend: str, store_dir: Optional[str]):
    """A store to read finished games from, or None if unreachable."""
    import os

    os.environ["SOC_BACKEND"] = backend
    if store_dir:
        os.environ["SOC_STORE_DIR"] = store_dir
    try:
        from sea_of_colours.snowpark import backend as soc_backend
        return soc_backend.get_store()
    except Exception:
        return None


def _bracket_rounds(all_rounds: list, played: list, agents: list) -> list[dict]:
    """The bracket, round by round, the way it is drawn.

    Every tie is listed whether or not it has been played, and a round
    that cannot be derived yet is simply absent — ``fixtures.rounds``
    stops walking at the first unfinished round rather than predicting
    the next one. A page that showed a predicted pairing would be read
    as a fixture, and somebody would turn up to watch it.

    The first round also carries its seeds, since that is the only
    place the draw is explained.
    """
    results = {r.id: r for r in played}
    seeds = {a: i + 1 for i, a in enumerate(agents)}
    out: list[dict] = []

    for rnd in all_rounds:
        stage = rnd[0].stage if rnd else ""
        ties: list[dict] = []
        for m in rnd:
            r = results.get(m.id)
            ties.append({
                "id": m.id,
                "a": m.a, "b": m.b,
                "seed_a": seeds.get(m.a, 0), "seed_b": seeds.get(m.b, 0),
                "winner": r.winner if r else "",
                "scoreline": r.scoreline if r else "",
                "decided_by": r.decided_by if r else "unplayed",
                "margin": r.margin if r else 0,
                "complete": bool(r and r.complete),
                "games": len(r.games) if r else 0,
            })
        out.append({
            "stage": stage,
            "label": fixtures.STAGE_LABEL.get(stage, stage),
            "ties": ties,
            "complete": all(t["complete"] for t in ties),
        })
    return out


def _weapons(seat_rows: list[dict], agents: list[str]) -> list[dict]:
    """Per-agent weapon and kill careers, busiest first.

    Sorted by what an agent *did* rather than what it owns, because the
    interesting entrant is the one that fired, not the one that banked
    the most blue.
    """
    out = [stats.merge(seat_rows, a) for a in agents]
    out = [row for row in out if row["games"]]
    out.sort(key=lambda r: (-r["fired_total"], -r["kills_total"], r["agent"]))
    return out


def _health(matches: list[dict]) -> dict:
    """Whether these results mean anything.

    A season where every model call failed still produces a score and a
    winner — the harness falls back to the built-in heuristic, which
    plays the night perfectly well. That is the single most dangerous
    failure this page can have, because nothing about a fallen-back
    league *looks* wrong. So the count goes on the page, loudly.
    """
    games = [g for m in matches for g in m["games"]]
    fell_back = sum(1 for g in games if g.get("fallback_turns"))
    return {
        "games": len(games),
        "matches": len(matches),
        "games_with_fallback": fell_back,
        "fallback_turns": sum(int(g.get("fallback_turns") or 0) for g in games),
        "errored": sum(1 for g in games if g.get("error") or g.get("aborted")),
    }


def write(data: dict, path: Path | str = DATA_PATH) -> Path:
    """Emit the blob as a registering script, plus a plain JSON twin.

    The ``.js`` is what the page loads; the ``.json`` beside it is for
    anything else that wants the league — a spreadsheet, a slide, an
    agent asked to summarise the day.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(data, indent=2)
    path.write_text(f"window.SOC_LEAGUE = {body};\n")
    path.with_suffix(".json").write_text(body + "\n")
    return path
