"""weapon_forge — the machinery behind ``weapon_plays.py``. Do not edit.

Installed once into a fork by ``forge_install.py``, together with ~10 one-line
hooks in the existing modules. Everything a team changes lives in
``weapon_plays.py``; this file turns those declarations into options, wire
moves, doctrine, menu groups and replay tags.

The split is deliberate: ``weapon_plays.py`` is data a team owns, this is
machinery they should never have to read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

Cell = Tuple[int, int]


# ── the four axes a move is built from ─────────────────────────────────────
#
# WHEN — the board condition. Becomes the trigger predicate.
WHEN_CHOICES = (
    "always",          # every night we hold the weapon
    "redsign_mine",    # a pure WE found is live — we are defending it
    "redsign_theirs",  # a pure a RIVAL found is live — we are attacking it
    "no_redsign",      # NO pure is lit for anyone — an ordinary working night
    "other",           # custom predicate, supplied as `trigger`
)

# HOUR — which slot the weapon takes. Position in the move list IS the hour,
# so this is a hard constraint rather than a preference.
#
# Why it differs per weapon: an EMP cloud runs 8h and wants the night ahead of
# it, so past about H2 it has no night left to exploit. A chaff's 3h window has
# to land on the hour the rival was going to act. A SNAP lasts 1h and can be
# aimed at any beat.
HOUR_CHOICES: Dict[str, int] = {
    "super_early": 1,
    "early": 2,
    "mid": 8,
    "late": 16,
    "last_night": 1,   # hour is early; the GATE is the final day
}

# COMBINES_WITH — which existing play this borrows geometry from. This is what
# stops a weapon option being an orphan with coordinates nobody computed.
COMBINES_CHOICES = (
    "smash_grab",   # our own redsign wave-1 drop (CASE 1)
    "blind_grab",   # a rival's fogged seam, wave-1 drop + comb (CASE 2)
    "probe",        # pair with a probe placement
    "chain",        # pair with a juice chain on known red
    "standalone",   # denial only, banks nothing itself
)

# Engine facts. Wire verb and REPLAY tag are NOT always the same string:
# ``snap_launch`` on the wire arrives as ``snap`` in the replay frame, and a
# tag set keyed on the verb silently drops every frame — the agent then
# journals that the move never executed and corrupts the next night.
_WIRE_VERB = {"emp": "emp_launch", "chaff": "chaff_flare", "snap": "snap_launch"}
_FRAME_TAG = {"emp": "emp_launch", "chaff": "chaff_flare", "snap": "snap"}

# How many cells a launch aims at. Chaff aims at NOTHING — it takes no cell.
_AIMED = {"emp": 3, "snap": 1, "chaff": 0}

# Blue price, for the honest-arithmetic half of a rationale. Imported at use
# time from game.weapons so a fork never carries a stale literal.
_FALLBACK_BLUE = {"emp": 200, "chaff": 300, "snap": 100}


def _blue_cost(weapon: str) -> int:
    try:
        from sea_of_colours.game import weapons as _w
        return int(_w.BLUE_COST_BY_KIND.get(weapon, _FALLBACK_BLUE[weapon]))
    except Exception:
        return _FALLBACK_BLUE.get(weapon, 0)


@dataclass(frozen=True)
class WeaponPlay:
    """One named weapon move.

    ``play_id`` is public — it reaches the model in the prompt, the game log as
    the night resolves, the lab's frozen-turn journals and the season cards. Be
    as silly as you like about the TONE and never about the CONTENT: a cautious
    vision move called ``NUKE`` has told the model that option is aggressive,
    and that is a bug you will not find.
    """

    play_id: str
    weapon: str            # "snap" | "emp" | "chaff"

    when: str              # one of WHEN_CHOICES
    hour: str              # one of HOUR_CHOICES
    combines_with: str     # one of COMBINES_CHOICES

    #: The team's own one sentence: what firing this BUYS. Kept apart from
    #: ``rationale`` on purpose. This is INTENT and the team owns it; the
    #: rationale is ARGUMENT, composed from this plus mechanics plus the
    #: alternative it competes against — and that last part depends on what
    #: else is on tonight's menu, which nobody can know up front.
    why: str

    title: str = ""        # generated when empty
    rationale: str = ""    # generated when empty
    trigger: Optional[Callable[[Mapping[str, Any]], Sequence[Cell]]] = None
    menu_rank: int = 0

    #: Where the aim points come from.
    #:   "pattern"      — borrow the seam pattern's wave-1 drop (default)
    #:   "rival_probes" — the freshest rival probes, via scorch.probe_targets
    #:   "redsign"      — the rival seam, via scorch.redsign_targets
    targets: str = "pattern"

    #: How many steps the follow-up walk may take. The salvo, an optional
    #: probe, the drop, the steps and the pickup all share MAX_MOVES = 21.
    comb_max_steps: int = 6

    #: Launch a probe beside the landing so the walk is lit. Placed ADJACENT to
    #: the drop, never on it — landing a harvester on your own probe crushes it.
    probe_the_comb: bool = False

    #: Refuse to fire below this many real aim points. A charge fires all its
    #: missiles whether or not you aimed them, so on a thin board the spare
    #: ones widen a cloud around nothing — 200 blue for a cloud over one
    #: expiring eye is worse than holding the charge.
    min_targets: int = 1

    @property
    def kind(self) -> str:
        return self.weapon

    @property
    def wire_verb(self) -> str:
        return _WIRE_VERB[self.weapon]

    @property
    def frame_tag(self) -> str:
        return _FRAME_TAG[self.weapon]

    @property
    def at_hour(self) -> int:
        return HOUR_CHOICES.get(self.hour, 1)

    @property
    def aims_at_cells(self) -> int:
        return _AIMED.get(self.weapon, 0)

    def doctrine_const(self) -> str:
        return f"DOCTRINE_{self.play_id}"

    def validate(self) -> List[str]:
        """Problems a team can fix, in plain words. Empty list means fine."""
        bad: List[str] = []
        if self.weapon not in _WIRE_VERB:
            bad.append(f"{self.play_id}: weapon must be snap, emp or chaff")
        if self.when not in WHEN_CHOICES:
            bad.append(f"{self.play_id}: when must be one of {WHEN_CHOICES}")
        if self.when == "other" and self.trigger is None:
            bad.append(f"{self.play_id}: when='other' needs a trigger function")
        if self.hour not in HOUR_CHOICES:
            bad.append(f"{self.play_id}: hour must be one of {tuple(HOUR_CHOICES)}")
        if self.combines_with not in COMBINES_CHOICES:
            bad.append(
                f"{self.play_id}: combines_with must be one of {COMBINES_CHOICES}"
            )
        if not self.why.strip():
            bad.append(
                f"{self.play_id}: needs a `why` — one sentence on what firing "
                "it buys. Without it the rationale cannot be composed, and an "
                "option that cannot argue for itself does not get picked."
            )
        if self.weapon == "emp" and self.hour in ("mid", "late"):
            bad.append(
                f"{self.play_id}: an 8h EMP cloud fired at '{self.hour}' has "
                "no night left to exploit — use super_early or early"
            )
        return bad


# ── rationale composition ──────────────────────────────────────────────────
#
# The single highest-value habit in the harness: an option that only praises
# itself is competing badly, because the model is choosing BETWEEN options. So
# every generated rationale has four parts, in this order:
#
#   1. the team's WHY, verbatim — their read, in their words
#   2. honest arithmetic — cost, duration, what is actually caught
#   3. the ALTERNATIVE it beats, named
#   4. the honest COST, and when NOT to pick it
#
# Part 4 matters more than it looks. A rationale that hides its downside reads
# as a sales pitch and the model discounts the whole thing.

_MECHANICS = {
    "chaff": (
        "It is not aimed at a cell — it is aimed at an HOUR. At the hour it "
        "resolves, every OTHER seat's action that hour is cancelled: drops, "
        "steps, pickups, probes, even their own launches. The window runs 3h, "
        "so there is no re-try for them inside it."
    ),
    "emp": (
        "One launch fires up to 3 missiles at once, each darkening a radius-2 "
        "diamond of 13 cells for 8 hours. Probes inside die; harvesters are "
        "disabled hour by hour. They cannot drop into cells they cannot see, "
        "so a landing under the cloud is refused rather than delayed."
    ),
    "snap": (
        "One cell, one hour. It kills a probe on that cell and damages a "
        "harvester on it — including one that ARRIVES during the hour, which "
        "is what lets it guard a square rather than merely punish one. It "
        "resolves above the hour-start vision snapshot, so the landing it "
        "denies is refused tonight, not delayed."
    ),
}

# The honest downside per weapon. Stated, never hidden.
_COSTS = {
    "chaff": (
        "The jam is SYMMETRIC after the launch hour: you are immune only at "
        "the hour you fire, because that slot was spent firing. The next two "
        "hours cancel YOUR moves too, so the walk starts three hours later "
        "than the launch — never one. Do not pick this if you had a rich walk "
        "queued in those hours."
    ),
    "emp": (
        "Friendly fire is ON — your own units in the cloud are not immune. "
        "The salvo costs an hour-slot a harvester did not walk, and the cloud "
        "outlives most of the night, so ground you darken is ground you also "
        "cannot use unless you planned a hole to stand in."
    ),
    "snap": (
        "It removes exactly one cell for one hour. If they can reach the "
        "target from a second eye, or land from a different bearing, this buys "
        "a beat and nothing more."
    ),
}

# What each COMBINES_WITH pairing competes against on the menu.
#
# V12 ships EIGHTEEN named seam patterns plus eight numbered families, and which
# of them appears depends on the night. So a rationale that hardcodes "COMPARE:
# BLIND_GRAB ..." names a move that is often not on the menu — the exact desync
# the guide warns about ("you have told the model to pick a move that is not on
# the menu; it will either ignore the advice or invent the id").
#
# Instead: list the ids this pairing might really compete with, in preference
# order, and describe whichever one is ACTUALLY present tonight.
_RIVAL_IDS = {
    "blind_grab": ("BLIND_GRAB", "BLIND_AND_GRAB", "UNBEATEN_FLANK",
                   "CONTEST_DENY", "WALKIN_GRAB", "WALK_IN"),
    "smash_grab": ("SMASH_GRAB", "SMASH_GRAB_VALUE", "SEEN_GRAB",
                   "SECURE_MASS", "FULL_SWEEP"),
    "probe":      ("PR", "PRSNAP"),
    "chain":      ("CH", "GRAB"),
    "standalone": ("GRAB", "CH", "SMASH_GRAB", "BLIND_GRAB"),
}

# How to describe each one once we know it is on the menu. Keyed by the id
# itself, because what it TRADES differs: a supersede spends a probe, a chain
# banks certain points, a flank commits a second unit.
_RIVAL_PROSE = {
    "BLIND_GRAB": "spends a probe to blind the finder — cheaper, but it removes "
                  "one eye and they can still act from another",
    "BLIND_AND_GRAB": "blinds the finder and combs the WHOLE seam — richer than "
                      "us if the seam is fat, and it banks tonight",
    "UNBEATEN_FLANK": "commits a SECOND harvester on the opposite bearing, so it "
                      "works ground we are not touching",
    "CONTEST_DENY": "confirms and denies without landing — certain, and it keeps "
                    "the pure for tomorrow instead of taking it now",
    "WALKIN_GRAB": "walks in on foot for free, spending no probe at all",
    "WALK_IN": "reaches the seam on foot for free — no probe, no charge",
    "SMASH_GRAB": "just takes the pure and leaves their tempo untouched",
    "SMASH_GRAB_VALUE": "takes the richest cell of the seam and banks it now",
    "SEEN_GRAB": "takes a pure we can already SEE — no guessing",
    "SECURE_MASS": "banks the mass halo with a covering probe, lower ceiling and "
                   "near-certain",
    "FULL_SWEEP": "combs the whole seam with one unit",
    "PR": "a bare probe buys vision and banks nothing tonight",
    "PRSNAP": "insures a landing for one probe, but protects our grab rather "
              "than costing them anything",
    "CH": "a juice chain banks certain points off red we already see, and leaves "
          "their night alone",
    "GRAB": "a visible pure/mass grab banks the most certain points on the menu",
}


def _resolve_rival(p: "WeaponPlay", present: Sequence[str]) -> str:
    """Name a competitor that is REALLY on tonight's menu, and say what it trades.

    ``present`` is the option ids already in the registry. Numbered families are
    matched by prefix, since the actual id is GRAB1 / CH2 / PR3.
    """
    ids = list(present or ())
    for want in _RIVAL_IDS.get(p.combines_with, ()):
        for have in ids:
            if have == want or (want in ("PR", "PRSNAP", "CH", "GRAB", "BL")
                                and have.startswith(want)
                                and have[len(want):].isdigit()):
                prose = _RIVAL_PROSE.get(want, "")
                return f"{have} {prose}" if prose else have
    # Nothing recognised on the menu: argue against the class rather than an id
    # we cannot see. Never invent an id — the model picks moves by id.
    return ("the alternative is always a harvest chain: certain points now, "
            "against tempo taken off them")


_WHEN_PROSE = {
    "always": "any night we hold one",
    "redsign_mine": "a pure WE found is live and we are defending it",
    "redsign_theirs": "a pure a RIVAL found is live and we are taking it",
    "other": "the board condition this play was built for",
}


def compose_rationale(p: "WeaponPlay",
                      present: Sequence[str] = ()) -> str:
    """Build the argument the model reads. Overridden by ``p.rationale``.

    ``present`` is tonight's real option ids, so the COMPARE clause names a move
    that is actually on the menu rather than one we guessed at.
    """
    if p.rationale.strip():
        return p.rationale
    cost = _blue_cost(p.weapon)
    return " ".join([
        f"WHY: {p.why.strip().rstrip('.')}.",
        _MECHANICS.get(p.weapon, ""),
        f"Costs {cost} blue and one hour-slot at hour {p.at_hour}.",
        f"COMPARE: {_resolve_rival(p, present)}.",
        f"THE COST IS REAL: {_COSTS.get(p.weapon, '')}",
    ]).strip()


def compose_title(p: "WeaponPlay", target: Optional[Cell] = None) -> str:
    """One line, leading with the OUTCOME rather than the coordinates."""
    if p.title.strip():
        return p.title
    where = f" at ({target[0]},{target[1]})" if target else ""
    head = p.why.strip().rstrip(".")
    if len(head) > 74:
        head = head[:71].rsplit(" ", 1)[0] + "..."
    return f"{p.play_id} — {head}{where}"


# ── triggers ───────────────────────────────────────────────────────────────
def _stock(agent_view: Mapping[str, Any], weapon: str) -> int:
    orbit = agent_view.get("orbit") or {}
    st = orbit.get("weapon_stock") or {}
    try:
        return int(st.get(weapon) or 0)
    except (TypeError, ValueError):
        return 0


def _pattern_targets(p: "WeaponPlay", pattern: Any) -> Tuple[Optional[Cell], List[Cell]]:
    """Borrow the drop cell + comb path from the seam pattern we combine with.

    Returning ``(None, [])`` is a real answer: an option that lies about its
    own geometry is worse than no option.
    """
    waves = list(getattr(pattern, "waves", ()) or ())
    if not waves:
        return None, []
    w0 = waves[0]
    if getattr(w0, "deny_only", False):
        return None, []
    drop = getattr(w0, "drop_at", None)
    comb = [tuple(c) for c in (getattr(w0, "comb_path", None) or [])]
    return (tuple(drop) if drop else None), comb


def _redsign_state(agent_view: Mapping[str, Any],
                   seam_patterns: Sequence[Any]) -> str:
    """"mine" | "theirs" | "none" — whose pure, if any, is lit tonight.

    Reads the PATTERNS first because they carry engine-truth ownership on
    ``mine``; falls back to the raw view so "no redsign" is still answerable on
    a board where no pattern was built.
    """
    for pat in seam_patterns or []:
        if getattr(pat, "mine", None):
            return "mine"
    for pat in seam_patterns or []:
        if getattr(pat, "waves", None) is not None:
            return "theirs"
    rows = agent_view.get("redsign") or []
    if not rows:
        return "none"
    for r in rows:
        if isinstance(r, Mapping) and r.get("mine"):
            return "mine"
    return "theirs"


def _when_holds(p: "WeaponPlay", state: str) -> bool:
    """Does this play's WHEN match tonight's redsign state?"""
    if p.when == "always":
        return True
    if p.when == "redsign_mine":
        return state == "mine"
    if p.when == "redsign_theirs":
        return state == "theirs"
    if p.when == "no_redsign":
        return state == "none"
    return True  # "other" is decided by p.trigger


def _aim_points(
    p: "WeaponPlay",
    agent_view: Mapping[str, Any],
    seam_patterns: Sequence[Any],
) -> Tuple[List[Cell], List[Cell], List[str]]:
    """(aim cells, comb path, notes) for a play. Empty aim means do not offer.

    ``scorch`` is imported lazily and optionally: a fork that never needed EMP
    geometry does not carry it, and a missing module should degrade to the
    pattern path rather than crash the night.
    """
    notes: List[str] = []

    if p.targets in ("rival_probes", "redsign"):
        try:
            from . import scorch
        except ImportError:
            notes.append(
                f"{p.play_id}: needs scorch.py for targets={p.targets!r} and it "
                "is not in this fork"
            )
            return [], [], notes
        missiles = max(1, p.aims_at_cells)
        if p.targets == "rival_probes":
            rows = agent_view.get("enemy_probes") or agent_view.get("rival_probes") or []
            real = [r for r in rows if isinstance(r, Mapping)]
            # Count REAL eyes before the salvo pads itself out. `probe_targets`
            # always returns `missiles` cells, so counting its output would make
            # min_targets meaningless.
            if len(real) < max(1, p.min_targets):
                notes.append(
                    f"{p.play_id}: {len(real)} rival probe(s), needs "
                    f"{p.min_targets} — holding the charge"
                )
                return [], [], notes
            aim, why = scorch.probe_targets(agent_view, real, missiles=missiles)
            return list(aim), [], notes + list(why)
        # scorch.redsign_targets reads the rival smear off the view itself and
        # REFUSES an own redsign outright ("scorching a redsign you found
        # yourself denies your own harvesters the ground"), so the
        # redsign_mine = do-not-offer rule is enforced upstream of us.
        try:
            aim, region, why = scorch.redsign_targets(
                agent_view, radius=scorch.RADIUS, missiles=missiles)
        except Exception as exc:                        # noqa: BLE001
            notes.append(f"{p.play_id}: redsign_targets refused ({exc})")
            return [], [], notes
        notes.extend(list(why or []))
        if not aim:
            return [], [], notes
        # The comb is the smear MINUS our own blast: ground worth walking that
        # our charge did not just darken. Walking into our own cloud disables
        # the harvester hour by hour — friendly fire is on.
        dark = scorch.blast(aim, scorch.RADIUS)
        smear = [c for c in (scorch._cell(c) for c in
                             ((region or {}).get("cells") or [])) if c]
        # A filtered SET of walkable cells is not a PATH — plan_comb turns it
        # into a contiguous serpentine that never enters our own cloud, and
        # trims it to the move cap.
        plan = plan_comb(agent_view, value_cells=smear, avoid=dark,
                         max_steps=p.comb_max_steps)
        notes.extend(plan["notes"])
        drop = plan["drop_at"]
        if drop is None:
            return [], [], notes
        comb = [drop] + list(plan["walk"])
        return list(aim), comb, notes

    # default: borrow the pattern's wave-1 geometry
    for pat in seam_patterns or []:
        if _when_holds(p, _redsign_state(agent_view, [pat])):
            target, comb = _pattern_targets(p, pat)
            if target is not None:
                return [target], comb, notes
    return [], [], notes


# ── option building (the agency.py hook calls this) ────────────────────────
def build_options(
    agent_view: Mapping[str, Any],
    seam_patterns: Sequence[Any] = (),
    *,
    option_cls: Any = None,
    day: int = 0,
    day_cap: int = 7,
    present: Sequence[str] = (),
) -> "Dict[str, Any]":
    """Turn ``PLAYS`` into ``{option_id: Option}`` for tonight's menu.

    ``option_cls`` is ``agency.Option``, passed in by the hook so this module
    never imports the harness back (which would be a cycle).
    """
    from . import weapon_plays  # the team's declarations

    out: "Dict[str, Any]" = {}
    if option_cls is None:
        return out

    state = _redsign_state(agent_view, seam_patterns)
    # Real ids on tonight's menu: whatever the caller already registered, plus
    # the seam patterns about to become options.
    menu_ids = list(present or ()) + [
        str(getattr(pat, "pattern_id", "")) for pat in (seam_patterns or [])
    ]

    for p in sorted(weapon_plays.PLAYS, key=lambda q: q.menu_rank):
        if _stock(agent_view, p.weapon) <= 0:
            continue
        if p.hour == "last_night" and int(day) < int(day_cap):
            continue

        aim: List[Cell] = []
        comb: List[Cell] = []
        probe_at: Optional[Cell] = None

        if p.when == "other" and p.trigger is not None:
            aim = list(p.trigger(agent_view) or ())
        else:
            if not _when_holds(p, state):
                continue
            aim, comb, _notes = _aim_points(p, agent_view, seam_patterns)
            if p.probe_the_comb and comb:
                _pl = plan_comb(
                    agent_view, value_cells=comb,
                    avoid=_own_blast(p, aim), max_steps=p.comb_max_steps)
                probe_at = _pl["probe_at"]

        # Chaff aims at nothing, so an empty aim list is not a blocker for it.
        if p.aims_at_cells > 0 and not aim:
            continue

        target = aim[0] if aim else None
        out[p.play_id] = option_cls(
            option_id=p.play_id,
            kind=p.kind,
            title=compose_title(p, target),
            detail=_compose_detail(p, target, comb),
            execute_lines=_execute_lines(p, aim, comb, probe_at),
            payload={
                "shape": "occupy" if comb else "deny",
                "play_id": p.play_id,
                "weapon": p.weapon,
                "at_hour": p.at_hour,
                "target": list(target) if target else None,
                "aim": [list(c) for c in aim],
                "drop_at": list(comb[0]) if comb else None,
                "comb": [list(c) for c in comb],
                "probe_at": list(probe_at) if probe_at else None,
                # The packager cannot read the view (``_Packer`` keeps
                # harvesters and probe budget, not agent_view), so the rack
                # count travels with the option. Same night, same number.
                "stock": _stock(agent_view, p.weapon),
                "aims_at_cells": p.aims_at_cells,
            },
            rationale=compose_rationale(p, menu_ids),
        )
    return out


def _compose_detail(p: "WeaponPlay", target: Optional[Cell], comb: Sequence[Cell]) -> str:
    cost = _blue_cost(p.weapon)
    bits = [f"Fire at hour {p.at_hour}."]
    if p.aims_at_cells == 0:
        bits.append("No target cell — this is aimed at an HOUR, not a place.")
    elif target:
        bits.append(f"Target ({target[0]},{target[1]}).")
    bits.append(f"Costs {cost} blue and one hour-slot.")
    if comb:
        bits.append(f"Then land and comb {len(comb)} cell(s).")
    return " ".join(bits)


def _execute_lines(p: "WeaponPlay", aim: Sequence[Cell],
                   comb: Sequence[Cell],
                   probe_at: Optional[Cell] = None) -> List[str]:
    oid = p.play_id
    lines: List[str] = []
    if p.aims_at_cells == 0:
        lines.append(f"{oid}: {p.wire_verb} (ONE move, hour {p.at_hour}, NO target cell)")
    elif p.aims_at_cells == 1 and aim:
        lines.append(f"{oid}: {p.wire_verb} at {list(aim[0])} (hour {p.at_hour})")
    elif aim:
        # A salvo is ONE move and ONE charge — all cells in a single `at` list,
        # never one move per missile.
        lines.append(
            f"{oid}: {p.wire_verb} at {[list(c) for c in aim]} "
            f"(ONE move, ONE charge, hour {p.at_hour})"
        )
    if probe_at:
        lines.append(
            f"{oid}: probe at {list(probe_at)} (just OUTSIDE the cloud, "
            "beside the landing — not on it, or the drop crushes it)"
        )
    if comb:
        lines.append(f"{oid}: drop a harvester at {list(comb[0])}")
        if len(comb) > 1:
            lines.append(
                f"{oid}: walk {[list(c) for c in comb[1:]]} and pick up "
                "(all OUTSIDE your own cloud)"
            )
    return lines

# ── combs — a reusable walk planner ────────────────────────────────────────
#
# COMBS COME UP CONSTANTLY, so this is a first-class capability rather than a
# per-play hack. Any weapon that darkens or denies ground and then wants to WORK
# the ground it did not ruin needs the same three things: a legal contiguous
# walk, a start cell outside its own blast, and optionally a probe to light the
# walk.
#
# The trap it exists to avoid, learned the hard way: a filtered SET of walkable
# cells is not a PATH. Feeding `smear minus blast` straight to `emit_chain`
# produced a walk that backtracked ((19,19) -> (19,18) -> (19,19)) and ran to 27
# moves against a MAX_MOVES cap of 21. `comb_shapes.comb_path` is the harness's
# own answer — contiguous, never revisits, and it takes a `bad` set, which is
# precisely where our own cloud belongs.


def plan_comb(
    agent_view: Mapping[str, Any],
    *,
    value_cells: Sequence[Cell],
    avoid: Sequence[Cell] = (),
    max_steps: int = 6,
) -> Dict[str, Any]:
    """Plan a legal walk over ``value_cells`` that never enters ``avoid``.

    Returns ``{"probe_at", "drop_at", "walk", "notes"}``. ``drop_at`` is None
    when no legal start exists, which is a real answer — an option that claims a
    walk it cannot make is worse than an option with no walk.

    ``avoid`` is normally our OWN blast. Friendly fire is on: a harvester inside
    a cloud is disabled hour by hour, so the walk has to stay out of ground we
    just darkened, and the probe has to sit outside it too or it is swept.
    """
    notes: List[str] = []
    bad = {tuple(c) for c in (avoid or ())}
    want = [tuple(c) for c in (value_cells or ()) if tuple(c) not in bad]
    if not want:
        notes.append("no value cells survive outside our own blast")
        return {"probe_at": None, "drop_at": None, "walk": [], "notes": notes}

    w, h = _grid_bounds(agent_view)

    # Start on the cell with the most surviving neighbours — the thickest part
    # of what is left, so the serpentine has somewhere to go.
    def _neighbours(c: Cell) -> int:
        x, y = c
        return sum(1 for n in ((x+1, y), (x-1, y), (x, y+1), (x, y-1))
                   if n in set(want))

    start = max(want, key=_neighbours)

    try:
        from . import comb_shapes
        raw = comb_shapes.comb_path(
            start[0], start[1], start, w, h, bad, want, max_steps=max_steps,
        )
        walk = [tuple(int(v) for v in c) for c in (raw or [])]
    except Exception as exc:                            # noqa: BLE001
        notes.append(f"comb_path unavailable ({exc}); falling back to the start cell")
        walk = [start]

    # comb_path may include the start; normalise so walk[0] IS the drop cell.
    if not walk:
        walk = [start]
    if walk[0] != start:
        walk = [start] + [c for c in walk if c != start]

    # Hard cap. One move per step plus the drop and the pickup, inside
    # MAX_MOVES(21) shared with the salvo and any probe.
    if len(walk) > max_steps + 1:
        walk = walk[: max_steps + 1]
        notes.append(f"walk trimmed to {len(walk)} cell(s) to stay inside the move cap")

    inside = [c for c in walk if c in bad]
    if inside:
        walk = [c for c in walk if c not in bad]
        notes.append(f"dropped {len(inside)} cell(s) that sat inside our own cloud")

    probe_at = _probe_outside(walk, bad, w, h) if walk else None
    notes.append(
        f"{len(walk)} cell(s) walkable outside our own cloud, "
        f"starting {walk[0] if walk else None}"
    )
    return {
        "probe_at": probe_at,
        "drop_at": walk[0] if walk else None,
        "walk": walk[1:] if len(walk) > 1 else [],
        "notes": notes,
    }


def _probe_outside(walk: Sequence[Cell], bad: set,
                   w: int, h: int) -> Optional[Cell]:
    """A probe cell that lights the walk without sitting in our own cloud.

    Placed adjacent to the drop rather than ON it: landing a harvester on your
    own probe CRUSHES the probe, so the disk would go dark the moment the walk
    begins.
    """
    if not walk:
        return None
    x, y = walk[0]
    on_walk = set(walk)
    for cand in ((x+1, y), (x-1, y), (x, y+1), (x, y-1),
                 (x+1, y+1), (x-1, y-1), (x+1, y-1), (x-1, y+1)):
        if cand in bad or cand in on_walk:
            continue
        if 0 <= cand[0] < w and 0 <= cand[1] < h:
            return cand
    return None


def _own_blast(p: "WeaponPlay", aim: Sequence[Cell]) -> List[Cell]:
    """Cells our own charge darkens. Empty for a weapon with no radius."""
    if not aim or p.weapon != "emp":
        return []
    try:
        from . import scorch
        return list(scorch.blast([tuple(c) for c in aim], scorch.RADIUS))
    except Exception:                                   # noqa: BLE001
        return []


def _grid_bounds(agent_view: Mapping[str, Any]) -> Tuple[int, int]:
    grid = agent_view.get("grid") or {}
    try:
        return int(grid.get("width") or 32), int(grid.get("height") or 32)
    except (TypeError, ValueError):
        return 32, 32


# ── packing (the packager.py hook registers this) ──────────────────────────
def _pack_weapon(pk: Any, payload: Mapping[str, Any]) -> None:
    """Compile a declared weapon play into wire moves.

    Position in ``pk.moves`` IS the hour, which drives everything below.
    """
    try:
        stock = int(payload.get("stock") or 0)
    except (TypeError, ValueError):
        stock = 0
    play = str(payload.get("play_id") or "weapon play")
    if stock <= 0:
        pk.log.append(f"cut {play}: none of that weapon in the rack")
        return

    weapon = str(payload.get("weapon") or "")
    verb = _WIRE_VERB.get(weapon)
    if verb is None:
        pk.log.append(f"cut {play}: unknown weapon {weapon!r}")
        return

    want_hour = int(payload.get("at_hour") or 1)
    if len(pk.moves) + 1 > want_hour:
        pk.log.append(
            f"cut {play}: wanted hour {want_hour} and {len(pk.moves)} move(s) "
            "are already queued — firing late hits an hour nobody was using"
        )
        return

    mark = pk.begin()
    # Pad to the requested hour so the launch lands on the beat it was
    # designed for. `wait` is a legal move (game.policy.MoveTag).
    while len(pk.moves) + 1 < want_hour:
        pk.moves.append({"a": "wait"})

    aims = int(payload.get("aims_at_cells") or 0)
    target = payload.get("target")
    if aims == 0:
        pk.moves.append({"a": verb})               # chaff: no `at`, by design
    elif aims == 1 and target:
        pk.moves.append({"a": verb, "at": [int(target[0]), int(target[1])]})
    elif target:
        # A salvo is ONE move with a LIST of cells — not one move per missile,
        # and not one charge per missile either. Use the full aim list when the
        # option carried one.
        cells = [list(map(int, c)) for c in (payload.get("aim") or [])]
        pk.moves.append({"a": verb,
                         "at": cells or [[int(target[0]), int(target[1])]]})
    else:
        pk.rollback(mark)
        pk.log.append(f"cut {play}: needs a target cell and had none")
        return

    # Chaff jams its own house for the carry-over hours (simulator: "firing it
    # costs the launcher the full duration window"), so hold those hours rather
    # than queueing moves that would simply be cancelled — which would read on
    # the card as the play failing when it was really a mis-plan.
    if weapon == "chaff":
        pk.moves.append({"a": "wait"})
        pk.moves.append({"a": "wait"})

    comb = [tuple(c) for c in (payload.get("comb") or [])]
    drop_at = payload.get("drop_at")
    if not comb or not drop_at:
        return                                     # denial-only play: done

    unit = pk.next_harvester()
    if unit is None:
        pk.rollback(mark)
        pk.log.append(f"cut {play}: no harvester left to take the ground")
        return

    # The probe goes FIRST so its disk is open before the harvester lands —
    # a probe launched after the drop lights ground already walked.
    probe_at = payload.get("probe_at")
    if probe_at is not None and not pk.spend_probe(probe_at):
        pk.log.append(
            f"{play}: no probe stock for the covering probe — walking blind"
        )
    if not pk.emit_chain(unit, drop_at, comb):
        pk.rollback(mark)
        pk.log.append(
            f"cut {play}: the weapon would have fired but the landing did not "
            "compile — denying their hour and banking nothing is not the play"
        )


def packers() -> Dict[str, Any]:
    """``{kind: packer}`` for every weapon a team declared."""
    from . import weapon_plays
    return {p.kind: _pack_weapon for p in weapon_plays.PLAYS}


# ── menu groups (the agency.py hooks call these) ───────────────────────────
_HEADER_TEXT = {
    "chaff": "CHAFF PLAYS (spend a chaff to CANCEL an hour for every other seat — aimed at an HOUR, not a cell)",
    "emp":   "EMP PLAYS (spend a charge to darken ground for 8h — probes inside die, landings under it are refused)",
    "snap":  "SNAP PLAYS (one cell, one hour — kills the eye on it, and catches a harvester that ARRIVES that hour)",
}

# NOTE the deliberate omission. The reference fork's emp blurb ends "and never
# over a CERTAIN pure/mass grab", which teaches the model to defer to grabs
# unconditionally — and it then refused a weapon 3/3 with the option in the
# prompt five times. State the trade honestly and let the rationale argue it;
# do not pre-lose the argument in the group header.
_BLURB_TEXT = {
    "chaff": "delete an HOUR for every other seat — their drops, steps, pickups and probes at that hour are cancelled, and the 3h window leaves no re-try. Banks nothing itself, and the jam is symmetric after the launch hour, so it earns its price when the hour you delete is worth more to THEM than the next two are to you.",
    "emp":   "buy TEMPO by darkening ground: probes in the cloud die, harvesters are disabled, and a rival cannot drop into cells it cannot see for 8 hours. Banks nothing tonight and costs an hour a harvester did not walk — weigh it against the chain you drop for it.",
    "snap":  "the cheapest interdiction in the game — one cell, one hour, 100 blue. It kills the eye lighting their best target and catches a harvester that arrives that hour, so it can guard a square as well as punish one.",
}


def headers() -> List[Tuple[str, str]]:
    from . import weapon_plays
    seen: List[str] = []
    for p in sorted(weapon_plays.PLAYS, key=lambda q: q.menu_rank):
        if p.kind not in seen:
            seen.append(p.kind)
    return [(k, _HEADER_TEXT.get(k, f"{k.upper()} PLAYS")) for k in seen]


def blurbs() -> Dict[str, str]:
    from . import weapon_plays
    return {
        p.kind: _BLURB_TEXT.get(p.kind, "")
        for p in weapon_plays.PLAYS
        if _BLURB_TEXT.get(p.kind)
    }


def extend_headers(existing: Sequence[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Put weapon groups FIRST — this list's order is the menu's group order."""
    ours = headers()
    have = {k for k, _ in ours}
    return ours + [(k, h) for k, h in existing if k not in have]


