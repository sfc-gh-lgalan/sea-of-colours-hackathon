"""Check the REPLAY branch of the vision-border layer.

``backstage/probes/_fx_vision.py`` drives hand-built boards, which only ever exercise
the live path (own percept + inferred rivals). Replay is the other half and
it works differently: the frame carries ``cells_by_seat``, so every seat gets
an EXACT outline and none of them are inferred. This walks a real season's
cinematic and asserts that, plus the thing that actually broke — that the
frame handed to the border layer is the one whose cells are on the board.

Scratch harness, like the other ``backstage/probes/_fx_*.py`` — not part of pytest.

    python backstage/probes/_fx_vision_replay.py [base_url] [session_id]
"""

from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8021"
SESSION = sys.argv[2] if len(sys.argv) > 2 else ""


def main() -> int:
    if not SESSION:
        print("usage: _fx_vision_replay.py <base_url> <session_id>")
        return 2
    errors: list[str] = []

    with sync_playwright() as p:
        br = p.chromium.launch(channel="chrome")
        pg = br.new_page(viewport={"width": 1500, "height": 1000})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(f"{BASE}/?session={SESSION}&watch=1", wait_until="networkidle")
        pg.wait_for_timeout(4000)

        if not pg.evaluate("() => Boolean(window.__SOC_VISION_DBG__)"):
            print("FAIL: no __SOC_VISION_DBG__ hook")
            br.close()
            return 1

        # Step the replay and read the bands at each beat. A watcher sees
        # every seat, so every band must be exact (inferred=False) and there
        # must be more than one of them once both seats have vision.
        if not pg.query_selector("#replay-scrub"):
            print("FAIL: no #replay-scrub — the season has no replay frames")
            br.close()
            return 1
        n = int(pg.evaluate(
            "() => Number(document.getElementById('replay-scrub').max) || 0"
        ))
        print(f"tick count: {n + 1}")

        seen_multi = False
        for tick in range(0, n + 1, max(1, (n + 1) // 12)):
            pg.evaluate(
                """(i) => {
                  const s = document.getElementById('replay-scrub');
                  s.value = String(i);
                  s.dispatchEvent(new Event('input', {bubbles: true}));
                  s.dispatchEvent(new Event('change', {bubbles: true}));
                }""",
                tick,
            )
            pg.wait_for_timeout(220)
            bands = pg.evaluate("() => window.__SOC_VISION_DBG__.bands()")
            paths = pg.evaluate(
                "() => document.querySelectorAll('.vision-border-layer path').length"
            )
            src = pg.evaluate("() => window.__SOC_VISION_DBG__.source?.() ?? '?'")
            print(f"tick {tick:>2}: src={src} paths={paths} bands={json.dumps(bands)}")

            for b in bands:
                if b["inferred"]:
                    errors.append(
                        f"tick {tick}: seat {b['seat']} band is INFERRED — a replay "
                        f"frame carries cells_by_seat, so it must be exact"
                    )
            if len(bands) > 1:
                seen_multi = True
            if paths != len(bands):
                errors.append(
                    f"tick {tick}: {paths} paths for {len(bands)} bands"
                )

        if not seen_multi:
            errors.append(
                "never saw more than one band — the rival's exact outline is "
                "missing from the replay path"
            )

        br.close()

    print("\npage errors:" if errors else "\nPASS")
    for e in errors:
        print(f"  {e}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
