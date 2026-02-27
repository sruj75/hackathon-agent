import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2";

const EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send";

type EventRow = {
  id: string;
  user_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  executed: boolean;
  cron_job_id: number | null;
};

function getDailySessionId(userId: string): string {
  const day = new Date().toISOString().slice(0, 10);
  return `session_${userId}_${day}`;
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function formatCalendarReminderBody(payload: Record<string, unknown>, timezoneName: string): string {
  const eventTitle = asString(payload.event_title) ?? "upcoming event";
  const raw = asString(payload.event_start_time);
  if (!raw) return `${eventTitle} starts soon.`;

  const dt = new Date(raw);
  if (Number.isNaN(dt.getTime())) return `${eventTitle} starts soon.`;

  try {
    const formatted = new Intl.DateTimeFormat("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: true,
      timeZone: timezoneName,
    }).format(dt);
    return `${eventTitle} starts at ${formatted}.`;
  } catch {
    return `${eventTitle} starts soon.`;
  }
}

async function sendPushNotification(
  supabase: ReturnType<typeof createClient>,
  userId: string,
  title: string,
  body: string,
  data: Record<string, unknown>,
): Promise<boolean> {
  const tokenResult = await supabase.rpc("get_push_token_for_execution", {
    p_user_id: userId,
  });
  if (tokenResult.error) {
    console.error("get_push_token_for_execution failed", tokenResult.error.message);
    return false;
  }

  const pushToken = asString(tokenResult.data);
  if (!pushToken) return false;

  const response = await fetch(EXPO_PUSH_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      to: pushToken,
      title,
      body,
      data,
      sound: "default",
      priority: "high",
    }),
  });

  if (response.status !== 200) return false;

  const result = await response.json();
  const items = Array.isArray(result?.data) ? result.data : [];
  for (const item of items) {
    if (item?.status === "error") {
      const errorCode = item?.details?.error;
      if (errorCode === "DeviceNotRegistered") {
        await supabase.rpc("delete_push_token_for_execution", { p_user_id: userId });
      }
      return false;
    }
  }

  return true;
}

async function unscheduleIfPresent(
  supabase: ReturnType<typeof createClient>,
  jobId: number | null,
): Promise<void> {
  if (!jobId || Number.isNaN(Number(jobId))) return;
  const result = await supabase.rpc("unschedule_event_job", { p_job_id: Number(jobId) });
  if (result.error) {
    console.warn("unschedule_event_job failed", result.error.message);
  }
}

async function ensureNextMorningWake(
  supabase: ReturnType<typeof createClient>,
  eventId: string,
): Promise<{ ok: boolean; detail?: unknown; error?: string }> {
  const result = await supabase.rpc("ensure_next_morning_wake_event", {
    p_event_id: eventId,
  });
  if (result.error) {
    console.warn("ensure_next_morning_wake_event failed", result.error.message);
    return { ok: false, error: result.error.message };
  }
  return { ok: true, detail: result.data ?? null };
}

