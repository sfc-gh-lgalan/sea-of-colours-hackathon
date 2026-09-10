"""Rival LAUNCHES and RECOVERIES must show on their orbital platform even
when the board stays dark.

RULEBOOK §3.15 splits a harvester deployment in two. The landing CELL is
private. The platform ACTIVITY is not — how many harvesters a House
launched and recovered on a Nox, and whether the recovered ones came home
loaded or damaged, is printed for every seat in the Pre-Orbital Recap.
Before v1.28 the client honoured only the first half: one gate in
``runReplayAnimationsTick`` suppressed the entire delta, so a rival's
night in fog left their station panels dead and the recap that followed
then reported four launches nobody had seen.

The harness runs ONE season twice, from two perspectives, and compares:

  OBS   — control. Every rival action animates on the board AND on the
          station. Proves the probes below can actually observe an
          orbital arc, so a zero in the P1 pass means something.
  P1    — the case. p1 is a human seat that orders nothing all season,
          so it sees nothing and EVERY p2 action is fogged.

The claim under test is an equality, not a presence: P1 must receive the
same number of platform launches and recoveries as OBS, while drawing
ZERO orbital arcs on the board. One number proves the intel arrives, the
other proves the coordinate did not come with it.

Scratch harness, like the other ``backstage/probes/_fx_*.py`` — not part of pytest.

    python backstage/probes/_fx_orbitpublic.py --base http://127.0.0.1:8022
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[2] / "reports" / "orbitpublic"


def _post(base: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def _seed_season(base: str, nights: int = 3) -> str:
    """Resolve `nights` nights and STOP, leaving the season unfinished.

    p1 is a human seat submitting empty policies — the only way to
    advance a game over HTTP (there is no "next night" route, and the
    background bot worker is only kicked for slow LLM seats). A human
    submit calls ``submit_policy(auto_fire_bots=True)``, which fires p2
    and resolves the night.

    Ordering nothing also means p1 SEES nothing, which here is the point
    rather than a limitation: it makes every single p2 drop and pickup
    fogged, so the P1 pass has no ambiguous cases.

    The season is deliberately left OPEN. Playing it to the cap pops the
    victory sweep and the endgame score card over the whole page, and
    both sit above the replay transport — `[ OBS ]` is visible, enabled
    and completely unclickable, which Playwright reports as a 30s
    timeout on a button it can see. The replay frames are identical
    either way, so stopping early is cheaper than dismissing them.
    """
    cap = nights + 2
    game = _post(base, "/api/game/new", {
        "width": 30, "height": 20, "season_day_cap": cap,
        "players": ["p1", "p2"],
        "agents": {"p1": "human", "p2": "red_harvest"},
        "visibility_mode": "hidden",
    })
    sid = game["session_id"]
    last = None
    for _ in range(nights * 8 + 20):
        st = json.loads(urllib.request.urlopen(
            f"{base}/api/game/{sid}/status", timeout=30).read().decode())
        phase, day = st.get("phase"), st.get("day")
        if (day, phase) != last:
            print(f"  seeding: day {day} {phase}")
            last = (day, phase)
        if phase == "season_complete":
            return sid
        if int(day or 0) > nights:
            return sid
        if phase == "orbit":
            _post(base, f"/api/game/{sid}/orbit",
                  {"player": "p1", "actions": []})
        else:
            _post(base, f"/api/game/{sid}/policy",
                  {"player": "p1", "moves": []})
        time.sleep(0.35)
    raise SystemExit(f"season {sid} never got going (stuck at {last})")


# Wrap the station hook and watch the board for orbital arcs. Installed
# fresh before each pass so the two perspectives are counted separately.
#
# The board probe is a MutationObserver rather than a poll: an orbital arc
# is a `position: fixed` ghost that lives ~1100ms and removes itself, so a
# sampling loop would miss it between frames and read as the clean PASS
# this harness exists to distinguish from a real one.
_INSTRUMENT = """
() => {
  window.__FX_STATION__ = [];
  window.__FX_ARCS__ = [];
  window.__FX_MINEARCS__ = [];
  if (!window.__FX_WRAPPED__) {
    const inner = window.osOnEntityArrival;
    window.__FX_WRAPPED__ = true;
    window.osOnEntityArrival = function (delta, targetCell) {
      try {
        window.__FX_STATION__.push({
          kind: delta && delta.kind,
          owner: delta && delta.owner,
          // The recovery glyph's fill and tint. Recorded explicitly
          // rather than inferred from `keys`, which only proves the
          // field was PRESENT — `loaded: undefined` still lists.
          loaded: !!(delta && delta.loaded),
          damaged: !!(delta && delta.damaged),
          keys: delta ? Object.keys(delta).sort() : [],
          // The whole safety argument rests on this being null: a
          // station glyph that received a cell could aim at it.
          gotCell: targetCell != null,
        });
      } catch (_e) { /* never break playback to observe it */ }
      return inner ? inner.apply(this, arguments) : undefined;
    };
  }
  const obs = new MutationObserver((recs) => {
    for (const r of recs) {
      for (const n of r.addedNodes) {
        if (n.nodeType !== 1) continue;
        const cl = n.classList;
        if (!cl) continue;
        if (cl.contains("replay-anim-ghost--orbital")) {
          window.__FX_ARCS__.push({ cls: n.className });
        }
        // The minelayer sweeps the board on its own craft element, not
        // an orbital ghost, so the arc counter above cannot see it.
        if (cl.contains("minelayer-craft")) {
          window.__FX_MINEARCS__.push({ cls: n.className });
        }
      }
    }
  });
  obs.observe(document.body, { childList: true, subtree: true });
  window.__FX_OBS__ = obs;
}
"""

_RESET = """
() => {
  window.__FX_STATION__ = [];
  window.__FX_ARCS__ = [];
  window.__FX_MINEARCS__ = [];
}
"""

# v1.31 — the caltrop is RETIRED, so no season can produce this frame
# any more. That makes the injection below the only remaining coverage
# of the archived-season playback path, and this block worth keeping
# rather than deleting along with the weapon: `mine_lay` frames recorded
# before v1.31 still arrive at the client, and `playMineFx` /
# `station.js` still have to draw them.
#
# (It was already injected before the retirement, for a different
# reason: the harness's p1 sits idle all season so it never banks the
# BLUE to buy one, and RED_HARVEST never laid one in any seeded season
# measured.)
#
# Same route-interception technique as `_fx_reveal.py`. It tests the real
# `playMineFx` against the real `station.js` — only the provenance of the
# event is synthetic.
_MINE_AT = (2, 2)


def _install_mine_injector(pg, day_index: int = 1) -> dict:
    """Rewrite `/replay` so ONE frame carries a p2 mine-lay."""
    state = {"injected": 0, "frames": 0}

    def handler(route):
        resp = route.fetch()
        try:
            body = resp.json()
        except Exception:
            route.fulfill(response=resp)
            return
        frames = body.get("frames")
        if isinstance(frames, list) and len(frames) > day_index:
            f = frames[day_index]
            if isinstance(f, dict):
                f["mine"] = list(f.get("mine") or []) + [{
                    "kind": "mine_lay",
                    "at": list(_MINE_AT),
                    "owner": "p2",
                }]
                state["injected"] += 1
                state["frames"] = len(frames)
        route.fulfill(response=resp, body=json.dumps(body),
                      headers={**resp.headers, "content-type":
                               "application/json"})

    pg.route("**/replay*", handler)
    return state


def _play_through(pg, label: str) -> dict:
    """Autoplay the whole replay and return what each probe saw.

    Both passes MUST reach the final tick or the two are not counting the
    same material. The first cut of this deadline was sized off the tick
    count and quietly cut the OBS pass short: OBS animates the arcs, and
    an arc extends the dwell by its launch lead (`_OS_LEAD_MS`, up to
    2.6s a tick), so the perspective with MORE to draw is the one that
    runs out of time — and it under-reported, in the direction that makes
    the comparison pass.
    """
    n = int(pg.evaluate(
        "() => Number(document.getElementById('replay-scrub')?.max) || 0"))
    # Rewind, then hand over to the transport's own [ play ] — the tick
    # loop is what calls paintReplayFrameOntoMain("forward"), and only
    # "forward" runs animations. Scrubbing is a "jump" and would silently
    # animate nothing.
    pg.evaluate("""() => {
      const s = document.getElementById('replay-scrub');
      if (!s) return;
      s.value = '0';
      s.dispatchEvent(new Event('input', { bubbles: true }));
      s.dispatchEvent(new Event('change', { bubbles: true }));
    }""")
    pg.wait_for_timeout(500)
    pg.evaluate(_RESET)
    pg.click("#replay-play")
    # The button's LABEL is the playback state ("[ pause ]" while the
    # ticker is alive); it carries no state class, so a class check reads
    # "stopped" forever and the loop exits on the first poll.
    deadline = time.time() + (n + 1) * 4.0 + 30.0
    finished = False
    idx = 0
    while time.time() < deadline:
        pg.wait_for_timeout(400)
        idx = pg.evaluate(
            "() => Number(document.getElementById('replay-scrub')?.value) || 0")
        label_now = pg.evaluate(
            "() => (document.getElementById('replay-play')?.textContent || '')"
            ".trim()")
        if label_now == "[ play ]" and idx >= n:
            finished = True
            break
    pg.wait_for_timeout(1500)
    station = pg.evaluate("() => window.__FX_STATION__")
    arcs = pg.evaluate("() => window.__FX_ARCS__")
    minearcs = pg.evaluate("() => window.__FX_MINEARCS__ || []")
    print(f"\n=== {label} === (played {idx + 1}/{n + 1} ticks"
          f"{'' if finished else ', DID NOT FINISH'})")
    tally: dict[str, int] = {}
    for e in station:
        tally[f"{e['owner']}:{e['kind']}"] = tally.get(
            f"{e['owner']}:{e['kind']}", 0) + 1
    for k in sorted(tally):
        print(f"   station {k:<18} x{tally[k]}")
    print(f"   board orbital arcs : {len(arcs)}")
    return {
        "station": station, "arcs": arcs, "minearcs": minearcs,
        "ticks": n + 1, "finished": finished,
    }


def _count(station: list, owner: str, *kinds: str) -> int:
    return sum(1 for e in station
               if e["owner"] == owner and e["kind"] in kinds)


def _public_tally(base: str, sid: str) -> dict:
    """What the ENGINE says is public about each platform, per season.

    This is the oracle, and it is deliberately not the OBS pass.
    `orbital_activity_by_day` is the same structure
    (`session.tally_orbital_activity`) that fills the Pre-Orbital Recap,
    so asserting against it is asserting "the animation shows exactly
    what the report already prints" rather than "the animation shows
    whatever the observer view happened to draw".

    That distinction earned its keep immediately: the OBS pass reports
    ONE recovery where the engine records two. A pickup's station glyph
    is fired at the end of the board arc, so when the next tick cancels
    the arc in flight the glyph is lost with it. Comparing the two
    perspectives would have blamed the fogged seat for the observer's
    dropped frame.
    """
    d = json.loads(urllib.request.urlopen(
        f"{base}/api/game/{sid}/replay", timeout=90).read().decode())
    out: dict[str, int] = {}
    for _day, by_seat in (d.get("orbital_activity_by_day") or {}).items():
        for seat, t in (by_seat or {}).items():
            for k in ("dropped", "recovered", "recovered_carrying",
                      "recovered_damaged", "probes", "mines", "emps",
                      "chaff"):
                out[f"{seat}:{k}"] = out.get(f"{seat}:{k}", 0) + int(t.get(k, 0))
    return out


# Every public column in `tally_orbital_activity`, paired with the station
# glyph that must answer for it. The whole rule in one table: if the recap
# prints a number for a rival, their platform has to have shown it.
_PUBLIC_COLUMNS = [
    ("dropped", ("drop", "drop_bounce"), "launches"),
    ("recovered", ("pickup",), "recoveries"),
    ("probes", ("probe_emit",), "probes"),
    ("mines", ("mine_emit",), "minelayers"),
    ("emps", ("emp_launch",), "EMPs"),
    ("chaff", ("chaff_flare",), "chaff"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8022")
    ap.add_argument("--session", default="")
    ap.add_argument("--nights", type=int, default=3)
    args = ap.parse_args()
    base = args.base.rstrip("/")
    OUT.mkdir(parents=True, exist_ok=True)

    sid = args.session or _seed_season(base, args.nights)
    print(f"session {sid}")

    fails: list[str] = []
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1500, "height": 950})
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(f"{base}/?session={sid}&watch=1", wait_until="networkidle")
        pg.wait_for_timeout(3500)

        if not pg.query_selector("#replay-scrub"):
            print("FAIL: no #replay-scrub — the season produced no frames")
            br.close()
            return 1
        pg.evaluate(_INSTRUMENT)

        # --- pass 1: OBS, the control -----------------------------------
        pg.click("#replay-view-obs")
        pg.wait_for_timeout(500)
        obs = _play_through(pg, "OBS (control)")
        pg.screenshot(path=str(OUT / "obs.png"))

        # --- pass 2: P1, the seat that can see nothing -------------------
        p1btn = pg.query_selector('.cc-replay-view-btn[data-seat="p1"]') \
            or pg.query_selector('button.cc-replay-view-btn:not(#replay-view-obs)')
        if p1btn is None:
            print("FAIL: no per-seat perspective button to switch to")
            br.close()
            return 1
        p1btn.click()
        pg.wait_for_timeout(500)
        seat = pg.evaluate(
            "() => document.querySelector('.cc-replay-view-btn--active')"
            "?.textContent?.trim() || '?'")
        print(f"\nswitched perspective to {seat}")
        p1 = _play_through(pg, "P1 (rival fogged)")
        pg.screenshot(path=str(OUT / "p1.png"))

        # --- pass 3: the same seat, with one p2 mine-lay injected --------
        # Mines were the only weapon with no station glyph AT ALL, so
        # unlike the columns above there is no pre-v1.28 behaviour to
        # compare against — the branch is new code, and no seeded season
        # has ever exercised it.
        inj = _install_mine_injector(pg)
        pg.reload(wait_until="networkidle")
        pg.wait_for_timeout(3500)
        pg.evaluate(_INSTRUMENT)
        mb = pg.query_selector('.cc-replay-view-btn[data-seat="p1"]') \
            or pg.query_selector(
                'button.cc-replay-view-btn:not(#replay-view-obs)')
        if mb is not None:
            mb.click()
            pg.wait_for_timeout(500)
        mine = _play_through(pg, "P1 + injected p2 mine")
        pg.screenshot(path=str(OUT / "p1_mine.png"))
        print(f"   injected into {inj['injected']} replay response(s), "
              f"{inj['frames']} frames each")

        br.close()

    truth = _public_tally(base, sid)

    # (0) Neither pass may be truncated, or every count below is measuring
    #     a different length of season.
    for name, r in (("OBS", obs), ("P1", p1)):
        if not r["finished"]:
            fails.append(
                f"{name} playback did not reach the last tick — its counts "
                f"are a partial season and prove nothing"
            )

    # (1) THE FIX, checked column by column against the engine's own
    #     definition of what is public. Before v1.28 `dropped` and
    #     `recovered` read 0 on a fogged seat while the Pre-Orbital Recap
    #     for the same nights printed the full count; `mines` read 0 for
    #     EVERY seat because the minelayer had no station glyph at all.
    print(f"\n{'column':<12} {'recap':>6} {'P1 stn':>7} {'OBS stn':>8}")
    exercised: list[str] = []
    for col, kinds, label in _PUBLIC_COLUMNS:
        want = truth.get(f"p2:{col}", 0)
        got = _count(p1["station"], "p2", *kinds)
        got_obs = _count(obs["station"], "p2", *kinds)
        flag = "" if want == got else "   <-- MISMATCH"
        print(f"{col:<12} {want:>6} {got:>7} {got_obs:>8}{flag}")
        if want:
            exercised.append(label)
        if got != want:
            fails.append(
                f"P1's station showed {got} p2 {label}; the recap for the "
                f"same nights publishes {want}"
            )
    print(f"\nexercised by this season   : "
          f"{', '.join(exercised) if exercised else 'NOTHING'}")

    # Cargo fill is not decoration: full ▲ vs empty △ is the public
    # `recovered_carrying` column, so it has to agree with it.
    want_carry = truth.get("p2:recovered_carrying", 0)
    got_carry = sum(1 for e in p1["station"]
                    if e["owner"] == "p2" and e["kind"] == "pickup"
                    and e.get("loaded"))
    print(f"loaded recoveries          : recap {want_carry} · "
          f"P1 station {got_carry}")
    if got_carry != want_carry:
        fails.append(
            f"P1's station drew {got_carry} loaded recoveries against a "
            f"published {want_carry} — the ▲/△ fill is misreporting cargo"
        )

    # (2) The season must actually contain the thing under test, and the
    #     board probe must be able to see an arc at all. Without both, the
    #     zero in (3) is vacuous. Note the loop above compares 0 to 0 for
    #     any column the bot never used, so `exercised` is the honest
    #     record of what this run really covered.
    if truth.get("p2:dropped", 0) + truth.get("p2:recovered", 0) == 0:
        fails.append(
            "the seeded season records no p2 launches or recoveries — the "
            "headline case is untested; re-run or raise --nights"
        )
    if not obs["arcs"]:
        fails.append(
            "OBS drew zero orbital arcs — the MutationObserver is not "
            "catching them, so 'P1 drew none' cannot be trusted"
        )
    print(f"board orbital arcs         : OBS {len(obs['arcs'])} · "
          f"P1 {len(p1['arcs'])}")

    # (2b) The injected minelayer. Same two-part claim as a harvester
    #      launch — the platform announces it, the ground does not.
    mine_glyphs = _count(mine["station"], "p2", "mine_emit")
    print(f"injected p2 mine-lay       : {mine_glyphs} station glyph(s) · "
          f"{len(mine['minearcs'])} board minelayer(s)")
    if not inj["injected"]:
        fails.append(
            "the mine injector never fired — /replay was served from cache "
            "or the route pattern missed, so the mine columns prove nothing"
        )
    elif mine_glyphs != 1:
        fails.append(
            f"an injected p2 mine-lay produced {mine_glyphs} station glyphs, "
            f"expected 1 — the minelayer is silent on its platform, which is "
            f"the state every season shipped in before v1.28 (and which the "
            f"ORDERS tooltip contradicts: 'the minelayer's flight is public')"
        )
    if mine["minearcs"]:
        fails.append(
            f"P1 drew {len(mine['minearcs'])} minelayer craft on the board "
            f"for a rival's mine in fog — the mine's CELL is private (§4.9)"
        )

    # (3) THE THING THE FIX MUST NOT BREAK. p1 ordered nothing all season
    #     and can see nothing, so ANY orbital arc on its board is a
    #     private landing location being drawn.
    if p1["arcs"]:
        fails.append(
            f"P1 drew {len(p1['arcs'])} orbital arc(s) on the board despite "
            f"seeing nothing — a landing cell is being revealed (§3.15)"
        )

    # (4) The station hook must never be handed a cell, and a launch must
    #     never be announced as the ✕ bounce variant: an orbital observer
    #     loses the orblift at the cover boundary, so it learns that a
    #     craft left and not whether it set down.
    # A launch of any kind is a bare kind+owner. A recovery is the one
    # exception: `loaded` / `damaged` are themselves published columns.
    bare = {"drop", "probe_emit", "mine_emit", "emp_launch", "chaff_flare"}
    for e in p1["station"] + mine["station"]:
        if e["gotCell"]:
            fails.append(
                f"P1: {e['owner']} {e['kind']} passed a target cell to the "
                f"station hook — today it is ignored, but that is a coordinate "
                f"one line of station.js away from being drawn"
            )
        if e["kind"] == "drop_bounce":
            fails.append(
                f"P1: {e['owner']} launch announced as drop_bounce — a fogged "
                f"observer must not learn the drop failed"
            )
        if e["owner"] == "p2" and e["kind"] in bare \
                and set(e["keys"]) - {"kind", "owner"}:
            fails.append(
                f"P1: p2 {e['kind']} carried extra fields {e['keys']} — a "
                f"launch is a bare kind+owner"
            )

    print(f"\nshots in {OUT}")
    if fails:
        for f in fails:
            print(f"FAIL: {f}")
        print(f"{len(fails)} FAILURE(S)")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
