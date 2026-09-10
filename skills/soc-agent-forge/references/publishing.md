# Publishing — how an agent gets from a laptop into the league

Read this before Phase 0, and again before the final push. Most of the ways a
team loses a day's work live in here, and every one of them is silent until the
deadline.

## The idea underneath: an agent is one directory

This is the design decision everything else follows from. **Registering an agent
edits nothing shared.** There is no registry file to add yourself to, no config,
no queue. `agent_manifest.discover()` scans `harnesses/` at import time for any
directory containing an `agent.json`, and that file *is* the registration.

That single choice buys three things:

- forty teams can work simultaneously without touching a common file
- an entrant is a **directory** rather than a merge
- at the end, an organiser can lift each agent out of its fork and set it beside
  thirty-nine others with nothing to resolve

Everything below is a consequence. When a rule seems fussy, it is protecting
this property.

## Stage 0 — fork on GitHub, then clone *your* fork

Before anything else, and it is the step that goes wrong.

```bash
# press Fork on GitHub FIRST, then:
git clone https://github.com/<you>/sea-of-colours-hackathon.git
cd sea-of-colours-hackathon
git remote add upstream https://github.com/sfc-gh-lgalan/sea-of-colours-hackathon.git
```

`origin` is theirs and the only place they push. `upstream` is where kit fixes
come from and they never push to it. Nobody but the organiser has write access to
the event repo — that is the mechanism, not a precaution. One team's work cannot
land in another's, and there is no queue to join.

**Cloning the original by mistake is the expensive error, because nothing goes
wrong until they publish.** They can install, play, mint and improve for six
hours against the wrong remote and only discover it at the deadline. That is why
`soc doctor` reports the remote in one line, and why Phase 0 runs it first:

```
publishing to    ssh://git@github.com/sfc-gh-lgalan/sea-of-colours-hackathon.git
```

If that line names the organiser rather than the team, stop and fix it now.

## Stage 1 — mint

```bash
python scripts/soc.py new --team redwatch --name reaper \
    --participants "Ada Lovelace, Grace Hopper"
```

`new_agent.py` copies `harnesses/tabula_v12/` to `harnesses/redwatch_reaper/`,
repoints the copied imports, and writes an `agent.json` inside it:

```json
{
  "team": "redwatch",
  "name": "reaper",
  "participants": ["Ada Lovelace", "Grace Hopper"],
  "menu_label": "REDWATCH_REAPER — redwatch's agent (needs a Snowflake PAT · slow)",
  "entry": "harness:run",
  "needs_llm": true
}
```

Three details worth knowing:

- The directory is `<team>_<name>` so two teams cannot both claim `reaper`, and
  both parts must be lowercase ASCII because the name becomes a **package
  directory**.
- All three arguments are required, `--participants` included. The league table
  is the day's public record, and a row naming nobody cannot be credited or
  chased.
- The minter prepends a banner to the copied `README`. Without it, the mint's
  "now read your README" step points at a document whose first instruction is
  *don't edit this directory* — i.e. instructions to undo the mint.

**Never edit `tabula_v12` itself.** It is the control group for the whole room.
Take its label and every comparison on the day becomes meaningless.

## Stage 2 — improve, inside that directory only

The fork is a complete copy, so everything in it is theirs — including
`orbit_policy.py`, which was moved *inside* the harness in v1.40 precisely so
that "buy an EMP on day one" does not require editing kit that `soc push`
forbids.

## Stage 3 — `soc push`

Not a plain `git push`. It runs these steps in order:

1. **Is this remote yours?** Reads `git remote get-url origin`, parses
   `owner/name`, compares against `gh api user --jq .login`. Mismatch → refuse,
   with the exact recovery commands. Deliberately **best-effort**: without the
   `gh` CLI it cannot know who you are, and a check that cannot run must not
   block.
2. **Which agent is yours?** From the `agent.json` scan. With more than one fork
   present it stops and asks for `--agent <label>`.
3. **What changed?** `git status --porcelain -z --untracked-files=all`.
4. **Refuse if anything changed outside your directory.** The load-bearing
   guardrail — see below.
