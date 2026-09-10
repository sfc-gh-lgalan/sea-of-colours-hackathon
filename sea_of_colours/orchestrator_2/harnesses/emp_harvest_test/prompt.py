"""Prompt assembly for tabula_v11 — the COMPREHENSION restructure.

v10 keeps v8's three labelled worldview pillars and adds the comprehension
layer proven missing by the v8 audit:

  === SECTION 1 - THE GAME (how it works) ===
    RULES + DOCTRINE (core + state-triggered appendices, now including the
    COMPREHENSION note and the rewritten two-case redsign-poker book) + the
    STRATEGIST DIRECTIVE handoff (mover only, split mode).

  === SECTION 2 - THE BOARD NOW (what you see) ===
    YOUR STATE, WORLD VIEW, DROP-LEGAL, FOG+ECHO, ENEMY PROBES, EMP SCARS
    (new hazard), OPPONENT INTEL, OPPONENT WEAPONS + WEAPON GEOMETRY, wishlist,
    and the precomputed HINT menu.

  === SECTION 3 - WHAT HAPPENED LAST NIGHT (learn) ===
    The two-way WHAT HAPPENED digest (attacks TO you + denials BY you, with
    attribution + consequence — the keystone comprehension fix), the
    collision-aware LAST NIGHT block, the grounded REFLECT block (now forcing a
    consequence acknowledgment for probe/EMP/chaff loss, not just collisions),
    and MEMORY.

Battle-tested block formatters are imported verbatim from ``tabula_v7.prompt``
(pure functions over the agent view); v10 owns the digest-aware blocks and the
sectioned assembler. This is a true fork of the *feeding* layer that reuses
the *computation* layer.
"""

from __future__ import annotations

from typing import Any, List, Mapping, Sequence

from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test import (
    digest, doctrine, option_economics, out_of_grid, rules, scorch, world_view,
)
from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test._v7.orbit_wishlist import (
    Wishlist,
)
from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test._v7.probe_hints import (
    _redsign_centers,
    _enemy_probe_cells,
    _orbit_harvester_ids,
)
# Reuse v7's proven, pure block formatters + schemas verbatim. v11's menu rebuild
# retires the standalone HINT formatters (chain / probe / hot-drop / blue / the
# v10 tactics block): the OPTION MENU is now the single source of truth for every
# actionable play, carrying its own walk / yield / crush / risk. Only the pure
# BOARD-FACT formatters (state, visible red, drop-legal, fog, weapons) + the
# final-night supersede block remain imported. OPPONENT INTEL is a v11-local
# filtered variant (below) that no longer re-lists enemy probe launches.
from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test._v7.prompt import (
    format_state_block,
    format_drop_legal_block,
    format_fog_and_echo_block,
    format_opponent_weapons_block,
    format_supersede_hints_block,
    format_setup_night_advisory,
    format_reflect_block as _v7_reflect_block,
    format_last_night_block as _v7_last_night_block,
    _my_jam_events,
    _THINKER_SCHEMA,
)

_SECTION_1 = "=== SECTION 1 - THE GAME (how it works) ==="
_SECTION_2 = "=== SECTION 2 - THE BOARD NOW (what you can see right now) ==="
_SECTION_3 = "=== SECTION 3 - WHAT HAPPENED LAST NIGHT (learn from it) ==="

# v10 agency addendum to the (shared) v7 thinker schema: two extra, OPTIONAL
# fields the mover-side resolver consumes. Kept here (not in the frozen v7
# schema text) so v7/v8 thinkers are unchanged.
_V10_THINK_TASK = """\
=== YOUR TASK (THINK — reasoning only, this is the THINK pass) ===
You are the STRATEGIST for tonight. THINK the night through, then STOP:
  * Reflect on last night — what missed and WHY (name the real cause; do not
    over-generalise a collision on a DECOY cell into "always drop offset").
  * Read the REDSIGN: is it YOURS (CASE 1, mine=true) or a rival's (CASE 2)?
    Where is the pure(255), what is the seam worth, who is racing it?
  * Choose which OPTION MENU IDs you will select and in what execution order,
    plus any targets to prioritise / cells to avoid. When a pure(255) is
    reachable AND it is YOUR redsign, plan to DROP ON the pure (the landing cell
    is auto-harvested — that is free parcel #1) and bank it on a SHORT chain;
    only drop OFFSET when the cell is genuinely CONTESTED by rivals THIS night.
Output ONLY your analysis as CONCISE prose — no JSON, no move list, no headings.
Keep it under ~200 words. Reason, then stop; the next pass turns this into moves.
"""


_V10_PLAN_TASK = """\
=== YOUR TASK (COMMIT — decision only, this is the PLAN pass) ===
Turn the analysis above into a decision. Do NOT re-reason at length — you already
thought it through. Emit ONLY the decision fields, faithful to your analysis.
"""


# v10 MOVER output contract — MOVES ONLY. The thinker already reflected, planned,
# and reasoned (that prose is captured separately), so the mover must NOT rewrite
# any of it. Emitting only ``moves`` is what keeps the mover fast (the A4 fix:
# the shared v7 schema forced a wall of reflection/plan/rationale prose that cost
# 25-99s). Keep this in lock-step with tabula_v11/chat_schema._V10_MOVES_SCHEMA.
_V10_ACTION_SCHEMA = """\
OUTPUT CONTRACT — output ONE JSON object, MOVES ONLY. Start with the open-brace
character; NO prose before or after the JSON. Do NOT write any reflection, plan,
rationale, predicted-outcome, or memory fields — the reasoning is already done;
your ONLY job is to package it into moves. Fields:

  moves: list of wire-format actions, in execution order. Each is one of:
    {"a":"drop",   "unit":"harvester_p1", "at":[x,y]}
    {"a":"step",   "unit":"harvester_p1", "to":[x,y]}
    {"a":"pickup", "unit":"harvester_p1"}
    {"a":"probe",  "at":[x,y]}
  Chain grammar: drop -> step* -> pickup PER harvester. Probes anywhere.
  step "to" MUST be Manhattan-1 from the harvester's CURRENT cell: exactly ONE
  of (x+1,y),(x-1,y),(x,y+1),(x,y-1). NO diagonals, NO multi-cell jumps — list
  each intermediate cell as its own step. An illegal step is silently canceled
  and cascades into a crashed harvester at dawn.

  note: OPTIONAL one line — leave it out unless you had to CUT a low-priority
  plan item to fit the night; if so, name what you cut. Nothing else.
"""


_V10_THINKER_ADDENDUM = """\
=== v10 AGENCY FIELDS (put these RIGHT AFTER "posture", before "reasoning") ===
  "plan": ["ID", ...] — the OPTION MENU IDs you SELECT, in EXECUTION ORDER
      (e.g. ["SMASH_GRAB"] for CASE 1, or ["BLIND_GRAB","UNBEATEN_FLANK",
      "WALK_IN"] for CASE 2; add HDn/PRn/CHn/SSn for spare units). The geometry
      is pre-filled — you are choosing and ordering, not inventing coordinates.
      This is the MOST IMPORTANT field: it is what the mover executes. Emit it
      EARLY (right after posture) so it is never lost — an empty "plan" on a
      redsign night means the mover has nothing to execute and will misfire.
  "situational": {"mine": bool, "players": int, "chaff": bool, "emp": bool}
      — your read of the SITUATIONAL FACTS that justifies the plan. Fill it
      whenever a redsign is live.
  "chaff_react": bool — set TRUE when you EXPECT a chaff/EMP jam tonight (a
      rival with weapons, a scar near your seam, or you are ahead and want to
      lock in). The compiler now HONOURS this: it caps EVERY chain so each
      harvester banks and LIFTS inside the safe window instead of riding a long
      walk into the jam. Use it — it is no longer just advisory.
  "avoid": [[x,y], ...] — up to 3 cells you want kept OUT of tonight's routing
      (a mirror collision point, a cell you believe a rival already stripped).
      This steers cells your picks do NOT already name. It CANNOT cancel an
      option you selected: if you pick a play and also avoid its own drop cell,
      the pick wins and you will be told you contradicted yourself. To not do
      something, leave it out of "plan" — do not pick it and avoid it.
      NOT for the area you are attacking. Listing the contested seam you are
      about to hit is the single most common way a good attack gets mangled.
  "intent": ONE or TWO plain-language sentences — your INTENTION for tonight:
      what you are trying to achieve and WHY (name the seam/goal, e.g. "Race the
      north pure(255) before p2 with a smash-grab, bank short to dodge chaff").
      This is saved to your STRATEGY JOURNAL and shown back to you tomorrow to
      reflect against, so make it concrete and honest — it is your own memory.
  "reflection": ONE or TWO sentences reflecting on LAST NIGHT (read the STRATEGY
      JOURNAL + LAST NIGHT block): did what HAPPENED match the intent you set? If
      not, name the real cause (a FAILED move, a collision, a jam, a misread
      cell) — never call lost cargo "held". Leave empty ONLY on night 1.
  Leave "plan" empty (and omit "situational") only when NOTHING on the menu
  fits (rare); the mover then works from posture/targets as before.
"""


