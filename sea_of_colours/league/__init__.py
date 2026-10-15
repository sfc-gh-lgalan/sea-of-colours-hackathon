"""The league (v1.50) — 1v1 best-of-three between the day's agents.

Distinct from ``evals/battles``'s ``soc league``, which ranks agents by
their average pass rate over constructed boards. That answers "does this
fork handle a hard night correctly", which is a coaching question. This
answers the only question a competition can settle: **who beat whom**,
over whole seasons, head to head.

Five pieces, each usable on its own:

``roster``   who is actually competing, as an editable file rather than
             a scan of the tree.
``fixtures`` the shape of the competition — a straight knockout, with
             a play-in for whatever a field overflows its bracket by —
             and the best-of-three rule, kept pure so it can be tested
             without playing anything.
``runner``   plays matches, several at once, resumably.
``stats``    reads finished games back for weapons and kills. Nothing is
             captured at match time, so any past game can be re-read.
``site``     assembles the above into the data the ``/league/`` page
             draws.

The layering runs one way — ``fixtures`` knows nothing about the engine,
``stats`` knows nothing about fixtures — so the competition format can
change without touching the reader, and vice versa.
"""

from __future__ import annotations

from pathlib import Path

#: Everything the league generates. Deliberately under ``reports/`` and
#: never in ``league/`` beside the page: the page is source, this is
#: output, and a collection that writes into its own source directory
#: makes "is this checked in?" unanswerable.
ROOT = Path("reports/league")
