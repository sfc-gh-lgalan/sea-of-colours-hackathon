"""Night runner — executes per-player move queues into the session state.

The orchestrator gives each player a 21-hour *applied* budget per night
(RULEBOOK §3.10 v0.7.4 — one applied move per planetary night hour).
Queues themselves may be longer (up to :data:`MAX_QUEUE_LEN`); invalid
items — whether structurally malformed (``WasteMove`` markers from the
parser) or rejected at runtime (out-of-bounds step, harvester still
orbital, etc.) — are surfaced as yellow error log lines next to the
attempted action and **do not** consume a slot from the player's
budget. The simulator advances each player's pointer past invalid items
until it finds a valid one, applies it (one valid move per round per
player, alternating p1 → p2), and pushes a replay frame.

Rules (v0.6.0):

- Each *valid* applied move consumes one of :data:`MAX_MOVES` slots.
- Each *invalid* item logs an error (``level: "error"``) and is skipped.
- Harvester drop / step harvests every coloured tile entered — RED →
  GREEN, GREEN → EMPTY, BLUE → EMPTY (RULEBOOK §3.12). There is no
  per-night cap; the natural limit is the harvester's 6-parcel hold
  (drop + 5 steps).
- Probes spawned during the queue persist across nights — they're only
  destroyed when a harvester rides over them.
- Harvesters still on the surface at dawn forfeit their cargo.
"""

from __future__ import annotations

from typing import (
    AbstractSet,
    Dict,
    List,
    Mapping,
    Optional,
    Set,
    Tuple,
    TYPE_CHECKING,
)

from sea_of_colours.game.policy import (
    ChaffFlareMove,
    DropMove,
    EmpLaunchMove,
    SnapLaunchMove,
    MAX_MOVES,
    Move,
    PickupMove,
    ProbeMove,
    StepMove,
    WaitMove,
    WasteMove,
)
from sea_of_colours.game.tuning import live_only_drops

if TYPE_CHECKING:  # avoid runtime cycle
    from sea_of_colours.game.session import GameSession, PlayerId


# v0.9.6 — N-seat aware. Each call into :meth:`NightSimulator.run`
# derives the iteration order from ``sess.players`` so a session can
# carry 1-4 seats. The constant survives as a legacy default for any
# code path that still hard-codes a 2-seat shape (unit-test fixtures
# that bypass the engine, etc).
PLAYERS_ORDER: Tuple[str, ...] = ("p1", "p2")


def _next_actionable(
    queue: List[Move], start: int
) -> Tuple[Optional[Move], int]:
    """Return ``(queue[start], start)`` or ``(None, start)`` if exhausted.

    v0.9.9 — under the new "every row burns a slot" rule the
    orchestrator no longer skips :class:`WasteMove` markers when
    peeking ahead for pre-empt-eligible actions (EMP / chaff). A
    seat that wedges a waste in front of a launch loses that hour
    to the strikeout; the launch fires the *next* round. Callers
    that previously relied on the skip behaviour (swap pre-pass,
    chaff cancel, EMP peek) instead bail out cleanly when they
    encounter a :class:`WasteMove` so the main dispatch handles it.
    """
    if start < len(queue):
        return queue[start], start
    return None, start


def _describe_move(move: Move) -> str:
    """Compact human-readable form of a move for error context."""
    if isinstance(move, ProbeMove):
        return f"probe @({move.at[0]},{move.at[1]})"
    if isinstance(move, DropMove):
        return f"drop {move.unit} @({move.at[0]},{move.at[1]})"
    if isinstance(move, StepMove):
        return f"step {move.unit} → ({move.to[0]},{move.to[1]})"
    if isinstance(move, PickupMove):
        return f"pickup {move.unit}"
    if isinstance(move, WaitMove):
        return "wait"
    if isinstance(move, EmpLaunchMove):
        cells = ",".join(f"({tx},{ty})" for tx, ty in move.ats)
        return f"emp_launch @{cells}"
    if isinstance(move, ChaffFlareMove):
        return "chaff_flare"
    if isinstance(move, SnapLaunchMove):
        return f"snap_launch @({move.at[0]},{move.at[1]})"
    if isinstance(move, WasteMove):
        return "invalid policy entry"
    return type(move).__name__


