"""Neural narration for the teaching films (v1.47).

    python backstage/films/voice/narrate.py --list
    python backstage/films/voice/narrate.py --film basic_probe
    python backstage/films/voice/narrate.py --sample      # compare voices
    python backstage/films/voice/narrate.py --all         # every film

WHERE THE WORDS COME FROM
-------------------------
Nowhere in this folder. The narration is the **caption text already in
the shoot script**, lifted out of ``make_tutorial_films.py`` by reading
its AST.

That is the whole design and it is worth defending, because the obvious
alternative — a JSON file of narration lines — is a second copy of every
sentence in the tutorial. Copies drift. The failure it drifts into is
the nastiest kind: the caption says "right-click any square" while the
voice says something the UI stopped doing two versions ago, both halves
individually plausible, and the only witness is an attendee who now
trusts neither. Reading the shoot script means a reworded caption is a
reworded voice line, with no step in between that anyone can forget.

The cost is that a line can only be voiced if it is a literal in the
source. Anything computed at shoot time is reported as unvoiceable
rather than guessed at — see ``--list``.

WHY THE VIDEO WAITS FOR THE VOICE, NOT THE OTHER WAY ROUND
----------------------------------------------------------
Clips are rendered BEFORE a shoot so ``Film.say()`` can hold for the
real duration of the line it just put on screen. Sync then has one
source instead of two and cannot drift by construction.

Fitting voice into the gaps of an already-shot film is the alternative,
and it rots: every re-shoot moves the gaps a little, nothing detects it,
and the narration slides out from under the picture over a year of
unrelated edits.

The exception is the night films, whose captions fire when a given hour
reaches the screen rather than on a stopwatch — the sim owns that clock,
so a long line there overruns instead of stretching. ``--film`` flags
those lines so they can be shortened by hand.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import pathlib
import sys
import wave

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SHOOT = HERE.parent / "make_tutorial_films.py"
MODELS = HERE / "models"
CLIPS = HERE / "clips"

#: The shipped voice. ``high`` rather than ``medium`` because these lines
#: are short and declarative, which is exactly where a cheaper model's
#: flat prosody is most audible — it reads a five-word caption as a list
#: rather than a sentence.
DEFAULT_VOICE = "en_GB-cori-high"

#: Silence after a line before the film moves on. A caption that vanishes
#: on the voice's last syllable feels like an interruption; this is the
#: beat a human reader would leave.
TAIL_S = 0.45

# ── pulling the script out of the shoot ────────────────────────────────

def _films() -> dict[str, list[dict]]:
    """Every film, in shoot order, as its list of caption records.

    ``text`` is None for a ``say(None)`` clear and the sentinel
    ``UNVOICEABLE`` when the argument is not a literal. ``on_clock``
    marks a caption that fires after the night starts resolving, where
    the sim owns the pace and narration cannot stretch the picture to
    fit — derived from where ``f.praxis()`` sits in the source rather
    than listed by hand, so a film that grows a night is classified
    correctly without anyone remembering to come back here.
    """
    src = SHOOT.read_text("utf-8")
    tree = ast.parse(src)
    raw: dict[str, list[dict]] = {}

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        name = _film_name(node)
        if name is None:
            continue
        raw[name] = _captions(node)

    # A delivered film may be several shoots spliced end to end, so its
    # narration is theirs in order. Read from the shoot's own table for
    # the usual reason: a copy here would be one more thing to update.
    out: dict[str, list[dict]] = {}
    for name, parts in _splices(tree).items():
        if all(p in raw for p in parts):
            out[name] = [r for p in parts for r in raw[p]]
    spliced = {p for parts in _splices(tree).values() for p in parts}
    for name, rows in raw.items():
        if name not in spliced:
            out.setdefault(name, rows)
    return out


def _splices(tree: ast.Module) -> dict[str, list[str]]:
    """``SPLICES["adv_emp"] = ["adv_emp_rush", "adv_emp_wait"]``."""
    out: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if (
                isinstance(t, ast.Subscript)
                and isinstance(t.value, ast.Name)
                and t.value.id == "SPLICES"
                and isinstance(t.slice, ast.Constant)
                and isinstance(node.value, (ast.List, ast.Tuple))
            ):
                out[t.slice.value] = [
                    e.value for e in node.value.elts
                    if isinstance(e, ast.Constant)
                ]
    return out


def _film_name(fn: ast.FunctionDef) -> str | None:
    """The name in ``@film("basic_probe")``, if this is a film at all."""
    for dec in fn.decorator_list:
        if (
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Name)
            and dec.func.id == "film"
            and dec.args
            and isinstance(dec.args[0], ast.Constant)
            and isinstance(dec.args[0].value, str)
        ):
            return dec.args[0].value
    return None


UNVOICEABLE = "\x00unvoiceable"


def _captions(fn: ast.FunctionDef) -> list[dict]:
    """Caption records for one film, in the order the shoot says them.

    Ordered by source position rather than by ``ast.walk``, which is
    breadth-first and would interleave the branches of every ``if`` —
    and order is the one property narration cannot be wrong about.
    """
    calls: list[ast.Call] = []
    praxis_at: list[int] = []

    def visit(node) -> None:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "say":
                calls.append(node)
            elif node.func.attr == "praxis":
                praxis_at.append(node.lineno)
        for child in ast.iter_child_nodes(node):
            visit(child)

    for child in ast.iter_child_nodes(fn):
        visit(child)

    calls.sort(key=lambda c: (c.lineno, c.col_offset))
    first_praxis = min(praxis_at) if praxis_at else None

    rows = []
    for call in calls:
        text, hold = _line(call)
        rows.append({
            "text": text,
            "hold": hold,
            "on_clock": first_praxis is not None and call.lineno > first_praxis,
        })
    return rows


def _line(call: ast.Call) -> tuple[str | None, int | None]:
    if not call.args:
        return (None, None)
    arg = call.args[0]
    if isinstance(arg, ast.Constant):
        text = arg.value if isinstance(arg.value, str) else None
    else:
        text = UNVOICEABLE

    hold = None
    for kw in call.keywords:
        if kw.arg == "hold" and isinstance(kw.value, ast.Constant):
            hold = int(kw.value.value)
    return (text, hold)


# ── rendering ──────────────────────────────────────────────────────────

def _spoken(text: str) -> str:
    """The caption as it should be *heard*.

    Captions are typeset for the eye: an em dash is a visual pause and
    the UI's shouty nouns are emphasis, not spelling. Read literally,
    espeak says "PRAXIS" letter by letter and treats the dash as a word.
    """
    out = text.replace("\u2014", ",").replace("--", ",")
    return " ".join(out.split())


def _key(text: str, voice: str) -> str:
    """Cache key. Content-addressed, so re-rendering a film whose wording
    did not change costs nothing and a one-word edit re-renders one clip."""
    h = hashlib.sha1(f"{voice}\x1f{_spoken(text)}".encode()).hexdigest()[:16]
    return f"{voice}-{h}.wav"


def _duration(path: pathlib.Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


_LOADED: dict[str, object] = {}


def _voice(name: str):
    if name not in _LOADED:
        model = MODELS / f"{name}.onnx"
        if not model.exists():
            sys.exit(
                f"missing voice model {model.name}\n"
                f"  cd {MODELS.relative_to(ROOT)} && "
                f"python -m piper.download_voices {name}"
            )
        from piper import PiperVoice  # deferred: ~1s of onnxruntime import
        _LOADED[name] = PiperVoice.load(model)
    return _LOADED[name]


def render(text: str, voice: str = DEFAULT_VOICE) -> tuple[pathlib.Path, float]:
    """One caption to one wav. Returns (path, seconds)."""
    CLIPS.mkdir(parents=True, exist_ok=True)
    out = CLIPS / _key(text, voice)
    if not out.exists():
        with wave.open(str(out), "wb") as w:
            _voice(voice).synthesize_wav(_spoken(text), w)
    return out, _duration(out)


def plan(film: str, voice: str = DEFAULT_VOICE) -> list[dict]:
    """Render every line of one film and report what it costs in time.

    This is what the shoot consumes: ``hold`` is what the film currently
    waits, ``need`` is what the voice actually takes.
    """
    films = _films()
    if film not in films:
        sys.exit(f"no film {film!r} — try --list")

    rows = []
    for rec in films[film]:
        text = rec["text"]
        if text is None:
            continue
        if text == UNVOICEABLE:
            rows.append({**rec, "text": None, "unvoiceable": True})
            continue
        _, secs = render(text, voice)
        rows.append({
            **rec,
            "need": round(secs + TAIL_S, 2),
            "clip": _key(text, voice),
        })
    return rows


# ── CLI ────────────────────────────────────────────────────────────────

def _cmd_list() -> None:
    films = _films()

    def _sayable(rows):
        return [r for r in rows if r["text"] and r["text"] != UNVOICEABLE]

    total = sum(len(_sayable(r)) for r in films.values())
    print(f"{len(films)} delivered films, {total} voiceable lines\n")
    for name, rows in films.items():
        said = _sayable(rows)
        onclock = sum(1 for r in said if r["on_clock"])
        flag = f"  ({onclock} on the sim's clock)" if onclock else ""
        print(f"  {name:22} {len(said):3} lines{flag}")

    stuck = {
        n: sum(1 for r in rows if r["text"] == UNVOICEABLE)
        for n, rows in films.items()
    }
    if any(stuck.values()):
        print("\nnot literals in the source, so not voiceable:")
        for n, c in stuck.items():
            if c:
                print(f"  {n}: {c}")


def _cmd_film(name: str, voice: str, as_json: bool) -> None:
    rows = plan(name, voice)
    if as_json:
        print(json.dumps({"film": name, "voice": voice, "lines": rows}, indent=2))
        return

    total_need = 0.0
    on_clock = 0
    print(f"{name}  ({voice})\n")
    for r in rows:
        if r.get("unvoiceable"):
            print("      [not a literal — cannot voice]")
            continue
        hold = (r["hold"] or 0) / 1000.0
        total_need += r["need"]
        on_clock += bool(r["on_clock"])
        # `hold` is the shoot's EXPLICIT wait, not the line's real beat:
        # most captions are followed by clicks, hovers and animations
        # that give the voice room without any hold at all. So this is
        # shown for reference and never compared against — see the note
        # under the totals.
        mark = "~" if r["on_clock"] else " "
        print(f" {mark} {r['need']:5.2f}s  (hold {hold:4.2f}s)  {r['text']}")

    shot = film_seconds(name)
    print(f"\n  narration {total_need:.1f}s", end="")
    if shot:
        spare = shot - total_need
        verdict = (
            f"fits, {spare:.1f}s spare" if spare >= 0
            else f"OVER by {-spare:.1f}s"
        )
        print(f" against the shot film's {shot:.1f}s — {verdict}")
    else:
        print(" (film not shot yet, so nothing to compare)")

    if on_clock:
        print(
            f"  ~ {on_clock} line(s) fire when an hour reaches the screen, so "
            f"the sim owns\n    their pace and narration cannot stretch them."
        )
    print(
        "  Totals only bound the whole film. Whether each LINE fits its own\n"
        "  beat needs real caption timings, which only a shoot can measure\n"
        "  — that is what --manifest is for."
    )


def film_seconds(name: str) -> float | None:
    """How long the delivered film actually runs, via ffprobe.

    Compared against instead of the sum of holds because holds are a
    small part of a film's length — basic_probe holds for 5s and runs
    for 23s. Judging narration against holds would condemn films that
    have room to spare.
    """
    import shutil
    import subprocess

    webm = ROOT / "server" / "static" / "films" / f"{name}.webm"
    if not webm.exists() or not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(webm)],
            capture_output=True, text=True, timeout=20, check=True,
        )
        return float(out.stdout.strip())
    except Exception:
        return None


def _cmd_sample() -> None:
    line = "Probes are how you look. The whole board is fog \u2014 you cannot see the RED."
    print(f"line: {line}\n")
    for v in sorted(p.stem for p in MODELS.glob("*.onnx")):
        path, secs = render(line, v)
        print(f"  {secs:5.2f}s  {v:38} {path}")
    print("\n  afplay <path>   # to hear one")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true", help="films and line counts")
    ap.add_argument("--film", help="render one film's narration")
    ap.add_argument("--all", action="store_true", help="render every film")
    ap.add_argument("--sample", action="store_true", help="one line, every voice")
    ap.add_argument("--voice", default=DEFAULT_VOICE)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.list:
        _cmd_list()
    elif a.sample:
        _cmd_sample()
    elif a.film:
        _cmd_film(a.film, a.voice, a.json)
    elif a.all:
        for name in _films():
            _cmd_film(name, a.voice, a.json)
            print()
    else:
        ap.print_help()
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
