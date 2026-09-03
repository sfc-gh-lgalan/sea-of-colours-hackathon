"""One currency for every option on the menu.

The measured problem this exists to solve
-----------------------------------------
With all four firing rungs built and verified, and a weapon that needs no
target at all, the shipped agent chose a harvest chain **30 times out of 30**.
That is not plumbing and it is not visibility. Look at what it was comparing:

    [GRAB1] grab live mass (31,18)
         yield: red ~+379 (1 mass) · blue 0 · green 0

    [JAM] Chaff flare - freeze every rival for 3h
         cancels EVERY other seat's action for 3h board-wide ...

**One of those is a number and the other is an argument.** A small model under
an 800-token plan budget, asked to rank them, takes the number every time. The
options are denominated in different units and cannot be compared.

So price everything in the same unit -- expected points -- and say the number
on every line.

What this is not
----------------
It is not new estimation. ``option_economics`` already computes yields, crush
risk, collision risk and blind-fog estimates, and already renders most of it.
The Ledger collapses what exists into one comparable scalar. Where a number is
genuinely a guess it is marked, because a confidently wrong EV is worse than
honest prose.

The shadow price of BLUE
------------------------
BLUE cannot be shipped and cannot be scored. Its only use is conversion into
ordnance, and ordnance only produces denial, so blue is worth exactly what it
will deny -- and nothing at all if it is never spent. It therefore enters the
ledger as a *transfer price* in exactly two places, with opposite signs:

    acquiring blue :  + lambda * units
    firing a salvo :  + denial_points - lambda * blue_spent

Denial is credited once, at firing. ``denial_points`` is never derived from
lambda, which would be a correlated double count. Acquisition earns the price,
firing pays it, and across a season they net out.

Lambda is time-varying and terminal-zero: blue banked on the last night can
never be spent, so it is worth nothing. And it is **fitted, not authored** --
:mod:`calibration` updates it from realised denial per blue actually spent, so
an agent that hoards and never fires drives its own lambda toward zero and the
ledger stops paying for blue by itself. The mechanism self-corrects if the
thesis behind it is wrong, which is the best argument for shipping it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from sea_of_colours.orchestrator_2.harnesses.sagar_cursor import (
    calibration, option_economics as econ, scorch,
)

#: Kinds whose value is a harvester walk.
_HARVEST_KINDS = frozenset(
    {"seam", "hotdrop", "grab", "blue_grab", "chain", "frontier"}
)

#: Points a probe is credited with for the ground it lights. A probe banks
#: NOTHING itself; it buys the ability to drop somewhere next time. Deliberately
#: modest -- vision that is never converted is worth zero, and over-pricing it
#: would have the agent probing instead of mining.
_VISION_POINTS_PER_CELL = 1.2

#: Prior for what one unit of BLUE denies once spent, in points. An EMP costs
#: 200 blue and, well aimed, removes a night of vision from a rival. Starts
#: conservative on purpose: calibration corrects it upward from evidence, and
#: the failure mode of starting too high (buying blue that never fires) is the
#: exact defect this agent was built to fix.
_DENIAL_POINTS_PER_BLUE = 0.8

#: Shapes P(the blue gets spent before the season ends). Larger = more
#: pessimistic early. At k=2 a mid-season night prices blue at about 2/3 of its
#: denial value, and the final night at exactly zero.
_CONVERT_SHAPE_K = 2.0

#: Ranking and labelling are SEPARATE treatments so a measured change can be
#: attributed to one of them rather than to both at once (the consensus panel
#: was explicit about this). Both default on; either can be switched off.
_LABELS_ON = os.environ.get("SAGAR_CURSOR_LEDGER_LABELS", "1") != "0"
_ORDER_ON = os.environ.get("SAGAR_CURSOR_LEDGER_ORDER", "1") != "0"


@dataclass
class Estimate:
    """What an option is worth, and what that number is made of."""

    points: float = 0.0
    parts: Dict[str, float] = field(default_factory=dict)
    #: "measured" (engine-scored cells), "estimated" (fog / denial priors).
    confidence: str = "measured"

    def label(self) -> str:
        """The headline the model reads, with its decomposition behind it."""
        shown = [
            f"{name} {value:+.0f}"
            for name, value in self.parts.items()
            if abs(value) >= 1.0
        ]
        tail = f"  ({' · '.join(shown)})" if shown else ""
        mark = "~" if self.confidence != "measured" else ""
        return f"EV {mark}{self.points:+.0f}{tail}"


def blue_shadow_price(
    agent_view: Mapping[str, Any],
    *,
    day: Optional[int] = None,
    day_cap: Optional[int] = None,
) -> float:
    """Points per unit of BLUE tonight. Zero on the last night, by construction."""
    hud = (agent_view.get("hud") or {}) if agent_view else {}
    day = int(day if day is not None else (econ.view_day(agent_view) or 1))
    day_cap = int(day_cap if day_cap is not None else (hud.get("season_day_cap") or 7))

    nights_left = day_cap - day
    if nights_left <= 0:
        return 0.0  # blue that cannot be spent denies nothing
    p_convert = nights_left / (nights_left + _CONVERT_SHAPE_K)
    return calibration.denial_per_blue(_DENIAL_POINTS_PER_BLUE) * p_convert


def _harvest_estimate(
    payload: Mapping[str, Any], agent_view: Mapping[str, Any], lam: float,
) -> Estimate:
    try:
        yb = econ.yield_breakdown(econ.walk_cells(payload), agent_view)
    except Exception:
        return Estimate(0.0, {}, "estimated")

    red = float(yb.get("red_pts") or 0.0)
    green = float(yb.get("green_penalty") or 0.0)
    blue_units = float(yb.get("blue_fissile") or 0.0)
    blue_pts = lam * blue_units

    parts: Dict[str, float] = {}
    if red:
        parts["red"] = red
    if blue_pts:
        parts["blue"] = blue_pts
    if green:
        parts["green"] = green

    # An ECHO cell is real evidence, but it was last seen some nights ago and a
    # rival may have taken it since. Blending stale and live into one confident
    # total reads more certain than the board warrants.
    confidence = "measured" if not yb.get("echo_pts") else "estimated"
    return Estimate(red + blue_pts + green, parts, confidence)


def _probe_estimate(payload: Mapping[str, Any]) -> Estimate:
    """A probe scores nothing. It buys the ability to score later."""
    try:
        promise = float(
            payload.get("edge_promise") or payload.get("area_gain") or 0.0
        )
    except (TypeError, ValueError):
        promise = 0.0
    points = promise * _VISION_POINTS_PER_CELL
    return Estimate(points, {"vision": points} if points else {}, "estimated")


def _weapon_estimate(
    payload: Mapping[str, Any], agent_view: Mapping[str, Any], lam: float,
) -> Estimate:
    """Denial credited ONCE, here, minus the blue it consumes at the transfer price."""
    dials = scorch.specs(agent_view)
    verb = str(payload.get("verb") or "")

    if verb == "chaff_flare":
        blue_spent = float(dials["chaff"]["blue"])
        # Chaff buys hours, not ground: every rival action for its duration.
        # Credited as the tempo it protects on a contested pure, not as a share
        # of their wallet, because the league scores what WE do.
        denial = calibration.chaff_denial(140.0)
    else:
        blue_spent = float(dials["emp"]["blue"])
        killed = float(payload.get("probes_killed") or 0)
        denial = calibration.emp_denial_per_probe(90.0) * killed

    cost = lam * blue_spent
    parts: Dict[str, float] = {}
    if denial:
        parts["denies"] = denial
    if cost:
        parts["blue spent"] = -cost
    return Estimate(denial - cost, parts, "estimated")


def score(
    option: Any,
    agent_view: Mapping[str, Any],
    *,
    day: Optional[int] = None,
    day_cap: Optional[int] = None,
) -> Estimate:
    """Price one option in expected points."""
    kind = str(getattr(option, "kind", "") or "")
    payload = getattr(option, "payload", None) or {}
    lam = blue_shadow_price(agent_view, day=day, day_cap=day_cap)

    if kind in _HARVEST_KINDS:
        est = _harvest_estimate(payload, agent_view, lam)
    elif kind == "weapon":
        est = _weapon_estimate(payload, agent_view, lam)
    elif kind in ("probe", "supersede"):
        est = _probe_estimate(payload)
    else:
        est = Estimate(0.0, {}, "estimated")

    # Calibration: the agent's own record of how much of a prediction of this
    # KIND actually arrived. Per kind, never per board -- a per-board factor
    # would memorise the ten-board suite instead of learning anything.
    factor = calibration.factor_for(kind)
    if factor != 1.0 and est.points:
        est.points *= factor
        est.confidence = "estimated"
    return est


def labels_enabled() -> bool:
    return _LABELS_ON


def ordering_enabled() -> bool:
    return _ORDER_ON


def rank(
    options: List[Any],
    agent_view: Mapping[str, Any],
    *,
    day: Optional[int] = None,
    day_cap: Optional[int] = None,
) -> List[Any]:
    """Order by expected value, richest first. Nothing is removed.

    Ranking, not truncation. Hiding a low-EV option would also hide the
    board-winning line that occasionally comes from one, and nobody has
    measured how often that happens -- so the menu keeps every option and only
    changes the order it presents them in.
    """
    if not _ORDER_ON:
        return list(options)
    scored = [
        (score(o, agent_view, day=day, day_cap=day_cap).points, i, o)
        for i, o in enumerate(options)
    ]
    scored.sort(key=lambda row: (-row[0], row[1]))  # stable on ties
    return [row[2] for row in scored]
