"""Multiplayer tunnel: provider selection, fallback and URL scraping.

Why this file exists (v1.16): the Multiplayer button used to spawn
cloudflared and nothing else, which fails silently on corporate networks
that sinkhole ``*.trycloudflare.com`` in DNS. The fix was an ordered
provider list, and the thing worth pinning is not "cloudflared runs" but
the *fallback behaviour* — that a dead first provider hands off to the
second instead of taking the whole feature down with it.

v1.17 added the DNS acceptance gate and flipped the order. Publishing a
URL turns out not to mean the URL is usable: if the host's own resolver
can't look the hostname up, no guest can reach it either, so the provider
has to be rejected in favour of the next one. And the order flipped
because anonymous localhost.run hostnames rotate within minutes, killing
every invite link already handed out, whereas a Cloudflare quick tunnel
keeps its name for the life of the process.

v1.18 corrected the *timing* of that gate, and this is the subtlest bug
in the file's history: the gate was querying the hostname in the 2.4–3.5s
window between the provider printing it and the DNS record existing, and
the resulting cached NXDOMAIN (negative TTL 1800s on
``trycloudflare.com``) made the name unresolvable on the host's own
machine for half an hour. The gate was manufacturing the exact failure it
was written to detect, which is why "cloudflare is blocked here" looked
reproducible. So there are now tests that nothing — gate or watchdog —
touches DNS before the record can exist.

These tests never touch the network. Fake providers are real subprocesses
(so the scrape/timeout/exit paths are genuinely exercised) that just print
a line or sleep, and the DNS gate is stubbed except where it is the thing
under test.
"""

from __future__ import annotations

import pathlib
import re
import sys
import time
import urllib.error
import urllib.request
from unittest import mock

import pytest

from server import tunnel as soc_tunnel

# Captured before the autouse stub below replaces it, so the gate's own
# tests can exercise the real implementation.
_REAL_RESOLVES = soc_tunnel._hostname_resolves

# The shutdown test starts a *separate* interpreter, which needs the repo
# on its path the way pytest.ini puts it on ours.
_REPO = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_leftover_tunnel():
    """Every test starts and ends with nothing running."""
    soc_tunnel.stop()
    yield
    soc_tunnel.stop()


@pytest.fixture(autouse=True)
def _dns_resolves(monkeypatch):
    """Stub the acceptance gate open by default.

    Fake providers publish names like ``good.example.test`` that genuinely
    don't resolve, so without this every happy-path test would be rejected
    by the gate — and would also pay several seconds of real DNS retries
    to get there. Tests that are *about* the gate patch it themselves.
    """
    monkeypatch.setattr(soc_tunnel, "_hostname_resolves", lambda host, **kw: True)


def _fake(name: str, script: str, url_re: str, wait_s: float = 6.0,
          rotates: bool = False):
    """A provider that runs a short Python snippet instead of a tunnel."""
    return soc_tunnel.Provider(
        name=name,
        binary=sys.executable,
        argv=lambda port, _s=script: [sys.executable, "-c", _s],
        url_re=re.compile(url_re),
        install_hint=f"install {name}",
        wait_s=wait_s,
        rotates=rotates,
    )


_PUBLISHES = "print('tunnelled at https://good.example.test ok', flush=True)\nimport time; time.sleep(30)"
_PUBLISHES2 = "print('tunnelled at https://other.example.test ok', flush=True)\nimport time; time.sleep(30)"
_EXITS = "import sys; sys.exit(3)"
_SILENT = "import time; time.sleep(30)"


# ── ordering and availability ────────────────────────────────────────

def test_cloudflare_is_tried_before_localhost_run():
    """v1.17 — the order is decided by *hostname stability*, not by install
    cost. A Cloudflare quick tunnel keeps its name for the life of the
    process; an anonymous localhost.run name rotated after 13 minutes in
    testing and took every invite link with it. Reachability is settled at
    runtime by the DNS gate, so it can't justify the ordering."""
    names = [p.name for p in soc_tunnel.PROVIDERS]
    assert names.index("cloudflare") < names.index("localhost.run")


def test_the_fallback_provider_needs_no_install():
    """Whatever leads, the *last* resort has to work on a bare machine, or
    a laptop without cloudflared can't host at all."""
    assert soc_tunnel.PROVIDERS[-1].binary == "ssh"


def test_only_the_rotating_provider_is_flagged_as_such():
    """The flag is what lets the UI warn while links are still good,
    rather than after a rotation has already broken them."""
    by_name = {p.name: p for p in soc_tunnel.PROVIDERS}
    assert by_name["localhost.run"].rotates is True
    assert by_name["cloudflare"].rotates is False


def test_installed_is_true_when_any_provider_exists(monkeypatch):
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [_fake("f", _SILENT, "x")])
    assert soc_tunnel.status()["installed"] is True


def test_installed_is_false_when_no_provider_exists(monkeypatch):
    gone = soc_tunnel.Provider(
        name="nope", binary="soc-not-a-real-binary",
        argv=lambda port: ["soc-not-a-real-binary"],
        url_re=re.compile("x"), install_hint="get it",
    )
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [gone])
    assert soc_tunnel.status()["installed"] is False


