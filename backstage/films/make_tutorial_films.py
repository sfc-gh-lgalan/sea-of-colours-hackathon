#!/usr/bin/env python3
"""Generate the tutorial films by driving the real UI.

THE SCRIPT IS THE DELIVERABLE. The ``.webm`` files it writes are build
output, ignored by git, and re-shot by running this again. That is the
whole design: the ORDERS and ORBIT panels move, and a hand-recorded clip
of a moving UI is wrong within a week and then stays wrong, because
nobody re-records by hand. Re-running a script after a redesign costs a
minute.

Every film is a REAL session on the memory backend, driven with real
clicks through real selectors. There is no mock-up and no storyboard, so
a film cannot drift from the product without this script failing or the
frames visibly changing.

Usage::

    SOC_BACKEND=memory python run_web.py --no-reload --port 8022 --replace
    python backstage/films/make_tutorial_films.py --base http://127.0.0.1:8022
    python backstage/films/make_tutorial_films.py --only basic_probe

Point it at a server YOU started. See AGENTS.md — never at the user's.

WHAT THIS CANNOT CHECK. Each film asserts that the orders it meant to
give actually landed, which catches a selector that stopped matching.
It cannot tell you the film TEACHES the right thing. The spike this grew
from exited PASS while demonstrating an illegal move and stranding a
harvester. Watch every film before shipping it.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sea_of_colours.game import tutorial as soc_tutorial  # noqa: E402

OUT = ROOT / "server" / "static" / "films"

VIEWPORT = {"width": 1280, "height": 800}
# DO NOT try to shoot at 2x for a sharper zoom. It looks like the
# obvious win — the camera crops into the finished frame, so more pixels
# in means more detail when magnified — and it does not work:
# Playwright's screencast is captured at CSS resolution, and
# `record_video_size` only ever scales DOWN to fit. Ask for 2560x1600
# with `device_scale_factor=2` and you get a 2560x1600 canvas with the
# 1280x800 page pasted in the corner and mid-grey over the other three
# quarters, which then gets cropped into by a camera that thinks it has
# twice the resolution it has. Cost an hour; the frames were unmistakable.
# Raising the VIEWPORT instead would work, but it is not the same film:
# the app lays itself out against the window, so a 2560-wide viewport
# shows a differently proportioned product to the one attendees run.
# The zoom softens the picture. That is the price of the trade.

# ── the Advanced board ──────────────────────────────────────────────
#
# Advanced teaches SIGNS and WEAPONS, and neither can be taught on a
# board that does not happen to have them. Unlike the Basic films —
# which work on any board and so ride whatever ``--seed`` the batch is
# running under — these pin one seed, picked by
# ``backstage/films/_probe_advseed.py`` against three things the generator only
# sometimes delivers on a 24x16:
#
#   * exactly ONE bright blue smear, so "that glow is a blue pocket"
#     points at one thing rather than three;
#   * TWO pure-255 jackpots far apart, so each House can light its own
#     and the contested-jackpot lesson has two sides;
#   * all of it inset from the edge, because a probe is a radius-4 disk
#     and a close-up on column 0 is a close-up of the bezel.
#
# The blue is also the arc: the weapons cost 100 / 200 / 300 against a
# 250 stipend, so the hot drop on night one is literally what pays for
# the weapons in orbit. Change the seed and that stops being true.
# (The training range tops the last two up — see ``blue_grant_for`` —
# but only enough to make the lesson buyable, never enough to make the
# mining pointless.)
#
# It comes FROM the preset rather than being restated here, because the
# Advanced preset pins the same board for the player. That is the whole
# point of pinning it: the blue smear in the film is the blue smear on
# their map. A second copy of the number here could drift, and the way
# it would show up is a tutorial whose films quietly describe somewhere
# else — so there is only the one copy, and it lives with the mode.
ADVANCED_SEED = soc_tutorial.ADVANCED_TUTORIAL_SEED
ADV_SIGN = (12, 6)        # brightest bluesign cell — intensity 0.98
ADV_BLUE_LAND = (12, 4)   # BLUE 255 beneath the smear: the hot-drop prize
ADV_BLUE_STEP = (12, 5)   # BLUE 147, the second bite
ADV_MINE = (19, 5)        # our jackpot, sitting in a rich seam
ADV_THEIRS = (4, 12)      # theirs, right across the board
# Somewhere for the rival's early probes that lights nothing. Anything
# within radius 4 of a jackpot mints its beacon a night early and steals
# the only beat the redsign films have.
ADV_QUIET = (20, 12)
ADV_QUIET2 = (8, 2)
# Three radius-2 diamonds in a row overlap into one wall instead of
# three puddles — the "interwoven" pattern, centred on the rival's
# jackpot so the salvo takes their eye off it.
ADV_SALVO = [(2, 12), (4, 12), (6, 12)]
ADV_BESIDE = (8, 11)      # clear of every diamond, one square outside

# ── the tactical films (v1.37) ──────────────────────────────────────
# Every square below was picked off _probe_advboard.py against the real
# grid, not off a screenshot, and the arithmetic in the captions is that
# script's numbers. If the seed ever moves, re-run it — a comb that
# reads "five squares of ordinary ground" and walks four empty ones is
# a film that teaches the opposite of its caption.

#: The six-parcel comb out of ADV_MINE: R239 R7 R240 R166 R77 behind the
#: jackpot. Worth 1815 over seven hours, which is the offer smash-and-grab
#: turns down. Never walked on camera — it is priced, then rejected.
ADV_GREEDY = [(20, 5), (21, 5), (22, 5), (22, 4), (23, 4)]

#: Blind and grab. The salvo sits one row BELOW the rival's jackpot so it
#: catches their probe at (4,12) on the diamond's top point while leaving
#: the northern approach clear. Friendly fire is on and a cloud over the
#: comb would deny the harvest on every square of it (§4.9.3), so the
#: blast and the walk must not overlap — the first cut of this had the
#: salvo centred on the seam and banked nothing at all.
ADV_BLIND_SALVO = [(2, 14), (4, 14), (6, 14)]
ADV_BLIND_EYE = (2, 11)     # our probe: lights the comb, clear of the blast
ADV_BLIND_LAND = (3, 12)    # RED 220 — picked off the smear, before we can see
ADV_BLIND_COMB = [(3, 11), (4, 11), (4, 10), (5, 10), (6, 10)]

#: The EMP walk-in. ADV_BESIDE is the one square that is outside all
#: three diamonds and adjacent to one, and the best walk out of it ends
#: on the rival's jackpot — so the salvo denies them the seam for eight
#: hours and the same salvo's expiry is the clock we arrive on.
ADV_WALK_IN = [(7, 11), (7, 12), (6, 12), (5, 12), (4, 12)]

# Two things off the board that the camera now needs to reach. Named
# here rather than inlined because they are product selectors: if the
# station rail is rebuilt these are what break, and one grep should
# find them.
CATAPULT = "#os-catapult"
#: The hover readout. Framed WITH its square on the wreck close-ups —
#: a cross on its own does not say what happened there.
TOOLTIP = ".cell-tooltip"
#: The station glyph with the score and the pending "+X" beneath it.
#: The readout alone is a 17px number that fills a seventh of the frame
#: even wide open; anchoring it to the platform it hangs off gives the
#: shot something to be a close-up OF, and matches how the beat is
#: described — the number under the station.
SCOREBOARD = ('[data-os-vault="p1"]', '[data-os-score="p1"]',
              '[data-os-score-delta="p1"]')


# ── API helpers ─────────────────────────────────────────────────────

def _req(base: str, path: str, body: Optional[dict] = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path, data=data,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def seed_game(base: str, seed: int, preset: str = "basic") -> str:
    """A tutorial game — the exact board the films teach on.

    Posts the preset NAME so the film is shot on whatever
    ``game/tutorial.py`` currently says that mode is. Restating 24x16
    here would let the films quietly diverge from the mode.
    """
    game = _req(base, "/api/game/new", {"tutorial": preset, "seed": seed})
    return str(game["session_id"])


def seed_duel(base: str, seed: int, cap: int = 5,
              preset: str = "basic") -> str:
    """A preset-LOOKING board with both seats human.

    The outcome films — a crash, a stranding, a shipment — have to show
    a specific thing happen, and the Basic preset hands the other seat
    to a bot that will not take direction. Waiting for the bot to
    volunteer a collision is not a plan; a film that "usually" shows the
    lesson is a film that sometimes ships showing nothing.

    So both seats are human here and the rival's night is posted over
    HTTP before the camera rolls. Everything else is copied from the
    preset (via the module, not by restating 24x16) so the board the
    attendee learns on is the board they then play.
    """
    cfg = soc_tutorial.preset_config(preset) or {}
    game = _req(base, "/api/game/new", {
        "players": ["p1", "p2"],
        "agents": {"p1": "human", "p2": "human"},
        "width": int(cfg.get("width", 24)),
        "height": int(cfg.get("height", 16)),
        # Longer than Basic's three nights: these films need two nights
        # of build-up before the night that carries the lesson, and a
        # season-end card landing mid-film would upstage it.
        "season_day_cap": cap,
        "weapons_enabled": bool(cfg.get("weapons_enabled", False)),
        "signs_enabled": bool(cfg.get("signs_enabled", False)),
        "backend": "memory",
        "seed": seed,
    })
    return str(game["session_id"])


def submit_night(base: str, sid: str, moves: List[dict],
                 player: str = "p1") -> dict:
    return _req(base, f"/api/game/{sid}/policy",
                {"player": player, "moves": moves})


def submit_orbit(base: str, sid: str, actions: List[dict],
                 player: str = "p1") -> dict:
    return _req(base, f"/api/game/{sid}/orbit",
                {"player": player, "actions": actions})


def status(base: str, sid: str) -> dict:
    return _req(base, f"/api/game/{sid}/status")


def view(base: str, sid: str, player: str = "p1") -> dict:
    return _req(base, f"/api/game/{sid}/view?player={player}")


def live_squares(base: str, sid: str, player: str = "p1") -> Dict[tuple, dict]:
    """Squares a seat can land on right now, keyed ``(x, y)``.

    ``drop_mode`` is ``live_only``, so a stale echo is not a landing
    site. Setup picks its coordinates from this rather than from the
    rendered board, because setup runs before the page exists.
    """
    v = view(base, sid, player)
    w = int(v["width"])
    return {
        (i % w, i // w): c
        for i, c in enumerate(v["cells"])
        if c.get("kind") == "terrain" and not c.get("stale")
    }


def harvester_ids(base: str, sid: str, player: str = "p1") -> List[str]:
    v = view(base, sid, player)
    return [str(u["id"]) for u in v["units"] if u.get("type") == "harvester"]


# ── the film kit ────────────────────────────────────────────────────
#
# A cursor and a caption bar injected into the page. Both are plain DOM,
# so the recorder picks them up for free.
#
# The cursor is not decoration. Playwright paints no pointer into its
# video, and without one the UI appears to operate itself — which teaches
# nothing about WHERE TO CLICK, the single thing a first-timer is stuck
# on. The glide runs in JS on requestAnimationFrame rather than as a
# Python loop of evaluate() calls: 30 round-trips per move is jerky and
# slow, and the frame timing is what makes the motion read as a hand.

_KIT = """
() => {
  if (document.getElementById('film-cursor')) return;

  const c = document.createElement('div');
  c.id = 'film-cursor';
  c.style.cssText = [
    'position:fixed', 'left:0', 'top:0', 'width:26px', 'height:26px',
    'margin:-13px 0 0 -13px', 'border:3px solid #aaff00',
    'border-radius:50%', 'background:rgba(170,255,0,0.18)',
    'box-shadow:0 0 12px rgba(170,255,0,0.7)',
    'z-index:2147483647', 'pointer-events:none',
    'transition:transform 90ms ease-out, background 90ms ease-out',
  ].join(';');
  document.body.appendChild(c);

  const cap = document.createElement('div');
  cap.id = 'film-caption';
  cap.style.cssText = [
    'position:fixed', 'left:50%', 'bottom:28px', 'transform:translateX(-50%)',
    // Sized for the playback surface, not the capture: the modal shows
    // these at ~0.9x, and a caption set at the game's own 13px arrives
    // unreadable.
    'padding:12px 26px', 'background:rgba(0,0,0,0.88)',
    'border:1px solid #446600', 'color:#aaff00',
    'font:20px/1.35 ui-monospace,SFMono-Regular,Menlo,monospace',
    'letter-spacing:1.4px', 'text-transform:uppercase',
    'z-index:2147483647', 'pointer-events:none',
    'opacity:0', 'transition:opacity 260ms ease',
    'max-width:70vw', 'text-align:center',
  ].join(';');
  document.body.appendChild(cap);

  // The crop the camera is currently holding, in page pixels, or null
  // for the whole frame. The caption has to live INSIDE it: the camera
  // is a crop over the finished frame, so a caption pinned to the
  // bottom of the page is simply not in the picture during a close-up.
  // That is not theoretical — the first cut of the post-crop camera
  // shipped every close-up silently uncaptioned.
  let camWin = null;

  function placeCap() {
    const win = camWin || {
      x: 0, y: 0, w: window.innerWidth, h: window.innerHeight, z: 1,
    };
    // Shrink with the zoom so the caption reads at roughly its usual
    // size once the crop is blown back up — but never below the game's
    // own 13px, because text rendered smaller than that is mush no
    // amount of magnification recovers.
    const s = Math.max(1 / win.z, 0.65);
    cap.style.transformOrigin = '0 0';
    cap.style.transform = 'scale(' + s + ')';
    cap.style.bottom = 'auto';
    cap.style.maxWidth = Math.round(win.w * 0.88 / s) + 'px';
    // Reads the post-transform box, so this is the size it occupies on
    // screen rather than its layout size.
    const r = cap.getBoundingClientRect();
    cap.style.left = Math.round(win.x + (win.w - r.width) / 2) + 'px';
    cap.style.top = Math.round(win.y + win.h - r.height - 20 * s) + 'px';
  }

  window.__film = {
    cursor: c,
    caption: cap,
    at: { x: 0, y: 0 },
    say(text) {
      if (!text) { cap.style.opacity = '0'; return; }
      cap.textContent = text;
      placeCap();
      cap.style.opacity = '1';
    },
    frame(win) { camWin = win; placeCap(); },
    capBox() {
      const r = cap.getBoundingClientRect();
      return {x: r.left, y: r.top, w: r.width, h: r.height,
              lit: cap.style.opacity === '1'};
    },
    put(x, y) {
      c.style.left = x + 'px';
      c.style.top = y + 'px';
      this.at = { x, y };
    },
    glide(x, y, ms) {
      const from = { ...this.at };
      const t0 = performance.now();
      // easeInOutQuad — accelerate away, settle onto the target. A
      // linear glide reads as a machine even at the right duration.
      const ease = (t) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);
      return new Promise((res) => {
        const step = (now) => {
          const t = Math.min(1, (now - t0) / ms);
          const e = ease(t);
          this.put(from.x + (x - from.x) * e, from.y + (y - from.y) * e);
          if (t < 1) requestAnimationFrame(step);
          else res();
        };
        requestAnimationFrame(step);
      });
    },
    press(right) {
      c.style.transform = 'scale(0.6)';
      c.style.background = right
        ? 'rgba(255,180,0,0.55)' : 'rgba(170,255,0,0.55)';
      return new Promise((res) => setTimeout(() => {
        c.style.transform = 'scale(1)';
        c.style.background = 'rgba(170,255,0,0.18)';
        res();
      }, 150));
    },
  };
}
"""

# Runs at document start, before any app script. Two jobs, both of which
# have to happen BEFORE the first paint or they end up on film: mute the
# tutorial modal (these films play inside it — it must never film itself),
# and drop a black curtain over the boot. Without the curtain the first
# second of every film is a white flash and a half-built UI.
_CURTAIN_INIT = """
(() => {
  try { localStorage.setItem('soc.tutorial.muted.v1', '1'); } catch (e) {}
  const paint = () => {
    // At true document start there is no documentElement yet on the
    // first frame; retry rather than throw a page error.
    const host = document.body || document.documentElement;
    if (!host) { requestAnimationFrame(paint); return; }
    if (document.getElementById('film-curtain')) return;
    const d = document.createElement('div');
    d.id = 'film-curtain';
    d.style.cssText = [
      'position:fixed', 'inset:0', 'background:#04060a',
      'z-index:2147483646', 'pointer-events:none',
      'opacity:1', 'transition:opacity 420ms ease',
    ].join(';');
    host.appendChild(d);
  };
  paint();
  document.addEventListener('DOMContentLoaded', paint);
})();
"""

# ── the camera ──────────────────────────────────────────────────────
#
# The camera does not touch the page. It MEASURES.
#
# It used to be a CSS transform on `.cc-map-viewport`, and every fault
# in the first cut of these films came from that one decision. Scaling
# one element inside a fixed layout is not a camera move: the two
# station platforms, the ORDERS panel and the replay bar all stay put
# while the board swells inside its frame and clips against the edge,
# which reads as a rendering bug rather than a push-in. The grid's own
# dotted background magnifies with it into a pale grey slab. Four
# pixel-anchored overlays measure cell rects at paint time, so all four
# had to be chased and re-anchored on every frame of the glide or they
# drew the previous board's geometry over the new one. And the FX layer
# had to be re-parented out of the transform, because effects place
# themselves with a screen-space delta written back as a local offset —
# an identity that only holds while layer and cells share a scale.
#
# All of that is gone. `push_in` now records a KEYFRAME — a moment, a
# rectangle in page pixels, and a ramp — and the zoom is applied
# afterwards to the recorded frames, as a crop and rescale in ffmpeg.
# The whole 1280x800 frame moves together because it is one image by
# then, so nothing can be left behind: an overlay cannot fall off a
# board that is a photograph. The picture softens when it is enlarged,
# which is the honest cost and the reason for shooting at 2x below.
#
# It also unlocks the thing the transform could never do. The old camera
# could only ever look at the map, because the map viewport was the
# element being scaled. This one frames any rectangle on the page, which
# is how the score film gets to push in on the catapult and then on the
# score counting up under the station.
#
# The scale is still DERIVED, never passed. A hand-picked 2.1x once
# framed a pair of crash sites five squares apart so tightly that the
# second was off the bottom edge, and nothing failed: the film was a
# close-up of blank terrain with the captions still narrating
# explosions. Solving for a scale that fits every square the beat is
# about cannot make that mistake.
_MEASURE_CELLS = """
([cells, pad]) => {
  const els = cells.map(([x, y]) =>
    document.querySelector(`.cell[data-x="${x}"][data-y="${y}"]`));
  if (els.some((e) => !e)) return null;
  const base = els[0].getBoundingClientRect().width;
  let l = Infinity, t = Infinity, r = -Infinity, b = -Infinity;
  for (const e of els) {
    const b2 = e.getBoundingClientRect();
    l = Math.min(l, b2.left); t = Math.min(t, b2.top);
    r = Math.max(r, b2.right); b = Math.max(b, b2.bottom);
  }
  // Breathing room is measured in CELLS, not pixels, so a pad that
  // reads as half a square on a 24-wide board still reads as half a
  // square on a 40-wide one.
  const p = pad * base;
  return {x: l - p, y: t - p, w: (r - l) + 2 * p, h: (b - t) + 2 * p,
          base: base};
}
"""

_MEASURE_SEL = """
([sels, pad]) => {
  let l = Infinity, t = Infinity, r = -Infinity, b = -Infinity, n = 0;
  for (const sel of sels) {
    const e = document.querySelector(sel);
    if (!e) continue;
    const bb = e.getBoundingClientRect();
    if (bb.width < 1 || bb.height < 1) continue;
    n += 1;
    l = Math.min(l, bb.left); t = Math.min(t, bb.top);
    r = Math.max(r, bb.right); b = Math.max(b, bb.bottom);
  }
  if (!n) return null;
  // Pad in PIXELS here: a panel has no cell size to measure against.
  return {x: l - pad, y: t - pad, w: (r - l) + 2 * pad,
          h: (b - t) + 2 * pad, base: 0};
}
"""

_RAISE_CURTAIN = """
() => {
  const d = document.getElementById('film-curtain');
  if (d) { d.style.opacity = '0'; setTimeout(() => d.remove(), 520); }
  const m = document.querySelector('.soc-tut');
  if (m) m.hidden = true;
}
"""


class Film:
    """A page plus the cursor/caption kit, with human-paced verbs.

    Every verb dwells either side of its click. The dwells are not
    padding — automation clicks faster than anyone can follow, and a film
    with the dwells removed is a flicker.
    """

    def __init__(self, pg, plan: Optional[Dict[str, Any]] = None) -> None:
        self.pg = pg
        self.fails: List[str] = []
        #: Squares (and unit ids) that setup already committed the rival
        #: to. The outcome films must aim at exactly these — the rival's
        #: night is on the books before the page loads, so a film that
        #: picked its own target off the board would miss.
        self.plan: Dict[str, Any] = plan or {}
        #: The close-up currently held, as ``(cells, pad, max_scale)``.
        #: Kept so it can be re-measured whenever the grid is rebuilt
        #: under it — see ``_reaim``.
        self._cam: Optional[tuple] = None
        #: Camera keyframes, in the order they were called: each is a
        #: moment (seconds after ``t0``), a centre and a zoom in PAGE
        #: pixels, and a ramp. ``_post`` turns these into the crop.
        self.cam: List[Dict[str, float]] = []
        #: The crop the camera is holding, in page pixels, or ``None``
        #: wide open. Anything the film wants SEEN has to be inside it.
        self._win: Optional[Dict[str, float]] = None
        #: Monotonic zero. Set by ``shoot`` to the instant the page was
        #: created, which is also when the recording starts — so a
        #: keyframe's ``t`` and the boot-trim are measured off the same
        #: origin. That shared origin is the whole sync story: if the
        #: recorder actually starts a few frames late, both the trim and
        #: every keyframe slip by the same amount and the cues still
        #: land on their beats.
        self.t0: float = time.monotonic()
        #: When each caption went up, in seconds after ``t0`` — the same
        #: origin as the camera keyframes, so the post-pass can shift
        #: both by the boot-trim and keep them aligned (v1.47).
        #:
        #: Recorded on every shoot, not just a narrated one, because the
        #: cost is a few hundred bytes and the alternative is a second
        #: shoot whenever someone wants the timings. What the voiceover
        #: needs is exactly this: the instant a line appeared, so its
        #: audio can start there instead of being guessed at.
        self.cues: List[Dict[str, Any]] = []
        #: True while the night is resolving, where captions are cued off
        #: the sim's clock. Narration must not lengthen a caption in
        #: here: the next cue fires when its hour reaches the screen, so
        #: padding the wait would put the film behind its own night.
        self._on_clock: bool = False

    def kit(self) -> None:
        self.pg.evaluate(_KIT)

    def raise_curtain(self) -> None:
        self.pg.evaluate(_RAISE_CURTAIN)
        self.pg.wait_for_timeout(560)

    def say(self, text: Optional[str], hold: int = 0) -> None:
        self.pg.evaluate("(t) => window.__film.say(t)", text)
        if text:
            self.cues.append({"t": time.monotonic() - self.t0, "text": text})
            self._expect_caption_in_shot(text)
            if VOICE and not self._on_clock:
                # The picture waits for the voice, never the reverse.
                # Doing it here — at shoot time, off the real rendered
                # clip — is what makes narration impossible to
                # desynchronise: there is one clock, not an audio one
                # and a video one that have to be kept in agreement.
                hold = max(hold, _voice_hold_ms(text))
        if hold:
            self.pg.wait_for_timeout(hold)

    def _expect_caption_in_shot(self, text: str) -> None:
        """A caption outside the crop is a caption nobody ever sees.

        Worth a check of its own because it is invisible to every other
        one: the shoot passes, the film plays, the beat happens — and
        the line explaining it was cropped off. Exactly that shipped
        once, in every close-up at the same time.
        """
        if not self._win:
            return
        box = self.pg.evaluate("() => window.__film.capBox()")
        w = self._win
        if (box["x"] < w["x"] - 2 or box["y"] < w["y"] - 2
                or box["x"] + box["w"] > w["x"] + w["w"] + 2
                or box["y"] + box["h"] > w["y"] + w["h"] + 2):
            self.fails.append(
                f"caption {text[:44]!r} sits at "
                f"({box['x']:.0f},{box['y']:.0f} {box['w']:.0f}x{box['h']:.0f}) "
                f"but the camera is cropped to "
                f"({w['x']:.0f},{w['y']:.0f} {w['w']:.0f}x{w['h']:.0f}) — "
                f"this line is off the edge of the delivered frame"
            )

    def wait(self, ms: int) -> None:
        self.pg.wait_for_timeout(ms)

    def _centre(self, selector: str) -> Optional[tuple[float, float]]:
        box = self.pg.locator(selector).first.bounding_box()
        if not box:
            # A matched-but-boxless element means the panel it lives in
            # is not on screen — a wrong-phase selector, not a crash.
            # Raising here used to abandon the shoot half way and leave
            # a film that ends mid-sentence.
            self.fails.append(f"nothing to point at: {selector!r} has no box "
                              f"(wrong phase, or hidden)")
            return None
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    def glide(self, selector: str, ms: int = 620) -> None:
        centre = self._centre(selector)
        if centre is None:
            return
        x, y = centre
        self.pg.evaluate("([x, y, ms]) => window.__film.glide(x, y, ms)", [x, y, ms])
        # Move the REAL pointer too, or nothing hovers: the fake cursor is
        # decoration and fires no events. Hover states and the AoE preview
        # are half of what these films exist to show.
        self.pg.mouse.move(x, y)
        self.wait(int(ms * 0.9))

    def click(self, selector: str, before: int = 420, after: int = 700,
              ms: int = 620) -> None:
        self.glide(selector, ms=ms)
        self.wait(before)
        self.pg.evaluate("() => window.__film.press(false)")
        self.wait(120)
        self.pg.locator(selector).first.click()
        self.wait(after)

    def right_click(self, selector: str, before: int = 420, after: int = 800,
                    ms: int = 620) -> None:
        self.glide(selector, ms=ms)
        self.wait(before)
        self.pg.evaluate("() => window.__film.press(true)")
        self.wait(120)
        self.pg.locator(selector).first.click(button="right")
        self.wait(after)

    def cell(self, x: int, y: int) -> str:
        return f'.cell[data-x="{x}"][data-y="{y}"]'

    def hover_cell(self, x: int, y: int, ms: int = 600, hold: int = 700) -> None:
        self.glide(self.cell(x, y), ms=ms)
        self.wait(hold)

    def escape(self, after: int = 400) -> None:
        """Disarm the sticky picker.

        A landing chains straight into steps by design (v0.9.5), so the
        pick-mode banner is still blinking when a beat ends. Leaving it up
        ends the film mid-instruction, telling the viewer to keep clicking
        after the lesson is over.
        """
        self.pg.keyboard.press("Escape")
        self.wait(after)

    def queue(self) -> List[str]:
        return self.pg.evaluate("""() => [...document.querySelectorAll('.solo-queue-row')]
            .map((r) => (r.textContent || '').replace(/\\s+/g, ' ').trim())""")

    def slots(self) -> int:
        """Hours committed, read off the panel's own ``N/21 slots``.

        Not a row count. The queue collapses a walk chain into a single
        ``02-04 walk`` row, so three real orders read as one row — which
        made the first version of this check fail a film that was
        perfectly correct.
        """
        txt = self.pg.evaluate("""() => {
          const el = document.getElementById('solo-queue-count');
          return el ? (el.textContent || '') : '';
        }""")
        head = str(txt).split("/")[0].strip()
        return int(head) if head.isdigit() else -1

    def expect_slots(self, n: int, what: str) -> None:
        got = self.slots()
        if got < n:
            self.fails.append(
                f"{what}: wanted {n}+ committed hour(s), got {got} — the film "
                f"would show a turn that does not happen. rows={self.queue()}"
            )

    # ── the fleet roster ────────────────────────────────────────────

    def fleet_verb(self, row: int, verb: str) -> str:
        """One harvester's verb button. Rows are in roster order.

        Indexed with Playwright's ``nth=`` rather than ``:nth-of-type``
        because the roster host holds more than fleet rows, so the CSS
        ordinal and the visible ordinal are not the same number.
        """
        return (f'.cc-fleet-row >> nth={row} '
                f'>> .cc-fleet-verb:has-text("{verb}")')

    def order(self, row: int, at: tuple, steps: tuple = ()) -> None:
        """DROP a harvester, then walk it. The picker re-arms as STEP
        after a landing (v0.9.5), so a walk really is just more clicks."""
        self.click(self.fleet_verb(row, "DROP"), before=380, after=440)
        self.click(self.cell(*at), before=300, after=560, ms=560)
        for st in steps:
            self.click(self.cell(*st), before=200, after=460, ms=340)
        self.escape(after=280)

    def lift(self, row: int) -> None:
        sel = self.fleet_verb(row, "LIFT")
        if not self.pg.locator(sel).count():
            self.fails.append(f"no LIFT verb on fleet row {row}")
            return
        self.click(sel, before=340, after=620)

    # ── riding a live night ─────────────────────────────────────────

    def slot(self) -> str:
        """The clock on screen: ``VESPERA``, ``H01``..``H21``, ``AURORA``."""
        return str(self.pg.evaluate("""() => {
          const e = document.getElementById('replay-slot');
          return e ? (e.textContent || '').trim() : '';
        }"""))

    def _resolving(self) -> bool:
        return bool(self.pg.evaluate(
            "() => document.body.classList.contains('cc-resolving')"))

    # ── replaying a night that has already happened ─────────────────
    #
    # The cinematic runs at the game's pace, which is the right pace for
    # playing and the wrong one for teaching: a collision is over in
    # under a second and the eye has nowhere to be beforehand. There is
    # no speed control to turn down — and adding one to the product for
    # the benefit of the film crew would be the tail wagging the dog —
    # but the replay bar already walks a resolved night an hour at a
    # click, and stepping FORWARD re-fires that hour's animations. So a
    # beat worth a second look gets shown twice: once at speed, then
    # again a hand-cranked hour at a time.

    def replay_rewind_to(self, slot: str, limit: int = 40) -> bool:
        """Step the replay cursor back until the clock reads ``slot``."""
        want = slot.upper()
        for _ in range(limit):
            if self.slot().upper() == want:
                return True
            self.pg.click("#replay-prev")
            self.pg.wait_for_timeout(90)
        self.fails.append(
            f"could not rewind the replay to {want} in {limit} step(s); "
            f"the clock stopped at {self.slot()!r}"
        )
        return False

    def replay_step(self, n: int = 1, hold: int = 1400) -> None:
        """Advance the replay one hour per click, dwelling on each."""
        for _ in range(max(1, n)):
            self.pg.click("#replay-next")
            self.wait(hold)

    def praxis(self, cues: Optional[List[tuple]] = None,
               timeout_ms: int = 150_000) -> None:
        """Commit the night and ride the cinematic, cueing off its clock.

        ``cues`` are ``(slot, text)``, ``(slot, text, delay_ms)`` or
        ``(slot, text, delay_ms, [squares])``. The caption changes when
        that hour reaches the screen rather than after a measured wait,
        because a night's runtime depends on how many hours carry frames
        — a stopwatch drifts off the beat the first time a film's orders
        change.

        The fourth element moves the camera, and exists because a night
        with two events far apart cannot be framed by one static shot:
        fitting both at once drops the zoom to about 1.4x, which is no
        close-up at all. Cue the move on the QUIET hour before the event
        so the camera has settled by the time anything happens.

        The strand guard (v0.9.15) eats the first TRANSMIT and asks
        again, so a single click is not a commit. That is a real thing
        the player meets, and ``basic_stranded`` films it deliberately.
        """
        self._on_clock = True
        try:
            self._praxis(cues, timeout_ms)
        finally:
            self._on_clock = False

    def _praxis(self, cues: Optional[List[tuple]], timeout_ms: int) -> None:
        btn = "#solo-commit-night"
        self.click(btn, before=420, after=1100)
        if self.pg.locator(btn).is_visible():
            self.pg.locator(btn).click()
            self.wait(700)

        # The class goes on at submit; if it never appears the click did
        # not commit and the rest of the film is a static board.
        t0 = time.monotonic()
        while not self._resolving() and (time.monotonic() - t0) < 12:
            time.sleep(0.1)
        if not self._resolving():
            self.fails.append(
                "PRAXIS did not start a night — no `cc-resolving` on <body>. "
                "A guard probably ate the click; the rest of this film is a "
                "still frame."
            )
            return

        pending, seen = self._ride(cues, timeout_ms)
        if pending:
            self.fails.append(
                f"cues never fired: {[c[0] for c in pending]} — the night "
                f"reached {sorted(seen)}, so those captions were never seen"
            )

    def _ride(self, cues: Optional[List[tuple]],
              timeout_ms: int) -> tuple:
        """Poll the on-screen clock and fire cues as their hour lands."""
        pending = list(cues or [])
        seen: set = set()
        deadline = time.monotonic() + timeout_ms / 1000.0
        while self._resolving() and time.monotonic() < deadline:
            now = self.slot()
            if now and now not in seen:
                seen.add(now)
                # Re-aim before anything else. The cinematic rebuilds the
                # grid between hours, and a rebuilt grid can sit at a
                # different offset inside the viewport — the scale
                # survives that, the translate does not, so a close-up
                # set up during planning drifts off its subject the
                # moment the night starts playing. Cheap to just solve it
                # again, and snapping is invisible mid-cinematic.
                self._reaim()
                for cue in list(pending):
                    if cue[0] != now:
                        continue
                    pending.remove(cue)
                    if len(cue) > 2 and cue[2]:
                        self.wait(int(cue[2]))
                    self.say(cue[1])
                    if len(cue) > 3 and cue[3]:
                        self._aim(cue[3], ms=int(cue[4]) if len(cue) > 4
                                  else 560)
            time.sleep(0.1)
        return pending, seen

    def commit_orbit(self, cues: Optional[List[tuple]] = None,
                     timeout_ms: int = 60_000) -> None:
        """Commit the ORBIT phase and ride the beat that follows it.

        The catapults fire and the score folds in on the orbit RESOLVE,
        not on the night — which is the whole point of ``basic_score``.
        A phase only resolves once EVERY seat has committed, so on a
        duel board the rival's orbit has to be posted too; forgetting it
        is not an error, it is a film that quietly sits on a spinning
        board until the timeout.
        """
        btn = "#solo-commit-orbit"
        if not self.pg.locator(btn).is_visible():
            self.fails.append("no ORBIT commit button — wrong phase?")
            return
        self.click(btn, before=420, after=1200)
        t0 = time.monotonic()
        while not self._resolving() and (time.monotonic() - t0) < 12:
            time.sleep(0.1)
        self._ride(cues, timeout_ms)

    # ── looking closely ─────────────────────────────────────────────

    def at(self) -> float:
        """Seconds since the recording started."""
        return time.monotonic() - self.t0

    def _aim(self, target: Any, ms: int = 560) -> None:
        """Point the camera at whatever a cue asked for.

        Squares as ``(x, y)`` pairs, panels as CSS selectors. A cue can
        want either: ``basic_score`` follows one load off the ground and
        onto the catapult, and only half of that journey happens on the
        board.
        """
        if isinstance(target, str):
            self.push_in_on(target, ms=ms)
        elif target and isinstance(target[0], str):
            self.push_in_on(*target, ms=ms)
        else:
            self.push_in(*target, ms=ms)

    def _keyframe(self, rect: Dict[str, float], max_scale: float,
                  ms: int, what: str = "") -> float:
        """Record where the camera should be, and when.

        Set ``FILM_CAM=1`` to have every move print what it measured and
        what it solved for. Framing is the one thing no assertion here
        can judge — "is this a good shot?" is a question for eyes — and
        eyeballing a composition off exported frames without the numbers
        behind it is how the catapult beat got tuned three times.
        """
        vw = float(VIEWPORT["width"])
        vh = float(VIEWPORT["height"])
        w = max(1.0, float(rect["w"]))
        h = max(1.0, float(rect["h"]))
        z = max(1.0, min(max_scale, vw / w, vh / h))
        cx = float(rect["x"]) + w / 2.0
        cy = float(rect["y"]) + h / 2.0
        self.cam.append({"t": self.at(), "ms": float(ms), "z": z,
                         "cx": cx, "cy": cy})

        # Where the crop actually lands once it is clamped to the frame.
        # A subject near an edge never gets centred, and that, not the
        # scale, is usually what looks wrong.
        win_w, win_h = vw / z, vh / z
        self._win = {
            "x": min(max(cx - win_w / 2, 0.0), vw - win_w),
            "y": min(max(cy - win_h / 2, 0.0), vh - win_h),
            "w": win_w, "h": win_h, "z": z,
        }
        # Move the caption inside the new crop NOW, at the start of the
        # ramp rather than the end. The window only shrinks toward its
        # target, so a caption placed in the target is in shot for every
        # frame of the move; placed at the end it is missing for all of
        # them.
        self.pg.evaluate("(w) => window.__film.frame(w)", self._win)
        if os.environ.get("FILM_CAM"):
            print(f"    cam {what or 'move':22s} subject "
                  f"{w:4.0f}x{h:4.0f} @ ({cx:4.0f},{cy:4.0f})  z={z:4.2f}  "
                  f"window {win_w:4.0f}x{win_h:4.0f} @ "
                  f"({self._win['x']:4.0f},{self._win['y']:4.0f})  "
                  f"subject fills {100 * w / win_w:3.0f}% x {100 * h / win_h:3.0f}%")
        return z

    def _reaim(self) -> None:
        """Re-measure the held close-up against the board as it is now.

        The cinematic rebuilds the grid between hours and a rebuilt grid
        can sit at a different offset, so a close-up set up during
        planning drifts off its subject the moment the night plays. The
        old camera re-solved its transform; this one appends a snap
        keyframe (``ms=0``) with the subject's new position. Invisible
        mid-cinematic, and it keeps the crop pointed at the thing the
        caption is talking about.
        """
        if not self._cam:
            return
        cells, pad, max_scale = self._cam
        rect = self.pg.evaluate(
            _MEASURE_CELLS, [[list(c) for c in cells], pad])
        if not rect:
            return
        last = self.cam[-1] if self.cam else None
        z = max(1.0, min(max_scale,
                         float(VIEWPORT["width"]) / max(1.0, rect["w"]),
                         float(VIEWPORT["height"]) / max(1.0, rect["h"])))
        cx = float(rect["x"]) + float(rect["w"]) / 2.0
        cy = float(rect["y"]) + float(rect["h"]) / 2.0
        # Only if it actually moved. An unconditional keyframe every hour
        # would pile up hundreds of no-op steps in the filter expression.
        if last and abs(last["cx"] - cx) < 3 and abs(last["cy"] - cy) < 3 \
                and abs(last["z"] - z) < 0.02:
            return
        self.cam.append({"t": self.at(), "ms": 0.0, "z": z,
                         "cx": cx, "cy": cy})

    def push_in(self, *cells: tuple, pad: float = 1.4, max_scale: float = 2.6,
                ms: int = 900) -> None:
        """Move the CAMERA in until every given square is in frame.

        Not the game's zoom control. The product's zoom dragger spans
        45%-127% of a fit-to-width board, which is a legibility
        preference, not a close-up: at full stretch a harvester wreck is
        still about ten pixels of grey, and a collision burst is gone in
        under a second. The first cut of the crash film used it and the
        two explosions it was built around were invisible.

        Nothing happens on screen when this is called — see the camera
        note above. It measures the squares, banks a keyframe, and waits
        out the move so the beat still occupies the screen time a push-in
        takes. The zoom itself is cropped in afterwards.

        Pass every square the beat is about; the scale is solved for,
        never guessed. ``pad`` is in SQUARES and buys room for the burst,
        which is drawn well outside its cell.
        """
        rect = self.pg.evaluate(
            _MEASURE_CELLS, [[list(c) for c in cells], pad])
        if not rect:
            self.fails.append(f"cannot push in on {list(cells)} — those "
                              f"squares are not on the board, so the "
                              f"close-up shows nothing")
            return
        self._cam = (cells, pad, max_scale)
        self._keyframe(rect, max_scale, ms, f"cells {list(cells)}"[:22])
        self.wait(ms + 260)

    def push_in_on(self, *selectors: str, pad: float = 14.0,
                   max_scale: float = 3.2, ms: int = 900,
                   why: str = "") -> None:
        """Push in on PANELS rather than squares.

        The old transform could only ever magnify the map, because the
        map viewport was the element it scaled. A crop over the finished
        frame has no such loyalty, so the camera can look at the
        catapult, the station, the vault — anywhere the lesson happens
        to live. ``basic_score`` is built on this.
        """
        rect = self.pg.evaluate(_MEASURE_SEL, [list(selectors), pad])
        if not rect:
            self.fails.append(
                f"cannot push in on {list(selectors)}{' — ' + why if why else ''}"
                f" — nothing on screen matches, so the close-up is of the "
                f"page background")
            return
        self._cam = None
        self._keyframe(rect, max_scale, ms, selectors[0])
        self.wait(ms + 260)

    def watch_lifters(self) -> None:
        """Start tallying how many Houses have a lifter in the air at once.

        A collision beat is under a second and the craft are a glyph
        wide, so "did both lifters fly?" is not a question watching the
        film reliably answers — the first cut of `basic_crash` shipped
        with the rival's craft suppressed entirely and nobody caught it
        from the video.

        Two narrowings, both learned by writing the loose version first.
        Count DISTINCT seat colours, because one House launching twice
        is not the claim. And count only BOUNCE arcs: every lift at dawn
        is also an orbital ghost, so a census over the whole cinematic
        reaches two colours on the recoveries alone and passes happily
        with the collision itself rendered as a single craft.
        """
        self.pg.evaluate("""() => {
          window.__lifterPeak = 0;
          if (window.__lifterTimer) clearInterval(window.__lifterTimer);
          window.__lifterTimer = setInterval(() => {
            const seen = new Set();
            document.querySelectorAll('.replay-anim-ghost--bounce')
              .forEach((e) => seen.add(e.style.color || ''));
            window.__lifterPeak = Math.max(window.__lifterPeak, seen.size);
          }, 40);
        }""")

    def expect_lifters(self, n: int, where: str) -> None:
        peak = int(self.pg.evaluate("""() => {
          if (window.__lifterTimer) clearInterval(window.__lifterTimer);
          window.__lifterTimer = 0;
          return window.__lifterPeak || 0;
        }""") or 0)
        if peak < n:
            self.fails.append(
                f"{where}: only {peak} House(s) had a lifter on screen at "
                f"once, wanted {n} — the beat plays as one craft bouncing "
                f"off an empty square"
            )

    def pull_out(self, ms: int = 700) -> None:
        """Back out to the whole frame."""
        self._cam = None
        if not self.cam:
            return  # never pushed in; nothing to come back from
        self.cam.append({
            "t": self.at(), "ms": float(ms), "z": 1.0,
            "cx": float(VIEWPORT["width"]) / 2.0,
            "cy": float(VIEWPORT["height"]) / 2.0,
        })
        # Caption last, unlike a push-in. Going out, the window GROWS
        # away from where the caption is sitting, so it stays in shot
        # all the way; sending it to the page bottom now would drop it
        # out for the length of the move.
        self.wait(ms + 200)
        self._win = None
        self.pg.evaluate("() => window.__film.frame(null)")

    def score(self) -> str:
        return str(self.pg.evaluate("""() => {
          const e = document.querySelector('[data-os-score="p1"]');
          return e ? (e.textContent || '').trim() : '';
        }"""))

    def point_near(self, selector: str, dx: int = 0, dy: int = 0,
                   ms: int = 700) -> None:
        """Put the cursor BESIDE something instead of on top of it.

        Two reasons, both learned from watching the first cut. The ring
        is 26px and a station score is smaller than that, so pointing at
        the number hides the number. And hovering anywhere inside a
        station pops a full observations card over the left third of the
        board, which at some beats is exactly the information you want
        and at others is an empty panel covering the map.
        """
        box = self.pg.locator(selector).first.bounding_box()
        if not box:
            self.fails.append(f"no box for {selector!r} — cannot point at it")
            return
        x = box["x"] + box["width"] / 2 + dx
        y = box["y"] + box["height"] / 2 + dy
        self.pg.evaluate("([x, y, ms]) => window.__film.glide(x, y, ms)",
                         [x, y, ms])
        self.wait(int(ms * 0.9))

    def park(self) -> None:
        """Move the pointer somewhere that pops nothing.

        Harder than it sounds, and worth the trouble. Hovering a station
        pops a large observations card over the left third of the
        screen; hovering a square pops that square's readout. Both are
        the right thing to show when they ARE the subject and pure
        vandalism when the next beat is elsewhere — the first cut parked
        on the middle of the board and left a stale cell card sitting
        over the map through the catapult beat.

        So park in the dead strip under the grid: inside the map frame,
        over no square and no station.
        """
        spot = self.pg.evaluate("""() => {
          const mp = document.getElementById('map-player');
          const vp = document.querySelector('.cc-map-viewport');
          if (!mp || !vp) return null;
          const m = mp.getBoundingClientRect();
          const v = vp.getBoundingClientRect();
          // Under the board if there is room, otherwise beside it.
          if (v.bottom - m.bottom > 40)
            return [m.left + m.width / 2, m.bottom + 22];
          if (v.right - m.right > 40) return [m.right + 22, m.top + 40];
          return null;
        }""")
        if not spot:
            spot = [VIEWPORT["width"] * 0.34, VIEWPORT["height"] * 0.95]
        self.pg.mouse.move(spot[0], spot[1])
        self.pg.evaluate("([x, y]) => window.__film.put(x, y)", spot)
        self.wait(420)
        left = self.tooltip()
        if left:
            self.fails.append(
                f"parked the cursor and a cell card stayed up ({left[:40]!r}) "
                f"— it will sit over the board for the whole next beat"
            )

    def tooltip(self) -> str:
        return str(self.pg.evaluate("""() => {
          const t = document.querySelector('.cell-tooltip');
          return (t && !t.hidden) ? (t.textContent || '')
            .replace(/\\s+/g, ' ').trim() : '';
        }"""))


# ── film registry ───────────────────────────────────────────────────

FILMS: Dict[str, Callable[..., None]] = {}

#: Films delivered as one clip but shot in more than one take (v1.37).
#: A take is a whole session and a whole night, so anything that needs
#: to show the SAME move going two different ways cannot be one take —
#: and a before/after is often the only honest way to teach a timing
#: rule. Parts are ordinary films: they stage, shoot and get their
#: camera pass exactly like everything else, and the join is a stream
#: copy of two finished files, which is why they must share an encode.
#: The parts are deleted once joined; nothing outside here knows they
#: existed.
SPLICES: Dict[str, List[str]] = {}

#: What must be on screen before a film starts. Per-film because the
#: ORDERS panel does not exist during ORBIT and vice versa — waiting on
#: the wrong one is a 25-second timeout, not a useful error.
READY: Dict[str, str] = {}


def film(name: str, ready: str = "#orders-asset-roster") -> Callable:
    def deco(fn: Callable) -> Callable:
        FILMS[name] = fn
        READY[name] = ready
        return fn
    return deco


#: Setup hands the film the squares it chose. The outcome films need
#: this: the rival's orders are already posted at specific coordinates
#: before the page loads, so the film cannot go and pick its own target
#: off the rendered board and hope the two agree.
Plan = Dict[str, Any]


#: The only moves a harvester has. ``session._adj`` is Manhattan-1, so a
#: diagonal step is refused — and refused SILENTLY as far as a film is
#: concerned, because the click still happened and the picker stays
#: armed. A chain builder that offers diagonals therefore produces films
#: that walk two squares while the caption says four.
STEP_RING = ((1, 0), (0, 1), (-1, 0), (0, -1))


def _mid_cells(pg) -> Dict[str, int]:
    """Board extent, so beats aim by fraction rather than by literal.

    A hardcoded (12, 8) breaks silently the day the Basic board changes
    size — it still clicks something, just not the square the caption is
    talking about.
    """
    return pg.evaluate("""() => {
      const cells = [...document.querySelectorAll('.cell')];
      let mx = 0, my = 0;
      for (const c of cells) {
        mx = Math.max(mx, +c.dataset.x);
        my = Math.max(my, +c.dataset.y);
      }
      return { mx, my, n: cells.length };
    }""")


@film("basic_probe")
def _probe(f: Film, base: str, sid: str) -> None:
    """Night one: the board is black, and probes are how you look.

    Shows both routes to the same order — the DEPLOY button and the
    right-click menu — because the second one is invisible until someone
    tells you it is there.
    """
    ext = _mid_cells(f.pg)
    ax, ay = int(ext["mx"] * 0.32), int(ext["my"] * 0.40)
    bx, by = int(ext["mx"] * 0.68), int(ext["my"] * 0.62)

    f.say("The whole board is fog. You cannot see the RED.", hold=2200)

    f.say("Probes are how you look")
    f.click(".cc-deploy-btn", before=520, after=520)

    f.say("The outline is what this probe will reveal")
    # Two hovers before committing: the footprint tracks the pointer, and
    # ONE hover looks like decoration rather than a thing you aim.
    f.hover_cell(int(ext["mx"] * 0.5), int(ext["my"] * 0.3), ms=700, hold=850)
    f.hover_cell(ax, ay, ms=650, hold=950)
    f.click(f.cell(ax, ay), before=350, after=1000, ms=260)
    f.escape()
    f.expect_slots(1, "first probe")

    f.say("Right-click any square for the same orders", hold=700)
    f.right_click(f.cell(bx, by), before=520, after=900)
    # The board menu labels its rows in prose; match on the verb so a
    # label reword does not silently shoot a film of nothing.
    menu_item = '.board-menu-item:has-text("launch probe")'
    if f.pg.locator(menu_item).count():
        f.click(menu_item, before=420, after=900, ms=420)
    else:
        f.fails.append("no 'launch probe' row in the board menu")
    f.escape()
    f.expect_slots(2, "second probe")

    f.say("You start with two. Spend both \u2014 an unspent probe sees nothing.",
          hold=2400)
    f.say(None)
    f.wait(500)


@film("basic_praxis")
def _praxis(f: Film, base: str, sid: str) -> None:
    """Night one: orders are a plan until PRAXIS, and both Houses commit blind."""
    ext = _mid_cells(f.pg)
    ax, ay = int(ext["mx"] * 0.35), int(ext["my"] * 0.45)
    bx, by = int(ext["mx"] * 0.66), int(ext["my"] * 0.55)

    # Put a plan on the books quickly and quietly — this film is about
    # what happens to a plan, not about composing one.
    f.say("Nothing happens until you say so", hold=1500)
    f.click(".cc-deploy-btn", before=320, after=380)
    f.click(f.cell(ax, ay), before=280, after=520, ms=520)
    f.escape(after=250)
    f.click(".cc-deploy-btn", before=320, after=380)
    f.click(f.cell(bx, by), before=280, after=620, ms=520)
    f.escape(after=250)
    f.expect_slots(2, "plan for the praxis film")

    f.say("Your orders stack up as a PLAN")
    if f.pg.locator(".solo-queue-row").count():
        f.glide(".solo-queue-row", ms=700)
        f.wait(1100)
        rows = f.pg.locator(".solo-queue-row")
        if rows.count() > 1:
            f.glide(".solo-queue-row:nth-of-type(2)", ms=460)
            f.wait(900)

    f.say("PRAXIS carries it out")
    f.glide("#solo-commit-night", ms=760)
    f.wait(1200)
    f.say("Your opponent is writing theirs at the same time", hold=2400)
    f.say(None)
    f.wait(500)


def _lit_cells(pg) -> List[Dict[str, int]]:
    """Squares this seat can see RIGHT NOW, nearest the middle first.

    `drop_mode` is `live_only`, so a landing is legal only on live
    ground. A film that drops onto fog is teaching an order the engine
    refuses — which is exactly what the spike did, and passed.
    """
    return pg.evaluate("""() => {
      const cells = [...document.querySelectorAll('.cell')];
      let mx = 0, my = 0;
      for (const c of cells) {
        mx = Math.max(mx, +c.dataset.x);
        my = Math.max(my, +c.dataset.y);
      }
      const cx = mx / 2, cy = my / 2;
      return cells
        .filter((c) => !c.classList.contains('cell--fog')
                    && !c.classList.contains('cell--stale'))
        .map((c) => ({
          x: +c.dataset.x,
          y: +c.dataset.y,
          d: Math.hypot(+c.dataset.x - cx, +c.dataset.y - cy),
        }))
        .sort((a, b) => a.d - b.d);
    }""")


@film("basic_drop")
def _drop(f: Film, base: str, sid: str) -> None:
    """Night two: what RED is worth, and what taking it costs.

    Three lessons that only make sense together, which is why they are
    one film (v1.37).

    First the price list. The tooltip already does the arithmetic —
    purity x tier multiplier — but a player reading one card has
    nothing to compare it against. Four cards in a row, on a board
    pinned so the numbers can be said out loud, turns four abstract
    multipliers into an ordering: trace is a wasted hour, vein pays if
    you get a run of it, two mass is most of a jackpot, and a pure is
    worth thirty-three trace squares.

    Then the turn itself. An earlier cut landed on ``lit[0]`` — the
    first live square, whatever it was — so the film that teaches
    harvesting mostly harvested nothing, and never showed the walk
    paying. This one lands ON red and walks a real seam, ending on the
    pure it just priced.

    Then the bill. Every RED square harvested becomes GREEN 255, worth
    -100 to whoever banks it, and entry harvests are not optional
    (§3.4). The last shot is the 765 square reading -100, because the
    trail you leave is the other half of the move and the one nobody
    plans for.

    Drop, walk AND lift stay in one film, as before: splitting the lift
    out left this ending on a harvester the UI had already stickered
    STRANDED. See ``basic_stranded``, which shows that on purpose.
    """
    tiers = {t: tuple(c) for t, c in f.plan["tiers"].items()}
    seam = [tuple(c) for c in f.plan["seam"]]

    def read(tier: str) -> None:
        """Hover a square, frame its card, and check it says what the
        narration is about to claim."""
        x, y = tiers[tier]
        f.hover_cell(x, y, ms=520, hold=620)
        f.push_in_on(f.cell(x, y), TOOLTIP, pad=30.0, max_scale=2.5,
                     ms=760, why=f"the {tier} square and its card")
        f.wait(700)
        tip = f.tooltip()
        want = str(BASIC_TIER_SCORE[tier])
        if want not in tip or tier not in tip.lower():
            f.fails.append(
                f"the {tier} square at {tiers[tier]} reads {tip!r} — the "
                f"narration says {want}, so either the board moved or the "
                f"tooltip stopped pricing cells"
            )

    f.say("RED in the vault is the whole game. Seeing it scores nothing.",
          hold=3000)
    f.say("And no two RED squares are worth the same.", hold=2600)
    f.say("Hover one. The card prices it for you.", hold=2600)

    read("trace")
    f.say("TRACE. Purity 30 \u2014 and a 0.75 penalty on top. Twenty-three.",
          hold=3200)
    f.say("That is one hour of a 21-hour night, and one slot of a "
          "six-slot hold, for twenty-three points.", hold=3800)

    read("vein")
    f.say("VEIN. 107 at 1.0 \u2014 what you see is what you bank.", hold=3000)
    f.say("Ordinary. But there is a lot of vein, and seven of these is a "
          "jackpot.", hold=3400)

    read("mass")
    f.say("MASS. 203 at 1.5 \u2014 three hundred and five.", hold=3000)
    f.say("The workhorse. Two good MASS squares are worth about one "
          "jackpot, and they are far easier to find.", hold=3800)

    read("pure")
    f.say("PURE. 255 at 3.0. Seven hundred and sixty-five.", hold=3000)
    f.say("One square. One hour. Thirty-three TRACE squares.", hold=3200)
    f.say("A pure changes the pace of a game \u2014 which is why nobody "
          "lets you keep one quietly.", hold=3600)
    f.pull_out()

    f.say("So: never walk for trace. Walk a run of vein. Fight for mass. "
          "And take a pure the hour you can reach it.", hold=4200)

    f.say("DROP puts a harvester on the surface \u2014 and only where you "
          "can see RIGHT NOW", hold=3200)
    f.click('.cc-fleet-row .cc-fleet-verb', before=460, after=460)
    f.click(f.cell(*seam[0]), before=520, after=800, ms=700)

    # The picker re-arms as STEP after a landing, by design, so the walk
    # is just more clicks — which is the point worth showing.
    f.say("Every square you enter is harvested automatically. Keep "
          "clicking.", hold=3000)
    for step in seam[1:]:
        sel = f.cell(*step)
        if not f.pg.locator(sel).count():
            f.fails.append(f"no square at {step} — the pinned seam has "
                           f"moved, so the walk is off the board")
            return
        f.click(sel, before=240, after=480, ms=380)
    f.escape(after=300)
    f.say("Six squares \u2014 a landing and five steps. That is the whole "
          "hold, and the last one is the 765.", hold=3800)

    lift = '.cc-fleet-row .cc-fleet-verb:has-text("LIFT")'
    if not f.pg.locator(lift).count():
        f.fails.append("no LIFT verb on the fleet row")
        return
    f.say("Then LIFT, always. Ore on the surface at dawn is not ore.",
          hold=3000)
    f.click(lift, before=460, after=1000)
    f.expect_slots(7, "a landing, five steps and a lift")

    f.praxis(cues=[
        ("H01", "The landing harvests the square it lands on \u2014 237, "
         "mass", 800, [seam[0]]),
        ("H04", "Up the seam, one square an hour", 800),
        # No H07 cue: the lift is the last thing that happens, and the
        # clock runs straight from the hour of the last harvest to
        # AURORA rather than showing an hour with nothing left in it.
        ("H06", "And the last one is the pure. 765.", 1200, [seam[5]]),
        ("AURORA", "Lifted on hour seven with a full hold \u2014 2188 off "
         "one seam.", 1000),
    ])
    f.pull_out()

    f.say("Now look at what you left behind.", hold=2800)
    px, py = tiers["pure"]
    f.hover_cell(px, py, ms=620, hold=700)
    f.push_in_on(f.cell(px, py), TOOLTIP, pad=30.0, max_scale=2.5, ms=860,
                 why="the jackpot square, after the harvest")
    f.wait(900)
    tip = f.tooltip()
    if "GREEN" not in tip.upper() or "100" not in tip:
        f.fails.append(
            f"the harvested jackpot at {(px, py)} reads {tip!r} — this "
            f"film ends on it being GREEN and worth -100"
        )
    f.say("The same square. An hour ago it was 765.", hold=3000)
    f.say("Every RED square you harvest turns GREEN. Green is worth "
          "MINUS one hundred.", hold=3600)
    f.say("And harvesting is automatic \u2014 anything that walks in here "
          "has no choice about picking it up.", hold=3800)
    f.pull_out()
    f.say("So you did not just bank 2188. You laid six of those across "
          "the best seam on the board.", hold=4000)
    f.say("Including for yourself. Never walk back over your own trail.",
          hold=3600)
    f.say(None)
    f.wait(600)


@film("basic_stranded")
def _stranded(f: Film, base: str, sid: str) -> None:
    """Night two: forgetting the lift, committed and paid for.

    The old cut of this stopped at the STRANDED sticker and said "at
    dawn it is destroyed". Telling someone a consequence is not the same
    as showing it, and a film that ends on a warning label is a film you
    can read as a suggestion. So this one commits anyway, rides the
    night to Aurora, and then zooms in on the wreck the sunrise left.
    """
    land = tuple(f.plan["land"])
    walk = [tuple(c) for c in f.plan["walk"]]
    grave = walk[-1] if walk else land

    f.say("Now the mistake everybody makes once", hold=1900)
    f.order(0, land, tuple(walk))

    sticker = '.cc-fleet-row:has-text("STRANDED")'
    if not f.pg.locator(sticker).count():
        f.fails.append(
            "no STRANDED sticker after a drop with no lift — this film has "
            "nothing to point at, and the warning it teaches may be gone"
        )
        return
    f.say("Drop, walk \u2014 and no LIFT. Look at the fleet row.")
    f.glide(sticker, ms=760)
    f.wait(2000)

    # The strand guard eats the first TRANSMIT and asks again. That
    # second chance is the most useful thing on screen and most people
    # click straight through it, so give it its own beat.
    f.say("The game stops you once")
    f.click("#solo-commit-night", before=460, after=1300)
    warn = "#err-solo"
    if f.pg.locator(warn).is_visible():
        f.glide(warn, ms=700)
        f.wait(2600)
    else:
        f.fails.append(
            "no strand warning on the first TRANSMIT — either the guard is "
            "gone or this plan no longer strands anything"
        )
    f.say("Say yes anyway, and watch what dawn does")
    f.praxis(cues=[
        ("H01", "It lands. It works. It fills its hold."),
        ("AURORA", "AURORA \u2014 sunrise sweeps the surface"),
    ])

    f.say("Gone. Harvester and cargo both.", hold=2000)
    # Hover FIRST, then frame the cell and its readout together. Framing
    # the square alone and hovering afterwards puts the tooltip half off
    # the right edge — the close-up exists to make the wreck legible and
    # the card is the half that says what it is.
    f.hover_cell(grave[0], grave[1], ms=700, hold=900)
    f.push_in_on(f.cell(*grave), TOOLTIP, pad=26.0, max_scale=2.5,
                 why="the wreck and the card that names it")
    f.say("That cross is a permanent wreck")
    f.wait(1400)
    # The tooltip renders a wreck as the dagger glyph plus the dead
    # unit's id and the day it died — the word "destroyed" is only the
    # ARIA label, so matching on it passes a film that shows bare ground.
    tip = f.tooltip()
    if "\u2020" not in tip or "lost day" not in tip.lower():
        f.fails.append(
            f"no wreck in the tooltip at {grave} — got {tip[:140]!r}. The "
            f"close-up is pointing at empty ground."
        )
    f.wait(1400)
    f.say("A harvester you do not lift is not yours any more.", hold=2800)
    f.say(None)
    f.wait(600)


@film("basic_crash")
def _crash(f: Film, base: str, sid: str) -> None:
    """Last night: two harvesters cannot share a square — both ways.

    A real crash, twice, in one night. The previous cut of this talked
    about collisions over a board where none happened, because the bot
    in the other seat will not take direction. Setup opens both seats as
    human and posts the rival's night before the camera rolls, so this
    is a genuine simultaneous resolve — the explosions are the engine's,
    not a mock-up.

    Both shapes in one night, deliberately: they look nothing alike on
    screen (one is two ships that never land, the other is a wreck left
    sitting in the road) and a player who has only seen one does not
    recognise the other.
    """
    same = tuple(f.plan["same_square"])
    mine = tuple(f.plan["walk_from"])
    meet = tuple(f.plan["walk_into"])
    theirs = tuple(f.plan["their_from"])

    f.say("Your rival is planning right now, and you cannot see it",
          hold=2400)

    f.say("Send one harvester here")
    f.order(0, same)
    f.lift(0)

    f.say("And walk the other one along this seam")
    f.order(1, mine, (meet,))
    f.lift(1)
    f.expect_slots(5, "two harvesters out, both lifted")

    # The two collisions want opposite cameras, which is why this used
    # to be wrong. A WALK-IN is one sprite arriving at another and reads
    # fine tight. A SIMULTANEOUS DROP is two orbital arcs converging on
    # one square from opposite corners of the board — punch in on the
    # square and both arcs start off-screen, so all you see is a flash
    # in a hole. Hour one is therefore played WIDE, with the whole board
    # in frame and both lifters visible from launch, and the close-up is
    # saved for hour three.
    f.say("Both Houses commit blind. PRAXIS.")
    f.watch_lifters()
    f.praxis(cues=[
        ("H01", "Hour 1 \u2014 you both chose the same landing square"),
        # Hour 2 is the quiet one, so the push happens between the two
        # collisions rather than across either of them.
        ("H02", "Neither lands. Both damaged, both still in orbit.",
         900, [meet, mine, theirs]),
        ("H03", "And yours walks into theirs. Neither moves."),
        ("AURORA", "Your lifts ran, so the wrecks came home"),
    ])

    f.expect_lifters(2, "the simultaneous-drop collision")

    # Hour one again, hand-cranked. At the cinematic's pace two craft
    # meet and are gone inside a second, and nobody watching for the
    # first time knows where to be looking. The replay bar walks the
    # same night an hour a click, and stepping forward re-fires the
    # animations, so this is the identical collision at a speed you can
    # actually read. Gentle push only — the arcs still have to fit.
    f.pull_out()
    f.say("That first one is worth a second look", hold=2000)
    f.push_in(same, pad=5.0, max_scale=1.9, ms=800)
    if f.replay_rewind_to("VESPERA"):
        f.say("Two orbital lifters, one square, neither House knowing")
        f.replay_step(1, hold=2600)
        f.say("Both set down on the same ground. Both bounce.")
        f.replay_step(1, hold=3000)

    # By now the night has resolved, so the ORDERS roster is gone and
    # the aftermath lives in the ORBIT panel: two damaged hulls and a
    # price on the repair.
    f.pull_out()
    dmg = "[data-orbit-damaged-count]"
    if f.pg.locator(dmg).count():
        f.say("Two harvesters, one night, nothing harvested")
        f.glide(dmg, ms=760)
        f.wait(2000)
    count = str(f.pg.evaluate(
        "(s) => { const e = document.querySelector(s); "
        "return e ? (e.textContent || '').trim() : ''; }", dmg))
    if not any(ch.isdigit() and ch != "0" for ch in count):
        f.fails.append(
            f"orbit panel reports {count!r} damaged after two collisions — "
            f"the aftermath this film is built on did not happen"
        )
    f.say("Neither can work again until you pay for REPAIR")
    f.glide('[data-orbit-action="repair"]', ms=700)
    f.wait(2400)
    f.say("Read their trails. Do not share a square.", hold=2600)
    f.say(None)
    f.wait(600)


@film("basic_score")
def _score(f: Film, base: str, sid: str) -> None:
    """Where the number comes from: ground, hold, station, catapult, score.

    This is the one chain in the game that nobody works out by playing,
    because it straddles three phases. You harvest on a night and the
    scoreboard does not move. You commit the orbit and it still does not
    move — you only get a pending "+584" under it. The number itself
    climbs on the NEXT night's dusk, when a catapult you were not
    watching throws the load. Players read the gap as "harvesting does
    nothing" and stop doing it.

    So the film follows one load the whole way and refuses to cut.
    """
    land = tuple(f.plan["land"])
    walk = [tuple(c) for c in f.plan["walk"]]

    f.say("RED in the ground is worth nothing", hold=1700)
    f.hover_cell(land[0], land[1], ms=700, hold=2100)
    if not f.tooltip():
        f.fails.append(
            f"no tooltip over the seam at {land} — the film opens on a "
            f"cell readout that is not there"
        )

    f.say("Land on it, and walk it")
    f.order(0, land, tuple(walk))
    f.say("Every square it crosses goes into the hold", hold=1800)

    f.say("LIFT carries the hold up to your station")
    f.lift(0)
    f.expect_slots(len(walk) + 2, "drop + walk + lift")

    # Hour 1 is the landing, so the lift is one past the last step. Cue
    # off that number rather than a guess: the walk length comes from
    # the board, and a hardcoded H05 lands on the wrong beat the first
    # time the seam is a square shorter.
    lift_hour = f"H{len(walk) + 2:02d}"
    f.praxis(cues=[
        ("H01", "It lands"),
        ("H02", "Every square it cuts turns green \u2014 that is the ore"),
        (lift_hour, "Then the lift carries the hold home"),
    ])

    vault = '[data-os-vault="p1"]'
    if not f.pg.locator(vault).count():
        f.fails.append("no station vault on screen — the middle of the "
                       "chain this film exists to show is missing")
    else:
        f.say("Your station is holding it. Look at your score.")
        f.glide(vault, ms=780)
        f.wait(2200)

    before = f.score()
    f.say("Still nothing. Ore is not score.", hold=2000)

    # A phase resolves when every seat has committed, and on a duel
    # board nobody is playing the other one. Post it now: the ORBIT
    # phase does not exist until the night this film just played
    # resolved, so it could not have been pre-staged.
    submit_orbit(base, sid, [], "p2")
    f.say("Commit the orbit")
    f.commit_orbit()
    f.park()

    delta = '[data-os-score-delta="p1"]'
    pending = str(f.pg.evaluate(
        "(s) => { const e = document.querySelector(s); "
        "return e ? (e.textContent || '').trim() : ''; }", delta))
    if not pending.startswith("+"):
        f.fails.append(
            f"no pending ship delta under the score (got {pending!r}) — the "
            f"beat this film turns on is not on screen"
        )
    f.say(f"{pending or '+0'} is queued on the catapult")
    f.point_near(delta, dx=46, ms=760)
    f.wait(2200)

    # The load only flies on the NEXT night's dusk beat, so the film has
    # to play one more night to show the number move. That gap is not an
    # implementation detail to skip past — it IS the lesson.
    f.park()
    f.say("It ships on the next night. Watch the number.")
    v = view(base, sid)
    submit_night(base, sid, [{"a": "probe", "at": [int(v["width"] * 0.2),
                                                   int(v["height"] * 0.8)]}], "p2")
    f.click(".cc-deploy-btn", before=380, after=420)
    f.click(f.cell(int(v["width"] * 0.5), int(v["height"] * 0.25)),
            before=280, after=520, ms=520)
    f.escape(after=250)

    # The last act is not on the board at all, and the old camera could
    # not have shot it: it only ever scaled the map viewport, so the two
    # things this film is finally ABOUT — an arm throwing a load, and a
    # number climbing — were permanently out of shot. A crop over the
    # finished frame has no such loyalty.
    #
    # Get tight on the catapult BEFORE committing, so the throw happens
    # in a frame we are already holding rather than one we are chasing.
    # Tight, and tighter than it looks like it needs to be. The station
    # rail is a 122px column hard against the left edge, so a crop can
    # never centre it — it clamps at x=0 and the subject sits in the
    # left third whatever you ask for. The only lever left is how much
    # of the REST of the screen comes along, and at a gentler scale that
    # rest was the replay bar and half the board, which is how the first
    # cut ended up looking like an arbitrary corner of the page.
    f.push_in_on(CATAPULT, pad=12.0, max_scale=3.2,
                 why="the catapult is the subject of this film's last beat")
    f.praxis(cues=[
        ("VESPERA", "The catapult loads \u2014 and throws", 0),
        # Same hour, later. Two things happen a beat apart inside this
        # one frame — the arm throws at roughly 1.8s, and the "+X" then
        # folds into a readout that tweens up over about two thirds of a
        # second — and no single framing holds both, because the station
        # rail is a narrow column and fitting the catapult and the score
        # in together drops the pair to a fifth of the width.
        #
        # So: leave late and travel fast. The throw has happened by the
        # time the camera lets go of the catapult, and a 380ms pan lands
        # on the number while it is still climbing. Arriving after it
        # settles shows a total, which is not the lesson — the lesson is
        # a payment arriving.
        ("VESPERA", "\u2014 and the number climbs", 1900, SCOREBOARD, 380),
    ])

    after = f.score()
    if _num(after) <= _num(before):
        f.fails.append(
            f"score did not move ({before!r} -> {after!r}) — this film's "
            f"last beat is its whole point and it did not happen"
        )
    f.say("There it is \u2014 banked, and on the board")
    f.wait(2400)
    f.pull_out()
    f.park()

    f.say("Harvest tonight, ship tomorrow. That is the whole loop.",
          hold=2800)
    f.say(None)
    f.wait(600)


def _num(text: str) -> float:
    keep = "".join(ch for ch in str(text) if ch.isdigit() or ch == ".")
    try:
        return float(keep) if keep else -1.0
    except ValueError:
        return -1.0


@film("basic_supersede")
def _supersede(f: Film, base: str, sid: str) -> None:
    """Last night: overlapping probes waste each other."""
    ext = _mid_cells(f.pg)

    f.say("Probes do not stack usefully", hold=1900)
    f.click(".cc-deploy-btn", before=440, after=460)

    # Aim the first at ground already lit, then move to dark ground, so
    # the overlap and the alternative are both visible in one gesture.
    lit = _lit_cells(f.pg)
    if lit:
        f.say("Aim one over ground you already hold \u2014 look how little is new")
        f.hover_cell(lit[0]["x"], lit[0]["y"], ms=700, hold=1700)

    dark = f.pg.evaluate("""() => {
      const cells = [...document.querySelectorAll('.cell.cell--fog')];
      if (!cells.length) return null;
      let mx = 0, my = 0;
      for (const c of document.querySelectorAll('.cell')) {
        mx = Math.max(mx, +c.dataset.x);
        my = Math.max(my, +c.dataset.y);
      }
      // Furthest fogged square from the board centre reads clearly as
      // "somewhere else" rather than as a nudge.
      let best = null, bd = -1;
      for (const c of cells) {
        const d = Math.hypot(+c.dataset.x - mx / 2, +c.dataset.y - my / 2);
        if (d > bd) { bd = d; best = c; }
      }
      return best ? { x: +best.dataset.x, y: +best.dataset.y } : null;
    }""")
    if dark:
        f.say("Somewhere dark buys you far more")
        f.hover_cell(dark["x"], dark["y"], ms=800, hold=1500)
        f.click(f.cell(dark["x"], dark["y"]), before=340, after=900, ms=280)
        f.escape()
        f.expect_slots(1, "spread probe")
    else:
        f.fails.append("no fogged square left to contrast with — board fully lit")

    f.say("Light the ground you mean to walk. Walk everything you light.",
          hold=2600)
    f.say(None)
    f.wait(600)


@film("basic_buy_harvester", ready='[data-orbit-action="build_harvester"]')
def _buy_harvester(f: Film, base: str, sid: str) -> None:
    """Orbit two: the big purchase, and why."""
    f.say("You have banked a night's work and saved a turn's credits",
          hold=2200)

    f.say("One harvester can only work one seam a night")
    f.click('[data-orbit-action="build_harvester"]', before=560, after=900)

    f.say("Watch what the buy leaves you", hold=1800)
    wallet = "[data-readout='wallet'], .cc-orbit-readout"
    if f.pg.locator(wallet).count():
        f.glide(wallet, ms=700)
        f.wait(1500)
    f.say(None)
    f.wait(500)


@film("basic_orbit_probes", ready='[data-orbit-action="build_probe"]')
def _orbit(f: Film, base: str, sid: str) -> None:
    """Orbit: 1000 credits a turn, and vision is what it buys you first."""
    f.say("Between nights you are in orbit", hold=1800)

    wallet = "[data-readout='wallet'], .cc-orbit-readout"
    if f.pg.locator(wallet).count():
        f.glide(wallet, ms=760)
        f.wait(1400)
    f.say("1000 credits arrive every turn", hold=1800)

    probe_buy = '[data-orbit-action="build_probe"]'
    f.say("A harvester costs more than one turn's income")
    f.glide('[data-orbit-action="build_harvester"]', ms=700)
    f.wait(1500)

    f.say("So buy vision \u2014 probes are what you can afford")
    f.click(probe_buy, before=460, after=700)
    f.click(probe_buy, before=320, after=900)

    commit = "#solo-commit-orbit, [data-orbit-commit]"
    if f.pg.locator(commit).count():
        f.say("Commit the phase, same as a night")
        f.glide(commit, ms=700)
        f.wait(1400)
    f.say(None)
    f.wait(500)


# ── ADVANCED: signs, and the two weapons ────────────────────────────
#
# Basic teaches the machine. Advanced teaches the other House: what you
# can read about them without seeing them (signs), and what you can do
# to them without touching them (EMP, chaff). Every one of these is a
# duel, because every lesson needs a rival who turns up on cue.

def _adv_deploy(f: Film, label: str) -> str:
    return f'.cc-deploy-btn:has-text("{label}")'


@film("adv_hotdrop")
def _adv_hotdrop(f: Film, base: str, sid: str) -> None:
    """Night one: read the smear, then land on ground you cannot see.

    The name is the lesson. You are not dropping onto blue you found —
    you are queueing a landing for hour two into a hole hour one has not
    cut yet, on the strength of a glow. The film has to show the drop
    being clicked onto FOG for that to land.
    """
    sign = tuple(f.plan["sign"])
    land = tuple(f.plan["land"])
    step = tuple(f.plan["step"])

    f.say("The board is dark \u2014 but it is not blank", hold=2000)
    f.hover_cell(sign[0], sign[1], ms=900, hold=1800)
    f.say("That glow is a BLUE SIGN. Blue is radioactive, and the smear "
          "has been there since the season opened.", hold=3200)
    f.say("It says a blue pocket is somewhere under here. Not which "
          "square, and it never fades \u2014 not even once the blue is gone.",
          hold=3400)

    f.say("Probe the brightest part of it")
    f.click('.cc-deploy-btn:has-text("LAUNCH PROBE")', before=460, after=420)
    f.click(f.cell(sign[0], sign[1]), before=520, after=900, ms=700)
    f.escape(after=300)

    f.say("Now the HOT DROP: land the harvester on hour two, into the "
          "hole hour one has not cut yet", hold=3000)
    f.click('.cc-fleet-row .cc-fleet-verb', before=460, after=460)
    f.say("Still fog. You are aiming at the sign, not at anything you "
          "can see.")
    f.click(f.cell(land[0], land[1]), before=620, after=900, ms=760)
    f.click(f.cell(step[0], step[1]), before=300, after=560, ms=420)
    f.escape(after=300)

    lift = '.cc-fleet-row .cc-fleet-verb:has-text("LIFT")'
    if not f.pg.locator(lift).count():
        f.fails.append("no LIFT verb on the fleet row")
        return
    f.click(lift, before=420, after=800)
    f.expect_slots(4, "probe + hot drop + step + lift")

    f.praxis(cues=[
        ("H01", "Hour one \u2014 the probe opens the disk"),
        ("H02", "Hour two \u2014 and the landing you already committed to",
         600, [land, step]),
        ("H03", "BLUE 255. The guess paid.", 700),
        ("AURORA", "Blue is not score. Blue is what buys weapons.", 500),
    ])
    f.pull_out()
    f.say("A hot drop can miss. A probe you never follow always does.",
          hold=2600)
    f.say(None)
    f.wait(600)


@film("adv_buy_emp", ready='[data-orbit-action="build_emp"]')
def _adv_buy_emp(f: Film, base: str, sid: str) -> None:
    """Orbit two: weapons are bought with blue, not credits."""
    f.say("Credits buy hulls. Weapons cost BLUE.", hold=2400)

    blue = "#cc-orbit-blue-total, [data-readout='blue']"
    if f.pg.locator(blue).count():
        f.say("This is the blue you brought home last night")
        f.point_near(blue, dx=0, dy=-26, ms=760)
        f.wait(1800)
    else:
        f.fails.append("no blue readout in the orbit panel to point at")

    cost = '[data-orbit-cost="build_emp"]'
    if f.pg.locator(cost).count():
        f.say("An EMP is 200 blue and 250 credits")
        f.point_near(cost, dx=0, dy=-26, ms=700)
        f.wait(1600)

    f.say("You start each season with 250 blue \u2014 one weapon's worth, "
          "and no more. Everything after that you have to go and dig up.",
          hold=3400)

    f.say("Buy one")
    f.click('[data-orbit-action="build_emp"]', before=520, after=900)
    f.wait(900)

    f.say("Now watch the projected blue drop", hold=2000)
    if f.pg.locator("#cc-orbit-blue-proj").count():
        f.point_near("#cc-orbit-blue-proj", dx=0, dy=-26, ms=700)
        f.wait(1600)
    f.say(None)
    f.wait(500)


@film("adv_redsign")
def _adv_redsign(f: Film, base: str, sid: str) -> None:
    """Night two: your probe finds a pure seam, and tells everyone.

    The beat that matters is not the discovery — it is the second after
    it, when the film says out loud that the rival is now looking at the
    same beacon. A jackpot you find quietly would be worth hoarding; a
    jackpot that announces itself is a race, and that is the whole
    reason the Advanced board plays differently from Basic.

    Say ASYMMETRY out loud, though, because "everyone gets told" on its
    own is a reason not to bother probing. §4.11 jitters the smear's
    centre off the seam and spreads it well past the footprint, and
    paints it on fog only — so the finder is looking at the square in
    live vision while every rival gets a neighbourhood. That gap is the
    prize, and the first cut of this film never mentioned it.
    """
    mine = tuple(f.plan["mine"])

    quiet = tuple(f.plan["quiet"])

    f.say("Somewhere out there is PURE red \u2014 255, the richest square "
          "the map makes", hold=2800)
    f.say("Two probes, two guesses")
    # The jackpot goes SECOND. Queue position is the hour, a one-order
    # night is one hour long, and a discovery that lands in the same
    # breath as "here is your first probe" has no room to be a reveal.
    f.click('.cc-deploy-btn:has-text("LAUNCH PROBE")', before=460, after=420)
    f.click(f.cell(quiet[0], quiet[1]), before=520, after=800, ms=660)
    f.escape(after=300)
    f.click('.cc-deploy-btn:has-text("LAUNCH PROBE")', before=380, after=380)
    f.click(f.cell(mine[0], mine[1]), before=520, after=860, ms=660)
    f.escape(after=300)
    f.expect_slots(2, "a probe elsewhere, then one onto the jackpot")

    f.praxis(cues=[
        ("H01", "Hour one \u2014 the disk opens", 400),
        ("H02", "A REDSIGN. Your probe has walked onto a pure seam.",
         900, [mine]),
    ])
    f.wait(1400)

    beacon = ".redsign-cell, .redsign-beacon"
    if not f.pg.locator(beacon).count():
        f.fails.append(
            "no redsign painted after probing a pure-255 cell \u2014 this "
            "film's entire subject is missing from the frame"
        )
    f.say("And every other House just got one too.", hold=2400)
    f.say("A REDSIGN is public. Same hour for everyone, and it never "
          "says who lit it.", hold=3200)
    # The asymmetry is the lesson, and the first cut of this film left it
    # out entirely — it said the beacon was "minted over" the seam, which
    # reads as a pin on the square and makes finding one worth nothing.
    f.say("But it is not a pin. The smear sits OFF the real seam and "
          "spreads past it: a pure is somewhere around here.", hold=3600)
    f.say("You are the only House that can see the actual square. "
          "That gap is your head start.", hold=3400)
    f.pull_out()
    f.say("Pures are rationed \u2014 a couple on a board this size. That "
          "makes every one of them contested by default.", hold=3400)
    f.say(None)
    f.wait(600)


@film("adv_redsign_rival")
def _adv_redsign_rival(f: Film, base: str, sid: str) -> None:
    """Night two, the other way round: a beacon you did not light.

    Shot as the mirror of ``adv_redsign`` on purpose. Same night, same
    board, and the player does something harmless somewhere else — so
    the only thing that changes on their map is a beacon arriving out of
    empty fog, with no probe of theirs anywhere near it.
    """
    theirs = tuple(f.plan["theirs"])
    quiet = tuple(f.plan["quiet"])

    sign = tuple(f.plan["sign"])

    f.say("This time you go about your own business", hold=2200)
    f.click('.cc-deploy-btn:has-text("LAUNCH PROBE")', before=460, after=420)
    f.click(f.cell(quiet[0], quiet[1]), before=520, after=860, ms=700)
    f.escape(after=300)
    # Second probe for the same reason as in adv_redsign: one order is
    # one hour, and the beacon needs an hour of its own to arrive in.
    f.click('.cc-deploy-btn:has-text("LAUNCH PROBE")', before=380, after=380)
    f.click(f.cell(sign[0], sign[1]), before=460, after=800, ms=620)
    f.escape(after=300)

    f.praxis(cues=[
        ("H01", "Your probes open, over here", 400),
        ("H02", "\u2014 and a REDSIGN blazes over there.", 1100, [theirs]),
    ])
    f.wait(1500)

    beacon = ".redsign-cell, .redsign-beacon"
    if not f.pg.locator(beacon).count():
        f.fails.append(
            "no redsign on the board \u2014 the rival's discovery never "
            "reached this seat, which is the one thing the film claims"
        )
    f.say("You have no probe within a mile of it. That is the other "
          "House walking onto a pure seam.", hold=3200)
    f.say("Same hour they got it \u2014 but only the rough area. Not the "
          "square, not who, not how much.", hold=3600)
    f.say("Only the finder knows exactly where. The smear paints on FOG, "
          "so probe into it and it burns off as you close in.", hold=3600)
    f.pull_out()
    f.say("Which is the point: a jackpot cannot be hidden. It can only "
          "be reached first.", hold=3000)
    f.say(None)
    f.wait(600)


@film("adv_smash_grab")
def _adv_smash_grab(f: Film, base: str, sid: str) -> None:
    """Night three: take the pure you can see, and take it badly.

    The instinct on finding a jackpot is to work the seam around it,
    and the arithmetic says otherwise. A pure is 255 x 3.0 = 765 in ONE
    hour; the five squares behind it are 1050 spread over five more,
    plus the lift. So the greedy line is worth 73% more and costs three
    and a half times the exposure — and every extra hour is an hour the
    765 is still sitting on the board, where a rival who can see it can
    land on it.

    The film prices both and then throws the better one away, because
    that is the actual decision. Certainty is the thing being bought.
    """
    mine = tuple(f.plan["mine"])
    greedy = [tuple(c) for c in f.plan["greedy"]]

    f.say("Night two found this. Your probe is still sitting on it.",
          hold=2800)
    f.hover_cell(mine[0], mine[1], ms=900, hold=700)
    f.push_in_on(f.cell(*mine), TOOLTIP, pad=26.0, max_scale=2.4, ms=900,
                 why="the jackpot and the card that prices it")
    f.wait(1900)
    tip = f.tooltip()
    if "765" not in tip:
        f.fails.append(
            f"the pure at {mine} is not showing its 765 in the tooltip: "
            f"{tip!r} — this film's whole argument is that number"
        )
    f.say("PURE. 255 x 3.0 = 765, in one square.", hold=3000)
    f.pull_out()

    f.say("And a seam behind it \u2014 five more squares, 1050 between them",
          hold=3000)
    for c in greedy[:3]:
        f.hover_cell(c[0], c[1], ms=420, hold=420)
    f.park()

    f.say("So the greedy line banks 1815. It also keeps you on the "
          "surface for seven hours.", hold=3600)
    f.say("And for every one of them, that 765 is still on the board, "
          "where anyone who can see it can land on it.", hold=3800)

    f.say("SMASH AND GRAB: land ON the pure, and leave.", hold=2800)
    f.click('.cc-fleet-row .cc-fleet-verb', before=460, after=460)
    f.click(f.cell(*mine), before=560, after=860, ms=720)
    f.escape(after=300)
    lift = '.cc-fleet-row .cc-fleet-verb:has-text("LIFT")'
    if not f.pg.locator(lift).count():
        f.fails.append("no LIFT verb after the landing — no smash, no grab")
        return
    f.click(lift, before=420, after=780)
    f.expect_slots(2, "a landing and a lift, and nothing else")

    f.praxis(cues=[
        ("H01", "Landing harvests the square it lands on. 765, banked.",
         700, [mine]),
        ("H02", "Hour two \u2014 and it is off the planet.", 900),
        ("AURORA", "Two hours. One parcel. Five slots of hold you never "
         "opened.", 700),
    ])
    f.pull_out()
    f.say("You walked away from 1050 of good red. That is the price.",
          hold=3200)
    f.say("What you bought is CERTAINTY \u2014 ore in the hold cannot be "
          "harvested, contested or found by anybody.", hold=3800)
    f.say("Two things still beat it. CHAFF on your lift hour: no ride "
          "home, and dawn takes the hull and the hold with it.", hold=3800)
    f.say("Or a House with its own eye on that square, dropping into it "
          "the same hour. Then nobody lands and both hulls come home "
          "damaged.", hold=4000)
    f.say(None)
    f.wait(600)


@film("adv_blind_grab")
def _adv_blind_grab(f: Film, base: str, sid: str) -> None:
    """Night three: attack a jackpot you cannot see.

    The counterpart to smash-and-grab, and the harder sell, because it
    does not get the pure. What it gets is everything around getting
    the pure: their eye is out so they cannot land on it either, five
    squares of ordinary red are in the hold, and the probe that led the
    harvester in has lit the exact square for tomorrow.

    The trap this film exists to show is friendly fire. The obvious
    salvo — three diamonds centred on the beacon — denies the harvest
    on every square you were going to walk (§4.9.3). So the wall goes
    UNDER the seam: it still catches their probe on the diamond's top
    point, and leaves the northern approach clean.
    """
    theirs = tuple(f.plan["theirs"])
    salvo = [tuple(c) for c in f.plan["blind_salvo"]]
    eye = tuple(f.plan["blind_eye"])
    land = tuple(f.plan["blind_land"])
    comb = [tuple(c) for c in f.plan["blind_comb"]]

    f.say("Their beacon. You have never been within four squares of it.",
          hold=3000)
    f.hover_cell(land[0], land[1], ms=900, hold=1400)
    f.say("Fog. No tier, no purity, no number \u2014 the smear says a pure "
          "is around HERE and nothing else.", hold=3600)
    f.park()

    f.say("First: take the eye that lit it", hold=2600)
    f.click(_adv_deploy(f, "EMP"), before=520, after=520)
    for c in salvo:
        f.click(f.cell(c[0], c[1]), before=300, after=520, ms=420)
    f.escape(after=400)
    f.say("UNDER the seam, not over it. Your own cloud denies your own "
          "harvest \u2014 there is no friendly fire switch.", hold=3800)

    f.say("Then an eye of your own, on the northern edge")
    f.click('.cc-deploy-btn:has-text("LAUNCH PROBE")', before=460, after=420)
    f.click(f.cell(eye[0], eye[1]), before=520, after=820, ms=660)
    f.escape(after=300)

    f.say("And the harvester \u2014 queued NOW, before that probe has told "
          "you one thing", hold=3600)
    f.click('.cc-fleet-row .cc-fleet-verb', before=460, after=460)
    f.click(f.cell(*land), before=560, after=860, ms=720)
    f.say("Five steps, picked off a smear. This is the blind part.",
          hold=2600)
    for c in comb:
        f.click(f.cell(c[0], c[1]), before=220, after=460, ms=360)
    f.escape(after=300)
    lift = '.cc-fleet-row .cc-fleet-verb:has-text("LIFT")'
    if not f.pg.locator(lift).count():
        f.fails.append("no LIFT verb after the blind landing")
        return
    f.click(lift, before=420, after=780)
    f.expect_slots(9, "salvo, probe, blind landing, five steps and a lift")

    f.praxis(cues=[
        ("H01", "Their probe is gone. The beacon stays lit \u2014 and now "
         "only one House can look at it.", 900, salvo + [theirs]),
        ("H02", "Your eye opens on the edge of the smear", 700, [eye]),
        ("H03", "And the landing you already committed to", 800, [land]),
        ("H07", "Combing: 220, 129, 184, 124, 121 \u2014 and one bare "
         "square, because you were guessing.", 800),
        ("AURORA", "980 in the vault. Not a jackpot.", 700),
    ])
    f.pull_out()

    f.say("You missed the pure. Look what you bought anyway.", hold=3000)
    f.hover_cell(theirs[0], theirs[1], ms=900, hold=700)
    f.push_in_on(f.cell(*theirs), TOOLTIP, pad=26.0, max_scale=2.4, ms=900,
                 why="the seam the comb walked past, now readable")
    f.wait(1900)
    tip = f.tooltip()
    if "255" not in tip:
        f.fails.append(
            f"the rival jackpot at {theirs} did not come into vision: "
            f"{tip!r} — the pay-off of this film is being able to read it"
        )
    f.say("Your probe lit it on the way past. You know the square now.",
          hold=3200)
    f.pull_out()
    f.say("Blind and grab is not a jackpot play. It is a TEMPO play.",
          hold=2800)
    f.say("Their eye is out, so they cannot land on it either. You have "
          "980, the exact square, and the whole of tomorrow.", hold=4000)
    f.say(None)
    f.wait(600)


# ── the walk-in, in two takes ───────────────────────────────────────
#
# One night cannot show a timing rule and its consequence, so this is
# spliced: the same salvo, the same harvester, the same five squares,
# walked impatiently and then patiently. Both takes were run headless
# first (_probe_tactics.py) and the gap is not rhetorical — rushing
# banks literally nothing, because the cloud does not merely deny the
# harvest, it freezes the hull for four hours.
SPLICES["adv_emp"] = ["adv_emp_rush", "adv_emp_wait"]


def _emp_open(f: Film, salvo: List[tuple], theirs: tuple) -> None:
    """The half both takes share: fire the wall, park a hull outside."""
    f.say("One EMP launch is a salvo of three")
    f.click(_adv_deploy(f, "EMP"), before=520, after=520)
    f.say("Each missile is a radius-2 diamond. Overlap them and you get "
          "a wall, not three puddles.", hold=2600)
    for c in salvo:
        f.click(f.cell(c[0], c[1]), before=300, after=520, ms=420)
    f.escape(after=400)


@film("adv_emp_rush")
def _adv_emp_rush(f: Film, base: str, sid: str) -> None:
    """Take one: everything right except the clock."""
    theirs = tuple(f.plan["theirs"])
    salvo = [tuple(c) for c in f.plan["salvo"]]
    beside = tuple(f.plan["beside"])
    walk = [tuple(c) for c in f.plan["walk_in"]]

    f.say("The other House has an eye on their jackpot", hold=2400)
    f.hover_cell(theirs[0], theirs[1], ms=900, hold=1500)
    f.park()
    _emp_open(f, salvo, theirs)

    f.say("Eight hours of cloud over their seam. Now go and take it.",
          hold=3000)
    f.click('.cc-deploy-btn:has-text("LAUNCH PROBE")', before=460, after=420)
    f.click(f.cell(beside[0], beside[1]), before=520, after=800, ms=640)
    f.escape(after=300)
    f.click('.cc-fleet-row .cc-fleet-verb', before=460, after=460)
    f.click(f.cell(beside[0], beside[1]), before=520, after=820, ms=660)
    f.say("Land clear of the wall, then walk straight in. Five squares, "
          "ending on their 255.", hold=3400)
    for c in walk:
        f.click(f.cell(c[0], c[1]), before=220, after=440, ms=340)
    f.escape(after=300)
    lift = '.cc-fleet-row .cc-fleet-verb:has-text("LIFT")'
    if f.pg.locator(lift).count():
        f.click(lift, before=380, after=700)
    f.expect_slots(9, "salvo, probe, landing, five steps and a lift")

    f.praxis(cues=[
        ("H01", "Three missiles, one wall", 500, salvo + [theirs]),
        ("H03", "You land one square outside it", 700, [beside]),
        ("H04", "First step in \u2014 and the cloud denies the harvest",
         900, [walk[0]]),
        ("H07", "Disabled. Inside a live cloud a hull does not act at "
         "all.", 900),
        ("AURORA", "Lifted on hour nine with an empty hold.", 700),
    ])
    f.pull_out()
    f.say("Nothing. Not a reduced haul \u2014 nothing.", hold=2800)
    f.say("You spent the EMP, the probe and the whole night walking "
          "through your own weapon.", hold=3600)
    f.say(None)
    f.wait(700)


@film("adv_emp_wait")
def _adv_emp_wait(f: Film, base: str, sid: str) -> None:
    """Take two: the same night, played on the clock you set."""
    theirs = tuple(f.plan["theirs"])
    salvo = [tuple(c) for c in f.plan["salvo"]]
    beside = tuple(f.plan["beside"])
    walk = [tuple(c) for c in f.plan["walk_in"]]

    f.say("Again. Same salvo, same hull, same five squares.", hold=2800)
    _emp_open(f, salvo, theirs)

    f.say("The cloud is live from hour one to hour EIGHT. You know that "
          "because you set it.", hold=3600)
    f.click('.cc-deploy-btn:has-text("LAUNCH PROBE")', before=460, after=420)
    f.click(f.cell(beside[0], beside[1]), before=520, after=800, ms=640)
    f.escape(after=300)

    # The waits go in FRONT of the landing rather than between it and
    # the walk. Same hours either way — the hull is off the board until
    # hour eight and steps in on nine — but this keeps drop, walk and
    # lift in one uninterrupted picker session, which is the sequence
    # the panel is built for.
    f.say("So spend the hours first. Five of them, on nothing.",
          hold=3000)
    for _ in range(5):
        f.click('.cc-deploy-btn:has-text("WAIT")', before=220, after=320)
    f.say("Five hours of a 21-hour night, burned on purpose", hold=2800)

    f.say("Now land, and walk in behind the clock", hold=2600)
    f.click('.cc-fleet-row .cc-fleet-verb', before=460, after=460)
    f.click(f.cell(beside[0], beside[1]), before=520, after=820, ms=660)
    for c in walk:
        f.click(f.cell(c[0], c[1]), before=220, after=440, ms=340)
    f.escape(after=300)
    lift = '.cc-fleet-row .cc-fleet-verb:has-text("LIFT")'
    if f.pg.locator(lift).count():
        f.click(lift, before=380, after=700)
    f.expect_slots(14, "salvo, probe, landing, five waits, five steps, lift")

    f.praxis(cues=[
        ("H01", "Three missiles, one wall", 500, salvo + [theirs]),
        ("H05", "Nothing happening, deliberately", 800),
        ("H08", "Landing outside the wall on the hour it expires", 900,
         [beside]),
        ("H09", "Hour nine. The cloud is gone, and you step in.", 1000,
         [walk[0]]),
        ("H12", "Harvesting all the way down the row", 800),
        ("H13", "And the last square is their jackpot. 765.", 1100,
         [theirs]),
        ("AURORA", "1078 banked, off a seam they lit and never reached.",
         700),
    ])
    f.pull_out()
    f.say("They could read that timer too. The difference is you were "
          "already standing next to it when it ran out.", hold=4000)
    f.say("EMP does not take the red. It takes the CLOCK \u2014 and the "
          "clock is only worth having if you wait for it.", hold=4000)
    f.say(None)
    f.wait(600)


@film("adv_snap")
def _adv_snap(f: Film, base: str, sid: str) -> None:
    """Night three: one round, one square, and a landing that never lands.

    The claim this film has to earn is a timing claim, and timing is the
    hardest thing to photograph. So it is shot as a refusal: the rival
    has already queued a drop onto their own jackpot, resting on the eye
    they put down last night. The camera watches the SNAP take the eye
    and the drop come back refused IN THE SAME HOUR — which an EMP on
    the same square in the same hour could not do, because it resolves
    below the hour's vision note and SNAP resolves above it.

    The rival's night is on the books before the camera rolls (see
    ``_setup_advanced``), so what plays out is genuinely their plan
    being broken rather than a staged absence.
    """
    theirs = tuple(f.plan["theirs"])

    f.say("Their jackpot, their eye on it since last night.", hold=2800)
    f.hover_cell(theirs[0], theirs[1], ms=900, hold=1400)
    f.say("And a harvester of theirs in orbit. You do not need to guess "
          "what happens next \u2014 there is only one thing to do with "
          "that.", hold=3800)
    f.park()

    f.say("You cannot outrun a drop. You can arrive before it.", hold=3000)
    f.click(_adv_deploy(f, "SNAP"), before=520, after=520)
    f.click(f.cell(theirs[0], theirs[1]), before=420, after=860, ms=660)
    f.escape(after=400)
    f.expect_slots(1, "one round, on the square their eye is standing on")

    f.say("One missile. One square. Aimed at the PROBE, not the "
          "harvester.", hold=3400)

    f.praxis(cues=[
        ("H01", "The round lands first \u2014 before the board takes its "
         "note of who can see what.", 1100, [theirs]),
        ("H01", "Their eye is gone. So on this hour, that square was "
         "never lit \u2014 and the drop resting on it is REFUSED.",
         1200, [theirs]),
        ("AURORA", "Their harvester is still in orbit. The jackpot is "
         "still on the board.", 800),
    ])
    f.pull_out()

    # The contrast is the entire lesson, and it is a rule rather than a
    # dice roll, so the film states it flatly rather than shooting a
    # second take that would only show the same cell twice.
    f.say("An EMP on that square, on that hour, does NOT do this.",
          hold=3400)
    f.say("It resolves under the hour's note. The board has already "
          "written down that the square was lit, so the landing stands "
          "and you have paid 200 to kill a probe that had already done "
          "its job.", hold=4600)
    f.say("SNAP is the only thing on the board that moves before the "
          "note is taken. That beat is what the hundred buys.", hold=4000)

    f.say("It has a second use, if you would rather gamble.", hold=2600)
    f.say("The square stays hot for the rest of that hour. Anything that "
          "walks or lands into it is damaged \u2014 no harvest that turn, "
          "and 500 credits to put right.", hold=4400)
    f.say("Guess the square they are dropping onto and you take their "
          "whole night, not one hour of it.", hold=3400)
    f.say(None)
    f.wait(600)


@film("adv_buy_snap", ready='[data-orbit-action="build_snap"]')
def _adv_buy_snap(f: Film, base: str, sid: str) -> None:
    """Orbit three: the cheap weapon, and why cheap is not weak (v1.36).

    Deliberately a SHORT film. SNAP does almost nothing you can point a
    camera at — one missile, one square, one hour — and the temptation
    is to pad that with the mechanic, which belongs in ``adv_snap`` on
    the night it is fired. This film has one job: establish the price
    ladder, so that the number on the buy button reads as "the cheap
    one" rather than as an arbitrary hundred.
    """
    f.say("Three weapons, and they are priced 1, 2, 3.", hold=2600)

    for action, blurb in (
        ("build_snap", "SNAP \u2014 100"),
        ("build_emp", "EMP \u2014 200"),
        ("build_chaff", "chaff \u2014 300"),
    ):
        sel = f'[data-orbit-cost="{action}"]'
        if f.pg.locator(sel).count():
            f.say(blurb, hold=1500)
            f.point_near(sel, dx=0, dy=-26, ms=560)
            f.wait(1100)
    f.say(None)

    f.say("Your rack holds 600. So the ladder is really six pips, and "
          "these are one, two and three of them.", hold=3800)

    f.say("You have 50 blue left of the 250 you started with \u2014 the EMP "
          "took the rest. Only one of the three is even reachable.",
          hold=4000)
    f.say("This turn the training range has credited you 100, exactly "
          "one round. A real season mines it.", hold=3400)

    f.click('[data-orbit-action="build_snap"]', before=520, after=900)
    f.wait(700)
    if f.pg.locator("#cc-orbit-blue-proj").count():
        f.point_near("#cc-orbit-blue-proj", dx=0, dy=-26, ms=700)
        f.wait(1500)

    f.say("A hundred blue buys one square of one hour. That sounds like "
          "nothing.", hold=3200)
    f.say("What it actually buys is being FIRST. Tomorrow night is where "
          "that turns out to matter.", hold=3600)
    f.say(None)
    f.wait(500)


@film("adv_buy_chaff", ready='[data-orbit-action="build_chaff"]')
def _adv_buy_chaff(f: Film, base: str, sid: str) -> None:
    """Orbit three: a second hull, and the dearest thing on the board."""
    f.say("Two seams need two harvesters", hold=2200)
    f.click('[data-orbit-action="build_harvester"]', before=520, after=860)

    cost = '[data-orbit-cost="build_chaff"]'
    if f.pg.locator(cost).count():
        f.say("Chaff costs no credits at all \u2014 and 300 blue")
        f.point_near(cost, dx=0, dy=-26, ms=700)
        f.wait(2000)
    f.say("That is more than the 250 you start a season with, and you "
          "have spent 300 of it already. Chaff is not something you can "
          "buy \u2014 it is something you mine for.", hold=3800)

    # v1.34 — the training subsidy. The engine grants one chaff's worth
    # of blue at this orbit's open (``award_tutorial_blue_topup``), so
    # the buy now succeeds where it used to be a lesson in going
    # without. The film says where the blue came from: a gift presented
    # as a windfall teaches the wrong economy.
    f.say("This turn the training range has credited you 300 \u2014 one "
          "flare, once. A real season mines it.", hold=3400)

    f.click('[data-orbit-action="build_chaff"]', before=520, after=900)
    f.wait(900)
    if f.pg.locator("#cc-orbit-blue-proj").count():
        f.point_near("#cc-orbit-blue-proj", dx=0, dy=-26, ms=700)
        f.wait(1600)
    f.say("Spend it. What it buys is three hours in which nobody moves.",
          hold=3000)
    f.say(None)
    f.wait(500)


@film("adv_arms_bar", ready='[data-orbit-action="build_chaff"]')
def _adv_arms_bar(f: Film, base: str, sid: str) -> None:
    """Orbit three, second reel: weaponised blue is public (§4.9.8).

    Shot on the same turn as ``adv_buy_chaff`` and deliberately not
    merged into it. That film is about what a flare costs; this one is
    about what buying it TELLS everybody, which is a different lesson
    and lands better after the purchase than during it.

    The beat this exists to capture cannot be staged in the Orbit panel:
    the rack does not move until the orbit RESOLVES, so the blue-to-cyan
    flight happens on the commit. Hence buy, commit, then push in on the
    hull while the pips light.
    """
    f.say("One more thing about that flare, and it is the important one.",
          hold=2800)

    f.click('[data-orbit-action="build_chaff"]', before=520, after=900)
    f.say("Watch the station, not the panel.", hold=2200)

    # A phase resolves only once EVERY seat has committed, and on this
    # duel board nobody is flying the rival. Without this the film sits
    # on "waiting for YELLOW" and the transfer never happens on camera.
    submit_orbit(base, sid, [], "p2")

    # The purchase resolves here — this is the only moment the transfer
    # animation exists, so the camera has to already be on the hull.
    f.commit_orbit()
    f.wait(400)

    arms = '[data-os-arms="p1"]'
    if f.pg.locator(arms).count():
        f.push_in_on(arms, pad=42.0, max_scale=3.0, ms=900,
                     why="the arsenal bar is the subject of this film")
        # A bar of six DIM pips is what an empty rack and a broken data
        # path both look like, and the first cut of this film shot the
        # second one: forty seconds of narration about cyan, over a
        # station with no cyan on it. Six dim pips here is a failure.
        lit = f.pg.eval_on_selector(
            arms, "el => (el.textContent || '').replace(/[^\\u2588]/g, '').length")
        if not lit:
            f.fails.append(
                "p1's arsenal bar is unlit at the moment the film says the "
                f"blue turned cyan (bar reads "
                f"{f.pg.eval_on_selector(arms, 'el => el.title')!r})")
        f.say("The blue did not disappear. It turned CYAN.", hold=2800)
        f.wait(900)
        f.say("Six pips, a hundred blue of ordnance each. That is your "
              "ARSENAL, and it caps at 600.", hold=3800)
        f.point_near(arms, dx=0, dy=-26, ms=760)
        f.wait(1800)
        f.pull_out()
    else:
        # Never silently ship a film of the page background.
        f.fails.append(
            "no arsenal bar on p1's station — the whole subject of "
            "adv_arms_bar is missing from frame")

    f.say("At 600 the buy buttons grey out. You cannot hoard your way "
          "out of a bad position.", hold=3600)

    # The public half. A rival's card carries the same exact figure —
    # that is the claim, so the film has to actually show it rather
    # than assert it over a shot of the player's own station.
    rival = '[data-os-station="p2"]'
    if f.pg.locator(rival).count():
        f.say("And it is not yours alone to know.", hold=2400)
        f.glide(rival, ms=700)
        f.wait(1400)
        f.push_in_on(rival, pad=30.0, max_scale=2.6, ms=900,
                     why="the rival arsenal is the public half of the lesson")
        f.say("Every House reads every arsenal. Exactly \u2014 not a "
              "grade, not a guess.", hold=3600)
        f.wait(1200)
        f.pull_out()

    f.say("Your vault is still a secret. Your weapons never are.",
          hold=3400)
    f.say("Nobody on this board will ever be ambushed by a weapon they "
          "could not have seen coming. Including you.", hold=4000)
    f.say(None)
    f.wait(600)


@film("adv_chaff")
def _adv_chaff(f: Film, base: str, sid: str) -> None:
    """Night four: denial first, and then what denial becomes.

    An earlier cut opened by calling denial "an expensive shrug" on the
    way to the kill, which is wrong and teaches badly. Three hours in
    which no House can land, walk or lift is three hours of a contested
    seam being nobody's — and if you are the one already walking toward
    it, that is the whole seam. Denial is the weapon; most turns it is
    all you need.

    The kill is what the same flare becomes when it is aimed at one
    hour instead of three. The lifter is the only way off the surface,
    a cancelled pickup burns its slot, and dawn takes whatever is still
    standing. That is the advanced move, and it is built on the
    ordinary one.
    """
    grave = tuple(f.plan["grave"])

    f.say("Chaff jams every House for three hours. Yours included.",
          hold=2800)
    f.say("Three hours where nobody lands, nobody walks, nobody lifts. "
          "That is DENIAL, and denial takes seams.", hold=3600)
    f.say("Fire it over a pure nobody has grabbed yet and it is simply "
          "not available \u2014 while you are already walking at it.",
          hold=3800)
    f.say("Most turns that is the whole use, and it is worth the flare "
          "on its own.", hold=3000)
    f.say("Now aim the same three hours at ONE hour \u2014 the hour they "
          "reach for the lifter", hold=3400)

    # Four waits, so the flare goes up on H05 and smothers H05-H07.
    # Three was a hour too early: it ate the rival's last STEP as well,
    # and a harvester that never finished walking dies in the wrong
    # square with the wrong lesson attached — it looks like chaff stops
    # movement, when the thing worth teaching is that it stops the exit.
    f.say("Let them land. Let them work. Let them fill the hold.",
          hold=2600)
    for _ in range(4):
        f.click('.cc-deploy-btn:has-text("WAIT")', before=240, after=340)

    f.say("Flare on the hour they reach for the lifter")
    f.click(_adv_deploy(f, "CHAFF"), before=520, after=900)
    f.say("Three slots: the flare, and two hours of jamming yourself",
          hold=2600)
    f.expect_slots(5, "four waits and a flare")

    f.praxis(cues=[
        ("H02", "They land and start working", 500),
        ("H05", "Flare. Nobody acts for three hours \u2014 you included.",
         700),
        ("H06", "That was their pickup hour. The lifter never came.",
         900, [grave]),
        ("AURORA", "And the dawn wave takes whatever is still standing.",
         800, [grave]),
    ])
    f.wait(1600)

    f.say("A harvester, and everything in its hold", hold=2400)
    f.hover_cell(grave[0], grave[1], ms=800, hold=900)
    f.push_in_on(f.cell(*grave), TOOLTIP, pad=26.0, max_scale=2.5, ms=900,
                 why="the wreck and the card that names it")
    f.wait(1600)
    tip = f.tooltip()
    if "\u2020" not in tip and "lost" not in tip.lower():
        f.fails.append(
            f"no wreck in the tooltip at {grave}: {tip!r} \u2014 the close-up "
            "is pointing at empty ground"
        )
    f.wait(1200)
    f.pull_out()
    f.say("You never touched it. You just took away the ride home.",
          hold=3000)
    f.say("Denial buys you a seam. The lifter buys you the harvester and "
          "everything in it. Same flare \u2014 different hour.", hold=4000)
    f.say(None)
    f.wait(600)


# ── setup: getting a session to the turn a film needs ───────────────

#: Films that need a scriptable rival rather than the bot. Kept as a set
#: so ``setup_for`` routes on membership and nobody has to remember which
#: constructor a film wanted.
DUEL_FILMS = {"basic_crash", "basic_stranded", "basic_score"}


# ── the tier board ──────────────────────────────────────────────────
#
# Every other basic film runs on the batch seed, because none of them
# says a number out loud — "spread your probes" is true on any terrain.
# `basic_drop` is the exception: it prices four named squares and walks
# a named seam, so it pins its board (v1.37).
#
# Found with `_probe_basicseed.py`, which searched for the one thing the
# generator will not promise — one of each RED tier close enough
# together to tour with the cursor, next to a six-square orthogonal RED
# chain (a landing plus five steps, which is exactly the hold). Seed 14
# puts the quartet inside a 2-square spread and ends the chain ON the
# pure, so the walk pays off the lesson that preceded it.
#
# A single probe at BASIC_TIER_EYE covers all eight squares; the second
# probe only goes somewhere else because the reel spends the previous
# card telling players to spread them, and a film that contradicts its
# own card teaches the card is optional.
BASIC_DROP_SEED = 14
#: Deliberately one square SOUTH of the seam rather than on it. A
#: harvester entering a square crushes the probe in it, and this film
#: ends by reading the tooltip of a square the harvester walked over —
#: with the eye on the walk, that last beat plays over echo and the
#: −100 never appears.
BASIC_TIER_EYE = (14, 12)
BASIC_FAR_EYE = (6, 6)

#: (x, y) -> the purity the tooltip must read. Asserted in the film,
#: because a generator change that moves these turns the narration into
#: confident nonsense rather than a failure.
BASIC_TIERS: Dict[str, tuple] = {
    "trace": (13, 11),   # purity  30  x0.75 ->  23
    "vein": (15, 11),    # purity 107  x1.0  -> 107
    "mass": (14, 10),    # purity 203  x1.5  -> 305
    "pure": (14, 11),    # purity 255  x3.0  -> 765
}
BASIC_TIER_PURITY = {"trace": 30, "vein": 107, "mass": 203, "pure": 255}
BASIC_TIER_SCORE = {"trace": 23, "vein": 107, "mass": 305, "pure": 765}

#: Landing plus five steps. Ends on the pure the tour just priced.
BASIC_SEAM = [(12, 10), (13, 10), (13, 9), (14, 9), (14, 10), (14, 11)]


#: Where the rival is sent so it is nowhere near the lesson. The far
#: corner, both nights, and no harvester — see BASIC_TIER_EYE's note on
#: what the lite bot did to the first take of this film.
BASIC_RIVAL_EYE = (3, 2)


def _setup_tiers(base: str) -> tuple[str, Plan]:
    """Night two on the pinned tier board, with the quartet lit.

    A duel rather than the preset, for the reason in BASIC_DROP_SEED's
    note: this seam is the richest thing on the board and the lite bot
    goes for it. The rival's night two is posted here, before the
    camera rolls, because an unsubmitted rival leaves TRANSMIT disabled
    and the film times out on its own commit.
    """
    sid = seed_duel(base, BASIC_DROP_SEED, cap=3)
    submit_night(base, sid, [
        {"a": "probe", "at": list(BASIC_TIER_EYE)},
        {"a": "probe", "at": list(BASIC_FAR_EYE)},
    ], "p1")
    submit_night(base, sid, [{"a": "probe", "at": list(BASIC_RIVAL_EYE)}],
                 "p2")
    submit_orbit(base, sid, [{"a": "build_probe", "count": 2}], "p1")
    submit_orbit(base, sid, [], "p2")
    submit_night(base, sid, [{"a": "probe", "at": list(BASIC_RIVAL_EYE)}],
                 "p2")
    return sid, {
        "tiers": {t: list(c) for t, c in BASIC_TIERS.items()},
        "seam": [list(c) for c in BASIC_SEAM],
    }


def setup_for(base: str, name: str, seed: int) -> tuple[str, Plan]:
    """Create a session, walk it to the state ``name`` films, and say
    which squares it committed the rival to.

    Driven over HTTP rather than by clicking, deliberately: setup is not
    the lesson, and clicking it would put four minutes of it in frame.
    """
    if name in ADVANCED_FILMS:
        return _setup_advanced(base, name)
    if name in DUEL_FILMS:
        return _setup_duel(base, name, seed)
    if name == "basic_drop":
        return _setup_tiers(base)

    sid = seed_game(base, seed)
    st = status(base, sid)

    if name in ("basic_probe", "basic_praxis"):
        # Night one as the player meets it. If the game opens in ORBIT,
        # pass through it empty so the film starts on the planning board.
        if str(st.get("phase", "")).lower().startswith("orbit"):
            submit_orbit(base, sid, [])
        return sid, {}

    if name == "basic_orbit_probes":
        # The first ORBIT the player sees. A session opens on day 1
        # PLANNING, so that is one night away, not zero.
        if str(st.get("phase", "")).lower().startswith("orbit"):
            return sid, {}
        submit_night(base, sid, _probe_opening(base, sid))
        return sid, {}

    if name == "basic_buy_harvester":
        # Orbit two, with a night's takings banked so the harvester is
        # a decision rather than an impossibility.
        submit_night(base, sid, _probe_opening(base, sid))
        submit_orbit(base, sid, [{"a": "build_probe", "count": 2}])
        submit_night(base, sid, _probe_opening(base, sid))
        return sid, {}

    if name == "basic_supersede":
        # The last night, with two nights of play behind it: trails on
        # the board and enough ground lit that "spread your probes" has
        # somewhere to point.
        submit_night(base, sid, _probe_opening(base, sid))
        submit_orbit(base, sid, [{"a": "build_probe", "count": 2}])
        submit_night(base, sid, _probe_opening(base, sid))
        submit_orbit(base, sid, [
            {"a": "build_harvester"},
            {"a": "build_probe", "count": 2},
        ])
        return sid, {}

    raise SystemExit(f"no setup defined for film {name!r}")


# ── setup for the outcome films (both seats human) ──────────────────
#
# Two probes on the SAME tile supersede each other, so the two seats are
# always offset here. Getting that wrong blinds both Houses and the
# resulting film is a drop onto fog that the engine then refuses — with
# every assertion still green, because the orders did reach the queue.

def _duel_probes(w: int, h: int, wave: int) -> tuple[List[dict], List[dict]]:
    """This night's probes for (player, rival), aimed off board extent."""
    my = [(0.42, 0.50), (0.62, 0.50)] if wave == 0 else [(0.46, 0.34), (0.58, 0.66)]
    yours = [(0.50, 0.42), (0.70, 0.58)] if wave == 0 else [(0.54, 0.30), (0.66, 0.62)]
    mk = lambda pts: [{"a": "probe", "at": [int(w * fx), int(h * fy)]}
                      for fx, fy in pts]
    return mk(my), mk(yours)


