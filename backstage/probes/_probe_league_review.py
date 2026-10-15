#!/usr/bin/env python3
"""Drive `reports/league/review.html` in a real browser.

The league record is a **static, self-contained page whose entire body
is drawn by JavaScript from one baked blob** — which means it cannot
report its own breakage. A thrown exception in the draw code, a renamed
field in ``league.json``, a sticker that wraps out of its box: all of
them render a tidy, well-styled page with nothing in it, and it looks
fine until somebody opens the file you emailed to seventeen teams.

Pure-Python assertions cannot reach any of that, because the failure is
in the browser. So this drives it: counts what got drawn, hovers a tie
to prove the score card actually appears, and checks no sticker escapes
its own tie box.

Run it after touching ``sea_of_colours/league/review.py``:

    PLAYWRIGHT_BROWSERS_PATH="$HOME/Library/Caches/ms-playwright" \
        python backstage/probes/_probe_league_review.py

Deliberately outside pytest, like the rest of backstage/ — the suite
stays fast and dependency-free, and nobody installing at 9am needs a
120MB browser to run it.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PAGE = REPO / "reports" / "league" / "review.html"


def main() -> int:
    if not PAGE.is_file():
        print(f"! {PAGE} is missing — run `soc cup site` first")
        return 2

    from playwright.sync_api import sync_playwright

    fails: list[str] = []

    def want(cond: bool, msg: str) -> None:
        print(f"  {'ok  ' if cond else 'FAIL'}  {msg}")
        if not cond:
            fails.append(msg)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1200})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console",
                lambda m: errors.append(f"console.error: {m.text}")
                if m.type == "error" else None)

        page.goto(PAGE.as_uri(), wait_until="load")
        page.wait_for_timeout(900)

        print(f"\n{PAGE.relative_to(REPO)}  ({PAGE.stat().st_size // 1024} KB)\n")
        want(not errors, f"no page errors {errors}")

        # The page is JS-drawn, so "did anything render" is the first
        # and most important question.
        want(page.locator(".fig").count() >= 5, "headline figures drawn")
        want(page.locator(".rnd .rh").count() >= 4, "bracket rounds drawn")
        ties = page.locator(".tie").count()
        want(ties >= 8, f"ties drawn ({ties})")
        want(page.locator(".card").count() >= 2, "field cards drawn")
        want(page.locator("table.led tr").count() >= 3, "armoury ledger drawn")

        stickers = page.locator(".stk").count()
        want(stickers > 0, f"weapon stickers drawn ({stickers})")
        want(page.locator(".stk.cold").count() > 0,
             "unfired racks marked cold (dashed)")

        # A sticker that wraps out of its tie is the layout failure that
        # looks deliberate in a screenshot and wrong in a browser.
        escaped = page.evaluate("""() => {
          const out = [];
          document.querySelectorAll('.tie').forEach((t, i) => {
            const tb = t.getBoundingClientRect();
            t.querySelectorAll('.stk').forEach((s) => {
              const sb = s.getBoundingClientRect();
              if (sb.bottom > tb.bottom + 1 || sb.top < tb.top - 1
                  || sb.right > tb.right + 1) {
                out.push(i + ':' + s.textContent.trim());
              }
            });
          });
          return out;
        }""")
        want(not escaped, f"no sticker escapes its tie box {escaped}")

        # The whole point of the page: hover a tie, get its scores.
        played = page.locator(".tie.done").first
        want(played.count() > 0, "at least one played tie to hover")
        if played.count():
            played.hover()
            page.wait_for_timeout(350)
            pop = page.locator("#pop")
            want(pop.is_visible(), "hover reveals the score card")
            text = pop.inner_text() if pop.is_visible() else ""
            want("g1" in text, "score card lists per-season scores")
            want("season" in text.lower(), "score card names the season count")

            # And it must go away again, or it sits over the bracket.
            page.mouse.move(5, 5)
            page.wait_for_timeout(300)
            want(not pop.is_visible(), "score card hides on mouse-out")

        # Self-containment is the reason this file exists at all.
        html = PAGE.read_text()
        want("fetch(" not in html, "no fetch() — the page stays shareable")
        want("<script src=" not in html, "no external script")
        want('link rel="stylesheet" href="../' not in html, "no sibling CSS")

        shot = Path("/tmp/probe_league_review.png")
        page.screenshot(path=str(shot), full_page=True)
        print(f"\n  screenshot: {shot}")
        browser.close()

    print(f"\n{'PASS' if not fails else f'{len(fails)} FAILURE(S)'}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
