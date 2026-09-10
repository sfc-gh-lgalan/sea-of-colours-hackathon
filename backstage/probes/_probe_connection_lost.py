"""Does a locked-in player find out when the server goes away? (v1.46)

Written after a three-way game died mid-season behind a tunnel that
stopped routing. The engine was fine and the state was safe; what broke
was that nobody could tell. A player who has committed sits in the
"LOCKED IN — waiting for <names>" frame with their button deliberately
disabled, and the only thing that can release them is the live-sync
poller seeing a phase change. That poller swallowed every failure
(``catch (_e) { /* transient */ }``), so once the connection went, the
frame froze on a sentence that was no longer true — and it looks exactly
like legitimately waiting for a slow teammate. Three players each
concluded they were waiting on someone else.

So the thing to prove is not "the poller retries". It is that the
headline stops blaming a teammate and names the connection, and then
goes back to normal when the connection does. A unit test cannot see a
headline, which is why this drives a real browser.

The outage is faked by aborting ``/status`` at the browser, not by
stopping the server: it reproduces both real shapes (a refused request
and a 503 from a tunnel edge that is still answering) without touching
anything anyone else might be using.

Run it against a server you started yourself (see AGENTS.md)::

    SOC_BACKEND=memory python run_web.py --no-reload --port 8022 --replace
    python backstage/probes/_probe_connection_lost.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8022"

# The poller runs every ~2.5s and gives up after _LIVE_SYNC_OFFLINE_AFTER
# (3) consecutive misses, so ~7.5s is the floor. Wait past it rather than
# on it — a probe that races the thing it measures reports noise.
SETTLE_S = 18.0


def req(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.loads(resp.read())


def new_game():
    """Three humans, because the bug needs someone left to wait *for*."""
    return req("/api/game/new", {
        "players": ["p1", "p2", "p3"],
        "agents": {"p1": "human", "p2": "human", "p3": "human"},
        "width": 24, "height": 16, "season_day_cap": 6,
        "weapons_enabled": True, "signs_enabled": True,
        "backend": "memory", "seed": 4461,
    })["session_id"]


def headline(pg):
    el = pg.query_selector(".cc-resolving-recap-head")
    return (el.inner_text() if el else "").strip()


def committed(pg):
    """Is the page in the locked-in wait frame with the button held?"""
    return pg.evaluate(
        "() => { const b = document.getElementById('solo-commit-night');"
        " return { head: (document.querySelector('.cc-resolving-recap-head')"
        "   || {}).textContent || '', locked: b ? b.disabled : null }; }"
    )


def _cut(pg, mode):
    """Break the status poll in one of the three shapes seen in the wild.

    ``hang`` is the important one and the least obvious: the request is
    accepted and then simply never answered, which is what a tunnel whose
    edge has stopped routing actually does. It is the only shape that can
    wedge a poller with no request deadline, because the promise never
    settles and the in-flight latch is only released when it does.
    """
    if mode == "abort":
        pg.route("**/status*", lambda route: route.abort())
    elif mode == "503":
        pg.route("**/status*", lambda route: route.fulfill(
            status=503, content_type="text/plain", body="Service Unavailable",
        ))
    elif mode == "hang":
        # Deliberately neither fulfil, continue nor abort.
        pg.route("**/status*", lambda route: None)
    else:
        raise ValueError(mode)


def banner(pg):
    """The page-level offline banner — the only signal a reloaded player
    can get, since the wait overlay does not survive a reload."""
    return pg.evaluate(
        "() => { const el = document.getElementById('cc-offline-banner');"
        " return (el && !el.hidden) ? el.textContent.trim() : ''; }"
    )


def run(pw, mode):
    """``mode`` is how the outage presents: 'abort' or '503'."""
    print(f"\n=== outage shape: {mode} ===")
    sid = new_game()

    br = pw.chromium.launch()
    pg = br.new_page(viewport={"width": 1440, "height": 900})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)))
    pg.goto(f"{BASE}/play?session={sid}&player=p1", wait_until="networkidle")
    time.sleep(4)

    # Commit p1 with an empty queue. p2 and p3 stay untouched, so the
    # phase cannot resolve and p1 lands in the committed wait frame —
    # which is the only state where this bug exists.
    btn = pg.query_selector("#solo-commit-night")
    if not btn:
        print("  ! no night commit button — wrong phase?")
        br.close()
        return
    btn.click()
    time.sleep(6)

    before = committed(pg)
    print(f"  after commit : {before['head']!r}  (button locked: {before['locked']})")
    if "LOCKED IN" not in before["head"]:
        print("  ! never entered the committed wait frame; rest is meaningless")
        br.close()
        return

    # Cut the poller off. Everything else keeps working, exactly as it
    # does when a tunnel edge stops routing but the page stays loaded.
    _cut(pg, mode)
    print(f"  cut the status poll, waiting {SETTLE_S:.0f}s …")
    time.sleep(SETTLE_S)

    during = committed(pg)
    print(f"  during outage: {during['head']!r}")
    said_so = "CONNECTION LOST" in during["head"]
    print(f"  -> headline names the connection : {'YES' if said_so else 'NO'}")
    if not said_so:
        print("     (this is the bug: still blaming a teammate for our silence)")
    print(f"  -> banner                        : {banner(pg)[:60]!r}")

    # And back. A warning that cannot clear is its own false alarm — the
    # players stop believing the next one.
    pg.unroute("**/status*")
    time.sleep(SETTLE_S)
    after = committed(pg)
    print(f"  after recovery: {after['head']!r}  (button locked: {after['locked']})")
    recovered = "CONNECTION LOST" not in after["head"] and "LOCKED IN" in after["head"]
    cleared = not banner(pg)
    print(f"  -> clears when the server returns : "
          f"{'YES' if recovered and cleared else 'NO'}")

    if errs:
        print(f"  console errors: {errs[:4]}")

    br.close()
    return said_so and recovered and cleared


def run_reloaded(pw):
    """The case the first fix did NOT cover, and the one that actually
    happened: a player is handed a fresh invite link after the hostname
    rotated, loads it, and the connection dies again.

    A reload destroys the committed-wait overlay — it is built by the
    submit handler, not restored from the server — so the headline this
    file's other scenes rely on does not exist for them. Everything else
    on their screen is painted from the last good poll and therefore just
    freezes, which is indistinguishable from a quiet game. Only a
    page-level banner can reach them.
    """
    print("\n=== reloaded player (fresh link after a rotation) ===")
    sid = new_game()

    br = pw.chromium.launch()
    pg = br.new_page(viewport={"width": 1440, "height": 900})
    pg.goto(f"{BASE}/play?session={sid}&player=p1", wait_until="networkidle")
    time.sleep(4)
    btn = pg.query_selector("#solo-commit-night")
    if btn:
        btn.click()
        time.sleep(6)

    # The reload. Same session, same seat — exactly what a new invite link
    # is, minus the hostname change we cannot stage locally.
    pg.reload(wait_until="networkidle")
    time.sleep(5)
    print(f"  overlay after reload : {committed(pg)['head']!r}  "
          "(empty is expected — it does not survive a reload)")

    _cut(pg, "hang")
    print(f"  cut the status poll, waiting {SETTLE_S:.0f}s …")
    time.sleep(SETTLE_S)
    seen = banner(pg)
    print(f"  banner during outage : {seen[:72]!r}")
    told = "CONNECTION LOST" in seen
    print(f"  -> reloaded player is told : {'YES' if told else 'NO'}")

    pg.unroute("**/status*")
    time.sleep(SETTLE_S)
    cleared = not banner(pg)
    print(f"  -> and it clears           : {'YES' if cleared else 'NO'}")
    br.close()
    return told and cleared


def main():
    from playwright.sync_api import sync_playwright
    ok = True
    with sync_playwright() as pw:
        for mode in ("abort", "503", "hang"):
            ok = bool(run(pw, mode)) and ok
        ok = bool(run_reloaded(pw)) and ok
    print("\n" + ("every state reported the outage and recovered" if ok
                  else "SOMETHING DID NOT REPORT — read above"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