def _setup_duel(base: str, name: str, seed: int) -> tuple[str, Plan]:
    sid = seed_duel(base, seed)
    v = view(base, sid)
    w, h = int(v["width"]), int(v["height"])

    mine, theirs = _duel_probes(w, h, 0)
    submit_night(base, sid, mine, "p1")
    submit_night(base, sid, theirs, "p2")
    buy: List[dict] = [{"a": "build_probe", "count": 2}]
    submit_orbit(base, sid, buy, "p1")
    submit_orbit(base, sid, buy, "p2")

    mine, theirs = _duel_probes(w, h, 1)
    submit_night(base, sid, mine, "p1")
    submit_night(base, sid, theirs, "p2")

    if name == "basic_crash":
        # Both crash shapes have to fit in ONE night, which needs two
        # harvesters a seat. At 1500c against 1000c a turn that is why
        # this film costs two nights of build-up and the others do not.
        submit_orbit(base, sid, [{"a": "build_harvester"}], "p1")
        submit_orbit(base, sid, [{"a": "build_harvester"}], "p2")
        return sid, _plan_crash(base, sid)

    submit_orbit(base, sid, [{"a": "build_probe", "count": 2}], "p1")
    submit_orbit(base, sid, [{"a": "build_probe", "count": 2}], "p2")
    # The rival stays out of shot for these two: they are about what the
    # player's own orders do, and a second House wandering through frame
    # is just noise the caption has to compete with.
    submit_night(base, sid, [{"a": "probe", "at": [int(w * 0.12),
                                                   int(h * 0.15)]}], "p2")
    if name == "basic_score":
        return sid, _plan_seam(base, sid, steps=3, want_red=True)
    return sid, _plan_seam(base, sid, steps=2, want_red=True)


