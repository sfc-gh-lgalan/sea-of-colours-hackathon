"""Advisory doctrine — the playbook the agent SHOULD follow (tabula_v11 fork).

v10 keeps the frozen v7 core doctrine (imported verbatim) and rewrites the
redsign-poker book into a clear TWO-CASE, WEAPON-FREE playbook that branches
on the new engine-truth ``redsign.mine`` flag (Workstream E). It also keeps
v8's weapons-as-orbital-strike reframe and adds a short COMPREHENSION note
that points the agent at the new WHAT-HAPPENED digest.

Split (kept strict):
  * MECHANICS (immutable engine physics) live in :mod:`.rules`.
  * PLAYBOOK (advice) lives HERE, gated by ``prompt.build_prompt`` on live
    state so haiku never burns attention on doctrine it cannot act on tonight.

Carried over from v7 (unchanged): STRATEGIES_CORE, DOCTRINE_BLUE,
DOCTRINE_REDSIGN, DOCTRINE_LASTDAY_SUPERSEDE, DOCTRINE_BEWARE_EMP,
DOCTRINE_BEWARE_CHAFF.

New / rewritten in v10:
  * DOCTRINE_REDSIGN_POKER — the two-case weapon-free opening book (my-redsign
    vs not-mine), the user's hot-drop poker.
  * DOCTRINE_WEAPONS_ORBITAL — EMP/chaff reframed as targeted orbital strikes.
  * DOCTRINE_COMPREHENSION — read the attributed WHAT HAPPENED digest and act.
"""

from __future__ import annotations

# Re-export the frozen v7 doctrine so v10 owns one doctrine surface.
from sea_of_colours.orchestrator_2.harnesses.emp_harvest_test._v7.strategies import (  # noqa: F401
    STRATEGIES_CORE as _V7_STRATEGIES_CORE,
    DOCTRINE_BLUE,
    DOCTRINE_REDSIGN as _V7_DOCTRINE_REDSIGN,
    DOCTRINE_LASTDAY_SUPERSEDE,
    DOCTRINE_BEWARE_EMP,
    DOCTRINE_BEWARE_CHAFF,
)

# SCORING CORRECTION (v10 hermetic fix — hyg-scoring). A pure(255) is
# 255 × 3.0 = 765 base pts, NOT 2295 (the frozen v7 text double-counts the tier
# multiplier). Correct the two v7 strategy strings for v10 ONLY; v7/v8 baselines
# stay frozen. See tabula_v11/rules.py for the engine reference.
STRATEGIES_CORE = _V7_STRATEGIES_CORE.replace(
    "A pure(255) cell is 2295 base pts and usually overrides a normal chain",
    "A pure(255) cell is 765 base pts (the biggest single cell; the surrounding "
    "SEAM adds more) and usually overrides a normal chain",
)
# CONTESTED-JACKPOT EXCEPTION (v10 — hyg-contradictions). The frozen v7
# "MAXIMIZE THE CHAIN / go short only for a real reason" line teaches the agent
# to WALK long every time and frames an early pickup as wasted hours. That is
# right for ORDINARY red but WRONG for a contested jackpot: dropping on a
# pure(255)/redsign already banks it (auto-harvest, parcel #1), so the job is to
# SECURE it, not to ride it around the seam. Name the exception loudly.
STRATEGIES_CORE = STRATEGIES_CORE.replace(
    "Go SHORT only for a real reason (see below).",
    "Go SHORT only for a real reason (see below) — and a CONTESTED JACKPOT is "
    "the biggest one. When you drop ON a pure(255)/redsign you ALREADY banked it "
    "as parcel #1 (auto-harvest), so SECURE it: pick up within 1-2 hours (0 "
    "extra steps when chaff or an EMP is in play, or when a rival can SEE the "
    "cell; a public beacon alone is NOT that — nobody can punish a walk they "
    "cannot see, and on a blind unarmed board you take the whole seam). Do NOT "
    "ride a long chain over a jackpot — a crash/chaff/collision on the walk "
    "spills the whole load, and that risk beats the few extra harvest-hours. "
    "Strip the wider seam with a SEPARATE harvester or a LATER wave, never the "
    "grabbing unit.",
)
# MENU-AS-SOURCE-OF-TRUTH (v11 menu rebuild). The frozen v7 PER-TURN PROCEDURE
# points the agent at standalone CHAIN/HOT-DROP/PROBE HINT blocks and tells it to
# compute EV itself. v11 retired those blocks: the OPTION MENU now carries every
# play's walk, yield, crushes, and collision risk. Rewrite steps 1-2 to point at
# the menu (and drop the "hints carry no scores" line, which is now false).
STRATEGIES_CORE = STRATEGIES_CORE.replace(
    "  1. ENUMERATE opportunities: visible RED chains (CHAIN HINTS), hot-drop\n"
    "     combos (HOT DROP HINTS), any REDSIGN race, and fog worth probing\n"
    "     (PROBE HINTS). The HINT blocks below are your candidate menu.\n"
    "  2. SCORE each with the tier table in RULES (purity x tier_mult). You\n"
    "     compute EV yourself; the hints carry no scores.",
    "  1. ENUMERATE opportunities from the OPTION MENU below. It lists the plays\n"
    "     available tonight (grabs, redsign patterns, hot drops, probes, juice\n"
    "     chains) with walk, expected yield (red/blue/green), crushes, and\n"
    "     collision risk already computed. Its geometry is PRE-VALIDATED, so your\n"
    "     final picks are option IDs from this menu — never coordinates you\n"
    "     compose yourself.\n"
    "  2. COMPARE them on what the menu shows; prefer high yield at low collision\n"
    "     risk with no self-crush. You do NOT recompute EV or reconstruct the\n"
    "     geometry — the menu already carries it.",
)
# AGENT-OWNED TAIL TRIM (v11). The packager compiles the chain you pick VERBATIM
# (minus the hard hazards: chaff window, known-green, contested blind-walk); it
# does NOT shorten a walk for value/exposure reasons — that judgement is YOURS.
# So trim your own tails: when a chain's trailing steps are low-value AND the menu
# flags them under enemy vision, pick the SHORT variant or ``avoid`` those cells.
STRATEGIES_CORE = STRATEGIES_CORE.replace(
    "     geometry — the menu already carries it.",
    "     geometry — the menu already carries it.\n"
    "  2b. TRIM YOUR OWN TAILS (the compiler will NOT do it for you): when a\n"
    "     chain's TRAILING steps are low-value (trace red / fog / already-green)\n"
    "     AND the menu marks them under enemy vision (collision risk MED/HIGH),\n"
    "     take the SHORT variant or put those tail cells in `avoid` — secure the\n"
    "     rich head and lift early. Keep the full walk only when the tail's own\n"
    "     mass/pure/blue yield is worth the exposure.",
)
STRATEGIES_CORE = STRATEGIES_CORE.replace(
    "the HOT DROP HINTS give a crush-safe",
    "the HOT DROP options in the menu give a crush-safe",
)
# BOARD-AWARENESS PASS (v12). The v11 menu rebuild made the OPTION MENU the only
# block the agent acts on — a scan of 28 turns found just ONE coordinate in all
# the model's reasoning that came from anywhere else, even though WORLD VIEW
# carries ~66 vein-or-better cells per turn that no option surfaces. The menu is
# authoritative for LEGAL geometry; it is not a complete picture of the board.
# Step 6 spends the last of the agent's attention reconciling the two, and is
# deliberately worded as SELECT-AND-TRIM (re-rank, swap, shorten) rather than
# AUTHOR — inventing drop cells is the hallucination surface the pre-validated
# menu exists to close.
STRATEGIES_CORE = STRATEGIES_CORE.replace(
    "  5. SAFETY PASS: every chain ends in pickup THIS night; trim any chain\n"
    "     you can't finish inside 21 hours.",
    "  5. SAFETY PASS: every chain ends in pickup THIS night; trim any chain\n"
    "     you can't finish inside 21 hours.\n"
    "  6. BOARD PASS — before you commit, read your picks back against WORLD VIEW\n"
    "     and OUT-OF-GRID. The menu is authoritative for what is LEGAL, but it\n"
    "     offers a handful of plays; WORLD VIEW lists EVERY cell you can see and\n"
    "     OUT-OF-GRID every beacon and echo you know. Ask:\n"
    "       * Is every harvester out, and every probe I can afford spent?\n"
    "       * Does WORLD VIEW show richer red than the option I picked — a\n"
    "         mass/vein cluster no option surfaced? Then prefer the option that\n"
    "         lands nearest it, or the variant that stops on it.\n"
    "       * Are my walks padded with trace? Bring the landing closer to the\n"
    "         value, or cut the low-yield tail (see 2b).\n"
    "       * Is a PURE in play — seen, echoed, or under a redsign? Taking pure\n"
    "         and mass EARLY, denying a rival's probe or landing, and contesting\n"
    "         a redsign are what decide seasons. Never bank trace while a pure\n"
    "         goes uncontested.\n"
    "     You MAY re-rank, drop, swap or shorten your picks on this pass. You may\n"
    "     NOT invent a drop cell or a walk that no option offers.",
)
assert "6. BOARD PASS" in STRATEGIES_CORE, (
    "v12 board-awareness pass failed to apply — the v7 SAFETY PASS wording moved"
)
DOCTRINE_REDSIGN = _V7_DOCTRINE_REDSIGN.replace(
    "cell scores 765 x 3.0 = 2295 base pts and pure runs in SEAMS, so the",
    "cell scores 255 x 3.0 = 765 base pts and pure runs in SEAMS, so the",
)
assert "2295" not in STRATEGIES_CORE + DOCTRINE_REDSIGN, (
    "v10 scoring correction failed to apply"
)


