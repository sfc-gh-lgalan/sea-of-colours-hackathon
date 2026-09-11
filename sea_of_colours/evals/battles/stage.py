"""Build a playable night from a board, a rung and a loadout.

One function matters here: :func:`stage`. Everything else is the detail
of getting an honest board out of ``WorldBuilder``.

The ordering below is not arbitrary and changing it will quietly break
fixtures, so it is worth stating once:

1. **Terrain first, on a blank board.** ``reveal_red`` and friends only
   overwrite the cells they are given, so the noise generator's ore has
   to be scrubbed first or it survives underneath. It used to, and the
   consequence was bad: every board carried a handful of undeclared
   purity-255 cells, so ``lone_pure`` boards had four pures on them and
   an agent could be marked down for walking to a real one the board
   never admitted existed. Anything that reads the grid — vision,
   ledgers, the redsign region — has to see the final terrain, so this
   goes before everything.
2. **Synthetic green after red.** Green marks a cell a rival already
   stripped. It has to land after the red pass or the red pass puts the
   ore back, and the whole point of those cells is that they look
   valuable in stale memory and cost -100 to touch.
3. **The beacon after the cells it covers.** ``with_redsign`` records
   the pure cells as attributed so the engine does not re-mint a
   duplicate region over them at the next dawn.
4. **Vision last.** Probe placement is what decides whether the pure is
   drop-legal this hour or needs lighting first, and that is the single
   most consequential fact on most of these boards.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from sea_of_colours.evals.builder import WorldBuilder
from sea_of_colours.evals.battles.boards import PURE, Board
from sea_of_colours.evals.battles.ladder import Loadout, Rung

# Where extra opposition gets parked when a rung asks for more of it
# than the board captured. Kept off the seam by a few cells so the
# additions read as "closing in" rather than "already on top of it",
# which would change the decision instead of hardening it.
_RING = ((3, 2), (-3, 2), (2, -3), (-2, -3), (4, 0), (0, 4), (-4, 0), (0, -4))


@dataclass(frozen=True)
class StagedBattle:
    """A built session plus everything needed to score and explain it."""

    board: Board
    rung: Rung
    loadout: Loadout
    store: Any
    session_id: str
    seat: str

    @property
    def id(self) -> str:
        """Stable name for results, card filenames and league rows."""
        return f"{self.board.id}@{self.rung.id}+{self.loadout.id}"


def _seats(rung: Rung) -> list[str]:
    return ["p1"] + [f"p{i}" for i in range(2, rung.opponents + 2)]


def _blank_ore(wb: WorldBuilder) -> None:
    """Scrub the noise generator's RED so the board is only what it declares.

    A battle board is an argument — "one pure, watched, four hours left"
    — and the argument is void if the generator scattered three more
    pures across the map. It did: before this pass, ``early_solo_seam``
    shipped one declared pure and three undeclared ones, so an agent
    could be marked down for taking a real pure the board denied having.
    Undeclared mass and vein are the same problem one rung quieter: they
    move the value calculus the board's prose reasons about.

    Only RED. GREEN and BLUE stay, because neither changes what these
    scenarios ask. Natural green is texture the agent must learn to
    ignore (and the thing synthetic green is scored against), and blue
    is currency the boards set explicitly with ``give_blue_purity``.
    """
    from sea_of_colours.game.session import Cell, Tile

    sess = wb._ensure_session()
    for row in sess.grid:
        for x, cell in enumerate(row):
            if cell.tile == Tile.RED:
                row[x] = Cell(tile=Tile.EMPTY, purity=0)


def _clamp(x: int, y: int, w: int, h: int) -> tuple[int, int]:
    return max(0, min(w - 1, x)), max(0, min(h - 1, y))


def _ring_cells(
    origin: tuple[int, int], count: int, w: int, h: int,
    avoid: set[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Spread ``count`` positions around ``origin``, skipping ``avoid``."""
    out: list[tuple[int, int]] = []
    for dx, dy in _RING:
        if len(out) >= count:
            break
        cell = _clamp(origin[0] + dx, origin[1] + dy, w, h)
        if cell in avoid or cell in out:
            continue
        out.append(cell)
    return out


