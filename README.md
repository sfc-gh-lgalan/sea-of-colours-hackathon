# Sea of Colours

A turn-based, fog-of-war strategy game — and a working example of the
machinery it takes to make an LLM play something well.

You run a mining house: harvest colour from a hidden map at night, spend
the proceeds in orbit each morning, and out-think the other houses.
Every night you commit a sealed plan and watch it execute. You cannot
react mid-night, you cannot see most of the board, and at dawn anything
still on the surface is destroyed.

It ships with bot opponents and an LLM agent. **The hackathon exercise
is to build a better agent than the one in the box** — which means
working inside a real agent harness: prompt assembly, a memory system
that carries knowledge across nights, structured output the engine will
accept, and an eval loop to prove you improved something.

---

## Your hackathon, in order

Six steps. Each one works before the next, so nobody is blocked on setup
they haven't reached yet.

### 1 · Play a game — two minutes

Python 3.10+. No Snowflake account, no config, no build step.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run_web.py
```

> **Start the server in your own terminal — not through an AI coding
> agent.** A server an agent launches is a child of that agent's shell
> session: when the session ends or the agent moves on, the server dies
> with it and your game dies mid-night. It looks exactly like the app
> crashing. Open a terminal, run it yourself, and leave it running.

Open <http://127.0.0.1:8000> and hit **Quick game** — that spawns you vs
`RED_HARVEST_LITE` and drops you straight on the board. **Play** opens
the launcher instead, if you want to choose the map, the seats or the
opponent.

**New here? Open [`guide/index.html`](guide/index.html) in a browser** —
an illustrated walkthrough from downloading this repo to finishing your
first season, including the Snowflake setup for the LLM agent. The rest
of this file is the reference version of the same ground.

It opens straight off disk, before you install anything. Once the server
is up it is also served at <http://127.0.0.1:8000/guide/>, alongside the
manual at `/manual/` and the agent & harness guide at
`/manual/agent.html` — so a host can share one URL and everything is
reachable from it.

If anything looks wrong, `python scripts/quickstart_check.py` prints one
line per check with the fix beside any failure (`--network` to also
contact Snowflake, `--tests` to run the suite).

> Where game state lives is auto-detected: with no Snowflake setup the
> server uses the in-process `memory` store, which needs nothing and
> runs the full game. It prints the backend it chose (and why) on the
> second line of boot output.

### 2 · Play the person next to you

Click **MULTIPLAYER** on the title screen. The server opens a public
tunnel for you and hands out an invite link and QR per seat, playable
from anywhere — the next desk, another city, a phone on cellular.

```bash
brew install cloudflared   # recommended before you host
```

You can host with no install at all — the fallback provider is plain
`ssh`, which you already have — but its free address **changes after a
few minutes**, which kills invite links in the middle of a game.
`cloudflared` keeps one address for as long as the server runs, so it's
worth the thirty seconds. The two also get blocked by different corporate
network policies, so having both is what makes this work on a locked-down
laptop.

Details, the phone gotchas, and what to do when a network blocks a
provider: [`docs/MULTIPLAYER.md`](docs/MULTIPLAYER.md).

### 3 · Connect your Snowflake account

This is the destination, not a gate — everything above runs offline. A
**programmatic access token** is all you need to face V12, because it
calls Cortex *inference* over REST rather than needing anything
deployed. Optionally deploy the schema for sessions that survive a
restart.

Walkthrough: [`docs/SNOWFLAKE_SETUP.md`](docs/SNOWFLAKE_SETUP.md).

### 4 · Lose to V12

Seat P2 as **V12** and play a season. Watch what it does well — it plans
multi-night seam campaigns, remembers where it got hurt, and reasons
about what its rival is doing. Then watch what it doesn't do.

You will spot the gaps yourself before anyone points them out. That is
the intended order.

### 5 · Mint your own agent

```bash
python scripts/new_agent.py --team redwatch --name reaper
```

One command forks V12 into your own registered agent, renames its
identity so your turns are attributed to you, and puts it in the New
Game dropdown. Restart the server and you can play your fork against the
original.

**Don't edit V12 directly.** It is the control in your experiment —
without it you cannot tell whether you improved anything, and matches
between teams stop being like-for-like.

Name it `<team>_<agent>`: the label lands in the audit trail and on the
scoreboard.

### 6 · Close a gap, then prove it

Your fork ships with its own README pointing at both known gaps with
exact files and constants. Change something, then show it worked:

```bash
# fixed scenarios with assertions
python -m sea_of_colours.orchestrator_2.evals.cli --config redwatch_reaper --runtime cortex

