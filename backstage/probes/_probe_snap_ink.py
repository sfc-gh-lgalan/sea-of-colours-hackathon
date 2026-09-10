"""What the struck square actually looks like, close up (v1.40).

``_probe_snap.py`` answers "did it happen": the round resolves, the
scorch exists, the rack falls. This answers the different question the
v1.40 redraw was about — what the mark LOOKS like — because "one amber
cell" is not something a full-page shot at 1024px can settle.

It plays the same real night (p2 lights a cell, p1 SNAPs the beacon),
scrubs to the hour the round lands, and then reports what the tile is
actually carrying: the glyph, sampled repeatedly so the animation shows
up as a sequence rather than a still, and the computed style, so the
two properties the redraw was for can be checked rather than admired.
Those two are that the mark carries a block glyph from the shared
ordnance ramp instead of being a bare tint, and that nothing about it
glows — no box-shadow, no text-shadow, no filter.

Run against a server you started yourself (see AGENTS.md)::

    SOC_BACKEND=memory python run_web.py --no-reload --port 8022 --replace
    python backstage/probes/_probe_snap_ink.py
"""

from __future__ import annotations

import pathlib
import sys
import time

from playwright.sync_api import sync_playwright

# Sibling import, so this runs from the repo root like every other probe.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _probe_snap import new_game, open_page, req, wait_phase  # noqa: E402

#: Must match ``_ORDNANCE_CHARS`` in app.js — the ramp EMP and SNAP share.
RAMP = ["░░", "░▒", "▒▒", "▒▓", "▓▓", "██"]

TARGET = (10, 7)


def _play_a_snap_night() -> str:
    """The smash-and-grab denial, played for real. Same script as
    ``_probe_snap.scene_night`` — kept here rather than imported because
    that one is a printer, and this one needs the session back."""
    sid = new_game()
    for seat in ("p1", "p2"):
        req(f"/api/game/{sid}/policy", {"player": seat, "moves": []})
    wait_phase(sid, "orbit")
    req(f"/api/game/{sid}/orbit",
        {"player": "p1", "actions": [{"a": "build_snap"}], "commit": True})
    req(f"/api/game/{sid}/orbit", {"player": "p2", "actions": [], "commit": True})
    wait_phase(sid, "planning")

    req(f"/api/game/{sid}/policy",
        {"player": "p2", "moves": [{"a": "probe", "at": list(TARGET)}]})
    req(f"/api/game/{sid}/policy", {"player": "p1", "moves": []})
    wait_phase(sid, "orbit")
    for seat in ("p1", "p2"):
        req(f"/api/game/{sid}/orbit",
            {"player": seat, "actions": [], "commit": True})
    wait_phase(sid, "planning")

    req(f"/api/game/{sid}/policy",
        {"player": "p1", "moves": [{"a": "snap", "at": list(TARGET)}]})
    req(f"/api/game/{sid}/policy", {"player": "p2", "moves": [
        {"a": "drop", "unit": "harvester_p2", "at": list(TARGET)},
    ]})
    wait_phase(sid, "orbit")
    return sid