# ─────────────────────────────────────────────────────────────────────────
# COMPREHENSION — read the causal digest before you plan
# ─────────────────────────────────────────────────────────────────────────
DOCTRINE_COMPREHENSION = """\
UNDERSTAND LAST NIGHT BEFORE YOU MOVE (see WHAT HAPPENED):
  A bad night has a CAUSE. Before planning, read the WHAT HAPPENED digest and
  name it: who did what to you, and what it cost.
    * A probe of yours destroyed (superseded or collided) means you LOST that
      disk's VISION — any hot-drop that relied on it had no live sensor and
      banked nothing. Do not re-plan a drop into a disk you no longer own.
    * An EMP hit means your harvester was DISABLED in those hours; a pickup
      you scheduled then did NOT happen (dawn-crash risk). Re-time it.
    * A chaff jam CANCELLED your action slots in those hours. Nothing you
      queued then ran.
    * A harvester collision banked ZERO and the cargo was LOST (not held).
  If you denied an opponent (BY YOU), press it: a blinded rival cannot drop
  into that disk next hour. Set your reflection/gap_reason to the REAL cause,
  never "held in hoard" for cargo that was lost.
"""


# ─────────────────────────────────────────────────────────────────────────
# CERTAINTY — what a redsign night actually guarantees (v12 fix 3.2, OBS-44)
# ─────────────────────────────────────────────────────────────────────────
# The keystone of Phase D. Everything below it (the risk ladder, the repeat
# trigger, the own-seam ladder) is a refinement of this frame, so it is placed
# FIRST on any redsign night and kept short enough to be read every time. It
# replaces "HIGH risk" — an adjective the agent could not act on — with the four
# mechanical facts that decide the night.
DOCTRINE_CERTAINTY = """\
WHAT IS CERTAIN AND WHAT IS A WAGER (read this first — it decides the night):

  NOTATION: H01..H24 is the HOUR of the night, always. It never means "harvester
  1". Units are named by ordinal — "the 2nd harvester". Menu titles carry both,
  e.g. "Secure the mass ring (2nd harvester, H09)".

  1. THE FIRST-HOUR DROP IS THE ONLY CERTAINTY IN THE GAME. You drop, the landing
     cell auto-harvests. No rival walk, no collision, no EMP can prevent it.
     CHAFF is the single exception. Everything after that landing — every step,
     the pickup itself — is contingent.

  2. THE WAGER STAKES THE WHOLE HOLD, NOT THE NEXT CELL. Cargo banks at PICKUP,
     and a crash or jam ZEROES the hold. So step 3 of a walk does not risk step
     3's value: it re-risks the pure and everything else already aboard. When an
     option shows one yield number, split it yourself — what the LANDING secures,
     and what the WALK wagers to add. Extend only while the marginal cell beats
     the chance of losing everything behind it. That is why the value tail is
     mass/pure only and never more than two steps: trace and vein never clear
     that bar once a pure is in the hold.

  3. VISION IS FIXED AT NIGHT START. IT CAN BE ADDED, NEVER RETRACTED. A probe
     you launch at hour K opens its disk from K+1, so you can CREATE legality
     mid-night. Nothing REMOVES it. The consequence is the one most people get
     wrong: SUPERSEDING A RIVAL'S PROBE DOES NOT STOP THEM DROPPING TONIGHT.
     Their legality was computed before your probe landed. Superseding costs
     them TOMORROW. It is still worth doing — just never as a way to win
     tonight's race.

  4. THE WAGER IS PRICED BY WHO CAN PUNISH YOU — and it is a THRESHOLD, not a
     gradient:
       * rival LIVE vision on your cell -> they can AIM at you.
       * EMP in play -> they can hit a walking harvester WITHOUT aiming.
       * chaff in play -> it denies the H1 LANDING, the only thing that can.
       * player count -> how many INDEPENDENT blind stabs get taken at the same
         smear. This matters on a REDSIGN specifically, because the beacon is
         public: those stabs are not scattered over the map, they converge on the
         few cells you are standing on.
       * lateness -> how much is in the hold to lose.
     ONE OR TWO opponents and no EMP: nobody can aim at you, so the wager is
     nearly free — EXTEND (take the full sweep). THREE OR MORE opponents, OR any
     EMP: SHORT GRAB. Three blind stabs are three independent rolls, not one.

  5. THE TWO WEAPONS HAVE OPPOSITE ANSWERS — they do not substitute:
       * CHAFF attacks the CERTAINTY (it denies the landing) -> GO AGAIN. Stage a
         second bite at the same pure at a later hour; a jammed H1 leaves the
         pure ON THE BOARD for it.
       * EMP attacks the EXTENSION (it kills a walking harvester) -> GO SHALLOW.
         Shorten the chain, lift early.
     Chaff makes you repeat; EMP makes you shorten. Reading one as the other is
     how a night gets thrown away.
"""


