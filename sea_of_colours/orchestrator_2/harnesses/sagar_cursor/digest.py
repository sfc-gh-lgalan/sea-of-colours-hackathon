"""Attributed event digest — turn the raw event channels into causal prose.

Workstream A of the v10 comprehension uplift. ``view._last_night_recap`` now
emits three attributed channels on ``agent_view.last_night``:

  * ``incoming_attacks`` — probe kills / EMP / chaff done TO this seat, each
    with the attacker seat (``by``) and the ``consequence``.
  * ``my_denials``       — supersedes this seat inflicted ON opponents.
  * ``emp_scars``        — active EMP scars (cells + hours) as a board hazard.

Plus the pre-existing ``my_collisions`` (harvester simultaneous-drop pile-ups).

This module is pure string formatting over that structured data — no engine
access, no I/O. It exists so the prompt (SECTION 3) can show a two-way
"WHAT HAPPENED — to you & by you" narrative that draws the causal line for
the agent ("p2 superseded your probe -> you lost that disk -> the hot-drop
had no sensor -> 0 banked") instead of leaving it to confabulate a cause.
"""

from __future__ import annotations

from typing import Any, List, Mapping, Optional, Sequence


def _fmt_cell(at: Any) -> str:
    """Render an ``[x, y]`` cell, tolerating missing / malformed input."""
    if isinstance(at, (list, tuple)) and len(at) == 2 and at[0] is not None:
        try:
            return f"({int(at[0])},{int(at[1])})"
        except (TypeError, ValueError):
            return "a shared cell"
    return "a shared cell"


def _fmt_hours(hours: Sequence[Any]) -> str:
    """Compress a sorted hour list into contiguous ranges, e.g. ``9-11, 14``."""
    vals: List[int] = []
    for h in hours or []:
        try:
            vals.append(int(h))
        except (TypeError, ValueError):
            continue
    if not vals:
        return "?"
    vals = sorted(set(vals))
    spans: List[str] = []
    start = prev = vals[0]
    for v in vals[1:]:
        if v == prev + 1:
            prev = v
            continue
        spans.append(f"{start}-{prev}" if start != prev else f"{start}")
        start = prev = v
    spans.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ", ".join(spans)


def _fmt_seats(seats: Sequence[Any]) -> str:
    """Join a seat list for prose (``p2``, ``p2 & p3``, ``p2, p3 & p4``)."""
    names = [str(s) for s in (seats or []) if str(s)]
    if not names:
        return "a rival"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " & " + names[-1]


def narrate_incoming(agent_view: Mapping[str, Any]) -> List[str]:
    """Causal lines for everything done TO this seat last night."""
    ln = agent_view.get("last_night") or {}
    lines: List[str] = []

    for a in ln.get("incoming_attacks") or []:
        if not isinstance(a, Mapping):
            continue
        atype = str(a.get("type", ""))
        by = _fmt_seats(a.get("by"))
        cons = str(a.get("consequence") or "").strip()
        if atype == "emp_hit":
            unit = str(a.get("unit") or "a harvester")
            hrs = _fmt_hours(a.get("hours"))
            lines.append(
                f"{by} EMP-smothered {unit} at hour(s) {hrs} -> {cons}"
            )
        elif atype == "chaff_jam":
            hrs = _fmt_hours(a.get("hours"))
            units = a.get("units") or []
            u = f" ({_fmt_seats(units)})" if units else ""
            lines.append(
                f"{by} chaff-jammed your actions{u} at hour(s) {hrs} -> {cons}"
            )
        elif atype == "probe_superseded":
            cell = _fmt_cell(a.get("at"))
            lost = ", ".join(str(x) for x in (a.get("lost_ids") or [])) or "a probe"
            lines.append(
                f"{by} dropped a probe onto your {lost} at {cell}, "
                f"destroying it -> {cons}"
            )
        elif atype == "probe_collision":
            cell = _fmt_cell(a.get("at"))
            lines.append(
                f"your probe mutually annihilated with {by}'s probe at {cell} "
                f"-> {cons}"
            )

    # Harvester simultaneous-drop pile-ups (the pre-existing keystone channel).
    for c in ln.get("my_collisions") or []:
        if not isinstance(c, Mapping):
            continue
        cell = _fmt_cell(c.get("at"))
        others = c.get("others") or []
        who = f" with {_fmt_seats(others)}" if others else ""
        lines.append(
            f"your harvester COLLIDED at {cell}{who} -> banked ZERO; the cargo "
            f"was LOST, not held in hoard (the advertised cell was a "
            f"mutual-kill zone)"
        )
    return lines


