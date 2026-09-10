"""Smoke the battle room in a real browser and screenshot it.

The room is a static file opened over ``file://``, which is exactly the
mode that breaks silently — a fetch() would fail, a missing global would
render an empty page, and nothing would say so. So drive it for real:
open the file, click a failing turn, scrub the transport, and shout if
the console logged anything.

Not a test — a dev probe. Run it after touching room.html.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOM = Path("reports/battles/index.html").resolve()
# Outside the room's own directory: that directory gets served at
# /battles/ and zipped to share a bake, and neither wants dev shots in it.
OUT = Path("reports/battle-room-probe")


def main() -> int:
    if not ROOM.exists():
        print(f"no room at {ROOM} — run a recorded suite first", file=sys.stderr)
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []

    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1500, "height": 940})
        pg.on("console", lambda m: (
            problems.append(f"console.{m.type}: {m.text}")
            if m.type in ("error", "warning") else None
        ))
        pg.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))

        pg.goto(ROOM.as_uri())
        pg.wait_for_selector(".turn", timeout=10000)

        turns = pg.locator(".turn").count()
        print(f"turns in rail: {turns}")

        # The board must actually have pixels in it.
        painted = pg.evaluate("""() => {
          const c = document.getElementById('board');
          const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
          let lit = 0;
          for (let i = 0; i < d.length; i += 4)
            if (d[i] + d[i+1] + d[i+2] > 60) lit++;
          return { w: c.width, h: c.height, lit };
        }""")
        print(f"canvas: {painted}")
        if painted["lit"] < 200:
            problems.append(f"board looks blank: {painted}")

        pg.screenshot(path=str(OUT / "01-open.png"))

        # Open a failing turn — the case the room exists for.
        fails = pg.locator(".turn:has(.mk.f)")
        if fails.count():
            fails.first.click()
            pg.wait_for_timeout(250)
            pg.screenshot(path=str(OUT / "02-failing-turn.png"))
        else:
            problems.append("no failing turn in the bake to inspect")

        # Card panels the whole thing exists to show.
        for needed in ("Situation", "canonical", "Orders issued"):
            if needed.lower() not in pg.inner_text("#card").lower():
                problems.append(f"card is missing a {needed!r} section")

        # Play the turn through and confirm the transport moves.
        steps = pg.evaluate(
            "() => +document.getElementById('scrub').max"
        )
        for _ in range(min(int(steps), 6)):
            pg.click("#fwd")
        pg.wait_for_timeout(200)
        hour = pg.inner_text("#hour")
        print(f"after 6 steps: {hour!r}")
        if steps and not hour.startswith("H"):
            problems.append(f"transport did not advance: {hour!r}")
        pg.screenshot(path=str(OUT / "03-mid-playback.png"))

        # Fog: the board must visibly change, or the toggle is a lie.
        before = pg.evaluate(
            "() => { const c = document.getElementById('board');"
            " return c.toDataURL().length; }"
        )
        pg.click("#fog")
        pg.wait_for_timeout(150)
        after = pg.evaluate(
            "() => { const c = document.getElementById('board');"
            " return c.toDataURL().length; }"
        )
        if before == after:
            problems.append("fog toggle changed nothing on the canvas")
        pg.screenshot(path=str(OUT / "05-fog.png"))
        pg.click("#fog")

        # Expand the prompt panel (collapsed by default).
        heads = pg.locator("#card .sec h3")
        for i in range(heads.count()):
            if "prompt" in heads.nth(i).inner_text().lower():
                heads.nth(i).click()
                break
        pg.wait_for_timeout(150)
        pg.screenshot(path=str(OUT / "04-card-open.png"), full_page=True)

        br.close()

    if problems:
        print("\nPROBLEMS")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"\nclean — shots in {OUT}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
