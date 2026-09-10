"""FastAPI app — thin proxy over the SOC_* engine layer.

Every game route delegates to :mod:`sea_of_colours.snowpark.engine`. The
storage backend is **auto-detected** unless ``SOC_BACKEND`` names one:
``snowflake`` when the Snowpark deps and key-pair config are both
present, otherwise the zero-setup in-process ``memory`` store. The
resolved choice and the reason for it are printed at boot and served
from ``/api/meta/backend`` — see :mod:`sea_of_colours.snowpark.backend`.

Routes::

  GET  /                          SPA shell (player view)
  GET  /watch.html                SPA shell (watcher mode — alias of /)
  GET  /api/generate              Stateless map paint (spectator tooling)
  POST /api/game/new              SOC_INIT_SESSION
  GET  /api/game/latest           SOC_LIST_SESSIONS (most recent)
  GET  /api/sessions              Every persisted season (watcher picker)
  GET  /api/game/{id}/status      SOC_GET_SESSION_STATUS
  GET  /api/game/{id}/view        SOC_GET_VIEW (player percept + agent payload)
  GET  /api/game/{id}/observer    SOC_GET_OBSERVER
  GET  /api/game/{id}/replay      SOC_GET_REPLAY (multi-day)
  GET  /api/game/{id}/day-index   day-by-day frame counts (for scrub bar)
  POST /api/game/{id}/policy      SOC_SUBMIT_POLICY (resolves night when both ready)
  GET  /evals                     Eval command center SPA shell
  GET  /api/evals/scenarios       Every scenario in the harness + pass conditions
  GET  /api/evals/sessions        Past eval runs (filterable by scenario)
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Mapping, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from sea_of_colours.agent.runtime import run_agent_turn
from sea_of_colours.evals.assertions import AssertionContext
from sea_of_colours.evals.scenarios import SCENARIOS
from sea_of_colours.generator import GenerationParams, generate_grid
from sea_of_colours.render import cell_visual
from sea_of_colours.snowpark import backend as soc_backend
from sea_of_colours.snowpark.backend import get_store
from sea_of_colours.snowpark import engine as soc_engine
from sea_of_colours.game.session import MAX_SEATS, Phase
from sea_of_colours.game import tutorial as soc_tutorial
from turnlab import routes as turnlab_routes
from turnlab import store as turnlab_store


# v0.9.6 — N-seat games (1..MAX_SEATS) use canonical slugs ``p1`` … ``pN``.
# Pre-N-seat code-paths hard-coded the 2-seat split, which now rejects p3/p4
# at the API surface even though the engine accepts them. This helper is the
# single gate: callers pass in any user-supplied seat slug and we accept it
# iff it parses as ``p<int>`` within the session-cap range. Centralising the
# rule here avoids drift between the three API endpoints that need it.
_VALID_SEAT_SLUGS: frozenset[str] = frozenset(f"p{i}" for i in range(1, MAX_SEATS + 1))


def _is_valid_seat_slug(player: str | None) -> bool:
    return isinstance(player, str) and player in _VALID_SEAT_SLUGS


# v0.9.12 — cross-browser multiplayer. Two humans on different machines can
# submit for the same game within milliseconds of each other. Each submit is a
# hydrate -> mutate -> save_session_full round-trip; without serialisation a
# concurrent pair can read the same pre-mutation snapshot and the second save
# clobbers the first seat's stash (lost-update). These endpoints are sync
# ``def`` handlers, so FastAPI runs them in a worker threadpool — an
# ``asyncio.Lock`` would not apply. We guard each game_id with a process-wide
# ``threading.Lock`` so the read-modify-write for a given session is atomic.
# (For multi-process / multi-host deploys this would need a store-level lock;
# a single ``uvicorn`` worker — the documented hosting path — is covered.)
_GAME_LOCKS: dict[str, threading.Lock] = {}
_GAME_LOCKS_GUARD = threading.Lock()


def _game_lock(game_id: str) -> threading.Lock:
    with _GAME_LOCKS_GUARD:
        lock = _GAME_LOCKS.get(game_id)
        if lock is None:
            lock = threading.Lock()
            _GAME_LOCKS[game_id] = lock
        return lock


# ── Background agent turns ("the agent thinks on your time") ─────────────
# A Cortex/harness seat's turn costs ~30-80s. If we only ran it when the
# human hits TRANSMIT, they'd wait that long every round. Instead a
# per-game daemon worker pre-fires pending bot seats the moment a phase
# opens (kicked on game create, every status poll, and after each human
# submit), so by the time the human submits the agent has usually already
# played. Each bot turn holds ``_game_lock`` — the Cortex submit and a
# human submit are a cross-process read/modify/write on the same
# ``json_state`` blob, so they MUST be serialised — but status polls are
# lock-free, so the "who's being waited on" timer keeps ticking live.
_BOT_WORKERS: dict[str, threading.Thread] = {}
_BOT_WORKERS_GUARD = threading.Lock()
# game_id -> {"seat", "agent", "started"} while a bot turn is in flight.
_BOT_TURN_STATE: dict[str, dict[str, Any]] = {}
_BOT_TURN_GUARD = threading.Lock()


def _set_bot_turn(game_id: str, seat: str, agent: str) -> None:
    with _BOT_TURN_GUARD:
        _BOT_TURN_STATE[game_id] = {
            "seat": seat, "agent": agent, "started": time.time(),
        }


def _clear_bot_turn(game_id: str) -> None:
    with _BOT_TURN_GUARD:
        _BOT_TURN_STATE.pop(game_id, None)


def _get_bot_turn(game_id: str) -> Optional[dict[str, Any]]:
    with _BOT_TURN_GUARD:
        st = _BOT_TURN_STATE.get(game_id)
        return dict(st) if st else None


# Once a seat lands a policy the store's ``pending`` flag makes the turn
# permanently idempotent (it's skipped on every subsequent kick). This
# ledger only guards the OTHER case: a turn that returned WITHOUT landing
# a policy (an exception before the heuristic fallback, or a mid-turn
# server reload). Without it a persistently failing agent would be
# re-fired back-to-back on every status poll. We record the last attempt
# per ``(seat, phase, day)`` and back off for a cooldown before retrying.
_BOT_ATTEMPT_TS: dict[str, dict[tuple, float]] = {}
_BOT_ATTEMPT_GUARD = threading.Lock()
_BOT_RETRY_COOLDOWN_S = 25.0


def _bot_attempt_recent(game_id: str, key: tuple) -> bool:
    with _BOT_ATTEMPT_GUARD:
        ts = (_BOT_ATTEMPT_TS.get(game_id) or {}).get(key)
    return ts is not None and (time.time() - ts) < _BOT_RETRY_COOLDOWN_S


def _bot_attempt_mark(game_id: str, key: tuple) -> None:
    with _BOT_ATTEMPT_GUARD:
        _BOT_ATTEMPT_TS.setdefault(game_id, {})[key] = time.time()


# How long a human submit will wait for the game lock before giving up and
# returning ``agent_busy`` (the agent is mid-turn holding it). Short so the
# HTTP request never hangs — the browser keeps its wait frame up and retries.
_AGENT_SUBMIT_LOCK_WAIT_S = 3.0
# Display hint for the UI countdown — the hard per-turn ceiling enforced by
# ``orchestrator_2/cortex_invoker.py``.
_AGENT_TURN_CAP_MS = 80_000


def _try_lock(game_id: str, timeout: float) -> Optional[threading.Lock]:
    """Acquire the per-game lock, or return ``None`` if it can't within
    ``timeout`` (the background agent turn is holding it)."""
    lk = _game_lock(game_id)
    return lk if lk.acquire(timeout=timeout) else None


def _agent_status_meta(game_id: str, status: dict[str, Any]) -> dict[str, Any]:
    """Build the ``waiting_on`` + ``bot_turn`` block shared by /status and
    the submit responses so the browser can drive the wait-frame timer."""
    meta: dict[str, Any] = {}
    agents = status.get("agents") or {}
    pending = status.get("pending") or {}
    players = status.get("players") or list(agents.keys())
    phase = str(status.get("phase") or "")
    if phase and phase != "season_complete":
        waiting = []
        for s in players:
            if pending.get(s, False):
                continue
            is_human = str(agents.get(s, "human")).strip().lower() == "human"
            waiting.append({
                "seat": s,
                "agent": str(agents.get(s, "human")),
                "is_human": is_human,
            })
        meta["waiting_on"] = waiting
    turn = _get_bot_turn(game_id)
    if turn:
        meta["bot_turn"] = {
            "seat": turn["seat"],
            "agent": turn["agent"],
            "elapsed_ms": int(max(0.0, time.time() - turn["started"]) * 1000),
            "cap_ms": _AGENT_TURN_CAP_MS,
        }
    return meta


def _run_bot_turn(store, game_id: str, seat: str, agent_label: str) -> None:
    """Run one bot seat's turn through orchestrator_2, publishing live
    turn state for the UI timer for its whole duration."""
    from sea_of_colours.orchestrator_2.runtime import (
        run_agent_turn as _v2_run_agent_turn,
    )
    _set_bot_turn(game_id, seat, agent_label)
    try:
        _v2_run_agent_turn(store, game_id, seat, agent_label=agent_label)
    finally:
        _clear_bot_turn(game_id)


def _drive_bots(game_id: str, *, max_turns: int = 64) -> None:
    """Fire pending BOT seats (Cortex/harness) until a human is needed or
    the season completes. Store-based, night resolved with the local
    engine (populates the combat kill feed) — mirrors the season runner.

    Each iteration takes ``_game_lock`` so a bot turn never races a human
    submit on the shared ``json_state`` blob. Humans are never fired — the
    loop returns and hands control back to the browser. Runs in the
    background worker (``_kick_bots``); human submits are non-blocking and
    the browser polls /status for the resolution this produces.
    """
    # Route by game, not by process: the bot worker outlives the request
    # that started it, and driving a memory game against the Snowflake
    # store would find no session and quietly do nothing.
    store = _store_for(game_id)
    for _ in range(max_turns):
        with _game_lock(game_id):
            status = soc_engine.get_session_status(store, game_id)
            if str(status.get("phase") or "") == "season_complete":
                return
            agents = status.get("agents") or {}
            pending = status.get("pending") or {}
            players = status.get("players") or list(agents.keys())
            phase, day = status.get("phase"), status.get("day")

            bot_seat = None
            human_pending = False
            deferred_bot = False  # a bot we're backing off from (recent miss)
            for s in players:
                if pending.get(s, False):
                    continue  # already landed a policy — permanently skipped
                if str(agents.get(s, "human")).strip().lower() == "human":
                    human_pending = True
                    continue
                # A bot that hasn't landed a policy. If we tried it very
                # recently and it still hasn't stashed, back off (avoids a
                # tight retry loop on a failing/interrupted agent).
                if _bot_attempt_recent(game_id, (s, phase, day)):
                    deferred_bot = True
                    continue
                bot_seat = s
                break

            if bot_seat is None:
                # Don't force a resolve while a bot is merely in cooldown —
                # it hasn't submitted, so run_night would be premature.
                if human_pending or deferred_bot:
                    return  # waiting on a human / cooling-down bot
                # Everyone submitted but the phase hasn't advanced — resolve
                # the night locally (orbit auto-resolves on submit).
                # v1.19 — this read an undefined ``cur`` and raised NameError
                # instead of resolving, killing the worker on the one path
                # that exists to unstick a fully-submitted night. The
                # comparison below wants the phase/day we came in on.
                before = (phase, day)
                try:
                    soc_engine.run_night(store, game_id)
                except Exception as exc:  # pragma: no cover — defensive
                    print(f"[soc] _drive_bots run_night crashed: {exc}",
                          file=sys.stderr, flush=True)
                    return
                after = soc_engine.get_session_status(store, game_id)
                if (after.get("phase"), after.get("day")) == before:
                    return  # no progress — avoid spin
                continue

            # Fire this bot seat (holds the lock for the whole ~80s turn).
            # Mark the attempt first: a successful turn lands a policy and is
            # skipped via ``pending`` next loop; a failed one is skipped via
            # the cooldown so we don't hammer it.
            _bot_attempt_mark(game_id, (bot_seat, phase, day))
            try:
                _run_bot_turn(
                    store, game_id, bot_seat,
                    str(agents.get(bot_seat) or ""),
                )
            except Exception as exc:  # pragma: no cover — defensive
                print(f"[soc] _drive_bots seat={bot_seat} crashed: {exc}",
                      file=sys.stderr, flush=True)
                return


def _kick_bots(game_id: str) -> None:
    """Ensure a background worker is draining this game's pending bot seats.

    Idempotent: a no-op if a worker is already running for the game. Cheap
    to call on every status poll — the thread exits immediately when
    there's nothing for a bot to do (only humans pending / season over).
    """
    with _BOT_WORKERS_GUARD:
        existing = _BOT_WORKERS.get(game_id)
        if existing is not None and existing.is_alive():
            return

        def _worker() -> None:
            try:
                _drive_bots(game_id)
            except Exception as exc:  # pragma: no cover — defensive
                print(f"[soc] bot worker for {game_id} crashed: {exc}",
                      file=sys.stderr, flush=True)
            finally:
                with _BOT_WORKERS_GUARD:
                    if _BOT_WORKERS.get(game_id) is t:
                        _BOT_WORKERS.pop(game_id, None)

        t = threading.Thread(target=_worker, name=f"bots-{game_id[:8]}",
                             daemon=True)
        _BOT_WORKERS[game_id] = t
        t.start()


# Runtime labels the /agent/think route accepts. Mirrors
# ``agent.runtime._SUPPORTED_RUNTIMES`` — the engine raises on anything
# else, but rejecting at the API boundary gives a 400 instead of a 500.
_HEURISTIC_RUNTIMES = ("heuristic", "red_harvest_lite")

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_REPO_ROOT = Path(__file__).resolve().parent.parent
_MANUAL_DIR = _REPO_ROOT / "manual"
_GUIDE_DIR = _REPO_ROOT / "guide"
_DOCS_DIR = _REPO_ROOT / "docs"
_BATTLES_DIR = _REPO_ROOT / "reports" / "battles"
_INDEX_HTML = _STATIC_DIR / "index.html"
_EVALS_HTML = _STATIC_DIR / "evals.html"
_LANDING_HTML = _STATIC_DIR / "landing.html"

# v1.26 — identity for `run_web.py --replace`. Read the note on
# ``/api/meta/whoami`` before changing either value: the launcher decides
# whether it is allowed to kill the process on the port by asking the
# port itself, so this string is a contract, not a label.
_APP_IDENT = "sea_of_colours"
_STARTED_AT = time.time()

# ── Boot banner ────────────────────────────────────────────────────────
# Resolve and OPEN the store here, before serving. Building the Snowpark
# session lazily meant a bad key or an undeployed schema surfaced as a
# 500 on whichever API call happened to come first — nowhere near the
# command the operator had just run.
def _boot_banner() -> None:
    print("[soc] FastAPI starting", file=sys.stderr, flush=True)
    try:
        res = soc_backend.probe_store()
    except soc_backend.BackendUnavailable as exc:
        print(f"[soc] {exc}", file=sys.stderr, flush=True)
        if exc.fix:
            print(f"[soc]   fix: {exc.fix}", file=sys.stderr, flush=True)
        # Explicit request, explicit failure: don't limp along on a
        # backend they didn't ask for and silently lose their seasons.
        sys.exit(2)

    print(f"[soc] {res.summary()}", file=sys.stderr, flush=True)
    print(f"[soc]   {res.reason}", file=sys.stderr, flush=True)
    if res.fix:
        # Only call it a fix when something the operator asked for
        # failed. Landing on memory because no Snowflake setup exists is
        # the normal, supported outcome, and labelling that "fix" tells a
        # first-timer their working install is broken.
        label = "fix" if res.requested != "auto" else "for persistence"
        print(f"[soc]   {label}: {res.fix}", file=sys.stderr, flush=True)
    if not res.persists:
        # Be precise about what memory does and doesn't cost you. It is
        # the supported zero-setup path and a full game — but since v1.14
        # the launcher keeps LLM seats on a persistent backend, so say so
        # here rather than letting someone discover it as a missing
        # dropdown entry.
        print(
            "[soc]   full game vs RED_HARVEST/_LITE on memory; seasons are "
            "lost on restart and LLM agents need a persistent backend "
            "(docs/SNOWFLAKE_SETUP.md §2).",
            file=sys.stderr,
            flush=True,
        )


_boot_banner()

app = FastAPI(
    title="Sea of Colours",
    description="Orchestrator surface for the Sea of Colours world.",
    version="0.4.0",
)

# Replay payloads are large, highly-repetitive JSON (per-frame fog grids).
# Gzip compresses them ~10-30x on the wire so the multi-MB replay download
# stays small even though the parsed shape is still substantial.
app.add_middleware(GZipMiddleware, minimum_size=2048)

class _NoCacheStatic(StaticFiles):
    """Serve static assets with ``Cache-Control: no-cache`` headers.

    Browsers aggressively cache `/static/app.js` and `/static/styles.css`,
    so iterating on the UI without a hard-reload would silently keep
    serving stale code. For a dev-only orchestrator like this one we
    prefer freshness over the trivial bandwidth savings. The browser
    still gets ETag validation under the hood — `no-cache` just forces
    a re-check on every request.
    """

    async def get_response(self, path: str, scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        response.headers.setdefault(
            "Cache-Control", "no-cache, no-store, must-revalidate"
        )
        response.headers.setdefault("Pragma", "no-cache")
        response.headers.setdefault("Expires", "0")
        return response


class _DocsStatic(_NoCacheStatic):
    """Static files, but Markdown is served as text the browser will show.

    Starlette types ``.md`` as ``text/markdown``, which browsers download
    rather than render — so a doc link would silently produce a file in
    ~/Downloads instead of a page. Markdown is designed to read fine as
    plain text, so overriding the type is enough; rendering it properly
    would mean adding a Markdown dependency for a handful of links.
    """

    async def get_response(self, path: str, scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        if path.lower().endswith(".md"):
            response.headers["Content-Type"] = "text/plain; charset=utf-8"
        return response


app.mount("/static", _NoCacheStatic(directory=_STATIC_DIR), name="static")

# v1.12 — serve the two reader-facing document trees over HTTP as well.
# They were built as self-contained file:// pages and still work that
# way, but that meant the only way to share them was "clone the repo and
# open a folder". Mounting them lets a host hand out a URL — including
# over the multiplayer tunnel — and costs nothing, since both are plain
# static assets. `html=True` makes /guide/ and /manual/ serve index.html.
if _MANUAL_DIR.is_dir():
    app.mount(
        "/manual", _NoCacheStatic(directory=_MANUAL_DIR, html=True), name="manual",
    )
if _GUIDE_DIR.is_dir():
    app.mount(
        "/guide", _NoCacheStatic(directory=_GUIDE_DIR, html=True), name="guide",
    )
if _DOCS_DIR.is_dir():
    app.mount("/docs", _DocsStatic(directory=_DOCS_DIR), name="docs")

# The battle room, if a recorded suite has produced one. Written by
# `soc suite --record` and designed to work by double-clicking, so this
# mount is a convenience rather than the way in — but a host running the
# server for a room of attendees can point them at a URL, and an agent
# reviewing its own bake does not have to know where the repo lives.
# Absent until someone records, hence the existence check.
#
# The page itself is served from its source rather than from the copy
# the recorder leaves in the bake directory, for two reasons (v1.41):
# that copy is only as fresh as the last `--record`, and without one
# there is no directory to mount at all — which would put the live diff
# loop behind "go and run a twenty-minute suite first". Registered
# before the mount so it wins the /battles/ path; the mount still
# serves index.js and data/ beside it.
_ROOM_HTML = (
    _REPO_ROOT / "sea_of_colours" / "evals" / "battles" / "room" / "room.html"
)


@app.get("/battles/", include_in_schema=False)
@app.get("/battles", include_in_schema=False)
def battle_room() -> Response:
    if not _ROOM_HTML.is_file():
        raise HTTPException(status_code=404, detail="the room is missing")
    return Response(
        content=_ROOM_HTML.read_text(encoding="utf-8"),
        media_type="text/html; charset=utf-8",
        headers=_NO_CACHE_HEADERS,
    )


if _BATTLES_DIR.is_dir():
    app.mount(
        "/battles",
        _NoCacheStatic(directory=_BATTLES_DIR, html=True),
        name="battles",
    )


# ── the turn lab ────────────────────────────────────────────────────
#
# A side experiment that clones frozen turns and lets agents play the
# copies. It lives entirely in ``turnlab/`` — its own routes, its own
# page, its own local store — and is included here rather than written
# here so that nothing in it can reach for this module's store by habit.
# See turnlab/README.md.
app.include_router(turnlab_routes.router)


# Markdown reachable over HTTP, by exact path. An allowlist rather than
# a directory walk because this route sits at the URL root, where
# anything looser is a directory traversal waiting to happen.
#
# The agent docs are here because the guide links to them: an attendee
# reading the guide through a tunnel should be able to follow "here is
# how you fork V12" without being told to go find a folder on disk.
_SERVABLE_MARKDOWN = {
    "README",
    "RULEBOOK",
    "AGENTS",
    "sea_of_colours/orchestrator_2/README",
    "sea_of_colours/orchestrator_2/ARCHITECTURE",
    "sea_of_colours/orchestrator_2/harnesses/tabula_v12/README",
    "sea_of_colours/orchestrator_2/harnesses/tabula_v12/ENGINE_INTERFACE",
}


@app.get("/{name:path}.md", include_in_schema=False)
def api_root_markdown(name: str) -> Response:
    """Serve the Markdown the guide links to, as readable plain text."""
    if name not in _SERVABLE_MARKDOWN:
        raise HTTPException(status_code=404, detail="not found")
    path = _REPO_ROOT / f"{name}.md"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return Response(
        content=path.read_text(encoding="utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


def _rgb(triple: tuple[int, int, int]) -> str:
    return f"rgb({triple[0]},{triple[1]},{triple[2]})"


def _store():
    """The process-default storage backend.

    Only for work that isn't about one particular game (boot, listings,
    creating a season). Anything game-scoped must use :func:`_store_for`,
    or it will read a memory game out of Snowflake and find nothing.
    """
    return get_store()


def _store_for(game_id: str):
    """The store that owns ``game_id``.

    v1.14 — games choose their own backend in the New Game modal, so one
    process can serve a fast memory game and a persisted Snowflake season
    side by side. Ids created before this existed, or by another process,
    aren't in the registry and fall back to the default — which is the
    backend that wrote them.

    v1.42 — turn-lab sessions are routed out to the lab's own local
    store, ahead of the registry. They have to be: a lab clone opens in
    the ordinary game UI and is served by the ordinary game routes, so
    without this hook the app would look for it in whichever backend the
    process happens to be on and find nothing. Keeping the test on the
    id (rather than registering each clone) means it also works for a
    clone made by an earlier process, which is what you get when you
    restart the server with a lab tab still open.
    """
    if turnlab_store.owns(game_id):
        return turnlab_store.store()
    return soc_backend.store_for_session(game_id)


def _snowflake_store_status() -> tuple[bool, str, str]:
    """Can a game actually be created on the Snowflake store right now?

    ``snowflake_readiness()`` is a *file* check by design — it opens no
    connection, so it stays cheap enough to run on every boot. That
    leaves a gap this has to close: a fully configured machine behind a
    network policy, an expired key, or an undeployed schema all read as
    "ready" and would only fail at spawn time, as a 500.

    The boot probe already learned the truth, so fold its verdict in. If
    auto-detection tried Snowflake and could not open it, the resolution
    fell back to memory carrying the real error — surface that instead of
    accepting a choice that is certain to fail.

    Returns ``(ready, reason, fix)``, matching ``snowflake_readiness()``.
    """
    ready, why, fix = soc_backend.snowflake_readiness()
    if not ready:
        return (False, why, fix)
    res = soc_backend.resolution()
    if res.requested == "auto" and res.name != "snowflake":
        return (False, res.reason, res.fix)
    return (True, why, fix)


def _backend_options() -> list[dict[str, Any]]:
    """The per-game backend choices the New Game modal may offer.

    Memory is always available. Snowflake appears only when the store
    path is genuinely set up (extras installed, key-pair config, key file
    present) and carries the reason + fix when it isn't, so the modal can
    explain a greyed-out option instead of just hiding it.

    """
    default = soc_backend.resolution().name
    ready, why, fix = _snowflake_store_status()
    options: list[dict[str, Any]] = [
        {
            "value": "memory",
            "label": "Memory",
            "blurb": "Instant moves. The season vanishes when the server stops.",
            "available": True,
            "persists": False,
            # Heuristics only: an LLM match you can't replay defeats the
            # point of the iteration loop — see _llm_backend_conflict.
            "allows_llm": False,
            "reason": "",
            "fix": "",
        },
        {
            "value": "snowflake",
            "label": "Snowflake",
            "blurb": "Every move written to your account. Slower, and the "
                     "season is still there tomorrow.",
            "available": ready,
            "persists": True,
            "allows_llm": True,
            "reason": "" if ready else why,
            "fix": "" if ready else fix,
        },
    ]
    # A server explicitly started on `file` or `multi` should keep
    # offering what it was started with, rather than silently dropping
    # the operator onto memory.
    if default not in ("memory", "snowflake"):
        options.append({
            "value": default,
            "label": default.title(),
            "blurb": f"The backend this server was started with "
                     f"(SOC_BACKEND={default}).",
            "available": True,
            "persists": True,
            "allows_llm": True,
            "reason": "",
            "fix": "",
        })
    for opt in options:
        opt["default"] = opt["value"] == default
    return options


def _resolve_requested_backend(raw: Any) -> str:
    """Validate a New Game backend choice; empty means the server default."""
    name = str(raw or "").strip().lower()
    if not name:
        return soc_backend.resolution().name
    if name not in soc_backend.VALID_BACKENDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{name}' is not a storage backend — expected one of "
                f"{', '.join(soc_backend.VALID_BACKENDS)}."
            ),
        )
    if name in ("snowflake", "multi"):
        ready, why, fix = _snowflake_store_status()
        if not ready:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"The Snowflake store isn't set up on this machine: "
                    f"{why}."
                    + (f" Fix: {fix}." if fix else "")
                    + " See docs/SNOWFLAKE_SETUP.md §2, or pick the Memory "
                      "backend for a game that lasts until the server stops."
                ),
            )
    return name


def _llm_backend_conflict(backend_name: str, agents: dict[str, str]) -> str:
    """Why these seats can't run on this backend — ``""`` when they can.

    Memory is the fast, disposable mode: nothing is written down, so an
    LLM turn leaves no reasoning card to reopen and no season to replay.
    That costs more than it sounds like — the whole agent-iteration
    toolchain (``turn_suite.py``, ``replay_turn.py``, ``advise_v12.py``)
    reads persisted sessions, so an LLM match played on memory teaches
    you nothing you can go back and inspect. Since the hackathon is
    precisely about that loop, LLM seats want a durable store and memory
    stays what it is good at: instant games against a heuristic.

    Note this is a *product* line, not a technical limit — V12 itself
    runs fine on memory (Cortex is a REST call with a PAT and never
    touches the store; its memory modules keep a process-local cache).
    The setup story is what makes it the right line: the Snowflake store
    is a standard setup step (docs/SNOWFLAKE_SETUP.md §2), so anyone
    ready to run an LLM agent has one.
    """
    if backend_name != "memory":
        return ""
    seats = _llm_seats(agents)
    if not seats:
        return ""
    return (
        f"{', '.join(seats)} run an LLM agent. Those play on a persistent "
        f"backend so the season, and the agent's reasoning for every turn, "
        f"are still there to replay afterwards — which is what the turn "
        f"suite and the advisor read. Pick Snowflake, or seat a heuristic "
        f"opponent for a fast memory game."
    )


def _merged_sessions() -> list[dict[str, Any]]:
    """Every session across the backends this process has open.

    A single ``list_sessions`` would hide half the picker the moment a
    memory game and a Snowflake season coexist. Rows are tagged with the
    backend that holds them, first store wins on a duplicate id, and one
    unreachable store never blanks the list.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, store in soc_backend.stores_in_use():
        try:
            rows = soc_engine.list_sessions(store).get("sessions") or []
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[soc] list_sessions({name}) failed: {exc}",
                  file=sys.stderr, flush=True)
            continue
        for row in rows:
            sid = str(row.get("session_id") or "")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            enriched = dict(row)
            enriched["backend"] = name
            # The composite store tags rows with a finer source of its
            # own ("snowflake" / "local"); only fill the gap it leaves.
            if not enriched.get("source"):
                enriched["source"] = name
            soc_backend.register_session_backend(sid, name)
            out.append(enriched)
    return out


