"""The shape of the competition, and the best-of-three rule.

Pure: nothing here imports the engine, touches a store or plays a game.
That is deliberate — the format is the part most likely to be argued
about on the day ("what happens at 1-1-1?", "why do those two play an
extra match?"), and an argument you can settle by running a test is
shorter than one you settle by running a league.

**Straight knockout, with a play-in when the field is not a power of
two.** Win and go through, lose and go home. Seventeen entrants means
one play-in between the two lowest seeds, and the other fifteen enter
a bracket of sixteen. Seeding is by submission time, earliest first,
which is arbitrary but reproducible — nobody has to trust a shuffle.

**A match is three seasons, not one.** A single season between two LLM
agents is close to a coin toss: the models are stochastic, so the same
pairing on the same seed can go either way. Three narrows that without
costing the day.

**Sides swap, because the corners are not equal.** Seats p1 and p2 start
in different corners of the map, so a match played entirely from one
seat measures the corner as much as the agent. Game 1 puts A in p1,
game 2 puts A in p2 — which is why stopping early at 2-0 is *safe*: the
two games that produced it were side-balanced. Only the decider can be
lopsided, and its side is fixed by a hash of the match id, so it is
arbitrary but reproducible and evens out across a field.

**The decision order is wins, then margin.** First to two season wins
takes the match. If three seasons produce nobody on two — 1-1 with a
draw, or three draws — the match goes to aggregate point difference
across every season played. Only an exact aggregate tie is a real draw,
and in a scoring game that runs to the hundreds it is close to
impossible.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

#: Seasons in an ordinary match. Two decide it when one agent takes both.
BEST_OF = 3

#: Rounds that are played longer, by stage. The final is best-of-seven:
#: one season of Sea of Colours has a lot of variance in it — an LLM seat
#: plays the same board differently twice — and three seasons is enough
#: to sort a field but thin for the one match that names a champion.
#:
#: Only ever *longer*. A round shorter than best-of-three would make the
#: draw matter more than the play, and nothing here stops you setting
#: that, so don't.
BEST_OF_BY_STAGE = {"final": 7}


def best_of(stage: str) -> int:
    """Seasons in a match at this stage."""
    return int(BEST_OF_BY_STAGE.get(str(stage), BEST_OF))


def wins_needed(stage: str) -> int:
    """Season wins that take a match at this stage outright.

    Derived, never written down twice: a best-of-seven needs four and a
    best-of-three needs two, and hardcoding the ``2`` is exactly how a
    longer final ends up being awarded to whoever reached two first.
    """
    return best_of(stage) // 2 + 1


def unconditional_games(stage: str) -> int:
    """How many seasons get played no matter what the others do.

    A match cannot end before somebody reaches :func:`wins_needed`, so
    the first ``best_of - wins_needed`` seasons are certain: in a
    best-of-seven you cannot take four from three, so games one to
    three are always played. In a best-of-three only game one is.

    This is the only safe answer to "how much of a match can run at
    once". :mod:`runner` parallelises exactly this many and no more,
    which is what keeps the stopping rule here rather than in the
    concurrency code — a pool that ran a fourth season speculatively
    might play a season the match had already been decided without,
    and then have to decide whether it counted.
    """
    return max(1, best_of(stage) - wins_needed(stage))


def _digest(*parts: object) -> int:
    """A stable integer from anything — same input, same league.

    Python's ``hash()`` is salted per process, so using it would mean a
    match's seeds and decider side changed every time the runner
    restarted. A resumed league would then contradict the half of itself
    that already played.
    """
    raw = "|".join(str(p) for p in parts).encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


# ── the fixture list ───────────────────────────────────────────────


@dataclass(frozen=True)
class Game:
    """One season inside a match, with its sides and map already fixed."""

    n: int
    p1: str
    p2: str
    seed: int

    def seat_of(self, agent: str) -> str:
        return "p1" if self.p1 == agent else "p2"


@dataclass(frozen=True)
class Match:
    """Two agents, a stage, and a reproducible id."""

    id: str
    stage: str
    a: str
    b: str
    #: Set when a field is odd and this agent sits the round out. A bye
    #: is recorded rather than skipped so the standings can show why
    #: somebody has played fewer matches than everyone else.
    bye: bool = False

    def game(self, n: int) -> Game:
        """The nth season of this match — sides and seed already decided."""
        bo = best_of(self.stage)
        if not 1 <= n <= bo:
            raise ValueError(f"a {self.stage} match has {bo} games, not a game {n}")
        if n == bo and bo % 2:
            # The last season of an odd-length match cannot be balanced
            # against the ones before it, so it is settled by the match
            # id rather than by whoever was written down first.
            p1, p2 = (self.a, self.b) if _digest(self.id, "side") % 2 else (self.b, self.a)
        else:
            # Everything before it alternates, so the seasons pair off
            # and no agent gets the better corner more often.
            p1, p2 = (self.a, self.b) if n % 2 else (self.b, self.a)
        return Game(n=n, p1=p1, p2=p2, seed=seed_for(self.id, n))


def match_id(stage: str, a: str, b: str) -> str:
    """Stable id for a pairing, independent of which name came first."""
    lo, hi = sorted((a, b))
    return f"{stage}--{lo}--vs--{hi}"


def seed_for(mid: str, n: int) -> int:
    """Map seed for one game. Distinct per game, stable across reruns."""
    # Kept well inside 2**31 because the generator and the UI both round
    # -trip the seed through places that assume a normal-sized int.
    return _digest(mid, "seed", n) % 2_000_000_000


# ── results ────────────────────────────────────────────────────────


@dataclass
class GameResult:
    """What one season produced, in the match's own terms."""

    n: int
    session_id: str
    seed: int
    p1: str
    p2: str
    scores: dict = field(default_factory=dict)
    seconds: float = 0.0
    #: Turns where a model-backed seat played its safety net. Surfaced
    #: rather than swallowed: a season full of these was won by the
    #: built-in heuristic, and the standings should admit it.
    fallback_turns: int = 0
    aborted: str = ""
    error: str = ""

    def score_of(self, agent: str) -> int:
        seat = "p1" if self.p1 == agent else "p2"
        return int(self.scores.get(seat, 0) or 0)

    @property
    def winner(self) -> str:
        """Agent label, or "" for a scoring tie or a broken season."""
        if self.error or self.aborted or not self.scores:
            return ""
        s1, s2 = self.score_of(self.p1), self.score_of(self.p2)
        if s1 == s2:
            return ""
        return self.p1 if s1 > s2 else self.p2


