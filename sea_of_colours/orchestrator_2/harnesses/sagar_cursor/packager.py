"""v10 R1 — the DETERMINISTIC packager (compiler back-end).

The thinker (front-end) does the creative work: read state, pick posture, choose
and order tactical options. The resolver expands those IDs to concrete geometry
(:class:`..agency.Option` payloads). This module is the back-end: it COMPILES
that recipe into wire-format moves — a pure function with fully-defined semantics.

Why deterministic (see SEED69_FIXPLAN.md R1): every hallucination in the v9 audit
was in the LLM executor stage — wrong drop cells, multi-drop cycles on one unit,
zero-walk final-night drops, invented cell contents. The executor runs on the
SAME snapshot as the thinker (no new information), so any latitude only subtracts
value. Compiling the recipe in Python makes coordinate faithfulness and the
one-drop-per-unit hold structurally guaranteed, kills a Haiku round-trip, and
removes a whole failure surface. The LLM mover is kept ONLY as the no-recipe
fallback (handled in the harness).

The output still passes through the shared move sanitizer (legality / step
clipping / collision reroute) exactly like the LLM path did — the packager owns
the WHAT & WHERE (from the recipe) and HOW (unit/probe budgeting, contiguous
steps); the sanitizer owns final legality.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from sea_of_colours.orchestrator_2.harnesses.sagar_cursor._v7.probe_hints import (
    _orbit_harvester_ids,
)
from sea_of_colours.orchestrator_2.harnesses.sagar_cursor import (
    option_economics as econ,
)

# Kinds that each COMMIT ONE HARVESTER for a single outing (RULEBOOK §3.9.2 —
# one outing per harvester per night). ``seam`` is handled separately because a
# multi-wave campaign consumes one harvester PER non-deny wave.
_SINGLE_HARVESTER_KINDS = frozenset({"grab", "blue_grab", "chain", "hotdrop", "frontier"})
_PROBE_KINDS = frozenset({"probe", "supersede"})
# A supersede banks nothing but denies a rival's vision — worth roughly a mid
# fresh-vision gain when ranking which probes to keep under a stock shortage.
_SUPERSEDE_BASELINE_VALUE = 120.0


# Part C, REMOVED (fix 2.10, OBS-27). ``chaff_react`` is the thinker's flag for
# "I expect a jam tonight", and the packager used to translate it into a hard
# 2-step cap on EVERY chain. That translation was the compiler authoring: a step
# past the second is not ILLEGAL — the engine takes a 5-step walk under chaff
# perfectly happily — so how long a route runs is a value judgement and belongs
# to the agent.
#
# It was also, by measurement, the single most damaging thing the compiler did.
# On ``SNAP_408ddd46_d4_p1`` the agent's walk-in was routed correctly onto a
# pure(255) four steps out; the cap stopped it at two and banked trace, in every
# run of four consecutive suite sweeps, while ``compiler_clean`` read 100%
# because a cap was never recorded as an intervention.
#
# The flag is still honoured — as ADVICE the agent acts on by picking a shorter
# option — and every night it is set is now reported rather than enforced, so
# the intent is visible without the compiler acting on the agent's behalf.


def _probe_stock(agent_view: Mapping[str, Any]) -> int:
    return int((agent_view.get("orbit") or {}).get("probe_stock") or 0)


def _cell(v: Any) -> Optional[Tuple[int, int]]:
    if isinstance(v, (list, tuple)) and len(v) == 2:
        try:
            return (int(v[0]), int(v[1]))
        except (TypeError, ValueError):
            return None
    return None


def _steps_between(
    a: Tuple[int, int], b: Tuple[int, int],
) -> List[Tuple[int, int]]:
    """Manhattan-1 waypoints from ``a`` (exclusive) to ``b`` (inclusive).

    Guarantees every emitted step is exactly one N/S/E/W cell from the last —
    the engine adjacency rule — regardless of how the recipe spaced its comb
    cells (walk x first, then y). A no-op when ``a == b``.
    """
    out: List[Tuple[int, int]] = []
    cx, cy = a
    while cx != b[0]:
        cx += 1 if b[0] > cx else -1
        out.append((cx, cy))
    while cy != b[1]:
        cy += 1 if b[1] > cy else -1
        out.append((cx, cy))
    return out


def _steps_between_y_first(
    a: Tuple[int, int], b: Tuple[int, int],
) -> List[Tuple[int, int]]:
    """The other L between ``a`` and ``b`` — y first, then x.

    Same endpoint and the same Manhattan length as :func:`_steps_between`;
    only the corner differs. That is what makes it usable as a detour that
    costs nothing.
    """
    out: List[Tuple[int, int]] = []
    cx, cy = a
    while cy != b[1]:
        cy += 1 if b[1] > cy else -1
        out.append((cx, cy))
    while cx != b[0]:
        cx += 1 if b[0] > cx else -1
        out.append((cx, cy))
    return out


def _steps_avoiding(
    a: Tuple[int, int], b: Tuple[int, int], wake: Set[Tuple[int, int]],
) -> Tuple[List[Tuple[int, int]], bool]:
    """Manhattan waypoints from ``a`` to ``b``, preferring the L that misses ``wake``.

    This is a RELOCATION, not a cut. Both candidate paths are the same length
    and land on the same cell, so nothing about the agent's route — its reach,
    its target, its cost — changes. Only the corner moves. That keeps it inside
    the house rule that route *shape* belongs to the agent (OBS-27) while still
    declining to walk ground an earlier wave already stripped, which is worth
    zero and costs -100 (RULEBOOK: RED -> GREEN on harvest).

    Returns the waypoints and whether a detour was taken.
    """
    primary = _steps_between(a, b)
    if not wake or not any(step in wake for step in primary):
        return primary, False

    # Only the OTHER corner is considered, and this is a measured choice rather
    # than a lazy one.
    #
    # A full search over every monotone staircase looks strictly better: same
    # length, same destination, and it also solves the case where both L-shapes
    # are dirty. Built and measured, it was WORSE -- 4 wake collisions against
    # 0, and a point of suite score, on otherwise identical configurations.
    #
    # The reason is that this is a sequencing problem wearing a geometry
    # problem's clothes. Choosing a different clean route for one wave changes
    # the ground THAT wave strips, which moves the collision onto the wave
    # behind it. The flip deviates only when forced, and always to the same
    # alternative, so the wake it leaves stays predictable to everything
    # compiled after it. The greedy search re-plans each leg in isolation and
    # keeps handing the next unit a board it did not expect.
    alternate = _steps_between_y_first(a, b)
    hits_primary = sum(1 for step in primary if step in wake)
    hits_alternate = sum(1 for step in alternate if step in wake)
    if hits_alternate < hits_primary:
        return alternate, True
    # Neither corner clears it -- usually because the destination itself is the
    # wake cell, which is the caller's call to price, not ours to refuse. Keep
    # the original: never truncate, never lengthen.
    return primary, False


# ── self-inflicted drop-legality (crush ordering) ──────────────────────────
#
# A drop is legal only where the seat has a LIVE sensor beacon AT THAT HOUR.
# The menu checks that against the probes alive at the START of the night —
# but a harvester that drops or steps on a probe's own cell CRUSHES it, and
# every later drop that depended on that disk then fails with "no live sensor
# beacon", taking its whole chain down with it.
#
# Observed three times in the captures: night 5 of 871128e7 drops on
# probe_p1_7@(16,22) at H01, then drops (14,20) at H05 — a cell only that probe
# lit. The thinker cannot see the interaction (each option is independently
# legal when offered) and the engine only reports it the next morning.
#
# Legality is not a judgement call, so we fix it deterministically: run the
# dependent chain BEFORE the one that blinds it. Pure reordering — no pick is
# added, dropped, or re-aimed.
_DEPLOY_KINDS = frozenset(
    {"grab", "blue_grab", "seam", "hotdrop", "chain", "frontier"}
)


def _live_probe_cells(agent_view: Mapping[str, Any]) -> List[Tuple[int, int]]:
    """Cells holding one of our probes that is still alive tonight."""
    out: List[Tuple[int, int]] = []
    for e in ((agent_view.get("entities") or {}).get("mine") or []):
        if not isinstance(e, Mapping) or str(e.get("type") or "") != "probe":
            continue
        nr = e.get("nights_remaining")
        if isinstance(nr, (int, float)) and int(nr) <= 0:
            continue
        cell = _cell(e.get("pos") or e.get("at"))
        if cell is not None:
            out.append(cell)
    return out


def _footprint(payload: Mapping[str, Any]) -> Tuple[
    List[Tuple[int, int]], List[Tuple[int, int]]
]:
    """``(drop cells, every cell the unit occupies)`` for one option payload."""
    drops: List[Tuple[int, int]] = []
    walked: List[Tuple[int, int]] = []
    frames: List[Mapping[str, Any]] = [payload]
    frames += [w for w in (payload.get("waves") or []) if isinstance(w, Mapping)]
    for fr in frames:
        for key in ("drop_at", "at"):
            c = _cell(fr.get(key))
            if c is not None:
                drops.append(c)
        for key in ("cells", "comb_path", "walk"):
            for raw in (fr.get(key) or []):
                c = _cell(raw)
                if c is not None:
                    walked.append(c)
    return drops, drops + walked


def _order_for_probe_support(
    selected: Sequence[Any], agent_view: Mapping[str, Any],
) -> Tuple[List[Any], List[str]]:
    """Reorder runs so none is blinded by an earlier pick's probe crush.

    An option is CONSTRAINED when every probe lighting one of its drop cells is
    crushed by a different option in the same plan — then it must run first.
    Options with other support (a second disk, a harvester plus) are untouched,
    and a run that crushes the probe it is itself dropping onto is fine: the
    drop resolves before the crush.

    Stable: only genuinely blocked options move, and a dependency cycle is left
    exactly as the thinker ordered it.
    """
    try:
        from sea_of_colours.game.tuning import probe_vision_radius
        rr = int(probe_vision_radius()) ** 2
    except Exception:  # pragma: no cover - defensive
        rr = 16
    probes = _live_probe_cells(agent_view)
    runs = [o for o in selected if str(getattr(o, "kind", "")) in _DEPLOY_KINDS]
    if len(runs) < 2 or not probes:
        return list(selected), []

    drops: Dict[int, List[Tuple[int, int]]] = {}
    crushes: Dict[int, set] = {}
    for i, opt in enumerate(runs):
        d, occupied = _footprint(getattr(opt, "payload", None) or {})
        drops[i] = d
        crushes[i] = {p for p in probes if p in set(occupied)}

    # i must precede j when j crushes every probe that lights one of i's drops.
    after: Dict[int, set] = {i: set() for i in range(len(runs))}
    for i in range(len(runs)):
        for cell in drops[i]:
            support = {
                p for p in probes
                if (p[0] - cell[0]) ** 2 + (p[1] - cell[1]) ** 2 <= rr
            }
            if not support:
                continue  # legal some other way (harvester plus) — not our call
            for j in range(len(runs)):
                if j != i and support <= crushes[j]:
                    after[j].add(i)

    if not any(after.values()):
        return list(selected), []

    order: List[int] = []
    remaining = list(range(len(runs)))
    while remaining:
        ready = [i for i in remaining if not (after[i] - set(order))]
        if not ready:  # cycle — respect the thinker's ordering
            order.extend(remaining)
            break
        pick = ready[0]
        order.append(pick)
        remaining.remove(pick)
    if order == list(range(len(runs))):
        return list(selected), []

    log = [
        "reordered runs so a probe crush does not blind a later drop: "
        + " -> ".join(str(getattr(runs[i], "option_id", i)) for i in order)
    ]
    reordered = [runs[i] for i in order]
    out: List[Any] = []
    it = iter(reordered)
    for opt in selected:
        out.append(next(it) if str(getattr(opt, "kind", "")) in _DEPLOY_KINDS
                   else opt)
    return out, log


# ── value-based inventory reconciliation (RULEBOOK §3.9.2) ─────────────────
def _harvest_value(payload: Mapping[str, Any], agent_view: Mapping[str, Any]) -> float:
    """A scalar VALUE for a harvester option, engine-scored over its walk.

    ``red_pts`` dominates (it is the only thing that scores); BLUE fissile is
    worth roughly half a point each as spend-budget; GREEN is a straight
    penalty. Used to rank which runs to KEEP when there are fewer harvesters
    than selected runs — so we never drop the richer run just because the
    thinker ordered it later (the day-2 CH1-vs-GRAB1 bug)."""
    try:
        yb = econ.yield_breakdown(econ.walk_cells(payload), agent_view)
    except Exception:
        return 0.0
    return (
        float(yb.get("red_pts") or 0)
        + 0.5 * float(yb.get("blue_fissile") or 0)
        + float(yb.get("green_penalty") or 0)
    )


def _probe_priority(opt: Any) -> float:
    """Ranking value for a probe/supersede option under a stock shortage."""
    payload = getattr(opt, "payload", None) or {}
    try:
        v = float(payload.get("edge_promise") or payload.get("area_gain") or 0.0)
    except (TypeError, ValueError):
        v = 0.0
    if str(getattr(opt, "kind", "")) == "supersede":
        v = max(v, _SUPERSEDE_BASELINE_VALUE)
    return v


def _seam_wave_demand(opt: Any) -> int:
    """How many harvesters a seam campaign commits (one per non-deny wave)."""
    payload = getattr(opt, "payload", None) or {}
    return sum(
        1
        for w in (payload.get("waves") or [])
        if isinstance(w, Mapping) and not w.get("deny_only")
    )


# What a seam wave is worth BEYOND the red it banks: it blinds a rival's finder,
# denies them the pure the disk was lighting, and leaves us positioned on the
# seam tomorrow. None of that shows up in ``red_pts``, so without a premium a
# campaign is undervalued — but a premium is also the thing that must not be
# allowed to become an exemption.
#
# Scaled against the prize: a pure(255) is 765 pts, and blinding the finder
# denies a rival their shot at it. Crediting a quarter of that respects the
# denial while still letting a 253-pt chain beat a 70-pt seam — the exact
# inversion in OBS-21, where a campaign took both harvesters before any value
# was compared and the night banked 81 red.
_SEAM_DENIAL_PREMIUM = 190.0


def _seam_premium(opt: Any) -> float:
    """Premium for the waves of this campaign that actually deny something."""
    payload = getattr(opt, "payload", None) or {}
    n = sum(
        1
        for w in (payload.get("waves") or [])
        if isinstance(w, Mapping) and w.get("supersede") is not None
    )
    return _SEAM_DENIAL_PREMIUM * n


def _harvester_demand(opt: Any) -> int:
    """Harvesters this option consumes. 0 for probe-only options."""
    kind = str(getattr(opt, "kind", ""))
    if kind == "seam":
        # A deny-only campaign (CONTEST_DENY) spends probes, not harvesters, so
        # it demands 0 and never competes for a unit.
        return _seam_wave_demand(opt)
    return 1 if kind in _SINGLE_HARVESTER_KINDS else 0


def _probe_demand(opt: Any) -> int:
    """Probes this option spends, counting the ones buried inside a run.

    A seam wave's ``supersede`` / ``probe_at`` and a hot-drop's enabling probe
    are real stock draws even though the option's KIND is not ``probe``.
    """
    kind = str(getattr(opt, "kind", ""))
    if kind in _PROBE_KINDS:
        return 1
    payload = getattr(opt, "payload", None) or {}
    if kind == "seam":
        n = 0
        for w in (payload.get("waves") or []):
            if not isinstance(w, Mapping):
                continue
            n += sum(1 for k in ("supersede", "probe_at") if w.get(k) is not None)
        return n
    if kind == "frontier":
        return 1
    return sum(1 for k in ("supersede", "probe_at") if payload.get(k) is not None)


def coverage_note(
    selected: Sequence[Any],
    agent_view: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> str:
    """Fix 2.7 — tell the PLAN pass what its own THINK pass left in orbit.

    A harvester left in orbit banks nothing and a probe held back is simply
    unspent, so SOMETHING has always forced them out: the packager's completion
    pass and the sanitizer's T6 guard. Both pick by heuristic value with no idea
    what the agent was doing, which is how a unit meant to hold a seam ended up
    twenty cells south of it (OBS-20, OBS-27).

    This moves the requirement UPSTREAM. It is a deterministic count, injected
    between the two passes, so the AGENT spends the shortfall and stays the
    author. Returns "" when the plan already uses everything — silence is the
    normal case and the note must not become boilerplate.
    """
    alive = _orbit_harvester_ids(agent_view)
    stock = _probe_stock(agent_view)
    used_h = sum(_harvester_demand(o) for o in selected)
    used_p = sum(_probe_demand(o) for o in selected)
    spare_h, spare_p = len(alive) - used_h, stock - used_p
    if spare_h <= 0 and spare_p <= 0:
        return ""

    chosen = {str(getattr(o, "option_id", "")) for o in selected}
    lines = ["=== YOUR PLAN DOES NOT USE EVERYTHING YOU HAVE ==="]
    if spare_h > 0:
        idle = alive[used_h:] or alive[-spare_h:]
        unused = [
            str(getattr(o, "option_id", "?"))
            for oid, o in registry.items()
            if str(oid) not in chosen and _harvester_demand(o) > 0
        ]
        lines.append(
            f"{spare_h} of your {len(alive)} harvester(s) stay in ORBIT and bank "
            f"NOTHING: {', '.join(idle[:4])}."
        )
        if unused:
            lines.append(
                f"  Deploy options still on the menu: {', '.join(unused[:8])}."
            )
    if spare_p > 0:
        unused_p = [
            str(getattr(o, "option_id", "?"))
            for oid, o in registry.items()
            if str(oid) not in chosen and _probe_demand(o) > 0
            and _harvester_demand(o) == 0
        ]
        lines.append(
            f"{spare_p} of your {stock} probe(s) go UNSPENT. A probe you hold "
            f"back is not reserved for tomorrow — it is simply not fired."
        )
        if unused_p:
            lines.append(
                f"  Probe/denial options still on the menu: "
                f"{', '.join(unused_p[:8])}."
            )
    lines.append(
        "ADD them to your plan, or state in your reasoning why leaving them "
        "idle beats using them. Either answer is acceptable; saying nothing is "
        "not, because it hands the choice to the compiler."
    )
    return "\n".join(lines)


def _ranked_value(opt: Any, agent_view: Mapping[str, Any]) -> float:
    """The score a harvester option is ranked on — seams included.

    Seams used to skip ranking entirely. Now they are scored like everything
    else, with their denial worth added explicitly so it can be seen, argued
    with, and tuned (OBS-21).
    """
    base = _harvest_value(getattr(opt, "payload", None) or {}, agent_view)
    if str(getattr(opt, "kind", "")) == "seam":
        return base + _seam_premium(opt)
    return base


def reconcile_selected(
    selected_options: Sequence[Any],
    agent_view: Mapping[str, Any],
) -> Tuple[List[Any], List[Dict[str, Any]]]:
    """Reconcile the thinker's selected options against LIVE inventory.

    RULEBOOK §3.9.2: each harvester makes ONE outing per night, so the seat can
    run at most ``harvesters_alive`` chains. When the thinker selects more runs
    than that (or more probes than stock), keep the HIGHEST-VALUE ones and drop
    the rest — never silently drop by plan order (the day-2 gap where the richer
    red run was cut because it came second).

    Seams and singles rank in ONE pool (OBS-21). A campaign used to reserve its
    waves' harvesters BEFORE any value was compared, so a 70-pt seam could take
    both units and a 253-pt chain was cut without ever being weighed. A seam's
    genuine extra worth — the denial its supersede buys — is now an explicit
    premium on its score rather than an exemption from being scored.

    Returns ``(kept_options, report)`` where ``report`` is an ordered list of
    ``{id, kind, status, value, reason}`` covering EVERY selected option
    (``status`` ∈ ``kept`` | ``dropped``) — the structured record the harness
    persists so next turn's LAST NIGHT can show PLAN → COMPILED → DROPPED & WHY.
    """
    opts = [o for o in (selected_options or []) if o is not None]
    harvesters = len(_orbit_harvester_ids(agent_view))
    probe_stock = _probe_stock(agent_view)

    probe_opts = [o for o in opts if str(getattr(o, "kind", "")) in _PROBE_KINDS]
    deploy_opts = [o for o in opts if _harvester_demand(o) > 0]

    dropped_reason: Dict[str, str] = {}

    # ONE pool, ranked by value per harvester committed, filled greedily. Value
    # density is the right key because a 2-wave campaign must clear the bar for
    # BOTH units it takes, not just for one.
    ranked = sorted(
        deploy_opts,
        key=lambda o: _ranked_value(o, agent_view) / max(1, _harvester_demand(o)),
        reverse=True,
    )
    budget = harvesters
    kept_desc: List[str] = []
    shortfall: List[Tuple[Any, int, int]] = []
    for o in ranked:
        need = _harvester_demand(o)
        if need <= budget:
            budget -= need
            kept_desc.append(
                f"{getattr(o, 'option_id', '?')} "
                f"(~{round(_ranked_value(o, agent_view))})"
            )
        else:
            shortfall.append((o, need, budget))

    # Reasons are written after the pass so each can name what was kept INSTEAD.
    # The old message asserted "kept the higher-value run(s)" while dropping a
    # 253 for a 70 — and since this is the one compiler decision the agent ever
    # sees, a false one teaches the wrong lesson with authority (OBS-21).
    kept_txt = ", ".join(kept_desc) or "nothing"
    for o, need, left in shortfall:
        dropped_reason[o.option_id] = (
            f"needs {need} harvester(s), {left} left of {harvesters} alive — each "
            f"makes ONE outing/night (RULEBOOK §3.9.2). This run scored "
            f"~{round(_ranked_value(o, agent_view))}; kept instead: {kept_txt}"
        )

    if len(probe_opts) > probe_stock:
        ranked_p = sorted(probe_opts, key=_probe_priority, reverse=True)
        for o in ranked_p[probe_stock:]:
            dropped_reason[o.option_id] = (
                f"only {probe_stock} probe(s) in stock — kept the best {probe_stock}"
            )

    report: List[Dict[str, Any]] = []
    for o in opts:
        kind = str(getattr(o, "kind", ""))
        is_probe = kind in _PROBE_KINDS
        value = (
            round(_probe_priority(o))
            if is_probe
            else round(_ranked_value(o, agent_view))
        )
        oid = str(getattr(o, "option_id", "?"))
        if oid in dropped_reason:
            report.append(
                {
                    "id": oid,
                    "kind": kind,
                    "status": "dropped",
                    "value": value,
                    "reason": dropped_reason[oid],
                }
            )
        else:
            report.append(
                {"id": oid, "kind": kind, "status": "kept", "value": value, "reason": ""}
            )

    kept_options = [
        o for o in opts if str(getattr(o, "option_id", "?")) not in dropped_reason
    ]
    return kept_options, report


class _Packer:
    """Mutable budget state while compiling one night's recipe."""

    def __init__(
        self,
        agent_view: Mapping[str, Any],
        *,
        forbidden_cells: Optional[set] = None,
        avoid_cells: Optional[set] = None,
        live_red_cells: Optional[set] = None,
        chaff_short: bool = False,
    ) -> None:
        self.harvesters: List[str] = list(_orbit_harvester_ids(agent_view))
        self.probe_budget: int = _probe_stock(agent_view)
        self.moves: List[Dict[str, Any]] = []
        self.log: List[str] = []
        self._h_idx = 0
        self._probed_cells: set = set()
        self._drop_cells: set = set()
        # Part A1 — persistent stripped/GREEN union (fog-surviving). ENGINE FACT:
        # a drop here auto-harvests green for a penalty and banks nothing.
        #
        # As of workstream C this is a BACKSTOP, not the primary guard — the menu
        # no longer offers a hazardous drop cell at all (OBS-22), so reaching this
        # branch means something upstream failed. It is therefore rare, loud, and
        # RELOCATES within the option's own footprint before it deletes anything.
        self.hazard: set = set(forbidden_cells or ())
        # The thinker's own ``avoid`` list. A GUESS, not a fact, and kept strictly
        # apart from the hazard set: unioning the two made an agent hint read as
        # engine truth and deleted a correct attack under a message naming a cause
        # that did not apply (OBS-15). A soft avoid never overrides an explicit
        # pick — the contradiction is logged and the pick wins (OBS-20 item 3).
        self.avoid: set = set(avoid_cells or ())
        # Part A2 — the cells that are LIVE-RED for the seat right now
        # (``red_tiles[].freshness=='fresh'``). On a CONTESTED seam a blind sweep
        # must only STEP onto cells we can actually SEE are red at plan time —
        # never blind-walk a fogged neighbour that the rival may already have
        # stripped to green (the seed-56 self-harm generalised to the walk).
        self.live_red: set = set(live_red_cells or ())
        # TONIGHT'S wake — every cell a unit already emitted this night has
        # landed on or walked. Deliberately NOT unioned into ``hazard``: hazard
        # is an engine fact carried across nights, this is a consequence of the
        # order we are packing in right now, and merging the two is what made an
        # earlier fix report the wrong cause (OBS-15's lesson, applied here).
        #
        # Why it exists: seam patterns already exclude their OWN earlier waves
        # (``_value_drop(exclude=wave1_wake)``, and UNBEATEN_FLANK threads the
        # same set into its comb after OBS-54). Nothing does that ACROSS two
        # separately selected options, because each is built without knowing
        # what else the agent will pick. Measured over the baseline bake that is
        # 16 of 60 turns, 15 of them re-entering one cell: the pure a previous
        # wave had already lifted.
        self.wake: Set[Tuple[int, int]] = set()
        # Part C — the thinker's ``chaff_react`` flag. REPORTED, never enforced
        # (fix 2.10): it shortens nothing, and simply records that the agent
        # said it expected a jam so the night can be read back honestly.
        self.chaff_short: bool = bool(chaff_short)

    # ── resource draws ─────────────────────────────────────────────
    def next_harvester(self) -> Optional[str]:
        if self._h_idx < len(self.harvesters):
            u = self.harvesters[self._h_idx]
            self._h_idx += 1
            return u
        return None

    def idle_harvesters(self) -> List[str]:
        return self.harvesters[self._h_idx:]

    def spend_probe(self, at: Any) -> bool:
        cell = _cell(at)
        if cell is None or self.probe_budget <= 0:
            return False
        if cell in self._probed_cells:
            return False  # never launch two probes onto the same cell
        self.moves.append({"a": "probe", "at": [cell[0], cell[1]]})
        self._probed_cells.add(cell)
        self.probe_budget -= 1
        return True

    # ── run transactions ───────────────────────────────────────────
    # A run is "spend the enabling probes, then emit the chain", and the probes
    # have to be emitted FIRST so their disks open before the drop. If the chain
    # then fails to compile, those probes were spent for a run that never
    # happened — the card showed exactly that on SNAP_ac1c55bf_d6_p4, where
    # SECURE_MASS was deleted but its probe still fired (OBS-27, fix 2.5).
    def begin(self) -> Tuple[int, int, set, set, int]:
        return (len(self.moves), self.probe_budget, set(self._probed_cells),
                set(self._drop_cells), self._h_idx)

    def rollback(self, mark: Tuple[int, int, set, set, int]) -> None:
        n, budget, probed, drops, h_idx = mark
        del self.moves[n:]
        self.probe_budget = budget
        self._probed_cells = probed
        self._drop_cells = drops
        self._h_idx = h_idx

    def _relocate_drop(
        self, drop: Tuple[int, int], comb: Sequence[Any],
    ) -> Optional[Tuple[int, int]]:
        """Nearest legal landing cell inside this option's OWN footprint.

        Restricted to cells the option already names, so a relocation can never
        turn the play into something the agent did not pick — anything further
        afield is a different option and belongs on the menu instead.
        """
        best: Optional[Tuple[int, int]] = None
        best_d = None
        for c in comb or []:
            cell = _cell(c)
            if cell is None:
                continue
            if cell in self.hazard or cell in self._drop_cells:
                continue
            d = abs(cell[0] - drop[0]) + abs(cell[1] - drop[1])
            if best_d is None or d < best_d:
                best, best_d = cell, d
        return best

    # ── chain emit (drop -> contiguous steps -> pickup) ────────────
    def emit_chain(
        self, unit: str, drop_at: Any, comb: Sequence[Any],
        *, contested: bool = False, blind_walk: bool = False,
    ) -> bool:
        drop = _cell(drop_at)
        if drop is None:
            return False
        if drop in self._drop_cells:
            # NOT ILLEGAL, despite what this branch used to claim. The engine
            # refuses two drops on one square only in the SAME HOUR
            # (``simulator._maybe_resolve_simultaneous_drops``), and a seat acts
            # once per hour, so two of OUR OWN drops can never collide. The second
            # unit simply lands on ground the first stripped: -100, then it walks
            # on and banks the rest. Deleting the run to dodge that penalty threw
            # away the whole contested ring on SNAP_ac1c55bf_d6_p4 (OBS-27/42) —
            # a far worse trade. Ship it, price it, name the better option.
            self.log.append(
                f"drop {list(drop)} is the SECOND landing on that cell tonight, so "
                "it auto-harvests ground your own earlier wave already stripped "
                "(-100) before the walk. KEPT — it is legal and the walk still "
                "banks. To avoid the penalty pick a second-wave option that opens "
                "on the RING instead of on the pure."
            )
        if drop in self.avoid and drop not in self.hazard:
            # The agent picked an option and, in the same breath, listed its drop
            # cell as one to avoid. An advisory hint must not delete a deliberate
            # pick, so the pick wins and the contradiction is reported (OBS-15).
            self.log.append(
                f"avoid {list(drop)} CONTRADICTS your own pick and was ignored — "
                "`avoid` cannot cancel an option you selected; drop a pick you "
                "don't want instead"
            )
        if drop in self.hazard:
            # The landing cell is known-stripped/GREEN. Costly (-100) but LEGAL,
            # so relocating inside the option's own footprint is an improvement we
            # offer, never a condition of shipping: when no better cell exists the
            # run goes as ordered rather than being deleted (OBS-27). A harvester
            # left in orbit banks nothing at all, which is strictly worse than one
            # that eats a penalty and then works.
            moved = self._relocate_drop(drop, comb)
            if moved is None:
                self.log.append(
                    f"drop {list(drop)} is known stripped/GREEN (hazard memory, an "
                    "engine fact) and no cell in this "
                    "option's own footprint is clean, so it lands there and "
                    "auto-harvests green (-100). KEPT — legal, and the walk still "
                    "banks; an idle harvester banks nothing."
                )
            else:
                self.log.append(
                    f"relocated drop {list(drop)} -> {list(moved)}: original is "
                    "known stripped/GREEN (hazard memory); rest of the walk "
                    "unchanged"
                )
                comb = [c for c in (comb or []) if _cell(c) != moved]
                drop = moved
        # NOTE: NO cuts of any kind below this line. Route shape is the AGENT's
        # call — the OPTION MENU flags each chain's exposure, so the thinker
        # sheds a bad tail by PICKING differently. Since fix 2.10 that includes
        # the chaff cap, the last cut the compiler made.
        self.moves.append({"a": "drop", "unit": unit, "at": [drop[0], drop[1]]})
        self._drop_cells.add(drop)
        cur = drop
        steps_emitted = 0
        n_green = 0   # priced-and-kept green steps, warned once
        n_blind = 0   # priced-and-kept fogged steps on a contested seam
        n_detours = 0  # legs re-cornered to miss tonight's own wake
        n_skipped = 0  # waypoints an earlier wave had already stripped
        walked: List[Tuple[int, int]] = [drop]
        for c in comb or []:
            nxt = _cell(c)
            if nxt is None or nxt == cur:
                continue
            if nxt in self.wake:
                # A waypoint an earlier wave already stripped. Standing on it
                # banks nothing and costs -100, so it is not a target worth
                # keeping. Dropping it is not a CUT in the OBS-27 sense: the
                # walk still runs to every remaining target, so the route keeps
                # its reach and its intent — it just stops paying to revisit
                # ground we already took.
                n_skipped += 1
                if n_skipped == 1:
                    self.log.append(
                        f"skipped waypoint {list(nxt)} — an earlier wave already "
                        "stripped it, so it is synthetic green worth 0 and -100 "
                        "to stand on. The rest of the walk is unchanged."
                    )
                continue
            stopped = False
            leg, detoured = _steps_avoiding(cur, nxt, self.wake)
            if detoured:
                n_detours += 1
                self.log.append(
                    f"routed {unit} around {list(cur)} -> {list(nxt)} the other "
                    "way: the direct leg re-entered ground an earlier wave "
                    "already stripped. Same length, same destination — only the "
                    "corner moved."
                )
            for sx, sy in leg:
                # Both checks below used to TRUNCATE the walk. Neither is a
                # legality question — a green step costs -100 and a fogged step
                # is a gamble, but the engine accepts both — so both are value
                # judgements that belong to the agent (OBS-27). They now price
                # the route and let it run, once per reason so the log stays
                # readable.
                if (sx, sy) in self.hazard:
                    n_green += 1
                    if n_green == 1:
                        self.log.append(
                            f"walk enters known stripped/GREEN at {[sx, sy]} "
                            "(-100 each). KEPT — your route, your call; pick a "
                            "SHORT variant or `avoid` these cells to shed them."
                        )
                elif contested and not blind_walk and (sx, sy) not in self.live_red:
                    # A CONTESTED seam is live-confirmed, but its fogged
                    # neighbours may already have been stripped by the rival who
                    # confirmed it. Real risk, priced on the menu, not ours to veto.
                    n_blind += 1
                    if n_blind == 1:
                        self.log.append(
                            f"contested walk steps into FOG at {[sx, sy]} — not "
                            "live-red, so the rival may already have stripped it. "
                            "KEPT — the menu priced this exposure and you took it."
                        )
                # A BLIND_WALK wave (CASE-2 attack on a FOGGED rival seam)
                # deliberately steps onto fog — the pure is jittered, so the sweep
                # has to range over unseen cells. Nothing below refuses a step;
                # every route the agent ordered is emitted as ordered.
                self.moves.append({"a": "step", "unit": unit, "to": [sx, sy]})
                cur = (sx, sy)
                walked.append(cur)
                steps_emitted += 1
            if stopped:
                break
        if n_green > 1 or n_blind > 1:
            self.log.append(
                f"{unit} route totals: {n_green} green step(s) "
                f"(~-{n_green * 100}), {n_blind} fogged step(s) on a contested seam"
            )
        if n_detours:
            self.log.append(
                f"{unit} took {n_detours} wake detour(s) — same reach, same "
                "cells banked, without paying -100 to re-walk our own strip"
            )
        # Only now does this unit's ground count as wake: a unit never collides
        # with itself (it strips as it goes), and the predicate that measures
        # this scores cross-unit re-entry only.
        self.wake.update(walked)
        self.moves.append({"a": "pickup", "unit": unit})
        return True