# ── Slow-agent (Cortex / harness) live bot fan-out ──────────────────────
# Seats tagged with a non-heuristic agent (e.g. ``"tabula_v12"``) can't run
# through the fast in-memory heuristic fan-out (``_fire_bots_in_memory``):
# that path is hardcoded to RED_HARVEST and silently downgrades the agent.
# For any game containing such a seat we instead drive every pending bot
# seat through orchestrator_2 (store-based submit + local night
# resolution), mirroring the headless season runner. This is slower
# (~30-70s per Cortex turn) but is the only correct path for a real agent.
_HEURISTIC_AGENT_LABELS = {"human", "red_harvest", "heuristic"}


def _game_has_slow_bot(agents: dict[str, Any]) -> bool:
    """True when any seat runs a non-heuristic agent (Cortex / harness)."""
    return any(
        str(v).strip().lower() not in _HEURISTIC_AGENT_LABELS
        for v in (agents or {}).values()
    )


def _llm_seats(agents: dict[str, Any]) -> list[str]:
    """Seats bound to an in-process LLM harness (V12 and hackathon forks).

    Keyed off ``needs_llm`` rather than ``kind`` so a fork that doesn't
    call an LLM — a pure-heuristic harness is a legitimate entry — isn't
    forced to hold a PAT it never uses.
    """
    from sea_of_colours.orchestrator_2.binding_registry import (
        AGENT_LABEL_BINDINGS,
    )

    out = []
    for seat, label in (agents or {}).items():
        binding = AGENT_LABEL_BINDINGS.get(str(label).strip().lower())
        if binding is None or binding.kind != "harness_in_process":
            continue
        if binding.needs_llm:
            out.append(str(seat))
    return sorted(out)


