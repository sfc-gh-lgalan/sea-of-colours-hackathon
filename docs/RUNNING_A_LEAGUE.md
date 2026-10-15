# Running a league

How to take a room full of forked agents and produce a champion, a
public bracket and a replay of every night played. Written immediately
after running the first one, while the things that went wrong were
still fresh.

The first league: **17 entrants, 16 matches, 44 seasons, 3.7 hours of
wallclock, 2.7 GB of replays, 2 fallback turns.** Champion
`cadogan_sinopec_finally_came_up_with_agent_name`, 4–1 over
`yayfeng_main` in a best-of-seven final.

**Design lives next to the code** — see `sea_of_colours/league/README.md`
for why the bracket, the match rules and the runner are shaped the way
they are. This file is the sequence and the operational traps.

---

## The shape

- **Knockout**, not Swiss. A field that is still growing right up to
  the draw wants Swiss; a field that wants *a champion by the end of
  the day* wants knockout, because Swiss needs five rounds before it
  can rank anybody and knockout halves the work each round.
- **Best of three**, sides swapping, aggregate points as the
  tie-break. The starting corners are not equal, so a tie played from
  one seat measures the map as much as the agent.
- **Best of seven for the final** (v1.50). One season has a lot of
  variance in it — an LLM seat plays the same board differently twice
  — and three is enough to sort a field but thin for the one match
  that names a champion.
- **Play-ins** when the field is not a power of two. 17 entrants means
  one play-in between the two lowest seeds, and the other fifteen go
  straight into a bracket of 16.

Cost, for a field of `n`: `bracket_size(n) - 1 + play_ins` matches, and
at worst `3` seasons each with `7` for the final. For 17 that is 16
matches and at most 52 seasons. `soc cup plan` prints this for you
before you commit to anything.

---

## The sequence

### 1. Collect the field

The day runs on GitHub forks, so the agents are not in this repo.

```bash
soc collect                      # assembles ../soc-league from the forks
```

This builds the field in a **separate staging checkout**, never in this
repo and never in anyone's fork — attendees pull from upstream all day
and forty entrants landing here would hand the room a conflict. It
resets before each run, so a collection is a snapshot of the forks
rather than a pile of every earlier run.

Two things it deliberately refuses: a fork's own `tabula_v12` (every
fork has one; the baseline comes from upstream), and two forks claiming
the same agent name.

> **Trap: this is slow the first time.** The repo carries ~466 MB of
> checked-in tutorial films, and cloning forty forks means paying for
> them forty times. Use a shallow, blobless, sparse clone when you are
> only after `agent.json` and Python:
>
> ```bash
> git clone --depth 1 --filter=blob:none --sparse <fork-url>
> git -C <dir> sparse-checkout set sea_of_colours/orchestrator_2/harnesses
> ```
>
> `scripts/league_inventory.py` already does this and is the fast way
> to see what the field looks like before you commit to running it.

### 2. Draft the roster

```bash
soc cup roster --cutoff 2026-09-16T14:00:00+02:00
```

Writes `reports/league/roster.json`, seeded by each agent's **first
commit** — so `--cutoff` is how you say "only agents built today".
Agents committed before it are recorded but not entered.

**The roster is the editable source of truth and it is meant to be
edited.** Seed order is the file's order, and the organiser decides who
competes. Nothing downstream re-derives the field, so an agent you
delete from here does not play, full stop.

`soc cup roster` refuses to overwrite without `--force`, because
regenerating a roster mid-league reseeds the bracket under matches
already played.

### 3. Look before you leap

```bash
soc cup plan
```

Prints the round about to be played, the seeds, and **every season's
sides and board seed** without running anything. Run this before every
round. It is also how you confirm the bracket resumed into the fixtures
you expected after a crash.

### 4. Play a round

```bash
soc cup run --jobs 4 --backend file
```

One worker per **match**, not per season, because whether a decider
gets played depends on the first two — handing seasons to the pool puts
the competition rule inside the concurrency code.

`--jobs 4` means **eight model seats against Cortex at once**.

