"""Teaching mode: weapons and signs can be switched off (v1.32).

The browser harness (``backstage/films/_fx_tutorial.py``) covers what a player
SEES. This file covers what the engine ALLOWS, which is the half that
matters if the two ever disagree: a hidden button the engine would have
honoured is a cosmetic bug, but a visible-in-the-rules weapon the
tutorial claims does not exist is a lie the player later has to unlearn.

The other job here is the default. Every assertion about a flag being
off is paired with the same call on an ordinary session, because the
failure mode nobody would notice is teaching mode leaking into a real
season.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from sea_of_colours.game import tutorial as tut
from sea_of_colours.game.session import GameSession


def _sess(**kw) -> GameSession:
    return GameSession.new(20, 14, seed=4242, season_day_cap=3, **kw)


# ── the flags default to the full game ──────────────────────────────────

def test_an_ordinary_session_has_everything_on():
    """The flags are subtractive. Absent means 'the game as written'."""
    s = _sess()
    assert s.weapons_enabled is True
    assert s.signs_enabled is True
    assert s.tutorial == ""


def test_the_flags_survive_a_save_and_load():
    """A tutorial that forgets it is a tutorial after one round-trip is
    worse than no tutorial: the weapons come back mid-season."""
    s = _sess(weapons_enabled=False, signs_enabled=False, tutorial="basic")
    back = GameSession.from_dict(s.to_dict())
    assert back.weapons_enabled is False
    assert back.signs_enabled is False
    assert back.tutorial == "basic"


def test_a_pre_v132_save_loads_with_the_full_game():
    """Sessions written before these keys existed must not load as
    teaching games."""
    raw = _sess().to_dict()
    for key in ("weapons_enabled", "signs_enabled", "tutorial"):
        raw.pop(key, None)
    back = GameSession.from_dict(raw)
    assert back.weapons_enabled is True
    assert back.signs_enabled is True
    assert back.tutorial == ""


# ── weapons_enabled=False refuses, and says why ─────────────────────────

def _buy(s: GameSession, kind: str):
    return s._apply_build_weapon(
        "p1", kind=kind, count=1, blue_cost_each=200, credit_cost_each=250,
        display=kind.upper(),
    )


@pytest.mark.parametrize("kind", ["emp", "chaff"])
def test_weapons_cannot_be_bought_with_weapons_off(kind: str):
    ok, msg = _buy(_sess(weapons_enabled=False), kind)
    assert ok is False
    # Refused by NAME, the v1.13 retirement pattern — a bare False gives
    # an agent nothing to learn from.
    assert "weapons are disabled" in msg.lower()


@pytest.mark.parametrize("kind", ["emp", "chaff"])
def test_a_normal_game_never_refuses_for_that_reason(kind: str):
    """Asserted as negative space on purpose.

    A fresh seat cannot afford an EMP, so demanding success here would
    only be testing the starting wallet. What must hold is that whatever
    the engine says no to, it is not saying 'teaching mode'.
    """
    ok, msg = _buy(_sess(), kind)
    assert ok or "weapons are disabled" not in msg.lower(), msg


def test_emp_launch_is_refused_with_weapons_off():
    s = _sess(weapons_enabled=False)
    s.weapon_stock.setdefault("p1", {})["emp"] = 5
    ok, msg = s.apply_emp_launch("p1", 5, 5, hour=1)
    assert ok is False
    assert "weapons are disabled" in msg.lower()


def test_chaff_is_refused_with_weapons_off():
    s = _sess(weapons_enabled=False)
    s.weapon_stock.setdefault("p1", {})["chaff"] = 5
    ok, msg = s.apply_chaff_flare("p1", hour=1)
    assert ok is False
    assert "weapons are disabled" in msg.lower()


def test_stock_alone_does_not_re_enable_a_launch():
    """Belt and braces: the guard is on the ACTION, not on the wallet.

    A migrated save, a fixture or a future refund could put ammunition
    in a teaching session's stock; that must still not be firable.
    """
    s = _sess(weapons_enabled=False)
    s.weapon_stock.setdefault("p1", {}).update({"emp": 9, "chaff": 9})
    assert s.apply_emp_launch("p1", 4, 4, hour=1)[0] is False
    assert s.apply_chaff_flare("p1", hour=1)[0] is False
    assert s.weapon_stock["p1"]["emp"] == 9, "a refused launch must not bill"


# ── signs_enabled=False removes the layer, not just its contents ────────

def test_no_blue_sign_is_computed_with_signs_off():
    s = _sess(signs_enabled=False)
    assert s._compute_blue_sign() == []


def _a_pure_cell(s: GameSession):
    from sea_of_colours.game.session import Tile

    for y, row in enumerate(s.grid):
        for x, c in enumerate(row):
            if c.tile == Tile.RED and c.purity >= 255:
                return x, y
    pytest.skip("no pure-RED cell on this board — nothing to discover")


def test_no_redsign_is_registered_with_signs_off():
    s = _sess(signs_enabled=False)
    x, y = _a_pure_cell(s)
    s._register_redsign({(x, y): "p1"})
    assert s.redsign == []


def test_signs_off_does_not_SPEND_the_discovery():
    """The subtle half, and the reason the guard sits above the
    bookkeeping rather than below it.

    Marking the seam seen and then returning early would look identical
    today and be a real bug tomorrow: a save made in teaching mode, or a
    preset flipped mid-development, would come back with its jackpots
    already 'discovered' and no beacon would ever mint for them.
    """
    s = _sess(signs_enabled=False)
    x, y = _a_pure_cell(s)
    s._register_redsign({(x, y): "p1"})
    assert not s.redsign_seen


def test_the_same_discovery_does_mint_in_a_normal_game():
    """Pins the test above to the flag rather than to an inert board."""
    s = _sess()
    x, y = _a_pure_cell(s)
    s._register_redsign({(x, y): "p1"})
    assert s.redsign, "signs on but no beacon minted for a pure seam"
    assert s.redsign_seen


# ── the presets ─────────────────────────────────────────────────────────

def test_basic_is_the_reduced_ruleset():
    cfg = tut.preset_config("basic")
    assert cfg["weapons_enabled"] is False
    assert cfg["signs_enabled"] is False
    assert cfg["season_day_cap"] == 3
    assert (cfg["width"], cfg["height"]) == (24, 16)


def test_advanced_is_basic_sized_with_the_full_rules():
    """The second run's whole point is the same board, more game."""
    basic, adv = tut.preset_config("basic"), tut.preset_config("advanced")
    assert (adv["width"], adv["height"]) == (basic["width"], basic["height"])
    assert adv["weapons_enabled"] is True
    assert adv["signs_enabled"] is True


