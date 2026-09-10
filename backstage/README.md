# backstage — the browser and media kit (v1.47)

Everything in this repo that needs **a real browser, ffmpeg, or a speech
model** lives here and nowhere else.

The rule that gives the folder its name: **none of it is required to play
the game, run the test suite, fork an agent, run a season, or score a
league.** The attendee path is the five packages in the repo's own
`requirements.txt`. Playwright is a ~120MB browser download and a speech
model is another ~60MB; neither belongs on forty laptops at the start of
a day when nobody is going to shoot a film. So the day never installs
this, and `pytest` never imports it.

```bash
pip install -r backstage/requirements.txt
playwright install chromium
brew install ffmpeg          # films only; a binary, so not in the pip file
```

## What's in here

| | what | run it when |
|---|---|---|
| `films/` | the tutorial film rig — shoots the 21 teaching films and the landing hero loop by driving the real UI in a real browser | you changed something a film shows |
| `films/voice/` | neural voiceover for the films — scripts in, narration track out | you changed a film's captions |
| `probes/` | ~30 single-purpose browser harnesses (`_fx_*` geometry, `_probe_*` behaviour, `_ui_*` screenshots) | you touched the surface one of them watches |

Each folder has its own README; start there rather than here.

## Why a probe rather than a test

The suite in `tests/` covers what the engine computes. These cover what a
human would actually see, which is a different question and one that
pure-Python assertions cannot reach: whether a sprite lands on the right
cell, whether a caption is inside the shot, whether a static page loaded
over `file://` still renders, whether a dead tunnel produces the modal it
is supposed to. A page cannot report its own breakage — that is the whole
argument for keeping this kit, and for it being separate.

The trade is that probes need a browser, take seconds rather than
milliseconds, and several want a running server. That is exactly why they
are not in `pytest`: a suite everyone runs forty times a day has to stay
fast and dependency-free.

## The boundary runs one way

This folder reaches into the game, the server and `server/static/`.
**Nothing outside imports anything in here** — deleting `backstage/`
would cost you the ability to re-shoot films and re-run probes, and
nothing else. `pytest` passes without it.

Two consequences worth knowing. Some probes rely on test hooks in
`server/static/app.js` (search `Test hook for backstage/probes/`), so
renaming a probe orphans a comment — harmless, but fix it. And the films
themselves are **checked-in build output** under
`server/static/films/`: players get the `.webm`, never this rig.

## If you started a server for a probe

Most probes want one. Start your own, off port 8000, and remember the one
hard rule from AGENTS.md — whoever started it, owns it:

```bash
SOC_BACKEND=memory python run_web.py --no-reload --port 8022 --replace
```

## Provenance

This kit was scattered across `scripts/` until v1.47, which is why
`RULEBOOK.md` changelog entries cite paths like `scripts/_fx_orbit.py`.
Those are history and stay as written; the files are now in
`backstage/probes/`.
