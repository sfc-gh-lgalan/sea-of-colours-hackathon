#!/usr/bin/env python3
"""check_wiring.py — is the weapon actually wired? Asked of the live objects.

The honest counterpart to ``soc weapons``. That command is a useful one-second
smoke test, but every one of its rungs is a substring grep over source: rung 3
is ``rationale=`` and ``detail=`` merely co-occurring, rung 4 is a regex over
prose, and rung 2 is *filename*-based. All four can PASS with no working
weapon, and — as the forge showed — all four can FAIL on a working one.

This imports the fork and interrogates the real objects instead:

  1. the wire verb reaches the chat_schema enum    (else the model cannot emit
                                                    it, and there is no error)
  2. the kind reaches packager._DISPATCH           (else it never compiles)
  3. the frame tag reaches last_night's tag sets   (else it fires and is
                                                    invisible in the log)
  4. the rack is spoken in the prompt              (rung 1)
  5. doctrine is emitted when we hold the weapon   (rung 4)
  6. an option is actually BUILT from a synthetic  (the only check that proves
     board                                          the menu path end to end)

Usage:
    python skills/soc-agent-forge/scripts/check_wiring.py <agent_label>
    python skills/soc-agent-forge/scripts/check_wiring.py <agent_label> --json

Exit 0 when clean, 1 when any check fails. Read-only: it never edits.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HARNESSES = Path("sea_of_colours/orchestrator_2/harnesses")
PKG_ROOT = "sea_of_colours.orchestrator_2.harnesses"

# Run from the repo root, but this script lives three levels down in skills/,
# so the package is not importable without help.
if str(Path.cwd()) not in sys.path:
    sys.path.insert(0, str(Path.cwd()))

# Wire verb vs REPLAY frame tag. These are NOT always the same string, which is
# the trap: `snap_launch` on the wire arrives as `snap` in the replay frame, so
# a tag set keyed on the verb silently drops every frame and the agent journals
# that the move never executed.
WEAPONS: Dict[str, Tuple[str, str]] = {
    "emp": ("emp_launch", "emp_launch"),
    "chaff": ("chaff_flare", "chaff_flare"),
    "snap": ("snap_launch", "snap"),
}


class Result:
    def __init__(self) -> None:
        self.rows: List[Tuple[str, bool, str]] = []
        self.mode = "unknown"

    def add(self, name: str, ok: bool, note: str = "") -> None:
        self.rows.append((name, ok, note))

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.rows)


# ── a synthetic board, so option building can be exercised offline ─────────
@dataclass
class _FakeWave:
    drop_at: Tuple[int, int] = (14, 9)
    comb_path: List[Tuple[int, int]] = field(
        default_factory=lambda: [(15, 9), (15, 10)]
    )
    deny_only: bool = False
    earliest_hour: int = 1
    wave: int = 1


@dataclass
class _FakePattern:
    """Enough of a SeamPattern for `build_options` to borrow geometry from."""
    pattern_id: str = "BLIND_GRAB"
    kind: str = "seam"
    beacon: Tuple[int, int] = (14, 9)
    mine: Optional[bool] = False        # False => a RIVAL's redsign
    title: str = "fake"
    when: str = "fake"
    rationale: str = "fake"
    waves: List[_FakeWave] = field(default_factory=lambda: [_FakeWave()])


def _view(weapon: str, n: int = 2) -> Dict[str, Any]:
    """A synthetic board rich enough for every targeting mode to fire.

    It must carry a redsign with real ``cells``: ``scorch.redsign_targets``
    reads the smear off the view and returns nothing for a region with no
    cells, which made every ``targets="redsign"`` play look broken when it was
    the fixture that was thin.
    """
    smear = [[x, y] for x in range(18, 25) for y in range(18, 24)]
    return {
        "orbit": {"weapon_stock": {weapon: n},
                  "harvesters": [{"id": "harvester_p2"}],
                  "probes": 4},
        "my_assets": [
            {"kind": "harvester", "state": "orbit", "id": "harvester_p2"},
            {"kind": "harvester", "state": "orbit", "id": "harvester_p2_2"},
        ],
        "grid": {"width": 32, "height": 32},
        "probe_stock": 4,
        # a RIVAL smear (mine False) — an own sign is refused by design
        "redsign": [{"cells": smear, "mine": False, "x": 21, "y": 20}],
        "red_tiles": [], "blue_tiles": [], "blue_sign": [],
        # three rival eyes, freshest last, so recency ranking has something
        # to rank and min_targets has enough to clear
        "enemy_probes": [{"at": [10 + 3 * i, 10], "day_seen": i + 1}
                         for i in range(3)],
        "rival_probes": [{"x": 10 + 3 * i, "y": 10, "freshness": "fresh"}
                         for i in range(3)],
        "station_intel": {"self": {"blue": {"grade": "low"}}},
    }


def _imp(label: str, mod: str) -> Any:
    return importlib.import_module(f"{PKG_ROOT}.{label}.{mod}")


def check(label: str) -> Result:
    r = Result()
    root = HARNESSES / label
    if not root.is_dir():
        r.add("agent directory exists", False, f"no such fork: {root}")
        return r

    forged = (root / "weapon_forge.py").is_file()
    r.mode = "forge" if forged else "hand-wired"

    # Which weapons is this fork trying to carry?
    weapons: List[str] = []
    if forged:
        try:
            plays = _imp(label, "weapon_plays")
            weapons = sorted({p.weapon for p in plays.PLAYS})
        except Exception as exc:                       # noqa: BLE001
            r.add("weapon_plays.py imports", False, f"{type(exc).__name__}: {exc}")
            return r
        if not weapons:
            r.add("at least one play declared", False,
                  "weapon_plays.PLAYS is empty — declare a WeaponPlay")
            return r
        forge = _imp(label, "weapon_forge")
        bad = forge.validate_all()
        r.add("declarations are valid", not bad, "; ".join(bad))
    else:
        agency_src = (root / "agency.py").read_text(encoding="utf-8")
        pack_src = (root / "packager.py").read_text(encoding="utf-8")
        for noun, (verb, _t) in WEAPONS.items():
            if verb in agency_src or verb in pack_src or f"_pack_{noun}" in pack_src:
                weapons.append(noun)
        if not weapons:
            r.add("a weapon is wired at all", False,
                  "no weapon verb in agency.py or packager.py — this fork is "
                  "still disarmed, which is the normal state of a fresh mint. "
                  "Run forge_install.py, or wire one by hand.")
            return r

    # ── 1 · the schema wall ───────────────────────────────────────────────
    try:
        schema = _imp(label, "chat_schema")
        enum = list(schema._MOVE_ITEM["properties"]["a"]["enum"])
    except Exception as exc:                           # noqa: BLE001
        enum = []
        r.add("chat_schema imports", False, f"{type(exc).__name__}: {exc}")
    for w in weapons:
        verb = WEAPONS[w][0]
        r.add(f"schema enum carries {verb}", verb in enum,
              "" if verb in enum else
              f"the model physically cannot emit {verb} on a fallback night — "
              "a strict structured-output enum, and it fails with no error")

    # ── 2 · the packager ──────────────────────────────────────────────────
    try:
        packager = _imp(label, "packager")
        dispatch = dict(packager._DISPATCH)
    except Exception as exc:                           # noqa: BLE001
        dispatch = {}
        r.add("packager imports", False, f"{type(exc).__name__}: {exc}")
    for w in weapons:
        r.add(f"_DISPATCH routes kind {w!r}", w in dispatch,
              "" if w in dispatch else
              f"nothing compiles kind {w!r} — the option is offered, chosen, "
              "and silently never becomes a move")

    # ── 3 · the render gap ────────────────────────────────────────────────
    try:
        ln = _imp(label, "last_night")
        own, pub = set(ln._OWN_ACTION_TAGS), set(ln._PUBLIC_ORBITAL_TAGS)
    except Exception as exc:                           # noqa: BLE001
        own = pub = set()
        r.add("last_night imports", False, f"{type(exc).__name__}: {exc}")
    for w in weapons:
        tag = WEAPONS[w][1]
        r.add(f"own log renders {tag!r}", tag in own,
              "" if tag in own else
              f"a {w} will fire and be INVISIBLE in the seat's own execution "
              "log — the hour goes missing and the agent then journals that it "
              "never executed, corrupting the next night")
        r.add(f"rival log renders {tag!r}", tag in pub,
              "" if tag in pub else f"a rival's {w} will not show as activity")

    # ── 3b · every declared economy setting is actually APPLIED ───────────
    #
    # This class of bug is why the check exists. `strong_chain_red_min` was
    # declared on EconomyPolicy, printed by economy_summary() as though it were
    # in force, and never hooked into value_pyramid at all — so the summary
    # reported a policy the agent was not running. A setting that lies is worse
    # than a setting that is missing.
    if forged:
        eco = getattr(_imp(label, "weapon_plays"), "ECONOMY", None)
        want = getattr(eco, "strong_chain_red_min", None) if eco else None
        if want:
            try:
                vp = _imp(label, "value_pyramid")
                got = int(getattr(vp, "_STRONG_CHAIN_RED_MIN", -1))
            except Exception:                          # noqa: BLE001
                got = -1
            r.add("economy · strong_chain_red_min is applied", got == int(want),
                  "" if got == int(want) else
                  f"declared {want} but value_pyramid still reads {got} — the "
                  "hook is missing, so economy_summary() is reporting a policy "
                  "the agent is not running")
        try:
            dials = _imp(label, "orbit_policy").DEFAULT_DIALS
            if eco and getattr(eco, "buy_asap", False):
                cheapest = min(forge._blue_cost(w) for w in weapons)
                ok = int(dials.blue_always_build) == cheapest - 1
                r.add("economy · buy_asap is applied", ok,
                      "" if ok else
                      f"blue_always_build is {dials.blue_always_build}, expected "
                      f"{cheapest - 1} for a {cheapest}-blue weapon")
            for w, n in (getattr(eco, "hold_at", None) or {}).items():
                field = {"emp": "emp_stockpile_cap", "chaff": "chaff_stockpile_cap",
                         "snap": "snap_stockpile_cap"}.get(w)
                if field and hasattr(dials, field):
                    ok = int(getattr(dials, field)) == int(n)
                    r.add(f"economy · hold_at {w} is applied", ok,
                          "" if ok else
                          f"{field} is {getattr(dials, field)}, declared {n}")
        except Exception as exc:                       # noqa: BLE001
            r.add("economy · orbit dials readable", False,
                  f"{type(exc).__name__}: {exc}")

    # ── 4 · the rack, and 5 · doctrine ────────────────────────────────────
    for w in weapons:
        view = _view(w)
        if forged:
            rack = forge.format_rack_block(view)
            doc = forge.doctrine_for(view)
        else:
            try:
                pr = _imp(label, "prompt")
                rack = pr.format_rack_block(view)
            except Exception:                          # noqa: BLE001
                rack = ""
            doc = ""
        r.add(f"rack block names {w}", bool(rack) and w in rack.lower(),
              "" if rack else
              "the night phase is never told what is in the rack, so it plans "
              "as though the rack were empty")
        if forged:
            r.add(f"doctrine emitted for {w}", bool(doc.strip()),
                  "" if doc.strip() else
                  "an option nobody is told to want stays unwanted")
            has_corr = "CORRECTION" in doc
            r.add(f"doctrine CORRECTS the BEWARE_{w} framing", has_corr,
                  "" if has_corr else
                  "V12's BEWARE_ blocks describe this weapon from the "
                  "survivor's side. Without a correction that framing wins and "
                  "the weapon stays in the rack — measured: an agent refused a "
                  "chaff at H1 reasoning 'their H1 drop still lands'")

    # ── 6 · does an option actually get BUILT? ────────────────────────────
    if forged:
        try:
            agency = _imp(label, "agency")
            for w in weapons:
                # Try both board shapes: a play gated on `no_redsign` cannot
                # fire on the redsign board and vice versa, so a single
                # fixture would report a working play as broken.
                quiet = dict(_view(w))
                quiet["redsign"] = []
                built = []
                for view, pats in ((_view(w), [_FakePattern()]), (quiet, [])):
                    opts = forge.build_options(
                        view, pats, option_cls=agency.Option, day=7, day_cap=7,
                    )
                    built += [o for o in opts.values()
                              if getattr(o, "kind", "") == w]
                r.add(f"an option is built for {w}", bool(built),
                      "" if built else
                      "no option came out of build_options on a synthetic "
                      "rival-redsign board with stock in the rack — check the "
                      "play's `when` and `combines_with`")
                for o in built:
                    lines = list(getattr(o, "execute_lines", ()) or [])
                    verb = WEAPONS[w][0]
                    hit = any(verb in s for s in lines)
                    r.add(f"{o.option_id} names {verb} in execute_lines", hit,
                          "" if hit else
                          "the option exists but never tells the mover which "
                          "verb to emit")
                    r.add(f"{o.option_id} argues against an alternative",
                          "COMPARE" in (getattr(o, "rationale", "") or ""),
                          "" if "COMPARE" in (getattr(o, "rationale", "") or "")
                          else "the model is choosing BETWEEN options, so an "
                               "option that only praises itself competes badly")
        except Exception as exc:                       # noqa: BLE001
            r.add("build_options runs", False, f"{type(exc).__name__}: {exc}")

    return r


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("label")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    if not HARNESSES.is_dir():
        print("run this from the repo root (no sea_of_colours/... found)",
              file=sys.stderr)
        return 2

    res = check(args.label)

    if args.as_json:
        print(json.dumps({
            "agent": args.label, "mode": res.mode, "ok": res.ok,
            "checks": [{"name": n, "ok": o, "note": t} for n, o, t in res.rows],
        }, indent=2))
        return 0 if res.ok else 1

    width = 70
    print()
    print("-" * width)
    print(f"  WIRING CHECK · {args.label}   [{res.mode}]")
    print("-" * width)
    print()
    for name, ok, note in res.rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        for line in _wrap(note, width - 12):
            print(f"          {line}")
    print()
    if res.ok:
        print("  All wired, checked against the live objects rather than the")
        print("  source. That says the plumbing is real — not that the play is")
        print("  any good. Take it to the lab to find that out.")
    else:
        print("  Fix the topmost FAIL first. Each rung is invisible until the")
        print("  one before it works, so a lower failure may be a symptom.")
    print()
    return 0 if res.ok else 1


def _wrap(text: str, width: int) -> List[str]:
    if not text:
        return []
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


if __name__ == "__main__":
    sys.exit(main())
