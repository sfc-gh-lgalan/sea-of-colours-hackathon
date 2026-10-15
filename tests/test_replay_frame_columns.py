"""Every key the engine puts on a replay frame must have somewhere to go.

v1.53 — written because SNAP did not, and nothing said so.

`GameSession.replay_push_scene` stamps a weapon's FX payload onto the
frame (`snap`, alongside `emp` / `chaff` / `mine`) and its live-effect
snapshot beside it (`snap_clouds`, alongside `emp_clouds` /
`mines_active`). The file store keeps frames verbatim, so all of that
survives a `seasons/` replay. The Snowflake store writes a FIXED column
list and silently drops anything not in it — and SNAP, which shipped in
v1.36, was never added.

The result was the worst kind of bug: a Snowflake season kept the
frame, its `snap_launch` tag, its caption and its log line, so every
readout agreed a SNAP had been fired, and the one thing missing was the
payload the watcher's launch animation is gated on. It played perfectly
offline and did nothing in the league, for four months.

So this does not test SNAP. It tests the RULE — scanning the source for
what the engine can emit, so the next weapon fails here at the point it
is added rather than in a competition.
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap

from sea_of_colours.game.session import GameSession
from sea_of_colours.snowpark import snowpark_store

#: Written by the batch writer itself rather than copied off the frame.
_WRITER_OWNED = {"session_id", "day", "frame_idx", "global_idx", "ts"}

#: On the frame, but deliberately not persisted per-frame. Each of these
#: is either rebuilt on read or served from the live session; adding to
#: this list is a decision, which is the point of having to make it.
_NOT_PERSISTED = {
    # Rebuilt from the cells_player_pN columns on read.
    "cells_by_seat",
    # Legacy alias, re-aliased from cells_player_p1 on read.
    "cells_player",
    # Frame ordinal, not content.
    "w",
    # Snowflake has no column for these; the UI falls back to the live
    # readout. Noted in `replay_push_scene`'s own comments.
    "weapons",
    "credits",
    "scheduled_orders_by_seat",
}


def _frame_keys_the_engine_emits() -> set[str]:
    """Keys `replay_push_scene` can put on a frame, read off its source.

    Source-scanned on purpose. Calling it would need a live session per
    weapon and would only prove the paths the test happened to drive;
    the literal assignments are the complete list and they cannot drift
    from themselves.

    Walked as an AST rather than grepped, because the values assigned
    are dict literals of their own (`{"cx": ..., "r": ...}` for a
    cloud) and a regex for quoted keys cannot tell those from the
    frame's.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(
        GameSession.replay_push_scene)))
    keys: set[str] = set()
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for t in targets:
            # frame["key"] = ...
            if (isinstance(t, ast.Subscript)
                    and isinstance(t.value, ast.Name)
                    and t.value.id == "frame"
                    and isinstance(t.slice, ast.Constant)
                    and isinstance(t.slice.value, str)):
                keys.add(t.slice.value)
            # frame = {...}
            elif (isinstance(t, ast.Name) and t.id == "frame"
                    and isinstance(node.value, ast.Dict)):
                for k in node.value.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        keys.add(k.value)
    # The per-seat percepts, written through an f-string subscript that
    # has no literal to read.
    keys |= {f"cells_player_{p}" for p in ("p1", "p2", "p3", "p4")}
    return keys


def _snowflake_columns() -> set[str]:
    src = inspect.getsource(snowpark_store.SnowparkSocStore.append_replay_frames)
    cols = re.search(r'cols\s*=\s*\(\s*((?:"[^"]*"\s*)+)\)', src)
    assert cols, "cannot find the SOC_REPLAY_FRAME column list"
    joined = "".join(re.findall(r'"([^"]*)"', cols.group(1)))
    return {c.strip() for c in joined.split(",") if c.strip()}


def test_every_frame_key_is_persisted_or_deliberately_dropped():
    emitted = _frame_keys_the_engine_emits()
    stored = _snowflake_columns()
    orphans = emitted - stored - _WRITER_OWNED - _NOT_PERSISTED
    assert not orphans, (
        f"{sorted(orphans)} are written onto a replay frame by the engine "
        f"but have no SOC_REPLAY_FRAME column, so Snowflake-backed seasons "
        f"drop them on the floor while seasons/ keeps them. Add the column "
        f"in snowflake/soc_schema.sql (CREATE *and* the ALTER block), add "
        f"it to `cols` and `json_keys` in snowpark_store.py, or add it to "
        f"_NOT_PERSISTED here with a reason."
    )


def test_snap_specifically_round_trips():
    """The one that was actually broken, pinned by name.

    The generic test above would pass again if someone removed SNAP from
    the engine; this says SNAP in particular is expected to be here, for
    as long as the weapon exists.
    """
    stored = _snowflake_columns()
    for col in ("snap", "snap_clouds"):
        assert col in stored, (
            f"SOC_REPLAY_FRAME has no `{col}` column. A Snowflake season "
            f"will keep the snap_launch frame, its caption and its log "
            f"line, and lose the payload the launch animation needs — the "
            f"weapon fires and the replay shows nothing (#62)."
        )


def test_the_json_key_list_and_the_column_list_agree():
    """A column in one list and not the other writes into the wrong slot.

    The insert is positional: `cols` names the columns and the SELECT is
    built from the same string, so a key present in `json_keys` but
    absent from `cols` is simply never written, and the reverse reads
    NULL out of a frame that had a value.
    """
    src = inspect.getsource(snowpark_store.SnowparkSocStore.append_replay_frames)
    block = re.search(r"json_keys\s*=\s*\((.*?)\n        \)", src, re.S)
    assert block, "cannot find json_keys"
    keys = set(re.findall(r'"([a-z0-9_]+)"', block.group(1)))
    cols = _snowflake_columns()
    assert keys <= cols, (
        f"{sorted(keys - cols)} are in json_keys but not in cols — they "
        f"will never be written"
    )


def test_read_path_parses_every_json_column_it_writes():
    """A VARIANT written but not parsed on read comes back as a string.

    Which is worse than missing: `Array.isArray(frame.snap)` is false
    for a string, so the FX stays silent and the data looks present.
    """
    write = inspect.getsource(snowpark_store.SnowparkSocStore.append_replay_frames)
    read = inspect.getsource(snowpark_store.SnowparkSocStore.list_replay_frames)
    block = re.search(r"json_keys\s*=\s*\((.*?)\n        \)", write, re.S)
    assert block
    written = set(re.findall(r'"([a-z0-9_]+)"', block.group(1)))
    parsed = set(re.findall(r'"([a-z0-9_]+)"', read))
    missing = written - parsed
    assert not missing, (
        f"{sorted(missing)} are written as VARIANT but not JSON-parsed in "
        f"list_replay_frames, so they arrive at the client as strings"
    )
