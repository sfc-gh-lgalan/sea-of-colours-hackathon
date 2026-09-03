"""Advisory doctrine — the playbook the agent SHOULD follow.

Strategies are NOT enforced by the engine. They are our best current
understanding of how to play well, kept separate from ``rules.py`` so the
agent knows exactly which lines are physics (immutable) and which are
patterns (optional).

v5 change — STATE-TRIGGERED doctrine. Instead of one always-on wall of
text, the doctrine is split into:

  * ``STRATEGIES_CORE`` — lean and always shown. The objective, the
    per-turn procedure, the probe mandate, the harvester EV ladder, and
    the one canonical hot-drop statement. This is what the agent needs
    EVERY night regardless of situation.

  * Conditional appendices — appended by ``prompt.build_prompt`` ONLY when
    the live state calls for them, so haiku never burns attention on
    doctrine it can't act on tonight:
      - ``DOCTRINE_BLUE``        setup night OR the orbit wishlist raised
                                 ``grab_blue`` (the agent never decides its
                                 own blue-need; the orbit bot does).
      - ``DOCTRINE_REDSIGN``     a pure-RED beacon is broadcast on the map.
      - ``DOCTRINE_BEWARE_EMP``  the opponent-weapon tracker flags EMP stock.
      - ``DOCTRINE_BEWARE_CHAFF``the opponent-weapon tracker flags chaff.

Adding to this file? Ask yourself: "would the engine accept a move that
violates this?" — if yes it's a strategy; if no it's a rule. And: "is
this always true, or only in a specific situation?" — always-true goes in
CORE; situational goes in a gated appendix.
"""

