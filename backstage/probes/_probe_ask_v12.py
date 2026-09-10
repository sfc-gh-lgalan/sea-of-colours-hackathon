"""Click "ask V12" in a real browser and report what the click did.

The API answers fine, so a report of "nothing happens" is about the page.
Three things can produce it and they need telling apart: the control is
hidden (so there was nothing to click), the handler ran and bailed, or
the request went out and the answer was never painted. This watches the
console, the network and the panel's own DOM so one run says which.

    python backstage/probes/_probe_ask_v12.py --port 8022 --session <id>
"""

from __future__ import annotations

import argparse


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8022)
    ap.add_argument("--session", required=True)
    ap.add_argument("--seat", default="p1")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    base = f"http://127.0.0.1:{args.port}"
    url = f"{base}/?session={args.session}&player={args.seat}"
    print(f"url  {url}\n")

    console: list[str] = []
    errors: list[str] = []
    calls: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.on("console", lambda m: console.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on(
            "response",
            lambda r: calls.append(f"{r.status} {r.request.method} "
                                   f"{r.url.replace(base, '')}")
            if "/advisor" in r.url else None,
        )

        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(6000)

        state = page.evaluate(
            """() => {
              const root = document.getElementById('cc-advisor');
              const btn  = document.getElementById('advisor-ask');
              const err  = document.getElementById('advisor-error');
              const res  = document.getElementById('advisor-result');
              const r = btn ? btn.getBoundingClientRect() : null;
              return {
                rootExists: !!root,
                rootHidden: root ? root.hidden : null,
                rootDisplay: root ? getComputedStyle(root).display : null,
                btnExists: !!btn,
                btnDisabled: btn ? btn.disabled : null,
                btnBox: r ? {w: Math.round(r.width), h: Math.round(r.height),
                             x: Math.round(r.x), y: Math.round(r.y)} : null,
                errShown: err ? !err.hidden : null,
                errText: err ? err.textContent : null,
                resultHidden: res ? res.hidden : null,
              };
            }"""
        )
        print("advisor panel state on load:")
        for k, v in state.items():
            print(f"   {k:14s} {v}")

        avail = page.evaluate(
            f"""async () => {{
              const r = await fetch('/api/game/{args.session}/advisor',
                                    {{cache:'no-store'}});
              return {{status: r.status, body: await r.text()}};
            }}"""
        )
        print(f"\nGET /advisor from the page : {avail}")

        if not state["btnExists"]:
            print("\nFAIL: no #advisor-ask in the DOM at all")
            browser.close()
            return 1
        if state["rootHidden"]:
            print("\nThe control is HIDDEN — there is nothing to click.")
            print("This is the 'nothing happens' report if the player "
                  "expected a button and found none.")

        # Click it regardless of hidden, via JS, so we learn what the
        # handler does even when the panel is not on screen.
        calls.clear()
        before = len(console)
        page.evaluate("document.getElementById('advisor-ask').click()")
        page.wait_for_timeout(2000)
        mid = page.evaluate(
            """() => ({
                 busy: document.getElementById('cc-advisor')
                         .classList.contains('cc-advisor--busy'),
                 timer: !document.getElementById('advisor-timer').hidden,
                 elapsed: document.getElementById('advisor-elapsed').textContent,
               })"""
        )
        print(f"\n2s after click : {mid}")
        print(f"advisor calls  : {calls or 'NONE — no request went out'}")

        page.wait_for_timeout(50000)
        after = page.evaluate(
            """() => {
              const err = document.getElementById('advisor-error');
              const res = document.getElementById('advisor-result');
              const pane = document.getElementById('advisor-pane-plan');
              return {
                errShown: !err.hidden, errText: (err.textContent||'').slice(0,300),
                resultHidden: res.hidden,
                planText: (pane.textContent||'').slice(0,300),
                meta: (document.getElementById('advisor-meta').textContent||''),
              };
            }"""
        )
        print(f"\n50s later:")
        for k, v in after.items():
            print(f"   {k:14s} {v!r}")
        print(f"advisor calls  : {calls}")

        new = console[before:]
        if new:
            print("\nconsole during click:")
            for line in new[:40]:
                print("   ", line[:220])
        if errors:
            print("\nPAGE ERRORS:")
            for e in errors:
                print("   ", e[:400])
        page.screenshot(path="/tmp/ask_v12.png")
        print("\nscreenshot /tmp/ask_v12.png")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
