"""v11 Phase 3 / Part A1 — the PERSISTENT stripped/green hazard memory.

The seed-56 collapse (v11 → 75 pts) was a self-inflicted bleed: the agent
blind-dropped onto a rival's redsign pure that a rival harvester had already
STRIPPED to synthetic green on a prior night. By the time v11 dropped, the cell
was green — but FOGGED to v11 — so nothing at plan time flagged it, and the
engine auto-harvested green for −100/parcel.

The precaution exploits one fact: **green is monotonic.** A cell that has turned
GREEN (harvested → synthetic, or a natural hazard) never becomes valuable again.
So the union of every green cell the seat has EVER seen is a safe, growing "do
not drop/step here" set — even for cells now hidden by fog. This module
accumulates that union per (session, seat) and hands it to the deterministic
packager (which refuses to compile a drop/step onto it) and the move sanitizer
(as a backstop `bad` set that survives fog, unlike the view-only green helpers).

Storage mirrors :mod:`.memory`: a process-local cache always (tests / offline
eval) plus a best-effort ``SOC_AGENT_MEMORY`` row under a dedicated KIND so the
union survives process restarts within a season. A write failure never crashes
the turn.

NOTE: this closes the SELF/observed-green case. A rival stripping a pure while
it is fogged to us is never observed as green — that case is closed structurally
by Part B (do not send a harvester blind onto a fogged rival seam).
"""

from __future__ import annotations

import json as _json
from typing import Any, Mapping, Optional, Set, Tuple

from sea_of_colours.orchestrator_2.harnesses.sagar_cursor._v7.validators import (
    _synthetic_green_cells,
    _green_hazard_cells,
)

Cell = Tuple[int, int]

# KIND row key in SOC_AGENT_MEMORY (one authoritative union per session+seat).
_KIND = "arena:hazard_cells"

# Process-local cache: (session_id, player) -> set[Cell]. Always populated so
# tests and offline eval accumulate without a live Snowflake session.
_CACHE: dict = {}


def _cache_key(session_id: str, player: str) -> str:
    return f"{session_id}::{player}"


def enemy_trail_cells(agent_view: Mapping[str, Any]) -> Set[Cell]:
    """Cells a RIVAL harvester walked, which are therefore stripped GREEN.

    Fix 0.1 (OBS-43). Every step harvests — the CHAIN GRAMMAR says so in the
    same prompt — so a trail across a cell means that cell is gone. The two
    green readers above both walk ``world.live``, i.e. what we can SEE right
    now, so a trail over ground that is fogged to us is never learned. On
    ``SNAP_ac1c55bf_d6_p4`` the card printed `enemy_harvester_trail at=[17,7]`
    and, further down, `best_red_echo (17,7) purity_est=158` — the same cell,
    sold as red on the strength of a two-night-old echo, hours after a rival
    cleared it. `FULL_SWEEP` then routed through it and two others like it and
    advertised a yield that counted them IN.

    Folding them in here reaches every consumer at once: the seam geometry, the
    value pyramid, the packager's forbidden set and the card's own AVOID list.
    """
    out: Set[Cell] = set()
    intel = agent_view.get("competitor_intel") or {}
    if not isinstance(intel, Mapping):
        return out
    for bucket in ("new_this_day", "persistent_echoes"):
        for ev in (intel.get(bucket) or []):
            if not isinstance(ev, Mapping):
                continue
            if str(ev.get("kind") or "") != "enemy_harvester_trail":
                continue
            at = ev.get("at")
            if isinstance(at, (list, tuple)) and len(at) == 2:
                try:
                    out.add((int(at[0]), int(at[1])))
                except (TypeError, ValueError):
                    continue
    return out


def view_green_cells(agent_view: Mapping[str, Any]) -> Set[Cell]:
    """Every GREEN cell in THIS view — harvested by us, natural hazard, or
    stripped by a rival harvester whose trail we witnessed."""
    return (
        set(_synthetic_green_cells(agent_view))
        | set(_green_hazard_cells(agent_view))
        | enemy_trail_cells(agent_view)
    )


def load(
    session_id: str, player: str, *, store: Optional[Any] = None,
) -> Set[Cell]:
    """The accumulated hazard union for the seat (fog-surviving).

    Reads the process cache first; hydrates from Snowflake once if empty.
    """
    key = _cache_key(session_id, player)
    if key in _CACHE:
        return set(_CACHE[key])
    hydrated = _hydrate(session_id, player, store=store)
    _CACHE[key] = set(hydrated)
    return set(hydrated)


def accumulate(
    session_id: str,
    player: str,
    agent_view: Mapping[str, Any],
    *,
    store: Optional[Any] = None,
    season_name: Optional[str] = None,
) -> Set[Cell]:
    """Fold this view's green cells into the seat's union and return the union.

    Idempotent and monotonic: cells only ever get added. Persists best-effort;
    a write failure is swallowed so the turn never crashes on memory I/O.
    """
    key = _cache_key(session_id, player)
    union = load(session_id, player, store=store)
    fresh = view_green_cells(agent_view)
    new = fresh - union
    union |= fresh
    _CACHE[key] = set(union)
    if new:  # only pay the write when the union actually grew
        _persist(session_id, player, union, store=store, season_name=season_name)
    return set(union)