# ── setup for the Advanced arc ──────────────────────────────────────
#
# One board, one storyline, seven films cut out of different turns of
# it. Each shoot replays the same history from scratch and stops at the
# turn its film opens on, so the reels join up: the blue banked in the
# first film is the blue spent in the second, and the probe fried in the
# fifth is the one the rival launched in the fourth.

#: Advanced films, all of them duels — see ``_setup_advanced``.
ADVANCED_FILMS = {
    "adv_hotdrop", "adv_buy_emp", "adv_redsign", "adv_redsign_rival",
    "adv_smash_grab", "adv_blind_grab", "adv_snap",
    "adv_emp_rush", "adv_emp_wait", "adv_buy_snap", "adv_buy_chaff",
    "adv_arms_bar", "adv_chaff",
}

#: Where in the storyline each film opens. Order matters: the setup
#: replays every earlier turn before handing the session to the camera.
_ADV_TURN = {
    "adv_hotdrop": 0,        # night 1, as the player meets it
    "adv_buy_emp": 1,        # orbit 2
    "adv_redsign": 2,        # night 2
    "adv_redsign_rival": 2,  # night 2, mirrored
    "adv_buy_snap": 3,       # orbit 3
    "adv_buy_chaff": 5,      # orbit 4
    # Same turn as adv_buy_chaff, its own session: this one commits the
    # orbit to catch the blue-to-cyan transfer, which that film must not
    # do (it ends in the panel, mid-purchase).
    "adv_arms_bar": 5,       # orbit 4
    # Three films open on night 3, each a session of its own. They all
    # want the same board — two beacons lit, an EMP in stock, a hull
    # spare — and then do completely different things with it.
    "adv_smash_grab": 4,     # night 3: take the pure you can see
    "adv_blind_grab": 4,     # night 3: take a guess at the one you cannot
    "adv_snap": 4,           # night 3: deny the one they can see
    # Both takes of the spliced walk-in open on the same turn, which is
    # the point — the two halves differ only in what the player does
    # with the hours. `adv_emp` itself is kept here so the headless
    # probe can still stage "night four" by the name humans use.
    #
    # v1.36 moved these from night 3 to night 4. Night 3 became the
    # jackpot fight when SNAP arrived, and `advanced:planning:4` is
    # where the reel plays them — a film carries the day number it was
    # shot on burnt into its own chrome, so a lesson shown on night
    # four has to be SHOT on night four or the header argues with the
    # board behind the player. See `_EMP_TAKES` for what that costs the
    # setup.
    "adv_emp": 6,
    "adv_emp_rush": 6,       # night 4: take the clock, ignore it
    "adv_emp_wait": 6,       # night 4: take the clock, and use it
    "adv_chaff": 6,          # night 4
}

