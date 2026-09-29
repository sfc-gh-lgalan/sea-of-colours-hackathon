"""Chaff outranks the salvo, and outranks itself (RULEBOOK §4.9.5).

Bug #16 / rule ruling. §4.9.5 says that on a covered hour **every** seat's
action is cancelled. "Action" includes a launch — but the engine resolved
EMP and chaff together in one seat-ordered pre-hour pass, before the chaff
gate was ever consulted, so weapons were the one action class chaff could
not stop. Two consequences, both live:

* **A salvo beat a flare declared for the same hour.** Whichever seat the
  loop reached first simply flew, which made "answer their chaff with an
  EMP" a reliable counter that no rule granted.
* **Chaff chained into a lock.** A flare fired inside its own window
  re-armed the window *and* re-granted its launcher launch-hour immunity,
  so N flares jammed the opponent for N+2 consecutive hours while the
  chaffer stayed immune throughout.

The ruling, and what these tests pin:

* A flare cancels a same-hour salvo.
* Nothing pre-empts on an hour already covered by an earlier window.
* A cancelled launch burns its **slot** but keeps its **munition** — a
  jammed house never fired, so it never spent the round.
* Two flares on the same hour still BOTH fly. Neither is inside a window
  yet, they produce the identical effect, and so one is simply wasted.
  That symmetry is intended: it is what stops mutual chaff being cheaper
  than solo chaff.

**SNAP is the one exception, and only on the flare's own hour (v1.57,
CHG-1a, §4.9.4).** A round outruns a flare going up, so a same-hour
flare does not stop it and it spends its charge; a window already
standing does stop it, and then the charge is kept like any other
jammed launch. Worth knowing why that arrived late: chaff-beats-SNAP
was asserted in §4.9.4 and pinned by **nothing** — SNAP shipped in
v1.36 and never got a line in this file — so the rule could have
flipped either way and no test would have noticed. Both halves are
pinned below now, along with the salvo staying stopped, because the
edit worth catching is a later one widening "the fast weapon" into
"weapons".
"""

from __future__ import annotations

from typing import Optional

from sea_of_colours.game.session import GameSession, Phase
from sea_of_colours.game.weapons import CHAFF_DURATION_HOURS

CHAFF = {"a": "chaff_flare"}
EMP = {"a": "emp_launch", "at": [15, 10]}
# An empty cell on purpose. These tests are about the hour clock, so a
# SNAP that hits nothing is the cleanest probe of whether it flew.
SNAP = {"a": "snap_launch", "at": [15, 10]}
WAIT = {"a": "wait"}


def _duel(*, p1_chaff: int = 0, p2_chaff: int = 0,
          p1_emp: int = 0, p2_emp: int = 0,
          p1_snap: int = 0, p2_snap: int = 0) -> GameSession:
    sess = GameSession.new(20, 14, seed=31)
    sess.phase = Phase.PLANNING
    sess.weapon_stock["p1"]["chaff"] = p1_chaff
    sess.weapon_stock["p2"]["chaff"] = p2_chaff
    sess.weapon_stock["p1"]["emp"] = p1_emp
    sess.weapon_stock["p2"]["emp"] = p2_emp
    sess.weapon_stock["p1"]["snap"] = p1_snap
    sess.weapon_stock["p2"]["snap"] = p2_snap
    # Far apart: this suite is about the hour clock, not collisions.
    sess.entities["harvester_p1"].x = 5
    sess.entities["harvester_p1"].y = 5
    sess.entities["harvester_p2"].x = 10
    sess.entities["harvester_p2"].y = 10
    return sess


def _resolve(sess: GameSession, p1: list[dict], p2: list[dict]) -> list[dict]:
    sess.stash_policy("p1", p1)
    sess.stash_policy("p2", p2)
    sess.maybe_resolve_if_ready()
    return [f for f in (sess.last_night_replay or []) if isinstance(f, dict)]


