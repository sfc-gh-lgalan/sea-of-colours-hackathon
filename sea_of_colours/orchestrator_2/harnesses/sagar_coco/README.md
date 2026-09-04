# SAGAR_COCO

A fork of V12 built for the TechUp Mallorca league by Sagar Morakhia with Cortex Code.

**Thesis: the LLM should never be the last thing that touches an order.** V12 already
splits search (Python builds the legal menu) from judgement (the model picks). This fork
adds the third stage every production agent needs: verification. The model proposes, the
harness verifies the queue against the rules of good play, and doctrine decides.

## What changed (everything lives in this directory)

| Module | Change |
|---|---|
| `verifier.py` (new) | View-only audit of any order queue against the doctrine invariants (land on the pure, no wake re-entry, no rival GREEN, tail policy, probe order, slot and stock caps, deploy all) plus a deterministic **shape planner** that builds a complete legal night from the same fog-limited view. `choose()` submits the LLM plan when it is clean and within value tolerance, otherwise the shape plan, and records every reason on the card. |
| `tactics.py` (new) | Prices every weapon option in expected points (probes killed, tempo bought, hours spent, friendly fire), tags the best one `RECOMMENDED` when it clears a margin, adds it to the queue if the model omitted it, and sequences a chaff flare after the fleet's last pickup. Turns "persuasion" into arithmetic. |
| `scorch.py`, `chat_schema.py`, `agency.py`, `packager.py`, `doctrine.py`, `prompt.py`, `orbit_policy.py` | EMP plumbing adopted from the kit's worked example (`emp_harvest_test`), then extended: **chaff** as a first-class option kind (`CHAFF_JAM`), `wait` and `chaff_flare` in the schema, `DOCTRINE_SHAPE` (the eight rules the verifier enforces, told to the model up front), `DOCTRINE_CHAFF_JAM`, and a **board-aware orbit gate** (buy an EMP when there is something to shoot at, chaff when a jam has a target, never on the eve of settlement). |
| `harness.py` | `run()` is an exception shield around `_run_inner()`: the harness never raises and always submits a legal queue. The dead Agents-API finisher is ported to `CortexChatInvoker`. Model dials are env-overridable (`SAGAR_COCO_THINK_MODEL`, default `claude-sonnet-4-6`). The verifier runs after the sanitiser and preserves committed ordnance; the tactical trigger and the early-salvo guard run last. |

## Results (all rungs, `--loadout empty`, the league configuration)

| Agent | Mean over 50 battles | Weapons fired | Fallbacks |
|---|---|---|---|
| Untouched V12 fork | 0.8535 | 0 | 0 |
| Shape planner alone (offline, no model) | 0.9800 | n/a | n/a |
| SAGAR_COCO v1 (model + verifier) | 0.9575 | 7 EMP, 1 chaff (siege only) | 0 |

The one board that did not reach 1.0 in any run is `blind_grab_rival_seam`: its
`denial_probe` predicate wants a probe on a rival probe the seat is never shown.

## Fair play

The verifier reads only `agent_view`. It never hydrates the session, never imports the
suite's boards or predicates, and never peeks through fog. The shape rules are written as
rules of good play derived from the rulebook, and the agent is told them in its prompt.

## Dials

| Env var | Default | Effect |
|---|---|---|
| `SAGAR_COCO_THINK_MODEL` | `claude-sonnet-4-6` | THINK model |
| `SAGAR_COCO_PLAN_MODEL` | same as THINK | PLAN model |
| `SAGAR_COCO_MOVER_MODEL` | same as THINK | mover and finisher model |
| `SAGAR_COCO_SINGLE` | `0` | `1` skips the THINK/PLAN split |
| `SOC_CARD_DUMP_DIR` | unset | write a debug card per turn (includes the verifier's verdict) |

Constants worth knowing: `verifier.LLM_VALUE_TOLERANCE` (0.85), `verifier.UNCONTESTED_MIN_TAIL`
(3), `tactics.RECOMMEND_MARGIN` (120 points).
