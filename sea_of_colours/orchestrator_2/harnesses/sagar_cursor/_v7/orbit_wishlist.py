"""Deterministic orbit → tactical hand-off compiler.

The orbit phase runs BEFORE each night's planning. During orbit, the
seat can buy weapons, build harvesters, ship parcels, etc. Tabula v3
doesn't yet have an orbit LLM — the orbit stub still submits an empty
action queue — but it now ALSO computes a lightweight ``wishlist`` of
tactical priorities and writes it to memory so the next night's harness
can read and inject them into the prompt.

The wishlist encodes "what the orbit turn wants the night turn to know".
Every entry has a priority (1 = must-act, 2 = should-consider, 3 = nice-
to-have), a tag (canonical name for the priority), and a one-sentence
rationale the agent can quote.

Rules are DETERMINISTIC in v3. Later versions can promote this to an
LLM that reasons about the wishlist itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional


# Wishlist tag vocabulary — keep it small and stable so the night prompt
# can reference tags by name in its RULES.
WISHLIST_TAGS = {
    "grab_blue": "Blue vault is low and you have spare harvester capacity — send one to blue if no pressing RED chain.",
    "be_cautious": "A harvester was destroyed last night — plan more carefully; consider fewer simultaneous drops.",
    "ship_hoard": "Hoard is >70% full — the next orbit turn should ship. Do not overfill or you waste harvest.",
    "replace_harvester": "Harvester stock below 2 — the next orbit turn should build. Meanwhile play with what you have.",
    "conserve_probes": "Probe stock low (<= 1) — probe only if area_gain is high; save the shot for a targeted reveal.",
    "hot_drop_ready": "You have both a probe and a harvester in orbit AND an echo signal — a hot drop this night is on the table.",
    "beware_emp": "Opponent likely has EMP stock (13-cell blast, 3 missiles per launch). EMP is a vision/landing-denial weapon — see DOCTRINE_BEWARE_EMP for the two mitigation flavors.",
    "beware_chaff": "Opponent likely has chaff stock (cancels your actions for 3 consecutive hours). Avoid predictable pickup windows (hour 6-9, 12-16) and space multi-harvester pickups >=3 hours apart.",
}


@dataclass(frozen=True)
class WishlistEntry:
    tag: str
    priority: int  # 1 = must-act, 2 = should-consider, 3 = nice-to-have
    rationale: str


@dataclass
class Wishlist:
    entries: List[WishlistEntry] = field(default_factory=list)
    day_computed: int = 0
    # v9 additive: explicit ORBIT->NIGHT directives (imperatives the orbit
    # phase wants tonight's plan to obey). Derived deterministically while
    # orbit is a heuristic stub; authored directly once orbit becomes an
    # agent. Rendered by the v9 prompt only — v6/v7/v8 ignore this field, so
    # their prompts are byte-identical.
    directives: List[str] = field(default_factory=list)

    def as_prompt_lines(self) -> List[str]:
        if not self.entries:
            return []
        by_prio = sorted(self.entries, key=lambda e: (e.priority, e.tag))
        out = ["TACTICAL PRIORITY FROM ORBIT (from last orbit turn — advisory):"]
        for e in by_prio:
            out.append(f"  [P{e.priority}] {e.tag}: {e.rationale}")
        return out


def _blue_grade(agent_view: Mapping[str, Any]) -> str:
    return (
        ((agent_view.get("station_intel") or {}).get("self") or {})
        .get("blue", {}).get("grade") or "unknown"
    )


def _hoard_fullness(agent_view: Mapping[str, Any]) -> float:
    hoard = (agent_view.get("hud") or {}).get("hoard") or {}
    return float(hoard.get("pct_full") or 0.0)


def _harvester_count_in_orbit(agent_view: Mapping[str, Any]) -> int:
    assets = agent_view.get("my_assets") or []
    return sum(
        1 for a in assets
        if isinstance(a, Mapping)
        and a.get("kind") == "harvester"
        and a.get("state") == "orbit"
    )


def _probe_in_orbit(agent_view: Mapping[str, Any]) -> bool:
    stock = int((agent_view.get("orbit") or {}).get("probe_stock") or 0)
    return stock > 0


def _probe_stock(agent_view: Mapping[str, Any]) -> int:
    return int((agent_view.get("orbit") or {}).get("probe_stock") or 0)


def _destroyed_last_night(agent_view: Mapping[str, Any]) -> int:
    ln = agent_view.get("last_night") or {}
    destroyed = ln.get("my_assets_destroyed") or []
    return sum(
        1 for r in destroyed
        if isinstance(r, Mapping) and r.get("kind") == "harvester"
    )


def _has_echo(agent_view: Mapping[str, Any]) -> bool:
    echo = ((agent_view.get("navigation") or {}).get("best_red_echo")) or []
    return bool(echo)


def compute_wishlist(
    agent_view: Mapping[str, Any],
    day: int,
    *,
    opponent_weapon_estimates: Optional[Mapping[str, Any]] = None,
) -> Wishlist:
    """Compile the wishlist from the agent's current state.

    Called during orbit phase resolution (or right after) so its output
    is available to the NEXT night's prompt build.

    ``opponent_weapon_estimates`` is an optional ``{seat -> WeaponEstimate}``
    mapping from :mod:`.opponent_weapons`. When provided, this compiler
    fires ``beware_emp`` / ``beware_chaff`` / ``beware_mines`` tags for
    any opponent whose corresponding ``*_max`` is > 0 (i.e. we can't rule
    out that they have stock of that weapon).
    """
    entries: List[WishlistEntry] = []

    # P1 tags — must-act
    if _destroyed_last_night(agent_view) > 0:
        entries.append(WishlistEntry(
            tag="be_cautious",
            priority=1,
            rationale=WISHLIST_TAGS["be_cautious"],
        ))

    blue_grade = _blue_grade(agent_view)
    harvesters_in_orbit = _harvester_count_in_orbit(agent_view)
    blue_visible = len(agent_view.get("blue_tiles") or []) > 0
    # Fire grab_blue when: blue is NOT high (i.e. we could use more),
    # we have 2+ harvesters (spare capacity), AND blue is actually
    # visible on the board (no point wishlist-ing blue we can't reach).
    if (
        blue_grade in ("empty", "low", "medium")
        and harvesters_in_orbit >= 2
        and blue_visible
    ):
        entries.append(WishlistEntry(
            tag="grab_blue",
            priority=1 if blue_grade in ("empty", "low") else 2,
            rationale=WISHLIST_TAGS["grab_blue"],
        ))

    # Opponent-weapon tags — priorities driven by weapon lethality:
    #   EMP (P1) — vision/landing denial: destroys probes + disables harvesters
    #   Chaff (P2) — pickup-killer: cancels 3 consecutive hours of your actions
    # (Mines are tracked by the estimator but doctrine + wishlist emission
    # are disabled — the current campaign design doesn't emphasize the
    # hidden-hazard case. Re-enable by uncommenting the mines block below.)
    if opponent_weapon_estimates:
        emp_carriers: List[str] = []
        chaff_carriers: List[str] = []
        for seat, est in opponent_weapon_estimates.items():
            if getattr(est, "emps_max", 0) > 0:
                emp_carriers.append(f"{seat}[{est.emps_min}..{est.emps_max}]")
            if getattr(est, "chaff_max", 0) > 0:
                chaff_carriers.append(f"{seat}[{est.chaff_min}..{est.chaff_max}]")
        if emp_carriers:
            entries.append(WishlistEntry(
                tag="beware_emp",
                priority=1,
                rationale=WISHLIST_TAGS["beware_emp"] + f" (est: {', '.join(emp_carriers)})",
            ))
        if chaff_carriers:
            entries.append(WishlistEntry(
                tag="beware_chaff",
                priority=2,
                rationale=WISHLIST_TAGS["beware_chaff"] + f" (est: {', '.join(chaff_carriers)})",
            ))

    # P2 tags — should-consider
    if _hoard_fullness(agent_view) > 0.7:
        entries.append(WishlistEntry(
            tag="ship_hoard",
            priority=2,
            rationale=WISHLIST_TAGS["ship_hoard"],
        ))
    if harvesters_in_orbit < 2:
        entries.append(WishlistEntry(
            tag="replace_harvester",
            priority=2,
            rationale=WISHLIST_TAGS["replace_harvester"],
        ))

    # P3 tags — nice-to-have
    if _probe_stock(agent_view) <= 1:
        entries.append(WishlistEntry(
            tag="conserve_probes",
            priority=3,
            rationale=WISHLIST_TAGS["conserve_probes"],
        ))
    if _has_echo(agent_view) and _probe_in_orbit(agent_view) and harvesters_in_orbit >= 1:
        entries.append(WishlistEntry(
            tag="hot_drop_ready",
            priority=3,
            rationale=WISHLIST_TAGS["hot_drop_ready"],
        ))

    # v9 additive: derive explicit night directives (ignored by v6/v7/v8).
    directives = compute_night_directives(
        agent_view, day, opponent_weapon_estimates=opponent_weapon_estimates,
    )
    return Wishlist(entries=entries, day_computed=day, directives=directives)


def compute_night_directives(
    agent_view: Mapping[str, Any],
    day: int,
    *,
    opponent_weapon_estimates: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    """Derive explicit ORBIT->NIGHT directives from current state (v9).

    These are crisp imperatives for tonight's plan — the channel the orbit
    turn (heuristic today, an agent later) uses to steer the night agent:
      * NEED BLUE            — blue vault low + blue reachable.
      * VAULT NEARLY FULL    — ship soon + collect HIGH-VALUE ONLY tonight.
      * CONSERVE PROBES      — probe stock low, spend the shot deliberately.

    Deterministic and additive; the shared ``compute_wishlist`` populates
    ``Wishlist.directives`` with these so the v9 prompt can render them.
    """
    out: List[str] = []

    blue_grade = _blue_grade(agent_view)
    blue_visible = len(agent_view.get("blue_tiles") or []) > 0
    if blue_grade in ("empty", "low") and blue_visible:
        out.append(
            "NEED BLUE: your blue vault is low and blue is reachable — send a "
            "harvester to blue tonight unless a pressing RED chain outranks it."
        )

    pct = _hoard_fullness(agent_view)
    if pct >= 0.8:
        out.append(
            f"VAULT NEARLY FULL ({int(round(pct * 100))}%): ship on the next "
            "orbit turn, and tonight collect HIGH-VALUE ONLY (pure/mass — skip "
            "trace/vein) so you don't overfill and waste the harvest."
        )

    if _probe_stock(agent_view) <= 1:
        out.append(
            "CONSERVE PROBES: probe stock is low — spend the shot only on a "
            "high area_gain reveal or a supersede, not a speculative peek."
        )

    return out


def wishlist_to_memory_payload(w: Wishlist) -> Dict[str, Any]:
    """JSON-safe serialization for persistence in SOC_AGENT_MEMORY."""
    return {
        "day_computed": int(w.day_computed),
        "entries": [
            {"tag": e.tag, "priority": int(e.priority), "rationale": e.rationale}
            for e in w.entries
        ],
    }


def wishlist_from_memory_payload(payload: Optional[Mapping[str, Any]]) -> Wishlist:
    """Round-trip the JSON payload back into a Wishlist."""
    if not isinstance(payload, Mapping):
        return Wishlist()
    entries: List[WishlistEntry] = []
    for e in (payload.get("entries") or []):
        if not isinstance(e, Mapping):
            continue
        entries.append(WishlistEntry(
            tag=str(e.get("tag") or ""),
            priority=int(e.get("priority") or 3),
            rationale=str(e.get("rationale") or ""),
        ))
    return Wishlist(
        entries=entries,
        day_computed=int(payload.get("day_computed") or 0),
    )