# ── replay tags (the last_night.py hooks call these) ───────────────────────
def frame_tags() -> set:
    """Own-action tags, so a fired weapon is VISIBLE in the seat's own log.

    Without this the weapon resolves in the engine and the hour goes missing
    from the EXECUTION LOG; the agent then journals that it never executed and
    poisons the next night's reasoning.
    """
    from . import weapon_plays
    out = set()
    for p in weapon_plays.PLAYS:
        out.add(p.frame_tag)
    # Interdiction outcomes on our OWN units, for the same reason as ``empd``.
    out |= {"snapped", "chaffed", "empd"}
    return out


def public_tags() -> set:
    """Tags that are PUBLIC when a rival does them (RULEBOOK §5.1 / §4.9.4)."""
    from . import weapon_plays
    return {p.frame_tag for p in weapon_plays.PLAYS}


def captions() -> Dict[str, str]:
    return {
        "emp_launch": "fired an EMP salvo",
        "chaff_flare": "flared chaff",
        "snap": "fired a SNAP round",
    }


# ── schema (the chat_schema.py hook calls this) ────────────────────────────
def widen_schema(v7_move_item: Mapping[str, Any]) -> Dict[str, Any]:
    """Add every declared weapon's verb to the strict move enum.

    An ``enum`` in a structured-output schema is a HARD WALL: without the verb
    the model physically cannot emit the move, and it fails with no error — the
    play simply never appears. This governs the LLM MOVER, which runs on
    FALLBACK nights; the normal path compiles off the option menu and never
    touches it. Both have to know the verb.
    """
    from . import weapon_plays
    verbs = sorted({p.wire_verb for p in weapon_plays.PLAYS})
    props = dict(v7_move_item.get("properties") or {})
    props["a"] = {
        "type": "string",
        "enum": ["drop", "step", "pickup", "probe"] + verbs,
    }
    # A salvo's `at` is a LIST of cells where every other verb's is one cell;
    # `_CELL` is `array of integer` and rejects the nested form.
    if any(p.aims_at_cells > 1 for p in weapon_plays.PLAYS):
        props["at"] = {"type": "array"}
    return {**v7_move_item, "properties": props}


