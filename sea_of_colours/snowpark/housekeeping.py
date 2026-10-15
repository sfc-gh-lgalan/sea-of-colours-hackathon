"""Pruning stores — the bit nobody writes until the disk is full.

A season is cheap to make and expensive to keep: ``frames.jsonl`` is
about 96% of a session directory and a full LLM season runs to 170MB,
so a few weeks of development leaves ten gigabytes of runs nobody will
open again. Nothing in the repo deleted more than one session at a
time before this.

**It deletes through a running server when there is one.** The obvious
implementation is ``rm -rf seasons/*``, and it does work — the file
store finds sessions by scanning for ``meta.json``, so there is no
index to corrupt. But a live server holds sessions in memory and routes
by an in-process registry, so pulling the directories out from under it
leaves it serving rows for games that no longer exist. Going through
``DELETE /api/game/{id}`` keeps the server's own bookkeeping straight,
and costs only that the server has to be up. Direct-to-store is the
fallback for when it is not.

**Two things are never deleted.** Sessions whose name starts with
``eval:`` are refused by the server itself and skipped here too, and
``turnlab/data/`` is a separate store holding the checked-in board
library — this module has no path into it at all.
"""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

#: Where the file backend keeps sessions unless ``SOC_STORE_DIR`` says
#: otherwise. Matches ``file_store.DEFAULT_STORE_DIR``.
DEFAULT_STORE_DIR = Path("./seasons")

#: Exported per-turn cards. Not sessions — but named for the session
#: they came from, so they are orphaned the moment it goes.
CARD_DIR = Path("reports/seasons")

#: Never touched, for any flag. The lab is a different store with a
#: different lifetime, and its boards are checked in.
NEVER = ("turnlab/data",)


@dataclass
class Candidate:
    """One session considered for deletion, and why it was picked."""

    session_id: str
    season_name: str = ""
    phase: str = ""
    day: int = 0
    source: str = ""
    reason: str = ""

    @property
    def finished(self) -> bool:
        return self.phase == "season_complete"

    @property
    def is_eval(self) -> bool:
        return self.season_name.startswith("eval:")


@dataclass
class Plan:
    """What a prune would do, before it does any of it."""

    take: list = field(default_factory=list)
    keep: list = field(default_factory=list)
    card_dirs: list = field(default_factory=list)
    bytes_on_disk: int = 0
    via: str = ""

    def by_source(self) -> dict:
        out: dict[str, int] = {}
        for c in self.take:
            out[c.source or "?"] = out.get(c.source or "?", 0) + 1
        return out


# ── talking to a live server ───────────────────────────────────────


def server_at(base: str, timeout: float = 5.0) -> Optional[dict]:
    """Is one of *our* servers answering here? Returns its whoami row.

    Checks the identity route rather than just the port, for the same
    reason ``server/portguard.py`` does: something else on 8000 is not
    a Sea of Colours server, and issuing deletes at it would be a
    strange thing to do to somebody's dev stack.
    """
    try:
        with urllib.request.urlopen(f"{base}/api/meta/whoami", timeout=timeout) as r:
            row = json.loads(r.read().decode())
        return row if row.get("app") == "sea_of_colours" else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def list_via_api(base: str, timeout: float = 60.0) -> list[Candidate]:
    """Every session the server can see, across all its backends."""
    with urllib.request.urlopen(f"{base}/api/sessions", timeout=timeout) as r:
        rows = json.loads(r.read().decode())
    rows = rows.get("sessions") if isinstance(rows, dict) else rows
    return [
        Candidate(
            session_id=str(r.get("session_id") or ""),
            season_name=str(r.get("season_name") or ""),
            phase=str(r.get("phase") or ""),
            day=int(r.get("day") or 0),
            source=str(r.get("source") or ""),
        )
        for r in (rows or [])
        if r.get("session_id")
    ]


