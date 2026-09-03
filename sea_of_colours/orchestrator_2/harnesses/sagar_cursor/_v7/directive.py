"""Strategist directive — the tiny structured handoff between the v7
two-call split's THINKER (call 1) and MOVER (call 2).

The thinker reasons on the page and FINISHES with one ``DECISION:`` line
containing a small JSON object. We parse ONLY that line, sanitize it, and
render a compact directive block that gets injected into the mover's prompt.
The thinker's reasoning prose is never fed to the mover (see PLAN.md): a
truncated/absent DECISION yields ``None`` and the mover degrades to the
single-call v6 path.

Design constraints:
  * The payload is deliberately tiny (posture + up to 3 targets + a couple of
    flags). Geometry lives in the precomputed hints; the directive only carries
    the *decision*, keeping the handoff seam narrow.
  * Nothing here is authoritative over the engine or the move sanitizer — the
    directive is advisory. Targets are clamped to the grid but a bad one just
    gets dropped; it can never force an illegal move.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from sea_of_colours.orchestrator_2.harnesses.sagar_cursor._v7 import validators

# Postures the mover understands. Anything else is coerced to "aggressive"
# (the neutral "just harvest the best RED" default).
VALID_POSTURES = ("aggressive", "defensive", "redsign_race", "final_convert")
_DEFAULT_POSTURE = "aggressive"
_MAX_CELLS = 3
# v9: cap the ordered plan so a rambling thinker can't inject an unbounded list
# of IDs. A night rarely needs more than a couple of patterns + a probe/chain.
_MAX_PLAN_IDS = 6
# Only these keys survive from the thinker's situational read (whitelist).
_SITUATIONAL_KEYS = ("mine", "players", "chaff", "emp")

# Matches a ``DECISION:`` marker followed by a JSON object. Case-insensitive
# on the label; the JSON itself is decoded with json.raw_decode so trailing
# text after the object is tolerated.
_DECISION_RE = re.compile(r"DECISION\s*:\s*(\{)", re.IGNORECASE)


@dataclass
class Directive:
    """A sanitized strategist decision for one night.

    ``plan`` / ``situational`` are the v9 agency additions: the ORDERED option/
    pattern IDs the thinker picked from the curated menu, plus the structured
    read that justified them. They default empty so v7/v8 (whose prompt never
    offers a menu) behave exactly as before.
    """

    posture: str = _DEFAULT_POSTURE
    targets: List[Tuple[int, int]] = field(default_factory=list)
    chaff_react: bool = False
    avoid: List[Tuple[int, int]] = field(default_factory=list)
    note: str = ""
    plan: List[str] = field(default_factory=list)
    situational: Dict[str, Any] = field(default_factory=dict)


def parse_directive(text: str) -> Optional[Directive]:
    """Extract the LAST ``DECISION: {...}`` object from the thinker's output.

    Returns a raw (un-clamped) :class:`Directive` or ``None`` when no
    parseable DECISION line is present. We take the last occurrence so a
    thinker that muses "my decision will be..." mid-reasoning and then emits
    the real DECISION line at the end resolves to the final one.
    """
    raw = text or ""
    decoder = json.JSONDecoder()
    parsed_obj: Optional[dict] = None
    for m in _DECISION_RE.finditer(raw):
        try:
            obj, _ = decoder.raw_decode(raw[m.start(1):])
        except Exception:
            continue
        if isinstance(obj, dict):
            parsed_obj = obj  # keep scanning; keep the last valid one
    if parsed_obj is None:
        return None

    posture = str(parsed_obj.get("posture") or "").strip().lower()
    return Directive(
        posture=posture,
        targets=_coerce_cells(parsed_obj.get("targets")),
        chaff_react=_coerce_bool(parsed_obj.get("chaff_react")),
        avoid=_coerce_cells(parsed_obj.get("avoid")),
        note=str(parsed_obj.get("note") or "")[:120],
        plan=_coerce_ids(parsed_obj.get("plan")),
        situational=_coerce_situational(parsed_obj.get("situational")),
    )


def parse_directive_json(text: str) -> Tuple[Optional[Directive], str]:
    """Parse the CONTAINED-THINKER output (inference structured output path).

    Unlike :func:`parse_directive` (which scrapes a ``DECISION:`` line out of
    free-text streamed by the Agents-API thinker), the inference thinker returns
    the WHOLE content as one JSON object conforming to
    :data:`chat_schema.DECISION_RESPONSE_FORMAT` — ``reasoning`` FIRST, then the
    decision fields. We return ``(Directive, reasoning)`` so the caller can both
    act on the decision AND persist/scan the chain-of-thought.

    Returns ``(None, "")`` when the content is not a parseable JSON object.
    """
    raw = (text or "").strip()
    if not raw:
        return None, ""
    obj: Any = None
    try:
        obj = json.loads(raw)
    except Exception:
        # Tolerate leading/trailing prose around the object (rare with strict
        # schema mode, but cheap to guard) by decoding from the first brace.
        brace = raw.find("{")
        if brace >= 0:
            try:
                obj, _ = json.JSONDecoder().raw_decode(raw[brace:])
            except Exception:
                obj = None
    if not isinstance(obj, dict):
        # Truncation salvage. The schema is DECISION-FIRST (posture -> plan ->
        # situational -> ... -> reasoning LAST), so if the token cap clips the
        # tail the decision fields are already in the buffer even though the
        # JSON never closed. Regex the decision out of the partial buffer and
        # still build a directive; only fall back to (None, reasoning) when not
        # even a posture landed. This is the guard that stops a rambled/clipped
        # thinker from silently dropping the whole plan.
        salvaged = _salvage_decision(raw)
        return salvaged, _salvage_reasoning(raw)

    reasoning = str(obj.get("reasoning") or "").strip()
    posture = str(obj.get("posture") or "").strip().lower()
    directive = Directive(
        posture=posture,
        targets=_coerce_cells(obj.get("targets")),
        chaff_react=_coerce_bool(obj.get("chaff_react")),
        avoid=_coerce_cells(obj.get("avoid")),
        note=str(obj.get("note") or "")[:120],
        plan=_coerce_ids(obj.get("plan")),
        situational=_coerce_situational(obj.get("situational")),
    )
    return directive, reasoning


def sanitize_directive(
    directive: Optional[Directive], agent_view: Mapping[str, Any],
) -> Optional[Directive]:
    """Clamp a parsed directive to something safe to inject.

    * posture → coerced to a known value (default aggressive).
    * targets / avoid → in-bounds, de-duplicated, capped at ``_MAX_CELLS``.
    Returns ``None`` if the input was ``None`` (nothing to inject).
    """
    if directive is None:
        return None

    width, height = validators._world_dims(agent_view)

    def _clamp(cells: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
        out: List[Tuple[int, int]] = []
        for (x, y) in cells:
            if width and height and not (0 <= x < width and 0 <= y < height):
                continue
            if (x, y) in out:
                continue
            out.append((x, y))
            if len(out) >= _MAX_CELLS:
                break
        return out

    posture = directive.posture if directive.posture in VALID_POSTURES else _DEFAULT_POSTURE
    return Directive(
        posture=posture,
        targets=_clamp(directive.targets),
        chaff_react=bool(directive.chaff_react),
        avoid=_clamp(directive.avoid),
        note=directive.note,
        # ``plan`` IDs are validated against the live menu by the v9 resolver
        # (only that layer knows which IDs exist this turn); here we just cap +
        # de-dup so a malformed list can't bloat the handoff.
        plan=list(directive.plan)[:_MAX_PLAN_IDS],
        situational=dict(directive.situational),
    )


def format_directive_block(directive: Optional[Directive]) -> str:
    """Render the compact block injected into the mover prompt.

    Empty string when there is no directive (mover then runs exactly as the
    single-call v6 path).
    """
    if directive is None:
        return ""
    lines = [
        "STRATEGIST DIRECTIVE (from your own prior reasoning pass — act on "
        "this first, but RULES and legality still bind):",
        f"  posture: {directive.posture}",
    ]
    if directive.targets:
        tgt = ", ".join(f"({x},{y})" for (x, y) in directive.targets)
        lines.append(f"  prioritise these targets: {tgt}")
    if directive.chaff_react:
        lines.append(
            "  chaff_react: YES — keep chains SHORT and pick up EARLY "
            "(hour <=4); do not repeat a late-pickup window."
        )
    if directive.avoid:
        av = ", ".join(f"({x},{y})" for (x, y) in directive.avoid)
        lines.append(f"  avoid (contested / enemy vision): {av}")
    if directive.note:
        lines.append(f"  note: {directive.note}")
    return "\n".join(lines) + "\n"


def decision_line_complete(accum: str) -> bool:
    """Completion predicate for the thinker's streaming SSE loop.

    True once a well-formed ``DECISION: {...}`` object has streamed, so the
    invoker can close the socket the instant the usable directive lands
    (the reasoning that precedes it has already streamed).
    """
    return parse_directive(accum) is not None


# ── internals ──────────────────────────────────────────────────────────
def _salvage_decision(raw: str) -> Optional[Directive]:
    """Recover a Directive from a TRUNCATED decision-first buffer.

    The decision fields lead the object, so a clipped tail (usually inside the
    trailing ``reasoning`` string) still leaves ``posture`` / ``plan`` /
    ``situational`` / ``targets`` intact earlier in the text. We pull each with
    a targeted regex — tolerant of a missing closing bracket/brace — and build
    the directive. Returns ``None`` only when no posture is present (nothing
    actionable to hand the mover).
    """
    m = re.search(r'"posture"\s*:\s*"([A-Za-z_]+)"', raw)
    if not m:
        return None
    posture = m.group(1).strip().lower()

    def _array_slice(field: str) -> str:
        """Bracket-balanced slice of ``"field": [ ... ]`` incl. the brackets.

        Handles nested arrays (``targets`` is a list of ``[x,y]`` pairs), and
        returns the open fragment when the buffer was truncated before the
        matching close.
        """
        am = re.search(r'"' + field + r'"\s*:\s*\[', raw)
        if not am:
            return ""
        start = am.end() - 1  # the opening '['
        depth = 0
        for i in range(start, len(raw)):
            c = raw[i]
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return raw[start:i + 1]
        return raw[start:]  # truncated before the array closed

    plan_ids = re.findall(r'"([^"]+)"', _array_slice("plan"))
    note_m = re.search(r'"note"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
    situational: Dict[str, Any] = {}
    sit_m = re.search(r'"situational"\s*:\s*\{([^}]*)', raw)
    if sit_m:
        seg = sit_m.group(1)
        for key in ("mine", "chaff", "emp"):
            km = re.search(r'"' + key + r'"\s*:\s*(true|false)', seg)
            if km:
                situational[key] = km.group(1) == "true"
        pm = re.search(r'"players"\s*:\s*(\d+)', seg)
        if pm:
            situational["players"] = int(pm.group(1))

    return Directive(
        posture=posture,
        targets=_coerce_cells(_json_arr(_array_slice("targets"))),
        chaff_react=bool(re.search(r'"chaff_react"\s*:\s*true', raw)),
        avoid=_coerce_cells(_json_arr(_array_slice("avoid"))),
        note=str(note_m.group(1)) if note_m else "",
        plan=_coerce_ids(plan_ids),
        situational=situational,
    )


def _json_arr(text: str) -> Any:
    """Best-effort ``json.loads`` of a possibly-truncated array fragment."""
    try:
        return json.loads(text)
    except Exception:
        return []


def _salvage_reasoning(raw: str) -> str:
    """Best-effort extraction of the ``reasoning`` string from a TRUNCATED
    decision object (unparseable JSON). Returns "" if not found.

    Grabs everything after ``"reasoning":"`` up to the field that follows it
    (``","posture"`` etc.) or the end of the buffer, then unescapes the common
    JSON escapes. Deliberately forgiving — this is audit/scan salvage, never
    fed back to the mover.
    """
    m = re.search(r'"reasoning"\s*:\s*"', raw)
    if not m:
        return ""
    tail = raw[m.end():]
    # Cut at the start of the next top-level field if the object got that far.
    cut = re.search(r'"\s*,\s*"(?:posture|targets|chaff_react|avoid|note)"', tail)
    body = tail[: cut.start()] if cut else tail
    # Strip a dangling closing quote/brace fragment and unescape.
    body = body.rstrip().rstrip("}").rstrip().rstrip('"')
    return (
        body.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t").strip()
    )


def _coerce_cells(raw: Any) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            try:
                out.append((int(item[0]), int(item[1])))
            except (TypeError, ValueError):
                continue
    return out


def _coerce_ids(raw: Any) -> List[str]:
    """Ordered, de-duplicated option/pattern IDs from the thinker's ``plan``.

    IDs are kept as UPPERCASE tokens (letters, digits, ``_`` and ``#`` — the
    shapes the menu uses: ``SMASH_GRAB``, ``HD1``, ``BLIND_GRAB#2``). Anything
    else is dropped. Order is preserved (it encodes wave priority).
    """
    out: List[str] = []
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        if not isinstance(item, str):
            continue
        tok = item.strip().upper()
        if not tok or not re.fullmatch(r"[A-Z0-9_#]{1,32}", tok):
            continue
        if tok not in out:
            out.append(tok)
        if len(out) >= _MAX_PLAN_IDS:
            break
    return out


def _coerce_situational(raw: Any) -> Dict[str, Any]:
    """Whitelist the thinker's structured situational read.

    Keeps only ``mine`` (bool), ``players`` (int), ``chaff`` (bool),
    ``emp`` (bool); ignores anything else. Missing keys are simply absent.
    """
    out: Dict[str, Any] = {}
    if not isinstance(raw, Mapping):
        return out
    for k in _SITUATIONAL_KEYS:
        if k not in raw:
            continue
        if k == "players":
            try:
                out[k] = int(raw[k])
            except (TypeError, ValueError):
                continue
        else:
            out[k] = _coerce_bool(raw[k])
    return out


def _coerce_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "yes", "1", "y")
    if isinstance(raw, (int, float)):
        return raw != 0
    return False