def _pack_seam(pk: _Packer, payload: Mapping[str, Any]) -> None:
    """A multi-wave redsign campaign: each wave gets its own harvester."""
    waves = sorted(
        (w for w in (payload.get("waves") or []) if isinstance(w, Mapping)),
        key=lambda w: (int(w.get("wave") or 0), int(w.get("earliest_hour") or 0)),
    )
    for w in waves:
        # Part B — a DENY-ONLY wave commits NO harvester: it only spends its
        # supersede + confirm probe (blind the finder, light a fogged rival seam
        # for a real strike tomorrow). Never a blind harvester drop onto green.
        if w.get("deny_only"):
            if w.get("supersede") is not None:
                pk.spend_probe(w.get("supersede"))
            if w.get("probe_at") is not None:
                pk.spend_probe(w.get("probe_at"))
            continue
        unit = pk.next_harvester()
        if unit is None:
            pk.log.append(
                f"cut seam wave {w.get('wave')}: no harvester left (fleet sized)"
            )
            continue
        mark = pk.begin()
        if w.get("supersede") is not None:
            pk.spend_probe(w.get("supersede"))
        if w.get("probe_at") is not None:
            pk.spend_probe(w.get("probe_at"))
        ok = pk.emit_chain(
            unit, w.get("drop_at"), w.get("comb_path") or [],
            contested=bool(w.get("contested")),
            blind_walk=bool(w.get("blind_walk")),
        )
        if not ok:
            pk.rollback(mark)
            pk.log.append(
                f"seam wave {w.get('wave')} could not compile (no landing cell) — "
                "its harvester and probes were RETURNED to the pool, not spent"
            )


