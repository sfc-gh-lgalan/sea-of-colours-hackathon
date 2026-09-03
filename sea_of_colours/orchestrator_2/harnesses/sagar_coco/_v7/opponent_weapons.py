"""Opponent weapon inference — turns fuzzy station_intel readings into
per-opponent uncertainty ranges over ``{emp, chaff}`` stocks.

The engine hides opponents' exact weapon stocks from the fuzzy view we
get (see :func:`sea_of_colours.game.session.GameSession._station_observation`).
What IS public:

  * ``station_intel.opponents[X].blue.band`` — a 0-5 pip band where each
    pip is ~150 blue-purity. Weapons are BUILT with blue-purity, so a
    band drop between nights is evidence of a build.
  * ``station_intel.opponents[X].activity.{emps, chaff}`` — count of
    launches this seat performed on the last resolved night. Launches
    consume stock but DO NOT move the blue band (blue was spent at build
    time).

So the signal loop:

  * A band drop with 0 launches → they built ≥1 weapon last orbit turn.
    Which weapon? Unknown — the drop could be EMP (200), chaff (255), or
    any combination that fits inside the band range. We widen the ``max``
    on each weapon we can't rule out.
  * A launch on any weapon → decrement its ``min`` and ``max`` by the
    observed launch count (floor at 0). Launches are a hard signal.

Weapon cost reminders (from :mod:`sea_of_colours.game.weapons`):

  * EMP   — 200 blue + 250 credits
  * Chaff — 255 blue + 0 credits (spans 1-2 pips depending on start position)

v1.31 — the caltrop mine track was REMOVED, not merely hidden. It had
become actively wrong: at 100 blue it was the cheapest weapon, so every
band drop widened ``mines_max`` further than either real weapon and the
estimator's confidence was being spent inventing stock of something the
engine will not sell.

Sharper heuristic: a 2-pip drop in a single night with 0 launches is
strong evidence of chaff OR ≥2 weapons total. We don't try to disambiguate
that here — the uncertainty ranges surface it naturally and the doctrine
teaches the agent to fear whichever weapon has ``max > 0``.

Persistence: piggybacks on the in-process ``_IN_MEMORY_STORE`` keyed by
``(session_id, viewer_player, opponent_seat)``. First pass is memory-only;
a Snowflake MERGE mirror can be added later without changing the API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple


# Blue purity spent at BUILD, per weapon.
_EMP_BLUE_COST = 200
_CHAFF_BLUE_COST = 255

# Station-intel band step (see game.session STATION_BLUE_PIP_STEP = 150).
_BAND_STEP = 150


@dataclass
class WeaponEstimate:
    """Per-opponent inferred weapon stock (as (min, max) uncertainty range).

    The engine tells us launch counts precisely and the blue-band step
    coarsely. From these, we track a lower bound (``min``) and an upper
    bound (``max``) on each of the two weapon types.

    A value of ``max == 0`` means "we're confident this opponent has no
    stock of this weapon." Any ``max > 0`` should raise a warning in
    the wishlist / prompt.
    """
    seat: str
    emps_min: int = 0
    emps_max: int = 0
    chaff_min: int = 0
    chaff_max: int = 0
    #: Last observed blue-band for this opponent. ``None`` on the first
    #: turn we see them. Used as the anchor for detecting band drops.
    last_blue_band: Optional[int] = None
    #: Free-form one-line explanations, one per turn we updated the
    #: estimate. Rendered into the prompt as a small audit trail so the
    #: LLM can reason about WHY we think they have EMPs (not just that we
    #: do). Bounded to the last few entries to keep prompt size sane.
    inferences: List[str] = field(default_factory=list)

    def has_any(self) -> bool:
        return (self.emps_max + self.chaff_max) > 0

    def summary(self) -> str:
        """One-line render for prompts / logs."""
        return (
            f"{self.seat}: emp=[{self.emps_min}..{self.emps_max}] "
            f"chaff=[{self.chaff_min}..{self.chaff_max}]"
        )


# ─────────────────────────────────────────────────────────────────────────
# In-process store — keyed by (session_id, viewer_player) → { seat: est }
# ─────────────────────────────────────────────────────────────────────────

_MEMORY_STORE: Dict[Tuple[str, str], Dict[str, WeaponEstimate]] = {}


def _key(session_id: str, viewer: str) -> Tuple[str, str]:
    return (str(session_id), str(viewer))


def load_estimates(session_id: str, viewer: str) -> Dict[str, WeaponEstimate]:
    """Return the CURRENT stored estimates (copy — safe to mutate the
    returned dict without corrupting the store). Empty on first call."""
    stored = _MEMORY_STORE.get(_key(session_id, viewer)) or {}
    # Shallow copy the outer dict; WeaponEstimate itself is mutable but
    # we replace slots atomically in ``update_estimates``.
    return {k: v for k, v in stored.items()}


def store_estimates(
    session_id: str, viewer: str, estimates: Mapping[str, WeaponEstimate],
) -> None:
    """Persist ``estimates`` for the next turn's inference step."""
    _MEMORY_STORE[_key(session_id, viewer)] = dict(estimates)


