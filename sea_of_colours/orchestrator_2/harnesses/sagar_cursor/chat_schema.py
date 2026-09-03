"""v10 chat schemas — hermetic-ish overrides on top of the v7 specs.

v10 runs a CONTAINED TWO-STAGE thinker (THINK prose -> PLAN decision) BEFORE the
mover, so by the time the mover runs the reasoning already exists. The v7 mover
schema still forces the mover to (re)write ``reflection_on_last_night`` /
``plan_this_turn`` / ``rationale`` / ``predicted_outcome`` / ``memory_note`` —
a wall of prose the model spends 25-99s generating (the A4 latency wart). v10's
mover only needs to PACKAGE the thinker's committed plan into moves, so its
schema is trimmed to ``moves`` (+ an optional one-line ``note``). The reflection
/ plan / rationale surfaced for memory + audit are sourced from the THINKER
instead (see harness).

The decision (thinker) schema is re-exported unchanged.
"""

from __future__ import annotations

from sea_of_colours.orchestrator_2.harnesses.sagar_cursor._v7.chat_schema import (  # noqa: F401
    _MOVE_ITEM,
    _DECISION_SCHEMA as _V7_DECISION_SCHEMA,
)

# v11 STRATEGY JOURNAL: two extra agent-authored strings on the plan pass.
#   * ``intent``     — 1-2 sentences: what the agent is trying to do tonight +
#                      why. Saved to the journal and shown back next night.
#   * ``reflection`` — 1-2 sentences on how LAST night's plan actually executed
#                      (did outcome match intent? name the gap's cause).
# Declared AFTER the decision fields and BEFORE ``reasoning`` so the machine-
# readable decision (posture/plan/situational) still leads the object and can
# never be lost to truncation; only the trailing ``reasoning`` prose is at risk.
# We rebuild (rather than mutate) the v7 schema so v7/v8 remain untouched.
def _v11_decision_schema() -> dict:
    props: dict = {}
    for key, spec in _V7_DECISION_SCHEMA["properties"].items():
        if key == "reasoning":
            props["intent"] = {"type": "string"}
            props["reflection"] = {"type": "string"}
        props[key] = spec
    # ``intent``/``reflection`` land at the end if v7 ever drops ``reasoning``.
    props.setdefault("intent", {"type": "string"})
    props.setdefault("reflection", {"type": "string"})
    schema = dict(_V7_DECISION_SCHEMA)
    schema["properties"] = props
    return schema


_V11_DECISION_SCHEMA = _v11_decision_schema()

DECISION_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "soc_v11_strategist_decision",
        "schema": _V11_DECISION_SCHEMA,
    },
}

# Moves-only mover schema. ``additionalProperties: False`` means a strict
# structured-output model emits ONLY ``moves`` (+ the optional ``note``) — it
# CANNOT wander into the prose fields, which is what collapses the latency.
_V10_MOVES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "moves": {"type": "array", "items": _MOVE_ITEM},
        # optional, one line — a cheap escape hatch for a single caveat; the
        # prompt tells the mover to leave it empty unless something was cut.
        "note": {"type": "string"},
    },
    "required": ["moves"],
}

MOVES_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "soc_tabula_v11_moves", "schema": _V10_MOVES_SCHEMA},
}
