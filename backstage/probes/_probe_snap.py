"""Drive a real browser at the SNAP loop and report what it shows (v1.36).

Sibling of ``backstage/probes/_probe_arms_bar.py``, same reasoning and the same
non-assertion: what counts as right here is partly a judgement about a
picture, so this prints and you read it.

SNAP is worth a probe of its own because its whole point is a question
of ORDER, and order is exactly what a screenshot cannot show. The
weapon lands above the hour's vision snapshot, which is what lets it
refuse a landing the same hour it kills the beacon lighting it. If a
refactor slides it below the snapshot the board still animates, the
missile still flies, the probe still dies — and the harvester lands
anyway. ``tests/test_snap.py`` pins that in the engine; this checks the
UI in front of it tells the same story.

Five scenes:

1. **The desk.** Is there a SNAP row, does it quote 100b/250c off the
   published table, and does the arsenal ceiling dim it at the right
   moment (six SNAPs fit exactly, a seventh does not).
2. **The order.** Can you aim one from the board menu, does it draw a
   marker, does it wire as a bare ``[x, y]`` and not a salvo list.
3. **The night.** Does the launch resolve, does the FX run, does the
   scorch mark appear and then die one hour later.
4. **The rack.** Does the arsenal bar fall by one pip the moment it
   fires, rather than at dawn.
5. **The platform.** Does anything actually leave the station when the
   round goes. Shot in isolation against the EMP, because in a live
   night everything amber on screen is a candidate.

Whether the FX is not just present but *legible* is a different
question, and a pixel count answers it better than a screenshot does —
see ``backstage/films/ink_check.py``.

Run against a server you started yourself (see AGENTS.md)::

    SOC_BACKEND=memory python run_web.py --no-reload --port 8022 --replace
    python backstage/probes/_probe_snap.py
"""
from __future__ import annotations

import json
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


def new_game(seed=4471):
    return req("/api/game/new", {
        "players": ["p1", "p2"],
        "agents": {"p1": "human", "p2": "human"},
        "width": 24, "height": 16, "season_day_cap": 6,
        "weapons_enabled": True, "signs_enabled": True,
        "backend": "memory", "seed": seed,
    })["session_id"]


def open_page(pw, sid, seat="p1"):
    br = pw.chromium.launch()
    pg = br.new_page(viewport={"width": 1440, "height": 900})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)))
    pg.goto(f"{BASE}/?session={sid}&player={seat}", wait_until="networkidle")
    time.sleep(5)
    return br, pg, errs


def buttons(pg):
    return pg.evaluate("""() => {
      const out = {};
      document.querySelectorAll('.solo-add-orbit').forEach((b) => {
        const a = b.getAttribute('data-orbit-action');
        const act = b.closest('.cc-orbit-act');
        out[a] = {
          dim: b.classList.contains('is-disabled'),
          cost: act?.querySelector('.cc-orbit-cost')?.textContent?.trim() || '',
          why: act?.querySelector('.cc-orbit-why')?.textContent?.trim() || '',
        };
      });
      return out;
    }""")


def scene_desk(pw):
    print("\n=== scene 1: the SNAP row at the orbit desk ===")
    sid = new_game()
    for seat in ("p1", "p2"):
        req(f"/api/game/{sid}/policy", {"player": seat, "moves": []})
    wait_phase(sid, "orbit")
    br, pg, errs = open_page(pw, sid)

    for name, info in sorted(buttons(pg).items()):
        print(f"  {name:16s} cost {info['cost']:14s} "
              f"{'DIM' if info['dim'] else '   '} {info['why']}")

    print("  published prices:",
          pg.evaluate("() => JSON.stringify(window.__SOC_WEAPON_BLUE_COSTS__)"))
    print("  published cap   :",
          pg.evaluate("() => window.__SOC_WEAPON_BLUE_CAP__"))
    print("  snap speed      :",
          pg.evaluate("() => window.__SOC_SNAP_SPEED__"))

    # The ceiling: six SNAPs is exactly 600, a seventh cannot fit. Queue
    # them one at a time and watch the row dim on the SIXTH click, which
    # is the projection working rather than the engine refusing later.
    print("  queueing SNAPs against the 600 ceiling:")
    for i in range(1, 8):
        pg.click('.solo-add-orbit[data-orbit-action="build_snap"]')
        time.sleep(0.25)
        b = buttons(pg)["build_snap"]
        print(f"    after {i}: {'DIM' if b['dim'] else 'live'} {b['why']}")
    print("  console errors  :", errs or "(none)")
    br.close()