def test_advanced_is_the_one_preset_that_runs_a_fourth_night():
    """v1.36 — the weapon set outgrew three nights, and only Advanced
    teaches weapons.

    Pinned because the fourth night is load-bearing rather than
    cosmetic: ``adv_chaff`` is SHOT on night four (the rival needs a
    night to land, work and be caught mid-lift), and on a three-night
    cap the reel had to play on a night the player's own game could no
    longer produce. Shrinking this back re-breaks that quietly.
    """
    assert tut.preset_config("advanced")["season_day_cap"] == 4
    for other in ("basic", "quick"):
        assert tut.preset_config(other)["season_day_cap"] == 3, (
            f"{other} inherited Advanced's extra night — it teaches no "
            "weapons and has nothing to fit"
        )


def test_each_weapon_lesson_is_funded_on_the_turn_it_is_taught():
    """The subsidy is priced off the weapon, and lands the day it is spent.

    Both halves matter. Priced off the weapon, so a retune moves the
    gift instead of quietly making it the wrong size — chaff went
    255 → 300 at v1.36 and this needed no edit. Landing on the day,
    because paying early leaves the player carrying blue through a night
    with nothing the tutorial wants them to do with it.
    """
    from sea_of_colours.game.weapons import BLUE_COST_BY_KIND

    assert tut.blue_grant_for("advanced", 3) == BLUE_COST_BY_KIND["snap"]
    assert tut.blue_grant_for("advanced", 4) == BLUE_COST_BY_KIND["chaff"]
    # Day 2 buys the EMP out of the opening bank, unaided. That is the
    # lesson of day 2 and a grant would remove it.
    for quiet in (1, 2, 5):
        assert tut.blue_grant_for("advanced", quiet) == 0
    # No other preset is ever handed anything.
    for other in ("basic", "quick", "", "nonsense"):
        for day in range(1, 6):
            assert tut.blue_grant_for(other, day) == 0


