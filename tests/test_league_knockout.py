"""The competition format, tested without playing anything.

``league/fixtures`` is pure on purpose, so every rule the room might
argue about on the day is settled here in milliseconds rather than in
an hour of seasons: who plays an extra match, whether a 2-0 really can
stop early, what a 1-1-1 does, and — the one that quietly ruins
brackets — whether the top seeds are actually kept apart.
"""

from __future__ import annotations

import pytest

from sea_of_colours.league import fixtures as fx


FIELD_17 = [f"a{i:02d}" for i in range(1, 18)]


# ── helpers ────────────────────────────────────────────────────────


def _game(n, p1, p2, s1, s2):
    return fx.GameResult(n=n, session_id=f"s{n}", seed=n,
                         p1=p1, p2=p2, scores={"p1": s1, "p2": s2})


def _played(match, *scorelines):
    """A MatchResult for ``match`` from (winner, margin) pairs.

    Takes the winner by label so a test reads as "A won game one",
    which is the thing being asserted, rather than as seat scores that
    the reader has to map back through the side swap.
    """
    games = []
    for i, (winner, margin) in enumerate(scorelines, start=1):
        g = match.game(i)
        if winner == "":
            s1 = s2 = 100
        elif winner == g.p1:
            s1, s2 = 100 + margin, 100
        else:
            s1, s2 = 100, 100 + margin
        games.append(_game(i, g.p1, g.p2, s1, s2))
    return fx.MatchResult(id=match.id, stage=match.stage,
                          a=match.a, b=match.b, games=games)


def _win_all(matches, pick=lambda m: m.a):
    """Play out a whole round as a sweep in favour of ``pick``.

    The number of seasons is the stage's own, not two. It was two, and
    when the final became best-of-seven that quietly stopped completing
    it — so ``next_round`` kept handing the final back and the
    walk-the-whole-bracket tests span forever rather than failing.
    A helper that cannot finish a match is a hang, not an assertion.
    """
    return [_played(m, *[(pick(m), 10)] * fx.wins_needed(m.stage))
            for m in matches]


# ── the bracket's shape ────────────────────────────────────────────


@pytest.mark.parametrize("n,size,playins", [
    (2, 2, 0), (4, 4, 0), (5, 4, 1), (7, 4, 3), (8, 8, 0),
    (16, 16, 0), (17, 16, 1), (20, 16, 4), (31, 16, 15), (32, 32, 0),
])
def test_a_field_collapses_to_a_power_of_two(n, size, playins):
    field = [f"x{i}" for i in range(n)]
    assert fx.bracket_size(n) == size
    assert len(fx.play_in_matches(field)) == playins
    # Every play-in is two agents fighting for one slot, so the slots
    # after them must come out exactly at the bracket size.
    assert len(fx.seeded_slots(field)) == size


def test_seventeen_means_one_play_in_between_the_last_two_seeds():
    pi = fx.play_in_matches(FIELD_17)
    assert len(pi) == 1
    assert {pi[0].a, pi[0].b} == {"a16", "a17"}
    assert pi[0].stage == fx.PLAYIN


def test_the_fifteen_direct_entrants_never_play_a_play_in():
    playing = {m.a for m in fx.play_in_matches(FIELD_17)} | {
        m.b for m in fx.play_in_matches(FIELD_17)}
    assert playing == {"a16", "a17"}
    assert len(set(FIELD_17) - playing) == 15


@pytest.mark.parametrize("size", [2, 4, 8, 16, 32, 64])
def test_standard_order_is_a_permutation_of_its_seeds(size):
    order = fx.standard_order(size)
    assert sorted(order) == list(range(1, size + 1))


@pytest.mark.parametrize("size", [4, 8, 16, 32, 64])
def test_every_first_round_tie_sums_to_one_more_than_the_bracket(size):
    """The property that makes it a seeding rather than a shuffle."""
    order = fx.standard_order(size)
    for i in range(0, size, 2):
        assert order[i] + order[i + 1] == size + 1


@pytest.mark.parametrize("size", [4, 8, 16, 32])
def test_top_seeds_are_kept_apart(size):
    """1 and 2 in opposite halves, 3 and 4 in opposite quarters, and so
    on — the whole reason to seed at all. This is the check that
    catches a hand-written bracket order."""
    order = fx.standard_order(size)
    pos = {s: i for i, s in enumerate(order)}
    groups = 2
    while groups <= size:
        # Cut the bracket into `groups` equal blocks. The top `groups`
        # seeds must land one per block: two halves hold seeds 1 and 2,
        # four quarters hold seeds 1-4, and so on.
        block = size // groups
        seen: dict[int, int] = {}
        for s in range(1, groups + 1):
            b = pos[s] // block
            assert b not in seen, (
                f"seeds {seen.get(b)} and {s} share 1 of {groups} blocks")
            seen[b] = s
        groups *= 2