# ─────────────────────────────────────────────────────────────────────────
# THE RISK LADDER — levels that name a SHAPE (v12 fix 3.5, OBS-37)
# ─────────────────────────────────────────────────────────────────────────
# The menu prints a risk level on every option and, until now, that level was an
# adjective with no stated consequence — so it could not change a decision. Each
# rung here names the COMMITMENT it implies, which is the only form of the
# question the agent can actually answer.
DOCTRINE_RISK_LADDER = """\
THE RISK LEVEL SIZES YOUR COMMITMENT. IT DOES NOT SET YOUR CHAIN LENGTH.
Those are two separate questions and conflating them is how a safe board gets
played timidly. Answer them in order:

  QUESTION 1 — HOW MANY UNITS GO AT THE PURE? Read the level off the option:
    LOW        no rival vision on the cell, no weapons in play.
               -> ONE unit is enough. Nobody is coming for it in time.
    MED        weapons in play, but still no rival eyes on the cell.
               -> ONE unit, and lift on schedule.
    HIGH       a rival has ECHO or LIVE knowledge of the cell, OR any live
               redsign is on the board.
               -> commit a unit at the EARLIEST legal hour, and plan a second
                  bite if chaff could cancel the first.
    VERY HIGH  live rival vision AND weapons AND more than one opponent.
               -> TWO UNITS ON THE SAME CELL — the DROP BLOCK.
    ULTRA HIGH late season, several seats, at least one with live vision.
               -> everything you have, repeatedly. There is no tomorrow to save
                  a harvester for.

  EVERY LIVE REDSIGN STARTS AT HIGH. A public pure is worth contesting by
  definition, so the floor on COMMITMENT is not negotiable.

  QUESTION 2 — HOW FAR DOES THE UNIT WALK ONCE IT IS THERE? This is NOT the
  level. A walk is only punished by someone who can actually reach it, so the
  test is narrow:
    * ONE OR TWO opponents and NO EMP -> EXTEND. Take FULL_SWEEP. Nobody can
      aim at you; they would have to blind-drop into the smear and get lucky.
    * THREE OR MORE opponents, OR any EMP -> SHORT GRAB. Three blind stabs are
      three independent rolls, not one.
  READ THIS CAREFULLY: a HIGH that comes ONLY from the redsign floor — no rival
  has ever had eyes on the cell, and no weapon is in play — shortens NOTHING.
  Nobody can punish a walk they cannot see. On that board the level is telling
  you to GO, not to flinch, and the full sweep is the correct play. Flinching
  there is the single most expensive habit this seat has.

  THE DROP BLOCK (what makes VERY HIGH a real play and not just a warning). When
  a rival can SEE the pure they can LAND on it, not merely race you to it. The
  engine's answer is specific:
    * SAME HOUR, SAME CELL — nobody lands. Both units are damaged and stay in
      orbit, and the cell is NOT harvested by either side. The pure SURVIVES, so
      your SECOND harvester takes it an hour later, uncontested — their unit is
      wreckage now, and wreckage does not block.
    * LATER HOUR, THEY ARE ALREADY ON IT — they spill. Dropping onto a healthy
      enemy harvester damages both and EMPTIES their hold: their pure banks
      nothing. Note carefully — the cargo is DESTROYED, not transferred. A
      follow-up drop cannot collect it.
  So matching the contested cell works in both directions, and if they never
  come your drop simply succeeds and you have the pure anyway. The cost is that
  a damaged harvester is not repaired at dawn: 500 credits through Orbit, and
  the unit is out until you pay. Spend it on a pure or on the leader. Never on a
  vein.
"""