> **There is no rate limiter anywhere in the Cortex path.**
> `orchestrator_2/cortex_chat.py` posts straight out, so `--jobs` is
> the only thing standing between your league and an account-wide 429.
> 4 was comfortable; we did not test higher.

Resumable at match granularity: a match with a file on disk is not
re-offered, so a crashed round picks up where it stopped.

**For the final, or any single match, add `--wide`:**

```bash
soc cup run --wide --jobs 3 --backend file
```

A one-match round leaves the whole machine idle behind one season at a
time. `--wide` runs the seasons the match is *certain* to play
concurrently — the first three of a best-of-seven, since you cannot
take four from three — then strictly one at a time after that. It also
resumes at *season* granularity, so a final stopped at three seasons of
seven can be finished without replaying the three that are already
good.

### 5. Publish

```bash
soc cup site
```

Writes three things from the same data, and they must never go out of
step:

- `reports/league/league.js` — the live bracket at
  **`http://127.0.0.1:8000/league/`** (needs the server up; the page
  reads a generated blob, so opening `league/index.html` off disk shows
  an empty bracket).
- `league/announce.html` — the **shareable announcement**,
  self-contained, for posting to Slack *before and during*. Rewritten
  in place between sentinel comments (`// <<<SOC_RESULTS` …
  `// >>>SOC_RESULTS`); everything outside them is hand-written prose
  and is left alone.
- `reports/league/review.html` — the **shareable full record**, for
  *after*. Entirely generated, entirely self-contained, ~46 KB: every
  tie with its season-by-season scores behind a hover, weapon stickers
  reading `fired / bought` (a dashed sticker is a rack paid for and
  never used), the field with teams and participants, and an armoury
  ledger. This is the file you send the room when it is over. It is
  **output** — change `sea_of_colours/league/review.py`, never the
  HTML, and re-run `soc cup site`.

> **A static page cannot report its own breakage.** All three draw
> their whole body from a baked blob, so a thrown exception or a
> renamed field renders a tidy, well-styled page with *nothing in it*
> — and it looks fine until somebody opens the file you emailed to
> seventeen teams. After touching any of them, run
> `backstage/probes/_probe_league_review.py` (and
> `_probe_battle_room.py` for its sibling). It counts what got drawn,
> hovers a tie to prove the score card appears, and fails if a sticker
> escapes its box or a `fetch()` sneaks in.

`soc cup site` is **not automatic**. Run it after every round, or the
page shows a league that finished an hour ago.

---

## Watch these while it runs

**Fallback turns.** A model-backed seat that times out plays its
built-in heuristic instead, and the season is then partly won by
RED_HARVEST rather than by the agent. Surfaced rather than swallowed —
`soc cup run` flags them per match and the site shows them. The first
league had 2 across 44 seasons, which is noise; a match full of them
should be replayed.

**Cortex reachability.** LLM calls that fail return `HTTP 401` under an
IP network policy and the seat **silently falls back to the
heuristic**. A whole league can complete, look clean, and be a
heuristic tournament. If fallback turns spike, check the network policy
before you check the agents.

**Disk.** ~61 MB per season, almost all of it `frames.jsonl`. The first
league cost 2.7 GB. Budget it.

---

## Hygiene

```bash
soc prune --local-resumable --yes      # local seasons stuck mid-game
soc prune --local --keep league --yes  # everything local except the league
```

`--keep` takes a season name or id prefix and is repeatable. **Always
`--keep` the league** — deleting its sessions does not change the
result, but it kills every replay link on the published bracket.

Without `--yes` it is a dry run. There is no global session index, so
`soc prune` is the supported path; `rm -rf seasons/<id>` also works.

---

## Adding a late entrant

Adding a name to the roster mid-league **reseeds the bracket** and
orphans every tie already played. So don't.

Instead run a **challenger match**: the late entry plays an agent that
has already qualified, for that agent's slot in the next round.

1. Declare it in `reports/league/challenger_pending.json` with `id`,
   `stage`, `a`, `b`, `for_slot` and a `note` explaining itself. It
   appears on the bracket immediately as "commissioned, not started".