def _pack_hotdrop(pk: _Packer, payload: Mapping[str, Any]) -> None:
    # The old "already has a drop -> skip" guard is gone: a repeat landing is
    # legal and merely costs -100, and emit_chain now says so on the card (2.1).
    unit = pk.next_harvester()
    if unit is None:
        pk.log.append("cut hot-drop: no harvester left")
        return
    mark = pk.begin()
    if payload.get("supersede") is not None:
        pk.spend_probe(payload.get("supersede"))
    if payload.get("probe_at") is not None:
        pk.spend_probe(payload.get("probe_at"))
    if not pk.emit_chain(unit, payload.get("drop_at"), payload.get("comb_path") or []):
        pk.rollback(mark)
        pk.log.append(
            "hot-drop could not compile (no landing cell) — its harvester and "
            "probes were RETURNED to the pool, not spent"
        )


def _pack_chain(pk: _Packer, payload: Mapping[str, Any]) -> None:
    unit = pk.next_harvester()
    if unit is None:
        pk.log.append("cut juice chain: no harvester left")
        return
    mark = pk.begin()
    if not pk.emit_chain(unit, payload.get("drop_at"), payload.get("cells") or []):
        pk.rollback(mark)
        pk.log.append("juice chain could not compile (no landing cell)")


def _pack_probe(pk: _Packer, payload: Mapping[str, Any]) -> None:
    if not pk.spend_probe(payload.get("at")):
        pk.log.append("cut probe: no stock left")