# ─────────────────────────────────────────────────────────────────────────
# REDSIGN POKER — the two-case, WEAPON-FREE hot-drop book
# ─────────────────────────────────────────────────────────────────────────
DOCTRINE_REDSIGN_POKER = """\
REDSIGN POKER (no weapons in play) — a public pure-RED beacon is a HONEYPOT:
  Everyone sees the SAME advertised cell, so everyone dives it and two drops
  on one cell in one hour COLLIDE (both damaged, both bank ZERO). The pure
  runs in a SEAM, so the points are in the cells AROUND the beacon. The game is
  ALWAYS Hawk — but SMART hawk: there are many cells to go down, so fan out and
  hit the seam from waves and angles rather than stacking the one lit cell.

  YOU CHOOSE THE PLAY. The OPTION MENU has pre-built, named seam PATTERNS with
  the geometry already filled in. Read the ONE question — is this MY redsign
  (engine-truth ``mine`` flag / OWNERSHIP line) — then put the pattern IDs you
  want, IN EXECUTION ORDER, into your "plan". You are selecting and ordering,
  not inventing coordinates.

  DECLINING IS A MOVE, AND IT HAS A PRICE. A beacon you leave alone does not
  score zero — a rival banks the pure, so the board swings by roughly TWICE its
  value (the menu prints the number under REDSIGN PATTERNS). Weigh your safe
  chain against THAT, not against nothing. There is no rule here and no forced
  id: sometimes the chain really is right. But the honest comparison is "bank
  266 and hand them 735", not "141 versus 266", and the second framing is what
  loses seasons.
    * The rough indifference point is MASS-OR-BETTER you can actually bank
      instead. Live mass in hand is a real alternative to contesting; a vein
      chain is not, however certain it looks.
    * TWO harvesters combing DIFFERENT sectors of one seam is not twice the
      exposure — it is roughly double the chance of crossing the pure, and each
      unit is a separate chance to clip a laden rival on their way out.
    * Clipping a loaded enemy harvester costs you nothing and costs them their
      whole hold. Denial you get for free is still points.
    * Remember what a harvester is worth (FLEET TONIGHT prints it): its own
      future harvests, and nothing else. On the last night that is zero.

  ============================================================
  CASE 1 — IT IS YOUR REDSIGN (mine=true; you know the pure cell):
  ============================================================
  HOW you take the pure depends on whether you can DROP on it right now. The menu
  shows ONE of two own-seam trios — pick whichever IDs it actually lists.

  WHY GO EARLY — BANK THE PURE BEFORE THEY DENY IT. The redsign is PUBLIC (§3.15):
  EVERY rival sees this pure and is racing you to it. Within an hour or two they
  will SUPERSEDE the probe that lights it (blinding you), blind-walk onto it, or
  COLLIDE on the cell — any of which costs you the crown jewel. So take it EARLY
  and pick up FAST. "No enemy vision RIGHT NOW" is a trap — a public pure DRAWS
  contest, so a safe-looking hour you waste is the hour a rival arrives.

  THREE WAYS TO TAKE IT — A REAL CHOICE, NOT A RULE. The menu offers all of
  them; weigh them yourself. They sit on a ladder of certainty-for-points, and
  it is the SAME harvester, so pick exactly one:
    * SMASH_GRAB (the CERTAIN play) — land STRAIGHT on the pure, it
      auto-harvests, lift at H2. Zero steps, two hours. You bank the jackpot
      (~765) for CERTAIN and leave the rest of the seam. Nothing on the board
      beats an H1 landing: not a rival's walk, not a collision, not an EMP.
      CHAFF is the only thing that denies it.
    * SMASH_GRAB_VALUE (the SMALL trade) — the same drop, then ONE or TWO steps
      onto adjacent MASS/PURE only, then lift. Usually a large fraction of the
      seam's points for a couple of extra hours of exposure. The drop is still
      guaranteed; the steps are not. Good on a quiet board against one rival —
      they would have to get lucky with a blind attack to catch you. Bad with
      chaff/EMPs about or with three rivals converging, because now several
      independent blind attacks are searching the same ground.
    * FULL_SWEEP (the GAMBLE) — ride the WHOLE visible seam in ONE outing (its
      pures + the mass ring): FAR more points, but ONE harvester means a
      chaff/EMP/collision ANYWHERE on the walk spills the WHOLE unlifted load, the
      pure included. It is a genuine gamble, and the odds get WORSE with (a) MORE
      players racing the same public seam and (b) LATER in the season (more weapons
      banked, tighter contest). Offset that against the sheer value: EARLY, few
      rivals, quiet board -> the sweep's extra pures are usually worth it;
      CROWDED, LATE, or weapons about -> take the sure SMASH_GRAB.
  With TWO harvesters you do NOT have to choose: the FIRST unit runs SMASH_GRAB
  at H01 to lock the jackpot, and the SECOND runs SECURE_MASS / LATE_SWEEP later
  in the night to bank the ring safely (a crash on those later waves costs only
  ring mass, never the pure).

  THE PAIRING RULE — ONE FROM EACH COLUMN. Every own-seam play either TAKES THE
  PURE or works ground the pure has already left:
    * FIRST WAVE (takes the pure): SMASH_GRAB, SMASH_GRAB_VALUE, FULL_SWEEP.
      Pick exactly ONE — they want the same cell and the same harvester.
    * SECOND WAVE (the pure is gone): SECURE_MASS, SWEEP_RING, LATE_SWEEP. These
      never re-enter the pure cell, because by then it is your own stripped
      green and costs -100 to touch.
  A normal night is one play from each column.

  THE ONE EXCEPTION — CHAFF INSURANCE, AND IT IS KEYED ON CHAFF ALONE. The only
  reason to send a second unit at a pure you already grabbed is that the first
  grab may never have happened: CHAFF is the sole mechanic that cancels an H1
  landing, and a cancelled landing leaves the pure on the board. So when chaff
  is in play AND you hold H1 (the pure is drop-legal now), the menu offers
  CHAFF_INSURANCE — the same cell, hours later, with the second harvester. The
  pricing is the argument: if the first wave died you gain the jackpot (~+765);
  if it lived you pay one green (~-100). Without chaff this play is the -100 and
  nothing else, so do not take it on a quiet board. And note what does NOT
  trigger it: a crowd of rivals is a reason to go SHORT, not to go TWICE.

  BECAUSE THE PURE IS BANKED, THE RING IS LOW-STAKES. Once H1 has locked the
  jackpot you can work the surrounding mass FREELY — with a SEPARATE harvester on
  its OWN later wave (a DIFFERENT hour block), each backed by its own probe. A ring
  wave that crashes or gets jammed only costs ring mass, never the pure — that is
  the whole reason you secured it first. So the 2nd/3rd harvester can be greedier
  and range wider than the grabbing unit ever should.

  (1a) PURE IS DROP-LEGAL NOW (a live probe disk covers it) — the SMASH trio, plus
  FULL_SWEEP as the greedy first-harvester alternative:
    plan = ["SMASH_GRAB", "SECURE_MASS", "LATE_SWEEP"]   # sure grab + ring waves
       or  ["SMASH_GRAB_VALUE", "SECURE_MASS"]           # grab + the touching mass
       or  ["FULL_SWEEP"]                                # one-harvester seam gamble
    * SMASH_GRAB (1st harvester, H01) — the GRAB is the DROP. Land ON the pure — it auto-harvests,
      so the jackpot banks as parcel #1 the instant you touch down. Drop STRAIGHT
      (no probe) and lift: ZERO extra steps, always. A direct smash is EMP-PROOF
      (it resolves in one hour). The CERTAIN grab.
    * SMASH_GRAB_VALUE (same harvester as SMASH_GRAB, H01) — the drop, then 1-2
      steps onto touching MASS/PURE, then lift. Never runs past two squares and
      never spends a step on trace. Offered only when such cells actually exist.
    * FULL_SWEEP (the ALTERNATIVE to SMASH_GRAB, same harvester, H01) — instead of
      lifting after the pure, ride the whole visible seam and bank its pures + mass
      in one walk.
    SMASH_GRAB, SMASH_GRAB_VALUE and FULL_SWEEP are the SAME unit — pick exactly
    ONE of the three. Choose by the risk profile, not by habit: FULL_SWEEP when
    the value clearly outweighs the risk, SMASH_GRAB_VALUE for the middle, and
    SMASH_GRAB when you must be sure of the jackpot.
    * SECURE_MASS (2nd harvester, H09) — on its OWN wave at a LATER hour block
      and backed by its own probe, strips the MASS ring around the pure (most of a
      seam's points live in the ring, not the single lit cell). The jackpot is
      already banked, so this unit can be GREEDIER than the grabber — a crash here
      costs ring mass, never the pure. This is the SHORT, secured version.
    * SWEEP_RING (the ALTERNATIVE to SECURE_MASS, same harvester, H09) — the
      greedy version of that same wave: all the visible mass, and then a push
      into the UNLIT side of the pure's surround. That tail is the whole point.
      Value clusters around a pure, so the half of its ring you have never seen
      is the densest unknown on the board — this is not frontier exploration,
      it is the rest of a seam you already know is rich. The wave's probe is
      placed on the fogged bearing, so the ground is lit before you walk it.
      Pick SWEEP_RING when the board is quiet enough to spend the hours, and
      SECURE_MASS when it is not. Do NOT pick both — one harvester.
    * LATE_SWEEP (3rd harvester, H12) — later still, bigger pattern from a new
      bearing over the dense seam.

  (1b) PURE IS FOGGED/ECHO BUT WALKABLE (no probe needed) — the WALK-IN trio:
    plan = ["WALKIN_GRAB", "WALKIN_SECURE", "WALKIN_LATE"]
    The pure is yours but sits outside a live probe disk. Only the initial DROP
    needs live coverage — STEPS do not — so you land on the nearest live frontier
    cell and WALK straight onto the pure. No probe required.
    * WALKIN_GRAB (1st harvester, H01) — drop on the live frontier, walk the fog steps onto your
      pure, grab, pick up FAST.
    * WALKIN_SECURE — CRUCIAL: a walk-in is NOT EMP-proof (it spans hours, so an
      EMP can jam the steps). The pure is too valuable to trust to one chain, so
      a SECOND harvester DOUBLE-WALKS the same pure from a different frontier: if
      the first walk was jammed this still banks the jackpot; if it landed, re-hitting takes a
      green — worth it to be SURE. It then walks on into the surrounding mass.
    * WALKIN_LATE — a THIRD, staggered walk-in that takes the pure then sweeps the
      mass halo around it.
    Default on a rich own seam under walk-in: take WALKIN_GRAB + WALKIN_SECURE at
    minimum (double-walk the jackpot); add WALKIN_LATE with a third harvester.

  If the menu shows NEITHER own-seam trio, your pure is fogged, unwalkable, and
  you hold no probe this night — it is simply unreachable. Do not force it: spend
  the fleet on a CHAIN (CHn) / HOT DROP / rival beacon, and buy a probe next orbit
  if you want the seam tomorrow.
  Deploy every harvester you have. Add a PROBE (PRn) for any spare probe once the
  pure is secured.

  ============================================================
  CASE 2 — IT IS NOT YOUR REDSIGN (mine=false; a rival found it) — ATTACK it:
  ============================================================
  DEFAULT POSTURE: ATTACK. A redsign is nearly always exactly ONE night old — a
  frontier probe trips it, and the finder usually cannot harvest it until the
  NEXT day — so the pure is very likely STILL THERE, and the seam is MASS-RICH.
  That means a blind drop is +EV even when it MISSES the pure: a hit is a double
  win (huge points + huge denial), and a miss still banks the surrounding
  vein/mass. So do NOT default to sitting back — the timid confirm-only play is
  what throws these away. The menu shows two things at once:

  2a. FOGGED to you (no live probe of yours on the seam) — the USUAL case. The
      menu now offers a BLIND ATTACK (plus CONTEST_DENY, demoted, below). Scale
      the attack to your FLEET — this is the whole decision:
        * 0 probes → you CANNOT land on a fogged seam. Skip it; farm red you can
          SEE and buy a probe for tomorrow.
        * 1 probe, 1 harvester → the blind attack, in one of TWO sizes (both are
          on the menu; YOU pick, the menu will not pick for you):
            - BLIND_AND_GRAB — the LONG comb, ~5 cells across the mass-rich
              seam. Best odds of actually crossing the pure, and it banks what
              it walks over. Costs you the harvester for most of the night.
            - BLIND_GRAB — the SHORT grab, drop on the best guess, 1-2 steps,
              lift. Worse odds, far less exposure. Take this when weapons are
              about (a jam mid-walk zeroes unlifted cargo), when several rivals
              are converging on the same seam, or when you need the unit back.
        * 1 probe, 2 harvesters → a blind attack with the FIRST harvester; send
          the SECOND one ELSEWHERE (a chain / your own seam). Do NOT stack the
          second harvester behind one lone probe on a contested seam — spread
          the risk.
        * 2 probes, 2 harvesters → the TWO-BEARING attack: a blind attack (the
          FIRST harvester blinds the finder + combs one sector) AND
          UNBEATEN_FLANK (the SECOND brings its OWN probe and combs a DIFFERENT
          sector from the opposite bearing). One is likely contested; the other
          banks.
      The accepted risk is FOG, never a KNOWN penalty — the compiler still
      refuses any drop/step onto a cell you KNOW is stripped/green.

      CONTEST_DENY (DEMOTED — the "ahead / want-certainty" alternative): supersede
      the finder + drop a probe to confirm the pure, commit NO harvester, and
      SMASH it for real tomorrow. Prefer this over a blind attack only when you
      are AHEAD (protect the lead, take the sure denial) or you specifically want
      certainty before spending a harvester. It is denial without the fog gamble
      — but it banks nothing tonight, so it is the cautious pick, not the default.

  2b. LIVE-confirmed (a live probe of yours already reveals red on the seam) —
      the menu shows the aggressive trio. Now you KNOW where the value is, so
      contest hard. Typical plan, in order:
    plan = ["BLIND_GRAB", "UNBEATEN_FLANK", "WALK_IN"]
      — but read the FIRST id off the menu, because it splits on what you can
      actually see (the menu will show exactly one of these two):
    * SEEN_GRAB — their PURE itself is under YOUR live vision. This is not a
      gamble and must not be priced as one: you have the exact cell, so drop ON
      it (the landing auto-harvests) and LIFT. The H1 landing is the one thing
      on a contested seam nobody can take off you. Superseding the finder on the
      way blinds them for free. Whenever this id is on the menu, it beats a blind
      play at the same seam outright — there is nothing left to guess.
    * BLIND_GRAB — the pure is NOT in your sight. That covers both evidence
      states, and the play is the same in each: here in 2b you can see some red
      on the seam but not the pure itself; in 2a above you can see nothing at
      all but the broadcast smear. A stale echo of the pure counts as NOT in
      sight — an echo is a coordinate you wrote down, not vision, and the cell
      may already be stripped. Blind the finder (land your probe ON theirs to
      supersede it), then a blind drop and a SWEEP. KEY
      DIFFERENCE FROM SMASH_GRAB: here you do NOT know the exact pure — the
      broadcast is a jittered smear, so one guessed drop cell rarely IS the pure.
      Comb across the top candidates (not zero — a single-cell blind grab usually
      banks trace for nothing), then pick up. Poker note: if they drop in hour K
      it STILL lands (vision snapshots at hour-start); your supersede only denies
      them from K+1. Try it anyway — the sweep banks nearby red and denies.
    * UNBEATEN_FLANK — the SECURED bank: a fresh probe on the OPPOSITE axis
      reaches the same seam from an uncontested angle after the EMP window. This
      is the wave nobody is fighting for; make it your reliable pickup.
    * WALK_IN — late outer mop-up from a THIRD bearing: pure red bleeds into
      good red, so you scoop the halo even if the pure itself is contested (and
      you keep great vision for tomorrow).
  If a wave 1 crash means NOBODY banked the pure, that is fine: the pure tile
  SURVIVES a collision. Come again next wave / next night from a new angle.

  THE ONE OPPORTUNITY-COST CAVEAT (and it is a HIGH bar — do not let it make you
  timid): only defer the blind attack when the confirmed chain you can already
  see is genuinely COMPARABLE to the redsign's expected value. A fresh rival
  redsign is a pure(255) sitting in a MASS-RICH seam — its expected haul is large
  (typically 400-800+), so a modest ~150-200 juice chain does NOT outweigh it: a
  small "bird in the hand" is the exact reasoning that makes you shy. Defer ONLY
  when the visible chain is itself a big secured bank (its own pure/mass, in the
  same league as the redsign — think ≥400, not ≥150). Below that bar, and while
  you hold a spare probe + harvester, the DEFAULT is still ATTACK.
  And even when you DO defer, do not just walk away: spend a spare PROBE to light
  the seam (a CONTEST_DENY or a plain probe on the beacon) so you SEE it and
  strike for real NEXT night. Bank the big sure value now, set up the kill for
  tomorrow — but never trade a whole redsign for a small safe chain.

  ============================================================
  CASE 3 — TWO REDSIGNS AT ONCE (one MINE + one a RIVAL's):
  ============================================================
  Now the menu shows BOTH: your own set (the SMASH trio SMASH_GRAB/SECURE_MASS/
  LATE_SWEEP, or the WALK-IN trio WALKIN_GRAB/WALKIN_SECURE/WALKIN_LATE if your
  pure must be walked) and the rival's set suffixed #2 — the aggressive trio
  (SEEN_GRAB#2 or BLIND_GRAB#2, then UNBEATEN_FLANK#2 / WALK_IN#2) if the rival
  seam is LIVE to you, or the blind-attack pair BLIND_GRAB#2 / UNBEATEN_FLANK#2
  (plus a demoted CONTEST_DENY#2) if the rival seam is fogged. A fresh fogged rival seam is worth
  ATTACKING (mass-rich, usually unharvested), but your FIRST harvester still
  belongs on your OWN pure — contest the rival with the SECOND unit / spare
  probe, not the first.
    FIRST, CHECK FOR MUTUAL VISIBILITY. Read the EARLIEST-HOUR line: if BOTH
    pures show you at H1 AND show a rival at H1 on each, then both seats can see
    both jackpots and this is no longer a race — it is denial poker, and there
    is a frame for it. When two pures are lit and everyone sees both, THERE IS
    NO BRANCH IN WHICH THEY TAKE BOTH — provided you spend the night on PURE
    CELLS AND NOTHING ELSE. Mass detours and vein-first walk-ins are exactly how
    that guarantee gets thrown away. Two ways to play it:
      * DOUBLE SMASH (salted earth) — one harvester ON each pure at H01, both
        auto-harvesting on landing, both lifted by H02. Both jackpots banked in
        two hours. Worst case they lift one and crash you on the other, which is
        still mutual denial. High floor, low ceiling.
      * BLIND ONE, DOUBLE-DROP THE OTHER — H01 drop on pure A, H02 probe onto
        their eye over zone B to blind them, H03 lift A, then a second smash on
        B now that it is unwatched. The second run is the attack AND the
        insurance at once, because two harvesters landing on one cell in one
        hour do not auto-harvest: neither lands and the pure survives INTACT for
        the follow-up. Higher ceiling, more variance.
    THE FORK IS WEAPONS, and it selects the play rather than merely colouring
    it: with no weapons about, take the breadth — DOUBLE SMASH banks two pures
    before anything can react. With weapons in play, breadth is what gets
    punished; go deep on one pure and deny the other.
    If mutual visibility does NOT hold, play the ordinary fork below.

    * The FIRST harvester is NOT a choice: grab your own pure (SMASH_GRAB or
      WALKIN_GRAB). It is
      the surest points on the board and yours alone — always take it first.
      (This rule is suspended under mutual visibility above, where both pures
      are equally droppable at H01 and committing the first unit to yours by
      rule forfeits the tempo argument before it starts.)
    * Your 2nd (and 3rd) harvester is a REAL FORK, and no rule can pick it for
      you — THINK IT THROUGH:
        (a) CONTEST THE RIVAL — SEEN_GRAB#2 (their pure is in your sight: land on
            it and lift) or BLIND_GRAB#2 (blind their finder, then blind-walk
            the mass-rich seam) or UNBEATEN_FLANK#2 (crawl in from an untested
            angle). This is DOUBLE DAMAGE: you take their pure AND deny them (they
            get nothing, you get everything), and even a miss banks nearby
            vein/mass on a fresh seam. But it is RISKIER — you blind first, and if
            they smash-grab in the same hour it still lands; weaker bots won't
            even know the move. High upside, high variance.
        (b) SECOND OWN DROP — SECURE_MASS (or WALKIN_SECURE) your own seam. SAFER
            and near-certain: you both walk away with something (they get their
            pure, you get your mass / a double-walked jackpot). Lower variance.
    * Let the SCORE tilt the fork (do not hard-code it): even or AHEAD → you can
      afford the double-damage swing on the rival. MASSIVELY BEHIND → the safe
      own-mass bank is usually smarter than a coin-flip; but if only a big swing
      can win, take the risk. Weapons likely on the rival seam → contesting is
      dearer (their chaff can zero your blind grab), so lean toward securing your
      own — or commit TWO units to the rival on distinct cells so one survives.
    * State your reasoning in "reasoning": which fork, and WHY (score, weapons,
      player count). This is exactly the judgement the menu hands to you.

  ============================================================
  WEAPONS SET THE CADENCE (defensive read):
  ============================================================
    * CHAFF seen -> a H1 smash can be cancelled. KEEP A BACKUP WAVE: always
      carry UNBEATEN_FLANK (or a later HOT DROP) so one jam does not zero your
      night.
    * EMP seen near the seam -> the patterns already SPACE later waves past the
      cloud window and FLANK the fresh probe OUTSIDE the blast. Do not schedule
      two pickups inside the same jam window.

  THE IDEA (no weapons): get in there and harvest SOMETHING. The ONLY things
  that pull a harvester off this contest are ANOTHER redsign elsewhere or a
  genuinely juicy mass CHAIN (CHn) — then spend ONE harvester there instead.

  DISJOINT PATHS: whenever two of your harvesters work the same seam, keep their
  chains >=2 cells apart so they never share a cell — a friendly collision is
  the same zero-bank pile-up as diving the beacon.
"""


