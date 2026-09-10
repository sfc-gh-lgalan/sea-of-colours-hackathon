"""Dump the DOM shape of a UI region so edits target the right node.

Scratch harness, like the other ``scripts/_*.py`` — not a test.

Usage::

    python backstage/probes/_ui_probe.py '<css selector>' [depth]
"""

from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"


def main() -> int:
    sel = sys.argv[1] if len(sys.argv) > 1 else "body"
    depth = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    with sync_playwright() as p:
        br = p.chromium.launch(channel="chrome")
        pg = br.new_page(viewport={"width": 1440, "height": 900})
        pg.goto(BASE, wait_until="networkidle")
        pg.wait_for_timeout(900)
        pg.click("#btn-quick")
        pg.wait_for_selector("#solo-night-form", state="visible", timeout=60000)
        pg.wait_for_timeout(2800)
        tree = pg.evaluate(
            """([sel, depth]) => {
              const walk = (el, d) => {
                if (!el || d < 0) return null;
                const r = el.getBoundingClientRect();
                return {
                  tag: el.tagName.toLowerCase(),
                  id: el.id || undefined,
                  cls: el.className && typeof el.className === 'string'
                    ? el.className : undefined,
                  hidden: el.hidden || undefined,
                  box: [Math.round(r.x), Math.round(r.y),
                        Math.round(r.width), Math.round(r.height)],
                  text: (el.children.length === 0 && el.textContent)
                    ? el.textContent.trim().slice(0, 60) : undefined,
                  kids: d > 0
                    ? [...el.children].map((c) => walk(c, d - 1)) : undefined,
                };
              };
              return [...document.querySelectorAll(sel)].map((e) => walk(e, depth));
            }""",
            [sel, depth],
        )
        print(json.dumps(tree, indent=1))
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