def _pack_supersede(pk: _Packer, payload: Mapping[str, Any]) -> None:
    if not pk.spend_probe(payload.get("probe_at")):
        pk.log.append("cut supersede: no stock left")


def _pack_frontier(pk: _Packer, payload: Mapping[str, Any]) -> None:
    at = _cell(payload.get("at"))
    if at is None:
        return
    unit = pk.next_harvester()
    if unit is None:
        pk.log.append("cut frontier hot-drop: no harvester left")
        return
    mark = pk.begin()
    pk.spend_probe(at)               # un-fog the blind cell
    if not pk.emit_chain(unit, at, []):   # drop auto-harvests; pick up, no walk
        pk.rollback(mark)
        pk.log.append(
            "frontier hot-drop could not compile — its harvester and probe were "
            "RETURNED to the pool, not spent"
        )


_PROBE_ONLY_KINDS = {"probe", "supersede"}


def _is_probe_only(opt: Any) -> bool:
    """Does this option spend ONLY probes, with no harvester outing?

    Used to push standalone probe launches behind every outing (fix 2.4). A
    probe that ENABLES a drop is not standalone — it lives inside its own run's
    payload (``probe_at`` / ``supersede``) and still fires before that drop.
    """
    kind = str(getattr(opt, "kind", "") or "")
    if kind in _PROBE_ONLY_KINDS:
        return True
    if kind == "seam":
        waves = (getattr(opt, "payload", None) or {}).get("waves") or []
        return bool(waves) and all(
            isinstance(w, Mapping) and w.get("deny_only") for w in waves
        )
    return False


