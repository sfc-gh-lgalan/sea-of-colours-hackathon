"""Weapons readiness — which rung is your agent stuck on?

``soc suite`` reports the outcome: *nothing fired across 27 armed
battles*. True, and useless on its own — that sentence reads the same
whether the model could not name the verb, was never offered the play,
or simply did not fancy it. The attendee is left to guess, and guessing
usually lands on "the model is stupid", which is almost never it.

Firing a weapon needs four independent things to be true, and they have
to be built in order because each one is invisible until the one before
it works:

1. **The agent knows it owns a rack.** It cannot choose a weapon it was
   never told it has.
2. **The play is offerable and compilable.** The move schema has to
   allow the verb, the option menu has to carry the play, and the
   packager has to pass it through.
3. **The offer explains itself.** Bare geometry is not a decision. The
   menu line has to say what firing here buys, in the terms a human
   would use.
4. **Doctrine says when, and at what tempo.** An option nobody is told
   to reach for stays unreached.

This module checks all four by reading the fork's own source, so it
needs no model, no credentials and no game — which is what lets it run
in the inner loop rather than at the end of it.

**It is a smoke test, not a proof.** It answers "have you built this
rung at all", not "is your implementation good". A rung can pass here
and still fire at the wrong cells; that is what ``soc suite`` and the
saved cards are for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, get_args


def _weapon_verbs() -> tuple[str, ...]:
    """Wire verbs the engine accepts for ordnance, asked of the engine.

    v1.45 — this was a hand-written literal and it silently missed
    ``snap_launch`` for the whole of SNAP's life, so a fork whose
    doctrine was SNAP-only was told it had not built rungs 2 and 4. SNAP
    is the cheapest weapon at 100 blue and so the likeliest first one a
    team reaches for, which made it the worst possible verb to miss —
    and the failure was invisible, because the ladder answered
    confidently either way.

    Every ordnance tag is ``<kind>_<verb>`` for a kind the economy
    prices, which is enough to derive the list from the two things that
    already have to be right. A new weapon is picked up here for free.
    """
    from sea_of_colours.game.policy import MoveTag
    from sea_of_colours.game.weapons import BLUE_COST_BY_KIND

    kinds = set(BLUE_COST_BY_KIND)
    return tuple(
        tag for tag in get_args(MoveTag) if tag.split("_")[0] in kinds
    )


#: Wire verbs the engine accepts for ordnance (``game/policy.py``).
WEAPON_VERBS = _weapon_verbs()

#: The weapon nouns those verbs are built from — ``emp``, ``chaff``,
#: ``snap`` — used to spot offensive doctrine below.
WEAPON_NOUNS = tuple(sorted({v.split("_")[0] for v in WEAPON_VERBS}))

#: Files whose weapon-awareness does NOT count for rung 1.
#:
#: The rung asks whether the half of the agent that could actually FIRE
#: knows what is in the rack, so a read only counts if it could change a
#: decision. Two kinds cannot:
#:
#:   * Buying code. It happens in orbit and reads the rack by
#:     construction — counting it would pass every fork on day zero.
#:   * Reporting code. ``card.py`` renders the turn for a human after
#:     the fact (v1.38 gave it an arsenal line so a lab card says who was
#:     armed). Printing the rack on a diagnostic is not the night phase
#:     knowing it, and a fork that got a green rung 1 out of a debug
#:     render would be sent up the ladder with the bottom rung missing —
#:     which is precisely the misdiagnosis this module exists to stop.
_BUYING_FILES = ("orbit.py", "orbit_policy.py", "card.py")

#: Doctrine counts as offensive when it tells the seat to SPEND its own
#: ordnance. Matching loose phrases like "deny their" does not work — the
#: shipped doctrine already says to land a spare probe on a rival's probe
#: to blind it, which is probe denial and nothing to do with weapons.
_OFFENSIVE_RE = re.compile(
    r"\b(fire|launch|flare|spend|salvo)\w*\s+"
    r"(?:your|an|a|the|one)?\s*(" + "|".join(WEAPON_NOUNS) + r")\b",
    re.IGNORECASE,
)

#: ...but not when the rival is the one doing the firing. "if they launch
#: an EMP" is survival advice, which the fork already ships.
_RIVAL_SUBJECT_RE = re.compile(
    r"\b(they|rival|rival's|opponent|opponent's|enemy|their)\b[^.]{0,40}$",
    re.IGNORECASE,
)


@dataclass
class Rung:
    """One step of the ladder, and what to do if it is not built yet."""

    n: int
    name: str
    passed: bool
    detail: str
    fix: str

    @property
    def mark(self) -> str:
        return "PASS" if self.passed else "FAIL"


def _py_files(root: Path) -> List[Path]:
    """Fork sources, newest layout first, skipping frozen baselines.

    ``_v7/`` is the frozen v7 lineage kept for regression baselines. A
    fork that only edits the frozen copy has not changed its live agent,
    so counting it would report a pass the attendee cannot observe.
    """
    return sorted(
        p for p in root.rglob("*.py")
        if "__pycache__" not in p.parts
    )


def _read(paths: List[Path]) -> Dict[Path, str]:
    out: Dict[Path, str] = {}
    for p in paths:
        try:
            out[p] = p.read_text(encoding="utf-8")
        except OSError:
            continue
    return out


def _live_sources(src: Dict[Path, str]) -> Dict[Path, str]:
    """Sources excluding the frozen ``_v7`` lineage."""
    return {p: t for p, t in src.items() if "_v7" not in p.parts}


def _rung_1_knows_its_rack(src: Dict[Path, str]) -> Rung:
    """Does anything outside the buying code read the seat's stock?"""
    hits = [
        p.name for p, text in _live_sources(src).items()
        if "weapon_stock" in text and p.name not in _BUYING_FILES
    ]
    if hits:
        return Rung(
            1, "the agent knows it owns a rack", True,
            f"read in {', '.join(sorted(set(hits))[:4])}",
            "",
        )
    buys = any("weapon_stock" in t for t in src.values())
    return Rung(
        1, "the agent knows it owns a rack", False,
        ("only the buying code reads weapon_stock — the night phase is "
         "never told what is in the rack"
         if buys else "nothing in the fork reads weapon_stock"),
        (f"carry the seat's own {'/'.join(WEAPON_NOUNS)} counts into the "
         "night view (world_view.py) and say them in the prompt "
         "(prompt.py). An agent cannot choose a weapon it does not know "
         "it owns."),
    )


