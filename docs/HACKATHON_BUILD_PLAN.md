# Hackathon repo build plan

This repo (`sea-of-colours-hackathon`) is a from-scratch port of the
`sea_of_colours` dev repo, purpose-built as a hackathon distribution:
clone it, play a game, then build your own agent to compete. This doc
is the working plan for finishing that port — read this **before**
picking up any of the pending phases below. It is the single
source-of-truth for "what's done, what's left, what a phase means."

Original dev repo (for reference / diffing, not part of this repo):
`/Users/lgalan/Desktop/sea_of_colours` on the machine this was ported
from. That repo remains the primary/working copy; this one is the
distribution artifact for hackathon attendees only.

## North star — what the attendee experience actually is

Every phase below serves this arc. When a phase decision is ambiguous,
resolve it in favour of this:

1. **Clone and play, fast.** Minutes, not an afternoon.
   `pip install -r requirements.txt` → `python run_web.py` → in a game
   against a heuristic opponent. The first opponent is
   `RED_HARVEST_LITE` (weapons off) so a first-timer isn't being EMP'd
   and chaffed while still learning what a parcel is; `RED_HARVEST`
   (weapons on) is the step up.

   **The tutorial destination is the game running on the attendee's own
   Snowflake demo account** — that's the point of the event, and the
   guide walks them there. But it must be a *destination*, not a
   gate: the store backend **auto-detects** (Snowflake when the config
   and deps are present, memory otherwise) so nobody is ever staring at
   a stack trace before they've seen the board. See Phase 3 — the two
   Snowflake dependencies are independent and the onboarding must not
   conflate them.
2. **Play the person next to them.** The one-click cloudflared tunnel +
   QR-invite flow already works and is a highlight of the event, not a
   side feature — it's what makes the room social. Preserving it
   end-to-end is a hard requirement (Phase 3).
3. **Then build an agent** — and the agent they build on is **V12**,
   forked from the shipped champion harness.
4. **The exercise is the deliberate gap in V12** (below). Everything in
   the guide funnels toward it.

### The deliberate gap — V12 buys weapons but never fires them

This is the hackathon's central exercise, and it is a **real, verified
property of the code**, not a contrivance:

- **V12's night planning never emits a weapon move.** The weapon action
  tokens (`emp_launch`, `chaff_flare`) appear nowhere in
  the `tabula_v12/` harness *except* `last_night.py`, which reads what
  was done **to** it. It can see it was EMP'd; it has no path to EMP
  back.
- **But its orbit phase buys them anyway.** `tabula_v12/orbit.py`'s
  `submit_orbit` delegates wholesale to the shared heuristic
  `plan_orbit_actions(agent_view)` — **with weapons enabled** — and
  then applies only a fleet-recovery gate. So the heuristic's weapons
  economy (blue over ~300 → always build a weapon) spends V12's blue
  and credits into a stockpile that sits untouched all season.
- **And its doctrine actively deprioritises blue** — the weapons
  currency. `value_pyramid.py` can grab blue (`_BLUE_GRAB_MIN`,
  `GRAB_BLUE`, the `BL*` option IDs) but `doctrine.py`'s
  `DOCTRINE_BLUE` says RED always outranks blue, take it only when the
  vault is low or a harvester would otherwise idle.

So V12 is strong at what it does and has a coherent, demonstrable hole:
it under-harvests the weapons currency, auto-spends it on a magazine,
and then never pulls the trigger. **Two halves for an attendee to
solve**, which is exactly the shape of the work:

- **An orbital purchasing heuristic** — buy the *right* weapon for the
  situation, instead of inheriting the shared playbook's generic
  blue-threshold rule.
- **A way to inject weapon plays and strategies into the agent** so it
  can *choose* to use them — new option-menu entries, doctrine text,
  and packager/validator support for the weapon moves.

That gap should be **stated openly** in the guide (Phase 6, "Meet V12")
rather than left as a puzzle: "here is the champion, here is precisely
what it can't do, go fix it." Do **not** quietly close this gap while
tidying the repo — Phase 4's orbit rework in particular must leave a
weapons decision for the attendee to make.

## Status

| Phase | What | Status |
| --- | --- | --- |
| 0 | Revert the RED_HARVEST_LITE experiment from the original dev repo (unrelated pre-existing WIP untouched); confirm green baseline | ✅ done |
| 1 | Port to this clean repo; BYO-Snowflake docs; re-apply RED_HARVEST_LITE fresh | ✅ done |
| 1.5 | Re-sync from the dev repo (engine/rulebook/manual/UI), bring V12 across, delete V11 | ✅ done (2026-08-25) — see audit below; **amended** later the same day to pick up dev `5bf8f63`, which landed 10s before the port commit and was missed (agent & harness manual page) |
| 2 | Trim the agent roster to exactly `RED_HARVEST` / `RED_HARVEST_LITE` / `V12` | ✅ done (2026-08-25) — legacy Cortex Agents-API path removed, all retired harnesses deleted, V12 flattened off `tabula_v7` and now self-contained |
| 3 | Easy install & first-run verification pass — **the north-star phase**; untangle the two Snowflake dependencies, preserve the multiplayer/tunnel suite. Treat it as a product goal, not a checkbox | 🟢 substantially done (2026-08-25, v1.12) — backend auto-detects, boot probe + actionable errors, `quickstart_check.py`, one-click Quick game, docs reconciled; verified from a fresh venv on base requirements. Remaining: a walk-through against a genuinely fresh **trial account** (all Snowflake checks so far were offline or against an existing account) |
| 4 | Simplify the Orbital phase down to purchasing + weapons buying (drop refine/catapult/jettison); re-teach all three agents the new orbit | ✅ done (2026-08-25, **v1.13**) — engine, view, both agents, UI, RULEBOOK §4 and the attendee docs all reconciled; 916 tests green. Catapult graphics **kept** and resized to the settlement manifest. Follow-up below: the orbit eval scenarios still name retired verbs |
| 4.5 | **Multiplayer audit** — the suite is a hackathon requirement and is partly broken; needs its own pass | ✅ done (2026-08-26, **v1.15**) — root cause found: `run_web.py` binds loopback, so every LAN invite QR was unreachable. `--lan` added, the server now self-tests the address it advertises and tells the two failure causes apart. 951 tests green. See "as built" below |
| 4.6 | **Per-game backend choice** — pick Memory or Snowflake in the New Game modal instead of once per process; Memory means fast, no persistence, no LLM agent | ✅ done (2026-08-26, **v1.14**) — routing, merged listing, modal dropdown, Quick game pinned to memory; frozen-constant trap fixed and pinned by a test. **The rationale changed during implementation — see "as built" below.** 937 tests green |
| 4.9 | **Transport, root cause** — why "Cloudflare is blocked here" was never true | ✅ done (2026-08-26, **v1.18**) — the DNS gate added in 4.8 was **causing** the failure it detected. A tunnel hostname doesn't exist until ~2.4s after the client prints it (measured: printed 5.6s, resolvable 8.0s); the gate queried immediately and retried every 0.8s, so its first lookups cached an `NXDOMAIN` under `trycloudflare.com`'s **1800s negative TTL** — killing the name on the host's own resolver for half an hour. Proven by A/B: two tunnels seconds apart, the hammered name still NXDOMAIN locally while `1.1.1.1` resolved it, the untouched one fine. Gate now waits 12s before its first lookup and the liveness watchdog is held behind the same gate (it resolves the same name). Cloudflare now wins in 17s, end to end, on the VPN. 36 tunnel tests. See "as built" below |
| 4.8 | **Transport, corrected** — a soak and a retest overturned both of 4.7's conclusions | ✅ done (2026-08-26, **v1.17**), **DNS half wrong — see 4.9** — anonymous `localhost.run` names rotate in **13 min** (old one 503s, killing every invite link), and the Cloudflare DNS block turned out **intermittent**, not structural: same laptop, VPN up, served 200 the next day. So the order flipped to `cloudflare` first and reachability became a **runtime DNS gate** instead of an assumption. Invite modal warns when a rotating provider is carrying the tunnel. 28 tunnel tests; 997 green. See "as built" below |
| 4.7 | **Multiplayer transport** — make "click MULTIPLAYER, share a link" work on a locked-down corporate laptop | ✅ done (2026-08-26, **v1.16**), **superseded by 4.8** — LAN is unavailable room-wide (firewall is Jamf-enforced) and corporate DNS sinkholes `*.trycloudflare.com`, so the tunnel became an ordered **provider list**: `localhost.run` (no install, no account) then `cloudflare`. Adds a liveness watchdog after finding a live process serving a dead URL. 20 new tests; 971 green. See "as built" below |
| 5 | Finish/polish the interactive manual (`manual/`) | pending |
| 6 | Hackathon Guide (onboarding, sibling to `manual/`) | 🟡 first cut landed (2026-08-25) — `guide/index.html`; revisit after Phases 3/4 change the install story and the orbit |
| 7 | In-game read-only agent advisor — invoke V12/RED_HARVEST mid-turn from the UI: reasoning card (readable + txt dump), magenta board overlay, adopt-into-your-policy | 🟢 substantially done (2026-08-26, **v1.19**) — **ASK V12**: `GET/POST /api/game/{id}/advisor` drives the harness with `submit=False` (its existing read-only path, so no memory is written and the game is untouched); night/planning turns only, gated on a Snowflake-backed game plus Cortex credentials; timer, cancel, and force-cancel on submit; plan/thinking tabs; a numbered violet board overlay on hover; adopt-into-your-policy. `tests/test_advisor_endpoint.py`. **Remaining:** RED_HARVEST can't be asked (V12 only), and there's no txt dump of the reasoning |
| 7.5 | **The agent iteration loop** — make the turn-replay suite something an attendee can actually use to improve a fork. Designed in `docs/AGENT_LOOP_PLAN.md` | ✅ done — shipped as the turn lab (`turnlab/`, v1.42), `scripts/soc.py`, `soc season` (v1.40) and the league (`soc cup`, v1.50). Row was stale; corrected 2026-09-19 |
| 8 | Drift-testing alarms + PR-based submission workflow doc; "add weapons to V12" as the flagship worked example | pending |

Sequencing: 1→1.5→2→3 is the critical path. 1.5 must land **before** 2:
the trim deletes things wholesale, and re-syncing from the dev repo
afterwards would drag the deleted agents straight back in. 4 is
intentionally unscheduled — its shape is agreed but the open questions
in that section need answering before any code. 5-8 build on top of
the Phase 1 repo and can be
reordered relative to each other.

(1.5 is numbered as a decimal deliberately, so the phase numbers
already referenced in conversation and throughout this doc stay
stable.)

## What Phase 0/1 actually did (context, not TODOs)

- Reverted a same-session experiment from the original dev repo (6
  files: `sea_of_colours/agent/heuristic_agent.py`,
  `sea_of_colours/agent/runtime.py`,
  `sea_of_colours/orchestrator_2/binding_registry.py`,
  `sea_of_colours/orchestrator_2/dispatcher.py`,
  `sea_of_colours/orchestrator_2/runtime.py`, `scripts/run_season.py`,
  `tests/test_agent.py`) back to the dev repo's own baseline —
  confirmed via `git stash` that the one remaining test failure
  (`tests/test_eval_scenarios.py::test_heuristic_passes_known_scenarios[two_seams_choose_one]`)
  is pre-existing on a clean `HEAD`, unrelated to anything done here.
- Copied a trimmed snapshot of the dev repo here via `rsync`
  (respecting current uncommitted WIP on `manual/manual.css`,
  `manual/manual.js`, and the `tabula_v11/` harness — those were
  carried over as-is, not just the last commit), excluding:
  `reports/`, `card*.txt`, `prompt_dump.txt`, `seasons*`,
  `_tmp_seasons_dbg/`, `audit_html/`, `batch_snowpark/`, `soc_eras/`,
  `build/`, `tmp_hallucination_audit.py`.
- Re-applied the `RED_HARVEST_LITE` variant fresh, directly in this
  repo: a `weapons_enabled: bool` flag threaded through
  `plan_orbit_actions()` / `plan_moves()` / `HeuristicAgent` in
  `sea_of_colours/agent/heuristic_agent.py`, wired through both the
  legacy `agent/runtime.py` (`runtime_override="red_harvest_lite"`)
  and `orchestrator_2`'s `binding_registry.py` /
  `dispatcher.py` / `runtime.py` (`HEURISTIC_LITE_BINDING`, locator
  `"RED_HARVEST_LITE"`), plus a `--p1/--p2/--p3/--p4 red_harvest_lite`
  CLI choice in `scripts/run_season.py`. 3 regression tests added to
  `tests/test_agent.py`.
- Found + fixed a real gap on a from-scratch install: `requirements.txt`
  was missing `httpx` (needed by `fastapi.testclient`) and `requests`
  (needed by `cortex_chat.py` / `cortex_invoker.py`). Both added.
- Added `requirements-snowflake.txt` (optional `snowflake-snowpark-python`,
  only needed for `scripts/deploy_soc_schema.py` / `SOC_BACKEND=snowflake`
  — NOT needed for V11, which talks to Cortex over plain REST + a PAT).
- Added `docs/SNOWFLAKE_SETUP.md` — from-scratch BYO-trial-account
  walkthrough, split into "PAT only, for V11" vs. "optional persistent
  sessions." Fixed a stale claim in `README.md` (said default backend
  was `memory`; code default is `snowflake` — `SOC_BACKEND=memory` is
  the opt-in offline path).
