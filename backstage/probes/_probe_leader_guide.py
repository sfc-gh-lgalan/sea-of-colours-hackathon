"""Does guide/leader.html still render off disk? (v1.46)

The leader guide is self-contained on purpose — it has to open by
double-clicking the file, before Python works, on a laptop that has just
been handed to someone. That means the usual safety net does not apply:
no server, no console anyone will look at, and a broken layout looks
exactly like a finished one until a human scrolls it.

So this opens it over ``file://`` and reports what a reader would
actually get: the scenes, the new three-mode content, and whether
anything failed to load.

    python backstage/probes/_probe_leader_guide.py
"""
from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parents[2]
PAGE = HERE / "guide" / "leader.html"

# Phrases that must survive, because each is the payload of an edit
# rather than decoration. If one goes missing the page still renders
# beautifully and simply no longer says the thing.
MUST_SAY = [
    "the three ways to test an agent",
    "Play against it",
    "refused on the Memory backend",
    "reload on by default",
    "Mint before you start the server",
    # v1.47 — the fork/push explainer. Its payload is the two rules an
    # attendee cannot deduce from the commands: that a stray file outside
    # the agent folder blocks the push, and that pushing is not entering.
    "How the GitHub side works",
    "outside your folder blocks the",
    "Pushing is not entering",
]


def main():
    from playwright.sync_api import sync_playwright

    if not PAGE.exists():
        print(f"! missing {PAGE}")
        return 1

    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1280, "height": 900})
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)))
        pg.goto(PAGE.as_uri(), wait_until="load")
        pg.wait_for_timeout(1200)

        scenes = pg.eval_on_selector_all(".gd-scene", "els => els.length")
        tables = pg.eval_on_selector_all("table", "els => els.length")
        terms = pg.eval_on_selector_all(".gd-term", "els => els.length")
        text = pg.inner_text("body")

        print(f"scenes : {scenes}")
        print(f"tables : {tables}")
        print(f"term blocks : {terms}")
        print(f"words  : {len(text.split())}")
        print()
        ok = True
        for phrase in MUST_SAY:
            hit = phrase.lower() in text.lower()
            ok = ok and hit
            print(f"  [{'x' if hit else ' '}] {phrase}")

        # A copy button that does not copy is worse than no button: the
        # reader believes they have the command.
        copied = pg.eval_on_selector_all(".gd-copy", "els => els.length")
        print(f"\ncopy buttons : {copied}")

        shot = HERE / "reports" / "leader_guide.png"
        shot.parent.mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(shot), full_page=False)
        print(f"screenshot   : {shot}")

        if errs:
            print(f"console errors: {errs[:4]}")
            ok = False
        br.close()

    print("\n" + ("guide renders and says everything it should" if ok
                  else "SOMETHING IS MISSING — read above"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
