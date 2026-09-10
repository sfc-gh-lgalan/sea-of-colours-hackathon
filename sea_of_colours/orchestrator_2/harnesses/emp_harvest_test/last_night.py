"""tabula_v11 — MEMORY OF LAST NIGHT (Section 3, single elegant block).

This is component (2) of the v11 prompt north star: a concise, scannable memory
of last night that fuses FOUR engine-truth sources into one block —

  * YOU ORDERED     — the exact queue you submitted (engine truth), with the
                      numeric expected yield you committed to at submit time.
  * EXECUTION LOG   — the per-hour timeline of what ACTUALLY happened to your
                      units (drop/step/pickup/probe + ok/FAILED + reason).
  * YIELD           — realized RED/BLUE/GREEN, scored with the ENGINE model, put
                      side-by-side with what you expected.
  * WHAT YOU SAW    — public orbital events + enemy field moves that fell inside
                      your live vision + your losses/denials.
  * REFLECT         — a short, directed reflection anchored to the log above so
                      the agent cannot confabulate a harvest that never happened.

HARNESS-ONLY, NO ENGINE EDITS. The faithful per-hour timeline is NOT on the
agent view (it is stripped from the persisted session blob). Instead we READ the
already-persisted replay frames — one immutable frame per executed/wasted move —
straight from the store via ``store.list_replay_frames(session_id, day, day)``
and reconstruct the observer-filtered log here. Each frame carries ``hour`` /
``owner`` / ``tag`` / ``attempted`` (structured, e.g. ``drop harvester_p1
@(29,16)``) / ``outcome`` / ``caption`` and the per-seat visibility snapshot
``cells_player_{seat}`` (row-major dense percept). The opening frame carries the
submitted ``scheduled_orders`` for the seat.

Visibility contract (RULEBOOK §3.15 / §5.1):
  * your OWN moves          — always shown (with outcome + reason).
  * PUBLIC orbital events   — probe launches / EMP salvos / chaff flares are
                              public, so any seat's are shown.
  * ENEMY field moves       — a rival's step/drop/pickup/mine-lay is shown ONLY
                              when the acted cell was inside YOUR live vision that
                              hour; otherwise it is withheld.

If no frames are available (day 1, or an older session that predates the replay
sidecar) the block degrades gracefully to the ``agent_view.last_night`` channels.
Pure functions over (frames, agent_view, prior_day_entry); the store read is the
only I/O and is isolated in :func:`render`.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test import (
    digest,
    option_economics as econ,
)

Cell = Tuple[int, int]

# Own-move tags that describe a field/orbital ACTION worth logging (own always).
_OWN_ACTION_TAGS = {
    "drop", "step", "pickup", "probe", "mine_lay",
    "emp_launch", "chaff_flare", "wait",
    # v13 — SNAP is a real per-hour action (§4.9.4) and the seat needs to see
    # it in its own log. Without this tag in the set the frame is dropped
    # and the reflection block reads "I ordered a SNAP that never executed"
    # when the wire move did in fact land — a self-report that corrupts
    # tomorrow's journal.
    "snap",
    # failure/interdiction outcomes on your OWN units — learning signal.
    "waste", "empd", "damaged",
    # v13 — SNAP-hit outcomes on your own units, for the same reason as
    # ``empd``: the seat needs to see the interdiction, not the underlying
    # move that failed to resolve.
    "snapped",
}
# Tags that are PUBLIC when a rival does them (RULEBOOK §5.1 / §3.15 / §4.9.4).
_PUBLIC_ORBITAL_TAGS = {"probe", "emp_launch", "chaff_flare", "snap"}
# A rival's FIELD moves — shown only when the cell fell in your live vision.
_ENEMY_FIELD_TAGS = {"step", "drop", "pickup", "mine_lay"}
# Frames that are scaffolding, not a per-hour action.
_SKIP_TAGS = {"open", "dawn", "emp_decay"}

_CELL_RE = re.compile(r"\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)")


# ── small helpers ──────────────────────────────────────────────────────────
def _as_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _last_cell(text: Any) -> Optional[Cell]:
    """The LAST ``(x,y)`` in a frame's ``attempted``/``caption`` string.

    ``attempted`` is structured (``drop harvester_p1 @(29,16)`` / ``step
    harvester_p1 → (28,16)`` / ``probe @(20,10)``) so the last coord pair is the
    action's target cell. ``pickup`` has none -> ``None``.
    """
    if not isinstance(text, str):
        return None
    matches = _CELL_RE.findall(text)
    if not matches:
        return None
    x, y = matches[-1]
    try:
        return int(x), int(y)
    except (TypeError, ValueError):
        return None


def _fmt_cell(cell: Optional[Cell]) -> str:
    if cell is None:
        return ""
    return f"({cell[0]},{cell[1]})"


def _visible_cells(
    frame: Mapping[str, Any], seat: str, width: int, height: int,
) -> Optional[Set[Cell]]:
    """The set of cells ``seat`` had LIVE vision on in ``frame``.

    Read from the row-major ``cells_player_{seat}`` dense percept: a cell is
    live-visible iff its row is fresh terrain (``kind == "terrain"`` and not
    ``stale``). Coordinates are recovered from the row-major index because the
    packed rows do not carry x/y. Returns ``None`` when the snapshot is absent
    or malformed — the caller then WITHHOLDS enemy detail (fail-closed on fog).
    """
    rows = frame.get(f"cells_player_{seat}")
    if not isinstance(rows, list) or not rows:
        rows = frame.get("cells_player")
    if not isinstance(rows, list) or len(rows) != width * height:
        return None
    out: Set[Cell] = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, Mapping):
            continue
        if row.get("kind") == "terrain" and not row.get("stale"):
            out.add((idx % width, idx // width))
    return out


# ── execution log (from replay frames) ─────────────────────────────────────
def read_execution_log(
    frames: Sequence[Mapping[str, Any]],
    player: str,
    *,
    width: int,
    height: int,
) -> List[Dict[str, Any]]:
    """Observer-filtered per-hour log for ``player`` from replay ``frames``.

    Entry shape: ``{hour, kind, seat, tag, cell, outcome, reason, text}`` where
    ``kind`` is ``"own"`` / ``"orbital"`` (public enemy orbital) / ``"enemy"``
    (enemy field seen in your vision). Ordered by (hour, frame order).
    """
    out: List[Dict[str, Any]] = []
    for frame in frames or []:
        if not isinstance(frame, Mapping):
            continue
        tag = str(frame.get("tag") or "")
        if tag in _SKIP_TAGS or not tag:
            continue
        owner = frame.get("owner")
        if owner is None:
            continue  # collision/system frames — surfaced via the digest instead
        owner = str(owner)
        hour = _as_int(frame.get("hour"), 0)
        outcome = str(frame.get("outcome") or "")
        attempted = frame.get("attempted")
        caption = str(frame.get("caption") or "")
        cell = _last_cell(attempted) or _last_cell(caption)

        if owner == str(player):
            if tag not in _OWN_ACTION_TAGS:
                continue
            reason = caption if outcome == "failed" else ""
            out.append({
                "hour": hour, "kind": "own", "seat": owner, "tag": tag,
                "cell": cell, "outcome": outcome or "ok", "reason": reason,
                "text": str(attempted or caption),
            })
            continue

        # A rival acted. Public orbital events are always visible.
        if tag in _PUBLIC_ORBITAL_TAGS:
            out.append({
                "hour": hour, "kind": "orbital", "seat": owner, "tag": tag,
                "cell": cell, "outcome": outcome or "ok", "reason": "",
                "text": str(attempted or caption),
            })
            continue

        # A rival FIELD move — withhold unless it fell in your live vision.
        if tag in _ENEMY_FIELD_TAGS and cell is not None:
            visible = _visible_cells(frame, str(player), width, height)
            if visible is not None and cell in visible:
                out.append({
                    "hour": hour, "kind": "enemy", "seat": owner, "tag": tag,
                    "cell": cell, "outcome": outcome or "ok", "reason": "",
                    "text": str(attempted or caption),
                })
    out.sort(key=lambda e: (int(e.get("hour") or 0),))
    return out


def read_orders(
    frames: Sequence[Mapping[str, Any]], player: str,
) -> List[str]:
    """Your submitted queue for last night, from the opening frame.

    Reads the opening frame's ``scheduled_orders[player]`` (each row carries a
    human ``label`` such as ``drop harvester_p1 @(29,16)``). Empty when there is
    no opening frame (the caller then falls back to the memory entry).
    """
    for frame in frames or []:
        if not isinstance(frame, Mapping):
            continue
        if str(frame.get("tag") or "") != "open":
            continue
        sched = frame.get("scheduled_orders")
        if not isinstance(sched, Mapping):
            return []
        rows = sched.get(str(player)) or []
        labels: List[str] = []
        for r in rows:
            if isinstance(r, Mapping):
                lab = str(r.get("label") or "").strip()
                if lab and r.get("action") != "invalid":
                    labels.append(lab)
        return labels
    return []


# ── realized yield ──────────────────────────────────────────────────────────
# generator.Tile: EMPTY=0, GREEN=1, RED=2, BLUE=3.
_TILE_NAME = {0: "EMPTY", 1: "GREEN", 2: "RED", 3: "BLUE"}


def _tile_name(raw: Any) -> str:
    """Normalise a parcel's tile (int enum or name string) to RED/BLUE/GREEN."""
    if isinstance(raw, str):
        return raw.strip().upper()
    try:
        return _TILE_NAME.get(int(raw), "")
    except (TypeError, ValueError):
        return ""


