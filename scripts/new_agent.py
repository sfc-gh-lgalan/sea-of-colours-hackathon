#!/usr/bin/env python3
"""Mint a new agent by forking V12 — the first thing you run at the hackathon.

    python scripts/new_agent.py --team redwatch --name reaper

That copies the shipped V12 harness to
``sea_of_colours/orchestrator_2/harnesses/redwatch_reaper/``, repoints its
imports, and writes an ``agent.json`` declaring it. Restart the server and
``REDWATCH_REAPER`` is in the New Game dropdown, playable against V12.

**Your agent is one directory.** Registration is discovery over
``agent.json`` (v1.39), so nothing outside your fork is touched when it
is created and nothing outside it needs to change again. That is what
lets forty teams work at once without treading on each other, and what
lets the end-of-day collection lift each agent out of its own fork and
set it beside the others with nothing to merge.

**Why fork instead of editing V12 in place.** V12 is the baseline you are
trying to beat. Edit it directly and you lose the control: "better than
before" becomes unmeasurable, matches against other teams are no longer
like-for-like, and `git diff` stops telling you what you changed. The
copy costs a second and keeps the comparison honest.

Naming is ``<team>_<agent>`` so a room full of forks stays legible and
two teams can't collide on ``reaper``.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_HARNESSES = _REPO / "sea_of_colours" / "orchestrator_2" / "harnesses"
_SOURCE = _HARNESSES / "tabula_v12"
_REGISTRY = _REPO / "sea_of_colours" / "orchestrator_2" / "binding_registry.py"

_SOURCE_NAME = "tabula_v12"

# Same rule as a Python identifier, minus the right to be weird: the name
# becomes a package directory, a dict key, and a Snowflake-ish agent
# constant, so keep it to lowercase ascii.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _die(msg: str) -> "NoReturn":  # type: ignore[valid-type]
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _validate(part: str, field: str) -> str:
    part = part.strip().lower()
    if not _NAME_RE.match(part):
        _die(
            f"--{field} must be lowercase letters, digits and underscores, "
            f"starting with a letter (got {part!r})"
        )
    if part.endswith("_"):
        _die(f"--{field} must not end with an underscore (got {part!r})")
    return part


# v1.44 — the copied README is V12's, and V12's opens by telling you not
# to edit the directory you are reading. Unbannered, the minter's own
# "now read your README" step points at a document instructing you to
# undo the mint. The banner is prepended rather than surgical because
# V12's README is edited often and any patch keyed to its wording would
# rot silently; a preamble stays true whatever the body says.
_FORK_README_BANNER = """\
> ### This directory is yours — {label}
>
> Everything below this line was written about V12 and still describes
> your code accurately, because you have not changed it yet. Two things
> to read past:
>
> - **"Don't edit this directory"** applies to `harnesses/tabula_v12/`,
>   the pristine baseline you are scored against. It does not apply here.
>   Edit anything in this directory you like — that is the exercise.
> - **The fork instructions** are how you got here. You do not need them
>   again unless you want a second agent.
>
> Rewrite this README as the description below stops being true. What you
> changed and why is what the league reads.

