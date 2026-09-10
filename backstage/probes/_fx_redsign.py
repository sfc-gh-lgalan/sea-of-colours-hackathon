"""Trace when the REDSIGN overlay paints across a night's replay ticks.

Reproduces the user-reported beat order: beacon visible at the top of
the night, gone at PRAXIS BEGINS, then "discovered". The cinematic and
the replay share ``paintReplayFrameOntoMain``, so stepping the replay
exercises the same painter without needing a live agent turn.

Prints one row per tick: the tick's day/slot/hour, the cutoffs the
painter set, and how many redsign blobs are on the map. The beacon
should first appear at the discovery (day, hour) and never before.

Scratch harness, like the other ``backstage/probes/_fx_*.py`` — not a test.
"""

from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
SESSION = sys.argv[1] if len(sys.argv) > 1 else "46615fbd25744a078bc390098802c0c1"


def main() -> int:
    errors: list[str] = []
    with sync_playwright() as p:
        br = p.chromium.launch(channel="chrome")
        pg = br.new_page(viewport={"width": 1500, "height": 1000})
        pg.on("pageerror", lambda e: errors.append(str(e)))

        pg.goto(f"{BASE}/?session={SESSION}&watch=1", wait_until="networkidle")
        pg.wait_for_timeout(3500)
        pg.evaluate(
            """() => {
              for (const id of ['cc-endgame','cc-endgame-backdrop']) {
                const e = document.getElementById(id);
                if (e) { e.hidden = true; e.style.display = 'none'; }
              }
            }"""
        )
        pg.wait_for_timeout(400)

        meta = pg.evaluate(
            """() => {
              const w = window;
              return {
                ticks: (w.__SOC_DBG__ && w.__SOC_DBG__.ticks) || null,
              };
            }"""
        )
        print("debug hook:", meta)

        # No debug hook in the page, so drive the public transport: step the
        # scrubber and read the DOM after each move.
        slider = pg.query_selector("#replay-scrub")
        if not slider:
            print("no #replay-scrub — cannot step")
            br.close()
            return 1
        n = int(pg.evaluate("() => Number(document.getElementById('replay-scrub').max) || 0"))
        print("tick count:", n + 1)

        rows = []
        for i in range(n + 1):
            pg.evaluate(
                """(i) => {
                  const s = document.getElementById('replay-scrub');
                  s.value = String(i);
                  s.dispatchEvent(new Event('input', {bubbles: true}));
                  s.dispatchEvent(new Event('change', {bubbles: true}));
                }""",
                i,
            )
            pg.wait_for_timeout(120)
            row = pg.evaluate(
                """() => ({
                  day: (document.getElementById('replay-day-badge')||{}).textContent,
                  hour: (document.getElementById('replay-slot')||{}).textContent,
                  blobs: document.querySelectorAll('.redsign-cell').length,
                  beacons: document.querySelectorAll('.redsign-beacon').length,
                })"""
            )
            row["i"] = i
            rows.append(row)

        first = next((r for r in rows if r["blobs"] or r["beacons"]), None)
        print("first tick showing a redsign:", json.dumps(first))
        print()
        for r in rows:
            mark = "  <== REDSIGN" if (r["blobs"] or r["beacons"]) else ""
            print(
                f'  [{r["i"]:>3}] {str(r["day"]).strip():<10} '
                f'{str(r["hour"]).strip():<10} '
                f'blobs={r["blobs"]:<3} beacon={r["beacons"]}{mark}'
            )

        print("\npage errors:", errors or "none")
        br.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
