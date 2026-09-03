"""v11 — SUPERSEDE target selection (blind a rival's probe, RULEBOOK §3.16).

A supersede lands YOUR probe on a rival's live probe: it destroys theirs and
takes the cell (yours survives), denying the enemy that probe's landing + vision.
v7's ``top_supersede_hints`` only fired on the FINAL night and ranked purely by
recency. For redsign combat we want it available every night, targeted with
intent:

* **By owner + score** — when you are TRAILING, blind the LEADER's probes first
  (deny the seat that's beating you). ``hud.scores`` carries every seat's RED
  total, so we can tell who leads and whether we're behind.
* **By recency** — the freshest probe has the most future vision to deny; an old
  probe has already paid most of its value.
* **Skip the near-dead** — a probe about to expire (its vision effectively spent)
  is not worth a whole probe to blind, so it is dropped from the menu.

Harness-only + v11-local: reuses v7's read helpers (friendly probe positions)
without editing shared code. Output shape matches the existing supersede hint so
``agency._supersede_option`` compiles it unchanged, plus enrichment fields
(``owner``, ``owner_score``, ``is_leader``, ``nights_remaining``) for the menu.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from sea_of_colours.game.tuning import probe_lifetime_nights
from sea_of_colours.orchestrator_2.harnesses.sagar_coco._v7.probe_hints import (
    _friendly_probe_positions,
)

# A probe with this many (or fewer) nights of life left is "about to expire" —
# its vision is effectively spent, so it is not worth a whole probe to blind.
_NEAR_EXPIRY_NIGHTS = 1

# Chebyshev radius within which an enemy probe counts as the "finder" lighting a
# redsign seam (mirrors ``seam_control._REGION_MATCH_RADIUS`` = the probe vision
# half-width). An enemy probe this close to a live redsign beacon is the disk
# revealing the contested pure — blinding IT denies the jackpot, so it ranks
# ahead of any other target when a redsign is on the board.
_FINDER_MATCH_RADIUS = 4


def _redsign_finder_cells(agent_view: Mapping[str, Any]) -> set:
    """Enemy-probe cells sitting on/near a live redsign beacon (the finders).

    Any redsign — mine (a rival is contesting it) or a rival's (their discoverer
    lighting the pure) — makes a nearby enemy probe the single most valuable blind
    on the board: it is the disk that reveals the contested pure. Returns the set
    of enemy-probe cells within ``_FINDER_MATCH_RADIUS`` of any redsign centre.
    """
    centers: List[Tuple[int, int]] = []
    for r in (agent_view.get("redsign") or []):
        if not isinstance(r, Mapping):
            continue
        c = r.get("center")
        if isinstance(c, (list, tuple)) and len(c) == 2:
            try:
                centers.append((int(round(float(c[0]))), int(round(float(c[1])))))
            except (TypeError, ValueError):
                continue
    if not centers:
        return set()
    finders: set = set()
    for row in _enemy_probes(agent_view):
        at = row.get("at")
        if not (isinstance(at, (list, tuple)) and len(at) == 2):
            continue
        cell = (int(at[0]), int(at[1]))
        if any(
            max(abs(cell[0] - cx), abs(cell[1] - cy)) <= _FINDER_MATCH_RADIUS
            for cx, cy in centers
        ):
            finders.add(cell)
    return finders


def _enemy_probes(agent_view: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Public enemy-probe sightings with OWNER preserved, freshest first.

    Enemy probe launches are public (§3.15). We read both competitor_intel
    channels (``new_this_day`` launches + older ``persistent_echoes``), dedup by
    cell keeping the most-recent sighting, and — unlike v7's ``_enemy_probe_cells``
    — carry the owning seat so the menu can target by player.
    """
    best: Dict[Tuple[int, int], Dict[str, Any]] = {}

    def _add(at: Any, day_seen: Any, owner: Any) -> None:
        if not (isinstance(at, (list, tuple)) and len(at) >= 2):
            return
        try:
            cell = (int(at[0]), int(at[1]))
        except (TypeError, ValueError):
            return
        try:
            d = int(day_seen) if day_seen is not None else 0
        except (TypeError, ValueError):
            d = 0
        prev = best.get(cell)
        if prev is None or d >= int(prev.get("day_seen") or 0):
            best[cell] = {"at": cell, "day_seen": d, "owner": str(owner or "")}

    ci = agent_view.get("competitor_intel") or {}
    for row in (ci.get("new_this_day") or []):
        if isinstance(row, Mapping) and row.get("kind") == "enemy_probe_launch":
            _add(row.get("at"), row.get("day_seen"), row.get("owner"))
    for row in (ci.get("persistent_echoes") or []):
        if isinstance(row, Mapping) and row.get("kind") == "enemy_probe":
            _add(row.get("at"), row.get("last_seen_day"), row.get("owner"))

    return sorted(
        best.values(), key=lambda r: int(r.get("day_seen") or 0), reverse=True
    )


