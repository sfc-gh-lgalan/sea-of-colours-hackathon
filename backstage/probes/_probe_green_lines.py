"""BUG-12: does the station score include GREEN, and does it say so?

Two assertions, because the bug was two things wearing one coat.

**The number.** `_osGetCumulativeScore(day)` drives every station readout.
It used to sum the night's shipped RED and nothing else, so a seat that
dumped GREEN read high for the whole replay and only reconciled at the
closing snap. This walks every day of a finished season and checks the
running total against the engine's own `cumulative_shipped_score`, which
has always had the debit folded in.

**The line.** A `-N green` line must appear under the seat at the launch
that charged it — the `-100` the tracker says no player has ever seen.

Expectations are derived from the replay payload, not typed in, so this
works against any finished season.

    python backstage/probes/_probe_green_lines.py --session <id>
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "films"))
# A finished season opens on a seat picker and then on the endgame card,
# and the page does not fetch `/replay` until the picker is answered — so
# a probe that just navigates sees a permanently disabled strip and
# concludes, wrongly, that the accessors are missing. The film rig already
# solves all of this; borrow it rather than re-learn it.
from make_zoomout_clips import open_season  # noqa: E402


def fetch(base: str, sid: str) -> dict:
    with urllib.request.urlopen(f"{base}/api/game/{sid}/replay", timeout=120) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8023")
    ap.add_argument("--session", default="c958be9f5db5433a8bed14e6856c0fda")
    ap.add_argument("--seat", default="p1")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()

    payload = fetch(a.base, a.session)
    cum_engine = payload.get("cumulative_shipped_score") or {}
    cat = payload.get("catapult_by_day") or {}
    if not cat:
        print("no catapult_by_day on this season — nothing to check")
        return 1

    # Rebuild the truth per day straight from the wire: RED shipped less the
    # GREEN charged at the same orbit.
    days = sorted(int(d) for d in cat)
    want: dict[int, dict[str, float]] = {}
    green_days: dict[int, dict[str, int]] = {}
    run: dict[str, float] = {}
    for d in days:
        blob = cat[str(d)]
        for seat, sb in ((blob.get("catapult") or {}).get("seats") or {}).items():
            run[seat] = run.get(seat, 0.0) + float(sb.get("score_shipped") or 0.0)
        g = {}
        for seat, jb in ((blob.get("jettison") or {}).get("seats") or {}).items():
            pen = int(jb.get("penalty") or 0)
            if pen:
                run[seat] = run.get(seat, 0.0) - pen
                g[seat] = pen
        want[d] = dict(run)
        green_days[d] = g

    charged = {d: g for d, g in green_days.items() if g}
    print(f"season {a.session[:8]} · days {days[0]}..{days[-1]} · "
          f"{len(charged)} night(s) charged GREEN: "
          f"{ {d: g for d, g in charged.items()} }")
    if not charged:
        print("this season never dumped GREEN — pick another to prove the line")
        return 1

    fails: list[str] = []
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=not a.headed)
        pg = br.new_context(viewport={"width": 1440, "height": 900}).new_page()
        open_season(pg, a.base, a.session, a.seat)
        try:
            pg.wait_for_function(
                "() => typeof window._osGetCumulativeScore === 'function'",
                timeout=60_000)
        except Exception:
            have = pg.evaluate(
                "() => Object.keys(window).filter((k) => k.startsWith('_os'))")
            print(f"_osGetCumulativeScore never appeared. _os* globals: {have}")
            br.close()
            return 1

        # ── 1. the number ────────────────────────────────────────────────
        got = pg.evaluate(
            "(ds) => Object.fromEntries(ds.map("
            "  (d) => [d, window._osGetCumulativeScore(d)]))", days)
        print(f"\n{'day':>4} {'seat':5} {'expected':>10} {'station':>10} {'green':>7}")
        for d in days:
            for seat in sorted(want[d]):
                exp = round(want[d][seat])
                act = round(float((got.get(str(d)) or {}).get(seat) or 0.0))
                g = green_days[d].get(seat, 0)
                flag = "" if abs(exp - act) <= 1 else "   <-- WRONG"
                if flag:
                    fails.append(f"day {d} {seat}: station {act}, expected {exp}")
                print(f"{d:>4} {seat:5} {exp:>10} {act:>10} "
                      f"{('-' + str(g)) if g else '':>7}{flag}")

        final_day = days[-1]
        for seat, v in cum_engine.items():
            act = float((got.get(str(final_day)) or {}).get(seat) or 0.0)
            if abs(float(v) - act) > 1.5:
                fails.append(
                    f"end of season {seat}: station {act:.0f} vs engine "
                    f"cumulative_shipped_score {float(v):.0f}")

        # ── 2. the line ──────────────────────────────────────────────────
        # Drive the station renderer straight at a charged night rather than
        # playing the whole season to reach it.
        day = sorted(charged)[0]
        shown = pg.evaluate(
            "(d) => { if (typeof window._osGetDayGreenPenalty !== 'function')"
            "   return null; return window._osGetDayGreenPenalty(d); }", day)
        if not shown:
            fails.append("_osGetDayGreenPenalty missing or empty — the station "
                         "cannot draw a line it cannot look up")
        else:
            print(f"\nday {day} green charge exposed to the station: {shown}")
            if shown != charged[day]:
                fails.append(f"day {day} penalty {shown} != wire {charged[day]}")

        # ── 3. the line, on screen ───────────────────────────────────────
        # The accessor existing is not the bug being fixed; a player seeing
        # the charge is. Play from the top and watch for the line to be
        # pushed at a launch. Polled rather than waited on a single
        # selector, because the stack is wiped by the following tick.
        pg.evaluate(
            "() => { const s = document.getElementById('replay-scrub');"
            "  s.value = '0';"
            "  s.dispatchEvent(new Event('input', {bubbles: true})); }")
        pg.wait_for_timeout(800)
        pg.evaluate("() => document.getElementById('replay-play').click()")
        seen_line = None
        for _ in range(240):                       # ~2 minutes of playback
            seen_line = pg.evaluate(
                "() => { const e = document.querySelector('.os-settle-line--green');"
                "  return e ? e.textContent : null; }")
            if seen_line:
                break
            pg.wait_for_timeout(500)
        if seen_line:
            print(f"green line rendered under a station: {seen_line!r}")
        else:
            fails.append("no .os-settle-line--green ever appeared during "
                         "playback — the charge is in the score but still "
                         "unexplained on screen")

        br.close()

    print()
    if fails:
        print(f"FAIL ({len(fails)})")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PASS — station running score is net of GREEN, and the per-night "
          "charge is reachable for the line")
    return 0


if __name__ == "__main__":
    sys.exit(main())