# ── doctrine ───────────────────────────────────────────────────────────────
#
# Two rules learned the hard way.
#
# LENGTH. The reference fork ships DOCTRINE_SCORCH at 147 lines and
# DOCTRINE_SNAP at 78. A fork that added ~140 lines of weapon doctrine to this
# baseline lost 7-2 across nine seeds (-1035 points per season) with zero
# fallbacks, so it was decision quality, not a bug. The model mirrors the style
# of what it reads: long analytical input buys diffuse choices. These blocks are
# deliberately ~20 lines each.
#
# CONFLICT. V12's own BEWARE_* blocks describe every weapon from the SURVIVOR's
# side, and that framing actively suppresses offensive use. The worst case is
# measurable: DOCTRINE_BEWARE_CHAFF says chaff is "blind-fired at the hours
# opponents most naturally pick up", tells the seat to avoid hours 6-9 and
# 12-16, and to "prefer pickup at hour <= 4". So H1 sits inside its SAFE zone.
# An agent holding a chaff then refuses to fire it at H1 with the reasoning
# "their H1 drop still lands" — which is the model applying that doctrine
# CORRECTLY to a case where it is wrong. Adding offensive doctrine alongside an
# uncorrected BEWARE_ block leaves two frames and the incumbent wins, so every
# weapon here ships a CORRECTION as well as an addition.

