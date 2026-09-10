# Team leader's guide

> **There is a nicer version of this page.** Open
> [`guide/leader.html`](../guide/leader.html) in a browser — same content,
> laid out like the manual, with a stage nav and copy buttons. It opens
> straight off disk (no server needed) and is also served at
> `/guide/leader.html`. This Markdown copy is the one to grep, diff, and
> paste to a coding assistant.

**Who this is for.** One person per team, running four or five people
through a day that ends in a league table. You are not expected to be the
best coder in the room — most of the actual editing will be done by your
team's coding assistants. What you own is *sequence*: making sure nobody
is still installing at 11:00, that your fork exists before anyone writes
code into it, and that something of yours is published before collection
closes.

**The one sentence that matters.** Whatever is on your fork when the
organiser runs collection is what plays in the league — so push early,
push something that runs, and improve it after. A brilliant agent that
never got pushed scores nothing.

Attendee-facing depth lives in [HACKATHON_AGENTS.md](HACKATHON_AGENTS.md)
(mint → improve → test → publish → league). This document is the *day*:
what happens in what order, who does it, and what "done" looks like at
each checkpoint. Where the two disagree, HACKATHON_AGENTS.md wins on
mechanics and this one wins on sequencing.

---

## The shape of the day

| # | Stage | Who | Done when |
| - | ----- | --- | --------- |
| 1 | Install and check | everyone, before the day | `soc doctor` says *No problems found* |
| 2 | Learn the game | everyone | finished the advanced tutorial |
| 3 | Play a real game | pairs | you have lost one and know why |
| 4 | Mint the team agent | **leader** | `soc list` shows your label |
| 5 | Publish it empty | **leader** | it is on your fork, unmodified |
| 6 | First version — pick a doctrine | team | `soc weapons` shows four rungs |
| 7 | Evaluate | team | lab diffs + a headless season |
| 8 | Collate feedback | **leader** | one list, ranked |
| 9 | Round two | team | changes tested, not just written |
| 10 | Final polish and final push | **leader** | pushed, and `soc doctor` clean |
| 11 | League | organiser | — |

Stages 6 → 9 are a loop. Expect to go round it twice; plan for once.

---

## 1. Install and check

Ideally done the evening before. An install that starts at 09:30 costs
the morning.

Python 3.10 or newer. No Snowflake account, no config and no build step
are needed to play.

```bash
git clone <your fork>            # your fork, not upstream — see stage 4
cd sea-of-colours-hackathon
pip install -r requirements.txt
python scripts/soc.py doctor
```

`doctor` is the arbiter. It reports the Python it found, the storage
backend, which agents are discovered and routable, and — since v1.45 —
the exact Snowflake connection file and section it resolved:

```
  snowflake conn   /Users/you/.snowflake/connections.toml [soc]
  LLM credentials  present
```

There is also `python scripts/quickstart_check.py`, which checks the base
install offline. Nothing it reports as *skip* is required to play — only
the base install can actually fail it.

**Checkpoint.** `No problems found`, and `LLM credentials present` for
anyone who will run an LLM agent. Credentials come from the standard
Snowflake connection store, so most people who have used the Snowflake
CLI or Cortex Code are already configured — see
[SNOWFLAKE_SETUP.md](SNOWFLAKE_SETUP.md) §1. Playing and testing need no
Snowflake at all.

### How much Snowflake does your team actually need?

Less than people assume, and getting this right saves a morning:

| You want to… | Needs |
| --- | --- |
| Play, take tutorials, face the heuristics | **Nothing.** No account, no config |
| Run the test suite, mint an agent, `soc lab` | **Nothing** |
| Play *against* V12, or run your own LLM agent | **A PAT.** That's it — the model call is a REST call |
| Keep seasons in your Snowflake account | The extras and a schema deploy ([§2](SNOWFLAKE_SETUP.md)) |

The middle row is the one teams get wrong. An LLM seat needs a
**persistent** backend, but persistent does not mean Snowflake — start
the server with `SOC_BACKEND=file` and you get a durable, replayable
store on local disk that accepts LLM seats:

```bash
SOC_BACKEND=file python run_web.py --no-reload
```

The New Game modal then offers **File** alongside Memory, and it will
take a V12 seat. No `requirements-snowflake.txt`, no schema deploy, no
warehouse. Only the last row of that table needs the full setup.

> **Leader's job here:** collect `soc doctor` output from every team
> member in a chat thread before the day starts. It is thirty seconds
> each and it converts the morning's mystery failures into a list you
> already solved.

---

## 2. Learn the game