@dataclass
class MatchResult:
    """A played match, and how it was decided."""

    id: str
    stage: str
    a: str
    b: str
    games: list = field(default_factory=list)
    bye: bool = False

    def wins(self, agent: str) -> int:
        return sum(1 for g in self.games if g.winner == agent)

    @property
    def margin(self) -> int:
        """Aggregate point difference, from A's side."""
        return sum(g.score_of(self.a) - g.score_of(self.b) for g in self.games)

    @property
    def complete(self) -> bool:
        """Has this match been played out far enough to stand?"""
        if self.bye:
            return True
        if any(g.error or g.aborted for g in self.games):
            return False
        if len(self.games) >= best_of(self.stage):
            return True
        return max(self.wins(self.a), self.wins(self.b)) >= wins_needed(self.stage)

    @property
    def winner(self) -> str:
        """Agent label, or "" for a draw. Empty while still unfinished."""
        if self.bye:
            return self.a
        if not self.complete:
            return ""
        wa, wb = self.wins(self.a), self.wins(self.b)
        need = wins_needed(self.stage)
        if wa >= need or wb >= need:
            return self.a if wa > wb else self.b
        if self.margin > 0:
            return self.a
        if self.margin < 0:
            return self.b
        return ""

    @property
    def decided_by(self) -> str:
        """``wins`` | ``margin`` | ``draw`` | ``bye`` | ``unplayed``."""
        if self.bye:
            return "bye"
        if not self.complete:
            return "unplayed"
        if max(self.wins(self.a), self.wins(self.b)) >= wins_needed(self.stage):
            return "wins"
        return "margin" if self.margin else "draw"

    @property
    def scoreline(self) -> str:
        return f"{self.wins(self.a)}-{self.wins(self.b)}"

    @property
    def fallback_turns(self) -> int:
        return sum(g.fallback_turns for g in self.games)


