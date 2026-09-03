"""v11 Phase 1 — the VALUE PYRAMID: provenance-tagged value + force-surfaced grabs.

The seed-69 diagnostics showed v10 could SEE a pure and still offer no way to
take it: a LIVE pure not wrapped in a redsign pattern, or an ECHO pure one cell
from live, fell through every precondition-gated menu family and the turn banked
zero. The root cause is that "value" and "how you reach it" were computed
implicitly across five modules with no single, coherent model.

This module makes the top of the pyramid explicit and UNCONDITIONAL:

    IF YOU CAN SEE A PURE IN LIVE — grab it (SMASH).
    IF A PURE IS IN ECHO / on the edge of live AND walkable — walk in and grab it.
    IF the best thing you can see is LIVE MASS — chain it.

...regardless of redsign ownership or whether the seam machinery fired. Provenance
comes straight from the engine view (``red_tiles[].freshness``: ``fresh`` = LIVE,
``stale`` = ECHO) so there is no guessing.

Scope (Phase 1): only value the seat can actually SEE (LIVE + ECHO red, rich
LIVE blue). EXPECTED value (redsign fog, needs a probe to pin the exact cell) is
left to :mod:`.seam_control` (walk-in / probe-grab patterns) and the probe hints.
The grabs produced here are ``chain``-shaped (drop + contiguous walk) so the
existing deterministic packager compiles them with no new machinery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from sea_of_colours.orchestrator_2.harnesses.sagar_cursor._v7.probe_hints import (
    _covers,
    _grid_dims,
    _known_green_cells,
    _los_cells,
    _orbit_harvester_ids,
    _tier_name,
    _vision_disk,
    _visible_red,
)
from sea_of_colours.orchestrator_2.harnesses.sagar_cursor.seam_control import (
    _HOLD_CAP_STEPS,
    _NSEW_ORDER,
    _live_cells,
    _mass_tail,
    _walkin_from_live,
)

# Provenance tiers (engine truth from red_tiles[].freshness).
LIVE = "LIVE"
ECHO = "ECHO"
EXPECTED = "EXPECTED"  # redsign fog — not force-surfaced here (see seam_control)

# Value tiers mirror the engine purity bands (probe_hints._tier_name).
_PURE_MIN = 255
_MASS_MIN = 151
# A real vein, matching the engine band and chain_filter's own value floor. Only
# surfaced for a harvester that would otherwise sit in orbit — see the VEIN
# branch in ``force_surface_grabs`` for why the pyramid has to reach this low.
_VEIN_MIN = 51
# Rich blue worth a grab — matches the sanitizer's blue-loot floor so we never
# surface a blue the corrector would then reroute around.
_BLUE_GRAB_MIN = 192
# A juice chain counts as a "strong" red claim on a harvester when it banks at
# least this many red points. Blue is only surfaced when orbital asks for it OR
# the seat has MORE harvesters than strong red chains (a spare unit that would
# otherwise idle / gather low-yield red) — see ``force_surface_grabs``.
_STRONG_CHAIN_RED_MIN = 150
# Short mass halo to sweep after banking the pure (kept tight — the pure is the
# prize; a long tail exposes the unit to weapons). The thinker/packager can trim.
_SMASH_TAIL = 3


@dataclass
class ValueCandidate:
    """One value cell the seat can SEE, tagged with provenance + default action."""

    cell: Tuple[int, int]
    purity: int
    tier: str            # pure | mass | vein | trace
    colour: str          # RED | BLUE
    provenance: str      # LIVE | ECHO
    drop_legal: bool     # under live coverage AND not green → can drop NOW


@dataclass
class GrabSpec:
    """A force-surfaced, ready-to-compile grab (chain-shaped: drop + walk)."""

    action: str                       # SMASH | WALK_IN | GRAB_MASS | GRAB_BLUE
    target: Tuple[int, int]           # the pure/mass/blue cell we are taking
    drop_at: Tuple[int, int]          # where the harvester lands
    cells: List[Tuple[int, int]]      # ordered walk (ends on / past the target)
    tier: str
    provenance: str
    colour: str = "RED"
    purity: int = 0
    note: str = ""                    # why this grab is surfaced (e.g. blue gate)


# ── provenance helpers ──────────────────────────────────────────────────
def _red_by_provenance(
    agent_view: Mapping[str, Any],
) -> Tuple[Dict[Tuple[int, int], int], Dict[Tuple[int, int], int]]:
    """Split ``red_tiles`` into (LIVE, ECHO) purity maps by ``freshness``."""
    live: Dict[Tuple[int, int], int] = {}
    echo: Dict[Tuple[int, int], int] = {}
    for row in (agent_view.get("red_tiles") or []):
        if not isinstance(row, Mapping):
            continue
        try:
            x, y = int(row["x"]), int(row["y"])
            p = int(row.get("purity") or 0)
        except (TypeError, KeyError, ValueError):
            continue
        if p <= 0:
            continue
        if str(row.get("freshness") or "") == "fresh":
            live[(x, y)] = p
        else:
            echo[(x, y)] = p
    return live, echo


def _rich_blue(agent_view: Mapping[str, Any]) -> Dict[Tuple[int, int], int]:
    """LIVE blue tiles at/above the grab floor, keyed (x, y) -> purity."""
    out: Dict[Tuple[int, int], int] = {}
    for row in (agent_view.get("blue_tiles") or []):
        if not isinstance(row, Mapping):
            continue
        try:
            x, y = int(row["x"]), int(row["y"])
            p = int(row.get("purity") or 0)
        except (TypeError, KeyError, ValueError):
            continue
        if p >= _BLUE_GRAB_MIN:
            out[(x, y)] = p
    return out


def _all_blue(agent_view: Mapping[str, Any]) -> Dict[Tuple[int, int], int]:
    """Every visible blue tile keyed (x, y) -> purity, floor or not."""
    out: Dict[Tuple[int, int], int] = {}
    for row in (agent_view.get("blue_tiles") or []):
        if not isinstance(row, Mapping):
            continue
        try:
            out[(int(row["x"]), int(row["y"]))] = int(row.get("purity") or 0)
        except (TypeError, KeyError, ValueError):
            continue
    return out


def _blue_tail(
    agent_view: Mapping[str, Any],
    frm: Tuple[int, int],
    green: Set[Tuple[int, int]],
    width: int,
    height: int,
    used: Set[Tuple[int, int]],
    n: int,
) -> List[Tuple[int, int]]:
    """Extend a BLUE grab into adjacent blue worth the step — and no further.

    R2.1. This branch used to borrow ``_mass_tail``, which is built for the red
    halo that rings a pure: it ranks neighbours by RED purity and, finding none,
    blind-walks any non-green cell on the assumption the halo is there but
    unseen. On a fully-visible blue cluster that assumption is simply wrong, and
    the result was s69 night 3 — a blue grab at (36,20) padded east across two
    empty cells, which cost two hours and slid the night's real play into a chaff
    window.

    So: only step onto blue we can actually SEE, richest first, and stop the
    moment there is nothing adjacent worth taking. A blue grab that is one cell
    long is the correct answer to a one-cell deposit.
    """
    blue = _all_blue(agent_view)
    tail: List[Tuple[int, int]] = []
    cur = frm
    seen = set(used) | {frm}
    for _ in range(max(0, n)):
        nbrs = [
            c for c in ((cur[0] + dx, cur[1] + dy) for dx, dy in _NSEW_ORDER)
            if 0 <= c[0] < width and 0 <= c[1] < height
            and c not in green and c not in seen and blue.get(c, 0) > 0
        ]
        if not nbrs:
            break
        nbrs.sort(key=lambda c: (-blue[c], c[1], c[0]))
        cur = nbrs[0]
        tail.append(cur)
        seen.add(cur)
    return tail


def build_candidates(agent_view: Mapping[str, Any]) -> List[ValueCandidate]:
    """The provenance-tagged value pyramid for THIS view (observability + tests).

    Ranked richest-first, LIVE before ECHO within a tier. Pure > mass > vein for
    red; rich blue is appended below red (it is loot but red always wins a tie).
    """
    live, echo = _red_by_provenance(agent_view)
    green = _known_green_cells(agent_view)
    live_cells = _live_cells(agent_view)

    def _cand(cell, p, prov) -> ValueCandidate:
        return ValueCandidate(
            cell=cell, purity=p, tier=_tier_name(p), colour="RED",
            provenance=prov,
            drop_legal=(cell in live_cells and cell not in green),
        )

    cands: List[ValueCandidate] = [_cand(c, p, LIVE) for c, p in live.items()]
    cands += [_cand(c, p, ECHO) for c, p in echo.items()]
    cands.sort(key=lambda c: (0 if c.provenance == LIVE else 1, -c.purity, c.cell[1], c.cell[0]))

    for c, p in sorted(_rich_blue(agent_view).items(), key=lambda kv: -kv[1]):
        cands.append(ValueCandidate(
            cell=c, purity=p, tier="blue", colour="BLUE", provenance=LIVE,
            drop_legal=(c in live_cells and c not in green),
        ))
    return cands


# ── force-surfaced grabs ─────────────────────────────────────────────────
def force_surface_grabs(
    agent_view: Mapping[str, Any],
    *,
    existing_targets: Set[Tuple[int, int]] = frozenset(),
    max_pure: int = 2,
    max_mass: int = 1,
    max_vein: int = 2,
    max_blue: int = 1,
    blue_requested: bool = False,
    harvesters_alive: Optional[int] = None,
    strong_chain_count: int = 0,
) -> List[GrabSpec]:
    """Ready-to-compile grabs for any SEEN pure/mass/blue not already targeted.

    * PURE (LIVE or ECHO): SMASH if drop-legal now, else WALK_IN from the nearest
      live frontier if walkable. Not-walkable pures are skipped (a probe is
      needed — that is seam_control / probe-hint territory, Phase 2).
    * MASS (LIVE, drop-legal): a plain chain grab when a rich mass cell isn't
      already covered.
    * BLUE (LIVE, drop-legal, rich): a HIGH-YIELD BLUE grab, surfaced only when it
      would NOT steal a harvester from red — i.e. orbital asks for it
      (``blue_requested``) OR the seat has more harvesters than strong red chains
      (``harvesters_alive`` > ``strong_chain_count``), so a spare unit that would
      otherwise idle / gather low-yield red goes to blue instead.

    ``existing_targets`` are cells already targeted by the seam/hot-drop/chain
    menu; we skip any grab whose target OR drop cell collides so we never
    double-offer the same value.
    """
    width, height = _grid_dims(agent_view)
    green = _known_green_cells(agent_view)
    live_cells = _live_cells(agent_view)
    live, echo = _red_by_provenance(agent_view)
    covered: Set[Tuple[int, int]] = set(existing_targets)
    specs: List[GrabSpec] = []

    # 1. PURES — richest first, LIVE preferred over ECHO.
    pures = [(c, p, LIVE) for c, p in live.items() if p >= _PURE_MIN]
    pures += [(c, p, ECHO) for c, p in echo.items() if p >= _PURE_MIN]
    pures.sort(key=lambda t: (0 if t[2] == LIVE else 1, -t[1], t[0][1], t[0][0]))

    n_pure = 0
    for cell, p, prov in pures:
        if n_pure >= max_pure or cell in covered:
            continue
        if cell in live_cells and cell not in green:
            tail = _mass_tail(agent_view, cell, green, width, height, {cell}, _SMASH_TAIL)
            specs.append(GrabSpec(
                action="SMASH", target=cell, drop_at=cell, cells=tail,
                tier="pure", provenance=prov, purity=p,
            ))
            covered.add(cell)
            covered.update(tail)
            n_pure += 1
            continue
        wk = _walkin_from_live(
            agent_view, cell, green, width, height,
            avoid_drops=covered, max_steps=_HOLD_CAP_STEPS,
        )
        if wk is None:
            continue  # needs a probe — leave to seam_control / probe hints
        drop, path = wk
        tail = _mass_tail(
            agent_view, cell, green, width, height, set(path) | {drop}, 2,
        )
        specs.append(GrabSpec(
            action="WALK_IN", target=cell, drop_at=drop, cells=list(path) + tail,
            tier="pure", provenance=prov, purity=p,
        ))
        covered.add(drop)
        covered.add(cell)
        covered.update(tail)
        n_pure += 1

    # 2. Best MASS not already covered — LIVE first, then ECHO on foot.
    #
    # R2.9 — this branch used to be LIVE-only, and the echo hot-drop builder
    # below was PURE-only, so a mass cell we had seen and lost fell through
    # every surfacing path. OUT-OF-GRID went on listing it (correctly: it is
    # often the richest known ground on the board) while no id on the menu could
    # take it. s69 night 5 is the case — 578-710 points of echo mass at (19,13),
    # named in the prompt, unactable, and the night went to a 266-point chain.
    masses = sorted(
        ((c, p, LIVE) for c, p in live.items()
         if _MASS_MIN <= p < _PURE_MIN and c in live_cells
         and c not in green and c not in covered),
        key=lambda t: (-t[1], t[0][1], t[0][0]),
    )
    masses += sorted(
        ((c, p, ECHO) for c, p in echo.items()
         if _MASS_MIN <= p < _PURE_MIN and c not in green and c not in covered
         and c not in live),
        key=lambda t: (-t[1], t[0][1], t[0][0]),
    )
    n_mass = 0
    for cell, p, prov in masses:
        if n_mass >= max_mass or cell in covered:
            continue
        if cell in live_cells and cell not in green:
            tail = _mass_tail(
                agent_view, cell, green, width, height, {cell}, _HOLD_CAP_STEPS,
            )
            specs.append(GrabSpec(
                action="GRAB_MASS", target=cell, drop_at=cell, cells=tail,
                tier="mass", provenance=prov, purity=p,
            ))
            covered.add(cell)
            covered.update(tail)
            n_mass += 1
            continue
        # Out of live coverage: only the DROP needs vision, so land on the
        # nearest live frontier cell and walk in. No probe, same as a pure.
        wk = _walkin_from_live(
            agent_view, cell, green, width, height,
            avoid_drops=covered, max_steps=_HOLD_CAP_STEPS,
        )
        if wk is None:
            continue  # needs a probe — the echo hot-drop builder picks it up
        drop, path = wk
        specs.append(GrabSpec(
            action="WALK_IN", target=cell, drop_at=drop, cells=list(path),
            tier="mass", provenance=prov, purity=p,
        ))
        covered.add(drop)
        covered.add(cell)
        covered.update(path)
        n_mass += 1

    spare_harvester = (
        harvesters_alive is not None and harvesters_alive > strong_chain_count
    )

    # 3. LIVE VEIN (drop-legal) — the pyramid's bottom rung, and the ONLY path
    #    onto an isolated vein. A vein is too poor for the mass branch above and
    #    too short to form a juice chain (heuristic_chains needs a contiguous
    #    run), so a lone one falls through every surfacing path and no menu id
    #    can take it — the R2.9 gap one tier down. Caelum_Compass d6 is the case:
    #    a redsign suppressed both chains, probe stock was 0 so every hot drop
    #    read UNAFFORDABLE, and the seat's whole menu was ONE option for THREE
    #    harvesters while four drop-legal veins sat in plain view. The think pass
    #    named two of them in prose and the plan pass could not select them,
    #    because resolve_plan only accepts menu ids.
    #
    #    Gated exactly like blue below: only a harvester that would otherwise
    #    IDLE gets sent to a vein, so rich nights keep a clean menu. A drop
    #    auto-harvests its own cell, so even a bare landing banks the vein.
    if spare_harvester:
        veins = sorted(
            ((c, p) for c, p in live.items()
             if _VEIN_MIN <= p < _MASS_MIN and c in live_cells
             and c not in green and c not in covered),
            key=lambda kv: (-kv[1], kv[0][1], kv[0][0]),
        )
        n_vein = 0
        for cell, p in veins:
            if n_vein >= max_vein or cell in covered:
                continue
            tail = _mass_tail(
                agent_view, cell, green, width, height, {cell}, _HOLD_CAP_STEPS,
            )
            specs.append(GrabSpec(
                action="GRAB_VEIN", target=cell, drop_at=cell, cells=tail,
                tier="vein", provenance=LIVE, purity=p,
                note=(
                    f"spare harvester ({harvesters_alive}) beyond "
                    f"{strong_chain_count} strong red chain(s) — a vein banks "
                    f"less than a seam but an idle harvester banks nothing"
                ),
            ))
            covered.add(cell)
            covered.update(tail)
            n_vein += 1

    # 4. Rich LIVE BLUE (drop-legal) — HIGH-YIELD BLUE. Only surface it when it
    #    would NOT steal a harvester from red: orbital asks, OR there is a spare
    #    harvester beyond the strong red chains it could otherwise run.
    if blue_requested or spare_harvester:
        if blue_requested:
            blue_note = (
                "orbital requests blue (blue vault low) — worth a harvester tonight"
            )
        else:
            blue_note = (
                f"spare harvester ({harvesters_alive}) beyond {strong_chain_count} "
                f"strong red chain(s) — grab blue rather than waste it on low-yield red"
            )
        blues = sorted(
            ((c, p) for c, p in _rich_blue(agent_view).items()
             if c in live_cells and c not in green and c not in covered),
            key=lambda kv: (-kv[1], kv[0][1], kv[0][0]),
        )
        for cell, p in blues[:max_blue]:
            tail = _blue_tail(agent_view, cell, green, width, height, {cell}, 2)
            specs.append(GrabSpec(
                action="GRAB_BLUE", target=cell, drop_at=cell, cells=tail,
                tier="blue", provenance=LIVE, colour="BLUE", purity=p,
                note=blue_note,
            ))
            covered.add(cell)
            covered.update(tail)

    return specs


# ── echo pures that no walk can reach ────────────────────────────────────
def force_surface_echo_hotdrops(
    agent_view: Mapping[str, Any],
    *,
    existing_targets: Set[Tuple[int, int]] = frozenset(),
    max_hints: int = 1,
) -> List[Dict[str, Any]]:
    """HOT-DROP hints for ECHO pures that ``force_surface_grabs`` skipped.

    A pure we saw and no longer see is the best target on the board: unlike a
    redsign smear we know the EXACT square, so it needs no search. But when it
    is out of live vision AND not walkable from the frontier, the grab builder
    above bails ("needs a probe — leave to seam_control"), and seam_control aims
    its waves at the JITTERED smear centre, which is routinely 2-3 cells off the
    solved cell (the same miss that made agency.py strip redsign HDs). So the
    exact square could go untargeted while OUT-OF-GRID told the agent to hot-drop
    it — an invitation to invent coordinates. This closes it with a real option.

    Dedup is by ``existing_targets`` — cells an offered option ALREADY walks (see
    ``seam_control.planned_harvest_cells``), never by beacon proximity: a redsign
    is minted the first time any seat sees a pure and persists all season, so
    "near a smear" is true of essentially every echo pure and would suppress this
    option permanently.

    Geometry mirrors ``probe_hints.top_hot_drop_hints``: prefer an OFFSET probe
    centre so the landing does not crush the probe we just paid for, fall back
    to centring on the target and landing adjacent. Returns hint dicts in the
    ``hot_drop_hints`` shape, so the existing agency/packager path compiles them
    with no new machinery.
    """
    stock = int(
        agent_view.get("probe_stock")
        or (agent_view.get("orbit") or {}).get("probe_stock")
        or 0
    )
    if stock <= 0 or max_hints <= 0:
        return []
    harvesters = _orbit_harvester_ids(agent_view)
    if not harvesters:
        return []

    width, height = _grid_dims(agent_view)
    los = _los_cells(agent_view)
    green = _known_green_cells(agent_view)
    _live, echo = _red_by_provenance(agent_view)

    def _eligible(floor: int) -> List[Tuple[Tuple[int, int], int]]:
        return [
            (c, p) for c, p in echo.items()
            if p >= floor
            and c not in los          # in vision -> it is a plain grab
            and c not in green        # already mined out
            and c not in existing_targets
        ]

    # Pures first, richest-first; then MASS to fill the remaining slots (R2.9).
    # Mass that can be walked was already taken by ``force_surface_grabs``, so
    # what reaches here is mass no route can touch without a probe — exactly the
    # case that had no id at all.
    pures = sorted(_eligible(_PURE_MIN), key=lambda kv: (-kv[1], kv[0][1], kv[0][0]))
    targets: List[Tuple[Tuple[int, int], int]] = list(pures)
    if len(targets) < max_hints:
        seen = {c for c, _ in targets}
        targets += sorted(
            (
                (c, p) for c, p in _eligible(_MASS_MIN)
                if p < _PURE_MIN and c not in seen
            ),
            key=lambda kv: (-kv[1], kv[0][1], kv[0][0]),
        )
    if not targets:
        return []

    hints: List[Dict[str, Any]] = []
    for (tx, ty), purity in targets[:max_hints]:
        offsets = [
            (tx + dx, ty + dy)
            for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2)
            if not (dx == 0 and dy == 0)
        ]
        chosen: Optional[Tuple[int, int]] = None
        best_gain = -1
        for cx, cy in offsets + [(tx, ty)]:
            if not (0 <= cx < width and 0 <= cy < height):
                continue
            if not _covers(cx, cy, tx, ty):
                continue
            gain = sum(
                1 for c in _vision_disk(cx, cy, width, height) if c not in los
            )
            if gain > best_gain:
                best_gain = gain
                chosen = (cx, cy)
        if chosen is None:
            continue

        drop_at = (tx, ty)
        comb: List[Tuple[int, int]] = []
        if chosen == (tx, ty):
            # Probe had to centre on the pure — land ADJACENT and step on, so
            # the probe survives the night it was bought for.
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = tx + dx, ty + dy
                if not (0 <= nx < width and 0 <= ny < height):
                    continue
                if (nx, ny) in green:
                    continue
                if _covers(chosen[0], chosen[1], nx, ny):
                    drop_at = (nx, ny)
                    comb = [(tx, ty)]
                    break
        is_pure = purity >= _PURE_MIN
        hints.append({
            "signal_type": "echo-pure" if is_pure else "echo-mass",
            "signal_intensity": 1.0,
            "probe_at": [chosen[0], chosen[1]],
            "drop_at": [drop_at[0], drop_at[1]],
            "comb_path": [[cx, cy] for cx, cy in comb],
            "area_gain": max(0, best_gain),
            "unit": harvesters[len(hints) % len(harvesters)],
            "exact_cell": [tx, ty],
            "note": (
                f"you SAW {'pure' if is_pure else f'mass p{purity}'} at "
                f"({tx},{ty}) and lost vision — this is the EXACT square, not a "
                "smear; no search needed"
            ),
        })
    return hints