# ── starting ─────────────────────────────────────────────────────────

def test_a_working_provider_returns_its_url(monkeypatch):
    monkeypatch.setattr(
        soc_tunnel, "PROVIDERS",
        [_fake("good", _PUBLISHES, r"https://good\.example\.test")],
    )
    res = soc_tunnel.start(8000, wait_s=10)
    assert res["ok"] is True
    assert res["url"] == "https://good.example.test"
    assert res["provider"] == "good"


def test_a_dead_first_provider_falls_through_to_the_second(monkeypatch):
    """The regression this whole change exists to prevent."""
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("dead", _EXITS, r"https://never\.example\.test"),
        _fake("good", _PUBLISHES, r"https://good\.example\.test"),
    ])
    res = soc_tunnel.start(8000, wait_s=20)
    assert res["ok"] is True
    assert res["provider"] == "good"


def test_a_silent_first_provider_times_out_and_hands_over(monkeypatch):
    """A provider that connects but never publishes must not hold the
    whole feature hostage until the browser gives up."""
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("mute", _SILENT, r"https://never\.example\.test", wait_s=2.0),
        _fake("good", _PUBLISHES, r"https://good\.example\.test"),
    ])
    res = soc_tunnel.start(8000, wait_s=20)
    assert res["ok"] is True
    assert res["provider"] == "good"


def test_every_provider_failing_reports_each_reason(monkeypatch):
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("one", _EXITS, "x"),
        _fake("two", _EXITS, "x"),
    ])
    res = soc_tunnel.start(8000, wait_s=15)
    assert res["ok"] is False
    assert "one" in res["error"] and "two" in res["error"]


def test_no_provider_at_all_names_how_to_get_one(monkeypatch):
    gone = soc_tunnel.Provider(
        name="nope", binary="soc-not-a-real-binary",
        argv=lambda port: ["soc-not-a-real-binary"],
        url_re=re.compile("x"), install_hint="brew install nope",
    )
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [gone])
    res = soc_tunnel.start(8000, wait_s=5)
    assert res["ok"] is False
    assert res["installed"] is False
    assert "brew install nope" in res["error"]


def test_a_second_start_on_the_same_port_reuses_the_tunnel(monkeypatch):
    monkeypatch.setattr(
        soc_tunnel, "PROVIDERS",
        [_fake("good", _PUBLISHES, r"https://good\.example\.test")],
    )
    first = soc_tunnel.start(8000, wait_s=10)
    second = soc_tunnel.start(8000, wait_s=10)
    assert second.get("reused") is True
    assert second["url"] == first["url"]


# ── the DNS acceptance gate (v1.17) ──────────────────────────────────
#
# Publishing a URL is not the same as being usable. If the host's resolver
# can't look the hostname up, the host's browser can't either, so every
# invite link would be dead on arrival — and the tunnel process would sit
# there looking perfectly healthy the whole time.

def test_an_unresolvable_hostname_is_rejected(monkeypatch):
    monkeypatch.setattr(soc_tunnel, "_hostname_resolves", lambda host, **kw: False)
    monkeypatch.setattr(
        soc_tunnel, "PROVIDERS",
        [_fake("blocked", _PUBLISHES, r"https://good\.example\.test")],
    )
    res = soc_tunnel.start(8000, wait_s=10)
    assert res["ok"] is False
    # The message has to name DNS, or the user goes hunting for a bug that
    # isn't in this repo.
    assert "resolve" in res["error"]
    assert "good.example.test" in res["error"]


def test_a_dns_blocked_provider_falls_through_to_the_next(monkeypatch):
    """The exact corporate-network case: cloudflared runs fine and prints a
    URL, but the name is sinkholed, so localhost.run has to take over."""
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("sinkholed", _PUBLISHES, r"https://good\.example\.test"),
        _fake("reachable", _PUBLISHES2, r"https://other\.example\.test"),
    ])
    monkeypatch.setattr(
        soc_tunnel, "_hostname_resolves",
        lambda host, **kw: host != "good.example.test",
    )
    res = soc_tunnel.start(8000, wait_s=20)
    assert res["ok"] is True
    assert res["provider"] == "reachable"


def test_a_rejected_provider_leaves_nothing_running(monkeypatch):
    """An orphan holding the port forward would poison the next attempt."""
    monkeypatch.setattr(soc_tunnel, "_hostname_resolves", lambda host, **kw: False)
    monkeypatch.setattr(
        soc_tunnel, "PROVIDERS",
        [_fake("blocked", _PUBLISHES, r"https://good\.example\.test")],
    )
    soc_tunnel.start(8000, wait_s=10)
    st = soc_tunnel.status()
    assert st["running"] is False
    assert st["url"] is None


