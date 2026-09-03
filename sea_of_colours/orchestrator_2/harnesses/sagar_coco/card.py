"""The v12 CARD — one human-readable page per turn: the prompt the model was
handed, the reasoning it wrote back, and what that compiled to.

Two callers, one renderer
-------------------------
* ``scripts/advise_v12.py`` renders a card on demand for a session the harness
  re-runs read-only (a frozen snapshot, or a live seat you are playing).
  ``scripts/dump_suite_cards.py`` drives that path for the whole turn suite.
* The harness itself writes a card as it plays, when ``SOC_CARD_DUMP_DIR`` is
  set — see :func:`dump_if_enabled`.

The second path exists because a season night happens ONCE. The audit table
truncates ``prompt_excerpt`` at 32 KB, which on a v12 card does not even reach
the OPTION MENU, and the board cannot be re-derived afterwards without a full
record-replay. So if a season is worth reviewing, its cards have to be captured
while it runs.

Both callers share :func:`render` so a season card and a suite card are the same
artefact and can be read — or diffed — side by side.
"""

from __future__ import annotations

import json
import os
import re
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

_RULE = "=" * 72
_SUBRULE = "-" * 72

#: Round-trip latency is the only thing that differs between two runs of the
#: same board, so it is normalised out of dumped cards — a diff between two
#: archives then contains nothing but the effect of the change.
_LATENCY_MS = re.compile(r"\bms=\d+\b")

#: Directory to write season cards into. Unset = no dumping (the default), so
#: this costs nothing on a normal run.
_DUMP_ENV = "SOC_CARD_DUMP_DIR"


def _wrap(text: str, indent: str = "  ") -> str:
    out: List[str] = []
    for para in str(text or "").splitlines() or [""]:
        if not para.strip():
            out.append("")
            continue
        out.extend(
            textwrap.wrap(
                para, width=96, initial_indent=indent, subsequent_indent=indent,
            )
            or [indent]
        )
    return "\n".join(out)


def board_summary(agent_view: Mapping[str, Any]) -> str:
    """Compact read of what the harness actually saw (for correlation)."""
    red = agent_view.get("red_tiles") or []
    live = sum(1 for t in red if str(t.get("freshness") or "") == "fresh")
    echo = len(red) - live
    # Live redsign broadcasts live under the ``redsign`` (singular) key — the
    # ``redsigns``/``red_signs`` names never populate, so the header always read 0
    # even with a redsign live. Count the actual broadcast regions.
    signs = (
        agent_view.get("redsign")
        or agent_view.get("redsigns")
        or agent_view.get("red_signs")
        or []
    )
    # Enemy probes are surfaced via competitor_intel (public launches +
    # persistent echoes), NOT a top-level ``enemy_probes`` key — count them the
    # same way the ENEMY PROBES / SUPERSEDES blocks do so the header matches the
    # prompt (was always 0 before, even when supersede targets existed).
    _ci = agent_view.get("competitor_intel") or {}
    _probe_cells = {
        (int(r["at"][0]), int(r["at"][1]))
        for src in ("new_this_day", "persistent_echoes")
        for r in (_ci.get(src) or [])
        if isinstance(r, dict)
        and str(r.get("kind") or "").startswith("enemy_probe")
        and isinstance(r.get("at"), (list, tuple))
        and len(r["at"]) >= 2
    }
    enemy_probes = list(_probe_cells)
    probe_stock = (
        agent_view.get("probe_stock")
        or (agent_view.get("orbit") or {}).get("probe_stock")
        or 0
    )
    return (
        f"red_tiles={len(red)} (live={live} echo={echo})  "
        f"redsigns={len(signs)}  enemy_probes={len(enemy_probes)}  "
        f"probe_stock={probe_stock}"
    )


def _sub(extras: Mapping[str, Any], kind: str) -> Dict[str, Any]:
    for s in extras.get("sub_invocations") or []:
        if str(s.get("kind") or "") == kind:
            return s
    return {}


