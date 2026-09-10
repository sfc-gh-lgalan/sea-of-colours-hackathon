#!/usr/bin/env python3
"""forge_install.py — install the weapon-forge hooks into a fork. Once.

Copies ``weapon_forge.py`` and ``weapon_plays.py`` into the fork, then inserts
ten one-line hooks into the existing modules so they read from those files.
After this, adding a weapon or a move is an edit to ``weapon_plays.py`` and
nothing else.

WHY THIS EXISTS
    Wiring one weapon by hand across six files was measured at 7m42s and
    introduced two bugs — a broken ``if/elif`` chain in ``prompt.py``, and two
    calls to helper functions that did not exist. Every fresh mint is a
    byte-identical copy of ``tabula_v12``, so the ten insertion points are
    deterministic strings and this can be mechanical instead.

SAFETY
    * Refuses a fork it has already patched (idempotent).
    * Refuses if ANY anchor is missing or appears more than once, naming which
      — a fork that has been hand-edited is not safe to patch blind.
    * ``--dry-run`` prints every edit and writes nothing. It is the default
      when stdout is not a terminal.
    * Never touches ``_v7/`` — that is the frozen regression baseline and a
      test pins it.

USAGE
    python skills/soc-agent-forge/scripts/forge_install.py <label> --dry-run
    python skills/soc-agent-forge/scripts/forge_install.py <label> --apply
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Tuple

HARNESSES = Path("sea_of_colours/orchestrator_2/harnesses")
TEMPLATES = Path(__file__).resolve().parent.parent / "templates"

MARKER = "# --- weapon-forge hook (installed by forge_install.py) ---"


# Each hook: (file, anchor, insert_text, where)
#   where = "after"  -> insert immediately after the anchor line
#           "before" -> insert immediately before the anchor line
#           "replace"-> replace the anchor line entirely
#
# Anchors are full-line-unique strings in a pristine tabula_v12 copy. Verified.
def hooks(pkg: str) -> List[Tuple[str, str, str, str]]:
    imp = f"from sea_of_colours.orchestrator_2.harnesses.{pkg} import weapon_forge"
    return [
        # ── chat_schema.py — rung 2a, the schema wall ──────────────────────
        (
            "chat_schema.py",
            "    _MOVE_ITEM,",
            "    _MOVE_ITEM as _V7_MOVE_ITEM,",
            "replace",
        ),
        (
            "chat_schema.py",
            ")",
            f"{MARKER}\n{imp}\n"
            "# An enum in a strict structured-output schema is a HARD WALL: without\n"
            "# the verb the model physically cannot emit the move, with no error.\n"
            "_MOVE_ITEM = weapon_forge.widen_schema(_V7_MOVE_ITEM)",
            "after_first_after_anchor",
        ),

        # ── agency.py — rungs 2b and 3, the option and its menu group ──────
        (
            "agency.py",
            '    reg: "OrderedDict[str, Option]" = OrderedDict()',
            f"    {MARKER}\n"
            "    # Declared weapon plays go in first; their PRINTED position comes\n"
            "    # from _KIND_HEADERS order below, not from insertion order.\n"
            "    # `present=reg` lets a weapon rationale name a competitor that is\n"
            "    # REALLY on tonight's menu. V12 ships 18 named seam patterns plus 8\n"
            "    # numbered families, so a hardcoded 'COMPARE: BLIND_GRAB' often names\n"
            "    # a move that is not there — and the model picks moves by id.\n"
            "    reg.update(weapon_forge.build_options(\n"
            "        agent_view, seam_patterns, option_cls=Option,\n"
            "        present=list(reg)))",
            "after",
        ),
        (
            "agency.py",
            "_KIND_HEADERS = [",
            f"{MARKER}\n"
            "# Weapon groups are prepended — this list's order IS the menu's group\n"
            "# order, so a weapon buried below the grabs reads as an afterthought.\n"
            "_KIND_HEADERS_BASE = [",
            "replace",
        ),
        (
            "agency.py",
            "_KIND_BLURB = {",
            "_KIND_HEADERS = weapon_forge.extend_headers(_KIND_HEADERS_BASE)\n\n"
            "_KIND_BLURB = {",
            "replace",
        ),

        # ── packager.py — rung 2c, compiling to wire moves ─────────────────
        # (no line insertion needed; see TAIL_HOOKS + VERIFY)

        # ── prompt.py — rung 1 (the rack) and rung 4 (doctrine) ────────────
        (
            "prompt.py",
            "    geometry_block = format_weapon_geometry_block(opponent_weapon_estimates)",
            f"    {MARKER}\n"
            "    rack_block = weapon_forge.format_rack_block(agent_view)",
            "after",
        ),
        (
            "prompt.py",
            "    if geometry_block:",
            f"    {MARKER}\n"
            "    # Our own rack, AFTER the threat blocks: read what can be done to\n"
            "    # us, then what we can do back. Kept OUT of the geometry/weapons\n"
            "    # if-elif chain below — that chain is a spacing fix between two\n"
            "    # THREAT blocks and folding this in suppresses its blank line.\n"
            "    if rack_block:\n"
            "        parts += [rack_block, \"\\n\"]",
            "before",
        ),
        (
            "prompt.py",
            '        text += "\\n\\n" + doctrine.DOCTRINE_BEWARE_SNAP',
            f"    {MARKER}\n"
            "    # Gated on OUR OWN rack, not a rival's estimated arsenal. Every\n"
            "    # BEWARE_ block above fires on the THREAT side, which is why the\n"
            "    # baseline says nothing about spending ordnance on a quiet board —\n"
            "    # precisely the cheapest night to fire. This also emits the\n"
            "    # CORRECTIONS that answer those blocks; without them the survivor\n"
            "    # framing wins and the weapon stays in the rack.\n"
            "    _weapon_doctrine = weapon_forge.doctrine_for(agent_view)\n"
            "    if _weapon_doctrine:\n"
            "        text += \"\\n\\n\" + _weapon_doctrine",
            "after",
        ),

        # ── the blue economy — fund and buy what you declared ──────────────
        (
            "orbit_policy.py",
            "DEFAULT_DIALS = OrbitDials()",
            f"{MARKER}\n"
            "# ECONOMY from weapon_plays.py: buy the cheapest declared weapon as\n"
            "# soon as it is affordable, and never buy ordnance with no play.\n"
            "DEFAULT_DIALS = weapon_forge.tune_dials(OrbitDials())",
            "replace",
        ),
        (
            "prompt.py",
            "    if not blue_vault_is_short(agent_view):",
            f"    {MARKER}\n"
            "    # The stock gate asks whether the VAULT is short, which is the\n"
            "    # wrong question for an armed seat: a 'medium' vault can hold 150\n"
            "    # and still be 150 short of a charge. Also ask whether the rack\n"
            "    # can fire at all.\n"
            "    if not (blue_vault_is_short(agent_view)\n"
            "            or weapon_forge.blue_also_requested(agent_view)):",
            "replace",
        ),
        (
            "prompt.py",
            '        text += "\\n\\n" + doctrine.DOCTRINE_BLUE',
            f"        {MARKER}\n"
            "        # DOCTRINE_BLUE ranks blue below red, which is right for an\n"
            "        # agent that spends blue on nothing. Correct it while the\n"
            "        # rack is empty — emitted AFTER, so recency favours it.\n"
            "        _blue_ammo = weapon_forge.blue_doctrine_for(agent_view)\n"
            "        if _blue_ammo:\n"
            "            text += \"\\n\\n\" + _blue_ammo",
            "after",
        ),

        # ── value_pyramid.py — the blue/red trade ─────────────────────────
        (
            "value_pyramid.py",
            "_STRONG_CHAIN_RED_MIN = 150",
            f"{MARKER}\n"
            "# ECONOMY.strong_chain_red_min: the red a chain must bank before it\n"
            "# outranks a blue run for a harvester. Raising it diverts a unit to\n"
            "# blue unless the red on offer is genuinely better — which is what an\n"
            "# agent whose weapon is BOUGHT with blue actually wants.\n"
            "_STRONG_CHAIN_RED_MIN = weapon_forge.strong_chain_red_min(150)",
            "replace",
        ),

        # ── last_night.py — the render gap ────────────────────────────────
        # (no line insertion needed; see TAIL_HOOKS + VERIFY)
    ]


# Symbols a tail hook depends on. Verified as SUBSTRINGS rather than exact
# lines, because a maintainer may legitimately reflow a set literal onto one
# line — as happened to ``_PUBLIC_ORBITAL_TAGS`` when SNAP was added to the
# baseline. An exact-line anchor would break on formatting; a name would not.
VERIFY: List[Tuple[str, str]] = [
    ("packager.py", "_DISPATCH = {"),
    ("last_night.py", "_OWN_ACTION_TAGS"),
    ("last_night.py", "_PUBLIC_ORBITAL_TAGS"),
]


# Hooks that append a line after a whole statement rather than a single line.
TAIL_HOOKS: Dict[str, str] = {
    "packager.py": (
        f"\n{MARKER}\n"
        "# Register a packer per declared weapon. Without a _DISPATCH entry the\n"
        "# option is offered, chosen, and silently never compiles.\n"
        "_DISPATCH.update(weapon_forge.packers())\n"
    ),
    "last_night.py": (
        f"\n{MARKER}\n"
        "# Without these a weapon fires in the engine and is INVISIBLE in the\n"
        "# seat's own execution log — the hour simply goes missing, and the agent\n"
        "# then journals that it never executed, corrupting the next night.\n"
        "# Note the wire verb and the replay tag are NOT always the same string:\n"
        "# snap_launch on the wire arrives as `snap` in the frame.\n"
        "_OWN_ACTION_TAGS |= weapon_forge.frame_tags()\n"
        "_PUBLIC_ORBITAL_TAGS |= weapon_forge.public_tags()\n"
    ),
    "agency.py": "",
    "chat_schema.py": "",
    "prompt.py": "",
    "orbit_policy.py": "",
    "value_pyramid.py": "",
}


def _imports_line(pkg: str) -> str:
    return f"from sea_of_colours.orchestrator_2.harnesses.{pkg} import weapon_forge"


def check(root: Path) -> Tuple[bool, List[str]]:
    """Verify every anchor is present exactly once. Returns (ok, problems)."""
    problems: List[str] = []
    pkg = root.name
    seen: Dict[str, int] = {}
    for fname, anchor, _text, _where in hooks(pkg):
        key = f"{fname}::{anchor}"
        if key in seen:
            continue
        seen[key] = 1
        f = root / fname
        if not f.is_file():
            problems.append(f"{fname}: missing from the fork")
            continue
        src = f.read_text(encoding="utf-8")
        n = sum(1 for ln in src.splitlines() if ln == anchor)
        if n == 0:
            problems.append(
                f"{fname}: anchor not found -> {anchor.strip()!r}. This fork "
                "has been hand-edited; patch it manually or re-mint."
            )
        elif n > 1:
            problems.append(
                f"{fname}: anchor appears {n} times -> {anchor.strip()!r}. "
                "Ambiguous, refusing to guess."
            )

    # Tail hooks only need the symbol to exist somewhere, so these are
    # substring checks — resilient to a set literal being reflowed.
    for fname, needle in VERIFY:
        f = root / fname
        if not f.is_file():
            problems.append(f"{fname}: missing from the fork")
            continue
        if needle not in f.read_text(encoding="utf-8"):
            problems.append(
                f"{fname}: expected to define {needle!r} and does not. This "
                "fork does not look like a tabula_v12 copy."
            )
    return (not problems), problems


def already_installed(root: Path) -> bool:
    fg = root / "weapon_forge.py"
    if fg.is_file():
        return True
    for fname in ("agency.py", "packager.py", "prompt.py"):
        f = root / fname
        if f.is_file() and MARKER in f.read_text(encoding="utf-8"):
            return True
    return False


def build_edits(root: Path) -> Dict[str, str]:
    """Produce the new content for each touched file. Pure — writes nothing."""
    pkg = root.name
    imp = _imports_line(pkg)
    out: Dict[str, str] = {}

    for fname, anchor, text, where in hooks(pkg):
        src = out.get(fname) or (root / fname).read_text(encoding="utf-8")
        lines = src.splitlines()
        new: List[str] = []
        done = False
        for ln in lines:
            if ln == anchor and not done:
                done = True
                if where == "replace":
                    new.append(text)
                elif where == "before":
                    new.append(text)
                    new.append(ln)
                elif where == "after":
                    new.append(ln)
                    new.append(text)
                elif where == "after_first_after_anchor":
                    new.append(ln)
                    new.append("")
                    new.append(text)
                continue
            new.append(ln)
        out[fname] = "\n".join(new) + "\n"

    # Tail additions, plus the import every hooked file needs. Files that only
    # need a tail (packager, last_night) are loaded here for the first time.
    for fname, tail in TAIL_HOOKS.items():
        src = out.get(fname)
        if src is None:
            f = root / fname
            if not f.is_file():
                continue
            src = f.read_text(encoding="utf-8")
        if tail:
            src = src.rstrip("\n") + "\n" + tail
        if imp not in src:
            src = _insert_import(src, imp)
        out[fname] = src
    return out


def _insert_import(src: str, imp: str) -> str:
    """Insert the import after the file's LEADING import block.

    Done with AST rather than line heuristics. A scan for "the last line
    starting with from/import" walks straight past the header and lands inside
    a function body that happens to contain a deferred import — which produced
    an IndentationError at agency.py:1508 on the first attempt.
    """
    import ast

    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src

    end = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            end = max(end, int(getattr(node, "end_lineno", node.lineno) or 0))
        elif end:
            break          # first non-import at top level: header is over
    if end <= 0:
        end = 1

    lines = src.splitlines()
    block = ["", MARKER, imp]
    return "\n".join(lines[:end] + block + lines[end:]) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("label", help="agent label, e.g. redwatch_reaper")
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (default is a dry run)")
    args = ap.parse_args()

    if not HARNESSES.is_dir():
        print("run this from the repo root (no sea_of_colours/... found)",
              file=sys.stderr)
        return 2

    root = HARNESSES / args.label
    if not root.is_dir():
        print(f"no such fork: {root}", file=sys.stderr)
        return 2

    if already_installed(root):
        print(f"  {args.label}: forge already installed — nothing to do.")
        print("  Add weapons by editing weapon_plays.py in that folder.")
        return 0

    ok, problems = check(root)
    if not ok:
        print(f"\n  REFUSING to patch {args.label}:\n")
        for p in problems:
            print(f"    - {p}")
        print()
        return 1

    edits = build_edits(root)

    print()
    print(f"  forge install · {args.label}" + ("" if args.apply else "  (DRY RUN)"))
    print("  " + "-" * 60)
    print(f"  copy in    weapon_forge.py   ({_tlines('weapon_forge.py')} lines, do not edit)")
    print(f"  copy in    weapon_plays.py   ({_tlines('weapon_plays.py')} lines, YOURS)")
    for fname in sorted(edits):
        before = len((root / fname).read_text(encoding="utf-8").splitlines())
        after = len(edits[fname].splitlines())
        print(f"  hook       {fname:<18} {before} -> {after} lines (+{after - before})")
    print()

    if not args.apply:
        print("  Nothing written. Re-run with --apply to install.")
        print()
        return 0

    for fname, content in edits.items():
        (root / fname).write_text(content, encoding="utf-8")
    for t in ("weapon_forge.py", "weapon_plays.py"):
        shutil.copy2(TEMPLATES / t, root / t)

    print("  Installed. Next:")
    print(f"    1. edit  {root}/weapon_plays.py")
    print(f"    2. check python skills/soc-agent-forge/scripts/check_wiring.py {args.label}")
    print()
    return 0


def _tlines(name: str) -> int:
    try:
        return len((TEMPLATES / name).read_text(encoding="utf-8").splitlines())
    except OSError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
