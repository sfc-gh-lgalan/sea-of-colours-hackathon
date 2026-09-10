"""v10 agency layer — the curated option MENU and the ID -> geometry RESOLVER.

This is the seam that turns "Python decides, agent transcribes" into "Python
CURATES, agent SELECTS, mover PACKAGES". Every tactical choice the harness has
already compiled — redsign seam PATTERNS, hot drops, probe placements, juice
chains, supersedes — is registered under a STABLE, human-legible ID this turn:

    SMASH_GRAB / BLIND_GRAB / UNBEATEN_FLANK / WALK_IN   (seam patterns)
    HD1 HD2 ...   hot drops
    PR1 PR2 ...   probe placements
    CH1 CH2 ...   juice chains
    SS1 SS2 ...   supersedes

Flow:
  1. ``build_registry`` assembles the ID -> :class:`Option` map from the already
     ranked hints + seam patterns (geometry stays deterministic).
  2. ``format_menu_block`` renders the menu for the THINKER prompt — it reasons
     over ownership / players / weapons and returns an ordered ``plan`` of IDs.
  3. ``resolve_plan`` validates the thinker's chosen IDs against the registry
     (unknown IDs are dropped — the menu is the source of truth).
  4. ``format_execute_block`` renders the selected options, in the thinker's
     order, as a verbatim "EXECUTE THESE" recipe for the MOVER to package into
     moves. Low transcription risk: the mover copies pre-built geometry.

The registry MUST be rebuilt identically for the thinker prompt and the resolve
step (same inputs -> same IDs), so the harness builds it once per night.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Collection, Dict, List, Mapping, Optional, Sequence, Tuple

from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test import chain_filter
from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test import comb_shapes
from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test import option_economics
from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test import packager
from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test import scorch
from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test import value_pyramid
from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test.seam_control import (
    SeamPattern,
)
from sea_of_colours.game.tuning import probe_vision_radius
from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test._v7.probe_hints import (
    _grid_dims,
    _vision_disk,
    _visible_red,
)

# Comb-shape variants offered per hot drop (the user's "length/area is the
# agent's choice"). Suffix keeps the ID short + legible; the thinker picks one.
_SHAPE_META = {
    "STRETCH": ("L", "stretch — long line, MAX intel/area (may leave the disk)"),
    "SWEEP": ("T", "sweep — TIGHT dense harvest around the drop"),
    "SAMPLE": ("Q", "sample — quick 2-step in/out (hot/CONTESTED grab)"),
}
_SHAPE_ORDER = ("STRETCH", "SWEEP", "SAMPLE")


@dataclass
class Option:
    """A single selectable tactical option with pre-filled geometry."""

    option_id: str
    kind: str            # "seam" | "hotdrop" | "probe" | "chain" | "supersede"
    title: str           # compact menu label
    detail: str          # "when to pick me" context for the menu
    execute_lines: List[str] = field(default_factory=list)
    payload: Dict[str, Any] = field(default_factory=dict)
    # Fix 3.1 (OBS-34) — WHY this play exists and what it trades. Seam patterns
    # have carried this text since v10 and it has never once reached the agent:
    # every named play was being offered as bare geometry, so the reasoning that
    # distinguishes (say) a tempo grab from a value grab lived only in our heads.
    rationale: str = ""
    # v1.40 — which menu section this prints under, when that differs from
    # what it IS. A SNAP cover is a probe launch in every way the packager
    # cares about (same verb, same 1-probe cost), so it must stay kind
    # "probe" or the compiler has to learn a word for it. But filed under
    # PROBE PLACEMENTS it reads as an exploration option, which is the one
    # thing it is not. Empty = group by kind, which is every other option.
    menu_group: str = ""

    @property
    def group(self) -> str:
        return self.menu_group or self.kind

    def menu_line(self) -> str:
        detail = f" — {self.detail}" if self.detail else ""
        return f"  [{self.option_id}] {self.title}{detail}"


# ── helpers ─────────────────────────────────────────────────────────────
def _xy(v: Any) -> Optional[str]:
    if isinstance(v, (list, tuple)) and len(v) == 2:
        try:
            return f"({int(v[0])},{int(v[1])})"
        except (TypeError, ValueError):
            return None
    return None


def _xy_tuple(v: Any) -> Optional[Tuple[int, int]]:
    if isinstance(v, (list, tuple)) and len(v) == 2:
        try:
            return (int(v[0]), int(v[1]))
        except (TypeError, ValueError):
            return None
    return None


def _walk(cells: Sequence[Any]) -> str:
    out = []
    for c in cells or []:
        s = _xy(c)
        if s:
            out.append(s)
    return " ".join(out)


# ── registry construction ───────────────────────────────────────────────
def _seam_option(p: SeamPattern) -> Option:
    # Surface the wave-1 DROP cell in the menu line so the pure-grab reads as a
    # CONCRETE target (not vague prose) — otherwise the thinker gravitates to the
    # raw hot-drop coords and misses the pure (the day-2 whiff).
    detail = p.when
    if p.waves and not getattr(p.waves[0], "deny_only", False):
        d = p.waves[0].drop_at
        detail = f"wave-1 drop ({int(d[0])},{int(d[1])}) — {p.when}"
    elif p.waves:
        # Part B CONTEST_DENY — no drop; surface it as a confirm/deny play so the
        # thinker never reads it as a place to land a harvester.
        b = p.beacon
        detail = f"confirm+deny @({int(b[0])},{int(b[1])}) — {p.when}"
    return Option(
        option_id=p.pattern_id,
        kind="seam",
        title=p.title,
        detail=detail,
        execute_lines=p.execute_block().splitlines(),
        payload=p.to_dict(),
        rationale=p.rationale,
    )


def _hotdrop_option(
    idx: int,
    h: Mapping[str, Any],
    *,
    id_suffix: str = "",
    shape_blurb: str = "",
) -> Option:
    probe = _xy(h.get("probe_at"))
    drop = _xy(h.get("drop_at"))
    walk = _walk(h.get("comb_path") or [])
    sig = str(h.get("signal_type") or "signal")
    unit = str(h.get("unit") or "a harvester")
    oid = f"HD{idx}{id_suffix}"
    line = f"{oid}: probe {probe} -> drop {drop}"
    if walk:
        line += f" -> walk {walk}"
    line += f"  (unit {unit})"
    exec_lines = [line]
    sup = _xy(h.get("supersede"))
    if sup:
        exec_lines.append(
            f"  FIRST MOVE: probe {sup} to SUPERSEDE the finder's probe "
            "(blind them before you drop — do NOT skip this)"
        )
    detail = f"{sig} hot drop"
    if shape_blurb:
        detail += f", {shape_blurb}"
    if h.get("contested"):
        detail += ", CONTESTED"
    return Option(
        option_id=oid,
        kind="hotdrop",
        title=f"{sig} hot drop {drop or '?'}",
        detail=detail,
        execute_lines=exec_lines,
        payload=dict(h),
    )


def _num_xy(v: Any) -> Optional[Tuple[int, int]]:
    """Numeric (x, y) from a [x, y]/(x, y) pair — unlike ``_xy`` which formats
    a display string. Returns None for anything else."""
    if isinstance(v, (list, tuple)) and len(v) == 2:
        try:
            return int(v[0]), int(v[1])
        except (TypeError, ValueError):
            return None
    return None


def _hotdrop_value_cells(
    h: Mapping[str, Any], agent_view: Mapping[str, Any],
) -> List[Tuple[int, int]]:
    """Visible value (red + blue) inside the hot drop's probe disk.

    SWEEP/SAMPLE combs bias their walk toward these cells. Without them the
    combs walked blind and could route AROUND the very cluster the drop landed
    on (the "walked around its own live blue" bug). We rank red by purity and
    append any blue tiles in the disk so a bluesign hot drop sweeps its cluster.
    """
    probe = _num_xy(h.get("probe_at")) or _num_xy(h.get("drop_at"))
    if probe is None:
        return []
    width, height = _grid_dims(agent_view)
    disk = set(_vision_disk(probe[0], probe[1], width, height))
    red = _visible_red(agent_view)
    cells = sorted((c for c in red if c in disk), key=lambda c: -red[c])
    seen = set(cells)
    for row in (agent_view.get("blue_tiles") or []):
        if not isinstance(row, Mapping):
            continue
        try:
            c = (int(row["x"]), int(row["y"]))
        except (TypeError, KeyError, ValueError):
            continue
        if c in disk and c not in seen:
            cells.append(c)
            seen.add(c)
    return cells


def _hotdrop_shape_options(
    idx: int, h: Mapping[str, Any], agent_view: Mapping[str, Any],
) -> List[Option]:
    """Expand one hot drop into its comb-shape variants (STRETCH/SWEEP/SAMPLE).

    Same probe+drop, three walks the thinker chooses between. A shape with an
    empty walk (boxed in by green/edge) is skipped; if none survive we fall back
    to the drop's own precompiled comb as a single plain HDn so the drop is never
    lost. Each variant shares ``group`` so the packager treats them as one drop.
    """
    variants = comb_shapes.comb_variants(
        agent_view, h.get("probe_at"), h.get("drop_at"),
        value_cells=_hotdrop_value_cells(h, agent_view),
    )
    opts: List[Option] = []
    for shape in _SHAPE_ORDER:
        comb = variants.get(shape) or []
        if not comb and shape != "SAMPLE":
            continue
        suffix, blurb = _SHAPE_META[shape]
        payload = dict(h)
        payload["comb_path"] = comb
        payload["shape"] = shape
        payload["group"] = f"HD{idx}"
        opts.append(_hotdrop_option(idx, payload, id_suffix=suffix, shape_blurb=blurb))
    if not opts:
        opts.append(_hotdrop_option(idx, h))
    return opts


_GRAB_TITLE = {
    "SMASH": "SMASH the pure",
    "WALK_IN": "walk in & grab the pure",
    "GRAB_MASS": "grab live mass",
    "GRAB_BLUE": "grab live blue",
}


def _grab_option(idx: int, spec: "value_pyramid.GrabSpec") -> Option:
    """Render a force-surfaced VALUE-PYRAMID grab as a chain-compiled option.

    Phase 1: any pure/mass/blue the seat can SEE and reach becomes a top-of-menu
    ``grab`` — packaged exactly like a juice chain (drop + walk), so the
    deterministic packager needs no new machinery. The distinct kind/ID keeps it
    unmistakable as a "you can see this — take it" play, ranked above everything.

    R2.2 — blue gets its own ``BL*`` family. Sharing ``GRAB*`` meant that on a
    night with no red grab the blue one came out as ``GRAB1``, and doctrine's
    "PRIORITY RED GRABS — GRAB1 first" then read as an instruction to spend the
    first harvester on blue. That is exactly what happened on s69 night 3.
    """
    oid = f"BL{idx}" if spec.action == "GRAB_BLUE" else f"GRAB{idx}"
    tgt = _xy(spec.target)
    drop = _xy(spec.drop_at)
    walk = _walk(spec.cells)
    if spec.action == "WALK_IN":
        line = f"{oid}: drop {drop} -> walk {walk}  (walk ONTO the pure {tgt}, then mass)"
    else:
        line = f"{oid}: drop {drop}" + (f" -> walk {walk}" if walk else "")
        line += "  (auto-harvest on the drop cell)"
    title = f"{_GRAB_TITLE.get(spec.action, 'grab')} {tgt or '?'}"
    detail = (
        f"{spec.provenance} {spec.tier} "
        f"({spec.colour.lower()} p{spec.purity}) — you can SEE it; take it, no probe needed"
    )
    if spec.note:
        detail += f" — {spec.note}"
    return Option(
        option_id=oid,
        kind="blue_grab" if spec.action == "GRAB_BLUE" else "grab",
        title=title,
        detail=detail,
        execute_lines=[line],
        payload={
            "drop_at": list(spec.drop_at),
            "cells": [list(c) for c in spec.cells],
            "unit": "a harvester",
            "grab_action": spec.action,
            "target": list(spec.target),
            "provenance": spec.provenance,
            "colour": spec.colour,
            "purity": spec.purity,
        },
    )


def _existing_targets(reg: "Mapping[str, Option]") -> set:
    """Every cell the current menu already targets (seam waves / hot-drops /
    chains / probes), so force-surfaced grabs never double-offer the same value."""
    cells: set = set()

    def _add(v: Any) -> None:
        c = _num_xy(v)
        if c is not None:
            cells.add(c)

    for opt in reg.values():
        pay = opt.payload or {}
        for w in (pay.get("waves") or []):
            if isinstance(w, Mapping):
                _add(w.get("drop_at"))
        _add(pay.get("drop_at"))
        _add(pay.get("at"))
        _add(pay.get("probe_at"))
        for c in (pay.get("cells") or []):
            _add(c)
        for c in (pay.get("comb_path") or []):
            _add(c)
    return cells


def _probe_option(idx: int, h: Mapping[str, Any]) -> Option:
    at = _xy(h.get("at"))
    frm = str(h.get("extends_from") or "fog")
    detail = f"extends {frm}"
    if h.get("contested"):
        detail += ", CONTESTED"
    return Option(
        option_id=f"PR{idx}",
        kind="probe",
        title=f"probe {at or '?'}",
        detail=detail,
        execute_lines=[f"PR{idx}: launch a probe at {at}"],
        payload=dict(h),
    )


# ── EMP salvos (this fork's addition) ───────────────────────────────────
#
# Two plays, and they exist separately because they buy different things.
# EMP_SCORCH kills eyes: rival probes die the hour the cloud forms, so it
# is a strike with a body count. SCORCH_REDSIGN kills nothing — it makes
# ground unusable for eight hours, which is only worth an hour of your
# night when someone else is racing you for that ground.
#
# Both carry ``detail`` AND ``rationale`` (rung 3). Bare geometry is not a
# decision: "emp (12,7)" tells the thinker nothing it can weigh against a
# grab worth 400 RED, and an option that cannot be weighed does not get
# picked. Same lesson as OBS-34 on the seam patterns.


def _emp_scorch_option(
    targets: Sequence[Tuple[int, int]],
    *,
    kills: int,
    notes: Sequence[str],
    cloud_hours: int,
    covered: int,
) -> Option:
    aim = " ".join(_fmt_cell(c) for c in targets)
    return Option(
        option_id="EMP_SCORCH",
        kind="emp",
        title=f"EMP salvo -> {aim}",
        detail=(
            f"kills {kills} rival probe(s) on impact; {covered} cells go dark "
            f"for {cloud_hours}h. Costs ONE hour-slot and one charge."
        ),
        # The recipe names the WIRE VERB, not prose. On a fallback night
        # the LLM mover transcribes these lines straight into moves, and
        # "launch an EMP" is not something it can transcribe — the whole
        # salvo is one move whose ``at`` is a LIST of cells.
        execute_lines=[
            f"EMP_SCORCH: emp_launch at {[list(c) for c in targets]} "
            "(ONE move, all cells in one `at` list, ONE charge)"
        ],
        payload={"targets": [list(c) for c in targets], "kind_note": "probes"},
        rationale=(
            "Blinding the finder is worth more than the cells. A probe you "
            "kill tonight is vision the rival never converts into a landing "
            "tomorrow, and probes die the hour the cloud forms — this is the "
            "one weapon play with a body count rather than an area denial. "
            "Freshest probe first: an old probe has already told them what is "
            "there. " + " ".join(notes) + " "
            "PRICE: the launch spends one of your 21 hours and the seat does "
            "nothing else that hour, so weigh it against the RED the chain "
            "you drop instead would have banked. Your own units are NOT "
            "immune — the compiler will keep your plans out of the diamond "
            "and tell you when it had to."
        ),
    )


def _scorch_redsign_option(
    targets: Sequence[Tuple[int, int]],
    *,
    notes: Sequence[str],
    cloud_hours: int,
    covered: int,
) -> Option:
    aim = " ".join(_fmt_cell(c) for c in targets)
    return Option(
        option_id="SCORCH_REDSIGN",
        kind="emp",
        title=f"EMP salvo over the RIVAL redsign -> {aim}",
        detail=(
            f"{covered} cells of THEIR smear unusable for {cloud_hours}h; "
            "you walk in after the cloud lifts. Costs ONE hour-slot."
        ),
        execute_lines=[
            f"SCORCH_REDSIGN: emp_launch at {[list(c) for c in targets]} "
            "(ONE move, all cells in one `at` list, ONE charge)"
        ],
        payload={"targets": [list(c) for c in targets], "kind_note": "redsign"},
        rationale=(
            "A rival found a pure and you did not. You cannot out-search them "
            "to it tonight, but you can make the ground cost them the night: "
            "anything they land in the smear is disabled from the following "
            "hour and banks nothing. " + " ".join(notes) + " "
            "This kills NOTHING — it buys tempo, so it is only worth an hour "
            "when you believe they are actually coming. "
            "TWO WAYS THIS IS WRONG: if the sign is YOURS, scorching it locks "
            "your own harvesters out of your own pure for 8 hours to deny a "
            "rival who may not even be en route — take the grab instead. And "
            "the cloud does not care whose it is, so plan your own walk-in "
            f"for after hour {cloud_hours + 1}, not before."
        ),
    )


def _blind_scorch_option(
    shape: Mapping[str, Any],
    *,
    probe_at: Tuple[int, int],
    comb: Sequence[Tuple[int, int]],
    cloud_hours: int,
    variant: str,
) -> Option:
    """The full shape: scorch around a hole, occupy it, walk out at dawn-ish.

    The play SCORCH_REDSIGN cannot express. A blanket salvo denies the
    smear to them and to you; this one denies it only to them, because
    the three missiles are placed to leave one cell open and that cell is
    where your probe and your harvester go.
    """
    hole = tuple(shape["hole"])
    targets = [tuple(t) for t in shape["targets"]]
    aim = " ".join(_fmt_cell(c) for c in targets)
    open_extra = [c for c in shape.get("open_cells") or [] if tuple(c) != hole]
    # The rim variant is the headline id because it is the better play;
    # the tight one carries the suffix.
    oid = "BLIND_SCORCH_T" if variant == "hole" else "BLIND_SCORCH"
    # The difference between the two variants is not cosmetic, and the
    # engine is blunt about it: landing a harvester on your own probe
    # CRUSHES the probe ("1 probe crushed" in the drop frame). Putting
    # both on the hole is one cell doing two jobs and costs you the eye
    # the moment the harvester arrives; the rim keeps it lit all night.
    where = (
        "probe INTO the hole — simplest, but the landing CRUSHES that probe, "
        "so you comb blind from hour 4 on"
        if variant == "hole" else
        "probe on the clear rim so the landing does NOT crush it — the disk "
        "stays lit over the whole walk"
    )
    return Option(
        option_id=oid,
        kind="emp",
        title=f"scorch {aim} leaving {_fmt_cell(hole)} open, then take it",
        detail=(
            f"{len(shape['covered'])} cells of THEIR smear dark for "
            f"{cloud_hours}h; you hold {_fmt_cell(hole)} inside it — "
            f"{where}, comb {len(comb)} cells out when the cloud lifts. "
            "Salvo + probe + drop = 3 hour-slots up front."
        ),
        execute_lines=[
            f"{oid}: emp_launch at {[list(c) for c in targets]} "
            "(ONE move, all cells in one `at` list, ONE charge)",
            f"{oid}: probe at {list(probe_at)}",
            f"{oid}: drop a harvester at {list(hole)}",
            f"{oid}: after the cloud clears, walk "
            f"{[list(c) for c in comb]} and pick up",
        ],
        payload={
            "shape": "occupy",
            "targets": [list(c) for c in targets],
            "hole": list(hole),
            "probe_at": list(probe_at),
            "comb": [list(c) for c in comb],
            "open_cells": [list(c) for c in open_extra],
        },
        rationale=(
            "AREA DENIAL YOU CAN STAND IN. A cell is only darkened when a "
            "missile lands within its radius, and you choose where the "
            "missiles land — so aim all three at least one cell further out "
            "than the blast reaches and the middle stays clear. For the whole "
            "cloud's life that hole is a place only you can use: your probe "
            "there is not swept, your harvester there is not disabled, and no "
            "rival can walk in to contest it. When the cloud lifts you are "
            "already standing on the seam with a full comb ahead of you and "
            "they are still in orbit. "
            "THE HOURS IN BETWEEN ARE NOT DEAD TIME — the salvo, the probe "
            "and the drop cost three slots, and the rest of the night is "
            "yours: run your other harvesters somewhere else entirely while "
            "the cloud does the holding. The compiler sequences it that way "
            "and only inserts a wait if you genuinely gave it nothing to do. "
            + (
                f"Leakage: {len(open_extra)} other cell(s) of the smear are "
                "also uncovered, and a rival can use those too. "
                if open_extra else "The salvo covers the rest of the smear. "
            ) +
            "The comb is BLIND — the smear is an approximation, so it walks "
            "toward the middle where a pure is likeliest, not at a cell you "
            "have seen."
        ),
    )


def _blind_scorch_options(agent_view: Mapping[str, Any]) -> List[Option]:
    """Shaped salvos over a rival's redsign, best variant first."""
    radius, missiles, cloud_hours = scorch.specs(agent_view)
    region_cells, region, _notes = _rival_redsign_cells(agent_view)
    if not region_cells or region is None:
        return []
    shape = scorch.shaped_salvo(
        region_cells, radius=radius, missiles=missiles,
    )
    if shape is None:
        return []

    hole = tuple(shape["hole"])
    width, height = _grid_dims(agent_view)
    # Blind comb: the smear only approximates where the pure is, so walk
    # toward its middle, which is where the jitter is likeliest to have
    # put one. Ordered nearest-the-middle first because ``comb_path``
    # reads the order as a value ranking.
    cx = sum(c[0] for c in region_cells) / len(region_cells)
    cy = sum(c[1] for c in region_cells) / len(region_cells)
    value_cells = sorted(
        set(tuple(c) for c in shape["covered"]) | {hole},
        key=lambda c: (abs(c[0] - cx) + abs(c[1] - cy), c),
    )

    out: List[Option] = []
    # Rim first: it keeps its probe alive through the walk, which on a
    # blind comb over a jittered smear is the whole difference between
    # walking toward value and walking toward the middle and hoping.
    for variant, probe_at in _blind_scorch_probe_sites(
        shape, agent_view, radius,
    ):
        comb = [
            tuple(c) for c in comb_shapes.comb_path(
                probe_at[0], probe_at[1], hole, width, height,
                set(), value_cells, max_steps=6,
            )
        ]
        if not comb:
            continue
        out.append(_blind_scorch_option(
            shape, probe_at=probe_at, comb=comb,
            cloud_hours=cloud_hours, variant=variant,
        ))
    return out