Nobody can write an agent for a game they have not played. This is not
optional warm-up; the doctrine your team writes in stage 6 is only as
good as its understanding of what wins.

Start the server, then press **Tutorial** on the title screen. That opens
a chooser with three cards — Basic, Advanced, and Quick game.

```bash
python run_web.py
# http://127.0.0.1:8000
```

There are two teaching tutorials, and both are short. (The third card,
**Quick game**, is not a tutorial: it is a full-size board with all rules
on and no films — the right thing for stage 3, not this one.)

**Basic** runs three days and teaches
the loop: orbit, planning, night, praxis. **Advanced** runs four and is
the one that matters for agent work — it teaches the weapons economy your
agent will spend all day reasoning about, including buying, the public
arsenal bar that advertises your spend to everyone, and a night with a
SNAP and a chaff in it.

Do both, in order. Advanced assumes basic.

**Checkpoint.** Every team member has finished the advanced tutorial and
can answer: what does a rival learn when you buy a weapon? (If the answer
is "nothing", they have not understood the arsenal bar, and their
doctrine in stage 6 will be built on sand.)

---

## 3. Play a real game

Play against the shipped heuristics before you play each other:
`red_harvest_lite` carries no weapons, `red_harvest` does. Losing to
`red_harvest` teaches more in twenty minutes than beating the lite bot
teaches all morning.

Then play each other. **Multiplayer** on the title screen opens a public
tunnel and gives you a scannable link per seat — no install, no account,
no firewall change, and it works from a phone on cellular. A game seats
up to four.

It is not hot-seat: each person opens their own invite link and pilots
their own seat. One detail that wastes ten minutes when it goes wrong —
a link carrying only `?session=…` makes you a **read-only watcher**. A
playable seat needs the `&player=pN` too, which the invite modal's links
already have. If someone is watching rather than playing, that is why.

> **Host it from your own terminal, not through a coding assistant.** A
> server started by an assistant belongs to that assistant's shell and
> dies when the session ends. In multiplayer the tunnel dies with it, so
> every guest is dropped mid-game and their invite links stop resolving.
> This is the single most common way a demo falls over.

**Checkpoint.** Someone on the team can explain, unprompted, why they
lost a specific game. If everyone is still saying "I think I just need
more harvesters", play another.

---

## 4. Mint the team agent

**This is the leader's job and it should happen early — before anyone has
written a line of code.** Everything downstream is named after this
decision, and renaming later means re-pushing under a new identity.

Fork the repo on GitHub first, clone *your fork*, then:

```bash
python scripts/soc.py new \
  --team redwatch \
  --name reaper \
  --participants 'Ada Lovelace, Grace Hopper, Alan Turing'
```

Three things to get right:

- **`--participants` is required and is the league's public record.**
  Everyone who touches the agent goes in. A row that names nobody cannot
  be credited.
- **The label is `team_name`** — here `redwatch_reaper`. That string is
  what you type into every later command and what appears in the game's
  seat dropdown as `REDWATCH_REAPER`.
- **It copies V12 wholesale** — around 47 files, a complete working
  agent, not a stub. Your fork *starts* at parity with the baseline. That
  is deliberate: you are differentiating from a working agent, not
  building one from nothing.

### What minting touches — and why that matters to you

```bash
git status --short
# ?? sea_of_colours/orchestrator_2/harnesses/redwatch_reaper/
```

**One new directory. Nothing shared is edited.** No registry file, no
import list, no central table. This is the property that lets forty teams
work simultaneously without a merge conflict, and it is why the guide
tells you never to add your agent to `binding_registry.py` — doing so
would break the very thing that makes your fork independent.

**Checkpoint.** `python scripts/soc.py list` shows your label under
AGENTS.

---

## 5. Publish it empty

Push the untouched fork immediately:

```bash
python scripts/soc.py push
```

This feels pointless. It is the single highest-value five seconds of your
day. It proves your remote is correct, your directory is registrable, and
your team has a scoring entry — while there is still all day to fix any
of those. `push` refuses if `origin` is not yours, which catches the
person who cloned upstream instead of forking.

**Checkpoint.** Your agent directory is visible on your fork on GitHub.

### How the GitHub side works, in one page

Worth reading once, because every confusing thing that happens later
follows from it.

**Each team owns a fork. Nobody has write access to the event repo.**
That is not a precaution, it is the mechanism: one team's push cannot
break another's, and there is no queue to join. Your clone has two
remotes — `origin` is your fork and the only place you ever push,
`upstream` is where kit fixes come from and you never push to it.

**Nobody opens a pull request.** If someone on your team asks whether to
PR their agent or branch off the main repo, the answer to both is no —
their agent stays in their fork and you never receive a commit from
them.

