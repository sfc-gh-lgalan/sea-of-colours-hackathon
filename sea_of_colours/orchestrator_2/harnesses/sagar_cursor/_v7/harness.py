"""tabula_v7 — night entry point.

Diff from v4: a doctrine rewrite for comprehension, not new mechanics.
v4's rules/strategies were a single always-on wall of text (and, due
to a copy-paste import, v4 actually shipped v3's verbose probe prose
rather than its own slim mandate). v5 fixes the wiring (fully self-
contained package — every import resolves inside ``tabula_v7``) and
restructures the doctrine around two ideas:

  1. STATE-TRIGGERED strategy. The static prompt carries only a lean,
     always-true CORE (objective + a numbered per-turn procedure +
     the probe mandate + the harvester EV ladder + one canonical
     hot-drop statement). Situational doctrine is appended by
     ``prompt.build_prompt`` ONLY when the live state calls for it:
       * DOCTRINE_BLUE     — setup night OR the orbit wishlist raised
                             ``grab_blue`` (the agent never decides its
                             own blue-need; the orbit bot does).
       * DOCTRINE_REDSIGN  — a redsign broadcast is on the board.
       * DOCTRINE_BEWARE_* — the opponent-weapon tracker flags EMP or
                             chaff stock (unchanged gating from v4).
     A quiet solo night is CORE-only; blue/redsign/weapon text appears
     exactly when it matters, keeping haiku's attention on the plan.

  2. Correct facts. A probe's Euclidean radius-4 disk (~49 cells) is
     BOTH its vision AND its drop-legal zone (cell (x,y) is legal off a
     probe at (cx,cy) iff (x-cx)^2+(y-cy)^2<=16). v4 wrongly advertised
     the 81-cell Chebyshev 9x9 box as drop-legal, so the model dropped on
     box corners the engine rejects, crashing hot-drop chains. The
     DROP-LEGAL block, the hot-drop hints, and the move validator now all
     use the Euclidean disk.

New primary agent SOC_RED_REAPER_TABULA_V7 and finisher
SOC_RED_REAPER_TABULA_V7_FINISHER. v1-v4 remain isolated at their
own bindings.

Flow per turn:

  1. Close prior day's memory (reflect: fill actual_banked + crushes)
  2. Snapshot turn-start state (score, probe cells) for use next turn
  3. Read the last N memory entries → format as replay prose
  4. Build prompt (rules + gated strategies + state + hints + schema)
  5. Invoke Cortex agent SOC_RED_REAPER_TABULA_V7 (single call)
  6. Parse JSON response strictly; on failure, invoke the v5 finisher
  7. Submit the move queue via engine.submit_policy
  8. Write this turn's memory entry (plan + rationale + prediction + moves)
  9. Return an envelope compatible with the orchestrator_2 dispatcher

Only fires on planning phase. Orbit routes to :mod:`.orbit_stub`.
"""

from __future__ import annotations

import json as _json
import os
import re
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

from sea_of_colours.orchestrator_2.cortex_invoker import CortexAgentInvoker
from sea_of_colours.orchestrator_2.cortex_chat import CortexChatInvoker
from sea_of_colours.orchestrator_2.harnesses.sagar_cursor._v7 import (
    chat_schema,
    directive as directive_mod,
    heuristic_chains,
    memory,
    move_sanitizer,
    opponent_weapons,
    orbit_stub,
    orbit_wishlist as wishlist_mod,
    probe_hints as probe_hints_mod,
    prompt as prompt_mod,
    recorder,
    validators,
)


# ── Agent identity + budgets ───────────────────────────────────────────
AGENT_NAME = "SOC_RED_REAPER_TABULA_V7"
INNER_AGENT_LABEL = "TABULA_V7"
# The continuation finisher — a companion Cortex agent with the same
# game doctrine but a strict JSON-only response contract. Invoked
# ONLY when the primary agent's response can't be parsed. The primary
# spec's "strategic pilot" identity primes analytical prose output;
# when we asked the primary itself to "finish" its own draft it kept
# reverting to re-analysis. A dedicated finisher spec avoids that
# by having the response contract wired in at the spec level.
FINISHER_AGENT_NAME = "SOC_RED_REAPER_TABULA_V7_FINISHER"
# v7 two-call split — call 1 (the STRATEGIST). Reasons on the page, then
# commits a tiny DECISION directive that we inject into the mover prompt.
# Gated by TABULA_V7_MODE=split (default single = frozen v6 behaviour), so
# every split turn is strictly comparable to the v6 champion.
THINKER_AGENT_NAME = "SOC_RED_REAPER_TABULA_V7_THINKER"
# Thinker wallclock. IMPORTANT (measured 2026-07): haiku-4-5 on the Cortex
# Agents API does ~25-30KB of AUTOMATIC extended thinking (diverted to the
# invoker's thinking channel) BEFORE it streams any answer text, so the
# DECISION line only lands at ~40-45s. An 18s cap truncated it mid-think every
# night → empty answer → no directive → the split silently degraded to v6.
# We give it 50s so the DECISION reliably streams. NB: this means a split turn
# (thinker + mover) exceeds the 55s live wall — split mode is an A/B / headless
# investigation setting, NOT yet live-play-ready (see PLAN.md wallclock note).
_THINKER_WALLCLOCK_S = 50
_THINKER_RESPONSE_CAP = 6_000