def _score_parcels(parcels: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Aggregate a list of banked parcels into engine-scored R/B/G totals.

    Uses the same scoring model as :mod:`option_economics` so expected (menu)
    and actual (banked) are directly comparable. Tolerates both parcel shapes:
    the view's ``my_parcels_banked`` (``tile``/``purity``/``from``) and the
    replay-frame hoard site (``tile_at_harvest``/``purity_at_harvest``/``cell``).
    """
    red_pts = 0.0
    red_tiers: Dict[str, int] = {}
    blue_fissile = 0
    green_cells = 0
    cells: List[Any] = []
    n = 0
    for p in parcels:
        if not isinstance(p, Mapping):
            continue
        n += 1
        tile = _tile_name(p.get("tile", p.get("tile_at_harvest")))
        purity = _as_int(
            p.get("purity", p.get("purity_at_harvest", p.get("value"))), 0
        )
        cells.append(tuple(p.get("from") or p.get("cell") or (None, None)))
        if tile == "RED":
            red_pts += econ._red_ship_points(purity)
            t = econ._tier(purity)
            red_tiers[t] = red_tiers.get(t, 0) + 1
        elif tile == "BLUE":
            blue_fissile += purity
        elif tile == "GREEN":
            green_cells += 1
    return {
        "parcels": n,
        "red_pts": int(round(red_pts)),
        "red_tiers": red_tiers,
        "blue_fissile": int(blue_fissile),
        "green_penalty": -econ.GREEN_ENDGAME_PENALTY * green_cells,
        "green_cells": green_cells,
        "cells": cells,
    }


def _hoard_sites(frame: Mapping[str, Any], player: str) -> List[Mapping[str, Any]]:
    hoard = frame.get("hoard")
    if not isinstance(hoard, Mapping):
        return []
    seat = hoard.get(str(player))
    sites = seat.get("sites") if isinstance(seat, Mapping) else None
    return [s for s in sites if isinstance(s, Mapping)] if isinstance(sites, list) else []


def frames_report_hoard(
    frames: Optional[Sequence[Mapping[str, Any]]], player: str,
) -> bool:
    """Did the replay actually carry a hoard readout for this seat?

    R2.4. ``banked_from_frames`` returns ``[]`` both when the hoard did not grow
    (a real, meaningful zero) and when there were no frames to read (missing
    data). Collapsing those two is what let a night that banked NOTHING be
    recorded as a night that banked everything: the empty delta was treated as
    "no data" and the caller fell through to the harvest-time channel. A seat
    key present in any frame's ``hoard`` means the readout happened, whatever it
    said, so an empty delta from here on is authoritative.
    """
    for f in frames or []:
        if not isinstance(f, Mapping):
            continue
        hoard = f.get("hoard")
        if isinstance(hoard, Mapping) and isinstance(hoard.get(str(player)), Mapping):
            return True
    return False


def harvested_from_view(
    agent_view: Mapping[str, Any],
) -> List[Mapping[str, Any]]:
    """The view's harvest-time channel — parcels PICKED UP, not necessarily kept.

    Reported when the harvester touches the cell, so it still lists cargo that
    was later spilled by a collision, a jam or a dawn crash. Useful only to say
    "you dug this and then lost it"; never as a stand-in for what banked.
    """
    ln = agent_view.get("last_night") or {}
    return [p for p in (ln.get("my_parcels_banked") or []) if isinstance(p, Mapping)]


def banked_from_frames(
    frames: Sequence[Mapping[str, Any]], player: str,
) -> List[Mapping[str, Any]]:
    """Parcels that ENTERED your hoard last night = dawn hoard − opening hoard.

    Diffed by parcel id, so it is independent of the ``harvested_on_planning_day``
    stamp that the view's ``my_parcels_banked`` filter depends on (that filter can
    come back empty even when the hoard clearly grew — the bug this routes around).
    The opening frame (``tag == "open"``) holds the pre-night hoard; the final
    frame holds the post-night hoard. Every frame carries a hoard snapshot, so
    the last frame is authoritative for the end state.
    """
    if not frames:
        return []
    open_ids: Set[str] = set()
    for f in frames:
        if str(f.get("tag") or "") == "open":
            for s in _hoard_sites(f, player):
                sid = str(s.get("id") or "")
                if sid:
                    open_ids.add(sid)
            break
    final_sites: List[Mapping[str, Any]] = []
    for f in frames:
        sites = _hoard_sites(f, player)
        if sites:
            final_sites = sites  # ends on the last frame carrying a hoard (dawn)
    return [
        s for s in final_sites
        if str(s.get("id") or "") and str(s.get("id") or "") not in open_ids
    ]


def _harvest_by_cell(
    parcels: Sequence[Mapping[str, Any]],
) -> Dict[Cell, Dict[str, Any]]:
    """Map each harvested cell -> the parcel banked there last night.

    Lets the EXECUTION LOG annotate every drop/step with what it ACTUALLY banked
    (``harvested RED value 103 (vein) — now SYNTHETIC-GREEN in your wake``). This
    grounds reflection in engine truth per-cell: the agent reads its OWN harvest
    wake as the success it was, and cannot confabulate a green -100 penalty from
    cells that are green NOW only because it stripped the red off them last night.
    """
    out: Dict[Cell, Dict[str, Any]] = {}
    for p in parcels:
        if not isinstance(p, Mapping):
            continue
        raw = p.get("from") or p.get("cell")
        if not (isinstance(raw, (list, tuple)) and len(raw) >= 2):
            continue
        try:
            cell = (int(raw[0]), int(raw[1]))
        except (TypeError, ValueError):
            continue
        out[cell] = {
            "tile": _tile_name(p.get("tile", p.get("tile_at_harvest"))),
            "purity": _as_int(
                p.get("purity", p.get("purity_at_harvest", p.get("value"))), 0
            ),
        }
    return out


def _banked_parcels(
    agent_view: Mapping[str, Any],
    frames: Optional[Sequence[Mapping[str, Any]]],
    player: str,
) -> List[Mapping[str, Any]]:
    """Last night's banked parcels — replay hoard delta, view fallback.

    R2.4 — the delta wins even when it is EMPTY, provided the frames carried a
    hoard readout at all. Banking nothing is a real outcome and has to be
    sayable; the fallback exists for missing data, not for bad news.
    """
    if frames and player:
        banked = [
            b for b in banked_from_frames(frames, player) if isinstance(b, Mapping)
        ]
        if banked or frames_report_hoard(frames, player):
            return banked
    return harvested_from_view(agent_view)


def actual_yield(
    agent_view: Mapping[str, Any],
    frames: Optional[Sequence[Mapping[str, Any]]] = None,
    player: str = "",
) -> Dict[str, Any]:
    """Aggregate last night's banked parcels into engine-scored R/B/G.

    Uses the authoritative replay-frame hoard delta (what actually entered the
    hoard last night). Falls back to the view's harvest-time channel ONLY when
    the frames carried no hoard readout — an empty delta is a real zero (R2.4).
    """
    return _score_parcels(_banked_parcels(agent_view, frames, player))


def lost_in_transit(
    agent_view: Mapping[str, Any],
    frames: Optional[Sequence[Mapping[str, Any]]],
    player: str,
) -> Dict[str, Any]:
    """What you HARVESTED last night that never reached the berth.

    R2.4. The two channels disagree exactly when cargo was dug and then spilled —
    a collision, a chaff jam, a dawn crash. That gap is the most instructive fact
    of the night and used to be invisible, because the harvest channel was quietly
    substituted for the banked one and the loss read as a win. Scored so the card
    can name the size of it.
    """
    if not (frames and player and frames_report_hoard(frames, player)):
        return {}
    banked_ids = {
        str(p.get("id") or "") for p in banked_from_frames(frames, player)
    }
    lost = [
        p for p in harvested_from_view(agent_view)
        if str(p.get("id") or p.get("square_id") or "") not in banked_ids
    ]
    return _score_parcels(lost) if lost else {}


# ── formatting ──────────────────────────────────────────────────────────────
def _fmt_expected(expected: Optional[Mapping[str, Any]]) -> str:
    if not expected:
        return ""
    red = _as_int(expected.get("red_pts"), 0)
    blue = _as_int(expected.get("blue_fissile"), 0)
    green = _as_int(expected.get("green_penalty"), 0)
    bits = [f"red ~+{red}"]
    if blue:
        bits.append(f"blue +{blue} fissile")
    if green:
        bits.append(f"green {green}")
    return " · ".join(bits)


def _fmt_red_tiers(tiers: Mapping[str, int]) -> str:
    order = ("pure", "mass", "vein", "trace")
    parts = [f"{tiers[t]} {t}" for t in order if tiers.get(t)]
    return ", ".join(parts)


def _fmt_harvest(harv: Mapping[str, Any]) -> str:
    """One-line 'what this step banked + its wake' annotation for the log."""
    tile = str(harv.get("tile") or "").upper()
    purity = _as_int(harv.get("purity"), 0)
    if tile == "RED":
        pts = int(round(econ._red_ship_points(purity)))
        return (
            f"harvested RED value {pts} ({econ._tier(purity)} p{purity}) "
            "— now SYNTHETIC-GREEN in your wake"
        )
    if tile == "BLUE":
        return f"harvested BLUE {purity} fissile — now SYNTHETIC-GREEN in your wake"
    if tile == "GREEN":
        return "stepped GREEN — banked a -100 endgame parcel (a real mistake)"
    return ""


def _log_line(e: Mapping[str, Any]) -> str:
    hour = _as_int(e.get("hour"), 0)
    tag = str(e.get("tag") or "")
    cell = e.get("cell")
    outcome = str(e.get("outcome") or "ok")
    cell_s = f" {_fmt_cell(cell)}" if cell else ""
    verdict = "ok" if outcome != "failed" else "FAILED"
    line = f"  H{hour:02d} {tag}{cell_s}".ljust(30) + f" {verdict}"
    reason = str(e.get("reason") or "").strip()
    if outcome == "failed" and reason:
        # Keep just the human tail after the seat prefix, trimmed.
        tail = reason.split(":", 1)[-1].strip() if ":" in reason else reason
        line += f"  {tail[:70]}"
        return line
    # A successful drop/step auto-harvests the parcel under it — surface WHAT it
    # banked and that the cell is now the agent's own (harmless) green wake.
    harv = e.get("harvest")
    if isinstance(harv, Mapping):
        note = _fmt_harvest(harv)
        if note:
            line += f"  · {note}"
    return line


def format_block(memory: Mapping[str, Any]) -> str:
    """Render the assembled memory dict into the SECTION 3 block."""
    day = memory.get("day")
    orders: List[str] = list(memory.get("orders") or [])
    own_log = [e for e in (memory.get("execution_log") or []) if e.get("kind") == "own"]
    seen_log = [e for e in (memory.get("execution_log") or []) if e.get("kind") != "own"]
    act = memory.get("actual") or {}
    expected = memory.get("expected")
    intent = str(memory.get("intent") or "").strip()
    what_you_saw: List[str] = list(memory.get("what_you_saw") or [])
    degraded = bool(memory.get("degraded"))

    out: List[str] = [
        f"LAST NIGHT (day {day}) — YOUR ORDERS, WHAT HAPPENED, WHAT YOU SAW.",
        "(Learn from this: reconcile your plan to the ENGINE LOG below; never "
        "claim a harvest that is not listed there.)",
    ]

    # YOUR INTENT (what you told yourself you'd do last night).
    if intent:
        out.append(f'YOUR INTENT WAS: "{intent}"')

    # YOU ORDERED (+ expected).
    exp_s = _fmt_expected(expected)
    header = "YOU ORDERED" + (f"  (expected: {exp_s})" if exp_s else "") + ":"
    out.append(header)
    if orders:
        for o in orders[:8]:
            out.append(f"  {o}")
    else:
        out.append("  (no orders on record)")

    # PACKAGER RECONCILIATION — the runs/probes the packager dropped to fit your
    # live inventory, and why. This is the PLAN -> COMPILED -> DROPPED loop: it
    # teaches the agent to plan within inventory (RULEBOOK §3.9.2).
    dropped = [
        r for r in (memory.get("reconciliation") or [])
        if isinstance(r, Mapping) and r.get("status") == "dropped"
    ]
    if dropped:
        out.append(
            "PACKAGER RECONCILED YOUR PLAN (you asked for more than your "
            "inventory allows — each harvester makes ONE outing/night, §3.9.2):"
        )
        for r in dropped[:6]:
            rid = str(r.get("id") or "?")
            val = _as_int(r.get("value"), 0)
            reason = str(r.get("reason") or "").strip()
            out.append(f"  DROPPED [{rid}] (value ~{val}) — {reason}")
        out.append(
            "  => Next time plan WITHIN inventory: at most (harvesters alive) "
            "runs and (probe stock) probes, richest-value first."
        )

    # EXECUTION LOG (own, per hour).
    out.append("EXECUTION LOG (per hour — engine truth):")
    if own_log:
        for e in own_log[:24]:
            out.append(_log_line(e))
    elif degraded:
        # An empty log with no frames behind it is a MISSING RECORD, not a
        # failed night: the YIELD line two rows down is read from the session
        # blob and is routinely non-zero here. The old wording asserted the
        # night had failed, and the agent reasoned from it — spending its
        # reflection diagnosing a phantom execution bug (OBS-28).
        out.append(
            "  (unavailable — the per-hour engine replay for last night is "
            "missing from this session's record. This is NOT a report that "
            "your moves failed; read the YIELD line below for what you "
            "actually banked.)"
        )
    else:
        out.append("  (nothing executed — 0 of your moves reached the board)")

    # YIELD actual vs expected.
    red_tiers_s = _fmt_red_tiers(act.get("red_tiers") or {})
    tiers_suffix = f" ({red_tiers_s})" if red_tiers_s else ""
    exp_red = _as_int((expected or {}).get("red_pts"), 0) if expected else None
    exp_suffix = f"  [exp ~+{exp_red}]" if exp_red is not None else ""
    out.append("YIELD (actual vs expected):")
    out.append(
        f"  red +{_as_int(act.get('red_pts'), 0)}{tiers_suffix}{exp_suffix}"
        f"   blue +{_as_int(act.get('blue_fissile'), 0)} fissile"
        f"   green {_as_int(act.get('green_penalty'), 0)}"
        f"   ({_as_int(act.get('parcels'), 0)} parcel(s) banked)"
    )
    # R2.4 — cargo dug and then spilled. This line used to be impossible to
    # print: the harvest-time channel was substituted for the banked one, so a
    # total loss rendered as a full success and the reflection celebrated it.
    lost = memory.get("lost") or {}
    if _as_int(lost.get("parcels"), 0):
        lost_tiers = _fmt_red_tiers(lost.get("red_tiers") or {})
        lost_suffix = f" ({lost_tiers})" if lost_tiers else ""
        out.append(
            f"  LOST IN TRANSIT: {_as_int(lost.get('parcels'), 0)} parcel(s) "
            f"worth red +{_as_int(lost.get('red_pts'), 0)}{lost_suffix}"
            f"   blue +{_as_int(lost.get('blue_fissile'), 0)} fissile — "
            "HARVESTED but never banked. Find the hour in the log above (a "
            "collision, a jam, or a unit still on the surface at dawn) and say "
            "so; do NOT count this as yield."
        )

    # WHAT YOU SAW.
    if what_you_saw:
        out.append("WHAT YOU SAW (your live vision + public orbital):")
        for line in what_you_saw[:8]:
            out.append(f"  {line}")

    # REFLECT.
    out.append(
        "REFLECT (<=3 sentences, anchored to the log above): did the outcome "
        "match your expectation? name the exact hour/cause that broke it (a "
        "FAILED line, a collision, a jam) — do NOT call lost cargo 'held'. State "
        "ONE concrete change for tonight."
    )
    if degraded:
        out.append(
            "  (note: the per-hour engine replay was unavailable, so the log "
            "above is reconstructed from the summary channels.)"
        )
    return "\n".join(out) + "\n"


# ── enemy-facing "what you saw" lines ───────────────────────────────────────
def live_redsign_centres(agent_view: Mapping[str, Any]) -> List[List[int]]:
    """Smear centres of every redsign LIVE for this seat right now.

    Persisted onto the night's memory entry so the NEXT night can diff it — see
    :func:`_dark_beacons`.
    """
    out: List[List[int]] = []
    for r in (agent_view.get("redsign") or []):
        if not isinstance(r, Mapping):
            continue
        c = r.get("center")
        if isinstance(c, (list, tuple)) and len(c) >= 2:
            try:
                out.append([int(round(float(c[0]))), int(round(float(c[1])))])
            except (TypeError, ValueError):
                continue
    return out


# Two centres this close are the same beacon seen through smear jitter.
_BEACON_MATCH = 3


def _dark_beacons(
    agent_view: Mapping[str, Any],
    prior_day_entry: Optional[Mapping[str, Any]],
) -> List[str]:
    """Redsigns that were live at last plan and are gone today (fix 0.6, OBS-45).

    A redsign retires the instant its LAST pure is harvested, by anyone, and
    retirement is global — the region simply leaves every seat's view. So a seat
    that committed two harvesters and a probe to a seam opens today's card to
    find it absent, with no line explaining why. That silence is a wasted
    signal: a beacon going dark is a public fact that SOMEONE just banked a
    pure, which is exactly the tempo read the doctrine asks for. We cannot name
    them — the engine records ``spent_by`` but the view never ships a retired
    region — so we report the event and leave the attribution open.
    """
    if not isinstance(prior_day_entry, Mapping):
        return []
    was = prior_day_entry.get("live_redsigns")
    if not isinstance(was, list) or not was:
        return []
    now = live_redsign_centres(agent_view)
    lines: List[str] = []
    for c in was:
        if not isinstance(c, (list, tuple)) or len(c) < 2:
            continue
        try:
            cell = (int(c[0]), int(c[1]))
        except (TypeError, ValueError):
            continue
        if any(
            max(abs(cell[0] - n[0]), abs(cell[1] - n[1])) <= _BEACON_MATCH
            for n in now
        ):
            continue
        lines.append(
            f"the REDSIGN near ~({cell[0]},{cell[1]}) WENT DARK — a beacon "
            "retires only when its LAST pure is taken, so somebody banked it "
            "last night. That seam is spent; stop pricing options against it."
        )
    return lines


def _what_you_saw(
    agent_view: Mapping[str, Any],
    seen_log: Sequence[Mapping[str, Any]],
    prior_day_entry: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    """Public orbital + in-vision enemy field + your losses/denials."""
    lines: List[str] = []
    # A retired beacon leads: it is the one item here that invalidates PLANS
    # rather than merely reporting what happened.
    for line in _dark_beacons(agent_view, prior_day_entry):
        lines.append(f"BOARD: {line}")
    # In-vision enemy field moves + public orbital, from the replay log.
    for e in seen_log:
        seat = str(e.get("seat") or "a rival")
        tag = str(e.get("tag") or "")
        cell = e.get("cell")
        hour = _as_int(e.get("hour"), 0)
        kind = str(e.get("kind") or "")
        cell_s = f" {_fmt_cell(cell)}" if cell else ""
        if kind == "orbital":
            verb = {
                "probe": "launched a probe",
                "emp_launch": "fired an EMP salvo",
                "chaff_flare": "flared chaff",
                # v13 — SNAP is public per §4.9.4 (the strike itself is
                # reported on combat_events for every seat, whether or not
                # it found anything, because the scorch mark announces it).
                "snap": "fired a SNAP round",
            }.get(tag, tag)
            suffix = "" if tag == "chaff_flare" else cell_s
            note = " (public)" if tag == "probe" else ""
            lines.append(f"H{hour:02d} {seat} {verb}{suffix}{note}")
        else:  # enemy field, in your vision
            verb = {"step": "moved a harvester", "drop": "dropped a harvester",
                    "pickup": "lifted a harvester", "mine_lay": "laid a mine"}.get(tag, tag)
            lines.append(f"H{hour:02d} {seat} {verb} seen{cell_s} (in your vision)")
    # Losses to you + denials by you (attributed digest channels). Asset
    # destructions come FIRST — a lost harvester + spilled cargo is the most
    # consequential learning signal and was previously never surfaced.
    for line in digest.narrate_losses(agent_view):
        lines.append(f"TO YOU: {line}")
    for line in digest.narrate_incoming(agent_view):
        lines.append(f"TO YOU: {line}")
    for line in digest.narrate_denials(agent_view):
        lines.append(f"BY YOU: {line}")
    return lines


# ── assembly + public entry ─────────────────────────────────────────────────
def build(
    frames: Sequence[Mapping[str, Any]],
    agent_view: Mapping[str, Any],
    prior_day_entry: Optional[Mapping[str, Any]],
    *,
    day: int,
) -> Dict[str, Any]:
    """Assemble the structured memory from replay frames + the agent view.

    Pure — the store read happens in :func:`render`. Falls back to the
    ``agent_view.last_night`` channels when no frames are present.
    """
    ln = agent_view.get("last_night") or {}
    day_ended = ln.get("day_ended", (int(day) - 1) if day else 0)
    width, height = econ._grid_dims(agent_view)
    player = str((agent_view.get("meta") or {}).get("seat")
                 or (agent_view.get("meta") or {}).get("player") or "")

    exec_log = read_execution_log(frames, player, width=width, height=height) if frames else []
    orders = read_orders(frames, player) if frames else []
    degraded = not bool(frames) or (not exec_log and not orders)

    if degraded:
        # Fallback: reconstruct a coarse own-log + orders from the summary
        # channels so the block still renders on day 1 / older sessions.
        exec_log = _fallback_own_log(agent_view)
        orders = orders or _fallback_orders(prior_day_entry)

    expected = None
    intent = ""
    reconciliation: List[Mapping[str, Any]] = []
    if isinstance(prior_day_entry, Mapping):
        exp = prior_day_entry.get("expected_yield")
        if isinstance(exp, Mapping):
            expected = exp
        intent = str(prior_day_entry.get("intent") or "").strip()
        rec = prior_day_entry.get("reconciliation")
        if isinstance(rec, list):
            reconciliation = [r for r in rec if isinstance(r, Mapping)]

    # Banked parcels (authoritative hoard delta; view fallback) drive BOTH the
    # scored YIELD and the per-step harvest annotation in the EXECUTION LOG.
    banked = _banked_parcels(agent_view, frames, player)
    harvest_map = _harvest_by_cell(banked)
    for e in exec_log:
        if (
            e.get("kind") == "own"
            and str(e.get("tag") or "") in ("drop", "step")
            and str(e.get("outcome") or "ok") != "failed"
        ):
            c = e.get("cell")
            if isinstance(c, tuple) and c in harvest_map:
                e["harvest"] = harvest_map[c]

    seen_log = [e for e in exec_log if e.get("kind") != "own"]
    return {
        "day": day_ended,
        "intent": intent,
        "orders": orders,
        "execution_log": exec_log,
        "actual": _score_parcels(banked),
        "lost": lost_in_transit(agent_view, frames, player),
        "expected": expected,
        "reconciliation": reconciliation,
        "what_you_saw": _what_you_saw(agent_view, seen_log, prior_day_entry),
        "degraded": degraded,
    }


def _fallback_own_log(agent_view: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Coarse own execution log from ``agent_view.last_night.my_orders``."""
    ln = agent_view.get("last_night") or {}
    out: List[Dict[str, Any]] = []
    for o in (ln.get("my_orders") or []):
        if not isinstance(o, Mapping):
            continue
        text = str(o.get("text") or "")
        outcome = "failed" if str(o.get("outcome")) == "illegal" else "ok"
        cell = _last_cell(text)
        out.append({
            "hour": 0, "kind": "own", "seat": "", "tag": "order",
            "cell": cell, "outcome": outcome,
            "reason": str(o.get("reason") or "") if outcome == "failed" else "",
            "text": text,
        })
    return out


