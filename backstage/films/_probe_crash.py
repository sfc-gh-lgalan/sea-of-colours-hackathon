"""Scratch: prove the engine gives the films what they must show.

Three things the outcome films need, none of which the bot in the other
seat will supply on request: two crashes in ONE night (so a single
cinematic carries both), and a harvest that actually turns into score.

Driven entirely over HTTP with no browser, which is the point — a film
takes a minute to shoot and tells you nothing about WHY a beat did not
happen. Settle the mechanic here first, then choreograph it. Both of the
nastiest surprises behind `basic_crash` and `basic_score` were found in
this file: harvester steps are 4-way orthogonal (a diagonal is dropped
in silence), and an orbit phase does not resolve until every seat has
committed, so a duel board needs the rival's empty orbit posted too.

Needs a server: `python backstage/films/_probe_crash.py` against BASE below.
"""
from __future__ import annotations

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8022"
W, H = 24, 16


def req(path, body=None):
    d = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        BASE + path, data=d,
        headers={"Content-Type": "application/json"} if d else {},
    )
    with urllib.request.urlopen(r, timeout=90) as resp:
        return json.loads(resp.read().decode())


def new_game(seed, seats=("p1", "p2"), cap=5):
    g = req("/api/game/new", {
        "players": list(seats),
        "agents": {s: "human" for s in seats},
        "width": W, "height": H, "season_day_cap": cap,
        "weapons_enabled": False, "signs_enabled": False,
        "backend": "memory", "seed": seed,
    })
    return str(g["session_id"])


def night(sid, seat, moves):
    return req(f"/api/game/{sid}/policy", {"player": seat, "moves": moves})


def orbit(sid, seat, actions):
    return req(f"/api/game/{sid}/orbit", {"player": seat, "actions": actions})


def state(sid):
    st = req(f"/api/game/{sid}/status")
    return str(st.get("phase")), int(st.get("day") or 0)


