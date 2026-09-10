"""Walk the Advanced tutorial's four days and check the lesson is payable.

The Advanced tutorial asks the player to buy one weapon per orbit — an
EMP on day 2, a SNAP on day 3, a chaff on day 4 — against a 250 opening
bank and a teaching board that will not reliably mine the difference. So
two of the three purchases are subsidised, and the subsidy is the part
that can silently rot: it is priced off ``BLUE_COST_BY_KIND`` at one end
and spent at a buy button at the other, with a day number in between.

This walks it as the player does. For each orbit it prints the bank, the
grant, what the lesson costs and whether the button is live, then buys
and moves on. Anything that reads ``short Nb`` on the turn its own reel
tells you to buy is the failure this exists to catch, and it is a
failure no unit test sees, because each half is individually correct.

Run against a server you started yourself::

    SOC_BACKEND=memory python run_web.py --no-reload --port 8022 --replace
    python backstage/films/_probe_adv_arc.py
"""
from __future__ import annotations

import json
import time
import urllib.request

BASE = "http://127.0.0.1:8022"

#: What each orbit's reel tells the player to buy. Read off
#: ``tutorial.py``'s grant table rather than restated, so a retune moves
#: both together.
from sea_of_colours.game import tutorial as soc_tutorial  # noqa: E402
from sea_of_colours.game.weapons import BLUE_COST_BY_KIND  # noqa: E402


def req(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.loads(resp.read())


def wait_phase(sid, want, limit=160):
    for _ in range(limit):
        st = req(f"/api/game/{sid}/status")
        if st.get("phase") == want:
            return st
        time.sleep(0.4)
    return req(f"/api/game/{sid}/status")


def orbit_block(sid, seat="p1"):
    """The seat's own wallet and rack, as the buy panel reads them."""
    return (req(f"/api/game/{sid}/view?player={seat}")
            .get("agent_view", {}).get("orbit", {}) or {})


def log_lines(sid, seat="p1"):
    """What the seat can see in its log right now.

    ``agent_view.recent_log``, not ``status.log``: the latter is a
    different and much shorter feed that reports none of this, which
    cost a while spent believing the subsidy was never announced. It is
    a rolling window either way, so the caller has to sample it each
    day rather than read it once at the end.
    """
    av = req(f"/api/game/{sid}/view?player={seat}").get("agent_view", {})
    return [
        str(e.get("text", "")) if isinstance(e, dict) else str(e)
        for e in (av.get("recent_log") or [])
    ]


def arsenal(stock, prices):
    return sum(int(stock.get(k, 0)) * int(v.get("blue", 0))
               for k, v in prices.items())


def main():
    cap = soc_tutorial.ADVANCED_TUTORIAL_DAY_CAP
    print(f"advanced day cap: {cap}")
    print("grant table     :", soc_tutorial.TUTORIAL_BLUE_GRANT_WEAPON["advanced"])

    sid = req("/api/game/new", {
        "players": ["p1", "p2"], "tutorial": "advanced", "backend": "memory",
    })["session_id"]
    print("session         :", sid)

    st = req(f"/api/game/{sid}/status")
    print("season_day_cap  :", st.get("season_day_cap"), "phase", st.get("phase"))

    ok = True
    announced: list[str] = []
    for day in range(1, cap + 1):
        st = wait_phase(sid, "orbit") if day > 1 else req(f"/api/game/{sid}/status")
        if st.get("phase") != "orbit":
            # Day 1 opens in planning; there is no orbit desk on night one.
            print(f"\nday {day}: opens in {st.get('phase')} (no orbit desk)")
        else:
            announced += [t for t in log_lines(sid)
                          if "training subsidy" in t.lower()
                          and t not in announced]
            ob = orbit_block(sid)
            bank = int(ob.get("blue_purity_total", 0))
            prices = ob.get("weapon_prices", {}) or {}
            want = soc_tutorial.blue_grant_weapon_for("advanced", day)
            grant = soc_tutorial.blue_grant_for("advanced", day)
            price = int(BLUE_COST_BY_KIND.get(want, 0)) if want else 0
            held = arsenal(ob.get("weapon_stock", {}) or {}, prices)
            print(f"\nday {day} ORBIT  bank {bank:4d}b   "
                  f"lesson {want or '-':6s} costs {price:4d}b   "
                  f"grant {grant:4d}b   arsenal {held}b   "
                  f"prices {json.dumps({k: v.get('blue') for k, v in prices.items()})}")
            if want:
                if bank < price:
                    print(f"   ✗ CANNOT AFFORD the {want} its own reel asks for "
                          f"({bank}b in the bank, {price}b needed)")
                    ok = False
                else:
                    print(f"   ✓ affordable ({bank - price}b left after)")
                req(f"/api/game/{sid}/orbit", {
                    "player": "p1",
                    "actions": [{"a": f"build_{want}", "count": 1}],
                    "commit": True,
                })
            else:
                # Day 2's EMP is unsubsidised on purpose — the opening
                # bank covers it, and that is the lesson.
                if day == 2:
                    emp = int(BLUE_COST_BY_KIND["emp"])
                    print(f"   emp costs {emp}b, unsubsidised — "
                          f"{'✓ affordable' if bank >= emp else '✗ SHORT'}")
                    if bank < emp:
                        ok = False
                    req(f"/api/game/{sid}/orbit", {
                        "player": "p1",
                        "actions": [{"a": "build_emp", "count": 1}],
                        "commit": True,
                    })
                else:
                    req(f"/api/game/{sid}/orbit",
                        {"player": "p1", "actions": [], "commit": True})

        wait_phase(sid, "planning")
        ob = orbit_block(sid)
        stock = ob.get("weapon_stock", {}) or {}
        prices = ob.get("weapon_prices", {}) or {}
        print(f"day {day} NIGHT  stock {json.dumps(stock, sort_keys=True)}   "
              f"arsenal {arsenal(stock, prices)}b   "
              f"bank {ob.get('blue_purity_total', '?')}b")

        moves = []
        # Fire the SNAP on the night its reel teaches it, so the arc is
        # walked the way the tutorial narrates it rather than hoarded.
        if day == 3 and int(stock.get("snap", 0)) > 0:
            moves = [{"a": "snap", "at": [10, 7]}]
            print("        firing the SNAP (night three's lesson)")
        req(f"/api/game/{sid}/policy", {"player": "p1", "moves": moves})
        end = wait_phase(sid, "orbit", limit=40)
        if end.get("phase") not in ("orbit", "planning", "ended", "finished"):
            print("        phase after night:", end.get("phase"))

    final = req(f"/api/game/{sid}/status")
    print(f"\nfinal: day {final.get('day')} phase {final.get('phase')} "
          f"(cap {final.get('season_day_cap')})")

    # The subsidy has to be VISIBLE, not just applied — a tutorial that
    # silently tops up your balance is teaching an economy that does not
    # exist. So check it was announced, and check it named the weapon.
    print("\nsubsidy announcements:")
    for t in announced:
        print("  ", t[:130])
    want_n = len(soc_tutorial.TUTORIAL_BLUE_GRANT_WEAPON["advanced"])
    if len(announced) != want_n:
        print(f"   ✗ expected {want_n} announcements, saw {len(announced)}")
        ok = False

    print("\n" + ("PASS" if ok else "FAIL — a lesson was unaffordable"))


if __name__ == "__main__":
    main()