def test_the_two_earliest_submissions_can_only_meet_in_the_final():
    rs = []
    # Seed 1 and seed 2 both win everything.
    for _ in range(6):
        nxt = fx.next_round(FIELD_17, rs)
        if not nxt:
            break
        rs += _win_all(nxt, pick=lambda m: m.a if m.a < m.b else m.b)
    final = [r for r in rs if r.stage == "final"]
    assert len(final) == 1
    assert {final[0].a, final[0].b} == {"a01", "a02"}


# ── how a match is decided ─────────────────────────────────────────


def test_sides_swap_between_the_first_two_games():
    m = fx.Match(id="t", stage="r16", a="A", b="B")
    g1, g2 = m.game(1), m.game(2)
    assert (g1.p1, g1.p2) == ("A", "B")
    assert (g2.p1, g2.p2) == ("B", "A")
    assert g1.seed != g2.seed


def test_a_decider_side_is_arbitrary_but_stable():
    m = fx.Match(id="t", stage="r16", a="A", b="B")
    assert m.game(3).p1 == fx.Match(id="t", stage="r16", a="A", b="B").game(3).p1
    # And it is not simply "whoever was written down first" twice over.
    sides = {fx.Match(id=f"t{i}", stage="r16", a="A", b="B").game(3).p1
             for i in range(12)}
    assert sides == {"A", "B"}


def test_two_nil_ends_the_match_after_two_games():
    m = fx.Match(id="t", stage="r16", a="A", b="B")
    r = _played(m, ("A", 30), ("A", 12))
    assert r.complete and r.winner == "A"
    assert r.scoreline == "2-0"
    assert r.decided_by == "wins"
    assert not fx.needs_decider(r)


def test_one_one_needs_a_decider():
    m = fx.Match(id="t", stage="r16", a="A", b="B")
    r = _played(m, ("A", 30), ("B", 12))
    assert not r.complete
    assert r.winner == ""
    assert fx.needs_decider(r)


def test_a_decider_settles_it_on_wins_not_margin():
    """The case that decides whether the rule is worth stating.

    B loses game one by four hundred points and wins the next two by
    one each. A therefore has much the better aggregate — and loses
    anyway, because wins are checked before margin. Margin is only the
    tie-break, never an override.
    """
    m = fx.Match(id="t", stage="r16", a="A", b="B")
    r = _played(m, ("A", 400), ("B", 1), ("B", 1))
    assert r.complete
    assert r.winner == "B", "two season wins beats a big single margin"
    assert r.decided_by == "wins"
    assert r.margin > 0, "A is ahead on aggregate and still loses"


def test_a_draw_in_the_mix_falls_through_to_aggregate_margin():
    m = fx.Match(id="t", stage="r16", a="A", b="B")
    r = _played(m, ("A", 5), ("B", 40), ("", 0))
    assert r.complete
    assert max(r.wins("A"), r.wins("B")) < 2
    assert r.decided_by == "margin"
    assert r.winner == "B"


def test_three_draws_is_a_real_draw():
    m = fx.Match(id="t", stage="r16", a="A", b="B")
    r = _played(m, ("", 0), ("", 0), ("", 0))
    assert r.complete
    assert r.margin == 0
    assert r.decided_by == "draw"
    assert r.winner == ""


def test_a_draw_stops_the_bracket_rather_than_guessing():
    """A knockout cannot advance a draw, and inventing a rule here
    would hide it. The walk stops so a human decides."""
    pi = fx.play_in_matches(FIELD_17)
    drawn = _played(pi[0], ("", 0), ("", 0), ("", 0))
    assert drawn.winner == ""
    assert fx.next_round(FIELD_17, [drawn]) == []


def test_a_broken_season_leaves_the_match_incomplete():
    m = fx.Match(id="t", stage="r16", a="A", b="B")
    r = _played(m, ("A", 10), ("A", 10))
    r.games[1].error = "cortex timeout"
    assert not r.complete
    assert r.winner == ""


# ── walking the bracket ────────────────────────────────────────────


def test_the_first_round_offered_is_the_play_in_alone():
    nxt = fx.next_round(FIELD_17)
    assert len(nxt) == 1
    assert nxt[0].stage == fx.PLAYIN


def test_the_round_of_16_waits_for_the_play_in():
    assert fx.seeded_slots(FIELD_17).count(None) == 1
    pi = fx.play_in_matches(FIELD_17)
    rs = _win_all(pi)
    slots = fx.seeded_slots(FIELD_17, rs)
    assert None not in slots
    assert slots.count(pi[0].a) == 1
    assert pi[0].b not in slots