def format_rack_block(agent_view: Mapping[str, Any]) -> str:
    """What ordnance this seat owns tonight, and what one charge does.

    Rung 1 of the weapons ladder. V12 carries the same numbers on the
    view — ``orbit.weapon_stock`` has always been there — and prints them
    nowhere, so its night phase plans as though the rack were empty. The
    fix is this small: say it out loud.

    Silent on an empty rack. A block reading "EMP 0, chaff 0" is noise on
    most nights and, worse, it invites the model to reason about a
    weapon it cannot fire.
    """
    rack = scorch.stock(agent_view)
    if rack["emp"] <= 0 and rack["chaff"] <= 0:
        return ""
    radius, missiles, hours = scorch.specs(agent_view)
    lines = ["YOUR RACK (bought in orbit — spending it is a NIGHT move):"]
    if rack["emp"] > 0:
        lines.append(
            f"  EMP x{rack['emp']} — one charge fires {missiles} missiles at "
            f"once; each darkens a radius-{radius} diamond "
            f"({2 * radius * (radius + 1) + 1} cells) for {hours}h. Probes in "
            "it are DESTROYED; harvesters in it are disabled hour by hour. "
            "FRIENDLY FIRE IS ON."
        )
    if rack["chaff"] > 0:
        lines.append(f"  chaff x{rack['chaff']}")
    if rack["emp"] > 0:
        lines.append(
            "  -> The salvo costs ONE of your 21 hour-slots: you fire OR you "
            "move that hour, never both. Pick EMP_SCORCH / SCORCH_REDSIGN off "
            "the menu to spend one; a charge left in the rack scores nothing."
        )
    return "\n".join(lines) + "\n"


# ── Board blocks carried from v8 ───────────────────────────────────────
def format_enemy_probes_block(
    agent_view: Mapping[str, Any], *, day: int,
) -> str:
    """First-class ENEMY PROBES block (from v8 §4c).

    An enemy probe launch is PUBLIC (§3.15). Promotes these to a standing
    block on EVERY night so the agent can route around them, stage behind
    them, or supersede them, and so it understands a rival can hot-drop into
    these disks (the collision risk on a shared cell).
    """
    enemy = _enemy_probe_cells(agent_view)
    if not enemy:
        return ""
    lines = [
        "ENEMY PROBES (public launches — a rival SEES and can DROP into each "
        "disk; a shared drop cell risks a mutual-kill collision):"
    ]
    for row in enemy[:6]:
        at = row.get("at")
        if not (isinstance(at, (list, tuple)) and len(at) == 2):
            continue
        ds = row.get("day_seen")
        src = str(row.get("source") or "seen")
        age_txt = ""
        try:
            if ds is not None and int(ds) > 0:
                age = max(0, int(day) - int(ds))
                age_txt = f" (day {int(ds)}, ~{age}n old)"
        except (TypeError, ValueError):
            age_txt = ""
        lines.append(f"  ({int(at[0])},{int(at[1])}){age_txt} via {src}")
    lines.append(
        "  -> Route your drops/steps around these disks. Staging a harvester "
        "behind a FRESH probe of your own keeps you off their watched cells. "
        "Landing YOUR probe on one of these cells SUPERSEDES it (destroys "
        "their vision; yours survives) — a spare-probe denial you can play ANY "
        "night (see the SUPERSEDES menu group); if you are TRAILING, blind the "
        "leader's freshest probe first, and skip any about to expire."
    )
    return "\n".join(lines) + "\n"


def format_opponent_block(agent_view: Mapping[str, Any]) -> str:
    """v11 OPPONENT INTEL — v7's block MINUS the enemy_probe_launch events.

    Enemy probe launches are already surfaced as the first-class ENEMY PROBES
    block (route-around / supersede targets) and annotated ``! WATCHED`` inside
    DROP-LEGAL, so re-listing them here was a THIRD copy of the same cells (the
    day-2 card showed (11,14)/(20,14) three times). We drop just those events and
    keep everything else: any other revealed events, persistent echoes, and the
    per-seat banked/activity totals (the only rival scoreboard the night sees).
    """
    ci = agent_view.get("competitor_intel") or {}
    new_events = [
        ev for ev in (ci.get("new_this_day") or [])
        if isinstance(ev, Mapping)
        and (ev.get("kind") or ev.get("type")) != "enemy_probe_launch"
    ]
    persistent = list(ci.get("persistent_echoes") or [])
    opponents = ((agent_view.get("station_intel") or {}).get("opponents") or {})
    if not new_events and not persistent and not opponents:
        return ""

    lines = ["OPPONENT INTEL (what they revealed to you — you don't see their queue):"]
    if new_events:
        lines.append(f"  new_this_day ({len(new_events)} events):")
        for ev in new_events[:5]:
            kind = ev.get("kind") or ev.get("type") or "event"
            at = ev.get("at") or ev.get("pos") or ""
            hour = ev.get("hour")
            hour_s = f" hour={hour}" if hour is not None else ""
            lines.append(f"    - {kind}{hour_s} at={at}")
    if persistent:
        lines.append(
            f"  persistent_echoes ({len(persistent)}): still hearing prior activity"
        )
    if isinstance(opponents, Mapping) and opponents:
        for seat, info in list(opponents.items())[:2]:
            if not isinstance(info, Mapping):
                continue
            blue = (info.get("blue") or {}).get("total", "?")
            green = (info.get("green") or {}).get("total", "?")
            act = info.get("activity") or {}
            probes = act.get("probes", "?")
            dropped = act.get("dropped", "?")
            lines.append(
                f"  {seat}: blue_banked={blue} green_banked={green} "
                f"probes_launched={probes} harvesters_dropped={dropped}"
            )
    return "\n".join(lines) + "\n"


def _any_could_hold(estimates: "Mapping[str, Any] | None", kind: str) -> bool:
    """Could ANY opponent be holding a ``kind``?

    v1.38 — the one gate every weapon warning in this module asks, so
    they cannot drift apart. It defers to
    ``WeaponEstimate.could_hold``, which is true only when some rack
    that fits the seat's published total contains that kind. The minimum
    spend is therefore enforced by the decode and not by a threshold
    repeated here: no chaff warning under the 300 blue a chaff costs, no
    EMP warning under 200, no SNAP warning under 100.

    ``getattr`` because a fork may still be storing estimates pickled by
    a pre-v1.38 harness, which have no ``could_hold``; those fall back
    to the named maxima and simply never report SNAP.
    """
    for est in (estimates or {}).values():
        probe = getattr(est, "could_hold", None)
        if callable(probe):
            if probe(kind):
                return True
            continue
        stem = {"emp": "emps", "chaff": "chaff", "snap": "snap"}.get(kind, kind)
        if int(getattr(est, f"{stem}_max", 0) or 0) > 0:
            return True
    return False


