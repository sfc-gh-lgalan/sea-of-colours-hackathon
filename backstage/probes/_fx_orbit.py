#!/usr/bin/env python3
"""Is the ORBIT panel readable, and does it dim the right things?

The v1.25 pass rewrote the top of the panel. What was there: seven equal
readout tiles, four of which broke the vault down by tier — RED as
``trace/vein/mass/pure`` and BLUE as ``shallow/mid/sink/deep``. That is
vault bookkeeping, and the VAULT tab plus the settlement block below
already carry it; meanwhile the two figures you actually spend, credits
and blue, had the same weight as everything else. So the panel answered
"what is in my hold" loudly and "what can I buy" quietly, which is
backwards for a screen whose entire job is buying.

Asserted here rather than eyeballed:

* **The wallet leads.** CREDITS and BLUE render bigger than the fleet
  readouts beside them, and the retired tier tiles are gone.
* **Dimming tells the truth.** A button you cannot afford is dimmed and
  says why. REPAIR with nothing damaged is dimmed too, but for a
  different reason worth keeping separate: there is no target, so it is
  a genuine no-op rather than a thing you might buy later tonight.
* **Dimming is not gating.** Same standing rule as ORDERS — the panel
  annotates, it does not prevent. Every dimmed control stays reachable.

Driving the game into ORBIT: a bot-only season never advances on its
own, so seat p1 is human and posts one empty policy. That resolves the
night (``auto_fire_bots``) and lands the session in the ORBIT phase with
a starting wallet, which is the state this panel exists for.

Scratch harness like the other ``backstage/probes/_fx_*.py`` — not pytest.

Usage::

    python backstage/probes/_fx_orbit.py --base http://127.0.0.1:8022

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

OUT = pathlib.Path(__file__).resolve().parents[2] / "reports" / "orbit"


def _post(base: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=60) as r:
        return json.loads(r.read().decode())


def _drive_to_orbit(base: str, sid: str) -> str:
    """Post empty policies until the session sits in ORBIT.

    One submit is normally enough. The loop exists because the bot seat
    resolves asynchronously on some paths and the phase can lag the
    response by a beat.
    """
    for _ in range(6):
        st = _get(base, f"/api/game/{sid}/status")
        phase = str(st.get("phase") or "")
        if phase == "orbit":
            return phase
        _post(base, f"/api/game/{sid}/policy", {"player": "p1", "moves": []})
        time.sleep(0.4)
    return str(_get(base, f"/api/game/{sid}/status").get("phase") or "?")


# Read the panel back. Font sizes are read COMPUTED, not from the class
# list, because "the wallet leads" is a claim about what the eye gets.
_PROBE = """
() => {
  const panel = document.getElementById('cc-panel-orbit');
  if (!panel) return null;
  const box = (el) => el.getBoundingClientRect();
  const px = (el, prop) => parseFloat(getComputedStyle(el)[prop]) || 0;
  const tile = (el) => {
    const label = el.querySelector('.cc-orbit-readout-label');
    const value = el.querySelector('.cc-orbit-readout-value');
    return {
      key: el.dataset.readout || null,
      label: label ? label.textContent.trim() : null,
      value: value ? value.textContent.trim().replace(/\\s+/g, ' ') : null,
      valueSize: value ? px(value, 'fontSize') : null,
      width: Math.round(box(el).width),
    };
  };
  const btn = (b) => {
    const wrap = b.closest(".cc-orbit-act") || b;
    return {
      action: b.dataset.orbitAction || null,
      text: b.textContent.trim().replace(/\\s+/g, ' '),
      dimmed: b.classList.contains('is-disabled'),
      opacity: Number(getComputedStyle(b).opacity),
      wrapOpacity: Number(getComputedStyle(wrap).opacity),
      events: getComputedStyle(b).pointerEvents,
      disabled: b.disabled,
      title: b.title,
      why: (() => {
        const n = wrap.querySelector('.cc-orbit-why');
        return n ? n.textContent.trim() : null;
      })(),
    };
  };
  return {
    tiles: [...panel.querySelectorAll('.cc-orbit-readout')].map(tile),
    readoutRight: (() => {
      const r = panel.querySelector('.cc-orbit-readouts');
      return r ? Math.round(box(r).right) : null;
    })(),
    panelRight: Math.round(box(panel).right),
    readoutHeight: (() => {
      const r = panel.querySelector('.cc-orbit-readouts');
      return r ? Math.round(box(r).height) : null;
    })(),
    buttons: [...panel.querySelectorAll('.solo-add-orbit')].map(btn),
    settleRed: (document.getElementById('cc-settle-red') || {}).textContent,
    settleGreen: (document.getElementById('cc-settle-green') || {}).textContent,
    // v1.27 — the three thirds. Reported as geometry rather than as class
    // names so the assertions describe what a player sees.
    thirds: ['.cc-orbit-facts', '.cc-orbit-actions', '.cc-orbit-policy']
      .map((sel) => {
        const el = panel.querySelector(sel);
        if (!el) return { sel, present: false };
        const b = box(el);
        return {
          sel,
          present: true,
          top: Math.round(b.top),
          bottom: Math.round(b.bottom),
          height: Math.round(b.height),
          scrollable: el.scrollHeight - el.clientHeight > 1,
        };
      }),
    commit: (() => {
      const b = document.getElementById('solo-commit-orbit');
      if (!b) return null;
      const r = box(b);
      return {
        top: Math.round(r.top),
        bottom: Math.round(r.bottom),
        inScroller: !!b.closest('.cc-orbit-policy-scroll'),
        inPolicy: !!b.closest('.cc-orbit-policy'),
        overflow: Math.max(0, b.scrollWidth - b.clientWidth),
      };
    })(),
    // Where the FACTS live, so a regression that leaves them in the
    // toolbar (which is where they were before v1.27) is caught.
    factsHold: (() => {
      const facts = panel.querySelector('.cc-orbit-facts');
      const ids = ['cc-orbit-credits', 'cc-orbit-assets-list', 'cc-settle-red'];
      return ids.map((id) => {
        const el = document.getElementById(id);
        return { id, inFacts: !!(el && facts && facts.contains(el)) };
      });
    })(),
    panelBottom: Math.round(box(panel).bottom),
  };
}
"""


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
    phase = _drive_to_orbit(base, sid)
    print(f"session {sid} · phase {phase}")
    if phase != "orbit":
        print(f"FAIL: could not reach the ORBIT phase (stuck at {phase!r})")
        return 1

    fails: list[str] = []
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        for width in (1280, 1024):
            pg = br.new_page(viewport={"width": width, "height": 800})
            pg.goto(f"{base}/?session={sid}&player=p1",
                    wait_until="networkidle")
            pg.wait_for_selector("#cc-panel-orbit", timeout=20000)
            # The ORBIT tab is only shown in-phase; click it to focus.
            tab = pg.query_selector('[data-cc-tab="orbit"]')
            if tab:
                tab.click()
            pg.wait_for_timeout(900)

            o = pg.evaluate(_PROBE)
            pg.screenshot(path=str(OUT / f"orbit_{width}.png"))
            if o is None:
                fails.append(f"{width}: no #cc-panel-orbit on the page")
                pg.close()
                continue

            print(f"\n=== {width} ===")
            print(f"readout block   : {o['readoutHeight']}px tall, "
                  f"{len(o['tiles'])} tiles")
            for t in o["tiles"]:
                print(f"   {str(t['key']):<14} {str(t['label']):<12} "
                      f"{str(t['value'])[:34]:<34} {t['valueSize']}px")
            print(f"settle red      : {o['settleRed']!r}")
            print(f"settle green    : {o['settleGreen']!r}")
            print("buttons         :")
            for b in o["buttons"]:
                print(f"   {str(b['action']):<16} dim={str(b['dimmed']):<5} "
                      f"op={b['opacity']:.2f}/{b['wrapOpacity']:.2f} "
                      f"ev={b['events']:<5} why={b['why']!r}")

            keys = [t["key"] for t in o["tiles"]]

            # (1) The tier breakdowns are retired.
            for gone in ("tier-counts", "blue-tier-counts"):
                if gone in keys:
                    fails.append(
                        f"{width}: readout {gone!r} is still on the panel — "
                        f"vault tier detail belongs to the VAULT tab and the "
                        f"settlement block, not the buying screen"
                    )

            # (2) The wallet is present and leads. Both spendables must
            #     exist, and must render larger than a fleet readout, or
            #     "lead" is a claim the layout does not honour.
            for need in ("credits", "blue"):
                if need not in keys:
                    fails.append(f"{width}: no {need!r} readout")
            wallet = [t for t in o["tiles"] if t["key"] in ("credits", "blue")]
            rest = [t for t in o["tiles"]
                    if t["key"] not in ("credits", "blue")
                    and t["valueSize"]]
            if wallet and rest:
                w_min = min(t["valueSize"] for t in wallet)
                r_max = max(t["valueSize"] for t in rest)
                print(f"wallet {w_min}px vs rest {r_max}px")
                if w_min <= r_max:
                    fails.append(
                        f"{width}: the wallet does not lead — credits/blue "
                        f"render at {w_min}px against {r_max}px for the "
                        f"fleet readouts"
                    )

            # (3) Nothing overruns the rail.
            if o["readoutRight"] and o["readoutRight"] > o["panelRight"] + 1:
                fails.append(
                    f"{width}: the readout block overruns the panel by "
                    f"{o['readoutRight'] - o['panelRight']}px"
                )

            # (4) Dimming. Every dimmed control must say WHY and must
            #     stay reachable — the ORDERS rule applies here too.
            for b in o["buttons"]:
                if b["dimmed"]:
                    if b["events"] == "none" or b["disabled"]:
                        fails.append(
                            f"{width}: {b['action']} is dimmed AND "
                            f"unreachable (events={b['events']}, "
                            f"disabled={b['disabled']})"
                        )
                    if not b["why"]:
                        fails.append(
                            f"{width}: {b['action']} is dimmed with no "
                            f"reason shown — a dead-looking button has to "
                            f"say what would revive it"
                        )
                    if min(b["opacity"], b["wrapOpacity"]) > 0.75:
                        fails.append(
                            f"{width}: {b['action']} is flagged dim but "
                            f"renders at opacity "
                            f"{min(b['opacity'], b['wrapOpacity'])}"
                        )

            # (5) REPAIR with nothing damaged is the canonical no-op.
            rep = next((b for b in o["buttons"] if b["action"] == "repair"),
                       None)
            if rep is None:
                fails.append(f"{width}: no repair button")
            elif not rep["dimmed"]:
                fails.append(
                    f"{width}: nothing is damaged on a fresh season but "
                    f"REPAIR is not dimmed"
                )

            # (6) The behavioural half of "dim is not gate". The checks
            #     above only prove the button is REACHABLE; this proves
            #     the handler does not bounce it. v0.9.1 refused a dimmed
            #     build with "! can't afford that right now", so the
            #     structural checks would have passed while the panel was
            #     still gating.
            dim_build = next(
                (b for b in o["buttons"]
                 if b["dimmed"] and b["action"] != "repair"), None,
            )
            if dim_build is None:
                print("note: nothing unaffordable to click-test at this "
                      "wallet — the enqueue check was skipped")
            else:
                before = pg.evaluate(
                    "() => document.querySelectorAll("
                    "'#solo-orbit-queue .solo-queue-row').length")
                pg.click(
                    f'.solo-add-orbit[data-orbit-action='
                    f'"{dim_build["action"]}"]')
                pg.wait_for_timeout(250)
                after = pg.evaluate(
                    "() => document.querySelectorAll("
                    "'#solo-orbit-queue .solo-queue-row').length")
                err = pg.evaluate(
                    "() => { const e = document.getElementById("
                    "'err-solo-orbit'); return e && !e.hidden "
                    "? e.textContent.trim() : null; }")
                print(f"dim click       : {dim_build['action']} "
                      f"queue {before} -> {after} err={err!r}")
                if after <= before:
                    fails.append(
                        f"{width}: clicking the dimmed "
                        f"{dim_build['action']} did not enqueue it — the "
                        f"panel is gating, not annotating"
                    )
                if err:
                    fails.append(
                        f"{width}: clicking the dimmed "
                        f"{dim_build['action']} raised {err!r}; an "
                        f"unaffordable order is a plan, not an error"
                    )
                pg.screenshot(path=str(OUT / f"orbit_overdraft_{width}.png"))

            # (7) v1.27 — the panel is three stacked thirds: facts, then
            #     options, then COMMIT over the queue.
            t = {d["sel"]: d for d in o["thirds"]}
            print("thirds          :")
            for sel in (".cc-orbit-facts", ".cc-orbit-actions",
                        ".cc-orbit-policy"):
                d = t.get(sel, {"present": False})
                if not d.get("present"):
                    fails.append(f"{width}: {sel} is missing from the panel")
                    continue
                print(f"   {sel:<20} {d['top']:>4}..{d['bottom']:<4} "
                      f"{d['height']:>4}px scroll={d['scrollable']}")

            present = [t[s] for s in
                       (".cc-orbit-facts", ".cc-orbit-actions",
                        ".cc-orbit-policy")
                       if t.get(s, {}).get("present")]
            if len(present) == 3:
                # Order and no overlap. A pane that starts above the one
                # before it has ended is a pane drawn on top of it.
                for a, b in zip(present, present[1:]):
                    if b["top"] < a["bottom"] - 1:
                        fails.append(
                            f"{width}: {b['sel']} starts at {b['top']} but "
                            f"{a['sel']} runs to {a['bottom']} — the thirds "
                            f"overlap"
                        )
                # Roughly equal, and the tolerance is not laziness.
                # `flex-basis: 0` under `box-sizing: border-box` is
                # clamped UP to each item's own padding + border, so the
                # three panes grow by an identical share of the free
                # space but start from different floors: at 1280 the
                # options pane carries 8px of padding top and bottom
                # plus a rule and lands ~16px taller than the policy
                # pane, which carries only the rule. Equal to the pixel
                # would mean giving all three the same chrome, which
                # would cost either a cramped scrollport or a padded
                # queue. 24px on a ~230px pane is under the threshold
                # where anyone reads them as unequal.
                hs = [d["height"] for d in present]
                if min(hs) > 0 and max(hs) - min(hs) > 24:
                    fails.append(
                        f"{width}: the thirds are not thirds — heights {hs}"
                    )
                # Nothing runs off the bottom of the rail.
                if present[-1]["bottom"] > o["panelBottom"] + 2:
                    fails.append(
                        f"{width}: the policy third ends at "
                        f"{present[-1]['bottom']} past the panel's "
                        f"{o['panelBottom']}"
                    )

            # The facts must be IN the facts third — before v1.27 the
            # settlement readout lived mid-toolbar, between two rows of
            # buttons, which is what made it read as a control.
            for f in o["factsHold"]:
                if not f["inFacts"]:
                    fails.append(
                        f"{width}: #{f['id']} is not inside .cc-orbit-facts"
                    )

            # COMMIT leads the bottom third and is outside its scroller,
            # so a long queue cannot carry it off the panel.
            c = o["commit"]
            if c is None:
                fails.append(f"{width}: no #solo-commit-orbit")
            else:
                if not c["inPolicy"]:
                    fails.append(
                        f"{width}: COMMIT is not in the bottom third")
                if c["inScroller"]:
                    fails.append(
                        f"{width}: COMMIT is inside .cc-orbit-policy-scroll "
                        f"— it would scroll away under a long queue"
                    )
                if c["overflow"] > 0:
                    fails.append(
                        f"{width}: the COMMIT label overflows its button by "
                        f"{c['overflow']}px (it will wrap to two lines)"
                    )
                pol = t.get(".cc-orbit-policy", {})
                if pol.get("present") and c["top"] > pol["top"] + 24:
                    fails.append(
                        f"{width}: COMMIT sits {c['top'] - pol['top']}px into "
                        f"the bottom third instead of leading it"
                    )

            # (8) COMMIT must not move as the queue fills. The click test
            #     above already queued one row; add several more and
            #     re-measure. A button that drifts under the cursor in
            #     proportion to how much you planned is the complaint that
            #     produced this layout.
            commit_before = (o["commit"] or {}).get("top")
            for _ in range(6):
                pg.click('.solo-add-orbit[data-orbit-action="build_probe"]')
                pg.wait_for_timeout(60)
            pg.wait_for_timeout(300)
            o2 = pg.evaluate(_PROBE)
            rows = pg.evaluate(
                "() => document.querySelectorAll("
                "'#solo-orbit-queue .solo-queue-row').length")
            commit_after = (o2["commit"] or {}).get("top")
            if commit_before is not None and commit_after is not None:
                drift = abs(commit_after - commit_before)
                print(f"commit drift    : {drift}px over {rows} queued rows")
                if drift > 1:
                    fails.append(
                        f"{width}: COMMIT moved {drift}px between an empty "
                        f"queue and {rows} rows"
                    )
            t2 = {d["sel"]: d for d in o2["thirds"]}
            if not t2.get(".cc-orbit-policy", {}).get("present"):
                fails.append(f"{width}: the bottom third vanished")
            pg.screenshot(path=str(OUT / f"orbit_queued_{width}.png"))

            pg.close()
        br.close()

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
