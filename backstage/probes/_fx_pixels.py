#!/usr/bin/env python3
"""Pixel proof for one cell across one replay tick.

The DOM census can be fooled: a sprite can be present, computed-visible,
and still invisible because something opaque is painted over it. That is
exactly how the doomed-probe hold went unnoticed — the node was there the
whole time, under the terrain stand-in. So this harness ignores the DOM
and just screenshots the cell every ~250ms.

Usage: python backstage/probes/_fx_pixels.py <tick> <x> <y> [base] [slug]
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

# A tick is an HOUR, not a night, so counting from the top of the season to
# reach "night 4 hour 2" is both tedious and wrong-by-one in practice. Accept
# "D:H" and seek with the day-jump control, keeping bare ints for one-offs.
_ARG = sys.argv[1] if len(sys.argv) > 1 else "2"
DAY, TICK = (int(x) for x in _ARG.split(":")) if ":" in _ARG else (0, int(_ARG))
CX = int(sys.argv[2]) if len(sys.argv) > 2 else 10
CY = int(sys.argv[3]) if len(sys.argv) > 3 else 10
BASE = sys.argv[4] if len(sys.argv) > 4 else "http://127.0.0.1:8013"
SLUG = sys.argv[5] if len(sys.argv) > 5 else "probe-fx-reel"


def main() -> int:
    with sync_playwright() as p:
        br = p.chromium.launch(channel="chrome")
        pg = br.new_page(viewport={"width": 1600, "height": 1000},
                         device_scale_factor=3)
        pg.goto(f"{BASE}/watch.html?season={SLUG}",
                wait_until="networkidle", timeout=60_000)
        pg.wait_for_timeout(2500)
        pg.evaluate("""() => {
          for (const id of ['cc-victor', 'cc-endgame', 'cc-report',
                            'cc-resolving-overlay']) {
            const el = document.getElementById(id);
            if (el) { el.style.display = 'none'; el.hidden = true; }
          }
        }""")
        pg.evaluate(
            "() => { const s = document.getElementById('replay-scrub');"
            " if (s) { s.value = '0';"
            " s.dispatchEvent(new Event('input', {bubbles: true})); } }"
        )
        pg.wait_for_timeout(1200)

        nxt = pg.query_selector("#replay-next")
        if DAY:
            jump = pg.query_selector("#replay-day-next")
            for _ in range(DAY - 1):
                jump.click()
                pg.wait_for_timeout(500)
        for _ in range(TICK):
            nxt.click()
            pg.wait_for_timeout(600)
        pg.wait_for_timeout(4000)

        sel = f'#map-player [data-x="{CX}"][data-y="{CY}"]'
        # Wide boards run off a 1600px viewport, and screenshot clips are
        # viewport-relative: an off-screen cell silently yields a black
        # strip that reads exactly like "the sprite is missing".
        pg.evaluate(
            "(s) => { const e = document.querySelector(s);"
            " if (e) e.scrollIntoView({block: 'center', inline: 'center'}); }",
            sel,
        )
        pg.wait_for_timeout(400)
        label = pg.evaluate(
            "() => [document.getElementById('replay-day-badge')?.textContent,"
            " document.getElementById('replay-slot')?.textContent].join(' ')"
        )
        print(f"at tick {TICK}: {(label or '').strip()}")
        # A 3x3 block around the cell gives the eye some context.
        box = pg.evaluate(
            """(s) => { const e = document.querySelector(s);
                 if (!e) return null; const r = e.getBoundingClientRect();
                 return {x: r.left - r.width, y: r.top - r.height,
                         width: r.width * 3, height: r.height * 3}; }""",
            sel,
        )
        if not box:
            print(f"! no cell {CX},{CY}")
            return 1

        nxt.click()
        shots = []
        for i in range(16):
            path = f"/tmp/px_{i:02d}.png"
            pg.screenshot(path=path, clip=box)
            shots.append(path)
            pg.wait_for_timeout(250)

        # Stitch into one strip so the whole tick reads at a glance.
        pg.set_content(
            "<body style='margin:0;background:#111;display:flex;flex-wrap:wrap'>"
            + "".join(
                f"<div style='color:#8f8;font:10px monospace;text-align:center'>"
                f"<div>+{i * 250}ms</div>"
                f"<img src='file://{s}' style='width:90px;image-rendering:pixelated'>"
                f"</div>"
                for i, s in enumerate(shots)
            )
            + "</body>"
        )
        pg.wait_for_timeout(400)
        pg.screenshot(path="/tmp/px_strip.png", full_page=True)
        print(f"tick {TICK} cell ({CX},{CY}) -> /tmp/px_strip.png")
        br.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
