"""Shoot the landing page's hero loop.

The old hero was a near-black board with two faint probe disks on it.
On the rebuilt landing page — which greyscales the footage, tints it
acid and lays a vignette over the middle — there was simply nothing
left to see. A hero has to survive being treated, which means it has to
be BRIGHT and BUSY before anything is done to it, and on this board
brightness means lit ground: probe disks, EMP clouds, explosions, a
redsign smear.

So this stages a deliberately overcrowded duel — six probes a seat,
three harvesters a seat, weapons on — spends three nights building it
up off camera, and films the fourth. It reuses the tutorial film rig
wholesale (the browser, the curtain, the post-process camera); the only
things it does differently are that it never writes a caption and it
delivers to `server/static/media/` instead of the films directory.

    python run_web.py --port 8022 --no-reload     # in another shell
    python backstage/films/make_hero_reel.py

Writes hero.webm, hero.mp4 and hero-poster.jpg, all three of which are
tracked, so `git checkout server/static/media/` is the revert.
"""
from __future__ import annotations

import argparse
import pathlib
import random
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from backstage.films.make_tutorial_films import (  # noqa: E402
    VIEWPORT, Film, _CURTAIN_INIT, _post, _req, live_squares,
    submit_night, submit_orbit, view,
)

MEDIA = ROOT / "server" / "static" / "media"

#: Wider than it is tall so the board itself fills a 16:10 hero frame,
#: but not much bigger than the tutorial board: on 34x20 the events were
#: far enough apart that no single shot held two of them, and a reel
#: that has to travel between beats spends its length travelling.
W, H = 28, 17
CAP = 6


# ── staging ─────────────────────────────────────────────────────────

def _new_game(base: str, seed: int) -> str:
    game = _req(base, "/api/game/new", {
        "players": ["p1", "p2"],
        "agents": {"p1": "human", "p2": "human"},
        "width": W, "height": H,
        "season_day_cap": CAP,
        "weapons_enabled": True,
        "signs_enabled": True,
        "backend": "memory",
        "seed": seed,
    })
    return str(game["session_id"])


def _fan(n: int, band: float, jitter: random.Random) -> List[List[int]]:
    """``n`` probe targets spread across a horizontal band of the board.

    Spread rather than clustered because two probes on one tile
    supersede each other, and because the whole point of the reel is a
    board with lit ground all over it.
    """
    out = []
    for i in range(n):
        fx = (i + 0.5) / n
        x = int(W * (0.08 + 0.84 * fx)) + jitter.randint(-1, 1)
        y = int(H * band) + jitter.randint(-2, 2)
        out.append([max(1, min(W - 2, x)), max(1, min(H - 2, y))])
    return out


def _reds(base: str, sid: str, seat: str) -> List[Tuple[int, int]]:
    """Live squares with RED on them, richest first."""
    got = []
    for (x, y), cell in live_squares(base, sid, seat).items():
        if str(cell.get("tile")) == "RED" and int(cell.get("purity") or 0) > 40:
            got.append(((x, y), int(cell["purity"])))
    got.sort(key=lambda p: -p[1])
    return [c for c, _ in got]


def _walk(start: Tuple[int, int], n: int,
          taken: set) -> List[List[int]]:
    """A short orthogonal walk out of ``start``, avoiding ``taken``."""
    path, cur = [], start
    for _ in range(n):
        nxt = None
        for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
            cand = (cur[0] + dx, cur[1] + dy)
            if (1 <= cand[0] < W - 1 and 1 <= cand[1] < H - 1
                    and cand not in taken):
                nxt = cand
                break
        if nxt is None:
            break
        taken.add(nxt)
        path.append([nxt[0], nxt[1]])
        cur = nxt
    return path


