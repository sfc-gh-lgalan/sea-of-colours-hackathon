#!/usr/bin/env python3
"""``soc`` — the one command for the hackathon.

    python scripts/soc.py new --team redwatch --name reaper
    python scripts/soc.py lab
    python scripts/soc.py season --p1 redwatch_reaper --p2 tabula_v12
    python scripts/soc.py share            # hand it to a teammate
    python scripts/soc.py push
    python scripts/soc.py league

There is deliberately one entry point. The kit has a fair amount behind
it — a scenario suite, a fork minter, a submission path, a league — and
discovering four separate scripts is four chances to not find the third
one. ``soc`` with no arguments lists everything it can do.

**Written to be driven by an agent.** Most attendees will be improving
their agent through a coding assistant rather than by hand, so the
assistant is a first-class user of this CLI. That means: every error
names the fix, every subcommand explains what it is for in ``--help``,
and output is plain text an LLM can read back without parsing tricks.
``--json`` is there for when it wants structure instead.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# Every scenario runs in memory. Set before the engine is imported so
# backend auto-detection cannot wander off to a live account and start
# writing rows because somebody happened to have a config file.
os.environ.setdefault("SOC_BACKEND", "memory")


def _die(msg: str, *, fix: str = "") -> None:
    print(f"error: {msg}", file=sys.stderr)
    if fix:
        print(f"  fix: {fix}", file=sys.stderr)
    raise SystemExit(2)


#: Commands built on the battles suite. They still work — nothing is
#: removed — but the turn lab does the same job on real frozen turns
#: rather than hand-built ones, so a new fork should start there.
_SUPERSEDED = {
    "suite": "open a frozen turn in the lab and invoke your fork on it",
    "why": "the lab's divergence view diffs your take against V12's",
    "list": "the lab's launcher lists every frozen turn and every fork",
    # `diff` is NOT here — unlike these, it is gone rather than
    # deprecated (v1.43). See `cmd_diff_gone`.
    # v1.42 — `weapons` and `league` are deliberately NOT here. They are
    # the two reasons battles/ still exists, and the lab does not replace
    # either. `weapons` is a static scan of a fork's source: it answers
    # "could this agent ever fire?", which watching one turn cannot —
    # a fork that never puts the option on its menu looks identical to
    # one that considered the shot and declined. `league` ranks a field,
    # and the lab scores nothing on purpose.
}


def _superseded(cmd: str) -> None:
    """Say once, on stderr, that this command is on its way out.

    v1.42 — deliberately a notice and not a removal. The battles suite
    scores agents on ten constructed boards; the lab does it on turns
    that were actually played, which is both more honest and less to
    maintain. But `soc suite` is quoted in the attendee docs and in
    every fork's minting output, so breaking it mid-hackathon would
    strand people. It goes to stderr so `--json` output stays clean
    for anything parsing it.
    """
    if os.environ.get("SOC_QUIET_DEPRECATED"):
        return
    instead = _SUPERSEDED.get(cmd)
    print(f"note: `soc {cmd}` is superseded by the turn lab "
          f"(python run_web.py, then /lab).", file=sys.stderr)
    if instead:
        print(f"      instead: {instead}", file=sys.stderr)
    print("      it still works and nothing has been removed.",
          file=sys.stderr)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=_REPO, capture_output=True, text=True, check=False
    ).stdout.strip()


def _changed_paths() -> list[str]:
    """Every path git considers modified or untracked.

    Uses ``-z`` and does not strip: porcelain's status field is two
    columns wide and the first is often a space, so any leading-
    whitespace trim silently eats the first character of the first
    filename — which reads as a mysteriously missing file rather than
    as a parsing bug.
    """
    raw = subprocess.run(
        ["git", "status", "--porcelain", "-z", "--untracked-files=all"],
        cwd=_REPO, capture_output=True, text=True, check=False,
    ).stdout
    out: list[str] = []
    for entry in raw.split("\0"):
        if len(entry) > 3:
            out.append(entry[3:])
    return out


# ── new ─────────────────────────────────────────────────────────────


def cmd_new(args) -> int:
    """Mint your agent. Step one, and it touches nothing but your folder."""
    from scripts import new_agent

    argv = ["--team", args.team, "--name", args.name,
            "--participants", args.participants]
    if args.menu_label:
        argv += ["--menu-label", args.menu_label]
    return new_agent.main(argv)


# ── suite ───────────────────────────────────────────────────────────


def cmd_suite(args) -> int:
    """Score an agent against the redsign battles."""
    from sea_of_colours.evals.battles import boards, ladder, report, runner

    picked = _pick_boards(boards, args.board)
    rungs = (
        [ladder.get_rung(r) for r in args.rung.split(",")]
        if args.rung and args.rung != "all"
        else list(ladder.up_to(args.up_to))
    )
    loadouts = (
        [ladder.get_loadout(l) for l in args.loadout.split(",")]
        if args.loadout != "all"
        else list(ladder.LOADOUTS)
    )

    total = len(picked) * len(rungs) * len(loadouts) * args.runs
    print(
        f"running {len(picked)} board(s) x {len(rungs)} rung(s) x "
        f"{len(loadouts)} loadout(s) x {args.runs} run(s) = {total} turns "
        f"as {args.agent}",
        file=sys.stderr,
    )

    card_dir = Path(args.cards) if args.cards else None

    rec = None
    if args.record:
        from sea_of_colours.evals.battles import recorder as rec_mod

        rec = rec_mod.Recorder(
            agent=args.agent,
            root=args.record_dir or rec_mod.DEFAULT_ROOT,
        )

    def tick(res):
        mark = "ok  " if res.rate == 1.0 else ("~   " if res.flaky else "FAIL")
        print(f"  {mark} {res.battle_id:<46} {res.score:>4.0%}", file=sys.stderr)

    result = runner.run_suite(
        picked, rungs, loadouts,
        agent=args.agent, runs=args.runs, card_dir=card_dir,
        on_battle=None if args.json else tick,
        recorder=rec,
    )

    room = rec.finish() if rec is not None else None

    if args.json:
        print(json.dumps(_as_json(result), indent=2))
    else:
        print()
        print(report.render(result, verbose=args.verbose))
        if card_dir:
            print(f"  cards written to {card_dir}/")
        if room is not None:
            print(f"  recorded {len(rec.bake.turns)} turn(s) — open the room:")
            print(f"    open {room}")
            print("    (or http://127.0.0.1:8000/battles/ with the server up)")
            print("  every turn is replayable there, card and all.")
    return 0 if result.clean == len(result.battles) else 1


def _pick_boards(boards, spec: str):
    if not spec or spec == "all":
        return list(boards.BOARDS)
    out = []
    for name in spec.split(","):
        try:
            out.append(boards.get(name.strip()))
        except KeyError as exc:
            _die(str(exc))
    return out


def _as_json(result) -> dict:
    return {
        "agent": result.agent,
        "score": round(result.score, 4),
        "clean": result.clean,
        "battles": [
            {
                "id": b.battle_id,
                "board": b.board.id,
                "rung": b.rung.id,
                "loadout": b.loadout.id,
                "rate": round(b.rate, 3),
                "score": round(b.score, 3),
                "failures": [
                    {"check": n, "count": c} for n, c in b.recurring_failures()
                ],
            }
            for b in result.battles
        ],
        "by_rung": {k: {"clean": v[0], "total": v[1]}
                    for k, v in result.by_rung().items()},
        "weapons_fired": result.weapons_fired(),
    }


# ── why ─────────────────────────────────────────────────────────────


def cmd_why(args) -> int:
    """Explain one board: the question, the canonical, and what happened.

    The command to reach for when the suite says a board failed and the
    next question is "what was it supposed to do".
    """
    from sea_of_colours.evals.battles import (
        baseline, boards, ladder, report, runner,
    )

    board = _pick_boards(boards, args.board)[0]

    print()
    print("═" * 74)
    print(f"  {board.id}   ·   day {board.day}   ·   {board.shape}")
    print("═" * 74)
    print()
    print(_para("THE QUESTION", board.question))
    print(_para("THE CANONICAL PLAY", board.canonical))
    if board.baseline:
        print(_para("WHAT STOCK V12 DID", board.baseline))
    note = board.expect.get("note")
    if note:
        print(_para("HOW IT IS SCORED", str(note)))
    print("  Predicates: " + ", ".join(
        k for k in board.expect if k != "note") + "\n")

    if not args.agent:
        return 0

    res = runner.run_battle(
        board, ladder.get_rung(args.rung), ladder.get_loadout(args.loadout),
        agent=args.agent, runs=args.runs,
    )
    print("─" * 74)
    print(f"  {args.agent.upper()} AT {args.rung.upper()}")
    print("─" * 74)
    print()
    for run in res.runs:
        line = (f"  run {run.run_index}: "
                f"{'passed' if run.passed else 'failed'} "
                f"({run.score:.0%} of checks, {run.seconds}s)")
        # The frozen V12 run for this exact battle, when we have one.
        # Prose alone cannot say "you walked four RED where it walked
        # six", and that is usually the whole finding.
        vs = baseline.compare(run.battle_id, run.score)
        print(f"{line}\n           {vs}" if vs else line)
        for move in run.moves:
            print(f"      {json.dumps(move)}")
        for check in run.checks:
            print(f"      {check}")
        if run.rationale:
            print(report._wrap("      " + run.rationale[:600], 66, 6))
        print()

    _print_baseline_moves(res.battle_id)
    return 0


def cmd_diff_gone(args) -> int:
    """`soc diff` is gone. Say where it went.

    v1.43 — it ran one turn and diffed it against the frozen V12 turn
    for the same board. The lab's divergence view does that on a turn
    that was really played, and diffs the prompts too, so the command
    went with the module behind it.

    Kept as a hidden subcommand rather than deleted outright because it
    is quoted in four docs and in old chat logs, and argparse's "invalid
    choice" tells a reader nothing about where the feature went.
    """
    print(
        "`soc diff` has been removed (v1.43).\n"
        "\n"
        "  The turn lab's divergence view replaces it, and does more: it\n"
        "  diffs the prompts and the reasoning as well as the moves, on a\n"
        "  turn out of a real season rather than a constructed board.\n"
        "\n"
        "    python run_web.py     then open /lab\n"
        "    python scripts/soc.py lab      # the turns, without a server\n",
        file=sys.stderr,
    )
    return 2


def _print_baseline_moves(battle_id: str) -> None:
    """Stock V12's own orders on this battle, for a side-by-side read."""
    from sea_of_colours.evals.battles import baseline

    card = baseline.card(battle_id)
    if not card:
        return
    print("─" * 74)
    print(f"  STOCK V12 ON THIS BATTLE   ({card['result'].get('score', 0):.0%})")
    print("─" * 74)
    print()
    for move in card.get("moves") or []:
        print(f"      {json.dumps(move)}")
    missed = [c["name"] for c in card.get("checks") or [] if not c.get("passed")]
    print(f"\n      missed: {', '.join(missed) if missed else 'nothing'}")
    fired = sum((card["result"].get("weapons_fired") or {}).values())
    print(f"      fired:  {fired}\n")


