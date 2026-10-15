-- ============================================================================
-- Sea of Colours — Snowflake Schema (SOC_*)
-- ============================================================================
-- One row per game session, with normalized child tables for grid cells,
-- entities, ledgers, hoard / shipped parcels, policy queues, structured
-- logs, append-only replay frames, and Cortex-agent audit trails.
--
-- Mirrors the Python in-memory model from sea_of_colours/game/session.py
-- (GameSession.to_dict / from_dict) 1:1 so Phase 2's Snowpark procs can
-- ferry state in/out without translation layers.
--
-- ⚠️ THIS FILE UNDER-DESCRIBES THE LIVE ACCOUNT (v1.43).
--    Six tables were migrated to HYBRID TABLES for latency and are hybrid in
--    SOC_HACKATHON_DB.SEA_OF_COLOURS right now:
--
--      SOC_GAME_SESSION, SOC_GAME_LOG, SOC_REPLAY_FRAME,
--      SOC_POLICY_QUEUE, SOC_AGENT_INVOCATION, SOC_AGENT_MEMORY
--
--    They are declared below as standard tables. Because every DDL is
--    CREATE TABLE IF NOT EXISTS, re-running this file is a harmless no-op and
--    will NOT convert them back — but a FRESH account deployed from this file
--    gets standard tables and will be ~4x slower per point write.
--
--    Each migrated table has a *_FDN_BAK standard-table backup holding its
--    pre-swap rows; revert is two renames per table. The measurements, the
--    migration steps and the operational caveats (enforced primary keys,
--    session-based consistency, doing renames with the web server down) are in
--    docs/SNOWFLAKE_LATENCY_BRIEF.md §U–§V and
--    docs/SNOWFLAKE_PERF_HANDOVER.md.
--
--    Hybrid tables are unavailable on Google Cloud and in trial accounts, so
--    this file is deliberately left portable rather than hard-coding HYBRID.
--
-- ⚠️ NON-DESTRUCTIVE BY DESIGN.  All table DDLs are CREATE TABLE IF NOT
--     EXISTS so re-running this file NEVER wipes existing data.  Schema
--     evolutions (added columns, new tables) must be applied as
--     standalone ALTER scripts in snowflake/migrations/ — running this
--     file by itself will NOT pick them up after the first deploy.
--     Views live in soc_views.sql and use CREATE OR REPLACE.
--
-- ⚠️ TEMPLATED.  {{SOC_DATABASE}} / {{SOC_SCHEMA}} are substituted by
--     scripts/deploy_soc_schema.py (see sea_of_colours/snowpark/naming.py).
--     Deploy through that script; pasting this file raw into Snowsight
--     will fail on the placeholders.
-- ============================================================================

USE DATABASE {{SOC_DATABASE}};
CREATE SCHEMA IF NOT EXISTS {{SOC_SCHEMA}};
USE SCHEMA {{SOC_SCHEMA}};

-- --------------------------------------------------------------------------
-- 1) Game session — canonical row per game
-- --------------------------------------------------------------------------
-- json_state is GameSession.to_dict() blob — durable snapshot of everything
-- the engine needs to deserialize (entities, hoard, ledgers, …). It's the
-- source of truth for "load session" until the per-table normal forms
-- catch up. Phase 2 keeps both in sync on every SOC_RUN_NIGHT.
CREATE TABLE IF NOT EXISTS SOC_GAME_SESSION (
    session_id           STRING        NOT NULL PRIMARY KEY,
    season_name          STRING,
    width                INT           NOT NULL,
    height               INT           NOT NULL,
    seed                 INT           NOT NULL,
    day                  INT           NOT NULL DEFAULT 1,
    phase                STRING        NOT NULL DEFAULT 'planning',
    json_state           VARIANT,
    created_at           TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    last_simulated_at    TIMESTAMP_NTZ,
    last_touched_at      TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- Migration for deployments that pre-date the season-name column. New
-- envs land the column via the CREATE above; existing envs pick it up
-- here as a no-op when the column is already present. The session
-- writer (snowpark_store.save_session) will populate it on every MERGE.
ALTER TABLE SOC_GAME_SESSION ADD COLUMN IF NOT EXISTS season_name STRING;

-- --------------------------------------------------------------------------
-- 2) Square identity ledger — port of sea_of_colours/game/ledger.py
-- --------------------------------------------------------------------------
-- One row per generated cell. Identity is frozen at generation time so a
-- harvested parcel keeps its original (tile, purity) certificate of origin
-- even after the live grid mutates.
CREATE TABLE IF NOT EXISTS SOC_SQUARE_IDENTITY (
    session_id            STRING NOT NULL,
    x                     INT    NOT NULL,
    y                     INT    NOT NULL,
    square_id             STRING NOT NULL,     -- 16-char blake2b digest
    tile_at_generation    INT    NOT NULL,     -- 0=EMPTY, 1=GREEN, 2=RED, 3=BLUE
    purity_at_generation  INT    NOT NULL,
    PRIMARY KEY (session_id, x, y)
);

