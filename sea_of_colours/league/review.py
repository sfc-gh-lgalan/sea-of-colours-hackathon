"""The league as one shareable file — `reports/league/review.html`.

The third page, and the one for *after* the competition rather than
during it.

Why a third. `/league/` is the live bracket and needs a server, because
it reads a generated blob and links into real replays.
`league/announce.html` is the shareable snapshot, and is deliberately
thin — it is mostly hand-written prose with one baked block, so it can
be dropped into Slack before a single match is played. Neither is a
*record*: one needs infrastructure, the other is an announcement.

This is the record. Entirely generated, entirely self-contained, no
fetch and no sibling files, and it carries everything the league
produced — every tie, every season score behind a hover, and every
weapon anybody bought or fired. It is the file you send to seventeen
teams when it is over.

Two rules it inherits from announce.html and must keep: **no fetch,
ever** (the moment it depends on anything it stops being shareable,
which is the only reason it exists), and the design tokens come from
``manual/manual.css`` so it sits beside the guides without a seam.

One rule of its own: **it is output, not source.** Do not hand-edit it
— change this module and re-run ``soc cup site``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sea_of_colours.league import ROOT

#: Generated output, so it lives with the other generated league data
#: rather than beside the two hand-written pages in ``league/``.
REVIEW_PATH = ROOT / "review.html"

#: Weapon kinds in rack order (cheapest rung first), with the colour
#: token and the glyph their sticker wears. Order matters: it is the
#: order a fork meets them in, so a reader scanning a row of stickers
#: sees the same progression every time.
KINDS = (
    ("snap", "amber", "SNAP"),
    ("chaff", "blue", "CHAFF"),
    ("emp", "red", "EMP"),
)


def _weapons_in(match: dict) -> dict:
    """Per-agent ``{kind: [fired, bought]}`` for one match."""
    out: dict = {}
    for game in match.get("games") or []:
        for row in (game.get("stats") or {}).values():
            agent = row.get("agent") or ""
            if not agent:
                continue
            slot = out.setdefault(agent, {})
            for kind, _c, _l in KINDS:
                pair = slot.setdefault(kind, [0, 0])
                pair[0] += int((row.get("fired") or {}).get(kind, 0) or 0)
                pair[1] += int((row.get("bought") or {}).get(kind, 0) or 0)
    return out


def _seasons_in(match: dict) -> list:
    """The per-season scorelines behind a tie — what the hover reveals.

    Kept in the agents' terms rather than p1/p2, because "who won game
    two" is the question, and making the reader map seats back through
    the side swap is how a scoreboard gets misread.
    """
    out = []
    for game in match.get("games") or []:
        sc = game.get("scores") or {}
        out.append({
            "n": game.get("n"),
            "p1": game.get("p1") or "",
            "p2": game.get("p2") or "",
            "s1": int(sc.get("p1", 0) or 0),
            "s2": int(sc.get("p2", 0) or 0),
            "w": game.get("winner") or "",
            "sid": game.get("session_id") or "",
            "mins": round(float(game.get("seconds") or 0) / 60, 1),
            "fb": int(game.get("fallback_turns") or 0),
        })
    return out


def compact(data: dict) -> dict:
    """Everything the page draws, and nothing it doesn't.

    Trimmed rather than passed through: ``league.json`` carries the
    per-turn detail the live site needs for its popovers, and baking
    that into a file meant for email would make it megabytes.
    """
    fmt = data.get("format") or {}
    labels = fmt.get("stage_labels") or {}
    by_id = {m["id"]: m for m in (data.get("matches") or [])}

    rounds = []
    for rnd in data.get("bracket") or []:
        ties = []
        for tie in rnd.get("ties") or []:
            match = by_id.get(tie.get("id"), {})
            ties.append({
                "a": tie.get("a") or "", "b": tie.get("b") or "",
                "sa": tie.get("seed_a") or "", "sb": tie.get("seed_b") or "",
                "w": tie.get("winner") or "",
                "score": tie.get("scoreline") or "",
                "margin": int(tie.get("margin") or 0),
                "how": tie.get("decided_by") or "",
                "done": bool(tie.get("complete")),
                "seasons": _seasons_in(match),
                "weapons": _weapons_in(match),
            })
        rounds.append({
            "stage": rnd.get("stage") or "",
            "label": rnd.get("label") or "",
            "ties": ties,
        })

    entrants = {e["label"]: e for e in
                ((data.get("roster") or {}).get("entrants") or [])}
    wpn = {w["agent"]: w for w in (data.get("weapons") or [])}

    field = []
    for row in data.get("progress") or []:
        agent = row.get("agent") or ""
        if not row.get("played"):
            continue
        ent = entrants.get(agent, {})
        w = wpn.get(agent, {})
        if agent == data.get("champion"):
            fate = "champion"
        elif row.get("out_at"):
            fate = f"out at {labels.get(row['out_at'], row['out_at'])}"
        else:
            fate = "still in"
        field.append({
            "agent": agent,
            "team": ent.get("team") or "",
            "people": ent.get("participants") or [],
            "seed": row.get("seed") or 0,
            "fate": fate,
            "champ": agent == data.get("champion"),
            "won": row.get("won") or 0,
            "lost": row.get("lost") or 0,
            "sw": row.get("season_wins") or 0,
            "sl": row.get("season_losses") or 0,
            "margin": int(row.get("margin") or 0),
            "beaten_by": row.get("beaten_by") or "",
            "bought": w.get("bought") or {},
            "fired": w.get("fired") or {},
            "kills": w.get("kills") or {},
        })
    field.sort(key=lambda r: (not r["champ"], -r["won"], -r["margin"]))

    chal = []
    for m in data.get("challengers") or []:
        chal.append({
            "a": m.get("a") or "", "b": m.get("b") or "",
            "w": m.get("winner") or "",
            "score": m.get("scoreline") or "",
            "margin": int(m.get("margin") or 0),
            "done": bool(m.get("complete")),
            "note": m.get("note") or "",
            "for_slot": m.get("for_slot") or "",
            "seasons": _seasons_in(m),
            "weapons": _weapons_in(m),
        })

    # Agents credited with EMP kills that cannot be theirs, because they
    # never bought an EMP. Before v1.50 the SNAP's probe kills were
    # recorded in the ``emp_probes`` row — the kills are real and the
    # victims right, only the weapon is wrong. Rounds played before the
    # fix keep what their sessions recorded, so the page has to say so
    # rather than quietly relabel history. Computed, so the caveat
    # disappears on its own once every season is post-fix.
    mislabelled = sorted(
        r["agent"] for r in field
        if int((r["kills"] or {}).get("emp_probes", 0) or 0) > 0
        and int((r["bought"] or {}).get("emp", 0) or 0) == 0
    )

    health = data.get("health") or {}
    fired = sum(int((w.get("fired") or {}).get(k, 0) or 0)
                for w in (data.get("weapons") or []) for k, _c, _l in KINDS)
    bought = sum(int((w.get("bought") or {}).get(k, 0) or 0)
                 for w in (data.get("weapons") or []) for k, _c, _l in KINDS)

    return {
        "generated": datetime.now().astimezone().strftime("%a %d %b %Y, %H:%M"),
        "champion": data.get("champion") or "",
        "entrants": len(field),
        "matches": int(health.get("matches") or 0),
        "seasons": int(health.get("games") or 0),
        "fallback": int(health.get("fallback_turns") or 0),
        "fired": fired,
        "bought": bought,
        "best_of": fmt.get("best_of_by_stage") or {},
        "labels": labels,
        "kill_labels": fmt.get("kill_labels") or {},
        "rounds": rounds,
        "field": field,
        "challengers": chal,
        "mislabelled": mislabelled,
    }


# ── the page ───────────────────────────────────────────────────────
#
# One string, because it has to be one file. Written with __DATA__ as
# the only substitution rather than an f-string: the CSS is full of
# braces and escaping every one of them makes it unreadable and
# unpastable.

_PAGE = r"""<!doctype html>
<!--
  SEA OF COLOURS · KNOCKOUT LEAGUE — the full record
  ═════════════════════════════════════════════════════════════════

  GENERATED FILE. Do not edit.
    Source: sea_of_colours/league/review.py
    Rebuild: soc cup site

  Entirely self-contained: no sibling CSS, no sibling JS, no fetch,
  nothing that reaches the engine. Drop it in Slack, mail it, open it
  off a USB stick in ten years — it draws the same league.

  Hover any tie for its season-by-season scores.
