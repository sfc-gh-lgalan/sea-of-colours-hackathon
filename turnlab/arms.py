"""What a seat is holding when the night starts.

A frozen board carries whatever its season had, and for the boards
minted so far that is nothing — no EMP, no chaff, an empty hoard. Which
makes one question unaskable: does this agent do anything different when
you hand it a weapon? That is the question the redsign nights were built
around, and "buy an EMP on day one" is the exercise a fork is set.

So a rack is a property of *opening* a board, never of the board. The
board stays the fixed point it has to be, and two runs of the same night
can then differ by exactly one thing.

The four racks are the ones the battles ladder used, and the numbers are
restated here rather than imported. Battles is on its way out and the
lab is meant to outlive it; a dependency pointing at code being deleted
is worse than a second copy of four small tuples.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, MutableMapping


#: The weapon kinds a rack can name, in the order they read best. Named
#: here rather than derived from ``BLUE_COST_BY_KIND`` because a rack is
#: a fixed editorial choice — "one EMP" — and a weapon added to the
#: engine should not silently appear in the lab's pickers with no rack
#: author having thought about what it is for. :func:`arm` still
#: validates against the board's own price table, so the two cannot
#: drift into offering something a board cannot hold.
KINDS: tuple[str, ...] = ("emp", "chaff", "snap")


@dataclass(frozen=True)
class Rack:
    """Ordnance handed to one seat, plus the BLUE to build more."""

    id: str
    emp: int = 0
    chaff: int = 0
    snap: int = 0
    #: Weapon-build fuel, as hoard purity. Zero on every shipped rack —
    #: see :data:`RACKS`. Kept because closing the procurement gap would
    #: want it back, and ``_give_blue`` is not code worth rediscovering.
    blue: int = 0
    note: str = ""

    @property
    def counts(self) -> dict[str, int]:
        """Ordnance by kind, in the shape ``weapon_stock`` uses.

        Everything downstream reads this rather than the fields, so
        adding a weapon is a field and a rack entry — not an edit to
        ``arm``, ``stock_of`` and ``catalogue`` as well.
        """
        return {kind: int(getattr(self, kind, 0)) for kind in KINDS}


#: One of each, at most. The ladder handed out pairs, which turned every
#: run into a question about salvo economics when the question worth
#: asking is simpler: given a weapon, does this agent fire it at all? A
#: second round only lets a fork look decisive by spending twice.
#:
#: And no BLUE, since v1.43. A rack used to arrive with 200–455 purity of
#: build fuel, on the reasoning that stock lets a seat fire what it was
#: given while BLUE lets it make more. In this lab it cannot: weapons are
#: built by orbit actions (``BuildEmpAction`` / ``BuildChaffAction``) and
#: a frozen turn is a night, with ``turn.settle`` submitting empty orbit
#: actions afterwards. So the fuel was never spendable — it just sat in
#: the hoard as two fat parcels, taking vault slots, showing up in the
#: percept and moving the score.
#:
#: That made the one comparison the racks exist for dishonest: armed
#: versus unarmed was also richer versus poorer, and a fork that shipped
#: more looked like a fork that used its EMP well. Arming now changes
#: exactly one thing.
RACKS: tuple[Rack, ...] = (
    Rack("empty", note="no ordnance — the board exactly as it was frozen"),
    Rack("chaff", chaff=1,
         note="one chaff: can cancel a lift, or strand a committed rival"),
    Rack("emp", emp=1,
         note="one EMP: can deny ground for eight hours and time a walk-in"),
    Rack("both", emp=1, chaff=1,
         note="one of each — if the play does not change, nothing was learned"),
    # v1.36. SNAP asks a narrower question than the other two, because it
    # is a guess rather than an area: the fork has to name the ONE square
    # it thinks the night turns on, an hour before it turns. A fork that
    # fires it at the middle of the map has told you as much as one that
    # does not fire it at all.
    Rack("snap", snap=1,
         note="one SNAP: denies a single square for a single hour — "
              "beacon, landing or walk-in, but you must pick the square"),
    Rack("arsenal", emp=1, chaff=1, snap=1,
         note="one of all three, which is exactly the 600 cap — the most "
              "ordnance any seat can be holding when a night opens"),
)

BY_ID: dict[str, Rack] = {r.id: r for r in RACKS}

DEFAULT = "empty"


def get(rack_id: str) -> Rack:
    try:
        return BY_ID[str(rack_id or DEFAULT)]
    except KeyError:
        raise KeyError(
            f"no rack named {rack_id!r}. Available: " + ", ".join(BY_ID)
        ) from None


def arm(blob: MutableMapping[str, Any], seat: str, rack_id: str) -> Rack:
    """Stamp a rack onto a session blob, in place.

    Writes the stockpile directly rather than running an orbit phase,
    which is the same shortcut the eval builder takes: the point is to
    watch an agent *use* a weapon, not to make it buy one first.

    The shortcut skips ``_apply_build_weapon``, and with it the arsenal
    cap (RULEBOOK §4.9.8) — so the check is repeated here. Not because
    any shipped rack is over the ceiling (``arsenal`` sits exactly on
    it), but because a rack that quietly exceeded it would hand every
    agent on the board a public arsenal figure the engine could never
    have produced, and the lab's one job is to pose real positions.

    v1.36 — both the cap and the prices come off THIS BOARD's stamp
    rather than today's constants, and a kind the board does not price
    is refused rather than stamped. A season frozen before SNAP existed
    has no SNAP in its economy; writing one into its stockpile would
    pose a rack that board's own engine would refuse to sell, which is
    the same dishonesty the cap check exists to prevent.
    """
    from sea_of_colours.game.weapons import (
        LEGACY_BLUE_COST_BY_KIND,
        LEGACY_WEAPONISED_BLUE_CAP,
        weaponised_blue,
    )

    rack = get(rack_id)
    counts = {k: n for k, n in rack.counts.items() if n}

    prices = blob.get("weapon_blue_costs") or dict(LEGACY_BLUE_COST_BY_KIND)
    cap = int(blob.get("weapon_blue_cap") or LEGACY_WEAPONISED_BLUE_CAP)

    unpriced = sorted(set(counts) - set(prices))
    if unpriced:
        raise ValueError(
            f"rack {rack.id!r} needs {', '.join(unpriced)}, which this "
            "board's season does not stock — pick a rack the board could "
            "actually have been holding"
        )

    held = weaponised_blue(counts, prices)
    if held > cap:
        raise ValueError(
            f"rack {rack.id!r} is {held} blue of ordnance, over the "
            f"{cap} arsenal cap — no seat can hold it, so "
            "no frozen turn should pose it"
        )
    stock = blob.setdefault("weapon_stock", {})
    stock[str(seat)] = {kind: int(rack.counts.get(kind, 0)) for kind in prices}
    if rack.blue:
        _give_blue(blob, str(seat), rack.blue)
    _restamp_public_arsenal(blob, str(seat), held, cap)
    return rack


def _restamp_public_arsenal(
    blob: MutableMapping[str, Any], seat: str, held: int, cap: int,
) -> None:
    """Make the clone's frozen station history agree with the new rack.

    A clone inherits ``station_obs_by_day`` from the season it was cut
    from, and every entry carries that season's public arsenal reading
    (§4.9.8). Arming used to write ``weapon_stock`` and leave those
    alone, so a lab run held two contradictory answers to "what is this
    seat carrying" — and which one the UI drew depended on the
    scrubber. Live, the station bar showed the rack; nudge the cursor
    and it fell back to the per-day snapshot and quietly drew the
    *source season's* rack instead. That failure is nastier than a
    blank bar, because the wrong number is a plausible one: a seat
    opened with one SNAP read as three pips of ordnance it did not
    have, on a board where the orbit panel was still offering the SNAP.

    Rewriting inherited history is not the liberty it sounds like. A
    clone is a throwaway with no past of its own — every day in here
    belongs to another game — and what the lab poses is "this seat is
    holding this rack, on this night". Making the scenery say so is
    what "opened armed" has to mean if the board is to be readable.
    Any consumer of the snapshots is fixed by this, not just the bar.
    """
    by_day = blob.get("station_obs_by_day")
    if not isinstance(by_day, MutableMapping):
        return
    for by_phase in by_day.values():
        if not isinstance(by_phase, MutableMapping):
            continue
        for by_seat in by_phase.values():
            if not isinstance(by_seat, MutableMapping):
                continue
            obs = by_seat.get(seat)
            if not isinstance(obs, MutableMapping):
                continue
            # Only where the season published an arsenal at all. A
            # season with weapons off has no ``arms`` key, and inventing
            # one would put ordnance on a board that cannot fire it.
            arms = obs.get("arms")
            if isinstance(arms, MutableMapping):
                arms["blue"] = int(held)
                arms["cap"] = int(cap)


def _give_blue(blob: MutableMapping[str, Any], seat: str, purity_total: int) -> None:
    """Fill the seat's hoard with BLUE parcels summing to ``purity_total``.

    Mirrors ``give_blue_purity`` in the eval builder, including the
    ``*_at_harvest`` keys — without those the parcels read as
    ``colour=EMPTY`` downstream and go uncounted, which looks exactly
    like arming silently not working.
    """
    from sea_of_colours.game.session import HOARD_CAPACITY
    from sea_of_colours.generator import Tile

    hoard = blob.setdefault("hoard_squares", {})
    parcels = list(hoard.get(seat) or [])
    remaining = max(0, int(purity_total))
    i = len(parcels)
    # A hoard that overflows its capacity is not a state the engine can
    # reach on its own, so don't hand it one; drop the excess instead.
    while remaining > 0 and len(parcels) < HOARD_CAPACITY:
        chunk = min(255, remaining)
        parcels.append({
            "site_id": f"blue-{seat}-lab-{i}",
            "origin_tile": int(Tile.BLUE),
            "origin_purity": chunk,
            "tile_at_harvest": int(Tile.BLUE),
            "purity_at_harvest": chunk,
        })
        remaining -= chunk
        i += 1
    hoard[seat] = parcels


def stock_of(blob: Any) -> dict[str, dict[str, int]]:
    """Every seat's actual ordnance, read off the session."""
    out: dict[str, dict[str, int]] = {}
    for seat, held in (blob.get("weapon_stock") or {}).items():
        if not isinstance(held, dict):
            continue
        counts = {k: int(held.get(k) or 0) for k in KINDS}
        if any(counts.values()):
            out[str(seat)] = counts
    return out