# head-to-head against the baseline
python scripts/run_matchup_v12.py --modes lite

# read what your agent actually saw and decided, turn by turn
SOC_CARD_DUMP_DIR=/tmp/cards python run_web.py
```

Start here: [`harnesses/tabula_v12/README.md`](sea_of_colours/orchestrator_2/harnesses/tabula_v12/README.md).

---

## The two gaps

V12 is strong and has two deliberate holes. Closing either is a good
day's work; closing both should win you the room.

**It buys weapons and never fires them.** V12 accumulates EMPs and chaff
in orbit, then plans every night as if unarmed. It can see it was EMP'd;
it has no path to EMP back. Four separate things have to change before
it can pull the trigger, and finding them is a good tour of the harness.

**It treats BLUE as an afterthought.** Which is partly *why* the first
gap persists: BLUE is the weapons currency. Five reinforcing gates keep
blue subordinate to red, and loosening one usually achieves nothing
because another still holds.

---

## What you'll learn

The game is the excuse. The transferable part is everything around the
model call.

**Harness design.** An LLM on its own cannot play this — it hallucinates
illegal moves, forgets what it learned, and rambles past the turn
budget. A harness is what turns a model into an agent that operates
meaningfully: assemble what it needs to see, constrain what it can say,
validate what comes back, and repair it when it's wrong. V12 is a
worked, working example you can read in an afternoon.

**Memory systems.** V12 carries four distinct kinds of memory across
nights, and they do different jobs:

| Memory | What it holds |
| --- | --- |
| `last_night.py` | Episodic — what just happened, including what was done *to* you |
| `journal.py` | A continuous strategy thread the agent writes to itself, night to night |
| `world_view.py` | A durable model of the map and rivals, accumulated across days |
| `hazard_memory.py` | Negative memory — where it got hurt, so it stops going back |

Getting an agent to *use* memory rather than just carry it is most of
the difficulty, and it is visible here in a way it rarely is in a demo.

**Where to put the intelligence.** V12 splits deterministic search
(Python enumerates the legal, sensible plays) from judgement (the model
picks between them). Knowing which half to change is the core skill —
and the reason "teach it to use weapons" is a code change, not a prompt
change.

**Structured output that survives contact.** JSON schemas, a sanitiser
that repairs illegal moves, and layered fallbacks for when the model
returns something unusable.

**Evaluating an agent.** Fixed scenarios with assertions, head-to-head
matches, and per-turn audit cards to answer "why did it do that?".

**Cortex inference in practice.** Calling a model over REST with a PAT,
under the token and latency budgets that come with a human waiting for
their turn to resolve.

---

## The game in a minute

**Night (Nox).** You submit an ordered queue: drop harvesters from
orbit, walk them across tiles, launch probes, pick up cargo. The queue
runs to completion without you. *You are planning, not playing.*

**Dawn.** Everything still on the surface is destroyed.

**Day (Orbit).** Repair, buy probes, build weapons — spend as much as
your wallet allows. Then your vault settles itself: every RED parcel
ships and scores, every GREEN parcel is dumped at a penalty. Seasons
run **7 nights**.

### What makes it hard

**You are nearly blind.** A harvester sees only the tile it stands on.
Probes reveal a ~49-cell disk but expire after 3 nights, and you can
only drop a harvester onto a tile a *live* probe is currently watching.
Vision is a resource you spend and keep renewing.

**Only what you ship counts,** and purity is multiplied by tier:

| Tier | Purity | Multiplier |
| --- | --- | --- |
| `trace` | 0–50 | ×0.75 |
| `vein` | 51–150 | ×1.0 |
| `mass` | 151–254 | ×1.5 |
| `pure` | 255 | **×3.0** |

One `pure` parcel scores 765; ten `trace` parcels score 375. Volume
loses to quality — but pure cores sit deepest inside seams, where you
can see least.

**GREEN is unavoidable and it hurts.** Byproduct of all mining, cannot
be refused, eats hold space, and every parcel still held at season end
is **−100**.

**BLUE funds violence.** EMPs and chaff. Rivals can jam your egress and
disable your harvesters, and you find out from the wreckage.

Full rules: [RULEBOOK.md](RULEBOOK.md), or `manual/index.html` for the
interactive version.

---

## How it works

```
Browser ── FastAPI (server/app.py) ── engine (game/) ── store
                    │                                   memory │ file │ Snowflake
                    └── orchestrator_2 ── binding registry ── your agent
