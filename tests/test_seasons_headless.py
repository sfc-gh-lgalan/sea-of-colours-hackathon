"""Headless seasons, and the cards they leave behind.

Two promises are worth pinning here, because both fail silently.

A season is only useful if you can look at it afterwards, and "afterwards"
means a different process — so the test that matters is not "did it run"
but "can the server see it". And a mistyped agent must be an error: a
season that quietly seats the heuristic instead produces a complete,
plausible result that measures the wrong thing, and the tournament path
runs unattended where nobody would catch it.
"""

from __future__ import annotations

import json
import re

import pytest

from sea_of_colours.evals import cards, dispatch, seasons
from sea_of_colours.snowpark import engine as soc_engine


@pytest.fixture
def real_orbit(monkeypatch):
    """Undo the conftest's orbit-skipping for this module.

    ``tests/conftest.py`` force-passes ``skip_initial_orbit=True`` and
    auto-settles the dawn orbit, which keeps the pre-v0.8.0 suite green.
    A season run under that patch never has an orbit phase at all — so
    buying, the whole economic half of the game, silently does not
    happen and every card is a night card. Testing the season loop
    against that would be testing the harness.
    """
    import conftest as ct
    from sea_of_colours.game import simulator as sim
    from sea_of_colours.game import session as sess_mod
    from sea_of_colours.snowpark import engine as eng

    monkeypatch.setattr(eng, "init_session", ct._orig_init)
    monkeypatch.setattr(sim.NightSimulator, "run", ct._orig_night_run)
    monkeypatch.setattr(sess_mod.GameSession, "new", ct._orig_new)
    yield


@pytest.fixture
def file_backend(tmp_path, monkeypatch):
    """A durable store of our own, so a test cannot see real seasons."""
    monkeypatch.setenv("SOC_BACKEND", "file")
    monkeypatch.setenv("SOC_STORE_DIR", str(tmp_path / "store"))
    from sea_of_colours.snowpark import backend as soc_backend

    soc_backend.reset_for_tests()
    yield soc_backend.get_store()
    soc_backend.reset_for_tests()


@pytest.fixture
def played(real_orbit, file_backend, tmp_path):
    """One short heuristic season, played once and reused."""
    result = seasons.run_season(
        {"p1": "heuristic", "p2": "red_harvest_lite"},
        seed=11, width=24, height=16, days=2, season_name="Test_Season",
        store=file_backend,
    )
    return result


# ── the loop ──────────────────────────────────────────────────────


def test_a_season_plays_to_the_end(played):
    assert not played.aborted
    assert played.days_played == 2
    assert played.season_day_cap == 2


def test_both_seats_are_asked_every_day(played):
    """A seat that silently stops planning is the failure to catch."""
    for seat in ("p1", "p2"):
        days = {t.day for t in played.turns_for(seat)}
        assert days == {1, 2}, f"{seat} did not plan every day: {days}"


def test_orbit_and_night_are_separate_turns(played):
    """Buying is half the game; collapsing the phases hides it."""
    phases = {t.phase for t in played.turns}
    assert "orbit" in phases and "planning" in phases


def test_the_season_is_scored(played):
    assert set(played.scores) == {"p1", "p2"}
    assert all(isinstance(v, int) for v in played.scores.values())


def test_a_heuristic_season_never_reports_a_fallback(played):
    """Fallback means "the model was unreachable". A heuristic has none."""
    assert played.fallback_turns == 0


def test_seats_are_named_after_the_agents_holding_them(played, file_backend):
    """The default, and the one a season under review needs.

    A season is read after the fact and often by someone who did not run
    it, so a scoreboard that does not say which agent held which seat is
    a scoreboard you cannot act on.
    """
    view = soc_engine.get_view(file_backend, played.session_id, "p1")
    names = {s: (pr.get("display_name") or "")
             for s, pr in view["player_profiles"].items()}
    # `heuristic` resolves to the RED_HARVEST slug, which is the point —
    # the name follows the agent that actually took the seat, not the
    # alias it was asked for.
    assert "RED_HARVEST" in names["p1"].upper()
    assert "LITE" in names["p2"].upper()


def test_the_winner_is_the_top_score():
    result = seasons.SeasonResult(
        season_name="x", session_id="y", seed=1, backend="memory",
        seats={"p1": "a", "p2": "b"}, scores={"p1": 10, "p2": 40},
    )
    assert result.winner == "p2"
    assert result.winning_agent == "b"


def test_a_tie_has_no_winner():
    """Better than picking one arbitrarily and calling it a league result."""
    result = seasons.SeasonResult(
        season_name="x", session_id="y", seed=1, backend="memory",
        seats={"p1": "a", "p2": "b"}, scores={"p1": 40, "p2": 40},
    )
    assert result.winner == ""