def test_the_gate_retries_before_giving_up(monkeypatch):
    """A freshly minted hostname legitimately takes a moment to become
    resolvable; failing on the first miss would discard a good provider."""
    seen = {"n": 0}

    def _resolver(*_a, **_k):
        seen["n"] += 1
        if seen["n"] < 3:
            raise soc_tunnel.socket.gaierror("not yet")
        return [("fam", "type", "proto", "", ("1.2.3.4", 443))]

    monkeypatch.setattr(soc_tunnel.socket, "getaddrinfo", _resolver)
    assert _REAL_RESOLVES("x.test", tries=5, delay=0.01, grace=0) is True
    assert seen["n"] == 3


def test_the_gate_gives_up_on_a_name_that_never_resolves(monkeypatch):
    def _nxdomain(*_a, **_k):
        raise soc_tunnel.socket.gaierror("NXDOMAIN")

    monkeypatch.setattr(soc_tunnel.socket, "getaddrinfo", _nxdomain)
    assert _REAL_RESOLVES("x.test", tries=3, delay=0.01, grace=0) is False


def test_a_non_dns_socket_error_does_not_condemn_the_provider(monkeypatch):
    """Only a name-resolution failure is evidence about DNS. Anything else
    is a blip, and failing the provider for it would be a false negative."""
    def _blip(*_a, **_k):
        raise OSError("network down for a moment")

    monkeypatch.setattr(soc_tunnel.socket, "getaddrinfo", _blip)
    assert _REAL_RESOLVES("x.test", tries=2, delay=0.01, grace=0) is True


def test_an_empty_hostname_is_not_accepted():
    assert _REAL_RESOLVES("", tries=1) is False


# ── the gate must not poison the name it is testing (v1.18) ──────────
# A lookup issued before the record exists is not a harmless "no": the
# resolver caches it for the zone's negative TTL, so one early query makes
# the hostname dead on the host's own machine long after the tunnel is
# healthy. Everything below exists to keep us patient.

def test_the_gate_stays_silent_during_the_grace_period(monkeypatch):
    asked: list[float] = []

    def _record(*_a, **_k):
        asked.append(time.monotonic())
        return [("fam", "type", "proto", "", ("1.2.3.4", 443))]

    monkeypatch.setattr(soc_tunnel.socket, "getaddrinfo", _record)
    t0 = time.monotonic()
    assert _REAL_RESOLVES("x.test", grace=0.5, tries=1) is True
    assert asked, "the gate never looked the name up at all"
    assert asked[0] - t0 >= 0.5, "asked before the record could exist"


def test_the_grace_period_is_wide_enough_for_a_real_tunnel():
    """Measured lag between cloudflared printing its URL and the name
    resolving anywhere: 2.4–3.5s over three runs. Cutting it fine is a coin
    flip where losing costs 1800s of dead hostname, so keep a multiple."""
    assert soc_tunnel._DNS_GRACE_S >= 10.0


def test_retries_back_off_slowly_rather_than_hammering():
    """Each miss re-caches the negative answer, so a tight retry loop
    extends the outage rather than catching a slow record."""
    assert soc_tunnel._DNS_RETRY_S >= 5.0


def test_the_watchdog_does_not_probe_before_the_gate_opens(monkeypatch):
    """The liveness probe resolves the same hostname through the same
    resolver, so an eager watchdog poisons the name just as effectively as
    an eager gate — and it used to start the moment the URL was scraped."""
    events: list[str] = []

    def _gate(host, **_kw):
        events.append("gate:start")
        time.sleep(0.75)
        events.append("gate:open")
        return True

    monkeypatch.setattr(soc_tunnel, "_hostname_resolves", _gate)
    monkeypatch.setattr(
        soc_tunnel._TunnelManager, "_probe_once",
        staticmethod(lambda url: events.append("probe") or True),
    )
    monkeypatch.setattr(
        soc_tunnel, "PROVIDERS",
        [_fake("slow-dns", _PUBLISHES, r"https://good\.example\.test")],
    )
    assert soc_tunnel.start(8000, wait_s=10)["ok"] is True
    time.sleep(0.3)  # let a rogue watchdog incriminate itself
    assert "gate:open" in events
    before_open = events[: events.index("gate:open")]
    assert "probe" not in before_open, f"probed too early: {events}"


# ── status shape ─────────────────────────────────────────────────────

def test_status_names_the_running_provider(monkeypatch):
    monkeypatch.setattr(
        soc_tunnel, "PROVIDERS",
        [_fake("good", _PUBLISHES, r"https://good\.example\.test")],
    )
    soc_tunnel.start(8000, wait_s=10)
    st = soc_tunnel.status()
    assert st["running"] is True
    assert st["provider"] == "good"
    assert st["port"] == 8000


def test_stopping_clears_the_url(monkeypatch):
    monkeypatch.setattr(
        soc_tunnel, "PROVIDERS",
        [_fake("good", _PUBLISHES, r"https://good\.example\.test")],
    )
    soc_tunnel.start(8000, wait_s=10)
    soc_tunnel.stop()
    st = soc_tunnel.status()
    assert st["running"] is False
    assert st["url"] is None
    assert st["provider"] is None


# ── liveness watchdog ────────────────────────────────────────────────
#
# A live subprocess is not a live tunnel. Observed: localhost.run served
# 503 for twelve minutes while ssh sat there perfectly happy, because the
# free tier rotates hostnames out from under an open session.

