"""Lightweight, mechanical move sanitizer for the tabula_v7 harness.

The prompt does the heavy lifting of getting the LLM to plan good moves,
but three failure modes recur no matter how the doctrine is worded — they
are geometry mistakes the model makes under time pressure, not strategy
mistakes. This module is the structural guardrail that catches them AFTER
the LLM responds and BEFORE the plan is submitted to the engine:

  1. NO-BEACON / ILLEGAL DROP — a ``drop`` onto a cell that is not in
     live vision (no probe disk / friendly-unit tile) or off-grid. The
     engine rejects those, and every step/pickup that depended on the
     landing no-ops, wasting the harvester's whole night. We reroute to a
     legal adjacent cell when one exists, or drop the doomed chain
     entirely. We also refuse a drop onto GREEN / synthetic-green — that
     one the engine ALLOWS (v1.48: this docstring used to claim otherwise,
     which is how the veto came to be applied to steps as well). We block
     it because landing on stripped ground banks nothing and pays -100, so
     it is self-harm with no upside — not because it is illegal. Walking
     ACROSS green is left alone; the menu prices it and the agent decides.

  2. SELF-CRUSH (nuanced) — a ``drop`` onto a friendly probe cell that
     still has >= 2 nights of vision left AND has no loot underneath. That
     needlessly destroys future vision. We reroute the landing to an
     adjacent drop-legal cell to preserve the probe. Crushing is LEFT
     ALONE when it is justified: the probe is expiring (<= 1 night) or the
     cell holds visible RED the harvester needs to grab.

  3. HARVESTER COLLISION — two friendly harvesters routed onto the SAME
     cell collide (both damaged, ZERO parcels banked). We keep the first
     unit's claim and reroute / truncate the second.

Design principles:
  * NEVER emit a move the engine would reject. When in doubt, TRUNCATE
    (bank what's safe) rather than gamble.
  * NEVER strand a harvester on the surface — truncating a chain always
    leaves (or inserts) a pickup so the unit comes home.
  * Be transparent: every change is appended to a human-readable log the
    harness stamps into the audit rationale.

The heavy geometry helpers are shared with :mod:`.validators` so the
sanitizer and the (now-informational) validator agree on drop legality.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7.validators import (
    _live_vision_cells,
    _synthetic_green_cells,
    _green_hazard_cells,
    _friendly_probe_cells,
    _world_dims,
    _initial_harvester_positions,
)
from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7.probe_hints import (
    _visible_red,
    _orbit_harvester_ids,
)

Cell = Tuple[int, int]
Move = Mapping[str, Any]

# Manhattan-1 neighbourhood (engine adjacency = N/S/E/W, no diagonals).
_NSEW = ((1, 0), (-1, 0), (0, 1), (0, -1))

# Minimum blue purity that counts as "loot worth crushing a probe for"
# (only consulted when a caller opts in via ``blue_is_loot=True``). Blue is
# valuable, but a probe's multi-night vision outranks a mere trace of blue —
# so we only treat genuinely RICH blue (top-quartile purity, i.e. "pure blue")
# as a reason to keep a drop/step on a friendly probe cell rather than reroute
# off the value. Red uses ``> 0`` because pure red per-cell always beats vision.
_BLUE_LOOT_MIN = 192


def _visible_blue(agent_view: Mapping[str, Any]) -> Dict[Cell, int]:
    """RICH blue cells (purity >= ``_BLUE_LOOT_MIN``) currently in LOS.

    Mirrors :func:`_visible_red` for the blue channel. ``blue_tiles`` rows are
    ``{"x", "y", "purity"|"value", ...}``. Only cells at/above the loot floor
    are returned so a trace of blue never justifies burning a live probe.
    """
    out: Dict[Cell, int] = {}
    for row in (agent_view.get("blue_tiles") or []):
        if not isinstance(row, Mapping):
            continue
        try:
            x, y = int(row["x"]), int(row["y"])
            p = int(row.get("purity") or row.get("value") or 0)
        except (TypeError, KeyError, ValueError):
            continue
        if p >= _BLUE_LOOT_MIN:
            out[(x, y)] = p
    return out

# Per-outing hold cap (RULEBOOK §3): a harvester's hold holds 6 parcels
# (drop + up to 5 steps). The engine now enforces this by parcel count
# (GameSession.try_step_unit cancels a step once the hold is full). A
# chain can therefore never bank more than 6 parcels, so any outing with
# MORE than 6 steps is guaranteed to waste hours on steps the engine will
# cancel. We conservatively clip at 6 steps — this never removes a step
# that could legally bank (even a drop on an EMPTY tile tops out at 6
# steps) while trimming clearly-wasteful 7th+ steps from the plan.
_MAX_STEPS_PER_OUTING = 6


def _friendly_probe_nights(agent_view: Mapping[str, Any]) -> Dict[Cell, int]:
    """``(x, y) -> nights_remaining`` for every active friendly probe.

    Defaults a probe with no ``nights_remaining`` field to 2 (treat as
    "still valuable") so a missing field never triggers a reroute we can't
    justify. Expired probes (<= 0) are skipped.
    """
    out: Dict[Cell, int] = {}
    for e in ((agent_view.get("entities") or {}).get("mine") or []):
        if not isinstance(e, Mapping) or str(e.get("type") or "") != "probe":
            continue
        pos = e.get("pos") or e.get("at")
        if not (isinstance(pos, (list, tuple)) and len(pos) == 2):
            continue
        try:
            cell = (int(pos[0]), int(pos[1]))
        except (TypeError, ValueError):
            continue
        nr = e.get("nights_remaining")
        nights = int(nr) if isinstance(nr, (int, float)) else 2
        if nights <= 0:
            continue
        out[cell] = nights
    return out


def _euclid_disk(cx: int, cy: int, width: int, height: int) -> Set[Cell]:
    """Engine vision/drop-legal shape: Euclidean radius-4 disk."""
    out: Set[Cell] = set()
    for dx in range(-4, 5):
        for dy in range(-4, 5):
            if dx * dx + dy * dy > 16:
                continue
            x, y = cx + dx, cy + dy
            if 0 <= x < width and 0 <= y < height:
                out.add((x, y))
    return out


def _has_following_step(moves: List[Move], drop_idx: int, unit: str) -> bool:
    """True if ``unit`` has at least one ``step`` after ``drop_idx`` before
    its next ``pickup``/``drop`` — i.e. this drop starts a harvest chain, not
    a lone drop-and-lift."""
    for j in range(drop_idx + 1, len(moves)):
        mj = moves[j]
        if not isinstance(mj, Mapping):
            continue
        if str(mj.get("unit") or "") != unit:
            continue
        a = str(mj.get("a") or "")
        if a == "step":
            return True
        if a in ("pickup", "drop"):
            return False
    return False


def _first_following_step_cell(
    moves: List[Move], drop_idx: int, unit: str,
) -> Optional[Cell]:
    """The cell ``unit``'s FIRST step after ``drop_idx`` targets, or None.

    Used to spare a valuable probe on a self-crush drop WITHOUT orphaning the
    chain: instead of landing on the probe centre (empty, no loot) and
    crushing it, we land directly on this first-step cell (the chain was
    going to step onto it anyway) and skip the now-redundant step. This is
    strictly better than keeping the crush — the probe survives and no
    harvest is lost."""
    for j in range(drop_idx + 1, len(moves)):
        mj = moves[j]
        if not isinstance(mj, Mapping):
            continue
        if str(mj.get("unit") or "") != unit:
            continue
        a = str(mj.get("a") or "")
        if a == "step":
            to = mj.get("to")
            if isinstance(to, (list, tuple)) and len(to) == 2:
                try:
                    return (int(to[0]), int(to[1]))
                except (TypeError, ValueError):
                    return None
            return None
        if a in ("pickup", "drop"):
            return None
    return None


def sanitize_moves(
    moves: List[Move],
    agent_view: Mapping[str, Any],
    *,
    chain_hints: Optional[List[Mapping[str, Any]]] = None,
    is_final_night: bool = False,
    enemy_probe_cells: Optional[Set[Cell]] = None,
    blue_is_loot: bool = False,
    extra_bad_cells: Optional[Set[Cell]] = None,
    deploy_idle: bool = True,
    release_vacated_cells: bool = False,
) -> Tuple[List[Move], List[str]]:
    """Return ``(sanitized_moves, change_log)``.

    ``sanitized_moves`` is a new list safe to submit; ``change_log`` is a
    list of short human-readable strings describing every edit made (empty
    when the plan was already clean).

    Optional guardrails (all default off / empty so existing callers and
    tests keep their behaviour):

      * ``chain_hints`` — the heuristic RED chains for THIS night. Used by
        the deploy-all-harvesters guard (T6): any harvester left in orbit
        after the plan is spent gets the top *unused* legal chain appended
        (drop -> steps -> pickup). An idle harvester banks nothing — on the
        final night that is pure lost points; every night it is wasted EV.
      * ``is_final_night`` — when True, a ``probe`` launch that is NOT
        superseding a known enemy probe is dropped (T7): a probe on the
        last night buys vision for a night that never comes.
      * ``enemy_probe_cells`` — cells holding a known enemy probe. A
        final-night probe onto one of these is a legitimate SUPERSEDE and
        is kept.
      * ``deploy_idle`` — set False to turn T6 off. T6 is the one guard here
        that ADDS a play rather than making an illegal one legal, and a caller
        that has already decided how to spend its harvesters needs to be able
        to say so. Defaults True so v7 and its tests are unchanged.
      * ``release_vacated_cells`` — treat the collision map as CURRENT
        OCCUPANCY rather than as the whole night's trail (v12, OBS-27). See the
        note beside ``claimed`` below for why the trail model is wrong. Default
        False so every earlier harness keeps the behaviour it was measured with.
    """
    width, height = _world_dims(agent_view)
    if width <= 0 or height <= 0:  # can't reason about geometry — pass through
        return list(moves), []

    enemy: Set[Cell] = set(enemy_probe_cells or ())

    # Every drop target anywhere in the plan. Used to spare a final-night
    # probe that is actually a HOT-DROP enabler (probe -> friendly drop into
    # its disk) rather than a wasteful frontier scout. Killing a hot-drop
    # probe strands the harvester whose drop needed that probe's vision —
    # the exact bug that deleted harvester_p1_2's whole chain on a day-7
    # final night.
    drop_targets: Set[Cell] = set()
    for _m in moves:
        if isinstance(_m, Mapping) and str(_m.get("a") or "") == "drop":
            _at = _m.get("at")
            if isinstance(_at, (list, tuple)) and len(_at) == 2:
                try:
                    drop_targets.add((int(_at[0]), int(_at[1])))
                except (TypeError, ValueError):
                    pass

    live: Set[Cell] = set(_live_vision_cells(agent_view))
    bad: Set[Cell] = _synthetic_green_cells(agent_view) | _green_hazard_cells(agent_view)
    # Part A1 (v11) — persistent stripped/GREEN union that SURVIVES FOG. The
    # view-only helpers above miss a cell a rival stripped while it was fogged to
    # us; this backstop forbids drops/steps onto any cell we have ever seen green,
    # even now-hidden ones. Default empty so v7/v8/v9 callers are unchanged.
    if extra_bad_cells:
        bad = bad | set(extra_bad_cells)
    probe_cells: Set[Cell] = _friendly_probe_cells(agent_view)
    # Frozen snapshot of the probes that existed at TURN START — used to
    # detect a probe launched onto our OWN live probe (self-supersede).
    # ``probe_cells`` is mutated below as new probes launch this turn, so
    # we cannot reuse it for that check.
    turn_start_probes: Set[Cell] = set(probe_cells)
    probe_nights = _friendly_probe_nights(agent_view)
    red: Dict[Cell, int] = _visible_red(agent_view)
    # Opt-in (v10): rich blue under a probe is loot too, so a drop/step onto a
    # pure-blue cell held by a friendly probe is a justified crush, not a
    # reroute. Off by default so v7/v8 (and existing tests) are unchanged.
    blue: Dict[Cell, int] = _visible_blue(agent_view) if blue_is_loot else {}

    # A probe launched EARLIER in this same moves list must be tracked too,
    # otherwise a same-turn ``probe (34,18)`` -> ``drop (34,18)`` sequence
    # crushes the just-launched probe and the drop-crush check never fires
    # (the cell wasn't in ``probe_cells`` at turn start). Default a fresh
    # probe to the engine's configured lifetime so the ">= 2 nights"
    # preserve rule treats it as still-valuable.
    rules = ((agent_view.get("meta") or {}).get("rules") or {})
    try:
        fresh_probe_nights = int(rules.get("probe_lifetime_nights") or 3)
    except (TypeError, ValueError):
        fresh_probe_nights = 3

    # Per-unit lifecycle: pos is None while in orbit, a cell while on the
    # surface, and the unit is added to ``done`` once it has picked up or
    # its chain was removed/truncated.
    pos: Dict[str, Optional[Cell]] = _initial_harvester_positions(agent_view)
    done: Set[str] = set()
    # Units that received a drop this turn (or started on the surface) —
    # feeds the deploy-all-harvesters guard so we never re-deploy a unit
    # that is already working, and DO deploy one left idle in orbit.
    deployed: Set[str] = {u for u, c in pos.items() if c is not None}

    # Cells OCCUPIED by a friendly unit right now (collision map).
    #
    # v12 — this used to be the whole TRAIL: every cell any unit had ever
    # touched stayed claimed for the rest of the night, so a second harvester
    # could never cross ground the first had walked. The engine has no such
    # rule. A seat's moves resolve one per hour in queue order, so by the time
    # the second unit arrives the first has walked on (or lifted) and the cell
    # is empty. Treating the trail as a wall truncated the run that was going
    # to take the pure on SNAP_408ddd46_d4_p1, 6 runs out of 6 (OBS-27) —
    # the same wrong belief as the packager's "second drop is always a
    # self-collision". Cells are now RELEASED as their occupant leaves.
    claimed: Dict[Cell, str] = {c: u for u, c in pos.items() if c is not None}

    def _release(unit: str) -> None:
        """This unit is no longer standing on its cell — free it for others."""
        if not release_vacated_cells:
            return
        cell = pos.get(unit)
        if cell is not None and claimed.get(cell) == unit:
            claimed.pop(cell, None)

    # Per-unit drop reroute delta. When a drop is relocated (x,y)->(nx,ny),
    # the LLM's hand-built step chain was planned from the ORIGINAL cell, so
    # every following step is now non-adjacent and would truncate. We record
    # the delta and translate the unit's subsequent steps by it — re-threading
    # the chain onto the new landing instead of amputating it (the dominant
    # source of "drift": ~68% of planned steps were lost to this).
    drop_delta: Dict[str, Cell] = {}

    # Units whose drop ABSORBED their first planned step to spare a probe
    # (see the T2 self-crush block). The very next step for such a unit is
    # now redundant (it targets the cell we just dropped on) and must be
    # skipped rather than truncate the chain as a zero-length move.
    absorb_skip: Set[str] = set()

    out: List[Move] = []
    log: List[str] = []

    def _in_bounds(x: int, y: int) -> bool:
        return 0 <= x < width and 0 <= y < height

    def _legal_land(x: int, y: int, unit: str) -> bool:
        """A cell a harvester may DROP or STEP onto without engine rejection
        or self-harm: in-bounds, in live vision, not green, not claimed by
        a different unit."""
        if not _in_bounds(x, y):
            return False
        if (x, y) in bad:
            return False
        if (x, y) not in live:
            return False
        other = claimed.get((x, y))
        return other is None or other == unit

    def _reroute_target(cx: int, cy: int, unit: str, *, avoid_probes: bool) -> Optional[Cell]:
        for dx, dy in _NSEW:
            nx, ny = cx + dx, cy + dy
            if not _legal_land(nx, ny, unit):
                continue
            if avoid_probes and (nx, ny) in probe_cells:
                continue
            return (nx, ny)
        return None

    def _truncate(unit: str) -> None:
        """Cut a unit's chain here: bank whatever it holds with a pickup,
        then mark it done so later moves for it are dropped."""
        if unit in done:
            return
        if pos.get(unit) is not None:
            out.append({"a": "pickup", "unit": unit})
        _release(unit)
        done.add(unit)

    for idx, m in enumerate(moves):
        if not isinstance(m, Mapping):
            log.append("dropped a non-dict move")
            continue
        act = str(m.get("a") or "")

        if act == "probe":
            # Probes are in-bounds-legal to the engine, but two launches are
            # self-harm the engine happily executes:
            #   (T7a) SELF-SUPERSEDE — a probe onto a cell where we ALREADY
            #         have a live probe destroys our own probe for zero
            #         vision gain (we already see that disk). Drop it unless
            #         the cell also holds an enemy probe worth superseding.
            #   (T7b) FINAL-NIGHT FRONTIER PROBE — on the last night a probe
            #         reveals ground no harvester will ever reach. Drop it
            #         unless it lands on a known enemy probe (a legit
            #         final-night supersede/denial play).
            at = m.get("at")
            if isinstance(at, (list, tuple)) and len(at) == 2:
                try:
                    px, py = int(at[0]), int(at[1])
                except (TypeError, ValueError):
                    px = py = None
                if px is not None and _in_bounds(px, py):
                    cell = (px, py)
                    if cell in turn_start_probes and cell not in enemy:
                        log.append(
                            f"dropped probe at {cell} — would supersede our OWN "
                            f"live probe (destroys vision we already have)"
                        )
                        continue
                    if is_final_night and cell not in enemy:
                        # Keep it ONLY if it's a hot-drop enabler: a friendly
                        # drop lands inside this probe's disk (harvest THIS
                        # night). A pure frontier scout (no drop in its disk)
                        # is still wasteful on the last night — drop that.
                        disk = _euclid_disk(px, py, width, height)
                        if disk.isdisjoint(drop_targets):
                            log.append(
                                f"dropped final-night probe at {cell} — frontier "
                                f"scout with no harvest to enable (no drop in its "
                                f"disk, not an enemy supersede)"
                            )
                            continue
                        # else: a drop lands in this disk -> hot-drop enabler,
                        # keep the probe so that drop stays legal.
                    live |= _euclid_disk(px, py, width, height)
                    # Track the launch so a later same-turn drop/step onto
                    # (px,py) is treated as a crush (see report bug).
                    probe_cells.add(cell)
                    probe_nights.setdefault(cell, fresh_probe_nights)
                    out.append(m)
                    continue
            out.append(m)  # malformed probe — let the engine ignore it
            continue

        unit = str(m.get("unit") or "")

        if act == "pickup":
            if unit in done:
                log.append(f"{unit}: dropped redundant pickup after chain end")
                continue
            if pos.get(unit) is None:
                log.append(f"{unit}: dropped pickup — unit not on surface")
                continue
            out.append(m)
            _release(unit)   # lifted: the cell is free for any later unit
            done.add(unit)
            continue

        # Any drop/step for a unit whose chain we've already ended is dead.
        if unit in done:
            log.append(f"{unit}: dropped '{act}' after chain was truncated")
            continue

        if act == "drop":
            at = m.get("at")
            if not (isinstance(at, (list, tuple)) and len(at) == 2):
                log.append(f"{unit}: dropped malformed drop.at")
                done.add(unit)
                continue
            try:
                x, y = int(at[0]), int(at[1])
            except (TypeError, ValueError):
                log.append(f"{unit}: dropped drop with non-int coords")
                done.add(unit)
                continue
            if pos.get(unit) is not None:
                log.append(f"{unit}: dropped duplicate drop — already on surface")
                continue

            requested: Cell = (x, y)  # where the LLM asked to land (chain anchor)
            has_chain = _has_following_step(moves, idx, unit)
            did_absorb = False  # set when we spare a probe via first-step absorb

            # (T2) Nuanced self-crush reroute — probe still valuable + no loot.
            if (x, y) in probe_cells:
                nights = probe_nights.get((x, y), 2)
                has_loot = red.get((x, y), 0) > 0 or blue.get((x, y), 0) > 0
                if nights >= 2 and not has_loot:
                    if has_chain:
                        # A harvest chain follows. The probe centre is EMPTY
                        # (no loot), so landing there banks nothing and only
                        # destroys the probe. Instead ABSORB the first step:
                        # land directly on the chain's first cell (which the
                        # harvester was going to step onto next anyway) and
                        # skip that now-redundant step. Spares the probe AND
                        # keeps the full harvest — strictly better than the old
                        # keep-the-crush behaviour. Falls back to keeping the
                        # crush only if that first cell is not a legal, non-probe
                        # landing.
                        first = _first_following_step_cell(moves, idx, unit)
                        if (
                            first is not None
                            and _legal_land(first[0], first[1], unit)
                            and first not in probe_cells
                        ):
                            log.append(
                                f"{unit}: absorbed first step — drop {(x, y)}->"
                                f"{first} to spare a probe ({nights} nights "
                                f"vision) without orphaning the chain"
                            )
                            x, y = first
                            absorb_skip.add(unit)
                            did_absorb = True
                        else:
                            log.append(
                                f"{unit}: kept crush at {(x, y)} — chain follows "
                                f"and no legal first-step cell to spare the probe"
                            )
                    else:
                        alt = _reroute_target(x, y, unit, avoid_probes=True)
                        if alt is not None:
                            log.append(
                                f"{unit}: rerouted lone drop {(x, y)}->{alt} to "
                                f"spare a probe ({nights} nights vision, no loot)"
                            )
                            x, y = alt
                        else:
                            log.append(
                                f"{unit}: kept crush at {(x, y)} — no legal "
                                f"adjacent landing to spare the probe"
                            )

            # (T1) Illegal drop (no vision / green / off-grid) + (T4) collision.
            if not _legal_land(x, y, unit):
                # A11: try to dodge onto a NON-probe cell first — rerouting a
                # collision straight onto a live friendly probe crushes future
                # vision just to sidestep a same-night clash. Only accept a
                # probe cell as the last legal option (better than stranding).
                alt = (
                    _reroute_target(x, y, unit, avoid_probes=True)
                    or _reroute_target(x, y, unit, avoid_probes=False)
                )
                if alt is not None:
                    reason = (
                        "collision" if claimed.get((x, y)) not in (None, unit)
                        else "not drop-legal"
                    )
                    log.append(f"{unit}: rerouted drop {(x, y)}->{alt} ({reason})")
                    x, y = alt
                else:
                    log.append(
                        f"{unit}: removed drop chain at {(x, y)} — illegal and no "
                        f"legal adjacent landing"
                    )
                    done.add(unit)
                    continue

            # If the drop moved from where the LLM asked AND a chain follows,
            # record the delta so the following steps get translated to stay
            # contiguous with the new landing (re-thread, not truncate). Skip
            # this for a first-step ABSORB — there the remaining steps are
            # already contiguous from the new landing (we only dropped the
            # first, redundant step), so translating them would misalign the
            # chain off its seam.
            if (x, y) != requested and has_chain and not did_absorb:
                drop_delta[unit] = (x - requested[0], y - requested[1])
                log.append(
                    f"{unit}: re-threading chain by {drop_delta[unit]} after "
                    f"drop moved {requested}->{(x, y)}"
                )

            new_move = dict(m)
            new_move["at"] = [x, y]
            out.append(new_move)
            pos[unit] = (x, y)
            claimed[(x, y)] = unit
            deployed.add(unit)
            continue

        if act == "step":
            # If this unit's drop absorbed its first step (to spare a probe),
            # the first step move is now redundant — it targets the cell we
            # already dropped on. Skip exactly one such step per absorb.
            if unit in absorb_skip:
                absorb_skip.discard(unit)
                log.append(
                    f"{unit}: skipped redundant first step (absorbed into drop)"
                )
                continue
            to = m.get("to")
            cur = pos.get(unit)
            if cur is None:
                log.append(f"{unit}: dropped step — unit not on surface")
                continue
            if not (isinstance(to, (list, tuple)) and len(to) == 2):
                log.append(f"{unit}: step had malformed target — truncating")
                _truncate(unit)
                continue
            try:
                tx, ty = int(to[0]), int(to[1])
            except (TypeError, ValueError):
                log.append(f"{unit}: step coords not int — truncating")
                _truncate(unit)
                continue
            # Re-thread: if this unit's drop was relocated, translate the
            # planned step by the same delta so the contiguous chain follows
            # the new landing cell instead of pointing back at the old one.
            d = drop_delta.get(unit)
            if d is not None:
                tx, ty = tx + d[0], ty + d[1]
                m = {"a": "step", "unit": unit, "to": [tx, ty]}
            cx, cy = cur
            adjacent = abs(tx - cx) + abs(ty - cy) == 1
            # v1.48 — green is NOT a step legality test. ``bad`` still gates
            # DROPS (landing on stripped ground banks nothing, so it is pure
            # self-harm and the menu declines to offer it), but the engine has
            # no rule against WALKING over green: it costs -100 at settlement
            # and that is a price, not a refusal. Vetoing it here truncated
            # chains that were crossing their own wake on purpose — to reach a
            # mass behind it, or to re-walk a seam a rival might have denied —
            # and cost more red than the penalty ever did. The menu now prices
            # the crossing honestly, so the agent can make this trade itself.
            legal = adjacent and _in_bounds(tx, ty)
            collides = claimed.get((tx, ty)) not in (None, unit)
            if not legal or collides:
                why = (
                    "collision" if collides
                    else "non-adjacent/off-grid"
                )
                log.append(
                    f"{unit}: truncated chain at step->{(tx, ty)} ({why}); "
                    f"banking from {cur}"
                )
                _truncate(unit)
                continue
            # (T5) Step-onto-own-probe crush. Stepping onto a friendly probe
            # cell destroys it just like a drop does. If the probe is still
            # valuable (>= 2 nights) and there is no visible loot on the
            # cell, the step is wasteful — truncate here to bank what we
            # have and preserve the probe's vision. If the probe is expiring
            # or loot sits on the cell, allow the step (justified crush).
            if (tx, ty) in probe_cells:
                nights = probe_nights.get((tx, ty), 2)
                has_loot = red.get((tx, ty), 0) > 0 or blue.get((tx, ty), 0) > 0
                if nights >= 2 and not has_loot:
                    log.append(
                        f"{unit}: truncated chain before step->{(tx, ty)} "
                        f"(would crush own probe, {nights} nights vision, no "
                        f"loot); banking from {cur}"
                    )
                    _truncate(unit)
                    continue
            out.append(m)
            _release(unit)   # vacate the cell we are stepping off
            pos[unit] = (tx, ty)
            claimed[(tx, ty)] = unit
            continue

        # Unknown action — keep it; the engine will ignore anything invalid.
        out.append(m)

    # (T6) DEPLOY-ALL-HARVESTERS. A harvester left in orbit banks nothing —
    # on the final night that is pure lost points, every night it is wasted
    # EV. For each still-idle orbit harvester, append the top UNUSED
    # heuristic chain that is legal for it (drop -> steps -> pickup). This
    # is the structural fix for the "primary/finisher only deployed one of
    # two harvesters and left the 256-pt chain on the table" failure.
    if chain_hints and deploy_idle:
        idle = [
            u for u in _orbit_harvester_ids(agent_view)
            if u not in deployed and u not in done
        ]
        used_hint_ids: Set[int] = set()
        # Reserve one slot for a final pickup; keep the whole plan <= 21.
        _CAP = 21
        for unit in idle:
            if len(out) >= _CAP - 1:
                break
            chain = _pick_legal_chain(
                unit, chain_hints, used_hint_ids, _legal_land,
            )
            if not chain:
                continue
            dcell = chain[0]
            out.append({"a": "drop", "unit": unit, "at": [dcell[0], dcell[1]]})
            pos[unit] = dcell
            claimed[dcell] = unit
            deployed.add(unit)
            steps_added = 0
            for cx, cy in chain[1:]:
                if len(out) >= _CAP - 1:  # keep room for the pickup
                    break
                out.append({"a": "step", "unit": unit, "to": [cx, cy]})
                pos[unit] = (cx, cy)
                claimed[(cx, cy)] = unit
                steps_added += 1
            out.append({"a": "pickup", "unit": unit})
            done.add(unit)
            log.append(
                f"{unit}: deployed idle harvester on unused chain from "
                f"{dcell} (+{steps_added} steps) — was left in orbit"
            )

    # Any harvester still on the surface with no pickup would dawn-crash.
    # Insert a terminal pickup so it banks and returns.
    for unit, p in pos.items():
        if p is not None and unit not in done:
            out.append({"a": "pickup", "unit": unit})
            log.append(f"{unit}: appended missing pickup at {p} (crash guard)")
            done.add(unit)

    # (T8) OUTING MOVEMENT CAP. Clip each harvester's chain to at most
    # _MAX_STEPS_PER_OUTING steps since its last drop (RULEBOOK §3). The
    # engine cancels 6th+ steps anyway; dropping them here keeps the plan
    # honest and avoids burning hours on no-op moves. Resets on each drop
    # so a pick-up-and-re-drop legitimately starts a fresh 5-step outing.
    out, cap_log = _clip_outing_steps(out)
    log.extend(cap_log)

    return out, log


def _clip_outing_steps(moves: List[Move]) -> Tuple[List[Move], List[str]]:
    """Drop any harvester ``step`` beyond the per-outing movement cap.

    Counts steps since each unit's most recent ``drop``; steps past
    :data:`_MAX_STEPS_PER_OUTING` are removed (``drop``/``pickup``/``probe``
    are always kept). Logs once per unit that was clipped.
    """
    steps_since_drop: Dict[str, int] = {}
    clipped_units: Dict[str, int] = {}
    kept: List[Move] = []
    for m in moves:
        act = str(m.get("a") or "")
        unit = str(m.get("unit") or "")
        if act == "drop" and unit:
            steps_since_drop[unit] = 0
            kept.append(m)
        elif act == "step" and unit:
            if steps_since_drop.get(unit, 0) >= _MAX_STEPS_PER_OUTING:
                clipped_units[unit] = clipped_units.get(unit, 0) + 1
                continue
            steps_since_drop[unit] = steps_since_drop.get(unit, 0) + 1
            kept.append(m)
        else:
            kept.append(m)
    log: List[str] = [
        f"{unit}: clipped {n} step(s) past the {_MAX_STEPS_PER_OUTING}-tile "
        f"outing cap (6-parcel hold, RULEBOOK §3)"
        for unit, n in clipped_units.items()
    ]
    return kept, log


def _pick_legal_chain(
    unit: str,
    chain_hints: List[Mapping[str, Any]],
    used_hint_ids: Set[int],
    legal_land,
) -> List[Cell]:
    """Pick the first unused chain hint whose cells form a legal,
    contiguous walk for ``unit`` right now. Returns the ordered cell list
    (drop first), TRUNCATED at the first cell that is illegal / non-adjacent
    / already claimed, or ``[]`` if not even the drop cell is legal.
    """
    for hint in chain_hints:
        if id(hint) in used_hint_ids:
            continue
        cells_raw = hint.get("cells") or []
        if not cells_raw:
            continue
        try:
            drop = (int(cells_raw[0][0]), int(cells_raw[0][1]))
        except (TypeError, ValueError, IndexError):
            continue
        if not legal_land(drop[0], drop[1], unit):
            continue
        used_hint_ids.add(id(hint))
        walk: List[Cell] = [drop]
        cur = drop
        for raw in cells_raw[1:]:
            try:
                nxt = (int(raw[0]), int(raw[1]))
            except (TypeError, ValueError, IndexError):
                break
            if abs(nxt[0] - cur[0]) + abs(nxt[1] - cur[1]) != 1:
                break  # non-contiguous — stop here, bank what we have
            if not legal_land(nxt[0], nxt[1], unit):
                break
            walk.append(nxt)
            cur = nxt
        return walk
    return []
