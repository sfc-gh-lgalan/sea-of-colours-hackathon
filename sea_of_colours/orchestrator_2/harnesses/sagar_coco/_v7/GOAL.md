# Tabula v7 — stated goal

## One sentence

Give haiku a dedicated, **reliably-captured reasoning pass** (call 1) that commits
a posture + target, then execute it with the **proven v6 moves-first mover**
(call 2) — turning last-night comprehension into *changed behaviour* (chaff
defence, grounded targeting) **without any endpoint or model change**.

v7 is a **two-call reasoning split** on the current stack (`claude-haiku-4-5` +
the Cortex Agents `agent:run` API). Detailed plan: **`PLAN.md`**.

## Why a split now, when tabula was born by killing the old one

The old split (`pilot_v3` strategist/tactician, `pilot_v4`) failed for a reason
we have since fixed, not because splitting is wrong. The old *mover* predated the
v6 recipe — no `{"moves":[` first-byte rule, no reason-in-head, no roomier cap,
no completion-predicate early-close, no sanitizer backstop — so it rambled and
truncated before emitting moves ("the tactician never finished"). That was a
moves-first problem masquerading as a split problem.

With the v6 recipe on the mover **and** a *conclusion-first* contract on the
thinker, **both calls now reliably finish**, and the completion predicate closes
each one early so the two calls fit inside the existing 55s wall.

## Invariants (do NOT regress)

- **Moves-first is sacred.** Call 2 reuses the v6 mover contract verbatim
  (`{"moves":[` first, reason-in-head, 8000 cap, predicate, sanitizer).
- **Strictly ≥ v6 by construction.** If the thinker fails/empties, the mover
  runs with no directive and degrades to single-call v6 behaviour.
- No new game mechanics. The directive payload stays tiny (hints carry geometry).

## Deferred to v8

Native **bounded extended thinking** (single call, highest fidelity) via the
Cortex *inference* API (`/api/v2/cortex/v1/chat/completions`,
`reasoning.max_tokens`) on a thinking-capable model (`claude-sonnet-4-6`). That
is an endpoint + model migration and carries the original comprehension agenda
(G1 chaff-reactive planning, G2 grounded targeting). See
`../tabula_v8/PLAN.md`. The thinking-channel isolation already shipped in
`sea_of_colours/agent/cortex_invoker.py` is the shared groundwork for it.