_ROTATES = (
    "print('https://first.example.test', flush=True)\n"
    "import time; time.sleep(1)\n"
    "print('https://second.example.test', flush=True)\n"
    "time.sleep(30)"
)


def test_a_url_that_never_answered_is_not_called_broken(monkeypatch):
    """The corporate-DNS trap: our probe uses the OS resolver, which can
    be blocked for tunnel domains while browsers get through. Never
    having confirmed a URL is not evidence against it."""
    monkeypatch.setattr(
        soc_tunnel, "PROVIDERS",
        [_fake("good", _PUBLISHES, r"https://good\.example\.test")],
    )
    soc_tunnel.start(8000, wait_s=10)
    # good.example.test does not resolve, so the probe cannot succeed.
    assert soc_tunnel.status()["serving"] is None


def test_a_rotated_hostname_is_picked_up_and_counted(monkeypatch):
    monkeypatch.setattr(
        soc_tunnel, "PROVIDERS",
        [_fake("rot", _ROTATES, r"https://[a-z]+\.example\.test")],
    )
    res = soc_tunnel.start(8000, wait_s=10)
    assert res["url"] == "https://first.example.test"
    deadline = time.time() + 8
    while time.time() < deadline:
        if soc_tunnel.status()["rotations"] >= 1:
            break
        time.sleep(0.2)
    st = soc_tunnel.status()
    assert st["rotations"] == 1
    assert st["url"] == "https://second.example.test"


def test_a_5xx_from_the_edge_counts_as_not_serving():
    """503 is precisely the rotation symptom: the edge is up, but no
    longer routed to us."""
    err = urllib.error.HTTPError("u", 503, "Service Unavailable", None, None)
    with mock.patch.object(urllib.request, "urlopen", side_effect=err):
        assert soc_tunnel._TunnelManager._probe_once("https://x.test") is False


def test_a_4xx_still_proves_the_round_trip():
    err = urllib.error.HTTPError("u", 404, "Not Found", None, None)
    with mock.patch.object(urllib.request, "urlopen", side_effect=err):
        assert soc_tunnel._TunnelManager._probe_once("https://x.test") is True


def test_a_stopped_tunnel_reports_no_rotations():
    soc_tunnel.stop()
    st = soc_tunnel.status()
    assert st["serving"] is None
    assert st["rotations"] == 0


# ── the downgrade must not be silent (v1.46) ─────────────────────────
#
# A three-way game ran the whole way on localhost.run *with cloudflared
# installed*, rotated twice and died, and nobody could have known they
# were on the fallback: ``start`` built a failures list and dropped it the
# instant a later provider won, so success looked identical either way.
# Falling through is always a positive failure of everything above, never
# a race, so the reason exists at the moment it used to be discarded.

_COMPLAINS = (
    "print('ERR  couldn't connect to the edge', flush=True)\n"
    "import sys; sys.exit(3)"
)


def test_falling_through_records_which_provider_was_lost_and_why(monkeypatch):
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("dead", _EXITS, r"https://never\.example\.test"),
        _fake("good", _PUBLISHES, r"https://good\.example\.test"),
    ])
    res = soc_tunnel.start(8000, wait_s=20)
    assert res["provider"] == "good"
    skipped = res["skipped"]
    assert [s["provider"] for s in skipped] == ["dead"]
    assert skipped[0]["error"], "a skip with no reason is the bug, restated"


def test_winning_on_the_first_provider_skips_nothing(monkeypatch):
    """The case that has to stay distinguishable from a downgrade — an
    empty list is how the UI knows not to warn."""
    monkeypatch.setattr(
        soc_tunnel, "PROVIDERS",
        [_fake("good", _PUBLISHES, r"https://good\.example\.test")],
    )
    assert soc_tunnel.start(8000, wait_s=10)["skipped"] == []
    assert soc_tunnel.status()["skipped"] == []


def test_the_downgrade_is_still_there_when_the_ui_asks_later(monkeypatch):
    """The share card polls ``status`` long after ``start`` returned, so
    the reason has to outlive the call that discovered it."""
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("dead", _EXITS, r"https://never\.example\.test"),
        _fake("good", _PUBLISHES, r"https://good\.example\.test"),
    ])
    soc_tunnel.start(8000, wait_s=20)
    assert [s["provider"] for s in soc_tunnel.status()["skipped"]] == ["dead"]


def test_a_failed_provider_keeps_what_it_actually_said(monkeypatch):
    """"exited before publishing a URL" says we saw no URL. The provider
    usually said why on the line before, and that line is the difference
    between a diagnosis and a shrug."""
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("chatty", _COMPLAINS, r"https://never\.example\.test"),
        _fake("good", _PUBLISHES, r"https://good\.example\.test"),
    ])
    res = soc_tunnel.start(8000, wait_s=20)
    tail = res["skipped"][0]["tail"]
    assert any("couldn't connect to the edge" in ln for ln in tail), tail