_DOCTRINE = {
    "chaff": """\
CHAFF — CANCEL THE MOVE, NOT THE PLACE.
SPEND A CHAFF when you hold one and an hour of theirs is worth more than
two of yours.
A chaff is not aimed at a cell. It is aimed at an HOUR: at the hour it
resolves, every OTHER seat's action that hour is cancelled — their drop,
their step, their pickup, their probe. The window runs 3 hours, so there
is no re-try for them inside it.
THE COST IS SYMMETRIC. You are immune only at the hour you launch,
because that slot was spent launching. The next two hours cancel YOUR
moves too. Plan the walk three hours after the flare, never one — a move
ordered inside your own window is simply cancelled, and the night then
reads as a failure that was really a mis-plan.
WHEN IT IS WRONG: if you had a rich walk queued in those hours, you are
paying 300 blue and two hours to deny one. Take the grab instead.
""",
    "emp": """\
EMP — AREA DENIAL YOU CAN STAND IN.
SPEND A CHARGE early or not at all: the cloud runs 8 hours and wants the
night ahead of it, so past hour 2 there is no night left to exploit.
One launch fires up to 3 missiles as ONE move and ONE charge — each
darkens a radius-2 diamond of 13 cells. Probes inside are destroyed;
harvesters inside are disabled hour by hour. A rival cannot drop into
cells it cannot see, so a landing under the cloud is REFUSED, not
delayed.
A cell is only darkened when a missile lands within its radius, and you
choose where they land — so aim at least one cell further out than the
blast reaches and the middle stays clear. That hole is a place only you
can use for the cloud's whole life.
FRIENDLY FIRE IS ON. Your own units in the cloud are not immune.
WHEN IT IS WRONG: darkening ground you wanted to walk. If you have not
planned a hole to stand in, you have denied yourself as well as them.
""",
    "snap": """\
SNAP — THE CHEAPEST INTERDICTION IN THE GAME.
SPEND A SNAP on the single eye that lights their best target. 100 blue,
one cell, one hour.
It kills a probe on that cell and damages a harvester on it — including
one that ARRIVES during the hour. That last half is what lets a SNAP
GUARD a square rather than merely punish one.
It resolves ABOVE the hour-start vision snapshot, so the landing it
denies is refused TONIGHT rather than delayed. This is the one thing an
EMP cannot do, and it is why SNAP answers a smash-and-grab.
WHEN IT IS WRONG: if they can reach the target from a second eye, or
land from another bearing, you have bought a beat and nothing more.
Count their eyes before you fire.
""",
}

