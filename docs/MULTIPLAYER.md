# Multiplayer & mobile — current method

This is the canonical "how we run a shared game right now" runbook (laptop +
phone, or friends across the internet). For the architecture/backend details
see the [README](../README.md) and [RULEBOOK.md](../RULEBOOK.md).

## TL;DR

```bash
python run_web.py
```

Click **MULTIPLAYER** on the title screen. The server opens a public
tunnel, moves your browser onto it, and creates the game; the **INVITE
PLAYERS** modal then gives a scannable QR and link per seat that work
from anywhere — other room, other city, phone on cellular.

No install, no account, no flags, no firewall change. Both clients sync
every 2.5s.

> **Host the game from your own terminal.** Never start the server via an
> AI coding assistant: the process belongs to that assistant's shell
> session and is killed when the session ends. In multiplayer that is not
> just your problem — the tunnel goes down with the server, so every
> guest is dropped mid-game and their invite links stop resolving.

## What the button actually does

It asks the server for a public URL, which `server/tunnel.py` gets by
running a tunnel client as a subprocess and scraping the URL it prints.
There is an **ordered list of providers**, and the first one that
publishes a URL **whose hostname this machine can resolve** wins:

| Order | Provider | Needs | Why it's there |
| --- | --- | --- | --- |
| 1 | `cloudflare` | `brew install cloudflared` | Its address lasts as long as the server runs, so invite links don't expire mid-game |
| 2 | `localhost.run` | nothing — plain `ssh` | No install and no account, so a bare laptop can still host; its free address rotates (see below) |

If none of them work you get a message saying why — naming every provider
tried and what went wrong with each — and the button falls back to
starting a local game rather than hanging.

When a *later* provider wins, the ones it beat are kept in
`status()["skipped"]` (provider, reason, and a bounded tail of what it
actually printed). That exists because a silent downgrade onto the
rotating fallback is indistinguishable from a clean start until the links
begin to die, which is precisely when it is too late to be useful.

### The time budget, and who is allowed to spend it

Each provider is costed at **publish + accept**, because the DNS gate
below is part of winning, not a free extra. The endpoint allows 75s in
total and every attempt holds back a floor for the providers behind it —
enough for them to *succeed*, deliberately not enough to fail slowly in.
Two providers' worst cases add up to more than anyone will sit through, so
reserving the worst case would starve the preferred provider out of
existence; reserving the floor keeps Cloudflare's full 20s publish window
while leaving localhost.run the 37s it needs to negotiate SSH and clear
the gate.

The preferred provider also gets a **second attempt**, funded strictly
from budget nobody behind it is owed. So a provider that fails fast is
retried — a fast failure is the kind that is plausibly a coin flip — and
one that fails slowly has already had its second chance. The last
provider is never retried: there is nothing behind it to protect.

None of this is visible on the happy path, which is a URL at ~6s and an
accepted name at ~18s. It only decides what happens on a bad day.

### Why the resolve check matters — and why it waits first

Publishing a URL is not the same as being usable. A network can block a
tunnel provider purely at the DNS layer, so the client connects happily,
prints a URL, and stays up — while the hostname resolves for nobody. That
failure is invisible from the tunnel's side, and it looks exactly like a
bug in this repo.

So after a provider publishes, we resolve its hostname through the **OS
resolver** — the same one the host's browser will use — and only then
accept it. A name the host can't look up is a name they can't hand to a
guest.

**The check waits about twelve seconds before its first lookup, and that
delay is the entire point of it.** Printing the URL and the DNS record
existing are separate events 2.4–3.5s apart (measured over three runs:
cloudflared printed at 5.4–5.8s, the name first resolved anywhere at
8.0–9.3s). Ask inside that window and you don't get a harmless "not yet"
— the resolver
**caches** the miss, and `trycloudflare.com` publishes a negative TTL of
**1800 seconds**. One impatient lookup therefore kills the hostname on the
host's own machine for half an hour, which is the one machine whose
resolver decides whether any invite link works.

