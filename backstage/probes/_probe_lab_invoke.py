"""Click the lab's INVOKE button in a real browser and report what happened.

A static complaint ("the button does nothing") has three shapes and the
console tells them apart in one run: the handler never fired, the fetch
never went out, or the fetch went out and the answer was never painted.
So this watches all three — console, network, and the DOM under the
seat block — rather than asserting on any one of them.

    python backstage/probes/_probe_lab_invoke.py --port 8022 --board LAB_cfa9912d_d6_p1
"""

from __future__ import annotations

import argparse
import json
import urllib.request


def _post(base: str, path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as fh:
        return json.load(fh)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8022)
    ap.add_argument("--board", default="LAB_cfa9912d_d6_p1")
    ap.add_argument("--agent", default="tabula_v12")
    ap.add_argument("--seat", default="p1")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    base = f"http://127.0.0.1:{args.port}"
    opened = _post(base, "/api/lab/open", {"board": args.board})
    url = base + opened["url"]
    print(f"board  {args.board}")
    print(f"url    {url}\n")

    console: list[str] = []
    errors: list[str] = []
    calls: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.on("console", lambda m: console.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on(
            "request",
            lambda r: calls.append(f"{r.method} {r.url.replace(base, '')}")
            if "/api/lab/" in r.url else None,
        )

        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(3000)

        # Why can the rows be in the DOM and not on screen? Walk up from
        # the box and print the first ancestor that is hiding it, because
        # "element is not visible" on its own does not say who did it.
        chain = page.evaluate(
            """() => {
              const el = document.getElementById('lab-invoke');
              if (!el) return 'no #lab-invoke at all';
              const out = [];
              for (let n = el; n && n !== document.body; n = n.parentElement) {
                const cs = getComputedStyle(n);
                const r = n.getBoundingClientRect();
                out.push({
                  tag: n.tagName.toLowerCase(),
                  id: n.id || '',
                  cls: (n.className || '').toString().slice(0, 60),
                  hidden: n.hasAttribute('hidden'),
                  display: cs.display, visibility: cs.visibility,
                  opacity: cs.opacity, h: Math.round(r.height),
                });
              }
              return out;
            }"""
        )
        print("\nvisibility chain from #lab-invoke upward:")
        for node in chain if isinstance(chain, list) else [chain]:
            print("   ", node)

        tabs = page.evaluate(
            """() => Array.from(document.querySelectorAll(
                 '.cc-tab, [data-panel], .cc-panel-tab, nav button'))
               .map(b => ({t: (b.textContent||'').trim().slice(0,18),
                           id: b.id||'', cls: (b.className||'').toString().slice(0,40),
                           dp: b.dataset.panel||''}))
               .slice(0, 30)"""
        )
        print("\ncandidate tab controls:")
        for t in tabs:
            print("   ", t)

        box = page.query_selector("#lab-invoke")
        print(f"#lab-invoke present : {bool(box)}")
        blocks = page.query_selector_all(".cc-lab-seat")
        print(f"seat blocks         : {[b.get_attribute('data-seat') for b in blocks]}")

        block = page.query_selector(f'.cc-lab-seat[data-seat="{args.seat}"]')
        if block is None:
            print(f"\nFAIL: no seat block for {args.seat}")
            print("console:", *console[-25:], sep="\n  ")
            browser.close()
            return 1

        pick = block.query_selector(".cc-lab-pick")
        options = page.eval_on_selector(
            f'.cc-lab-seat[data-seat="{args.seat}"] .cc-lab-pick',
            "el => Array.from(el.options).map(o => o.value)",
        )
        print(f"dropdown options    : {options}")

        btn = block.query_selector(".cc-lab-invoke-btn")
        print(f"button text         : {btn.text_content()!r}")
        print(f"button disabled     : {btn.get_attribute('disabled')}")

        pick.select_option(args.agent)
        print(f"selected            : {args.agent}")

        calls.clear()
        before = len(console)
        btn.click()
        page.wait_for_timeout(2500)

        print(f"\nafter click, button : {btn.text_content()!r} "
              f"busy={btn.get_attribute('data-busy')}")
        print(f"lab api calls       : {calls or 'NONE — the fetch never went out'}")

        out = block.query_selector(".cc-lab-out")
        print(f"output hidden       : {out.get_attribute('hidden') is not None}")
        print(f"output text         : {(out.text_content() or '')[:200]!r}")

        # V12 is slow; give it the same room the API took.
        page.wait_for_timeout(45000)
        print(f"\n45s later, button   : {btn.text_content()!r}")
        print(f"lab api calls       : {calls}")
        print(f"output text         : {(out.text_content() or '')[:400]!r}")

        new = console[before:]
        if new:
            print("\nconsole during click:")
            for line in new[:40]:
                print("   ", line[:220])
        if errors:
            print("\nPAGE ERRORS:")
            for e in errors:
                print("   ", e[:400])
        page.screenshot(path="/tmp/lab_invoke.png", full_page=False)
        print("\nscreenshot /tmp/lab_invoke.png")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