-->
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Sea of Colours · Knockout League · The Record</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500&family=VT323&display=swap" rel="stylesheet" />
<style>
  /* Tokens from manual/manual.css, so this sits beside the guides and
     the announcement without a seam. Inlined, never linked. */
  :root {
    --bg: #0d0d12;
    --raise: #131319;
    --raise2: #16161d;
    --fg: #f0f6fc;
    --dim: #a8b1bd;
    --frame: #4a4a5a;
    --soft: #2a2a35;
    --green: #4dca5e;
    --red: #ff8585;
    --blue: #7ab8ff;
    --amber: #ffc857;
    --mono: 'JetBrains Mono', 'Fira Code', 'SF Mono', Consolas, Menlo,
            ui-monospace, monospace;
    --retro: 'VT323', 'JetBrains Mono', ui-monospace, monospace;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 0 0 110px;
    background: var(--bg); color: var(--fg);
    font-family: var(--mono); font-size: 13.5px; line-height: 1.65;
    -webkit-font-smoothing: antialiased;
  }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 0 26px; }
  .dim { color: var(--dim); }
  .amber { color: var(--amber); } .green { color: var(--green); }
  .blue { color: var(--blue); }   .red { color: var(--red); }
  b, strong { font-weight: 500; }
  h2 {
    font-family: var(--retro); font-weight: 400;
    font-size: 30px; margin: 54px 0 4px; letter-spacing: .01em;
  }
  h2 + .say { color: var(--dim); margin: 0 0 22px; max-width: 80ch; }

  /* ══ hero ══════════════════════════════════════════════════════ */
  .hero {
    border-bottom: 1px solid var(--soft); padding: 44px 0 32px;
    background:
      radial-gradient(900px 260px at 18% -40%, rgba(122,184,255,.13), transparent 70%),
      radial-gradient(700px 240px at 82% -30%, rgba(255,200,87,.10), transparent 70%);
  }
  .kicker {
    color: var(--dim); letter-spacing: .3em;
    text-transform: uppercase; font-size: 10px;
  }
  h1 {
    font-family: var(--retro); font-size: 64px; line-height: .95;
    margin: 14px 0 2px; font-weight: 400;
  }
  h1 .sub { color: var(--amber); }
  .stamp {
    margin-top: 14px; display: inline-block;
    border: 1px solid var(--soft); background: var(--raise);
    border-radius: 2px; padding: 5px 11px; font-size: 10.5px;
    letter-spacing: .14em; text-transform: uppercase; color: var(--dim);
  }

  /* The champion plate. The one place the page raises its voice. */
  .champ-plate {
    margin: 26px 0 0; display: flex; align-items: center; gap: 18px;
    border: 1px solid var(--amber); border-left-width: 3px;
    background: linear-gradient(90deg, rgba(255,200,87,.10), rgba(255,200,87,.02) 60%, transparent);
    border-radius: 2px; padding: 16px 20px; flex-wrap: wrap;
  }
  .champ-plate .cw {
    font-size: 9.5px; letter-spacing: .26em; text-transform: uppercase;
    color: var(--amber);
  }
  .champ-plate .cn {
    font-family: var(--retro); font-size: 34px; line-height: 1;
    color: var(--fg); word-break: break-all;
  }
  .champ-plate .ct { color: var(--dim); font-size: 11px; }

  /* ══ figures ═══════════════════════════════════════════════════ */
  .figs {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(128px, 1fr));
    gap: 1px; background: var(--soft); border: 1px solid var(--soft);
    border-radius: 2px; margin: 26px 0 0;
  }
  .fig { background: var(--raise); padding: 14px 15px; }
  .fig .v {
    font-family: var(--retro); font-size: 34px; line-height: 1;
    color: var(--amber);
  }
  .fig .k {
    font-size: 9px; letter-spacing: .17em; text-transform: uppercase;
    color: var(--dim); margin-top: 5px;
  }

  /* ══ weapon stickers ═══════════════════════════════════════════
     The thing this page is for. The fraction is fired/bought,
     deliberately: a rack bought and never fired is the single most
     telling number about an agent, and a plain "10 snaps" hides it.

     These were briefly rotated a degree or two each, on the theory
     that a decal reads as applied rather than typeset. Wrong: at this
     density, against the straight edges of the tie boxes they sit
     inside, a tilt does not read as a sticker — it reads as broken
     rendering. Squared up and left alone. */
  .stk {
    display: inline-flex; align-items: baseline; gap: 5px;
    border: 1px solid; border-radius: 2px;
    padding: 1px 6px 2px; margin: 2px 3px 2px 0;
    font-size: 9.5px; letter-spacing: .1em; text-transform: uppercase;
    white-space: nowrap; position: relative;
  }
  .stk .f {
    font-family: var(--retro); font-size: 16px; letter-spacing: 0;
    font-variant-numeric: tabular-nums;
  }
  .stk .of { opacity: .55; font-size: 9px; }
  .stk.snap  { color: var(--amber); border-color: rgba(255,200,87,.5);
               background: rgba(255,200,87,.08); }
  .stk.chaff { color: var(--blue);  border-color: rgba(122,184,255,.5);
               background: rgba(122,184,255,.08); }
  .stk.emp   { color: var(--red);   border-color: rgba(255,133,133,.5);
               background: rgba(255,133,133,.08); }
  /* Bought and never fired. Dashed and drained of colour, because it
     is a rack sitting in orbit — it looks unused on purpose. */
  .stk.cold {
    border-style: dashed; color: var(--frame);
    background: transparent;
  }
  .stk.cold .f { color: var(--frame); }
  .stk.none {
    border-color: var(--soft); color: var(--frame);
    background: transparent; letter-spacing: .16em;
  }

  /* ══ bracket ═══════════════════════════════════════════════════ */
  .shape {
    display: flex; gap: 0; align-items: stretch;
    overflow-x: auto; padding-bottom: 10px;
  }
  .rnd { display: flex; flex-direction: column; min-width: 186px; flex: 1; }
  .rnd .rh {
    color: var(--dim); font-size: 9.5px; letter-spacing: .18em;
    text-transform: uppercase; padding: 0 0 12px 2px; white-space: nowrap;
  }
  .rnd .rh i { color: var(--frame); font-style: normal; }
  .cell {
    flex: 1; display: flex; align-items: center;
    padding-right: 18px; position: relative;
  }
  .rnd .cell { margin-bottom: 5px; }
  .rnd .cell:nth-child(odd) { margin-bottom: 15px; }
  .cell::before {
    content: ""; position: absolute; right: 9px; top: 50%;
    width: 9px; border-top: 1px solid var(--soft);
  }
  .rnd.last .cell::before { display: none; }
  .tie {
    width: 100%; border: 1px solid var(--soft); background: var(--raise);
    border-radius: 2px; overflow: hidden; cursor: help;
    transition: border-color .12s, background .12s;
  }
  .tie:hover, .tie.on {
    border-color: var(--amber); background: var(--raise2);
  }
  .tie .s {
    padding: 4px 8px; font-size: 10.5px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .tie .s + .s { border-top: 1px solid var(--soft); }
  .tie .s .n { color: var(--frame); display: inline-block; min-width: 17px; }
  /* The loser is dimmed, not struck through: sixteen of seventeen go
     out of a knockout, so losing is the normal case and marking it as
     damage would paint most of the board as failure. */
  .tie .s.won { color: var(--green); }
  .tie .s.lost { color: var(--dim); }
  .tie .s .g {
    float: right; color: var(--frame); padding-left: 8px;
    font-variant-numeric: tabular-nums;
  }
  .tie .s.won .g { color: var(--green); }
  .tie .w { padding: 2px 8px 4px; border-top: 1px solid var(--soft); }
  /* Flush with the top of its column, so the winner sits beside the
     final it came out of. Centred, it floats in the middle of a
     column that is eight ties tall and reads as unfinished. */
  .rnd.champ .cell { flex: 0 0 auto; align-items: flex-start; }
  .cup {
    border: 1px solid var(--amber);
    background: rgba(255,200,87,.07); color: var(--amber);
    padding: 11px 15px; border-radius: 2px; text-align: center;
    font-size: 10px; letter-spacing: .16em; text-transform: uppercase;
  }
  .cup .nm {
    display: block; font-family: var(--retro); font-size: 19px;
    letter-spacing: 0; text-transform: none; color: var(--fg);
    margin-top: 5px; word-break: break-all; max-width: 150px;
  }

  /* ══ hover card ════════════════════════════════════════════════
     Mouseover scores. Fixed-position and JS-placed rather than a CSS
     tooltip, because a tie in the last column has no room to its
     right and a pure-CSS card would render off the page. */
  #pop {
    position: fixed; z-index: 90; display: none;
    min-width: 290px; max-width: 370px;
    border: 1px solid var(--frame); background: #0a0a0e;
    box-shadow: 0 14px 40px rgba(0,0,0,.72);
    border-radius: 3px; padding: 12px 14px 13px; font-size: 11px;
  }
  #pop .ph {
    font-size: 9px; letter-spacing: .2em; text-transform: uppercase;
    color: var(--amber); margin-bottom: 8px;
  }
  #pop .pn { font-size: 12px; margin-bottom: 2px; word-break: break-all; }
  #pop .pn.w { color: var(--green); }
  #pop .pn.l { color: var(--dim); }
  #pop table { width: 100%; border-collapse: collapse; margin: 9px 0 4px; }
  #pop td {
    padding: 2px 0; font-variant-numeric: tabular-nums;
    border-bottom: 1px solid var(--soft); font-size: 10.5px;
  }
  #pop td.n { color: var(--frame); width: 26px; }
  #pop td.sc { text-align: right; white-space: nowrap; }
  #pop td.sc b { color: var(--green); }
  #pop .foot {
    color: var(--dim); font-size: 10px; margin-top: 8px;
    border-top: 1px solid var(--soft); padding-top: 7px;
  }
  #pop .sid { color: var(--frame); font-size: 9px; word-break: break-all; }

  /* ══ field ═════════════════════════════════════════════════════ */
  .card {
    border: 1px solid var(--soft); background: var(--raise);
    border-radius: 2px; margin-bottom: 9px; overflow: hidden;
  }
  .card.gold { border-color: var(--amber); }
  .card .top {
    display: flex; align-items: baseline; gap: 12px;
    padding: 10px 14px 6px; flex-wrap: wrap;
  }
  .card .sd {
    font-family: var(--retro); font-size: 22px; color: var(--frame);
    min-width: 30px;
  }
  .card.gold .sd { color: var(--amber); }
  .card .nm { font-size: 13px; word-break: break-all; }
  .card .tm {
    color: var(--dim); font-size: 10px; letter-spacing: .14em;
    text-transform: uppercase;
  }
  .card .fate {
    margin-left: auto; font-size: 9px; letter-spacing: .14em;
    text-transform: uppercase; color: var(--frame);
  }
  .card.gold .fate { color: var(--amber); }
  .card .mid {
    padding: 2px 14px 10px; display: flex; gap: 20px; flex-wrap: wrap;
    align-items: center;
  }
  .card .rec { color: var(--dim); font-size: 10.5px; }
  .card .rec b { color: var(--fg); }
  .card .ppl {
    padding: 0 14px 10px; color: var(--frame); font-size: 10px;
    line-height: 1.5;
  }
  .card .kil {
    padding: 7px 14px; border-top: 1px solid var(--soft);
    background: var(--raise2); color: var(--dim); font-size: 10px;
  }
  .card .kil b { color: var(--fg); }

  /* ══ ledger ════════════════════════════════════════════════════ */
  table.led { width: 100%; border-collapse: collapse; margin-top: 8px; }
  table.led th {
    text-align: left; font-weight: 400; color: var(--dim);
    font-size: 9px; letter-spacing: .17em; text-transform: uppercase;
    padding: 0 10px 7px 0; border-bottom: 1px solid var(--soft);
  }
  table.led td {
    padding: 7px 10px 7px 0; border-bottom: 1px solid var(--soft);
    font-size: 11px; vertical-align: middle;
    font-variant-numeric: tabular-nums;
  }
  table.led td.ag { word-break: break-all; }
  table.led tr:hover td { background: var(--raise); }

  .note {
    border-left: 2px solid var(--frame); background: var(--raise);
    padding: 11px 14px; margin: 18px 0; color: var(--dim);
    font-size: 11.5px;
  }
  .note b { color: var(--fg); }
  footer {
    margin-top: 58px; padding-top: 18px; border-top: 1px solid var(--soft);
    color: var(--frame); font-size: 10px;
  }
  @media (max-width: 720px) {
    h1 { font-size: 42px; }
    .shape { display: block; }
    .rnd { min-width: 0; margin-bottom: 20px; }
    .rnd .cell { margin-bottom: 6px !important; }
    .cell::before { display: none; }
  }