# ``disclose()`` lived here until v1.34, and its removal is the whole
# point of that change rather than a tidy-up.
#
# It existed because a rival's stock used to be private. V12 inferred it
# by watching that rival's blue-purity band drop between nights, which
# was useless here twice over: a frozen turn has no previous night to
# difference against, and a rack is granted rather than bought, so there
# was no spend to notice even if it had. You could hand a seat an EMP and
# every agent on the board would still read ``emp: none observed``. So
# the lab reached past the engine and seeded the estimator directly.
#
# That was mimicry, and it was the lab's least honest moment: agents in
# here were handed something no agent in a real season could get, which
# made "does arming change the play?" a question about the lab's own
# scaffolding as much as about the fork.
#
# Since v1.34 the engine broadcasts every seat's weaponised blue
# (RULEBOOK §4.9.8), computed from ``weapon_stock`` — the very field
# ``arm()`` above writes. A stamped rack and a bought one now produce an
# identical public reading, because nothing in the observation asks how
# the stock got there. The lab needs no special channel: it sets the
# state, and the ordinary percept carries it.


def stockable(blob: Any) -> set[str]:
    """The weapon kinds a given board's season actually prices (v1.36).

    Reads the board rather than today's constants, for the reason
    :func:`arm` does: a night frozen before SNAP existed has no SNAP in
    its economy.
    """
    from sea_of_colours.game.weapons import LEGACY_BLUE_COST_BY_KIND

    prices = (blob or {}).get("weapon_blue_costs") or LEGACY_BLUE_COST_BY_KIND
    return set(prices)


