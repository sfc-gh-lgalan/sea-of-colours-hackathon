"""Does the launcher actually show the four SNAP nights, highlighted? (v1.40)

A board blurb is prose in a Python dict rendered by a static page, and
neither end can report its own breakage: the note can be perfect and the
``[[marker]]`` can still arrive as literal brackets, or worse, the whole
note can fall back to "day 6 · p1" because the server is holding a stale
module. Both failures look fine in a diff.

So this drives the real page in a real browser and asks it three things
per board: that the title is the one the note gives it, that the marked
clauses became ``<mark>`` elements rather than text, and that no stray
brackets survived anywhere in the drawer.

    python backstage/probes/_probe_lab_notes.py --port 8022
"""

from __future__ import annotations

import argparse
import sys

from playwright.sync_api import sync_playwright

# The four nights, and the title each one must be showing.
WANT = {
    "LAB_83e44557_d6_p1": "Sighted and Armed",
    "LAB_fad99794_d3_p1": "Both Eyes on the Same Pure",
    "LAB_30890438_d2_p1": "Two Ghosts, One Seam",
    "LAB_30890438_d6_p1": "The Late Reversal",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8022)
    ap.add_argument("--shot", default="/tmp/lab_notes.png")
    args = ap.parse_args()

    bad = []
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        page = br.new_page(viewport={"width": 1100, "height": 1000})
        page.goto(f"http://127.0.0.1:{args.port}/lab/", wait_until="networkidle")

        options = page.eval_on_selector_all(
            "#boardpick option", "els => els.map(e => [e.value, e.textContent])"
        )
        print(f"{len(options)} boards in the picker")
        for value, text in options:
            if value in WANT:
                print(f"  {value:22} {text.strip()}")

        for board, title in WANT.items():
            page.select_option("#boardpick", board)
            page.wait_for_timeout(400)

            shown = dict(options).get(board, "")
            if title not in shown:
                bad.append(f"{board}: picker says {shown!r}, wanted {title!r}")

            marks = page.eval_on_selector_all(
                "#note mark", "els => els.map(e => e.textContent)"
            )
            note_text = page.inner_text("#note")
            if not marks:
                bad.append(f"{board}: nothing highlighted in the note")
            if "[[" in note_text or "]]" in note_text:
                bad.append(f"{board}: raw [[markers]] leaked into the drawer")

            # The wash has to actually be yellow, not inherited grey.
            colour = ""
            if marks:
                colour = page.eval_on_selector(
                    "#note mark", "e => getComputedStyle(e).color"
                )
            print(f"\n{board}  —  {len(marks)} highlighted, colour {colour}")
            for m in marks:
                print(f"    ▸ {m[:88]}")

        page.select_option("#boardpick", "LAB_fad99794_d3_p1")
        page.wait_for_timeout(400)
        page.screenshot(path=args.shot, full_page=True)
        print(f"\nscreenshot: {args.shot}")
        br.close()

    if bad:
        print("\nFAILED:")
        for b in bad:
            print("  ✗", b)
        return 1
    print("\nall four nights: titled, highlighted, no leaked markers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
