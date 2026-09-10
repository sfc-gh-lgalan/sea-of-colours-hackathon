"""Scratch: prove the Advanced arc before any of it is choreographed.

Five claims the Advanced films are built on, none of which is obvious
from reading the engine, and all of which are expensive to discover from
a film that took a minute to shoot and shows nothing:

  A. A HOT DROP lands. Probe on hour K, drop blind into that disk on
     hour K+1, and the landing validates against the hour-start snapshot.
     Landing on BLUE banks the blue that pays for every weapon.
  B. A REDSIGN fires when your own probe first sees a pure-255, and the
     beacon is public — the rival sees it without seeing your probe.
  C. An EMP salvo of three interwoven diamonds kills a rival probe, and
     the redsign it discovered stays lit (the beacon is minted, not a
     live reading) — which is the whole point: they know, and cannot look.
  D. You can land NEXT TO a cloud and still auto-harvest, but landing
     INSIDE an established one denies the harvest (§4.9.3).
  E. CHAFF cancels a rival's LIFT and the dawn wave then destroys the
     harvester, credited as a chaff kill.

Board is seed 2351 on the Advanced preset, chosen by _probe_advseed.py:
one unambiguous blue smear at (12,4)-(12,6) and two jackpots far apart
at (19,5) and (4,12).

    python backstage/films/_probe_advanced.py
"""
from __future__ import annotations

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8022"
SEED = 2351
BLUE = (12, 5)          # probe aim — the bright heart of the smear
BLUE_LAND = (12, 4)     # BLUE 255, the hot-drop landing square
MINE = (19, 5)          # our jackpot
THEIRS = (4, 12)        # the rival's


def req(path, body=None):
    d = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        BASE + path, data=d,
        headers={"Content-Type": "application/json"} if d else {})
    with urllib.request.urlopen(r, timeout=90) as resp:
        return json.loads(resp.read().decode())


def new_duel(cap=6):
    g = req("/api/game/new", {
        "players": ["p1", "p2"],
        "agents": {"p1": "human", "p2": "human"},
        "width": 24, "height": 16, "season_day_cap": cap,
        "weapons_enabled": True, "signs_enabled": True,
        "backend": "memory", "seed": SEED,
    })
    return str(g["session_id"])


def night(sid, seat, moves):
    return req(f"/api/game/{sid}/policy", {"player": seat, "moves": moves})


def orbit(sid, seat, actions):
    return req(f"/api/game/{sid}/orbit", {"player": seat, "actions": actions})


def st(sid):
    s = req(f"/api/game/{sid}/status")
    return str(s.get("phase")), int(s.get("day") or 0)


def v(sid, seat="p1"):
    return req(f"/api/game/{sid}/view?player={seat}")


def blue_of(sid, seat="p1"):
    inv = v(sid, seat).get("inventory") or {}
    return inv.get("blue_purity", inv.get("blue"))


def harvs(sid, seat="p1"):
    return [{k: u.get(k) for k in ("id", "pos", "orbit", "damaged")}
            for u in v(sid, seat)["units"] if u.get("type") == "harvester"]


def captions(sid, day):
    rep = req(f"/api/game/{sid}/replay?player=p1")
    return [str(f.get("caption") or "") for f in (rep.get("frames") or [])
            if int(f.get("day") or 0) == day and f.get("caption")]


