"""sagar_cursor — clean fork of the frozen v11 champion (changes land in Section 2).

v12 begins as a vanilla, self-contained copy of v11 (identity rebranded only);
the planned work is a Section-2 (THE BOARD NOW) redesign. v11 stays frozen as the
fallback champion. Everything below is the inherited v11 thesis, verbatim.

--- inherited v11 thesis ---

tabula_v11 — the ASSIGNMENT-COMPREHENSION release (true fork of v10).

v10 completed its objectives: the deterministic compiler/packager (faithful
execution, no freelancing), walk-in reachability for echo/edge pures, and the
PLAN-vs-CORRECTOR-vs-EXECUTED reflection digest (grounded, corrector-aware
reflections). v10 won seed 69 vs the heuristic cleanly. v11 inherits all of that
and attacks the failure class v10's seed-69 diagnostics exposed.

The diagnosis: v10 fixed *execution* but not *assignment*. The agent has no
single, coherent model for "given everything I can see, what should each of my
≤3 harvesters do tonight?" — instead a pile of special-case pattern families and
a menu that only fires under narrow preconditions. Value provenance
(LIVE / ECHO / EXPECTED) and posture (who else can see it, weapons, tempo) are
computed implicitly and inconsistently across five modules, producing:
  * probe-starved dead turns (orbit builds a harvester → 0 probes → every
    probe-gated pattern silently vanishes → 0 moves);
  * echo-pure literalism (a harvester-discovered pure in ECHO offered nothing,
    even one cell from live and trivially walk-in-able);
  * dual-redsign confusion (mine + rival live at once, no way to express it).

v11's thesis: make value provenance + posture FIRST-CLASS inputs to ONE
assignment pass — the **Pyramid × Posture** model (see ``tabula_v11_PLAN.md``).

What v11 owns (phased):
  * Phase 1 — :mod:`.value_pyramid`: a ranked, provenance-tagged candidate list
    (LIVE/ECHO/EXPECTED × PURE/MASS/BLUE/FRONTIER) → default action (SMASH /
    WALK_IN / PROBE_GRAB / CHAIN / GRAB_BLUE / PROBE). Any LIVE pure force-
    surfaces a grab; any ECHO pure walkable from live force-surfaces a walk-in —
    regardless of redsign. Kills the dead-turn + echo-literalism failure modes.
  * Phase 2 — orbit ↔ tactics coupling: reserve a probe when reachable value
    needs one; the menu labels each play's probe cost.
  * Phase 3 — :mod:`.posture`: annotate each play with enemy-vision/weapons/
    tempo/ahead-behind reads that tune chain length, drop timing, and defensive
    riders (supersede, double-walk for EMP redundancy).
  * Phase 4 — grounded memories: keep v10's self-execution digest + a cross-day
    strategies memory (tried N / banked M / interdicted K) for on-the-job learning.

The deterministic packager (v10) is UNCHANGED — the pyramid only changes what the
thinker is OFFERED, not how it compiles. v6/v7/v8/v9/v10 stay frozen; v11's
reliance on shared modules (v7's compute layer) is by import only. The
native-thinking-Sonnet bet (bigger model + endpoint migration) is v12.
"""