STRATEGIES_CORE = """\
STRATEGIES (playbook — advisory, not enforced by the engine)

YOUR OBJECTIVE — spend every resource on RED:
  You control N harvesters (~7 usable hours each) and M probes (1 hour
  each). Every hour is spent on exactly ONE of:
    (a) BANK RED now      — a harvester chain that ends in pickup.
    (b) REVEAL RED later  — a probe into fresh fog for a future night.
    (c) PROTECT a bank    — get a loaded harvester home safely.
  Idle hours, sub-par chains, and probes left in the magazine are how you
  lose. Maximize (RED banked tonight + RED you set up for coming nights).

PER-TURN PROCEDURE — run this every night:
  1. ENUMERATE opportunities: visible RED chains (CHAIN HINTS), hot-drop
     combos (HOT DROP HINTS), any REDSIGN race, and fog worth probing
     (PROBE HINTS). The HINT blocks below are your candidate menu.
  2. SCORE each with the tier table in RULES (purity x tier_mult). You
     compute EV yourself; the hints carry no scores.
  3. ASSIGN each harvester its highest-EV chain. Take the best RED you
     can safely finish.
  4. FILL leftover hours with probes into the best fresh fog (see PROBE
     MANDATE). Do not leave probe stock unused while fog remains.
  5. SAFETY PASS: every chain ends in pickup THIS night; trim any chain
     you can't finish inside 21 hours.

PROBE MANDATE — under-launching is the cardinal sin:
  A probe reveals a Euclidean radius-4 disk (~49 cells) for 3 nights.
  Its purpose is to surface NEXT NIGHT'S harvest target — not to keep
  vision over ground you already worked. A probe you don't launch tonight
  is RED you can't harvest tomorrow, so if you have stock and there is
  fog, LAUNCH:
    * probe_stock >= 2 AND fog_count > 300  -> launch AT LEAST 2 probes.
    * probe_stock >= 1 AND fog_count > 150  -> launch AT LEAST 1 probe.
  Placement:
    * Into FRESH fog — prefer high area_gain (the disk lands mostly on
      cells you've never surfaced). Overlap with synthetic-green or
      already-visible ground is a wasted probe.
    * A DIFFERENT region from tonight's chain — range the map, don't
      double-cover. Two probes with overlapping disks reveal the same
      ground; spread them.
    * Just past the LOS edge of a pure/mass cluster — RED runs in seams,
      so edge_promise flags likely seam extensions.
  Re-probing already-explored ground is only justified when your probe
  was DESTROYED (EMP / crush / chaff) before you harvested its disk —
  read LAST NIGHT.my_assets_destroyed. Otherwise: move the frontier.
  Skip probing only when fog_count is 0, or the best candidate's
  area_gain is tiny (~<15), or on the FINAL night when the reveal would
  only pay off on a night that never comes (a same-night hot drop still
  works on the final night).

HARVESTER TARGET SELECTION — highest EV first:
  Rank each harvester's options and take the best it can safely finish:
    1. A REDSIGN race, if a REDSIGN block is present (see that block).
    2. The best visible RED chain (mass/pure > vein > trace).
    3. A hot drop onto strong echo/signal RED (see HOT DROP).
    4. Blue — ONLY if a BLUE block is present this turn (see that block).
  A pure(255) cell is 2295 base pts and usually overrides a normal chain
  — but never abandon or crash a loaded harvester to chase it.

MAXIMIZE THE CHAIN — a committed harvester must EARN its trip:
  A harvester has ~7 usable hours. A drop is expensive (and a hot drop
  also spent a probe), so once you commit one, WALK. Spend as many hours
  as you can safely finish stepping through RED before pickup: a chain
  that banks 1-2 cells and picks up at hour 3-4 wasted 3-4 harvest-hours
  and, on a hot drop, the whole probe. Default target: fill the
  harvester's hours (drop + ~5 steps + pickup), harvesting the densest
  reachable RED seam. Go SHORT only for a real reason (see below).

CRASH PREVENTION — never lose a harvester, but don't be timid:
  Every drop is a commitment to a pickup THIS night. Before submitting a
  chain, count hours_used = 1 (drop) + steps + 1 (pickup); if that
  exceeds the hours you have left after other actions, TRIM the tail so
  the pickup still lands. That is the ONLY reason to shorten a chain by
  default — a chain that fits its hours should be LONG, not short.
  Shorten deliberately ONLY when CONTESTED: OPPONENT INTEL shows enemy
  probes/drops near your target, a BEWARE_EMP/CHAFF block is live, or you
  are racing a REDSIGN. Then drop, grab the 1-2 best cells, pickup early —
  surface time is exposure time. Absent a threat, long chains win.

DON'T CRUSH A PROBE YOU STILL NEED:
  Dropping OR stepping a harvester onto a cell where YOUR OWN probe sits
  DESTROYS that probe and its vision. This is a deliberate trade, not a
  free landing pad:
    * OK to crush when the probe is SPENT — it expires tonight
      (nights_remaining <= 1), its disk is already worked out, or the
      juicy cell you want is UNDER the probe (you must land/step on it to
      grab it). Take the loot; the probe was done anyway.
    * DON'T crush a probe with 2+ nights left just to use its centre as a
      landing pad. If the loot is elsewhere in the disk, land on an
      ADJACENT drop-legal, non-green cell and step to the loot — you keep
      the probe AND the vision it gives you next night.
  The DROP-LEGAL block flags cells that sit on your probes; check it.

DON'T COLLIDE YOUR OWN HARVESTERS:
  Two friendly harvesters that land on, or step through, the SAME cell in
  the same window COLLIDE — both take damage and return with ZERO
  parcels. Give each harvester a DISJOINT chain: keep their paths at
  least 2 cells apart and never route two of them onto a shared cell.
  Plan the full route of harvester A before you place harvester B.

GREEN IS POISON — never step or drop on it:
  Harvested RED becomes GREEN (synthetic-green). Dropping or stepping on
  ANY green cell (natural or synthetic) auto-banks a -100 endgame parcel
  for nothing. Never walk a chain back over the cells you just harvested
  (your own green wake), and route around synthetic-green shown in the
  VISIBLE RED / DROP-LEGAL blocks.

FINAL NIGHT (day == last day) — no tomorrow:
  A probe launched on the final night reveals ground you will NEVER
  harvest (there is no next night). Do NOT spend probes on frontier
  vision. Spend the night converting every reachable RED/BLUE into banked
  points, and if you have spare probe stock, use it to SUPERSEDE enemy
  probes (see the LAST-NIGHT block when present) rather than wasting it.

HOT DROP — one mechanic, two uses:
  Live-vision refreshes every hour, so you can reveal-then-harvest in the
  SAME night:
    hour K:   probe(at=[x,y])            -> its disk becomes live-vision
    hour K+1: drop(harvester at=[x',y']) where (x',y') is in that disk
  The probe MUST appear BEFORE the drop in your moves list, and the drop
  target MUST be inside the DROP-LEGAL zone the probe creates.
  DO NOT drop on the probe's OWN cell [x,y] — that CRUSHES the probe you
  just paid for and throws away its next 2 nights of vision. Land on an
  ADJACENT in-disk cell instead (the HOT DROP HINTS give a crush-safe
  drop_at). Only land on the probe cell itself if the richest revealed
  RED is on that exact cell.
  Then WALK the seam: the fresh probe reveals ~49 cells, so spend as many
  of the harvester's remaining hours as you can safely finish stepping
  through the densest RED — a hot drop that walks only 1-2 cells wasted
  the probe. Pickup only when you're out of safe hours (or contested).
  Its two best uses are racing a REDSIGN and sampling a bluesign cluster
  (see those blocks when present).

GLOBAL PRIORITY when options conflict:
  REDSIGN race (if reachable safely) > best visible RED chain > probe the
  frontier > blue top-up (only if a BLUE block is present) > protect /
  short-chain when contested. Never trade a guaranteed bank for a gamble
  that risks a crash.
"""


