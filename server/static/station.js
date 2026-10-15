/* station.js — inline orbital "space station" panels (production).
 *
 * Reconstructed after a workspace re-sync dropped the untracked file. Merged
 * from the orbital_exp fork + all in-session enhancements:
 *   • two-phase DUSK (orbital resolve) → PRAXIS (surface resolve) model
 *   • RED ship catapult + GREEN solar catapult (load → launch → ghost)
 *   • blue (fissile) two-phase burn (lift+hold at dusk, consume at praxis)
 *   • live hooks (osOnLiveDusk / osOnLive / osOnLivePlayers)
 *   • per-seat vault rendering (observed hoard vs enemy estimate)
 * jshint esversion:11
 */

(function () {
  "use strict";

  /* ── feature gate (permanent, default ON) ────────────────────────────
   * The station UI is now a permanent feature: ON by default. It can still
   * be turned OFF with ``?station=0`` (that choice persists to localStorage),
   * and re-enabled with ``?station=1``. When disabled we install NO
   * window.osOn* hooks, so app.js's typeof-guarded calls all skip and the
   * panels (``display:none`` until ``body.os-active``) stay hidden. */
  const _osQS = (() => {
    try { return new URLSearchParams(window.location.search).get("station"); }
    catch (_e) { return null; }
  })();
  let _osEnabled;
  if (_osQS != null) {
    _osEnabled = _osQS !== "0" && _osQS.toLowerCase() !== "false";
    try { localStorage.setItem("soc_station_ui", _osEnabled ? "1" : "0"); } catch (_e) {}
  } else {
    // Default ON unless the user has explicitly opted out previously.
    let _stored = null;
    try { _stored = localStorage.getItem("soc_station_ui"); } catch (_e) {}
    _osEnabled = _stored !== "0";
  }
  if (!_osEnabled) return;  // station UI off — leave all hooks undefined
  const _osActivate = () => { try { document.body.classList.add("os-active"); } catch (_e) {} };
  if (document.body) _osActivate();
  else document.addEventListener("DOMContentLoaded", _osActivate);

  /* ── constants ────────────────────────────────────────────────────── */

  // Fallback seat colors (matches app.js OWNER_COLOR + playerColor defaults)
  const OS_COLOR_DEFAULTS = {
    p1: "#FFFFFF",
    p2: "#FCF871",
    p3: "#E45EF0",
    p4: "#82F4FB",
  };

  const OS_LEFT_SEATS  = ["p1", "p3"];
  const OS_RIGHT_SEATS = ["p2", "p4"];

  // Diamond geometry — matches demo.html
  const OS_DIA_HALF    = 5;
  const OS_DIA_SIZE    = OS_DIA_HALF * 2 - 1;  // 9
  const OS_VAULT_ROWS  = 3;   // 3 rows → doubles as the 3-bar unseen view
  const OS_VAULT_COLS  = 5;   // 5 pips per bar
  const OS_VAULT_CELLS = OS_VAULT_ROWS * OS_VAULT_COLS;  // 15 = HOARD_CAPACITY

  const OS_DENSITY = ["░", "▒", "▓", "█"];
  const OS_VAULT_COLORS = {
    red:   "#ff4444",
    green: "#44cc44",
    blue:  "#4488ff",
    grey:  "#444455",
  };

  // Grade → approximate parcel counts (HOARD_CAPACITY=15)
  const OS_FULLNESS_TO_CELLS = { empty: 0, low: 2, half: 5, high: 10, full: 14 };
  // Green estimate strings → midpoint counts
  const OS_GREEN_EST_CELLS   = { "0": 0, "1-3": 2, "4-7": 5, "8-12": 10, "13+": 13 };

  // ── Unseen-station "3 bars" view ─────────────────────────────────────
  // For rivals we can't observe, the vault region renders as three 5-pip bars
  // (fullness / blue / green) using the SAME squares, drawn differently.
  const OS_BAR_FULL_COLOR  = "#9a9aae";  // grey fullness pips
  const OS_BAR_EMPTY_COLOR = "#2b2b3a";  // dim unlit pip
  // grade/estimate → lit pip count (0..5). Fullness + green already carry 5
  // fog bands so they map 1:1; blue is only a 4-band grade in the fog (parity
  // with what the agent sees), so it maps coarsely here. A finer 125/pip blue
  // bar needs a server-side info-range change (which also feeds the agent).
  const OS_FULLNESS_LIT   = { empty: 0, low: 1, half: 2.5, high: 4, full: 5 };
  const OS_GREEN_LIT      = { "0": 0, "1-3": 1.5, "4-7": 3, "8-12": 4, "13+": 5 };
  const OS_BLUE_GRADE_LIT = { none: 0, low: 1.5, medium: 3, high: 5 };
  const OS_BLUE_PER_PIP   = 150;  // burn-detection band: fine 150/pip steps
  const OS_BLUE_LEVEL_PER_PIP = 300;  // LEVEL bar: 5 pips → ~1500 war-chest ceiling

  const OS_TIER_GLYPH  = { trace: "░", vein: "▒", mass: "▓", pure: "█" };
  // Doubled density blocks for the mini catapult tiles — bigger + clearer.
  const OS_TIER_GLYPH2 = { trace: "░░", vein: "▒▒", mass: "▓▓", pure: "██" };
  function _osTierGlyph2(tier) { return OS_TIER_GLYPH2[tier] || "▓▓"; }
  // Purity (0..255) → doubled density block (mirrors app.js catTierGlyph).
  function _osPurityGlyph2(purity) {
    const p = Math.max(0, Math.min(255, Number(purity) || 0));
    if (p >= 255) return "██";
    if (p >= 151) return "▓▓";
    if (p >= 51)  return "▒▒";
    return "░░";
  }

  const OS_EVENT_LABEL = {
    probe:       "probe launched",
    drop:        "orblift dropped harvester",
    drop_bounce: "orblift failed (bounce)",
    pickup:      "orblift recovered harvester",
    // v1.31 — the caltrop is retired and no new season can emit this,
    // but seasons recorded before v1.31 still replay through here.
    // Deleting the entry does not tidy anything; it blanks a glyph in
    // the archive. Same for OS_EVENT_GLYPH below and `mine_emit`.
    mine_lay:    "mine laid",
    emp_launch:  "EMP launched",
    snap_launch: "SNAP fired",
    chaff_flare: "chaff flare",
    abandoned:   "abandoned",
    damaged:     "damaged",
  };

  const OS_EVENT_GLYPH = {
    probe: "·", drop: "▼", drop_bounce: "▼", pickup: "▲",
    mine_lay: "◆", emp_launch: "◯", snap_launch: "✚", chaff_flare: "✶",
    abandoned: "✖", damaged: "⚠",
  };

  // Catapult lane geometry + fill. v1.13 — these are now the RESTING
  // size of each lattice, not a capacity: settlement is automatic and
  // uncapped, so a heavy night grows the grid past them (see
  // _osEnsureCatSlots). They keep an idle catapult looking like a
  // catapult on a night with nothing to ship.
  const OS_CAT_SLOTS   = 20;
  const OS_GREEN_SLOTS = 12;
  const OS_GREEN_FILL  = "#44cc44";

  // Blue (fissile) burn.
  const OS_BLUE_BURN_COLOR = "#4aa3ff";
  const _OS_BLUE_LVL = { none: 0, low: 1, medium: 2, high: 3 };

  // v1.34 — the arsenal (RULEBOOK §4.9.8). Blue that has become ordnance
  // stops being blue and starts being cyan: same pips, same glyph, a
  // colour that says "this is pointed at somebody now".
  const OS_ARMS_COLOR   = "#39d3d3";
  const OS_ARMS_SEGMENTS = 6;          // 600 cap at 100 blue a segment
  const OS_ARMS_PER_PIP  = 100;
  const OS_ARMS_CAP_FALLBACK = 600;
  const OS_ARMS_FADE_MS  = 420;        // pip fade up / down (v1.35)
  const OS_ARMS_LIFT_MS  = 460;        // held ◆ turning cyan before it flies
  const OS_ARMS_FLY_MS   = 620;        // ...and the flight into the bar

  // Replay dwell timing (ms). DUSK holds long enough for the load animation to
  // read; PRAXIS holds for the launch + count-up. Consumed by app.js via
  // window._osPendingDwellMs.
  const OS_LOAD_STAGGER   = 160;
  const OS_DWELL_DUSK_BASE = 1200;
  const OS_DWELL_DUSK_MAX  = 4200;
  const OS_DWELL_OPEN      = 1600;

  /* ── state ────────────────────────────────────────────────────────── */

  let _initialized   = false;
  let _activeSeats   = [];
  // v1.53 — boot teleport. Declared up here with the rest of the state
  // rather than beside the code that uses it, because the arming call
  // lives in the public-hooks block near the top of the file: a `let`
  // further down puts it in the temporal dead zone at that point, and
  // the `try` around the arming swallowed the ReferenceError whole. The
  // stations then simply never hid, with nothing logged anywhere.
  let _osBootPending = false;  // armed by app.js, cleared when it runs
  let _osBootRan     = false;  // one teleport per page
  /** @type {number | null} */
  let _osBootFailsafe = null;  // dead-man's switch; see _osBootArm
  let _stationObs    = {};    // seat → latest obs snapshot
  let _catData       = null;  // latest RED catapult.slot_assignments array
  let _greenData     = null;  // latest GREEN jettison.slot_assignments array
  let _scores        = {};    // seat → {score}   (current target scores)
  let _displayScore  = {};    // seat → number    (currently-animating value)
  let _scoreRaf      = {};    // seat → rAF id for the count-up tween
  let _replayDriven  = false; // true in replay: scores count up from ticks
  let _lastActivity  = {};    // seat → event array from last briefing
  let _catLaunchTimers = [];  // pending setTimeout ids for catapult clear-after-launch
  let _vaultSettleTimers = []; // pending setTimeout ids for delayed vault depletion
  let _viewerSeat      = null;  // "p1"/"p2"/"p3"/"p4"/"obs" — current replay POV
  let _currentDay    = null;  // day number of the last rendered tick
  let _currentPhase  = "day"; // "day" | "night" — tracks most recent dawn/dusk
  let _prevLeader    = null;  // seat currently flagged as leader (for change-flash)
  let _lastTickIdx   = -1;    // replay tick index of the last tick (forward detect)
  let _vaultCells    = {};    // seat → current {type,density}[] shown in the vault
  let _blueHeld      = {};    // seat → { count, burn, exact } staged blue burn
  // v1.35 — blue-worth of ordnance each seat has LAUNCHED so far in the
  // night under the cursor, and the pip count each arsenal bar was last
  // painted at. The first lets the bar fall at the hour the weapon
  // flies instead of at the next dawn; the second is what tells a
  // repaint which pips are new, so they can fade rather than blink.
  let _armsFired     = {};    // seat → blue fired so far tonight
  let _armsLit       = {};    // seat → whole pips lit at last paint
  // The rack the dusk arming beat has just filled, held until the
  // post-orbital snapshot catches up. The fill lands DURING the dusk
  // dwell, while _stationObs is still parked on the pre-orbital reading
  // so the vault can drain into it; without this the bar would light and
  // then drop straight back to yesterday's rack.
  let _armsPinned    = {};    // seat → arms reading the beat is asserting
  let _liveProfiles  = null;  // live player_profiles fallback (turn-1, no replay yet)
  let _praxisPending = null;  // day whose staged launch resolves on the next
                             // forward night frame (dusk staged → praxis fires)
  let _liveMode      = false; // true when showing the LIVE board (prefer live
                              // inventory over replay reconstruction for vaults)
  // v1.34 — "is the panel showing a PAST moment?", which is not the same
  // question as _liveMode and differs from it exactly at boot. _liveMode
  // starts false meaning "nobody has told us yet"; this starts false
  // meaning "we are at the present until a replay tick says otherwise",
  // which is the correct default for a page that has just opened on a
  // live game. Using _liveMode here made a freshly loaded board draw
  // day one's empty arsenal all season.
  let _showingPast   = false;
  let _loadedDay     = null;  // day whose catapult LOAD animation already played
  let _catFired      = false; // RED catapult launched → render slots as ghost
  let _greenFired    = false; // GREEN catapult launched → ghost likewise

  /* ── color helpers ────────────────────────────────────────────────── */

  function _seatColor(seat) {
    const d = window._socOrbitalData;
    let c = d?.playerProfiles?.[seat]?.color;
    if ((!c || c === "") && _liveProfiles) c = _liveProfiles?.[seat]?.color;
    return (c && c !== "") ? c : (OS_COLOR_DEFAULTS[seat] || "#FFFFFF");
  }

  /* ── orbit→surface sequencing ─────────────────────────────────────────
   * When ON (default), a station launch animation plays FIRST and the
   * matching on-board landing is held back by a per-kind lead (see app.js
   * _osOrbitLeadMs) so an orbital action reads as "launched from orbit,
   * then executes on the surface". OFF = both play simultaneously.
   * app.js owns the lead + replay dwell; this is just the shared master
   * flag, driven by the Settings checkbox (see app.js). */
  if (typeof window._osOrbitSeq !== "boolean") window._osOrbitSeq = true;

  // The station UI surfaces orbit reports via hover/click cards, so hide any
  // standalone orbit-report buttons if present.
  function _osHideReportButtons() {
    document
      .querySelectorAll("[data-os-report-btn], .cc-orbit-report-btn")
      .forEach((b) => { b.style.display = "none"; });
  }

  /* ── public hooks ─────────────────────────────────────────────────── */

  /* v1.53 — arming the boot teleport is a FLAG app.js sets, not a hook it
   * calls, and that is forced by load order: this file is loaded after
   * app.js, and app.js arms the curtain inside its own boot IIFE — which
   * runs before a single `osOn*` defined here exists. A hook would simply
   * never fire. Firing the teleport is a hook, because by then everything
   * is loaded. */
  try {
    if (window.__SOC_BOOT_PENDING__ === true) _osBootArm();
  } catch (_e) { /* no boot — stations just appear, as they always did */ }

  /** Arm for a boot that starts AFTER this file loaded — NEW GAME from
   *  the idle landing screen, where the flag above was already read as
   *  false. The stations exist by then, so this marks them directly. */
  window.osOnBootArm = function () {
    _osBootArm();
    _osMarkPreboot();
  };

  /** Cue the teleport, `afterMs` from now. app.js calls this as the fog
   *  wave crosses the board, so the stations land into a settled map. */
  window.osOnBootTeleport = function (afterMs) {
    _osRunBootTeleport(afterMs);
  };

  /** No teleport after all — show the stations as they are. Called when
   *  app.js abandons the boot (a failed load, a seat picker, boot off). */
  window.osOnBootCancel = function () {
    if (!_osBootRan) _osBootAbort();
  };

  window.osOnOrbitalDataLoaded = function () {
    _viewerSeat = window._socReplayViewSeat || null;
    const d = window._socOrbitalData;
    _replayDriven = !!(d && Object.keys(d.catapultByDay || {}).length > 0);
    _osInitPanels();
    _osHideReportButtons();
    _initialized = true;
    _osFitMapZoom(0);
    _osInstallResizeFit();
  };

  // Live game hook: build the station skeleton from the live player list before
  // any replay/night data exists (fixes "stations don't appear on turn 1").
  window.osOnLivePlayers = function (players, profiles, viewerSeat) {
    if (Array.isArray(players) && players.length) {
      window.__SOC_PLAYERS__ = players.slice(0, 4);
    }
    if (profiles) _liveProfiles = profiles;
    if (viewerSeat) _viewerSeat = viewerSeat;
    if (_initialized && window._socOrbitalData) return;
    _osInitPanels();
    _osHideReportButtons();
    _initialized = true;
    _osFitMapZoom(0);
    _osInstallResizeFit();
  };

  window.osOnViewerChange = function (seat) {
    _viewerSeat = seat;
    _osRefreshAllVaults();
  };

  // LIVE orbital resolve: called right after the player commits an orbit that
  // resolves but produces NO night frames yet (the night hasn't been played).
  // Stages the DUSK beat directly so the shipment + blue/consumption is
  // reviewable while planning the night. Mirrors the ``slot === "dusk"``
  // forward branch of osOnReplayTick, but sourced from freshly-pulled data.
  //
  // Returns how long the beat it just started runs for (v1.35). The
  // caller needs it because this animation has no dwell of its own to
  // hide behind: it plays over the live board, and anything that covers
  // the board meanwhile — the tutorial modal, most of all — has to know
  // to wait rather than land on top of the thing it is teaching.
  window.osOnLiveDusk = function (day) {
    const d = window._socOrbitalData;
    if (!d || !day) return 0;
    _liveMode = false;  // this beat animates from pre-orbital reconstruction
    _showingPast = true;

    for (const t of _catLaunchTimers) clearTimeout(t);
    _catLaunchTimers = [];
    for (const t of _vaultSettleTimers) clearTimeout(t);
    _vaultSettleTimers = [];
    _osClearCatFiring();

    const blob = d.catapultByDay?.[String(day)];
    _catData   = blob?.catapult ? blob.catapult.slot_assignments : null;
    _greenData = blob?.jettison ? blob.jettison.slot_assignments : null;
    _catFired = false; _greenFired = false;  // fresh (unfired) load
    _osRenderCatSlots();
    _osRenderGreenCatSlots();
    _osSetPhase("night");
    _currentDay = day; _currentPhase = "night";
    _osClearBlueHeld();

    // Score holds at the PREVIOUS day's cumulative; the just-shipped haul shows
    // as a pending "+X" badge (it folds into the total when the night plays).
    _osUpdateScoresForDay(day - 1, false);
    _osShowShipDeltas(day);

    // Vault reconstructs to the END OF THE LAST NIGHT (= pre-orbital for this
    // day). Render that, then animate the shipment/jettison depleting it, and
    // lift the burned blue out to be held above the station.
    for (const s of _activeSeats) {
      if (_osObserved(s)) _osSetVault(s, _osVaultSourceCells(s));
      else _osRenderVault(s);
    }
    _osCatLoadAnim();
    _osGreenCatLoadAnim();
    _scheduleDepart(_catData, "red");
    _scheduleDepart(_greenData, "green");
    const _armsEndMs = _osBlueStageForDay(day, true);
    // v1.9 (bug #10) — once the shed animation has played, snap the observed
    // vault to the server's TRUE post-orbital inventory. At a LIVE dusk there
    // are no night frames yet to reconstruct from, so ``preferLive`` reads
    // ``lastLiveInventory`` (which already reflects the resolved orbit). Without
    // this the diamond keeps whatever the red/green shed left behind — it never
    // sheds burned blue or an overflow eviction — and drifts from the tab.
    let _beatMs = 0;
    {
      const _redN   = (_catData   || []).filter(e => e && e.seat).length;
      const _greenN = (_greenData || []).filter(e => e && e.flushed).length;
      const _loadN  = Math.max(_redN, _greenN);
      const _loadEndMs = OS_DWELL_DUSK_BASE + Math.max(0, _loadN - 1) * OS_LOAD_STAGGER;
      _vaultSettleTimers.push(
        setTimeout(() => _osReconcileObservedVaults(true), _loadEndMs),
      );
      _beatMs = Math.max(_loadEndMs, _armsEndMs);
    }
    // Mark this day's load as already PLAYED so the night cinematic's DUSK beat
    // doesn't re-run the load animation (fixes "catapult loads twice").
    _loadedDay = day;
    // ARM the praxis fold so the pending "+X" folds into the score when the
    // night actually plays (or when LIVE re-stages this DUSK). Previously null,
    // which is part of why the "+X" sometimes never got added to the total.
    _praxisPending = day;
    return _beatMs;
  };

  // LIVE final settlement (#4): the season-closing orbit resolved live with
  // NO night to follow. Stage it like DUSK then LAUNCH inline — catapults
  // fire, "+X" folds into the score, blue consumes, vaults settle — and only
  // once that has fully played does ``onDone`` run (so the winner celebration
  // lands AFTER the last scores are on the board, not before).
  window.osOnLiveResolve = function (day, onDone) {
    const d = window._socOrbitalData;
    const _fin = () => { if (typeof onDone === "function") { try { onDone(); } catch (e) {} } };
    if (!d || !day) { _fin(); return; }
    _liveMode = false;
    _showingPast = true;

    for (const t of _catLaunchTimers) clearTimeout(t);
    _catLaunchTimers = [];
    for (const t of _vaultSettleTimers) clearTimeout(t);
    _vaultSettleTimers = [];
    _osClearCatFiring();

    const blob = d.catapultByDay?.[String(day)];
    _catData   = blob?.catapult ? blob.catapult.slot_assignments : null;
    _greenData = blob?.jettison ? blob.jettison.slot_assignments : null;
    _catFired = false; _greenFired = false;
    _osRenderCatSlots();
    _osRenderGreenCatSlots();
    _osSetPhase("night");
    _currentDay = day; _currentPhase = "night";
    _osClearBlueHeld();
    _osUpdateScoresForDay(day - 1, false);
    _osShowShipDeltas(day);

    for (const s of _activeSeats) {
      if (_osObserved(s)) _osSetVault(s, _osVaultSourceCells(s));
      else _osRenderVault(s);
    }
    const _postObs = d.stationObsByDay?.[String(day)]?.post || {};

    _osCatLoadAnim();
    _osGreenCatLoadAnim();
    _scheduleDepart(_catData, "red");
    _scheduleDepart(_greenData, "green");
    const _armsEndMs = _osBlueStageForDay(day, true);
    _loadedDay = day;
    _praxisPending = null;

    const _redN   = (_catData   || []).filter(e => e && e.seat).length;
    const _greenN = (_greenData || []).filter(e => e && e.flushed).length;
    const _loadN  = Math.max(_redN, _greenN);
    // v1.35 — don't fire on top of an arming beat that is still running.
    // Consuming the held row mid-conversion strands the glyphs it was
    // about to fly into the bar.
    const _loadEndMs = Math.max(
      OS_DWELL_DUSK_BASE + Math.max(0, _loadN - 1) * OS_LOAD_STAGGER,
      _armsEndMs - 500);

    const _fireT = setTimeout(() => {
      _osCatLaunchAnim();
      _osGreenCatLaunchAnim();
      _osUpdateScoresForDay(day, true);
      _osConsumeShipDeltas();
      _osShowGreenCharge(day);
      _osBlueConsumeAll();
      for (const [s, o] of Object.entries(_postObs)) _stationObs[s] = o;
      _osRefreshAllVaults();
    }, _loadEndMs + 500);
    _catLaunchTimers.push(_fireT);

    const _doneT = setTimeout(_fin, _loadEndMs + 500 + OS_DWELL_OPEN + 600);
    _catLaunchTimers.push(_doneT);
    window._osPendingDwellMs = _loadEndMs + 500 + OS_DWELL_OPEN + 600;
  };

  // Called when the viewer leaves replay and returns to the LIVE board. Snaps
  // the station panel out of whatever mid-replay pose it held and shows live
  // truth: full cumulative score + real post-orbital vault.
  window.osOnLive = function (day) {
    const d = window._socOrbitalData;

    for (const t of _catLaunchTimers) clearTimeout(t);
    _catLaunchTimers = [];
    for (const t of _vaultSettleTimers) clearTimeout(t);
    _vaultSettleTimers = [];
    _osClearCatFiring();

    _catData = null; _greenData = null;
    _catFired = false; _greenFired = false; _loadedDay = null;
    _osRenderCatSlots();
    _osRenderGreenCatSlots();
    _osClearBlueHeld();
    _osClearShipDeltas();
    _praxisPending = null;

    _liveMode = true;
    _showingPast = false;

    let latest = Number(day) || 0;
    const keys = Object.keys(d?.catapultByDay || {});
    for (const k of keys) { const n = Number(k); if (n > latest) latest = n; }
    if (latest > 0) {
      _osUpdateScoresForDay(latest, false);
      _currentDay = latest;  // so hover cards resolve the correct live day
    }

    for (const s of _activeSeats) _osRenderVault(s);
  };

  // Repaint observed seats' vaults straight from the live inventory hook
  // (which now folds in any queued/pending refine). Called by app.js when the
  // orbit queue changes so the orbital vault mirrors the tab's pending preview
  // — independent of _liveMode / replay staging.
  window.osRefreshLiveVault = function () {
    if (typeof window._socLiveInventory !== "function") return;
    for (const s of _activeSeats) {
      if (!_osObserved(s)) continue;
      let cells = null;
      try {
        const linv = window._socLiveInventory(s);
        if (linv && Array.isArray(linv.hoard)) cells = _osHoardCells(linv);
      } catch (e) { /* ignore */ }
      if (cells) _osSetVault(s, cells);
    }
  };

  window.osOnOrbitalReportDay = function (kind, day) {
    const d = window._socOrbitalData;
    if (!d) return;
    const phase = kind === "recap" ? "pre" : "post";
    const obs = d.stationObsByDay?.[String(day)]?.[phase] || {};
    for (const [seat, sObs] of Object.entries(obs)) {
      _stationObs[seat] = sObs;
      _osRenderVault(seat);
    }
    if (kind === "briefing") {
      const blob = d.catapultByDay?.[String(day)];
      if (blob?.catapult) { _catData = blob.catapult.slot_assignments; _osRenderCatPips(); }
      if (blob?.jettison) { _greenData = blob.jettison.slot_assignments; _osRenderGreenCatSlots(); }
      const evts = d.orbitalEventsByDay?.[String(day)] || {};
      for (const [seat, list] of Object.entries(evts)) { _lastActivity[seat] = list; }
    }
  };

  window.osOnReplayTick = function ({ slot, day, tag, idx }) {
    const d = window._socOrbitalData;
    if (!d) return;
    _liveMode = false;  // replay/scrub is reconstruction-driven, not live truth
    _showingPast = true;

    // Forward auto-play advances the tick index by exactly one; a scrub jumps.
    // Only forward steps get the resolve ANIMATIONS; scrubbing snaps to state.
    const forward = (typeof idx === "number") && (idx === _lastTickIdx + 1);
    if (typeof idx === "number") _lastTickIdx = idx;

    window._osPendingDwellMs = 0;

    _osSyncArmsFired(idx, slot, forward);

    for (const t of _catLaunchTimers) clearTimeout(t);
    _catLaunchTimers = [];
    for (const t of _vaultSettleTimers) clearTimeout(t);
    _vaultSettleTimers = [];
    // Settlement +/- lines only belong on the terminal RESOLVE frame; wipe
    // them on every other frame so scrubbing back never leaves them stranded.
    if (slot !== "resolve") _osClearSettleStacks();

    // Wipe any stranded firing flash and reconcile both grids from current
    // assignments so every tick starts from clean slots.
    _osClearCatFiring();
    _osRenderCatSlots();
    _osRenderGreenCatSlots();

    const blob = d.catapultByDay?.[String(day)];

    if (slot === "resolve") {
      // ── FINAL SETTLEMENT (#8) ────────────────────────────────────────
      // The season-closing orbit resolved in a beat with NO night frames to
      // follow. Stage it like DUSK (RED + GREEN load, blue lifts, "+X"
      // shows) and then LAUNCH inline: after the load dwell the catapults
      // FIRE, the score folds in, blue consumes, and the vaults settle to
      // their final post-season reading.
      _catData = blob?.catapult ? blob.catapult.slot_assignments : null;
      _greenData = blob?.jettison ? blob.jettison.slot_assignments : null;
      _catFired = false; _greenFired = false;
      _osRenderCatSlots();
      _osRenderGreenCatSlots();
      _osSetPhase("night");
      _currentDay = day; _currentPhase = "night";
      _osClearBlueHeld();
      _osUpdateScoresForDay(day - 1, false);
      _osShowShipDeltas(day);

      const _preObs = d.stationObsByDay?.[String(day - 1)]?.pre
        || d.stationObsByDay?.[String(day)]?.pre || {};
      for (const [s, o] of Object.entries(_preObs)) _stationObs[s] = o;
      for (const s of _activeSeats) {
        if (_osObserved(s)) _osSetVault(s, _osVaultCellsAtTick(s, idx - 1));
        else _osRenderEnemyBars(s);
      }
      const _postObs = d.stationObsByDay?.[String(day)]?.post || {};

      if (forward) {
        _osCatLoadAnim();
        _osGreenCatLoadAnim();
        _scheduleDepart(_catData, "red");
        _scheduleDepart(_greenData, "green");
        const _armsEndMs = _osBlueStageForDay(day, true);
        _loadedDay = day;
        const _redN   = (_catData   || []).filter(e => e && e.seat).length;
        const _greenN = (_greenData || []).filter(e => e && e.flushed).length;
        const _loadN  = Math.max(_redN, _greenN);
        // As at the live resolve: never fire on top of a running arming
        // beat, or the held glyphs are stranded mid-conversion (v1.35).
        const _loadEndMs = Math.max(
          OS_DWELL_DUSK_BASE + Math.max(0, _loadN - 1) * OS_LOAD_STAGGER,
          _armsEndMs - 500);
        _osClearSettleStacks();
        // No night frame will trigger praxis, so FIRE after the load dwell.
        const _fireAt = _loadEndMs + 500;
        const _fireT = setTimeout(() => {
          _osCatLaunchAnim();
          _osGreenCatLaunchAnim();
          _osUpdateScoresForDay(day, true);   // fold the night's RED, less its GREEN
          _osConsumeShipDeltas();
          _osShowGreenCharge(day);
          _osBlueConsumeAll();
          for (const [s, o] of Object.entries(_postObs)) _stationObs[s] = o;
          _osRefreshAllVaults();
          // v1.x (bug #10, terminal orbit) — the shed only pops parcels that
          // flew to the catapults; the FINAL refinery also consumes trace
          // inputs (cascade folds), burns BLUE and may evict on overflow, so
          // the working cells over-count. Snap the OBSERVED diamonds to the
          // authoritative post-settlement hoard (reconstructVaultAtTick now
          // serves ``final_hoard`` at the resolve tick) so the orbital vault
          // matches the VAULT tab and the settled score.
          _osReconcileObservedVaults(false);
        }, _fireAt);
        _catLaunchTimers.push(_fireT);
        // v1.x — after the shipped fold, animate the closing settlement:
        // per-seat GREEN PENALTY (-) then vault-RED FIRE-SALE (+) as separate
        // stacked delta lines, tweening each score down/up to its final.
        let _settleExtra = 0;
        if (_osHasSettlement()) {
          const _settleStart = _fireAt + 950;   // let the shipped tween land
          _settleExtra = 950 + _osPlaySettlement(_settleStart);
        }
        // Celebrate only AFTER the launch animation + score fold + settlement
        // have fully settled — previously this fired at launch START, so the
        // confetti played over an un-resolved board / mid-count scores.
        const _finaleAt = _fireAt + _settleExtra + OS_DWELL_OPEN + 600;
        const _finaleT = setTimeout(() => {
          if (typeof window._osOnSeasonResolved === "function") {
            try { window._osOnSeasonResolved(day); } catch (e) {}
          }
        }, _finaleAt);
        _catLaunchTimers.push(_finaleT);
        window._osPendingDwellMs = _finaleAt;
        _praxisPending = null;
      } else {
        // Scrub onto the finale → snap to the fully-resolved post-season state.
        for (const [s, o] of Object.entries(_postObs)) _stationObs[s] = o;
        _catFired = true; _greenFired = true;
        _loadedDay = day;
        _osRenderCatSlots();
        _osRenderGreenCatSlots();
        _osUpdateScoresForDay(day, false);
        _osConsumeShipDeltas();
        _osClearSettleStacks();
        // Snap straight to the canonical final (shipped -/+ settlement) so a
        // scrub onto the finale reads the same total as the results card.
        const _settle = (typeof window._osGetSettlement === "function")
          ? (window._osGetSettlement() || {}) : {};
        for (const s of _activeSeats) {
          if (_settle[s]) _osSetScoreImmediate(s, _settle[s].final | 0);
        }
        _osClearBlueHeld();
        _osRefreshAllVaults();
        // Snap the diamonds to the authoritative post-settlement hoard so a
        // scrub onto the finale matches the tab (no stale RED / over-count).
        _osReconcileObservedVaults(false);
        _praxisPending = null;
      }

    } else if (slot === "dusk") {
      // ── ORBITAL RESOLVE (hour-0 / pre-praxis DUSK) ─────────────────────
      // The whole orbital phase for `day` resolved in a beat BEFORE this frame
      // existed. Stage it for review: RED loads the ship catapult, GREEN the
      // solar catapult, BLUE lifts + holds above the station, and the score
      // gained shows as a PENDING "+X" badge. Nothing LAUNCHES yet.
      const alreadyStaged = (_loadedDay === day);
      _catData = blob?.catapult ? blob.catapult.slot_assignments : null;
      _greenData = blob?.jettison ? blob.jettison.slot_assignments : null;
      _catFired = false; _greenFired = false;  // loaded but not yet fired
      _osRenderCatSlots();
      _osRenderGreenCatSlots();
      _osSetPhase("night");
      _currentDay = day; _currentPhase = "night";
      _osClearBlueHeld();

      _osUpdateScoresForDay(day - 1, false);
      _osShowShipDeltas(day);

      if (forward) {
        // START at the PRE-orbital state (end of last night = pre[day-1]) so
        // the observed vault + rival bars begin full, then ANIMATE down to
        // post: observed parcels fly out of the vault (cell removal), blue
        // lifts + holds, rival bars drop a pip. We do NOT reconstruct the
        // observed vault at settle — the depletion is driven by cell removal
        // (_scheduleDepart); a frame.hoard reconstruct here could reset it.
        const _preObs = d.stationObsByDay?.[String(day - 1)]?.pre
          || d.stationObsByDay?.[String(day)]?.pre || {};
        for (const [s, o] of Object.entries(_preObs)) _stationObs[s] = o;
        for (const s of _activeSeats) {
          if (_osObserved(s)) _osSetVault(s, _osVaultCellsAtTick(s, idx - 1));
          else _osRenderEnemyBars(s);  // rival bars @ pre-orbital
        }
        // Only run the LOAD fly-in if this DUSK wasn't already staged live at
        // orbit-commit (osOnLiveDusk) — avoids the "catapult loads twice".
        if (!alreadyStaged) {
          _osCatLoadAnim();
          _osGreenCatLoadAnim();
        }
        _scheduleDepart(_catData, "red");
        _scheduleDepart(_greenData, "green");
        const _armsEndMs = _osBlueStageForDay(day, true);
        _loadedDay = day;
        _praxisPending = day;
        const _redN   = (_catData   || []).filter(e => e && e.seat).length;
        const _greenN = (_greenData || []).filter(e => e && e.flushed).length;
        const _loadN  = Math.max(_redN, _greenN);
        const _loadEndMs = OS_DWELL_DUSK_BASE + Math.max(0, _loadN - 1) * OS_LOAD_STAGGER;
        window._osPendingDwellMs = Math.min(
          OS_DWELL_DUSK_MAX, Math.max(_loadEndMs + 400, _armsEndMs));
        // Advance the RIVAL bars to their post-orbital reading so their
        // occupancy/blue visibly drops within the DUSK dwell (observed vault
        // is handled by the cell-removal animation above).
        const _postObs = d.stationObsByDay?.[String(day)]?.post || {};
        const _settleT = setTimeout(() => {
          for (const [s, o] of Object.entries(_postObs)) _stationObs[s] = o;
          // v1.9 (bug #10) — reconcile OBSERVED vaults to the reconstructed
          // POST hoard here too, not just the rival bars. The cell-removal
          // shed above only sheds red/green catapult parcels; snapping to the
          // authoritative per-tick reconstruction heals any residual drift
          // (burned blue, overflow eviction) so the diamond matches the tab.
          _osReconcileObservedVaults(false);
        }, _loadEndMs);
        _vaultSettleTimers.push(_settleT);
      } else {
        // Scrub: snap to the resolved POST-orbital state + staged blue. On a
        // scrub the reconstruction gives the correct hoard for the tick and
        // there's no depletion animation to preserve.
        const _postObs = d.stationObsByDay?.[String(day)]?.post || {};
        for (const [s, o] of Object.entries(_postObs)) _stationObs[s] = o;
        // ARM the praxis fold: a DUSK beat always has its "+X" pending until
        // the next praxis frame folds it in. Previously this was nulled, so
        // scrubbing onto DUSK then playing forward left the "+X" badge stuck
        // and the score never updated (the forward praxis guard needs this).
        _praxisPending = day;
        _osRefreshAllVaults();
        _osBlueStageForDay(day, false);
      }

    } else if (slot === "dawn") {
      // ── new DAY: the night's harvest is in; show the full pre-orbital vault.
      _catData = null;
      _greenData = null;
      _catFired = false; _greenFired = false;  // ghost cleared, catapult empty
      _loadedDay = null;
      _osRenderCatSlots();
      _osRenderGreenCatSlots();
      _osClearBlueHeld();
      const obs = d.stationObsByDay?.[String(day)]?.pre || {};
      for (const [seat, sObs] of Object.entries(obs)) { _stationObs[seat] = sObs; }
      _osSetPhase("day");
      _currentDay = day; _currentPhase = "day";
      _praxisPending = null;
      _osUpdateScoresForDay(day, false);
      _osClearShipDeltas();
      _osRefreshAllVaults();

    } else if (day !== _currentDay) {
      // Scrub to a different day's night frame — snap to a best-effort state.
      const obs = d.stationObsByDay?.[String(day)]?.post
        || d.stationObsByDay?.[String(day)]?.pre
        || {};
      for (const [seat, sObs] of Object.entries(obs)) { _stationObs[seat] = sObs; }
      _catData = blob?.catapult ? blob.catapult.slot_assignments : null;
      _greenData = blob?.jettison ? blob.jettison.slot_assignments : null;
      // A NIGHT/praxis frame is post-launch → show the shipped payload as ghost.
      _catFired = true; _greenFired = true;
      _loadedDay = day;
      _osRenderCatSlots();
      _osRenderGreenCatSlots();
      _osClearBlueHeld();
      _currentDay = day; _currentPhase = "night";
      _praxisPending = null;
      _osUpdateScoresForDay(day, false);
      _osClearShipDeltas();
      _osRefreshAllVaults();

    } else {
      // ── NIGHT / PRAXIS frame ───────────────────────────────────────────
      // The first forward night frame after dusk is "PRAXIS begins": the staged
      // orbit RESOLVES — catapults launch, the "+X" folds in, blue consumes.
      // Either way the station now reads its POST-orbital snapshot — keep
      // _stationObs pinned there so hover cards + rival bars stay current.
      const _postObs = d.stationObsByDay?.[String(day)]?.post || {};
      for (const [s, o] of Object.entries(_postObs)) _stationObs[s] = o;
      if (forward && _praxisPending === day) {
        _praxisPending = null;
        _osCatLaunchAnim();
        _osGreenCatLaunchAnim();
        _osUpdateScoresForDay(day, true);
        _osConsumeShipDeltas();
        _osShowGreenCharge(day);
        _osBlueConsumeAll();
        window._osPendingDwellMs = OS_DWELL_OPEN;
      } else if (!forward) {
        // Scrub landed on a praxis hour → everything already resolved.
        _praxisPending = null;
        if (!_catData && blob?.catapult) _catData = blob.catapult.slot_assignments;
        if (!_greenData && blob?.jettison) _greenData = blob.jettison.slot_assignments;
        _catFired = true; _greenFired = true;
        _loadedDay = day;
        _osRenderCatSlots();
        _osRenderGreenCatSlots();
        _osUpdateScoresForDay(day, false);
        _osConsumeShipDeltas();
        _osClearBlueHeld();
      }
      // v1.x — keep the ORBITAL VAULT (diamond) in lock-step with the VAULT
      // tab THROUGH PRAXIS. As each harvester banks its haul that hour the
      // reconstructed hoard grows (the tab already reflects this per deposit),
      // so reconcile the observed diamonds to the per-tick reconstruction on
      // every night frame — forward AND scrub. Previously the diamond only
      // refreshed at dusk/dawn, so it sat frozen all night while the tab
      // updated hour by hour. ``_osReconcileObservedVaults`` reads
      // ``reconstructVaultAtTick(replayTickIdx)`` (same source as the tab), so
      // the two are consistent by construction, at the correct hour.
      _osReconcileObservedVaults(false);
    }
  };

  // True if the viewer can see this seat's real vault contents.
  function _osObserved(seat) {
    return _viewerSeat === "obs" || _viewerSeat === seat;
  }

  // Render every vault from its best source (observed → per-parcel hoard;
  // enemy → coarse grade estimate).
  function _osRefreshAllVaults() {
    for (const s of _activeSeats) _osRenderVault(s);
    // The arsenal rides along with every vault repaint. It is driven by
    // the same _stationObs snapshots, so anywhere those move the bar has
    // to move with them — including every scrub target, which is what
    // stops a backwards scrub leaving a stale rack on screen.
    _osRefreshAllArms();
  }

  // v1.9 (bug #10) — Snap the OBSERVED vaults back to the authoritative
  // post-orbital hoard after a DUSK shed has animated. The shed only pops the
  // red/green parcels that flew to the catapults (``_scheduleDepart``); it does
  // NOT model every way a vault shrinks at orbit — burned BLUE fissile, a
  // tier-priority overflow eviction, or a refine's byproducts all leave the
  // working ``_vaultCells`` over-counting the true POST reading. The VAULT tab
  // reconstructs authoritative POST straight from the frame hoard snapshot, so
  // without this reconcile the diamond and the tab (and true content) drift
  // apart until the next full refresh. ``preferLive`` reads the server's
  // current inventory (the only POST truth at a LIVE dusk, where no night frame
  // exists yet to reconstruct from); replay falls through to the per-tick
  // reconstruction, which already carries the post-orbital hoard for the tick.
  function _osReconcileObservedVaults(preferLive) {
    for (const s of _activeSeats) {
      if (!_osObserved(s)) { _osRenderEnemyBars(s); continue; }
      let cells = null;
      if (preferLive && typeof window._socLiveInventory === "function") {
        try {
          const linv = window._socLiveInventory(s);
          if (linv && Array.isArray(linv.hoard)) cells = _osHoardCells(linv);
        } catch (e) { /* fall through to reconstruction */ }
      }
      if (!cells) cells = _osVaultSourceCells(s);
      _osSetVault(s, cells);
    }
  }

  // v1.9 (bug #10) — the diamond's CURRENT working cell count for a seat, so
  // the app-side vault audit (window._socVaultAudit) can compare what the
  // orbital station is showing against the VAULT tab + authoritative hoard.
  window._osVaultCount = function (seat) {
    const cells = _vaultCells[seat];
    return Array.isArray(cells) ? cells.length : null;
  };

  // Vault cells for a seat AT a specific replay tick (observed → per-parcel
  // reconstruction at that tick; enemy → coarse estimate).
  function _osVaultCellsAtTick(seat, tickIdx) {
    const fn = window._socReconstructVaultAt;
    if (_osObserved(seat) && typeof fn === "function") {
      try {
        const inv = fn(tickIdx, seat);
        if (inv && Array.isArray(inv.hoard)) return _osHoardCells(inv);
      } catch (e) { /* fall through to estimate */ }
    }
    return _osEstimateCells(_stationObs[seat] || {});
  }

  // Shed vault cells over the dusk load window, one per departing parcel, so
  // the vault empties IN SYNC with parcels arriving at the catapults.
  function _scheduleDepart(list, type) {
    (list || []).forEach((e, i) => {
      const seat = e && e.seat;
      const eligible = e && seat && (type === "green" ? e.flushed : true);
      if (!eligible) return;
      _vaultSettleTimers.push(setTimeout(
        () => _osRemoveVaultCell(seat, type),
        i * OS_LOAD_STAGGER + 150,
      ));
    });
  }

  window.osOnEntityArrival = function (delta, _targetCell) {
    if (!delta) return;
    const seat  = delta.owner || "p1";
    const side  = OS_LEFT_SEATS.includes(seat) ? "left" : "right";
    const color = _seatColor(seat);

    if (delta.kind === "probe_emit") {
      // Probe = small fast recon dot (EMP-style solid marker, player-coloured).
      const yOff = (Math.random() - 0.5) * 90;
      _osQueueAnim(seat, "●", color, side, "out", { dur: 2200, ease: "in", size: 8, yOff, label: "probe" });
    } else if (delta.kind === "emp_launch") {
      const yOff = (Math.random() - 0.5) * 90;
      _osQueueAnim(seat, "■", "#00ffff", side, "out", { dur: 2200, ease: "in", size: 8, yOff, label: "emp" });
    } else if (delta.kind === "snap_launch") {
      // v1.36 — SNAP had no branch here at all, so a seat could fire one
      // and its platform would sit there doing nothing. Amber and ✚ to
      // match the board's order marker and scorch mark; smaller than the
      // EMP's ■ because it is one missile, not a salvo of three. The
      // 1470ms is the EMP's 2200 over SNAP's 1.5× speed — the whole
      // point of the weapon is that it gets there first, so the station
      // must not be the one place it looks slow.
      const yOff = (Math.random() - 0.5) * 90;
      _osQueueAnim(seat, "✚", "#ffd166", side, "out", { dur: 1470, ease: "in", size: 9, yOff, label: "snap" });
    } else if (delta.kind === "mine_emit") {
      // v1.28 — the minelayer had no station glyph, so a seat could lay
      // mines all night with its platform showing nothing. Carries the
      // ◆ of the board's minelayer arc and the magenta of the mine order
      // marker, so the weapon reads the same in all three places; the
      // slower 2800ms sits between the EMP dart and the harvester lift,
      // which is where a laying run belongs.
      const yOff = (Math.random() - 0.5) * 90;
      _osQueueAnim(seat, "◆", "#ff5fd7", side, "out", { dur: 2800, ease: "in", size: 11, yOff, label: "minelayer" });
    } else if (delta.kind === "chaff_flare") {
      const BURST = [
        { ch: "░", yOff: -80, delay:   0, dur: 750, col: "#cccccc" },
        { ch: "▒", yOff: -50, delay:  25, dur: 900, col: "#999999" },
        { ch: "▓", yOff: -20, delay:  50, dur: 820, col: "#dddddd" },
        { ch: "░", yOff:  10, delay:  15, dur: 700, col: "#aaaaaa" },
        { ch: "▒", yOff:  35, delay:  60, dur: 860, col: "#bbbbbb" },
        { ch: "░", yOff:  60, delay:  35, dur: 780, col: "#888888" },
        { ch: "▓", yOff: -65, delay:  70, dur: 680, col: "#eeeeee" },
        { ch: "▒", yOff:  82, delay:  45, dur: 930, col: "#aaaaaa" },
      ];
      for (const p of BURST) {
        _osQueueAnim(seat, p.ch, p.col, side, "out", { dur: p.dur, ease: "out", size: 5, yOff: p.yOff, delay: p.delay });
      }
      _osQueueAnim(seat, "✶", "#dddddd", side, "out", { dur: 900, ease: "out", size: 9, yOff: 0, label: "chaff" });
    } else if (delta.kind === "drop") {
      // Harvester deploying via orblift — big descending triangle, tagged.
      _osQueueAnim(seat, "▲", color, side, "out", { dur: 3400, ease: "in", size: 18, yOff: 30, label: "harvester" });
    } else if (delta.kind === "drop_bounce") {
      _osQueueAnim(seat, "▲", color + "88", side, "out", { dur: 3400, ease: "in", size: 18, yOff: 30, label: "harvester ✕" });
    } else if (delta.kind === "pickup") {
      const emped = delta.emped;
      const dmg   = delta.damaged;
      // Fill reflects CARGO: full ▲ = recovering a loaded harvester, empty
      // △ = recovering an empty one. Damage/EMP only tint the glyph + label.
      const glyph = delta.loaded ? "▲" : "△";
      const col   = emped ? "#00ffff" : dmg ? "#ff4444" : color;
      const lbl   = emped ? "harvester emp'd" : dmg ? "harvester hit" : "recover";
      _osQueueAnim(seat, glyph, col, side, "in", { dur: 3400, ease: "out", size: 18, yOff: dmg ? 55 : 30, label: lbl });
    }
  };

  window.osOnScoreUpdate = function (bySeat) {
    if (_replayDriven) return;  // replay drives scores from ticks, not live HUD
    if (!bySeat) return;
    _scores = bySeat;
    for (const [seat, s] of Object.entries(bySeat)) {
      _osSetScoreImmediate(seat, s.score ?? 0);
    }
    _osUpdateLeader();
  };

  /* ── init ─────────────────────────────────────────────────────────── */

  function _osInitPanels() {
    const d = window._socOrbitalData;

    const seen = new Set();
    for (const dayData of Object.values(d?.stationObsByDay || {}))
      for (const phase of Object.values(dayData || {}))
        for (const seat of Object.keys(phase || {})) seen.add(seat);
    const fromProfiles = Object.keys(d?.playerProfiles || _liveProfiles || {});
    const fromLive = Array.isArray(window.__SOC_PLAYERS__)
      ? window.__SOC_PLAYERS__.map((p) => (typeof p === "string" ? p : p?.seat)).filter(Boolean)
      : [];
    const all = seen.size
      ? [...seen]
      : (fromProfiles.length ? fromProfiles : (fromLive.length ? fromLive : ["p1", "p2"]));
    const ORDER = ["p1", "p2", "p3", "p4"];
    _activeSeats = ORDER.filter(s => all.includes(s));

    const leftEl  = document.getElementById("os-side-left");
    const rightEl = document.getElementById("os-side-right");
    if (!leftEl || !rightEl) return;
    leftEl.innerHTML  = "";
    rightEl.innerHTML = "";

    const profiles = d?.playerProfiles || _liveProfiles || {};
    for (const seat of _activeSeats) {
      const el = _osBuildStation(seat, profiles);
      (OS_LEFT_SEATS.includes(seat) ? leftEl : rightEl).appendChild(el);
    }

    // Central RED ship catapult + GREEN solar catapult panels. In a 4-player
    // game the two platforms per side move to the top + bottom CORNERS, so the
    // catapult sits BETWEEN them (column middle) to avoid overlap; otherwise it
    // pins to the bottom of the column as before.
    const fourP = _activeSeats.length >= 3;
    leftEl.classList.toggle("os-side--4p", fourP);
    rightEl.classList.toggle("os-side--4p", fourP);
    const leftCat  = _osBuildCatapultPanel();
    const rightCat = _osBuildGreenCatapultPanel();
    if (fourP && leftEl.children.length >= 2) leftEl.insertBefore(leftCat, leftEl.children[1]);
    else leftEl.appendChild(leftCat);
    if (fourP && rightEl.children.length >= 2) rightEl.insertBefore(rightCat, rightEl.children[1]);
    else rightEl.appendChild(rightCat);

    // Pre-populate _stationObs with day 1 post obs so the first paint matches
    // what the replay's first DUSK(1) tick will show.
    const days = Object.keys(d?.stationObsByDay || {}).map(Number).sort((a, b) => a - b);
    if (days.length) {
      _currentDay = days[0];
      _currentPhase = "night";
      const firstPost = d.stationObsByDay[String(days[0])]?.post
        || d.stationObsByDay[String(days[0])]?.pre
        || {};
      for (const [seat, sObs] of Object.entries(firstPost)) _stationObs[seat] = sObs;
    }

    _osRefreshAllVaults();

    // v1.45 — settle the score readouts to the present. Every OTHER thing
    // that writes a score is a *beat* — a replay tick, the DUSK stage, the
    // LIVE button — and a page that has just opened has run none of them,
    // so without this the readouts keep the "—" they were born with. What
    // hid it for so long is that a seat shipping RED resolves an orbit,
    // which stages a DUSK beat, which paints them: play a normal game and
    // the placeholder is gone before you look at it. Ship nothing (or
    // reload mid-season) and there is no beat, so the station sits blank
    // next to a live board. Latest day, no tween — this is the cold open,
    // not a scoring moment.
    let latestScored = 0;
    for (const k of Object.keys(d?.catapultByDay || {})) {
      const n = Number(k);
      if (Number.isFinite(n) && n > latestScored) latestScored = n;
    }
    if (latestScored > 0 && typeof window._osGetCumulativeScore === "function") {
      _osUpdateScoresForDay(latestScored, false);
    } else {
      // Day one, or the skeleton built from the live player list before the
      // replay payload landed. Nobody has shipped anything, so zero is the
      // true reading and "—" is just us not having looked yet.
      for (const seat of _activeSeats) _osSetScoreImmediate(seat, 0);
      _osUpdateLeader();
    }

    // v1.53 — the stations were just (re)built, so anything the boot
    // sequence marked on the old DOM is gone. Put the hold back.
    if (_osBootPending) _osMarkPreboot();

    _osBindHover();
  }

  // v1.20 — who is flying this seat: H human, B heuristic bot, A LLM
  // agent. app.js owns the classification (it has the agent roster and
  // its needs_llm flags) and publishes the result; a seat we know
  // nothing about gets no badge rather than a wrong one.
  function _osSeatKind(seat) {
    const kinds = window.__SOC_SEAT_KIND__;
    const k = kinds && typeof kinds === "object" ? kinds[seat] : null;
    return k === "H" || k === "B" || k === "A" ? k : "";
  }

  const _OS_KIND_TITLE = {
    H: "human — piloted from a browser",
    B: "bot — heuristic, no LLM",
    A: "agent — LLM",
  };

  function _osBuildStation(seat, profiles) {
    const color  = _seatColor(seat);
    const rawTag = profiles?.[seat]?.tag || seat.toUpperCase();
    const tag    = rawTag.slice(0, 3).padEnd(3).toUpperCase();
    const side   = OS_LEFT_SEATS.includes(seat) ? "left" : "right";
    const kind   = _osSeatKind(seat);

    const div = document.createElement("div");
    div.className = "os-station";
    div.dataset.osStation = seat;
    div.style.setProperty("--os-color", color);

    div.innerHTML = `
<div class="os-diamond-wrap">
  <pre class="os-diamond" data-os-diamond="${seat}"></pre>
  <pre class="os-vault-pre" data-os-vault="${seat}"></pre>
  <span class="os-diamond-tag">${tag}</span>
  ${kind
    ? `<span class="os-diamond-kind" data-os-kind="${kind}"` +
      ` title="${_OS_KIND_TITLE[kind]}">${kind}</span>`
    : ""}
      <div class="os-blue-flash" data-os-blue-flash="${seat}"></div>
      <pre class="os-arms os-arms--${side === "left" ? "right" : "left"}"
           data-os-arms="${seat}"></pre>
    </div>
<div class="os-score" data-os-score="${seat}">—</div>
<div class="os-score-delta" data-os-score-delta="${seat}"></div>
<div class="os-settle" data-os-settle="${seat}"></div>
<div class="os-blue-burn" data-os-blue-burn="${seat}"></div>`;

    const preEl = div.querySelector(`[data-os-diamond="${seat}"]`);
    if (preEl) _osRebuildDiamond(preEl, side === "right");

    return div;
  }

  /* ── diamond / vault rendering ────────────────────────────────────── */

  // Diamond body: player-colored shape, vault region left as spaces (the hole).
  //
  // v1.53 — each hull block is its own `<span>` rather than a character in
  // one long string, so the boot teleport can address them individually
  // (ramp a single pixel up the `OS_DENSITY` scale, tint it, settle it).
  // The vault overlay beside this has always rendered per-span for the
  // same reason, so this is the hull catching up rather than a new idea.
  //
  // The 9x9 character grid is load-bearing and must survive the change:
  // `_osVaultAnchor`, `_osBlueStageOne` and `_osQueueAnim` all derive a
  // cell size as `wrapWidth / OS_DIA_SIZE` and position sprites off it.
  // Plain inline spans inside a `white-space: pre` block do not affect
  // metrics, and the gaps stay as bare spaces, so the grid is unchanged.
  function _osRebuildDiamond(el, mirror) {
    const SIZE = OS_DIA_SIZE, HALF = OS_DIA_HALF;
    let html = "";
    for (let r = 0; r < SIZE; r++) {
      const dist     = Math.abs(r - (HALF - 1));
      const diaStart = dist;
      const diaEnd   = SIZE - 1 - dist;
      for (let c = 0; c < SIZE; c++) {
        const inDiamond = c >= diaStart && c <= diaEnd;
        const inVault   = r < OS_VAULT_ROWS && (mirror
          ? c >= SIZE - OS_VAULT_COLS
          : c < OS_VAULT_COLS);
        html += (!inVault && inDiamond)
          ? `<span class="os-hull-px" data-r="${r}" data-c="${c}">█</span>`
          : " ";
      }
      if (r < SIZE - 1) html += "\n";
    }
    el.innerHTML = html;
  }

  /* ── v1.53 — the boot teleport ──────────────────────────────────────
   *
   * Stations do not appear, they arrive. Each hull pixel starts as
   * nothing, then climbs the density ramp — ░ ▒ ▓ █ — in hot red as a
   * wave sweeps up the diamond, and cools to the seat colour once it is
   * solid. Same four glyphs the vault readout uses, so the effect is
   * built out of the station's own alphabet rather than a new one.
   *
   * The glyph has to change, and CSS cannot animate text, so the ramp is
   * a single rAF loop over every pixel of every station. The cool-down
   * is left to CSS: dropping the `--warm` class lets a transition carry
   * the colour back to the inherited `var(--os-color)`, which works for
   * any colour format a player profile might hand us and needs no RGB
   * arithmetic here.
   */

  /** Blank → █ for one pixel. */
  const OS_BOOT_RAMP_MS   = 400;
  /** Bottom row of a diamond to its top row. */
  const OS_BOOT_SWEEP_MS  = 540;
  /** Per-pixel slop, so the front is a wave and not a shutter. */
  const OS_BOOT_JITTER_MS = 140;
  /** How far into one platform's arrival the NEXT one starts, as a
   *  share of that arrival's own length.
   *
   *  A share rather than a flat delay, because the two numbers have to
   *  move together: this was 210ms against a run of a little over a
   *  second — a 20% offset, which at a glance was every station
   *  arriving at once. At 0.34 the first platform is finishing while
   *  the one behind it is roughly 40% materialised, so the relay reads
   *  as a sequence without any of them waiting their turn. Retuning
   *  the sweep now carries the stagger with it instead of quietly
   *  collapsing it.
   *
   *  The share is tuned against a MEASUREMENT, not arithmetic, and the
   *  two disagree by a lot. `runMs` below is the envelope — the last
   *  pixel's worst-case front plus its ramp — but the diamond's rows
   *  bunch the front times well inside it, so a hull is actually done
   *  in about 700ms rather than the ~1080 the dials add up to. Spacing
   *  off the arithmetic put the trailing platform at 16% when the
   *  leader landed, which looks like queueing.
   *  `backstage/probes/_probe_boot_animation.py` prints the fill curve
   *  — re-read it before touching this. */
  const OS_BOOT_SEAT_SHARE = 0.36;
  /** Per-seat slop on that gap, as a share of it. The arrivals are
   *  meant to be uneven, not metronomic. Kept well under 1 so the
   *  order never scrambles. */
  const OS_BOOT_SEAT_VARY  = 0.45;
  /** Ceiling on the whole relay, as a share of one platform's run:
   *  every platform must have STARTED arriving before the first one
   *  finishes. That is the difference between a relay and a queue —
   *  with four seats and an uncapped gap the last two had not begun
   *  when the leader landed, so the side column filled in one corner
   *  at a time instead of the whole rank coming in together.
   *
   *  Expressed against the same envelope as the share above, and for
   *  the same reason: a hull is really done in about 0.65 of it, so
   *  spending 0.6 on the relay puts the last platform's first pixel
   *  just before the leader's last one. */
  const OS_BOOT_INFLIGHT   = 0.6;
  /** Red → seat colour. Must match the `.os-hull-px` transition. */
  const OS_BOOT_COOL_MS   = 460;

  function _osReduceMotion() {
    try {
      return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (_e) { return false; }
  }

  /** Hold every station off-screen until its pixels are due.
   *
   *  Re-applied from `_osInitPanels` rather than set once, because that
   *  function runs TWICE on a normal load — `osOnLivePlayers` builds the
   *  turn-1 skeleton and `osOnOrbitalDataLoaded` rebuilds it once the
   *  replay payload lands — and the second pass throws away the first
   *  pass's DOM along with anything marked on it. */
  function _osMarkPreboot() {
    for (const el of document.querySelectorAll(".os-station, .os-catapult")) {
      el.classList.add("os-preboot");
    }
  }

  /** Give up on the animation and just show the stations.
   *
   *  Every failure path lands here: boot switched off, reduced motion, a
   *  load that errored before the teleport was cued, or the failsafe.
   *  A station that never arrives is much worse than one that does not
   *  animate. */
  function _osBootAbort() {
    _osBootRan = true;
    _osBootPending = false;
    if (_osBootFailsafe !== null) {
      clearTimeout(_osBootFailsafe);
      _osBootFailsafe = null;
    }
    for (const el of document.querySelectorAll(".os-preboot")) {
      el.classList.remove("os-preboot");
    }
    for (const el of document.querySelectorAll(".os-station--boot")) {
      el.classList.remove("os-station--boot");
    }
    for (const px of document.querySelectorAll(".os-hull-px")) {
      px.textContent = "█";
      px.classList.remove("os-hull-px--warm");
    }
  }

  /** Arm (or re-arm) the hold, and set the dead-man's switch.
   *
   *  The failsafe is independent of app.js's own, because it answers a
   *  different question — "app.js armed me and then never came back" —
   *  which a curtain timer cannot cover: a curtain can lift perfectly
   *  well while the stations underneath stay hidden forever. */
  function _osBootArm() {
    _osBootPending = true;
    _osBootRan = false;
    if (_osBootFailsafe !== null) clearTimeout(_osBootFailsafe);
    _osBootFailsafe = window.setTimeout(() => {
      _osBootFailsafe = null;
      if (!_osBootRan) _osBootAbort();
    }, 30000);
  }

  function _osRunBootTeleport(afterMs) {
    if (_osBootRan) return;
    if (_osReduceMotion() || !(Number(afterMs) > 0)) { _osBootAbort(); return; }

    const stations = _activeSeats
      .map((seat) => document.querySelector(`[data-os-station="${seat}"]`))
      .filter(Boolean);
    if (!stations.length) { _osBootAbort(); return; }

    _osBootRan = true;
    _osBootPending = false;
    if (_osBootFailsafe !== null) {
      clearTimeout(_osBootFailsafe);
      _osBootFailsafe = null;
    }

    const start = performance.now() + Number(afterMs);
    const SPAN = OS_DIA_SIZE - 1;
    const rigs = [];

    // One platform's arrival, end to end: the front crossing the hull,
    // the slop on the last pixel to be caught, and that pixel's own
    // ramp to solid. The relay is spaced against this rather than
    // against a number typed beside it.
    const runMs = OS_BOOT_SWEEP_MS + OS_BOOT_JITTER_MS + OS_BOOT_RAMP_MS;
    const gaps = Math.max(1, stations.length - 1);
    const seatGap = Math.min(
      runMs * OS_BOOT_SEAT_SHARE, (runMs * OS_BOOT_INFLIGHT) / gaps,
    );
    // Accumulated, so the slop wanders the rhythm rather than being
    // cancelled out each step the way an independent per-seat offset
    // would be.
    let seatAt = 0;

    stations.forEach((stEl, i) => {
      if (i > 0) {
        seatAt += seatGap * (1 + (Math.random() - 0.5) * OS_BOOT_SEAT_VARY);
      }
      const pixels = [];
      for (const px of stEl.querySelectorAll(".os-hull-px")) {
        const r = Number(px.getAttribute("data-r"));
        const c = Number(px.getAttribute("data-c"));
        // Bottom to top, leaning slightly across the columns. A pure row
        // sweep reads as a shutter coming up; the lean plus the jitter
        // below is what makes it a front moving through the shape.
        const up   = (SPAN - r) / SPAN;
        const lean = (c / SPAN) * 0.24;
        const at = start
          + seatAt
          + ((up + lean) / 1.24) * OS_BOOT_SWEEP_MS
          + Math.random() * OS_BOOT_JITTER_MS;
        px.textContent = " ";
        pixels.push({ px, at, warm: false, done: false });
      }
      // Safe to unhide now: every pixel is a space and the furniture is
      // held back by `--boot`, so the station occupies its layout slot
      // while showing nothing.
      stEl.classList.remove("os-preboot");
      stEl.classList.add("os-station--boot");
      rigs.push({ el: stEl, pixels, settling: false });
    });

    /** The catapult rails have nothing to materialise — they are
     *  scaffold, not a hull — so they simply turn up alongside the first
     *  platform that finishes. Hung off that rather than its own timer:
     *  a timer has to guess the length of the teleport, and a guess that
     *  drifts leaves the rails arriving visibly after the stations they
     *  belong to. */
    let railsShown = false;
    const showRails = () => {
      if (railsShown) return;
      railsShown = true;
      for (const el of document.querySelectorAll(".os-catapult.os-preboot")) {
        el.classList.remove("os-preboot");
      }
    };

    const tick = () => {
      const now = performance.now();
      let pendingRigs = 0;
      for (const rig of rigs) {
        let landed = 0;
        for (const p of rig.pixels) {
          if (p.done) { landed++; continue; }
          const dt = now - p.at;
          if (dt < 0) continue;
          if (dt >= OS_BOOT_RAMP_MS) {
            p.px.textContent = "█";
            // Dropping `--warm` hands the colour to the CSS transition,
            // which walks it back to the inherited seat colour.
            p.px.classList.remove("os-hull-px--warm");
            p.done = true;
            landed++;
            continue;
          }
          if (!p.warm) { p.px.classList.add("os-hull-px--warm"); p.warm = true; }
          const idx = Math.min(
            OS_DENSITY.length - 1,
            Math.floor((dt / OS_BOOT_RAMP_MS) * OS_DENSITY.length),
          );
          const ch = OS_DENSITY[idx];
          if (p.px.textContent !== ch) p.px.textContent = ch;
        }
        if (landed < rig.pixels.length) { pendingRigs++; continue; }
        if (!rig.settling) {
          rig.settling = true;
          // Per station, not once at the end: a seat whose hull is solid
          // has arrived, and should get its tag, vault and bob back
          // without waiting on the seat that started 600ms after it.
          window.setTimeout(() => {
            rig.el.classList.remove("os-station--boot");
            showRails();
          }, OS_BOOT_COOL_MS);
        }
      }
      if (pendingRigs) { requestAnimationFrame(tick); return; }
      // Belt and braces: a rig with no pixels at all would never reach
      // the settle branch above, and the rails must not be the thing
      // left behind by an edge case.
      showRails();
    };
    requestAnimationFrame(tick);
  }

  // Vault overlay pre: renders only the vault chars, positioned atop the diamond.
  function _osRenderVaultPre(seat, cells, mirror) {
    const el = document.querySelector(`[data-os-vault="${seat}"]`);
    if (!el) return;
    const SIZE = OS_DIA_SIZE;
    let html = "";
    for (let r = 0; r < SIZE; r++) {
      for (let c = 0; c < SIZE; c++) {
        const inVault = r < OS_VAULT_ROWS && (mirror
          ? c >= SIZE - OS_VAULT_COLS
          : c < OS_VAULT_COLS);
        if (inVault) {
          const vIdx = mirror
            ? r * OS_VAULT_COLS + (c - (SIZE - OS_VAULT_COLS))
            : r * OS_VAULT_COLS + c;
          const cell = cells[vIdx] || null;
          if (cell) {
            const ch  = OS_DENSITY[(cell.density ?? 4) - 1] || "█";
            const col = OS_VAULT_COLORS[cell.type] || "#888";
            html += `<span style="color:${col}">${ch}</span>`;
          } else {
            html += " ";
          }
        } else {
          html += " ";
        }
      }
      if (r < SIZE - 1) html += "\n";
    }
    el.innerHTML = html;
  }

  // Build {type,density}[] from a reconstruction hoard — correct per-parcel
  // RED/GREEN/BLUE colours + purity density.
  function _osHoardCells(inv) {
    const hoard = Array.isArray(inv?.hoard) ? inv.hoard : [];
    return hoard.slice(0, OS_VAULT_CELLS).map((p) => {
      const tile = Number(p.tile_at_harvest ?? p.origin_tile ?? -1);
      const pur  = Number(p.purity_at_harvest ?? p.origin_purity ?? 0);
      const type = tile === 1 ? "green" : tile === 3 ? "blue" : "red";
      return { type, density: Math.max(1, Math.min(4, Math.ceil(pur / 64) || 1)) };
    });
  }

  // Build coarse estimate cells for an ENEMY seat from its obs grade bands.
  function _osEstimateCells(obs) {
    const total = OS_FULLNESS_TO_CELLS[obs?.fullness?.grade] ?? 0;
    const green = Math.min(total, OS_GREEN_EST_CELLS[obs?.green?.estimate] ?? 0);
    const cells = [];
    for (let g = 0; g < green; g++)         cells.push({ type: "green", density: 1 });
    for (let r = 0; r < total - green; r++) cells.push({ type: "grey",  density: 1 });
    return cells;
  }

  // ── 3-bar unseen view helpers ────────────────────────────────────────
  function _osFullnessLit(obs) { return OS_FULLNESS_LIT[obs?.fullness?.grade] ?? 0; }
  function _osGreenLit(obs)    { return OS_GREEN_LIT[obs?.green?.estimate] ?? 0; }
  // LEVEL bar (0..5): the seat's total available fissile war-chest (bank +
  // vault) on a ~300/pip scale so a full-season hoard (~1500) spreads across
  // the 5 pips as fractional mid-fill blocks, instead of pinning to "full"
  // from the coarse 750 band ceiling (which made every mid/late-game station
  // read the same). The finer 150/pip _osBlueBand is kept for burn detection
  // and the agent signal. Falls back to the band for legacy snapshots with no
  // exact total.
  function _osBlueLit(obs) {
    const b = obs?.blue;
    if (!b) return 0;
    if (b.total != null)
      return Math.min(OS_VAULT_COLS, (Number(b.total) || 0) / OS_BLUE_LEVEL_PER_PIP);
    return _osBlueBand(b);
  }

  // A pip bar for a lit amount: full pips █, the boundary pip a mid-fill
  // block (▓/▒) for the fractional part, empties a dim dot. Defaults to
  // the 5-wide vault bars; the arsenal passes 6 (v1.34).
  function _osBar(lit, color, max) {
    max = Number(max) || OS_VAULT_COLS;
    lit = Math.max(0, Math.min(max, Number(lit) || 0));
    const pips = [];
    for (let i = 0; i < max; i++) {
      const fill = lit - i;
      if (fill >= 1)     pips.push({ ch: "█", color });
      else if (fill > 0) pips.push({ ch: fill > 0.5 ? "▓" : "▒", color });
      else               pips.push({ ch: "·", color: OS_BAR_EMPTY_COLOR });
    }
    return pips;
  }

  /* ── the arsenal bar (v1.34, RULEBOOK §4.9.8) ─────────────────────── */
  //
  // Weaponised blue is public and exact for every seat, so unlike the
  // vault bars beside it this one reads the same whoever is looking.
  //
  // TWO SOURCES, and which one wins depends on what the panel is FOR.
  //
  // Replaying, the honest answer is what the seat held on the day being
  // watched, so the per-day snapshot wins. Playing live, the honest
  // answer is what they are holding right now, so the freshly polled
  // view wins — and it has to, because ``_stationObs`` in live mode
  // lags: it is seeded from the first day in the replay payload and
  // only advances on replay ticks, so a live board would sit showing
  // day one's empty rack all season.
  //
  // The live figure also covers two cases the snapshots cannot. A
  // session recorded before v1.34 has no ``arms`` key at all, and — the
  // one that actually bites — a turn-lab board is a frozen season whose
  // snapshots predate the rack the lab stamped on it at open time.
  function _osArms(seat) {
    const live = window.__SOC_ARMS__?.[seat];
    const liveOk = live && live.blue != null;
    if (!_showingPast && liveOk) return live;
    if (_armsPinned[seat]) return _armsPinned[seat];
    const snap = _stationObs[seat]?.arms;
    if (snap && snap.blue != null) return _osArmsLessFired(seat, snap);
    return liveOk ? live : null;
  }

  // v1.35 — a night's snapshot is pinned to the POST-ORBITAL rack for
  // the whole night, so on its own the bar would sit still while an EMP
  // flies and only drop at the next dawn. The launches are known per
  // hour, so subtract them and the bar falls on the hour it should.
  //
  // Deliberately applied to the SNAPSHOT branch only. The live poll
  // already reports what the seat is holding now, so subtracting there
  // would count the same launch twice.
  function _osArmsLessFired(seat, snap) {
    const fired = Number(_armsFired[seat]) || 0;
    if (fired <= 0) return snap;
    return { ...snap, blue: Math.max(0, (Number(snap.blue) || 0) - fired) };
  }

  // Re-read how much of each rack has been fired by the tick we are
  // about to draw, and repaint any bar that moved. Playing forward the
  // repaint fades; a scrub snaps, because arriving somewhere is not the
  // same as watching it happen.
  //
  // Zeroed on the day boundaries. At DUSK the cursor still points into
  // the tail of LAST night, so its launches would otherwise be
  // subtracted from a rack that has since been rebuilt — and the whole
  // beat that follows is about that rebuild.
  function _osSyncArmsFired(idx, slot, forward) {
    const atTick = window._osArmsFiredAtTick;
    const boundary = (slot === "dusk" || slot === "dawn" || slot === "resolve");
    const usable = !boundary
      && typeof atTick === "function" && typeof idx === "number";
    for (const seat of _activeSeats) {
      // The pinned reading belongs to the beat that set it, and that
      // beat is over the moment the cursor moves.
      const wasPinned = _armsPinned[seat] != null;
      delete _armsPinned[seat];
      const was = Number(_armsFired[seat]) || 0;
      const now = usable ? (Number(atTick(idx, seat)) || 0) : 0;
      _armsFired[seat] = now;
      if (now !== was || wasPinned) _osRenderArms(seat, forward === true);
    }
  }

  function _osArmsOn() {
    return window.__SOC_WEAPONS_ON__ !== false;
  }

  // Paint a seat's 6-pip arsenal bar. Mirrored to the opposite side of
  // the hull from the vault, which is the point: cargo one corner,
  // ordnance the other.
  // v1.35 — pips fade rather than blink. A pip that has just lit rises
  // into view; one that has just gone dark holds its old glyph for a
  // beat and fades. Without it, a fired EMP is a single frame of change
  // in a corner of the screen, and the whole reason the bar exists is
  // that a rival's ordnance is meant to be noticed.
  //
  // ``animate`` is off for a snap: a scrub or a wholesale refresh is not
  // a thing happening, it is the panel being told where it already is.
  function _osRenderArms(seat, animate) {
    const el = document.querySelector(`[data-os-arms="${seat}"]`);
    if (!el) return;
    const arms = _osArmsOn() ? _osArms(seat) : null;
    if (!arms) { el.innerHTML = ""; delete _armsLit[seat]; return; }
    const cap = Number(arms.cap) || OS_ARMS_CAP_FALLBACK;
    const held = Math.max(0, Number(arms.blue) || 0);
    const lit = held / OS_ARMS_PER_PIP;
    const prev = _armsLit[seat];
    const fade = animate !== false && prev != null && prev !== lit;
    let spent = 0;
    el.innerHTML = _osBar(lit, OS_ARMS_COLOR, OS_ARMS_SEGMENTS)
      .map((p, i) => {
        const wasOn = fade && (prev - i) > 0;
        const nowOn = (lit - i) > 0;
        if (fade && wasOn && !nowOn) {
          // Still drawn as ordnance while it fades — going straight to
          // the empty dot is the blink we are trying to avoid.
          spent += 1;
          return `<span class="os-arms-pip os-arms-pip--out"`
            + ` style="color:${OS_ARMS_COLOR}">\u2588</span>`;
        }
        const cls = (fade && nowOn && !wasOn) ? " os-arms-pip--in" : "";
        return `<span class="os-arms-pip${cls}" style="color:${p.color}">${p.ch}</span>`;
      })
      .join("");
    _armsLit[seat] = lit;
    el.title = `arsenal ${held}/${cap} blue — public to every seat`;
    // Those pips are showing a rack that is already gone; once the fade
    // is done the bar has to settle onto the dim dots underneath.
    if (spent) setTimeout(() => _osRenderArms(seat, false), OS_ARMS_FADE_MS);
  }

  function _osRefreshAllArms(animate) {
    for (const seat of _activeSeats) _osRenderArms(seat, animate);
  }

  // app.js calls this after each poll publishes ``__SOC_ARMS__``. The
  // vault repaints ride on replay ticks and orbit-queue edits, neither
  // of which fires when a rival arms mid-turn — and "everyone can see
  // it immediately" is the entire rule (RULEBOOK §4.9.8), so the bar
  // cannot wait for the next tick to say so.
  window.osRefreshArms = function () {
    if (!_initialized) return;
    _osRefreshAllArms();
  };

  // Render the 3-bar unseen view into a seat's vault-pre: row 0 grey fullness,
  // row 1 blue fissile, row 2 green. Same vault squares, drawn as bars.
  function _osRenderEnemyBars(seat) {
    const el = document.querySelector(`[data-os-vault="${seat}"]`);
    if (!el) return;
    const obs = _stationObs[seat] || {};
    const bars = [
      _osBar(_osFullnessLit(obs), OS_BAR_FULL_COLOR),
      _osBar(_osBlueLit(obs),     OS_BLUE_BURN_COLOR),
      _osBar(_osGreenLit(obs),    OS_GREEN_FILL),
    ];
    const mirror  = OS_RIGHT_SEATS.includes(seat);
    const SIZE    = OS_DIA_SIZE;
    const colBase = mirror ? SIZE - OS_VAULT_COLS : 0;
    let html = "";
    for (let r = 0; r < SIZE; r++) {
      for (let c = 0; c < SIZE; c++) {
        const inVault = r < OS_VAULT_ROWS && c >= colBase && c < colBase + OS_VAULT_COLS;
        if (inVault && bars[r]) {
          const pip = bars[r][c - colBase];
          html += `<span style="color:${pip.color}">${pip.ch}</span>`;
        } else {
          html += " ";
        }
      }
      if (r < SIZE - 1) html += "\n";
    }
    el.innerHTML = html;
    _vaultCells[seat] = null;  // bar mode: no per-cell depletion animation
  }

  // Best source of vault cells for a seat: observed → reconstruction hoard;
  // enemy → grade estimate.
  function _osVaultSourceCells(seat) {
    const observed = _osObserved(seat);
    if (_liveMode && observed && typeof window._socLiveInventory === "function") {
      try {
        const linv = window._socLiveInventory(seat);
        if (linv && Array.isArray(linv.hoard)) return _osHoardCells(linv);
      } catch (e) { /* fall through */ }
    }
    const fn = window._socReconstructVault;
    if (observed && typeof fn === "function") {
      try {
        const inv = fn(seat);
        if (inv && Array.isArray(inv.hoard)) return _osHoardCells(inv);
      } catch (e) { /* fall through to estimate */ }
    }
    return _osEstimateCells(_stationObs[seat] || {});
  }

  // Store + paint a seat's vault cells (the working array we deplete at dusk).
  function _osSetVault(seat, cells) {
    _vaultCells[seat] = cells;
    _osRenderVaultPre(seat, cells, OS_RIGHT_SEATS.includes(seat));
  }

  // Remove one cell of ``type`` from a seat's working vault and repaint.
  function _osRemoveVaultCell(seat, type) {
    const cells = _vaultCells[seat];
    if (!cells || !cells.length) return;
    for (let i = cells.length - 1; i >= 0; i--) {
      const t = cells[i].type;
      if (t === type || ((type === "red" || type === "green") && t === "grey")) {
        cells.splice(i, 1);
        _osSetVault(seat, cells);
        return;
      }
    }
    cells.pop();
    _osSetVault(seat, cells);
  }

  // Full vault as catapult-style density tiles (for the mouseover of stations
  // we observe). One tile per parcel, coloured by resource, density by purity.
  function _osRenderVaultGrid(inv) {
    const hoard = Array.isArray(inv?.hoard) ? inv.hoard : [];
    const cap = Math.max(hoard.length, Number(inv?.hoard_capacity) || 15);
    const wrap = document.createElement("div");
    wrap.className = "os-vault-card";
    const head = document.createElement("div");
    head.className = "os-hover-card-head";
    head.textContent = `VAULT · ${hoard.length}/${cap} hold`;
    wrap.appendChild(head);
    const grid = document.createElement("div");
    grid.className = "os-vault-grid";
    for (let i = 0; i < cap; i++) {
      const p = hoard[i];
      const cell = document.createElement("span");
      cell.className = "os-vault-tile";
      if (p) {
        const tile = Number(p.tile_at_harvest ?? p.origin_tile ?? -1);
        const pur  = Number(p.purity_at_harvest ?? p.origin_purity ?? 0);
        const type = tile === 1 ? "green" : tile === 3 ? "blue" : "red";
        cell.textContent = _osPurityGlyph2(pur);
        cell.style.color = OS_VAULT_COLORS[type];
        cell.style.borderColor = OS_VAULT_COLORS[type];
        cell.dataset.type = type;
      } else {
        cell.textContent = "";
        cell.dataset.empty = "1";
      }
      grid.appendChild(cell);
    }
    wrap.appendChild(grid);
    return wrap;
  }

  // Repaint a single seat's vault from its best current source. Observed seats
  // show real parcel cells; unseen rivals show the 3-bar estimate view.
  function _osRenderVault(seat) {
    if (_osObserved(seat)) _osSetVault(seat, _osVaultSourceCells(seat));
    else _osRenderEnemyBars(seat);
    // Same choke point for the arsenal: every path that repaints a
    // seat's vault has, by definition, just moved that seat's snapshot.
    _osRenderArms(seat);
  }

  // Center of a seat's vault region in screen coords (animation origin). Pass
  // jitter=true to scatter multiple parcels leaving the same vault.
  function _osVaultAnchor(seat, jitter) {
    const stEl = document.querySelector(`[data-os-station="${seat}"]`);
    if (!stEl) return null;
    const wrapEl = stEl.querySelector(".os-diamond-wrap") || stEl;
    const wr = wrapEl.getBoundingClientRect();
    const charW  = wr.width  / OS_DIA_SIZE;
    const charH  = wr.height / OS_DIA_SIZE;
    const mirror = OS_RIGHT_SEATS.includes(seat);
    const cx = mirror
      ? wr.right - OS_VAULT_COLS * 0.5 * charW
      : wr.left  + OS_VAULT_COLS * 0.5 * charW;
    const cy = wr.top + OS_VAULT_ROWS * 0.5 * charH;
    const jx = jitter ? (Math.random() - 0.5) * OS_VAULT_COLS * charW * 0.6 : 0;
    const jy = jitter ? (Math.random() - 0.5) * OS_VAULT_ROWS * charH * 0.6 : 0;
    return { x: cx + jx, y: cy + jy };
  }

  /* ── central RED ship catapult panel ─────────────────────────────── */

  function _osBuildCatapultPanel() {
    const div = document.createElement("div");
    div.className = "os-catapult";
    div.id = "os-catapult";
    const lbl = document.createElement("div");
    lbl.className = "os-catapult-label";
    lbl.textContent = "CATAPULT";
    div.appendChild(lbl);
    const grid = document.createElement("div");
    grid.className = "os-catapult-grid";
    grid.id = "os-catapult-grid";
    for (let i = 0; i < OS_CAT_SLOTS; i++) {
      const sq = document.createElement("span");
      sq.className = "os-cat-slot";
      sq.dataset.osCatSlot = String(i);
      grid.appendChild(sq);
    }
    div.appendChild(grid);
    return div;
  }

  // Alias kept for the briefing hook (osOnOrbitalReportDay).
  function _osRenderCatPips() { _osRenderCatSlots(); }

  /* v1.13 — grow (or shrink back) a catapult grid to hold the whole
   * manifest.
   *
   * Both lattices used to be fixed: 20 RED slots and 12 GREEN, because
   * slots were the scarce thing seats bid over. Settlement is automatic
   * now — every parcel in every vault ships or is dumped — so a night's
   * load can exceed the old lattice (two full 15-parcel vaults is 30).
   * The grid is `repeat(5, 21px)` with implicit rows, so it just needs
   * the right number of cells; we round up to a whole row and never go
   * below ``floor`` so an idle catapult still reads as the same object
   * rather than collapsing to nothing between settlements. */
  function _osEnsureCatSlots(grid, want, floor) {
    if (!grid) return [];
    const need = Math.max(floor, Math.ceil(Math.max(0, want) / 5) * 5);
    let have = grid.childElementCount;
    while (have < need) {
      const sq = document.createElement("span");
      sq.className = "os-cat-slot";
      sq.dataset.osCatSlot = String(have);
      grid.appendChild(sq);
      have += 1;
    }
    while (have > need) {
      grid.lastElementChild?.remove();
      have -= 1;
    }
    return grid.querySelectorAll("[data-os-cat-slot]");
  }

  function _osClearCatFiring() {
    document
      .querySelectorAll(".os-catapult [data-os-cat-slot]")
      .forEach((sq) => { delete sq.dataset.firing; });
  }

  function _osRenderCatSlots() {
    const grid = document.getElementById("os-catapult-grid");
    if (!grid) return;
    const slots = _osEnsureCatSlots(
      grid, (_catData || []).length, OS_CAT_SLOTS,
    );
    slots.forEach((sq, i) => {
      const entry = _catData ? _catData[i] : null;
      if (entry && entry.seat) {
        const color = _seatColor(entry.seat);
        sq.style.setProperty("--os-color", color);
        sq.textContent = _osTierGlyph2(entry.tier);
        sq.dataset.active = "1";
        if (_catFired) sq.dataset.ghost = "1"; else delete sq.dataset.ghost;
      } else {
        sq.style.removeProperty("--os-color");
        sq.textContent = "";
        delete sq.dataset.active;
        delete sq.dataset.ghost;
      }
    });
  }

  /* v1.13 — per-parcel stagger, compressed so the wave lands in about
   * the same wall-clock time no matter how big the load is. With the
   * bid draft gone a night can ship every parcel in both vaults, and a
   * flat per-slot delay would stretch a 30-parcel haul into a five
   * second crawl. Small loads keep the original spacing. */
  function _osCatStagger(n, spacing, budget) {
    return n > 1 ? Math.min(spacing, budget / n) : spacing;
  }

  // Animate parcels flying from each station's vault to the catapult (at DUSK).
  function _osCatLoadAnim() {
    if (!_catData) return;
    const grid = document.getElementById("os-catapult-grid");
    if (!grid) return;
    const slots = _osEnsureCatSlots(grid, _catData.length, OS_CAT_SLOTS);
    const lag = _osCatStagger(_catData.length, 160, 3200);
    for (let i = 0; i < _catData.length && i < slots.length; i++) {
      const entry = _catData[i];
      if (!entry?.seat) continue;
      const anchor = _osVaultAnchor(entry.seat, true);
      if (!anchor) continue;
      const sr = slots[i].getBoundingClientRect();
      const x0 = anchor.x;
      const y0 = anchor.y;
      const x1 = sr.left + sr.width  * 0.5;
      const y1 = sr.top  + sr.height * 0.5;
      _osQueuePtAnim(x0, y0, x1, y1, _osTierGlyph2(entry.tier),
        _seatColor(entry.seat), { dur: 1000, ease: "inout", size: 15, delay: i * lag });
    }
  }

  // Dematerialise each loaded slot in a wave, then leave a low-opacity GHOST.
  function _osCatLaunchAnim() {
    if (!_catData) return;
    const grid = document.getElementById("os-catapult-grid");
    if (!grid) return;
    const slots = _osEnsureCatSlots(grid, _catData.length, OS_CAT_SLOTS);

    // Mark fired NOW so an early dwell cut still ghosts on the next tick.
    _catFired = true;

    const SEQ   = ["▓▓", "▒▒", "░░", "··", ""];
    const FRAME = 90;
    const LAG   = _osCatStagger(_catData.length, 60, 1200);

    let maxEnd = 0;
    for (let i = 0; i < _catData.length && i < slots.length; i++) {
      const entry = _catData[i];
      if (!entry?.seat) continue;
      const sq    = slots[i];
      const start = i * LAG;
      maxEnd = Math.max(maxEnd, start + SEQ.length * FRAME);

      const glyph = _osTierGlyph2(entry.tier);
      const color = _seatColor(entry.seat);
      _catLaunchTimers.push(setTimeout(() => {
        sq.dataset.firing = "1";
        _osSpawnLaunchRise(sq, glyph, color);
      }, start));

      SEQ.forEach((ch, step) => {
        _catLaunchTimers.push(setTimeout(() => {
          sq.textContent = ch;
          delete sq.dataset.firing;
          if (ch === "") {
            delete sq.dataset.active;
            sq.style.removeProperty("--os-color");
          }
        }, start + (step + 1) * FRAME));
      });
    }

    _catLaunchTimers.push(setTimeout(() => {
      // Keep _catData and repaint as a low-opacity GHOST.
      _catFired = true;
      _osRenderCatSlots();
    }, maxEnd + 60));
  }

  // A parcel flung up out of a catapult slot, dematerialising into orbit.
  function _osSpawnLaunchRise(slotEl, glyph, color) {
    if (!slotEl) return;
    const r = slotEl.getBoundingClientRect();
    const x = r.left + r.width * 0.5;
    const y = r.top + r.height * 0.5;
    _osQueuePtAnim(x, y, x + (Math.random() - 0.5) * 24, y - 80, glyph, color,
      { dur: 900, ease: "out", size: 11 });
  }

  /* ── green solar-jettison catapult (right column) ─────────────────── */

  function _osBuildGreenCatapultPanel() {
    const div = document.createElement("div");
    div.className = "os-catapult os-catapult--green";
    div.id = "os-catapult-green";
    const lbl = document.createElement("div");
    lbl.className = "os-catapult-label";
    lbl.textContent = "SOLAR";
    div.appendChild(lbl);
    const grid = document.createElement("div");
    grid.className = "os-catapult-grid";
    grid.id = "os-catapult-green-grid";
    for (let i = 0; i < OS_GREEN_SLOTS; i++) {
      const sq = document.createElement("span");
      sq.className = "os-cat-slot";
      sq.dataset.osCatSlot = String(i);
      grid.appendChild(sq);
    }
    div.appendChild(grid);
    return div;
  }

  function _osRenderGreenCatSlots() {
    const grid = document.getElementById("os-catapult-green-grid");
    if (!grid) return;
    const slots = _osEnsureCatSlots(
      grid, (_greenData || []).length, OS_GREEN_SLOTS,
    );
    slots.forEach((sq, i) => {
      const entry = _greenData ? _greenData[i] : null;
      if (entry && entry.flushed && entry.seat) {
        sq.style.setProperty("--os-color", _seatColor(entry.seat));
        sq.style.color = OS_GREEN_FILL;
        sq.textContent = "\u2588\u2588";
        sq.dataset.active = "1";
        sq.dataset.green = "1";
        if (_greenFired) sq.dataset.ghost = "1"; else delete sq.dataset.ghost;
      } else {
        sq.style.removeProperty("--os-color");
        sq.style.removeProperty("color");
        sq.textContent = "";
        delete sq.dataset.active;
        delete sq.dataset.green;
        delete sq.dataset.ghost;
      }
    });
  }

  // Parcels fly from each flushing station's vault into the green catapult.
  function _osGreenCatLoadAnim() {
    if (!_greenData) return;
    const grid = document.getElementById("os-catapult-green-grid");
    if (!grid) return;
    const slots = _osEnsureCatSlots(grid, _greenData.length, OS_GREEN_SLOTS);
    const lag = _osCatStagger(_greenData.length, 160, 3200);
    for (let i = 0; i < _greenData.length && i < slots.length; i++) {
      const entry = _greenData[i];
      if (!entry?.flushed || !entry?.seat) continue;
      const anchor = _osVaultAnchor(entry.seat, true);
      if (!anchor) continue;
      const sr = slots[i].getBoundingClientRect();
      const x0 = anchor.x;
      const y0 = anchor.y;
      const x1 = sr.left + sr.width  * 0.5;
      const y1 = sr.top  + sr.height * 0.5;
      _osQueuePtAnim(x0, y0, x1, y1, "\u2588\u2588", OS_GREEN_FILL,
        { dur: 1000, ease: "inout", size: 15, delay: i * lag });
    }
  }

  // Dissolve wave when the solar catapult flushes its payload → ghost.
  function _osGreenCatLaunchAnim() {
    if (!_greenData) return;
    const grid = document.getElementById("os-catapult-green-grid");
    if (!grid) return;
    const slots = _osEnsureCatSlots(grid, _greenData.length, OS_GREEN_SLOTS);

    _greenFired = true;  // ghost survives an early dwell cut (see red lane)

    const SEQ   = ["\u2593\u2593", "\u2592\u2592", "\u2591\u2591", "\u00B7\u00B7", ""];
    const FRAME = 90;
    const LAG   = _osCatStagger(_greenData.length, 60, 1200);

    let maxEnd = 0;
    for (let i = 0; i < _greenData.length && i < slots.length; i++) {
      const entry = _greenData[i];
      if (!entry?.flushed || !entry?.seat) continue;
      const sq    = slots[i];
      const start = i * LAG;
      maxEnd = Math.max(maxEnd, start + SEQ.length * FRAME);
      _catLaunchTimers.push(setTimeout(() => {
        sq.dataset.firing = "1";
        _osSpawnLaunchRise(sq, "\u2588\u2588", OS_GREEN_FILL);
      }, start));
      SEQ.forEach((ch, step) => {
        _catLaunchTimers.push(setTimeout(() => {
          sq.textContent = ch;
          delete sq.dataset.firing;
          if (ch === "") {
            delete sq.dataset.active;
            delete sq.dataset.green;
            sq.style.removeProperty("--os-color");
            sq.style.removeProperty("color");
          }
        }, start + (step + 1) * FRAME));
      });
    }

    _catLaunchTimers.push(setTimeout(() => {
      _greenFired = true;
      _osRenderGreenCatSlots();
    }, maxEnd + 60));
  }

  /* ── score ────────────────────────────────────────────────────────── */

  function _osRenderScore(seat, val) {
    const el = document.querySelector(`[data-os-score="${seat}"]`);
    if (el) el.textContent = Number(val).toLocaleString();
  }

  // Snap a seat's readout to an exact value with no tween.
  function _osSetScoreImmediate(seat, val) {
    val = Number(val) || 0;
    _scores[seat] = { score: val };
    _displayScore[seat] = val;
    if (_scoreRaf[seat]) { cancelAnimationFrame(_scoreRaf[seat]); delete _scoreRaf[seat]; }
    const el = document.querySelector(`[data-os-score="${seat}"]`);
    if (el) { el.textContent = val.toLocaleString(); el.classList.remove("os-score--ticking"); }
  }

  // Tween a seat's readout up (or down) to ``target`` over ~650ms.
  function _osAnimateScoreTo(seat, target) {
    target = Number(target) || 0;
    const from = Number(_displayScore[seat]) || 0;
    _scores[seat] = { score: target };
    if (from === target) { _osRenderScore(seat, target); return; }
    if (Math.abs(target - from) < 1) { _osSetScoreImmediate(seat, target); return; }
    if (_scoreRaf[seat]) cancelAnimationFrame(_scoreRaf[seat]);
    const DUR = 650;
    const t0 = performance.now();
    const el = document.querySelector(`[data-os-score="${seat}"]`);
    const step = (now) => {
      const raw = Math.min((now - t0) / DUR, 1);
      const eased = 1 - (1 - raw) * (1 - raw);
      const cur = Math.round(from + (target - from) * eased);
      _displayScore[seat] = cur;
      if (el) el.textContent = cur.toLocaleString();
      if (el) el.classList.toggle("os-score--ticking", raw < 1);
      if (raw < 1) {
        _scoreRaf[seat] = requestAnimationFrame(step);
      } else {
        delete _scoreRaf[seat];
        _displayScore[seat] = target;
        if (el) { el.textContent = target.toLocaleString(); el.classList.remove("os-score--ticking"); }
        _osUpdateLeader();
      }
    };
    _scoreRaf[seat] = requestAnimationFrame(step);
  }

  // Set every seat's readout to the cumulative RED shipped score through
  // ``throughDay``. ``animate`` tweens (forward play); otherwise snaps.
  function _osUpdateScoresForDay(throughDay, animate) {
    if (typeof window._osGetCumulativeScore !== "function") return;
    const cum = window._osGetCumulativeScore(throughDay) || {};
    for (const seat of _activeSeats) {
      const target = Number(cum[seat]) || 0;
      if (animate) _osAnimateScoreTo(seat, target);
      else _osSetScoreImmediate(seat, target);
    }
    _osUpdateLeader();
  }

  /* ── ship-delta "+X" preview badge ────────────────────────────────── */

  // At DUSK show each seat the score it's ABOUT to gain when the catapult
  // fires (through-N minus through-(N-1)) as a "+X" badge under the readout.
  //
  // v1.51 (BUG-12) — the cumulative sum is now NET of that orbit's GREEN
  // charge, so the raw difference would quietly net the two off and a night
  // that shipped 400 RED while dumping four GREEN would advertise "+0".
  // Add the charge back so this badge stays what it says it is — the RED
  // coming in — and let ``_osShowGreenCharge`` bill the green on its own
  // line, the same way the season-closing fold separates them.
  function _osShowShipDeltas(day) {
    if (typeof window._osGetCumulativeScore !== "function") return;
    const prev = window._osGetCumulativeScore(day - 1) || {};
    const cur  = window._osGetCumulativeScore(day) || {};
    const green = (typeof window._osGetDayGreenPenalty === "function")
      ? (window._osGetDayGreenPenalty(day) || {}) : {};
    for (const seat of _activeSeats) {
      const delta = (Number(cur[seat]) || 0) - (Number(prev[seat]) || 0)
        + (Number(green[seat]) || 0);
      const el = document.querySelector(`[data-os-score-delta="${seat}"]`);
      if (!el) continue;
      if (delta > 0) {
        el.textContent = `+${Math.round(delta).toLocaleString()}`;
        el.dataset.pending = "1";
        el.classList.remove("os-score-delta--spent");
      } else {
        el.textContent = "";
        delete el.dataset.pending;
        el.classList.remove("os-score-delta--spent");
      }
    }
  }

  // At LAUNCH the pending "+X" is realised in the score — flash it spent + fade.
  function _osConsumeShipDeltas() {
    for (const seat of _activeSeats) {
      const el = document.querySelector(`[data-os-score-delta="${seat}"]`);
      if (!el || !el.dataset.pending) continue;
      delete el.dataset.pending;
      el.classList.add("os-score-delta--spent");
      setTimeout(() => {
        el.classList.remove("os-score-delta--spent");
        el.textContent = "";
      }, 900);
    }
  }

  function _osClearShipDeltas() {
    for (const seat of _activeSeats) {
      const el = document.querySelector(`[data-os-score-delta="${seat}"]`);
      if (el) {
        el.textContent = "";
        delete el.dataset.pending;
        el.classList.remove("os-score-delta--spent");
      }
    }
  }

  /* ── final settlement "+/-" fold (season-close only) ──────────────── */

  // Wipe every seat's stacked settlement lines (green penalty / red
  // fire-sale). Called at the start of the closing RESOLVE beat and on
  // scrub so the stacks never double up across replays.
  function _osClearSettleStacks() {
    for (const seat of _activeSeats) {
      const el = document.querySelector(`[data-os-settle="${seat}"]`);
      if (el) el.innerHTML = "";
    }
  }

  function _osPushSettleLine(seat, text, kind) {
    const el = document.querySelector(`[data-os-settle="${seat}"]`);
    if (!el) return;
    const line = document.createElement("div");
    line.className = `os-settle-line os-settle-line--${kind}`;
    line.textContent = text;
    el.appendChild(line);
    requestAnimationFrame(() => line.classList.add("os-settle-line--in"));
  }

  // v1.51 (BUG-12) — bill this night's GREEN on its own line, at the launch
  // that charged it. Reuses the season-closing fold's line so a nightly
  // charge and a final one read identically; the stack is wiped by the next
  // non-resolve tick (see ``_osClearSettleStacks``), which gives the line
  // the launch dwell and no longer.
  //
  // This is the ``-100`` a player could never see: the charge landed in the
  // score every orbit and nothing on the platform ever named it.
  function _osShowGreenCharge(day) {
    if (typeof window._osGetDayGreenPenalty !== "function") return;
    const green = window._osGetDayGreenPenalty(day) || {};
    for (const seat of _activeSeats) {
      const n = Number(green[seat]) || 0;
      if (n > 0) _osPushSettleLine(seat, `-${n.toLocaleString()} green`, "green");
    }
  }

  // True when the loaded season carries a settlement breakdown with any
  // non-zero green penalty or vault-red fire-sale to animate.
  function _osHasSettlement() {
    const settle = (typeof window._osGetSettlement === "function")
      ? (window._osGetSettlement() || {}) : {};
    return _activeSeats.some((s) => {
      const b = settle[s];
      return b && (((b.green_penalty | 0) > 0) || ((b.vault_red_loss | 0) > 0));
    });
  }

  // After the shipped "+X" folds in at the closing launch, animate the
  // per-seat GREEN PENALTY (-) then vault-RED FIRE-SALE (+) as separate
  // stacked delta lines, tweening each score to the canonical final.
  // Schedules its own timers (registered for cleanup) and returns the total
  // ms consumed after ``startDelayMs`` so the caller can push the finale.
  function _osPlaySettlement(startDelayMs) {
    const settle = (typeof window._osGetSettlement === "function")
      ? (window._osGetSettlement() || {}) : {};
    // v1.51 (BUG-12) — deliberately NOT clearing the stack here any more.
    // The resolve path already wiped it before scheduling the launch, and
    // the launch has since pushed the final night's own "-N green" charge;
    // clearing again threw that line away a second before the "+red"
    // fire-sale was due to stack under it. Scrubbing is still covered by
    // the tick-level clear.
    const STEP = 1150;
    let cursor = startDelayMs;
    let total = 0;
    const anyGreen = _activeSeats.some(
      (s) => settle[s] && (settle[s].green_penalty | 0) > 0);
    const anyRed = _activeSeats.some(
      (s) => settle[s] && (settle[s].vault_red_loss | 0) > 0);

    // This green is NOT the green ``_osShowGreenCharge`` bills, and the two
    // can never double-debit: that one is the parcels DISPOSED at each
    // orbit (already in the running score, so it is a label only), this one
    // is whatever is still HELD in the vault at season close (not in the
    // running score, so it tweens). Disjoint by construction. In practice
    // settlement flushes the vault before this is read, so ``green_penalty``
    // is 0 on every season in ``seasons/`` and this branch does not fire —
    // kept because a save that ends holding green is still scored for it.
    if (anyGreen) {
      const t = setTimeout(() => {
        for (const s of _activeSeats) {
          const gp = (settle[s] && (settle[s].green_penalty | 0)) || 0;
          if (gp <= 0) continue;
          _osPushSettleLine(s, `-${gp.toLocaleString()} green`, "green");
          _osAnimateScoreTo(s, (Number(_displayScore[s]) || 0) - gp);
        }
      }, cursor);
      _catLaunchTimers.push(t);
      cursor += STEP; total = cursor - startDelayMs;
    }
    if (anyRed) {
      const t = setTimeout(() => {
        for (const s of _activeSeats) {
          const rl = (settle[s] && (settle[s].vault_red_loss | 0)) || 0;
          if (rl <= 0) continue;
          _osPushSettleLine(s, `+${rl.toLocaleString()} red`, "red");
          _osAnimateScoreTo(s, (Number(_displayScore[s]) || 0) + rl);
        }
      }, cursor);
      _catLaunchTimers.push(t);
      cursor += STEP; total = cursor - startDelayMs;
    }
    // Safety snap to the canonical final so rounding never drifts.
    const tf = setTimeout(() => {
      for (const s of _activeSeats) {
        if (settle[s]) _osAnimateScoreTo(s, settle[s].final | 0);
      }
    }, cursor);
    _catLaunchTimers.push(tf);
    total += 700;
    return total;
  }

  function _osUpdateLeader() {
    if (!_activeSeats.length) return;
    let maxScore = -Infinity, leader = null;
    for (const s of _activeSeats) {
      const v = (_displayScore[s] != null) ? _displayScore[s] : (_scores[s]?.score ?? 0);
      if (v > maxScore) { maxScore = v; leader = s; }
    }
    for (const s of _activeSeats) {
      const el = document.querySelector(`[data-os-score="${s}"]`);
      if (!el) continue;
      el.classList.toggle("os-score--lead", s === leader && maxScore > 0);
    }
    // Flash white when the lead changes hands.
    if (leader && _prevLeader !== null && leader !== _prevLeader && maxScore > 0) {
      const el = document.querySelector(`[data-os-score="${leader}"]`);
      if (el) {
        el.classList.remove("os-score--leadflash");
        void el.offsetWidth;
        el.classList.add("os-score--leadflash");
        setTimeout(() => el.classList.remove("os-score--leadflash"), 1200);
      }
    }
    if (leader) _prevLeader = leader;
  }

  /* ── blue (fissile) two-phase burn ────────────────────────────────── */
  //
  // ORBITAL RESOLVE (dusk): blue to be spent is LIFTED out and HELD above the
  // station as ◆ glyphs with a pending "−Xp" badge (review beat).
  // SURFACE RESOLVE (praxis/open): the held blue is CONSUMED (flash + rise/fade)
  // and the badge is realised. Observed seats show exact Σpurity; enemy seats
  // show a relative number of pips from their coarse grade drop.

  // ◆ HELD glyph count. Observed (exact) burn shows ONE ◆ per 100 BLUE spent;
  // a hidden station shows the ACTUAL number of pips its blue bar dropped.
  const OS_BLUE_PER_HELD = 100;   // 1 held ◆ = 100 BLUE (was ~50)
  function _osBlueHeldCount(burn, exact) {
    return exact
      ? Math.max(1, Math.min(12, Math.round(burn / OS_BLUE_PER_HELD)))
      : Math.max(1, Math.min(OS_VAULT_COLS, Math.round(burn)));  // burn = pip drop
  }

  // Blue band (0..5 @ 150/pip) for a seat's blue obs, from the finer server
  // band when present, else the coarse 4-band grade.
  // Blue as a 0..5 pip band (150/pip). Prefer the server's finer band; else
  // derive the SAME band from the exact Σpurity (replay snapshots carry
  // blue.total even for seasons saved before the band field existed); else
  // fall back to the coarse 4-band grade. Deriving from total is what makes
  // the rival blue bar actually MOVE when ~150+ blue is spent (the grade
  // often doesn't cross a boundary).
  function _osBlueBand(b) {
    if (!b) return 0;
    if (b.band != null) return Number(b.band) || 0;
    if (b.total != null) {
      return Math.min(OS_VAULT_COLS, Math.floor((Number(b.total) || 0) / OS_BLUE_PER_PIP));
    }
    return Math.round(OS_BLUE_GRADE_LIT[b.grade] ?? 0);
  }

  // v1.34 — how much of a seat's blue spend this orbit turned into
  // ordnance, read off the public arsenal figure rather than guessed at.
  //
  // This is a SEPARATE delta from the blue burn above, and conflating
  // the two was the trap. Blue can leave a vault for reasons that have
  // nothing to do with weapons, so "blue went down, therefore weapons"
  // would light the cyan flight on a night nobody armed. And the
  // arsenal can move on its own: firing an EMP drains the rack without
  // touching blue at all. Only the arms delta knows which happened.
  //
  // Returns positive when the rack GREW (blue became ordnance) and
  // negative when it SHRANK (a weapon was fired).
  function _osArmsDelta(prevObs, nextObs) {
    const a = prevObs?.arms, b = nextObs?.arms;
    // A snapshot from before v1.34 has no arms key at all. Absent is not
    // zero: treating it as zero would read every first post-upgrade
    // reading as a whole rack being built in one night.
    if (!a || !b || a.blue == null || b.blue == null) return 0;
    return (Number(b.blue) || 0) - (Number(a.blue) || 0);
  }

  // Returns the length of the longest beat it staged. DUSK holds the
  // camera on the stations for at least that long: an arming beat cut
  // off halfway is the bug this whole rework exists to fix, and a
  // catapult-only dwell is not long enough to cover one (v1.35).
  function _osBlueStageForDay(day, animate) {
    const d = window._socOrbitalData; if (!d) return 0;
    let beatEnd = 0;
    // The orbit that resolves at DUSK(N) consumes blue between the END of
    // night N-1 (pre[N-1]) and the POST-orbit reading (post[N]). blue.total
    // includes the bank stipend, so this delta covers bank spend too.
    const pre  = d.stationObsByDay?.[String(day - 1)]?.pre  || {};
    const post = d.stationObsByDay?.[String(day)]?.post || {};
    for (const seat of _activeSeats) {
      const observed = _osObserved(seat);
      const pb = pre[seat]?.blue  || {};
      const qb = post[seat]?.blue || {};
      let burn = 0, exact = false;
      if (observed && pb.total != null && qb.total != null) {
        // Exact Σpurity burned (bank spend included).
        burn  = Math.max(0, (Number(pb.total) || 0) - (Number(qb.total) || 0));
        exact = true;
      } else {
        // Hidden station: burn = ACTUAL pips its blue bar dropped this turn.
        burn = Math.max(0, _osBlueBand(pb) - _osBlueBand(qb));
      }
      const armed = _osArmsDelta(pre[seat], post[seat]);
      const postArms = post[seat]?.arms || null;
      if (burn > 0) {
        beatEnd = Math.max(beatEnd, _osBlueStageOne(
          seat, burn, exact, animate, armed, postArms) || 0);
      } else if (armed !== 0) {
        _osArmsOnlyBeat(seat, animate, postArms);
      }
    }
    return beatEnd;
  }

  // The rack moved but no blue left the vault. Going UP that is a hidden
  // seat whose coarse blue band happened not to cross a pip boundary;
  // going DOWN it is ordnance that was fired during the night just
  // played — and that already had its beat, hour by hour, as the bar
  // fell under _armsFired. So this only ever settles the bar.
  function _osArmsOnlyBeat(seat, animate, postArms) {
    if (postArms) _armsPinned[seat] = postArms;
    _osRenderArms(seat, animate);
  }

  // Lift a seat's committed blue out of the vault and hold it above the
  // station. Returns how long the beat it scheduled runs for, so the
  // caller can hold the camera on the stations until it has finished.
  function _osBlueStageOne(seat, burn, exact, animate, armed, postArms) {
    let beatEnd = 0;
    const stEl = document.querySelector(`[data-os-station="${seat}"]`);
    if (!stEl) return beatEnd;
    const wrapEl = stEl.querySelector(".os-diamond-wrap") || stEl;
    const n = _osBlueHeldCount(burn, exact);
    // v1.34 — how many of the held ◆ are becoming ordnance rather than
    // simply being spent. That many fly into the arsenal bar; the rest
    // drift up and fade, because nothing was armed with them.
    _blueHeld[seat] = { count: n, burn, exact, armed: Number(armed) || 0, postArms };
    _blueHeld[seat].toArms = _osArmedGlyphCount(_blueHeld[seat]);

    let held = wrapEl.querySelector(`[data-os-blue-held="${seat}"]`);
    if (!held) {
      held = document.createElement("div");
      held.className = "os-blue-held";
      held.dataset.osBlueHeld = seat;
      wrapEl.appendChild(held);
    }
    held.innerHTML = "";
    for (let i = 0; i < n; i++) {
      const g = document.createElement("span");
      g.textContent = "\u25C6";
      if (animate) { g.style.animationDelay = (i * 60) + "ms"; g.classList.add("os-blue-held--in"); }
      held.appendChild(g);
    }

    // Lift ◆ up from the vault to the held row (forward play only). We do NOT
    // remove vault cells here: blue is spent bank-FIRST (a scalar, not a hoard
    // parcel), and any vault BLUE consumed already vanished from the
    // post-orbital reconstruction the vault renders from.
    if (animate) {
      const anchor = _osVaultAnchor(seat, false);
      const hr = held.getBoundingClientRect();
      for (let i = 0; i < n; i++) {
        if (!anchor) break;
        const fx = hr.left + hr.width * (n > 1 ? (0.15 + 0.7 * i / (n - 1)) : 0.5);
        _vaultSettleTimers.push(setTimeout(() => {
          _osQueuePtAnim(anchor.x, anchor.y, fx, hr.top + hr.height * 0.5,
            "\u25C6", OS_BLUE_BURN_COLOR,
            { dur: 680, ease: "out", size: 12, tag: "fissile" });
        }, i * OS_LOAD_STAGGER + 120));
      }
      // v1.35 — and once it has landed in the held row, the part of it
      // that became ordnance goes cyan and flies into the arsenal bar.
      // This used to wait for PRAXIS, and the waiting was the whole
      // problem: by praxis the night is running, the camera has left
      // the stations, and a weapon bought at dusk is often fired the
      // same night — so the one beat that says "they built something"
      // went past unseen. The warning has to land while the player is
      // still looking at the stations, which is now.
      if (_blueHeld[seat].toArms > 0) {
        const liftEnd = (n - 1) * OS_LOAD_STAGGER + 120 + 680;
        _vaultSettleTimers.push(setTimeout(
          () => _osArmsConvert(seat), liftEnd + 140));
        beatEnd = liftEnd + 140 + _osArmsConvertMs(_blueHeld[seat].toArms);
      }
    } else if (_blueHeld[seat].toArms > 0) {
      // Scrubbed onto this dusk. The post-orbital snapshot is already in
      // place, so there is nothing to animate — just do not fly these
      // glyphs anywhere later.
      _blueHeld[seat].toArms = 0;
      _osRenderArms(seat, false);
    }

    // Pending "−Xp" badge (exact) or relative "◆ pips" (enemy). v1.35 —
    // cyan when the blue was armed rather than merely spent. Same
    // number, different substance: −200p in blue is an expense, −200p
    // in cyan is a missile with your name on it.
    const badge = stEl.querySelector(`[data-os-blue-burn="${seat}"]`);
    if (badge) {
      badge.textContent = exact
        ? `\u25C6 \u2212${Math.round(burn).toLocaleString()}p`
        : `\u25C6 ${"\u25AA".repeat(n)}`;
      badge.dataset.pending = "1";
      badge.classList.remove("os-blue-burn--spent");
      badge.classList.toggle("os-blue-burn--armed", (Number(armed) || 0) > 0);
    }
    return beatEnd;
  }

  // Wall-clock length of the arming beat for ``n`` glyphs, measured from
  // the moment it starts to a breath after the last pip lights.
  function _osArmsConvertMs(n) {
    return OS_ARMS_LIFT_MS + (n - 1) * 70
      + (n - 1) * 60 + OS_ARMS_FLY_MS + 260;
  }

  // How many of the held ◆ represent blue that became ordnance. The
  // glyphs are a coarse rendering of an amount, so this converts back
  // through whatever each one is worth on this seat.
  function _osArmedGlyphCount(info) {
    const armedBlue = Math.max(0, Number(info.armed) || 0);
    if (armedBlue <= 0) return 0;
    const perGlyph = info.exact
      ? (info.burn / Math.max(1, info.count))
      : OS_BLUE_PER_PIP;
    return Math.min(info.count,
      Math.max(1, Math.round(armedBlue / Math.max(1, perGlyph))));
  }

  // The arming beat, in two movements. First the held ◆ turn cyan where
  // they hover — same glyphs, same place, the only thing that changed is
  // what they ARE. Then they fly into the arsenal bar and light it, one
  // pip per glyph as each arrives, so the bar visibly fills rather than
  // snapping to a new reading.
  function _osArmsConvert(seat) {
    const info = _blueHeld[seat];
    if (!info || !info.toArms) return;
    const n = info.toArms;
    info.toArms = 0;              // claimed — the consume beat leaves these be
    const postArms = info.postArms;
    const armedBlue = Math.max(0, Number(info.armed) || 0);

    const stEl = document.querySelector(`[data-os-station="${seat}"]`);
    const held = stEl?.querySelector(`[data-os-blue-held="${seat}"]`);
    const glyphs = held
      ? Array.from(held.querySelectorAll("span")).slice(0, n)
      : [];
    if (!glyphs.length) {
      if (postArms) _armsPinned[seat] = postArms;
      _osRenderArms(seat, true);
      return;
    }

    glyphs.forEach((g, i) => {
      g.dataset.armed = "1";
      g.style.animationDelay = (i * 70) + "ms";
      g.classList.remove("os-blue-held--in");
      g.classList.add("os-blue-held--arming");
    });

    // The bar's reading has to come from the POST-orbital snapshot, and
    // at dusk _stationObs is still parked on the pre-orbital one so the
    // vault can drain into it. Pin the figure the glyphs are carrying.
    const endBlue = postArms ? (Number(postArms.blue) || 0) : armedBlue;
    const startBlue = Math.max(0, endBlue - armedBlue);
    const base = postArms || { cap: OS_ARMS_CAP_FALLBACK };

    _vaultSettleTimers.push(setTimeout(() => {
      const pips = _osArmsPipRects(seat);
      glyphs.forEach((g, i) => {
        const r = g.getBoundingClientRect();
        const t = pips[Math.min(Math.max(0, pips.length - 1), i)];
        if (t) {
          _osQueuePtAnim(r.left + r.width * 0.5, r.top + r.height * 0.5,
            t.x, t.y, "\u25C6", OS_ARMS_COLOR,
            { dur: OS_ARMS_FLY_MS, ease: "out", size: 12,
              delay: i * 60, tag: "fissile" });
        }
        g.remove();
        // Light the pip as this ◆ lands, not as it leaves.
        _vaultSettleTimers.push(setTimeout(() => {
          const blue = (i === n - 1)
            ? endBlue
            : startBlue + Math.round(armedBlue * (i + 1) / n);
          _armsPinned[seat] = { ...base, blue };
          _osRenderArms(seat, true);
        }, i * 60 + OS_ARMS_FLY_MS));
      });
    }, OS_ARMS_LIFT_MS + (n - 1) * 70));
  }

  // Screen positions of a seat's arsenal pips, in fill order, so a ◆ in
  // flight can be aimed at the pip it is about to light.
  function _osArmsPipRects(seat) {
    const el = document.querySelector(`[data-os-arms="${seat}"]`);
    if (!el) return [];
    const out = [];
    el.querySelectorAll("span").forEach((s) => {
      const r = s.getBoundingClientRect();
      out.push({ x: r.left + r.width * 0.5, y: r.top + r.height * 0.5 });
    });
    return out;
  }

  // v1.35 — ordnance leaving the rack used to fly cyan ◆ out of the bar
  // here. It doesn't any more: the bar now falls on the hour the weapon
  // launches rather than at the next dusk, and at that point the pip
  // fading out IS the event. Flying glyphs off a station the camera has
  // left, a whole day after the fact, only ever said it twice.

  function _osBlueConsumeAll() {
    for (const seat of _activeSeats) if (_blueHeld[seat]) _osBlueConsumeOne(seat);
  }

  function _osBlueConsumeOne(seat) {
    const stEl = document.querySelector(`[data-os-station="${seat}"]`);
    const info = _blueHeld[seat] || {};
    const frac = info.count ? Math.min(1, info.count / 10) : 0.5;

    if (stEl) {
      const flash = stEl.querySelector(`[data-os-blue-flash="${seat}"]`);
      if (flash) {
        flash.style.setProperty("--os-burn", (0.35 + frac * 0.65).toFixed(2));
        flash.classList.remove("os-blue-flash--fire");
        void flash.offsetWidth;
        flash.classList.add("os-blue-flash--fire");
        setTimeout(() => flash.classList.remove("os-blue-flash--fire"), 1100);
      }
      const held = stEl.querySelector(`[data-os-blue-held="${seat}"]`);
      if (held) {
        // v1.35 — by the time we get here the ordnance has usually
        // already flown: the arming beat runs at DUSK now, so what is
        // left hovering is blue that was merely SPENT, and it keeps the
        // original drift-up-and-fade. Pretending otherwise would be a
        // lie told in animation.
        //
        // ``toArms`` is only still set on the short paths that stage and
        // consume within a second of each other (final settlement, live
        // resolve), where the dusk timer never got to fire. Same beat,
        // just triggered late — and it marks its own glyphs, so the fade
        // below steps around them.
        const pending = info.toArms || 0;
        if (pending > 0) _osArmsConvert(seat);
        held.querySelectorAll("span").forEach((g, i) => {
          if (g.dataset.armed) return;
          const r = g.getBoundingClientRect();
          _osQueuePtAnim(r.left + r.width * 0.5, r.top + r.height * 0.5,
            r.left + (Math.random() - 0.5) * 30, r.top - (30 + Math.random() * 44),
            "\u25C6", OS_BLUE_BURN_COLOR,
            { dur: 720 + Math.random() * 240, ease: "out", size: 12,
              delay: i * 50, tag: "fissile" });
          g.style.animationDelay = (i * 50) + "ms";
          g.classList.add("os-blue-held--out");
        });
        const drop = pending > 0
          ? OS_ARMS_LIFT_MS + pending * 70 + OS_ARMS_FLY_MS + 200
          : 950;
        setTimeout(() => { held.remove(); }, Math.max(950, drop));
      }
      const badge = stEl.querySelector(`[data-os-blue-burn="${seat}"]`);
      if (badge) {
        delete badge.dataset.pending;
        badge.classList.add("os-blue-burn--spent");
        setTimeout(() => {
          badge.classList.remove("os-blue-burn--spent", "os-blue-burn--armed");
          badge.textContent = "";
        }, 1000);
      }
    }
    delete _blueHeld[seat];
  }

  // Remove any staged/held blue with no animation (dawn / scrub / new day).
  function _osClearBlueHeld() {
    // v1.34 — drop any fissile ◆ still in flight as well. A scrub
    // backwards used to leave the canvas mid-animation, which was
    // survivable when every particle only faded; now that some of them
    // END somewhere (lighting an arsenal pip), a stranded flight reads
    // as a build that did not happen. Snapping the bars to the
    // authoritative state afterwards is the other half of that.
    //
    // Filtered by tag rather than cleared wholesale: this runs at dawn
    // and on every scrub, and a catapult launch queued in the same tick
    // has nothing to do with blue.
    for (let i = _anims.length - 1; i >= 0; i--) {
      if (_anims[i].tag === "fissile") _anims.splice(i, 1);
    }
    _armsPinned = {};
    _osRefreshAllArms(false);
    for (const seat of _activeSeats) {
      const stEl = document.querySelector(`[data-os-station="${seat}"]`);
      if (stEl) {
        const held = stEl.querySelector(`[data-os-blue-held="${seat}"]`);
        if (held) held.remove();
        const badge = stEl.querySelector(`[data-os-blue-burn="${seat}"]`);
        if (badge) {
          badge.textContent = "";
          delete badge.dataset.pending;
          badge.classList.remove("os-blue-burn--spent", "os-blue-burn--armed");
        }
      }
      delete _blueHeld[seat];
    }
  }

  /* ── phase ────────────────────────────────────────────────────────── */

  function _osSetPhase(phase) {
    document.querySelectorAll(".os-station").forEach(el => {
      el.classList.toggle("os-station--night", phase === "night");
      el.classList.toggle("os-station--day",   phase === "day");
    });
  }

  /* ── point-to-point canvas animation ────────────────────────────── */

  // ``colorEnd`` (v1.34) tints the glyph from ``color`` to ``colorEnd``
  // across the flight. Used by the arsenal build beat, where a ◆ of blue
  // arrives at the rack as cyan — the colour change IS the mechanic, so
  // it has to happen in transit rather than at either endpoint.
  function _osQueuePtAnim(x0, y0, x1, y1, glyph, color, opts) {
    _osEnsureCanvas();
    _anims.push({
      glyph, color, x0, y0, x1, y1,
      colorEnd: opts?.colorEnd ?? null,
      tag:   opts?.tag   ?? "",
      dur:   opts?.dur   ?? 600,
      ease:  opts?.ease  ?? "inout",
      size:  opts?.size  ?? 8,
      start: performance.now() + (opts?.delay ?? 0),
      slot: 0, seat: "_cat", dir: "_",
    });
    if (!_animRaf) _animRaf = requestAnimationFrame(_osAnimLoop);
  }

  // #rrggbb → #rrggbb across t. Only ever fed the two literals above, so
  // it assumes 6-digit hex rather than parsing colour generally.
  function _osLerpHex(a, b, t) {
    const pa = parseInt(a.slice(1), 16), pb = parseInt(b.slice(1), 16);
    if (!Number.isFinite(pa) || !Number.isFinite(pb)) return a;
    const m = (sh) => {
      const ca = (pa >> sh) & 255, cb = (pb >> sh) & 255;
      return Math.round(ca + (cb - ca) * t);
    };
    return `rgb(${m(16)},${m(8)},${m(0)})`;
  }

  /* ── animation canvas ─────────────────────────────────────────────── */

  let _animCvs = null;
  let _animCtx = null;
  let _animRaf = null;
  const _anims = [];
  const _animSlot = {};

  function _osEnsureCanvas() {
    if (_animCvs) return;
    _animCvs = document.createElement("canvas");
    // Named like the other station surfaces so a probe can find it: the
    // blue-to-cyan flight (§4.9.8) only exists as pixels here, and there
    // is nothing else on the page to read it off.
    _animCvs.setAttribute("data-os-anim", "1");
    _animCvs.style.cssText = "position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:9500";
    _animCvs.width  = window.innerWidth;
    _animCvs.height = window.innerHeight;
    document.body.appendChild(_animCvs);
    _animCtx = _animCvs.getContext("2d");
    window.addEventListener("resize", () => {
      if (!_animCvs) return;
      _animCvs.width  = window.innerWidth;
      _animCvs.height = window.innerHeight;
    });
  }

  function _osEase(raw, kind) {
    if (kind === "in")    return raw * raw;
    if (kind === "out")   return 1 - (1 - raw) * (1 - raw);
    if (kind === "inout") return raw * raw * (3 - 2 * raw);
    return raw;
  }

  function _osQueueAnim(seat, glyph, color, side, dir, opts) {
    _osEnsureCanvas();

    const stEl  = document.querySelector(`[data-os-station="${seat}"]`);
    const mapEl = document.querySelector(".cc-map-area");
    if (!stEl || !mapEl) return;

    const wrapEl = stEl.querySelector(".os-diamond-wrap") || stEl;
    const rr = wrapEl.getBoundingClientRect();
    const mr = mapEl.getBoundingClientRect();

    const slot = _animSlot[seat + dir] || 0;
    _animSlot[seat + dir] = slot + 1;

    const yOff = opts?.yOff ?? 0;
    // Station launch point: the diamond edge facing the board.
    const sx = side === "left" ? rr.right : rr.left;
    const sy = rr.top + rr.height * 0.5;

    // Craft travel on a QUADRATIC BEZIER (arc), not a straight line into the
    // board. The endpoint + control depend on the table size:
    //   • 2P — arc UP the board's edge, bulging OUT into the side gutter, so
    //     craft NEVER cross into the play area.
    //   • 4P — the corner platforms fire toward the board CENTRE.
    const fourPlayer = _activeSeats.length >= 3;
    let mx, my, cx, cy;
    if (fourPlayer) {
      // Corner platforms sweep along their side's black border toward the
      // MIDDLE of that edge — they NEVER cross into the map (the surface hit
      // is already drawn on the board itself; this only shows the launch).
      mx = side === "left" ? mr.left + 10 : mr.right - 10;   // hug the board edge
      my = mr.top + mr.height * 0.5;                         // toward mid of the edge
      const fan = ((slot % 3) - 1) * mr.height * 0.04;       // fan concurrent craft
      cx = sx + (side === "left" ? -74 : 74);                // bulge OUT (gutter)
      cy = (sy + my) / 2 + fan;
    } else {
      mx = side === "left" ? mr.left + 10 : mr.right - 10;   // hug the board edge
      my = mr.top + mr.height * 0.06;                        // arc up to the top
      cx = sx + (side === "left" ? -70 : 70);                // bulge OUT (gutter)
      cy = (sy + my) / 2 - mr.height * 0.12;                 // bulge upward
    }
    my += yOff;

    let x0, y0, x1, y1;
    if (dir === "in") { x0 = mx; y0 = my; x1 = sx; y1 = sy; }
    else              { x0 = sx; y0 = sy; x1 = mx; y1 = my; }

    _anims.push({
      glyph, color, x0, y0, x1, y1, cx, cy, curved: true,
      dur:   opts?.dur   ?? 600,
      ease:  opts?.ease  ?? "inout",
      size:  opts?.size  ?? 9,
      label: opts?.label ?? null,   // small identifying tag ("probe"/"harvester")
      slot, seat, dir,
      start: performance.now() + (opts?.delay ?? 0),
    });

    if (!_animRaf) _animRaf = requestAnimationFrame(_osAnimLoop);
  }

  function _osAnimLoop(now) {
    if (!_animCtx) return;
    _animCtx.clearRect(0, 0, _animCvs.width, _animCvs.height);

    _animCtx.textAlign    = "center";
    _animCtx.textBaseline = "middle";

    const alive = [];
    for (const a of _anims) {
      const elapsed = now - a.start;
      if (elapsed < 0) { alive.push(a); continue; }
      const raw = Math.min(elapsed / a.dur, 1);
      const t   = _osEase(raw, a.ease);
      let x, y, tanx, tany;
      if (a.curved) {
        const mt = 1 - t;
        x = mt * mt * a.x0 + 2 * mt * t * a.cx + t * t * a.x1;
        y = mt * mt * a.y0 + 2 * mt * t * a.cy + t * t * a.y1;
        tanx = 2 * mt * (a.cx - a.x0) + 2 * t * (a.x1 - a.cx);
        tany = 2 * mt * (a.cy - a.y0) + 2 * t * (a.y1 - a.cy);
      } else {
        x = a.x0 + (a.x1 - a.x0) * t;
        y = a.y0 + (a.y1 - a.y0) * t;
        tanx = a.x1 - a.x0; tany = a.y1 - a.y0;
      }
      const len = Math.sqrt(tanx * tanx + tany * tany) || 1;
      const ux  = -tanx / len, uy = -tany / len;

      // v1.34 — the in-flight tint. The trail carries it too, so the
      // wake behind a ◆ heading for the rack turns cyan with it.
      const ink = a.colorEnd ? _osLerpHex(a.color, a.colorEnd, t) : a.color;

      const trailSize = Math.max(6, a.size * 0.65);
      _animCtx.font = `${trailSize}px 'Courier New',monospace`;
      for (let dd = 1; dd <= 3; dd++) {
        _animCtx.globalAlpha = Math.max(0, 0.25 - dd * 0.07);
        _animCtx.fillStyle   = ink;
        _animCtx.fillText("·", x + ux * dd * 8, y + uy * dd * 8);
      }

      _animCtx.font        = `${a.size}px 'Courier New',monospace`;
      _animCtx.globalAlpha = raw > 0.95 ? 1 - (raw - 0.95) * 20 : 1;
      _animCtx.fillStyle   = ink;
      _animCtx.fillText(a.glyph, x, y);

      // Identifying tag riding just below the craft ("probe" / "harvester" …).
      if (a.label) {
        const lf = Math.max(7, Math.round(a.size * 0.5));
        _animCtx.font = `${lf}px 'Courier New',monospace`;
        _animCtx.globalAlpha = (raw > 0.9 ? 1 - (raw - 0.9) * 10 : 1) *
          (raw < 0.12 ? raw / 0.12 : 1);   // full opacity, fade in at spawn / out at end
        _animCtx.fillStyle = a.color;
        _animCtx.fillText(a.label, x, y + a.size * 0.6 + lf * 0.8);
      }
      _animCtx.globalAlpha = 1;

      if (raw < 1) {
        alive.push(a);
      } else {
        if ((_animSlot[a.seat + a.dir] || 0) > 0) _animSlot[a.seat + a.dir]--;
      }
    }

    _anims.length = 0;
    _anims.push(...alive);

    if (_anims.length > 0) {
      _animRaf = requestAnimationFrame(_osAnimLoop);
    } else {
      _animCtx.clearRect(0, 0, _animCvs.width, _animCvs.height);
      _animRaf = null;
    }
  }

  /* ── hover cards ─────────────────────────────────────────────────── */

  const _cards     = {};
  const _hideDelay = {};

  // Resolve the day the hover cards should read. Prefer the tick-tracked day
  // (correct in replay AND live, where the replay cursor is stale).
  function _osDay() {
    // v1.34 — in live play prefer the newest day with readings. The
    // replay rewind that runs at boot parks ``_currentDay`` on day 1,
    // so a page opened mid-season used to show day-1 readings beside a
    // station drawn from today; the arsenal row made that contradiction
    // legible (0/600 next to two lit pips) rather than introducing it.
    if (!_showingPast && typeof window._osLatestObsDay === "function") {
      const latest = Number(window._osLatestObsDay()) || 0;
      if (latest > 0) return latest;
    }
    if (_currentDay) return _currentDay;
    const live = (typeof window._osGetCurrentReplayDay === "function")
      ? window._osGetCurrentReplayDay()
      : null;
    return live || 1;
  }

  function _osBindHover() {
    // Station panels: hover = THIS seat's station + orbital observations;
    // click = full pre-orbital recap (all seats) via the report modal.
    for (const seat of _activeSeats) {
      const el = document.querySelector(`[data-os-station="${seat}"]`);
      if (!el) continue;
      const key = "st_" + seat;
      el.style.cursor = "pointer";
      el.addEventListener("mouseenter", () => {
        clearTimeout(_hideDelay[key]);
        _osShowCard(key, () => _osBuildStationCard(seat), el);
      });
      el.addEventListener("mouseleave", () => {
        _hideDelay[key] = setTimeout(() => _osHideCard(key), 120);
      });
      el.addEventListener("click", () => {
        if (typeof window._osOpenReport === "function")
          window._osOpenReport("recap", _osDay());
      });
    }

    // RED ship catapult: hover = catapult grid; click = full briefing.
    const catEl = document.getElementById("os-catapult");
    if (catEl) {
      const key = "catapult";
      catEl.style.cursor = "pointer";
      catEl.addEventListener("mouseenter", () => {
        clearTimeout(_hideDelay[key]);
        _osShowCard(key, _osBuildCatCard, catEl);
      });
      catEl.addEventListener("mouseleave", () => {
        _hideDelay[key] = setTimeout(() => _osHideCard(key), 120);
      });
      catEl.addEventListener("click", () => {
        if (typeof window._osOpenReport === "function")
          window._osOpenReport("briefing", _osDay());
      });
    }

    // GREEN solar catapult hover.
    const greenEl = document.getElementById("os-catapult-green");
    if (greenEl) {
      const key = "catapult-green";
      greenEl.style.cursor = "pointer";
      greenEl.addEventListener("mouseenter", () => {
        clearTimeout(_hideDelay[key]);
        _osShowCard(key, _osBuildGreenCatCard, greenEl);
      });
      greenEl.addEventListener("mouseleave", () => {
        _hideDelay[key] = setTimeout(() => _osHideCard(key), 120);
      });
      greenEl.addEventListener("click", () => {
        if (typeof window._osOpenReport === "function")
          window._osOpenReport("briefing", _osDay());
      });
    }
  }

  function _osShowCard(key, builder, anchorEl) {
    _osHideCard(key);
    const card = builder();
    if (!card) return;
    document.body.appendChild(card);
    _cards[key] = card;

    card.addEventListener("mouseenter", () => clearTimeout(_hideDelay[key]));
    card.addEventListener("mouseleave", () => {
      _hideDelay[key] = setTimeout(() => _osHideCard(key), 80);
    });

    const ar   = anchorEl.getBoundingClientRect();
    const onLeft = ar.left < window.innerWidth / 2;
    requestAnimationFrame(() => {
      const ch = card.offsetHeight;
      const top = Math.max(4, Math.min(ar.top, window.innerHeight - ch - 8));
      card.style.top = top + "px";
      if (onLeft) {
        card.style.left  = (ar.right + 6) + "px";
      } else {
        card.style.right = (window.innerWidth - ar.left + 6) + "px";
      }
    });
  }

  function _osHideCard(key) {
    if (_cards[key]) { _cards[key].remove(); delete _cards[key]; }
  }

  // Station hover: ONE seat's STATION OBSERVATIONS (pre) + ORBITAL OBSERVATIONS
  // for the current day. For observed stations, show the full vault grid too.
  function _osBuildStationCard(seat) {
    const day = _osDay();
    if (!day || !window._osRenderStationObs || !window._osRenderOrbitalObs) return null;
    const card = document.createElement("div");
    card.className = "os-hover-card";
    const observed = _osObserved(seat);
    if (observed && typeof window._socReconstructVault === "function") {
      try {
        const vg = _osRenderVaultGrid(window._socReconstructVault(seat));
        if (vg) card.appendChild(vg);
      } catch (e) { /* no vault snapshot — skip */ }
    }
    try {
      card.appendChild(window._osRenderStationObs(
        day, "pre", "STATION OBSERVATIONS · end of Nox", seat
      ));
      card.appendChild(window._osRenderOrbitalObs(day, seat));
    } catch (e) {
      card.textContent = "// data not available";
    }
    const hint = document.createElement("div");
    hint.className = "os-hover-card-hint";
    hint.textContent = "click — full pre-orbital recap";
    card.appendChild(hint);
    return card;
  }

  // Catapult hover: shipping catapult grid for the current day.
  function _osBuildCatCard() {
    const day = _osDay();
    if (!day || !window._osGetCatapultBlob || !window._osRenderCatGrid) return null;
    const blob = window._osGetCatapultBlob(day);
    if (!blob) return null;
    const cat   = blob.catapult || {};
    const slots = Array.isArray(cat.slot_assignments) ? cat.slot_assignments : [];

    const card = document.createElement("div");
    card.className = "os-hover-card os-hover-card--cat";

    const head = document.createElement("div");
    head.className = "os-hover-card-head";
    head.textContent =
      `SHIPPING CATAPULT · ${cat.slots_awarded ?? 0}/${cat.slots_total ?? 20} slots shipped`;
    card.appendChild(head);

    if (!slots.length) {
      const dim = document.createElement("div");
      dim.className = "os-hover-card-dim";
      dim.textContent = "// quiet orbit";
      card.appendChild(dim);
    } else {
      card.appendChild(window._osRenderCatGrid(slots));
      if (window._osRenderCatLegend) {
        const allSeats = window._osGetActiveSeatsList ? window._osGetActiveSeatsList() : [];
        card.appendChild(window._osRenderCatLegend(slots, allSeats));
      }
    }

    const hint = document.createElement("div");
    hint.className = "os-hover-card-hint";
    hint.textContent = "click — full briefing";
    card.appendChild(hint);
    return card;
  }

  // Green solar-jettison hover: the flush lattice for the current day.
  function _osBuildGreenCatCard() {
    const day = _osDay();
    if (!day || !window._osGetCatapultBlob || !window._osRenderGreenGrid) return null;
    const blob = window._osGetCatapultBlob(day);
    if (!blob) return null;
    const jet   = blob.jettison || {};
    const slots = Array.isArray(jet.slot_assignments) ? jet.slot_assignments : [];
    const total = Number(jet.slots_total) || 12;

    const card = document.createElement("div");
    card.className = "os-hover-card os-hover-card--cat";

    const head = document.createElement("div");
    head.className = "os-hover-card-head";
    head.textContent =
      `SOLAR CATAPULT · ${jet.slots_used ?? 0}/${total} flushed`;
    card.appendChild(head);

    card.appendChild(window._osRenderGreenGrid(slots, total));
    if (window._osRenderCatLegend) {
      const allSeats = window._osGetActiveSeatsList ? window._osGetActiveSeatsList() : [];
      card.appendChild(window._osRenderCatLegend(slots, allSeats));
    }

    const hint = document.createElement("div");
    hint.className = "os-hover-card-hint";
    hint.textContent = "click — full briefing";
    card.appendChild(hint);
    return card;
  }

  /* ── fit map to available width ──────────────────────────────────── */

  function _osFitMapZoom(attempt) {
    const mapArea     = document.querySelector(".cc-map-area");
    const viewport    = document.querySelector(".cc-map-viewport");
    const mapGrid     = document.querySelector("#map-player .map-grid");
    const gridW       = mapGrid && mapGrid.getBoundingClientRect().width;
    if (!mapArea || !viewport || !gridW) {
      if ((attempt || 0) < 30) setTimeout(() => _osFitMapZoom((attempt || 0) + 1), 80);
      return;
    }
    const currentFont = parseFloat(
      getComputedStyle(document.documentElement).getPropertyValue("--map-font-size")
    ) || 14;
    const gridH   = mapGrid.scrollHeight || mapGrid.getBoundingClientRect().height;
    const targetW = (currentFont * mapArea.clientWidth)   / gridW;
    const targetH = (currentFont * viewport.clientHeight) / gridH;
    const target  = Math.min(targetW, targetH);
    if (typeof window._osSetMapZoomDefault === "function") window._osSetMapZoomDefault(target);
    if (typeof window._osApplyMapZoom      === "function") window._osApplyMapZoom(target);
  }

  function _osInstallResizeFit() {
    const mapArea = document.querySelector(".cc-map-area");
    if (!mapArea || !window.ResizeObserver) return;
    let pending = false;
    new ResizeObserver(() => {
      if (pending) return;
      pending = true;
      requestAnimationFrame(() => { pending = false; _osFitMapZoom(0); });
    }).observe(mapArea);
  }

  /* ── DOM ready ────────────────────────────────────────────────────── */

  if (window._socOrbitalData) {
    _viewerSeat = window._socReplayViewSeat || null;
    window.osOnOrbitalDataLoaded();
  }
})();