```

The **engine** is pure Python and knows nothing about agents. The
**orchestrator** decides who takes each turn and hands everyone an
identical fog-limited view — the fairness contract is that no agent gets
a privileged board. A **harness** is one function:

```python
def run(*, store, session_id, player, view) -> dict:
    ...
```

Register it in `binding_registry.py` and it is immediately playable from
the menu and runnable in the eval suite. That is the whole plug-in
surface — see [`orchestrator_2/README.md`](sea_of_colours/orchestrator_2/README.md).

### The one thing to understand about V12

The model does not invent moves. Each night, Python precomputes a menu
of legal, concrete plays — seam campaigns, probe patterns, harvest
chains — and the LLM picks ids off that menu. The selection is compiled
into moves and sanitised before it reaches the engine.

If a play is not on the menu, no amount of prompting will produce it.

### The three agents

- **`RED_HARVEST_LITE`** — the deterministic heuristic with weapons
  switched off. Start here; it won't mine or EMP you while you're still
  learning what a parcel is. The default rival in the NEW GAME menu.
- **`RED_HARVEST`** — the same pure-Python playbook with the full
  weapons economy
  ([`heuristic_agent.py`](sea_of_colours/agent/heuristic_agent.py)).
  No Snowflake required, deterministic, fully tested. The real baseline.
- **`V12`** ([`tabula_v12/`](sea_of_colours/orchestrator_2/harnesses/tabula_v12/))
  — the LLM agent, thinking via Snowflake Cortex. Needs a PAT but no
  deployed agent object and no particular storage backend.

All three consume the same view and emit the same move queue, which is
exactly what makes them comparable on a scoreboard.

---

## Where to go next

| I want to… | Go to |
| --- | --- |
| Be walked through the whole thing | [`guide/index.html`](guide/index.html) — install → first game → first agent change |
| Learn the rules interactively | `manual/index.html` — open it in a browser |
| Read the canonical rules | [RULEBOOK.md](RULEBOOK.md) |
| See one real agent turn dissected | `manual/agent.html` |
| Win a harder game | Set the rival to `RED_HARVEST` (weapons on) |
| Fork and improve the agent | [`tabula_v12/README.md`](sea_of_colours/orchestrator_2/harnesses/tabula_v12/README.md) |
| Write an agent from scratch | [`orchestrator_2/README.md`](sea_of_colours/orchestrator_2/README.md) |
| Face or fork the LLM agent | [`docs/SNOWFLAKE_SETUP.md`](docs/SNOWFLAKE_SETUP.md) — a PAT is all you need |
| Play a friend | [`docs/MULTIPLAYER.md`](docs/MULTIPLAYER.md) — one-click tunnel + QR invite |
| Generate maps from the terminal | [`docs/MAP_GENERATOR.md`](docs/MAP_GENERATOR.md) — the standalone `main.py` CLI |

---

## Running it

```bash
python run_web.py                # auto-detect storage; prints what it chose
pytest                           # engine + orchestrator suites
```

**Storage is auto-detected, then chosen per game.** Unset means *auto*:
`snowflake` when the Snowpark deps and key-pair auth are both present,
otherwise the in-process `memory` store. `SOC_BACKEND` overrides that
*default*, and an explicit value is strict — the server exits rather
than quietly falling back, so a persistent season never lands somewhere
you didn't intend.

```bash
SOC_BACKEND=memory    python run_web.py   # fully offline, in-process only
SOC_BACKEND=file      python run_web.py   # offline but durable, one JSON per season
SOC_BACKEND=snowflake python run_web.py   # require Snowflake; fail loudly if unusable
```

From v1.14 each game also picks its own store in the New Game launcher's
**STORAGE** row, and one server happily runs both at once — a throwaway
memory game against a heuristic alongside a persisted Snowflake season.
Two consequences worth knowing:

- **Memory games are heuristics-only.** An LLM match that isn't written
  down leaves nothing for `replay_turn.py`, `turn_suite.py` or the
  advisor to read back, which is the part of the loop that makes an
  agent better. LLM seats therefore want a persistent backend.
- **Quick game always uses memory** — it's the "just let me play"
  button, and it shouldn't cost a warehouse round-trip per move.

`GET /api/meta/backend` reports the default, the reason, and every
per-game option; the landing-page badge shows the default.

**Hosting a shared game:** click **MULTIPLAYER**. `server/tunnel.py`
walks an ordered list of tunnel providers and uses the first that
publishes a public URL *that this machine can actually resolve* —
`cloudflare` (`brew install cloudflared`) then `localhost.run` (plain
`ssh`, no install, no account). Cloudflare leads because its address is
stable for the life of the process; the fallback is there because egress
filtering on locked-down networks drops SSH or non-443 ports for some
providers and not others, so between them almost every network is covered. `GET /api/tunnel/status` reports which one is carrying
the tunnel, and whether its address is one that rotates.

The resolve check is the important part, and the counter-intuitive bit is
that it **waits before looking**. A hostname exists a couple of seconds
after the provider prints it, and a lookup in that gap gets cached as
"no such name" for the zone's negative TTL — half an hour on
`trycloudflare.com` — on the host's own resolver. Checking too eagerly
therefore *creates* the outage it's testing for, which cost us a day of
blaming the corporate network. Details and measurements:
[`docs/MULTIPLAYER.md`](docs/MULTIPLAYER.md).

Same-Wi-Fi LAN play via `python run_web.py --lan` still exists, but needs
the host to accept **inbound** connections, which MDM-managed laptops
typically forbid — the tunnel only ever dials outward, so it sidesteps
the firewall entirely. `--lan` also turns auto-reload off, because the
cross-player submit lock is a per-process `threading.Lock` and a memory
season lives in that process, so a file save mid-party would reset
everyone.

Seat links live at `/play?session=<id>&player=p2` — the `/play` matters,
`/` is the title screen.

**Snowflake** is optional twice over: a PAT gets you the V12 agent, and
a schema deploy gets you sessions that survive a restart. The game state
and engine can live in Snowflake — the FastAPI server is a thin proxy
over [`snowpark/engine.py`](sea_of_colours/snowpark/engine.py), which
also backs the stored procedures in
[`snowflake/soc_procedures.sql`](snowflake/soc_procedures.sql). See
[RULEBOOK §5](RULEBOOK.md#5-snowflake-architecture-v04) and
[`docs/SNOWFLAKE_SETUP.md`](docs/SNOWFLAKE_SETUP.md).

---

## Project layout

```
RULEBOOK.md                       Canonical rules + changelog — the tie-breaker
AGENTS.md                         Always-on context for AI coding agents
run_web.py                        Start the web UI
main.py                           Standalone map-generator CLI (docs/MAP_GENERATOR.md)

guide/index.html                  Install guide + tutorial — start here
manual/index.html                 Interactive rules manual
manual/agent.html                 One real V12 turn taken apart

sea_of_colours/
├── game/                         Pure-Python engine — session, simulator, ledgers
├── snowpark/                     Storage-agnostic engine wrappers + backend selection
├── agent/                        RED_HARVEST / RED_HARVEST_LITE heuristic
├── orchestrator_2/               Agent orchestration + the plug-in contract
│   └── harnesses/tabula_v12/     V12 — the LLM agent you fork
├── evals/                        Scenario + eval harness
└── noise.py, generator.py, render.py, png.py, data.py
                                  Terrain generation and rendering

server/
├── app.py                        FastAPI proxy: /play, /api/game/*, /guide, /manual
└── static/                       index.html, app.js, styles.css, landing.*

scripts/
├── new_agent.py                  Fork V12 into your own registered agent
├── quickstart_check.py           Pre-flight: deps, backend, credentials
├── deploy_soc_schema.py          Snowflake schema deploy (creates DB + warehouse)
└── run_matchup_v12.py            Headless V12 matchups

snowflake/                        SOC_* tables, views, stored procedures
tests/                            Engine + orchestrator suites (`pytest`)
```