def needs_decider(result: MatchResult) -> bool:
    """Is another season required, given the ones already played?

    Was "is a *third* season required", which only made sense while
    every match was best-of-three. A best-of-seven asks the same
    question after each of six seasons, so the rule is stated once here
    and the runner stops when this says to.
    """
    if result.bye or len(result.games) >= best_of(result.stage):
        return False
    if any(g.error or g.aborted for g in result.games):
        return False              # broken: stop, do not grind on
    return (max(result.wins(result.a), result.wins(result.b))
            < wins_needed(result.stage))


# ── the bracket ────────────────────────────────────────────────────

#: Round names by how many matches the round holds. Anything larger
#: than a round of 16 is named for its size, which is what everyone
#: calls it anyway.
STAGE_BY_MATCHES = {1: "final", 2: "sf", 4: "qf", 8: "r16", 16: "r32"}

#: The play-in. Its own stage rather than "round one" because it is the
#: only round that does not halve the field.
PLAYIN = "playin"

STAGE_LABEL = {
    PLAYIN: "Play-in",
    "r32": "Round of 32",
    "r16": "Round of 16",
    "qf": "Quarter-final",
    "sf": "Semi-final",
    "final": "Final",
}


def bracket_size(n_entrants: int) -> int:
    """The power of two this field collapses into.

    A knockout needs a power of two, so a field of 17 plays one match
    before the bracket proper and enters it as 16. Rounding *down*
    rather than up is what makes that work: rounding up to 32 would
    mean fifteen agents sitting out a round they were never in.
    """
    if n_entrants < 2:
        return 0
    size = 1
    while size * 2 <= n_entrants:
        size *= 2
    return size


def standard_order(size: int) -> list[int]:
    """Seed positions for a bracket of this size, textbook order.

    Built by reflection — every seed ``s`` in a bracket of ``size/2``
    becomes the pair ``(s, size+1-s)``. That is the construction that
    gives the property the whole exercise depends on: the two top seeds
    land in opposite halves, the next two in opposite quarters, and so
    on, so the best agents can only meet late. Writing the order out by
    hand instead is how brackets end up quietly putting seeds 1 and 2
    in the same quarter.
    """
    if size < 2:
        return [1]
    order = [1, 2]
    while len(order) < size:
        m = len(order) * 2
        nxt: list[int] = []
        for s in order:
            nxt.extend((s, m + 1 - s))
        order = nxt
    return order


def play_in_matches(entrants: Sequence[str]) -> list[Match]:
    """The matches that trim the field to a power of two.

    The lowest seeds play them, paired highest-against-lowest among
    themselves, which is the convention and also the kindest reading:
    the agents that arrived last are the ones asked for an extra win.
    """
    n = len(entrants)
    size = bracket_size(n)
    extra = n - size
    if extra <= 0:
        return []

    # The bottom 2*extra seeds fight for the bottom `extra` slots.
    tail = list(entrants[size - extra:])
    out: list[Match] = []
    for i in range(extra):
        a, b = tail[i], tail[len(tail) - 1 - i]
        out.append(Match(id=match_id(PLAYIN, a, b), stage=PLAYIN, a=a, b=b))
    return out


def seeded_slots(
    entrants: Sequence[str], played: Iterable[MatchResult] = (),
) -> list[Optional[str]]:
    """The first round's slots in bracket order.

    A slot is ``None`` while the play-in that feeds it is unfinished,
    so a caller can draw the bracket before it is decided instead of
    waiting for it.
    """
    n = len(entrants)
    size = bracket_size(n)
    if size < 2:
        return []
    extra = n - size

    # Seeds that go straight in keep their seed number; each play-in
    # resolves into the slot its higher seed would have occupied.
    winners = {r.id: r.winner for r in played
               if r.stage == PLAYIN and r.complete}
    direct: dict[int, Optional[str]] = {
        i + 1: entrants[i] for i in range(size - extra)
    }
    for i, m in enumerate(play_in_matches(entrants)):
        direct[size - extra + 1 + i] = winners.get(m.id) or None

    return [direct.get(s) for s in standard_order(size)]


def stage_for(n_matches: int) -> str:
    return STAGE_BY_MATCHES.get(n_matches, f"r{n_matches * 2}")


