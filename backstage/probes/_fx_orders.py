#!/usr/bin/env python3
"""Does the rebuilt ORDERS panel line up, and does it tell the truth?

The v1.24 redesign turned the roster from three status buckets of chips
into one grid row per actionable unit. Two things about it are worth
asserting rather than eyeballing, because both are exactly what the old
panel got wrong:

* **ALIGNMENT.** The old rows were flex-wrap, content-sized, and only
  some of them grew a plan annotation underneath — so the action
  affordances sat at a different x on every row. The check here is
  literal: every row's verb block must start at the same pixel.
* **THE ANNOTATION.** The panel is now an ANNOTATOR, not a gate. Queuing
  more probes than you hold must stay possible AND must be visible. So
  we queue an overdraft and assert both halves: the order lands, and the
  note says so.

Plus the stranding sticker, which is the one piece of information the
panel never carried: a harvester left on the surface at the end of your
queue is destroyed at Aurora (§3.11.2).

Scratch harness like the other ``backstage/probes/_fx_*.py`` — not pytest.

Usage::

    python backstage/probes/_fx_orders.py --base http://127.0.0.1:8022

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

OUT = pathlib.Path(__file__).resolve().parents[2] / "reports" / "orders"


def _post(base: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


# Read back the panel's geometry and text. Returns null when the roster
# has not rendered, so the caller can fail with a useful message.
_PROBE = """
() => {
  const host = document.getElementById('orders-asset-roster');
  if (!host) return null;
  const rows = [...host.querySelectorAll('.cc-fleet-row')];
  const box = (el) => el.getBoundingClientRect();
  return {
    rowCount: rows.length,
    // The regression: the verb block must start at the same x on
    // every row, whatever the unit is called or where it stands.
    verbX: rows.map((r) => {
      const a = r.querySelector('.cc-fleet-acts');
      return a ? Math.round(box(a).left * 10) / 10 : null;
    }),
    verbsPerRow: rows.map((r) =>
      [...r.querySelectorAll('.cc-fleet-verb')].map((b) => b.textContent.trim())),
    stickers: rows.map((r) =>
      [...r.querySelectorAll('.cc-sticker')].map((s) => s.textContent.trim())),
    plans: rows.map((r) => {
      const p = r.querySelector('.cc-fleet-plan');
      return p ? p.textContent.trim() : null;
    }),
    deployBtns: [...host.querySelectorAll('.cc-deploy-btn')]
      .map((b) => b.textContent.trim()),
    deployDisabled: [...host.querySelectorAll('.cc-deploy-btn')]
      .map((b) => b.disabled),
    // v1.25 dimming. Recorded per deploy LINE: whether the panel marked
    // it empty, what opacity that actually renders at, and whether the
    // pointer can still reach the button. The last one is the whole
    // point of the rule — dim must never quietly become disabled.
    deployLines: [...host.querySelectorAll('.cc-deploy-line')].map((l) => {
      const b = l.querySelector('.cc-deploy-btn');
      const note = l.querySelector('.cc-deploy-stock');
      return {
        label: b ? b.textContent.trim() : null,
        empty: l.classList.contains('cc-deploy-line--empty'),
        opacity: Number(getComputedStyle(l).opacity),
        events: getComputedStyle(b).pointerEvents,
        disabled: b ? b.disabled : null,
        note: note ? note.textContent.trim() : null,
      };
    }),
    stockNotes: [...host.querySelectorAll('.cc-deploy-stock')]
      .map((s) => s.textContent.trim()),
    // Anything sticking out past the rail's right edge is clipped and
    // therefore unclickable — the failure mode this panel already had
    // once (the v1.20 min-content clipping bug).
    hostRight: Math.round(box(host).right * 10) / 10,
    // A button squeezed narrower than its own label overlaps its
    // neighbour — how the first cut of this layout failed at 1024px.
    squeezed: [...host.querySelectorAll('.cc-fleet-verb, .cc-deploy-btn')]
      .filter((b) => b.scrollWidth > b.clientWidth + 1)
      .map((b) => b.textContent.trim()),
    // A unit's three verbs must share ONE line; if they wrap, every
    // harvester costs four lines of a rail that is already scrolling.
    verbLines: [...host.querySelectorAll('.cc-fleet-acts')].map(
      (a) => new Set([...a.querySelectorAll('.cc-fleet-verb')].map(
        (b) => Math.round(box(b).top))).size),
    overflowRight: [...host.querySelectorAll(
      '.cc-fleet-acts, .cc-deploy-line, .cc-fleet-mid')]
      .map((e) => Math.round((box(e).right - box(host).right) * 10) / 10)
      .filter((d) => d > 0.5),
    overNotes: [...host.querySelectorAll('.cc-deploy-stock--over')]
      .map((s) => s.textContent.trim()),
    // These must be GONE — they are the old vault-copy roster.
    legacyPods: host.querySelectorAll('.cc-orders-pod').length,
    legacyRows: host.querySelectorAll('.cc-orders-row').length,
    legacyWraps: host.querySelectorAll('.cc-orders-chip-wrap').length,
    dividers: [...host.querySelectorAll('.cc-orders-divider')]
      .map((d) => d.textContent.trim()),
  };
}
"""


# v1.25 — the queue composer itself. The rows lost their coordinate
# spinners and the commit bar moved to the top of the panel, and both of
# those are easy to half-break in a way a screenshot will not catch.
_QPROBE = """
() => {
  const host = document.getElementById('solo-move-queue');
  const commit = document.querySelector('.cc-orders-commit');
  // v1.27 — the panel is two halves and the DIRECTIVES are the bottom
  // one's only scroller. `.cc-orders-scroll` (the single v1.25 scroll
  // region) no longer exists.
  const scroll = document.querySelector('.cc-orders-queue-scroll');
  const compose = document.querySelector('.cc-orders-compose');
  const policy = document.querySelector('.cc-orders-policy');
  const roster = document.getElementById('orders-asset-roster');
  const toolbar = document.getElementById('solo-queue-toolbar');
  if (!host || !commit || !scroll || !compose || !policy) return null;
  const box = (el) => el.getBoundingClientRect();
  const rows = [...host.querySelectorAll('.solo-queue-row')];
  // How many visual lines does this row occupy? Cluster child tops with
  // a tolerance: children on one line still differ by a pixel or two
  // from baseline alignment, and comparing raw tops reports every row
  // as wrapped. 6px is comfortably under a line-height and comfortably
  // over the alignment jitter.
  const lineCount = (r) => {
    const tops = [...r.children]
      .filter((c) => c.getClientRects().length)
      .map((c) => box(c).top)
      .sort((a, b) => a - b);
    let lines = 0;
    let last = -1e9;
    for (const t of tops) {
      if (t - last > 6) { lines += 1; last = t; }
    }
    return lines;
  };
  return {
    rowCount: rows.length,
    // The spinners are the thing that was removed. Any survivor means
    // a code path still builds the old row.
    numInputs: host.querySelectorAll('.solo-num-input').length,
    xyWraps: host.querySelectorAll('.solo-queue-xy').length,
    // Thin AND unwrapped: every row's children share one baseline row.
    rowLines: rows.map(lineCount),
    rowHeights: rows.map((r) => Math.round(box(r).height)),
    controls: rows.map((r) =>
      [...r.querySelectorAll('.solo-queue-mini')].map(
        (b) => b.textContent.trim())),
    targets: rows.map((r) => {
      const a = r.querySelector('.solo-queue-at');
      return a ? a.textContent.trim() : null;
    }),
    labels: rows.map((r) => {
      const l = r.querySelector('.solo-queue-label');
      return l ? l.textContent.trim() : null;
    }),
    // The commit bar must sit ABOVE the scroll box, not inside it.
    commitBottom: Math.round(box(commit).bottom),
    scrollTop: Math.round(box(scroll).top),
    commitTop: Math.round(box(commit).top),
    commitInsideScroll: scroll.contains(commit),
    slotText: (document.getElementById('solo-queue-count') || {}).textContent,
    slotVisible: (() => {
      const s = document.getElementById('solo-queue-count');
      return !!(s && s.offsetParent !== null);
    })(),
    // Is the scroller actually the thing that scrolls?
    scrollable: scroll.scrollHeight > scroll.clientHeight + 1,
    scrollY: scroll.scrollTop,
    // v1.27 — the two-half split. The roster belongs to the TOP half and
    // the queue to the bottom one; if either drifts into the other the
    // panel is back to one long list with a bar stuck on it.
    composeBottom: Math.round(box(compose).bottom),
    policyTop: Math.round(box(policy).top),
    composeScrollable: compose.scrollHeight > compose.clientHeight + 1,
    composeScrollY: compose.scrollTop,
    rosterInCompose: !!roster && compose.contains(roster),
    rosterInPolicy: !!roster && policy.contains(roster),
    // The two utility buttons sit above the rows and must not scroll
    // away with them.
    toolbarPresent: !!toolbar && !toolbar.hidden,
    toolbarInsideScroll: !!toolbar && scroll.contains(toolbar),
    toolbarBottom: toolbar ? Math.round(box(toolbar).bottom) : null,
    toolbarFont: toolbar
      ? parseFloat(getComputedStyle(
          toolbar.querySelector('button') || toolbar).fontSize)
      : null,
    transmitFont: parseFloat(getComputedStyle(
      document.getElementById('solo-commit-night')).fontSize),
    // The button is `nowrap`, so a label that no longer fits the rail
    // shows up as horizontal overflow rather than a second line.
    transmitOverflow: (() => {
      const b = document.getElementById('solo-commit-night');
      return Math.round(b.scrollWidth - b.clientWidth);
    })(),
    transmitHeight: Math.round(
      box(document.getElementById('solo-commit-night')).height),
    hostRight: Math.round(box(host).right),
    overflowRight: rows
      .map((r) => Math.round(box(r).right - box(host).right))
      .filter((d) => d > 1),
  };
}
"""


# v1.27 made the banner's row PERMANENT (`visibility: hidden` when idle)
# so that arming an order stops shoving the board down a line. The cost is
# that `offsetParent !== null` is no longer a visibility test for this
# element — a `visibility: hidden` node still has one. Read the computed
# style or this probe silently reports the banner as up forever.
_BANNER_UP = """
() => {
  const b = document.querySelector('.pick-mode-banner');
  if (!b) return false;
  const cs = getComputedStyle(b);
  return cs.display !== 'none' && cs.visibility !== 'hidden';
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
    print(f"session {sid}")

    fails: list[str] = []
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1280, "height": 800})
        pg.goto(f"{base}/?session={sid}&player=p1", wait_until="networkidle")
        pg.wait_for_selector("#orders-asset-roster", timeout=20000)
        pg.wait_for_timeout(800)

        idle = pg.evaluate(_PROBE)
        if idle is None:
            print("FAIL: no #orders-asset-roster on the page")
            br.close()
            return 1
        print(f"fleet rows      : {idle['rowCount']}")
        print(f"dividers        : {idle['dividers']}")
        print(f"deploy buttons  : {idle['deployBtns']}")
        print(f"stock notes     : {idle['stockNotes']}")
        pg.screenshot(path=str(OUT / "orders_idle.png"))
        # v1.27 — where TRANSMIT sits on an EMPTY queue. The split is a
        # fixed fraction precisely so this does not change as the night
        # fills; sizing the bottom half to its content (the first cut)
        # walked the button ~200px up the rail as orders were added.
        q_empty = pg.evaluate(_QPROBE)

        if idle["rowCount"] < 1:
            fails.append("no fleet rows rendered — the seat owns harvesters")
        for k in ("legacyPods", "legacyRows", "legacyWraps"):
            if idle[k]:
                fails.append(f"{k}={idle[k]} — old vault-copy roster still present")

        # ALIGNMENT — the headline regression.
        xs = [x for x in idle["verbX"] if x is not None]
        if len(xs) != idle["rowCount"]:
            fails.append("a fleet row has no verb block")
        elif len(set(xs)) > 1:
            fails.append(f"verb columns do not align: {sorted(set(xs))}")
        else:
            print(f"verb column x   : {xs[0] if xs else '-'} (all rows agree)")

        # Every unit offers all three verbs — agency is not filtered.
        for i, vs in enumerate(idle["verbsPerRow"]):
            if vs != ["[DROP]", "[WALK]", "[LIFT]"]:
                fails.append(f"row {i} verbs are {vs}, expected DROP/WALK/LIFT")

        # Weapons must stay clickable at zero stock (annotate, don't gate).
        if any(idle["deployDisabled"]):
            fails.append(
                "a deploy button is disabled — the panel must annotate an "
                "overdraft, not prevent it"
            )

        # v1.25 DIMMING. Two halves, and the second is the one that
        # matters: an empty bay must LOOK empty, and must still be
        # reachable. The tempting fix for the first half (`disabled`, or
        # `pointer-events: none`) silently breaks the second, so both are
        # asserted here rather than trusting the class name.
        print("deploy lines    :")
        for ln in idle["deployLines"]:
            print(f"   {ln['label']:<18} empty={str(ln['empty']):<5} "
                  f"op={ln['opacity']:.2f} events={ln['events']} "
                  f"note={ln['note']!r}")
            held_none = (ln["note"] or "").startswith("0 held")
            if held_none and not ln["empty"]:
                fails.append(
                    f"{ln['label']} holds nothing but is not dimmed"
                )
            if ln["empty"]:
                if ln["opacity"] > 0.75:
                    fails.append(
                        f"{ln['label']} is marked empty but renders at "
                        f"opacity {ln['opacity']} — the dim rule is not "
                        f"reaching it"
                    )
                if ln["events"] == "none" or ln["disabled"]:
                    fails.append(
                        f"{ln['label']} is dimmed AND unreachable — dimming "
                        f"must not gate (events={ln['events']}, "
                        f"disabled={ln['disabled']})"
                    )

        # ── queue an overdraft of probes and a drop with no lift ──
        stock = pg.evaluate(
            "() => Number(window.__SOC_ORDERS_DBG__.stock().probe || 0)")
        n_over = int(stock) + 2
        pg.evaluate(
            """(n) => {
              for (let i = 0; i < n; i++) {
                window.__SOC_ORDERS_DBG__.addQueueRow('probe', 3 + i, 4);
              }
              const u = window.__SOC_ORDERS_DBG__.firstHarvester();
              if (u) window.__SOC_ORDERS_DBG__.addQueueRow('drop', 6, 6, u);
              window.__SOC_ORDERS_DBG__.rerender();
            }""",
            n_over,
        )
        pg.wait_for_timeout(300)
        after = pg.evaluate(_PROBE)
        pg.screenshot(path=str(OUT / "orders_overdraft.png"))
        print(f"\nprobe stock     : {stock}, queued {n_over}")
        print(f"overdraft notes : {after['overNotes']}")
        print(f"stickers        : {after['stickers']}")
        print(f"plans           : {[p for p in after['plans'] if p]}")

        if not after["overNotes"]:
            fails.append(
                f"queued {n_over} probes against a stock of {stock} and no "
                "note flagged the overdraft"
            )
        elif f"only {stock} held" not in after["overNotes"][0]:
            fails.append(f"overdraft note reads {after['overNotes'][0]!r}")

        flat = [s for row in after["stickers"] for s in row]
        if "stranded" not in flat:
            fails.append(
                "dropped a harvester with no LIFT queued and no STRANDED "
                "sticker appeared — that unit dies at Aurora (\u00a73.11.2)"
            )

        # Alignment must survive a row growing a plan line.
        xs2 = [x for x in after["verbX"] if x is not None]
        if len(set(xs2)) > 1:
            fails.append(
                f"verb columns fell out of line once a row grew a plan: "
                f"{sorted(set(xs2))}"
            )

        # ── the verbs must actually arm the picker ──
        # The redesign moved DROP/WALK off a global pod and onto the
        # unit, which means the verb now has to carry the unit id into
        # `assetSelect` itself. Clicking one and reading the state back
        # is the check that the row knows which harvester it is.
        pg.locator(".cc-fleet-row .cc-fleet-verb").first.click()
        pg.wait_for_timeout(200)
        armed = pg.evaluate(
            """() => {
              const b = document.querySelector('.cc-fleet-verb--armed');
              const banner = document.querySelector('.pick-mode-banner');
              return {
                btn: b ? b.textContent.trim() : null,
                rowArmed: !!document.querySelector('.cc-fleet-row--armed'),
                banner: banner && banner.offsetParent !== null
                  ? banner.textContent.trim().slice(0, 60) : null,
              };
            }"""
        )
        print(f"\narmed by click  : {armed}")
        if armed["btn"] != "[DROP]":
            fails.append(f"clicking [DROP] armed {armed['btn']!r}")
        if not armed["rowArmed"]:
            fails.append("the armed unit's row is not marked")
        if not armed["banner"]:
            fails.append("arming a verb did not raise the pick-mode banner")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(200)
        if pg.evaluate("() => !!document.querySelector('.cc-fleet-verb--armed')"):
            fails.append("Escape did not disarm the verb")

        # v1.27 — the roster now has the top half to itself, but it still
        # scrolls, so a tall fleet puts DEPLOY and TIMING below the fold.
        # Shoot the element itself so every block can be judged.
        pg.locator("#orders-asset-roster").screenshot(
            path=str(OUT / "orders_roster.png"))

        # ── v1.25: the queue composer ──
        # Fill it with a mix that exercises every row shape: a drop, a
        # walk chain (which collapses), a lift, probes and a chaff (which
        # reserves its two self-jam slots).
        pg.evaluate(
            """() => {
              const D = window.__SOC_ORDERS_DBG__;
              const u = D.firstHarvester();
              if (u) {
                D.addQueueRow('drop', 12, 4, u);
                for (let i = 0; i < 4; i++) D.addQueueRow('step', 13 + i, 5, u);
                D.addQueueRow('pickup', 0, 0, u);
              }
              D.addQueueRow('probe', 22, 8);
              D.addQueueRow('chaff_flare', 0, 0);
              D.rerender();
            }"""
        )
        pg.wait_for_timeout(300)
        q = pg.evaluate(_QPROBE)
        pg.screenshot(path=str(OUT / "orders_queue.png"))
        if q is None:
            fails.append("no queue host / commit bar / scroll region on the page")
        else:
            print(f"\nqueue rows      : {q['rowCount']}")
            print(f"labels          : {q['labels']}")
            print(f"targets         : {q['targets']}")
            print(f"row heights     : {q['rowHeights']}")
            print(f"slot badge      : {q['slotText']!r} visible={q['slotVisible']}")

            # THE removal. Any spinner left means a row path was missed.
            if q["numInputs"] or q["xyWraps"]:
                fails.append(
                    f"coordinate spinners still in the queue: "
                    f"{q['numInputs']} inputs / {q['xyWraps']} wrappers"
                )
            # ...but the target must still be WRITTEN somewhere, or the
            # removal has cost the player the only record of it.
            if not any(q["targets"]):
                fails.append(
                    "no row shows its target cell — dropping the spinners "
                    "must not drop the coordinates with them"
                )
            # Thin: one line per row, and no row taller than a comfortable
            # two-line box (a wrap doubles it, which is the failure).
            if any(n > 1 for n in q["rowLines"]):
                fails.append(
                    f"queue rows wrap onto multiple lines: {q['rowLines']}"
                )
            tall = [h for h in q["rowHeights"] if h > 26]
            if tall:
                fails.append(f"queue rows are not thin: heights {tall}")
            # Controls survive on every actionable row. A plain row gets
            # all three; a collapsed walk chain gets remove only (the
            # movers would have to reorder five slots at once).
            for i, c in enumerate(q["controls"]):
                if c and c not in (["\u2191", "\u2193", "\u00d7"], ["\u00d7"]):
                    fails.append(f"queue row {i} controls are {c}")
            if q["overflowRight"]:
                fails.append(
                    f"queue rows overrun the rail by {q['overflowRight']} px"
                )

            # LAYOUT: commit bar above the scroller, and outside it.
            if q["commitInsideScroll"]:
                fails.append(
                    "the commit bar is INSIDE the scroll region — it will "
                    "scroll away, which is the bug the move was made to fix"
                )
            if q["commitBottom"] > q["scrollTop"] + 1:
                fails.append(
                    f"commit bar (bottom {q['commitBottom']}) is not above "
                    f"the scroller (top {q['scrollTop']})"
                )
            if not q["slotVisible"]:
                fails.append("the N/21 slot badge is not visible")

            # TRANSMIT is in the SAME PLACE on a full night as an empty
            # one. This is the assertion the fixed split exists for.
            if q_empty:
                drift = abs(q["commitTop"] - q_empty["commitTop"])
                print(f"TRANSMIT drift  : {drift}px "
                      f"(empty {q_empty['commitTop']} -> "
                      f"full {q['commitTop']})")
                if drift > 1:
                    fails.append(
                        f"TRANSMIT moved {drift}px between an empty queue "
                        f"and {q['rowCount']} rows — the halves are sizing "
                        "to content again"
                    )

            # v1.27 — THE SPLIT. Orders you can make on top, the policy
            # you have composed below, and neither half may leak into the
            # other: a roster that ends up in the bottom half is back to
            # the one-long-list panel this replaced.
            if not q["rosterInCompose"] or q["rosterInPolicy"]:
                fails.append(
                    "the fleet roster is not in the TOP half "
                    f"(inCompose={q['rosterInCompose']} "
                    f"inPolicy={q['rosterInPolicy']})"
                )
            if q["composeBottom"] > q["policyTop"] + 1:
                fails.append(
                    f"the halves overlap: compose ends at "
                    f"{q['composeBottom']}, policy starts at {q['policyTop']}"
                )
            # The utility buttons are ABOVE the rows, outside the scroll
            # box, and rendered smaller than TRANSMIT — they are the
            # occasional controls, it is the nightly one.
            if q["toolbarPresent"]:
                if q["toolbarInsideScroll"]:
                    fails.append(
                        "[clear queue] / [show all] are inside the queue "
                        "scroller and will scroll away from the rows they "
                        "act on"
                    )
                if q["toolbarBottom"] > q["scrollTop"] + 1:
                    fails.append(
                        f"the utility buttons (bottom {q['toolbarBottom']}) "
                        f"are not above the directives "
                        f"(top {q['scrollTop']})"
                    )
                if not (q["toolbarFont"] < q["transmitFont"]):
                    fails.append(
                        f"the utility buttons ({q['toolbarFont']}px) are not "
                        f"smaller than TRANSMIT ({q['transmitFont']}px)"
                    )

            # And TRANSMIT must STAY put when EITHER half scrolls.
            pg.evaluate(
                "() => { for (const sel of ['.cc-orders-queue-scroll',"
                " '.cc-orders-compose']) {"
                " const s = document.querySelector(sel);"
                " if (s) s.scrollTop = s.scrollHeight; } }")
            pg.wait_for_timeout(200)
            q2 = pg.evaluate(_QPROBE)
            pg.screenshot(path=str(OUT / "orders_scrolled.png"))
            print(f"scrolled to     : queue {q2['scrollY']} "
                  f"(scrollable={q['scrollable']}) · "
                  f"compose {q2['composeScrollY']} "
                  f"(scrollable={q['composeScrollable']})")
            if q2["commitTop"] != q["commitTop"]:
                fails.append(
                    f"the commit bar moved when a half scrolled: "
                    f"{q['commitTop']} -> {q2['commitTop']}"
                )
            if q["scrollable"] and q2["scrollY"] == 0:
                fails.append(
                    "the directives overflow but .cc-orders-queue-scroll "
                    "did not scroll — the panel is probably the scroller"
                )
            if q["composeScrollable"] and q2["composeScrollY"] == 0:
                fails.append(
                    "the top half overflows but .cc-orders-compose did not "
                    "scroll"
                )

        # ── narrow rail ──
        pg.set_viewport_size({"width": 1024, "height": 768})
        pg.wait_for_timeout(300)
        narrow = pg.evaluate(_PROBE)
        qnarrow = pg.evaluate(_QPROBE)
        pg.screenshot(path=str(OUT / "orders_1024.png"))
        # The rail barely changes width between these two viewports, so a
        # control that fits at 1280 nearly fits at 1024 — which is how
        # TRANSMIT came to render as two lines with `PRAXIS ]` orphaned
        # underneath and nobody noticed. Both widths, every time.
        for label, snap in (("1280", q if q else None), ("1024", qnarrow)):
            if not snap:
                continue
            print(f"{label} TRANSMIT   : {snap['transmitFont']}px, "
                  f"h={snap['transmitHeight']} overflow="
                  f"{snap['transmitOverflow']}")
            if snap["transmitOverflow"] > 1:
                fails.append(
                    f"at {label}px the TRANSMIT label overflows its button "
                    f"by {snap['transmitOverflow']}px"
                )
            if snap["toolbarPresent"] and snap["toolbarInsideScroll"]:
                fails.append(
                    f"at {label}px the utility buttons are inside the queue "
                    "scroller"
                )
        xs3 = [x for x in narrow["verbX"] if x is not None]
        if len(set(xs3)) > 1:
            fails.append(f"verb columns misalign at 1024px: {sorted(set(xs3))}")
        else:
            print(f"\n1024px verb x   : {xs3[0] if xs3 else '-'}")
        for label, snap in (("1280", after), ("1024", narrow)):
            if snap["squeezed"]:
                fails.append(
                    f"at {label}px these are narrower than their own label "
                    f"and overlap a neighbour: {snap['squeezed']}"
                )
            if snap["overflowRight"]:
                fails.append(
                    f"at {label}px content overruns the rail by "
                    f"{snap['overflowRight']} px"
                )
            if any(n > 1 for n in snap["verbLines"]):
                fails.append(
                    f"at {label}px a unit's verbs wrap onto "
                    f"{max(snap['verbLines'])} lines"
                )
        print(f"verb lines/unit : 1280 {after['verbLines']} \u00b7 "
              f"1024 {narrow['verbLines']}")

        # ── v1.28: TRANSMIT must take the aiming banner down WITH it ──
        #
        # The path picker is sticky — chaining re-arms after every click
        # and only [ stop ] lowers it — so the banner is normally still up
        # when TRANSMIT is pressed. It blinks twice a second, so it sat
        # flashing over the board for the length of the commit, telling
        # you to "click neighbour to chain" into a queue already gone.
        #
        # v1.26 disarms on the phase flip, which is driven by the 2.5s
        # poller. That is why this asserts a DEADLINE and not merely that
        # the banner ends up down: the late disarm would satisfy a check
        # with no clock in it, and the late disarm is the bug.
        #
        # Runs last, because unlike everything above it really submits.
        pg.set_viewport_size({"width": 1280, "height": 900})
        pg.wait_for_timeout(300)
        pg.locator(".cc-fleet-row .cc-fleet-verb").first.click()
        pg.wait_for_timeout(250)
        # The strand and full-vault guards bail on the FIRST click and
        # leave you composing — deliberately, so a warning does not also
        # confiscate your aim. This fixture strands a harvester, so the
        # first click always warns. Ack it, then measure the real one.
        # Read the banner SYNCHRONOUSLY, in the same task as the click.
        #
        # A wall-clock deadline cannot tell the two disarms apart here:
        # on the memory backend the night resolves in ~250ms, so v1.26's
        # phase-flip disarm looks instant and a timed check passes with
        # the submit disarm removed. (It measured 243ms with the fix and
        # 255ms without — the bug is invisible to a clock on this backend
        # and only shows on Snowflake, where a commit takes seconds. That
        # is exactly how it reached a user.)
        #
        # `submitSoloNight` is async but disarms BEFORE its first await,
        # so a synchronous read after `.click()` returns sees the submit's
        # own disarm and cannot see anything needing a round trip.
        _CLICK_AND_READ = """() => {
          const up = () => {
            const el = document.querySelector('.pick-mode-banner');
            if (!el) return false;
            const cs = getComputedStyle(el);
            return cs.display !== 'none' && cs.visibility !== 'hidden';
          };
          const before = up();
          document.getElementById('solo-commit-night').click();
          return { before, afterSync: up() };
        }"""
        clicked = pg.evaluate(_CLICK_AND_READ)
        t0 = time.time()
        pg.wait_for_timeout(250)
        # A guard that BAILS must leave the aim alone — you are still
        # composing, and confiscating it would punish reading a warning.
        # So when the first click only warned, that click proves nothing
        # either way and the measurement moves to the one that submits.
        warned = pg.evaluate(
            "() => { const e = document.getElementById('err-solo');"
            "        return !!e && !e.hidden; }"
        )
        if warned:
            print("transmit guard  : warned once, acking")
            if clicked["afterSync"] is False:
                fails.append(
                    "a bailed TRANSMIT disarmed the picker — a warning must "
                    "not also confiscate the aim you are still composing"
                )
            clicked = pg.evaluate(_CLICK_AND_READ)
            t0 = time.time()
        armed_before = clicked["before"]
        # v1.28 — and while the night resolves, watch the extract strip.
        #
        # SEED / RED ON MAP / EXTRACTED is omniscient: the seed regenerates
        # the board offline, the total is unseeable through fog, and the
        # percentage is summed over every seat so your own take subtracts
        # to your rival's. It was gated on `mainMapSource === "replay"`,
        # which reads as "replay only" and is not — the night cinematic
        # sets exactly that on a live board.
        #
        # This is the only harness that commits a real night from a real
        # seat, so it is the only one that reaches the leaking state. The
        # strip refreshes on replay LOAD, not on scrub, which is why
        # poking the scrubber cannot stand in for it.
        # TWO nights, and the second is what does it. The strip refreshes
        # from `syncReplayRowOnly`, which runs on replay LOAD — not on a
        # scrub, and not on a paint. Night one's load happens while the
        # board is still `live`, so it reads correctly hidden however
        # broken the gate is; it is night one's CINEMATIC that leaves
        # `mainMapSource === "replay"`, and night two's load then lands on
        # top of it. A one-night fixture passes vacuously.
        strip_leaked = False

        def _sample_strip() -> None:
            nonlocal strip_leaked
            if not strip_leaked and pg.eval_on_selector(
                "#cc-extract-strip", "el => !el.hidden"
            ):
                strip_leaked = True

        banner_ms = None
        for _ in range(40):
            _sample_strip()
            if banner_ms is None and not pg.evaluate(_BANNER_UP):
                banner_ms = (time.time() - t0) * 1000.0
            pg.wait_for_timeout(50)
            if banner_ms is not None and strip_leaked:
                break

        # Night one's cinematic leaves `mainMapSource === "replay"`. Under
        # the old gate the strip then shows on the NEXT replay rebuild, so
        # force one rather than waiting for night two: switching replay
        # perspective reloads the frames, which calls `syncReplayRowOnly`
        # and re-decides the strip — with the board still in replay.
        rebuilt = False
        for _ in range(25):
            _sample_strip()
            if strip_leaked:
                break
            pg.wait_for_timeout(300)
            if not rebuilt and pg.evaluate(
                "() => !!document.querySelector('.cc-replay-view-btn')"
            ):
                pg.evaluate("""() => {
                  const b = document.querySelectorAll('.cc-replay-view-btn');
                  if (b.length) b[b.length - 1].click();
                }""")
                rebuilt = True
                pg.wait_for_timeout(600)
                _sample_strip()
        print(f"replay rebuilt  : {rebuilt}")
        strip_dbg = pg.evaluate("""() => {
          const D = window.__SOC_HEADER_DBG__;
          if (!D) return null;
          const x = D.extraction();
          return { hasData: !!(x && x.map_red_value > 0),
                   mapSource: D.mapSource(), watchMode: D.watchMode(),
                   livePhase: D.livePhase() };
        }""")
        print(f"extract strip   : leaked={strip_leaked} · {strip_dbg}")
        if strip_leaked:
            fails.append(
                "the extract strip (SEED / RED ON MAP / EXTRACTED) showed "
                "during a live commit — that is the whole map's RED total "
                "and every seat's take, on a fogged board"
            )
        elif not (strip_dbg or {}).get("hasData"):
            print("WARN: no extraction payload reached the client, so the "
                  "strip stayed hidden for the wrong reason")
        # HONESTY NOTE, so nobody reads more into this line than it says:
        # unlike the banner check above, this one has never been observed
        # to FAIL. Backing the gate out leaves it passing, because on the
        # memory backend nothing re-runs `updateExtractStrip` between the
        # board reaching `mainMapSource === "replay"` and the end of the
        # run — the preconditions printed above are all met, the decision
        # simply is not retaken. It is therefore a one-way guard: it can
        # catch the strip appearing where it must not, and cannot prove
        # the reported sighting is gone. That still wants a live
        # Snowflake night.
        print(f"\nbanner at submit: up_before={armed_before} "
              f"down_in_same_task={not clicked['afterSync']} "
              f"(wall clock {banner_ms if banner_ms is None else round(banner_ms)}ms, "
              f"not asserted)")
        pg.screenshot(path=str(OUT / "orders_transmit.png"))
        if not armed_before:
            fails.append(
                "could not raise the aiming banner before TRANSMIT, so the "
                "disarm-on-submit check proves nothing"
            )
        elif clicked["afterSync"]:
            fails.append(
                "TRANSMIT did not take the aiming banner down with it — it "
                "blinks twice a second, so it flashes over the board for the "
                "length of the commit, telling you to aim into a queue that "
                "has already gone"
            )

        br.close()

    for f in fails:
        print(f"FAIL: {f}")
    print(f"\nshots in {OUT}")
    print("PASS" if not fails else f"{len(fails)} FAILURE(S)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
