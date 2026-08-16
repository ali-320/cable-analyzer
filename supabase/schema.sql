-- Cable Analyzer remote database schema (Supabase / PostgreSQL).
--
-- Mirrors the tables created for the Pi (pi-zero-2w-01) remote upload and the
-- working insert-only RLS setup. The uploader (src/telemetry/remote.py) posts
-- to /rest/v1/sessions and /rest/v1/samples with:
--   sessions: on_conflict=session_id
--   samples:  on_conflict=session_id,sample_index
-- so both unique constraints below are required for the upsert to work.
--
-- This file is idempotent: it can be run on a fresh Supabase project or pasted
-- into the SQL editor of the existing project without touching stored rows.

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.sessions (
    session_id        TEXT PRIMARY KEY,
    device_id         TEXT NOT NULL,
    mode              TEXT,
    v_target          DOUBLE PRECISION,
    length_m          DOUBLE PRECISION,
    phone_expected    BOOLEAN NOT NULL DEFAULT FALSE,
    started_at        DOUBLE PRECISION,          -- epoch seconds (null in probe mode)
    ended_at          DOUBLE PRECISION,          -- epoch seconds (null in probe mode)
    charging_detected BOOLEAN NOT NULL DEFAULT FALSE,
    v_present         BOOLEAN NOT NULL DEFAULT FALSE,
    fault_reason      TEXT,
    probe_json        JSONB,
    verdict_json      JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.samples (
    session_id   TEXT NOT NULL,
    device_id    TEXT NOT NULL,
    sample_index INTEGER NOT NULL,
    t            DOUBLE PRECISION NOT NULL,      -- seconds since acquisition start
    voltage_v    DOUBLE PRECISION,
    current_a    DOUBLE PRECISION,
    power_w      DOUBLE PRECISION,
    state        TEXT,                           -- NO_SOURCE/NO_PHONE/CHARGING/CHARGED/FAULT/VERIFICATION/PROBE
    valid        BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (session_id, sample_index)
);

-- ---------------------------------------------------------------------------
-- Row Level Security
-- ---------------------------------------------------------------------------

ALTER TABLE public.sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.samples  ENABLE ROW LEVEL SECURITY;

-- The Pi uses the anon key only for inserts; it never reads rows back
-- (the uploader sends Prefer: return=minimal, so no response SELECT is needed).
GRANT USAGE ON SCHEMA public TO anon;
GRANT INSERT ON TABLE public.sessions TO anon;
GRANT INSERT ON TABLE public.samples  TO anon;

DROP POLICY IF EXISTS "pi can insert sessions" ON public.sessions;
DROP POLICY IF EXISTS "pi can insert samples"  ON public.samples;

CREATE POLICY "pi can insert sessions"
ON public.sessions
FOR INSERT
TO anon
WITH CHECK (device_id = 'pi-zero-2w-01');

CREATE POLICY "pi can insert samples"
ON public.samples
FOR INSERT
TO anon
WITH CHECK (device_id = 'pi-zero-2w-01');