def _para(title: str, body: str) -> str:
    import textwrap

    wrapped = textwrap.fill(" ".join(body.split()), width=68,
                            initial_indent="  ", subsequent_indent="  ")
    return f"  {title}\n{wrapped}\n"


# ── list ────────────────────────────────────────────────────────────


# ── season ──────────────────────────────────────────────────────────


def _season_integrity(store, session_id: str, days_played: int) -> list[str]:
    """Durable-state checks that only a real backend can fail.

    This is the detector for the "day 3 repeats three times" replay bug
    (docs/SNOWFLAKE_LATENCY_BRIEF.md §7). When a stale ``_hydrate_session``
    read makes the runner re-resolve a night, the night's replay frames and
    log lines are appended a second and third time. Nothing raises; the
    season just quietly contains a day that happened repeatedly.

    It cannot be caught by the local test suite, because on the file and
    memory backends writes are synchronous and instant so the race window
    never opens. It has to be checked against whatever the season actually
    persisted, which is what this does — through the store protocol, so it
    works identically on file and on Snowflake.

    Returns a list of human-readable problems; empty means clean.
    """
    problems: list[str] = []

    # ── replay frames: contiguous, unique, one run per day ──────────
    try:
        index = sorted(
            store.day_index(session_id), key=lambda d: int(d.get("day", 0) or 0)
        )
    except Exception as exc:  # noqa: BLE001 — a store without day_index
        index = []
        problems.append(f"could not read the replay day index: {exc}")

    expected_next: int | None = None
    for entry in index:
        day = int(entry.get("day", 0) or 0)
        count = int(entry.get("frame_count", 0) or 0)
        first = entry.get("first_global_idx")
        last = entry.get("last_global_idx")
        if first is None or last is None:
            continue
        first, last = int(first), int(last)
        span = last - first + 1
        if span != count:
            problems.append(
                f"day {day}: {count} frames but global_idx spans {span} "
                f"({first}..{last}) — frames were appended more than once"
            )
        if expected_next is not None and first != expected_next:
            problems.append(
                f"day {day}: global_idx starts at {first}, expected "
                f"{expected_next} — a gap or an overlap with the previous day"
            )
        expected_next = last + 1

    if index and len(index) != len({int(d.get("day", 0) or 0) for d in index}):
        problems.append("the replay day index contains a repeated day")

    # ── log: no line written twice for the same day ─────────────────
    try:
        rows = store.list_log(session_id) or []
    except Exception as exc:  # noqa: BLE001
        rows = []
        problems.append(f"could not read the game log: {exc}")

    seen: dict[tuple, int] = {}
    for row in rows:
        key = (
            int(row.get("day", 0) or 0),
            str(row.get("level", "")),
            str(row.get("text", "")),
        )
        seen[key] = seen.get(key, 0) + 1
    dupes = {k: n for k, n in seen.items() if n > 1}
    if dupes:
        worst = sorted(dupes.items(), key=lambda kv: -kv[1])[:3]
        problems.append(
            f"{len(dupes)} log line(s) written more than once, e.g. "
            + "; ".join(f"day {k[0]} x{n}: {k[2][:48]!r}" for k, n in worst)
        )

    seqs = [int(r.get("seq", -1) or -1) for r in rows if r.get("seq") is not None]
    if seqs and len(seqs) != len(set(seqs)):
        problems.append(
            f"{len(seqs) - len(set(seqs))} duplicate log seq value(s) — "
            "the MAX(seq) folding lost a race"
        )

    return problems