DOCTRINE_DROP_ON_VALUE = """\
DROP ON THE VALUE — the landing cell is auto-harvested (free parcel #1):
  When a harvester DROPS onto a cell, the engine harvests THAT cell immediately
  (drop counts as harvest #1), and every step after also harvests its
  destination. So the drop cell is not a staging square — it is your FIRST
  parcel. Two consequences:
    * DROP DIRECTLY ON the highest-value reachable cell (pure > mass > vein).
      Do NOT drop on trace/edge and "walk in" to the good stuff — that wastes
      parcel #1 on junk and exposes the pure for extra hours.
    * SECURE A PURE SHORT. When a pure (or very high-tier) cell is reachable,
      drop ON it FIRST and pick up FAST — 0 extra steps under chaff/EMP/contest,
      1-2 only when clearly safe. The auto-harvested pure sits in the harvester's
      HOLD and only banks to your hoard at PICKUP, so a long chain leaves the
      whole load one crash/chaff/collision from being spilled. Strip the wider
      seam with a SECOND harvester or a later wave, never the grabbing unit.
  Exception: never drop/step onto GREEN or synthetic-green (that auto-banks a
  -100 parcel). Value-first, but hazard cells are still off-limits.

  PRIORITY RED GRABS are the top of your value pyramid — ids GRAB1, GRAB2, … —
  and they list every pure / mass RED you can already SEE (LIVE) or REACH (ECHO
  walk-in), pre-built as drop+walk with NO probe. If a RED GRAB is offered it is
  almost always your FIRST pick: take the pure/mass you can see before anything
  speculative (a fresh probe, a fogged frontier, a supersede). A GRAB tagged
  WALK_IN drops on a LIVE frontier cell and walks ONTO a fogged/echo pure — that
  is the CORRECT way to take a pure that is not drop-legal, NOT the "drop on
  junk" mistake (the frontier cell is empty, the pure is the prize). Only skip a
  RED GRAB when a redsign pattern already secures that same pure, or a rival will
  clearly beat you to it this hour.

  HIGH-YIELD BLUE GRABS carry their OWN ids — BL1, BL2, … — never GRAB*. They are
  a SEPARATE, lower group: rich blue you can SEE and grab with zero risk, but RED
  always outranks blue for a scarce harvester. "Grab it FIRST" above is about
  GRAB*, and says nothing about BL*. A BL* only appears when orbital asks for
  blue (vault low) OR you have a spare harvester beyond your strong red chains.
  Take one when you NEED blue or a harvester would otherwise idle / gather
  low-yield red — never over a red chain or a redsign that could use that unit.

  CHOOSE THE COMB SHAPE. A hot drop may appear on the menu as three variants of
  the SAME drop — pick exactly ONE per drop; the length/area is YOUR call:
    * STRETCH (…L) — long straight-ish line, MAX new intel/area. Use on a BLIND
      bluesign/fog gamble where you don't know where the value sits and want to
      see + sweep the most ground.
    * SWEEP (…T) — tight dense serpentine hugging the drop. Use when you have
      LANDED ON a known cluster and just want to strip it.
    * SAMPLE (…Q) — quick 2-step in/out. Use on a HOT/CONTESTED cell: grab what
      you can and lift before a rival collision or chaff zeroes the load.
"""


