"""Does the narration toggle actually work? (v1.47)

    SOC_BACKEND=memory python run_web.py --no-reload --port 8022 --replace
    python backstage/films/_probe_narration.py

Needs `basic_probe.webm` to be a NARRATED film — shoot it with
``--voice`` and mux it first, or this reports the control as absent,
which is the correct answer for a silent one.

Why a probe and not a test: every claim here is a browser fact. Whether
an engine admits to an audio track, whether ``play()`` is refused by the
autoplay policy, whether ``loop`` came off — none of it is reachable
from Python, and all of it fails silently in a way a player experiences
as "the sound button does nothing".

``basic:planning:1`` is used because its two chapters are exactly the
contrast that matters: chapter one is narrated, chapter two is not, so
the control has to appear and then disappear.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

MODAL = ".soc-tut"
BTN = "[data-tut-narrate]"
VIDEO = ".soc-tut-video"


def _api(base: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{base}{path}", data=data,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _film(pg) -> dict:
    return pg.locator(VIDEO).first.evaluate(
        "(el) => ({ muted: el.muted, loop: el.loop, paused: el.paused,"
        " t: el.currentTime, d: el.duration,"
        " audio: (typeof el.webkitAudioDecodedByteCount === 'number'"
        "   ? el.webkitAudioDecodedByteCount > 0"
        "   : (typeof el.mozHasAudio === 'boolean' ? el.mozHasAudio : null)) })"
    )


def _btn(pg) -> dict:
    b = pg.locator(BTN).first
    if b.count() == 0:
        return {"there": False}
    return {
        "there": True,
        "shown": b.is_visible(),
        "label": (b.inner_text() or "").strip(),
        "pressed": b.get_attribute("aria-pressed"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8022)
    ap.add_argument("--seed", type=int, default=90210)
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    fails: list[str] = []

    sid = _api(base, "/api/game/new",
               {"tutorial": "basic", "seed": args.seed})["session_id"]

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        # NO --autoplay-policy override. The point of this probe is the
        # policy a player's browser actually has, and a flag that lets
        # audio autoplay would test a browser nobody is using.
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1600, "height": 1000})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(f"{base}/play?session={sid}&player=p1", wait_until="networkidle")
        pg.wait_for_timeout(4000)

        if pg.locator(MODAL).first.is_hidden():
            print("FAIL: tutorial modal did not open")
            br.close()
            return 1

        # ── chapter one: the narrated film ───────────────────────────
        # Give the poll in stageFor time to notice the audio track.
        pg.wait_for_timeout(1800)
        film = _film(pg)
        btn = _btn(pg)
        print(f"chapter 1  film={film}")
        print(f"           button={btn}")

        if not film["audio"]:
            print(
                "\nbasic_probe.webm has no audio track, so there is nothing\n"
                "to toggle. Narrate it first:\n"
                "  python backstage/films/make_tutorial_films.py --only "
                "basic_probe --base " + base + " --voice\n"
                "  python backstage/films/voice/mux.py --film basic_probe"
            )
            br.close()
            return 1

        if not btn["shown"]:
            fails.append("film has audio but the narration button stayed hidden")
        if btn.get("label") != "narration: off":
            fails.append(f"button starts at {btn.get('label')!r}, want 'narration: off'")
        if not film["muted"]:
            fails.append("film did not start muted — autoplay would be refused")
        if not film["loop"]:
            fails.append("a silent-by-default film should loop")

        # ── turn it on ───────────────────────────────────────────────
        pg.locator(BTN).first.click()
        pg.wait_for_timeout(1200)
        film = _film(pg)
        btn = _btn(pg)
        print(f"after click film={film}")
        print(f"           button={btn}")

        if btn.get("label") == "click to hear narration":
            fails.append("play() was refused even though the click was a gesture")
        elif btn.get("label") != "narration: on":
            fails.append(f"button says {btn.get('label')!r}, want 'narration: on'")
        if film["muted"]:
            fails.append("still muted after turning narration on")
        if film["loop"]:
            fails.append("loop is still on — the voice will restart mid-sentence")
        if film["paused"]:
            fails.append("film paused after unmuting")
        if btn.get("pressed") != "true":
            fails.append("aria-pressed did not follow the state")

        # ── chapter two: a silent film ───────────────────────────────
        # The control is a fact about the film, not the modal, so it has
        # to go away again — offering narration on a silent chapter is
        # how a player concludes the feature is broken.
        pg.locator("[data-tut-next]").first.click()
        pg.wait_for_timeout(4200)
        btn = _btn(pg)
        film = _film(pg)
        print(f"chapter 2  film={film}")
        print(f"           button={btn}")
        if btn.get("shown"):
            fails.append("narration button still showing on a silent film")
        if not film["loop"]:
            fails.append("silent film should be back to looping")

        # ── and back, to prove the preference stuck ───────────────────
        pg.locator("[data-tut-prev]").first.click()
        pg.wait_for_timeout(2600)
        film = _film(pg)
        btn = _btn(pg)
        print(f"back to 1  film={film}")
        print(f"           button={btn}")
        if film["muted"] or film["loop"]:
            fails.append(
                "narration did not re-apply on return — the remembered "
                "preference is not being honoured"
            )

        if errors:
            fails.append(f"page errors: {errors[:3]}")
        br.close()

    for f in fails:
        print("FAIL:", f)
    print("\nnarration toggle works" if not fails else "\nFAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
