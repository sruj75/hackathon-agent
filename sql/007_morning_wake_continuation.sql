-- Ensure morning_wake events self-perpetuate day-over-day from Supabase runtime.

CREATE OR REPLACE FUNCTION public.ensure_next_morning_wake_event(
    p_event_id TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_event RECORD;
    v_user RECORD;
    v_tz TEXT;
    v_hour INT;
    v_minute INT;
    v_local_now TIMESTAMP;
    v_target_local TIMESTAMP;
    v_target_utc TIMESTAMPTZ;
    v_seed_date TEXT;
    v_existing RECORD;
    v_existing_cron_job BIGINT;
    v_new_event_id TEXT;
    v_new_job_id BIGINT;
    v_requires_reschedule BOOLEAN;
BEGIN
    IF p_event_id IS NULL OR btrim(p_event_id) = '' THEN
        RETURN jsonb_build_object('status', 'skipped', 'reason', 'missing_event_id');
    END IF;

    SELECT id, user_id, event_type
    INTO v_event
    FROM public.events
    WHERE id = p_event_id
    LIMIT 1;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('status', 'skipped', 'reason', 'event_not_found');
    END IF;

    IF v_event.event_type <> 'morning_wake' THEN
        RETURN jsonb_build_object('status', 'skipped', 'reason', 'not_morning_wake');
    END IF;

    -- Prevent duplicate next-day events during concurrent executions for same user.
    PERFORM pg_advisory_xact_lock(hashtext(v_event.user_id));

    SELECT user_id, wake_time, timezone
    INTO v_user
    FROM public.users
    WHERE user_id = v_event.user_id
    LIMIT 1;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('status', 'skipped', 'reason', 'user_not_found');
    END IF;

    IF v_user.wake_time IS NULL
       OR v_user.wake_time !~ '^(?:[01][0-9]|2[0-3]):[0-5][0-9]$' THEN
        RETURN jsonb_build_object('status', 'skipped', 'reason', 'invalid_wake_time');
    END IF;

    IF v_user.timezone IS NULL OR btrim(v_user.timezone) = '' THEN
        RETURN jsonb_build_object('status', 'skipped', 'reason', 'missing_timezone');
    END IF;

    v_tz := v_user.timezone;

    BEGIN
        v_local_now := now() AT TIME ZONE v_tz;
    EXCEPTION
        WHEN OTHERS THEN
            RETURN jsonb_build_object('status', 'skipped', 'reason', 'invalid_timezone');
    END;

    v_hour := split_part(v_user.wake_time, ':', 1)::INT;
    v_minute := split_part(v_user.wake_time, ':', 2)::INT;
    v_target_local := date_trunc('day', v_local_now)
        + make_interval(hours => v_hour, mins => v_minute);
    IF v_target_local <= v_local_now THEN
        v_target_local := v_target_local + INTERVAL '1 day';
    END IF;

    v_target_utc := v_target_local AT TIME ZONE v_tz;
    v_seed_date := to_char(v_target_local::DATE, 'YYYY-MM-DD');

    SELECT id, scheduled_time, cron_job_id, payload
    INTO v_existing
    FROM public.events
    WHERE user_id = v_event.user_id
      AND event_type = 'morning_wake'
      AND executed = FALSE
      AND payload->>'seed_date' = v_seed_date
    ORDER BY scheduled_time ASC
    LIMIT 1;

    IF FOUND THEN
        v_existing_cron_job := v_existing.cron_job_id;
        v_requires_reschedule := (
            v_existing_cron_job IS NULL
            OR date_trunc('minute', v_existing.scheduled_time AT TIME ZONE 'UTC')
                <> date_trunc('minute', v_target_utc AT TIME ZONE 'UTC')
            OR coalesce(v_existing.payload->>'timezone', '') <> v_tz
        );

        IF NOT v_requires_reschedule THEN
            RETURN jsonb_build_object(
                'status', 'ok',
                'action', 'noop_existing',
                'event_id', v_existing.id,
                'cron_job_id', v_existing_cron_job,
                'seed_date', v_seed_date,
                'scheduled_time', v_target_utc
            );
        END IF;

        v_new_job_id := public.schedule_event_job(v_existing.id, v_target_utc, v_tz);

        UPDATE public.events
        SET scheduled_time = v_target_utc,
            payload = jsonb_build_object(
                'reason', 'daily_bootstrap',
                'seed_date', v_seed_date,
                'timezone', v_tz,
                'schedule_owner', 'system',
                'schedule_policy', 'morning_bootstrap'
            ),
            executed = FALSE,
            cron_job_id = v_new_job_id,
            last_error = NULL,
            last_attempt_at = NULL,
            updated_at = now()
        WHERE id = v_existing.id;

        IF v_existing_cron_job IS NOT NULL THEN
            PERFORM public.unschedule_event_job(v_existing_cron_job);
        END IF;

        RETURN jsonb_build_object(
            'status', 'ok',
            'action', 'rescheduled_existing',
            'event_id', v_existing.id,
            'cron_job_id', v_new_job_id,
            'seed_date', v_seed_date,
            'scheduled_time', v_target_utc
        );
    END IF;

    v_new_event_id := gen_random_uuid()::TEXT;
    INSERT INTO public.events (
        id,
        user_id,
        scheduled_time,
        event_type,
        payload,
        executed,
        cron_job_id,
        created_at,
        updated_at
    ) VALUES (
        v_new_event_id,
        v_event.user_id,
        v_target_utc,
        'morning_wake',
        jsonb_build_object(
            'reason', 'daily_bootstrap',
            'seed_date', v_seed_date,
            'timezone', v_tz,
            'schedule_owner', 'system',
            'schedule_policy', 'morning_bootstrap'
        ),
        FALSE,
        NULL,
        now(),
        now()
    );

    BEGIN
        v_new_job_id := public.schedule_event_job(v_new_event_id, v_target_utc, v_tz);
        UPDATE public.events
        SET cron_job_id = v_new_job_id,
            updated_at = now()
        WHERE id = v_new_event_id;
    EXCEPTION
        WHEN OTHERS THEN
            DELETE FROM public.events WHERE id = v_new_event_id;
            RAISE;
    END;

    RETURN jsonb_build_object(
        'status', 'ok',
        'action', 'created',
        'event_id', v_new_event_id,
        'cron_job_id', v_new_job_id,
        'seed_date', v_seed_date,
        'scheduled_time', v_target_utc
    );
END;
$$;


REVOKE ALL ON FUNCTION public.ensure_next_morning_wake_event(TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.ensure_next_morning_wake_event(TEXT) TO service_role;