def _preflight_llm_credentials(agents: dict[str, Any]) -> None:
    """Refuse to start a game whose LLM seat could never think.

    Without a PAT the harness doesn't error — it falls back and passes
    every night with zero moves, so the player watches "V12" sit still
    and concludes the agent is broken. Catch it at creation, where we
    can name the fix, rather than letting it look like a game bug.
    """
    seats = _llm_seats(agents)
    if not seats:
        return
    from sea_of_colours.orchestrator_2.cortex_chat import credentials_status

    ready, reason = credentials_status()
    if ready:
        return
    raise HTTPException(
        status_code=400,
        detail=(
            f"Seat(s) {', '.join(seats)} are set to an LLM agent, but "
            f"Snowflake credentials are missing: needs {reason}. "
            f"See docs/SNOWFLAKE_SETUP.md. To play right now with no "
            f"setup, pick RED_HARVEST_LITE or RED_HARVEST instead."
        ),
    )


_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


@app.get("/")
def index(request: Request):
    """Landing / title screen — Quick game / Play / Multiplayer / Replay.

    The command-centre SPA lives at ``/play``. Deep links are **not**
    path-agnostic, despite what this docstring used to claim: the landing
    page has no session-loading code, so a seat link pointed at ``/``
    silently opened the title screen instead of the game. That is what
    every LAN invite QR did before v1.12, and what the README documented.

    Generation is fixed at the source (``buildSeatUrl`` pins ``/play``),
    but links live in other people's chat history and on printed QR
    codes, so honour the old shape here too rather than stranding them.
    """
    if request.query_params.get("session"):
        return RedirectResponse(f"/play?{request.url.query}", status_code=307)
    return FileResponse(_LANDING_HTML, headers=_NO_CACHE_HEADERS)


@app.get("/play")
def play_shell() -> FileResponse:
    """Command-centre SPA (formerly served at ``/``)."""
    return FileResponse(_INDEX_HTML, headers=_NO_CACHE_HEADERS)


@app.get("/evals")
def evals_shell() -> FileResponse:
    """Eval command center SPA shell.

    Standalone single-page app that lists every scenario in
    :data:`sea_of_colours.evals.scenarios.SCENARIOS`, surfaces each
    one's assertions as a "pass conditions" headline, and embeds the
    watcher for any past eval session via the
    :func:`api_evals_sessions` endpoint. Lives next to ``/`` rather
    than under ``/watch.html`` because the read-only watcher and the
    eval browser have very different chrome.
    """
    return FileResponse(_EVALS_HTML, headers=_NO_CACHE_HEADERS)


# v1.13 — /mobile is retired. It was a standalone 953-line fork frozen at
# the initial commit while app.js kept moving, and the main SPA has
# implemented the phone UX it duplicated for a long time (the orders
# bottom bar, the orders sheet, the phone media queries). Redirect rather
# than 404: printed QR codes and pasted links outlive the code.
@app.get("/mobile")
def mobile_shell(request: Request) -> RedirectResponse:
    q = request.url.query
    return RedirectResponse(url=f"/play{'?' + q if q else ''}", status_code=307)


@app.get("/watch.html")
def watch_shell() -> FileResponse:
    """Watcher entry point — Phase C.

    Serves the same SPA shell as ``/`` but is the canonical URL the CLI
    season runner prints in its post-run summary (``/watch.html?season=
    <slug>``). The frontend reads ``?session=<id>`` / ``?season=<slug>``
    / ``?watch=1`` from the URL on boot and switches into a read-only
    watcher mode that hides the policy / NEW GAME controls and loads
    the requested season's replay end-to-end.
    """
    return FileResponse(_INDEX_HTML, headers=_NO_CACHE_HEADERS)


@app.get("/api/generate")
def api_generate(
    seed: Optional[int] = Query(None, description="Deterministic seed; random when omitted."),
    width: int = Query(80, ge=4, le=400),
    height: int = Query(50, ge=4, le=400),
) -> dict:
    """Stateless map paint (spectator tooling — not session-bound)."""
    if seed is None:
        seed = random.randrange(2**31)
    params = GenerationParams(width=width, height=height, seed=seed)
    grid = generate_grid(params)
    cells: list[dict[str, Any]] = []
    for row in grid:
        for cell in row:
            fg, bg, ch = cell_visual(cell)
            entry: dict[str, Any] = {"ch": ch, "bg": _rgb(bg)}
            if fg is not None:
                entry["fg"] = _rgb(fg)
            cells.append(entry)
    return {"seed": seed, "width": width, "height": height, "cells": cells}


@app.get("/api/game/palette")
def api_game_palette() -> dict[str, Any]:
    """Return the curated seat color palette for player customization.
    
    v0.9.18 — exposes SEAT_COLOR_PALETTE from session.py so the new-game
    modal can render swatches and validate user picks without duplicating
    the palette definition.
    """
    from sea_of_colours.game.session import SEAT_COLOR_PALETTE, SEAT_DEFAULT_COLORS
    return {
        "palette": [
            {"hex": hex_color, "rgb": list(rgb)}
            for hex_color, rgb in SEAT_COLOR_PALETTE.items()
        ],
        "defaults": dict(SEAT_DEFAULT_COLORS),
    }