def cmd_season(args) -> int:
    """Play a full season headlessly and keep everything it thought.

    The suite is for micro-tweaks — one hard night, scored in seconds.
    This is the other half: whether the agent can hold a whole season
    together. Slower, and the only way to catch a fork that plays every
    individual night well and still finishes last.
    """
    # Must beat the engine import, and overrides the module-level
    # `memory` default: a season nobody can replay is not worth running,
    # and memory does not survive the process.
    os.environ["SOC_BACKEND"] = args.backend
    if args.store_dir:
        os.environ["SOC_STORE_DIR"] = args.store_dir

    from sea_of_colours.evals import dispatch, seasons
    from sea_of_colours.snowpark import backend as soc_backend

    seats: dict[str, str] = {}
    for n in (1, 2, 3, 4):
        agent = getattr(args, f"p{n}", None)
        if agent:
            seats[f"p{n}"] = agent
    if len(seats) < 2:
        _die("a season needs at least two seats",
             fix="--p1 <your agent> --p2 red_harvest")
    for seat, agent in seats.items():
        try:
            dispatch.resolve(agent)
        except ValueError as exc:
            _die(str(exc), fix="python scripts/soc.py list")

    if args.backend == "memory":
        print("warning: --backend memory keeps nothing — the season will "
              "not be replayable after this process exits.", file=sys.stderr)

    soc_backend.reset_for_tests()

    roster = "  ".join(f"{s}={a}" for s, a in seats.items())
    print(f"season on {args.backend}: {roster}", file=sys.stderr)
    if any(dispatch.needs_llm(a) for a in seats.values()):
        print("  at least one seat calls a model every night — this will "
              "take real minutes.", file=sys.stderr)

    def tick(turn):
        if args.quiet:
            return
        mark = "!" if turn.error else ("~" if turn.fell_back else " ")
        line = (turn.error or turn.rationale or "").replace("\n", " ")[:64]
        print(f"  {mark} d{turn.day} {turn.seat:<3} {turn.phase:<9} {line}",
              file=sys.stderr)

    result = seasons.run_season(
        seats,
        seed=args.seed, width=args.width, height=args.height, days=args.days,
        season_name=args.name, on_turn=tick,
    )
    record = seasons.write_record(result, args.record_dir or seasons.DEFAULT_ROOT)

    integrity = _season_integrity(
        soc_backend.store_for_session(result.session_id),
        result.session_id,
        result.days_played,
    )

    if args.json:
        print(json.dumps({
            "season": result.season_name,
            "session_id": result.session_id,
            "seats": result.seats,
            "scores": result.scores,
            "winner": result.winner,
            "winning_agent": result.winning_agent,
            "days_played": result.days_played,
            "fallback_turns": result.fallback_turns,
            "aborted": result.aborted,
            "record": str(record),
            "integrity": integrity,
        }, indent=2))
        return 1 if (result.aborted or integrity) else 0

    print()
    print(f"  {result.season_name}  ({result.session_id})")
    print(f"  {result.days_played}/{result.season_day_cap} days"
          f"  ·  {len(result.turns)} turns  ·  {result.seconds}s")
    print()
    for seat, score in sorted(result.scores.items(), key=lambda kv: -kv[1]):
        crown = "  <- winner" if seat == result.winner else ""
        print(f"    {seat:<4} {result.seats.get(seat, '?'):<22} "
              f"{score:>7}{crown}")
    if not result.winner and result.scores:
        print("    (tied — no winner)")
    print()
    if result.fallback_turns:
        print(f"  WARNING: {result.fallback_turns} turn(s) fell back to the "
              "built-in heuristic — the model was not reached, so that part "
              "of this season is not your agent's.")
        print()
    if result.aborted:
        print(f"  ABORTED: {result.aborted}")
        print()
    if integrity:
        # Loud on purpose. This is the §7 regression detector and a silent
        # pass/fail line would be worthless in a concurrent soak.
        print("  INTEGRITY FAILURE — the persisted season is not consistent:")
        for problem in integrity:
            print(f"    - {problem}")
        print("    This is the 'repeated day' class of bug. Do not trust this")
        print("    season's scores or replay. See docs/SNOWFLAKE_LATENCY_BRIEF.md §7.")
        print()
    else:
        print("  integrity  replay frames contiguous, no repeated log lines")
        print()
    # Both formats, because the readers differ: paste the Markdown into a
    # coding agent, open the HTML yourself.
    print(f"  cards      {record}/all-cards.html   (open this)")
    print(f"             {record}/all-cards.md     (paste this to an agent)")
    print(f"             {record}/cards/  (one file per turn)")
    if args.backend != "memory":
        # The server has to be told the SAME backend. `python run_web.py`
        # auto-detects, which lands on memory or snowflake depending on
        # the machine — either way it cannot see a file-backed season,
        # and the failure looks like "my season vanished".
        env = f"SOC_BACKEND={args.backend}"
        if args.backend == "file" and args.store_dir:
            env += f" SOC_STORE_DIR={args.store_dir}"
        print(f"  watch it   {env} python run_web.py")
        # ?session= on the root page, not watch.html — there is no
        # watch.html, and app.js reads the session off the query string.
        print(f"             http://127.0.0.1:8000/?session={result.session_id}")
    return 1 if (result.aborted or integrity) else 0