def test_the_play_in_winner_takes_the_slot_facing_the_top_seed():
    pi = fx.play_in_matches(FIELD_17)
    rs = _win_all(pi)
    r16 = fx.next_round(FIELD_17, rs)
    assert len(r16) == 8
    top = [m for m in r16 if "a01" in (m.a, m.b)]
    assert len(top) == 1
    assert {top[0].a, top[0].b} == {"a01", pi[0].a}


def test_the_whole_thing_is_sixteen_matches_and_one_champion():
    rs: list = []
    stages: list[str] = []
    for _ in range(9):
        nxt = fx.next_round(FIELD_17, rs)
        if not nxt:
            break
        stages.append(f"{nxt[0].stage}x{len(nxt)}")
        rs += _win_all(nxt)
    assert stages == ["playinx1", "r16x8", "qfx4", "sfx2", "finalx1"]
    assert len(rs) == 16
    assert fx.champion(FIELD_17, rs) == "a01"
    assert fx.next_round(FIELD_17, rs) == []


def test_the_whole_thing_is_at_most_fifty_two_seasons():
    """Sixteen matches: fifteen best-of-three and one best-of-seven.

    Was 48 when every round was the same length. The guard against an
    infinite walk is the round counter, not the assertion at the end —
    a bracket that cannot complete would otherwise hang the suite.
    """
    total = 0
    rounds = 0
    rs: list = []
    while True:
        nxt = fx.next_round(FIELD_17, rs)
        if not nxt:
            break
        rounds += 1
        assert rounds <= 8, "bracket is not converging"
        for m in nxt:
            # Worst case is every match going its full distance.
            total += fx.best_of(m.stage)
        rs += _win_all(nxt)
    assert total == 15 * 3 + 7 == 52


def test_resuming_mid_round_only_offers_what_is_missing():
    pi = _win_all(fx.play_in_matches(FIELD_17))
    r16 = fx.next_round(FIELD_17, pi)
    done = _win_all(r16[:3])
    pending = fx.next_round(FIELD_17, pi + done)
    assert len(pending) == 5
    assert {m.id for m in pending} == {m.id for m in r16[3:]}


def test_the_bracket_is_reproducible_across_restarts():
    """Ids, seeds and decider sides must not move when the runner is
    restarted — a resumed league would otherwise contradict the half
    of itself already on disk."""
    a = [(m.id, m.game(1).seed, m.game(3).p1)
         for m in fx.next_round(FIELD_17)]
    b = [(m.id, m.game(1).seed, m.game(3).p1)
         for m in fx.next_round(FIELD_17)]
    assert a == b


def test_match_ids_do_not_depend_on_which_name_came_first():
    assert fx.match_id("qf", "A", "B") == fx.match_id("qf", "B", "A")


def test_every_match_in_the_league_has_a_distinct_id():
    rs: list = []
    ids: list[str] = []
    while True:
        nxt = fx.next_round(FIELD_17, rs)
        if not nxt:
            break
        ids += [m.id for m in nxt]
        rs += _win_all(nxt)
    assert len(ids) == len(set(ids)) == 16


def test_no_agent_plays_twice_in_a_round():
    pi = _win_all(fx.play_in_matches(FIELD_17))
    for rnd in (fx.next_round(FIELD_17, pi),):
        seats = [m.a for m in rnd] + [m.b for m in rnd]
        assert len(seats) == len(set(seats))


# ── reading it back ────────────────────────────────────────────────


def test_progress_puts_the_champion_first_and_records_who_went_out_where():
    rs: list = []
    while True:
        nxt = fx.next_round(FIELD_17, rs)
        if not nxt:
            break
        rs += _win_all(nxt)
    rows = fx.progress(FIELD_17, rs)
    assert rows[0].agent == "a01"
    assert rows[0].out_at == ""
    assert rows[0].won == 4 and rows[0].lost == 0

    beaten_in_final = next(r for r in rows if r.out_at == "final")
    assert beaten_in_final.beaten_by == "a01"

    # The agent knocked out in the play-in is last, and played once.
    tail = rows[-1]
    assert tail.out_at == fx.PLAYIN
    assert tail.played == 1


def test_progress_counts_seasons_not_just_matches():
    pi = fx.play_in_matches(FIELD_17)
    rs = [_played(pi[0], (pi[0].a, 10), (pi[0].b, 10), (pi[0].a, 10))]
    rows = {r.agent: r for r in fx.progress(FIELD_17, rs)}
    assert rows[pi[0].a].season_wins == 2
    assert rows[pi[0].a].season_losses == 1
    assert rows[pi[0].b].season_wins == 1