def _blind_scorch_probe_sites(
    shape: Mapping[str, Any], agent_view: Mapping[str, Any], radius: int,
) -> List[Tuple[str, Tuple[int, int]]]:
    """Where the enabling probe goes — two real choices, both offered.

    Into the hole is the tidy answer: one cell does both jobs, and the
    disk is centred on the walk. On the rim is the better answer when the
    hole sits at the edge of the smear, because the probe's disk then
    reaches further across the ground the harvester will actually walk —
    and a rim cell outside the blast is just as safe as the hole is.
    """
    hole = tuple(shape["hole"])
    covered = {tuple(c) for c in shape["covered"]}
    if not covered:
        return [("hole", hole)]
    # Rim = clear of every diamond, but within probe vision of the hole so
    # the drop is actually legal at hour start (§3.9.7).
    reach = probe_vision_radius()
    blast_cells = scorch.blast(
        [tuple(t) for t in shape["targets"]], radius,
    )
    rim = sorted(
        c for c in scorch.diamond(hole, reach, _grid_dims(agent_view))
        if c not in blast_cells and c != hole
        and abs(c[0] - hole[0]) + abs(c[1] - hole[1]) >= 2
    )
    if not rim:
        return [("hole", hole)]
    # Furthest clear cell still covering the hole: maximum extra disk.
    return [("rim", rim[-1]), ("hole", hole)]