# Contained-thinker (inference-API) config. The containment fix, now applied to
# the THINKER instead of only the mover: the thinker runs on the Cortex
# inference API with a reasoning-FIRST json-schema (chat_schema.DECISION_
# RESPONSE_FORMAT) and a HARD token cap. It reasons on the page (the ``reasoning``
# field is generated BEFORE the decision fields, so those tokens condition the
# decision — genuine bounded CoT), but structurally CANNOT ramble past the cap.
# This is why the Agents-API thinker blew the 50s wall and this one won't.
# Selected with TABULA_V7_THINKER_API=chat (now the default when split is on).
_THINKER_CHAT_MODEL = "claude-haiku-4-5"
# Roomier than the mover's 2000: the thinker must fit its full CoT + the decision
# object. Measured on seed 42: at 2500 the two longest nights (rich redsign /
# final-convert boards) ran the reasoning-first CoT past the cap and truncated
# the object before it closed → no parseable directive. 3600 clears those; the
# _salvage_reasoning() fallback still preserves the CoT if any night overruns.
_THINKER_CHAT_MAX_TOKENS = 3_600
_THINKER_CHAT_WALLCLOCK_S = 40

# Mover backend. Default "agents" = the Cortex Agents API (SOC_RED_REAPER_TABULA_V7
# spec). "chat" = the Cortex inference /chat/completions API via CortexChatInvoker
# — a HARD-capped, json-schema-guaranteed answer with no orchestration-loop
# runaway (see cortex_chat.py). This is the containment fix: an answer is always
# complete within the token cap. Selected with TABULA_V7_MOVER_API=chat.
_MOVER_CHAT_MODEL = "claude-haiku-4-5"
# ~2000 answer tokens fits a 21-move plan + terse prose comfortably.
_MOVER_CHAT_MAX_TOKENS = 2_000
# v3 prompt grew from ~13KB (initial) → ~25KB (opponent weapons + gated
# doctrine + last-night truth + nomadic doctrine). Bumped from 30s → 60s
# to give haiku headroom on the larger reasoning surface without falling
# to the heuristic fallback on complex nights (see days 5/7 of the solo
# normal run — those timed out on 30s).
_WALLCLOCK_S = 60
# Response cap: a full 21-move plan plus the reflection/rationale/
# predicted/memory prose routinely exceeds 3000 bytes; when it did, the
# invoker appended a "[…response capped]" marker that corrupted the JSON
# and forced a finisher/heuristic fallback (the v5-rerun score collapse:
# 3 of 7 solo nights fell to the heuristic). Under the "more tokens, not
# more time" constraint we raise the BYTE cap generously — it costs no
# wallclock (the ``_arena_json_complete`` predicate still closes the
# socket the instant a balanced JSON object with ``moves`` streams, so a
# well-behaved response returns just as fast) and it stops the truncation.
_RESPONSE_CAP = 8_000
_MAX_MOVES = 21

# Continuation retry — invoked when the first haiku call runs out of
# budget mid-plan. Roomier than before so the finisher can actually close
# a large plan instead of hitting its own byte cap and cascading to the
# heuristic fallback.
_CONTINUATION_WALLCLOCK_S = 20
_CONTINUATION_RESPONSE_CAP = 4_000

# (D) Below this total purity×tier value across a finisher plan's harvested
# cells, we consider the plan to have banked "essentially nothing" and
# prefer the deterministic heuristic chain instead. One trace cell (~p10)
# is ~7.5; a single vein (~p94) is ~94. The threshold sits above a lone
# trace parcel but below any real vein, so only genuinely empty wanders
# (the day-7 failure) get swapped.
_MIN_FINISHER_RED_VALUE = 30.0


# Turn-start snapshots keyed by (session_id, player) so we can compute
# banked-this-night on the NEXT turn. Process-local; fine for a single
# season run. Tests reset via ``clear_snapshots``.
_SNAPSHOTS: Dict[str, Dict[str, Any]] = {}


def _snap_key(session_id: str, player: str) -> str:
    return f"{session_id}::{player}"


def _capture_snapshot(
    session_id: str, player: str, day: int, agent_view: Mapping[str, Any],
) -> None:
    key = _snap_key(session_id, player)
    slot = _SNAPSHOTS.setdefault(key, {"score_by_day": {}, "probes_by_day": {},
                                       "moves_by_day": {}})
    hud = agent_view.get("hud") or {}
    scores = hud.get("scores") or {}
    me = ((agent_view.get("meta") or {}).get("player")) or player
    slot["score_by_day"][int(day)] = int(scores.get(me) or 0)
    entities = (agent_view.get("entities") or {}).get("mine") or []
    probe_cells = set()
    for e in entities:
        if not isinstance(e, Mapping):
            continue
        if str(e.get("type") or "") != "probe":
            continue
        pos = e.get("pos")
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            try:
                probe_cells.add((int(pos[0]), int(pos[1])))
            except (TypeError, ValueError):
                continue
    slot["probes_by_day"][int(day)] = probe_cells



