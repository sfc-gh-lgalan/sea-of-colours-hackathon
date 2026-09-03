"""Post-turn recorder for tabula.

After the night resolves, this module:

1. Reads the engine's post-resolve state to determine what the seat banked.
2. Detects probe-crush events (harvester drop cell matched a friendly
   probe's cell at turn start).
3. Updates the current-day memory entry with the outcome fields.

Called by the harness on the NEXT turn's entry (before that turn's LLM
call), so the reflection block is populated when the pilot reads it.
Deferring the write to the next turn means we don't have to reach into
the engine mid-resolve — we just query the session state.
"""

from __future__ import annotations

from typing import Any, List, Mapping, Optional, Tuple

from sea_of_colours.orchestrator_2.harnesses.sagar_cursor._v7 import memory


def detect_probe_crushes(
    moves_submitted: List[Mapping[str, Any]],
    my_probe_cells_at_turn_start: set,
) -> List[str]:
    """Return human-readable strings for each crush the pilot's drops caused.

    A crush = ``drop`` action whose target cell equals one of the seat's
    live probe cells at the start of the turn. We compute this against
    the turn-start snapshot (not the resolved state) so the memory
    entry reflects the pilot's intent at submission time.
    """
    out: List[str] = []
    for m in moves_submitted:
        if not isinstance(m, Mapping):
            continue
        if str(m.get("a") or "") != "drop":
            continue
        at = m.get("at")
        if not (isinstance(at, (list, tuple)) and len(at) == 2):
            continue
        try:
            cell = (int(at[0]), int(at[1]))
        except (TypeError, ValueError):
            continue
        if cell in my_probe_cells_at_turn_start:
            unit = str(m.get("unit") or "?")
            out.append(f"harvester {unit} crushed friendly probe at [{cell[0]},{cell[1]}]")
    return out


def compute_banked_this_night(
    session_id: str,
    player: str,
    *,
    day_at_turn_start: int,
    score_before: int,
    store: Any = None,
) -> Optional[int]:
    """Diff the seat's score before and after night resolution.

    Returns None if the read fails (e.g. still mid-resolve). The
    harness treats None as "unresolved — leave actual_banked null in
    memory and let next turn's reflection say 'not yet observed'".
    """
    try:
        from sea_of_colours.snowpark import engine as soc_engine
        if store is None:
            from sea_of_colours.snowpark.backend import get_store
            store = get_store()
        sess = soc_engine._hydrate_session(store, session_id)
        score_after = int(sess.score_for(player) if hasattr(sess, "score_for") else 0)
        return max(0, score_after - int(score_before))
    except Exception:
        return None


def close_prior_day_if_needed(
    session_id: str,
    player: str,
    *,
    current_day: int,
    turn_start_score_by_day: dict,
    turn_start_probes_by_day: dict,
    moves_submitted_by_day: dict,
    store: Any = None,
    season_name: Optional[str] = None,
) -> None:
    """Fill in ``actual_banked`` + ``probe_crushes`` on the previous day's memory.

    Called at the top of each turn (before the LLM fires). Uses the
    turn-start snapshots the harness captured on the prior turn to
    compute what the engine's resolve step actually banked.
    """
    prior_day = int(current_day) - 1
    if prior_day < 1:
        return  # No prior day on the season's first night.
    if prior_day not in turn_start_score_by_day:
        return  # No snapshot recorded (e.g., pilot missed prior turn).

    score_before = int(turn_start_score_by_day[prior_day])
    banked = compute_banked_this_night(
        session_id, player,
        day_at_turn_start=prior_day, score_before=score_before, store=store,
    )
    probe_cells = turn_start_probes_by_day.get(prior_day, set())
    moves = moves_submitted_by_day.get(prior_day, [])
    crushes = detect_probe_crushes(moves, probe_cells)

    memory.update_after_resolve(
        session_id, player, prior_day,
        actual_banked=banked,
        probe_crushes=crushes,
        store=store,
        season_name=season_name,
    )