-- --------------------------------------------------------------------------
-- 3) Asset record — port of sea_of_colours/game/asset_ledger.py
-- --------------------------------------------------------------------------
-- Per-asset (harvester / orblift / probe) lifecycle row. Lives forever once
-- created — destroyed assets keep their row with destroyed_on_day set.
CREATE TABLE IF NOT EXISTS SOC_ASSET_RECORD (
    session_id               STRING NOT NULL,
    asset_id                 STRING NOT NULL,
    asset_type               STRING NOT NULL,  -- 'harvester' | 'orblift' | 'probe'
    owner                    STRING NOT NULL,  -- 'p1' | 'p2'
    created_on_day           INT    NOT NULL,
    first_deployed_day       INT,              -- null while still orbital
    destroyed_on_day         INT,              -- null while alive
    destroyed_by             STRING,           -- e.g. 'crushed_by_harvester@(7,7)'
    last_seen_x              INT,
    last_seen_y              INT,
    total_red_harvested      INT    NOT NULL DEFAULT 0,
    total_days_on_surface    INT    NOT NULL DEFAULT 0,
    updated_at               TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (session_id, asset_id)
);

-- --------------------------------------------------------------------------
-- 4) Entity state — *current* live position / cargo per entity
-- --------------------------------------------------------------------------
-- Mirrors GameSession.entities. Updated by every move; destroyed entities
-- are deleted (SOC_ASSET_RECORD retains the lifetime info).
CREATE TABLE IF NOT EXISTS SOC_ENTITY_STATE (
    session_id           STRING NOT NULL,
    entity_id            STRING NOT NULL,
    entity_type          STRING NOT NULL,     -- 'harvester' | 'orblift' | 'probe'
    owner                STRING NOT NULL,
    x                    INT,                 -- null when orbital
    y                    INT,
    carrying_red         BOOLEAN DEFAULT FALSE,
    orbital_cargo_red    BOOLEAN DEFAULT FALSE,
    cargo_squares        VARIANT,             -- list of parcel dicts for harvesters
    lost_last_night      BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (session_id, entity_id)
);

-- --------------------------------------------------------------------------
-- 5) Grid cells — current tile / purity per square
-- --------------------------------------------------------------------------
-- Diverges from SOC_SQUARE_IDENTITY once a harvester converts RED → GREEN.
-- last_mutated_day stamps the dawn when the cell last changed so the
-- replay can highlight recently-converted cells.
CREATE TABLE IF NOT EXISTS SOC_GRID_CELL (
    session_id        STRING NOT NULL,
    x                 INT    NOT NULL,
    y                 INT    NOT NULL,
    tile              INT    NOT NULL,
    purity            INT    NOT NULL,
    last_mutated_day  INT,
    PRIMARY KEY (session_id, x, y)
);