</style>
</head>
<body>

<div class="hero"><div class="wrap">
  <div class="kicker">Sea of Colours &middot; Hackathon</div>
  <h1>Knockout League<br /><span class="sub">The Record</span></h1>
  <div class="stamp" id="stamp"></div>
  <div id="champ"></div>
  <div class="figs" id="figs"></div>
</div></div>

<div class="wrap">

  <h2>The bracket</h2>
  <p class="say">
    Every tie the competition played. <b>Hover any box</b> for the
    season-by-season scores behind it, the weapons fired in it, and
    the session ids you can replay it from.
  </p>
  <div class="shape" id="shape"></div>
  <div id="late"></div>

  <h2>The field</h2>
  <p class="say">
    Seventeen agents, one per team, each built during the hackathon.
    Stickers read <b>fired / bought</b> &mdash; a dashed sticker is a
    rack that was paid for and never used.
  </p>
  <div id="fieldnote"></div>
  <div id="field"></div>

  <h2>The armoury</h2>
  <p class="say">
    What the room actually did with its weapons. Every charge costs
    blue out of the ground and credits out of the bank, so the gap
    between bought and fired is harvesting capacity parked in orbit.
  </p>
  <div id="ledger"></div>

  <footer>
    Generated by <code>soc cup site</code> from
    <code>reports/league/league.json</code> &mdash; every number on this
    page came out of a played season. Self-contained by design: no
    fetch, no sibling files.
  </footer>