DOCTRINE_FULL_UTILIZATION = """\
DEPLOY THE WHOLE FLEET (modus operandi) — you have more than one harvester
tonight, so get EVERY one onto the board. An idle harvester in orbit banks
nothing; a wasted unit is a wasted night. Give each its OWN target (a JUICE
CHAIN, a HOT DROP, or a seam wave) and put an ID for each into "plan". Keep two
harvesters' paths >=2 cells apart so they never share a cell (a friendly
collision banks ZERO, same as diving a beacon). Length follows VALUE — a thin
seam is a SHORT chain, never a padded walk — but the default is: all harvesters
out, every night.
"""


DOCTRINE_MULTIPROBE = """\
DEPLOY EVERY SPARE PROBE, AND SPREAD THEM (vision is how you win the NEXT night).
A probe launches from orbit and does NOT cost your harvesters' walk hours — an
idle probe in stock is simply blindness you chose. So after the probe(s) your
seam pattern already uses, put a PR id into "plan" for EACH remaining probe in
stock. TWO rules:
  * SPREAD, don't dogpile. One flank probe on the seam you are already working
    is plenty; every extra probe stacked on the SAME region is wasted vision.
    Send the rest to NEW ground — a DIFFERENT signal (a bluesign cluster, a
    second redsign), a fresh fog centroid, or a scouting edge — so tomorrow you
    can act on the whole map, not one corner.
  * SUPERSEDE (SS) to deny, not just on the last night. Any night you hold a
    SPARE probe (>1 probe, or your red chains already bank high so a probe is
    free), spend it landing ON a rival's probe to BLIND it — deny their next
    landing + vision. If you are TRAILING, blind the LEADER's freshest probe
    first; never waste one on a probe about to expire (its vision is spent).
    On the FINAL night this is the DEFAULT for every spare probe — there is no
    "next night" to see for, so blind the enemy's last harvest.
Selecting only one PR while probes sit in stock is the single most common way
this seat throws away a night's vision. Match the number of PR/SS ids to your
probe stock.
"""


