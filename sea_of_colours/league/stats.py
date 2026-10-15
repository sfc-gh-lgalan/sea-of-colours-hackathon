"""What an agent actually did in a game — weapons and kills.

Read **after** the fact, out of the persisted session, rather than
captured while the match runs. Three things follow from that, all of
them worth having:

* it works on games played before this module existed;
* a bug here costs a re-read, not a re-run;
* nothing in the match path has to know the league exists, so an
  instrumentation mistake can never change a result.

The engine already keeps everything needed. Weapons fired live in
``weapons_used``; kills live in ``combat_attrib``, attributed
attacker→victim, which is what makes "killer moves" answerable per
agent rather than per game.

**Weapons bought is the one derived number.** There is no purchase
ledger — orbit debits the cost and increments ``weapon_stock``, and
firing moves a unit from stock to ``weapons_used``. Since both start at
zero, ``bought = stock + used`` is exact, and unlike parsing the orbit
log it cannot be broken by rewording a message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

#: Ordnance kinds, in the order a reader thinks about them: cheapest
#: first. Taken from the economy rather than written out, so a new
#: weapon appears here without an edit (``docs/ADDING_A_WEAPON.md``).
def weapon_kinds() -> tuple[str, ...]:
    try:
        from sea_of_colours.game.weapons import BLUE_COST_BY_KIND
        return tuple(sorted(BLUE_COST_BY_KIND, key=lambda k: BLUE_COST_BY_KIND[k]))
    except Exception:
        return ("snap", "emp", "chaff")


#: ``combat_attrib`` keys worth showing, and what to call them in front
#: of a human. These are the *aggressive* ones — the stats that mean an
#: agent did something to somebody, which is the whole question the day
#: was about.
KILL_STATS: tuple[tuple[str, str], ...] = (
    ("probes_crushed", "probes crushed"),
    ("emp_probes", "probes EMP'd"),
    ("snap_probes", "probes SNAPped"),
    ("emp_harvesters", "harvesters smothered"),
    ("snap_harvesters", "harvesters SNAPped"),
    ("chaff_jams", "slots jammed by chaff"),
    ("harv_damaged", "harvesters rammed"),
    ("harv_lost_chaff", "harvesters stranded"),
    ("probes_superseded", "probes superseded"),
)


@dataclass
class SeatStats:
    """One seat's season, in the terms the league cares about."""

    seat: str
    agent: str = ""
    score: int = 0
    bought: dict = field(default_factory=dict)
    fired: dict = field(default_factory=dict)
    #: Attacker→victim counts for this seat as the attacker, keyed by
    #: the ``combat_attrib`` stat name.
    kills: dict = field(default_factory=dict)
    blue_spent: int = 0
    red_harvested: int = 0
    harvesters_lost: int = 0

    @property
    def bought_total(self) -> int:
        return sum(self.bought.values())

    @property
    def fired_total(self) -> int:
        return sum(self.fired.values())

    @property
    def kills_total(self) -> int:
        return sum(sum(v.values()) for v in self.kills.values())

    @property
    def unfired(self) -> int:
        """Ordnance bought and never used — the day's signature failure.

        An agent that buys a rack and never fires it has built the
        buying half of the weapon pipeline and none of the firing half,
        which looks identical to "chose not to" from the outside. It is
        the single most useful number on the page for a team asking why
        their weapons changed nothing.
        """
        return max(0, self.bought_total - self.fired_total)

    def to_json(self) -> dict:
        return {
            "seat": self.seat,
            "agent": self.agent,
            "score": self.score,
            "bought": dict(self.bought),
            "fired": dict(self.fired),
            "kills": {k: dict(v) for k, v in self.kills.items()},
            "bought_total": self.bought_total,
            "fired_total": self.fired_total,
            "kills_total": self.kills_total,
            "unfired": self.unfired,
            "blue_spent": self.blue_spent,
            "red_harvested": self.red_harvested,
            "harvesters_lost": self.harvesters_lost,
        }


def for_session(
    store: Any, session_id: str, seats: Optional[Mapping[str, str]] = None,
) -> dict[str, SeatStats]:
    """Read one finished game. Never raises — a game with no stats is
    still a game with a result, and the league must not lose a match
    because the reader tripped.
    """
    from sea_of_colours.snowpark import engine as soc_engine

    out: dict[str, SeatStats] = {}
    try:
        sess = soc_engine._hydrate_session(store, session_id)
    except Exception:
        return out

    kinds = weapon_kinds()
    summary: Mapping[str, Any] = {}
    try:
        summary = soc_engine.get_endgame_summary(store, session_id) or {}
    except Exception:
        summary = {}
    by_seat = {
        str(p.get("seat")): p for p in (summary.get("players") or [])
        if isinstance(p, Mapping)
    }

    for seat in sess.players:
        stock = sess.weapon_stock.get(seat, {}) or {}
        used = sess.weapons_used.get(seat, {}) or {}
        row = by_seat.get(seat, {})
        combat = row.get("combat") or {}

        stats = SeatStats(
            seat=seat,
            agent=str((seats or {}).get(seat, "")),
            score=int(row.get("score", 0) or 0),
            fired={k: int(used.get(k, 0) or 0) for k in kinds},
            bought={
                k: int(stock.get(k, 0) or 0) + int(used.get(k, 0) or 0)
                for k in kinds
            },
            blue_spent=int(row.get("blue_spent", 0) or 0),
            red_harvested=int(row.get("red_harvested", 0) or 0),
            harvesters_lost=int(combat.get("harvesters_lost", 0) or 0),
        )
        for key, _label in KILL_STATS:
            victims = (sess.combat_attrib.get(key) or {}).get(seat, {}) or {}
            hits = {v: int(c) for v, c in victims.items() if int(c or 0) > 0}
            if hits:
                stats.kills[key] = hits
        out[seat] = stats

    # Score is the one field that must be right even when the summary
    # could not be built, because it is what decides the match.
    for seat, stats in out.items():
        if not stats.score:
            try:
                stats.score = int(sess.score_for(seat))
            except Exception:
                pass
    return out


def merge(rows: list[dict], agent: str) -> dict:
    """Roll one agent's per-game stats up into a career line."""
    kinds = weapon_kinds()
    bought = {k: 0 for k in kinds}
    fired = {k: 0 for k in kinds}
    kills: dict[str, int] = {}
    games = 0
    for row in rows:
        if row.get("agent") != agent:
            continue
        games += 1
        for k in kinds:
            bought[k] += int((row.get("bought") or {}).get(k, 0) or 0)
            fired[k] += int((row.get("fired") or {}).get(k, 0) or 0)
        for key, victims in (row.get("kills") or {}).items():
            kills[key] = kills.get(key, 0) + sum(int(c or 0) for c in victims.values())
    return {
        "agent": agent,
        "games": games,
        "bought": bought,
        "fired": fired,
        "kills": kills,
        "bought_total": sum(bought.values()),
        "fired_total": sum(fired.values()),
        "kills_total": sum(kills.values()),
        "unfired": max(0, sum(bought.values()) - sum(fired.values())),
    }
