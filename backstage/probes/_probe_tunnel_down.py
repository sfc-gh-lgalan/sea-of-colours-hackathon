"""Can a game survive its tunnel being replaced underneath it? (v1.46)

A tunnel that dies is now restarted, but it comes back on a *different*
hostname. That single fact is the whole problem this checks, because it
splits the room in two and the halves cannot talk to each other:

* whoever is still connected — in practice the host, on localhost — is
  holding the only working copy of the new address;
* everyone who was playing through the old one is on a hostname that no
  longer resolves, and **cannot be told anything by the server**, because
  reaching the server is precisely what they have lost.

There is no push that fixes that, so the recovery is a human passing one
URL along, and the UI's job is to make that the only step. The host gets
links ready to send; the stranded player gets a box to paste one into and
lands back in their own seat. This drives both halves in a real browser,
because both are DOM that no unit test can see.

The stranded half needs the page to *be* on a tunnel-looking origin —
127.0.0.1 is correctly classified as "you can wait this out". Chromium's
``--host-resolver-rules`` gives us a real hostname pointed at the local
server, so ``window.location.hostname`` is genuinely a tunnel name
without a tunnel existing.

Run it against a server you started yourself (see AGENTS.md)::

    SOC_BACKEND=memory python run_web.py --no-reload --port 8023 --replace
    python backstage/probes/_probe_tunnel_down.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

PORT = 8023
BASE = f"http://127.0.0.1:{PORT}"
# Resolves to the local server via Chromium's resolver rules. Has to look
# like a tunnel to the classifier, i.e. be neither loopback nor a private
# range — the point of the scene is that the page cannot tell itself to
# just hold on.
FAKE_HOST = "probe-old.trycloudflare.com"
NEW_HOST = "probe-new.trycloudflare.com"

# The watcher polls every 15s and deliberately treats its first reading as
# a baseline, so a page opened after a restart isn't told about one it
# never experienced. Two polls plus slack.
WATCH_S = 38.0
# Offline is declared at ~7s; the escalation to "stranded" is at 20s.
STRAND_S = 30.0


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
    return req("/api/game/new", {
        "players": ["p1", "p2", "p3"],
        "agents": {"p1": "human", "p2": "human", "p3": "human"},
        "width": 24, "height": 16, "season_day_cap": 6,
        "weapons_enabled": True, "signs_enabled": True,
        "backend": "memory", "seed": 4461,
    })["session_id"]


def hang_status(pg):
    """Make ``/status`` accept and never answer, and return the release.

    That shape matters: a restarted tunnel does not refuse the old name so
    much as stop answering on it, and a request that never settles is the
    only thing that can wedge a poller with no deadline.

    The release exists because a route handler that never resolves is
    *cancelled* when the browser closes, and Playwright prints a full
    traceback per pending route — two hundred lines of noise with any
    real failure buried inside, which defeats the point of a probe whose
    output you are meant to read. ``unroute`` alone does not settle the
    ones already in flight; they have to be answered.
    """
    pending = []
    pg.route("**/status*", lambda route: pending.append(route))

    def release():
        for r in pending:
            try:
                r.abort()
            except Exception:
                pass
        pending.clear()
        try:
            pg.unroute("**/status*")
        except Exception:
            pass

    return release


def modal(pg):
    """The tunnel modal's mode, title and the seat links it offers."""
    return pg.evaluate(
        "() => { const el = document.getElementById('cc-tunnel-down');"
        " if (!el) return null;"
        " return { mode: el.dataset.mode,"
        "   title: (el.querySelector('.cc-tunnel-down-title')||{}).textContent||'',"
        "   body: el.textContent || '',"
        "   links: Array.from(el.querySelectorAll('.cc-share-input'))"
        "     .map(i => i.value).filter(Boolean),"
        "   qrs: el.querySelectorAll('.cc-share-qr img').length,"
        "   hasInput: !!el.querySelector('input:not([readonly])') }; }"
    )