def clear_store() -> None:
    """Test helper — reset the in-process store."""
    _MEMORY_STORE.clear()


# ─────────────────────────────────────────────────────────────────────────
# Inference
# ─────────────────────────────────────────────────────────────────────────


def update_estimates(
    session_id: str,
    viewer: str,
    agent_view: Mapping[str, Any],
    *,
    max_audit_lines: int = 4,
) -> Dict[str, WeaponEstimate]:
    """Fold this turn's ``station_intel`` + ``activity`` into prior estimates.

    Reads ``agent_view.station_intel.opponents`` — each entry has:

      * ``seat`` — opponent's player id
      * ``blue.band`` — current 0-5 pip band (~150 purity per pip)
      * ``activity.emps`` / ``.chaff`` — launches observed on the
        just-resolved night

    Returns the UPDATED estimates dict (and stores it for next turn).
    """
    prior = load_estimates(session_id, viewer)
    station_intel = agent_view.get("station_intel") or {}
    opponents = station_intel.get("opponents") or []

    updated: Dict[str, WeaponEstimate] = {}
    for opp in opponents:
        if not isinstance(opp, Mapping):
            continue
        seat = str(opp.get("seat") or "")
        if not seat or seat == viewer:
            continue

        # Start from prior (or fresh) estimate.
        est = prior.get(seat) or WeaponEstimate(seat=seat)

        # 1) LAUNCH decrement — hard signal. If opponent fired K EMPs
        #    last night, they used K units of EMP stock. Same for chaff.
        activity = opp.get("activity") or {}
        emps_launched = int(activity.get("emps") or 0)
        chaff_launched = int(activity.get("chaff") or 0)
        if emps_launched > 0:
            est.emps_min = max(0, est.emps_min - emps_launched)
            est.emps_max = max(0, est.emps_max - emps_launched)
            est.inferences.append(
                f"day-recap: {seat} launched {emps_launched} EMP(s) — stock decremented"
            )
        if chaff_launched > 0:
            est.chaff_min = max(0, est.chaff_min - chaff_launched)
            est.chaff_max = max(0, est.chaff_max - chaff_launched)
            est.inferences.append(
                f"day-recap: {seat} launched {chaff_launched} chaff — stock decremented"
            )

        # 2) BAND-DROP inference — soft signal. If blue.band dropped
        #    since last turn AND no observed launches account for it,
        #    they built weapon(s). Widen ``max`` on all weapons that
        #    fit in the drop.
        current_band = _extract_band(opp)
        if est.last_blue_band is not None and current_band is not None:
            delta_pips = est.last_blue_band - current_band
            # Only care about drops (rises = they harvested more blue).
            if delta_pips > 0:
                total_launched = emps_launched + chaff_launched
                # A launch does NOT consume blue at launch time — blue
                # was spent at build. So the whole band drop is
                # attributable to builds, regardless of launches.
                # Estimate the blue-purity range that fits in delta pips:
                #   at least delta_pips * BAND_STEP - (BAND_STEP - 1) since
                #   the drop had to cross that many pip boundaries; at
                #   most (delta_pips + 1) * BAND_STEP - 1 in the worst case.
                min_blue_spent = max(1, (delta_pips - 1) * _BAND_STEP + 1)
                max_blue_spent = (delta_pips + 1) * _BAND_STEP - 1
                # For each weapon type, add to ``max`` the number of
                # units of that type that could fit in the upper-bound
                # blue spend. That's the WORST case ("could they have
                # bought this many?"). ``min`` doesn't move — we can't
                # PROVE any specific weapon was built without more info.
                est.emps_max += max_blue_spent // _EMP_BLUE_COST
                est.chaff_max += max_blue_spent // _CHAFF_BLUE_COST
                est.inferences.append(
                    f"band-drop: {seat} blue {est.last_blue_band}→{current_band} "
                    f"({delta_pips} pip{'s' if delta_pips != 1 else ''}, "
                    f"~{min_blue_spent}-{max_blue_spent} blue spent, "
                    f"{total_launched} launch{'es' if total_launched != 1 else ''} observed) "
                    f"→ opponent built weapon(s)"
                )

        est.last_blue_band = current_band

        # Trim the audit trail so the prompt doesn't bloat over a
        # long season. Keep the most recent entries.
        if len(est.inferences) > max_audit_lines:
            est.inferences = est.inferences[-max_audit_lines:]

        updated[seat] = est

    # Carry forward any prior seats we didn't see this turn (shouldn't
    # happen mid-season, but defensive if the opponent seat list changes).
    for seat, est in prior.items():
        updated.setdefault(seat, est)

    store_estimates(session_id, viewer, updated)
    return updated


def _extract_band(opp: Mapping[str, Any]) -> Optional[int]:
    blue = opp.get("blue")
    if not isinstance(blue, Mapping):
        return None
    band = blue.get("band")
    if band is None:
        return None
    try:
        return int(band)
    except (TypeError, ValueError):
        return None