# ── it has to survive the process ─────────────────────────────────


def test_the_server_can_replay_what_was_played_headlessly(played, file_backend):
    """The whole point: a headless season opens in the normal UI."""
    from fastapi.testclient import TestClient
    from server.app import app

    client = TestClient(app)
    listed = client.get("/api/sessions").json()
    rows = listed if isinstance(listed, list) else listed.get("sessions", [])
    assert played.session_id in {r.get("session_id") for r in rows}

    view = client.get(f"/api/game/{played.session_id}/view?player=p1")
    assert view.status_code == 200


def test_every_turn_leaves_an_agent_row(played, file_backend):
    """What the AGENT tab fills from. No rows, no reasoning in replay."""
    rows = file_backend.list_agent_invocations(played.session_id)
    assert len(rows) == len(played.turns)


# ── the cards ─────────────────────────────────────────────────────


def test_a_card_is_written_for_every_turn(played, tmp_path):
    out = seasons.write_record(played, tmp_path / "records")
    written = sorted(p.name for p in (out / "cards").glob("*.md"))
    assert len(written) == len(played.turns)


def test_the_orbit_card_does_not_overwrite_the_night_card(played, tmp_path):
    """Both are named by day and seat; only the phase separates them."""
    out = seasons.write_record(played, tmp_path / "records")
    names = {p.name for p in (out / "cards").glob("*.md")}
    assert "d02_p1_orbit.md" in names
    assert "d02_p1_planning.md" in names


def test_the_record_summarises_the_season(played, tmp_path):
    out = seasons.write_record(played, tmp_path / "records")
    blob = json.loads((out / "season.json").read_text(encoding="utf-8"))
    assert blob["session_id"] == played.session_id
    assert blob["seats"] == {"p1": "heuristic", "p2": "red_harvest_lite"}
    assert len(blob["turns"]) == len(played.turns)


def test_the_download_serves_the_whole_season(played, file_backend):
    from fastapi.testclient import TestClient
    from server.app import app

    client = TestClient(app)
    res = client.get(f"/api/game/{played.session_id}/agent-cards")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/markdown")
    assert "attachment" in res.headers.get("content-disposition", "")
    assert res.text.count("day ") >= len(played.turns)


def test_the_download_can_be_narrowed_to_one_turn(played, file_backend):
    from fastapi.testclient import TestClient
    from server.app import app

    client = TestClient(app)
    whole = client.get(f"/api/game/{played.session_id}/agent-cards")
    one = client.get(
        f"/api/game/{played.session_id}/agent-cards?day=2&player=p1"
    )
    assert one.status_code == 200
    assert len(one.text) < len(whole.text)


def test_a_url_ending_in_md_would_have_been_swallowed():
    """Guards the rename. `/{name:path}.md` matches slashes, so it claims
    every .md URL in the tree — this endpoint was 404ing because of it."""
    from server.app import app

    paths = {str(getattr(r, "path", "")) for r in app.routes}
    assert "/api/game/{game_id}/agent-cards" in paths
    assert "/api/game/{game_id}/cards.md" not in paths


# ── rendering ─────────────────────────────────────────────────────


def test_a_card_renders_what_the_model_was_told_and_said():
    card = cards.normalise(
        {"rationale": "took the pure", "prompt_excerpt": "you are a house",
         "response_text": "the pure is worth it", "ms_elapsed": 900},
        envelope={"extras": {"option_menu_block": "A) drop on pure",
                             "selected_option_ids": ["A"]}},
        season="S", day=3, player="p1", agent="reaper",
    )
    text = cards.render(card)
    assert "# p1 — day 3" in text
    assert "took the pure" in text
    assert "you are a house" in text
    assert "A) drop on pure" in text


def test_a_heuristic_card_says_there_is_nothing_to_show():
    text = cards.render(cards.normalise(day=1, player="p2"))
    assert "no card" in text.lower()


def test_turns_nest_under_the_season_heading():
    """Demotion has to move the per-turn heading, not strip its hashes."""
    joined = cards.render_many(
        [cards.normalise(day=1, player="p1", agent="a")], title="Season",
    )
    assert joined.startswith("# Season")
    assert "\n## p1 — day 1" in joined


