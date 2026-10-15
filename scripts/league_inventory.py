#!/usr/bin/env python3
"""Organiser-side fork inventory — who is in the field, and can they fire?

`soc collect` assembles a *runnable* league: it clones upstream so the
field sits in a working checkout. That clone is ~900MB and takes minutes,
because the tutorial films are checked in and `.webm` does not delta —
and the per-fork fetches shallow the repo immediately afterwards, so
nearly all of it is thrown away on arrival.

This does the other job: answer "what have the teams actually built" in
under a minute, without a full checkout. It takes only each fork's
harness directory, via a partial + sparse clone, so the films are never
fetched at all.

**Organiser-only, and deliberately additive.** Nothing here changes what
an attendee runs, so it can land mid-hackathon without asking a room of
people to pull. It never writes to this repo and never to anyone's fork.

Upstream's own harnesses are NOT entrants. Every fork inherits them, so
counting them would credit the organiser for a directory nobody wrote —
`emp_harvest_test` is the live case, since unlike `tabula_v12` it
carries a manifest and would otherwise be ranked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

HARNESS_PREFIX = "sea_of_colours/orchestrator_2/harnesses"

#: Anything that is not a real agent directory.
_NOT_AGENTS = {"__pycache__"}


# ── shelling out ──────────────────────────────────────────────────────


def _run(args: Sequence[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args), cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, check=False,
    )


def _gh_json(endpoint: str, jq: str) -> list[str]:
    """One `gh api` call, returning the jq-selected lines."""
    done = _run(["gh", "api", endpoint, "--jq", jq])
    if done.returncode != 0:
        raise SystemExit(
            f"error: gh api {endpoint} failed\n"
            f"  {(done.stderr or '').strip()[:300]}\n"
            f"  fix: check `gh auth status`"
        )
    return [ln for ln in done.stdout.splitlines() if ln.strip()]


# ── the field ─────────────────────────────────────────────────────────


@dataclass
class Agent:
    label: str
    fork: str
    participants: list[str] = field(default_factory=list)
    team: str = ""
    name: str = ""
    path: Optional[Path] = None
    #: Content hash, so one team mirroring an agent across two forks is
    #: told apart from two teams genuinely claiming one name.
    fingerprint: str = ""
    #: ISO dates of the first and last commit to touch this directory,
    #: so "built today" is answerable without trusting a fork's tip date.
    created: str = ""
    updated: str = ""
    #: The same two with their time of day kept, local. The dates above
    #: are for the columns; these are what the league roster splits on,
    #: since an event starting after lunch cannot be cut on a date.
    created_at: str = ""
    updated_at: str = ""
    #: Weapon verbs found anywhere in the agent's own sources. The rung
    #: ladder ignores `_v7/`, which is wrong for the schema — see
    #: `weapon_verbs_present`.
    verbs: list[str] = field(default_factory=list)
    #: First unbuilt weapons rung, or None when all four are built.
    start_at: Optional[int] = None
    rungs: list[bool] = field(default_factory=list)


def fingerprint(directory: Path) -> str:
    """Hash a harness directory's tracked-looking content."""
    h = hashlib.sha256()
    for f in sorted(directory.rglob("*")):
        if not f.is_file() or "__pycache__" in f.parts:
            continue
        h.update(str(f.relative_to(directory)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:12]


def upstream_repo() -> str:
    """owner/name that `origin` points at."""
    got = _run(["git", "remote", "get-url", "origin"], cwd=_REPO)
    if got.returncode != 0:
        raise SystemExit("error: no `origin` remote here")
    url = got.stdout.strip()
    tail = url.split("github.com", 1)[-1].lstrip(":/")
    return tail[:-4] if tail.endswith(".git") else tail


def shipped_labels() -> set[str]:
    """Harness directories upstream ships — inherited by every fork.

    Read from `origin/main` rather than the working tree, so an agent
    the organiser happens to have lying around uncommitted does not get
    mistaken for part of the kit.
    """
    got = _run(
        ["git", "ls-tree", "--name-only", "origin/main", f"{HARNESS_PREFIX}/"],
        cwd=_REPO,
    )
    out: set[str] = set()
    for line in got.stdout.splitlines():
        leaf = line.rstrip("/").rsplit("/", 1)[-1]
        if leaf and not leaf.endswith((".py", ".md", ".json")):
            out.add(leaf)
    return out


def list_forks(repo: str) -> list[tuple[str, str]]:
    """[(full_name, default_branch)] for every fork of ``repo``."""
    rows = _gh_json(
        f"repos/{repo}/forks?per_page=100",
        '.[] | "\\(.full_name)\\t\\(.default_branch)"',
    )
    return [tuple(r.split("\t", 1)) for r in rows]  # type: ignore[misc]


def harvest(
    fork: str,
    branch: str,
    cache: Path,
    *,
    inherited: set[str],
) -> list[Agent]:
    """Take one fork's own agent directories into ``cache``.

    Partial (`--filter=blob:none`) plus sparse, so only blobs under the
    harnesses prefix are ever fetched — which is what keeps the films
    out of it. A full `--depth 1` clone would still be ~470MB.
    """
    work = cache / fork.replace("/", "__")
    if work.exists():
        shutil.rmtree(work)

    cloned = _run([
        "git", "clone", "--quiet", "--depth", "1", "--single-branch",
        "--branch", branch, "--filter=blob:none", "--no-checkout",
        f"https://github.com/{fork}.git", str(work),
    ])
    if cloned.returncode != 0:
        print(f"  ! {fork}: clone failed — "
              f"{(cloned.stderr or '').strip()[:120]}", file=sys.stderr)
        return []

    _run(["git", "sparse-checkout", "init", "--cone"], cwd=work)
    _run(["git", "sparse-checkout", "set", HARNESS_PREFIX], cwd=work)
    out = _run(["git", "checkout", "--quiet"], cwd=work)
    if out.returncode != 0:
        print(f"  ! {fork}: checkout failed — "
              f"{(out.stderr or '').strip()[:120]}", file=sys.stderr)
        return []

    root = work / HARNESS_PREFIX
    if not root.is_dir():
        return []

    found: list[Agent] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name in _NOT_AGENTS or d.name in inherited:
            continue
        manifest = d / "agent.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  ! {fork}: {d.name} has an unreadable manifest ({exc})",
                  file=sys.stderr)
            continue
        found.append(Agent(
            label=d.name,
            fork=fork,
            participants=list(data.get("participants") or []),
            team=str(data.get("team") or ""),
            name=str(data.get("name") or ""),
            path=d,
            fingerprint=fingerprint(d),
        ))
    return found


