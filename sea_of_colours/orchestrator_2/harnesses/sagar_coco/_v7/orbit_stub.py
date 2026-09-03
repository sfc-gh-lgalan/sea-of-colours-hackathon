"""Orbit-phase submission helpers.

v1.13 — settlement is automatic: every RED parcel ships and scores and
every GREEN parcel is dumped at -100, whatever the seat submits. So the
orbit turn is now purely a *shopping* decision, and the choice below is
only about whether the seat spends its credits, not whether it scores.

Two callable functions:

  * :func:`submit_empty_orbit` — submits ``[]``. The engine advances and
    the vault still settles; the seat simply buys nothing and banks its
    credits. Cheap, and a legitimate baseline.

  * :func:`submit_heuristic_orbit` — routes the orbit turn through
    RED_HARVEST's ``plan_orbit_actions``: repair damaged harvesters,
    build a harvester under the fleet cap, buy weapons with BLUE, and
    top up probe stock. Used for solo runs and live seasons.

The harness picks between them per :envvar:`TABULA_V7_ORBIT_MODE`
(``"empty"`` or ``"heuristic"``, default ``"heuristic"``).

.. note::
   **This is one of the two deliberate hackathon gaps.** The heuristic
   path buys weapons but nothing in the night pipeline ever *fires*
   them, and it does not prioritise BLUE when choosing where to harvest
   — so the arsenal it pays for tends to sit unused. Closing that loop
   is the exercise. See the harness README.
"""

from __future__ import annotations

import os
import time
from typing import Any, Mapping

from sea_of_colours.snowpark import engine as soc_engine


def submit_empty_orbit(
    store: Any, session_id: str, player: str,
) -> Mapping[str, Any]:
    """Submit an empty orbit action queue and return the audit envelope.

    Note this does not mean "score nothing": settlement is automatic
    (v1.13), so the vault still ships. It means "buy nothing".

    The audit envelope mirrors the shape run_agent_turn() returns for
    normal turns so downstream loggers don't need a special case.
    """
    started = time.time()
    result = soc_engine.submit_orbit_actions(store, session_id, player, [])
    ms_elapsed = int((time.time() - started) * 1000)
    return {
        "ok": True,
        "agent_id": "TABULA_V7",
        "runtime": "harness_in_process",
        "rationale": (
            "[orbit stub — phase-1 has no orbit LLM; submitting empty so "
            "engine advances to next night]"
        ),
        "orbit_actions_count": 0,
        "ms_elapsed": ms_elapsed,
        "submit_result": result,
    }


def submit_heuristic_orbit(
    store: Any, session_id: str, player: str, view: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Route the orbit turn through RED_HARVEST's ``plan_orbit_actions``.

    Full playbook (from :func:`sea_of_colours.agent.heuristic_agent.plan_orbit_actions`):

      1. Repair damaged harvesters.
      2. Build a new harvester (under fleet cap + affordable).
      3. Buy weapons with BLUE, if the seat has weapons enabled.
      4. Top up probe stock.

    There is no shipping or jettison step: v1.13 settles the vault
    automatically. There is also no action cap, so the playbook stops
    when the wallet does rather than at three.

    Falls back to :func:`submit_empty_orbit` on any exception so an
    orbit failure never blocks a season.
    """
    started = time.time()
    try:
        from sea_of_colours.agent.heuristic_agent import plan_orbit_actions
        agent_view = view.get("agent_view") or view
        actions, rationale = plan_orbit_actions(agent_view)
        result = soc_engine.submit_orbit_actions(
            store, session_id, player, list(actions),
        )
        ms_elapsed = int((time.time() - started) * 1000)
        return {
            "ok": True,
            "agent_id": "TABULA_V7",
            "runtime": "harness_in_process",
            "rationale": f"[orbit heuristic] {rationale}",
            "orbit_actions_count": len(actions),
            "ms_elapsed": ms_elapsed,
            "submit_result": result,
        }
    except Exception as exc:  # pragma: no cover — defensive fallback
        # A broken orbit heuristic must NEVER block the season. Log the
        # reason in the envelope so the multinight driver surfaces it.
        empty_env = submit_empty_orbit(store, session_id, player)
        empty_env["rationale"] = (
            f"[orbit heuristic FAILED: {type(exc).__name__}: {exc}] "
            "fell back to empty orbit — parcels still settle, but the "
            "seat bought nothing this turn"
        )
        return empty_env


def submit_orbit(
    store: Any, session_id: str, player: str, view: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Dispatch the orbit turn based on ``TABULA_V7_ORBIT_MODE``.

    Default is ``"heuristic"`` (builds units, buys weapons). Set to
    ``"empty"`` to force the no-op path (useful for regression tests
    where the buy-nothing score is the baseline).
    """
    mode = os.environ.get("TABULA_V7_ORBIT_MODE", "heuristic").strip().lower()
    if mode == "empty":
        return submit_empty_orbit(store, session_id, player)
    return submit_heuristic_orbit(store, session_id, player, view)
