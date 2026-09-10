#!/usr/bin/env python3
"""Screenshot a reel's cards, chapter by chapter.

Written for the redsign copy rewrite (v1.36) and kept because the gap it
covers is permanent: half of every reel is prose in `tutorial.js`, and
nothing else in the rig ever looks at it. The films assert that the
orders landed, not that the words next to them are true — the night-two
card claimed a redsign was "minted over" the seam for two versions,
which is the opposite of what §4.11 does.

Point it at another reel by editing REEL and the turn below.

    python backstage/films/_probe_redsign_card.py --base http://127.0.0.1:8022
"""
from __future__ import annotations

import argparse
import pathlib
import sys

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backstage.films.make_tutorial_films import setup_for  # noqa: E402

REEL = "advanced:planning:2"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8022")
    ap.add_argument("--out", default="/tmp/filmlook")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sid, _ = setup_for(args.base, "adv_redsign", 0)

    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_context(viewport={"width": 1280, "height": 900}).new_page()
        pg.goto(f"{args.base}/?session={sid}&player=p1", wait_until="networkidle")
        pg.wait_for_timeout(2500)
        # The modal takes its reel from the turn, not from a key, so ask
        # for the turn: `open()` is an index into whatever `onState` last
        # resolved.
        pg.evaluate(
            """(d) => {
                 document.dispatchEvent(
                   new CustomEvent('soc:tutorial-state', {detail: d}));
                 window.socTutorial.open(0);
               }""",
            {"game": sid, "tutorial": "advanced", "phase": "planning", "day": 2},
        )
        pg.wait_for_selector(".soc-tut", state="visible", timeout=10000)
        pg.wait_for_timeout(1200)

        for i in range(2):
            if i:
                pg.locator("[data-tut-next]").first.click()
                pg.wait_for_timeout(1400)
            shot = out / f"card_redsign_{i}.png"
            pg.locator(".soc-tut").first.screenshot(path=str(shot))
            print(f"wrote {shot}")

        br.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