This bit us hard enough to be worth recording: an earlier version of the
check queried immediately and retried every 0.8s, and the resulting
self-inflicted `NXDOMAIN` was diagnosed for a full day as "the corporate
network blocks Cloudflare". It didn't. See the changelog note in
`server/tunnel.py` (v1.18) and `tests/test_tunnel_providers.py`, which now
pins that neither the gate nor the liveness watchdog touches DNS early.

One honest limitation: the host's resolver isn't necessarily the guest's.
This catches the common case (both on the same corporate network) and it
guarantees the host can at least open their own game, but a guest on a
differently-filtered network can still be unlucky.

## Corporate networks (why there is more than one provider)

Locked-down networks do block tunnels on purpose, and the mechanisms catch
different providers:

- **Egress filtering.** A network can allow outbound 443 while dropping
  SSH to some hosts and the high ports `localtunnel` and `bore` use. This
  is the one we actually measured.
- **DNS blocklists.** A resolver *can* sinkhole a tunnel domain, which
  looks identical to the self-inflicted caching above and is why the gate
  exists. Worth knowing it's possible; we have not confirmed a real
  instance of it here.

Measured on a Snowflake-managed laptop, corporate resolver, VPN up,
2026-08-26:

| Provider | Hostname resolves | Transport | Verdict |
| --- | --- | --- | --- |
| `cloudflare` | yes, ~8s after start | yes (200) | **works**; hostname stable for the life of the process (soaked 48 min) |
| `localhost.run` | yes, immediately | yes (200) | works, but the free hostname rotates — see below |
| `localtunnel` | yes | no (502) | unusable |
| `serveo`, `pinggy` | yes | no (blocked) | unusable |

Cloudflare leads because its address is stable. `localhost.run` stays
behind it because needing no install at all is genuinely valuable on a
laptop without `cloudflared`, and because the two fail in unrelated
conditions — a network that drops SSH generally permits Cloudflare and
vice versa.

### Hostname rotation on free localhost.run tunnels

We connect as `nokey@localhost.run`, the anonymous free mode, and **its
hostname rotates**. Measured: a tunnel published
`cf80a4d4c4839b.lhr.life`, served 200 for thirteen minutes, then silently
moved to `f0498bc340546a.lhr.life` — after which the original returned
`503`. The `ssh` process stayed alive and healthy throughout, which is why
`server/tunnel.py` carries a liveness watchdog rather than trusting the
subprocess.

Every invite link and QR handed out before a rotation is dead afterwards,
and the host is hit hardest, because MULTIPLAYER navigates their browser
onto the tunnel origin. Their docs say free tunnels "change domain names
after a few hours"; thirteen minutes is what we actually saw.

The documented fix is to present a **registered** SSH key instead of
`nokey`. That is not a drop-in: an unregistered key is refused outright
(`Permission denied (publickey)`), so a stable name needs an account with
the key uploaded to `admin.localhost.run` — per person. That is exactly
the per-attendee setup this transport exists to avoid, so it is not the
default.

**What we do instead:** `cloudflare` leads the provider list, because a
quick tunnel keeps its hostname for the life of the process. localhost.run
stays as the fallback, since needing no install is genuinely valuable when
`cloudflared` isn't installed — but when it's carrying the tunnel, the
invite modal says so and warns that the links can expire.
`GET /api/tunnel/status` exposes this as `rotates` (known up front, from
the provider) alongside `rotations` (how many have actually happened).

## When the tunnel dies outright

A rotation changes the address; a death removes it. The watchdog used to
notice the subprocess had exited and simply retire, which put the game off
the air permanently and silently at whatever hour the provider gave up.

### Two deaths, and the one that actually happens

A tunnel can fail in two shapes and they look nothing alike from here:

| Shape | What we see | Detected in |
| --- | --- | --- |
| The client exits | `proc.poll()` returns | ≤0.5s |
| The edge stops routing | process healthy, URL 503s | ~90s |

The second is the one that took a real game off the air. `ssh` stayed up
and cheerful for twelve minutes while localhost.run served 503 on a
hostname it had already moved on from — `poll()` is `None` throughout, so
watching the process teaches you nothing. That is why the liveness
watchdog exists at all, and for a while it recorded `serving: false` and
did nothing with it, which left the one failure we had genuinely observed
as the one we did not repair.

Both now restart the tunnel. They differ only in how much confirmation
they need, and the asymmetry is deliberate: a process that has exited is
not coming back, so there is nothing to gain by waiting, while a failed
probe might be *our* network rather than the tunnel's. A false positive
costs everyone their links, so a dead route needs `_ROUTE_DEAD_AFTER`
consecutive failures — three, or ~90s at the 30s probe interval — before
we act. By then the links are worthless anyway and there is nothing left
to protect by waiting longer.

One case is deliberately excluded: a tunnel that has **never** answered.
Without a prior success we cannot tell a dead route from a probe path
that never worked from this machine — a corporate resolver, say — and
restarting on that would loop forever over a tunnel guests can reach
perfectly well. So `serving` stays `null` ("can't tell") rather than
`false`, and nothing fires.

It now **restarts the tunnel**, up to three times. A restart means a new
hostname, so it invalidates every link already handed out exactly as a
rotation does — but a fresh link beats a dead server, and there is no way
to keep an anonymous hostname across a process death.

The new address is **printed to the server's terminal**, and that is the
point rather than a nicety: when the tunnel dies, every browser pointed at
it — including the host's, if they were playing through it — is stranded
on a hostname that no longer exists, and cannot be told the new one by a
server it can no longer reach. The console is the only channel left open.

```
[soc] tunnel died and was restarted (1/3). NEW ADDRESS — old invite links are dead:
[soc]   https://sundry-words-here.trycloudflare.com
```

`GET /api/tunnel/status` carries the state as four fields that together
separate the cases a host has to tell apart:

| Fields | Meaning |
| --- | --- |
| `expected: false` | no tunnel was ever asked for |
| `running: true` | up; `revivals` says how many deaths it has survived |
| `reviving: true` | briefly down, coming back |
| `expected: true`, not running, not reviving | down for good, out of restarts |

`revivals` is kept until an explicit stop rather than cleared on the way
down, because a host whose tunnel has finally died most needs to know it
died three times first. Pressing MULTIPLAYER again gives it a fresh
budget; that is a human deciding, which is the difference between a retry
and a retry storm against someone else's free service.

### What the players see, and why it has to be two things

A restart splits the room in half, and the halves cannot help each other:

- whoever is still connected — in practice the host, on localhost — is
  holding the only working copy of the new address;
- everyone who was playing through the old one is on a hostname that no
  longer resolves and **cannot be told anything by the server**, because
  reaching the server is exactly what they have lost.

There is no push that fixes the second group, and no amount of cleverness
invents one. So the recovery is a human passing one URL along, and the
UI's whole job is to make that the only step. Both halves are the same
component (`showTunnelDownModal`, `server/static/app.js`) because it is
one event seen from two sides:

| Mode | Who sees it | Trigger | What it offers |
| --- | --- | --- | --- |
| `restarted` | anyone still connected | `revivals` goes up | fresh per-seat links + QRs to send on |
| `stranded` | anyone cut off | 20s offline on a tunnel origin | a box to paste the new address into |

The asymmetry that makes this cheap: a seat link is
`<origin>/play?session=<sid>&player=<seat>`, and **only the origin
changes**. Session and seat are already in the stranded page's own URL —
the half of the link that survived — so it can rebuild its own link from
a bare address. The host never works out who needs which link, and the
player never learns what a seat id is.

Two details that are easy to get wrong:

- **The offline banner must not promise recovery on a tunnel origin.** It
  used to end "this page catches up on its own when it returns", which is
  true for a LAN blip and a lie after a restart — that address never
  returns, and a player who believes it waits forever for a server that
  cannot answer. `_looksLikeTunnelOrigin()` decides, and it is a negative
  test (not loopback, not a private range) rather than a list of the two
  providers we ship, so a third provider fails safe instead of being
  silently classified as a LAN game.
- **The stranded modal clears itself if the connection comes back**, but
  the host's link list does not. A warning that outlives its cause trains
  people to ignore the next one; a list of links the host still has to
  send has not stopped being true just because their own page is fine.

**Verify with `backstage/probes/_probe_tunnel_down.py`.** It drives both halves in
a real browser, using Chromium's `--host-resolver-rules` to put the page
on a genuine tunnel-looking hostname pointed at the local server — the
classifier is the thing under test, so `127.0.0.1` would not do. The
scene that matters most is the last assertion: that pasting the new
address lands the player back in **their own** seat. It joins as `p2` on
purpose, because a bug that rebuilt the link from a default seat would
pass as `p1` and be invisible.

## Same-Wi-Fi LAN play (usually not an option on managed laptops)

```bash
python run_web.py --lan
# [soc] LAN hosting: other devices join at http://192.168.1.42:8000/
```

`--lan` binds every interface and turns auto-reload off, and the invite
modal then addresses links to your LAN IP.

> **This requires the host to accept inbound connections**, which a
> corporate MDM usually forbids. Snowflake laptops are enrolled in Jamf
> with the firewall set to *block all incoming*, so LAN play is simply
> unavailable there and no flag will change that — use the tunnel, which
> only ever dials outward. Check with `GET /api/meta/lan`: it reports
> `lan_reachable` and `lan_firewalled` and names the fix in `lan_hint`.

### Storage

The **STORAGE** row in the launcher decides whether the match survives a
restart. Memory is fine for one sitting and needs no setup; Snowflake
keeps the season (schema deploy required — `docs/SNOWFLAKE_SETUP.md` §2).
Since v1.14 that is a per-game choice, so `SOC_BACKEND` no longer needs
pinning before a party.

## Seat links live at `/play`, not `/`

A seat link is `http://<host>/play?session=<id>&player=p2`. The `/play`
matters: `/` is the title screen and has no session-loading code, so a
link pointed there silently opens the title screen instead of the game.
Until v1.12 the LAN invite QR codes did exactly that — `fetchLanOrigin()`
returns a bare origin whose path is `/`, so every QR a phone scanned on
the same Wi-Fi landed on the title screen. Generation now pins `/play`,
and `/` redirects when it sees a `?session=`, so older links still work.

## Why the button moves your browser onto the tunnel

Invite links and QR codes are built from **whatever URL the laptop is browsing**
(`window.location`), so:

| Laptop is on…            | QR/links point at…        | Guest result                    |
| ------------------------ | ------------------------- | ------------------------------- |
| **tunnel URL**           | the public tunnel URL     | ✅ works anywhere               |
| LAN IP (`192.168.x.x`)   | the LAN IP                | ✅ only on same Wi-Fi + firewall allows |
| `localhost` / `127.0.0.1`| the LAN IP (auto-rewrite) | ❌ unless same Wi-Fi **and** firewall allows incoming |

That's why **MULTIPLAYER** redirects you to the tunnel origin before
creating the game: it puts the host on the public URL so every seat link
generated afterwards is automatically reachable. You don't have to do
anything, but it explains why the address bar changes.

The tunnel URL is **per-server, not per-game** — it keeps working for every new
game; only the `?session=<id>` part changes. It changes only when the tunnel
process restarts, since anonymous quick tunnels get a new random hostname
each run. If you need a stable address, both providers offer accounts that
issue a persistent name.

## Mobile interface notes