def render(
    *, session_id: str, seat: str, status: Mapping[str, Any],
    agent_view: Mapping[str, Any], res: Mapping[str, Any], full: bool,
    show_prompt: bool = False, seat_status_line: Optional[str] = None,
) -> str:
    """The card. ``seat_status_line`` overrides the advisor's pending/your-move
    header — a season dump is neither (the night is already played)."""
    extras = res.get("extras") or {}
    day = int(agent_view.get("meta", {}).get("day")
              or agent_view.get("hud", {}).get("day") or 0)
    cap = int(agent_view.get("hud", {}).get("season_day_cap") or 7)
    score = int((agent_view.get("hud", {}).get("scores") or {}).get(seat, 0))
    pending = bool((status.get("pending") or {}).get(seat, False))
    directive = extras.get("thinker_directive") or {}

    lines: List[str] = []
    lines.append(_RULE)
    lines.append(f"  v12 ADVISOR — {seat}  |  day {day}/{cap}  |  score {score}")
    lines.append(f"  session {session_id}")
    lines.append(
        "  seat status: " + (
            seat_status_line if seat_status_line is not None
            else ('ALREADY SUBMITTED (showing what v12 would have done)'
                  if pending else 'YOUR MOVE (not yet submitted)')
        )
    )
    lines.append(f"  board v12 saw: {board_summary(agent_view)}")
    lines.append(
        f"  thinker: api={extras.get('thinker_api') or '-'} "
        f"ms={extras.get('thinker_ms') or 0} "
        f"retried={extras.get('thinker_retried')}"
    )
    lines.append(_RULE)

    if show_prompt:
        # EXACTLY what the model receives, verbatim (includes the worldview,
        # doctrine, memory replay, and the options menu — the whole input).
        think_prompt = extras.get("thinker_prompt") or ""
        lines.append("")
        lines.append(_RULE)
        lines.append("  EXACTLY WHAT THE AGENT RECEIVES — THINK PROMPT (verbatim)")
        lines.append(_RULE)
        for pl in str(think_prompt).splitlines():
            lines.append(pl)
        plan_prompt = extras.get("plan_prompt") or ""
        if plan_prompt:
            lines.append("")
            lines.append(_SUBRULE)
            lines.append("  PLAN PROMPT (verbatim — sent after THINK, adds the think analysis)")
            lines.append(_SUBRULE)
            for pl in str(plan_prompt).splitlines():
                lines.append(pl)
    else:
        menu_block = extras.get("option_menu_block") or ""
        if menu_block:
            lines.append("")
            lines.append("OPTIONS OFFERED (heuristic-surfaced menu the thinker chose from):")
            for ml in str(menu_block).splitlines():
                lines.append(f"  {ml}" if ml.strip() else "")

    lines.append("")
    lines.append(_RULE)
    lines.append("  THE AGENT THINKING")
    lines.append(_RULE)
    lines.append("STAGE 1 — THINK (bounded reasoning):")
    think = extras.get("thinker_reasoning") or ""
    if not think:
        think = "(no THINK prose — thinker off, empty, or fell through to the mover)"
    lines.append(_wrap(think))

    lines.append("")
    lines.append("STAGE 2 — PLAN (the decision it committed to):")
    if directive:
        lines.append(f"  posture   : {directive.get('posture')}")
        if directive.get("plan"):
            lines.append(f"  plan IDs  : {', '.join(str(p) for p in directive['plan'])}")
        if directive.get("targets"):
            lines.append(f"  targets   : {directive['targets']}")
        if directive.get("avoid"):
            lines.append(f"  avoid     : {directive['avoid']}")
        lines.append(f"  chaff_react: {directive.get('chaff_react')}")
        if directive.get("situational"):
            lines.append(f"  situational: {directive['situational']}")
        if directive.get("note"):
            lines.append("  note:")
            lines.append(_wrap(directive["note"], indent="    "))
    else:
        lines.append("  (no structured directive — see raw PLAN below)")
    plan_raw = _sub(extras, "plan").get("response_text") or ""
    if plan_raw:
        lines.append("  raw PLAN JSON:")
        lines.append(_wrap(plan_raw, indent="    "))

    # Courtesy footer: what that reasoning actually compiled to, so we can
    # correlate the words with the moves when diagnosing.
    moves = res.get("moves") or []
    lines.append("")
    lines.append(_SUBRULE)
    lines.append(f"  -> compiled to {len(moves)} move(s)  "
                 f"[exec={'packager' if extras.get('packager_used') else 'mover'}"
                 f"{'; fallback' if extras.get('fallback_used') else ''}]")
    if extras.get("selected_option_ids"):
        lines.append(f"  -> selected options: {', '.join(str(o) for o in extras['selected_option_ids'])}")
    if extras.get("sanitizer_changes"):
        lines.append("  -> corrector/sanitizer:")
        for s in extras["sanitizer_changes"][:8]:
            lines.append(f"       · {s}")

    if full:
        lines.append("")
        lines.append(_SUBRULE)
        lines.append("  FULL detail")
        if extras.get("packager_log"):
            lines.append("  packager log:")
            for s in extras["packager_log"][:20]:
                lines.append(f"       · {s}")
        lines.append("  moves:")
        for m in moves:
            lines.append(f"       {json.dumps(m)}")

    lines.append(_RULE)
    return "\n".join(lines)


# ── season capture ───────────────────────────────────────────────────────
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(text: str) -> str:
    return _SAFE.sub("_", str(text or "")).strip("_") or "unknown"


def _label(session_id: str, season_name: str) -> str:
    """Directory name for this session: the season name when we have one
    (readable), always suffixed with the session id (unique — seasons get
    re-run under the same name)."""
    sid = _slug(session_id)[:12]
    return f"{_slug(season_name)}__{sid}" if season_name else sid


def dump_if_enabled(
    *, session_id: str, seat: str, agent_view: Mapping[str, Any],
    res: Mapping[str, Any], season_name: str = "",
) -> Optional[str]:
    """Write this turn's card to ``$SOC_CARD_DUMP_DIR`` when that is set.

    One file per seat per night: ``<dir>/<season>__<sid>/d<NN>_<seat>.txt``,
    holding the verbatim THINK and PLAN prompts, the reasoning, the committed
    plan and the compiled queue — the same page the advisor prints.

    Never raises. A capture problem must not cost a season a turn, so any
    failure is swallowed and the night proceeds.
    """
    root = os.environ.get(_DUMP_ENV, "").strip()
    if not root:
        return None
    try:
        day = int((agent_view.get("meta") or {}).get("day")
                  or (agent_view.get("hud") or {}).get("day") or 0)
        out_dir = Path(root) / _label(session_id, season_name)
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"d{day:02d}_{_slug(seat)}.txt"
        card = render(
            session_id=session_id, seat=seat, status={}, agent_view=agent_view,
            res=res, full=True, show_prompt=True,
            seat_status_line="LIVE SEASON — the card exactly as it was played",
        )
        dest.write_text(_LATENCY_MS.sub("ms=<normalised>", card) + "\n",
                        encoding="utf-8")
        return str(dest)
    except Exception:  # noqa: BLE001 — capture is never worth losing a turn over
        return None