def catalogue(blob: Any = None) -> list[dict[str, Any]]:
    """The racks, for the launcher's pickers.

    Pass a board's session blob and each rack comes back marked with
    whether that board can hold it. Every shipped board predates SNAP,
    so without this the picker offers two racks (``snap``, ``arsenal``)
    that :func:`arm` then refuses — a menu whose items are traps. The
    rack list stays whole rather than being filtered, because "this
    board is too old for that weapon" is worth telling an attendee; a
    silently shorter menu on some boards than others just reads as a
    bug.
    """
    labels = {"emp": "EMP", "chaff": "chaff", "snap": "SNAP"}
    priced = stockable(blob) if blob is not None else None
    out = []
    for r in RACKS:
        needs = sorted(k for k, n in r.counts.items() if n)
        missing = sorted(set(needs) - priced) if priced is not None else []
        out.append({
            "id": r.id,
            **r.counts,
            "blue": r.blue,
            "note": r.note,
            "label": (
                "no weapons" if not any(r.counts.values())
                else " + ".join(
                    f"{n} {labels.get(kind, kind)}"
                    for kind, n in r.counts.items() if n
                )
            ),
            "available": not missing,
            "unavailable_because": (
                ""
                if not missing
                else "this board was frozen before "
                     + "/".join(labels.get(k, k) for k in missing)
                     + " existed, so its season cannot stock one"
            ),
        })
    return out