def scene_host_restarted(pw):
    """The connected half: told the links changed, handed new ones.

    Faked at the browser rather than by killing a real tunnel, so the
    scene is about the UI reacting to ``revivals`` going up — which is the
    only thing the page can actually observe.
    """
    print("\n=== host side: the tunnel restarted under a live game ===")
    sid = new_game()
    br = pw.chromium.launch()
    pg = br.new_page(viewport={"width": 1440, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)))

    revivals = {"n": 0}

    def _tunnel(route):
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "installed": True, "running": True, "provider": "cloudflare",
                "url": f"https://{NEW_HOST}", "providers": ["cloudflare"],
                "rotates": False, "rotations": 0, "skipped": [],
                "revivals": revivals["n"], "reviving": False, "expected": True,
            }),
        )

    pg.route("**/api/tunnel/status*", _tunnel)
    pg.goto(f"{BASE}/play?session={sid}&player=p1", wait_until="networkidle")
    time.sleep(6)
    print(f"  modal before the restart : {modal(pg)}")

    revivals["n"] = 1
    print(f"  tunnel now reports revivals=1, waiting up to {WATCH_S:.0f}s …")
    # Poll for it rather than sleeping the whole budget in one go: a fixed
    # sleep either races the watcher or hides how long it really took, and
    # "how long until a host finds out" is half the point of this scene.
    m = None
    waited = 0.0
    while waited < WATCH_S:
        time.sleep(2.0)
        waited += 2.0
        m = modal(pg)
        if m:
            print(f"  noticed after            : {waited:.0f}s")
            break
    ok = bool(m) and m["mode"] == "restarted"
    print(f"  modal appeared           : {'YES' if ok else 'NO'}")
    if m:
        print(f"  title                    : {m['title']!r}")
        print(f"  seat links offered       : {len(m['links'])} (QRs: {m['qrs']})")
        for ln in m["links"]:
            print(f"      {ln}")
        ok = ok and len(m["links"]) == 3 and m["qrs"] == 3
        # Each link has to name a *different* seat, or the host sends
        # three copies of one seat's link and two players can't get in.
        seats = sorted(ln.split("player=")[-1] for ln in m["links"])
        print(f"  seats covered            : {seats}")
        ok = ok and seats == ["p1", "p2", "p3"]
    if errs:
        print(f"  page errors: {errs[:3]}")
        ok = False
    br.close()
    return ok


