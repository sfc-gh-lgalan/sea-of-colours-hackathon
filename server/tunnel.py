"""Manage a single public *quick tunnel* from inside the server.

The Multiplayer button wants a public URL with one click instead of the
player dropping to a terminal. This module owns exactly one ephemeral
tunnel subprocess and exposes start / status / stop for the API layer.

v1.16 — this used to hard-code cloudflared, which broke on locked-down
corporate networks in a way that looked like our bug. Two independent
policies bite, and they bite different providers:

* **DNS blocklists.** A corporate resolver can return NXDOMAIN for a
  tunnel hostname while the same name resolves fine on 1.1.1.1. The
  provider's *transport* is untouched (a request with DNS bypassed
  returns 200), so the tunnel is perfectly healthy and simply unnameable.
  Nothing in our code can fix that: the guest's browser has to resolve
  the host. All we can do is notice and use a different provider.
* **Egress filtering.** The same network allows outbound 443 but drops
  SSH to some hosts and the high ports localtunnel and bore rely on.

So no single provider is safe. We keep an ordered list and use the first
one that actually publishes a *reachable* URL, which turns two partial
answers into one that covers nearly every network — the failure modes are
complementary, since a network that blocks SSH egress generally permits
Cloudflare and vice versa.

v1.17 — the order flipped, and a gate was added. Two measurements drove
it, both on a Snowflake laptop with the VPN up:

1. **Anonymous localhost.run hostnames rotate, fast.** A tunnel published
   ``cf80a4d4c4839b.lhr.life``, served 200 for thirteen minutes, then
   moved to a new name — and the old one returned 503. Every invite link
   and QR handed out before that point was dead, and the *host* was worst
   off, because the Multiplayer flow parks their browser on the tunnel
   origin. Their docs claim "a few hours"; we measured thirteen minutes.
   The documented fix is a **registered** SSH key rather than ``nokey``,
   but an unregistered key is refused outright, so a stable name needs an
   account per person — the exact setup cost this transport exists to
   avoid. Cloudflare quick tunnels keep their hostname for the life of
   the process, so they are the better default *when reachable*.
2. **Cloudflare names sometimes would not resolve here at all**, while
   the same name resolved on 1.1.1.1 and served 200 with DNS bypassed.
   That was read as a corporate filter on newly-observed subdomains and
   motivated ``_hostname_resolves``: accept a provider only once its
   hostname resolves through the same OS resolver the host's browser will
   use, else fall through. **The reading was wrong — see v1.18.** The gate
   is still the right idea; its timing was not.

v1.18 — there is no DNS block. *We* were the DNS block, and the gate was
the thing causing the failure it was written to detect.

Publishing a URL and the DNS record existing are two separate events.
Measured on cloudflared over three runs: the URL is printed at 5.4–5.8s
and the name first resolves anywhere at 8.0–9.3s, a window of **2.4–3.5s**.
The old gate started querying the instant the URL appeared and retried
every 0.8s, so its first lookups always landed *before* the record
existed. A resolver that is asked for a name that does not exist caches
that answer, and ``trycloudflare.com`` publishes a negative TTL of
**1800 seconds**. One premature lookup therefore made the hostname dead
on this machine for half an hour — on the *host's* resolver, the one
whose answer decides whether the invite links work at all.

The A/B that settled it: two quick tunnels started seconds apart, one
queried eight times at birth and one left alone. After 75s the untouched
name resolved locally; the hammered one was still NXDOMAIN locally while
1.1.1.1 happily returned an address for it. Same laptop, same VPN, same
resolver, opposite outcomes — the only variable was our own impatience.

So the gate now **waits for the record to plausibly exist before it opens
its mouth** (see :data:`_DNS_GRACE_S`), and the liveness watchdog is held
back behind the same gate, because its probe goes through the OS resolver
too and would otherwise poison the name the gate is about to test.

v1.46 — the fallback was silent, and that cost a live game. A three-way
multiplayer session ran the whole way on localhost.run *with cloudflared
installed*, rotated its hostname twice, and finally died — and nobody
could have known they had been downgraded, because the evidence was
thrown away twice over. :meth:`_TunnelManager.start` built a ``failures``
list and dropped it on the floor the moment a later provider won, and
:meth:`_scrape_url` drained each provider's output hunting for a URL and
discarded every other line. A successful ``start()`` therefore looked
identical whichever provider answered, so "you are on the stable one" and
"we tried the stable one, it failed for this reason, you are on the one
that rotates" were indistinguishable from the outside.

Falling through is a *positive failure* of everything above it, never a
race — the providers are tried strictly in order and only one process is
ever alive — so the reason always exists at the moment it is discarded.
Keeping it is what turns "the links keep dying" into "cloudflare could
not publish in 20s, here is what it said". Hence :data:`_TAIL_LINES` and
the ``skipped`` key on :func:`status`.

Three more things came out of that post-mortem, all of them about the
cost of *losing once*.

* **The preferred provider now gets more than one attempt**
  (:data:`_ATTEMPTS_PREFERRED`). Its failure modes are dominated by
  timing — a URL that arrives a second after we stopped waiting, a DNS
  record that is not up yet — and those are coin flips, not verdicts.
  Losing one of them used to buy a whole session on a provider that
  rotates its hostname every few minutes. Retrying costs seconds once;
  not retrying cost a three-player game twice in two hours. The *last*
  provider gets a single attempt, because there is nothing behind it to
  protect and a second try only delays the honest "no tunnel".
* **The DNS gate is now charged to the provider that runs it.** It used
  to execute after the per-provider polling loop and so fell outside
  ``wait_s`` entirely, while still burning the shared deadline: a first
  provider that published slowly and then failed the gate could spend
  ~48s of a 60s budget and hand the fallback 12s against the 25 it needs
  for an SSH negotiation. That is "no tunnel at all" caused purely by
  accounting. Each provider is now costed at publish + gate
  (:func:`_provider_cost`) and every attempt reserves what the providers
  behind it still need.
* **A dead tunnel comes back** (:data:`_MAX_REVIVALS`). The watchdog
  used to notice the subprocess had exited and simply retire. Nothing
  restarted it, so a game went off the air permanently and silently at
  whatever hour its provider gave up. It now relaunches, bounded, and
  says so in ``status``. The new URL is a new URL — there is no way
  around that on an anonymous tunnel — but a fresh link beats no server.

Notes:

* Quick tunnels need no account; the public URL is parsed from the
  provider's own log output and changes every run.
* If no provider is available or all fail, :func:`start` returns an
  ``error`` the UI surfaces instead of crashing, and the caller falls
  back to a local game.
"""

