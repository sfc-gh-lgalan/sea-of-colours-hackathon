"""Lay narration onto a shot film (v1.47).

    python backstage/films/voice/mux.py --film basic_probe
    python backstage/films/voice/mux.py --film basic_probe --dry-run
    python backstage/films/voice/mux.py --all

Needs a cue sheet, which a shoot writes: ``make_tutorial_films.py``
records the instant each caption went up and drops
``voice/build/<film>.cues.json`` beside the film. So the order is always
shoot, then mux — muxing against a stale cue sheet is refused rather
than allowed to put the voice under the wrong pictures.

WHAT IT WILL AND WILL NOT DO
----------------------------
Each line starts exactly when its caption appears. That is the whole
point: caption and voice are the same sentence, so anything other than
"together" reads as a fault.

When a line is still talking as the next caption arrives, the choice is
between talking over the picture change or cutting the sentence off.
This delays the next line instead, and REPORTS it — a voice half a
second behind is survivable and self-correcting, a clipped sentence is
not. Delays that accumulate past ``DRIFT_LIMIT_S`` fail the mux, because
by then the narration is describing something that already happened and
the honest fix is a shorter caption, not a cleverer mixer.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from backstage.films.voice import narrate  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FILMS = ROOT / "server" / "static" / "films"
BUILD = HERE / "build"

#: How far behind its caption a line may drift before the mux is a
#: failure rather than a compromise. Half a second reads as natural
#: speech; two seconds reads as a broken video.
DRIFT_LIMIT_S = 2.0

#: Opus at 24kbps mono. These are one voice reading short sentences with
#: no music, which is the case Opus is best at — 19 minutes of it costs
#: ~3MB against 123MB of picture, so quality is not worth trading here.
BITRATE = "24k"


def cues(film: str) -> list[dict]:
    path = BUILD / f"{film}.cues.json"
    if not path.exists():
        sys.exit(
            f"no cue sheet for {film!r}\n"
            f"  shoot it first — the timings come from the shoot:\n"
            f"  python backstage/films/make_tutorial_films.py --only {film}"
        )
    return json.loads(path.read_text("utf-8"))["cues"]


def _pcm(path: pathlib.Path) -> tuple[bytes, int, int]:
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            sys.exit(f"{path.name}: expected 16-bit mono")
        return w.readframes(w.getnframes()), w.getframerate(), w.getsampwidth()


def build_track(film: str, voice: str, pad_to: float | None = None
                ) -> tuple[pathlib.Path, list[str], float]:
    """One wav the length of the film, with each line at its caption.

    Returns the track, any notes about lines that had to start late, and
    the moment the LAST WORD ends — which is not the track's length once
    it has been padded out to the picture, and is the only number that
    can tell you the narration overran.

    Assembled by splicing bytes rather than with an ffmpeg filter graph:
    a 23-line film would be a 23-input ``adelay``/``amix`` chain, which
    is both slower and far harder to reason about when a line lands in
    the wrong place. Silence in 16-bit PCM is just zero bytes.
    """
    notes: list[str] = []
    rows = cues(film)
    if not rows:
        sys.exit(f"{film}: cue sheet has no captions")

    clips = []
    rate = width = None
    for row in rows:
        path, secs = narrate.render(row["text"], voice)
        pcm, r, w = _pcm(path)
        rate, width = rate or r, width or w
        if r != rate:
            sys.exit("voice clips disagree on sample rate")
        clips.append((row["t"], secs, pcm, row["text"]))

    track = bytearray()
    cursor = 0.0  # seconds of audio written so far
    for want, secs, pcm, text in clips:
        if want < cursor:
            late = cursor - want
            notes.append(f"{late:+.2f}s late: {text}")
            if late > DRIFT_LIMIT_S:
                notes.append(
                    f"  ^ past the {DRIFT_LIMIT_S}s limit — shorten the "
                    f"caption before this one"
                )
        at = max(want, cursor)
        gap = int((at - cursor) * rate) * width
        track += b"\x00" * gap
        track += pcm
        cursor = at + secs

    # Pad out to the full length of the picture. Without this the mux
    # has to be told which stream wins, and `-shortest` picks the audio
    # — quietly cutting the film's closing beat off to match the last
    # word. A caller that trims two seconds of video every time it adds
    # sound is not a thing anyone would notice until the ending stopped
    # making sense.
    last_word = cursor
    if pad_to and pad_to > cursor:
        track += b"\x00" * (int((pad_to - cursor) * rate) * width)

    out = BUILD / f"{film}.voice.wav"
    BUILD.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(track))
    return out, notes, last_word


def mux(film: str, voice: str, dry: bool) -> bool:
    src = FILMS / f"{film}.webm"
    if not src.exists():
        sys.exit(f"{src.relative_to(ROOT)} is not there — shoot it first")
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not on PATH (brew install ffmpeg)")

    picture = narrate.film_seconds(film) or 0.0
    track, notes, spoken = build_track(film, voice, pad_to=picture)

    print(f"{film}: narration ends at {spoken:.1f}s of a {picture:.1f}s film")
    for n in notes:
        print(f"  {n}")
    over = [n for n in notes if "past the" in n]
    if spoken > picture + 0.5:
        print(f"  last line runs {spoken - picture:.1f}s past the end")
        over.append("longer than the film")
    if over:
        print("  NOT muxed — fix the captions and re-shoot")
        return False
    if dry:
        print(f"  dry run — track at {track.relative_to(ROOT)}")
        return True

    # Copy the video through untouched. Re-encoding it to add a sound
    # track would cost a generation of quality for nothing, and these
    # are already tuned VP8.
    tmp = src.with_suffix(".voiced.webm")
    # No `-shortest`: the track is already padded to the picture, and
    # letting ffmpeg pick the shorter stream is what silently trims the
    # film's last beat.
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(src), "-i", str(track),
        "-c:v", "copy", "-c:a", "libopus", "-b:a", BITRATE,
        "-map", "0:v:0", "-map", "1:a:0", str(tmp),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        tmp.unlink(missing_ok=True)
        err = getattr(exc, "stderr", b"") or b""
        tail = (err.decode(errors="replace").strip().splitlines() or [""])[-1]
        print(f"  ffmpeg failed: {tail[:120]}")
        return False

    before = src.stat().st_size
    src.unlink()
    tmp.rename(src)
    after = src.stat().st_size
    print(f"  muxed — {before/1024:.0f}KB to {after/1024:.0f}KB")
    return True


def _seconds(wav: pathlib.Path) -> float:
    with wave.open(str(wav), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--film")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--voice", default=narrate.DEFAULT_VOICE)
    ap.add_argument("--dry-run", action="store_true",
                    help="build the track and report, but leave the film alone")
    a = ap.parse_args()

    if a.all:
        names = sorted(p.name[: -len(".cues.json")]
                       for p in BUILD.glob("*.cues.json"))
        if not names:
            sys.exit("no cue sheets in voice/build/ — shoot some films first")
    elif a.film:
        names = [a.film]
    else:
        ap.print_help()
        return 2

    ok = all(mux(n, a.voice, a.dry_run) for n in names)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
