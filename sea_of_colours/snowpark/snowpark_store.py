"""SnowparkSocStore — :class:`SocStore` backed by a live Snowpark session.

The protocol surface in :mod:`sea_of_colours.snowpark.store` is intentionally
narrow; this module implements each method as a parameterised SQL call
against the SOC_* schema (declared in ``snowflake/soc_schema.sql``).

Two modes are supported:

* **Stored-procedure mode** (the normal case in Snowflake): a Snowpark
  ``session`` object is passed in (the same one the proc handler
  receives). We use ``session.sql(...).collect()`` to issue statements
  and ``session.create_dataframe(...)`` to bulk-insert rows.
* **Driver mode** (used by Phase 3's FastAPI proxy): a Python connector
  cursor wrapped in a minimal adapter — see :func:`from_cursor`. Same
  semantics; just a different way of issuing SQL.

Insert performance: we use a single ``MERGE INTO ... USING (SELECT ...
FROM VALUES(...))`` per batched upsert so a 25-frame night writes 25
rows of replay in one statement, and the per-day SOC_GAME_LOG insert
is also one statement.
"""

from __future__ import annotations

import json
import os
import queue
import threading
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

# v1.42 — kill-switch for the empty-parcel DELETE skip in
# :meth:`SnowparkSocStore._replace_parcels`. Default ON; export
# ``SOC_SKIP_EMPTY_PARCELS=0`` to restore the unconditional DELETE.
_SKIP_EMPTY_PARCELS = os.environ.get(
    "SOC_SKIP_EMPTY_PARCELS", "1"
).strip().lower() not in {"0", "false", "no"}