def _tagged(frames: list[dict], tag: str, hour: Optional[int] = None) -> list[dict]:
    hits = [f for f in frames if f.get("tag") == tag]
    if hour is not None:
        hits = [f for f in hits if int(f.get("hour") or 0) == hour]
    return hits


def test_an_unopposed_salvo_still_launches():
    """Guard. Without this the tests below could pass by breaking EMP."""
    sess = _duel(p1_emp=1)
    frames = _resolve(sess, [EMP, WAIT], [WAIT, WAIT])
    assert _tagged(frames, "emp_launch", hour=1), "an unjammed salvo must fly"
    assert sess.weapon_stock["p1"]["emp"] == 0, "and must cost its charge"


def test_a_flare_cancels_a_same_hour_salvo():
    sess = _duel(p1_chaff=1, p2_emp=1)
    frames = _resolve(sess, [CHAFF, WAIT, WAIT], [EMP, WAIT, WAIT])

    assert _tagged(frames, "chaff_flare", hour=1), "the flare fires"
    assert not _tagged(frames, "emp_launch"), (
        "the salvo was declared for a jammed hour and must not fly"
    )
    assert _tagged(frames, "chaffed", hour=1), "and it is cancelled as chaffed"


def test_a_cancelled_salvo_keeps_its_charge():
    """The house never fired, so it never spent the round."""
    sess = _duel(p1_chaff=1, p2_emp=1)
    _resolve(sess, [CHAFF, WAIT, WAIT], [EMP, WAIT, WAIT])
    assert sess.weapon_stock["p2"]["emp"] == 1, (
        "a jammed salvo must stay in stock for a later hour or night"
    )


def test_a_salvo_inside_an_existing_window_keeps_its_charge():
    """Same rule one hour later: the window from hour 1 still covers it."""
    sess = _duel(p1_chaff=1, p2_emp=1)
    frames = _resolve(sess, [CHAFF, WAIT, WAIT], [WAIT, EMP, WAIT])
    assert not _tagged(frames, "emp_launch"), "hour 2 is inside the window"
    assert sess.weapon_stock["p2"]["emp"] == 1


def test_chaff_cannot_be_chained_into_a_lock():
    """Three flares queued back to back; only the first is on a free hour."""
    sess = _duel(p1_chaff=3)
    frames = _resolve(sess, [CHAFF, CHAFF, CHAFF, WAIT], [WAIT] * 4)

    fired = _tagged(frames, "chaff_flare")
    assert len(fired) == 1, (
        f"only the hour-1 flare is on an uncovered hour, got {len(fired)} launches"
    )
    assert int(fired[0].get("hour") or 0) == 1
    assert sess.weapon_stock["p1"]["chaff"] == 2, (
        "the two jammed flares must remain in stock"
    )


def test_a_cancelled_flare_still_burns_its_slot():
    """It is cancelled, not deferred: the queue moves on rather than
    re-attempting the flare once the window lifts."""
    sess = _duel(p1_chaff=3)
    frames = _resolve(sess, [CHAFF, CHAFF, CHAFF, WAIT], [WAIT] * 4)

    window_end = 1 + CHAFF_DURATION_HOURS - 1
    assert not _tagged(frames, "chaff_flare", hour=window_end + 1), (
        "the queued flares were cancelled during the window, so none may "
        "resurface on the first free hour after it"
    )
    p1_after = [
        f for f in frames
        if f.get("owner") == "p1" and int(f.get("hour") or 0) == window_end + 1
    ]
    assert p1_after and all(f.get("tag") == "wait" for f in p1_after), (
        f"expected p1's 4th queued row (a wait) at hour {window_end + 1}, "
        f"got {[f.get('tag') for f in p1_after]}"
    )


def test_two_flares_on_the_same_hour_both_fly():
    """Neither is inside a window yet, so both fire — and since the effect
    is identical for every seat, one flare is simply wasted."""
    sess = _duel(p1_chaff=1, p2_chaff=1)
    frames = _resolve(sess, [CHAFF, WAIT, WAIT], [CHAFF, WAIT, WAIT])

    assert len(_tagged(frames, "chaff_flare", hour=1)) == 2, "both must fire"
    assert sess.weapon_stock["p1"]["chaff"] == 0
    assert sess.weapon_stock["p2"]["chaff"] == 0, "both paid; one bought nothing"