"""


# v1.48 — a fork arms itself; stock V12 does not.
#
# V12's orbit.py passes ``weapons_enabled=False`` (see the v14 note there),
# which is right for the baseline: it has no night-phase play that fires a
# charge, so ordnance costs it a probe and scores nothing. But the mint is
# a wholesale copy, so a fork inherited that off-switch and bought nothing
# all game — silently, because the descriptor explaining the skipped
# purchase is gated on the same flag. Eighteen agent-seasons went by
# without a shot before anyone noticed.
#
# Keyed to the call line rather than the comment block above it, which is
# prose and will be reworded. If even this stops matching, the mint fails
# loudly (``_ARMED_CHECK`` below) — the one outcome worth ruling out is
# handing someone an agent that can never arm and never says so.
_V12_DISARM = (
    "        actions, rationale = plan_orbit_actions("
    "agent_view, weapons_enabled=False)"
)
_FORK_ARM = """\
        # Stock V12 passes ``weapons_enabled=False`` here, and the comment
        # above explains why: with no play that fires a charge, ordnance
        # costs it a probe and scores nothing. Your fork is a different
        # proposition — "buy an EMP on day one" is the exercise — so the
        # mint turns the switch back on. Set it to False again if you would
        # rather spend the credits on vision. Just make that your decision
        # rather than something you inherited without being told.
        actions, rationale = plan_orbit_actions(agent_view)"""


def _copy_harness(dest: Path, label: str, *, dry_run: bool) -> int:
    """Copy V12 and repoint its self-imports at the new package."""
    files = [
        p for p in sorted(_SOURCE.rglob("*"))
        if p.is_file()
        and "__pycache__" not in p.parts
        and p.suffix in {".py", ".md"}
    ]
    if dry_run:
        return len(files)

    for src in files:
        rel = src.relative_to(_SOURCE)
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_text(encoding="utf-8")
        # Two substitutions, both needed.
        #
        # Lowercase covers imports: every module self-references by
        # absolute path, so there are no relative imports to fix.
        #
        # Uppercase covers identity, which is easy to overlook and does
        # real damage if you do. `INNER_AGENT_LABEL = "TABULA_V12"` is
        # what lands in the audit trail, so an unrenamed fork files its
        # turns under V12's name — and the whole point is comparing the
        # two. It also namespaces the `TABULA_V12_*` env toggles, so two
        # forks on one machine can be tuned independently.
        text = text.replace(_SOURCE_NAME, label)
        text = text.replace(_SOURCE_NAME.upper(), label.upper())
        if rel.name == "README.md":
            text = _FORK_README_BANNER.format(label=label.upper()) + text
        if rel.name == "orbit.py":
            text = text.replace(_V12_DISARM, _FORK_ARM)
        out.write_text(text, encoding="utf-8")
    return len(files)


def _check_armable() -> None:
    """Fail before anything is written, not halfway through.

    Runs with the other pre-flight checks so a source V12 this script can
    no longer arm is caught while the mint is still a no-op.
    """
    text = (_SOURCE / "orbit.py").read_text(encoding="utf-8")
    if _V12_DISARM in text:
        return
    if "weapons_enabled=False" in text:
        _die(
            "cannot arm the fork: tabula_v12/orbit.py disables weapon "
            "buying on a line this script no longer recognises. Update "
            "_V12_DISARM in scripts/new_agent.py — a fork that silently "
            "cannot buy a weapon cannot do the exercise."
        )


def _check_registrable(label: str) -> None:
    """Fail before anything is written, not halfway through."""
    # Built-ins are the one thing a fork must not shadow: take the
    # ``tabula_v12`` label and you become the baseline everyone is scored
    # against, which is exactly the comparison the fork exists to make.
    text = _REGISTRY.read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    if f'"{label}"' in code:
        _die(
            f"{label!r} is a built-in agent name — pick another --team or "
            f"--name so your agent is scored separately from it"
        )
    existing = _HARNESSES / label / "agent.json"
    if existing.exists():
        _die(
            f"{existing.relative_to(_REPO)} already exists — pick another "
            f"--name, or delete that directory first"
        )


def _people(raw: str | None) -> list[str]:
    """Parse ``--participants`` into a roster, or explain what is missing.

    Comma-separated because that is what someone types without thinking,
    and because a shell makes repeated flags tedious for a table of four
    people who want to get on with it.
    """
    people = [p.strip() for p in (raw or "").replace(";", ",").split(",")]
    people = [p for p in people if p]
    if not people:
        _die(
            "--participants is required: name everyone at the table, e.g.\n"
            "    --participants 'Ada Lovelace, Grace Hopper'\n\n"
            "  The league table at the end of the day is the public record "
            "of who\n  built what. A row that names only an agent cannot be "
            "credited to\n  anybody, and a broken entrant cannot be chased "
            "to its authors."
        )
    return people


def _write_manifest(dest: Path, team: str, name: str, menu_label: str,
                    participants: list[str], *, dry_run: bool) -> None:
    """Declare the fork inside its own directory.

    This is the whole of registration (v1.39). Nothing shared is edited,
    which is what makes forty teams working at once possible: two agents
    can never touch the same file, so two agents can never conflict.
    It is also what makes collection cheap — an entrant is a directory
    with a manifest in it, so gathering the field out of forty forks is
    forty checkouts and no merges.
    """
    if dry_run:
        return
    payload = {
        "team": team,
        "name": name,
        "participants": participants,
        "menu_label": menu_label,
        "entry": "harness:run",
        "needs_llm": True,
    }
    (dest / "agent.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="new_agent",
        description="Copy the V12 harness into an agent of your own.",
    )
    ap.add_argument("--team", required=True, help="Your team name, e.g. redwatch")
    ap.add_argument("--name", required=True, help="Your agent name, e.g. reaper")
    ap.add_argument(
        "--participants", required=True,
        help="Everyone at the table, comma-separated, e.g. 'Ada, Grace'",
    )
    ap.add_argument(
        "--menu-label", default=None,
        help="Text shown in the New Game dropdown (default: auto).",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be created without writing anything.",
    )
    args = ap.parse_args(argv)

    team = _validate(args.team, "team")
    name = _validate(args.name, "name")
    participants = _people(args.participants)
    label = f"{team}_{name}"
    const = f"SOC_{label.upper()}"
    dest = _HARNESSES / label
    menu_label = args.menu_label or (
        f"{label.upper()} — {team}'s agent (needs a Snowflake PAT · slow)"
    )

    if not _SOURCE.is_dir():
        _die(f"source harness missing: {_SOURCE}")
    if dest.exists():
        _die(f"{dest.relative_to(_REPO)} already exists — pick another --name")
    # Every check that can fail runs before the first file is written, so
    # a rejected name never leaves a half-copied package behind.
    _check_registrable(label)
    _check_armable()

    n = _copy_harness(dest, label, dry_run=args.dry_run)
    try:
        _write_manifest(dest, team, name, menu_label, participants,
                        dry_run=args.dry_run)
    except BaseException:
        if not args.dry_run and dest.exists():
            shutil.rmtree(dest)
        raise

    rel = dest.relative_to(_REPO)
    if args.dry_run:
        print(f"--dry-run: would create {rel}/ ({n} files + agent.json)")
        print(f"--dry-run: would register {label!r} by discovery")
        return 0

    print(f"created  {rel}/  ({n} files, forked from tabula_v12)")
    print(f"declared  {rel}/agent.json  ->  {label}")
    print()
    print("Everything your agent is lives in that one directory. Nothing")
    print("outside it was touched, and nothing outside it needs to be —")
    print("that one directory is all that travels to the league.")
    print()
    print("next:")
    print("  1. restart the server (python run_web.py)")
    print(f"  2. NEW GAME -> pick {label.upper()} for a rival seat")
    print(f"  3. read {rel}/README.md — the two gaps V12 ships with are")
    print("     the exercise; that file says exactly where they live")
    print()
    print("  test it on a frozen turn (this is the loop):")
    print("    python run_web.py     then open /lab")
    print(f"    ...cast {label.upper()} into a seat, and diff it against V12")
    print()
    print("  or list the turns and forks without a server:")
    print("    python scripts/soc.py lab")
    print()
    # The rule exists because the shared suite is shared. One fork's
    # work-in-progress red becomes forty people's red, all day (v1.43).
    print("  your own tests go inside your folder, not in tests/:")
    print(f"    {rel}/tests/test_*.py")
    print(f"    python -m pytest {rel}")
    print()
    # Working in a pair is the normal shape, and the alternative people
    # reach for otherwise is pushing half-finished work somewhere their
    # partner can pull it from (v1.43).
    print("  hand it to a teammate (one file, nothing published):")
    print(f"    python scripts/soc.py share --agent {label}")
    print("    ...they run: python scripts/soc.py grab <that file>")
    print()
    print("  publish it to your fork (your folder only):")
    print(f"    python scripts/soc.py push")
    print()
    print("  that pushes to YOUR fork, which is where your agent lives.")
    print("  an organiser collects the forks to build the league, so push")
    print("  as you go — what is on your fork at collection time plays.")
    print("  not sure your remote is right?  python scripts/soc.py doctor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
