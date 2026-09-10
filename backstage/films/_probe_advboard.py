#!/usr/bin/env python3
"""Scratch: print the Advanced tutorial board, tile by tile.

The advanced films are staged on hand-picked squares, and picking them
off a screenshot is how you end up filming a five-step comb that walks
into two EMPTY cells and teaches nothing. This prints what is actually
there: tier, purity, and — for the tactical films — the 4-way walk
neighbourhood and the Manhattan-2 blast a salvo cell would cover.

    python backstage/films/_probe_advboard.py
    python backstage/films/_probe_advboard.py --around 4,12 --r 5

Nothing here talks to a server; the HTTP views are all fogged and would
hand back render glyphs. The engine is importable, so ask it.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sea_of_colours.game import tutorial as soc_tutorial  # noqa: E402
from sea_of_colours.game.session import GameSession  # noqa: E402
from sea_of_colours.game.session import RED_QUALITY_MULTIPLIER  # noqa: E402


def build() -> GameSession:
    cfg = soc_tutorial.preset_config("advanced") or {}
    return GameSession.new(
        int(cfg.get("width", 24)), int(cfg.get("height", 16)),
        seed=int(soc_tutorial.ADVANCED_TUTORIAL_SEED),
        season_day_cap=int(cfg.get("season_day_cap", 6)),
        weapons_enabled=True, signs_enabled=True, tutorial="advanced",
    )


def tier(pur: int) -> str:
    if pur >= 255:
        return "pure"
    if pur >= 151:
        return "mass"
    if pur >= 51:
        return "vein"
    return "trace"


def at(sess, x, y):
    if not (0 <= x < sess.width and 0 <= y < sess.height):
        return None, 0
    c = sess.grid[y][x]
    return getattr(c.tile, "name", str(c.tile)), int(getattr(c, "purity", 0) or 0)


def glyph(sess, x, y) -> str:
    t, p = at(sess, x, y)
    if t == "RED":
        return {"pure": "##", "mass": "R+", "vein": "r ", "trace": ". "}[tier(p)]
    if t == "BLUE":
        return "B+" if p >= 151 else "b "
    if t == "GREEN":
        return "g "
    return "  "


def worth(sess, x, y) -> float:
    t, p = at(sess, x, y)
    if t != "RED":
        return 0.0
    return p * RED_QUALITY_MULTIPLIER[tier(p)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--around", default="", help="x,y to zoom on")
    ap.add_argument("--r", type=int, default=4)
    args = ap.parse_args()
    sess = build()

    print(f"board {sess.width}x{sess.height}  seed "
          f"{soc_tutorial.ADVANCED_TUTORIAL_SEED}")
    print("     " + "".join(f"{x:<2}" for x in range(sess.width)))
    for y in range(sess.height):
        row = "".join(glyph(sess, x, y) for x in range(sess.width))
        print(f"  {y:>2} {row}")
    print("  legend  ## pure255  R+ mass  r vein  . trace  B+/b blue  g green")

    pures = [(x, y) for y in range(sess.height) for x in range(sess.width)
             if at(sess, x, y) == ("RED", 255)]
    print(f"\npures: {pures}")

    if args.around:
        cx, cy = (int(v) for v in args.around.split(","))
        r = args.r
        print(f"\n── around ({cx},{cy}), r={r} " + "─" * 30)
        for y in range(cy - r, cy + r + 1):
            cells = []
            for x in range(cx - r, cx + r + 1):
                t, p = at(sess, x, y)
                if t is None:
                    cells.append("  ---   ")
                else:
                    cells.append(f"{t[0]}{p:>3}{'*' if (x, y) == (cx, cy) else ' '}"
                                 f"{'':2}")
            print(f"  y={y:>2} " + "".join(cells))

        # What a 4-way comb out of here could actually bank. Six parcels
        # is the hold, so a comb is the drop plus five steps.
        print(f"\n  best 4-way combs of 5 steps from ({cx},{cy}):")
        best = []

        def walk(path, seen):
            if len(path) == 6:
                best.append((sum(worth(sess, *c) for c in path), list(path)))
                return
            x, y = path[-1]
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (x + dx, y + dy)
                if n in seen or at(sess, *n)[0] is None:
                    continue
                seen.add(n)
                path.append(n)
                walk(path, seen)
                path.pop()
                seen.remove(n)

        walk([(cx, cy)], {(cx, cy)})
        best.sort(key=lambda p: -p[0])
        for score, path in best[:5]:
            desc = " ".join(f"({x},{y}){at(sess, x, y)[0][0]}{at(sess, x, y)[1]}"
                            for x, y in path)
            print(f"    {score:>7.0f}  {desc}")

        print(f"\n  Manhattan-2 blast centred ({cx},{cy}) covers:")
        blast = [(cx + dx, cy + dy)
                 for dx in range(-2, 3) for dy in range(-2, 3)
                 if abs(dx) + abs(dy) <= 2]
        print(f"    {blast}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
