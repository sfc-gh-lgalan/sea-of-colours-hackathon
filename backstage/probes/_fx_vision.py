"""Render the vision-border layer against hand-built boards and shoot it.

The awkward cases for a vision outline are the ones a real season only
coughs up occasionally: two seats whose sight overlaps, a disk clipped by
the board edge, a hole punched in your own vision, and the moment a rival's
inferred footprint runs along your own hard edge. So this drives the
painter directly through ``window.__SOC_VISION_DBG__`` with boards built to
order, rather than waiting for a game to produce them.

Also shoots the rich cell tooltip on a RED square and on an occupied one.

Scratch harness, like the other ``backstage/probes/_fx_*.py`` — not part of pytest.

    python backstage/probes/_fx_vision.py [base_url]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8021"
# A session has to be loaded: the bare URL is the landing page, which never
# builds a board (and so never installs the debug hook).
SESSION = sys.argv[2] if len(sys.argv) > 2 else "87ad9645795e405389f854d4b96e57a6"
OUT = Path("reports/vision")

W, H = 40, 28

# Seat palette, mirroring session.py SEAT_DEFAULT_COLORS.
P1, P2 = "#FFFFFF", "#FCF871"

BUILD_BOARD = """
([w, h, spec]) => {
  const VOID = 'rgb(26,19,34)';
  const cells = [];
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      cells.push({ kind: 'fog' });
    }
  }
  const put = (x, y, cell) => {
    if (x < 0 || y < 0 || x >= w || y >= h) return;
    cells[y * w + x] = cell;
  };
  // Terrain under a live cell: cycle the tiers so the border is tested
  // against bright pure RED, dark EMPTY and solid GREEN alike.
  const terrain = (x, y, stale) => {
    const n = (x * 7 + y * 13) % 11;
    let c;
    if (n === 0) c = { ch: '  ', bg: 'rgb(255,0,0)', tile: 'RED', purity: 255, tier: 'pure' };
    else if (n < 3) c = { ch: '\\u2593\\u2593', fg: 'rgb(255,0,0)', bg: VOID, tile: 'RED', purity: 151 + n * 30, tier: 'mass' };
    else if (n < 5) c = { ch: '\\u2592\\u2592', fg: 'rgb(255,0,0)', bg: VOID, tile: 'RED', purity: 60 + n * 10, tier: 'vein' };
    else if (n < 7) c = { ch: '  ', bg: 'rgb(63,185,80)', tile: 'GREEN', purity: 255 };
    else if (n < 9) c = { ch: '\\u2592\\u2592', fg: 'rgb(59,143,224)', bg: VOID, tile: 'BLUE', purity: 90, tier: 'mid' };
    else c = { ch: '\\u2588\\u2588', fg: VOID, bg: VOID, tile: 'EMPTY', purity: 0 };
    return Object.assign({ kind: 'terrain', stale: !!stale, echo_probe: false }, c);
  };
  for (const [x, y] of spec.live) put(x, y, terrain(x, y, false));
  for (const [x, y] of spec.echo) put(x, y, terrain(x, y, true));
  for (const p of spec.probes) {
    const base = cells[p.y * w + p.x];
    const cell = (base && base.kind === 'terrain')
      ? base
      : { kind: 'fog' };
    cell.entity = { ch: '\\u00B7', fg: p.colour, nights_remaining: 2 };
    cell.occupants = [{
      id: p.id, type: 'probe', owner: p.owner,
      label: p.id, nights_remaining: 2,
    }];
    put(p.x, p.y, cell);
  }
  for (const hv of (spec.harvesters || [])) {
    const cell = cells[hv.y * w + hv.x] || { kind: 'fog' };
    cell.entity = { ch: 'X', fg: hv.colour, carrying: true };
    cell.occupants = [{ id: hv.id, type: 'harvester', owner: hv.owner, label: hv.id }];
    put(hv.x, hv.y, cell);
  }
  window.__SOC_VISION_DBG__.paintBoard(w, h, cells);
  return window.__SOC_VISION_DBG__.bands();
}
"""


def disk(cx: int, cy: int, r: int) -> list[list[int]]:
    """Euclidean disk, matching _euclidean_disk in the engine."""
    return [
        [x, y]
        for y in range(max(0, cy - r), min(H, cy + r + 1))
        for x in range(max(0, cx - r), min(W, cx + r + 1))
        if (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    ]


def intersect(a, b) -> list[list[int]]:
    """Cells in both sets — the clip a rival band is now subject to."""
    keep = {(x, y) for x, y in b}
    return [[x, y] for x, y in a if (x, y) in keep]


def union(*groups) -> list[list[int]]:
    seen = {}
    for g in groups:
        for xy in g:
            seen[(xy[0], xy[1])] = xy
    return list(seen.values())


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    # Own vision: two probe disks plus a harvester's plus-shape, with one
    # cell punched out to force an interior ring (the hole case).
    own = union(disk(11, 9, 4), disk(16, 12, 4), disk(28, 18, 1))
    own = [c for c in own if tuple(c) != (11, 9)]

    # v1.22 — a rival band is now CLIPPED to your own live set, so every
    # scene has to say what it expects that intersection to be. `rival`
    # names the p2 cells that should survive the clip; an empty set means
    # the band must not be drawn at all.
    corner_own = disk(4, 3, 4)
    # Two patches of your own sight with a one-column fog gap between them,
    # so a rival disk laid across the gap has to break into two islands.
    split_own = [
        c for c in union(disk(20, 14, 5), disk(30, 14, 5)) if c[0] != 25
    ]

    scenes = {
        # The headline: your own vision, and a rival's footprint overlapping
        # it, so both edges must survive the crossing.
        "overlap": {
            "live": own,
            "echo": disk(30, 8, 3),
            "probes": [
                {"x": 11, "y": 9, "owner": "p1", "id": "probe_p1_1", "colour": P1},
                {"x": 16, "y": 12, "owner": "p1", "id": "probe_p1_2", "colour": P1},
                {"x": 18, "y": 10, "owner": "p2", "id": "probe_p2_7", "colour": P2},
            ],
            "harvesters": [
                {"x": 28, "y": 18, "owner": "p1", "id": "harvester_p1", "colour": P1},
            ],
            "rival": intersect(disk(18, 10, 4), own),
        },
        # A rival probe you cannot see draws NOTHING, even though its launch
        # is public. Two far-flung p2 probes sit on fog; only the one inside
        # your own sight survives, and it is against the board corner so the
        # clip has to close cleanly against two edges at once.
        "outside": {
            "live": corner_own,
            "echo": [],
            "probes": [
                {"x": 4, "y": 3, "owner": "p1", "id": "probe_p1_1", "colour": P1},
                {"x": 2, "y": 2, "owner": "p2", "id": "probe_p2_1", "colour": P2},
                {"x": 1, "y": 25, "owner": "p2", "id": "probe_p2_far1", "colour": P2},
                {"x": 38, "y": 1, "owner": "p2", "id": "probe_p2_far2", "colour": P2},
            ],
            "harvesters": [],
            # The far probes land on fog, so they are not even visible to be
            # inferred from; only (2,2)'s disk contributes.
            "rival": intersect(disk(2, 2, 4), corner_own),
        },
        # Your own vision is two patches with a fog gap, and a rival probe
        # you CAN see sits in one of them with a disk reaching across into
        # the other: clipping breaks their band into two disjoint islands,
        # so one seat's path must carry more than one ring.
        "split": {
            "live": split_own,
            "echo": [],
            "probes": [
                {"x": 20, "y": 14, "owner": "p1", "id": "probe_p1_1", "colour": P1},
                {"x": 30, "y": 14, "owner": "p1", "id": "probe_p1_2", "colour": P1},
                {"x": 23, "y": 14, "owner": "p2", "id": "probe_p2_1", "colour": P2},
            ],
            "harvesters": [],
            "rival": intersect(disk(23, 14, 4), split_own),
            "min_rings": 2,
        },
    }

    with sync_playwright() as p:
        br = p.chromium.launch(channel="chrome")
        pg = br.new_page(viewport={"width": 1500, "height": 1000})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(f"{BASE}/?session={SESSION}", wait_until="networkidle")
        pg.wait_for_timeout(2500)

        if not pg.evaluate("() => Boolean(window.__SOC_VISION_DBG__)"):
            print("FAIL: no __SOC_VISION_DBG__ hook — is this the new app.js?")
            br.close()
            return 1

        # Probe radius the harness assumes; the app reads it from meta.rules.
        pg.evaluate("() => { window.__SOC_PROBE_RADIUS__ = 4; }")

        for name, spec in scenes.items():
            bands = pg.evaluate(BUILD_BOARD, [W, H, spec])
            pg.wait_for_timeout(350)
            print(f"\n[{name}] bands: {json.dumps(bands)}")

            # v1.22 — the rival band must be exactly its disk ∩ your live
            # set. Counting cells rather than eyeballing the shot is what
            # catches a clip that is off by a row, or one that quietly
            # stopped clipping at all.
            want = len(spec["rival"])
            rival = [b for b in bands if b["inferred"]]
            got = sum(b["cells"] for b in rival)
            ok = got == want and (want == 0) == (not rival)
            print(f"[{name}] rival clipped to own live: {got} cells (want {want}) {'ok' if ok else 'FAIL'}")
            if not ok:
                errors.append(
                    f"{name}: rival band is {got} cells over {len(rival)} band(s), "
                    f"expected {want} — the clip to own live vision is wrong"
                )
            for b in bands:
                if not b["inferred"] and b["seat"] != "p1":
                    errors.append(f"{name}: unexpected solid band for {b['seat']}")

            info = pg.evaluate(
                """() => {
                  const svg = document.querySelector('.vision-border-layer');
                  if (!svg) return { svg: false };
                  const paths = Array.from(svg.querySelectorAll('path'));
                  const grid = document.querySelector('#map-player .map-grid');
                  const gr = grid.getBoundingClientRect();
                  const sr = svg.getBoundingClientRect();
                  return {
                    svg: true,
                    viewBox: svg.getAttribute('viewBox'),
                    paths: paths.length,
                    strokes: Array.from(new Set(paths.map((p) => p.getAttribute('stroke')))),
                    // The layer must sit exactly on the grid or every line is
                    // half a cell out — the classic failure for this feature.
                    dx: Math.round((sr.left - gr.left) * 100) / 100,
                    dy: Math.round((sr.top - gr.top) * 100) / 100,
                    dw: Math.round((sr.width - gr.width) * 100) / 100,
                    dh: Math.round((sr.height - gr.height) * 100) / 100,
                    lens: paths.map((p) => Math.round(p.getTotalLength())),
                    // One subpath per closed ring, so this counts the
                    // islands a clipped band broke into.
                    rings: paths.map((p) => (p.getAttribute('d').match(/M/g) || []).length),
                    dashes: paths.map((p) => getComputedStyle(p).strokeDasharray),
                    widths: paths.map((p) => getComputedStyle(p).strokeWidth),
                  };
                }"""
            )
            print(f"[{name}] layer: {json.dumps(info)}")
            if info.get("svg"):
                aligned = all(abs(info[k]) < 0.6 for k in ("dx", "dy", "dw", "dh"))
                print(f"[{name}] aligned to grid: {aligned}")
                if not aligned:
                    errors.append(f"{name}: border layer is off the grid — {info}")
                # One path per band since the halo was dropped in v1.22 — if
                # it ever comes back this is the line that will say so.
                if info["paths"] != len(bands):
                    errors.append(
                        f"{name}: {info['paths']} paths for {len(bands)} bands "
                        f"(expected one each)"
                    )
                want_rings = spec.get("min_rings")
                if want_rings and max(info["rings"] or [0]) < want_rings:
                    errors.append(
                        f"{name}: no band drew {want_rings}+ rings ({info['rings']}) "
                        f"— a clip that splits a disk must emit one ring per island"
                    )
            else:
                errors.append(f"{name}: no border layer painted")

            host = pg.query_selector(".cc-map-viewport")
            if host:
                host.screenshot(path=str(OUT / f"vision_{name}.png"))

        # ── Tooltip: a RED square, then one with a rival probe on it ──
        pg.evaluate(
            """() => {
              window.__SOC_QUALITY_MULT__ = { trace: 0.75, vein: 1.0, mass: 1.5, pure: 3.0 };
              window.__SOC_GREEN_PENALTY__ = 100;
            }"""
        )
        tips = pg.evaluate(
            """() => {
              const D = window.__SOC_VISION_DBG__;
              const red = { kind: 'terrain', stale: false, ch: '\\u2593\\u2593',
                fg: 'rgb(255,0,0)', bg: 'rgb(26,19,34)',
                tile: 'RED', purity: 191, tier: 'mass' };
              const pure = { kind: 'terrain', stale: false, ch: '  ',
                bg: 'rgb(255,0,0)', tile: 'RED', purity: 255, tier: 'pure' };
              const green = { kind: 'terrain', stale: false, ch: '  ',
                bg: 'rgb(63,185,80)', tile: 'GREEN', purity: 255 };
              const occupied = Object.assign({}, red, {
                stale: true, echo_probe: true,
                entity: { ch: '\\u00B7', fg: '#FCF871', nights_remaining: 2 },
                occupants: [{ id: 'probe_p2_15', type: 'probe', owner: 'p2',
                              label: 'probe_p2_15', nights_remaining: 2 }],
              });
              const legacy = { kind: 'terrain', stale: false, ch: '\\u2592\\u2592',
                fg: 'rgb(59,143,224)', bg: 'rgb(26,19,34)' };
              // A pre-v1.22 RED tile: no purity in the payload, so the
              // score has to be estimated from the dither band.
              const legacyRed = { kind: 'terrain', stale: false, ch: '\\u2593\\u2593',
                fg: 'rgb(255,0,0)', bg: 'rgb(26,19,34)' };
              return {
                red: D.tip(red, 21, 12),
                pure: D.tip(pure, 9, 4),
                green: D.tip(green, 3, 3),
                occupied: D.tip(occupied, 21, 12),
                legacy: D.tip(legacy, 5, 5),
                legacy_red: D.tip(legacyRed, 6, 6),
              };
            }"""
        )
        for key, html in tips.items():
            plain = (
                html.replace("</div>", " | ")
                .replace("<", "\n<")
            )
            import re as _re
            text = _re.sub(r"<[^>]+>", "", plain)
            text = " ".join(text.split())
            print(f"\n[tip:{key}] {text}")

        # Shoot the tooltip card itself so the layout can be eyeballed.
        for key in ("red", "occupied"):
            pg.evaluate(
                """(html) => {
                  const t = document.querySelector('.cell-tooltip');
                  t.innerHTML = html;
                  t.hidden = false;
                  t.style.left = '40px';
                  t.style.top = '40px';
                }""",
                tips[key],
            )
            pg.wait_for_timeout(250)
            el = pg.query_selector(".cell-tooltip")
            if el:
                el.screenshot(path=str(OUT / f"tip_{key}.png"))

        # Sanity: the score line must show purity x multiplier, not a band.
        if "287" not in tips["red"]:
            errors.append("RED 191 x1.5 should bank 287; tooltip says otherwise")
        if "765" not in tips["pure"]:
            errors.append("RED 255 x3.0 should bank 765; tooltip says otherwise")
        if "51\u2013150" not in tips["legacy"]:
            errors.append("legacy payload should still fall back to the band")
        # v1.23 — a payload with no purity used to print no score at all,
        # which silenced every dithered tile on a pre-v1.22 server. mass is
        # 151–254 at x1.5, so the band prices at 227–381.
        if "227\u2013381" not in tips["legacy_red"]:
            errors.append(
                "a RED tile with no purity must still price as a band estimate; "
                f"got: {tips['legacy_red']}"
            )

        br.close()

    print("\npage errors:", errors if errors else "none")
    print(f"shots -> {OUT}/")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