def _record_moves(
    session_id: str, player: str, day: int, moves: List[Mapping[str, Any]],
) -> None:
    key = _snap_key(session_id, player)
    slot = _SNAPSHOTS.setdefault(key, {"score_by_day": {}, "probes_by_day": {},
                                       "moves_by_day": {}})
    slot["moves_by_day"][int(day)] = [dict(m) for m in moves]


def _historical_probe_targets(
    session_id: str, player: str, store: Optional[Any] = None,
) -> List[Tuple[int, int]]:
    """Every (x,y) this player has probed earlier in the season.

    Reads the process-local ``_SNAPSHOTS.moves_by_day`` first; if empty
    (e.g. a fresh process resuming a persisted season), falls back to
    parsing ``moves_summary`` from ``memory.read_recent`` which is
    hydrated from ``SOC_AGENT_MEMORY``. Feeds the compiler's
    anti-clustering filter — see ``probe_hints.top_probe_hints`` for
    why active-only was insufficient.
    """
    positions: List[Tuple[int, int]] = []
    key = _snap_key(session_id, player)
    slot = _SNAPSHOTS.get(key) or {}
    for _day, moves in (slot.get("moves_by_day") or {}).items():
        for m in moves or []:
            if not isinstance(m, Mapping):
                continue
            if str(m.get("a") or "") != "probe":
                continue
            at = m.get("at")
            if isinstance(at, (list, tuple)) and len(at) == 2:
                try:
                    positions.append((int(at[0]), int(at[1])))
                except (TypeError, ValueError):
                    continue
    if positions:
        return positions

    # Fallback: parse ``moves_summary`` strings from persisted memory.
    # Format: ``probe@[X, Y]`` (see :func:`_summarise_moves`).
    import re
    pat = re.compile(r"probe@\[(-?\d+),\s*(-?\d+)\]")
    try:
        entries = memory.read_recent(
            session_id, player, limit=99, store=store,
        )
    except Exception:
        entries = []
    for e in entries or []:
        ms = str((e or {}).get("moves_summary") or "")
        for match in pat.finditer(ms):
            try:
                positions.append((int(match.group(1)), int(match.group(2))))
            except (TypeError, ValueError):
                continue
    return positions


def clear_snapshots() -> None:
    """Test helper — reset the per-session snapshot cache."""
    _SNAPSHOTS.clear()


def _split_mode() -> bool:
    """True when the v7 two-call reasoning split is enabled.

    Off by default so v7 is byte-for-byte the frozen v6 champion until we
    opt a run into ``TABULA_V7_MODE=split`` for A/B comparison.
    """
    return os.environ.get("TABULA_V7_MODE", "single").strip().lower() == "split"


def _mover_api() -> str:
    """Which backend the MOVER uses: ``"agents"`` (default, Cortex Agents API)
    or ``"chat"`` (Cortex inference /chat/completions — hard-capped + schema-
    guaranteed, no orchestration runaway). Set ``TABULA_V7_MOVER_API=chat``.
    """
    return os.environ.get("TABULA_V7_MOVER_API", "agents").strip().lower()


def _thinker_api() -> str:
    """Which backend the THINKER uses: ``"chat"`` (default, the CONTAINED
    thinker — Cortex inference API, reasoning-first schema, hard token cap) or
    ``"agents"`` (the legacy unbounded Agents-API thinker, kept only for A/B
    comparison — it blows the wall). Set ``TABULA_V7_THINKER_API=agents`` to
    revert. Only consulted when split mode is on.
    """
    return os.environ.get("TABULA_V7_THINKER_API", "chat").strip().lower()