</div>

<div id="pop"></div>

<script>
const D = __DATA__;

const esc = (s) => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const nf = (n) => Number(n || 0).toLocaleString("en-GB");
const KINDS = [["snap", "SNAP"], ["chaff", "CHAFF"], ["emp", "EMP"]];

/* A row of stickers from a {kind: [fired, bought]} map, or from a
   pair of {kind: n} maps. Nothing is drawn for a weapon the agent
   never bought — an armoury of three greyed-out boxes per agent
   would be seventeen rows of noise. */
function stickers(pairs, opts) {
  const o = opts || {};
  let out = "";
  for (const [k, label] of KINDS) {
    const p = pairs[k] || [0, 0];
    const fired = Number(p[0] || 0), bought = Number(p[1] || 0);
    if (!bought && !fired) continue;
    const cold = fired === 0 ? " cold" : "";
    out += `<span class="stk ${k}${cold}" title="${label}: ${fired} fired of ${bought} bought">`
        +  `${label} <span class="f">${fired}</span>`
        +  `<span class="of">/${bought}</span></span>`;
  }
  if (!out && !o.quiet) out = `<span class="stk none">no weapons</span>`;
  return out;
}

function pairsFromTotals(fired, bought) {
  const p = {};
  for (const [k] of KINDS) p[k] = [Number((fired||{})[k] || 0), Number((bought||{})[k] || 0)];
  return p;
}