def scene_order(pw):
    print("\n=== scene 2: aiming a SNAP from the board ===")
    # Buy over the API and open the page in PLANNING. The board menu is
    # a night-orders affordance and is deliberately unavailable at the
    # orbit desk, so a page still sitting in ORBIT has no menu to find —
    # which is what the first cut of this scene kept reporting.
    sid = new_game()
    for seat in ("p1", "p2"):
        req(f"/api/game/{sid}/policy", {"player": seat, "moves": []})
    wait_phase(sid, "orbit")
    req(f"/api/game/{sid}/orbit",
        {"player": "p1", "actions": [{"a": "build_snap"}], "commit": True})
    req(f"/api/game/{sid}/orbit", {"player": "p2", "actions": [], "commit": True})
    st = wait_phase(sid, "planning")
    print("  phase before opening the page:", st.get("phase"), "day", st.get("day"))

    br, pg, errs = open_page(pw, sid)

    print("  ORDERS panel SNAP control:", pg.evaluate("""() => {
      const hits = Array.from(document.querySelectorAll('button, [data-action]'))
        .map(e => (e.textContent || '').replace(/\\s+/g, ' ').trim())
        .filter(t => t.includes('SNAP'));
      return hits.slice(0, 4);
    }"""))

    # Right-click the board and look for the SNAP entry.
    pg.click('[data-x="8"][data-y="6"]', button="right")
    time.sleep(0.8)
    print("  board menu:", pg.evaluate("""() => {
      const m = document.querySelector('.board-menu');
      return m ? (m.innerText || '').split('\\n').filter(Boolean) : null;
    }"""))
    hit = pg.evaluate("""() => {
      const m = document.querySelector('.board-menu');
      if (!m) return 'no menu';
      const row = Array.from(m.querySelectorAll('*')).find(
        (e) => (e.textContent || '').includes('SNAP here'));
      if (!row) return 'no SNAP row';
      (row.closest('.board-menu-item') || row).click();
      return 'clicked';
    }""")
    print("  'SNAP here':", hit)
    time.sleep(0.8)
    print("  order queue rows:", pg.evaluate("""() => {
      const rows = document.querySelectorAll('#solo-queue li, .cc-queue-row');
      return Array.from(rows).map((r) => (r.innerText || '').replace(/\\s+/g, ' ').trim());
    }"""))
    print("  markers on board:", pg.evaluate(
        """() => document.querySelectorAll('.cc-order-marker').length"""))
    print("  console errors:", errs or "(none)")
    pg.screenshot(path="/tmp/snap_order.png", full_page=True)
    print("  full page -> /tmp/snap_order.png")
    br.close()