def _pack_emp(pk: "_Packer", payload: Mapping[str, Any]) -> None:
    """RUNG 2c — compile an EMP salvo to the one wire move the engine takes.

    Three things this has to get right, and each was learned the expensive way
    in ``docs/TEACHING_WEAPONS.md``:

    * **A salvo is ONE move and ONE hour**, not one per missile. It consumes a
      single slot of the seat's 21 (§3.10), and the whole salvo lands that hour.
      Emitting three moves would burn three hours and three charges to do the
      job of one.
    * **The engine's shape, not ours.** ``EmpLaunchMove`` takes a primary ``at``
      plus ``extra_ats`` for the rest of the salvo; it does not take a list in
      ``at``.
    * **Fire it first.** Probes die on contact, so an early kill costs the rival
      a night of vision while a late one destroys a picture they have already
      used — and an 8h cloud lit at hour 15 of a 21-hour Nox loses most of
      itself to Aurora. The salvo is therefore hoisted to the front of the
      queue rather than landing wherever the plan happened to name it.
    """
    if str(payload.get("verb") or "") == "chaff_flare":
        # Targetless by nature: it blankets the board. Same tempo argument as a
        # salvo, so it takes the same hour-1 slot.
        pk.moves.insert(0, {"a": "chaff_flare"})
        pk.log.append(
            "chaff_flare hoisted to hour 1 — every rival action cancelled for "
            "its duration; our own units pass through untouched"
        )
        return

    targets = [
        cell for cell in (_cell(t) for t in (payload.get("targets") or []))
        if cell is not None
    ]
    if not targets:
        return
    move: Dict[str, Any] = {"a": "emp_launch", "at": [targets[0][0], targets[0][1]]}
    if len(targets) > 1:
        move["extra_ats"] = [[x, y] for x, y in targets[1:]]
    # Hour 1. Everything already queued keeps its order behind it.
    pk.moves.insert(0, move)
    pk.log.append(
        f"emp_launch hoisted to hour 1 at {[list(t) for t in targets]} — "
        f"one charge, one move, one hour; "
        f"{payload.get('probes_killed', 0)} rival probe(s) in the blast"
    )


