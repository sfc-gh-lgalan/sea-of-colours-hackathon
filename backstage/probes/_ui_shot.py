"""Screenshot the command-centre UI region by region.

Boots a fresh quick game in a real browser and captures the surfaces the
UI pass touches — header, board, bottom replay bar, orders panel — at a
couple of viewport sizes, so "does this scale to a small laptop" is a
thing we can look at rather than guess about.

Scratch harness, like the other ``scripts/_*.py`` — not a test.

Usage::

    python backstage/probes/_ui_shot.py [tag] [width height]
"""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
OUT = "/tmp"
# Set from argv: a session id boots watcher mode on that season instead
# of starting a fresh quick game (the only way to see a populated
# timeline — dawn/dusk pips, day ticks, a scrub with range).
WATCH = ""

# 1440x900 is the small-laptop case the user cares about; 1680x1050 is a
# roomier desk monitor. Anything narrower than ~1200 is phone territory
# and has its own page (mobile.html).
SIZES = {
    "laptop": (1440, 900),
    "desk": (1680, 1050),
}

REGIONS = {
    "header": ".cc-header",
    "board": ".os-map-row",
    "orbitalL": "#os-side-left",
    "orbitalR": "#os-side-right",
    "bottombar": ".cc-replay-strip",
    "orders": '.cc-panel[data-cc-panel="orders"]',
    "tabbar": ".cc-tabbar",
}


def shoot(pg, tag: str, size_name: str) -> None:
    pg.screenshot(path=f"{OUT}/ui_{tag}_{size_name}_full.png")
    print(f"  {OUT}/ui_{tag}_{size_name}_full.png")
    for name, sel in REGIONS.items():
        el = pg.query_selector(sel)
        if el is None:
            print(f"  (no {name}: {sel})")
            continue
        try:
            el.screenshot(path=f"{OUT}/ui_{tag}_{size_name}_{name}.png")
            print(f"  {OUT}/ui_{tag}_{size_name}_{name}.png")
        except Exception as exc:  # off-screen / zero-size is fine to skip
            print(f"  ({name} failed: {type(exc).__name__})")


def main() -> int:
    global WATCH
    tag = sys.argv[1] if len(sys.argv) > 1 else "now"
    WATCH = sys.argv[2] if len(sys.argv) > 2 else ""
    errors: list[str] = []
    with sync_playwright() as p:
        br = p.chromium.launch(channel="chrome")
        for size_name, (w, h) in SIZES.items():
            pg = br.new_page(viewport={"width": w, "height": h})
            pg.on("pageerror", lambda e: errors.append(str(e)))
            if WATCH:
                pg.goto(f"{BASE}/?session={WATCH}&watch=1", wait_until="networkidle")
                pg.wait_for_timeout(6000)
                # A complete season auto-pops the results screen, which
                # dims the whole page. Drop it and park the cursor
                # mid-timeline so the bar has something to show.
                pg.evaluate(
                    """() => {
                      for (const e of document.querySelectorAll(
                        '[id*="endgame"], [class*="endgame"]')) {
                        e.hidden = true;
                        e.style.display = 'none';
                      }
                      const s = document.getElementById('replay-scrub');
                      if (s) {
                        s.value = String(Math.floor(Number(s.max) * 0.5));
                        s.dispatchEvent(new Event('input', {bubbles: true}));
                      }
                    }"""
                )
                pg.wait_for_timeout(2500)
            else:
                pg.goto(BASE, wait_until="networkidle")
                pg.wait_for_timeout(1000)
                pg.click("#btn-quick")
                pg.wait_for_selector(
                    "#solo-night-form", state="visible", timeout=60000,
                )
                pg.wait_for_timeout(3000)
            print(f"[{size_name} {w}x{h}]")
            shoot(pg, tag, size_name)
            # What the header actually knows about the season.
            if size_name == "laptop":
                head = pg.evaluate(
                    """() => {
                      const n = document.getElementById('cc-season-name');
                      const caps = [...document.querySelectorAll('.cc-newgame-cap')];
                      return {
                        seasonName: n ? n.textContent : '(missing)',
                        seasonHidden: n ? n.hidden : null,
                        capsHidden: caps.map((c) => c.hidden),
                      };
                    }"""
                )
                print("  header:", head)
            pg.close()
        br.close()
    print("page errors:", errors or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
