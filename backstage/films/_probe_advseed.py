"""Scratch: find a tutorial board that can actually teach signs.

The Advanced films need three things on one 24x16 board, and the map
generator promises none of them together: a BLUE pocket bright enough
to aim a hot drop at, at least two PURE (255) seams far enough apart
that one can be lit by each House, and enough RED between them to make
a harvester worth flying. Shooting first and finding out afterwards
costs a minute a take, so pick the seed here.

Prints a table over a seed range and then the full detail for the best
few, including bluesign centres and pure clusters.

    python backstage/films/_probe_advseed.py --n 60
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sea_of_colours.game import tutorial as soc_tutorial  # noqa: E402
from sea_of_colours.game.session import GameSession  # noqa: E402


def _clusters(cells: set) -> list:
    """8-connected components, as the engine groups pure seams."""
    out, seen = [], set()
    for c in cells:
        if c in seen:
            continue
        stack, comp = [c], []
        seen.add(c)
        while stack:
            x, y = stack.pop()
            comp.append((x, y))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (x + dx, y + dy)
                    if n in cells and n not in seen:
                        seen.add(n)
                        stack.append(n)
        out.append(comp)
    return sorted(out, key=len, reverse=True)


def survey(seed):
    """Build the session in-process — every HTTP view is fogged.

    The observer route is fog-of-war too (it is a spectator seat, not a
    map dump), so asking it what is on the board returns render glyphs
    and nothing else. The engine is right here; use it.
    """
    cfg = soc_tutorial.preset_config("advanced") or {}
    sess = GameSession.new(
        int(cfg.get("width", 24)), int(cfg.get("height", 16)),
        seed=seed, season_day_cap=int(cfg.get("season_day_cap", 3)),
        weapons_enabled=True, signs_enabled=True, tutorial="advanced",
    )
    pures, reds, blues = set(), 0, set()
    for y in range(sess.height):
        for x in range(sess.width):
            cell = sess.grid[y][x]
            tile = getattr(cell.tile, "name", str(cell.tile))
            pur = int(getattr(cell, "purity", 0) or 0)
            if tile == "RED":
                reds += 1
                if pur == 255:
                    pures.add((x, y))
            elif tile == "BLUE":
                blues.add((x, y))
    sign = [
        {"center": r.get("center"), "cells": r.get("cells") or []}
        for r in (sess.blue_sign or [])
    ]
    pc = _clusters(pures)
    bc = _clusters(blues)
    # How far apart are the two biggest pure seams? A pair sitting on
    # top of each other cannot be "one each" for a contested-jackpot film.
    sep = 0
    if len(pc) >= 2:
        ax = sum(p[0] for p in pc[0]) / len(pc[0])
        ay = sum(p[1] for p in pc[0]) / len(pc[0])
        bx = sum(p[0] for p in pc[1]) / len(pc[1])
        by = sum(p[1] for p in pc[1]) / len(pc[1])
        sep = round(((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5, 1)
    return {
        "seed": seed, "red": reds,
        "pure": len(pures), "pure_seams": len(pc), "sep": sep,
        "blue": len(blues), "blue_pockets": len(bc),
        "sign_regions": len(sign),
        "sign_cells": sum(len(r.get("cells") or []) for r in sign),
        "pc": pc, "bc": bc, "sign": sign,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1000)
    ap.add_argument("--n", type=int, default=40)
    args = ap.parse_args()

    rows = []
    for s in range(args.start, args.start + args.n):
        try:
            rows.append(survey(s))
        except Exception as exc:
            print(f"  seed {s}: {exc}")
    print(f"{'seed':>6} {'red':>4} {'pure':>5} {'seams':>6} {'sep':>5} "
          f"{'blue':>5} {'pockets':>8} {'signRg':>7} {'signCl':>7}")
    for r in rows:
        print(f"{r['seed']:>6} {r['red']:>4} {r['pure']:>5} "
              f"{r['pure_seams']:>6} {r['sep']:>5} {r['blue']:>5} "
              f"{r['blue_pockets']:>8} {r['sign_regions']:>7} "
              f"{r['sign_cells']:>7}")

    # Everything the films need has to sit AWAY FROM THE EDGE. A probe
    # is a radius-4 disk and a close-up frames a few squares around its
    # subject; a jackpot in column 0 gets half a probe and a close-up of
    # the bezel. Same for the bluesign the hot drop aims at.
    def inset(xy, w=24, h=16, m=4):
        return m <= xy[0] < w - m and 3 <= xy[1] < h - 3

    def score(r):
        pures = [c[0] for c in r["pc"] if inset(c[0])]
        bright = [
            (int(c[0]), int(c[1]))
            for reg in r["sign"] for c in (reg.get("cells") or [])
            if float(c[2]) >= 0.75 and inset((int(c[0]), int(c[1])))
        ]
        if len(pures) < 2 or not bright:
            return None
        # The two jackpots want to be far apart (one per House) and the
        # bluesign wants to be clear of both, so the hot-drop film and
        # the redsign film are not shot over the same three squares.
        (ax, ay), (bx, by) = pures[0], pures[1]
        sep = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
        clear = min(
            min(((hx - px) ** 2 + (hy - py) ** 2) ** 0.5 for px, py in pures)
            for hx, hy in bright
        )
        if sep < 10 or clear < 5:
            return None
        return {"sep": round(sep, 1), "clear": round(clear, 1),
                "pures": pures, "bright": bright[:5]}

    good = []
    for r in rows:
        s = score(r)
        if s and r["red"] >= 90:
            good.append((r, s))
    good.sort(key=lambda p: -(p[1]["sep"] + p[1]["clear"]))
    print(f"\n{len(good)} inset candidate(s); best few:")
    for r, s in good[:5]:
        print(f"\n  seed {r['seed']}  red={r['red']}  pure-sep={s['sep']}  "
              f"sign-clear={s['clear']}")
        print(f"    jackpots : {s['pures']}")
        print(f"    bright   : {s['bright']}")
        for reg in r["sign"][:3]:
            cells = reg.get("cells") or []
            hot = sorted(cells, key=lambda c: -float(c[2]))[:3]
            print(f"    sign @{[round(v, 1) for v in reg.get('center')]}: "
                  f"{len(cells)} cell(s), brightest "
                  f"{[(c[0], c[1], round(float(c[2]), 2)) for c in hot]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
