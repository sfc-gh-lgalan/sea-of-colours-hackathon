"""Scratch check: does the Basic tutorial actually come up weapons-free?

Not a pytest — it drives a real browser against a running server, the
same way the other ``backstage/probes/_fx_*.py`` harnesses do. Run it after any
change to the teaching presets or the film modal.

    SOC_BACKEND=memory python run_web.py --no-reload --port 8022 &
    python backstage/films/_fx_tutorial.py --port 8022

What it asserts, and why each one is here rather than in a unit test:

* The preset reaches the BOARD, not just the API. Every dial in
  ``game/tutorial.py`` is threaded through four layers to get here, and
  the failure mode of a broken thread is a tutorial that silently plays
  as a full 40x28 season.
* No weapon control is VISIBLE. Not "disabled" — visible. The whole
  point of the weapons-off mode is that a first-timer never learns that
  weapons exist, so a greyed EMP button is a failure, and only a real
  layout can tell us whether one is on screen.
* The film modal auto-opens on the first turn and closes on Escape.
* It auto-opens again in a SECOND tutorial game in the same browser,
  and stops when the mute box says so. Both need one browser profile
  carried across games, which is why they live here.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request


def _api(base: str, path: str, body: dict | None = None) -> dict:
    url = f"{base}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _check_film(pg, where: str) -> list[str]:
    """Is a real film on the stage, and is it running?

    Checks playback rather than existence. `<video src>` pointing at a
    404 fires `error` and the modal swaps in a placeholder, so the only
    honest test is whether frames are advancing.
    """
    pg.wait_for_timeout(900)
    if pg.locator(".soc-tut-stage-empty").count():
        return [f"{where}: stage fell back to the 'not generated yet' placeholder"]
    v = pg.locator(".soc-tut-video").first
    if v.count() == 0:
        return [f"{where}: no <video> on the stage at all"]
    info = v.evaluate(
        "(el) => ({ rs: el.readyState, t: el.currentTime, d: el.duration,"
        " paused: el.paused })"
    )
    if int(info["rs"] or 0) < 2:
        return [f"{where}: film never loaded (readyState {info['rs']})"]
    if not (info["d"] and info["d"] > 3):
        return [f"{where}: film duration is {info['d']} — that is not a film"]
    if info["paused"] or not (info["t"] and info["t"] > 0):
        return [f"{where}: film loaded but is not playing (t={info['t']})"]
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8022)
    ap.add_argument("--seed", type=int, default=90210)
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    created = _api(base, "/api/game/new", {"tutorial": "basic", "seed": args.seed})
    sid = created["session_id"]
    fails: list[str] = []

    if int(created.get("width", 0)) != 24 or int(created.get("height", 0)) != 16:
        fails.append(f"board is {created.get('width')}x{created.get('height')}, want 24x16")
    if int(created.get("season_day_cap", 0)) != 3:
        fails.append(f"season_day_cap {created.get('season_day_cap')}, want 3")
    if created.get("weapons_enabled") is not False:
        fails.append("weapons_enabled should be False")
    if created.get("signs_enabled") is not False:
        fails.append("signs_enabled should be False")
    if created.get("agents", {}).get("p2") != "red_harvest_lite":
        fails.append(f"p2 is {created.get('agents', {}).get('p2')}, want red_harvest_lite")

    view = _api(base, f"/api/game/{sid}/view?player=p1")
    rules = (view.get("agent_view") or {}).get("meta", {}).get("rules") or {}
    if rules.get("weapons_enabled") is not False:
        fails.append("meta.rules.weapons_enabled did not reach the view")
    if rules.get("signs_enabled") is not False:
        fails.append("meta.rules.signs_enabled did not reach the view")
    if view.get("blue_sign"):
        fails.append(f"blue_sign has {len(view['blue_sign'])} regions with signs off")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright missing — API checks only")
        for f in fails:
            print("FAIL:", f)
        print("PASS (api)" if not fails else "FAIL")
        return 1 if fails else 0

    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1600, "height": 1000})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(f"{base}/play?session={sid}&player=p1", wait_until="networkidle")
        pg.wait_for_timeout(3500)

        # The modal should have opened itself on turn one.
        modal = pg.locator(".soc-tut").first
        if modal.count() == 0 or modal.is_hidden():
            fails.append("tutorial modal did not auto-open")
        else:
            title = pg.locator("[data-tut-title]").inner_text().strip()
            if not title:
                fails.append("tutorial modal opened with no title")

            # A film that 404s degrades to a placeholder and the modal
            # still looks fine — which is exactly why this has to be
            # checked in a browser rather than by listing the directory.
            fails.extend(_check_film(pg, "chapter 1"))
            pg.screenshot(path="reports/_fx_tutorial_modal.png")

            # Every chapter of the opening reel, not just the first.
            while pg.locator("[data-tut-next]:not([hidden])").count():
                pg.locator("[data-tut-next]").click()
                pg.wait_for_timeout(1200)
                fails.extend(_check_film(pg, "a later chapter"))

            pg.keyboard.press("Escape")
            pg.wait_for_timeout(300)
            if modal.is_visible():
                fails.append("Escape did not close the tutorial modal")

        if pg.locator("#soc-tutorial-btn").count() == 0:
            fails.append("no [ TUTORIAL ] button in a teaching game")

        # Nothing weapon-shaped may be on screen. `visible` is the whole
        # assertion: a hidden-but-present node is fine, a dimmed one is not.
        weapon_selectors = [
            '[data-orbit-action="build_emp"]',
            '[data-orbit-action="build_chaff"]',
            '[data-orbit-block="weapons"]',
            '[data-readout="weapon-stock"]',
            ".cc-weapon-stock-chip",
        ]
        for sel in weapon_selectors:
            loc = pg.locator(sel)
            for i in range(loc.count()):
                if loc.nth(i).is_visible():
                    fails.append(f"weapon control visible with weapons off: {sel}")
                    break

        body_text = pg.locator("body").inner_text()
        for word in ("EMP", "CHAFF", "MINE"):
            if word in body_text:
                fails.append(f"the word {word!r} is on screen with weapons off")

        # The reel is supposed to REFRESH each turn, not just exist on
        # turn one. Play the night out from under the open page and see
        # whether the orbit reel lets itself in.
        _api(base, f"/api/game/{sid}/policy",
             {"player": "p1", "moves": [{"a": "probe", "at": [8, 7]}]})
        # v1.35 — and it has to let the board finish first. The modal
        # covers the map, and it used to open the instant the phase
        # flipped: straight over the night cinematic and the orbital
        # resolution, so the tutorial hid the very things it narrates.
        # Sampled rather than asserted at one instant, because the
        # failure is an OVERLAP and an overlap has to be caught while
        # it is happening.
        pg.evaluate("""() => {
            window.__tutOverlap = 0;
            window.__tutBusySeen = 0;
            window.__tutWatch = setInterval(() => {
              const m = document.querySelector('.soc-tut');
              const shown = !!(m && !m.hidden);
              const busy = !!(window._socBoardMidAnimation
                              && window._socBoardMidAnimation());
              if (busy) window.__tutBusySeen += 1;
              if (busy && shown) window.__tutOverlap += 1;
            }, 100);
        }""")
        try:
            pg.wait_for_function(
                """() => {
                  const t = document.querySelector('[data-tut-title]');
                  const m = document.querySelector('.soc-tut');
                  return m && !m.hidden && t
                      && /orbit/i.test(t.textContent || '');
                }""",
                timeout=90000,
            )
            watch = pg.evaluate("""() => {
                clearInterval(window.__tutWatch);
                return {overlap: window.__tutOverlap,
                        busy: window.__tutBusySeen};
            }""")
            print(f"  modal-vs-board: {watch['busy']} busy samples, "
                  f"{watch['overlap']} of them with the modal up")
            if not watch["busy"]:
                print("  note: board never reported busy — FX may be off, "
                      "so the overlap check proved nothing this run")
            elif watch["overlap"]:
                fails.append(
                    f"the tutorial modal was up for {watch['overlap']} of the "
                    f"{watch['busy']} samples where the board was still "
                    "animating — it is covering the resolution it teaches"
                )
            fails.extend(_check_film(pg, "the orbit reel"))
            pg.screenshot(path="reports/_fx_tutorial_orbit.png")
        except Exception:
            phase = _api(base, f"/api/game/{sid}/status").get("phase")
            fails.append(
                f"orbit reel never auto-opened (server phase is {phase!r}) — "
                "the per-turn refresh is the whole feature"
            )

        # A SECOND tutorial, in the same browser, must teach too.
        #
        # This is the one the harness used to miss, because every run
        # gets a clean profile and this bug only exists on the second
        # game. The modal remembers which reels it has shown so it does
        # not reopen on every four-second poll; when that memory was
        # keyed on the reel name alone, and reel names are identical in
        # every Basic game, anyone who had played once got a tutorial
        # with no tutorial in it — and no clue why.
        pg.keyboard.press("Escape")
        second = _api(base, "/api/game/new",
                      {"tutorial": "basic", "seed": args.seed + 1})
        sid2 = second["session_id"]
        pg.goto(f"{base}/play?session={sid2}&player=p1", wait_until="networkidle")
        try:
            pg.wait_for_function(
                """() => {
                  const m = document.querySelector('.soc-tut');
                  const t = document.querySelector('[data-tut-title]');
                  return m && !m.hidden && t && (t.textContent || '').trim();
                }""",
                timeout=20000,
            )
        except Exception:
            fails.append(
                "the modal did not auto-open in a SECOND tutorial game — a "
                "returning player gets a teaching mode that teaches nothing"
            )

        # ...and the mute box must still win, since that is the actual
        # "stop showing me these" control.
        pg.evaluate("() => window.localStorage.setItem('soc.tutorial.muted.v1', '1')")
        third = _api(base, "/api/game/new",
                     {"tutorial": "basic", "seed": args.seed + 2})
        pg.goto(f"{base}/play?session={third['session_id']}&player=p1",
                wait_until="networkidle")
        pg.wait_for_timeout(3500)
        if pg.locator(".soc-tut").first.is_visible():
            fails.append("muted, but the modal auto-opened anyway")
        if pg.locator("#soc-tutorial-btn").count() == 0:
            fails.append("muted removed the [ TUTORIAL ] button — mute silences "
                         "the auto-open, it does not take the films away")
        pg.evaluate("() => window.localStorage.removeItem('soc.tutorial.muted.v1')")

        if errors:
            fails.append(f"page errors: {errors[:3]}")
        pg.screenshot(path="reports/_fx_tutorial.png", full_page=False)
        br.close()

    for f in fails:
        print("FAIL:", f)
    print("PASS" if not fails else f"FAIL ({len(fails)})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