def main() -> int:
    sid = _play_a_snap_night()
    print(f"session {sid} — SNAP fired at {TARGET}")

    with sync_playwright() as pw:
        br, pg, errs = open_page(pw, sid)

        def scorch():
            return pg.evaluate(
                """() => document.querySelectorAll('.snap-cloud-cell').length""")

        # Rewind past the launch, then walk forward until the mark lands.
        for _ in range(40):
            pg.click("#replay-prev")
            time.sleep(0.03)
        time.sleep(1.2)

        landed = False
        for _ in range(28):
            pg.click("#replay-next")
            time.sleep(0.5)
            if scorch():
                landed = True
                break
        if not landed:
            print("  the scorch never appeared — nothing to look at")
            print("  console errors:", errs or "(none)")
            br.close()
            return 1

        slot = pg.evaluate(
            """() => document.getElementById('replay-slot')?.textContent""")
        print(f"\n=== the mark, at {slot} ===")

        seen = []
        for _ in range(10):
            g = pg.evaluate("""() => {
              const el = document.querySelector('.snap-cloud-cell');
              if (!el) return null;
              const cs = getComputedStyle(el);
              return { text: el.textContent, cls: el.className,
                       color: cs.color, bg: cs.backgroundColor,
                       shadow: cs.boxShadow, tshadow: cs.textShadow,
                       filter: cs.filter, border: cs.borderTopWidth,
                       font: cs.fontFamily.split(',')[0] };
            }""")
            if g:
                seen.append(g)
            pg.wait_for_timeout(150)

        if not seen:
            print("  the mark vanished while being sampled")
            br.close()
            return 1

        glyphs = [s["text"] for s in seen]
        print(f"  glyphs sampled over ~1.5s: {glyphs}")
        off = [g for g in glyphs if g and g not in RAMP]
        print(f"  every glyph from the shared ordnance ramp: "
              f"{'yes' if not off else 'NO — ' + str(off)}")
        print(f"  it animates: "
              f"{'yes' if len(set(glyphs)) > 1 else 'NO — single frozen glyph'}")

        s = seen[-1]
        print("\n=== is it flat? ===")
        for k in ("color", "bg", "border", "font", "shadow", "tshadow", "filter"):
            print(f"  {k:8} {s[k]}")
        glow = [k for k in ("shadow", "tshadow", "filter")
                if s[k] and s[k] not in ("none", "")]
        print(f"  -> {'GLOW STILL PRESENT: ' + str(glow) if glow else 'flat, no glow'}")

        cell = pg.query_selector(f'[data-x="{TARGET[0]}"][data-y="{TARGET[1]}"]')
        if cell and cell.bounding_box():
            b = cell.bounding_box()
            pad = 70
            pg.screenshot(path="/tmp/snap_cell.png", clip={
                "x": max(0, b["x"] - pad), "y": max(0, b["y"] - pad),
                "width": b["width"] + pad * 2, "height": b["height"] + pad * 2,
            })
            print("\n  crop -> /tmp/snap_cell.png")
        pg.screenshot(path="/tmp/snap_board.png")
        print("  board -> /tmp/snap_board.png")
        print(f"  console errors: {errs or '(none)'}")
        br.close()

        _seen_from_the_dark(pw, sid)
    return 0


def _seen_from_the_dark(pw, sid: str) -> None:
    """The half of this that actually matters: a SNAP is fired FROM AN
    ORBITAL STATION, so every seat watches it go regardless of who can
    see the ground it lands on. RULEBOOK §4.9.4 says "Visibility: open",
    and this is the check that the client agrees.

    p2 is the right seat to ask. Its probe was the thing the round
    killed, so the target cell is exactly where p2's vision was taken
    away — if the mark were fog-gated anywhere in the stack, this is the
    view it would be missing from."""
    print("\n=== the same round, watched by the seat that lost the probe ===")
    br, pg, errs = open_page(pw, sid, seat="p2")

    fog = pg.evaluate(f"""() => {{
      const c = document.querySelector('[data-x="{TARGET[0]}"][data-y="{TARGET[1]}"]');
      return c ? c.className : null;
    }}""")
    print(f"  p2's view of {TARGET}: {fog}")

    for _ in range(40):
        pg.click("#replay-prev")
        time.sleep(0.03)
    time.sleep(1.2)

    saw_scorch = saw_station = False
    for _ in range(28):
        pg.click("#replay-next")
        time.sleep(0.5)
        if pg.evaluate("""() => document.querySelectorAll('.snap-cloud-cell').length"""):
            saw_scorch = True
        # The platform beat is transient, so catch it any tick it shows.
        if pg.evaluate("""() => {
              const n = document.querySelectorAll('[data-os-anim], .os-anim');
              return n.length > 0;
            }"""):
            saw_station = True
        if saw_scorch:
            break

    print(f"  scorch mark rendered for p2 : {'yes' if saw_scorch else 'NO'}")
    print(f"  platform beat seen at least once: "
          f"{'yes' if saw_station else 'not caught (transient)'}")
    if saw_scorch:
        pg.screenshot(path="/tmp/snap_from_p2.png")
        print("  p2's board -> /tmp/snap_from_p2.png")
    print(f"  console errors: {errs or '(none)'}")
    br.close()


if __name__ == "__main__":
    sys.exit(main())