/* ── hero ─────────────────────────────────────────────────────── */
document.getElementById("stamp").textContent =
  D.champion ? `Final result \u00b7 ${D.generated}` : `In progress \u00b7 ${D.generated}`;

if (D.champion) {
  const me = (D.field || []).find((f) => f.champ) || {};
  document.getElementById("champ").innerHTML =
    `<div class="champ-plate">
       <div>
         <div class="cw">Champion</div>
         <div class="cn">${esc(D.champion)}</div>
         <div class="ct">${esc(me.team || "")}${
           me.won ? ` &middot; ${me.won} ties won, none lost` : ""}${
           me.margin ? ` &middot; ${me.margin > 0 ? "+" : ""}${nf(me.margin)} aggregate` : ""}</div>
       </div>
     </div>`;
}

document.getElementById("figs").innerHTML = [
  [D.entrants, "entrants"],
  [D.matches, "matches"],
  [D.seasons, "seasons played"],
  [D.fired, "weapons fired"],
  [D.bought - D.fired, "charges never fired"],
  [D.fallback, "fallback turns"],
].map(([v, k]) => `<div class="fig"><div class="v">${nf(v)}</div>`
                + `<div class="k">${k}</div></div>`).join("");

/* ── bracket ──────────────────────────────────────────────────── */
const TIES = {};
let tieSeq = 0;

