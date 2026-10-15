#!/usr/bin/env python3
"""What frame rate does Playwright actually hand us?

Every delivered clip is 25fps, and that number comes from ONE place:
`_camera_filter` writes `fps=25` into its `zoompan`, which forces the
output rate whatever the recording was. So "the clips are juddery" has
two possible causes and they need different fixes — either the browser
only ever gave us 25 frames a second, or it gave us more and the camera
pass has been throwing them away.

This answers that. It records a page whose only content is a box moving
at a known speed, and reports the rate of the RAW file, before anything
in `make_tutorial_films` touches it.

    python backstage/probes/_probe_record_fps.py
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from backstage.films.make_tutorial_films import VIEWPORT  # noqa: E402

#: Continuous motion, no easing, so a dropped frame is obvious and the
#: count is easy to reason about.
PAGE = """
<!doctype html><meta charset="utf-8">
<style>
  html, body { margin: 0; background: #000; overflow: hidden; }
  .b { position: absolute; top: 40%; width: 120px; height: 120px;
       background: #0f0; animation: run 2s linear infinite; }
  @keyframes run { from { left: 0 } to { left: 90vw } }
</style>
<div class="b"></div>
"""

#: Long enough that the second or so of browser and page setup — which
#: IS recorded, since the video starts when the context does — does not
#: move the answer. A four second run made it look like 31fps purely
#: because the setup was a quarter of the denominator.
SECONDS = 20


def probe(path: pathlib.Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-count_frames", "-show_entries",
         "stream=r_frame_rate,avg_frame_rate,nb_read_frames,width,height",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=300)
    return json.loads(out.stdout)["streams"][0]


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            # The clock has to start where the RECORDING starts, which
            # is when the context is created and not when the wait
            # begins — everything in between is in the file.
            t0 = time.monotonic()
            ctx = br.new_context(viewport=VIEWPORT, record_video_dir=tmp,
                                 record_video_size=VIEWPORT)
            pg = ctx.new_page()
            pg.set_content(PAGE)
            pg.wait_for_timeout(SECONDS * 1000)
            raw = pg.video.path()
            ctx.close()
            wall = time.monotonic() - t0
            br.close()

        st = probe(pathlib.Path(raw))
        frames = int(st.get("nb_read_frames") or 0)
        rate = frames / wall
        print(f"\n  raw recording: {st['width']}x{st['height']}, "
              f"container says {st['avg_frame_rate']}, "
              f"{frames} frames over {wall:.1f}s of wall clock")
        print(f"  -> about {rate:.0f} fps out of the browser")
        if rate > 30:
            print("\n  The browser gives more than 25. The camera pass is "
                  "discarding them — raise the fps it writes into zoompan.")
        else:
            print("\n  The browser gives ~25 and no amount of ffmpeg will "
                  "invent more. Smoother has to come from somewhere else.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