def test_the_kept_output_cannot_grow_without_bound(monkeypatch):
    """This drains a live subprocess for the whole session, so it is a
    ring buffer or it is a leak."""
    noisy = (
        "for i in range(500): print('line %d' % i, flush=True)\n"
        "import sys; sys.exit(1)"
    )
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("noisy", noisy, r"https://never\.example\.test"),
        _fake("good", _PUBLISHES, r"https://good\.example\.test"),
    ])
    res = soc_tunnel.start(8000, wait_s=20)
    assert len(res["skipped"][0]["tail"]) <= soc_tunnel._TAIL_LINES


def test_a_starved_provider_is_not_blamed_for_failing(monkeypatch):
    """Running out of shared budget means this one was never tried. Filing
    it as its own failure sends the reader after the wrong provider."""
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("slow", _SILENT, r"https://never\.example\.test", wait_s=2.0),
        _fake("starved", _PUBLISHES, r"https://good\.example\.test"),
    ])
    res = soc_tunnel.start(8000, wait_s=2.0)
    assert res["ok"] is False
    starved = [s for s in res["skipped"] if s["provider"] == "starved"]
    assert starved and "out of time" in starved[0]["error"]


def test_reusing_a_tunnel_still_reports_the_original_downgrade(monkeypatch):
    """Reuse is the common path — the share card asks again every time
    someone opens it — and the links expire for the same reason they
    always did. Reporting a clean slate here would hide the warning from
    exactly the screen that hands out the links."""
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("dead", _EXITS, r"https://never\.example\.test"),
        _fake("good", _PUBLISHES, r"https://good\.example\.test", rotates=True),
    ])
    soc_tunnel.start(8000, wait_s=20)
    again = soc_tunnel.start(8000, wait_s=20)
    assert again["reused"] is True
    assert [s["provider"] for s in again["skipped"]] == ["dead"]
    assert again["rotates"] is True


def test_stopping_forgets_the_downgrade(monkeypatch):
    """Stale advice about a tunnel that no longer exists is worse than
    none: the next start may well pick a different provider."""
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("dead", _EXITS, r"https://never\.example\.test"),
        _fake("good", _PUBLISHES, r"https://good\.example\.test"),
    ])
    soc_tunnel.start(8000, wait_s=20)
    soc_tunnel.stop()
    assert soc_tunnel.status()["skipped"] == []


# ── budget arithmetic (v1.46) ────────────────────────────────────────
#
# The bug these pin is an accounting one, and it is nastier than it
# sounds because its symptom is "no tunnel at all" on a machine where
# both providers work. The DNS gate used to run *after* the per-provider
# polling loop and so outside ``wait_s``, while still spending the shared
# deadline: an unlucky first provider could publish slowly, fail the gate
# and walk off with 48s of a 60s budget, handing the fallback 12s against
# the 25 it needs just to negotiate SSH.

def test_the_gate_is_charged_to_the_provider_that_runs_it():
    """A provider's cost is publish *and* accept. Budgeting only the first
    is what let the gate spend someone else's time."""
    prov = _fake("p", _SILENT, "x", wait_s=20.0)
    assert soc_tunnel._provider_cost(prov) == 20.0 + soc_tunnel._gate_cost()


def test_the_gate_cost_is_derived_from_its_own_dials():
    """Written down as a literal it would rot the moment someone retuned
    the grace, and the budget arithmetic downstream would quietly stop
    being true — which is the original bug, one level up."""
    assert soc_tunnel._gate_cost() == (
        soc_tunnel._DNS_GRACE_S + 2 * soc_tunnel._DNS_RETRY_S
    )


def test_the_gate_gives_up_retries_before_it_gives_up_grace():
    """Being *early* is the failure that poisons the resolver for half an
    hour (v1.18), so a squeezed gate must shed retries and never shorten
    the grace. One lookup after the full grace is the floor."""
    assert soc_tunnel._tries_within(0) == 1
    assert soc_tunnel._tries_within(soc_tunnel._DNS_GRACE_S) == 1
    assert soc_tunnel._tries_within(soc_tunnel._DNS_GRACE_S
                                    + soc_tunnel._DNS_RETRY_S) == 2
    assert soc_tunnel._tries_within(None) == 3


def test_the_shipped_budget_lets_both_real_providers_publish():
    """The regression in numbers. At the old 60s, honest accounting
    squeezed *cloudflare* — the provider whose hostname survives the
    session — below its own publish window, so the room would drift onto
    the rotating fallback by arithmetic rather than by circumstance."""
    cf, lhr = soc_tunnel.PROVIDERS
    budget = 75.0
    allowance = max(
        soc_tunnel._provider_floor(cf),
        min(soc_tunnel._provider_cost(cf), budget - soc_tunnel._provider_floor(lhr)),
    )
    assert min(cf.wait_s, allowance) == cf.wait_s
    # ...and the fallback still gets a full SSH negotiation afterwards.
    assert budget - allowance >= soc_tunnel._provider_floor(lhr)


def test_a_slow_first_provider_cannot_starve_the_fallback(monkeypatch):
    """The whole point. The first provider burns its patience without
    publishing; the second must still get enough time to work."""
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("hog", _SILENT, r"https://never\.example\.test", wait_s=3.0),
        _fake("good", _PUBLISHES, r"https://good\.example\.test", wait_s=3.0),
    ])
    res = soc_tunnel.start(8000, wait_s=6.0)
    assert res["ok"] is True, res
    assert res["provider"] == "good"


