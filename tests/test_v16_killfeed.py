"""v1.6 — attacker→victim combat attribution ("kill feed") unit tests.

Each test drives ``NightSimulator.run`` for a single mechanic and asserts
the resulting :attr:`GameSession.combat_attrib` matrix (and the
``moves_cancelled`` personal tally) picked up the right attacker/victim
pair. Attribution is a full NxN matrix — a seat can be its own victim —
so the tests check the exact ``[attacker][victim]`` cell.
"""

from __future__ import annotations

from sea_of_colours.game.entities import Entity
from sea_of_colours.game.policy import (
    ChaffFlareMove,
    EmpLaunchMove,
    SnapLaunchMove,
    StepMove,
)
from sea_of_colours.game.session import GameSession, Phase
from sea_of_colours.game.simulator import NightSimulator


def _fresh_night_session(seed: int = 7) -> GameSession:
    sess = GameSession.new(20, 14, seed=seed)
    assert sess.phase == Phase.PLANNING
    for p in sess.players:
        sess.blue_bank[p] = 0
    return sess


def _stock_weapon(sess: GameSession, owner: str, kind: str, n: int = 1) -> None:
    slot = sess.weapon_stock.setdefault(
        owner, {"emp": 0, "chaff": 0},
    )
    slot[kind] = int(slot.get(kind, 0)) + int(n)


def _cell(matrix, stat, atk, vic) -> int:
    return int(((matrix.get(stat) or {}).get(atk) or {}).get(vic, 0))


def test_probe_crush_attributed_to_crushing_house() -> None:
    sess = _fresh_night_session()
    # p1 harvester on the surface, one step from p2's probe.
    h1 = sess.entities["harvester_p1"]
    h1.x, h1.y = 5, 8
    h1.damaged = False
    sess.entities["probe_p2_x"] = Entity(
        id="probe_p2_x", entity_type="probe", owner="p2", x=6, y=8,
    )

    NightSimulator().run(sess, {"p1": [StepMove(unit="harvester_p1", to=(6, 8))]})

    assert _cell(sess.combat_attrib, "probes_crushed", "p1", "p2") == 1
    assert "probe_p2_x" not in sess.entities


def test_emp_probe_kill_attributed() -> None:
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "emp", n=1)
    sess.entities["probe_p2_y"] = Entity(
        id="probe_p2_y", entity_type="probe", owner="p2", x=8, y=8,
    )

    NightSimulator().run(sess, {"p1": [EmpLaunchMove(at=(8, 8))]})

    assert _cell(sess.combat_attrib, "emp_probes", "p1", "p2") == 1
    # And it is NOT also counted as a snap kill (v1.50).
    assert _cell(sess.combat_attrib, "snap_probes", "p1", "p2") == 0


def test_snap_probe_kill_is_not_reported_as_an_emp_kill() -> None:
    """v1.50 — the SNAP reuses the EMP's blast sweep internally.

    That sweep credited every probe it destroyed to ``emp_probes``
    whoever fired, so an agent that had never bought an EMP could end a
    season credited with probes "EMP'd" — which is what both finalists
    of the first knockout league showed. Same reason
    ``snap_harvesters`` is kept apart from ``harv_damaged``: a shot
    asset must not read as another weapon's casualty.
    """
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "snap", n=1)
    sess.entities["probe_p2_z"] = Entity(
        id="probe_p2_z", entity_type="probe", owner="p2", x=8, y=8,
    )

    NightSimulator().run(sess, {"p1": [SnapLaunchMove(at=(8, 8))]})

    assert "probe_p2_z" not in sess.entities, "the beacon should be gone"
    assert _cell(sess.combat_attrib, "snap_probes", "p1", "p2") == 1
    assert _cell(sess.combat_attrib, "emp_probes", "p1", "p2") == 0, \
        "a SNAP kill must not land in the EMP row"


def test_emp_harvester_catch_is_distinct_and_attributed() -> None:
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "emp", n=1)
    h2 = sess.entities["harvester_p2"]
    h2.x, h2.y = 8, 8
    h2.damaged = False

    # p1 fires an EMP over (8,8); p2 tries to act each hour but is smothered.
    NightSimulator().run(sess, {
        "p1": [EmpLaunchMove(at=(8, 8))],
        "p2": [
            StepMove(unit="harvester_p2", to=(7, 8)),
            StepMove(unit="harvester_p2", to=(9, 8)),
        ],
    })

    # Distinct: the harvester is caught for several hours but counts once.
    assert _cell(sess.combat_attrib, "emp_harvesters", "p1", "p2") == 1
    # p2 had at least one slot smothered by the cloud.
    assert int(sess.moves_cancelled.get("p2", 0)) >= 1


def test_chaff_jam_attributed_and_counts_cancelled_move() -> None:
    sess = _fresh_night_session()
    _stock_weapon(sess, "p1", "chaff", n=1)
    h2 = sess.entities["harvester_p2"]
    h2.x, h2.y = 5, 5
    h2.damaged = False

    NightSimulator().run(sess, {
        "p1": [ChaffFlareMove()],
        "p2": [StepMove(unit="harvester_p2", to=(5, 6))],
    })

    assert _cell(sess.combat_attrib, "chaff_jams", "p1", "p2") == 1
    assert int(sess.moves_cancelled.get("p2", 0)) >= 1


def test_harvester_collision_damage_is_mutual() -> None:
    sess = _fresh_night_session()
    h1 = sess.entities["harvester_p1"]
    h2 = sess.entities["harvester_p2"]
    h1.x, h1.y = 5, 5
    h2.x, h2.y = 6, 5
    h1.damaged = h2.damaged = False

    # p1 steps into p2's cell → blocked collision, both damaged.
    NightSimulator().run(sess, {
        "p1": [StepMove(unit="harvester_p1", to=(6, 5))],
    })

    assert _cell(sess.combat_attrib, "harv_damaged", "p1", "p2") == 1
    assert _cell(sess.combat_attrib, "harv_damaged", "p2", "p1") == 1


def test_combat_attrib_round_trips() -> None:
    sess = _fresh_night_session()
    sess._attrib("probes_crushed", "p1", "p2", 3)
    sess.moves_cancelled["p2"] = 4
    sess.emp_harv_seen["p1|harvester_p2"] = True

    restored = GameSession.from_dict(sess.to_dict())

    assert _cell(restored.combat_attrib, "probes_crushed", "p1", "p2") == 3
    assert int(restored.moves_cancelled.get("p2", 0)) == 4
    assert restored.emp_harv_seen.get("p1|harvester_p2") is True
