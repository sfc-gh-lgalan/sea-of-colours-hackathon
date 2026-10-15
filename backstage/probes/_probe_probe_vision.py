"""Does a probe's vision wait for the probe? And whose borders are drawn?

Four complaints, one surface — the replay's vision layer telling the
viewer things before, or beyond, what it should.

1. **In OBS the disc was already open.** The reveal mask is a fog delta,
   and the observer's own cells carry no fog, so nothing ever flipped
   and nothing was ever held: the tiles under a probe's disc painted
   with the frame, while the streak was still in the air.
2. **The outline arrived with the frame** in every view. The border
   layer is painted off the resolved state, so the seat-coloured ring
   around a new disc was drawn before the probe reached it.
3. **Every seat's border was drawn whoever you watched.** Picking a seat
   on the transport row changed the fog underneath but not the outlines
   over it, so you watched one House's board with all four Houses' true
   sight on top of it.
4. **The rival band blinked.** A rival probe only counted while it stood
   in your own live sight, and your sight sweeps — so a probe plainly
   marked on the board dropped in and out of the inference hour by hour.

Needs a server with a finished season on it:

    python backstage/probes/_probe_probe_vision.py --base http://127.0.0.1:8023
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "reports" / "probes"

#: `vision-border` is a true line of sight; `--inferred` is the dashed
#: guess built from probes the viewer can account for. Which of the two
#: a seat gets is the whole of complaint 3.
_BANDS = """
() => {
  const svg = document.querySelector('.vision-border-layer');
  if (!svg) return { solid: 0, dashed: 0 };
  return {
    solid: svg.querySelectorAll('path:not(.vision-border--inferred)').length,
    dashed: svg.querySelectorAll('path.vision-border--inferred').length,
  };
}
"""

#: Ground held back from the reveal, and something in the air to hold
#: it for. The streak is an unclassed full-screen <canvas> pinned to
#: the body (`spawnProbeTrail`), so it is counted by shape, not name.
#:
#: Two kinds of hold, because there are two right answers. A seat gets
#: a fog block (`.cell--probe-pending`) at full strength. OBS holds the
#: cell at the dim it already had (`data-obs-held`), and blocks as well
#: where the ground was never seen — the repaint has already put real
#: terrain under the dim, and a pale wash is not fog.
#:
#: `bright` is the thing that must never happen: a block in the
#: observer view with no dim on it, which is a grey disc arriving ahead
#: of the probe on a board whose fog is dim.
_HELD = """
() => ({
  pending: document.querySelectorAll(
    '.cell--probe-pending, .cell[data-obs-held]').length,
  bright: document.querySelectorAll(
    '.cell[data-obs-held].cell--probe-pending:not([data-obs-dim])').length,
  enclosed: (() => {
    // The tiles and the outlines have to agree. A band drawn around
    // ground that is still held is a seat-coloured ring enclosing
    // nothing but dark, which is what a per-seat hold used to produce
    // whenever two Houses opened ground in the same tick: the hold was
    // filed under the acting seat and the rival's band ignored it.
    const svg = document.querySelector('.vision-border-layer');
    const paths = svg ? [...svg.querySelectorAll('path')] : [];
    if (!paths.length) return 0;
    const pt = svg.createSVGPoint();
    let n = 0;
    for (const c of document.querySelectorAll('.cell[data-obs-held]')) {
      pt.x = Number(c.dataset.x) + 0.5;
      pt.y = Number(c.dataset.y) + 0.5;
      if (paths.some((p) => p.isPointInFill(pt))) n++;
    }
    return n;
  })(),
  streaks: [...document.body.children].filter(
    (e) => e.tagName === 'CANVAS').length,
})
"""

#: Scrub, then shove the season finale back out of the way. Landing on
#: the last tick of a finished season raises it, and it swallows the
#: clicks on the transport row underneath.
_SCRUB = """
(t) => {
  const s = document.getElementById('replay-scrub');
  s.value = String(t);
  s.dispatchEvent(new Event('input', { bubbles: true }));
  s.dispatchEvent(new Event('change', { bubbles: true }));
  for (const id of ['cc-endgame', 'cc-victor']) {
    const el = document.getElementById(id);
    if (el) el.remove();
  }
}
"""


def finished_season(base: str) -> str:
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

    sid = a.session or finished_season(a.base)
    print(f"season {sid}")
    fails: list[str] = []

    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_context(viewport={"width": 1600, "height": 1000}).new_page()
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

        seats = pg.evaluate(
            "() => (window.__SOC_PLAYERS__ || ['p1','p2']).slice()")
        mx = int(pg.evaluate(
            "() => Number(document.getElementById('replay-scrub').max)"))
        print(f"  seats {seats}, ticks 0..{mx}")
        # Tick 0 is the board before anyone has moved and has no sight on
        # it at all; park somewhere the season is actually under way.
        mid = max(8, mx // 2)

        # ── 3. whose borders, per view ──────────────────────────────
        for view in ["obs", *seats]:
            pg.click(f"#replay-view-{view}")
            pg.wait_for_timeout(700)
            pg.evaluate(_SCRUB, mid)
            pg.wait_for_timeout(1100)
            b = pg.evaluate(_BANDS)
            print(f"  view {view:4s}: {b['solid']} solid, {b['dashed']} dashed")
            if view == "obs":
                if b["solid"] < len(seats):
                    fails.append(
                        f"OBS drew {b['solid']} solid band(s) for "
                        f"{len(seats)} seats — the observer view is the one "
                        f"place every seat's true sight belongs, and it is "
                        f"short")
            elif b["solid"] != 1:
                fails.append(
                    f"watching {view}, {b['solid']} seats have a SOLID "
                    f"border. Only {view}'s own sight is {view}'s to know; a "
                    f"rival's belongs dashed and inferred, or not at all")

        # ── 4. the rival band holds through the night ───────────────
        # Not "is it ever drawn" — it always was. The complaint is that it
        # blinked, so this walks the night and counts the hours it is
        # missing while a rival probe is sitting marked on the board.
        pg.click(f"#replay-view-{seats[0]}")
        pg.wait_for_timeout(700)
        seen, blank, known = 0, 0, 0
        for t in range(8, mx - 2, max(1, (mx - 8) // 14)):
            pg.evaluate(_SCRUB, t)
            pg.wait_for_timeout(330)
            b = pg.evaluate(_BANDS)
            rivals = pg.evaluate(
                """(me) => {
                  const st = (document.getElementById('map-player')
                    || document.querySelector('.map-host'))?.__cellStore;
                  if (!st || !Array.isArray(st.cells)) return 0;
                  let n = 0;
                  for (const c of st.cells) {
                    for (const o of (c?.occupants || [])) {
                      if (o?.type === 'probe' && o.owner && o.owner !== me) n++;
                    }
                  }
                  return n;
                }""", seats[0])
            if not rivals:
                continue
            known += 1
            if b["dashed"]:
                seen += 1
            else:
                blank += 1
        print(f"  {seats[0]}: {known} hours with a rival probe on the board, "
              f"{seen} with a dashed band, {blank} without")
        if known >= 6 and seen < known * 0.6:
            fails.append(
                f"watching {seats[0]}, a rival probe was on the board for "
                f"{known} sampled hours but the dashed band was drawn in "
                f"only {seen}. It is blinking, not holding")

        # ── and it survives winding back ────────────────────────────
        far = max(mid + 1, mx - 4)
        pg.evaluate(_SCRUB, mid)
        pg.wait_for_timeout(900)
        there = pg.evaluate(_BANDS)
        pg.evaluate(_SCRUB, far)
        pg.wait_for_timeout(900)
        pg.evaluate(_SCRUB, mid)
        pg.wait_for_timeout(900)
        back = pg.evaluate(_BANDS)
        print(f"  tick {mid}: {there} -> wound to {far} and back: {back}")
        if there != back:
            fails.append(
                f"tick {mid} drew {there} on the way through and {back} "
                f"after winding back to it. The border layer is keeping "
                f"state across frames instead of being a function of one")

        # ── 1 + 2. the disc and its outline wait for the probe ──────
        pg.click("#replay-view-obs")
        pg.wait_for_timeout(700)
        pg.evaluate(_SCRUB, 1)
        pg.wait_for_timeout(800)
        pg.evaluate(
            "() => { const b = [...document.querySelectorAll('button')]"
            "  .find((e) => /play/i.test(e.textContent || '')); if (b) b.click(); }")
        # The assertion is simply that OBS holds ground back AT ALL. It
        # never used to: the observer's cells carry no fog, so the
        # fog→visible delta the mask is built from was empty every
        # frame and the disc painted with it.
        saw_streak, saw_held, held_max, fogged, ringed = 0, 0, 0, 0, 0
        for _ in range(500):
            s = pg.evaluate(_HELD)
            if s["streaks"]:
                saw_streak += 1
            if s["pending"]:
                saw_held += 1
                held_max = max(held_max, s["pending"])
            fogged = max(fogged, s["bright"])
            ringed = max(ringed, s["enclosed"])
            pg.wait_for_timeout(55)
            if saw_held > 10:
                break
        print(f"  OBS: {saw_streak} sampled frames with a streak in the air, "
              f"{saw_held} holding ground back (peak {held_max} cells), "
              f"{ringed} of them inside a band")
        if ringed:
            fails.append(
                f"{ringed} held cell(s) were inside a vision band. The "
                f"tiles are dark and the outline says a House can see "
                f"them — the hold and the border layer are reading "
                f"different answers")
        if fogged:
            fails.append(
                f"OBS fog-blocked {fogged} cell(s) at full strength. The "
                f"observer's fog is dim, so an undimmed block is a grey "
                f"disc that is BRIGHTER than the ground around it — it "
                f"announces the landing it is supposed to hide")
        pg.screenshot(path=str(SHOTS / "probe_vision_obs.png"))
        if not saw_streak:
            print("  (nothing caught in the air in the sampled window)")
        elif not saw_held:
            fails.append(
                f"in OBS, {saw_streak} sampled frames had a streak in the "
                f"air and not one of them held a single cell back. The disc "
                f"is painting with the frame — the tiles are on screen "
                f"before the probe that reveals them")

        if errs:
            fails.append(f"page errors: {errs[:3]}")
        br.close()

    for f in fails:
        print(f"  - {f}")
    print(f"\nshots in {SHOTS}")
    print("FAIL" if fails else "PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
