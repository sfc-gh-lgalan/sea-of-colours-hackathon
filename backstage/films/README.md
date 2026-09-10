# backstage/films — the tutorial film rig

Everything that shoots, checks or stages the teaching films lives here and
nowhere else. Nothing in this folder is imported by the game, the server or
the agents; deleting it would cost you the ability to re-shoot, and nothing
else.

Four things the films need are deliberately **not** here, because they are
product rather than kit:

| lives at | what | why not here |
|---|---|---|
| `server/static/films/*.webm` | the films | build output the game serves to players |
| `server/static/media/hero.*` | the landing page's hero loop | build output the landing page serves |
| `server/static/tutorial.js` | the reels — titles, prose, which turn each plays on | source, and the modal that shows them |
| `sea_of_colours/game/tutorial.py` | the presets and the Advanced seed | engine |

The boundary runs one way: this folder reaches out to those three, nothing
reaches in. Every mention of `backstage/films/` elsewhere in the repo is prose —
AGENTS.md, the two docs, and docstrings in `tests/test_tutorial_mode.py`
pointing at the harness. No code outside imports anything here.

## Shoot

```bash
SOC_BACKEND=memory python run_web.py --no-reload --port 8022 --replace
python backstage/films/make_tutorial_films.py --base http://127.0.0.1:8022
python backstage/films/make_tutorial_films.py --list           # what there is
python backstage/films/make_tutorial_films.py --only basic_drop
FILM_CAM=1 python backstage/films/make_tutorial_films.py ...   # camera numbers
```

Point it at a server **you** started — see AGENTS.md, never at the user's.
The whole batch is about sixteen minutes.

The landing page's hero loop is shot by the same rig, against the same
server, and takes about ninety seconds:

```bash
python backstage/films/make_hero_reel.py --base http://127.0.0.1:8022
```

It is a different job from a teaching film and the differences are the
interesting part. It writes **no captions**; it composes the whole night
behind the curtain so the delivered loop starts at the first hour rather
than at twenty seconds of menu-clicking; and every shot is framed on the
BOARD rather than the viewport, because two thirds of a pulled-out frame
is the ORDERS panel and a UI panel behind a wordmark reads as clutter.
See `shot()` for why the camera CENTRE has to be clamped and not just its
corners. Output goes to `server/static/media/` and is tracked, so
`git checkout server/static/media/` is the revert.

## Watch

`screening.html` puts every film on one page with the card text that ships
beside it, so reviewing the reels does not mean playing three nights of two
tutorials to reach six of them. No server and no Python:

```bash
open backstage/films/screening.html
```

It builds its index from `window.socTutorial.reels` rather than a list of its
own, so it cannot drift from the reels, and it flags both directions of
mismatch: a reel naming a film that is not on disk, and a film in
`make_tutorial_films.py` that no reel shows anyone. `_fx_screening.py` checks
that the index fills and the first film plays.

Watching is still the check that matters, but there is one thing the eye is
bad at and a script is good at: noticing that a fast effect never rendered
at all. `ink_check.py` counts a weapon's colour frame by frame across a whole
film, so "the missile is subtle" and "there is no missile" stop looking alike:

```bash
python backstage/films/ink_check.py server/static/films/adv_emp.webm  emp
python backstage/films/ink_check.py server/static/films/adv_snap.webm snap
```

This is not paranoia. v1.36 shipped `adv_snap.webm` as a minute of narration
over a strike that measured 61 px at its peak against the EMP's 12,000 — a
film of nothing, passing every assertion the rig makes, because the rig
asserts that the launch *resolved* and cannot see whether it was *drawn*.

It is also how you tell a redraw apart from a regression. When SNAP's FX was
redrawn flat in v1.40, the same count said 268 px against the old glowing
cross's 323 — a picture that changed idiom without going quiet. Which is the
other reason a film is a fan-out surface: an FX change re-teaches whatever
film shows it. `adv_snap.webm` was re-shot for that redraw, because the
shipped one was a minute of the game explaining a visual it had stopped
drawing.