from __future__ import annotations

import atexit
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional, Pattern


@dataclass(frozen=True)
class Provider:
    """One way of getting a public URL for a local port.

    ``argv`` builds the command; ``url_re`` finds the public URL in
    whatever the process prints. ``binary`` is what must exist on PATH for
    the provider to be usable at all.
    """

    name: str
    binary: str
    argv: Callable[[int], List[str]]
    url_re: Pattern[str]
    install_hint: str
    # Per-provider patience. localhost.run negotiates an SSH session and
    # takes noticeably longer than cloudflared to print its URL, so give it
    # more room before falling through.
    wait_s: float = 20.0
    # Does this provider change the hostname out from under a live session?
    # Anonymous localhost.run does (measured: 13 minutes), which kills every
    # invite link already handed out, so the UI has to warn about it.
    rotates: bool = False

    def available(self) -> bool:
        return shutil.which(self.binary) is not None


def _lhr_argv(port: int) -> List[str]:
    return [
        "ssh",
        "-T",
        "-n",
        # accept-new (rather than "no") still pins the host key after the
        # first connection, so we get unattended startup without silently
        # re-trusting a changed key on every later run.
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        # Without this ssh happily stays up after a failed forward, and we
        # would sit out the whole timeout waiting for a URL that can never
        # arrive instead of failing over to the next provider.
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ConnectTimeout=15",
        "-R", f"80:localhost:{int(port)}",
        "nokey@localhost.run",
    ]


def _cloudflared_argv(port: int) -> List[str]:
    return [
        "cloudflared", "tunnel",
        "--url", f"http://localhost:{int(port)}",
        "--no-autoupdate",
    ]


# v1.17 — cloudflare leads because its hostname survives the whole
# session; localhost.run's anonymous names rotate in minutes and take
# every shared invite link with them. localhost.run stays as the fallback
# precisely because it needs no install and no account, so a laptop
# without cloudflared, or on a network that drops it, can still host.
# Whether cloudflare is *reachable* is decided at runtime by the DNS gate
# in ``_try_provider``, never by assumption.
PROVIDERS: List[Provider] = [
    Provider(
        name="cloudflare",
        binary="cloudflared",
        argv=_cloudflared_argv,
        url_re=re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE),
        install_hint="brew install cloudflared",
        wait_s=20.0,
    ),
    Provider(
        name="localhost.run",
        binary="ssh",
        argv=_lhr_argv,
        url_re=re.compile(r"https://[a-z0-9-]+\.lhr\.life", re.IGNORECASE),
        install_hint="ssh is preinstalled on macOS, Linux and Windows 10+",
        wait_s=25.0,
        rotates=True,
    ),
]


# How long to stay silent after a provider prints its URL, before asking
# the resolver about it. Measured gap between "URL printed" and "name
# resolvable" on cloudflared quick tunnels: 2.4–3.5s over three runs. This
# is deliberately ~3x the worst of those, because the two outcomes are
# wildly asymmetric — a few seconds of spinner against a name that a
# premature lookup renders unusable for the next 1800 seconds (the zone's
# negative-cache TTL).
_DNS_GRACE_S = 12.0
# Gap between retries once we do start asking. Long, for the same reason:
# every miss re-caches the negative answer, so an impatient retry loop
# actively extends the damage instead of catching a slow record.
_DNS_RETRY_S = 8.0