#: The walk-in takes, which need a night the storyline does not
#: otherwise leave them: an unspent EMP, one hull, and a rival holding
#: still. Three branches in ``_setup_advanced`` key off this.
_EMP_TAKES = {"adv_emp", "adv_emp_rush", "adv_emp_wait"}


def _setup_advanced(base: str, name: str) -> tuple[str, Plan]:
    """Replay the Advanced storyline up to the turn ``name`` opens on.

    Both seats are human. Every Advanced lesson is about the OTHER
    House — a beacon they light, a probe you fry, a lift you cancel —
    and the bot will not do any of those on cue.

    The seed is pinned rather than taken from the batch: this board's
    blue pocket and two jackpots are load-bearing (see ``ADVANCED_SEED``),
    and a film of a hot drop onto ground that has no blue under it is a
    film of nothing.

    THE RULE THAT BITES: whenever the camera is going to commit a night,
    the rival's orders for THAT night must already be in. Two human
    seats means the first to transmit is put into "waiting for the other
    House", which locks the button — and locks it in a way that looks
    exactly like a hang, thirty seconds into a shoot that has already
    run a minute. So every branch below that hands over a PLANNING turn
    posts p2's night on the way out.
    """
    stop = _ADV_TURN[name]
    sid = seed_duel(base, ADVANCED_SEED, cap=6, preset="advanced")
    plan: Plan = {
        "sign": list(ADV_SIGN), "land": list(ADV_BLUE_LAND),
        "step": list(ADV_BLUE_STEP), "mine": list(ADV_MINE),
        "theirs": list(ADV_THEIRS), "quiet": list(ADV_QUIET),
        "salvo": [list(c) for c in ADV_SALVO], "beside": list(ADV_BESIDE),
        "greedy": [list(c) for c in ADV_GREEDY],
        "blind_salvo": [list(c) for c in ADV_BLIND_SALVO],
        "blind_eye": list(ADV_BLIND_EYE),
        "blind_land": list(ADV_BLIND_LAND),
        "blind_comb": [list(c) for c in ADV_BLIND_COMB],
        "walk_in": [list(c) for c in ADV_WALK_IN],
    }
    mine_h = harvester_ids(base, sid, "p1")[0]

    def probe(at) -> dict:
        return {"a": "probe", "at": list(at)}

    # ── turn 0 · night 1 — the hot drop that funds everything ────────
    if stop <= 0:
        submit_night(base, sid, [probe(ADV_QUIET)], "p2")
        return sid, plan
    submit_night(base, sid, [
        probe(ADV_SIGN),
        {"a": "drop", "unit": mine_h, "at": list(ADV_BLUE_LAND)},
        {"a": "step", "unit": mine_h, "to": list(ADV_BLUE_STEP)},
        {"a": "pickup", "unit": mine_h},
    ], "p1")
    submit_night(base, sid, [probe(ADV_QUIET)], "p2")

    # ── turn 1 · orbit 2 — blue becomes an EMP ───────────────────────
    if stop <= 1:
        return sid, plan
    submit_orbit(base, sid, [
        {"a": "build_emp", "count": 1},
        {"a": "build_probe", "count": 2},
    ], "p1")
    # The rival buys the harvester here that the chaff film later
    # strands. Buying it later would leave them nothing to lose.
    submit_orbit(base, sid, [
        {"a": "build_probe", "count": 2},
        {"a": "build_harvester"},
    ], "p2")

    # ── turn 2 · night 2 — a beacon each ─────────────────────────────
    if stop <= 2:
        # Both redsign films open here, and they want opposite things of
        # the rival. The mirror film needs their discovery to happen
        # DURING the filmed night, so the camera catches the beacon
        # arriving out of nowhere. The other film needs them to light
        # NOTHING, or two beacons come up at once and "you found it"
        # stops being a sentence about the player.
        if name == "adv_redsign_rival":
            # A WAIT in front so their discovery lands on H02. Their
            # probe on H01 would put the beacon up in the same hour the
            # player's own first probe opens, and the film's one reveal
            # would arrive before anyone had a reason to look.
            submit_night(base, sid,
                         [{"a": "wait"}, probe(ADV_THEIRS)], "p2")
        else:
            submit_night(base, sid, [probe(ADV_QUIET2)], "p2")
        return sid, plan
    submit_night(base, sid, [probe(ADV_MINE)], "p1")
    submit_night(base, sid, [probe(ADV_THEIRS)], "p2")

    # ── turn 3 · orbit 3 — the SNAP ──────────────────────────────────
    # v1.36 — this orbit used to buy the chaff and the second hull.
    # Both moved to turn 5, because the teaching subsidy moved: the
    # engine now grants one SNAP's worth of blue here and one chaff's
    # worth at the next orbit (``TUTORIAL_BLUE_GRANT_WEAPON``). Shooting
    # the chaff buy on this turn would be shooting a purchase the player
    # cannot make until tomorrow — and, since v1.36 priced chaff at 300,
    # one the seat genuinely cannot afford on camera.
    if stop <= 3:
        return sid, plan
    submit_orbit(base, sid, [
        {"a": "build_snap", "count": 1},
        {"a": "build_probe", "count": 2},
    ], "p1")
    submit_orbit(base, sid, [{"a": "build_probe", "count": 2}], "p2")

    # ── turn 4 · night 3 — the salvo ─────────────────────────────────
    if stop <= 4:
        if name == "adv_snap":
            # This one film needs the rival to actually TRY the
            # smash-and-grab, because the lesson is it being refused.
            #
            # They do not probe tonight: the eye they are landing on is
            # the one they put down on night two, which is still alive
            # (three-night lifetime). That matters. The whole point is
            # that a landing rests on a beacon that already exists, and
            # the SNAP arrives above the hour's vision note and takes it
            # away retroactively — so their drop, queued in good faith
            # against a square that WAS lit, is refused on the night.
            # Re-probing would put a second eye on the square and the
            # denial would not be clean.
            their_h = [
                u["id"] for u in view(base, sid, "p2")["units"]
                if u.get("type") == "harvester" and u.get("orbit")
            ]
            if not their_h:
                raise SystemExit(
                    "advanced setup left the rival with no harvester in "
                    "orbit — adv_snap would have nothing to deny"
                )
            submit_night(base, sid, [
                {"a": "drop", "unit": their_h[0], "at": list(ADV_THEIRS)},
                {"a": "pickup", "unit": their_h[0]},
            ], "p2")
        else:
            # The rival's eye has to still be ON the jackpot for the
            # salvo to take it, so they re-probe it this night.
            submit_night(base, sid, [probe(ADV_THEIRS)], "p2")
        return sid, plan
    if name in _EMP_TAKES:
        # The walk-in takes are the one pair shot AFTER this night, so
        # this is the night that must not spend their weapon. A bare
        # WAIT rather than a probe: anything we put on the board here
        # is still on it when the camera rolls, and the film lays its
        # own eye at ADV_BESIDE as part of the lesson.
        submit_night(base, sid, [{"a": "wait"}], "p1")
    else:
        submit_night(base, sid, [
            {"a": "emp_launch", "at": [list(c) for c in ADV_SALVO]},
            probe(ADV_BESIDE),
        ], "p1")
    submit_night(base, sid, [probe(ADV_THEIRS)], "p2")

    # ── turn 5 · orbit 4 — the second hull, and the chaff ────────────
    # The chaff subsidy lands here (v1.36), so this is the first orbit
    # on which a flare is buyable at all.
    if stop <= 5:
        return sid, plan
    if name in _EMP_TAKES:
        # No second hull for the walk-in takes. The film drives "the"
        # harvester by clicking the first fleet row, and a second row
        # makes that selector a coin toss — so these two takes keep the
        # one-hull fleet they were choreographed against. No flare
        # either: it would sit in the DEPLOY panel next to the button
        # the film is pointing at.
        submit_orbit(base, sid, [{"a": "build_probe", "count": 2}], "p1")
    else:
        submit_orbit(base, sid, [
            {"a": "build_chaff", "count": 1},
            {"a": "build_harvester"},
            {"a": "build_probe", "count": 2},
        ], "p1")
    submit_orbit(base, sid, [{"a": "build_probe", "count": 2}], "p2")

    # ── turn 6 · night 4 — the lift that never comes ─────────────────
    if name in _EMP_TAKES:
        # The walk-in wants the opposite of the chaff night: the rival
        # sitting still with a live eye on their own jackpot, so the
        # wall has something to deny and the seam is still there to
        # walk onto when it expires. Giving them the hot drop below
        # would put a hull on the square the player is walking at.
        submit_night(base, sid, [probe(ADV_THEIRS)], "p2")
        return sid, plan

    # Their whole night is posted before the camera rolls: hot drop,
    # two squares of work, then reach for the lifter on H05. Our flare
    # goes up on H04 and smothers H04-H06.
    their_h = [
        u["id"] for u in view(base, sid, "p2")["units"]
        if u.get("type") == "harvester" and u.get("orbit")
    ]
    if not their_h:
        raise SystemExit(
            "advanced setup left the rival with no harvester in orbit — "
            "adv_chaff would have nothing to strand"
        )
    walk = [(ADV_THEIRS[0], ADV_THEIRS[1] - 1),
            (ADV_THEIRS[0] + 1, ADV_THEIRS[1] - 1)]
    submit_night(base, sid, [
        probe(ADV_THEIRS),
        {"a": "drop", "unit": their_h[0], "at": list(ADV_THEIRS)},
        {"a": "step", "unit": their_h[0], "to": list(walk[0])},
        {"a": "step", "unit": their_h[0], "to": list(walk[1])},
        {"a": "pickup", "unit": their_h[0]},
    ], "p2")
    plan["grave"] = list(walk[1])
    return sid, plan


