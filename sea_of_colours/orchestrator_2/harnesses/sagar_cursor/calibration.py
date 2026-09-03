"""What the agent learned about its own optimism, carried across seasons.

The ledger predicts; the engine settles. The gap between them is the only
honest way to know whether a prediction of a given KIND can be trusted, and
V12 already writes ``expected_yield`` to memory and reconciles it the following
turn -- it simply never reads it back as a correction. This closes that loop.

Two rules shape the whole module.

**Per option KIND, never per board.** A per-board factor would memorise the ten
battle boards and generalise to nothing. A per-kind factor says something real
about the agent: "your seam estimates run 30% hot", which transfers to any board.

**Local JSON is the only required store.** Every memory in the shipped harness
is keyed by ``session_id``, so the agent is amnesiac between seasons -- it can
get better at tonight, never at the game. Persisting the factors is what makes
season N+1 start where season N finished. Snowflake mirrors it when reachable,
because that is the on-theme demonstration, but it is **never** on the critical
path: the league runs offline, and an agent that needs a network to think is an
agent that scores zero when the network is not there. Every Snowflake call here
is best-effort and swallowed.
"""

from __future__ import annotations

import json
import os
import pathlib
import threading
from typing import Any, Dict, Mapping, Optional

#: Checked in beside the agent so a fresh clone starts with whatever the agent
#: had already learned, with no setup and no credentials.
_STORE = pathlib.Path(__file__).with_name("calibration.json")

#: Never trust a factor built from a handful of turns; an LLM seat is noisy and
#: a two-sample correction is noise dressed as knowledge.
_MIN_SAMPLES = 8

#: Clamp. A calibration factor is a nudge, not a licence to invert the ledger --
#: without this one bad season could teach the agent that seams are worthless.
_FLOOR, _CEIL = 0.5, 1.5

_lock = threading.Lock()
_cache: Optional[Dict[str, Any]] = None


def _load() -> Dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    try:
        _cache = json.loads(_STORE.read_text(encoding="utf-8"))
    except Exception:
        # Missing or unreadable is the NORMAL first-run state, not an error.
        _cache = {"kinds": {}, "denial": {}}
    _cache.setdefault("kinds", {})
    _cache.setdefault("denial", {})
    return _cache


def _save(data: Dict[str, Any]) -> None:
    try:
        _STORE.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass  # a read-only checkout must not break a turn


def factor_for(kind: str) -> float:
    """How much of a prediction of this kind actually arrives. 1.0 until proven."""
    row = _load()["kinds"].get(str(kind))
    if not isinstance(row, Mapping):
        return 1.0
    n = int(row.get("n") or 0)
    if n < _MIN_SAMPLES:
        return 1.0
    predicted = float(row.get("predicted") or 0.0)
    actual = float(row.get("actual") or 0.0)
    if predicted <= 0.0:
        return 1.0
    return max(_FLOOR, min(_CEIL, actual / predicted))


def record(kind: str, *, predicted: float, actual: float) -> None:
    """Add one settled turn to the running record for ``kind``."""
    with _lock:
        data = _load()
        row = data["kinds"].setdefault(str(kind), {"n": 0, "predicted": 0.0, "actual": 0.0})
        row["n"] = int(row.get("n") or 0) + 1
        row["predicted"] = float(row.get("predicted") or 0.0) + float(predicted)
        row["actual"] = float(row.get("actual") or 0.0) + float(actual)
        _save(data)
    _mirror_to_snowflake(kind, predicted=predicted, actual=actual)


def _denial(key: str, default: float) -> float:
    row = _load()["denial"].get(key)
    if not isinstance(row, Mapping):
        return default
    n = int(row.get("n") or 0)
    if n < _MIN_SAMPLES:
        return default
    total = float(row.get("total") or 0.0)
    return total / n if n else default


def denial_per_blue(default: float) -> float:
    """Realised points denied per unit of BLUE actually spent.

    The self-correcting term. An agent that banks blue and never fires records
    nothing, so this stays at its conservative prior and the shadow price never
    inflates on the strength of denial it has not delivered.
    """
    return _denial("per_blue", default)


def emp_denial_per_probe(default: float) -> float:
    return _denial("emp_per_probe", default)


def chaff_denial(default: float) -> float:
    return _denial("chaff", default)


def record_denial(key: str, value: float) -> None:
    with _lock:
        data = _load()
        row = data["denial"].setdefault(key, {"n": 0, "total": 0.0})
        row["n"] = int(row.get("n") or 0) + 1
        row["total"] = float(row.get("total") or 0.0) + float(value)
        _save(data)


# ── Snowflake mirror — optional, best-effort, never required ──────────────
_SF_ENABLED = os.environ.get("SAGAR_CURSOR_SNOWFLAKE_MEMORY", "0") == "1"


def _mirror_to_snowflake(kind: str, *, predicted: float, actual: float) -> None:
    """Write one row to Snowflake if configured. Silence is a valid outcome.

    Opt-IN rather than opt-out. The default path touches no network at all, so
    the league run cannot be slowed, blocked or failed by this, and the feature
    can be demonstrated by setting one environment variable.
    """
    if not _SF_ENABLED:
        return
    try:  # pragma: no cover - exercised only with credentials present
        from sea_of_colours.snowpark import backend

        session = backend.snowpark_session_for(None)
        if session is None:
            return
        session.sql(
            "INSERT INTO SOC_CURSOR_DB.AGENT.CALIBRATION "
            "(kind, predicted, actual, recorded_at) "
            "SELECT ?, ?, ?, CURRENT_TIMESTAMP()",
            params=[str(kind), float(predicted), float(actual)],
        ).collect()
    except Exception:
        # Unreachable, unauthorised, table absent, wrong region -- all the same
        # answer here: the agent keeps its local record and plays on.
        return
