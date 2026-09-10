"""Smoke-check the ASK V12 panel in a real browser.

Drives a fresh memory-backed game and asserts the two things that can
only break in the browser: the control wires up without throwing, and it
stays hidden on a game that cannot honour it. Then it force-shows the
panel with a stubbed answer to prove the tabs, the numbered map overlay
and [ adopt plan ] actually work end to end — the live path needs Cortex
credentials, which CI does not have.

Scratch harness, like the other ``backstage/probes/_fx_*.py`` — not a test.
"""

from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"

STUB = {
    "ok": True,
    "player": "p1",
    "day": 1,
    "elapsed_ms": 4210,
    # A five-step walk, then other work: exactly the shape the composer
    # used to render as five near-identical coordinate rows.
    "moves": [
        {"a": "step", "unit": "harvester_p1", "to": [12, 8]},
        {"a": "step", "unit": "harvester_p1", "to": [13, 8]},
        {"a": "step", "unit": "harvester_p1", "to": [14, 9]},
        {"a": "step", "unit": "harvester_p1", "to": [15, 10]},
        {"a": "step", "unit": "harvester_p1", "to": [15, 11]},
        {"a": "pickup", "unit": "harvester_p1"},
        {"a": "probe", "at": [7, 5]},
        {"a": "emp_launch", "at": [[9, 9], [10, 9]]},
    ],
    "thinking": {
        "reasoning": (
            "The seam at 7,5 is the cheapest read I have. Two nights ago the "
            "same bearing paid out a vein, and nothing since has contradicted "
            "it.\n\nThe alternative was to push the harvester east toward the "
            "remembered pure cell, but that cell has no live beacon and the "
            "drop would be refused, so it is not a real option this Nox."
        ),
        "intent": "bank the near seam before the vault fills",
        "reflection": (
            "Last night I over-probed and spent an hour I needed for the "
            "pickup. One probe is enough here."
        ),
        "option_menu": (
            "  probe_seam    cost 1 probe   est +120 red\n"
            "  chain_red     cost 0         est  +80 red\n"
            "  hold          cost 0         est    0"
        ),
        "ms": 3100,
    },
    "plan": {
        "posture": "greedy",
        "plan_ids": ["probe_seam", "chain_red"],
        "targets": [[12, 8]],
        "note": "bank RED before the vault fills",
        "sanitizer_changes": [],
        "fallback_used": False,
    },
    "prompts": {
        "think": "(think prompt, untruncated)",
        "plan": "(plan prompt, untruncated)",
        "mover": "",
    },
}


