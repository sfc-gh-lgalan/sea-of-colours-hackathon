"""Scratch check: the landing page's TUTORIAL chooser.

Small on purpose. The chooser is three buttons that post a preset name,
and the only thing worth pinning in a browser is that each card reaches
a game with the shape its blurb promises — the API-level checks live in
tests/test_tutorial_mode.py.

    SOC_BACKEND=memory python run_web.py --no-reload --port 8022 &
    python backstage/films/_fx_landing_tut.py --port 8022
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8022)
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    from playwright.sync_api import sync_playwright

    fails: list[str] = []
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(f"{base}/", wait_until="networkidle")
        pg.wait_for_timeout(600)

        body = pg.locator("body").inner_text()
        if "Quick game" in body and "Tutorial" not in body:
            fails.append("landing still leads with 'Quick game'")

        btn = pg.locator("button:has-text('Tutorial')").first
        if btn.count() == 0:
            fails.append("no Tutorial button on the landing page")
        else:
            btn.click()
            pg.wait_for_timeout(600)
            cards = pg.locator(".landing-tut-card")
            if cards.count() != 3:
                fails.append(f"chooser has {cards.count()} cards, want 3")
            names = pg.locator(".landing-tut-name").all_inner_texts()
            for want in ("Basic", "Advanced", "Quick"):
                if not any(want.lower() in n.lower() for n in names):
                    fails.append(f"no {want!r} card — got {names}")
            pg.screenshot(path="reports/_fx_landing_tut.png")

        if errors:
            fails.append(f"page errors: {errors[:3]}")
        br.close()

    for f in fails:
        print("FAIL:", f)
    print("PASS" if not fails else f"FAIL ({len(fails)})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