function side(name, seed, tie, isA) {
  if (!name) return `<div class="s tbd"><span class="n">&mdash;</span>TBD</div>`;
  const won = tie.done && tie.w === name;
  const cls = tie.done ? (won ? "won" : "lost") : "";
  let g = "";
  if (tie.done && tie.score) {
    const p = String(tie.score).split("-");
    g = isA ? p[0] : p[1];
  }
  return `<div class="s ${cls}"><span class="n">${seed ? esc(seed) : ""}</span>`
       + `${esc(name)}${g !== "" ? `<span class="g">${g}</span>` : ""}</div>`;
}

function tieBox(tie) {
  const id = "t" + (tieSeq++);
  TIES[id] = tie;
  const fired = {};
  for (const [k] of KINDS) fired[k] = [0, 0];
  for (const w of Object.values(tie.weapons || {})) {
    for (const [k] of KINDS) {
      fired[k][0] += Number((w[k] || [0, 0])[0] || 0);
      fired[k][1] += Number((w[k] || [0, 0])[1] || 0);
    }
  }
  const stk = stickers(fired, { quiet: true });
  return `<div class="tie${tie.done ? " done" : ""}" data-t="${id}">`
       + side(tie.a, tie.sa, tie, true)
       + side(tie.b, tie.sb, tie, false)
       + (stk ? `<div class="w">${stk}</div>` : "")
       + `</div>`;
}