def format_weapon_geometry_block(
    estimates: "Mapping[str, Any] | None",
) -> str:
    """WEAPON GEOMETRY constants (from v8 §4d).

    Rendered when some opponent could be holding something, so the agent
    reasons about a strike with a KNOWN blast radius on a known beacon —
    not a distance-from-a-cluster gamble.

    v1.38 — each weapon's paragraph is gated on that weapon being
    possible for someone, rather than the whole block being gated on EMP
    or chaff. That fixes two things at once: a seat holding only SNAP no
    longer produces an empty block, and a seat that cannot afford a
    chaff no longer gets chaff geometry recited at it.
    """
    if not estimates:
        return ""
    could_emp = _any_could_hold(estimates, "emp")
    could_chaff = _any_could_hold(estimates, "chaff")
    could_snap = _any_could_hold(estimates, "snap")
    if not (could_emp or could_chaff or could_snap):
        return ""
    try:
        from sea_of_colours.game.weapons import (
            EMP_RADIUS,
            EMP_MISSILES_PER_LAUNCH,
            EMP_CLOUD_HOURS,
            CHAFF_DURATION_HOURS,
            SNAP_CLOUD_HOURS,
            SNAP_MISSILES_PER_LAUNCH,
        )
    except Exception:  # pragma: no cover - defensive
        EMP_RADIUS, EMP_MISSILES_PER_LAUNCH = 2, 3
        EMP_CLOUD_HOURS, CHAFF_DURATION_HOURS = 8, 3
        SNAP_CLOUD_HOURS, SNAP_MISSILES_PER_LAUNCH = 1, 1
    out = "WEAPON GEOMETRY (reason with the exact numbers, not vibes):\n"
    if could_emp:
        out += (
            f"  EMP: an orbital salvo of {EMP_MISSILES_PER_LAUNCH} missiles; "
            f"each forms a Manhattan-radius-{EMP_RADIUS} cloud (~13 cells) "
            f"that lasts {EMP_CLOUD_HOURS}h. Inside it: probes DESTROYED, "
            f"harvesters DISABLED (they keep their haul — only a dawn crash "
            f"kills them). It is aimed at a CELL (usually your latest probe "
            f"or a pure beacon), reachable anywhere on the map.\n"
        )
    if could_snap:
        out += (
            f"  SNAP: {SNAP_MISSILES_PER_LAUNCH} missile at exactly ONE cell, "
            f"cloud {SNAP_CLOUD_HOURS}h. It resolves BEFORE the hour's vision "
            f"snapshot and before every drop, step and pickup on that square, "
            f"which is the whole weapon: a probe there is destroyed BEFORE it "
            f"sees, so a drop relying on that sight is refused. A harvester "
            f"standing there or stepping in is DAMAGED and harvests nothing; "
            f"a landing into it is REFUSED (stays in orbit, damaged, outing "
            f"unspent). One cell only — spreading across distinct cells beats "
            f"it, re-timing does not.\n"
        )
    if could_chaff:
        out += (
            f"  CHAFF: cancels every OTHER seat's actions for "
            f"{CHAFF_DURATION_HOURS} consecutive hours. A pickup inside that "
            f"window is lost -> dawn-crash risk. It has no location — it is a "
            f"seat-wide jam.\n"
        )
    return out


def _harvester_worth_line(*, day: int, day_cap: int, vault_score: int) -> str:
    """R2.10 — what a harvester is WORTH tonight, in points, out loud.

    Harvesters score nothing. Their whole value is the harvests they have left
    to make, so it falls to zero on the last night — and yet the agent kept
    holding one back to keep it safe. On the final night of `V12_HEUR3_GO_s56`
    it left a unit in orbit rather than risk it on a contested seam, protecting
    an asset that could not be spent again and forfeiting the only points still
    on the board.

    Priced off the seat's own record: mean nightly bank x nights that remain
    after tonight. Stated as a fact, not an instruction — the risk judgement
    stays the agent's, it just gets to make it with the number in hand.
    """
    nights_after = max(0, int(day_cap) - int(day))
    if nights_after <= 0:
        return (
            "\n  WHAT A HARVESTER IS WORTH TONIGHT: NOTHING. This is the FINAL "
            "night — a unit you keep safe is a unit that never banks again, and "
            "harvesters score zero points themselves. There is no such thing as "
            "a losing risk with an idle unit: the only way it can cost you is by "
            "staying in orbit. Deploy EVERY alive harvester, on the most valuable "
            "ground you can reach, however contested."
        )
    nights_played = max(1, int(day) - 1)
    mean = float(vault_score) / nights_played if vault_score > 0 else 0.0
    if mean <= 0:
        return (
            f"\n  WHAT A HARVESTER IS WORTH TONIGHT: only the harvests it has "
            f"left — {nights_after} more night(s) after this one. It scores no "
            "points of its own; losing one costs you those future nights and "
            "nothing else."
        )
    return (
        f"\n  WHAT A HARVESTER IS WORTH TONIGHT: ~{int(round(mean * nights_after))} "
        f"points — your mean nightly bank (~{int(round(mean))}) across the "
        f"{nights_after} night(s) that remain after this one. That is the WHOLE "
        "cost of losing one; harvesters score nothing themselves. Compare it "
        "against what tonight's ground is worth before you hold a unit back, and "
        "note that an idle unit banks zero, which is the one certain loss."
    )


def blue_vault_grade(agent_view: Mapping[str, Any]) -> str:
    """The seat's blue vault band: ``none | low | medium | high`` (or "").

    R2.3 — the engine grades summed purity as ``none / low / medium / high``
    (``session.STATION_PURITY_BANDS``). The shared wishlist tests for ``"empty"``,
    which is not one of them, so an empty vault fell through every blue check
    while a vault holding 80 tripped all of them. Everything in v12 that asks
    "are we short of blue?" comes through here.
    """
    return str(
        ((agent_view.get("station_intel") or {}).get("self") or {})
        .get("blue", {}).get("grade") or ""
    ).lower()


def blue_vault_is_short(agent_view: Mapping[str, Any]) -> bool:
    """A vault the seat should top up when the night allows it."""
    return blue_vault_grade(agent_view) in ("none", "low")


def blue_is_requested(agent_view: Mapping[str, Any]) -> bool:
    """Should tonight's menu force-surface a blue grab?

    The orbital ask, re-derived on the correct bands: the vault is genuinely
    short, there is a spare unit to send (2+ harvesters in orbit) and there is
    blue on the board to send it to. Note what this does NOT do — it does not
    outrank anything. It only puts a ``BL*`` on the menu; whether the unit is
    better spent on red is the agent's call, and doctrine says red wins ties.
    """
    if not blue_vault_is_short(agent_view):
        return False
    if not (agent_view.get("blue_tiles") or []):
        return False
    return len(_orbit_harvester_ids(agent_view)) >= 2


def _blue_condition_line(
    agent_view: Mapping[str, Any], option_menu_block: str,
) -> str:
    """R2.3 — the blue request, re-derived and reframed for v12.

    Three things were wrong with the inherited ``NEED BLUE`` directive. It tested
    the vault against a band called ``"empty"`` that the engine never emits (the
    bands are ``none / low / medium / high``), so an empty vault stayed quiet
    while a vault holding 80 shouted; it fired whether or not the menu had any
    blue to take; and it arrived as an imperative in a block headed "act on these
    tonight", which put it level with a live redsign.

    Blue is a background condition for a quiet night, so it is phrased as one and
    only appears when the vault is genuinely short AND there is a real ``BL*``
    option to act on.
    """
    grade = blue_vault_grade(agent_view)
    if grade not in ("none", "low"):
        return ""
    if "HIGH-YIELD BLUE GRABS" not in (option_menu_block or ""):
        return ""  # nothing on the menu to act on — saying it is just noise
    line = (
        f"BLUE VAULT {grade.upper()} — if a harvester would otherwise idle or "
        "run low-yield red, a BL* grab tops the vault up. This is a background "
        "condition, NOT a call to action"
    )
    if _has_live_redsign(agent_view):
        line += ": a redsign is live tonight, and that outranks blue outright"
    return line + "."


def _has_live_redsign(agent_view: Mapping[str, Any]) -> bool:
    return any(
        isinstance(r, Mapping) for r in (agent_view.get("redsign") or [])
    )


