#!/usr/bin/env python3
"""Check the screening room lists every film and plays one.

Cheap, but it covers the two ways this page fails quietly. It reads its
index out of `window.socTutorial.reels`, so a change to tutorial.js's
shape empties it and the page still renders — just with nothing in it.
And it reaches up two directories for both the reels and the films, so
anything that moves either one breaks it without a word.

Runs off disk, like the page: no server needed.

    python backstage/films/_fx_screening.py
"""
from __future__ import annotations

import argparse
import pathlib

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FILMS = ROOT / "server" / "static" / "films"
PAGE = HERE / "screening.html"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", default="/tmp/filmlook/screening.png")
    args = ap.parse_args()

    on_disk = sorted(p.name for p in FILMS.glob("*.webm"))
    fails: list[str] = []

    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_context(viewport={"width": 1400, "height": 900}).new_page()
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(PAGE.as_uri(), wait_until="networkidle")
        pg.wait_for_timeout(2500)

        items = pg.locator("#index .item")
        n = items.count()
        if n < len(on_disk):
            fails.append(f"{n} chapters listed but {len(on_disk)} films on disk")

        # Every film on disk should be named by some chapter. Read the
        # filename span rather than the button text: the button also
        # carries a duration, and "0:26" runs straight into the name.
        listed = set(pg.eval_on_selector_all(
            "#index .item .nm", "els => els.map(e => e.textContent.trim())"))
        for name in on_disk:
            if name not in listed:
                fails.append(f"{name} is on disk and in no reel")

        # Nothing may be marked missing: a reel naming an unshot film.
        gone = pg.locator("#index .item.gone")
        if gone.count():
            fails.append(f"{gone.count()} chapter(s) point at a film that "
                         f"will not load: {gone.all_inner_texts()}")

        # And the first one has to actually play.
        vid = pg.locator("#player video")
        if not vid.count():
            fails.append("no <video> on the stage for the first chapter")
        else:
            pg.wait_for_timeout(1800)
            played = pg.evaluate(
                "() => { const v = document.querySelector('#player video');"
                "        return v ? v.currentTime : 0; }"
            )
            if played <= 0:
                fails.append(f"the first film is not playing (t={played})")

        warn = pg.locator("#warn")
        if not warn.is_hidden():
            fails.append(f"orphan warning is up: {warn.inner_text()}")

        if errors:
            fails.append(f"page errors: {errors[:3]}")

        pathlib.Path(args.shot).parent.mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=args.shot)
        br.close()

    for f in fails:
        print(f"  FAIL {f}")
    print("FAIL" if fails else f"PASS — {n} chapters, {len(on_disk)} films")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
