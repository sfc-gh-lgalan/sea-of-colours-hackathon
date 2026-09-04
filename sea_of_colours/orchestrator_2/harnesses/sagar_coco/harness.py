"""sagar_coco — night entry point (clean fork of v11; changes land in Section 2).

v12 starts as a vanilla copy of the frozen v11 champion (identity rebranded only).
The planned v12 work is a Section-2 (THE BOARD NOW) redesign. Everything below is
inherited v11 verbatim.



v11 is the ASSIGNMENT-COMPREHENSION release on the current stack (haiku,
inference API). It inherits v10's whole deterministic compiler stack (faithful
packager, walk-in reachability, the PLAN-vs-CORRECTOR-vs-EXECUTED reflection
digest, seat differentiation, economy/final-night guards) and attacks the
failure class v10's seed-69 diagnostics exposed: the agent has no single coherent
model for "what should each harvester do tonight?" — value provenance
(LIVE/ECHO/EXPECTED) and posture (enemy vision/weapons/tempo) are computed
implicitly and inconsistently across five modules, producing probe-starved dead
turns, echo-pure literalism, and dual-redsign confusion.

What v11 adds vs v10 (see tabula_v11_PLAN.md — the Pyramid × Posture model):
  * Phase 1 — a provenance-tagged VALUE PYRAMID: any LIVE pure force-surfaces a
    grab, any ECHO pure walkable from live force-surfaces a walk-in, best LIVE
    mass a chain — regardless of redsign. Kills dead turns + echo literalism.
  * Phase 2 — orbit ↔ tactics coupling: reserve a probe when reachable value
    needs one; label every play's probe cost.
  * Phase 3 — a POSTURE annotation (enemy vision + droppable-hour, weapons,
    enemy probes, ahead/behind, tempo) that tunes length/timing/defensive riders.
  * Phase 4 — grounded memories: keep v10's self-execution digest + a cross-day
    strategies memory for on-the-job learning.

It owns its turn loop and FEEDING layer (the digest-aware prompt in
:mod:`.prompt`, the doctrine in :mod:`.doctrine`, the option menu in
:mod:`.agency` / :mod:`.seam_control`), while reusing v7's proven COMPUTATION
layer (hint compilers, sanitizer, wishlist, memory, weapon inference, recorder)
by import. v10 stays frozen as the fallback champion. (v12 = native-thinking bet.)

The CONTAINED TWO-CALL SPLIT (reasoning-first thinker -> moves-first mover,
both on the inference API) is native and on by default; ``SAGAR_COCO_SINGLE=1``
runs mover-only for an A/B.

Flow per turn mirrors v10: close prior-day memory, snapshot, read memory, build
the v11 prompt, (thinker ->) mover, parse/sanitize, submit, persist, return the
dispatcher envelope (with per-sub-agent audit rows).
"""

from __future__ import annotations

import hashlib
import os
import random
import time
from typing import Any, Dict, List, Mapping, Sequence

from sea_of_colours.orchestrator_2.cortex_invoker import CortexAgentInvoker
from sea_of_colours.orchestrator_2.cortex_chat import CortexChatInvoker
from sea_of_colours.orchestrator_2.harnesses.sagar_coco._v7 import (
    chat_schema,
    directive as directive_mod,
    harness as v7h,
    heuristic_chains,
    memory,
    move_sanitizer,
    opponent_weapons,
    probe_hints as probe_hints_mod,
    recorder,
)
from sea_of_colours.orchestrator_2.harnesses.sagar_coco._v7 import (
    orbit_wishlist as wishlist_mod,
)
from sea_of_colours.orchestrator_2.harnesses.sagar_coco import prompt as prompt_mod
from sea_of_colours.orchestrator_2.harnesses.sagar_coco import (
    agency as agency_mod,
    card as card_mod,
    chain_filter,
    chat_schema as v10_chat_schema,
    frontier as frontier_mod,
    hazard_memory as hazard_memory_mod,
    hint_dispersion as hint_dispersion_mod,
    journal as journal_mod,
    last_night as last_night_mod,
    option_economics as econ_mod,
    orbit as orbit_mod,
    packager,
    seam_control as seam_control_mod,
    speculative as speculative_mod,
    supersede as supersede_mod,
    value_pyramid,
    verifier as verifier_mod,
    scorch,
    tactics as tactics_mod,
)

# ── Agent identity ─────────────────────────────────────────────────────
INNER_AGENT_LABEL = "SAGAR_COCO"
THINKER_AGENT_LABEL = "SAGAR_COCO_THINKER"
# The rare JSON-finisher fallback reuses v7's dedicated finisher spec.
FINISHER_AGENT_NAME = v7h.FINISHER_AGENT_NAME

# Budgets carried over from v7/v8's contained-split config (measured on seed 42).
# SAGAR_COCO — model dials are env-overridable so a season can A/B a stronger
# thinker (claude-sonnet-4-6 answered in ~1.5 s on the build account) without a
# code change. The league has no per-turn deadline, only the wallclock caps.
_THINKER_CHAT_MODEL = os.environ.get("SAGAR_COCO_THINK_MODEL", "claude-sonnet-4-6")
_THINKER_CHAT_MAX_TOKENS = v7h._THINKER_CHAT_MAX_TOKENS
_THINKER_CHAT_WALLCLOCK_S = v7h._THINKER_CHAT_WALLCLOCK_S
# CONTAINED TWO-STAGE thinker budgets (measured on the seed-69 day-2 replay):
#   THINK — bounded prose reasoning, own hard budget (~1.5k chars observed, well
#           under this cap; the cap is the leash so it can never run away).
#   PLAN  — decision-only JSON, tiny dedicated budget so it ALWAYS lands.
_THINK_CHAT_MAX_TOKENS = 1400
_THINK_CHAT_WALLCLOCK_S = 60
_PLAN_CHAT_MODEL = os.environ.get("SAGAR_COCO_PLAN_MODEL", _THINKER_CHAT_MODEL)
_PLAN_CHAT_MAX_TOKENS = 800
_PLAN_CHAT_WALLCLOCK_S = 45
_MOVER_CHAT_MODEL = os.environ.get("SAGAR_COCO_MOVER_MODEL", _THINKER_CHAT_MODEL)
# A4 (mover latency). v10's mover is MOVES-ONLY (chat_schema._V10_MOVES_SCHEMA) —
# the thinker already reflected/planned/reasoned, so the mover no longer writes
# any prose. A full night is ~20 short move objects (~600-900 tokens of JSON),
# so we halve the v7 budget and tighten the wall clock. This is the fix for the
# 25-99s mover turns (it was spending the budget on reflection/rationale prose).
_MOVER_CHAT_MAX_TOKENS = 1_100
_WALLCLOCK_S = 40
_CONTINUATION_WALLCLOCK_S = v7h._CONTINUATION_WALLCLOCK_S
_CONTINUATION_RESPONSE_CAP = v7h._CONTINUATION_RESPONSE_CAP
_MIN_FINISHER_RED_VALUE = v7h._MIN_FINISHER_RED_VALUE
_MAX_MOVES = v7h._MAX_MOVES


def _split_on() -> bool:
    """v12 runs the contained split by default; ``SAGAR_COCO_SINGLE=1`` disables."""
    return os.environ.get("SAGAR_COCO_SINGLE", "0").strip().lower() not in (
        "1", "true", "yes",
    )