def format_orbit_directives_block(
    wishlist: "Wishlist | None",
    agent_view: Optional[Mapping[str, Any]] = None,
    option_menu_block: str = "",
) -> str:
    """v10 ORBIT DIRECTIVES block — crisp imperatives from the orbit turn.

    Renders ``Wishlist.directives`` (need_blue / vault-nearly-full / conserve),
    the explicit orbit->night channel. Empty when the orbit turn had no
    directive for tonight. The shared compiler's blue line is dropped and
    re-derived here (R2.3); the rest passes through untouched.
    """
    directives = [
        d for d in (getattr(wishlist, "directives", None) or [])
        if not str(d).strip().upper().startswith("NEED BLUE")
    ]
    blue = _blue_condition_line(agent_view or {}, option_menu_block)
    if not directives and not blue:
        return ""
    lines = ["ORBIT DIRECTIVES (act on these tonight):"]
    for d in directives[:5]:
        lines.append(f"  - {d}")
    if blue:
        lines.append(f"  - {blue}")
    return "\n".join(lines) + "\n"


def format_hint_tactics_block(
    hot_drop_hints: Sequence[Mapping[str, Any]],
    probe_hints: Sequence[Mapping[str, Any]],
) -> str:
    """v10 TACTICAL OPTIONS block — surface the ranked/contested hint metadata.

    The shared compilers now tag each hint ``contested`` and attach ranked
    seam OFFSETS (``alt_drops``) + a ``supersede`` option. This block turns
    that into a short menu so the agent makes contested placement a deliberate
    choice (offset onto the seam / blind the finder) instead of blindly diving
    the single advertised cell. Empty when nothing is contested.
    """
    def _xy(v: Any) -> "str | None":
        if isinstance(v, (list, tuple)) and len(v) == 2:
            return f"({int(v[0])},{int(v[1])})"
        return None

    lines: List[str] = []
    for h in hot_drop_hints or []:
        if not isinstance(h, Mapping) or not h.get("contested"):
            continue
        sig = str(h.get("signal_type") or "signal")

        # CASE 1 — this redsign is OURS: we know the pure cell, move first.
        if h.get("mine") is True:
            drop_s = _xy(h.get("drop_at")) or "the pure"
            lines.append(
                f"  YOUR {sig} (CASE 1) — you discovered this seam, so you know "
                f"where the pure is. SMASH-AND-GRAB: drop on/adjacent {drop_s} "
                f"wave 1 before rivals arrive."
            )
            continue

        # CASE 2 — a rival's / unclaimed contested beacon. Each seat is assigned
        # a DIFFERENT seam offset so we fan out instead of stacking.
        assigned = _xy(h.get("seat_offset"))
        if assigned:
            parts = [
                f"  CONTESTED {sig} (CASE 2) — everyone races the advertised "
                f"cell and COLLIDES (0 banked). YOUR ASSIGNED approach is "
                f"{assigned} (rivals get different seam cells) — drop THERE, "
                f"not on the beacon."
            ]
        else:
            drop_s = _xy(h.get("drop_at")) or "?"
            parts = [
                f"  CONTESTED {sig} (CASE 2) at {drop_s} — everyone races this "
                f"cell; a shared drop COLLIDES (0 banked)."
            ]
        alts = [a for a in (h.get("alt_drops") or [])
                if isinstance(a, (list, tuple)) and len(a) == 2]
        # Show the fallback seam cells AFTER the assigned lead (already rotated).
        fallback = ", ".join(f"({int(a[0])},{int(a[1])})" for a in alts[1:]) \
            if assigned else ", ".join(f"({int(a[0])},{int(a[1])})" for a in alts)
        if fallback:
            parts.append(f"    -> other seam cells if blocked: {fallback}")
        sup = h.get("supersede")
        if isinstance(sup, (list, tuple)) and len(sup) == 2:
            parts.append(
                f"    -> or SUPERSEDE the finder's probe at "
                f"({int(sup[0])},{int(sup[1])}) to blind them first"
            )
        lines.extend(parts)

    contested_probes = [
        h for h in (probe_hints or [])
        if isinstance(h, Mapping) and h.get("contested")
    ]
    for h in contested_probes:
        at = h.get("at")
        at_s = (
            f"({int(at[0])},{int(at[1])})"
            if isinstance(at, (list, tuple)) and len(at) == 2 else "?"
        )
        lines.append(
            f"  CONTESTED probe placement {at_s} — near a public beacon or an "
            f"enemy probe; expect company, plan redundancy."
        )

    if not lines:
        return ""
    return "TACTICAL OPTIONS (contested cells — choose deliberately):\n" + \
        "\n".join(lines) + "\n"


def format_situational_facts_block(
    agent_view: Mapping[str, Any],
    player_count: int,
    opponent_weapon_estimates: "Mapping[str, Any] | None",
) -> str:
    """v10 SITUATIONAL FACTS — the ground truth the thinker must reason over.

    Surfaces exactly the dials the thinker echoes back in ``situational``
    (mine? / players / chaff / emp / snap) so its structured read is
    GROUNDED, not guessed. ``mine`` is engine truth; the weapon dials fuse
    "seen last night" (recap) with "could be in stock" (the public
    arsenal). Rendered only in the thinker pass. Always non-empty (it
    anchors the decision even off-seam).

    Adding a dial here is a four-file edit and all four are load-bearing:
    this line, the ``situational`` schema in ``chat_schema`` (strict mode
    forbids a key it does not declare), ``_SITUATIONAL_KEYS`` in
    ``directive`` (which drops anything unlisted), and the field list in
    this module's header docstring, which is what the model actually
    reads. ``snap`` went in at v1.38.
    """
    any_mine, any_not = _redsign_ownership(agent_view)
    if any_mine and any_not:
        mine_s = "mixed (you own at least one beacon, a rival owns another)"
    elif any_mine:
        mine_s = "true (a live REDSIGN is YOURS -> CASE 1)"
    elif any_not:
        mine_s = "false (the live REDSIGN is a rival's -> CASE 2)"
    else:
        mine_s = "n/a (no live redsign)"

    ln = agent_view.get("last_night") or {}
    chaff_seen = any(
        "chaff" in str(a.get("type") or "").lower()
        for a in (ln.get("incoming_attacks") or [])
        if isinstance(a, Mapping)
    )
    emp_seen = bool(ln.get("emp_scars"))
    # v1.38 — a SNAP that hit you arrives as a victim-private
    # ``snap_hit``; there is no scar list to read because the cloud
    # lives one hour and leaves nothing to age.
    snap_seen = any(
        str(a.get("type") or "") in ("snap", "snap_hit")
        for a in (ln.get("combat_events") or [])
        if isinstance(a, Mapping)
    )
    est_emp = _any_could_hold(opponent_weapon_estimates, "emp")
    est_chaff = _any_could_hold(opponent_weapon_estimates, "chaff")
    est_snap = _any_could_hold(opponent_weapon_estimates, "snap")

    def _w(seen: bool, est: bool) -> str:
        if seen:
            return "YES (hit you last night)"
        if est:
            # v1.34 — no longer a guess. The engine broadcasts every
            # seat's weaponised blue (§4.9.8), so this is a fact about
            # what a rival is carrying, not a suspicion.
            return "YES (a rival is holding stock — public, see ARSENALS)"
        return "none observed"

    players_s = str(player_count) if player_count else "unknown"
    return (
        "SITUATIONAL FACTS (reason over these; echo them back in "
        "\"situational\"):\n"
        f"  mine: {mine_s}\n"
        f"  players: {players_s}\n"
        f"  chaff: {_w(chaff_seen, est_chaff)}\n"
        f"  emp: {_w(emp_seen, est_emp)}\n"
        f"  snap: {_w(snap_seen, est_snap)}\n"
    )