DOCTRINE_WEAPONS_ORBITAL = """\
KNOW WHO HAS WHAT — weapons are ORBITAL strikes, not ground units:
  Opponents are in ORBIT. EMP and chaff are launched FROM orbit and can hit
  ANYWHERE on the map regardless of where an opponent's units sit. "They're
  clustered on the far side, so low risk" is a MISREAD — range is not the
  variable. The real question is: does THIS seat have stock, and do you
  present an obvious aim point?
    * A public REDSIGN + your probe drop on the beacon is a BULLSEYE: it tells
      a weapon-capable seat EXACTLY where to aim an EMP. Prefer offset drops.
    * Read OPPONENT WEAPONS per-seat: a FIRED launch is a FACT (that stock
      existed and was spent); an un-fired [min..max] build estimate is an
      ESTIMATE, not certainty. Treat fired = known threat, estimate = hedge.
    * Match the reaction to the seat that actually has the weapon, not to a
      global "someone might" — if only p3 has chaff and p3 is not contesting
      your seam tonight, do not shorten every chain.
  See WEAPON GEOMETRY for the exact blast radius / durations, and EMP SCARS
  for cells to route around this coming night.
"""


# Deliberately the shortest of the three BEWARE blocks, and the only one
# that ends with a single instruction.
#
# The first draft carried a four-bullet mitigation section — land offset,
# spread across distinct cells, assume the rack is not spent — which is a
# defensive playbook against a weapon the agent cannot see coming. SNAP
# is one cell chosen by somebody else with no tell; there is no read to
# make, so that section could only buy timid play across the board, paid
# for out of the attention the seat needs for harvesting.
#
# What survives is the part that is actionable BEFORE the fact, and it is
# one line: a grab resting on one probe rests on one deletable cell.
# Everything else about a SNAP is learned afterwards from the combat feed
# (``snap_hit``), which is why that renderer is worth more here than any
# amount of doctrine.
DOCTRINE_BEWARE_SNAP = """\
OPPONENT WEAPONS — beware_snap (one square, taken off you first):
  What it does to you:
    * ONE missile at ONE cell, resolved there BEFORE the hour's vision
      snapshot and before every drop, step and pickup on it.
    * A probe on that cell is DESTROYED BEFORE IT SEES. The sight it
      would have given never exists, so a landing that depended on it
      is refused for want of vision — not blocked, unsighted.
    * A harvester on the cell, or stepping into it, is DAMAGED and
      harvests nothing. A landing into it is turned back: the hull
      stays in orbit, damaged, its outing unspent.

  The one thing that CAN be done in advance, if you judge it worth a
  probe: a grab resting on a SINGLE probe rests on a single cell a rival
  can delete for 100 blue, the cheapest thing on the ladder. A SECOND
  probe that sees the same cell from a different one keeps the sight
  when the first is taken — SNAP lands on one square, so it cannot have
  both.

  When the menu can build that cover it offers it as PRSNAP*, and it is
  an OPTION, not an instruction. It banks nothing, it costs a probe and
  an hour on ground you can already see, and it is worth nothing at all
  if the rival never bought a SNAP — which you cannot know, only bound.
  Weigh it like any other play: how sorry you would be to lose this
  landing, against what else that probe could open. Declining it is a
  perfectly good answer on most nights.

  Beyond that, do not re-plan around SNAP. You cannot see it coming, and
  the rest of what it did you will read in the combat feed afterwards.
"""


DOCTRINE_WEAPONS_MULTIWAVE = """\
WEAPONS => MORE UNITS ON THE SEAM, NOT ONE SHORTER CHAIN (offensive read):
  The defensive rules above (short chains, dodge predictable pickup windows) are
  how a SINGLE unit survives. They are NOT the whole answer, because a lone
  chain — however short — is ONE EMP or ONE chaff away from a zero night. When a
  weapon is likely on a contested seam, the winning move is REDUNDANCY ACROSS
  HARVESTERS: commit MORE of the fleet to the SAME seam on DISTINCT cells so a
  strike that catches one wave still leaves another banking.
    * SPLIT THE TARGETS. Send one harvester to GRAB THE PURE (short, danger-gated
      drop+lift) and a SECOND to work the surrounding MASS on a different cell /
      bearing. Pure and mass are different cells — hitting BOTH with two units is
      not a repeat, it is coverage. Add a THIRD wave (later HOT DROP / flank
      probe) past the blast window if you have the unit.
    * DISTINCT CELLS, DISJOINT PATHS (>=2 apart). Two units must never share a
      drop or a step cell — the packager/sanitizer will delete the second as a
      self-collision, and in the engine it is the same zero-bank pile-up as
      diving the beacon. Redundancy means DIFFERENT cells on the same seam, never
      the same cell twice.
    * STAGGER PAST THE WINDOW. Space the waves so their pickups do not all fall in
      one jam window (the flank/late wave lands AFTER the likely EMP/chaff hour).
    * BEING JAMMED IS NOT DEATH. An EMP'd harvester KEEPS its haul and can still
      be picked up; everything it banked before the strike comes home. So a second
      committed unit is cheap insurance, not a gamble — the downside is a few
      hours, the upside is you still bank if the first wave is jammed.
  The failure this fixes: on a weapons-likely night the seat retreats to ONE
  timid short chain, gets it jammed, and banks nothing while two harvesters sat
  idle in orbit. Deploy the fleet ONTO the seam; let the strike waste itself on
  one wave.
"""