def delete_via_api(base: str, sid: str, timeout: float = 120.0) -> Optional[str]:
    """Delete one session. Returns an error string, or None on success."""
    req = urllib.request.Request(f"{base}/api/game/{sid}", method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            json.loads(r.read().decode())
        return None
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode()[:160]
        except Exception:
            pass
        return f"HTTP {exc.code} {body}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return str(exc)


# ── talking to the store directly ──────────────────────────────────


def list_local(store_dir: Path | str = DEFAULT_STORE_DIR) -> list[Candidate]:
    """Sessions on disk, read from their own meta rows.

    Used when no server is up. Reads ``meta.json`` only — the whole
    point of the file store's layout is that listing never has to load
    a 160MB frame log.
    """
    root = Path(store_dir)
    if not root.is_dir():
        return []
    out: list[Candidate] = []
    for d in sorted(root.iterdir()):
        meta = d / "meta.json"
        if not d.is_dir() or not meta.is_file():
            continue
        try:
            row = (json.loads(meta.read_text()) or {}).get("row") or {}
        except (OSError, json.JSONDecodeError):
            row = {}
        out.append(Candidate(
            session_id=d.name,
            season_name=str(row.get("season_name") or ""),
            phase=str(row.get("phase") or ""),
            day=int(row.get("day") or 0),
            source="local",
        ))
    return out


def dir_bytes(path: Path) -> int:
    try:
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    except OSError:
        return 0


# ── choosing what goes ─────────────────────────────────────────────


def league_sessions(root: Path | str = "reports/league/matches") -> set[str]:
    """Session ids the league's own match files point at.

    These are the replays behind every link on the league page, so
    they are not junk however old they look — a prune that took them
    would leave a published bracket full of dead links, and there is no
    getting them back. Read from the match files rather than from a
    naming convention, because the convention would drift.
    """
    out: set[str] = set()
    d = Path(root)
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.json")):
        try:
            row = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for g in row.get("games") or []:
            sid = str((g or {}).get("session_id") or "")
            if sid:
                out.add(sid)
    return out


def make_plan(
    rows: Sequence[Candidate],
    *,
    local_all: bool = False,
    local_resumable: bool = False,
    remote_resumable: bool = False,
    remote_all: bool = False,
    cards: bool = False,
    store_dir: Path | str = DEFAULT_STORE_DIR,
    protect: Optional[Iterable[str]] = None,
    via: str = "",
) -> Plan:
    """Decide, without doing anything. Every skip carries its reason."""
    plan = Plan(via=via)
    root = Path(store_dir)
    spared = set(protect or ())

    for c in rows:
        local = c.source in ("local", "file", "")
        if c.is_eval:
            c.reason = "eval session — protected"
            plan.keep.append(c)
            continue
        if c.session_id in spared:
            c.reason = "a league replay — protected"
            plan.keep.append(c)
            continue

        want = False
        if local and local_all:
            want, c.reason = True, "local"
        elif local and local_resumable and not c.finished:
            want, c.reason = True, "local, resumable"
        elif not local and remote_all:
            want, c.reason = True, f"{c.source}"
        elif not local and remote_resumable and not c.finished:
            want, c.reason = True, f"{c.source}, resumable"

        if want:
            plan.take.append(c)
            if local:
                plan.bytes_on_disk += dir_bytes(root / c.session_id)
        else:
            c.reason = "not in scope"
            plan.keep.append(c)

    if cards and CARD_DIR.is_dir():
        plan.card_dirs = sorted(d for d in CARD_DIR.iterdir() if d.is_dir())
        plan.bytes_on_disk += sum(dir_bytes(d) for d in plan.card_dirs)

    return plan


# ── doing it ───────────────────────────────────────────────────────


def apply(
    plan: Plan,
    *,
    base: Optional[str] = None,
    store_dir: Path | str = DEFAULT_STORE_DIR,
    on_step: Optional[Callable[[int, int, Candidate, Optional[str]], None]] = None,
) -> list[tuple[Candidate, str]]:
    """Carry out a plan. Returns whatever failed, with its error.

    Sequential on purpose. Deleting a Snowflake session touches
    fourteen tables, and firing those concurrently at one warehouse
    buys very little and risks lock contention on the shared session
    table — this runs once, unattended, and being slow is fine.
    """
    failures: list[tuple[Candidate, str]] = []
    root = Path(store_dir)
    total = len(plan.take)

    for i, c in enumerate(plan.take, 1):
        err: Optional[str] = None
        if base:
            err = delete_via_api(base, c.session_id)
        else:
            d = root / c.session_id
            if d.is_dir():
                try:
                    shutil.rmtree(d)
                except OSError as exc:
                    err = str(exc)
            else:
                err = "no local directory and no server to ask"
        if err:
            failures.append((c, err))
        if on_step is not None:
            on_step(i, total, c, err)

    for d in plan.card_dirs:
        shutil.rmtree(d, ignore_errors=True)

    return failures


def human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024.0
    return f"{n:.1f}TB"