# ── SNAP salvos (v13 — the third weapon shape) ────────────────────────
#
# SNAP is one missile, one cell, one hour. Two properties matter here and
# neither the EMP salvos nor a chain option carry them:
#
#   1. It resolves ABOVE the hour's vision snapshot (§4.9.4). A landing
#      into the cell it lands on is refused THE HOUR IT FIRES, not next —
#      the deliberate opposite of the EMP, which resolves below the
#      snapshot and only denies from the following hour. This is the
#      smash-and-grab counter: a rival about to belly-flop a pure at H1
#      lands on a hot cell, is damaged in orbit, and never spends the
#      outing (§3.9.2). "Kills the beacon before the landing checks it."
#
#   2. It is the cheapest ordnance in the game — 100 blue vs the EMP's
#      200, vs chaff's 300 (§4.9.8).
#
# SNAP_KILL is the generic cheap-single-probe-hunt (unrestricted target).
# SNAP_STRIKE is the specific rival-redsign probe-hunt whose value scales
# with HOW FEW rival probes see the redsign — killing a rival's SOLE eye
# denies their drop tonight; killing one of three eyes achieves nothing.


def _snap_kill_option(
    target: Tuple[int, int], *,
    victim_note: str,
    freshness_days: Optional[int] = None,
) -> Option:
    fresh = (
        f" (last seen day {int(freshness_days)})"
        if freshness_days is not None else ""
    )
    return Option(
        option_id="SNAP_KILL",
        kind="snap",
        title=f"SNAP round -> {_fmt_cell(target)}",
        detail=(
            "One round, one cell, ONE hour-slot. Kills the probe on impact; "
            "any harvester on the cell this hour is crippled; any landing "
            "into it THIS hour is refused (§4.9.4)."
        ),
        execute_lines=[
            f"SNAP_KILL: snap at {list(target)} (ONE cell, ONE charge)"
        ],
        payload={"target": list(target), "kind_note": "probe_kill"},
        rationale=(
            f"Freshest single eye, cheapest ordnance. {victim_note}{fresh}. "
            "SNAP is 100 blue and one hour-slot — half the blue of an EMP, "
            "same hour. Where EMP kills 2-3 probes for the cost of a whole "
            "salvo, SNAP kills ONE for the cost of a chain-step. Take it "
            "when a probe you badly want dead sits alone, or when your BLUE "
            "vault is running short and you cannot afford to build EMPs. "
            "UNIQUE PROPERTY: resolves ABOVE the vision snapshot, so a drop "
            "into this cell THIS hour is refused (an EMP fired on the same "
            "cell only denies from NEXT hour). If a rival is about to land, "
            "the SNAP is the only weapon that stops them tonight."
        ),
    )


def _snap_strike_option(
    target: Tuple[int, int], *,
    beacon: Tuple[int, int],
    probes_covering: int,
    total_visible_probes: Sequence[Tuple[int, int]],
    freshness_days: Optional[int],
) -> Option:
    """SNAP a rival probe covering a NON-mine redsign's pure cell.

    Telemetry on the line: how many rival probes currently see the pure,
    which one this shot kills, and a plain-English note that the play is
    only really effective at ~1 (kills the sole eye = denies the drop) and
    degrades sharply above that.
    """
    fresh = (
        f" (last seen day {int(freshness_days)})"
        if freshness_days is not None else ""
    )
    others = [c for c in total_visible_probes if c != target]
    others_txt = (
        f"; {len(others)} other probe(s) still see the beacon: {others}"
        if others else ""
    )
    if probes_covering <= 1:
        eff_head = "STRONG"
        effectiveness = (
            "STRONG — this is the rival's SOLE eye on the pure. Killing it "
            "removes their live coverage: their next drop into this cell is "
            "refused for lack of vision (§3.9.7), THIS NIGHT. That is exactly "
            "the outcome a full BLIND_SCORCH tries to buy for twice the blue "
            "and a whole harvester's outing."
        )
    elif probes_covering == 2:
        eff_head = "DEGRADED"
        effectiveness = (
            "DEGRADED — a second rival probe also sees the pure. This SNAP "
            "removes ONE eye; the drop is still legal from the other. Half "
            "the denial for the same cost. Take it only when the second "
            "probe is about to expire or when you have another way to blind "
            "the second eye this night (SS on it, or a follow-up SNAP)."
        )
    else:
        eff_head = "WEAK"
        effectiveness = (
            f"WEAK — {probes_covering} rival probes see this pure. Killing "
            "one is one-in-many denial; the drop still lands. Save the "
            "charge for a lower-vision target."
        )
    return Option(
        option_id="SNAP_STRIKE",
        kind="snap",
        title=(
            f"SNAP the finder's probe {_fmt_cell(target)} on rival redsign "
            f"@{_fmt_cell(beacon)}"
        ),
        detail=(
            f"One round, one cell, ONE hour-slot. Kills probe "
            f"{_fmt_cell(target)}; the beacon at {_fmt_cell(beacon)} is "
            f"currently lit by {probes_covering} rival probe(s). "
            f"Effectiveness: {eff_head}."
        ),
        execute_lines=[
            f"SNAP_STRIKE: snap at {list(target)} "
            f"(ONE cell, ONE charge — kills 1 of {probes_covering} probes "
            f"covering the rival redsign @{list(beacon)})"
        ],
        payload={
            "target": list(target),
            "beacon": list(beacon),
            "probes_covering": int(probes_covering),
            "kind_note": "rival_redsign_finder",
        },
        rationale=(
            f"TELEMETRY: rival redsign @{beacon} is lit by "
            f"{probes_covering} rival probe(s) right now{others_txt}. "
            f"Freshest eye is at {target}{fresh}. "
            f"EFFECTIVENESS: {effectiveness} "
            "SNAP resolves ABOVE the vision snapshot (§4.9.4), so a drop "
            "into a cell whose sole lighting probe you just killed is "
            "refused THIS NIGHT, not next. That is the property this play "
            "is buying, and it is worth 100 blue only when the coverage "
            "count is genuinely 1. Compare to BLIND_SCORCH: same denial "
            "outcome (rival cannot land), but BLIND_SCORCH costs 200 blue "
            "and 3 hour-slots, and it puts YOUR harvester on the seam. "
            "SNAP_STRIKE is the CHEAP option when you want denial only, "
            "or when your BLUE vault cannot afford a BLIND_SCORCH build."
        ),
    )


def _snap_options(
    agent_view: Mapping[str, Any],
    enemy_probes: Sequence[Mapping[str, Any]],
) -> List[Option]:
    """Every SNAP play worth offering tonight, best first.

    Order: SNAP_STRIKE (rival redsign, targeted) then SNAP_KILL (freshest
    single eye anywhere). Only surfaces when the rack has ≥1 SNAP and there
    is at least one enemy probe on the board.
    """
    rack = scorch.stock(agent_view)
    if rack.get("snap", 0) <= 0:
        return []
    if not enemy_probes:
        return []
    out: List[Option] = []

    # SNAP_STRIKE — only offered when the rival redsign's pure cell is
    # covered by ≤2 rival probes (per doctrine: effectiveness collapses
    # above that). Pick the FRESHEST probe covering the pure.
    r_cells, region, _notes = _rival_redsign_cells(agent_view)
    if region is not None and region.get("pure_cells"):
        pure = scorch._cell(region["pure_cells"][0])
        if pure is not None:
            reach = probe_vision_radius()
            probes_covering: List[Tuple[Tuple[int, int], int]] = []
            for row in (enemy_probes or []):
                if not isinstance(row, Mapping):
                    continue
                at = _cell_of(row.get("at"))
                if at is None:
                    continue
                # Euclidean disk r=4: dx² + dy² <= reach².
                dx, dy = at[0] - pure[0], at[1] - pure[1]
                if dx * dx + dy * dy <= reach * reach:
                    probes_covering.append((at, int(row.get("day_seen") or 0)))
            probes_covering.sort(key=lambda t: -t[1])
            if probes_covering and len(probes_covering) <= 2:
                target, day_seen = probes_covering[0]
                out.append(_snap_strike_option(
                    target,
                    beacon=(pure[0], pure[1]),
                    probes_covering=len(probes_covering),
                    total_visible_probes=[p for p, _ in probes_covering],
                    freshness_days=day_seen or None,
                ))

    # SNAP_KILL — freshest enemy probe on the board, regardless of what it
    # covers. Cheap single-target denial when the redsign play doesn't fit.
    freshest: Optional[Tuple[Tuple[int, int], int, str]] = None
    for row in (enemy_probes or []):
        if not isinstance(row, Mapping):
            continue
        at = _cell_of(row.get("at"))
        if at is None:
            continue
        d = int(row.get("day_seen") or 0)
        owner = str(row.get("owner") or "a rival")
        if freshest is None or d > freshest[1]:
            freshest = (at, d, owner)
    if freshest is not None:
        target, day_seen, owner = freshest
        already_targeted = any(
            tuple(o.payload.get("target") or []) == tuple(target)
            for o in out
        )
        if not already_targeted:
            out.append(_snap_kill_option(
                target,
                victim_note=f"{owner}'s probe",
                freshness_days=day_seen or None,
            ))
    return out


def _rival_redsign_cells(
    agent_view: Mapping[str, Any],
) -> Tuple[List[Tuple[int, int]], Optional[Mapping[str, Any]], List[str]]:
    for region in (agent_view.get("redsign") or []):
        if not isinstance(region, Mapping) or region.get("mine"):
            continue
        cells = [
            c for c in (scorch._cell(c) for c in (region.get("cells") or []))
            if c
        ]
        if cells:
            return cells, region, []
    return [], None, []