def _plan_seam(base: str, sid: str, steps: int, want_red: bool) -> Plan:
    """A walkable run, richest RED first.

    Picked from the engine's own live set rather than from the rendered
    board, because setup runs before the page exists — and because
    "looks red" and "is live" are different questions.
    """
    live = live_squares(base, sid, "p1")
    if not live:
        raise SystemExit("duel setup left p1 with no live ground to land on")
    scored = sorted(
        live.items(),
        key=lambda kv: -_purity(kv[1]) if want_red else 0,
    )
    for start, _ in scored[:40]:
        run = _walk_run(live, start, steps, prefer_red=want_red)
        if len(run) == steps + 1:
            return {"land": list(run[0]), "walk": [list(c) for c in run[1:]]}
    raise SystemExit(
        f"no run of {steps + 1} adjacent live squares — cannot stage a walk"
    )


def _plan_crash(base: str, sid: str) -> Plan:
    """Pick the three squares both crashes need, then post the rival's night.

    ``same`` is where both Houses land at hour 1. ``meet`` is where the
    rival's second harvester will be standing when the player's walks
    into it. They have to be in BOTH seats' live vision or one of the
    two orders is refused and the film shows half a lesson.
    """
    mine = live_squares(base, sid, "p1")
    yours = live_squares(base, sid, "p2")
    shared = set(mine) & set(yours)
    if len(shared) < 12:
        raise SystemExit(
            f"only {len(shared)} squares lit for both seats — not enough "
            f"shared ground to stage a crash"
        )
    quad = _crash_quad(shared, mine)
    if quad is None:
        raise SystemExit("no adjacent trio in shared vision for the walk-in")
    same, from_mine, meet, from_theirs = quad

    ids_me = harvester_ids(base, sid, "p1")
    ids_you = harvester_ids(base, sid, "p2")
    if len(ids_me) < 2 or len(ids_you) < 2:
        raise SystemExit(
            f"a seat has fewer than two harvesters (p1={ids_me}, p2={ids_you})"
            f" — both crash shapes will not fit in one night"
        )

    # The rival's night, on the books before the camera rolls. Their
    # walk goes in at hour 2 and the player's at hour 3, so the player
    # arrives to find the square already taken — the shape a real player
    # meets, rather than a dead heat.
    submit_night(base, sid, [
        {"a": "drop", "unit": ids_you[0], "at": list(same)},
        {"a": "drop", "unit": ids_you[1], "at": list(from_theirs)},
        {"a": "step", "unit": ids_you[1], "to": list(meet)},
        {"a": "pickup", "unit": ids_you[1]},
    ], "p2")
    return {
        "same_square": list(same),
        "walk_from": list(from_mine),
        "walk_into": list(meet),
        "their_from": list(from_theirs),
    }