def test_an_llm_card_shows_the_orders_that_reached_the_engine():
    """``orchestrator_2.runtime`` drops the harness's top-level ``moves``.

    Every LLM card therefore carried the reasoning and none of the
    orders, which is the half you need to tell "it decided badly" from
    "it decided well and the compiler emitted something else".
    """
    card = cards.normalise(
        envelope={"extras": {"final_moves": [
            {"a": "drop", "unit": "harvester_p1", "at": [17, 19]},
            {"a": "probe", "at": [9, 8]},
        ]}},
        day=4, player="p1", agent="tabula_v12",
    )
    text = cards.render(card)
    assert "## Orders issued (2)" in text
    assert "drop harvester_p1 (17,19)" in text
    assert "probe (9,8)" in text


def test_the_heuristic_spelling_of_moves_still_renders():
    """The offline runtime passes ``moves`` at the top level instead."""
    card = cards.normalise(
        envelope={"moves": [{"a": "probe", "at": [1, 2]}]},
        day=1, player="p2", agent="red_harvest",
    )
    assert "probe (1,2)" in cards.render(card)


def test_a_card_shows_what_the_sanitiser_rewrote():
    card = cards.normalise(
        envelope={"extras": {
            "sanitizer_changes": ["rerouted step off green at (18,20)"],
            "fallback_used": True,
            "fallback_reason": "parse: missing 'moves' key",
        }},
        day=2, player="p1", agent="tabula_v12",
    )
    text = cards.render(card)
    assert "## Corrections (1)" in text
    assert "rerouted step off green at (18,20)" in text
    assert "FELL BACK" in text and "missing 'moves' key" in text


def test_a_clean_turn_has_no_corrections_section():
    card = cards.normalise(envelope={"extras": {"sanitizer_changes": []}},
                           day=1, player="p1")
    assert "Corrections" not in cards.render(card)


def test_the_model_markdown_cannot_escape_its_fence():
    """A reply containing its own fence must not break the card open.

    If it does, the next section's heading renders as the model's prose
    and a 12,000-line season file becomes unreadable.
    """
    reply = "# THINK PASS\n```json\n{\"a\": 1}\n```\ndone"
    card = cards.normalise(
        envelope={"response": reply, "extras": {"final_moves": []}},
        day=1, player="p1", agent="tabula_v12",
    )
    text = cards.render(card)
    assert "````markdown" in text, "fence must widen past the nested one"
    assert "```json" in text, "the model's own fence survives intact"
    # In a season file the card's OWN headings demote a level, while the
    # model's stay exactly as written — they are inside a fence, so a
    # renderer shows them as code rather than as another turn.
    joined = cards.render_many([card], title="S")
    assert re.search(r"^### What the model said", joined, flags=re.M)
    assert re.search(r"^# THINK PASS", joined, flags=re.M), (
        "fenced content must be left alone, not demoted"
    )


# ── the HTML view ─────────────────────────────────────────────────


def test_html_cards_are_self_contained_and_carry_every_section():
    """The HTML view must stand alone off ``file://``.

    These pages are opened straight out of ``reports/seasons/``, where a
    ``fetch`` cannot work — same rule as the battle room. So the data
    has to arrive in the document, and every section the Markdown shows
    has to be there too, or the two views quietly disagree about what a
    turn contained.
    """
    card = cards.normalise(
        {"rationale": "take the seam", "prompt": "you are p1",
         "response_text": "thinking"},
        envelope={
            "moves": [{"a": "probe", "at": [1, 2]}],
            "extras": {
                "option_menu_block": "[GRAB1] step x5",
                "selected_option_ids": ["GRAB1"],
                "sanitizer_changes": ["dropped an illegal step"],
            },
        },
        day=3, player="p1", agent="tabula_v12",
    )
    html_out = cards.render_html([card], title="S")

    assert "fetch(" not in html_out, "a fetch cannot work over file://"
    assert "<script id=\"cards\"" in html_out
    for expected in (
        "take the seam", "probe (1,2)", "dropped an illegal step",
        "[GRAB1] step x5", "you are p1",
    ):
        assert expected in html_out, expected

    # The payload must be parseable JSON — an unescaped ``</`` inside a
    # model reply would close the script element early and blank the page.
    blob = re.search(
        r'<script id="cards" type="application/json">(.*?)</script>',
        html_out, flags=re.S,
    )
    assert blob, "the data block must survive rendering"
    parsed = json.loads(blob.group(1).replace("<\\/", "</"))
    assert [s["title"] for s in parsed["turns"][0]["sections"]][0] == \
        "Rationale"


def test_html_cards_escape_a_closing_script_tag_in_a_model_reply():
    card = cards.normalise(
        envelope={"response": "use </script><b>this</b>"},
        day=1, player="p1",
    )
    out = cards.render_html([card])
    assert "</script><b>" not in out, "the reply must not break out"
    assert "<\\/script>" in out