def _emp_options(
    agent_view: Mapping[str, Any],
    enemy_probes: Sequence[Mapping[str, Any]],
) -> List[Option]:
    """Every salvo worth offering tonight, best first.

    Offered whenever the rack is non-empty and a target exists. NOT
    filtered by whether the play looks wise — that judgement is the
    thinker's, informed by doctrine and by the prices on the lines above.
    Withholding the option would make the seat look like it declined a
    play it was never shown, which is the exact failure this fork exists
    to fix.
    """
    rack = scorch.stock(agent_view)
    if rack["emp"] <= 0:
        return []
    radius, missiles, cloud_hours = scorch.specs(agent_view)
    out: List[Option] = []

    hits, notes = scorch.probe_targets(
        agent_view, enemy_probes, missiles=missiles,
    )
    if hits:
        live = {c for c in hits}
        kills = sum(
            1 for r in (enemy_probes or [])
            if _cell_of(r.get("at")) in live
        )
        out.append(_emp_scorch_option(
            hits, kills=kills, notes=notes, cloud_hours=cloud_hours,
            covered=len(scorch.blast(hits, radius)),
        ))

    # The shaped play goes ABOVE the blanket one. Both deny the rival the
    # same ground; only one of them also puts you on it, so a thinker
    # reading top-down should meet that first.
    out.extend(_blind_scorch_options(agent_view))

    sign_hits, region, sign_notes = scorch.redsign_targets(
        agent_view, radius=radius, missiles=missiles,
    )
    if sign_hits and region is not None:
        out.append(_scorch_redsign_option(
            sign_hits, notes=sign_notes, cloud_hours=cloud_hours,
            covered=len(scorch.blast(sign_hits, radius)),
        ))
    return out


def _cell_of(at: Any) -> Optional[Tuple[int, int]]:
    return scorch._cell(at)


def _snap_cover_option(
    idx: int, h: Mapping[str, Any], backed: Sequence[str] = (),
) -> Option:
    """A second probe over a landing whose sight rests on one probe (v1.40).

    Offered, never prescribed. Whether a rival actually bought the SNAP is
    unknowable — the public total says only what it could be — so the honest
    framing is a cost and a risk laid side by side, with the call left where
    it belongs. The text names SNAP explicitly because the doctrine block
    that explains the danger and this option that answers it are read pages
    apart, and an option the agent cannot connect to the fear is an option
    it will not reach for.
    """
    at = _xy(h.get("at")) or "?"
    covers = _xy(h.get("covers")) or "?"
    primary = _xy(h.get("primary")) or "?"
    gain = int(h.get("area_gain") or 0)
    ids = ", ".join(str(b) for b in backed if b)
    fresh = f", opens {gain} fresh cell{'s' if gain != 1 else ''}" if gain else ""
    return Option(
        option_id=f"PRSNAP{idx}",
        kind="probe",
        menu_group="snap_cover",
        title=f"probe {at} — SNAP cover for {covers}",
        detail=(
            f"second pair of eyes on {covers}{fresh}"
            + (f"; backs {ids}" if ids else "")
        ),
        rationale=(
            f"SNAP MITIGATION — take it only if you judge the probe at "
            f"{primary} to be in real danger tonight. {covers} is currently "
            f"seen by that one probe. A SNAP is the cheapest weapon on the "
            f"ladder at 100 blue and lands on ONE cell before that cell's "
            f"vision snapshot, so if it takes {primary} the sight never "
            f"exists and the landing on {covers} is refused for want of it. "
            f"A probe at {at} sees {covers} as well, from a cell the same "
            f"round cannot reach. The cost is one probe and an hour spent on "
            f"ground you can already see; against that, whether this landing "
            f"is one you would be sorry to lose, and whether the rival's "
            f"published blue is enough to be a SNAP at all. Your call — an "
            f"unarmed rival makes this a wasted probe."
        ),
        execute_lines=[f"PRSNAP{idx}: launch a probe at {at}"],
        payload=dict(h),
    )


def _chain_option(idx: int, h: Mapping[str, Any], *, id_suffix: str = "") -> Option:
    oid = f"CH{idx}{id_suffix}"
    drop = _xy(h.get("drop_at"))
    cells = _walk(h.get("cells") or [])
    length = int(h.get("length") or len(h.get("cells") or []))
    unit = str(h.get("unit") or "a harvester")
    line = f"{oid}: drop {drop} then walk {cells}  (unit {unit})"
    detail = "known-red walk, no probe needed"
    if id_suffix == "S":
        detail = "SHORT — grab the rich head and lift early (lower exposure/hold use)"
    return Option(
        option_id=oid,
        kind="chain",
        title=f"juice chain {drop or '?'} (x{length})",
        detail=detail,
        execute_lines=[line],
        payload=dict(h),
    )


# Drop + this many steps = the "rich head" of a long chain: a SHORT variant the
# thinker can pick to bank the densest cells and lift before the tail exposes it.
_CHAIN_SHORT_STEPS = 2


def _chain_shape_options(idx: int, h: Mapping[str, Any]) -> List[Option]:
    """Full chain plus, for a LONG chain, a SHORT 'rich head' variant.

    The greedy chain is purity-descending, so its first cells are the richest;
    a SHORT variant (drop + 2 steps) lets the thinker trade tail length for lower
    exposure / hold use. The two share the same drop cell, so the packager's
    duplicate-drop guard means only one ever executes even if both are selected.
    """
    full = _chain_option(idx, h)
    opts = [full]
    cells = [
        c for c in (h.get("cells") or [])
        if isinstance(c, (list, tuple)) and len(c) == 2
    ]
    keep = _CHAIN_SHORT_STEPS + 1  # drop + 2 steps
    if len(cells) > keep:
        sh = dict(h)
        sh["cells"] = cells[:keep]
        sh["length"] = keep
        sh["purities"] = list(h.get("purities") or [])[:keep]
        sh["tiers"] = list(h.get("tiers") or [])[:keep]
        sh["group"] = f"CH{idx}"
        opts.append(_chain_option(idx, sh, id_suffix="S"))
    return opts


def _supersede_option(idx: int, h: Mapping[str, Any]) -> Option:
    at = _xy(h.get("probe_at"))
    owner = str(h.get("owner") or "").strip()
    title = f"supersede {owner + ' probe' if owner else 'enemy probe'} {at or '?'}"
    detail = "land your probe on theirs to blind it (yours survives)"
    notes: List[str] = []
    owner_score = int(h.get("owner_score") or 0)
    if h.get("is_finder"):
        notes.append(
            "the REDSIGN FINDER — this disk lights the contested pure; blinding "
            "it DENIES the jackpot (blind this first)"
        )
    if h.get("is_leader"):
        notes.append(f"the LEADER's probe (score {owner_score})")
    elif owner:
        notes.append(f"{owner} (score {owner_score})" if owner_score > 0 else owner)
    rem = h.get("nights_remaining")
    if isinstance(rem, int):
        notes.append(f"{rem} night(s) of vision left to deny")
    if notes:
        detail += " — " + "; ".join(notes)
    return Option(
        option_id=f"SS{idx}",
        kind="supersede",
        title=title,
        detail=detail,
        execute_lines=[f"SS{idx}: launch a probe onto {at} to supersede the enemy probe"],
        payload=dict(h),
    )


# ── frontier hot-drop (gated last-resort) ───────────────────────────────
# Purity floor below which known RED counts as "trace-only" (nothing worth a
# real chain). Mirrors the engine's vein floor so the gate matches scoring.
try:  # pragma: no cover - defensive import
    from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test._v7.probe_hints import (
        _VEIN_MIN_PURITY as _TRACE_CEIL,
    )
except Exception:  # pragma: no cover
    _TRACE_CEIL = 51

_FRONTIER_WALK = 3  # blind sample — a short walk, mover trims to danger/hours


def _best_known_red_purity(
    chain_hints: Sequence[Mapping[str, Any]], agent_view: Mapping[str, Any],
) -> int:
    """Peak purity of any RED the seat already knows it can harvest.

    Reads the compiled chain hints (their per-cell purities) and the visible-red
    tiles. This is the yield the frontier hot-drop is gated against: if the best
    known red is only trace, a blind drop into promising fog can beat it.
    """
    best = 0
    for h in chain_hints or []:
        for p in (h.get("purities") or []):
            try:
                best = max(best, int(p))
            except (TypeError, ValueError):
                continue
    for t in (agent_view.get("red_tiles") or []):
        if isinstance(t, Mapping):
            try:
                best = max(best, int(t.get("purity") or 0))
            except (TypeError, ValueError):
                continue
    return best


def _frontier_hotdrop_option(
    agent_view: Mapping[str, Any],
    chain_hints: Sequence[Mapping[str, Any]],
    probe_hints: Sequence[Mapping[str, Any]],
    *,
    has_seam: bool,
    has_hotdrop: bool,
) -> Optional[Option]:
    """A blind hot-drop into the best echo/fog cluster — ONLY as a last resort.

    Gate (all must hold): no redsign seam pattern and no bluesign/redsign hot
    drop are on offer (those are strictly better targets), AND the best RED the
    seat already knows is trace-only (< the vein floor), AND there is a promising
    fog target (a probe hint with echo/seam promise). Otherwise returns None so
    the option never appears when real red is available — exactly the user's
    "only if there is nothing beyond trace" gate.
    """
    if has_seam or has_hotdrop:
        return None
    if _best_known_red_purity(chain_hints, agent_view) >= _TRACE_CEIL:
        return None

    best = None
    for h in probe_hints or []:
        if not isinstance(h, Mapping):
            continue
        at = h.get("at")
        if not (isinstance(at, (list, tuple)) and len(at) == 2):
            continue
        ep = int(h.get("edge_promise") or 0)
        if ep <= 0:
            continue
        if best is None or ep > best[0]:
            best = (ep, (int(at[0]), int(at[1])), h)
    if best is None:
        return None

    ep, (tx, ty), hint = best
    world = agent_view.get("world") or {}
    w = int(world.get("width") or 40)
    hgt = int(world.get("height") or 28)
    # A short blind sample walk from the drop cell, kept in-bounds. Fixed bearing
    # (E then S) — it is blind terrain, so this is only a suggestion the mover
    # trims; the DROP cell auto-harvests whatever is under it.
    walk: List[str] = []
    cx, cy = tx, ty
    for dx, dy in ((1, 0), (1, 0), (0, 1)):
        nx, ny = min(max(cx + dx, 0), w - 1), min(max(cy + dy, 0), hgt - 1)
        if (nx, ny) != (cx, cy):
            walk.append(f"({nx},{ny})")
            cx, cy = nx, ny
        if len(walk) >= _FRONTIER_WALK:
            break
    line = f"FR1: probe ({tx},{ty}) -> drop ({tx},{ty})"
    if walk:
        line += " -> walk " + " ".join(walk)
    line += "  (unit a harvester; blind sample — pick up when the hold fills)"
    return Option(
        option_id="FR1",
        kind="frontier",
        title=f"frontier hot-drop ({tx},{ty})",
        detail=(
            "LAST RESORT — known red is trace-only; blind-drop into the best "
            f"echo (edge_promise={ep}) to sample fresh red rather than idle a unit"
        ),
        execute_lines=[line],
        payload=dict(hint),
    )