def _standings(
    agent_view: Mapping[str, Any], my_player: str,
) -> Tuple[int, Dict[str, int], Optional[str], bool]:
    """Return ``(my_score, enemy_scores, leader, trailing)`` from ``hud.scores``."""
    hud = agent_view.get("hud") or {}
    raw = hud.get("scores")
    scores: Dict[str, int] = {}
    if isinstance(raw, Mapping):
        for p, s in raw.items():
            try:
                scores[str(p)] = int(s or 0)
            except (TypeError, ValueError):
                continue
    my_score = scores.get(str(my_player))
    if my_score is None:
        try:
            my_score = int(hud.get("score") or 0)
        except (TypeError, ValueError):
            my_score = 0
    enemy_scores = {p: s for p, s in scores.items() if p != str(my_player)}
    leader = max(enemy_scores, key=lambda p: enemy_scores[p]) if enemy_scores else None
    trailing = leader is not None and my_score < enemy_scores[leader]
    return my_score, enemy_scores, leader, trailing


def top_supersede_hints(
    agent_view: Mapping[str, Any],
    *,
    day: int,
    my_player: str,
    max_hints: int = 4,
    exclude_cells: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Ranked supersede targets for tonight (see module docstring).

    Ordering: when TRAILING, the leader's probes first; then by owner score
    (blind the strongest seat); then by recency (freshest = most vision to deny).
    Filtered: friendly-occupied cells and probes about to expire are dropped.
    Capped at spare probe stock. ``[]`` when no stock or no live enemy probe.

    ``exclude_cells`` are enemy-probe cells a chosen seam pattern ALREADY blinds
    this night (``seam_control.planned_supersede_cells``). A stand-alone SS on the
    same cell is redundant — the redsign attack denies it for free — so it is
    dropped from the menu to stop the thinker double-spending a probe on one blind.
    """
    stock = int((agent_view.get("orbit") or {}).get("probe_stock") or 0)
    if stock <= 0:
        return []

    probes = _enemy_probes(agent_view)
    if not probes:
        return []

    lifetime = probe_lifetime_nights() or 0
    friendly = set(_friendly_probe_positions(agent_view))
    excluded: set = set()
    for c in (exclude_cells or []):
        if isinstance(c, (list, tuple)) and len(c) == 2:
            excluded.add((int(c[0]), int(c[1])))
    finder_cells = _redsign_finder_cells(agent_view)
    _my_score, enemy_scores, leader, trailing = _standings(agent_view, my_player)

    cands: List[Dict[str, Any]] = []
    for row in probes:
        at = row["at"]
        if at in friendly:
            continue  # we already sit there — nothing to supersede
        if at in excluded:
            continue  # a chosen seam pattern already blinds this probe for free
        remaining: Optional[int] = None
        if lifetime:
            remaining = lifetime - (int(day) - int(row.get("day_seen") or 0))
            if remaining <= _NEAR_EXPIRY_NIGHTS:
                continue  # about to expire — its vision is already spent
        owner = str(row.get("owner") or "")
        cands.append({
            "probe_at": [int(at[0]), int(at[1])],
            "supersedes": "enemy_probe",
            "owner": owner,
            "day_seen": int(row.get("day_seen") or 0),
            "owner_score": int(enemy_scores.get(owner, 0)),
            # Only flag "leader" when we are ACTUALLY trailing a seat ahead of
            # us — otherwise (e.g. day 2, everyone at 0) labelling a rival "the
            # LEADER (score 0)" is misleading and mis-ranks nothing.
            "is_leader": bool(trailing and leader and owner == leader),
            # The disk lighting a live redsign — blinding it denies the
            # contested jackpot, so it outranks even the leader's other probes.
            "is_finder": bool((int(at[0]), int(at[1])) in finder_cells),
            "nights_remaining": remaining,
        })

    # Redsign finder first (deny the contested pure), THEN the leader's probes
    # when trailing, then by owner score, then recency.
    cands.sort(key=lambda h: (
        0 if h["is_finder"] else 1,
        0 if (trailing and h["is_leader"]) else 1,
        -int(h["owner_score"]),
        -int(h["day_seen"]),
    ))
    return cands[: min(max_hints, stock)]
