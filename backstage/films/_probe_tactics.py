#!/usr/bin/env python3
"""Scratch: prove the three tactical nights before choreographing them.

The advanced films moved from "here is a weapon" to "here is a move you
would actually make", and the moves have sharp edges the engine will not
warn you about:

  A. SMASH AND GRAB — drop straight onto a pure you can see, lift the
     next hour. Claim: one hour banks 765 (255 x 3.0), which is 42% of
     the whole six-parcel comb, and the hold comes home 1/6 full.
  B. BLIND AND GRAB — EMP the probe that lit the beacon, then probe,
     land blind and comb five squares of the approximate area. Claim:
     their eye goes out, our comb is OUTSIDE our own cloud and therefore
     still harvests, and the probe reveals the exact seam for tomorrow.
  C. THE WALK-IN — salvo the rival's jackpot, park a harvester one
     square outside the blast, wait out our own cloud, and step onto the
     pure on the first hour it is clear. Claim: the harvests land
     because we waited, and the last one is their 255.

Every one of those is a claim about hour arithmetic, and getting it
wrong costs a 60-second shoot to find out. Ask the engine instead.

    SOC_BACKEND=memory python run_web.py --no-reload --port 8022 --replace
    python backstage/films/_probe_tactics.py
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backstage.films.make_tutorial_films import (  # noqa: E402
    ADV_BLIND_COMB, ADV_BLIND_EYE, ADV_BLIND_LAND, ADV_BLIND_SALVO,
    ADV_BESIDE, ADV_MINE, ADV_SALVO, ADV_THEIRS, ADV_WALK_IN,
    _setup_advanced, harvester_ids, submit_night, view,
)

BASE = "http://127.0.0.1:8022"


def hold_of(base, sid, seat="p1"):
    inv = view(base, sid, seat).get("inventory") or {}
    return {k: inv.get(k) for k in
            ("credits", "hoard", "hoard_count", "red_purity", "blue_purity")}


def harvs(base, sid, seat="p1"):
    return [{k: u.get(k) for k in ("id", "pos", "orbit", "damaged")}
            for u in view(base, sid, seat)["units"]
            if u.get("type") == "harvester"]


def probes(base, sid, seat):
    return [tuple(u.get("pos") or []) for u in view(base, sid, seat)["units"]
            if u.get("type") == "probe"]


def log_lines(base, sid, day, want):
    import urllib.request
    with urllib.request.urlopen(
            f"{base}/api/game/{sid}/replay?player=p1", timeout=90) as r:
        rep = json.loads(r.read().decode())
    out = []
    for f in rep.get("frames") or []:
        if int(f.get("day") or 0) != day:
            continue
        cap = str(f.get("caption") or "")
        if cap and any(w.lower() in cap.lower() for w in want):
            out.append(f"h{f.get('hour')}: {cap[:120]}")
    return out


def scenario(name, moves, note):
    print(f"\n{'=' * 68}\n{name}\n{'=' * 68}")
    sid, plan = _setup_advanced(BASE, "adv_emp")   # night 3, board ready
    before = hold_of(BASE, sid)
    print(f"  session {sid}")
    print(f"  before: {json.dumps(before)}")
    print(f"  their probes: {probes(BASE, sid, 'p2')}")

    hid = harvester_ids(BASE, sid, "p1")[0]
    q = moves(hid)
    print(f"  queue ({len(q)} slots): "
          + " ".join(m.get("a", "?")[:5] for m in q))
    r = submit_night(BASE, sid, q, "p1")
    if not r.get("ok", True):
        print(f"  !! policy refused: {r}")
        return
    submit_night(BASE, sid, [{"a": "probe", "at": list(ADV_THEIRS)}], "p2")

    after = hold_of(BASE, sid)
    print(f"  after : {json.dumps(after)}")
    print(f"  their probes now: {probes(BASE, sid, 'p2')}")
    print(f"  our harvesters  : {harvs(BASE, sid)}")
    for line in log_lines(BASE, sid, 3, note):
        print(f"    {line}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    globals()["BASE"] = args.base

    want = ["harvest", "denied", "destroy", "emp", "lift", "orblift",
            "pure", "pick", "chaff", "probe"]

    # ── A: smash and grab ────────────────────────────────────────────
    scenario(
        "A. SMASH AND GRAB — land on the pure, leave with it",
        lambda h: [
            {"a": "drop", "unit": h, "at": list(ADV_MINE)},
            {"a": "pickup", "unit": h},
        ],
        want,
    )

    # ── B: blind and grab ────────────────────────────────────────────
    scenario(
        "B. BLIND AND GRAB — put the eye out, comb the guess",
        lambda h: [
            {"a": "emp_launch", "at": [list(c) for c in ADV_BLIND_SALVO]},
            {"a": "probe", "at": list(ADV_BLIND_EYE)},
            {"a": "drop", "unit": h, "at": list(ADV_BLIND_LAND)},
        ] + [{"a": "step", "unit": h, "to": list(c)} for c in ADV_BLIND_COMB]
          + [{"a": "pickup", "unit": h}],
        want,
    )

    # ── C: the walk-in ───────────────────────────────────────────────
    scenario(
        "C. THE WALK-IN — wait out your own cloud, then take their seam",
        lambda h: [
            {"a": "emp_launch", "at": [list(c) for c in ADV_SALVO]},
            {"a": "probe", "at": list(ADV_BESIDE)},
            {"a": "drop", "unit": h, "at": list(ADV_BESIDE)},
        ] + [{"a": "wait"}] * 5
          + [{"a": "step", "unit": h, "to": list(c)} for c in ADV_WALK_IN]
          + [{"a": "pickup", "unit": h}],
        want,
    )

    # ── C2: the walk-in as the film actually queues it ───────────────
    # Same hours, different queue. The film puts the waits in FRONT of
    # the landing so that drop/walk/lift stay one picker session in the
    # ORDERS panel; that only works if the late landing is still legal
    # (the probe from hour two has to still be alive to make the square
    # droppable at hour eight). Cheaper to find out here.
    scenario(
        "C2. THE WALK-IN, WAITS FIRST — the order the film clicks",
        lambda h: [
            {"a": "emp_launch", "at": [list(c) for c in ADV_SALVO]},
            {"a": "probe", "at": list(ADV_BESIDE)},
        ] + [{"a": "wait"}] * 5
          + [{"a": "drop", "unit": h, "at": list(ADV_BESIDE)}]
          + [{"a": "step", "unit": h, "to": list(c)} for c in ADV_WALK_IN]
          + [{"a": "pickup", "unit": h}],
        want,
    )

    # ── C': the same walk-in, done impatiently ───────────────────────
    # The control. If walking straight in banks the same as waiting,
    # the whole lesson is invented and the film should not be made.
    scenario(
        "C'. CONTROL — the same walk with no wait, straight into the cloud",
        lambda h: [
            {"a": "emp_launch", "at": [list(c) for c in ADV_SALVO]},
            {"a": "probe", "at": list(ADV_BESIDE)},
            {"a": "drop", "unit": h, "at": list(ADV_BESIDE)},
        ] + [{"a": "step", "unit": h, "to": list(c)} for c in ADV_WALK_IN]
          + [{"a": "pickup", "unit": h}],
        want,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