class NightSimulator:
    """Interleaves two player move queues per round and finalises the night."""

    def run(self, sess: "GameSession", queues: Mapping[str, List[Move]]) -> None:
        from sea_of_colours.game.session import Phase, cast_player

        # v0.9.6 — derive the iteration order from the per-session
        # seat list so a 3- or 4-seat night resolves the same way a
        # 2-seat night does. Cached on a local so every helper below
        # can read it without reaching back into ``sess.players``.
        seats: Tuple[str, ...] = tuple(sess.players)

        for ent in sess.entities.values():
            ent.lost_last_night = False

        replay: List[dict] = []

        # v0.7.4 — PRAXIS is the name for the night-resolution event:
        # the moment both Houses' policies execute simultaneously on
        # the planet's surface (RULEBOOK §3.10). The night clock runs
        # 1..HOURS_PER_NIGHT (21) and each applied move is one hour.
        sess.log_info(f"[praxis] day {sess.day} — Nox begins")

        # v1.6 — reset the per-night chaff-egress-block ledger used for
        # harvester-lost-via-chaff attribution (see dawn_strand_harvesters).
        sess._chaff_egress_block = {}

        # RULEBOOK §3.9.2 — start the night with a clean one-outing-per-harvester
        # ledger for this day (a harvester may deploy once per night; the guard in
        # ``try_drop_unit`` refuses any second drop of the same unit tonight).
        sess.deployed_harvesters_by_day[int(sess.day)] = set()

        queue_for: Dict[str, List[Move]] = {
            p: list(queues.get(p, [])) for p in seats
        }
        pointers: Dict[str, int] = {p: 0 for p in seats}
        applied: Dict[str, int] = {p: 0 for p in seats}

        # Stash both seats' raw queues on the opening frame so the
        # synced ORDERS drawer (v0.7.3) can render scheduled / executed
        # / failed states without back-channelling. Each scheduled
        # entry is a small dict: {idx, action, target?, unit?, status}.
        # ``status`` starts as "pending" and the frontend flips it to
        # "ok" / "failed" as the scrubber walks the frames.
        opening_orders = self._build_scheduled_orders(queue_for)
        sess.replay_push_scene(
            replay,
            "[opening] PRAXIS begins",
            tag="open",
            attempted=None,
            outcome=None,
            hour=0,
        )
        # Inject ``scheduled_orders`` directly onto the opening frame
        # (replay_push_scene doesn't accept it because the field is
        # purely informational and only meaningful on the opener).
        if replay and opening_orders:
            replay[-1]["scheduled_orders"] = opening_orders

        def _has_work(p: str) -> bool:
            return (
                applied[p] < MAX_MOVES
                and pointers[p] < len(queue_for[p])
            )

        # v0.9 — per-hour interdiction state. ``chaff_active_until``
        # is the latest hour a chaff is still in effect (set when a
        # chaff move resolves; consulted at the start of every
        # subsequent hour). ``chaff_triggerers_by_hour[hour]`` lists
        # which seats are immune for that SINGLE hour because they fired
        # the flare then (they spent the slot launching, so they aren't
        # also cancelled the same hour). The launcher is NOT propagated
        # forward — chaff jams its own house for the carry-over hours
        # too, so firing it costs the launcher the full duration window.
        # ``disabled_units_this_hour`` is the set of harvester ids whose
        # current cell sits inside any active EMP cloud at hour-N start.
        chaff_triggerers_by_hour: Dict[int, set[str]] = {}
        chaff_active_until: int = 0
        # Hours at which a chaff actually RESOLVED (fired). Drives
        # ``chaff_active_until``; kept distinct from the per-hour
        # launch-immunity set above.
        chaff_fired_hours: set[int] = set()

        # Round-by-round interleave. A "round" is one valid applied move
        # per player; invalid items are skipped without burning a slot.
        # The shared planetary hour-of-night ticks once per pass — both
        # Houses' Nth applied moves happen "during hour N" of the same
        # night, in parallel (RULEBOOK §3.10, v0.7.4).
        while any(_has_work(p) for p in seats):
            current_hour = (max(applied.values()) if applied else 0) + 1

            # v0.9 — Pre-hour phase (RULEBOOK §5):
            #   1. Tick EMP clouds (decay + prune).
            #   2. Identify harvesters currently inside any cloud cell.
            #   3. Peek-ahead chaff: if every seat's next actionable
            #      move is a ChaffFlareMove, apply them NOW so the
            #      rest of the hour knows about the cancellation.
            #
            # v1.19 — ``chaff_active_until`` is passed in DELIBERATELY before
            # this hour's own refresh below, so it describes only windows
            # opened on EARLIER hours. That distinction is the whole rule:
            # two seats flaring on the same hour are not yet inside anyone's
            # window, so both fire (and one is wasted, §4.9.5); a flare
            # queued into a window already running is a cancelled action.
            disabled_units_this_hour = self._pre_hour_phase(
                sess, queue_for, pointers, applied, replay,
                hour=current_hour,
                chaff_triggerers_by_hour=chaff_triggerers_by_hour,
                chaff_fired_hours=chaff_fired_hours,
                seats=seats,
                chaff_active_until=chaff_active_until,
            )
            # v1.19 — seats whose slot for THIS hour was already spent in
            # the pre-hour phase (EMP launch / chaff flare). They must be
            # invisible to the collision pre-passes below: those peek each
            # seat's *next* queued move, which for a pre-empted seat is
            # next hour's action, and resolving it now would give that
            # seat two actions in one hour. That was live — an EMP launch
            # plus a swap collision in the same hour, damaging BOTH
            # harvesters — and it needed no chaff to reach.
            preempted_now = getattr(sess, "_preempted_seats_this_hour", set())

            # Refresh chaff_active_until ONLY when a chaff freshly fired
            # this hour (not on a carry-over immunity entry).
            if current_hour in chaff_fired_hours:
                chaff_active_until = max(
                    chaff_active_until,
                    self._chaff_until_for(sess, current_hour),
                )

            # Pass-through swap pre-pass (RULEBOOK §3.6 v0.7.3): only
            # fires if no side is being chaffed at this hour (chaff
            # cancels everything else; a swap would be one of those
            # cancelled things). For N-seat games we still scan the
            # seat pairs; a non-pair swap (3-way etc.) cannot occur
            # because every move targets exactly one tile.
            chaff_this_hour = current_hour <= chaff_active_until
            triggerers = chaff_triggerers_by_hour.get(current_hour, set())
            all_chaff_immune = chaff_this_hour and set(seats) <= triggerers
            if not chaff_this_hour or all_chaff_immune:
                # v1.48 — both pre-passes run before either can send us
                # round again, and each reports the seats it spent so the
                # next one does not act for them twice. A swap used to
                # ``continue`` on the spot, which pushed any contested
                # drop on the SAME hour into the next one; contention is
                # contention, and all of it belongs to this hour.
                spent_now: Set[str] = set(preempted_now)

                handled_swap = self._maybe_resolve_swap_collision(
                    sess, queue_for, pointers, applied, replay,
                    hour=current_hour,
                    seats=seats,
                    skip_seats=spent_now,
                )
                spent_now |= handled_swap

                # v0.9.10 — simultaneous drop collision check. If multiple
                # harvesters try to drop on the same square this hour, none
                # land, all become damaged and stay in orbit.
                handled_simul_drop = self._maybe_resolve_simultaneous_drops(
                    sess, queue_for, pointers, applied, replay,
                    hour=current_hour,
                    seats=seats,
                    skip_seats=spent_now,
                )
                spent_now |= handled_simul_drop

                # v1.49 — the egress snapshot is taken HERE, ahead of the
                # converge pre-pass, because that pass has to know whether
                # the harvester standing on a contested cell is leaving.
                # Recomputed below for the dispatch proper, since a
                # converge collision changes who is still standing.
                vacating = self._departing_harvesters(
                    sess, seats, queue_for, pointers, applied,
                    preempted=preempted_now,
                    chaff_active_until=chaff_active_until,
                    chaff_triggerers=triggerers,
                    current_hour=current_hour,
                    disabled_units=disabled_units_this_hour,
                    spent_seats=spent_now,
                )

                # v1.49 (§3.17.6) — two harvesters stepping into one empty
                # cell. The plainest reading of §3.17's opening sentence,
                # but not one of its four illustrated patterns, so it used
                # to fall through to the seat loop and let whoever was
                # walked first take the cell.
                handled_converge = self._maybe_resolve_converging_steps(
                    sess, queue_for, pointers, applied, replay,
                    hour=current_hour,
                    seats=seats,
                    skip_seats=spent_now,
                    disabled_units=disabled_units_this_hour,
                    departing_units=vacating,
                )

                if handled_swap or handled_simul_drop or handled_converge:
                    continue

            # v0.9.17 — hour-start visibility snapshot for live-only drops,
            # so a beacon a rival destroys later this same hour still
            # validates this hour's landing (parallel resolution, §3.10).
            # Only populated when the mode is on — default drops keep their
            # resolve-time LOS.
            #
            # v1.28 — TAKEN INSIDE ``_pre_hour_phase`` and merely read here.
            # Recomputing it at this point is the bug that shipped: this
            # line sits after the pre-hour phase, which is where this hour's
            # EMP salvos fly, so the "hour-start" snapshot was taken with
            # the rival's beacons already fried. Do not move it back.
            live_snapshot: Dict[str, set] = getattr(
                sess, "_live_snapshot_this_hour", {},
            ) or {}

            # v1.48 — hour-start EGRESS snapshot, the occupancy sibling of
            # the visibility snapshot above and for the same reason
            # (parallel resolution, §3.10). A harvester lifting off — or,
            # since v1.49, stepping off — this hour is departing, not
            # arriving, so §3.17 does not collide a lander with it. Taken
            # before any seat acts so the outcome stops depending on which
            # seat the loop reaches first.
            sess._departing_units_this_hour = self._departing_harvesters(  # type: ignore[attr-defined]
                sess, seats, queue_for, pointers, applied,
                preempted=getattr(sess, "_preempted_seats_this_hour", set()),
                disabled_units=disabled_units_this_hour,
                chaff_active_until=chaff_active_until,
                chaff_triggerers=chaff_triggerers_by_hour.get(
                    current_hour, set(),
                ),
                current_hour=current_hour,
            )

            preempted = getattr(sess, "_preempted_seats_this_hour", set())
            for p in seats:
                if applied[p] >= MAX_MOVES:
                    continue
                if p in preempted:
                    # Already applied during the pre-hour phase (chaff
                    # or EMP launch). Don't consume another slot.
                    continue
                pid = cast_player(p)
                # If chaff is active this hour and this seat is NOT a
                # triggerer, cancel its next actionable move with a
                # ``chaffed`` tag. The slot still counts as applied.
                if (
                    current_hour <= chaff_active_until
                    and p not in chaff_triggerers_by_hour.get(current_hour, set())
                ):
                    # v1.6 kill-feed: who is jamming this seat right now?
                    attackers = self._active_chaffers(
                        sess, chaff_triggerers_by_hour, current_hour,
                    )
                    # Peek the move being cancelled — if it's this seat's
                    # egress (pickup), remember the chaffer so a dawn strand
                    # can be blamed on them (harv_lost_chaff).
                    blocked_move, _ = _next_actionable(queue_for[p], pointers[p])
                    cancelled = self._cancel_next_actionable(
                        sess, pid, queue_for[p], pointers, replay,
                        hour=current_hour,
                        tag="chaffed",
                        caption=f"{p}: action cancelled by orbital chaff @ hour {current_hour}",
                    )
                    if cancelled:
                        applied[p] += 1
                        sess._bump_moves_cancelled(p)
                        for atk in attackers:
                            sess._attrib("chaff_jams", atk, p)
                        # v1.8 — canonical combat feed: record the jammed
                        # slot (hour + attacker + affected unit) so the
                        # victim's last_night recap shows what chaff cost it.
                        _jam_uid = getattr(blocked_move, "unit", None)
                        sess.note_chaff_jam(
                            str(p),
                            next(iter(attackers)) if attackers else "",
                            int(current_hour),
                            unit=_jam_uid if isinstance(_jam_uid, str) else None,
                        )
                        if isinstance(blocked_move, PickupMove) and attackers:
                            uid = getattr(blocked_move, "unit", None)
                            if isinstance(uid, str):
                                sess._chaff_egress_block[uid] = next(
                                    iter(attackers)
                                )
                    continue
                applied_this_pass = self._advance_until_valid(
                    sess, pid, queue_for[p], pointers, replay,
                    hour=current_hour,
                    disabled_units=disabled_units_this_hour,
                    live_override=live_snapshot.get(p),
                )
                if applied_this_pass:
                    applied[p] += 1

            # v1.49 (§3.11.1) — last thing in the hour, once every seat
            # has had its say: a probe that ended up under a harvester is
            # crushed no matter which of the two got there first. See
            # :meth:`_crush_probes_under_harvesters` for why the mover's
            # own crush is left exactly as it was.
            self._crush_probes_under_harvesters(
                sess, replay, hour=current_hour,
            )

        # v0.9.13 — normalise per-seat hour stamps to the TRUE
        # hours-consumed for each seat. During resolution the planetary
        # clock is ``current_hour = max(applied) + 1`` shared across all
        # seats. That's correct while seats march in lockstep, but a seat
        # that fell behind (its slot was eaten by an EMP-launch / chaff
        # pre-empt, which emits a frame WITHOUT bumping ``applied``) keeps
        # marching after the leading seats exhaust their queues — at which
        # point ``max(applied)`` freezes and every catch-up frame the
        # lagging seat emits inherits the SAME frozen hour. That produced
        # the duplicate ``H0x`` stamps in the replay.
        #
        # Frames are already appended in strict chronological emission
        # order, and the dispatch loop emits at most one frame per seat
        # per hour (one move per seat per pass; pre-empted seats are
        # skipped in the main pass). So a per-owner running counter
        # recovers each seat's real hour-of-night — including pre-empt
        # frames, which legitimately consume the seat's hour — and the
        # one-frame-per-seat-per-hour invariant holds by construction.
        seat_hour_seq: Dict[str, int] = {}
        for frame in replay:
            f_owner = frame.get("owner")
            f_hour = frame.get("hour")
            if not f_owner or not isinstance(f_hour, int) or f_hour <= 0:
                continue
            seat_hour_seq[f_owner] = seat_hour_seq.get(f_owner, 0) + 1
            frame["hour"] = seat_hour_seq[f_owner]

        # Defensive check (should never fire now that hours are re-stamped
        # per seat above): each seat must carry at most ONE replay frame
        # per hour-of-night (RULEBOOK §3.10). Boundary frames (owner=None
        # / hour=0 — dawn, opening) are exempt by convention.
        seen_hour_keys: set[tuple[str, int]] = set()
        for frame in replay:
            f_owner = frame.get("owner")
            f_hour = frame.get("hour")
            if not f_owner or not isinstance(f_hour, int) or f_hour <= 0:
                continue
            key = (str(f_owner), int(f_hour))
            if key in seen_hour_keys:
                sess.log_error(
                    f"[orchestrator] WARN: duplicate hour stamp "
                    f"H{f_hour:02d} for seat {f_owner} — "
                    f"this violates the one-action-per-seat-per-hour "
                    f"contract (RULEBOOK §3.10). Frame caption: "
                    f"{(frame.get('caption') or '')[:80]}"
                )
            seen_hour_keys.add(key)

        # Tick the day counters on every asset still surfaced *before*
        # the dawn strand teleports harvesters back to orbit, so a
        # harvester that successfully picked up still gets credited for
        # tonight's deployment and a stranded one is credited once
        # before the dawn penalty clears its position.
        sess.advance_asset_day_counters()

        # v0.9.16 — roll the replay one notch PAST the last live cloud so
        # the night never freezes on a standing EMP cloud (it read as
        # "weird to end the day with an emp cloud remaining"). If any
        # cloud is still up when the queues run dry, emit a boundary
        # "decay" frame per remaining cloud-hour — each snapshots the
        # shrinking field — ending on the first frame where the field is
        # fully clear (one hour after the cloud expires). These are
        # purely visual (owner=None, hour=0): no actions resolve here and
        # the per-seat hour re-stamp / duplicate-hour guards above are
        # already done, so the boundary frames slot in cleanly. The real
        # evaporation happens just below.
        if any(int(c.get("hours_remaining", 0)) > 0 for c in sess.emp_clouds):
            _decay_guard = 0
            while sess.emp_clouds and _decay_guard < 64:
                _decay_guard += 1
                # Visual-only decay: shrink ``hours_remaining`` and prune
                # WITHOUT the probe/mine sweep that tick_emp_clouds runs.
                # The field already swept every ACTION hour during the
                # night; these post-queue frames only wind the cloud down
                # for the replay, so they must not alter game outcomes.
                for c in sess.emp_clouds:
                    c["hours_remaining"] = max(
                        0, int(c.get("hours_remaining", 0)) - 1,
                    )
                sess.emp_clouds = [
                    c for c in sess.emp_clouds
                    if int(c.get("hours_remaining", 0)) > 0
                ]
                caption = (
                    "[Aurora] EMP cloud dissipating"
                    if sess.emp_clouds
                    else "[Aurora] EMP cloud cleared"
                )
                sess.replay_push_scene(
                    replay,
                    caption,
                    tag="emp_decay",
                    hour=0,
                )

        # v0.9 — EMP clouds are intra-night only (RULEBOOK §5).
        # Evaporate any survivors at dawn so a 17-hour-launch cloud
        # doesn't bleed into tomorrow's planning phase.
        sess.emp_clouds = []
        sess.snap_clouds = []
        # Per-night transient: clear the chaff pre-empt cache so the
        # next night starts fresh.
        if hasattr(sess, "_preempted_seats_this_hour"):
            try:
                delattr(sess, "_preempted_seats_this_hour")
            except AttributeError:
                pass
        if hasattr(sess, "_emp_established_cells_this_hour"):
            try:
                delattr(sess, "_emp_established_cells_this_hour")
            except AttributeError:
                pass
        if hasattr(sess, "_live_snapshot_this_hour"):
            try:
                delattr(sess, "_live_snapshot_this_hour")
            except AttributeError:
                pass
        if hasattr(sess, "_snap_hot_cells_this_hour"):
            try:
                delattr(sess, "_snap_hot_cells_this_hour")
            except AttributeError:
                pass
        if hasattr(sess, "_departing_units_this_hour"):
            try:
                delattr(sess, "_departing_units_this_hour")
            except AttributeError:
                pass

        destroyed_at_dawn = sess.dawn_strand_harvesters()
        for line in destroyed_at_dawn:
            sess.log_error(line)

        # Probes persist between days (RULEBOOK §3.11.1): they are only
        # destroyed when a harvester rides over them during a night —
        # UNLESS optional probe decay is enabled
        # (``SOC_PROBE_LIFETIME_NIGHTS``), in which case probes that have
        # outlived their lifetime expire now (disk drops to echo).
        expired_probes = sess.decay_probes()

        # v1.1 — ALWAYS emit exactly one dawn boundary frame at the
        # synthetic "22nd hour". This is the sunrise sweep the watcher
        # animates: stranded harvesters take the red-X, lifetime-expired
        # probes drop to echo, and every surviving probe loses a ring
        # (decremented in ``replay_push_scene`` for ``tag="dawn"``). The
        # caption carries the destruction tally so the frame reads cleanly
        # even on a quiet night.
        sess.replay_push_scene(
            replay,
            (
                f"[H22] Aurora — {len(expired_probes)} probe(s) destroyed, "
                f"{len(destroyed_at_dawn)} harvester(s) destroyed"
            ),
            tag="dawn",
            hour=22,
        )

        # v0.9.11 — stamp the night's observable orbital activity
        # (launch/recovery counts + damage, NO coordinates) and the
        # end-of-night ("pre"-orbital) station readings, keyed by the
        # night's day number so they line up with this day's replay
        # frames + log. Feeds the Pre-Orbital Recap report + agent view.
        night_day = str(int(sess.day))
        activity = sess.tally_orbital_activity(replay)
        if activity:
            sess.orbital_activity_by_day[night_day] = activity
        events = sess.tally_orbital_events(replay)
        if events:
            sess.orbital_events_by_day[night_day] = events
        sess.station_obs_by_day.setdefault(night_day, {})[
            "pre"
        ] = sess.station_observation_snapshot()

        sess.day += 1
        # Collision marks live for exactly 1 game day (§3.6 v0.7.3).
        # Prune AFTER the day increment so the just-finished night's
        # marks survive into the next planning phase as "yesterday's"
        # scars, then fall off the following dawn.
        sess._prune_collision_marks()
        # v1.8 — EMP map scars decay on the same 1-day cadence.
        sess._prune_emp_marks()
        # v0.8.0 — dawn auto-repair removed. Damaged harvesters now
        # stay damaged into the next Orbit phase and must be repaired
        # via a paid Orbit REPAIR action (RULEBOOK §3.6.1). Pickup
        # still clears damage in real time so a successful pickup is
        # the "free" repair path.
        # Season cap (§3.0, v0.7.4): once we've simulated the final
        # planned night, day is now season_day_cap+1 (i.e. nothing left
        # to plan for). Pin phase to SEASON_COMPLETE and skip the
        # planning hand-off — the caller / UI is responsible for
        # surfacing the season-over banner and starting a new game.
        # Replay frames + log are still flushed to the store on the
        # way out so the watcher can replay the full final night. The
        # cap is now read from the per-session attribute so 5-night
        # demos and 7-night tournament runs share one schema.
        cap = int(getattr(sess, "season_day_cap", None) or 7)
        if sess.day > cap:
            # v1.0 — the final night opened a restricted SETTLEMENT ORBIT
            # rather than ending the season outright, so there was a last
            # shipping window behind the end-of-game screen.
            #
            # v1.30 — that window no longer decides anything, so it no
            # longer asks. Since v1.13 the only orbit actions left spend
            # credits on hardware, RED ships automatically and GREEN
            # clears automatically; on the terminal orbit the hardware is
            # never used, which is why the heuristic answers "nothing
            # worth buying" and why a human sees an empty panel. Every
            # seat still had to SUBMIT that nothing before anyone could be
            # told who won — in a 4-player game, three people waiting on a
            # fourth to click through a screen with no decision on it.
            #
            # So settle it here instead. This is the same OrbitResolver
            # call the submit path would have made, with the empty queues
            # it would have carried; `final_orbit` still gates the
            # resolver's SEASON_COMPLETE branch, and the flag is still
            # persisted, so a season saved mid-final-orbit by an older
            # build resolves exactly as before when its seats submit.
            sess.final_orbit = True
            sess.phase = Phase.ORBIT
            sess.pending_policies = {p: None for p in seats}
            sess.pending_orbit_actions = {p: None for p in seats}
            for p in seats:
                sess._remember_entire_visibility(cast_player(p, allowed=seats))
            sess.log_info(
                f"[finalOrbit] {sess.season_name or sess.session_id} — "
                f"final day {cap} resolved; settling automatically."
            )
            # Before the resolver, so the settlement's own log lines land
            # after the night they settle — the order a reader expects.
            sess.last_night_replay = replay
            from sea_of_colours.game.orbit_resolver import OrbitResolver
            OrbitResolver().run(sess, {p: [] for p in seats})
            return
        # v0.8.0 — dawn now opens the ORBIT phase, not PLANNING.
        # Each new game-day starts with the daytime strategic step:
        # build / repair / refine / catapult bid. The night queue
        # opens only after the orbit settlement resolves.
        sess.phase = Phase.ORBIT
        # v1.1 — award the day's stipend at Orbit ENTRY so the credit
        # readout (and the planning-phase budget projection) reflect
        # spendable funds. Idempotent: the OrbitResolver backstop is a
        # no-op once this fires.
        sess.award_orbit_credits()
        # v1.34 — and the teaching subsidy, if this is the day a lesson
        # asks for more blue than the board can have supplied. No-op in
        # every game that is not a tutorial.
        sess.award_tutorial_blue_topup()
        sess.pending_policies = {p: None for p in seats}
        sess.pending_orbit_actions = {p: None for p in seats}
        for p in seats:
            sess._remember_entire_visibility(cast_player(p, allowed=seats))

        sess.log_info(f"[dawnComplete] day {sess.day} opens in ORBIT phase")
        sess.last_night_replay = replay

    def _advance_until_valid(
        self,
        sess: "GameSession",
        owner: "PlayerId",
        queue: List[Move],
        pointers: Dict[str, int],
        replay: List[dict],
        *,
        hour: int = 0,
        disabled_units: Optional[set[str]] = None,
        live_override: Optional[set] = None,
    ) -> bool:
        """Apply the next queued move for ``owner`` (valid OR illegal).

        Returns True when exactly one queue slot was consumed (and a
        replay frame pushed), False if the queue is exhausted. Every
        item burns a slot regardless of outcome:

        - :class:`WasteMove` (parse failure) → ``tag="waste"`` frame, slot consumed.
        - Apply returns ``waste`` (legal shape, illegal at runtime — e.g.
          ``drop`` onto an occupied cell) → ``tag="waste"`` frame, slot
          consumed. The frontend renders both via the same strike-through
          style.
        - EMP-disabled unit → ``tag="empd"`` frame, slot consumed.
        - Successful apply → ``tag`` reflects the action, slot consumed.

        v0.9.9 (RULEBOOK §3.10): the historical "skip invalid, retry"
        loop is gone. The orchestrator now treats each queue row as
        one of the 21 nightly slots — illegal moves no longer hide
        behind successful retries, they're struck through in-place so
        the seat can see exactly what failed.

        ``hour`` is the shared night clock for this round (1..21). The
        frame inherits the same hour stamp regardless of outcome.

        ``disabled_units`` is the set of harvester ids inside an EMP
        cloud at hour-N start (v0.9, RULEBOOK §5). A move that targets
        one of them is short-circuited as ``tag="empd"`` — EXCEPT a
        ``pickup`` (v0.9.13), since the orblift is orbital and an EMP
        only denies the surface, so extraction is always allowed.
        """
        if pointers[owner] >= len(queue):
            return False

        # v0.9.10 — damaged-unit fast-forward (RULEBOOK §3.10).
        #
        # When the next queued move targets a HARVESTER that's already
        # been damaged earlier in the same night (or stayed damaged
        # from yesterday), the orchestrator forbids the action up front
        # AND eagerly consumes every CONSECUTIVE non-pickup move queued
        # for that same unit in one consolidated waste frame. The seat
        # still loses one hour-slot — the rest of the unit's run is
        # advanced as a pointer jump so the timeline doesn't drown in
        # N identical "damaged — awaiting pickup" rejections.
        #
        # The first pickup for the unit is preserved (pickup REPAIRS
        # damage on lift, RULEBOOK §3.6.1), so the agent can still
        # extract the harvester next iteration. Probes / actions on
        # OTHER units later in the queue resolve as normal.
        peek = queue[pointers[owner]]
        peek_unit = getattr(peek, "unit", None)
        if (
            isinstance(peek_unit, str)
            and not isinstance(peek, (PickupMove, WasteMove))
        ):
            peek_ent = sess.entities.get(peek_unit)
            if (
                peek_ent is not None
                and getattr(peek_ent, "entity_type", None) == "harvester"
                and bool(getattr(peek_ent, "damaged", False))
            ):
                run_start = pointers[owner]
                run_end = run_start
                while run_end < len(queue):
                    m = queue[run_end]
                    if isinstance(m, PickupMove):
                        break
                    if getattr(m, "unit", None) != peek_unit:
                        break
                    run_end += 1
                n_skipped = run_end - run_start
                pointers[owner] = run_end
                first_desc = _describe_move(peek)
                if n_skipped == 1:
                    err = (
                        f"{owner}: {first_desc} — {peek_unit} damaged, "
                        f"awaiting pickup"
                    )
                    attempted_str = first_desc
                else:
                    err = (
                        f"{owner}: {n_skipped}× action on {peek_unit} struck "
                        f"— unit damaged, awaiting pickup"
                    )
                    attempted_str = f"{n_skipped}× {first_desc}"
                sess.log_error(self._stamp_hour(hour, err))
                sess.replay_push_scene(
                    replay,
                    err,
                    owner=owner,
                    tag="damaged",
                    attempted=attempted_str,
                    outcome="failed",
                    hour=hour,
                )
                return True

        move = queue[pointers[owner]]
        pointers[owner] += 1

        if isinstance(move, WasteMove):
            err = f"{owner}: invalid policy entry — {move.reason}"
            sess.log_error(self._stamp_hour(hour, err))
            sess._bump_moves_cancelled(owner)
            sess.replay_push_scene(
                replay,
                err,
                owner=owner,
                tag="waste",
                attempted="invalid_policy_entry",
                outcome="failed",
                hour=hour,
            )
            return True

        # v0.9 — EMP-disable: if the move targets a unit currently
        # sitting inside any active EMP cloud, the action is silently
        # smothered for this hour. The slot is consumed so the seat
        # doesn't get to "re-roll" via an empty hour.
        #
        # v0.9.13 — pickup is EXEMPT. The orblift is orbital (§0.4 / §3.5)
        # and an EMP only denies the *surface*; it can't reach up and
        # disable the lifter. So an EMP'd harvester can still be hoisted
        # to the berth — extraction is "always allowed" the same way the
        # damaged-unit fast-forward above preserves the free pickup path.
        # Without this exemption an EMP that lingers over a dropped
        # harvester smothers the seat's trailing pickup every hour, and
        # the unit is destroyed as "abandoned on the surface" at dawn
        # (§3.11.2) despite the seat correctly queueing the recovery.
        unit_id = getattr(move, "unit", None)
        if (
            disabled_units
            and isinstance(unit_id, str)
            and unit_id in disabled_units
            and not isinstance(move, PickupMove)
        ):
            caption = (
                f"{owner}: {unit_id} disabled by EMP cloud @ hour {hour}"
            )
            sess.log_info(self._stamp_hour(hour, caption))
            sess._bump_moves_cancelled(owner)
            sess.replay_push_scene(
                replay,
                caption,
                owner=owner,
                tag="empd",
                attempted=_describe_move(move),
                outcome="failed",
                hour=hour,
            )
            return True

        attempted = _describe_move(move)
        emp_blocked_cells = getattr(sess, "_emp_established_cells_this_hour", None)
        snap_hot_cells = getattr(sess, "_snap_hot_cells_this_hour", None)
        departing_units = getattr(sess, "_departing_units_this_hour", None)
        caption, tag, side = self._apply_one(
            sess, owner, move, hour=hour, live_override=live_override,
            emp_blocked_cells=emp_blocked_cells,
            snap_hot_cells=snap_hot_cells,
            departing_units=departing_units,
        )
        if tag == "waste":
            # v0.9.9 — illegal-at-runtime move (legal shape, but the
            # apply step refused it: drop onto an occupied cell, step
            # off-grid, pickup with empty hold, etc.). The slot is
            # consumed and the frame is struck through; the seat no
            # longer gets a free re-roll.
            err = f"{owner}: {attempted} — {caption}"
            sess.log_error(self._stamp_hour(hour, err))
            sess._bump_moves_cancelled(owner)
            sess.replay_push_scene(
                replay,
                err,
                owner=owner,
                tag="waste",
                attempted=attempted,
                outcome="failed",
                hour=hour,
            )
            return True

        sess.log_info(self._stamp_hour(hour, caption))
        for line in side:
            sess.log_info(self._stamp_hour(hour, line))
        collisions = sess.pending_collision_events
        sess.pending_collision_events = []
        crushed_probes = sess.pending_probe_crush_events
        sess.pending_probe_crush_events = []
        sess.replay_push_scene(
            replay,
            caption,
            owner=owner,
            tag=tag,
            collisions=collisions or None,
            crushed_probes=crushed_probes or None,
            attempted=attempted,
            outcome="ok",
            hour=hour,
        )
        sess._redsign_hour = int(hour)  # type: ignore[attr-defined]
        sess._pulse_probe_cameras()
        return True

    def _pre_hour_phase(
        self,
        sess: "GameSession",
        queue_for: Dict[str, List[Move]],
        pointers: Dict[str, int],
        applied: Dict[str, int],
        replay: List[dict],
        *,
        hour: int,
        chaff_triggerers_by_hour: Dict[int, set[str]],
        chaff_fired_hours: Optional[set[int]] = None,
        seats: Optional[Tuple[str, ...]] = None,
        chaff_active_until: int = 0,
    ) -> set[str]:
        """v0.9 — Run the EMP / chaff pre-emption phase for ``hour``.

        Returns the set of harvester ids that should have their
        hour-``hour`` action smothered as ``tag="empd"`` because they
        sit inside an active EMP cloud cell. Side-effects:

        * Decays existing EMP clouds (RULEBOOK §5).
        * Peeks each seat's next-actionable move. If it's an
          ``EmpLaunchMove`` or ``ChaffFlareMove`` AND the cost check
          passes, the move is APPLIED INLINE: cost debited, pointer
          advanced, slot consumed, replay frame pushed. If the cost
          check fails, the move is left for the main dispatch (which
          will turn it into a normal runtime-waste — pointer
          advances, slot NOT consumed, the seat retries with the
          next move).
        * Chaff triggerers are tracked in
          ``chaff_triggerers_by_hour[hour]`` so the main loop's
          chaff-gate can recognise them as immune.

        EMP effects ALWAYS resolve before regular moves at the same
        hour ("emp_first" — RULEBOOK §5 / user spec). That ordering
        is enforced structurally: clouds tick + launches resolve
        before the disable check runs, so a launch at hour N is in
        effect for the same hour's disable computation.

        v1.19 — chaff outranks the salvo, and outranks itself. Two rules,
        one cause (RULEBOOK §4.9.5 — "EVERY seat's action in each covered
        hour is cancelled"):

        * A flare fired at ``hour`` cancels a **same-hour** EMP launch,
          so this method resolves flares first and only then salvos.
        * ``chaff_active_until`` is the last hour covered by a window
          opened on an EARLIER hour. Inside such an hour nothing
          pre-empts at all.

        Either way the cancelled launch burns its slot (``tag="chaffed"``,
        applied by the main dispatch) but **keeps its munition in stock**
        for a later hour or night — a jammed house never got to fire, so
        it never spent the round.

        Resolving both launch types in one seat-ordered pass is what let
        chaff be chained into a lock (each flare re-armed the window and
        re-granted its launcher immunity) and let a salvo fly out of a
        fully jammed house — whichever seat the loop happened to reach
        first simply won.
        """
        from sea_of_colours.game.session import cast_player

        seat_list: Tuple[str, ...] = seats if seats is not None else tuple(sess.players)

        # 1. Tick existing clouds first. Clouds launched THIS hour
        #    won't be ticked yet (they enter the list below with
        #    ``hours_remaining`` snapshotted from the constants).
        sess.tick_emp_clouds()
        # v1.36 — SNAP clouds age on the same beat. They live one hour,
        # so this is what clears last hour's scorch mark, and it must
        # happen before this hour's own SNAPs land below.
        sess.tick_snap_clouds()
        # …and the hot-cell stamp is per-hour by definition: a square
        # SNAPped at hour 3 is safe to walk onto at hour 4.
        sess._snap_hot_cells_this_hour = {}  # type: ignore[attr-defined]

        # v1.10 — snapshot the "established" cloud footprint: clouds
        # that survived the decay above, i.e. were already active
        # BEFORE this hour's own launches (resolved next, step 2). A
        # drop/step landing on one of these cells this hour does NOT
        # auto-harvest (RULEBOOK §4.9.3) — the cloud was already known
        # / avoidable going into the hour. A cloud freshly spawned by
        # THIS hour's launch is deliberately excluded from this set so
        # a same-hour "missile lands the same hour a harvester does"
        # coincidence still harvests once before the unit goes empd
        # from the following hour on.
        established_cloud_cells = sess.cells_in_any_emp_cloud()

        # 2. Peek + pre-empt chaff / EMP for each seat.
        #
        #    v1.19 — chaff now goes FIRST and can veto this hour's EMP
        #    launches. Weapons used to be the one action class chaff could
        #    not touch, because both launch types were pre-empted in a
        #    single seat-ordered pass: whoever the loop reached first
        #    simply flew. That contradicted §4.9.5 ("EVERY seat's action
        #    in each covered hour is cancelled") and made a salvo the
        #    reliable counter to a flare.
        preempted: set[str] = set()
        already_jammed = int(hour) <= int(chaff_active_until)

        # 2a. Flares. Skipped wholesale inside a window opened on an
        #     earlier hour — nothing acts during chaff, so the flare is
        #     never spent and stays in stock for later.
        for p in (() if already_jammed else seat_list):
            if applied[p] >= MAX_MOVES:
                continue
            move, idx = _next_actionable(queue_for[p], pointers[p])
            if not isinstance(move, ChaffFlareMove):
                continue
            owner_pid = cast_player(p, allowed=seat_list)
            ok, msg = sess.apply_chaff_flare(owner_pid, hour=hour)
            if not ok:
                continue
            self._consume_preempt_slot(
                sess, p, queue_for[p], pointers, idx, hour, replay,
            )
            applied[p] += 1
            preempted.add(p)
            # The launcher is "immune" only for the LAUNCH hour — it
            # spent that slot firing the flare, so it isn't also
            # cancelled this same hour. It is NOT propagated forward:
            # the flare jams its OWN house too for the carry-over
            # hours (CHAFF_DURATION_HOURS - 1), so firing chaff costs
            # the launcher the full CHAFF_DURATION_HOURS-turn window
            # (launch + self-jam), not just the launch slot. (Was: the
            # triggerer used to be propagated forward and stay immune.)
            chaff_triggerers_by_hour.setdefault(hour, set()).add(p)
            if chaff_fired_hours is not None:
                chaff_fired_hours.add(hour)
            # v1.8 — canonical combat feed: record the public flare so
            # agents see WHO jammed and for WHICH hours (chaff is a
            # temporal, location-less effect — no map scar).
            from sea_of_colours.game.weapons import CHAFF_DURATION_HOURS
            sess.record_chaff_flare(str(p), int(hour), int(CHAFF_DURATION_HOURS))
            chaff_events = sess.pending_chaff_events
            sess.pending_chaff_events = []
            sess.log_info(self._stamp_hour(hour, msg))
            sess.replay_push_scene(
                replay,
                msg,
                owner=p,
                tag="chaff_flare",
                attempted=_describe_move(move),
                outcome="ok",
                hour=hour,
                chaff=chaff_events or None,
            )

        # A flare fired in 2a jams this same hour, so everything below is
        # cancelled by the main dispatch with its charge intact. Computed
        # once here because both remaining launch stages need it.
        jammed_now = already_jammed or bool(chaff_triggerers_by_hour.get(hour))

        # 2b. SNAP — the fast weapon, and the reason this function's
        #     ordering is what it is (v1.36, §4.9.4). It resolves ABOVE the
        #     hour-start vision snapshot below, which is the whole weapon:
        #     killing a probe here means the drop that beacon was going to
        #     validate never sees it, so a SNAP denies a smash-and-grab in
        #     the same hour it flies. Every other effect in the engine is
        #     judged against the snapshot and therefore cannot do that.
        #
        #     It outruns a flare fired on this same hour (v1.57, CHG-1a)
        #     — hence ``already_jammed`` here and not ``jammed_now``,
        #     which is the entire rule change. A round is faster than a
        #     flare going up, so being flared AT does not stop it; being
        #     flared EARLIER does, because by then the jam is standing
        #     and there is nothing left to outrun. That is the same
        #     distinction stage 2a already draws for the flares
        #     themselves, and it reuses the same variable.
        self._snap_preempt_phase(
            sess,
            queue_for,
            pointers,
            applied,
            replay,
            hour=hour,
            seat_list=seat_list,
            preempted=preempted,
            already_jammed=already_jammed,
        )

        # 2c. The hour-start LIVE snapshot for live-only drops (v1.28).
        #
        #     Position is load-bearing in BOTH directions and neither is
        #     an accident.
        #
        #     ABOVE the salvos, which is what v1.28 fixed: a salvo kills
        #     probes, so taking the snapshot after one let a rival's
        #     missile fry a beacon and only then record what that seat
        #     could "see at hour start". That blanks a landing §3.9.7
        #     explicitly protects — "a beacon a rival destroys,
        #     supersedes, or EMPs LATER in the same hour still validates
        #     that hour's landing". Seen in the wild: Terra_Kestrel day 6
        #     hour 1.
        #
        #     BELOW the SNAP stage, which is v1.36 and is the opposite
        #     ruling for the opposite weapon: a SNAP is meant to take the
        #     eye out before the landing is judged. Two weapons, one
        #     snapshot, and which side of it you sit on IS the difference
        #     between them.
        #
        #     Moving it below chaff cost nothing — a flare cancels
        #     actions, it does not change what anybody can see.
        live_snapshot: Dict[str, set] = {}
        if live_only_drops():
            live_snapshot = {
                p: sess.tiles_visible_now(cast_player(p)) for p in seat_list
            }

        # 2d. Salvos — only on an hour no flare covers. Note the asymmetry
        #     is deliberate and is the point of the weapon: two flares on
        #     one hour both fly (neither is inside a window yet, so one is
        #     wasted), but a flare beats a salvo declared for the same hour.
        for p in (() if jammed_now else seat_list):
            if applied[p] >= MAX_MOVES or p in preempted:
                continue
            move, idx = _next_actionable(queue_for[p], pointers[p])
            if not isinstance(move, EmpLaunchMove):
                continue
            owner_pid = cast_player(p, allowed=seat_list)
            ok, msg = sess.apply_emp_launch(
                owner_pid, move.at[0], move.at[1], hour=hour,
                extra_targets=list(move.extra_ats),
            )
            if not ok:
                # Cost check failed → leave it for main dispatch
                # to surface as a runtime waste.
                continue
            self._consume_preempt_slot(
                sess, p, queue_for[p], pointers, idx, hour, replay,
            )
            applied[p] += 1
            preempted.add(p)
            emp_events = sess.pending_emp_events
            sess.pending_emp_events = []
            sess.log_info(self._stamp_hour(hour, msg))
            sess.replay_push_scene(
                replay,
                msg,
                owner=p,
                tag="emp_launch",
                attempted=_describe_move(move),
                outcome="ok",
                hour=hour,
                emp=emp_events or None,
            )

        # 3. Build the disabled-units set AFTER launches resolved.
        cloud_cells = sess.cells_in_any_emp_cloud()
        disabled: set[str] = set()
        if cloud_cells:
            for ent in sess.entities.values():
                if ent.entity_type != "harvester":
                    continue
                if ent.x is None or ent.y is None:
                    continue
                if (int(ent.x), int(ent.y)) in cloud_cells:
                    disabled.add(ent.id)
                    # v1.6 kill-feed: credit every cloud owner catching this
                    # harvester (distinct per attacker/unit for the season).
                    _atks = sess.emp_cloud_owners_at(int(ent.x), int(ent.y))
                    for atk in _atks:
                        sess.note_emp_catch(str(ent.id), str(ent.owner), atk)
                    # v1.8 — canonical combat feed: record this smothered
                    # HOUR per unit so the victim's last_night recap can
                    # show exactly which hours it lost (and to whom).
                    _atk0 = next(iter(_atks)) if _atks else ""
                    sess.note_emp_hit(
                        str(ent.id), str(ent.owner), str(_atk0), int(hour),
                    )

        # 4. Stash preempted seats — and the established-cloud snapshot
        #    from step 1 — on transient attrs so the main loop's seat
        #    iteration (and the harvest-gate in ``_apply_one``) can read
        #    them without changing every call signature in between.
        #    Both are cleared at end of night.
        sess._preempted_seats_this_hour = preempted  # type: ignore[attr-defined]
        sess._emp_established_cells_this_hour = established_cloud_cells  # type: ignore[attr-defined]
        sess._live_snapshot_this_hour = live_snapshot  # type: ignore[attr-defined]

        return disabled

    def _snap_preempt_phase(
        self,
        sess: "GameSession",
        queue_for: Dict[str, List[Move]],
        pointers: Dict[str, int],
        applied: Dict[str, int],
        replay: List[dict],
        *,
        hour: int,
        seat_list: Tuple[str, ...],
        preempted: set[str],
        already_jammed: bool,
    ) -> None:
        """Fire this hour's SNAPs, before anybody's vision is recorded.

        v1.36, RULEBOOK §4.9.4. Split out of :meth:`_pre_hour_phase`
        rather than inlined, and that is deliberate: SNAP is the newest
        weapon and the one most likely to be withdrawn. Retiring it
        should be deleting a call and a method, not unpicking a night
        loop — see ``docs/ADDING_A_WEAPON.md``.

        Returns nothing. The damage it does lands on the session (probes
        destroyed, harvesters damaged, ``snap_clouds`` extended) and the
        hot cells it stamps are read by ``_apply_one`` later in the hour.

        **A flare fired on this hour does not stop it (v1.57, §4.9.4).**
        SNAP is the fast weapon and it outruns a flare going up; what it
        cannot outrun is a window already standing, so the gate is
        ``already_jammed`` rather than ``jammed_now``. Pricing falls out
        of that and needed no code of its own: a round that fires spends
        itself here, and one that does not never reaches
        ``apply_snap_launch`` — it drops through to the main dispatch,
        is tagged ``chaffed`` like any other jammed action, and keeps
        its charge.
        """
        from sea_of_colours.game.session import cast_player

        for p in (() if already_jammed else seat_list):
            if applied[p] >= MAX_MOVES or p in preempted:
                continue
            move, idx = _next_actionable(queue_for[p], pointers[p])
            if not isinstance(move, SnapLaunchMove):
                continue
            owner_pid = cast_player(p, allowed=seat_list)
            ok, msg = sess.apply_snap_launch(
                owner_pid, move.at[0], move.at[1], hour=hour,
            )
            if not ok:
                # Cost check failed → leave it for the main dispatch to
                # surface as a runtime waste, exactly like a dry salvo.
                continue
            self._consume_preempt_slot(
                sess, p, queue_for[p], pointers, idx, hour, replay,
            )
            applied[p] += 1
            preempted.add(p)
            snap_events = sess.pending_snap_events
            sess.pending_snap_events = []
            sess.log_info(self._stamp_hour(hour, msg))
            sess.replay_push_scene(
                replay,
                msg,
                owner=p,
                tag="snap_launch",
                attempted=_describe_move(move),
                outcome="ok",
                hour=hour,
                snap=snap_events or None,
            )

    def _consume_preempt_slot(
        self,
        sess: "GameSession",
        seat: str,
        queue: List[Move],
        pointers: Dict[str, int],
        move_idx: int,
        hour: int,
        replay: List[dict],
    ) -> None:
        """Advance the seat's pointer past the pre-empted move.

        v0.9.9 — :func:`_next_actionable` no longer skips wastes, so
        ``move_idx`` is always equal to ``pointers[seat]`` and the
        helper simply bumps the pointer by one. (Kept as a method so
        the call sites in :meth:`_pre_hour_phase` stay readable.)
        """
        pointers[seat] = move_idx + 1

    def _cancel_next_actionable(
        self,
        sess: "GameSession",
        owner: "PlayerId",
        queue: List[Move],
        pointers: Dict[str, int],
        replay: List[dict],
        *,
        hour: int,
        tag: str,
        caption: str,
    ) -> bool:
        """Cancel the seat's next queued move with ``tag`` + caption.

        v0.9.9 — every row burns a slot, including invalid ones, so
        the helper no longer skips :class:`WasteMove` markers when
        the seat is chaffed. If the next row is waste it gets
        rendered as ``tag="waste"`` (strikeout); otherwise it gets
        rendered with the caller's ``tag`` (e.g. ``chaffed``).
        Returns True iff a slot was consumed.
        """
        idx = pointers[owner]
        if idx >= len(queue):
            return False
        move = queue[idx]
        pointers[owner] = idx + 1
        if isinstance(move, WasteMove):
            err = f"{owner}: invalid policy entry — {move.reason}"
            sess.log_error(self._stamp_hour(hour, err))
            sess.replay_push_scene(
                replay,
                err,
                owner=owner,
                tag="waste",
                attempted="invalid_policy_entry",
                outcome="failed",
                hour=hour,
            )
            return True
        sess.log_info(self._stamp_hour(hour, caption))
        sess.replay_push_scene(
            replay,
            caption,
            owner=owner,
            tag=tag,
            attempted=_describe_move(move),
            outcome="failed",
            hour=hour,
        )
        return True

    def _chaff_until_for(
        self, sess: "GameSession", hour: int,
    ) -> int:
        """Last hour the chaff fired at ``hour`` is still in effect."""
        from sea_of_colours.game.weapons import CHAFF_DURATION_HOURS
        return int(hour) + int(CHAFF_DURATION_HOURS) - 1

    def _active_chaffers(
        self,
        sess: "GameSession",
        chaff_triggerers_by_hour: Dict[int, set[str]],
        hour: int,
    ) -> set[str]:
        """v1.6 — seats whose chaff flare is in effect at ``hour`` (the
        houses to blame for a jam this hour). A flare fired at hour ``h``
        covers ``h .. _chaff_until_for(h)``."""
        out: set[str] = set()
        for fire_hour, firers in (chaff_triggerers_by_hour or {}).items():
            if int(fire_hour) <= int(hour) <= self._chaff_until_for(
                sess, int(fire_hour)
            ):
                out |= set(firers or ())
        return out

    @staticmethod
    def _stamp_hour(hour: int, text: str) -> str:
        """Prepend a planetary-night hour tag (``[H03]``) to a log line.

        Hour 0 is reserved for pre/post-night events (opening + dawn);
        we leave those untagged so the watcher's log reads
        ``[praxis] day 3 — night begins`` instead of
        ``[H00] [praxis] day 3 — night begins``.
        """
        if not hour or hour < 1:
            return text
        return f"[H{hour:02d}] {text}"

    def _build_scheduled_orders(
        self, queue_for: Dict[str, List[Move]]
    ) -> Dict[str, List[Dict[str, object]]]:
        """Flatten both seats' submitted policies for the ORDERS drawer.

        The output shape is consumed by the v0.7.3 frontend ORDERS
        drawer; each entry carries enough metadata for the watcher to
        render the action verbatim (e.g. ``"step harvester_p1 →
        (11,10)"``) without re-parsing the raw policy JSON. The
        ``status`` field is always ``"pending"`` at emit time; the
        client mutates it to ``"ok"`` / ``"failed"`` as the scrubber
        advances past matching frames.
        """
        def _entry(idx: int, m: Move) -> Dict[str, object]:
            row: Dict[str, object] = {
                "idx": idx,
                "label": _describe_move(m),
                "status": "pending",
            }
            if isinstance(m, ProbeMove):
                row["action"] = "probe"
                row["target"] = [int(m.at[0]), int(m.at[1])]
            elif isinstance(m, DropMove):
                row["action"] = "drop"
                row["unit"] = m.unit
                row["target"] = [int(m.at[0]), int(m.at[1])]
            elif isinstance(m, StepMove):
                row["action"] = "step"
                row["unit"] = m.unit
                row["target"] = [int(m.to[0]), int(m.to[1])]
            elif isinstance(m, PickupMove):
                row["action"] = "pickup"
                row["unit"] = m.unit
            elif isinstance(m, WasteMove):
                row["action"] = "invalid"
                row["reason"] = getattr(m, "reason", "")
            else:
                row["action"] = "unknown"
            return row

        return {
            p: [_entry(i, m) for i, m in enumerate(queue_for.get(p, []))]
            for p in queue_for.keys()
        }

    def _departing_harvesters(
        self,
        sess: "GameSession",
        seats: Tuple[str, ...],
        queue_for: Dict[str, List[Move]],
        pointers: Dict[str, int],
        applied: Dict[str, int],
        *,
        preempted: AbstractSet[str],
        chaff_active_until: int,
        chaff_triggerers: AbstractSet[str],
        current_hour: int,
        disabled_units: Optional[AbstractSet[str]] = None,
        spent_seats: Optional[AbstractSet[str]] = None,
    ) -> Set[str]:
        """Harvesters that will vacate their cell during ``current_hour``.

        v1.48 (RULEBOOK §3.17) — a harvester being lifted is *departing*,
        and §3.17 collides two harvesters **arriving** on one cell. A
        lander therefore takes a cell its rival is lifting off without a
        collision, which is what the rulebook has always said and what
        the shipped agent has always been told (V12's SEEN_GRAB doctrine:
        "drop on it, auto-harvest, and lift").

        Decided at hour start, before any seat acts, because the bug this
        closes was that the answer depended on whether the engine's seat
        loop reached the lifter or the lander first — an implementation
        detail with no rules standing (§3.10, §3.13). See
        docs/OUTSTANDING_ISSUES.md #56.

        Deciding it up front is only sound because **every way the move
        can fail is knowable now**. For a pickup they are all static —
        no such harvester, lifter not in orbit, harvester already
        orbital — and none of them depend on what another seat does.

        v1.49 (§3.17.7) — **step-aways are in here too**, which is what
        closes the hand-off gap. It looks like it should not be possible:
        a step CAN be refused by something a rival does, namely a
        collision at its own destination. But that is the only such
        reason, and it is the one this method computes. Reading
        ``try_step_unit`` from the top, every other refusal is static —
        wrong owner, not on the surface, already damaged, not adjacent,
        out of bounds, hold full — and the two dynamic gates above it in
        the simulator, chaff and an EMP-smothered unit, are both settled
        before the round and passed in. Everything *after* the collision
        check moves the harvester unconditionally: a snap-hot cell
        cripples it where it lands and an EMP cloud denies it the
        harvest, but neither rewinds the step, so the origin is vacated
        either way.

        So the question "will this cell be free?" becomes a fixpoint
        over the step graph rather than a race:

        - A step whose destination is empty goes.
        - A step whose destination is being vacated by a step that goes,
          goes — which resolves a convoy of any length.
        - A step blocked by someone who stays is refused, and that
          refusal propagates back down the chain behind it.
        - What survives with nobody stationary to blame is a **cycle**,
          and a cycle is allowed: everyone in it is leaving. Note the
          grid is bipartite, so the shortest cycle is four harvesters;
          the two-unit case is a pass-through swap and §3.17.4 collides
          it, which the swap pre-pass has already settled before we get
          here.
        - Two or more steps into ONE cell collide (§3.17.6) and nobody
          vacates, so they are excluded before the fixpoint runs.
        """
        from sea_of_colours.game.session import cast_player

        smothered = set(disabled_units or ())
        spent = set(spent_seats or ())

        def _seat_acts_this_hour(p: str) -> bool:
            if applied[p] >= MAX_MOVES:
                return False
            if p in preempted or p in spent:
                # Already acted this hour; the next queued move belongs
                # to a LATER hour.
                return False
            if current_hour <= chaff_active_until and p not in chaff_triggerers:
                # Chaff cancels this seat's slot, so nothing happens.
                return False
            return True

        leaving: Set[str] = set()
        for p in seats:
            if not _seat_acts_this_hour(p):
                continue
            move, _ = _next_actionable(queue_for[p], pointers[p])
            if not isinstance(move, PickupMove):
                continue
            # Mirror try_pickup_unit's preconditions exactly.
            hh = sess.entities.get(move.unit)
            if hh is None or hh.entity_type != "harvester":
                continue
            if hh.owner != p:
                continue
            if hh.x is None:
                continue
            lf = sess.lifter_for(cast_player(p))
            if lf is None or lf.x is not None:
                continue
            leaving.add(hh.id)

        leaving |= self._steps_that_will_vacate(
            sess, seats, queue_for, pointers,
            acts=_seat_acts_this_hour,
            smothered=smothered,
            lifting=leaving,
        )
        return leaving

    @staticmethod
    def _steps_that_will_vacate(
        sess: "GameSession",
        seats: Tuple[str, ...],
        queue_for: Dict[str, List[Move]],
        pointers: Dict[str, int],
        *,
        acts,
        smothered: AbstractSet[str],
        lifting: AbstractSet[str],
    ) -> Set[str]:
        """The fixpoint described in :meth:`_departing_harvesters`."""
        from sea_of_colours.game.session import HARVESTER_HOLD_CAPACITY, _adj

        # harvester id -> destination cell
        wants: Dict[str, Tuple[int, int]] = {}
        for p in seats:
            if not acts(p):
                continue
            move, _ = _next_actionable(queue_for[p], pointers[p])
            if not isinstance(move, StepMove):
                continue
            h = sess.entities.get(move.unit)
            if not h or h.entity_type != "harvester" or h.owner != p:
                continue
            if h.x is None or h.y is None:
                continue
            if bool(getattr(h, "damaged", False)):
                continue
            if h.id in smothered:
                continue
            if len(h.cargo_squares) >= HARVESTER_HOLD_CAPACITY:
                continue
            tx, ty = int(move.to[0]), int(move.to[1])
            if not (0 <= tx < sess.width and 0 <= ty < sess.height):
                continue
            if not _adj((h.x, h.y), (tx, ty)):
                continue
            wants[h.id] = (tx, ty)

        # Two or more arrivals on one cell is a collision (§3.17.6):
        # nobody gets there, so nobody leaves where they were.
        crowded = {
            dest for dest in wants.values()
            if sum(1 for d in wants.values() if d == dest) > 1
        }
        movers = {hid for hid, dest in wants.items() if dest not in crowded}

        # Who is standing where. Wrecks are skipped: a damaged harvester
        # does not block (§3.17.1), so it cannot refuse anyone's step.
        occupant: Dict[Tuple[int, int], str] = {}
        for e in sess.entities.values():
            if e.entity_type != "harvester" or e.x is None or e.y is None:
                continue
            if bool(getattr(e, "damaged", False)):
                continue
            occupant[(int(e.x), int(e.y))] = str(e.id)

        # Propagate refusal backwards: blocked by someone staying, or by
        # someone who is themselves blocked. Whatever is left when this
        # settles is either walking into free space or going round in a
        # ring, and both of those vacate.
        blocked: Set[str] = set()
        while True:
            newly = {
                hid for hid in movers - blocked
                if (lambda occ: (
                    occ is not None
                    and occ != hid
                    and occ not in lifting
                    and (occ not in movers or occ in blocked)
                ))(occupant.get(wants[hid]))
            }
            if not newly:
                break
            blocked |= newly

        return movers - blocked

    def _maybe_resolve_swap_collision(
        self,
        sess: "GameSession",
        queue_for: Dict[str, List[Move]],
        pointers: Dict[str, int],
        applied: Dict[str, int],
        replay: List[dict],
        *,
        hour: int = 0,
        seats: Optional[Tuple[str, ...]] = None,
        skip_seats: Optional[AbstractSet[str]] = None,
    ) -> Set[str]:
        """Detect & resolve pass-through swaps before the round.

        Returns the seats whose slot was spent here (empty if none) —
        truthy exactly when something was resolved, so callers can
        still read it as a flag.

        v0.9.6 — generalised to walk every (a, b) seat pair in
        :attr:`GameSession.players`. A swap is still pair-wise
        (RULEBOOK §3.6) — only two harvesters cross at a time — but
        with 3-4 seats two independent pairs can swap on the same hour.

        v1.48 — and both pairs now resolve HERE, on this hour. This
        used to stop at the first pair and lean on the outer round loop
        to catch the second one, which quietly cost the second pair an
        hour: the loop derives its clock from ``max(applied) + 1``, and
        the first pair had just bumped ``applied``. Both pairs wrecked
        either way, so the board was right and only the replay lied —
        but it lied about WHO, because the pair on the lower seats
        always got the earlier stamp, and §3.13 gives seat index no say
        in anything. (The per-seat re-stamp downstream cannot repair
        it: a joint collision frame is owned by nobody, and that pass
        skips frames with no owner.)

        v1.19 — ``skip_seats`` excludes seats that already spent this
        hour's slot in the pre-hour phase. Their next queued move
        belongs to a LATER hour, so pairing it with a rival's move for
        *this* hour both grants a second action and stages a collision
        between two moves that were never simultaneous.
        """
        from sea_of_colours.game.session import cast_player

        seat_list: Tuple[str, ...] = seats if seats is not None else tuple(sess.players)
        spent = set(skip_seats or ())
        resolved: Set[str] = set()
        # Rescan after each pair: a seat that has just crossed is added
        # to ``spent``, so it cannot be paired again on the same hour.
        while True:
            pair_found = False
            for ai in range(len(seat_list)):
                for bi in range(ai + 1, len(seat_list)):
                    pa, pb = seat_list[ai], seat_list[bi]
                    if pa in spent or pb in spent:
                        continue
                    a_move, _ = _next_actionable(queue_for[pa], pointers[pa])
                    b_move, _ = _next_actionable(queue_for[pb], pointers[pb])
                    if not (isinstance(a_move, StepMove) and isinstance(b_move, StepMove)):
                        continue
                    if applied[pa] >= MAX_MOVES or applied[pb] >= MAX_MOVES:
                        continue
                    ha = sess.entities.get(a_move.unit)
                    hb = sess.entities.get(b_move.unit)
                    if not (ha and hb and ha.entity_type == "harvester" and hb.entity_type == "harvester"):
                        continue
                    if ha.x is None or ha.y is None or hb.x is None or hb.y is None:
                        continue
                    if bool(getattr(ha, "damaged", False)) or bool(getattr(hb, "damaged", False)):
                        continue
                    ta = (int(a_move.to[0]), int(a_move.to[1]))
                    tb = (int(b_move.to[0]), int(b_move.to[1]))
                    if (ha.x, ha.y) != tb or (hb.x, hb.y) != ta:
                        continue
                    W, H = sess.width, sess.height
                    if not (0 <= ta[0] < W and 0 <= ta[1] < H and 0 <= tb[0] < W and 0 <= tb[1] < H):
                        continue

                    ok, caption = sess.try_swap_collision(
                        cast_player(pa, allowed=seat_list), ha.id, ta,
                        cast_player(pb, allowed=seat_list), hb.id, tb,
                    )
                    if not ok:
                        continue
                    # v0.9.9 — _next_actionable no longer skips wastes, so
                    # each seat's pointer sits directly on the consumed
                    # StepMove. Advance by one and burn the slot.
                    for p in (pa, pb):
                        pointers[p] += 1
                        applied[p] += 1
                        spent.add(p)
                        resolved.add(p)
                    sess.log_info(self._stamp_hour(hour, caption))
                    collisions = sess.pending_collision_events
                    sess.pending_collision_events = []
                    sess.replay_push_scene(
                        replay,
                        caption,
                        owner=None,
                        tag="collision_swap",
                        collisions=collisions or None,
                        hour=hour,
                        attempted=f"swap {ha.id} ↔ {hb.id}",
                        outcome="ok",
                    )
                    sess._redsign_hour = int(hour)  # type: ignore[attr-defined]
                    sess._pulse_probe_cameras()
                    pair_found = True
                    break
                if pair_found:
                    break
            if not pair_found:
                return resolved

    def _maybe_resolve_simultaneous_drops(
        self,
        sess: "GameSession",
        queue_for: Dict[str, List[Move]],
        pointers: Dict[str, int],
        applied: Dict[str, int],
        replay: List[dict],
        *,
        hour: int = 0,
        seats: Optional[Tuple[str, ...]] = None,
        skip_seats: Optional[AbstractSet[str]] = None,
    ) -> Set[str]:
        """Detect & resolve simultaneous drop collisions before the round.

        Returns the seats whose slot was spent here (empty if none) —
        truthy exactly when something was resolved, so callers can
        still read it as a flag.

        v0.9.10 — when multiple harvesters are trying to drop on the
        same square during the same hour, none land, all become damaged
        and stay in orbit. This is checked pre-hour (like swap collision)
        so we can handle all involved seats in one go.

        v1.48 — *every* contested square on this hour is settled here,
        where it used to stop after the first and leave the rest to the
        next turn of the round loop. Same defect as the swap pre-pass
        and the same cost: the clock is ``max(applied) + 1``, so the
        second pile-up was reported an hour late, and which one came
        second was decided by seat index. The groups are keyed by
        target square and a seat can only be dropping on one of them,
        so they are disjoint by construction — no rescan needed, just
        do not leave early.

        v1.19 — ``skip_seats`` excludes seats that already spent this
        hour's slot in the pre-hour phase; see
        :meth:`_maybe_resolve_swap_collision` for why counting them here
        both double-acts the seat and invents a collision between moves
        from two different hours.
        """
        from sea_of_colours.game.session import cast_player

        seat_list: Tuple[str, ...] = seats if seats is not None else tuple(sess.players)
        spent = set(skip_seats or ())

        resolved: Set[str] = set()

        # Build a map of target squares -> list of (seat, move, harvester) tuples
        drops_by_target: Dict[Tuple[int, int], List[Tuple[str, DropMove, object]]] = {}

        for p in seat_list:
            if applied[p] >= MAX_MOVES:
                continue
            if p in spent:
                continue
            move, _ = _next_actionable(queue_for[p], pointers[p])
            if not isinstance(move, DropMove):
                continue
            
            harvester = sess.entities.get(move.unit)
            if not harvester or harvester.entity_type != "harvester":
                continue
            if harvester.x is not None:  # Already on surface
                continue
            if bool(getattr(harvester, "damaged", False)):  # Already damaged
                continue
            
            target = (int(move.at[0]), int(move.at[1]))
            drops_by_target.setdefault(target, []).append((p, move, harvester))
        
        # Find any target with multiple simultaneous drops
        for target, drops in drops_by_target.items():
            if len(drops) < 2:
                continue
            
            # Multiple drops on same square! Damage all harvesters, none land.
            x, y = target
            involved_owners = []
            involved_harvesters = []
            total_spilled = 0
            
            for seat, move, harvester in drops:
                # Damage the harvester but keep it in orbit
                spilled = sess._damage_harvester(harvester)
                total_spilled += spilled
                involved_owners.append(harvester.owner)
                involved_harvesters.append(harvester.id)
                
                # Advance pointer and consume slot for this seat
                pointers[seat] += 1
                applied[seat] += 1
                resolved.add(seat)
            
            # Record collision event
            sess._record_collision(
                x, y, involved_owners,
                event_type="simultaneous_drops",
                harvesters=involved_harvesters,
            )
            # v1.6 kill-feed: every participating house damaged every other
            # participant's unit in the pile-up (one unit per seat here).
            _distinct = [
                o for i, o in enumerate(involved_owners)
                if o and o not in involved_owners[:i]
            ]
            for _atk in _distinct:
                for _vic in _distinct:
                    if _atk != _vic:
                        sess._attrib("harv_damaged", str(_atk), str(_vic))
            
            owners_label = "+".join(sorted(set(involved_owners)))
            harv_label = ", ".join(involved_harvesters)
            caption = (
                f"SIMULTANEOUS DROP COLLISION at ({x},{y}) — "
                f"({owners_label}) {harv_label} all stay orbital damaged, "
                f"{total_spilled} cargo square(s) lost"
            )
            
            sess.log_info(self._stamp_hour(hour, caption))
            collisions = sess.pending_collision_events
            sess.pending_collision_events = []
            sess.replay_push_scene(
                replay,
                caption,
                owner=None,
                tag="collision_simultaneous_drops",
                collisions=collisions or None,
                hour=hour,
                attempted=f"simultaneous drops: {harv_label}",
                outcome="collision",
            )
            sess._redsign_hour = int(hour)  # type: ignore[attr-defined]
            sess._pulse_probe_cameras()

        return resolved

    def _maybe_resolve_converging_steps(
        self,
        sess: "GameSession",
        queue_for: Dict[str, List[Move]],
        pointers: Dict[str, int],
        applied: Dict[str, int],
        replay: List[dict],
        *,
        hour: int = 0,
        seats: Optional[Tuple[str, ...]] = None,
        skip_seats: Optional[AbstractSet[str]] = None,
        disabled_units: Optional[AbstractSet[str]] = None,
        departing_units: Optional[AbstractSet[str]] = None,
    ) -> Set[str]:
        """Two or more harvesters stepping into ONE EMPTY cell (§3.17).

        v1.49 (§3.17.6). §3.17's governing sentence collides two harvesters
        *arriving* on the same cell, and this is the plainest possible
        case of it — but it was not one of the four illustrated
        patterns, so it fell through to the ordinary seat loop. There
        the first seat walked completed its step and **took the cell**,
        and only the second was turned back. Both wrecked either way,
        so the cost was not who survived but where: the winner's wreck
        sat on the contested cell, the scars followed it, and which
        harvester won was decided by seat index (§3.13 says it has no
        say in anything).

        Settled the way §3.17.3 and §3.17.4 settle the other two step
        patterns: nobody moves, everybody wrecks **at their original
        positions**. The scar goes on the contested cell, following
        §3.17.2 — the other case where a destination is fought over and
        no one arrives.

        Deciding it before the round is sound for the same reason the
        egress snapshot is: every remaining way ``try_step_unit``
        refuses a move is a **static precondition** — not on the
        surface, already damaged, not adjacent, out of bounds, hold
        full — so no rival can falsify one of them mid-hour and leave
        two harvesters wrecked over a step that was never going to
        happen. The two that are NOT static are excluded up front:
        chaff (the caller only runs the pre-passes on an unjammed hour)
        and an EMP-smothered unit, which is why ``disabled_units`` is
        passed in.

        A healthy harvester already standing on the contested cell is
        rammed too and wrecks in place (§3.17.3), without spending a
        slot — it is not acting, it is being run into. That case had the
        same disease one cell over: the occupant was wrecked by whichever
        stepper the seat loop reached first, and the SECOND stepper then
        strolled on unharmed, because by then the occupant was a wreck
        and wrecks do not block (§3.17.1).

        v1.49 — an occupant that is *leaving* this hour is not an
        occupant. ``departing_units`` is the egress fixpoint, so a cell
        being vacated counts as empty and the arrivals collide with each
        other over it, not with the harvester on its way out.
        """
        from sea_of_colours.game.session import HARVESTER_HOLD_CAPACITY, _adj

        seat_list: Tuple[str, ...] = seats if seats is not None else tuple(sess.players)
        spent = set(skip_seats or ())
        smothered = set(disabled_units or ())
        departing = set(departing_units or ())
        resolved: Set[str] = set()

        steps_by_target: Dict[Tuple[int, int], List[Tuple[str, object]]] = {}
        for p in seat_list:
            if p in spent or applied[p] >= MAX_MOVES:
                continue
            move, _ = _next_actionable(queue_for[p], pointers[p])
            if not isinstance(move, StepMove):
                continue
            h = sess.entities.get(move.unit)
            if not h or h.entity_type != "harvester" or h.owner != p:
                continue
            if h.x is None or h.y is None:
                continue
            if bool(getattr(h, "damaged", False)):
                continue
            if h.id in smothered:
                continue
            if len(h.cargo_squares) >= HARVESTER_HOLD_CAPACITY:
                continue
            tx, ty = int(move.to[0]), int(move.to[1])
            if not (0 <= tx < sess.width and 0 <= ty < sess.height):
                continue
            if not _adj((h.x, h.y), (tx, ty)):
                continue
            steps_by_target.setdefault((tx, ty), []).append((p, h))

        for (tx, ty), movers in steps_by_target.items():
            if len(movers) < 2:
                continue

            # Anyone healthy still standing there wrecks in place with
            # them (§3.17.3), and does NOT spend a slot — it is not
            # acting, it is being run into. Without this the occupant was
            # rammed by whichever stepper the seat loop reached first,
            # and the SECOND one strolled onto the cell unharmed, because
            # by then the occupant was a wreck and wrecks do not block.
            #
            # ``departing`` excludes a harvester that is leaving this
            # hour: the arrivals then contend over an empty cell and it
            # gets away cleanly, which is §3.17.5's ruling applied to a
            # square two rivals both wanted.
            occupants = sess._undamaged_harvesters_at(
                tx, ty, departing=departing,
            )

            owners: List[str] = []
            harvester_ids: List[str] = []
            total_spilled = 0

            for seat, h in movers:
                total_spilled += sess._damage_harvester(h)
                owners.append(str(h.owner))
                harvester_ids.append(str(h.id))
                pointers[seat] += 1
                applied[seat] += 1
                spent.add(seat)
                resolved.add(seat)

            for occupant in occupants:
                total_spilled += sess._damage_harvester(occupant)
                owners.append(str(occupant.owner))
                harvester_ids.append(str(occupant.id))

            sess._record_collision(
                tx, ty, owners,
                event_type="converging_steps",
                harvesters=harvester_ids,
            )
            # v1.6 kill-feed: every House in the pile-up damaged every
            # other House's unit.
            _distinct = [
                o for i, o in enumerate(owners) if o and o not in owners[:i]
            ]
            for _atk in _distinct:
                for _vic in _distinct:
                    if _atk != _vic:
                        sess._attrib("harv_damaged", str(_atk), str(_vic))

            owners_label = "+".join(sorted(set(owners)))
            harv_label = ", ".join(harvester_ids)
            caption = (
                f"CONVERGING STEPS at ({tx},{ty}) — "
                f"({owners_label}) {harv_label} all wreck at their "
                f"original positions, {total_spilled} cargo square(s) lost"
            )
            sess.log_info(self._stamp_hour(hour, caption))
            collisions = sess.pending_collision_events
            sess.pending_collision_events = []
            # Ownerless, like the other joint collisions. The client
            # needs no new branch for it: the timeline renders any frame
            # without an owner, and playCollisionFx works off the
            # ``collisions`` payload rather than the tag. There is no
            # bespoke movement to animate here — nobody went anywhere.
            sess.replay_push_scene(
                replay,
                caption,
                owner=None,
                tag="collision_converging_steps",
                collisions=collisions or None,
                hour=hour,
                attempted=f"converging steps: {harv_label}",
                outcome="collision",
            )
            sess._redsign_hour = int(hour)  # type: ignore[attr-defined]
            sess._pulse_probe_cameras()

        return resolved

    def _crush_probes_under_harvesters(
        self,
        sess: "GameSession",
        replay: List[dict],
        *,
        hour: int,
    ) -> None:
        """v1.49 (§3.11.1) — nothing survives the hour under a harvester.

        Crushing is normally the mover's own business, done inside its
        slot, and for the ordinary case that is right: a harvester rides
        onto a probe that was already there. It is wrong when the probe
        launches onto the harvester's cell on the SAME hour, because
        then the answer turned on seat index. Prober first and its probe
        was flattened by the landing that followed; harvester first and
        the probe settled underneath one and lived, which is also the
        only way the board could ever show a probe and a harvester
        sharing a cell.

        This closes it without disturbing the mover's crush, which keeps
        its caption, its frame and its kill-feed credit. The sweep runs
        once every seat has acted, so by then the ordinary case has
        cleaned up after itself and the only thing left to find is a
        probe that arrived late.

        A cell with more than one harvester on it is a wreck pile, and
        the crush is credited to nobody: picking one of them would put
        the seat loop back in charge of the answer.
        """
        by_cell: Dict[Tuple[int, int], List[str]] = {}
        for e in sess.entities.values():
            if e.entity_type == "harvester" and e.x is not None:
                by_cell.setdefault((int(e.x), int(e.y)), []).append(str(e.owner))
        if not by_cell:
            return
        buried = sorted({
            (int(e.x), int(e.y))
            for e in sess.entities.values()
            if e.entity_type == "probe"
            and e.x is not None
            and (int(e.x), int(e.y)) in by_cell
        })
        if not buried:
            return

        msgs: List[str] = []
        for x, y in buried:
            owners = by_cell[(x, y)]
            msgs.extend(sess.consume_probes_at(
                x, y, crusher_owner=owners[0] if len(owners) == 1 else None,
            ))
        if not msgs:
            return

        crushed_probes = sess.pending_probe_crush_events
        sess.pending_probe_crush_events = []
        caption = "; ".join(msgs)
        sess.log_info(self._stamp_hour(hour, caption))
        # Ownerless, like the joint collisions: the probe's House lost it
        # and the harvester's House did nothing but stand there, so
        # neither one is the actor. The client needs no new branch —
        # playProbeCrushFx works off ``crushed_probes``, not the tag.
        sess.replay_push_scene(
            replay,
            caption,
            owner=None,
            tag="probe_crushed",
            crushed_probes=crushed_probes or None,
            hour=hour,
            outcome="ok",
        )

    def _apply_one(
        self,
        sess: "GameSession",
        owner: "PlayerId",
        move: Move,
        *,
        hour: int = 0,
        live_override: Optional[set] = None,
        emp_blocked_cells: Optional[set] = None,
        snap_hot_cells: Optional[dict] = None,
        departing_units: Optional[AbstractSet[str]] = None,
    ) -> tuple[str, str, List[str]]:
        """Apply one move; return ``(caption, tag, side_messages)``.

        ``side_messages`` carries auxiliary info (crushed probes, hoard
        deposits) that the caller logs as ``info`` when the action was
        valid; on ``waste`` it's discarded.

        ``emp_blocked_cells`` (v1.10, RULEBOOK §4.9.3) is the set of
        cells inside an EMP cloud that was already active BEFORE this
        hour's own launches resolved. A drop/step landing on one of
        these cells still lands (it isn't bounced like a mine) but
        does not auto-harvest — the cell was already "hot" going into
        the hour. A cloud freshly spawned this same hour is excluded,
        so a same-hour launch+landing coincidence still harvests once.
        """
        side: List[str] = []

        if isinstance(move, ProbeMove):
            # v0.8.0 — probes are now a finite resource. The seat
            # must have stock (replenish via the Orbit BUILD_PROBE
            # action) before a ProbeMove can land. Empty stock is
            # surfaced as a yellow error and the move is skipped
            # without burning a tick.
            if int(sess.probe_stock.get(owner, 0)) <= 0:
                return (
                    f"{owner}: probe stock exhausted — build a probe in "
                    f"the next Orbit phase",
                    "waste",
                    side,
                )
            ok, msg = sess.spawn_probe(owner, move.at[0], move.at[1], hour=hour)
            if ok:
                sess.probe_stock[owner] = max(
                    0, int(sess.probe_stock.get(owner, 0)) - 1
                )
            return msg, ("probe" if ok else "waste"), side

        if isinstance(move, DropMove):
            ok, msg, _harvested = sess.try_drop_unit(
                owner, move.unit, move.at[0], move.at[1],
                live_override=live_override,
                emp_blocked_cells=emp_blocked_cells,
                snap_hot_cells=snap_hot_cells,
                departing_units=departing_units,
            )
            if not ok:
                return msg, "waste", side
            crushed = sess.consume_probes_at(
                move.at[0], move.at[1], crusher_owner=owner,
            )
            side.extend(crushed)
            if crushed:
                msg = f"{msg}; {len(crushed)} probe crushed"
            return msg, "drop", side

        if isinstance(move, StepMove):
            ok, msg, _harvested = sess.try_step_unit(
                owner, move.unit, move.to[0], move.to[1],
                emp_blocked_cells=emp_blocked_cells,
                snap_hot_cells=snap_hot_cells,
                departing_units=departing_units,
            )
            if not ok:
                return msg, "waste", side
            crushed = sess.consume_probes_at(
                move.to[0], move.to[1], crusher_owner=owner,
            )
            side.extend(crushed)
            if crushed:
                msg = f"{msg}; {len(crushed)} probe crushed"
            return msg, "step", side

        if isinstance(move, PickupMove):
            ok, msg, parcels = sess.try_pickup_unit(owner, move.unit)
            if not ok:
                return msg, "waste", side
            sess.deposit_haul_to_hoard(owner, move.unit, parcels, side)
            return msg, "pickup", side

        # v0.9 — WAIT consumes the hour slot without acting. The
        # caption is intentionally terse; the replay scrubber can
        # coalesce runs of all-wait hours into a "lull" frame so the
        # watcher's timeline stays readable.
        if isinstance(move, WaitMove):
            return f"{owner} waited at this hour", "wait", side

        # v1.31 — the caltrop MINE was retired and its dispatch branch
        # went with it. A stale ``mine_lay`` no longer reaches here at
        # all: `parse_moves` turns it into a WasteMove carrying the
        # retirement reason (`_RETIRED_MOVE_TAGS`), which the branch
        # below reports.

        if isinstance(move, EmpLaunchMove):
            # Reached here only if pre-empt was skipped (insufficient
            # resources). Surface as a runtime waste so the slot
            # doesn't get consumed.
            ok, msg = sess.apply_emp_launch(
                owner, move.at[0], move.at[1], hour=0,
                extra_targets=list(move.extra_ats),
            )
            if not ok:
                return msg, "waste", side
            # Unlikely path: pre-empt missed it but main dispatch
            # succeeded. Treat as a regular emp_launch frame.
            return msg, "emp_launch", side

        if isinstance(move, ChaffFlareMove):
            ok, msg = sess.apply_chaff_flare(owner, hour=0)
            if not ok:
                return msg, "waste", side
            return msg, "chaff_flare", side

        if isinstance(move, SnapLaunchMove):
            # Same "pre-empt declined it" fallback the salvo has above:
            # reached only when the stock check failed up in
            # ``_snap_preempt_phase``, so this surfaces the refusal as a
            # runtime waste rather than silently eating the slot.
            ok, msg = sess.apply_snap_launch(
                owner, move.at[0], move.at[1], hour=hour,
            )
            if not ok:
                return msg, "waste", side
            return msg, "snap_launch", side

        return f"unknown move type {type(move).__name__}", "waste", side