**It has to be a real fork, made with the Fork button.** Creating an
empty repo and pointing a remote at it is what most people mean by
"forking", and from the laptop it is indistinguishable — full history,
your own copy, push works. What is missing is GitHub's link back to the
event repo, and the league is collected by walking that repo's forks, so
an unlinked copy is never found and never scored with no error anywhere.
`soc doctor` flags it (v1.47). Renaming a fork afterwards is fine; the
link survives it.

**Your agent is one directory, and that directory is the registration.**
There is no central list. The kit finds agents by scanning `harnesses/`
for `agent.json` files, so minting writes one folder and touches nothing
shared. Everything else here is a consequence of that.

`soc push` is not a plain `git push`. It runs six steps, and each one is
a check that exists because instructions do not survive a deadline:

1. **Is `origin` yours?** It compares your remote against your GitHub
   login. If you cloned upstream by mistake it stops and prints the
   exact commands to fix it — your work comes across intact.
2. **Which agent is yours?** Found by the `agent.json` scan. With more
   than one fork present it asks, via `--agent <label>`.
3. **Has anything changed outside your directory?** If so it refuses,
   and lists what.
4. It stages **only your agent directory** — `git add -- <your folder>`.
5. It commits, with `<label>: update agent` unless you pass `-m`.
6. It pushes to your fork. If your branch moved underneath you — a
   teammate pushed, or you pulled upstream on another laptop — it
   rebases and retries automatically, twice.

Step 3 is the one that surprises people, so know it before it bites: a
single edited file *anywhere* outside your folder blocks the push, even
though the commit would never have included it. That strictness is
deliberate. At the end of the day only your directory is lifted out of
your fork, so a change outside it would not travel — your agent would
quietly behave differently in the league than it does on your laptop.
Revert the stray file with `git checkout -- <path>` and push again. If a
change outside really is necessary, that is a change to the *kit*, so
raise it rather than forcing it in.

Step 6 is safe for the same reason. Because every team's commits touch
different directories, histories interleave with nothing to resolve —
which is why the rebase can be automatic rather than a question. If one
ever does conflict, something outside your folder moved; get an
organiser rather than forcing it.

**Pushing is not entering.** Your fork is where your agent lives. An
organiser collects the forks at the end to assemble the league, fetching
each agent directory into a separate staging area. So keep pushing as
you go: whatever is on your fork at collection time is what plays.

---

## 6. First version — pick a doctrine

Now the actual work. The temptation is to change everything; the useful
move is to pick one behaviour and make your agent decisively better at
it. Weapons are the richest seam because the baseline is deliberately
conservative with them.

The economy your team is reasoning about: a 600-BLUE cap per player, and
three weapons priced 1-2-3 — SNAP at 100, EMP at 200, chaff at 300. Every
player's *total* weaponised BLUE is public, so 600 could be two chaff or
three EMPs or six SNAPs, and reading that ambiguity well is itself an
edge.

Check where your agent stands on being *able* to fight at all:

```bash
python scripts/soc.py weapons --agent redwatch_reaper
```

```
  1. the agent knows it owns a rack               FAIL
  2. the play is offerable and compilable         FAIL
  3. the offer explains itself                    FAIL
  4. doctrine says when, and at what tempo        FAIL

  START AT RUNG 1.
```

**All four FAIL on a fresh fork, and that is correct** — you inherited them
from V12, and V12 buys weapons without ever firing them. Do not read this as
a broken mint. It is the exercise, and §6b below is where you fix it.

The ladder is a smoke test over your own source: it answers "have you built
this rung", never "is it any good". It needs no model and no credentials and
runs in a second, so it belongs in the inner loop rather than at the end of
it — and it earns its keep later, when someone rips out a chunk of the prompt
and rung 3 quietly starts failing.

**Checkpoint.** Your team can state its doctrine in one sentence — "we
buy a SNAP on day one and use it to deny the first pure landing" — and
point at the file where that decision now lives.

### Name your moves. Seriously.

Your agent builds a menu of options each night and hands them to the
model with short ids. Out of the box they are inherited from V12 and
they are *fine*: `GRAB1`, `PR2`, `BL3`, `PRSNAP1`. They are also
completely anonymous, and a league table of eleven agents all narrating
`GRAB1` is a league table with no personalities in it.

Rename them. `SMASHBURGER`, `LAST_SUPPER`, `POLITE_ROBBERY` — whatever
your team finds funny at 2pm. They live in your own harness directory
alongside the group headers and blurbs that describe each kind of play,
so renaming is a change to your fork and nobody else's.

