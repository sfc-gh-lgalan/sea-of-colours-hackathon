"""Storage-agnostic engine wrappers — the SOC_* procedure surface.

Each function maps 1:1 to a Snowpark Python stored procedure declared in
``snowflake/soc_procedures.sql``. They operate against a
:class:`~sea_of_colours.snowpark.store.SocStore` so the same code runs
in-process (with :class:`InMemorySocStore` for tests) and in Snowflake
(with ``SnowparkSocStore`` in Phase 3).

Persistence contract on every state-mutating call:

* :func:`save_session_full` is invoked at the end of the operation. It
  upserts ``SOC_GAME_SESSION``, ``SOC_SQUARE_IDENTITY``,
  ``SOC_ASSET_RECORD``, ``SOC_ENTITY_STATE``, ``SOC_GRID_CELL``,
  ``SOC_HOARD_PARCEL``, ``SOC_SHIPPED_PARCEL`` so cross-session SQL
  always reflects the latest tick.
* :func:`run_night` additionally streams every replay frame as a row in
  ``SOC_REPLAY_FRAME`` and every new log entry into ``SOC_GAME_LOG``,
  preserving the multi-day history Phase 4 needs.
"""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence

from sea_of_colours.game.policy import (
    MAX_MOVES,
    MAX_QUEUE_LEN,
    moves_to_wire,
    orbit_actions_to_wire,
    parse_moves,
    parse_orbit_actions,
)
from sea_of_colours.game.session import (
    GameSession,
    Phase,
    cast_player,
)
from sea_of_colours.snowpark.store import SocStore
from sea_of_colours.snowpark.view import build_agent_view


# ── Helpers ──────────────────────────────────────────────────────────


def _seat_pack(sess: GameSession) -> Dict[str, bool]:
    """Phase-aware seat pending map.

    Returns ``{p: True}`` when the seat has stashed for the *current*
    phase. PLANNING reads ``pending_policies``; ORBIT reads
    ``pending_orbit_actions``. The runtime / season runner uses this
    to decide which seat goes next without having to branch on phase
    themselves.
    """
    if sess.phase == Phase.ORBIT:
        return {
            p: sess.pending_orbit_actions.get(p) is not None for p in sess.players
        }
    return {p: sess.pending_policies.get(p) is not None for p in sess.players}


def _square_identity_rows(sess: GameSession) -> List[Dict[str, Any]]:
    """Flatten the in-memory :class:`SquareLedger` into row dicts.

    Defensive: ledger entries are keyed by ``"x:y"`` so we can always
    recover the coordinates from the key even if the value dict has
    been corrupted (e.g. a legacy save that round-tripped through a
    sparser shape). Pre-v0.9.5 this used a hard ``entry["x"]`` access
    which surfaced as a 404 ``KeyError: 'x'`` on policy submit; the
    fallback now keeps the night resolving instead of bricking the
    session.
    """
    rows: List[Dict[str, Any]] = []
    ledger = sess.ledger
    if ledger is None:
        return rows
    for key, entry in ledger.entries.items():
        x_raw = entry.get("x")
        y_raw = entry.get("y")
        if x_raw is None or y_raw is None:
            parts = str(key).split(":", 1)
            if len(parts) == 2:
                try:
                    x_raw = int(parts[0]) if x_raw is None else x_raw
                    y_raw = int(parts[1]) if y_raw is None else y_raw
                except ValueError:
                    continue
            else:
                continue
        try:
            xi = int(x_raw)
            yi = int(y_raw)
        except (TypeError, ValueError):
            continue
        rows.append({
            "session_id": sess.session_id,
            "x": xi,
            "y": yi,
            "square_id": str(entry.get("square_id", "")),
            "tile_at_generation": int(entry.get("tile_at_generation", 0)),
            "purity_at_generation": int(entry.get("purity_at_generation", 0)),
        })
    return rows


def _asset_record_rows(sess: GameSession) -> List[Dict[str, Any]]:
    return [
        {
            "session_id": sess.session_id,
            "asset_id": rec.asset_id,
            "asset_type": rec.asset_type,
            "owner": rec.owner,
            "created_on_day": rec.created_on_day,
            "first_deployed_day": rec.first_deployed_day,
            "destroyed_on_day": rec.destroyed_on_day,
            "destroyed_by": rec.destroyed_by,
            "last_seen_x": rec.last_seen_x,
            "last_seen_y": rec.last_seen_y,
            "total_red_harvested": rec.total_red_harvested,
            "total_days_on_surface": rec.total_days_on_surface,
        }
        for rec in sess.asset_records.values()
    ]


def _entity_state_rows(sess: GameSession) -> List[Dict[str, Any]]:
    return [
        {
            "session_id": sess.session_id,
            "entity_id": ent.id,
            "entity_type": ent.entity_type,
            "owner": ent.owner,
            "x": ent.x,
            "y": ent.y,
            "carrying_red": bool(ent.carrying_red),
            "orbital_cargo_red": bool(ent.orbital_cargo_red),
            "cargo_squares": [dict(c) for c in ent.cargo_squares],
            "lost_last_night": bool(ent.lost_last_night),
        }
        for ent in sess.entities.values()
    ]


def _grid_cell_rows(sess: GameSession) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for y, row in enumerate(sess.grid):
        for x, cell in enumerate(row):
            rows.append({
                "session_id": sess.session_id,
                "x": x,
                "y": y,
                "tile": int(cell.tile),
                "purity": int(cell.purity),
            })
    return rows


def _parcel_rows(
    sess: GameSession, owner: str, source: str
) -> List[Dict[str, Any]]:
    """Flatten in-memory parcels into ``SOC_*_PARCEL`` rows.

    The in-memory parcel dict produced by
    :meth:`GameSession._stash_parcel_on_harvester` uses gameplay-
    centric keys (``purity_at_harvest``, ``tile_at_harvest``,
    ``harvested_on_planning_day``, ``site_id``) that do not match
    the persistence schema's ``origin_*`` column names. We translate
    here so the SQL view (``SOC_SESSION_STANDINGS``) can sum
    ``origin_purity`` for the scoreboard — without this mapping
    every parcel persists with NULL purity and every session ends
    with score 0 (the bug that masked Phase-C scores).
    """
    parcels = (
        sess.hoard_squares if source == "hoard" else sess.shipped_squares
    )[owner]
    rows: List[Dict[str, Any]] = []
    for slot, parcel in enumerate(parcels):
        rows.append({
            "session_id": sess.session_id,
            "owner": owner,
            "slot": slot,
            "square_id": parcel.get("square_id") or parcel.get("site_id"),
            "origin_x": parcel.get("origin_x", parcel.get("x")),
            "origin_y": parcel.get("origin_y", parcel.get("y")),
            "origin_tile": parcel.get(
                "origin_tile",
                parcel.get("tile_at_harvest", parcel.get("tile")),
            ),
            "origin_purity": parcel.get(
                "origin_purity",
                parcel.get("purity_at_harvest", parcel.get("purity")),
            ),
            "harvested_day": parcel.get(
                "harvested_day",
                parcel.get(
                    "harvested_on_planning_day", parcel.get("day"),
                ),
            ),
            # NOTE: site_uid is declared INT in :file:`soc_schema.sql`
            # but the in-memory ``site_id`` is a hex slug like
            # ``sq-987f24-0001``. We deliberately leave site_uid NULL
            # rather than try to coerce a string into an int column;
            # the canonical id lives in ``square_id`` and ``payload``.
            "site_uid": parcel.get("site_uid"),
            "payload": dict(parcel),
        })
    return rows


def _save_session_row(sess: GameSession) -> Dict[str, Any]:
    state = sess.to_dict()
    # PERF (v1.5, P1): the full night replay is persisted authoritatively as
    # immutable rows in SOC_REPLAY_FRAME (served by ``get_replay`` /
    # ``list_replay_frames``). Keeping a SECOND copy inside the ``json_state``
    # blob doubled the MERGE payload on every night save — worst in 4-player
    # games where each frame carries four dense per-seat cell grids. Nothing
    # reads ``last_night_replay`` back from a reloaded blob (the replay endpoint
    # and evals both read the frame table), so we drop it from the persisted
    # state. ``to_dict()`` itself is unchanged, so the in-memory round-trip
    # (and its test) keep the field.
    state.pop("last_night_replay", None)
    return {
        "session_id": sess.session_id,
        "season_name": sess.season_name,
        "width": sess.width,
        "height": sess.height,
        "seed": sess.seed,
        "day": sess.day,
        "phase": sess.phase.value,
        "json_state": state,
    }


def save_session_full(store: SocStore, sess: GameSession) -> None:
    """Persist every SOC_* table for ``sess`` (idempotent for the caller).

    v0.9.5 — each sub-call is wrapped with a perf timer when
    ``SOC_PERF=1`` is set; the timings are printed to stderr as a
    separate block under the parent submit's summary so we can see
    which table eats the wall time.

    v0.9.6 — every save phase writes to a DIFFERENT SOC_* table, so
    they're independent at the SQL level and we fan them out across
    a small thread pool. Snowpark's :class:`Session` (and the raw
    Snowflake Python connector) is safe for concurrent SQL because
    each ``session.sql()`` call opens its own cursor under the hood,
    and the per-table writes have no FK constraints between them.
    The fan-out drops wall time from the sum of every phase
    (~10–15s pre-v0.9.6) to roughly the slowest single phase (~3–5s,
    typically ``upsert_grid_cells`` or ``save_session``).

    Disable parallelism by exporting ``SOC_PARALLEL_SAVE=0`` if you
    ever hit thread-safety issues with an exotic store impl.
    """
    sub_timings: Dict[str, float] = {}

    # Capture every payload up-front (cheap, in-process) so the
    # worker threads never touch the mutable ``sess`` — keeps the
    # parallel path independent of whatever the engine's other
    # phases are doing.
    session_row = _save_session_row(sess)
    hoard_by_owner = {
        owner: _parcel_rows(sess, owner, "hoard") for owner in sess.players
    }
    shipped_by_owner = {
        owner: _parcel_rows(sess, owner, "shipped") for owner in sess.players
    }

    def _wrap(label: str, fn):
        """Wrap a store call so its wall time lands in ``sub_timings``.

        ``sub_timings`` is only mutated from the worker thread once
        per phase (single dict write), so we don't need a Lock — the
        CPython GIL serialises the dict store. The READ that prints
        the summary happens after every worker has joined.
        """

        def _run() -> None:
            with _time_block(label, sub_timings):
                fn()

        return _run

    # v1.x (E1b) — the AUTHORITATIVE control-flow row (SOC_GAME_SESSION.json_state)
    # is written SYNCHRONOUSLY on the main thread, NEVER in the parallel pool
    # below. ``_hydrate_session`` reads this row EXCLUSIVELY, so if its MERGE
    # shares the one Snowpark ``Session`` with 6 sibling writes on a thread pool,
    # a nondeterministic interleave can let the very next ``_hydrate_session``
    # observe a PRE-resolution snapshot. That stale read makes the season runner
    # re-pick an already-submitted seat / force-resolve again, so the night is
    # simulated 2-3x and its replay frames + log lines are appended repeatedly
    # (the "day 3 repeats three times" replay bug). Keeping this write off the
    # shared-session pool guarantees read-your-writes for the runner's decisions.
    #
    # v1.41 — FOUR PHASES DELETED: square identity, asset records, entity
    # state and grid cells. Each was a WRITE-ONLY PROJECTION — nothing in
    # the app ever read them back (the store protocol has no reader for
    # any of the four, and the one SQL view over SOC_ASSET_RECORD now
    # derives itself from ``json_state`` instead). They existed so a human
    # could query a game in SQL, and they were charging ~2.7s of every
    # turn on Snowflake for the privilege, plus the in-process cost of
    # building 1120 grid-cell dicts per save.
    #
    # They are DERIVABLE, not lost: ``json_state`` already carries grid,
    # entities and the asset ledger, so a view or Dynamic Table can
    # reconstitute any of them at zero write cost. The store methods stay
    # implemented for backfills and for anyone who wants the tables back.
    # See docs/SNOWFLAKE_LATENCY_BRIEF.md.
    #
    # Hoard and shipped survive because they are genuinely READ — by
    # ``list_hoard`` / ``list_shipped`` and by ``bulk_session_scores``,
    # which is what the season picker scores off.
    phases = [
        (
            "ssf.replace_hoard_bundle",
            lambda: store.replace_hoard_bundle(
                sess.session_id, hoard_by_owner,
            ),
        ),
        (
            "ssf.replace_shipped_bundle",
            lambda: store.replace_shipped_bundle(
                sess.session_id, shipped_by_owner,
            ),
        ),
    ]

    if _PARALLEL_SAVE and len(phases) > 1:
        # Lazy import: pulling concurrent.futures only when we actually
        # need it keeps cold-start time honest for callers that disable
        # parallelism (e.g. unit tests via SOC_PARALLEL_SAVE=0).
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=len(phases)) as pool:
            # ``list(...)`` forces every Future to materialise so we
            # block until the slowest phase completes; ``result()``
            # re-raises any worker exception in the caller's stack
            # (preserving the pre-v0.9.6 failure contract).
            futures = [pool.submit(_wrap(label, fn)) for label, fn in phases]
            for fut in futures:
                fut.result()
    else:
        for label, fn in phases:
            with _time_block(label, sub_timings):
                fn()

    # E1b — authoritative row LAST, on the main thread. Once this returns the
    # session is fully persisted (derived tables already landed above) and any
    # subsequent ``_hydrate_session`` on this connection is guaranteed to read
    # the post-resolution state.
    with _time_block("ssf.save_session", sub_timings):
        store.save_session(session_row)

    # v1.43 — if the store buffers writes, this is the turn boundary: land
    # game state (authoritative row, policy queue, parcels) now, and leave the
    # append-only tables (log, replay frames, agent invocations) to the
    # per-day flush. Stores that do not buffer have no ``flush_turn`` and this
    # is a no-op, so the engine still never asks "am I on Snowflake?" — it
    # asks the store (enforced by tests/test_per_game_backend.py).
    _flush_turn = getattr(store, "flush_turn", None)
    if callable(_flush_turn):
        with _time_block("ssf.flush_turn", sub_timings):
            _flush_turn()

    if _PERF_LOG:
        total = sum(sub_timings.values())
        # When parallel, "total" is the SUM of per-phase wall times
        # but the actual wall-clock cost is roughly the MAX. Surface
        # both so the breakdown is honest about what changed.
        wall = max(sub_timings.values()) if sub_timings else 0.0
        header_total = wall if _PARALLEL_SAVE else total
        suffix = " (parallel · wall ≈ max)" if _PARALLEL_SAVE else ""
        ordered = sorted(sub_timings.items(), key=lambda kv: -kv[1])
        lines = [
            f"[soc-perf]   └─ save_session_full breakdown "
            f"({header_total:.1f}ms{suffix}):"
        ]
        for k, v in ordered:
            if v >= 0.05:
                lines.append(f"     {k:<32s} {v:>9.2f}ms")
        print("\n".join(lines), file=sys.stderr, flush=True)