2. Play it into `reports/league/challenger/`, **not** `matches/`.
   `fixtures` derives the whole bracket from the roster plus whatever
   is in `matches/`, so a challenger filed there would either be
   ignored or shift the slots under a round still being played.
3. Seed the winner into the next round **by hand**.

The cost is that hand step. The benefit is that a late entry cannot
perturb a single tie.

---

## Traps, all of them learned the hard way

**A season played in the parent process is not saved.** The store
backend resolves at **import time**, and the parent has already
imported most of the tree to work out the fixtures — so pointing it at
a store with an env var does nothing, and it falls back to `memory`.
The scores come back, get written into the match file, and the session
evaporates with the process. Two games of the first final read as
played, 4–1, with no replay, cards or stats behind them.

Every season now runs inside a pool even when there is only one, and
`tests/test_league_runner_isolation.py` has an AST scan that fails if
`_play_game` is called outside the worker entry point. **If you add a
code path that plays a season, it goes through the pool.**

**The backend must be durable.** `file` or `snowflake`. The runner
refuses `memory` outright — on it a season evaporates when its worker
exits and every replay link on the site 404s. That guard did not save
us from the bug above, because the bug was not on the path the guard
was on.

**Never hardcode "first to two".** Match length is per stage, so the
wins needed must be *derived* (`fixtures.wins_needed`). The literal `2`
lived in four places and any one left behind would have awarded the
best-of-seven final to whoever reached two first — ending it 2–1 at
game three and never playing games four or five.

The same mistake hides in test helpers: one that swept every round 2–0
stopped completing the final, so `next_round` handed the final back
forever and the suite **hung** instead of failing. Bracket-walking
tests need a round counter.

**Match-level resume is too coarse for a long match.** A match file
exists from its first season onward, so `already_played` is true
immediately. `soc cup run --wide` passes `include_partial=True` to
`runner.plan` to get a partly played match offered back.

**Write after every season, not at the end.** A match takes a quarter
of an hour and a final takes half; writing once meant the site showed
nothing until it finished, so a round in progress looked identical to
one nobody had started. Writes are atomic (temp file, rename) and
readers already handle an incomplete match.

**Check what the kill-feed is actually counting.** Both finalists
finished credited with probes "EMP'd" having never bought an EMP — the
SNAP reuses the EMP's blast sweep internally and the kills landed in
`emp_probes`. Fixed in v1.50 (`snap_probes`), but the general lesson
stands: if a stat says an agent used a weapon it never bought, the stat
is wrong, not the agent.

---

## Reading a result honestly

**Weapons bought minus weapons fired is the most useful number on the
page.** The first league's finalists bought 27 charges and fired 15.
Every unfired snap is 100 blue and 250 credits of harvesting capacity
sitting in a rack. An agent with a big unfired count has a buying
policy its plays cannot consume — which is a tuning problem, not a bug.

**A gap between "selected" and "fired" is a bug; a gap between
"offered" and "selected" is not.** Both finalists compiled and fired
every weapon play they put in a plan. What looked like a broken
pipeline on another agent turned out to be a play whose preconditions
were simply rare. Check the funnel — offered → in a plan → compiled →
fired — before blaming the harness.

**One season is not evidence.** LLM seats play the same seed
differently. That is the entire reason for best-of-three, and for the
final being longer.

---

## Files

```
sea_of_colours/league/          the code — see its README.md
reports/league/roster.json      the field. EDITABLE source of truth
reports/league/matches/         one JSON per bracket match
reports/league/challenger/      late entries, kept out of the bracket
reports/league/challenger_pending.json   commissioned, not yet played
reports/league/league.js        generated — what /league/ reads
reports/league/review.html      generated — the shareable full record
league/index.html               the live bracket (source, hand-written)
league/announce.html            the shareable announcement (prose + baked block)
seasons/<session_id>/           replays and per-turn cards
scripts/league_inventory.py     organiser-side survey of the forks
```

Replay one season directly at
`http://127.0.0.1:8000/play?session=<session_id>`.
