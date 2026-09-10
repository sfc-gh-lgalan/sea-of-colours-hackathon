#!/usr/bin/env python3
"""Can you click the TOP ROW of the board while an order is armed — and does
the board hold still while you do it?

Two defects, one strip of screen, and the fix for the first caused the second.

v1.24: ``.pick-mode-banner`` was ``position:absolute; top:0`` INSIDE
``.cc-map-viewport``, so it painted over the board's first rows. It carries the
``[ stop ]`` button, so it cannot be ``pointer-events:none`` — it swallowed
every click in the strip it covered. And it only appears WHILE aiming, i.e.
exactly when those squares are targets. Fixed by moving it into its own grid
row above the viewport.

v1.27: that row was ``auto`` and the banner ``display:none`` when idle, so the
row collapsed and arming an order shoved the entire board down a line. Fixed by
reserving the row (``visibility`` instead of ``display``), so it is always the
same height.

The assertions are geometric, not behavioural, because that is where both
defects live: with the banner VISIBLE the map host must start below the
banner's bottom edge, ``elementFromPoint`` over row 0 must land on a real cell,
and the host's top must be IDENTICAL before and after arming. Using
elementFromPoint rather than a synthetic click is deliberate — a click that
lands on the banner may silently do nothing, which is exactly how the first one
shipped unnoticed.

Scratch harness, like the other ``backstage/probes/_fx_*.py`` — not a test.

Usage::

    python backstage/probes/_fx_pickbanner.py --base http://127.0.0.1:8022

Point it at an AGENT-RUN server, never the user's. See AGENTS.md.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request

from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).resolve().parents[2] / "reports" / "pickbanner"


def _post(base: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


# Read back the geometry that matters. Returns None for anything missing so
# the caller can fail with a useful message rather than a TypeError.
_PROBE = """
() => {
  const banner = document.getElementById('pick-mode-banner');
  const host = document.getElementById('map-player');
  const cell = document.querySelector('#map-player .row .cell');
  if (!banner || !host || !cell) return null;
  const b = banner.getBoundingClientRect();
  const h = host.getBoundingClientRect();
  const c = cell.getBoundingClientRect();
  const hit = document.elementFromPoint(
    c.left + c.width / 2, c.top + c.height / 2,
  );
  return {
    bannerHidden: banner.hidden,
    bannerBottom: b.bottom,
    bannerTop: b.top,
    hostTop: h.top,
    cellTop: c.top,
    hitClass: hit ? hit.className : null,
    hitId: hit ? hit.id : null,
    hitIsCell: !!(hit && hit.closest && hit.closest('.cell')),
    hitIsBanner: !!(hit && hit.closest && hit.closest('.pick-mode-banner')),
  };
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8022")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    OUT.mkdir(parents=True, exist_ok=True)

    game = _post(base, "/api/game/new", {
        "width": 30, "height": 20, "season_day_cap": 3,
        "players": ["p1", "p2"],
        "agents": {"p1": "human", "p2": "red_harvest"},
        "visibility_mode": "hidden",
    })
    sid = game["session_id"]
    print(f"session {sid}")

    fails: list[str] = []
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1280, "height": 800})
        pg.goto(f"{base}/?session={sid}&player=p1", wait_until="networkidle")
        pg.wait_for_selector("#map-player .row .cell", timeout=20000)

        before = pg.evaluate(_PROBE)
        if before is None:
            print("FAIL: map or banner missing from the page")
            return 1
        print(f"banner hidden : {before['bannerHidden']}")
        pg.screenshot(path=str(OUT / "idle.png"))

        # Show the banner exactly as enterPickMode does. We drive the DOM
        # rather than the arming flow on purpose: the defect is layout, and
        # this isolates it from whatever the ORDERS panel is doing.
        pg.evaluate("""
          () => {
            const b = document.getElementById('pick-mode-banner');
            const l = document.getElementById('pick-mode-label');
            if (l) l.textContent = 'pick a cell for PROBE';
            if (b) b.hidden = false;
          }
        """)
        pg.wait_for_timeout(150)

        after = pg.evaluate(_PROBE)
        pg.screenshot(path=str(OUT / "armed.png"))

        if after["bannerHidden"]:
            fails.append("banner did not become visible — test proved nothing")

        # THE regression: the banner must not sit on top of the board.
        overlap = after["bannerBottom"] - after["hostTop"]
        print(f"banner bottom {after['bannerBottom']:.1f} vs "
              f"map top {after['hostTop']:.1f}  (overlap {overlap:.1f}px)")
        if overlap > 0.5:
            fails.append(
                f"banner overlaps the map by {overlap:.1f}px — the top rows "
                "are still covered"
            )

        # And the first cell must actually be the thing under the pointer.
        print(f"element over cell(0,0): id={after['hitId']!r} "
              f"class={after['hitClass']!r}")
        if after["hitIsBanner"]:
            fails.append("the banner is the element over cell (0,0)")
        if not after["hitIsCell"]:
            fails.append(
                f"cell (0,0) is not hit-testable — got {after['hitClass']!r}"
            )

        # v1.27 — THE BOARD MUST NOT MOVE AT ALL.
        #
        # This assertion is INVERTED from v1.24, deliberately, and the
        # inversion is the whole point of the change. v1.24 required the
        # map to shift DOWN, because a banner that displaced nothing was
        # the signature of it still being an overlay covering the top
        # rows. It fixed the covering and bought the shift: arming an
        # order jolted the board a line down, right as you went to click
        # a square on it.
        #
        # The reserved row satisfies both at once — the banner is above
        # the board (checked above) AND costs no movement, because its
        # row is held open whether or not anything is aiming.
        shift = abs(after["hostTop"] - before["hostTop"])
        print(f"map shifted {shift:.1f}px when the banner appeared")
        if shift > 0.5:
            fails.append(
                f"the map moved {shift:.1f}px when the banner appeared — the "
                "row is collapsing when idle instead of being reserved"
            )
        # A reserved row is only reserved if it has height while hidden.
        if before["bannerBottom"] - before["bannerTop"] < 1:
            fails.append(
                "the hidden banner has no height — the row is collapsed, so "
                "any future content change will shove the board again"
            )
        # ...and it must still be genuinely hidden, not merely blank: an
        # invisible bar that eats clicks is the v1.24 bug wearing a hat.
        if before["hitIsBanner"]:
            fails.append(
                "the HIDDEN banner is the element over cell (0,0) — it is "
                "still hit-testable"
            )

        br.close()

    for f in fails:
        print(f"FAIL: {f}")
    print(f"\nshots in {OUT}")
    print("PASS" if not fails else f"{len(fails)} FAILURE(S)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