#: How far apart the two crash sites must be, in squares. Close enough
#: that one zoomed frame holds both, far enough that they read as two
#: events rather than one pile-up. The first cut put them diagonally
#: adjacent and the second collision looked like debris from the first.
CRASH_SPREAD = (3, 5)


def _crash_quad(shared: set, live: Dict[tuple, dict]):
    """``(same, from_mine, meet, from_theirs)`` — all lit for both seats.

    ``meet`` needs two free neighbours, one for each House to walk in
    from, and has to sit ``CRASH_SPREAD`` away from ``same``.
    """
    lo, hi = CRASH_SPREAD
    ordered = sorted(shared, key=lambda c: -_purity(live.get(c, {})))
    for meet in ordered:
        nbrs = [(meet[0] + dx, meet[1] + dy) for dx, dy in STEP_RING]
        nbrs = [n for n in nbrs if n in shared]
        if len(nbrs) < 2:
            continue
        for same in ordered:
            gap = max(abs(same[0] - meet[0]), abs(same[1] - meet[1]))
            if not (lo <= gap <= hi):
                continue
            spare = [n for n in nbrs if n != same]
            if len(spare) >= 2:
                return same, spare[0], meet, spare[1]
    return None


def _purity(cell: dict) -> int:
    if str(cell.get("tile")) != "RED":
        return -1
    return int(cell.get("purity") or 0)


