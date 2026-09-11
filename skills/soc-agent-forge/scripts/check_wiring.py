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
#: (wire verb, replay frame tag) per weapon. They are the SAME for all three:
#: v12 v1.48 corrected a comment in `last_night.py` that had claimed the snap
#: frame was spelled `snap` while the wire verb was `snap_launch`. This table
#: encoded that mistake, so the checker would have passed a forge emitting the
#: wrong tag and failed one emitting the right one.
WEAPONS: Dict[str, Tuple[str, str]] = {
    "emp": ("emp_launch", "emp_launch"),
    "chaff": ("chaff_flare", "chaff_flare"),
    "snap": ("snap_launch", "snap_launch"),
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


def _why_no_option(forge: Any, label: str, weapon: str) -> str:
    """The REAL reason an option did not build, not a guess at it.

    Two causes dominate and they need opposite fixes: a missing `scorch.py`
    (geometry unavailable, so no aim points) versus a `when`/`targets` that
    genuinely did not match the board.
    """
    # 1. the module the targeting modes depend on
    try:
        _imp(label, "scorch")
    except Exception as exc:                            # noqa: BLE001
        return ("  CAUSE: scorch.py is missing or will not import "
                f"({type(exc).__name__}: {exc}).\n"
                "  `targets=\"rival_probes\"` and `targets=\"redsign\"` need it "
                "for salvo geometry.\n"
                "  FIX: re-run forge_install.py --upgrade --apply, which now "
                "copies it in.")

    # 2. ask the forge itself what it saw
    try:
        plays = [p for p in getattr(_imp(label, "weapon_plays"), "PLAYS", ())
                 if p.weapon == weapon]
        for p in plays:
            for view, pats in ((_view(weapon), [_FakePattern()]),
                               ({**_view(weapon), "redsign": []}, [])):
                _aim, _comb, notes = forge._aim_points(p, view, pats)
                if notes:
                    return (f"  {p.play_id} reported: " + "; ".join(notes)
                            + f"\n  (when={p.when!r}, targets={p.targets!r}, "
                              f"min_targets={p.min_targets})")
        if plays:
            p = plays[0]
            return (f"  no notes returned. Check when={p.when!r} against the "
                    "board, and\n  min_targets="
                    f"{p.min_targets} against how many targets exist.")
    except Exception as exc:                            # noqa: BLE001
        return f"  could not diagnose further: {type(exc).__name__}: {exc}"
    return "  no plays declared for this weapon."


def _view(weapon: str, n: int = 2) -> Dict[str, Any]:
    """A synthetic board rich enough for every targeting mode to fire.

    It must carry a redsign with real ``cells``: ``scorch.redsign_targets``
    reads the smear off the view and returns nothing for a region with no
    cells, which made every ``targets="redsign"`` play look broken when it was
    the fixture that was thin.

    Two more shapes are load-bearing for the same reason, both added after a
    real play was reported broken by a thin board:

    * ONE rival eye INSIDE probe-vision range of the smear centre, or
      ``targets="finder_probe"`` finds no covering eye and refuses. The three
      distant eyes below rank for ``rival_probes`` but cover nothing.
    * A PURE — a ``red_tiles`` row at purity 255 — inside that same smear, with
      an eye near it, or ``targets="contested_pure"`` has nothing to contest.
      `red_tiles` used to be empty here, which is not a board any pure play can
      fire on.
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
        # a RIVAL smear (mine False) — an own sign is refused by design.
        # `center` must be a LIST, which is the shape the real seat view uses:
        # option_economics reads `row.get("center", row.get("centre"))` and
        # `finder_probes` the same. This fixture carried only x/y, so every
        # centre-reading mode saw no centre and refused on a board that looked
        # complete.
        "redsign": [{"cells": smear, "mine": False,
                     "center": [21, 20], "x": 21, "y": 20}],
        # the pure they lit, at full purity, visible to US. This is what
        # `contested_pure` aims at and what `finder_probe` upgrades on.
        "red_tiles": [{"x": 21, "y": 20, "purity": 255}],
        "blue_tiles": [], "blue_sign": [],
        # three rival eyes far off, freshest last, so recency ranking has
        # something to rank and min_targets has enough to clear — PLUS one eye
        # adjacent to the pure, which is the covering eye every pure-denial
        # mode needs.
        "enemy_probes": [{"at": [10 + 3 * i, 10], "day_seen": i + 1}
                         for i in range(3)] + [{"at": [22, 20], "day_seen": 4}],
        "rival_probes": [{"x": 10 + 3 * i, "y": 10, "freshness": "fresh"}
                         for i in range(3)] + [{"x": 22, "y": 20,
                                                "freshness": "fresh"}],
        "station_intel": {"self": {"blue": {"grade": "low"}}},
    }


def _imp(label: str, mod: str) -> Any:
    return importlib.import_module(f"{PKG_ROOT}.{label}.{mod}")


def check(label: str, *, skip_llm: bool = False) -> Result:
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

    # ── -1 · CAN THE MODEL BE REACHED AT ALL? ─────────────────────────────
    #
    # Ranks BELOW procurement because nothing else means anything without it.
    # A whole afternoon went into diagnosing "the weapons never fire" when every
    # model call was returning HTTP 401 in 130ms: the seat fell back to the
    # built-in heuristic, the heuristic passes the night, and a passing agent
    # never harvests, never banks blue and never fires. Three wrong theories —
    # prompt size, a malformed schema, the forge itself — before anyone called
    # the model directly.
    #
    # `soc doctor` used to be insufficient here — its `credentials_status()`
    # returns (True, '') whenever a token EXISTS, so it reported "LLM
    # credentials present" while every request was refused. Since v1.48
    # doctor makes this same real call, so the two now agree. We keep the
    # check anyway: this script is the one an agent runs unattended, and it
    # should not depend on the user having run another command first.
    if not skip_llm:
        try:
            from sea_of_colours.orchestrator_2.cortex_chat import (
                CortexChatInvoker,
            )
            from sea_of_colours.orchestrator_2.harnesses.tabula_v12.harness \
                import _THINKER_CHAT_MODEL as _MODEL
            res = CortexChatInvoker(
                model=_MODEL, response_format=None, max_completion_tokens=20,
            ).invoke("Reply with exactly: OK", wallclock_cap_s=20)
            ok = bool(res.get("ok"))
            err = str(res.get("error") or "")[:200]
            r.add("the model answers a real call", ok,
                  "" if ok else
                  f"HTTP {res.get('status_code')} in "
                  f"{res.get('elapsed_ms')}ms.\n  {err}\n"
                  "  Every LLM seat will fall back to the heuristic, which "
                  "PASSES the night —\n  so no blue is banked and nothing "
                  "ever fires. This is not an agent bug.\n"
                  "  A 390422 means this machine's IP is not on the Snowflake "
                  "network policy\n  allowlist: check whether your VPN is on.")
        except Exception as exc:                        # noqa: BLE001
            r.add("the model answers a real call", False,
                  f"could not even attempt it: {type(exc).__name__}: {exc}")

    # ── 0 · PROCUREMENT — can the agent even BUY the weapon? ──────────────
    #
    # Rung zero, and the one that hid a real bug. Every firing rung can pass
    # while the rack stays empty all game, because the orbital is a separate
    # code path. Stock tabula_v12 can only emit build_chaff and build_emp.
    for w in weapons:
        try:
            op = _imp(label, "orbit_policy")
            # `blue_purity_total` is the key the orbital reads — a plausible
            # `blue_purity` reads as 0 and fails a working agent.
            # Deliberately RICH. The orbital spends on harvesters and probes
            # before it reaches ordnance, so a lean fixture starves procurement
            # and fails a working agent — the third false FAIL of this exact
            # shape. The question is "CAN it buy", not "does it prioritise".
            view = {"orbit": {"credits": 6000, "blue_purity_total": 3000,
                              "weapon_stock": {}, "probes": 4,
                              "harvesters": [{"id": "h1"}, {"id": "h2"}]},
                    "my_assets": [], "blue_tiles": [], "red_tiles": []}
            acts, _why = op.plan_orbit_actions(view, weapons_enabled=True)
            bought = {a.get("a") for a in acts}
            ok = f"build_{w}" in bought
            r.add(f"procurement · orbital can buy {w}", ok,
                  "" if ok else
                  f"no build_{w} action with 900 blue and 900 credits in hand. "
                  f"The play can never arm — every firing rung below may PASS "
                  f"while the rack stays empty for the whole game. Emitted: "
                  f"{sorted(bought) or 'nothing'}")
        except Exception as exc:                        # noqa: BLE001
            r.add(f"procurement · orbital can buy {w}", False,
                  f"{type(exc).__name__}: {exc}")

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
                # Diagnose rather than guess. `_aim_points` catches import and
                # geometry failures into a notes list and returns no aim, so the
                # option silently vanishes. The first version of this message
                # blamed `when`/`combines_with` and sent a builder down the
                # wrong path for most of an hour when the real cause was a
                # missing scorch.py.
                r.add(f"an option is built for {w}", bool(built),
                      "" if built else
                      "no option came out of build_options on a synthetic "
                      "board with stock in the rack.\n"
                      + _why_no_option(forge, label, w))
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
    ap.add_argument("--skip-llm", action="store_true",
                    help="skip the live model call (offline, or in a tight "
                         "loop). Every other check is static.")
    ap.add_argument("label")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    if not HARNESSES.is_dir():
        print("run this from the repo root (no sea_of_colours/... found)",
              file=sys.stderr)
        return 2

    res = check(args.label, skip_llm=args.skip_llm)

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