def test_no_provider_is_squeezed_below_the_point_of_working(monkeypatch):
    """A tight budget overruns rather than handing out attempts designed
    to fail. Half a publish window is not a cheap try, it is a guaranteed
    loss that also costs the time it took."""
    seen: list[float] = []
    real = soc_tunnel._TunnelManager._try_provider

    def _record(self, prov, port, wait_s, allowance=None):
        seen.append(wait_s)
        return real(self, prov, port, wait_s, allowance)

    monkeypatch.setattr(soc_tunnel._TunnelManager, "_try_provider", _record)
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("good", _PUBLISHES, r"https://good\.example\.test", wait_s=4.0),
    ])
    soc_tunnel.start(8000, wait_s=1.0)
    assert seen and all(w > 0 for w in seen), seen


# ── retrying the preferred provider (v1.46) ──────────────────────────

def test_the_preferred_provider_gets_a_second_chance(monkeypatch):
    """Its failures are timing, not verdicts: a URL a second late, a DNS
    record not up yet. Losing one coin flip used to buy a whole session
    on a provider that rotates its hostname every few minutes."""
    tries = {"n": 0}
    # Fails once, then publishes — the transient miss that cost a game.
    flaky = _fake("flaky", _EXITS, r"https://good\.example\.test", wait_s=3.0)
    real = soc_tunnel._TunnelManager._try_provider

    def _flaky(self, prov, port, wait_s, allowance=None):
        if prov.name == "flaky":
            tries["n"] += 1
            if tries["n"] == 1:
                return {"ok": False, "error": "transient", "tail": []}
            return {"ok": True, "url": "https://good.example.test",
                    "running": True, "provider": "flaky", "rotates": False}
        return real(self, prov, port, wait_s, allowance)

    monkeypatch.setattr(soc_tunnel._TunnelManager, "_try_provider", _flaky)
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        flaky,
        _fake("rotator", _PUBLISHES2, r"https://other\.example\.test",
              rotates=True),
    ])
    res = soc_tunnel.start(8000, wait_s=40.0)
    assert res["provider"] == "flaky", res
    assert tries["n"] == 2
    # Won on retry, so nothing was skipped and no downgrade is reported.
    assert res["skipped"] == []


def test_the_last_provider_is_not_retried(monkeypatch):
    """There is nothing behind it to protect, so a second attempt only
    delays an honest failure — and delay is what the caller is short of."""
    tries = {"n": 0}
    real = soc_tunnel._TunnelManager._try_provider

    def _count(self, prov, port, wait_s, allowance=None):
        tries["n"] += 1
        return real(self, prov, port, wait_s, allowance)

    monkeypatch.setattr(soc_tunnel._TunnelManager, "_try_provider", _count)
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("only", _EXITS, r"https://never\.example\.test", wait_s=2.0),
    ])
    assert soc_tunnel.start(8000, wait_s=20.0)["ok"] is False
    assert tries["n"] == 1


def test_a_retry_is_skipped_when_it_would_eat_the_fallback(monkeypatch):
    """The retry is a use of spare budget, not a claim on the fallback's.
    A provider that fails *slowly* has already spent its second chance."""
    tries = {"n": 0}
    real = soc_tunnel._TunnelManager._try_provider

    def _count(self, prov, port, wait_s, allowance=None):
        if prov.name == "slowfail":
            tries["n"] += 1
        return real(self, prov, port, wait_s, allowance)

    monkeypatch.setattr(soc_tunnel._TunnelManager, "_try_provider", _count)
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("slowfail", _SILENT, r"https://never\.example\.test", wait_s=3.0),
        _fake("good", _PUBLISHES, r"https://good\.example\.test", wait_s=2.0),
    ])
    res = soc_tunnel.start(8000, wait_s=3.5)
    assert tries["n"] == 1, "retried into the fallback's budget"
    assert res["provider"] == "good", res


# ── reviving a tunnel that died on its own (v1.46) ───────────────────
#
# The failure this closes: a provider dropped the tunnel mid-game and the
# watchdog, having noticed, simply returned. Nothing restarted it, so the
# game was off the air permanently and silently.

def test_a_tunnel_that_dies_is_brought_back(monkeypatch):
    """The one that cost a session. A new hostname is not a reason to stay
    down — a fresh link beats a dead server."""
    monkeypatch.setattr(soc_tunnel, "_REVIVE_DELAY_S", 0.05)
    dies = ("print('tunnelled at https://good.example.test ok', flush=True)\n"
            "import time; time.sleep(1.0)")
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("flappy", dies, r"https://good\.example\.test", wait_s=4.0),
    ])
    assert soc_tunnel.start(8000, wait_s=20.0)["ok"] is True
    deadline = time.time() + 15
    while time.time() < deadline:
        if soc_tunnel.status().get("revivals"):
            break
        time.sleep(0.2)
    st = soc_tunnel.status()
    assert st["revivals"] >= 1, st
    assert st["running"] is True or st["reviving"] is True, st


