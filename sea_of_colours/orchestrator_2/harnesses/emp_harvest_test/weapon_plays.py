"""What this seat fires, and what it deliberately leaves alone.

EMP IS NOT DECLARED HERE, AND THAT IS THE POINT.

This fork already has an EMP play it won games with: ``BLIND_SCORCH`` shapes
a salvo so it leaves one cell of the rival smear untouched, probes the rim,
drops a harvester into the hole at hour three and defers the comb to hour
nine when the cloud lifts (``agency._blind_scorch_option`` ->
``packager._pack_emp``). It is four beats deep and it understands its own
friendly fire.

``weapon_forge.packers()`` returns one packer per DECLARED weapon and the
install hook does ``_DISPATCH.update(...)``. Declare an EMP play here and
that dict update silently replaces ``_pack_emp`` with the forge's generic
``_pack_weapon``, which fires a plain salvo and takes no ground. The rack
would still empty, the log would still say a salvo flew, and the four-beat
play that actually banked the red would be gone. So: snap and chaff are
declared, EMP is left to the code that already does it better.

The consequence is the ECONOMY below has to be written defensively — see
the note on ``never_buy_what_you_cannot_fire``.
"""

from __future__ import annotations

from typing import Tuple

from .weapon_forge import EconomyPolicy, WeaponPlay

# ``never_buy_what_you_cannot_fire`` zeroes the stockpile cap of any weapon
# with no declared play (``weapon_forge.tune_dials``). Left at its default
# that is exactly right, and here it is exactly wrong: EMP has no play in
# this file yet is this seat's best weapon, so the default would set
# ``emp_stockpile_cap = 0`` and quietly disarm the thing we came for.
#
# ``hold_at`` then names all three caps explicitly rather than relying on
# the fork's own dials, because the three of them have to be read as one
# budget: game/weapons.py prices EMP at 200 blue, chaff at 300 and snap at
# 100, and WEAPONISED_BLUE_CAP is 600. One of each is 600 exactly. There is
# no room for a second charge of anything, so every cap here is 1 — the old
# ``emp_stockpile_cap = 2`` was 400 blue of intent against a ceiling that
# could not pay for it.
#
# (``snap_stockpile_cap`` does not exist on this fork's OrbitDials, so
# tune_dials skips it via hasattr and the snap is bought by
# ``weapon_forge.add_procurement`` instead. That is the hook's whole job.)
ECONOMY = EconomyPolicy(
    buy_asap=True,
    never_buy_what_you_cannot_fire=False,
    hold_at={"emp": 1, "chaff": 1, "snap": 1},
    seek_blue_always=True,
)


PLAYS: Tuple[WeaponPlay, ...] = (
    # ── snap ──────────────────────────────────────────────────────────
    # Both snap plays are DENIAL ONLY (``take_the_ground`` left False). A
    # snap owns its cell for hour one, so the seat physically cannot land
    # there until hour two; carrying a harvester here would double-book the
    # cell against the grab that actually banks it.
    WeaponPlay(
        play_id="PURE_TRAP",
        weapon="snap",
        when="always",             # the target resolver is the real gate
        hour="super_early",
        targets="contested_pure",
        min_targets=1,
        combines_with="smash_grab",
        why=(
            "a pure we can see that a rival probe also watches is the one cell "
            "on the board whose occupation is predictable — they will smash-and-"
            "grab it at hour one; snapping it refuses that landing and damages "
            "the hull, then a smash-grab lands on the cold pure at hour two and "
            "banks it — pick ONE grab on that cell, not two"
        ),
    ),
    WeaponPlay(
        play_id="BLIND_THE_FINDER",
        weapon="snap",
        when="redsign_theirs",
        hour="super_early",
        targets="finder_probe",
        min_targets=1,
        combines_with="blind_grab",
        why=(
            "when a rival has lit a pure we cannot see, the eye that found it is "
            "the only target we can name; snapping it at hour one refuses their "
            "drop for lack of live vision, then a paired blind-grab combs the "
            "smear they can no longer reach"
        ),
    ),
    # ── chaff ─────────────────────────────────────────────────────────
    # This fork has been BUYING chaff since it was written and has never had
    # a way to fire it: `chaff_flare` appears nowhere but the last-night log
    # reader. Every purchase was 300 blue — half the arsenal cap — spent on a
    # charge with no exit from the rack. This play is that exit.
    WeaponPlay(
        play_id="CANCEL_DROP",
        weapon="chaff",
        when="redsign_theirs",
        hour="super_early",
        combines_with="blind_grab",
        why=(
            "a rival that has just lit a pure will drop on it at hour one; "
            "cancelling that hour kills their smash-and-grab and leaves the "
            "pure sitting there for our follow-on grab"
        ),
    ),
)
