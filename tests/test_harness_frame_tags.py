"""The harness must filter replay frames on tags the engine really emits.

A seat's EXECUTION LOG is not read from the engine — it is rebuilt in the
harness from replay frames, filtered by a hand-written tag set. That set
is a copy of engine vocabulary living in another module, and nothing had
ever checked the two agreed.

They did not. ``_OWN_ACTION_TAGS`` carried ``"snap"`` while the engine
writes ``snap_launch``, so every SNAP a seat fired vanished from its own
log; the hour just went missing, with no gap and no error. Observed in a
real season: a seat fired a SNAP that fried a rival probe, saw nothing in
its log, and wrote "the SNAP denial worked" into its strategy journal
anyway — right by luck, and the guess is what it carried forward. The set
also carried ``"snapped"``, which the engine has never emitted at all.

Both are the same failure and neither is visible from inside the harness,
because a tag that matches nothing looks exactly like a night where the
thing did not happen. So these tests compare against the engine source
itself rather than against a second hand-written list.

Only the shipped baseline is checked. Attendee forks copy it wholesale,
so fixing it here is what stops it being replicated into the room; their
directories are their own and may not exist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import last_night


ENGINE_DIR = Path(__file__).resolve().parents[1] / "sea_of_colours" / "game"

#: Tags the log has to carry whatever else changes: every weapon the
#: engine can fire, and every way one can be used against you.
MUST_LOG = {"emp_launch", "snap_launch", "chaff_flare", "chaffed", "empd"}


def _engine_tags() -> set[str]:
    """Every replay-frame tag the engine can write.

    Scraped from the source rather than restated, because a restated
    list is the exact thing that went wrong: it agrees on the day it is
    written and drifts silently afterwards.
    """
    found: set[str] = set()
    for py in ENGINE_DIR.rglob("*.py"):
        text = py.read_text(errors="ignore")
        found |= set(re.findall(r'tag=["\']([a-z_]+)["\']', text))
        found |= set(re.findall(r'"tag":\s*["\']([a-z_]+)["\']', text))
    return found


def test_the_engine_tag_scrape_finds_something():
    """Guard the guard: a regex that matches nothing passes everything."""
    tags = _engine_tags()
    assert "emp_launch" in tags and "drop" not in MUST_LOG - tags
    assert len(tags) > 8, f"scrape looks broken, found only {tags}"


@pytest.mark.parametrize("tag", sorted(MUST_LOG))
def test_every_weapon_tag_is_logged_to_its_owner(tag):
    """A seat has to see its own ordnance in its own log."""
    assert tag in last_night._OWN_ACTION_TAGS, (
        f"{tag!r} is a real engine frame tag but the harness does not log "
        f"it — a seat doing this would see the hour go missing"
    )


def test_no_tag_is_matched_that_the_engine_never_writes():
    """The ``snapped`` class of bug: a filter token that matches nothing.

    Harmless-looking and self-concealing — it reads as coverage while
    providing none, and it is why ``"snap"`` survived a fix that was
    aimed straight at it.
    """
    engine = _engine_tags()
    claimed = last_night._OWN_ACTION_TAGS | last_night._PUBLIC_ORBITAL_TAGS
    # 'wait' and the field verbs come from the order queue, not from a
    # tag= literal, so they are legitimately absent from the scrape.
    from_queue = {"wait", "drop", "step", "pickup", "probe", "mine_lay"}
    phantom = sorted(claimed - engine - from_queue)
    assert not phantom, (
        f"{phantom} appear in the harness tag sets but the engine never "
        f"emits them, so they filter nothing and hide what they claim to show"
    )


def test_a_snap_frame_reaches_the_execution_log():
    """End to end on the shape the engine actually stores."""
    frames = [
        {"owner": "p1", "tag": "snap_launch", "hour": 5,
         "attempted": "snap_launch @(8,14)", "outcome": "ok",
         "caption": "p1 fired SNAP at (8,14); 1 probe(s) fried"},
    ]

    rows = last_night.read_execution_log(frames, "p1", width=40, height=28)

    own = [r for r in rows if r.get("kind") == "own"]
    assert [r["tag"] for r in own] == ["snap_launch"], (
        f"the SNAP hour did not survive the filter: {rows}"
    )
    assert own[0]["hour"] == 5