This is not only decoration. Those ids show up in the game log as the
night resolves, in the lab's frozen-turn journals, and in the season
cards you paste into your assistant:

```
[REDWATCH_REAPER] [plan=aggressive: SMASHBURGER1, PR2] [predicted=medium] moves=14
```

Three real benefits, in rising order of usefulness:

1. **Identity.** Your agent's turns are recognisable in a shared replay,
   which matters when the room is watching the league.
2. **Debugging.** You can grep a whole season for your own vocabulary and
   find every turn where the agent reached for that play.
3. **Diffing against V12.** In the lab you are comparing your fork's
   decision against the baseline's on the same turn. When both are
   saying `GRAB1` you have to read carefully to see whose is whose;
   when yours says `SMASHBURGER1`, you don't.

**One caution, and it is a real one.** These names are not cosmetic to
the model — they are part of the prompt it reads, and it picks moves by
id. A funny name costs nothing, but a *misleading* one costs plays: call
a cautious vision move `NUKE` and you have quietly told the model that
option is aggressive. The rule is simple — be as silly as you like about
the *tone*, never about the *content*. `SMASHBURGER` for a smash on your
own pure seam is perfect. `SMASHBURGER` for a defensive probe is a bug
you will spend an hour not finding.

**Rename all of it, not most of it.** An id is written in more than one
place: where the option is minted, in the line that tells the model how
to execute it, and in any doctrine or wishlist text that recommends it
by name. `PRSNAP` appears in three files. If your doctrine still says
"prefer PRSNAP" after you renamed the option to `BODYGUARD`, you have
told the model to pick a move that is not on the menu — it will either
ignore the advice or invent the id. That exact bug shipped here once and
took a while to spot, because nothing crashes; the agent just quietly
stops taking a play you thought you had recommended.

Grep your own folder for the old name before you call it done.

Keep them unique enough to grep, and remember they are public: they end
up in the league, on a screen, in front of the room.

---

## 6b. Giving your agent its first weapon shape

This is the heart of the day, so it gets its own stage.

**Start from the truth that V12 cannot fight.** Run the ladder against
the baseline and you get four FAILs:

```
$ python scripts/soc.py weapons --agent tabula_v12

  1. the agent knows it owns a rack               FAIL
  2. the play is offerable and compilable         FAIL
  3. the offer explains itself                    FAIL
  4. doctrine says when, and at what tempo        FAIL
```

That is not a bug and not an oversight — it is the exercise. V12 buys
weapons and never fires them. Every fork in the room starts from an
agent that owns a rack it does not know how to use, and the teams that
place well are the ones that fix that first.

### Decide the shape before anyone types

Three questions, ten minutes, whole team. Do it before a line is
written, because the answers change what you build.

1. **How many weapons?** One, done properly, beats three done vaguely.
   SNAP is the cheapest to reason about (100 blue, one cell, one hour)
   and the cheapest to *afford*, so it is the usual first pick. EMP is
   the crowd-pleaser. Chaff at 300 is the hardest to justify and the
   easiest to waste.
2. **What is the one sentence?** "We kill the eye that lights their
   pure, the night they were going to land on it." If you cannot say it
   in one sentence, the doctrine you write will not say it either.
3. **What is it called?** See above. Name it now — the name goes in
   four places and renaming later is the bug in the previous section.

### The four surfaces, and what each one is for

A weapon play is not one edit. It is four, in this order, and each is
invisible until the one before it works — which is exactly why teams get
stuck and conclude "the model is stupid". It is almost never the model.

| # | Surface | File in your fork | What it means |
| - | ------- | ----------------- | ------------- |
| 1 | **Know the rack** | `world_view.py`, `prompt.py` | The night phase is told what you own. Reading it in `orbit_policy.py` doesn't count — that's the buying code, and it knows by construction |
| 2 | **Offer and compile** | `chat_schema.py`, `agency.py`, `packager.py` | The schema permits the verb, the menu carries the play, the packager passes it through. All three or nothing fires |
| 3 | **Explain the offer** | `agency.py` — `Option.detail` and `Option.rationale` | Bare geometry is not a decision. Say what firing *buys* |
| 4 | **Doctrine** | `doctrine.py`, `_v7/orbit_wishlist.py` | When to reach for it, and at what tempo. An option nobody is told to want stays unwanted |

Check yourself at any point with `soc weapons --agent <yours>`. It reads
your source, needs no model and no credentials, and runs in a second, so
it belongs in the inner loop rather than at the end of it. It is a smoke
test: it answers "have you built this rung", never "is it any good".