## What's here

| file | what it is |
|---|---|
| `make_tutorial_films.py` | the rig. One function per film, plus the `Film` verb kit |
| `make_hero_reel.py` | the landing page's hero loop — same rig, no captions, board-only framing |
| `_fx_tutorial.py` | end-to-end browser check of the tutorial modal |
| `ink_check.py` | counts a weapon's ink per frame — catches an FX that never drew |
| `screening.html` | watch all sixteen on one page, with their card text |
| `_fx_screening.py` | checks `screening.html` indexes every film and plays one |
| `_fx_landing_tut.py` | the landing page's three-card TUTORIAL chooser |
| `_probe_advseed.py` | picked `ADVANCED_TUTORIAL_SEED` against what the Advanced films need on the board |
| `_probe_advanced.py` | staged and debugged the Advanced storyline turn by turn |
| `_probe_crash.py` | worked out what the engine actually does on a collision |
| `_probe_redsign_card.py` | screenshots a reel's cards — the prose, which no film check reads |
| `_probe_adv_arc.py` | walks Advanced's four days and checks each orbit can afford the weapon its own reel tells you to buy |

The `_probe_*` scripts are one-shot investigations kept for the next person
who has to ask the same question. They are not maintained and may not run
against a much-changed engine — read them for the answer, not the code.

Two are worth reaching for again. `_probe_redsign_card.py`, because half of
each reel is prose in `tutorial.js`, the films check none of it, and pointing
it at another reel is a one-line edit. And `_probe_adv_arc.py`, because the
teaching subsidy is priced at one end and spent at a buy button at the other,
with a day number in between — retune any of the three and the way you find
out is an attendee stuck on a lesson they cannot pay for.

## Films carry their own day number

A film is a screen recording, so the game chrome inside it says `# day 3 ·
orbit` and `NOX 03` in the same font as the header above the modal. **Moving
a reel to a different day therefore means re-shooting its films**, and no
test can tell you otherwise — the evidence is inside the video. Adding
Advanced's fourth night caught three films this way; see issue 44.

Checking it is quick, and worth doing after any reel shuffle:

```bash
ffmpeg -ss 7 -i server/static/films/adv_emp.webm -frames:v 1 \
  -vf "crop=200:26:436:8,scale=800:-1:flags=neighbor" /tmp/hdr.png
```

## The script is the deliverable

The `.webm` files in `server/static/films/` are **generated build output that
is checked in**. They are never hand-recorded. That is the whole design: the
ORDERS and ORBIT panels move, and a hand-recorded clip of a moving UI is wrong
within a week and then stays wrong, because nobody re-records by hand.

Every film is a real session on the memory backend, driven with real clicks
through real selectors, so a film cannot drift from the product without this
script failing or the frames visibly changing.

**What it cannot check:** each film asserts that the orders it meant to give
actually landed, which catches a selector that stopped matching. It cannot
tell you the film *teaches* the right thing. The spike this grew from exited
PASS while demonstrating an illegal move and stranding a harvester. **Watch
every film before shipping it.**

## The camera is not in the browser

`push_in` measures a rectangle and banks a keyframe; the zoom is a crop
applied afterwards in ffmpeg. It used to be a CSS transform on the map
viewport and that was wrong in five separate ways —
`docs/TUTORIAL_PLAN.md` §12 has the full account, including why you cannot
fix the softness by shooting at 2x. Read it before changing the camera.

## Fan-out

A selector rename, a moved button or a reworded verb can silently turn a film
into a clip of the wrong thing. If you change the ORDERS panel, the fleet row,
the board context menu or the orbit buys: re-shoot, and **watch the result**.

- `docs/TUTORIAL_PLAN.md` — the state of play
- `server/static/tutorial.js` — the reel text and which turn each plays on
- `sea_of_colours/game/tutorial.py` — the presets and the Advanced seed
