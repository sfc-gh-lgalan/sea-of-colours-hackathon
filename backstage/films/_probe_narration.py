"""Does the tutorial narration actually reach the room? (v1.48)

    SOC_BACKEND=memory python run_web.py --no-reload --port 8022 --replace
    python backstage/films/_probe_narration.py

Why a probe and not a test: every claim here is a browser fact. Whether
an engine admits to an audio track, whether ``play()`` was refused by
the autoplay policy, whether ``loop`` came off, whether any audio was
actually decoded — none of it is reachable from Python, and all of it
fails silently in a way a player experiences as "the sound is broken".

v1.48 rewrote what this asks. All twenty-one films are narrated now and
narration defaults ON, so the old contrast (chapter one narrated,
chapter two silent) no longer exists and "starts at narration: off" is
no longer the wanted answer. What matters now is the sequence a real
attendee walks into, because the modal AUTO-OPENS: there is usually no
user activation yet, the unmuted play() is refused through no fault of
the film, and the question is whether the player recovers gracefully or
quietly shows twenty-one narrated films in silence.

So, in order: it must fall back to a silent, still-playing film and say
what would fix it; the next ordinary click anywhere must turn the voice
on; the toggle must still turn it off; and that "off" must survive a
chapter change.

Deliberately NO ``--autoplay-policy`` override. The refusal is the
first thing under test, and a browser told never to refuse cannot
report it.
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
    """What the element is doing, including whether sound came out.

    ``bytes`` is the one that cannot be faked by flags: an element can
    be unmuted, playing and completely silent, and only the decoder
    counter tells them apart.
    """
    return pg.locator(VIDEO).first.evaluate(
        "(el) => ({ muted: el.muted, loop: el.loop, paused: el.paused,"
        " t: el.currentTime, vol: el.volume,"
        " src: (el.currentSrc || el.src || '').split('/').pop(),"
        " bytes: el.webkitAudioDecodedByteCount || 0,"
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


def _show(label: str, film: dict, btn: dict) -> None:
    print(f"{label:<12} film={film}")
    print(f"{'':<12} button={btn}")


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

        # ── on arrival: refused, but honest and still moving ──────────
        # Give stageFor's poll time to notice the audio track.
        pg.wait_for_timeout(1800)
        film, btn = _film(pg), _btn(pg)
        _show("on arrival", film, btn)

        if not film["audio"]:
            print(
                f"\n{film['src']} has no audio track, so there is nothing to\n"
                "toggle. Narrate it first:\n"
                "  python backstage/films/make_tutorial_films.py --only "
                "01_basic_probe --base " + base + " --voice\n"
                "  python backstage/films/voice/mux.py --film 01_basic_probe"
            )
            br.close()
            return 1

        if not btn["shown"]:
            fails.append("film has audio but the narration button stayed hidden")
        if film["paused"]:
            fails.append(
                "film is PAUSED on arrival — an unmuted autoplay was "
                "refused and nothing recovered it. This is the frozen-film "
                "failure: the player sees a still image, not a tutorial."
            )
        if not film["t"]:
            fails.append("clock never advanced — the film is not running")

        refused = bool(film["muted"])
        if refused:
            if btn.get("label") != "click to hear narration":
                fails.append(
                    f"muted, but the button says {btn.get('label')!r} — a "
                    f"film that cannot speak has to say what would fix it"
                )
            if not film["loop"]:
                fails.append("a fallen-back silent film should loop")
            print("  (autoplay refused, as expected without a gesture)")
        else:
            print("  (this browser allowed audible autoplay outright)")

        # ── the next ordinary click has to buy the voice ──────────────
        # NOT a click on the narration button. The modal auto-opens, so
        # most attendees never aim at that button; if only that button
        # works, the narration may as well not exist.
        if refused:
            pg.locator(f"{MODAL} h3").first.click()
            pg.wait_for_timeout(2500)
            film, btn = _film(pg), _btn(pg)
            _show("after click", film, btn)
            if film["muted"]:
                fails.append(
                    "still muted after an unrelated user gesture — an "
                    "auto-opened tutorial would stay silent for good"
                )
            if btn.get("label") != "narration: on":
                fails.append(f"button says {btn.get('label')!r}, want 'narration: on'")

        if film["loop"] and not film["muted"]:
            fails.append("loop is still on — the voice will restart mid-sentence")
        if film["paused"]:
            fails.append("film paused after unmuting")
        if not film["muted"] and not film["vol"]:
            fails.append("unmuted but volume is 0")
        if not film["bytes"]:
            fails.append(
                "no audio bytes decoded — unmuted and silent, which is what "
                "a missing or broken voice track looks like"
            )
        else:
            print(f"  decoded {film['bytes']} bytes of audio — sound is real")

        # ── the toggle still turns it off ─────────────────────────────
        pg.locator(BTN).first.click()
        pg.wait_for_timeout(1500)
        film, btn = _film(pg), _btn(pg)
        _show("toggled off", film, btn)
        if not film["muted"]:
            fails.append("toggle did not mute the film")
        if not film["loop"]:
            fails.append("a silenced film should go back to looping")
        if btn.get("label") != "narration: off":
            fails.append(f"button says {btn.get('label')!r}, want 'narration: off'")
        if btn.get("pressed") != "false":
            fails.append("aria-pressed did not follow the state")

        # ── and "off" has to survive a chapter change ─────────────────
        # Every film is narrated now, so the next chapter is a fresh
        # element that will try to speak unless the preference is read.
        pg.locator("[data-tut-next]").first.click()
        pg.wait_for_timeout(4200)
        film, btn = _film(pg), _btn(pg)
        _show("next chapter", film, btn)
        if not film["muted"]:
            fails.append(
                "narration came back on in the next chapter — 'off' is not "
                "being remembered, so silencing it lasts one film"
            )
        if btn.get("label") != "narration: off":
            fails.append(f"button says {btn.get('label')!r}, want 'narration: off'")

        # ── turning it back on works on a later chapter too ───────────
        pg.locator(BTN).first.click()
        pg.wait_for_timeout(2000)
        film, btn = _film(pg), _btn(pg)
        _show("back on", film, btn)
        if film["muted"]:
            fails.append("could not turn narration back on")
        if not film["bytes"]:
            fails.append("re-enabled narration decoded no audio")

        if errors:
            fails.append(f"page errors: {errors[:3]}")
        br.close()

    print()
    for f in fails:
        print("FAIL:", f)
    print("narration reaches the room" if not fails else "FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