def cmd_list(args) -> int:
    """Everything the kit knows about: boards, rungs, loadouts, agents."""
    from sea_of_colours.evals.battles import boards, ladder
    from sea_of_colours.orchestrator_2 import binding_registry as br

    print("\n  BOARDS")
    for b in boards.BOARDS:
        print(f"    {b.id:<24} day {b.day}  {b.shape}")
    print("\n  RUNGS (easiest first)")
    for r in ladder.up_to(ladder.ORDER[-1]):
        print(f"    {r.id:<24} {r.summary}")
    print("\n  LOADOUTS")
    for l in ladder.LOADOUTS:
        print(f"    {l.id:<24} {l.note}")
    print("\n  AGENTS")
    for a in br.selectable_agents():
        print(f"    {a['value']:<24} {a['label']}")
    print()
    return 0


# ── lab ─────────────────────────────────────────────────────────────


def cmd_lab(args) -> int:
    """The frozen turns your fork can be tested on, and how to open them.

    The lab itself is a page, not a CLI — you pick a turn, cast the
    seats and watch the night resolve in the real UI. This subcommand
    exists so that someone who found `soc` first is told the lab is
    there, and can see what is in it without starting a server.
    """
    from turnlab import boards as lab_boards
    from turnlab import cast as lab_cast
    from turnlab import recall as lab_recall
    from turnlab import store as lab_store

    found = lab_boards.discover(lab_store.store())
    if not found:
        print("\n  no frozen turns yet.")
        print("  grab one:  python -m turnlab grab <season> <day>\n")
        return 0

    print("\n  FROZEN TURNS")
    for b in found:
        d = b.as_dict()
        seed = d.get("seed")
        print(f"    {d.get('name') or d['id']}")
        print(f"      {d['id']}   day {d['day']}   "
              f"{d.get('seats_n') or '?'} players"
              + (f"   seed {seed}" if seed is not None else ""))
        if d.get("tests"):
            print(f"      tests: {d['tests']}")
        # Worth saying out loud: it is the difference between an agent
        # that remembers five nights and one that opens cold, and only
        # one night in the library has it.
        journal = lab_recall.frozen_journal(d["id"]) or {}
        carries = {
            seat: len(entries)
            for seat, entries in (journal.get("seats") or {}).items()
            if entries
        }
        if carries:
            says = ", ".join(f"{s} {n} nights" for s, n in sorted(carries.items()))
            print(f"      journal: {says}")

    print("\n  FORKS THAT CAN PLAY THEM")
    castable, _ = lab_cast.roster()
    for a in castable:
        print(f"    {a.label:<24} {a.display}")

    print("\n  open one:  python run_web.py   then visit /lab\n")
    return 0


# ── doctor ──────────────────────────────────────────────────────────


def cmd_doctor(args) -> int:
    """Check the kit is sound before blaming your agent."""
    from sea_of_colours.orchestrator_2 import agent_manifest
    from sea_of_colours.orchestrator_2 import binding_registry as br

    problems: list[str] = list(br.DISCOVERY_PROBLEMS)
    found, more = agent_manifest.discover()
    problems += [p for p in more if p not in problems]

    print(f"  python           {sys.version.split()[0]}")
    print(f"  backend          {os.environ.get('SOC_BACKEND')}")
    print(f"  agents found     {len(found)} discovered, "
          f"{len(br.AGENT_LABEL_BINDINGS)} routable")
    for man in found:
        print(f"                   {man.label}  ({man.directory.name}/)")

    # v1.45 — name the connection we resolved. "absent" used to be the
    # whole story, which was useless when the real problem was the right
    # file and the wrong section: the fix for those two is not the same,
    # and this is the command you run to tell them apart.
    try:
        from sea_of_colours.snowpark import sfconn

        _props, where = sfconn.resolve_source()
        print(f"  snowflake conn   {where}")
    except Exception as exc:  # pragma: no cover - diagnostic only
        print(f"  snowflake conn   could not be resolved ({exc})")

    # Ask the invoker, not the environment. The PAT may live on the
    # resolved connection rather than in the shell, so an env-var-only
    # check reported "absent" to everyone who followed the guide — the
    # worst possible false negative, because this is the command you run
    # when your agent will not think and it sent you looking for the
    # wrong bug.
    try:
        from sea_of_colours.orchestrator_2.cortex_chat import (
            credentials_status,
        )

        ready, missing = credentials_status()
    except Exception as exc:
        ready, missing = False, f"could not be checked ({exc})"
    print(f"  LLM credentials  {'present' if ready else 'absent'}"
          f"{'' if ready else '  (heuristic agents still run)'}")
    if not ready and missing:
        print(f"                   needs {missing}")

    # Checked here as well as in `soc push` because the whole value is in
    # finding out at 09:30 rather than at 16:45. Nothing about a wrong
    # remote shows up while you work — you only meet it when you publish.
    origin = _git("remote", "get-url", "origin")
    if not origin:
        print("  publishing to    nowhere — this checkout has no 'origin'")
        problems.append(
            "no 'origin' remote, so there is nothing to push your agent to. "
            "Fork the repo on GitHub and clone your fork."
        )
    else:
        theirs = _pushing_at_someone_elses_repo("origin")
        print(f"  publishing to    {origin}")
        if theirs:
            problems.append(
                f"'origin' is {theirs}, which is not your fork — you cloned "
                f"the upstream repo. Everything works until you push. Run "
                f"`soc push` for the two commands that fix it."
            )
        else:
            unlinked = _not_a_real_fork("origin")
            if unlinked:
                problems.append(
                    f"'origin' is {unlinked}, which is yours but is NOT a "
                    f"fork — GitHub has no link from it back to the event "
                    f"repo. Your work is safe and pushing will keep working; "
                    f"the problem is only at the end, because the league is "
                    f"collected from forks and yours would not be found. Fix "
                    f"it now with `gh repo fork <event repo> --remote=false` "
                    f"and `git remote set-url origin <your fork>` — nothing "
                    f"is lost, it only changes where origin points."
                )

    if problems:
        print("\n  PROBLEMS")
        for p in problems:
            print(f"    - {p}")
        return 1
    print("\n  No problems found.")
    return 0


# ── push ────────────────────────────────────────────────────────────


def _pushing_at_someone_elses_repo(remote: str) -> str | None:
    """``owner/name`` if this remote is not yours, else None.

    The mistake this exists to catch: cloning the upstream repo instead
    of forking it. Everything works — you play, you mint, you improve —
    right up to the push, which fails on permissions with an hour left.
    Best-effort by design: without `gh` we cannot know who you are, and a
    check that cannot run must not be a check that blocks.
    """
    from sea_of_colours.orchestrator_2 import fork_collect

    url = _git("remote", "get-url", remote)
    if not url:
        return None
    try:
        repo = fork_collect.upstream_of(url)
    except fork_collect.CollectError:
        return None
    return _not_mine(repo)


