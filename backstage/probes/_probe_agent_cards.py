#!/usr/bin/env python3
"""Smoke-test the AGENT tab's card download in a real browser.

The endpoint is covered by tests; what those cannot catch is the button
not being there, being permanently disabled, or the click going nowhere
— all of which are the difference between a working feature and a dead
control in the corner of a panel.

Assumes a server already running with a persisted season on it::

    SOC_BACKEND=file SOC_STORE_DIR=.soc_probe_ui \\
        python scripts/soc.py season --p1 heuristic --p2 red_harvest_lite \\
        --days 2 --name UI_Probe --store-dir .soc_probe_ui
    SOC_BACKEND=file SOC_STORE_DIR=.soc_probe_ui \\
        python run_web.py --port 8023 --no-reload

    python backstage/probes/_probe_agent_cards.py --port 8023
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

OUT = Path("reports/agent-cards-probe")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8023)
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            accept_downloads=True,
        )
        page = ctx.new_page()
        page.on("console", lambda m: (
            problems.append(f"console.{m.type}: {m.text}")
            if m.type == "error" else None
        ))
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))

        # Load the most recent persisted season.
        sessions = page.request.get(f"{base}/api/sessions").json()
        rows = sessions if isinstance(sessions, list) else sessions.get(
            "sessions", [])
        if not rows:
            print("no persisted sessions on that server", file=sys.stderr)
            return 2
        sid = rows[0]["session_id"]
        print(f"session: {sid}  ({rows[0].get('season_name')})")

        page.goto(f"{base}/?session={sid}", wait_until="networkidle")
        page.wait_for_timeout(2500)

        # Open the AGENT tab.
        tab = page.query_selector('[data-cc-tab="log"]')
        if tab is None:
            problems.append("no AGENT tab found")
        else:
            tab.click()
            page.wait_for_timeout(1200)

        btn = page.query_selector("#agent-cards-download")
        if btn is None:
            problems.append("the .MD button is not in the DOM")
        else:
            print(f"button visible: {btn.is_visible()}  "
                  f"disabled: {btn.get_attribute('disabled') is not None}")
            if btn.get_attribute("disabled") is not None:
                problems.append(
                    "the .MD button is still disabled with a season loaded"
                )
            else:
                with page.expect_download(timeout=15000) as dl:
                    btn.click()
                got = dl.value
                dest = OUT / (got.suggested_filename or "cards.md")
                got.save_as(dest)
                text = dest.read_text(encoding="utf-8")
                print(f"downloaded: {dest.name}  {len(text)} bytes")
                if "agent cards" not in text.lower():
                    problems.append("the download does not look like a card")

        page.screenshot(path=str(OUT / "agent-tab.png"))
        browser.close()

    for problem in problems:
        print(f"  ! {problem}", file=sys.stderr)
    print("FAILED" if problems else "OK")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
