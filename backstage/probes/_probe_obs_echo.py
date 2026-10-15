"""In OBS, does remembered ground still read as live?

The observer's own dense cells carry no fog and no staleness — the
observer sees everything — so any statement about sight in OBS has to
be built from the SEATS' arrays. Two places were not:

* `paintObsVisionMap` dimmed on `pc.kind !== "fog"`, which is true for a
  remembered tile as well as a live one. Most of a mature board is
  remembered, so the board read as almost fully lit and the vision
  outlines drawn over it enclosed bright patches of a bright map.
* the hover readout took its LIVE/ECHO badge off the omniscient cell,
  so it said LIVE on all 1120 squares.

Both now go through a three-state `obsVis` (2 live / 1 echo / 0 never
seen). This checks the board against the seats' own arrays, cell by
cell, and reads the badge on one of each.

    python backstage/probes/_probe_obs_echo.py --base http://127.0.0.1:8023
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "reports" / "probes"

#: The board that is actually ON SCREEN.
#:
#: Both map hosts stay in the DOM and one is shown, so picking
#: `#map-observer` by name gets you a stale store left over from the
#: live observer pull — and the replay, OBS included, paints into
#: `#map-player`. Same visibility test the film rig uses for the same
#: reason.
_HOST = """
  const vis = (m) => m && m.getBoundingClientRect().width > 0;
  const o = document.getElementById('map-observer');
  const p = document.getElementById('map-player');
  const host = (vis(p) && p) || (vis(o) && o);