# Corrections that must land AFTER the BEWARE_ block they answer, so recency
# favours the correction. Keyed by weapon.
_CORRECTION = {
    "chaff": """\
CORRECTION TO THE CHAFF BLOCK ABOVE — YOU ARE HOLDING ONE.
That block is about SURVIVING a rival's chaff, and it describes chaff as
blind-fired at likely PICKUP hours. That is the defender's view and it
does not describe the weapon in your rack.
Fired deliberately, a chaff cancels a SPECIFIC hour you have read: any
action, not only a pickup. An early flare is not wasted — a drop is an
action, so an hour-1 chaff cancels an hour-1 DROP. The "safe hours" in
that block are safe for YOUR pickups; they are not hours where your own
chaff does nothing.
""",
    "emp": """\
CORRECTION TO THE EMP BLOCK ABOVE — YOU ARE HOLDING ONE.
That block is about surviving a salvo. Offensively the number that
matters is how many of their eyes sit inside ONE radius-2 diamond: two
or more and a single charge blinds the lot.
""",
    "snap": """\
CORRECTION TO THE SNAP BLOCK ABOVE — YOU ARE HOLDING ONE.
That block tells you a rival's SNAP can guard a cell against your
arrival. The same is true in your hand: your SNAP catches a harvester
that arrives during the hour, so it defends your landing as well as
punishing theirs.
""",
}


