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

from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test._v7.chat_schema import (  # noqa: F401
    _MOVE_ITEM as _V7_MOVE_ITEM,
    _DECISION_SCHEMA as _V7_DECISION_SCHEMA,
)

# ── the weapon verb ────────────────────────────────────────────────────
#
# The v7 move item allows drop / step / pickup / probe and nothing else,
# and an ``enum`` in a strict structured-output schema is a hard wall:
# the model cannot emit ``emp_launch`` even when the prompt asks for it,
# so every earlier attempt at an EMP agent looked like the model refusing
# and was actually the schema refusing. Widen it here rather than in
# ``_v7/``, which is the frozen regression baseline.
#
# Note this only governs the LLM MOVER, which runs on fallback nights.
# The normal path is the packager compiling ``EMP_SCORCH`` off the option
# menu, and that never passes through this schema at all. Both have to
# know the verb or the fallback silently disarms the seat.
_MOVE_ITEM = {
    **_V7_MOVE_ITEM,
    "properties": {
        **_V7_MOVE_ITEM["properties"],
        "a": {
            "type": "string",
            "enum": [
                "drop", "step", "pickup", "probe",
                # {"a": "emp_launch", "at": [[x,y], ...]} — up to 3 cells
                # for one charge (RULEBOOK §4.9.3).
                "emp_launch",
            ],
        },
        # A salvo's ``at`` is a LIST of cells, where every other verb's is
        # a single cell. ``_CELL`` is `array of integer`, which rejects
        # the nested form, so the field is widened to accept either and
        # the packager/sanitiser normalise it.
        "at": {"type": "array"},
    },
}


def _with_declared_weapon_verbs(move_item: dict) -> dict:
    """Add the verbs of every declared play, keeping the ones already here.

    Deliberately NOT ``weapon_forge.widen_schema``. That helper rebuilds the
    enum as ``["drop", "step", "pickup", "probe"] + declared verbs``, and this
    fork does not declare an EMP play — its EMP is the hand-built four-beat
    BLIND_SCORCH, not a forge play. Calling the helper would therefore delete
    ``emp_launch`` from the enum, and by the note above that is a hard wall:
    the fallback mover would silently stop being able to fire the one weapon
    this seat was built around, and it would look like the model refusing.

    Union, never replace.
    """
    from . import weapon_plays

    have = list(move_item["properties"]["a"]["enum"])
    for verb in sorted({p.wire_verb for p in weapon_plays.PLAYS}):
        if verb not in have:
            have.append(verb)
    props = {**move_item["properties"], "a": {"type": "string", "enum": have}}
    return {**move_item, "properties": props}


_MOVE_ITEM = _with_declared_weapon_verbs(_MOVE_ITEM)

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
    props["situational"] = _v38_situational(props.get("situational"))
    schema = dict(_V7_DECISION_SCHEMA)
    schema["properties"] = props
    return schema


def _v38_situational(v7_spec: dict | None) -> dict:
    """Add ``snap`` to the situational read the thinker echoes back.

    SITUATIONAL FACTS grew a ``snap`` line in v1.38 and the prompt tells
    the thinker to echo that block back. Under Cortex strict mode the
    schema is the binding half of that instruction: ``situational`` sets
    ``additionalProperties: False`` and lists ``required``, so asking for
    a key the schema forbids is a request the model is not allowed to
    satisfy. Both halves move together or neither does.

    Rebuilt rather than mutated, like the move item above — the v7 dict
    is a module-level constant that v7 and v8 still serve from.
    """
    spec = dict(v7_spec or {})
    props = dict(spec.get("properties") or {})
    props["snap"] = {"type": "boolean"}
    spec["properties"] = props
    spec["required"] = list(spec.get("required") or []) + ["snap"]
    return spec


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