-- --------------------------------------------------------------------------
-- 6) Hoard parcels (on-planet vault)
-- --------------------------------------------------------------------------
-- One row per square banked into a player's hoard via pickup. Keyed by
-- session_id + slot so cross-session SQL ("top 10 highest-purity hauls
-- ever") works.
CREATE TABLE IF NOT EXISTS SOC_HOARD_PARCEL (
    session_id     STRING NOT NULL,
    owner          STRING NOT NULL,
    slot           INT    NOT NULL,                -- 0-based insertion index
    square_id      STRING,
    origin_x       INT,
    origin_y       INT,
    origin_tile    INT,
    origin_purity  INT,
    harvested_day  INT,
    site_uid       INT,
    payload        VARIANT,                        -- full snapshot row
    deposited_at   TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (session_id, owner, slot)
);

-- --------------------------------------------------------------------------
-- 7) Shipped parcels (post-vault, future §4 mechanic)
-- --------------------------------------------------------------------------
-- Schema mirrors the hoard so the same UI renderer paints both.
CREATE TABLE IF NOT EXISTS SOC_SHIPPED_PARCEL (
    session_id     STRING NOT NULL,
    owner          STRING NOT NULL,
    slot           INT    NOT NULL,
    square_id      STRING,
    origin_x       INT,
    origin_y       INT,
    origin_tile    INT,
    origin_purity  INT,
    harvested_day  INT,
    shipped_day    INT,
    site_uid       INT,
    payload        VARIANT,
    shipped_at     TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (session_id, owner, slot)
);

-- --------------------------------------------------------------------------
-- 8) Policy queue — one row per (day, player) submission
-- --------------------------------------------------------------------------
-- queue is the JSON array the player POSTed (sea_of_colours/game/policy.py
-- format).  SOC_RUN_NIGHT reads both seats' rows once both are submitted.
CREATE TABLE IF NOT EXISTS SOC_POLICY_QUEUE (
    session_id    STRING NOT NULL,
    day           INT    NOT NULL,
    player        STRING NOT NULL,   -- 'p1' | 'p2'
    queue         VARIANT NOT NULL,  -- raw move array
    move_count    INT    NOT NULL DEFAULT 0,
    submitted_at  TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (session_id, day, player)
);

-- --------------------------------------------------------------------------
-- 9) Game log — structured event log (replaces GameSession.log strings)
-- --------------------------------------------------------------------------
-- seq is monotonic-per-session so we can paginate without missing rows.
-- level is 'info' or 'error'; the HUD paints errors yellow next to the
-- attempted action.
CREATE TABLE IF NOT EXISTS SOC_GAME_LOG (
    session_id  STRING NOT NULL,
    day         INT    NOT NULL,
    seq         INT    NOT NULL,
    level       STRING NOT NULL DEFAULT 'info',
    text        STRING NOT NULL,
    ts          TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (session_id, day, seq)
);