def doctrine_for(agent_view: Mapping[str, Any]) -> str:
    """Weapon doctrine for the weapons THIS SEAT is actually holding.

    Note the gate polarity. Every BEWARE_ block in the baseline fires on the
    THREAT side — what a rival might hold, or what hit us last night — which is
    why V12 says nothing about spending ordnance on a quiet board, precisely
    the cheapest night to fire. This gates on OUR OWN rack.
    """
    from . import weapon_plays
    held: List[str] = []
    for p in weapon_plays.PLAYS:
        if p.weapon not in held and _stock(agent_view, p.weapon) > 0:
            held.append(p.weapon)
    if not held:
        return ""
    parts: List[str] = []
    for w in held:
        if _DOCTRINE.get(w):
            parts.append(_DOCTRINE[w])
        if _CORRECTION.get(w):
            parts.append(_CORRECTION[w])
    return "\n\n".join(parts)


# ── the rack block (the prompt.py hook calls this) ─────────────────────────
def format_rack_block(agent_view: Mapping[str, Any]) -> str:
    """What ordnance THIS SEAT owns tonight, and what one charge does.

    Rung 1 of the weapons ladder, and the whole of it. ``orbit.weapon_stock``
    has been on the view since v0.9 and V12 prints it NOWHERE, so its night
    phase plans as though the rack were empty. Everything else in the prompt is
    about what might be shot AT us.

    Silent on an empty rack: a line reading "chaff 0" is noise on most nights
    and invites the model to reason about a weapon it cannot fire.
    """
    from . import weapon_plays
    lines: List[str] = []
    for w in ("chaff", "emp", "snap"):
        if not any(p.weapon == w for p in weapon_plays.PLAYS):
            continue
        n = _stock(agent_view, w)
        if n <= 0:
            continue
        cost = _blue_cost(w)
        if w == "chaff":
            lines.append(
                f"  CHAFF x{n} ({cost} blue each) — cancels EVERY OTHER seat's "
                "action at the hour you fire, for 3h. NOT aimed at a cell: "
                "aimed at an HOUR. Hours 2-3 of the window cancel YOUR moves "
                "too, so plan the walk from hour 4."
            )
        elif w == "emp":
            lines.append(
                f"  EMP x{n} ({cost} blue each) — one charge fires 3 missiles "
                "as ONE move; each darkens 13 cells for 8h. Probes inside die, "
                "harvesters are disabled. FRIENDLY FIRE IS ON."
            )
        else:
            lines.append(
                f"  SNAP x{n} ({cost} blue each) — one cell, one hour. Kills "
                "the eye on it and catches a harvester that ARRIVES that hour."
            )
    if not lines:
        return ""
    return "\n".join(
        ["YOUR RACK (bought in orbit — spending it is a NIGHT move):"] + lines
    )