# ── Public entry point ────────────────────────────────────────────────
def run(
    *, store: Any, session_id: str, player: str, view: Mapping[str, Any],
) -> Dict[str, Any]:
    """Drive one arena turn. Returns the dispatcher audit envelope."""
    started = time.time()
    phase = str(
        view.get("phase")
        or (view.get("agent_view", {}).get("meta", {}) or {}).get("phase")
        or ""
    ).lower()
    if phase == "orbit":
        # Route through the orbit dispatcher — heuristic RED_HARVEST by
        # default (ships parcels, builds harvesters+probes, jettisons
        # greens). Env var ``TABULA_V7_ORBIT_MODE=empty`` reverts to the
        # legacy no-op stub for regression tests.
        return orbit_stub.submit_orbit(store, session_id, player, view)

    agent_view = view.get("agent_view") or {}
    meta = agent_view.get("meta") or {}
    hud = agent_view.get("hud") or {}
    day = int(meta.get("day") or hud.get("day") or 0)
    day_cap = int(hud.get("season_day_cap") or 7)
    scores = hud.get("scores") or {}
    vault_score = int(scores.get(player) or 0)
    season_name = str(hud.get("season_name") or "")

    # 1. Close prior day's memory with the outcome the engine just resolved.
    snap = _SNAPSHOTS.get(_snap_key(session_id, player), {})
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
    _capture_snapshot(session_id, player, day, agent_view)

    # 3. Read the last 3 memory entries and format as replay prose.
    prior_entries = memory.read_recent(session_id, player, limit=3, store=store)
    memory_replay = memory.format_replay(prior_entries)
    # The prior day's entry (with its engine-resolved actual_banked +
    # probe_crushes, filled in by step 1 above) grounds the REFLECT block so
    # the LLM echoes the real number instead of guessing.
    prior_day_entry = next(
        (e for e in reversed(prior_entries) if int(e.get("day") or 0) == day - 1),
        None,
    )

    # 4. Build the prompt.
    chain_hints = heuristic_chains.top_chain_hints(agent_view, max_chains=3)
    probe_hints = probe_hints_mod.top_probe_hints(
        agent_view,
        max_hints=3,
        historical_probe_positions=_historical_probe_targets(
            session_id, player, store=store,
        ),
    )
    hot_drop_hints = probe_hints_mod.top_hot_drop_hints(agent_view, max_hints=2)

    # Final-night probe-supersede targets (enemy probe cells). Only
    # meaningful on the last day — build_prompt gates the block/doctrine
    # on day>=day_cap, but we only bother computing them then.
    supersede_hints = (
        probe_hints_mod.top_supersede_hints(agent_view, max_hints=4)
        if int(day) >= int(day_cap) else []
    )

    # Opponent-weapon estimates — fold this turn's station_intel +
    # activity into last turn's estimate, then hand to the wishlist
    # compiler so ``beware_emp`` / ``beware_chaff`` / ``beware_mines``
    # can fire when any opponent likely has stock. See
    # :mod:`.opponent_weapons` for the inference logic.
    weapon_estimates = opponent_weapons.update_estimates(
        session_id, player, agent_view,
    )

    # Wishlist — deterministic tactical priorities from station_intel +
    # hoard + last night. In v3 phase 1 we compute fresh at planning time
    # (equivalent to the orbit-turn compilation because inputs don't
    # materially shift between orbit resolution and next-night planning).
    # A later revision will persist the orbit-turn's wishlist and read
    # it here so we can compare "orbit's view" vs "night's view" honestly.
    wishlist = wishlist_mod.compute_wishlist(
        agent_view,
        day=day,
        opponent_weapon_estimates=weapon_estimates,
    )

    # Blue hints only when the wishlist explicitly asks for them (or
    # when blue tiles are visible AND we have >=2 harvesters). Keeps
    # the prompt clean when blue isn't on the menu.
    want_blue = any(e.tag == "grab_blue" for e in wishlist.entries)
    blue_hints = (
        heuristic_chains.top_blue_chain_hints(agent_view, max_chains=2)
        if want_blue else []
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
    )

    # 4b. (v7 split, optional) STRATEGIST reasoning pass. Call 1 reasons on
    #     the page and commits a small DECISION directive (posture + targets +
    #     chaff flag). We parse ONLY the DECISION line, sanitize it, and inject
    #     it into the mover prompt below. Any failure → empty directive → the
    #     mover runs exactly as single-call v6 (strictly >= v6 by construction).
    directive_block = ""
    thinker_used = False
    thinker_directive = None
    thinker_response_chars = 0
    thinker_reasoning = ""
    thinker_api = ""
    thinker_ms = 0
    if _split_mode():
        thinker_used = True
        thinker_api = _thinker_api()
        thinker_prompt = prompt_mod.build_prompt(mode="thinker", **prompt_kwargs)
        thinker_started = time.time()
        if thinker_api == "chat":
            # CONTAINED thinker: inference API, reasoning-first schema, hard cap.
            # The whole content is one JSON object (reasoning FIRST, then the
            # decision fields) — parse_directive_json returns both so we can act
            # on the decision AND persist/scan the chain-of-thought.
            thinker_invoker = CortexChatInvoker(
                model=_THINKER_CHAT_MODEL,
                response_format=chat_schema.DECISION_RESPONSE_FORMAT,
                max_completion_tokens=_THINKER_CHAT_MAX_TOKENS,
            )
            thinker_result = thinker_invoker.invoke(
                thinker_prompt, wallclock_cap_s=_THINKER_CHAT_WALLCLOCK_S,
            )
            thinker_text = str(thinker_result.get("response") or "")
            raw_directive, thinker_reasoning = directive_mod.parse_directive_json(
                thinker_text,
            )
        else:
            # Legacy unbounded Agents-API thinker (A/B only — blows the wall).
            thinker_invoker = CortexAgentInvoker(
                agent_name=THINKER_AGENT_NAME,
                text_completion_predicate=directive_mod.decision_line_complete,
            )
            thinker_result = thinker_invoker.invoke(
                thinker_prompt,
                wallclock_cap_s=_THINKER_WALLCLOCK_S,
                response_cap_bytes=_THINKER_RESPONSE_CAP,
            )
            thinker_text = str(thinker_result.get("response") or "")
            raw_directive = directive_mod.parse_directive(thinker_text)
        thinker_ms = int((time.time() - thinker_started) * 1000)
        thinker_response_chars = len(thinker_text)
        thinker_directive = directive_mod.sanitize_directive(
            raw_directive, agent_view,
        )
        directive_block = directive_mod.format_directive_block(thinker_directive)

    prompt_text = prompt_mod.build_prompt(
        mode="mover", strategist_directive_block=directive_block, **prompt_kwargs
    )

    # 5. Invoke the MOVER.
    mover_api = _mover_api()
    if mover_api == "chat":
        # Cortex inference path: HARD max_completion_tokens cap + json-schema
        # guarantee → a complete, valid moves object every time, no Agents-API
        # orchestration-loop runaway. This is the containment fix.
        invoker = CortexChatInvoker(
            model=_MOVER_CHAT_MODEL,
            response_format=chat_schema.MOVES_RESPONSE_FORMAT,
            max_completion_tokens=_MOVER_CHAT_MAX_TOKENS,
        )
        result = invoker.invoke(prompt_text, wallclock_cap_s=_WALLCLOCK_S)
    else:
        # Cortex Agents path (default). We hand the invoker a completion
        # predicate: as soon as a full balanced JSON object containing
        # ``"moves"`` has streamed, close the SSE socket. Kills the haiku
        # "re-emit the same object three times" pattern that saturated the
        # response cap in prior tests.
        invoker = CortexAgentInvoker(
            agent_name=AGENT_NAME,
            text_completion_predicate=_arena_json_complete,
        )
        result = invoker.invoke(
            prompt_text,
            wallclock_cap_s=_WALLCLOCK_S,
            response_cap_bytes=_RESPONSE_CAP,
        )
    response_text = str(result.get("response") or "")

    # 6. Parse JSON. On failure, fall back to heuristic chain hint.
    #    We do NOT validate individual moves here anymore — that was
    #    too punitive. The engine already rejects individual illegal
    #    moves in a submitted plan (bad drops, off-grid steps, etc.)
    #    while executing the legal ones in order. A harness-side
    #    validator that treats ANY invalid move as a full plan
    #    failure was masking the LLM's intent (e.g. discarding a
    #    valid probe launch just because the follow-up drop was
    #    illegal), and turned partial success into total fallback.
    decision, decision_error = _extract_decision_json(response_text)
    fallback_used = False
    fallback_reason = ""  # populated when fallback triggers
    continuation_used = False
    if decision_error or not decision.get("moves"):
        # 6a. Continuation retry — hand the primary's partial output to
        #     the dedicated FINISHER agent. The finisher's spec is a
        #     JSON-only completion service: same game doctrine as the
        #     primary but with a stripped response contract that
        #     forbids prose. Retrying with the primary itself failed
        #     because its own spec identity ("strategic pilot") kept
        #     defaulting back to re-analysis.
        if response_text.strip():
            continuation_used = True
            cont_prompt = _continuation_prompt(
                response_text,
                day=day,
                day_cap=day_cap,
                orbit_harvesters=probe_hints_mod._orbit_harvester_ids(agent_view),
                chain_hints=chain_hints,
                supersede_hints=supersede_hints,
            )
            finisher_invoker = CortexAgentInvoker(agent_name=FINISHER_AGENT_NAME)
            cont_result = finisher_invoker.invoke(
                cont_prompt,
                wallclock_cap_s=_CONTINUATION_WALLCLOCK_S,
                response_cap_bytes=_CONTINUATION_RESPONSE_CAP,
            )
            cont_text = str(cont_result.get("response") or "")
            cont_decision, cont_err = _extract_decision_json(cont_text)
            if not cont_err and cont_decision.get("moves"):
                decision = cont_decision
                response_text_continuation = cont_text
            else:
                response_text_continuation = cont_text
                fallback_used = True
                fallback_reason = (
                    f"finisher_failed: {cont_err or 'missing moves'}"
                )
                decision = _heuristic_fallback_decision(chain_hints, day)
        else:
            response_text_continuation = ""
            fallback_used = True
            fallback_reason = (
                f"parse: {decision_error}" if decision_error
                else "parse: missing 'moves' key"
            )
            decision = _heuristic_fallback_decision(chain_hints, day)
    else:
        response_text_continuation = ""

    proposed = list(decision.get("moves") or [])

    # 7b. Mechanical move sanitizer — the structural guardrail for the
    #     recurring geometry mistakes (no-beacon drops, needless self-crush,
    #     harvester collisions, dawn-crash-inducing missing pickups). We do
    #     NOT sanitize the heuristic fallback plan (it is already legal by
    #     construction) to avoid double-touching those moves.
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
        )

        # (D) Finisher safety net. The finisher (used only when the primary
        # failed) has been observed to improvise a plan that wanders through
        # UNMAPPED cells and banks ~nothing while a real chain sits unused
        # (the day-7 disaster: banked 1 trace parcel, ignored a 256-pt
        # chain). If a finisher plan lands on essentially no reachable RED
        # yet chain hints exist, the deterministic heuristic chain is
        # strictly better — swap to it.
        if continuation_used and chain_hints:
            if _plan_red_value(proposed, agent_view) <= _MIN_FINISHER_RED_VALUE:
                fallback_used = True
                fallback_reason = "finisher_low_value: swapped to heuristic chain"
                decision = _heuristic_fallback_decision(chain_hints, day)
                proposed = list(decision.get("moves") or [])
                sanitizer_log.append(
                    "swapped finisher plan -> heuristic (finisher banked "
                    "~no reachable RED while chains were available)"
                )

    # 8. Cap at 21 slots (engine ceiling), submit via engine.
    final_moves = proposed[:_MAX_MOVES]
    _record_moves(session_id, player, day, final_moves)

    from sea_of_colours.snowpark import engine as soc_engine
    submit_result = soc_engine.submit_policy(store, session_id, player, final_moves)

    # 9. Write this turn's memory entry (agent-authored fields only;
    #    actual_banked + crushes get filled in on NEXT turn's reflection).
    entry = memory.new_entry(
        day,
        plan_this_turn=str(decision.get("plan_this_turn") or ""),
        rationale=str(decision.get("rationale") or ""),
        predicted_outcome=(decision.get("predicted_outcome") or {}),
        moves_summary=_summarise_moves(final_moves),
        memory_note=str(decision.get("memory_note") or ""),
    )
    memory.save_entry(session_id, player, entry, store=store, season_name=season_name)

    ms_elapsed = int((time.time() - started) * 1000)
    return {
        "ok": True,
        "agent_id": INNER_AGENT_LABEL,
        "runtime": "harness_in_process",
        "rationale": (
            f"[plan={decision.get('plan_this_turn','')[:80]}] "
            f"[predicted={_pred_label(decision)}] "
            f"[fallback={fallback_used}"
            + (f":{fallback_reason[:120]}" if fallback_used else "")
            + f"]"
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
        "submitted_policy": True,
        "wallclock_capped": bool(result.get("wallclock_capped")),
        "ms_elapsed": ms_elapsed,
        # Raw text captured for audit — dispatcher lifts this into
        # ``DispatchResult.response`` which the audit writer stamps into
        # ``SOC_AGENT_INVOCATION.response_text``. Without this, fallback
        # forensics on parse/validate failures are impossible (see days
        # 3/5/7 of the solo normal run — silent fallbacks with no visible
        # LLM output).
        "response": response_text,
        "extras": {
            "inner_agent": AGENT_NAME,
            "plan_label": str(decision.get("plan_this_turn") or "")[:80],
            "predicted_outcome": decision.get("predicted_outcome"),
            "reflection_on_last_night": decision.get("reflection_on_last_night"),
            "materialized_count": len(final_moves),
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "sanitizer_changes": sanitizer_log,
            "continuation_used": continuation_used,
            "continuation_response_chars": len(response_text_continuation),
            # v7 two-call split forensics.
            "thinker_used": thinker_used,
            "thinker_directive": (
                {
                    "posture": thinker_directive.posture,
                    "targets": thinker_directive.targets,
                    "chaff_react": thinker_directive.chaff_react,
                    "avoid": thinker_directive.avoid,
                    "note": thinker_directive.note,
                }
                if thinker_directive else None
            ),
            "thinker_response_chars": thinker_response_chars,
            "thinker_api": thinker_api,
            # The contained thinker's captured chain-of-thought. Persisted so it
            # can be scanned across scenarios (scan_thinker.py) — the "does it
            # actually reason?" audit — and shown in the UI as its own step.
            "thinker_reasoning": thinker_reasoning,
            "thinker_ms": thinker_ms,
            # Multi-row audit payload: the thinker gets its OWN
            # SOC_AGENT_INVOCATION row so both reasoning passes are visible in
            # the UI (the multi-agent observability fix). The primary mover row
            # is written from the top-level envelope as usual.
            "sub_invocations": (
                [
                    {
                        "label": THINKER_AGENT_NAME,
                        "kind": "thinker",
                        "api": thinker_api,
                        "response_text": thinker_reasoning,
                        "rationale": (
                            f"[posture={thinker_directive.posture}"
                            + (";chaff" if thinker_directive.chaff_react else "")
                            + f"] targets={thinker_directive.targets}"
                            if thinker_directive else "[thinker=no directive]"
                        ),
                        "ms_elapsed": thinker_ms,
                        "prompt_excerpt": (thinker_prompt[:32_000]
                                           if thinker_used else ""),
                    }
                ]
                if thinker_used else []
            ),
            "mover_api": mover_api,
            "mover_capped": bool(result.get("capped")),
            "prompt_chars": len(prompt_text),
            "response_chars": len(response_text),
            "submit_result": submit_result,
            # 32KB slice of the prompt — audit writer caps at
            # ``PROMPT_EXCERPT_CHAR_CAP`` (also 32KB) so this is a no-op
            # truncation for typical prompts (~25KB).
            "prompt_excerpt": prompt_text[:32_000],
        },
    }


# ── Helpers ────────────────────────────────────────────────────────────
def _arena_json_complete(accum: str) -> bool:
    """Text-completion predicate for the arena's streaming SSE loop.

    Returns True once the accumulated response contains at least one
    balanced JSON object that carries the required ``moves`` field.
    Used to close the SSE socket the moment the model has emitted a
    parseable decision — killing the "same JSON three times" haiku
    hedge that used to saturate the response cap.

    Cheap by design: only calls ``json.JSONDecoder.raw_decode`` when
    braces balance and ``"moves"`` has appeared, so the fast path
    for partial streams is a simple substring check.
    """
    if '"moves"' not in accum:
        return False
    # Balance check first — cheap and eliminates 99% of false positives.
    open_count = accum.count("{")
    close_count = accum.count("}")
    if open_count < 1 or close_count < open_count:
        return False
    # Confirm with a real decoder pass. Look for the first `{` that
    # starts a parseable object containing "moves".
    decoder = _json.JSONDecoder()
    for match in re.finditer(r"\{", accum):
        try:
            parsed, _ = decoder.raw_decode(accum[match.start():])
        except Exception:
            continue
        if isinstance(parsed, dict) and "moves" in parsed:
            return True
    return False


def _continuation_prompt(
    partial_response: str,
    *,
    day: int = 0,
    day_cap: int = 7,
    orbit_harvesters: Optional[List[str]] = None,
    chain_hints: Optional[List[Mapping[str, Any]]] = None,
    supersede_hints: Optional[List[Mapping[str, Any]]] = None,
) -> str:
    """Build a 'complete this plan' prompt for the FINISHER agent.

    The old version handed over ONLY the tail of the primary's partial
    draft. That broke catastrophically when the primary never reached a
    "committing to moves" state (it spent its whole budget on analysis):
    the finisher had no committed moves to extract, so it improvised from
    whatever candidate coordinate happened to sit in the last 3KB — often
    launching a probe on the final night, superseding its own probe, and
    deploying only one of two harvesters (see the day-7 disaster).

    The fix is to hand the finisher the BOARD FACTS it needs to build a
    correct plan from scratch when the partial has no committed moves:
    the day (so final-night rules apply), the harvesters that MUST be
    deployed, the top heuristic chains (so it can commit to the real
    256-pt chain, not a candidate it half-saw in prose), and any
    final-night supersede targets. Kept compact so the finisher spends
    its wallclock emitting JSON, not re-reading context.
    """
    trimmed = partial_response.strip()
    if len(trimmed) > 3_000:
        trimmed = trimmed[-3_000:]

    is_final = int(day) >= int(day_cap)
    lines: List[str] = []
    lines.append(f"BOARD FACTS (day {day} of {day_cap}"
                 + (" — FINAL NIGHT" if is_final else "") + "):")

    harvesters = list(orbit_harvesters or [])
    if harvesters:
        lines.append(
            "  HARVESTERS IN ORBIT — you MUST deploy EVERY one of these "
            "(an idle harvester banks nothing): " + ", ".join(harvesters)
        )

    for h in (chain_hints or [])[:3]:
        cells = h.get("cells") or []
        tiers = h.get("tiers") or []
        cell_str = " -> ".join(f"({c[0]},{c[1]})" for c in cells)
        tier_str = "/".join(str(t) for t in tiers) if tiers else ""
        lines.append(
            f"  CHAIN for {h.get('unit','harvester_p1')}: drop {cell_str}"
            + (f"  [{tier_str}]" if tier_str else "")
        )

    if is_final:
        if supersede_hints:
            tgt = ", ".join(
                f"({h['probe_at'][0]},{h['probe_at'][1]})"
                for h in supersede_hints
                if isinstance(h.get("probe_at"), (list, tuple))
            )
            lines.append(
                "  FINAL-NIGHT PROBES: launch NO frontier probes. Spare "
                f"probe stock MAY supersede enemy probes at: {tgt}"
            )
        else:
            lines.append(
                "  FINAL-NIGHT: launch NO probes (no next night to harvest "
                "their disk, and there is nothing to supersede)."
            )

    facts = "\n".join(lines)

    return (
        "You are completing a Sea of Colours planning turn. A partial draft "
        "from the primary strategist is below; it ran out of budget.\n"
        "\n"
        f"{facts}\n"
        "\n"
        "=== BEGIN PARTIAL DRAFT ===\n"
        f"{trimmed}\n"
        "=== END PARTIAL DRAFT ===\n"
        "\n"
        "If the partial contains committed moves, finish them. If it does "
        "NOT (it is pure analysis with no moves array), BUILD the plan "
        "yourself from the BOARD FACTS above: deploy EVERY orbit harvester "
        "on a chain, end each with a pickup, and obey the final-night probe "
        "rule. Emit ONE complete JSON object per your response contract. "
        "JSON only. Start with the open-brace character. GO:\n"
    )


def _extract_decision_json(text: str) -> Tuple[Dict[str, Any], Optional[str]]:
    """Find the first top-level JSON object in ``text`` and parse it.

    Two-pass recovery so we don't lose the LLM's plan to a single
    misplaced brace:

      1. Standard: strip any markdown fence, walk each ``{`` position,
         return the first balanced object that carries a ``moves`` key.

      2. Salvage (when pass 1 fails): scan the raw text for a
         ``"moves":\\s*\\[ ... \\]`` array on its own and treat that as
         the decision. Observed on day 5 of the arena_solo_normal run
         where haiku emitted a valid moves array but closed the
         wrapping object one brace too early — the array survived
         verbatim outside the malformed object.
    """
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()
    decoder = _json.JSONDecoder()

    # Pass 1 — find a balanced object that has ``moves``.
    for match in re.finditer(r"\{", raw):
        try:
            parsed, _ = decoder.raw_decode(raw[match.start():])
        except Exception:
            continue
        if isinstance(parsed, dict) and "moves" in parsed:
            return parsed, None

    # Pass 2 — salvage a bare ``"moves": [...]`` array.
    moves_match = re.search(r'"moves"\s*:\s*(\[)', raw)
    if moves_match:
        start = moves_match.start(1)
        try:
            arr, _ = decoder.raw_decode(raw[start:])
        except Exception:
            arr = None
        if isinstance(arr, list):
            # Try to also recover ``plan_this_turn`` / ``rationale`` from
            # the malformed prefix so the memory entry isn't blank.
            def _grab(field: str) -> str:
                m = re.search(rf'"{field}"\s*:\s*"([^"]*)"', raw)
                return m.group(1) if m else ""
            recovered = {
                "moves": arr,
                "plan_this_turn": _grab("plan_this_turn") or "[recovered]",
                "rationale": _grab("rationale")
                    or "recovered moves array from malformed JSON",
                "predicted_outcome": {
                    "banked_pts_estimate": "medium",
                    "what_could_go_wrong": "recovered from malformed JSON",
                },
                "reflection_on_last_night": None,
                "memory_note": _grab("memory_note")
                    or "[recovered from malformed JSON]",
            }
            return recovered, None

    return {}, "no_json_object"


def _heuristic_fallback_decision(
    chain_hints: List[Mapping[str, Any]], day: int,
) -> Dict[str, Any]:
    """Compose a minimal decision from the top chain hint.

    Used when the LLM response can't be parsed or every proposed move
    fails validation. Emits a legal drop → step* → pickup chain from
    the top-scored heuristic hint. If no hint is available, emits an
    empty move queue (engine treats as pass — safer than a bad move).
    """
    if not chain_hints:
        return {
            "plan_this_turn": "[fallback] no chain hints available; passing.",
            "rationale": "compiler produced zero chains",
            "predicted_outcome": {"banked_pts_estimate": "low",
                                  "what_could_go_wrong": "no harvest attempted"},
            "moves": [],
            "memory_note": "[fallback] compiler had nothing; skipped night.",
        }
    top = chain_hints[0]
    unit = top.get("unit") or "harvester_p1"
    cells = list(top.get("cells") or [])
    if not cells:
        return {"plan_this_turn": "[fallback] hint had no cells", "rationale": "",
                "predicted_outcome": {"banked_pts_estimate": "low",
                                      "what_could_go_wrong": "hint empty"},
                "moves": [], "memory_note": "[fallback] empty hint."}
    moves: List[Dict[str, Any]] = [{"a": "drop", "unit": unit, "at": list(cells[0])}]
    for cell in cells[1:]:
        moves.append({"a": "step", "unit": unit, "to": list(cell)})
    moves.append({"a": "pickup", "unit": unit})
    return {
        "plan_this_turn": f"[fallback] follow top heuristic chain via {unit}",
        "rationale": "LLM parse/validate failed; used heuristic top chain",
        "predicted_outcome": {"banked_pts_estimate": "medium",
                              "what_could_go_wrong": "heuristic didn't consider EV"},
        "moves": moves,
        "memory_note": f"[fallback] executed heuristic chain via {unit} on day {day}.",
    }


def _plan_red_value(
    moves: List[Mapping[str, Any]], agent_view: Mapping[str, Any],
) -> float:
    """Total purity×tier over the visible-RED cells a plan actually lands
    on (drop + step targets). A cheap proxy for "how much RED will this
    plan bank" — used by the finisher safety net (D). Cells not in visible
    RED contribute 0 (e.g. wandering unmapped fog).
    """
    red = probe_hints_mod._visible_red(agent_view)
    if not red:
        return 0.0
    total = 0.0
    seen: set = set()
    for m in moves:
        if not isinstance(m, Mapping):
            continue
        act = str(m.get("a") or "")
        xy = m.get("at") if act == "drop" else m.get("to") if act == "step" else None
        if not (isinstance(xy, (list, tuple)) and len(xy) == 2):
            continue
        try:
            cell = (int(xy[0]), int(xy[1]))
        except (TypeError, ValueError):
            continue
        if cell in seen:
            continue
        seen.add(cell)
        p = red.get(cell, 0)
        if p > 0:
            total += p * heuristic_chains._TIER_MULT.get(
                heuristic_chains._tier_name(p), 1.0
            )
    return total


def _summarise_moves(moves: List[Mapping[str, Any]]) -> str:
    """Compact one-line summary of a move queue for memory storage."""
    parts: List[str] = []
    for m in moves:
        a = str(m.get("a") or "")
        if a == "drop":
            parts.append(f"drop@{m.get('at')}")
        elif a == "step":
            parts.append(f"step->{m.get('to')}")
        elif a == "pickup":
            parts.append(f"pickup({m.get('unit')})")
        elif a == "probe":
            parts.append(f"probe@{m.get('at')}")
        else:
            parts.append(a)
    return ", ".join(parts)


def _pred_label(decision: Mapping[str, Any]) -> str:
    po = decision.get("predicted_outcome") or {}
    return str(po.get("banked_pts_estimate") or "?")