def stage(
    board: Board,
    rung: Rung,
    loadout: Loadout,
    *,
    seed: int = 4242,
    width: int = 40,
    height: int = 28,
) -> StagedBattle:
    """Construct the night. Returns a session ready for one planning turn."""
    seats = _seats(rung)
    wb = WorldBuilder(
        seed=seed,
        width=width,
        height=height,
        day=board.day,
        season_day_cap=board.season_day_cap,
        season_name=f"battle::{board.id}::{rung.id}",
        players=seats,
    )

    # 1. terrain -----------------------------------------------------
    _blank_ore(wb)
    red_cells: list[tuple[int, int, int]] = [
        (x, y, PURE) for x, y in board.pures
    ]
    red_cells += [(x, y, p) for x, y, p in board.red]
    wb.reveal_red(red_cells)

    # 2. ground a rival already stripped -----------------------------
    if board.green:
        wb.reveal_green_synthetic(list(board.green), owner="p2")

    # 3. the beacon --------------------------------------------------
    if board.redsign_center is not None:
        # Intensity falls off from the centre, which is what makes a
        # redsign an approximation rather than a coordinate — the sign
        # tells every seat roughly where, and only the finder knows
        # exactly. Scoring a blind comb depends on that gradient being
        # real, so it is computed from distance rather than flattened.
        cx, cy = board.redsign_center
        smear: list[tuple[int, int, float]] = []
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                x, y = _clamp(cx + dx, cy + dy, width, height)
                d = max(abs(dx), abs(dy))
                smear.append((x, y, round(1.0 - 0.28 * d, 3)))
        wb.with_redsign(
            center=board.redsign_center,
            cells=smear,
            minted_on_day=max(1, board.day - 1),
            minted_by=("p1" if board.redsign_owner == "ours" else "p2"),
        )

    # 4. our fleet ---------------------------------------------------
    # Reconciled, not added to: every seat is spawned with a harvester
    # already, so appending the board's count would silently hand each
    # side one more unit than the analysis was written against — and
    # "how many harvesters do you have" is most of what these boards
    # are about.
    _set_fleet(wb, "p1", board.harvesters)
    for cell in board.our_probes:
        wb.place_probe(at=cell, owner="p1")

    # Probe STOCK is what the agent may spend tonight; the placed probes
    # above are last night's, already on the ground.
    _set_probe_stock(wb, "p1", board.probes_in_stock)

    # 5. the opposition ----------------------------------------------
    occupied = {tuple(c) for c in board.our_probes}
    occupied |= {tuple(c) for c in board.rival_probes}

    # A rival probe must be PLACED and also RECORDED as a public launch.
    # `place_probe` only sets the entity position, which the opponent's own
    # seat can see but ours cannot: our view learns about rival probes solely
    # through the public channels `_competitor_intel` derives (§3.15 — every
    # launch is observable). Without the second call, `board.rival_probes` sat
    # on the board completely invisible to us, so every probe-targeting play
    # resolved zero eyes and refused on a board whose whole premise was being
    # watched. Same shape of bug as the `siege` rack above.
    for cell in board.rival_probes:
        wb.place_probe(at=cell, owner="p2")
        wb.enemy_probe_launched(at=cell)

    # Opposition fleets are reconciled the same way, then the captured
    # positions are applied on top so a rival that the analysis put on
    # the board is standing where it was.
    for seat in seats[1:]:
        _set_fleet(wb, seat, 2)
    for i, cell in enumerate(board.rival_harvesters):
        wb.place_harvester(
            f"harvester_p2_{i + 1}", at=cell, state="surface", owner="p2"
        )

    anchor = board.redsign_center or (board.pures[0] if board.pures else (20, 14))

    if rung.extra_rival_probes:
        extra = _ring_cells(anchor, rung.extra_rival_probes, width, height, occupied)
        for i, cell in enumerate(extra):
            owner = seats[1 + (i % max(1, len(seats) - 1))]
            wb.place_probe(at=cell, owner=owner)
            wb.enemy_probe_launched(at=cell)     # public, as above
            occupied.add(cell)

    if rung.rival_harvesters_on_seam:
        near = _ring_cells(anchor, rung.rival_harvesters_on_seam + 2,
                           width, height, occupied)
        for i in range(min(rung.rival_harvesters_on_seam, len(near))):
            owner = seats[1 + (i % max(1, len(seats) - 1))]
            wb.place_harvester(
                f"harvester_{owner}_seam{i + 1}",
                at=near[i], state="surface", owner=owner,
            )
            occupied.add(near[i])

    # Whether they can actually LAND on the jackpot this hour, which is
    # the difference between a race and a walkover.
    if rung.rival_sees_the_pure and board.pures:
        wb.grant_live_vision(list(board.pures), player="p2")

    # 6. vision on our own seam --------------------------------------
    # A fogged pure is an ECHO: we know the coordinate from a previous
    # night but cannot drop on it until something lights it. That costs
    # a probe and an hour, and several canonicals turn on exactly that.
    if board.pures and not board.pure_is_fogged:
        wb.grant_live_vision(list(board.pures), player="p1")

    # 7. ordnance ----------------------------------------------------
    #
    # The loadout is the axis that arms US, so it wins where it says
    # anything. But a rung may ALSO declare our stock — ``siege`` does,
    # and its own summary line ends "and you are armed too". That was
    # being silently dropped: only the loadout was read, so `siege` ran
    # with an empty rack and the rung's promise was false. Take the
    # larger of the two, which keeps the loadout authoritative while
    # letting a rung establish a floor.
    wb.give_weapon_stock(
        player="p1",
        emp=max(loadout.emp, rung.our_emp),
        chaff=max(loadout.chaff, rung.our_chaff),
        # No rung grants snap, so there is no floor to take the max against —
        # the loadout is the only source. Omitting this left every snap agent
        # with an empty rack on every board.
        snap=loadout.snap,
    )
    blue = loadout.blue or rung.our_blue
    if blue:
        wb.give_blue_purity(player="p1", purity_total=blue)

    for seat in seats[1:]:
        wb.give_weapon_stock(player=seat, emp=rung.rival_emp, chaff=rung.rival_chaff)
        if rung.rival_blue:
            wb.give_blue_purity(player=seat, purity_total=rung.rival_blue)

    store, sid = wb.build()
    return StagedBattle(
        board=board, rung=rung, loadout=loadout,
        store=store, session_id=sid, seat="p1",
    )


