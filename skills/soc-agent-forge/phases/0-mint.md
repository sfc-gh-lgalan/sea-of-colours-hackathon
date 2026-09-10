# Phase 0 — Fork, mint, publish

Target: **under one minute** once the fork exists. Nothing here needs a
decision, so do not turn it into a conversation.

Full detail in `references/publishing.md`. Read that if anything below refuses.

## Step 0 — is this their fork, or the organiser's repo?

Ask before anything else, because this is the step that goes wrong and it is
**invisible until the deadline**. A team can install, play, mint and improve for
six hours against the wrong remote and only find out when they publish.

```bash
python scripts/soc.py doctor
```

The line that matters:

```
publishing to    ssh://git@github.com/<owner>/sea-of-colours-hackathon.git
```

If `<owner>` is the organiser rather than the team, stop. They cloned the
original instead of forking it. Fix it now:

```bash
# press Fork on GitHub, then point origin at the fork
git remote set-url origin https://github.com/<them>/sea-of-colours-hackathon.git
git remote add upstream https://github.com/sfc-gh-lgalan/sea-of-colours-hackathon.git
```

`origin` is theirs and the only place they push. `upstream` is where kit fixes
come from and they never push to it.

Also check `No problems found`, and `LLM credentials present` for anyone who will
run an LLM agent.

## What actually needs Snowflake

Teams over-estimate this badly:

| To do this | You need |
| --- | --- |
| Play, tutorials, face the heuristics | **Nothing** |
| Run tests, mint an agent, `soc lab` listing | **Nothing** |
| Run your own LLM agent, or play against V12 | **A PAT.** That is it |
| Keep seasons in your Snowflake account | The extras and a schema deploy |

An LLM seat needs a *persistent* backend, but persistent does not mean
Snowflake — `SOC_BACKEND=file` is a durable local store that accepts LLM seats.

## Ask for the name and the participants

Two things, together, in one question. Do not ask them separately.

- **Team and agent name.** The label is `<team>_<name>` — so `--team redwatch
  --name reaper` gives `redwatch_reaper`, shown in the game as
  `REDWATCH_REAPER`. This string goes in every later command, so get it right
  now; renaming later means re-pushing under a new identity.
- **Participants.** Required, and it is the league's public record. Everyone who
  touches the agent goes in. A row that names nobody cannot be credited.

## Mint

Validate first — it costs nothing and catches a bad name before any file is
written. Note this runs `new_agent.py` **directly**: `--dry-run` exists on the
minter but is not forwarded through `soc new`, so `soc new --dry-run` errors with
*"unrecognized arguments"*.

```bash
python scripts/new_agent.py --team redwatch --name reaper \
  --participants 'Ada Lovelace, Grace Hopper, Alan Turing' --dry-run
```

It reports what it would create:

```
--dry-run: would create .../harnesses/redwatch_reaper/ (47 files + agent.json)
--dry-run: would register 'redwatch_reaper' by discovery
```

Then mint for real through `soc`:

```bash
python scripts/soc.py new --team redwatch --name reaper \
  --participants 'Ada Lovelace, Grace Hopper, Alan Turing'
```

The name must match `^[a-z][a-z0-9_]*$` after lowercasing. Rejected: spaces,
hyphens, a leading digit, punctuation, a trailing underscore. It also refuses a
collision with a built-in or an existing fork. All of these fail *before*
writing, so a rejection has cost nothing.

## What it did

One new directory, and the `agent.json` inside it **is** the registration —
`agent_manifest.discover()` scans `harnesses/` at import time for exactly that
file. Nothing shared is edited: no registry, no import list, no central table.
That is the property that lets forty teams work simultaneously, and every rule in
`references/publishing.md` exists to protect it.

```bash
git status --short
# ?? sea_of_colours/orchestrator_2/harnesses/redwatch_reaper/
```

Inside is a complete working agent: 47 files, 23,168 lines, V12 copied
wholesale with its identity renamed. The fork *starts* at parity with the
baseline. The team differentiates from a working agent rather than building one.

**Never edit `tabula_v12` itself.** It is the control group for the whole room —
take its label and every comparison on the day becomes meaningless.

## Publish it empty, before changing anything

```bash
python scripts/soc.py push --dry-run    # always preview first
python scripts/soc.py push
```

This feels pointless on an untouched fork and it is the highest-value five
seconds of the day. It proves the remote is right, the directory is registrable,
and the team has a scoring entry — while there is still all day to fix any of
those.

Two refusals to expect, and both are the tool doing its job:

- **"origin is not yours"** — they cloned the organiser's repo instead of
  forking. Step 0 should have caught it; fix the remote and retry.
- **"changes outside `<your directory>`"** — a single edited file *anywhere* else
  blocks the push, even though the commit would never have included it. This is
  not fussiness. Only that one directory is lifted into the league, so an
  out-of-folder change silently **does not travel** and the agent then behaves
  differently there than on the laptop. Revert with
  `git checkout -- <path>` and push again.

`--force` exists but is hidden from `--help`, which is the kit telling you
something: if a change outside the folder is genuinely needed, it is a change to
the **kit**, and that is a conversation with an organiser.

**Pushing is not entering.** The fork is where the agent lives; the organiser
assembles the league from the forks at the end. So push as they go — whatever is
on the fork at collection time is what plays.

## Set expectations before moving on

Say this now, unprompted, because it prevents the most common early derail:

> `soc weapons` on your fresh fork will show four FAILs. That is correct. V12
> buys weapons and never fires them — every fork in the room starts from an
> agent that owns a rack it cannot use, and fixing that first is what places
> well.

## Gate

`python scripts/soc.py list` shows the label under AGENTS, and the directory is
visible on the team's fork on GitHub. Then go to `phases/1-choose.md`.
