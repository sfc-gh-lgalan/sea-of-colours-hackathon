#!/usr/bin/env python3
"""Scratch harness — the resolved board must never appear before the
animation that explains it.

THE BUG THIS PINS (v1.26). The server flips the status to "night
resolved" the moment the engine finishes, but the night's FRAMES are
still being written. A pull that means to animate fetches the replay a
beat later, gets a day that is still behind, concludes there is nothing
new to play, and paints the resolved board. The frames land seconds
afterwards and the NEXT pull animates a night whose outcome is already
on screen. Reported as "PRAXIS BEGINS still triggers after showing the
full board end state ... it kills the mood".

HOW IT IS REPRODUCED. The lag is a property of the STORE, so a
memory-backed game never shows it and a Snowflake one shows it only
sometimes — neither is testable. Instead the replay RESPONSE is
intercepted in the browser and the newest night's frames are stripped
out of it, which is exactly the shape the client sees mid-write. The
strip is then lifted, standing in for the frames landing.

TWO SESSIONS, because an assertion about the lagged run is worthless
without evidence the harness can see a cinematic at all: CONTROL runs a
night untouched, LAGGED runs the same night behind the strip. Both must
animate; only the lagged one should have waited.

WHY THE SUBMIT IS A CLICK. `startLiveSync` — the poller that spots a
resolve — is only started for multi-human or joined sessions, so a
solo game driven by HTTP posts never animates anything and every
assertion here passes or fails for the wrong reason. Clicking TRANSMIT
goes through the submit path, which against a fast bot resolves the
night in the response.

WHAT IS ASSERTED. That the lagged pull WAITED and then PLAYED rather
than skipping. `played: true` is sufficient to prove the reveal did not
come first: the paint lives in the `else` of the same branch, so one
pull cannot both animate and pre-reveal.

Run against a scratch server you own:
    python run_web.py --port 8022 --replace &
    python backstage/probes/_fx_reveal.py --base http://127.0.0.1:8022
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[2] / "reports" / "reveal"


def _post(base: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _new_game(base: str) -> str:
    game = _post(base, "/api/game/new", {
        "width": 30, "height": 20, "season_day_cap": 6,
        "players": ["p1", "p2"],
        "agents": {"p1": "human", "p2": "red_harvest"},
        "visibility_mode": "hidden",
    })
    return game["session_id"]


def _summarise(log: list[dict]) -> list[str]:
    out = []
    for e in log:
        keep = {
            k: e.get(k) for k in (
                "replayLastDay", "statusDay", "played", "expectNightFrames",
                "framesWaitedMs", "framesArrived", "skippedBecause", "stage",
                "detail",
            )
            if e.get(k) not in (None, "")
        }
        out.append(json.dumps(keep))
    return out


def _run_night(base: str, br, lag_ms: int, label: str) -> dict:
    """Play one night and return what the cinematic recorder saw.

    ``lag_ms`` > 0 strips the newest night's frames out of every replay
    response for that long, then lets the real payload through.
    """
    sid = _new_game(base)
    lagging = {"on": lag_ms > 0, "hits": 0}

    def handle_replay(route) -> None:
        if not lagging["on"]:
            route.continue_()
            return
        try:
            resp = route.fetch()
            body = resp.json()
        except Exception:
            route.continue_()
            return
        frames = body.get("frames") or []
        if frames:
            newest = max(int(f.get("day") or 0) for f in frames)
            body["frames"] = [
                f for f in frames if int(f.get("day") or 0) < newest
            ]
            lagging["hits"] += 1
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(body))

    pg = br.new_page(viewport={"width": 1280, "height": 900})
    pg.route("**/replay*", handle_replay)
    pg.goto(f"{base}/?session={sid}&player=p1", wait_until="networkidle")
    pg.wait_for_timeout(2000)
    pg.evaluate("() => { window._socCinematicLog = []; }")

    pg.locator("#solo-commit-night").click()
    if lag_ms:
        pg.wait_for_timeout(lag_ms)
        lagging["on"] = False
    # Long enough for the frame wait (<=9s) plus the cinematic itself.
    pg.wait_for_timeout(22000)

    log = pg.evaluate("() => window._socCinematicLog || []")
    pg.screenshot(path=str(OUT / f"{label}.png"))
    pg.close()
    return {"sid": sid, "log": log, "stripped": lagging["hits"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8022")
    ap.add_argument("--lag-ms", type=int, default=3000)
    args = ap.parse_args()
    base = args.base.rstrip("/")
    OUT.mkdir(parents=True, exist_ok=True)

    fails: list[str] = []
    with sync_playwright() as p:
        br = p.chromium.launch()

        control = _run_night(base, br, 0, "control")
        print(f"CONTROL {control['sid'][:8]} — no lag")
        for line in _summarise(control["log"]):
            print("   " + line)
        if not [e for e in control["log"] if e.get("played")]:
            fails.append(
                "the CONTROL night did not animate with nothing injected, "
                "so this harness cannot observe a cinematic and the lagged "
                "result below means nothing"
            )

        lagged = _run_night(base, br, args.lag_ms, "lagged")
        print(f"\nLAGGED  {lagged['sid'][:8]} — "
              f"{lagged['stripped']} replay responses stripped")
        for line in _summarise(lagged["log"]):
            print("   " + line)
        br.close()

    log = lagged["log"]
    if not lagged["stripped"]:
        fails.append(
            "no replay response was stripped, so the lag was never "
            "simulated — check the route glob"
        )
    if not [e for e in log if (e.get("framesWaitedMs") or 0) > 0]:
        fails.append(
            "no pull waited for the withheld frames — the reveal-order "
            "guard did not engage (expectNightFrames unset, or the replay "
            "was never behind the status)"
        )
    if not [e for e in log if e.get("played")]:
        fails.append(
            "the lagged night never animated: the board was revealed and "
            "PRAXIS BEGINS was skipped — the reported bug"
        )
    bailed = [e for e in log if e.get("stage")]
    if bailed:
        fails.append(f"a cinematic bailed mid-play: {bailed[0].get('detail')}")

    print()
    if fails:
        for f in fails:
            print(f"FAIL: {f}")
        return 1
    print(f"shots in {OUT}")
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