def narrate_losses(agent_view: Mapping[str, Any]) -> List[str]:
    """Causal lines for YOUR OWN assets destroyed last night.

    Renders ``last_night.my_assets_destroyed`` — the channel the RULEBOOK tells
    the agent to READ but the LAST NIGHT block never surfaced, so a lost
    harvester + spilled cargo was under-reported as a mere "pickup cancelled"
    (the day-5 Aurora loss the agent narrated only as a chaff jam). Each entry
    carries ``id`` / ``kind`` / ``at`` / ``reason``.
    """
    ln = agent_view.get("last_night") or {}
    lines: List[str] = []
    for d in ln.get("my_assets_destroyed") or []:
        if not isinstance(d, Mapping):
            continue
        kind = str(d.get("kind") or "unit")
        uid = str(d.get("id") or kind)
        cell = _fmt_cell(d.get("at"))
        reason = str(d.get("reason") or "unspecified").strip()
        if kind == "harvester":
            lines.append(
                f"you LOST {uid} at {cell} — {reason}. Its cargo SPILLED "
                f"(that unit banked ZERO) and the harvester is GONE from your "
                f"fleet until you rebuild it in orbit — do NOT count its load as "
                f"held."
            )
        elif kind == "probe":
            lines.append(
                f"you LOST {uid} ({kind}) at {cell} — {reason}; that disk's "
                f"vision is gone."
            )
        else:
            lines.append(f"you LOST {uid} ({kind}) at {cell} — {reason}.")
    return lines


def narrate_denials(agent_view: Mapping[str, Any]) -> List[str]:
    """Causal lines for everything this seat did TO opponents last night."""
    ln = agent_view.get("last_night") or {}
    lines: List[str] = []
    for d in ln.get("my_denials") or []:
        if not isinstance(d, Mapping):
            continue
        dtype = str(d.get("type", ""))
        cell = _fmt_cell(d.get("at"))
        cons = str(d.get("consequence") or "").strip()
        against = _fmt_seats(d.get("against"))
        if dtype == "probe_superseded":
            lines.append(
                f"you superseded {against}'s probe at {cell} -> {cons}"
            )
    return lines


def narrate_scars(agent_view: Mapping[str, Any]) -> List[str]:
    """Board-hazard lines for EMP scars still live this coming night."""
    ln = agent_view.get("last_night") or {}
    lines: List[str] = []
    for s in ln.get("emp_scars") or []:
        if not isinstance(s, Mapping):
            continue
        cell = _fmt_cell(s.get("at"))
        hrs = _fmt_hours(s.get("hours"))
        lines.append(f"{cell} scarred at hour(s) {hrs}")
    return lines


def format_event_digest_block(agent_view: Mapping[str, Any]) -> str:
    """SECTION 3 two-way digest: what happened TO you and BY you last night.

    Returns "" when nothing attributable happened, so the prompt can skip the
    block entirely on a quiet night.
    """
    incoming = narrate_incoming(agent_view)
    denials = narrate_denials(agent_view)
    if not incoming and not denials:
        return ""
    out: List[str] = ["WHAT HAPPENED LAST NIGHT (cause -> effect):"]
    if incoming:
        out.append("  TO YOU (attacks + losses — learn the lesson):")
        for line in incoming[:6]:
            out.append(f"    - {line}")
    if denials:
        out.append("  BY YOU (denial you inflicted — press the advantage):")
        for line in denials[:6]:
            out.append(f"    - {line}")
    return "\n".join(out) + "\n"


def format_emp_scars_block(agent_view: Mapping[str, Any]) -> str:
    """SECTION 2 hazard block: EMP scars to route drops/steps around."""
    scars = narrate_scars(agent_view)
    if not scars:
        return ""
    out = [
        "EMP SCARS (still live — route drops/steps AROUND these; a unit "
        "caught inside is disabled and risks a dawn crash):"
    ]
    for line in scars[:8]:
        out.append(f"  {line}")
    return "\n".join(out) + "\n"