def _count_strong_chains(
    chain_hints: Sequence[Mapping[str, Any]], agent_view: Mapping[str, Any],
) -> int:
    """How many known juice chains bank a "strong" red haul this night.

    A chain is strong when it clears ``value_pyramid._STRONG_CHAIN_RED_MIN``. This
    gates the HIGH-YIELD BLUE grab: blue is only surfaced when the seat has more
    harvesters than strong red chains (a spare unit) or orbital asks for blue."""
    n = 0
    seen_drops: set = set()
    for h in chain_hints or []:
        if not isinstance(h, Mapping):
            continue
        cells = [
            (int(c[0]), int(c[1]))
            for c in (h.get("cells") or [])
            if isinstance(c, (list, tuple)) and len(c) == 2
        ]
        if not cells:
            continue
        # SHORT variants share a drop with their full chain (only one runs), so
        # count a given drop cell once to avoid double-counting harvester demand.
        drop = cells[0]
        if drop in seen_drops:
            continue
        yb = option_economics.yield_breakdown(cells, agent_view)
        if int(yb.get("red_pts") or 0) >= value_pyramid._STRONG_CHAIN_RED_MIN:
            n += 1
            seen_drops.add(drop)
    return n


# A chain is "the same dig" as the seam once more than this share of its cells
# lies inside the redsign footprint. Single-sourced from chain_filter, which
# already uses the identical rule to collapse two chains onto one cluster.
_SEAM_OVERLAP_MAX = chain_filter._OVERLAP_MAX


def _deploy_capacity(
    reg: "Mapping[str, Option]", agent_view: Mapping[str, Any],
) -> int:
    """How many harvesters this menu can actually field tonight.

    Not "how many deploy options exist" — how many the seat can PAY for. A hot
    drop the agent cannot afford is not an option, it is a line of text, and
    counting it is what let Caelum_Compass d6 look like a seven-option menu when
    it was a one-option menu: six hot drops against a probe stock of zero.

    Zero-probe runs all count; probe-funded ones are capped by stock, since two
    options needing a probe each cannot both fly on a single probe.
    """
    stock = packager._probe_stock(agent_view)
    free = paid = 0
    for opt in reg.values():
        if packager._harvester_demand(opt) <= 0:
            continue
        need = packager._probe_demand(opt)
        if need <= 0:
            free += 1
        elif need <= stock:
            paid += 1
    return free + min(paid, stock)


def _chains_off_the_seam(
    chain_hints: Sequence[Mapping[str, Any]],
    agent_view: Mapping[str, Any],
    has_seam: bool,
) -> Tuple[List[Mapping[str, Any]], List[Mapping[str, Any]]]:
    """Drop juice chains that walk INSIDE a fresh redsign footprint (fix 1.5).

    Returns ``(kept, suppressed)``. The suppressed half used to be discarded
    here; it is handed back so ``build_registry`` can put chains BACK when the
    rest of the menu cannot field the fleet (see ``_deploy_capacity``). Menu
    pressure is a nice-to-have — a harvester with nothing legal to do is not.

    On a redsign night the seam patterns already offer that ground, in shapes
    built for a contested race — so a chain over the same cells is a duplicate
    that competes with the play it duplicates, and it crowds the top of a menu
    the agent reads under a token budget (OBS-36).

    The rule is GEOMETRIC, not categorical: a chain elsewhere on the board is
    exactly what a spare harvester should take when it is not attacking, and a
    blanket "no chains on redsign nights" would delete it. Only cells inside the
    broadcast footprint count.

    It also EASES with sign age. A fresh sign dominates because its pure is
    almost certainly still there; once the seam has been fought over for a
    couple of nights that is no longer true, and the rest of the board deserves
    the menu space back. An undated sign never eases — we cannot show it is old.
    """
    hints = [h for h in (chain_hints or []) if isinstance(h, Mapping)]
    if not has_seam or not hints:
        return hints, []

    age = option_economics.sign_age_nights(agent_view)
    if age is not None and age > option_economics._SIGN_FRESH_NIGHTS:
        return hints, []

    footprint = option_economics.redsign_footprint(agent_view)
    if not footprint:
        return hints, []

    kept: List[Mapping[str, Any]] = []
    suppressed: List[Mapping[str, Any]] = []
    for h in hints:
        cells = {
            (int(c[0]), int(c[1]))
            for c in (h.get("cells") or [])
            if isinstance(c, (list, tuple)) and len(c) == 2
        }
        drop = h.get("drop_at")
        if isinstance(drop, (list, tuple)) and len(drop) == 2:
            cells.add((int(drop[0]), int(drop[1])))
        # MAJORITY overlap, not first contact. The footprint is the smear
        # DILATED by _REDSIGN_PUBLIC_RADIUS, so an any-cell test deletes chains
        # that merely clip the outer ring on their way past — Caelum_Compass d6
        # lost a chain whose DROP CELL was outside the footprint entirely, on
        # three of six walk cells. The threshold matches chain_filter's own
        # "more than half its cells = the same dig" rule, so a chain is only
        # suppressed when it really does duplicate the seam ground.
        share = len(cells & footprint) / max(1, len(cells))
        (suppressed if share > _SEAM_OVERLAP_MAX else kept).append(h)
    return kept, suppressed


def build_registry(
    *,
    agent_view: Mapping[str, Any],
    seam_patterns: Sequence[SeamPattern] = (),
    hot_drop_hints: Sequence[Mapping[str, Any]] = (),
    probe_hints: Sequence[Mapping[str, Any]] = (),
    chain_hints: Sequence[Mapping[str, Any]] = (),
    supersede_hints: Sequence[Mapping[str, Any]] = (),
    snap_cover_hints: Sequence[Mapping[str, Any]] = (),
    blue_requested: bool = False,
    harvesters_alive: Optional[int] = None,
    hazard_cells: Collection[Any] = (),
    enemy_probes: Sequence[Mapping[str, Any]] = (),
) -> "OrderedDict[str, Option]":
    """Assemble the ordered ID -> Option registry for this night.

    Order (and thus menu order): seam patterns first (the redsign centrepiece),
    then hot drops, probes, chains, supersedes.

    ``hazard_cells`` is the seat's fog-surviving stripped/GREEN union. It is
    applied HERE rather than in the compiler (OBS-22): the same fact means
    "don't offer this" at menu-build time and "delete the play the agent already
    committed to" at compile time, and only the first is useful to the agent.

    ``enemy_probes`` feeds the fork's EMP salvos. Salvos are registered
    FIRST — ahead even of the grabs — for the same reason the compiler
    fires them at hour one: a cloud's worth is what it denies over the
    following eight hours, so a salvo the thinker reads last is a salvo
    it plans around instead of planning with.
    """
    reg: "OrderedDict[str, Option]" = OrderedDict()

    for opt in _emp_options(agent_view, enemy_probes):
        reg[opt.option_id] = opt

    # v13 — SNAP plays (the third weapon shape). Registered right after the
    # EMP salvos so a thinker reading the menu top-down meets both weapon
    # families in one block, and the SNAP entries carry their own kind
    # ("snap") so the menu header can name them separately.
    for opt in _snap_options(agent_view, enemy_probes):
        reg[opt.option_id] = opt

    for p in seam_patterns or []:
        if isinstance(p, SeamPattern):
            opt = _seam_option(p)
            reg[opt.option_id] = opt

    # A REDSIGN hot-drop hint is ALREADY represented by a seam PATTERN
    # (SMASH_GRAB / BLIND_GRAB), which lands ON the pure. Registering it AGAIN as
    # a raw HDn is a trap: those HD cells sit NEAR the jittered smear, 2-3 cells
    # SHORT of the pure, and the thinker picks the concrete-looking HD over the
    # pattern (the day-2 whiff — banked trace, missed the 765 pure). So drop
    # redsign HDs from the raw menu whenever seam patterns exist; keep BLUESIGN
    # hot-drops (night-1 blue has no pattern representation).
    kept_hotdrops = [
        h for h in (hot_drop_hints or [])
        if isinstance(h, Mapping)
        and not (str(h.get("signal_type") or "") == "redsign" and seam_patterns)
    ]
    for i, h in enumerate(kept_hotdrops, start=1):
        for opt in _hotdrop_shape_options(i, h, agent_view):
            reg[opt.option_id] = opt

    for i, h in enumerate(probe_hints or [], start=1):
        if isinstance(h, Mapping):
            opt = _probe_option(i, h)
            reg[opt.option_id] = opt

    kept_chains, seam_suppressed = _chains_off_the_seam(
        chain_hints, agent_view, bool(seam_patterns),
    )
    for i, h in enumerate(kept_chains, start=1):
        if isinstance(h, Mapping):
            for opt in _chain_shape_options(i, h):
                reg[opt.option_id] = opt

    for i, h in enumerate(supersede_hints or [], start=1):
        if isinstance(h, Mapping):
            opt = _supersede_option(i, h)
            reg[opt.option_id] = opt

    # PHASE-1 VALUE PYRAMID — force-surface pure/mass RED the seat can SEE and
    # reach as a top-priority GRAB, regardless of redsign. A HIGH-YIELD BLUE grab
    # is surfaced only when it would NOT steal a harvester from red: orbital asks
    # for it, or the seat has a spare harvester beyond its strong red chains.
    # Deduped against the cells the menu above already targets.
    strong_chains = _count_strong_chains(chain_hints, agent_view)
    # R2.2 — red and blue number independently, so GRAB1 is always red and a
    # blue grab can never inherit the priority doctrine attaches to GRAB1.
    n_red = n_blue = 0
    for spec in value_pyramid.force_surface_grabs(
        agent_view, existing_targets=_existing_targets(reg),
        blue_requested=blue_requested,
        harvesters_alive=harvesters_alive,
        strong_chain_count=strong_chains,
    ):
        if spec.action == "GRAB_BLUE":
            n_blue += 1
            opt = _grab_option(n_blue, spec)
        else:
            n_red += 1
            opt = _grab_option(n_red, spec)
        reg[opt.option_id] = opt

    # FRONTIER hot-drop (gated last resort): only added when there is no better
    # target on the board (no seam / hot drop) AND known red is trace-only.
    frontier = _frontier_hotdrop_option(
        agent_view, chain_hints, probe_hints,
        has_seam=bool(seam_patterns), has_hotdrop=bool(hot_drop_hints),
    )
    if frontier is not None:
        reg[frontier.option_id] = frontier

    # IDLE-FLEET BACKSTOP. Menu pressure (the seam filter above) is a
    # nice-to-have; a harvester with nothing legal to do is a wasted night. If
    # everything else on the menu still cannot field the fleet, put the
    # seam-suppressed chains BACK, richest first, until it can.
    #
    # Caelum_Compass d6 is why this exists: a live redsign suppressed the
    # chains, probe stock was 0 so all six hot drops read UNAFFORDABLE, and
    # three harvesters were offered ONE playable option. The think pass then
    # described the plays it wanted in prose — it had no menu id to name them
    # with — and resolve_plan, which only accepts ids, dropped two thirds of
    # the plan. Runs silent whenever the menu can already field the fleet.
    if harvesters_alive:
        shortfall = int(harvesters_alive) - _deploy_capacity(reg, agent_view)
        if shortfall > 0 and seam_suppressed:
            restored = sorted(
                seam_suppressed, key=chain_filter._chain_ev, reverse=True,
            )[:shortfall]
            for i, h in enumerate(restored, start=len(kept_chains) + 1):
                for opt in _chain_shape_options(i, h):
                    reg[opt.option_id] = opt

    # SNAP COVER last, so it can name every landing option already registered
    # — including the GRABs and restored chains added above. The hint knows
    # the cell it insures; only the finished menu knows what the agent will
    # call that landing when it picks one.
    for i, h in enumerate(snap_cover_hints or [], start=1):
        if not isinstance(h, Mapping):
            continue
        covers = _xy_tuple(h.get("covers"))
        backed = (
            [oid for oid, o in reg.items() if covers in _option_drop_cells(o)]
            if covers is not None else []
        )
        opt = _snap_cover_option(i, h, backed)
        reg[opt.option_id] = opt

    _apply_hazard(reg, hazard_cells)
    return reg


