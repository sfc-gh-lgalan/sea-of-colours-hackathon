---
name: soc-agent-forge
description: >
  Mint, arm, share and iterate a Sea of Colours hackathon agent. Use for ANY
  Sea of Colours agent work: minting a team agent, giving an agent weapons
  (EMP / SNAP / chaff), naming a move, designing doctrine, working out why an
  agent will not fire, reading season cards, or setting up lab and headless
  tests. Triggers: sea of colours, soc, soc new, mint an agent, mint my agent,
  team agent, my first agent, give my agent EMPs, give it a snap, add a weapon,
  arm my agent, name a move, name my moves, weapon doctrine, my agent won't
  fire, agent never fires, soc weapons, four rungs, tabula_v12, emp_harvest,
  turnlab, frozen turn, all-cards.md, season cards, headless season, run a
  season, play against my agent, publish my agent, share my agent, socfork,
  league, soc push, soc doctor, soc collect, push my agent, push refused,
  changes outside, origin is not yours, wrong remote, cloned instead of forked,
  fork the repo, upstream remote, am I ready to submit, submit my agent,
  did my agent enter, collection.
---

# Sea of Colours — agent forge

Get a team from nothing to a **named, armed, shareable agent** fast, then keep
the iteration loop turning.

The kit this drives is already good. `soc` is explicitly built to be driven by
a coding assistant — every error names its fix, every subcommand explains
itself, `--json` exists for structure. What is missing is *sequence*: 10,814
lines of documentation, a 6,279-line rulebook, and a hackathon team with about
six hours. This skill is the sequence.

## The one thing to get right

Whatever is on the team's fork when the organiser runs collection is what plays
in the league. So: **push early, push something that runs, improve after.** A
brilliant agent that never got pushed scores nothing.

## The idea underneath: an agent is one directory

This is the design decision everything else follows from. **Registering an agent
edits nothing shared.** There is no registry file to add yourself to, no config,
no queue. `agent_manifest.discover()` scans `harnesses/` at import time for any
directory containing an `agent.json`, and that file *is* the registration.

That single choice buys three things: forty teams can work simultaneously without
touching a common file; an entrant is a **directory** rather than a merge; and at
the end an organiser can lift each agent out of its fork and set it beside
thirty-nine others with nothing to resolve.

Every publishing rule protects that property. In particular `soc push` **refuses
if anything has changed outside the agent directory**, even something the commit
would never have included — because only that directory travels into the league,
so an out-of-folder change silently goes missing and the agent then behaves
differently there than on the laptop. That is divergence, not untidiness.

And **pushing is not entering.** The fork is where the agent lives; the league is
assembled from the forks. See `references/publishing.md` — most of the ways a
team loses a day's work are in there, and every one is silent until the deadline.

## Phases

Each has a gate. The gates matter because the weapon rungs are genuinely
order-dependent — each is invisible until the one before it works, which is why
teams conclude "the model is stupid" when it is almost never the model.

| Phase | File | Gate |
| --- | --- | --- |
| 0 · Fork, mint, publish | `phases/0-mint.md` | `soc doctor` names YOUR fork; `soc list` shows the label; empty fork pushed |
| 1 · Choose and name | `phases/1-choose.md` | A `WeaponPlay` spec with a name, a trigger and a one-sentence why |
| 2 · Generate | `phases/2-generate.md` | `soc weapons` four PASS; `pytest` green |
| 3 · Handoff | `phases/3-prove.md` | Team told what exists, and chose: share, or run an improvement loop |
| 4 · Commission | `phases/4-commission.md` | A written report: what fires, what doesn't, what's untested |
| 5 · Iterate | `phases/5-iterate.md` | A ranked, evidenced list — and a re-run that tests it |

Phases 1→5 are a loop. Expect to go round twice; plan for once.

## Two facts that save the most time

**A fresh fork scores four FAILs, and that is correct.** `soc new` copies V12
wholesale — 47 files, 23,168 lines, a complete working agent. But V12 buys
weapons and never fires them, so `soc weapons` reports:

```
1. the agent knows it owns a rack               FAIL
2. the play is offerable and compilable         FAIL
3. the offer explains itself                    FAIL
4. doctrine says when, and at what tempo        FAIL
```

That is the exercise, not a broken mint. (`docs/TEAM_LEADER_GUIDE.md` §6 claims
all four pass on a fresh fork. That is wrong — a fresh fork *is* V12. Do not
let a team lose ten minutes to it.)

**Arming an agent is small, and it is data rather than surgery.**
`emp_harvest_test` is ~2,550 lines across 11 files — the *maximal* example, with
two weapons, five named plays and compound seam patterns. Hand-wiring one
compound play across six files was measured at **7m42s** and introduced two
bugs. So Phase 2 installs a forge once — **0.24s, ten one-line hooks** — after
which a weapon is one dataclass in one file:

```python
WeaponPlay(
    play_id="CANCEL_SMASH",
    weapon="chaff",
    when="redsign_theirs",      # always | redsign_mine | redsign_theirs | other
    hour="super_early",         # super_early | early | mid | late | last_night
    combines_with="blind_grab", # borrows that pattern's real geometry
    why="a seat that has just found a pure drops on it at hour 1, so "
        "cancelling that one hour takes their whole opening",
)
```