def test_an_explicit_stop_is_not_undone_by_the_watchdog(monkeypatch):
    """The safety interlock. A supervisor that cannot tell "it died" from
    "a human closed it" would make the stop button advisory."""
    monkeypatch.setattr(soc_tunnel, "_REVIVE_DELAY_S", 0.05)
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("good", _PUBLISHES, r"https://good\.example\.test", wait_s=4.0),
    ])
    assert soc_tunnel.start(8000, wait_s=20.0)["ok"] is True
    soc_tunnel.stop()
    time.sleep(1.0)
    st = soc_tunnel.status()
    assert st["running"] is False, st
    assert st["revivals"] == 0, st


def test_revivals_are_bounded(monkeypatch):
    """A provider refusing us in a loop must not become an unattended
    retry storm against someone else's service."""
    monkeypatch.setattr(soc_tunnel, "_REVIVE_DELAY_S", 0.02)
    monkeypatch.setattr(soc_tunnel, "_MAX_REVIVALS", 2)
    dies = ("print('tunnelled at https://good.example.test ok', flush=True)\n"
            "import time; time.sleep(0.3)")
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("flappy", dies, r"https://good\.example\.test", wait_s=4.0),
    ])
    soc_tunnel.start(8000, wait_s=20.0)
    time.sleep(4.0)
    st = soc_tunnel.status()
    assert st["revivals"] == 2, st
    assert st["reviving"] is False, st
    assert st["running"] is False, "should have given up, not kept flapping"


def test_giving_up_still_says_how_hard_it_tried(monkeypatch):
    """Zeroing the count on the way down would delete the post-mortem at
    the moment it becomes the whole story. ``expected`` without
    ``running`` or ``reviving`` is the "down for good" state."""
    monkeypatch.setattr(soc_tunnel, "_REVIVE_DELAY_S", 0.02)
    monkeypatch.setattr(soc_tunnel, "_MAX_REVIVALS", 1)
    dies = ("print('tunnelled at https://good.example.test ok', flush=True)\n"
            "import time; time.sleep(0.3)")
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("flappy", dies, r"https://good\.example\.test", wait_s=4.0),
    ])
    soc_tunnel.start(8000, wait_s=20.0)
    time.sleep(3.0)
    st = soc_tunnel.status()
    assert st["expected"] is True, st
    assert st["running"] is False and st["reviving"] is False, st
    assert st["revivals"] == 1, st
    # ...and an explicit stop is the one thing that clears it.
    soc_tunnel.stop()
    assert soc_tunnel.status()["expected"] is False
    assert soc_tunnel.status()["revivals"] == 0


def test_a_revival_does_not_refill_its_own_budget(monkeypatch):
    """The trap in routing revivals through ``start``: the public entry
    point resets the counter, so without the private flag the bound would
    reset on every use and stop being a bound."""
    mgr = soc_tunnel._TunnelManager()
    mgr._revivals = 2
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("good", _PUBLISHES, r"https://good\.example\.test", wait_s=4.0),
    ])
    try:
        mgr.start(8000, wait_s=20.0, _revival=True)
        assert mgr._revivals == 2
        mgr.stop()
        mgr.start(8000, wait_s=20.0)
        assert mgr._revivals == 0, "a human asking again is a fresh budget"
    finally:
        mgr.stop()


def test_a_live_process_serving_a_dead_route_is_restarted(monkeypatch):
    """The failure that actually took a game off the air.

    localhost.run rotated its hostname out from under a session and served
    503 on the old one for twelve minutes while ``ssh`` sat there perfectly
    healthy. ``proc.poll()`` is ``None`` throughout, so watching the
    process teaches us nothing — which is why `serving: false` existed at
    all, and why recording it without acting on it left the one failure we
    had genuinely observed as the one we did not repair.
    """
    monkeypatch.setattr(soc_tunnel, "_REVIVE_DELAY_S", 0.05)
    monkeypatch.setattr(soc_tunnel, "_ROUTE_DEAD_AFTER", 2)
    # Answer once so the watchdog has a success to regress *from*, then
    # refuse for good.
    answers = iter([True, False, False, False, False, False, False])
    monkeypatch.setattr(
        soc_tunnel._TunnelManager, "_probe_once",
        staticmethod(lambda url: next(answers, False)),
    )
    # Probe back-to-back rather than waiting out the real 30s interval.
    monkeypatch.setattr(
        soc_tunnel._TunnelManager, "_settle", staticmethod(
            lambda proc, seconds: proc.poll() is not None,
        ),
    )
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("stuck", _PUBLISHES, r"https://good\.example\.test", wait_s=4.0),
    ])
    assert soc_tunnel.start(8000, wait_s=20.0)["ok"] is True

    deadline = time.time() + 15
    while time.time() < deadline:
        if soc_tunnel.status()["revivals"]:
            break
        time.sleep(0.2)
    assert soc_tunnel.status()["revivals"] >= 1, soc_tunnel.status()