def _option_drop_cells(opt: Option) -> List[Tuple[int, int]]:
    """Cells this option puts a HARVESTER down on.

    Only drops matter for the hazard veto — a probe landing on stripped green is
    harmless, and a step across one costs the -100 but is priced, not refused.
    """
    pay = opt.payload or {}
    out: List[Tuple[int, int]] = []
    if opt.kind in ("probe", "supersede"):
        return out
    for w in (pay.get("waves") or []):
        if isinstance(w, Mapping):
            c = _num_xy(w.get("drop_at"))
            if c is not None:
                out.append(c)
    for key in ("drop_at", "at"):
        c = _num_xy(pay.get(key))
        if c is not None:
            out.append(c)
    return out


def _option_walk_cells(opt: Option) -> List[Tuple[int, int]]:
    """Cells this option WALKS over.

    Drop cells are excluded: a chain hint's ``cells[0]`` IS its ``drop_at``, and
    a drop on a hazard is handled by removing the option outright, so counting
    it here as well would report the same cell twice under the softer heading.
    """
    pay = opt.payload or {}
    drops = set(_option_drop_cells(opt))
    out: List[Tuple[int, int]] = []
    for key in ("cells", "comb_path"):
        for c in (pay.get(key) or []):
            n = _num_xy(c)
            if n is not None and n not in drops:
                out.append(n)
    for w in (pay.get("waves") or []):
        if isinstance(w, Mapping):
            for c in (w.get("cells") or w.get("comb_path") or []):
                n = _num_xy(c)
                if n is not None and n not in drops:
                    out.append(n)
    return out


def _apply_hazard(reg: "OrderedDict[str, Option]", hazard_cells: Collection[Any]) -> None:
    """Drop options that land a harvester on a hazard; price the rest.

    A hazard cell is one WE stripped to green on an earlier night. Dropping
    there banks nothing and costs -100, so such an option is never worth
    offering. A hazard merely crossed by a walk stays on the menu with the
    penalty stated, because green is legal and the trade may still be right
    (RULEBOOK: "Stepping onto natural GREEN does NOT destroy the harvester").
    """
    hz = {c for c in (_num_xy(v) for v in (hazard_cells or ())) if c is not None}
    if not hz:
        return
    for oid in [k for k, o in reg.items()
                if any(c in hz for c in _option_drop_cells(o))]:
        del reg[oid]
    for opt in reg.values():
        hit = sorted({c for c in _option_walk_cells(opt) if c in hz})
        if hit:
            opt.payload["hazard_in_walk"] = [list(c) for c in hit]


# ── menu render (for the thinker prompt) ────────────────────────────────
_KIND_HEADERS = [
    ("emp", "EMP SALVOS — ids BLIND_SCORCH* / EMP_SCORCH / SCORCH_REDSIGN (you own a charge; ONE hour-slot, fires at hour 1, 8h cloud, kills probes, friendly fire ON. BLIND_SCORCH* shapes the salvo to leave a hole you then OCCUPY — prefer it: same denial, and you end the night on the ground)"),
    ("snap", "SNAP ROUNDS — ids SNAP_KILL / SNAP_STRIKE (you own a SNAP; ONE cell, ONE hour, 100 blue — cheapest ordnance in the game. Unique property: SNAP resolves ABOVE the vision snapshot (§4.9.4), so a rival landing into the SNAPped cell THIS HOUR is refused, not next. This is the smash-and-grab counter: an EMP fired on the same cell only denies from NEXT hour. Use for cheap single-cell denial, single-eye probe kill, or to block a rival's H1 landing on a contested pure)"),
    ("grab", "PRIORITY RED GRABS — ids GRAB* (mass/pure RED you can SEE or reach — the highest-value take, no probe; grab it FIRST)"),
    ("seam", "REDSIGN PATTERNS (multi-wave campaigns — pick & order by case; when you hold an EMP charge and a redsign is live, SMASH_THEN_LOCK (your own seam) and RACE_CRASH_EMP (shared vision) compound plays package the smash + salvo + follow-up harvester waves as ONE id — pick the one matching the REDSIGN STATE. For a rival redsign you cannot see, BLIND_SCORCH in the EMP SALVOS block above IS the shield play — no compound needed)"),
    ("hotdrop", "HOT DROPS (probe+drop into fresh fog this night)"),
    ("probe", "PROBE PLACEMENTS (open fresh vision)"),
    ("snap_cover", "SNAP COVER — ids PRSNAP* (a SECOND probe over a landing only one probe can see; optional insurance, not a required move)"),
    ("chain", "JUICE CHAINS (walk known red, no probe)"),
    ("blue_grab", "HIGH-YIELD BLUE GRABS — ids BL* (rich blue you can SEE and grab with no risk — use when you NEED blue, or you have a spare harvester that would otherwise be wasted on low-yield red; BL* is NOT a GRAB* and does not inherit its priority)"),
    ("supersede", "SUPERSEDES (spend a spare probe to BLIND a rival's probe — deny their next landing & vision; yours survives)"),
    ("frontier", "FRONTIER HOT-DROP (last resort — known red is trace-only)"),
]

# One-line "what this kind of play brings to the table" — rendered under each
# group header so the thinker weighs the KIND before the individual options.
_KIND_BLURB = {
    "emp": "spend a charge you already own to blind or lock out a rival. Buys TEMPO, banks nothing tonight, and the hour it costs is an hour a harvester did not walk — so take it when denial is worth more than the chain you drop for it, and never over a CERTAIN pure/mass grab.",
    "grab": "mass/pure RED you can SEE — the highest-value bank, no probe, lowest risk; take it FIRST.",
    "seam": "redsign campaigns — SMASH your own pure; ATTACK a rival's (blind the finder + blind-walk the fresh, mass-rich seam). CONTEST_DENY is the demoted ahead/certainty play (confirm now, smash tomorrow).",
    "hotdrop": "spend a probe to open fresh fog and harvest BLIND this night.",
    "probe": "pure vision — banks 0 tonight, buys tomorrow's targets.",
    "snap_cover": "insurance against SNAP, offered because a landing worth having rests on a single probe and a rival's published blue could be a SNAP. Banks nothing itself. Worth a probe only if you think the primary is a target tonight — if the rival is unarmed, or the landing is one you could shrug off, spend the probe on fresh ground instead.",
    "chain": "walk RED you already see — zero probe, guaranteed legal, lowest risk.",
    "blue_grab": "rich blue you can SEE — zero risk, no probe; grab it when you need blue or a spare harvester would otherwise idle (RED always outranks it for a scarce harvester).",
    "supersede": "deny an enemy landing by blinding their probe — best when you hold >1 probe or your red chains already bank high (a spare probe is free denial); if you're TRAILING, blind the LEADER's freshest probe first. Skip probes about to expire — their vision is already spent.",
    "frontier": "last resort — known red is trace-only; blind-sample the best echo.",
}

# Tier print order for the yield breakdown (richest first).
_TIER_ORDER = ("pure", "mass", "vein", "trace")


def _fmt_cell(c: Tuple[int, int]) -> str:
    return f"({int(c[0])},{int(c[1])})"


def _fmt_walk(walk: Sequence[Tuple[int, int]]) -> str:
    if not walk:
        return ""
    if len(walk) == 1:
        return _fmt_cell(walk[0])
    return _fmt_cell(walk[0]) + " -> " + " ".join(_fmt_cell(c) for c in walk[1:])


def _fmt_blind(blind: Mapping[str, Any]) -> str:
    """The expectation behind a blind comb over a redsign smear (fix 0.3).

    R2.8 — leads with POOLED odds (the chance the comb crosses the pure ANYWHERE
    along it) rather than the best single cell, and states what the jackpot is
    worth if you take it. The old line reported a per-cell fraction next to an
    expectation that was already discounted by that fraction, so a 6-cell comb
    over a 25-cell smear read as a one-in-twenty-five raffle ticket and lost to
    any undiscounted chain on sight.
    """
    shape = (
        f"{blind['halo_density']:.0%} red at ~{blind['halo_purity']} purity"
    )
    basis = (
        f"non-pure cells priced at what this seam actually shows — {shape}"
        if blind.get("halo_measured")
        else f"you can see NONE of this seam, so non-pure cells are priced off "
             f"the seams you CAN see — {shape}"
    )
    odds = float(blind.get("pure_odds") or blind.get("best_pure_odds") or 0.0)
    jackpot = int(blind.get("unclaimed_pure_pts") or 0)
    cover = ""
    if blind.get("smear_cells"):
        cover = (
            f" ({int(blind['cells'])} of ~{int(blind['smear_cells'])} smear "
            f"cells walked)"
        )
    head = (
        f"~{odds:.0%} chance this comb CROSSES the pure{cover} — the pure is "
        f"worth ~+{jackpot} on its own; expected red over the whole route "
        f"~+{int(blind['expected_pts'])}"
    )
    stale = ""
    survival = float(blind.get("pure_survival", 1.0))
    nights = int(blind.get("nights_held") or 0)
    if survival < 1.0:
        stale = (
            f" NOTE — this sign was broadcast {nights} night(s) ago and the "
            f"FINDER has held exact vision of the pure ever since, so it is "
            f"only ~{survival:.0%} likely to still be on the board; the "
            f"expectation above is discounted for that. What you are attacking "
            f"for is the UNWORKED HALO and the DENIAL, not the jackpot. Expect "
            f"stripped ground (-100 a cell) where they have already walked."
        )
    return (
        f"{head}. That expectation is ALREADY discounted by the odds; a juice "
        f"chain's number is not discounted at all, so do not read the two as "
        f"like for like. WIDE variance ({basis}).{stale}"
    )