@dataclass
class Clash:
    """Two forks offering the same agent label."""

    label: str
    kept: str
    also: str
    identical: bool


def resolve_clashes(agents: list[Agent]) -> tuple[list[Agent], list[Clash]]:
    """One label, one entrant — and say who else claimed it.

    A team whose members each push to their own fork produces the same
    agent twice; that is a mirror, not a dispute, and collapsing it
    quietly would still be wrong because the roster must say which fork
    the league actually ran. Two *different* agents under one name is a
    different animal and has to be settled by a human.
    """
    by_label: dict[str, list[Agent]] = defaultdict(list)
    for a in agents:
        by_label[a.label].append(a)

    kept: list[Agent] = []
    clashes: list[Clash] = []
    for label, group in by_label.items():
        first, rest = group[0], group[1:]
        kept.append(first)
        for other in rest:
            clashes.append(Clash(
                label=label,
                kept=first.fork,
                also=other.fork,
                identical=other.fingerprint == first.fingerprint,
            ))
    return kept, clashes


def engine_weapon_verbs() -> tuple[str, ...]:
    """Ask the engine which ordnance verbs it accepts."""
    try:
        from sea_of_colours.evals.battles import readiness
        return tuple(readiness.WEAPON_VERBS)
    except Exception:
        return ("emp_launch", "chaff_flare", "snap_launch")


