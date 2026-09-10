"""Did the weapon FX actually make it into the film?

The rig's own harness asserts OUTCOMES — the launch resolved, the probe
died, the drop was refused — and every one of those can be true while
the screen shows nothing at all. That is not hypothetical: v1.36 shipped
``adv_snap.webm`` as a minute of narration over a strike that was, on
the numbers, invisible. Nobody noticed, because a contact sheet at one
frame every two seconds cannot catch a third of a second of missile and
so quietly implies it was never there.

So count instead of sampling. Decodes a whole .webm at its real frame
rate and reports, per frame, how many pixels carry a weapon's ink.
Comparison is the point — run it against a film everyone agrees reads
well and you get a number to judge against::

    python backstage/films/ink_check.py server/static/films/adv_emp.webm  emp
    python backstage/films/ink_check.py server/static/films/adv_snap.webm snap

At the time of writing the EMP's cloud peaks around 12,000 px. The
SNAP's strike is one square rather than a radius-2 disk so it will never
approach that, but the first cut managed 61 px, and 61 px is the reading
this tool exists to catch.

Two things keep it honest. The seat gutters are cropped off by default,
because the platform diamonds are big blocks of seat colour that would
swamp anything board-sized (pass ``gutter`` when the station IS the
subject). And what matters is the DELTA over the film's own resting
level, not the raw count, since the UI chrome is never entirely black.
"""
from __future__ import annotations

import subprocess
import sys

import numpy as np

def amber(board):
    """SNAP ink at any alpha over the dark board.

    An exact RGB match is the wrong test: the trail is drawn ADDITIVELY
    (so a faint pixel is a dim amber, not #ffd166) and the landing flash
    composites at 0.88. Match the hue ratio instead — red leading,
    green a bit behind it, blue well back — which is what stays true as
    the ink fades.
    """
    r = board[:, :, 0]
    g = board[:, :, 1]
    b = board[:, :, 2]
    return ((r > 110) & (g > r * 0.60) & (g < r * 0.92) & (b < r * 0.55)).sum()


def cyan(board):
    """The EMP's ink, same reasoning — used only as a control, to show
    the detector finds a weapon FX that everyone agrees is visible."""
    r = board[:, :, 0]
    g = board[:, :, 1]
    b = board[:, :, 2]
    return ((b > 110) & (g > b * 0.60) & (r < b * 0.55)).sum()


DETECTORS = {"snap": amber, "emp": cyan}


def frames(path, w=480, h=270):
    """Decode to raw RGB at native frame rate, downscaled for speed."""
    cmd = [
        "ffmpeg", "-v", "error", "-i", path,
        "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    size = w * h * 3
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    proc.stdout.close()
    proc.wait()


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else \
        "server/static/films/adv_snap.webm"
    fps = float(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=avg_frame_rate", "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout.strip().split("/")[0]) / 1000.0
    fps = fps if fps > 1 else 25.0

    which = sys.argv[2] if len(sys.argv) > 2 else "snap"
    # "board" crops the seat gutters off, because the yellow platform
    # diamond out there would swamp a board-sized signal. "gutter" is the
    # opposite crop, and the only way to see the station's launch glyph:
    # it is drawn on the station's own canvas, invisible to the DOM.
    region = sys.argv[3] if len(sys.argv) > 3 else "board"
    detect = DETECTORS[which]
    counts = []
    for f in frames(path):
        if region == "board":
            cut = f[:, 90:390]
        elif region == "gutter":
            cut = f[:, 0:150]
        else:
            cut = f
        counts.append(int(detect(cut.astype(np.int16))))

    arr = np.array(counts)
    base = float(np.median(arr))
    print(f"{path}  [{which} ink, {region}]")
    print(f"  frames {len(arr)}  ~{len(arr)/fps:.1f}s at {fps:.1f}fps")
    print(f"  resting {which} level (median): {base:.0f} px")
    print(f"  peak: {arr.max()} px at frame {int(arr.argmax())} "
          f"(t={arr.argmax()/fps:.2f}s)")

    lively = np.where(arr > base + 12)[0]
    if not len(lively):
        print(f"  NOTHING above the resting level — no {which} visible anywhere.")
        return
    # Group neighbouring frames into events.
    runs, start = [], lively[0]
    for a, b in zip(lively, lively[1:]):
        if b - a > 4:
            runs.append((start, a))
            start = b
    runs.append((start, lively[-1]))
    print(f"  {len(runs)} amber event(s) above resting:")
    for s, e in runs:
        peak = arr[s:e + 1].max()
        print(f"    t={s/fps:6.2f}s .. {e/fps:6.2f}s  "
              f"({(e-s+1)/fps:.2f}s)  peak {peak} px")


if __name__ == "__main__":
    main()