def _set_fleet(wb: WorldBuilder, player: str, count: int) -> None:
    """Make ``player`` own exactly ``count`` harvesters, parked in orbit.

    Surplus units are deleted rather than parked, because a harvester
    sitting in orbit is still a unit the agent can deploy — and "one
    surviving harvester" is the entire premise of two of these boards.
    """
    sess = wb._ensure_session()
    mine = sorted(
        (uid for uid, e in sess.entities.items()
         if e.entity_type == "harvester" and e.owner == player)
    )
    for uid in mine[count:]:
        sess.entities.pop(uid, None)
    for i in range(len(mine), count):
        wb.place_harvester(f"harvester_{player}_{i + 1}", state="orbit",
                           owner=player)
    for uid in mine[:count]:
        ent = sess.entities[uid]
        ent.x = None
        ent.y = None
        ent.cargo_squares = []
        ent.carrying_red = False
        ent.damaged = False


def _set_probe_stock(wb: WorldBuilder, player: str, count: int) -> None:
    """Set how many probes the seat may deploy tonight.

    Reached through the session rather than a builder mutator because
    there isn't one — and adding a public mutator for a single internal
    field felt like a worse trade than one documented reach-in. If
    ``WorldBuilder`` grows ``give_probe_stock`` later, delete this.
    """
    sess = wb._ensure_session()
    fleet = getattr(sess, "probe_stock", None)
    if isinstance(fleet, dict):
        fleet[player] = int(count)
