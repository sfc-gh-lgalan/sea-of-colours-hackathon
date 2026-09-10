# Building and shipping your agent

Everything you need for the day, in the order you need it. If you read
one section, read [Publishing](#7-publishing-your-agent) — it is what
gets you into the league.

> Running a team rather than writing the agent yourself? Start with the
> [Team leader's guide](TEAM_LEADER_GUIDE.md), which sequences the whole
> day and says what "done" looks like at each stage. Come back here for
> how anything actually works.

---

## 0. Fork first, then clone your fork

<a id="fork-first"></a>

Before anything else. On GitHub, press **Fork** on the repo. Clone the
copy that lands in your own account — not the original:

```bash
git clone https://github.com/<you>/sea-of-colours-hackathon.git
cd sea-of-colours-hackathon
git remote add upstream https://github.com/sfc-gh-lgalan/sea-of-colours-hackathon.git
```

You work in your fork all day. `origin` is yours and is the only place
you push; `upstream` is where kit fixes come from and you never push to
it. Nobody has write access to the repo the event runs from, which is
the point — one team's work cannot land in another's.

**Cloning the original by mistake is the expensive error**, because
nothing goes wrong until you publish: you can install, play, mint an
agent and improve it for six hours against the wrong remote and only
find out at the deadline. `soc doctor` tells you in one line, so run it
early. If you have already done it, `soc push` prints the fix and your
work comes across intact.

**Use the Fork button — making your own empty repo is not the same
thing.** It looks identical from your laptop: full history, your own
copy, `push` works, `doctor` is happy that the remote is yours. What is
missing is GitHub's link back to this repo, and exactly one thing needs
it — the league is collected by walking this repo's *forks*, so an
unlinked copy is never found and never scored, with no error to tell
anyone. `soc doctor` and `soc push` both call this out (v1.47), and the
fix is thirty seconds that loses nothing:

```bash
gh repo fork sfc-gh-lgalan/sea-of-colours-hackathon --remote=false
git remote set-url origin https://github.com/<you>/sea-of-colours-hackathon.git
```

Renaming your fork afterwards is fine — the link survives it.

---

## 1. Mint your agent

```bash
python scripts/soc.py new --team redwatch --name reaper \
    --participants "Ada Lovelace, Grace Hopper"
```

That forks the shipped V12 harness into
`sea_of_colours/orchestrator_2/harnesses/redwatch_reaper/` and declares
it in an `agent.json` inside that folder.

**All three are required, including the names.** The league table at the
end of the day is the public record of who built what, and a row reading
`redwatch_reaper` and nothing else cannot be credited to anybody or
chased if it breaks. List everyone at the table — real names, handles,
nicknames, whatever you answer to. You can edit the list in your
`agent.json` later when someone joins.

**Your agent is one directory.** Nothing outside it was touched, and
nothing outside it needs to change again — not a registry, not a config,
not the UI. The `agent.json` written inside it *is* the registration:
the kit finds agents by scanning for those files, so there is no shared
list for you to add yourself to and no queue to join.

That one-directory rule is what the rest of the day rests on. It is why
forty teams can work at once without treading on each other, and it is
why the organiser can lift your agent out of your fork at the end and
drop it beside everyone else's without a single conflict to resolve.

Restart the server and your agent is in the New Game dropdown.

> **Do not edit `tabula_v12` itself.** It is the baseline every fork is
> measured against, and it is the control group for the whole room. Take
> its label and every comparison on the day becomes meaningless — which
> is why the tooling refuses to let you.

---

## 2. Improve it

Your fork is a complete copy of V12, so everything is yours to change.
Its own `README.md` is the tour: the pipeline, the two gaps it ships
with on purpose, and where to change what.

**That includes what it buys.** The orbit-phase buying policy is
`orbit_policy.py` *inside your fork* — credits, BLUE, when a weapon is
worth a harvester. It used to be shared code outside every harness,
which meant changing it was a change to the kit and `soc push` would
refuse it. Since v1.40 each agent owns its own, so "buy an EMP the first
day I can afford one" is a change to one file in your own directory.
Every fork starts with a copy that behaves exactly like V12's, so any
difference in what your agent buys is a difference you chose.

Most people will do this through a coding assistant rather than by hand.
That works well, and the kit is built for it — but point the assistant
at the fork's README first, and give it something concrete to aim at:

> "Read `harnesses/redwatch_reaper/README.md`. Then open the turn lab,
> run my fork and stock V12 on **Beaten to the Seam**, and use the
> divergence view to find the first place they disagree."

### The two gaps worth knowing about

Stock V12 ships with two deliberate holes. They are the exercise:

1. **It buys weapons and never fires them.** Open any lab turn twice —
   once with the seat's rack empty, once with an EMP in it — and see
   whether a single order changes. If nothing does, your agent has not
   learned to fight, and that is the largest single opportunity in the
   kit. See [the four rungs](#the-four-rungs-of-firing-a-weapon) below.
2. **Its plan menu cannot express some correct plays.** Sometimes the
   agent's own reasoning finds the right answer and the option list has
   no way to say it. The divergence view compares the two menus option
   by option, which is where this shows up: your fork was never offered
   the move it needed.

### The four rungs of firing a weapon

Gap 1 is the one most teams pick, so it is worth knowing the shape
before you start. Firing needs **four independent things** to be true,
and they have to be built in order, because each is invisible until the
one before it works:

1. **The agent knows it owns a rack.** Today only the buying code reads
   `weapon_stock`; the night phase is never told. It cannot choose a
   weapon it does not know it has.
2. **The play is offerable and compilable.** Three places must agree —
   the move schema has to allow the verb, `agency.py` has to register an
   option that emits it, and the packager has to pass it through.
3. **The offer explains itself.** A menu line of bare geometry is not a
   decision. Say what firing here buys, in human terms: *"3 rival probes
   under 2 clouds — blinds their eye on your pure for 8h."* `Option` has
   `detail` and `rationale` fields waiting for exactly this.
4. **Doctrine says when, and at what tempo.** The shipped doctrine
   teaches surviving EMP and chaff, never using them. An option nobody
   is told to reach for stays unreached.

**This whole job has been done once, on a real fork, and written up.**
[`docs/TEACHING_WEAPONS.md`](TEACHING_WEAPONS.md) walks all four rungs
with every file named — the schema change that silently kills the play
if you miss it, the shaped salvo that leaves a hole you can stand in,
the orbit bug that left the rack empty with the blue already banked, and
what happened when it ran. Read it before starting; it will save you the
afternoon.

Its headline finding is worth having in advance: **building all four
rungs does not make an agent fire.** At the end of that build the menu
offered the salvo, the doctrine argued for it, and the model still chose
a harvest chain three times out of three. The four rungs get you to the
point where firing is *possible and reasoned about*. Making it actually
happen is a separate problem — ranking, framing, how the cost is
quoted — and you cannot start on it until the rungs are done.

You do not have to track that by hand:

```bash
python scripts/soc.py weapons --agent redwatch_reaper
```

prints the ladder, marks each rung, and names the single next action. It
reads your fork's source, so it needs no model and no credentials and
runs instantly.

**This is the split worth internalising.** Rungs 1–3 are *code* — make
the move possible, and compute where to aim. Rung 4 is *doctrine* —
prose telling the model when firing beats harvesting. Skip the code and
the model wants to fire but has no verb to say it with. Skip the
doctrine and the option sits in the menu, never chosen. When a play
never happens, check in that order: was the **option** offered, could
the **schema** express it, did the **packager** survive it. Only after
those three is it actually the model's judgement.

---

## 3. Test it on a frozen turn

```bash
python run_web.py            # then open http://127.0.0.1:8000/lab
```

A **frozen turn** is a real turn out of a real season, snapshotted the
instant before a seat planned: the same board, the same fog, the same
memories and the same journal that seat actually had — the journal
travels inside the board, so it costs no account and no credentials.
Only **Vetus Lantern** carries one, because it is the only night in the
library an LLM played; the rest were played by RED_HARVEST, which keeps
no diary, and their agents are told so rather than told nothing. Pick one, choose
who sits in each seat — stock V12, a heuristic, your fork — optionally
hand a seat a weapon, and hit go. You watch the night resolve in the
ordinary game UI, because it *is* the ordinary game UI; the lab does not
draw its own.

There is no score, and that is deliberate. A mark out of ten tells you
almost nothing you can act on. The question worth asking is **how is my
fork different from V12 on this exact position, and why** — so the lab
answers that one instead, in a divergence view that diffs your agent's
prompt, reasoning, plan and issued orders against a frozen V12 take of
the same turn. The baselines are checked in, so that comparison costs no
credentials and no waiting.

Six nights ship in the library:

| Night | What it is for |
|---|---|
| **Vanilla Opener** · day 1 | the cold open — no map, no seam, no history, just an opening move |
| **Early Redsign Battle** · day 2 | both seats found the same seam on night one — who commits, who blinks |
| **Beaten to the Seam** · day 3 | you just lost a race 6-to-1, and a second seam is up with you better placed |
| **Second Wind** · day 4 | the trailing seat just banked the best lift of the game and doubled its fleet |
| **After the Gold Rush** · day 4 | both seams are spent — what does an agent do with no jackpot |
| **Vetus Lantern** · day 6 | the leader is discovered and cannot hide — blind attack, EMP denial, chaff; the one night that carries five nights of real V12 journal |

`python scripts/soc.py lab` lists them, and the forks that can play them,
without starting a server. To add your own, grab a day out of a season
you have already played:

```bash
python -m turnlab grab <season> <day>
```

Two things to be careful about:

- **Run a turn more than once.** An LLM is not deterministic, and a
  position it plays well once in three is a position it does not
  understand.
- **Watch for the fallback warning.** If your harness cannot reach its
  model it plays a built-in safety net, and that is not your agent's
  reasoning. The lab says so rather than quietly showing you orders.

If something looks wrong with the kit rather than your agent:

```bash
python scripts/soc.py doctor
```

### The old scoring suite

`soc suite` and `soc why` scored a fork against ten *constructed*
boards — positions assembled to pose a question, rather than positions
a season actually reached. They still run and still pass their tests,
but they are **deprecated as of v1.42** and print a notice saying so.
`soc diff` was the same idea for a single turn and is **gone as of
v1.43**; the divergence view above replaces it and diffs the prompts
too. The lab replaced them because a real frozen turn asks a
better question and is far less to keep true as the rules move. See
[the suite's README](../sea_of_colours/evals/battles/README.md) if you
need the rungs and loadouts.

---

## 4. Read the divergence

Watching the night tells you *that* a turn went differently. To see why,
open the divergence view from the turn you just ran. It puts your fork's
take beside stock V12's on the same frozen turn and diffs the whole
thing, in the order you should debug in:

1. **Orders issued** — what actually reached the engine, colour-coded
   where the two agents disagree.
2. **Corrections** — where the compiler rewrote or dropped an order.
3. **Options** — the menu each agent chose from, compared by option id,
   so you can see whether yours was even *offered* the play V12 took.
4. **Reasoning** — the thinker pass, in full.
5. **The prompt**, verbatim and uncut, broken into the three sections
   the agent actually receives, with the parts that differ from V12
   marked. A `[ RAW ]` toggle gives you the whole thing as one
   continuous diff if you would rather read it straight.

That order is the debugging order, and it is worth following even when
you are sure you know the answer. A bad play with sound reasoning is a
**compiler** problem. Sound reasoning off a bad menu is an **options**
problem. Only when the menu and the prompt are both right is the model
itself worth blaming. Check the menu before you blame the model.

The board also carries the plan overlay: hover a seat's invocation and
its proposed orders paint onto the map in the same graphics the game
uses for a queued policy — numbered path badges, dashed legs, probe and
EMP markers, blast footprints. Two forks' plans on one position, one at
a time, is usually faster than reading either of them.

`[ CARD ]` and `[ CARD .MD ]` give you the same turn as the Markdown
card a full season would have produced, if you want to keep it or paste
it somewhere.

---

## 5. Play a whole season

The lab is for one night at a time. It will not tell
you whether your agent can hold a **season** together — bank early
enough, buy the right thing on day two, still be scoring on the last
night. An agent can play every individual night well and still finish
last, and the only way to find that out is to play the whole thing:

```bash
python scripts/soc.py season --p1 redwatch_reaper --p2 red_harvest
```

Pick whoever you like for the other seats — another fork, stock
`tabula_v12`, `red_harvest_lite` for an easier opponent, up to `--p4`.
It runs offline and prints the result:

```
  Aurum_Vallis  (8f2c19a4…)
  5/5 days  ·  22 turns  ·  41.3s

    p1   redwatch_reaper           2043  <- winner
    p2   red_harvest               1622
```

Two things come out of it.

**The season is in your replays.** It is written through the same store
the live server reads, so start the server and watch it back exactly
like a game you played by hand — the whole night, hour by hour, both
seats:

```bash
SOC_BACKEND=file python run_web.py
# then open the URL the command printed
```

`SOC_BACKEND=file` matters. A bare `python run_web.py` auto-detects,
which on most laptops means the in-memory store — and an in-memory
server cannot see a season on disk, so your replay list comes up empty
and it looks like the season was never written. The `soc season` output
prints the exact command to use.

**Every turn's card is kept.** One Markdown file per turn under
`reports/seasons/<season>/cards/`, plus an `all-cards.md` with the lot
in play order. Orbit and night are separate files, because buying is
half the game and a season lost on day two is usually lost in orbit.
Each card has the rationale, the orders, the option menu, what the model
said and the prompt it was given — the same things the lab shows, in a
form you can read in an editor or paste to a coding agent and ask what
went wrong.

The same cards also come out as **`all-cards.html`**: one page with a
day rail down the side and a coloured tab per section, so finding "day
three, p1, what was it offered" is two clicks instead of scrolling
1,400 lines. Use whichever suits the reader — the HTML for you, the
Markdown for your coding agent, which does much better with plain text.
Both are rendered from the same data, so they cannot disagree.

You can also pull the cards from a game you played in the browser: the
AGENT tab has **`[ READ ]`** (opens the HTML in a new tab) and
**`[ .MD ]`** (downloads the Markdown).

A few flags worth knowing:

| flag | what it does |
|---|---|
| `--seed N` | same board every time — the way to compare two versions of your agent |
| `--days N` | shorter season while iterating |
| `--p3` / `--p4` | more seats |
| `--backend memory` | fastest, keeps nothing, not replayable |
| `--json` | machine-readable result |

**A season with an LLM seat takes real minutes**, since every seat calls
a model every night. Use the lab for the fast loop — one turn is about
twenty seconds — and a season to confirm the change held up. If the run reports turns that *fell back*,
your agent never reached its model and that part of the season is the
built-in heuristic, not you — check `soc doctor`.

---

## 6. Passing it round your team

Two of you on one agent is the normal shape, and you do not want to
publish to the whole room every time you want the other person to try
something. `soc share` writes your fork to a single file:

```bash
python scripts/soc.py share
#   packed redwatch_reaper — 51 files, 470K
#   /Users/you/sea-of-colours/redwatch_reaper.socfork
#
#   fingerprint 431c4f80 …
```

Send that file however you already talk to each other — chat, email,
AirDrop, a USB stick. On the other laptop:

```bash
python scripts/soc.py grab redwatch_reaper.socfork
```

It is castable in the lab and selectable in the New Game menu straight
away, with nothing to register. Three things worth knowing:

- **`grab` runs their Python.** That is the point of it, but it means
  grabbing from someone you do not know is the same decision as running
  a script they sent you. The format only carries `.py`, `.md` and
  `.json`, and unpacking cannot write outside the one fork directory, so
  a parcel cannot touch the engine or your other agents — but the code
  inside does execute when you cast it.
- **The fingerprint settles arguments.** Packing the same code always
  produces the same one, so "I'm on 431c4f80, what are you on?" answers
  *are we in sync* without either of you unpacking anything.
- **You can hold both at once.** By default `grab` refuses to overwrite
  an agent you already have. `--force` replaces yours; `--as-team mine
  --as-name theirs` installs it alongside under a new label, imports
  repointed, so you can cast both into the same frozen turn and diff
  them.

Parcels are gitignored, so one sitting in your repo will not upset
`soc push`.

---

## 7. Publishing your agent

**You work in your own fork.** You forked the repo before you cloned it
([§0](#0-fork-first-then-clone-your-fork)), so `origin` is *your* copy
on GitHub and you are the only person
who can write to it. Nobody pushes to the repo the event runs from —
not you, not the other teams, not the organisers during the day.

**Push early and push often.** An agent that only exists on your laptop
does not compete.

```bash
python scripts/soc.py push
```

That commits **your agent's directory and nothing else**, then pushes to
your fork. Run it as many times as you like — every improvement, every
hour, it does not matter. There is no submission deadline ritual and no
form to fill in; whatever is on your fork when the organiser collects is
what plays.

**Pushing is not entering.** Your fork is where your agent lives. The
league is assembled separately, out of everyone's forks, by an organiser
running `soc collect` (§8). This is worth holding onto because it
explains the two things that surprise people: your push cannot break
anyone else's agent, and your agent does not appear in anybody's repo
but your own until collection.

### Taking kit updates

Fixes to the engine or the harness land upstream during the day. Pull
them into your fork whenever you like:

```bash
git pull upstream main
```

That is the only thing `upstream` is for. If you do not have that remote,
you skipped a line in §1:

```bash
git remote add upstream https://github.com/sfc-gh-lgalan/sea-of-colours-hackathon.git
```

### Why it refuses sometimes

**"origin is not yours."** You cloned the original repo instead of
forking it first. Everything works up to this point, which is why it is
worth checking early — `soc doctor` reports it. Nothing is lost: `soc
push` prints the two commands that repoint this clone at your own fork,
and your work comes with it.

**"changes outside <your folder>."** If you have changed files elsewhere,
`push` stops and lists them:

```
error: changes outside sea_of_colours/orchestrator_2/harnesses/redwatch_reaper/:
  server/static/app.js
```

This is the one rule the day depends on, and the reason is collection.
Only your own directory is lifted out of your fork and into the league,
so a change outside it does not travel — it is silently absent there,
and your agent behaves differently in the league than it does at your
desk. That is a miserable thing to debug at 17:00. Move what you need
into your folder, revert the rest with `git checkout -- <path>`, and
push again.

If you genuinely need a change outside your folder, that is a change to
the *kit* rather than to your agent — flag it to an organiser rather
than forcing it in, so everyone gets it.

### Checking you are in

```bash
python scripts/soc.py doctor    # remote, credentials, discovery — all of it
```

`doctor` prints what `origin` points at. If that is not your fork, fix it
now rather than at the deadline.

---

## 8. The league

### How the field is assembled

Your agent is in your fork; thirty-nine others are in theirs. An
organiser gathers them:

```bash
python scripts/soc.py collect          # --dry-run first to see who is out there
```

That finds every fork of the repo, takes the agent directory out of each
one, and assembles them all in a **separate checkout** — `../soc-league`
by default. The repo everybody is pulling from is never written to, and
neither is anybody's fork. Each agent goes on living in the fork it came
from; the staging area is only where the league is *run*.

Collection is a snapshot, not an accumulation: it resets to upstream and
re-fetches everyone each time, so re-running always gives a true picture
of the forks as they stand. It writes an `ENTRANTS.md` naming every agent
collected, the fork it came from and who is on the team.

Two things it will not do. It will not take a fork's copy of
`tabula_v12` — the baseline has to come from upstream or it means
nothing, and every fork carries a copy. And it will not silently pick a
winner when two forks claim the same agent name: the clash is reported
and both are held back, because that is two teams' work and guessing is
the one outcome nobody wants. Distinct `--team` names make this a
non-event.

### Then the league runs

Over the same boards at the same rungs, in the staging area:

```bash
cd ../soc-league
python scripts/soc.py league --runs 3 --up-to siege --cards reports/league
```

The entrant list there is simply every directory with an `agent.json` —
which is exactly why registration is a file in your own folder and not a
line in a shared one. Nobody can be left out because a merge went wrong,
because there are no merges.

A broken entrant cannot take the league down with it. A manifest that
will not parse is skipped with a warning naming the file and the error;
an agent whose code will not import runs, fails and scores 0% rather
than aborting the run. Check you are not that entrant with `soc doctor`,
which lists the same problems before the day ends rather than after.

Agents whose harness never reached a model are marked with `*` and their
fallback rate is printed. They still appear — a silently missing entrant
is worse than a marked one — but the table says plainly that the score
is partly the safety net's.

---

## Command reference

| command | what it does |
|---|---|
| `soc new --team T --name N --participants "..."` | fork V12 into your own agent |
| `soc lab` | the frozen turns, and every fork that can play them |
| `soc season` | play a whole headless season with your fork in a seat |
| `soc weapons --agent A` | which of the four firing rungs you are stuck on |
| `soc doctor` | check the kit before blaming your agent |
| `soc share` | pack your fork into one file for a teammate |
| `soc grab F` | install a fork a teammate shared with you |
| `soc push` | publish your agent to your GitHub fork (your folder only) |
| `soc league` | run every collected agent and rank them |

Organisers only:

| command | what it does |
|---|---|
| `soc collect` | gather every fork's agent into a staging area for the league |

Testing a fork happens in the lab rather than at the command line —
`python run_web.py`, then `/lab`, or the **Turn Lab** button on the
landing page. `python -m turnlab grab <season> <day>` adds a night of
your own to the library.

Deprecated as of v1.42, still working, and superseded by the lab:
`soc suite`, `soc why`, `soc list`. Each prints a notice naming what to
use instead. `soc diff` was removed in v1.43 — use the divergence view.

Every command takes `--help`, and `soc` on its own lists them all.
