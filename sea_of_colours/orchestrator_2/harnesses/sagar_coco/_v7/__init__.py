"""Inherited internals — the V7 generation, vendored into V12.

**This is not a separate agent.** It used to be
``harnesses/tabula_v7/``, a sibling harness in a long R&D lineage
(``tabula`` → ``v2`` … → ``v12``). When the hackathon distribution
trimmed the roster to one LLM agent, V12 turned out to import **all 15**
of v7's modules — including ``harness`` itself — so v7 could not simply
be deleted. Rather than leave V12 depending on a "retired" sibling, the
package moved in here and everything else in the lineage was removed.

It lives in a subpackage instead of being merged flat because four
module names collide with V12's own (``chat_schema``, ``harness``,
``prompt``, ``rules``) and in each case the V12 version *imports* this
one — they are layers, not duplicates.

**If you're forking V12 for the hackathon:** start with the modules in
``sagar_coco/`` proper. This directory is the stable substrate beneath
them (probe hints, move sanitising, validators, prompt block
formatters, the memory/recorder loop). You can change it, but most
agent improvements belong a layer up.
"""