# ─────────────────────────────────────────────────────────────────────────
# CONDITIONAL APPENDICES — appended by ``prompt.build_prompt`` only when the
# live state makes them relevant. Kept as separate module constants so each
# can be gated independently. When one of these appears in the prompt it is
# relevant THIS night; when it is absent, that concern does not apply.
# ─────────────────────────────────────────────────────────────────────────


DOCTRINE_BLUE = """\
BLUE HARVEST — active THIS night (setup night, or orbit asked for blue):
  This block appears only because it is setup night OR the orbit turn
  raised a grab_blue priority (your BLUE vault funds weapons + orbital
  repairs and is running low). You do NOT decide blue-need yourself — the
  orbit bot does, via the wishlist. When this block is present, spending
  ONE harvester on blue is on the menu; otherwise keep every harvester on
  RED.

  Which harvester: the "spare" one — the harvester whose best available
  RED chain is weakest (roughly < 400 pts). If every harvester has a
  strong RED chain (> 600 pts), take the RED and let blue wait a night.

  BLUESIGN — how to find blue (public, static, from day 1):
    bluesign is a map of blue clusters, each 20-40 cells with per-cell
    intensity 0.0-1.0. Bright cells (>= 0.6) almost certainly have real
    BLUE nearby, but the exact purity is RANDOMIZED — you know blue is
    "somewhere dense in this cluster," not which exact cell is rich.
  Recipe (a hot drop):
    1. probe the brightest bluesign cell (hour K).
    2. drop the spare harvester into that disk (hour K+1).
    3. crawl 1-3 steps sampling neighbors (some are trace, some rich).
    4. pickup. Blue uses the SAME drop -> step* -> pickup grammar and the
       SAME crash rule as RED.

  SETUP NIGHT: a bluesign hot drop is a strong night-1 move even with
  zero vision — you're gambling on known-blue territory instead of blind
  fog. Pair it with 2+ probes into different quadrants.

  Skip blue when you already have >= 3 blue cells in live-vision (a known
  chain beats a bluesign gamble), or on the final night if the payoff is
  deferred.
"""


DOCTRINE_REDSIGN = """\
REDSIGN — a pure-RED beacon is LIVE on the board (RACE IT THIS NIGHT):
  This block appears because the engine broadcast a REDSIGN: some seat
  found a pure(255) RED cell and its coord is PUBLIC to everyone. A pure
  cell scores 765 x 3.0 = 2295 base pts and pure runs in SEAMS, so the
  cells around it are usually mass/pure too — this is the single biggest
  bank on the board and every opponent is coming for it. Act on turn ONE.

  IT IS ALWAYS REACHABLE — do not talk yourself out of it. A probe you
  launch creates a fresh drop-legal disk ANYWHERE on the map, so you can
  hot-drop onto the beacon THIS night regardless of where your harvesters
  are. "Too far" is not a reason to skip it; scouting-toward-it-for-
  tomorrow is the WRONG play (someone banks it tonight).

  THE PLAY (same night):
    1. PROBE the beacon area. Launch a PRIMARY probe so the pure cell is
       in live-vision, PLUS (if you have stock) a BACKUP probe offset a
       few cells away, outside the primary's Manhattan-r2 blast — the
       public coord invites an EMP, and the backup keeps the cell
       droppable if the primary is fried.
    2. HOT DROP your best harvester into that disk at the NEXT hour.
       Land ADJACENT to the probe centre (NOT on it — that crushes the
       probe), or on a pure/mass cell that is not the probe centre.
    3. STRIP THE SEAM. Walk through the pure/mass run, harvesting every
       high-tier cell you can safely reach — this is a long chain, not a
       1-cell snatch. Bank the whole seam, not just the beacon cell.
    4. TIMING: if a BEWARE_EMP block is ALSO present, the area will likely
       be EMP'd hours 3-8 — then go fast (hour-1 snatch of the best 2-3
       cells, pickup by hour 4) or run a second wave at hour 9+. With NO
       EMP flagged, take the time to strip the full seam.
    5. OVERRIDE, with a limit: the pure seam overrides an ordinary planned
       chain — but NEVER abandon an already-loaded harvester or stretch a
       chain into a dawn crash to chase it.
"""


