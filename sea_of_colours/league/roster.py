"""Who is actually in the league.

``agent_manifest.discover()`` finds every fork in the tree, which is the
wrong question for a competition. The tree also holds ``tabula_v12``
(the baseline everyone forked), the organiser's own test rigs, and
agents written days before the event — seating those silently would
produce a league nobody agreed to enter.

So the field is a **file**, not a scan. ``reports/league/roster.json``
names the entrants; everything else discovered is written down beside
them with the reason it is not competing. That second list is the point:
"why is my agent not in the league" is the question an organiser gets
asked across a noisy room, and a filter that leaves no trace cannot
answer it.

The file is seeded from a cutoff — agents whose first commit landed
after the event started — and then **edited by hand**. The cutoff is a
convenience for building the first draft, not an authority; once the
file exists it wins, because the organiser knows things the commit log
does not (a team that renamed an agent, a fork that pushed late, an
entrant that withdrew).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from sea_of_colours.league import ROOT

#: Default location. One file, checked by eye before a league runs.
ROSTER_PATH = ROOT / "roster.json"

#: Agents that are never entrants however the roster is built. These
#: ship in the repo, so every fork has a copy and crediting one to a
#: team would be crediting them with somebody else's work.
SHIPPED = ("tabula_v12", "emp_harvest_test")


@dataclass
class Entrant:
    """One competing agent, and enough provenance to credit it."""

    label: str
    team: str = ""
    participants: tuple[str, ...] = ()
    fork: str = ""
    #: First commit to touch the agent's directory, ISO-8601 local. Kept
    #: because it is the evidence for the entrant being in the field at
    #: all, and an organiser overruling the cutoff should be able to see
    #: what they are overruling.
    first_commit: str = ""

    @property
    def display(self) -> str:
        """Something short enough for a standings row."""
        return self.label


@dataclass
class Benched:
    """A discovered agent that is not competing, and why not."""

    label: str
    reason: str


@dataclass
class Roster:
    """The field, plus the trail of everyone left out of it."""

    entrants: list[Entrant] = field(default_factory=list)
    bench: list[Benched] = field(default_factory=list)
    cutoff: str = ""
    note: str = ""

    @property
    def labels(self) -> list[str]:
        return [e.label for e in self.entrants]

    def entrant(self, label: str) -> Optional[Entrant]:
        return next((e for e in self.entrants if e.label == label), None)

    # ── persistence ────────────────────────────────────────────────

    def to_json(self) -> dict:
        return {
            "cutoff": self.cutoff,
            "note": self.note,
            "entrants": [
                {
                    "label": e.label,
                    "team": e.team,
                    "participants": list(e.participants),
                    "fork": e.fork,
                    "first_commit": e.first_commit,
                }
                for e in self.entrants
            ],
            "bench": [{"label": b.label, "reason": b.reason} for b in self.bench],
        }

    def save(self, path: Path | str = ROSTER_PATH) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), indent=2) + "\n")
        return path


def load(path: Path | str = ROSTER_PATH) -> Roster:
    """Read the roster. Missing file is an error, not an empty league.

    Falling back to "everyone in the tree" would be the one failure mode
    worth avoiding: it produces a plausible league containing the
    baselines, and nothing about the output would say so.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"no roster at {path} — build one with `soc cup roster`, then "
            "edit it to say who is competing"
        )
    raw = json.loads(path.read_text())
    return Roster(
        cutoff=str(raw.get("cutoff") or ""),
        note=str(raw.get("note") or ""),
        entrants=[
            Entrant(
                label=str(e["label"]),
                team=str(e.get("team") or ""),
                participants=tuple(e.get("participants") or ()),
                fork=str(e.get("fork") or ""),
                first_commit=str(e.get("first_commit") or ""),
            )
            for e in raw.get("entrants") or []
        ],
        bench=[
            Benched(label=str(b["label"]), reason=str(b.get("reason") or ""))
            for b in raw.get("bench") or []
        ],
    )


def discovered() -> dict[str, dict]:
    """Every agent the dispatcher could actually seat, by label."""
    from sea_of_colours.orchestrator_2 import agent_manifest

    manifests, _problems = agent_manifest.discover()
    return {
        m.label: {
            "team": m.team,
            "participants": tuple(m.participants),
            "directory": str(m.directory),
        }
        for m in manifests
    }


def check(roster: Roster) -> list[str]:
    """Problems that would make a league run lie, worst first.

    Run before pairing, never after. An entrant the dispatcher cannot
    resolve fails on first contact — but by then half the field has
    played, and a league you have to re-run from scratch is a league
    that does not finish before people go home.
    """
    problems: list[str] = []
    found = discovered()

    seen: set[str] = set()
    for e in roster.entrants:
        if e.label in seen:
            problems.append(f"{e.label}: listed twice in the roster")
        seen.add(e.label)
        if e.label not in found:
            problems.append(
                f"{e.label}: in the roster but not in the tree — copy the "
                "agent into sea_of_colours/orchestrator_2/harnesses/"
            )
        if e.label in SHIPPED:
            problems.append(
                f"{e.label}: ships with the repo, so it cannot be an entrant"
            )
        if not e.participants:
            # v1.41 made participants mandatory for exactly this reason.
            problems.append(
                f"{e.label}: names no participants, so a win cannot be credited"
            )

    if len(roster.entrants) < 2:
        problems.append("a league needs at least two entrants")
    return problems


def seed_from(
    agents: Iterable[dict],
    *,
    cutoff: str,
    note: str = "",
) -> Roster:
    """Draft a roster from inventory rows, splitting on the cutoff.

    ``agents`` is whatever ``scripts/league_inventory.py`` collected:
    dicts carrying ``label``, ``team``, ``participants``, ``fork`` and
    ``first_commit``. Anything first committed at or after ``cutoff``
    becomes an entrant; everything else is benched with its date, so the
    reason is legible without going back to the commit log.
    """
    roster = Roster(cutoff=cutoff, note=note)
    for row in agents:
        label = str(row.get("label") or "")
        if not label:
            continue
        first = str(row.get("first_commit") or "")
        if label in SHIPPED:
            roster.bench.append(Benched(label, "ships with the repo"))
        elif not first:
            roster.bench.append(Benched(label, "no commit date — check by hand"))
        elif first < cutoff:
            roster.bench.append(Benched(label, f"built before the event ({first[:16]})"))
        else:
            roster.entrants.append(Entrant(
                label=label,
                team=str(row.get("team") or ""),
                participants=tuple(row.get("participants") or ()),
                fork=str(row.get("fork") or ""),
                first_commit=first,
            ))
    roster.entrants.sort(key=lambda e: e.first_commit)
    roster.bench.sort(key=lambda b: b.label)
    return roster
