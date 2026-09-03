"""sagar_cursor — OUT-OF-GRID KNOWLEDGE: what you can SENSE but cannot SEE.

WORLD VIEW (the block above this one) is strictly live vision. This block is
the other half of the board model: engine-truth facts the seat holds about
cells it has NO current line of sight on. Two kinds:

  * SIGNS — public beacons over high-tier seams (``redsign`` = pure RED,
    ``blue_sign`` = fissile blue). Visible to every house through fog, but
    deliberately SMEARED: the engine strips the real cells and exposes only a
    fuzzy, off-centre centroid. You know an area, never a square.
  * ECHOES — cells this seat DID see and no longer does (``world.echo`` rows,
    which carry ``last_seen_day``). Exact coordinates, possibly stale content.

The distinction drives the play, so the block states it plainly: a sign must be
PINNED (probe it, then work the disk) while a high-tier echo is already pinned
and can be hot-dropped on the exact cell. That asymmetry is the whole reason an
echoed pure outranks a redsign as a target.

Rendering is conditional per section — a seat with no signs and no rich echoes
gets no block at all, so quiet nights pay no token cost.

Pure function over the engine ``agent_view``; no state, no persistence.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence, Set, Tuple

from sea_of_colours.game.tuning import probe_vision_radius
from sea_of_colours.orchestrator_2.harnesses.sagar_cursor._v7.probe_hints import (
    _enemy_probe_cells,
)
from sea_of_colours.orchestrator_2.harnesses.sagar_cursor.world_view import (
    _live_rows,
    _my_probe_cells,
    _tier,
)

Cell = Tuple[int, int]

# An echo is worth listing at mass or better (the band where a single cell
# changes a night's scoreline). Matches world_view._tier's mass floor.
_ECHO_MIN_PURITY = 151
# Age bands, in nights since last sighting.
_ECHO_HOT_NIGHTS = 1     # ~certain to still be there
_ECHO_COLD_NIGHTS = 3    # probably mined out by now
_ECHO_MAX_ROWS = 8
_BLUE_SIGN_MAX_ROWS = 4

# A probe "covers" a sign when its disk plausibly overlaps the smear: the
# probe's own vision radius plus the redsign match radius the seam machinery
# already uses. Chebyshev, deliberately generous — this is a fold/contest
# heuristic for the reader, not a targeting computation.
_SIGN_ACCESS_SLACK = 2


def _cheb(a: Cell, b: Cell) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _center_of(row: Mapping[str, Any]) -> "Cell | None":
    """Smear centroid: explicit ``center``, else the mean of ``cells``."""
    c = row.get("center")
    if isinstance(c, (list, tuple)) and len(c) >= 2:
        try:
            return (int(round(float(c[0]))), int(round(float(c[1]))))
        except (TypeError, ValueError):
            pass
    cells = row.get("cells")
    if isinstance(cells, (list, tuple)) and cells:
        xs: List[float] = []
        ys: List[float] = []
        for pt in cells:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                try:
                    xs.append(float(pt[0]))
                    ys.append(float(pt[1]))
                except (TypeError, ValueError):
                    continue
        if xs:
            return (
                int(round(sum(xs) / len(xs))),
                int(round(sum(ys) / len(ys))),
            )
    return None


def _live_cells(agent_view: Mapping[str, Any]) -> Set[Cell]:
    out: Set[Cell] = set()
    for row in _live_rows(agent_view):
        try:
            out.add((int(row["x"]), int(row["y"])))
        except (TypeError, KeyError, ValueError):
            continue
    return out


def _smear_cells(row: Mapping[str, Any]) -> Set[Cell]:
    out: Set[Cell] = set()
    for pt in (row.get("cells") or []):
        if isinstance(pt, Mapping):
            xf, yf = pt.get("x"), pt.get("y")
        elif isinstance(pt, (list, tuple)) and len(pt) >= 2:
            xf, yf = pt[0], pt[1]
        else:
            continue
        try:
            out.add((int(round(float(xf))), int(round(float(yf)))))
        except (TypeError, ValueError):
            continue
    return out


def _strength_note(
    agent_view: Mapping[str, Any], row: Mapping[str, Any], day: int,
) -> str:
    """How much of this seam is left, as far as we can tell (fix 0.8, OBS-45).

    A redsign retires ONLY when its last pure is taken, so a beacon still on the
    card is a hard guarantee that at least one pure survives — a genuinely
    valuable fact the card never stated. What it does NOT distinguish is a
    virgin four-pure seam from one three houses have already worked: the
    footprint, intensities and framing are frozen at mint and identical either
    way. We cannot see the engine's remaining count, but we can say what OUR
    eyes make of the seam right now, and how stale that reading is.
    """
    smear = _smear_cells(row)
    if not smear:
        return ""
    live: Dict[Cell, Tuple[str, int]] = {}
    for r in _live_rows(agent_view):
        try:
            cell = (int(r["x"]), int(r["y"]))
        except (TypeError, KeyError, ValueError):
            continue
        if cell in smear:
            live[cell] = (
                str(r.get("tile") or "").upper(), int(r.get("purity") or 0),
            )
    pure = sum(1 for t, p in live.values() if t == "RED" and p >= 255)
    mass = sum(
        1 for t, p in live.values() if t == "RED" and _tier(p) == "mass"
    )
    stripped = sum(1 for t, _p in live.values() if t in ("GREEN", "EMPTY"))

    head = "the beacon is LIVE, so at least one pure SURVIVES"
    if not live:
        # No LIVE sight. That is not the same as never having looked: most
        # fogged seams still carry echo rows from an earlier night, and calling
        # that "NEVER seen" is precisely the kind of false statement this phase
        # exists to remove. ``red_tiles`` carries no date (the view does not
        # stamp one), so we report the reading and are explicit that its age is
        # unknown rather than inventing one.
        echo_pure = echo_mass = echo_seen = 0
        for r in (agent_view.get("red_tiles") or []):
            if not isinstance(r, Mapping):
                continue
            try:
                cell = (int(r["x"]), int(r["y"]))
                p = int(r.get("purity") or 0)
            except (TypeError, KeyError, ValueError):
                continue
            if cell not in smear:
                continue
            echo_seen += 1
            if p >= 255:
                echo_pure += 1
            elif _tier(p) == "mass":
                echo_mass += 1
        if echo_seen:
            return (
                f"strength: {head} — you have NO live sight of it; your last "
                f"reading (undated) held {echo_pure} pure + {echo_mass} mass "
                f"over {echo_seen} remembered cell(s), and a rival may have "
                "worked it since"
            )
        return f"strength: {head} — but you have NEVER seen inside this smear"

    seen_bits = f"{pure} pure + {mass} mass visible"
    if stripped:
        seen_bits += f", {stripped} cell(s) already stripped"
    if pure:
        return f"strength: {head}; {seen_bits} — the jackpot is in your sight"
    return (
        f"strength: {head}; {seen_bits} — the surviving pure is in the part "
        "you CANNOT see"
    )


def _access_note(
    center: Cell, mine: Sequence[Cell], enemy: Sequence[Cell],
) -> str:
    """Who has a probe positioned to work this sign TONIGHT."""
    reach = probe_vision_radius() + _SIGN_ACCESS_SLACK
    m = sum(1 for p in mine if _cheb(p, center) <= reach)
    e = sum(1 for p in enemy if _cheb(p, center) <= reach)
    if m and e:
        return f"probes in range: YOURS x{m}, RIVAL x{e} — contested"
    if m:
        return f"probes in range: YOURS x{m} — you have the inside track"
    if e:
        return f"probes in range: RIVAL x{e}, none of yours — they are ahead"
    return "probes in range: NOBODY — open board, first to pin it wins"


# ── section builders ────────────────────────────────────────────────────
_HEADER = (
    "OUT-OF-GRID KNOWLEDGE — facts you KNOW but have NO direct visibility "
    "over.\n"
    "  WORLD VIEW above is live sight only. This block is everything else you\n"
    "  legitimately know: beacons you can sense through fog (SIGNS) and cells\n"
    "  you saw earlier and no longer watch (ECHOES). None of it is guesswork,\n"
    "  but none of it is current sight either — a SIGN gives you an AREA and\n"
    "  never a square; an ECHO gives you an exact square and a stale reading.\n"
    "  PINNING a target is the job: HOT DROP (probe at hour 1, then drop a\n"
    "  harvester INSIDE the new disk the same night, then walk + pick up) is\n"
    "  the fastest way to turn out-of-grid knowledge into banked cargo."
)

_PURE_ECONOMICS = (
    "  WHY A PURE DECIDES THE SEASON: pure is the top purity band and there\n"
    "  are only a handful of pure cells on the whole map — a redsign is not\n"
    "  \"a good chain\", it is a slice of the final scoreline. And the swing is\n"
    "  DOUBLE: a pure you bank is also a pure your rival never banks, so a\n"
    "  contested seam moves the table by twice its face value.\n"
    "  => A live redsign should almost ALWAYS be contested. Declining is\n"
    "     normally a losing move, and in a 1v1 it is almost always losing.\n"
    "     The only sound reasons to decline are:\n"
    "       * you can SEE a pure elsewhere in WORLD VIEW and can take it now;\n"
    "       * you can SEE mass you can bank tonight worth more than the gamble;\n"
    "       * several rivals already hold probes on the sign and you hold none\n"
    "         (read the \"probes in range\" note on each sign before you fold).\n"
    "     Even when you decline the main push, ONE comb or ONE staged probe\n"
    "     into the smear is usually worth the hour."
)

# Conversion GEOMETRY (CASE 1 / CASE 2, blind-walk, short-chain) is owned by the
# single redsign doctrine surface in Section 1 — see the hyg-dedup note in
# prompt.py. All that belongs here is the one thing that surface cannot know:
# that a solved seam's exact cells are sitting in ECHOES below. Denial stays
# because nothing else in the prompt frames a rival's spill as an OBJECTIVE —
# every other mention of spilling treats it purely as a risk to our own load.
_DENIAL = (
    "  DENIAL IS A REAL PLAY: a rival harvester that dies loaded SPILLS its\n"
    "  cargo, so a rival who spills a pure banks nothing. Losing a harvester is\n"
    "  normally bad — but when you are AHEAD, or when the unit you kill is\n"
    "  carrying pure, crashing / EMPing / jamming it is a good trade.\n"
    "  If you already know the exact pure cell (you saw it, then lost vision)\n"
    "  it is listed in ECHOES below — hot-drop that square instead of\n"
    "  re-searching a seam you have already solved. For the rest of the\n"
    "  geometry, play the CASE 1 / CASE 2 redsign doctrine above."
)


def _red_signs_section(
    agent_view: Mapping[str, Any],
    *,
    day: int,
    mine: Sequence[Cell],
    enemy: Sequence[Cell],
) -> List[str]:
    rows = [r for r in (agent_view.get("redsign") or []) if isinstance(r, Mapping)]
    if not rows:
        return []
    out = [
        "",
        "RED SIGNS — a pure-RED seam some house's probe or harvester has seen.",
        "  Public, anonymous, permanent, and SMEARED: the centre below is",
        "  deliberately off-centre noise, an area to race to, not the cell.",
        _PURE_ECONOMICS,
        "",
    ]
    for r in rows:
        center = _center_of(r)
        if center is None:
            continue
        owner = "YOURS" if r.get("mine") else "RIVAL"
        try:
            found = int(r.get("day") or 0)
        except (TypeError, ValueError):
            found = 0
        age = f"found day {found} (~{max(0, int(day) - found)}n ago)" if found else "found earlier"
        block = (
            f"  ~({center[0]},{center[1]})  {owner}  {age}\n"
            f"      {_access_note(center, mine, enemy)}"
        )
        strength = _strength_note(agent_view, r, day)
        if strength:
            block += f"\n      {strength}"
        out.append(block)
    out.append("")
    out.append(_DENIAL)
    return out


def _blue_signs_section(
    agent_view: Mapping[str, Any],
    *,
    mine: Sequence[Cell],
    enemy: Sequence[Cell],
) -> List[str]:
    rows = [r for r in (agent_view.get("blue_sign") or []) if isinstance(r, Mapping)]
    if not rows:
        return []
    out = [
        "",
        "BLUE SIGNS — fissile-blue pockets. Radiating since birth, known to",
        "  every house, and permanent (the sign does NOT fade once the pocket",
        "  is mined out, so an old sign may be an empty hole). Blue funds",
        "  weapons and repairs; it is NOT your primary score. Work a blue sign",
        "  with a SPARE harvester, or when your red chains are genuinely weak.",
        "",
    ]
    for r in rows[:_BLUE_SIGN_MAX_ROWS]:
        center = _center_of(r)
        if center is None:
            continue
        out.append(
            f"  ~({center[0]},{center[1]})  {_access_note(center, mine, enemy)}"
        )
    return out


def _echoes_section(agent_view: Mapping[str, Any], *, day: int) -> List[str]:
    """High-tier RED cells we saw and no longer see, freshest first."""
    live = _live_cells(agent_view)
    enemy = [row["at"] for row in _enemy_probe_cells(agent_view)]
    reach = probe_vision_radius()

    found: List[Tuple[int, int, Dict[str, Any]]] = []
    for row in ((agent_view.get("world") or {}).get("echo") or []):
        if not isinstance(row, Mapping):
            continue
        if str(row.get("tile") or "").upper() != "RED":
            continue
        try:
            x, y = int(row["x"]), int(row["y"])
            purity = int(row.get("purity") or 0)
        except (TypeError, KeyError, ValueError):
            continue
        if purity < _ECHO_MIN_PURITY or (x, y) in live:
            continue
        seen = row.get("last_seen_day")
        age = max(0, int(day) - int(seen)) if isinstance(seen, int) else -1
        found.append((-purity, age, {"at": (x, y), "purity": purity,
                                     "age": age, "seen": seen}))
    if not found:
        return []

    found.sort(key=lambda t: (t[1] if t[1] >= 0 else 99, t[0]))
    out = [
        "",
        "ECHOES — cells YOU saw and can no longer see. The coordinates are",
        "  EXACT; the contents are as of the night you last looked.",
        "",
    ]
    for _, _, e in found[:_ECHO_MAX_ROWS]:
        x, y = e["at"]
        tier = _tier(e["purity"])
        age = e["age"]
        when = (
            f"last seen day {e['seen']} ({age}n ago)" if age >= 0
            else "last seen: age unknown"
        )
        # Retrieval route: an echo touching the edge of a live disk can be
        # walked into from vision we already hold; otherwise it needs a probe.
        touching = any(_cheb((x, y), c) <= 1 for c in live)
        route = (
            "touching live vision — WALK IN from the disk edge, no probe needed"
            if touching else
            "outside vision — HOT DROP it (probe so this cell lands inside the "
            "new disk, then drop + walk + pick up the same night)"
        )
        risk = ""
        if any(_cheb((x, y), p) <= reach for p in enemy):
            risk = "\n      ! a rival probe disk covers this cell — assume they can see it too"
        out.append(f"  ({x},{y}) {tier}  {when}\n      {route}{risk}")

    out.append("")
    out.append(
        "  READING AGES: a high-tier echo "
        f"{_ECHO_HOT_NIGHTS}n old is close to a fact — go and take it.\n"
        f"  At {_ECHO_COLD_NIGHTS}n or older, or with a rival probe disk over "
        "it, assume it has been\n"
        "  mined and treat it as a lead, not as loot.\n"
        "  A PURE in echo is the best target on the board: unlike a SIGN you\n"
        "  know the EXACT square, so it needs no search — just a hot drop."
    )
    return out


def format_out_of_grid_block(
    agent_view: Mapping[str, Any], *, day: int,
) -> str:
    """Render OUT-OF-GRID KNOWLEDGE, or ``""`` when nothing is known."""
    mine = sorted(_my_probe_cells(agent_view))
    enemy = [row["at"] for row in _enemy_probe_cells(agent_view)]

    body: List[str] = []
    body += _red_signs_section(agent_view, day=day, mine=mine, enemy=enemy)
    body += _blue_signs_section(agent_view, mine=mine, enemy=enemy)
    body += _echoes_section(agent_view, day=day)
    if not body:
        return ""
    return "\n".join([_HEADER] + body) + "\n"