### The anatomy of one weapon option

Everything in rungs 2 and 3 is one object. Here is the shape, with a
team's own flavour on it:

```python
Option(
    option_id="EMP_BONANZA",
    kind="emp",
    title="EMP BONANZA — three of their eyes, one cloud, (14,9)",
    detail=(
        "One EMP, radius 2, 8-hour cloud. Catches rival probes at "
        "(13,8), (14,9) and (15,10) — every eye they have on the "
        "north seam. Costs 200 blue and one hour-slot."
    ),
    execute_lines=["EMP_BONANZA: emp at [14, 9]"],
    rationale=(
        "Their whole northern read is three probes in a 2-cell "
        "huddle, which is a filing error, not a formation. One "
        "cloud blinds all of it for 8 hours. They cannot drop into "
        "cells they cannot see (§3.9.7), so tonight's landing on "
        "your pure at (15,11) is refused — not delayed. Compare: "
        "SNAP is 100 blue and kills exactly one of the three, which "
        "leaves the drop legal from the other two."
    ),
)
```

Four things that example is doing deliberately, and all four are worth
copying:

- The **title** is one line and leads with the outcome, not the
  coordinates.
- The **detail** is honest arithmetic — cost, radius, duration, what is
  actually caught.
- The **rationale** argues *against the alternative*. This is the single
  highest-value habit in the whole harness: the model is choosing
  between options, so an option that only praises itself is competing
  badly.
- The name is silly and the content is exact. `EMP_BONANZA` genuinely is
  a bonanza — three probes in one cloud. That is the rule from the
  previous section doing its job.

### What the model does with it

Options go into the menu grouped by kind, with a header and a one-line
blurb about what that *kind* of play brings. The model then argues with
itself. A real night, lightly dramatised:

> **Menu says:** `GRAB1` — 340 red at (7,3), visible, no probe needed.
> `EMP_BONANZA` — three eyes, one cloud, (14,9). `PRSNAP1` — a second
> probe over your landing at (15,11), insurance only, banks nothing.
>
> **Thinker:** GRAB1 banks tonight and risks nothing. BONANZA banks
> nothing tonight — it takes away *their* night. I hold 340 red I can
> see and they are about to take a pure off me. Denial is worth more
> than the bank here, and it is only worth it because all three probes
> sit in one radius; next night they will not.
>
> **Also considered:** PRSNAP1 insures the landing for 0 blue and one
> probe, but insurance protects my grab and does nothing to their
> tempo. BONANZA does both — blind them and my landing is safe by
> consequence.
>
> **Plan:** `[EMP_BONANZA, GRAB1]`, posture aggressive.

Which lands in the log, in front of the whole room, as:

```
[REDWATCH_REAPER] [plan=aggressive: EMP_BONANZA, GRAB1] [predicted=high] moves=11
```

The useful thing about that dialogue is what it reveals when it goes
*wrong*. If your agent keeps declining EMP_BONANZA, the rationale is
losing an argument — and you can read exactly which one, on the card,
and rewrite the sentence that lost it. That is the loop. It is much
faster than tuning numbers.

### Plans worth arguing about

Give your team a few shapes to fight over. None of these is the right
answer; the arguing is the point.

- **The bonanza.** Wait for clustered probes, blind the lot. High
  ceiling, needs patience, and the window closes fast.
- **The tempo tax.** A cheap SNAP most nights, aimed at whichever single
  eye is lighting their best target. Never spectacular, always annoying.
- **The insurance policy.** Never fire offensively; spend everything on
  covering your own landings. Boring, surprisingly hard to beat.
- **The bluff.** Buy 600 of arsenal early and fire none of it. Everyone
  can see your total and not what it is, so they play around a rack you
  are not using. Cheeky, and it costs you 600 blue you could have spent.
- **The all-in.** Chaff on the lift hour, every time you can afford it.
  Either brilliant or an expensive way to make noise.

---

## 6c. How the team actually works — the cycle

The mistake is for five people to take turns at one keyboard. The build
is one person's work for an hour; the *knowing what to build* is
everyone's, and it can happen at the same time.

### Split into three tracks, on purpose

| Track | People | What they do | Output |
| ----- | ------ | ------------ | ------ |
| **Build** | 1–2 | Implement the four surfaces. Head down, few opinions, keeps pushing | An agent that fires |
| **Scout** | 1–2 | Play V12. Lose to it. Work out how to exploit it | A list of nefarious moves |
| **QA** | 1–2 | Run seasons and lab turns, read cards, feed transcripts to the assistant | A ranked list of what's broken |