Everything else is derived: the option and its rationale, the wire move, the
menu group, the replay tags, the schema enum, the doctrine. Rung 1 is a print
statement; the fork's own docstring says so:

> V12 carries the same numbers on the view — `orbit.weapon_stock` has always
> been there — and prints them nowhere, so its night phase plans as though the
> rack were empty. The fix is this small: say it out loud.

**The `why` is the field that decides whether it fires.** A team supplies one
sentence: what firing this *buys*. The full rationale is composed from that plus
the weapon's mechanics plus **the alternative it beats** — and that last part
depends on what else is on tonight's menu, which nobody can know up front. An
option that only praises itself competes badly, because the model is choosing
*between* options.

**A correctly-wired weapon can still refuse, and usually for one reason.** V12's
`BEWARE_*` blocks describe every weapon from the **survivor's** side, and that
framing suppresses offensive use. Measured: an agent holding a chaff refused to
fire at H1 reasoning *"their H1 drop still lands"* — because
`DOCTRINE_BEWARE_CHAFF` calls chaff "blind-fired at the hours opponents most
naturally pick up" and marks early hours safe. The model applied V12's own
doctrine correctly to a case where it is wrong. So every weapon ships a
**correction**, not just an addition. See `references/doctrine-conflicts.md`.

## Defend the defaults

The kit's defaults encode decisions someone got burned to learn. Under time
pressure, a team — or an assistant optimising a sweep — will reach past them
for a local speedup. Treat a default override as something to justify.

- **Never emit `--backend memory`.** `soc season` already defaults to `file`,
  which is offline, needs no Snowflake account, and is replayable. Memory keeps
  the cards and destroys the session, so there is nothing to open. It buys
  almost nothing: a 3-day heuristic season on `file` takes 2.3s.
- **Always echo the `?session=` URL** the season prints, so the replay is a
  visible next step rather than something you have to know exists.
- **The season and the server must agree on backend.** A `file` season and a
  default-backend server will not find each other, and that looks exactly like
  a broken replay.
- **Never `--wipe-first`.** It destroys every prior session, replay and audit
  row for the whole account.
- **Mint before starting the server**, or start it with reload on. A
  `--no-reload` server built its roster at import and will not see a new agent.

## Adding prose is not free

The measured cautionary case: a fork that was a *strict superset* of V12 — same
code path and prompt when no weapons are held, so it cannot lose on a
weapon-free night — lost **7–2 across nine seeds, −1,035 points per season**,
after roughly 140 lines of doctrine prose were added. Zero fallbacks, so it was
decision quality, not a bug.

The model mirrors the style of what it reads. Long analytical input produces
long unfocused output, and prose prohibitions ("do NOT ramble") are unreliable
— only structural changes and input *reduction* move behaviour. So treat "add
more doctrine" as the option that needs evidence, and prefer a shorter, sharper
block over a longer one.

## Interviewing, not interrogating

Phase 1 is a conversation and it should feel like one. Lead with **multiple
choice** — five archetypes in `references/archetypes.md` — because a team that
picks "the tempo tax" and names it in ninety seconds is ahead of a team still
designing at 11:00. Free text is the escape hatch for teams who want it, not
the entry point.

Ask for one thing at a time. Never present a wall of questions. When a team's
answer is vague, offer two concrete readings rather than asking them to be more
specific.

## References

Load on demand — do not read these up front.

- `references/publishing.md` — **read before Phase 0.** Fork-first, `soc push`'s
  seven steps, the out-of-directory guardrail and why it exists, share vs push vs
  enter, and what collection does
- `references/v12-vocabulary.md` — **read before naming a move.** All 18 named
  seam patterns and 8 numbered families your move is printed beside, and why a
  rationale must name a competitor that is really on tonight's menu
- `references/archetypes.md` — the three weapons on both cost axes, and the five
  plays worth arguing about
- `references/doctrine-conflicts.md` — **read this when a wired weapon refuses.**
  The `BEWARE_*` suppressors and the correction each weapon must ship
- `references/diagnosis.md` — symptom → cause → fix site, six causes
- `references/verification-ladder.md` — the six test surfaces, cheapest first
- `references/minimal-weapon.md` — the measured V12→fork diff, and the six edit
  sites the forge automates (useful when hand-wiring, or when debugging a hook)
- `references/blue-and-buying.md` — **conditional**: the five blue gates, plus
  the buy-cadence menu. Only when a chosen weapon costs ≥200 blue, and only
  after it fires
- `templates/commission-report.md` — the Phase 4 report scaffold
- `templates/weapon_plays.py` — the file a team edits. Nothing else
- `templates/weapon_forge.py` — the machinery. Installed, never edited
- `scripts/forge_install.py` — installs the hooks once, ~0.24s. Dry run by
  default; refuses a fork whose anchors have drifted
- `scripts/check_wiring.py` — imports the fork and interrogates the live
  objects. The honest counterpart to `soc weapons`

## Scope

Everything stays inside the team's own harness directory,
`sea_of_colours/orchestrator_2/harnesses/<label>/`. `soc push` enforces this
and will refuse a diff that reaches outside it. Wanting to change the engine is
a conversation with the organisers, not a commit.

Never add the agent to `binding_registry.py`. Registration is an import-time
scan for `agent.json`, and that property is what lets forty teams work at once
without a merge conflict.
