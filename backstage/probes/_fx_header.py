#!/usr/bin/env python3
"""Scratch harness: shoot the header strip and the new-game launcher.

Not a pytest — it drives a real browser against a running server and
leaves PNGs in ``reports/header/`` for eyeballing.

    python backstage/probes/_fx_header.py [--port 8021]

Covers the v1.22 header/launcher pass:
  * idle header (the NIGHTS spinner should be gone)
  * watcher header (no duplicated season name, no "fixtures" tickbox,
    EXTRACTED % visible without having to scrub first)
  * the launcher at 1 / 2 / 4 seats, and with a long custom player name
    (the case that used to wrap "P2 · YELLOW" onto two lines)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.request

OUT = pathlib.Path(__file__).resolve().parents[2] / "reports" / "header"


def _post(base: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def _seed_season(
    base: str, nights: int = 3, bots: int = 1, stop_after: int = 0,
) -> str:
    """Spawn a season and play it to the end, so the watcher has real frames.

    p1 is a *human* seat that submits nothing each night. That is the only
    way to advance a game over HTTP: there is no "next night" route, and
    the background bot worker is only kicked for slow (LLM/harness) seats,
    so an all-bot heuristic game just sits at day 1 forever. A human
    submit calls ``submit_policy(auto_fire_bots=True)``, which fires the
    rival and resolves the night.

    (An earlier version of this script POSTed to a ``/bots`` route that
    does not exist and swallowed the 404, leaving a day-1 season with zero
    frames — which looks exactly like the bug it was meant to check.)

    Because p1 never orders anything it also never SEES anything, so a
    two-seat season has exactly one seat with vision. Pass ``bots=2`` when
    the thing under test needs two seats that can actually see — otherwise
    a correct renderer looks broken.
    """
    seats = ["p1"] + [f"p{i}" for i in range(2, 2 + bots)]
    game = _post(base, "/api/game/new", {
        "width": 30, "height": 20, "season_day_cap": nights,
        "players": seats,
        "agents": {s: ("human" if s == "p1" else "red_harvest") for s in seats},
        "visibility_mode": "hidden",
    })
    sid = game["session_id"]
    last = None
    for _ in range(nights * 6 + 20):
        st = json.loads(urllib.request.urlopen(
            f"{base}/api/game/{sid}/status", timeout=30).read().decode())
        phase, day = st.get("phase"), st.get("day")
        if (day, phase) != last:
            print(f"  seeding: day {day} {phase}")
            last = (day, phase)
        if phase == "season_complete":
            return sid
        # v1.28 — `stop_after` leaves the season OPEN with real frames in
        # it. The extract-strip check needs that: the strip is SUPPOSED to
        # appear once a season is over (nothing left to leak — the endgame
        # card publishes every score), so a played-out fixture cannot tell
        # a correct gate from an absent one.
        if stop_after and int(day or 0) > stop_after:
            return sid
        if phase == "orbit":
            _post(base, f"/api/game/{sid}/orbit",
                  {"player": "p1", "actions": []})
        else:
            _post(base, f"/api/game/{sid}/policy",
                  {"player": "p1", "moves": []})
        time.sleep(0.4)
    raise SystemExit(f"season {sid} never finished (stuck at {last})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8021)
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("FAIL: pip install playwright && playwright install chromium")
        return 2

    OUT.mkdir(parents=True, exist_ok=True)
    sid = _seed_season(base)
    print(f"seeded season {sid}")

    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1440, "height": 900})

        # ── idle header + launcher ────────────────────────────────────
        pg.goto(f"{base}/play", wait_until="networkidle")
        pg.wait_for_timeout(600)
        assert pg.query_selector("#new-game-cap") is None, \
            "NIGHTS spinner is still in the header"
        pg.locator("header.cc-header").screenshot(
            path=str(OUT / "header_idle.png"))

        pg.click("#new-game-btn")
        pg.wait_for_timeout(500)
        panel = pg.locator(".cc-newgame-modal-panel")
        panel.screenshot(path=str(OUT / "launcher_2seat.png"))
        print("summary:", pg.inner_text("#ngm-summary"))

        pg.click('#ngm-seat-count button[data-count="4"]')
        pg.wait_for_timeout(300)
        panel.screenshot(path=str(OUT / "launcher_4seat.png"))
        print("summary 4:", pg.inner_text("#ngm-summary"))

        # A long name is what used to fold the seat id onto two lines.
        pg.fill('.cc-newgame-seat[data-seat="p2"] .cc-newgame-cust-name',
                "Bartholomew")
        # Click the LABEL, not the input: the radio is visually hidden by
        # design (the marker is drawn in ASCII on the label text), so the
        # label is the only real hit target — same as for a user.
        pg.click('.cc-newgame-visopt:has(input[value="open"])')
        pg.wait_for_timeout(300)
        panel.screenshot(path=str(OUT / "launcher_longname.png"))
        print("summary open:", pg.inner_text("#ngm-summary"))

        pg.click('#ngm-seat-count button[data-count="1"]')
        pg.wait_for_timeout(300)
        panel.screenshot(path=str(OUT / "launcher_1seat.png"))
        print("summary 1:", pg.inner_text("#ngm-summary"))

        # ── watcher header ───────────────────────────────────────────
        pg.goto(f"{base}/play?session={sid}&watch=1", wait_until="networkidle")
        pg.wait_for_timeout(2500)
        hdr = pg.locator("header.cc-header")
        hdr.screenshot(path=str(OUT / "header_watch.png"))

        strip_shown = pg.eval_on_selector(
            "#cc-extract-strip", "el => !el.hidden")
        pct = pg.inner_text("#cc-extract-pct")
        name_dupe = pg.eval_on_selector(
            "#cc-season-name", "el => !el.hidden")
        fixtures_in_header = pg.eval_on_selector_all(
            "header.cc-header #watch-show-fixtures", "els => els.length")
        meta_gone = pg.eval_on_selector_all(
            "#watch-picker-meta", "els => els.length")
        print(f"extract strip visible : {strip_shown}  ({pct})")
        print(f"season nameplate dupe : {name_dupe} (want False)")
        print(f"fixtures in header    : {fixtures_in_header} (want 0)")
        print(f"picker meta nodes     : {meta_gone} (want 0)")

        pg.locator("#cc-extract-strip").screenshot(
            path=str(OUT / "extract_strip.png"))

        # ── v1.28: the SAME season, from a LIVE SEAT, must show nothing ──
        #
        # The strip is omniscient three times over — the SEED regenerates
        # the board offline, RED ON MAP is a total you cannot see through
        # fog, and EXTRACTED is summed over every seat, so your own take
        # subtracts to your rival's.
        #
        # It was gated on `mainMapSource === "replay"`, which reads like
        # "only in replay" and is not: the night cinematic sets exactly
        # that on a live board, and so does scrubbing your own past
        # nights. Scrubbing is the cheaper of the two to drive, and it
        # exercises the identical state, so that is what this does.
        # `&player=p1` is load-bearing and not obvious: a bare
        # `?session=<id>` is ALREADY watch mode (see JOIN_MODE / WATCH_MODE
        # at the top of app.js — any session param without a seat is a
        # spectator). A fixture that omits it tests the watcher twice and
        # reports the strip as correctly visible, which is what the first
        # cut of this check did.
        open_sid = _seed_season(base, nights=4, stop_after=2)
        print(f"open season     : {open_sid}")
        pg.goto(f"{base}/play?session={open_sid}&player=p1",
                wait_until="networkidle")
        pg.wait_for_timeout(2500)
        live_phase = pg.evaluate(
            "() => document.getElementById('phase-line')?.textContent || '?'")
        live_hidden_idle = pg.eval_on_selector(
            "#cc-extract-strip", "el => el.hidden")
        # PLAY the replay rather than scrubbing to it. `updateExtractStrip`
        # only runs from `syncReplayRowOnly`, so a single scrub can set
        # `mainMapSource = "replay"` a beat AFTER the last strip update and
        # leave it stale-hidden — which reads as a pass and is why the
        # first cut of this check could not fail. The tick loop re-syncs
        # every frame, which is exactly what the live night cinematic does.
        scrubbed = False
        live_hidden_scrub = True
        if pg.query_selector("#replay-scrub"):
            # TWO scrubs, not one, and the second is the load-bearing one.
            # `updateExtractStrip` only runs from `syncReplayRowOnly`, and
            # the first scrub is what SETS `mainMapSource = "replay"` — so
            # its own sync had already read the old value. One scrub leaves
            # the strip stale-hidden, which reads as a pass however broken
            # the gate is (the first cut of this check did exactly that).
            for target in ("0", "max"):
                pg.evaluate(
                    """(t) => {
                      const s = document.getElementById('replay-scrub');
                      if (!s) return;
                      s.value = (t === 'max') ? s.max : t;
                      s.dispatchEvent(new Event('input', { bubbles: true }));
                      s.dispatchEvent(new Event('change', { bubbles: true }));
                    }""",
                    target,
                )
                pg.wait_for_timeout(900)
                if not pg.eval_on_selector(
                        "#cc-extract-strip", "el => el.hidden"):
                    live_hidden_scrub = False
            scrubbed = True
        live_src = pg.evaluate(
            "() => window.__SOC_VISION_DBG__?.source?.() ?? 'unknown'")
        pre = pg.evaluate("""() => {
          const D = window.__SOC_HEADER_DBG__;
          if (!D) return null;
          const x = D.extraction();
          return {
            hasData: !!(x && x.map_red_value > 0),
            mapSource: D.mapSource(),
            watchMode: D.watchMode(),
            livePhase: D.livePhase(),
          };
        }""")
        print(f"live seat precond     : {pre}")
        print(f"live seat phase       : {live_phase.strip()!r}")
        print(f"live seat, idle       : hidden={live_hidden_idle} (want True)")
        print(f"live seat, scrubbed   : hidden={live_hidden_scrub} "
              f"(want True; scrubbed={scrubbed}, map source={live_src})")
        pg.screenshot(path=str(OUT / "extract_live_hidden.png"))
        br.close()

    ok = strip_shown and not name_dupe and not fixtures_in_header \
        and not meta_gone and live_hidden_idle and live_hidden_scrub
    if not scrubbed:
        print("WARN: no #replay-scrub on the live seat — the leak path "
              "(map source flipping to 'replay' mid-game) went untested")
    print("PASS" if ok else "FAIL")
    print(f"shots -> {OUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