- Verified end to end: fresh venv + `pip install -r requirements.txt`
  → `SOC_BACKEND=memory pytest` → 539 passed, 1 pre-existing unrelated
  failure, 4 skipped → `uvicorn server.app:app` boots and serves the
  game homepage.
- Initial commit made (`git log --oneline` → "Initial commit: Sea of
  Colours hackathon repo"). Local-only — no remote configured, nothing
  pushed anywhere.

## Phase 1.5 — Re-sync from the dev repo

**Do not start this until the user says "go".** They are still making
changes in `/Users/lgalan/Desktop/sea_of_colours` (game, manual, rules
engine, agents). The Phase 1 snapshot is therefore already stale, and
will be staler still by the time this runs — everything below is a
*procedure* plus a dated example of what it turned up, not a fixed
worklist.

**Goal:** bring this repo back level with the dev repo's latest work,
without clobbering the things this repo has deliberately changed.

### The trap: what must NOT be overwritten

A naive `rsync` from the dev repo would silently undo Phase 1. These
files are *intentionally* different here and need merging, not
copying:

- **The RED_HARVEST_LITE re-application (7 files).** `weapons_enabled`
  threading in `sea_of_colours/agent/heuristic_agent.py`,
  `agent/runtime.py`, `orchestrator_2/binding_registry.py`,
  `orchestrator_2/dispatcher.py`, `orchestrator_2/runtime.py`,
  `scripts/run_season.py`, `tests/test_agent.py`. LITE exists **only
  here** — it was deliberately reverted from the dev repo in Phase 0.
- **Hackathon-only edits:** `requirements.txt` (the `httpx` /
  `requests` additions), `README.md` (the `SOC_BACKEND` default fix),
  `AGENTS.md`.
- **Hackathon-only files** the dev repo has never had:
  `requirements-snowflake.txt`, `docs/SNOWFLAKE_SETUP.md`, and this
  file. So: **never `rsync --delete`.**

For each of those, diff both sides and hand-merge the dev-repo change
on top of the local one, rather than taking either wholesale.

### Procedure

1. **Commit the dev repo first.** ✅ Done by the user — `main` is clean
   at `7abf550` (2 commits unpushed, which is fine; we sync from the
   working tree, not the remote).
2. **Re-take the divergence snapshot.** Dry run, checksum-based, no
   deletes:

   ```bash
   rsync -rn --checksum --out-format="%n" \
     --exclude='.git/' --exclude='__pycache__/' --exclude='.pytest_cache/' \
     --exclude='.venv/' --exclude='.DS_Store' --exclude='.claude/' \
     --exclude='reports/' --exclude='card*.txt' --exclude='prompt_dump.txt' \
     --exclude='seasons*' --exclude='_tmp_seasons_dbg/' --exclude='audit_html/' \
     --exclude='batch_snowpark/' --exclude='soc_eras/' --exclude='build/' \
     --exclude='tmp_hallucination_audit.py' \
     /Users/lgalan/Desktop/sea_of_colours/ \
     /Users/lgalan/Desktop/sea-of-colours-hackathon/
   ```

   Note the two exclusions added since Phase 1: `.DS_Store` (the dev
   repo has several; they'd land as new untracked files here) and
   `.claude/` (local tooling settings, gitignored here anyway).
3. **Triage every path the dry run lists** into: straight copy /
   hand-merge (the do-not-clobber list above) / skip (retired-agent
   debris that Phase 2 is about to delete anyway — no point porting it
   just to remove it next phase).
4. **Copy, then diff-review before committing.** `git diff` here is
   the real check that nothing on the do-not-clobber list regressed.
5. **Verify:** `SOC_BACKEND=memory pytest` against the 539-passed /
   1-pre-existing-failure baseline, plus the 3 LITE regression tests in
   `tests/test_agent.py` still passing — those are the canary for a
   botched merge. New dev-repo tests coming across (e.g.
   `orchestrator_2/tests/test_tabula_v11_supersede.py`) will move the
   pass count up; account for that rather than treating it as drift.
6. **Commit** (the user asked for this phase to end in a commit) and
   update the status table above.

### What the 2026-08-25 execution actually did

- Copied **69 files**: engine (`game/session.py`, `game/simulator.py`,
  `snowpark/engine.py`, new `snowpark/snapshot.py`), `RULEBOOK.md`,
  the whole `manual/`, `server/static/app.js` + `index.html`, the
  `tabula_v12/` harness, 19 new v12 tests, `advise_v12.py` /
  `run_matchup_v12.py`, the turn-replay suite, and
  `tabula_v7/move_sanitizer.py`.
- **Hand-merged 3 files**, all non-overlapping so each was a clean
  hunk application: `binding_registry.py` (kept `HEURISTIC_LITE_BINDING`
  + `red_harvest_lite`, took the V12 entry), `orchestrator_2/runtime.py`
  (kept the LITE `weapons_enabled` branch, took the `soc_snapshot`
  import + `maybe_take()` call), `AGENTS.md`.
- **Skipped 12 clutter scripts** (the `_audit_*` / `_scan_v12_*`
  diagnostics).
- **Deleted V11**: `harnesses/tabula_v11/`, `tabula_v11_PLAN.md`, 18
  `test_tabula_v11_*.py`, the binding + label, and
  `run_solo_v11.py` / `run_matchup_v11.py` / `run_season_v11_capture.py`
  / `advise_v11.py`. Verified first that **v12 only mentions v11 in
  inherited docstrings — no imports** (those stale docstrings are
  cosmetic debt for Phase 2; several v12 modules still open with
  `"""tabula_v11 — …"""`).
- Repointed `run_matchup_v12.py`'s `v11` benchmark mode to a `lite`
  mode against `red_harvest_lite`.
- **Verified:** `tests/` → 723 passed / 4 skipped / 1 failure (the
  known pre-existing `two_seams_choose_one`, unchanged).
  `sea_of_colours/orchestrator_2/tests/` → **737 passed**, including
  all five new v12 harness tests.

Still to do from this phase: the **`tabula_v7` flatten** (below), and
`docs/SNOWFLAKE_SETUP.md` still describes V11 throughout.

### Audit, 2026-08-25 (the real one — this is the sync being executed)

Dev repo `main` is **clean and fully committed** at `7abf550`, three
commits past the port base `b916fbc`:

```
7abf550 tabula_v12: document the harness + engine boundary; seam and pricing fixes (OBS-42..59)
0f7f1ff turn_suite: archive the pre-Phase-A card set so changes can be judged card-vs-card
3bd15fd tabula_v12: fork with world view + out-of-grid, turn-replay suite, OBS-27..41
```

Because the base commit is known, every diverging path was classified
**three ways** (base vs dev vs here) rather than guessed — extract the
base tree with `git archive b916fbc | tar -x -C .tmp_base` and compare.
97 paths diverge:

**TAKE-DEV — clean, no merge needed (12).** The hackathon repo never
touched these, so dev wins outright: `RULEBOOK.md`,
`game/session.py`, `game/simulator.py`, `snowpark/engine.py`,
`server/static/app.js`, `server/static/index.html`,
`manual/index.html`, `manual/manual.css`, `manual/manual.js`,
`tests/conftest.py`, `tests/test_v09_weapons.py`,
`tests/test_v0_7_0.py`, plus `tabula_v7/move_sanitizer.py`.
*(The manual files look like conflicts to a two-way diff only because
the Phase 1 port captured mid-edit WIP; `git log -- manual/` here shows
one commit, the import. Dev is strictly ahead.)*

**NEW-IN-DEV — additive (62).** The whole `tabula_v12/` harness (24
files incl. `README.md` + `ENGINE_INTERFACE.md`); 19 new tests (14
`tests/test_v12_*.py`, 5 `orchestrator_2/tests/test_tabula_v12_*.py`);
`snowpark/snapshot.py`; `scripts/advise_v12.py`,
`run_matchup_v12.py`, `__init__.py`; the turn-replay suite
(`turn_suite.py`, `turn_library.py`, `replay_turn.py`,
`replay_card.py`, `dump_suite_cards.py`, `scenario_facts.py`);
`manual/manual-changes.txt`.

**CONFLICT — genuine hand-merge (3).**
`orchestrator_2/binding_registry.py` and `orchestrator_2/runtime.py`
(this side carries `RED_HARVEST_LITE`, dev adds the V12 wiring — keep
both), and `AGENTS.md`.

**KEEP-HACK — dev unchanged since base, don't touch (9).**
`README.md`, `requirements.txt`, `heuristic_agent.py`,
`agent/runtime.py`, `dispatcher.py`, `scripts/run_season.py`,
`tests/test_agent.py`, and the three `tabula_v11/` files + its test
(moot — v11 is being deleted).

**SKIP as clutter (11).** One-off diagnostics tied to work already
done: `scripts/_audit_canonical_fog.py`, `_audit_drop_legal.py`,
`_audit_fix_reach.py`, `_audit_redsign_contest.py`,
`_audit_v12_boardpass.py`, `_scan_v12_consumption.py`,
`_scan_v12_tiererr.py`, `_scan_v12_worldview.py`, `_render_oog.py`,
`_inspect_day.py`, `own_redsign_report.py`, `reconstruct_prompt.py`.

The multiplayer/tunnel files (`server/tunnel.py`, `server/app.py`,
`docs/MULTIPLAYER.md`, `mobile.html`, `landing.js`) show **no
divergence** — nothing to merge, but they stay on the keep-list.

### ⚠️ V12 is not a self-contained fork — it depends on `tabula_v7`

The audit's most consequential finding, and it invalidates Phase 2 as
originally written. `tabula_v12/` makes **26 imports from
`tabula_v7`** across 16 of its 24 modules:

```
probe_hints  validators  rules  strategies  prompt
orbit_wishlist  chat_schema  heuristic_chains
orbit_stub (fallback)  move_sanitizer
```

`tabula_v7` imports only from itself, so the closure stops there — but
**V12 + V7 is the minimum shipping set**, and `tabula_v7/` cannot simply
be deleted.

**Recommended fix: flatten V7's eight needed modules into
`tabula_v12/`,** then delete `tabula_v7/`. Reasons this is worth doing
properly rather than just keeping v7 around:

- An attendee forking V12 should get **one self-contained directory**.
  Discovering that the champion agent imports half its brain from a
  harness the guide calls "retired" is exactly the confusion Phase 2
  exists to remove.
- The weapons exercise specifically requires editing `probe_hints`,
  `validators` and `packager` — so the modules they must change would
  otherwise live *outside* the folder they forked.

Do the flatten as its own reviewable step with tests green before and
after, not mixed into the copy.

### `tabula_v12` — resolved: it comes over and replaces V11

**Decided.** V12 is the harness the hackathon is built around (see
"North star" at the top), so `harnesses/tabula_v12/` is a **required**
part of this sync, not an optional extra, and it *supersedes* V11 as
the shipped LLM agent. Phase 2's keep-list is updated to match.

Consequences for the sync itself:

- `sea_of_colours/orchestrator_2/binding_registry.py` is now the
  trickiest file in the whole port. It is **simultaneously** on the
  do-not-clobber list (it carries the `RED_HARVEST_LITE` binding, which
  exists only here) and a must-take (the dev repo's uncommitted edit is
  what adds `KNOWN_AGENT_BINDINGS["SOC_RED_REAPER_TABULA_V12"]` and the
  `"tabula_v12"` label, pointing at
  `tabula_v12.harness:run` / `agent_label="TABULA_V12"`). Hand-merge
  it: keep LITE, take V12. Do not copy it wholesale in either
  direction.
- Bring the v12 tooling across too — `scripts/advise_v12.py` and
  `scripts/run_matchup_v12.py` — since Phase 7's advisor is modelled on
  the `advise_*` pattern and will target V12. The `_scan_v12_*.py` /
  `_render_oog.py` diagnostics are scratch; leave them unless the user
  wants them.

**V11 is deleted** (decided). The roster is exactly `RED_HARVEST` /
`RED_HARVEST_LITE` / `V12`; `tabula_v11/` goes in Phase 2 alongside
v2–v10. Two caveats that make this non-trivial:

- `tabula_v12/` is a fork of v11 but is **not** fully self-contained —
  it imports `tabula_v7.orbit_stub` on its empty-orbit and
  exception-fallback paths. Grep every surviving harness for
  cross-harness imports before deleting anything (see Phase 2).
- Still bring `test_tabula_v11_supersede.py` across in this sync and
  re-point it at v12 if the behaviour it covers survives the fork —
  deleting v11 shouldn't silently delete its regression coverage of
  logic v12 inherited.

Still open at "go":

- **How much dev-repo WIP gets committed there** before the port
  (step 1) — the v12 harness is currently untracked in the dev repo, so
  at minimum it needs committing for there to be anything stable to
  port.

### Amendment — dev `5bf8f63` was missed by ten seconds

The port snapshot was taken at 12:22:00; dev committed `5bf8f63`
("manual: agent & harness guide — one real turn taken apart") at
12:21:50. The audit was therefore correct about everything it saw and
simply never saw that commit. Nothing else was stranded — re-verified
by diffing the two trees afterwards:

- `sea_of_colours/game/` is **byte-identical** to dev (14 files, same
  combined hash).
- `manual/manual.js` is dev's content plus only the `#tab=` deep-link
  addition made here.
- Every other difference is deliberate hackathon-side work
  (`naming.py`, auto-detect `backend.py`, `weapons_enabled` in
  `heuristic_agent.py`, the Cortex removal in `runtime.py` /
  `cortex_invoker.py` / `agent/__init__.py`).

Ported afterwards: `manual/agent.{html,js,css}`, `manual/agent-data.js`,
`scripts/export_agent_guide_data.py`, and the `[ AGENT & HARNESS → ]`
nav link in `manual/index.html`. Reconciliation needed was small — the
page was written post-V12-fork, so it names no retired harness or the
Cortex Agents-API; only `tabula_v7/move_sanitizer.py` needed re-pointing
at `tabula_v12/_v7/` after the flatten (in `agent.js` and an `agent.css`
comment).

**Lesson for any future re-sync:** pin the dev SHA you are porting from
and record it, rather than rsyncing a live tree. A commit landing
mid-port is invisible otherwise.

## Phase 2 — Reduce the agent roster to exactly three

**Goal:** only `RED_HARVEST`, `RED_HARVEST_LITE`, and `V12` should exist
as agents a participant can select or read about. Everything else is
pre-hackathon R&D debris that will only confuse someone trying to
understand "how do I build an agent."

### ✅ Step 1 done (2026-08-25) — the legacy Cortex Agents-API path is gone

The first tranche of the trim: an entire *generation* of agent
infrastructure, retired because V12 doesn't use any of it. V12 talks to
Cortex **inference** over REST with a PAT; the old agents were Snowflake
*objects* deployed from SQL specs and invoked through a different API.

Removed:

- **27 `soc_create_agent*.sql` specs** (both `snowflake/` and
  `orchestrator_2/snowflake/`).
- **`agent/runtime.py`: 1275 → 253 lines.** Deleted `AI_AGENTS`,
  `CORTEX_AGENT_DEFAULT`, `RULES_IN_SPEC_AGENTS`, `_runtime_mode`,
  `_cortex_agent_name`, `_build_cortex_prompt` (+ `_slim`),
  `_annotate_cortex_diagnostics`, the world primers, and the whole
  Cortex branch of `run_agent_turn` including its submission-race
  re-poll logic. The module is now heuristic-only.
- **`scripts/smoke_cortex.py`, `scripts/pilot_turn.py`** — both drove
  the removed path.
- `run_season.py`'s `cortex` seat choice, `--cortex-agent` flag, and
  Snowflake preflight.
- `deploy_soc_schema.py`'s agent-spec deployment (`--no-agent`,
  `--agents-only`, `--agent-name`) — it was globbing for files that no
  longer exist.

Behaviour now, deliberately loud rather than silent:

- `POST /agent/think?runtime=cortex` → **410** with a message pointing
  at `tabula_v12`.
- `run_agent_turn(..., runtime_override="cortex")` → **`ValueError`**.
  Silently demoting an LLM seat to the heuristic is the exact
  misattribution that hid past regressions, so it must not be possible.
- `?runtime=` accepts only `heuristic` / `red_harvest_lite`.

⚠️ **`agent/cortex_invoker.py` was NOT deleted — and must not be.**
The plan assumed it was dead. It isn't: `orchestrator_2/cortex_invoker.py`
subclasses it and `cortex_chat.py` imports `_load_sf_props` /
`SNOWFLAKE_PAT_ENV` from it, so **V12 depends on it transitively**. It
survives as the shared SSE/PAT transport. Its Agents-API *wiring* is
what went away, not the HTTP plumbing.

Two bugs found and fixed while doing this:

1. `app.js::selectedVersusMatchup()` defaulted p1 to `"cortex"` whenever
   its dropdown was missing — and that dropdown doesn't exist in
   `index.html` at all, so the VERSUS flow would have sent the retired
   runtime on **every** call. Now defaults to RED_HARVEST vs
   RED_HARVEST_LITE.
2. `pytest.ini` had `testpaths = tests`, so a bare `pytest` skipped the
   737 `orchestrator_2` tests — **including V12's**. Fixed; the default
   run is now 1449 tests, up from 713.

Tests: 14 cortex-runtime tests removed, 3 added pinning the new refusals.
The 4 `CortexAgentInvoker` SSE tests were **kept** (still live code).
Green at the known baseline (the one pre-existing
`two_seams_choose_one` eval failure).

### ✅ Step 2 done (2026-08-25) — roster trimmed to one harness, V12 flattened

`harnesses/` now contains exactly **`tabula_v12/`** (plus the versioning
guide and the v12 plan doc). Deleted: `pilot_v2`, `pilot_v3`,
`pilot_v4`, `tabula`, `tabula_v2`–`v6`, `v8`, `v9`, `v10`,
`CHANGES_TABULA_v6_v7.md`, and their ~27 test modules.

**V12 is now self-contained** — verified by an AST walk: it imports
nothing from any other harness package. `tabula_v7/` moved wholesale to
**`tabula_v12/_v7/`** (16 modules) with the import prefix rewritten
across 37 Python files. `_v7/__init__.py` explains what it is so the
directory isn't a mystery to someone forking the agent.

`binding_registry.py` is down to one entry in each map:
`KNOWN_AGENT_BINDINGS = {SOC_RED_REAPER_TABULA_V12}` and
`AGENT_LABEL_BINDINGS = {tabula_v12, red_harvest_lite}`. A regression
test pins that retired labels no longer resolve to a harness.

Scripts: deleted the 15 retired-agent diagnostics the sweep list names.
Then verified **all 30 remaining scripts still import**, which caught a
mistake worth recording — `replay_card.py` was on the delete list but
is part of the **Phase 7.5 turn-replay suite** and `turn_library.py`
imports it. It was untracked (arrived in the 1.5 sync, never
committed), so `git checkout` could not bring it back; recovered from
the dev repo at `~/Desktop/sea_of_colours`. `_season_naming.py` was
likewise still needed by `rename_seasons.py`. Both restored.

> ⚠️ Lesson for the remaining trim phases: the sweep list in this doc
> was assembled by grepping for retired agent *names*, so it catches
> files that merely mention them — including ones another keeper
> imports. Run an import check over what's left after any bulk delete,
> and commit before deleting untracked files.

Green at the known baseline (892 passed, the one pre-existing
`two_seams_choose_one` failure). V12 verified still playing a seat
end-to-end after the move.

### ✅ V12 menu wiring verified (2026-08-25) — plus a silent-failure fix

Traced end-to-end and confirmed **not** a dangling UI option:

```
NEW GAME modal (agents.p2 = "tabula_v12")
  → POST /api/game/new            server/app.py
  → GameSession.agents            persisted in the session blob
  → _game_has_slow_bot → _kick_bots → _drive_bots   (background thread)
  → orchestrator_2.runtime.run_agent_turn(agent_label="tabula_v12")
  → AGENT_LABEL_BINDINGS["tabula_v12"] → tabula_v12.harness:run
```

Verified live: a `red_harvest_lite` vs `tabula_v12` game advances days
with `status.bot_turn = {seat: p2, agent: tabula_v12}`. Note V12 is
driven **server-side** by that worker and is deliberately *not*
reachable from `/agent/think`, which is heuristic-only.

**The silent failure.** A V12 seat with no Snowflake credentials does
not error. The harness's per-turn fallback absorbs the miss, so the
seat passes every night with **zero moves** while reporting `ok=True`,
`agent_id="TABULA_V12"`, `error=None`. The only trace is
`[fallback=True:parse: no_json_object]` buried in the rationale. An
attendee would watch "V12" sit motionless and conclude the agent — or
the game — is broken.

Fixed with a **creation-time preflight**
(`cortex_chat.credentials_status()` → `app._preflight_llm_credentials`):
seating an LLM agent with no PAT/account now returns **400** naming the
seat, the missing credential, the file to put it in, and the
"pick RED_HARVEST_LITE to play right now" escape hatch. The
frontend was also dropping the server's `detail` on the floor and
rendering a bare `! HTTP 400`; it now surfaces the message.

The distinction matters and is deliberate: the **per-turn** fallback is
correct for a call that fails mid-game and must stay. What's refused is
only the seat that could never have worked at all.

Also fixed: `pytest.ini` collected only `tests/`, silently skipping the
737 `orchestrator_2` tests — including V12's. A bare `pytest` now runs
1452 (was 713).

⚠️ **Known drift, not yet fixed:** `/api/game/new` does **no
whitelisting** of agent labels. An unrecognised label is stored, treated
as a slow bot, then resolves to RED_HARVEST in the binding registry — a
silent downgrade of the same family as the one above.
`AGENT_LABEL_BINDINGS` also still exposes the legacy harnesses
(`pilot_v2`, `tabula_v7`, …) to API/CLI callers even though the modal
only offers four. Both resolve naturally when the retired harnesses are
deleted; worth a whitelist regardless.

**Keep:**
- `RED_HARVEST` — `sea_of_colours/agent/heuristic_agent.py`, weapons on.
  The step-up opponent, and also the source of the shared
  `plan_orbit_actions()` playbook that V12 delegates its orbit to.
- `RED_HARVEST_LITE` — same file, `weapons_enabled=False` (done in
  Phase 1). The **first** opponent a new player meets, per the north
  star.
- `V12` — `sea_of_colours/orchestrator_2/harnesses/tabula_v12/`, the
  only LLM harness and the thing attendees fork. Binding:
  `KNOWN_AGENT_BINDINGS["SOC_RED_REAPER_TABULA_V12"]` in
  `binding_registry.py`, `kind="harness_in_process"`, locator
  `tabula_v12.harness:run`, calls out via
  `orchestrator_2/cortex_chat.py` (`CortexChatInvoker`, the inference
  chat/completions API — not the older Agents API). Arrives in
  Phase 1.5.

⚠️ **`tabula_v7/` cannot be deleted until V12 is flattened.** The
Phase 1.5 audit found **26 imports from `tabula_v7` across 16 of V12's
24 modules** — not just the `orbit_stub` fallback. Flatten the eight
needed v7 modules into `tabula_v12/` first (see the Phase 1.5 section),
then delete `tabula_v7/`. Deleting it naively breaks the shipped agent.

**V11 is deleted too** (decided — see Phase 1.5). Add `tabula_v11/` to
the removal list below and drop `_TABULA_V11` / `tabula_v11` from
`binding_registry.py`. Exactly three agents ship.

**Do NOT remove while trimming:** this phase is about the *agent
roster* only. The multiplayer/tunnel suite (`server/tunnel.py`,
`docs/MULTIPLAYER.md`, `server/static/mobile.html`, the QR vendor lib,
`p2_join_qr.gif`) is a keeper — see the Phase 3 section. Same for the
manual. When in doubt about whether something is R&D debris, check it
isn't on a Phase 3/5/6 keep-list first.

**Remove — harness directories** (`sea_of_colours/orchestrator_2/harnesses/`):
```
pilot_v2/  pilot_v3/  pilot_v4/
tabula/    tabula_v2/ tabula_v3/ tabula_v4/ tabula_v5/ tabula_v6/
tabula_v7/ tabula_v8/ tabula_v9/ tabula_v10/ tabula_v11/
tabula_v11_PLAN.md   tabula_v12_PLAN.md   CHANGES_TABULA_v6_v7.md
```
⚠️ **Correction (2026-08-25) — `tabula_v7/` is not a one-module
dependency.** This list used to say "V12 imports its `orbit_stub`,
inline what's needed or keep that one module." That is wrong, and
acting on it would have broken the shipped agent. Measured with an AST
walk over the import graph: V12 imports **all 15 v7 modules** (~287 KB),
`tabula_v7.harness` itself among them —

```
chat_schema directive harness heuristic_chains memory move_sanitizer
opponent_weapons orbit_stub orbit_wishlist probe_hints prompt recorder
rules strategies validators
```

A **flat** merge into `tabula_v12/` is also off the table: 4 module
names collide (`chat_schema`, `harness`, `prompt`, `rules`) and in each
case v12's version *imports* v7's, so merging them would be a rewrite,
not a move.

So the flatten is: move the whole package to a **subpackage**
`tabula_v12/_v7/` and rewrite the import prefix. Order matters —
`tabula_v8/`, `_v9/`, `_v10/` also import v7, so they must be deleted
**before** v7 moves, or their imports break mid-flight.
(`tabula_v11_PLAN.md` / `tabula_v12_PLAN.md` are planning docs, not
harnesses. `tabula_v12_PLAN.md` describes the agent that is now
actually shipping, so it's worth mining for the "Meet V12" tab
(Phase 6) before deleting — or folding into
`PILOT_VERSIONING_GUIDE.md`'s "minting a new agent" section.)

**Remove — `binding_registry.py`:**
- `KNOWN_AGENT_BINDINGS` entries: `SOC_RED_REAPER_PILOT_V2`,
  `_PILOT_V3`, `_PILOT_V4`, `_TABULA`, `_TABULA_V2` .. `_TABULA_V10`
  `_TABULA_V11` (keep only `_TABULA_V12`).
- `AGENT_LABEL_BINDINGS` entries: `pilot_v2`, `pilot_v3`, `pilot_v4`,
  `pilot_v6_arena`, `tabula`, `tabula_v2` .. `tabula_v10` (keep
  `tabula_v12`, `red_harvest_lite`).

**Remove/trim — `sea_of_colours/agent/runtime.py`:**
- `AI_AGENTS` dict currently has 3 entries: `SOC_RED_REAPER` (base
  Cortex Agents-API agent), `SOC_RED_REAPER_GRID_FAST` (referenced by
  `.env.canonical`'s `SOC_CORTEX_AGENT=` default — **fix that file
  too** once this entry is gone), `SOC_RED_REAPER_PILOT` (rules-in-spec
  variant). None of these are `V12` (V12 doesn't go through this dict
  at all — it's `orchestrator_2`-only). Decide with the user whether
  to drop `AI_AGENTS` + the whole legacy Cortex-Agents-API path
  (`cortex_invoker.py`, `SOC_AGENT_RUNTIME=cortex`) entirely, since
  V12 supersedes it, or keep `SOC_RED_REAPER` as a documented
  legacy/back-compat option. Whichever way: no dangling references to
  `GRID_FAST` or `PILOT` should remain if removed.

**Remove — SQL agent specs** (safe to delete once the bindings above
are gone; nothing will reference them):
- `snowflake/soc_create_agent_grid.sql`, `_grid_fast.sql`, `_grid_v2.sql`,
  `_list.sql`, `_list_v2.sql`, `_pilot.sql`. (`snowflake/soc_create_agent.sql`
  is the `SOC_RED_REAPER` base spec — tie its fate to the `AI_AGENTS`
  decision above.)
- `sea_of_colours/orchestrator_2/snowflake/soc_create_agent_pilot_v2.sql`,
  `_pilot_v3.sql`, `_scratch.sql`, `_strategic.sql`, `_strategist_v4.sql`,
  `_tabula.sql`, `_tabula_v2.sql` .. `_tabula_v7*.sql` (incl.
  `_finisher`/`_thinker` variants), `_tactical.sql`, `_tactician_v4.sql`.
  (`orchestrator_v2_schema.sql` is infra, not an agent spec — keep it.)
  **No `tabula_v11`/`v12` SQL spec exists** — confirmed intentional,
  both are `harness_in_process` and never register a Snowflake Agent
  object.

**Rewrite:** `PILOT_VERSIONING_GUIDE.md`, around minting a new agent
**from V12** rather than from a `pilot_v2` that no longer exists.

✅ **Done differently (v1.12).** The guide was deleted rather than
rewritten, along with `MIGRATION.md`. Both were version-history
narratives about harnesses that don't exist; a rewrite would have been
a new document wearing an old name. Replaced by two purpose-built docs
plus a tool:
- `orchestrator_2/README.md` — the plug-in contract and how to register.
- `harnesses/tabula_v12/README.md` — the fork guide: the turn pipeline,
  what a card is, where plays come from, and both deliberate gaps with
  exact file/constant references.
- `scripts/new_agent.py` — forks V12 to `<team>_<agent>`, renames its
  identity so audit rows attribute correctly, and registers the binding.

Registration is now one edit, not two: the New Game dropdown is built
from `/api/meta/agents` (served off `binding_registry.selectable_agents`),
so a registered fork is selectable without a frontend change.

**Sweep for stragglers** (found so far via `grep -rl` for
`pilot_v[234]|GRID_FAST|SOC_RED_REAPER_PILOT|SOC_RED_REAPER_GRID|tabula_v([1-9]|10)\b`,
excluding `reports/` per the repo-wide convention of not editing
historical AI transcripts):
- Docs: `docs/AGENT_ARCHITECTURE.md`, `docs/OUTSTANDING_ISSUES.md`,
  `docs/RULES_PENDING_REVISION.md` — read each and decide keep
  (historical/still-accurate) vs. rewrite vs. delete-the-section.
- Scripts (mostly one-off diagnostics tied to retired agents — most of
  these are probably fine to delete outright rather than edit):
  `scripts/_season_naming.py`, `diag_v9_vs_heuristic.py`,
  `exp_contained_think.py`, `pilot_turn.py`, `pilot_v4_probe.py`,
  `reconstruct_prompt.py`, `regress_context.py`,
  `replay_thinker_prompt.py`, `run_per_seat.py`, `run_season_v2.py`,
  `run_solo_v5.py`, `run_v5_vs_v4.py`, `run_versus.py`, `scan_thinker.py`,
  `tabula_hallucination_audit.py`, `tabula_v2_multinight.py`. Two
  **do** need to stay functional and just get their retired-agent
  references cleaned instead of deleted: `scripts/run_matchup_v11.py`
  (or its `_v12` successor from Phase 1.5 — keep one, not both) and
  `scripts/run_season.py` (both are live, general-purpose runners that
  happen to mention old agent names in comments/choices). Same for
  `scripts/advise_v11.py` vs `advise_v12.py`: Phase 7 builds on the
  advisor pattern and Phase 6 tab 9 points attendees at it, so exactly
  one should survive and it should target V12.
- `docs/SNOWFLAKE_SETUP.md` — written in Phase 1 around "a PAT, for
  V11". Every user-facing mention needs to become V12, since that's the
  only reason an attendee needs Snowflake at all.
- Tests: `tests/test_player_profiles.py`, `tests/test_world_grid.py` —
  check whether they assert on retired agent names/tags specifically,
  or just happen to match the grep pattern incidentally (e.g. a
  fixture named similarly) before touching anything.
- `.env.canonical` — currently sets
  `SOC_CORTEX_AGENT=SOC_RED_REAPER_GRID_FAST`; needs a new default (or
  removal) once the `AI_AGENTS` decision above is made. V12 doesn't
  use `SOC_CORTEX_AGENT` at all (that env var is legacy-cortex-only).

**After the trim:** re-run `SOC_BACKEND=memory pytest` — expect the same
539 passed / 1 pre-existing-unrelated-failure baseline as Phase 1 (the
pre-existing failure is `test_heuristic_passes_known_scenarios[two_seams_choose_one]`,
confirmed unrelated to any agent-roster changes). If new failures show
up, they're from this phase's trim — find and fix rather than skip.

## Phase 3 — Easy install & first-run experience

**This is the north-star phase** (step 1 at the top of this doc). Judge
it against a stopwatch and a stranger, not against a checklist.

### Untangle the two Snowflake dependencies (do this first)

Most of the confusion in this area comes from one word doing two jobs.
There are **two independent** Snowflake dependencies, and the docs,
errors and UI should never conflate them:

| | What it's for | Needs | Required to play? |
| --- | --- | --- | --- |
| **Cortex + PAT** | V12 *thinks* — one REST call to the inference API | `SNOWFLAKE_PAT` + `account` in `sf_config` | Only to face/fork V12 |
| **`SOC_BACKEND=snowflake`** | Game sessions *persist* to `UMAN_SIM_DB.SEA_OF_COLOURS` | `snowflake-snowpark-python`, key-pair auth in `~/.ssh/sf_config`, `scripts/deploy_soc_schema.py` | No — memory/file work fine |

They use **different credentials** (a PAT vs a key-pair) and neither
implies the other. `docs/SNOWFLAKE_SETUP.md` already gets this right
("These are independent") — the code's defaults don't.

**Decision: auto-detect the store backend.** Resolve `snowflake` when
the config and deps are actually present, fall back to memory
otherwise, and keep `SOC_BACKEND` as an explicit override that still
wins when set. This gets the attendee to their own account — the
tutorial destination — without letting an incomplete setup block the
first move.

### The work

- ✅ (DONE, v1.12) **Fix the broken default.** `SOC_BACKEND` defaulted
  to `snowflake` while `snowflake-snowpark-python` lived only in the
  *optional* `requirements-snowflake.txt`, so a by-the-book
  `pip install -r requirements.txt && python run_web.py` booted a
  server, rendered the homepage, and died with
  `ModuleNotFoundError: snowflake.snowpark` on the first game action —
  an install that looks fine right up until it doesn't. Unset now means
  **auto**: `snowflake` only when the Snowpark extras *and* key-pair
  auth are both present, else `memory`.
  - **Explicit is strict, auto is forgiving.** `SOC_BACKEND=snowflake`
    exits rather than silently degrading, so a season never lands
    somewhere the operator didn't intend; only `auto` falls back.
  - **A PAT-only `sf_config` does not select Snowflake.** That's the
    docs §1 / V12 setup and says nothing about wanting persistence.
    Keying detection on `private_key_file` keeps "I want to play
    against V12" from turning into "I'm writing to your account".
  - ⚠️ **Auto never resolves to a live backend under pytest.** Detection
    keys off a config file present on any machine that has done the
    key-pair setup, and `init_session` *wipes the target schema* — and
    `tests/conftest.py` pins nothing (individual modules `setdefault`
    ad hoc). Without the guard, a dev running the suite writes to their
    own account. This is the same class of accident that destroyed dev
    data during Phase 1.5. Pinned by
    `tests/test_backend_autodetect.py`.
  - Snowpark stays *out* of base `requirements.txt`: auto-detect
    removes the reason to add it, and it's a heavy dep for the majority
    who never persist.
- ✅ (DONE, v1.12) **Resolve the backend at startup, not lazily.**
  `probe_store()` opens the store in the boot banner, so a bad key or
  an undeployed schema is reported next to the command that started the
  server instead of as a 500 on whichever API call came first. Boot
  prints the resolved backend, the reason, and the fix.
- ✅ (DONE, v1.12) **Show it in the UI.** `/api/meta/backend` returns
  `{backend, requested, reason, fix, persists, summary}`;
  `/api/meta/status` gained `persists` + `reason`. The landing badge
  names the real backend (`memory`, not the old ambiguous `local`) and
  carries the reason as its tooltip.
- ✅ (DONE, v1.12) **Actionable messages for every half-configured
  state**, via `_fix_for()`: missing config → the keys to add; schema
  absent → `python scripts/deploy_soc_schema.py`; bad key-pair / JWT →
  check `user=` and the registered public key; warehouse trouble →
  distinguished from "not set up yet", since the fixes differ. The
  "for persistence:" hint is deliberately *not* labelled "fix" on the
  auto→memory path — landing on memory is the supported outcome, and
  calling it a fix tells a first-timer their working install is broken.
- ✅ (DONE, v1.12) **`scripts/quickstart_check.py`** — it didn't exist;
  now it does. Python version, virtualenv, base deps, engine imports,
  resolved backend, agent roster, Cortex credentials, Snowpark
  readiness, `cloudflared`, and `pytest` behind `--tests`. One
  pass/fail line each with the fix beside any failure, so the output is
  self-contained.
  - **Only the base install can fail the run.** Snowflake, Cortex and
    `cloudflared` report `skip`, not `fail` — a green run means nothing
    is broken, not that you configured Snowflake. Reporting optional
    capability as failure trains people to ignore the output.
  - **Offline by default** (`--network` opts in), because opening a
    Snowpark session costs seconds and resumes a warehouse.
  - The `--tests` line reports `warn`, not `fail`, on the known
    `two_seams_choose_one` failure — same reasoning as the guide's
    pytest checkpoint.
- ✅ (DONE, v1.12) **Fix the docs' story.** Every surface that told
  people to prefix `SOC_BACKEND=memory` now just says `python
  run_web.py`: `README.md` (quickstart + backend table + hosting note),
  `docs/SNOWFLAKE_SETUP.md` (TL;DR + four rewritten troubleshooting
  rows), `AGENTS.md`, `docs/MULTIPLAYER.md`, `RULEBOOK.md` §5.3,
  `guide/index.html` (02 First run, 05 Snowflake, 07 Multiplayer,
  troubleshooting). There is no `.env.canonical` in this repo.
  `scripts/run_season.py` no longer forces `snowflake` when unset —
  that made the runner unusable offline; it auto-detects and warns that
  a memory season is invisible to a separate watcher. Its `--backend`
  help also had a dangling sentence fragment, now rewritten.
- **Verify end-to-end against a genuinely fresh trial account and a
  fresh clone**, walking the doc literally as a first-time reader
  would — not from this already-configured machine. Re-verify after
  Phase 2's edits land, since deletions are easy to over-reach.
- ✅ (DONE, v1.12) **One-click quick game.** A `Quick game` button on the
  landing page goes to `/play?new=quick`, which spawns you vs
  `RED_HARVEST_LITE` on the standard map and lands on the board — no
  launcher. Every field in that modal has a right answer for a
  first-timer and no way to know it, so those four decisions were pure
  stall. `Play` still opens the launcher for anyone who wants to change
  something. Reuses the existing `?new=multi` deep-link convention
  rather than adding a second mechanism.
- Keep `SOC_BACKEND=memory` working and tested — it's what `pytest`
  uses and the offline fallback.
- ✅ (DONE, Phase 2) **`pytest.ini` collected only `tests`**, so the
  ~737 orchestrator tests — including every test covering V12, the
  agent attendees fork — never ran on a bare `pytest`, and someone who
  broke their fork got a green suite. `testpaths` is now
  `tests sea_of_colours/orchestrator_2/tests`. The feared cost didn't
  materialise: both trees together run in ~6s, so no marker was needed.

### Isolate the deployment under its own database (decided)

So the hackathon build can be installed and torn down from scratch on a
machine that already runs the dev repo, **without touching the existing
`UMAN_SIM_DB.SEA_OF_COLOURS`**. Decided naming:

- database **`SOC_HACKATHON_DB`**, schema `SEA_OF_COLOURS`
- warehouse **`SOC_HACKATHON_WH`**

A separate database (rather than a second schema) means teardown is one
`DROP DATABASE SOC_HACKATHON_DB` and a botched test can't touch real
seasons.

**There's a live bug to fix on the way.**
`create_snowpark_session()` already honours `database=` / `schema=` from
`sf_config` when building the session — but the deploy then runs
`USE DATABASE {DEFAULT_DATABASE}` / `USE SCHEMA {DEFAULT_SCHEMA}` from
the module constants, and the `.sql` files hardcode the names in their
own `USE` statements. So overriding in config today is **half-wired**:
you'd still deploy into `UMAN_SIM_DB.SEA_OF_COLOURS`. That's precisely
the collision this change is meant to prevent, so fix the plumbing
rather than just changing the constants.

Surfaces (small, once Phase 2 has deleted the retired agent SQL specs —
they account for most of the ~90 hardcoded hits in the repo):

- `scripts/deploy_soc_schema.py` — `DEFAULT_DATABASE` / `DEFAULT_SCHEMA`
  / `DEFAULT_WAREHOUSE`, and the two `USE …` calls that bypass the
  resolved config.
- `snowflake/soc_schema.sql`, `soc_views.sql`, `soc_procedures.sql`, and
  `orchestrator_2/snowflake/orchestrator_v2_schema.sql` — each opens
  with hardcoded `USE DATABASE` / `USE SCHEMA`. Template these at deploy
  time (substitute before executing) rather than leaving two sources of
  truth.
- Prose/docstrings: `snowpark/backend.py`, `scripts/run_season.py`,
  `RULEBOOK.md`, `docs/AGENT_ARCHITECTURE.md`, `docs/SNOWFLAKE_SETUP.md`.

Resolve names once, from env (`SOC_DB` / `SOC_SCHEMA` / `SOC_WH`) with
the hackathon defaults above, falling back to `sf_config`. Then verify
the real goal: deploy from scratch on a machine that already has the
dev deployment, and confirm the dev schema is untouched.

### Preserve the multiplayer + tunnel suite (do not trim this)

**Humans playing each other locally is a first-class part of the
event**, not a side feature — it's Phase 6 tab 6, and it's what makes
the room social rather than nine people each staring at a bot. The
whole one-click flow already exists and works; the risk in this repo is
that it gets quietly dropped as "not agent-related" during the Phase 2
trim or overlooked in the Phase 1.5 re-sync. **Explicitly keep and
verify:**

- **`server/tunnel.py`** — the one-click cloudflared *quick tunnel*
  manager behind the landing page's Multiplayer button. Owns a single
  ephemeral subprocess, scrapes `https://<random>.trycloudflare.com`
  out of cloudflared's log, exposes start/status/stop to the API, and
  cleans up `atexit`. **No Cloudflare account needed** — which is
  exactly why it suits a hackathon.
  - ⚠️ Two behaviours that must survive any refactor, both already
    documented in the module: `cloudflared` missing from PATH returns
    a surfaceable `error` instead of crashing (the UI should say
    `brew install cloudflared`), and the `ready` flag is
    **informational only and must never gate the flow** — it's probed
    via the OS resolver, which a corporate VPN blocks even when the
    player's browser reaches the tunnel fine. Someone "tidying" that
    into a readiness gate would break multiplayer on exactly the
    laptops most likely to be in the room.
- **`docs/MULTIPLAYER.md`** — the canonical runbook, and better than
  anything we'd rewrite from scratch. Keep its content, especially:
  - **The key gotcha**: invite QR codes and links are built from
    `window.location`, so the host must browse the **tunnel URL**, not
    `localhost`. It has the full laptop-origin → phone-result table.
  - The tunnel URL is **per-server, not per-game** — it survives new
    games, only `?session=<id>` changes.
  - The **LAN-only** fallback for no-internet rooms (`/api/meta/lan`,
    same-Wi-Fi + firewall caveats).
  - Hosting rules that keep seats in sync, and the troubleshooting
    table.
  - Two updates it needs rather than a rewrite: its TL;DR hardcodes
    `SOC_BACKEND=snowflake`, which should follow the auto-detect
    decision above; and it should mention the in-server Multiplayer
    button, since the runbook currently walks the manual
    `cloudflared tunnel --url` route.
- **The invite/join UX** — the per-seat QR + link modal
  (`server/static/vendor/qrcode.js`), the `?player=pN` deep link, and
  the mobile client (`server/static/mobile.html`). `p2_join_qr.gif` at
  the repo root is the demo asset for this; keep it or fold it into the
  Phase 6 guide.
- **Sync constraint:** the flow assumes a **single worker** serving all
  seats. Anything that changes how the server is launched (a
  `run_web.py` rework, a `--workers` flag, the backend auto-detect
  above) must not break same-process seat sync. Multiplayer works on
  the memory backend precisely because every seat hits the same
  process — worth an explicit regression check.
- **Add `cloudflared` to `quickstart_check.py`** as an optional check
  ("multiplayer-ready: yes/no, `brew install cloudflared`"), so a host
  finds out before their friends are waiting.
- **Verify it live during Phase 3**, two devices, on a real tunnel —
  not just by reading the code.

## Phase 4 — Simplified Orbit: purchasing + weapons buying only

> ✅ **Shipped 2026-08-25 as RULEBOOK v1.13.** Everything below is the
> plan as agreed; it was implemented as written. What actually landed,
> and the one follow-up it left behind, is recorded in *Phase 4 — as
> built* at the end of this section.

The Orbit phase collapses to the two decisions a player actually finds
interesting — **what to buy** and **what weapons to build**. Everything
else settles automatically.

### The new Orbit

**Keep — queued by the player, as today:**
- `build_harvester`, `build_probe`, `repair{unit}` — the purchasing
  decisions (§4.2).
- `build_emp`, `build_chaff` — the weapons buys (§4.9). Weapons stay
  **build-first**: bought in Orbit, fired during Nox out of
  `weapon_stock`. That part of the loop is unchanged. (`build_mine` was
  on this list until the caltrop was retired in v1.31 — RULEBOOK §4.9.4.)

**Remove or automate:**
- `ship_catapult` — the per-parcel credit-bid slot draft (§4.4). RED
  scores automatically at settlement instead of being bid for.
- `refine` (§4.3) **and** the Final Refinery (§4.3.1) — dropped
  entirely. This is the single biggest simplification: refining is the
  rule most likely to lose a first-time player, and §4.3.1 is a
  special-case ruleset that exists only for the last orbit.
- `solar_jettison` — the GREEN catapult flush (§4.7). GREEN is
  auto-deducted.

Note this **supersedes part of the earlier strawman**, which had
auto-buying probes/harvesters and auto-buying a weapon at a BLUE
threshold. The current direction is the opposite for those two:
purchasing and weapons buying are precisely the things that stay in
the player's hands, because they're the decisions worth making.

### Knock-on questions to settle before implementing

**✅ Decided (2026-08-25, by the user):**

- **Drop `MAX_ORBIT_ACTIONS` entirely.** With only purchases and
  weapons left, the slot cap is pure annoyance — **credits and blue
  become the only constraint**. Buy as much as you can afford.
  Engine note: the cap is enforced in `game/policy.py` and mirrored in
  the station UI's slot counter, and `plan_orbit_actions()` currently
  *stops planning* at 3; all three need the cap removed, not raised.
- **RED auto-ships every orbit, no penalty**, and **GREEN is
  auto-deducted at a flat −100**. Settlement is fully automatic in both
  directions; neither is a player decision any more.
- **The tier multipliers survive** (§4.4) — auto-shipping changes *when
  and whether you choose*, not what a parcel is worth. Richer RED still
  scores more, so where you send harvesters at night stays the decision
  that matters.

**✅ Settled (2026-08-25) — the four remaining questions, resolved
against the code rather than by guesswork:**

- **No economy retune. `ORBIT_CREDITS_PER_TURN = 1000`,
  `PROBE_BUILD_COST = 250` and `STARTING_BLUE_PURITY = 250` all stand.**
  The worry was that dropping `MAX_ORBIT_ACTIONS` would unleash probe
  spam and dissolve the fog. It doesn't: **orbit actions already carry a
  `count`** (`count=int(getattr(act, "count", 1) or 1)` in
  `orbit_resolver.py`), so one `build_probe` action always bought as
  many probes as you could afford. The cap limited *how many kinds of
  thing* you did per orbit, never the volume of any one of them.
  Removing it therefore changes no throughput — it only stops the UI
  telling you that repairing a rig and buying a probe and building an
  EMP is one thing too many.
- **BLUE needs no rework, and refining was never its source.** The
  concern was that dropping `refine` would break the blue economy.
  Backwards: `n_available` reads `blue_bank + sum(blue parcels)`, so
  blue arrives by **harvesting it**, and `REFINE_BLUE_COST` made
  refining a *competing consumer*. Removing refine leaves blue as a pure
  weapons budget — exactly the intent — and slightly loosens it, which
  is the right direction when the flagship exercise is teaching an agent
  to actually spend it. Starting bank stays one EMP.
- **RED-purity-as-fuel dies.** Confirmed in `orbit_resolver.py`: its
  only consumers are the catapult bid (`red_fuel` on the bid, §4.5) and
  the green-flush commitment (`_burn_fuel_avoiding`). Phase 4 removes
  both, so nothing reads it. Delete the concept rather than leaving an
  unreferenced field.
- **The locking model dies with it.** Locks exist to stop double-spending
  **parcels** across refine/ship/jettison — see the per-parcel bid /
  green-flush locking block. Those three are gone; credits and blue are
  plain scalar balances, so debiting at submit is sufficient. Keep the
  phase-flip and lock-clear, drop per-parcel reservation.

### Surfaces this touches (full fan-out — use the `AGENTS.md` checklist)

Engine first: `game/orbit_resolver.py` (~31 references to the removed
action names alone), the constants at the top of `game/session.py`, and
`snowpark/engine.py`'s `submit_orbit_actions`. Then `RULEBOOK.md` §4 —
a substantial rewrite of §4.2–§4.7 plus a new `### vX.Y` changelog
entry and a `Canonical Configuration` update; keep the `§` numbering
stable where you can, since code comments cite it. Then the UI: the
orbital station (`server/static/station.js` / `station.css`, the
refinery dialog and catapult-bid controls), `mobile.html`, and the
`orbital_exp/` copies. Then tests and `docs/RULES_PENDING_REVISION.md`.

### The agents have to be re-taught this (do not skip)

Changing the rules without changing the agents leaves all three of them
planning actions that no longer exist. This is exactly the drift
failure `AGENTS.md` warns about, and here it's guaranteed rather than
hypothetical:

- **`agent/heuristic_agent.py` → `plan_orbit_actions()`.** Its fixed
  priority playbook (repair → build harvester → build probes → ship RED
  via the catapult → weapons) loses the shipping priority outright, and
  what remains has to be re-ranked as a pure purchasing-plus-weapons
  budget problem. Note both heuristics come from this one function via
  the `weapons_enabled` gate, so **`RED_HARVEST_LITE`'s orbit becomes
  purchases only** — check it's still a coherent tutorial opponent when
  weapons were one of just two decision types and it's opted out of
  one.
- **The V12 harness** (`orchestrator_2/harnesses/tabula_v12/`):
  `orbit.py`'s `submit_orbit`, the orbit-directives block in
  `prompt.py`, `doctrine.py`, and the cost/value models in
  `option_economics.py` / `value_pyramid.py` — those price options
  against ship bids and refine yields that will no longer exist.
  `rules.py` is the rules text handed to the model and must match the
  new RULEBOOK exactly. Note V12's orbit is a *thin wrapper* over the
  shared `plan_orbit_actions()`, so fixing the heuristic fixes most of
  V12's orbit for free — but its doctrine and option pricing still
  describe the old economy to the model.
- ⚠️ **Do not accidentally close the hackathon's gap.** Simplifying
  orbit to "purchasing + weapons buying" makes the weapons decision
  *more* prominent, which is good — but it must stay a decision the
  **attendee** gets to improve. Keep V12 shipping without weapon
  *usage*, and keep its inherited orbit buying generic enough that
  writing a better purchasing heuristic is still a real exercise.
- **Prefer dynamic over hardcoded**, per `AGENTS.md`: have prompts read
  the surviving action set and costs from `meta.rules` rather than
  swapping one hardcoded list for another, so the retune after this one
  doesn't need another prompt edit.
- **Evals and tests**: the orbit scenarios in `sea_of_colours/evals/`
  and `orchestrator_2/tests/` (there's a sizeable body of
  `test_orbit_*` coverage) encode the old playbook as expectations.

### Sequencing note

Phase 5 (manual) documents the current orbit, and Phase 7's advisor
currently sidesteps orbit turns entirely. Both get easier if this lands
first — but this is also the riskiest change in the plan, so it stays
unscheduled until the user calls it.

### Phase 4 — as built (2026-08-25, RULEBOOK v1.13)

Landed as planned. `MAX_ORBIT_ACTIONS`, refining, the Final Refinery,
the RED credit-bid draft and the GREEN flush are all gone from the
engine, the view, both agents, the UI and the rulebook. RED and GREEN
settle automatically every orbit; BLUE is never settled. 916 tests
green (the one failure, `two_seams_choose_one`, pre-dates this work).

Three decisions worth recording because they aren't obvious from the
plan above:

- **The catapult graphics stayed.** The user's call, and the right one:
  the bidding *mechanic* was the problem, not the launch animation. Both
  lattices now size themselves to the settlement manifest — RED resting
  at 20 cells, GREEN at 12, growing past that on a heavy night — so the
  catapult always fills and fires instead of showing a half-empty draft
  board. The per-parcel launch stagger is compressed for large manifests
  so the wave lands in roughly constant wall-clock time.
- **Retired verbs fail loudly, not silently.** `_RETIRED_ORBIT_TAGS` in
  `policy.py` maps each removed tag to a reason naming v1.13, so a stale
  client, an old replay or a forked harness gets a waste line explaining
  itself rather than an unexplained no-op.
- **`tests/conftest.py` grew a `banked_parcels()` helper.** Auto-settlement
  empties the vault each orbit, which broke ~13 legacy assertions that
  read `hoard_squares` after a night. The helper unions hoard + shipped
  so those tests assert "what the night produced" rather than "what is
  still sitting in the vault".

**Outstanding follow-up (small, not blocking):** the orbit **eval
scenarios** in `sea_of_colours/evals/scenarios.py` still assert on
`ship_catapult` / `refine` / `solar_jettison`
(e.g. `PolicyContainsAction(action="ship_catapult")`). They are **not**
wired into `pytest` — `tests/test_eval_scenarios.py` only runs the night
scenarios, which is why the suite is green — but anyone running the full
eval battery will see them fail. Either retarget them at the new
purchase verbs or retire them; fold this into Phase 7.5, which is
already about making the eval/replay loop usable by attendees.

## Phase 4.5 — Multiplayer audit

Raised by the user on 2026-08-25: "the multiplayer offers a mobile
version of the game that no longer works. I am also having some problems
with it right now." Preserving the multiplayer/tunnel suite is an
explicit hackathon requirement, so this needs a dedicated pass rather
than opportunistic patching. Two things were fixed immediately because
they were breaking the documented happy path; the rest is open.

### Fixed already (v1.12)

- **Seat links pointed at the wrong page.** `buildSeatUrl()` inherited
  the path from whatever URL it was handed. That was fine for
  same-origin links (you're already on `/play`) but wrong for LAN links:
  `fetchLanOrigin()` returns a bare origin like `http://192.168.1.42:8000`,
  whose path is `/` — the landing page, which has no session-loading
  code. **Every LAN invite QR opened the title screen.** `buildSeatUrl`
  now pins `/play`, and `GET /` redirects (307) when it sees a
  `?session=`, so links already shared or printed still work.
- **The README documented the broken URL** (`http://<host>/?session=…`),
  as did `index()`'s docstring, which claimed deep links were
  "path-agnostic". Both corrected.

### Open — needs its own pass

- **`/mobile` is a frozen fork.** `server/static/mobile.html` is 953
  self-contained lines with 51 inline functions and no shared code with
  `app.js`; untouched since the initial commit while `app.js` (19k
  lines) kept moving. The main SPA already implements the exact mobile
  UX `docs/MULTIPLAYER.md` describes — the `cc-mobile-orders` bottom bar
  ("▤ ORDERS · n" / "» TRANSMIT"), the orders sheet, 14 phone media
  queries in `styles.css`. **Recommendation: retire `/mobile`**, drop
  the second QR from the invite modal (`app.js` ~17285 and ~17339), and
  let phones use `/play`. Cheaper than resurrecting a duplicate, and
  kills a whole class of future drift. Confirm with the user first —
  it's a visible removal.
- **The user's live problems are undiagnosed.** The URL bug plausibly
  explains "phone lands somewhere useless", but not necessarily all of
  it. Reproduce a real two-device game (laptop + phone over tunnel)
  before declaring anything fixed: seat picker, the 2.5s poll, the
  waiting-for strip, and night resolution with two humans.
- **No multiplayer regression test exists.** `tests/` covers the engine
  and the docs routes but nothing asserts that a seat link resolves to a
  playable seat. A cheap `TestClient` test over `/` → `/play` redirect
  and seat binding would have caught the QR bug.

### Phase 4.5 — as built (2026-08-26, v1.15)

**Root cause of "multiplayer doesn't work": `run_web.py` binds
`127.0.0.1`.** Every doc teaches `python run_web.py`, so the server the
host is running accepts nothing from the network — while the invite
modal independently asks for the host's LAN IP, gets a correct answer,
and renders a QR for an address nothing is listening on. The phone gets
a bare timeout. Each half looked right in isolation, which is why this
survived: `_lan_ip()` was accurate, the QR encoded exactly what it was
given, and no layer knew the other's assumption was false.

The tunnel path always worked, because `cloudflared` connects to
`localhost` from the same machine — which is why the documented runbook
appeared fine and the LAN path silently didn't.

**What landed**

- `run_web.py` grew a real CLI: `--host`, `--port` (previously ignored
  outright), `--reload/--no-reload`, and `--lan`, which binds `0.0.0.0`,
  forces reload **off** and prints the join address. Reload off is not
  cosmetic — the submit lock is a per-process `threading.Lock` and a
  memory season lives in that process, so a file save mid-party resets
  everyone. Binding stays opt-in because it is an exposure decision:
  there is no auth.
- `GET /api/meta/lan` now reports `lan_reachable` (a TCP self-connect to
  the address it is about to advertise) and `lan_firewalled`, not just
  `lan_ip`.
- `fetchLanOrigin()` refuses to build an origin it knows is dead, and
  the loopback branch no longer assumes a tunnel is required — a host on
  `--lan` is browsing localhost *and* reachable, and used to be sent off
  to install `cloudflared` for nothing.
- The tunnel-helper modal stopped asserting "your firewall blocks
  incoming connections", which was a guess and usually wrong, and now
  offers `--lan` first with the tunnel as the remote/firewalled answer.
- `tests/test_lan_hosting.py` (13 tests) pins the flag semantics and
  every branch of the diagnosis.

**Known limit, deliberately stated rather than hidden.** The self-test
proves the bind, not the path: a same-host connection to your own LAN IP
is short-circuited by the kernel and never crosses the macOS application
firewall, so a machine in block-all mode can answer itself while
refusing the phone. Hence `lan_firewalled` as a separate signal — and
the reason the two are reported independently is that they need opposite
fixes, and telling someone who already ran `--lan` to run `--lan` is
worse than saying nothing.

**Already closed earlier, confirmed during this pass:** `/mobile` was
retired in v1.13 (redirects to `/play`, second QR dropped) and seat
links were fixed in v1.12 with `tests/test_seat_links.py`. Both were
still described as broken in `docs/MULTIPLAYER.md`; that doc also still
told hosts to pin `SOC_BACKEND`, stale since v1.14. All reconciled.

**Not reproduced:** the two-device game itself. Core sync was verified
server-side (two human seats, correct pending state, night resolving on
the second submit, replay frames written), but the author's machine has
the macOS firewall in block-all + stealth, so a real phone join could
not be tested here. Worth one live check on a machine with the firewall
open before the day.

### Phase 4.9 — as built (2026-08-26, v1.18): we were the DNS block

**The gate from 4.8 was manufacturing the outage it was written to
detect.** Three explanations were offered across two days — a corporate
DNS sinkhole, then an intermittent block, then a reputation filter on
newly-observed subdomains — and all three were wrong. The real mechanism
is mundane and entirely ours.

Two events that look simultaneous are not. `cloudflared` prints its URL at
5.4–5.8s; the DNS record first resolves *anywhere* at 8.0–9.3s (three
runs, so a 2.4–3.5s window). The gate ran the instant the URL appeared and
retried every 0.8s, so its first
two or three lookups asked for a name that genuinely did not exist yet. A
resolver caches that answer, and `trycloudflare.com` publishes a negative
TTL of **1800 seconds**. One premature lookup therefore made the hostname
unresolvable *on the host's machine* — the one whose resolver decides
whether any invite link works — for the next half hour.

The A/B that settled it, two quick tunnels started seconds apart on one
laptop with the VPN up:

| | queried 8× at birth | left untouched 75s |
| --- | --- | --- |
| OS resolver at t+75s | **NXDOMAIN** | `104.16.231.132` |
| `1.1.1.1` (control) | `104.16.230.132` | `104.16.230.132` |

Same network, same moment, opposite outcomes; the hammered name plainly
existed, since the public resolver returned it. The only variable was our
own impatience. It also explains the "intermittent" reading in 4.8: the
Cloudflare name that *worked* was one whose first local lookup happened
minutes after creation, and the ones that "failed" were the ones the gate
had touched at birth.

**What changed**

- `_hostname_resolves` waits `_DNS_GRACE_S` (12s, ~3× the worst measured lag)
  before its **first** lookup, and backs off `_DNS_RETRY_S` (8s) between
  retries rather than 0.8s — every miss re-caches the negative answer, so
  a tight loop extends the damage instead of catching a slow record.
- The liveness watchdog is held behind the same gate via a
  `threading.Event`. It probes over `urllib`, i.e. through the same OS
  resolver, so an eager watchdog poisoned the name just as effectively —
  a second source of the same bug that would have survived fixing only
  the gate.
- Tests pin the *timing*, not just the outcome: that the gate is silent
  during the grace period, that the constants stay generous, and that no
  probe happens before the gate opens.

**Verified end to end** on the VPN: `tunnel.start()` returns `cloudflare`
in 17.0s, the URL fetches 200 through the OS resolver, and `status()`
reports `serving: true, rotates: false`.

**The lesson worth keeping:** a health check that mutates the thing it
measures is worse than no check. This one had a 30-minute blast radius and
a plausible scapegoat (corporate IT), which is exactly why it survived
three rounds of diagnosis.

### Phase 4.8 — as built (2026-08-26, v1.17): the transport, corrected

**4.7 shipped two conclusions that a day of measurement overturned.** Both
were honestly derived from what we could see at the time; both were wrong
in a way that mattered, and the corrections point in opposite directions.

**Correction 1 — the default provider expires.** 4.7 led with
`localhost.run` because it needs no install and no account, and left the
hostname question open. A soak answered it: the tunnel published
`cf80a4d4c4839b.lhr.life`, served 200 for **thirteen minutes**, then
moved to a new name — and the old one returned 503. `ssh` stayed alive
and healthy throughout, so nothing failed loudly. Every invite link and
QR already handed out was dead, and the *host* came off worst, because
the button parks their browser on the tunnel origin. Their docs say free
tunnels rotate "after a few hours"; thirteen minutes is what we saw, and
the second name outlived the first, so it isn't a fixed period either.

The documented fix — a **registered** SSH key instead of `nokey` — was
tested and rejected: an unregistered key is refused outright
(`Permission denied (publickey)`), so a stable name needs an account with
the key uploaded, per person. That is the setup cost this transport
exists to eliminate.

**Correction 2 — the DNS block is intermittent, not structural.** 4.7
recorded that Snowflake's resolver `NXDOMAIN`s `*.trycloudflare.com`, and
demoted Cloudflare on that basis. The next day, on the same laptop with
the VPN confirmed up and the corporate resolver in use, a fresh
Cloudflare quick tunnel resolved and served **200 to an ordinary request
with no bypass**. Both observations were real, which means the block is a
filter on *newly observed* subdomains that clears as a name ages — so it
cannot be encoded in a provider ordering at all.

**What landed**

- **Order flipped:** `cloudflare` leads, `localhost.run` is the fallback.
  The deciding property is hostname *stability*, not install cost, since
  a link that dies mid-game is worse than one extra `brew install`.
- **A DNS acceptance gate.** A provider is accepted only once the
  hostname it published resolves through the OS resolver — the same one
  the host's browser uses. It retries for a few seconds, because a new
  name legitimately takes a moment; only `socket.gaierror` counts, so an
  unrelated blip can't condemn a good provider. This replaces a guess
  about the network with a test of the actual condition, which is what
  makes a fixed order safe.
- **`rotates` on the provider and in `/api/tunnel/status`** — known up
  front, unlike `rotations`, which can only report a rotation that has
  already broken someone's link. The invite modal uses it to warn while
  the links are still good, and only when a rotating provider is actually
  carrying the tunnel.
- Budget raised to 60s in `api_tunnel_start`: each provider now has a
  gate to clear, and under-budgeting would starve the fallback the gate
  exists to reach.
- `tests/test_tunnel_providers.py` grew to 28 — the new order, the
  rotation flag, and the gate (rejection, fall-through, no orphan left
  running, retry, give-up, non-DNS errors, empty host).
- Docs corrected rather than merely updated: the DNS table in
  `docs/MULTIPLAYER.md` is now explicitly a **dated snapshot** with the
  intermittency explained, plus a section on rotation with the real
  numbers; `brew install cloudflared` is promoted back to recommended in
  `README.md`, `guide/index.html` and `scripts/quickstart_check.py`.

**Known limitation, stated plainly:** the gate checks the *host's*
resolver, which is not necessarily a guest's. It catches the common case
and guarantees the host can open their own game, but a guest on a
differently-filtered network can still be unlucky, and no amount of
local testing can detect that.

### Phase 4.7 — as built (2026-08-26, v1.16): multiplayer transport

**This supersedes 4.5's advice.** The firewall in that "known limit" is
not a local quirk — it is **Jamf MDM policy**, so *every* Snowflake
laptop blocks inbound. LAN play is therefore unavailable to the whole
room and no flag fixes it. An outbound tunnel is the only viable
transport, which promoted the tunnel from "remote fallback" to the
primary path.

**The tunnel was then found to be broken too, for a different reason.**
Corporate DNS returns `NXDOMAIN` for `*.trycloudflare.com` while the same
name resolves on `1.1.1.1`. Cloudflare's transport is *fine* — a request
with DNS bypassed returned 200 from the live game — so the tunnel was
perfectly healthy and simply unnameable. The failure is unfixable from
our side: the guest's browser has to resolve the host.

**Measured on this network (VPN up), which is why the fix is a list:**

| Provider | Hostname | Transport | Verdict |
| --- | --- | --- | --- |
| `localhost.run` | resolves | 200 | works |
| `cloudflare` | NXDOMAIN | works | unreachable by name |
| `localtunnel` | resolves | 502 | high ports dropped |
| `serveo` | resolves | blocked | SSH egress dropped |
| `pinggy` | resolves | no route | edge IP filtered |
| `ngrok`, `devtunnels` | resolve | 443 open | need an account |

Two independent policies — DNS blocklisting and egress filtering — catch
different providers, and no single provider clears both everywhere. So
`server/tunnel.py` became an **ordered provider list**, using the first
that publishes a URL.

**What landed**

- `Provider` dataclass + `PROVIDERS` list. `localhost.run` leads because
  it needs no install and no account (plain `ssh`, present everywhere)
  and isn't blocklisted; `cloudflare` follows for networks that permit it
  but drop SSH. The failure modes are complementary, which is the point.
- Sequential fallback with per-provider patience and a total budget;
  a provider that exits, or connects but never publishes, is torn down
  and the next is tried rather than taking the feature down.
- **A liveness watchdog, from a real observation:** localhost.run served
  503 for twelve minutes while `ssh` sat there happy, because the free
  tier "changes domain names regularly" (their docs). `running` was
  therefore a lie. `status()` now carries a tri-state `serving`
  (True / False / **None** = cannot tell) plus a `rotations` count, and
  the scraper updates the URL when a new one is announced.
- The tri-state matters: the probe uses the OS resolver, which is exactly
  what corporate DNS blocks, so a failure only counts **after** a
  success. Otherwise we'd accuse a working tunnel of being dead on
  precisely the networks the second provider exists to serve.
- `tests/test_tunnel_providers.py` (20 tests) pins ordering, fallback,
  the watchdog states and both URL regexes against real banner output.
- Docs reconciled: `brew install cloudflared` demoted from prerequisite
  to optional second provider in `README.md`, `guide/index.html` and
  `scripts/quickstart_check.py`; `docs/MULTIPLAYER.md` rewritten around
  the button rather than a manual command.

**Open question at time of writing:** how long an anonymous
localhost.run hostname actually survives. A soak is running. If names
rotate on the order of minutes rather than hours, the default should be
reconsidered — an invite link that dies mid-game is worse than one that
never worked, and the honest options are then to lead with cloudflared
where DNS permits it, or to accept an account for a stable name.

## Phase 4.6 — Per-game backend choice (NEXT — agreed, not started)

**Status: planned 2026-08-25, implementation deferred to the next
session. Nothing has been written yet.**

### Why

Backend selection is currently per *process*, resolved once at boot from
`SOC_BACKEND` (auto-detecting). That produces a bad split:

- An **attendee** on a clean laptop auto-detects to `memory` and gets a
  fast game. Correct.
- A **Snowflake employee** — anyone with the Snowpark extras and a
  key-pair `sf_config`, i.e. everyone likely to demo this — auto-detects
  to `snowflake`, so every single move is a warehouse round-trip. The
  game is noticeably slow and they never chose it. (Verified on the
  author's machine: `_resolve(None)` → `snowflake`.)

Rather than change the default, the user's call is to **choose per game,
in the New Game modal**, defaulting to whatever the process auto-detects
(so attendees still get memory automatically and nothing regresses).

### The two modes

| | Snowflake (default where available) | Memory |
| --- | --- | --- |
| Speed | a warehouse write per action | in-process dict |
| Persistence | seasons survive restart; replay works | gone on restart |
| Agents offered | human, heuristics, **and the LLM agent** | human + heuristics **only** |

**Memory games deliberately do not offer the LLM agent.** This is the
user's decision and it is a *product* one, not a technical limit —
worth recording because the code does not force it:

> V12 runs perfectly well on the memory backend. Cortex inference is a
> plain REST call with a PAT and never touches the store. What it
> *silently loses* is its memory systems — `hazard_memory.py`,
> `frontier.py` and `_v7/memory.py` all early-return unless the backend
> is Snowflake, so persistent hazard memory, enemy-landing tracking and
> the per-session narrative memory quietly switch off.
>
> A half-working V12 is worse than no V12: attendees would draw
> conclusions about their agent from a run where its memory was off, and
> memory systems are exactly what the hackathon is meant to teach.
> Hiding the LLM option on memory games keeps the mode honest — "fast
> local play, heuristics only" — and keeps the Snowflake story intact
> where it matters.

### Design (mapped, ready to implement)

The engine needs **no changes**: every `engine.py` entry point already
takes `store` as its first argument. `CompositeSocStore`
(`multi_store.py`) is a working blueprint for session→store routing.

1. **`backend.py`** — add a public `get_store_for(name)` (thin switch
   over the existing private `_get_*_store()` factories, reusing the
   singletons so no extra connections) plus a thread-safe in-process
   `session_id → backend` registry.
2. **`server/app.py`** — add `_store_for(game_id)` and swap the ~25
   bare `_store()` call sites. **Include `_drive_bots`** (line ~221),
   the background bot worker — it is easy to miss and would drive a
   memory game against the Snowflake store.
3. **`POST /api/game/new`** — accept a `backend` field, resolve the
   store before `init_session`, register the session against it, and
   echo it back so the client can display it.
4. **Session listing** — `GET /api/sessions` must merge across the
   stores actually in use, or the picker loses half the seasons. Reuse
   the `CompositeSocStore.list_sessions` union/dedupe/`source`-tag
   pattern rather than writing a second one.
5. **New Game modal** (`app.js` ~15474) — a backend dropdown defaulting
   to the auto-detected backend. Disable the Snowflake option with the
   reason when `snowflake_readiness()` says it is not available, and
   hide/disable the LLM agent option when Memory is selected.

### ⚠ The trap to fix first

`SOC_BACKEND` is a **module-level constant frozen at import time**
(`backend.py:220`), and the four V12 memory call sites compare against
it:

```
hazard_memory.py:147, hazard_memory.py:181,
frontier.py:97, _v7/memory.py:90, _v7/memory.py:211
```

With per-session backends this check becomes wrong: a server booted on
Snowflake serving a *memory* game would still let V12 write that game's
memory into Snowflake. Those sites already receive a `store` argument —
switch them from "is the **process** on snowflake?" to "does **this
store** have a live Snowpark session?" (`getattr(store, "session",
None)`, which they already fall back to checking). That is both the fix
and a decoupling worth having anyway.

### Out of scope (decided)

- **No durable backend column.** The registry is in-process only.
  Memory sessions do not survive a restart *by definition*, so losing
  the mapping loses nothing that still exists.
- **`multi` stays a server-wide mode**, not a per-game choice.

### Phase 4.6 — as built (2026-08-26, v1.14)

Implemented as designed, with **one premise corrected and one policy
decision changed**. Both are worth reading before touching this again.

**The stated reason for "no LLM on memory" was wrong.** The plan above
says V12 "silently loses its memory systems" on the memory backend.
It doesn't: `hazard_memory`, `frontier` and `_v7/memory` all populate a
*process-local* cache unconditionally and only hydrate from Snowflake
when that cache is cold. Within one server process V12's memory works
normally, and a memory game dies with the process anyway — so nothing
usable is lost. What is genuinely lost is the durable audit trail and
the ability to reopen the season.

**The real reason, and the decision.** That distinction matters because
of who it affects: playing V12 needs only a PAT (`SNOWFLAKE_SETUP.md`
§1), while the store needs key-pair auth (§2), so the two can diverge.
The question became *what the setup instruction says*, not what people
happen to have. Decided: **§2 is a standard hackathon setup step**, on
the grounds that the whole agent-iteration toolchain
(`turn_suite.py`, `replay_turn.py`, `advise_v12.py`) reads persisted
sessions — a PAT-only attendee could play V12 but never take a game
apart, which is the part of the day that teaches anything. So LLM seats
are refused on memory **unconditionally**, and §2 was rewritten as
expected rather than optional, with the `openssl` key ceremony spelled
out inline (it was previously an outbound link, and it is the most
error-prone step in the repo).

If that call is ever revisited, the gate is one function —
`_llm_backend_conflict` in `server/app.py` — plus the `allows_llm` flag
in `_backend_options`.

**What landed**

- `backend.py`: `get_store_for(name)`, an in-process
  `session_id → backend` registry (`register/backend_for/forget`),
  `store_for_session`, `stores_in_use`, and `snowpark_session_for(store)`.
- `server/app.py`: `_store_for(game_id)` on every game-scoped call site
  (including `_drive_bots`), `_merged_sessions()` behind `/api/sessions`
  and `/api/game/latest`, a `backend` field on `POST /api/game/new` that
  is validated, registered and echoed back, and `options` on
  `/api/meta/backend`.
- Frontend: a STORAGE radio group in the New Game modal, unavailable
  backends shown greyed-out *with the reason* rather than hidden, the
  agent roster filtered by the choice, and Quick game pinned to memory.
- `tests/test_per_game_backend.py` — routing, merged listing, the
  memory/LLM line, and a source scan that fails if any V12 module reads
  the frozen `SOC_BACKEND` constant again.

**Two docs bugs found and fixed on the way**

- `SNOWFLAKE_SETUP.md` warned twice, and `AGENTS.md` once, that
  **NEW GAME wipes the target schema**. It doesn't — `init_session` has
  been append-only since v0.5, nothing in the new-game path calls
  `wipe_all_sessions`, and `tests/test_session_persistence.py` exists to
  pin that. A false destructive warning on the persistent backend is
  exactly the thing that pushes people onto memory.
- The guide still told readers the env var in front of `run_web.py` was
  "not optional" — stale since Phase 3's auto-detection — and quoted a
  pytest baseline of 892.

## Phase 5 — Manual pass

Finish/polish the existing interactive manual (`manual/index.html`,
`manual.css`, `manual.js` — carried over from the dev repo's own
mid-edit state, not a finished baseline). Revisit if Phase 4 lands and
changes the ruleset underneath it.

## Phase 6 — Hackathon Guide (leveraging the manual)

Lightweight sibling to `manual/` — same retro-terminal shell/CSS, same
tab-strip pattern, but prose/snippets/buttons rather than new animated
demos. The tab ladder **is** the north-star arc, one rung at a time:

1. **Welcome** — what the game is, 60-second pitch.
2. **Install & Play** — clone → `pip install` → `run_web.py`, and
   you're on a board. Then the Snowflake ladder, in the order Phase 3
   untangles it: PAT for Cortex (so you can meet V12), then key-pair +
   schema deploy (so your seasons persist on your own demo account).
   Make clear which step unlocks what, and that none of it blocks
   playing.
3. **Manual** — the interactive rulebook (`manual/`), linked in as-is.
4. **Your First Game** — vs `RED_HARVEST_LITE` (weapons off), training
   wheels.
5. **Level Up** — vs full `RED_HARVEST` (weapons on), the real
   baseline.
6. **Play With Friends** — multiplayer, and a genuine highlight rather
   than a footnote: one-click tunnel, scan a QR, play the person next
   to you. Content exists in `docs/MULTIPLAYER.md` and needs bringing
   into the guide's voice; lead with the Multiplayer button, keep the
   browse-the-tunnel-URL gotcha prominent, and note the LAN-only
   fallback for a room with bad wifi. `p2_join_qr.gif` is the ready-made
   demo asset.
7. **Meet V12** — how the champion LLM agent is built (harness
   anatomy: `prompt.py` / `doctrine.py` / `agency.py` / `packager.py`),
   and **explicitly, up front: it doesn't use weapons yet**. This tab
   is where the deliberate gap gets named.
8. **Build Your Own Agent** — the mint-a-new-version workflow. Now
   mostly written: point at `scripts/new_agent.py` and
   `harnesses/tabula_v12/README.md` rather than restating them.
9. **Improve Your Agent** — the concrete levers: doctrine knobs, prompt
   sections, the option menu; plus how to use the invoker (Phase 7),
   the headless runner and the replay viewer to watch your agent think
   and iterate. **"Add weapons" is the flagship worked example.**
10. **Ship It** — the drift-testing checklist
    (completeness / timing / token alarms) that must pass, then the
    tournament submission process.

Note this ladder deliberately front-loads *playing* (tabs 1–6) before
*building* (7–10) — someone who hasn't felt a chaff flare cancel their
turn has no intuition for why the weapons gap in V12 matters.

### First cut — shipped 2026-08-25 (`guide/index.html`)

Built as a **scrolling document**, not a tab shell. The ladder above is
a reading order, and a scroll with a sticky section nav expresses that
better than tabs, which invite jumping into step 7 before step 2. The
manual keeps the tab shell because its tabs are independent demos; the
guide's sections are sequential and each one assumes the last.
Self-contained single file (inline CSS/JS), so it opens from a
downloaded folder before Python exists on the machine.

Palette, fonts and frame chrome are lifted from `manual/manual.css`, so
the two read as one publication. Sections map to the ladder as:
`00 What this is` (1) · `01 Install` + `02 First run` (2) ·
`03 First game` (4) · `04 First night` (3, via deep links) ·
`05 Snowflake` (2's ladder) · `06 Play V12` (5, 7) ·
`07 Multiplayer` (6) · `08 Build` (7–9) · `09 Trouble`.

**Also landed:** `manual/manual.js` now honours `#tab=<name>` deep
links (`activateTab(tabFromHash() || "tiles")` plus a `hashchange`
listener), so a guide step can drop the reader on the exact demo it
cites. Namespaced `tab=` because bare `#editor` was already taken.
Invalid or absent hashes fall back to `tiles`.

**Deliberately honest, don't "fix" these by softening the text:**
- The repo is **private** (`sfc-gh-lgalan/sea-of-colours-hackathon`), so
  the guide says so and explains that GitHub 404s rather than 403s for
  repos you can't see — otherwise every un-added attendee reads "404" as
  "wrong URL". Attendees must be added as collaborators. If it ever goes
  public, update the `data-repo-url` block in §01 and drop that note.
- The `pytest` checkpoint names the known `two_seams_choose_one`
  failure and its expected count rather than claiming green, so an
  attendee doesn't debug a pre-existing bug as an install problem.
- `02 First run` leads with `SOC_BACKEND=memory` and explains *why* the
  env var is mandatory. **Phase 3 should delete that explanation**, not
  reword it — when the backend auto-detects, this whole callout and the
  matching troubleshooting rows come out.

**Still owed (tabs 8 and 10 of the ladder):** the mint-a-new-agent
workflow and the ship-it/drift-testing checklist are one-paragraph
gestures in `08 Build`, pending Phases 7.5 and 8. `04 First night`
narrates the loop against the *current* orbit — Phase 4's rework
invalidates its last step and the `07 Multiplayer` hosting advice
depends on Phase 3's backend decision.

## Phase 7 — In-game agent advisor (front-end invoker)

**Plan only — nothing here is implemented yet.**

Bring the `scripts/advise_v*.py` read-only `submit=False` advisor into
the web UI as a button you can hit mid-turn, while you're playing your
own seat: *"what would the agent do here, and why?"* — shown as a
readable card **and** drawn on the board — with the option to adopt its
answer as your own policy. Three deliverables: the invoker, the card,
the overlay. (`advise_v11.py` is the version in this repo today;
`advise_v12.py` arrives with Phase 1.5 and is the one to build on.)

### 7a — Invoker button + backend

- Keep the **invoker style** of `advise_v11.py` verbatim: attach to the
  live store, `harness.run(..., submit=False)`, and hard-fail if the
  result comes back with `submitted_policy=True` rather than render a
  misleading card. Nothing is submitted, no turn memory or snapshot is
  written, the seat stays yours.
- Backend: a read-only `POST /api/game/{id}/advise` taking the seat and
  the agent to ask, returning the raw payload (`moves` + `extras`) —
  the same blob `--json` dumps today. Render the card **client-side**
  from that payload so the txt dump and the on-screen card can't drift
  apart.
- Which agents can be asked: `V12` **and** `RED_HARVEST` (and
  `RED_HARVEST_LITE`). Note the asymmetry — V12 is a live Cortex call,
  so it needs a PAT and will fail on the pure-offline
  `SOC_BACKEND=memory` path; the heuristics run anywhere and cost
  nothing. The button should degrade honestly (offer the heuristic
  advisors, explain why V12 is unavailable) rather than error out.
  **This matters for the north star:** a player who hasn't set up a PAT
  yet must still get *something* useful from the advisor button, so the
  heuristic advisors carry it until they do — and the "V12 needs a PAT"
  message is a natural prompt to go finish that step.
- Once an attendee is running their **own** forked agent, the advisor
  should be able to invoke that too — it's the fastest debug loop they
  have. Drive the agent list off `AGENT_LABEL_BINDINGS` rather than
  hardcoding three names.
- **Phase constraint carried over from the CLI:** the advisor covers
  NIGHT planning, where the THINK/PLAN reasoning lives. ORBIT turns are
  skipped there because advising them would submit. Decide whether to
  simply disable the button during orbit (matching today's behaviour)
  or to do the extra work of making orbit advice read-only too —
  cheaper to disable it for the hackathon, and Phase 4 may change the
  orbit phase out from under this anyway.
- Latency: a V12 advisory is a two-stage LLM call (THINK then PLAN),
  i.e. tens of seconds. Needs a spinner/elapsed timer and a cancel,
  and it must not block the rest of the turn UI.

### 7b — The card (harness + reasoning, readable or dumped)

Full fidelity with the CLI card — `_render_card()` in
`scripts/advise_v11.py` is the spec, and the on-screen panel should
show the same sections in the same order:

- Header: seat, day/cap, score, whether you've already submitted, the
  board the harness actually saw (`_board_summary()`), and the thinker
  telemetry (`thinker_api`, `thinker_ms`, `thinker_retried`).
- `OPTIONS OFFERED` — the heuristic-surfaced menu the thinker chose
  from (`option_menu_block`).
- `STAGE 1 — THINK` prose, then `STAGE 2 — PLAN` (posture, plan IDs,
  targets, avoid, chaff_react, note) and the raw PLAN JSON.
- The compile footer: how many moves it produced, packager vs. mover,
  fallback, selected option IDs, sanitizer/corrector changes.
- Expandable extras mirroring the CLI flags: `--full` (packager log +
  packed moves) and `--prompt` (the **verbatim** THINK and PLAN prompts
  — the single most useful thing for someone about to write their own
  harness, since it's exactly what the model receives).
- **Dump to txt**: a download/copy button producing byte-identical
  output to `advise_v11.py --out card.txt`, plus the raw JSON trace
  (`--json`). Same 72-column rules and monospace framing — these get
  pasted into chats and issues, so plain text matters more than pretty
  HTML here. Best way to guarantee identity is to port `_render_card()`
  once into a shared renderer rather than reimplement it in JS; worth
  deciding at implementation time whether that lives server-side (one
  renderer, endpoint returns both text and payload) or client-side.

### 7c — Magenta board overlay + adopt-into-policy

- Paint the advised moves onto the main board as a **magenta** ghost
  layer: recommended step paths, drop/probe targets, weapon aim points,
  each tagged with the option ID from the card so board and prose
  cross-reference. Sibling to the existing
  `paintPlannedOrdersOverlay()` (`server/static/app.js`), and it should
  compose with, not replace, your own queued orders — the point is
  seeing your plan and the agent's side by side.
- ⚠️ **Colour collision to resolve at implementation time:** magenta is
  already the **p3 seat colour** (`NGM_SEAT_LABELS` / the p1-white,
  p2-yellow, p3-magenta, p4-cyan scheme). In any game with three or
  more seats a magenta advisory layer reads as "p3 did something."
  Keep magenta but make the treatment unmistakably advisory — dashed
  ghost outlines, reduced opacity, a distinct glyph, an animated
  marching-ants stroke — and check it specifically against a 4-seat
  board with a live p3 before calling it done. If it still reads
  ambiguously, raise it with the user rather than silently switching
  colour.
- **Adopt button:** loads the advised moves into your order queue as
  editable orders — your policy, pre-filled, not auto-submitted. You
  can then tweak and submit yourself, or clear it. Partial adoption
  (per-move or per-option-ID) is the nicer version if it's cheap;
  all-or-nothing is acceptable for a first cut.
- Adopted moves must go through the **same validation path as
  hand-entered orders** — never trust the advisory payload as
  pre-validated, since the board can move underneath it (a stale
  advisory from before an opponent's action, or from before you edited
  your own queue). Re-validate on adopt and again on submit, and show
  clearly when an advisory has gone stale.

### Why this matters for the hackathon

This is the main teaching surface of the whole event: it's how an
attendee sees a real harness's prompt, reasoning, option menu and
compiled moves against a board they personally understand, before they
fork V12 in Phase 8. Worth more polish than its position in the phase
list suggests.

It's also the most direct way to **show** the deliberate gap rather
than just assert it. Invoke V12 on a night where a rival's harvesters
are clustered, and the card will reason about the threat, list its
options, and then plan a harvest — because no weapon move is on its
menu to pick. That's a far better motivator for "go give it weapons"
than a paragraph in a README.

## Phase 7.5 — The agent iteration loop

> **See `docs/AGENT_LOOP_PLAN.md` (v1.37).** That is now the design
> document for this phase and for Phase 8's submission workflow: the
> three-speed loop (pinned turn / scenario suite / season), the `soc`
> façade, the semantic card diff, and a build order. The notes below are
> the reasoning it was written from and still stand — in particular the
> card-vs-card judgement, which the plan agrees is the highest-value
> single item.

**Decided: bring the dev repo's turn-replay suite across in Phase 1.5,
then do real work on it here.** It arrives as-is (dev tooling, written
for one person who already knows how it works); this phase is about
turning it into something an attendee can pick up.

The pieces coming over: `scripts/turn_suite.py`, `turn_library.py`,
`replay_turn.py`, `replay_card.py`, `dump_suite_cards.py`,
`scenario_facts.py`. Read them first — the commit that introduced them
(`3bd15fd` / `0f7f1ff`, "turn-replay suite" + "archive the pre-Phase-A
card set so changes can be judged card-vs-card") describes the actual
workflow: pin a set of turns, run a harness over them, and diff the
resulting cards to judge whether a change helped.

**That card-vs-card diff is the single most valuable thing here.** It's
the answer to "I changed my doctrine — is it better?", which is the
question every attendee will have within an hour of forking V12, and
it's much cheaper than running full seasons.

The work:

- **Read and simplify.** Six scripts with overlapping responsibilities
  is more surface than this needs. Look for the smallest set of
  commands that supports: pick turns → run my agent → see the cards →
  diff against a baseline.
- **Make it fork-aware.** It should target *any* registered binding
  (the attendee's `tabula_v13`), not just the harness it was written
  for. Same `AGENT_LABEL_BINDINGS` lookup as the Phase 7 advisor.
- **Ship a starter turn library** — a handful of interesting pinned
  turns (a contested seam, a rival cluster worth EMPing, a blue-rich
  board) so nobody has to build a corpus before they can iterate. The
  weapons exercise needs turns where weapons obviously pay.
- **Join it up with Phase 7.** The advisor card and the replay card
  should be the same artifact seen two ways — live in the UI, and
  batched over pinned turns. If they diverge, an attendee learns the
  format twice.
- Document the loop in Phase 6 tab 9 ("Improve Your Agent"), with the
  weapons change as the worked example: baseline cards → add weapons →
  diff → season run.

## Phase 8 — Drift-testing alarms + submission workflow

- Extend `sea_of_colours/evals/assertions.py` with `OrdersIssuedFully`,
  `WallclockBudget` (vs. `cortex_chat.py`'s wall-clock cap constants —
  note Phase 2 may have changed exactly where these live if the legacy
  `cortex_invoker.py`'s `WALLCLOCK_CAP_OVERRIDES` gets removed), and
  `TokenBudget` (verify/add token-usage capture in `cortex_chat.py`
  first — check whether the chat/completions response envelope already
  surfaces token counts before assuming you need to add capture).
- `docs/HACKATHON_SUBMISSION.md`: fork a harness folder (`python
  scripts/new_agent.py --team X --name Y`, which also registers the
  binding), run the drift checklist, open a PR with the harness plus its
  registry entry.
- **The flagship worked example: "give V12 weapons."** Write it as a
  full walkthrough, since it's the exercise the whole guide funnels
  into and it exercises every part of the submission path:
  1. Fork `tabula_v12/` → `tabula_v13/` (or the attendee's own name),
     register the binding.
  2. **Orbital purchasing heuristic** — override the inherited
     `plan_orbit_actions()` delegation in `orbit.py` with a real
     buy-the-right-weapon rule (situation-aware, not the shared
     blue-threshold default). Note `orbit.py` is already the natural
     seam: it *wraps* the shared planner and post-processes its
     actions, so an attendee has a worked pattern to copy.
  3. **Feed the currency** — retune the `DOCTRINE_BLUE` ranking in
     `doctrine.py` / the blue thresholds in `value_pyramid.py` so the
     seat actually harvests enough blue to pay for a magazine.
  4. **Inject the plays** — surface weapon moves as option-menu
     entries, teach the doctrine when each is worth firing
     (`emp_launch` to disable a cluster, `chaff_flare` to blank a
     turn), and extend the packager /
     validators to emit and accept those move shapes.
  5. Run it against `RED_HARVEST` (weapons on) and against stock V12,
     and show the score delta as the proof it worked.
- A good measurable hook for step 5: stock V12 ends seasons with an
  **unused weapon stockpile** it paid blue and credits for. "Wasted
  spend → zero launches" is a clean before/after metric, and worth
  surfacing in the eval assertions above.

## Working conventions established so far (keep following these)

- **Don't commit unless explicitly asked.** Stage + describe changes,
  wait for a go-ahead, per this repo's git norms (mirrors the original
  dev repo's `AGENTS.md` git section).
- **Prefer targeted `StrReplace` edits over rewriting whole files**,
  especially in `heuristic_agent.py` (huge file) and
  `binding_registry.py` (many similar dict entries — easy to
  fat-finger the wrong one without full context).
- **Re-run `SOC_BACKEND=memory pytest` after each meaningful change**
  and compare against the 539-passed/1-pre-existing-failure baseline
  rather than assuming green.
- **Clean up any throwaway `.venv` / `__pycache__` / `.pytest_cache`**
  created while verifying, before considering a phase "done" — keeps
  `git status` honest and the tree light for the next diff.
- Fan-out discipline from the original repo's `AGENTS.md` still
  applies here: a rule/constant/formula change is not "done" until
  engine → `RULEBOOK.md` → agent prompts/logic → client/UI → tests →
  trackers are all reconciled. Phase 4 in particular will need this
  checklist in full.