def format_last_night_block(agent_view: Mapping[str, Any]) -> str:
    """v10 LAST NIGHT block — v7's engine record PLUS the collision channel.

    The collision lines are the keystone fix: a simultaneous-drop pile-up
    banks ZERO and shows up in no other channel. Sourced from the fog-safe
    ``last_night.my_collisions``. (The richer two-way attack narrative lives
    in the WHAT HAPPENED digest block above this.)
    """
    base = _v7_last_night_block(agent_view)
    ln = agent_view.get("last_night") or {}
    collisions = ln.get("my_collisions") or []
    if not collisions:
        return base

    lines: List[str] = []
    if not base.strip():
        lines.append(
            f"LAST NIGHT (day {ln.get('day_ended', '?')} — engine record):"
        )
    else:
        lines.append(base.rstrip("\n"))
    lines.append(f"  my_collisions ({len(collisions)}) — YOU banked ZERO here:")
    for c in collisions[:4]:
        if not isinstance(c, Mapping):
            continue
        at = c.get("at") or [None, None]
        others = c.get("others") or []
        who = f" with {others}" if others else ""
        at_s = (
            f"[{at[0]},{at[1]}]" if at and at[0] is not None else "a shared cell"
        )
        lines.append(
            f"    - harvester COLLIDED at {at_s}{who} — returned orbital "
            f"damaged, 0 cargo (cargo LOST, NOT held in hoard). The advertised "
            f"cell was a mutual-kill zone."
        )
    return "\n".join(lines) + "\n"


def format_reflect_block(
    agent_view: Mapping[str, Any],
    prior_day_entry: "Mapping[str, Any] | None",
    day: int,
) -> str:
    """v10 grounded REFLECT — v7's block PLUS forced consequence acknowledgment.

    v7's reflect frames a 0 shipped-change with a healthy harvest as "held, not
    lost". On a night with a COLLISION or an incoming ATTACK (probe kill / EMP /
    chaff) that framing is wrong — cargo/vision was genuinely LOST. So when such
    an event is on record we append a hard "this was a LOSS, name the cause"
    line the model must acknowledge, defeating the confabulation seen in the v8
    audit ("thin seam" / "held not lost").
    """
    base = _v7_reflect_block(agent_view, prior_day_entry, day)
    ln = agent_view.get("last_night") or {}
    collisions = ln.get("my_collisions") or []
    incoming = ln.get("incoming_attacks") or []
    banked = ln.get("my_parcels_banked") or []
    if not collisions and not incoming and not banked:
        return base

    prior_day = int(day) - 1
    lines: List[str] = []
    if not base.strip():
        lines.append(
            f"REFLECT ON LAST NIGHT (day {prior_day}) — engine ground truth:"
        )
    else:
        lines.append(base.rstrip("\n"))

    # ANCHOR (anti-confabulation): a cell you HARVESTED last night is now SPENT,
    # so it shows GREEN / '! AVOID' in TODAY's drop-legal zones. The audit caught
    # the seat reading that current-green state and RETROJECTING it onto last
    # night's drop ("(15,4) hit synthetic-green, crashed the chain") when the
    # record plainly says it dropped there and auto-harvested RED. Anchor the
    # reflection to my_parcels_banked so it stops inverting a success into a crash.
    if banked:
        cells = ", ".join(
            f"({b.get('from', [None, None])[0]},{b.get('from', [None, None])[1]})"
            for b in banked[:6] if isinstance(b, Mapping)
        )
        lines.append(
            "  ANCHOR TO THE RECORD: my_parcels_banked lists cells you HARVESTED "
            f"last night ({cells}). You banked those — you did NOT crash or hit "
            "green there. A cell you harvest becomes SPENT and shows GREEN / "
            "'! AVOID' in TODAY's drop-legal zones: that is the SUCCESS signature "
            "of a completed harvest, NOT evidence of a green crash. Never infer a "
            "past crash from a cell being green today — trust the LAST NIGHT "
            "engine record over the current map state."
        )

    for c in collisions[:3]:
        if not isinstance(c, Mapping):
            continue
        at = c.get("at") or [None, None]
        others = c.get("others") or []
        at_s = f"[{at[0]},{at[1]}]" if at and at[0] is not None else "the beacon"
        who = f" with {others}" if others else " with rivals"
        lines.append(
            f"  COLLISION LOSS: a harvester collided at {at_s}{who} and banked "
            f"ZERO — this cargo is LOST, not held. Set gap_reason to the "
            f"collision (NOT 'held in hoard'). The trap was diving a CONTESTED "
            f"advertised cell blind at the same hour as rivals — the fix is to "
            f"fan out / stagger your wave timing, NOT to abandon the pure. On "
            f"YOUR redsign still SECURE the pure (that is what SMASH_GRAB does — "
            f"drop on the seam core); only offset OFF a cell rivals are "
            f"contesting THIS night."
        )
    for a in incoming[:3]:
        if not isinstance(a, Mapping):
            continue
        atype = str(a.get("type", "")).replace("_", " ")
        by = ", ".join(str(b) for b in (a.get("by") or [])) or "a rival"
        cons = str(a.get("consequence") or "").strip()
        lines.append(
            f"  LOSS TO ACKNOWLEDGE ({atype} by {by}): {cons} Set gap_reason "
            f"to THIS cause — do not confabulate a 'thin seam' or call lost "
            f"cargo 'held'."
        )
    return "\n".join(lines) + "\n"


# ── Doctrine assembly (v7 gating + v10 additions) ───────────────────────
def _alive_harvester_count(agent_view: Mapping[str, Any]) -> int:
    """Count the reading seat's alive harvesters (orbit + surface)."""
    entities = (agent_view.get("entities") or {}).get("mine") or []
    return sum(
        1 for e in entities
        if isinstance(e, Mapping) and str(e.get("type") or "") == "harvester"
    )


def _probe_stock(agent_view: Mapping[str, Any]) -> int:
    """Probes available to LAUNCH from orbit this night (0 if none)."""
    try:
        return int((agent_view.get("orbit") or {}).get("probe_stock") or 0)
    except (TypeError, ValueError):
        return 0


def _redsign_ownership(agent_view: Mapping[str, Any]) -> "tuple[bool, bool]":
    """Return (any_mine, any_not_mine) over the live redsign regions."""
    any_mine = any_not = False
    for r in agent_view.get("redsign") or []:
        if not isinstance(r, Mapping):
            continue
        if r.get("mine"):
            any_mine = True
        else:
            any_not = True
    return any_mine, any_not


def _tempo_lines(agent_view: Mapping[str, Any], day: int) -> "list[str]":
    """Per-seat minimum-time-to-pure for every live beacon (v12 fix 3.3).

    OBS-44 item 2. The card already prints probe disks; this states what they
    MEAN for the only race that matters. Rendered per beacon because the answer
    differs per pure — we can hold H1 on ours and be at H2 on theirs.

    The floor sentence is not decoration. The obvious misreading of `p1: H1 ·
    you: H2` is that the seam is lost and should be skipped, which cedes a pure
    for free; rivals fumble, and a heuristic seat may not contest at all.
    """
    out: "list[str]" = []
    for r in agent_view.get("redsign") or []:
        if not isinstance(r, Mapping):
            continue
        cell = option_economics._as_cell(
            r.get("pure_cell") or r.get("center") or r.get("at")
        )
        if cell is None:
            continue
        seats = option_economics.time_to_pure(agent_view, cell, day=day)
        if len(seats) < 2:
            continue
        whose = "yours" if r.get("mine") else "rival's"
        order = " · ".join(f"{k}: {v}" for k, v in seats.items())
        out.append(f"  ({cell[0]},{cell[1]}) [{whose}] — {order}")
    if not out:
        return out
    return [
        "EARLIEST HOUR EACH SEAT COULD LAND ON THE PURE (from probe disks at "
        "night start):",
        *out,
        "  H1 = a live disk covers it now, so that seat can drop on it in the "
        "first hour. H2 = they must launch a probe first.",
        "  THIS SIZES YOUR COMMITMENT — IT NEVER CANCELS IT. Reading 'they hold "
        "H1, we do not' as a reason to skip the seam cedes a pure for FREE, and "
        "that is never right: trying costs one outing and a probe, and rivals "
        "DO fumble. ALWAYS commit at least one harvester to a live redsign at "
        "the earliest legal hour. Tempo decides only the SECOND unit and the "
        "probes — whether to add the value tail, take mass elsewhere, or spend "
        "the shots on frontier.",
    ]