-- --------------------------------------------------------------------------
-- 10) Replay frames — per-tick snapshots (the multi-day history)
-- --------------------------------------------------------------------------
-- Append-only. Every move (drop / step / pickup / probe / waste / dawn)
-- emits one row. The Phase-4 UI scrubs across all days using SOC_DAY_INDEX
-- (defined in soc_views.sql) to render day-divider ticks.
--
-- cells / cells_player_p1 / cells_player_p2 are VARIANT to keep
-- compatibility with the existing front-end paint pipeline; once volumes
-- climb we can swap them for a compressed delta encoding.
CREATE TABLE IF NOT EXISTS SOC_REPLAY_FRAME (
    session_id        STRING NOT NULL,
    day               INT    NOT NULL,
    frame_idx         INT    NOT NULL,        -- 0-based within a day
    global_idx        INT    NOT NULL,        -- monotonic across the session
    caption           STRING,
    tag               STRING,                 -- 'open' | 'probe' | 'drop' |
                                              -- 'step' | 'pickup' | 'waste' |
                                              -- 'dawn' | 'collision_swap'
    owner             STRING,                 -- 'p1' | 'p2' | null
    cells             VARIANT,                -- observer-view (cheat) snapshot
    cells_player_p1   VARIANT,                -- p1 percept snapshot
    cells_player_p2   VARIANT,                -- p2 percept snapshot
    entities          VARIANT,                -- entity row summaries
    hoard             VARIANT,                -- hoard delta
    -- v0.7.3+ — per-frame FX + accounting metadata. ``collisions`` and
    -- ``crushed_probes`` drive the collision-ring + pixel-splash FX in
    -- the watcher; ``scheduled_orders`` rides on the opening frame so
    -- the ORDERS pane can render executed/failed/pending status as
    -- the scrubber walks the night; ``attempted`` + ``outcome`` give
    -- the synced log drawer enough context to flag yellow failures.
    -- ``hour`` (v0.7.4) is the 1..21 planetary night clock.
    collisions        VARIANT,
    crushed_probes    VARIANT,
    scheduled_orders  VARIANT,
    attempted         STRING,
    outcome           STRING,                 -- 'ok' | 'failed' | null
    hour              INT,                    -- 0 (opener/dawn) | 1..21
    -- v0.9 — interdiction-weapon FX payloads (RULEBOOK §5). Each
    -- column is an array of event dicts written by the engine on
    -- the frame the weapon resolved:
    --   emp        : [{kind:'emp_launch', owner, cx, cy, radius, ...}]
    --   mine       : [{kind:'mine_lay'|'mine_hit', owner, x, y, ...}]
    --   chaff      : [{kind:'chaff_flare', owner, from_hour, ...}]
    --   emp_clouds   : per-frame snapshot of every active EMP cloud
    --                  so the watcher can fade the dim region without
    --                  replaying every launch.
    --   mines_active : v0.9.4 per-frame snapshot of every active mine
    --                  so the watcher can paint a persistent rhombus
    --                  on each mined cell between lay-time and the
    --                  matching detonate frame.
    -- v1.31 — the caltrop mine was RETIRED (RULEBOOK §4.9.4). The two
    -- mine columns are kept, and kept described above, because seasons
    -- archived before the retirement still replay out of them. Nothing
    -- writes them any more; a new weapon in the vacated slot should add
    -- its own columns rather than reuse these.
    emp               VARIANT,
    mine              VARIANT,
    chaff             VARIANT,
    emp_clouds        VARIANT,
    mines_active      VARIANT,
    -- v0.9.11 — p3/p4 percept snapshots (N-seat OBS replay).
    cells_player_p3   VARIANT,
    cells_player_p4   VARIANT,
    -- v1.53 — SNAP (RULEBOOK §4.9.7), which landed in v1.36 and never
    -- got a home here. The engine wrote `snap` / `snap_clouds` onto the
    -- frame exactly like `emp` / `emp_clouds`, and this table silently
    -- dropped both, so on a Snowflake season a SNAP had a tagged frame
    -- and a log line but no payload — and the watcher's launch FX is
    -- gated on the payload. It played on `seasons/` and vanished on
    -- Snowflake, which is why it went a whole competition unnoticed.
    --   snap        : [{kind:'snap_launch', owner, at, destroyed_probes,
    --                   damaged_harvesters, ...}]
    --   snap_clouds : per-frame snapshot of every live scorch mark, so
    --                 a scrub paints the mark without re-firing.
    -- ADDING A WEAPON MEANS ADDING COLUMNS HERE. See
    -- docs/ADDING_A_WEAPON.md — this table is the step that gets missed
    -- because nothing fails, it just quietly forgets.
    snap              VARIANT,
    snap_clouds       VARIANT,
    ts                TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (session_id, day, frame_idx)
);

