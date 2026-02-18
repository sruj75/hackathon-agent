-- Migrate per-event scheduling from cron-jobs.org to Supabase pg_cron + pg_net.
-- This keeps the one-job-per-event architecture intact.

CREATE EXTENSION IF NOT EXISTS pg_cron;
CREATE EXTENSION IF NOT EXISTS pg_net;


CREATE OR REPLACE FUNCTION public.schedule_event_job(
    p_event_id TEXT,
    p_run_at TIMESTAMPTZ,
    p_timezone TEXT DEFAULT 'UTC'
)
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_job_name TEXT;
    v_job_id BIGINT;
    v_schedule TEXT;
    v_command TEXT;
    v_run_at_utc TIMESTAMP;
    v_now_utc TIMESTAMP;
    v_target_url TEXT;
    v_secret TEXT;
    v_headers JSONB;
    v_body JSONB;
BEGIN
    IF p_event_id IS NULL OR btrim(p_event_id) = '' THEN
        RAISE EXCEPTION 'event_id is required';
    END IF;
    IF p_run_at IS NULL THEN
        RAISE EXCEPTION 'run_at is required';
    END IF;
    IF p_timezone IS NULL OR btrim(p_timezone) = '' THEN
        RAISE EXCEPTION 'timezone is required';
    END IF;

    -- We schedule in UTC for deterministic behavior.
    v_run_at_utc := date_trunc('minute', p_run_at AT TIME ZONE 'UTC');
    v_now_utc := date_trunc('minute', now() AT TIME ZONE 'UTC');
    IF v_run_at_utc <= v_now_utc THEN
        v_run_at_utc := v_now_utc + INTERVAL '1 minute';
    END IF;

    v_schedule := format(
        '%s %s %s %s *',
        EXTRACT(MINUTE FROM v_run_at_utc)::INT,
        EXTRACT(HOUR FROM v_run_at_utc)::INT,
        EXTRACT(DAY FROM v_run_at_utc)::INT,
        EXTRACT(MONTH FROM v_run_at_utc)::INT
    );

    SELECT decrypted_secret
    INTO v_target_url
    FROM vault.decrypted_secrets
    WHERE name = 'scheduler_execute_event_url'
    LIMIT 1;

    IF v_target_url IS NULL THEN
        SELECT decrypted_secret
        INTO v_target_url
        FROM vault.decrypted_secrets
        WHERE name = 'project_url'
        LIMIT 1;

        IF v_target_url IS NOT NULL THEN
            v_target_url := rtrim(v_target_url, '/') || '/functions/v1/execute-event';
        END IF;
    END IF;

    IF v_target_url IS NULL THEN
        RAISE EXCEPTION 'Missing vault secret scheduler_execute_event_url or project_url';
    END IF;

    SELECT decrypted_secret
    INTO v_secret
    FROM vault.decrypted_secrets
    WHERE name = 'scheduler_secret'
    LIMIT 1;

    IF v_secret IS NULL THEN
        RAISE EXCEPTION 'Missing vault secret scheduler_secret';
    END IF;

    v_headers := jsonb_build_object(
        'Content-Type', 'application/json',
        'X-Scheduler-Secret', v_secret
    );
    v_body := jsonb_build_object('event_id', p_event_id);

    v_command := format(
        'select net.http_post(url := %L, headers := %L::jsonb, body := %L::jsonb, timeout_milliseconds := 10000) as request_id',
        v_target_url,
        v_headers::TEXT,
        v_body::TEXT
    );

    v_job_name := substr(
        format(
            'event_%s_%s',
            regexp_replace(substr(p_event_id, 1, 24), '[^a-zA-Z0-9_]+', '', 'g'),
            floor(EXTRACT(EPOCH FROM clock_timestamp()))::BIGINT
        ),
        1,
        63
    );

    SELECT cron.schedule(v_job_name, v_schedule, v_command)
    INTO v_job_id;

    RETURN v_job_id;
END;
$$;


CREATE OR REPLACE FUNCTION public.unschedule_event_job(p_job_id BIGINT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_removed BOOLEAN;
BEGIN
    IF p_job_id IS NULL THEN
        RETURN FALSE;
    END IF;

    SELECT cron.unschedule(p_job_id) INTO v_removed;
    RETURN coalesce(v_removed, FALSE);
EXCEPTION
    WHEN OTHERS THEN
        RETURN FALSE;
END;
$$;


CREATE OR REPLACE FUNCTION public.get_event_for_execution(p_event_id TEXT)
RETURNS JSONB
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT to_jsonb(e)
    FROM public.events e
    WHERE e.id = p_event_id
    LIMIT 1;
$$;


CREATE OR REPLACE FUNCTION public.get_user_timezone_for_execution(p_user_id TEXT)
RETURNS TEXT
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT u.timezone
    FROM public.users u
    WHERE u.user_id = p_user_id
    LIMIT 1;
$$;


CREATE OR REPLACE FUNCTION public.get_push_token_for_execution(p_user_id TEXT)
RETURNS TEXT
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT p.expo_push_token
    FROM public.push_tokens p
    WHERE p.user_id = p_user_id
    LIMIT 1;
$$;


CREATE OR REPLACE FUNCTION public.delete_push_token_for_execution(p_user_id TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    DELETE FROM public.push_tokens WHERE user_id = p_user_id;
    RETURN TRUE;
END;
$$;


CREATE OR REPLACE FUNCTION public.finalize_event_execution(
    p_event_id TEXT,
    p_last_error TEXT,
    p_attempted_at TIMESTAMPTZ DEFAULT now(),
    p_executed BOOLEAN DEFAULT TRUE
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_rows INTEGER;
BEGIN
    UPDATE public.events
    SET executed = p_executed,
        last_error = p_last_error,
        last_attempt_at = coalesce(p_attempted_at, now()),
        updated_at = now()
    WHERE id = p_event_id;

    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN v_rows = 1;
END;
$$;


CREATE OR REPLACE FUNCTION public.get_scheduler_secret_for_execution()
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_secret TEXT;
BEGIN
    SELECT decrypted_secret
    INTO v_secret
    FROM vault.decrypted_secrets
    WHERE name = 'scheduler_secret'
    LIMIT 1;

    RETURN v_secret;
END;
$$;


REVOKE ALL ON FUNCTION public.schedule_event_job(TEXT, TIMESTAMPTZ, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.unschedule_event_job(BIGINT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.get_event_for_execution(TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.get_user_timezone_for_execution(TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.get_push_token_for_execution(TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.delete_push_token_for_execution(TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finalize_event_execution(TEXT, TEXT, TIMESTAMPTZ, BOOLEAN) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.get_scheduler_secret_for_execution() FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.schedule_event_job(TEXT, TIMESTAMPTZ, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.unschedule_event_job(BIGINT) TO service_role;
GRANT EXECUTE ON FUNCTION public.get_event_for_execution(TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.get_user_timezone_for_execution(TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.get_push_token_for_execution(TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.delete_push_token_for_execution(TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.finalize_event_execution(TEXT, TEXT, TIMESTAMPTZ, BOOLEAN) TO service_role;
GRANT EXECUTE ON FUNCTION public.get_scheduler_secret_for_execution() TO service_role;