document.getElementById("shape").innerHTML =
  (D.rounds || []).map((r, i, all) => {
    const n = (r.ties || []).length;
    const count = i === 0 ? `${n} match${n === 1 ? "" : "es"}` : String(n);
    return `<div class="rnd${i === all.length - 1 ? " last" : ""}">`
         + `<div class="rh">${esc(r.label)} <i>&middot; ${count}</i></div>`
         + (r.ties || []).map(tieBox).join("")
         + `</div>`;
  }).join("")
  + (D.champion
      ? `<div class="rnd last champ"><div class="rh">Champion</div>`
      + `<div class="cell"><div class="cup">Winner<span class="nm">`
      + `${esc(D.champion)}</span></div></div></div>`
      : "");

/* Late entries sit below the bracket, never in it — the bracket is
   derived from the draw, and a challenger contested a slot inside a
   round already being played. */
if ((D.challengers || []).length) {
  document.getElementById("late").innerHTML =
    `<h2>Late entry</h2><p class="say">Submitted after the draw, so rather
     than re-seed a bracket already in play it contested a qualifier's
     slot.</p>`
    + (D.challengers).map((c) => {
        const t = Object.assign({}, c, { sa: "", sb: "" });
        return `<div style="max-width:340px">${tieBox(t)}</div>`
             + (c.note ? `<div class="note">${esc(c.note)}</div>` : "");
      }).join("");
}

/* ── hover card: the season-by-season scores ──────────────────── */
const pop = document.getElementById("pop");

function popHTML(t) {
  const rows = (t.seasons || []).map((g) => {
    const aw = g.w === t.a, bw = g.w === t.b;
    // Scores are stored by seat; show them in the tie's own A-then-B
    // order so the column means the same thing on every row.
    const sa = g.p1 === t.a ? g.s1 : g.s2;
    const sb = g.p1 === t.a ? g.s2 : g.s1;
    return `<tr><td class="n">g${g.n}</td><td class="sc">`
         + `${aw ? "<b>" : ""}${nf(sa)}${aw ? "</b>" : ""}`
         + ` <span class="dim">v</span> `
         + `${bw ? "<b>" : ""}${nf(sb)}${bw ? "</b>" : ""}`
         + `</td><td class="sc dim" style="width:52px">${g.mins}m</td></tr>`;
  }).join("");

  const wk = Object.entries(t.weapons || {})
    .map(([ag, p]) => {
      const s = stickers(p, { quiet: true });
      return s ? `<div style="margin-top:5px"><div class="sid">${esc(ag)}</div>${s}</div>` : "";
    }).filter(Boolean).join("");

  const how = !t.done ? "Not played"
    : t.how === "margin" ? `Decided on aggregate points (${t.margin > 0 ? "+" : ""}${nf(t.margin)})`
    : t.how === "bye" ? "Bye"
    : t.how === "draw" ? "Genuine draw"
    : `Won on seasons ${esc(t.score)}`;

  return `<div class="ph">${(t.seasons || []).length} season(s) played</div>`
    + `<div class="pn ${t.done ? (t.w === t.a ? "w" : "l") : ""}">${esc(t.a) || "TBD"}</div>`
    + `<div class="pn ${t.done ? (t.w === t.b ? "w" : "l") : ""}">${esc(t.b) || "TBD"}</div>`
    + (rows ? `<table>${rows}</table>` : "")
    + `<div class="foot">${how}.`
    + (t.margin ? ` Aggregate ${t.margin > 0 ? "+" : ""}${nf(t.margin)} to ${esc(t.a)}.` : "")
    + `</div>`
    + (wk ? `<div style="margin-top:8px">${wk}</div>` : "")
    + ((t.seasons || []).some((g) => g.sid)
        ? `<div class="sid" style="margin-top:8px">replay: `
          + (t.seasons).filter((g) => g.sid)
              .map((g) => `g${g.n} ${g.sid.slice(0, 8)}`).join(" &middot; ")
          + `</div>`
        : "");
}

function place(el) {
  const r = el.getBoundingClientRect(), p = pop.getBoundingClientRect();
  let x = r.right + 12, y = r.top;
  if (x + p.width > window.innerWidth - 10) x = r.left - p.width - 12;
  if (x < 10) x = 10;
  if (y + p.height > window.innerHeight - 10) y = window.innerHeight - p.height - 10;
  if (y < 10) y = 10;
  pop.style.left = x + "px";
  pop.style.top = y + "px";
}