def stage(base: str, seed: int) -> Tuple[str, Dict]:
    """Three nights of build-up, then hand back the fourth to the camera.

    Everything here is posted over HTTP. None of it is the reel; it
    exists so that when the camera does roll there are eighteen units on
    the board and most of the map is lit.
    """
    rng = random.Random(seed)
    sid = _new_game(base, seed)

    # ── night 1: everybody looks ──────────────────────────────────
    submit_night(base, sid, [{"a": "probe", "at": c}
                             for c in _fan(3, 0.30, rng)], "p1")
    submit_night(base, sid, [{"a": "probe", "at": c}
                             for c in _fan(3, 0.70, rng)], "p2")
    for seat in ("p1", "p2"):
        submit_orbit(base, sid, [{"a": "build_probe", "count": 4},
                                 {"a": "build_harvester"}], seat)

    # ── night 2: first hulls down, more eyes out ──────────────────
    for seat, band in (("p1", 0.42), ("p2", 0.58)):
        moves: List[dict] = [{"a": "probe", "at": c}
                             for c in _fan(4, band, rng)]
        red = _reds(base, sid, seat)
        if red:
            moves.append({"a": "drop", "at": list(red[0])})
            moves += [{"a": "step", "to": c}
                      for c in _walk(red[0], 3, {red[0]})]
            moves.append({"a": "pickup"})
        submit_night(base, sid, moves, seat)
    for seat in ("p1", "p2"):
        submit_orbit(base, sid, [{"a": "build_probe", "count": 4},
                                 {"a": "build_harvester"},
                                 {"a": "build_emp", "count": 1}], seat)

    # ── night 3: fill the board out ───────────────────────────────
    for seat, band in (("p1", 0.20), ("p2", 0.80)):
        moves = [{"a": "probe", "at": c} for c in _fan(4, band, rng)]
        red = _reds(base, sid, seat)
        for start in red[:2]:
            moves.append({"a": "drop", "at": list(start)})
            moves += [{"a": "step", "to": c}
                      for c in _walk(start, 2, {start})]
            moves.append({"a": "pickup"})
        submit_night(base, sid, moves, seat)
    for seat in ("p1", "p2"):
        submit_orbit(base, sid, [{"a": "build_probe", "count": 5},
                                 {"a": "build_harvester"}], seat)

    # ── night 4: the one that gets filmed ─────────────────────────
    # Everything is chosen here, before the browser exists, so the
    # camera can be pointed at events by coordinate rather than by
    # hoping something photogenic wanders into frame.
    mine = _reds(base, sid, "p1")
    yours = _reds(base, sid, "p2")
    shared = [c for c in mine if c in set(yours)]

    plan: Dict = {"probes": _fan(5, 0.5, rng)}

    # A collision, guaranteed: one cell both seats can see, and a hull
    # each aimed into it on the same hour. Preferring a square the
    # camera can actually centre on, because a beat staged where the
    # lens cannot reach is a beat that happens off-frame — the first cut
    # put the crash four cells from the right edge and the punch-in for
    # it clamped six cells short of the wreck.
    pool = [c for c in shared if reachable(c)] or shared \
        or [c for c in mine if reachable(c)] or mine
    crash = pool[0] if pool else (W // 2, H // 2)
    plan["crash"] = list(crash)

    # An EMP wall, placed over the rival's half and away from the crash
    # so the two beats do not happen in the same square.
    cx = max(3, min(W - 4, crash[0]))
    plan["salvo"] = [[max(2, cx - 8), int(H * 0.66)],
                     [max(2, cx - 6), int(H * 0.66)],
                     [max(2, cx - 4), int(H * 0.66)]]

    # A harvester that survives, so the reel has a walk in it as well as
    # a wreck.
    runner = next((c for c in mine if c != crash and reachable(c)),
                  next((c for c in mine if c != crash), crash))
    plan["runner"] = list(runner)
    plan["runway"] = _walk(runner, 4, {runner, crash})

    # The rival's night, posted now: TRANSMIT stays disabled until every
    # seat has committed, and a hero shoot that hangs on that is twenty
    # minutes of nothing.
    theirs: List[dict] = [{"a": "probe", "at": c} for c in _fan(4, 0.34, rng)]
    theirs.append({"a": "drop", "at": list(crash)})
    if yours:
        far = next((c for c in yours if c != crash), None)
        if far:
            theirs.append({"a": "drop", "at": list(far)})
            theirs += [{"a": "step", "to": c} for c in _walk(far, 3, {far})]
            theirs.append({"a": "pickup"})
    submit_night(base, sid, theirs, "p2")

    v = view(base, sid, "p1")
    plan["units"] = sum(1 for _ in v.get("units") or [])
    return sid, plan


# ── the reel ────────────────────────────────────────────────────────

#: Every wide shot in the reel. Framing the BOARD rather than the
#: viewport is the single biggest thing that makes this footage usable
#: as a background: pull all the way out and two thirds of the frame is
#: the ORDERS panel, the fleet roster and the clock, which behind a
#: title reads as clutter. Pushed to the board, the frame is all seams,
#: vision borders and trails.
BOARD: List[tuple] = [(0, 0), (W - 1, H - 1)]


#: Half-size of the subject rectangle a punch-in asks for, in cells.
_SUB_W, _SUB_H = 5, 3
#: How far the CENTRE of a punch-in must stay from the board edge, in
#: cells. The camera builds a window of `viewport / zoom` centred on the
#: subject and only clamps it to the VIEWPORT — which is wider than the
#: map, so the slack it takes up is the ORDERS panel. Clamping the
#: subject's corners is not enough and was the first thing tried here:
#: an 11x7 subject padded 1.4x and fitted to 16:10 comes back as a
#: window about 16x10 cells, so the centre is what has to be held in.
_KEEP_X, _KEEP_Y = 10, 7


def reachable(c: Tuple[int, int]) -> bool:
    """Can the camera centre a punch-in here without catching a panel?"""
    return (_KEEP_X <= c[0] <= W - 1 - _KEEP_X
            and _KEEP_Y <= c[1] <= H - 1 - _KEEP_Y)


def shot(cx: int, cy: int) -> List[tuple]:
    """A punch-in on a cell, guaranteed to frame nothing but board."""
    cx = max(_KEEP_X, min(W - 1 - _KEEP_X, cx))
    cy = max(_KEEP_Y, min(H - 1 - _KEEP_Y, cy))
    return [(cx - _SUB_W, cy - _SUB_H), (cx + _SUB_W, cy + _SUB_H)]


def compose(f: Film) -> None:
    """Queue the night. Runs with the curtain DOWN — none of it is shot.

    Building the queue takes about twenty seconds of clicking through
    pickers, and a hero loop cannot afford twenty seconds of anything,
    least of all a menu. The camera is only let in once the orders are
    in and the night is about to run.
    """
    crash = tuple(f.plan["crash"])
    runner = tuple(f.plan["runner"])
    runway = [tuple(c) for c in f.plan["runway"]]
    salvo = [tuple(c) for c in f.plan["salvo"]]

    for c in f.plan["probes"]:
        f.click('.cc-deploy-btn:has-text("LAUNCH PROBE")', before=90, after=90)
        f.click(f.cell(c[0], c[1]), before=70, after=120, ms=140)
        f.escape(after=70)

    if f.pg.locator('.cc-deploy-btn:has-text("EMP")').count():
        f.click('.cc-deploy-btn:has-text("EMP")', before=110, after=140)
        for c in salvo:
            f.click(f.cell(c[0], c[1]), before=70, after=110, ms=130)
        f.escape(after=90)

    f.order(0, runner, tuple(runway))
    f.lift(0)
    if f.pg.locator(".cc-fleet-row").count() > 1:
        f.order(1, crash)
    f.park()


def reel(f: Film, base: str, sid: str) -> None:
    """One night, no captions, camera doing all the talking.

    Shape: open on the whole board while the probes bloom, three
    punch-ins on beats `stage` placed by coordinate, then back out to
    the board for the last hours — so the loop point is a calm wide
    frame at both ends. Cutting from a close-up back to a wide is the
    thing that makes a looping background look broken.
    """
    crash = tuple(f.plan["crash"])
    runner = tuple(f.plan["runner"])
    runway = [tuple(c) for c in f.plan["runway"]]
    salvo = [tuple(c) for c in f.plan["salvo"]]

    mid = runway[len(runway) // 2] if runway else runner
    f.push_in(*BOARD, pad=1.02, max_scale=3.2, ms=1)   # snap, don't ramp
    f.wait(500)
    f.praxis(cues=[
        ("H01", None, 200),
        ("H02", None, 150, shot(*crash), 700),
        ("H04", None, 250, shot(*mid), 700),
        ("H06", None, 250, shot(*salvo[len(salvo) // 2]), 700),
        ("H08", None, 200, BOARD, 1000),
    ])
    f.push_in(*BOARD, pad=1.02, max_scale=3.2, ms=700)
    f.wait(1100)


# ── delivery ────────────────────────────────────────────────────────

def deliver(src: pathlib.Path) -> List[str]:
    """webm in place, plus an mp4 and a poster beside it."""
    notes = []
    mp4 = MEDIA / "hero.mp4"
    poster = MEDIA / "hero-poster.jpg"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
             "-c:v", "libx264", "-preset", "slow", "-crf", "27",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart",
             "-an", str(mp4)],
            check=True, capture_output=True, timeout=900)
        notes.append(f"mp4 {mp4.stat().st_size / 1024:.0f} KB")
    except (OSError, subprocess.SubprocessError) as exc:
        notes.append(f"mp4 FAILED ({type(exc).__name__})")
    try:
        # A frame from a third of the way in: the opening seconds are
        # the queue filling up, which is not what a poster should be.
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", "9", "-i", str(src),
             "-frames:v", "1", "-q:v", "4", str(poster)],
            check=True, capture_output=True, timeout=180)
        notes.append(f"poster {poster.stat().st_size / 1024:.0f} KB")
    except (OSError, subprocess.SubprocessError) as exc:
        notes.append(f"poster FAILED ({type(exc).__name__})")
    return notes


def shoot(base: str, seed: int, trim: bool) -> int:
    sid, plan = stage(base, seed)
    print(f"  staged {sid} — {plan['units']} unit(s) on a {W}x{H} board")
    print(f"  crash @{plan['crash']}  runner @{plan['runner']}  "
          f"salvo {plan['salvo']}")

    MEDIA.mkdir(parents=True, exist_ok=True)
    for stray in MEDIA.glob("page@*.webm"):
        stray.unlink(missing_ok=True)

    fails: List[str] = []
    t0 = time.time()
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        ctx = br.new_context(viewport=VIEWPORT, record_video_dir=str(MEDIA),
                             record_video_size=VIEWPORT)
        ctx.add_init_script(_CURTAIN_INIT)
        pg = ctx.new_page()
        t_page = time.monotonic()
        lead = 0.0
        errors: List[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))

        f = Film(pg, plan)
        f.t0 = t_page
        try:
            pg.goto(f"{base}/?session={sid}&player=p1",
                    wait_until="networkidle")
            pg.wait_for_selector("#orders-asset-roster", state="visible",
                                 timeout=25000)
            pg.wait_for_timeout(1200)
            f.kit()
            pg.evaluate("() => window.__film.put(640, 720)")
            # Compose behind the curtain, THEN mark the boot cut, THEN
            # let the camera in. `lead` is what the post-pass trims to,
            # so taking it here is what keeps the menu-clicking out of
            # the delivered loop.
            compose(f)
            lead = time.monotonic() - t_page
            f.raise_curtain()
            reel(f, base, sid)
        except Exception as exc:                       # noqa: BLE001
            fails.append(f"{type(exc).__name__}: {exc}")
        fails.extend(f.fails)
        if errors:
            fails.append(f"page errors: {errors[:3]}")
        raw = pg.video.path()
        ctx.close()
        br.close()

    src = pathlib.Path(raw)
    if not src.exists():
        print(f"  FAIL playwright reported {raw} and it is not there")
        return 1

    final = MEDIA / "hero.webm"
    final.unlink(missing_ok=True)
    src.rename(final)
    note = _post(final, lead, trim, f.cam)
    print(f"  {time.time() - t0:5.1f}s  "
          f"{final.stat().st_size / 1024:6.0f} KB  hero.webm{note}")
    for n in deliver(final):
        print(f"           {n}")

    for msg in fails:
        print(f"  FAIL {msg}")
    if fails:
        print("\nFAIL — the reel is on disk but something above went wrong. "
              "`git checkout server/static/media/` puts the old one back.")
        return 1
    print("\nPASS — now WATCH it. No assertion here can tell you whether a "
          "hero loop is exciting.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8022")
    ap.add_argument("--seed", type=int, default=90210)
    ap.add_argument("--no-trim", action="store_true")
    args = ap.parse_args()
    return shoot(args.base.rstrip("/"), args.seed, trim=not args.no_trim)


if __name__ == "__main__":
    raise SystemExit(main())
