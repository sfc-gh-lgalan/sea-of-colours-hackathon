#!/usr/bin/env python3
"""Full-board sprite census for the replay cinematic.

Supersedes ``_fx_probe.py`` / ``_fx_collision.py``, both of which gave
false negatives for the same reason: they only looked where I had already
decided to look. ``_fx_probe`` sampled a hardcoded cell list, so a sprite
vanishing anywhere else was invisible; ``_fx_collision`` timed the
explosion only, and never asked when the harvester sprite actually left
the board — which is the thing being complained about.

This one assumes nothing. Every ~40ms it takes a census of EVERY visible
entity sprite on the board, plus every in-flight ghost, and diffs
consecutive frames. The output is a per-tick timeline of

    +Nms  GONE (x,y) <glyph> <tooltip>
    +Nms  NEW  (x,y) <glyph> <tooltip>
    +Nms  ghosts 0 -> 2          (sprites in flight)
    +Nms  FX ring/splash/flash

so "the harvester left before the other one landed" is a thing you read
straight off the timeline, rather than infer.

Usage: python backstage/probes/_fx_census.py [base_url] [season_slug] [max_ticks]
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8013"
SLUG = sys.argv[2] if len(sys.argv) > 2 else "probe-fx-reel"
MAX_TICKS = int(sys.argv[3]) if len(sys.argv) > 3 else 14
# Skip ahead without sampling — the interesting beat is often late in the
# reel and sampling every earlier tick costs ~5s each.
START_TICK = int(sys.argv[4]) if len(sys.argv) > 4 else 0

# One frame: every visible sprite on the board, keyed by cell, plus the
# in-flight layer and the FX layers.
SAMPLE_JS = """
() => {
  const out = { t: performance.now(), s: {}, ghosts: [], fx: {} };
  const host = document.getElementById('map-player') || document;
  for (const cell of host.querySelectorAll('[data-x][data-y]')) {
    const parts = [];
    for (const o of cell.querySelectorAll('.entity-overlay')) {
      // The arriving unit's overlay exists but is visibility:hidden for
      // the duration of its flight, so only computed-visible counts.
      const cs = getComputedStyle(o);
      if (cs.visibility === 'hidden') continue;
      // Colour, not just the character: a probe superseded by a rival's
      // probe is '·' before and '·' after, so a glyph-only diff calls a
      // change of owner "no change" and the swap goes unseen.
      parts.push(o.textContent.trim() + '/' + cs.color.replace(/\\s/g, ''));
    }
    if (!parts.length) continue;
    out.s[`${cell.dataset.x},${cell.dataset.y}`] = {
      g: parts.join(''),
      occ: (cell.getAttribute('data-occ') || '').slice(0, 46),
    };
  }
  for (const gh of document.querySelectorAll('.replay-anim-ghost')) {
    const r = gh.getBoundingClientRect();
    out.ghosts.push(`${gh.textContent.trim()}@${Math.round(r.left)},${Math.round(r.top)}`);
  }
  out.fx.splash = document.querySelectorAll('.probe-splash, .probe-ripple').length;
  out.fx.ring = document.querySelectorAll(
    '.collision-ring, .chain-explosion, .x-overlay').length;
  out.fx.flash = document.querySelectorAll('.cell--collision-flash').length;
  out.fx.standin = document.querySelectorAll('.harvest-terrain-standin').length;
  out.cap = (document.getElementById('replay-caption') || {}).textContent || '';
  return out;
}
"""


def main() -> int:
    with sync_playwright() as p:
        br = p.chromium.launch(channel="chrome")
        pg = br.new_page(viewport={"width": 1600, "height": 1000})
        pg.goto(f"{BASE}/watch.html?season={SLUG}",
                wait_until="networkidle", timeout=60_000)
        pg.wait_for_timeout(2500)
        pg.evaluate("""() => {
          for (const id of ['cc-victor', 'cc-endgame', 'cc-report',
                            'cc-resolving-overlay']) {
            const el = document.getElementById(id);
            if (el) { el.style.display = 'none'; el.hidden = true; }
          }
          document.body.classList.remove('cc-resolving');
        }""")

        # The watcher opens a finished season on its final day; rewind.
        pg.evaluate(
            "() => { const s = document.getElementById('replay-scrub');"
            " if (s) { s.value = '0';"
            " s.dispatchEvent(new Event('input', {bubbles: true})); } }"
        )
        pg.wait_for_timeout(1200)

        nxt = pg.query_selector("#replay-next")
        if not nxt:
            print("! no #replay-next control — is this the watcher page?")
            return 1

        for _ in range(START_TICK):
            nxt.click()
            pg.wait_for_timeout(600)
        if START_TICK:
            pg.wait_for_timeout(4000)   # let the last skipped tick settle

        for tick in range(START_TICK, MAX_TICKS):
            prev = pg.evaluate(SAMPLE_JS)
            t0 = prev["t"]
            nxt.click()
            events: list[str] = []
            pg_ghosts = len(prev["ghosts"])
            pfx = dict(prev["fx"])

            for _ in range(120):          # ~4.8s, covers the 2600ms lead
                cur = pg.evaluate(SAMPLE_JS)
                dt = int(cur["t"] - t0)

                for k, v in prev["s"].items():
                    if k not in cur["s"]:
                        events.append(f"  +{dt:>5}ms  GONE ({k}) "
                                      f"{v['g']!r} {v['occ']}")
                    elif cur["s"][k]["g"] != v["g"]:
                        # The case that matters and that presence-only
                        # diffing misses entirely: the cell keeps a sprite
                        # but it becomes a DIFFERENT one — a probe replaced
                        # by the harvester that killed it.
                        events.append(f"  +{dt:>5}ms  SWAP ({k}) "
                                      f"{v['g']!r} -> {cur['s'][k]['g']!r} "
                                      f"| {cur['s'][k]['occ']}")
                for k, v in cur["s"].items():
                    if k not in prev["s"]:
                        events.append(f"  +{dt:>5}ms  NEW  ({k}) "
                                      f"{v['g']!r} {v['occ']}")

                if len(cur["ghosts"]) != pg_ghosts:
                    events.append(f"  +{dt:>5}ms  ghosts {pg_ghosts} -> "
                                  f"{len(cur['ghosts'])}  {cur['ghosts']}")
                    pg_ghosts = len(cur["ghosts"])
                for key in ("splash", "ring", "flash", "standin"):
                    if cur["fx"][key] != pfx[key]:
                        events.append(f"  +{dt:>5}ms  FX {key} "
                                      f"{pfx[key]} -> {cur['fx'][key]}")
                        pfx[key] = cur["fx"][key]

                prev = cur
                pg.wait_for_timeout(40)

            cap = (prev["cap"] or "").strip().replace("\n", " ")[:90]
            print(f"\n=== tick {tick} === {cap}")
            if events:
                for e in events:
                    print(e)
            else:
                print("  (no sprite or fx transitions)")

        br.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