document.addEventListener("mouseover", (e) => {
  const el = e.target.closest(".tie");
  if (!el) return;
  const t = TIES[el.dataset.t];
  if (!t) return;
  pop.innerHTML = popHTML(t);
  pop.style.display = "block";
  place(el);
  el.classList.add("on");
});
document.addEventListener("mouseout", (e) => {
  const el = e.target.closest(".tie");
  if (!el) return;
  el.classList.remove("on");
  pop.style.display = "none";
});

/* ── field ────────────────────────────────────────────────────── */

/* Say it out loud rather than quietly correcting it. Some of these
   agents are credited with EMP kills they cannot have scored, because
   the SNAP used to record its probe kills in the EMP row — the kills
   happened, the weapon is misnamed. Fixed mid-competition, so the
   earlier rounds keep what they recorded. */
if ((D.mislabelled || []).length) {
  document.getElementById("fieldnote").innerHTML =
    `<div class="note"><b>One caveat on the kill counts.</b> Rounds played
      before the engine fix in v1.50 recorded a SNAP's probe kills in the
      <b>probes EMP'd</b> row, because the SNAP reuses the EMP's blast
      sweep internally. So ${D.mislabelled.length} agent${
        D.mislabelled.length === 1 ? "" : "s"} below
      &mdash; ${D.mislabelled.map(esc).join(", ")} &mdash;
      show EMP kills despite never buying an EMP. The kills are real and
      the victims correct; the weapon named is not. Later rounds, and the
      whole final, are recorded correctly as <b>probes SNAPped</b>.
      History is left as it was rather than rewritten.</div>`;
}

document.getElementById("field").innerHTML = (D.field || []).map((f) => {
  const kills = Object.entries(f.kills || {})
    .filter(([, n]) => Number(n) > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([k, n]) => `<b>${nf(n)}</b> ${esc((D.kill_labels || {})[k] || k)}`)
    .join(" &middot; ");
  return `<div class="card${f.champ ? " gold" : ""}">
    <div class="top">
      <div class="sd">${f.seed}</div>
      <div>
        <div class="nm">${esc(f.agent)}</div>
        <div class="tm">${esc(f.team)}</div>
      </div>
      <div class="fate">${esc(f.fate)}${
        f.beaten_by ? ` &middot; beaten by ${esc(f.beaten_by)}` : ""}</div>
    </div>
    <div class="mid">
      <div class="rec"><b>${f.won}W ${f.lost}L</b> in ties
        &middot; <b>${f.sw}&ndash;${f.sl}</b> in seasons
        &middot; ${f.margin > 0 ? "+" : ""}${nf(f.margin)} aggregate</div>
      <div>${stickers(pairsFromTotals(f.fired, f.bought))}</div>
    </div>
    ${f.people && f.people.length
      ? `<div class="ppl">${f.people.map(esc).join(" &middot; ")}</div>` : ""}
    ${kills ? `<div class="kil">${kills}</div>` : ""}
  </div>`;
}).join("");

/* ── armoury ledger ───────────────────────────────────────────── */
const armed = (D.field || [])
  .map((f) => ({ f, b: Object.values(f.bought || {}).reduce((a, c) => a + Number(c || 0), 0),
                    fi: Object.values(f.fired || {}).reduce((a, c) => a + Number(c || 0), 0) }))
  .sort((a, b) => b.fi - a.fi || b.b - a.b);

document.getElementById("ledger").innerHTML =
  `<table class="led">
     <tr><th>Agent</th><th>Rack</th><th style="text-align:right">Fired</th>
         <th style="text-align:right">Bought</th>
         <th style="text-align:right">Never fired</th></tr>`
  + armed.map(({ f, b, fi }) => `<tr>
      <td class="ag">${f.champ ? "<span class='amber'>&#9733;</span> " : ""}${esc(f.agent)}</td>
      <td>${stickers(pairsFromTotals(f.fired, f.bought))}</td>
      <td style="text-align:right">${fi ? `<b>${fi}</b>` : "<span class='dim'>0</span>"}</td>
      <td style="text-align:right" class="dim">${b}</td>
      <td style="text-align:right" class="${b - fi > 0 ? "red" : "dim"}">${b - fi}</td>
    </tr>`).join("")
  + `</table>`;
</script>
</body>
</html>
"""


def build(data: dict) -> str:
    """The whole page as one string."""
    blob = json.dumps(compact(data), separators=(",", ":"), sort_keys=False)
    # Cannot appear in JSON-encoded data, but a </script> inside a
    # string value would end the block early. Cheap to be safe.
    blob = blob.replace("</", "<\\/")
    return _PAGE.replace("__DATA__", blob)


def write(data: dict, path: Path | str = REVIEW_PATH) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(build(data))
    return p
