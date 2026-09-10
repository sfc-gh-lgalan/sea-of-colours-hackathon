# The verification ladder

Six surfaces. They answer different questions and cost wildly different amounts
of time. Work top-down. Teams routinely do only the slowest and then wonder why
the result was inconclusive.

| Surface | Command | Loop time | PAT | Answers |
| --- | --- | --- | --- | --- |
| Unit tests | `python -m pytest tests -q` | seconds | no | is the kit still sound |
| Scenario evals | `python -m scripts.run_evals --format compact` | ms each | no | 16 tactical situations |
| Wiring smoke | `python scripts/soc.py weapons --agent <label>` | ~1s | no | which rung am I stuck on |
| **Frozen turn** | `run_web.py` → `/lab` | ~20s a take | yes | **why did it choose that** |
| Headless season | `soc season …` | minutes (LLM) | yes | does it survive an arc |
| Human play | `SOC_BACKEND=file run_web.py` | a sitting | yes | what is it like to face |

Two of these need no credentials at all and run in under a second, which makes
them the right inner loop.

## The credential-free fast loop

Anything in `orbit_policy.py` — buying, caps, blue thresholds — is **deterministic
Python with no LLM call**. Orbit runs as a heuristic; the model is never asked.

That means the whole *economics* half of the weapon problem is testable with
`pytest` in seconds, with no PAT and no latency. If a team is blocked on
credentials, or waiting on a season, this is the work that can proceed anyway.

The *firing* half needs the slow LLM loop. Run them as two tracks.

## Why the lab comes before the season

The lab is the only surface that shows you **why**, which is why skipping it makes
the other two hard to act on. A season score tells you that you lost. The lab
tells you your agent read the arsenal, feared a SNAP that could not exist, and
covered a probe it did not need to.

A frozen turn is a real turn from a real season, snapshotted the instant before a
seat planned. Casting a fork into it is the engine actually running that night —
not a reconstruction. Nothing you do there can touch a live game: the lab clones
the board and writes only to its own directory.

Start with **`plain_night_armed`** — day 4, *no redsign, no pure*, rack loaded.
It is the case where the agent has ordnance and nothing to fear. If it will not
fire there it will not fire anywhere, and it is exactly the board where stock V12
loads no weapon doctrine at all.

Then use `[ vs V12 ]` for the side-by-side diff on identical information. Two
things to know:

- V12's answers are **pre-frozen**, so it is a stable reference, not a live
  re-ask.
- V12 baselines are recorded **unarmed by design** — its reply schema has no
  weapon verbs. A rack grants stock but never changes its schema. The gap is the
  exercise.

One thing the lab cannot answer: whether the agent would *buy* the weapon. The
lab stamps a rack rather than playing an orbit phase, so procurement is
unmeasurable there. `soc season` is the blunt instrument for that.

## Run a turn more than once

An LLM is not deterministic. One lab take is an anecdote. If a decision matters,
take it three times before believing it — and if the three disagree, that
instability is itself the finding.

## Seasons: parallel, and vary the opponent

Separate processes, separate stores, so they genuinely run at once — three
heuristic seasons measured at 19s wall clock, 241% CPU. LLM seasons are dominated
by inference latency rather than CPU, so parallel matters *more* there: three cost
roughly what one costs.

Vary the **opponent**, not the seed:

- vs `red_harvest` (armed heuristic) — are we competent
- vs `tabula_v12` — do we beat the baseline the whole field is built from
- vs `<label>_v1` — was today worth it

That last one is the sharpest signal available. Beating the bot proves you are
competent; beating yesterday's self proves you are improving. Snapshot with
`soc share` + `soc grab --as-name <label>_v1`, which repoints the copy so both
run side by side with nothing to register.

When re-testing a change, **hold the seed.** Changing the agent and the seed at
once tells you nothing.

## Never `--backend memory`

`soc season` already defaults to `file` — offline, no Snowflake account,
replayable, and it prints the replay URL for you. Memory keeps the report cards
and destroys the session, so there is nothing to open in the UI. It buys almost
nothing: 2.3s for a 3-day heuristic season on `file`, plus an integrity check.

Worth knowing so nobody conflates the two senses of "memory": the memory
*backend* does **not** affect the agent's memory. A season is one process, so the
in-process store still threads the STRATEGY JOURNAL and LAST NIGHT blocks across
all seven days. What the memory backend costs is replay durability, nothing else.

The season and the server must agree on backend. A `file` season and a
default-backend server will not find each other, which looks exactly like a
broken replay.

## Read `fallback_turns` before believing any score

`season.json` carries it. Non-zero means part of that season was the built-in
heuristic, not the agent, because a missing PAT falls back silently per turn.
Check it first, every time.