# ── SNAP is the exception, and only on the flare's own hour (v1.57) ──
#
# CHG-1a. The round is faster than the flare going up, so being flared
# AT does not stop it; a window already standing does, because by then
# there is nothing left to outrun. Everything below exists because the
# old rule — chaff beats SNAP outright — was stated in §4.9.4 and pinned
# by nothing at all, so it could have flipped either way unnoticed.

def test_an_unopposed_snap_still_launches():
    """Guard, the same one the salvo gets above."""
    sess = _duel(p1_snap=1)
    frames = _resolve(sess, [SNAP, WAIT], [WAIT, WAIT])
    assert _tagged(frames, "snap_launch", hour=1), "an unjammed round must fly"
    assert sess.weapon_stock["p1"]["snap"] == 0, "and must cost its charge"


def test_a_snap_outruns_a_flare_fired_on_its_own_hour():
    """The rule change. Compare `test_a_flare_cancels_a_same_hour_salvo`:
    identical setup, opposite outcome, and that asymmetry IS the weapon."""
    sess = _duel(p1_chaff=1, p2_snap=1)
    frames = _resolve(sess, [CHAFF, WAIT, WAIT], [SNAP, WAIT, WAIT])

    assert _tagged(frames, "chaff_flare", hour=1), "the flare still fires"
    assert _tagged(frames, "snap_launch", hour=1), (
        "a SNAP outruns a flare going up on the same hour and must fly"
    )


def test_a_snap_that_outran_the_flare_spends_its_round():
    """It fired, so it costs. The refund only ever existed because a
    jammed house never fired."""
    sess = _duel(p1_chaff=1, p2_snap=1)
    _resolve(sess, [CHAFF, WAIT, WAIT], [SNAP, WAIT, WAIT])
    assert sess.weapon_stock["p2"]["snap"] == 0


def test_a_snap_inside_an_existing_window_does_not_fly():
    """The other half, and the half that keeps chaff worth buying: once
    the window is up the round is cancelled like anything else."""
    sess = _duel(p1_chaff=1, p2_snap=1)
    frames = _resolve(sess, [CHAFF, WAIT, WAIT], [WAIT, SNAP, WAIT])

    assert not _tagged(frames, "snap_launch"), "hour 2 is inside the window"
    assert _tagged(frames, "chaffed", hour=2), "and it is cancelled as chaffed"
    assert sess.weapon_stock["p2"]["snap"] == 1, (
        "a round that never flew must stay in stock"
    )


def test_the_salvo_is_still_stopped_on_the_flares_own_hour():
    """The exception is SNAP's alone. Pinned here rather than left to the
    EMP tests above, because the failure worth catching is a future edit
    widening 'fast weapon' to mean 'any weapon'."""
    sess = _duel(p1_chaff=1, p2_emp=1, p2_snap=1)
    frames = _resolve(sess, [CHAFF, WAIT, WAIT], [EMP, WAIT, WAIT])

    assert not _tagged(frames, "emp_launch"), "the salvo is not fast"
    assert sess.weapon_stock["p2"]["emp"] == 1


def test_two_flares_on_the_same_hour_do_not_extend_the_window():
    """The waste is the point — a second flare must buy no extra hours."""
    sess = _duel(p1_chaff=1, p2_chaff=1)
    frames = _resolve(sess, [CHAFF] + [WAIT] * 5, [CHAFF] + [WAIT] * 5)

    window_end = 1 + CHAFF_DURATION_HOURS - 1
    assert _tagged(frames, "chaffed", hour=window_end), "window covers its last hour"
    assert not _tagged(frames, "chaffed", hour=window_end + 1), (
        f"the window must end at hour {window_end}; a second same-hour flare "
        "cannot stack onto it"
    )
