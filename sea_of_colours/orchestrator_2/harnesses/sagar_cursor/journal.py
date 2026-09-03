"""tabula_v11 — the continuous STRATEGY JOURNAL.

The agent's night-to-night learning thread. Each turn the agent authors a short
``intent`` (what it is trying to do tonight + why) and, on the next turn, a short
``reflection`` on how that plan actually executed. The harness fuses those with
engine truth so every day becomes a self-contained journal record:

    Day 3 INTENT:  "Race the north pure(255) seam before p2 — SMASH_GRAB wave 1."
          HAPPENED: banked red +206 (5 parcels); 1 FAILED move
          SAW:      p2 launched a probe (12,23); p2 harvester seen (24,21)
          REFLECT:  "Grab worked but I lost a step at H09 (not adjacent); tighten
                     the walk and pick up one hour earlier tonight."

Rendered oldest -> newest so the model reads its own evolving story, then extends
it with tonight's intent. Pure string formatting over the memory entries + the
already-built LAST NIGHT memory dict (see :mod:`last_night`); the only new agent
output is the ``intent`` / ``reflection`` pair, captured from the plan pass JSON.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


def _as_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ── capture the agent-authored intent + reflection from the plan JSON ────────
def parse_intent_reflection(plan_text: str) -> Tuple[str, str]:
    """Pull ``intent`` / ``reflection`` from the plan pass's JSON output.

    Best-effort and forgiving: handles a clean object, an object embedded in
    stray prose, and a truncated tail (regex salvage). Returns ``("", "")`` when
    neither field is present. Both are trimmed to a journal-friendly length.
    """
    raw = (plan_text or "").strip()
    if not raw:
        return "", ""
    obj: Any = None
    try:
        obj = json.loads(raw)
    except Exception:
        brace = raw.find("{")
        if brace >= 0:
            try:
                obj, _ = json.JSONDecoder().raw_decode(raw[brace:])
            except Exception:
                obj = None
    intent = reflection = ""
    if isinstance(obj, dict):
        intent = str(obj.get("intent") or "").strip()
        reflection = str(obj.get("reflection") or "").strip()
    if not intent:
        m = re.search(r'"intent"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        if m:
            intent = m.group(1).replace('\\"', '"').replace("\\n", " ").strip()
    if not reflection:
        m = re.search(r'"reflection"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        if m:
            reflection = m.group(1).replace('\\"', '"').replace("\\n", " ").strip()
    return intent[:280], reflection[:280]


# ── engine-truth summaries for a resolved night (from the LAST NIGHT dict) ───
def outcome_summary(last_night_memory: Mapping[str, Any]) -> str:
    """One-line "what actually happened" from the LAST NIGHT memory dict."""
    act = last_night_memory.get("actual") or {}
    own = [
        e for e in (last_night_memory.get("execution_log") or [])
        if e.get("kind") == "own"
    ]
    failed = [e for e in own if e.get("outcome") == "failed"]
    parts = [
        f"banked red +{_as_int(act.get('red_pts'))} "
        f"({_as_int(act.get('parcels'))} parcel(s))"
    ]
    if _as_int(act.get("blue_fissile")):
        parts.append(f"blue +{_as_int(act.get('blue_fissile'))} fissile")
    if _as_int(act.get("green_cells")):
        parts.append(f"{_as_int(act.get('green_cells'))} green")
    if failed:
        parts.append(f"{len(failed)} FAILED move(s)")
    return "; ".join(parts)


def enemy_summary(last_night_memory: Mapping[str, Any]) -> str:
    """One-line "what you saw the enemy do" from the LAST NIGHT memory dict."""
    lines = [
        str(x) for x in (last_night_memory.get("what_you_saw") or [])
        if str(x).strip()
    ]
    if not lines:
        return ""
    return " | ".join(lines[:3])[:220]


def enrich_prior_entry(
    prior_entry: Optional[Dict[str, Any]],
    last_night_memory: Mapping[str, Any],
    *,
    reflection: str = "",
) -> Optional[Dict[str, Any]]:
    """Fold engine-truth outcome + observed enemy (+ reflection) onto the prior
    day's entry so the journal record for that day is self-contained. Mutates and
    returns ``prior_entry`` (``None`` -> no-op)."""
    if prior_entry is None:
        return None
    happened = outcome_summary(last_night_memory)
    if happened:
        prior_entry["happened"] = happened
    enemy = enemy_summary(last_night_memory)
    if enemy:
        prior_entry["enemy_seen"] = enemy
    if reflection:
        prior_entry["reflection"] = reflection
    return prior_entry


# ── render the continuous thread ─────────────────────────────────────────────
def render_journal(entries: Sequence[Mapping[str, Any]]) -> str:
    """Format the memory entries into the continuous STRATEGY JOURNAL block."""
    rows = [e for e in (entries or []) if isinstance(e, Mapping)]
    rows.sort(key=lambda e: _as_int(e.get("day")))
    if not rows:
        return (
            "STRATEGY JOURNAL (your continuous thread): (no prior nights — this "
            "is your first turn; set a clear INTENT you can reflect on tomorrow.)"
        )
    out: List[str] = [
        "STRATEGY JOURNAL (your continuous thread — the INTENT you set, what "
        "HAPPENED, what you SAW, and your REFLECTION; extend it tonight):"
    ]
    for e in rows:
        day = e.get("day")
        intent = (
            str(e.get("intent") or "").strip()
            or str(e.get("plan_this_turn") or "").strip()
            or "(no intent recorded)"
        )
        out.append(f"  Day {day} INTENT: \"{intent}\"")
        happened = str(e.get("happened") or "").strip()
        if happened:
            out.append(f"         HAPPENED: {happened}")
        else:
            banked = e.get("actual_banked")
            if banked is not None:
                out.append(f"         HAPPENED: banked {_as_int(banked)} pts")
        enemy = str(e.get("enemy_seen") or "").strip()
        if enemy:
            out.append(f"         SAW: {enemy}")
        reflection = str(e.get("reflection") or "").strip()
        if reflection:
            out.append(f"         REFLECT: \"{reflection}\"")
    return "\n".join(out)