_DISPATCH = {
    "seam": _pack_seam,
    "hotdrop": _pack_hotdrop,
    "weapon": _pack_emp,
    # v11 Phase-1 force-surfaced VALUE-PYRAMID grab — drop + contiguous walk,
    # identical wire shape to a juice chain, so it compiles through _pack_chain.
    "grab": _pack_chain,
    "blue_grab": _pack_chain,
    "chain": _pack_chain,
    "probe": _pack_probe,
    "supersede": _pack_supersede,
    "frontier": _pack_frontier,
}


def _complete_utilization(
    pk: _Packer,
    agent_view: Mapping[str, Any],
    *,
    chain_hints: Sequence[Mapping[str, Any]],
    probe_hints: Sequence[Mapping[str, Any]],
    supersede_hints: Sequence[Mapping[str, Any]],
) -> None:
    """R1 completion pass — "all harvesters dropped, all probes used".

    A GUARANTEE, not a judgement (see the design note): after the committed plan,
    deploy any still-idle harvester onto the best UNUSED offered chain, and spend
    any leftover probe stock on offered probe / supersede targets. Draws only
    from already-offered geometry, so it can never invent an off-menu cell.
    """
    for unit in pk.idle_harvesters():
        # Deploy the idle unit onto the HIGHEST-VALUE unused offered chain (not
        # merely the first listed), so "use all harvesters" also means "use them
        # on the best remaining geometry".
        candidates = [
            h
            for h in (chain_hints or [])
            if _cell(h.get("drop_at")) is not None
            and _cell(h.get("drop_at")) not in pk._drop_cells
        ]
        if not candidates:
            break
        chosen = max(candidates, key=lambda h: _harvest_value(h, agent_view))
        # Consume the pool slot so idle_harvesters() shrinks in lock-step.
        pk.next_harvester()
        pk.emit_chain(unit, chosen.get("drop_at"), chosen.get("cells") or [])
        pk.log.append(
            f"completion: {unit} was idle, so it was deployed onto an offered "
            f"chain from {list(_cell(chosen.get('drop_at')) or [])} "
            f"(~{round(_harvest_value(chosen, agent_view))}) — you did not "
            f"pick this"
        )

    if pk.probe_budget > 0:
        # Supersedes first (cheap denials), then remaining probe targets ranked
        # by promise, so leftover stock lands on the BEST offered cells.
        ranked_probes = sorted(
            (probe_hints or []),
            key=lambda h: float(h.get("edge_promise") or h.get("area_gain") or 0),
            reverse=True,
        )
        for h in list(supersede_hints or []) + ranked_probes:
            if pk.probe_budget <= 0:
                break
            at = h.get("probe_at") if "probe_at" in h else h.get("at")
            if pk.spend_probe(at):
                # Named, because the agent has reserved probes in writing and
                # been overruled without ever learning it (OBS-17).
                pk.log.append(
                    f"completion: spent a leftover probe at "
                    f"{list(_cell(at) or [])} — you did not pick this; a probe "
                    f"held back is treated as unspent, not as reserved"
                )