def _hydrate_session(store: SocStore, session_id: str) -> GameSession:
    """Load and rehydrate a session from the durable ``json_state`` blob."""
    row = store.load_session(session_id)
    if not row:
        raise KeyError(f"session not found: {session_id}")
    blob = row.get("json_state")
    if not blob:
        raise ValueError(
            f"session {session_id} has no json_state — was init_session run?"
        )
    # Unwrap repeatedly: snapshots written before the double-encode fix stored
    # the blob as a JSON string INSIDE the JSON column, so one decode yields
    # another string. Looping costs nothing on a well-formed row and keeps those
    # frozen boards readable rather than stranding them.
    while isinstance(blob, str):
        blob = json.loads(blob)
    return GameSession.from_dict(blob)


# ── Procedure handlers ───────────────────────────────────────────────


def init_session(
    store: SocStore,
    seed: int,
    width: int = 40,
    height: int = 28,
    season_name: Optional[str] = None,
    season_day_cap: Optional[int] = None,
    skip_initial_orbit: bool = False,
    *,
    players: Optional[Sequence[str]] = None,
    agents: Optional[Mapping[str, str]] = None,
    visibility_mode: str = "hidden",
    player_profiles: Optional[Mapping[str, Mapping[str, str]]] = None,
    weapons_enabled: bool = True,
    signs_enabled: bool = True,
    tutorial: str = "",
) -> Dict[str, Any]:
    """Generate + persist a new session (mirrors POST /api/game/new).

    **Append-only persistence (v0.5):** prior seasons are NOT wiped on
    init. Each call mints a fresh ``session_id`` and the row coexists
    with every earlier session in ``SOC_GAME_SESSION``. The Phase C
    watcher frontend lists every persisted season so the live UI and
    the CLI orchestrator can both spawn games without trampling each
    other's history.

    Wiping is still available — :meth:`SocStore.wipe_all_sessions` is
    public — and the CLI exposes a ``--wipe-first`` flag for callers
    who explicitly want a clean board. A future archive feature can
    hook in by snapshotting SOC_* into archive tables before calling
    ``wipe_all_sessions()`` directly.

    ``season_name`` is optional — when omitted the session generates a
    deterministic ``LatinWord_EnglishNoun`` label from ``seed``. CLI
    runners that want to stamp a specific name (e.g. for tournament
    bookkeeping) can pass it through.

    v0.9.6 — extended to take an explicit seat list (``players``), a
    per-seat agent map (``agents``: ``"human"`` or ``"red_harvest"``),
    and a ``visibility_mode`` (``"hidden"`` or ``"open"``). Defaults
    keep the legacy 2-seat / human / hidden shape.

    v1.32 — ``weapons_enabled`` / ``signs_enabled`` are the teaching
    modes' rule switches and ``tutorial`` records which preset asked for
    them. All three default to a full game, so every existing caller is
    unaffected.
    """
    sess = GameSession.new(
        int(width),
        int(height),
        int(seed),
        season_name=season_name,
        season_day_cap=season_day_cap,
        skip_initial_orbit=bool(skip_initial_orbit),
        players=players,
        agents=agents,
        visibility_mode=visibility_mode,
        player_profiles=player_profiles,
        weapons_enabled=bool(weapons_enabled),
        signs_enabled=bool(signs_enabled),
        tutorial=str(tutorial or ""),
    )
    save_session_full(store, sess)
    return {
        "session_id": sess.session_id,
        "season_name": sess.season_name,
        "season_day_cap": int(sess.season_day_cap),
        "seed": sess.seed,
        "width": sess.width,
        "height": sess.height,
        "day": sess.day,
        "phase": sess.phase.value,
        "pending": _seat_pack(sess),
        "max_moves": MAX_MOVES,
        "max_queue_len": MAX_QUEUE_LEN,
        # v0.9.6 — surface the new fields so the launcher modal can
        # echo back what the new game actually allocated.
        "players": list(sess.players),
        "agents": dict(sess.agents),
        "visibility_mode": str(sess.visibility_mode),
        "weapons_enabled": bool(sess.weapons_enabled),
        "signs_enabled": bool(sess.signs_enabled),
        "tutorial": str(sess.tutorial or ""),
    }


def _run_bot_in_memory(
    sess: GameSession,
    player: str,
    runtime: str,
) -> Dict[str, Any]:
    """Run one bot's turn against an IN-MEMORY :class:`GameSession`.

    v0.9.8 — pulled out of :func:`run_agent_turn` to power the batched
    :func:`auto_fire_bot_seats`. The function mutates ``sess`` in place
    (stashes the policy / orbit actions, potentially triggering night /
    orbit resolution via the session's own ``maybe_resolve_*`` hooks)
    and returns the envelope + a rationale row the caller will persist
    later in a single Snowflake write batch.

    No store / network calls happen here — that's the whole point. The
    pre-v0.9.8 path went through :func:`run_agent_turn` for every bot,
    which dragged in two ``get_view`` hydrates + one ``submit_policy``
    save + one ``save_agent_rationale`` save per bot (so ~5-8s wall per
    bot, 3 bots × 2 phases ≈ 30-50s on a healthy warehouse). Batching
    cuts that to a single hydrate + single save at the top of the
    parent submit.
    """
    from sea_of_colours.agent.heuristic_agent import (
        HeuristicAgent,
        plan_orbit_actions,
    )
    from sea_of_colours.agent.runtime import HEURISTIC_AGENT_NAME

    started = time.time()
    pid = cast_player(player, allowed=sess.players)
    agent_view = build_agent_view(sess, pid, [])  # in-memory; no log lookup

    runtime_norm = (runtime or "heuristic").strip().lower()
    # The orbit playbook is heuristic-only by design (Cortex agents
    # don't reason about ORBIT submissions). Force the runtime label
    # to "heuristic" when we're firing an orbit turn so the audit
    # trail is honest about what carried the seat.
    if sess.phase == Phase.ORBIT:
        orbit_actions, rationale = plan_orbit_actions(agent_view)
        sess.stash_orbit_actions(pid, orbit_actions)
        orbit_resolved = False
        if sess.both_orbit_ready():
            orbit_resolved = bool(sess.maybe_resolve_orbit_if_ready())
        tool_calls_log = [
            {"name": "soc_get_view", "args": {"echo": "in-process"}},
            {
                "name": "soc_submit_orbit_actions",
                "args": {"action_count": len(orbit_actions)},
            },
        ]
        ms_elapsed = int((time.time() - started) * 1000)
        envelope = {
            "ok": True,
            "agent_id": HEURISTIC_AGENT_NAME,
            "player": player,
            "runtime": "heuristic",
            "rationale": rationale,
            "moves": [],
            "orbit_actions": orbit_actions,
            "tool_calls": tool_calls_log,
            "submitted": True,
            "night_resolved": False,
            "orbit_resolved": orbit_resolved,
            "ms_elapsed": ms_elapsed,
            "_rationale_row": {
                "day": int(sess.day),
                "agent_id": HEURISTIC_AGENT_NAME,
                "player": player,
                "rationale": rationale,
                "runtime": "heuristic",
                "tool_calls": tool_calls_log,
                "ms_elapsed": ms_elapsed,
            },
        }
        return envelope

    # PLANNING — Cortex is not safe to run from this batched in-memory
    # path (it submits its own policy through a Snowflake proc that
    # would race the batched save). The batched loop is therefore
    # heuristic-only; ``runtime_override="cortex"`` callers should
    # still use :func:`run_agent_turn` directly. Live-play hits this
    # path 100% of the time because the human-vs-bots launcher only
    # offers ``human`` / ``red_harvest``.
    if runtime_norm == "cortex":
        # Defensive: fall through to heuristic and tag the rationale.
        # The Cortex path is an explicit choice on the agent/think
        # endpoint, not something we want to take on a bot fan-out.
        runtime_norm = "heuristic"

    agent = HeuristicAgent()
    plan = agent.play(agent_view)
    moves = plan["moves"]
    rationale = plan["rationale"]
    tool_calls = list(plan["tool_calls"])

    sess.stash_policy(pid, moves)
    night_resolved = False
    ran_day: Optional[int] = None
    replay_frames: List[Dict[str, Any]] = []
    if sess.both_ready():
        ran_day = int(sess.day)
        sess.maybe_resolve_if_ready()
        night_resolved = True
        replay_frames = list(getattr(sess, "last_night_replay", []) or [])

    ms_elapsed = int((time.time() - started) * 1000)
    envelope = {
        "ok": True,
        "agent_id": HEURISTIC_AGENT_NAME,
        "player": player,
        "runtime": "heuristic",
        "rationale": rationale,
        "moves": moves,
        "tool_calls": tool_calls,
        "submitted": True,
        "night_resolved": night_resolved,
        "ms_elapsed": ms_elapsed,
        # Sidecars consumed by ``auto_fire_bot_seats`` to persist
        # everything in one final write batch.
        "_rationale_row": {
            "day": int(ran_day or sess.day),
            "agent_id": HEURISTIC_AGENT_NAME,
            "player": player,
            "rationale": rationale,
            "runtime": "heuristic",
            "tool_calls": tool_calls,
            "ms_elapsed": ms_elapsed,
        },
        "_replay_frames": replay_frames if night_resolved else [],
        "_replay_day": ran_day,
    }
    return envelope


def _fire_bots_in_memory(
    sess: GameSession,
    *,
    runtime_override: Optional[str] = None,
) -> tuple[
    List[Dict[str, Any]],  # envelopes
    List[Dict[str, Any]],  # rationale_rows
    List[tuple[int, List[Dict[str, Any]]]],  # replay_batches
]:
    """Fan out every still-pending bot seat against an in-memory session.

    v0.9.8 — extracted from :func:`auto_fire_bot_seats` so
    :func:`submit_policy` / :func:`submit_orbit_actions` can run the
    fan-out inline (against the session they just stashed for the
    human) and persist everything in a single save batch. The
    function is pure mutation against ``sess`` — no store / network
    calls — and returns the data the caller needs to durably persist
    rationales + replay frames after the final ``save_session_full``.
    """
    envelopes: List[Dict[str, Any]] = []
    rationale_rows: List[Dict[str, Any]] = []
    replay_batches: List[tuple[int, List[Dict[str, Any]]]] = []
    runtime_label = (runtime_override or "heuristic").strip().lower()

    for _ in range(64):
        if sess.phase == Phase.SEASON_COMPLETE:
            break
        pending = _seat_pack(sess)
        due_seat: Optional[str] = None
        for seat in sess.players:
            agent_id = str(sess.agents.get(seat, "human")).lower()
            if agent_id == "human":
                continue
            if pending.get(seat):
                continue
            due_seat = seat
            break
        if due_seat is None:
            break
        try:
            env = _run_bot_in_memory(sess, due_seat, runtime_label)
        except Exception as exc:  # pragma: no cover — defensive log
            print(
                f"[soc] _fire_bots_in_memory: seat={due_seat} crashed: {exc}",
                file=sys.stderr, flush=True,
            )
            break
        rationale_rows.append(env.pop("_rationale_row", {}))
        frames = env.pop("_replay_frames", []) or []
        replay_day = env.pop("_replay_day", None)
        if frames and replay_day is not None:
            replay_batches.append((int(replay_day), frames))
        envelopes.append(env)

    return envelopes, rationale_rows, replay_batches