def _not_a_real_fork(remote: str) -> str | None:
    """``owner/name`` if this remote is yours but is not a GitHub *fork*.

    The near-miss the ownership check cannot see (v1.47). Making a fresh
    empty repo and pointing a remote at it is what most people mean by
    "I forked it", and locally it is indistinguishable: full history,
    your own copy, push works, `soc push` is happy because the repo
    genuinely is yours.

    What is missing is GitHub's parent link, and only one thing needs
    it — ``soc collect`` finds the field with ``gh api repos/X/forks``,
    which lists forks and nothing else. So this attendee works all day,
    publishes successfully, and is simply absent from the league with no
    error anywhere. It has already happened once, which is the whole
    reason this exists.

    Only asked of a checkout that has an ``upstream`` remote, and that
    condition is doing real work rather than being a cheap guard. "Not a
    fork" is equally true of the event repo itself, so without it the
    organiser's own clone reports the problem it exists to warn other
    people about. Having an ``upstream`` is the one thing that says *I
    am downstream of something* — which is precisely the attendee
    condition, and the guide tells them to add it in the same breath as
    cloning.

    Best-effort for the same reason as its sibling: no ``gh``, no
    answer, and a check that cannot run must not block.
    """
    from sea_of_colours.orchestrator_2 import fork_collect

    url = _git("remote", "get-url", remote)
    if not url:
        return None
    if not _git("remote", "get-url", "upstream"):
        return None
    try:
        repo = fork_collect.upstream_of(url)
    except fork_collect.CollectError:
        return None
    if _not_mine(repo):
        return None  # someone else's repo is the other check's story
    done = subprocess.run(
        ["gh", "api", f"repos/{repo}", "--jq", ".fork"],
        capture_output=True, text=True, check=False,
    )
    if done.returncode != 0:
        return None
    return repo if done.stdout.strip() == "false" else None


def _not_mine(repo: str) -> str | None:
    """``repo`` if it belongs to someone other than the logged-in user."""
    done = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"],
        capture_output=True, text=True, check=False,
    )
    if done.returncode != 0:
        return None
    me = (done.stdout or "").strip().lower()
    owner = repo.split("/", 1)[0].lower()
    return None if not me or owner == me else repo


def cmd_push(args) -> int:
    """Publish your agent to your own fork — your folder, and nothing else.

    Two rules, and they are the same rule seen from either end. You push
    to *your* fork, so nobody else's push can break your agent and yours
    cannot break theirs. And your diff stays inside your own directory,
    so `soc collect` can lift it out at the end of the day and drop it
    beside thirty-nine others without a single conflict to resolve.

    Pushing is not entering. Your fork is where your agent lives; the
    league is assembled from the forks by an organiser. Saying so at the
    end matters — "it is now in the league" was a lie the moment the day
    moved to forks, and the kind that is only discovered at scoring.

    They are checks rather than instructions because instructions do not
    survive a deadline.
    """
    from sea_of_colours.orchestrator_2 import agent_manifest

    theirs = _pushing_at_someone_elses_repo(args.remote)
    if theirs and not args.force:
        _die(
            f"'{args.remote}' points at {theirs}, which is not yours.\n"
            f"\n  You cloned the upstream repo instead of forking it. That "
            f"is the\n  one setup mistake that works perfectly until the "
            f"moment you push.\n"
            f"\n  Your work is fine and none of it is lost. Make your fork "
            f"and point\n  this clone at it:\n"
            f"\n    gh repo fork {theirs} --remote=false\n"
            f"    git remote set-url {args.remote} "
            f"https://github.com/<you>/{theirs.split('/', 1)[1]}.git\n"
            f"    git remote add upstream https://github.com/{theirs}.git\n"
            f"    python scripts/soc.py push\n"
            f"\n  Then `git pull upstream main` is how you take kit updates, "
            f"and\n  `soc push` always goes to your fork."
        )

    found, problems = agent_manifest.discover()
    if problems:
        for p in problems:
            print(f"  warning: {p}", file=sys.stderr)

    mine = _resolve_agent(found, args.agent, problems)
    rel = mine.directory.relative_to(_REPO).as_posix()

    changed = _changed_paths()
    stray = [f for f in changed if not f.startswith(rel)]
    if stray and not args.force:
        print(f"error: changes outside {rel}/:", file=sys.stderr)
        for f in stray[:20]:
            print(f"  {f}", file=sys.stderr)
        print(
            "\n  Your agent has to be self-contained, because at the end of "
            "the day\n  only this directory is lifted out of your fork and "
            "into the league.\n  A change outside it will not travel — it "
            "will simply be missing, and\n  your agent will behave "
            "differently there than it does here.\n"
            "\n  Move what you need into your own directory, revert the rest"
            "\n  (git checkout -- <path>), and push again.\n"
            "\n  If a change outside really is necessary, it is a change to "
            "the kit\n  rather than to your agent — raise it, do not "
            "--force it in.",
            file=sys.stderr,
        )
        return 2

    if not changed:
        print("  nothing to push — no changes in "
              f"{rel}/ since the last commit")
        return 0

    message = args.message or f"{mine.label}: update agent"
    if args.dry_run:
        print(f"  --dry-run: would commit {len(changed)} file(s) under {rel}/")
        print(f"  --dry-run: would push to {args.remote} {_branch()}")
        return 0

    subprocess.run(["git", "add", "--", rel], cwd=_REPO, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=_REPO, check=True)
    return _push_with_rebase(args.remote, _branch(), mine.label, rel)