@app.post("/api/game/new")
def api_game_new(
    payload: Optional[dict[str, Any]] = None,
    seed: Optional[int] = Query(None),
    width: int = Query(40, ge=8, le=200),
    height: int = Query(28, ge=8, le=200),
    season_day_cap: Optional[int] = Query(
        None,
        ge=1,
        le=60,
        description=(
            "Number of nights in the season. Defaults to "
            "SEASON_DAY_CAP (v0.9.18: 7). Clamped to 1..60."
        ),
    ),
) -> dict[str, Any]:
    """Start a new game. v0.9.6 — accepts the N-seat launcher payload.

    Query params (``seed`` / ``width`` / ``height`` / ``season_day_cap``)
    survive for the legacy "open watcher" path. The optional JSON
    body lets the new-game modal override those AND specify:

    * ``players``: list of seat ids in canonical order (1-4 entries,
      defaults to ``["p1", "p2"]``).
    * ``agents``: per-seat agent assignment, e.g.
      ``{"p1": "human", "p2": "red_harvest"}``. Defaults every seat
      to ``"human"``.
    * ``visibility_mode``: ``"hidden"`` (default, fog-of-war) or
      ``"open"`` (omniscient, OBS-style).
    """
    body = payload or {}

    def _maybe_int(value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    body_seed = _maybe_int(body.get("seed"))
    if body_seed is not None:
        seed = body_seed
    body_width = _maybe_int(body.get("width"))
    if body_width is not None:
        width = max(8, min(200, body_width))
    body_height = _maybe_int(body.get("height"))
    if body_height is not None:
        height = max(8, min(200, body_height))
    body_cap = _maybe_int(body.get("season_day_cap"))
    if body_cap is not None:
        season_day_cap = max(1, min(60, body_cap))

    raw_players = body.get("players")
    players: Optional[list[str]] = None
    if isinstance(raw_players, list) and raw_players:
        players = [str(p) for p in raw_players][:4]
    raw_agents = body.get("agents") or {}
    agents: dict[str, str] = {}
    if isinstance(raw_agents, dict):
        for k, v in raw_agents.items():
            agents[str(k)] = str(v).strip().lower() or "human"
    # v1.32 — teaching presets, resolved BEFORE the backend and credential
    # preflight below, because a preset overrides both the seat agents and
    # the store. Resolving after would let a tutorial spawned from a page
    # with stale form state demand Cortex credentials the attendee has not
    # set up yet — which is the exact wall the tutorial exists to avoid.
    #
    # The client posts a NAME, never a bag of overrides, so the landing
    # page cannot drift from what the engine mints. Explicit body fields
    # still win, so a caller (the film harness) can pin one dial without
    # restating the preset.
    weapons_enabled = True
    signs_enabled = True
    tutorial_name = ""
    preset = soc_tutorial.preset_config(body.get("tutorial"))
    if preset is not None:
        if body.get("width") in (None, ""):
            width = int(preset.get("width", width))
        if body.get("height") in (None, ""):
            height = int(preset.get("height", height))
        if body.get("season_day_cap") in (None, ""):
            season_day_cap = int(preset.get("season_day_cap", season_day_cap))
        # A preset may pin its board. Advanced does, because its lessons
        # need terrain the generator only sometimes provides and because
        # its films are shot on that exact map — see
        # ``ADVANCED_TUTORIAL_SEED``. An explicit seed still wins, so the
        # harness can shoot a variant without editing the preset.
        if seed is None and preset.get("seed") is not None:
            seed = int(preset["seed"])
        weapons_enabled = bool(preset.get("weapons_enabled", True))
        signs_enabled = bool(preset.get("signs_enabled", True))
        tutorial_name = (
            soc_tutorial.normalise_preset(body.get("tutorial"))
            if soc_tutorial.is_teaching(body.get("tutorial"))
            else ""
        )
        # A teaching game is always you against the in-process heuristic,
        # on the memory store: no credentials, no network, no warehouse.
        players = players or ["p1", "p2"]
        agents = {
            "p1": "human",
            "p2": str(preset.get("opponent") or "red_harvest_lite"),
        }
        body = {**body, "backend": "memory"}

    # Explicit dials win over the preset, and are the only way to get a
    # weapons-off board WITHOUT the preset's forced seat assignment. The
    # film harness needs exactly that: the Basic look, but with both
    # seats human so a collision can be scripted rather than hoped for
    # from a bot. Booleans only — a missing key must not read as False.
    if isinstance(body.get("weapons_enabled"), bool):
        weapons_enabled = body["weapons_enabled"]
    if isinstance(body.get("signs_enabled"), bool):
        signs_enabled = body["signs_enabled"]

    # v1.14 — the backend is a per-game choice now. Resolve it before the
    # credential preflight so an unreachable store fails on the store's
    # own terms rather than as a confusing agent error.
    backend_name = _resolve_requested_backend(body.get("backend"))
    conflict = _llm_backend_conflict(backend_name, agents)
    if conflict:
        raise HTTPException(status_code=400, detail=conflict)
    _preflight_llm_credentials(agents)
    visibility_mode = str(body.get("visibility_mode") or "hidden").strip().lower()
    if visibility_mode not in ("hidden", "open"):
        visibility_mode = "hidden"

        # v0.9.18 — extract player_profiles from body (custom names/tags/colors)
    raw_profiles = body.get("player_profiles")
    player_profiles: Optional[dict[str, dict[str, str]]] = None
    if isinstance(raw_profiles, dict):
        player_profiles = {}
        for seat, prof in raw_profiles.items():
            if isinstance(prof, dict):
                player_profiles[str(seat)] = {
                    "display_name": str(prof.get("display_name", "")),
                    "tag": str(prof.get("tag", ""))[:3].upper(),
                    "color": str(prof.get("color", "")).upper(),
                }

    # v1.40 — a seat holding a *named* agent carries that name. The
    # engine mints Latin house names for bot seats, which is the right
    # flavour for the built-in houses and actively unhelpful for a fork
    # somebody named: picking DRYRUN_PILOT and then watching "Aureus
    # Mustela" play makes it impossible to tell whose agent is whose.
    # Only forks are renamed, and only where the player did not type
    # their own name in — the modal still wins.
    named = _named_agent_profiles(agents, player_profiles)
    if named:
        player_profiles = {**named, **(player_profiles or {})}

    if seed is None:
        seed = random.randrange(2**31)
    try:
        target_store = soc_backend.get_store_for(backend_name)
    except Exception as exc:
        # Last line of defence. The readiness check is offline and the
        # boot probe is a snapshot, so a warehouse can still go away
        # between boot and this click — which must read as "your store is
        # unreachable", not as a 500 on New Game.
        raise HTTPException(
            status_code=400,
            detail=(
                f"Could not open the {backend_name} store: {exc}. "
                f"Fix: {soc_backend._fix_for(exc)}. Or pick the Memory "
                f"backend to play now."
            ),
        ) from exc
    created = soc_engine.init_session(
        target_store,
        seed=seed,
        width=width,
        height=height,
        season_day_cap=season_day_cap,
        players=players,
        agents=agents or None,
        visibility_mode=visibility_mode,
        player_profiles=player_profiles,
        weapons_enabled=weapons_enabled,
        signs_enabled=signs_enabled,
        tutorial=tutorial_name,
    )
    if isinstance(created, dict):
        gid = created.get("session_id")
        if isinstance(gid, str) and gid:
            # Register before kicking bots: the worker resolves its own
            # store by id, and an unregistered game falls back to the
            # process default — which for a memory game on a Snowflake
            # server is the wrong store entirely.
            soc_backend.register_session_backend(gid, backend_name)
            created["backend"] = backend_name
            created["persists"] = backend_name != "memory"
            # v1.11 — kick the background bot worker so any Cortex/harness
            # seat starts thinking the moment the game exists, in parallel
            # with the human opening the board (agent plays on the human's
            # time).
            if _game_has_slow_bot(agents):
                _kick_bots(gid)
    return created


def _is_eval_session(row: dict[str, Any]) -> bool:
    """True when a session row carries the eval command-center tag.

    Eval-recorded sessions (see
    :func:`sea_of_colours.evals.runner.run_scenario`) set
    ``season_name`` to ``eval:<scenario>:<config>`` so the watcher
    UI's season picker can filter them out — evals aren't gameplay
    seasons and shouldn't clutter the main "open a saved game"
    dropdown. The same predicate gates the NEW-GAME wipe so eval
    sessions survive across season changes.
    """
    return str(row.get("season_name") or "").startswith("eval:")


@app.get("/api/game/latest")
def api_game_latest() -> dict[str, Any]:
    # Walk in last-touched order until we find a non-eval session — the
    # watcher's "open the most recent game" affordance shouldn't pop
    # straight into an eval replay (those have their own UI at /evals).
    for row in _merged_sessions():
        if _is_eval_session(row):
            continue
        return {
            "session_id": row["session_id"],
            "day": row.get("day"),
            "phase": row.get("phase"),
            "width": row.get("width"),
            "height": row.get("height"),
        }
    raise HTTPException(status_code=404, detail="no sessions yet")


@app.get("/api/sessions")
def api_sessions(
    season: Optional[str] = Query(
        None,
        description=(
            "Filter to a single season by URL slug (e.g. 'glacies-helix'). "
            "When provided, returns at most one row — used by the watcher's "
            "?season=<slug> deep-link path."
        ),
    ),
) -> dict[str, Any]:
    """List every persisted season for the watcher frontend.

    See :func:`sea_of_colours.snowpark.engine.list_sessions` for the
    enriched per-row shape. Sessions are returned in ``last_touched_at
    DESC`` order so the most recent CLI / UI run is first.

    Pass ``?season=<slug>`` to resolve a slug-only deep-link (used by
    the CLI runner's printed watch URL); the response then contains
    zero or one entry depending on whether the slug matches.
    """
    sessions = _merged_sessions()
    if season:
        target = str(season).strip().lower()
        row = next(
            (s for s in sessions
             if (s.get("season_slug") or "").lower() == target),
            None,
        )
        if row and _is_eval_session(row):
            # Slug-resolution still works for eval sessions (handy
            # for sharing deep links) but we hide them from the
            # picker dropdown — the eval UI at /evals is the
            # canonical surface for them.
            return {"sessions": []}
        return {"sessions": [row] if row else []}
    return {
        "sessions": [s for s in sessions if not _is_eval_session(s)],
    }


@app.delete("/api/game/{game_id}")
def api_game_delete(game_id: str) -> dict[str, Any]:
    """Permanently delete one replay/session (the watcher's bin control).

    Refuses eval-tagged sessions (those belong to the /evals surface).
    Routes to whichever backend owns the id under the composite store, so
    a local file season and a Snowflake season are both deletable from the
    same picker. Deleting a missing id is a no-op (still returns ok)."""
    store = _store_for(game_id)
    row = store.load_session(game_id)
    if row is not None and _is_eval_session(row):
        raise HTTPException(
            status_code=403,
            detail="eval sessions can't be deleted from the watcher",
        )
    store.delete_session(game_id)
    soc_backend.forget_session_backend(game_id)
    return {"ok": True, "session_id": game_id}


# ---------------------------------------------------------------------
# Eval command center API
# ---------------------------------------------------------------------
@app.get("/api/evals/scenarios")
def api_evals_scenarios() -> dict[str, Any]:
    """List every scenario in the harness with its pass conditions.

    The frontend renders one sidebar entry per scenario and a
    headline-table of ``describe()`` strings — see
    :meth:`sea_of_colours.evals.assertions.Assertion.describe`. We
    return ``tags`` so the sidebar can group/colour items (e.g. the
    spatial-reasoning trio used by the Phase 1 A/B).
    """
    out: list[dict[str, Any]] = []
    for s in SCENARIOS:
        out.append(
            {
                "name": s.name,
                "summary": s.summary,
                "player": s.player,
                "tags": list(s.tags),
                "assertions": [
                    {"name": a.name, "describe": a.describe()}
                    for a in s.assertions
                ],
            }
        )
    return {"scenarios": out}


def _parse_eval_season(season_name: Optional[str]) -> Optional[tuple[str, str]]:
    """Parse ``eval:<scenario>:<config>`` season labels.

    Returns ``None`` for non-eval seasons (so the UI can ignore them)
    and ``(scenario_name, config_label)`` for tagged eval rows.
    All eval-recorded sessions get this tag at write time in
    :func:`sea_of_colours.evals.runner.run_scenario`; the 12
    pre-tagger legacy rows were backfilled with
    ``eval:<scenario>:legacy`` tags so every eval session
    classifies through this one function — no fingerprint
    fallback required.
    """
    if not season_name or not season_name.startswith("eval:"):
        return None
    parts = season_name.split(":", 2)
    if len(parts) != 3:
        return None
    return parts[1], parts[2]


@app.get("/api/evals/sessions")
def api_evals_sessions(
    scenario: Optional[str] = Query(
        None, description="Filter to a single scenario name."
    ),
) -> dict[str, Any]:
    """Past eval sessions, optionally filtered to one scenario.

    Resolves each session's scenario + config from its
    ``eval:<scenario>:<config>`` season-name tag — newly-recorded
    eval runs set this in :func:`run_scenario` and legacy sessions
    were backfilled with ``eval:<scenario>:legacy`` tags by the
    one-shot in
    ``scripts/`` (see chat history). Untagged sessions are
    treated as gameplay and ignored here. Every returned entry
    carries everything the command-center UI needs to render a row
    without follow-up calls: ``passed`` (re-evaluated against the
    stored policy + post-night session state), ``per_assertion``
    for the verdict breakdown, ``policy`` to surface drop / chain
    coords, and a ``watch_url`` for the embedded iframe.

    Returned newest-first — the underlying
    :func:`SocStore.list_sessions` already orders by
    ``last_touched_at DESC``.
    """
    store = _store()
    scenarios_by_name = {s.name: s for s in SCENARIOS}
    rows = store.list_sessions()

    out: list[dict[str, Any]] = []
    for row in rows:
        sid = row["session_id"]
        tag = _parse_eval_season(row.get("season_name"))
        if tag is None:
            # Untagged = gameplay session. The watcher owns those,
            # not the eval command center.
            continue
        scen_name, config_label = tag
        is_tagged = True

        # Optional scenario filter — applied before policy lookup so
        # we skip expensive store calls for sessions the caller
        # isn't asking about.
        if scenario and scen_name != scenario:
            continue

        # Fetch the submitted policy. After a night resolves,
        # ``row.day`` has already advanced by one, so we look up
        # against day-1 first — that's where the harvester run we
        # want to inspect lives. For unresolved fixtures the
        # original day still works, so we fall back to that.
        day = int(row.get("day") or 0)
        p1_pol: list[dict[str, Any]] = []
        policy_day: Optional[int] = None
        for try_day in (day - 1, day):
            if try_day < 0:
                continue
            policies = store.list_policies(sid, try_day) or {}
            cand = policies.get("p1") or []
            if cand:
                p1_pol = list(cand)
                policy_day = try_day
                break
        night_resolved = policy_day is not None and policy_day < day

        # Evaluate the scenario's assertions against the stored
        # policy. Structural checks (HarvesterChainHits, MustAvoid,
        # EndsWithPickup, …) are pure functions of the move queue and
        # always reliable. Context-sensitive checks (MinExpectedValue
        # reads ``cell_purity``; NoSyntheticGreenSteps reads
        # ``is_synthetic_green``) inspect the live cell state — which
        # has been MUTATED by the resolved night for replay-enabled
        # sessions (the RED the agent harvested is gone, so
        # MinExpectedValue scores 0). For those rows we surface a
        # "n/a" verdict instead of a misleading FAIL — the pass
        # condition is still listed in the headline so the user
        # knows what was being tested; we just can't reconstruct
        # the answer from post-night state.
        _CONTEXT_SENSITIVE = ("MinExpectedValue", "NoSyntheticGreenSteps")
        per_assertion: list[dict[str, Any]] = []
        all_passed = True
        scen_def = scenarios_by_name.get(scen_name)
        if scen_def is not None and p1_pol:
            try:
                sess = soc_engine._hydrate_session(store, sid)
                ctx = AssertionContext(
                    session=sess,
                    player=scen_def.player,
                    day_at_run=(policy_day if policy_day is not None else day),
                )
                for a in scen_def.assertions:
                    if night_resolved and a.name in _CONTEXT_SENSITIVE:
                        per_assertion.append(
                            {
                                "name": a.name,
                                "passed": None,
                                "detail": "n/a (post-night state)",
                            }
                        )
                        continue
                    res = a.evaluate(p1_pol, context=ctx)
                    per_assertion.append(
                        {
                            "name": res.name,
                            "passed": res.passed,
                            "detail": res.detail,
                        }
                    )
                    if not res.passed:
                        all_passed = False
            except Exception:
                # Best-effort — surface the session without verdicts
                # if we can't hydrate (corrupt blob, missing schema).
                per_assertion = []
                all_passed = False

        drop_coord = next(
            (mv.get("at") for mv in p1_pol if mv.get("a") == "drop"), None,
        )
        n_steps = sum(1 for mv in p1_pol if mv.get("a") == "step")
        n_probes = sum(1 for mv in p1_pol if mv.get("a") == "probe")

        # Surface the Cortex agent identity (e.g. SOC_RED_REAPER_LIST
        # vs SOC_RED_REAPER_GRID) by reaching into SOC_AGENT_INVOCATION.
        # We take the LAST invocation on ``policy_day`` for the seat
        # under test — that's the row whose ``agent_id`` matches the
        # ``soc_submit_policy`` call (earlier rows on the same day are
        # ``soc_save_rationale`` followups or aborted Cortex attempts).
        # ``has_prompt`` lets the transcript tab tell the user up-front
        # whether the model's input was recorded (post-Phase-1 runs) or
        # is lost to history (legacy + heuristic-only runs).
        agent_id: Optional[str] = None
        has_prompt = False
        if (
            scen_def is not None
            and policy_day is not None
            and hasattr(store, "list_agent_invocations")
        ):
            try:
                invocations = store.list_agent_invocations(sid, day=policy_day)
            except Exception:
                invocations = []
            seat = scen_def.player
            seat_rows = [
                r for r in invocations
                if str(r.get("player") or "").lower() == seat
            ]
            if seat_rows:
                # ``list_agent_invocations`` orders by ``seq`` ASC, so
                # the last entry is the most recent turn for this seat.
                latest = seat_rows[-1]
                agent_id = latest.get("agent_id")
                has_prompt = bool(latest.get("prompt_excerpt"))

        out.append(
            {
                "session_id": sid,
                "scenario": scen_name,
                "config": config_label,
                "tagged": is_tagged,
                "agent_id": agent_id,
                "has_prompt": has_prompt,
                "passed": bool(per_assertion) and all_passed,
                "per_assertion": per_assertion,
                "drop": drop_coord,
                "n_steps": n_steps,
                "n_probes": n_probes,
                "n_moves": len(p1_pol),
                "day": row.get("day"),
                "policy_day": policy_day,
                "player": scen_def.player if scen_def else "p1",
                "watch_url": f"/?session={sid}",
            }
        )

    return {"sessions": out}


@app.get("/api/game/{game_id}/status")
def api_game_status(game_id: str) -> dict[str, Any]:
    try:
        status = soc_engine.get_session_status(_store_for(game_id), game_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # v1.11 — if the game has a slow (Cortex/harness) seat, make sure the
    # background worker is draining it so the agent thinks while the human
    # deliberates. Idempotent + cheap (no-op when nothing's pending).
    if _game_has_slow_bot(status.get("agents") or {}):
        _kick_bots(game_id)
    # Surface who's being waited on + the in-flight bot turn for the UI timer.
    status.update(_agent_status_meta(game_id, status))
    # v1.20 — which store owns THIS game. Backend is a per-game choice
    # (v1.14), so the header badge must not read the process default:
    # a memory game and a Snowflake season can be open on one server,
    # and only one of them keeps its replay tomorrow.
    backend_name = soc_backend.backend_for_session(game_id)
    status["backend"] = backend_name
    status["persists"] = backend_name != "memory"
    return status


def _queue_field(payload: dict[str, Any], *accepted: str) -> Any:
    """Read a submit endpoint's queue, refusing a near-miss field name.

    A misspelled key used to be indistinguishable from an empty
    submission: the seat locked a no-op turn and the response still
    said ``ok``. Anyone driving the API by hand — an attendee harness,
    one of our own scratch scripts — then spent a while debugging a
    game that had quietly agreed to do nothing. An empty queue is still
    legal (it's how you pass), but naming the wrong field is not.
    """
    for name in accepted:
        if name in payload:
            value = payload[name]
            return [] if value is None else value
    stray = [k for k, v in payload.items() if k != "player" and isinstance(v, list)]
    if stray:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown field {stray[0]!r}; send the queue as "
                f"{accepted[0]!r} (or omit it to pass)"
            ),
        )
    return []