The scouts matter more than they sound. **Every agent in the room is
descended from V12**, including everyone else's. So a weakness you find
in V12 while playing it is, on the day, a weakness in most of the field.
Playing the baseline is competitive research, not warm-up — and it needs
no code, no credentials and nobody's permission, so it can start the
moment the build track begins.

### The loop, once the build lands

```
  scout finds a move ──► build implements ──► QA runs seasons + lab
        ▲                                              │
        └──────────────  ranked feedback  ◄────────────┘
                    (leader keeps the list)
                              │
                          push between
                          every round
```

Push between rounds, not at the end. A fork you pushed an hour ago is a
fork you can go back to.

### Benchmark against your own past self

This is the trick that tells you whether you are improving or just
changing. Snapshot the agent before a round of edits, under a new name:

```bash
python scripts/soc.py share --agent redwatch_reaper --out v1.socfork
python scripts/soc.py grab v1.socfork --as-name reaper_v1
```

`grab` repoints the copy at its new name, so both run side by side with
nothing to register. Now improve `redwatch_reaper` freely — and when you
want to know if it worked, play your new one against your old one:

```bash
python scripts/soc.py season --p1 redwatch_reaper --p2 reaper_v1 --seed 99
```

That is a far sharper signal than "we beat the heuristic again". Beating
the bot proves you are competent; beating yesterday's self proves you
are improving.

> **Snapshots are for you, not the league.** `soc push --agent` publishes
> one directory, so push your real agent and leave the `_v1` copies
> local. A team that publishes five variants has five half-agents in the
> table instead of one good one.

### Run seasons in parallel — this is the cheap bit

Seasons are separate processes writing to separate stores, so you can
just run them at once. Three full heuristic seasons on this machine:

```bash
python scripts/soc.py season --p1 redwatch_reaper --p2 red_harvest      --seed 11 --name A --quiet &
python scripts/soc.py season --p1 redwatch_reaper --p2 tabula_v12       --seed 22 --name B --quiet &
python scripts/soc.py season --p1 redwatch_reaper --p2 reaper_v1        --seed 33 --name C --quiet &
wait
```

Measured: **three full seasons in 19 seconds** wall clock, at 241% CPU —
they genuinely run in parallel rather than queueing. Seasons with an LLM
seat are far slower, because they are dominated by inference latency
rather than CPU — which is precisely why parallel matters more there,
not less. Three LLM seasons cost roughly what one costs in wall-clock
time.

Vary the *opponent*, not the seed, across the three. One against the
armed heuristic tells you whether you are competent; one against V12
tells you whether you beat the baseline the field is built from; one
against your previous version tells you whether today was worth it.

### Feed the transcripts to your coding assistant

Every season writes a Markdown card per turn and one combined file:

```
reports/seasons/<NAME>_<id>/all-cards.md     (paste this to an agent)
reports/seasons/<NAME>_<id>/cards/           (one file per turn)
```

A heuristic season's combined file is around 550 lines — small enough to
paste whole. This is the highest-leverage thing QA does all day: hand
the assistant the cards and ask *why*, not *what*. "Here is the season.
On night 3 we declined EMP_BONANZA with three probes in radius. Read the
rationale and tell me which argument lost."

Geometry faults especially fall out of this quickly — an agent firing at
a cell one off the cluster, or insuring a landing it was never going to
make, is obvious in a transcript and nearly invisible in a score.

Keep QA and the assistant busy on the cards while the build track works.
That is the whole reason to split the team: nobody is waiting.

---

## 7. Evaluate — the three ways to test an agent

There are exactly three, they answer different questions, and they cost
wildly different amounts of time. Teams routinely do only the slow one
and then wonder why the result is inconclusive.

| | Answers | Costs | Needs a PAT |
| --- | --- | --- | --- |
| **Lab** — one frozen turn | *Why* did it choose that? | ~20s a take | Yes |
| **Headless** — a whole season | Does the doctrine hold up over an arc? | ~15s a turn, unattended | Yes |
| **Play against it** | What is it actually like to face? | A real sitting | Yes |

Do them in that order. The lab tells you what your agent is thinking, the
season tells you whether that thinking survives contact, and playing it
tells you the thing neither will ever print — whether it is any fun, and
whether its weapon does what you imagined when you named it.

### 1. The lab — one frozen turn, side by side

A frozen turn is a real turn from a real season, snapshotted the instant
before a seat planned. You cast your fork into that seat and compare its
decision against V12's on identical information. Nothing you do here can
touch a live game: the lab clones the board and writes only to its own
directory.

```bash
python scripts/soc.py lab            # list the turns and forks, no server
python run_web.py                    # then open /lab in the browser
```

