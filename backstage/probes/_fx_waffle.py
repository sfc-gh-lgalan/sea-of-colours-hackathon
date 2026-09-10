"""Smoke-check the end-of-season extraction block + waffle.

Stubs ``/summary`` in the page so this works against a server that
predates the payload, and so the arithmetic is checked against numbers
we control rather than whatever the last season happened to do.

Scratch harness, like the other ``backstage/probes/_fx_*.py`` — not a test.
"""

from __future__ import annotations

import json

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"

# Deliberately awkward: the four shares are 19.7 / 17.0 / 19.0 / 19.0 of
# a 9581-point map, so the largest-remainder allocation has real work to
# do and the waffle must still come to exactly 100 cells.
MAP_VALUE = 9581
STUB = {
    "session_id": "stub",
    "season_name": "Stub_Lattice",
    "season_day_cap": 7,
    "is_season_complete": True,
    "days": [1, 2, 3, 4, 5, 6, 7],
    "seed": 1695867309,
    "combat_seats": [
        {"seat": "p1", "name": "Phoenix", "tag": "PHX", "color": "#FFFFFF"},
        {"seat": "p2", "name": "Ursus", "tag": "URS", "color": "#FCF871"},
    ],
    "players": [
        {
            "seat": "p1", "name": "Phoenix", "tag": "PHX", "is_human": True,
            "color": "#FFFFFF", "score": 1527, "rank": 1,
            "breakdown": {"shipped": 1627, "green_penalty": 100,
                          "vault_red_loss": 0},
            "credits_spent": 40, "harvesters_built": 2,
            "red_harvested": 31, "green_harvested": 1, "blue_harvested": 1,
            "blue_spent": 0, "green_jettisoned": 0, "green_held": 1,
            "vault": {"red_by_tier": {}, "red_total": 0, "green": 1, "blue": 0},
            "combat": {}, "killfeed": {}, "personal": {"red_harvested": 31},
        },
        {
            "seat": "p2", "name": "Ursus", "tag": "URS", "is_human": False,
            "color": "#FCF871", "score": 1418, "rank": 2,
            "breakdown": {"shipped": 1818, "green_penalty": 400,
                          "vault_red_loss": 0},
            "credits_spent": 30, "harvesters_built": 2,
            "red_harvested": 11, "green_harvested": 5, "blue_harvested": 5,
            "blue_spent": 0, "green_jettisoned": 0, "green_held": 4,
            "vault": {"red_by_tier": {}, "red_total": 0, "green": 4, "blue": 0},
            "combat": {}, "killfeed": {}, "personal": {"red_harvested": 11},
        },
    ],
    "red_by_day": {"p1": [3, 6, 12, 20, 23, 28, 31],
                   "p2": [0, 3, 8, 8, 10, 11, 11]},
    "shipped_by_day": {"p1": [0, 25, 89, 217, 1276, 1441, 1441],
                       "p2": [0, 0, 51, 1687, 1687, 1818, 1818]},
    "manifest": [],
    "extraction": {
        "map_red_value": MAP_VALUE,
        "map_red_cells": 304,
        "map_by_tier": {"trace": 244, "vein": 56, "mass": 1, "pure": 3},
        "harvested_value": 3707,
        "shipped_value": 3445,
        "unmined_value": MAP_VALUE - 3707,
        "cells_mined": 42,
        "pct_harvested": 38.7,
        "pct_shipped": 36.0,
        "pct_cells": 13.8,
        "by_seat": {
            "p1": {"harvested_value": 1889, "shipped_value": 1627,
                   "unbanked_value": 262, "cells": 31,
                   "pct_harvested": 19.7, "pct_shipped": 17.0},
            "p2": {"harvested_value": 1818, "shipped_value": 1818,
                   "unbanked_value": 0, "cells": 11,
                   "pct_harvested": 19.0, "pct_shipped": 19.0},
        },
        "harvested_by_day": {
            "p1": [180, 360, 700, 1100, 1400, 1700, 1889],
            "p2": [0, 200, 500, 900, 1300, 1700, 1818],
        },
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
        pg.wait_for_timeout(1000)
        pg.click("#btn-quick")
        pg.wait_for_selector("#solo-night-form", state="visible", timeout=60000)
        pg.wait_for_timeout(2000)

        pg.evaluate(
            """(stub) => {
              const real = window.fetch;
              window.fetch = (u, o) => {
                const s = String(u);
                if (s.includes('/summary')) {
                  return Promise.resolve(new Response(JSON.stringify(stub),
                    {headers: {'Content-Type': 'application/json'}}));
                }
                return real(u, o);
              };
            }""",
            STUB,
        )
        # The reopen pill is the only handle on openEndGameModal from
        # outside the IIFE; un-hide it and click rather than reaching in.
        pg.evaluate(
            """() => {
              const b = document.getElementById('cc-endgame-reopen');
              if (b) { b.hidden = false; b.style.display = 'block'; }
            }"""
        )
        pg.click("#cc-endgame-reopen")
        pg.wait_for_timeout(1400)

        shown = pg.evaluate(
            "() => { const e = document.getElementById('cc-endgame');"
            " return !!e && !e.hidden; }"
        )
        print("endgame modal open:", shown)

        stats = pg.evaluate(
            """() => {
              const cells = [...document.querySelectorAll('.eg-waffle-cell')];
              const count = (c) => cells.filter(
                (e) => e.classList.contains(c)).length;
              const pct = document.querySelector('.eg-extract-pct');
              return {
                total: cells.length,
                banked: count('eg-waffle-cell--banked'),
                unbanked: count('eg-waffle-cell--unbanked'),
                untouched: count('eg-waffle-cell--untouched'),
                headline: pct ? pct.textContent.trim() : null,
                legend: [...document.querySelectorAll('.eg-waffle-key')]
                  .map((e) => e.textContent.replace(/\\s+/g, ' ').trim()),
              };
            }"""
        )
        print("waffle:", json.dumps(stats, indent=2))

        ok = stats["total"] == 100
        print("exactly 100 cells:", ok)
        mined = stats["banked"] + stats["unbanked"]
        print(f"mined cells {mined} (expect ~39 for 38.7%)")

        el = pg.query_selector(".eg-extract")
        if el:
            el.scroll_into_view_if_needed()
            pg.wait_for_timeout(200)
            el.screenshot(path="/tmp/waffle.png")
            print("shot -> /tmp/waffle.png")

        print("page errors:", errors or "none")
        br.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
