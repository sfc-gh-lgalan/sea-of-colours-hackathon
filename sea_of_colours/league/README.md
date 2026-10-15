# `sea_of_colours/league/` — the day's public record

Takes a field of forked agents and produces a champion, a bracket
anyone can read, and a replay of every night played.

**To actually run one, read `docs/RUNNING_A_LEAGUE.md`.** This file is
why the code is shaped the way it is, and which invariants you will
break if you rearrange it.

## The five modules

| | |
|---|---|
| `roster.py` | who competes. Drafts from an inventory by first-commit cutoff, then gets **edited by hand** — the organiser decides the field, and nothing downstream re-derives it. |
| `fixtures.py` | pure functions. The bracket, the seeding, the match rules. No IO, no engine, no store — which is why 66 tests over it run in under a tenth of a second. |
| `runner.py` | plays the fixtures, several at once, resumably. Owns the process boundary. |
| `stats.py` | reads a played season back out of the store and reduces it to weapons, kills and score. |
| `site.py` | turns results into the blob `/league/` renders, and bakes the shareable announcement. |
| `review.py` | the whole league as one self-contained file — `reports/league/review.html`. The record, for after. |

The dependency order is that order, and it is one-directional.
`fixtures` knows nothing about a store; `stats` knows nothing about a
bracket.

## Invariants

**The competition rule lives in `fixtures`, never in the concurrency
code.** A job is a match, not a season, because whether a decider gets
played depends on the first two. The one exception is
`play_match_wide`, which parallelises exactly
`fixtures.unconditional_games(stage)` seasons — the ones no other
result can make irrelevant — and that number is *derived*, not chosen.

**Wins needed is derived from match length.** `wins_needed(stage)` is
`best_of(stage) // 2 + 1`. The literal `2` used to appear in four
places, and any one of them surviving would award a best-of-seven final
to whoever reached two first. If you add a stage-length, do not write
down how many wins it takes.

**Every season is played in a fresh process.** The store backend
resolves at *import* time, so only a process that has not yet imported
the engine can be pointed at a store. A season played in the parent
silently resolves to `memory`: the scores come back, get written down,
and the session evaporates. This cost two games of the first final
their replays. `tests/test_league_runner_isolation.py` has an AST scan
that fails if `_play_game` is called from anywhere but the worker entry
point — **it is guarding a hole that is invisible at runtime.**

**The store must be durable.** `file`, `snowflake` or `multi`. The
runner refuses `memory` rather than discovering later that every replay
link on the site is dead.

**One file per match, written after every season.** Several processes
appending to a shared log is a corruption waiting for a long match
card; a file per match is crash-safe and makes resuming a directory
listing. Written after each season rather than at the end, because a
final takes half an hour and a page that shows nothing until it
finishes cannot be told from one that never started. Writes go through
a temp file and a rename, and readers already handle an incomplete
match.

**Spawn, not fork.** Snowpark connections are not fork-safe and a
forked child inherits one it does not own. The failures are
intermittent and awful to diagnose.

**Late entrants go beside the bracket, not in it.** `fixtures` derives
the entire bracket from the roster plus whatever is in `matches/`, so
adding a name mid-league reseeds everything and orphans every tie
already played. A challenger match lives in `challenger/` and its
winner is seeded on by hand — see the runbook.

## Where the shape came from

Knockout rather than Swiss because the day needs a champion by the end
of it: Swiss cannot rank anybody until five rounds are in, and knockout
halves the field each round. Best-of-three because one season of Sea of
Colours has real variance in it and a single game measures the board as
much as the agent. Sides swap for the same reason — the starting
corners are not equal. Aggregate points break a tie only after wins,
because a season lost by four hundred and two won by one each is still
two wins.

The final is best-of-seven (v1.50) on the same logic taken one step
further: three sorts a field, but the match that names a champion
deserves more than one bad board's worth of luck.

## Three pages, and why

| page | needs | for |
|---|---|---|
| `/league/` (`league/index.html` + generated `league.js`) | a server | watching it happen, and clicking into real replays |
| `league/announce.html` | a browser | posting the draw and the standings as they land |
| `reports/league/review.html` (from `review.py`) | a browser | the record, sent to the room when it is over |

The two shareable ones **must not fetch anything**. The moment either
depends on a sibling file it stops being shareable, which is the only
reason it exists. Both inherit their tokens from `manual/manual.css`
so they sit beside the guides without a seam.

`announce.html` is *source* with one generated block, because it is
mostly hand-written prose. `review.html` is *generated whole*, because
there is nothing in it to preserve — edit `review.py` instead.

None of them can report their own breakage: the body is drawn from a
baked blob, so an exception in the draw code yields a tidy page with
nothing in it. `backstage/probes/_probe_league_review.py` is what
catches that.

## Tests

- `tests/test_league_knockout.py` — the bracket and match rules, pure
  and fast. Includes the best-of-seven cases and the seeding property
  that keeps seeds 1 and 2 apart until the final.
- `tests/test_league_runner_isolation.py` — the process boundary and
  partial-match resume. Structural, not behavioural: it reads the
  source, because the bug it guards is about *which process* a call
  happens in and no assertion on a return value can see that.