def rounds(
    entrants: Sequence[str], played: Iterable[MatchResult] = (),
) -> list[list[Match]]:
    """Every round of the bracket, as far as results allow.

    The last list is the round waiting to be played. Rounds beyond it
    are omitted rather than guessed, because a pairing that depends on
    an unplayed result is not a fixture — it is a prediction, and a
    page that shows one gets read as a fixture.
    """
    results = {r.id: r for r in played}
    out: list[list[Match]] = []

    pi = play_in_matches(entrants)
    if pi:
        out.append(pi)
        done = all(getattr(results.get(m.id), "complete", False) for m in pi)
        if not done:
            return out

    slots = seeded_slots(entrants, played)
    if len(slots) < 2 or any(s is None for s in slots):
        return out

    side: list[str] = [s for s in slots if s]
    while len(side) >= 2:
        stage = stage_for(len(side) // 2)
        this: list[Match] = []
        for i in range(0, len(side), 2):
            a, b = side[i], side[i + 1]
            this.append(Match(id=match_id(stage, a, b), stage=stage, a=a, b=b))
        out.append(this)

        winners: list[str] = []
        for m in this:
            r = results.get(m.id)
            if r is None or not r.complete or not r.winner:
                # An unfinished round (or a genuine draw, which needs a
                # human) ends the walk. Everything after it is unknown.
                return out
            winners.append(r.winner)
        side = winners

    return out


def next_round(
    entrants: Sequence[str], played: Iterable[MatchResult] = (),
) -> list[Match]:
    """The matches to play now: the first round not yet complete."""
    results = {r.id: r for r in played}
    for rnd in rounds(entrants, played):
        pending = [m for m in rnd
                   if not getattr(results.get(m.id), "complete", False)]
        if pending:
            return pending
    return []


def champion(
    entrants: Sequence[str], played: Iterable[MatchResult] = (),
) -> str:
    """Who won it, or "" while anything is still outstanding."""
    results = {r.id: r for r in played}
    all_rounds = rounds(entrants, played)
    if not all_rounds or all_rounds[-1][0].stage != "final":
        return ""
    r = results.get(all_rounds[-1][0].id)
    return r.winner if r and r.complete else ""


# ── reading a bracket back ─────────────────────────────────────────


@dataclass
class Progress:
    """One agent's run through the bracket, for the stats panels.

    Not a league table — a knockout has no table, and inventing one
    would invite "but they were second" arguments about agents that
    never met. This is a record of how far each got and what its
    seasons looked like on the way.
    """

    agent: str
    seed: int = 0
    played: int = 0
    won: int = 0
    lost: int = 0
    drawn: int = 0
    season_wins: int = 0
    season_losses: int = 0
    season_draws: int = 0
    margin: int = 0
    fallback_turns: int = 0
    reached: str = ""
    out_at: str = ""
    beaten_by: str = ""
    opponents: tuple[str, ...] = ()


def progress(
    entrants: Sequence[str], played: Iterable[MatchResult] = (),
) -> list[Progress]:
    """Per-agent record, ordered by how far each agent got."""
    results = list(played)
    rows = {a: Progress(agent=a, seed=i + 1) for i, a in enumerate(entrants)}
    met: dict[str, list[str]] = {a: [] for a in entrants}

    # Stage depth, so "reached" can be compared. The play-in is depth 0
    # and the final is the deepest, whatever the bracket's size.
    order = [PLAYIN] + [stage_for(1 << k) for k in range(8, -1, -1)]
    depth = {s: i for i, s in enumerate(order)}

    for r in results:
        if not r.complete:
            continue
        for me, them in ((r.a, r.b), (r.b, r.a)):
            row = rows.get(me)
            if row is None:
                continue
            row.played += 1
            met[me].append(them)
            row.season_wins += r.wins(me)
            row.season_losses += r.wins(them)
            row.season_draws += sum(1 for g in r.games if not g.winner)
            row.margin += r.margin if me == r.a else -r.margin
            row.fallback_turns += r.fallback_turns
            if depth.get(r.stage, 0) >= depth.get(row.reached, -1):
                row.reached = r.stage
            if r.winner == me:
                row.won += 1
            elif r.winner == them:
                row.lost += 1
                row.out_at, row.beaten_by = r.stage, them
            else:
                row.drawn += 1

    for a, row in rows.items():
        row.opponents = tuple(met[a])

    king = champion(entrants, results)
    return sorted(
        rows.values(),
        key=lambda p: (
            0 if p.agent == king else 1,
            -depth.get(p.out_at or p.reached, -1),
            -p.margin,
            p.seed,
        ),
    )