def test_the_two_stipends_actually_land_and_neither_pays_twice():
    """The banking half, which the pricing tests above cannot see.

    v1.36 turned one grant into two, and the idempotence gate is "the
    last day paid" rather than "has been paid" — precisely so a second
    grant is possible. This pins both halves of that: day 4 is not
    swallowed by day 3 having happened, and neither day can be milked
    by calling the hook again (a reload does exactly that).
    """
    from sea_of_colours.game.weapons import BLUE_COST_BY_KIND

    s = GameSession.new(20, 14, seed=4242, season_day_cap=4,
                        tutorial="advanced")
    s.agents = {p: "human" for p in s.players}
    seat = s.players[0]
    opening = int(s.blue_bank.get(seat, 0))

    s.day = 2
    s.award_tutorial_blue_topup()
    assert s.blue_bank[seat] == opening, "day 2 buys the EMP unaided"

    s.day = 3
    s.award_tutorial_blue_topup()
    s.award_tutorial_blue_topup()  # a reload must not re-gift
    assert s.blue_bank[seat] == opening + BLUE_COST_BY_KIND["snap"]

    s.day = 4
    s.award_tutorial_blue_topup()
    s.award_tutorial_blue_topup()
    assert s.blue_bank[seat] == (
        opening + BLUE_COST_BY_KIND["snap"] + BLUE_COST_BY_KIND["chaff"]
    )


def test_the_stipend_never_reaches_the_seat_flying_the_bot():
    """A subsidised heuristic buys a weapon with it, and the Advanced
    films — shot on this preset and seed — stop matching the game."""
    s = GameSession.new(20, 14, seed=4242, season_day_cap=4,
                        tutorial="advanced")
    bot = s.players[1]
    s.agents = {s.players[0]: "human", bot: "red_harvest_lite"}
    before = int(s.blue_bank.get(bot, 0))
    s.day = 3
    s.award_tutorial_blue_topup()
    assert s.blue_bank[bot] == before


def test_a_withdrawn_weapon_stops_paying_a_stipend_instead_of_exploding():
    """Retiring SNAP must not also take the tutorial down with it.

    The grant names a weapon rather than a number, so the failure mode
    worth guarding is the lookup, not the arithmetic: an unpriced kind
    owes nothing and says so, the way the caltrop's slot does.
    """
    import sea_of_colours.game.weapons as W

    original = dict(W.BLUE_COST_BY_KIND)
    try:
        W.BLUE_COST_BY_KIND.pop("snap", None)
        assert tut.blue_grant_for("advanced", 3) == 0
        # And the day that still has a live weapon keeps paying.
        assert tut.blue_grant_for("advanced", 4) == original["chaff"]
    finally:
        W.BLUE_COST_BY_KIND.clear()
        W.BLUE_COST_BY_KIND.update(original)


def test_quick_does_not_shrink_the_board():
    """Quick is a short real season, not a tutorial — it must not
    inherit the teaching board."""
    cfg = tut.preset_config("quick")
    assert "width" not in cfg and "height" not in cfg
    assert cfg["season_day_cap"] == 3