def main() -> int:
    errors: list[str] = []
    with sync_playwright() as p:
        br = p.chromium.launch(channel="chrome")
        pg = br.new_page(viewport={"width": 1500, "height": 1000})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: m.type == "error" and errors.append(m.text))

        pg.goto(BASE, wait_until="networkidle")
        pg.wait_for_timeout(1200)
        # The orders panel (and the advisor inside it) only exists once a
        # game is running, so take the landing page's one-click route in.
        pg.click("#btn-quick")
        pg.wait_for_selector("#solo-night-form", state="visible", timeout=60000)
        pg.wait_for_timeout(2500)

        # 1. Hidden on a memory game (no Snowflake => no advisor).
        gone = pg.evaluate(
            "() => { const e = document.getElementById('cc-advisor');"
            " return !e || e.hidden; }"
        )
        print(f"hidden on memory game: {gone}")

        # 2. Force the panel open with a stubbed answer and exercise it.
        pg.evaluate(
            """(stub) => {
              const real = window.fetch;
              window.fetch = (u, o) => {
                const s = String(u);
                if (s.includes('/advisor') && (!o || o.method !== 'POST')) {
                  return Promise.resolve(new Response(
                    JSON.stringify({available: true, reason: ''}),
                    {headers: {'Content-Type': 'application/json'}}));
                }
                if (s.includes('/advisor')) {
                  return Promise.resolve(new Response(JSON.stringify(stub),
                    {headers: {'Content-Type': 'application/json'}}));
                }
                return real(u, o);
              };
              const e = document.getElementById('cc-advisor');
              if (e) e.hidden = false;
            }""",
            STUB,
        )
        pg.click("#advisor-ask")
        pg.wait_for_timeout(900)

        state = pg.evaluate(
            """() => ({
              result: !document.getElementById('advisor-result').hidden,
              meta: document.getElementById('advisor-meta').textContent,
              moves: document.querySelectorAll('.cc-advisor-moves li').length,
              planVisible: !document.getElementById('advisor-pane-plan').hidden,
            })"""
        )
        print("after ask:", json.dumps(state))

        pg.click("#advisor-tab-think")
        think = pg.evaluate(
            "() => document.getElementById('advisor-pane-think').textContent.trim()"
        )
        print("thinking tab:", think[:60])

        pg.hover("#advisor-show")
        pg.wait_for_timeout(400)
        markers = pg.evaluate(
            """() => [...document.querySelectorAll('.advisor-marker')]
                 .map((m) => m.textContent)"""
        )
        print("overlay markers:", markers)
        pg.screenshot(path="/tmp/advisor_overlay.png")

        pg.mouse.move(0, 0)
        pg.wait_for_timeout(300)
        cleared = pg.evaluate(
            "() => document.querySelectorAll('.advisor-marker').length"
        )
        print("markers after unhover:", cleared)

        # The full card: it must carry the decision (including the intent
        # and reflection the inline pane has no room for), the orders and
        # the reasoning, all on the one tab.
        pg.click("#advisor-expand")
        pg.wait_for_timeout(500)
        card = pg.evaluate(
            """() => {
              const b = document.getElementById('advisor-modal-body');
              return {
                sections: [...b.querySelectorAll('.cc-card-section')]
                  .map((s) => s.textContent.trim()),
                terms: [...b.querySelectorAll('dt')].map((d) => d.textContent),
                orders: b.querySelectorAll('.cc-advisor-moves li').length,
                reasoning: (b.querySelector('.cc-card-pre--inline') || {})
                  .textContent || '',
              };
            }"""
        )
        print("card sections:", card["sections"])
        print("card terms:   ", card["terms"])
        print("card orders:  ", card["orders"])
        print("card reasoning:", card["reasoning"][:70].replace("\n", " "))
        pg.screenshot(path="/tmp/advisor_card.png")
        pg.click("#advisor-modal-close")
        pg.wait_for_timeout(250)

        pg.click("#advisor-adopt")
        pg.wait_for_timeout(400)
        adopted = pg.evaluate(
            """() => [...document.getElementById('solo-move-queue').children]
                 .map((r) => r.textContent.trim().replace(/\\s+/g, ' '))"""
        )
        print("adopted queue rows:", len(adopted))
        for r in adopted[:10]:
            print("   ", r[:90])
        pg.screenshot(path="/tmp/advisor_panel.png")

        # Does the composer actually fit its rail, or is the last
        # control being clipped off the edge?
        fit = pg.evaluate(
            """() => {
              const host = document.getElementById('solo-move-queue');
              const rows = [...host.querySelectorAll('.solo-queue-row')];
              return {
                host: Math.round(host.clientWidth),
                panel: Math.round(
                  (document.querySelector('.cc-drawer') || host).clientWidth),
                rows: rows.map((r) => ({
                  w: Math.round(r.clientWidth),
                  scroll: Math.round(r.scrollWidth),
                  over: r.scrollWidth - r.clientWidth,
                })),
                chain: (() => {
                  const out = [];
                  let el = host;
                  while (el && el !== document.body) {
                    out.push({
                      sel: el.tagName.toLowerCase()
                        + (el.id ? '#' + el.id : '')
                        + (typeof el.className === 'string' && el.className
                          ? '.' + el.className.trim().split(/\\s+/).join('.')
                          : ''),
                      client: Math.round(el.clientWidth),
                      scroll: Math.round(el.scrollWidth),
                      overflowX: getComputedStyle(el).overflowX,
                    });
                    el = el.parentElement;
                  }
                  return out;
                })(),
              };
            }"""
        )
        print("fit:", json.dumps(fit))

        # The composer itself: the five-step walk must read as one row.
        q = pg.query_selector("#solo-move-queue")
        if q:
            q.screenshot(path="/tmp/queue_collapsed.png")
            print("collapsed -> /tmp/queue_collapsed.png")
        # v1.20 — the whole ORDERS panel, to prove the sticky TRANSMIT
        # foot stays on screen with a queue long enough to scroll.
        panel = pg.query_selector("#cc-panel-orders")
        if panel:
            pg.evaluate(
                """() => { const p = document.getElementById('cc-panel-orders');
                           if (p) p.scrollTop = 0; }"""
            )
            panel.screenshot(path="/tmp/orders_full.png")
            print("panel     -> /tmp/orders_full.png")
            print(
                "toolbar visible:",
                pg.evaluate(
                    """() => { const t =
                         document.getElementById('solo-queue-toolbar');
                       return !!(t && !t.hidden && t.offsetParent); }"""
                ),
            )
            print(
                "transmit label:",
                pg.evaluate(
                    """() => (document.getElementById('solo-commit-night')
                        ||{}).textContent.trim().replace(/\\s+/g,' ')"""
                ),
            )

        twist = pg.query_selector(".solo-queue-twist")
        if twist:
            twist.click()
            pg.wait_for_timeout(350)
            q = pg.query_selector("#solo-move-queue")
            if q:
                q.screenshot(path="/tmp/queue_expanded.png")
                print("expanded  -> /tmp/queue_expanded.png")
        else:
            print("!! no chain summary row rendered")
        br.close()

    print("\npage errors:", errors or "none")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
