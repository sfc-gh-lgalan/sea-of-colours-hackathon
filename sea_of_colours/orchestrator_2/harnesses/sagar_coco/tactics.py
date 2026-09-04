"""SAGAR_COCO tactics: price weapon options in points and decide when to fire.

The kit's own finding (docs/TEACHING_WEAPONS.md) is that building all four
firing rungs does not make the model fire: the menu offers the salvo, the
doctrine argues for it, and the model takes a harvest chain. This module moves
the decision from persuasion to arithmetic.

* ``price_and_recommend`` attaches an expected-points figure to every weapon
  option (what the salvo denies, what the hour costs) and tags the single best
  one RECOMMENDED when a deterministic trigger fires.
* ``enforce_recommended`` adds the recommended ordnance to the queue if the
  model did not, and re-sequences a chaff flare after the fleet's last pickup.

Everything is computed from the fog-limited view. Nothing here can make the
harvest shape worse: ordnance is one or two extra slots at the head (EMP) or
tail (chaff) of a 21-slot night, and the harness caps the queue afterwards.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from sea_of_colours.orchestrator_2.harnesses.sagar_coco import scorch
from sea_of_colours.orchestrator_2.harnesses.sagar_coco import verifier as V

PURE_POINTS = 765.0
# A rival probe watching our pure is worth roughly the pure it lets them land on,
# discounted by the chance they actually come tonight.
P_RIVAL_COMES_WATCHED = 0.45
# A cloud over their sign buys tempo, not a body count.
TEMPO_POINTS_PER_CELL = 4.0
# The hour a salvo spends is an hour a harvester did not walk: price it as the
# weakest RED cell in the best chain, which is what actually gets dropped.
HOUR_COST_POINTS = 60.0
RECOMMEND_MARGIN = 120.0     # recommended only when the net is clearly positive


def _rack(agent_view: Mapping[str, Any]) -> Dict[str, int]:
    try:
        return scorch.stock(agent_view)
    except Exception:  # noqa: BLE001
        return {"emp": 0, "chaff": 0}


def _watching_probes(s: V.Situation) -> List[Tuple[Tuple[int, int], int]]:
    """Rival probes whose disk covers one of our pures or our own sign centre."""
    interest = list(s.pures) + [g["center"] for g in s.signs if g["mine"]]
    out = []
    for e, rec in s.enemy_probes.items():
        if any(V.in_disk(e, c, s.r) for c in interest):
            out.append((e, rec))
    return out


def price_option(opt: Any, s: V.Situation, radius: int = 2) -> float:
    """Expected net points of a weapon option, from the seat's own view."""
    kind = str(getattr(opt, "kind", ""))
    pay = getattr(opt, "payload", None) or {}
    if kind == "emp":
        targets = [V._xy(t) for t in (pay.get("targets") or [])]
        targets = [t for t in targets if t is not None]
        covered = set()
        for t in targets:
            covered |= _blast(t, radius, s.dims)
        kills = [e for e in s.enemy_probes if e in covered]
        watching = {e for e, _ in _watching_probes(s)}
        value = 0.0
        for e in kills:
            value += PURE_POINTS * P_RIVAL_COMES_WATCHED if e in watching else 90.0
        note = str(pay.get("kind_note") or "")
        is_tempo = note in ("redsign", "occupy") or str(getattr(opt, "option_id", "")).startswith(("BLIND_SCORCH", "SCORCH_REDSIGN"))
        # Tempo only pays on ground we cannot already work: if we can SEE red in the
        # smear (a lit pure, a known halo) the cloud locks us out as much as them.
        known_red_in_cloud = [c for c in covered if s.reds.get(c, 0) > 0]
        if is_tempo and not known_red_in_cloud and not s.pures:
            tempo = TEMPO_POINTS_PER_CELL * len(covered)
            # An eight-hour cloud lit on the last night buys tempo for a tomorrow
            # that never comes: only the hours before dawn are worth anything.
            if s.day >= s.day_cap:
                tempo *= 0.25
            value += tempo
        # Every known RED cell darkened is a cell our own fleet cannot harvest tonight.
        value -= sum(V.red_value(s.reds[c]) * 0.5 for c in known_red_in_cloud)
        # friendly fire: our own pures inside the cloud make the play self-harm outright
        own_hit = [p for p in s.pures if p in covered]
        value -= 600.0 * len(own_hit)
        return value - HOUR_COST_POINTS
    if kind == "chaff":
        # A flare cancels every seat's orders for three hours and the launcher is
        # immune only in the launch hour, so it is priced as pure denial EARNED ONLY
        # when our own fleet is already lifted. The harness guarantees that by
        # sequencing the flare after the last pickup; if that ever fails, the
        # final gate moves it, so there is no self-jam term to model here beyond
        # the hour it spends. What it is worth is the landings and lifts the rivals
        # lose on ground we care about.
        close = [h for h in s.enemy_harvesters if any(V.cheb(h, p) <= 3 for p in s.pures)]
        final = s.day >= s.day_cap
        value = 0.0
        if close and s.pures:
            value += PURE_POINTS * 0.35 * min(2, len(close))
        if final and s.pures and s.watched(s.pures[0]):
            value += 150.0
        return value - HOUR_COST_POINTS
    return 0.0