def weapon_verbs_present(directory: Path, verbs: Sequence[str]) -> list[str]:
    """Engine weapon verbs in this agent's *move schema* — can it emit one?

    Scoped to `chat_schema` on purpose. Searching the whole harness finds
    a verb in every fork, because the stock kit reads rival ordnance and
    names the verbs to do it; that tells you nothing about whether this
    agent can fire. The enum the model is allowed to emit does.

    Deliberately *includes* `_v7/`. The rung ladder skips it as a frozen
    lineage, but the live harness imports from it — the move schema is
    `_v7/chat_schema.py`, re-exported by the top-level module — so
    skipping it calls a fork that wired weapons correctly unbuilt.
    """
    seen: set[str] = set()
    for f in (directory / "chat_schema.py", directory / "_v7" / "chat_schema.py"):
        if not f.is_file():
            continue
        text = f.read_text(errors="replace")
        # Quoted, because these are enum members rather than prose. Bare
        # `snap` is excluded by construction: it is a stock *situational
        # boolean* ("is a snap threat live"), not a move verb, and
        # counting it would mark every fork in the room as armed.
        seen.update(
            v for v in verbs if f'"{v}"' in text or f"'{v}'" in text
        )
    return sorted(seen)


def commit_times(fork: str, label: str) -> tuple[str, str]:
    """(first, last) commit *timestamps* for this agent, local ISO-8601.

    Asked of the API rather than the clone, because a `--depth 1` fetch
    knows only the tip commit's date — which is the fork's, not the
    agent's, and would call every directory in a busy fork "today".

    Time of day, not just the date, because an event that starts after
    lunch cannot be split on a date: the morning's experiments and the
    afternoon's entrants share one. Rendered in local time for the same
    reason — "14:42" has to mean what the room remembers happening.
    """
    path = f"{HARNESS_PREFIX}/{label}"
    done = _run([
        "gh", "api",
        f"repos/{fork}/commits?path={path}&per_page=100",
        "--jq", ".[].commit.committer.date",
    ])
    stamps = [s.strip() for s in done.stdout.splitlines() if s.strip()]
    if not stamps:
        return "", ""

    def _local(raw: str) -> str:
        try:
            utc = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return raw
        return utc.astimezone().isoformat(timespec="seconds")

    return _local(stamps[-1]), _local(stamps[0])


def commit_dates(fork: str, label: str) -> tuple[str, str]:
    """(first, last) commit dates touching this agent, as YYYY-MM-DD."""
    first, last = commit_times(fork, label)
    return first[:10], last[:10]


def scan_readiness(agents: Iterable[Agent]) -> None:
    """Fill in each agent's weapons rungs, in place.

    Uses the shipped ladder rather than a private copy, so a retune of
    what counts as "built" is picked up here for free.
    """
    try:
        from sea_of_colours.evals.battles import readiness
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"  ! readiness scan unavailable: {exc}", file=sys.stderr)
        return

    for a in agents:
        if a.path is None:
            continue
        try:
            rungs = readiness.check(a.path)
            a.rungs = [bool(r.passed) for r in rungs]
            gap = readiness.first_gap(rungs)
            a.start_at = None if gap is None else gap.n
        except Exception as exc:  # pragma: no cover
            print(f"  ! {a.label}: readiness scan failed ({exc})",
                  file=sys.stderr)


# ── output ────────────────────────────────────────────────────────────


