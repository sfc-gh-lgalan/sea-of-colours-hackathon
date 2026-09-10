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

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test._v7.probe_hints import (
    _orbit_harvester_ids,
)
from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test import (
    option_economics as econ,
)
from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test import scorch
# The night's length in hours IS the queue cap (§3.10), and the splice in
# ``flush_deferred`` has to respect it or a pickup falls off the end.
from sea_of_colours.game.policy import MAX_MOVES

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
    """How many harvesters a seam campaign commits (one per non-deny wave).

    v13 — ``emp_only`` waves fire a salvo and commit NO harvester (case B's
    H3 halo lock, case C's H3 hole-shape). Same for ``snap_only`` waves,
    which fire a SNAP at a single cell (case C's H1 landing-block in
    SNAP_BLOCK). Neither must count toward the demand or the campaign
    will be pre-flight-rejected for lacking a unit it never wanted,
    silently dropping the whole play. This was the seed-4242
    SMASH_THEN_LOCK failure: 3 waves, but only 2 harvesters needed, and
    the packager cut the campaign because it read demand as 3.
    """
    payload = getattr(opt, "payload", None) or {}
    return sum(
        1
        for w in (payload.get("waves") or [])
        if isinstance(w, Mapping)
        and not w.get("deny_only")
        and not w.get("emp_only")
        and not w.get("snap_only")
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
        # Part C — the thinker's ``chaff_react`` flag. REPORTED, never enforced
        # (fix 2.10): it shortens nothing, and simply records that the agent
        # said it expected a jam so the night can be read back honestly.
        self.chaff_short: bool = bool(chaff_short)
        # ── this fork: the seat's own ordnance ─────────────────────────
        # ``emp_budget`` is the rack; ``scorched`` maps every cell under
        # one of OUR OWN clouds to the hour it clears. The hour matters:
        # a cloud is not a permanent hazard like stripped green, it is a
        # timer, and the difference between "never go here" and "not for
        # eight hours" is a whole harvester outing.
        self.emp_budget: int = scorch.stock(agent_view)["emp"]
        self.emp_radius, self.emp_missiles, self.emp_cloud_hours = (
            scorch.specs(agent_view)
        )
        # v13 — SNAP rack, kept alongside EMP for the same reason: a night
        # phase that reads the rack must see every weapon or it will plan
        # around a subset. Specs live on ``orbit.weapon_specs.snap``; the
        # only one the packager actually needs is a boolean "have any" —
        # the wire move is a single cell, radius 0.
        self.snap_budget: int = scorch.stock(agent_view).get("snap", 0)
        self.scorched: Dict[Tuple[int, int], int] = {}
        self._friendly_fire_warned = False
        #: Walks held back until a cloud lifts. Flushed LAST, after the
        #: completion pass, so the hours a held harvester spends standing
        #: still are filled with the seat's other work rather than waits.
        self.deferred: List[Dict[str, Any]] = []

    # ── own ordnance ───────────────────────────────────────────────
    def clear_hour(self, cell: Any) -> int:
        """The first hour ``cell`` is safe again, or 0 if it never wasn't.

        ``len(self.moves)`` IS the hour counter — the seat applies one
        move per hour in queue order (§3.10) — so the compiler can answer
        "is this step inside my own cloud?" simply by comparing the slot
        it is about to occupy against this.
        """
        c = _cell(cell)
        return self.scorched.get(c, 0) if c is not None else 0

    def note_friendly_fire(self, cell: Any, hour: int) -> None:
        """Price a move that lands under our own cloud. Never cut it.

        Same rule as every other guard in this compiler: the engine allows
        it, so it is the agent's call (OBS-27). What we owe the agent is
        that it finds out, once, in terms it can act on.
        """
        clear = self.clear_hour(cell)
        if clear <= hour or self._friendly_fire_warned:
            return
        self._friendly_fire_warned = True
        self.log.append(
            f"FRIENDLY FIRE: {list(_cell(cell) or [])} is under YOUR OWN EMP "
            f"until hour {clear}, and this move lands at hour {hour}. The unit "
            f"will sit 'empd' — the hour is spent and nothing is banked. KEPT "
            "(your call), but either order this play AFTER the cloud lifts or "
            "aim the salvo somewhere your own night does not go."
        )

    def give_back_harvester(self) -> None:
        """Return the unit drawn by :meth:`next_harvester` to the pool.

        A play that draws a harvester and then cannot use it must put it
        back, or the completion pass believes the fleet is committed and
        leaves the unit in orbit banking nothing.
        """
        self._h_idx = max(0, self._h_idx - 1)

    def clear_hour_max(self) -> int:
        """The first hour EVERY one of our own clouds has lifted."""
        return max(self.scorched.values(), default=0)

    def defer(
        self, *, unit: str, start: Tuple[int, int],
        comb: Sequence[Tuple[int, int]], not_before: int,
    ) -> None:
        self.deferred.append(
            {"unit": unit, "start": start, "comb": list(comb),
             "not_before": int(not_before)},
        )

    def flush_deferred(self, *, max_moves: int) -> None:
        """Place each held walk at the hour its cloud lifts — by SPLICING.

        This is the part that makes the shaped scorch a real play rather
        than an expensive way to stand still. The seat gets one action per
        hour across the WHOLE fleet (§3.10), so the queue is already an
        interleaved timeline; nothing says a unit's moves have to be
        contiguous in it. So the held walk is INSERTED at the hour the
        cloud clears and whatever was queued for those hours is pushed out
        behind it — the other harvester runs its first few steps while the
        cloud stands, the held unit combs the seam the hour it lifts, and
        the other harvester then finishes.

        Padding with ``wait`` is the fallback, not the plan, and it fires
        only when the agent gave the night nothing else to do. Walking in
        early is NOT an alternative: a step into a standing cloud lands
        but banks nothing (§4.9.3) and the unit is disabled from the next
        hour, so it costs the same hour AND the cell.
        """
        for job in self.deferred:
            tail: List[Dict[str, Any]] = []
            cur = job["start"]
            for nxt in job["comb"]:
                for sx, sy in _steps_between(cur, nxt):
                    tail.append({"a": "step", "unit": job["unit"],
                                 "to": [sx, sy]})
                    cur = (sx, sy)
            tail.append({"a": "pickup", "unit": job["unit"]})

            start_hour = max(1, int(job["not_before"]))
            insert_at = start_hour - 1          # 0-based slot index
            if insert_at >= len(self.moves):
                pad = insert_at - len(self.moves)
                if pad:
                    self.moves.extend({"a": "wait"} for _ in range(pad))
                    self.log.append(
                        f"{pad} hour(s) of WAIT before {job['unit']} walks: "
                        "the cloud stands until hour "
                        f"{start_hour} and you gave the night nothing else to "
                        "do with those hours. Pick another chain or a probe "
                        "and they fill themselves — the walk is unaffected."
                    )
                insert_at = len(self.moves)
            else:
                pushed = len(self.moves) - insert_at
                self.log.append(
                    f"{pushed} of your other move(s) were moved to AFTER "
                    f"{job['unit']}'s walk: they run during the cloud, the "
                    f"walk goes in at hour {start_hour} the moment it lifts, "
                    "and they finish behind it. Every unit's own order is "
                    "unchanged."
                )

            # A pickup pushed past the queue cap is a harvester lost at
            # Aurora — the one outcome nobody intends. Shorten the WALK to
            # fit rather than let the lift fall off the end, and say so.
            overflow = len(self.moves) + len(tail) - int(max_moves)
            if overflow > 0:
                keep = max(0, len(tail) - 1 - overflow)
                self.log.append(
                    f"{job['unit']}'s walk was cut from {len(tail) - 1} steps "
                    f"to {keep}: the night is {max_moves} hours long and the "
                    "PICKUP has to fit inside it. A harvester that is not "
                    "lifted is destroyed at Aurora — the walk is worth less "
                    "than the unit."
                )
                tail = tail[:keep] + [tail[-1]]
            self.moves[insert_at:insert_at] = tail
        self.deferred.clear()

    def spend_emp(self, targets: Sequence[Tuple[int, int]]) -> bool:
        """Fire one charge at up to ``emp_missiles`` cells. One slot."""
        if self.emp_budget <= 0:
            return False
        aim = list(targets)[: self.emp_missiles]
        if not aim:
            return False
        launch_hour = len(self.moves) + 1
        self.moves.append(
            {"a": "emp_launch", "at": [[c[0], c[1]] for c in aim]},
        )
        self.emp_budget -= 1
        clears_at = launch_hour + self.emp_cloud_hours
        for cell in scorch.blast(aim, self.emp_radius):
            self.scorched[cell] = max(self.scorched.get(cell, 0), clears_at)
        self.log.append(
            f"EMP salvo away at hour {launch_hour}: "
            f"{[list(c) for c in aim]} -> {len(self.scorched)} cells dark "
            f"until hour {clears_at}. That hour is spent; the seat does "
            "nothing else in it."
        )
        return True

    def spend_snap(self, at: Tuple[int, int]) -> bool:
        """Fire one SNAP round at a single cell. One slot, one hour hot.

        v13 — SNAP resolves ABOVE the hour-start vision snapshot (§4.9.4).
        That is the whole point of the weapon: a rival's H1 landing on the
        cell is refused THIS hour, not next, because their landing checks a
        live snapshot the SNAP already killed the probe under. The engine
        does all of that; the packager just has to emit the wire move,
        drain the rack, and note the friendly-fire footprint (one cell for
        one hour). Sequenced early like the EMP salvo — the value of an
        early SNAP is a landing denied, and a landing denied at H03 is
        two hours the seat still has left to bank on.
        """
        if self.snap_budget <= 0:
            return False
        cell = _cell(at)
        if cell is None:
            return False
        launch_hour = len(self.moves) + 1
        self.moves.append({"a": "snap", "at": [cell[0], cell[1]]})
        self.snap_budget -= 1
        # Hot for the launch hour only (§4.9.4 — SNAP_CLOUD_HOURS = 1).
        # A friendly harvester about to step / drop on this cell in the
        # SAME hour is maimed exactly as the enemy would be, so note it.
        self.note_friendly_fire(cell, launch_hour)
        self.log.append(
            f"SNAP away at hour {launch_hour}: one round at {list(cell)}. "
            "The cell is hot for that hour only; a rival landing this hour "
            "is refused (damaged in orbit, no outing spent), and any "
            "harvester stepping onto it is crippled on the square."
        )
        return True

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
        # A probe is DESTROYED by a cloud, not merely disabled, and the
        # sweep runs every hour the cloud stands — so this one is not a
        # priced risk, it is a probe thrown away. Still not cut: the
        # agent may be superseding a rival probe it knows dies with it.
        self.note_friendly_fire(cell, len(self.moves) + 1)
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
        self.note_friendly_fire(drop, len(self.moves) + 1)
        self.moves.append({"a": "drop", "unit": unit, "at": [drop[0], drop[1]]})
        self._drop_cells.add(drop)
        cur = drop
        steps_emitted = 0
        n_green = 0   # priced-and-kept green steps, warned once
        n_blind = 0   # priced-and-kept fogged steps on a contested seam
        for c in comb or []:
            nxt = _cell(c)
            if nxt is None or nxt == cur:
                continue
            stopped = False
            for sx, sy in _steps_between(cur, nxt):
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
                self.note_friendly_fire((sx, sy), len(self.moves) + 1)
                self.moves.append({"a": "step", "unit": unit, "to": [sx, sy]})
                cur = (sx, sy)
                steps_emitted += 1
            if stopped:
                break
        if n_green > 1 or n_blind > 1:
            self.log.append(
                f"{unit} route totals: {n_green} green step(s) "
                f"(~-{n_green * 100}), {n_blind} fogged step(s) on a contested seam"
            )
        self.moves.append({"a": "pickup", "unit": unit})
        return True


def _pack_seam(pk: _Packer, payload: Mapping[str, Any]) -> None:
    """A multi-wave redsign campaign: each wave gets its own harvester."""
    waves = sorted(
        (w for w in (payload.get("waves") or []) if isinstance(w, Mapping)),
        key=lambda w: (int(w.get("wave") or 0), int(w.get("earliest_hour") or 0)),
    )
    for w in waves:
        # v13 EMP-only wave: fire the salvo, spend the enabling probe (if any),
        # commit NO harvester. Case A's H1 shield, case B/C's H3 halo lock —
        # the harvester wave that follows is deferred to cloud-lift by its
        # own ``defer_until_clear`` flag (below).
        #
        # Defensive: exceptions in the compound EMP branches MUST NOT crash
        # the packager (a crash here routes the seat to _heuristic_fallback,
        # which is the worst outcome on the board). Wrap the branch so a
        # broken wave logs and the seam pattern falls back to its remaining
        # (non-EMP) waves.
        # v13 — snap_only seam wave. Fires ONE SNAP round at ``snap_at`` and
        # commits no harvester. Used by SNAP_BLOCK to hot-mark a pure cell
        # at H1 so a rival's H1 landing is refused (§4.9.4), then wave 2
        # lands our own harvester on the (now cool) cell at H2.
        if w.get("snap_only") and w.get("snap_at"):
            try:
                aim = _cell(w.get("snap_at"))
                if aim is None:
                    pk.log.append(
                        f"seam wave {w.get('wave')} snap_only: no valid target"
                    )
                    continue
                if not pk.spend_snap(aim):
                    pk.log.append(
                        f"seam wave {w.get('wave')} snap_only: rack empty — "
                        "the SNAP block never fired; nothing prevents the "
                        "rival's H1 landing this hour"
                    )
            except Exception as e:  # pragma: no cover — defensive
                pk.log.append(
                    f"seam wave {w.get('wave')} snap_only crashed ({e!r})"
                )
            continue
        if w.get("emp_only") and w.get("emp_launch_at"):
            try:
                aim = [
                    c for c in (_cell(t) for t in (w.get("emp_launch_at") or [])) if c
                ]
                if not aim:
                    pk.log.append(
                        f"seam wave {w.get('wave')} emp_only: no valid target cells"
                    )
                    continue
                if w.get("probe_at") is not None:
                    pk.spend_probe(w.get("probe_at"))
                if not pk.spend_emp(aim):
                    pk.log.append(
                        f"seam wave {w.get('wave')} emp_only: rack empty — "
                        "the compound pattern's shield never went up"
                    )
            except Exception as e:  # pragma: no cover — defensive
                pk.log.append(
                    f"seam wave {w.get('wave')} emp_only crashed ({e!r}) — "
                    "skipped; the base pattern book still stands"
                )
            continue
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
        # v13 — a wave with ``defer_until_clear`` holds its drop+comb until the
        # last EMP cloud from earlier waves lifts (case A wave 2, case B/C
        # wave 3). Everything else in the queue runs during the wait, and the
        # deferred walk is spliced in the moment the cloud clears — same
        # mechanism the shaped-scorch play (BLIND_SCORCH) already uses.
        #
        # Defensive: any exception here is caught and the wave falls back to
        # the ordinary emit_chain path (which is what the pattern would have
        # done without deferral). Better to skip the shield timing than to
        # bomb out the whole packager and route to fallback.
        drop_at = _cell(w.get("drop_at"))
        comb_path = list(w.get("comb_path") or [])
        if w.get("defer_until_clear") and drop_at is not None:
            try:
                not_before = pk.clear_hour_max()
                if not_before <= len(pk.moves):
                    # Cloud already lifted (or nothing scorched yet) — the
                    # deferred gate is redundant; fall through to the normal
                    # emit path below.
                    pass
                else:
                    # Land the drop-legality probe now if the drop cell is
                    # not already covered — otherwise the deferred landing
                    # arrives to a dark cell and the sanitizer deletes it.
                    if (drop_at not in pk._probed_cells
                            and w.get("probe_at") is None):
                        pk.spend_probe(drop_at)
                    pk.moves.append(
                        {"a": "drop", "unit": unit, "at": [drop_at[0], drop_at[1]]},
                    )
                    pk._drop_cells.add(drop_at)
                    pk.defer(
                        unit=unit, start=drop_at, comb=comb_path,
                        not_before=not_before,
                    )
                    pk.log.append(
                        f"seam wave {w.get('wave')} held until hour "
                        f"{not_before} (cloud-lift): the drop lands after "
                        "the salvo clears, then combs the halo the cloud "
                        "has been protecting."
                    )
                    continue
            except Exception as e:  # pragma: no cover — defensive
                pk.rollback(mark)
                pk.log.append(
                    f"seam wave {w.get('wave')} defer branch crashed "
                    f"({e!r}) — falling back to immediate emit_chain"
                )
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


def _pack_emp(pk: _Packer, payload: Mapping[str, Any]) -> None:
    """One charge, up to three cells, one hour-slot (RULEBOOK §4.9.3).

    The whole salvo is a SINGLE wire move — ``at`` is a list of cells, not
    a cell — so a three-missile strike costs one of the seat's 21 slots,
    not three. Getting that wrong is the difference between a scorch that
    costs an hour and one that costs a harvester's whole outing.

    ``shape="occupy"`` is the fork's headline play and compiles to four
    beats rather than one: salvo, enabling probe into the cell the salvo
    deliberately missed, harvester onto it, and then a comb DEFERRED
    until the cloud lifts. Only the first three are emitted here; the
    walk is registered with :meth:`_Packer.defer` so the hours in between
    can be filled with the seat's other work instead of with waiting.
    """
    targets = [
        c for c in (_cell(t) for t in (payload.get("targets") or [])) if c
    ]
    if not targets:
        pk.log.append("cut EMP salvo: no valid target cells")
        return
    if not pk.spend_emp(targets):
        pk.log.append(
            "cut EMP salvo: the rack is empty — a charge is bought in ORBIT, "
            "and you cannot fire one you did not buy"
        )
        return
    if str(payload.get("shape") or "") != "occupy":
        return

    hole = _cell(payload.get("hole"))
    probe_at = _cell(payload.get("probe_at")) or hole
    if hole is None:
        pk.log.append("scorch shape had no hole cell — salvo only")
        return
    unit = pk.next_harvester()
    if unit is None:
        pk.log.append(
            f"salvo away, but no harvester was left to hold {list(hole)} — "
            "the cloud denies them the smear and buys you nothing. Free the "
            "unit by dropping another outing, or take the plain salvo."
        )
        return
    if not pk.spend_probe(probe_at):
        # Without live vision at hour start the drop is illegal (§3.9.7),
        # so the hole cannot be taken and holding a harvester back for it
        # would waste the unit as well as the charge.
        pk.log.append(
            f"salvo away, but no probe was left to light {list(probe_at)}, so "
            "the drop into the hole would be illegal at hour start (§3.9.7). "
            "The scorch stands; the occupation does not."
        )
        pk.give_back_harvester()
        return

    pk.note_friendly_fire(hole, len(pk.moves) + 1)
    pk.moves.append({"a": "drop", "unit": unit, "at": [hole[0], hole[1]]})
    pk._drop_cells.add(hole)

    comb = [c for c in (_cell(c) for c in (payload.get("comb") or [])) if c]
    clear_at = pk.clear_hour_max()
    pk.defer(unit=unit, start=hole, comb=comb, not_before=clear_at)
    pk.log.append(
        f"{unit} holds the hole at {list(hole)} — clear of your own cloud, "
        f"and no rival can reach it while the cloud stands. Its walk is held "
        f"until hour {clear_at}; the hours until then are free for your other "
        "plays."
    )


def _pack_snap(pk: _Packer, payload: Mapping[str, Any]) -> None:
    """One SNAP round at a single cell (§4.9.4).

    Wire shape: ``{"a": "snap", "at": [x, y]}`` — a bare cell, not a list.
    The payload's ``target`` is normalised to that shape by ``_cell``.
    SNAP_KILL and SNAP_STRIKE both funnel here; the difference is the
    target-selection heuristic in agency.py, not the packager.
    """
    target = _cell(payload.get("target"))
    if target is None:
        pk.log.append(
            f"cut SNAP: no valid target cell in payload {dict(payload)!r}"
        )
        return
    if not pk.spend_snap(target):
        pk.log.append(
            "cut SNAP: rack is empty — a SNAP is bought in ORBIT, and you "
            "cannot fire one you did not buy"
        )
        return


_DISPATCH = {
    "seam": _pack_seam,
    "hotdrop": _pack_hotdrop,
    "emp": _pack_emp,
    "snap": _pack_snap,  # v13 — SNAP round: one cell, one hour, above snapshot
    # v11 Phase-1 force-surfaced VALUE-PYRAMID grab — drop + contiguous walk,
    # identical wire shape to a juice chain, so it compiles through _pack_chain.
    "grab": _pack_chain,
    "blue_grab": _pack_chain,
    "chain": _pack_chain,
    "probe": _pack_probe,
    "supersede": _pack_supersede,
    "frontier": _pack_frontier,
}


def _emp_footprint(
    opts: Sequence[Any], agent_view: Mapping[str, Any],
) -> set:
    """Every cell the salvos in ``opts`` would darken."""
    radius, missiles, _hours = scorch.specs(agent_view)
    cells: set = set()
    for o in opts:
        if str(getattr(o, "kind", "")) != "emp":
            continue
        targets = [
            c for c in
            (_cell(t) for t in ((getattr(o, "payload", None) or {})
                                .get("targets") or []))
            if c
        ][:missiles]
        cells |= scorch.blast(targets, radius)
    return cells


def _order_for_emp_cloud(
    opts: Sequence[Any], agent_view: Mapping[str, Any],
) -> Tuple[List[Any], List[str]]:
    """Sequence a night that contains one of our own salvos.

    Like ``_order_for_probe_support`` above, this is PHYSICS rather than
    preference, which is the only justification this compiler accepts for
    touching the agent's stated order. Two facts drive it:

    * A cloud is worth what it denies over the following eight hours, and
      the night is 21 hours long. Fired late it denies almost nothing —
      so the salvo goes to hour one.
    * Friendly fire is on, and the seat's slot index IS the hour. So of
      the remaining plays, the ones that never enter the diamond are
      sequenced first: that is free, it costs the agent nothing it asked
      for, and it buys the plays that DO enter the diamond eight hours of
      clock they would not otherwise have had.

    What this deliberately does NOT do is drop a play for entering the
    cloud. The reordering is the help; the warning in
    :meth:`_Packer.note_friendly_fire` is the honesty; the choice stays
    the agent's.
    """
    opts = list(opts or [])
    salvos = [o for o in opts if str(getattr(o, "kind", "")) == "emp"]
    if not salvos:
        return opts, []

    log: List[str] = []
    rest = [o for o in opts if str(getattr(o, "kind", "")) != "emp"]
    blast_cells = _emp_footprint(salvos, agent_view)

    def _touches(o: Any) -> bool:
        pay = getattr(o, "payload", None) or {}
        cells = set()
        for key in ("drop_at", "at", "probe_at"):
            c = _cell(pay.get(key))
            if c:
                cells.add(c)
        for key in ("cells", "comb_path"):
            for c in (pay.get(key) or []):
                cc = _cell(c)
                if cc:
                    cells.add(cc)
        for w in (pay.get("waves") or []):
            if isinstance(w, Mapping):
                c = _cell(w.get("drop_at"))
                if c:
                    cells.add(c)
                for cc in (w.get("cells") or []):
                    c2 = _cell(cc)
                    if c2:
                        cells.add(c2)
        return bool(cells & blast_cells)

    # Stable within each bucket, so the agent's relative order survives
    # everywhere it does not conflict with the cloud.
    clear = [o for o in rest if not _touches(o)]
    under = [o for o in rest if _touches(o)]

    if len(salvos) > 1:
        log.append(
            f"you picked {len(salvos)} salvos; they fire in the order given, "
            "each spending its own hour and its own charge"
        )
    log.append(
        "EMP resequenced to hour 1 — a cloud is worth what it denies over "
        "the next 8 hours, and one fired late denies nothing. Your relative "
        "order for everything else is unchanged."
    )
    if under:
        log.append(
            f"{len(under)} of your plays enter your own blast diamond, so "
            f"they were moved behind the {len(clear)} that do not. This buys "
            "them clock; it does not make them safe — check the hour each "
            "one now lands on against the cloud's clear time."
        )
    return salvos + clear + under, log


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
    max_moves: int = MAX_MOVES,
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
    ordered, emp_log = _order_for_emp_cloud(ordered, agent_view)
    pk.log.extend(emp_log)
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
    # LAST, and after the completion pass on purpose. A walk held for a
    # cloud is spliced in at the hour that cloud lifts, and everything
    # queued for those hours is pushed out behind it — so the more real
    # work the night already has, the fewer WAITs it needs. Flushing
    # before completion would pad with waits and then append the very
    # work that should have filled them.
    pk.flush_deferred(max_moves=max_moves)
    return pk.moves, pk.log
