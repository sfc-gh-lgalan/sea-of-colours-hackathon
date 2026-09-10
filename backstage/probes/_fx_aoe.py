"""Verify the order-footprint preview: shapes, layer, and hover readout.

Two jobs, and the first is the important one.

**Shape parity.** The client draws a probe disk and an EMP diamond from its
own copy of the engine's geometry. That copy is exactly the kind of thing
AGENTS.md warns about — a radius retuned in ``tuning.py`` or ``weapons.py``
and the UI goes on promising a footprint the night will not deliver. So this
diffs the SHIPPED client function, called in a real browser, against the
engine's own ``_euclidean_disk`` / ``_manhattan_disk``, cell for cell, over
every centre on a board — corners and edges included, where the clipping
happens.

v1.31 — the mine cluster was the third shape here. It went with the
caltrop; a third weapon should add its shape back to ``engine_footprint``
and the action list in ``check_parity``.

**Rendering.** Then it aims each order for real, moves the pointer, and reads
back the drawn layer: one path per footprint, aligned to the grid, live one
solid and committed ones dashed. Shots land in ``reports/aoe/``.

Scratch harness, like the other ``backstage/probes/_fx_*.py`` — not part of pytest.

    python backstage/probes/_fx_aoe.py [base_url] [session_id]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from playwright.sync_api import sync_playwright  # noqa: E402

from sea_of_colours.game import weapons  # noqa: E402
from sea_of_colours.game.session import (  # noqa: E402
    GameSession,
    _euclidean_disk,
    _manhattan_disk,
)
from sea_of_colours.game.tuning import probe_vision_radius  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8021"
SESSION = sys.argv[2] if len(sys.argv) > 2 else "87ad9645795e405389f854d4b96e57a6"
OUT = Path("reports/aoe")

W, H = 40, 28
P1, P2 = "#FFFFFF", "#FCF871"

# Minimal board: enough live terrain under the cursor that the tooltip has
# something to count, and a couple of occupants for the EMP readout.
BUILD_BOARD = """
([w, h, spec]) => {
  const VOID = 'rgb(26,19,34)';
  const cells = [];
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) cells.push({ kind: 'fog' });
  }
  const terrain = (x, y) => {
    const n = (x * 7 + y * 13) % 11;
    let c;
    if (n === 0) c = { ch: '  ', bg: 'rgb(255,0,0)', tile: 'RED', purity: 255, tier: 'pure' };
    else if (n < 4) c = { ch: '\\u2593\\u2593', fg: 'rgb(255,0,0)', bg: VOID, tile: 'RED', purity: 160, tier: 'mass' };
    else if (n < 7) c = { ch: '  ', bg: 'rgb(63,185,80)', tile: 'GREEN', purity: 255 };
    else if (n < 9) c = { ch: '\\u2592\\u2592', fg: 'rgb(59,143,224)', bg: VOID, tile: 'BLUE', purity: 90, tier: 'mid' };
    else c = { ch: '\\u2588\\u2588', fg: VOID, bg: VOID, tile: 'EMPTY', purity: 0 };
    return Object.assign({ kind: 'terrain', stale: false, echo_probe: false }, c);
  };
  for (const [x, y] of spec.live) {
    if (x >= 0 && y >= 0 && x < w && y < h) cells[y * w + x] = terrain(x, y);
  }
  for (const o of (spec.occupants || [])) {
    const cell = cells[o.y * w + o.x] || { kind: 'fog' };
    cell.entity = { ch: o.type === 'probe' ? '\\u00B7' : 'X', fg: o.colour };
    cell.occupants = [{ id: o.id, type: o.type, owner: o.owner, label: o.id }];
    cells[o.y * w + o.x] = cell;
  }
  window.__SOC_VISION_DBG__.paintBoard(w, h, cells);
}
"""


def rect(x0: int, y0: int, x1: int, y1: int) -> list[list[int]]:
    return [[x, y] for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]


def real_cells() -> dict | None:
    """Optional: a real seat's percept to paint under the footprints.

    ``--real http://host/api/game/<id>/view?player=p1``. A plain GET, so it
    is safe to point at a server someone is playing on. Skipped when absent,
    because the assertions never depend on it — this exists so the *look*
    can be judged against honest fog rather than the saturated test board.
    """
    if "--real" not in sys.argv:
        return None
    url = sys.argv[sys.argv.index("--real") + 1]
    import urllib.request

    with urllib.request.urlopen(url, timeout=20) as fh:
        j = json.loads(fh.read().decode())
    cells = j.get("cells") or []
    if not cells:
        print(f"  (--real returned no cells from {url})")
        return None
    print(f"  real percept: {j.get('width')}x{j.get('height')}, {len(cells)} cells")
    return {"width": j["width"], "height": j["height"], "cells": cells}


def plain(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def engine_footprint(action: str, x: int, y: int) -> set[tuple[int, int]]:
    """The cells the ENGINE would touch — the answer the client must match."""
    if action == "probe":
        return _euclidean_disk(x, y, probe_vision_radius(), W, H)
    if action == "emp_launch":
        return _manhattan_disk(x, y, weapons.EMP_RADIUS, W, H)
    raise ValueError(action)


def check_parity(pg, errors: list[str]) -> None:
    """Diff the shipped client shape against the engine's, everywhere."""
    print("\n── shape parity vs the engine ──")
    print(
        f"engine dials: probe r{probe_vision_radius()} \u00b7 "
        f"EMP r{weapons.EMP_RADIUS}"
    )
    for action in ("probe", "emp_launch"):
        # Every cell on the board, so the rim cases are all covered rather
        # than sampled — 1120 centres is nothing for either side.
        centres = [[x, y] for y in range(H) for x in range(W)]
        got = pg.evaluate(
            """([action, centres, w, h]) => centres.map(
                 ([x, y]) => window.__SOC_AOE_DBG__.footprint(action, x, y, w, h)
               )""",
            [action, centres, W, H],
        )
        bad = 0
        first = None
        for (cx, cy), cells in zip(centres, got):
            want = engine_footprint(action, cx, cy)
            mine = {(int(x), int(y)) for x, y in cells}
            if mine != want:
                bad += 1
                if first is None:
                    first = (
                        f"({cx},{cy}) client {len(mine)} vs engine {len(want)}; "
                        f"missing {sorted(want - mine)[:6]} extra {sorted(mine - want)[:6]}"
                    )
        status = "ok" if bad == 0 else "FAIL"
        print(f"  {status:4} {action:11} {len(centres)} centres, {bad} mismatched")
        if bad:
            errors.append(f"{action}: {bad} centres disagree with the engine — {first}")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    live = rect(6, 4, 33, 23)
    occupants = [
        {"x": 20, "y": 14, "type": "probe", "owner": "p2", "id": "probe_p2_3", "colour": P2},
        {"x": 21, "y": 13, "type": "harvester", "owner": "p1", "id": "harvester_p1", "colour": P1},
        {"x": 19, "y": 15, "type": "probe", "owner": "p2", "id": "probe_p2_4", "colour": P2},
    ]

    with sync_playwright() as p:
        br = p.chromium.launch(channel="chrome")
        pg = br.new_page(viewport={"width": 1500, "height": 1000})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(f"{BASE}/?session={SESSION}", wait_until="networkidle")
        pg.wait_for_timeout(2500)

        if not pg.evaluate("() => Boolean(window.__SOC_AOE_DBG__)"):
            print("FAIL: no __SOC_AOE_DBG__ hook — is this the new app.js?")
            br.close()
            return 1

        # Pin the client's dials to the engine's, so parity is testing the
        # SHAPES rather than whether a /view happened to land first.
        pg.evaluate(
            """([pr, er]) => {
              window.__SOC_PROBE_RADIUS__ = pr;
              window.__SOC_EMP_RADIUS__ = er;
              window.__SOC_QUALITY_MULT__ = { trace: 0.75, vein: 1.0, mass: 1.5, pure: 3.0 };
              window.__SOC_GREEN_PENALTY__ = 100;
            }""",
            [probe_vision_radius(), weapons.EMP_RADIUS],
        )

        check_parity(pg, errors)

        # Legibility judged on a REAL percept first, if one was offered.
        # The synthetic terrain below is every tier at full saturation,
        # which no generated map looks like: it is the right board for
        # "does the outline survive the worst case", and the wrong one for
        # "does this look good". Pass --real <view-url> for the latter.
        real = real_cells()
        if real:
            pg.evaluate(
                """([w, h, cells]) => window.__SOC_VISION_DBG__.paintBoard(w, h, cells)""",
                [real["width"], real["height"], real["cells"]],
            )
            pg.wait_for_timeout(300)
            pg.evaluate(
                """() => window.__SOC_AOE_DBG__.setQueue([
                     { a: 'probe', x: 12, y: 8 },
                     { a: 'emp_launch', ats: [[26, 10], [28, 18]] },
                   ])"""
            )
            for name, action, cell in (
                ("real_probe", "probe", (20, 14)),
                ("real_emp", "emp_launch", (18, 12)),
            ):
                pg.evaluate("(a) => window.__SOC_AOE_DBG__.aim(a)", action)
                pg.evaluate("([x, y]) => window.__SOC_AOE_DBG__.hover(x, y)", list(cell))
                pg.wait_for_timeout(250)
                host = pg.query_selector(".cc-map-viewport")
                if host:
                    host.screenshot(path=str(OUT / f"aoe_{name}.png"))
            pg.evaluate("() => window.__SOC_AOE_DBG__.setQueue([])")

        pg.evaluate(BUILD_BOARD, [W, H, {"live": live, "occupants": occupants}])
        pg.wait_for_timeout(300)

        # scene -> (queued rows, aimed action, hover cell, expected shapes)
        scenes = {
            # Aiming a probe over open ground: one bright disk, nothing else.
            "probe": ([], "probe", (14, 10), 1),
            # An EMP diamond laid over two rival probes AND your own
            # harvester — the friendly-fire line is the point of the readout.
            "emp": ([], "emp_launch", (20, 14), 1),
            # A committed salvo plus a committed probe, with a fourth
            # footprint live under the cursor: dim vs bright in one shot.
            "queued": (
                [
                    {"a": "probe", "x": 12, "y": 8},
                    {"a": "emp_launch", "ats": [[26, 10], [28, 18], [22, 20]]},
                ],
                "probe",
                (30, 6),
                5,
            ),
            # Hard against the corner, where the footprint is clipped and the
            # tooltip has to own up to the wasted missiles.
            "edge": ([], "probe", (1, 1), 1),
            # Queue only, nothing aimed: every footprint must be dashed.
            "idle": ([{"a": "probe", "x": 16, "y": 12}], None, None, 1),
        }

        for name, (queue, aim, hover, want_shapes) in scenes.items():
            pg.evaluate("(q) => window.__SOC_AOE_DBG__.setQueue(q)", queue)
            pg.evaluate("(a) => window.__SOC_AOE_DBG__.aim(a)", aim)
            if hover:
                pg.evaluate("([x, y]) => window.__SOC_AOE_DBG__.hover(x, y)", list(hover))
            else:
                pg.evaluate("() => window.__SOC_AOE_DBG__.hover(null, null)")
            pg.wait_for_timeout(250)

            shapes = pg.evaluate("() => window.__SOC_AOE_DBG__.shapes()")
            print(f"\n[{name}] shapes: {json.dumps(shapes)}")
            if len(shapes) != want_shapes:
                errors.append(
                    f"{name}: {len(shapes)} footprints, expected {want_shapes}"
                )
            if aim and hover and not any(s["live"] for s in shapes):
                errors.append(f"{name}: aiming but no live footprint under the pointer")
            if not aim and any(s["live"] for s in shapes):
                errors.append(f"{name}: nothing aimed, yet a live footprint is drawn")

            info = pg.evaluate(
                """() => {
                  const svg = document.querySelector('.aoe-layer');
                  if (!svg) return { svg: false };
                  const paths = Array.from(svg.querySelectorAll('path'));
                  const grid = document.querySelector('#map-player .map-grid');
                  const gr = grid.getBoundingClientRect();
                  const sr = svg.getBoundingClientRect();
                  return {
                    svg: true,
                    viewBox: svg.getAttribute('viewBox'),
                    paths: paths.length,
                    dx: Math.round((sr.left - gr.left) * 100) / 100,
                    dy: Math.round((sr.top - gr.top) * 100) / 100,
                    dw: Math.round((sr.width - gr.width) * 100) / 100,
                    dh: Math.round((sr.height - gr.height) * 100) / 100,
                    live: paths.map((p) => p.classList.contains('aoe-shape--live')),
                    dashes: paths.map((p) => getComputedStyle(p).strokeDasharray),
                    fills: paths.map((p) => getComputedStyle(p).fillOpacity),
                  };
                }"""
            )
            print(f"[{name}] layer: {json.dumps(info)}")
            if not info.get("svg"):
                errors.append(f"{name}: no footprint layer painted")
            else:
                if info["paths"] != len(shapes):
                    errors.append(
                        f"{name}: {info['paths']} paths for {len(shapes)} footprints"
                    )
                # Half a cell out and every outline is wrong — same failure
                # mode the vision layer has, same guard.
                if not all(abs(info[k]) < 0.6 for k in ("dx", "dy", "dw", "dh")):
                    errors.append(f"{name}: footprint layer is off the grid — {info}")
                # The live/committed distinction has to survive as a VISUAL
                # difference, not just a class name.
                for is_live, dash in zip(info["live"], info["dashes"]):
                    solid = dash in ("none", "")
                    if is_live != solid:
                        errors.append(
                            f"{name}: live={is_live} but dasharray={dash!r} "
                            f"— committed footprints must read as dashed"
                        )

            if hover:
                tip = pg.evaluate(
                    "([x, y]) => window.__SOC_AOE_DBG__.tip(x, y)", list(hover)
                )
                print(f"[tip:{name}] {plain(tip)}")
                if aim and not tip:
                    errors.append(f"{name}: aiming but the tooltip carries no area block")

            host = pg.query_selector(".cc-map-viewport")
            if host:
                host.screenshot(path=str(OUT / f"aoe_{name}.png"))

        # ── The two readouts worth pinning by content ──
        pg.evaluate("() => window.__SOC_AOE_DBG__.setQueue([])")
        pg.evaluate("() => window.__SOC_AOE_DBG__.aim('emp_launch')")
        pg.evaluate("() => window.__SOC_AOE_DBG__.hover(20, 14)")
        pg.wait_for_timeout(200)
        emp_tip = pg.evaluate("() => window.__SOC_AOE_DBG__.tip(20, 14)")
        if "yours" not in emp_tip:
            errors.append(
                "an EMP aimed over your own harvester must say so — "
                f"got: {plain(emp_tip)}"
            )
        if "13" not in plain(emp_tip):
            errors.append(f"EMP r2 covers 13 cells; tooltip says: {plain(emp_tip)}")

        pg.evaluate("() => window.__SOC_AOE_DBG__.aim('probe')")
        pg.evaluate("() => window.__SOC_AOE_DBG__.hover(1, 1)")
        pg.wait_for_timeout(200)
        edge_tip = pg.evaluate("() => window.__SOC_AOE_DBG__.tip(1, 1)")
        if "clipped" not in edge_tip:
            errors.append(
                f"a disk hanging off the rim must warn: {plain(edge_tip)}"
            )

        # Shoot the tooltip cards themselves.
        for key, html in (("emp", emp_tip), ("edge", edge_tip)):
            pg.evaluate(
                """(html) => {
                  const t = document.querySelector('.cell-tooltip');
                  t.innerHTML = html;
                  t.hidden = false;
                  t.style.left = '40px';
                  t.style.top = '40px';
                }""",
                html,
            )
            pg.wait_for_timeout(200)
            el = pg.query_selector(".cell-tooltip")
            if el:
                el.screenshot(path=str(OUT / f"tip_{key}.png"))

        # Disarming must take the layer with it.
        pg.evaluate("() => { window.__SOC_AOE_DBG__.aim(null); }")
        pg.evaluate("() => window.__SOC_AOE_DBG__.hover(null, null)")
        pg.wait_for_timeout(200)
        if pg.evaluate("() => Boolean(document.querySelector('.aoe-layer'))"):
            errors.append("footprint layer survived an empty queue with nothing aimed")

        br.close()

    print("\npage errors:", errors if errors else "none")
    print(f"shots -> {OUT}/")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