# v1.46 — how much of a provider's own output to keep for a post-mortem.
# "timed out waiting for a URL" says we did not see one; the provider
# usually said *why* on the line before, and that line is the difference
# between a diagnosis and a shrug. Bounded because this drains a live
# subprocess for the lifetime of the session.
_TAIL_LINES = 12

# v1.46 — attempts before giving up on a provider that has a fallback
# behind it. Two, not more: the failures worth retrying are timing ones
# and they clear on the second go or they are not timing failures.
_ATTEMPTS_PREFERRED = 2

# v1.46 — consecutive failed liveness probes, *after* at least one
# success, before we treat a live subprocess as a dead tunnel and restart
# it. This is the failure that actually took a game off the air: ssh sat
# there perfectly healthy while the edge served 503 for twelve minutes,
# so watching the process teaches us nothing.
#
# Three is chosen against the cost of being wrong in each direction. A
# false positive changes the hostname and kills everyone's links, so it
# must not fire on one unlucky probe — and the probe runs from the
# server's own network, which can blip independently of every guest. At
# the 30s probe interval three failures is ~90s of consistently dead
# route, by which point the links are worthless anyway and there is
# nothing left to protect by waiting.
_ROUTE_DEAD_AFTER = 3

# v1.46 — how many times a tunnel that dies on its own is relaunched
# before we stop. Bounded so a provider refusing us in a loop cannot turn
# into an unattended fork bomb against someone else's service; three is
# enough to ride out a restart-worthy blip and few enough to be obviously
# not a retry storm.
_MAX_REVIVALS = 3
# Wait between a death and the relaunch. Long enough not to hammer a
# provider that has just dropped us, short enough that a player refreshing
# the page has a reasonable chance of the tunnel being back.
_REVIVE_DELAY_S = 3.0


def _gate_cost() -> float:
    """Worst-case wall time of the DNS acceptance gate.

    Derived rather than written down, so retuning the grace or the retry
    spacing cannot silently invalidate the budget arithmetic that depends
    on it — which is precisely the bug this exists to prevent.
    """
    return _DNS_GRACE_S + 2 * _DNS_RETRY_S


def _provider_cost(prov: "Provider") -> float:
    """What one full attempt at ``prov`` can cost us, worst case.

    Publishing a URL and being *accepted* are two different phases and
    only the first was ever budgeted. Counting both is what stops an
    unlucky first provider spending the fallback's time.
    """
    return prov.wait_s + _gate_cost()


def _provider_floor(prov: "Provider") -> float:
    """The least time in which ``prov`` could still *succeed*.

    Deliberately not :func:`_provider_cost`. Reserving a later provider's
    worst case would starve the preferred one out of existence — two
    providers' worst cases exceed any budget a human will wait through —
    and the worst case is the branch where that provider fails anyway.
    What has to be protected is its chance of *working*: long enough to
    publish, plus the DNS grace and one lookup. Anything above this floor
    is a bonus the earlier providers are welcome to spend.
    """
    return prov.wait_s + _DNS_GRACE_S