def _fmt_yield(
    yb: Mapping[str, Any],
    *,
    probed: bool = True,
    blind: Optional[Mapping[str, Any]] = None,
) -> str:
    """Colour-broken yield line from an option_economics yield dict.

    ``probed`` is False for a walk-in attack that launches NO probe (nothing
    lands on the fog at all), so the blind-cell caveat must not promise coverage
    that never arrives.

    R2.6 — even when ``probed`` is True the probe does not make tonight's walk
    sighted. The whole plan is committed at turn start, so no step can be
    re-aimed on what the probe finds; what the launch actually buys is LEGALITY
    for the drop, DENIAL if it lands on a rival probe, and INTEL for tomorrow.
    Calling it a reveal invited the agent to price a blind comb as an adaptive
    one and then feel cheated by the variance.

    ``blind`` is the smear expectation from ``option_economics.blind_estimate``,
    present only when fog cells fall inside a redsign smear.
    """
    tiers = yb.get("red_tiers") or {}
    red_pts = int(yb.get("red_pts") or 0)
    blue = int(yb.get("blue_fissile") or 0)
    green_pen = int(yb.get("green_penalty") or 0)
    green_n = int(yb.get("green_cells") or 0)
    unknown = int(yb.get("unknown_cells") or 0)

    # A fully-blind maneuver (all cells fog). Fix 0.3 (OBS-30): "unknown" is
    # still the truth about WHICH cells hold what, but it is not the truth about
    # what the walk is worth — a redsign smear carries per-cell odds on a
    # guaranteed pure. Rendering the bare word here made every attack lose to any
    # priced alternative on sight, whatever the odds actually were.
    if not tiers and blue == 0 and green_n == 0 and unknown:
        reveal = (
            "your probe makes the drop LEGAL and lights this ground for TOMORROW "
            "— it cannot re-aim tonight's steps, which are fixed now"
            if probed else
            "no probe at all — you walk in blind"
        )
        head = (
            f"yield: unknown ({unknown} blind fog cell(s) — {reveal}; drop cell "
            "auto-harvests whatever is under it)"
        )
        return (head + f"  [{_fmt_blind(blind)}]") if blind else head

    parts: List[str] = []
    if tiers:
        tb = ", ".join(f"{tiers[t]} {t}" for t in _TIER_ORDER if tiers.get(t))
        parts.append(f"red ~+{red_pts} ({tb})")
    else:
        parts.append(f"red ~+{red_pts}")
    parts.append(f"blue {blue}" + (" fissile" if blue else ""))
    parts.append(f"green {green_pen}" + (f" ({green_n})" if green_n else ""))
    line = "yield: " + " · ".join(parts)
    extra: List[str] = []
    # Fix 0.2 (OBS-43) — say how much of that red is a GUESS. An ECHO cell is
    # real evidence and still counts, but it was last seen N nights ago and a
    # rival may have taken it since, so a total that blends the two reads more
    # confident than the board warrants.
    echo_pts = int(yb.get("echo_pts") or 0)
    if echo_pts and red_pts:
        age = int(yb.get("echo_age") or 0)
        aged = f", {age}n stale" if age else ""
        extra.append(
            f"~+{echo_pts} of that red is ECHO{aged} — "
            f"{int(yb.get('echo_cells') or 0)} cell(s) nobody has seen since, "
            "so a rival may already have stripped them"
        )
    if unknown:
        # Same reasoning as the fully-blind branch: when the fog sits inside a
        # smear it has odds, and "+N fog" alone reads as pure downside.
        extra.append(
            f"+{unknown} fog · {_fmt_blind(blind)}" if blind else f"+{unknown} fog"
        )
    if int(yb.get("over_hold") or 0):
        extra.append(f"{int(yb['over_hold'])} cell(s) past the 6-parcel hold won't bank")
    if extra:
        line += "  [" + "; ".join(extra) + "]"
    return line


def _fmt_crush(
    crush: Mapping[str, Any],
    self_notes: Optional[Sequence[str]] = None,
) -> str:
    bits: List[str] = []
    if crush.get("self"):
        if self_notes:
            # Tier + future-utility aware verdicts (option_economics): a high-value
            # cell on an expiring/last-night/being-extracted probe is a worthwhile
            # trade, not a blanket "avoid".
            bits.extend(self_notes)
        else:
            cells = ", ".join(_fmt_cell(c) for c in crush["self"])
            bits.append(f"CRUSHES YOUR probe {cells} (loses its vision — avoid)")
    if crush.get("enemy"):
        cells = ", ".join(_fmt_cell(c) for c in crush["enemy"])
        bits.append(f"SUPERSEDES enemy probe {cells} (blinds them — good)")
    return "crush: " + ("; ".join(bits) if bits else "none")


def _fmt_risk(risk: Sequence[Any]) -> str:
    level, reason = risk
    return f"collision risk: {level} ({reason})"


def _fmt_overlap(claims: Sequence[Tuple[Tuple[int, int], str, Sequence[str]]]) -> str:
    """The 'you cannot bank this twice' caveat (fix 0.3, OBS-30/OBS-4)."""
    bits = []
    for cell, tier, others in claims:
        rivals = ", ".join(others[:3]) + ("…" if len(others) > 3 else "")
        bits.append(f"{_fmt_cell(cell)} {tier} (also in {rivals})")
    return (
        "ALREADY COUNTED ELSEWHERE: " + "; ".join(bits) + " — whichever option "
        "runs FIRST banks these; the later one arrives to stripped green, worth "
        "0 and -100 each. Do NOT add these yields together."
    )


def _econ_detail_lines(
    opt: Option,
    econ: Mapping[str, Any],
    overlap: Optional[Sequence[Tuple[Tuple[int, int], str, Sequence[str]]]] = None,
) -> List[str]:
    """The indented economics lines rendered under an option's headline.

    Deploy options (grab/seam/hotdrop/chain/frontier) show walk + yield + a
    combined crush/risk line. Vision-only options (probe/supersede) show what the
    reveal buys plus any crush/risk instead of a fabricated yield.

    ``overlap`` lists this option's mass/pure cells that another option also
    banks — see ``option_economics.overlap_claims``.
    """
    lines: List[str] = []
    if opt.kind in ("probe", "supersede"):
        pay = opt.payload or {}
        bits: List[str] = []
        if pay.get("area_gain") is not None:
            bits.append(f"area_gain={pay.get('area_gain')}")
        if pay.get("edge_promise") is not None:
            bits.append(f"edge_promise={pay.get('edge_promise')}")
        prefix = "vision only" + (f" ({' '.join(bits)})" if bits else "")
        lines.append(f"{prefix} — banks 0 tonight, buys tomorrow's targets")
        crush = _fmt_crush(econ["crush"], econ.get("crush_self_notes"))
        risk = _fmt_risk(econ["risk"])
        lines.append(crush + " · " + risk if crush != "crush: none" else risk)
        return lines

    walk = econ.get("walk") or []
    if walk:
        n = int(econ.get("length") or len(walk))
        lines.append(f"walk: {_fmt_walk(walk)}  ({n} cell{'s' if n != 1 else ''})")
    lines.append(_fmt_yield(
        econ.get("yield") or {},
        probed=_option_probe_cost(opt) > 0,
        blind=econ.get("blind"),
    ))
    if overlap:
        lines.append(_fmt_overlap(overlap))
    hz = (opt.payload or {}).get("hazard_in_walk") or []
    if hz:
        cells = " ".join(_fmt_cell(c) for c in hz)
        lines.append(
            f"HAZARD in walk: {cells} — WE stripped {'these' if len(hz) > 1 else 'this'} "
            "to green on an earlier night; nothing left to bank and -100 each at "
            "season end. Legal, but only worth crossing for what lies beyond."
        )
    lines.append(
        _fmt_crush(econ["crush"], econ.get("crush_self_notes"))
        + " · " + _fmt_risk(econ["risk"])
    )
    return lines


def _option_probe_cost(opt: Option) -> int:
    """How many probes this option SPENDS from stock — mirrors the packager's
    spend sites exactly (packager._pack_*): probe/supersede/frontier = 1;
    hot-drop = its probe + any supersede; seam = per-wave probe + supersede;
    grabs and juice chains = 0 (their whole point on a probe-starved night)."""
    pay = opt.payload or {}
    k = opt.kind
    if k in ("probe", "supersede", "frontier"):
        return 1
    if k == "hotdrop":
        return (1 if pay.get("probe_at") is not None else 0) + (
            1 if pay.get("supersede") is not None else 0
        )
    if k == "seam":
        n = 0
        for w in (pay.get("waves") or []):
            if isinstance(w, Mapping):
                n += (1 if w.get("probe_at") is not None else 0) + (
                    1 if w.get("supersede") is not None else 0
                )
        return n
    return 0  # grab, chain — zero-probe by construction