DOCTRINE_SCORCH = """\
FIRE AN EMP — you are holding a charge, and a charge in the rack scores 0.
Every rule above this one is about SURVIVING someone else's weapon; this one
is about spending yours. You own EMP stock (see YOUR RACK).

FIRST QUESTION: is a REDSIGN live tonight? Check the OPTION MENU for a
[BLIND_SCORCH] / [SCORCH_REDSIGN] / [SMASH_THEN_LOCK] / [RACE_CRASH_EMP]
entry. If any of those appear, a redsign is on the board and this section's
REDSIGN CASES below are the ONLY correct EMP plays. If none appear, no
redsign is live and the QUIET NIGHT rule at the bottom applies.

  ┌─────────────────────────────────────────────────────────────────────┐
  │ REDSIGN-LIVE rule — never fire EMP_SCORCH away from the redsign     │
  │ when a redsign fight is on the board. EMP_SCORCH kills far-away     │
  │ probes for TOMORROW's vision denial. That is worth an hour on a     │
  │ quiet night. On a redsign night it is worth almost nothing: the     │
  │ rival is coming for the pure at THIS beacon, not for the ground     │
  │ their scattered probes light. Denying their side probes leaves the  │
  │ redsign contest unchanged — you spent a charge to affect ground     │
  │ nobody is fighting for. The charge belongs at the beacon.           │
  └─────────────────────────────────────────────────────────────────────┘

  WHAT IT COSTS. One charge and — the part that decides most calls — ONE of
  your 21 hour-slots. A launch pre-empts the seat for that hour (§3.10): you
  fire OR you move, never both. So the honest comparison is never "EMP vs
  nothing", it is "EMP vs the chain I drop instead". Price it that way — and
  price the whole play, not the salvo in isolation: SMASH_THEN_LOCK and
  RACE_CRASH_EMP each carry a REAL red yield on the deferred harvester wave,
  and the salvo is what buys that ground.

  THE THREE REDSIGN CASES — pick by REDSIGN STATE:

  CASE A — the redsign is NOT YOURS and you cannot see the pure:
    * The correct play is [BLIND_SCORCH] in the EMP SALVOS block (shaped
      salvo + probe + drop on the hole). Not EMP_SCORCH, not SCORCH_REDSIGN,
      not a probe attack — BLIND_SCORCH.
    * BLIND_SCORCH IS the shield play. The three missiles are placed so a
      single cell of the smear stays clear, you probe that cell (on the rim
      so the landing does not crush it), and drop the harvester on it. For
      the next 8h that hole is a cell ONLY you can walk on, and the whole
      smear is dark for every rival converging on the beacon. When the
      cloud lifts you comb outward from the middle of the seam while they
      are still in orbit.
    * The yield line on BLIND_SCORCH reads "red ~+0" because the comb is
      blind. Do NOT read that as "worth nothing". The play banks the
      halo when the cloud lifts AND denies the smear to 3 rivals for 8h.
      Its real value is 400-600 red + a full night of rival tempo lost.
      A blind gamble at BLIND_AND_GRAB banks similar expected red WITHOUT
      the denial and WITHOUT the hole-occupation certainty. BLIND_SCORCH
      dominates it whenever you own a charge.
    * DO NOT delay the landing to cloud-lift. A harvester in orbit is a
      harvester banking nothing — one outing per unit per night (§3.9.2),
      so waiting until H9 doesn't buy you a second play, it just starts
      the chain later on unprobed ground. BLIND_SCORCH's H3 landing is on
      a cell you PICKED (the hole) with a probe that lit it. That
      certainty is worth more than the 6 hours of "flexibility" a delayed
      drop pretends to offer.
    * Prefer BLIND_SCORCH over BLIND_SCORCH_T (which lands the harvester
      ON its own probe and crushes it, going blind mid-cloud). Prefer
      BLIND_SCORCH over SCORCH_REDSIGN (blanket denial, no hole, no
      follow-up).
    * DO NOT run BLIND_SCORCH if you have your OWN live probe on the pure:
      you have a smash-grab in that case, take the certain +765 first.

  CASE B — YOUR REDSIGN, and you are alone or dominant (0-1 rivals):
    * Menu offers SMASH_THEN_LOCK (H1 smash + H3 halo salvo + cloud-lift 2nd
      unit comb).
    * SMASH FIRST — the H1 landing on YOUR pure beats every weapon. Certain
      +765. Only chaff denies it.
    * H3 EMP the HALO around the pure, hole = pure. The three missiles are
      placed so the pure cell stays clear while the ring of mass around it
      goes dark. Result: your rivals cannot work the halo for 8h; your
      second harvester can. The pure was banked at H2, so the salvo denies
      nothing you would have taken yourself.
    * If you have no 2nd harvester the pattern still runs as smash + lock
      (2 waves): the pure is banked and the seam is still yours next night.
    * NEVER SCORCH YOUR OWN PURE. The salvo is aimed at the halo AROUND it;
      any menu play that darkens the pure itself locks you out too.

  CASE C — SHARED VISION: you and >=1 rival both see the pure:
    * Menu offers RACE_CRASH_EMP (H1 drop expecting crash + H3 salvo with
      hole=pure + cloud-lift 2nd unit re-drop).
    * H1 you both drop on the pure. If you land, +765. If you mutually
      collide, both harvesters are damaged and both spill — but AERIAL
      DENIAL DOES NOT CONSUME THE PURE (§4.9), it is still on the board.
    * H3 EMP the halo, hole = pure. Rivals cannot walk in for 8h; you can.
    * Cloud-lift, your second harvester drops on the pure. Either way (you
      landed at H1 OR you crashed and re-took at H9), the pure is banked
      and every rival lost their whole night on it.
    * REQUIRES TWO HARVESTERS. With one, fall back to SMASH_THEN_LOCK — a
      crash-and-recover you cannot recover from is a night thrown away.
    * SKIP IF CHAFF IS IN PLAY. Chaff cancels the H1 landing outright, and
      the second-unit re-drop cannot follow a landing that never happened.
      The pattern is not offered on a chaffed night; heed that.

  QUIET NIGHT (no redsign anywhere on the board): kill the freshest eye
  with EMP_SCORCH. Launch it on the newest rival probe. Probes die the
  hour the cloud forms, so an early kill costs the rival a NIGHT of vision
  they have not yet converted into a landing. An old probe has already told
  them what is there — scorching it burns an hour to destroy information
  they already used. Freshest first. This is a one-move play (no harvester),
  so the hour cost is compared against ONE chain you drop instead. Take it
  when killing the eye is worth more than the RED that chain would have
  banked. IF A REDSIGN IS LIVE this rule DOES NOT APPLY — go to the REDSIGN
  CASES above.

  BLIND_SCORCH's shorter siblings (fallback plays only):
    * SCORCH_REDSIGN is a blanket salvo over a rival smear with no hole
      and no follow-up. Weakest of the salvo family — offered because it
      is legal, but BLIND_SCORCH almost always dominates.
    * EMP_SCORCH (probe-kill away from the beacon) is the QUIET-NIGHT
      play. On a redsign night the charge belongs at the beacon; using
      it on far probes is the failure mode this doctrine exists to
      prevent.

  FIRE IT FIRST OR DO NOT FIRE IT — a salvo is worth its slot at hour 1 or
  hour 3, and close to nothing after that. Probes die on CONTACT so an
  early kill costs them a night of vision; landings happen early so darkened
  ground only denies anything while they still have hours to land in; and a
  cloud lit at hour 15 loses most of its eight hours to Aurora. If the only
  slot you can spare is late in the night, the honest read is that you do
  not have room for one tonight — keep the charge and spend the hour on a
  chain.

  WHEN NOT TO — the two ways this play loses you the game:
    * NEVER SCORCH YOUR OWN REDSIGN when you can grab it. Grading in the
      compound patterns keeps the pure clear (hole = pure). A blanket
      SCORCH_REDSIGN on your own sign locks your harvesters out of your own
      +765 for 8h to deny a rival who may not even be coming.
      Certain RED outranks speculative denial every time.
    * NEVER FIRE INTO YOUR OWN NIGHT. Friendly fire is on. Your harvesters
      go 'empd' in your own cloud — hour spent, nothing banked — and your
      own probes are DESTROYED by it. The compound patterns handle this
      for you (deferred waves land AFTER the cloud lifts, rim probes sit
      OUTSIDE the blast); do NOT hand-schedule a walk-in for hour 4 of a
      cloud that clears at hour 9.

  THE CLOUD'S EIGHT HOURS ARE NOT YOUR EIGHT HOURS. You get ONE action per
  hour across the WHOLE FLEET (§3.10), and the queue is a single interleaved
  timeline. A harvester holding a hole (or waiting for cloud-lift) is one
  unit standing still while every other unit works. So a night built around
  a compound scorch should ALSO pick a full slate of chains and probes
  somewhere else on the map. The compiler splices the held walk in at the
  exact hour the cloud lifts and pushes your other moves out behind it —
  you only have to GIVE IT ENOUGH TO DO. If the log says it inserted WAITs,
  that is the night telling you the plan was too thin, not that the scorch
  was wrong.
"""


__all__ = [
    "STRATEGIES_CORE",
    "DOCTRINE_BLUE",
    "DOCTRINE_SCORCH",
    "DOCTRINE_REDSIGN",
    "DOCTRINE_LASTDAY_SUPERSEDE",
    "DOCTRINE_BEWARE_EMP",
    "DOCTRINE_BEWARE_CHAFF",
    "DOCTRINE_BEWARE_SNAP",
    "DOCTRINE_COMPREHENSION",
    "DOCTRINE_CERTAINTY",
    "DOCTRINE_RISK_LADDER",
    "DOCTRINE_REDSIGN_POKER",
    "DOCTRINE_DROP_ON_VALUE",
    "DOCTRINE_FULL_UTILIZATION",
    "DOCTRINE_MULTIPROBE",
    "DOCTRINE_WEAPONS_ORBITAL",
    "DOCTRINE_WEAPONS_MULTIWAVE",
]