def test_progress_surfaces_fallback_turns_rather_than_hiding_them():
    m = fx.play_in_matches(FIELD_17)[0]
    r = _played(m, (m.a, 10), (m.a, 10))
    r.games[0].fallback_turns = 5
    rows = {p.agent: p for p in fx.progress(FIELD_17, [r])}
    assert rows[m.a].fallback_turns == 5


# ── a longer final (v1.50) ──────────────────────────────────────────
#
# The final is best-of-seven while every other round is best-of-three.
# These pin the two things that break when a match length becomes a
# per-stage property: the wins needed must be DERIVED (a hardcoded 2
# hands the longer final to whoever reaches two first), and the sides
# must still pair off so nobody gets the better corner more often.


def test_the_final_is_longer_than_the_rest():
    assert fx.best_of("final") == 7
    for stage in ("playin", "r16", "qf", "sf"):
        assert fx.best_of(stage) == 3


def test_wins_needed_is_derived_not_written_down():
    assert fx.wins_needed("sf") == 2
    assert fx.wins_needed("final") == 4


def test_two_wins_does_not_take_a_final():
    """The bug this whole change risks introducing."""
    m = fx.Match(id="f", stage="final", a="A", b="B")
    r = _played(m, ("A", 30), ("A", 12))
    assert not r.complete, "2-0 is not a best-of-seven"
    assert r.winner == ""
    assert fx.needs_decider(r)


def test_four_wins_takes_a_final():
    m = fx.Match(id="f", stage="final", a="A", b="B")
    r = _played(m, ("A", 30), ("A", 12), ("B", 9), ("A", 4), ("A", 7))
    assert r.complete and r.winner == "A"
    assert r.scoreline == "4-1"
    assert r.decided_by == "wins"
    assert not fx.needs_decider(r), "stop at four, do not play seven"


def test_a_final_can_go_the_full_seven():
    m = fx.Match(id="f", stage="final", a="A", b="B")
    trading = [("A", 30), ("B", 12), ("A", 9), ("B", 4), ("A", 7), ("B", 3)]
    six = _played(m, *trading)
    assert not six.complete, "3-3 is not a result"
    assert fx.needs_decider(six)

    seven = _played(m, *trading, ("A", 1))
    assert seven.complete and seven.winner == "A"
    assert seven.scoreline == "4-3"
    assert seven.decided_by == "wins"
    assert not fx.needs_decider(seven)


def test_final_sides_pair_off_across_the_first_six():
    m = fx.Match(id="f", stage="final", a="A", b="B")
    firsts = [m.game(n).p1 for n in range(1, 7)]
    assert firsts.count("A") == 3 and firsts.count("B") == 3, firsts
    # And consecutive seasons swap, rather than running AAABBB.
    for n in range(1, 6):
        assert m.game(n).p1 != m.game(n + 1).p1


def test_the_seventh_season_side_is_arbitrary_but_stable():
    a = fx.Match(id="f", stage="final", a="A", b="B").game(7).p1
    b = fx.Match(id="f", stage="final", a="A", b="B").game(7).p1
    assert a == b
    sides = {fx.Match(id=f"f{i}", stage="final", a="A", b="B").game(7).p1
             for i in range(12)}
    assert sides == {"A", "B"}


def test_a_final_has_no_eighth_season():
    m = fx.Match(id="f", stage="final", a="A", b="B")
    try:
        m.game(8)
    except ValueError:
        return
    raise AssertionError("game(8) should not exist in a best-of-seven")


def test_every_season_of_a_final_gets_its_own_seed():
    m = fx.Match(id="f", stage="final", a="A", b="B")
    seeds = [m.game(n).seed for n in range(1, 8)]
    assert len(set(seeds)) == 7


def test_only_the_certain_seasons_can_run_at_once():
    """What ``runner.play_match_wide`` is allowed to parallelise.

    Three of a best-of-seven, because you cannot take four from three,
    so none of the first three can make another irrelevant. One of a
    best-of-three, for the same reason. Getting this wrong means a pool
    playing a season the match had already been decided without.
    """
    assert fx.unconditional_games("final") == 3
    assert fx.unconditional_games("sf") == 1
    for stage in ("playin", "r16", "qf", "sf", "final"):
        bo, need = fx.best_of(stage), fx.wins_needed(stage)
        n = fx.unconditional_games(stage)
        # Nobody can be finished before the last of them has been played.
        assert n + need > bo - 1 or n < need
        assert n == max(1, bo - need)
        assert n < bo, "a match must keep at least one season conditional"