def _rung_2_can_offer_and_compile(src: Dict[Path, str]) -> Rung:
    """Schema allows the verb, an option carries it, packager passes it."""
    live = _live_sources(src)

    def _files_mentioning(*needles: str) -> List[str]:
        return [
            p.name for p, text in live.items()
            if any(n in text for n in needles)
        ]

    schema = [
        p.name for p, text in live.items()
        if "chat_schema" in p.name and any(v in text for v in WEAPON_VERBS)
    ]
    options = [
        p.name for p, text in live.items()
        if "agency" in p.name and any(v in text for v in WEAPON_VERBS)
    ]
    compile_ = [
        p.name for p, text in live.items()
        if p.name in ("packager.py", "move_sanitizer.py")
        and any(v in text for v in WEAPON_VERBS)
    ]

    missing: List[str] = []
    if not schema:
        missing.append("the move schema has no weapon verb")
    if not options:
        missing.append("no option in agency.py emits one")
    if not compile_:
        missing.append("the packager/sanitizer would strip it")

    if not missing:
        return Rung(
            2, "the play is offerable and compilable", True,
            "schema, option menu and packager all know the verb", "",
        )
    return Rung(
        2, "the play is offerable and compilable", False,
        "; ".join(missing),
        ("all three must agree before a single salvo launches. Widen the "
         "schema enum first (it is the cheapest to verify), then register "
         "a weapon option, then let it through the packager."),
    )