def render(
    agents: list[Agent],
    forks: int,
    barren: list[str],
    clashes: Sequence[Clash] = (),
) -> str:
    armed = [a for a in agents if a.start_at is None and a.rungs]
    lines = [
        "",
        "─" * 78,
        f"  THE FIELD · {len(agents)} agent(s) from "
        f"{len({a.fork for a in agents})} of {forks} fork(s)",
        "─" * 78,
        "",
    ]
    today = date.today().isoformat()
    if agents:
        lines.append(
            f"  {'agent':<34} {'started':<11} {'last':<11} "
            f"{'weapon verbs':<28} rungs"
        )
        for a in sorted(agents, key=lambda x: (x.created or "9999", x.label)):
            if a.rungs:
                mark = "".join("#" if r else "." for r in a.rungs)
                state = mark if a.start_at is not None else "ALL"
            else:
                state = "?"
            new = " *" if a.created == today else "  "
            lines.append(
                f"  {a.label:<34} {a.created or '?':<11} "
                f"{a.updated or '?':<11} "
                f"{(', '.join(a.verbs) or '—')[:28]:<28} {state}{new}"
            )
        fresh = [a for a in agents if a.created == today]
        wired = [a for a in agents if a.verbs]
        lines += [
            "",
            "  * started today.  rungs: 1 rack · 2 offerable · 3 explains · "
            "4 doctrine",
            f"  {len(fresh)} of {len(agents)} started today.",
            f"  {len(wired)} carry an engine weapon verb in their source; "
            f"{len(armed)} pass all four rungs.",
        ]

    disputed = [c for c in clashes if not c.identical]
    mirrored = [c for c in clashes if c.identical]
    if disputed:
        lines += ["", f"  !! NAME CLASHES NEEDING A DECISION ({len(disputed)})"]
        for c in disputed:
            lines += [
                f"    {c.label} — DIFFERENT code in two forks",
                f"      ran: {c.kept}",
                f"      ignored: {c.also}",
            ]
    if mirrored:
        lines += ["", f"  mirrored across forks ({len(mirrored)}) — same "
                      f"agent, counted once"]
        for c in mirrored:
            lines.append(f"    {c.label} — ran {c.kept}, also in {c.also}")

    if barren:
        lines += ["", f"  NO AGENT OF THEIR OWN ({len(barren)})"]
        for f in barren:
            lines.append(f"    {f}")
    return "\n".join(lines) + "\n"