def _assemble_doctrine(
    *,
    agent_view: Mapping[str, Any],
    day: int,
    day_cap: int,
    hot_drop_hints: Sequence[Mapping[str, Any]],
    wishlist: Wishlist,
    opponent_weapon_estimates: "Mapping[str, Any] | None",
    supersede_hints: Sequence[Mapping[str, Any]],
    is_setup_night: bool,
    mover_has_recipe: bool = False,
    vault_score: int = 0,
) -> str:
    """Assemble core doctrine + state-triggered appendices (v7 gating + v10).

    When ``mover_has_recipe`` the reading pass is the MOVER holding the thinker's
    committed EXECUTE-THIS-PLAN recipe: the planning doctrines that tempt it to
    ADD units (full-utilization) are replaced by a short "package, don't add"
    note so it stops re-planning the fleet.
    """
    text = doctrine.STRATEGIES_CORE

    # COMPREHENSION — fires whenever something attributable happened to/by us.
    ln = agent_view.get("last_night") or {}
    if (ln.get("incoming_attacks") or ln.get("my_denials")
            or ln.get("my_collisions")):
        text += "\n\n" + doctrine.DOCTRINE_COMPREHENSION

    # REDSIGN + the v10 two-case POKER book — a public pure-RED beacon is live.
    redsign_present = bool(agent_view.get("redsign")) or any(
        isinstance(h, Mapping) and h.get("signal_type") == "redsign"
        for h in (hot_drop_hints or ())
    )
    if redsign_present:
        # ONE redsign doctrine surface (hyg-dedup/hyg-contradictions): a compact
        # locator, then v10's authoritative REDSIGN POKER + DROP-ON-VALUE below.
        # The legacy v7 DOCTRINE_REDSIGN is intentionally NOT included — it
        # conflicted ("long chain, not a 1-cell snatch" / "land ADJACENT") with
        # v10's drop-on-value + agent-owns-length doctrine.
        centers = _redsign_centers(agent_view)
        if centers:
            coord_str = ", ".join(f"(~{cx},~{cy})" for cx, cy in centers[:4])
            text += (
                f"\n\nREDSIGN LIVE near {coord_str} — a pure(255) RED seam is "
                f"PUBLIC (every seat sees it). The broadcast coord is a JITTERED "
                f"smear, not the exact pure. Check WORLD VIEW / ECHO for unfogged "
                f"pure/mass near there FIRST; else use the redsign HOT DROP / "
                f"SMASH_GRAB geometry from the menu. Play it by the two cases below:"
            )
        else:
            text += (
                "\n\nREDSIGN LIVE — a pure(255) RED seam is PUBLIC. Play it by "
                "the two cases below:"
            )
        # v10: point the agent at the right CASE using engine-truth ownership.
        any_mine, any_not = _redsign_ownership(agent_view)
        if any_mine and not any_not:
            text += (
                "\n\nOWNERSHIP: a live REDSIGN is YOURS -> play CASE 1 (you "
                "know the pure cell; smash-and-grab wave 1)."
            )
        elif any_not and not any_mine:
            text += (
                "\n\nOWNERSHIP: the live REDSIGN is NOT yours -> play CASE 2 — "
                "ATTACK by default (it is fresh + mass-rich): blind the finder + "
                "blind-walk the seam, scaled to your probes/harvesters. "
                "CONTEST_DENY is the demoted ahead/certainty fallback."
            )
        elif any_mine and any_not:
            text += (
                "\n\nOWNERSHIP: mixed — at least one REDSIGN is yours (CASE 1) "
                "and one is a rival's (CASE 2); pick per beacon."
            )
        tempo = _tempo_lines(agent_view, day)
        if tempo:
            text += "\n\n" + "\n".join(tempo)
        # v12 fix 3.2 (OBS-44) — the certainty frame goes BEFORE the playbook:
        # every case below is a choice about how much certainty to trade, and
        # the agent cannot make that choice while "HIGH risk" is all it is told.
        text += "\n\n" + doctrine.DOCTRINE_CERTAINTY
        # v12 fix 3.5 (OBS-37) — the ladder turns the menu's risk label into a
        # unit count, so it has to be read before the plays it sizes.
        text += "\n\n" + doctrine.DOCTRINE_RISK_LADDER
        text += "\n\n" + doctrine.DOCTRINE_REDSIGN_POKER

    # DROP-ON-VALUE (I13/F7) — teach the auto-harvest of the landing cell +
    # secure-the-pure-short whenever there is real value to grab (a redsign in
    # play, or hot-drop hints pointing at a value cluster). Off-seam quiet nights
    # skip it to keep the mover's attention budget lean.
    high_value_drop = redsign_present or bool(hot_drop_hints)
    if high_value_drop:
        text += "\n\n" + doctrine.DOCTRINE_DROP_ON_VALUE

    # FULL UTILIZATION — fires on a multi-harvester night (the "deploy every
    # harvester" modus operandi). Single-harvester nights skip it (nothing to
    # spread) to keep the doctrine lean. The MOVER holding a committed recipe gets
    # a "package, don't add" note INSTEAD — the thinker already sized the fleet,
    # and leaving full-utilization in tempts the mover to bolt on off-menu units.
    alive_harvesters = _alive_harvester_count(agent_view)
    if alive_harvesters >= 2:
        # A7: state the EXACT alive count as a hard fact. The agent kept
        # hallucinating a phantom "three harvesters available" when only two
        # were alive (a crashed/idle unit does not exist tonight), then planned
        # a third chain that dissolved. Anchor the plan to the real roster.
        fleet_fact = (
            f"\n\nFLEET TONIGHT: you have {alive_harvesters} harvester(s) ALIVE "
            "and usable this night (the fleet maximum is 3, but only the alive "
            f"units count — plan for {alive_harvesters}, no phantom extra unit)."
            + _harvester_worth_line(
                day=day, day_cap=day_cap, vault_score=vault_score,
            )
        )
        if mover_has_recipe:
            text += fleet_fact + (
                " FLEET ALREADY SIZED: your reasoning pass already decided how "
                "many units to deploy and to what (see EXECUTE THIS PLAN). PACKAGE "
                "that plan — do NOT add an extra harvester drop or probe to 'use "
                "every unit'. An unused unit the plan left in orbit is deliberate."
            )
        else:
            text += fleet_fact + "\n\n" + doctrine.DOCTRINE_FULL_UTILIZATION

    # MULTIPROBE — spend AND spread spare probe stock for next-night vision.
    # Fires in the THINKER path (the plan is what sizes the probes); the mover
    # holding a recipe is told separately not to add units, so it is skipped
    # there. Keyed to actual stock so a lean 0-1 probe night stays quiet. This is
    # the fix for the "dogpile every probe on the current seam / leave stock
    # idle" pattern — the thinker was selecting one PR while 3-4 sat in orbit.
    probe_stock = _probe_stock(agent_view)
    is_final_night = int(day) >= int(day_cap)
    if not mover_has_recipe and probe_stock >= 2:
        # A6: only offer the "SS instead of PR" weaponisation on the ACTUAL final
        # night. On every earlier night a spare probe buys tomorrow's vision, so
        # naming the final-night option here primed the day-5 "final night"
        # hallucination (it superseded instead of scouting with days still left).
        if is_final_night:
            spend_hint = (
                "put a PR (or an SS to blind a rival — it is the final night) id "
                "in \"plan\" for the spare ones"
            )
        else:
            nights_left = int(day_cap) - int(day)
            spend_hint = (
                f"put a PR id in \"plan\" for the spare ones — this is night "
                f"{int(day)} of {int(day_cap)} ({nights_left} night(s) LEFT, NOT "
                "the final night), so a scouting probe usually buys more than a "
                "denial: SUPERSEDE (SS) only to blind a rival off REAL value "
                "(a contested pure, or the leader's fresh disk while you trail) "
                "— otherwise spend the shot on tomorrow's vision"
            )
        text += (
            f"\n\nPROBE STOCK TONIGHT: {probe_stock} probe(s) ready to launch "
            f"from orbit — {spend_hint}, spread across NEW ground.\n"
            + doctrine.DOCTRINE_MULTIPROBE
        )

    # BLUE — setup night OR the vault is genuinely short (R2.3: read the band
    # here rather than trusting the shared tag, which tests a band that does
    # not exist and so fires on 80 blue but not on none).
    if is_setup_night or blue_is_requested(agent_view):
        text += "\n\n" + doctrine.DOCTRINE_BLUE

    # Weapons — jam that hit us last night forces reaction even if tracker cold.
    jam_events = _my_jam_events(agent_view)
    was_chaffed = any(str(e.get("type")) == "chaff_jam" for e in jam_events)
    was_empd = any(str(e.get("type")) == "emp_hit" for e in jam_events)
    # v1.38 — ``snap_hit`` is the victim-private half of a SNAP (§4.9.4);
    # the public ``snap`` says a shot was fired, this says it landed on
    # us. Being hit forces the doctrine on regardless of what the seat
    # can still afford, exactly as for the other two.
    was_snapped = any(str(e.get("type")) == "snap_hit" for e in jam_events)
    if jam_events:
        from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test._v7.prompt import (
            _format_combat_event,
        )
        jam_lines = "; ".join(_format_combat_event(e) for e in jam_events[:3])
        text += "\n\nTHREAT LAST NIGHT (react NOW): " + jam_lines
        # The two weapons deny an HOUR, so the counter is to move the
        # window. SNAP denies a CELL, so moving the window changes
        # nothing and the counter is to move the aim point — v1.38, when
        # SNAP joined this list and would otherwise have inherited
        # advice that does not apply to it.
        if was_chaffed or was_empd:
            text += (
                " Do NOT schedule a pickup in the jammed hours again — the "
                "opponent blind-fires the same predictable window. Pick up "
                "EARLY (hour <=4) or shift the window."
            )
        if was_snapped:
            text += (
                " That was a CELL denied, not an hour, so re-timing will not "
                "help: they read your aim point. Land OFFSET from the obvious "
                "beacon and spread across DISTINCT cells — one SNAP takes one "
                "square, and at 100 blue they can hold several."
            )

    # v1.38 — one gate, asked per weapon. ``could_hold`` is true only if
    # some rack that fits the seat's public total contains that kind, so
    # the minimum spend is enforced by the decode rather than by a
    # threshold written out here: no chaff warning below the 300 a chaff
    # costs, no EMP warning below 200, and SNAP — which had no gate at
    # all before, because it had no field — from 100 up.
    opp_has_emp = _any_could_hold(opponent_weapon_estimates, "emp")
    opp_has_chaff = _any_could_hold(opponent_weapon_estimates, "chaff")
    opp_has_snap = _any_could_hold(opponent_weapon_estimates, "snap")
    # v10: whenever any weapon is in play, reframe it as an orbital strike, then
    # add the OFFENSIVE read — commit more harvesters to the seam on distinct
    # cells rather than retreating to one short (jammable) chain.
    if (
        opp_has_emp or opp_has_chaff or opp_has_snap
        or was_empd or was_chaffed or was_snapped
    ):
        text += "\n\n" + doctrine.DOCTRINE_WEAPONS_ORBITAL
        text += "\n\n" + doctrine.DOCTRINE_WEAPONS_MULTIWAVE
    if opp_has_emp or was_empd:
        text += "\n\n" + doctrine.DOCTRINE_BEWARE_EMP
    if opp_has_chaff or was_chaffed:
        text += "\n\n" + doctrine.DOCTRINE_BEWARE_CHAFF
    if opp_has_snap or was_snapped:
        text += "\n\n" + doctrine.DOCTRINE_BEWARE_SNAP

    # This fork's reason to exist. Gated on OWNING a charge, not on the
    # rivals owning one: every block above is about surviving someone
    # else's weapon, and none of them fire when the threat board is quiet
    # — which is precisely the night a scorch is cheapest.
    if scorch.stock(agent_view)["emp"] > 0:
        text += "\n\n" + doctrine.DOCTRINE_SCORCH

    # v13 — DOCTRINE_SNAP gates on owning a SNAP charge, same as SCORCH gates
    # on an EMP charge. Rung 1 for the third weapon shape: the LLM has to
    # know how to spend what it holds. When ONLY SNAP is in the rack, this
    # is the entire weapon doctrine the seat sees.
    if scorch.stock(agent_view).get("snap", 0) > 0:
        text += "\n\n" + doctrine.DOCTRINE_SNAP

    # FINAL NIGHT — supersede enemy probes. Gated to the ACTUAL final night
    # (A6): earlier nights must not see this or the agent starts declaring
    # "final night" and burning probes on denial while scouting still pays.
    if is_final_night and supersede_hints:
        text += "\n\n" + doctrine.DOCTRINE_LASTDAY_SUPERSEDE

    return text


