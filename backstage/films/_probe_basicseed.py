"""Scratch: find a basic-tutorial board that can teach the tiers.

``basic_drop`` has to do two things the generator does not promise
together. It has to put one of each RED tier — trace, vein, mass and
pure — under the cursor so the tooltip can price them side by side,
and it has to give the harvester a six-square orthogonal RED chain to
walk (a landing plus five steps, which is exactly the hold). Both have
to sit inside the two probe disks the film opens with, because a
tooltip over fog says nothing and ``drop_mode`` is ``live_only``.

The basic preset does not pin a seed — every real tutorial game rolls
a fresh board, and that is right for a player. A film is different: it
names squares out loud. So this picks the seed the film pins, and the
two probe centres that light what it names.

    python backstage/films/_probe_basicseed.py --n 400

Prints a table of candidates and then the full detail for the best
few: the tier quartet with its scores, the chain with its running
total, and the probe centres that cover them.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Dict, List, Optional, Tuple

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sea_of_colours.game import tutorial as soc_tutorial  # noqa: E402
from sea_of_colours.game.session import GameSession  # noqa: E402
from sea_of_colours.game.tuning import probe_vision_radius  # noqa: E402

MULT = {"trace": 0.75, "vein": 1.0, "mass": 1.5, "pure": 3.0}
TIERS = ("trace", "vein", "mass", "pure")


def tier_of(purity: int) -> str:
    if purity >= 255:
        return "pure"
    if purity >= 151:
        return "mass"
    if purity >= 51:
        return "vein"
    return "trace"


#: The film runs on a DUEL board, not a tutorial one. The basic preset
#: hands the other seat to RED_HARVEST_LITE, and the lite bot is drawn
#: to exactly the kind of seam this film needs — the first take of it
#: ended with the bot dropping onto the walk and ramming the harvester
#: head-on at hour five, both hulls damaged, nothing banked. So the
#: rival is a parked human seat here, as it is for every other outcome
#: film, and the board has to be generated the way `seed_duel` does it.
DUEL_CAP = 3


def board(seed: int) -> Tuple[GameSession, Dict[Tuple[int, int], int]]:
    cfg = soc_tutorial.preset_config("basic") or {}
    sess = GameSession.new(
        int(cfg.get("width", 24)), int(cfg.get("height", 16)),
        seed=seed, season_day_cap=DUEL_CAP,
        weapons_enabled=bool(cfg.get("weapons_enabled", False)),
        signs_enabled=bool(cfg.get("signs_enabled", False)),
    )
    red: Dict[Tuple[int, int], int] = {}
    for y in range(sess.height):
        for x in range(sess.width):
            cell = sess.grid[y][x]
            if getattr(cell.tile, "name", str(cell.tile)) == "RED":
                pur = int(getattr(cell, "purity", 0) or 0)
                if pur > 0:
                    red[(x, y)] = pur
    return sess, red


def disk(cx: int, cy: int, w: int, h: int, r: int) -> set:
    """What a probe at (cx,cy) can see. Euclidean, as the engine cuts it."""
    return {(x, y)
            for y in range(max(0, cy - r), min(h, cy + r + 1))
            for x in range(max(0, cx - r), min(w, cx + r + 1))
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r}


def chains(red: Dict[Tuple[int, int], int], n: int
           ) -> List[List[Tuple[int, int]]]:
    """Every simple orthogonal walk of ``n`` RED squares.

    Orthogonal because the engine refuses a diagonal step, and simple
    because a harvester that revisits a square finds it already spent
    (the first visit turned it GREEN) and banks a -100 instead.
    """
    out: List[List[Tuple[int, int]]] = []

    def walk(path: List[Tuple[int, int]]) -> None:
        if len(path) == n:
            out.append(list(path))
            return
        x, y = path[-1]
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nxt = (x + dx, y + dy)
            if nxt in red and nxt not in path:
                path.append(nxt)
                walk(path)
                path.pop()

    for start in red:
        walk([start])
    return out


def score(path, red) -> float:
    return sum(red[c] * MULT[tier_of(red[c])] for c in path)


def inset(xy, w, h, m=3) -> bool:
    return m <= xy[0] < w - m and 2 <= xy[1] < h - 2


def survey(seed: int, steps: int) -> Optional[dict]:
    sess, red = board(seed)
    w, h, r = sess.width, sess.height, int(probe_vision_radius())
    if not red:
        return None

    pures = [c for c, p in red.items() if p == 255 and inset(c, w, h)]
    if not pures:
        return None

    # The chain is what the harvester walks, so it is the thing with the
    # least slack: pick the richest one first and fit the rest around it.
    cand = [p for p in chains(red, steps + 1)
            if all(inset(c, w, h) for c in p)]
    if not cand:
        return None
    cand.sort(key=lambda p: -score(p, red))

    for path in cand[:40]:
        # One probe over the chain. Centring it on the middle square
        # keeps the whole walk lit for the whole night.
        mid = path[len(path) // 2]
        seen_a = disk(mid[0], mid[1], w, h, r)
        if not set(path) <= seen_a:
            continue
        for pure in pures:
            # The second probe sits on the jackpot, which is the only
            # way to be sure a 255 is readable — they are too rare to
            # hope one falls inside a disk aimed at something else.
            seen = seen_a | disk(pure[0], pure[1], w, h, r)
            lit_red = {c: p for c, p in red.items() if c in seen}
            have: Dict[str, List] = {t: [] for t in TIERS}
            for c, p in lit_red.items():
                have[tier_of(p)].append((c, p))
            if any(not have[t] for t in TIERS):
                continue
            # A quartet the camera can hold: one per tier, as close
            # together as they come, so the mouseover tour is a short
            # move and not a trip across the board.
            quartet = {}
            for t in TIERS:
                if t == "pure":
                    quartet[t] = (pure, 255)
                    continue
                quartet[t] = min(
                    have[t],
                    key=lambda cp: (cp[0][0] - pure[0]) ** 2
                    + (cp[0][1] - pure[1]) ** 2)
            spread = max(
                abs(a[0][0] - b[0][0]) + abs(a[0][1] - b[0][1])
                for a in quartet.values() for b in quartet.values())
            return {
                "seed": seed, "path": path, "walk": round(score(path, red)),
                "pure": pure, "probes": [mid, pure],
                "quartet": quartet, "spread": spread,
                "red": len(red), "pures": len(pures),
            }
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--steps", type=int, default=5)
    args = ap.parse_args()

    hits = []
    for s in range(args.start, args.start + args.n):
        try:
            got = survey(s, args.steps)
        except Exception as exc:                       # noqa: BLE001
            print(f"  seed {s}: {exc}")
            continue
        if got:
            hits.append(got)

    print(f"{len(hits)} of {args.n} seed(s) can teach the tiers AND walk "
          f"{args.steps} steps of RED\n")
    print(f"{'seed':>6} {'walk':>6} {'spread':>7} {'red':>4} {'pures':>6}  "
          f"probes")
    for r in sorted(hits, key=lambda r: (r["spread"], -r["walk"]))[:20]:
        print(f"{r['seed']:>6} {r['walk']:>6} {r['spread']:>7} "
              f"{r['red']:>4} {r['pures']:>6}  {r['probes']}")

    print("\nbest few in full:")
    for r in sorted(hits, key=lambda r: (r["spread"], -r["walk"]))[:4]:
        print(f"\n  seed {r['seed']}  walk={r['walk']}  "
              f"quartet spread={r['spread']}  probes={r['probes']}")
        for t in TIERS:
            (c, p) = r["quartet"][t]
            print(f"    {t:<6} {str(c):>9}  purity {p:>3}  "
                  f"x{MULT[t]:<4} = {round(p * MULT[t]):>4}")
        _, red = board(r["seed"])
        run = 0
        for i, c in enumerate(r["path"]):
            run += red[c] * MULT[tier_of(red[c])]
            what = "land" if i == 0 else f"step {i}"
            print(f"    {what:<7} {str(c):>9}  purity {red[c]:>3}  "
                  f"{tier_of(red[c]):<5} running {round(run):>4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
