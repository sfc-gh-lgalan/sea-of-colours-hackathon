#!/usr/bin/env python3
"""One-off: what is actually eating the width in a queue row?

Scratch diagnostic for the v1.25 composer work. Prints the measured
width of every child of a queue row so the layout can be tuned against
numbers instead of guesses. Delete when the composer settles.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

from playwright.sync_api import sync_playwright


def _post(base: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


PROBE = """
() => {
  const host = document.getElementById('solo-move-queue');
  const row = host.querySelector('.solo-queue-row');
  if (!row) return null;
  const b = (el) => el.getBoundingClientRect();
  const cs = getComputedStyle(row);
  return {
    drawer: Math.round(b(document.getElementById('cc-drawer')).width),
    scroll: Math.round(b(document.querySelector('.cc-orders-scroll')).width),
    host: Math.round(b(host).width),
    row: Math.round(b(row).width),
    rowPad: cs.padding,
    rowGap: cs.gap,
    fontSize: cs.fontSize,
    children: [...row.children].map((c) => ({
      cls: c.className,
      txt: (c.textContent || '').trim().slice(0, 22),
      w: Math.round(b(c).width * 10) / 10,
      minW: getComputedStyle(c).minWidth,
      top: Math.round(b(c).top),
    })),
    sumChildren: Math.round(
      [...row.children].reduce((a, c) => a + b(c).width, 0)),
  };
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8022")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    game = _post(base, "/api/game/new", {
        "width": 30, "height": 20, "season_day_cap": 5,
        "players": ["p1", "p2"],
        "agents": {"p1": "human", "p2": "red_harvest"},
        "visibility_mode": "hidden",
    })
    sid = game["session_id"]

    with sync_playwright() as pw:
        br = pw.chromium.launch()
        for w in (1280, 1024):
            pg = br.new_page(viewport={"width": w, "height": 800})
            pg.goto(f"{base}/?session={sid}&player=p1", wait_until="networkidle")
            pg.wait_for_selector("#orders-asset-roster", timeout=20000)
            pg.wait_for_timeout(600)
            pg.evaluate(
                """() => {
                  const D = window.__SOC_ORDERS_DBG__;
                  const u = D.firstHarvester();
                  if (u) D.addQueueRow('drop', 12, 4, u);
                  D.rerender();
                }"""
            )
            pg.wait_for_timeout(250)
            out = pg.evaluate(PROBE)
            print(f"\n=== viewport {w} ===")
            if not out:
                print("no row")
                continue
            print(f"drawer {out['drawer']}  scroll {out['scroll']}  "
                  f"host {out['host']}  row {out['row']}")
            print(f"pad {out['rowPad']}  gap {out['rowGap']}  "
                  f"font {out['fontSize']}")
            for c in out["children"]:
                print(f"   {c['w']:>7}px  top={c['top']}  min={c['minW']:<8} "
                      f"{c['cls'][:34]:<34} {c['txt']!r}")
            print(f"   sum children = {out['sumChildren']}px")
            pg.close()
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
