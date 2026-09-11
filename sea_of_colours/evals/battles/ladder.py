"""Difficulty rungs — the same board, against progressively worse trouble.

A board on its own asks "does your agent find the play". That is worth
knowing once. What it does not tell you is whether the play was found or
merely remembered: an agent tuned until it passes nine fixed boards has
learned nine boards, which is worth nothing on night three of a real
season against somebody who brought chaff.

So each board is run at a rung. A rung does not touch the geometry — the
pures, the mass and the shape of the decision stay exactly as analysed —
it changes what is arrayed against you: how many opponents, how much of
the seam they can see, how many of their harvesters are already
committed to it, and what is in everybody's weapon rack.

The rungs are ordered so that a passing agent should degrade gracefully
rather than fall off a cliff. If it passes QUIET and fails WATCHED, it
does not understand tempo. If it passes WATCHED and fails ARMED, it does
not understand that a lift can be cancelled. If it passes ARMED and
fails CROWDED, it is planning against one opponent. Where an agent stops
tells you what to teach it, which is the entire point.

**The weapons axis is separate and deliberate.** ``loadout`` arms YOUR
agent rather than the opposition. Stock V12 buys weapons and never fires
them — that is one of the two gaps it ships with — so running the same
board at the same rung with an empty rack and then with a full one
isolates exactly one question: given the means to shape the night, does
your agent use them? An agent that scores identically with and without
two EMP in the rack has not learned to fight, whatever its pass rate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class Rung:
    """One difficulty setting, applied on top of any board."""

    id: str
    order: int
    summary: str
    teaches: str

    # How many seats besides ours. The engine scales some things with
    # seat count, so this is a real change and not just more probes.
    opponents: int = 1

    # Multipliers and additions on the board's captured opposition.
    # Kept as deltas rather than absolutes so a rung composes with any
    # board without knowing that board's geometry.
    extra_rival_probes: int = 0
    rival_harvesters_on_seam: int = 0
    rival_sees_the_pure: bool = False

    # What the opposition is holding. The single most important dial:
    # chaff is the only thing that can deny an hour-one landing, so its
    # presence changes whether repeating a pure insures anything.
    rival_chaff: int = 0
    rival_emp: int = 0

    # What we are holding, before any ``loadout`` override.
    our_chaff: int = 0
    our_emp: int = 0

    # Blue in the bank decides who can arm at the NEXT orbit, which is
    # what makes a late board frightening even when the racks are empty.
    rival_blue: int = 0
    our_blue: int = 0


RUNGS: tuple[Rung, ...] = (
    Rung(
        id="quiet",
        order=0,
        summary="one opponent, nothing watching, no weapons anywhere",
        teaches=(
            "that a free extension must be taken. With nobody able to "
            "punish exposure, a short grab leaves money on the table and is "
            "the failure case — the only board where greed is correct."
        ),
        opponents=1,
    ),
    Rung(
        id="watched",
        order=1,
        summary="one opponent with live vision on the jackpot",
        teaches=(
            "tempo. They can land on the pure before you can, so the "
            "question stops being how much you can carry and becomes how "
            "fast you can arrive. Vein on the way in now costs the pure."
        ),
        opponents=1,
        extra_rival_probes=1,
        rival_sees_the_pure=True,
    ),
    Rung(
        id="armed",
        order=2,
        summary="one opponent holding chaff and an EMP, and able to buy more",
        teaches=(
            "that a plan can be cancelled. Chaff is the only thing that "
            "denies an hour-one landing, so with chaff in play a second "
            "pass at the pure finally insures against something — and a "
            "chain that lifts late can be cut before it lifts at all. "
            "Value-first ordering stops being a nicety."
        ),
        opponents=1,
        extra_rival_probes=1,
        rival_sees_the_pure=True,
        rival_chaff=1,
        rival_emp=1,
        rival_blue=220,
    ),
    Rung(
        id="crowded",
        order=3,
        summary="three opponents, several probes on the seam, two committed",
        teaches=(
            "that you do not hold hour one and must go anyway. With rival "
            "harvesters already committed to the seam the grab is "
            "contingent, and the temptation is to take the safe cells "
            "somewhere else. Conceding costs the same as trying and wins "
            "nothing."
        ),
        opponents=3,
        extra_rival_probes=3,
        rival_harvesters_on_seam=2,
        rival_sees_the_pure=True,
        # Armed to at least the previous rung's level, on purpose. The
        # captured four-seat board had empty racks and three seats that
        # could all arm at the next orbit, which is a real state — but a
        # rung that is SAFER than the one below it breaks the promise
        # the ladder makes, and "I passed crowded but not armed" would
        # then tell you nothing about what to fix.
        rival_chaff=1,
        rival_emp=1,
        rival_blue=210,
    ),
    Rung(
        id="siege",
        order=4,
        summary="three armed opponents, committed to the seam, and you are armed too",
        teaches=(
            "whether your agent can fight. Everything above, plus ordnance "
            "on both sides. This is the rung where doing nothing with a full "
            "rack is visibly the wrong answer: they will deny you unless you "
            "deny them first."
        ),
        opponents=3,
        extra_rival_probes=3,
        rival_harvesters_on_seam=2,
        rival_sees_the_pure=True,
        rival_chaff=2,
        rival_emp=1,
        rival_blue=400,
        our_chaff=1,
        our_emp=1,
        our_blue=200,
    ),
)

BY_ID: Mapping[str, Rung] = {r.id: r for r in RUNGS}

# The ordered names, for CLI help and for "run everything up to here".
ORDER: tuple[str, ...] = tuple(r.id for r in sorted(RUNGS, key=lambda r: r.order))


@dataclass(frozen=True)
class Loadout:
    """What OUR agent is holding, overriding whatever the rung granted.

    Separate from the rung because it answers a different question. The
    rung asks "can it still find the play when things get worse"; the
    loadout asks "does it do anything different when handed a weapon".
    Holding the rung fixed and moving only this isolates the second.
    """

    id: str
    emp: int = 0
    chaff: int = 0
    snap: int = 0
    blue: int = 0
    note: str = ""


LOADOUTS: tuple[Loadout, ...] = (
    Loadout("empty", note="no ordnance — the baseline every rung assumes"),
    Loadout("chaff", chaff=2, blue=200,
            note="two chaff: can cancel a lift, or strand a committed rival"),
    Loadout("emp", emp=2, blue=200,
            note="two EMP: can deny ground for eight hours and time a walk-in"),
    # There was no snap loadout at all, so a snap agent could not be armed on
    # any board and its play was the one thing the lab could never show. Two
    # charges, because the strong snap play fires at H1 and wants a second for
    # the following night.
    Loadout("snap", snap=2, blue=200,
            note="two snap: can refuse an hour-one landing on a watched pure"),
    Loadout("both", emp=1, chaff=2, snap=2, blue=400,
            note="a full rack — if the play does not change, nothing was learned"),
)

LOADOUT_BY_ID: Mapping[str, Loadout] = {l.id: l for l in LOADOUTS}


def get_rung(rung_id: str) -> Rung:
    try:
        return BY_ID[rung_id]
    except KeyError:
        raise KeyError(
            f"no rung named {rung_id!r}. Available, easiest first: "
            + ", ".join(ORDER)
        ) from None


def get_loadout(loadout_id: str) -> Loadout:
    try:
        return LOADOUT_BY_ID[loadout_id]
    except KeyError:
        raise KeyError(
            f"no loadout named {loadout_id!r}. Available: "
            + ", ".join(sorted(LOADOUT_BY_ID))
        ) from None


def up_to(rung_id: str) -> Sequence[Rung]:
    """Every rung up to and including ``rung_id``, easiest first.

    The normal way to run a suite: an agent that fails QUIET has nothing
    to prove at SIEGE, and running it there anyway wastes tokens and
    buries the finding that matters.
    """
    ceiling = get_rung(rung_id)
    return tuple(
        r for r in sorted(RUNGS, key=lambda r: r.order) if r.order <= ceiling.order
    )
