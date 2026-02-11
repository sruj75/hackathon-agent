-- Remove implicit timezone fallback and harden direct table access.

ALTER TABLE public.users
    ALTER COLUMN timezone DROP DEFAULT;

CREATE TABLE IF NOT EXISTS public.composio_connections (
    user_id TEXT PRIMARY KEY,
    connected_accounts JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.push_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.composio_connections ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.users FROM anon, authenticated;
REVOKE ALL ON TABLE public.push_tokens FROM anon, authenticated;
REVOKE ALL ON TABLE public.sessions FROM anon, authenticated;
REVOKE ALL ON TABLE public.events FROM anon, authenticated;
REVOKE ALL ON TABLE public.composio_connections FROM anon, authenticated;