"""

#: One seat's own board, as live/echo/fog per cell.
#:
#: Ground truth comes from the SEAT VIEWS rather than from the replay
#: payload, which spares the probe the scrub-stop -> frame-index mapping
#: the film rig refuses to reverse-engineer for exactly the reason it
#: would go stale. A seat's painted store holds that seat's own dense
#: cells, complete with `kind` and `stale`, so reading each seat in turn
#: at one tick and unioning them is the same answer by a route the page
#: cannot fudge.
_SEAT_STATE = """
() => {
""" + _HOST + """
  const st = host && host.__cellStore;
  if (!st || !Array.isArray(st.cells)) return null;
  const out = new Array(st.width * st.height);
  for (let i = 0; i < out.length; i++) {
    const c = st.cells[i];
    out[i] = (!c || c.kind === 'fog') ? 0 : (c.stale ? 1 : 2);
  }
  return { w: st.width, h: st.height, v: out };
}
"""

#: What OBS decided, and what it actually painted.
_OBS_STATE = """
() => {
""" + _HOST + """
  const st = host && host.__cellStore;
  if (!st) return { err: 'no cell store on the observer board' };
  if (!st.obsVis) return { err: 'no obsVis on the store' };
  const w = st.width, h = st.height;
  const painted = new Array(w * h);
  for (let i = 0; i < w * h; i++) {
    const el = host.querySelector(
      `.cell[data-x="${i % w}"][data-y="${Math.floor(i / w)}"]`);
    const a = el ? el.getAttribute('data-obs-dim') : null;
    painted[i] = el ? (a === 'fog' ? 0 : a === 'echo' ? 1 : 2) : -1;
  }
  return { w, h, v: Array.from(st.obsVis), painted };
}
"""

#: Find one cell in each state and hand back its coordinates. The hover
#: itself is driven by Playwright's real mouse — the readout is wired to
#: `mousemove` and a synthesised event does not raise it.
_BADGE = """
(want) => {
""" + _HOST + """
  const st = host && host.__cellStore;
  if (!st || !st.obsVis) return null;
  for (let i = 0; i < st.width * st.height; i++) {
    if (st.obsVis[i] !== want) continue;
    const x = i % st.width, y = Math.floor(i / st.width);
    if (host.querySelector(`.cell[data-x="${x}"][data-y="${y}"]`)) {
      return [x, y];
    }
  }
  return null;
}
"""


def finished(base: str) -> str:
    with urllib.request.urlopen(f"{base}/api/sessions", timeout=60) as r:
        rows = json.loads(r.read().decode())["sessions"]
    for row in rows:
        if row.get("phase") == "season_complete":
            return row["session_id"]
    raise SystemExit("no finished season on this server")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8023")
    ap.add_argument("--session", default=None)
    a = ap.parse_args()
    SHOTS.mkdir(parents=True, exist_ok=True)

    sid = a.session or finished(a.base)
    print(f"season {sid}")
    fails: list[str] = []

    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_context(viewport={"width": 1700, "height": 1050}).new_page()
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(f"{a.base}/play?session={sid}&station=1&boot=0",
                wait_until="domcontentloaded")
        pg.wait_for_selector("#replay-scrub", timeout=120_000)
        pg.wait_for_function(
            "() => { const s = document.getElementById('replay-scrub');"
            "  return s && !s.disabled && Number(s.max) > 8; }",
            timeout=120_000)
        pg.wait_for_timeout(1200)
        pg.click("#replay-view-obs")
        pg.wait_for_timeout(600)
        mx = int(pg.evaluate(
            "() => Number(document.getElementById('replay-scrub').max)"))
        # Late enough that the board has plenty of memory on it — which
        # is the whole point: early on almost nothing is remembered and
        # the bug does not show.
        pg.evaluate(
            "(t) => { const s = document.getElementById('replay-scrub');"
            "  s.value = String(t);"
            "  s.dispatchEvent(new Event('input', {bubbles: true}));"
            "  s.dispatchEvent(new Event('change', {bubbles: true}));"
            "  for (const id of ['cc-endgame','cc-victor']) {"
            "    const e = document.getElementById(id); if (e) e.remove(); } }",
            max(10, int(mx * 0.75)))
        pg.wait_for_timeout(1600)
        seats = pg.evaluate(
            "() => (window.__SOC_PLAYERS__ || ['p1','p2']).slice()")

        # Ground truth: every seat's own board at this same tick.
        union: list[int] = []
        for s in seats:
            pg.click(f"#replay-view-{s}")
            pg.wait_for_timeout(800)
            one = pg.evaluate(_SEAT_STATE)
            if not one:
                fails.append(f"could not read {s}'s board")
                continue
            if not union:
                union = list(one["v"])
            else:
                union = [max(a, b) for a, b in zip(union, one["v"])]
            print(f"  {s}: {one['v'].count(2)} live, {one['v'].count(1)} "
                  f"echo, {one['v'].count(0)} fog")

        pg.click("#replay-view-obs")
        pg.wait_for_timeout(1000)
        got = pg.evaluate(_OBS_STATE)
        if got.get("err"):
            print(f"  !! {got['err']}")
            fails.append(str(got["err"]))
        elif union:
            want_echo = union.count(1)
            print(f"  houses between them: {union.count(2)} live, "
                  f"{want_echo} echo, {union.count(0)} never seen")
            print(f"  OBS decided:          {got['v'].count(2)} live, "
                  f"{got['v'].count(1)} echo, {got['v'].count(0)} never seen")
            print(f"  OBS painted:          {got['painted'].count(2)} live, "
                  f"{got['painted'].count(1)} echo, "
                  f"{got['painted'].count(0)} never seen")
            bad = [(i % got["w"], i // got["w"], union[i], got["v"][i])
                   for i in range(len(union)) if union[i] != got["v"][i]]
            if bad:
                fails.append(
                    f"{len(bad)} cell(s) where OBS disagrees with the seats' "
                    f"own boards, e.g. (x,y,want,got) {bad[:4]}")
            off = [i for i in range(len(got["v"]))
                   if got["v"][i] != got["painted"][i]]
            if off:
                fails.append(
                    f"{len(off)} cell(s) painted a different state from the "
                    f"one OBS decided, e.g. index {off[:4]}")
            # The regression in one number: before the fix every
            # remembered cell painted at full brightness, so this was 0.
            if want_echo == 0:
                print("  (no remembered ground at this tick — inconclusive)")
            elif got["painted"].count(1) == 0:
                fails.append(
                    f"{want_echo} cells are remembered but unseen and NONE "
                    f"are painted that way — echo is still rendering as "
                    f"something else")

        for want, label in ((2, "LIVE"), (1, "ECHO"), (0, "FOG")):
            at = pg.evaluate(_BADGE, want)
            if not at:
                print(f"  no {label} cell on the board to hover")
                continue
            pg.hover(f'#map-player .cell[data-x="{at[0]}"][data-y="{at[1]}"]')
            pg.wait_for_timeout(400)
            txt = pg.evaluate(
                "() => { const t = document.querySelector('.cell-tooltip');"
                "  return t && !t.hidden ? t.textContent.trim() : ''; }")
            one = " ".join(txt.split())[:64]
            print(f"  hover {at} (want {label}): {one!r}")
            if label not in txt:
                fails.append(
                    f"cell {at} is {label} to the Houses but its readout "
                    f"says {one!r}")

        pg.screenshot(path=str(SHOTS / "obs_echo.png"))
        if errs:
            fails.append(f"page errors: {errs[:3]}")
        br.close()

    for f in fails:
        print(f"  - {f}")
    print(f"\nshot in {SHOTS / 'obs_echo.png'}")
    print("FAIL" if fails else "PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