def write_inventory(
    path: Path,
    agents: list[Agent],
    repo: str,
    clashes: Sequence[Clash] = (),
) -> Path:
    rows = ["# The field", "", f"Collected from the forks of `{repo}`.", "",
            "Dates are the first and last commit to touch the agent's own",
            "directory. `Verbs` is every engine weapon verb found anywhere in",
            "its source, `_v7/` included — which the rung ladder skips.", "",
            "| Agent | Team | Participants | Fork | Started | Last | Verbs | Rungs |",
            "|---|---|---|---|---|---|---|---|"]
    for a in sorted(agents, key=lambda x: (x.created or "9999", x.label)):
        if a.rungs:
            weapons = ("all four" if a.start_at is None
                       else f"start at {a.start_at}")
        else:
            weapons = "not scanned"
        rows.append(
            f"| `{a.label}` | {a.team} | {', '.join(a.participants) or '—'} "
            f"| [{a.fork}](https://github.com/{a.fork}) "
            f"| {a.created or '?'} | {a.updated or '?'} "
            f"| {', '.join(a.verbs) or '—'} | {weapons} |"
        )
    if clashes:
        rows += ["", "## Claimed by more than one fork", ""]
        for c in clashes:
            how = ("identical — a team mirroring its own work"
                   if c.identical else
                   "**DIFFERENT CODE — needs a decision**")
            rows.append(
                f"- `{c.label}`: ran `{c.kept}`, also in `{c.also}` ({how})"
            )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def write_json(path: Path, agents: list[Agent], repo: str) -> Path:
    """The same inventory, for a program rather than a person.

    `INVENTORY.md` is the thing an organiser reads; this is what the
    league roster is seeded from. Kept as a separate file rather than
    parsed back out of the Markdown, because a table that exists to be
    read will get reformatted the first time it looks cramped, and
    anything parsing it breaks silently when that happens.
    """
    payload = {
        "upstream": repo,
        "collected": datetime.now().astimezone().isoformat(timespec="seconds"),
        "agents": [
            {
                "label": a.label,
                "team": a.team,
                "name": a.name,
                "participants": list(a.participants),
                "fork": a.fork,
                "first_commit": a.created_at,
                "last_commit": a.updated_at,
                "verbs": list(a.verbs),
                "rungs": list(a.rungs),
                "start_at": a.start_at,
                "fingerprint": a.fingerprint,
            }
            for a in sorted(agents, key=lambda x: (x.created_at or "9999", x.label))
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def export(agents: list[Agent], into: Path) -> None:
    """Lay each agent down as a plain directory, ready to read or copy."""
    for a in agents:
        if a.path is None:
            continue
        dest = into / a.label
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(a.path, dest, ignore=shutil.ignore_patterns("__pycache__"))
        a.path = dest


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="league_inventory",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--into", default="../soc-agents",
                   help="where the agents land (default ../soc-agents)")
    p.add_argument("--repo", default="",
                   help="owner/name to collect the forks of "
                        "(default: whatever origin points at)")
    p.add_argument("--skip-owner", action="append", default=[],
                   help="ignore this GitHub user (repeatable)")
    p.add_argument("--no-readiness", action="store_true",
                   help="skip the weapons scan (faster)")
    p.add_argument("--keep-cache", action="store_true",
                   help="leave the per-fork clones behind for inspection")
    p.add_argument("--jobs", type=int, default=12,
                   help="forks fetched at once (default 12). All of this "
                        "is network wait, so raise it if github lets you")
    args = p.parse_args(argv)

    repo = args.repo or upstream_repo()
    into = Path(args.into).expanduser()
    if not into.is_absolute():
        into = (_REPO / into).resolve()
    cache = into / ".cache"
    cache.mkdir(parents=True, exist_ok=True)

    inherited = shipped_labels()
    print(f"\n  upstream {repo}")
    print(f"  not entrants (every fork inherits them): "
          f"{', '.join(sorted(inherited)) or 'none'}")

    forks = list_forks(repo)
    skip = {o.lower() for o in args.skip_owner}
    forks = [(f, b) for f, b in forks if f.split("/")[0].lower() not in skip]
    print(f"  {len(forks)} fork(s) to look at\n")

    # Threads, not processes: every second of this is spent waiting on
    # github, so the GIL is released the whole time and a pool of them
    # turns a four-minute sweep into about twenty seconds. Each fork
    # clones into its own cache directory, so they never touch.
    agents: list[Agent] = []
    barren: list[str] = []
    harvested: dict[str, list[Agent]] = {}
    seen = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {
            pool.submit(harvest, fork, branch, cache, inherited=inherited): fork
            for fork, branch in forks
        }
        for fut in as_completed(futures):
            fork = futures[fut]
            seen += 1
            try:
                got = fut.result()
            except Exception as exc:
                print(f"  [{seen}/{len(forks)}] {fork} — FAILED ({exc})",
                      file=sys.stderr)
                got = []
            harvested[fork] = got
            names = ", ".join(a.label for a in got) or "nothing of its own"
            print(f"  [{seen}/{len(forks)}] {fork} — {names}")

    # Re-ordered to the fork listing, because a thread pool finishes in
    # whatever order the network felt like and a roster that reshuffles
    # between runs is one nobody can diff.
    for fork, _branch in forks:
        got = harvested.get(fork) or []
        if got:
            agents.extend(got)
        else:
            barren.append(fork)

    # Before export: two forks offering one label would otherwise have
    # the second silently overwrite the first on disk.
    agents, clashes = resolve_clashes(agents)
    export(agents, into)

    verbs = engine_weapon_verbs()
    print("\n  dating each agent and reading its verbs...")

    def _date_one(a: Agent) -> None:
        a.created_at, a.updated_at = commit_times(a.fork, a.label)
        a.created, a.updated = a.created_at[:10], a.updated_at[:10]
        if a.path is not None:
            a.verbs = weapon_verbs_present(a.path, verbs)

    # One API round trip per agent, and they are independent.
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        list(pool.map(_date_one, agents))
    if not args.no_readiness:
        scan_readiness(agents)
    if not args.keep_cache:
        shutil.rmtree(cache, ignore_errors=True)

    print(render(agents, len(forks), barren, clashes))
    roster = write_inventory(into / "INVENTORY.md", agents, repo, clashes)
    machine = write_json(into / "INVENTORY.json", agents, repo)
    print(f"  agents in {into}")
    print(f"  inventory written to {roster}")
    print(f"                      {machine}  (for `soc cup roster`)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