def _walk_run(live: Dict[tuple, dict], start: tuple, n: int,
              prefer_red: bool) -> List[tuple]:
    out = [start]
    used = {start}
    cur = start
    for _ in range(n):
        best = None
        for dx, dy in STEP_RING:
            c = (cur[0] + dx, cur[1] + dy)
            if c in used or c not in live:
                continue
            p = _purity(live[c])
            if prefer_red and p < 0:
                continue
            if best is None or p > best[1]:
                best = (c, p)
        if best is None:
            break
        used.add(best[0])
        out.append(best[0])
        cur = best[0]
    return out


def _probe_opening(base: str, sid: str) -> List[dict]:
    """Two probes into the middle of the board — the honest first turn.

    Aimed off the board's real extent rather than at literal coordinates,
    so a change to the Basic board size moves the probes with it instead
    of silently putting them in a corner.
    """
    v = view(base, sid)
    w = int(v.get("width") or 24)
    h = int(v.get("height") or 16)
    return [
        {"a": "probe", "at": [int(w * 0.35), int(h * 0.45)]},
        {"a": "probe", "at": [int(w * 0.65), int(h * 0.55)]},
    ]


# ── shoot ───────────────────────────────────────────────────────────

def _ramp(kfs: List[Dict[str, float]], key: str, v0: float,
          mul: float = 1.0) -> str:
    """A piecewise ffmpeg expression through the keyframes for one value.

    Between keyframes the value is a CONSTANT — the previous target —
    which is what keeps this linear in the number of keyframes. Writing
    it as "previous expression, then ramp from it" instead doubles the
    string at every step and a seven-keyframe film produces a filter
    megabytes wide.

    ``T`` is output time, spliced in by the caller.
    """
    if not kfs:
        return f"{v0 * mul:.3f}"
    last = kfs[-1][key] * mul
    expr = f"{last:.3f}"
    for i in range(len(kfs) - 1, -1, -1):
        t = kfs[i]["t"]
        d = max(0.001, kfs[i]["ms"] / 1000.0)
        v = kfs[i][key] * mul
        prev = (v0 if i == 0 else kfs[i - 1][key]) * mul
        # Smoothstep, so the move eases in and out rather than jerking
        # into motion — the CSS transition it replaces was a
        # cubic-bezier for the same reason.
        p = f"clip((T-{t:.3f})/{d:.4f},0,1)"
        s = f"({p}*{p}*(3-2*{p}))"
        ramp = f"({prev:.3f}+({v:.3f}-{prev:.3f})*{s})"
        if i == len(kfs) - 1:
            expr = f"if(lt(T,{t + d:.3f}),{ramp},{v:.3f})"
        else:
            nxt = kfs[i + 1]["t"]
            expr = (f"if(lt(T,{t + d:.3f}),{ramp},"
                    f"if(lt(T,{nxt:.3f}),{v:.3f},{expr}))")
        if i == 0:
            expr = f"if(lt(T,{t:.3f}),{prev:.3f},{expr})"
    return expr