Deno.serve(async (req: Request): Promise<Response> => {
  try {
    const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? Deno.env.get("SUPABASE_PROJECT_URL");
    const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
    if (!supabaseUrl || !serviceRoleKey) {
      return new Response(
        JSON.stringify({ error: "Missing Supabase runtime env" }),
        { status: 500, headers: { "Content-Type": "application/json" } },
      );
    }

    const supabase = createClient(supabaseUrl, serviceRoleKey, {
      auth: { persistSession: false, autoRefreshToken: false },
    });

    const secretResult = await supabase.rpc("get_scheduler_secret_for_execution");
    const expectedSecret = asString(secretResult.data);
    const providedSecret = asString(req.headers.get("x-scheduler-secret"));
    if (secretResult.error || !expectedSecret || providedSecret !== expectedSecret) {
      return new Response(
        JSON.stringify({ error: "Unauthorized" }),
        { status: 401, headers: { "Content-Type": "application/json" } },
      );
    }

    const payload = await req.json().catch(() => ({}));
    const eventId = asString(payload?.event_id);
    if (!eventId) {
      return new Response(
        JSON.stringify({ error: "event_id is required" }),
        { status: 400, headers: { "Content-Type": "application/json" } },
      );
    }

    const eventResult = await supabase.rpc("get_event_for_execution", { p_event_id: eventId });
    if (eventResult.error) {
      return new Response(
        JSON.stringify({ error: `event fetch failed: ${eventResult.error.message}` }),
        { status: 500, headers: { "Content-Type": "application/json" } },
      );
    }

    const event = eventResult.data as EventRow | null;
    if (!event) {
      return new Response(
        JSON.stringify({ error: "Event not found" }),
        { status: 404, headers: { "Content-Type": "application/json" } },
      );
    }

    if (event.executed) {
      await unscheduleIfPresent(supabase, event.cron_job_id ?? null);
      return new Response(
        JSON.stringify({ status: "already_executed", event_id: eventId }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }

    const eventType = asString(event.event_type) ?? "checkin";
    const eventUserId = asString(event.user_id);
    if (!eventUserId) {
      return new Response(
        JSON.stringify({ error: "Event missing user_id" }),
        { status: 500, headers: { "Content-Type": "application/json" } },
      );
    }

    const rawPayload =
      event.payload && typeof event.payload === "object" && !Array.isArray(event.payload)
        ? event.payload
        : {};
    const eventPayload = { ...rawPayload };

    let calendarTimezone: string | null = null;
    if (eventType === "calendar_reminder") {
      calendarTimezone = asString(eventPayload.timezone);
      if (!calendarTimezone) {
        const tzResult = await supabase.rpc("get_user_timezone_for_execution", {
          p_user_id: eventUserId,
        });
        if (!tzResult.error) {
          calendarTimezone = asString(tzResult.data);
        }
      }
      if (calendarTimezone) {
        eventPayload.timezone = calendarTimezone;
      }
    }

    const reason = asString(eventPayload.reason) ?? "scheduled_checkin";
    const triggerType = asString(eventPayload.trigger_type) ?? eventType;
    const calendarEventId = asString(eventPayload.calendar_event_id);

    let title = "Check-in";
    let body = "You have a scheduled check-in.";
    if (eventType === "morning_wake") {
      title = "Good morning";
      body = "Ready to plan your day?";
    } else if (eventType === "calendar_reminder") {
      title = `Upcoming: ${asString(eventPayload.event_title) ?? "Event"}`;
      body = calendarTimezone
        ? formatCalendarReminderBody(eventPayload, calendarTimezone)
        : "Reminder unavailable: missing timezone.";
    } else if (eventType === "checkin") {
      body = `It is time for your check-in (${reason}).`;
    }

    const notificationData: Record<string, unknown> = {
      session_id: getDailySessionId(eventUserId),
      type: eventType,
      trigger_type: triggerType,
      event_id: eventId,
      user_id: eventUserId,
    };
    if (calendarEventId) notificationData.calendar_event_id = calendarEventId;

    let pushSent = false;
    if (!(eventType === "calendar_reminder" && !calendarTimezone)) {
      pushSent = await sendPushNotification(supabase, eventUserId, title, body, notificationData);
    }

    let lastError: string | null = pushSent ? null : "push_failed_or_missing_token";
    if (eventType === "calendar_reminder" && !calendarTimezone) {
      lastError = "missing_timezone";
    }

    const finalize = await supabase.rpc("finalize_event_execution", {
      p_event_id: eventId,
      p_last_error: lastError,
      p_attempted_at: new Date().toISOString(),
      p_executed: true,
    });
    if (finalize.error) {
      return new Response(
        JSON.stringify({ error: `finalize failed: ${finalize.error.message}` }),
        { status: 500, headers: { "Content-Type": "application/json" } },
      );
    }

    let nextMorningWake: { ok: boolean; detail?: unknown; error?: string } | null = null;
    if (eventType === "morning_wake") {
      nextMorningWake = await ensureNextMorningWake(supabase, eventId);
    }

    await unscheduleIfPresent(supabase, event.cron_job_id ?? null);

    return new Response(
      JSON.stringify({
        status: "executed",
        event_id: eventId,
        event_type: eventType,
        push_sent: pushSent,
        next_morning_wake: nextMorningWake,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return new Response(
      JSON.stringify({ error: message }),
      { status: 500, headers: { "Content-Type": "application/json" } },
    );
  }
});
