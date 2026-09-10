#!/usr/bin/env python3
"""Does the right-click board menu offer the right verbs, and only act
when it is allowed to?

The menu is the INVERSE of the panel flow: the square is already chosen,
so the menu is built from what that square affords. Three properties are
worth asserting rather than eyeballing, because each is a way the
inversion can go quietly wrong:

* **THE OFFER IS CELL-CORRECT.** `walk` may appear only on a cardinal
  neighbour of the unit's PROJECTED position, `lift` only on the square
  it is standing on, `drop` only while it is in orbit. Getting this
  wrong produces a menu that reads fine and enqueues orders the engine
  rejects at PRAXIS, which is the worst of both worlds.
* **THE OFFER FOLLOWS THE QUEUE, NOT THE SNAPSHOT.** Queue a drop, and
  the very next right-click must offer `walk` from the LANDING cell —
  `projectedUnitState`, not `current_pos`. This is the one the panel got
  wrong for a long time.
* **DIM IS NOT GATE, STILL.** probe / EMP / mine must be offered at zero
  stock, annotated. A harvester that cannot reach the square is LISTED
  (dimmed, with the distance) rather than omitted, because a unit
  silently missing from a menu reads as a bug.

Plus the gate: no menu at all in watcher mode, where there is no seat to
order for.

Scratch harness like the other ``backstage/probes/_fx_*.py`` — not pytest.

Usage::

    python backstage/probes/_fx_boardmenu.py --base http://127.0.0.1:8022

Point it at an AGENT-RUN server, never the user's. See AGENTS.md.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).resolve().parents[2] / "reports" / "boardmenu"


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=60) as r:
        return json.loads(r.read().decode())


def _post(base: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def _labels(model: list[dict]) -> list[str]:
    return [m["label"] for m in model if m["label"]]


def _actionable(model: list[dict]) -> list[str]:
    return [m["label"] for m in model if m["actionable"]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8022")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    OUT.mkdir(parents=True, exist_ok=True)

    game = _post(base, "/api/game/new", {
        "width": 30, "height": 20, "season_day_cap": 5,
        "players": ["p1", "p2"],
        "agents": {"p1": "human", "p2": "red_harvest"},
        "visibility_mode": "hidden",
    })
    sid = game["session_id"]
    print(f"session {sid}")

    fails: list[str] = []
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1280, "height": 800})
        pg.goto(f"{base}/?session={sid}&player=p1", wait_until="networkidle")
        pg.wait_for_selector("#map-player .cell", timeout=20000)
        pg.wait_for_timeout(900)

        if not pg.evaluate("() => Boolean(window.__SOC_MENU_DBG__)"):
            print("FAIL: no __SOC_MENU_DBG__ hook — stale app.js?")
            br.close()
            return 1

        if not pg.evaluate("() => window.__SOC_MENU_DBG__.allowed()"):
            print("FAIL: menu refuses to open on a live human seat")
            br.close()
            return 1

        # ── (1) a harvester in orbit offers DROP on any square ───────
        m = pg.evaluate("() => window.__SOC_MENU_DBG__.model(10, 10)")
        acts = _actionable(m)
        print(f"orbit @10,10    : {acts}")
        if not any(a.startswith("drop hv") for a in acts):
            fails.append("a harvester in orbit does not offer DROP")
        if any(a.startswith("walk") or a.startswith("lift") for a in acts):
            fails.append(
                "a harvester in ORBIT is offering walk/lift — those need it "
                "to be on the surface"
            )
        for want in ("launch probe here", "EMP here", "mine here"):
            if want not in acts:
                fails.append(f"deploy bay {want!r} missing from the menu")

        # Zero stock must still be offered, annotated. A fresh seat has
        # no weapons at all, so this is the live case not a contrived one.
        emp = next((i for i in m if i["label"] == "EMP here"), None)
        print(f"EMP at 0 stock  : {emp}")
        if emp is None or not emp["actionable"]:
            fails.append(
                "EMP is not actionable at zero stock — dim is not gate"
            )
        # The wording is the PANEL's, via the shared `_stockNote` — if
        # these two ever say different things about the same bay, that
        # is the drift this assertion exists to catch.
        elif emp["note"] != "0 held" or not emp["warn"]:
            fails.append(
                f"EMP bay is empty but the note does not match the ORDERS "
                f"panel's wording (expected '0 held' + warn): {emp}"
            )

        # ── (2) queue a drop; the offer must follow the QUEUE ────────
        unit = pg.evaluate("() => window.__SOC_ORDERS_DBG__.firstHarvester()")
        print(f"unit            : {unit}")
        pg.evaluate(
            "([u]) => window.__SOC_ORDERS_DBG__.addQueueRow('drop', 10, 10, u)",
            [unit],
        )
        pg.wait_for_timeout(150)

        at_landing = _actionable(
            pg.evaluate("() => window.__SOC_MENU_DBG__.model(10, 10)"))
        nbr = _actionable(
            pg.evaluate("() => window.__SOC_MENU_DBG__.model(11, 10)"))
        far = pg.evaluate("() => window.__SOC_MENU_DBG__.model(20, 4)")
        print(f"after drop @10,10: {at_landing}")
        print(f"neighbour @11,10 : {nbr}")

        if not any(a.startswith("lift hv") for a in at_landing):
            fails.append(
                "standing on the landing cell, the menu does not offer LIFT — "
                "it is reading current_pos, not the projected position"
            )
        if not any(a.startswith("walk hv") for a in nbr):
            fails.append(
                "a cardinal neighbour of the queued landing does not offer "
                "WALK — projectedUnitState is not being consulted"
            )
        # Diagonal is NOT a step: the engine's rule is dx+dy == 1.
        diag = _actionable(
            pg.evaluate("() => window.__SOC_MENU_DBG__.model(11, 11)"))
        if any(a.startswith("walk hv") for a in diag):
            fails.append(
                "a DIAGONAL neighbour offers WALK; the engine's step rule is "
                "cardinal (dx+dy==1) and the menu must mirror it"
            )
        # Far away: listed, dimmed, with a distance — not omitted.
        far_hv = [i for i in far if i["label"] and i["label"].startswith("hv")]
        print(f"far @20,4        : {far_hv}")
        if not far_hv:
            fails.append(
                "an out-of-reach harvester vanished from the menu instead of "
                "being listed with its distance"
            )
        elif far_hv[0]["actionable"]:
            fails.append("an out-of-reach harvester is offering a verb")
        elif not (far_hv[0]["note"] or "").endswith("away"):
            fails.append(f"no distance on the out-of-reach row: {far_hv[0]}")

        # ── (3) the menu can CANCEL, not just order ──────────────────
        pg.evaluate("() => window.__SOC_ORDERS_DBG__.addQueueRow('probe', 7, 7)")
        pg.wait_for_timeout(150)
        at7 = pg.evaluate("() => window.__SOC_MENU_DBG__.model(7, 7)")
        rem = [i for i in at7 if (i["label"] or "").startswith("remove")]
        print(f"queued @7,7      : {[i['label'] for i in rem]}")
        if not rem:
            fails.append(
                "a square with a queued order offers no way to remove it — "
                "the menu is write-only"
            )

        # ── (4) it actually works end to end, through real DOM ───────
        before = len(pg.evaluate("() => window.__SOC_MENU_DBG__.queue()"))
        pg.evaluate("() => window.__SOC_MENU_DBG__.open(12, 6, 400, 300)")
        pg.wait_for_timeout(120)
        if not pg.evaluate("() => window.__SOC_MENU_DBG__.isOpen()"):
            fails.append("openBoardMenu did not put a menu on the page")
        else:
            pg.screenshot(path=str(OUT / "menu_open.png"))
            geo = pg.evaluate("""
              () => {
                const el = document.querySelector('.board-menu');
                const r = el.getBoundingClientRect();
                return {
                  w: Math.round(r.width), h: Math.round(r.height),
                  offRight: r.right > window.innerWidth,
                  offBottom: r.bottom > window.innerHeight,
                  items: [...el.querySelectorAll('.board-menu-item')]
                    .map((b) => b.textContent.trim()),
                  ringed: document.querySelectorAll('.cell--menu-at').length,
                };
              }
            """)
            print(f"menu box         : {geo['w']}x{geo['h']} "
                  f"ringed={geo['ringed']}")
            if geo["offRight"] or geo["offBottom"]:
                fails.append("the menu overruns the viewport")
            if geo["ringed"] != 1:
                fails.append(
                    f"expected exactly 1 ringed square, got {geo['ringed']}")
            # Click a real row.
            pg.click(".board-menu-item >> nth=0")
            pg.wait_for_timeout(200)
            after = len(pg.evaluate("() => window.__SOC_MENU_DBG__.queue()"))
            still = pg.evaluate("() => window.__SOC_MENU_DBG__.isOpen()")
            print(f"click item       : queue {before} -> {after} open={still}")
            if after <= before:
                fails.append("clicking a menu row did not enqueue anything")
            if still:
                fails.append("the menu stayed open after a row was chosen")

        # Escape closes it.
        pg.evaluate("() => window.__SOC_MENU_DBG__.open(9, 9, 300, 300)")
        pg.wait_for_timeout(100)
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(120)
        if pg.evaluate("() => window.__SOC_MENU_DBG__.isOpen()"):
            fails.append("Escape did not close the menu")
        if pg.evaluate("() => document.querySelectorAll('.cell--menu-at').length"):
            fails.append("the ring survived the menu being closed")

        # A real right-click through the browser, not the debug hook —
        # this is the only check that the listener is actually wired and
        # that the native menu is suppressed.
        pg.click("#map-player .cell >> nth=45", button="right")
        pg.wait_for_timeout(200)
        real = pg.evaluate("() => window.__SOC_MENU_DBG__.isOpen()")
        print(f"real right-click : open={real}")
        if not real:
            fails.append("a genuine right-click on a cell opened nothing")
        pg.screenshot(path=str(OUT / "menu_rightclick.png"))
        pg.keyboard.press("Escape")

        # ── (5) the gate: a watcher has no seat to order for ─────────
        w = br.new_page(viewport={"width": 1280, "height": 800})
        w.goto(f"{base}/?session={sid}&watch=1", wait_until="networkidle")
        w.wait_for_timeout(1200)
        allowed = w.evaluate(
            "() => window.__SOC_MENU_DBG__ "
            "? window.__SOC_MENU_DBG__.allowed() : null")
        print(f"watcher allowed  : {allowed}")
        if allowed is not False:
            fails.append(
                f"the board menu is reachable in WATCH mode (allowed={allowed})"
            )
        w.close()

        # ── (6) the ORBIT phase closes the ORDERS tab, so it must close
        #        the menu too, or right-click is a back door into a
        #        phase the panel shuts.
        phase = "?"
        for _ in range(6):
            phase = str(_get(base, f"/api/game/{sid}/status").get("phase") or "")
            if phase == "orbit":
                break
            _post(base, f"/api/game/{sid}/policy", {"player": "p1", "moves": []})
            time.sleep(0.4)
        print(f"drove to phase   : {phase}")
        o = br.new_page(viewport={"width": 1280, "height": 800})
        o.goto(f"{base}/?session={sid}&player=p1", wait_until="networkidle")
        o.wait_for_timeout(1500)
        allowed_orbit = o.evaluate("() => window.__SOC_MENU_DBG__.allowed()")
        print(f"orbit phase      : menu allowed={allowed_orbit}")
        if phase == "orbit" and allowed_orbit:
            fails.append(
                "the board menu is reachable during the ORBIT phase, where "
                "the ORDERS panel is hidden — a back door"
            )
        elif phase != "orbit":
            print("note: never reached the orbit phase; gate not exercised")
        o.close()

        # ── (7) AN ARMED AIM MUST NOT SURVIVE THE PHASE FLIP.
        #
        # Distinct from (6), and (6) cannot catch it: that check loads a
        # page that is ALREADY in orbit, whereas this leak needs the phase
        # to change underneath a page that is open and armed. Reported
        # from a live season as a pick banner reading "click neighbour to
        # chain" sitting over the board during the orbital turn — for a
        # queue whose panel had just been hidden. Since v1.24 the banner
        # holds a real grid row, so a stale one also shortens the map.
        #
        # Needs its own session because the page has to be open and armed
        # BEFORE the night resolves.
        game2 = _post(base, "/api/game/new", {
            "width": 30, "height": 20, "season_day_cap": 5,
            "players": ["p1", "p2"],
            "agents": {"p1": "human", "p2": "red_harvest"},
            "visibility_mode": "hidden",
        })
        sid2 = game2["session_id"]
        a = br.new_page(viewport={"width": 1280, "height": 800})
        a.goto(f"{base}/?session={sid2}&player=p1", wait_until="networkidle")
        a.wait_for_timeout(1500)
        a.locator(".cc-fleet-row .cc-fleet-verb").first.click()
        a.wait_for_timeout(250)
        # v1.27 — `offsetParent !== null` is NO LONGER a visibility test for
        # this element. The banner's row is now RESERVED (it is hidden with
        # `visibility`, not `display`, so arming an order stops shoving the
        # board down a line), and a `visibility: hidden` element still has
        # an offsetParent. Ask the computed style.
        armed_before = a.evaluate(
            """() => {
              const b = document.querySelector('.pick-mode-banner');
              const cs = b && getComputedStyle(b);
              return {
                banner: !!(b && cs.display !== 'none'
                  && cs.visibility !== 'hidden' && b.offsetParent !== null),
                pickMode: !!document.querySelector('.map--pick-mode'),
              };
            }"""
        )
        print(f"armed (planning) : {armed_before}")
        if not armed_before["banner"]:
            fails.append(
                "could not arm a verb in PLANNING, so the phase-flip "
                "disarm check never ran"
            )
        # Flip the phase under the open page and let its poller notice.
        ph2 = "?"
        for _ in range(6):
            ph2 = str(_get(base, f"/api/game/{sid2}/status").get("phase") or "")
            if ph2 == "orbit":
                break
            _post(base, f"/api/game/{sid2}/policy",
                  {"player": "p1", "moves": []})
            time.sleep(0.4)
        a.wait_for_timeout(4000)
        after = a.evaluate(
            """() => {
              const b = document.querySelector('.pick-mode-banner');
              const cs = b && getComputedStyle(b);
              return {
                phase: window.__SOC_MENU_DBG__
                  ? window.__SOC_MENU_DBG__.allowed() : null,
                banner: !!(b && cs.display !== 'none'
                  && cs.visibility !== 'hidden' && b.offsetParent !== null),
                pickMode: !!document.querySelector('.map--pick-mode'),
                armedVerb: !!document.querySelector('.cc-fleet-verb--armed'),
              };
            }"""
        )
        print(f"after flip to {ph2:<7}: {after}")
        if ph2 != "orbit":
            print("note: never reached orbit; disarm-on-flip not exercised")
        else:
            if after["banner"]:
                fails.append(
                    "the pick-mode banner survived the flip into ORBIT — "
                    "it still instructs the player to click the board for "
                    "a composer that is now hidden"
                )
            if after["pickMode"]:
                fails.append(
                    "the board is still in pick-mode during ORBIT "
                    "(.map--pick-mode left on)"
                )
            if after["armedVerb"]:
                fails.append(
                    "a fleet verb is still armed during ORBIT"
                )
        a.close()
        br.close()

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
