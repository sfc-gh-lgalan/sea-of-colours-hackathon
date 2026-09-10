#!/usr/bin/env python3
"""Shoot the storage badge and its tooltip, hovered.

The badge moved to the header's right edge and grew a styled tooltip in
place of the native ``title`` (which was ~1s late and drawn outside the
CRT filter). Both are things you have to LOOK at, so this hovers it and
captures the result rather than asserting on the DOM.

Scratch harness, like the other ``scripts/_*.py`` — not a test.

Usage::

    python backstage/probes/_ui_badge.py <session-id> [width height]
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
OUT = "/tmp"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    session = sys.argv[1]
    w = int(sys.argv[2]) if len(sys.argv) > 3 else 1440
    h = int(sys.argv[3]) if len(sys.argv) > 3 else 900

    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(viewport={"width": w, "height": h})
        pg.goto(f"{BASE}/play?session={session}", wait_until="networkidle")

        badge = pg.locator("#cc-backend-badge")
        badge.wait_for(state="visible", timeout=20_000)

        box = badge.bounding_box() or {}
        print(f"badge text : {badge.inner_text()!r}")
        print(f"badge class: {badge.get_attribute('class')}")
        print(f"badge x    : {box.get('x'):.0f} (viewport {w}) "
              f"-> {'RIGHT' if box.get('x', 0) > w / 2 else 'LEFT'} half")
        print(f"native title attr: {badge.get_attribute('title')!r}")

        pg.locator(".cc-header").screenshot(path=f"{OUT}/badge_header.png")

        badge.hover()
        tip = pg.locator("#cc-backend-tip")
        tip.wait_for(state="visible", timeout=5_000)
        pg.wait_for_timeout(400)
        tbox = tip.bounding_box() or {}
        print(f"\ntooltip visible: {tip.is_visible()}")
        print(f"tooltip head   : {tip.locator('.cc-backend-tip__head').inner_text()!r}")
        print(f"tooltip rows   : {tip.locator('.cc-backend-tip__row').count()}")
        print(f"tooltip box    : x={tbox.get('x'):.0f} y={tbox.get('y'):.0f} "
              f"w={tbox.get('width'):.0f} h={tbox.get('height'):.0f}")
        onscreen = (
            tbox.get("x", -1) >= 0
            and tbox.get("x", 0) + tbox.get("width", 0) <= w
            and tbox.get("y", 0) + tbox.get("height", 0) <= h
        )
        print(f"fully on-screen: {onscreen}")

        pg.screenshot(
            path=f"{OUT}/badge_tip.png",
            clip={"x": 0, "y": 0, "width": w,
                  "height": int(tbox.get("y", 0) + tbox.get("height", 0) + 20)},
        )
        # The card on its own, for looking at the design rather than the
        # placement.
        tip.screenshot(path=f"{OUT}/badge_card.png")
        wrapped = tip.evaluate(
            "el => [...el.querySelectorAll('.cc-backend-tip__li')]"
            "        .filter(r => r.getBoundingClientRect().height > 20).length"
        )
        print(f"ledger rows that wrap: {wrapped}")

        badge.evaluate("el => el.blur()")
        pg.mouse.move(w / 2, h / 2)
        pg.wait_for_timeout(300)
        print(f"tooltip hidden on mouseout: {not tip.is_visible()}")

        br.close()
    print(f"\nwrote {OUT}/badge_header.png  {OUT}/badge_tip.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