def _camera_filter(cam: List[Dict[str, float]], start: float,
                   vw: int, vh: int, fps: int = 25) -> Optional[str]:
    """Turn the recorded keyframes into one ``zoompan`` filter.

    ``zoompan`` and not ``crop`` because crop evaluates its WIDTH and
    HEIGHT once, at configuration — only x and y are per-frame. A crop
    that changes size over time is therefore not expressible, which is
    exactly what a push-in is.

    Keyframes are in CSS pixels against ``VIEWPORT``; the recording is
    at a higher device scale, so everything is multiplied up to video
    pixels here rather than at record time. Measuring in CSS px is what
    lets the shoot resolution change without touching a single film.
    """
    kfs = sorted(({"t": k["t"] - start, "ms": k["ms"], "z": k["z"],
                   "cx": k["cx"], "cy": k["cy"]} for k in cam),
                 key=lambda k: k["t"])
    # Anything wholly before the cut sets the opening state instead.
    z0, cx0, cy0 = 1.0, VIEWPORT["width"] / 2.0, VIEWPORT["height"] / 2.0
    live: List[Dict[str, float]] = []
    for k in kfs:
        if k["t"] + k["ms"] / 1000.0 <= 0:
            z0, cx0, cy0 = k["z"], k["cx"], k["cy"]
        else:
            live.append(k)
    if not live and z0 == 1.0:
        return None

    sx = vw / float(VIEWPORT["width"])
    sy = vh / float(VIEWPORT["height"])
    z = _ramp(live, "z", z0)
    cx = _ramp(live, "cx", cx0, mul=sx)
    cy = _ramp(live, "cy", cy0, mul=sy)
    tv = f"({'on'}/{fps})"
    z, cx, cy = (e.replace("T", tv) for e in (z, cx, cy))
    # x/y are the TOP-LEFT of the window in input pixels, and the window
    # is iw/zoom wide — so clamp against the frame or the crop walks off
    # the edge and ffmpeg clips it to a black bar.
    x = f"clip(({cx})-(iw/zoom)/2,0,iw-iw/zoom)"
    y = f"clip(({cy})-(ih/zoom)/2,0,ih-ih/zoom)"
    return (f"zoompan=z='max(1,{z})':x='{x}':y='{y}'"
            f":d=1:s={VIEWPORT['width']}x{VIEWPORT['height']}:fps={fps}")


def _splice(whole: str, parts: List[str]) -> str:
    """Join finished takes into the delivered clip, and bin the takes.

    A stream copy, not a re-encode: every take came out of ``_post``
    with the same codec, size and rate, so there is nothing to
    reconcile and a second encode would only cost quality. If the copy
    is refused the join is wrong in a way worth hearing about rather
    than papering over, so it reports instead of falling back.
    """
    srcs = [OUT / f"{p}.webm" for p in parts]
    missing = [s.name for s in srcs if not s.exists()]
    if missing:
        return f"take(s) not on disk: {missing}"

    listing = OUT / f"{whole}.parts.txt"
    listing.write_text("".join(f"file '{s.resolve()}'\n" for s in srcs))
    out = OUT / f"{whole}.webm"
    tmp = OUT / f"{whole}.joining.webm"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat",
           "-safe", "0", "-i", str(listing), "-c", "copy", str(tmp)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", b"") or b""
        listing.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
        return f"concat refused: {detail.decode(errors='replace')[:200]}"

    out.unlink(missing_ok=True)
    tmp.rename(out)
    listing.unlink(missing_ok=True)
    for s in srcs:
        s.unlink(missing_ok=True)
    kb = out.stat().st_size / 1024
    print(f"  {whole:24s} {'':5s}  {kb:6.0f} KB  {out.name}  "
          f"(joined {len(parts)} takes)")
    return ""


#: The voice to shoot for, or None for a silent film. Set by ``--voice``.
#: When set, every stretchable caption waits for its narration, so the
#: delivered film is as long as the words take (v1.47).
VOICE: Optional[str] = None

#: Silence left after a spoken line before the film moves on. Matches
#: ``voice/narrate.py`` — imported rather than copied so the two cannot
#: disagree about how long a beat is.
_voice_holds: Dict[str, int] = {}


def _voice_hold_ms(text: str) -> int:
    """How long the picture must dwell for this line to be heard.

    Cached per shoot because a caption can repeat, and rendering is the
    slow part. Failure is soft on purpose: a voice that will not render
    should cost a silent film, not a dead batch two hours into a shoot.
    """
    if text not in _voice_holds:
        try:
            sys.path.insert(0, str(ROOT))
            from backstage.films.voice import narrate
            _, secs = narrate.render(text, VOICE or narrate.DEFAULT_VOICE)
            _voice_holds[text] = int((secs + narrate.TAIL_S) * 1000)
        except Exception as exc:  # noqa: BLE001
            print(f"  (voice unavailable for {text[:32]!r}: {exc})")
            _voice_holds[text] = 0
    return _voice_holds[text]


#: Where cue sheets land. Not in ``OUT`` — that directory is SERVED, and
#: a sidecar JSON beside the films would be shipped to players for no
#: reason. Gitignored: a cue sheet is a measurement of one shoot, and it
#: is only meaningful for the film that shoot produced.
CUES = pathlib.Path(__file__).resolve().parent / "voice" / "build"


def _write_cues(name: str, cues: List[Dict[str, Any]], start: float) -> None:
    """Caption timings for the DELIVERED film, boot-trim already removed.

    Shifted here rather than at read time so the file needs no context to
    interpret: ``t`` is seconds into the .webm a viewer watches. A line
    the trim swallowed is dropped, because it is not in the film.
    """
    rows = [
        {"t": round(c["t"] - start, 3), "text": c["text"]}
        for c in cues if c["t"] - start >= 0
    ]
    CUES.mkdir(parents=True, exist_ok=True)
    (CUES / f"{name}.cues.json").write_text(
        json.dumps({"film": name, "cues": rows}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _trim_start(lead: float) -> float:
    """Where the delivered film starts, in recording time.

    Its own function because three things now measure from it — the
    trim, the camera keyframes and the narration cues — and a copy of
    ``lead - 0.35`` in any of them would put the voice a third of a
    second off the picture with nothing to catch it (v1.47).
    """
    return max(0.0, lead - 0.35)  # keep a beat of black, then the fade


def _post(final: pathlib.Path, lead: float, trim: bool,
          cam: Optional[List[Dict[str, float]]] = None) -> str:
    """Cut the boot off the front, apply the camera, and re-encode.

    Three problems, one pass. The recording starts when the page is
    created, so every film opens on several seconds of curtain — dead
    weight in a clip the modal LOOPS. The push-ins recorded during the
    shoot have to be cropped in. And Playwright's raw webm is ~3x larger
    than it needs to be for a near-static UI, which matters because
    these ship in the repo and load in a modal.

    The trim and the camera share a clock origin (see ``Film.t0``), so
    a keyframe's output time is just its offset minus the cut.

    Best-effort: no ffmpeg, or a failed encode, leaves the raw file in
    place. A slightly long film is worth having; no film is not.
    """
    if not trim:
        return ""
    start = _trim_start(lead)
    vw, vh = _dims(final)
    zoom = _camera_filter(cam or [], start, vw, vh) if cam else None
    tmp = final.with_suffix(".post.webm")
    script = final.with_suffix(".filter.txt")
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-ss", f"{start:.2f}", "-i", str(final)]
    if zoom:
        # Via a file: a seven-keyframe camera is tens of kilobytes of
        # expression and long enough to trip the argument limit.
        script.write_text(zoom)
        cmd += ["-filter_script:v", str(script)]
    elif (vw, vh) != (VIEWPORT["width"], VIEWPORT["height"]):
        cmd += ["-vf", f"scale={VIEWPORT['width']}:{VIEWPORT['height']}"]
    cmd += [
        "-c:v", "libvpx", "-crf", "32", "-b:v", "0",
        "-qmin", "4", "-qmax", "44",
        "-deadline", "good", "-cpu-used", "2", "-an", str(tmp),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        tmp.unlink(missing_ok=True)
        script.unlink(missing_ok=True)
        err = getattr(exc, "stderr", b"") or b""
        tail = err.decode(errors="replace").strip().splitlines()[-1:] or [""]
        return f"  (raw — post pass failed: {tail[0][:90]})"
    script.unlink(missing_ok=True)
    if tmp.stat().st_size < 20_000:
        tmp.unlink(missing_ok=True)
        return "  (raw — post-pass output looked empty)"
    final.unlink()
    tmp.rename(final)
    moves = len(cam or [])
    return f"  (-{start:.1f}s boot{f', {moves} camera move(s)' if moves else ''})"


def _dims(path: pathlib.Path) -> tuple[int, int]:
    """The recording's real pixel size, so the camera can scale to it."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0",
             str(path)], check=True, capture_output=True, timeout=60)
        w, h = out.stdout.decode().strip().split(",")[:2]
        return int(w), int(h)
    except (OSError, subprocess.SubprocessError, ValueError):
        return VIEWPORT["width"], VIEWPORT["height"]


def shoot(base: str, name: str, seed: int, keep_raw: bool,
          trim: bool = True) -> tuple[bool, str]:
    fn = FILMS[name]
    sid, plan = setup_for(base, name, seed)
    if plan:
        print(f"  {name:24s} staged {json.dumps(plan)}")
    OUT.mkdir(parents=True, exist_ok=True)
    # OUT is a SERVED directory. Playwright names its raw capture after
    # the page, and a shoot that dies before the rename leaves that file
    # behind — where it would otherwise get committed and shipped.
    for stray in OUT.glob("page@*.webm"):
        stray.unlink(missing_ok=True)

    fails: List[str] = []
    t0 = time.time()
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        ctx = br.new_context(
            viewport=VIEWPORT,
            record_video_dir=str(OUT),
            record_video_size=VIEWPORT,
        )
        ctx.add_init_script(_CURTAIN_INIT)
        pg = ctx.new_page()
        # Recording starts here, so this is frame zero. Everything until
        # the curtain lifts is boot, and gets cut in the post-pass.
        t_page = time.monotonic()
        lead = 0.0
        errors: List[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))

        f = Film(pg, plan)
        f.t0 = t_page  # keyframes and the boot-trim share this origin
        try:
            pg.goto(f"{base}/?session={sid}&player=p1", wait_until="networkidle")
            pg.wait_for_selector(READY[name], state="visible", timeout=25000)
            pg.wait_for_timeout(1200)
            f.kit()
            pg.evaluate("() => window.__film.put(640, 720)")
            lead = time.monotonic() - t_page
            f.raise_curtain()
            fn(f, base, sid)
        except Exception as exc:  # noqa: BLE001 — report, don't kill the batch
            fails.append(f"{name} raised {type(exc).__name__}: {exc}")
        fails.extend(f.fails)
        if errors:
            fails.append(f"page errors: {errors[:3]}")

        raw = pg.video.path()
        ctx.close()  # the video is only finalised on context close
        br.close()

    secs = time.time() - t0
    final = OUT / f"{name}.webm"
    src = pathlib.Path(raw)
    if src.exists():
        if final.exists():
            final.unlink()
        if keep_raw:
            final.write_bytes(src.read_bytes())
        else:
            src.rename(final)
        note = _post(final, lead, trim, f.cam)
        _write_cues(name, f.cues, _trim_start(lead) if trim else 0.0)
        kb = final.stat().st_size / 1024
        print(f"  {name:24s} {secs:5.1f}s  {kb:6.0f} KB  {final.name}{note}")
    else:
        fails.append(f"playwright reported a video at {raw} and it is not there")

    for msg in fails:
        print(f"  FAIL [{name}] {msg}")
    return (not fails), name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8022")
    ap.add_argument("--only", action="append", default=None,
                    help="shoot just this film (repeatable)")
    ap.add_argument("--seed", type=int, default=20260828)
    ap.add_argument("--keep-raw", action="store_true")
    ap.add_argument("--no-trim", action="store_true",
                    help="skip the ffmpeg boot-trim / re-encode pass")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--voice", nargs="?", const="", default=None,
                    metavar="NAME",
                    help="shoot for narration: every stretchable caption "
                         "waits for its spoken line, so the film comes out "
                         "as long as the words take. Mux the audio in "
                         "afterwards with voice/mux.py.")
    args = ap.parse_args()

    global VOICE
    if args.voice is not None:
        sys.path.insert(0, str(ROOT))
        from backstage.films.voice import narrate
        VOICE = args.voice or narrate.DEFAULT_VOICE
        print(f"shooting for narration in {VOICE}")

    if args.list:
        for n in FILMS:
            print(n)
        return 0

    base = args.base.rstrip("/")
    # A spliced name is a delivery, not a film: asking for it means
    # asking for its takes. Expanding here rather than in `shoot` keeps
    # `--only adv_emp` meaning what a reader expects.
    wanted = args.only or (list(FILMS) + list(SPLICES))
    names: List[str] = []
    for n in wanted:
        names.extend(SPLICES.get(n, [n]))
    unknown = [n for n in names if n not in FILMS]
    if unknown:
        print(f"unknown film(s): {unknown}. --list to see them all.")
        return 2

    print(f"shooting {len(names)} film(s) into {OUT}")
    bad: List[str] = []
    for i, n in enumerate(names):
        ok, _ = shoot(base, n, args.seed + i, args.keep_raw,
                      trim=not args.no_trim)
        if not ok:
            bad.append(n)

    for whole, parts in SPLICES.items():
        if not all(p in names for p in parts):
            continue
        if any(p in bad for p in parts):
            print(f"  {whole:24s} not joined — a take failed")
            bad.append(whole)
            continue
        err = _splice(whole, parts)
        if err:
            bad.append(whole)
            print(f"  FAIL [{whole}] {err}")

    print()
    if bad:
        print(f"FAIL — {len(bad)} film(s) with problems: {', '.join(bad)}")
        return 1
    print(f"PASS — {len(names)} film(s). Now WATCH them; no check here can "
          f"tell you a film teaches the right thing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
