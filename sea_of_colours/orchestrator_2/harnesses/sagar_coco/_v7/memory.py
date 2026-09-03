"""Per-(session, player) narrative memory for tabula.

The pilot writes one memory entry per turn. Entries carry:

* ``day`` — the game day this entry describes.
* ``plan_this_turn`` — one-sentence intent, agent-authored.
* ``rationale`` — one line on WHY the plan (agent-authored).
* ``predicted_outcome`` — {"banked_pts_estimate": "high|medium|low",
  "what_could_go_wrong": "one sentence"}.
* ``moves_summary`` — compact string of what was submitted post-validation.
* ``actual_banked`` — filled in by the recorder AFTER the engine resolves
  the night; None until then.
* ``probe_crushes`` — list of "harvester X crushed probe Y at [x,y]"
  strings; populated by the recorder.
* ``memory_note`` — one-sentence agent narrative for future replay.

Storage: ``SOC_AGENT_MEMORY`` (SESSION_ID, SEASON_NAME, PLAYER, KIND,
PAYLOAD, UPDATED_AT). We use ``KIND = 'arena:day<N>'`` as the row key
so each day has one authoritative entry per player; overwrites via
MERGE keep the record idempotent under retries.

Phase 1 keeps this dumb on purpose: no cross-session aggregation, no
strategies index, no vector search. Add those later once the loop is
proven.
"""

from __future__ import annotations

import json as _json
from typing import Any, Dict, List, Mapping, Optional

# Kept as module-level so tests can monkeypatch to an in-memory dict.
_IN_MEMORY_STORE: Dict[str, Dict[str, Any]] = {}


def _key(session_id: str, player: str, day: int) -> str:
    return f"{session_id}::{player}::{int(day)}"


def new_entry(
    day: int,
    *,
    plan_this_turn: str = "",
    rationale: str = "",
    predicted_outcome: Optional[Mapping[str, Any]] = None,
    moves_summary: str = "",
    memory_note: str = "",
) -> Dict[str, Any]:
    """Construct a blank memory entry with agent-authored fields filled.

    The engine-authored fields (``actual_banked``, ``probe_crushes``) get
    populated by :func:`update_after_resolve` once the night resolves.
    """
    return {
        "day": int(day),
        "plan_this_turn": str(plan_this_turn or "")[:280],
        "rationale": str(rationale or "")[:280],
        "predicted_outcome": dict(predicted_outcome or {}),
        "moves_summary": str(moves_summary or "")[:600],
        "actual_banked": None,
        "probe_crushes": [],
        "memory_note": str(memory_note or "")[:280],
    }


def save_entry(
    session_id: str,
    player: str,
    entry: Mapping[str, Any],
    *,
    store: Optional[Any] = None,
    season_name: Optional[str] = None,
) -> None:
    """Persist ``entry`` for ``(session, player, day)``.

    Writes to the in-process dict always (so tests and offline eval have
    memory) and, if a Snowflake store is configured, to
    ``SOC_AGENT_MEMORY``. The Snowflake write is best-effort — a broken
    connection should not crash the pilot's turn.
    """
    day = int(entry.get("day", 0))
    k = _key(session_id, player, day)
    _IN_MEMORY_STORE[k] = dict(entry)

    # Snowflake persistence (best-effort). Import lazily so the module
    # stays importable in offline test environments without a live
    # Snowflake session.
    try:
        from sea_of_colours.snowpark.backend import snowpark_session_for
        session = snowpark_session_for(store)
        if session is None:
            return
        payload = _json.dumps(entry, default=str)
        # MERGE — idempotent per (session, player, day).
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
            params=[
                session_id, season_name or "", player,
                f"arena:day{day}", payload,
            ],
        ).collect()
    except Exception:
        # Never crash the pilot's turn on a memory-write failure.
        pass


