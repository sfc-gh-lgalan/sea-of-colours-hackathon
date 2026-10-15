"""Is GREEN accounted for correctly, and does the platform say so?

Written for BUG-12 and kept as the standing engine check. The report was
that the `-100` GREEN line never appears under the station scores and the
discount looks like it waits for the final night; the open question was
which half the player saw — a wrong number, or a right number with no
explanation — since the two photograph identically.

The answer (OUTSTANDING_ISSUES #57): the engine was right on all 88
seats and the platform was wrong on 75. Fixed in v1.51. The ENGINE
section below is still live and should stay 88/88; the PLATFORM section
re-derives the pre-fix arithmetic and is a record of the defect's size.

So this reads every finished season on disk and, per seat, compares:

  * `compute_player_score`              — the canonical score
  * `compute_score_breakdown`           — the results card's three lines
  * `cumulative_shipped_score`          — the cached counter the HUD seeds from
  * the `settlement` payload            — what `station.js` animates

against the parcels themselves. It reads the persisted SHIPPED / HOARD
tables directly rather than hydrating a session, which is the same path
`SocStore.bulk_session_scores` takes.

    python backstage/probes/_audit_green_accounting.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping

from sea_of_colours.game.session import (
    GREEN_ENDGAME_PENALTY,
    compute_player_score,
    compute_score_breakdown,
)
from sea_of_colours.generator import Tile


def _is_green(parcel: Mapping[str, Any]) -> bool:
    origin = parcel.get("tile_at_harvest")
    if origin is None:
        origin = parcel.get("origin_tile")
    try:
        return int(origin) == int(Tile.GREEN)
    except (TypeError, ValueError):
        return parcel.get("score_tier") == "green"


def audit(root: Path) -> Dict[str, Any] | None:
    try:
        st = json.loads((root / "state.json").read_text()).get("json_state") or {}
    except (OSError, ValueError):
        return None
    if st.get("phase") != "season_complete":
        return None

    shipped_all = st.get("shipped_squares") or {}
    hoard_all = st.get("hoard_squares") or {}
    cum = st.get("cumulative_shipped_score") or {}
    seats: List[str] = sorted(set(shipped_all) | set(hoard_all))

    # Replay the two running totals the PLATFORM computes per night, and
    # keep them apart, because they disagree and only one is right:
    #
    #   ui_running   `app.js` `_osGetCumulativeScore` — sums the catapult's
    #                `score_shipped` per day. RED only; its own comment says
    #                "Green (solar jettison) is disposal and adds no score."
    #   jettisoned   `catapult_history[day].jettison.seats[seat].penalty` —
    #                the GREEN charge that same night, already persisted and
    #                already shipped to the replay, and read by nobody.
    ui_running: Dict[str, float] = {}
    jettisoned: Dict[str, int] = {}
    for entry in st.get("catapult_history") or []:
        for seat, sb in ((entry.get("catapult") or {}).get("seats") or {}).items():
            ui_running[seat] = ui_running.get(seat, 0.0) + float(
                sb.get("score_shipped") or 0.0)
        for seat, jb in ((entry.get("jettison") or {}).get("seats") or {}).items():
            jettisoned[seat] = jettisoned.get(seat, 0) + int(jb.get("penalty") or 0)

    rows = []
    for seat in seats:
        shipped = shipped_all.get(seat) or []
        hoard = hoard_all.get(seat) or []
        final = compute_player_score(shipped, hoard, is_complete=True)
        br = compute_score_breakdown(shipped, hoard, is_complete=True)

        disposed = sum(1 for p in shipped if _is_green(p))
        held = sum(1 for p in hoard if _is_green(p))

        # What `engine.py` actually ships to the replay as `settlement`.
        # Note it prices GREEN off the VAULT only.
        payload = {
            "shipped": int(round(float(cum.get(seat) or 0.0))),
            "green_penalty": GREEN_ENDGAME_PENALTY * held,
            "vault_red_loss": br["vault_red_loss"],
            "final": final,
        }
        rows.append({
            "seat": seat, "final": final, "breakdown": br,
            "disposed_green": disposed, "held_green": held,
            "cum": float(cum.get(seat) or 0.0), "payload": payload,
            "ui_running": ui_running.get(seat, 0.0),
            "jettisoned": jettisoned.get(seat, 0),
        })
    return {"id": root.name, "seats": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="seasons")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    done = []
    for d in sorted(Path(a.seasons).iterdir()):
        if not d.is_dir():
            continue
        got = audit(d)
        if got:
            done.append(got)
        if a.limit and len(done) >= a.limit:
            break

    print(f"{len(done)} finished seasons on disk\n")

    bad_identity = bad_cum = silent = late = 0
    overstated = 0
    worst = (0.0, "", "")
    total_seats = 0
    print(f"{'season':10} {'seat':4} {'final':>7} {'shippedRED':>11} "
          f"{'green':>7} {'station shows':>14} {'overstated by':>14}")
    for s in done:
        for r in s["seats"]:
            total_seats += 1
            br = r["breakdown"]
            # The station readout vs the truth it must land on.
            drift = r["ui_running"] - r["cum"]
            if abs(drift) > 1.5:
                overstated += 1
                if drift > worst[0]:
                    worst = (drift, s["id"][:8], r["seat"])
            # 1. Do the card's three lines reconstruct the total?
            if br["shipped"] - br["green_penalty"] + br["vault_red_loss"] \
                    != br["final"]:
                bad_identity += 1
            # 2. Is the cached counter consistent with the parcels?
            #    It is RED score minus DISPOSED green only.
            want_cum = br["shipped"] - GREEN_ENDGAME_PENALTY * r["disposed_green"]
            if abs(want_cum - r["cum"]) > 1.5:
                bad_cum += 1
            # 3. Would the player ever see a green line?
            shown = r["payload"]["green_penalty"]
            owed = br["green_penalty"]
            if owed and not shown:
                silent += 1
            if owed:
                late += 1
            print(f"{s['id'][:8]:10} {r['seat']:4} {r['final']:>7} "
                  f"{br['shipped']:>11} {br['green_penalty']:>7} "
                  f"{int(r['ui_running']):>14} "
                  f"{('+' + str(int(drift))) if drift > 1.5 else '-':>14}")

    print(f"\n{total_seats} seats audited across {len(done)} finished seasons\n")
    print("ENGINE")
    print(f"  results-card lines reconstruct the total ....... "
          f"{total_seats - bad_identity}/{total_seats}")
    print(f"  cumulative_shipped_score matches the parcels ... "
          f"{total_seats - bad_cum}/{total_seats}")
    # These re-derive the PRE-v1.51 station arithmetic in Python — they are
    # the size of the defect, not a live reading. `_osGetCumulativeScore`
    # now subtracts the jettison penalty; `_probe_green_lines.py` is what
    # checks the shipped code, in a browser, and would catch a regression.
    print("\nPLATFORM, as it behaved before v1.51 (see OUTSTANDING_ISSUES #57)")
    print(f"  seats actually charged for green .............. {late}")
    print(f"  ...that saw no green line at all .............. {silent}")
    print(f"  ...whose station running score read too high .. {overstated}")
    if worst[0]:
        print(f"  worst overstatement ........................... "
              f"+{int(worst[0])} ({worst[1]} {worst[2]})")
    print("\n  (the ENGINE figures above are live — they should stay 88/88)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