def test_quick_is_not_a_teaching_game():
    assert tut.is_teaching("quick") is False
    assert tut.is_teaching("basic") is True
    assert tut.is_teaching("advanced") is True


@pytest.mark.parametrize("junk", ["", None, "BASIC-ish", "expert", 7])
def test_an_unknown_preset_resolves_to_nothing(junk):
    """The name arrives from the client, so it is untrusted input; an
    unrecognised one must fall through to an ordinary game rather than
    half-configuring one."""
    assert tut.normalise_preset(junk) == ""
    assert tut.preset_config(junk) is None


def test_preset_names_are_case_and_prefix_forgiving():
    assert tut.normalise_preset("  BASIC ") == "basic"
    assert tut.normalise_preset("tut-advanced") == "advanced"


def test_only_advanced_pins_its_board():
    """Basic teaches the machine and lands on any terrain. Advanced
    teaches signs and weapons, which need the board to co-operate — so
    it is the one preset that names a seed."""
    assert tut.preset_config("advanced")["seed"] == tut.ADVANCED_TUTORIAL_SEED
    assert "seed" not in tut.preset_config("basic")
    assert "seed" not in tut.preset_config("quick")


def test_the_advanced_board_can_teach_what_advanced_teaches():
    """The guard on the pinned seed.

    Every Advanced reel asserts something about this specific map: a
    blue smear bright enough to aim a hot drop at, and two pure seams
    far enough apart to be one each. None of that is guaranteed by the
    generator — it was searched for. So a retune of the generator, or a
    fat-fingered seed, must fail HERE rather than as a tutorial that
    describes terrain the player cannot find.
    """
    cfg = tut.preset_config("advanced")
    s = GameSession.new(
        cfg["width"], cfg["height"], seed=cfg["seed"],
        season_day_cap=cfg["season_day_cap"],
        weapons_enabled=True, signs_enabled=True, tutorial="advanced",
    )

    pures = [
        (x, y)
        for y in range(s.height) for x in range(s.width)
        if getattr(s.grid[y][x].tile, "name", "") == "RED"
        and int(getattr(s.grid[y][x], "purity", 0) or 0) == 255
    ]
    assert len(pures) >= 2, f"advanced board has {len(pures)} pure seam(s)"
    apart = max(
        ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
        for a in pures for b in pures
    )
    assert apart >= 10, (
        f"the two jackpots are only {apart:.1f} apart — 'one each' is not "
        "a thing you can say about this board"
    )

    bright = [
        (int(c[0]), int(c[1]))
        for region in (s.blue_sign or [])
        for c in (region.get("cells") or [])
        if float(c[2]) >= 0.75
    ]
    assert bright, "no bright blue sign — the hot-drop film aims at nothing"
    # Inset, or a radius-4 probe disk and a close-up both run off the edge.
    assert any(
        3 <= x < s.width - 3 and 2 <= y < s.height - 2 for x, y in bright
    ), f"every bright sign cell is jammed against the edge: {bright}"


def test_every_preset_names_an_in_process_opponent():
    """No teaching game may need credentials. The heuristic runs in
    process; anything else would make the tutorial the first thing to
    break on a laptop with no Snowflake setup."""
    for name, cfg in tut.TUTORIAL_PRESETS.items():
        assert cfg["opponent"] == "red_harvest_lite", name


# ── the reels and the shot films must agree ─────────────────────────────

_REPO = pathlib.Path(__file__).resolve().parents[1]
_REEL_FILM = re.compile(r'film:\s*"([A-Za-z0-9_]+\.webm)"')


def _referenced_films() -> set:
    src = (_REPO / "server" / "static" / "tutorial.js").read_text("utf-8")
    return set(_REEL_FILM.findall(src))


def _shot_films() -> set:
    return {p.name for p in (_REPO / "server" / "static" / "films").glob("*.webm")}