def read_recent(
    session_id: str,
    player: str,
    *,
    limit: int = 3,
    store: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Return up to ``limit`` most-recent memory entries for the seat.

    Reads from the in-process store first (covers offline / test). If a
    Snowflake store is configured and the in-process store is empty, we
    hydrate from ``SOC_AGENT_MEMORY``. Sorted by day ascending so the
    replay reads oldest → newest.
    """
    keys = [k for k in _IN_MEMORY_STORE if k.startswith(f"{session_id}::{player}::")]
    if not keys:
        _hydrate_from_snowflake(session_id, player, store=store)
        keys = [k for k in _IN_MEMORY_STORE if k.startswith(f"{session_id}::{player}::")]
    entries = [_IN_MEMORY_STORE[k] for k in keys]
    entries.sort(key=lambda e: int(e.get("day", 0)))
    if limit and len(entries) > limit:
        return entries[-limit:]
    return entries


def update_after_resolve(
    session_id: str,
    player: str,
    day: int,
    *,
    actual_banked: Optional[int] = None,
    probe_crushes: Optional[List[str]] = None,
    store: Optional[Any] = None,
    season_name: Optional[str] = None,
) -> None:
    """Fill in the engine-authored fields on an existing entry.

    Called by :mod:`recorder` after the night resolves and the engine
    has updated the hoard + emitted the combat / crush events. If the
    entry doesn't exist yet (edge case: pilot never wrote a plan), we
    create a stub so at least the outcome is captured.
    """
    k = _key(session_id, player, day)
    entry = _IN_MEMORY_STORE.get(k)
    if entry is None:
        entry = new_entry(day)
    if actual_banked is not None:
        entry["actual_banked"] = int(actual_banked)
    if probe_crushes is not None:
        entry["probe_crushes"] = list(probe_crushes)
    _IN_MEMORY_STORE[k] = entry
    save_entry(session_id, player, entry, store=store, season_name=season_name)


def format_replay(entries: List[Dict[str, Any]]) -> str:
    """Turn memory entries into the prose block injected into the prompt.

    Shape:
        Day 1: plan="..." predicted=medium banked=1820 note="..." [crushes: ...]
        Day 2: plan="..." predicted=high banked=? (this-night resolving now)
    """
    lines: List[str] = []
    for e in entries:
        day = e.get("day")
        plan = e.get("plan_this_turn") or "(no plan recorded)"
        pred = (e.get("predicted_outcome") or {}).get("banked_pts_estimate") or "?"
        actual = e.get("actual_banked")
        note = e.get("memory_note") or ""
        crushes = e.get("probe_crushes") or []
        line = f"Day {day}: plan=\"{plan}\" predicted={pred}"
        if actual is not None:
            line += f" banked={actual}"
        else:
            line += " banked=? (not yet resolved)"
        if crushes:
            line += f" crushes={crushes}"
        if note:
            line += f" note=\"{note}\""
        lines.append(line)
    return "\n".join(lines) if lines else "(no prior nights)"


def _hydrate_from_snowflake(
    session_id: str,
    player: str,
    *,
    store: Optional[Any] = None,
) -> None:
    """Best-effort load of prior memory entries from SOC_AGENT_MEMORY."""
    try:
        from sea_of_colours.snowpark.backend import snowpark_session_for
        session = snowpark_session_for(store)
        if session is None:
            return
        rows = session.sql(
            """
            SELECT KIND, PAYLOAD FROM SOC_AGENT_MEMORY
            WHERE session_id = ? AND player = ?
              AND kind LIKE 'arena:day%'
            """,
            params=[session_id, player],
        ).collect()
        for r in rows:
            kind = str(r["KIND"])
            payload = r["PAYLOAD"]
            if isinstance(payload, str):
                try:
                    payload = _json.loads(payload)
                except Exception:
                    continue
            if not isinstance(payload, dict):
                continue
            try:
                day = int(kind.split("arena:day", 1)[-1])
            except ValueError:
                continue
            _IN_MEMORY_STORE[_key(session_id, player, day)] = dict(payload)
    except Exception:
        pass


def clear_in_memory_store() -> None:
    """Test helper — reset the process-local cache between fixtures."""
    _IN_MEMORY_STORE.clear()
