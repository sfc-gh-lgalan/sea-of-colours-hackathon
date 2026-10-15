"""Does a SNAP launch actually play in the replay?

Written for #62: on `veritas_herald` (30e11d20) day 6, p4 fires a SNAP
at (34,4), the log says so, the frame is tagged `snap_launch` — and the
replay showed nothing, because the Snowflake schema had no column for
the payload the FX is gated on.

Drives the real page rather than reading JSON, because the JSON was
never the thing that was wrong: the frame was always there. What was
missing was a dart on screen.

    PLAYWRIGHT_BROWSERS_PATH=~/Library/Caches/ms-playwright \
      python backstage/probes/_probe_snap_replay.py --base http://127.0.0.1:8022
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# The film rig already owns "open a Snowflake season and wait until the
# strip is genuinely ready", which is four minutes of patience and two
# non-obvious steps. Borrowing it beats a second, worse copy.
from backstage.films.make_season_clips import open_replay  # noqa: E402

SHOTS = Path(__file__).resolve().parents[2] / "reports" / "probes"

#: Everything `playSnapFx` puts on screen. The dart goes through
#: `spawnProbeTrail`, which paints into an unclassed full-screen
#: <canvas> pinned to the body, so it is counted by shape like the
#: probe streak is. The strike and the scorch that follows it share
#: `.snap-cloud-cell` — the strike IS the mark, arriving hot.
#: All three are transient, so this is sampled in a loop.
_FX = """
() => ({
  darts: [...document.body.children].filter(
    (e) => e.tagName === 'CANVAS').length,
  strikes: document.querySelectorAll('.snap-cloud-cell--strike').length,
  scorch: document.querySelectorAll('.snap-cloud-cell').length,
})
"""

_SCRUB = """
(to) => {
  for (const id of ['cc-endgame', 'cc-victor']) {
    const el = document.getElementById(id);
    if (el) el.remove();
  }
  const s = document.getElementById('replay-scrub');
  if (!s || s.disabled) return null;
  s.value = String(to);
  s.dispatchEvent(new Event('input', { bubbles: true }));
  s.dispatchEvent(new Event('change', { bubbles: true }));
  return { at: Number(s.value), max: Number(s.max) };
}
"""

#: #replay-play by id, not by matching button text. The handler is a
#: TOGGLE, so a text match that happens to find two candidates — or
#: finds the right one twice — starts playback and immediately stops
#: it, which looks exactly like "the animation never fired".
_PLAY = """
() => {
  const b = document.getElementById('replay-play');
  if (b) b.click();
  return Boolean(b);
}
"""

_AT = """
() => {
  const s = document.getElementById('replay-scrub');
  return s ? Number(s.value) : -1;
}
"""

#: A frame index out of `/replay` is NOT a scrub stop — the strip
#: collapses empty hours, so the two counts differ. The film rig hit
#: this first and settled on matching the CAPTION, which is the one
#: thing both sides agree on; same trick here.
_WALK = """
async () => {
  const s = document.getElementById('replay-scrub');
  const cap = document.getElementById('replay-now-playing');
  if (!s || s.disabled) return null;
  const max = Number(s.max) || 0;
  const out = [];
  for (let i = 0; i <= max; i++) {
    s.value = String(i);
    s.dispatchEvent(new Event('input', {bubbles: true}));
    await new Promise((r) => setTimeout(r, 12));
    out.push((cap && cap.textContent || '').replace(/\\s+/g, ' ').trim());
  }
  return out;
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8022")
    ap.add_argument("--session", default="30e11d20d9864bfa9a367d45a222facd")
    ap.add_argument("--day", type=int, default=6)
    a = ap.parse_args()

    SHOTS.mkdir(parents=True, exist_ok=True)
    fails: list[str] = []

    with urllib.request.urlopen(
        f"{a.base}/api/game/{a.session}/replay"
        f"?day_from={a.day}&day_to={a.day}", timeout=600,
    ) as r:
        frames = (json.loads(r.read().decode()).get("frames") or [])

    shots = [(i, f) for i, f in enumerate(frames)
             if f.get("tag") == "snap_launch"]
    print(f"day {a.day}: {len(frames)} frames, {len(shots)} snap_launch")
    if not shots:
        print("  nothing to check on this day")
        return 0
    for i, f in shots:
        has = bool(f.get("snap"))
        print(f"  frame {i}: {f.get('caption')!r}")
        print(f"    payload on the wire: {'yes' if has else 'NO (archived '
              'before the column existed — the client must rebuild it)'}")
        if not has and "(" not in str(f.get("attempted") or f.get("caption")):
            fails.append(
                f"frame {i} has no `snap` payload AND no target in its "
                f"caption or `attempted`, so there is nothing to rebuild "
                f"from. This strike is unrecoverable")

    want = str(shots[0][1].get("caption") or "")
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(viewport={"width": 1600, "height": 1000})
        caps = open_replay(pg, a.base, a.session, "p4")
        # Matched on "SNAP" plus the coordinate rather than the whole
        # caption: the strip rewords and truncates what the frame
        # stored, but the weapon and the square it hit survive both.
        coord = want.replace(" ", "")
        coord = coord[coord.find("("):coord.find(")") + 1] if "(" in coord else ""
        idx = next((i for i, c in enumerate(caps)
                    if "snap" in c.lower()
                    and coord and coord in c.replace(" ", "")), -1)
        print(f"  {len(caps)} scrub stops; the strike on {coord} is stop {idx}")
        if idx < 0:
            hits = [(i, c) for i, c in enumerate(caps) if "snap" in c.lower()]
            print(f"  (no stop names it. {len(hits)} mention SNAP at all"
                  + (f", e.g. {hits[0]}" if hits else "") + ")")
            br.close()
            return 1

        # `open_replay` leaves the board in OBS. Check the FIRING
        # SEAT's own view too: `playSnapFx` resolves its target through
        # `mapPlayer`, and if the observer paints somewhere else the
        # weapon would be silent in exactly the view a season is
        # watched in — which is where this was reported from.
        for view in ("p4", "obs"):
            pg.click(f"#replay-view-{view}")
            pg.wait_for_timeout(600)
            host = pg.evaluate(
                "() => { const vis = (m) => m && m.getBoundingClientRect().width > 0;"
                "  const p = document.getElementById('map-player');"
                "  const o = document.getElementById('map-observer');"
                "  return (vis(p) && 'map-player') || (vis(o) && 'map-observer')"
                "    || 'neither'; }")
            cell = pg.evaluate(
                "(h) => Boolean(document.querySelector("
                "  `#${h} .cell[data-x=\"34\"][data-y=\"4\"]`))", host)
            print(f"  view {view:4s}: board paints into #{host}, "
                  f"target cell present: {cell}")
        pg.click("#replay-view-obs")
        pg.wait_for_timeout(600)

        # Land a few stops short and PRESS PLAY, rather than dragging
        # the slider onto the frame. The FX is forward-playback only by
        # design — a scrub deliberately re-fires nothing, or dragging
        # the strip would set off every weapon in the night — so the
        # only way to ask "does a viewer see this" is to watch it the
        # way a viewer does.
        pg.evaluate(_SCRUB, max(0, idx - 3))
        pg.wait_for_timeout(1500)

        # Baselined, not counted. The board keeps canvases around for
        # other effects, so an absolute dart count answers "is there a
        # canvas on this page", which is always yes. Only the RISE over
        # the frame before means a missile was launched.
        before = pg.evaluate(_FX)
        started = pg.evaluate(_AT)
        pressed = pg.evaluate(_PLAY)
        peak = dict(before)
        shot_at = None
        # Long enough to cover three stops of playback plus the dart's
        # own flight (540ms / missile speed) and the strike after it.
        for _ in range(140):
            now = pg.evaluate(_FX)
            for k, v in now.items():
                peak[k] = max(peak.get(k, 0), v)
            if now["scorch"] and shot_at is None:
                shot_at = str(SHOTS / "snap_replay_strike.png")
                pg.screenshot(path=shot_at)
            if pg.evaluate(_AT) > idx + 1:
                break
            pg.wait_for_timeout(50)

        print(f"  play button found: {pressed}; walked stop {started} -> "
              f"{pg.evaluate(_AT)} (target {idx})")
        print(f"  on the frame before: {before}")
        print(f"  peak during the launch: {peak}")
        flew = peak["darts"] > before["darts"]
        # Asserted on `.snap-cloud-cell`, not on the `--strike` variant.
        # The strike is the same element mid-animation and it hands the
        # class off within a couple of frames, so a 50ms sampler misses
        # it more often than not — and the question here is whether the
        # square ends up marked, which is what `scorch` answers.
        landed = peak["scorch"] > before["scorch"]
        print(f"  a missile flew: {flew} · the square was marked: {landed}"
              + (f" ({shot_at})" if shot_at else ""))
        if not flew:
            fails.append(
                "playing through the snap_launch frame put no missile in "
                "the air. p4 fired a SNAP at the cell named in the "
                "caption and the replay stayed silent — which is #62")
        if not landed:
            fails.append(
                "a missile flew but nothing marked the square it hit. "
                "The impact tile and the scorch are one element, so an "
                "overlay repaint with no cloud behind it wipes the "
                "strike a frame after it lands")
        pg.wait_for_timeout(900)
        pg.screenshot(path=str(SHOTS / "snap_replay_after.png"))
        br.close()

    print(f"\nshots in {SHOTS}")
    for f in fails:
        print(f"  - {f}")
    print("FAIL" if fails else "PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