def _tries_within(budget: Optional[float]) -> int:
    """How many lookups fit in ``budget`` after the grace period.

    v1.46 — the gate used to run outside any budget at all, which is how
    it could spend the fallback's time. Deriving the try count instead of
    fixing it keeps the patience where there is room for it and gives it
    up where there is not, without ever shortening the grace: being early
    is the one thing that actively causes the outage (see v1.18), so the
    grace is a floor and the retries are the adjustable part.
    """
    if budget is None:
        return 3
    spare = budget - _DNS_GRACE_S
    if spare < 0:
        return 1
    return max(1, min(3, 1 + int(spare // _DNS_RETRY_S)))


def _hostname_resolves(
    host: str,
    *,
    tries: int = 3,
    delay: float = _DNS_RETRY_S,
    grace: float = _DNS_GRACE_S,
) -> bool:
    """Can this machine's resolver look the hostname up?

    This is the gate that decides whether a provider is usable *now*. It
    deliberately uses the OS resolver, because that is what the host's
    browser will use, and a name the host cannot resolve is a name they
    cannot hand to a guest.

    v1.18 — it waits ``grace`` seconds before the *first* lookup, which is
    the whole point of the function rather than a politeness. Asking about
    a name in the window between the provider printing it and the record
    existing does not just return a useless "no", it caches that "no" for
    the zone's negative TTL and so **creates** the outage this gate exists
    to detect. Waiting is free; being early costs half an hour.

    Only a genuine name-resolution failure counts: any other socket error
    is not evidence about DNS, so we give the provider the benefit of the
    doubt rather than failing it for, say, a transient network blip.
    """
    if not host:
        return False
    if grace > 0:
        time.sleep(grace)
    for attempt in range(max(1, tries)):
        try:
            socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            return True
        except socket.gaierror:
            if attempt + 1 < tries:
                time.sleep(delay)
        except OSError:
            return True
    return False


class _TunnelManager:
    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._url: Optional[str] = None
        self._port: Optional[int] = None
        self._provider: Optional[str] = None
        self._rotates = False
        self._ready = False
        # None until the public URL has answered once. After that, False
        # means it answered and then stopped — see _watch for why the
        # distinction matters.
        self._serving: Optional[bool] = None
        self._rotations = 0
        # v1.46 — providers that were tried and failed before the one we
        # ended up on, newest last. Survives into ``status`` precisely
        # because the winning return used to discard it, which is what made
        # a downgrade to the rotating provider invisible.
        self._skipped: List[Dict[str, object]] = []
        # Rolling tail of the *current* provider's output, so a failure can
        # be explained rather than merely reported. Reset per attempt.
        self._tail: Deque[str] = deque(maxlen=_TAIL_LINES)
        # v1.46 — revival state. ``_want_port`` outlives ``_stop_locked``
        # (which clears ``_port``) because a tunnel that died still knows
        # which port it was for, and that is the one thing a relaunch
        # needs. ``_wanted`` is the intent: set when a human asked for a
        # tunnel, cleared by an explicit stop, and it is what tells the
        # difference between "it died" and "we were told to shut it down".
        self._want_port: Optional[int] = None
        self._wanted = False
        self._revivals = 0
        self._reviving = False
        self._reader: Optional[threading.Thread] = None
        self._prober: Optional[threading.Thread] = None
        # v1.18 — opened once the DNS gate accepts the provider. Until then
        # the liveness watchdog must not probe: its request resolves the
        # public hostname through the OS resolver, and a lookup before the
        # record exists poisons that resolver for the negative TTL.
        self._probe_gate: Optional[threading.Event] = None
        self._lock = threading.RLock()

    # ── introspection ────────────────────────────────────────────────
    @staticmethod
    def available_providers() -> List[Provider]:
        return [p for p in PROVIDERS if p.available()]

    @classmethod
    def installed(cls) -> bool:
        """Is *any* provider usable? The UI only needs the yes/no."""
        return bool(cls.available_providers())

    def _running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def status(self) -> Dict[str, object]:
        with self._lock:
            running = self._running()
            return {
                "installed": self.installed(),
                "providers": [p.name for p in self.available_providers()],
                "running": running,
                "provider": self._provider if running else None,
                "url": self._url if running else None,
                "port": self._port if running else None,
                # ``ready`` flips True once the server confirms the public
                # URL answers from *its own* process. This is INFORMATIONAL
                # ONLY — do not gate the Multiplayer flow on it. The probe
                # resolves the public host via the OS resolver, which a
                # corporate VPN/security agent can block even while the
                # browser (DoH / encrypted DNS) reaches the tunnel fine.
                "ready": bool(self._ready) if running else False,
                # Tri-state, unlike ``running``: True = confirmed answering,
                # False = answered before and has since stopped, None = never
                # confirmed, so we genuinely cannot tell. Only False is
                # actionable.
                "serving": self._serving if running else None,
                # Anonymous localhost.run names "change regularly" (their
                # docs), which silently invalidates every invite link already
                # handed out. Surfacing the count lets the UI say so.
                "rotations": self._rotations if running else 0,
                # v1.17 — known *in advance* from the provider, unlike
                # ``rotations`` which can only report a rotation that has
                # already broken someone's link. This is what lets the UI
                # warn while the links are still good.
                "rotates": bool(self._rotates) if running else False,
                # v1.46 — what we tried and lost before this one, so the UI
                # can say "you are on the fallback, and here is why" instead
                # of leaving a downgrade to be inferred from a hostname. An
                # empty list means the first choice won, which is the case
                # worth being able to distinguish.
                "skipped": list(self._skipped) if running else [],
                # v1.46 — times this tunnel died on its own and was
                # relaunched. Unlike ``rotations`` this survives a stop
                # only in the sense that it is reset by one: it describes
                # the *current* tunnel's history. Non-zero means every
                # link handed out before it is dead, same as a rotation,
                # so the UI has to treat it the same way.
                # Kept until an explicit stop rather than cleared when the
                # process goes: a host whose tunnel has died for good most
                # needs to know it died three times first, and zeroing
                # this on the way down deletes the post-mortem at exactly
                # the moment it becomes the whole story.
                "revivals": self._revivals if self._wanted else 0,
                # True while a relaunch is in flight, so a UI polling
                # through the gap can say "coming back" rather than
                # "gone". These are seconds apart, but they are the
                # seconds someone is staring at a broken link.
                "reviving": bool(self._reviving),
                # "There is supposed to be a tunnel right now." Together
                # with the three above this separates the states a host
                # has to tell apart and could not before: never started
                # (expected false), up, briefly down and coming back
                # (reviving), and down for good having exhausted its
                # revivals (expected, not running, not reviving).
                "expected": bool(self._wanted),
            }

    # ── lifecycle ────────────────────────────────────────────────────
    def start(
        self,
        port: int,
        *,
        wait_s: Optional[float] = None,
        _revival: bool = False,
    ) -> Dict[str, object]:
        """Start (or reuse) a public tunnel to ``http://localhost:<port>``.

        Tries each available provider in order and returns as soon as one
        publishes a URL. ``wait_s`` caps the *total* budget across
        providers; omit it to use each provider's own patience.

        ``_revival`` is set only by :meth:`_revive` and does one thing:
        keeps the revival budget. A relaunch goes through this method like
        any other start, so without it the bound would reset itself on
        every use and stop being a bound at all.
        """
        with self._lock:
            # Intent, recorded before anything can fail. A tunnel that dies
            # later needs to know it was wanted and on which port, and
            # ``_stop_locked`` deliberately does not clear either.
            self._wanted = True
            self._want_port = int(port)
            if not _revival:
                self._revivals = 0
            usable = self.available_providers()
            if not usable:
                hints = "; ".join(f"{p.name}: {p.install_hint}" for p in PROVIDERS)
                return {
                    "ok": False,
                    "installed": False,
                    "error": f"no tunnel provider available ({hints})",
                }
            # Reuse an existing tunnel for the same port.
            if self._running() and self._port == port:
                return {
                    "ok": True, "url": self._url, "running": True,
                    "reused": True, "provider": self._provider,
                    "rotates": self._rotates,
                    # Reuse must report the ORIGINAL downgrade, not an empty
                    # list: the caller is about to hand out invite links and
                    # the reason those links expire has not changed just
                    # because we skipped the provider walk this time.
                    "skipped": list(self._skipped),
                }
            # A tunnel to a different port is replaced.
            if self._running():
                self._stop_locked()

        deadline = None if wait_s is None else time.time() + wait_s
        # v1.46 — kept in two shapes on purpose: prose for the all-failed
        # error, structured for ``status``. The structured one is the whole
        # fix, because the path that used to lose it is the path where a
        # *later* provider wins and the user is silently downgraded.
        failures: List[str] = []
        skipped: List[Dict[str, object]] = []

        def _note(name: str, reason: str, tail: Optional[List[str]] = None) -> None:
            failures.append(f"{name}: {reason}")
            skipped.append({"provider": name, "error": reason, "tail": tail or []})

        for idx, prov in enumerate(usable):
            # What the providers behind this one still need. Holding it
            # back is the whole fix for the starvation case: an unlucky
            # first provider may spend its own budget and no more, so
            # "no tunnel at all" can never be an accounting artefact.
            reserve = sum(_provider_floor(p) for p in usable[idx + 1:])
            # Only a provider with something behind it is worth retrying.
            # For the last one a second attempt protects nothing and just
            # delays an honest failure.
            attempts = _ATTEMPTS_PREFERRED if idx < len(usable) - 1 else 1
            last: Dict[str, object] = {}

            for attempt in range(attempts):
                if attempt and deadline is not None:
                    # A retry spends *spare* budget, never the fallback's.
                    # So a provider that failed fast gets another go and
                    # one that failed slowly has already had it — which is
                    # the right split anyway: a fast failure is the kind
                    # that is plausibly transient, and a slow one has
                    # already demonstrated the patience it needed.
                    spare = deadline - time.time() - reserve
                    if spare < _provider_floor(prov):
                        break
                if deadline is not None and deadline - time.time() <= 0:
                    # Genuinely nothing left. Named as its own outcome
                    # because "this provider failed" and "this provider
                    # never ran" send a reader to different places.
                    last = {
                        "error": "out of time — earlier providers used the budget",
                    }
                    break
                allowance: Optional[float] = _provider_cost(prov)
                if deadline is not None:
                    # Floored at the point where this provider could still
                    # succeed. Squeezing anyone below that buys nothing —
                    # a provider given too little to work is a provider
                    # given away — so a tight budget overruns slightly
                    # rather than handing out attempts designed to fail.
                    allowance = max(
                        _provider_floor(prov),
                        min(allowance, deadline - time.time() - reserve),
                    )
                publish_budget = min(prov.wait_s, allowance)
                last = self._try_provider(
                    prov, port, publish_budget, allowance=allowance,
                )
                if last.get("ok"):
                    with self._lock:
                        self._skipped = skipped
                    # Hand the same list back to the caller, so the page
                    # that asked for the tunnel can warn immediately
                    # instead of waiting to poll for it.
                    return {**last, "skipped": skipped}
                if attempt + 1 < attempts:
                    # Say which attempt, or the tail below reads as one
                    # inexplicable failure rather than a retried one.
                    failures.append(
                        f"{prov.name} (try {attempt + 1}): "
                        f"{last.get('error', 'failed')}"
                    )

            _note(
                prov.name,
                str(last.get("error", "failed")),
                last.get("tail") if isinstance(last.get("tail"), list) else None,
            )

        return {
            "ok": False,
            "installed": True,
            "error": "no tunnel could be established (" + "; ".join(failures) + ")",
            "skipped": skipped,
        }

    def _try_provider(
        self, prov: Provider, port: int, wait_s: float,
        allowance: Optional[float] = None,
    ) -> Dict[str, object]:
        """Spawn one provider and see it all the way to accepted.

        ``wait_s`` budgets the *publishing* phase. ``allowance``, when
        given, budgets the whole attempt including the DNS gate, so that
        whatever this provider does not spend is still there for the next
        one. Omit it and the gate falls back to its own patience, which is
        what the tests and any direct caller want.
        """
        started = time.time()
        with self._lock:
            self._url = None
            self._ready = False
            self._serving = None
            self._rotations = 0
            self._tail.clear()
            self._port = int(port)
            self._provider = prov.name
            self._rotates = prov.rotates
            # Created before the reader thread starts, so the watchdog it
            # spawns can never find this unset and probe unguarded.
            gate = self._probe_gate = threading.Event()
            try:
                self._proc = subprocess.Popen(
                    prov.argv(port),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except OSError as exc:
                self._proc = None
                self._provider = None
                self._rotates = False
                return {"ok": False, "error": f"failed to launch: {exc}"}

            proc = self._proc
            self._reader = threading.Thread(
                target=self._scrape_url, args=(proc, prov), daemon=True,
            )
            self._reader.start()

        # Poll (outside the lock) for the URL to show up.
        end = time.time() + max(wait_s, 0.0)
        published: Optional[str] = None
        while time.time() < end:
            with self._lock:
                if self._url and self._proc is proc:
                    published = self._url
                elif self._proc is proc and not self._running():
                    tail = self._recent_output()
                    self._stop_locked()
                    return {
                        "ok": False,
                        "error": "exited before publishing a URL",
                        "tail": tail,
                    }
            if published:
                break
            time.sleep(0.25)

        if published:
            # v1.17 — publishing a URL is not the same as being usable. If
            # this machine can't resolve the hostname, neither can the
            # host's browser, so the invite links would all be dead on
            # arrival. Reject and let the next provider try, rather than
            # handing back a URL that looks fine in the response body.
            host = urllib.parse.urlsplit(published).hostname or ""
            gate_budget = (
                None if allowance is None
                else max(0.0, allowance - (time.time() - started))
            )
            if _hostname_resolves(host, tries=_tries_within(gate_budget)):
                # Name is known good, so the watchdog can start probing it
                # without risk of caching a miss.
                gate.set()
                return {
                    "ok": True, "url": published,
                    "running": True, "provider": prov.name,
                    "rotates": prov.rotates,
                }
            with self._lock:
                tail = self._recent_output()
                if self._proc is proc:
                    self._stop_locked()
            return {
                "ok": False,
                "error": (
                    f"published {published} but this machine cannot resolve "
                    f"{host} — DNS here is blocking it, so guests could not "
                    "reach it either"
                ),
                "tail": tail,
            }

        # Timed out: tear this one down so the next provider starts clean
        # rather than leaving an orphan holding the port forwarding.
        with self._lock:
            tail = self._recent_output()
            if self._proc is proc:
                self._stop_locked()
        return {
            "ok": False,
            "error": f"timed out waiting for a URL after {wait_s:.0f}s",
            "tail": tail,
        }

    def _revive(self, dead: subprocess.Popen, reason: str = "it died") -> None:
        """Relaunch a tunnel that died on its own.

        ``dead`` may still be *running*: a tunnel whose edge has stopped
        routing is exactly as useless as one whose process exited, and
        that is the shape the outage we are fixing actually took. The
        teardown below terminates it either way.

        Only ever called from the watchdog, and only for the process it
        was supervising. Three conditions have to hold, and each rules out
        a case where coming back would be wrong:

        * ``_wanted`` — a human asked for this tunnel and has not asked
          for it to stop. An explicit :meth:`stop` clears it, so shutting
          a tunnel down cannot be undone by its own supervisor.
        * ``self._proc is dead`` — we are reviving the current tunnel, not
          racing a start that has already replaced it.
        * a revival budget remains. A provider refusing us in a loop must
          not become an unattended retry storm against their service.

        The new tunnel gets a new hostname, which kills every link already
        handed out. That is not a reason to stay down: a fresh link beats
        a dead server, and ``revivals`` in :meth:`status` tells the UI to
        warn exactly as it does for a rotation.
        """
        with self._lock:
            if self._proc is not dead or not self._wanted:
                return
            port = self._want_port
            if port is None or self._revivals >= _MAX_REVIVALS:
                return
            self._revivals += 1
            self._reviving = True
            # Clear the corpse now so ``status`` stops advertising a URL
            # that certainly does not answer, rather than after the delay.
            self._stop_locked()

        def _relaunch() -> None:
            try:
                time.sleep(_REVIVE_DELAY_S)
                # Re-check under the lock: a human may have pressed stop,
                # or started their own tunnel, during the delay.
                with self._lock:
                    if not self._wanted or self._running():
                        return
                res = self.start(int(port), _revival=True)
                # The console is the *only* channel that still reaches the
                # host. Everyone's browser — theirs included, if they were
                # playing through the tunnel — is stranded on a hostname
                # that no longer exists and cannot be told the new one by
                # the server it can no longer reach. Printing it here is
                # what makes the revival actionable rather than merely
                # true. Stated loudly because the operator is not watching.
                if res.get("ok"):
                    print(
                        f"\n[soc] tunnel restarted — {reason} "
                        f"({self._revivals}/{_MAX_REVIVALS}). "
                        f"NEW ADDRESS — old invite links are dead:\n"
                        f"[soc]   {res.get('url')}\n"
                        f"[soc] send that to your players; they keep their "
                        f"seats and anything already submitted.\n",
                        flush=True,
                    )
                else:
                    print(
                        f"\n[soc] tunnel is down — {reason} — and could not "
                        f"be restarted: {res.get('error')}\n"
                        f"[soc] press MULTIPLAYER in the UI to try again.\n",
                        flush=True,
                    )
            except Exception:
                # A supervisor thread that raises takes nothing useful with
                # it and loses the flag below, so swallow and stand down.
                pass
            finally:
                with self._lock:
                    self._reviving = False

        threading.Thread(target=_relaunch, daemon=True).start()

    def _recent_output(self) -> List[str]:
        """Snapshot the current provider's last few lines.

        Taken *before* ``_stop_locked`` so the tail is not cleared out from
        under the caller, and copied rather than shared because the reader
        thread keeps appending to the deque until the process actually dies.
        """
        return [ln for ln in list(self._tail) if ln]

    def _scrape_url(self, proc: subprocess.Popen, prov: Provider) -> None:
        """Read provider output for its public URL, including changes.

        The URL is not write-once. localhost.run rotates anonymous
        hostnames while the session stays up, and if it announces the new
        one we want to be holding it rather than a name that now 404s.
        """
        if proc.stdout is None:
            return
        started_watch = False
        for line in proc.stdout:
            # v1.46 — keep a bounded tail. This loop has always had to run to
            # stop the pipe buffer blocking the child; throwing the lines away
            # while doing so is what left a failed provider unexplainable.
            text = line.strip()
            if text:
                with self._lock:
                    if self._proc is proc:
                        self._tail.append(text)
            m = prov.url_re.search(line)
            if m:
                with self._lock:
                    if self._proc is proc and m.group(0) != self._url:
                        if self._url is not None:
                            self._rotations += 1
                            self._serving = None
                        self._url = m.group(0)
                        if not started_watch:
                            started_watch = True
                            self._prober = threading.Thread(
                                target=self._watch,
                                args=(proc, self._probe_gate),
                                daemon=True,
                            )
                            self._prober.start()
            # Keep draining so the pipe buffer never blocks the child.

    def _watch(
        self, proc: subprocess.Popen, gate: Optional[threading.Event] = None,
    ) -> None:
        """Keep checking that the public URL still answers.

        A live subprocess is not proof of a live tunnel: localhost.run has
        been observed serving 503 for twelve minutes while ssh sat there
        perfectly happy, because the free tier rotates names out from
        under you. Without this, the UI would keep advertising an invite
        link that stopped working, which is worse than failing loudly.

        The probe goes through ``urllib`` → the OS resolver, which a
        corporate VPN can block for tunnel domains even when browsers get
        through. So a failure is only meaningful **after** a success:
        before that we report ``None`` (can't tell) rather than accusing a
        perfectly good tunnel of being dead.

        v1.18 — that same OS resolver dependency is why this waits for
        ``gate``. Probing the moment the URL is scraped would look up a
        name that does not exist yet and cache the miss for half an hour,
        breaking the tunnel we are here to supervise. The DNS gate opens
        this once the record is known to exist.
        """
        while gate is not None and not gate.wait(0.5):
            with self._lock:
                if self._proc is not proc:
                    return
            if proc.poll() is not None:
                # Deliberately *not* a revival: before the gate opens we
                # are still inside the start path, which is already
                # handling this failure and moving to the next provider.
                # Reviving here would race it into a second tunnel.
                return

        # Consecutive probe failures since the last success. Only counted
        # once the tunnel has answered at least once — see below.
        dead_probes = 0

        while True:
            with self._lock:
                if self._proc is not proc:
                    return
                url = self._url
            if proc.poll() is not None:
                # v1.46 — this used to be a bare return, and that return is
                # how a game went off the air for good: the supervisor
                # noticed the tunnel had died and quietly retired with it.
                self._revive(proc, "the tunnel client exited")
                return
            if not url:
                if self._settle(proc, 2.0):
                    self._revive(proc, "the tunnel client exited")
                    return
                continue

            ok = self._probe_once(url)
            with self._lock:
                if self._proc is not proc:
                    return
                if ok:
                    self._ready = True
                    self._serving = True
                    dead_probes = 0
                elif self._ready:
                    # Answered before, doesn't now — a real regression.
                    self._serving = False
                    dead_probes += 1
                # else: never answered, so silence rather than a guess.
                # Deliberately not counted: without a prior success we
                # cannot tell a dead route from a probe path that never
                # worked, and restarting on the latter would be a loop.
                route_dead = dead_probes >= _ROUTE_DEAD_AFTER
            if route_dead:
                # v1.46 — a live process is not a live tunnel, and this is
                # the shape the outage actually took. Recording `serving:
                # false` and doing nothing meant the one failure we had
                # genuinely observed was the one we did not act on.
                self._revive(
                    proc,
                    f"the tunnel stopped routing "
                    f"({dead_probes} failed checks in a row)",
                )
                return
            if self._settle(proc, 30.0):
                self._revive(proc, "the tunnel client exited")
                return

    @staticmethod
    def _settle(proc: subprocess.Popen, seconds: float) -> bool:
        """Sleep, but wake early if ``proc`` dies. True if it did.

        v1.46 — the probe interval is 30s because an HTTP round trip to
        the tunnel edge is not free, but ``poll()`` is, and conflating the
        two meant a dead tunnel went unnoticed for up to half a minute
        before anything could react. Guests spend that time on a link that
        cannot work, which reads as the game having frozen.
        """
        deadline = time.time() + seconds
        while time.time() < deadline:
            if proc.poll() is not None:
                return True
            time.sleep(min(0.5, max(0.0, deadline - time.time())))
        return proc.poll() is not None

    @staticmethod
    def _probe_once(url: str) -> bool:
        """Does the public URL respond at all? Any HTTP status counts —
        we're testing the round trip, not the app."""
        target = url.rstrip("/") + "/api/meta/backend"
        try:
            req = urllib.request.Request(target, method="GET")
            with urllib.request.urlopen(req, timeout=6) as resp:
                resp.read(1)
            return True
        except urllib.error.HTTPError as exc:
            # 5xx from the tunnel edge means the tunnel is NOT routing back
            # to us — that is exactly the rotation failure we're hunting —
            # whereas a 4xx proves the round trip works.
            return exc.code < 500
        except Exception:
            return False

    def stop(self) -> Dict[str, object]:
        """Shut the tunnel down and *mean it*.

        v1.46 — the distinction from :meth:`_stop_locked` is the whole
        safety of the revival machinery. This clears the intent, so a
        watchdog that wakes to find its process gone stands down instead
        of dutifully bringing back the tunnel a human just closed.
        """
        with self._lock:
            self._wanted = False
            self._want_port = None
            self._revivals = 0
            self._stop_locked()
            return {"ok": True, "running": False}

    def _stop_locked(self) -> None:
        """Tear the current process down, leaving intent alone.

        Used both for a real shutdown and between provider attempts, which
        is why it must not touch ``_wanted`` / ``_want_port`` — a failed
        first provider is not a decision to stop wanting a tunnel.
        """
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except OSError:
                pass
        self._proc = None
        self._url = None
        self._port = None
        self._provider = None
        self._rotates = False
        self._ready = False
        self._serving = None
        self._rotations = 0
        self._skipped = []
        self._tail.clear()
        # Release any watchdog still parked on the gate; it re-checks the
        # process identity on wake and retires itself.
        if self._probe_gate is not None:
            self._probe_gate.set()
            self._probe_gate = None


_manager = _TunnelManager()


def start(port: int, *, wait_s: Optional[float] = None) -> Dict[str, object]:
    return _manager.start(port, wait_s=wait_s)


def status() -> Dict[str, object]:
    return _manager.status()


def stop() -> Dict[str, object]:
    return _manager.stop()


@atexit.register
def _cleanup() -> None:
    """Don't leave a tunnel client behind when the server exits.

    v1.46 — this stopped being merely tidy when revival landed. On Ctrl-C
    the tunnel client shares our process group and takes the signal too,
    so the watchdog sees precisely the death it now exists to repair and
    would spawn a replacement moments before the interpreter exits —
    leaving an orphan holding a tunnel to a port nothing is listening on.
    ``stop`` clears the intent, which is what makes the watchdog stand
    down rather than race us out of the door.
    """
    try:
        _manager.stop()
    except Exception:
        pass