def _json(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


def _quote(value: Any) -> str:
    """SQL-literal-encode a Python value (NULL-safe).

    Snowflake interprets backslash escape sequences inside single-quoted
    string literals (``'foo\\n'`` → literal newline, ``'\\\\'`` →
    literal backslash, etc.), so we MUST double backslashes *before*
    we wrap in quotes. Otherwise any JSON we hand to ``PARSE_JSON`` is
    silently corrupted — e.g. ``json.dumps({"x": "a\\nb"})`` produces
    the two-character escape ``\\n``, which Snowflake then turns back
    into a literal newline inside the JSON string, and ``PARSE_JSON``
    explodes with ``"unterminated string, line 2, pos 0"``.

    Order matters: double the backslashes first, then the quotes — the
    other way round would also double-escape the backslashes inside the
    ``''`` we just inserted.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value).replace("\\", "\\\\").replace("'", "''")
    return f"'{text}'"


class SnowparkSocStore:
    """SocStore implementation against the live SEA_OF_COLOURS schema."""

    # Order: rows that reference nothing first. There are no declared FKs
    # in ``soc_schema.sql`` so technically any order works, but listing
    # the "child" tables before ``SOC_GAME_SESSION`` matches the natural
    # data dependency and keeps the wipe self-documenting.
    _SESSION_TABLES: tuple = (
        # v1.19 — orchestrator_2's two tables were absent from every deploy
        # until now, so they were never wipeable either. Both key on
        # session_id; leaving them out would let a harness inherit a dead
        # game's journal on a reused session id.
        "SOC_AGENT_MEMORY",
        "SOC_AGENT_BINDING",
        "SOC_AGENT_INVOCATION",
        "SOC_ORCHESTRATOR_LOG",
        "SOC_REPLAY_FRAME",
        "SOC_GAME_LOG",
        "SOC_POLICY_QUEUE",
        "SOC_SHIPPED_PARCEL",
        "SOC_HOARD_PARCEL",
        "SOC_ASSET_RECORD",
        "SOC_ENTITY_STATE",
        "SOC_GRID_CELL",
        "SOC_SQUARE_IDENTITY",
        "SOC_GAME_SESSION",
    )

    def __init__(
        self,
        session,
        *,
        session_factory=None,
        max_connections: int = 1,
    ) -> None:
        """
        :param session_factory: zero-arg callable returning a NEW Snowpark
            session. Supplying it turns on the connection pool; without
            it every statement runs on ``session`` exactly as before.
        :param max_connections: ceiling on live connections, counting
            ``session``. ``1`` keeps the historical single-session
            behaviour.

        Why a pool at all: ``engine.save_session_full`` fans seven table
        writes across a thread pool, but they all shared this one
        session, and the connector serialises per connection — so the
        "parallel" save was largely a queue. Measured, a 4-row write
        took as long as a 1120-row one (~1s each) because the cost was
        waiting, not work.

        Why borrow/return rather than thread-locals: that fan-out builds
        a NEW ``ThreadPoolExecutor`` per save, so thread-affine sessions
        would open fresh connections every save — and a connection costs
        ~4.5s to establish, far more than the save it was meant to speed
        up. A pool outlives the threads that borrow from it.
        """
        self.session = session
        self._factory = session_factory
        self._max_connections = max(1, int(max_connections))
        self._idle: "queue.LifoQueue" = queue.LifoQueue()
        self._idle.put(session)
        self._live = 1
        self._pool_lock = threading.Lock()
        # v1.42 — (table, session_id, owner-set) scopes this instance has
        # already emptied, so a repeat empty bundle can skip its DELETE.
        # See :meth:`_replace_parcels`.
        self._empty_parcel_scopes: set = set()

    # ── SQL helpers ───────────────────────────────────────────────
    def _borrow(self):
        """A session nobody else is using, growing the pool if allowed.

        LIFO so a small working set stays hot rather than round-robining
        every connection. Falls back to blocking for a free session once
        the ceiling is reached, which bounds connections without ever
        failing a statement.
        """
        if self._factory is None:
            return self.session
        try:
            return self._idle.get_nowait()
        except queue.Empty:
            pass
        with self._pool_lock:
            grow = self._live < self._max_connections
            if grow:
                self._live += 1
        if grow:
            try:
                return self._factory()
            except Exception:
                # A warehouse that will not give us a second connection
                # is not a reason to fail the turn — wait for a free one.
                with self._pool_lock:
                    self._live -= 1
        return self._idle.get()

    def _release(self, session) -> None:
        if self._factory is not None:
            self._idle.put(session)

    def _exec(
        self, sql: str, params: Optional[Sequence[Any]] = None,
    ) -> List[Any]:
        """Run one statement, optionally with bind parameters.

        v1.41 — BIND, DO NOT INLINE. Snowflake charges
        ``compilation_time`` roughly in proportion to the SIZE OF THE SQL
        TEXT, and this store used to inline every value as a literal. The
        authoritative session MERGE shipped ~510KB of JSON as one string
        literal and spent ~400ms being *parsed* before any work began; a
        replay-frame insert shipped 1.17MB and spent ~1400ms. Measured
        against ``query_history``, compilation was ~40% of a 13s turn.

        A bind sends the payload out-of-band, so the SQL text stays a
        couple of hundred bytes, compiles in tens of ms, and — because
        the text is now identical every turn — Snowflake can reuse the
        compiled plan. See ``docs/SNOWFLAKE_LATENCY_BRIEF.md``.
        """
        session = self._borrow()
        try:
            if params:
                return list(session.sql(sql, params=list(params)).collect())
            return list(session.sql(sql).collect())
        finally:
            self._release(session)

    # ── season lifecycle ──────────────────────────────────────────
    def wipe_all_sessions(self) -> None:
        """Wipe every persisted season EXCEPT eval-tagged ones.

        Called by :func:`engine.init_session` so each NEW GAME starts
        a fresh season — the "every new season overwrites the
        previous" contract requested by the player UI. We use
        ``DELETE`` rather than ``TRUNCATE`` because ``DELETE`` only
        needs the ``DELETE`` privilege (already granted to
        ``SYSADMIN``); ``TRUNCATE`` needs ownership and would force
        every caller back into ``ACCOUNTADMIN``.

        Sessions whose ``season_name`` starts with ``eval:`` are
        preserved across wipes — those are evaluation fixtures
        owned by the ``/evals`` command center, not gameplay
        seasons, and a player clicking NEW GAME shouldn't nuke
        their A/B history. We delete child rows by parent-session
        membership so the SQL is consistent: any child row whose
        ``session_id`` points at a NON-eval session disappears
        along with that session.

        See :func:`server.app._is_eval_session` for the matching
        predicate on the API side.
        """
        # Step 1: child tables — drop any row whose parent is about
        # to be deleted. We compute "to-be-deleted parents" as the
        # complement of the eval-tagged set so a future tag scheme
        # that doesn't use season_name can override this.
        deletable_predicate = (
            "session_id IN ("
            "  SELECT session_id FROM SOC_GAME_SESSION "
            "  WHERE season_name IS NULL "
            "     OR NOT season_name LIKE 'eval:%'"
            ")"
        )
        for tbl in self._SESSION_TABLES:
            if tbl == "SOC_GAME_SESSION":
                continue
            try:
                self._exec(f"DELETE FROM {tbl} WHERE {deletable_predicate}")
            except Exception:
                # Table may not exist on a fresh deploy — treat as a
                # no-op so the first ever NEW GAME still succeeds.
                pass
        # Step 2: drop the parent rows themselves (any tagged eval
        # session stays).
        try:
            self._exec(
                "DELETE FROM SOC_GAME_SESSION "
                "WHERE season_name IS NULL OR NOT season_name LIKE 'eval:%'"
            )
        except Exception:
            pass

    def delete_session(self, session_id: str) -> None:
        """Delete one session id across every SOC_* table.

        Child tables first (by ``session_id`` membership), then the parent
        ``SOC_GAME_SESSION`` row. Missing tables are tolerated so a partial
        deploy still deletes what exists."""
        pred = f"session_id = {_quote(str(session_id))}"
        for tbl in self._SESSION_TABLES:
            if tbl == "SOC_GAME_SESSION":
                continue
            try:
                self._exec(f"DELETE FROM {tbl} WHERE {pred}")
            except Exception:
                pass
        try:
            self._exec(f"DELETE FROM SOC_GAME_SESSION WHERE {pred}")
        except Exception:
            pass

    # ── SOC_GAME_SESSION ──────────────────────────────────────────
    def save_session(self, row: Mapping[str, Any]) -> None:
        # The hot statement of the whole app: ``json_state`` is the ONLY
        # row the replay engine reads back, and it is ~510KB of JSON.
        # Inlined as a literal it cost ~1105ms, of which ~400ms was pure
        # parse. Bound, the SQL text is ~600 bytes and constant across
        # turns, so the plan caches (v1.41).
        sql = (
            "MERGE INTO SOC_GAME_SESSION t "
            "USING (SELECT "
            "? AS session_id, "
            "? AS season_name, "
            "?::INT AS width, "
            "?::INT AS height, "
            "?::INT AS seed, "
            "?::INT AS day, "
            "? AS phase, "
            "PARSE_JSON(?) AS json_state"
            ") s ON t.session_id = s.session_id "
            "WHEN MATCHED THEN UPDATE SET "
            "  season_name = s.season_name, "
            "  width = s.width, height = s.height, seed = s.seed, "
            "  day = s.day, phase = s.phase, json_state = s.json_state, "
            "  last_touched_at = CURRENT_TIMESTAMP() "
            "WHEN NOT MATCHED THEN INSERT "
            "  (session_id, season_name, width, height, seed, day, phase, json_state) "
            "  VALUES (s.session_id, s.season_name, s.width, s.height, "
            "          s.seed, s.day, s.phase, s.json_state)"
        )
        self._exec(sql, [
            str(row["session_id"]),
            row.get("season_name"),
            int(row["width"]),
            int(row["height"]),
            int(row["seed"]),
            int(row["day"]),
            str(row["phase"]),
            _json(row.get("json_state")),
        ])

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        sql = (
            "SELECT session_id, season_name, width, height, seed, day, phase, json_state "
            "FROM SOC_GAME_SESSION WHERE session_id = "
            f"{_quote(session_id)}"
        )
        rows = self._exec(sql)
        if not rows:
            return None
        return _row_to_dict(rows[0])

    def list_sessions(self) -> List[Dict[str, Any]]:
        sql = (
            "SELECT session_id, season_name, width, height, seed, day, phase "
            "FROM SOC_GAME_SESSION ORDER BY last_touched_at DESC"
        )
        return [_row_to_dict(r) for r in self._exec(sql)]

    def latest_session(self) -> Optional[Dict[str, Any]]:
        rows = self.list_sessions()
        return rows[0] if rows else None

    def bulk_session_scores(self) -> Dict[str, Dict[str, int]]:
        """Canonical per-seat score for every session.

        Folds the persisted SHIPPED / HOARD rows through
        :func:`sea_of_colours.game.session.compute_player_score` — the
        same scorer behind :meth:`GameSession.score_for` and the results
        screen — so the watcher's season picker cannot disagree with the
        endgame card. Three grouped reads, still no per-session
        hydration.

        This used to delegate to :sql:`SOC_SESSION_STANDINGS`, and that
        view is a second, divergent copy of the scoring rules: it sums
        raw ``origin_purity``, so it applies no RED tier multiplier and
        it *credits* the auto-disposed GREEN parcels §4.7 appends to
        SHIPPED at +255 each instead of charging
        ``GREEN_ENDGAME_PENALTY``. That is 355 points per green parcel,
        enough to invert the ranking of a finished season. Only the
        Snowflake store carried the duplicate — the memory and file
        stores always called the real scorer — which is why every
        offline season and the whole test suite agreed while live
        Snowflake games quietly did not.
        """
        from sea_of_colours.game.session import compute_player_score

        phase_by_sid: Dict[str, str] = {}
        for row in self._exec("SELECT session_id, phase FROM SOC_GAME_SESSION"):
            d = _row_to_dict(row)
            sid = d.get("session_id")
            if sid:
                phase_by_sid[str(sid)] = str(d.get("phase") or "")

        def _by_session(table: str) -> Dict[str, Dict[str, List[Mapping[str, Any]]]]:
            grouped: Dict[str, Dict[str, List[Mapping[str, Any]]]] = {}
            rows = self._exec(
                "SELECT session_id, owner, origin_tile, origin_purity, payload "
                f"FROM {table}"
            )
            for row in rows:
                d = _row_to_dict(row)
                sid, owner = d.get("session_id"), d.get("owner")
                if not sid or not owner:
                    continue
                seats = grouped.setdefault(str(sid), {})
                seats.setdefault(str(owner), []).append(_canon_parcel(d))
            return grouped

        shipped = _by_session("SOC_SHIPPED_PARCEL")
        hoard = _by_session("SOC_HOARD_PARCEL")

        out: Dict[str, Dict[str, int]] = {}
        for sid in set(phase_by_sid) | set(shipped) | set(hoard):
            shipped_by_owner = shipped.get(sid, {})
            hoard_by_owner = hoard.get(sid, {})
            # N-seat aware: the owner column is the only seat list we
            # have here (SOC_GAME_SESSION has no players column), so a
            # session with no parcels yet falls back to the 2-seat default.
            seats = sorted(set(shipped_by_owner) | set(hoard_by_owner)) or ["p1", "p2"]
            # The season-end vault-RED fire-sale only realises once the
            # season is actually over, matching GameSession.score_for.
            complete = phase_by_sid.get(sid, "") == "season_complete"
            out[sid] = {
                seat: compute_player_score(
                    shipped_by_owner.get(seat, []),
                    hoard_by_owner.get(seat, []),
                    is_complete=complete,
                )
                for seat in seats
            }
        return out

    # ── SOC_SQUARE_IDENTITY ───────────────────────────────────────
    def upsert_square_identity(
        self, session_id: str, entries: List[Mapping[str, Any]]
    ) -> None:
        if not entries:
            return
        values = ",".join(
            "(" + ",".join(
                [
                    _quote(session_id),
                    _quote(int(r["x"])),
                    _quote(int(r["y"])),
                    _quote(str(r["square_id"])),
                    _quote(int(r["tile_at_generation"])),
                    _quote(int(r["purity_at_generation"])),
                ]
            ) + ")"
            for r in entries
        )
        sql = (
            "MERGE INTO SOC_SQUARE_IDENTITY t USING ("
            f"SELECT * FROM (VALUES {values}) AS v("
            "  session_id, x, y, square_id, tile_at_generation, purity_at_generation"
            ")"
            ") s "
            "ON t.session_id = s.session_id AND t.x = s.x AND t.y = s.y "
            "WHEN MATCHED THEN UPDATE SET square_id = s.square_id, "
            "  tile_at_generation = s.tile_at_generation, "
            "  purity_at_generation = s.purity_at_generation "
            "WHEN NOT MATCHED THEN INSERT VALUES "
            "(s.session_id, s.x, s.y, s.square_id, "
            " s.tile_at_generation, s.purity_at_generation)"
        )
        self._exec(sql)

    # ── SOC_ASSET_RECORD ──────────────────────────────────────────
    def upsert_asset_records(
        self, session_id: str, records: List[Mapping[str, Any]]
    ) -> None:
        if not records:
            return
        values = ",".join(
            "(" + ",".join(
                [
                    _quote(session_id),
                    _quote(str(r["asset_id"])),
                    _quote(str(r["asset_type"])),
                    _quote(str(r["owner"])),
                    _quote(int(r["created_on_day"])),
                    _quote(r.get("first_deployed_day")),
                    _quote(r.get("destroyed_on_day")),
                    _quote(r.get("destroyed_by")),
                    _quote(r.get("last_seen_x")),
                    _quote(r.get("last_seen_y")),
                    _quote(int(r.get("total_red_harvested", 0))),
                    _quote(int(r.get("total_days_on_surface", 0))),
                ]
            ) + ")"
            for r in records
        )
        sql = (
            "MERGE INTO SOC_ASSET_RECORD t USING ("
            f"SELECT * FROM (VALUES {values}) AS v("
            "  session_id, asset_id, asset_type, owner, created_on_day, "
            "  first_deployed_day, destroyed_on_day, destroyed_by, "
            "  last_seen_x, last_seen_y, total_red_harvested, "
            "  total_days_on_surface"
            ")"
            ") s ON t.session_id = s.session_id AND t.asset_id = s.asset_id "
            "WHEN MATCHED THEN UPDATE SET "
            "  asset_type = s.asset_type, owner = s.owner, "
            "  created_on_day = s.created_on_day, "
            "  first_deployed_day = s.first_deployed_day, "
            "  destroyed_on_day = s.destroyed_on_day, "
            "  destroyed_by = s.destroyed_by, "
            "  last_seen_x = s.last_seen_x, last_seen_y = s.last_seen_y, "
            "  total_red_harvested = s.total_red_harvested, "
            "  total_days_on_surface = s.total_days_on_surface, "
            "  updated_at = CURRENT_TIMESTAMP() "
            "WHEN NOT MATCHED THEN INSERT VALUES "
            "(s.session_id, s.asset_id, s.asset_type, s.owner, "
            " s.created_on_day, s.first_deployed_day, s.destroyed_on_day, "
            " s.destroyed_by, s.last_seen_x, s.last_seen_y, "
            " s.total_red_harvested, s.total_days_on_surface, "
            " CURRENT_TIMESTAMP())"
        )
        self._exec(sql)

    # ── SOC_ENTITY_STATE ──────────────────────────────────────────
    def replace_entity_state(
        self, session_id: str, rows: List[Mapping[str, Any]]
    ) -> None:
        """Replace the session's entity rows with ``rows``.

        v0.9.5 — collapsed the per-entity INSERT loop into one
        multi-row ``INSERT … SELECT … FROM VALUES`` statement so a
        12-entity night (3 harvesters + 1 lifter + ~8 probes per
        seat) costs 2 Snowflake round-trips (DELETE + INSERT)
        instead of 1 + N. ``cargo_squares`` rides as a VARIANT via
        ``PARSE_JSON`` lifted into the SELECT.
        """
        self._exec(
            f"DELETE FROM SOC_ENTITY_STATE WHERE session_id = {_quote(session_id)}"
        )
        if not rows:
            return
        cols = (
            "session_id, entity_id, entity_type, owner, x, y, carrying_red, "
            "orbital_cargo_red, cargo_squares, lost_last_night"
        )
        alias_list = (
            "session_id, entity_id, entity_type, owner, x, y, carrying_red, "
            "orbital_cargo_red, cargo_squares_j, lost_last_night"
        )
        select_list = (
            "session_id, entity_id, entity_type, owner, x, y, carrying_red, "
            "orbital_cargo_red, PARSE_JSON(cargo_squares_j), lost_last_night"
        )
        # Entities per session are ≤ ~15, so a single 1-chunk batch
        # always fits comfortably under the SQL text cap.
        value_tuples: List[str] = []
        for r in rows:
            cargo_json = _json(list(r.get("cargo_squares") or []))
            parts = [
                _quote(session_id),
                _quote(str(r["entity_id"])),
                _quote(str(r["entity_type"])),
                _quote(str(r["owner"])),
                _quote(r.get("x")),
                _quote(r.get("y")),
                _quote(bool(r.get("carrying_red"))),
                _quote(bool(r.get("orbital_cargo_red"))),
                _quote(cargo_json),
                _quote(bool(r.get("lost_last_night"))),
            ]
            value_tuples.append("(" + ", ".join(parts) + ")")
        sql = (
            f"INSERT INTO SOC_ENTITY_STATE ({cols}) "
            f"SELECT {select_list} FROM (VALUES "
            + ", ".join(value_tuples)
            + f") AS v({alias_list})"
        )
        self._exec(sql)

    # ── SOC_GRID_CELL ─────────────────────────────────────────────
    def upsert_grid_cells(
        self, session_id: str, rows: List[Mapping[str, Any]]
    ) -> None:
        if not rows:
            return
        # 40×28 = 1120 cells. Each VALUES row is tiny (~6 small ints
        # plus the session_id string), so a single 1500-row chunk
        # comfortably fits Snowflake's 1MB SQL text cap and collapses
        # the entire grid into ONE round-trip instead of the previous
        # six chunks of 200. v0.9.6 — the grid write was the biggest
        # remaining offender in ``save_session_full`` (3-6s of wall
        # time); merging into one statement drops that to a single
        # Snowflake roundtrip (~500ms-1s).
        for chunk in _chunks(rows, 1500):
            values = ",".join(
                "(" + ",".join(
                    [
                        _quote(session_id),
                        _quote(int(r["x"])),
                        _quote(int(r["y"])),
                        _quote(int(r["tile"])),
                        _quote(int(r["purity"])),
                        _quote(r.get("last_mutated_day")),
                    ]
                ) + ")"
                for r in chunk
            )
            sql = (
                "MERGE INTO SOC_GRID_CELL t USING ("
                f"SELECT * FROM (VALUES {values}) AS v("
                "  session_id, x, y, tile, purity, last_mutated_day"
                ")"
                ") s ON t.session_id = s.session_id AND t.x = s.x AND t.y = s.y "
                "WHEN MATCHED THEN UPDATE SET tile = s.tile, "
                "  purity = s.purity, last_mutated_day = s.last_mutated_day "
                "WHEN NOT MATCHED THEN INSERT VALUES "
                "(s.session_id, s.x, s.y, s.tile, s.purity, s.last_mutated_day)"
            )
            self._exec(sql)

    # ── SOC_HOARD_PARCEL / SOC_SHIPPED_PARCEL ────────────────────
    def replace_hoard(
        self, session_id: str, owner: str, rows: List[Mapping[str, Any]]
    ) -> None:
        self._replace_parcels("SOC_HOARD_PARCEL", session_id, {owner: rows})

    def replace_shipped(
        self, session_id: str, owner: str, rows: List[Mapping[str, Any]]
    ) -> None:
        self._replace_parcels("SOC_SHIPPED_PARCEL", session_id, {owner: rows})

    def replace_hoard_bundle(
        self,
        session_id: str,
        by_owner: Mapping[str, List[Mapping[str, Any]]],
    ) -> None:
        """One-shot replacement of every player's hoard for a session.

        v0.9.6 — pre-v0.9.6 ``save_session_full`` called
        :meth:`replace_hoard` once per player, costing 4 Snowflake
        round-trips (2 DELETE + 2 INSERT) for hoard + the same for
        shipped. The bundled variant collapses both seats into ONE
        DELETE (session-scoped) + ONE multi-owner batched INSERT
        per parcel table, dropping the per-call cost from ~1.5s of
        wall time to a single Snowflake roundtrip.
        """
        self._replace_parcels("SOC_HOARD_PARCEL", session_id, by_owner)

    def replace_shipped_bundle(
        self,
        session_id: str,
        by_owner: Mapping[str, List[Mapping[str, Any]]],
    ) -> None:
        """Counterpart of :meth:`replace_hoard_bundle` for shipped parcels."""
        self._replace_parcels("SOC_SHIPPED_PARCEL", session_id, by_owner)

    def _replace_parcels(
        self, table: str, session_id: str,
        by_owner: Mapping[str, List[Mapping[str, Any]]],
    ) -> None:
        """Replace one or more players' parcel rows in ``table`` atomically.

        v0.9.6 — generalised the per-player API to take an owner→rows
        map so callers (like ``save_session_full``) can collapse the
        per-seat loop into a SINGLE DELETE + SINGLE batched INSERT.
        Pre-v0.9.5 a full hoard (25 parcels) cost 1 + 25 round-trips
        per player per save; v0.9.5 cut that to 2 round-trips per
        player; v0.9.6 cuts it to 2 round-trips TOTAL across both
        seats. The single-owner public methods stay thin wrappers
        around this for backward compatibility.
        """
        owners = sorted(by_owner.keys())
        if not owners:
            return
        value_tuples: List[str] = []
        for owner in owners:
            for r in (by_owner.get(owner) or []):
                payload_json = _json(r.get("payload") or dict(r))
                parts = [
                    _quote(session_id),
                    _quote(owner),
                    _quote(int(r["slot"])),
                    _quote(r.get("square_id")),
                    _quote(r.get("origin_x")),
                    _quote(r.get("origin_y")),
                    _quote(r.get("origin_tile")),
                    _quote(r.get("origin_purity")),
                    _quote(r.get("harvested_day")),
                    _quote(r.get("site_uid")),
                    _quote(payload_json),
                ]
                value_tuples.append("(" + ", ".join(parts) + ")")

        # v1.42 — skip the DELETE when we are replacing empty with empty.
        #
        # Hoards are empty for most of the early game, so a season spent ~8.2s
        # of server time issuing DELETEs that matched no rows, plus ~300ms of
        # client latency each (docs/SNOWFLAKE_LATENCY_BRIEF.md §T1.1).
        #
        # The guard is deliberately narrow: we only skip when THIS store
        # instance has already emptied this exact (table, session, owner-set)
        # scope, so the first call for any scope always issues the DELETE and
        # establishes the fact. We never skip when there are rows to write.
        #
        # Soundness limit, stated plainly: if a DIFFERENT process inserts
        # parcels for the same session behind our back, we would skip a DELETE
        # that should have removed them. Parcels are only ever written by
        # ``save_session_full`` for a session, and the backend is per-game, so
        # there is one writer in practice. Set ``SOC_SKIP_EMPTY_PARCELS=0`` to
        # disable and restore the unconditional DELETE.
        scope = (table, session_id, ",".join(owners))
        if not value_tuples:
            if _SKIP_EMPTY_PARCELS and scope in self._empty_parcel_scopes:
                return
            self._delete_parcels(table, session_id, owners)
            # Single set mutation, atomic under the GIL. ``save_session_full``
            # runs hoard and shipped concurrently but they are different
            # tables, hence different scopes — no contention on a key.
            self._empty_parcel_scopes.add(scope)
            return
        self._empty_parcel_scopes.discard(scope)
        self._delete_parcels(table, session_id, owners)
        cols = (
            "session_id, owner, slot, square_id, origin_x, origin_y, "
            "origin_tile, origin_purity, harvested_day, site_uid, payload"
        )
        alias_list = (
            "session_id, owner, slot, square_id, origin_x, origin_y, "
            "origin_tile, origin_purity, harvested_day, site_uid, payload_j"
        )
        select_list = (
            "session_id, owner, slot, square_id, origin_x, origin_y, "
            "origin_tile, origin_purity, harvested_day, site_uid, "
            "PARSE_JSON(payload_j)"
        )
        sql = (
            f"INSERT INTO {table} ({cols}) "
            f"SELECT {select_list} FROM (VALUES "
            + ", ".join(value_tuples)
            + f") AS v({alias_list})"
        )
        self._exec(sql)

    def _delete_parcels(
        self, table: str, session_id: str, owners: List[str],
    ) -> None:
        """Session-scoped DELETE for ``owners`` in ``table``.

        Split out of :meth:`_replace_parcels` in v1.42 so the empty-bundle
        fast path can reuse it. Behaviour is unchanged: when EVERY owner is
        being replaced at once we don't need a per-owner WHERE filter, but
        callers that only refresh ONE player still get correctness via the
        owner filter (legacy single-owner code path).
        """
        if len(owners) == 1:
            (only_owner,) = owners
            self._exec(
                f"DELETE FROM {table} WHERE session_id = {_quote(session_id)} "
                f"AND owner = {_quote(only_owner)}"
            )
        else:
            self._exec(
                f"DELETE FROM {table} WHERE session_id = {_quote(session_id)} "
                f"AND owner IN ({', '.join(_quote(o) for o in owners)})"
            )

    def list_hoard(self, session_id: str, owner: str) -> List[Dict[str, Any]]:
        rows = self._exec(
            "SELECT * FROM SOC_HOARD_PARCEL "
            f"WHERE session_id = {_quote(session_id)} "
            f"AND owner = {_quote(owner)} ORDER BY slot"
        )
        return [_row_to_dict(r) for r in rows]

    def list_shipped(self, session_id: str, owner: str) -> List[Dict[str, Any]]:
        rows = self._exec(
            "SELECT * FROM SOC_SHIPPED_PARCEL "
            f"WHERE session_id = {_quote(session_id)} "
            f"AND owner = {_quote(owner)} ORDER BY slot"
        )
        return [_row_to_dict(r) for r in rows]

    # ── SOC_POLICY_QUEUE ──────────────────────────────────────────
    def upsert_policy(
        self, session_id: str, day: int, player: str, queue: List[Any],
    ) -> None:
        sql = (
            "MERGE INTO SOC_POLICY_QUEUE t USING (SELECT "
            "? AS session_id, "
            "?::INT AS day, "
            "? AS player, "
            "PARSE_JSON(?) AS queue, "
            "?::INT AS move_count) s "
            "ON t.session_id = s.session_id AND t.day = s.day "
            "AND t.player = s.player "
            "WHEN MATCHED THEN UPDATE SET queue = s.queue, "
            "  move_count = s.move_count, "
            "  submitted_at = CURRENT_TIMESTAMP() "
            "WHEN NOT MATCHED THEN INSERT (session_id, day, player, "
            "  queue, move_count) VALUES "
            "  (s.session_id, s.day, s.player, s.queue, s.move_count)"
        )
        self._exec(sql, [
            str(session_id), int(day), str(player),
            _json(queue), len(queue),
        ])

    def list_policies(
        self, session_id: str, day: int
    ) -> Dict[str, List[Any]]:
        rows = self._exec(
            "SELECT player, queue FROM SOC_POLICY_QUEUE "
            f"WHERE session_id = {_quote(session_id)} AND day = {_quote(int(day))}"
        )
        out: Dict[str, List[Any]] = {}
        for r in rows:
            d = _row_to_dict(r)
            queue = d.get("queue")
            if isinstance(queue, str):
                try:
                    queue = json.loads(queue)
                except json.JSONDecodeError:
                    queue = []
            out[d["player"]] = queue or []
        return out

    # ── SOC_GAME_LOG ──────────────────────────────────────────────
    def append_log(
        self, session_id: str, day: int,
        entries: List[Mapping[str, Any]],
    ) -> None:
        if not entries:
            return
        # v1.41 — ONE statement, was two. The MAX(seq) lookup is now a
        # cross-joined scalar rather than a separate round-trip, and the
        # rows ride as a bound JSON array instead of inlined literals.
        # FLATTEN's ``index`` is 0-based, so ``base + index + 1`` numbers
        # the batch exactly as the old ``next_seq + offset`` did.
        payload = [
            {
                "level": str(e.get("level", "info")),
                "text": str(e.get("text", "")),
            }
            for e in entries
        ]
        self._exec(
            "INSERT INTO SOC_GAME_LOG (session_id, day, seq, level, text) "
            "SELECT ?, ?::INT, m.base + v.index + 1, "
            "       v.value:level::STRING, v.value:text::STRING "
            "FROM TABLE(FLATTEN(input => PARSE_JSON(?))) v, "
            "     (SELECT COALESCE(MAX(seq), 0) AS base FROM SOC_GAME_LOG "
            "      WHERE session_id = ?) m",
            [str(session_id), int(day), _json(payload), str(session_id)],
        )

    def list_log(
        self, session_id: str,
        day_from: Optional[int] = None,
        day_to: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        where = [f"session_id = {_quote(session_id)}"]
        if day_from is not None:
            where.append(f"day >= {int(day_from)}")
        if day_to is not None:
            where.append(f"day <= {int(day_to)}")
        sql = (
            "SELECT session_id, day, seq, level, text, ts FROM SOC_GAME_LOG "
            f"WHERE {' AND '.join(where)} ORDER BY seq"
        )
        if limit is not None:
            sql = (
                f"SELECT * FROM ({sql}) ORDER BY seq DESC LIMIT {int(limit)}"
            )
        rows = [_row_to_dict(r) for r in self._exec(sql)]
        if limit is not None:
            rows.reverse()
        return rows

    # ── SOC_REPLAY_FRAME ──────────────────────────────────────────
    def append_replay_frames(
        self, session_id: str, day: int, frames: List[Mapping[str, Any]],
    ) -> None:
        """Bulk-insert all frames from one night.

        v0.9.5 — pre-v0.9.5 this fired one ``INSERT`` per frame, so a
        single PRAXIS that produced ~150 frames cost ~150 Snowflake
        round-trips and dominated the user-perceived latency.

        The batched path collapses N rows into a single
        ``INSERT … SELECT … FROM (VALUES (…), (…), …)`` statement,
        with ``PARSE_JSON`` lifted into the outer SELECT so each
        VARIANT column is hydrated from a string literal in the
        VALUES row. Snowflake's documented SQL text cap is 1MB; we
        chunk at 50 frames per statement so even a frame loaded with
        12 JSON blobs (cells, cells_player_*, entities, hoard, etc.)
        stays well under that limit.

        Behaviour is otherwise identical to the per-row writer:
        ``global_idx`` is monotonically assigned starting from
        ``MAX(global_idx) + 1`` so consumers see the same ordering
        contract.
        """
        if not frames:
            return
        # E1b — idempotent per (session, day). A night's frames are written in
        # ONE batch at resolution; if the same night is resolved again (e.g. a
        # stale-read re-dispatch), drop the prior copy first so the replay can
        # never contain a duplicated day (the "day 3 repeats three times" bug).
        self._exec(
            "DELETE FROM SOC_REPLAY_FRAME "
            f"WHERE session_id = {_quote(session_id)} AND day = {int(day)}"
        )
        # The VARIANT columns we round-trip via PARSE_JSON. Keep this
        # list in the SAME order as the VALUES tuple below — the
        # SELECT references them by positional alias.
        json_keys = (
            "cells",
            "cells_player_p1",
            "cells_player_p2",
            "entities",
            "hoard",
            "collisions",
            "crushed_probes",
            "scheduled_orders",
            # v0.9 interdiction-weapon FX payloads.
            "emp",
            "mine",
            "chaff",
            "emp_clouds",
            # v0.9.4 — persistent mine snapshot per frame.
            "mines_active",
            # v0.9.11 — p3/p4 percepts. Appended at the END so the
            # ``json_keys[:8]`` / ``json_keys[8:]`` split below (which
            # straddles the attempted/outcome/hour scalar columns) is
            # unaffected; these ride in the ``[8:]`` trailing group.
            "cells_player_p3",
            "cells_player_p4",
            # v1.53 — SNAP's FX payload and its scorch-mark snapshot.
            # Missing since the weapon shipped in v1.36: the frame was
            # written with its tag and caption and the two VARIANTs were
            # dropped, so a Snowflake season knew a SNAP had been fired
            # and had nothing to animate it with. Appended at the END for
            # the same reason p3/p4 were.
            "snap",
            "snap_clouds",
        )
        cols = (
            "session_id, day, frame_idx, global_idx, caption, tag, owner, "
            "cells, cells_player_p1, cells_player_p2, entities, hoard, "
            "collisions, crushed_probes, scheduled_orders, attempted, "
            "outcome, hour, emp, mine, chaff, emp_clouds, mines_active, "
            "cells_player_p3, cells_player_p4, snap, snap_clouds"
        )
        # v1.41 — ONE BOUND JSON ARRAY PER CHUNK, flattened server-side.
        #
        # This was the most expensive statement in the app. The old shape
        # pasted every frame's JSON in as a quoted literal inside a VALUES
        # list: 1.17MB of SQL text per 50 frames, of which ~1400ms of the
        # statement's ~3.2s was Snowflake merely PARSING the text. The SQL
        # is now ~1KB and byte-identical every time, so it compiles in tens
        # of ms and the plan caches across nights.
        #
        # No PARSE_JSON per column any more either: once the array is
        # parsed, ``v.value:cells`` is ALREADY a VARIANT. Only the scalars
        # need a cast, and ``hour`` still yields SQL NULL when the frame
        # predates the planetary night clock (JSON null casts to NULL),
        # which preserves the per-row writer's original contract.
        _SCALARS = {
            "session_id": "STRING", "day": "INT", "frame_idx": "INT",
            "caption": "STRING", "tag": "STRING", "owner": "STRING",
            "attempted": "STRING", "outcome": "STRING", "hour": "INT",
        }
        col_names = [c.strip() for c in cols.split(",") if c.strip()]
        select_list = ", ".join(
            # ``global_idx`` is assigned SERVER-SIDE off the running max
            # rather than read back first — one whole round-trip saved per
            # night, and at ~270ms of protocol floor per statement
            # (measured) round-trips are now the dominant cost.
            #
            # Chunks need no offset: each chunk is its own statement, so
            # it recomputes MAX over the rows the previous chunk already
            # committed. The subquery reads a pre-insert snapshot, so the
            # max cannot shift underneath the rows being written.
            "m.base + v.index" if c == "global_idx"
            else (f"v.value:{c}::{_SCALARS[c]}" if c in _SCALARS
                  else f"v.value:{c}")
            for c in col_names
        )
        sql = (
            f"INSERT INTO SOC_REPLAY_FRAME ({cols}) SELECT {select_list} "
            "FROM TABLE(FLATTEN(input => PARSE_JSON(?))) v, "
            "     (SELECT COALESCE(MAX(global_idx), -1) + 1 AS base "
            "      FROM SOC_REPLAY_FRAME WHERE session_id = ?) m"
        )

        # Build rows lazily so the chunker can slice arbitrary windows
        # without re-walking the source. Each row carries (frame, the
        # global_idx assigned to it).
        indexed = list(enumerate(frames))

        # 100 rows/statement. The bind is a payload, not SQL text, so the
        # old 1MB text cap no longer binds the chunk size — this is now
        # only about keeping one request a sane size (~2MB at the heaviest
        # frames) and halving the statement count while we are here.
        for chunk in _chunks(indexed, 100):
            payload: List[Dict[str, Any]] = []
            for offset, frame in chunk:
                hour_val = frame.get("hour")
                rec: Dict[str, Any] = {
                    "session_id": session_id,
                    "day": int(day),
                    "frame_idx": int(frame.get("frame_idx", offset)),
                    "caption": frame.get("caption"),
                    "tag": frame.get("tag"),
                    "owner": frame.get("owner"),
                    "attempted": frame.get("attempted"),
                    "outcome": frame.get("outcome"),
                    "hour": hour_val if isinstance(hour_val, int) else None,
                }
                for k in json_keys:
                    rec[k] = frame.get(k)
                payload.append(rec)
            # Bind order follows the order the ``?`` marks appear in the
            # text: the payload, then the session id in the max lookup.
            self._exec(sql, [_json(payload), str(session_id)])

    def list_replay_frames(
        self, session_id: str,
        day_from: Optional[int] = None,
        day_to: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        where = [f"session_id = {_quote(session_id)}"]
        if day_from is not None:
            where.append(f"day >= {int(day_from)}")
        if day_to is not None:
            where.append(f"day <= {int(day_to)}")
        rows = self._exec(
            "SELECT * FROM SOC_REPLAY_FRAME "
            f"WHERE {' AND '.join(where)} ORDER BY global_idx"
        )
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = _row_to_dict(r)
            # VARIANT columns come back as JSON strings from the driver;
            # parse them so callers (and the watcher viewer-toggle) can
            # consume them as Python structures directly.
            for col in (
                "cells",
                "cells_player_p1",
                "cells_player_p2",
                "entities",
                "hoard",
                "collisions",
                "crushed_probes",
                "scheduled_orders",
                # v0.9 — interdiction-weapon FX arrays. Same VARIANT
                # JSON-string treatment as the v0.7 FX columns above.
                "emp",
                "mine",
                "chaff",
                "emp_clouds",
                # v0.9.4 — per-frame mine snapshot.
                "mines_active",
                # v0.9.11 — p3/p4 percepts (N-seat OBS replay).
                "cells_player_p3",
                "cells_player_p4",
                # v1.53 — SNAP. See the write path.
                "snap",
                "snap_clouds",
            ):
                v = d.get(col)
                if isinstance(v, str):
                    try:
                        d[col] = json.loads(v)
                    except json.JSONDecodeError:
                        pass
            # Back-compat alias: the playing UI (app.js) still reads
            # `frame.cells_player`. Surface p1's snapshot under that key
            # until the Phase-5 viewer toggle replaces it.
            if d.get("cells_player") is None and d.get("cells_player_p1") is not None:
                d["cells_player"] = d["cells_player_p1"]
            # v0.9.11 — rebuild ``cells_by_seat`` from the per-seat
            # columns so the OBS view (which reads cells_by_seat first)
            # works on Snowflake-persisted replays, not just the
            # in-memory store that keeps the full frame dict.
            cbs = {}
            for seat in ("p1", "p2", "p3", "p4"):
                seat_cells = d.get(f"cells_player_{seat}")
                if seat_cells is not None:
                    cbs[seat] = seat_cells
            if cbs and d.get("cells_by_seat") is None:
                d["cells_by_seat"] = cbs
            out.append(d)
        return out

    def day_index(self, session_id: str) -> List[Dict[str, Any]]:
        rows = self._exec(
            "SELECT day, frame_count, first_global_idx, last_global_idx, "
            "       started_at, ended_at, first_caption, last_caption "
            "FROM SOC_DAY_INDEX "
            f"WHERE session_id = {_quote(session_id)} ORDER BY day"
        )
        return [_row_to_dict(r) for r in rows]

    # ── SOC_AGENT_INVOCATION ──────────────────────────────────────
    def append_agent_invocation(self, row: Mapping[str, Any]) -> None:
        self.append_agent_invocations([row])

    def append_agent_invocations(
        self, rows: Sequence[Mapping[str, Any]],
    ) -> None:
        """Batched agent-invocation insert (v1.5, P3).

        One ``MAX(seq)`` lookup + one multi-row ``INSERT`` for the whole
        fan-out instead of a SELECT+INSERT per bot. A 4-seat game (3 bots)
        drops from ~6 Snowflake round-trips to 2 per TRANSMIT. ``PARSE_JSON``
        is applied in the outer SELECT so each row's ``tool_calls`` still lands
        as a VARIANT. Assumes all rows share one ``session_id`` (they do — the
        fan-out is per session).
        """
        rows = [r for r in rows if r]
        if not rows:
            return
        sid = str(rows[0]["session_id"])
        # v1.41 — one bound statement, was a MAX(seq) SELECT plus an
        # INSERT of inlined literals. ``prompt_excerpt`` / ``response_text``
        # can be several KB apiece, so this one also stops shipping whole
        # LLM transcripts through the SQL parser.
        payload = [
            {
                "day": int(r["day"]),
                "agent_id": str(r["agent_id"]),
                "player": str(r["player"]),
                "prompt_excerpt": r.get("prompt_excerpt"),
                "tool_calls": r.get("tool_calls") or [],
                "rationale": r.get("rationale"),
                "response_text": r.get("response_text"),
                "ms_elapsed": r.get("ms_elapsed"),
                "status": r.get("status") or "ok",
            }
            for r in rows
        ]
        self._exec(
            "INSERT INTO SOC_AGENT_INVOCATION (session_id, day, seq, agent_id, "
            " player, prompt_excerpt, tool_calls, rationale, response_text, "
            " ms_elapsed, status) "
            "SELECT ?, v.value:day::INT, m.base + v.index + 1, "
            "       v.value:agent_id::STRING, v.value:player::STRING, "
            "       v.value:prompt_excerpt::STRING, v.value:tool_calls, "
            "       v.value:rationale::STRING, v.value:response_text::STRING, "
            "       v.value:ms_elapsed::INT, v.value:status::STRING "
            "FROM TABLE(FLATTEN(input => PARSE_JSON(?))) v, "
            "     (SELECT COALESCE(MAX(seq), 0) AS base "
            "      FROM SOC_AGENT_INVOCATION WHERE session_id = ?) m",
            [sid, _json(payload), sid],
        )

    def list_agent_invocations(
        self, session_id: str, day: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        where = [f"session_id = {_quote(session_id)}"]
        if day is not None:
            where.append(f"day = {int(day)}")
        rows = self._exec(
            "SELECT * FROM SOC_AGENT_INVOCATION "
            f"WHERE {' AND '.join(where)} ORDER BY seq"
        )
        return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: Any) -> Dict[str, Any]:
    """Best-effort row -> dict that works with Snowpark Row or tuples."""
    if hasattr(row, "as_dict"):
        return {k.lower(): v for k, v in row.as_dict().items()}
    if hasattr(row, "_asdict"):
        return {k.lower(): v for k, v in row._asdict().items()}
    if isinstance(row, Mapping):
        return {k.lower(): v for k, v in row.items()}
    return {f"col{i}": v for i, v in enumerate(row)}


def _canon_parcel(row: Mapping[str, Any]) -> Mapping[str, Any]:
    """Unwrap a parcel row's VARIANT ``payload`` into the scorer's dict.

    The payload is the same snapshot the live session carries, including
    the catapult-stamped ``effective_purity`` / ``score_tier``, so
    scoring off it gives the identical answer to scoring the in-memory
    game. Snowflake hands a VARIANT back as JSON text. The flat
    ``origin_*`` columns are the fallback for any row written before the
    payload column existed — ``compute_player_score`` and
    ``_parcel_purity`` both accept that shape too, just without the
    stamped tier.
    """
    payload = row.get("payload")
    if isinstance(payload, str) and payload.strip():
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = None
    return payload if isinstance(payload, dict) and payload else row


def _chunks(items: List[Any], n: int) -> Iterable[List[Any]]:
    for i in range(0, len(items), n):
        yield items[i : i + n]
