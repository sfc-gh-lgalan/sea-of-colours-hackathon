"""How long does each replay sprite actually live, in a recorded browser?

Written to settle "the zoom-out clips are juddery", where the suspect
was the output frame rate. It was not: the pulls deliver 28-50 changed
frames a second while the camera moves. The judder is the board, and
specifically the harvester.

    orbital drop   1108ms  — ~28 frames at Chromium's 25fps.  Reads fine.
    harvester step  185ms  — ~4.6 frames, AND `styles.css` spends them
                             as `steps(3, end)`, so it is three discrete
                             hops. Snappy at 60Hz on a live screen;
                             illegible in a wide, slow, recorded shot.

`--smooth N` proves the remedy without touching the game: it overrides
the hop with an N-millisecond linear glide, which the shoot can inject
the same way. 520 takes the step to ~540ms and 13 frames of real motion.

Two traps this hit, both worth keeping. `open_season` leaves the strip
at the END of the season, and autoplay from the last stop halts on its
first tick — which looks exactly like a replay that never animates, so
always scrub in first. And whole-frame luma differencing cannot see one
sprite move; measure the sprites, not the picture.

    python backstage/probes/_probe_replay_motion.py --smooth 520
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "films"))
# The setup is the shoot's own, imported rather than copied: a probe that
# opens the page even slightly differently is not measuring the shoot.
from make_zoomout_clips import open_season  # noqa: E402

# Poll the DOM faster than any animation can complete and record every
# ghost's lifetime. `replay-anim-ghost` is the sprite `runSingleDeltaAnimation`
# spawns for a step; the drop/probe arcs use the same layer.
_WATCH = """
() => {
  const seen = new Map();
  const log = [];
  window.__motion = { log, media: {
    reduce: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    nopref: window.matchMedia('(prefers-reduced-motion: no-preference)').matches,
  }};
  let id = 0;
  const tick = () => {
    const now = performance.now();
    const live = document.querySelectorAll(
      '[class*="replay-anim-ghost"], [class*="replay-anim"], .replay-fx');
    const here = new Set();
    live.forEach((el) => {
      if (!el.__mid) {
        el.__mid = ++id;
        seen.set(el.__mid, {born: now, el, path: []});
      }
      here.add(el.__mid);
    });
    // Track WHERE each sprite is, not just that it exists. The orbital
    // lifter's judder is a distance-per-frame problem: its arc is eased
    // to be fast at the edges, so a recorded frame can miss most of the
    // approach entirely.
    live.forEach((el) => {
      const r = el.getBoundingClientRect();
      const rec = seen.get(el.__mid);
      if (rec) rec.path.push([Math.round(now - rec.born),
                              Math.round(r.left), Math.round(r.top)]);
    });
    for (const [mid, rec] of seen) {
      if (!here.has(mid)) {
        log.push({ms: Math.round(now - rec.born), cls: rec.el.className,
                  path: rec.path});
        seen.delete(mid);
      }
    }
    window.__motion.raf = (window.__motion.raf || 0) + 1;
    window.__motion.peak = Math.max(window.__motion.peak || 0, live.length);
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);

  // Separate the two failure modes: a replay that never advanced looks
  // identical to one that advanced without animating.
  const c0 = document.querySelector('.cell[data-x]');
  window.__motion.cell = c0 ? c0.getBoundingClientRect().width : 0;

  const s = document.getElementById('replay-scrub');
  window.__motion.scrub = [];
  setInterval(() => window.__motion.scrub.push(Number(s.value)), 500);
}
"""


def _report_jumps(path, frame_ms: float, cell: float = 0.0) -> None:
    """How far does this sprite move between two RECORDED frames?

    rAF runs at ~120fps in the shoot's browser but the recorder only
    keeps 25, so smoothness is not "did it animate" — it is "how big is
    the gap the viewer's eye has to bridge". Anything past roughly a
    cell width per frame stops reading as motion and starts reading as
    teleporting.
    """
    if len(path) < 4:
        print("      (too few samples)")
        return
    # Drop the first sample. This watcher's rAF callback is registered
    # before the arc's, so on the sprite's birth frame it reads the node
    # at its unpositioned origin — a corner-to-arc "jump" that no viewer
    # ever sees, because the browser paints only after every callback.
    path = path[1:]
    # Resample the rAF trace onto the recorder's grid.
    grid, nxt = [], float(path[0][0])
    for ms, x, y in path:
        if ms >= nxt:
            grid.append((ms, x, y))
            nxt = ms + frame_ms
    if len(grid) < 2:
        print("      (crosses in under one recorded frame)")
        return
    jumps = [((grid[i][1] - grid[i - 1][1]) ** 2
              + (grid[i][2] - grid[i - 1][2]) ** 2) ** 0.5
             for i in range(1, len(grid))]
    per = (lambda j: f"{j / cell:4.1f} cells") if cell else (lambda j: "")
    print(f"      {len(grid)} recorded frames, biggest jump "
          f"{int(max(jumps))}px ({per(max(jumps))}), "
          f"median {int(sorted(jumps)[len(jumps) // 2])}px")
    scale = max(1.0, max(jumps) / 40.0)
    for i, j in enumerate(jumps):
        print(f"      f{i + 1:<2} {int(j):>5}px {per(j)} "
              f"|{'#' * max(1, int(j / scale))}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8023")
    # A file-backed season by default: Snowflake takes minutes to hand
    # the strip over and this question does not need it.
    ap.add_argument("--session", default="8eca04acc92a49c198e33659e817db33")
    ap.add_argument("--seat", default="p1")
    ap.add_argument("--stop", type=int, default=20,
                    help="scrub stop to start playback from")
    ap.add_argument("--frame-ms", type=float, default=40.0,
                    help="recorder frame interval (25fps = 40ms)")
    ap.add_argument("--watch-s", type=float, default=14.0)
    ap.add_argument("--smooth", type=int, default=0,
                    help="override the step hop with a glide of N ms")
    ap.add_argument("--video", action="store_true",
                    help="record video, as the real shoot does")
    a = ap.parse_args()

    with sync_playwright() as pw:
        br = pw.chromium.launch()
        kw = {"viewport": {"width": 1280, "height": 800}}
        if a.video:
            kw |= {"record_video_dir": "/tmp/probe_motion",
                   "record_video_size": {"width": 1280, "height": 800}}
        ctx = br.new_context(**kw)
        pg = ctx.new_page()
        open_season(pg, a.base, a.session, a.seat)

        # `open_season` leaves the strip at the END of the season. Play
        # from there and the loop stops on its first tick, which reads
        # exactly like "the replay does not animate".
        pg.evaluate(
            "(i) => { const s = document.getElementById('replay-scrub');"
            "  s.value = String(i);"
            "  s.dispatchEvent(new Event('input', {bubbles: true})); }",
            a.stop)
        pg.wait_for_timeout(1200)

        if a.smooth:
            # The game quantises a harvester step into three hops over
            # 160ms, which is snappy on a live screen and illegible at
            # 25fps zoomed out. Films override it; the game keeps it.
            pg.add_style_tag(content=(
                '.replay-anim-ghost--step {'
                f'  transition: transform {a.smooth}ms linear !important;'
                '}'))
        pg.evaluate(_WATCH)
        pg.evaluate("() => document.getElementById('replay-play').click()")
        pg.wait_for_timeout(int(a.watch_s * 1000))

        out = pg.evaluate("() => window.__motion")
        log = out.get("log") or []
        raf = out.get("raf") or 0
        print(json.dumps(out.get("media"), indent=2))
        print(f"\nrequestAnimationFrame ran {raf} times in {a.watch_s}s "
              f"-> {raf / a.watch_s:.1f} fps of animation clock")
        scrub = out.get("scrub") or []
        print(f"scrub walked {scrub[0] if scrub else '?'} -> "
              f"{scrub[-1] if scrub else '?'} "
              f"({len(set(scrub))} distinct stops in {a.watch_s}s)")
        print(f"animated sprites observed: {len(log)} "
              f"(peak {out.get('peak')} on screen at once)")
        for rec in log[:25]:
            kind = str(rec["cls"]).replace("replay-anim-ghost", "").strip()
            print(f"\n  {rec['ms']:>5}ms  {kind or 'ghost'}")
            _report_jumps(rec.get("path") or [], a.frame_ms,
                          float(out.get("cell") or 0))
        ctx.close()
        br.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
