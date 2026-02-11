-- Intentive initial Supabase schema

CREATE TABLE IF NOT EXISTS public.users (
    user_id TEXT PRIMARY KEY,
    wake_time TEXT,
    bedtime TEXT,
    timezone TEXT DEFAULT 'UTC',
    health_anchors JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.push_tokens (
    user_id TEXT PRIMARY KEY REFERENCES public.users(user_id) ON DELETE CASCADE,
    expo_push_token TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    date TEXT NOT NULL,
    state JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.events (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    scheduled_time TIMESTAMPTZ NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    executed BOOLEAN NOT NULL DEFAULT FALSE,
    cron_job_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error TEXT,
    last_attempt_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_date
    ON public.sessions (user_id, date);

CREATE INDEX IF NOT EXISTS idx_events_user_type_time
    ON public.events (user_id, event_type, scheduled_time);

CREATE INDEX IF NOT EXISTS idx_events_reconcile
    ON public.events (executed, cron_job_id, scheduled_time);

CREATE INDEX IF NOT EXISTS idx_events_seed_date
    ON public.events ((payload->>'seed_date'));

-- RLS + policies: see 002_auth_timezone_hardening.sql and 003_rls_user_policies.sql
