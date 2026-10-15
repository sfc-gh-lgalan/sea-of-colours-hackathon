"""Does a lab seat that HOLDS a weapon show it on its station?

The arsenal bar has two sources — the live poll and the per-day
snapshot frozen into the session — and `_osArms` picks between them on
`_showingPast`. A turn-lab board is the case where those two disagree
the most: the snapshot is a real season's day-N reading, taken before
the lab existed, while the rack was stamped onto the clone at open
time. So a lab seat can hold a SNAP and have a snapshot that says it
holds nothing, and which one the bar draws is the whole question.

Run against a scratch server you own:

    python backstage/probes/_probe_arms_lab.py --port 8022
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# What each rack is worth in blue, so the expected pip count is derived
# from the price table rather than typed in beside it.
from sea_of_colours.game.weapons import BLUE_COST_BY_KIND  # noqa: E402

PER_PIP = 100
SEGMENTS = 6


def _post(base: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        f"{base}{path}", data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


# Read the bar the way a player reads it: off the glyphs. `·` is an
# empty pip, anything else is ordnance.
_BAR = """
(seat) => {
  const el = document.querySelector(`[data-os-arms="${seat}"]`);
  if (!el) return null;
  const text = [...el.querySelectorAll('.os-arms-pip')].map(p => p.textContent).join('');
  return {
    text,
    lit: [...text].filter(c => c !== '\\u00b7' && c.trim()).length,
    title: el.getAttribute('title') || '',
    live: (window.__SOC_ARMS__ || {})[seat] || null,
    self: window.__SOC_ARMS_SELF__,
    past: window.__SOC_SHOWING_PAST__,
  };
}
"""


def _blob(store, sid: str):
    row = store.load_session(sid)
    blob = row.get("json_state")
    while isinstance(blob, str):
        blob = json.loads(blob)
    return blob


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8022)
    ap.add_argument("--rack", default="snap")
    ap.add_argument("--other", default="chaff",
                    help="rack for the OTHER seat — different on purpose, so "
                         "a bar that draws the wrong seat's rack is visible")
    ap.add_argument("--board", default=None)
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    from playwright.sync_api import sync_playwright

    from turnlab import arms as lab_arms
    from turnlab import boards as lab_boards
    from turnlab import store as lab_store
    from turnlab import turn as lab_turn

    store = lab_store.store()
    found = lab_boards.discover(store)
    if args.board:
        board = next(b for b in found if b.id == args.board)
    else:
        # Not just `found[0]`: a rack is refused unless the board's own
        # season stocked that weapon, so the board has to be chosen by
        # what it can hold rather than by position in the list.
        board = next(
            b for b in found
            if {args.rack, args.other} - {"empty"}
            <= lab_arms.stockable(_blob(store, b.id))
        )
    rack = lab_arms.get(args.rack)
    counts = rack.counts
    want_blue = sum(BLUE_COST_BY_KIND.get(k, 0) * n for k, n in counts.items())
    want_pips = min(SEGMENTS, -(-want_blue // PER_PIP))  # ceil

    # Arm BOTH seats, differently. The arsenal is public (§4.9.8), so
    # every station on screen has to read true whoever is looking — and
    # the rival's bar is the one that goes through the `opponents` half
    # of `station_intel`, which is a different path from your own.
    other = "p2" if board.seat == "p1" else "p1"
    racks = {board.seat: args.rack, other: args.other}
    want = {}
    for s, rid in racks.items():
        c = lab_arms.get(rid).counts
        blue = sum(BLUE_COST_BY_KIND.get(k, 0) * n for k, n in c.items())
        want[s] = (rid, c, blue, min(SEGMENTS, -(-blue // PER_PIP)))

    opened = lab_turn.open_board(board.id, arms=racks)
    run_id = opened["run_id"]
    print(f"board {board.id}")
    for s, (rid, c, blue, pips) in want.items():
        held = {k: n for k, n in c.items() if n}
        print(f"  {s} rack {rid:8s} {held or 'none'} = {blue} blue "
              f"-> {pips} pip(s) expected")

    fails: list[str] = []
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_context(viewport={"width": 1440, "height": 900}).new_page()
        # Once per viewing seat: self and rival go down different paths.
        for viewer in (board.seat, other):
            pg.goto(f"{base}/play?session={run_id}&player={viewer}&boot=0",
                    wait_until="domcontentloaded")
            pg.wait_for_selector(f'[data-os-arms="{viewer}"]', timeout=60_000)
            # The bar repaints off the poll, not off first paint, so give
            # the poll a turn before reading it.
            pg.wait_for_timeout(4000)
            print(f"  viewing as {viewer}:")
            for s in (board.seat, other):
                got = pg.evaluate(_BAR, s)
                rid, c, blue, pips = want[s]
                role = "self " if s == viewer else "rival"
                print(f"    {s} {role} bar {got['text']!r} lit={got['lit']} "
                      f"want={pips}  live={got['live']}")
                if got["lit"] != pips:
                    held = {k: n for k, n in c.items() if n}
                    fails.append(
                        f"viewing as {viewer}, {s} ({role.strip()}) holds "
                        f"{held} — worth {blue} blue — and its station bar "
                        f"shows {got['lit']} lit pip(s), not {pips}. Bar is "
                        f"{got['text']!r}, live poll says {got['live']}")
            pg.screenshot(path=f"/tmp/arms_lab_{viewer}.png")

        # ── and now the same board, scrubbed ────────────────────────
        # `_osArms` hands the bar to the per-day SNAPSHOT the moment the
        # cursor leaves live, which is right for a real season and wrong
        # here: a lab clone's snapshots belong to the season it was cut
        # from, and that season never held the rack the lab stamped on
        # at open time. So the rack can vanish from the bar just because
        # someone moved the scrubber, on a board where it is still
        # sitting in the orbit panel.
        pg.goto(f"{base}/play?session={run_id}&player={board.seat}&boot=0",
                wait_until="domcontentloaded")
        pg.wait_for_selector(f'[data-os-arms="{board.seat}"]', timeout=60_000)
        pg.wait_for_timeout(4000)
        moved = pg.evaluate(
            "() => { const s = document.getElementById('replay-scrub');"
            "  if (!s || s.disabled) return null;"
            "  const to = Math.min(Number(s.max) || 0, 4);"
            "  s.value = String(to);"
            "  s.dispatchEvent(new Event('input', {bubbles: true}));"
            "  s.dispatchEvent(new Event('change', {bubbles: true}));"
            "  return {to, max: Number(s.max)}; }")
        pg.wait_for_timeout(1800)
        print(f"  scrubbed to {moved}:")
        for s in (board.seat, other):
            got = pg.evaluate(_BAR, s)
            rid, c, blue, pips = want[s]
            print(f"    {s} bar {got['text']!r} lit={got['lit']} want={pips}")
            if got["lit"] != pips:
                held = {k: n for k, n in c.items() if n}
                fails.append(
                    f"after a scrub, {s} still holds {held} — the orbit "
                    f"panel still offers it — but its station bar dropped "
                    f"to {got['lit']} lit pip(s) from {pips}. Bar is "
                    f"{got['text']!r}")
        pg.screenshot(path="/tmp/arms_lab_scrubbed.png")
        br.close()

    for f in fails:
        print(f"  - {f}")
    print("\nshot -> /tmp/arms_lab.png")
    print("FAIL" if fails else "PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