def _push_with_rebase(remote: str, branch: str, label: str, rel: str) -> int:
    """Push, and if the branch moved under us, rebase and try again.

    On a fork you are not racing the room any more, but you can still be
    rejected as non-fast-forward: a teammate sharing the fork pushed, or
    you pulled upstream on the other laptop. Git is right to refuse, and
    without this it surfaces as a CalledProcessError traceback at the
    exact moment an attendee least wants to read one.

    Rebasing is safe *because* of the one-directory rule: the only
    commits that can be in flight touch different directories, so the
    histories interleave with nothing to resolve. That is the rule paying
    for itself, and it is why this retries rather than asking. If a
    rebase does conflict, something outside your folder moved and the
    answer is a human, not a flag.
    """
    for attempt in (1, 2, 3):
        done = subprocess.run(["git", "push", remote, branch], cwd=_REPO)
        if done.returncode == 0:
            print(f"\n  pushed {label} to {remote}/{branch}.")
            # The closing line promises collection, so it must not be
            # printed to someone collection will never reach.
            unlinked = _not_a_real_fork(remote)
            if unlinked:
                print(
                    f"\n  WARNING — {unlinked} is yours, but it is not a "
                    f"FORK.\n"
                    f"\n  Your push worked and your work is safe. But GitHub "
                    f"has no link from\n  this repo back to the event repo, "
                    f"and the league is collected by\n  walking the event "
                    f"repo's forks — so as things stand your agent would\n"
                    f"  not be found, with no error to tell anyone.\n"
                    f"\n  It is a thirty-second fix and loses nothing:\n"
                    f"\n    gh repo fork <event repo> --remote=false\n"
                    f"    git remote set-url {remote} <your new fork>\n"
                    f"    python scripts/soc.py push\n"
                )
                return 0
            print(f"\n  That is your fork, which is where your agent lives. "
                  f"An organiser\n  collects the forks to build the league, "
                  f"so keep pushing as you go —\n  whatever is on your fork "
                  f"at collection time is what plays.")
            return 0
        if attempt == 3:
            break
        print(f"\n  the branch moved — rebasing onto {remote}/"
              f"{branch} and retrying ({attempt}/2)")
        pulled = subprocess.run(
            ["git", "pull", "--rebase", remote, branch], cwd=_REPO,
        )
        if pulled.returncode != 0:
            subprocess.run(["git", "rebase", "--abort"], cwd=_REPO)
            print(
                f"\nerror: could not rebase onto {remote}/{branch}.\n"
                f"\n  Your commit is safe — it is still here, just not "
                f"pushed yet.\n  A conflict means something outside {rel}/ "
                f"moved, which is not\n  supposed to happen. Show this to an "
                f"organiser rather than\n  forcing it.",
                file=sys.stderr,
            )
            return 3
    print(
        f"\nerror: still could not push after rebasing twice.\n"
        f"\n  Your commit is safe locally. The room may just be busy — "
        f"wait a\n  moment and run `soc push` again.",
        file=sys.stderr,
    )
    return 3


def _branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD") or "main"


def _resolve_agent(found, requested: str | None, problems=None):
    if requested:
        for man in found:
            if man.label == requested:
                return man
        # A directory can exist and still not be discovered, because a
        # manifest that will not load is skipped. Saying "no such agent,
        # go mint one" to someone whose agent is right there — and whose
        # real problem is one bad line of JSON — sends them to delete and
        # start over. If we have a problem naming their folder, that is
        # the error worth printing.
        for problem in problems or ():
            if f"/{requested}/" in str(problem):
                _die(
                    f"{requested!r} exists but its manifest cannot be used",
                    fix=str(problem),
                )
        _die(
            f"no agent named {requested!r}",
            fix="run `python scripts/soc.py list` to see what exists, or "
                "`soc new --team <team> --name <name>` to mint one",
        )
    forks = [m for m in found if m.label != "tabula_v12"]
    if len(forks) == 1:
        return forks[0]
    if not forks:
        _die(
            "no agent of your own was found",
            fix="python scripts/soc.py new --team <team> --name <name>",
        )
    _die(
        f"{len(forks)} agents found, so I cannot tell which is yours",
        fix="pass --agent <label>, one of: "
            + ", ".join(sorted(m.label for m in forks)),
    )


# ── share / grab ────────────────────────────────────────────────────


def cmd_share(args) -> int:
    """Pack your agent into one file, to hand to a teammate.

    The gap this fills is between "we are on the same team" and "this is
    finished". `soc push` is the second of those: it commits, it races
    the room for the branch, and it puts the work on the public record.
    A pair iterating on one agent needs the first, several times an
    hour, and had nothing.
    """
    from sea_of_colours.orchestrator_2 import agent_manifest, fork_parcel

    found, problems = agent_manifest.discover()
    mine = _resolve_agent(found, args.agent, problems)

    try:
        text = fork_parcel.pack(mine)
    except fork_parcel.ParcelError as exc:
        _die(str(exc))

    out = Path(args.out) if args.out else Path.cwd() / (
        mine.label + fork_parcel.SUFFIX
    )
    out.write_text(text, encoding="utf-8")

    parcel = fork_parcel.read(text)
    size = len(text) / 1024
    print(f"  packed {mine.label} — {parcel.header['files']} files, "
          f"{size:.0f}K")
    print(f"  {out}")
    print(f"\n  fingerprint {parcel.fingerprint} — packing the same code "
          f"always gives\n  the same one, so you can both check you are "
          f"on the same version\n  without either of you unpacking "
          f"anything.")
    print(f"\n  They run:  python scripts/soc.py grab {out.name}")
    return 0


def cmd_grab(args) -> int:
    """Install an agent a teammate shared with you.

    Says out loud that this runs their code. The format refuses
    anything that is not source, and unpacking cannot write outside the
    one directory — but the Python inside is the point of the exercise
    and it does execute, so the honest framing is "grab from people you
    know" rather than a list of reassuring guarantees.
    """
    from sea_of_colours.orchestrator_2 import fork_parcel

    path = Path(args.parcel)
    if not path.is_file():
        _die(f"no such file: {path}",
             fix="pass the .socfork your teammate sent you")

    try:
        parcel = fork_parcel.read(path.read_text(encoding="utf-8"))
    except fork_parcel.ParcelError as exc:
        _die(str(exc))
    except UnicodeDecodeError:
        _die(f"{path} is not text, so it is not a parcel",
             fix="a .socfork is a text file; check you sent the right one")

    rename = None
    if args.as_team or args.as_name:
        if not (args.as_team and args.as_name):
            _die("--as-team and --as-name go together",
                 fix="pass both, e.g. --as-team mine --as-name theirs")
        rename = (args.as_team, args.as_name)

    print(f"  {parcel.label} by {parcel.participants or 'unnamed'} "
          f"({parcel.header.get('files')} files, "
          f"fingerprint {parcel.fingerprint})")

    try:
        done = fork_parcel.install(parcel, force=args.force, rename=rename)
    except fork_parcel.ParcelError as exc:
        _die(str(exc))

    verb = "replaced" if done.replaced else "installed"
    print(f"  {verb} {done.label} — {done.files} files")
    if done.renamed_from:
        print(f"  repointed from {done.renamed_from}, so both can run "
              f"side by side")
    print(f"\n  It is castable now, with nothing else to register:\n"
          f"    python scripts/soc.py lab        # frozen turns to try it on\n"
          f"    python run_web.py                # and it is in the New Game "
          f"menu")
    return 0


# ── collect ─────────────────────────────────────────────────────────