def build_prompt(
    *,
    agent_view: Mapping[str, Any],
    day: int,
    day_cap: int,
    vault_score: int,
    memory_replay: str,
    chain_hints: Sequence[Mapping[str, Any]],
    probe_hints: Sequence[Mapping[str, Any]] = (),
    hot_drop_hints: Sequence[Mapping[str, Any]] = (),
    blue_hints: Sequence[Mapping[str, Any]] = (),
    supersede_hints: Sequence[Mapping[str, Any]] = (),
    wishlist: "Wishlist | None" = None,
    opponent_weapon_estimates: "Mapping[str, Any] | None" = None,
    prior_day_entry: "Mapping[str, Any] | None" = None,
    mode: str = "mover",
    strategist_directive_block: str = "",
    option_menu_block: str = "",
    last_night_block: str = "",
    player_count: int = 0,
    think_analysis: str = "",
    coverage_note: str = "",
) -> str:
    """Assemble the tabula_v11 prompt as three labelled worldview sections.

    ``mode`` selects the closing contract ("mover" -> moves-first ACTION
    schema; "thinker" -> reasoning-first DECISION schema). The board-fact
    sections are shared between both passes.
    """
    wl = wishlist if wishlist is not None else Wishlist()
    setup_advisory = format_setup_night_advisory(agent_view, day, day_cap)
    is_setup_night = bool(setup_advisory.strip())

    # The MOVER holding the thinker's committed recipe is a PACKAGER: it keeps the
    # board facts it needs for LEGALITY (visible red, drop-legal) but loses the
    # blocks that tempt it to RE-TARGET (raw hot-drops, tactics, chain/probe/blue
    # hints, and the fog/echo frontier menu) and the full-utilization doctrine.
    mover_has_recipe = mode == "mover" and bool(strategist_directive_block)

    doctrine_text = _assemble_doctrine(
        agent_view=agent_view,
        day=day,
        day_cap=day_cap,
        hot_drop_hints=hot_drop_hints,
        wishlist=wl,
        opponent_weapon_estimates=opponent_weapon_estimates,
        supersede_hints=supersede_hints,
        is_setup_night=is_setup_night,
        mover_has_recipe=mover_has_recipe,
        vault_score=vault_score,
    )

    directive_part = (
        ("\nSTRATEGIST DIRECTIVE (top-priority guidance from your reasoning "
         "pass — execute it):\n" + strategist_directive_block + "\n")
        if (mode == "mover" and strategist_directive_block)
        else ""
    )

    is_last_day = int(day) >= int(day_cap)
    self_exec_block = digest.format_self_execution_block(prior_day_entry, agent_view)
    event_digest_block = digest.format_event_digest_block(agent_view)
    emp_scars_block = digest.format_emp_scars_block(agent_view)
    last_night_block_legacy = format_last_night_block(agent_view)
    reflect_block = format_reflect_block(agent_view, prior_day_entry, day)
    opponent_block = format_opponent_block(agent_view)
    enemy_probes_block = format_enemy_probes_block(agent_view, day=day)
    weapons_block = format_opponent_weapons_block(opponent_weapon_estimates)
    geometry_block = format_weapon_geometry_block(opponent_weapon_estimates)
    # ORBIT GUIDANCE — a single crisp "act on these tonight" block. v11 drops the
    # separate TACTICAL PRIORITY FROM ORBIT (wishlist entries) block: it was a
    # second rendering of the same orbit turn (conserve/replace), and the
    # replace-harvester priority isn't even a night action. The imperative
    # directives are the actionable half; the rest lives in doctrine (FLEET fact).
    directives_block = format_orbit_directives_block(
        wl, agent_view, option_menu_block,
    )

    parts: List[str] = []

    # Banner: setup-night alert rides above everything when it fires.
    if setup_advisory:
        parts += [setup_advisory, "\n"]

    # ── SECTION 1 — THE GAME ──────────────────────────────────────────
    parts += [
        _SECTION_1, "\n\n",
        rules.RULES_SUMMARY, "\n",
        doctrine_text, "\n",
        directive_part,
    ]

    # ── SECTION 2 — THE BOARD NOW ─────────────────────────────────────
    parts += [
        "\n", _SECTION_2, "\n\n",
        format_state_block(
            agent_view, day=day, day_cap=day_cap, vault_score=vault_score,
        ), "\n",
    ]
    # YOUR RACK rides immediately under the fleet readout, because it is
    # the same kind of fact: a thing the seat owns and can spend tonight.
    # V12 never printed it anywhere, which is the whole reason its EMPs
    # went home unfired — an agent cannot choose a weapon it was never
    # told it has.
    rack_block = format_rack_block(agent_view)
    if rack_block:
        parts += [rack_block, "\n"]
    parts += [
        world_view.format_world_view_block(agent_view), "\n",
    ]
    # OUT-OF-GRID rides directly under WORLD VIEW: together they are the whole
    # board model (what I can see / what I know but cannot see). Self-gating —
    # renders nothing on a night with no signs and no rich echoes.
    out_of_grid_block = out_of_grid.format_out_of_grid_block(agent_view, day=day)
    if out_of_grid_block:
        parts += [out_of_grid_block, "\n"]
    parts += [
        format_drop_legal_block(agent_view), "\n",
    ]
    # FOG & ECHO is a frontier TARGET menu (edge_promise, echo clusters). The
    # mover holding a recipe must not re-target, so hide it — this is the exact
    # block it mined for the off-menu frontier hot-drop. Keep it for the thinker
    # and for recipe-less mover nights.
    if not mover_has_recipe:
        parts += [format_fog_and_echo_block(agent_view), "\n"]
    if enemy_probes_block:
        parts += [enemy_probes_block, "\n"]
    if emp_scars_block:
        parts += [emp_scars_block, "\n"]
    if opponent_block:
        parts += [opponent_block, "\n"]
    if weapons_block:
        parts += [weapons_block]
    if geometry_block:
        parts += [geometry_block, "\n"]
    elif weapons_block:
        parts += ["\n"]
    if directives_block:
        parts += [directives_block, "\n"]
    # v11 menu rebuild: the standalone HINT blocks (HEURISTIC SUGGESTIONS / PROBE
    # PLACEMENT HINTS / HOT DROP HINTS / TACTICAL OPTIONS / BLUE HARVEST HINTS)
    # are GONE. They were the split brain — full geometry here, selectable IDs in
    # the OPTION MENU, and the model forced to staple them together. The enriched
    # OPTION MENU below now carries every option's walk / yield / crush / risk, so
    # it is the single source of truth. (The final-night supersede reminder stays;
    # it is a timing nudge, not a target menu.)
    if is_last_day and supersede_hints:
        parts += [format_supersede_hints_block(supersede_hints), "\n"]

    # ── SECTION 3 — WHAT HAPPENED LAST NIGHT ──────────────────────────
    parts += ["\n", _SECTION_3, "\n\n"]
    # v11 memory rebuild: the single elegant MEMORY OF LAST NIGHT block (orders +
    # expected, the per-hour engine execution log, R/B/G actual-vs-expected, what
    # you saw, and a directed reflection) is now the single source of truth for
    # Section 3 — it subsumes the old self-exec / event-digest / last-night /
    # reflect stack (which read four overlapping channels the model had to
    # staple together). When it is supplied by the harness we render ONLY it (+
    # the agent-authored memory replay). The legacy stack stays as the fallback
    # for callers/tests that do not pass the new block.
    if last_night_block:
        parts += [last_night_block, "\n"]
    else:
        # Own PLAN vs CORRECTOR vs EXECUTED comes FIRST — it is the engine-truth
        # anchor the reflection must reconcile to (anti-confabulation), ahead of
        # the rival-facing cause->effect digest.
        if self_exec_block:
            parts += [self_exec_block, "\n"]
        if event_digest_block:
            parts += [event_digest_block, "\n"]
        if last_night_block_legacy:
            parts += [last_night_block_legacy, "\n"]
        if reflect_block:
            parts += [reflect_block, "\n"]
    # ``memory_replay`` is the v11 self-labelled STRATEGY JOURNAL (the continuous
    # intent -> happened -> saw -> reflection thread). Legacy callers/tests may
    # still pass a bare replay string; render whichever was supplied verbatim.
    parts += [f"{memory_replay}\n"]

    # ── Closing contract ──────────────────────────────────────────────
    # The CONTAINED TWO-STAGE thinker (mode="think" then mode="plan") replaces
    # the single reasoning-first "thinker" call: stage 1 THINKS on its own hard
    # token budget (bounded prose, no decision to starve), stage 2 turns that
    # bounded analysis into the compact decision JSON on its own budget (so the
    # plan ALWAYS lands, conditioned on real reasoning). "thinker" is kept for
    # the legacy single-call path / tests.
    if mode in ("thinker", "think", "plan"):
        # Agency layer: the grounded situational facts + the ID'd option menu
        # the thinker SELECTS from (echoed into "plan"). Rendered right before
        # the closing so they are the last thing it reads before it reasons.
        situational_block = format_situational_facts_block(
            agent_view, player_count, opponent_weapon_estimates,
        )
        parts += ["\n", situational_block]
        if option_menu_block:
            parts += ["\n", option_menu_block]
        if mode == "think":
            parts += ["\n", _V10_THINK_TASK]
        elif mode == "plan":
            parts += [
                "\n=== YOUR ANALYSIS (from your think pass — commit it now) ===\n",
                "<<<\n", (think_analysis or "").strip(), "\n>>>\n",
            ]
            # Fix 2.7 — a deterministic count of what the THINK pass left in
            # orbit, placed AFTER the analysis so it reads as a check on the
            # plan just made rather than as fresh board facts. Empty (and so
            # invisible) whenever the plan already spends everything.
            if coverage_note:
                parts += ["\n", coverage_note.strip(), "\n"]
            parts += [
                _V10_PLAN_TASK,
                "\n", _V10_THINKER_ADDENDUM,
                "\nOUTPUT ONE JSON OBJECT NOW — DECISION FIRST: \"posture\", then "
                "\"plan\" (the OPTION MENU IDs from your analysis, in execution "
                "order), then \"situational\" when a REDSIGN is live, then your "
                "\"intent\" (tonight) and \"reflection\" (on last night) for the "
                "JOURNAL; a SHORT \"reasoning\" LAST. Start with the open-brace "
                "character. GO:\n",
            ]
        else:  # legacy single-call "thinker"
            parts += [
                "\n", _THINKER_SCHEMA,
                "\n", _V10_THINKER_ADDENDUM,
                "\nOUTPUT ONE JSON OBJECT NOW — DECISION FIRST: \"posture\", then "
                "\"plan\" (the OPTION MENU IDs in execution order), then "
                "\"situational\" when a REDSIGN is live, then your \"intent\" "
                "(tonight) and \"reflection\" (on last night) for the JOURNAL; a "
                "SHORT \"reasoning\" goes LAST. Start with the open-brace "
                "character. GO:\n",
            ]
    else:
        # MOVER. When it holds a committed recipe it is a pure packager (the
        # EXECUTE-THIS-PLAN block is in Section 1) and needs no menu. But on the
        # rare no-recipe fallback night the hint blocks used to be its only
        # targets — those are gone now, so render the OPTION MENU here too so the
        # fallback mover still has the single source of truth to work from.
        if not mover_has_recipe and option_menu_block:
            parts += ["\n", option_menu_block]
        parts += [
            "\n", _V10_ACTION_SCHEMA,
            "\nOUTPUT THE JSON OBJECT NOW. Start with the open-brace "
            "character. GO:\n",
        ]
    return "".join(parts)
