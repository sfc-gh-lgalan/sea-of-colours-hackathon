"""JSON schema for the tabula plan, used with the Cortex inference API's
``response_format={"type":"json_schema", ...}`` to HARD-guarantee a valid
``moves`` object at the API level (no more "prose ate the budget, no moves").

Cortex structured-output quirks we design around (learned empirically):
  * ``type`` as an array (e.g. ``["object","null"]`` for nullable) is rejected.
  * ``minItems`` / ``maxItems`` are rejected.
  * heterogeneous list items are fine as long as only the always-present field
    (``a``) is ``required`` and the rest are optional.

So the schema is intentionally loose on the optional per-move fields (``unit`` /
``at`` / ``to``) — the move sanitizer + engine remain the authority on legality;
the schema's job is only to guarantee the SHAPE (a ``moves`` array of action
objects + a plan string), which is what was missing on the Agents API.
"""

_CELL = {"type": "array", "items": {"type": "integer"}}

# Grounded reflection on the prior night. WITHOUT this in the schema, strict
# json-schema mode silently drops the field — so the inference (chat) mover
# emitted NO reflection, severing the night-to-night learning loop (the agent
# kept walking into the same chaff window it was jammed by the night before,
# because it never processed "you got chaffed"). Shape mirrors the prompt's
# ACTION SCHEMA: {predicted, actual:int, gap_reason}. Kept OPTIONAL at the top
# level (not in ``required``) because night 1 has no prior night and Cortex
# strict mode can't express a nullable object (``type`` arrays are rejected);
# the prompt drives WHEN to fill it, the schema only makes it expressible.
_REFLECTION = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "predicted": {"type": "string"},
        "actual": {"type": "integer"},
        "gap_reason": {"type": "string"},
    },
    "required": ["actual", "gap_reason"],
}

_MOVE_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "a": {"type": "string", "enum": ["drop", "step", "pickup", "probe"]},
        "unit": {"type": "string"},
        "at": _CELL,
        "to": _CELL,
    },
    "required": ["a"],
}

_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "moves": {"type": "array", "items": _MOVE_ITEM},
        "reflection_on_last_night": _REFLECTION,
        "plan_this_turn": {"type": "string"},
        "rationale": {"type": "string"},
        "predicted_outcome": {
            "type": "object",
            "properties": {
                "banked_pts_estimate": {"type": "string"},
                "what_could_go_wrong": {"type": "string"},
            },
            "required": ["banked_pts_estimate"],
        },
        "memory_note": {"type": "string"},
    },
    "required": ["moves", "plan_this_turn"],
}

# The full ``response_format`` object to hand the inference invoker.
MOVES_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "soc_tabula_plan", "schema": _PLAN_SCHEMA},
}


# ── Strategist (thinker) decision schema ───────────────────────────────
#
# The v7 two-call split's THINKER, run on the *inference* API. CONTAINMENT via
# ORDERING: structured-output models emit properties in declaration order, so
# the tiny ACTIONABLE decision (``posture`` -> ``plan`` -> ``situational`` ->
# ``targets``) is emitted FIRST — it is complete inside the first ~150 tokens
# and can never be lost to truncation. ``reasoning`` is LAST and the prompt
# bounds it to a couple of sentences; if the model still over-writes it and the
# token cap clips the tail, the decision is already fully formed in the buffer.
#
# (History: ``reasoning`` used to be FIRST + unbounded — "think there first". On
# dense redsign nights haiku rambled ~9k chars into it, hit ``max_completion_
# tokens`` before the object closed, the JSON never reached ``plan``/``posture``,
# parse failed, and the directive was LOST -> the mover freelanced the wrong
# hot-drop. Decision-first kills that failure mode structurally.)
#
# The ``reasoning`` string is still captured + persisted so it can be scanned
# across scenarios (see scan_thinker.py) — the "does it actually reason?" audit.
_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        # FIRST — the decisive, machine-readable call. Emitted before anything
        # the model might ramble, so truncation can never cost us the decision.
        "posture": {
            "type": "string",
            "enum": ["aggressive", "defensive", "redsign_race", "final_convert"],
        },
        # v9 agency (additive; absent for v7/v8 whose prompt never mentions it).
        # ``plan`` is the ordered list of option/pattern IDs the thinker SELECTED
        # from the curated menu (e.g. ["SMASH_GRAB","PR1"]); the harness resolver
        # expands them to concrete geometry for the mover. ``situational`` is the
        # structured read that justifies the selection — surfaced only on redsign
        # nights, and when present every field is filled (Cortex strict mode).
        "plan": {"type": "array", "items": {"type": "string"}},
        "situational": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "mine": {"type": "boolean"},
                "players": {"type": "integer"},
                "chaff": {"type": "boolean"},
                "emp": {"type": "boolean"},
            },
            "required": ["mine", "players", "chaff", "emp"],
        },
        "targets": {"type": "array", "items": _CELL},
        "chaff_react": {"type": "boolean"},
        "avoid": {"type": "array", "items": _CELL},
        "note": {"type": "string"},
        # LAST — a SHORT rationale (prompt caps it to a couple of sentences).
        # Ordered last on purpose: it conditions nothing below it, so it cannot
        # push the decision past the token budget.
        "reasoning": {"type": "string"},
    },
    # Force a posture; the rest are advisory and may be omitted. ``reasoning`` is
    # deliberately NOT required — a decisive call with no prose still parses.
    "required": ["posture"],
    "additionalProperties": False,
}

DECISION_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "soc_strategist_decision", "schema": _DECISION_SCHEMA},
}
