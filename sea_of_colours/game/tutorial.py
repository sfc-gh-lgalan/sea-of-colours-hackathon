"""Teaching-mode presets (v1.32).

One place decides what "Basic" means. The landing page posts a preset
NAME, not a bag of overrides, so the client cannot drift from the engine
— and a test, a film harness and the browser all spawn byte-identical
tutorials.

Presets are expressed as OVERRIDES on the ordinary new-game defaults
rather than as complete configurations. A field added to the New Game
modal later therefore reaches the tutorial paths automatically instead of
silently defaulting; the alternative (three independent literals) is a
drift bug that would surface months later as "the tutorial ignores X".

Note what is NOT here: nothing in this module is consulted at rule-check
time. A preset only chooses the values of ``GameSession.weapons_enabled``
and ``signs_enabled`` when the game is minted. The engine then asks the
session, never the preset name, so a future preset cannot quietly change
what is legal mid-season.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

#: Three nights. Long enough to see a full loop (orbit → plan → night →
#: orbit), short enough that a first-timer reaches a score card rather
#: than abandoning a half-played season.
TUTORIAL_DAY_CAP = 3

#: ...except Advanced, which runs FOUR (v1.36).
#:
#: Not a change of heart about length — a change in what has to fit. The
#: weapon set went from two to three when SNAP arrived, and Advanced is
#: the mode that teaches weapons: each one needs an orbit to be bought in
#: and a night to be fired on, and three nights hold two weapons.
#:
#: Cramming it was tried on paper and is worse than lengthening. Night
#: two was already the heaviest turn in either tutorial at four chapters
#: (redsign, rival redsign, smash-and-grab, blind-grab), and the fourth
#: night is what lets that split in half. It also repairs an existing
#: mismatch: the ``adv_chaff`` film is *shot* on night four, because the
#: rival needs a night to land, work and be caught mid-lift — but on a
#: three-night cap the reel had to play on night three, so the player was
#: shown a sequence their own game could no longer run.
#:
#: Basic and Quick are untouched. Basic teaches no weapons, so it has
#: nothing to fit.
ADVANCED_TUTORIAL_DAY_CAP = 4

#: The teaching board. Smaller than the 40x28 default so a probe's
#: radius-4 disk is a visible fraction of the map rather than a dot, and
#: so the fog clears fast enough to feel like progress.
TUTORIAL_WIDTH = 24
TUTORIAL_HEIGHT = 16

#: The opponent for every preset. The heuristic runs in-process, so a
#: tutorial needs no Snowflake credentials and no network — the attendee
#: can play before they have configured anything.
TUTORIAL_OPPONENT = "red_harvest_lite"

#: Advanced runs on ONE fixed board, and Basic does not.
#:
#: Basic's lessons are about the machine — probe, drop, walk, lift — and
#: they land on any terrain, so a fresh map each time costs nothing.
#: Advanced's lessons are about SIGNS and WEAPONS, and those need the
#: board to co-operate: a bright blue smear to hot-drop into, and two
#: pure seams far enough apart that each House can light one. The
#: generator supplies that combination only sometimes, and an Advanced
#: game that happens not to have it teaches the player that the mode is
#: broken.
#:
#: Pinning it buys a second thing worth more than the first: the
#: Advanced films are shot on this seed, so the blue smear in the video
#: is the blue smear on the player's own map. Chosen by
#: ``backstage/films/_probe_advseed.py`` — see the header of
#: ``backstage/films/make_tutorial_films.py`` for what it was chosen against.
ADVANCED_TUTORIAL_SEED = 2351


TUTORIAL_PRESETS: Dict[str, Dict[str, Any]] = {
    # Weapons and signs both off. Basic teaches the core loop only:
    # probe to see, drop to harvest, lift to bank. Signs are off TOGETHER
    # with weapons rather than separately — a board with signature
    # intelligence but no way to act on it teaches a game that does not
    # exist.
    "basic": {
        "width": TUTORIAL_WIDTH,
        "height": TUTORIAL_HEIGHT,
        "season_day_cap": TUTORIAL_DAY_CAP,
        "weapons_enabled": False,
        "signs_enabled": False,
        "opponent": TUTORIAL_OPPONENT,
    },
    # Same board size, everything switched on. The point of the second
    # run is what signs and weapons ADD to terrain you have already
    # learned to read.
    "advanced": {
        "width": TUTORIAL_WIDTH,
        "height": TUTORIAL_HEIGHT,
        "season_day_cap": ADVANCED_TUTORIAL_DAY_CAP,
        "seed": ADVANCED_TUTORIAL_SEED,
        "weapons_enabled": True,
        "signs_enabled": True,
        "opponent": TUTORIAL_OPPONENT,
    },
    # Not a tutorial: a full-size, full-rules season that is merely
    # short. It lives here because it is the third card on the same
    # chooser and shares the "short, heuristic opponent" shape. It keeps
    # the three-night cap: Advanced grew a fourth night to fit a weapon
    # lesson, and Quick teaches nothing, so it inherits nothing.
    "quick": {
        "season_day_cap": TUTORIAL_DAY_CAP,
        "weapons_enabled": True,
        "signs_enabled": True,
        "opponent": TUTORIAL_OPPONENT,
    },
}

#: ``quick`` is a normal game that happens to be short, so it must not
#: put the player in a teaching UI. Only these presets set
#: ``GameSession.tutorial`` and therefore only these show the film modal.
TEACHING_PRESETS = ("basic", "advanced")


#: Blue handed to the human seat at a teaching game's orbit open (v1.34,
#: extended to a second grant in v1.36).
#:
#: Advanced asks the player to buy one weapon per orbit: an EMP at orbit
#: 2, a SNAP at orbit 3, a chaff at orbit 4. Only the first is affordable
#: unaided — 200 out of the 250 opening bank — and one night on a 24x16
#: teaching board will not reliably mine the 400 the other two want. A
#: lesson the player cannot complete teaches them the button is broken.
#:
#: So each of the two later orbits is credited **exactly the weapon it
#: asks for**, on the morning it asks. What the player still has to
#: supply is the credits and the judgement; what they are not asked to
#: supply is a mining result the board may not offer.
#:
#: Two properties worth keeping if you edit this:
#:
#: * **Priced off the weapon, never written as a literal.** The amount IS
#:   "one chaff" / "one SNAP". Chaff moved 255 → 300 at v1.36 and this
#:   needed no edit, which is the whole point of the indirection.
#: * **The grant lands on the turn that spends it**, not earlier. Paying
#:   day 3's SNAP money on day 2 would leave the player carrying blue
#:   through a night with nothing the tutorial wants them to do with it,
#:   reading a bank they had not earned.
#:
#: Paid into ``blue_bank``, not as a parcel. That matters more than it
#: used to: a parcel's purity is clamped at 255 (§3.14), so from v1.36 a
#: chaff's worth of blue will no longer FIT in one parcel.
#:
#: Only teaching presets appear here, and the grant is announced in the
#: log rather than slipped in — a tutorial that silently edits your
#: balance is teaching an economy that does not exist.
TUTORIAL_BLUE_GRANT_WEAPON: Dict[str, Dict[int, str]] = {
    "advanced": {3: "snap", 4: "chaff"},
}


def blue_grant_weapon_for(tutorial: Any, day: Any) -> str:
    """Which weapon ``day``'s subsidy is the price of, or ``""``.

    Separate from the amount so the log line can name what the money is
    for. "+100 BLUE" on its own reads like a bug; "+100 BLUE — enough
    for one SNAP" reads like a lesson.
    """
    key = normalise_preset(tutorial)
    if not key:
        return ""
    return TUTORIAL_BLUE_GRANT_WEAPON.get(key, {}).get(int(day or 0), "")


def blue_grant_for(tutorial: Any, day: Any) -> int:
    """Blue owed to each seat at ``day``'s orbit open, or ``0``."""
    from sea_of_colours.game.weapons import BLUE_COST_BY_KIND

    kind = blue_grant_weapon_for(tutorial, day)
    # A weapon that has been withdrawn owes no stipend. Returning 0
    # rather than raising means retiring SNAP does not also break the
    # tutorial on the way out — the reel goes, and this goes quiet.
    return int(BLUE_COST_BY_KIND.get(kind, 0)) if kind else 0


def normalise_preset(name: Any) -> str:
    """Return a known preset name, or ``""`` for anything else."""
    key = str(name or "").strip().lower().replace("tut-", "")
    return key if key in TUTORIAL_PRESETS else ""


def preset_config(name: Any) -> Optional[Mapping[str, Any]]:
    """Overrides for ``name``, or ``None`` when it names no preset."""
    key = normalise_preset(name)
    return TUTORIAL_PRESETS.get(key) if key else None


def is_teaching(name: Any) -> bool:
    """Does this preset put the player in the tutorial UI?"""
    return normalise_preset(name) in TEACHING_PRESETS