def test_every_reel_points_at_a_film_that_exists():
    """A reel naming a film nobody shot degrades to prose in silence.

    That is the right runtime behaviour and a terrible way to find out:
    the attendee gets a chapter about collisions with no collision in
    it, and the modal reports nothing. Renaming a film in
    ``backstage/films/make_tutorial_films.py`` without renaming it here is the
    exact way this happens.
    """
    missing = sorted(_referenced_films() - _shot_films())
    assert not missing, (
        f"tutorial.js asks for {missing}, which is not in "
        f"server/static/films/ — those chapters will play nothing"
    )


def test_no_film_is_carried_without_a_reel_using_it():
    """Films are checked-in build output of a few MB each, so an orphan
    is dead weight in every clone — and usually the leftover half of a
    rename that half-happened."""
    orphans = sorted(_shot_films() - _referenced_films())
    assert not orphans, (
        f"{orphans} are committed but no reel plays them; delete them or "
        f"wire them into server/static/tutorial.js"
    )


_REEL_OPEN = re.compile(
    r'"(?P<key>(?:basic|advanced|quick):'
    r'(?P<phase>orbit|planning):(?P<day>\d+))"\s*:\s*\{'
)


def _reels() -> dict:
    """Every reel key mapped to the film names it plays, in order."""
    src = (_REPO / "server" / "static" / "tutorial.js").read_text("utf-8")
    opens = list(_REEL_OPEN.finditer(src))
    out = {}
    for i, m in enumerate(opens):
        end = opens[i + 1].start() if i + 1 < len(opens) else len(src)
        out[m.group("key")] = _REEL_FILM.findall(src[m.end():end])
    return out


def _shoot_turns() -> dict:
    """``_ADV_TURN`` out of the film rig, without importing it.

    Read rather than imported because the rig pulls in Playwright at
    module scope, and this assertion is about two tables agreeing — it
    has no business needing a browser installed to make it.
    """
    src = (_REPO / "backstage" / "films" / "make_tutorial_films.py").read_text("utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_ADV_TURN" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("_ADV_TURN is gone from make_tutorial_films.py")


def _turn_to_slot(turn: int) -> tuple:
    """Which (phase, day) a shoot turn lands on.

    The calendar is: night one, then orbit/night for every day after,
    because the night rolls the date before the orbit desk opens. So
    turn 0 is day 1's night and turns alternate from there.
    """
    if turn % 2:
        return "orbit", (turn + 3) // 2
    return "planning", (turn + 2) // 2


def test_a_film_is_shown_on_the_day_it_was_shot_on():
    """A film carries its own day number, burnt in.

    Every film is a screen recording of a real session, so the game
    chrome inside it says "# day 3 · pick orders" and "NOX 03". Play
    that chapter on night four and the header inside the video argues
    with the header above it, which reads exactly like the tutorial has
    lost track of where the player is.

    Nothing at runtime can catch this — the reel plays whatever file it
    is handed — and nothing in the shoot can either, because each side
    is individually correct. It only shows up as the two tables
    disagreeing, which is what this compares. v1.36 shipped three films
    wrong this way while adding Advanced's fourth night: the reel moved
    and the shoot turn did not.
    """
    turns = _shoot_turns()
    wrong = []
    for key, films in _reels().items():
        if not key.startswith("advanced:"):
            continue
        _, phase, day = key.split(":")
        for f in films:
            turn = turns.get(f[:-len(".webm")])
            if turn is None:
                continue
            got = _turn_to_slot(int(turn))
            if got != (phase, int(day)):
                wrong.append(
                    f"{f} is shot on turn {turn} (= {got[0]} day {got[1]}) "
                    f"but {key} plays it on {phase} day {day}"
                )
    assert not wrong, (
        "reel and shoot turn disagree, so these films show the wrong day "
        "number to the player:\n  " + "\n  ".join(wrong)
    )