> **`/mobile` was retired in v1.13.** It was a 953-line standalone fork,
> frozen since the initial commit while `app.js` moved on, and the main
> SPA had long since grown the better phone UX. `/mobile` now redirects
> to `/play` and the invite modal offers one QR per seat rather than two.

- The phone client is the main SPA; a `?player=pN` link boots it into a
  playable, fog-of-war seat (not the read-only watcher).
- **Map**: one-finger drag pans, two-finger pinch zooms, tap a cell to deploy.
  A tap-vs-drag guard stops a pan/pinch from firing a stray order.
- **Map-first layout**: on a phone the map fills the screen. A fixed bottom
  bar shows **▤ ORDERS · n** and **» TRANSMIT** — that's it when you're just
  looking at the map.
- **Orders sheet**: tapping **ORDERS** slides the panel up as a sheet *over*
  the map (no page scrolling). The tab strip rides on top of the sheet so you
  can switch to VAULT / GRAPHICS / LOG. Tapping the visible map, or ORDERS
  again, dismisses it.
- **Deploy loop**: open ORDERS → tap an asset chip (probe/harvester/EMP/…) →
  the sheet auto-drops so the map is tappable → tap the target cell → reopen
  ORDERS to review the queue → **TRANSMIT** (always one tap in the bottom bar).

## Hosting rules (don't break sync)

- **One uvicorn worker, no `--reload`.** The cross-player submit lock is
  a per-process `threading.Lock`, and a memory-backed season lives in
  that process — so an incidental file save mid-party doesn't just
  hiccup, it resets everyone. `run_web.py --lan` turns reload off for
  you; if you hand-roll a uvicorn command, leave `--reload` out.
- **Choose storage when you create the game**, in the launcher's STORAGE
  row. All browsers hit the same process either way.

## Troubleshooting

- **Start here:** `GET /api/tunnel/status` says whether a tunnel is up,
  which provider is carrying it, and which providers are installed. For
  LAN problems, `GET /api/meta/lan` answers `lan_reachable` and
  `lan_firewalled` and names the fix in `lan_hint`.
- **MULTIPLAYER says it couldn't establish a tunnel:** the error lists
  every provider it tried and why each failed. If `cloudflare` isn't among
  them, install it with `brew install cloudflared` — it's the one whose
  address survives the whole game.
- **The tunnel starts but *your own browser* can't open the URL:** either
  your DNS is sinkholing that provider's domain, or something looked the
  name up before it existed and cached the miss. Compare `dig <host>` with
  `dig @1.1.1.1 <host>`: if the public resolver answers and yours doesn't,
  wait for the negative TTL to lapse (30 minutes on `trycloudflare.com`)
  or just click MULTIPLAYER again for a fresh name — a restart is far
  quicker than the cache expiring.
  ⚠️ **Don't `dig` a tunnel hostname in the first few seconds** to check on
  it. The record isn't published yet, and the cached miss is precisely
  what makes the link dead for the next half hour. Give it ~15s.
- **Error 1033 from Cloudflare:** the name resolved fine and you reached
  Cloudflare's edge — it's the *origin* that's missing, i.e. `cloudflared`
  exited. Check the server is still running and click MULTIPLAYER again.
- **It worked earlier and the URL is now dead:** anonymous quick tunnels
  get a new hostname on every restart. Click MULTIPLAYER again and
  reshare; old links do not survive a restart.
- **Guest gets the title screen instead of the game:** an old link
  pointing at `/` rather than `/play`. `/` redirects when it sees
  `?session=`, so re-copy the link from the invite modal.
- **`ERR_CONNECTION_RESET` on the phone (LAN only):** the macOS firewall
  is blocking incoming connections. Check with
  `/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate`.
  On an MDM-managed laptop you probably can't change this — use the
  tunnel, which needs no inbound access at all.
- **Phone can't reach the LAN IP but `localhost` works on the laptop:**
  firewall, or the Wi-Fi has client isolation (common on guest and
  conference networks). Use the tunnel.