@app.post("/api/game/{game_id}/orbit")
def api_game_orbit(game_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Submit a seat's Orbit phase action queue (v0.8.0).

    Mirrors :func:`api_game_policy` for the daytime ORBIT phase.
    Payload shape: ``{"player": "<seat_id>", "actions": [...]}`` where
    each action follows the discriminator schema declared in
    :mod:`sea_of_colours.game.policy`. v0.9.6 — accepts any seat
    in :attr:`GameSession.players` (1-4 seats).
    """
    player = payload.get("player")
    if not isinstance(player, str) or not player:
        raise HTTPException(status_code=400, detail="player is required")
    actions_field: Any = _queue_field(payload, "actions", "commands")
    pre_status = soc_engine.get_session_status(_store_for(game_id), game_id)
    slow = _game_has_slow_bot(pre_status.get("agents") or {})

    # ── Fast path: no slow agent — unchanged legacy behaviour. ──────────
    if not slow:
        with _game_lock(game_id):
            try:
                result = soc_engine.submit_orbit_actions(
                    _store_for(game_id), game_id, player, actions_field,
                    auto_fire_bots=True,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        status = soc_engine.get_session_status(_store_for(game_id), game_id)
        return {
            "ok": result["ok"],
            "errors": result.get("errors") or [],
            "pending_orbit": status.get("pending") or {},
            "phase": status.get("phase") or result.get("phase"),
            "day": status.get("day") or result.get("day"),
            "orbit_resolved": result.get("orbit_resolved", False),
            "credits": result.get("credits") or {},
            "probe_stock": result.get("probe_stock") or {},
            "log_tail": status.get("log_tail", []),
            "max_orbit_actions": result.get("max_orbit_actions"),
        }

    # ── Slow path: NON-BLOCKING (mirrors api_game_policy). ──────────────
    lk = _try_lock(game_id, _AGENT_SUBMIT_LOCK_WAIT_S)
    if lk is None:
        status = soc_engine.get_session_status(_store_for(game_id), game_id)
        return {
            "ok": True,
            "agent_busy": True,
            "errors": [],
            "pending_orbit": status.get("pending") or {},
            "phase": status.get("phase"),
            "day": status.get("day"),
            "orbit_resolved": False,
            "credits": {},
            "probe_stock": {},
            "log_tail": status.get("log_tail", []),
            "max_orbit_actions": None,
            **_agent_status_meta(game_id, status),
        }
    try:
        try:
            result = soc_engine.submit_orbit_actions(
                _store_for(game_id), game_id, player, actions_field,
                auto_fire_bots=False,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        lk.release()
    if result.get("ok"):
        _kick_bots(game_id)
    status = soc_engine.get_session_status(_store_for(game_id), game_id)
    orbit_resolved = (
        str(pre_status.get("phase")) == "orbit"
        and str(status.get("phase") or "") != "orbit"
    )
    return {
        "ok": result["ok"],
        "errors": result.get("errors") or [],
        "pending_orbit": status.get("pending") or {},
        "phase": status.get("phase") or result.get("phase"),
        "day": status.get("day") or result.get("day"),
        "orbit_resolved": orbit_resolved,
        "credits": result.get("credits") or {},
        "probe_stock": result.get("probe_stock") or {},
        "log_tail": status.get("log_tail", []),
        "max_orbit_actions": result.get("max_orbit_actions"),
        **_agent_status_meta(game_id, status),
    }


@app.post("/api/game/{game_id}/policy")
def api_game_policy(game_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    player = payload.get("player")
    if not isinstance(player, str) or not player:
        raise HTTPException(status_code=400, detail="player is required")
    moves_field: Any = _queue_field(payload, "moves", "commands")
    pre_status = soc_engine.get_session_status(_store_for(game_id), game_id)
    slow = _game_has_slow_bot(pre_status.get("agents") or {})

    # ── Fast path: no slow agent — unchanged legacy behaviour. ──────────
    # v0.9.12 — serialise the hydrate->mutate->save for this game so two
    # humans submitting near-simultaneously can't lost-update each other.
    if not slow:
        with _game_lock(game_id):
            try:
                result = soc_engine.submit_policy(
                    _store_for(game_id), game_id, player, moves_field,
                    auto_fire_bots=True,
                )
            except KeyError as exc:
                # v0.9.5 — surface the full traceback so we can pin down the
                # bare ``["x"]`` lookup; keep raising so the FE is unchanged.
                print("\n[soc] submit_policy KeyError traceback ↓↓↓",
                      file=sys.stderr, flush=True)
                traceback.print_exc(file=sys.stderr)
                print("[soc] submit_policy KeyError traceback ↑↑↑\n",
                      file=sys.stderr, flush=True)
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        status = soc_engine.get_session_status(_store_for(game_id), game_id)
        return {
            "ok": result["ok"],
            "errors": result.get("errors") or [],
            "pending": status.get("pending") or {},
            "phase": status.get("phase") or result.get("phase"),
            "day": status.get("day") or result.get("day"),
            "night_resolved": result.get("night_resolved", False),
            "log_tail": status.get("log_tail", []),
            "moves_stashed": result.get("moves_stashed", 0),
        }

    # ── Slow path: game has a Cortex/harness seat — NON-BLOCKING. ───────
    # v1.11 — we no longer synchronously drive the agent (which could hang
    # the request for up to the agent's ~80s turn). Instead: stash the human
    # WITHOUT firing bots (auto_fire_bots=False resolves inline only if the
    # agent already submitted during deliberation), then kick the background
    # worker and return immediately. The browser holds a "waiting on <agent>"
    # frame and polls /status for the resolution. If the agent is mid-turn
    # and holding the lock, we can't stash safely — return ``agent_busy`` so
    # the browser keeps its frame and retries once the agent lands.
    lk = _try_lock(game_id, _AGENT_SUBMIT_LOCK_WAIT_S)
    if lk is None:
        status = soc_engine.get_session_status(_store_for(game_id), game_id)
        return {
            "ok": True,
            "agent_busy": True,
            "errors": [],
            "pending": status.get("pending") or {},
            "phase": status.get("phase"),
            "day": status.get("day"),
            "night_resolved": False,
            "log_tail": status.get("log_tail", []),
            "moves_stashed": 0,
            **_agent_status_meta(game_id, status),
        }
    try:
        try:
            result = soc_engine.submit_policy(
                _store_for(game_id), game_id, player, moves_field,
                auto_fire_bots=False,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        lk.release()
    if result.get("ok"):
        _kick_bots(game_id)  # background: fire agent + resolve
    status = soc_engine.get_session_status(_store_for(game_id), game_id)
    # Resolved inline only if the agent had already submitted (both_ready).
    night_resolved = (
        str(pre_status.get("phase")) == "planning"
        and str(status.get("phase") or "") != "planning"
    ) or (int(status.get("day") or 0) > int(pre_status.get("day") or 0))
    return {
        "ok": result["ok"],
        "errors": result.get("errors") or [],
        "pending": status.get("pending") or {},
        "phase": status.get("phase") or result.get("phase"),
        "day": status.get("day") or result.get("day"),
        "night_resolved": night_resolved,
        "log_tail": status.get("log_tail", []),
        "moves_stashed": result.get("moves_stashed", 0),
        **_agent_status_meta(game_id, status),
    }


@app.get("/api/game/{game_id}/replay")
def api_game_replay(
    game_id: str,
    day_from: Optional[int] = Query(None, alias="day_from"),
    day_to: Optional[int] = Query(None, alias="day_to"),
) -> dict[str, Any]:
    try:
        reply = soc_engine.get_replay(
            _store_for(game_id), game_id, day_from=day_from, day_to=day_to,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # The legacy shape was {mode, phase, width, height, frames}. Keep
    # `frames` populated (flattened across all days) for back-compat,
    # but also expose the new `days` / `day_index` payload Phase 4 uses.
    #
    # Payload-size guard: a 4-seat, multi-night replay can balloon past
    # half a gigabyte and OOM-crash the browser tab on parse. Two big
    # offenders, both pure duplication:
    #   1. ``days[].frames`` / ``days[].frames_compact`` repeat every
    #      frame the flat ``frames`` list already carries. The watcher
    #      frontend reads ``frames`` + ``day_index`` only, so we ship
    #      ``days`` as lightweight ``{day}`` markers (tests assert the
    #      day numbers, nothing reads the nested frames over HTTP).
    #   2. Each frame's ``cells_player_p1..pN`` are exact copies of
    #      ``cells_by_seat[pN]`` (the frontend's primary read). We strip
    #      the aliases from the wire frames; ``cells_by_seat`` and the
    #      single ``cells_player`` (still used by the reveal animation)
    #      remain. The engine reply / stored frames are untouched.
    frames: list[dict[str, Any]] = []
    for day_bucket in reply.get("days") or []:
        for f in day_bucket.get("frames") or []:
            frames.append(
                {k: v for k, v in f.items() if not k.startswith("cells_player_")}
            )
    slim_days = [{"day": b.get("day")} for b in (reply.get("days") or [])]
    return {
        "mode": "replay",
        "phase": reply.get("phase"),
        "width": reply.get("width"),
        "height": reply.get("height"),
        "frames": frames,
        "days": slim_days,
        "day_index": reply.get("day_index") or [],
        "total_frames": reply.get("total_frames", len(frames)),
        "catapult_by_day": reply.get("catapult_by_day") or {},
        # v0.9.10 — forward the N-seat fields from the engine reply so
        # the frontend can populate __SOC_PLAYERS__ from replay mode.
        "players": reply.get("players") or [],
        "cumulative_shipped_score": reply.get("cumulative_shipped_score") or {},
        # v1.x — per-seat final settlement breakdown (shipped / green penalty
        # / vault-red fire-sale / final) for the animated +/- settlement lines.
        "settlement": reply.get("settlement") or {},
        # v1.x — authoritative post-settlement hoard snapshot per seat so the
        # replay vault reconstructor shows the SETTLED vault (not stale RED)
        # on the terminal RESOLVE tick.
        "final_hoard": reply.get("final_hoard") or {},
        "orbit_log_by_day": reply.get("orbit_log_by_day") or {},
        "log_by_day": reply.get("log_by_day") or {},
        # v0.9.11 — per-day station-observation snapshots ({pre,post})
        # + observable orbital-activity tallies for the Pre-Orbital
        # Recap / Post-Orbital Briefing reports.
        "station_obs_by_day": reply.get("station_obs_by_day") or {},
        "orbital_activity_by_day": reply.get("orbital_activity_by_day") or {},
        # Ordered per-day orbital event lists (probe/orblift/EMP launches,
        # recoveries, collisions). The engine produces these but the HTTP
        # layer previously dropped them; the inline station UI's hover-card
        # event log reads this. Kept lightweight (event tallies, not frames).
        "orbital_events_by_day": reply.get("orbital_events_by_day") or {},
        # v1.x — discovery-triggered REDSIGN beacons (RULEBOOK §4.11).
        # Full final list with per-region ``day``; the client day-gates and
        # paints the persistent pulse overlay in replay.
        "redsign": reply.get("redsign") or [],
        # v1.0 — end-of-game support for the replay end screen.
        "player_names": reply.get("player_names") or {},
        # v0.9.18 — forward custom identity profiles (tags + colors) so the
        # replay/watch UI renders named, coloured seats instead of P1/P2.
        "player_profiles": reply.get("player_profiles") or {},
        "is_season_complete": bool(reply.get("is_season_complete", False)),
        "season_day_cap": reply.get("season_day_cap") or 0,
        # v1.22 — the map's identity and the season's extraction curve.
        # ``get_replay`` has returned both since v1.20, but this response is
        # an explicit key-by-key projection and they were never added to it,
        # so the header's SEED / RED ON MAP / EXTRACTED readout has been
        # reading ``undefined`` and hiding itself for its whole life.
        # tests/test_replay_payload_keys.py now pins the projection.
        "seed": reply.get("seed"),
        "extraction": reply.get("extraction") or None,
    }


@app.get("/api/game/{game_id}/summary")
def api_game_summary(game_id: str) -> dict[str, Any]:
    """End-of-game results payload (rankings, tallies, manifest, chart)."""
    try:
        return soc_engine.get_endgame_summary(_store_for(game_id), game_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/game/{game_id}/day-index")
def api_game_day_index(game_id: str) -> dict[str, Any]:
    """Compact per-day metadata for the Phase-4 scrub bar header."""
    try:
        reply = soc_engine.get_replay(_store_for(game_id), game_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "session_id": game_id,
        "day_index": reply.get("day_index") or [],
    }


@app.get("/api/game/{game_id}/view")
def api_game_view(
    game_id: str,
    player: str = Query("p1"),
) -> dict[str, Any]:
    """Player-only percept. Use ``/observer`` for the cheat full-map view."""
    if not _is_valid_seat_slug(player):
        raise HTTPException(status_code=400, detail="unknown player slug")
    try:
        return soc_engine.get_view(_store_for(game_id), game_id, player)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _advisor_availability(game_id: str) -> tuple[bool, str]:
    """Can this game offer "ask V12" right now, and if not, why not?

    Two independent things have to be true, and they fail for different
    reasons, so they are reported separately rather than as one shrug:

    * **Cortex credentials.** The advisor is a live LLM round trip. With no
      PAT the harness does not raise — it quietly falls through to its
      fallback and returns a plan nobody thought about, which is worse
      than no button at all.
    * **A Snowflake-backed game.** Not a technical requirement of the call
      (inference is just an HTTPS request with a PAT, and the advisor
      writes no memory), but it keeps one rule in the product instead of
      two: LLM agents are offered on Snowflake games. A button that
      appears on a memory game while ``tabula_v12`` is missing from that
      same game's seat dropdown would read as a bug.
    """
    from sea_of_colours.orchestrator_2.cortex_chat import credentials_status

    store = _store_for(game_id)
    if soc_backend.snowpark_session_for(store) is None:
        return (False, "this game is stored in memory, not Snowflake")
    ready, reason = credentials_status()
    if not ready:
        return (False, f"Snowflake credentials missing: needs {reason}")
    return (True, "")


@app.get("/api/game/{game_id}/advisor")
def api_game_advisor_status(game_id: str) -> dict[str, Any]:
    """Whether the V12 advisor button should exist for this game.

    Cheap and side-effect free — the client asks on load so it can decide
    whether to render the control at all.
    """
    available, reason = _advisor_availability(game_id)
    return {"available": available, "reason": reason}


@app.post("/api/game/{game_id}/advisor")
def api_game_advisor(
    game_id: str,
    player: str = Query("p1"),
) -> dict[str, Any]:
    """Ask V12 what it would do on this seat's board, WITHOUT playing it.

    Runs the harness's read-only advisor path (``submit=False``), which
    executes the whole THINK -> PLAN -> PACKAGE -> SANITIZE pipeline and
    returns the trace, but submits nothing and writes no memory. That last
    part is the important one: the seat belongs to a human, and V12's
    journal / hazard memory / reflection anchor are keyed by
    ``(session, seat)``. Writing them here would fabricate a history of
    turns V12 never played, and the human's own choices would silently
    become "what V12 did last night" in a later prompt.

    The cost of that decision is that the advice is *stateless* — each
    call reasons from the live board and last night's engine-truth replay,
    with no thread back through earlier nights. It is a second opinion on
    this position, not a season-long strategy, and the UI says so.
    """
    if not _is_valid_seat_slug(player):
        raise HTTPException(status_code=400, detail="unknown player slug")

    available, reason = _advisor_availability(game_id)
    if not available:
        raise HTTPException(status_code=409, detail=reason)

    store = _store_for(game_id)
    try:
        status = soc_engine.get_session_status(store, game_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if player not in (status.get("players") or []):
        raise HTTPException(status_code=404, detail=f"no seat {player}")
    if status.get("is_season_complete"):
        raise HTTPException(status_code=409, detail="the season is over")
    phase = str(status.get("phase") or "").lower()
    if phase != Phase.PLANNING.value:
        # The harness's orbit branch SUBMITS — it has no read-only path —
        # so routing an orbit turn here would silently play the seat.
        raise HTTPException(
            status_code=409,
            detail=f"the advisor covers night planning; this seat is in {phase}",
        )

    from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import harness as v12

    view = soc_engine.get_view(store, game_id, player)
    started = time.time()
    try:
        res = v12.run(
            store=store, session_id=game_id, player=player, view=view,
            submit=False,
        )
    except Exception as exc:  # noqa: BLE001 — surface any harness fault as 502
        raise HTTPException(
            status_code=502, detail=f"V12 failed to answer: {exc}",
        ) from exc

    if res.get("submitted_policy"):
        # Belt and braces. If a future edit ever makes the advisor path
        # submit, fail loudly here rather than quietly stealing the turn.
        raise HTTPException(
            status_code=500,
            detail="advisor submitted a policy — refusing to report it",
        )

    extras = res.get("extras") or {}
    directive = extras.get("thinker_directive") or {}
    return {
        "ok": True,
        "player": player,
        "day": int(status.get("day") or 0),
        "elapsed_ms": int((time.time() - started) * 1000),
        "moves": res.get("moves") or [],
        "thinking": {
            "reasoning": extras.get("thinker_reasoning") or "",
            "api": extras.get("thinker_api") or "",
            "ms": int(extras.get("thinker_ms") or 0),
            "retried": bool(extras.get("thinker_retried")),
            "option_menu": extras.get("option_menu_block") or "",
            "intent": extras.get("agent_intent") or "",
            "reflection": extras.get("agent_reflection") or "",
        },
        # v1.19 — the FULL, untruncated prompts for each pass. The harness
        # has always returned these "so a human can audit the input the same
        # way the model sees it"; the advisor just never forwarded them. The
        # audit table's copy is capped at 32k, so this is the only place the
        # whole thing is available. Powers the card download.
        "prompts": {
            "think": extras.get("thinker_prompt") or "",
            "plan": extras.get("plan_prompt") or "",
            "mover": extras.get("mover_prompt") or "",
        },
        "plan": {
            "posture": directive.get("posture") or "",
            "note": directive.get("note") or "",
            "plan_ids": list(directive.get("plan") or []),
            "targets": directive.get("targets") or [],
            "avoid": directive.get("avoid") or [],
            "chaff_react": directive.get("chaff_react"),
            "situational": directive.get("situational") or "",
            "selected_options": list(extras.get("selected_option_ids") or []),
            "sanitizer_changes": list(extras.get("sanitizer_changes") or []),
            "packager_used": bool(extras.get("packager_used")),
            "fallback_used": bool(extras.get("fallback_used")),
        },
    }


def _named_agent_profiles(
    agents: Mapping[str, str] | None,
    supplied: Mapping[str, Mapping[str, str]] | None,
) -> dict[str, dict[str, str]]:
    """Profiles for seats holding an attendee's named fork.

    Deliberately narrow. Built-in seats (``human``, the heuristics, stock
    V12) keep the engine's Latin naming, because those house names are a
    feature of the game and several tests pin them. A discovered fork is
    the case the flavour fails: somebody chose that name and needs to
    see it on the board.
    """
    if not agents:
        return {}
    try:
        from sea_of_colours.evals import dispatch
        from sea_of_colours.orchestrator_2 import agent_manifest
        from sea_of_colours.orchestrator_2 import binding_registry as br
    except Exception:
        return {}

    found, _problems = agent_manifest.discover()
    forks = {man.label for man in found}
    if not forks:
        return {}

    seats = {
        seat: label
        for seat, label in agents.items()
        if str(label or "").strip().lower() in forks
        and not (supplied or {}).get(seat, {}).get("display_name")
    }
    if not seats:
        return {}
    _ = br  # imported to force discovery to have run at least once
    return dispatch.seat_profiles(seats)


@app.get("/api/game/{game_id}/observer")
def api_game_observer(game_id: str) -> dict[str, Any]:
    """Omniscient observer mosaic — for the GRAPHICS drawer only."""
    try:
        return soc_engine.get_observer(_store_for(game_id), game_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/game/{game_id}/agent-log")
def api_game_agent_log(
    game_id: str,
    day: int | None = Query(
        None,
        description="Optional day filter (1-indexed). Omit for all days.",
    ),
    player: str | None = Query(
        None,
        description="Optional seat filter ('p1' or 'p2'). Omit for all seats.",
    ),
) -> dict[str, Any]:
    """Replay-mode access to ``SOC_AGENT_INVOCATION`` rows so the
    front-end AGENT tab can fill in rationale for a persisted season.

    The live AGENT panel captures rationales inline from the
    ``/agent/think`` response; this endpoint is the equivalent
    surface for sessions loaded into watcher mode.
    """
    if player is not None and not _is_valid_seat_slug(player):
        raise HTTPException(status_code=400, detail="unknown player slug")
    store = _store_for(game_id)
    if not hasattr(store, "list_agent_invocations"):
        # In-memory store has no agent-log persistence; return empty
        # so the frontend's lazy-fetch path silently no-ops.
        return {"session_id": game_id, "day": day, "player": player, "invocations": []}
    try:
        rows = store.list_agent_invocations(game_id, day=day)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if player is not None:
        rows = [r for r in rows if str(r.get("player") or "").lower() == player]
    # Trim to the fields the frontend actually renders so the
    # response stays compact. ``prompt_excerpt`` stores the full
    # Cortex prompt (typed STRING in Snowflake, ~28KB ceiling per
    # ``PROMPT_PAYLOAD_CAP_CHARS``) — surfacing it here is what
    # powers the /evals TRANSCRIPT tab. Legacy rows with
    # ``prompt_excerpt=NULL`` predate this column being populated;
    # the UI renders them as "(prompt not recorded)".
    out = []
    for r in rows:
        tool_calls = r.get("tool_calls")
        # Snowpark hands us the column either as a parsed JSON list
        # or as the raw string we PARSE_JSON'd on insert; normalise.
        if isinstance(tool_calls, str):
            try:
                tool_calls = json.loads(tool_calls)
            except (json.JSONDecodeError, TypeError):
                tool_calls = []
        elif tool_calls is None:
            tool_calls = []
        out.append(
            {
                "day": r.get("day"),
                "seq": r.get("seq"),
                "agent_id": r.get("agent_id"),
                "player": r.get("player"),
                "runtime": r.get("runtime") or _infer_runtime(r.get("agent_id")),
                "prompt": r.get("prompt_excerpt"),
                "rationale": r.get("rationale"),
                "response_text": r.get("response_text"),
                "tool_calls": tool_calls,
                "ms_elapsed": r.get("ms_elapsed"),
                "status": r.get("status"),
            }
        )
    return {
        "session_id": game_id,
        "day": day,
        "player": player,
        "invocations": out,
    }


# NOT named `cards.md`: the root Markdown route is `/{name:path}.md`, and
# `:path` matches slashes, so it claims every URL ending in .md anywhere
# in the tree and answers 404 for anything off its allowlist. The
# downloaded filename comes from Content-Disposition regardless.
@app.get("/api/game/{game_id}/agent-cards")
def api_game_cards_markdown(
    game_id: str,
    day: int | None = Query(None, description="One day, or omit for all."),
    player: str | None = Query(None, description="One seat, or omit for all."),
    fmt: str = Query("md", description="'md' to feed a model, 'html' to read."),
) -> Response:
    """The agent's turn(s) as a downloadable card.

    The AGENT tab can already show a rationale, but reading a whole
    season's thinking in a side panel is miserable and copying it out is
    worse. This hands over the same rows as a file: prompt, reasoning,
    orders and timing per turn, in play order.

    Works for any persisted season, whoever played it — a headless
    `soc season` run and a game played in the browser leave the same
    rows. The in-memory backend keeps no invocations, so a memory game
    downloads an honest note saying so rather than an empty file.
    """
    if player is not None and not _is_valid_seat_slug(player):
        raise HTTPException(status_code=400, detail="unknown player slug")

    from sea_of_colours.evals import cards as soc_cards

    store = _store_for(game_id)
    try:
        status = soc_engine.get_session_status(store, game_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    season = str(status.get("season_name") or game_id)

    rows: list[dict[str, Any]] = []
    if hasattr(store, "list_agent_invocations"):
        rows = list(store.list_agent_invocations(game_id, day=day) or [])
    if player is not None:
        rows = [r for r in rows if str(r.get("player") or "").lower() == player]
    rows.sort(key=lambda r: (int(r.get("day") or 0), int(r.get("seq") or 0)))

    # One card per TURN, not per row. A V12-family harness writes one
    # invocation row per pass — reasoning, decision, packager — so a
    # row-per-card view listed every turn three times, identically
    # labelled, with the thinking on one card and the option ids it
    # chose on the next (v1.41).
    normalised = [
        soc_cards.normalise_group(g, season=season, session_id=game_id)
        for g in soc_cards.group_rows(rows)
    ]
    want_html = str(fmt).lower() == "html"
    empty_note = (
        "No agent turns were recorded for this session. A game played "
        "entirely by humans has no cards, and the in-memory backend "
        "keeps none — run on `file` or `snowflake` to retain them."
    )

    if want_html:
        # Rendered even when empty: the page explains itself, where a
        # zero-byte download just looks broken.
        body = soc_cards.render_html(
            normalised, title=f"{season} — agent cards",
        )
        media, ext = "text/html; charset=utf-8", "html"
    else:
        body = (
            soc_cards.render_many(normalised, title=f"{season} — agent cards")
            if normalised
            else f"# {season} — agent cards\n\n{empty_note}\n"
        )
        media, ext = "text/markdown; charset=utf-8", "md"

    stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in season)
    if day is not None:
        stem += f"_d{day:02d}"
    if player:
        stem += f"_{player}"
    # HTML opens in a tab (inline); Markdown is only useful as a file.
    disposition = (
        f'inline; filename="{stem}_cards.html"' if want_html
        else f'attachment; filename="{stem}_cards.md"'
    )
    return Response(
        content=body,
        media_type=media,
        headers={
            "Content-Disposition": disposition,
            **_NO_CACHE_HEADERS,
        },
    )


# v1.43 — the `/api/battles/*` fast-diff routes lived here. They drove
# a live mode in the battle room: pick a frozen turn, pick an agent, get
# one turn back diffed against V12. The turn lab does that on turns that
# were actually played rather than constructed ones, so the routes went
# with the module behind them. `/battles/` still serves the recorded
# room, which is static and never fetched anything.


def _infer_runtime(agent_id: str | None) -> str:
    """Best-effort runtime inference for legacy rows that pre-date the
    ``runtime`` column. ``RED_HARVEST`` is the heuristic; anything
    else (Cortex agents like ``SOC_RED_REAPER``) is treated as
    cortex."""
    if not agent_id:
        return ""
    return "heuristic" if str(agent_id).upper() == "RED_HARVEST" else "cortex"


@app.post("/api/game/{game_id}/agent/think")
def api_agent_think(
    game_id: str,
    player: str = Query("p1"),
    runtime: str | None = Query(
        None,
        description=(
            "Optional per-call runtime override: 'heuristic' (RED_HARVEST) "
            "or 'red_harvest_lite' (RED_HARVEST_LITE, weapons disabled). "
            "Defaults to 'heuristic'."
        ),
    ),
) -> dict[str, Any]:
    """Run one deterministic agent turn for ``player`` end-to-end.

    Fetches the player view, runs the in-process heuristic, submits the
    resulting policy through the SOC engine, and writes an audit row to
    ``SOC_AGENT_INVOCATION``. The envelope includes ``agent_id`` so the
    caller knows who played.

    This route is heuristic-only. **LLM seats do not come through here**
    — seat the player as ``tabula_v12`` at game creation and the
    orchestrator dispatches V12 automatically, on any storage backend.
    """
    if not _is_valid_seat_slug(player):
        raise HTTPException(status_code=400, detail="unknown player slug")
    if runtime == "cortex":
        # Explicit, actionable 410 rather than a generic 400: the old
        # Agents-API runtime was a documented part of this route, so
        # callers still asking for it deserve to be told where it went.
        raise HTTPException(
            status_code=410,
            detail=(
                "The 'cortex' runtime was removed along with the Cortex "
                "Agents-API specs. The LLM agent is now V12: seat a "
                "player as 'tabula_v12' when creating the game and the "
                "orchestrator dispatches it (works on any SOC_BACKEND; "
                "needs SNOWFLAKE_PAT). Use 'heuristic' for RED_HARVEST."
            ),
        )
    if runtime is not None and runtime not in _HEURISTIC_RUNTIMES:
        raise HTTPException(
            status_code=400,
            detail=f"runtime must be one of {sorted(_HEURISTIC_RUNTIMES)}",
        )
    try:
        return run_agent_turn(
            _store_for(game_id), game_id, player, runtime_override=runtime,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/meta/agents")
def api_meta_agents() -> dict[str, Any]:
    """The New Game roster, straight from the binding registry.

    The dropdown was a hardcoded list in ``app.js``, so registering a
    hackathon fork took a frontend edit too. Teams missed it and hit the
    worst kind of failure: the agent works, the eval runs, but it cannot
    be picked in a game. Serving the roster means one registry entry is
    enough.
    """
    from sea_of_colours.orchestrator_2.binding_registry import selectable_agents

    return {"agents": selectable_agents()}


@app.get("/api/meta/whoami")
def api_meta_whoami() -> dict[str, Any]:
    """Who is serving this port — for ``run_web.py --replace`` (v1.26).

    The launcher needs to answer "may I kill whatever owns 8000?", and
    the honest form of that question is asked of the *port*, not of the
    process table: matching on a command line guesses at identity, while
    an answer on the socket proves it. So this returns the one thing
    that makes the kill safe (``app``) and the one thing that makes it
    precise (``pid`` — no ``lsof``, no ambiguity about which of several
    python processes is the listener).

    ``root`` is here so a replace can *say* which checkout it is about to
    stop, which matters on a machine running two of them.

    Deliberately unauthenticated and deliberately dull: it discloses a
    pid and a path to anyone who can already reach the server, which on
    a ``--lan`` bind is the same set of people who can take a seat.
    """
    return {
        "app": _APP_IDENT,
        "pid": os.getpid(),
        "root": str(_REPO_ROOT),
        "started_at": _STARTED_AT,
        "uptime_s": round(time.time() - _STARTED_AT, 1),
    }


@app.get("/api/meta/backend")
def api_meta_backend() -> dict[str, Any]:
    """Diagnostic — which storage backend is the proxy talking to, and why.

    ``reason`` matters as much as ``backend``: with auto-detection the
    answer to "where did my game go?" is usually a sentence about a
    missing dependency or config, not the backend name.

    v1.14 — ``backend`` is only the *default* now. ``options`` is what
    the New Game modal renders, one entry per per-game choice.
    """
    res = soc_backend.resolution()
    return {
        "backend": res.name,
        "requested": res.requested,
        "reason": res.reason,
        "fix": res.fix,
        "persists": res.persists,
        "summary": res.summary(),
        "options": _backend_options(),
    }


@app.get("/api/meta/status")
def api_meta_status() -> dict[str, Any]:
    """Health for the landing-page status badge.

    Reports the backend mode, per-store health (the composite store knows
    whether Snowflake is reachable vs local-only), and whether a public
    tunnel is currently up. The badge maps this to local / snowflake / off.
    """
    from server import tunnel as soc_tunnel

    res = soc_backend.resolution()
    backend = res.name
    stores: dict[str, str] = {}
    store = _store()
    health = getattr(store, "health", None)
    if callable(health):
        try:
            stores = health()
        except Exception:
            stores = {}
    else:
        # Single-backend server: the active store is simply "ok".
        stores = {backend: "ok"}
    return {
        "backend": backend,
        "persists": res.persists,
        "reason": res.reason,
        "stores": stores,
        "tunnel": soc_tunnel.status(),
    }


@app.post("/api/tunnel/start")
def api_tunnel_start(request: Request) -> dict[str, Any]:
    """Start (or reuse) a public quick tunnel to this server.

    Targets the port the caller connected on (so a dev server on :8000
    just works), falling back to 8000. Returns the public URL once a
    provider publishes one, along with which provider won — see
    ``server/tunnel.py`` for why there is more than one.

    v1.16 — the budget has to cover the whole provider list, not just the
    first. Capping it lower would mean a network that blocks provider one
    never gets to try provider two, which is the entire point of having
    a list. The client polls ``/api/tunnel/status`` regardless, so a slow
    answer here degrades to a spinner rather than a failure.

    v1.17 — raised to 60s because each provider now also has to clear the
    DNS acceptance gate before it counts, and the whole reason the gate
    exists is to hand over to the next provider. Budgeting only for the
    happy path would starve the fallback of the time it needs.

    v1.46 — 75s, and the number is derived rather than picked. The gate is
    now charged to the provider that runs it, and each attempt holds back
    a floor for the providers behind it (``_provider_floor``). At 60s that
    arithmetic squeezed *cloudflare* — the one whose hostname survives the
    session — down to an 11s publish window and a single DNS lookup, which
    is how you end up on the rotating fallback by accident. 75 is the
    point where the preferred provider gets its full 20s and three
    lookups while localhost.run keeps its 37s floor intact.

    This only ever matters when things go wrong: the happy path is a URL
    at ~6s and an accepted name at ~18s, and the client polls
    ``/api/tunnel/status`` regardless, so a slow answer here is a spinner
    rather than a failure.
    """
    from server import tunnel as soc_tunnel

    port = request.url.port or 8000
    return soc_tunnel.start(int(port), wait_s=75.0)


@app.get("/api/tunnel/status")
def api_tunnel_status() -> dict[str, Any]:
    from server import tunnel as soc_tunnel

    return soc_tunnel.status()


@app.post("/api/tunnel/stop")
def api_tunnel_stop() -> dict[str, Any]:
    from server import tunnel as soc_tunnel

    return soc_tunnel.stop()


def _lan_ip() -> Optional[str]:
    """Best-effort primary LAN IPv4 of the host running this server.

    Opens a UDP socket toward a public address and reads back the local
    endpoint the OS picked — this resolves the address other devices on
    the same network would use to reach us, without sending any packets
    or requiring internet access. Returns ``None`` (and the caller falls
    back to the request origin) if the host is fully offline.
    """
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            return None
    finally:
        s.close()
    if not ip or ip.startswith("127."):
        return None
    return ip


def _lan_port_open(ip: str, port: int, timeout: float = 0.4) -> bool:
    """Does *this* server actually accept connections on ``ip:port``?

    v1.15 — knowing the host's LAN IP is not the same as being reachable
    at it, and conflating the two silently broke every LAN invite. The
    default `run_web.py` binds 127.0.0.1, so the modal would resolve a
    perfectly correct LAN address, encode it in a QR, and the phone would
    get connection-refused with nothing on screen to explain why.

    A TCP connect to our own advertised address settles the bind
    question exactly: if nothing is listening there, this fails, and
    that is the case that was silently breaking every LAN invite.

    It is **not** proof a phone can connect. A same-host connection to
    your own LAN IP is short-circuited by the kernel and never traverses
    the network stack the macOS application firewall filters, so a
    machine in "block all incoming" + stealth mode still answers itself
    while refusing the phone. Treat True as "the server is listening",
    not "the network is clear" — which is why the invite modal still
    names the firewall as a possible cause.

    Short timeout: this is a same-machine round trip, and the invite
    modal is waiting on it.
    """
    import socket as _socket

    try:
        with _socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


_FIREWALL_CACHE: dict[str, Optional[bool]] = {}


def _macos_firewall_blocks_incoming() -> Optional[bool]:
    """Is the macOS application firewall set to block incoming?

    This is the difference between the two reasons a LAN invite fails,
    and they need opposite fixes: a loopback bind is solved by
    ``run_web.py --lan``, a blocking firewall is not solved by anything
    on our side. Telling someone who already used ``--lan`` to use
    ``--lan`` is worse than saying nothing.

    Returns None when the question doesn't apply (not macOS) or can't be
    answered, so callers can stay quiet rather than guess. Cached: the
    answer changes about once a year and this is on the invite path.
    """
    if "blocks" in _FIREWALL_CACHE:
        return _FIREWALL_CACHE["blocks"]
    result: Optional[bool] = None
    fw = "/usr/libexec/ApplicationFirewall/socketfilterfw"
    if sys.platform == "darwin" and os.path.exists(fw):
        try:
            out = subprocess.run(
                [fw, "--getglobalstate"],
                capture_output=True, text=True, timeout=2.0,
            ).stdout.lower()
            # "State = 2" is block-all. State 1 lets allowed apps through,
            # which we can't judge from here, so only the unambiguous
            # case warns — crying wolf here would push people onto a
            # tunnel they don't need.
            result = "state = 2" in out or "blocking all" in out
        except Exception:
            result = None
    _FIREWALL_CACHE["blocks"] = result
    return result


@app.get("/api/meta/lan")
def api_meta_lan(request: Request) -> dict[str, Any]:
    """Report the host's LAN IP *and whether it is usable*.

    The invite modal builds phone-reachable URLs from this instead of
    ``localhost``, which on a phone points at the phone itself.
    ``lan_ip`` is ``None`` when offline; ``lan_reachable`` is False when
    the address exists but this server is not listening on it, which is
    the default for ``python run_web.py`` and needs ``--lan``.
    """
    import socket as _socket

    ip = _lan_ip()
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    reachable = bool(ip) and _lan_port_open(ip, port)
    firewalled = _macos_firewall_blocks_incoming()
    if not reachable and firewalled:
        # Both causes present as the same silent timeout, and this one
        # can't be fixed by a flag — so name it first, or they'll keep
        # restarting the server and getting nowhere.
        hint = ("macOS is set to block all incoming connections, so other "
                "devices can't reach this server even on the same Wi-Fi. "
                "Turn it off in System Settings → Network → Firewall, or "
                "use a tunnel, which needs no firewall change.")
    elif not reachable:
        hint = ("This server is only listening on localhost, so a phone "
                "cannot reach it. Restart with: python run_web.py --lan")
    elif firewalled:
        hint = ("macOS is set to block all incoming connections. This "
                "server answers itself, but a phone may still be refused "
                "— if it hangs, that's why.")
    else:
        hint = ""
    return {
        "lan_ip": ip,
        "lan_port": port,
        "lan_reachable": reachable,
        "lan_firewalled": firewalled,
        "lan_hint": hint,
        "hostname": _socket.gethostname(),
    }
