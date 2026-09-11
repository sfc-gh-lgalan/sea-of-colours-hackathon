#!/usr/bin/env python3
"""check_docs — assert the skill's PROSE still matches its CODE.

Every claim in this skill's markdown that can be derived mechanically should be
derived mechanically. Three real bugs in one day came from prose drifting away
from the thing it described:

  * the hook table listed 5 files and said "ten hooks"; the installer had 12
    hooks across 7 files, because the economy work landed after the table
  * `phases/1-choose.md` documented `require_spare_harvester=True` for hours
    after the field was deleted as a knob that could not be turned off
  * `economy_summary()` reported `strong_chain_red_min` as active while no hook
    applied it — the setting lied

None of those break a test. They mislead a human under time pressure, which in a
hackathon is worse. Run this after touching the installer or `EconomyPolicy`:

    python skills/soc-agent-forge/scripts/check_docs.py

Exit 0 clean, 1 with findings. No arguments, no side effects, reads only.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import List, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
FINDINGS: List[Tuple[str, str]] = []

#: Tokens that look like a field assignment but are REPLAY-CARD syntax, so the
#: docs quote them legitimately. `[fallback=True]` on a plan line means the model
#: was never reached; it is not a setting anyone can declare.
_NOT_FIELDS = {"fallback", "plan", "predicted", "session", "seed", "day"}


def fail(where: str, what: str) -> None:
    FINDINGS.append((where, what))


def _read(rel: str) -> str:
    p = ROOT / rel
    return p.read_text(encoding="utf-8") if p.is_file() else ""


# ── 1 · the hook table and count ──────────────────────────────────────────

def check_hooks() -> None:
    """The installer is the source of truth for what gets patched."""
    src = _read("scripts/forge_install.py")
    if not src:
        fail("scripts/forge_install.py", "missing — cannot verify hook claims")
        return

    # Files the installer touches: the first element of each hook tuple, plus
    # the tail-hook keys. Both are string literals of the form "name.py".
    files: Set[str] = set(re.findall(r'"([a-z_]+\.py)"', src))
    # Copied wholesale, not hooked — they belong in the copy list, not the
    # hook table. scorch.py joined them when the missing-geometry bug was fixed.
    files -= {"weapon_forge.py", "weapon_plays.py", "scorch.py"}

    # Hooks = anchored inserts + tail additions. Counted from the call each one
    # installs, which is also what makes the installer idempotent per hook.
    # Count HOOK ENTRIES, not weapon_forge calls. Two hooks (the uncapped
    # surplus-branch gates) insert a plain guard with no call in it, so counting
    # calls undercounted by two and the lint reported "clean" on a wrong number.
    n_hooks = len(re.findall(r'^\s+\(\n\s+"[a-z_]+\.py",', src, re.M))

    for rel in ("SKILL.md", "phases/2-generate.md"):
        doc = _read(rel)
        if not doc:
            continue
        for claim in re.findall(r"(\w+) one-line hooks", doc):
            if _spelled(claim) != n_hooks:
                fail(rel, f"claims '{claim} one-line hooks'; the installer "
                          f"has {n_hooks}")
        # ...and the FILE count in the same sentence. This check used to verify
        # only the hook number, so "seventeen one-line hooks across eight files"
        # linted clean while the installer patched seven. Two numbers in one
        # phrase means two claims, and a reader trusts both.
        #
        # Anchored to "one-line hooks across N files" rather than a bare
        # "across N files", because the docs legitimately say other things about
        # other file counts — `emp_harvest_test` is "~2,550 lines across 11
        # files", and hand-wiring a play "across six files" is a historical
        # measurement. A lint that flags true sentences gets switched off.
        for claim in re.findall(r"one-line hooks across (\w+) files", doc):
            if _spelled(claim) != len(files):
                fail(rel, f"claims 'across {claim} files'; the installer "
                          f"patches {len(files)}: {', '.join(sorted(files))}")

    table = set(re.findall(r"^\| `([a-z_]+\.py)`", _read("phases/2-generate.md"),
                           re.M))
    if table and table != files:
        missing = sorted(files - table)
        extra = sorted(table - files)
        if missing:
            fail("phases/2-generate.md",
                 f"hook table is MISSING {', '.join(missing)} — the installer "
                 f"patches {len(files)} files, the table lists {len(table)}")
        if extra:
            fail("phases/2-generate.md",
                 f"hook table lists {', '.join(extra)}, which the installer "
                 "does not patch")


_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
          "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
          "twenty": 20}


def _spelled(word: str) -> int:
    return _WORDS.get(word.lower(), int(word) if word.isdigit() else -1)


# ── 2 · every documented EconomyPolicy field still exists ─────────────────

def check_economy_fields() -> None:
    """A doc naming a deleted field sends a team to write code that does nothing."""
    src = _read("templates/weapon_forge.py")
    if not src:
        fail("templates/weapon_forge.py", "missing")
        return

    fields: Set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ClassDef) and node.name == "EconomyPolicy":
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(
                        stmt.target, ast.Name):
                    fields.add(stmt.target.id)
    if not fields:
        fail("templates/weapon_forge.py", "no EconomyPolicy fields found")
        return

    # Anything that LOOKS like a field assignment in the docs must be real.
    for doc in sorted(ROOT.glob("phases/*.md")) + sorted(
            ROOT.glob("references/*.md")):
        text = doc.read_text(encoding="utf-8")
        # The backtick must ABUT the name: `[fallback=True]` is a card
        # annotation, not a field, and flagging it is noise.
        for name in set(re.findall(r"`([a-z_]\w*)=(?:True|False|\d|\{|None)",
                                   text)):
            if name in fields or name in _NOT_FIELDS:
                continue
            # WeaponPlay fields are documented the same way — allow those.
            if name in _weaponplay_fields(src):
                continue
            fail(str(doc.relative_to(ROOT)),
                 f"documents `{name}=` but no such field exists on "
                 "EconomyPolicy or WeaponPlay")


def _weaponplay_fields(src: str) -> Set[str]:
    out: Set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ClassDef) and node.name == "WeaponPlay":
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(
                        stmt.target, ast.Name):
                    out.add(stmt.target.id)
    return out


# ── 3 · every setting the summary reports is actually enforced ────────────

def check_settings_enforced() -> None:
    """A setting that is reported but never applied is worse than one missing.

    `economy_summary()` printing a policy the agent is not running is the exact
    shape of the `strong_chain_red_min` bug.
    """
    src = _read("templates/weapon_forge.py")
    if not src or "def economy_summary" not in src:
        return
    summary = src[src.index("def economy_summary"):]
    before = src[:src.index("def economy_summary")]
    install = _read("scripts/forge_install.py")

    for field in re.findall(r"eco\.(\w+)", summary):
        used_elsewhere = f"eco.{field}" in before
        # A field may instead be applied by a named helper the installer hooks.
        hooked = field in install
        if not used_elsewhere and not hooked:
            fail("templates/weapon_forge.py",
                 f"economy_summary() reports `{field}` but nothing applies it — "
                 "the summary would describe a policy the agent is not running")


# ── 4 · phase and reference cross-links resolve ───────────────────────────

def check_links() -> None:
    for doc in sorted(ROOT.rglob("*.md")):
        text = doc.read_text(encoding="utf-8")
        for ref in set(re.findall(r"`((?:phases|references|templates|scripts)"
                                  r"/[\w.-]+)`", text)):
            if not (ROOT / ref).exists():
                fail(str(doc.relative_to(ROOT)),
                     f"links `{ref}` which does not exist")


def main() -> int:
    check_hooks()
    check_economy_fields()
    check_settings_enforced()
    check_links()

    print()
    print("  DOC CHECK · soc-agent-forge")
    print("  " + "-" * 66)
    if not FINDINGS:
        print("  Clean. Every mechanically-derivable claim matches the code.")
        print()
        return 0
    for where, what in FINDINGS:
        print(f"  DRIFT  {where}")
        for line in _wrap(what):
            print(f"           {line}")
    print()
    print(f"  {len(FINDINGS)} finding(s). These do not break tests — they "
          "mislead\n  a human under time pressure, which is worse.")
    print()
    return 1


def _wrap(text: str, width: int = 62) -> List[str]:
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


if __name__ == "__main__":
    sys.exit(main())