def scene_night(pw):
    """Buy, fire, and watch the night — the whole loop over the API,
    with the browser only along to render it."""
    print("\n=== scene 3+4: firing a SNAP, and the rack falling ===")
    sid = new_game()
    for seat in ("p1", "p2"):
        req(f"/api/game/{sid}/policy", {"player": seat, "moves": []})
    wait_phase(sid, "orbit")
    req(f"/api/game/{sid}/orbit",
        {"player": "p1", "actions": [{"a": "build_snap"}], "commit": True})
    req(f"/api/game/{sid}/orbit", {"player": "p2", "actions": [], "commit": True})
    wait_phase(sid, "planning")

    # p2 lights a cell with a probe and lands on it; p1 SNAPs the beacon
    # in the same hour. This is the smash-and-grab denial, played for
    # real rather than in a unit test.
    req(f"/api/game/{sid}/policy", {"player": "p2", "moves": [
        {"a": "probe", "at": [10, 7]},
    ]})
    req(f"/api/game/{sid}/policy", {"player": "p1", "moves": []})
    wait_phase(sid, "orbit")
    req(f"/api/game/{sid}/orbit", {"player": "p1", "actions": [], "commit": True})
    req(f"/api/game/{sid}/orbit", {"player": "p2", "actions": [], "commit": True})
    wait_phase(sid, "planning")

    req(f"/api/game/{sid}/policy", {"player": "p1", "moves": [
        {"a": "snap", "at": [10, 7]},
    ]})
    req(f"/api/game/{sid}/policy", {"player": "p2", "moves": [
        {"a": "drop", "unit": "harvester_p2", "at": [10, 7]},
    ]})
    wait_phase(sid, "orbit")

    st = status(sid)
    print("  day now:", st.get("day"), "phase:", st.get("phase"))
    rep = req(f"/api/game/{sid}/replay")
    frames = rep.get("frames") or rep.get("replay") or []
    snap_frames = [f for f in frames if f.get("tag") == "snap_launch"]
    print("  snap_launch frames:", len(snap_frames))
    for f in snap_frames[:2]:
        print("    owner", f.get("owner"), "hour", f.get("hour"),
              "| snap payload keys:",
              sorted((f.get("snap") or [{}])[0].keys()) if f.get("snap") else None)
        for ev in (f.get("snap") or []):
            print("      destroyed_probes:", ev.get("destroyed_probes"))
            print("      damaged_harvesters:", ev.get("damaged_harvesters"))
    cloudy = [f for f in frames if f.get("snap_clouds")]
    print("  frames carrying a snap_clouds snapshot:", len(cloudy))

    # The denial itself: did p2's harvester stay in orbit?
    log = "\n".join(
        str(e.get("text", "")) for e in (status(sid).get("log") or [])
        if isinstance(e, dict)
    )
    for line in log.splitlines():
        if "snap" in line.lower() or "drop" in line.lower() or "live" in line.lower():
            print("   log:", line[:130])

    br, pg, errs = open_page(pw, sid)

    def bar(seat="p1"):
        return pg.evaluate(
            f"""() => document.querySelector('[data-os-arms="{seat}"]')?.textContent""")

    print("  arms bar on load:", bar())
    # Rewind to before the launch and step forward hour by hour: the bar
    # must fall on the launch hour, not at dawn.
    for _ in range(40):
        pg.click("#replay-prev")
        time.sleep(0.03)
    time.sleep(1.2)
    print("  rewound to:",
          pg.evaluate("""() => document.getElementById('replay-slot')?.textContent"""),
          "| bar", bar())
    seen = None
    for _ in range(28):
        pg.click("#replay-next")
        time.sleep(0.55)
        row = (
            pg.evaluate("""() => document.getElementById('replay-slot')?.textContent"""),
            bar(),
            pg.evaluate("""() => document.querySelectorAll('.snap-cloud-cell').length"""),
        )
        if row != seen:
            print(f"    {row[0]:14s} bar {row[1]}  scorch cells {row[2]}")
            seen = row
    print("  console errors:", errs or "(none)")
    pg.screenshot(path="/tmp/snap_night.png", full_page=True)
    print("  full page -> /tmp/snap_night.png")
    br.close()


def scene_launch_glyph(pw):
    """Does the PLATFORM show anything when the round leaves it?

    v1.36 — this scene exists because it did not. SNAP shipped with no
    branch in station.js's arrival dispatcher at all, so a seat could
    fire one and its platform sat there doing nothing, while every other
    weapon on the board announces itself. Nothing caught it: the glyph
    is drawn on the station's own canvas, so no DOM assertion can see
    it, and in a live night it shares the frame with a missile, a scorch
    mark and an arms bar that are all the same amber.

    So call the hook by hand with nothing else moving, and photograph
    it. The EMP is shot immediately after as a control — it has always
    worked, so if the EMP draws and the SNAP does not, the fault is the
    SNAP branch rather than this harness.
    """
    print("\n=== scene 5: the launch glyph on the platform ===")
    sid = new_game()
    br, pg, errs = open_page(pw, sid)
    print("  station element:", pg.evaluate(
        """() => !!document.querySelector('[data-os-station="p1"]')"""))
    for kind in ("snap_launch", "emp_launch"):
        pg.evaluate(
            "(k) => window.osOnEntityArrival({ kind: k, owner: 'p1' })", kind)
        # Mid-flight: the craft arcs out over 1.5-2.2s, so half a second
        # in it is clear of the platform and not yet gone.
        time.sleep(0.6)
        pg.screenshot(path=f"/tmp/snap_glyph_{kind}.png")
        print(f"  {kind} -> /tmp/snap_glyph_{kind}.png")
        time.sleep(3.0)
    print("  console errors:", errs or "(none)")
    br.close()


def main():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        scene_desk(pw)
        scene_order(pw)
        scene_night(pw)
        scene_launch_glyph(pw)


if __name__ == "__main__":
    main()