DOCTRINE_LASTDAY_SUPERSEDE = """\
FINAL NIGHT — SUPERSEDE ENEMY PROBES (deny their last harvest):
  This block appears because it is the LAST night of the season. A probe
  you launch tonight gives NO future vision (there is no tomorrow), so
  spare probe stock is wasted on frontier scouting. Instead, weaponise it:
    * A probe dropped ONTO a cell holding a PRIOR enemy probe DESTROYS and
      SUPERSEDES it (RULEBOOK §3.16) — your probe survives, theirs is
      gone, and they lose vision + the ability to drop/hot-drop into that
      disk on this final night.
    * The SUPERSEDE HINTS block lists the freshest known enemy probe
      cells (their launches are public). After you have committed every
      harvester to its best RED/BLUE chain, spend each LEFTOVER probe
      launching directly onto one of those enemy-probe cells — freshest
      first (their newest probe covers the ground they most want tonight).
  PRIORITY: banking your own RED/BLUE comes FIRST. Only supersede with
  probe stock left over after your harvest chains are placed — never trade
  a harvester's harvest hour for a supersede. Superseding costs only probe
  stock and an hour, and a denied enemy harvest can swing a close game.
"""


DOCTRINE_BEWARE_EMP = """\
OPPONENT WEAPONS — beware_emp (vision + landing denial):
  What EMP actually does:
    * EMP is used for VISION DENIAL and LANDING DENIAL. It is RARELY
      aimed at a moving harvester — random hits are unlikely.
    * Manhattan-r2 blast (13 cells per missile x 3 missiles per launch).
      Inside the blast: probes are DESTROYED; harvesters are
      DISABLED for 8 hours (or the rest of the night).
    * CRITICAL: an EMP'd harvester RETAINS its haul and can still be
      picked up. Being EMP'd is NOT death — only a dawn crash kills the
      harvester and its cargo. Cells you banked before the EMP come home
      when the harvester is picked up.
    * Most likely target: YOUR LATEST launched probe — a blind-fire
      stall to deny your next-night landing. Second: an area of dense
      RED the opponent wants to shield from you for 8h.

  FLAVOR A — REDSIGN fired (pure red is public, everyone is coming):
    Assume EMP aims at the pure area around hour 3-8.
    * HOUR-1 DROP: race to bank pure before EMP fires — short chain,
      pickup by hour 4.
    * OR HOUR-9+ DROP: probe fresh at hour 8, drop at hour 9 after an
      hour-1 EMP window has closed. Second wave.
    * Support with a SECOND probe OUTSIDE the primary probe's Manhattan-r2
      blast (offset above/below) so a lost primary doesn't strand you.

  FLAVOR B — no pure on the board (opponent blind-fires at probes):
    * Support every harvest with a second probe outside the primary's
      blast area. If chaining, avoid the primary probe's exact cell —
      that's their most likely aim point.
    * BUT if the primary probe's disk holds the best red on the board,
      drop directly on it at hour 1 — speed beats stealth.

  Universal: SHORTEN CHAINS when beware_emp fires. Grab the high-scoring
  parcels only. Faster in, faster out, less time on surface.
"""


DOCTRINE_BEWARE_CHAFF = """\
OPPONENT WEAPONS — beware_chaff (pickup-killer, avoid predictable windows):
  What chaff actually does:
    * Chaff cancels all OTHER seats' actions for 3 CONSECUTIVE hours
      (CHAFF_DURATION_HOURS = 3). The triggerer's own actions still
      resolve; only opponents lose the window.
    * If your PICKUP falls in a chaffed hour, the pickup is cancelled.
      The harvester stays on the surface, and if the window straddles
      your last legal pickup hour -> dawn crash + haul lost.

  Mitigation — avoid PREDICTABLE pickup windows (chaff is blind-fired at
  the hours opponents most naturally pick up):
    * Avoid HOUR 6-9  (the "drop at 1, walk 5, pickup at 7" pattern).
    * Avoid HOUR 12-16 (the "mid-night drop, mid-night pickup" pattern).
    * Prefer pickup at hour <= 4 (before the enemy has read the field) or
      hour >= 19 (less incentive to burn chaff on a near-empty night).
    * A short chain (drop -> 1-2 steps -> pickup at hour 3-4) is the
      strongest chaff-hedge — you're gone before the natural window opens.

  CRITICAL — SPACE MULTI-HARVESTER PICKUPS >= 3 HOURS APART:
    One chaff kills a 3-hour window. If two harvesters pick up within 3
    hours of each other, ONE chaff cancels BOTH -> double dawn crash.
    e.g. pickups at hour 4 and hour 8 (4-hour gap) — chaff catches at
    most one. NEVER pickups at hour 4 and hour 5.

  Universal: SHORTEN CHAINS when beware_chaff fires. Shorter chain =
  earlier pickup = smaller window for chaff to catch you.
"""