-- Idempotent column adds for schemas created before v0.7.4. These
-- ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` statements are no-ops
-- on a freshly-created table (the columns are already present from
-- the CREATE TABLE above) and a forward-migration for any deploy that
-- pre-dates the FX-metadata fields.
ALTER TABLE SOC_REPLAY_FRAME ADD COLUMN IF NOT EXISTS collisions       VARIANT;
ALTER TABLE SOC_REPLAY_FRAME ADD COLUMN IF NOT EXISTS crushed_probes   VARIANT;
ALTER TABLE SOC_REPLAY_FRAME ADD COLUMN IF NOT EXISTS scheduled_orders VARIANT;
ALTER TABLE SOC_REPLAY_FRAME ADD COLUMN IF NOT EXISTS attempted        STRING;
ALTER TABLE SOC_REPLAY_FRAME ADD COLUMN IF NOT EXISTS outcome          STRING;
ALTER TABLE SOC_REPLAY_FRAME ADD COLUMN IF NOT EXISTS hour             INT;
-- v0.9 forward-migration for deploys built before the interdiction
-- weapons layer landed. Idempotent on fresh tables (columns already
-- present in CREATE TABLE above).
ALTER TABLE SOC_REPLAY_FRAME ADD COLUMN IF NOT EXISTS emp              VARIANT;
ALTER TABLE SOC_REPLAY_FRAME ADD COLUMN IF NOT EXISTS mine             VARIANT;
ALTER TABLE SOC_REPLAY_FRAME ADD COLUMN IF NOT EXISTS chaff            VARIANT;
ALTER TABLE SOC_REPLAY_FRAME ADD COLUMN IF NOT EXISTS emp_clouds       VARIANT;
-- v0.9.4 forward-migration: per-frame mine snapshot for the new
-- persistent rhombus overlay.
ALTER TABLE SOC_REPLAY_FRAME ADD COLUMN IF NOT EXISTS mines_active     VARIANT;
-- v0.9.11 forward-migration: per-seat percept snapshots for seats 3 & 4
-- so 3-/4-player OBS replays can combine every seat's vision (the
-- pre-v0.9.11 schema only persisted p1/p2, leaving p3/p4 fog-of-war
-- absent on round-trip). Idempotent on fresh tables.
ALTER TABLE SOC_REPLAY_FRAME ADD COLUMN IF NOT EXISTS cells_player_p3  VARIANT;
ALTER TABLE SOC_REPLAY_FRAME ADD COLUMN IF NOT EXISTS cells_player_p4  VARIANT;
-- v1.53 forward-migration: SNAP's FX payload and its scorch-mark
-- snapshot. Every Snowflake deploy since v1.36 has been dropping both
-- on the floor. Re-deploy is enough to start keeping them; nights
-- already archived cannot be recovered, because the payload was never
-- written down.
ALTER TABLE SOC_REPLAY_FRAME ADD COLUMN IF NOT EXISTS snap             VARIANT;
ALTER TABLE SOC_REPLAY_FRAME ADD COLUMN IF NOT EXISTS snap_clouds      VARIANT;

-- --------------------------------------------------------------------------
-- 11) Agent invocations — Cortex agent audit trail (Phase 5)
-- --------------------------------------------------------------------------
-- One row per agent call. prompt_excerpt / response_text are trimmed to
-- 4 KB at insert time (the simulator's view excerpt + the final summary)
-- so we don't bloat the table with verbose tool traffic — tool_calls is a
-- VARIANT array of {name, args, result_excerpt} for that.
CREATE TABLE IF NOT EXISTS SOC_AGENT_INVOCATION (
    session_id      STRING NOT NULL,
    day             INT    NOT NULL,
    seq             INT    NOT NULL,
    agent_id        STRING NOT NULL,          -- 'RED_HARVEST' (heuristic) or an AI agent name (e.g. 'SOC_RED_REAPER')
    player          STRING NOT NULL,          -- 'p1' | 'p2'
    prompt_excerpt  STRING,
    tool_calls      VARIANT,
    rationale       STRING,
    response_text   STRING,
    ms_elapsed      INT,
    status          STRING DEFAULT 'ok',
    ts              TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (session_id, day, seq)
);

-- --------------------------------------------------------------------------
-- 12) Orchestrator log — high-level events (game create, night resolved,
--     agent invoked, etc.).  Optional but matches AA4's pattern and helps
--     post-mortem debugging.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS SOC_ORCHESTRATOR_LOG (
    id           INT AUTOINCREMENT PRIMARY KEY,
    ts           TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    session_id   STRING,
    event_type   STRING,
    message      STRING,
    details      VARIANT
);

-- --------------------------------------------------------------------------
-- 13) Agent memory — moved to sea_of_colours/orchestrator_2/snowflake/.
--     This table is harness-specific and no longer lives
--     in the universal orchestrator schema. See:
--       sea_of_colours/orchestrator_2/snowflake/orchestrator_v2_schema.sql
-- --------------------------------------------------------------------------