def _persist_bot_fanout(
    store: SocStore,
    sess: GameSession,
    *,
    rationale_rows: List[Dict[str, Any]],
    replay_batches: List[tuple[int, List[Dict[str, Any]]]],
    extra_log_lines: Optional[List[Dict[str, Any]]] = None,
    extra_replay_batches: Optional[
        List[tuple[int, List[Dict[str, Any]]]]
    ] = None,
    save_session: bool = True,
    log_baseline: Optional[int] = None,
    timings: Optional[Dict[str, float]] = None,
) -> None:
    """Persist the accumulated state of a bot fan-out in parallel.

    Single fan-out worker per durable surface:

    * ``save_session_full`` (the slow one; itself parallel)
    * ``append_agent_invocation`` × N (parallel)
    * ``append_log`` (any new in-memory log lines)
    * ``append_replay_frames`` × N

    Each phase is independent (different SOC_* tables, no FK) so we
    fan them out across a small thread pool. Wall time ≈ slowest
    single phase (typically ``save_session_full``).
    """
    from concurrent.futures import ThreadPoolExecutor

    timings = timings if timings is not None else {}
    session_id = sess.session_id

    # Inject rationale chatter into the in-memory session log so the
    # next get_view reflects what just happened. SOC_GAME_LOG copy
    # lands via append_log in the fan-out below.
    #
    # v0.9.8 — each rationale row carries the day the bot actually
    # ran (planning bots get the pre-night day, orbit bots get the
    # orbit-phase day). We append directly with that day instead of
    # calling ``sess.log_info`` because ``log_info`` stamps the
    # CURRENT ``sess.day`` — which at this point in the fan-out is
    # already the post-roll day, so every rationale (regardless of
    # whether it was a day-N plan or a day-N+1 orbit move) would be
    # bucketed under day N+1. Tagging with ``row.get("day")``
    # preserves the original attribution end-to-end.
    for row in rationale_rows:
        if not row:
            continue
        runtime_tag = f"[{row.get('runtime')}] " if row.get("runtime") else ""
        text = (
            f"[{row.get('agent_id')}] {runtime_tag}({row.get('player')}) "
            f"{row.get('rationale') or ''}"
        )
        rationale_day = row.get("day")
        try:
            rationale_day = int(rationale_day) if rationale_day else int(sess.day)
        except (TypeError, ValueError):
            rationale_day = int(sess.day)
        sess.log.append({
            "level": "info",
            "text": text,
            "day": rationale_day,
        })

    # Compute the durable log slice. Caller can pin the baseline; if
    # not, capture from the top so we publish every new line.
    if log_baseline is None:
        log_baseline = 0
    new_log_lines: List[Dict[str, Any]] = list(sess.log[log_baseline:])
    if extra_log_lines:
        new_log_lines.extend(extra_log_lines)

    all_replay_batches = list(replay_batches)
    if extra_replay_batches:
        all_replay_batches.extend(extra_replay_batches)

    def _persist_session() -> None:
        if not save_session:
            return
        with _time_block("save_session_full", timings):
            save_session_full(store, sess)

    def _persist_agent_rows() -> None:
        with _time_block("append_agent_invocations", timings):
            valid_rows = [r for r in rationale_rows if r]
            if not valid_rows:
                return
            payloads = [
                {
                    "session_id": session_id,
                    "day": int(r.get("day") or sess.day),
                    "agent_id": r.get("agent_id"),
                    "player": r.get("player"),
                    "rationale": r.get("rationale") or "",
                    "prompt_excerpt": None,
                    "tool_calls": list(r.get("tool_calls") or []),
                    "response_text": None,
                    "ms_elapsed": r.get("ms_elapsed"),
                    "status": "ok",
                }
                for r in valid_rows
            ]
            # v1.5 (P3): one batched insert for the whole bot fan-out instead
            # of a SELECT+INSERT per bot (a 4-seat game went from ~6 round-trips
            # to 2). Fall back to per-row for any store that predates the
            # batched method.
            batch = getattr(store, "append_agent_invocations", None)
            try:
                if callable(batch):
                    batch(payloads)
                else:
                    for p in payloads:
                        store.append_agent_invocation(p)
            except Exception as exc:  # pragma: no cover
                print(
                    f"[soc] _persist_bot_fanout: invocation persist failed: "
                    f"{exc}",
                    file=sys.stderr, flush=True,
                )

    def _persist_log() -> None:
        if not new_log_lines:
            return
        # v0.9.8 — group log lines by their entry-level ``day`` field so
        # ``SOC_GAME_LOG`` carries per-line day attribution. With the
        # batched bot fan-out a single ``append_log`` call now spans
        # multiple days (day-N planning resolves into day-N night which
        # rolls into day-N+1 orbit + planning), and the pre-v0.9.8
        # "one append_log per submit, stamp every row with sess.day"
        # path would attribute day-N events to day-N+1 and break the
        # replay viewer's per-day LOG filter. Entries without a day
        # field fall back to the session's current day so legacy
        # session blobs keep working.
        with _time_block("append_log", timings):
            buckets: Dict[int, List[Dict[str, Any]]] = {}
            fallback_day = int(sess.day)
            for entry in new_log_lines:
                d = entry.get("day") if isinstance(entry, Mapping) else None
                day_key = int(d) if isinstance(d, (int, float)) and d else fallback_day
                buckets.setdefault(day_key, []).append(entry)
            for d, lines in sorted(buckets.items()):
                try:
                    store.append_log(session_id, d, lines)
                except Exception as exc:  # pragma: no cover
                    print(
                        f"[soc] _persist_bot_fanout: append_log failed "
                        f"(day={d}): {exc}",
                        file=sys.stderr, flush=True,
                    )

    def _persist_replay_frames() -> None:
        if not all_replay_batches:
            return
        with _time_block("append_replay_frames", timings):
            for day, frames in all_replay_batches:
                try:
                    store.append_replay_frames(session_id, day, frames)
                except Exception as exc:  # pragma: no cover
                    print(
                        f"[soc] _persist_bot_fanout: append_replay_frames "
                        f"failed (day={day}): {exc}",
                        file=sys.stderr, flush=True,
                    )

    phases = [
        _persist_session,
        _persist_agent_rows,
        _persist_log,
        _persist_replay_frames,
    ]
    with _time_block("fanout_total", timings):
        with ThreadPoolExecutor(max_workers=len(phases)) as pool:
            futs = [pool.submit(fn) for fn in phases]
            for f in futs:
                f.result()