def _blast(centre: Tuple[int, int], radius: int, dims: Tuple[int, int]) -> set:
    w, h = dims
    out = set()
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            if abs(dx) + abs(dy) <= radius:
                x, y = centre[0] + dx, centre[1] + dy
                if 0 <= x < w and 0 <= y < h:
                    out.add((x, y))
    return out


def price_and_recommend(registry: "Mapping[str, Any]", agent_view: Mapping[str, Any], *,
                        day: int, day_cap: int) -> str:
    """Annotate weapon options in place with a price line; tag the best RECOMMENDED."""
    rack = _rack(agent_view)
    if rack.get("emp", 0) <= 0 and rack.get("chaff", 0) <= 0:
        return ""
    s = V.situation(agent_view)
    specs = (agent_view.get("orbit") or {}).get("weapon_specs") or {}
    radius = int((specs.get("emp") or {}).get("radius") or 2)
    best_id: Optional[str] = None
    best_net = -1e9
    notes: List[str] = []
    for oid, opt in registry.items():
        kind = str(getattr(opt, "kind", ""))
        if kind not in ("emp", "chaff"):
            continue
        net = price_option(opt, s, radius)
        opt.payload["expected_net_points"] = round(net)
        opt.detail = f"{opt.detail} EXPECTED NET: {net:+.0f} pts."
        notes.append(f"{oid}={net:+.0f}")
        if net > best_net:
            best_net, best_id = net, oid
    if best_id is not None and best_net >= RECOMMEND_MARGIN:
        opt = registry[best_id]
        opt.title = f"RECOMMENDED: {opt.title}"
        opt.payload["recommended"] = True
        opt.rationale = (
            f"RECOMMENDED by the tactical pricer: expected net {best_net:+.0f} points after the hour it costs. "
            + (opt.rationale or "")
        )
        return f"tactics: {', '.join(notes)}; RECOMMENDED {best_id}"
    return f"tactics: {', '.join(notes)}; none recommended" if notes else ""


def enforce_recommended(moves: Sequence[Mapping[str, Any]], registry: "Mapping[str, Any]",
                        agent_view: Mapping[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Add the RECOMMENDED ordnance if the model left it out; sequence chaff last."""
    out = [dict(m) for m in moves if isinstance(m, Mapping)]
    log: List[str] = []
    rack = _rack(agent_view)
    has_emp = any(str(m.get("a")) == "emp_launch" for m in out)
    has_chaff = any(str(m.get("a")) == "chaff_flare" for m in out)
    rec = [o for o in registry.values() if (getattr(o, "payload", None) or {}).get("recommended")]
    for opt in rec:
        kind = str(getattr(opt, "kind", ""))
        pay = getattr(opt, "payload", None) or {}
        if kind == "emp" and not has_emp and rack.get("emp", 0) > 0:
            targets = [list(t) for t in (pay.get("targets") or []) if V._xy(t) is not None][:3]
            if targets and len(out) < V.MAX_MOVES:
                out.insert(0, {"a": "emp_launch", "at": targets})
                has_emp = True
                log.append(f"tactics: added RECOMMENDED salvo {opt.option_id} at {targets} (model omitted it)")
        elif kind == "chaff" and not has_chaff and rack.get("chaff", 0) > 0:
            if len(out) < V.MAX_MOVES:
                out.append({"a": "chaff_flare"})
                has_chaff = True
                log.append("tactics: added RECOMMENDED chaff flare after the fleet lifts (model omitted it)")
    # chaff always after the last pickup: it jams us too
    if has_chaff:
        flares = [m for m in out if str(m.get("a")) == "chaff_flare"]
        rest = [m for m in out if str(m.get("a")) != "chaff_flare"]
        last_pick = max((i for i, m in enumerate(rest) if str(m.get("a")) == "pickup"), default=-1)
        rest.insert(last_pick + 1, flares[0])
        if len(flares) > 1:
            log.append("tactics: dropped duplicate chaff flares (one per night)")
        out = rest
    return out[:V.MAX_MOVES], log