5. `git add -- <your directory>`
6. `git commit -m "<label>: update agent"` (or `-m`)
7. `git push origin <current-branch>`. On a non-fast-forward rejection it runs
   `git pull --rebase origin <branch>` and retries, up to twice.

**Rebasing is safe *because of* the one-directory rule.** The only commits in
flight touch different directories, so histories interleave with nothing to
resolve. If a rebase does conflict, something outside a folder moved — get an
organiser rather than forcing it.

## The guardrail, and why it is not about tidiness

Step 4 is the one that surprises people. A single edited file *anywhere* outside
the agent directory blocks the push, even though the commit would never have
included it. The tool's own words:

> Your agent has to be self-contained, because at the end of the day only this
> directory is lifted out of your fork and into the league. **A change outside it
> will not travel — it will simply be missing, and your agent will behave
> differently there than it does here.**

That is the real risk: not mess, but **divergence**. An out-of-folder change
works perfectly on the laptop, silently does not travel, and the agent then
behaves differently in the league — discovered at the worst possible moment.

So it is stricter than the commit on purpose: `soc push` only ever *stages* the
agent directory, but it *refuses outright* if stray changes exist anywhere. An
unrelated edited doc blocks the push until reverted.

```bash
git checkout -- <path>      # revert the stray file
python scripts/soc.py push --dry-run   # confirm clean
```

**Always `--dry-run` first.** It previews without writing, and on a dirty tree it
prints the exact list to revert.

`--force` exists but is hidden from `--help` (`argparse.SUPPRESS`), which is the
kit telling you something: if a change outside the folder is genuinely necessary
then it is a change to the **kit**, not to the agent. Raise it with an organiser.
Do not force it in.

**Gitignored files do not count** — which is exactly why `*.socfork` is in
`.gitignore`. A parcel left in the repo root would otherwise block a push it has
nothing to do with.

## push vs share vs enter — three different things

| | Command | For |
| --- | --- | --- |
| **Hand to a teammate** | `soc share --agent <label>` → `.socfork`, then `soc grab <file>` | working in parallel right now |
| **Publish to your fork** | `soc push` | the league, and a fallback point |
| **Enter the league** | *nothing* — the organiser collects | — |

**Pushing is not entering.** The fork is where the agent lives; the league is
assembled from the forks. So push as you go — whatever is on the fork at
collection time is what plays.

And push *between rounds*, not at the end. A fork pushed an hour ago is a fork
you can go back to.

**Snapshots stay local.** `soc share` + `soc grab --as-team <team> --as-name <name>_v1`
(both rename flags are required — `--as-name` alone errors) gives a
side-by-side benchmark against your own past self with nothing to register — but
push only the real agent. A team that publishes five variants enters five
half-agents instead of one good one.

## Stage 4 — collection (organiser side)

`soc collect` builds the field in `../soc-league`, a **separate staging
checkout** — never in this repo and never in anyone's fork, because attendees
pull from `upstream` all day and forty entrants landing on `main` is forty merge
conflicts posted to the whole room at once.

Per fork it runs `git fetch --depth 1 <url> <branch>` then
`git checkout FETCH_HEAD -- <dir>`, landing one directory without a second full
clone on disk. It **resets before each run**, so a collection is a true snapshot
of the forks rather than an accumulation of earlier attempts.

It will not take a fork's `tabula_v12` (every fork has one; the baseline comes
from upstream), and two forks claiming one name is **reported and skipped**
rather than silently resolved — those are different teams' work.

Nothing there trusts a fork: paths are validated so a crafted tree cannot write
outside `harnesses/`, and a broken manifest is a skipped entrant rather than a
crashed collection. The Python does still run when it is cast, which is stated
rather than hidden.

## The final-hour checklist

Stop editing earlier than feels comfortable. The last hour is verification.

```bash
python scripts/soc.py doctor        # remote right? credentials present?
python scripts/soc.py list          # agent still discovered?
python scripts/soc.py push --dry-run    # nothing stray outside the folder?
python scripts/soc.py season --p1 <label> --p2 tabula_v12 --days 3
python scripts/soc.py push -m "final"
```

The failure this prevents: a team whose last refactor broke discovery, pushed at
the buzzer, and enters the league as a directory the loader skips.