def auto_fire_bot_seats(
    store: SocStore,
    session_id: str,
    *,
    runtime_override: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Drive every bot-controlled seat that hasn't stashed yet.

    v0.9.6 — N-seat live-play helper. After a human seat submits, we
    walk :attr:`GameSession.agents` and run every seat assigned to a
    bot (``"red_harvest"`` or any agent id that isn't ``"human"``)
    that's still waiting on a submission for the current phase.
    Without this the human locks their queue and the night sits
    forever waiting for the bot seats to "submit" the same way a
    human would.

    v0.9.8 — batched in-memory fan-out. The pre-v0.9.8 implementation
    called :func:`run_agent_turn` once per bot, which dragged in two
    Snowflake hydrates + one ``save_session_full`` from
    ``submit_policy`` + another ``save_session_full`` from
    ``save_agent_rationale`` per bot. For a 4-seat (1H + 3B) game
    that's six full save fan-outs per phase × two phases per
    TRANSMIT click ≈ 50–90s wall on a healthy warehouse — which is
    the slowness the user noticed. The new path hydrates once, runs
    every pending bot directly against the in-memory session via
    :func:`_run_bot_in_memory`, lets night / orbit resolve naturally
    against the same in-memory object, and only persists the
    accumulated state ONCE at the end of the loop. Agent rationales
    + log lines + replay frames are flushed in the same final batch.

    Returns the list of envelopes (one per bot fired). The envelopes
    drop the ``_rationale_row`` / ``_replay_frames`` / ``_replay_day``
    sidecars before returning so the public shape is unchanged.
    Failures are logged but never re-raised so a single bot crash
    doesn't take the whole submit path down with it.
    """
    timings: Dict[str, float] = {}
    envelopes: List[Dict[str, Any]] = []
    rationale_rows: List[Dict[str, Any]] = []
    replay_batches: List[tuple[int, List[Dict[str, Any]]]] = []

    with _time_block("hydrate_session", timings):
        try:
            sess = _hydrate_session(store, session_id)
        except KeyError:
            return envelopes

    log_baseline = len(sess.log)

    with _time_block("run_bots", timings):
        envelopes, rationale_rows, replay_batches = _fire_bots_in_memory(
            sess, runtime_override=runtime_override,
        )

    if not envelopes:
        _emit_perf_summary(
            "auto_fire_bot_seats", session_id, "(none)", timings,
            extra={"bots_fired": "0"},
        )
        return envelopes

    _persist_bot_fanout(
        store, sess,
        rationale_rows=rationale_rows,
        replay_batches=replay_batches,
        log_baseline=log_baseline,
        timings=timings,
    )

    _emit_perf_summary(
        "auto_fire_bot_seats", session_id, "(batched)", timings,
        extra={
            "bots_fired": str(len(envelopes)),
            "rationale_rows": str(len([r for r in rationale_rows if r])),
            "replay_batches": str(len(replay_batches)),
        },
    )
    return envelopes


# v0.9.5 — gate the per-phase timing breakdown behind ``SOC_PERF=1``
# so production logs don't drown in instrumentation noise. When the
# env var is set, ``submit_policy`` / ``submit_orbit_actions`` emit a
# single multi-line log block to stderr summarising the cost of each
# Snowflake-bound step. Useful to confirm the v0.9.5 batched writers
# actually moved the needle vs. the pre-batch baseline.
_PERF_LOG = os.environ.get("SOC_PERF", "").strip() not in ("", "0", "false", "False")

# v0.9.6 — fan out the independent table writes inside
# :func:`save_session_full` across a thread pool by default. The
# per-table writes go to different SOC_* tables and Snowpark/Snowflake
# Python driver are safe for concurrent SQL on a shared session/conn
# (each call opens its own cursor). Set ``SOC_PARALLEL_SAVE=0`` to
# revert to the sequential v0.9.5 path (useful for unit tests or to
# isolate a thread-safety bug in a custom store).
_PARALLEL_SAVE = os.environ.get(
    "SOC_PARALLEL_SAVE", "1"
).strip() not in ("", "0", "false", "False")


@contextmanager
def _time_block(label: str, store: Dict[str, float]) -> Iterator[None]:
    """Record the wall time of the enclosed block into ``store[label]``.

    Cheap: ``time.perf_counter()`` is a monotonic ns-resolution clock
    so even sub-ms phases register accurately. The label is the dict
    key downstream callers print in the perf summary.
    """
    t0 = time.perf_counter()
    try:
        yield
    finally:
        store[label] = (time.perf_counter() - t0) * 1000.0


def _emit_perf_summary(
    op: str, session_id: str, player: str, timings: Dict[str, float],
    *, extra: Optional[Mapping[str, Any]] = None,
) -> None:
    """Pretty-print a one-block timing summary for one submit call.

    Only fires when ``SOC_PERF=1`` is set in the environment. The block
    sticks the most expensive phases first so they're easy to scan.
    """
    if not _PERF_LOG:
        return
    total = sum(timings.values())
    # Sort by descending duration so the offender shows up first.
    ordered = sorted(timings.items(), key=lambda kv: -kv[1])
    lines = [
        f"[soc-perf] {op} session={session_id[:8]} player={player} "
        f"TOTAL={total:.1f}ms",
    ]
    for k, v in ordered:
        if v >= 0.05:  # suppress sub-50µs phases
            lines.append(f"  {k:<28s} {v:>9.2f}ms")
    if extra:
        for k, v in extra.items():
            lines.append(f"  {k:<28s} {v}")
    print("\n".join(lines), file=sys.stderr, flush=True)


def submit_policy(
    store: SocStore,
    session_id: str,
    player: str,
    moves: Any,
    *,
    auto_fire_bots: bool = False,
) -> Dict[str, Any]:
    """Stash a policy + resolve the night if both seats are ready.

    v0.9.5 — when ``SOC_PERF=1`` is exported, each phase's wall time is
    captured and a single summary block is printed to stderr. The
    breakdown reveals which Snowflake-bound step dominates the user-
    perceived latency. Run with ``SOC_PERF=`` (or unset) to silence.

    v0.9.8 — opt-in inline bot fan-out via ``auto_fire_bots=True``.
    When enabled, every bot seat that's still pending in this phase
    gets fanned out through the heuristic playbook BEFORE the
    final save_session_full, so a single 1-human-vs-N-bots TRANSMIT
    click costs one hydrate + one save cycle instead of one-per-bot.
    Default is False so existing callers (tests, headless season
    runner, eval harness) see the same semantics as v0.9.7. The
    live-play HTTP route (``POST /api/game/{id}/policy``) sets this
    True so a 4-seat game stays snappy.
    """
    timings: Dict[str, float] = {}
    with _time_block("hydrate_session", timings):
        sess = _hydrate_session(store, session_id)
    if player not in sess.players:
        return {
            "ok": False,
            "errors": [
                f"unknown player '{player}' — session seats are "
                f"{list(sess.players)}"
            ],
            "session_id": session_id,
        }
    pid = cast_player(player, allowed=sess.players)

    log_baseline = len(sess.log)
    with _time_block("stash_policy", timings):
        ok, errs = sess.stash_policy(pid, moves)

    # Audit row in SOC_POLICY_QUEUE regardless of whether parsing
    # ultimately accepted the queue — the raw submission is forensic
    # data ("the agent thought this was a good idea").
    if ok and sess.pending_policies[pid] is not None:
        with _time_block("upsert_policy", timings):
            store.upsert_policy(
                session_id,
                sess.day,
                player,
                moves_to_wire(sess.pending_policies[pid] or []),
            )

    resolved = False
    ran_day: Optional[int] = None
    if ok:
        if sess.both_ready():
            ran_day = sess.day  # captured *before* run_night ticks the calendar
            with _time_block("resolve_night", timings):
                sess.maybe_resolve_if_ready()
            resolved = True

    # ── v0.9.8 inline bot fan-out ─────────────────────────────
    # Collect anything the night resolution produced (replay
    # frames) BEFORE we let the bots advance the phase further.
    # The orbit phase that opens immediately after night resolution
    # is the most common place for follow-on bot work in a 4-seat
    # game, so we want one save batch to cover all of it.
    fanout_envelopes: List[Dict[str, Any]] = []
    fanout_rationales: List[Dict[str, Any]] = []
    fanout_replay_batches: List[tuple[int, List[Dict[str, Any]]]] = []
    if ok and resolved and ran_day is not None:
        frames = list(getattr(sess, "last_night_replay", []) or [])
        if frames:
            fanout_replay_batches.append((int(ran_day), frames))
    if ok and auto_fire_bots:
        with _time_block("fire_bots_in_memory", timings):
            (
                fanout_envelopes,
                fanout_rationales,
                more_replay_batches,
            ) = _fire_bots_in_memory(sess)
        for day, frames in more_replay_batches:
            fanout_replay_batches.append((day, frames))

    # Decide whether we needed any custom persistence at all. When
    # auto_fire_bots produced rows we route through _persist_bot_fanout
    # (parallel writes for all four surfaces); otherwise keep the
    # original sequential ordering so the legacy "save+log+replay"
    # contract is preserved bit-for-bit.
    if fanout_envelopes or fanout_rationales:
        _persist_bot_fanout(
            store, sess,
            rationale_rows=fanout_rationales,
            replay_batches=fanout_replay_batches,
            log_baseline=log_baseline,
            timings=timings,
        )
    else:
        with _time_block("save_session_full", timings):
            save_session_full(store, sess)

        new_log = sess.log[log_baseline:]
        if new_log:
            # v0.9.8 — group by per-entry day so cross-night submits
            # (an empty p1 policy that lets the night resolve) attribute
            # the planning lines to day N and the night narration to
            # day N too, instead of stamping every line with sess.day
            # (which would tag day-N events as day-N+1 once the calendar
            # ticks forward at the end of run_night).
            with _time_block("append_log", timings):
                buckets: Dict[int, List[Dict[str, Any]]] = {}
                fallback_day = int(ran_day or sess.day)
                for entry in new_log:
                    d = entry.get("day") if isinstance(entry, Mapping) else None
                    day_key = int(d) if isinstance(d, (int, float)) and d else fallback_day
                    buckets.setdefault(day_key, []).append(entry)
                for d, lines in sorted(buckets.items()):
                    store.append_log(session_id, d, lines)

        if fanout_replay_batches:
            with _time_block("append_replay_frames", timings):
                for day, frames in fanout_replay_batches:
                    store.append_replay_frames(session_id, day, frames)

    _emit_perf_summary(
        "POST /policy", session_id, player, timings,
        extra={
            "resolved": str(resolved),
            "bots_fired": str(len(fanout_envelopes)),
            "replay_batches": str(len(fanout_replay_batches)),
        },
    )

    return {
        "ok": bool(ok),
        "errors": errs if not ok else sess.errors.get(pid, []),
        "session_id": session_id,
        "phase": sess.phase.value,
        "day": sess.day,
        "pending": _seat_pack(sess),
        "night_resolved": resolved,
        "moves_stashed": (
            len(sess.pending_policies[pid] or [])
            if sess.pending_policies[pid]
            else 0
        ),
        # v0.9.8 — surface the inline fan-out summary so callers
        # (server/app.py) can skip the redundant external
        # ``auto_fire_bot_seats`` call.
        "bots_fired": len(fanout_envelopes),
    }


def submit_orbit_actions(
    store: SocStore,
    session_id: str,
    player: str,
    actions: Any,
    *,
    auto_fire_bots: bool = False,
) -> Dict[str, Any]:
    """Stash an Orbit action queue + resolve once both seats are ready.

    Counterpart to :func:`submit_policy` for the daytime Orbit phase
    (v0.8.0). Per-item parse failures become :class:`OrbitWasteAction`
    markers and surface as yellow log lines at resolution time.

    v0.9.8 — opt-in inline bot fan-out via ``auto_fire_bots=True``.
    The live-play HTTP route enables it so a 1-human-vs-N-bots
    orbit submit costs one hydrate + one save cycle instead of
    one-per-bot. Defaults to False so test / eval / season-runner
    callers see the legacy semantics.
    """
    timings: Dict[str, float] = {}
    with _time_block("hydrate_session", timings):
        sess = _hydrate_session(store, session_id)
    if player not in sess.players:
        return {
            "ok": False,
            "errors": [
                f"unknown player '{player}' — session seats are "
                f"{list(sess.players)}"
            ],
            "session_id": session_id,
        }
    pid = cast_player(player, allowed=sess.players)

    log_baseline = len(sess.log)
    with _time_block("stash_orbit_actions", timings):
        ok, errs = sess.stash_orbit_actions(pid, actions)

    resolved = False
    ran_day: Optional[int] = None
    if ok:
        if sess.both_orbit_ready():
            ran_day = sess.day
            with _time_block("resolve_orbit", timings):
                sess.maybe_resolve_orbit_if_ready()
            resolved = True

    # ── v0.9.8 inline bot fan-out ──
    fanout_envelopes: List[Dict[str, Any]] = []
    fanout_rationales: List[Dict[str, Any]] = []
    fanout_replay_batches: List[tuple[int, List[Dict[str, Any]]]] = []
    if ok and auto_fire_bots:
        with _time_block("fire_bots_in_memory", timings):
            (
                fanout_envelopes,
                fanout_rationales,
                more_replay_batches,
            ) = _fire_bots_in_memory(sess)
        for day, frames in more_replay_batches:
            fanout_replay_batches.append((day, frames))

    if fanout_envelopes or fanout_rationales:
        _persist_bot_fanout(
            store, sess,
            rationale_rows=fanout_rationales,
            replay_batches=fanout_replay_batches,
            log_baseline=log_baseline,
            timings=timings,
        )
    else:
        with _time_block("save_session_full", timings):
            save_session_full(store, sess)

        new_log = sess.log[log_baseline:]
        if new_log:
            # v0.9.8 — see ``submit_policy`` above for rationale; group
            # by per-entry day so orbit-settles-into-next-day flows
            # tag each line with its actual day.
            with _time_block("append_log", timings):
                buckets: Dict[int, List[Dict[str, Any]]] = {}
                fallback_day = int(ran_day or sess.day)
                for entry in new_log:
                    d = entry.get("day") if isinstance(entry, Mapping) else None
                    day_key = int(d) if isinstance(d, (int, float)) and d else fallback_day
                    buckets.setdefault(day_key, []).append(entry)
                for d, lines in sorted(buckets.items()):
                    store.append_log(session_id, d, lines)

        if fanout_replay_batches:
            with _time_block("append_replay_frames", timings):
                for day, frames in fanout_replay_batches:
                    store.append_replay_frames(session_id, day, frames)

    _emit_perf_summary(
        "POST /orbit", session_id, player, timings,
        extra={
            "resolved": str(resolved),
            "bots_fired": str(len(fanout_envelopes)),
        },
    )

    pending_orbit_pack = {
        p: sess.pending_orbit_actions.get(p) is not None for p in sess.players
    }

    return {
        "ok": bool(ok),
        "errors": errs if not ok else sess.errors.get(pid, []),
        "session_id": session_id,
        "phase": sess.phase.value,
        "day": sess.day,
        "pending_orbit": pending_orbit_pack,
        "orbit_resolved": resolved,
        # v1.13 — no cap. Kept in the payload (as null) rather than
        # dropped, so a stale client reads "no limit" instead of KeyError.
        "max_orbit_actions": None,
        "credits": dict(sess.credits),
        "probe_stock": dict(sess.probe_stock),
        # v0.9.3 — surface the built-weapons stockpile so the
        # frontend's orbit-readout pill ("EMP 0 · MINE 0 · CHAFF 0")
        # updates in lock-step with the latest orbit resolution. Deep-
        # copy is cheap (always ≤ 6 ints) and shields the engine's
        # mutable state from accidental client edits.
        "weapon_stock": {
            p: dict(sess.weapon_stock.get(p, {}) or {}) for p in sess.players
        },
        # v0.9.8 — surface the inline fan-out summary so callers
        # (server/app.py) can skip the redundant external
        # ``auto_fire_bot_seats`` call.
        "bots_fired": len(fanout_envelopes),
    }


def run_night(store: SocStore, session_id: str) -> Dict[str, Any]:
    """Force-resolve the night (no-op if both seats aren't ready)."""
    sess = _hydrate_session(store, session_id)
    if not sess.both_ready():
        return {
            "ok": False,
            "session_id": session_id,
            "day": sess.day,
            "phase": sess.phase.value,
            "pending": _seat_pack(sess),
            "reason": "both seats must submit policies first",
        }
    ran_day = sess.day
    log_baseline = len(sess.log)
    sess.maybe_resolve_if_ready()
    save_session_full(store, sess)

    new_log = sess.log[log_baseline:]
    if new_log:
        store.append_log(session_id, ran_day, new_log)
    if sess.last_night_replay:
        store.append_replay_frames(
            session_id, ran_day, list(sess.last_night_replay),
        )
    return {
        "ok": True,
        "session_id": session_id,
        "ran_day": ran_day,
        "day": sess.day,
        "phase": sess.phase.value,
        "pending": _seat_pack(sess),
    }


def get_view(
    store: SocStore, session_id: str, player: str
) -> Dict[str, Any]:
    """Player percept + structured agent payload."""
    sess = _hydrate_session(store, session_id)
    if player not in sess.players:
        raise ValueError(
            f"unknown player '{player}' — session seats are "
            f"{list(sess.players)}"
        )
    pid = cast_player(player, allowed=sess.players)
    dense = sess.player_dense_view(pid)
    pending_moves = sess.pending_policies.get(pid) or []
    recent_log = store.list_log(session_id, limit=20)
    agent_view = build_agent_view(sess, pid, recent_log)
    # Season + score fields are also exposed at the top level so the
    # playing UI can render "DAY n/CAP · Alba_Cipher" and a live
    # leaderboard without descending into the structured ``agent_view``
    # payload (which is optimised for LLM consumption and may change
    # shape independently). v0.7.4 — the cap is read off the session
    # (per-season setting) rather than the module default.
    from sea_of_colours.game.session import SEASON_DAY_CAP
    season_day_cap = int(getattr(sess, "season_day_cap", None) or SEASON_DAY_CAP)
    return {
        "mode": "player",
        "viewer": pid,
        "session_id": sess.session_id,
        "season_name": sess.season_name,
        "season_day_cap": season_day_cap,
        "is_season_complete": sess.is_season_complete(),
        "final_orbit": bool(getattr(sess, "final_orbit", False)),
        "player_names": dict(sess.player_names),
        # v0.9.18 — player identity profiles (custom names, tags, colors)
        "player_profiles": {
            p: dict(sess.player_profiles.get(p, {}))
            for p in sess.players
        },
        # v0.9.6 — surface the per-session seat list / agent map /
        # visibility flag so the frontend can render N-seat tabs,
        # gate the OBS tab on ``open`` visibility, and label bot
        # seats next to their colour chips.
        "players": list(sess.players),
        "agents": dict(sess.agents),
        "visibility_mode": str(sess.visibility_mode),
        "score": sess.score_for(pid),
        "scores": {p: sess.score_for(p) for p in sess.players},
        "width": sess.width,
        "height": sess.height,
        "day": sess.day,
        "phase": sess.phase.value,
        "cells": dense,
        # v0.9.x — static blue-sign overlay (RULEBOOK §4.6): orbit-wide,
        # fog-independent, identical for every seat. Surfaced top-level
        # so the frontend can paint it without descending into
        # ``agent_view``.
        "blue_sign": [dict(r) for r in (sess.blue_sign or [])],
        # v1.x — discovery-triggered REDSIGN beacons (RULEBOOK §4.11):
        # public, fog-independent, persistent. Surfaced top-level so the
        # frontend can paint the pulse overlay directly.
        "redsign": [dict(r) for r in (sess.redsign or [])],
        # v0.9.x — uncapped PUBLIC shipped ledger (all seats, full
        # detail). Surfaced top-level so the live VAULT can render the
        # all-seat SHIPPED record without descending into ``agent_view``.
        "shipped_record": agent_view.get("shipped_record", []),
        "units": sess.unit_summary_for_owner(pid),
        "inventory": sess.inventory_pack(pid),
        "policy_hints": sess.policy_hints(pid),
        "pending": _seat_pack(sess),
        "pending_moves": moves_to_wire(pending_moves),
        "max_moves": MAX_MOVES,
        "agent_view": agent_view,
    }


def get_observer(store: SocStore, session_id: str) -> Dict[str, Any]:
    """Cheat omniscient mosaic (mirrors GET /api/game/{id}/observer).

    v0.9.6 — N-seat output. The pre-N legacy keys ``units_p1`` /
    ``units_p2`` / ``inventory_p1`` / ``inventory_p2`` are still
    emitted so older frontend builds keep working, but new code
    should use ``units_by_seat`` / ``inventory_by_seat`` which
    iterates :attr:`GameSession.players`. The OBS view itself is
    only reachable when ``visibility_mode == "open"`` (gated at
    the API layer); we don't re-check here so legacy hidden-mode
    tests that drive observer for free still pass.
    """
    sess = _hydrate_session(store, session_id)
    units_by_seat = {p: sess.unit_summary_for_owner(p) for p in sess.players}
    inv_by_seat = {p: sess.inventory_pack(p) for p in sess.players}
    out: Dict[str, Any] = {
        "mode": "observer",
        "players": list(sess.players),
        "agents": dict(sess.agents),
        "visibility_mode": str(sess.visibility_mode),
        "width": sess.width,
        "height": sess.height,
        "day": sess.day,
        "phase": sess.phase.value,
        "cells": sess.observer_cells_rowmajor(),
        "blue_sign": [dict(r) for r in (sess.blue_sign or [])],
        "redsign": [dict(r) for r in (sess.redsign or [])],
        "units_by_seat": units_by_seat,
        "inventory_by_seat": inv_by_seat,
        "scores": {p: sess.score_for(p) for p in sess.players},
    }
    # Legacy 2-seat aliases — kept so old frontend builds and tests
    # that haven't migrated to the by-seat keys keep working.
    if "p1" in units_by_seat:
        out["units_p1"] = units_by_seat["p1"]
        out["inventory_p1"] = inv_by_seat["p1"]
    if "p2" in units_by_seat:
        out["units_p2"] = units_by_seat["p2"]
        out["inventory_p2"] = inv_by_seat["p2"]
    return out


def get_replay(
    store: SocStore,
    session_id: str,
    day_from: Optional[int] = None,
    day_to: Optional[int] = None,
) -> Dict[str, Any]:
    """Multi-day replay scrub — used by Phase-4 UI."""
    from sea_of_colours.game.session import GREEN_ENDGAME_PENALTY
    sess = _hydrate_session(store, session_id)
    frames = store.list_replay_frames(session_id, day_from, day_to)
    day_index = store.day_index(session_id)
    # Group by day for the Phase-4 UI's day-divider rendering.
    days: Dict[int, Dict[str, Any]] = {}
    for f in frames:
        day = int(f["day"])
        days.setdefault(day, {"day": day, "frames": []})
        days[day]["frames"].append(f)
    # v0.8.0 — attach the per-day catapult settlement payload as a
    # parallel ``catapult_by_day`` map (the replay overlay reads
    # this directly without needing to re-fetch). We deliberately do
    # NOT inject empty day buckets into ``days`` for catapult-only
    # days (e.g. the resolved ORBIT phase that opened a brand-new day
    # before any night frames landed) because tests / scrubber UI
    # treat ``days`` as the canonical "days that have frames" list.
    catapult_by_day: Dict[int, Dict[str, Any]] = {}
    for entry in list(sess.catapult_history or []):
        d = int(entry.get("day", 0) or 0)
        if d <= 0:
            continue
        if day_from is not None and d < int(day_from):
            continue
        if day_to is not None and d > int(day_to):
            continue
        catapult_by_day[d] = dict(entry)
        if d in days:
            days[d]["catapult"] = dict(entry)
    # v0.9 — Compute a per-day collapsed view that folds runs of
    # all-wait / EMP-smothered / chaffed frames into a single
    # ``tag="lull"`` summary frame. The frontend's "skip lulls"
    # toggle picks between ``frames`` (full timeline) and
    # ``frames_compact`` (collapsed) at scrub time so the watcher
    # can speed-read a quiet night without losing the full record.
    for day_entry in days.values():
        day_entry["frames_compact"] = _collapse_lull_runs(
            day_entry.get("frames") or []
        )
    # v0.9.1 — bucket the orbit-stamped log entries by day so the
    # LOG tab's "ORBIT" filter chip can show a per-day feed of build /
    # repair / settlement chatter without re-parsing the session log on
    # every scrub tick.
    #
    # v0.9.8 — ALSO bucket EVERY log entry by day (not just orbit)
    # so the LOG drawer can refocus on the replay cursor day without
    # losing fidelity once the live tail rolls past the cutoff. Each
    # entry now carries a ``day`` field at write time (see
    # ``session.log_info``), so the partitioning is exact rather than
    # text-sniffed.
    orbit_log_by_day: Dict[int, List[Dict[str, Any]]] = {}
    log_by_day: Dict[int, List[Dict[str, Any]]] = {}
    fallback_day = int(getattr(sess, "day", 0) or 0)
    for entry in list(getattr(sess, "log", []) or []):
        if not isinstance(entry, dict):
            continue
        d_raw = entry.get("day", 0)
        try:
            d = int(d_raw) if d_raw is not None else 0
        except (TypeError, ValueError):
            d = 0
        if d <= 0:
            # Legacy entry — try to sniff ``day N`` from the text so
            # at least the per-day LOG filter still groups it. Fall
            # through to fallback_day if nothing matches.
            text = str(entry.get("text", ""))
            import re as _re
            m = _re.search(r"(?:^|\W)day\s+(\d+)\b", text, _re.IGNORECASE)
            if m:
                try:
                    d = int(m.group(1))
                except ValueError:
                    d = fallback_day
            else:
                d = fallback_day
        if d <= 0:
            continue
        if day_from is not None and d < int(day_from):
            continue
        if day_to is not None and d > int(day_to):
            continue
        row = {
            "level": entry.get("level", "info"),
            "text": entry.get("text", ""),
            "day": d,
        }
        log_by_day.setdefault(d, []).append(row)
        if entry.get("phase") == "orbit":
            orbit_log_by_day.setdefault(d, []).append(row)

    # v0.9.11 — forward the per-day station-observation snapshots
    # ({"pre","post"} per seat, EXACT readings; the frontend fuzzes
    # per-viewer client-side) and the observable orbital-activity
    # tallies so the Pre-Orbital Recap + Post-Orbital Briefing reports
    # can be reopened at any scrub cursor.
    def _in_window(day_val: int) -> bool:
        if day_from is not None and day_val < int(day_from):
            return False
        if day_to is not None and day_val > int(day_to):
            return False
        return True

    station_obs_by_day: Dict[int, Dict[str, Any]] = {}
    for d_key, by_phase in (getattr(sess, "station_obs_by_day", {}) or {}).items():
        try:
            d_int = int(d_key)
        except (TypeError, ValueError):
            continue
        if d_int <= 0 or not _in_window(d_int):
            continue
        station_obs_by_day[d_int] = {
            phase: {seat: dict(obs) for seat, obs in (by_seat or {}).items()}
            for phase, by_seat in (by_phase or {}).items()
        }

    orbital_activity_by_day: Dict[int, Dict[str, Any]] = {}
    for d_key, by_seat in (getattr(sess, "orbital_activity_by_day", {}) or {}).items():
        try:
            d_int = int(d_key)
        except (TypeError, ValueError):
            continue
        if d_int <= 0 or not _in_window(d_int):
            continue
        orbital_activity_by_day[d_int] = {
            seat: dict(tally) for seat, tally in (by_seat or {}).items()
        }

    # v0.9.13 — ordered orbital EVENT log per day (chronological, no
    # coords) so the Pre-Orbital Recap can render the event list without
    # walking the replay-frame table client-side.
    orbital_events_by_day: Dict[int, Dict[str, Any]] = {}
    for d_key, by_seat in (getattr(sess, "orbital_events_by_day", {}) or {}).items():
        try:
            d_int = int(d_key)
        except (TypeError, ValueError):
            continue
        if d_int <= 0 or not _in_window(d_int):
            continue
        orbital_events_by_day[d_int] = {
            seat: [dict(ev) for ev in (evs or [])]
            for seat, evs in (by_seat or {}).items()
        }

    return {
        "mode": "replay",
        "phase": sess.phase.value,
        "width": sess.width,
        "height": sess.height,
        "players": list(sess.players),
        "days": [days[k] for k in sorted(days)],
        "day_index": day_index,
        "total_frames": len(frames),
        # v1.x — discovery-triggered REDSIGN beacons (RULEBOOK §4.11).
        # Full final list with per-region ``day``; the replay client
        # day-gates each region (shows it once the discovery night is
        # reached) and paints the persistent pulse overlay.
        "redsign": [dict(r) for r in (sess.redsign or [])],
        "catapult_by_day": catapult_by_day,
        "orbit_log_by_day": orbit_log_by_day,
        "log_by_day": log_by_day,
        "station_obs_by_day": station_obs_by_day,
        "orbital_activity_by_day": orbital_activity_by_day,
        "orbital_events_by_day": orbital_events_by_day,
        # v0.9.9 — final cumulative score per seat. The replay viewer
        # uses this to seed the HUD scoreboard before scrubbing.
        "cumulative_shipped_score": {
            p: float(sess.cumulative_shipped_score.get(p, 0.0) or 0.0)
            for p in sess.players
        },
        # v1.x — final settlement breakdown per seat (season complete only).
        # Drives the animated "+red fire-sale" / "-green penalty" delta lines
        # in the closing DUSK RESOLVE beat, and lets the station readouts fold
        # from shipped-only up/down to the canonical final score.
        # NOTE (v1.24) — deliberately NOT switched to
        # ``compute_score_breakdown``, though it looks like the same
        # thing. These four numbers drive station.js's RESOLVE animation,
        # which TWEENS the already-displayed running score by each line,
        # so they must be the deltas NOT yet reflected on the scoreboard —
        # not the season's totals. Disposed GREEN was debited from
        # ``cumulative_shipped_score`` when it was disposed, so feeding
        # the card's (correct) whole-season green figure in here would
        # subtract it a second time on screen. The results card in
        # ``get_endgame_summary`` is the one that wants totals.
        # (Related: ``vault_green_count`` is 0 once settlement has
        # flushed the vault, so the green delta line effectively never
        # fires today. Left alone — it is cosmetic and unverified without
        # a full season in a browser.)
        "settlement": (
            {
                p: {
                    "shipped": int(round(
                        sess.cumulative_shipped_score.get(p, 0.0) or 0.0
                    )),
                    "green_penalty": int(
                        GREEN_ENDGAME_PENALTY * sess.vault_green_count(p)
                    ),
                    "vault_red_loss": int(round(sess.vault_red_loss_value(p))),
                    "final": int(sess.score_for(p)),
                }
                for p in sess.players
            }
            if sess.is_season_complete()
            else {}
        ),
        # v1.x — authoritative POST-settlement hoard snapshot per seat
        # ({seat: {count, sites:[...]}}, same shape as per-frame ``hoard``).
        # The terminal SETTLEMENT orbit resolves in the ORBIT phase with
        # NO night frames, so the last night frame's hoard is stale
        # (pre-settlement, still full of RED). The replay vault
        # reconstructor uses this for the closing RESOLVE tick so the vault
        # matches the settled score instead of showing shipped RED.
        "final_hoard": (
            sess._hoard_snap_payload() if sess.is_season_complete() else {}
        ),
        # v1.0 — end-of-game support for the replay end screen.
        "player_names": dict(sess.player_names),
        # v0.9.18 — player identity profiles (custom names, tags, colors)
        "player_profiles": {
            p: dict(sess.player_profiles.get(p, {}))
            for p in sess.players
        },
        "is_season_complete": sess.is_season_complete(),
        "season_day_cap": int(
            getattr(sess, "season_day_cap", None) or 0
        ),
        # v1.20 — the replay header reads the map's identity (seed) and
        # the season's extraction curve. The seed was previously only
        # available at game creation and in the season picker, so a
        # replay could never tell you which map you were looking at.
        "seed": sess.seed,
        "extraction": compute_extraction(
            sess,
            list(range(
                1,
                int(getattr(sess, "season_day_cap", None) or 0) + 1,
            )),
        ),
    }


def compute_extraction(sess: GameSession, days: List[int]) -> Dict[str, Any]:
    """How much of the map's RED value came off the ground, and by whom.

    v1.20 — the denominator is a **genesis constant**, not a running
    total: harvesting a RED cell turns it into synthetic GREEN(255) and
    nothing regrows it (:meth:`GameSession._harvest_at`), so the map only
    ever depletes. That lets us fix "the prize" at generation time from
    the square ledger and measure every season against the same number.

    Value is tier-weighted exactly as scoring is — ``purity ×
    RED_QUALITY_MULTIPLIER[tier]`` (RULEBOOK §3.1) — so a pure-255 cell
    is worth 765 and a trace-40 cell 30. This is deliberate and it is
    why the value percentage runs far ahead of the cell percentage:
    seats go for the good squares, so a season can lift 39% of the map's
    value out of 14% of its RED cells.

    Two numerators, because they answer different questions:

    * ``harvested`` — lifted off the map. Irreversible; the cell is
      spent whether or not the seat ever banks it.
    * ``shipped``   — actually settled into score. The shortfall is red
      that died with a harvester or is still sitting in a vault.
    """
    from sea_of_colours.generator import Tile
    from sea_of_colours.game.session import RED_QUALITY_MULTIPLIER

    def value_of(purity: Any, tier: Optional[str] = None) -> int:
        p = max(0, min(255, int(purity or 0)))
        t = tier or GameSession._tier_for_purity(p)
        return int(round(p * RED_QUALITY_MULTIPLIER.get(t, 1.0)))

    # ── denominator: the map as generated ────────────────────────────
    map_value = 0
    map_cells = 0
    map_by_tier: Dict[str, int] = {}
    for entry in (sess.ledger.entries or {}).values():
        try:
            if int(entry["tile_at_generation"]) != int(Tile.RED):
                continue
            p = int(entry["purity_at_generation"])
        except (KeyError, TypeError, ValueError):
            continue
        # A RED tile can generate at purity 0 (tier "empty"). It is
        # terrain, not prize — worth nothing to mine — so it stays out
        # of both the value and the cell denominator. Counting it would
        # put a floor under "% of cells mined" that no seat can clear.
        if p <= 0:
            continue
        tier = GameSession._tier_for_purity(p)
        map_cells += 1
        map_value += value_of(p, tier)
        map_by_tier[tier] = map_by_tier.get(tier, 0) + 1

    def pct(part: int) -> float:
        return round(100.0 * part / map_value, 1) if map_value else 0.0

    # ── numerators, per seat ─────────────────────────────────────────
    by_seat: Dict[str, Dict[str, Any]] = {}
    harvested_by_day: Dict[str, List[int]] = {}
    tot_harvested = tot_shipped = tot_cells = 0

    for seat in sess.players:
        h_value = h_cells = 0
        per_day: Dict[int, int] = {}
        for parcel in sess.harvest_log.get(seat, []) or []:
            tile = parcel.get("tile_at_harvest")
            if tile is None:
                tile = parcel.get("origin_tile")
            try:
                if int(tile) != int(Tile.RED):
                    continue
            except (TypeError, ValueError):
                continue
            purity = int(GameSession._parcel_purity(parcel) or 0)
            if purity <= 0:
                continue  # worthless ground — excluded from the map total too
            v = value_of(purity)
            h_value += v
            h_cells += 1
            d = int(parcel.get("harvested_on_planning_day", 0) or 0)
            per_day[d] = per_day.get(d, 0) + v

        s_value = 0
        for parcel in sess.shipped_squares.get(seat, []) or []:
            tile = parcel.get("tile_at_harvest")
            if tile is None:
                tile = parcel.get("origin_tile")
            try:
                if int(tile) != int(Tile.RED):
                    continue
            except (TypeError, ValueError):
                continue
            eff = parcel.get("effective_purity")
            if eff is None:
                eff = GameSession._parcel_purity(parcel)
            s_value += value_of(eff, parcel.get("score_tier"))

        running = 0
        series: List[int] = []
        for d in days:
            running += per_day.get(d, 0)
            series.append(running)
        harvested_by_day[seat] = series

        by_seat[seat] = {
            "harvested_value": h_value,
            "shipped_value": s_value,
            # Harvested but never banked: lost with a harvester, or
            # still in the vault. The waffle shades this differently
            # from banked value.
            "unbanked_value": max(0, h_value - s_value),
            "cells": h_cells,
            "pct_harvested": pct(h_value),
            "pct_shipped": pct(s_value),
        }
        tot_harvested += h_value
        tot_shipped += s_value
        tot_cells += h_cells

    return {
        "map_red_value": map_value,
        "map_red_cells": map_cells,
        "map_by_tier": map_by_tier,
        "harvested_value": tot_harvested,
        "shipped_value": tot_shipped,
        "unmined_value": max(0, map_value - tot_harvested),
        "cells_mined": tot_cells,
        "pct_harvested": pct(tot_harvested),
        "pct_shipped": pct(tot_shipped),
        "pct_cells": (
            round(100.0 * tot_cells / map_cells, 1) if map_cells else 0.0
        ),
        "by_seat": by_seat,
        "harvested_by_day": harvested_by_day,
    }


def get_endgame_summary(store: SocStore, session_id: str) -> Dict[str, Any]:
    """Final results payload for the end-of-game screen.

    Returns ranked per-seat tallies (score + breakdown, credits spent,
    harvesters built, colour harvest totals, green disposal, vault
    residue), a per-day cumulative RED-harvested series for the step
    chart, and a flat shipping manifest. Safe to call mid-season (the
    numbers are simply "so far"); the UI only auto-shows it once the
    session is ``season_complete``.
    """
    from sea_of_colours.generator import Tile
    from sea_of_colours.game.session import (
        GREEN_ENDGAME_PENALTY,
        RED_QUALITY_MULTIPLIER,
        SEASON_DAY_CAP,
        compute_score_breakdown,
        owner_color_css,
    )
    from sea_of_colours.game.player_names import SEAT_LABEL

    def _parcel_is_green(parcel: Mapping[str, Any]) -> bool:
        """Is this SHIPPED row an auto-disposed GREEN parcel?

        Mirrors ``compute_player_score`` exactly, including its fallback:
        prefer the origin tile, and only if that is unreadable fall back
        to the ``score_tier`` label.
        """
        origin = parcel.get("tile_at_harvest")
        if origin is None:
            origin = parcel.get("origin_tile")
        try:
            return int(origin) == int(Tile.GREEN)
        except (TypeError, ValueError):
            return parcel.get("score_tier") == "green"

    def _parcel_score_exact(parcel: Mapping[str, Any]) -> tuple[float, int, str]:
        """Per-parcel score contribution, UNROUNDED.

        v1.24 — this is the same term ``compute_player_score`` sums, and
        it now agrees with it on both counts it used to get wrong:

        * **GREEN is a charge, not a zero.** Auto-disposed GREEN is
          appended to ``shipped_squares`` carrying its GREEN origin tile
          (``OrbitResolver._dispose_green_auto``) and the scorer subtracts
          ``GREEN_ENDGAME_PENALTY`` for it. This helper had no GREEN
          branch, so it priced a disposed parcel at
          ``effective_purity 0 x mult = 0`` — which is why the cumulative
          shipped curve ran ABOVE the final score by exactly 100 per
          disposed GREEN (Nubes_Fissure: Piotr's curve ended 1289 against
          a true 900, and 4 of that 389 gap was 4 greens).
        * **Rounding happens ONCE, at the end.** The scorer accumulates
          floats and rounds the total; this rounded every parcel, so a
          seat carrying many ``trace`` rows (x0.75, i.e. rarely an
          integer) drifted a point or two. Florian's manifest came to 624
          against a true 626 for exactly that reason.
        """
        if _parcel_is_green(parcel):
            return -float(GREEN_ENDGAME_PENALTY), 0, "green"
        eff = parcel.get("effective_purity")
        if eff is None:
            eff = sess._parcel_purity(parcel)
        eff = max(0, int(eff))
        tier = parcel.get("score_tier") or sess._tier_for_purity(eff)
        return eff * RED_QUALITY_MULTIPLIER.get(tier, 1.0), eff, tier

    def _parcel_score_value(parcel: Mapping[str, Any]) -> tuple[int, int, str]:
        """Rounded per-parcel score, for a single manifest row's display."""
        value, eff, tier = _parcel_score_exact(parcel)
        return int(round(value)), eff, tier

    sess = _hydrate_session(store, session_id)
    seats: List[str] = list(sess.players)
    cap = int(getattr(sess, "season_day_cap", None) or SEASON_DAY_CAP)
    # v1.24 — the axis has to reach the SETTLEMENT day, which is cap + 1.
    # The terminal orbit resolves after the last played night and stamps
    # ``shipped_day = cap + 1`` on everything it ships, so an axis of
    # 1..cap silently dropped the whole final settlement off the
    # cumulative-shipped curve — Lucas banked 429 points on day 8 of a
    # 7-night season and the chart's last point still read 1840 against a
    # final 2069. Derived from the parcels rather than assumed, so a
    # season that settles some other way still charts completely.
    last_shipped_day = cap
    for _seat_parcels in (sess.shipped_squares or {}).values():
        for _p in _seat_parcels or []:
            try:
                _d = int(_p.get("shipped_day") or _p.get("shipped_on_day") or 0)
            except (TypeError, ValueError):
                continue
            last_shipped_day = max(last_shipped_day, _d)
    days = list(range(1, last_shipped_day + 1))

    def _tile_of(parcel: Mapping[str, Any]) -> Optional[int]:
        t = parcel.get("tile_at_harvest")
        if t is None:
            t = parcel.get("origin_tile")
        try:
            return int(t)
        except (TypeError, ValueError):
            return None

    players_out: List[Dict[str, Any]] = []
    red_by_day: Dict[str, List[int]] = {}
    shipped_by_day: Dict[str, List[int]] = {}
    manifest: List[Dict[str, Any]] = []

    # v1.4 (#8) — season-total combat / attrition per seat for the end-game
    # report. Sourced from the public orbital-activity silhouette
    # (``orbital_activity_by_day``: launches, recoveries, harvester losses,
    # weapons fired) summed across every night, plus the asset ledger for
    # destroyed-probe counts. Each seat's tallies are rendered in that seat's
    # colour on the results screen (so a 2-seat game reads as the mine/enemy
    # split). Attacker attribution (whose harvester crushed whom) isn't
    # recorded, so counts are victim-side / own-actions only.
    activity_totals: Dict[str, Dict[str, int]] = {}
    for _by_seat in (sess.orbital_activity_by_day or {}).values():
        for _s, _tally in (_by_seat or {}).items():
            acc = activity_totals.setdefault(str(_s), {})
            for _k, _v in (_tally or {}).items():
                try:
                    acc[_k] = acc.get(_k, 0) + int(_v)
                except (TypeError, ValueError):
                    continue

    probes_lost_by_seat: Dict[str, int] = {}
    try:
        from sea_of_colours.game.asset_ledger import ASSET_LEDGER_STORE

        _ledger = ASSET_LEDGER_STORE.load(session_id)
        if _ledger is not None:
            for _rec in _ledger.list_all():
                if _rec.asset_type == "probe" and _rec.destroyed_on_day is not None:
                    probes_lost_by_seat[_rec.owner] = (
                        probes_lost_by_seat.get(_rec.owner, 0) + 1
                    )
    except Exception:
        probes_lost_by_seat = {}

    for seat in seats:
        # v0.9.18 — identity comes from the player profile first (custom or
        # bot-generated Latin name + random palette colour), then the legacy
        # player_names / SEAT_LABEL fallbacks for pre-profile sessions.
        profile = sess.player_profiles.get(seat, {})
        name = (
            profile.get("display_name")
            or sess.player_names.get(seat)
            or SEAT_LABEL.get(seat, seat.upper())
        )
        is_human = str(sess.agents.get(seat, "human")).lower() == "human"

        # Colour harvest totals + per-day RED cumulative series.
        red_harvested = green_harvested = blue_harvested = 0
        red_per_day: Dict[int, int] = {}
        for parcel in sess.harvest_log.get(seat, []) or []:
            tile = _tile_of(parcel)
            if tile == int(Tile.RED):
                red_harvested += 1
                d = int(parcel.get("harvested_on_planning_day", 0) or 0)
                red_per_day[d] = red_per_day.get(d, 0) + 1
            elif tile == int(Tile.GREEN):
                green_harvested += 1
            elif tile == int(Tile.BLUE):
                blue_harvested += 1
        running = 0
        series: List[int] = []
        for d in days:
            running += red_per_day.get(d, 0)
            series.append(running)
        red_by_day[seat] = series

        # Green disposal (flushed via the solar jettison catapult).
        green_jettisoned = 0
        for entry in sess.catapult_history or []:
            jett = (entry.get("jettison") or {}).get("seats") or {}
            seat_jett = jett.get(seat) or {}
            parcels = seat_jett.get("jettisoned_parcels")
            if isinstance(parcels, list):
                green_jettisoned += len(parcels)

        # Score breakdown.
        #
        # v1.24 — decomposed off the parcels so the three lines actually
        # ADD UP to the score on the card. It used to read ``shipped`` off
        # ``cumulative_shipped_score`` and ``green_penalty`` off
        # ``vault_green_count``, and those two describe different worlds:
        # GREEN is auto-disposed into SHIPPED at settlement, so by the
        # time the card renders the vault holds no GREEN (penalty line
        # reads 0) while its cost is already buried inside the shipped
        # figure. A seat could pay 400 in GREEN charges and the card would
        # say "shipped 900, green 0" with nothing to explain the gap —
        # which is exactly the "the end score doesn't match" report.
        # Now: shipped is RED only, and green_penalty is every GREEN
        # charged, wherever the parcel currently sits.
        _bd = compute_score_breakdown(
            sess.shipped_squares.get(seat, []),
            sess.hoard_squares.get(seat, []),
            is_complete=sess.is_season_complete(),
        )
        shipped_score = _bd["shipped"]
        green_penalty = _bd["green_penalty"]
        vault_red_loss = _bd["vault_red_loss"]

        stats = sess.season_stats.get(seat) or {}
        credits_spent = int(
            max(
                0,
                round(
                    float(stats.get("credits_awarded", 0.0))
                    - int(sess.credits.get(seat, 0))
                ),
            )
        )

        # Vault residue.
        red_tiers = sess.red_tier_counts(seat)
        vault = {
            "red_by_tier": red_tiers,
            "red_total": int(sum(red_tiers.values())),
            "green": int(sess.vault_green_count(seat)),
            "blue": int(sess.blue_tier_counts(seat).get("trace", 0)
                        + sess.blue_tier_counts(seat).get("vein", 0)
                        + sess.blue_tier_counts(seat).get("mass", 0)
                        + sess.blue_tier_counts(seat).get("pure", 0)),
        }

        # v0.9.18 — tag + colour from the player profile (set at the top of
        # the loop); the profile colour is the seat's real custom/random
        # palette colour, falling back to the legacy per-seat default.
        tag = profile.get("tag", "") or seat.upper()[:3]
        seat_color = profile.get("color") or owner_color_css(seat)

        # v1.6 kill-feed — attacker→victim matrices for this seat (as the
        # attacker), each row aligned to the fixed ``seats`` order so the
        # frontend can colour every number by the victim's seat colour.
        # v1.52 (BUG-13) — weapons come from the engine's own firing
        # ledger, not from re-reading replay frames. ``weapons_used`` is
        # bumped in all three fire paths AFTER the stock drain (a refused
        # fire never counts), and ``bought = stock + used`` is exact
        # because both start at zero — the same reasoning
        # ``league/stats.py`` already relies on. The frame-derived
        # ``orbital_activity_by_day`` used below for probes/drops is a
        # rebuild, and replay frames are exactly what a hard crash under
        # the buffered store loses, so it must not be what a weapon count
        # rests on.
        w_used = sess.weapons_used.get(seat) or {}
        w_stock = sess.weapon_stock.get(seat) or {}
        kinds = list(sess.weapon_prices())
        fired_by_kind = {k: int(w_used.get(k, 0) or 0) for k in kinds}
        ordnance = {
            k: {
                "fired": fired_by_kind[k],
                "bought": fired_by_kind[k] + int(w_stock.get(k, 0) or 0),
            }
            for k in kinds
        }

        # v1.52 (BUG-13) — repair the pre-v1.50 attribution at read time.
        # Until v1.50 the SNAP's blast reused ``_emp_sweep_destroy`` and
        # credited ``emp_probes`` unconditionally, so seasons played
        # before it carry SNAP kills filed as EMP kills — 8 seats across
        # the 44 on disk, every one of them a seat that fired a SNAP and
        # never owned an EMP. The code is fixed; the played season cannot
        # be. Only the UNAMBIGUOUS case is moved: the seat fired no EMP
        # at all, so an EMP kill is impossible and a SNAP is the only
        # thing that could have made it. A seat that fired both is left
        # alone, because then the rows genuinely cannot be told apart.
        attrib_seat: Dict[str, Dict[str, int]] = {}
        for _stat, _by_atk in (sess.combat_attrib or {}).items():
            attrib_seat[_stat] = dict((_by_atk or {}).get(seat, {}) or {})
        if fired_by_kind.get("emp", 0) == 0 and fired_by_kind.get("snap", 0) > 0:
            for _emp_stat, _snap_stat in (("emp_probes", "snap_probes"),
                                          ("emp_harvesters", "snap_harvesters")):
                _moved = attrib_seat.get(_emp_stat) or {}
                if not _moved:
                    continue
                _dst = attrib_seat.setdefault(_snap_stat, {})
                for _victim, _n in _moved.items():
                    _dst[_victim] = int(_dst.get(_victim, 0) or 0) + int(_n or 0)
                attrib_seat[_emp_stat] = {}

        def _kf_row(stat: str) -> List[int]:
            row = attrib_seat.get(stat) or {}
            return [int(row.get(v, 0) or 0) for v in seats]

        killfeed = {
            "probes_crushed": _kf_row("probes_crushed"),
            "probes_superseded": _kf_row("probes_superseded"),
            "emp_probes": _kf_row("emp_probes"),
            "snap_probes": _kf_row("snap_probes"),
            "emp_harvesters": _kf_row("emp_harvesters"),
            "snap_harvesters": _kf_row("snap_harvesters"),
            "chaff_jams": _kf_row("chaff_jams"),
            "harv_damaged": _kf_row("harv_damaged"),
            "harv_lost_chaff": _kf_row("harv_lost_chaff"),
        }

        at = activity_totals.get(seat, {})
        # v1.6 — personal (non-attributed) tallies for this seat.
        personal = {
            "probes_launched": int(at.get("probes", 0)),
            "harvesters_dropped": int(at.get("dropped", 0)),
            "harvesters_recovered": int(at.get("recovered", 0)),
            "red_harvested": red_harvested,
            "green_harvested": green_harvested,
            "blue_harvested": blue_harvested,
            # v1.52 — off the ledger, and SNAP now has a key at all. It
            # was counted (``tally_orbital_activity`` has bumped "snaps"
            # since v1.38) and summed, and then read by nobody, so the
            # weapon the room fires most was the only one the card could
            # not show: 29 SNAPs fired across the 44 finished seasons,
            # none of them printable.
            "snaps_fired": fired_by_kind.get("snap", 0),
            "emps_fired": fired_by_kind.get("emp", 0),
            "chaff_fired": fired_by_kind.get("chaff", 0),
            # Mines retired in v1.31, so there is no ledger to read and
            # the frame tally is the only source left. Kept because
            # ``tally_orbital_activity`` still walks ``mine_lay`` on
            # purpose, so an ARCHIVED season goes on reporting them.
            "mines_laid": int(at.get("mines", 0)),
            "moves_cancelled": int(sess.moves_cancelled.get(seat, 0) or 0),
        }
        combat = {
            "probes_launched": int(at.get("probes", 0)),
            "harvesters_dropped": int(at.get("dropped", 0)),
            "harvesters_recovered": int(at.get("recovered", 0)),
            # Harvesters stranded / destroyed at dawn (never recovered).
            "harvesters_lost": int(at.get("abandoned", 0)),
            # Harvesters caught by an EMP (surviving-emp'd + lost-while-emp'd).
            "harvesters_emped": int(
                at.get("emped", 0) + at.get("abandoned_emped", 0)
            ),
            "harvesters_damaged": int(at.get("damaged", 0)),
            # Probes crushed / destroyed on the surface (asset-ledger derived).
            "probes_lost": int(probes_lost_by_seat.get(seat, 0)),
            # Offensive interdiction fired this season. v1.52 — off the
            # ledger, as above. NOTE this block looks dead from the web
            # card (nothing in ``server/static/`` read it until v1.52)
            # but ``league/stats.py`` reads ``harvesters_lost`` from it,
            # so it must not be dropped as a duplicate of ``personal``.
            "snaps_fired": fired_by_kind.get("snap", 0),
            "emps_fired": fired_by_kind.get("emp", 0),
            "mines_laid": int(at.get("mines", 0)),
            "chaff_fired": fired_by_kind.get("chaff", 0),
        }

        players_out.append({
            "seat": seat,
            "name": name,
            "tag": tag,
            "is_human": is_human,
            "color": seat_color,
            "score": int(sess.score_for(seat)),
            "breakdown": {
                "shipped": int(round(shipped_score)),
                "green_penalty": int(green_penalty),
                "vault_red_loss": int(round(vault_red_loss)),
            },
            "credits_spent": credits_spent,
            "harvesters_built": int(round(float(stats.get("harvesters_built", 0.0)))),
            "red_harvested": red_harvested,
            "green_harvested": green_harvested,
            "blue_harvested": blue_harvested,
            "blue_spent": int(round(float(stats.get("blue_spent", 0.0)))),
            "green_jettisoned": green_jettisoned,
            "green_held": int(sess.vault_green_count(seat)),
            "vault": vault,
            "combat": combat,
            "killfeed": killfeed,
            "personal": personal,
            # v1.52 (BUG-13) — fired AND bought, per kind, so the card can
            # say what a weapon achieved rather than only that it existed.
            # Without the pair, ordnance bought and never fired reads
            # exactly like ordnance never bought: 41 of the 70 SNAPs
            # bought across the finished seasons were never fired, and
            # 62 of 88 seats ended holding something.
            "ordnance": ordnance,
        })

        # Shipping manifest rows for this seat + per-day cumulative
        # shipped-score series. ``value`` is the tier-weighted score the
        # parcel actually contributes (the same per-parcel term in
        # ``compute_player_score``); the parcel's ``score`` field is
        # stale (often 0) so we never read it.
        # v1.20 — read ``shipped_day``, which is what OrbitResolver
        # actually stamps at settlement. This asked for
        # ``shipped_on_day``, a key nothing has ever written, so every
        # parcel landed in day 0: the cumulative-shipped chart drew a
        # flat zero line and every manifest row said "day 0". The test
        # missed it by hand-building a parcel with the phantom key.
        shipped_per_day: Dict[int, float] = {}
        for parcel in sess.shipped_squares.get(seat, []) or []:
            exact, eff, tier = _parcel_score_exact(parcel)
            value = int(round(exact))
            ship_day = int(
                parcel.get("shipped_day")
                or parcel.get("shipped_on_day")
                or 0
            )
            # Accumulate the UNROUNDED term; the series rounds once per
            # point below, so the last point equals the final score.
            shipped_per_day[ship_day] = shipped_per_day.get(ship_day, 0.0) + exact
            # Emit the FULL shipped parcel (paint, coords, purity, transit
            # charge, tier, lineage, …) so the results screen can render
            # it with the same vault/shipping squares + hover tooltip the
            # live game uses. ``score`` is overwritten with the canonical
            # tier-weighted value (the stored ``score`` field is stale),
            # and ``owner``/``name``/``day``/``value`` are stamped on top.
            row = dict(parcel)
            row.update({
                "owner": seat,
                "name": name,
                "day": ship_day,
                "value": value,
                "score": value,
                "effective_purity": eff,
                "tier": tier,
                "catapult_shipped": True,
            })
            manifest.append(row)
        running_ship = 0.0
        ship_series: List[int] = []
        for d in days:
            running_ship += shipped_per_day.get(d, 0.0)
            ship_series.append(int(round(running_ship)))
        shipped_by_day[seat] = ship_series

    # Rank by final score (desc); stable on seat order for ties.
    ranked = sorted(
        players_out, key=lambda r: (-int(r["score"]), seats.index(r["seat"]))
    )
    for i, row in enumerate(ranked):
        row["rank"] = i + 1

    # v1.6 — fixed seat order (with colour/name/tag) that every player's
    # kill-feed row is aligned to, so column ``i`` always maps to the same
    # victim seat regardless of ranking.
    _by_seat = {r["seat"]: r for r in players_out}
    combat_seats = [
        {
            "seat": s,
            "name": _by_seat.get(s, {}).get("name", s),
            "tag": _by_seat.get(s, {}).get("tag", s.upper()[:3]),
            "color": _by_seat.get(s, {}).get("color", "#888"),
        }
        for s in seats
    ]

    return {
        "session_id": session_id,
        "season_name": sess.season_name,
        "season_day_cap": cap,
        "is_season_complete": sess.is_season_complete(),
        "days": days,
        "players": ranked,
        "combat_seats": combat_seats,
        "red_by_day": red_by_day,
        "shipped_by_day": shipped_by_day,
        "manifest": manifest,
        # v1.20 — extraction efficiency: what share of the map's RED
        # value this season actually got off the ground, and how much of
        # that was banked. See ``compute_extraction``.
        "seed": sess.seed,
        "extraction": compute_extraction(sess, days),
    }


_LULL_TAGS = frozenset({"wait", "empd", "chaffed"})


def _collapse_lull_runs(frames: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fold consecutive lull-shaped frames into a single summary frame.

    A *lull frame* is one whose ``tag`` is in :data:`_LULL_TAGS` —
    voluntary waits, EMP-smothered actions, and chaff-cancelled
    actions. Runs of two or more such frames in a row collapse to a
    single synthetic frame with ``tag="lull"`` and a ``lull_run``
    block carrying the original count + the first/last hour stamps.
    The first frame's grid/entities snapshot is preserved so the
    watcher still sees a consistent state at the collapse point.

    Single-frame lulls are passed through as-is — collapsing one
    frame to one frame has no value and would confuse the scrubber's
    hour counter.
    """
    out: List[Dict[str, Any]] = []
    run: List[Dict[str, Any]] = []

    def _emit_run() -> None:
        if not run:
            return
        if len(run) == 1:
            out.append(run[0])
            return
        first = dict(run[0])
        first["tag"] = "lull"
        first["lull_run"] = {
            "count": len(run),
            "from_hour": int(run[0].get("hour", 0) or 0),
            "to_hour": int(run[-1].get("hour", 0) or 0),
            "tags": sorted({str(f.get("tag", "")) for f in run if f.get("tag")}),
        }
        first["caption"] = (
            f"[lull] {len(run)} hour(s) of waits / smothered actions"
        )
        out.append(first)

    for f in frames:
        tag = str(f.get("tag") or "")
        if tag in _LULL_TAGS:
            run.append(f)
            continue
        if run:
            _emit_run()
            run = []
        out.append(f)
    if run:
        _emit_run()
    return out


def get_log(
    store: SocStore,
    session_id: str,
    day_from: Optional[int] = None,
    day_to: Optional[int] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    rows = store.list_log(
        session_id, day_from=day_from, day_to=day_to, limit=limit,
    )
    return {"session_id": session_id, "rows": rows}


def get_inventory(
    store: SocStore, session_id: str, player: str
) -> Dict[str, Any]:
    sess = _hydrate_session(store, session_id)
    if player not in sess.players:
        raise ValueError(
            f"unknown player '{player}' — session seats are "
            f"{list(sess.players)}"
        )
    return {
        "session_id": session_id,
        "player": player,
        "inventory": sess.inventory_pack(cast_player(player, allowed=sess.players)),
    }


def get_session_status(
    store: SocStore, session_id: str
) -> Dict[str, Any]:
    sess = _hydrate_session(store, session_id)
    # v1.5 (P1 follow-up): ``last_night_replay`` is no longer persisted in the
    # session blob (SOC_REPLAY_FRAME is authoritative), so a reloaded session
    # has an empty in-memory list. The client gates the whole night-replay /
    # animation pipeline on this count (``replayWindowCount`` → refreshNightReplay
    # → /replay fetch), so it MUST reflect the durable frame count, not the
    # stripped blob — otherwise the map jumps straight to the final frame with
    # no animations. Derive it from the frame table's lightweight day-index
    # (one GROUP BY); fall back to the in-memory list for the same-process
    # fresh-resolve case or a store without day_index.
    try:
        _didx = store.day_index(session_id)
        replay_windows = sum(int(d.get("frame_count", 0) or 0) for d in _didx)
    except Exception:
        replay_windows = len(getattr(sess, "last_night_replay", []) or [])
    if not replay_windows:
        replay_windows = len(getattr(sess, "last_night_replay", []) or [])
    # v0.9.9 — surface the cumulative shipped score per seat for the
    # HUD scoreboard, plus the latest catapult settlement so the
    # orbital summary modal can auto-pop with the new ``slot_assignments``
    # / ``slot mouseover`` data without a separate /replay round-trip.
    latest_catapult: Optional[Dict[str, Any]] = None
    if sess.catapult_history:
        latest_catapult = dict(sess.catapult_history[-1])

    # v0.9.11 — carry the latest per-day station-obs snapshots
    # ({"pre","post"}) + observable orbital-activity tally so the live
    # Pre-Orbital Recap / Post-Orbital Briefing can auto-pop without a
    # /replay round-trip. ``day`` keys are ints for the frontend.
    station_obs_by_day = getattr(sess, "station_obs_by_day", {}) or {}
    orbital_activity_by_day = getattr(sess, "orbital_activity_by_day", {}) or {}
    orbital_events_by_day = getattr(sess, "orbital_events_by_day", {}) or {}
    latest_obs_day: Optional[int] = None
    keys = []
    for d_key in station_obs_by_day.keys():
        try:
            keys.append(int(d_key))
        except (TypeError, ValueError):
            continue
    if keys:
        latest_obs_day = max(keys)
    latest_station_obs: Optional[Dict[str, Any]] = None
    if latest_obs_day is not None:
        by_phase = station_obs_by_day.get(str(latest_obs_day)) or {}
        latest_station_obs = {
            "day": latest_obs_day,
            "pre": {
                seat: dict(obs)
                for seat, obs in (by_phase.get("pre") or {}).items()
            },
            "post": {
                seat: dict(obs)
                for seat, obs in (by_phase.get("post") or {}).items()
            },
            "activity": {
                seat: dict(tally)
                for seat, tally in (
                    orbital_activity_by_day.get(str(latest_obs_day)) or {}
                ).items()
            },
            "events": {
                seat: [dict(ev) for ev in (evs or [])]
                for seat, evs in (
                    orbital_events_by_day.get(str(latest_obs_day)) or {}
                ).items()
            },
        }
    return {
        "session_id": session_id,
        # The season has a name and until v1.20 the playing UI never had
        # it — the header could only say "day 4", and the name lived
        # solely in the watcher's season picker.
        "season_name": sess.season_name,
        "season_day_cap": int(sess.season_day_cap),
        "day": sess.day,
        "phase": sess.phase.value,
        # True during the post-final-night settlement orbit so the UI can
        # show the "FINAL SETTLEMENT" banner. v1.13 — it no longer gates
        # which actions are legal; buying on the last orbit is allowed,
        # it just buys you nothing.
        "final_orbit": bool(getattr(sess, "final_orbit", False)),
        "is_season_complete": sess.is_season_complete(),
        "player_names": dict(sess.player_names),
        # v0.9.18 — player identity profiles (custom names, tags, colors)
        "player_profiles": {
            p: dict(sess.player_profiles.get(p, {}))
            for p in sess.players
        },
        "players": list(sess.players),
        # v0.9.12 — surface the per-seat human/bot map so the multiplayer
        # frontend can identify human seats (join picker, "waiting for: pN"
        # strip) straight off /status without a /view round-trip.
        "agents": dict(sess.agents),
        "pending": _seat_pack(sess),
        "replay_windows": replay_windows,
        "log_tail": sess.log[-50:],
        "max_moves": MAX_MOVES,
        "max_queue_len": MAX_QUEUE_LEN,
        "cumulative_shipped_score": {
            p: float(sess.cumulative_shipped_score.get(p, 0.0) or 0.0)
            for p in sess.players
        },
        # v1.24 — the CANONICAL running score, for the HUD scoreboard.
        #
        # ``cumulative_shipped_score`` above is a SHIPPED-bay cache and
        # stays as it is, because the SHIPPED tab is genuinely about that
        # bay. But it is the wrong number to headline as "your score",
        # for a reason that is only visible in the ORBIT phase: GREEN
        # harvested tonight sits in the vault until settlement runs, and
        # ``compute_player_score`` charges it immediately while the cache
        # does not. Measured: 3 GREEN held ->  HUD 765 vs scorer 465, and
        # the player's number then falls 300 the instant they submit.
        # Worse, ``/view`` and the agent percept already use the scorer,
        # so a human and a bot in the same seat were shown different
        # running scores for the same position.
        #
        # GREEN is not a decision the player can still avoid — disposal
        # is mandatory (§4.5, "green can never be displaced out of a
        # vault"), so the uncharged figure was one that could not
        # survive the next few seconds.
        "scores": {p: int(sess.score_for(p)) for p in sess.players},
        "latest_catapult": latest_catapult,
        "latest_station_obs": latest_station_obs,
    }


def _session_kind(session_id: str, season_name: Optional[str]) -> str:
    """Classify a session row for the picker.

    Both markers are set at write time by
    :mod:`sea_of_colours.snowpark.snapshot` — fixtures take an id prefix,
    clones take a season-name prefix — so this is a read of intent, not a
    guess about shape.
    """
    if str(session_id or "").startswith(("SNAP_", "FIX_")):
        return "fixture"
    if str(season_name or "").startswith("REPLAY:"):
        return "replay"
    return "season"


def list_sessions(store: SocStore) -> Dict[str, Any]:
    """Surface every persisted session for the watcher frontend.

    The /watch.html season picker consumes this directly, so each row
    carries everything needed to render an entry without a per-session
    follow-up call:

    - ``season_name`` / ``season_slug`` — human-readable label + URL slug
      (``glacies-helix``) for deep-linking from the CLI runner.
    - ``day`` / ``phase`` — "DAY 5/5 · season_complete" rendering.
    - ``scores`` — final per-seat scores (``{seat: score}`` for every
      seat in the game, N-seat aware) so the picker can show winners.
      These use the CANONICAL :meth:`GameSession.score_for` — the same
      number the end-of-game screen shows (shipped tier-weighted value
      minus the green endgame penalty, plus the season-end vault-RED
      fire-sale) — so the picker, the results screen, and the HUD's
      shipped-score line all agree.
    - ``last_touched_at`` is intentionally NOT serialised — it's a
      datetime under Snowflake and we don't want JSON ordering / TZ
      surprises in the picker. The list is already returned in
      ``last_touched_at DESC`` order by the store.
    - ``kind`` — ``season`` | ``fixture`` | ``replay``. Test fixtures
      (frozen nights, see :mod:`sea_of_colours.snowpark.snapshot`) and
      their throwaway clones live in the same table as real seasons and
      outnumber them, so the picker needs to tell them apart. They read
      as permanently mid-flight because nothing ever advances them.
    """
    from sea_of_colours.game.season_names import season_name_to_slug

    rows = store.list_sessions()
    # Single bulk score pass — the store folds the canonical
    # ``compute_player_score`` over each session's SHIPPED + HOARD
    # parcel tables (N-seat aware, tier multipliers + endgame penalties
    # included), so the picker matches the results screen exactly
    # without the N-roundtrip cost of hydrating every session.
    try:
        scores_by_sid = store.bulk_session_scores()
    except Exception:
        scores_by_sid = {}
    out: List[Dict[str, Any]] = []
    for r in rows:
        sid = r["session_id"]
        season_name = r.get("season_name")
        slug = season_name_to_slug(season_name) if season_name else None
        scores = scores_by_sid.get(sid, {"p1": 0, "p2": 0})
        out.append(
            {
                "session_id": sid,
                "season_name": season_name,
                "season_slug": slug,
                "kind": _session_kind(sid, season_name),
                "day": r.get("day"),
                "phase": r.get("phase"),
                "width": r.get("width"),
                "height": r.get("height"),
                "seed": r.get("seed"),
                "scores": scores,
                # Forwarded by CompositeSocStore so the picker can show a
                # per-row origin icon ("snowflake" / "local"); None under a
                # single-backend store.
                "source": r.get("source"),
            }
        )
    return {"sessions": out}


def find_session_by_slug(
    store: SocStore, season_slug: str
) -> Optional[Dict[str, Any]]:
    """Resolve a season URL slug to its session metadata row.

    Used by the watcher frontend's ``?season=<slug>`` deep-link path —
    the slug is human-friendly and tied to the season name, the
    session_id is the canonical key for every other API. Returns
    ``None`` when the slug doesn't match any persisted season.
    """
    if not season_slug:
        return None
    target = str(season_slug).strip().lower()
    if not target:
        return None
    payload = list_sessions(store)
    for row in payload.get("sessions", []):
        if (row.get("season_slug") or "").lower() == target:
            return row
    return None


def save_agent_rationale(
    store: SocStore,
    session_id: str,
    day: int,
    agent_id: str,
    player: str,
    rationale: str,
    *,
    runtime: Optional[str] = None,
    prompt_excerpt: Optional[str] = None,
    tool_calls: Optional[List[Mapping[str, Any]]] = None,
    response_text: Optional[str] = None,
    ms_elapsed: Optional[int] = None,
) -> Dict[str, Any]:
    """Audit-trail row + visible LOG line for the agent's turn.

    Two writes:

    * One durable row in ``SOC_AGENT_INVOCATION`` (the audit trail
      consumed by ``test_run_agent_turn_logs_invocation`` and any
      future "what did the agent think?" tooling).
    * One entry in the session's in-memory ``log`` so the LOG panel
      in the playing UI surfaces ``[<agent_id>] [<runtime>] <rationale>``
      and it survives the next ``refreshStatus`` re-render. Without
      this, the front-end appended the rationale to ``orchLogEl`` only
      for it to be wiped by the next ``log_tail`` refresh — making
      the click look silent even though the agent had decided.
    """
    row = {
        "session_id": session_id,
        "day": int(day),
        "agent_id": agent_id,
        "player": player,
        "rationale": rationale,
        "prompt_excerpt": prompt_excerpt,
        "tool_calls": list(tool_calls or []),
        "response_text": response_text,
        "ms_elapsed": ms_elapsed,
        "status": "ok",
    }
    store.append_agent_invocation(row)

    # Best-effort: also append to the session's in-memory log so the
    # LOG panel reflects the rationale on the next status fetch.
    # Skip silently if the session has somehow disappeared (rare).
    runtime_tag = f"[{runtime}] " if runtime else ""
    log_text = f"[{agent_id}] {runtime_tag}({player}) {rationale}"
    try:
        sess = _hydrate_session(store, session_id)
        sess.log_info(log_text)
        # v1.42 — authoritative row ONLY, not ``save_session_full``.
        #
        # The single mutation above is one appended ``log`` entry, which lives
        # inside ``json_state``; no parcel, grid or asset state changes here.
        # Going through ``save_session_full`` therefore re-wrote the hoard and
        # shipped parcel tables with byte-identical data — a DELETE + INSERT
        # pair each — on every agent turn. Measured on a 6-turn season that was
        # 6 of the 13 total ``save_session_full`` calls and 4 redundant
        # statements apiece, at ~300ms of unavoidable per-statement client
        # latency (see docs/SNOWFLAKE_LATENCY_BRIEF.md §T1.1).
        #
        # Writing only the authoritative row keeps ``sess.log`` in the blob, so
        # ``get_session_status``'s ``log_tail`` and the per-day log partitioning
        # in ``get_view`` are unchanged. It also STRICTLY REDUCES the §7 hazard:
        # fewer concurrent writes around the authoritative MERGE, and this one
        # is synchronous on the calling thread with no pool involved.
        store.save_session(_save_session_row(sess))
        # Also persist the entry into SOC_GAME_LOG so it survives a
        # full proc round-trip on the Snowflake backend.
        store.append_log(
            session_id,
            int(day),
            [{"level": "info", "text": log_text}],
        )
    except KeyError:
        pass
    return {"ok": True, "session_id": session_id, "agent_id": agent_id}
