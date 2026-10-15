"""v1.53: does the game actually boot the way it claims to?

The boot sequence is four cooperating pieces and none of them can report
its own breakage — a curtain that never lifts is a black page, a fog wave
whose delays are all zero is a board that just appears, and a station hull
stuck mid-teleport is a station that is simply not there. All four look
like "nothing happened" from Python.

So, in a real browser:

**The curtain** is up on arrival and gone a few seconds later. This is
the one with teeth: every lift is downstream of a fetch, so a branch that
forgets to call `_bootReady` strands the player on black.

**The fog** carries a per-cell `animation-delay` that grows with distance
from the middle of the board. Checked as a correlation across the whole
grid rather than against fixed numbers, so retuning the wave does not
break the probe.

**The hull** is per-span, and every span ends up at the seat's own colour
rather than the hot red it arrives in.

**Day one.** A finished season opens on its first night with no results
card in the way — the greeting it used to get was its own ending.

And `?boot=0` still puts you straight into the game, because that is what
every film and probe in this folder relies on.

    python backstage/probes/_probe_boot_animation.py --session <id>
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "reports" / "probes"


#: Every cell's boot delay against its distance from the centre of the
#: board. Read after the reveal has been scheduled but before it has
#: finished, which is the only window where the delays are on the DOM.
_CELL_DELAYS = """
() => {
  const host = document.getElementById('map-player')
    || document.querySelector('.cc-map-area .cc-map-body')
    || document.body;
  const cells = [...host.querySelectorAll('.cell[data-x]')];
  if (!cells.length) return null;
  let mx = 0, my = 0;
  for (const c of cells) {
    mx = Math.max(mx, Number(c.getAttribute('data-x')));
    my = Math.max(my, Number(c.getAttribute('data-y')));
  }
  const cx = mx / 2, cy = my / 2;
  const out = [];
  for (const c of cells) {
    const d = Number(c.style.animationDelay.replace('ms', '')) || 0;
    const dx = Number(c.getAttribute('data-x')) - cx;
    const dy = Number(c.getAttribute('data-y')) - cy;
    out.push([Math.hypot(dx, dy), d, c.classList.contains('cell--boot')]);
  }
  return out;
}
"""

#: What every hull pixel has settled to. `--warm` is the hot red it
#: passes through; nothing should still be wearing it at the end.
_HULL = """
() => {
  const out = {};
  for (const st of document.querySelectorAll('.os-station')) {
    const seat = st.getAttribute('data-os-station');
    const want = getComputedStyle(st).getPropertyValue('--os-color').trim();
    const px = [...st.querySelectorAll('.os-hull-px')];
    out[seat] = {
      n: px.length,
      want,
      warm: px.filter((p) => p.classList.contains('os-hull-px--warm')).length,
      solid: px.filter((p) => p.textContent === '\\u2588').length,
      colours: [...new Set(px.map((p) => getComputedStyle(p).color))],
      preboot: st.classList.contains('os-preboot'),
      booting: st.classList.contains('os-station--boot'),
    };
  }
  out.__rails = [...document.querySelectorAll('.os-catapult')]
    .filter((c) => c.classList.contains('os-preboot')).length;
  return out;
}
"""

#: Sample every seat's hull fill on every frame, so the relay can be
#: measured rather than eyeballed. Installed before the teleport runs;
#: it parks itself once every station is solid.
#:
#: The question it exists to answer is not "did they arrive" but "did
#: they arrive at DIFFERENT times". Four platforms materialising
#: together is the failure mode, and it looks fine in a screenshot.
_WATCH_RELAY = """
() => {
  window.__relay = [];
  // The spans are BUILT solid and merely hidden, so "everything is a
  // full block" is the state before the teleport as well as after it.
  // Sampling has to wait for the blanking that starts the sequence,
  // or the watcher parks on the starting line and reports a
  // simultaneous arrival that never happened.
  let started = false;
  const t0 = performance.now();
  const tick = () => {
    const row = { t: Math.round(performance.now()), seats: {} };
    let all = 0, n = 0;
    for (const st of document.querySelectorAll('.os-station')) {
      const seat = st.getAttribute('data-os-station');
      const px = [...st.querySelectorAll('.os-hull-px')];
      if (!px.length) continue;
      const solid = px.filter((p) => p.textContent === '\\u2588').length;
      // Two readings, because they answer different questions.
      //
      // `solid` is "has it finished". The overlap question needs "how
      // far in does it LOOK", and neither end of the glyph range
      // answers that: counting full blocks calls a hull 14% done when
      // it is plainly half materialised, and counting any non-blank
      // calls it 100% while most of it is still faint. So walk the
      // density ramp and take the mean — a hull of solid \\u2592 reads
      // as half arrived, which is what a viewer would say.
      const RAMP = ' \\u2591\\u2592\\u2593\\u2588';
      let fill = 0;
      for (const p of px) {
        const i = RAMP.indexOf(p.textContent);
        fill += (i < 0 ? 0 : i) / (RAMP.length - 1);
      }
      row.seats[seat] = fill / px.length;
      row[seat] = solid / px.length;
      n++;
      if (solid === px.length) all++; else started = true;
    }
    if (started && n) window.__relay.push(row);
    if ((!started || all < n) && performance.now() - t0 < 30000) {
      requestAnimationFrame(tick);
    }
  };
  requestAnimationFrame(tick);
}
"""

#: The 9x9 character grid the station sprites are anchored against, A/B'd
#: against the flat string the hull used to be.
#:
#: This is the one that would cost the most to get wrong and show the
#: least. `_osVaultAnchor`, `_osBlueStageOne` and `_osQueueAnim` all size
#: themselves as `wrapWidth / OS_DIA_SIZE`, so if wrapping each block in
#: a span shifted the advance width by even a fraction, every vault pip,
#: blue transfer and orbital arc in the game quietly lands off-centre —
#: on a 9-character grid, in a 76px box, by an amount no one would ever
#: attribute to a teleport animation.
#:
#: Measured in the page, on the live element, by swapping the spans for
#: the equivalent plain text and back. Same fonts, same box, same frame:
#: nothing else gives an answer worth trusting at this precision.
_METRICS = """
() => {
  const st = document.querySelector('.os-station');
  if (!st) return null;
  const wrap = st.querySelector('.os-diamond-wrap');
  const pre = st.querySelector('.os-diamond');
  const vault = st.querySelector('.os-vault-pre');
  if (!wrap || !pre || !vault) return null;

  const spans = pre.getBoundingClientRect();
  const wrapSpans = wrap.getBoundingClientRect().width;
  const keep = pre.innerHTML;
  pre.textContent = pre.textContent;   // collapse to the pre-v1.53 shape
  const plain = pre.getBoundingClientRect();
  const wrapPlain = wrap.getBoundingClientRect().width;
  pre.innerHTML = keep;

  const text = pre.textContent.split("\\n");
  return {
    rows: text.length,
    cols: text[0].length,
    hull: [spans.width, spans.height],
    plain: [plain.width, plain.height],
    wrap: [wrapSpans, wrapPlain],
    vault: [vault.getBoundingClientRect().width,
            vault.getBoundingClientRect().height],
  };
}
"""

_CURTAIN = """
() => {
  const el = document.getElementById('cc-boot');
  if (!el) return 'gone';
  if (el.hidden) return 'parked';
  return el.classList.contains('cc-boot--out') ? 'lifting' : 'up';
}
"""

#: Count full board repaints, stamped against the page clock.
#:
#: Every painter in app.js replaces the grid with one `innerHTML`
#: assignment, so a repaint is exactly "the `.map-grid` child of the map
#: host got swapped". That matters here because a repaint DURING the
#: reveal throws away every cell mid-animation — the wave does not
#: restart, it simply stops — and, worse, it means the board the player
#: watched spread is not the board they are left looking at.
#:
#: Installed at document start because the first paint is the one that
#: needs counting and it happens long before a probe could attach.
_WATCH_PAINTS = """
(() => {
  window.__paints = [];
  const attach = () => {
    const host = document.getElementById('map-player');
    if (!host) { requestAnimationFrame(attach); return; }
    new MutationObserver((recs) => {
      for (const r of recs) {
        for (const n of r.addedNodes) {
          if (n.nodeType === 1 && n.className
              && String(n.className).includes('map-grid')) {
            window.__paints.push(Math.round(performance.now()));
          }
        }
      }
    }).observe(host, { childList: true });
  };
  attach();
})();
"""


def pearson(xs: List[float], ys: List[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return num / (dx * dy) if dx and dy else 0.0


def complete_season(base: str) -> str:
    """Any finished season on this server — the day-1 check needs one."""
    with urllib.request.urlopen(f"{base}/api/sessions", timeout=60) as r:
        body = json.loads(r.read())
    for s in body.get("sessions", []):
        if str(s.get("kind") or "season") != "season":
            continue
        sid = s.get("session_id")
        try:
            with urllib.request.urlopen(
                    f"{base}/api/game/{sid}/status", timeout=60) as r:
                st = json.loads(r.read())
        except Exception:
            continue
        if st.get("is_season_complete"):
            return str(sid)
    raise SystemExit("no finished season on this server to open")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8023")
    ap.add_argument("--session", default="")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--strip", type=int, default=0, metavar="N",
                    help="also shoot N frames across the boot, 200ms apart, "
                         "into reports/probes/boot_strip/. The assertions "
                         "below can tell you the wave RAN; only frames can "
                         "tell you whether it looks like anything.")
    a = ap.parse_args()

    sid = a.session or complete_season(a.base)
    url = f"{a.base}/play?session={sid}&station=1"
    print(f"season {sid}")
    SHOTS.mkdir(parents=True, exist_ok=True)

    if a.strip:
        strip = SHOTS / "boot_strip"
        strip.mkdir(parents=True, exist_ok=True)
        for old in strip.glob("*.png"):
            old.unlink()
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=not a.headed)
            pg = br.new_context(
                viewport={"width": 1440, "height": 900}).new_page()
            pg.goto(url, wait_until="domcontentloaded")
            for i in range(a.strip):
                pg.screenshot(path=str(strip / f"{i:02d}.png"))
                pg.wait_for_timeout(200)
            br.close()
        print(f"  {a.strip} frames in {strip}")

    fails: List[str] = []
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=not a.headed)
        ctx = br.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_init_script(_WATCH_PAINTS)
        pg = ctx.new_page()
        errors: List[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))

        # ── the curtain is up before anything else is ───────────────
        pg.goto(url, wait_until="domcontentloaded")
        first = pg.evaluate(_CURTAIN)
        print(f"  curtain on arrival: {first}")
        if first != "up":
            fails.append(f"the curtain was {first!r} on arrival, not up — "
                         f"the player watched the session assemble")
        pg.screenshot(path=str(SHOTS / "boot_1_curtain.png"))

        # ── and nothing is poking through it ────────────────────────
        # The curtain is only worth having if it covers everything, and
        # "everything" is a moving target: the results reopen pill sits
        # at z-index 12000 and showed straight through the first cut of
        # this. Sampling the screen rather than auditing the stylesheet,
        # because the question is what a player sees.
        over = pg.evaluate(
            "() => { const b = document.getElementById('cc-boot');"
            "  if (!b) return [];"
            "  const w = innerWidth, h = innerHeight, out = new Set();"
            "  for (let x = 40; x < w; x += 80)"
            "    for (let y = 40; y < h; y += 80) {"
            "      const e = document.elementFromPoint(x, y);"
            "      if (e && !b.contains(e) && e !== b)"
            "        out.add(e.id || e.className || e.tagName);"
            "    }"
            "  return [...out]; }")
        if over:
            fails.append(f"these are on top of the boot curtain: {over}. "
                         f"The player is watching the session assemble "
                         f"through a hole in it")

        # ── the whole side column is held, not just the platforms ───
        # Sampled the instant the stations are BUILT, which is the only
        # window that answers the question. `_osInitPanels` runs during
        # the load and the teleport releases the hold on its way in, so
        # a check taken any later reads False whether the hold worked or
        # not — which is exactly the false pass this replaced.
        pg.wait_for_selector(".os-station", state="attached", timeout=120_000)
        held = pg.evaluate(
            "() => [...document.querySelectorAll('.os-station, .os-catapult')]"
            "  .map((e) => [e.className.split(' ')[0],"
            "    e.classList.contains('os-preboot'),"
            "    e.classList.contains('os-station--boot'),"
            "    getComputedStyle(e).visibility])")
        print(f"  side column as built: {held}")
        pg.evaluate(_WATCH_RELAY)
        if not held:
            fails.append("no stations or rails were ever built")
        else:
            # Two states count as held, because how far the sequence has
            # got by the time this line runs depends on how fast the
            # server answered — a memory-backed new game is already
            # teleporting here, a season off disk has not started.
            # `--boot` IS the arrival: furniture hidden, pixels ramping.
            # What this is looking for is the third state, a station
            # standing there complete having never teleported at all.
            loose = [h[0] for h in held
                     if not (h[1] and h[3] == "hidden") and not h[2]]
            if loose:
                fails.append(f"{loose} were not held for the boot — they are "
                             f"on screen, complete, before the teleport that "
                             f"is supposed to bring them in")

        # ── the fog is staggered from the middle ────────────────────
        # Sampled as soon as the board exists rather than after the
        # reveal: app.js strips the delays once the wave lands.
        pg.wait_for_function(
            "() => document.querySelectorAll('.cell[data-x]').length > 50",
            timeout=120_000)
        rows = None
        for _ in range(80):
            got = pg.evaluate(_CELL_DELAYS)
            if got and any(r[2] for r in got):
                rows = got
                break
            pg.wait_for_timeout(50)
        if not rows:
            fails.append("no cell ever carried `cell--boot` — the fog wave "
                         "never ran, so the board just appeared")
        else:
            dist = [r[0] for r in rows]
            delay = [r[1] for r in rows]
            r = pearson(dist, delay)
            print(f"  fog: {len(rows)} cells, delay 0-{max(delay):.0f}ms, "
                  f"r={r:.3f} against distance from centre")
            if r < 0.8:
                fails.append(f"cell delays barely track distance (r={r:.2f}) "
                             f"— that is not a wave, it is noise")
            if max(delay) < 200:
                fails.append(f"the whole wave fits in {max(delay):.0f}ms — "
                             f"nobody will see it")
        pg.screenshot(path=str(SHOTS / "boot_2_fog.png"))

        # ── the curtain lifts ───────────────────────────────────────
        try:
            pg.wait_for_function(
                "() => { const e = document.getElementById('cc-boot');"
                "  return !e || e.hidden; }", timeout=40_000)
            print("  curtain lifted")
        except Exception:
            fails.append("the curtain never lifted — the page is a black "
                         "rectangle and there is no way out of it")

        # ── nothing repaints the board out from under the reveal ────
        wave_at = pg.evaluate(
            "() => { const c = document.querySelector('.cell--boot');"
            "  return c ? Math.round(performance.now()) : null; }")
        pg.wait_for_timeout(3500)
        paints = pg.evaluate("() => window.__paints || []")
        print(f"  board paints at {paints}ms; wave seen at {wave_at}ms")
        if wave_at is not None:
            during = [p for p in paints if p > wave_at]
            if during:
                fails.append(
                    f"the board was repainted {len(during)} time(s) at "
                    f"{during}ms, after the reveal started at {wave_at}ms. "
                    f"A repaint replaces every cell mid-animation, so the "
                    f"wave the player watched spread is not the board they "
                    f"are left with")

        # ── the hull teleports and cools to the seat colour ─────────
        hull: Dict[str, dict] = pg.evaluate(_HULL) or {}
        rails = hull.pop("__rails", 0)
        if rails:
            fails.append(f"{rails} catapult rail(s) never came back from the "
                         f"boot hold — the side column is half a station")
        if not hull:
            fails.append("no stations on the page — cannot check the hull")
        for seat, h in sorted(hull.items()):
            print(f"  {seat}: {h['n']} hull px, {h['solid']} solid, "
                  f"{h['warm']} still hot, colours {h['colours']}")
            if h["n"] < 20:
                fails.append(f"{seat} hull is {h['n']} spans — the diamond "
                             f"did not render per-pixel")
            if h["preboot"] or h["booting"]:
                fails.append(f"{seat} is still mid-teleport after 3.5s — it "
                             f"is invisible and staying that way")
            if h["warm"]:
                fails.append(f"{seat} left {h['warm']} pixels hot — they "
                             f"never cooled to the seat colour")
            if h["solid"] != h["n"]:
                fails.append(f"{seat} settled with {h['n'] - h['solid']} "
                             f"pixels off the full block glyph")
            if len(h["colours"]) != 1:
                fails.append(f"{seat} hull ended up {len(h['colours'])} "
                             f"different colours: {h['colours']}")
        pg.screenshot(path=str(SHOTS / "boot_3_settled.png"))

        # ── the platforms arrive one after another, overlapping ─────
        relay = pg.evaluate("() => window.__relay || []")
        done: Dict[str, int] = {}
        for row in relay:
            for seat in row["seats"]:
                if row.get(seat, 0) >= 1 and seat not in done:
                    done[seat] = row["t"]
        if len(done) < 2:
            print(f"  relay: only {len(done)} platform(s) — nothing to stagger")
        else:
            first = min(done.values())
            # Where everyone else was at the moment the first one landed.
            at_first = next(
                (r["seats"] for r in relay if r["t"] >= first), {})
            order = sorted(done.items(), key=lambda kv: kv[1])
            ladder = ", ".join(
                f"{s} lands +{t - first}ms, {at_first.get(s, 0):.0%} "
                f"visible when the first did"
                for s, t in order)
            spread = max(done.values()) - first
            print(f"  relay: {ladder} · spread {spread}ms")

            # The fill curve of the FIRST platform, printed so the seat
            # gap can be picked off a measurement. It is nothing like
            # linear — the front crosses the hull well before the
            # pixels behind it finish ramping — so guessing "half the
            # run" from the constants lands nowhere near half filled.
            lead = order[0][0]
            t0 = next((r["t"] for r in relay if r["seats"].get(lead, 1) < 1),
                      relay[0]["t"])
            curve = []
            for r in relay:
                age = r["t"] - t0
                if age >= 0 and (not curve or age - curve[-1][0] >= 120):
                    curve.append((age, r["seats"].get(lead, 0)))
                if r["t"] >= done[lead]:
                    break
            print("  lead fill: " + "  ".join(
                f"{a}ms {f:.0%}" for a, f in curve))
            if spread < 150:
                fails.append(
                    f"the platforms all arrived within {spread}ms of each "
                    f"other — that is a simultaneous pop, not a relay "
                    f"({ladder})")
            # A band, not `0 < f < 1`. The point of the relay is that
            # the platform behind is VISIBLY mid-arrival when the one
            # in front lands — 98% filled technically passes a bare
            # inequality and looks simultaneous.
            mid = [s for s, f in at_first.items() if 0.15 < f < 0.75]
            if not mid:
                fails.append(
                    f"when the first platform landed, no other was visibly "
                    f"part-way in: "
                    f"{ {s: round(f, 2) for s, f in at_first.items()} }. "
                    f"They are arriving together or queueing, not overlapping")

        # ── the 9x9 grid the sprites are anchored to is unchanged ───
        m = pg.evaluate(_METRICS)
        if not m:
            fails.append("could not measure the hull — no station to read")
        else:
            dw = abs(m["hull"][0] - m["plain"][0])
            dh = abs(m["hull"][1] - m["plain"][1])
            dwrap = abs(m["wrap"][0] - m["wrap"][1])
            print(f"  metrics: {m['rows']}x{m['cols']} chars · "
                  f"hull {m['hull'][0]:.3f}x{m['hull'][1]:.3f} vs flat "
                  f"{m['plain'][0]:.3f}x{m['plain'][1]:.3f} "
                  f"(d {dw:.3f}/{dh:.3f}) · wrap d {dwrap:.3f}")
            if (m["rows"], m["cols"]) != (9, 9):
                fails.append(f"the hull is {m['rows']}x{m['cols']} characters, "
                             f"not 9x9 — OS_DIA_SIZE and the sprite anchors "
                             f"disagree about the shape of a station")
            # A quarter-pixel, and the gap between the two things this
            # can find. Splitting a text run into spans costs a few
            # hundredths of a pixel in shaping rounding — measured at
            # 0.078px across the whole 9-character row, which is 0.009px
            # per cell and cannot be seen or accumulated into anything.
            # A real break — a stray letter-spacing, a padding, an
            # inline-block — moves it by a whole character, ~8.4px here.
            # Nothing lands in between, so the threshold is not delicate.
            if dw > 0.25 or dh > 0.25 or dwrap > 0.25:
                fails.append(
                    f"per-span hull measures {dw:.3f}x{dh:.3f}px differently "
                    f"from the flat string it replaced (wrap {dwrap:.3f}px). "
                    f"Every station sprite is positioned as "
                    f"wrapWidth/OS_DIA_SIZE, so they are all now off by a "
                    f"fraction of a cell")

        # ── a finished season opens on its first night ──────────────
        at = pg.evaluate(
            "() => { const s = document.getElementById('replay-scrub');"
            "  return s ? {v: Number(s.value), max: Number(s.max),"
            "    off: s.disabled} : null; }")
        endgame = pg.evaluate(
            "() => { const e = document.getElementById('endgame-overlay')"
            "   || document.querySelector('.cc-endgame');"
            "  return !!(e && !e.hidden); }")
        print(f"  scrubber at {at} · results card open: {endgame}")
        if at and not at["off"] and at["v"] != 0:
            fails.append(f"a finished season opened at stop {at['v']} of "
                         f"{at['max']}, not at the beginning")
        if endgame:
            fails.append("the results card popped on open — it announces "
                         "the ending before showing the beginning")

        # ── ?boot=0 is a straight line into the game ────────────────
        pg.goto(f"{url}&boot=0", wait_until="domcontentloaded")
        off = pg.evaluate(_CURTAIN)
        print(f"  with ?boot=0 the curtain is: {off}")
        if off == "up":
            fails.append("?boot=0 still raised the curtain — every film and "
                         "probe in backstage/ depends on it not doing that")

        if errors:
            fails.append(f"page errors: {errors[:3]}")
        br.close()

    print(f"\nshots in {SHOTS}")
    if fails:
        print(f"\nFAIL ({len(fails)})")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