def pack_recipe(
    selected_options: Sequence[Any],
    agent_view: Mapping[str, Any],
    *,
    chain_hints: Sequence[Mapping[str, Any]] = (),
    probe_hints: Sequence[Mapping[str, Any]] = (),
    supersede_hints: Sequence[Mapping[str, Any]] = (),
    forbidden_cells: Optional[set] = None,
    avoid_cells: Optional[set] = None,
    live_red_cells: Optional[set] = None,
    chaff_short: bool = False,
    complete: bool = True,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Compile the thinker's resolved recipe into wire moves (deterministic).

    ``selected_options`` is the priority-ordered list of :class:`..agency.Option`
    from ``resolve_plan``. Returns ``(moves, log)``. When ``complete`` is set, the
    utilization pass guarantees every alive harvester and probe is deployed from
    offered geometry. Returns an empty move list when there is no recipe — the
    caller then falls back to the LLM mover.

    ``forbidden_cells`` (Part A1) is the persistent stripped/GREEN union — an
    ENGINE FACT. A step onto one truncates the walk; a drop onto one relocates
    inside the option's own footprint and deletes the run only if no legal cell
    exists there. Since workstream C the menu already withholds such options
    (OBS-22), so this path is a backstop.

    ``avoid_cells`` is the thinker's own soft hint and is kept SEPARATE: it never
    cancels an option the agent explicitly selected — the contradiction is logged
    and the pick wins (OBS-15). Passing it in ``forbidden_cells`` instead is the
    bug that deleted a correct attack and blamed hazard memory for it.

    ``live_red_cells`` (Part A2) is the seat's LIVE-red set — a contested wave's
    sweep may only step onto these cells. ``chaff_short`` (Part C) is the
    thinker's ``chaff_react`` flag; since fix 2.10 it is REPORTED on the card and
    shortens nothing.

    Options are compiled in THE ORDER THE AGENT GAVE THEM. The sole exception is
    ``_order_for_probe_support``, which is legality rather than preference.
    """
    pk = _Packer(
        agent_view,
        forbidden_cells=forbidden_cells,
        avoid_cells=avoid_cells,
        live_red_cells=live_red_cells,
        chaff_short=chaff_short,
    )
    ordered, order_log = _order_for_probe_support(
        selected_options or [], agent_view,
    )
    pk.log.extend(order_log)
    # Fix 2.4, CORRECTED. This block used to hoist every harvester outing ahead
    # of every standalone probe, on the argument that a probe buys tomorrow
    # while an outing banks tonight. True as ADVICE, and not ours to impose:
    # harvester -> probe -> harvester is a legal night, and an agent may well
    # want it (a mid-night denial shot, a probe placed once a unit has cleared
    # the cell). The agent's stated order is the plan; the compiler executes it.
    #
    # The ONE reordering left is ``_order_for_probe_support`` above, and it
    # survives because it is a LEGALITY question rather than a preference: a
    # drop needs live coverage at hour start, so a run must not be sequenced
    # after the run that crushes the probe providing it.
    if pk.chaff_short:
        # Fix 2.10 — reported, never enforced. The flag used to mean "cap every
        # chain at 2 steps", which cost the d4 pure in four consecutive sweeps.
        pk.log.append(
            "you set chaff_react (you expect a jam tonight). NOTHING was "
            "shortened on your behalf — route length is your call, so pick a "
            "SHORT option if you want the fleet lifting early."
        )
    for opt in ordered:
        kind = getattr(opt, "kind", None)
        payload = getattr(opt, "payload", None) or {}
        fn = _DISPATCH.get(str(kind))
        if fn is not None:
            fn(pk, payload)
    if complete:
        _complete_utilization(
            pk,
            agent_view,
            chain_hints=chain_hints,
            probe_hints=probe_hints,
            supersede_hints=supersede_hints,
        )
    return pk.moves, pk.log