Pick a board, arm the seats from the ARMS control if you want to see how
your agent behaves against a rack, and cast your fork from the AGENT tab.
A take comes back in about twenty seconds and brings its reasoning with
it — the plan tag, the option it picked, and the prompt it was given.

This is the only one of the three that shows you *why*, which is why
skipping it makes the other two hard to act on. A season score tells you
that you lost; the lab tells you that your agent read the arsenal, feared
a SNAP that could not exist, and covered a probe it did not need to.

### 2. Headless — a whole season, no browser

```bash
python scripts/soc.py season \
  --p1 redwatch_reaper --p2 tabula_v12 \
  --days 7 --seed 99 --name ROUND1
```

Offline and replayable by default. It writes a Markdown card per turn and
prints a URL that opens the finished season in the normal game UI:

```
  cards      reports/seasons/ROUND1_.../all-cards.md   (paste this to an agent)
  watch it   SOC_BACKEND=file python run_web.py
             http://127.0.0.1:8000/?session=...
```

That `all-cards.md` is the highest-value artifact of the day for a team
working through a coding assistant — it is the whole season as text, and
you can paste it straight into the assistant and ask what went wrong.

### 3. Play against it yourself

The one teams forget, and the one that catches the embarrassing stuff.

```bash
SOC_BACKEND=file python run_web.py
```

Then **Play**, and pick your own agent for the opposing seat. It takes
its turn automatically after you transmit; you do not invoke it.

**One thing will stop you, and it is deliberate.** An LLM seat is
*refused* on the Memory backend, in these words:

```
p2 run an LLM agent. Those play on a persistent backend so the season,
and the agent's reasoning for every turn, are still there to replay
afterwards — which is what the turn suite and the advisor read.
```

That is a product rule, not a technical limit — the point is that a match
you cannot reopen teaches you nothing. Any persistent backend clears it,
and `file` needs no Snowflake account, so `SOC_BACKEND=file` is the
answer for almost everyone. Pick the backend in the New Game modal, or
set it in the environment as above.

Play three or four nights, not one. What you are looking for is not the
score — it is whether the agent ever does something that makes you say
"why would it do *that*", because that sentence is the highest-quality
bug report your team will get all day.

**Checkpoint.** One lab take you can explain, one season you have
actually watched, and one game you have personally lost or won against
your own agent. A score nobody watched is a rumour.

> **Will the server see a newly minted agent?**
>
> It depends on one flag, and this catches people every year.
>
> - `python run_web.py` — **reload is on by default.** Minting writes new
>   `.py` files, the watcher notices, the server restarts itself, and the
>   agent appears in the New Game dropdown and the lab within about ten
>   seconds. No action needed beyond a browser refresh.
> - `python run_web.py --no-reload` — **it will not appear.** The roster
>   is built once, when the server imports the registry. Verified: mint
>   an agent next to a running `--no-reload` server and `soc list` shows
>   it while the server's own roster still does not. Restart it, and both
>   the New Game dropdown and the lab pick it up.
>
> `--no-reload` is the right flag for a long game or a real session, so
> the case where you must restart is exactly the case where you least
> want to. Mint before you start the server, not after.

---

## 8. Collate feedback — the leader's stage

By now four people have opinions and three of them contradict. Your job
is to turn that into one ranked list before anyone edits anything.

The discipline that makes round two work:

1. **Write the complaint as an observation, not a fix.** "It bought chaff
   on day 2 with no rival in vision" beats "make it buy chaff later".
2. **Attach evidence** — a lab board and seat, or a turn number in a
   season card. An unevidenced complaint costs an hour and finds nothing.
3. **Rank ruthlessly.** You will implement two or three, not nine.
4. **Cut anything that is a game-rules disagreement** rather than an
   agent bug. Those are for the organisers, not your afternoon.

**Checkpoint.** A numbered list, in your team channel, ordered, with
evidence against each item. Share it with the central team — see below.

---

## 9. Round two — implement and test

Only now does anyone edit. Rules that keep this from going wrong:

- **Everything stays inside your harness directory.** `soc push` enforces
  this and will refuse a diff that reaches outside it. If you find
  yourself wanting to change the engine, that is a conversation with the
  organisers, not a commit.
- **Your tests live in your folder too:**
  `sea_of_colours/orchestrator_2/harnesses/redwatch_reaper/tests/`.
- **Re-run the same seed.** Changing the agent and the seed at once tells
  you nothing.

If two people are working on the same agent, they cannot both push to the
same fork conveniently — pass it as one file instead:

```bash
python scripts/soc.py share --agent redwatch_reaper   # writes a .socfork
python scripts/soc.py grab redwatch_reaper.socfork    # on their machine
```

**Checkpoint.** Same seed, same opponent, better result — or a clear
understanding of why not.

---

## 10. Final polish and final push

Stop editing earlier than feels comfortable. The last hour is for
verification, not features.

```bash
python scripts/soc.py doctor        # clean?
python scripts/soc.py list          # your agent still listed?
python scripts/soc.py season --p1 redwatch_reaper --p2 tabula_v12 --days 3
python scripts/soc.py push -m "final"
```

**Checkpoint.** Your fork on GitHub carries the agent you intend to
enter, and it ran a season within the last thirty minutes.

The failure mode this prevents: a team whose final refactor broke
discovery, pushed at the buzzer, and enters the league as a directory the
loader skips.

---

## 11. The league

The organiser collects the field into a **separate staging checkout**,
not into this repo and not into anyone's fork — attendees are pulling
from upstream all day, and forty entrants landing here would hand the
room a conflict.

```bash
python scripts/soc.py collect --dry-run   # lists the forks it can see
python scripts/soc.py collect             # assembles ../soc-league
python scripts/soc.py league
```

Collection resets before each run, so it is a snapshot of the forks as
they stand — not an accumulation of every earlier run. It will not take a
fork's `tabula_v12` (every fork has one; the baseline comes from
upstream) and will not silently resolve two forks claiming the same name.

---

## Appendix: how an agent actually becomes runnable

Worth understanding, because when an agent "does not appear" this is
always the reason.

Minting writes a directory containing an `agent.json`. Nothing registers
it explicitly. Instead, when `binding_registry` is imported it scans
`harnesses/` for directories containing an `agent.json`, validates each,
and folds the valid ones into the routing table. That import-time scan is
the whole mechanism.

Two consequences, and both catch people:

**A fresh process sees your agent immediately.** `soc list`, `soc lab`,
`soc season` and `soc doctor` each start Python anew, so they pick up a
just-minted agent with no further step.

**A running server depends on whether it is watching for changes.** The
roster is built when the server imports the registry, so the question is
only ever "has this process imported it since you minted?".

- `python run_web.py` — reload is **on** by default. New `.py` files trip
  the watcher, the server restarts itself, and the agent turns up in both
  the New Game dropdown and the lab's cast list within about ten seconds.
- `python run_web.py --no-reload` — reload is off, nothing re-imports,
  and the agent is invisible to that server until you restart it. This is
  the flag AGENTS.md gives for a real session, and the one a long game
  should use, so the trap is set exactly where it hurts most.

Either way, hard-refresh the browser tab afterwards: the page caches the
roster it was served. And if you know you are about to mint, mint first
and start the server second.

### Where it can silently not appear

| Symptom | Cause | Fix |
| --- | --- | --- |
| Not in the New Game dropdown, but `soc list` shows it | Server was started `--no-reload` before you minted | Restart `run_web.py`, hard-refresh |
| Missing everywhere, no error | Invalid `agent.json` — most often no `participants` | `soc doctor` prints it under PROBLEMS |
| Listed but not selectable | No `menu_label` in `agent.json` | Add one |
| In the dropdown but the seat does nothing all night | No PAT — the harness falls back silently | `soc doctor`; see SNOWFLAKE_SETUP.md |
| Rejected at game creation | LLM seat on the Memory backend | Choose a persistent backend |

A malformed manifest only removes **that one agent**. Everyone else's
still loads, which is deliberate — one team's typo must not stop the
room.

### Does it need Snowflake?

| Surface | Needs a PAT? |
| --- | --- |
| `soc list`, `soc doctor` | No |
| Tutorial, playing vs heuristics | No |
| Turn lab — casting an LLM fork | Yes, or it quietly falls back to a heuristic |
| Headless season with an LLM seat | Yes, same silent fallback |
| Web New Game with an LLM seat | Yes — and it is checked up front, so this one refuses rather than disappoints |

The Memory backend refuses LLM seats outright. That is a product rule,
not a technical limit: V12 runs fine on memory, but a match you cannot
reopen teaches you nothing, and the whole point of the day is the
iteration loop. Any persistent backend will do — including `file`.

The fallbacks are the trap — a season that "ran fine" may have been the
built-in heuristic the whole way. A headless season at least says so, in
these words:

```
  WARNING: 14 turn(s) fell back to the built-in heuristic — the model was
  not reached, so that part of this season is not your agent's.
```

If you see that, your result is not a measurement of your agent. Fix the
credentials before you interpret anything.