def test_write_record_emits_both_card_formats(tmp_path, file_backend):
    """Markdown for a model, HTML for a person — never one or the other."""
    result = seasons.run_season(
        {"p1": "heuristic", "p2": "heuristic"}, days=1, store=file_backend,
        season_name="BOTH", seed=7,
    )
    out = seasons.write_record(result, root=tmp_path)
    assert (out / "all-cards.md").read_text().strip()
    html_out = (out / "all-cards.html").read_text()
    assert "<script id=\"cards\"" in html_out
    assert "BOTH" in html_out


# ── refusing to run the wrong thing ───────────────────────────────


def test_a_mistyped_agent_is_refused_before_the_season_starts(file_backend):
    with pytest.raises(ValueError, match="unknown agent"):
        seasons.run_season(
            {"p1": "redwatch_repaer", "p2": "heuristic"},
            days=1, store=file_backend,
        )


def test_a_season_needs_two_seats(file_backend):
    with pytest.raises(ValueError):
        seasons.run_season({}, store=file_backend)


# ── seats carry their agent's name ────────────────────────────────


def test_a_seat_is_named_after_the_agent_holding_it(played, file_backend):
    """Latin house names are flavour for a game you are playing. For a
    season you are reviewing they hide which agent held which seat."""
    from fastapi.testclient import TestClient
    from server.app import app

    client = TestClient(app)
    status = client.get(f"/api/game/{played.session_id}/status").json()
    profiles = status.get("player_profiles") or {}
    assert profiles["p1"]["display_name"] == "RED_HARVEST"
    assert profiles["p2"]["display_name"] == "RED_HARVEST_LITE"


def test_the_menu_blurb_is_not_used_as_the_name():
    """`menu_label` is dropdown copy — the name is the part before it."""
    assert dispatch.display_name("red_harvest") == "RED_HARVEST"
    assert "heuristic bot" not in dispatch.display_name("red_harvest")


def test_two_spellings_of_one_bot_show_the_same_name():
    """`heuristic` and `red_harvest` are the same agent. A seat that
    reads HEURISTIC in one season and RED_HARVEST in the next looks
    like two competitors in a league table."""
    assert dispatch.display_name("heuristic") == dispatch.display_name(
        "red_harvest"
    )


def test_seats_never_share_a_tag():
    """A scoreboard with two identical tags is worse than an ugly one."""
    for seats in (
        {"p1": "red_harvest", "p2": "red_harvest_lite"},
        {"p1": "red_harvest", "p2": "red_harvest"},
        {"p1": "red_harvest", "p2": "red_harvest",
         "p3": "red_harvest", "p4": "red_harvest"},
    ):
        profiles = dispatch.seat_profiles(seats)
        tags = [p["tag"] for p in profiles.values()]
        assert len(set(tags)) == len(tags), tags
        assert all(len(t) == 3 for t in tags), tags


def test_every_named_seat_gets_a_palette_colour():
    from sea_of_colours.game.session import SEAT_COLOR_PALETTE

    profiles = dispatch.seat_profiles(
        {"p1": "red_harvest", "p2": "tabula_v12"}, seed=4,
    )
    for prof in profiles.values():
        assert prof["color"] in SEAT_COLOR_PALETTE


def test_a_typed_in_name_still_wins_over_the_agent_name():
    """The New Game modal is explicit input; it must not be overridden."""
    from server.app import _named_agent_profiles

    agents = {"p1": "red_harvest"}
    supplied = {"p1": {"display_name": "Alice", "tag": "ALI", "color": ""}}
    assert _named_agent_profiles(agents, supplied).get("p1") is None


def test_builtin_seats_keep_their_latin_house_names():
    """Only attendee forks are renamed — the houses are a game feature."""
    from server.app import _named_agent_profiles

    assert _named_agent_profiles(
        {"p1": "human", "p2": "red_harvest", "p3": "tabula_v12"}, None,
    ) == {}


# ── dispatch ──────────────────────────────────────────────────────


def test_the_heuristic_labels_run_without_credentials():
    for label in ("heuristic", "red_harvest", "red_harvest_lite"):
        assert dispatch.is_offline(label)
        assert not dispatch.needs_llm(label)


def test_the_shipped_llm_agent_is_known_and_needs_a_model():
    assert dispatch.resolve("tabula_v12") == "tabula_v12"
    assert not dispatch.is_offline("tabula_v12")


def test_an_llm_seat_gets_a_cortex_identity():
    """Drives bot naming and colour, not which runtime plays the turn."""
    assert dispatch.strategy_slug("heuristic") == "red_harvest"
    assert dispatch.strategy_slug("tabula_v12") == "cortex"
