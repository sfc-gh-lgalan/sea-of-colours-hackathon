# Tabula v7 — PLAN: two-call reasoning split (haiku, Agents API)

> ## ⚠️ FINDINGS — first A/B (seed 42, 2026-07). Split is PARKED, default stays `single`.
>
> Built, tested (345 pass), deployed (3 live agents), and run end-to-end. The
> A/B says **do not promote the split**:
>
> | Run (seed 42) | thinker cap | thinker fired | p1 (v7) | p2 (heur) | winner |
> |---|---|---|---|---|---|
> | A | 18s | 0/7 nights (silent v6) | **1768** | 1622 | p1 ✓ |
> | B | 50s | 2/7 nights | **980** | 3083 | p2 ✗ |
>
> **What we measured (live, not assumed):**
> 1. **haiku extended-thinks HARD when told to reason on the page.** The thinker
>    emits ~27 KB of reasoning on the Agents API's *thinking channel* (correctly
>    diverted by the invoker) BEFORE any answer text; the parseable `DECISION:`
>    line only lands at **~44 s**. At an 18 s cap it was truncated mid-think
>    every night → empty answer → `directive=None` → **the split silently ran as
>    plain v6** (that's why run A ≈ the v6 champion).
> 2. **The mover does NOT extended-think.** Measured 0 thinking bytes, moves-first
>    JSON in ~5 s. So the split doesn't "add" a missing reasoning pass to a
>    thinking-less mover — it bolts a slow *second* pass on.
> 3. **Even at a wall-breaking 50 s cap the thinker only lands a usable directive
>    ~2/7 nights**, and when it does it's low-information (`posture=aggressive,
>    targets=[]`). It cost **~+40 s/night** (turns 60–66 s, over the 55 s wall)
>    and the head-to-head **regressed** (980 vs 1768). n=1 per arm + haiku
>    stochasticity means the swing isn't *all* the thinker — but there is **zero
>    evidence of benefit** and a clear latency + wall cost.
>
> **Conclusion.** The two-call split on haiku + Agents API is not worth
> promoting: the reasoning pass is unbounded (~30 s) and can't be closed early
> (the DECISION is emitted last, after the think), so it can't co-exist with the
> mover under the wall. Kept behind `TABULA_V7_MODE=split` (default `single` =
> v6) as a documented negative result.
>
> **Silver lining → v8.** The thinker proves haiku *can* produce rich, coherent,
> structured reasoning with a clean machine-readable conclusion. The only
> problem is it's **unbounded**. That is exactly what `reasoning.max_tokens` on
> the Cortex *inference* API fixes (bound the think to fit the wall) — see
> `../tabula_v9/PLAN.md` (native bounded thinking). v7's result strengthens the
> v9 case rather than the Agents-API split. (`../tabula_v8/PLAN.md` is the
> separate data + doctrine + observability bundle on the current stack.)

**Goal (original).** Give the agent a dedicated reasoning pass whose *conclusion* is
reliably captured, then hand a tiny directive to the proven v6 moves-first
mover. This converts last-night comprehension into changed behaviour (chaff
defence, grounded targeting) on the **current stack** — no endpoint change, no
model change, no loss of the moves-first win.

**Core idea.** Two sequential Cortex Agent calls per night:

```
call 1: THINKER (reason-first, DECISION-last)  call 2: MOVER (v6 moves-first, verbatim)
  in : full board + grounded reflect             in : same board + hints + STRATEGIST DIRECTIVE
       + opponent/chaff intel + hints                  (injected from call 1's DECISION)
  out: <reasoning on the page ...>               out: {"moves":[...], plan, rationale, ...}
       DECISION: {posture,targets,flags}    ->
  ~44s MEASURED (see FINDINGS); NOT ~18s         ~5-15s, predicate closes on balanced JSON
```

> NB: the "both calls finish reliably under the wall" premise below was the
> hypothesis; the FINDINGS box above records that it did NOT hold — the thinker
> emits its DECISION only after ~30 s of extended thinking, so the predicate
> can't close it early and it overruns the wall.

---

## 1. The THINKER (call 1) — new agent `SOC_RED_REAPER_TABULA_V7_THINKER`

Purpose: read the world (especially the grounded prior-night reflection and
opponent/chaff intel) and commit a **posture + targets**, so the mover executes
a decision instead of re-deriving one under byte pressure.

**Reasoning-first / DECISION-last contract.** The thinker's *whole purpose* is
to let haiku actually deliberate on the page (for a non-thinking model, on-page
CoT *is* its test-time reasoning) free of moves-pressure. So:
- **Reason on the page** as long as needed, then **FINISH with exactly one
  `DECISION:` line** (valid JSON directive) as the last line.
- The completion predicate closes the socket as soon as a well-formed `DECISION:`
  line has streamed. Because the required output is tiny (one line, not a moves
  array), truncation risk is low.
- **We consume ONLY the parsed+validated DECISION.** The reasoning transcript is
  audit-only and is NEVER injected into the mover. If the thinker truncates
  before a valid DECISION, the directive is `None` and the mover degrades to
  single-call v6 — we never feed raw/partial thinking to the mover (that is the
  finisher-blindness antipattern).

**Directive schema (tiny — keep the handoff seam narrow):**
```json
{
  "posture": "aggressive" | "defensive" | "redsign_race" | "final_convert",
  "targets": [[x,y], ...],        // 1-3 anchor cells / disks to prioritise
  "chaff_react": true | false,    // shorten chains + early pickup if true
  "avoid": [[x,y], ...],          // optional: enemy-probe / contested cells
  "note": "one short phrase"      // human-readable intent for audit
}
```
Emitted as a `DECISION: {…json…}` line so the predicate can close on it.

**Budget:** wallclock cap ~18s (invoker override); completion predicate closes
the socket as soon as a valid `DECISION:` line is present. Byte cap is roomy
(reasoning is free here — it lives in call 1's buffer, never the mover's).

**Inputs it uniquely leans on for G1/G2:**
- Grounded reflection (actual vs predicted banked, chaff/crush losses) — already
  built in v6.
- Opponent-weapon tracker (chaff/EMP stock) — drives `chaff_react`.
- Hint summaries (top chains, hot-drop/redsign combos) — drives `targets`.

## 2. The MOVER (call 2) — reuse the v6 primary verbatim

- Reuse `SOC_RED_REAPER_TABULA_V7` (the current v6-equivalent spec) unchanged:
  moves-first, reason-in-head, 8000 cap, completion predicate, sanitizer.
- Inject ONE block into its prompt: `STRATEGIST DIRECTIVE: posture=…;
  targets=…; chaff_react=…; avoid=…`. The mover treats it as a high-priority
  hint (like the wishlist), still bounded by RULES/sanitizer.
- Everything else (hints, drop-legal, reflection) stays as today.

## 3. Harness orchestration (`harness.py`)

1. Build the shared board context once.
2. Invoke the thinker with an ~18s cap + `DECISION:` completion predicate.
3. Parse + validate + sanitize the directive (`directive.py`): clamp targets to
   in-bounds cells, drop malformed fields, cap list lengths. Invalid/empty →
   directive = None.
4. Build the mover prompt (inject directive block if present) and invoke the
   existing mover path unchanged.
5. Persist the thinker directive + reasoning + mover output to
   `SOC_AGENT_INVOCATION` for audit.

**Fallbacks (guarantee v7 ≥ v6):**
- Thinker errors / empty / invalid directive → mover runs with **no** directive
  block → identical to single-call v6.
- Mover partial/illegal → existing finisher + sanitizer + heuristic net.

## 4. Wallclock budget (the main cost to manage)

**HYPOTHESIS (did not hold — see FINDINGS):**

| Phase | Cap | Hoped typical | MEASURED |
|---|---|---|---|
| Thinker | 18s→50s | ~8-12s | **~44s** (thinks ~30s first) |
| Mover | 35s | ~10-15s | ~5-15s ✓ |
| **Total** | | ~20-27s | **~60-66s (OVER the 55s wall)** |

The predicate early-close does NOT help the thinker: haiku streams ~30 s of
extended thinking *before* the DECISION, so there is nothing to close early on.
This is the reason the split can't fit the wall on the Agents API.

## 5. A/B toggle

`TABULA_V7_MODE = split | single` (env var, default `single` until validated).
`single` = exact v6 behaviour (mover only). Lets us measure split vs the frozen
v6 champion on identical seeds.

## 6. Tests (`tests/test_tabula_v7_split.py`)

- Directive parse/validate: good JSON, malformed, out-of-bounds targets clamped,
  over-long lists truncated.
- Injection: directive present → mover prompt contains the STRATEGIST DIRECTIVE
  block; absent → prompt identical to single-call.
- Fallback: thinker empty/error → mover invoked with no directive (single-call
  parity).
- Predicate: thinker closes on a `DECISION:` line; mover still closes on
  balanced JSON.
- Wallclock caps wired (thinker override present in `cortex_invoker.py`).

## 7. Success criteria

- Beats frozen v6 (and heuristic) on seeds 42 / 7 / 99 / 2024.
- Measurable drop in harvesters lost to chaff at dawn, driven by `chaff_react`
  postures that actually shorten chains / pull pickups earlier.
- No increase in truncated/finisher-fallback turns vs v6.
- Turn wallclock p95 < 55s.

## 8. Risks & mitigations

- **Additive wallclock** → predicate early-close + tight thinker cap; A/B before
  committing.
- **Handoff fidelity seam** → directive is tiny; geometry stays in hints; mover
  still sanitized. Drift is bounded and observable (directive persisted).
- **2× token cost** → accepted; revisit if it dominates.
- **Still haiku** → this buys channel separation + a reasoning pass, not a bigger
  brain. If decision *quality* is the ceiling, that's v8's job.

## 9. Build order

1. `directive.py` (schema + validate + sanitize) + tests.
2. `SOC_RED_REAPER_TABULA_V7_THINKER` SQL spec (conclusion-first) + invoker caps.
3. Harness two-call wiring + directive injection + `TABULA_V7_MODE` toggle.
4. Audit persistence.
5. A/B run vs v6 on one seed, then the full seed set.