def validate_all() -> List[str]:
    """Every problem across every declared play. Empty means fine."""
    from . import weapon_plays
    bad: List[str] = []
    seen: Dict[str, str] = {}
    for p in weapon_plays.PLAYS:
        bad.extend(p.validate())
        if p.play_id in seen:
            bad.append(f"{p.play_id}: declared twice")
        seen[p.play_id] = p.weapon
    return bad


# ── the blue economy ───────────────────────────────────────────────────────
#
# Declared once in weapon_plays.py as ECONOMY, applied by three hooks. Before
# this was templated it was ~4 minutes of hand-editing across four files, and
# it is the half a team most often gets wrong — a weapon nobody can afford is
# indistinguishable from a weapon that does not work.
#
# One thing NOT offered here, deliberately. ``value_pyramid._BLUE_GRAB_MIN``
# (192) looks like an obvious dial to lower, and its own comment says why not:
# it "matches the sanitizer's blue-loot floor so we never surface a blue the
# corrector would then reroute around". That floor is ``_BLUE_LOOT_MIN = 192``
# in _v7/move_sanitizer.py, which is the FROZEN baseline a test pins. Lower one
# without the other and the menu offers blue the sanitizer reroutes away from —
# an option that appears, gets picked, and quietly becomes a different move.
# 192+ funds a 300-blue flare comfortably, so the floor stays.

