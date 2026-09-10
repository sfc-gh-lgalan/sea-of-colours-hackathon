"""Drive a real browser at the arsenal UI and report what it shows (v1.34).

The arsenal bar (RULEBOOK §4.9.8) is drawn by ``station.js`` from data
that reaches it through three hops — engine observation, view, published
global — and it is the kind of thing that renders *something* whatever
goes wrong. A bar of six dim pips is exactly what a correct empty rack
and a completely broken data path both look like, which is why this
exists: the first cut of the feature read 0/600 on a seat visibly
holding an EMP, and nothing in the page or the test suite noticed.

Sibling of ``backstage/probes/_probe_battle_room.py``, same reasoning.

Run it against a server you started yourself (see AGENTS.md)::

    SOC_BACKEND=memory python run_web.py --no-reload --port 8022 --replace
    python backstage/probes/_probe_arms_bar.py

Three scenes. The first plays two empty nights so both seats genuinely
hold an EMP during an orbit, then reports the bars, the published
globals, the buy-button refusals and the rival hover card. The second
buys a weapon through the UI and scrubs the night backwards and
forwards, which is where an animation that leaks state goes wrong. The
third fires the weapon and walks the night hour by hour, because since
v1.35 the bar is supposed to fall at the launch rather than at dawn.

Read the output; nothing here asserts, because what counts as right is
a judgement about a picture. ``server/static/films/adv_arms_bar.webm``
puts a camera on the same beat.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8022"


def req(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.loads(resp.read())


def status(sid):
    return req(f"/api/game/{sid}/status")


def wait_phase(sid, want, limit=120):
    for _ in range(limit):
        st = status(sid)
        if st.get("phase") == want:
            return st
        time.sleep(0.5)
    return status(sid)


def new_game():
    return req("/api/game/new", {
        "players": ["p1", "p2"],
        "agents": {"p1": "human", "p2": "human"},
        "width": 24, "height": 16, "season_day_cap": 6,
        "weapons_enabled": True, "signs_enabled": True,
        "backend": "memory", "seed": 2351,
    })["session_id"]


# Pixel classes on the animation canvas. The flight starts the blue of a
# fissile pip and ends the cyan of an arsenal pip, so counting both over
# a few frames is the only way to see that the conversion happened and
# not merely that something moved.
_PIXEL_PROBE = """() => {
  const c = document.querySelector('canvas[data-os-anim]');
  if (!c) return null;
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  let blue = 0, cyan = 0;
  for (let i = 0; i < d.length; i += 4) {
    const [r, g, b, a] = [d[i], d[i+1], d[i+2], d[i+3]];
    if (a < 40 || r > 150 || b < 120) continue;
    // Both ends of the flight are blue-dominant, so the tell is green:
    // the fissile blue (#4aa3ff) runs well short of its own blue channel,
    // the arsenal cyan (#39d3d3) matches it.
    if (b - g > 45) blue++;
    else cyan++;
  }
  return {blue, cyan};
}"""


def scene_buy(pw, base_note):
    """Buy a weapon through the UI and watch the blue turn into cyan."""
    sid = new_game()
    for seat in ("p1", "p2"):
        req(f"/api/game/{sid}/policy", {"player": seat, "moves": []})
    wait_phase(sid, "orbit")
    # p2 settles over the API so the orbit resolves the moment p1 commits
    # in the browser — that is the beat the animation hangs off.
    req(f"/api/game/{sid}/orbit", {"player": "p2", "actions": [], "commit": True})

    br = pw.chromium.launch()
    pg = br.new_page(viewport={"width": 1440, "height": 900})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)))
    pg.goto(f"{BASE}/?session={sid}&player=p1", wait_until="networkidle")
    time.sleep(5)

    def bar():
        return pg.evaluate(
            """() => document.querySelector('[data-os-arms="p1"]')?.textContent""")

    print("\n=== scene: buying a weapon in the browser ===")
    # The hover card and the bar are two renderings of one public fact,
    # and they must not disagree. They did: the card reads the ``pre``
    # (end of Nox) snapshot, so between buying a weapon and playing the
    # night it said 0/600b beside a bar showing two lit pips.
    def card_arsenal():
        pg.hover('[data-os-station="p1"]')
        time.sleep(1.1)
        out = pg.evaluate("""() => {
            const c = document.querySelector('.os-hover-card');
            if (!c) return null;
            const m = (c.innerText || '').match(/arsenal\\s*\\n?\\s*(\\S+)/i);
            return m ? m[1] : '(no arsenal row)';
        }""")
        pg.mouse.move(700, 700)
        time.sleep(0.3)
        return out

    print("arms bar before commit :", bar(), "| card", card_arsenal())
    pg.click('.solo-add-orbit[data-orbit-action="build_emp"]')
    time.sleep(0.4)
    pg.click("#solo-commit-orbit")
    wait_phase(sid, "planning")
    # Wait for the poll that publishes the new rack, or the comparison
    # below just races it and finds both readings honestly empty.
    try:
        pg.wait_for_function(
            "() => Number(window.__SOC_ARMS_SELF__) > 0", timeout=20000)
    except Exception:
        print("    !! the live arsenal never reached the page after a buy")
    time.sleep(1.5)
    _bar, _card = bar(), card_arsenal()
    print("arms bar after orbit   :", _bar, "| card", _card)
    lit = (_bar or "").count("\u2588")
    if lit and _card and _card.startswith("0/"):
        print("    !! the card says an empty rack while the bar shows",
              lit, "lit pips — they are the same public number")

    # The blue is DRAINED IN NOX, not at the orbit desk, so the flight
    # belongs to the night cinematic. Run an empty night and watch it.
    for seat in ("p1", "p2"):
        req(f"/api/game/{sid}/policy", {"player": seat, "moves": []})

    wait_phase(sid, "orbit")
    time.sleep(6)
    print("arms bar after night   :", bar())

    # Now walk the night back and forward on the replay bar. That is both
    # the scrub check and the only reliable way to catch the flight: it
    # rides the DUSK/PRAXIS beats, which the live reel plays once and
    # never again, and a backwards scrub is exactly where an animation
    # that leaks state goes wrong.
    print("\n--- replay scrub across the DUSK that bought the EMP ---")
    for _ in range(30):
        pg.click("#replay-prev")
        time.sleep(0.05)
    time.sleep(1.5)
    print("slot after rewind      :",
          pg.evaluate("""() => document.getElementById('replay-slot')?.textContent"""),
          "| bar", bar())

    frames = []
    walk = []
    for _ in range(34):
        pg.click("#replay-next")
        step = {"slot": pg.evaluate(
            """() => document.getElementById('replay-slot')?.textContent"""),
            "blue": 0, "cyan": 0}
        walk.append(step)
        # Since v1.35 the whole beat — lift, recolour, flight — runs
        # inside the DUSK dwell, and end to end that is over two
        # seconds. Sample right across it: stopping early reports the
        # blue lift as if it were the whole animation, which is exactly
        # what this probe said the first time the beat overran.
        for _ in range(34):
            time.sleep(0.09)
            f = pg.evaluate(_PIXEL_PROBE)
            frames.append(f)
            if f:
                step["blue"] = max(step["blue"], f["blue"])
                step["cyan"] = max(step["cyan"], f["cyan"])
        step["bar"] = pg.evaluate(
            """() => document.querySelector('[data-os-arms="p1"]')?.textContent""")

    if all(f is None for f in frames):
        print("frames                 : NO CANVAS — nothing ever animated")
    else:
        lit = [f for f in frames if f and (f["blue"] or f["cyan"])]
        print("frames with paint      :", len(lit), "of", len(frames))
        print("peak blue / peak cyan  :",
              max((f["blue"] for f in lit), default=0),
              "/", max((f["cyan"] for f in lit), default=0))
        print("trace                  :",
              " ".join(f"{f['blue']}b/{f['cyan']}c" for f in lit[:30])
              or "(nothing drew)")
    print("hour by hour (slot · peak paint · bar):")
    last = None
    for s in walk:
        key = (s["slot"], s["bar"], bool(s["blue"] or s["cyan"]))
        if key == last:
            continue                      # the cursor stopped moving
        last = key
        print(f"    {str(s['slot']):>10}  {s['blue']:>3}b/{s['cyan']:>3}c  {s['bar']}")
    # What to look for: cyan paint on the DUSK row and a bar that goes
    # from six dots to two lit pips there — v1.35 moved the conversion
    # off PRAXIS and onto DUSK precisely so it lands while the camera is
    # still on the stations. Cyan appearing only on a later row means
    # the beat has drifted back into the night again.
    time.sleep(1.5)
    print("bar at end of scrub    :", bar())
    pg.click("#replay-live")
    time.sleep(2.5)
    print("bar back on LIVE       :", bar())
    print("__SOC_ARMS_SELF__      :", pg.evaluate("window.__SOC_ARMS_SELF__"))
    pg.screenshot(path="/tmp/arms_after_buy.png")
    print("full page -> /tmp/arms_after_buy.png", base_note)
    print("console errors         :", "; ".join(errs[:8]) or "(none)")
    br.close()


def scene_fire(pw):
    """Fire the weapon and check the bar falls on the hour it flies.

    Before v1.35 the arsenal bar was pinned to the post-orbital snapshot
    for the whole night, so a seat that emptied its rack at hour 1 still
    read as fully armed until the next dawn — which is the opposite of
    what a warning light is for.
    """
    sid = new_game()
    for seat in ("p1", "p2"):
        req(f"/api/game/{sid}/policy", {"player": seat, "moves": []})
    wait_phase(sid, "orbit")
    for seat in ("p1", "p2"):
        req(f"/api/game/{sid}/orbit",
            {"player": seat, "actions": [{"a": "build_emp", "count": 1}],
             "commit": True})
    wait_phase(sid, "planning")
    # p1 spends its EMP; p2 sits still and keeps its own, which makes the
    # two bars a control pair — one must fall, the other must not.
    req(f"/api/game/{sid}/policy",
        {"player": "p1", "moves": [{"a": "emp_launch", "at": [12, 8]}]})
    req(f"/api/game/{sid}/policy", {"player": "p2", "moves": []})
    wait_phase(sid, "orbit")

    br = pw.chromium.launch()
    pg = br.new_page(viewport={"width": 1440, "height": 900})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)))
    pg.goto(f"{BASE}/?session={sid}&player=p1", wait_until="networkidle")
    time.sleep(5)

    print("\n=== scene: firing the weapon (bar must fall mid-night) ===")
    print("__SOC_WEAPON_BLUE_COSTS__:",
          pg.evaluate("JSON.stringify(window.__SOC_WEAPON_BLUE_COSTS__)"))
    print("_osArmsFiredAtTick hook  :",
          pg.evaluate("typeof window._osArmsFiredAtTick"))

    # Walk the tick index directly rather than clicking through the
    # night. Two empty-ish nights make a replay only a few hours long, so
    # a click walk collapses into one row and says nothing; the tally is
    # what the bar is driven by, so read it per tick.
    tally = pg.evaluate(
        """() => {
            const out = [];
            for (let i = 0; i < 40; i++) {
                out.push([i,
                    window._osArmsFiredAtTick(i, 'p1'),
                    window._osArmsFiredAtTick(i, 'p2')]);
            }
            return out;
        }""")
    print("fired-so-far by tick (p1 launches at hour 1, p2 never does):")
    last = None
    for i, a, b in tally:
        if (a, b) == last:
            continue
        last = (a, b)
        print(f"    tick {i:>2}   p1 {a:>4}   p2 {b:>4}")
    if all(a == 0 for _, a, _ in tally):
        print("    !! p1 never registers a launch — the bar cannot fall mid-night")
    if any(b for _, _, b in tally):
        print("    !! p2 registers a launch it never made")

    # Now the render path, driven rather than clicked. Two empty-ish
    # nights make a replay a handful of ticks long, so walking it with
    # the replay buttons never lands on a night hour and the walk says
    # nothing at all. The bar is a function of exactly two inputs — the
    # post-orbital snapshot and the tally above — so hold the first and
    # move the second through the launch.
    pg.evaluate("""() => {
        window.__fired = 0;
        window._osArmsFiredAtTick =
          (i, seat) => (seat === 'p1' ? window.__fired : 0);
    }""")
    print("driven night (p1 fires at h2; p2 is the control and must not move):")
    for hour, fired in enumerate([0, 0, 200, 200]):
        pg.evaluate("(f) => { window.__fired = f; }", fired)
        pg.evaluate(
            "(i) => window.osOnReplayTick("
            "{slot: null, day: 2, tag: 'open', idx: i})", 40 + hour)
        time.sleep(0.15)
        # Caught mid-fade: a pip going out is still drawn as ordnance
        # while it fades, so this is where the --out class is visible.
        mid = pg.evaluate(
            """() => document.querySelector('[data-os-arms="p1"]').innerHTML""")
        time.sleep(0.8)
        row = pg.evaluate(
            """() => ({
                p1: document.querySelector('[data-os-arms="p1"]').textContent,
                t1: document.querySelector('[data-os-arms="p1"]').title,
                p2: document.querySelector('[data-os-arms="p2"]').textContent,
            })""")
        fading = [c for c in ("os-arms-pip--in", "os-arms-pip--out") if c in mid]
        print(f"    h{hour}  fired {fired:>4}   p1 {row['p1']}  p2 {row['p2']}"
              f"   {row['t1'].split(' — ')[0]}   fade: {fading or '-'}")
    print("console errors           :", "; ".join(errs[:8]) or "(none)")
    pg.screenshot(path="/tmp/arms_fired.png")
    print("full page -> /tmp/arms_fired.png")
    br.close()


def main() -> int:
    sid = new_game()
    st = status(sid)
    print("session", sid, "| phase", st.get("phase"), "day", st.get("day"))

    # Night 1 (empty) -> orbit 2.
    for seat in ("p1", "p2"):
        req(f"/api/game/{sid}/policy", {"player": seat, "moves": []})
    st = wait_phase(sid, "orbit")
    print("-> phase", st.get("phase"), "day", st.get("day"))

    # Orbit 2: each seat buys one EMP out of the 250 opening bank.
    for seat in ("p1", "p2"):
        out = req(f"/api/game/{sid}/orbit",
                  {"player": seat, "actions": [{"a": "build_emp", "count": 1}],
                   "commit": True})
        print(seat, "orbit ->", json.dumps(out)[:220])
    st = wait_phase(sid, "planning")
    print("-> phase", st.get("phase"), "day", st.get("day"))

    # Night 2 (empty) -> orbit 3, now each seat HOLDS an EMP.
    for seat in ("p1", "p2"):
        req(f"/api/game/{sid}/policy", {"player": seat, "moves": []})
    st = wait_phase(sid, "orbit")
    print("-> phase", st.get("phase"), "day", st.get("day"))

    view = req(f"/api/game/{sid}/view?player=p1")
    av = view.get("agent_view") or view
    si = av.get("station_intel") or {}
    print("\nmeta.rules.weapon_blue_cap =",
          ((av.get("meta") or {}).get("rules") or {}).get("weapon_blue_cap"))
    print("self arms   :", (si.get("self") or {}).get("arms"))
    for o in si.get("opponents") or []:
        print("opp", o.get("seat"), "arms:", o.get("arms"),
              "| rival blue keys:", sorted((o.get("blue") or {}).keys()))

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1440, "height": 900})
        errs = []
        pg.on("console",
              lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)))
        pg.goto(f"{BASE}/?session={sid}&player=p1", wait_until="networkidle")
        time.sleep(5)

        print("\n--- globals ---")
        print("__SOC_ARMS__ :", pg.evaluate("JSON.stringify(window.__SOC_ARMS__)"))
        print("__SOC_CAP__  :", pg.evaluate("window.__SOC_WEAPON_BLUE_CAP__"))
        print("__SOC_ARMS_SELF__:",
              pg.evaluate("window.__SOC_ARMS_SELF__"))

        print("\n--- hooks ---")
        print("osRefreshArms:", pg.evaluate("typeof window.osRefreshArms"))
        print("osOnLive     :", pg.evaluate("typeof window.osOnLive"))
        pg.evaluate("window.osRefreshArms && window.osRefreshArms()")
        time.sleep(0.6)
        print("after manual refresh, p1 title:",
              pg.evaluate("""document.querySelector('[data-os-arms="p1"]')?.title"""))

        print("\n--- station arms bars (each seat holds 1 EMP = 200) ---")
        for seat in ("p1", "p2"):
            info = pg.evaluate(
                """(seat) => {
                    const el = document.querySelector(`[data-os-arms="${seat}"]`);
                    if (!el) return {missing: true};
                    const r = el.getBoundingClientRect();
                    return {text: el.textContent, title: el.title,
                            cls: el.className,
                            colors: [...el.querySelectorAll('span')]
                                      .map(s => s.style.color),
                            box: {x: Math.round(r.x), y: Math.round(r.y),
                                  w: Math.round(r.width)}};
                }""", seat)
            print(" ", seat, json.dumps(info))

        def afford(label):
            print(f"\n--- orbit affordability: {label} ---")
            for act in ("build_emp", "build_chaff", "build_probe"):
                info = pg.evaluate(
                    """(act) => {
                        const btn = document.querySelector(
                          `.solo-add-orbit[data-orbit-action="${act}"]`);
                        if (!btn) return {missing: true};
                        const wrap = btn.closest('.cc-orbit-act');
                        const why = wrap?.querySelector('.cc-orbit-why');
                        return {disabled: btn.classList.contains('is-disabled'),
                                dim: wrap?.classList.contains('cc-orbit-act--dim'),
                                why: why ? why.textContent : null};
                    }""", act)
                print(f"  {act:14s}", json.dumps(info))

        afford("cap 600, holding 200")

        # Drop the ceiling to 200 so the seat is AT it. The cap is read
        # from the published value on every refresh, so this exercises
        # the real refusal path with real held stock.
        # A plain assignment would not survive: the status poll republishes
        # the cap every second or so and would put 600 straight back. Pin
        # it behind a getter with a no-op setter so the poll's write is
        # swallowed instead of fighting us.
        pg.evaluate("""() => Object.defineProperty(
            window, '__SOC_WEAPON_BLUE_CAP__',
            {get: () => 200, set: () => {}, configurable: true})""")
        # Queueing anything re-runs the affordability pass, which is what
        # makes it re-read the ceiling.
        pg.click('.solo-add-orbit[data-orbit-action="build_probe"]')
        time.sleep(1.5)
        afford("cap forced to 200, holding 200 -> should refuse weapons")

        print("\n--- rival station hover card ---")
        pg.hover('[data-os-station="p2"]')
        time.sleep(1.5)
        card = pg.evaluate(
            """() => {
                const c = document.querySelector(
                  '.os-hover-card, .os-obs-card, .cc-hover-card');
                return c ? c.innerText : null;
            }""")
        print(card)

        pg.screenshot(path="/tmp/arms_full.png")
        box = pg.evaluate(
            """() => {
                const el = document.querySelector('[data-os-station="p1"]');
                if (!el) return null;
                const r = el.getBoundingClientRect();
                return {x: Math.max(0, r.x - 30), y: Math.max(0, r.y - 30),
                        width: r.width + 60, height: r.height + 60};
            }""")
        if box:
            pg.screenshot(path="/tmp/arms_p1.png", clip=box)
            print("\ncropped p1 station -> /tmp/arms_p1.png", box)

        print("\n--- console errors ---")
        print("\n".join(errs[:20]) or "(none)")
        br.close()

        scene_buy(pw, "(look for two cyan pips beside the station)")
        scene_fire(pw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