def _rung_3_offer_explains_itself(src: Dict[Path, str]) -> Rung:
    """A weapon option has to carry prose, not just geometry."""
    live = _live_sources(src)
    agency = {p: t for p, t in live.items() if "agency" in p.name}
    weapon_bits = [
        (p, t) for p, t in agency.items()
        if any(v in t for v in WEAPON_VERBS)
    ]
    if not weapon_bits:
        return Rung(
            3, "the offer explains itself", False,
            "no weapon option exists to check yet",
            "build rung 2 first — this rung grades what that one produces.",
        )
    explained = [
        p.name for p, t in weapon_bits
        if "rationale=" in t and "detail=" in t
    ]
    if explained:
        return Rung(
            3, "the offer explains itself", True,
            f"weapon options set detail and rationale in {explained[0]}", "",
        )
    return Rung(
        3, "the offer explains itself", False,
        "weapon options carry geometry but no detail/rationale",
        ("fill Option.detail and Option.rationale — say what the salvo "
         "buys ('3 rival probes under 2 clouds, blinds their eye on your "
         "pure for 8h'), not where it lands. Bare geometry was not enough "
         "for seam plays either; see the OBS-34 note on Option.rationale."),
    )


def _rung_4_doctrine_says_when(src: Dict[Path, str]) -> Rung:
    """Is there offensive doctrine, or only survival advice?"""
    live = _live_sources(src)
    doctrine = {p: t for p, t in live.items() if "doctrine" in p.name}
    if not doctrine:
        return Rung(
            4, "doctrine says when, and at what tempo", False,
            "no doctrine module found in the fork",
            "add one — the option menu is a list, doctrine is the judgement.",
        )
    hits: List[str] = []
    for text in doctrine.values():
        for m in _OFFENSIVE_RE.finditer(text):
            before = text[max(0, m.start() - 60):m.start()]
            if _RIVAL_SUBJECT_RE.search(before):
                continue  # "if they launch an EMP" — survival, not use
            hits.append(m.group(0).strip().lower())
    if hits:
        return Rung(
            4, "doctrine says when, and at what tempo", True,
            f"tells the seat to spend its own ordnance "
            f"(e.g. {sorted(set(hits))[0]!r})",
            "",
        )
    return Rung(
        4, "doctrine says when, and at what tempo", False,
        f"doctrine covers surviving {', '.join(WEAPON_NOUNS)}, never using them",
        ("write the offensive case: when denying a rival's eye beats "
         "harvesting, and which hour to fire. An option nobody is told "
         "to reach for stays unreached."),
    )


def check(agent_dir: Path) -> List[Rung]:
    """Walk the four rungs against a fork's source."""
    src = _read(_py_files(agent_dir))
    return [
        _rung_1_knows_its_rack(src),
        _rung_2_can_offer_and_compile(src),
        _rung_3_offer_explains_itself(src),
        _rung_4_doctrine_says_when(src),
    ]


def first_gap(rungs: List[Rung]) -> Optional[Rung]:
    """The lowest unbuilt rung — the only one worth working on."""
    for r in rungs:
        if not r.passed:
            return r
    return None


def render(agent: str, rungs: List[Rung]) -> str:
    """Human-readable ladder, ending in the single next action."""
    w = 74
    out = [
        "",
        "─" * w,
        f"  WEAPONS READINESS · {agent}",
        "─" * w,
        "",
    ]
    for r in rungs:
        out.append(f"  {r.n}. {r.name:<44} {r.mark}")
        if r.detail:
            out.append(f"       {r.detail}")
    out.append("")

    gap = first_gap(rungs)
    if gap is None:
        out += [
            "  All four rungs are built. Whether it fires WELL is a",
            "  different question — run the suite with a rack and compare:",
            "",
            "    soc suite --agent <yours> --loadout empty,both",
            "",
            "  If armed and unarmed score the same, it is firing into space.",
            "",
        ]
        return "\n".join(out)

    out += [
        f"  START AT RUNG {gap.n}.",
        "",
    ]
    out += ["  " + line for line in _wrap(gap.fix, w - 4)]
    out += [
        "",
        "  The rungs are ordered because each is invisible until the one",
        "  before it works. Building 4 before 1 changes nothing you can see.",
        "",
    ]
    return "\n".join(out)


def _wrap(text: str, width: int) -> List[str]:
    words = text.split()
    lines: List[str] = []
    cur = ""
    for word in words:
        if cur and len(cur) + 1 + len(word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return lines