@dataclass(frozen=True)
class EconomyPolicy:
    """How the seat funds and buys the weapons it declared.

    Defaults are the conservative reading: fund what you fire, do not buy what
    you cannot fire, and never pull your only harvester off red.
    """

    #: Ask for blue whenever the rack cannot fire — not merely when the VAULT
    #: is short. The stock gate asks the wrong question for an armed agent: a
    #: "medium" vault can hold 150 and still be 150 short of a flare.
    seek_blue_when_rack_empty: bool = True

    #: Buy the cheapest declared weapon the moment it is affordable. The stock
    #: branch tests ``blue_total > blue_always_build`` and then affordability
    #: at the weapon's price, so a threshold EQUAL to the price means the first
    #: purchase waits for price+1 blue. This sets it to price-1.
    buy_asap: bool = True

    #: Set the stockpile cap to 0 for any weapon with no declared play. Blue
    #: spent on ordnance you cannot fire is blue not spent on the one you can,
    #: and V12's own README warns this makes the agent worse rather than better.
    never_buy_what_you_cannot_fire: bool = True

    #: Per-weapon hold limits, e.g. {"chaff": 1}. Empty leaves stock caps alone.
    hold_at: Optional[Dict[str, int]] = None

    # NOTE there is deliberately no `require_spare_harvester` flag. The
    # baseline's own `blue_is_requested` ends with `len(harvesters) >= 2` and
    # that check runs downstream of everything here, so the guard holds whatever
    # we set — a flag would have been a knob that could not be turned off, which
    # is worse than no knob. Sending your ONLY unit to fetch currency loses more
    # than the weapon gains, so this is the right default to be stuck with.

    #: Ask for blue on EVERY night, not only when the rack is empty. For a seat
    #: that keeps building charges rather than holding one, the rack being
    #: non-empty is not a reason to stop earning.
    seek_blue_always: bool = False

    #: Red a juice chain must bank before it outranks a blue run for a
    #: harvester. The stock value is 150, so a 200-red chain keeps a unit off
    #: blue all night. Raise it to divert a harvester to blue unless the red on
    #: offer is genuinely better. ``None`` leaves the stock value alone.
    strong_chain_red_min: Optional[int] = None


def _economy() -> "EconomyPolicy":
    from . import weapon_plays
    return getattr(weapon_plays, "ECONOMY", None) or EconomyPolicy()


def _declared_weapons() -> List[str]:
    from . import weapon_plays
    return sorted({p.weapon for p in weapon_plays.PLAYS})


def tune_dials(dials: Any) -> Any:
    """Apply ECONOMY to the orbit dials. Hooked at ``DEFAULT_DIALS``."""
    import dataclasses

    eco = _economy()
    declared = _declared_weapons()
    if not declared:
        return dials

    changes: Dict[str, Any] = {}

    if eco.buy_asap:
        cheapest = min(_blue_cost(w) for w in declared)
        # price-1 so `blue_total > threshold` and `blue_total >= price` agree.
        changes["blue_always_build"] = max(0, cheapest - 1)

    caps = {"emp": "emp_stockpile_cap",
            "chaff": "chaff_stockpile_cap",
            "snap": "snap_stockpile_cap"}
    if eco.never_buy_what_you_cannot_fire:
        for w, field_name in caps.items():
            if w not in declared and hasattr(dials, field_name):
                changes[field_name] = 0
    for w, n in (eco.hold_at or {}).items():
        field_name = caps.get(w)
        if field_name and hasattr(dials, field_name):
            changes[field_name] = int(n)

    if not changes:
        return dials
    try:
        return dataclasses.replace(dials, **changes)
    except TypeError:
        # A fork may have renamed a dial. Better to run on stock values than
        # to crash the orbit phase.
        return dials


def blue_also_requested(agent_view: Mapping[str, Any]) -> bool:
    """Extra reason to force-surface a blue grab. Hooked into the stock gate.

    Returns True only when we hold none of any declared weapon — i.e. we are
    saving for something specific. Once armed, blue buys no further weapon
    tonight and red genuinely does outrank it again.
    """
    eco = _economy()
    declared = _declared_weapons()
    if not declared:
        return False
    # A seat that keeps BUILDING charges rather than holding one is still
    # earning on a night its rack is full — the next charge is next night's play.
    if eco.seek_blue_always:
        return True
    if not eco.seek_blue_when_rack_empty:
        return False
    return all(_stock(agent_view, w) <= 0 for w in declared)


_DOCTRINE_BLUE_IS_AMMO = """\
CORRECTION — YOU SPEND BLUE, SO BLUE IS AMMUNITION.
The block above ranks blue below red because most agents bank blue and
never use it. You are not most agents: your ordnance is bought with blue,
and it is how you take a rival's best night off them.
So while your rack is EMPTY, a BL* is not a consolation prize for an idle
harvester — it is the reload. Weigh it against a red chain on that basis
rather than on tonight's points alone.
Once you HOLD a charge this reverses: you have your round, blue banks no
further weapon this night, and red outranks it again exactly as above.
"""


def blue_doctrine_for(agent_view: Mapping[str, Any]) -> str:
    """The ammunition correction, or "" when it does not apply.

    Emitted AFTER DOCTRINE_BLUE so recency favours the correction — the same
    ordering trick the weapon doctrine uses against the BEWARE_ blocks.
    """
    if not blue_also_requested(agent_view):
        return ""
    return _DOCTRINE_BLUE_IS_AMMO


def strong_chain_red_min(stock_value: int) -> int:
    """The red a chain must bank before it outranks a blue run.

    Hooked into ``value_pyramid``. Raising it diverts a harvester to blue
    unless the red on offer is genuinely better, which is what an agent whose
    weapon is bought with blue actually wants.
    """
    want = _economy().strong_chain_red_min
    return int(want) if want else int(stock_value)


def economy_summary() -> List[str]:
    """Human-readable lines describing what ECONOMY will do. For the CLI."""
    eco = _economy()
    declared = _declared_weapons()
    out: List[str] = []
    if not declared:
        return ["no weapons declared — economy untouched"]
    if eco.buy_asap:
        cheapest = min(_blue_cost(w) for w in declared)
        out.append(
            f"buy ASAP: blue_always_build -> {cheapest - 1} "
            f"(cheapest declared weapon is {cheapest} blue)"
        )
    if eco.never_buy_what_you_cannot_fire:
        undeclared = [w for w in ("emp", "chaff", "snap") if w not in declared]
        if undeclared:
            out.append(f"never buy (no declared play): {', '.join(undeclared)}")
    for w, n in (eco.hold_at or {}).items():
        out.append(f"hold at most {n} {w}")
    if eco.seek_blue_always:
        out.append("seek blue EVERY night (keep building charges)")
    elif eco.seek_blue_when_rack_empty:
        out.append("seek blue whenever the rack cannot fire")
    if eco.strong_chain_red_min:
        out.append(
            f"divert a harvester to blue unless a chain banks "
            f">{eco.strong_chain_red_min} red (stock: 150)"
        )
    # Inherited from the baseline, not a setting of ours — stated so a team
    # knows the guard exists rather than assuming we turned it on.
    out.append("but never divert the last harvester (baseline guard)")
    return out