def _fallback_orders(prior_day_entry: Optional[Mapping[str, Any]]) -> List[str]:
    """Orders from the prior-day memory entry when the opening frame is gone."""
    e = prior_day_entry or {}
    planned = e.get("planned_cells") or []
    if planned:
        return [str(p) for p in planned[:8]]
    plan_ids = e.get("plan_ids") or []
    if plan_ids:
        return [", ".join(str(p) for p in plan_ids[:8])]
    ms = str(e.get("moves_summary") or "").strip()
    return [ms] if ms else []


def collect(
    store: Any,
    session_id: str,
    player: str,
    agent_view: Mapping[str, Any],
    prior_day_entry: Optional[Mapping[str, Any]],
    *,
    day: int,
) -> Dict[str, Any]:
    """Read last night's replay frames and assemble the memory dict.

    Best-effort: any store failure degrades to the summary-channel fallback so a
    prompt is always produced. Split from :func:`render` so callers that need the
    structured memory (e.g. the journal's outcome/enemy summaries) can reuse the
    single store read instead of computing it twice.
    """
    ln = agent_view.get("last_night") or {}
    day_ended = _as_int(ln.get("day_ended"), (int(day) - 1) if day else 0)
    frames: List[Mapping[str, Any]] = []
    if store is not None and session_id and day_ended > 0:
        try:
            frames = list(
                store.list_replay_frames(session_id, day_ended, day_ended) or []
            )
        except Exception:
            frames = []
    # ``meta.seat`` may be absent on some fixtures; stamp the caller's seat so the
    # pure builder can filter without re-reading the view.
    view = dict(agent_view)
    meta = dict(view.get("meta") or {})
    meta.setdefault("seat", player)
    view["meta"] = meta
    return build(frames, view, prior_day_entry, day=day)


def render(
    store: Any,
    session_id: str,
    player: str,
    agent_view: Mapping[str, Any],
    prior_day_entry: Optional[Mapping[str, Any]],
    *,
    day: int,
) -> str:
    """Read last night's replay frames and render the SECTION 3 block."""
    return format_block(
        collect(store, session_id, player, agent_view, prior_day_entry, day=day)
    )