# ── persistence (best-effort; mirrors memory.py) ────────────────────────
def _persist(
    session_id: str,
    player: str,
    union: Set[Cell],
    *,
    store: Optional[Any] = None,
    season_name: Optional[str] = None,
    kind: str = _KIND,
) -> None:
    try:
        from sea_of_colours.snowpark.backend import snowpark_session_for
        session = snowpark_session_for(store)
        if session is None:
            return
        payload = _json.dumps(
            {"cells": [[int(x), int(y)] for (x, y) in sorted(union)]}
        )
        session.sql(
            """
            MERGE INTO SOC_AGENT_MEMORY t
            USING (SELECT ? AS session_id, ? AS season_name, ? AS player,
                          ? AS kind, PARSE_JSON(?) AS payload) s
            ON t.session_id = s.session_id AND t.player = s.player AND t.kind = s.kind
            WHEN MATCHED THEN UPDATE SET payload = s.payload,
                                         season_name = s.season_name,
                                         updated_at = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (session_id, season_name, player, kind, payload, updated_at)
                VALUES (s.session_id, s.season_name, s.player, s.kind, s.payload, CURRENT_TIMESTAMP())
            """,
            params=[session_id, season_name or "", player, kind, payload],
        ).collect()
    except Exception:
        pass


def _hydrate(
    session_id: str, player: str, *, store: Optional[Any] = None,
    kind: str = _KIND,
) -> Set[Cell]:
    out: Set[Cell] = set()
    try:
        from sea_of_colours.snowpark.backend import snowpark_session_for
        session = snowpark_session_for(store)
        if session is None:
            return out
        rows = session.sql(
            """
            SELECT PAYLOAD FROM SOC_AGENT_MEMORY
            WHERE session_id = ? AND player = ? AND kind = ?
            """,
            params=[session_id, player, kind],
        ).collect()
        for r in rows:
            payload = r["PAYLOAD"]
            if isinstance(payload, str):
                try:
                    payload = _json.loads(payload)
                except Exception:
                    continue
            for c in (payload or {}).get("cells") or []:
                if isinstance(c, (list, tuple)) and len(c) == 2:
                    try:
                        out.add((int(c[0]), int(c[1])))
                    except (TypeError, ValueError):
                        continue
    except Exception:
        pass
    return out


# ── spent BLUE pockets (fix 0.7, OBS-45) ───────────────────────────────
# A bluesign is computed ONCE at season birth from generation-time geometry
# (``session._compute_blue_sign``) and deliberately survives depletion, which is
# fine as lore and ruinous as a target list: red beacons retire when their last
# pure is taken and blue has no retirement mechanism at all. Worse, the sampler
# PREFERS fogged cells, so a pocket we mined out ourselves gets promoted the
# moment our probe moves on and the cell re-fogs.
#
# Same monotonic trick as the green union above: a blue cell we have SEEN to be
# empty never refills, so remembering it is safe forever. Note a mined blue
# collapses to EMPTY rather than green (RULEBOOK §3.12), so the green union
# cannot carry this and it needs a union of its own.
_BLUE_KIND = "arena:spent_blue"
_BLUE_CACHE: dict = {}


def observed_spent_blue(agent_view: Mapping[str, Any]) -> Set[Cell]:
    """Bluesign cells THIS view proves are no longer blue.

    A cell we can currently see, inside a bluesign cluster, whose live tile is
    anything other than BLUE — mined out by anyone, or a smear cell that never
    held blue in the first place (the signature is deliberately jittered off the
    real pocket, so plenty of bright cells never had any).
    """
    live: dict = {}
    for row in ((agent_view.get("world") or {}).get("live") or []):
        if not isinstance(row, Mapping):
            continue
        try:
            live[(int(row["x"]), int(row["y"]))] = str(row.get("tile") or "").upper()
        except (TypeError, KeyError, ValueError):
            continue
    out: Set[Cell] = set()
    for cluster in (agent_view.get("blue_sign") or []):
        if not isinstance(cluster, Mapping):
            continue
        for cell in (cluster.get("cells") or []):
            if not isinstance(cell, (list, tuple)) or len(cell) < 2:
                continue
            try:
                c = (int(cell[0]), int(cell[1]))
            except (TypeError, ValueError):
                continue
            tile = live.get(c)
            if tile is not None and tile != "BLUE":
                out.add(c)
    return out


def load_spent_blue(
    session_id: str, player: str, *, store: Optional[Any] = None,
) -> Set[Cell]:
    """The accumulated spent-blue union for the seat (fog-surviving)."""
    key = _cache_key(session_id, player)
    if key in _BLUE_CACHE:
        return set(_BLUE_CACHE[key])
    hydrated = _hydrate(session_id, player, store=store, kind=_BLUE_KIND)
    _BLUE_CACHE[key] = set(hydrated)
    return set(hydrated)


def accumulate_spent_blue(
    session_id: str,
    player: str,
    agent_view: Mapping[str, Any],
    *,
    store: Optional[Any] = None,
    season_name: Optional[str] = None,
) -> Set[Cell]:
    """Fold this view's proven-empty bluesign cells into the seat's union."""
    key = _cache_key(session_id, player)
    union = load_spent_blue(session_id, player, store=store)
    fresh = observed_spent_blue(agent_view)
    new = fresh - union
    union |= fresh
    _BLUE_CACHE[key] = set(union)
    if new:
        _persist(
            session_id, player, union,
            store=store, season_name=season_name, kind=_BLUE_KIND,
        )
    return set(union)


def clear_cache() -> None:
    """Test helper — reset the process-local unions between fixtures."""
    _CACHE.clear()
    _BLUE_CACHE.clear()