def _ceding_lines(
    seam_opts: "Sequence[Option]", agent_view: Mapping[str, Any],
) -> List[str]:
    """R2.11 — what it COSTS to leave each live redsign alone.

    The menu prices every redsign play as "my expected gain against my risk",
    which is only one side of the ledger. A pure nobody contests does not
    evaporate: a rival banks it, and the board swings by twice its value. Framed
    the old way, the choice on s69 night 5 read "141 against 266" and the chain
    won; framed honestly it reads "bank 266 and hand over 765, or contest".

    This is DISCLOSURE, not a rule. It states the counterfactual and stops. No
    option is forced, nothing is re-ranked, and the judgement stays the agent's —
    which on the evidence is better than any gate we would write.
    """
    beacons: Dict[Tuple[int, int], bool] = {}
    for opt in seam_opts:
        pay = opt.payload or {}
        b = _num_xy(pay.get("beacon"))
        if b is None:
            continue
        beacons.setdefault(b, bool(pay.get("mine")))
    if not beacons:
        return []
    jackpot = option_economics.pure_ship_points()
    ages = option_economics.sign_ages_by_beacon(agent_view)
    out: List[str] = []
    for beacon, mine in sorted(beacons.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        whose = "YOUR seam" if mine else "a RIVAL's seam"
        nights = 0 if mine else int(ages.get(beacon, 0))
        if nights <= 0:
            out.append(
                f"IF YOU DO NOT CONTEST {_fmt_cell(beacon)} ({whose}): the pure "
                f"there is worth ~+{jackpot} to whoever banks it. Declining does "
                f"not score you 0 — it scores a rival +{jackpot}, so the swing "
                f"between taking it and ceding it is ~{2 * jackpot}. Weigh a "
                f"safe chain against THAT, not against 0."
            )
        else:
            # OBS-53 — the swing is only real while the pure is. Stating +735 in
            # the present tense about a seam the finder has worked for a night
            # is how the seat talked itself onto four stripped cells.
            out.append(
                f"IF YOU DO NOT CONTEST {_fmt_cell(beacon)} ({whose}): a pure is "
                f"worth ~+{jackpot}, so the swing is ~{2 * jackpot} — BUT this "
                f"sign was broadcast {nights} night(s) ago and the finder has "
                f"held exact vision of it ever since. Assume the jackpot is "
                f"PROBABLY ALREADY BANKED. Contest it for the UNWORKED HALO and "
                f"to deny their next run, and expect stripped ground (-100 a "
                f"cell) on the routes they have already walked — do NOT spend "
                f"the night as though the +{jackpot} were still sitting there."
            )
    return out


def format_menu_block(
    registry: "Mapping[str, Option]",
    *,
    agent_view: Optional[Mapping[str, Any]] = None,
    weapon_estimates: Optional[Mapping[str, Any]] = None,
    probe_stock: Optional[int] = None,
    harvesters_alive: Optional[int] = None,
    day: Optional[int] = None,
    day_cap: Optional[int] = None,
) -> str:
    """Render the ID'd option menu for the THINKER to select from.

    Authoritative for LEGAL geometry — every pick is an ID from here, so the
    model never composes a coordinate — but deliberately NOT framed as the whole
    board any more (v12): WORLD VIEW / OUT-OF-GRID carry value no option
    surfaces, and the PER-TURN PROCEDURE's step 6 reconciles them against the
    picks made here.

    Empty when the registry is empty. Grouped by kind (with a one-line "what this
    brings" blurb per group); every option leads with the stable ID the thinker
    echoes into ``plan``. When ``agent_view`` is supplied each option is annotated
    (via :mod:`.option_economics`) with its WALK, expected YIELD (red/blue/green
    on the engine's real scoring model), CRUSHES (yours vs enemy), and enemy
    COLLISION RISK — so the model never has to reconstruct geometry or cross-
    reference a separate hazard block. Options are sorted affordable-first within
    each group and unaffordable ones are tagged explicitly against ``probe_stock``.
    """
    if not registry:
        return ""
    by_kind: Dict[str, List[Option]] = {}
    for opt in registry.values():
        by_kind.setdefault(opt.group, []).append(opt)

    header = (
        "OPTION MENU (SELECT by ID — put the IDs you choose, in execution "
        "order, into \"plan\"; the geometry is pre-filled for you). Every option "
        "carries its walk, expected yield, crushes and collision risk, and its "
        "geometry is PRE-VALIDATED — so you never reconstruct any of it, and "
        "your picks are IDs from this menu, never coordinates you compose. This "
        "menu is authoritative for what is LEGAL tonight, but it is not the "
        "whole board: WORLD VIEW and OUT-OF-GRID carry value it does not "
        "surface. Use them to choose BETWEEN these options and to trim them — "
        "not to invent new ones."
    )
    if harvesters_alive is not None:
        n = int(harvesters_alive)
        header += (
            f"\n  HARVESTER BUDGET: you have {n} harvester(s) alive this night, "
            f"and each makes ONE outing per night (RULEBOOK §3.9.2). So select AT "
            f"MOST {n} harvest run(s) (GRABS / JUICE CHAINS / HOT DROPS / FRONTIER "
            f"— one harvester each). You CANNOT run two chains on one harvester. "
            f"If you pick more than {n}, the packager keeps only the {n} "
            f"highest-value run(s) and DROPS the rest — so choose your best "
            f"{n} up front (a wave/seam campaign spends one harvester per wave)."
        )
    if probe_stock is not None:
        header += (
            f"\n  PROBE BUDGET: you have {int(probe_stock)} probe(s) in stock this "
            "night. Options are tagged with their probe cost; the sum of your "
            "picks' costs must not exceed your stock. Zero-probe GRABS / JUICE "
            "CHAINS always execute — prefer them when probes are scarce."
        )
    # Fix 0.3 (OBS-30) — a menu-WIDE pass before anything renders. Each yield is
    # honest on its own and the set of them is not: the same pure is sold by
    # every option whose walk crosses it, and the agent picks two and adds.
    # Needs every walk up front, hence a pass of its own.
    overlaps: Dict[str, List[Any]] = {}
    if agent_view is not None:
        walks = {
            o.option_id: option_economics.walk_cells(o.payload or {})
            for opts in by_kind.values() for o in opts
        }
        overlaps = option_economics.overlap_claims(walks, agent_view)

    lines: List[str] = [header]
    for kind, kind_header in _KIND_HEADERS:
        opts = by_kind.get(kind)
        if not opts:
            continue
        # Affordable-first within the group (stable: keeps generation order among
        # equally-affordable options) so the thinker reads what it can DO tonight
        # before what it can't.
        if probe_stock is not None:
            opts = sorted(
                opts, key=lambda o: _option_probe_cost(o) > int(probe_stock)
            )
        lines.append(f" {kind_header}:")
        blurb = _KIND_BLURB.get(kind)
        if blurb:
            lines.append(f"   ({blurb})")
        if kind == "seam" and agent_view is not None:
            for cl in _ceding_lines(opts, agent_view):
                lines.append(f"   {cl}")
        for opt in opts:
            cost = _option_probe_cost(opt)
            if cost:
                tag = f"  · needs {cost} probe{'s' if cost != 1 else ''}"
                if probe_stock is not None and cost > int(probe_stock):
                    tag += f" — UNAFFORDABLE (you have {int(probe_stock)})"
            else:
                tag = "  · no probe"
            lines.append(opt.menu_line() + tag)
            # v12 fix 3.1 — the WHY sits directly under the geometry, above the
            # economics, so the trade is read before the numbers are weighed.
            if opt.rationale:
                lines.append(f"       WHY: {opt.rationale}")
            if agent_view is not None:
                econ = option_economics.annotate(
                    opt.payload or {}, agent_view, weapon_estimates,
                    day=day, day_cap=day_cap,
                )
                for dl in _econ_detail_lines(opt, econ, overlaps.get(opt.option_id)):
                    lines.append(f"       {dl}")
    return "\n".join(lines) + "\n"


# ── plan recovery (prose CoT -> ordered IDs) ────────────────────────────
def recover_plan_from_prose(
    reasoning: str, registry: "Mapping[str, Option]", *, max_ids: int = 6,
) -> List[str]:
    """Salvage an ordered plan from the thinker's PROSE when the JSON ``plan``
    came back empty (F4/O8: the thinker names ``BLIND_GRAB`` / ``SS1`` in its
    chain-of-thought but forgets to echo the IDs into the structured field, so
    ``resolve_plan([])`` returns nothing and the decisive seam geometry — incl.
    the finder-probe supersede — never reaches the mover).

    We scan the reasoning for EXACT registry IDs (token-bounded, case-insensitive
    — ``#`` and ``_`` count as ID chars so ``SMASH_GRAB`` and ``BLIND_GRAB#2`` are
    matched whole and not as substrings of each other), and return the IDs in the
    order they FIRST appear (that ordering encodes the thinker's wave priority),
    deduped and capped. Returns [] when nothing recognisable is present — the
    caller then falls back to the classic directive block exactly as before, so
    this can only ADD a recovered plan, never corrupt a good one.
    """
    if not reasoning or not registry:
        return []
    text = reasoning.upper()
    hits: List[tuple] = []
    for oid in registry:
        oid_u = oid.upper()
        pat = re.compile(
            r"(?<![A-Z0-9_#])" + re.escape(oid_u) + r"(?![A-Z0-9_#])"
        )
        m = pat.search(text)
        if m is not None:
            hits.append((m.start(), oid))
    hits.sort(key=lambda t: t[0])
    out: List[str] = []
    for _pos, oid in hits:
        if oid not in out:
            out.append(oid)
        if len(out) >= max_ids:
            break
    return out


# ── resolver (thinker plan -> mover recipe) ─────────────────────────────
# A coordinate decoration the thinker sometimes bolts onto a bare id, e.g.
# ``PR1_at_22_18`` / ``PR1@(22,18)`` / ``PR1 at 22,18`` instead of ``PR1``. We
# strip it so the id still resolves rather than being dropped as unknown (which
# silently lost probes — the night-6 "0 probes launched" wart).
_ID_DECORATION = re.compile(r"(?i)[ _]?(?:at[_ ]?|@)\(?\d+[\d ,_)]*$")


def _normalize_option_id(raw: str) -> str:
    """Bare option id: upper-cased, with any trailing coordinate tail stripped."""
    key = raw.strip().upper()
    return _ID_DECORATION.sub("", key).strip("_ ")


def resolve_plan(
    plan_ids: Sequence[str], registry: "Mapping[str, Option]",
) -> List[Option]:
    """Expand the thinker's chosen IDs to concrete options, in its order.

    Case-insensitive match against the registry; a bare-id fallback strips any
    coordinate decoration the thinker appended (``PR1_at_22_18`` -> ``PR1``).
    Unknown / duplicate IDs are dropped (the menu is authoritative). Returns []
    when nothing resolves.
    """
    if not plan_ids or not registry:
        return []
    upper = {k.upper(): v for k, v in registry.items()}
    out: List[Option] = []
    seen: set = set()
    for raw in plan_ids:
        if not isinstance(raw, str):
            continue
        key = raw.strip().upper()
        opt = upper.get(key) or upper.get(_normalize_option_id(raw))
        if opt is None or opt.option_id in seen:
            continue
        seen.add(opt.option_id)
        out.append(opt)
    return out


# Option kinds that actually DEPLOY a harvester (bank RED). Probes/supersedes
# alone bank nothing — the final-night collapse was a plan of only these.
_DEPLOY_KINDS = frozenset({"grab", "blue_grab", "seam", "hotdrop", "chain", "frontier"})


def _is_deploy_option(opt: Option) -> bool:
    """True iff this option actually commits a harvester to bank RED.

    A CONTEST_DENY seam (Part B) has kind ``seam`` but every wave is
    ``deny_only`` — it spends probes only, banks nothing — so it must NOT count
    as a deploy for the final-night guard.
    """
    if opt.kind not in _DEPLOY_KINDS:
        return False
    if opt.kind == "seam":
        waves = (opt.payload or {}).get("waves") or []
        if not waves:
            return True  # no wave detail (test/legacy) -> assume it deploys
        return any(
            isinstance(w, Mapping) and not w.get("deny_only") for w in waves
        )
    return True


def ensure_final_night_deploy(
    selected: Sequence[Option],
    registry: "Mapping[str, Option]",
    *,
    alive_harvesters: int,
    is_final_night: bool,
) -> Tuple[List[Option], bool]:
    """R5 — guarantee the final-night plan actually deploys a harvester.

    After a chain loss the thinker sometimes picks an all-probes/supersede plan
    on the LAST night — probes only pay off on a tomorrow that never comes, so
    the seat banks zero (the vs-HEUR d7 defensive collapse). This is a thinker
    posture bug the deterministic packager cannot fix (it faithfully compiles a
    no-deploy plan). When it is the final night and a harvester is alive but the
    resolved plan deploys none, prepend the best available deploy option (registry
    order: seam > hot-drop > chain > frontier). No-op otherwise. Returns
    ``(options, injected)``.
    """
    out = list(selected)
    if not is_final_night or alive_harvesters <= 0:
        return out, False
    if any(_is_deploy_option(o) for o in out):
        return out, False
    for opt in registry.values():
        if _is_deploy_option(opt):
            return [opt] + out, True
    return out, False


def format_execute_block(selected: Sequence[Option]) -> str:
    """Render the resolved options as a PRIORITY-ORDERED recipe for the MOVER.

    The list is the thinker's committed plan, in PRIORITY order (item 1 first).
    The mover PACKAGES it — it owns the mechanics (which unit runs which item,
    the exact hour each move lands, trimming to fit the 21h night / 6-parcel hold
    / legality) but it does NOT re-plan: it may not drop a higher item, re-target
    a cell, or turn a probe into a drop (or vice-versa), or add an off-menu move.

    Empty when nothing was selected (the harness then falls back to the classic
    strategist-directive block, preserving v7/v8 behaviour).
    """
    if not selected:
        return ""
    n = len(selected)
    lines = [
        "EXECUTE THIS PLAN — it is your reasoning pass's committed decision, in "
        "PRIORITY ORDER (item 1 = highest priority). You are the PACKAGER, not a "
        "planner. Your job and ONLY your job:",
        "  * WHAT & WHERE are FIXED. Emit EVERY probe / supersede / drop / walk "
        "line below as a real move. Do NOT drop an item, move a target cell, turn "
        "a probe into a drop (or the reverse), or add ANY move that is not listed "
        "(no off-menu hot drops, no extra probes to 'use every unit' — the plan "
        "already sized the fleet).",
        "  * HOW & WHEN are YOURS. Assign each item to a specific unit, choose the "
        "hour each move lands, and interleave the items across the night.",
        "  * IF SOMETHING MUST GIVE, CUT FROM THE BOTTOM. When hours / units / "
        f"probes run short, drop the LAST item(s) first (item {n} before item "
        f"{n - 1} …), and within a kept item shorten its WALK TAIL — never skip "
        "or shorten a higher-priority item to fit a lower one. Item 1 lands.",
        "  * Legality still binds: if an exact drop cell is not drop-legal, use "
        "the nearest legal cell from the SAME item's geometry — do not re-target "
        "to a different objective.",
        "PLAN (priority order):",
    ]
    for i, opt in enumerate(selected, start=1):
        for j, line in enumerate(opt.execute_lines):
            prefix = f" {i}. " if j == 0 else "     "
            lines.append(f"{prefix}{line}")
    return "\n".join(lines) + "\n"