def _autofill_on() -> bool:
    """Whether the two passes that ADD unrequested plays are allowed to run.

    Two of them exist: the packager's completion pass (idle harvester onto the
    best unused chain, leftover probes onto offered targets) and the sanitizer's
    T6 deploy-all guard. Both were written against a real failure — a harvester
    left in orbit banks nothing — but both also overrode a night the agent had
    deliberately shaped, spending probes it said in writing it was holding back
    (OBS-17) and putting a harvester on an unrelated chain twenty cells off the
    seam it was meant to hold (OBS-20, OBS-27).

    v12 fix 2.3 — DEFAULT IS NOW OFF. The requirement they served ("use
    everything") did not go away; it moved UPSTREAM to the coverage check
    between THINK and PLAN (``packager.coverage_note``, fix 2.7), where the
    AGENT spends the shortfall and stays the author. Set ``SAGAR_COCO_AUTOFILL=1``
    to restore the old guarantee and measure the two against each other.
    """
    return os.environ.get("SAGAR_COCO_AUTOFILL", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _thinker_plan_summary(directive: Any) -> str:
    """One-line plan_this_turn sourced from the thinker directive (A4).

    The moves-only mover no longer authors ``plan_this_turn``; the thinker's
    posture + selected option IDs already describe the night, so summarise them
    for the memory replay. Empty when there is no directive.
    """
    if directive is None:
        return ""
    posture = str(getattr(directive, "posture", "") or "")
    plan = [str(p) for p in (getattr(directive, "plan", None) or [])]
    if plan:
        return f"{posture}: {', '.join(plan[:6])}".strip(": ").strip()
    return posture


def _synth_predicted_outcome(
    selected_options: Sequence[Any], *, is_final_night: bool,
) -> Dict[str, Any]:
    """R6 — a deterministic ``predicted_outcome`` for the reflect loop.

    The moves-only mover (A4) and the deterministic packager (R1) never author
    this field, so it read ``[predicted=?]`` every turn and the next night's
    reflect had no prediction to score against. Derive a coarse banked estimate
    from the committed deploy options (a redsign seam/hot-drop = high; a known-red
    chain/frontier = medium; probes/supersedes only = low) plus the dominant risk.
    """
    deploy = [o for o in selected_options if getattr(o, "kind", "") in
              ("seam", "hotdrop", "chain", "frontier")]
    if not deploy:
        est = "low"
    elif any(getattr(o, "kind", "") in ("seam", "hotdrop") for o in deploy):
        est = "high"
    else:
        est = "medium"
    contested = any(
        (getattr(o, "payload", None) or {}).get("contested") for o in deploy
    )
    if contested:
        risk = ("contested seam — a simultaneous rival drop, chaff or EMP during "
                "pickup could zero the grab; staggered/flanked waves are the hedge")
    elif is_final_night:
        risk = ("final night — no tomorrow to recover a crashed chain, so a "
                "collision or misread cell banks zero with no second chance")
    elif not deploy:
        risk = "no harvester deploy this turn — banks nothing but vision/denial"
    else:
        risk = ("chain could be cut short by a green cell, a crush, or the "
                "6-parcel hold filling before the richest cells")
    return {"banked_pts_estimate": est, "what_could_go_wrong": risk}


def _expected_yield_from_moves(
    moves: Sequence[Mapping[str, Any]],
    agent_view: Mapping[str, Any],
) -> Dict[str, int]:
    """Numeric expected yield over the ACTUAL COMPILED ROUTE (drop + step cells).

    Scored with the SAME engine model as the option menu
    (:func:`option_economics.yield_breakdown`), but from the moves the packager
    actually emitted — NOT the raw selected options. This closes the day-2 gap
    where ``expected`` credited a run (CH1's +461 red) the packager had dropped
    for inventory, so next night's LAST NIGHT compared expected-vs-actual on a
    route that never ran. Now ``expected`` reflects exactly what will execute, so
    a large expected-vs-actual gap means the NIGHT diverged (jam/collision/fog),
    not that the compiler quietly trimmed the plan.
    """
    cells: List[Tuple[int, int]] = []
    for m in moves or []:
        if not isinstance(m, Mapping):
            continue
        a = m.get("a")
        raw = m.get("at") if a == "drop" else (m.get("to") if a == "step" else None)
        if isinstance(raw, (list, tuple)) and len(raw) == 2:
            try:
                cells.append((int(raw[0]), int(raw[1])))
            except (TypeError, ValueError):
                continue
    yb = econ_mod.yield_breakdown(cells, agent_view)
    return {
        "red_pts": int(yb.get("red_pts") or 0),
        "blue_fissile": int(yb.get("blue_fissile") or 0),
        "green_penalty": int(yb.get("green_penalty") or 0),
    }


def _selected_target_cells(selected_options: Sequence[Any]) -> List[str]:
    """One ``ID->(x,y)`` string per selected option (its primary drop/probe cell).

    Persisted onto the memory entry so NEXT turn's reflection can show what the
    plan was AIMED at, with the REAL cells the packager will hit — the agent then
    quotes true coordinates instead of re-typing them from memory (the day-4
    ``(30,18)`` drift). Falls back to the bare id when no cell is resolvable.
    """
    out: List[str] = []
    for o in selected_options or []:
        pay = getattr(o, "payload", None) or {}
        cell = None
        waves = pay.get("waves")
        if isinstance(waves, list) and waves and isinstance(waves[0], Mapping):
            cell = waves[0].get("drop_at")
        cell = cell or pay.get("drop_at") or pay.get("at") or pay.get("probe_at")
        oid = str(getattr(o, "option_id", "?"))
        if isinstance(cell, (list, tuple)) and len(cell) == 2:
            try:
                out.append(f"{oid}->({int(cell[0])},{int(cell[1])})")
                continue
            except (TypeError, ValueError):
                pass
        out.append(oid)
    return out


def _turn_rng(session_id: str, player: str, day: int) -> "random.Random":
    """A reproducible per-(session, seat, night) RNG.

    Uses a STABLE hash (sha256, not Python's per-process ``hash``) so the same
    inputs always yield the same stream — the whole point is that a season is
    replayable and auditable, not that it is unpredictable. Because the seat id
    is part of the seed, p1/p2/p3 get DIFFERENT streams on the same board, which
    is what breaks the symmetric-determinism pile-ups (Phase 1).
    """
    digest = hashlib.sha256(
        f"{session_id}|{player}|{int(day)}".encode("utf-8")
    ).hexdigest()
    return random.Random(int(digest[:16], 16))


def clear_snapshots() -> None:
    """Test helper — v10 shares v7's process-local snapshot cache."""
    v7h.clear_snapshots()


# ── Public entry point ─────────────────────────────────────────────────
def run(
    *, store: Any, session_id: str, player: str, view: Mapping[str, Any],
    submit: bool = True,
) -> Dict[str, Any]:
    """SAGAR_COCO exception shield around the real turn.

    A harness that raises scores 0 on that battle and hands the seat to the
    orchestrator's heuristic. Whatever goes wrong inside, this always returns
    an envelope and, on a night turn, always submits a legal queue built by
    the deterministic shape planner (or an empty queue as the last resort).
    """
    try:
        return _run_inner(
            store=store, session_id=session_id, player=player, view=view,
            submit=submit,
        )
    except Exception as exc:  # noqa: BLE001 - the whole point is to catch everything
        started = time.time()
        phase = str(
            view.get("phase")
            or (view.get("agent_view", {}).get("meta", {}) or {}).get("phase")
            or ""
        ).lower()
        moves: List[Dict[str, Any]] = []
        note = f"shield: {type(exc).__name__}: {str(exc)[:160]}"
        if phase != "orbit":
            agent_view = view.get("agent_view") or {}
            try:
                moves = verifier_mod.emergency_plan(agent_view)
            except Exception as exc2:  # noqa: BLE001
                note += f" | emergency_plan failed: {type(exc2).__name__}"
                moves = []
            if submit:
                try:
                    from sea_of_colours.snowpark import engine as soc_engine
                    soc_engine.submit_policy(store, session_id, player, moves[:_MAX_MOVES])
                except Exception as exc3:  # noqa: BLE001
                    note += f" | submit failed: {type(exc3).__name__}"
        return {
            "ok": True,
            "agent_id": INNER_AGENT_LABEL,
            "runtime": "harness_in_process",
            "rationale": f"[plan=shield] [fallback=False] [exec=shape_plan] {note} moves={len(moves)}",
            "moves": moves[:_MAX_MOVES],
            "submitted_policy": bool(submit and phase != "orbit"),
            "wallclock_capped": False,
            "ms_elapsed": int((time.time() - started) * 1000),
            "response": "",
            "extras": {"inner_agent": INNER_AGENT_LABEL, "shield": note,
                       "final_moves": moves[:_MAX_MOVES], "fallback_used": False},
        }


def _run_inner(
    *, store: Any, session_id: str, player: str, view: Mapping[str, Any],
    submit: bool = True,
) -> Dict[str, Any]:
    """Drive one v10 arena turn. Returns the dispatcher audit envelope.

    ``submit`` (default True) is the live path — it commits the policy and
    persists this turn's memory/snapshot. Pass ``submit=False`` for the
    read-only ADVISOR path (``scripts/advise_v11.py``): the full
    THINK->PLAN->PACKAGE->SANITIZE pipeline runs and the trace is returned,
    but the engine is NOT told to submit and no turn memory/snapshot is
    written — so a human can play the seat and inspect "what would v11 do
    here, and why?" against the same live state without disturbing the game.
    """
    started = time.time()
    phase = str(
        view.get("phase")
        or (view.get("agent_view", {}).get("meta", {}) or {}).get("phase")
        or ""
    ).lower()
    if phase == "orbit":
        return orbit_mod.submit_orbit(store, session_id, player, view)

    agent_view = view.get("agent_view") or {}
    meta = agent_view.get("meta") or {}
    hud = agent_view.get("hud") or {}
    day = int(meta.get("day") or hud.get("day") or 0)
    day_cap = int(hud.get("season_day_cap") or 7)
    scores = hud.get("scores") or {}
    vault_score = int(scores.get(player) or 0)
    season_name = str(hud.get("season_name") or "")

    # 1. Close prior day's memory with the engine-resolved outcome.
    #    ADVISOR (submit=False) skips this: it must not mutate the seat's
    #    turn memory when a human is actually driving the seat.
    snap = v7h._SNAPSHOTS.get(v7h._snap_key(session_id, player), {})
    if submit:
        recorder.close_prior_day_if_needed(
            session_id, player,
            current_day=day,
            turn_start_score_by_day=snap.get("score_by_day", {}),
            turn_start_probes_by_day=snap.get("probes_by_day", {}),
            moves_submitted_by_day=snap.get("moves_by_day", {}),
            store=store,
            season_name=season_name,
        )

    # 2. Snapshot this turn's start state for next turn's reflection.
    #    ADVISOR: skip — a read-only pass must not clobber the reflect anchor.
    if submit:
        v7h._capture_snapshot(session_id, player, day, agent_view)

    # 2b. Part A1 — fold this view's GREEN into the seat's persistent, fog-
    #     surviving stripped/GREEN union. Green is monotonic, so the union is a
    #     safe "never drop/step here" set for the packager + sanitizer — the
    #     precaution against blind-dropping onto a prior-night stripped cell (the
    #     seed-56 −900 self-harm).
    hazard_cells = hazard_memory_mod.accumulate(
        session_id, player, agent_view, store=store, season_name=season_name,
    )
    # Fix 0.7 (OBS-45) — the same monotonic trick for BLUE. A bluesign is
    # generation-time geometry that never retires, so without this union a
    # pocket the seat mined out itself keeps being sampled as a target.
    spent_blue = hazard_memory_mod.accumulate_spent_blue(
        session_id, player, agent_view, store=store, season_name=season_name,
    )

    # 3. Read memory + the prior-day entry that grounds the REFLECT block.
    prior_entries = memory.read_recent(session_id, player, limit=5, store=store)
    prior_day_entry = next(
        (e for e in reversed(prior_entries) if int(e.get("day") or 0) == day - 1),
        None,
    )

    # v11 MEMORY OF LAST NIGHT (Section 3). Reconstructed harness-side from the
    # persisted replay frames (read-only) + the agent view — the single elegant
    # block that subsumes the old self-exec / digest / last-night / reflect
    # stack. Best-effort: any store hiccup degrades to the summary channels.
    last_night_memory = last_night_mod.collect(
        store, session_id, player, agent_view, prior_day_entry, day=day,
    )
    last_night_block = last_night_mod.format_block(last_night_memory)

    # v11 STRATEGY JOURNAL — fold LAST NIGHT's engine-truth outcome + observed
    # enemy onto the prior-day entry so the continuous thread shows what actually
    # HAPPENED and what you SAW under that day's INTENT (the reflection for that
    # day is authored by the plan pass below and stamped afterwards). Rendered in
    # place of the plain memory replay.
    journal_mod.enrich_prior_entry(prior_day_entry, last_night_memory)
    memory_replay = journal_mod.render_journal(prior_entries)

    # 4. Precompute the hint menu (v7 compilers, now with v10 seeded variability:
    #    a per-(session, seat, night) rng breaks near-tie symmetry so seats fan
    #    out instead of stacking the single deterministic best).
    turn_rng = _turn_rng(session_id, player, day)
    # A8: over-generate then dedupe by BODY overlap + a vein-value floor so two
    # harvesters are never handed near-identical chains on one small cluster
    # (the day-5 double-dig) and trace-only junk chains are dropped.
    # v11 menu-rebuild: over-generate MORE and keep more DISTINCT chains so the
    # menu actually offers a spread of juice chains to choose between (the "why so
    # few?" gap). Dedupe still collapses body-overlapping digs, so this only adds
    # genuinely different walks, and agency adds a SHORT variant per long chain.
    chain_hints = chain_filter.dedupe_and_floor(
        heuristic_chains.top_chain_hints(agent_view, max_chains=8),
    )[:5]
    # Probe menu = ANCHORED (assured/near-assured) + FRONTIER (speculative).
    #   * Anchored probes enable a known target (redsign/echo/seam extension) —
    #     they stay DETERMINISTIC (go where the value is); pulled from the shared
    #     compiler, with its frontier fog-centroid/los-edge seeds dropped.
    #   * Frontier probes are pure exploration — placed by the v10 fuzzy,
    #     enemy-aware, edge-seeking sampler so seats spread to the sides and away
    #     from ground rivals have worked instead of all diving the map centre.
    own_probe_history = v7h._historical_probe_targets(
        session_id, player, store=store,
    )
    frontier_mod.record_enemy_landings(
        session_id, player, agent_view,
        day=day, store=store, season_name=season_name,
    )
    _anchored_labels = {"redsign", "blue_sign", "echo", "seam_extension"}
    anchored_probes = [
        h for h in probe_hints_mod.top_probe_hints(
            agent_view,
            max_hints=3,
            historical_probe_positions=own_probe_history,
            rng=turn_rng,
        )
        if str(h.get("extends_from") or "") in _anchored_labels
    ]
    # Hot drops = REDSIGN (assured/contested — deterministic, seam patterns own
    # them) + BLUESIGN (speculative — per-seat SAMPLED so seats don't stack the
    # single brightest cell; the day-1 (35,21) 3-way pile-up). The bluesign
    # sampler tags its hints ``varied=True`` so the geometric pincer leaves them
    # be (they are already seat-distinct); if there is no bluesign to sample we
    # fall back to the shared compiler's bluesign picks.
    raw_hot_drops = probe_hints_mod.top_hot_drop_hints(
        agent_view, max_hints=4, rng=turn_rng,
    )
    redsign_hot_drops = [
        h for h in raw_hot_drops if str(h.get("signal_type") or "") == "redsign"
    ]
    bluesign_hot_drops = speculative_mod.sample_bluesign_hotdrops(
        agent_view, rng=turn_rng, max_hints=2, spent=spent_blue,
    ) or [
        h for h in raw_hot_drops if str(h.get("signal_type") or "") != "redsign"
    ]
    hot_drop_hints = (redsign_hot_drops + bluesign_hot_drops)[:3]
    # Ownership tagging + fallback dispersion (Fix A). personalize_hot_drops now
    # stamps the engine-truth ``mine`` flag and applies the geometric pincer only
    # as a SAFETY NET — it no-ops on ``varied`` hints (already seat-distinct).
    hot_drop_hints = hint_dispersion_mod.personalize_hot_drops(
        hot_drop_hints, agent_view, player,
    )
    # v10 agency: compile the redsign seam PATTERN menu + the ID'd option
    # registry the thinker selects from (curate -> select -> package). Built
    # BEFORE the frontier probes so this night's committed seam-probe cells can
    # be fed into the frontier sampler (below) as no-go ground.
    seam_patterns = seam_control_mod.build_seam_menu(
        agent_view, hot_drop_hints,
        seat_index=hint_dispersion_mod.seat_index(player),
        # probe stock is top-level in the live view, but some fixtures/older
        # shapes nest it under ``orbit`` — read either so the budget guard never
        # silently reads 0 (which would drop every probe-gated pattern).
        probe_stock=int(
            agent_view.get("probe_stock")
            or (agent_view.get("orbit") or {}).get("probe_stock")
            or 0
        ),
    )
    # The cells the seam menu already commits a probe to (BLIND_GRAB cover /
    # supersede, UNBEATEN_FLANK / CONTEST_DENY probes, own-seam waves). A frontier
    # (exploration) probe next to one of these is wasted vision — that ground is
    # already lit by the attack — so bar it structurally from the sampler.
    seam_probe_cells = seam_control_mod.planned_probe_cells(seam_patterns)
    # v12 — ECHO-PURE hot drop. A pure we SAW, lost vision of, and cannot walk to
    # is the one target where we know the EXACT square while the seam waves are
    # still aiming at a jittered smear. Built HERE (after the seam menu, before
    # the registry) so it can dedup against the cells a pattern already walks,
    # and appended AFTER personalize_hot_drops so the dispersion pincer cannot
    # slide the probe disk off the one cell that makes the option worth playing.
    hot_drop_hints = hot_drop_hints + value_pyramid.force_surface_echo_hotdrops(
        agent_view,
        existing_targets=seam_control_mod.planned_harvest_cells(seam_patterns),
    )
    # v11 — supersedes are offered EVERY night (not just the final night) so the
    # agent can wage redsign combat: deny a rival's landing/vision with a spare
    # probe, targeted by owner-score (blind the leader when trailing) + recency,
    # skipping probes about to expire. Stock-gated inside the builder. Built AFTER
    # the seam menu so any enemy probe a chosen seam pattern already BLINDS (its
    # ``supersede`` cell) is excluded — no double-spending a probe on a blind the
    # redsign attack performs for free.
    supersede_hints = supersede_mod.top_supersede_hints(
        agent_view, day=int(day), my_player=player, max_hints=4,
        exclude_cells=seam_control_mod.planned_supersede_cells(seam_patterns),
    )
    frontier_probes = frontier_mod.select_frontier_probes(
        agent_view,
        session_id=session_id,
        seat_index=hint_dispersion_mod.seat_index(player),
        rng=turn_rng,
        max_hints=3,
        own_history=own_probe_history,
        avoid_cells=seam_probe_cells,
        player=player,
        store=store,
    )
    # Merge, anchored first (assured value leads), dedup by cell, cap at 3.
    probe_hints = []
    _seen_probe_cells: set = set()
    for h in list(anchored_probes) + list(frontier_probes):
        at = h.get("at")
        key = (int(at[0]), int(at[1])) if isinstance(at, (list, tuple)) and len(at) == 2 else None
        if key is not None and key in _seen_probe_cells:
            continue
        if key is not None:
            _seen_probe_cells.add(key)
        probe_hints.append(h)
        if len(probe_hints) >= 3:
            break
    player_count = len(scores) if isinstance(scores, dict) and scores else 0
    # Weapon estimates feed both the doctrine gating AND the menu's per-option
    # collision-risk annotation (a rival holding EMP/chaff lifts risk), so they
    # must be computed BEFORE the menu block is rendered.
    weapon_estimates = opponent_weapons.update_estimates(
        session_id, player, agent_view,
    )
    # Wishlist first: the orbital "grab_blue" request gates whether a visible blue
    # is surfaced as a PRIORITY grab in the menu (red always wins the harvester
    # otherwise), so it must be known BEFORE the registry is built.
    wishlist = wishlist_mod.compute_wishlist(
        agent_view, day=day, opponent_weapon_estimates=weapon_estimates,
    )
    # R2.3 — derived from the engine's own vault bands (none/low/medium/high)
    # rather than the shared wishlist tag, which tests for an "empty" band the
    # engine never emits and so asked for blue on a vault holding 80 while
    # staying silent on a vault holding nothing.
    want_blue = prompt_mod.blue_is_requested(agent_view)
    blue_hints = (
        heuristic_chains.top_blue_chain_hints(agent_view, max_chains=2)
        if want_blue else []
    )
    harvesters_alive = len(probe_hints_mod._orbit_harvester_ids(agent_view))
    # Probe launches are PUBLIC (rulebook 3.15): the same stitched sighting list the
    # supersede hints use is legitimate intelligence for aiming a salvo.
    enemy_probes = probe_hints_mod._enemy_probe_cells(agent_view)
    option_registry = agency_mod.build_registry(
        agent_view=agent_view,
        seam_patterns=seam_patterns,
        hot_drop_hints=hot_drop_hints,
        probe_hints=probe_hints,
        chain_hints=chain_hints,
        supersede_hints=supersede_hints,
        blue_requested=want_blue,
        harvesters_alive=harvesters_alive,
        hazard_cells=hazard_cells,
        enemy_probes=enemy_probes,
    )
    # SAGAR_COCO: the verifier's own complete night goes on the menu as SHAPE_PLAN
    # so the model can choose it (or beat it) instead of being corrected later.
    # Listed first: an OrderedDict rebuilt with the shape option at the head.
    shape_moves_menu, shape_log_menu = verifier_mod.shape_plan(agent_view)
    shape_audit_menu = verifier_mod.audit(shape_moves_menu, agent_view)
    shape_opt = agency_mod.shape_option(
        shape_moves_menu, shape_log_menu, shape_audit_menu.value,
        shape_audit_menu.red_cells, shape_audit_menu.pures_taken,
    )
    if shape_opt is not None and shape_audit_menu.clean:
        from collections import OrderedDict as _OD
        option_registry = _OD([(shape_opt.option_id, shape_opt)] + list(option_registry.items()))
    # SAGAR_COCO tactical pricing: every weapon option gets a points figure and
    # the single best one is tagged RECOMMENDED when the Python trigger fires.
    tactics_note = tactics_mod.price_and_recommend(option_registry, agent_view, day=day, day_cap=day_cap)
    option_menu_block = agency_mod.format_menu_block(
        option_registry,
        agent_view=agent_view,
        weapon_estimates=weapon_estimates,
        probe_stock=int(
            agent_view.get("probe_stock")
            or (agent_view.get("orbit") or {}).get("probe_stock")
            or 0
        ),
        harvesters_alive=harvesters_alive,
        day=day,
        day_cap=day_cap,
    )

    prompt_kwargs: Dict[str, Any] = dict(
        agent_view=agent_view,
        day=day, day_cap=day_cap, vault_score=vault_score,
        memory_replay=memory_replay,
        chain_hints=chain_hints,
        probe_hints=probe_hints,
        hot_drop_hints=hot_drop_hints,
        blue_hints=blue_hints,
        supersede_hints=supersede_hints,
        wishlist=wishlist,
        opponent_weapon_estimates=weapon_estimates,
        prior_day_entry=prior_day_entry,
        last_night_block=last_night_block,
    )

    # 4b. CONTAINED THINKER (reasoning-first, inference API, hard cap).
    directive_block = ""
    thinker_used = False
    thinker_directive = None
    thinker_reasoning = ""
    thinker_plan_reasoning = ""
    agent_intent = ""
    agent_reflection = ""
    thinker_response_chars = 0
    thinker_ms = 0
    thinker_prompt = ""
    thinker_retried = False
    selected_options: List[Any] = []
    packable_options: List[Any] = []
    reconciliation_report: List[Dict[str, Any]] = []
    final_deploy_injected = False
    plan_prompt = ""
    if _split_on():
        thinker_used = True
        # ── STAGE 1 — THINK (bounded prose, its OWN hard budget) ───────────
        # The think call is ALLOWED to spend its whole budget reasoning; there
        # is no decision crammed into it, so nothing to starve. Capped tokens =
        # the leash that stops the ~9k-char ramble that used to eat the budget
        # and drop the decision entirely.
        thinker_prompt = prompt_mod.build_prompt(
            mode="think",
            option_menu_block=option_menu_block,
            player_count=player_count,
            **prompt_kwargs,
        )
        thinker_started = time.time()
        think_invoker = CortexChatInvoker(
            model=_THINKER_CHAT_MODEL,
            response_format=None,
            max_completion_tokens=_THINK_CHAT_MAX_TOKENS,
        )
        think_result = think_invoker.invoke(
            thinker_prompt, wallclock_cap_s=_THINK_CHAT_WALLCLOCK_S,
        )
        thinker_reasoning = str(think_result.get("response") or "").strip()
        think_ms = int((time.time() - thinker_started) * 1000)

        # ── STAGE 2 — PLAN (decision-only JSON, own budget, conditioned on the
        # bounded think) — this ALWAYS lands: the payload is tiny and the model
        # already reasoned, so it just commits. I1 retry guards a transient
        # empty body.
        # Fix 2.7 — COVERAGE CHECK, between the passes. Recover the options the
        # THINK prose named, count the harvesters and probes they spend, and if
        # anything is left idle say so in the PLAN prompt. The agent then spends
        # it (or argues why not) instead of the completion pass doing it blind.
        think_coverage = packager.coverage_note(
            agency_mod.resolve_plan(
                agency_mod.recover_plan_from_prose(
                    thinker_reasoning, option_registry,
                ),
                option_registry,
            ),
            agent_view,
            option_registry,
        )
        plan_prompt = prompt_mod.build_prompt(
            mode="plan",
            option_menu_block=option_menu_block,
            player_count=player_count,
            think_analysis=thinker_reasoning,
            coverage_note=think_coverage,
            **prompt_kwargs,
        )
        plan_invoker = CortexChatInvoker(
            model=_PLAN_CHAT_MODEL,
            # v11: the journal-aware decision schema adds the agent-authored
            # ``intent`` / ``reflection`` strings (captured below into the
            # continuous STRATEGY JOURNAL); decision fields still lead the object.
            response_format=v10_chat_schema.DECISION_RESPONSE_FORMAT,
            max_completion_tokens=_PLAN_CHAT_MAX_TOKENS,
        )
        plan_started = time.time()
        raw_directive = None
        plan_text = ""
        thinker_plan_reasoning = ""
        for _attempt in range(2):
            plan_result = plan_invoker.invoke(
                plan_prompt, wallclock_cap_s=_PLAN_CHAT_WALLCLOCK_S,
            )
            plan_text = str(plan_result.get("response") or "")
            raw_directive, _plan_reasoning = directive_mod.parse_directive_json(
                plan_text,
            )
            if _plan_reasoning:
                thinker_plan_reasoning = str(_plan_reasoning)
            if raw_directive is not None:
                break
            if _attempt == 0:
                thinker_retried = True
        plan_ms = int((time.time() - plan_started) * 1000)
        thinker_ms = think_ms + plan_ms
        # The bounded THINK prose is the audited/recoverable reasoning (it names
        # the patterns); ``thinker_response_chars`` tracks the PLAN payload.
        thinker_response_chars = len(plan_text)
        # v11 STRATEGY JOURNAL: capture the agent-authored intent (for tonight)
        # + reflection (on last night) from the plan JSON. Fall back to the THINK
        # prose for a reflection when the field was omitted so the journal is
        # never blank on a night that had a prior turn.
        agent_intent, agent_reflection = journal_mod.parse_intent_reflection(
            plan_text
        )
        if not agent_reflection and prior_day_entry is not None:
            agent_reflection = thinker_reasoning[:280]
        thinker_directive = directive_mod.sanitize_directive(
            raw_directive, agent_view,
        )
        # PLAN-HANDOFF HARDENING (I3/F4/O8): the thinker often NAMES the pattern
        # in prose (``BLIND_GRAB``, ``SS1``) but leaves the structured ``plan``
        # empty, so the decisive geometry (incl. the finder-probe supersede)
        # never reaches the mover. When the JSON plan is empty, recover the
        # ordered IDs from the chain-of-thought. Additive only — a populated
        # plan is left untouched.
        #
        # Extended: also fire when parsing produced NO directive at all (a
        # clipped/rambled thinker). Rather than let the mover freelance with no
        # recipe, build a MINIMAL directive from the prose-recovered plan so the
        # agency layer still resolves an EXECUTE-THESE block. This is the gap
        # that let a redsign night ship the wrong hot-drop: the thinker had
        # priced the pure in prose but the empty structured plan handed the
        # mover nothing, so it copied a stale offset combo.
        if thinker_directive is None and thinker_reasoning:
            recovered = agency_mod.recover_plan_from_prose(
                thinker_reasoning, option_registry,
            )
            if recovered:
                thinker_directive = directive_mod.sanitize_directive(
                    directive_mod.Directive(
                        posture=directive_mod._DEFAULT_POSTURE,
                        plan=recovered,
                    ),
                    agent_view,
                )
        if (
            thinker_directive is not None
            and not thinker_directive.plan
            and thinker_reasoning
        ):
            recovered = agency_mod.recover_plan_from_prose(
                thinker_reasoning, option_registry,
            )
            if recovered:
                thinker_directive.plan = recovered
        # AGENCY RESOLVE: expand the thinker's selected option/pattern IDs to
        # their pre-built geometry and hand the mover an EXECUTE-THESE recipe.
        # Falls back to the classic strategist-directive block when the thinker
        # selected nothing valid (off-seam nights / v7-style guidance).
        selected_options = (
            agency_mod.resolve_plan(thinker_directive.plan, option_registry)
            if thinker_directive else []
        )
        # R5: on the final night, a plan of only probes/supersedes banks zero
        # (they pay a tomorrow that never comes). If a harvester is alive but the
        # resolved plan deploys none, force in the best deploy option.
        selected_options, final_deploy_injected = (
            agency_mod.ensure_final_night_deploy(
                selected_options, option_registry,
                alive_harvesters=len(
                    probe_hints_mod._orbit_harvester_ids(agent_view)
                ),
                is_final_night=int(day) >= int(day_cap),
            )
        )
        # RULEBOOK §3.9.2 INVENTORY RECONCILIATION. The thinker may select more
        # harvest runs than there are harvesters (each makes ONE outing/night) or
        # more probes than stock. Keep the HIGHEST-VALUE options and drop the
        # rest — never silently by plan order (the day-2 gap where the richer red
        # run was cut for coming second). ``selected_options`` stays the FULL plan
        # (for plan_ids + the PLAN->COMPILED->DROPPED report); ``packable_options``
        # is what the packager actually compiles.
        packable_options, reconciliation_report = packager.reconcile_selected(
            selected_options, agent_view,
        )
        execute_block = agency_mod.format_execute_block(packable_options)
        directive_block = (
            execute_block
            or directive_mod.format_directive_block(thinker_directive)
        )

    prompt_text = prompt_mod.build_prompt(
        mode="mover", strategist_directive_block=directive_block,
        # The recipe-less fallback mover now works from the enriched OPTION MENU
        # (the standalone hint blocks were retired); pass it so build_prompt can
        # render it when there is no committed recipe.
        option_menu_block=option_menu_block,
        **prompt_kwargs,
    )

    # 5. EXECUTION. R1 (compiler pattern): when the thinker committed to a recipe
    #    (resolved options), COMPILE it to moves deterministically — coordinate
    #    faithfulness + one-drop-per-unit are guaranteed by construction, and we
    #    save the mover round-trip. The LLM mover is kept ONLY as the no-recipe
    #    fallback (off-seam nights / prose-recovery misses / packager no-op).
    result: Dict[str, Any] = {}
    response_text = ""
    decision: Dict[str, Any] = {}
    decision_error = ""
    packager_used = False
    packager_log: List[str] = []
    mover_ms = 0
    if selected_options:
        # Part A2 — the seat's LIVE-red cells (freshness=='fresh'); a contested
        # blind-grab sweep may only step onto these, never blind-walk a fogged
        # neighbour that a rival may already have stripped to green.
        live_red_cells = set(seam_control_mod._live_red(agent_view).keys())
        # Part C — carry the THINKER's own spatial/temporal constraints INTO the
        # deterministic packager (the audit's #1 lever: they used to reach only
        # the advisory LLM-mover block). ``chaff_react`` caps every chain so it
        # lifts inside the safe window.
        #
        # ``avoid`` is passed SEPARATELY from hazard memory. Unioning them made an
        # agent guess indistinguishable from an engine fact, so a self-
        # contradictory plan (pick BLIND_GRAB, avoid its own drop cell) silently
        # lost its harvester under a log line blaming hazard memory — a cause that
        # did not apply, and one that sent the whole audit down a false trail
        # (OBS-15).
        avoid_cells = set(getattr(thinker_directive, "avoid", []) or [])
        chaff_react = bool(getattr(thinker_directive, "chaff_react", False))
        packed, packager_log = packager.pack_recipe(
            packable_options, agent_view,
            chain_hints=chain_hints,
            probe_hints=probe_hints,
            supersede_hints=supersede_hints,
            forbidden_cells=hazard_cells,
            avoid_cells=avoid_cells,
            live_red_cells=live_red_cells,
            chaff_short=chaff_react,
            complete=_autofill_on(),
        )
        if packed:
            packager_used = True
            decision = {"moves": packed}
            response_text = (
                "[deterministic packager — recipe compiled to wire moves]"
            )

    if not packager_used:
        # CONTAINED MOVER (moves-first, inference API, schema-guaranteed).
        invoker = CortexChatInvoker(
            model=_MOVER_CHAT_MODEL,
            response_format=v10_chat_schema.MOVES_RESPONSE_FORMAT,
            max_completion_tokens=_MOVER_CHAT_MAX_TOKENS,
        )
        _mover_started = time.time()
        result = invoker.invoke(prompt_text, wallclock_cap_s=_WALLCLOCK_S)
        mover_ms = int((time.time() - _mover_started) * 1000)
        response_text = str(result.get("response") or "")
        decision, decision_error = v7h._extract_decision_json(response_text)

    # 6. Parse JSON; on failure, finisher retry then heuristic net.
    fallback_used = False
    fallback_reason = ""
    continuation_used = False
    response_text_continuation = ""
    if not packager_used and (decision_error or not decision.get("moves")):
        if response_text.strip():
            continuation_used = True
            cont_prompt = v7h._continuation_prompt(
                response_text,
                day=day, day_cap=day_cap,
                orbit_harvesters=probe_hints_mod._orbit_harvester_ids(agent_view),
                chain_hints=chain_hints,
                supersede_hints=supersede_hints,
            )
            # SAGAR_COCO — the v7 finisher targeted a Cortex AGENT OBJECT
            # (SOC_RED_REAPER_TABULA_V7_FINISHER) that was deleted when V12
            # moved to chat completions, so every repair call failed and the
            # turn dropped to the heuristic. Ported to the chat API with the
            # moves schema so the repair actually repairs.
            finisher_invoker = CortexChatInvoker(
                model=_MOVER_CHAT_MODEL,
                response_format=v10_chat_schema.MOVES_RESPONSE_FORMAT,
                max_completion_tokens=_MOVER_CHAT_MAX_TOKENS,
            )
            cont_result = finisher_invoker.invoke(
                cont_prompt,
                wallclock_cap_s=_CONTINUATION_WALLCLOCK_S,
            )
            cont_text = str(cont_result.get("response") or "")
            response_text_continuation = cont_text
            cont_decision, cont_err = v7h._extract_decision_json(cont_text)
            if not cont_err and cont_decision.get("moves"):
                decision = cont_decision
            else:
                fallback_used = True
                fallback_reason = f"finisher_failed: {cont_err or 'missing moves'}"
                decision = v7h._heuristic_fallback_decision(chain_hints, day)
        else:
            fallback_used = True
            fallback_reason = (
                f"parse: {decision_error}" if decision_error
                else "parse: missing 'moves' key"
            )
            decision = v7h._heuristic_fallback_decision(chain_hints, day)

    proposed = list(decision.get("moves") or [])

    # 7. Mechanical move sanitizer (skip the already-legal heuristic plan).
    sanitizer_log: List[str] = []
    if not fallback_used and proposed:
        is_final_night = int(day) >= int(day_cap)
        enemy_probe_cells = {
            (int(h["probe_at"][0]), int(h["probe_at"][1]))
            for h in supersede_hints
            if isinstance(h.get("probe_at"), (list, tuple))
            and len(h["probe_at"]) == 2
        }
        proposed, sanitizer_log = move_sanitizer.sanitize_moves(
            proposed,
            agent_view,
            chain_hints=chain_hints,
            is_final_night=is_final_night,
            enemy_probe_cells=enemy_probe_cells,
            # v10: rich blue under a probe is loot — don't reroute off pure blue
            # (the "walked around its own live blue" bug on the dual-redsign day).
            blue_is_loot=True,
            # v11 Part A1: the fog-surviving stripped/GREEN union as a backstop —
            # catches a fogged rival-stripped cell a probe re-lit that the
            # view-only green helpers miss (the seed-56 blind-drop-onto-green).
            extra_bad_cells=hazard_cells,
            # The sanitizer's own deploy-all guard is the SECOND pass that adds
            # a play the agent did not ask for; it answers to the same switch
            # as the packager's completion pass so a measurement moves both.
            deploy_idle=_autofill_on(),
            # A cell is blocked only while a unit is STANDING on it, not for the
            # rest of the night because someone walked through (OBS-27). The
            # trail model truncated the run carrying the pure on
            # SNAP_408ddd46_d4_p1 in all six baseline runs.
            release_vacated_cells=True,
        )
        if continuation_used and chain_hints:
            if v7h._plan_red_value(proposed, agent_view) <= _MIN_FINISHER_RED_VALUE:
                fallback_used = True
                fallback_reason = "finisher_low_value: swapped to heuristic chain"
                decision = v7h._heuristic_fallback_decision(chain_hints, day)
                proposed = list(decision.get("moves") or [])
                sanitizer_log.append(
                    "swapped finisher plan -> heuristic (finisher banked ~no "
                    "reachable RED while chains were available)"
                )

    # 7b. SAGAR_COCO PLAN VERIFIER. The model proposed, the packager compiled,
    #     the sanitiser repaired; now audit the queue against the doctrine
    #     invariants and compare it with a deterministic shape plan built from
    #     the same fog-limited view. Highest verified value wins, ties to the
    #     LLM, and every rejection reason lands on the card.
    verifier_choice = verifier_mod.choose(proposed, agent_view)
    verifier_source = verifier_choice.source
    if verifier_source not in ("llm",):
        # Weapons are orthogonal to the harvest shape: keep whatever ordnance the
        # model committed to (one salvo, one flare at most) at the head of the queue.
        ordnance = [m for m in proposed if isinstance(m, Mapping) and str(m.get("a")) in ("emp_launch", "chaff_flare")]
        proposed = ordnance[:2] + list(verifier_choice.moves)
        sanitizer_log.append(f"verifier replaced the plan ({'; '.join(verifier_choice.reasons)})")
        # the shape plan is not a fallback: it is the agent's own doctrine
        fallback_used = False
        fallback_reason = ""
    # Tactical trigger: if the trigger fired and the model still did not fire, the
    # deterministic layer adds the recommended salvo. Firing is priced, not forbidden.
    proposed, tactic_notes = tactics_mod.enforce_recommended(proposed, option_registry, agent_view)
    sanitizer_log.extend(tactic_notes)
    # The salvo guard runs last: a salvo is worth its slot at hour 1 or 2 and little after.
    proposed, salvo_notes = scorch.enforce_early_salvo(proposed)
    sanitizer_log.extend(salvo_notes)
    # FINAL GATE. The tactical layer mutated a queue the verifier had already signed,
    # so re-check exactly what that injection can break: one charge of each, the
    # flare after the last pickup, the salvo at hour 1, the 21-slot cap and the
    # probe stock. Ordnance is shed before any harvest order.
    proposed, gate_notes = verifier_mod.final_gate(proposed, agent_view)
    sanitizer_log.extend(gate_notes)

    # 8. Cap + submit.
    final_moves = proposed[:_MAX_MOVES]
    if submit:
        v7h._record_moves(session_id, player, day, final_moves)
        from sea_of_colours.snowpark import engine as soc_engine
        submit_result = soc_engine.submit_policy(
            store, session_id, player, final_moves,
        )
    else:
        # ADVISOR: compute-only. Do not touch the engine or the move ledger.
        submit_result = {"ok": True, "advise": True}

    # 9. Persist this turn's memory entry.
    #
    # A4: the v10 mover is MOVES-ONLY now, so it emits no plan/rationale/memory
    # prose. Source the turn narrative from the THINKER (which already reflected
    # and reasoned) instead, falling back to whatever the mover happened to emit
    # (belt-and-braces for the non-split legacy path). This keeps the memory
    # replay + audit rich WITHOUT paying the mover's prose-generation latency.
    plan_this_turn = str(decision.get("plan_this_turn") or "")
    rationale = str(decision.get("rationale") or "")
    predicted_outcome = decision.get("predicted_outcome") or {}
    memory_note = str(decision.get("memory_note") or decision.get("note") or "")
    if thinker_used:
        plan_this_turn = plan_this_turn or _thinker_plan_summary(thinker_directive)
        rationale = rationale or thinker_plan_reasoning[:400]
        memory_note = memory_note or thinker_reasoning[:200]
    # R6: the packager/moves-only mover emits no prediction — synthesize a coarse
    # one from the committed deploy options so the reflect loop has a target.
    if not predicted_outcome and selected_options:
        predicted_outcome = _synth_predicted_outcome(
            selected_options, is_final_night=int(day) >= int(day_cap),
        )
        decision["predicted_outcome"] = predicted_outcome
    entry = memory.new_entry(
        day,
        plan_this_turn=plan_this_turn,
        rationale=rationale,
        predicted_outcome=predicted_outcome,
        moves_summary=v7h._summarise_moves(final_moves),
        memory_note=memory_note,
    )
    # A/E — persist the PLAN vs CORRECTOR vs EXECUTED trio so next turn's reflect
    # can render an engine-truth "what you did" digest (kills the day-4-style
    # confabulation where a 0-move night is narrated as a 6-parcel harvest).
    entry["plan_ids"] = [str(o.option_id) for o in selected_options]
    entry["planned_cells"] = _selected_target_cells(selected_options)
    entry["corrector_notes"] = [str(s) for s in (sanitizer_log or [])][:8]
    # The COMPILER's own edits — relocations, truncations, refusals, completion
    # additions. These were the largest of the three rewrites and the only one
    # the agent was never shown, so a night could ship sharing a single move
    # with the night that was planned and read as if it had gone to plan
    # (OBS-20). Kept separate from the corrector's notes because the two answer
    # different questions: the compiler changed WHAT was attempted, the
    # corrector changed HOW it was expressed.
    entry["compiler_notes"] = [str(s) for s in (packager_log or [])][:8]
    entry["moves_executed"] = len(final_moves)
    # Fix 0.6 (OBS-45) — the beacons live at THIS plan, so tomorrow can diff
    # them and report any that went dark. Retirement is global and silent: the
    # view drops a spent region entirely, so without a record of what we saw
    # tonight there is nothing left to notice its absence against.
    entry["live_redsigns"] = last_night_mod.live_redsign_centres(agent_view)
    # Numeric expected yield (engine-scored) over the ACTUAL COMPILED ROUTE, so
    # NEXT night's LAST NIGHT block can show expected-vs-actual R/B/G against what
    # really ran (not what was selected). save_entry JSON-dumps the whole entry,
    # so this rides through with no memory-schema change.
    entry["expected_yield"] = _expected_yield_from_moves(final_moves, agent_view)
    # RULEBOOK §3.9.2 — the packager's PLAN -> COMPILED -> DROPPED reconciliation
    # (value-based inventory fit). Persisted so next turn's LAST NIGHT can teach
    # the agent WHICH selected options were dropped and WHY (learn to plan within
    # inventory). Only kept when something was actually dropped (a no-op otherwise
    # keeps the memory entry lean).
    if any(r.get("status") == "dropped" for r in reconciliation_report):
        entry["reconciliation"] = reconciliation_report
    # v11 STRATEGY JOURNAL: this turn's agent-authored INTENT (falls back to the
    # plan summary in single-mover mode / when the field was omitted).
    entry["intent"] = (agent_intent or plan_this_turn)[:280]
    if submit:
        memory.save_entry(
            session_id, player, entry, store=store, season_name=season_name,
        )
        # Back-fill the prior day's REFLECTION (authored this turn about last
        # night) + the engine-truth outcome/enemy summaries onto that day's entry
        # so the journal record for it is self-contained. Re-save the mutated
        # prior entry (MERGE is idempotent per (session, player, day)).
        if prior_day_entry is not None and (
            agent_reflection or prior_day_entry.get("happened")
        ):
            journal_mod.enrich_prior_entry(
                prior_day_entry, last_night_memory, reflection=agent_reflection,
            )
            memory.save_entry(
                session_id, player, prior_day_entry,
                store=store, season_name=season_name,
            )

    ms_elapsed = int((time.time() - started) * 1000)
    payload = {
        "ok": True,
        "agent_id": INNER_AGENT_LABEL,
        "runtime": "harness_in_process",
        "rationale": (
            f"[plan={plan_this_turn[:80]}] "
            f"[predicted={v7h._pred_label(decision)}] "
            f"[fallback={fallback_used}"
            + (f":{fallback_reason[:120]}" if fallback_used else "")
            + "]"
            + (" [exec=packager]" if packager_used else " [exec=mover]")
            + f" [verifier={verifier_source}]"
            + (" [continuation=recovered]"
               if (continuation_used and not fallback_used) else "")
            + (f" [sanitized={len(sanitizer_log)}]" if sanitizer_log else "")
            + (f" [thinker={thinker_directive.posture}"
               + (";chaff" if thinker_directive.chaff_react else "") + "]"
               if thinker_directive
               else (" [thinker=none]" if thinker_used else ""))
            + f" moves={len(final_moves)}"
        ),
        "moves": final_moves,
        "submitted_policy": bool(submit),
        "wallclock_capped": bool(result.get("wallclock_capped")),
        "ms_elapsed": ms_elapsed,
        "response": response_text,
        "extras": {
            "inner_agent": INNER_AGENT_LABEL,
            "plan_label": plan_this_turn[:80],
            "predicted_outcome": predicted_outcome or None,
            "reflection_on_last_night": decision.get("reflection_on_last_night"),
            "materialized_count": len(final_moves),
            # Full compiled queue (additive) so read-only tooling / the season
            # capture can review exactly what was submitted, not just the count.
            "final_moves": list(final_moves),
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "verifier": {
                "source": verifier_source,
                "reasons": verifier_choice.reasons,
                "llm_audit": verifier_choice.llm_audit.summary() if verifier_choice.llm_audit else None,
                "shape_audit": verifier_choice.shape_audit.summary(),
                "shape_log": verifier_choice.shape_log,
            },
            "packager_used": packager_used,
            "packager_log": packager_log,
            "final_deploy_injected": final_deploy_injected,
            "mover_ms": mover_ms,
            "sanitizer_changes": sanitizer_log,
            "continuation_used": continuation_used,
            "continuation_response_chars": len(response_text_continuation),
            "thinker_used": thinker_used,
            "thinker_directive": (
                {
                    "posture": thinker_directive.posture,
                    "targets": thinker_directive.targets,
                    "chaff_react": thinker_directive.chaff_react,
                    "avoid": thinker_directive.avoid,
                    "note": thinker_directive.note,
                    "plan": thinker_directive.plan,
                    "situational": thinker_directive.situational,
                }
                if thinker_directive else None
            ),
            "option_menu_ids": list(option_registry.keys()),
            # The full human-readable option menu the thinker chose from — the
            # heuristic-surfaced chains / probes / hot-drops / seam patterns with
            # coordinates + probe costs. Surfaced for the read-only advisor so a
            # human can see EVERY option that was on the table, not just the ids.
            "option_menu_block": option_menu_block,
            "selected_option_ids": [o.option_id for o in selected_options],
            "seam_pattern_ids": [p.pattern_id for p in seam_patterns],
            "thinker_response_chars": thinker_response_chars,
            "thinker_api": "chat" if thinker_used else "",
            # EXACTLY what the model receives — the full, untruncated prompt text
            # for each pass (the advisor prints these verbatim so a human can
            # audit the input the same way the model sees it).
            "thinker_prompt": thinker_prompt,
            "plan_prompt": plan_prompt,
            "mover_prompt": prompt_text,
            "thinker_reasoning": thinker_reasoning,
            "agent_intent": agent_intent,
            "agent_reflection": agent_reflection,
            "thinker_ms": thinker_ms,
            "thinker_retried": thinker_retried,
            "sub_invocations": (
                [
                    {
                        "label": "SAGAR_COCO_THINK",
                        "kind": "think",
                        "api": "chat",
                        "response_text": thinker_reasoning,
                        "rationale": "[think pass — bounded reasoning]",
                        "ms_elapsed": think_ms,
                        "prompt_excerpt": thinker_prompt[:32_000],
                    },
                    {
                        "label": THINKER_AGENT_LABEL,
                        "kind": "plan",
                        "api": "chat",
                        "response_text": plan_text,
                        "rationale": (
                            f"[posture={thinker_directive.posture}"
                            + (";chaff" if thinker_directive.chaff_react else "")
                            + f"] plan={[o.option_id for o in selected_options]}"
                            + f" targets={thinker_directive.targets}"
                            if thinker_directive else "[plan=no directive]"
                        ),
                        "ms_elapsed": plan_ms,
                        "prompt_excerpt": plan_prompt[:32_000],
                    },
                ]
                if thinker_used else []
            ),
            "mover_api": "chat",
            "mover_capped": bool(result.get("capped")),
            "prompt_chars": len(prompt_text),
            "response_chars": len(response_text),
            "submit_result": submit_result,
            "prompt_excerpt": prompt_text[:32_000],
        },
    }
    # A season night happens ONCE and the audit's prompt_excerpt is truncated
    # at 32 KB — short of the OPTION MENU. When SOC_CARD_DUMP_DIR is set, keep
    # the whole card on disk so a batch of seasons can be read back as files.
    card_mod.dump_if_enabled(
        session_id=session_id, seat=player, agent_view=agent_view,
        res=payload, season_name=season_name,
    )
    return payload