def cmd_collect(args) -> int:
    """Organiser-side: assemble the field out of everyone's forks.

    The one thing to understand before running it is that the field is
    built somewhere else. Attendees push to their own forks all day and
    keep pulling from upstream; dropping forty entrants onto the repo
    they are all pulling from would hand the whole room a merge
    conflict at the worst possible moment. So collection clones a
    disposable staging area and assembles there, and this repo is never
    written to.
    """
    from sea_of_colours.orchestrator_2 import fork_collect

    into = Path(args.into).expanduser().resolve()

    # The guard that makes the docstring true rather than aspirational.
    # `--into .` is the obvious typo and it is the one that hurts.
    if into == _REPO or _REPO in into.parents:
        _die(
            f"refusing to collect into {into}, which is inside the repo you "
            f"are running from.\n"
            f"  The field is assembled in a throwaway checkout so that "
            f"nobody's fork has to merge it.\n"
            f"  fix: --into ../soc-league (the default), or any path outside "
            f"this repo"
        )

    try:
        repo = fork_collect.resolve_repo(_REPO, args.repo)
    except fork_collect.CollectError as exc:
        _die(str(exc))

    print(f"\n  collecting the forks of {repo}")

    try:
        forks = fork_collect.forks_of(repo)
    except fork_collect.CollectError as exc:
        _die(str(exc))

    if not forks:
        print("\n  no forks yet — nobody has published an agent.")
        print("  Attendees publish with `soc push`, which goes to their")
        print("  own fork; check they have forked rather than cloned.\n")
        return 0

    print(f"  {len(forks)} fork(s) to look at")

    if args.dry_run:
        # Dry runs answer "who is in the field", which needs no checkout
        # at all — so they do not build a staging area either. Making a
        # rehearsal create a 300MB clone would stop anyone rehearsing.
        report = fork_collect.Report()
        for fork in forks:
            report.skipped.append(fork_collect.Skipped(
                fork.full_name, "(whole fork)", "dry run — not fetched",
            ))
        for line in _collect_lines(report, forks):
            print(line)
        print("\n  drop --dry-run to actually assemble the field.\n")
        return 0

    try:
        root = fork_collect.prepare_staging(into, repo)
    except fork_collect.CollectError as exc:
        _die(str(exc))

    print(f"  staging area {root}")

    report = fork_collect.collect(
        root, forks, skip_owners=args.skip_owner or ()
    )
    roster = fork_collect.write_roster(root, report, repo)

    print(f"\n  IN THE FIELD ({len(report.taken)})")
    for t in sorted(report.taken, key=lambda x: x.label):
        who = ", ".join(t.participants) or "unnamed"
        print(f"    {t.label:<28} {t.fork}")
        print(f"    {'':<28} {who}")

    if report.skipped:
        print(f"\n  NOT COLLECTED ({len(report.skipped)})")
        for s in report.skipped:
            print(f"    {s.fork} — {s.what}: {s.why}")

    print(f"\n  roster written to {roster}")
    print(f"\n  run the league there, not here:")
    print(f"    cd {root} && python scripts/soc.py league\n")
    return 0 if report.ok else 1


def _collect_lines(report, forks) -> list[str]:
    return [f"    {f.full_name}" for f in forks]


# ── weapons ─────────────────────────────────────────────────────────


def cmd_weapons(args) -> int:
    """Which of the four firing rungs is this agent stuck on?

    ``soc suite`` says whether anything fired. This says WHY not, which
    is the question you actually have at that moment.
    """
    from sea_of_colours.evals.battles import readiness
    from sea_of_colours.orchestrator_2 import agent_manifest

    # The baseline is a built-in binding, not a discovered fork, but it is
    # the thing every fork starts as — so checking it has to work, if only
    # to show what a freshly minted agent scores.
    if args.agent == "tabula_v12":
        label = "tabula_v12"
        directory = (
            _REPO / "sea_of_colours/orchestrator_2/harnesses/tabula_v12"
        )
    else:
        found, _ = agent_manifest.discover()
        man = _resolve_agent(found, args.agent)
        label, directory = man.label, man.directory

    rungs = readiness.check(directory)
    print(readiness.render(label, rungs))
    return 0 if readiness.first_gap(rungs) is None else 1


# ── league ──────────────────────────────────────────────────────────


def cmd_league(args) -> int:
    """Score every submitted agent on the same boards and rank them.

    The entrant list is the set of directories with a manifest, which is
    why registration had to stop being a shared file: nobody can be left
    out of the league by a merge going wrong.
    """
    from sea_of_colours.evals.battles import boards, ladder, report, runner
    from sea_of_colours.orchestrator_2 import agent_manifest

    found, problems = agent_manifest.discover()
    for p in problems:
        print(f"  warning: {p}", file=sys.stderr)

    entrants = [m.label for m in found]
    if args.include_baseline:
        entrants.append("red_harvest")
    if not entrants:
        _die("no agents to run", fix="mint one with `soc new`")

    picked = _pick_boards(boards, args.board)
    rungs = list(ladder.up_to(args.up_to))
    loadouts = [ladder.get_loadout(l) for l in args.loadout.split(",")]

    results = []
    for label in entrants:
        print(f"  running {label}...", file=sys.stderr)
        results.append(
            runner.run_suite(
                picked, rungs, loadouts,
                agent=label, runs=args.runs,
                card_dir=Path(args.cards) / label if args.cards else None,
            )
        )
    print()
    print(report.league_table(
        results, rosters={m.label: m.participants for m in found},
    ))
    if args.json:
        Path(args.json).write_text(
            json.dumps([_as_json(r) for r in results], indent=2),
            encoding="utf-8",
        )
        print(f"  wrote {args.json}")
    return 0