def test_a_route_that_never_worked_is_not_treated_as_a_regression(monkeypatch):
    """No prior success means we cannot tell a dead route from a probe
    path that never worked from here — a corporate resolver, say. Counting
    those would restart in a loop over a tunnel guests can reach fine."""
    monkeypatch.setattr(soc_tunnel, "_REVIVE_DELAY_S", 0.05)
    monkeypatch.setattr(soc_tunnel, "_ROUTE_DEAD_AFTER", 2)
    monkeypatch.setattr(
        soc_tunnel._TunnelManager, "_probe_once", staticmethod(lambda url: False),
    )
    monkeypatch.setattr(
        soc_tunnel._TunnelManager, "_settle", staticmethod(
            lambda proc, seconds: proc.poll() is not None,
        ),
    )
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("quiet", _PUBLISHES, r"https://good\.example\.test", wait_s=4.0),
    ])
    assert soc_tunnel.start(8000, wait_s=20.0)["ok"] is True
    time.sleep(2.0)
    st = soc_tunnel.status()
    assert st["revivals"] == 0, st
    assert st["running"] is True, st
    # ...and it says "can't tell" rather than accusing the tunnel.
    assert st["serving"] is None, st


def test_shutting_the_server_down_leaves_no_orphan(tmp_path):
    """Revival turns a tidy-up into a correctness requirement.

    On Ctrl-C the tunnel client shares our process group and takes the
    signal too — which is exactly the death the watchdog now exists to
    repair. Without the intent being cleared first it would spawn a
    replacement moments before the interpreter exits, and that one
    outlives us, holding a public tunnel to a port nothing is listening
    on. Runs a real server-like process so the ``atexit`` path is the
    thing under test rather than a stand-in for it.
    """
    import os
    import subprocess

    pidfile = tmp_path / "client.pid"
    inner = f'''
import sys, time, re
sys.path.insert(0, {str(_REPO)!r})
from server import tunnel as t
t._REVIVE_DELAY_S = 0.05
t._hostname_resolves = lambda host, **kw: True
script = ("import os, time\\n"
          "open({str(pidfile)!r}, 'w').write(str(os.getpid()))\\n"
          "print('tunnelled at https://good.example.test ok', flush=True)\\n"
          "time.sleep(600)")
t.PROVIDERS = [t.Provider(name='fake', binary=sys.executable,
    argv=lambda port: [sys.executable, '-c', script],
    url_re=re.compile(r'https://good\\.example\\.test'),
    install_hint='x', wait_s=6.0, rotates=False)]
print('OK' if t.start(8000, wait_s=20.0).get('ok') else 'FAIL', flush=True)
time.sleep(600)
'''
    server = subprocess.Popen(
        [sys.executable, "-c", inner], stdout=subprocess.PIPE, text=True,
    )
    try:
        assert server.stdout.readline().strip() == "OK"
        pid = int(pidfile.read_text())

        def _alive() -> bool:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False

        # Guard against the probe quietly testing nothing.
        assert _alive(), "tunnel client was never up"
        server.send_signal(2)  # Ctrl-C
        server.wait(timeout=30)
        time.sleep(2.0)
        assert not _alive(), "left a tunnel client orphaned after shutdown"
    finally:
        if server.poll() is None:
            server.kill()


def test_a_dead_tunnel_remembers_which_port_to_come_back_on(monkeypatch):
    """``_stop_locked`` clears ``_port`` and also runs between provider
    attempts, so the revival cannot read the port from there."""
    monkeypatch.setattr(soc_tunnel, "PROVIDERS", [
        _fake("good", _PUBLISHES, r"https://good\.example\.test", wait_s=4.0),
    ])
    soc_tunnel.start(8123, wait_s=20.0)
    mgr = soc_tunnel._manager
    with mgr._lock:
        mgr._stop_locked()
        assert mgr._port is None
        assert mgr._want_port == 8123
        assert mgr._wanted is True


# ── the regexes, against real observed output ────────────────────────

def test_localhost_run_regex_matches_its_real_banner():
    """Verbatim from a live run — the banner mentions the host twice, and
    only the second is a URL."""
    line = ("cf0377d8977167.lhr.life tunneled with tls termination, "
            "https://cf0377d8977167.lhr.life")
    prov = next(p for p in soc_tunnel.PROVIDERS if p.name == "localhost.run")
    assert prov.url_re.search(line).group(0) == "https://cf0377d8977167.lhr.life"


def test_cloudflare_regex_matches_its_real_banner():
    line = "|  https://ottawa-finite-hosts-answering.trycloudflare.com   |"
    prov = next(p for p in soc_tunnel.PROVIDERS if p.name == "cloudflare")
    got = prov.url_re.search(line).group(0)
    assert got == "https://ottawa-finite-hosts-answering.trycloudflare.com"


def test_the_regexes_do_not_match_each_others_hosts():
    lhr = next(p for p in soc_tunnel.PROVIDERS if p.name == "localhost.run")
    cf = next(p for p in soc_tunnel.PROVIDERS if p.name == "cloudflare")
    assert lhr.url_re.search("https://x.trycloudflare.com") is None
    assert cf.url_re.search("https://x.lhr.life") is None
