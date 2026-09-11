"""Fluent builder DSL for constructing eval-scenario game states.

A :class:`WorldBuilder` wraps the standard
:func:`sea_of_colours.snowpark.engine.init_session` flow but exposes
chainable mutators for everything a scenario fixture typically wants to
control: day, scores, harvester position/state, probes (yours and the
opponent's), per-cell RED purity overrides, fog-of-war reveals, and
last-night recap injection.

Constructor never touches Snowflake — it always builds against a fresh
:class:`~sea_of_colours.snowpark.store.InMemorySocStore`. Live Cortex
runs of an eval need ``SOC_BACKEND=snowflake`` and use the constructed
fixture's ``session_id`` against that store; cross-store transfer is
out of scope for v1 (the heuristic backend covers every scenario by
construction, and prompt-iteration cycles run against it).

Why a builder and not a JSON fixture format:

* Each scenario is a few lines of Python — no parser, no schema drift.
* IDE refactors propagate across all scenarios automatically when we
  rename a field (e.g. ``Entity.damaged``).
* We can mutate the *real* :class:`~sea_of_colours.game.session.GameSession`
  via its public surface (or, for state the engine doesn't expose
  ergonomically, direct field assignment with a comment explaining why).
* Type-checked end-to-end: ``mypy`` catches "you set ``cargo`` on an
  Entity but Entity has no ``cargo`` field".

The builder returns ``(store, session_id)`` and the constructed
session is fully persisted before return — the next call into
``run_agent_turn`` rehydrates from the store exactly like a real turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

from sea_of_colours.game.asset_ledger import AssetRecord
from sea_of_colours.game.entities import Entity
from sea_of_colours.game.session import GameSession, Phase
from sea_of_colours.generator import Cell, Tile
from sea_of_colours.snowpark import engine as soc_engine
from sea_of_colours.snowpark.store import InMemorySocStore, SocStore


def _default_store() -> SocStore:
    """Pick the WorldBuilder default store based on ``SOC_BACKEND``.

    * ``SOC_BACKEND=memory`` (or unset in test contexts) → InMemorySocStore.
      Isolated, fast, no external deps — the historical fixture behaviour.
    * ``SOC_BACKEND=snowflake`` → route through :func:`backend.get_store`.
      Every scenario built while the env var is set persists to Snowflake
      (``SOC_GAME_SESSION`` / ``SOC_GAME_LOG`` / ``SOC_REPLAY_FRAME`` /
      ``SOC_AGENT_MEMORY``), so multi-night runs are replayable in the
      frontend.
    * If the Snowflake path fails (credentials missing, connector import
      error, …) we fall back to :class:`InMemorySocStore` so unit tests
      run under the env var never crash in CI. The fallback is logged
      via ``print`` on stderr so the caller can tell persistence didn't
      happen.
    """
    from sea_of_colours.snowpark.backend import SOC_BACKEND, get_store
    if SOC_BACKEND != "snowflake":
        return InMemorySocStore()
    try:
        return get_store()
    except Exception as exc:  # pragma: no cover — defensive
        import sys
        print(
            f"[WorldBuilder] SOC_BACKEND=snowflake requested but "
            f"get_store() failed ({type(exc).__name__}: {exc}); "
            f"falling back to InMemorySocStore",
            file=sys.stderr,
        )
        return InMemorySocStore()


PlayerId = str  # "p1" | "p2"
XY = Tuple[int, int]


def _ensure_unit(sess: GameSession, unit_id: str, kind: str, owner: PlayerId) -> Entity:
    """Return the existing entity, or mint a fresh one if not present.

    Used by builder mutators that may be called before
    :meth:`~sea_of_colours.game.session.GameSession._spawn_defaults`
    has been re-run for a multi-harvester scenario, or by tests that
    need a probe that wasn't part of the default roster.

    Also seeds an :class:`AssetRecord` when the unit is new — the
    session's ``_spawn_defaults`` only registers records for the
    default roster (harvester_{seat} + orblift_{seat}), so scenarios
    that add extra units (e.g. ``harvester_p1_2``) would otherwise be
    invisible to the view's ``my_assets`` block. That in turn causes
    the Cortex agent to strip moves for the unit even when the
    harness surfaces valid chains for it.
    """
    if unit_id in sess.entities:
        return sess.entities[unit_id]
    ent = Entity(unit_id, kind, owner, None, None)
    sess.entities[unit_id] = ent
    if unit_id not in sess.asset_records:
        sess.asset_records[unit_id] = AssetRecord(
            asset_id=unit_id,
            asset_type=kind,
            owner=owner,
            session_id=sess.session_id,
            created_on_day=sess.day,
            first_deployed_day=None,
            last_seen_x=None,
            last_seen_y=None,
        )
    return ent


@dataclass
class WorldBuilder:
    """Fluent constructor for eval fixtures.

    Usage::

        store, sid = (WorldBuilder(seed=42, day=4, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .reveal_red(cells=[(12, 8, 255), (13, 8, 240)])
            .place_enemy_harvester("harvester_p2", at=(12, 8))
            .build())

    Every mutator returns ``self`` so calls chain naturally. Mutators
    are commutative wherever the underlying engine state allows — i.e.
    placing the harvester before or after revealing cells produces the
    same final state — except where explicitly noted.
    """

    seed: int = 42
    width: int = 40
    height: int = 28
    day: int = 1
    season_day_cap: int = 7
    season_name: Optional[str] = None
    # Seat list. ``None`` keeps the engine default (p1/p2), which is what
    # almost every fixture wants. The redsign battles need it because
    # "three opponents already on your seam" is a different game from
    # "one opponent", and the engine scales real things off seat count —
    # the pure-RED floor among them (v1.28).
    players: Optional[Sequence[str]] = None
    store: SocStore = field(default_factory=_default_store)

    # Internal session reference — populated on first mutator call.
    _sess: Optional[GameSession] = field(default=None, init=False, repr=False)
    _session_id: Optional[str] = field(default=None, init=False, repr=False)

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------
    def _ensure_session(self) -> GameSession:
        """Create the underlying session on first mutator call.

        Lazy so the constructor stays cheap and scenarios that only
        introspect the builder (e.g. for ``--list`` in the CLI) don't
        burn CPU generating a grid they'll discard.
        """
        if self._sess is not None:
            return self._sess
        info = soc_engine.init_session(
            self.store,
            seed=self.seed,
            width=self.width,
            height=self.height,
            season_name=self.season_name,
            season_day_cap=self.season_day_cap,
            players=list(self.players) if self.players else None,
        )
        sid = info["session_id"]
        self._session_id = sid
        # Hydrate the persisted session so we can mutate freely and
        # round-trip back through save_session_full. We deliberately
        # work on a hydrated copy rather than the in-process instance
        # ``init_session`` constructed so the eval fixture exercises the
        # same serialisation path as a live turn.
        sess = soc_engine._hydrate_session(self.store, sid)
        # Advance the day counter to the configured target. We DON'T
        # run nights to get there — scenarios start with a fresh
        # planning phase on the chosen day; any "previous activity"
        # they want to imply (last_night recap, fresh trails, enemy
        # echoes) is staged explicitly by the relevant mutator.
        sess.day = int(self.day)
        sess.phase = Phase.PLANNING
        self._sess = sess
        return sess

    # ------------------------------------------------------------------
    # Asset placement
    # ------------------------------------------------------------------
    def place_harvester(
        self,
        unit_id: str = "harvester_p1",
        *,
        at: Optional[XY] = None,
        state: str = "orbit",
        cargo: int = 0,
        damaged: bool = False,
        owner: Optional[PlayerId] = None,
    ) -> "WorldBuilder":
        """Position one of *your* harvesters.

        ``state`` accepts:
        * ``"orbit"``   → ``(x, y) = (None, None)``, cargo cleared
        * ``"surface"`` → ``(x, y) = at`` (required)
        """
        sess = self._ensure_session()
        owner_id = owner or ("p2" if unit_id.endswith("_p2") else "p1")
        ent = _ensure_unit(sess, unit_id, "harvester", owner_id)
        if state == "orbit":
            ent.x = None
            ent.y = None
            ent.cargo_squares = []
            ent.carrying_red = False
        elif state == "surface":
            if at is None:
                raise ValueError("place_harvester(state='surface') requires at=(x,y)")
            ent.x = int(at[0])
            ent.y = int(at[1])
            ent.cargo_squares = [
                {"site_id": f"stub-{i}", "origin_purity": 0} for i in range(int(cargo))
            ]
            ent.carrying_red = cargo > 0
        else:
            raise ValueError(f"unknown harvester state: {state!r}")
        ent.damaged = bool(damaged)
        return self

    def place_enemy_harvester(
        self,
        unit_id: str = "harvester_p2",
        *,
        at: Optional[XY] = None,
        state: str = "surface",
        damaged: bool = False,
    ) -> "WorldBuilder":
        """Place the *opponent's* harvester.

        The default state is ``"surface"`` because that's the only state
        an enemy harvester is observable to you in (orbital drops are
        private per §0.4 magnetic cover). Use ``state="orbit"`` for
        completeness if you want the enemy explicitly idle.
        """
        return self.place_harvester(
            unit_id,
            at=at,
            state=state,
            damaged=damaged,
            owner="p2",
        )

    def place_probe(
        self,
        unit_id: Optional[str] = None,
        *,
        at: XY,
        owner: PlayerId = "p1",
    ) -> "WorldBuilder":
        """Drop one of *your* probes onto ``at``.

        ``unit_id`` defaults to a monotonic ``probe_{owner}_{n}`` id
        consistent with how ``GameSession.deploy_probe`` allocates
        names. If you pass an explicit id the helper trusts you.
        """
        sess = self._ensure_session()
        if unit_id is None:
            sess.probe_seq[owner] = sess.probe_seq.get(owner, 0) + 1
            unit_id = f"probe_{owner}_{sess.probe_seq[owner]}"
        ent = _ensure_unit(sess, unit_id, "probe", owner)
        ent.x = int(at[0])
        ent.y = int(at[1])
        return self

    def place_enemy_probe(self, *, at: XY, unit_id: Optional[str] = None) -> "WorldBuilder":
        """Drop the opponent's probe onto ``at`` (visible to you per §3.15)."""
        return self.place_probe(unit_id, at=at, owner="p2")

    def grant_live_vision(
        self,
        cells: Sequence[XY],
        *,
        player: PlayerId = "p1",
    ) -> "WorldBuilder":
        """Place probes that grant *live* LOS over every cell in ``cells``.

        The agent's ``navigation.best_red_visible`` and the dense world
        view only include tiles that a deployed unit can *currently*
        see — :func:`sea_of_colours.snowpark.view.build_agent_view`
        derives them from :meth:`GameSession.tiles_visible_now`. So
        writing entries into ``memory_tiles`` (echo) is not enough to
        make a scenario's RED cluster show up as "in LOS"; we have to
        anchor a real probe whose Euclidean radius-2 disk covers the
        target.

        This helper greedily places one probe per uncovered cell until
        every target is inside *some* probe's disk. Probes are placed
        ON the target cell (or as close as possible) so the disk
        centres on the seam — a more clever cover would minimise the
        count, but cluster sizes in our scenarios are small enough that
        a greedy strategy uses at most 1-3 probes per region.
        """
        from sea_of_colours.game.session import PROBE_VISION_RADIUS

        sess = self._ensure_session()
        targets: List[XY] = [(int(x), int(y)) for x, y in cells]
        covered: set = set()
        # Anything already covered by existing probes counts.
        r2 = PROBE_VISION_RADIUS * PROBE_VISION_RADIUS
        for ent in sess.entities.values():
            if ent.entity_type != "probe" or ent.owner != player:
                continue
            if ent.x is None or ent.y is None:
                continue
            for x, y in targets:
                if (x - ent.x) ** 2 + (y - ent.y) ** 2 <= r2:
                    covered.add((x, y))
        for tx, ty in targets:
            if (tx, ty) in covered:
                continue
            self.place_probe(at=(tx, ty), owner=player)
            for x, y in targets:
                if (x - tx) ** 2 + (y - ty) ** 2 <= r2:
                    covered.add((x, y))
        return self

    # ------------------------------------------------------------------
    # Grid / RED purity overrides
    # ------------------------------------------------------------------
    def reveal_red(self, cells: Sequence[Tuple[int, int, int]]) -> "WorldBuilder":
        """Force specific cells to be ``Tile.RED`` with the given purity.

        ``cells`` is an iterable of ``(x, y, purity)`` triples. Purity
        is clamped to ``0..255``. Use this to construct a known seam
        without depending on the noise generator's seed-specific
        layout.

        After the override we also re-snapshot the square ledger entry
        for each touched cell so :class:`SquareLedger` agrees with the
        grid — otherwise scoring helpers reading the ledger see the
        original noise-generated tile.
        """
        sess = self._ensure_session()
        for x, y, purity in cells:
            if not (0 <= x < sess.width and 0 <= y < sess.height):
                raise ValueError(f"reveal_red cell ({x},{y}) out of bounds")
            p = max(0, min(255, int(purity)))
            sess.grid[y][x] = Cell(tile=Tile.RED, purity=p)
            if sess.ledger is not None:
                # Refresh the ledger row in place so downstream views
                # (which read from the ledger) report the override.
                # SquareLedger keys natural entries by "x:y" → dict.
                key = f"{x}:{y}"
                row = sess.ledger.entries.get(key)
                if row is not None:
                    row["tile_at_generation"] = int(Tile.RED)
                    row["purity_at_generation"] = p
        return self

    def reveal_empty(self, cells: Sequence[XY]) -> "WorldBuilder":
        """Force specific cells to ``Tile.EMPTY`` (purity 0)."""
        sess = self._ensure_session()
        for x, y in cells:
            sess.grid[y][x] = Cell(tile=Tile.EMPTY, purity=0)
            if sess.ledger is not None:
                key = f"{x}:{y}"
                row = sess.ledger.entries.get(key)
                if row is not None:
                    row["tile_at_generation"] = int(Tile.EMPTY)
                    row["purity_at_generation"] = 0
        return self

    def reveal_green_synthetic(
        self,
        cells: Sequence[XY],
        *,
        owner: PlayerId = "p1",
    ) -> "WorldBuilder":
        """Mark cells as ``Tile.GREEN`` (purity 255) as if RED was harvested.

        These cells should now read as "synthetic green" — banking them
        scores ZERO. To stay consistent with how the live engine
        produces synth-green (a fresh row in
        :attr:`SquareLedger.synthetic_rows` with ``lineage='synthetic'``)
        we call :meth:`~sea_of_colours.game.ledger.SquareLedger.mint_synthetic_green`.
        Without that, the world-board renderer (which inspects
        ``ledger.record(x,y).lineage``) would mis-render the cell as
        natural green (``Gn``) instead of synthetic (``gn``).

        ``owner`` is the seat credited with the prior harvest. Defaults
        to ``p1`` because that's the harness we evaluate; pass ``p2``
        for "enemy already harvested this seam" fixtures (used by
        :func:`enemy_trail_in_seam`).
        """
        sess = self._ensure_session()
        for x, y in cells:
            sess.grid[y][x] = Cell(tile=Tile.GREEN, purity=255)
            sess.track_harvests.setdefault(owner, set()).add(f"{x}:{y}")
            if sess.ledger is not None:
                key = f"{x}:{y}"
                parent_sid = sess.ledger.lookup(x, y) or f"natural-{x}-{y}"
                sess.ledger.mint_synthetic_green(
                    x=x,
                    y=y,
                    day=max(1, int(sess.day) - 1),
                    harvester_id=f"harvester_{owner}",
                    owner=owner,
                    parent_square_id=parent_sid,
                )
                # Keep the natural row's tile in sync so legacy callers
                # that read ``entries[k].tile_at_generation`` see GREEN
                # not the original RED.
                row = sess.ledger.entries.get(key)
                if row is not None:
                    row["tile_at_generation"] = int(Tile.GREEN)
                    row["purity_at_generation"] = 255
        return self

    # ------------------------------------------------------------------
    # Vision / fog
    # ------------------------------------------------------------------
    def clear_visibility(self, player: PlayerId = "p1") -> "WorldBuilder":
        """Wipe ``player``'s memory — every cell becomes fog."""
        sess = self._ensure_session()
        sess.memory_tiles[player] = {}
        sess.probe_intel[player] = {}
        return self

    def reveal_to_player(
        self,
        cells: Iterable[XY],
        *,
        player: PlayerId = "p1",
        stale: bool = False,
    ) -> "WorldBuilder":
        """Add cells to ``player``'s remembered map.

        ``stale=False`` means the cells were observed *this turn* and
        will appear as live LOS in the agent's view. ``stale=True``
        marks them as echo (seen before, may be stale).
        """
        sess = self._ensure_session()
        from sea_of_colours.game.session import cell_to_paint  # local to avoid cycle

        mem = sess.memory_tiles.setdefault(player, {})
        for x, y in cells:
            cell = sess.grid[y][x]
            mem[f"{x}:{y}"] = {
                "paint": dict(cell_to_paint(cell)),
                "stale": bool(stale),
            }
        return self

    # ------------------------------------------------------------------
    # Scores / hoard
    # ------------------------------------------------------------------
    def set_score(self, *, p1: int = 0, p2: int = 0) -> "WorldBuilder":
        """Seed the hoard with synthetic RED parcels summing to the requested score.

        We bank ``score // 255`` cells at purity 255 plus one residue
        parcel at ``score % 255``. The agent view's ``hud.score`` is
        derived from purities on banked parcels, so this is the
        minimal-surgery way to express "you start the night with N
        score banked".

        Parcels carry ``origin_tile`` as the :class:`~sea_of_colours.generator.Tile`
        int value — that matches the format the live ``hoard_squares``
        list uses and what ``_vault_tier_breakdown`` expects.
        """
        sess = self._ensure_session()
        for owner, target in (("p1", int(p1)), ("p2", int(p2))):
            parcels: List[dict] = []
            remaining = max(0, target)
            i = 0
            while remaining > 0:
                chunk = min(255, remaining)
                parcels.append(
                    {
                        "site_id": f"synth-{owner}-{i}",
                        "origin_tile": int(Tile.RED),
                        "origin_purity": chunk,
                    }
                )
                remaining -= chunk
                i += 1
            sess.hoard_squares[owner] = parcels
        return self

    def fill_hoard(
        self,
        *,
        player: PlayerId = "p1",
        red: int = 0,
        green: int = 0,
        blue: int = 0,
    ) -> "WorldBuilder":
        """Stuff the hoard with mixed-tier parcels (vault-pressure scenarios).

        v1.9 — write BOTH the ``origin_*`` and ``*_at_harvest`` key sets.
        The engine treats them as distinct fields (origin = the seam
        record, at_harvest = what the parcel actually carried), and
        different downstream readers key off different names. In
        particular, ``sea_of_colours/snowpark/view.py`` renders
        ``orbit.hoard_parcels`` by reading ``tile_at_harvest`` /
        ``purity_at_harvest`` — writing only the ``origin_*`` names left
        RED parcels displayed as ``colour="EMPTY"``, which then made
        :func:`_ship_candidates` in the pilot_v4 orbit compiler skip
        them (no ``colour=="RED"`` matches) and produce no ship option
        even for a full hoard.
        """
        sess = self._ensure_session()
        parcels: List[dict] = []
        for i in range(red):
            parcels.append({
                "site_id": f"hoard-r-{i}",
                "origin_tile": int(Tile.RED),
                "origin_purity": 200,
                "tile_at_harvest": int(Tile.RED),
                "purity_at_harvest": 200,
            })
        for i in range(green):
            parcels.append({
                "site_id": f"hoard-g-{i}",
                "origin_tile": int(Tile.GREEN),
                "origin_purity": 255,
                "tile_at_harvest": int(Tile.GREEN),
                "purity_at_harvest": 255,
            })
        for i in range(blue):
            parcels.append({
                "site_id": f"hoard-b-{i}",
                "origin_tile": int(Tile.BLUE),
                "origin_purity": 100,
                "tile_at_harvest": int(Tile.BLUE),
                "purity_at_harvest": 100,
            })
        sess.hoard_squares[player] = parcels
        return self

    def give_weapon_stock(
        self,
        *,
        player: PlayerId = "p1",
        emp: int = 0,
        chaff: int = 0,
        snap: int = 0,
    ) -> "WorldBuilder":
        """Pre-arm the seat's weapon stockpile.

        Each weapon would normally cost BLUE + credits to build during
        the orbit phase. For night-phase eval scenarios that need the
        agent to *use* a weapon, we skip the build cost and stamp the
        stockpile directly.

        ``snap`` was missing here, in ``Loadout`` and in ``stage.py``, which
        meant no lab board could arm a snap at all: a snap agent ran every rung
        with an empty rack and its play could never be observed. The engine has
        always supported the verb — it was only the fixtures that could not
        express it.
        """
        sess = self._ensure_session()
        slot = sess.weapon_stock.setdefault(
            player, {"emp": 0, "chaff": 0, "snap": 0}
        )
        slot["emp"] = int(emp)
        slot["chaff"] = int(chaff)
        slot["snap"] = int(snap)
        return self

    def give_blue_purity(
        self,
        *,
        player: PlayerId = "p1",
        purity_total: int,
    ) -> "WorldBuilder":
        """Stuff the hoard with BLUE parcels summing to the requested purity.

        BLUE is the weapon-build fuel (200 purity per EMP, etc.). This
        gives a quick way to set the seat's effective BLUE balance for
        scenarios that test affordability without exercising the full
        orbit phase.
        """
        sess = self._ensure_session()
        parcels = list(sess.hoard_squares.get(player) or [])
        remaining = max(0, int(purity_total))
        i = len(parcels)
        while remaining > 0:
            chunk = min(255, remaining)
            parcels.append({
                "site_id": f"blue-{player}-{i}",
                "origin_tile": int(Tile.BLUE),
                "origin_purity": chunk,
                # v1.9 — mirror the *_at_harvest keys so downstream
                # readers (view.py, ship compiler) count these parcels
                # under blue_purity_total instead of dropping them as
                # ``colour=EMPTY``. Same shape fix as fill_hoard.
                "tile_at_harvest": int(Tile.BLUE),
                "purity_at_harvest": chunk,
            })
            remaining -= chunk
            i += 1
        sess.hoard_squares[player] = parcels
        return self

    # ------------------------------------------------------------------
    # Opponent footprint (visible from your side)
    # ------------------------------------------------------------------
    def enemy_probe_launched(
        self,
        *,
        at: XY,
        day: Optional[int] = None,
    ) -> "WorldBuilder":
        """Stage an enemy probe-launch that the agent will see this turn.

        Per §3.15 (magnetic cover): every probe launch is publicly
        observable. We reuse the session's own
        :meth:`_probe_tile_snapshot` so the echo record matches the
        shape the live engine writes through
        :meth:`_record_enemy_probe_echo`. Skipping this helper would
        produce malformed echoes that crash :meth:`player_dense_view`
        on the next read.
        """
        sess = self._ensure_session()
        from sea_of_colours.game.session import cell_to_paint

        x, y = int(at[0]), int(at[1])
        d = int(day) if day is not None else int(sess.day)
        key = f"{x}:{y}"
        cell = sess.grid[y][x]
        # Memory tile so the cell is "seen" at all (stale echo)
        sess.memory_tiles.setdefault("p1", {})[key] = {
            "paint": dict(cell_to_paint(cell)),
            "stale": True,
        }
        # Probe-intel snapshot — shape matches _probe_tile_snapshot so
        # downstream renderers (player_dense_view, build_agent_view)
        # find every key they expect.
        snap = sess._probe_tile_snapshot(x, y)
        snap["day_seen"] = d
        snap["via"] = "probe_launch"
        snap["launched_by"] = "p2"
        snap["probe_id"] = f"probe_p2_launch_{d}_{x}_{y}"
        sess.probe_intel.setdefault("p1", {})[key] = snap
        return self

    def enemy_trail(
        self,
        cells: Sequence[XY],
        *,
        day: Optional[int] = None,
        unit_id: str = "harvester_p2",
    ) -> "WorldBuilder":
        """Stamp a fresh enemy harvester trail through ``cells``.

        Trails are universal (§3.7), so the agent sees them regardless
        of fog-of-war as soon as it has LOS on the cell. We bump the
        per-cell counter in ``track_paths['p2']`` and timestamp the
        last visit to ``day`` (defaults to ``sess.day - 1`` so the
        trail reads as "fresh, walked yesterday").
        """
        sess = self._ensure_session()
        d = int(day) if day is not None else max(1, int(sess.day) - 1)
        bucket = sess.track_paths.setdefault("p2", {})
        for x, y in cells:
            key = f"{x}:{y}"
            row = bucket.get(key, {"n": 0, "d": None, "h": None})
            row["n"] = int(row.get("n", 0)) + 1
            row["d"] = d
            row["h"] = unit_id
            bucket[key] = row
        return self

    # ------------------------------------------------------------------
    # Phase / economy / rival telemetry (for pilot-comprehension scenarios)
    # ------------------------------------------------------------------
    def with_redsign(
        self,
        *,
        center: XY,
        cells: Sequence[Tuple[int, int, float]],
        minted_on_day: Optional[int] = None,
        minted_by: str = "p2",
        hour: int = 0,
    ) -> "WorldBuilder":
        """Inject a public REDSIGN beacon (§4.11) at ``center`` covering
        ``cells`` (each ``(x, y, intensity)``).

        Matches the entry shape produced by
        :meth:`GameSession._mint_redsign_region` so downstream views and
        the arsenal tracker read it the same as an engine-minted seam.
        ``minted_by`` is metadata for the assertion layer — the engine
        itself doesn't store attribution on the region record, but
        scenarios that key off "who saw pure last night" (e.g.
        ``redsign_seen_last_nox``) need it.
        """
        sess = self._ensure_session()
        d = int(minted_on_day) if minted_on_day is not None else int(sess.day)
        idx = len(sess.redsign or [])
        cx = float(center[0])
        cy = float(center[1])
        clist: List[List[float]] = []
        for cell in cells:
            if len(cell) < 3:
                continue
            cx_i, cy_i, inten = int(cell[0]), int(cell[1]), float(cell[2])
            clist.append([cx_i, cy_i, round(max(0.0, min(1.0, inten)), 3)])
            # Mark the raw pure cell as attributed so the engine won't
            # re-mint a duplicate region over it on the next dawn.
            sess.redsign_seen.add(f"{cx_i},{cy_i}")
        region = {
            "id": f"redsign-{idx}",
            "center": [round(cx, 2), round(cy, 2)],
            "cells": clist,
            "day": d,
            "hour": int(hour),
            # Non-engine metadata: attribution for coherence assertions.
            # The view drops unknown keys via ``dict(r)``, so this is
            # safe to persist and re-read.
            "minted_by": str(minted_by),
        }
        sess.redsign.append(region)
        return self

    def set_phase(self, phase: str) -> "WorldBuilder":
        """Set the session phase — ``"orbit"`` or ``"planning"``.

        Scenarios default to :class:`Phase.PLANNING` (Nox); orbit-phase
        comprehension tests (fleet rebuild, ship-vs-park) need to see
        the orbit action menu, which is gated on ``sess.phase``.
        """
        sess = self._ensure_session()
        norm = str(phase).strip().lower()
        if norm in ("orbit", "orbital"):
            sess.phase = Phase.ORBIT
        elif norm in ("planning", "nox", "plan"):
            sess.phase = Phase.PLANNING
        else:
            raise ValueError(
                f"set_phase: unknown phase {phase!r} "
                "(expected 'orbit' or 'planning')"
            )
        return self

    def set_credits(
        self,
        *,
        p1: Optional[int] = None,
        p2: Optional[int] = None,
    ) -> "WorldBuilder":
        """Set the seat credit balances (orbit-phase build cost gating).

        Only writes the seats explicitly named — passing ``p1=200`` and
        omitting ``p2`` leaves the opponent's credits untouched. Values
        are clamped to non-negative ints; the engine never stores
        credit debt.
        """
        sess = self._ensure_session()
        if p1 is not None:
            sess.credits["p1"] = max(0, int(p1))
        if p2 is not None:
            sess.credits["p2"] = max(0, int(p2))
        return self

    def record_last_night_combat(
        self,
        *,
        type: str,
        owner: str,
        hours: Sequence[int],
        day: Optional[int] = None,
        cells: Optional[Sequence[XY]] = None,
        targets: Optional[Sequence[XY]] = None,
        radius: int = 0,
        victim: Optional[str] = None,
        unit: Optional[str] = None,
    ) -> "WorldBuilder":
        """Stamp a canonical combat event onto ``combat_events_by_day``.

        Mirrors :meth:`GameSession._record_combat_event` — the arsenal
        tracker (``rival_arsenal.py``) reads this feed to estimate rival
        EMP/chaff stocks. Scenarios that require the agent to notice
        "opponent burned 2 EMPs last night, so a 3rd is unlikely" use
        this to seed the world.

        ``type`` is one of ``"emp"``, ``"emp_hit"``, ``"chaff"``,
        ``"chaff_jam"``. Only the fields relevant to that type need to
        be provided.
        """
        sess = self._ensure_session()
        d = int(day) if day is not None else int(sess.day)
        t = str(type).strip().lower()
        event: dict = {
            "type": t,
            "owner": str(owner),
            "day": d,
            "hours": [int(h) for h in hours],
        }
        if cells is not None:
            event["cells"] = [[int(x), int(y)] for x, y in cells]
        if targets is not None:
            event["targets"] = [[int(x), int(y)] for x, y in targets]
        if radius:
            event["radius"] = int(radius)
        if victim is not None:
            event["victim"] = str(victim)
            event.setdefault("by", [str(owner)])
        if unit is not None:
            event["unit"] = str(unit)
            event.setdefault("by", [str(owner)])
        sess.combat_events_by_day.setdefault(str(d), []).append(event)
        return self

    # ------------------------------------------------------------------
    # Finalise
    # ------------------------------------------------------------------
    def build(self) -> Tuple[SocStore, str]:
        """Persist the session and return ``(store, session_id)``.

        Every mutator above worked on the hydrated session; this call
        walks back through ``save_session_full`` so the next read
        (e.g. ``run_agent_turn``) sees exactly the state we set up.
        """
        sess = self._ensure_session()
        # Re-run the "remember everything currently visible" pass so
        # entities we just placed surface in the agent view. (This is
        # the same pass GameSession.new runs after _spawn_defaults.)
        #
        # Driven off the session's actual seat list rather than a
        # hardcoded p1/p2: on a four-seat board the later seats would
        # otherwise start with no memory of their own units, which reads
        # to the agent as an empty map rather than a crowded one.
        for p in (sess.players or ("p1", "p2")):
            sess._remember_entire_visibility(p)  # type: ignore[arg-type]
        soc_engine.save_session_full(self.store, sess)
        assert self._session_id is not None
        return self.store, self._session_id
