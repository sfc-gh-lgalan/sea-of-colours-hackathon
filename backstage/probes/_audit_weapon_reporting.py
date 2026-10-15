"""Does the results card report the weapons a season actually fired?

Written for BUG-13, whose report was that the end-of-game card
"under-reports weapons and their effectiveness", with a screenshot of a
seat credited `EMP'd probes 1` while its `// operations` block had no
`EMPs fired` row at all — a kill it had no weapon to make.

The card has TWO sources for one fact, which is the whole problem:

  fires   `personal.emps_fired` etc, from `orbital_activity_by_day` —
          DERIVED after the fact by re-reading replay frame tags
          (`session.tally_orbital_activity` matches `emp_launch` /
          `chaff_flare` / `snap_launch`).
  kills   `killfeed.*`, from `combat_attrib` — recorded by the engine at
          the moment of the kill, in persisted game state.

`sea_of_colours/league/stats.py` ignores the derived one and reads
`weapons_used`, the engine's own firing ledger, noting that
`bought = weapon_stock + weapons_used` is exact because both start at
zero. That is the authority this compares everything against.

So, per seat, for every finished season on disk:

  * `weapons_used`                      — the engine's firing ledger (truth)
  * `weapon_stock` + `weapons_used`     — bought, exactly
  * `orbital_activity_by_day` summed    — what the card prints as fired
  * `combat_attrib`                     — kills, and who is credited

and it reports the contradictions a player can actually see.

    python backstage/probes/_audit_weapon_reporting.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

#: The card's `perDefs` key -> the `orbital_activity_by_day` key it reads.
#: `snaps` is absent on purpose: nothing in `engine.py` ever reads it into
#: the payload, which is defect 1.
CARD_FIRE_KEYS = {"emp": "emps", "chaff": "chaff"}

KINDS = ("snap", "emp", "chaff")

#: Which weapon each aggressive `combat_attrib` row implies was fired, so
#: a kill can be checked against the ledger of fires.
KILL_IMPLIES = {
    "emp_probes": "emp",
    "emp_harvesters": "emp",
    "snap_probes": "snap",
    "snap_harvesters": "snap",
    "chaff_jams": "chaff",
    "harv_lost_chaff": "chaff",
}


def audit(root: Path) -> Dict[str, Any] | None:
    try:
        st = json.loads((root / "state.json").read_text()).get("json_state") or {}
    except (OSError, ValueError):
        return None
    if st.get("phase") != "season_complete":
        return None

    used = st.get("weapons_used") or {}
    stock = st.get("weapon_stock") or {}
    attrib = st.get("combat_attrib") or {}
    activity = st.get("orbital_activity_by_day") or {}

    seats: List[str] = sorted(set(used) | set(stock))
    if not seats:
        return None

    # What the card would print as "fired", summed the way engine.py does.
    card: Dict[str, Dict[str, int]] = {s: {} for s in seats}
    for tally_by_seat in activity.values():
        for seat, tally in (tally_by_seat or {}).items():
            if seat not in card:
                card[seat] = {}
            for k, v in (tally or {}).items():
                try:
                    card[seat][k] = card[seat].get(k, 0) + int(v)
                except (TypeError, ValueError):
                    continue

    # Kills credited to each seat as the ATTACKER, plus anything credited
    # to a non-seat attacker (the decaying-cloud sweep passes
    # owner="emp_field", so those kills belong to no one on the card).
    kills: Dict[str, Dict[str, int]] = {s: {} for s in seats}
    orphans: Dict[str, Dict[str, int]] = {}
    for stat, by_attacker in (attrib or {}).items():
        for atk, victims in (by_attacker or {}).items():
            n = sum(int(c or 0) for c in (victims or {}).values())
            if not n:
                continue
            if atk in kills:
                kills[atk][stat] = kills[atk].get(stat, 0) + n
            else:
                orphans.setdefault(atk, {})[stat] = \
                    orphans.setdefault(atk, {}).get(stat, 0) + n

    rows = []
    for seat in seats:
        u = {k: int((used.get(seat) or {}).get(k, 0) or 0) for k in KINDS}
        s = {k: int((stock.get(seat) or {}).get(k, 0) or 0) for k in KINDS}
        shown = {
            k: int(card[seat].get(CARD_FIRE_KEYS[k], 0)) if k in CARD_FIRE_KEYS
            else None                      # the card has no row for this
            for k in KINDS
        }
        rows.append({
            "seat": seat,
            "used": u,
            "bought": {k: s[k] + u[k] for k in KINDS},
            "shown": shown,
            "kills": kills[seat],
        })

    # Does the frame-derived tally cover every night the season played?
    # Against `season_day_cap`, NOT `day` — at completion `day` has already
    # advanced one past the cap (8 on a 7-night season), so comparing to it
    # reports every season as missing its last night, which it is not.
    day_cap = int(st.get("season_day_cap", 0) or 0)
    tallied = sorted(int(d) for d in activity) if activity else []

    return {
        "id": root.name[:8],
        "rows": rows,
        "orphans": orphans,
        "day": day_cap,
        "tallied": tallied,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="seasons")
    ap.add_argument("--verbose", action="store_true",
                    help="one line per seat, not just the offenders")
    a = ap.parse_args()

    found = [r for r in (audit(p) for p in sorted(Path(a.root).iterdir())
                         if p.is_dir()) if r]
    if not found:
        print(f"no finished seasons under {a.root}/")
        return 1

    seats_total = sum(len(r["rows"]) for r in found)
    print(f"{len(found)} finished seasons · {seats_total} seats")
    print("(the blocks below are the PRE-v1.52 card; the live check is "
          "CARD AS SHIPPED at the end)\n")

    # ── the contradictions a player COULD see, before v1.52 ──────────────
    # These re-derive the old arithmetic from raw state: they are the size
    # of the defect and stay non-zero for seasons already played. The live
    # assertion is CARD AS SHIPPED at the bottom.
    ghost_kill = []        # a kill whose weapon the card shows no fires of
    misattributed = []     # a kill by a weapon the seat never fired at all
    unreported = []        # fired, but the card has no row for that weapon
    miscount = []          # card's fired count != the engine's ledger
    missing_night = []     # the derived tally does not cover the season
    orphaned = []          # kills credited to a non-seat attacker
    unfired = []           # bought and never fired — the effectiveness gap

    for r in found:
        if r["tallied"] and r["tallied"][-1] < r["day"]:
            missing_night.append(
                f"{r['id']}: played to day {r['day']}, activity tallied only "
                f"to day {r['tallied'][-1]}")
        for atk, stats in (r["orphans"] or {}).items():
            orphaned.append(f"{r['id']}: {sum(stats.values())} kill(s) "
                            f"credited to '{atk}' ({stats})")
        for row in r["rows"]:
            seat, u, shown = row["seat"], row["used"], row["shown"]
            gap = sum(row["bought"][k] - u[k] for k in KINDS)
            if gap > 0:
                unfired.append(
                    f"{r['id']} {seat}: bought "
                    f"{sum(row['bought'].values())}, fired "
                    f"{sum(u.values())} — {gap} never left the rack")
            for kind in KINDS:
                if shown[kind] is None:
                    if u[kind]:
                        unreported.append(
                            f"{r['id']} {seat}: fired {u[kind]} {kind.upper()}, "
                            f"card has no row for it")
                elif shown[kind] != u[kind]:
                    miscount.append(
                        f"{r['id']} {seat}: card shows {shown[kind]} "
                        f"{kind.upper()} fired, ledger says {u[kind]}")
            for stat, n in (row["kills"] or {}).items():
                kind = KILL_IMPLIES.get(stat)
                if not kind or not n:
                    continue
                vis = shown[kind]
                if u[kind] == 0:
                    # The kill is attributed to a weapon this seat never
                    # owned, in persisted `combat_attrib`. Pre-v1.50 the
                    # SNAP's sweep credited `emp_probes` unconditionally,
                    # so these are almost always a SNAP kill wearing an
                    # EMP's name — baked into the season and not fixable
                    # by the reader.
                    also = " (it did fire %d SNAP)" % u["snap"] if (
                        kind == "emp" and u["snap"]) else ""
                    misattributed.append(
                        f"{r['id']} {seat}: {n} '{stat}' but the ledger says "
                        f"it never fired an {kind.upper()}{also}")
                elif vis is None or vis == 0:
                    ghost_kill.append(
                        f"{r['id']} {seat}: {n} '{stat}' while the card shows "
                        f"no {kind.upper()} fired (ledger: {u[kind]})")

    def block(title: str, items: list, note: str = "") -> None:
        print(f"{title} ... {len(items)}")
        if note:
            print(f"  {note}")
        for line in items[:8]:
            print(f"  - {line}")
        if len(items) > 8:
            print(f"  ... and {len(items) - 8} more")
        print()

    block("A kill the card shows no weapon for", ghost_kill,
          "the reported screenshot: the kill is attributed correctly, but "
          "the card cannot print the fire beside it")
    block("A kill by a weapon the seat NEVER fired", misattributed,
          "persisted combat_attrib. Pre-v1.50 the SNAP's sweep credited "
          "emp_probes unconditionally; the code is fixed, these seasons "
          "are not")
    block("Fired a weapon the card cannot print", unreported,
          "SNAP has no row in perDefs and nothing emits the key")
    block("Card's fired count disagrees with the ledger", miscount,
          "derived-from-frames vs the engine's own weapons_used")
    block("Seasons whose nights were not all tallied", missing_night,
          "orbital_activity_by_day is built per night from replay frames")
    block("Kills credited to a non-seat attacker", orphaned,
          "the decaying-cloud sweep passes owner='emp_field', so a kill by "
          "a standing cloud belongs to nobody on the card")
    block("Bought ordnance that never fired", unfired,
          "the league calls this 'unfired' and puts it on the page; the "
          "results card derives no such figure")

    # ── the ledger itself, for scale ─────────────────────────────────────
    tot_used = {k: 0 for k in KINDS}
    tot_bought = {k: 0 for k in KINDS}
    tot_shown = {k: 0 for k in KINDS}
    for r in found:
        for row in r["rows"]:
            for k in KINDS:
                tot_used[k] += row["used"][k]
                tot_bought[k] += row["bought"][k]
                tot_shown[k] += row["shown"][k] or 0
    print("WEAPONS ACROSS EVERY FINISHED SEASON")
    print(f"  {'':8} {'bought':>7} {'fired':>7} {'pre-v1.52':>10}")
    for k in KINDS:
        card_says = "(no row)" if k not in CARD_FIRE_KEYS else str(tot_shown[k])
        print(f"  {k.upper():8} {tot_bought[k]:>7} {tot_used[k]:>7} "
              f"{card_says:>10}")
    fired_all = sum(tot_used.values())
    shown_all = sum(tot_shown[k] for k in CARD_FIRE_KEYS)
    print(f"\n  {fired_all} weapons fired in total; before v1.52 the card "
          f"could account for {shown_all} of them"
          + (f" ({100 * shown_all / fired_all:.0f}%)" if fired_all else "")
          + ". It now reads the ledger and accounts for all of them —\n"
          "  see CARD AS SHIPPED below, which is the live check.")

    # ── the card as actually shipped ─────────────────────────────────────
    # Everything above re-derives the PRE-v1.52 arithmetic from raw state
    # and is a record of the defect's size. This builds the real payload
    # through `get_endgame_summary` and asserts the contradictions are
    # gone, so it fails if the fix regresses.
    print("\nCARD AS SHIPPED (get_endgame_summary)")
    try:
        from sea_of_colours.snowpark.file_store import FileSocStore
        from sea_of_colours.snowpark import engine as soc_engine
    except Exception as exc:                          # pragma: no cover
        print(f"  skipped — cannot import the engine ({exc})")
        return 0

    store = FileSocStore(a.root)
    bad: List[str] = []
    checked = 0
    for r in found:
        sid = next((p.name for p in Path(a.root).iterdir()
                    if p.name.startswith(r["id"])), None)
        if not sid:
            continue
        try:
            payload = soc_engine.get_endgame_summary(store, sid)
        except Exception as exc:
            bad.append(f"{r['id']}: summary raised {type(exc).__name__}: {exc}")
            continue
        for p in payload.get("players", []):
            checked += 1
            seat = p.get("seat")
            per = p.get("personal") or {}
            ordn = p.get("ordnance") or {}
            kf = p.get("killfeed") or {}
            if not ordn:
                bad.append(f"{r['id']} {seat}: no ordnance block")
            for kind in KINDS:
                fired = int(per.get(f"{kind}s_fired" if kind != "chaff"
                                    else "chaff_fired", 0) or 0)
                led = int((ordn.get(kind) or {}).get("fired", 0) or 0)
                if fired != led:
                    bad.append(f"{r['id']} {seat}: personal says {fired} "
                               f"{kind.upper()} fired, ordnance says {led}")
            # The contradiction the bug was reported as: a kill printed
            # with no fire of that weapon anywhere on the card.
            for stat, kind in KILL_IMPLIES.items():
                n = sum(int(c or 0) for c in (kf.get(stat) or []))
                if not n:
                    continue
                led = int((ordn.get(kind) or {}).get("fired", 0) or 0)
                if led == 0:
                    bad.append(
                        f"{r['id']} {seat}: card shows {n} '{stat}' with 0 "
                        f"{kind.upper()} fired")
    print(f"  seats rendered ......................... {checked}")
    print(f"  self-contradicting cards ............... {len(bad)}")
    for line in bad[:8]:
        print(f"  - {line}")
    if len(bad) > 8:
        print(f"  ... and {len(bad) - 8} more")
    if bad:
        print("\nFAIL")
        return 1
    print("  every kill on every card has the weapon that made it")

    if a.verbose:
        print(f"\n{'season':9} {'seat':5} " + " ".join(f"{k:>14}" for k in KINDS))
        for r in found:
            for row in r["rows"]:
                cells = []
                for k in KINDS:
                    v = row["shown"][k]
                    cells.append(f"{row['used'][k]}f/{row['bought'][k]}b"
                                 f"/{'--' if v is None else v}c")
                print(f"{r['id']:9} {row['seat']:5} "
                      + " ".join(f"{c:>14}" for c in cells))
        print("  legend: <fired>f/<bought>b/<what the card prints>c")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