def board(sid, seat):
    """Live squares for a seat, keyed (x, y) -> cell."""
    v = req(f"/api/game/{sid}/view?player={seat}")
    return v, {
        (i % v["width"], i // v["width"]): c
        for i, c in enumerate(v["cells"])
        if c.get("kind") == "terrain" and not c.get("stale")
    }


def harvs(sid, seat):
    v, _ = board(sid, seat)
    return [
        {k: u.get(k) for k in
         ("id", "pos", "orbit", "damaged", "cargo_squares", "orbital_cargo_red")}
        for u in v["units"] if u.get("type") == "harvester"
    ]


def credits(sid, seat):
    v = req(f"/api/game/{sid}/view?player={seat}")
    inv = v.get("inventory") or {}
    return inv.get("credits", v.get("agent_view", {}).get("hud", {}).get("credits"))


# ── A: both crashes in one night ────────────────────────────────────

def test_double_crash(seed):
    print("\n=== A: two crashes, one night ===")
    sid = new_game(seed)
    # Night 1 + 2: probes. Offset between seats — two probes on the same
    # tile supersede each other, which quietly blinds both houses.
    night(sid, "p1", [{"a": "probe", "at": [10, 8]}, {"a": "probe", "at": [16, 8]}])
    night(sid, "p2", [{"a": "probe", "at": [12, 8]}, {"a": "probe", "at": [18, 8]}])
    for s in ("p1", "p2"):
        orbit(sid, s, [{"a": "build_probe", "count": 2}])
    night(sid, "p1", [{"a": "probe", "at": [11, 6]}, {"a": "probe", "at": [15, 10]}])
    night(sid, "p2", [{"a": "probe", "at": [13, 6]}, {"a": "probe", "at": [17, 10]}])
    for s in ("p1", "p2"):
        orbit(sid, s, [{"a": "build_harvester"}])
    print("  after orbit 2:", state(sid), "credits p1", credits(sid, "p1"))
    print("  p1 fleet:", json.dumps(harvs(sid, "p1")))

    ids1 = [h["id"] for h in harvs(sid, "p1")]
    ids2 = [h["id"] for h in harvs(sid, "p2")]
    if len(ids1) < 2 or len(ids2) < 2:
        print("  FAIL: a seat did not get a second harvester")
        return None
    _, l1 = board(sid, "p1")
    _, l2 = board(sid, "p2")
    shared = set(l1) & set(l2)
    P, Q, R, S = (12, 8), (14, 10), (15, 10), (16, 10)
    print("  P,Q,R,S shared:", [c in shared for c in (P, Q, R, S)])

    night(sid, "p1", [
        {"a": "drop", "unit": ids1[0], "at": list(P)},
        {"a": "drop", "unit": ids1[1], "at": list(Q)},
        {"a": "step", "unit": ids1[1], "to": list(R)},
    ])
    night(sid, "p2", [
        {"a": "drop", "unit": ids2[0], "at": list(P)},
        {"a": "drop", "unit": ids2[1], "at": list(S)},
        {"a": "step", "unit": ids2[1], "to": list(R)},
    ])
    print("  after night 3:", state(sid))
    print("  p1 fleet:", json.dumps(harvs(sid, "p1")))
    caps = _captions(sid, day=3)
    for c in caps:
        if "COLLISION" in c:
            print("  >>", c)
    kinds = {"drop": 0, "step": 0}
    for c in caps:
        if "COLLISION" not in c:
            continue
        kinds["drop" if "SIMULTANEOUS" in c or "dropped" in c else "step"] += 1
    print("  collision captions:", kinds)
    return sid


# ── B: harvest -> station -> catapult -> score ──────────────────────

def test_score(seed):
    print("\n=== B: harvest becomes score ===")
    sid = new_game(seed)
    night(sid, "p1", [{"a": "probe", "at": [10, 8]}, {"a": "probe", "at": [16, 8]}])
    night(sid, "p2", [{"a": "probe", "at": [4, 3]}])
    for s in ("p1", "p2"):
        orbit(sid, s, [{"a": "build_probe", "count": 2}])

    _, l1 = board(sid, "p1")
    reds = sorted(
        ((xy, c) for xy, c in l1.items()
         if c.get("tile") == "RED" and int(c.get("purity") or 0) > 0),
        key=lambda kv: -int(kv[1].get("purity") or 0),
    )
    print(f"  live red squares: {len(reds)}")
    if len(reds) < 3:
        print("  FAIL: not enough live RED to harvest")
        return None
    chain = _chain([xy for xy, _ in reds], l1, 4)
    print("  harvest chain:", chain,
          [int(l1[c].get("purity") or 0) for c in chain])
    if len(chain) < 3:
        print("  FAIL: red squares are not adjacent enough to walk")
        return None

    moves = [{"a": "drop", "unit": "harvester_p1", "at": list(chain[0])}]
    moves += [{"a": "step", "unit": "harvester_p1", "to": list(c)}
              for c in chain[1:]]
    moves += [{"a": "pickup", "unit": "harvester_p1"}]
    night(sid, "p1", moves)
    night(sid, "p2", [{"a": "probe", "at": [4, 12]}])
    print("  after harvest night:", state(sid))
    print("  p1 fleet:", json.dumps(harvs(sid, "p1")))
    v = req(f"/api/game/{sid}/view?player=p1")
    print("  score now:", v.get("scores"))

    for s in ("p1", "p2"):
        orbit(sid, s, [])
    v = req(f"/api/game/{sid}/view?player=p1")
    print("  after orbit:", state(sid), "score:", v.get("scores"))

    rep = req(f"/api/game/{sid}/replay?player=p1")
    print("  cumulative_shipped_score:",
          json.dumps(rep.get("cumulative_shipped_score"))[:200])
    cat = rep.get("catapult_by_day") or {}
    print("  catapult days:", sorted(cat.keys()))
    for day, blob in sorted(cat.items()):
        s = json.dumps(blob)
        print(f"    day {day}: {len(s)} bytes {s[:180]}")
    return sid


def _chain(cands, live, n):
    """A walkable run of adjacent squares starting at the richest."""
    start = cands[0]
    out = [start]
    cur = start
    used = {start}
    rich = set(cands)
    for _ in range(n - 1):
        best = None
        for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1),
                       (1, 1), (-1, -1), (1, -1), (-1, 1)):
            c = (cur[0] + dx, cur[1] + dy)
            if c in used or c not in live:
                continue
            if c not in rich:
                continue
            p = int(live[c].get("purity") or 0)
            if best is None or p > best[1]:
                best = (c, p)
        if best is None:
            break
        used.add(best[0])
        out.append(best[0])
        cur = best[0]
    return out


def _captions(sid, day):
    rep = req(f"/api/game/{sid}/replay?player=p1")
    out = []
    for fr in rep.get("frames") or []:
        if int(fr.get("day") or 0) != day:
            continue
        cap = fr.get("caption")
        if cap:
            out.append(str(cap))
    return out


if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 777
    test_double_crash(seed)
    test_score(seed)
    sys.exit(0)