# ── wiring ──────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="soc",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd")

    n = sub.add_parser("new", help="mint your agent (start here)")
    n.add_argument("--team", required=True)
    n.add_argument("--name", required=True)
    n.add_argument("--participants", required=True,
                   help="everyone at the table, comma-separated")
    n.add_argument("--menu-label", default=None)
    n.set_defaults(fn=cmd_new)

    s = sub.add_parser("suite", help="score an agent against the redsign battles")
    s.add_argument("--agent", default="red_harvest")
    s.add_argument("--board", default="all", help="comma-separated, or 'all'")
    s.add_argument("--rung", default=None,
                   help="comma-separated rungs; overrides --up-to")
    s.add_argument("--up-to", default="armed",
                   help="run every rung up to this one (default: armed)")
    s.add_argument("--loadout", default="empty",
                   help="comma-separated, or 'all'")
    s.add_argument("--runs", type=int, default=1,
                   help="attempts per battle; >1 exposes flakiness")
    s.add_argument("--cards", default=None,
                   help="directory to write per-run cards into")
    s.add_argument("--record", action="store_true",
                   help="record every turn for replay in the battle room")
    s.add_argument("--record-dir", default=None,
                   help="where bakes go (default: reports/battles)")
    s.add_argument("--json", action="store_true")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_suite)

    w = sub.add_parser("why", help="explain a board, and optionally play it")
    w.add_argument("board")
    w.add_argument("agent", nargs="?", default=None)
    w.add_argument("--rung", default="quiet")
    w.add_argument("--loadout", default="empty")
    w.add_argument("--runs", type=int, default=1)
    w.set_defaults(fn=cmd_why)

    # Hidden: removed in v1.43, but four docs still name it and argparse's
    # "invalid choice" would not say where it went.
    # No `help=`, which is what keeps it out of the listing — argparse
    # renders `help=SUPPRESS` literally rather than hiding the row.
    dg = sub.add_parser("diff")
    dg.add_argument("rest", nargs="*", help=argparse.SUPPRESS)
    dg.set_defaults(fn=cmd_diff_gone)

    se = sub.add_parser(
        "season", help="play a full season headlessly and keep every card")
    se.add_argument("--p1", default=None, help="agent for seat p1")
    se.add_argument("--p2", default=None, help="agent for seat p2")
    se.add_argument("--p3", default=None)
    se.add_argument("--p4", default=None)
    se.add_argument("--seed", type=int, default=42)
    se.add_argument("--days", type=int, default=None,
                    help="season length (default: the engine's cap)")
    se.add_argument("--name", default=None, help="season name")
    se.add_argument("--width", type=int, default=40)
    se.add_argument("--height", type=int, default=28)
    se.add_argument(
        "--backend", default="file", choices=("file", "memory", "snowflake"),
        help="where the season is kept. 'file' is offline and replayable; "
             "'memory' keeps nothing",
    )
    se.add_argument("--store-dir", default=None,
                    help="where the file backend writes (SOC_STORE_DIR)")
    se.add_argument("--record-dir", default=None,
                    help="where cards go (default: reports/seasons)")
    se.add_argument("--quiet", action="store_true")
    se.add_argument("--json", action="store_true")
    se.set_defaults(fn=cmd_season)

    sub.add_parser("list", help="boards, rungs, loadouts, agents").set_defaults(
        fn=cmd_list
    )
    sub.add_parser("lab", help="frozen turns to test your fork on (start here)"
                   ).set_defaults(fn=cmd_lab)
    sub.add_parser("doctor", help="check the kit before blaming your agent"
                   ).set_defaults(fn=cmd_doctor)

    wp = sub.add_parser("weapons",
                        help="which firing rung is your agent stuck on?")
    wp.add_argument("--agent", default=None)
    wp.set_defaults(fn=cmd_weapons)

    pu = sub.add_parser(
        "push",
        help="publish your agent to your fork (your folder only)",
        description=(
            "Commit your agent's directory — and nothing else — and push it "
            "to your own GitHub fork. Push as often as you like; whatever is "
            "on your fork when an organiser runs `soc collect` is what plays "
            "in the league. It refuses if 'origin' is not yours, which is "
            "what cloning the upstream repo instead of forking it looks "
            "like, and prints how to fix it without losing work."
        ),
    )
    pu.add_argument("--agent", default=None)
    pu.add_argument("-m", "--message", default=None)
    pu.add_argument("--remote", default="origin")
    pu.add_argument("--dry-run", action="store_true")
    pu.add_argument("--force", action="store_true",
                    help=argparse.SUPPRESS)
    pu.set_defaults(fn=cmd_push)

    sh = sub.add_parser(
        "share",
        help="pack your agent into one file to hand to a teammate",
        description=(
            "Write your fork to a single .socfork file. Send it however "
            "you like — chat, email, AirDrop — and the other person runs "
            "`soc grab` on it. Nothing is committed and nothing is "
            "published; this is for passing work-in-progress inside a "
            "team, where `soc push` would be far too heavy."
        ),
    )
    sh.add_argument("--agent", default=None)
    sh.add_argument("--out", default=None,
                    help="where to write it (default: <label>.socfork here)")
    sh.set_defaults(fn=cmd_share)

    gr = sub.add_parser(
        "grab",
        help="install an agent a teammate shared with you",
        description=(
            "Unpack a .socfork into your harnesses/ so you can run it. "
            "IT IS SOMEBODY ELSE'S PYTHON AND IT WILL RUN ON YOUR "
            "MACHINE — grab from people you know. Once installed it is "
            "castable in the lab and selectable in a new game with no "
            "further steps."
        ),
    )
    gr.add_argument("parcel", help="the .socfork file you were sent")
    gr.add_argument("--force", action="store_true",
                    help="replace your copy if you already have this agent")
    gr.add_argument("--as-team", default=None,
                    help="install under a different team, alongside yours")
    gr.add_argument("--as-name", default=None,
                    help="install under a different name, alongside yours")
    gr.set_defaults(fn=cmd_grab)

    co = sub.add_parser(
        "collect",
        help="organisers: gather every fork's agent into a staging area",
        description=(
            "Find every fork of this repo, take the agent out of each one, "
            "and assemble them all in a separate checkout ready for the "
            "league. This repo is not written to: attendees are still "
            "pulling from it, and the field is disposable. Each agent goes "
            "on living in the fork it came from — the staging area is "
            "where the league runs, not where anyone's work is kept."
        ),
    )
    co.add_argument("--into", default="../soc-league",
                    help="where to assemble the field (default ../soc-league)")
    co.add_argument("--repo", default=None,
                    help="owner/name to collect the forks of "
                         "(default: whatever origin points at)")
    co.add_argument("--dry-run", action="store_true",
                    help="list the forks without fetching anything")
    co.add_argument("--skip-owner", action="append", default=[],
                    help="ignore this GitHub user (repeatable)")
    co.set_defaults(fn=cmd_collect)

    lg = sub.add_parser("league", help="run every submitted agent and rank them")
    lg.add_argument("--board", default="all")
    lg.add_argument("--up-to", default="armed")
    lg.add_argument("--loadout", default="empty")
    lg.add_argument("--runs", type=int, default=1)
    lg.add_argument("--cards", default=None)
    lg.add_argument("--json", default=None)
    lg.add_argument("--include-baseline", action="store_true", default=True)
    lg.set_defaults(fn=cmd_league)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return 0
    # One place, so a superseded command cannot quietly lose its notice
    # by someone adding an early return to its body.
    if getattr(args, "cmd", None) in _SUPERSEDED:
        _superseded(args.cmd)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