def main() -> int:
    sid = new_duel()
    print(f"session {sid}")
    print("start blue:", blue_of(sid), "| phase", st(sid))

    # ── A: hot drop onto the bluesign ────────────────────────────────
    print("\n=== A: hot drop ===")
    hid = harvs(sid)[0]["id"]
    night(sid, "p1", [
        {"a": "probe", "at": list(BLUE)},
        {"a": "drop", "unit": hid, "at": list(BLUE_LAND)},
        {"a": "step", "unit": hid, "to": [12, 5]},
        {"a": "step", "unit": hid, "to": [12, 6]},
        {"a": "pickup", "unit": hid},
    ])
    # Rival probes somewhere neutral. Anywhere within r=4 of THEIRS
    # lights their jackpot a night early and steals the redsign film's
    # only beat — (6,12) did exactly that on the first run.
    night(sid, "p2", [{"a": "probe", "at": [20, 12]}])
    for c in captions(sid, 1):
        if any(k in c for k in ("dropped", "harvested", "BLUE", "probe")):
            print("  ", c[:110])
    print("  after night 1:", st(sid), "| blue now:", blue_of(sid))

    # ── the orbit that buys the weapons ──────────────────────────────
    print("\n=== orbit 2: what the blue buys ===")
    r = orbit(sid, "p1", [{"a": "build_emp", "count": 1}])
    print("  build_emp ok:", r.get("ok"), r.get("errors") or "")
    r2 = orbit(sid, "p1", [{"a": "build_emp", "count": 1},
                           {"a": "build_chaff", "count": 1},
                           {"a": "build_probe", "count": 2}])
    print("  emp+chaff ok:", r2.get("ok"), r2.get("errors") or "")
    orbit(sid, "p2", [{"a": "build_probe", "count": 2}])
    print("  after orbit:", st(sid), "| blue left:", blue_of(sid))
    inv = v(sid).get("inventory") or {}
    print("  stock:", json.dumps({k: inv.get(k) for k in
                                  ("weapon_stock", "probe_stock", "credits")}))

    # ── B: redsigns, mine and theirs ─────────────────────────────────
    print("\n=== B: redsign ===")
    hid = harvs(sid)[0]["id"]
    night(sid, "p1", [{"a": "probe", "at": list(MINE)}])
    night(sid, "p2", [{"a": "probe", "at": list(THEIRS)}])
    rs = v(sid).get("redsign") or []
    print(f"  p1 sees {len(rs)} redsign region(s):")
    for r in rs:
        print(f"    center={r.get('center')} mine={r.get('mine')} "
              f"cells={len(r.get('cells') or [])} day={r.get('day')} "
              f"hour={r.get('hour')}")
    for c in captions(sid, 2):
        if "SIGN" in c.upper():
            print("  ", c[:110])
    print("  after night 2:", st(sid))

    # ── C + D: the salvo denies ground, and "beside" is not "inside" ──
    print("\n=== C/D: EMP salvo ===")
    orbit(sid, "p1", [{"a": "build_emp", "count": 2},
                      {"a": "build_harvester"},
                      {"a": "build_probe", "count": 2}])
    orbit(sid, "p2", [{"a": "build_probe", "count": 2}])
    print("  after orbit 3:", st(sid))

    # The rival re-lights their jackpot; we blanket it and kill the eye.
    # Three diamonds in a row overlap into one wall rather than three
    # separate puddles — that is the "interwoven" the film shows.
    salvo = [[2, 12], [4, 12], [6, 12]]
    hid = harvs(sid)[0]["id"]
    night(sid, "p1", [
        {"a": "emp_launch", "at": salvo},
        {"a": "probe", "at": [8, 11]},            # just clear of the wall
        {"a": "drop", "unit": hid, "at": [8, 11]},  # beside, not inside
    ])
    night(sid, "p2", [{"a": "probe", "at": list(THEIRS)}])
    caps = captions(sid, 3)
    for c in caps:
        if any(k in c.upper() for k in ("EMP", "DESTROY", "HARVEST", "DROP")):
            print("  ", c[:120])
    print("  p2 probes alive:",
          [u.get("id") for u in v(sid, "p2")["units"]
           if u.get("type") == "probe"])
    print("  after night 3:", st(sid))

    # ── E: chaff eats a lift, dawn eats the harvester ────────────────
    print("\n=== E: chaff strands a rival ===")
    orbit(sid, "p1", [{"a": "build_chaff", "count": 1}])
    orbit(sid, "p2", [{"a": "build_harvester"}, {"a": "build_probe", "count": 2}])
    print("  after orbit 4:", st(sid), "| p2 fleet:", harvs(sid, "p2"))

    their = [h for h in harvs(sid, "p2") if h["orbit"]]
    if not their:
        print("  !! rival has nothing to strand")
        return 1
    tid = their[0]["id"]
    # The rival hot-drops (last night's salvo took every eye they had),
    # works two squares, then reaches for the lifter on H05.
    night(sid, "p2", [
        {"a": "probe", "at": [4, 12]},
        {"a": "drop", "unit": tid, "at": [4, 12]},
        {"a": "step", "unit": tid, "to": [4, 11]},
        {"a": "step", "unit": tid, "to": [5, 11]},
        {"a": "pickup", "unit": tid},
    ])
    # Our flare goes up on H04 and smothers H04-H06 — their pickup is
    # queued for H05, so the lifter never comes.
    night(sid, "p1", [
        {"a": "wait"}, {"a": "wait"}, {"a": "wait"},
        {"a": "chaff_flare"},
    ])
    for c in captions(sid, 4):
        if any(k in c.upper() for k in
               ("CHAFF", "STRAND", "DESTROY", "AURORA", "LIFT", "PICKUP")):
            print("  ", c[:120])
    print("  rival fleet after:", harvs(sid, "p2"))
    print("  after night 4:", st(sid))
    return 0


if __name__ == "__main__":
    sys.exit(main())