def format_self_execution_block(
    prior_day_entry: Optional[Mapping[str, Any]],
    agent_view: Mapping[str, Any],
) -> str:
    """SECTION 3 — the agent/corrector/engine rendition of YOUR OWN last night.

    The event digest above narrates what RIVALS did. This block narrates what
    *you* did: the plan you committed, what the CORRECTOR changed and why, and
    what actually EXECUTED / banked per the engine. It is the anti-confabulation
    anchor — on a night where the corrector stripped an undroppable plan the
    agent otherwise narrates a phantom harvest (the day-4 ``6 RED parcels`` that
    never happened). Reads only fields the v10 harness stamps onto the memory
    entry (``plan_ids`` / ``planned_cells`` / ``corrector_notes`` /
    ``moves_executed``) plus the engine's ``my_parcels_banked``. Returns ``""``
    when there is no prior-day record (day 1) or the entry predates this feature.
    """
    e = prior_day_entry or {}
    plan_ids = e.get("plan_ids") or []
    planned = e.get("planned_cells") or []
    corrector = e.get("corrector_notes") or []
    compiler = e.get("compiler_notes") or []
    moves_exec = e.get("moves_executed")
    banked = e.get("actual_banked")
    # Nothing stamped and no outcome → old-shape entry / genuinely no record.
    if not (plan_ids or planned or corrector or compiler) and moves_exec is None:
        return ""

    ln = agent_view.get("last_night") or {}
    parcels = [b for b in (ln.get("my_parcels_banked") or []) if isinstance(b, Mapping)]
    day = e.get("day")

    out: List[str] = [
        f"YOUR OWN LAST NIGHT — PLAN vs COMPILER vs CORRECTOR vs EXECUTED "
        f"(day {day}). This is the engine record of what YOU did; reconcile "
        "your reflection to it:"
    ]

    if planned:
        out.append("  YOU COMMITTED: " + "; ".join(str(p) for p in planned[:6]))
    elif plan_ids:
        out.append("  YOU COMMITTED: " + ", ".join(str(p) for p in plan_ids[:6]))
    else:
        out.append("  YOU COMMITTED: (no deploy plan — heuristic/empty turn)")

    if moves_exec == 0:
        out.append(
            "  EXECUTED: NOTHING — 0 moves reached the board. You harvested "
            "nothing last night. Do NOT claim any harvest, drop, or banked "
            "parcel; there was none."
        )
    elif parcels:
        cells = ", ".join(_fmt_cell(b.get("from")) for b in parcels[:6])
        out.append(
            f"  EXECUTED & BANKED: harvested {len(parcels)} parcel(s) at {cells} "
            "(engine truth — quote THESE cells, not remembered ones)."
        )
    elif moves_exec is not None:
        out.append(
            f"  EXECUTED: {moves_exec} move(s) submitted, but 0 parcels banked to "
            "the hoard last night."
        )

    if compiler:
        out.append(f"  THE COMPILER CHANGED YOUR NIGHT ({len(compiler)} edit(s)):")
        for c in compiler[:5]:
            out.append(f"    - {c}")
        out.append(
            "    These are edits to WHAT was attempted, made after you chose. A "
            "relocated drop, a truncated walk, a run refused outright, or a "
            "harvester/probe spent on something you did not ask for. Read them "
            "before you reflect: the night that ran may not be the night you "
            "designed, and next turn's menu is the place to correct it."
        )

    if corrector:
        out.append(f"  THE CORRECTOR REWROTE YOUR PLAN ({len(corrector)} change(s)):")
        for c in corrector[:5]:
            out.append(f"    - {c}")
        out.append(
            "    Cause: a move was illegal as written — a drop with no LIVE probe "
            "on the cell (echo/memory is not enough), a green hazard, or a "
            "friendly same-cell collision. If EXECUTED is NOTHING, your whole "
            "plan was undroppable — say so and pick a reachable target next time."
        )

    if banked is not None:
        out.append(f"  NET TO VAULT: {int(banked)} pts banked this resolve.")

    out.append(
        "  => Reconcile: your reflection MUST match the EXECUTED line. Never "
        "narrate a harvest that is not listed there."
    )
    return "\n".join(out) + "\n"
