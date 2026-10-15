"""In-memory game session, fog-of-war percepts, JSON serialization.

Authoritative state container for the orchestrator. The night runner now
lives in :mod:`sea_of_colours.game.simulator` and consumes parsed
:class:`~sea_of_colours.game.policy.Move` queues produced by
:func:`~sea_of_colours.game.policy.parse_moves`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import (
    AbstractSet,
    Any,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    cast,
)
import copy
import random
import re
import uuid

from sea_of_colours.game.entities import Entity
from sea_of_colours.game.asset_ledger import (
    ASSET_LEDGER_STORE,
    AssetLedger,
    AssetRecord,
)
from sea_of_colours.game.ledger import LEDGER_STORE, SquareLedger
from sea_of_colours.game.policy import MAX_MOVES, Move, moves_to_wire, parse_moves
from sea_of_colours.game.tuning import (
    live_only_drops,
    map_halo_density,
    probe_lifetime_nights,
    probe_vision_radius,
)
from sea_of_colours.generator import (
    Cell,
    GenerationParams,
    Grid,
    Tile,
    generate_grid,
    pure_count_range,
)
from sea_of_colours.render import (
    BLUE_LEVEL_NAMES,
    RED_LEVEL_NAMES,
    blue_level,
    cell_visual,
    red_level,
)


def _rgb(triple: tuple[int, int, int]) -> str:
    return f"rgb({triple[0]},{triple[1]},{triple[2]})"


def cell_to_paint(cell: Cell) -> dict[str, Any]:
    fg, bg, ch = cell_visual(cell)
    d: dict[str, Any] = {"ch": ch, "bg": _rgb(bg)}
    if fg is not None:
        d["fg"] = _rgb(fg)
    return d


def cell_facts(tile: Tile, purity: int) -> dict[str, Any]:
    """Terrain identity for the map hover readout: colour, tier, purity.

    v1.22 — the dense map payload had only ever carried render colours
    (``ch``/``fg``/``bg``), so the client had to *infer* the tier from the
    dither glyph. That can recover the three-wide band (``▒▒`` → "51–150")
    but never the number, while agents have always read exact purity off
    ``agent_view``. Shipping it closes a gap that was tilting the board in
    the agent's favour.

    Tier names come from :mod:`sea_of_colours.render`, not a second copy of
    the cutoffs (RULEBOOK §2.2). GREEN and EMPTY have no tier ladder, so
    they get a colour and nothing else.
    """
    p = max(0, min(255, int(purity)))
    facts: dict[str, Any] = {"tile": tile.name, "purity": p}
    if tile == Tile.RED:
        facts["tier"] = RED_LEVEL_NAMES[red_level(p) - 1]
    elif tile == Tile.BLUE:
        facts["tier"] = BLUE_LEVEL_NAMES[blue_level(p) - 1]
    return facts


def _snapshot_facts(snap: Mapping[str, Any]) -> dict[str, Any]:
    """:func:`cell_facts` for a frozen echo / memory tile.

    Returns ``{}`` for snapshots written before v1.22 (they carry paint but
    no ``tile``), so an in-flight season keeps rendering — those cells just
    fall back to the tier the client infers from the dither glyph, exactly
    as every cell did before.
    """
    raw_tile = snap.get("tile")
    if raw_tile is None:
        return {}
    try:
        return cell_facts(Tile(int(raw_tile)), int(snap.get("purity", 0)))
    except (TypeError, ValueError):
        return {}


#: Per-player CSS colours used everywhere a unit is rendered — on the
#: live map (via :meth:`GameSession._glyph_for_entity`), in the asset
#: chip Vault, and in replay animations. Owner colour is preserved
#: regardless of cargo state — a carrying harvester stays in its seat
#: colour but switches to the "loaded" glyph variant, so colour-blind
#: players have a shape signal without losing seat readability.
#:
#: v0.9.6 — extended to 4 seats for the N-seat live-play overhaul.
#: White → ``p1`` (default seat).
#: Yellow → ``p2`` (second seat, classic opponent).
#: Magenta → ``p3`` (third seat).
#: Cyan → ``p4`` (fourth seat).
ENTITY_OWNER_COLOR: Dict[str, Tuple[int, int, int]] = {
    "p1": (248, 248, 248),
    "p2": (255, 220, 90),
    "p3": (255, 95, 215),
    "p4": (95, 215, 255),
}
ENTITY_OWNER_DEFAULT: Tuple[int, int, int] = (200, 200, 200)

#: v0.9.18 — curated seat color palette for player customization.
#: High-visibility colors that don't clash with terrain (RED #d14b4b,
#: GREEN #3fb950, BLUE #3b8fe0) or the near-black background (#0e0b16).
#: Maps hex → RGB tuple for validation + server-side color lookups.
SEAT_COLOR_PALETTE: Dict[str, Tuple[int, int, int]] = {
    "#FFFFFF": (255, 255, 255),      # White
    "#FCF871": (252, 248, 113),      # Yellow
    "#85F57E": (133, 245, 126),      # Spring green
    "#82F4FB": (130, 244, 251),      # Aqua
    "#E45EF0": (228, 94, 240),       # Orchid pink
    "#B6FF3A": (182, 255, 58),       # Lime / chartreuse
    "#FF8A1E": (255, 138, 30),       # Orange
    "#FF49B0": (255, 73, 176),       # Hot pink
    "#C29BFF": (194, 155, 255),      # Lavender
}

#: v0.9.18 — default seat color assignments (hex keys into SEAT_COLOR_PALETTE).
SEAT_DEFAULT_COLORS: Dict[str, str] = {
    "p1": "#FFFFFF",    # White
    "p2": "#FCF871",    # Yellow
    "p3": "#E45EF0",    # Orchid pink
    "p4": "#82F4FB",    # Aqua
}
#: Retained for back-compat / replay payloads — no longer used to paint
#: the live map (carrying harvesters now keep their owner colour).
ENTITY_CARRYING_FG: Tuple[int, int, int] = (255, 90, 90)
ENTITY_LOST_FG: Tuple[int, int, int] = (110, 110, 122)


def owner_color(owner: Optional[str]) -> Tuple[int, int, int]:
    """Return the canonical (r, g, b) tuple for ``owner`` (``'p1'``/``'p2'``)."""
    if not owner:
        return ENTITY_OWNER_DEFAULT
    return ENTITY_OWNER_COLOR.get(owner, ENTITY_OWNER_DEFAULT)


def owner_color_css(owner: Optional[str]) -> str:
    """CSS ``rgb(...)`` string for ``owner`` — convenience for the frontend payload."""
    return _rgb(owner_color(owner))


#: Map-overlay glyphs for live entities. Single BMP code points so they
#: render cleanly in any monospace font (no emoji width surprises).
#:
#: * harvester → ``x`` lowercase x  (the original SOC marker — instantly
#:                   readable on a 2-ch terrain dither, no Unicode
#:                   fallback issues across terminal fonts)
#: * harvester (carrying)
#:               → ``X`` uppercase X  (visually "loaded" / heavier; same
#:                   character class as empty harvester so seat colour
#:                   reads identically regardless of cargo state)
#: * probe     → ``·`` U+00B7 MIDDLE DOT (compact sensor pip)
#: * orblift   → ``▲`` U+25B2 BLACK UP-POINTING TRIANGLE
#:                   (only ever visible in the vault — lifters are orbital
#:                   so they never appear on the map, but the chip in the
#:                   vault uses the same glyph for consistency)
ENTITY_GLYPHS: Dict[str, str] = {
    "harvester": "X",                # X — always uppercase
    "harvester_carrying": "X",       # uppercase X — loaded
    "probe": "\u00B7",               # ·
    "orblift": "\u25B2",             # ▲
}


#: Vision is a **Euclidean disk**: a unit with vision ``r`` sees every
#: cell ``(x, y)`` whose centre is within geometric distance ``r`` of
#: the unit's centre (``dx*dx + dy*dy <= r*r``). This produces a circle
#: rather than a square (RULEBOOK §3.11). Bump these numbers to widen
#: the disk — area grows roughly as ``π·r²``.
#
# v0.6.0: harvester vision is a **plus** — radius 1 on the Euclidean
# disk resolves to the harvester's own cell + its 4 cardinal neighbours
# (N/S/E/W). Diagonals are NOT included (``d² = 2 > 1``). As a result
# every step lays a 3-cell-wide ribbon of intel along the harvester's
# path (centre cell from this frame, plus the 2 lateral cells of the
# plus; the next step swaps which cell is "behind" vs "ahead").
# NOTE: PROBE_VISION_RADIUS below is only the legacy fallback constant;
# the live vision path reads sea_of_colours.game.tuning.probe_vision_radius(),
# whose canonical default is 4 (~49-cell Euclidean disk). See RULEBOOK §3.9.7.
HARVESTER_LOS_RADIUS = 1
PROBE_VISION_RADIUS = 2

# Default planning-day cap for a season. After Night N=season_day_cap
# resolves, the session transitions to ``Phase.SEASON_COMPLETE`` and
# refuses further policy submissions.
#
# v0.7.4 — this is now only the *default*. The cap is also stored
# per-session on ``GameSession.season_day_cap`` so a single Snowflake
# database can host short-form (5-night) demos alongside long-form
# (e.g. 7-night) tournament runs without monkey-patching the module
# constant. Callers that need to honour the cap should read
# ``sess.season_day_cap`` (with a fallback to this constant when
# hydrating legacy payloads that predate the field).
#
# v0.8.0 — raised from 5 to 10 so the Orbit phase (§4) has room to
# matter: credits compound, the catapult auction can run several
# rounds, and refine has time to build a high-tier export. Override
# per-session via ``/api/game/new?season_day_cap=N`` or the
# ``GameSession.new(season_day_cap=N)`` kwarg.
#
# v0.9.18 — settled on 7 nights as the canonical season length: long
# enough for the Orbit economy to compound and for harvester losses to
# bite (no more free repairs), short enough to watch end-to-end. The
# per-session override still works for one-off long/short matches.
SEASON_DAY_CAP = 7

# v0.7.4 — Planetary night clock.
#
# Each policy is up to 21 actions per House per night. The night
# itself is exactly that long: ``HOURS_PER_NIGHT = 21``. Every applied
# move (drop / step / pickup / probe) takes exactly one hour. The
# round-by-round interleave (RULEBOOK §3.10) maps onto a shared
# wall-clock — both Houses' Nth applied moves happen "during hour N"
# of the same planetary night, in parallel. Failed (waste) attempts
# share the hour of the next valid move the same House is working
# toward, so a watcher reading the synced log sees the engine's
# retry chatter at the same hour-stamp as the successful action that
# eventually resolved.
HOURS_PER_NIGHT = 21

#: Back-compat aliases for older callers that referenced the previous
#: Chebyshev-shaped names. Safe to remove once no external scripts use
#: them; the new code path uses the ``_RADIUS`` names exclusively.
HARVESTER_LOS_CHEB = HARVESTER_LOS_RADIUS
PROBE_HALF_SPAN = PROBE_VISION_RADIUS

#: Legacy module-level seat list. v0.9.6 — kept as a back-compat alias
#: for callers that still hard-code "p1"/"p2" pairs (tests, simulator,
#: evals). New code should prefer :attr:`GameSession.players`, which
#: is per-session and supports 1-4 seats.
PLAYERS = ("p1", "p2")

#: Maximum simultaneous seats a session can hold. The launcher UI lets
#: the user pick 1-4 and assigns each seat to a human or a bot agent.
#: Bumping past 4 requires extending :data:`ENTITY_OWNER_COLOR`,
#: :data:`SPAWN_SLOTS`, and the frontend seat-tab generator at minimum.
MAX_SEATS: int = 4

#: Default seat IDs in canonical order. ``new_session(num_players=N)``
#: takes the first ``N`` of these. Stays in lockstep with the keys of
#: :data:`ENTITY_OWNER_COLOR` so every supported seat has a colour.
DEFAULT_SEAT_IDS: Tuple[str, ...] = ("p1", "p2", "p3", "p4")

HARVESTER_HOLD_CAPACITY = 6
"""Per-outing hold capacity for a harvester (RULEBOOK §3).

"Hold capacity is 6 parcels per outing: the drop tile plus the (up to) 5
step tiles." A harvester banks every coloured cell it enters until its
hold is full; once it holds this many parcels, further steps are
cancelled (it keeps its 6 and must be picked up + re-dropped to bank
more). Enforced in :meth:`GameSession.try_step_unit`.

NB this is a *parcel* cap, not a raw step cap: a harvester dropped on an
EMPTY tile banks nothing on the drop and so may legitimately take 6
steps to fill the hold (see ``test_no_per_color_harvest_cap``). Prior to
v1.x the cap was documented but enforced nowhere, so a hand-built chain
could keep stepping and bank >6 parcels in a single outing."""

HOARD_CAPACITY = 15
"""Max harvested field squares retained at base per player.

v0.9.x — tightened from 25 to 15. Green parcels share this hoard, so a
smaller vault makes every un-flushed green (each a -100 endgame penalty,
:data:`GREEN_ENDGAME_PENALTY`) both dead weight and a score liability.
Hoard parcels no longer score (see :meth:`GameSession.score_for`); the
vault is a working buffer cleared via the Orbit-phase catapult
(RULEBOOK §4) before parcels become permanent score."""

SHIPPED_CAPACITY = None
"""SHIPPED is UNCAPPED (v0.9.x). The post-catapult ``SHIPPED`` storage
is an unbounded, public, permanent record of every parcel every house
sent to Earth — not a fixed-size bay. ``None`` signals "no limit" to the
HUD/inventory packers. Shipped parcels are the sole score contribution
(§3.1)."""

ORBIT_CREDITS_PER_TURN: int = 1000
"""Per-seat credit award at the start of every Orbit phase (RULEBOOK §4)."""

HARVESTER_BUILD_COST: int = 1500
"""Credits to mint a new harvester via the Orbit BUILD action."""

PROBE_BUILD_COST: int = 250
"""Credits to top up a single probe via the Orbit BUILD action."""

REPAIR_COST: int = 500
"""Credits to repair one damaged harvester via the Orbit REPAIR action.

v0.8.0 — repair is no longer free at dawn. The dawn auto-repair sweep
was removed (RULEBOOK §3.6.1) so a wreck in orbit costs real money to
patch up."""

PROBE_INITIAL_STOCK: int = 2
"""Probes each seat has in inventory at session creation. Consumed by
PROBE moves at night; replenished via the Orbit BUILD_PROBE action."""

HARVESTER_MAX_PER_PLAYER: int = 3
"""Hard cap on owned, non-destroyed harvesters per seat. The Orbit
BUILD_HARVESTER action refuses to mint a fourth (RULEBOOK §4)."""

STARTING_BLUE_PURITY: int = 250
"""Fissile stipend every house begins the season with. Seeded as a
single BLUE parcel in the vault at game birth so it reads through
``blue_purity_available`` exactly like harvested blue. Blue is the
spend surface for REFINING and the weapons economy (§4)."""

# v1.13 — the catapult is gone. RED used to compete for 20 shared slots
# via a per-parcel credit bid, each row charging a RED-purity transit
# fee. That draft was the single most-explained rule in the game and the
# decision it asked for (how much to bid) was rarely interesting, so RED
# now ships automatically at settlement with no fee and no competition.
# The tier multiplier below survives untouched: auto-shipping changes
# *whether you choose*, not what a parcel is worth, so where you send
# harvesters at night stays the decision that matters.
RED_QUALITY_MULTIPLIER: Dict[str, float] = {
    "trace": 0.75,
    "vein": 1.0,
    "mass": 1.5,
    "pure": 3.0,
}
"""Tunable tier-quality multiplier applied at ship time. A pure-255
parcel scores 255 × 3.0 = 765; a trace-40 parcel scores 40 × 0.75 = 30.
Tweak these constants in one place to retune the entire RED economy —
the hoard still stores raw purity, the formula only kicks in when a
parcel lands in ``shipped_squares`` (see ``score_for``)."""

# v1.13 — the green catapult is gone too. Flushing GREEN used to be a
# round-robin slot draft paid for in forfeit RED fuel; now every GREEN
# parcel a house is still holding is simply charged at
# GREEN_ENDGAME_PENALTY. Same pressure, no ceremony: GREEN is a tax on
# mining blind, and the interesting decision was always whether to take
# the harvest at all, not how to dispose of the consequences.
_MINE_REFUND_BLUE_EACH: int = 100
"""v1.31 — blue purity handed back per unspent caltrop when a pre-1.31
save loads (the retired ``MINE_COST_BLUE_PURITY``). Frozen here rather
than imported from :mod:`weapons` precisely because the weapon is gone:
this is a historical price, not a live dial, and it must not drift if a
replacement weapon takes the slot at a different cost."""


GREEN_ENDGAME_PENALTY: int = 100
"""Final-score penalty per undisposed VAULT-green parcel (toxic legacy).
Green is the 'mistake tax' (blind/contested harvests); it must be
flushed through the green catapult before season end or it bleeds
score. Surfaced live in :meth:`score_for` as a standing liability."""


def compute_player_score(
    shipped_parcels: Sequence[Mapping[str, Any]],
    hoard_parcels: Sequence[Mapping[str, Any]],
    *,
    is_complete: bool,
) -> int:
    """Canonical season score for one seat, from raw parcel lists.

    Single source of truth shared by :meth:`GameSession.score_for` and
    the watcher season picker (``engine.list_sessions`` scores straight
    off the persisted SHIPPED / HOARD parcel tables via
    ``SocStore.bulk_session_scores`` — no full-session hydration), so
    the standings, the HUD's shipped-score line, and the end-of-game
    results screen can never disagree.

        score = tier-weighted effective purity over SHIPPED RED parcels
              - GREEN_ENDGAME_PENALTY per GREEN parcel, whether it was
                auto-disposed at settlement or is still in the vault
              + (season-end only) 50%-of-raw-purity fire-sale on any
                RED left unshipped in the vault

    v1.13 — GREEN is auto-disposed each orbit and the disposed parcels
    are appended to SHIPPED carrying their GREEN origin tile. They are
    charged here rather than credited, which is what lets the bulk
    scoreboard (``SocStore.bulk_session_scores``, which reads the
    SHIPPED/HOARD tables without hydrating a session) reach the same
    number as :meth:`GameSession.score_for` with no extra column.

    ``GameSession._parcel_purity`` / ``_tier_for_purity`` are pure
    staticmethods; referencing them keeps the purity/tier rules in one
    place. They resolve at call time, after the class is defined.
    """
    total = 0.0
    for parcel in shipped_parcels or []:
        origin = parcel.get("tile_at_harvest")
        if origin is None:
            origin = parcel.get("origin_tile")
        try:
            is_green = int(origin) == int(Tile.GREEN)
        except (TypeError, ValueError):
            is_green = parcel.get("score_tier") == "green"
        if is_green:
            total -= GREEN_ENDGAME_PENALTY
            continue
        eff = parcel.get("effective_purity")
        if eff is None:
            eff = GameSession._parcel_purity(parcel)
        eff = max(0, int(eff))
        tier = parcel.get("score_tier") or GameSession._tier_for_purity(eff)
        total += eff * RED_QUALITY_MULTIPLIER.get(tier, 1.0)
    green = 0
    red_loss = 0.0
    for parcel in hoard_parcels or []:
        tile = parcel.get("tile_at_harvest")
        if tile is None:
            tile = parcel.get("origin_tile")
        try:
            t = int(tile)
        except (TypeError, ValueError):
            continue
        if t == int(Tile.GREEN):
            green += 1
        elif t == int(Tile.RED):
            red_loss += 0.5 * max(0, GameSession._parcel_purity(parcel))
    total -= GREEN_ENDGAME_PENALTY * green
    if is_complete:
        total += red_loss
    return int(round(total))


def compute_score_breakdown(
    shipped_parcels: Sequence[Mapping[str, Any]],
    hoard_parcels: Sequence[Mapping[str, Any]],
    *,
    is_complete: bool,
) -> Dict[str, int]:
    """The three lines that ADD UP to :func:`compute_player_score`.

    v1.24 — the results card used to assemble these itself from two
    sources that describe different moments, and so could not be made to
    reconcile: ``shipped`` came off ``cumulative_shipped_score`` (which
    already has the GREEN debit folded in) while ``green_penalty`` came
    off ``vault_green_count`` (which is ZERO once settlement has disposed
    the vault's GREEN into SHIPPED). A seat that paid 400 in GREEN
    charges therefore saw "shipped 900 · green 0" with no line accounting
    for the 400 — the "end score doesn't match" report.

    Splitting it here, beside the scorer, is the point: there is one
    definition of each term and the card cannot drift from the total
    again.

    Returns ``{"shipped", "green_penalty", "vault_red_loss", "final"}``
    where ``shipped - green_penalty + vault_red_loss == final`` exactly.
    ``shipped`` is RED only; ``green_penalty`` is every GREEN charged,
    whether it is still hoarded or was disposed into SHIPPED.
    """
    final = compute_player_score(
        shipped_parcels, hoard_parcels, is_complete=is_complete
    )

    shipped_red = 0.0
    green_count = 0
    for parcel in shipped_parcels or []:
        origin = parcel.get("tile_at_harvest")
        if origin is None:
            origin = parcel.get("origin_tile")
        try:
            is_green = int(origin) == int(Tile.GREEN)
        except (TypeError, ValueError):
            is_green = parcel.get("score_tier") == "green"
        if is_green:
            green_count += 1
            continue
        eff = parcel.get("effective_purity")
        if eff is None:
            eff = GameSession._parcel_purity(parcel)
        eff = max(0, int(eff))
        tier = parcel.get("score_tier") or GameSession._tier_for_purity(eff)
        shipped_red += eff * RED_QUALITY_MULTIPLIER.get(tier, 1.0)

    red_loss = 0.0
    for parcel in hoard_parcels or []:
        tile = parcel.get("tile_at_harvest")
        if tile is None:
            tile = parcel.get("origin_tile")
        try:
            t = int(tile)
        except (TypeError, ValueError):
            continue
        if t == int(Tile.GREEN):
            green_count += 1
        elif t == int(Tile.RED):
            red_loss += 0.5 * max(0, GameSession._parcel_purity(parcel))

    green_penalty = int(GREEN_ENDGAME_PENALTY * green_count)
    vault_red_loss = int(round(red_loss)) if is_complete else 0
    # ``final`` rounds the WHOLE sum once, so rounding each line
    # separately can leave the three a point adrift. Settle that residue
    # on the RED line (much the largest term, and the only one that is
    # a sum of fractional products) so the card always adds up. The other
    # two stay independently computed, so a genuine GREEN or fire-sale
    # error still shows up as a wrong line rather than being absorbed.
    shipped = final + green_penalty - vault_red_loss
    _honest = int(round(shipped_red))
    if abs(_honest - shipped) > 1:  # pragma: no cover — guards a real bug
        shipped = _honest
    return {
        "shipped": int(shipped),
        "green_penalty": green_penalty,
        "vault_red_loss": vault_red_loss,
        "final": int(final),
    }

# ── v0.9.11 — Station observation grades (RULEBOOK §3.15.x) ─────────
# Each platform broadcasts a coarse "reading" that any seat can pick
# up from orbit. Self sees exact numbers alongside the grade; rivals
# see only the grade band (fissile/toxic) or fuzzy range (green
# count). All thresholds live here so the fog-of-war economy is
# trivially tunable in one place.

#: Hoard fill grade vs HOARD_CAPACITY. ``empty`` is reserved for an
#: exactly-zero hold; otherwise the fraction picks the band by upper
#: bound: ``low (<25%) / half (<60%) / high (<90%) / full (>=90%)``.
STATION_FULLNESS_BANDS: List[Tuple[float, str]] = [
    (0.25, "low"),
    (0.60, "half"),
    (0.90, "high"),
]
STATION_FULLNESS_FULL = "full"

#: Summed-purity grade bands shared by BLUE (fissile) and GREEN (toxic)
#: readings: ``none=0, low=1-100, medium=101-250, high=251+``. BLUE also
#: carries a FINER pip band (see below) surfaced to rivals + the UI.
STATION_PURITY_BANDS: List[Tuple[int, str]] = [
    (0, "none"),
    (100, "low"),
    (250, "medium"),
]
STATION_PURITY_HIGH = "high"

#: v0.9.x — Finer BLUE (fissile) pip band for the station readout. Rivals
#: (and the UI's 3-bar unseen view) resolve blue in ``STATION_BLUE_PIP_STEP``
#: (150) purity steps up to ``STATION_BLUE_PIP_MAX`` (5) pips = a 750+ ceiling.
#: ``band`` = floor(total / step) capped at max: 0 / 1-150→1 / 151-300→2 /
#: ... / 601-750→5 / 750+→5. Sharper than the 4-band grade so both a human
#: and the agent can spot an ~200-BLUE EMP spend across a turn.
STATION_BLUE_PIP_STEP: int = 150
STATION_BLUE_PIP_MAX: int = 5


def _shipped_weapon_blue_costs() -> Dict[str, int]:
    """Today's weapon prices, for stamping into a NEW game (v1.36).

    Imported lazily and copied, matching how every other weapons
    constant is reached from this module: a session owns its prices
    from birth, and must not share a dict with the module that a later
    retune would edit under it.
    """
    from sea_of_colours.game.weapons import BLUE_COST_BY_KIND

    return dict(BLUE_COST_BY_KIND)


def _fresh_weapon_counters() -> Dict[str, Dict[str, int]]:
    """A zeroed counter row per seat, over the kinds shipped today (v1.36).

    Derived rather than written out, so that adding or withdrawing a
    weapon is an edit to ``BLUE_COST_BY_KIND`` and nothing else. The
    literal this replaced was the last place a retired weapon could
    still materialise a bay for itself on a brand-new game.
    """
    return {
        seat: dict.fromkeys(_shipped_weapon_blue_costs(), 0)
        for seat in ("p1", "p2")
    }


def _shipped_arsenal_cap() -> int:
    from sea_of_colours.game.weapons import WEAPONISED_BLUE_CAP

    return int(WEAPONISED_BLUE_CAP)


def _weapon_costs_from_save(data: Mapping[str, Any]) -> Dict[str, int]:
    """Price table for a loading save — stamped, else pre-retune."""
    from sea_of_colours.game.weapons import LEGACY_BLUE_COST_BY_KIND

    raw = data.get("weapon_blue_costs")
    if isinstance(raw, Mapping) and raw:
        out: Dict[str, int] = {}
        for kind, cost in raw.items():
            try:
                out[str(kind)] = int(cost)
            except (TypeError, ValueError):
                continue
        if out:
            return out
    return dict(LEGACY_BLUE_COST_BY_KIND)


def _weapon_counters_from_save(
    raw: Any,
    seats: Sequence[str],
    costs: Mapping[str, int],
) -> Dict[str, Dict[str, int]]:
    """Re-hydrate ``weapon_stock`` / ``weapons_used`` (v1.36).

    A hydrated seat carries **exactly the kinds this game prices** — a
    zero for each, and nothing else. A kind the save mentions that is no
    longer priced is a RETIRED weapon, and it is dropped here rather
    than carried: leaving it in would put a bay nobody can fill in front
    of the player, and give the arsenal a row the cap does not count.

    Dropping it is not the same as confiscating it. Retirement owes the
    seat its blue back, and the migration that pays it reads the RAW
    save (see the caltrop block in :meth:`GameSession.from_dict`), not
    this dict — which is what lets this stay a clean "kinds we price"
    projection. ``docs/ADDING_A_WEAPON.md`` states the obligation: retire
    a weapon and you write the refund block in the same change.
    """
    src = raw if isinstance(raw, Mapping) else {}
    out: Dict[str, Dict[str, int]] = {}
    for seat in seats:
        row = src.get(seat)
        row = row if isinstance(row, Mapping) else {}
        slot: Dict[str, int] = {}
        for kind in costs:
            try:
                slot[str(kind)] = int(row.get(kind) or 0)
            except (TypeError, ValueError):
                slot[str(kind)] = 0
        out[str(seat)] = slot
    return out


def _arsenal_cap_from_save(data: Mapping[str, Any]) -> int:
    from sea_of_colours.game.weapons import LEGACY_WEAPONISED_BLUE_CAP

    try:
        cap = int(data.get("weapon_blue_cap"))
    except (TypeError, ValueError):
        cap = 0
    return cap if cap > 0 else int(LEGACY_WEAPONISED_BLUE_CAP)

#: Fuzzy green-parcel count ranges broadcast to rivals (exact count is
#: only revealed to self): ``0 / 1-3 / 4-7 / 8-12 / 13+``.
STATION_GREEN_COUNT_BANDS: List[Tuple[int, str]] = [
    (0, "0"),
    (3, "1-3"),
    (7, "4-7"),
    (12, "8-12"),
]
STATION_GREEN_COUNT_HIGH = "13+"

# v1.13 — refining is gone. It let a house fold N low-tier parcels into
# fewer higher-tier ones for a BLUE fee, and it was reliably the rule
# that lost a first-time player: a purity-conserving partition with a
# residual, a slot cap, a blue cost and a special terminal variant. A
# parcel now ships at the tier it was mined, which makes *where you mine*
# the whole story. Removing it also hands BLUE back to the weapons
# economy, which is the only thing it funds now.

TYPE_PUBLIC_NAME: Dict[str, str] = {
    "harvester": "Harvester",
    "orblift": "Orbital lifter",
    "probe": "Probe",
}


def public_entity_title(ent: Entity) -> str:
    base = TYPE_PUBLIC_NAME.get(ent.entity_type, ent.entity_type.title())
    extras: List[str] = []
    if ent.x is None:
        extras.append("orbit")
    else:
        extras.append(f"tile {ent.x},{ent.y}")
    if getattr(ent, "lost_last_night", False):
        extras.append("Aurora stranding (empty cargo)")
    if ent.entity_type == "harvester" and getattr(ent, "cargo_squares", []):
        extras.append(f"cargo squares ×{len(ent.cargo_squares)}")
    if ent.entity_type == "orblift" and ent.orbital_cargo_red:
        extras.append("holds Red cargo")
    tail = f" · {' · '.join(extras)}" if extras else ""
    return f"{base} `{ent.id}` ({ent.owner}){tail}"


def occupant_wire(ent: Entity, sess: Optional["GameSession"] = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": ent.id,
        "type": ent.entity_type,
        "owner": ent.owner,
        "label": public_entity_title(ent),
    }
    if ent.entity_type in ("harvester", "probe"):
        result["idx"] = _unit_ordinal(ent.id)
    if ent.entity_type == "harvester":
        result["damaged"] = bool(getattr(ent, "damaged", False))
    # v0.9.18 — carry the probe's remaining coverage life on the occupant
    # so the frontend can ring/annotate ENEMY probes (which surface only
    # as occupants / fog-launch markers, never in the viewer's own asset
    # roster) the same way it does the viewer's own probes.
    if ent.entity_type == "probe" and sess is not None:
        nr = sess.probe_nights_remaining(ent)
        if nr is not None:
            result["nights_remaining"] = nr
    return result


class Phase(str, Enum):
    LOBBY = "lobby"
    # Daytime planning step where seats spend credits on builds /
    # repair / refine and submit bids to the catapult lanes (RULEBOOK
    # §4). Resolves to PLANNING once both seats lock their Orbit
    # action queue.
    ORBIT = "orbit"
    PLANNING = "planning"
    # Terminal phase once :data:`SEASON_DAY_CAP` nights have resolved.
    # ``submit_policy`` returns an error pointing the caller at NEW GAME.
    SEASON_COMPLETE = "season_complete"


# v0.9.6 — relaxed from ``Literal["p1","p2"]`` to ``str`` so a session
# can carry up to 4 seats (p1..p4). ``cast_player`` still validates the
# id against the session's seat list at runtime, so a typo like
# ``"p9"`` surfaces as a ValueError just like before.
PlayerId = str


def _chebyshev_cells(cx: int, cy: int, r: int, w: int, h: int) -> Set[Tuple[int, int]]:
    """Square (Chebyshev) ball — kept for non-vision utilities only.

    Use :func:`_euclidean_disk` for any vision / LOS query so the shape
    is a true circle (RULEBOOK §3.11).
    """
    out: set[Tuple[int, int]] = set()
    x0 = max(0, cx - r)
    x1 = min(w - 1, cx + r)
    y0 = max(0, cy - r)
    y1 = min(h - 1, cy + r)
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            if max(abs(x - cx), abs(y - cy)) <= r:
                out.add((x, y))
    return out


def _manhattan_disk(cx: int, cy: int, r: int, w: int, h: int) -> Set[Tuple[int, int]]:
    """All grid cells whose centre is within Manhattan distance ``r`` of ``(cx, cy)``.

    Manhattan distance ``|dx| + |dy| <= r`` describes a rhombus (a
    diamond rotated 45° from a square). v0.9.4 — EMP clouds use
    this shape so the AoE reads as a clean diamond on the map,
    matching the frontend ``paintEmpCloudOverlay`` rendering. The
    naïve square bbox pre-clip keeps the inner test cheap.
    """
    out: set[Tuple[int, int]] = set()
    x0 = max(0, cx - r)
    x1 = min(w - 1, cx + r)
    y0 = max(0, cy - r)
    y1 = min(h - 1, cy + r)
    for y in range(y0, y1 + 1):
        dy = abs(y - cy)
        for x in range(x0, x1 + 1):
            if abs(x - cx) + dy <= r:
                out.add((x, y))
    return out


def _euclidean_disk(cx: int, cy: int, r: int, w: int, h: int) -> Set[Tuple[int, int]]:
    """All grid cells whose centre is within Euclidean distance ``r`` of ``(cx, cy)``.

    This is the canonical vision shape: ``dx*dx + dy*dy <= r*r``. A probe
    with vision ``r`` sees every cell at travel-distance ``r`` or less,
    yielding a circular disk rather than a square. The naïve square bbox
    pre-clip keeps the inner test cheap.
    """
    out: set[Tuple[int, int]] = set()
    rr = r * r
    x0 = max(0, cx - r)
    x1 = min(w - 1, cx + r)
    y0 = max(0, cy - r)
    y1 = min(h - 1, cy + r)
    for y in range(y0, y1 + 1):
        dy = y - cy
        for x in range(x0, x1 + 1):
            dx = x - cx
            if dx * dx + dy * dy <= rr:
                out.add((x, y))
    return out


def _probe_vision_disk(cx: int, cy: int, w: int, h: int) -> Set[Tuple[int, int]]:
    """Cells a probe at ``(cx, cy)`` can see — Euclidean disk of radius
    :data:`PROBE_VISION_RADIUS` (env-overridable via ``SOC_PROBE_RADIUS``)."""
    return _euclidean_disk(cx, cy, probe_vision_radius(), w, h)


def _harvester_vision_disk(cx: int, cy: int, w: int, h: int) -> Set[Tuple[int, int]]:
    """Cells a harvester at ``(cx, cy)`` can see — Euclidean disk of radius
    :data:`HARVESTER_LOS_RADIUS`.

    At v0.6.0's radius of ``1`` the disk resolves to a plus shape: the
    harvester's own cell plus its 4 cardinal neighbours (N/S/E/W). The
    Euclidean test ``dx² + dy² ≤ 1`` excludes diagonals (``d² = 2``).
    So a harvester at the corner of a seam now sees four extra tiles
    every step, which the :meth:`_pulse_vision_intel` pulse records
    into the player's echo tier — its trail is no longer a single
    column of historical vision but a 3-cell-wide ribbon along the
    path. The harvest action itself still triggers on the cell the
    harvester occupies (handled in :meth:`GameSession._harvest_at`).
    """
    return _euclidean_disk(cx, cy, HARVESTER_LOS_RADIUS, w, h)


def _xy_key(x: int, y: int) -> str:
    return f"{x}:{y}"


def _tile_name_from_int(t: int) -> str:
    """Human-friendly tile name from a packed int (used by vault logs)."""
    try:
        return Tile(int(t)).name
    except (ValueError, KeyError):
        return f"TILE_{t}"


def _hydrate_cumulative_shipped_score(
    raw: Any,
    shipped_squares_raw: Any,
    seat_ids: Sequence[str],
) -> Dict[str, float]:
    """Re-hydrate cumulative shipped score, with a legacy recompute path.

    v0.9.9 — sessions saved BEFORE this counter existed have no
    ``cumulative_shipped_score`` key. Falling back to ``0.0`` would
    visibly zero-out a multi-day in-progress game's scoreboard the
    first time it round-tripped through ``_hydrate_session``. We
    recompute from the snapshotted ``shipped_squares`` (the canonical
    source of truth :meth:`score_for` reads from) so the legacy save
    rehydrates with the exact score the API would have returned.

    Going forward, the resolver bumps the cached counter and we trust
    it; this fallback only runs once per legacy session.
    """
    out: Dict[str, float] = {}
    raw_map: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    shipped_map: Mapping[str, Any] = (
        shipped_squares_raw if isinstance(shipped_squares_raw, Mapping) else {}
    )
    for seat in seat_ids:
        if seat in raw_map:
            try:
                out[seat] = float(raw_map[seat] or 0.0)
                continue
            except (TypeError, ValueError):
                pass
        rows = shipped_map.get(seat) or []
        total = 0.0
        for parcel in rows:
            if not isinstance(parcel, Mapping):
                continue
            # v1.13 — the SHIPPED bay is no longer RED-only: auto-disposed
            # GREEN is appended here too, carrying its origin tile, and it
            # is a debit. Scoring it as purity × multiplier would credit
            # the player for the mistake the parcel represents.
            origin = parcel.get("tile_at_harvest")
            if origin is None:
                origin = parcel.get("origin_tile")
            try:
                is_green = int(origin) == int(Tile.GREEN)
            except (TypeError, ValueError):
                is_green = parcel.get("score_tier") == "green"
            if is_green:
                total -= GREEN_ENDGAME_PENALTY
                continue
            eff_raw = parcel.get("effective_purity")
            if eff_raw is None:
                eff_raw = (
                    parcel.get("purity_at_harvest")
                    if isinstance(
                        parcel.get("purity_at_harvest"), (int, float)
                    )
                    else parcel.get("origin_purity")
                    if isinstance(parcel.get("origin_purity"), (int, float))
                    else parcel.get("purity") or 0
                )
            try:
                eff = max(0, int(eff_raw))
            except (TypeError, ValueError):
                eff = 0
            if eff <= 50:
                tier = "trace"
            elif eff <= 150:
                tier = "vein"
            elif eff <= 254:
                tier = "mass"
            else:
                tier = "pure"
            mult = RED_QUALITY_MULTIPLIER.get(tier, 1.0)
            total += eff * mult
        out[seat] = float(total)
    return out


def _normalize_log_entries(raw: Any) -> List[Dict[str, Any]]:
    """Coerce a deserialized log into structured log entries.

    Accepts the new structured form *and* legacy plain-string lists so a
    saved session from before v0.3.7 still loads cleanly. Anything that
    isn't a dict with a recognised level is treated as ``info``.

    v0.9.8 — preserves the rich-field shape: ``day``, ``phase``,
    ``kind``, ``data``. Pre-v0.9.8 this helper aggressively whitelisted
    the entry down to ``{level, text}`` which silently dropped the
    ``day`` stamp added by :meth:`GameSession.log_info` (et al). Every
    ``_hydrate_session`` cycle therefore wiped the per-line day, and
    the frontend LOG filter — which keys off ``day`` — could not
    distinguish between "all entries belong to today" and "all entries
    belong to whatever cursor day". In multi-seat games (4 bots × N
    days of events accumulate) the LOG visibly collapsed into a single
    undifferentiated stream. The whitelist now also keeps ``ts`` for
    chronology if upstream ever attaches one.
    """
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for entry in raw:
        if isinstance(entry, dict):
            level = str(entry.get("level") or "info")
            if level not in ("info", "error"):
                level = "info"
            text = str(entry.get("text") or "")
            row: Dict[str, Any] = {"level": level, "text": text}
            if "day" in entry:
                try:
                    row["day"] = int(entry["day"])
                except (TypeError, ValueError):
                    pass
            if "phase" in entry and entry["phase"]:
                row["phase"] = str(entry["phase"])
            if "kind" in entry and entry["kind"]:
                row["kind"] = str(entry["kind"])
            if "data" in entry and isinstance(entry["data"], Mapping):
                row["data"] = dict(entry["data"])
            if "ts" in entry and entry["ts"] is not None:
                row["ts"] = entry["ts"]
            out.append(row)
        else:
            out.append({"level": "info", "text": str(entry)})
    return out


#: Path-count → shading glyph. Each repeat-visit bumps the tile to the
#: next denser tier so the map alone communicates traffic intensity.
PATH_TIER_GLYPHS: Tuple[str, ...] = (
    "\u2591\u2591",  # 1 visit  → ░░ (Light Shade, 25%)
    "\u2592\u2592",  # 2 visits → ▒▒ (Medium Shade, 50%)
    "\u2593\u2593",  # 3 visits → ▓▓ (Dark Shade, 75%)
    "\u2588\u2588",  # 4+       → ██ (Full Block, 100%)
)


def path_tier_glyph(visit_count: int) -> Optional[str]:
    """Return the block-element glyph for a tile traversed ``visit_count`` times."""
    if visit_count <= 0:
        return None
    idx = min(visit_count, len(PATH_TIER_GLYPHS)) - 1
    return PATH_TIER_GLYPHS[idx]


def _load_path_counts(raw: Any) -> Dict[str, Dict[str, Any]]:
    """Restore a path-count map, tolerating every prior on-disk shape.

    Three persisted shapes exist in the wild and all three must load
    cleanly so older sessions keep working:

    - **Pre-v0.3.5**: a sorted list of ``"x:y"`` keys (each implying a
      single visit, no day stamp, no harvester id).
    - **v0.3.5 .. v0.4.x**: ``{"x:y": <count:int>}`` — visit count only.
    - **Current**: ``{"x:y": {"n": <count>, "d": <last_day>,
      "h": <last_harvester_id>}}`` — count + the day-of-last-visit and
      harvester id of the unit that recorded it. ``d`` / ``h`` may
      legitimately be ``None`` for legacy entries that loaded without
      that metadata.

    The in-memory shape is always the v0.5 dict-of-dicts so the rest of
    the engine doesn't branch on legacy / current. Callers should treat
    ``d`` / ``h`` as best-effort metadata that might be absent.
    """
    out: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            sk = str(k)
            if isinstance(v, dict):
                # Already in the new shape — just normalise keys.
                out[sk] = {
                    "n": int(v.get("n", 0)),
                    "d": (int(v["d"]) if v.get("d") is not None else None),
                    "h": (str(v["h"]) if v.get("h") is not None else None),
                }
            else:
                # Legacy int-count entry; promote with empty metadata.
                try:
                    n = int(v)
                except (TypeError, ValueError):
                    n = 0
                out[sk] = {"n": n, "d": None, "h": None}
        return out
    if isinstance(raw, (list, tuple, set)):
        for k in raw:
            out[str(k)] = {"n": 1, "d": None, "h": None}
        return out
    return out


def _adj(a: Tuple[int, int], b: Tuple[int, int]) -> bool:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1


def _clone_grid(grid: Grid) -> Grid:
    return [[Cell(c.tile, c.purity) for c in row] for row in grid]


def _unit_ordinal(unit_id: str) -> int:
    """Map an asset id to a 1-based ordinal so the map / chip UI can
    stamp a small subscript tag on each unit. Handles both shapes:

    * ``harvester_<p>``     → 1   (seat-1 spawn, no numeric suffix)
    * ``harvester_<p>_<n>`` → n   (orbit-built or re-spawn)
    * ``probe_<p>_<n>``     → n   (probes are always numbered)
    * ``orblift_<p>``       → 1   (1 per seat; ordinal mostly cosmetic)

    Unrecognised shapes fall back to 1 so the badge is always
    renderable rather than crashing the cell render.
    """
    if not isinstance(unit_id, str):
        return 1
    parts = unit_id.split("_")
    if len(parts) >= 3 and parts[0] in ("harvester", "probe", "orblift"):
        try:
            return int(parts[-1])
        except ValueError:
            return 1
    return 1


def _orbit_actions_to_wire(actions: Any) -> List[Dict[str, Any]]:
    """Serialise a list of OrbitAction (or raw wire dicts) to the
    JSON-safe wire shape. Tolerates already-wire dicts so callers
    don't have to know whether the list came from a freshly-parsed
    stash or a re-hydrated one. Lives at module scope to avoid a
    circular import with :mod:`policy`.
    """
    from sea_of_colours.game.policy import orbit_action_to_wire
    out: List[Dict[str, Any]] = []
    for a in actions or []:
        if isinstance(a, dict):
            out.append(dict(a))
            continue
        try:
            out.append(orbit_action_to_wire(a))
        except Exception:
            # Best-effort: anything we can't convert becomes a
            # ``waste`` marker so the resolver flags it loudly
            # instead of crashing on attribute access.
            out.append({"a": "waste", "reason": "unserialisable orbit action"})
    return out


def _orbit_actions_from_wire(payload: Any) -> Optional[List[Any]]:
    """Re-parse a wire payload (list of dicts) back into typed
    OrbitAction instances. ``None`` payload means "no submission yet"
    and is propagated unchanged so :meth:`both_orbit_ready` works.
    """
    if payload is None:
        return None
    from sea_of_colours.game.policy import parse_orbit_actions
    actions, _errs = parse_orbit_actions(payload)
    return list(actions)


@dataclass
class GameSession:
    """Authoritative opaque game container for the web MVP."""

    width: int
    height: int
    seed: int
    grid: Grid = field(repr=False)
    entities: Dict[str, Entity] = field(default_factory=dict)
    day: int = 1
    phase: Phase = Phase.PLANNING
    #: v0.9.6 — per-session seat list. Defaults to the legacy 2-seat
    #: shape so every existing test / call site that constructs a
    #: ``GameSession`` directly (or via :meth:`new` without
    #: ``num_players``) keeps the pre-v0.9.6 behaviour. ``__post_init__``
    #: extends the per-seat dicts to cover every seat in this tuple,
    #: so callers can safely override to 3 or 4 seats without
    #: pre-populating every keyed field by hand.
    players: Tuple[str, ...] = ("p1", "p2")
    #: v0.9.6 — per-seat agent assignment. ``"human"`` means the seat is
    #: piloted from the browser (or any external client); ``"red_harvest"``
    #: routes that seat through the in-process heuristic agent on every
    #: submit. Missing keys default to ``"human"`` for back-compat with
    #: legacy 2-seat snapshots.
    agents: Dict[str, str] = field(default_factory=dict)
    #: v0.9.6 — visibility model for the agent view + replay frames.
    #: ``"hidden"`` keeps the v0.8.x fog/memory/probe-intel rules (a
    #: seat sees only what its own units have seen). ``"open"``
    #: bypasses fog entirely — the agent view exposes the full grid
    #: and every seat's inventory, matching the OBS replay overlay.
    #: Replay frame snapshots also honour the same flag so a hidden
    #: game stays hidden after the season ends.
    visibility_mode: str = "hidden"
    #: v1.32 — per-GAME rule switches, for the teaching modes.
    #:
    #: Same rule as the storage backend (AGENTS.md, v1.14): these live on
    #: the SESSION, not in process config, so a tutorial and a real season
    #: can be open in two tabs against one server. Nothing may ask "is this
    #: process in tutorial mode?" — ask the session.
    #:
    #: They are also published on ``meta.rules`` (see
    #: ``snowpark/view.py:_active_rules``) rather than being enforced by a
    #: silent filter. An agent that is never TOLD weapons are off will
    #: propose EMPs every turn and have them eaten by the sanitiser, which
    #: burns its whole move budget and looks like a broken agent.
    weapons_enabled: bool = True
    #: Blue signs (§4.10) and discovery-triggered redsign beacons (§4.11).
    #: Off together: a board with one and not the other teaches a signage
    #: model that does not exist in a real season.
    signs_enabled: bool = True
    #: v1.36 — the weapon economy this GAME was created under: the blue
    #: price of each weapon kind, and the arsenal ceiling those prices
    #: are denominated in (§4.9.8).
    #:
    #: Stamped rather than read live because a price is not a process
    #: setting, it is a fact about a season. A game in flight when
    #: someone retunes ``weapons.py`` must keep the numbers its players
    #: have been buying against all week, and a finished season replayed
    #: afterwards must price its arsenal bar the way it was actually
    #: played — the client reads these off ``meta.rules`` to move the
    #: bar hour by hour, so a stale price makes a rack go negative
    #: mid-night in front of somebody.
    #:
    #: A save written before this field existed has neither, and
    #: ``from_dict`` fills them from ``LEGACY_*`` rather than from
    #: today's constants. Always reach for :meth:`weapon_prices` /
    #: :meth:`arsenal_cap`; never import the module constants at a call
    #: site that has a session in hand.
    weapon_blue_costs: Dict[str, int] = field(
        default_factory=lambda: _shipped_weapon_blue_costs()
    )
    weapon_blue_cap: int = field(default_factory=lambda: _shipped_arsenal_cap())
    #: ``""`` for an ordinary season, else the teaching preset that made
    #: this game (``"basic"`` / ``"advanced"``). The client reads it to pick
    #: which film reel the tutorial modal opens on; the engine only stores
    #: it. Rules are carried by the flags above, never by this string, so a
    #: future preset cannot quietly change what is legal.
    tutorial: str = ""
    probe_seq: Dict[str, int] = field(default_factory=lambda: {"p1": 0, "p2": 0})
    pending_policies: Dict[str, Optional[List[Move]]] = field(
        default_factory=lambda: {"p1": None, "p2": None}
    )
    #: Game-wide audit log. Each entry is a structured event
    #: ``{"level": "info"|"error", "text": "..."}`` so the HUD can paint
    #: errors in yellow next to the attempted action. Older callers that
    #: appended strings still work via :meth:`from_dict` (legacy strings
    #: are auto-tagged as ``info``); new code should prefer
    #: :meth:`log_info` / :meth:`log_error`.
    log: List[Dict[str, str]] = field(default_factory=list)
    errors: Dict[str, List[str]] = field(default_factory=lambda: {"p1": [], "p2": []})
    #: Per-player sparse memory keyed "x:y" → {"paint": {...}, "stale": bool}
    memory_tiles: Dict[str, Dict[str, dict[str, Any]]] = field(
        default_factory=lambda: {"p1": {}, "p2": {}}
    )
    #: Red harvested by owner's harvesters: night = planning calendar day stamp.
    harvest_log: Dict[str, List[dict[str, Any]]] = field(
        default_factory=lambda: {"p1": [], "p2": []}
    )
    #: Last-seen tiles from each player's probe camera (key ``x:y`` → snapshot).
    #: Updated every simulation step while a probe exists; kept after removal.
    probe_intel: Dict[str, Dict[str, dict[str, Any]]] = field(
        default_factory=lambda: {"p1": {}, "p2": {}}
    )
    #: Surface visit ledger per owner. Each entry keyed by ``"x:y"`` is a
    #: dict ``{n, d, h}`` carrying:
    #:   - ``n`` — visit count (drives the tiered shading overlay in
    #:     :meth:`_trail_markup`).
    #:   - ``d`` — day of the LAST visit (so a watcher can tell at a
    #:     glance which trails were laid tonight vs days ago). May be
    #:     ``None`` for legacy v0.3.5+ entries with no day stamp.
    #:   - ``h`` — id of the harvester that recorded the last visit
    #:     (so the OBS tooltip can read "p1 · harvester_p1 stepped
    #:     here · Day 4"). May be ``None`` for legacy entries.
    #: Persisting across nights keeps the map a permanent record of
    #: where a House has operated; the new metadata lets the trail
    #: ALSO communicate WHICH harvester did what THIS turn without
    #: replaying frames.
    track_paths: Dict[str, Dict[str, Dict[str, Any]]] = field(
        default_factory=lambda: {"p1": {}, "p2": {}}
    )
    #: Tiles where that owner's harvester converted RED→GREEN (``x:y``).
    track_harvests: Dict[str, Set[str]] = field(
        default_factory=lambda: {"p1": set(), "p2": set()}
    )
    #: Monotonic ids minted onto harvested squares (site ledger).
    square_uid_seq: int = 0
    #: Stored red harvested from field operations (picked up haul), max HOARD_CAPACITY.
    hoard_squares: Dict[str, List[Dict[str, Any]]] = field(
        default_factory=lambda: {"p1": [], "p2": []}
    )
    #: Squares already catapulted off-world (post-vault storage). Empty
    #: until §4 ship-to-Earth mechanics land; the shape mirrors
    #: :attr:`hoard_squares` so the UI can re-use the same renderer.
    shipped_squares: Dict[str, List[Dict[str, Any]]] = field(
        default_factory=lambda: {"p1": [], "p2": []}
    )

    #: Credits balance per seat (v0.8.0 — Orbit phase economy). Each
    #: seat receives :data:`ORBIT_CREDITS_PER_TURN` at the start of
    #: every Orbit phase and spends them on builds / repair via
    #: :class:`~sea_of_colours.game.policy.OrbitAction` actions.
    credits: Dict[str, int] = field(
        default_factory=lambda: {"p1": 0, "p2": 0}
    )

    #: v0.9.x — BLUE bank per seat (the fissile spend surface). Seeded
    #: with :data:`STARTING_BLUE_PURITY` at game birth and topped up
    #: nowhere automatically; harvested BLUE lives as vault parcels.
    #: ``blue_purity_available`` reads ``blue_bank + sum(blue parcels)``
    #: and ``debit_blue_purity`` spends the bank first, then parcels.
    #: Hybrid model: a clean number for "you have N blue" that doesn't
    #: occupy a vault slot, while harvested blue stays tier-graded.
    blue_bank: Dict[str, int] = field(
        default_factory=lambda: {"p1": 0, "p2": 0}
    )

    #: v0.9.x — BLUE-SIGN: per-pocket radiative signature overlay
    #: (RULEBOOK §4.6). Computed once at season birth from the
    #: generation-time blue geometry (so it's STATIC and PERSISTENT —
    #: it does NOT fade when the blue is depleted) and visible from
    #: orbit to EVERY house regardless of fog. Each entry is a fuzzy,
    #: off-center, noisy blob ``{"id", "center": [x, y],
    #: "cells": [[x, y, intensity], ...]}``. The smear deliberately
    #: does not reveal the exact blue squares, extent, or purity — only
    #: a rough area to maneuver toward.
    blue_sign: List[Dict[str, Any]] = field(default_factory=list, repr=False)

    #: v1.x — REDSIGN: discovery-triggered public beacons over pure-RED
    #: (purity 255) seams (RULEBOOK §4.11). UNLIKE blue-sign, redsign is
    #: NOT computed at birth — a region is minted the first time ANY seat's
    #: probe/harvester sees a pure-RED cell, and from then on is visible to
    #: EVERY house regardless of fog until the seam is spent — the beacon
    #: RETIRES when its last pure cell is harvested (``live`` flips False;
    #: see :meth:`_retire_redsign_if_spent`), because an announcement about
    #: a jackpot has nothing to say once the jackpot is banked. RULEBOOK
    #: §4.11 claimed the opposite until v1.33; this comment claimed it too,
    #: directly above the code that does the retiring. Each entry is
    #: a fuzzy, off-centre smear ``{"id", "center": [x, y],
    #: "cells": [[x, y, intensity], ...], "day": <discovered>}`` — the
    #: same shape as ``blue_sign`` plus the discovery ``day`` so replay can
    #: light it up at the right tick. Anonymous: never names the spotter.
    redsign: List[Dict[str, Any]] = field(default_factory=list, repr=False)
    #: Pure-RED cells already attributed to a redsign seam (``"x,y"`` keys),
    #: so re-sightings / the rest of a discovered seam don't re-trigger.
    redsign_seen: Set[str] = field(default_factory=set, repr=False)

    #: Probe stock per seat (v0.8.0). Starts at :data:`PROBE_INITIAL_STOCK`,
    #: consumed one-for-one by ``ProbeMove`` during PRAXIS, replenished
    #: via the Orbit ``BuildProbe`` action.
    probe_stock: Dict[str, int] = field(
        default_factory=lambda: {
            "p1": PROBE_INITIAL_STOCK,
            "p2": PROBE_INITIAL_STOCK,
        }
    )

    #: v0.9.3 — Stockpiled interdiction weapons per seat. Each weapon
    #: is built during the Orbit phase (``Build{Emp,Mine,Chaff}Action``)
    #: against blue purity + credits; the resulting stock is then
    #: drained by the matching night-phase launch move
    #: (``EmpLaunchMove`` / ``MineLayMove`` / ``ChaffFlareMove``).
    #:
    #: Shape: ``{seat: {"emp": int, "chaff": int}}`` (v1.31 — the
    #: ``mine`` key retired with the caltrop; loads refund any held).
    #: All slots default to ``0`` — a fresh seat owns no weapons; a
    #: night-move that finds zero stock for its weapon is wasted as
    #: a yellow log line ("``emp_launch: no EMP in stockpile``"),
    #: never live-debited mid-night.
    weapon_stock: Dict[str, Dict[str, int]] = field(
        default_factory=_fresh_weapon_counters
    )

    #: v0.9.5 — lifetime "weapons used" counter per seat. Mirrors the
    #: shape of :attr:`weapon_stock` but is append-only across the
    #: session: every successful EMP launch / mine lay / chaff flare
    #: bumps the matching counter by 1. The VAULT panel renders this
    #: as a "USED" bay next to the "AVAILABLE" bay so the seat can
    #: see what they've already spent across the season. The agent
    #: view surfaces it for symmetry with the existing
    #: ``weapon_stock`` block. Keyed by seat → ``{emp, mine, chaff}``.
    weapons_used: Dict[str, Dict[str, int]] = field(
        default_factory=_fresh_weapon_counters
    )

    #: Stashed Orbit-phase action queue per seat. ``None`` until the seat
    #: locks their Orbit submission; mirrors :attr:`pending_policies`
    #: for the night queue. Cleared by :class:`OrbitResolver` once both
    #: seats are ready and the Orbit phase resolves.
    pending_orbit_actions: Dict[str, Optional[List[Any]]] = field(
        default_factory=lambda: {"p1": None, "p2": None}
    )

    #: Append-only history of every Orbit catapult settlement: one
    #: entry per game-day with ``{day, auction, tithe, jettison}``
    #: sub-blocks. Used by the agent view to surface
    #: ``last_catapult_results`` so seats can read who shipped /
    #: jettisoned what last turn before bidding again. Persists across
    #: phases.
    catapult_history: List[Dict[str, Any]] = field(default_factory=list)

    #: v0.9.9 — Cumulative tier-multiplier-weighted shipped score per
    #: seat. Equals ``sum(effective_purity * RED_QUALITY_MULTIPLIER[tier])``
    #: across every parcel that has EVER cleared the catapult for that
    #: seat. The Orbit resolver bumps this counter at settlement time
    #: (once per day per seat) so the HUD scoreboard can render the
    #: season totals in O(1) without re-walking ``shipped_squares``
    #: every refresh. Same value :meth:`score_for` returns; the field
    #: simply caches it so the API can fan it out per seat without
    #: dragging the full shipped bay through every status response.
    cumulative_shipped_score: Dict[str, float] = field(
        default_factory=lambda: {"p1": 0.0, "p2": 0.0}
    )

    #: Human-readable name per seat for bot / agent seats (Latin
    #: ``"<Colour> <Animal>"`` handles minted at season birth — see
    #: :mod:`sea_of_colours.game.player_names`). Human seats are absent;
    #: callers fall back to the seat-colour label. Drives the
    #: end-of-game results screen and any named scoreboard.
    player_names: Dict[str, str] = field(default_factory=dict)

    #: v0.9.18 — per-seat player identity profiles for display (names, tags,
    #: colors). Stores ``{seat: {"display_name": str, "tag": str, "color": hex}}``.
    #: Defaults are backfilled in ``__post_init__`` from ``SEAT_LABEL`` +
    #: ``SEAT_DEFAULT_COLORS``. Bot seats merge ``player_names`` into display_name.
    player_profiles: Dict[str, Dict[str, str]] = field(default_factory=dict)

    #: Per-seat running season tallies for the end-of-game screen.
    #: Only the values that can't be cheaply derived from other state
    #: are accumulated here: ``credits_awarded`` (so ``spent`` =
    #: awarded − current balance), ``harvesters_built`` (paid Orbit
    #: BUILD_HARVESTER count, excludes the free starting harvester), and
    #: ``blue_spent`` (total BLUE purity debited for refine / weapons).
    #: Harvest totals + the red-by-day series are derived from
    #: :attr:`harvest_log`; green disposal from :attr:`catapult_history`.
    season_stats: Dict[str, Dict[str, float]] = field(default_factory=dict)

    #: v1.0 — set True when the post-final-night settlement orbit opens
    #: (RULEBOOK §4 end-of-season). During the final orbit only
    #: refine / ship / green-flush actions are legal; once it resolves
    #: the session transitions straight to ``SEASON_COMPLETE`` instead
    #: of opening another planning night.
    final_orbit: bool = False
    #: Last night's playback — one frame per executed (or wasted) move.
    last_night_replay: List[Dict[str, Any]] = field(default_factory=list)

    #: Per-cell collision markers (RULEBOOK §3.6 v0.7.3). Keyed by
    #: ``"x:y"`` → ``{"day": int, "owners": List[PlayerId]}``. Every
    #: harvester-on-harvester collision (drop-on, step-into, or
    #: pass-through swap) records the cell, the planning day on which
    #: it happened, and the seats whose harvesters were involved. The
    #: overlay decays after exactly one game day (``day_now -
    #: mark.day > 1`` ⇒ purged) so a watcher sees yesterday's
    #: crashes as a temporary scar on the planet's surface but the
    #: ledger doesn't accumulate forever. Mirrors the ``fresh_visits``
    #: 24h-attribution gate in :meth:`_trail_summary`.
    collision_marks: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    #: Transient per-move collision outbox. The session helpers
    #: (:meth:`_record_collision` / :meth:`try_swap_collision`) push
    #: a structured event into this list whenever a harvester-on-
    #: harvester crash resolves; the :class:`NightSimulator` drains
    #: it after each ``_apply_one`` and folds the events onto the
    #: matching replay frame as ``frame["collisions"]``. The frontend
    #: uses that payload to drive the v0.7.3 collision-ring animation
    #: + the wreckage glyph overlay. NOT persisted — purely a
    #: simulator <-> replay-frame channel within a single night.
    pending_collision_events: List[Dict[str, Any]] = field(default_factory=list)

    #: Transient per-move probe-crush outbox (v0.7.4). Mirrors the
    #: collision channel but for harvester-over-probe events: every
    #: time :meth:`consume_probes_at` deletes a probe it queues a
    #: ``{at, probe_id, probe_owner}`` record here. The simulator
    #: drains the list after each ``_apply_one`` and folds it onto
    #: the matching replay frame as ``frame["crushed_probes"]`` so
    #: the watcher can fire a small pixel-splash animation on the
    #: crushed cell. NOT persisted — purely a simulator <-> replay-
    #: frame channel within a single night.
    pending_probe_crush_events: List[Dict[str, Any]] = field(default_factory=list)

    #: v0.9.10 — permanent markers for destroyed harvesters. Keyed by
    #: ``"x:y"`` string, each value holds ``{owner, harvester_id, day}``
    #: to render a grey "gravestone" harvester icon in the grid. Unlike
    #: collision_marks (which decay after 1 day), these persist for the
    #: entire season so players can see where units were left without
    #: pickup.
    destroyed_harvester_markers: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    #: v0.9.11 — Per-night orbital activity tally, keyed by day → seat →
    #: ``{dropped, picked_up, picked_up_damaged}`` (counts only, NO
    #: coordinates). Stamped at end-of-night in :meth:`NightSimulator.run`
    #: from the night's drop/pickup frames. Observable by every seat
    #: (RULEBOOK §3.15.x — orbital launch/recovery is public, landing
    #: locations stay private). Feeds the Pre-Orbital Recap + agent view.
    orbital_activity_by_day: Dict[str, Dict[str, Dict[str, int]]] = field(
        default_factory=dict
    )

    #: RULEBOOK §3.9.2 — ONE OUTING PER HARVESTER PER NIGHT. A harvester makes
    #: at most one drop→walk→pickup sortie each night; once it has been deployed
    #: this night it may NOT be re-dropped, even after it lifts back to orbit.
    #: Keyed by day → set of harvester ids already deployed that night, so it is
    #: robust both through the night simulator (reset in :meth:`NightSimulator.run`)
    #: and for direct ``try_drop_unit`` callers (day key naturally scopes it).
    #: Transient — night resolution is atomic, so this is NOT persisted.
    deployed_harvesters_by_day: Dict[int, set] = field(default_factory=dict)

    #: v1.6 — Season-long attacker→victim combat attribution ("kill feed").
    #: Shape: ``{stat: {attacker_seat: {victim_seat: count}}}``. Stats:
    #: ``probes_crushed`` (harvester rode over a probe), ``probes_superseded``
    #: (a newer probe destroyed an older one on the same cell), ``emp_probes``
    #: (probe fried inside an EMP cloud), ``snap_probes`` (probe shot off the
    #: board by a SNAP — v1.50; split out for the same reason
    #: ``snap_harvesters`` is, see below, and note that before the split these
    #: landed in ``emp_probes`` and made agents that never bought an EMP look
    #: like they had), ``emp_harvesters`` (DISTINCT harvester
    #: smothered by an EMP — counted once per unit per season),
    #: ``snap_harvesters`` (harvester crippled by a SNAP, whether caught on the
    #: square, walked onto it, or turned back from landing on it — v1.36; kept
    #: apart from ``harv_damaged`` so a shot hull cannot be read as a rammed
    #: one), ``chaff_jams``
    #: (a rival action-slot cancelled by an orbital chaff), ``harv_damaged``
    #: (harvester-on-harvester collision damage), ``harv_lost_chaff`` (a
    #: harvester stranded at dawn because its egress was chaff-cancelled).
    #: The victim seat may equal the attacker (you can crush your own probe),
    #: so the matrix is a full NxN. Incremented at the event source during the
    #: night sim; read at end-game by :func:`get_endgame_summary`.
    combat_attrib: Dict[str, Dict[str, Dict[str, int]]] = field(
        default_factory=dict
    )

    #: v1.6 — Dedupe ledger for DISTINCT EMP'd-harvester attribution. Keyed
    #: ``"{attacker_seat}|{harvester_id}"`` → True once counted, so a unit
    #: smothered for multiple hours/nights is only tallied once per attacker.
    emp_harv_seen: Dict[str, bool] = field(default_factory=dict)

    #: v1.6 — Per-seat count of queued moves that resolved INVALID / cancelled
    #: at praxis execution (parse-waste + illegal-at-runtime + EMP-smothered +
    #: chaff-cancelled slots). A "wasted turns" personal stat.
    moves_cancelled: Dict[str, int] = field(default_factory=dict)

    #: v1.6 — transient per-night bookkeeping for harvester-lost-via-chaff
    #: attribution: ``{harvester_id: attacker_seat}`` recording the chaffer
    #: whose flare last cancelled this unit's egress. Consulted at dawn when a
    #: harvester is stranded. Not persisted (rebuilt each night).
    _chaff_egress_block: Dict[str, str] = field(default_factory=dict)

    #: v1.8 — Canonical per-day COMBAT EVENT feed, keyed by day(str) →
    #: ``[event, ...]``. Records the EPHEMERAL weapon effects that leave
    #: no other on-map artifact (EMP salvos + clouds, chaff flares) plus
    #: their per-unit impact, so an agent can read *what happened last
    #: night* without reconstructing it from dissipated clouds. Event
    #: shapes (``type`` discriminates):
    #:   * ``emp``   — a salvo: ``{owner, targets:[[x,y]..], radius,
    #:     cells:[[x,y]..], hours:[h..], day}``. PUBLIC (RULEBOOK §5.1 —
    #:     every seat sees the launch + cloud).
    #:   * ``emp_hit`` — a harvester smothered: ``{unit, victim, by:[seat..],
    #:     hours:[h..], day}``. Merged per (day, unit). Self-only detail.
    #:   * ``chaff`` — a flare: ``{owner, hours:[h..], day}``. PUBLIC.
    #:   * ``chaff_jam`` — a seat's slot jammed: ``{victim, by:[seat..],
    #:     units:[uid..], hours:[h..], day}``. Merged per (day, victim).
    #:     Self-only detail.
    #: Fog-gating is applied by the READER (:meth:`_last_night_recap`):
    #: PUBLIC events go to everyone; ``*_hit``/``*_jam`` only to the
    #: victim seat. Persisted so the recap survives a reload.
    combat_events_by_day: Dict[str, List[Dict[str, Any]]] = field(
        default_factory=dict
    )

    #: v1.8 — Decaying SPATIAL scar for EMP clouds, keyed ``"x:y"`` →
    #: ``{owners:[seat..], hours:[h..], day:int}``. Stamped for every cell
    #: an EMP cloud covered when it was fired (:meth:`apply_emp_launch`)
    #: and pruned after one game day (:meth:`_prune_emp_marks`), mirroring
    #: :attr:`collision_marks`. Surfaced on the dense/observer views as
    #: ``cell["combat"]["emp"]`` so the map itself answers "an EMP was
    #: active in this square last night, hours H..H". EMP is public
    #: (§5.1) so this shows on any non-fog cell.
    emp_marks: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    #: v0.9.13 — Per-night ORDERED orbital event log, keyed by day → seat →
    #: ``[{"tag": ..., "carrying"?: bool, "damaged"?: bool}, ...]`` in the
    #: order the actions were witnessed (NO coordinates). Stamped at
    #: end-of-night alongside :attr:`orbital_activity_by_day` from the same
    #: night frames, and persisted in the session blob so the Pre-Orbital
    #: Recap can render the chronological event list without depending on
    #: the (separately-stored / later-fetched) replay-frame table. Public
    #: per RULEBOOK §3.15.x.
    orbital_events_by_day: Dict[str, Dict[str, List[Dict[str, Any]]]] = field(
        default_factory=dict
    )

    #: v0.9.11 — Per-day station-observation snapshots, keyed by day →
    #: ``{"pre": {seat: obs}, "post": {seat: obs}}`` where each ``obs``
    #: is the EXACT reading (see :meth:`_station_observation`). The
    #: ``pre`` snapshot is stamped at end-of-night; the ``post`` snapshot
    #: at end-of-settlement. Replay fuzzes per-viewer client-side; the
    #: live agent view fuzzes opponents server-side. Persisted so the
    #: replay scrubber can serve historical readings.
    station_obs_by_day: Dict[str, Dict[str, Dict[str, Any]]] = field(
        default_factory=dict
    )

    #: v0.9 — Active EMP clouds on the planet (RULEBOOK §5).
    #:
    #: Each entry is a dict with shape::
    #:
    #:     {
    #:       "owner": "p1" | "p2",
    #:       "cx": int, "cy": int,
    #:       "radius": int,          # Chebyshev; snapshot of EMP_RADIUS at launch
    #:       "hours_remaining": int, # snapshot of EMP_CLOUD_HOURS, ticked down per hour
    #:       "launched_at_hour": int,
    #:       "launched_at_day": int,
    #:     }
    #:
    #: Persisted across the orbit phase so a cloud that bleeds into
    #: dawn cleanly evaporates at sunrise (the simulator zeroes
    #: ``hours_remaining`` after the last hour of the night and the
    #: list is pruned on the next ``tick_emp_clouds`` call). Open
    #: visibility: every seat reads from this list when assembling
    #: a world view; clouds are not gated by fog.
    emp_clouds: List[Dict[str, Any]] = field(default_factory=list)

    #: v1.36 — Active SNAP clouds (RULEBOOK §4.9.4). Same row shape as
    #: :attr:`emp_clouds` so the replay and the FX path can treat them
    #: as one family, but kept in its own list rather than sharing one
    #: with a ``kind`` discriminator.
    #:
    #: That separation is the retirement plan. SNAP is new and may not
    #: survive; if it does not, this list goes the way ``mines`` did —
    #: left standing and always empty — and no EMP code has to be
    #: untangled from it first. It also keeps the two weapons' very
    #: different rules from sharing a loop that would need a branch on
    #: every line: SNAP's radius is 0, its cloud lasts one hour, and it
    #: damages harvesters where the EMP merely smothers them.
    snap_clouds: List[Dict[str, Any]] = field(default_factory=list)

    #: v0.9 — Active caltrop mines on the surface (RULEBOOK §5).
    #:
    #: Keyed by ``"x:y"`` (JSON-safe) → ``{owner, laid_at_hour,
    #: laid_at_day}``. Owner-visible by default; other seats see a
    #: mine only via probe-witnessed echo (recorded into
    #: ``probe_intel[other_player]`` at lay time). A harvester
    #: stepping onto a mined tile consumes the mine (see
    #: :meth:`try_step_unit`).
    mines: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    #: v0.9 — Transient EMP / chaff replay outboxes. Drained by the
    #: simulator after each apply tick; folded onto the relevant
    #: replay frame as ``frame["emp"]`` / ``frame["chaff"]``. NOT
    #: persisted. (v1.31 — ``pending_mine_events`` went with the
    #: caltrop; ``replay_push_scene`` still accepts a ``mine=`` kwarg so
    #: archived frames keep their shape.)
    pending_emp_events: List[Dict[str, Any]] = field(default_factory=list)
    pending_chaff_events: List[Dict[str, Any]] = field(default_factory=list)
    pending_snap_events: List[Dict[str, Any]] = field(default_factory=list)

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    #: Human-friendly season label (``Aurora_Falcon``-style). Optional —
    #: stays ``None`` for legacy sessions loaded from a v0.4.0 payload
    #: that predates :mod:`sea_of_colours.game.season_names`. The
    #: ``init_session`` engine handler always fills this in for new
    #: sessions created via the Snowflake proc or REST API.
    season_name: Optional[str] = None

    #: Per-square identity ledger (Snowflake-shaped seam — see ledger.py).
    #: Built once at generation; never mutated by play, so a harvested
    #: parcel keeps the *original* tile/purity in its certificate of origin.
    ledger: Optional[SquareLedger] = None

    #: Per-asset lifecycle records (Snowflake-ready, see
    #: :mod:`sea_of_colours.game.asset_ledger`). Stays in lockstep with
    #: :attr:`entities` for living assets and retains entries for
    #: destroyed ones (probes mostly) so the VAULT can list them under
    #: the "destroyed" header.
    asset_records: Dict[str, AssetRecord] = field(default_factory=dict)

    #: Planning-day cap for this season (v0.7.4). Stored per-session so
    #: a single database can host short-form 5-night demos and longer
    #: 7+-night runs without redeploying. ``SEASON_DAY_CAP`` is the
    #: default at construction time; the simulator and the
    #: snowpark/view payloads always read from this attribute so the
    #: terminal phase and the HUD progress bar both stay accurate. A
    #: legacy payload that predates the field hydrates back to the
    #: module default (see :meth:`from_dict`).
    season_day_cap: int = SEASON_DAY_CAP

    # --- Per-seat backfill --------------------------------------------
    def __post_init__(self) -> None:
        """Ensure every per-seat dict has a slot for every seat in
        :attr:`players`. Existing slots are left untouched so a
        ``from_dict`` round-trip keeps its data; missing slots are
        materialised with empty per-seat defaults. This is what lets
        v0.9.6 N-seat sessions reuse the same dataclass shape as the
        legacy 2-seat builds.

        Also normalises :attr:`players` to a tuple (callers sometimes
        hand us a list / mutable iterable) and clamps to
        :data:`MAX_SEATS` so a typo / API mistake can't allocate an
        unbounded number of seat slots.
        """
        seats = tuple(self.players or ("p1", "p2"))
        seats = seats[:MAX_SEATS]
        if not seats:
            seats = ("p1", "p2")
        self.players = seats

        # Seat-keyed defaults — every dict-shaped field that the rest
        # of the engine iterates over. Stays in lockstep with the
        # ``default_factory`` shapes declared above so a backfilled
        # slot is indistinguishable from one that was created by the
        # constructor itself.
        for seat in seats:
            self.probe_seq.setdefault(seat, 0)
            self.pending_policies.setdefault(seat, None)
            self.errors.setdefault(seat, [])
            self.memory_tiles.setdefault(seat, {})
            self.harvest_log.setdefault(seat, [])
            self.probe_intel.setdefault(seat, {})
            self.track_paths.setdefault(seat, {})
            self.track_harvests.setdefault(seat, set())
            self.hoard_squares.setdefault(seat, [])
            self.shipped_squares.setdefault(seat, [])
            self.credits.setdefault(seat, 0)
            self.blue_bank.setdefault(seat, 0)
            self.probe_stock.setdefault(seat, PROBE_INITIAL_STOCK)
            self.weapon_stock.setdefault(seat, {})
            self.weapons_used.setdefault(seat, {})
            # v1.36 — off the stamped price table, not a literal pair, so
            # a game that stocks a third weapon backfills a slot for it
            # and a game that does not never grows a key it cannot use.
            for _kind in self.weapon_prices():
                self.weapon_stock[seat].setdefault(_kind, 0)
                self.weapons_used[seat].setdefault(_kind, 0)
            self.pending_orbit_actions.setdefault(seat, None)
            self.agents.setdefault(seat, "human")
            self.cumulative_shipped_score.setdefault(seat, 0.0)
            self.season_stats.setdefault(
                seat,
                {"credits_awarded": 0.0, "harvesters_built": 0.0, "blue_spent": 0.0},
            )
            # v0.9.18 — backfill player_profiles with defaults for any
            # missing seat. Tag defaults to first 3 alnum chars of name (upper).
            if seat not in self.player_profiles:
                from sea_of_colours.game.player_names import SEAT_LABEL
                name = self.player_names.get(seat) or SEAT_LABEL.get(seat, seat.upper())
                tag = "".join(c for c in name if c.isalnum())[:3].upper() or seat.upper()[:3]
                color = SEAT_DEFAULT_COLORS.get(seat, "#FFFFFF")
                self.player_profiles[seat] = {
                    "display_name": name,
                    "tag": tag,
                    "color": color,
                }

        # Normalise visibility mode — anything other than ``"open"`` is
        # treated as hidden so a typo can't accidentally bypass fog.
        self.visibility_mode = (
            "open" if str(self.visibility_mode).lower() == "open" else "hidden"
        )

    def seat_color(self, seat: str) -> Tuple[int, int, int]:
        """Return the RGB color tuple for a seat from player_profiles or fallback.
        
        v0.9.18 — looks up the seat's custom color from ``player_profiles``,
        validates it's in ``SEAT_COLOR_PALETTE``, and returns the RGB tuple.
        Falls back to ``SEAT_DEFAULT_COLORS`` if the profile color is missing
        or invalid, then to ``ENTITY_OWNER_COLOR`` for legacy compatibility.
        """
        profile = self.player_profiles.get(seat, {})
        color_hex = profile.get("color", "").upper()
        if color_hex in SEAT_COLOR_PALETTE:
            return SEAT_COLOR_PALETTE[color_hex]
        # Fallback: check default colors, then legacy entity colors
        default_hex = SEAT_DEFAULT_COLORS.get(seat, "").upper()
        if default_hex in SEAT_COLOR_PALETTE:
            return SEAT_COLOR_PALETTE[default_hex]
        return ENTITY_OWNER_COLOR.get(seat, ENTITY_OWNER_DEFAULT)

    # --- Constructors -------------------------------------------------
    @classmethod
    def new(
        cls,
        width: int,
        height: int,
        seed: int,
        *,
        season_name: Optional[str] = None,
        season_day_cap: Optional[int] = None,
        skip_initial_orbit: bool = False,
        players: Optional[Sequence[str]] = None,
        agents: Optional[Mapping[str, str]] = None,
        visibility_mode: str = "hidden",
        player_profiles: Optional[Mapping[str, Mapping[str, str]]] = None,
        weapons_enabled: bool = True,
        signs_enabled: bool = True,
        tutorial: str = "",
    ) -> GameSession:
        # v1.28 — the seat list is normalised BEFORE the map is generated,
        # because the pure-RED floor scales with it (below). It used to sit
        # after, and the reorder is safe: this block reads only ``players``.
        #
        # v0.9.6 — normalise the seat list. ``players`` may arrive as a
        # list, tuple, or ``None`` (legacy 2-seat default). Clamp to
        # ``MAX_SEATS`` and dedupe while preserving order so a UI bug
        # that passes ``["p1","p1","p2"]`` still produces a valid game.
        if players is None:
            seat_ids: Tuple[str, ...] = ("p1", "p2")
        else:
            seen: List[str] = []
            for p in players:
                pid = str(p)
                if pid not in seen:
                    seen.append(pid)
                if len(seen) >= MAX_SEATS:
                    break
            seat_ids = tuple(seen) if seen else ("p1", "p2")

        # v1.28 — the jackpot count scales with the table (RULEBOOK §2.2).
        #
        # v1.24 put a flat floor of 2 under the pure count, which is right
        # for a duel and thin for four Houses: two seats can be handed a
        # jackpot each and two get none. But pinning the floor exactly to
        # the seat count trades that for a worse problem — the count becomes
        # a constant, so finding your first jackpot tells you precisely how
        # many others exist. Hence a random band per seat count; see
        # ``PURE_COUNT_BY_SEATS`` for the table and why four Houses are
        # allowed to come up short of one each.
        #
        # Sized off the NORMALISED seats, not ``players``, so a caller
        # passing ``["p1","p1","p2"]`` asks for a duel's board and not a
        # three-hander's — which is why the seat block above was moved ahead
        # of map generation. It reads nothing but its own argument, so the
        # move is safe.
        #
        # Set at the CALL SITE on purpose: ``GenerationParams`` has no idea
        # how many seats there are, and baking a seat-shaped default into
        # the dataclass would change the meaning of every standalone
        # generator call and every existing test. The dataclass default
        # stays a flat floor of 2 with no draw.
        #
        # MEASURED before shipping, 200 seeds at 40x28 with the separation
        # left at 12: counts of 2, 3 and 4 are met on 100% of seeds with no
        # pair closer than 12; 5 and 6 are still always met but soften the
        # separation on 2% and 15% of boards. Nothing in range ever drops
        # below 9 and an r4 probe spans 8, so "no single probe lights two
        # jackpots" — the property the 12 protects — holds across the whole
        # table without the separation needing to scale down. Re-measure
        # with ``scripts/_mapgen_pure_census.py`` if either dial moves.
        pure_lo, pure_hi = pure_count_range(len(seat_ids))
        params = GenerationParams(
            width=width,
            height=height,
            seed=seed,
            min_pure_count=pure_lo,
            max_pure_count=pure_hi,
        )
        # v1.29 — SOC_MAP_HALO is the escape hatch on the jackpot deposit
        # (§2.2). Grading roughly doubled the RED on a board, which is a
        # balance change best judged after play, so backing it out is a
        # server restart rather than a code edit. 0 restores byte-identical
        # v1.28 terrain; a fraction thins the mass without changing the
        # deposit's reach. Safe mid-season either way: a session persists
        # its GRID, not just its seed, so this only affects new boards.
        halo = map_halo_density()
        if halo <= 0.0:
            params.grade_pure_red = False
        elif halo != 1.0:
            params.pure_mass_core_chance *= halo
            params.pure_mass_fringe_chance *= halo
        grid = generate_grid(params)
        ledger = SquareLedger.from_grid(seed, grid)
        # ``season_name`` falls back to a deterministic ``Aurora_Falcon``-style
        # label generated from the seed (see :mod:`season_names`). Callers
        # who want an explicit name (e.g. the CLI orchestrator stamping a
        # tournament run with ``--name "Tempus_Vault"``) can pass it in.
        if season_name is None:
            from sea_of_colours.game.season_names import generate_season_name
            season_name = generate_season_name(seed)
        # Clamp the cap to a positive int; ``None`` falls back to the
        # module default so existing callers keep their old behaviour.
        cap = int(season_day_cap) if season_day_cap is not None else SEASON_DAY_CAP
        if cap < 1:
            cap = 1
        # Per-seat agent map — default any unspecified seat to "human".
        agent_map: Dict[str, str] = {}
        if agents:
            for k, v in agents.items():
                if k in seat_ids:
                    agent_map[k] = str(v) if v else "human"
        for s in seat_ids:
            agent_map.setdefault(s, "human")
        visibility = "open" if str(visibility_mode).lower() == "open" else "hidden"
        sess = cls(
            width=width,
            height=height,
            seed=seed,
            grid=_clone_grid(grid),
            ledger=ledger,
            season_name=season_name,
            season_day_cap=cap,
            players=seat_ids,
            agents=agent_map,
            visibility_mode=visibility,
            weapons_enabled=bool(weapons_enabled),
            signs_enabled=bool(signs_enabled),
            tutorial=str(tutorial or ""),
        )
        # Mint Latin names for the bot / agent seats (human seats keep
        # their seat-colour label). Deterministic from the seed so a
        # replay always reconstructs the same competitors.
        from sea_of_colours.game.player_names import (
            generate_player_names,
            generate_player_tag,
        )
        sess.player_names = generate_player_names(seed, seat_ids, agent_map)
        
        # v0.9.18 — populate player_profiles with custom names/tags/colors
        # if provided, or auto-generate for bot seats from player_names + agent strategy.
        if player_profiles:
            for seat in seat_ids:
                if seat in player_profiles:
                    profile = dict(player_profiles[seat])
                    # Validate color is in palette; fallback to default
                    color_hex = profile.get("color", "").upper()
                    if color_hex not in SEAT_COLOR_PALETTE:
                        color_hex = SEAT_DEFAULT_COLORS.get(seat, "#FFFFFF")
                    # Ensure tag is uppercased and clamped to 3 chars
                    tag = profile.get("tag", "")[:3].upper()
                    if not tag:
                        name = profile.get("display_name", "")
                        tag = "".join(c for c in name if c.isalnum())[:3].upper() or seat.upper()[:3]
                    sess.player_profiles[seat] = {
                        "display_name": profile.get("display_name", ""),
                        "tag": tag,
                        "color": color_hex,
                    }
        
        # For bot seats without custom profiles, overwrite defaults with Latin names + tags
        # v0.9.18 — bots get random colors from the palette (seeded by game seed)
        import random
        palette_colors = list(SEAT_COLOR_PALETTE.keys())
        color_rng = random.Random(seed)
        used_colors = set()
        
        for seat in seat_ids:
            # Skip if custom profile was provided
            if player_profiles and seat in player_profiles:
                used_colors.add(player_profiles[seat].get("color", "").upper())
                continue
            # For bot seats, use Latin name + name-derived tag + random color
            if agent_map.get(seat) != "human" and seat in sess.player_names:
                bot_name = sess.player_names[seat]
                # v0.9.18 — derive the 3-letter tag from the bot's display
                # name (not the strategy slug) so two RED_HARVEST bots get
                # DISTINCT tags, e.g. "Aureus Mustela" → "AMU", matching the
                # human rule of "a three-letter version of your name".
                tag = generate_player_tag(bot_name)
                
                # Pick a random color from the palette that hasn't been used yet
                available = [c for c in palette_colors if c not in used_colors]
                if not available:
                    available = palette_colors  # Wrap around if all colors used
                bot_color = color_rng.choice(available)
                used_colors.add(bot_color)
                
                sess.player_profiles[seat] = {
                    "display_name": bot_name,
                    "tag": tag,
                    "color": bot_color,
                }
        # __post_init__ backfilled human seats with color-label defaults (WHITE, YELLOW, etc.)
        
        LEDGER_STORE.save(sess.session_id, ledger)
        sess._spawn_defaults()
        sess._seed_starting_blue()
        # v0.9.x — precompute the static blue-sign overlay once from the
        # generation-time blue geometry (RULEBOOK §4.6). Persisted in
        # ``to_dict`` so it survives reload AND blue depletion.
        sess.blue_sign = sess._compute_blue_sign()
        sess._seed_asset_records()
        # v0.8.1 — day 1 opens directly in PLANNING. There is no
        # ORBIT phase on day 1 because seats have no parcels to ship /
        # tithe / jettison yet, and credits can only be spent in Orbit
        # anyway. v1.1 — no birth stipend: income arrives at the first
        # Orbit entry (day 2), so the very first night opens with 0
        # credits and the first spendable balance is one
        # :data:`ORBIT_CREDITS_PER_TURN` award, not two.
        sess.phase = Phase.PLANNING
        # ``skip_initial_orbit`` is preserved as a no-op for callers
        # that still pass it (tests, legacy harnesses). It used to
        # immediately settle the day-1 orbit; that's now the default.
        _ = skip_initial_orbit
        for p in sess.players:
            sess._remember_entire_visibility(p)
        return sess

    def _seed_starting_blue(self) -> None:
        """Seed every house's BLUE bank with :data:`STARTING_BLUE_PURITY`
        at season birth (the fissile stipend). Blue is the spend surface
        for refining + weapons; the bank is read by
        :meth:`blue_purity_available` and spent first by
        :meth:`debit_blue_purity`. Kept as a number (not a vault parcel)
        so it doesn't consume a hoard slot."""
        purity = int(STARTING_BLUE_PURITY)
        if purity <= 0:
            return
        for seat in self.players:
            self.blue_bank[seat] = int(self.blue_bank.get(seat, 0)) + purity

    def _compute_blue_sign(self) -> List[Dict[str, Any]]:
        """Build the static blue-sign radiative overlay (RULEBOOK §4.6).

        Reads the GENERATION-TIME blue cells from the ledger (so the
        signature reflects the original geometry and persists even after
        every pocket is mined out), clusters them into pockets, and for
        each pocket paints a fuzzy, OFF-CENTER, noisy blob. The blob
        deliberately does not coincide with the exact blue squares — the
        center is jittered off the true centroid and the radius bleeds
        out past the pocket — so it only hints at "blue is roughly
        around here", never the exact extent or purity.

        Deterministic in ``self.seed`` so every house (and every replay)
        sees the identical overlay. Returns a list of region dicts
        ``{"id", "center": [x, y], "cells": [[x, y, intensity], ...]}``.
        """
        import math

        # v1.32 — signs off is a per-GAME rule, and the cheapest honest
        # place to honour it is here: every caller reads the returned list,
        # so an empty one means no seat can see a signature and nothing
        # downstream needs a second switch.
        if not self.signs_enabled:
            return []

        blue_cells: List[Tuple[int, int]] = []
        entries = getattr(self.ledger, "entries", {}) or {}
        for row in entries.values():
            try:
                if str(row.get("lineage")) != "natural":
                    continue
                if int(row.get("tile_at_generation", -1)) != int(Tile.BLUE):
                    continue
                blue_cells.append((int(row["x"]), int(row["y"])))
            except (TypeError, ValueError, KeyError):
                continue
        if not blue_cells:
            return []

        blue_set = set(blue_cells)
        # 8-connected components = blue pockets.
        seen: Set[Tuple[int, int]] = set()
        components: List[List[Tuple[int, int]]] = []
        for start in blue_cells:
            if start in seen:
                continue
            stack = [start]
            seen.add(start)
            comp: List[Tuple[int, int]] = []
            while stack:
                cx, cy = stack.pop()
                comp.append((cx, cy))
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nb = (cx + dx, cy + dy)
                        if nb in blue_set and nb not in seen:
                            seen.add(nb)
                            stack.append(nb)
            components.append(comp)

        rng = random.Random(int(self.seed) * 2654435761 + 90133)
        regions: List[Dict[str, Any]] = []
        for idx, comp in enumerate(components):
            n = len(comp)
            cx = sum(c[0] for c in comp) / n
            cy = sum(c[1] for c in comp) / n
            base_r = max(1.5, math.sqrt(n / math.pi))
            # Off-center bias: jitter the signature center off the true
            # pocket centroid so the brightest reading isn't the blue.
            scx = cx + rng.uniform(-0.7, 0.7) * base_r
            scy = cy + rng.uniform(-0.7, 0.7) * base_r
            # Fuzzy radius bleeds past the pocket footprint.
            radius = base_r * rng.uniform(1.4, 2.2) + 1.0
            bb = int(math.ceil(radius)) + 2
            cells: Dict[Tuple[int, int], float] = {}
            y0 = max(0, int(scy) - bb)
            y1 = min(self.height, int(scy) + bb + 1)
            x0 = max(0, int(scx) - bb)
            x1 = min(self.width, int(scx) + bb + 1)
            for gy in range(y0, y1):
                for gx in range(x0, x1):
                    d = math.hypot(gx - scx, gy - scy)
                    falloff = 1.0 - (d / radius)
                    if falloff <= 0.0:
                        continue
                    inten = falloff * rng.uniform(0.55, 1.25)
                    # Ragged fringe: drop ~half of the faint edge cells.
                    if inten < 0.18 and rng.random() < 0.5:
                        continue
                    if inten <= 0.08:
                        continue
                    inten = max(0.0, min(1.0, inten))
                    key = (gx, gy)
                    if inten > cells.get(key, 0.0):
                        cells[key] = inten
            if not cells:
                continue
            cell_list = [
                [int(x), int(y), round(float(w), 3)]
                for (x, y), w in sorted(cells.items())
            ]
            regions.append({
                "id": f"bluesign-{idx}",
                "center": [round(scx, 2), round(scy, 2)],
                "cells": cell_list,
            })
        return regions

    def _seed_asset_records(self) -> None:
        """Register lifecycle records for every Entity that exists at
        session birth (default roster: orbital harvester + orblift per
        player). Saves the ledger through :data:`ASSET_LEDGER_STORE`."""
        for ent in self.entities.values():
            self.asset_records[ent.id] = AssetRecord(
                asset_id=ent.id,
                asset_type=ent.entity_type,
                owner=ent.owner,
                session_id=self.session_id,
                created_on_day=self.day,
                first_deployed_day=(
                    self.day if ent.x is not None and ent.y is not None else None
                ),
                last_seen_x=ent.x,
                last_seen_y=ent.y,
            )
        self._persist_asset_ledger()

    def _persist_asset_ledger(self) -> None:
        ASSET_LEDGER_STORE.save(
            self.session_id,
            AssetLedger(session_id=self.session_id, records=dict(self.asset_records)),
        )

    def _ensure_asset_record(self, ent: Entity) -> AssetRecord:
        """Idempotently materialise an :class:`AssetRecord` for ``ent``.

        Used by the lifecycle helpers so old sessions deserialised from
        v0.3.7 payloads — which had no ``asset_records`` field — still
        accumulate stats from this night forward.
        """
        rec = self.asset_records.get(ent.id)
        if rec is None:
            rec = AssetRecord(
                asset_id=ent.id,
                asset_type=ent.entity_type,
                owner=ent.owner,
                session_id=self.session_id,
                created_on_day=self.day,
                last_seen_x=ent.x,
                last_seen_y=ent.y,
            )
            self.asset_records[ent.id] = rec
        return rec

    def _note_asset_deployed(self, asset_id: str) -> None:
        """Mark an asset's ``first_deployed_day`` if not yet set, and
        sync its last-seen position. Cheap to call repeatedly."""
        ent = self.entities.get(asset_id)
        if ent is None:
            return
        rec = self._ensure_asset_record(ent)
        if rec.first_deployed_day is None and ent.x is not None:
            rec.first_deployed_day = self.day
        if ent.x is not None and ent.y is not None:
            rec.last_seen_x = ent.x
            rec.last_seen_y = ent.y

    def _note_asset_harvested_red(self, asset_id: str) -> None:
        """Bump a harvester's lifetime ``total_red_harvested``."""
        ent = self.entities.get(asset_id)
        if ent is None:
            return
        rec = self._ensure_asset_record(ent)
        rec.total_red_harvested += 1

    def _note_asset_destroyed(
        self, asset_id: str, reason: Optional[str] = None
    ) -> None:
        """Mark an asset as destroyed on the current day. The entry is
        retained in :attr:`asset_records` (and surfaced under the
        destroyed list in :meth:`inventory_pack`) even after the
        :class:`Entity` is removed from :attr:`entities`."""
        rec = self.asset_records.get(asset_id)
        if rec is None:
            return
        if rec.destroyed_on_day is None:
            rec.destroyed_on_day = self.day
            rec.destroyed_by = reason

    def advance_asset_day_counters(self) -> None:
        """Increment ``total_days_on_surface`` for every living asset
        currently on the surface. Called once at the end of a night."""
        for ent in self.entities.values():
            if ent.x is None:
                continue
            rec = self._ensure_asset_record(ent)
            if rec.destroyed_on_day is not None:
                continue
            rec.total_days_on_surface += 1
        self._persist_asset_ledger()

    def _spawn_defaults(self) -> None:
        """Seed each seat in :attr:`players` with a harvester + orblift
        pair, both starting in orbit (``x = y = None``). v0.9.6 extends
        this from a hard-coded p1/p2 pair to an N-seat loop so a 3- or
        4-player game gets the same starting roster per seat without
        adding more code paths.
        """
        entities: Dict[str, Entity] = {}
        for seat in self.players:
            entities[f"harvester_{seat}"] = Entity(
                f"harvester_{seat}",
                "harvester",
                seat,
                None,
                None,
                carrying_red=False,
            )
            entities[f"orblift_{seat}"] = Entity(
                f"orblift_{seat}", "orblift", seat, None, None
            )
        self.entities = entities

    # --- Visibility ---------------------------------------------------
    def tiles_visible_now(self, player: PlayerId) -> Set[Tuple[int, int]]:
        vis: set[Tuple[int, int]] = set()
        w, h = self.width, self.height
        for e in self.entities.values():
            if e.owner != player:
                continue
            if e.x is None:
                continue
            if e.entity_type == "harvester":
                vis |= _harvester_vision_disk(e.x, e.y, w, h)
            elif e.entity_type == "probe":
                vis |= _probe_vision_disk(e.x, e.y, w, h)
        return vis

    def _remember_entire_visibility(self, player: PlayerId) -> None:
        mask = self.tiles_visible_now(player)
        self._blend_memory(player, mask)

    def _blend_memory(self, player: PlayerId, visible: Set[Tuple[int, int]]) -> None:
        mem = self.memory_tiles[player]
        wk = {(x, y) for x in range(self.width) for y in range(self.height)}
        for xy in visible:
            k = f"{xy[0]}:{xy[1]}"
            _cell = self.grid[xy[1]][xy[0]]
            entry: dict[str, Any] = {
                "paint": dict(cell_to_paint(_cell)),
                "stale": False,
                # v1.22 — freeze tile+purity alongside the paint so a stale
                # memory tile can quote the number it remembers, not just the
                # dither band. Mirrors _probe_tile_snapshot, which already did.
                "tile": int(_cell.tile),
                "purity": int(_cell.purity),
            }
            # v1.8 — freeze the trail into the memory tile at sighting time
            # (bug #1). See _probe_tile_snapshot: stale cells must serve the
            # last-observed trail, not the live ledger, so traffic on tiles
            # the seat can no longer see doesn't keep growing on the map.
            _tsum = self._trail_summary(xy[0], xy[1])
            if _tsum is not None:
                entry["trail"] = _tsum
            _tmk = self._trail_markup(xy[0], xy[1])
            if _tmk is not None:
                entry["trail_markup"] = _tmk
            mem[k] = entry
        for xy in wk:
            k = f"{xy[0]}:{xy[1]}"
            if xy in visible:
                continue
            if k not in mem:
                continue
            entry = mem[k]
            entry["stale"] = True

    def _entities_on_tile_sorted(self, x: int, y: int) -> List[Entity]:
        cands = [e for e in self.entities.values() if e.x == x and e.y == y]
        cands.sort(key=lambda e: public_entity_title(e))
        return cands

    def _probe_tile_snapshot(
        self, x: int, y: int, *, exclude_entity_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Capture an echo-intel snapshot of cell ``(x, y)``.

        ``exclude_entity_id`` drops a specific entity from both the
        ``occupants`` list AND the top-entity ``glyph_ch`` / ``glyph_fg``
        fields. Used by :meth:`_pulse_vision_intel` when a harvester
        observes its OWN cell (RULEBOOK §3.10/§3.12): the harvester's
        transient presence is communicated by the universal trail
        overlay, not by stamping a phantom-harvester glyph onto every
        echo it leaves behind. Other entities on the same tile (a
        friendly probe, a spotted enemy) still flow through normally —
        that's genuine intel the player should keep.
        """
        cell = self.grid[y][x]
        paint = dict(cell_to_paint(cell))
        occ_entities = self._entities_on_tile_sorted(x, y)
        if exclude_entity_id is not None:
            occ_entities = [e for e in occ_entities if e.id != exclude_entity_id]
        occ = [occupant_wire(en, self) for en in occ_entities]
        # Snapshot the *typed* tile + purity alongside the painted view.
        # The agent's structured view needs first-class tile data
        # (without parsing rendered colour characters) so it can include
        # echo-only RED tiles in :func:`build_agent_view` as stale-but-
        # actionable targets after the harvester leaves orbit and loses
        # live line-of-sight.
        base = {
            "paint": paint,
            "occupants": occ,
            "tile": int(cell.tile),
            "purity": int(cell.purity),
            # v0.9.13 — stamp the recording day so the renderer can expire the
            # entity-glyph "ghost" after a night. The terrain echo (paint /
            # tile / purity) persists as remembered ground; the stale unit
            # silhouette does not, so a dead probe or a moved harvester stops
            # leaving permanent ghost fields across the map.
            "day_seen": int(self.day),
        }
        # v1.8 — FREEZE the trail at snapshot time (bug #1: phantom trails).
        # Trails used to be re-derived LIVE from the global ledger and
        # stamped onto every visible cell — including echo tiles the seat
        # no longer has LOS on. That leaked live traffic: a harvester
        # walking through an echo-only region grew its trail on the map
        # (and in the agent's world view) with no fresh sighting. An echo
        # is a frozen memory, so its trail must freeze too. Live-LOS cells
        # still read the live ledger in the view builders; only stale
        # (echo/memory) cells serve this snapshot copy. Per RULEBOOK §3.12
        # trails are permanent surface changes, so we keep what was seen
        # rather than dropping it — it just stops updating until re-scouted.
        _tsum = self._trail_summary(x, y)
        if _tsum is not None:
            base["trail"] = _tsum
        _tmk = self._trail_markup(x, y)
        if _tmk is not None:
            base["trail_markup"] = _tmk
        top = self._entity_at_tile(x, y)
        if (
            exclude_entity_id is not None
            and top is not None
            and top.id == exclude_entity_id
        ):
            # The natural top is the entity we're hiding. Re-pick from
            # the filtered occupant list using the same probe-first
            # priority that :meth:`_entity_at_tile` applies, so a
            # spotted-along-with-us probe/harvester still surfaces as
            # the echo's glyph instead of being silently dropped.
            def _prio(ent: Entity) -> int:
                if ent.entity_type == "probe":
                    return 0
                if ent.entity_type == "harvester":
                    return 1
                return 9
            non_excluded = sorted(occ_entities, key=_prio)
            top = non_excluded[0] if non_excluded else None
        if top is None:
            return {**base, "glyph_ch": None, "glyph_fg": None}
        gh, fg = self._glyph_for_entity(top)
        return {**base, "glyph_ch": gh, "glyph_fg": fg}

    def _pulse_vision_intel(self) -> None:
        """Refresh persistent intel snapshots from probes *and* harvesters.

        Both observers write last-known tile snapshots into
        :attr:`probe_intel` using a circular vision disk (probe radius
        :data:`PROBE_VISION_RADIUS`, harvester radius
        :data:`HARVESTER_LOS_RADIUS`). The frontend renders any tile
        present here as a dimmed **echo** cell after the observer
        leaves, so surface vision from a harvester persists the same
        way probe vision does (RULEBOOK §3.11).

        Visibility tiers (RULEBOOK §3.8):

        * **fog**  — never seen / forgotten; absent from echo + memory.
        * **echo** — historical vision recorded here; cell exposes the
          last-known terrain, occupants, and entity glyph.
        * **live** — a unit currently has the cell in LOS (probe disk
          covers it, or a harvester is standing on it this frame).
          Live vision is computed each render in
          :meth:`tiles_visible_now`; this pulse is purely about the
          *echo* tier.

        Self-exclusion (v0.9.13): when any observer pulses the cell it
        occupies right now, the snapshot drops the observer itself from
        ``occupants`` / ``glyph_*`` so the echo doesn't ghost a phantom
        of the observer onto the map. For harvesters the trail overlay
        already says "this unit walked here"; for probes the live render
        shows the probe while it exists, so a destroyed probe leaves only
        the last SQUARE state behind, not a frozen ghost-probe glyph.
        Other units present at that frame still flow through normally —
        that's genuine intel the player should keep. Stale entity glyphs
        (e.g. an enemy harvester last seen here) are kept in the snapshot
        but expired by the renderer after a night (see ``day_seen``).
        """
        w, h = self.width, self.height
        # v1.x — REDSIGN discovery (RULEBOOK §4.11). Any pure-RED cell
        # (purity 255) that enters ANY seat's live vision this pulse and
        # hasn't been discovered before triggers a persistent public
        # beacon. Collect candidates here; mint the smears after the loop
        # so we scan the connected seam once per component.
        # v9 — carry the DISCOVERER: the seat whose vision first swept each
        # newly-pure cell this pulse. Players iterate in a stable order, so the
        # first witness wins deterministically; ties (co-vision in the same
        # pulse) are gathered per-seam in :meth:`_register_redsign`.
        newly_pure: Dict[Tuple[int, int], PlayerId] = {}
        for owner in self.players:
            oid = cast(PlayerId, owner)
            bucket = self.probe_intel[owner]
            for ent in self.entities.values():
                if cast(PlayerId, ent.owner) != oid:
                    continue
                if ent.x is None or ent.y is None:
                    continue
                if ent.entity_type == "probe":
                    area = _probe_vision_disk(ent.x, ent.y, w, h)
                elif ent.entity_type == "harvester":
                    area = _harvester_vision_disk(ent.x, ent.y, w, h)
                else:
                    continue
                for tx, ty in area:
                    # v0.9.13 — self-exclusion now covers probes too. Any
                    # observer drops its OWN glyph from its OWN cell so the
                    # echo records the last SQUARE state (terrain + harvest
                    # event + any *other* unit present), not a phantom of the
                    # observer itself. Previously a destroyed probe left a
                    # ghost-probe glyph frozen on the map; the trail overlay
                    # (for harvesters) and the live entity render (while the
                    # probe is alive) already communicate presence, so the
                    # echo silhouette of self was pure noise.
                    exclude_id: Optional[str] = None
                    if (tx, ty) == (ent.x, ent.y):
                        exclude_id = ent.id
                    bucket[_xy_key(tx, ty)] = self._probe_tile_snapshot(
                        tx, ty, exclude_entity_id=exclude_id,
                    )
                    # v1.x — REDSIGN trigger: an unseen pure-RED seam just
                    # entered vision. Cheap live-grid read; dedup + smear
                    # minting happens once after the pulse.
                    cell = self.grid[ty][tx]
                    if (
                        int(cell.tile) == int(Tile.RED)
                        and int(cell.purity) == 255
                        and f"{tx},{ty}" not in self.redsign_seen
                    ):
                        if (tx, ty) not in newly_pure:
                            newly_pure[(tx, ty)] = oid
        if newly_pure:
            self._register_redsign(newly_pure)
        # v9 (I12) — backstop: retire any beacon whose seam is no longer pure
        # (covers harvest paths + legacy regions that carry seam cells). The
        # authoritative retire happens at harvest in _retire_redsign_if_spent;
        # a freshly minted region is pure this pulse so it stays live.
        self._sweep_redsign_liveness()

    # Back-compat alias — older callers reach for the previous name.
    _pulse_probe_cameras = _pulse_vision_intel

    def _register_redsign(
        self, cells: "Mapping[Tuple[int, int], PlayerId]",
    ) -> None:
        """v1.x — mint a persistent public REDSIGN beacon for each newly
        discovered pure-RED seam (RULEBOOK §4.11).

        ``cells`` maps each pure-RED grid cell that just entered some seat's
        vision (and isn't already discovered) to its DISCOVERER — the seat
        whose vision first swept it this pulse (v9). For each seam, we flood
        the 8-connected pure-RED component it belongs to, mark every cell as
        seen (so the rest of the seam / re-sightings don't re-trigger), mint
        ONE fuzzy off-centre smear region, and log a public "RED SIGN" event.
        The public broadcast stays ANONYMOUS to rivals (the log text never
        names the discoverer, RULEBOOK §4.11); the discoverer is recorded on
        the region ONLY so each seat can privately tell "is this mine?".
        """
        # v1.32 — signs off (teaching mode). Return BEFORE marking anything
        # in ``redsign_seen``: the discovery trigger must be genuinely
        # absent, not merely silent. Consuming the discovery here would let
        # a later game with signs on load a save whose seams are already
        # spent, which is a much worse bug than a missing beacon.
        if not self.signs_enabled:
            return

        w, h = self.width, self.height

        def _is_pure(x: int, y: int) -> bool:
            if not (0 <= x < w and 0 <= y < h):
                return False
            c = self.grid[y][x]
            return int(c.tile) == int(Tile.RED) and int(c.purity) == 255

        for sx, sy in sorted(cells):
            if f"{sx},{sy}" in self.redsign_seen:
                continue  # swept up by an earlier component this pass
            # Flood the connected pure-RED seam (8-connected).
            comp: List[Tuple[int, int]] = []
            local: Set[Tuple[int, int]] = {(sx, sy)}
            stack = [(sx, sy)]
            while stack:
                x, y = stack.pop()
                comp.append((x, y))
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = x + dx, y + dy
                        if (nx, ny) not in local and _is_pure(nx, ny):
                            local.add((nx, ny))
                            stack.append((nx, ny))
            for x, y in comp:
                self.redsign_seen.add(f"{x},{y}")
            # Discoverer = seat that seeded this seam; co-discoverers = any
            # other seats that co-witnessed a cell of the SAME seam this pulse.
            discoverer = str(cells.get((sx, sy), ""))
            co_discoverers = sorted({
                str(cells[c]) for c in comp
                if c in cells and str(cells[c]) != discoverer
            })
            region = self._mint_redsign_region(
                comp, len(self.redsign),
                discoverer=discoverer, co_discoverers=co_discoverers,
            )
            self.redsign.append(region)
            cx, cy = region["center"]
            self.log_info(
                f"[redSign] RED SIGN — a pure RED seam blazed into view "
                f"near (~{int(round(cx))},~{int(round(cy))})"
            )

    def _mint_redsign_region(
        self, comp: Sequence[Tuple[int, int]], idx: int,
        *, discoverer: str = "", co_discoverers: "Optional[Sequence[str]]" = None,
    ) -> Dict[str, Any]:
        """Build one fuzzy, off-centre, noisy REDSIGN smear over a pure-RED
        seam — mirrors :meth:`_compute_blue_sign` so the two beacons read
        the same on the map. Deterministic per seed + seam location, so a
        reload reproduces an identical smear. The smear deliberately does
        NOT reveal the exact pure squares — only a rough area to race to.
        """
        import math

        n = max(1, len(comp))
        cx = sum(x for x, _ in comp) / n
        cy = sum(y for _, y in comp) / n
        # Deterministic per seed + seam centroid (distinct salt from blue).
        rng = random.Random(
            int(self.seed) * 2654435761 + 0x5ED516 + int(cx) * 131 + int(cy)
        )
        base_r = max(1.5, math.sqrt(n / math.pi))
        scx = cx + rng.uniform(-0.7, 0.7) * base_r
        scy = cy + rng.uniform(-0.7, 0.7) * base_r
        radius = base_r * rng.uniform(1.4, 2.2) + 1.0
        cells: List[List[float]] = []
        x0 = int(math.floor(scx - radius))
        x1 = int(math.ceil(scx + radius))
        y0 = int(math.floor(scy - radius))
        y1 = int(math.ceil(scy + radius))
        for yy in range(max(0, y0), min(self.height, y1 + 1)):
            for xx in range(max(0, x0), min(self.width, x1 + 1)):
                d = math.hypot(xx - scx, yy - scy)
                if d > radius:
                    continue
                inten = (1.0 - d / radius) * rng.uniform(0.55, 1.25)
                if inten < 0.18 and rng.random() < 0.5:
                    continue
                if inten <= 0.08:
                    continue
                cells.append([xx, yy, round(min(1.0, inten), 3)])
        if not cells:
            # Degenerate (all fringe dropped) — keep at least the centre.
            cells.append([int(round(scx)), int(round(scy)), 0.5])
        return {
            "id": f"redsign-{idx}",
            "center": [round(scx, 2), round(scy, 2)],
            "cells": cells,
            "day": int(self.day),
            # v1.x — praxis hour the seam was witnessed (set by the night
            # simulator before each vision pulse). Lets replay pop the pulse
            # at the exact discovery frame rather than the top of the night.
            # 0 for legacy regions / non-night pulses (shows from night start).
            "hour": int(getattr(self, "_redsign_hour", 0) or 0),
            # v9 — DISCOVERER attribution (engine-internal; the public log line
            # stays anonymous). Lets each seat's view derive ``mine`` so the
            # redsign-poker doctrine can branch on ground truth. Empty string
            # for legacy regions loaded from an older save.
            "discoverer": str(discoverer or ""),
            "co_discoverers": list(co_discoverers or []),
            # v9 (I12) — REDSIGN LIFECYCLE. A beacon marks a PURE-RED seam; it
            # must turn OFF once that seam's pure cells are all harvested
            # (RED→synthetic-green), otherwise it broadcasts "pure in FOG"
            # forever and lures every seat into chasing a jackpot that's gone.
            # ``pure_cells`` is the seam's real pure squares (engine-internal —
            # stripped from every seat view so it never leaks the exact pures);
            # ``live`` flips False when none remain pure; ``spent_day`` /
            # ``spent_by`` record when and by whom (ground truth) it was
            # exhausted. Legacy regions (loaded without these) default to live.
            "pure_cells": [[int(x), int(y)] for x, y in comp],
            "live": True,
            "spent_day": None,
            "spent_by": "",
        }

    def _cell_is_pure(self, x: int, y: int) -> bool:
        """True iff ``(x, y)`` is an on-grid pure-RED cell (purity 255)."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            return False
        c = self.grid[y][x]
        return int(c.tile) == int(Tile.RED) and int(c.purity) == 255

    def _retire_redsign_if_spent(
        self, x: int, y: int, owner: Optional[PlayerId] = None,
    ) -> None:
        """v9 (I12) — turn a REDSIGN beacon OFF when its seam is exhausted.

        Called right after a RED cell at ``(x, y)`` is harvested (→ synthetic
        green). If that cell belonged to a still-live redsign seam and NONE of
        that seam's ``pure_cells`` remain pure, the region is marked dead so it
        stops broadcasting and stops being offered as a hot-drop target. Cheap:
        seams are tiny and we only rescan a region that owned the harvested
        cell. ``owner`` (the harvester's seat) is recorded as ground-truth
        ``spent_by`` for later attribution work (I16).
        """
        for region in self.redsign or []:
            if not region.get("live", True):
                continue
            pures = region.get("pure_cells") or []
            if [int(x), int(y)] not in ([list(pc) for pc in pures]):
                continue
            if any(self._cell_is_pure(int(px), int(py)) for px, py in pures):
                continue
            region["live"] = False
            region["spent_day"] = int(self.day)
            region["spent_by"] = str(owner or "")
            cx, cy = region.get("center", [x, y])
            self.log_info(
                f"[redSign] RED SIGN spent — the pure seam near "
                f"(~{int(round(float(cx)))},~{int(round(float(cy)))}) is exhausted"
            )

    def _sweep_redsign_liveness(self, owner: Optional[PlayerId] = None) -> int:
        """Backstop sweep: recompute ``live`` for every region against the
        current grid. Returns the number of regions retired this call. Safe to
        run any time; the authoritative hook is :meth:`_retire_redsign_if_spent`
        at harvest, but this catches any path that mutates purity directly and
        backfills liveness for legacy regions that carry ``pure_cells``.
        """
        retired = 0
        for region in self.redsign or []:
            if not region.get("live", True):
                continue
            pures = region.get("pure_cells") or []
            if not pures:
                continue  # legacy region w/o seam cells — leave live
            if any(self._cell_is_pure(int(px), int(py)) for px, py in pures):
                continue
            region["live"] = False
            region.setdefault("spent_day", int(self.day))
            if region.get("spent_day") is None:
                region["spent_day"] = int(self.day)
            if not region.get("spent_by"):
                region["spent_by"] = str(owner or "")
            retired += 1
        return retired

    def _bump_path(
        self,
        owner: PlayerId,
        x: int,
        y: int,
        harvester_id: Optional[str] = None,
    ) -> int:
        """Increment the owner's visit count for ``(x, y)`` and return it.

        Also stamps ``d`` (current planning day) and ``h`` (the
        harvester id that did the moving) onto the entry so the OBS /
        replay watcher can tell *which* unit walked the cell and *when*
        — load-bearing for the "fresh trails carry rich tooltip"
        requirement (the trail itself becomes the post-night story).

        ``harvester_id`` defaults to ``None`` so older / external
        callers don't break, but the in-engine ``try_drop_unit`` /
        ``try_step_unit`` sites always pass it.
        """
        k = _xy_key(x, y)
        bucket = self.track_paths[owner]
        entry = bucket.get(k)
        if not isinstance(entry, dict):
            entry = {"n": 0, "d": None, "h": None}
        entry["n"] = int(entry.get("n", 0)) + 1
        entry["d"] = int(self.day)
        if harvester_id is not None:
            entry["h"] = str(harvester_id)
        bucket[k] = entry
        return int(entry["n"])

    def _trail_markup(self, x: int, y: int) -> Optional[dict[str, str]]:
        """Universal shading overlay at ``(x, y)`` — no longer per-owner.

        Block-element overlays drawn at full cell size so they read as
        actual *shading* on top of the terrain (RULEBOOK §3.12).

        - **Path tracks** — escalating density per *aggregate* visit
          count (1→``░░``, 2→``▒▒``, 3→``▓▓``, 4+→``██``) in a
          neutral off-white. Both players' crossings stack into the
          same trail: two p1 visits + one p2 visit reads as tier 3.

        v0.7.3 — harvest state no longer recolours the trail. RED→GREEN
        cells already paint their BG synthetic-green via the tile
        renderer, and GREEN/BLUE harvests collapse the cell to EMPTY
        (nothing left to highlight). Tinting the trail glyph green on
        top was double-counting on RED-harvests and outright misleading
        on GREEN/BLUE-harvests, so the trail markup is now strictly a
        path-density signal in a single neutral tone. The
        ``harvested`` attribution survives on :meth:`_trail_summary`
        for tooltips and agent reasoning — only the visual side-effect
        is gone.

        Player-of-origin is intentionally absent from the markup — the
        only place attribution surfaces is the ``fresh_visits`` list
        on the universal :meth:`_trail_summary` payload, and even
        there it's gated by the 1-day freshness window.
        """
        k = _xy_key(x, y)
        total = 0
        for owner in self.players:
            entry = self.track_paths.get(owner, {}).get(k)
            if isinstance(entry, dict):
                total += int(entry.get("n", 0))
        glyph = path_tier_glyph(total)
        if glyph is None:
            return None
        return {"fg": _rgb((220, 222, 230)), "ch": glyph}

    def _trail_summary(self, x: int, y: int) -> Optional[Dict[str, Any]]:
        """Universal trail summary at ``(x, y)`` — the v0.7.2 contract.

        Returns ``None`` when no crossings have been recorded by any
        player; otherwise a single dict::

            {
              "n": <aggregate visit count across all owners>,
              "tier": <1..4 tier index for n>,
              "harvested": <True if any owner has harvested this cell>,
              "fresh_visits": [
                {"owner": "p1", "h": "harvester_p1", "day": 3, "n": 2},
                ...
              ],
            }

        ``n`` and ``tier`` are deliberately anonymous — the density
        signal stacks crossings from both seats so the map alone
        communicates traffic intensity without leaking who made the
        crossing.

        ``fresh_visits`` is the ONLY attribution channel. An entry is
        "fresh" when the owner's last visit happened within the past
        day of game time (``self.day - last_visit_day <= 1``). After
        that the per-owner attribution falls off and the trail looks
        anonymous to everyone — only the aggregate count remains.
        This matches RULEBOOK §3.12: trails permanently alter the
        board (density never decays), but the "who did this?"
        annotation is a 24-hour signal.

        Fog-of-war is enforced by callers: this method does NOT check
        whether the observer can see ``(x, y)``. Callers must only
        attach the result to cells that the viewer is allowed to see
        (live or echo). Fog cells never carry a trail.
        """
        k = _xy_key(x, y)
        total_n = 0
        harvested = False
        fresh_visits: List[Dict[str, Any]] = []
        for owner in self.players:
            if k in self.track_harvests.get(owner, set()):
                harvested = True
            entry = self.track_paths.get(owner, {}).get(k)
            if not isinstance(entry, dict):
                continue
            n = int(entry.get("n", 0))
            if n <= 0:
                continue
            total_n += n
            d = entry.get("d")
            d_int = int(d) if isinstance(d, (int, float)) else None
            if d_int is not None and (int(self.day) - d_int) <= 1:
                fresh_visits.append(
                    {
                        "owner": owner,
                        "h": entry.get("h"),
                        "day": d_int,
                        "n": n,
                    }
                )
        if total_n <= 0 and not harvested:
            return None
        tier = min(total_n, len(PATH_TIER_GLYPHS)) if total_n > 0 else 0
        summary: Dict[str, Any] = {
            "n": total_n,
            "tier": tier,
            "fresh_visits": fresh_visits,
        }
        if harvested:
            summary["harvested"] = True
        return summary

    def _collision_summary(self, x: int, y: int) -> Optional[Dict[str, Any]]:
        """Active collision mark at ``(x, y)`` if within the 1-day window.

        Returns ``{day, owners, age}`` where ``age`` is 0 for "happened
        tonight" (still being replayed) and 1 for "happened yesterday"
        (the scar lingers through the next planning phase). Anything
        older returns ``None`` — the storage may have already been
        pruned, but this check is defensive.
        """
        key = _xy_key(x, y)
        mark = self.collision_marks.get(key)
        if not isinstance(mark, dict):
            return None
        day = mark.get("day")
        if not isinstance(day, int):
            return None
        age = int(self.day) - int(day)
        if age < 0 or age > 1:
            return None
        return {
            "day": int(day),
            "owners": list(mark.get("owners") or []),
            "age": age,
        }

    def unit_summary_for_owner(self, player: PlayerId) -> List[dict[str, Any]]:
        """Structured status for OWN units — no opponent geometry leaks."""
        rows: List[dict[str, Any]] = []
        for e in sorted(self.entities.values(), key=lambda x: x.id):
            if e.owner != player:
                continue
            row: dict[str, Any] = {
                "id": e.id,
                "type": e.entity_type,
                "carrying_red": e.carrying_red,
                "orbital_cargo_red": e.orbital_cargo_red,
            }
            if e.entity_type == "harvester":
                row["cargo_squares"] = len(e.cargo_squares)
                row["lost_last_night"] = bool(e.lost_last_night)
                # v0.9.5 — surface damage so the orbit panel can
                # show a per-harvester repair affordance + the
                # agent can route a RepairAction at the correct
                # harvester without inferring from the wreckage
                # glyph.
                row["damaged"] = bool(getattr(e, "damaged", False))
            if e.x is not None:
                row["pos"] = [e.x, e.y]
            else:
                row["pos"] = None
                row["orbit"] = True
            row["label"] = public_entity_title(e)
            rows.append(row)
        return rows

    # --- Observation export ------------------------------------------

    def _observer_cells_packed(self) -> List[dict[str, Any]]:
        cells: List[dict[str, Any]] = []
        w = self.width
        # v0.9.5 — pre-build a {(x, y): owner} index of every active
        # EMP cloud cell so the per-cell loop can stamp the tooltip-
        # ready ``emp_cloud`` payload without walking the cloud list
        # for each tile. Owner is informational only; the observer
        # view is omniscient by definition.
        emp_owner_at: Dict[Tuple[int, int], Dict[str, Any]] = {}
        for cloud in self.emp_clouds:
            if int(cloud.get("hours_remaining", 0)) <= 0:
                continue
            cx = int(cloud.get("cx", -1))
            cy = int(cloud.get("cy", -1))
            r = int(cloud.get("radius", 0))
            if cx < 0 or cy < 0 or r < 0:
                continue
            for (mx, my) in _manhattan_disk(cx, cy, r, self.width, self.height):
                emp_owner_at[(mx, my)] = {
                    "owner": str(cloud.get("owner", "")),
                    "hours_remaining": int(cloud.get("hours_remaining", 0)),
                    "cx": cx,
                    "cy": cy,
                    "radius": r,
                }
        for y, row in enumerate(self.grid):
            for x, cell in enumerate(row):
                packed = dict(cell_to_paint(cell))
                # Observer view is omniscient: surface the universal
                # trail summary (aggregate density + fresh visit
                # attribution within the last 24h). Per RULEBOOK §3.12
                # v0.7.2, trails are no longer player-specific; only
                # the ``fresh_visits`` list inside the summary carries
                # owner/harvester attribution, and only for ≤ 1 day.
                trail = self._trail_summary(x, y)
                if trail:
                    packed["trail"] = trail
                # Collision scars (§3.6 v0.7.3) — visible to the
                # observer view for one full game day, then decay.
                collision = self._collision_summary(x, y)
                if collision:
                    packed["collision"] = collision
                emp = emp_owner_at.get((x, y))
                if emp is not None:
                    packed["emp_cloud"] = emp
                # v1.8 — decaying EMP scar (where a cloud was active last
                # night + hours). Public per §5.1; omniscient here anyway.
                scar = self.emp_marks.get(_xy_key(x, y))
                if scar is not None:
                    packed["combat"] = {"emp": {
                        "owners": list(scar.get("owners") or []),
                        "hours": list(scar.get("hours") or []),
                        "day": int(scar.get("day", 0)),
                    }}
                # v0.9.10 — destroyed harvester gravestones (permanent).
                dest_key = _xy_key(x, y)
                dest_marker = self.destroyed_harvester_markers.get(dest_key)
                if dest_marker is not None:
                    packed["destroyed_harvester"] = {
                        "owner": str(dest_marker.get("owner", "")),
                        "harvester_id": str(dest_marker.get("harvester_id", "")),
                        "day": int(dest_marker.get("day", 0)),
                    }
                cells.append(packed)
        # Bind w to silence the unused-local warning when the index loop
        # above is the only consumer (keeps the variable explicit for
        # readability — _inject_occupants doesn't need it).
        del w
        self._inject_occupants(cells, omniscient=True)
        self._annotate_entity_glyphs_on_cells(cells, viewer=None, omniscient=True)
        return cells

    def observer_cells_rowmajor(self) -> List[dict[str, Any]]:
        """Authoritative spectator cells (reuse via _observer_cells_packed)."""
        return self._observer_cells_packed()

    def player_dense_view(self, player: PlayerId) -> List[dict[str, Any]]:
        """Row-major percept: fog / fresh terrain / echo / stale memory.

        Tile precedence:
          1. Currently in line-of-sight → fresh terrain + live entities.
          2. Persistent intel echo (probe or harvester last-known) →
             dimmed snapshot including last entity glyph / occupants.
          3. Plain memory (terrain-only) — legacy fallback when echo
             never recorded this tile.
          4. Fog.

        v0.9.6 — when :attr:`visibility_mode` is ``"open"``, every
        cell is treated as currently visible to ``player`` regardless
        of the actual line-of-sight calculation. Live entities still
        come from the global :attr:`entities` map so an OBS-style
        view exposes the full board. Fog/echo/memory branches still
        run as data-prep but never produce a "fog" cell on output.
        """
        if str(self.visibility_mode).lower() == "open":
            # Every tile on the board is visible to the SPECTATOR.
            # Critically we DO NOT call ``_blend_memory(player, vis)``
            # here — that would write every cell into the seat's
            # persistent ``memory_tiles``, which is the same store
            # the engine reads (RULEBOOK §3.10) to validate harvester
            # drops. Polluting memory with cells the seat has never
            # actually observed lets the AI agent's ``_is_valid_drop``
            # falsely report any cell as drop-valid (because its
            # ``agent_dense_view`` then sees them as ``via=memory``
            # echoes), but the engine still rejects those drops as
            # "in fog" — the two sides have to agree. So open mode
            # is now PRESENTATION-ONLY: the spectator below sees
            # every cell painted; the seat's memory only records
            # what the seat genuinely had vision on.
            vis = {
                (xi, yi)
                for yi in range(self.height)
                for xi in range(self.width)
            }
            self._blend_memory(player, self.tiles_visible_now(player))
        else:
            vis = self.tiles_visible_now(player)
            self._blend_memory(player, vis)

        dense: List[dict[str, Any]] = []
        mem = self.memory_tiles[player]
        echoes = self.probe_intel[player]
        # v0.9.5 — pre-build the EMP cloud index. EMP clouds are
        # public (the comment on :attr:`emp_clouds` notes "every seat
        # reads from this list … not gated by fog"), so we stamp the
        # tooltip-ready payload onto any visible cell regardless of
        # the seat's vision tier. Hidden cells (fog) skip this — the
        # frontend already paints a separate cloud overlay there.
        emp_cloud_at: Dict[Tuple[int, int], Dict[str, Any]] = {}
        for cloud in self.emp_clouds:
            if int(cloud.get("hours_remaining", 0)) <= 0:
                continue
            cx = int(cloud.get("cx", -1))
            cy = int(cloud.get("cy", -1))
            r = int(cloud.get("radius", 0))
            if cx < 0 or cy < 0 or r < 0:
                continue
            for (mx, my) in _manhattan_disk(cx, cy, r, self.width, self.height):
                emp_cloud_at[(mx, my)] = {
                    "owner": str(cloud.get("owner", "")),
                    "hours_remaining": int(cloud.get("hours_remaining", 0)),
                    "cx": cx,
                    "cy": cy,
                    "radius": r,
                }

        for y in range(self.height):
            for x in range(self.width):
                k = _xy_key(x, y)

                if (x, y) in vis:
                    paint = dict(cell_to_paint(self.grid[y][x]))
                    # Mark which sides face outside the live LOS so the
                    # frontend can outline the perimeter of the player's
                    # vision with a soft white edge (RULEBOOK §3.11).
                    vedge = ""
                    if (x, y - 1) not in vis:
                        vedge += "n"
                    if (x + 1, y) not in vis:
                        vedge += "e"
                    if (x, y + 1) not in vis:
                        vedge += "s"
                    if (x - 1, y) not in vis:
                        vedge += "w"
                    cell = {
                        "kind": "terrain",
                        "stale": False,
                        "echo_probe": False,
                        **paint,
                        **cell_facts(
                            self.grid[y][x].tile, self.grid[y][x].purity,
                        ),
                    }
                    if vedge:
                        cell["vedge"] = vedge
                elif k in echoes:
                    snap = echoes[k]
                    # v0.9.7 — probe-launch markers (RULEBOOK §3.15)
                    # reveal the probe occupant but NOT the terrain.
                    # Render as a fog cell with the probe glyph
                    # floating on top so the watcher sees "there's an
                    # enemy probe at (x,y)" without learning the tile
                    # / purity underneath. The ``entity`` overlay
                    # reuses the existing frontend renderer (which
                    # paints a single coloured char on top of the
                    # background) so we don't need a new code path on
                    # the JS side — fog cells already render through
                    # the same overlay pipeline.
                    if snap.get("via") == "probe_launch" and "paint" not in snap:
                        fog_cell: dict[str, Any] = {"kind": "fog"}
                        occ = snap.get("occupants")
                        if isinstance(occ, list) and occ:
                            fog_cell["occupants"] = occ
                            # Pick the probe occupant (there should
                            # only be one for a probe-launch marker)
                            # and stamp a glyph in the seat owner's
                            # colour so the cell renders as
                            # "probe glyph hovering over fog".
                            for o in occ:
                                if (
                                    isinstance(o, dict)
                                    and o.get("type") == "probe"
                                ):
                                    owner = str(o.get("owner", ""))
                                    # v0.9.18 — use custom seat color
                                    probe_rgb = self.seat_color(owner) if owner else ENTITY_OWNER_DEFAULT
                                    probe_ent = {
                                        "ch": ENTITY_GLYPHS["probe"],
                                        "fg": _rgb(probe_rgb),
                                    }
                                    # v0.9.18 — carry the probe's remaining
                                    # coverage life onto the marker so enemy
                                    # probes ring/fade exactly like our own.
                                    # Prefer the LIVE value (probe still up),
                                    # else fall back to the echo's snapshot.
                                    _live = self.entities.get(str(o.get("id") or ""))
                                    _nr = (
                                        self.probe_nights_remaining(_live)
                                        if _live is not None
                                        else o.get("nights_remaining")
                                    )
                                    if _nr is not None:
                                        probe_ent["nights_remaining"] = _nr
                                    fog_cell["entity"] = probe_ent
                                    break
                        dense.append(fog_cell)
                        continue
                    painted = dict(snap["paint"])
                    cell = {
                        "kind": "terrain",
                        "stale": True,
                        "echo_probe": True,
                        **painted,
                        # The echo froze tile+purity at sighting time, so the
                        # readout quotes what the seat REMEMBERS, matching the
                        # paint beside it. Snapshots taken before v1.22 have
                        # neither key and simply omit the facts.
                        **_snapshot_facts(snap),
                    }
                    # v0.9.13 — the terrain echo persists as remembered
                    # ground, but the stale entity silhouette (ghost probe /
                    # ghost harvester) is dropped once it's older than a night
                    # so dead probes and moved harvesters don't leave permanent
                    # ghost fields. Mirrors the trail-attribution freshness
                    # window (<= 1 day). Snapshots from before this field
                    # existed (no ``day_seen``) are treated as stale so legacy
                    # ghosts clear on the next view.
                    ds = snap.get("day_seen")
                    glyph_fresh = (
                        isinstance(ds, (int, float))
                        and (self.day - int(ds)) <= 1
                    )
                    occ = snap.get("occupants")
                    # v1.11 (RULEBOOK §3.15) — a publicly-launched rival
                    # probe merged onto this echo carries its OWN glyph
                    # marker (stamped by :meth:`_pulse_probe_launch`),
                    # independent of ``glyph_fresh``/``day_seen`` — those
                    # track when THIS SEAT last actually observed the
                    # terrain, not when a rival's probe publicly landed
                    # on it, and must not be conflated. This takes
                    # priority over the general ghost-glyph path below so
                    # the probe stays visible for as long as the marker
                    # exists (pruned on probe death/decay).
                    plaunch_glyph = snap.get("probe_launch_glyph")
                    if isinstance(plaunch_glyph, dict) and plaunch_glyph.get("ch"):
                        if isinstance(occ, list) and occ:
                            cell["occupants"] = occ
                        cell["entity"] = {
                            "ch": plaunch_glyph["ch"],
                            "fg": plaunch_glyph.get("fg"),
                        }
                    elif glyph_fresh:
                        if isinstance(occ, list) and occ:
                            cell["occupants"] = occ
                        gh, gf = snap.get("glyph_ch"), snap.get("glyph_fg")
                        if isinstance(gh, str) and gh.strip() and isinstance(gf, str):
                            cell["entity"] = {"ch": gh, "fg": gf}
                    # v1.8 — serve the trail FROZEN into this echo snapshot
                    # (bug #1). The seat has no live LOS here, so it sees the
                    # traffic as it was when last observed, not live growth.
                    _ftm = snap.get("trail_markup")
                    if isinstance(_ftm, dict):
                        cell["trail_markup"] = _ftm
                    _ft = snap.get("trail")
                    if isinstance(_ft, dict):
                        cell["trail"] = _ft
                elif k in mem:
                    m = mem[k]
                    cell = {
                        "kind": "terrain",
                        "stale": bool(m["stale"]),
                        "echo_probe": False,
                        **m["paint"],
                        **_snapshot_facts(m),
                    }
                    # v1.8 — frozen trail from the memory tile (bug #1).
                    _ftm = m.get("trail_markup")
                    if isinstance(_ftm, dict):
                        cell["trail_markup"] = _ftm
                    _ft = m.get("trail")
                    if isinstance(_ft, dict):
                        cell["trail"] = _ft
                else:
                    # v1.1 — harvester gravestones are PUBLIC, permanent
                    # landmarks: surface them even on never-explored (fog)
                    # cells so a wreck is visible to EVERY seat, not just
                    # whoever happened to scout that square. Terrain stays
                    # fogged — only the grave marker rides on top (the
                    # frontend renders it over the fog block, the same way
                    # an enemy probe-launch marker shows on a fog cell).
                    fog_only: Dict[str, Any] = {"kind": "fog"}
                    grave = self.destroyed_harvester_markers.get(_xy_key(x, y))
                    if grave is not None:
                        fog_only["destroyed_harvester"] = {
                            "owner": str(grave.get("owner", "")),
                            "harvester_id": str(grave.get("harvester_id", "")),
                            "day": int(grave.get("day", 0)),
                        }
                    dense.append(fog_only)
                    continue

                # Trail rendering (RULEBOOK §3.12). A single neutral-tone
                # glyph whose density tier reflects AGGREGATE crossings.
                # v1.8 — the trail is a fog-gated observation, not an
                # omniscient overlay (bug #1). Only a cell the seat has
                # LIVE line-of-sight on this frame reads the current
                # ledger; echo/memory cells serve the trail FROZEN at
                # their last sighting (attached in their branches above /
                # below). This stops traffic on tiles the seat can't see
                # from growing on the map (and in the agent world view).
                # Fog cells skip this entirely (see ``else`` → fog above).
                if (x, y) in vis:
                    trail_markup = self._trail_markup(x, y)
                    if trail_markup is not None:
                        cell["trail_markup"] = trail_markup
                    trail = self._trail_summary(x, y)
                    if trail:
                        cell["trail"] = trail
                # Collision scars are part of the observable surface
                # (§3.6 v0.7.3) — surface them on any visible cell.
                # Fog cells already skipped via the ``else`` branch above.
                collision = self._collision_summary(x, y)
                if collision:
                    cell["collision"] = collision

                # v0.9.10 — destroyed harvester gravestones (permanent).
                # Surface on any non-fog cell so the watcher can see where
                # harvesters were left without pickup.
                dest_key = _xy_key(x, y)
                dest_marker = self.destroyed_harvester_markers.get(dest_key)
                if dest_marker is not None:
                    cell["destroyed_harvester"] = {
                        "owner": str(dest_marker.get("owner", "")),
                        "harvester_id": str(dest_marker.get("harvester_id", "")),
                        "day": int(dest_marker.get("day", 0)),
                    }

                # v0.9.5 — EMP clouds are public (RULEBOOK §5.1 — not
                # gated by fog); surface the cloud payload on any
                # non-fog cell so the tooltip can render the active
                # interdiction zone without cross-referencing.
                emp_info = emp_cloud_at.get((x, y))
                if emp_info is not None:
                    cell["emp_cloud"] = emp_info

                # v1.8 — decaying EMP scar: "an EMP was active here last
                # night, hours H..H". Public (§5.1), so surface on any
                # non-fog cell (fog cells already skipped via the else
                # branch above). Reader-friendly for both the human map
                # overlay and the agent world view.
                scar = self.emp_marks.get(k)
                if scar is not None:
                    cell["combat"] = {"emp": {
                        "owners": list(scar.get("owners") or []),
                        "hours": list(scar.get("hours") or []),
                        "day": int(scar.get("day", 0)),
                    }}

                dense.append(cell)

        self._inject_occupants(dense, omniscient=False, viewer=player)
        self._annotate_entity_glyphs_on_player_cells(dense, player)
        return dense

    def agent_dense_view(self, player: PlayerId) -> dict[str, Any]:
        """Structured logical world view for the Cortex harness.

        Mirrors the visibility precedence rules of
        :meth:`player_dense_view` (live > echo > memory > fog) but emits
        addressable, *logical* rows the agent can filter without
        character-counting an ASCII map. Returns
        ``{"width", "height", "live": [...], "echo": [...],
        "fog_count": int}``.

        Live rows carry the current ledger square_id / lineage /
        parent_square_id (full provenance) because they correspond to
        present truth. Echo rows carry the **snapshot's** stale
        tile/purity and only ``last_seen_day`` / ``via`` metadata —
        the agent reads them as "this is what I knew was there".

        Trails follow the same universal-stacked rule used elsewhere
        (RULEBOOK §3.12): both seats' visit history surfaces on any
        non-fog cell, gated only by fog of war.
        """
        # v0.9.6 — ``agent_dense_view`` is consumed by the AGENT
        # (heuristic + Cortex) for planning, NOT by the spectator.
        # ``visibility_mode == "open"`` is a presentation concern for
        # the OBS / replay-watcher UI; flooding the agent's ``live``
        # list with every cell breaks fog-aware planning because the
        # engine still validates drops + steps against the seat's
        # private ``tiles_visible_now``. The agent's view must
        # therefore always be the seat-private one — the spectator
        # path renders the all-reveal map separately from this method.
        vis = self.tiles_visible_now(player)
        self._blend_memory(player, vis)

        echoes = self.probe_intel[player]
        mem = self.memory_tiles[player]

        live_rows: List[dict[str, Any]] = []
        echo_rows: List[dict[str, Any]] = []
        fog_count = 0

        def _tile_name(t: int) -> str:
            try:
                return Tile(int(t)).name
            except Exception:
                return str(int(t))

        def _entity_payload(ent: Entity) -> dict[str, Any]:
            payload: dict[str, Any] = {
                "id": ent.id,
                "kind": ent.entity_type,
                "owner": ent.owner,
            }
            if ent.entity_type == "harvester":
                payload["carrying"] = len(ent.cargo_squares)
            return payload

        for y in range(self.height):
            for x in range(self.width):
                k = _xy_key(x, y)

                if (x, y) in vis:
                    cell = self.grid[y][x]
                    purity = int(cell.purity)
                    row: dict[str, Any] = {
                        "x": x,
                        "y": y,
                        "tile": _tile_name(int(cell.tile)),
                        "purity": purity,
                        "value": max(0, min(255, purity)),
                    }
                    if self.ledger is not None:
                        sid = self.ledger.lookup(x, y)
                        if sid:
                            row["square_id"] = sid
                            led_row = self.ledger.row_by_sid(sid)
                            if led_row:
                                lineage = str(led_row.get("lineage") or "natural")
                                if lineage != "natural":
                                    row["lineage"] = lineage
                                psid = led_row.get("parent_square_id")
                                if psid:
                                    row["parent_square_id"] = psid
                    vedge = ""
                    if (x, y - 1) not in vis:
                        vedge += "n"
                    if (x + 1, y) not in vis:
                        vedge += "e"
                    if (x, y + 1) not in vis:
                        vedge += "s"
                    if (x - 1, y) not in vis:
                        vedge += "w"
                    if vedge:
                        row["vedge"] = vedge
                    trail = self._trail_summary(x, y)
                    if trail:
                        row["trail"] = trail
                    collision = self._collision_summary(x, y)
                    if collision:
                        row["collision"] = collision
                    top = self._entity_at_tile(x, y)
                    if top is not None:
                        row["entity"] = _entity_payload(top)
                    live_rows.append(row)
                elif k in echoes:
                    snap = echoes[k]
                    # v0.9.7 — probe-launch markers (§3.15) reveal the
                    # probe occupant but NOT the underlying terrain.
                    # Surface them as fog rows that carry the probe
                    # occupant so the agent learns "there's an enemy
                    # probe at (x,y)" without learning tile / purity.
                    # The drop validator (engine + agent) excludes
                    # these from drop-valid cells, matching the
                    # presentation.
                    if (
                        snap.get("via") == "probe_launch"
                        and "paint" not in snap
                    ):
                        fog_count += 1
                        occ = snap.get("occupants")
                        if isinstance(occ, list) and occ:
                            row = {
                                "x": x,
                                "y": y,
                                "via": "probe_launch",
                                "occupants": list(occ),
                                "fog": True,
                            }
                            day_seen = snap.get("day_seen")
                            if isinstance(day_seen, int):
                                row["last_seen_day"] = day_seen
                            echo_rows.append(row)
                        continue
                    snap_tile = int(snap.get("tile", 0))
                    snap_purity = int(snap.get("purity", 0))
                    row = {
                        "x": x,
                        "y": y,
                        "tile": _tile_name(snap_tile),
                        "purity": snap_purity,
                        "value": max(0, min(255, snap_purity)),
                    }
                    day_seen = snap.get("day_seen")
                    if isinstance(day_seen, int):
                        row["last_seen_day"] = day_seen
                    via = snap.get("via")
                    if isinstance(via, str):
                        row["via"] = via
                    occ = snap.get("occupants")
                    if isinstance(occ, list) and occ:
                        row["occupants"] = list(occ)
                    # v1.8 — frozen trail from the echo snapshot (bug #1):
                    # the agent must not read live traffic on tiles it can't
                    # currently see, only what it observed at sighting time.
                    _ft = snap.get("trail")
                    if isinstance(_ft, dict):
                        row["trail"] = _ft
                    collision = self._collision_summary(x, y)
                    if collision:
                        row["collision"] = collision
                    echo_rows.append(row)
                elif k in mem:
                    # Memory-only tiles count as echoes for the agent —
                    # the player has seen this terrain before. Use the
                    # memory snapshot to recover a tile name; the
                    # frontend uses paint for these but the agent only
                    # needs the categorical tile.
                    paint = mem[k].get("paint") or {}
                    # Memory paint dict carries 'bg' / 'fg' / 'ch' but
                    # NOT the raw tile int — fall back to inspecting
                    # the live grid (the planet doesn't randomly
                    # repaint between sightings; the worst-case stale
                    # is the agent learns a cell was harvested AFTER
                    # they last saw it, which is fine).
                    cell = self.grid[y][x]
                    row = {
                        "x": x,
                        "y": y,
                        "tile": _tile_name(int(cell.tile)),
                        "purity": int(cell.purity),
                        "value": max(0, min(255, int(cell.purity))),
                        "via": "memory",
                    }
                    # v1.8 — frozen trail from the memory tile (bug #1).
                    _ft = (mem.get(k) or {}).get("trail")
                    if isinstance(_ft, dict):
                        row["trail"] = _ft
                    collision = self._collision_summary(x, y)
                    if collision:
                        row["collision"] = collision
                    echo_rows.append(row)
                    # Suppress unused-name warning for paint.
                    _ = paint
                else:
                    fog_count += 1

        # v1.8 — stamp the decaying EMP scar onto every surfaced row
        # (live + echo). EMP is public (§5.1), so the "an EMP was active
        # here last night, hours H..H" signal rides on any non-fog cell,
        # giving spatial agents the interdiction footprint without having
        # to reconstruct the dissipated cloud.
        if self.emp_marks:
            for _row in (live_rows, echo_rows):
                for r in _row:
                    scar = self.emp_marks.get(_xy_key(int(r["x"]), int(r["y"])))
                    if scar is not None:
                        r["combat"] = {"emp": {
                            "owners": list(scar.get("owners") or []),
                            "hours": list(scar.get("hours") or []),
                            "day": int(scar.get("day", 0)),
                        }}

        return {
            "width": self.width,
            "height": self.height,
            "live": live_rows,
            "echo": echo_rows,
            "fog_count": fog_count,
        }

    def _may_see_entity(self, viewer: PlayerId, e: Entity, vis: Set[Tuple[int, int]]) -> bool:
        if e.x is None or e.y is None:
            return False
        return (e.x, e.y) in vis

    def _glyph_for_entity(self, e: Entity) -> Tuple[str, str]:
        """Return (single_char_glyph, foreground_css) for ``e`` on the map.

        Entity glyphs are rendered by the frontend as a *centered overlay*
        on top of the terrain dither, so they occupy a single column
        rather than the cell's full 2-ch width. The terrain pattern
        beneath stays visible, giving entities a clean readable focal point.

        Colour rule (v0.9.18 — uses custom seat colors from player_profiles):

        * Empty harvester / probe → custom seat colour from player_profiles
          via :meth:`seat_color`. Players read seat ownership at a glance.
        * Carrying harvester → glyph switches to the "loaded" variant
          ``X`` (uppercase) but seat colour is preserved. Cargo state
          is signalled by the case change alone, so colour-blind players
          have a shape signal and the seat owner stays unambiguous on
          the map (a hot-red repaint was tested but lost too much
          ownership information at a glance).

        Glyph choices are documented on :data:`ENTITY_GLYPHS`.
        """
        # v0.9.18 — use custom seat color from player_profiles
        seat_rgb = self.seat_color(e.owner) if e.owner else ENTITY_OWNER_DEFAULT
        seat_css = _rgb(seat_rgb)
        
        if e.entity_type == "probe":
            return (ENTITY_GLYPHS["probe"], seat_css)
        if e.entity_type == "harvester":
            if e.carrying_red:
                return (ENTITY_GLYPHS["harvester_carrying"], seat_css)
            return (ENTITY_GLYPHS["harvester"], seat_css)
        if e.entity_type == "orblift":
            # Orblifts are orbital → no map glyph. Returned for completeness.
            return (ENTITY_GLYPHS["orblift"], seat_css)
        return (" ", _rgb(ENTITY_OWNER_DEFAULT))

    def _entity_at_tile(self, x: int, y: int) -> Optional[Entity]:
        cands = [e for e in self.entities.values() if e.x == x and e.y == y]
        if not cands:
            return None

        def _prio(ent: Entity) -> int:
            if ent.entity_type == "probe":
                return 0
            if ent.entity_type == "harvester":
                return 1
            return 9

        cands.sort(key=_prio)
        return cands[0]

    def _sync_carrier(self, ent: Entity) -> None:
        ent.carrying_red = bool(ent.cargo_squares)

    def _mint_square_id(self) -> str:
        """Legacy fallback when the ledger is missing (defensive)."""
        self.square_uid_seq += 1
        return f"sq-{self.session_id[:6]}-{self.square_uid_seq:04x}"

    def _register_field_harvest(
        self,
        owner: PlayerId,
        harvester_id: str,
        x: int,
        y: int,
        origin_cell: Cell,
    ) -> str:
        """Tag harvested cell, stash manifest on harvester, append ledger.

        Called from :meth:`_harvest_at` for every coloured tile (RED,
        GREEN, BLUE) entered during drop or step. The parcel that
        travels with the harvester (and later into the hoard / orbit)
        carries:

        - ``square_id`` — canonical hex identity from :class:`SquareLedger`.
          For RED→GREEN harvests this is the **RED's original**
          identity (so the parcel records what was harvested, not the
          new synthetic-green tile that's left behind).
        - ``lineage`` — ``natural`` or ``synthetic`` (mirrored from the
          ledger row so the agent can spot "previously-harvested green").
        - ``tile_at_harvest`` / ``purity_at_harvest`` — the *origin*
          tile state before conversion, so the Vault can render the
          square visually after the fact.
        - ``paint`` — precomputed CSS-ready fg/bg/glyph for that origin.

        Additionally:
        - If the origin was RED, the ledger's existing RED row is
          stamped harvested **and** a synthetic-green row is minted at
          ``(x, y)`` with full provenance (the new GREEN tile that now
          sits on the surface owns its own identity).
        - If the origin was GREEN or BLUE, the ledger's existing row
          (natural OR synthetic) is stamped harvested; no new row
          minted, the cell becomes EMPTY.
        """
        if self.ledger is not None:
            site_id = self.ledger.lookup(x, y) or self._mint_square_id()
        else:
            site_id = self._mint_square_id()

        # Capture lineage from the ledger row so the parcel records
        # whether this was harvested from natural ground or a
        # previously-poisoned synthetic-green tile (load-bearing for
        # future reputation mechanics).
        lineage = "natural"
        parent_square_id: Optional[str] = None
        if self.ledger is not None:
            row = self.ledger.row_by_sid(site_id)
            if row:
                lineage = str(row.get("lineage") or "natural")
                parent_square_id = row.get("parent_square_id")

        # v0.9.2 — GREEN is binary, not graded. The generator stamps
        # green at 255 already, but the eval-scenario builder + any
        # future scripted seeds might place a fractional green; we
        # clamp at the parcel-creation boundary so the invariant
        # "every green in the vault is 255" can be relied on by the
        # UI ("just count green parcels") and by the score/jettison
        # math downstream.
        banked_purity = int(origin_cell.purity)
        if origin_cell.tile == Tile.GREEN:
            banked_purity = 255
        parcel: Dict[str, Any] = {
            "site_id": site_id,
            "square_id": site_id,
            "x": x,
            "y": y,
            "harvested_on_planning_day": self.day,
            "harvester_id": harvester_id,
            "tile_at_harvest": int(origin_cell.tile),
            "purity_at_harvest": banked_purity,
            "lineage": lineage,
            "parent_square_id": parent_square_id,
            "paint": dict(cell_to_paint(origin_cell)),
        }
        hh = self.entities[harvester_id]
        hh.cargo_squares.append(dict(parcel))
        self._sync_carrier(hh)
        self.harvest_log[owner].append(dict(parcel))

        # Close out the harvested row on the ledger so its lifecycle
        # ends here. Skip silently if the ledger isn't present (legacy
        # sessions) or doesn't recognise the id (defensive — should
        # never happen for natural-build cells).
        if self.ledger is not None:
            self.ledger.mark_harvested(
                site_id, harvested_on_day=self.day, harvested_by=harvester_id,
            )

        # RED → GREEN mints a new synthetic identity at (x, y) for the
        # freshly-poisoned green that's now on the surface. GREEN /
        # BLUE → EMPTY does NOT mint anything (the tile has no
        # substance left to identify).
        if (
            origin_cell.tile == Tile.RED
            and self.ledger is not None
        ):
            synth_sid = self.ledger.mint_synthetic_green(
                x=x,
                y=y,
                day=self.day,
                harvester_id=harvester_id,
                owner=owner,
                parent_square_id=site_id,
            )
            # Stash on the parcel as a forward-reference so the replay
            # / agent view can render "you poisoned square X, which
            # became synthetic Y". The PARCEL still carries site_id
            # (the original RED identity); minted_synthetic_id is
            # informational.
            hh.cargo_squares[-1]["minted_synthetic_id"] = synth_sid
            self.harvest_log[owner][-1]["minted_synthetic_id"] = synth_sid

        return site_id

    def _harvest_at(
        self,
        owner: PlayerId,
        harvester_id: str,
        x: int,
        y: int,
    ) -> Tuple[bool, Optional[str]]:
        """Harvest the coloured tile at ``(x, y)`` if any.

        Returns ``(harvested, site_id)``. ``harvested`` is True when a
        RED / GREEN / BLUE tile was banked into the harvester's cargo
        and the grid cell mutated accordingly:

        - RED   → GREEN(255)  (synthetic green minted; see
          :meth:`_register_field_harvest`)
        - GREEN → EMPTY(0)
        - BLUE  → EMPTY(0)

        Empty tiles are no-ops (returns ``(False, None)``). There is
        no per-color cap — every coloured entry banks a parcel; the
        natural limit is the harvester's hold (drop + 5 steps = 6
        parcels per outing). The vault tier-priority cascade in
        :meth:`deposit_haul_to_hoard` is what enforces overflow
        semantics at pickup time, NOT the harvest itself.
        """
        cell = self.grid[y][x]
        if cell.tile not in (Tile.RED, Tile.GREEN, Tile.BLUE):
            return False, None
        origin_cell = Cell(cell.tile, cell.purity)
        was_red = cell.tile == Tile.RED
        if was_red:
            self.grid[y][x] = Cell(Tile.GREEN, 255)
        else:
            self.grid[y][x] = Cell(Tile.EMPTY, 0)
        site_id = self._register_field_harvest(
            owner, harvester_id, x, y, origin_cell,
        )
        # Harvest-tracks overlay (§3.12 v0.7.3): only the RED → GREEN
        # conversion leaves a visible "residue" on the map (the
        # freshly minted synthetic-green tile that the new grid value
        # already encodes). Harvesting GREEN or BLUE removes the
        # tile entirely and leaves nothing behind beyond the normal
        # path trail. Marking those as "harvested" would tint an
        # empty cell green in the renderer, falsely implying there's
        # still synthetic-green sitting there — which is exactly the
        # confusion this branch existed to avoid. We now only mark
        # the cell when a synthetic-green identity was actually
        # minted (i.e., the source tile was RED).
        if was_red:
            self.track_harvests[owner].add(_xy_key(x, y))
            # v9 (I12) — if this pure cell was the last of a redsign seam,
            # retire the beacon so it stops luring seats into empty fog.
            if int(origin_cell.purity) == 255:
                self._retire_redsign_if_spent(x, y, owner)
        # Lifetime stats: the asset-ledger counter is named after RED
        # for historical reasons; we still bump it for non-RED harvests
        # so the chip's "lifetime harvested" surface stays meaningful
        # across colours.
        self._note_asset_harvested_red(harvester_id)
        return True, site_id

    def deposit_haul_to_hoard(
        self,
        owner: PlayerId,
        harvester_id: str,
        parcels: List[Dict[str, Any]],
        ops: List[str],
    ) -> None:
        """Deposit a harvester's cargo into the hoard with tier-priority cascade.

        While the hoard has free slots, parcels just append. Once the
        hoard is full each subsequent parcel is resolved by the §3.14
        cascade:

        - **GREEN incoming** displaces the lowest-purity vault BLUE if
          any; else the lowest-purity vault RED; else (vault all
          GREEN) the incoming GREEN is jettisoned.
        - **RED incoming** displaces the lowest-purity vault BLUE; else
          the lowest-purity vault RED **only if** incoming purity >
          vault RED's lowest purity; else jettisoned. Never displaces
          GREEN.
        - **BLUE incoming** displaces the lowest-purity vault BLUE
          **only if** incoming purity > vault BLUE's lowest purity;
          else jettisoned. Never displaces RED or GREEN.

        Every displacement / jettison is surfaced as a structured ops
        line so the watcher can show "displaced BLUE p=12 for RED
        p=180 (jettisoned)" — the storage story is now visible in the
        replay.
        """
        hoard = self.hoard_squares[owner]
        banked = 0
        displaced_count = 0
        jettisoned_count = 0
        for parcel in parcels:
            entry = dict(parcel)
            entry["stored_received_planning_day"] = self.day
            outcome = self._bank_with_tier_replacement(owner, entry, hoard, ops)
            if outcome == "banked":
                banked += 1
            elif outcome == "replaced":
                banked += 1
                displaced_count += 1
            else:  # outcome == "jettisoned"
                jettisoned_count += 1
        if parcels:
            tail = ""
            if displaced_count:
                tail += f"; +{displaced_count} via vault cascade"
            if jettisoned_count:
                tail += f"; −{jettisoned_count} jettisoned to outer space"
            ops.append(
                f"{owner} hoard {len(hoard)}/{HOARD_CAPACITY}; "
                f"{harvester_id} banked +{banked} squares{tail}",
            )
        else:
            ops.append(
                f"{owner} pickup {harvester_id} returned empty (hoard still {len(hoard)}/{HOARD_CAPACITY}).",
            )

    def _bank_with_tier_replacement(
        self,
        owner: PlayerId,
        parcel: Dict[str, Any],
        hoard: List[Dict[str, Any]],
        ops: List[str],
    ) -> str:
        """Resolve a single incoming parcel against the §3.14 tier ladder.

        Returns one of ``"banked"`` (added to vault, no displacement),
        ``"replaced"`` (vault was full; lower-tier parcel evicted to
        outer space), or ``"jettisoned"`` (no legal displacement; the
        incoming parcel itself goes to outer space).
        """
        if len(hoard) < HOARD_CAPACITY:
            hoard.append(parcel)
            return "banked"

        incoming_tile = int(parcel.get("tile_at_harvest", 0) or 0)
        incoming_purity = int(parcel.get("purity_at_harvest", 0) or 0)
        incoming_sid = parcel.get("square_id") or parcel.get("site_id") or "?"

        def _lowest_idx(tile_val: int) -> Optional[int]:
            best_i: Optional[int] = None
            best_p: int = 256
            for i, p in enumerate(hoard):
                if int(p.get("tile_at_harvest", 0) or 0) != tile_val:
                    continue
                pp = int(p.get("purity_at_harvest", 0) or 0)
                if pp < best_p:
                    best_p = pp
                    best_i = i
            return best_i

        def _replace(idx: int) -> str:
            displaced = hoard[idx]
            hoard[idx] = parcel
            ops.append(
                f"{owner} vault overflow: displaced "
                f"{_tile_name_from_int(int(displaced.get('tile_at_harvest', 0) or 0))} "
                f"p={int(displaced.get('purity_at_harvest', 0) or 0)} "
                f"@{displaced.get('square_id', '?')} for "
                f"{_tile_name_from_int(incoming_tile)} "
                f"p={incoming_purity} @{incoming_sid} (jettisoned)"
            )
            return "replaced"

        def _jettison() -> str:
            ops.append(
                f"{owner} vault overflow: jettisoned incoming "
                f"{_tile_name_from_int(incoming_tile)} "
                f"p={incoming_purity} @{incoming_sid} "
                f"(no legal displacement)"
            )
            return "jettisoned"

        # GREEN incoming — top tier, can displace BLUE or RED, but
        # never another GREEN (the doctrine "GREEN is sacred, you
        # don't trade it down" — §3.4 / §3.14).
        if incoming_tile == int(Tile.GREEN):
            i = _lowest_idx(int(Tile.BLUE))
            if i is not None:
                return _replace(i)
            i = _lowest_idx(int(Tile.RED))
            if i is not None:
                return _replace(i)
            return _jettison()

        # RED incoming — middle tier. Outranks BLUE structurally; can
        # displace another RED only if strictly higher purity. Never
        # touches GREEN.
        if incoming_tile == int(Tile.RED):
            i = _lowest_idx(int(Tile.BLUE))
            if i is not None:
                return _replace(i)
            i = _lowest_idx(int(Tile.RED))
            if i is not None:
                vault_p = int(hoard[i].get("purity_at_harvest", 0) or 0)
                if incoming_purity > vault_p:
                    return _replace(i)
            return _jettison()

        # BLUE incoming — bottom tier. Can only displace another BLUE,
        # and only at strictly higher purity.
        if incoming_tile == int(Tile.BLUE):
            i = _lowest_idx(int(Tile.BLUE))
            if i is not None:
                vault_p = int(hoard[i].get("purity_at_harvest", 0) or 0)
                if incoming_purity > vault_p:
                    return _replace(i)
            return _jettison()

        # Unknown tile — defensive fallback (shouldn't happen for
        # legal parcels). Jettison so the vault stays well-formed.
        return _jettison()

    @staticmethod
    def _parcel_purity(parcel: Mapping[str, Any]) -> int:
        """Best-effort 0..255 RED intensity for a single parcel.

        Parcels collected in-engine use ``purity_at_harvest``; parcels
        round-tripped through Snowflake's ``SOC_HOARD_PARCEL`` row
        mapping use ``origin_purity``; very old payloads might just have
        ``purity``. We accept any of the three and clamp into the canonical
        0..255 range so downstream score arithmetic never sees a None or
        out-of-band value.
        """
        raw = (
            parcel.get("purity_at_harvest")
            if isinstance(parcel.get("purity_at_harvest"), (int, float))
            else parcel.get("origin_purity")
            if isinstance(parcel.get("origin_purity"), (int, float))
            else parcel.get("purity")
        )
        try:
            v = int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            v = 0
        return max(0, min(255, v))

    def score_for(self, player: PlayerId) -> int:
        """Tier-weighted RED score across this player's **shipped** parcels.

        v0.9.6 (RULEBOOK §3.1) — only parcels that survived the Orbit
        catapult contribute, and each parcel's contribution is its
        **effective purity** (raw purity minus any cannibalisation the
        catapult fuel rule applied at ship time — see RULEBOOK §4.4)
        multiplied by :data:`RED_QUALITY_MULTIPLIER` for that parcel's
        tier. So a pure-255 ship scores 765, a vein-100 ship scores
        100, a trace-40 ship scores 30, etc. Tweak the multipliers in
        one place to retune the economy.

        Hoard RED earns nothing by sitting there: a player who
        stockpiles without shipping ends the season with unrealised
        value, recovering only the season-end fire-sale (50% of raw
        purity, no tier multiplier — :meth:`vault_red_loss_value`).

        v1.51 — the hoard is NOT ignored, though this said so until the
        BUG-12 audit. GREEN is charged wherever it sits, held or already
        disposed into SHIPPED, and the fire-sale is credited at close;
        both are inside :func:`compute_player_score`. The stale line
        here ("hoard contents explicitly do NOT count") outlived the
        v1.13 auto-disposal change and read as a licence to score off
        SHIPPED alone, which is how the station readout came to.
        """
        # Delegate to the module-level scorer so the watcher's season
        # picker (which scores straight off the persisted SHIPPED /
        # HOARD parcel tables, without hydrating a full GameSession) and
        # the end-of-game screen produce the EXACT same number.
        return compute_player_score(
            self.shipped_squares.get(player, []),
            self.hoard_squares.get(player, []),
            is_complete=self.is_season_complete(),
        )

    def vault_red_loss_value(self, player: PlayerId) -> float:
        """End-of-season fire-sale value of unshipped vault RED.

        Each remaining RED parcel sells at **50% of its raw purity**
        with **no tier multiplier** (RULEBOOK §4 — "red sold at a
        loss"). Returns the summed contribution; callers fold it into
        the final score and the end-of-game tally.
        """
        total = 0.0
        for parcel in self.hoard_squares.get(player, []):
            tile = parcel.get("tile_at_harvest")
            if tile is None:
                tile = parcel.get("origin_tile")
            try:
                if int(tile) != int(Tile.RED):
                    continue
            except (TypeError, ValueError):
                continue
            total += 0.5 * max(0, int(self._parcel_purity(parcel)))
        return total

    def _bump_stat(self, player: PlayerId, key: str, amount: float) -> None:
        """Accumulate a per-seat end-of-game tally (see :attr:`season_stats`)."""
        slot = self.season_stats.setdefault(
            str(player),
            {"credits_awarded": 0.0, "harvesters_built": 0.0, "blue_spent": 0.0},
        )
        slot[key] = float(slot.get(key, 0.0)) + float(amount)

    def _attrib(
        self,
        stat: str,
        attacker: Optional[str],
        victim: Optional[str],
        amount: int = 1,
    ) -> None:
        """Record ``amount`` of combat ``stat`` dealt by ``attacker`` to
        ``victim`` in the season attacker→victim matrix (see
        :attr:`combat_attrib`). Silently no-ops on missing seats so callers
        can stay terse. ``attacker == victim`` is legal (self-inflicted)."""
        if not attacker or not victim or amount == 0:
            return
        stat_m = self.combat_attrib.setdefault(str(stat), {})
        by_victim = stat_m.setdefault(str(attacker), {})
        by_victim[str(victim)] = int(by_victim.get(str(victim), 0)) + int(amount)

    def _bump_moves_cancelled(self, player: Optional[str]) -> None:
        """Increment the per-seat 'moves cancelled at execution' tally
        (waste / illegal-at-runtime / EMP-smothered / chaff-cancelled)."""
        if not player:
            return
        self.moves_cancelled[str(player)] = (
            int(self.moves_cancelled.get(str(player), 0)) + 1
        )

    def vault_green_count(self, player: PlayerId) -> int:
        """Number of GREEN parcels still sitting in this player's vault.

        Drives the green endgame penalty (:data:`GREEN_ENDGAME_PENALTY`)
        in :meth:`score_for` and is surfaced to the UI so a house can
        see how much toxic legacy it's carrying.
        """
        n = 0
        for parcel in self.hoard_squares.get(player, []):
            tile = parcel.get("tile_at_harvest")
            if tile is None:
                tile = parcel.get("origin_tile")
            try:
                if int(tile) == int(Tile.GREEN):
                    n += 1
            except (TypeError, ValueError):
                continue
        return n

    def is_season_complete(self) -> bool:
        """True once the season-cap dawn has fired (phase = SEASON_COMPLETE)."""
        return self.phase == Phase.SEASON_COMPLETE

    def _hoard_snap_payload(self) -> dict[str, Any]:
        # v0.9.5 — parcel rows arrive in two schemas: natural harvests
        # (RULEBOOK §3.13) carry the literal harvest cell as ``x`` /
        # ``y``; refined outputs minted by the Orbit ``refine`` action
        # carry their pre-refine anchor as ``origin_x`` / ``origin_y``
        # (the natural ``x`` / ``y`` is intentionally absent because a
        # refined parcel is the aggregate of multiple input cells).
        # The pre-v0.9.5 hard ``row["x"]`` read crashed the FIRST
        # replay-frame snapshot after a refine — surfaced as a
        # ``KeyError: 'x'`` HTTP 404 on TRANSMIT.
        def _coord(row: Dict[str, Any]) -> Optional[List[Any]]:
            xv = row.get("x", row.get("origin_x"))
            yv = row.get("y", row.get("origin_y"))
            if xv is None or yv is None:
                return None
            return [xv, yv]

        # v0.9.11 — sites now carry the full visual + provenance the
        # VAULT renderer needs (paint / purity / tile / harvest night).
        # The replay vault used to reconstruct hoard slots from
        # ``{id, cell}`` only, so scrubbing mid-night rendered every
        # parcel as a generic ``░░`` block with a "harvest night ?"
        # tooltip. Mirroring the live ``inventory_pack`` parcel shape
        # here makes the scrubbed vault identical to the live one.
        def _site(row: Dict[str, Any]) -> Dict[str, Any]:
            coord = _coord(row)
            return {
                "id": str(row.get("site_id") or row.get("square_id") or ""),
                "cell": coord,
                "paint": (
                    dict(row["paint"])
                    if isinstance(row.get("paint"), dict)
                    else None
                ),
                "tile_at_harvest": row.get(
                    "tile_at_harvest", row.get("origin_tile")
                ),
                "purity_at_harvest": row.get(
                    "purity_at_harvest", row.get("origin_purity")
                ),
                "harvested_on_planning_day": row.get(
                    "harvested_on_planning_day",
                    row.get("harvested_day"),
                ),
            }

        return {
            p: {
                "count": len(self.hoard_squares[p]),
                "sites": [_site(row) for row in self.hoard_squares[p][-40:]],
            }
            for p in self.players
        }

    def _weapons_snap_payload(self) -> dict[str, Any]:
        """v1.2 — per-frame weapons economy snapshot so the replay
        VAULT can render AVAILABLE (current stockpile) and USED
        (lifetime fired) accurately at any cursor, the same way
        ``_hoard_snap_payload`` backs the hoard grid. Keyed by seat →
        ``{stock: {emp, chaff, snap}, used: {emp, chaff, snap}}``,
        mirroring the live ``inventory_pack`` weapon block.

        Keyed off this game's own price table (v1.36) so an archived
        season renders the weapons it actually had — a frame stamped
        with a key that game never knew reads to the VAULT as a bay
        stuck at zero, which looks like a bug in the replay."""
        keys = tuple(self.weapon_prices())
        out: Dict[str, Any] = {}
        for p in self.players:
            stock = self.weapon_stock.get(p, {})
            used = self.weapons_used.get(p, {})
            out[p] = {
                "stock": {k: int(stock.get(k, 0) or 0) for k in keys},
                "used": {k: int(used.get(k, 0) or 0) for k in keys},
            }
        return out

    def _replay_entity_summaries(self) -> List[dict[str, Any]]:
        rows: List[dict[str, Any]] = []
        for eid, ent in sorted(self.entities.items(), key=lambda t: t[0]):
            row: Dict[str, Any] = {"id": eid, "t": ent.entity_type, "owner": ent.owner}
            row["surface"] = [ent.x, ent.y] if ent.x is not None else None
            if ent.entity_type == "harvester":
                row["cargo"] = [c.get("site_id") for c in ent.cargo_squares]
                row["lost"] = bool(ent.lost_last_night)
                row["damaged"] = bool(getattr(ent, "damaged", False))
            rows.append(row)
        return rows

    def _replay_snapshot_player_dense(self, player: PlayerId) -> List[dict[str, Any]]:
        """Capture fogged percept without letting _blend_memory advance stored memory."""
        mem_backup = copy.deepcopy(self.memory_tiles[player])
        try:
            return copy.deepcopy(self.player_dense_view(player))
        finally:
            self.memory_tiles[player] = mem_backup

    def replay_push_scene(
        self,
        timeline: List[dict[str, Any]],
        caption: str,
        *,
        owner: Optional[str] = None,
        tag: Optional[str] = None,
        collisions: Optional[List[Dict[str, Any]]] = None,
        crushed_probes: Optional[List[Dict[str, Any]]] = None,
        attempted: Optional[str] = None,
        outcome: Optional[str] = None,
        hour: Optional[int] = None,
        mine: Optional[List[Dict[str, Any]]] = None,
        emp: Optional[List[Dict[str, Any]]] = None,
        chaff: Optional[List[Dict[str, Any]]] = None,
        snap: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Append one snapshot frame to the night replay timeline.

        ``owner`` is the player whose move produced this frame, used by the
        client to filter to "my moves only" if needed. ``tag`` carries a
        short kind ("probe" / "drop" / "step" / "pickup" / "waste" /
        "dawn" / "collision_swap" / "open"). ``collisions`` is a
        structured list of collision events that landed on this tick
        (consumed from :attr:`pending_collision_events`); the frontend
        uses it to drive the v0.7.3 ring animation. ``attempted`` +
        ``outcome`` carry per-action accounting (so the synced log
        drawer can render failed actions in red even though their
        cell-state did not change). ``hour`` (v0.7.4) stamps the
        planetary night clock onto the frame — 1..21 for in-night
        actions, 0 for opener and dawn — so the watcher can read
        *when* tonight's chaos unfolded without counting frames.
        """
        # v0.9.6 — N-seat replay snapshot. Build a per-seat dense
        # snapshot for every seat in :attr:`players` so a 3- or
        # 4-player replay scrubs cleanly. The legacy ``cells_player_p1``
        # / ``cells_player_p2`` keys are still emitted alongside the
        # new ``cells_by_seat`` map for back-compat with older
        # frontend builds; the next major rev can drop them.
        cells_by_seat: Dict[str, Any] = {
            p: self._replay_snapshot_player_dense(cast(PlayerId, p))
            for p in self.players
        }
        # ``cells_player`` is the legacy "viewer" alias — defaults to
        # the first seat so an unaware client still gets something
        # painted instead of a blank canvas.
        default_seat = self.players[0] if self.players else "p1"
        frame: Dict[str, Any] = {
            "w": len(timeline),
            "caption": caption,
            "owner": owner,
            "tag": tag,
            "cells": copy.deepcopy(self._observer_cells_packed()),
            "cells_by_seat": cells_by_seat,
            "cells_player": cells_by_seat.get(default_seat),
            "entities": copy.deepcopy(self._replay_entity_summaries()),
            "hoard": copy.deepcopy(self._hoard_snap_payload()),
            "weapons": copy.deepcopy(self._weapons_snap_payload()),
            # v1.1 — per-frame credits snapshot so the replay VAULT's
            # CREDITS readout tracks the balance at the cursor (it only
            # changes between nights, at Orbit settlement, so within a
            # night every frame shares the same value). Survives the file
            # store verbatim; the Snowflake schema drops unknown columns,
            # so Snowflake-backed replays fall back to the live readout.
            "credits": {
                p: int(self.credits.get(p, 0) or 0) for p in self.players
            },
        }
        # v0.9.11 — emit a ``cells_player_pN`` alias for EVERY active
        # seat (was p1/p2 only). The OBS replay view combines per-seat
        # vision to decide which cells are collectively unseen; without
        # p3/p4 aliases a 3-/4-seat OBS dimmed every cell only p3 or p4
        # could see. ``cells_by_seat`` already carries all seats, but
        # the Snowflake round-trip historically dropped it, so the
        # explicit aliases keep both backends honest.
        for _seat, _cells in cells_by_seat.items():
            frame[f"cells_player_{_seat}"] = _cells
        if collisions:
            frame["collisions"] = [dict(c) for c in collisions]
        if crushed_probes:
            frame["crushed_probes"] = [dict(c) for c in crushed_probes]
        if mine:
            frame["mine"] = [dict(c) for c in mine]
        if emp:
            frame["emp"] = [dict(c) for c in emp]
        if chaff:
            frame["chaff"] = [dict(c) for c in chaff]
        if snap:
            frame["snap"] = [dict(c) for c in snap]
        if attempted is not None:
            frame["attempted"] = attempted
        if outcome is not None:
            frame["outcome"] = outcome
        if hour is not None:
            frame["hour"] = int(hour)
        # v0.9 — surface active cloud cells on every frame so the
        # watcher can render the dimmed/flashing EMP area without
        # having to reconstruct from launches. Cheap snapshot: a list
        # of ``{cx, cy, r, hours_remaining}`` dicts.
        if self.emp_clouds:
            frame["emp_clouds"] = [
                {
                    "cx": int(c.get("cx", 0)),
                    "cy": int(c.get("cy", 0)),
                    "r": int(c.get("radius", 0)),
                    "hours_remaining": int(c.get("hours_remaining", 0)),
                    "owner": str(c.get("owner", "")),
                }
                for c in self.emp_clouds
                if int(c.get("hours_remaining", 0)) > 0
            ]
        # v1.36 — the same snapshot for SNAP scorch marks. Its own key
        # rather than folded into ``emp_clouds``: a client that paints
        # one of these as an EMP cloud would be telling the watcher a
        # harvester standing there is smothered, and it is not.
        if self.snap_clouds:
            frame["snap_clouds"] = [
                {
                    "cx": int(c.get("cx", 0)),
                    "cy": int(c.get("cy", 0)),
                    "r": int(c.get("radius", 0)),
                    "hours_remaining": int(c.get("hours_remaining", 0)),
                    "owner": str(c.get("owner", "")),
                }
                for c in self.snap_clouds
                if int(c.get("hours_remaining", 0)) > 0
            ]
        # v1.31 — a ``mines_active`` snapshot used to ride every frame so
        # the watcher could paint a persistent rhombus on each mined
        # cell. Nothing can arm a caltrop now and load clears the rest,
        # so the block was unreachable. ARCHIVED frames still carry the
        # key and the client still renders it — that is the point of
        # retiring rather than deleting.
        # v1.2 — probe ring-degrade lands AT dawn. ``probe_nights_remaining``
        # is ``self.day``-relative and the dawn frame is captured BEFORE the
        # day counter rolls, so a surviving probe's ring count would only
        # drop on the NEXT night's first frame. For the hour-22 ``dawn``
        # frame, decrement each surviving probe's ``nights_remaining`` by one
        # so the loss (one fewer concentric ring) shows during the sunrise
        # sweep — matching the next night's baked value (no flicker). Expired
        # probes are already gone (``decay_probes``), so survivors never hit
        # 0 here. The cell-array aliases (``cells_player`` / ``cells_player_*``)
        # share list objects with ``cells_by_seat``, so we dedupe by id() to
        # avoid double-decrementing the same probe.
        if tag == "dawn":
            _seen_obj_ids: Set[int] = set()

            def _degrade_probe_life(obj: Any) -> None:
                oid = id(obj)
                if oid in _seen_obj_ids:
                    return
                _seen_obj_ids.add(oid)
                if isinstance(obj, dict):
                    if "nights_remaining" in obj:
                        kind = str(obj.get("type") or obj.get("entity_type") or "")
                        ident = str(obj.get("id") or "")
                        if kind == "probe" or ident.startswith("probe"):
                            try:
                                _nr = int(obj["nights_remaining"])
                                obj["nights_remaining"] = max(0, _nr - 1)
                            except (TypeError, ValueError):
                                pass
                    for _v in obj.values():
                        _degrade_probe_life(_v)
                elif isinstance(obj, list):
                    for _v in obj:
                        _degrade_probe_life(_v)

            for _ck in ("cells", "cells_by_seat", "cells_player", "entities"):
                if _ck in frame:
                    _degrade_probe_life(frame[_ck])
            for _seat in cells_by_seat:
                _degrade_probe_life(frame.get(f"cells_player_{_seat}"))
        timeline.append(frame)

    def _occupants_observer(self, x: int, y: int) -> List[dict[str, Any]]:
        ents = [
            ent
            for ent in self.entities.values()
            if ent.x == x and ent.y == y
        ]
        ents.sort(key=lambda e: public_entity_title(e))
        return [occupant_wire(e, self) for e in ents]

    def _inject_occupants(
        self,
        cells: List[dict[str, Any]],
        *,
        omniscient: bool,
        viewer: Optional[str] = None,
    ) -> None:
        w = self.width
        vp: Optional[PlayerId] = cast(PlayerId, viewer) if viewer else None
        vis: Optional[Set[Tuple[int, int]]] = (
            self.tiles_visible_now(vp) if vp is not None else None
        )
        for y in range(self.height):
            for x in range(w):
                idx = y * w + x
                if omniscient:
                    occ = self._occupants_observer(x, y)
                    if occ:
                        cells[idx]["occupants"] = occ
                    continue

                slot = cells[idx]
                if slot.get("kind") != "terrain" or slot.get("stale"):
                    continue
                assert vp is not None and vis is not None
                occ_body: List[dict[str, Any]] = []
                for ent in self.entities.values():
                    if ent.x != x or ent.y != y:
                        continue
                    if self._may_see_entity(vp, ent, vis):
                        occ_body.append(occupant_wire(ent, self))
                if occ_body:
                    occ_body.sort(key=lambda row: row["label"])
                    slot["occupants"] = occ_body

    def inventory_pack(self, player: PlayerId) -> dict[str, Any]:
        """Assets + hoard + shipped ledger for HUD.

        ``assets_by_status`` partitions every owned asset (alive or
        destroyed) into ``in_orbit`` / ``on_surface`` / ``destroyed``
        buckets, each row merging the lifecycle record with the
        currently live :class:`Entity` state (cargo, position). It's
        the source of truth for the VAULT panel's three asset
        sub-sections.
        """
        lifts = [
            ent
            for ent in self.entities.values()
            if ent.owner == player and ent.entity_type == "orblift"
        ]
        orbital_hold = sum(1 for lf in lifts if lf.orbital_cargo_red) > 0
        hoard_copy = [dict(row) for row in self.hoard_squares[player]]
        shipped_copy = [dict(row) for row in self.shipped_squares.get(player, [])]
        # v0.9.5 — surface BOTH bays of the weapons economy on the
        # inventory_pack so the VAULT panel can render "AVAILABLE"
        # + "USED" without a second view round-trip. The shape
        # mirrors :attr:`weapon_stock` directly; ``weapons_used``
        # is the lifetime counter bumped on every successful fire.
        wstock = self._ensure_weapon_stock_slot(player)
        wused = self._ensure_weapons_used_slot(player)
        return {
            "assets": self.unit_summary_for_owner(player),
            "assets_by_status": self._assets_by_status(player),
            "hoard": hoard_copy,
            "hoard_capacity": HOARD_CAPACITY,
            "shipped": shipped_copy,
            "shipped_capacity": SHIPPED_CAPACITY,
            "field_harvest_journal": [
                dict(row) for row in self.harvest_log.get(player, [])
            ],
            "harvested_tiles": hoard_copy,
            "orbital_holds_red": orbital_hold,
            "weapon_stock": {
                k: int(wstock.get(k, 0)) for k in self.weapon_prices()
            },
            "weapons_used": {
                k: int(wused.get(k, 0)) for k in self.weapon_prices()
            },
        }

    def _assets_by_status(self, player: PlayerId) -> Dict[str, List[Dict[str, Any]]]:
        """Bucket the player's asset ledger rows by current status.

        Each row merges the persisted :class:`AssetRecord` (lifetime
        stats) with the live :class:`Entity` state (current cargo,
        position) so the HUD only has to walk one list per section. A
        destroyed asset never re-resurfaces in ``in_orbit`` /
        ``on_surface`` — once ``destroyed_on_day`` is set, the row
        lives in ``destroyed`` for the rest of the session.
        """
        in_orbit: List[Dict[str, Any]] = []
        on_surface: List[Dict[str, Any]] = []
        destroyed: List[Dict[str, Any]] = []

        # Lifecycle rows we *do* have. Living entities without a record
        # yet (legacy sessions deserialised from pre-asset-ledger
        # payloads) get a synthesised one so they still show up.
        for ent in self.entities.values():
            if ent.owner != player:
                continue
            self._ensure_asset_record(ent)

        for rec in self.asset_records.values():
            if rec.owner != player:
                continue
            row = rec.to_dict()
            ent = self.entities.get(rec.asset_id)
            row["alive"] = rec.is_alive() and ent is not None
            if ent is not None:
                row["current_pos"] = (
                    [ent.x, ent.y] if ent.x is not None else None
                )
                if rec.asset_type == "harvester":
                    row["carrying_red"] = bool(ent.carrying_red)
                    row["current_cargo_count"] = len(ent.cargo_squares)
                    # v0.9.5 — surface the live ``damaged`` flag onto
                    # the row so both the VAULT chip and the ORDERS
                    # roster chip can render the wrench state without
                    # a second lookup. Repair history (``repair_count``
                    # / ``last_repaired_day``) already rides on the
                    # AssetRecord half of ``row``.
                    row["damaged"] = bool(getattr(ent, "damaged", False))
                if rec.asset_type == "orblift":
                    row["holds_red"] = bool(ent.orbital_cargo_red)
                if rec.asset_type == "probe" and ent.x is not None:
                    from sea_of_colours.game.tuning import probe_lifetime_nights as _plt
                    _k = _plt()
                    if _k:
                        _first = int(
                            rec.first_deployed_day
                            if rec.first_deployed_day is not None
                            else rec.created_on_day or 0
                        )
                        row["nights_remaining"] = max(
                            0, int(_k) - max(0, int(self.day) - _first)
                        )
            if rec.destroyed_on_day is not None or ent is None:
                # An entity disappearing without a destroyed_on_day is
                # rare today but worth handling: treat the row as
                # destroyed for display purposes.
                destroyed.append(row)
                continue
            if ent.x is None:
                in_orbit.append(row)
            else:
                on_surface.append(row)

        in_orbit.sort(key=lambda r: (r.get("asset_type", ""), r.get("asset_id", "")))
        on_surface.sort(key=lambda r: (r.get("asset_type", ""), r.get("asset_id", "")))
        destroyed.sort(
            key=lambda r: (r.get("destroyed_on_day") or 0, r.get("asset_id", "")),
        )
        return {
            "in_orbit": in_orbit,
            "on_surface": on_surface,
            "destroyed": destroyed,
        }

    def policy_hints(self, player: PlayerId) -> dict[str, Any]:
        """Authoring helpers for the queue-style policy editor."""
        allowed = sorted(self._allowed_ids(player))
        hv = next((k for k in allowed if k.startswith("harvester_")), None)

        bx = max(4, min(self.width - 6, self.width // 4))
        by = max(6, min(self.height - 10, self.height // 4))
        px = max(8, self.width // 5)
        py = max(6, self.height // 5)

        quick_inserts: List[dict[str, Any]] = [
            {
                "id": "probe",
                "label": "probe @one tile",
                "json": [{"a": "probe", "at": [px, py]}],
            },
        ]
        if hv:
            quick_inserts.append(
                {
                    "id": "drop_walk_pickup",
                    "label": "drop · walk · pickup",
                    "json": [
                        {"a": "drop", "unit": hv, "at": [bx, by]},
                        {"a": "step", "unit": hv, "to": [bx + 1, by]},
                        {"a": "step", "unit": hv, "to": [bx + 2, by]},
                        {"a": "pickup", "unit": hv},
                    ],
                },
            )
            quick_inserts.append(
                {
                    "id": "step",
                    "label": f"step {hv}",
                    "json": [{"a": "step", "unit": hv, "to": [bx + 1, by]}],
                },
            )
            quick_inserts.append(
                {
                    "id": "pickup",
                    "label": f"pickup {hv}",
                    "json": [{"a": "pickup", "unit": hv}],
                },
            )

        return {
            "entity_ids": allowed,
            "actions": ["probe", "drop", "step", "pickup"],
            "max_moves": MAX_MOVES,
            "quick_inserts": quick_inserts,
        }

    def _annotate_entity_glyphs_on_cells(
        self,
        cells: List[dict[str, Any]],
        viewer: Optional[str],
        *,
        omniscient: bool = False,
    ) -> None:
        w = self.width
        assert omniscient or viewer is not None
        viewer_p = cast(PlayerId, viewer) if viewer is not None else None
        for y in range(self.height):
            for x in range(w):
                idx = y * w + x
                ent = self._entity_at_tile(x, y)
                if ent is None:
                    continue
                if omniscient:
                    vis_ok = True
                else:
                    vis = self.tiles_visible_now(viewer_p)  # type: ignore[arg-type]
                    vis_ok = self._may_see_entity(viewer_p, ent, vis)  # type: ignore[arg-type]
                if not vis_ok:
                    continue
                gh, fg = self._glyph_for_entity(ent)
                if gh.strip():
                    e: Dict[str, Any] = {"ch": gh, "fg": fg}
                    if ent.entity_type == "harvester":
                        e["carrying"] = bool(ent.carrying_red)
                        e["damaged"] = bool(getattr(ent, "damaged", False))
                    if ent.entity_type in ("harvester", "probe"):
                        e["id"] = ent.id
                        e["idx"] = _unit_ordinal(ent.id)
                    if ent.entity_type == "probe":
                        from sea_of_colours.game.tuning import probe_lifetime_nights as _plt
                        _k = _plt()
                        if _k:
                            _rec = self.asset_records.get(ent.id)
                            _first = int(
                                (_rec.first_deployed_day if _rec and _rec.first_deployed_day is not None
                                 else _rec.created_on_day if _rec else 0) or 0
                            )
                            e["nights_remaining"] = max(0, int(_k) - max(0, int(self.day) - _first))
                    cells[idx]["entity"] = e

    def _annotate_entity_glyphs_on_player_cells(
        self, dense: List[dict[str, Any]], player: PlayerId
    ) -> None:
        w = self.width
        vis = self.tiles_visible_now(player)
        for y in range(self.height):
            for x in range(w):
                idx = y * w + x
                cell = dense[idx]
                ent = self._entity_at_tile(x, y)
                if ent is None:
                    continue
                if cell.get("echo_probe"):
                    continue
                if not self._may_see_entity(player, ent, vis):
                    continue
                if cell.get("kind") == "fog":
                    continue
                gh, fg = self._glyph_for_entity(ent)
                if gh.strip():
                    e: Dict[str, Any] = {"ch": gh, "fg": fg}
                    if ent.entity_type == "harvester":
                        e["carrying"] = bool(ent.carrying_red)
                        e["damaged"] = bool(getattr(ent, "damaged", False))
                    if ent.entity_type in ("harvester", "probe"):
                        e["id"] = ent.id
                        e["idx"] = _unit_ordinal(ent.id)
                    if ent.entity_type == "probe":
                        from sea_of_colours.game.tuning import probe_lifetime_nights as _plt
                        _k = _plt()
                        if _k:
                            _rec = self.asset_records.get(ent.id)
                            _first = int(
                                (_rec.first_deployed_day if _rec and _rec.first_deployed_day is not None
                                 else _rec.created_on_day if _rec else 0) or 0
                            )
                            e["nights_remaining"] = max(0, int(_k) - max(0, int(self.day) - _first))
                    cell["entity"] = e

    # --- Policy ingestion -------------------------------------------

    def _allowed_ids(self, player: PlayerId) -> Set[str]:
        return {k for k, ent in self.entities.items() if ent.owner == player}

    def stash_policy(
        self, player: PlayerId, payload: Mapping[str, Any] | List[Any] | None
    ) -> Tuple[bool, List[str]]:
        """Validate + store a move queue for ``player``.

        Accepts either a bare ``[move, …]`` list or ``{"moves": [...]}``.
        Parse-level errors per move become :class:`WasteMove` markers and
        do **not** reject the policy — they still consume a slot at
        execution. The session ``errors`` map keeps any structural
        complaints so the UI can surface them.
        """
        errs: List[str] = []
        if self.phase != Phase.PLANNING:
            # The SEASON_COMPLETE message is tuned so the orchestrator /
            # CLI can match on the substring and surface a friendly
            # "click NEW GAME" banner instead of a raw stack trace.
            if self.phase == Phase.SEASON_COMPLETE:
                errs.append(
                    f"season complete (day cap = {self.season_day_cap}); "
                    f"start a new game to play another season."
                )
            else:
                errs.append(
                    f"wrong phase ({self.phase.value}); "
                    f"policies only during planning."
                )
            self.errors[player] = errs
            return False, errs

        moves, hard_errs = parse_moves(payload)
        if hard_errs:
            self.errors[player] = hard_errs
            return False, hard_errs

        self.pending_policies[player] = moves
        self.errors[player] = []
        self.log_info(
            f"day {self.day}: {player} locked policy ({len(moves)} move(s))."
        )
        return True, []

    def log_info(self, text: str) -> None:
        """Append an informational entry to :attr:`log`.

        v0.9.1 — lines that look like orbit settlement chatter
        (``[orbit] ...``) auto-stamp ``phase="orbit"`` + the current
        day so the frontend log filter can split them out without
        re-parsing the text on every render.

        v0.9.8 — EVERY entry now carries a ``day`` field at write
        time (not just orbit chatter). Prior to v0.9.8 only [orbit]
        lines tagged ``day``; with the new batched bot fan-out the
        same ``append_log`` call now spans multiple days of events,
        so the database / replay viewer NEEDS the per-entry day to
        attribute each line to the correct night. The frontend
        LOG / AGENT filters fall back gracefully on rows whose
        ``day`` is missing (legacy sessions).
        """
        entry: Dict[str, Any] = {
            "level": "info",
            "text": text,
            "day": int(self.day),
        }
        if isinstance(text, str) and text.startswith("[orbit]"):
            entry["phase"] = "orbit"
        self.log.append(entry)

    def log_error(self, text: str) -> None:
        """Append an error entry to :attr:`log` — rendered yellow in the HUD.

        Same orbit-stamp behaviour as :meth:`log_info`."""
        entry: Dict[str, Any] = {
            "level": "error",
            "text": text,
            "day": int(self.day),
        }
        if isinstance(text, str) and text.startswith("[orbit]"):
            entry["phase"] = "orbit"
        self.log.append(entry)

    def log_event(self, kind: str, text: str, **data: Any) -> None:
        """Append a *structured* log entry — text for the HUD, plus a
        machine-readable ``kind`` and ``data`` dict the harness can
        filter on (used by ``build_agent_view`` to surface things like
        ``probe_launch`` and ``probe_collision`` into the cortex agent's
        ``competitor_intel.new_this_day`` block without parsing
        free-text). Backwards compatible with consumers that only read
        ``level`` / ``text`` — they see this as a regular info row."""
        self.log.append(
            {
                "level": "info",
                "text": text,
                "kind": kind,
                "data": dict(data),
                "day": int(self.day),
            },
        )

    def both_ready(self) -> bool:
        # v0.9.6 — "both_ready" survives as a method name for API
        # symmetry but the body now demands ALL seats in
        # :attr:`players` be stashed. Same predicate at 2 seats; a
        # multi-seat game just requires every seat to lock.
        return all(self.pending_policies.get(p) is not None for p in self.players)

    #: v0.9.6 — explicit alias that doesn't carry the legacy "both"
    #: framing. Engine and tests should prefer this for N-seat reads.
    all_ready = both_ready

    def maybe_resolve_if_ready(self) -> bool:
        if not self.both_ready():
            return False
        from sea_of_colours.game.simulator import NightSimulator

        queues = {
            p: list(self.pending_policies.get(p) or []) for p in self.players
        }
        NightSimulator().run(self, queues)
        return True

    # --- Orbit phase plumbing (v0.8.0) ------------------------------

    def stash_orbit_actions(
        self,
        player: PlayerId,
        payload: Mapping[str, Any] | List[Any] | None,
    ) -> Tuple[bool, List[str]]:
        """Validate + store an Orbit action queue for ``player``.

        Counterpart to :meth:`stash_policy` for the daytime Orbit
        phase. Parse-level errors per action become
        :class:`OrbitWasteAction` markers and surface as yellow log
        lines at resolution time without consuming a slot.
        """
        from sea_of_colours.game.policy import (
            OrbitAction,
            parse_orbit_actions,
        )

        errs: List[str] = []
        if self.phase != Phase.ORBIT:
            if self.phase == Phase.SEASON_COMPLETE:
                errs.append(
                    f"season complete (day cap = {self.season_day_cap}); "
                    f"start a new game to play another season."
                )
            else:
                errs.append(
                    f"wrong phase ({self.phase.value}); "
                    f"orbit actions only during orbit."
                )
            self.errors[player] = errs
            return False, errs

        # v1.13 — no action cap: credits and blue are the only constraint.
        actions, hard_errs = parse_orbit_actions(payload)
        if hard_errs:
            self.errors[player] = hard_errs
            return False, hard_errs

        # v1.13 — the final orbit used to be restricted to refine / ship /
        # green-flush, because those were the only things that still
        # mattered once the season was ending. All three are gone and
        # settlement is automatic, so there is nothing left to restrict:
        # a seat may buy on the last orbit, it just won't get to use it.
        # Wasting your own credits is a legal move, not an error.

        # Store as the raw OrbitAction list — the resolver consumes
        # this shape directly. ``pending_orbit_actions`` is typed as
        # ``Optional[List[Any]]`` so we don't import the union into
        # the field annotation (circular at dataclass build time).
        self.pending_orbit_actions[player] = list(actions)
        self.errors[player] = []
        self.log_info(
            f"day {self.day}: {player} locked orbit ({len(actions)} action(s))."
        )
        return True, []

    def both_orbit_ready(self) -> bool:
        return all(
            self.pending_orbit_actions.get(p) is not None for p in self.players
        )

    #: v0.9.6 — N-seat-friendly alias matching :attr:`all_ready`.
    all_orbit_ready = both_orbit_ready

    def maybe_resolve_orbit_if_ready(self) -> bool:
        """Trigger the Orbit settlement once both seats have locked.

        Mirrors :meth:`maybe_resolve_if_ready` for the night phase.
        Returns True when the resolver ran (phase advances to
        :data:`Phase.PLANNING`); False otherwise.
        """
        if not self.both_orbit_ready():
            return False
        from sea_of_colours.game.orbit_resolver import OrbitResolver

        queues: Dict[str, List[Any]] = {
            p: list(self.pending_orbit_actions.get(p) or []) for p in self.players
        }
        OrbitResolver().run(self, queues)
        return True

    def spend_credits(self, player: PlayerId, amount: int) -> bool:
        """Debit ``amount`` credits from ``player`` if affordable.

        Returns True (and deducts) when the balance covers the cost,
        False (no deduction) otherwise. Used by the per-parcel RED
        catapult bid lock (RULEBOOK §4.4) where credits are forfeit the
        instant the bid is programmed and ordered affordability is
        re-checked at settlement time.
        """
        amount = max(0, int(amount))
        if int(self.credits.get(player, 0)) < amount:
            return False
        self.credits[player] = int(self.credits.get(player, 0)) - amount
        return True

    def award_orbit_credits(self) -> None:
        """Top up every seat's credits balance by
        :data:`ORBIT_CREDITS_PER_TURN`. Called once at Orbit-phase
        entry by the simulator (and by :meth:`OrbitResolver` so an
        explicit ``run`` from a fresh load also gets the award).
        Idempotent within a single Orbit pass — gated on
        :attr:`_orbit_credits_awarded_day` (set after the award)."""
        cur_day = int(self.day)
        last = int(getattr(self, "_orbit_credits_awarded_day", 0) or 0)
        if last >= cur_day:
            return
        for p in self.players:
            self.credits[p] = int(self.credits.get(p, 0)) + ORBIT_CREDITS_PER_TURN
            self._bump_stat(p, "credits_awarded", ORBIT_CREDITS_PER_TURN)
        self._orbit_credits_awarded_day = cur_day  # type: ignore[attr-defined]
        self.log_info(
            f"[orbit] day {cur_day}: +{ORBIT_CREDITS_PER_TURN} credits "
            f"awarded to every seat."
        )

    def award_tutorial_blue_topup(self) -> None:
        """v1.34 — hand the teaching seat the blue its next lesson costs.

        The Advanced tutorial asks the player to buy one weapon per
        orbit — an EMP, then a SNAP, then a chaff. Only the EMP is
        affordable out of the opening bank, because it spends 200 of the
        250 and a few nights on a 24x16 board will not reliably mine the
        400 the other two want. The lesson was therefore failing on the
        economy rather than on anything it meant to teach.

        Three things this deliberately is not:

        * **Not silent.** It logs, and the orbit reel says it out loud.
          A tutorial that quietly edits your balance is teaching an
          economy that does not exist, and the player will carry that
          misunderstanding into a real season.
        * **Not for every seat.** Only seats a human is flying. The
          heuristic opponent handed a weapon's worth of blue buys a
          weapon with it, and the Advanced films — shot on this exact
          preset and seed — would stop matching the game the player is
          looking at.
        * **Not a rule.** Gated on ``tutorial`` naming a teaching
          preset, so no ordinary season can reach it, including
          ``quick``, which is a short game and not a lesson.

        Idempotent within a day, mirroring :meth:`award_orbit_credits` —
        the gate is persisted so a reload cannot re-gift. Note the gate
        is "the last day paid", not "has been paid", which is what lets
        v1.36 make a second grant on day 4 without touching it.
        """
        from sea_of_colours.game.tutorial import (
            blue_grant_for,
            blue_grant_weapon_for,
        )

        cur_day = int(self.day)
        preset = getattr(self, "tutorial", "")
        amount = blue_grant_for(preset, cur_day)
        if amount <= 0:
            return
        last = int(getattr(self, "_tutorial_blue_granted_day", 0) or 0)
        if last >= cur_day:
            return
        seats = [
            p for p in self.players
            if str(self.agents.get(p, "human")).lower() == "human"
        ]
        self._tutorial_blue_granted_day = cur_day  # type: ignore[attr-defined]
        if not seats:
            return
        for p in seats:
            self.blue_bank[p] = int(self.blue_bank.get(p, 0)) + amount
        kind = blue_grant_weapon_for(preset, cur_day).upper() or "weapon"
        self.log_info(
            f"[tutorial] day {cur_day}: +{amount} BLUE granted to "
            f"{', '.join(seats)} — a training subsidy, enough for one "
            f"{kind}. Real seasons mine their own."
        )

    def harvesters_owned_alive(self, player: PlayerId) -> int:
        """Count harvesters owned by ``player`` that are not destroyed.

        Includes both orbital and surface-deployed harvesters. Used by
        :meth:`apply_build_harvester` to enforce
        :data:`HARVESTER_MAX_PER_PLAYER`.
        """
        n = 0
        for ent in self.entities.values():
            if ent.entity_type != "harvester":
                continue
            if ent.owner != player:
                continue
            n += 1
        return n

    def apply_build_harvester(
        self, player: PlayerId,
    ) -> Tuple[bool, str]:
        """Mint a new orbital harvester. Costs :data:`HARVESTER_BUILD_COST`."""
        if self.credits.get(player, 0) < HARVESTER_BUILD_COST:
            return False, (
                f"{player}: build_harvester requires "
                f"{HARVESTER_BUILD_COST}c (have "
                f"{self.credits.get(player, 0)}c)"
            )
        if self.harvesters_owned_alive(player) >= HARVESTER_MAX_PER_PLAYER:
            return False, (
                f"{player}: build_harvester refused — already at "
                f"{HARVESTER_MAX_PER_PLAYER}/"
                f"{HARVESTER_MAX_PER_PLAYER} harvester cap"
            )
        # Mint a new harvester entity in orbit. The id format mirrors
        # the spawn_defaults convention (harvester_<owner>_<idx>) so
        # downstream tooling that filters on the prefix still works.
        idx = 1 + sum(
            1 for e in self.entities.values()
            if e.entity_type == "harvester" and e.owner == player
        )
        new_id = f"harvester_{player}_{idx}"
        # Collide-avoid: if the synthesized id is already taken, walk
        # the counter forward until a free slot is found.
        while new_id in self.entities:
            idx += 1
            new_id = f"harvester_{player}_{idx}"
        ent = Entity(
            id=new_id,
            entity_type="harvester",
            owner=str(player),
            x=None,
            y=None,
            carrying_red=False,
            orbital_cargo_red=False,
            cargo_squares=[],
            lost_last_night=False,
            damaged=False,
        )
        self.entities[new_id] = ent
        self.asset_records[new_id] = AssetRecord(
            asset_id=new_id,
            asset_type="harvester",
            owner=str(player),
            session_id=self.session_id,
            created_on_day=self.day,
            first_deployed_day=None,
            last_seen_x=None,
            last_seen_y=None,
        )
        self.credits[player] -= HARVESTER_BUILD_COST
        self._bump_stat(player, "harvesters_built", 1)
        self._persist_asset_ledger()
        return True, (
            f"{player}: built harvester {new_id} for "
            f"{HARVESTER_BUILD_COST}c"
        )

    def apply_build_probe(
        self, player: PlayerId, count: int = 1,
    ) -> Tuple[bool, str]:
        """Top up the seat's probe stock by ``count`` (v0.9.1).

        Costs ``PROBE_BUILD_COST`` per probe. v1.2 — PARTIAL FILL: a
        batch buys as many probes as the seat can afford rather than
        rejecting the whole order. So ``build_probe ×2`` with budget for
        one buys ONE (not zero). Only a batch the seat can't afford even
        a single unit of is a hard rejection. When the order is trimmed
        to budget we emit an extra ``error``-level ("[orbit] …amended…")
        line so the orbital log paints the amendment red, the same way a
        full rejection shows.
        """
        n = max(1, int(count or 1))
        have = int(self.credits.get(player, 0))
        affordable = have // PROBE_BUILD_COST if PROBE_BUILD_COST > 0 else n
        if affordable <= 0:
            return False, (
                f"{player}: build_probe ×{n} requires {n * PROBE_BUILD_COST}c "
                f"(have {have}c)"
            )
        k = min(n, affordable)
        cost = k * PROBE_BUILD_COST
        self.credits[player] -= cost
        self.probe_stock[player] = int(self.probe_stock.get(player, 0)) + k
        if k < n:
            # Order trimmed to budget — log the amendment as an error so
            # it stands out in red in the per-player orbital log / replay
            # timeline (both key on ``level == "error"``).
            self.log_error(
                f"[orbit] {player}: build_probe amended — requested {n}, "
                f"built {k} ({cost}c); {n - k} dropped, short "
                f"{(n - k) * PROBE_BUILD_COST}c"
            )
        return True, (
            f"{player}: built {k} probe(s) (+{k} stock → "
            f"{self.probe_stock[player]}) for {cost}c"
            + (f" [amended from {n}]" if k < n else "")
        )

    def _ensure_weapons_used_slot(self, player: PlayerId) -> Dict[str, int]:
        """v0.9.5 — guarantee ``weapons_used[player]`` exists with all
        weapon keys present. Mirrors :meth:`_ensure_weapon_stock_slot`
        so seats loaded from a legacy snapshot (pre-v0.9.5) don't
        crash when the counter is bumped on a fire.
        """
        slot = self.weapons_used.setdefault(str(player), {})
        for k in self.weapon_prices():
            slot.setdefault(k, 0)
        return slot

    def _bump_weapons_used(self, player: PlayerId, kind: str) -> int:
        """v0.9.5 — record that ``player`` just successfully fired
        one ``kind`` weapon. Returns the new total. Called from the
        three weapon fire paths (:meth:`apply_emp_launch`,
        :meth:`apply_chaff_flare`) AFTER the
        stockpile drain so a refused fire (empty stockpile, bad
        target) doesn't bump the counter.
        """
        slot = self._ensure_weapons_used_slot(player)
        slot[kind] = int(slot.get(kind, 0)) + 1
        return slot[kind]

    def _ensure_weapon_stock_slot(self, player: PlayerId) -> Dict[str, int]:
        """v0.9.3 — guarantee ``weapon_stock[player]`` exists with the
        canonical ``{emp, mine, chaff}`` shape, then return it.

        Used by every build / consume path so a legacy session whose
        ``weapon_stock`` dict was loaded without one of the seat
        sub-keys (e.g. a corrupted hand-edit) doesn't ``KeyError``
        on the first orbit action.
        """
        slot = self.weapon_stock.setdefault(
            str(player), {"emp": 0, "chaff": 0, "snap": 0},
        )
        for k in self.weapon_prices():
            slot.setdefault(k, 0)
        return slot

    # ── The stamped weapon economy (v1.36, §4.9.8) ──────────────────
    # Three accessors so no call site ever imports the module constants
    # while holding a session. That indirection is the whole migration:
    # an archived season keeps the prices it was played under, and a
    # weapon can be withdrawn by deleting a dict entry.

    def _weapon_counter_keys(
        self, counter: Mapping[str, Mapping[str, int]],
    ) -> List[str]:
        """Every kind to serialise: what this game prices, plus whatever
        is already sitting in the counter (v1.36).

        The second half is the retirement clause. A withdrawn weapon
        leaves the price table but its stock has to keep round-tripping
        or the refund at load has nothing to find.
        """
        keys = list(self.weapon_prices())
        for row in counter.values():
            for kind in (row or {}):
                if kind not in keys:
                    keys.append(str(kind))
        return keys

    def weapon_prices(self) -> Dict[str, int]:
        """Blue price per weapon kind, as of this game's creation."""
        stamped = getattr(self, "weapon_blue_costs", None)
        if isinstance(stamped, Mapping) and stamped:
            return {str(k): int(v) for k, v in stamped.items()}
        return _shipped_weapon_blue_costs()

    def arsenal_cap(self) -> int:
        """Most blue-worth of ordnance a seat may hold in this game."""
        stamped = getattr(self, "weapon_blue_cap", None)
        try:
            cap = int(stamped)
        except (TypeError, ValueError):
            cap = 0
        return cap if cap > 0 else _shipped_arsenal_cap()

    def arsenal_blue(self, player: PlayerId) -> int:
        """Public weaponised-blue reading for ``player`` (§4.9.8)."""
        from sea_of_colours.game.weapons import weaponised_blue

        return weaponised_blue(
            self.weapon_stock.get(str(player)), self.weapon_prices(),
        )

    def _apply_build_weapon(
        self,
        player: PlayerId,
        *,
        kind: str,
        count: int,
        blue_cost_each: int,
        credit_cost_each: int,
        display: str,
    ) -> Tuple[bool, str]:
        """v0.9.3 — shared core for ``build_emp`` / ``build_mine`` /
        ``build_chaff``.

        Atomic: refuses the whole batch when either the blue purity
        pool or the credit balance can't cover ``count`` units. On
        success, debits in one shot (lowest-purity blue parcels first,
        same algorithm as the legacy live-launch path) and bumps the
        seat's ``weapon_stock[kind]`` counter. Matches the BuildProbe
        "no partial fill" guarantee so the watcher's stock readout
        never flickers mid-action.
        """
        if not self.weapons_enabled:
            return False, (
                f"{player}: build_{kind} refused — weapons are disabled in "
                "this game (teaching mode)"
            )
        # v1.36 — a game can only build what its own price table names.
        #
        # This is the retirement seam, and the reason it is worth a
        # branch: withdrawing a weapon is deleting its entry from
        # ``BLUE_COST_BY_KIND``, after which every NEW game refuses it
        # here by name while every game already stamped with it plays
        # on unchanged. Without this the two halves disagree — the
        # arsenal reading iterates the stamp and would value the
        # unpriced weapon at nothing, so a seat could buy an unlimited
        # number of them straight through the cap.
        if kind not in self.weapon_prices():
            return False, (
                f"{player}: build_{kind} refused — this game does not "
                f"stock {display}s. Nothing was spent."
            )
        n = max(1, int(count or 1))
        blue_needed = n * int(blue_cost_each)
        credits_needed = n * int(credit_cost_each)
        # v1.34 — the arsenal ceiling (RULEBOOK §4.9.8). Refused here,
        # above the debit, for the same reason the affordability checks
        # are: a rejected build must cost nothing. Whole-batch, matching
        # the "no partial fill" guarantee below — a batch that would
        # breach the cap buys none of itself, rather than topping up to
        # the line and leaving the seat guessing how many it got.
        held_blue = self.arsenal_blue(player)
        cap = self.arsenal_cap()
        if held_blue + blue_needed > cap:
            return False, (
                f"{player}: build_{kind} ×{n} refused — would hold "
                f"{held_blue + blue_needed} blue of ordnance, over the "
                f"{cap} cap (holding {held_blue}). "
                "No blue or credits were spent."
            )
        avail = self.blue_purity_available(player)
        if avail < blue_needed:
            return False, (
                f"{player}: build_{kind} ×{n} needs {blue_needed} blue purity "
                f"(have {avail})"
            )
        if self.credits.get(player, 0) < credits_needed:
            return False, (
                f"{player}: build_{kind} ×{n} needs {credits_needed}c "
                f"(have {self.credits.get(player, 0)}c)"
            )
        ok, consumed, waste = (True, [], 0)
        if blue_needed > 0:
            ok, consumed, waste = self.debit_blue_purity(player, blue_needed)
            if not ok:
                return False, f"{player}: build_{kind} blue debit failed"
        if credits_needed > 0:
            self.credits[player] = int(self.credits[player]) - credits_needed
        slot = self._ensure_weapon_stock_slot(player)
        slot[kind] = int(slot.get(kind, 0)) + n
        # Build the log line; the consumed-blue list is logged for
        # forensic clarity (matches how live launches were reported
        # under v0.9.0–0.9.2).
        msg = (
            f"{player}: built {n} {display}(s) "
            f"(+{n} {kind} stock → {slot[kind]}); "
            f"spent {blue_needed} blue, {credits_needed}c"
        )
        if waste:
            msg += f" (blue waste {waste})"
        return True, msg

    def apply_build_emp(
        self, player: PlayerId, count: int = 1,
    ) -> Tuple[bool, str]:
        """v0.9.3 — Build ``count`` EMP warheads into the seat's stockpile.

        Cost: ``count × (EMP_COST_BLUE_PURITY + EMP_COST_CREDITS)``.
        See :meth:`_apply_build_weapon` for the atomic-debit rules.
        """
        from sea_of_colours.game.weapons import (
            EMP_COST_BLUE_PURITY,
            EMP_COST_CREDITS,
        )
        return self._apply_build_weapon(
            player,
            kind="emp",
            count=count,
            blue_cost_each=EMP_COST_BLUE_PURITY,
            credit_cost_each=EMP_COST_CREDITS,
            display="EMP warhead",
        )

    def apply_build_chaff(
        self, player: PlayerId, count: int = 1,
    ) -> Tuple[bool, str]:
        """v0.9.3 — Build ``count`` orbital chaff flares into the stockpile."""
        from sea_of_colours.game.weapons import (
            CHAFF_COST_BLUE_PURITY,
            CHAFF_COST_CREDITS,
        )
        return self._apply_build_weapon(
            player,
            kind="chaff",
            count=count,
            blue_cost_each=CHAFF_COST_BLUE_PURITY,
            credit_cost_each=CHAFF_COST_CREDITS,
            display="chaff flare",
        )

    def apply_build_snap(
        self, player: PlayerId, count: int = 1,
    ) -> Tuple[bool, str]:
        """v1.36 — Build ``count`` SNAP rounds into the stockpile (§4.9.4).

        Prices come off the game's own stamp rather than the module, so
        a season keeps buying at the numbers it started with — see
        :meth:`weapon_prices`. Credits are not stamped (they are not
        what the arsenal cap is denominated in) and come live.
        """
        from sea_of_colours.game.weapons import (
            SNAP_COST_BLUE_PURITY,
            SNAP_COST_CREDITS,
        )
        return self._apply_build_weapon(
            player,
            kind="snap",
            count=count,
            blue_cost_each=self.weapon_prices().get(
                "snap", SNAP_COST_BLUE_PURITY,
            ),
            credit_cost_each=SNAP_COST_CREDITS,
            display="SNAP round",
        )

    def apply_repair(
        self, player: PlayerId, unit: str,
    ) -> Tuple[bool, str]:
        """Repair a damaged harvester. Costs :data:`REPAIR_COST`."""
        if self.credits.get(player, 0) < REPAIR_COST:
            return False, (
                f"{player}: repair requires {REPAIR_COST}c (have "
                f"{self.credits.get(player, 0)}c)"
            )
        ent = self.entities.get(unit)
        if ent is None:
            return False, f"{player}: repair target '{unit}' not found"
        if ent.owner != player:
            return False, f"{player}: repair target '{unit}' is not owned"
        if ent.entity_type != "harvester":
            return False, f"{player}: repair target '{unit}' is not a harvester"
        if not bool(getattr(ent, "damaged", False)):
            return False, f"{player}: repair target '{unit}' is not damaged"
        ent.damaged = False
        self.credits[player] -= REPAIR_COST
        # v0.9.5 — stamp the repair onto the lifecycle ledger so the
        # HUD can render "repaired N×, last on day M" in the vault +
        # orders chip tooltip. The record is materialised lazily by
        # ``_ensure_asset_record`` which ``_assets_by_status`` already
        # calls; we do the same here so a repair landed via direct
        # API (no view refresh yet) still updates the counter.
        rec = self._ensure_asset_record(ent)
        rec.repair_count = int(rec.repair_count or 0) + 1
        rec.last_repaired_day = int(self.day)
        return True, f"{player}: repaired harvester {unit} for {REPAIR_COST}c"

    @staticmethod
    def _tier_for_purity(purity: int) -> str:
        # Mirror the legends used elsewhere (RULEBOOK §2.2):
        #   trace [1..50], vein [51..150], mass [151..254], pure [255].
        p = max(0, min(255, int(purity)))
        if p <= 0:
            return "empty"
        if p <= 50:
            return "trace"
        if p <= 150:
            return "vein"
        if p <= 254:
            return "mass"
        return "pure"

    def red_tier_counts(self, player: PlayerId) -> Dict[str, int]:
        """Return ``{trace, vein, mass, pure}`` parcel counts for RED hoard.

        Used by the Orbit panel + agent view to render the
        "TRACE 5 · VEIN 2 · MASS 0" pill. v1.13 — this used to tell a
        seat whether a refine button was worth clicking; refining is
        gone, so it now previews what the vault is worth when it ships
        automatically at settlement (RULEBOOK §4.4).
        """
        out: Dict[str, int] = {"trace": 0, "vein": 0, "mass": 0, "pure": 0}
        for p in self.hoard_squares.get(player, []) or []:
            try:
                if int(p.get("tile_at_harvest", -1)) != int(Tile.RED):
                    continue
            except (TypeError, ValueError):
                continue
            tier = self._tier_for_purity(self._parcel_purity(p))
            if tier in out:
                out[tier] += 1
        return out

    def blue_tier_counts(self, player: PlayerId) -> Dict[str, int]:
        """v0.9.5 — Return BLUE parcel counts by purity tier.

        The orbit panel needs a parallel readout for the weapons
        economy (BLUE pays for EMP / MINE / CHAFF). The tier names
        match the in-game shorthand from the RED ladder so the
        panel can render "TRACE 2 · VEIN 1 · MASS 0 · PURE 1"
        without renaming. The user-facing labels (shallow / mid /
        sink / deep) live in the frontend; this helper stays
        engine-side and keys by the canonical tier strings.
        """
        out: Dict[str, int] = {"trace": 0, "vein": 0, "mass": 0, "pure": 0}
        for p in self.hoard_squares.get(player, []) or []:
            try:
                if int(p.get("tile_at_harvest", -1)) != int(Tile.BLUE):
                    continue
            except (TypeError, ValueError):
                continue
            tier = self._tier_for_purity(self._parcel_purity(p))
            if tier in out:
                out[tier] += 1
        return out


    # --- Serialization ----------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly snapshot (for future Snowflake persistence)."""

        ents: dict[str, dict[str, Any]] = {}
        for eid, e in self.entities.items():
            ents[eid] = {
                "id": e.id,
                "type": e.entity_type,
                "owner": e.owner,
                "x": e.x,
                "y": e.y,
                "carrying_red": e.carrying_red,
                "orbital_cargo_red": e.orbital_cargo_red,
                "cargo_squares": [dict(c) for c in e.cargo_squares],
                "lost_last_night": e.lost_last_night,
                "damaged": bool(getattr(e, "damaged", False)),
            }

        grid_packed = [
            [[int(cell.tile), int(cell.purity)] for cell in row]
            for row in self.grid
        ]

        return {
            "session_id": self.session_id,
            "season_name": self.season_name,
            "season_day_cap": int(self.season_day_cap),
            "width": self.width,
            "height": self.height,
            "seed": self.seed,
            "day": self.day,
            "phase": self.phase.value,
            "grid": grid_packed,
            "entities": ents,
            "probe_seq": dict(self.probe_seq),
            # v0.9.6 — N-seat round-trip. Every per-seat dict iterates
            # over :attr:`players` so a 3/4-seat session serialises
            # cleanly. Snapshot the seat list so a future load can
            # reconstruct the same shape without falling back to the
            # legacy 2-seat default.
            "players": list(self.players),
            "agents": dict(self.agents),
            "visibility_mode": str(self.visibility_mode),
            # v1.32 — teaching-mode rule switches. Persisted rather than
            # re-derived, because ``tutorial`` alone must never be what
            # decides them (see the field docs).
            "weapons_enabled": bool(self.weapons_enabled),
            "signs_enabled": bool(self.signs_enabled),
            # v1.36 — the prices this season was played at, travelling
            # with it. See the field docs: a retune must not reach
            # backwards into a game already on disk.
            "weapon_blue_costs": dict(self.weapon_prices()),
            "weapon_blue_cap": int(self.arsenal_cap()),
            "tutorial": str(self.tutorial or ""),
            "pending_policies": {
                p: (moves_to_wire(self.pending_policies[p] or [])
                    if self.pending_policies.get(p) is not None else None)
                for p in self.players
            },
            "log": [dict(e) for e in self.log],
            "errors": {p: list(self.errors.get(p, [])) for p in self.players},
            "harvest_log": {p: [dict(r) for r in self.harvest_log.get(p, [])] for p in self.players},
            "hoard_squares": {p: [dict(r) for r in self.hoard_squares.get(p, [])] for p in self.players},
            "shipped_squares": {
                p: [dict(r) for r in self.shipped_squares.get(p, [])]
                for p in self.players
            },
            # v0.8.0 Orbit phase economy. Stored under json_state for
            # now; the persistence layer reads/writes from here and does
            # NOT shadow into a dedicated SOC_* table until a future
            # iteration needs analytics columns.
            "credits": {p: int(self.credits.get(p, 0)) for p in self.players},
            "blue_bank": {p: int(self.blue_bank.get(p, 0)) for p in self.players},
            # v0.9.x — static blue-sign overlay (RULEBOOK §4.6).
            "blue_sign": [dict(r) for r in (self.blue_sign or [])],
            # v1.x — discovery-triggered redsign beacons (RULEBOOK §4.11).
            "redsign": [dict(r) for r in (self.redsign or [])],
            "redsign_seen": sorted(self.redsign_seen or set()),
            "probe_stock": {
                p: int(self.probe_stock.get(p, PROBE_INITIAL_STOCK))
                for p in self.players
            },
            # v0.9.9 — cumulative tier-multiplier shipped score per seat
            # (HUD scoreboard). Float to preserve fractional multipliers
            # before final ``int`` rounding at render time.
            "cumulative_shipped_score": {
                p: float(self.cumulative_shipped_score.get(p, 0.0) or 0.0)
                for p in self.players
            },
            # v1.0 — end-of-game support: Latin names for bot/agent
            # seats, per-seat running tallies, and the final-orbit flag.
            "player_names": dict(self.player_names),
            # v0.9.18 — player identity profiles (display names, tags, colors).
            "player_profiles": {
                p: dict(self.player_profiles.get(p, {}))
                for p in self.players
            },
            "season_stats": {
                p: {
                    k: float(v)
                    for k, v in (self.season_stats.get(p) or {}).items()
                }
                for p in self.players
            },
            "final_orbit": bool(self.final_orbit),
            # v0.9.3 — built-weapons inventory. Seat-keyed map of
            # {emp, mine, chaff} integer counters. Defaults to all-zero
            # for legacy sessions that pre-date the build-first weapons
            # flow (from_dict re-fills any missing slot).
            # v1.36 — keyed by what this game actually stocks, plus any
            # kind already sitting in the counter. The second half is
            # what lets a RETIRED weapon survive a save: the stock has
            # to still be there at load for the refund to find it.
            "weapon_stock": {
                p: {
                    k: int(self.weapon_stock.get(p, {}).get(k, 0) or 0)
                    for k in self._weapon_counter_keys(self.weapon_stock)
                }
                for p in self.players
            },
            # v0.9.5 — lifetime weapons-used counter (one-way bump
            # on every successful fire). Persists across phases so
            # the VAULT "USED" bay survives a server restart.
            "weapons_used": {
                p: {
                    k: int(self.weapons_used.get(p, {}).get(k, 0) or 0)
                    for k in self._weapon_counter_keys(self.weapons_used)
                }
                for p in self.players
            },
            # v0.8.1 — Serialise OrbitAction instances back to their
            # wire (dict) form so they round-trip cleanly through JSON
            # storage. Previously these were dumped as opaque Python
            # objects which JSON would stringify, then the resolver
            # crashed with ``AttributeError: 'str' object has no
            # attribute 'tag'`` on re-hydrate.
            "pending_orbit_actions": {
                p: (
                    _orbit_actions_to_wire(
                        self.pending_orbit_actions.get(p) or []
                    )
                    if self.pending_orbit_actions.get(p) is not None
                    else None
                )
                for p in self.players
            },
            "catapult_history": [dict(r) for r in self.catapult_history],
            "orbital_activity_by_day": {
                str(d): {
                    seat: dict(tally) for seat, tally in (by_seat or {}).items()
                }
                for d, by_seat in (self.orbital_activity_by_day or {}).items()
            },
            # v1.6 — season attacker→victim combat matrices + wasted-turn tally.
            "combat_attrib": {
                stat: {
                    atk: dict(by_vic) for atk, by_vic in (by_atk or {}).items()
                }
                for stat, by_atk in (self.combat_attrib or {}).items()
            },
            "moves_cancelled": {
                str(s): int(n) for s, n in (self.moves_cancelled or {}).items()
            },
            "emp_harv_seen": {
                str(k): True for k in (self.emp_harv_seen or {})
            },
            "orbital_events_by_day": {
                str(d): {
                    seat: [dict(ev) for ev in (evs or [])]
                    for seat, evs in (by_seat or {}).items()
                }
                for d, by_seat in (self.orbital_events_by_day or {}).items()
            },
            "station_obs_by_day": {
                str(d): {
                    phase: {
                        seat: dict(obs) for seat, obs in (by_seat or {}).items()
                    }
                    for phase, by_seat in (by_phase or {}).items()
                }
                for d, by_phase in (self.station_obs_by_day or {}).items()
            },
            "_orbit_credits_awarded_day": int(
                getattr(self, "_orbit_credits_awarded_day", 0) or 0
            ),
            "_tutorial_blue_granted_day": int(
                getattr(self, "_tutorial_blue_granted_day", 0) or 0
            ),
            "asset_records": {
                k: r.to_dict() for k, r in self.asset_records.items()
            },
            "square_uid_seq": self.square_uid_seq,
            "track_paths": {
                p: dict(self.track_paths.get(p, {})) for p in self.players
            },
            "track_harvests": {p: sorted(self.track_harvests.get(p, set())) for p in self.players},
            "memory_tiles": {
                p: {k: dict(v) for k, v in self.memory_tiles.get(p, {}).items()}
                for p in self.players
            },
            "probe_intel": {
                p: {k: dict(v) for k, v in self.probe_intel.get(p, {}).items()}
                for p in self.players
            },
            "last_night_replay": [dict(f) for f in self.last_night_replay],
            "collision_marks": {
                k: {"day": int(v.get("day", 0)),
                    "owners": list(v.get("owners") or [])}
                for k, v in self.collision_marks.items()
            },
            # v1.8 — canonical combat feed + decaying EMP scars.
            "combat_events_by_day": {
                str(d): [dict(e) for e in (evs or [])]
                for d, evs in (self.combat_events_by_day or {}).items()
            },
            "emp_marks": {
                k: {
                    "owners": list(v.get("owners") or []),
                    "hours": list(v.get("hours") or []),
                    "day": int(v.get("day", 0)),
                }
                for k, v in (self.emp_marks or {}).items()
            },
            "destroyed_harvester_markers": {
                k: {
                    "owner": str(v.get("owner", "")),
                    "harvester_id": str(v.get("harvester_id", "")),
                    "day": int(v.get("day", 0)),
                }
                for k, v in self.destroyed_harvester_markers.items()
            },
            # v0.9 — interdiction state (RULEBOOK §5).
            "emp_clouds": [dict(c) for c in self.emp_clouds],
            "snap_clouds": [dict(c) for c in self.snap_clouds],
            "mines": {k: dict(v) for k, v in self.mines.items()},
            # SquareLedger keeps natural + synthetic-green identities with
            # full provenance. Without it persisted, every Snowflake-backed
            # hydrate would reset ``self.ledger`` to None and downstream
            # harvest paths would fall back to short ``sq-XXXX-NNNN`` ids
            # with lineage='natural' — losing synthetic-green provenance
            # across days. None when a legacy session predates the ledger.
            "ledger": (self.ledger.to_dict() if self.ledger is not None else None),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GameSession:
        grid: Grid = [
            [Cell(Tile(int(c[0])), int(c[1])) for c in row]
            for row in data["grid"]
        ]
        # v0.9.6 — determine the seat list FIRST so every per-seat
        # dict can be sized correctly. Legacy 2-seat snapshots (no
        # ``players`` key) fall back to ("p1","p2") so they keep
        # loading without migration. Clamp to MAX_SEATS to defend
        # against a corrupted payload.
        raw_players = data.get("players")
        if isinstance(raw_players, (list, tuple)) and raw_players:
            seen: List[str] = []
            for p in raw_players:
                pid = str(p)
                if pid and pid not in seen:
                    seen.append(pid)
                if len(seen) >= MAX_SEATS:
                    break
            seat_ids: Tuple[str, ...] = tuple(seen) if seen else ("p1", "p2")
        else:
            seat_ids = ("p1", "p2")
        # Per-seat agent map. Legacy snapshots default every seat to
        # ``human`` so the API surface is unchanged.
        raw_agents = data.get("agents") or {}
        agent_map: Dict[str, str] = {}
        if isinstance(raw_agents, Mapping):
            for k, v in raw_agents.items():
                if str(k) in seat_ids:
                    agent_map[str(k)] = str(v) if v else "human"
        for s in seat_ids:
            agent_map.setdefault(s, "human")
        visibility = (
            "open"
            if str(data.get("visibility_mode", "hidden")).lower() == "open"
            else "hidden"
        )
        # probe_seq legacy default keeps the 2-seat shape; __post_init__
        # backfills any extra seats with 0.
        probe_seq_raw = data.get("probe_seq")
        if isinstance(probe_seq_raw, Mapping):
            probe_seq_init = {str(k): int(v or 0) for k, v in probe_seq_raw.items()}
        else:
            probe_seq_init = {"p1": 0, "p2": 0}
        sess = cls(
            width=int(data["width"]),
            height=int(data["height"]),
            seed=int(data["seed"]),
            grid=grid,
            day=int(data.get("day", 1)),
            phase=Phase(str(data.get("phase", Phase.PLANNING.value))),
            players=seat_ids,
            agents=agent_map,
            visibility_mode=visibility,
            # v1.32 — pre-1.32 saves have neither key and must load as full
            # games, so both default to on.
            weapons_enabled=bool(data.get("weapons_enabled", True)),
            signs_enabled=bool(data.get("signs_enabled", True)),
            # v1.36 — a save with no stamp predates the retune, so it
            # loads at the LEGACY prices, not today's. Falling back to
            # the live constants is the one wrong answer here: it would
            # reprice a finished season's arsenal to numbers nobody in
            # it ever paid, and do it plausibly enough to be believed.
            weapon_blue_costs=_weapon_costs_from_save(data),
            weapon_blue_cap=_arsenal_cap_from_save(data),
            tutorial=str(data.get("tutorial") or ""),
            probe_seq=probe_seq_init,
            log=_normalize_log_entries(data.get("log")),
            errors={p: list((data.get("errors") or {}).get(p, [])) for p in seat_ids},
            harvest_log={
                p: [dict(r) for r in (data.get("harvest_log") or {}).get(p, [])]
                for p in seat_ids
            },
            hoard_squares={
                p: [dict(r) for r in (data.get("hoard_squares") or {}).get(p, [])]
                for p in seat_ids
            },
            shipped_squares={
                p: [dict(r) for r in (data.get("shipped_squares") or {}).get(p, [])]
                for p in seat_ids
            },
            credits={
                p: int((data.get("credits") or {}).get(p, 0))
                for p in seat_ids
            },
            blue_bank={
                p: int((data.get("blue_bank") or {}).get(p, 0))
                for p in seat_ids
            },
            blue_sign=[dict(r) for r in (data.get("blue_sign") or [])],
            redsign=[dict(r) for r in (data.get("redsign") or [])],
            redsign_seen=set(str(k) for k in (data.get("redsign_seen") or [])),
            probe_stock={
                p: int((data.get("probe_stock") or {}).get(
                    p, PROBE_INITIAL_STOCK,
                ))
                for p in seat_ids
            },
            # v0.9.3 — re-hydrate the built-weapons stockpile. Missing
            # keys (legacy sessions) collapse to 0 so the new build-
            # first flow is invisible to old saves until the first
            # Build*Action lands.
            weapon_stock=_weapon_counters_from_save(
                data.get("weapon_stock"), seat_ids, _weapon_costs_from_save(data),
            ),
            # v0.9.5 — lifetime weapons-used counter. Defaults to 0
            # for any seat/key absent from the snapshot so a
            # pre-v0.9.5 save round-trips into a clean state instead
            # of crashing on the dict lookup.
            weapons_used=_weapon_counters_from_save(
                data.get("weapons_used"), seat_ids, _weapon_costs_from_save(data),
            ),
            # v0.8.1 — re-parse the wire dicts back into OrbitAction
            # instances. The resolver expects typed actions (it reads
            # ``.tag`` etc.); stringy / raw-dict actions blow up.
            pending_orbit_actions={
                p: _orbit_actions_from_wire(
                    (data.get("pending_orbit_actions") or {}).get(p)
                )
                for p in seat_ids
            },
            catapult_history=[
                dict(r) for r in (data.get("catapult_history") or [])
            ],
            orbital_activity_by_day={
                str(d): {
                    seat: dict(tally) for seat, tally in (by_seat or {}).items()
                }
                for d, by_seat in (data.get("orbital_activity_by_day") or {}).items()
            },
            combat_attrib={
                stat: {
                    atk: {v: int(n) for v, n in (by_vic or {}).items()}
                    for atk, by_vic in (by_atk or {}).items()
                }
                for stat, by_atk in (data.get("combat_attrib") or {}).items()
            },
            moves_cancelled={
                str(s): int(n)
                for s, n in (data.get("moves_cancelled") or {}).items()
            },
            emp_harv_seen={
                str(k): True for k in (data.get("emp_harv_seen") or {})
            },
            orbital_events_by_day={
                str(d): {
                    seat: [dict(ev) for ev in (evs or [])]
                    for seat, evs in (by_seat or {}).items()
                }
                for d, by_seat in (data.get("orbital_events_by_day") or {}).items()
            },
            station_obs_by_day={
                str(d): {
                    phase: {
                        seat: dict(obs) for seat, obs in (by_seat or {}).items()
                    }
                    for phase, by_seat in (by_phase or {}).items()
                }
                for d, by_phase in (data.get("station_obs_by_day") or {}).items()
            },
            # v0.9.9 — re-hydrate the cumulative shipped score. Missing
            # keys (legacy sessions before this counter existed) fall
            # back to a recompute from ``shipped_squares`` so the HUD
            # scoreboard shows the right number after the first save→
            # load cycle without a manual migration step.
            cumulative_shipped_score=_hydrate_cumulative_shipped_score(
                data.get("cumulative_shipped_score"),
                data.get("shipped_squares") or {},
                seat_ids,
            ),
            player_names={
                str(k): str(v)
                for k, v in (data.get("player_names") or {}).items()
            },
            player_profiles={
                str(k): {
                    "display_name": str(v.get("display_name", "")),
                    "tag": str(v.get("tag", ""))[:3].upper(),
                    "color": str(v.get("color", "")).upper(),
                }
                for k, v in (data.get("player_profiles") or {}).items()
            },
            season_stats={
                p: {
                    k: float(v)
                    for k, v in ((data.get("season_stats") or {}).get(p) or {}).items()
                }
                for p in seat_ids
            },
            final_orbit=bool(data.get("final_orbit", False)),
            asset_records={
                str(k): AssetRecord.from_dict(v)
                for k, v in (data.get("asset_records") or {}).items()
            },
            square_uid_seq=int(data.get("square_uid_seq", 0)),
            track_paths={
                p: _load_path_counts((data.get("track_paths") or {}).get(p))
                for p in seat_ids
            },
            track_harvests={
                p: set((data.get("track_harvests") or {}).get(p, []))
                for p in seat_ids
            },
            memory_tiles={
                p: {k: dict(v) for k, v in (data.get("memory_tiles") or {}).get(p, {}).items()}
                for p in seat_ids
            },
            probe_intel={
                p: {k: dict(v) for k, v in (data.get("probe_intel") or {}).get(p, {}).items()}
                for p in seat_ids
            },
            last_night_replay=[dict(f) for f in (data.get("last_night_replay") or [])],
            collision_marks={
                str(k): {
                    "day": int(v.get("day", 0)),
                    "owners": [str(o) for o in (v.get("owners") or [])],
                }
                for k, v in (data.get("collision_marks") or {}).items()
            },
            # v1.8 — canonical combat feed + decaying EMP scars.
            combat_events_by_day={
                str(d): [dict(e) for e in (evs or [])]
                for d, evs in (data.get("combat_events_by_day") or {}).items()
            },
            emp_marks={
                str(k): {
                    "owners": [str(o) for o in (v.get("owners") or [])],
                    "hours": [int(h) for h in (v.get("hours") or [])],
                    "day": int(v.get("day", 0)),
                }
                for k, v in (data.get("emp_marks") or {}).items()
            },
            destroyed_harvester_markers={
                str(k): {
                    "owner": str(v.get("owner", "")),
                    "harvester_id": str(v.get("harvester_id", "")),
                    "day": int(v.get("day", 0)),
                }
                for k, v in (data.get("destroyed_harvester_markers") or {}).items()
            },
            # v0.9 — round-trip the weapons state. Legacy v0.8 payloads
            # won't have these fields; ``default_factory`` plus an empty
            # ``.get()`` fall through to fresh containers.
            emp_clouds=[dict(c) for c in (data.get("emp_clouds") or [])],
            snap_clouds=[dict(c) for c in (data.get("snap_clouds") or [])],
            # v1.31 — caltrops are NOT rehydrated. See the migration
            # below: a weapon that no longer exists must not still be
            # killing harvesters on a board mid-season.
            mines={},
            session_id=str(data.get("session_id") or uuid.uuid4().hex),
            season_name=(
                str(data["season_name"])
                if data.get("season_name") is not None else None
            ),
            season_day_cap=int(data.get("season_day_cap") or SEASON_DAY_CAP),
        )
        ents: Dict[str, Entity] = {}
        for eid, raw in (data.get("entities") or {}).items():
            ents[eid] = Entity(
                id=str(raw["id"]),
                entity_type=str(raw["type"]),
                owner=str(raw["owner"]),
                x=raw.get("x"),
                y=raw.get("y"),
                carrying_red=bool(raw.get("carrying_red", False)),
                orbital_cargo_red=bool(raw.get("orbital_cargo_red", False)),
                cargo_squares=[dict(c) for c in raw.get("cargo_squares", [])],
                lost_last_night=bool(raw.get("lost_last_night", False)),
                damaged=bool(raw.get("damaged", False)),
            )
        sess.entities = ents

        # v1.31 — MINE RETIREMENT MIGRATION.
        #
        # Two kinds of orphan can arrive from a pre-1.31 save, and both
        # would otherwise be silent. Armed caltrops are dropped above
        # (a retired weapon must not keep damaging steppers on a board
        # someone is still playing), and unspent stock is refunded here
        # rather than confiscated — the seat paid blue purity for it and
        # nothing they can now do will ever spend it.
        #
        # Refund goes back as blue purity at the price paid. Credits are
        # deliberately NOT refunded: the credit half was the build fee,
        # and the vault is where a player feels the loss.
        _stale_mines = len(data.get("mines") or {})
        _refunded = 0
        for _p in seat_ids:
            _held = int(
                ((data.get("weapon_stock") or {}).get(_p) or {}).get("mine", 0) or 0
            )
            if _held > 0:
                sess.blue_bank[_p] = int(
                    sess.blue_bank.get(_p, 0)
                ) + _held * _MINE_REFUND_BLUE_EACH
                _refunded += _held
        if _stale_mines or _refunded:
            sess.log_info(
                f"[mineRetired] v1.31 — cleared {_stale_mines} armed "
                f"caltrop(s) from the board and refunded {_refunded} "
                f"unspent mine(s) at {_MINE_REFUND_BLUE_EACH} blue each"
            )

        # Restore the square-identity ledger from the persisted blob so
        # synthetic-green provenance survives the Snowflake round-trip.
        # Legacy sessions (pre-v0.6.0) won't have a ``ledger`` field —
        # rebuild a natural-only ledger from the grid as a fallback so
        # downstream code still gets stable identities.
        ledger_blob = data.get("ledger")
        if isinstance(ledger_blob, Mapping):
            sess.ledger = SquareLedger.from_dict(ledger_blob)
        elif sess.ledger is None:
            sess.ledger = SquareLedger.from_grid(int(data["seed"]), sess.grid)
        if sess.ledger is not None:
            LEDGER_STORE.save(sess.session_id, sess.ledger)
        # v0.9.x — backfill the static blue-sign overlay for legacy
        # saves that predate it (recomputed from the now-restored
        # generation-time ledger, so it's identical to a fresh birth).
        if not sess.blue_sign and sess.ledger is not None:
            try:
                sess.blue_sign = sess._compute_blue_sign()
            except Exception:  # pragma: no cover — never block hydrate
                sess.blue_sign = []
        pp = data.get("pending_policies") or {}
        sess.pending_policies = {p: None for p in sess.players}
        for p in sess.players:
            entries = pp.get(p)
            if entries is None:
                continue
            moves, _ = parse_moves(entries)
            sess.pending_policies[p] = moves
        # Re-publish the asset ledger so the in-memory (or Snowflake)
        # store stays consistent with the freshly loaded session state.
        sess._persist_asset_ledger()
        # Restore the per-day orbit-credit gate so a round-tripped
        # session doesn't double-award credits when the resolver runs.
        awarded_day = data.get("_orbit_credits_awarded_day")
        if awarded_day is not None:
            try:
                sess._orbit_credits_awarded_day = int(awarded_day)  # type: ignore[attr-defined]
            except (TypeError, ValueError):
                sess._orbit_credits_awarded_day = 0  # type: ignore[attr-defined]
        # v1.34 — same gate for the teaching blue subsidy, so a reload
        # mid-tutorial cannot hand out a second one.
        granted_day = data.get("_tutorial_blue_granted_day")
        if granted_day is not None:
            try:
                sess._tutorial_blue_granted_day = int(granted_day)  # type: ignore[attr-defined]
            except (TypeError, ValueError):
                sess._tutorial_blue_granted_day = 0  # type: ignore[attr-defined]
        return sess

    # --- Primitive move applicators (used by NightSimulator) --------

    def harvester_blocks_cell(self, x: int, y: int, exclude: Optional[str] = None) -> bool:
        """Return True iff a *healthy* harvester occupies ``(x,y)``.

        Damaged harvesters (RULEBOOK §3.6.1, v0.7.3) are wreckage —
        they do **not** block movement. Any move that targets a cell
        containing only damaged harvesters proceeds normally (the
        arriving harvester pulls up next to the wreck). Healthy
        harvesters DO still block, but the blocking semantics changed
        in v0.7.3: drop / step into a cell with a healthy harvester
        no longer fails outright — the engine routes through a
        mutual-damage collision instead (see
        :meth:`_undamaged_harvesters_at` and the collision branches
        in :meth:`try_drop_unit` / :meth:`try_step_unit`).
        """
        for e in self.entities.values():
            if e.entity_type != "harvester":
                continue
            if e.id == exclude:
                continue
            if bool(getattr(e, "damaged", False)):
                continue
            if e.x == x and e.y == y:
                return True
        return False

    def _undamaged_harvesters_at(
        self,
        x: int,
        y: int,
        exclude: Optional[str] = None,
        *,
        departing: Optional[AbstractSet[str]] = None,
    ) -> List[Entity]:
        """List every healthy (non-damaged) harvester at ``(x, y)``.

        Used by drop/step to decide whether the incoming move resolves
        as a normal placement or a mutual-damage collision
        (§3.6 v0.7.3). The order is insertion order of
        :attr:`entities` so the resulting message is deterministic.

        v1.48 — ``departing`` names harvesters being lifted off the
        surface during THIS hour. §3.17's governing sentence collides
        two harvesters *arriving* on one cell; a harvester on its way to
        orbit is not arriving, so it cannot be rammed. Without this the
        answer depended on whether the engine's seat loop happened to
        reach the lifter or the lander first — see
        docs/OUTSTANDING_ISSUES.md #56.
        """
        leaving = departing or frozenset()
        out: List[Entity] = []
        for e in self.entities.values():
            if e.entity_type != "harvester":
                continue
            if e.id == exclude:
                continue
            if e.id in leaving:
                continue
            if bool(getattr(e, "damaged", False)):
                continue
            if e.x == x and e.y == y:
                out.append(e)
        return out

    def _damage_harvester(
        self, h: Entity, by: Optional[str] = None,
        *, category: str = "harv_damaged",
    ) -> int:
        """Flip a harvester to ``damaged`` and spill its cargo.

        Returns the number of parcels lost so the caller can fold
        them into the collision caption ("3 cargo squares lost").
        The cargo squares are dropped on the floor — they do not bank
        and they do not regenerate as surface tiles. The harvester
        stays on the surface (no orbital recall); only a successful
        pickup (§3.6.1) clears the damage flag and returns it to
        orbit.

        ``by`` is the house that caused this damage (the OTHER party in
        a collision) for the v1.6 kill-feed; called once per damaged
        unit per collision so counts don't double up.

        ``category`` names the kill-feed bucket, and defaults to the
        collision one because collisions are where this started (v1.36).
        A weapon that maims must pass its own: ``harv_damaged`` is
        documented as harvester-on-harvester damage, so filing a shot
        hull there would tell a reader the two houses rammed each other.
        """
        spilled = len(h.cargo_squares)
        h.cargo_squares = []
        h.carrying_red = False
        h.damaged = True
        # v1.6 kill-feed: credit the house that did it.
        if by:
            self._attrib(category, str(by), str(h.owner))
        return spilled

    def _record_collision(
        self,
        x: int,
        y: int,
        owners: List[str],
        *,
        event_type: str = "step_into",
        harvesters: Optional[List[str]] = None,
    ) -> None:
        """Stamp a 1-day collision mark at ``(x,y)`` (§3.6 v0.7.3).

        Also pushes a structured event into
        :attr:`pending_collision_events` so the simulator can attach
        it to the matching replay frame for frontend animation.
        """
        key = f"{x}:{y}"
        merged_owners: List[str] = []
        existing = self.collision_marks.get(key)
        if existing and int(existing.get("day", -1)) == int(self.day):
            for o in list(existing.get("owners") or []):
                if o not in merged_owners:
                    merged_owners.append(str(o))
        for o in owners:
            if o and o not in merged_owners:
                merged_owners.append(str(o))
        self.collision_marks[key] = {
            "day": int(self.day),
            "owners": merged_owners,
        }
        self.pending_collision_events.append({
            "type": str(event_type),
            "at": [int(x), int(y)],
            "owners": list(merged_owners),
            "harvesters": list(harvesters or []),
            "day": int(self.day),
        })

    def try_swap_collision(
        self,
        owner_a: PlayerId,
        harvester_a: str,
        target_a: Tuple[int, int],
        owner_b: PlayerId,
        harvester_b: str,
        target_b: Tuple[int, int],
    ) -> Tuple[bool, str]:
        """Resolve a pass-through swap as mutual damage (§3.6 v0.7.3).

        Caller has already verified that the two pending steps form a
        swap pattern (A's current pos == B's target and B's current
        pos == A's target). Both harvesters teleport to their
        respective destinations (one tile of motion each, the actual
        crossing happens conceptually mid-tile), both become
        damaged, both spill all cargo, and a collision mark is
        recorded on each destination cell. Returns
        ``(ok, single_caption)`` — the simulator wraps the result
        into a single replay frame with ``tag = "collision_swap"``.
        """
        ha = self.entities.get(harvester_a)
        hb = self.entities.get(harvester_b)
        if not (ha and hb and ha.entity_type == "harvester" and hb.entity_type == "harvester"):
            return False, "swap: bad harvesters"
        if ha.owner != owner_a or hb.owner != owner_b:
            return False, "swap: owner mismatch"
        ha.x, ha.y = int(ha.x), int(ha.y)  # stay — no position change
        hb.x, hb.y = int(hb.x), int(hb.y)  # stay — no position change
        spilled_a = self._damage_harvester(ha, by=hb.owner)
        spilled_b = self._damage_harvester(hb, by=ha.owner)
        # Mark both cells (target_a = B's current pos; target_b = A's current pos)
        self._record_collision(
            target_a[0], target_a[1], [ha.owner, hb.owner],
            event_type="swap", harvesters=[ha.id, hb.id],
        )
        self._record_collision(
            target_b[0], target_b[1], [ha.owner, hb.owner],
            event_type="swap", harvesters=[ha.id, hb.id],
        )
        owners_label = "+".join(sorted({ha.owner, hb.owner}))
        return (
            True,
            (
                f"COLLISION BLOCKED ({owners_label}) — "
                f"{ha.id}↔{hb.id} head-on; both damaged at "
                f"({target_b[0]},{target_b[1]}) ↔ "
                f"({target_a[0]},{target_a[1]}); "
                f"neither moves; "
                f"{spilled_a + spilled_b} cargo square(s) lost"
            ),
        )

    def _prune_collision_marks(self) -> None:
        """Drop collision marks older than 1 game day.

        Called as part of the dawn/night pipeline so the planet's
        surface "scars" decay on schedule — yesterday's collision is
        still visible (gap of 1), but two-days-old marks fade out
        entirely. Mirrors the ``fresh_visits`` decay in
        :meth:`_trail_summary`.
        """
        if not self.collision_marks:
            return
        cutoff = int(self.day) - 1
        stale = [k for k, v in self.collision_marks.items()
                 if int(v.get("day", -1)) < cutoff]
        for k in stale:
            self.collision_marks.pop(k, None)

    # ── v1.8 — canonical combat-event feed + EMP scar ────────────────

    def _record_combat_event(self, event: Dict[str, Any]) -> None:
        """Append ``event`` to the current game day's combat feed."""
        self.combat_events_by_day.setdefault(str(int(self.day)), []).append(event)

    def _combat_events_today(self) -> List[Dict[str, Any]]:
        return self.combat_events_by_day.setdefault(str(int(self.day)), [])

    def record_emp_artifacts(
        self,
        owner: str,
        targets: List[Tuple[int, int]],
        radius: int,
        cells: Set[Tuple[int, int]],
        *,
        hour: int,
        cloud_hours: int,
    ) -> None:
        """Record a PUBLIC EMP salvo event + stamp the decaying EMP scar.

        Called by :meth:`apply_emp_launch`. ``hours`` is the inclusive
        span the clouds stay live: ``hour .. hour + cloud_hours - 1``.
        The scar (``emp_marks``) is the SPATIAL surface (map overlay) and
        the ``emp`` event is the TEMPORAL surface (agent feed); both are
        public per RULEBOOK §5.1.
        """
        hours = list(range(int(hour), int(hour) + max(1, int(cloud_hours))))
        self._record_combat_event({
            "type": "emp",
            "owner": str(owner),
            "targets": [[int(tx), int(ty)] for tx, ty in targets],
            "radius": int(radius),
            "cells": [[int(cx), int(cy)] for cx, cy in sorted(cells)],
            "hours": list(hours),
            "day": int(self.day),
        })
        for (cx, cy) in cells:
            key = f"{int(cx)}:{int(cy)}"
            mk = self.emp_marks.get(key)
            if mk is not None and int(mk.get("day", -1)) == int(self.day):
                owners = list(mk.get("owners") or [])
                if str(owner) not in owners:
                    owners.append(str(owner))
                merged = sorted(set(list(mk.get("hours") or []) + hours))
                self.emp_marks[key] = {
                    "owners": owners, "hours": merged, "day": int(self.day),
                }
            else:
                self.emp_marks[key] = {
                    "owners": [str(owner)],
                    "hours": list(hours),
                    "day": int(self.day),
                }

    def note_emp_hit(
        self, unit: str, victim: str, attacker: str, hour: int,
    ) -> None:
        """Record (merge per unit) one harvester-hour smothered by an EMP."""
        if not unit or not victim:
            return
        for ev in self._combat_events_today():
            if ev.get("type") == "emp_hit" and ev.get("unit") == str(unit):
                if attacker and str(attacker) not in ev["by"]:
                    ev["by"].append(str(attacker))
                if int(hour) not in ev["hours"]:
                    ev["hours"].append(int(hour))
                    ev["hours"].sort()
                return
        self._record_combat_event({
            "type": "emp_hit",
            "unit": str(unit),
            "victim": str(victim),
            "by": [str(attacker)] if attacker else [],
            "hours": [int(hour)],
            "day": int(self.day),
        })

    def record_snap_strike(
        self, owner: str, x: int, y: int, *, hour: int,
    ) -> None:
        """Record a PUBLIC SNAP strike (v1.36, §4.9.4).

        The twin of :meth:`record_emp_artifacts`, and public for the
        same reason: the round leaves a scorch mark every seat can see,
        so hiding the event would only cost agents an inference every
        human gets for free off the map.

        No decaying scar to stamp, though — a SNAP cloud lives one hour
        and there is no ``emp_marks`` equivalent to age. The event *is*
        the record.

        What it deliberately does not carry is who it hit. That is the
        victim's private detail and rides on :meth:`note_snap_hit`,
        exactly as ``emp`` and ``emp_hit`` are split.
        """
        self._record_combat_event({
            "type": "snap",
            "owner": str(owner),
            "at": [int(x), int(y)],
            "hours": [int(hour)],
            "day": int(self.day),
        })

    def note_snap_hit(
        self, unit: str, victim: str, attacker: str, *,
        hour: int, outcome: str,
    ) -> None:
        """Record one harvester a SNAP crippled — the victim's copy.

        ``outcome`` is ``"crippled"`` for a hull caught standing on the
        square or walking onto it, and ``"landing_aborted"`` for one
        turned back at the door. The distinction is worth carrying
        rather than inferring: both leave a damaged harvester, but one
        of them is damaged *in orbit* with its outing unspent, and an
        agent that cannot tell them apart will look for a wreck on the
        board that is not there.
        """
        if not unit or not victim:
            return
        for ev in self._combat_events_today():
            if ev.get("type") == "snap_hit" and ev.get("unit") == str(unit):
                if attacker and str(attacker) not in ev["by"]:
                    ev["by"].append(str(attacker))
                if int(hour) not in ev["hours"]:
                    ev["hours"].append(int(hour))
                    ev["hours"].sort()
                return
        self._record_combat_event({
            "type": "snap_hit",
            "unit": str(unit),
            "victim": str(victim),
            "by": [str(attacker)] if attacker else [],
            "hours": [int(hour)],
            "outcome": str(outcome),
            "day": int(self.day),
        })

    def record_chaff_flare(self, owner: str, hour: int, duration: int) -> None:
        """Record a PUBLIC chaff flare (seat-wide jam for ``duration`` h)."""
        hours = list(range(int(hour), int(hour) + max(1, int(duration))))
        for ev in self._combat_events_today():
            if (ev.get("type") == "chaff" and ev.get("owner") == str(owner)
                    and ev.get("hours") and ev["hours"][0] == int(hour)):
                return  # idempotent under the sim's peek-ahead retries
        self._record_combat_event({
            "type": "chaff",
            "owner": str(owner),
            "hours": list(hours),
            "day": int(self.day),
        })

    def note_chaff_jam(
        self, victim: str, attacker: str, hour: int,
        unit: Optional[str] = None,
    ) -> None:
        """Record (merge per victim) one action-slot jammed by a chaff."""
        if not victim:
            return
        for ev in self._combat_events_today():
            if ev.get("type") == "chaff_jam" and ev.get("victim") == str(victim):
                if attacker and str(attacker) not in ev["by"]:
                    ev["by"].append(str(attacker))
                if int(hour) not in ev["hours"]:
                    ev["hours"].append(int(hour))
                    ev["hours"].sort()
                if unit and str(unit) not in ev["units"]:
                    ev["units"].append(str(unit))
                return
        self._record_combat_event({
            "type": "chaff_jam",
            "victim": str(victim),
            "by": [str(attacker)] if attacker else [],
            "units": [str(unit)] if unit else [],
            "hours": [int(hour)],
            "day": int(self.day),
        })

    def _prune_emp_marks(self) -> None:
        """Drop EMP scars older than 1 game day (mirrors collision marks)."""
        if not self.emp_marks:
            return
        cutoff = int(self.day) - 1
        stale = [k for k, v in self.emp_marks.items()
                 if int(v.get("day", -1)) < cutoff]
        for k in stale:
            self.emp_marks.pop(k, None)

    def spawn_probe(
        self, owner: PlayerId, x: int, y: int, *, hour: Optional[int] = None,
    ) -> Tuple[bool, str]:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return False, f"probe out of map @({x},{y})"
        # Identify any pre-existing probes at the target cell BEFORE we
        # register the new probe (RULEBOOK §3.16). Each is classed as
        # either SIMULTANEOUS (deployed this very turn — same day + hour)
        # or a PRIOR occupant (an earlier hour or an earlier night, since
        # probes persist across dawn §3.11.1). The two cases resolve
        # differently:
        #   • PRIOR occupant   → the incoming probe DESTROYS + SUPERSEDES
        #                        it (the newcomer survives, the old one
        #                        goes to the DESTROYED ledger).
        #   • SIMULTANEOUS pair → mutual annihilation (both destroyed).
        # ``_probe_deploy_stamp`` is a transient {uid: (day, hour)} map
        # of probes deployed during the current night's resolution; a
        # probe absent from it (or stamped on an earlier day/hour) is by
        # definition a prior occupant.
        pre_existing = [
            (eid, en)
            for eid, en in self.entities.items()
            if en.entity_type == "probe" and en.x == x and en.y == y
        ]
        stamps: Dict[str, Tuple[int, Optional[int]]] = getattr(
            self, "_probe_deploy_stamp", None
        ) or {}
        self._probe_deploy_stamp = stamps  # type: ignore[attr-defined]

        self.probe_seq[owner] = self.probe_seq.get(owner, 0) + 1
        pid = self.probe_seq[owner]
        uid = f"probe_{owner}_{pid}"
        while uid in self.entities:
            self.probe_seq[owner] += 1
            uid = f"probe_{owner}_{self.probe_seq[owner]}"
        self.entities[uid] = Entity(uid, "probe", owner, x, y)
        cur_stamp: Tuple[int, Optional[int]] = (int(self.day), hour)
        stamps[uid] = cur_stamp
        # Probes always surface immediately — registration + deployment
        # happen in one breath. The asset ledger is then persisted so the
        # Snowflake-bound row is observable as soon as the action lands.
        self.asset_records[uid] = AssetRecord(
            asset_id=uid,
            asset_type="probe",
            owner=owner,
            session_id=self.session_id,
            created_on_day=self.day,
            first_deployed_day=self.day,
            last_seen_x=x,
            last_seen_y=y,
        )

        # Orbital publicity (§3.15 / §0.4): a probe's ballistic
        # trajectory punches the magnetic cover unbent, so every other
        # House sees where it landed. Pulse the opponent's intel and
        # write a structured ``probe_launch`` event regardless of any
        # subsequent collision — the launch itself was observable.
        self._pulse_probe_launch(owner, x, y, uid)

        # §3.16(d) — collision-crater sweep. When two+ probes mutually annihilate
        # on a cell they clear it, so a THIRD probe launched onto the SAME cell in
        # the SAME turn (same day+hour stamp) would find it empty and survive on a
        # pairwise reading — a rules bug that let a latecomer steal an "empty"
        # seam cell. We record every simultaneous-collision cell keyed by its
        # turn stamp and destroy any same-stamp arrival that lands in the crater.
        # (v1.15 — this shipped in v0.9.17 citing a "§3.16 E4" that was never in
        # the RULEBOOK; the rule is now written down as §3.16(d).)
        craters: Dict[Tuple[int, int], Tuple[int, Optional[int]]] = getattr(
            self, "_probe_collision_cells", None
        ) or {}
        self._probe_collision_cells = craters  # type: ignore[attr-defined]
        if craters.get((x, y)) == cur_stamp:
            self._note_asset_destroyed(uid, reason="probe_collision")
            self._queue_probe_death_fx(
                uid, owner, x, y, reason="probe_collision",
            )
            self.entities.pop(uid, None)
            stamps.pop(uid, None)
            self._forget_probe_marks_at(x, y, [uid])
            self.log_event(
                "probe_collision",
                f"probe {uid} landed on a simultaneous-collision crater at "
                f"({x},{y}); destroyed",
                at=[x, y],
                day=self.day,
                destroyed_ids=[uid],
                owners={uid: owner},
            )
            self._persist_asset_ledger()
            return (
                True,
                f"{owner} deployed {uid} at ({x},{y}); "
                f"probe collision destroyed {uid}",
            )

        if pre_existing:
            simultaneous = [
                (eid, en) for eid, en in pre_existing
                if stamps.get(eid) == cur_stamp
            ]
            superseded = [
                (eid, en) for eid, en in pre_existing
                if stamps.get(eid) != cur_stamp
            ]

            if simultaneous:
                # §3.16 mutual annihilation — two (or more) probes landed
                # on this cell on the SAME turn, so none survive. The new
                # probe and every co-incident probe are destroyed (any
                # stray prior occupant is swept up too, defensively).
                destroyed_ids: List[str] = (
                    [uid]
                    + [eid for eid, _ in simultaneous]
                    + [eid for eid, _ in superseded]
                )
                owners_by_id = {uid: owner}
                for eid, en in simultaneous + superseded:
                    owners_by_id[eid] = cast(PlayerId, en.owner)
                for eid in destroyed_ids:
                    self._note_asset_destroyed(eid, reason="probe_collision")
                    self._queue_probe_death_fx(
                        eid, owners_by_id[eid], x, y,
                        reason="probe_collision",
                    )
                    self.entities.pop(eid, None)
                    stamps.pop(eid, None)
                self._forget_probe_marks_at(x, y, destroyed_ids)
                # v1.15 — the hour is ONE event (§3.16(c)). An earlier seat
                # in this same hour may already have taken the cell's
                # incumbent as a supersede; a second arrival proves nobody
                # held the cell, so that death re-files as a collision and
                # the credit is withdrawn.
                self._finalise_probe_contest(x, y, cur_stamp)
                # Mark the crater so a same-turn latecomer (E4) dies here too.
                craters[(x, y)] = cur_stamp
                self.log_event(
                    "probe_collision",
                    f"probe collision at ({x},{y}); "
                    + ", ".join(destroyed_ids)
                    + " all destroyed",
                    at=[x, y],
                    day=self.day,
                    destroyed_ids=destroyed_ids,
                    owners=owners_by_id,
                )
                self._persist_asset_ledger()
                return (
                    True,
                    f"{owner} deployed {uid} at ({x},{y}); "
                    f"probe collision destroyed {', '.join(destroyed_ids)}",
                )

            # §3.16 supersession — the incoming probe lands on top of an
            # OLDER probe (earlier hour or earlier night). The newcomer
            # destroys + replaces it; the newcomer survives.
            destroyed_ids = [eid for eid, _ in superseded]
            owners_by_id = {
                eid: cast(PlayerId, en.owner) for eid, en in superseded
            }
            for eid in destroyed_ids:
                self._note_asset_destroyed(eid, reason="probe_superseded")
                self._queue_probe_death_fx(
                    eid, owners_by_id[eid], x, y,
                    reason="probe_superseded",
                    crusher_owner=owner,
                )
                # v1.6 kill-feed: the incoming probe's house superseded the
                # older probe's house. v1.15 — provisional until the hour
                # closes; a second probe onto this cell on this stamp makes
                # it a collision instead (see _finalise_probe_contest).
                self._attrib("probes_superseded", owner, owners_by_id.get(eid))
                self._record_provisional_supersede(
                    x, y, cur_stamp, eid, owners_by_id[eid], owner,
                )
                self.entities.pop(eid, None)
                stamps.pop(eid, None)
            self._forget_probe_marks_at(x, y, destroyed_ids)
            self.log_event(
                "probe_superseded",
                f"probe {uid} superseded "
                + ", ".join(destroyed_ids)
                + f" at ({x},{y})",
                at=[x, y],
                day=self.day,
                new_probe=uid,
                new_owner=owner,
                destroyed_ids=destroyed_ids,
                owners=owners_by_id,
            )
            self._persist_asset_ledger()
            return (
                True,
                f"{owner} deployed {uid} at ({x},{y}); "
                f"superseded {', '.join(destroyed_ids)}",
            )

        self._persist_asset_ledger()
        return True, f"{owner} deployed {uid} at ({x},{y})"

    def _pulse_probe_launch(
        self, owner: PlayerId, x: int, y: int, probe_id: str
    ) -> None:
        """Make a probe launch publicly observable (RULEBOOK §3.15).

        - Pulses ``self.probe_intel[opponent]`` at ``(x, y)`` with a
          **probe-only** marker stamped ``via='probe_launch'`` and
          ``day_seen=self.day``. The marker carries the probe occupant
          payload so the opposing seat's map can render the probe
          glyph, but it deliberately omits ``paint`` / ``tile`` /
          ``purity`` so the underlying terrain stays fog (v0.9.7).
          Pre-v0.9.7 a full tile snapshot was broadcast, which let
          opponents inspect terrain they had never observed AND let
          their bots drop harvesters on that cell as if they had
          echo coverage. The new rule: probe-launch markers reveal
          the probe entity, not the terrain underneath.
        - Emits a structured ``probe_launch`` log event the harness
          surfaces under ``competitor_intel.new_this_day`` as
          ``enemy_probe_launch`` for the non-owning seat.
        - Harvester drops intentionally do NOT call this helper — the
          orblift bends through the magnetic cover (§0.4) and orbital
          observers lose the harvester at the cover boundary.
        """
        cell = self.grid[y][x]
        # v0.9.7 — probe-only marker. Capture just the probe occupant
        # payload (NOT the full terrain snapshot) so the opposing seat
        # learns "there is a probe at (x,y)" but the tile/purity/paint
        # under it stays fog. ``occupants`` is filtered to the probe
        # we just launched, so harvesters / mines / other probes on
        # the same tile aren't leaked either.
        probe_occ = None
        probe_ent_obj: Optional[Entity] = None
        for ent in self._entities_on_tile_sorted(x, y):
            if ent.id == probe_id:
                probe_occ = occupant_wire(ent, self)
                probe_ent_obj = ent
                break
        snap: dict[str, Any] = {
            "via": "probe_launch",
            "day_seen": self.day,
            "launched_by": owner,
            "probe_id": probe_id,
            "occupants": [probe_occ] if probe_occ else [],
        }
        k = _xy_key(x, y)
        for opp in self.players:
            if opp == owner:
                continue
            # If the opponent ALREADY has a richer echo for (x,y)
            # (e.g. their probe / harvester previously observed this
            # cell), don't overwrite it with the probe-only marker —
            # we'd be hiding terrain the seat had legitimately
            # revealed. Merge the probe occupant onto whatever's
            # there so the glyph still shows up.
            existing = self.probe_intel[cast(PlayerId, opp)].get(k)
            if existing and existing.get("via") != "probe_launch":
                occ_list = list(existing.get("occupants") or [])
                if probe_occ:
                    occ_list = [
                        o for o in occ_list
                        if not (isinstance(o, dict) and o.get("id") == probe_id)
                    ]
                    occ_list.append(probe_occ)
                existing["occupants"] = occ_list
                # §3.15 FIX (seed-69 E2). Merging the probe occupant onto an
                # OLDER terrain echo preserves the richer terrain — but if we
                # stop here the launch inherits the old echo's stale
                # ``day_seen`` and carries no launch stamp, so the view's
                # competitor-intel can't tell a fresh enemy probe just landed
                # here. That is exactly how the redsign-discovering probe went
                # invisible to the rival. Stamp the launch day + launcher so
                # the view surfaces it as ``enemy_probe_launch`` for the right
                # night WITHOUT overwriting the terrain snapshot's own
                # ``day_seen`` (which would falsely age the fog terrain fresh).
                existing["probe_launch_day"] = int(self.day)
                existing["launched_by"] = owner
                # v1.11 (RULEBOOK §3.15) — the FIX above made the launch
                # surface in the agent-facing intel feed, but never gave
                # the MAP a drawable glyph: the renderer's ghost-glyph
                # path is gated on the terrain echo's own ``day_seen``
                # (deliberately NOT bumped here — see above), so the
                # newly-merged probe occupant sat in ``occupants`` but
                # was never promoted to a visible ``entity``. Stamp a
                # SEPARATE, dedicated glyph marker (independent of the
                # terrain's staleness) so the public launch is always
                # visible on the map too, exactly as RULEBOOK §3.15
                # intends. Cleared by :meth:`_clear_probe_launch_markers`
                # when this probe dies/decays.
                if probe_occ and probe_ent_obj is not None:
                    gh, gf = self._glyph_for_entity(probe_ent_obj)
                    existing["probe_launch_glyph"] = {
                        "ch": gh, "fg": gf, "probe_id": probe_id,
                    }
                continue
            self.probe_intel[cast(PlayerId, opp)][k] = dict(snap)
        try:
            tile_name = Tile(int(cell.tile)).name
        except Exception:
            tile_name = str(int(cell.tile))
        self.log_event(
            "probe_launch",
            f"{owner} launched {probe_id} at ({x},{y}) — visible from orbit",
            owner=owner,
            probe_id=probe_id,
            at=[x, y],
            landed_on_tile=tile_name,
            landed_purity=int(cell.purity),
            day=self.day,
        )

    def _clear_probe_launch_markers(self, probe_id: str, x: int, y: int) -> None:
        """Remove ``via='probe_launch'`` echo markers for a destroyed probe.

        Called at every probe-death site (EMP, expiry, collision, crush)
        so opposing seats' maps stop showing the dead probe.  Only removes
        entries where the marker's ``probe_id`` field or its ``occupants``
        list matches ``probe_id`` — avoids collateral damage when a fresh
        probe later lands on the same cell and writes a new marker.

        v1.11 (RULEBOOK §3.15) — a probe that got merged onto a RICHER
        terrain echo (``via`` preserved as the echo's own, not
        ``"probe_launch"``) previously skipped this cleanup entirely: its
        ``occupants`` entry AND the dedicated ``probe_launch_glyph`` this
        fix now stamps (see :meth:`_pulse_probe_launch`) would otherwise
        linger forever as a permanent ghost, since that path isn't gated
        by the day-based glyph decay the general echo path uses. Every
        bucket's entry at ``(x, y)`` is now pruned by ``probe_id``
        regardless of ``via``.
        """
        k = _xy_key(x, y)
        for bucket in self.probe_intel.values():
            entry = bucket.get(k)
            if entry is None:
                continue
            if entry.get("via") == "probe_launch":
                if entry.get("probe_id") == probe_id:
                    del bucket[k]
                    continue
                occ = entry.get("occupants") or []
                if any(isinstance(o, dict) and o.get("id") == probe_id for o in occ):
                    remaining = [
                        o for o in occ
                        if not (isinstance(o, dict) and o.get("id") == probe_id)
                    ]
                    if remaining:
                        entry["occupants"] = remaining
                    else:
                        del bucket[k]
                continue
            # Merged-onto-richer-echo case (v1.11): prune the occupant
            # AND the dedicated glyph marker, but leave the terrain
            # snapshot itself untouched.
            occ = entry.get("occupants") or []
            if any(isinstance(o, dict) and o.get("id") == probe_id for o in occ):
                entry["occupants"] = [
                    o for o in occ
                    if not (isinstance(o, dict) and o.get("id") == probe_id)
                ]
            pg = entry.get("probe_launch_glyph")
            if isinstance(pg, dict) and pg.get("probe_id") == probe_id:
                entry.pop("probe_launch_glyph", None)

    def lifter_for(self, owner: PlayerId) -> Optional[Entity]:
        for e in self.entities.values():
            if e.entity_type == "orblift" and e.owner == owner:
                return e
        return None

    def try_drop_unit(
        self,
        owner: PlayerId,
        harvester_id: str,
        x: int,
        y: int,
        harvest_budget: int = 0,  # back-compat; no longer used (v0.6.0)
        live_override: Optional[Set[Tuple[int, int]]] = None,
        emp_blocked_cells: Optional[Set[Tuple[int, int]]] = None,
        snap_hot_cells: Optional[Dict[Tuple[int, int], Dict[str, Any]]] = None,
        departing_units: Optional[AbstractSet[str]] = None,
    ) -> Tuple[bool, str, bool]:
        """Drop a berthed harvester onto the surface.

        A harvester harvests the coloured square it lands on
        (RULEBOOK §3.12 — v0.6.0). RED→GREEN, GREEN→EMPTY, BLUE→EMPTY.
        There is no per-color cap; the natural limit is the
        6-parcel hold (drop + 5 steps). Returns
        ``(ok, message, harvested)`` to match :meth:`try_step_unit`.
        The ``harvest_budget`` parameter is preserved for back-compat
        with v0.5 callers but is no longer consulted.

        v1.10 (RULEBOOK §4.9.3) — ``emp_blocked_cells`` is the set of
        cells inside an EMP cloud that was already active before this
        hour's own launches resolved (passed by the night simulator).
        The harvester still lands on such a cell — the drop itself is
        unaffected — but the auto-harvest is denied.

        v1.36 (§4.9.4) — ``snap_hot_cells`` maps cells SNAPped earlier
        this hour to ``{"by": seat, "hour": h}``. A landing into one is
        refused outright and the hull is damaged in orbit, which is the
        same shape as arriving into a rival harvester and a harder stop
        than an EMP cloud (which lands you and only denies the harvest).
        """
        del harvest_budget  # unused — kept for signature compat
        hh = self.entities.get(harvester_id)
        if hh is None or hh.entity_type != "harvester" or hh.owner != owner:
            return False, f"drop: bad harvester '{harvester_id}'", False
        lf = self.lifter_for(owner)
        if lf is None or lf.x is not None or lf.y is not None:
            return False, "drop: lifter unavailable (must be orbital)", False
        if hh.x is not None:
            return False, f"drop: {harvester_id} already on surface", False
        # v1.24 (RULEBOOK §3.6.1) — a wreck may not be deployed. This is the
        # twin of the guard in ``try_step_unit``; it was missing, so a damaged
        # harvester sitting in orbit could be dropped straight back onto the
        # surface, and because the landing AUTO-HARVESTS (§3.12) that single
        # slot broke both halves of the rule at once — "cannot step or harvest
        # until repaired" AND "must be repaired via the paid Orbit repair
        # action before it can be deployed again". Since v0.9.18 pickup no
        # longer clears the flag, so without this check the paid REPAIR action
        # was entirely optional: crash, lift, re-drop, keep mining for free.
        if bool(getattr(hh, "damaged", False)):
            return (
                False,
                f"drop: {harvester_id} damaged — repair it in orbit first "
                f"(RULEBOOK §3.6.1)",
                False,
            )
        # RULEBOOK §3.9.2 — ONE OUTING PER HARVESTER PER NIGHT. Even after a
        # harvester lifts back to orbit (pickup clears its position), it may not
        # be re-dropped the same night. Blocks the "pick up + re-drop to bank
        # more within one night" loophole so a single unit can never make two
        # sorties in a night. Scoped by day so it resets every night.
        if harvester_id in self.deployed_harvesters_by_day.get(int(self.day), set()):
            return (
                False,
                f"drop: {harvester_id} already made its outing this night "
                f"(RULEBOOK §3.9.2 — one outing per harvester per night)",
                False,
            )
        if not (0 <= x < self.width and 0 <= y < self.height):
            return False, f"drop: ({x},{y}) out of bounds", False

        # v0.9.2 — RULEBOOK §3.10 update: a harvester can only LAND on
        # a cell the seat currently observes (live LOS) or has a probe
        # echo / past memory for (echo). Pure fog is forbidden, because
        # blind-dropping into terrain you've never seen makes the
        # opening probe scout meaningless. Once on the surface a
        # harvester can step / be picked up from anywhere — only the
        # initial landing is gated.
        #
        # v0.9.7 — tighten the echo rule. ``via='probe_launch'``
        # markers (seeded by :meth:`_pulse_probe_launch` when an
        # opponent's probe lands) reveal the probe entity to the
        # opposing seat but DELIBERATELY keep the terrain fogged —
        # they no longer count as drop-valid echoes. Otherwise the
        # opportunistic chain (enemy launches at (4,21) → I drop a
        # harvester there → my harvester's surface LoS pulse seeds
        # the surrounding disk for me) lets seats freely land
        # harvesters on terrain they have never legitimately
        # observed. Drops now require either live LoS, an OWN-echo
        # (probe pulse / harvester pulse / memory), or any echo
        # whose ``via`` is NOT ``probe_launch``.
        # ``live_override`` (when supplied by the night simulator) is the
        # seat's HOUR-START visible-tile snapshot, so a beacon
        # destroyed/superseded/EMP'd later in the *same* hour still
        # validates this hour's landing — parallel resolution (§3.10),
        # mirroring the same-hour rule used for probe collisions. Ad-hoc
        # callers (tests, single-shot drops) pass nothing and get the
        # live LOS as of now.
        live = live_override if live_override is not None else self.tiles_visible_now(owner)
        if (x, y) not in live:
            # v0.9.17 — ``SOC_DROP_MODE=live_only`` (RULEBOOK §3.9.7): a
            # harvester may ONLY be set down where the seat has a LIVE
            # sensor beacon right now (probe disk or a friendly
            # harvester's plus). Stale own-echo / memory no longer counts
            # — farming a remembered vein first costs a fresh probe (a
            # public, contestable act, §3.15).
            if live_only_drops():
                return (
                    False,
                    f"drop: ({x},{y}) has no live sensor beacon — "
                    f"live-only mode needs an active probe disk or a "
                    f"friendly harvester's plus over the landing cell "
                    f"(RULEBOOK §3.9.7). Drop a probe there first.",
                    False,
                )
            ekey = _xy_key(x, y)
            echo_entry = self.probe_intel.get(owner, {}).get(ekey)
            has_echo = (
                echo_entry is not None
                and echo_entry.get("via") != "probe_launch"
            )
            has_memory = ekey in self.memory_tiles.get(owner, {})
            if not has_echo and not has_memory:
                return (
                    False,
                    f"drop: ({x},{y}) is in fog — harvesters can only "
                    f"land on live or own-echo tiles (RULEBOOK §3.10). "
                    f"Enemy probe-launch markers don't reveal terrain.",
                    False,
                )

        # Mutual-damage collision (§3.6 v0.9.10) — dropping onto a
        # cell with one or more HEALTHY harvesters. The harvester(s)
        # already on the board stay (and become damaged). The dropping
        # harvester never lands — it stays in orbit (damaged). Damaged
        # harvesters already there are unaffected (they're wreckage;
        # this lifter can land beside them).
        owner_play = cast(PlayerId, owner)
        collisions = self._undamaged_harvesters_at(
            x, y, exclude=hh.id, departing=departing_units,
        )
        if collisions:
            # Dropping harvester stays orbital, becomes damaged (blamed on
            # the defender it rammed); defenders damaged by the dropper.
            spilled_self = self._damage_harvester(hh, by=collisions[0].owner)
            spilled_other = sum(
                self._damage_harvester(o, by=hh.owner) for o in collisions
            )
            involved_owners = [hh.owner] + [o.owner for o in collisions]
            involved_harvesters = [hh.id] + [o.id for o in collisions]
            self._record_collision(
                x, y, involved_owners,
                event_type="drop_on",
                harvesters=involved_harvesters,
            )
            other_label = ", ".join(o.id for o in collisions)
            owners_label = "+".join(sorted(set(involved_owners)))
            return (
                True,
                (
                    f"{owner} dropped {harvester_id} at ({x},{y}); "
                    f"COLLISION ({owners_label}) — {other_label} on board "
                    f"stays damaged, {harvester_id} stays orbital damaged, "
                    f"{spilled_self + spilled_other} cargo square(s) lost"
                ),
                False,
            )

        # v1.36 (§4.9.4) — a SNAP put on this cell earlier in the hour.
        # Resolved here, ABOVE the landing, and shaped exactly like the
        # collision immediately above: a landing that arrives into
        # something does not complete. The hull is damaged and stays in
        # orbit, and because it never touched down it never spends its
        # one outing for the night (§3.9.2) either.
        #
        # This is the one place SNAP treats a landing differently from a
        # walk-in. A harvester STEPPING onto a hot cell does move — it is
        # already on the surface, so there is nowhere to refuse it to —
        # and is crippled where it stands. A landing has an orbit to be
        # sent back to, so it is.
        #
        # Below the collision check on purpose: a cell holding both a
        # rival hull and a SNAP is a collision first, because that is the
        # rarer thing and the one the caption should name.
        if snap_hot_cells and (x, y) in snap_hot_cells:
            hit = snap_hot_cells[(x, y)]
            by, hr = str(hit.get("by", "")), int(hit.get("hour", 0))
            spilled = self._damage_harvester(
                hh, by=by, category="snap_harvesters",
            )
            self.note_snap_hit(
                harvester_id, str(hh.owner), by,
                hour=hr, outcome="landing_aborted",
            )
            lost = (
                f", {spilled} cargo square(s) lost" if spilled else ""
            )
            return (
                True,
                (
                    f"{owner} dropped {harvester_id} at ({x},{y}); "
                    f"SNAP on the cell — landing ABORTED, {harvester_id} "
                    f"stays orbital damaged{lost}"
                ),
                False,
            )

        hh.x, hh.y = x, y
        self._bump_path(owner_play, x, y, harvester_id=harvester_id)
        self._note_asset_deployed(harvester_id)
        # RULEBOOK §3.9.2 — record this harvester's (one) outing for the night,
        # so a subsequent pickup + re-drop the same night is refused by the guard
        # above. Only the actual-landing path records it (a collision that leaves
        # the unit orbital did not make an outing).
        self.deployed_harvesters_by_day.setdefault(int(self.day), set()).add(
            harvester_id
        )

        origin_tile = self.grid[y][x].tile
        # v1.10 (RULEBOOK §4.9.3) — landing inside an ALREADY-established
        # EMP cloud denies the auto-harvest outright. The harvester still
        # lands (it isn't bounced like a mine) and will go empd starting
        # next hour; it just banks nothing on this landing.
        if emp_blocked_cells and (x, y) in emp_blocked_cells:
            return (
                True,
                (
                    f"{owner} dropped {harvester_id} at ({x},{y}); "
                    f"EMP cloud denies auto-harvest"
                ),
                False,
            )
        harvested, site_id = self._harvest_at(owner_play, harvester_id, x, y)
        if not harvested:
            return True, f"{owner} dropped {harvester_id} at ({x},{y})", False
        colour = origin_tile.name
        return (
            True,
            (
                f"{owner} dropped {harvester_id} at ({x},{y}); "
                f"auto-harvested {colour} @{site_id}"
            ),
            True,
        )

    def try_step_unit(
        self,
        owner: PlayerId,
        harvester_id: str,
        nx: int,
        ny: int,
        harvest_budget: int = 0,  # back-compat; no longer used (v0.6.0)
        emp_blocked_cells: Optional[Set[Tuple[int, int]]] = None,
        snap_hot_cells: Optional[Dict[Tuple[int, int], Dict[str, Any]]] = None,
        departing_units: Optional[AbstractSet[str]] = None,
    ) -> Tuple[bool, str, bool]:
        """Step a harvester to an adjacent tile.

        Returns ``(ok, message, harvested)``. ``harvested`` is True when
        the step landed on a coloured tile (RED / GREEN / BLUE) and
        banked it into the harvester's cargo. RED → GREEN, GREEN →
        EMPTY, BLUE → EMPTY. The ``harvest_budget`` parameter is
        preserved for back-compat with v0.5 callers but is no longer
        consulted (v0.6.0 removed the per-night RED cap).

        v1.10 (RULEBOOK §4.9.3) — ``emp_blocked_cells`` is the set of
        cells inside an EMP cloud that was already active before this
        hour's own launches resolved (passed by the night simulator).
        The step itself still lands on such a cell; the auto-harvest
        is denied.

        v1.36 (§4.9.4) — ``snap_hot_cells`` maps cells SNAPped earlier
        this hour to ``{"by": seat, "hour": h}``. The step completes and
        the harvester is crippled where it lands, harvesting nothing —
        which is the half of SNAP that lets it guard a square instead of
        only punishing one.
        """
        del harvest_budget  # unused — kept for signature compat
        h = self.entities.get(harvester_id)
        if h is None or h.entity_type != "harvester" or h.owner != owner:
            return False, f"step: bad harvester '{harvester_id}'", False
        if h.x is None or h.y is None:
            return False, f"step: {harvester_id} not on surface", False
        if bool(getattr(h, "damaged", False)):
            return False, f"step: {harvester_id} damaged — awaiting pickup", False
        if not _adj((h.x, h.y), (nx, ny)):
            return False, f"step: {harvester_id} ({h.x},{h.y})→({nx},{ny}) not adjacent", False
        if not (0 <= nx < self.width and 0 <= ny < self.height):
            return False, f"step: ({nx},{ny}) out of bounds", False

        # Per-outing hold cap (RULEBOOK §3). A harvester banks every
        # coloured cell it enters until its hold is full at
        # HARVESTER_HOLD_CAPACITY parcels (drop + up to 5 steps). Once
        # full, further steps are cancelled: it keeps its <=6 parcels and
        # must be picked up + re-dropped to bank more. Guards against a
        # hand-built chain walking on and banking >6 parcels in one outing.
        if len(h.cargo_squares) >= HARVESTER_HOLD_CAPACITY:
            return (
                False,
                (
                    f"step: {harvester_id} hold full "
                    f"({HARVESTER_HOLD_CAPACITY} parcels/outing, RULEBOOK §3) "
                    f"— pick up + re-drop to bank more"
                ),
                False,
            )

        # v1.31 — the caltrop check used to sit here, ahead of collision
        # and harvest, cancelling the step and damaging the stepper.
        # Retired with the weapon.

        # Mutual-damage collision (§3.6 v0.9.9): stepping into a cell
        # with one or more HEALTHY harvesters — move is cancelled, both
        # stay at their current positions, both become damaged.
        # Damaged harvesters at the target tile don't trigger collision.
        owner_play = cast(PlayerId, owner)
        collisions = self._undamaged_harvesters_at(
            nx, ny, exclude=harvester_id, departing=departing_units,
        )
        if collisions:
            # Stepper stays at (h.x, h.y) — no position change.
            spilled_self = self._damage_harvester(h, by=collisions[0].owner)
            spilled_other = sum(
                self._damage_harvester(o, by=h.owner) for o in collisions
            )
            involved_owners = [h.owner] + [o.owner for o in collisions]
            involved_harvesters = [h.id] + [o.id for o in collisions]
            # Mark the target cell (where the defenders are)
            self._record_collision(
                nx, ny, involved_owners,
                event_type="step_into",
                harvesters=involved_harvesters,
            )
            # Also mark the stepper's cell so the explosion shows there
            self._record_collision(
                int(h.x), int(h.y), involved_owners,
                event_type="step_into",
                harvesters=involved_harvesters,
            )
            other_label = ", ".join(o.id for o in collisions)
            owners_label = "+".join(sorted(set(involved_owners)))
            return (
                True,
                (
                    f"{harvester_id} → ({nx},{ny}) BLOCKED; "
                    f"COLLISION ({owners_label}) — {harvester_id} + "
                    f"{other_label} damaged, neither moves, "
                    f"{spilled_self + spilled_other} cargo square(s) lost"
                ),
                False,
            )

        h.x, h.y = nx, ny
        self._bump_path(owner_play, nx, ny, harvester_id=harvester_id)
        self._note_asset_deployed(harvester_id)

        origin_tile = self.grid[ny][nx].tile
        # v1.36 (§4.9.4) — stepping onto a cell SNAPped earlier this
        # hour. Unlike the drop path this does NOT rewind the move: the
        # harvester is already on the surface, so there is no orbit to
        # refuse it back to, and the square it walked onto is the square
        # it is now wrecked on. Above the EMP gate because damage
        # outranks a denied harvest as a description of what happened.
        if snap_hot_cells and (nx, ny) in snap_hot_cells:
            hit = snap_hot_cells[(nx, ny)]
            self._snap_damage_harvesters_at(
                {(nx, ny)},
                by=str(hit.get("by", "")),
                hour=int(hit.get("hour", 0)),
            )
            return (
                True,
                (
                    f"{harvester_id} → ({nx},{ny}) into a SNAP — "
                    f"crippled, no harvest"
                ),
                False,
            )
        # v1.10 (RULEBOOK §4.9.3) — stepping into an ALREADY-established
        # EMP cloud denies the auto-harvest outright. The step still
        # lands (the harvester moves onto the cell) and will go empd
        # starting next hour; it just banks nothing on this step.
        if emp_blocked_cells and (nx, ny) in emp_blocked_cells:
            return (
                True,
                f"{harvester_id} → ({nx},{ny}); EMP cloud denies auto-harvest",
                False,
            )
        harvested, site_id = self._harvest_at(owner_play, harvester_id, nx, ny)
        if not harvested:
            return True, f"{harvester_id} → ({nx},{ny})", False
        colour = origin_tile.name
        return (
            True,
            f"{harvester_id} harvested {colour} at ({nx},{ny}) @{site_id}",
            True,
        )

    def try_pickup_unit(
        self, owner: PlayerId, harvester_id: str
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        hh = self.entities.get(harvester_id)
        if hh is None or hh.entity_type != "harvester" or hh.owner != owner:
            return False, f"pickup: bad harvester '{harvester_id}'", []
        lf = self.lifter_for(owner)
        if lf is None or lf.x is not None:
            return False, "pickup: lifter unavailable (must be orbital)", []
        if hh.x is None:
            return False, f"pickup: {harvester_id} already orbital", []
        was_damaged = bool(getattr(hh, "damaged", False))
        parcels = list(hh.cargo_squares)
        hh.cargo_squares.clear()
        self._sync_carrier(hh)
        lf.orbital_cargo_red = False
        hh.x = hh.y = None
        # v0.9.18 — free repair removed. Pickup no longer clears the
        # damage flag. A damaged harvester must be repaired via the paid
        # Orbit REPAIR action (500c) before it can be deployed again.
        # The cargo lost when the unit took damage is gone.
        tail = " (DAMAGED — requires REPAIR)" if was_damaged else ""
        return (
            True,
            f"{harvester_id} lifted to berth — banked {len(parcels)} parcel(s){tail}",
            parcels,
        )

    def cleanup_probes(self) -> List[str]:
        """Legacy hook: probes used to be retracted at dawn.

        Since RULEBOOK §3.11.1 (v0.3.4) probes persist across dawn and
        are only destroyed when a harvester rides over them
        (:meth:`consume_probes_at`). This method is preserved as a no-op
        so older callers don't break; it returns an empty list.
        """
        return []

    def probe_nights_remaining(self, ent: "Entity") -> Optional[int]:
        """Nights of live coverage a deployed probe has left.

        ``None`` when probe decay is off (``SOC_PROBE_LIFETIME_NIGHTS``
        unset), the entity isn't a deployed probe, or it has no asset
        record to date from. Single source of truth for the per-probe
        countdown surfaced on the map glyph, the hover tooltip, the
        asset roster and the agent's view — own probes AND enemy ones.
        """
        if ent is None or ent.entity_type != "probe" or ent.x is None:
            return None
        k = probe_lifetime_nights()
        if not k:
            return None
        rec = self.asset_records.get(ent.id)
        first = int(
            (
                rec.first_deployed_day
                if rec and rec.first_deployed_day is not None
                else rec.created_on_day if rec else 0
            )
            or 0
        )
        return max(0, int(k) - max(0, int(self.day) - first))

    def decay_probes(self) -> List[str]:
        """Expire probes that have outlived ``SOC_PROBE_LIFETIME_NIGHTS``.

        v0.9.17 — optional probe decay (RULEBOOK §3.11.1). When the knob
        is unset (default) probes persist forever and this is a no-op.
        When set to ``K`` nights, a probe deployed on day ``D`` is removed
        at the dawn of the night on which it has been present for ``K``
        nights (``self.day - D + 1 >= K``); its disk simply drops to echo
        like any other destruction, so the owner keeps the last snapshot
        but loses live coverage and must re-probe (a public, contestable
        act, §3.15) to keep farming the spot. Called once per dawn from
        the night simulator, BEFORE the day counter increments.
        """
        k = probe_lifetime_nights()
        if not k:
            return []
        expired: List[Tuple[str, Any]] = []
        for eid, en in list(self.entities.items()):
            if en.entity_type != "probe":
                continue
            rec = self.asset_records.get(eid)
            start = self.day
            if rec is not None:
                start = int(
                    rec.first_deployed_day
                    if rec.first_deployed_day is not None
                    else rec.created_on_day
                )
            if (int(self.day) - start + 1) >= int(k):
                expired.append((eid, en))
        stamps = getattr(self, "_probe_deploy_stamp", None)
        ids: List[str] = []
        for eid, en in expired:
            self._note_asset_destroyed(eid, reason="probe_expired")
            if en.x is not None and en.y is not None:
                self._clear_probe_launch_markers(eid, int(en.x), int(en.y))
            self.entities.pop(eid, None)
            if stamps:
                stamps.pop(eid, None)
            ids.append(eid)
        if expired:
            self._persist_asset_ledger()
            self.log_event(
                "probe_expired",
                f"probe lifetime ({int(k)} nights) reached — expired "
                + ", ".join(ids),
                day=self.day,
                ids=ids,
            )
        # v1.19 — this runs at EVERY Aurora, which is what v1.2 always
        # specified. It used to sit behind an early ``return`` taken when no
        # live probe happened to expire tonight, so a marker for a secretly
        # destroyed probe only cleared if some UNRELATED probe died on the
        # same dawn. With no other probes in play it never cleared at all.
        self._expire_stale_launch_markers(int(k))
        return ids

    def _expire_stale_launch_markers(self, lifetime_nights: int) -> None:
        """Retire §3.15 launch markers on the Aurora their probe was
        always scheduled to expire.

        A probe destroyed by crush / collision / supersede stays in the
        non-witnesses' intel on purpose (v1.2): they never saw it die, so
        they keep "last known position" until the night it was going to
        expire anyway. That schedule is the only bound on the marker, so it
        has to actually fire — otherwise a seat is looking at a probe that
        has been gone for the whole season.

        Both marker shapes are handled, which is the second half of the
        v1.19 fix. A launch onto a cell the viewer had never scouted is its
        own ``via="probe_launch"`` entry and the entry goes. A launch onto a
        cell they HAD scouted was merged onto that richer terrain echo
        (v1.11), leaving only a ``probe_launch_glyph`` overlay — the older
        sweep matched on ``via`` and so walked straight past those, and they
        were the exact case v1.11 called out as lingering "forever as a
        permanent ghost". Only the overlay is stripped there; the seat's own
        terrain snapshot is theirs and stays.
        """
        for bucket in self.probe_intel.values():
            drop: List[str] = []
            for key, entry in bucket.items():
                pid = self._launch_marker_probe_id(entry)
                if pid is None or pid in self.entities:
                    continue  # live probe — the lifetime pass owns it
                if not self._launch_marker_is_due(entry, pid, lifetime_nights):
                    continue
                if entry.get("via") == "probe_launch":
                    drop.append(key)
                else:
                    self._strip_launch_overlay(entry, pid)
            for key in drop:
                del bucket[key]

    def _launch_marker_probe_id(self, entry: dict[str, Any]) -> Optional[str]:
        """Which probe a §3.15 marker is about, or ``None`` if not a marker."""
        glyph = entry.get("probe_launch_glyph")
        if isinstance(glyph, dict) and glyph.get("probe_id"):
            return str(glyph["probe_id"])
        is_marker = (
            entry.get("via") == "probe_launch"
            or entry.get("probe_launch_day") is not None
        )
        if not is_marker:
            return None
        if entry.get("probe_id"):
            return str(entry["probe_id"])
        for occ in entry.get("occupants") or []:
            if isinstance(occ, dict) and occ.get("type") == "probe" and occ.get("id"):
                return str(occ["id"])
        return None

    def _launch_marker_is_due(
        self, entry: dict[str, Any], probe_id: str, lifetime_nights: int,
    ) -> bool:
        """Has this marker's probe reached the dawn it would have expired?"""
        first: Optional[int] = None
        rec = self.asset_records.get(probe_id)
        if rec is not None:
            stamp = (
                rec.first_deployed_day
                if rec.first_deployed_day is not None
                else rec.created_on_day
            )
            if stamp is not None:
                first = int(stamp)
        if first is None:
            # No ledger row to date it by (hydrated / legacy state). Fall
            # back to the day the marker itself records rather than assuming
            # the worst, so a launch stamped tonight isn't retired tonight.
            for field_name in ("probe_launch_day", "day_seen"):
                stamp = entry.get(field_name)
                if isinstance(stamp, (int, float)):
                    first = int(stamp)
                    break
        if first is None:
            return True
        return (int(self.day) - first + 1) >= int(lifetime_nights)

    def _strip_launch_overlay(self, entry: dict[str, Any], probe_id: str) -> None:
        """Remove the launch overlay, leaving the viewer's terrain echo.

        Mirrors the merged-case branch of
        :meth:`_clear_probe_launch_markers` so a marker retired by schedule
        and one retired by a witnessed death leave the same echo behind.
        """
        occ = entry.get("occupants") or []
        if any(isinstance(o, dict) and o.get("id") == probe_id for o in occ):
            entry["occupants"] = [
                o for o in occ
                if not (isinstance(o, dict) and o.get("id") == probe_id)
            ]
        glyph = entry.get("probe_launch_glyph")
        if isinstance(glyph, dict) and glyph.get("probe_id") == probe_id:
            entry.pop("probe_launch_glyph", None)
        entry.pop("probe_launch_day", None)

    def _freeze_final_probe_echo(self, probe: "Entity") -> None:
        """Take a probe's last look, the instant before it is destroyed.

        v1.8 — the probe witnesses its own killer (bug #1). The crusher
        has already recorded its trail on the cell by the time we get
        here (drop/step runs first), so the owner's intel keeps a final
        snapshot carrying the fresh trail and the harvester glyph. The
        next scheduled camera pulse cannot do this, because by then the
        probe is gone.

        v1.19 — that last look has to cover the probe's WHOLE vision
        disk, not just the cell it died on (bug #14). Refreshing one cell
        leaves the echo stitched from two different moments: the death
        cell shows the harvester where it now stands, while the cell it
        stepped *out of* still holds the harvester glyph an earlier pulse
        recorded there — and no later pulse can clear it, because the
        observer is dead. The owner was left looking at two copies of one
        harvester and no way to tell which was real. A disk-wide refresh
        is exactly the work the hourly pulse would have done had the
        probe survived the hour, so the echo stays one coherent instant.
        """
        bucket = self.probe_intel.get(probe.owner)
        if bucket is None or probe.x is None or probe.y is None:
            return
        for tx, ty in _probe_vision_disk(
            probe.x, probe.y, self.width, self.height,
        ):
            bucket[_xy_key(tx, ty)] = self._probe_tile_snapshot(
                tx, ty, exclude_entity_id=probe.id,
            )

    def _forget_probe_marks_at(
        self, x: int, y: int, probe_ids: Sequence[str],
    ) -> None:
        """Erase dead probes from every House's picture of one square.

        v1.19 (bug #19) — a probe mark left standing on a square with no
        probe on it. §3.15 publishes a launch to every other House, so the
        moment a probe lands, each opponent's echo carries it. Nothing ever
        took it back off: the §3.16 death paths destroyed the entity and
        left the marks, and the scheduled sweep that eventually clears them
        (:meth:`_expire_stale_launch_markers`) is a three-night timer. So
        for up to three nights a House could sit looking at a probe that
        annihilated in front of it.

        That timer is the right answer for a probe killed *out of sight* —
        you watched it land, you did not watch it die. It is the wrong
        answer here, because a §3.16 contest is exactly as public as the
        launches that caused it: two streaks converge on one square and
        burst. Every House that was told about the launch is told about the
        crash, so the mark goes now rather than in three nights' time.

        Deliberately narrow: only the named probes, only this square, and
        no fresh snapshot of anything. A House learns that a probe it had
        already been told about is gone — never anything it hadn't earned.
        """
        ids = {str(pid) for pid in probe_ids}
        key = _xy_key(x, y)
        for bucket in self.probe_intel.values():
            entry = bucket.get(key)
            if not isinstance(entry, dict):
                continue
            occ = [o for o in (entry.get("occupants") or []) if isinstance(o, dict)]
            hits = [o for o in occ if str(o.get("id")) in ids]
            glyph = entry.get("probe_launch_glyph")
            glyph_hit = (
                str(glyph.get("probe_id")) in ids
                if isinstance(glyph, dict) else False
            )
            if not hits and not glyph_hit:
                continue
            if entry.get("via") == "probe_launch" and len(hits) == len(occ):
                # A bare §3.15 marker: the entry exists only to say "a probe
                # landed here". With the probe gone there is nothing left for
                # it to report, so retire it whole and let the square fall
                # back to fog or to whatever older terrain memory it had.
                bucket.pop(key, None)
                continue
            for o in hits:
                self._strip_launch_overlay(entry, str(o.get("id")))
            if glyph_hit and isinstance(glyph, dict):
                self._strip_launch_overlay(entry, str(glyph.get("probe_id")))
            if not entry.get("occupants"):
                # The top-entity glyph was one of the probes we just removed;
                # leaving it would put the mark straight back on the square.
                entry["glyph_ch"] = None
                entry["glyph_fg"] = None

    def _record_provisional_supersede(
        self,
        x: int,
        y: int,
        stamp: Tuple[int, Optional[int]],
        victim_id: str,
        victim_owner: str,
        killer: str,
    ) -> None:
        """Note that ``victim_id`` was taken as a supersede on this stamp.

        v1.15 — provisional, because a supersede only stands if the seat
        that took the cell actually *holds* it when the hour closes. Seats
        resolve one at a time, so the first arrival cannot yet know whether
        a second probe is inbound on the same stamp; if one is, §3.16 says
        nobody held the cell and this death was a collision casualty all
        along. :meth:`_finalise_probe_contest` re-files it.
        """
        book = getattr(self, "_probe_supersedes_this_stamp", None)
        if book is None:
            book = {}
            self._probe_supersedes_this_stamp = book  # type: ignore[attr-defined]
        book.setdefault((int(x), int(y), stamp), []).append(
            (str(victim_id), str(victim_owner), str(killer))
        )

    def _finalise_probe_contest(
        self, x: int, y: int, stamp: Tuple[int, Optional[int]],
    ) -> None:
        """Re-file this stamp's supersedes on (x,y) as collision casualties.

        v1.15 — called when a second probe lands on the cell on the same
        ``(day, hour)``, which by §3.16 makes the whole hour one mutual
        annihilation: every probe on the square dies and no House takes
        the cell, so no House earns a supersede either. Without this the
        incumbent's ledger row and the ``probes_superseded`` kill-feed
        credit recorded whichever seat the resolver happened to reach
        first — a seat that lost its own probe in the same instant.
        """
        book = getattr(self, "_probe_supersedes_this_stamp", None) or {}
        for victim_id, victim_owner, killer in book.pop(
            (int(x), int(y), stamp), [],
        ):
            rec = self.asset_records.get(victim_id)
            if rec is not None:
                # Written directly: ``_note_asset_destroyed`` only fills a
                # blank row, and this one is already stamped.
                rec.destroyed_by = "probe_collision"
            stat_m = self.combat_attrib.get("probes_superseded") or {}
            by_victim = stat_m.get(killer)
            if by_victim:
                left = int(by_victim.get(victim_owner, 0)) - 1
                if left > 0:
                    by_victim[victim_owner] = left
                else:
                    by_victim.pop(victim_owner, None)
                # Drop the emptied attacker row rather than leaving a 0-hit
                # shell behind: the kill feed hides those, but the season
                # stats blob is also read raw by evals and the agent view.
                if not by_victim:
                    stat_m.pop(killer, None)

    def _queue_probe_death_fx(
        self,
        probe_id: str,
        probe_owner: str,
        x: int,
        y: int,
        *,
        reason: str,
        crusher_owner: Optional[str] = None,
    ) -> None:
        """Queue the pixel-splash record for a probe destroyed at (x,y).

        The simulator drains :attr:`pending_probe_crush_events` after every
        applied move and folds it onto that move's replay frame, so the
        watcher can splash the cell in the dead probe's seat colour.

        v1.19 — every way a probe can die on a cell now files here, not just
        the harvester crush this buffer was built for. The §3.16 paths
        (supersession, mutual annihilation, the collision crater) only wrote
        a log line, so a superseded probe simply blinked out of existence,
        and a mutual annihilation — where the arriving probe is created and
        destroyed inside one move and therefore never appears in a frame's
        entity snapshot — rendered *nothing at all*, streak included. Two
        seats could burn a probe each on the same cell and the board would
        not flicker.

        ``reason`` rides along so the UI can name the cause rather than
        calling every one of them a crush.
        """
        self.pending_probe_crush_events.append({
            "at": [int(x), int(y)],
            "probe_id": str(probe_id),
            "probe_owner": str(probe_owner),
            "crusher_owner": str(crusher_owner) if crusher_owner else None,
            "reason": str(reason),
        })

    def consume_probes_at(
        self, x: int, y: int, crusher_owner: Optional[str] = None,
    ) -> List[str]:
        """Destroy any probe(s) sitting on ``(x, y)``.

        Any harvester landing or stepping onto a probe crushes it,
        regardless of which House owns the probe. Returns op-log
        entries so callers can fold them into their replay captions.
        ``crusher_owner`` is the seat whose harvester rode over the
        probe (v1.6 kill-feed attribution); ``None`` on legacy callers.

        v0.7.4 — each crushed probe also pushes a structured event
        onto :attr:`pending_probe_crush_events`. The night simulator
        drains the buffer right after the call so the matching replay
        frame ships ``crushed_probes`` metadata and the frontend can
        fire a small pixel-splash animation on the crushed cell.
        """
        msgs: List[str] = []
        doomed = [
            (eid, en)
            for eid, en in self.entities.items()
            if en.entity_type == "probe" and en.x == x and en.y == y
        ]
        for eid, en in doomed:
            msgs.append(
                f"{en.owner}'s {eid} crushed under harvester at ({x},{y})"
            )
            self._note_asset_destroyed(
                eid, reason=f"crushed_by_harvester@({x},{y})"
            )
            self._queue_probe_death_fx(
                eid, en.owner, x, y,
                reason="crushed_by_harvester",
                crusher_owner=crusher_owner,
            )
            # v1.6 kill-feed: attribute the crush to the harvester's house.
            self._attrib("probes_crushed", crusher_owner, en.owner)
            del self.entities[eid]
        # v1.19 — freeze the final echoes only once every probe crushed in
        # this instant is off the board. Doing it inside the loop let the
        # first probe's last look record the second one, which was about
        # to die in the same step, leaving its owner an echo of a probe
        # that no longer exists.
        for _eid, en in doomed:
            self._freeze_final_probe_echo(en)
        if doomed:
            self._persist_asset_ledger()
        return msgs

    # ── v0.9.11 — Station observations (RULEBOOK §3.15.x) ───────────

    @staticmethod
    def _grade_fullness(count: int, capacity: int) -> str:
        if int(count) <= 0:
            return "empty"
        cap = max(1, int(capacity or 1))
        frac = float(count) / cap
        for threshold, label in STATION_FULLNESS_BANDS:
            if frac < threshold:
                return label
        return STATION_FULLNESS_FULL

    @staticmethod
    def _grade_purity(total: int) -> str:
        t = max(0, int(total))
        out = STATION_PURITY_HIGH
        for threshold, label in STATION_PURITY_BANDS:
            if t <= threshold:
                return label
        return out

    @staticmethod
    def _blue_pip_band(total: int) -> int:
        """Finer BLUE pip band (0..STATION_BLUE_PIP_MAX) at 150/pip."""
        t = max(0, int(total))
        return min(STATION_BLUE_PIP_MAX, t // STATION_BLUE_PIP_STEP)

    @staticmethod
    def _grade_green_count(count: int) -> str:
        c = max(0, int(count))
        for threshold, label in STATION_GREEN_COUNT_BANDS:
            if c <= threshold:
                return label
        return STATION_GREEN_COUNT_HIGH

    def _station_observation(
        self, player: PlayerId, *, fuzzy: bool,
    ) -> Dict[str, Any]:
        """Coarse orbital "reading" of a seat's platform (RULEBOOK §3.15.x).

        Every seat broadcasts a fog-of-war readout that any rival can
        pick up from orbit: how full the hold is, the fissile (BLUE)
        and toxic (GREEN) purity grades, and a fuzzy estimate of the
        green-parcel count. ``fuzzy=False`` (self) attaches the exact
        numbers alongside the grade; ``fuzzy=True`` (rivals) returns
        only the grade bands / count range so opponents never learn the
        precise vault contents — just the silhouette.

        The ``arms`` block is the exception and is exact for everyone
        (v1.34, §4.9.8). Omitted entirely when ``weapons_enabled`` is
        off, so a teaching game has no weapon vocabulary anywhere in the
        payload for the UI to render.
        """
        hoard = self.hoard_squares.get(player, []) or []
        count = len(hoard)
        capacity = HOARD_CAPACITY

        blue_total = self.blue_purity_available(player)

        green_parcels = [
            p for p in hoard
            if int(p.get("tile_at_harvest", p.get("origin_tile", -1)) or -1)
            == int(Tile.GREEN)
        ]
        green_total = sum(self._parcel_purity(p) for p in green_parcels)
        green_count = len(green_parcels)

        obs: Dict[str, Any] = {
            "seat": player,
            "fullness": {
                "grade": self._grade_fullness(count, capacity),
            },
            "blue": {
                "grade": self._grade_purity(blue_total),
                # Finer 150/pip band (0..5) surfaced to rivals + the UI so an
                # ~200-BLUE weapon spend is legible turn-over-turn.
                "band": self._blue_pip_band(blue_total),
            },
            "green": {
                "grade": self._grade_purity(green_total),
                "estimate": self._grade_green_count(green_count),
            },
        }
        # v1.34 — the arsenal is public (RULEBOOK §4.9.8). Deliberately
        # ABOVE the ``fuzzy`` gate below: everything up here is what any
        # rival can read from orbit, and ordnance is now part of that.
        # Weapons are the one thing on this platform whose whole point is
        # to be aimed at somebody else, so hiding the count made the
        # counter-play a guessing game rather than a decision.
        if self.weapons_enabled:
            obs["arms"] = {
                "blue": self.arsenal_blue(player),
                "cap": self.arsenal_cap(),
            }
        if not fuzzy:
            obs["fullness"].update({"count": count, "capacity": capacity})
            obs["blue"]["total"] = int(blue_total)
            obs["green"].update(
                {"total": int(green_total), "count": int(green_count)}
            )
        return obs

    def station_observation_snapshot(self) -> Dict[str, Dict[str, Any]]:
        """Exact station readings for every active seat (replay snapshot).

        Stored verbatim per day/phase so the replay scrubber can serve
        historical readings; per-viewer fuzzing happens client-side.
        """
        return {
            seat: self._station_observation(seat, fuzzy=False)
            for seat in self.players
        }

    @staticmethod
    def _empty_activity_tally() -> Dict[str, int]:
        # v0.9.12 — widened from {dropped, picked_up, picked_up_damaged}
        # to the full observable orbital silhouette. ``dropped`` /
        # ``picked_up`` / ``picked_up_damaged`` are retained as aliases so
        # any legacy consumer keeps working while the recap reads the new
        # split (recovered + recovered_carrying + recovered_damaged).
        return {
            "probes": 0,
            "dropped": 0,
            "recovered": 0,
            "recovered_carrying": 0,
            "recovered_damaged": 0,
            "mines": 0,
            "emps": 0,
            "chaff": 0,
            # v1.38 — SNAP shipped in v1.36 with no counter here, so a
            # seat could fire one every night and its public orbital
            # silhouette stayed flat. That is a fog leak in the
            # generous direction: the strike itself is public (§4.9.4),
            # so withholding the count only cost agents an inference a
            # human reads straight off the board.
            "snaps": 0,
            # v0.9.15 — harvester-loss / field-state counts so the
            # count-fallback recap path (``eventsFromActivityTally``)
            # renders abandoned / damaged / emp'd lines, not just
            # launches & recoveries.
            "abandoned": 0,
            "abandoned_emped": 0,
            "damaged": 0,
            "emped": 0,
            # ── legacy aliases (kept in sync below) ──
            "picked_up": 0,
            "picked_up_damaged": 0,
        }

    def tally_orbital_activity(
        self, frames: Sequence[Mapping[str, Any]],
    ) -> Dict[str, Dict[str, int]]:
        """Count per-seat observable orbital activity from a night's frames.

        RULEBOOK §3.15.x — a platform's launches and recoveries are
        public: any seat can observe HOW MANY probes / harvesters a rival
        sent down, how many came back (and whether carrying cargo or
        damaged), and the interdiction weapons it fired (mines / EMPs /
        chaff). Landing COORDINATES stay private (never recorded here).
        Derived purely from replay frame tags + captions so it can be
        recomputed for any night.
        """
        out: Dict[str, Dict[str, int]] = {}
        for f in frames or []:
            owner = f.get("owner")
            if not owner:
                continue
            tag = f.get("tag")
            caption = str(f.get("caption") or "")
            if tag == "probe":
                out.setdefault(owner, self._empty_activity_tally())["probes"] += 1
            elif tag == "drop":
                out.setdefault(owner, self._empty_activity_tally())["dropped"] += 1
            elif tag == "pickup":
                t = out.setdefault(owner, self._empty_activity_tally())
                t["recovered"] += 1
                t["picked_up"] += 1  # legacy alias
                # v0.9.18: try_pickup_unit stamps "DAMAGED — requires REPAIR"
                # when the recovered harvester was damaged. Pickup no longer
                # repairs; the unit returns to orbit still damaged.
                if "DAMAGED — requires REPAIR" in caption:
                    t["recovered_damaged"] += 1
                    t["picked_up_damaged"] += 1  # legacy alias
                # The caption reads "banked N parcel(s)"; N > 0 means the
                # recovered harvester came home loaded with red cargo.
                m = re.search(r"banked\s+(\d+)\s+parcel", caption)
                if m and int(m.group(1)) > 0:
                    t["recovered_carrying"] += 1
            # v1.31 — KEPT ON PURPOSE. No new frame carries this tag, but
            # this walks stored frames, so an archived season's scoreboard
            # still reports the mines that were laid in it.
            elif tag == "mine_lay":
                out.setdefault(owner, self._empty_activity_tally())["mines"] += 1
            elif tag == "emp_launch":
                out.setdefault(owner, self._empty_activity_tally())["emps"] += 1
            elif tag == "chaff_flare":
                out.setdefault(owner, self._empty_activity_tally())["chaff"] += 1
            elif tag == "snap_launch":
                out.setdefault(owner, self._empty_activity_tally())["snaps"] += 1

        # v0.9.15 — fold in field-state losses so the count-fallback recap
        # (``eventsFromActivityTally``) renders the same abandoned /
        # damaged lines as the ordered event log. Emp'd & damaged are
        # deduped per harvester id (a unit emp'd for 5 hours is ONE emp'd
        # harvester). Mirrors the survivor logic in ``tally_orbital_events``.
        emped_ids: Dict[str, Set[str]] = {}
        damaged_ids: Dict[str, Set[str]] = {}
        picked_up_damaged_ids: Dict[str, Set[str]] = {}
        for f in frames or []:
            owner = f.get("owner")
            if not owner:
                continue
            tag = f.get("tag")
            caption = str(f.get("caption") or "")
            if tag == "empd":
                m = re.search(r"(harvester_[A-Za-z0-9_]+)", caption)
                if m:
                    emped_ids.setdefault(str(owner), set()).add(m.group(1))
            elif tag == "damaged":
                m = re.search(r"(harvester_[A-Za-z0-9_]+)", caption)
                if m:
                    damaged_ids.setdefault(str(owner), set()).add(m.group(1))
            elif tag == "pickup" and "DAMAGED — requires REPAIR" in caption:
                m = re.search(r"(harvester_[A-Za-z0-9_]+)", caption)
                if m:
                    picked_up_damaged_ids.setdefault(str(owner), set()).add(m.group(1))

        abandoned_ids: Dict[str, Set[str]] = {}
        for marker in (self.destroyed_harvester_markers or {}).values():
            try:
                if int(marker.get("day", -1)) != int(self.day):
                    continue
            except (TypeError, ValueError):
                continue
            owner = marker.get("owner")
            if not owner:
                continue
            owner = str(owner)
            hid = str(marker.get("harvester_id") or "")
            t = out.setdefault(owner, self._empty_activity_tally())
            t["abandoned"] += 1
            if hid:
                abandoned_ids.setdefault(owner, set()).add(hid)
                if hid in (emped_ids.get(owner) or set()):
                    t["abandoned_emped"] += 1

        # Survivor damaged harvesters (still on surface at dawn, not picked
        # up and not stranded). v0.9.18: pickup no longer repairs, but picked
        # up harvesters are orbital now (don't appear in this surface list).
        # ``emped`` counts how many were also emp'd this night.
        for owner, units in damaged_ids.items():
            picked_up = picked_up_damaged_ids.get(owner) or set()
            lost = abandoned_ids.get(owner) or set()
            emped = emped_ids.get(owner) or set()
            t = out.setdefault(owner, self._empty_activity_tally())
            for uid in units:
                if uid in picked_up or uid in lost:
                    continue
                t["damaged"] += 1
                if uid in emped:
                    t["emped"] += 1
        return out

    #: Tags that count as observable orbital actions (public silhouette).
    #: ``mine_lay`` is retired (v1.31) but stays listed — this classifies
    #: stored frames, so dropping it would silently blank the orbital
    #: silhouette of every archived season that used caltrops.
    _ORBITAL_EVENT_TAGS = (
        "probe", "drop", "pickup", "mine_lay", "emp_launch", "chaff_flare",
        "snap_launch",
    )

    def tally_orbital_events(
        self, frames: Sequence[Mapping[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Ordered per-seat orbital EVENT log (parallel to
        :meth:`tally_orbital_activity`).

        Returns ``{seat: [{"tag", "carrying"?, "damaged"?, "emped"?},
        ..., {"tag": "abandoned"}]}`` in witnessed order. Same
        public-silhouette contract as the count tally — only the action
        kind + cargo/damaged/emp'd flags are recorded; landing
        COORDINATES are never included. A recovered harvester that was
        EMP-disabled tonight is flagged ``emped``; a harvester left on the
        surface at dawn (destroyed) is appended as an ``abandoned`` event.
        Stored in the session blob so the Pre-Orbital Recap can render the
        chronological list without round-tripping the replay-frame table.
        """
        # Pre-scan the night's non-action frames to learn which harvesters
        # each platform had EMP-disabled (tag="empd") or field-damaged
        # (tag="damaged") tonight. Both tags sit OUTSIDE the observable
        # action set, but the state they leave behind is a public
        # silhouette — a recovered / abandoned / orbiting harvester can be
        # tinted "emp'ed" / "damaged" in the recap even when no clean
        # pickup carried the flag. ``recovered_damaged`` tracks harvesters
        # that were picked up while damaged (v0.9.18: pickup no longer
        # repairs, so they stay damaged in orbit) — these don't appear in
        # the "still damaged" survivors list since they're orbital now.
        emped_units: Dict[str, Set[str]] = {}
        damaged_units: Dict[str, Set[str]] = {}
        recovered_damaged: Dict[str, Set[str]] = {}
        for f in frames or []:
            owner = f.get("owner")
            if not owner:
                continue
            tag = f.get("tag")
            caption = str(f.get("caption") or "")
            if tag == "empd":
                m = re.search(r"([A-Za-z0-9_]+)\s+disabled by EMP cloud", caption)
                if m:
                    emped_units.setdefault(str(owner), set()).add(m.group(1))
            elif tag == "damaged":
                m = re.search(r"(harvester_[A-Za-z0-9_]+)", caption)
                if m:
                    damaged_units.setdefault(str(owner), set()).add(m.group(1))
            elif tag == "pickup" and "DAMAGED — requires REPAIR" in caption:
                m = re.search(r"(harvester_[A-Za-z0-9_]+)", caption)
                if m:
                    recovered_damaged.setdefault(str(owner), set()).add(m.group(1))

        out: Dict[str, List[Dict[str, Any]]] = {}
        for f in frames or []:
            owner = f.get("owner")
            if not owner:
                continue
            tag = f.get("tag")
            if tag not in self._ORBITAL_EVENT_TAGS:
                continue
            caption = str(f.get("caption") or "")
            ev: Dict[str, Any] = {"tag": tag}
            if tag == "pickup":
                # v0.9.18: pickup no longer repairs, just extracts. Mark
                # damaged=True when caption warns repair is still needed.
                if "DAMAGED — requires REPAIR" in caption:
                    ev["damaged"] = True
                m = re.search(r"banked\s+(\d+)\s+parcel", caption)
                if m and int(m.group(1)) > 0:
                    ev["carrying"] = True
                units = emped_units.get(str(owner)) or set()
                if any(uid in caption for uid in units):
                    ev["emped"] = True
            out.setdefault(owner, []).append(ev)

        # Dawn-stranded ("abandoned on the surface") harvesters destroyed
        # THIS night. Sourced from the per-position gravestone markers
        # stamped by ``dawn_strand_harvesters`` (which ran earlier this
        # tick), filtered to the current day so only tonight's losses
        # surface. Public silhouette — every platform's losses are
        # observable from orbit (the harvester count visibly drops). A
        # harvester that was emp'd / damaged before being stranded carries
        # that flag onto the abandoned line.
        abandoned_units: Dict[str, Set[str]] = {}
        for marker in (self.destroyed_harvester_markers or {}).values():
            try:
                if int(marker.get("day", -1)) != int(self.day):
                    continue
            except (TypeError, ValueError):
                continue
            owner = marker.get("owner")
            if not owner:
                continue
            owner = str(owner)
            hid = str(marker.get("harvester_id") or "")
            ev = {"tag": "abandoned"}
            if hid:
                abandoned_units.setdefault(owner, set()).add(hid)
                if hid in (emped_units.get(owner) or set()):
                    ev["emped"] = True
                if hid in (damaged_units.get(owner) or set()):
                    ev["damaged"] = True
            out.setdefault(owner, []).append(ev)

        # Survivors: harvesters that ended the night still field-damaged
        # and remain on the surface (not picked up, not stranded). A
        # harvester that was picked up while damaged is orbital now (so it
        # doesn't show in this "still on surface" list), but it DID produce
        # a pickup event already marked damaged=True.
        for owner, units in damaged_units.items():
            picked_up_damaged = recovered_damaged.get(owner) or set()
            lost = abandoned_units.get(owner) or set()
            emped = emped_units.get(owner) or set()
            for uid in sorted(units):
                if uid in picked_up_damaged or uid in lost:
                    continue
                ev = {"tag": "damaged"}
                if uid in emped:
                    ev["emped"] = True
                out.setdefault(owner, []).append(ev)

        return out

    # ── v0.9 — Weapons economy + apply helpers ──────────────────────

    def blue_purity_available(self, player: PlayerId) -> int:
        """Sum of BLUE-parcel purities currently held in the vault.

        The v0.9 weapons economy is paid in *blue purity* (sum of
        parcel purities, lowest-first). This helper is the readout
        the UI surfaces next to "credits available" and the engine
        guard before debiting at launch time.

        v0.9.2 bugfix — read via :meth:`_parcel_purity` (which
        understands ``purity_at_harvest`` / ``origin_purity`` /
        ``purity``) so the count actually reflects parcels in the
        hoard. The earlier ``r.get("purity", 0)`` returned 0 for
        every parcel because the canonical key is ``purity_at_harvest``,
        which is why weapons couldn't be afforded.
        """
        total = int(self.blue_bank.get(player, 0))
        for r in self.hoard_squares.get(player, []) or []:
            tile = r.get("tile_at_harvest") or r.get("origin_tile")
            try:
                tile_int = int(tile)
            except (TypeError, ValueError):
                continue
            if tile_int != int(Tile.BLUE):
                continue
            total += self._parcel_purity(r)
        return total

    def debit_blue_purity(
        self, player: PlayerId, cost: int,
    ) -> Tuple[bool, List[str], int]:
        """Auto-consume BLUE parcels lowest-purity-first to pay ``cost``.

        Returns ``(ok, consumed_square_ids, waste_purity)``. On
        failure (insufficient blue) nothing is debited and the
        consumed list is empty.

        v0.9.5 — overpay refund. Pre-v0.9.5 the last consumed parcel
        contributed its FULL purity even when the seat only owed a
        fraction (the excess was "wasted"). With the §4 weapons
        economy charging single-parcel-sized batches (100b for a
        mine, 200b for an EMP) and the hoard usually containing
        ``sink`` (255p) parcels, that meant a single 100-blue mine
        build was nuking 255 purity from the vault. Now, when the
        last consumed parcel over-pays, we mint a residual parcel
        for the leftover and tag it with the same lineage so the
        vault stays in sync with the actual cost. The legacy
        ``waste_purity`` return is preserved as 0 so callers that
        log the value still work; the new ``residual_minted``
        information is available in the engine log.

        The hoard list is rewritten in place so callers don't have
        to re-sync indices.
        """
        if cost <= 0:
            return True, [], 0
        # v0.9.x — spend the BLUE bank first (the fissile stipend / clean
        # numeric surface), then fall back to consuming vault BLUE parcels
        # lowest-purity-first for any remainder.
        bank = int(self.blue_bank.get(player, 0))
        bucket = self.hoard_squares.get(player, []) or []
        blue_entries: List[Tuple[int, int, dict]] = []
        for ix, r in enumerate(bucket):
            tile = r.get("tile_at_harvest") or r.get("origin_tile")
            try:
                if int(tile) != int(Tile.BLUE):
                    continue
            except (TypeError, ValueError):
                continue
            purity = self._parcel_purity(r)
            blue_entries.append((purity, ix, r))
        blue_entries.sort(key=lambda t: (t[0], t[1]))
        parcel_avail = sum(p for p, _, _ in blue_entries)
        if bank + parcel_avail < cost:
            return False, [], 0
        # Affordable from here on — tally the full debit for the
        # end-of-game "blue used" readout.
        self._bump_stat(player, "blue_spent", cost)
        bank_spent = min(bank, cost)
        if bank_spent > 0:
            self.blue_bank[player] = bank - bank_spent
        remaining = cost - bank_spent
        if remaining <= 0:
            return True, [], 0
        consumed_ids: List[str] = []
        accum = 0
        drop_indices: List[int] = []
        last_parcel: Optional[Dict[str, Any]] = None
        last_purity = 0
        for purity, ix, parcel in blue_entries:
            if accum >= remaining:
                break
            consumed_ids.append(str(parcel.get("square_id") or parcel.get("site_id") or f"@{ix}"))
            drop_indices.append(ix)
            accum += purity
            last_parcel = parcel
            last_purity = purity
        # v0.9.5 — refund the over-pay as a residual parcel inheriting
        # the last consumed parcel's lineage. If the seat owed less
        # than one parcel's worth (overpay > 0), the leftover purity
        # is minted back as a single new BLUE parcel.
        overpay = max(0, accum - remaining)
        residual_minted: Optional[Dict[str, Any]] = None
        if overpay > 0 and last_parcel is not None:
            new_uid = self.square_uid_seq + 1
            self.square_uid_seq = new_uid
            tier_tag = self._tier_for_purity(int(last_purity)) or "blue"
            new_sid = (
                f"residual-{tier_tag}-{self.session_id[:6]}-{new_uid:04d}"
            )
            residual_minted = {
                "square_id": new_sid,
                "site_id": new_sid,
                "owner": str(player),
                "origin_x": last_parcel.get("origin_x", last_parcel.get("x")),
                "origin_y": last_parcel.get("origin_y", last_parcel.get("y")),
                "origin_tile": int(Tile.BLUE),
                "origin_purity": int(overpay),
                "purity_at_harvest": int(overpay),
                "harvested_day": int(self.day),
                "harvested_on_planning_day": int(self.day),
                "tile_at_harvest": int(Tile.BLUE),
                "lineage": "blue_residual",
                "refined_from": [
                    str(
                        last_parcel.get("square_id")
                        or last_parcel.get("site_id")
                        or ""
                    )
                ],
            }
        for ix in sorted(drop_indices, reverse=True):
            del bucket[ix]
        if residual_minted is not None:
            bucket.append(residual_minted)
        self.hoard_squares[player] = bucket
        # The ``waste`` return slot is now always 0 — overpay is
        # refunded rather than burned. Legacy callers that printed
        # the value still log a clean "(blue waste 0)" tail.
        return True, consumed_ids, 0

    # ── EMP clouds ──────────────────────────────────────────────────

    def apply_emp_launch(
        self,
        player: PlayerId,
        x: int,
        y: int,
        *,
        hour: int,
        extra_targets: Optional[List[Tuple[int, int]]] = None,
    ) -> Tuple[bool, str]:
        """Fire an orbital EMP salvo from the stockpile.

        v0.9.x — a single launch fires
        :data:`EMP_MISSILES_PER_LAUNCH` simultaneous missiles. ``(x, y)``
        is the primary target; ``extra_targets`` carries the rest (up to
        the salvo size). Each in-bounds target spawns its own Manhattan
        radius-:data:`EMP_RADIUS` cloud at the launch hour. One launch
        drains exactly one EMP from ``weapon_stock[player]["emp"]``
        regardless of how many missiles it carries.

        Beyond disabling harvesters, the salvo immediately **destroys**
        any PROBE and **neutralizes** any MINE caught in the union of the
        new clouds' cells (friendly fire on). The cost-debit lives in the
        ``Build*Action`` orbit path; this only drains stock.

        SHIPPED tuning constants are snapshotted into each cloud so
        mid-night balance tweaks don't retroactively change radius /
        lifetime.
        """
        from sea_of_colours.game.weapons import (
            EMP_RADIUS,
            EMP_CLOUD_HOURS,
            EMP_MISSILES_PER_LAUNCH,
        )

        if not self.weapons_enabled:
            return False, (
                f"{player}: emp_launch refused — weapons are disabled in "
                "this game (teaching mode)"
            )

        # Collect the salvo's target cells: primary first, then extras,
        # de-duplicated and bounds-filtered, capped at the salvo size.
        raw_targets: List[Tuple[int, int]] = [(int(x), int(y))]
        for t in (extra_targets or []):
            try:
                raw_targets.append((int(t[0]), int(t[1])))
            except (TypeError, ValueError, IndexError):
                continue
        targets: List[Tuple[int, int]] = []
        seen: Set[Tuple[int, int]] = set()
        for tx, ty in raw_targets:
            if (tx, ty) in seen:
                continue
            seen.add((tx, ty))
            if 0 <= tx < self.width and 0 <= ty < self.height:
                targets.append((tx, ty))
            if len(targets) >= int(EMP_MISSILES_PER_LAUNCH):
                break
        if not targets:
            return False, f"emp_launch: ({x},{y}) out of bounds"

        slot = self._ensure_weapon_stock_slot(player)
        if int(slot.get("emp", 0)) <= 0:
            return False, (
                "emp_launch: no EMP in stockpile — build one in the "
                "next Orbit phase before firing"
            )
        slot["emp"] = int(slot.get("emp", 0)) - 1
        # v0.9.5 — lifetime "weapons used" counter (drives the VAULT
        # "USED" bay). Bumped here, AFTER the stock drain succeeded.
        self._bump_weapons_used(player, "emp")

        cloud_cells: Set[Tuple[int, int]] = set()
        for tx, ty in targets:
            self.emp_clouds.append({
                "owner": str(player),
                "cx": int(tx),
                "cy": int(ty),
                "radius": int(EMP_RADIUS),
                "hours_remaining": int(EMP_CLOUD_HOURS),
                "launched_at_hour": int(hour),
                "launched_at_day": int(self.day),
            })
            cloud_cells |= _manhattan_disk(
                int(tx), int(ty), int(EMP_RADIUS), self.width, self.height,
            )

        # v1.8 — record the public combat-feed event + decaying map scar
        # so agents / the map can see WHERE an EMP was active and for
        # which hours, long after the cloud itself dissipates (bug/combat
        # surface). Recorded here at the single salvo-spawn point.
        self.record_emp_artifacts(
            str(player), targets, int(EMP_RADIUS), cloud_cells,
            hour=int(hour), cloud_hours=int(EMP_CLOUD_HOURS),
        )

        # Cross-system kill: probes destroyed, mines neutralized in the
        # blast (friendly fire on).
        destroyed_probes = self._emp_sweep_destroy(
            cloud_cells, owner=str(player), hour=int(hour),
        )

        self.pending_emp_events.append({
            "kind": "emp_launch",
            "owner": str(player),
            "at": [int(targets[0][0]), int(targets[0][1])],
            "targets": [[int(tx), int(ty)] for tx, ty in targets],
            "radius": int(EMP_RADIUS),
            "missiles": len(targets),
            "hours_remaining": int(EMP_CLOUD_HOURS),
            "launched_at_hour": int(hour),
            # v0.9.3 — stock-drain rather than live cost. Surfaced
            # here so the replay frame keeps a per-event accounting
            # entry even though the resource debit happened during
            # the previous orbit phase.
            "from_stockpile": True,
            "stock_remaining": int(slot["emp"]),
            "destroyed_probes": destroyed_probes,
        })
        salvo = (
            f"{len(targets)} missile(s) → "
            + ", ".join(f"({tx},{ty})" for tx, ty in targets)
        )
        extra = ""
        if destroyed_probes:
            extra = f"; {len(destroyed_probes)} probe(s) fried"
        return True, (
            f"{player} fired EMP salvo: {salvo}; clouds r={EMP_RADIUS} "
            f"for {EMP_CLOUD_HOURS}h{extra} (stock {slot['emp']} EMP left)"
        )

    def _emp_sweep_destroy(
        self,
        cells: Set[Tuple[int, int]],
        *,
        owner: str,
        hour: int,
        stat: str = "emp_probes",
    ) -> List[Dict[str, Any]]:
        """Destroy probes inside ``cells``.

        Friendly fire is on — units are destroyed regardless of owner.
        Returns the destroyed-probe event payloads for the launch/tick
        replay frame. ``owner`` is the EMP's owner (for the event
        caption); ``hour`` is the resolve hour.

        ``stat`` is the kill-feed row the destroyed probes are credited
        to, and it exists because **the SNAP calls this too** (v1.50).
        It used to credit ``emp_probes`` unconditionally, so a beacon
        shot off the board by a SNAP was reported as an EMP kill — and
        an agent that had never bought an EMP could finish a season
        with "3 probes EMP'd" against it. That is the same mistake
        ``snap_harvesters`` was split from ``harv_damaged`` to avoid
        (§4.10): a shot asset must never read as something else's
        casualty. Default stays ``emp_probes`` so the two EMP callers
        are unchanged.

        v1.31 — used to neutralise caltrop mines in the blast too, and
        returned a ``(probes, mines)`` pair. Retired with the weapon.
        """
        if not cells:
            return []
        destroyed_probes: List[Dict[str, Any]] = []
        for eid, en in list(self.entities.items()):
            if en.entity_type != "probe":
                continue
            if en.x is None or en.y is None:
                continue
            if (int(en.x), int(en.y)) in cells:
                destroyed_probes.append({
                    "probe_id": str(eid),
                    "owner": str(en.owner),
                    "at": [int(en.x), int(en.y)],
                })
                # v1.6 kill-feed: the shooter fried this probe's house.
                # v1.50 — which weapon did it is the caller's to say.
                self._attrib(stat, owner, en.owner)
                self._note_asset_destroyed(eid, reason=f"emp@hour{hour}")
                self._clear_probe_launch_markers(eid, int(en.x), int(en.y))
                del self.entities[eid]
        if destroyed_probes:
            self._persist_asset_ledger()
        return destroyed_probes

    def tick_emp_clouds(self) -> None:
        """Decrement every cloud's ``hours_remaining`` by 1 and prune.

        Called at the start of each hour by the simulator's
        :meth:`NightSimulator._pre_hour_phase`. Pruned clouds drop
        out of visibility immediately (no fade-out frame today —
        could be added as a CSS animation on disappearance). After
        decay, any probe/mine now sitting inside a still-active cloud
        is destroyed (catches units that moved/were laid into a
        standing cloud).
        """
        for c in self.emp_clouds:
            c["hours_remaining"] = max(0, int(c.get("hours_remaining", 0)) - 1)
        self.emp_clouds = [c for c in self.emp_clouds if int(c.get("hours_remaining", 0)) > 0]
        live_cells = self.cells_in_any_emp_cloud()
        if not live_cells:
            return
        destroyed_probes = self._emp_sweep_destroy(
            live_cells, owner="emp_field", hour=-1,
        )
        if destroyed_probes:
            self.pending_emp_events.append({
                "kind": "emp_field_sweep",
                "owner": "emp_field",
                "destroyed_probes": destroyed_probes,
            })

    def cells_in_any_emp_cloud(self) -> Set[Tuple[int, int]]:
        """Union of all active EMP cloud cells.

        v0.9.4 — uses Manhattan distance (``|dx|+|dy| <= r``) so the
        AoE is a rhombus / diamond rather than a square. Both the
        disable check and the frontend overlay use this shape so
        gameplay and visuals stay in sync.
        """
        out: Set[Tuple[int, int]] = set()
        for c in self.emp_clouds:
            if int(c.get("hours_remaining", 0)) <= 0:
                continue
            cx, cy = int(c.get("cx", -1)), int(c.get("cy", -1))
            r = int(c.get("radius", 0))
            out |= _manhattan_disk(cx, cy, r, self.width, self.height)
        return out

    def emp_cloud_owners_at(self, x: int, y: int) -> Set[str]:
        """Owners of every active EMP cloud whose rhombus covers ``(x, y)``
        (v1.6 kill-feed attribution)."""
        owners: Set[str] = set()
        for c in self.emp_clouds:
            if int(c.get("hours_remaining", 0)) <= 0:
                continue
            cx, cy = int(c.get("cx", -1)), int(c.get("cy", -1))
            r = int(c.get("radius", 0))
            if abs(int(x) - cx) + abs(int(y) - cy) <= r:
                owner = c.get("owner")
                if owner:
                    owners.add(str(owner))
        return owners

    def note_emp_catch(
        self, harvester_id: str, victim: str, attacker: str,
    ) -> None:
        """Attribute one DISTINCT EMP'd harvester (per attacker, per season)
        to ``attacker``'s kill-feed. Deduped via :attr:`emp_harv_seen` so a
        unit smothered for several hours / nights counts once per attacker."""
        if not harvester_id or not victim or not attacker:
            return
        key = f"{attacker}|{harvester_id}"
        if self.emp_harv_seen.get(key):
            return
        self.emp_harv_seen[key] = True
        self._attrib("emp_harvesters", attacker, victim)

    # ── SNAP (v1.36, RULEBOOK §4.9.4) ───────────────────────────────
    #
    # Deliberately a self-contained block, and the one thing to preserve
    # if this is ever edited: SNAP is the newest weapon and the most
    # likely to be withdrawn. Everything it owns is in here, in
    # ``snap_clouds`` / ``pending_snap_events``, and in
    # ``NightSimulator._snap_preempt_phase``. Retiring it should look
    # like the caltrop's retirement a few lines below — a deleted block
    # and a refund at load — not an archaeology exercise.

    def apply_snap_launch(
        self, player: PlayerId, x: int, y: int, *, hour: int,
    ) -> Tuple[bool, str]:
        """Fire one SNAP at ``(x, y)`` from the stockpile.

        Destroys any probe on the cell and damages any harvester
        standing there. Harvesters that ARRIVE during the hour are hit
        too, but not here — this method stamps the cell hot and
        ``try_step_unit`` / ``try_drop_unit`` do the second half, because
        an arrival that has not happened yet cannot be damaged by a
        function that runs before it.

        Friendly fire is on, exactly as it is for the salvo. A SNAP put
        down on your own beacon kills your own beacon.

        Called from the simulator's pre-empt phase ABOVE the hour-start
        vision snapshot, which is the entire weapon — see
        ``NightSimulator._snap_preempt_phase``. Firing it from anywhere
        else in the hour would leave it a worse EMP.
        """
        from sea_of_colours.game.weapons import (
            SNAP_CLOUD_HOURS,
            SNAP_RADIUS,
        )

        if not self.weapons_enabled:
            return False, (
                f"{player}: snap_launch refused — weapons are disabled in "
                "this game (teaching mode)"
            )
        tx, ty = int(x), int(y)
        if not (0 <= tx < self.width and 0 <= ty < self.height):
            return False, f"snap_launch: ({tx},{ty}) out of bounds"

        slot = self._ensure_weapon_stock_slot(player)
        if int(slot.get("snap", 0)) <= 0:
            return False, (
                "snap_launch: no SNAP in stockpile — build one in the "
                "next Orbit phase before firing"
            )
        slot["snap"] = int(slot.get("snap", 0)) - 1
        self._bump_weapons_used(player, "snap")

        self.snap_clouds.append({
            "owner": str(player),
            "cx": tx,
            "cy": ty,
            "radius": int(SNAP_RADIUS),
            "hours_remaining": int(SNAP_CLOUD_HOURS),
            "launched_at_hour": int(hour),
            "launched_at_day": int(self.day),
        })
        cells = _manhattan_disk(
            tx, ty, int(SNAP_RADIUS), self.width, self.height,
        )

        # Public first, so the strike is on the feed whether or not it
        # found anything — "they spent a SNAP on an empty square" is
        # intelligence too, and the scorch mark says it regardless.
        self.record_snap_strike(str(player), tx, ty, hour=int(hour))

        destroyed_probes = self._emp_sweep_destroy(
            cells, owner=str(player), hour=int(hour), stat="snap_probes",
        )
        damaged = self._snap_damage_harvesters_at(
            cells, by=str(player), hour=int(hour),
        )

        # Stamp the cell hot for the rest of the hour. A harvester that
        # walks or drops onto it later this hour is maimed on arrival —
        # which is what makes SNAP a square you can guard rather than a
        # unit you have to already have found.
        #
        # Carries the hour as well as the seat because the drop and step
        # paths have neither, and both have to file a ``snap_hit`` that
        # says WHEN — an agent reading "hour 0" cannot line the hit up
        # against the strike that caused it.
        hot = getattr(self, "_snap_hot_cells_this_hour", None)
        if hot is None:
            hot = {}
            self._snap_hot_cells_this_hour = hot  # type: ignore[attr-defined]
        for cell in cells:
            hot[cell] = {"by": str(player), "hour": int(hour)}

        self.pending_snap_events.append({
            "kind": "snap_launch",
            "owner": str(player),
            "at": [tx, ty],
            "targets": [[tx, ty]],
            "radius": int(SNAP_RADIUS),
            "missiles": 1,
            "hours_remaining": int(SNAP_CLOUD_HOURS),
            "launched_at_hour": int(hour),
            "from_stockpile": True,
            "stock_remaining": int(slot["snap"]),
            "destroyed_probes": destroyed_probes,
            "damaged_harvesters": damaged,
        })

        bits = []
        if destroyed_probes:
            bits.append(f"{len(destroyed_probes)} probe(s) fried")
        if damaged:
            bits.append(f"{len(damaged)} harvester(s) crippled")
        tail = f"; {', '.join(bits)}" if bits else "; nothing on the cell yet"
        return True, (
            f"{player} fired SNAP at ({tx},{ty}){tail} "
            f"(stock {slot['snap']} SNAP left)"
        )

    def _snap_damage_harvesters_at(
        self, cells: Set[Tuple[int, int]], *, by: str, hour: int,
    ) -> List[Dict[str, Any]]:
        """Cripple every harvester standing in ``cells``. Friendly fire on.

        Uses the ordinary ``damaged`` flag (§3.6.1) rather than a
        SNAP-specific state: a maimed harvester is a maimed harvester
        however it got that way, and inventing a second kind of broken
        would mean auditing every ``damaged`` check in the engine for
        which one it meant. The 500c repair applies.

        Only finds hulls ON the surface, which is why the drop path does
        its own damage rather than calling this: a landing refused at
        the door is still in orbit and this scan would walk straight
        past it.
        """
        out: List[Dict[str, Any]] = []
        if not cells:
            return out
        for ent in self.entities.values():
            if ent.entity_type != "harvester":
                continue
            if ent.x is None or ent.y is None:
                continue
            if (int(ent.x), int(ent.y)) not in cells:
                continue
            if bool(getattr(ent, "damaged", False)):
                continue  # already wreckage; don't double-count the kill feed
            spilled = self._damage_harvester(
                ent, by=by, category="snap_harvesters",
            )
            self.note_snap_hit(
                str(ent.id), str(ent.owner), by,
                hour=int(hour), outcome="crippled",
            )
            out.append({
                "harvester_id": str(ent.id),
                "owner": str(ent.owner),
                "at": [int(ent.x), int(ent.y)],
                "cargo_lost": int(spilled),
            })
        return out

    def tick_snap_clouds(self) -> None:
        """Age SNAP clouds by one hour and prune (v1.36).

        No standing-cloud sweep, unlike :meth:`tick_emp_clouds`. A SNAP
        is a moment, not a field: whatever was on the square when it
        landed is dealt with at launch, and whatever arrives later that
        hour is dealt with by the hot-cell stamp. By the time this tick
        would matter the cloud is already gone.
        """
        for c in self.snap_clouds:
            c["hours_remaining"] = max(0, int(c.get("hours_remaining", 0)) - 1)
        self.snap_clouds = [
            c for c in self.snap_clouds
            if int(c.get("hours_remaining", 0)) > 0
        ]

    def cells_in_any_snap_cloud(self) -> Set[Tuple[int, int]]:
        """Union of active SNAP cloud cells — for the map overlay only.

        Explicitly NOT folded into ``cells_in_any_emp_cloud``: a SNAP
        cloud does not smother anybody. It is the scorch mark left where
        one already went off.
        """
        out: Set[Tuple[int, int]] = set()
        for c in self.snap_clouds:
            if int(c.get("hours_remaining", 0)) <= 0:
                continue
            out |= _manhattan_disk(
                int(c.get("cx", -1)), int(c.get("cy", -1)),
                int(c.get("radius", 0)), self.width, self.height,
            )
        return out

    # ── Mines — RETIRED v1.31 ───────────────────────────────────────
    #
    # The caltrop subsystem lived here: _mine_key, _mine_cluster_cells,
    # apply_mine_lay, mine_at, mine_visible_to, detonate_mine_at, and
    # the probe-witness helper _tile_visible_to_probe that existed only
    # to decide who saw a lay. All removed together — with no way to
    # arm one, and existing caltrops cleared at load, every one of them
    # was unreachable.
    #
    # ``self.mines`` itself is KEPT (always empty) so the persisted
    # shape and the replay/FX path survive for archived seasons.

    # ── Chaff ───────────────────────────────────────────────────────

    def apply_chaff_flare(
        self, player: PlayerId, *, hour: int,
    ) -> Tuple[bool, str]:
        """Fire an orbital chaff flare from the stockpile.

        v0.9.3 — Drains one chaff from ``weapon_stock[player]["chaff"]``
        instead of debiting blue + credits. Returns success/failure;
        the simulator owns the per-hour "every other seat is chaffed"
        semantics, so this method only handles the stock drain and
        the replay record.
        """
        from sea_of_colours.game.weapons import CHAFF_DURATION_HOURS

        if not self.weapons_enabled:
            return False, (
                f"{player}: chaff_flare refused — weapons are disabled in "
                "this game (teaching mode)"
            )

        slot = self._ensure_weapon_stock_slot(player)
        if int(slot.get("chaff", 0)) <= 0:
            return False, (
                "chaff_flare: no chaff flare in stockpile — build one "
                "in the next Orbit phase before firing"
            )
        slot["chaff"] = int(slot.get("chaff", 0)) - 1
        # v0.9.5 — see :meth:`apply_emp_launch` / :meth:`apply_chaff_flare`
        # for the parallel bump.
        self._bump_weapons_used(player, "chaff")
        until_hour = int(hour) + int(CHAFF_DURATION_HOURS) - 1
        self.pending_chaff_events.append({
            "kind": "chaff_flare",
            "owner": str(player),
            "from_hour": int(hour),
            "until_hour": until_hour,
            "from_stockpile": True,
            "stock_remaining": int(slot["chaff"]),
        })
        return True, (
            f"{player} fired orbital chaff flare (hours "
            f"{hour}..{until_hour}); stock {slot['chaff']} chaff left"
        )

    def dawn_strand_harvesters(self) -> List[str]:
        """Dawn enforcement: the planet's surface destroys EVERYTHING except probes.

        Per RULEBOOK §3.11.2: any non-probe entity (harvesters today,
        plus any future surface-capable unit) still on the surface at
        sunrise is DESTROYED. The entity is removed from play, the
        asset ledger row is stamped with ``destroyed_on_day`` and
        ``destroyed_by = dawn_unrecovered@(x,y)``, and any cargo
        carried by the unit is spilled (never reaches the hoard).
        The row surfaces in the vault's DESTROYED bucket for the rest
        of the season. There is no repair path.

        Probes are explicitly exempt — they were ruled "fielded
        instrumentation" in §3.11.1 and persist across dawn; they are
        only destroyed when a harvester rides over them mid-night.

        Historical method name kept for back-compat (callers in
        ``simulator.py`` already use the new caption / log shape).
        """
        msgs: List[str] = []
        destroyed_ids: List[str] = []
        for ent in list(self.entities.values()):
            # Probes are the only surface-resident asset that survives
            # dawn (RULEBOOK §3.11.1).
            if ent.entity_type == "probe":
                continue
            if ent.x is None:
                continue
            owner_play = cast(PlayerId, ent.owner)
            spilled = len(getattr(ent, "cargo_squares", []) or [])
            last_xy = (ent.x, ent.y)
            if hasattr(ent, "cargo_squares"):
                ent.cargo_squares.clear()
            if hasattr(ent, "carrying_red"):
                ent.carrying_red = False
            # Record the destruction site BEFORE clearing the position so
            # the asset ledger preserves where the unit died.
            rec = self.asset_records.get(ent.id)
            if rec is not None:
                rec.last_seen_x = last_xy[0]
                rec.last_seen_y = last_xy[1]
            self._note_asset_destroyed(
                ent.id, reason=f"dawn_unrecovered@({last_xy[0]},{last_xy[1]})"
            )
            destroyed_ids.append(ent.id)
            # v0.9.10 — leave a permanent gravestone marker for destroyed
            # harvesters so the watcher can see where units were left.
            if ent.entity_type == "harvester":
                key = _xy_key(last_xy[0], last_xy[1])
                self.destroyed_harvester_markers[key] = {
                    "owner": str(ent.owner),
                    "harvester_id": str(ent.id),
                    "day": int(self.day),
                }
                # v1.6 kill-feed: if this unit's egress was chaff-cancelled
                # this night, blame the chaffer for the loss.
                attacker = self._chaff_egress_block.get(str(ent.id))
                if attacker:
                    self._attrib("harv_lost_chaff", attacker, str(ent.owner))
            type_label = ent.entity_type
            cargo_clause = (
                f"; {spilled} cargo square(s) spilled"
                if spilled
                else ""
            )
            msgs.append(
                f"{owner_play} Aurora destruction — {type_label} {ent.id} "
                f"unrecovered at ({last_xy[0]},{last_xy[1]}){cargo_clause}. "
                f"asset moved to destroyed log."
            )
        # Remove the destroyed entities from the live entity map so they
        # no longer appear in orbit / on the grid; the asset ledger still
        # carries the row with `destroyed_on_day` set.
        for eid in destroyed_ids:
            self.entities.pop(eid, None)
        if destroyed_ids:
            self._persist_asset_ledger()
        return msgs


def cast_player(pid: object, *, allowed: Optional[Sequence[str]] = None) -> PlayerId:
    """Validate ``pid`` against an allow-list of seat IDs.

    Defaults to the 4-seat universe ``p1..p4`` so a session that
    only carries 2 seats still accepts ``p3`` typed in from a stale
    URL — but the higher-level endpoints (``/policy``, ``/orbit``)
    pass ``allowed=sess.players`` to enforce a per-session check.
    Keeps the v0.8.x exception shape (``ValueError(pid)``) so
    existing exception handlers don't change.
    """
    candidates = tuple(allowed) if allowed is not None else DEFAULT_SEAT_IDS
    if not isinstance(pid, str) or pid not in candidates:
        raise ValueError(pid)
    return pid  # type: ignore[return-value]