def scene_stranded_guest(pw):
    """The disconnected half: told the address is gone, offered a way in.

    The assertion that matters is the last one — that pasting the new
    address lands them back in *their own seat*, not on the title screen
    or in someone else's. Session and seat live in this page's own URL,
    which is the half of the link that survived, so the host only ever has
    to pass on a bare address.
    """
    print("\n=== stranded player: the address they hold no longer exists ===")
    sid = new_game()
    br = pw.chromium.launch(args=[
        f"--host-resolver-rules=MAP {FAKE_HOST} 127.0.0.1,"
        f"MAP {NEW_HOST} 127.0.0.1",
    ])
    pg = br.new_page(viewport={"width": 1440, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)))

    # p2, deliberately: a bug that rebuilds the link from a default seat
    # rather than this page's own would pass as p1 and be invisible.
    pg.goto(f"http://{FAKE_HOST}:{PORT}/play?session={sid}&player=p2",
            wait_until="networkidle")
    time.sleep(5)
    print(f"  origin               : {pg.evaluate('() => location.origin')}")

    release = hang_status(pg)
    print(f"  cut the poller, waiting {STRAND_S:.0f}s for the escalation …")
    time.sleep(STRAND_S)

    m = modal(pg)
    ok = bool(m) and m["mode"] == "stranded"
    print(f"  modal appeared       : {'YES' if ok else 'NO'}")
    if m:
        print(f"  title                : {m['title']!r}")
        print(f"  offers a paste box   : {'YES' if m['hasInput'] else 'NO'}")
        ok = ok and m["hasInput"]
        # It must not repeat the banner's promise that this page will
        # recover on its own; for this origin that is precisely false.
        promised = "catches up on its own" in m["body"]
        print(f"  avoids the false 'it will come back' promise : "
              f"{'YES' if not promised else 'NO'}")
        ok = ok and not promised

    # The new address answers — that is the whole point of it — so lift
    # the outage before rejoining. Left in place, the hang route follows
    # the page across the navigation and the "recovered" game would be
    # just as dead as the one we left.
    release()

    # Paste the new address, exactly as a host would have sent it. http
    # rather than https only because this local server has no TLS; a real
    # tunnel address is https and takes the same path.
    pg.fill("#cc-tunnel-down input:not([readonly])", f"http://{NEW_HOST}:{PORT}")
    pg.click("#cc-tunnel-down .cc-share-copy")
    time.sleep(8)
    url = pg.url
    print(f"  after REJOIN         : {url}")
    back = (NEW_HOST in url and f"session={sid}" in url and "player=p2" in url)
    # Landing on the right URL is not the same as being *in* the game —
    # the seat badge is the page's own account of who it thinks you are,
    # so it catches a link that parses but seats you wrongly.
    seated = pg.evaluate(
        "() => { const el = document.getElementById('cc-seat-identity');"
        " return (el && !el.hidden) ? el.textContent.trim() : ''; }"
    )
    print(f"  seat badge on arrival: {seated!r}")
    back = back and "P2" in seated.upper()
    print(f"  -> back in their own seat on the new address : "
          f"{'YES' if back else 'NO'}")
    ok = ok and back

    if errs:
        print(f"  page errors: {errs[:3]}")
        ok = False
    br.close()
    return ok


def scene_bare_hostname(pw):
    """People paste what they see. Refusing a bare hostname on a
    technicality would be a poor way to end someone's game.

    Asserted on the URL the browser *attempts* rather than on arriving,
    because a bare hostname defaults to https — correct for a real tunnel
    and impossible against this plain-http local server. What is being
    checked is the link we build, which is the part we wrote.
    """
    print("\n=== stranded player pastes a bare hostname, no scheme ===")
    sid = new_game()
    br = pw.chromium.launch(args=[
        f"--host-resolver-rules=MAP {FAKE_HOST} 127.0.0.1,"
        f"MAP {NEW_HOST} 127.0.0.1",
    ])
    pg = br.new_page(viewport={"width": 1440, "height": 900})
    tried = []
    pg.on("request", lambda r: tried.append(r.url)
          if r.is_navigation_request() else None)
    pg.goto(f"http://{FAKE_HOST}:{PORT}/play?session={sid}&player=p3",
            wait_until="networkidle")
    time.sleep(5)
    release = hang_status(pg)
    time.sleep(STRAND_S)
    if not modal(pg):
        print("  ! no modal; scene cannot run")
        release()
        br.close()
        return False
    pg.fill("#cc-tunnel-down input:not([readonly])", f"{NEW_HOST}:{PORT}")
    pg.click("#cc-tunnel-down .cc-share-copy")
    time.sleep(5)
    went = [u for u in tried if NEW_HOST in u]
    print(f"  navigation attempted : {went[-1] if went else '(none)'}")
    ok = bool(went) and went[-1].startswith("https://") \
        and f"session={sid}" in went[-1] and "player=p3" in went[-1]
    print(f"  -> bare name accepted, https assumed, seat kept : "
          f"{'YES' if ok else 'NO'}")
    release()
    br.close()
    return ok


def main():
    from playwright.sync_api import sync_playwright
    ok = True
    with sync_playwright() as pw:
        ok = bool(scene_host_restarted(pw)) and ok
        ok = bool(scene_stranded_guest(pw)) and ok
        ok = bool(scene_bare_hostname(pw)) and ok
    print("\n" + ("both halves of a tunnel change are handled" if ok
                  else "SOMETHING DID NOT WORK — read above"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
