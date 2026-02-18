"""
Composio tools for Google Calendar and Tasks.
Scrappy implementation - no fancy abstractions.
"""
import asyncio
import os
from dotenv import load_dotenv
from composio import Composio, Action
from datetime import datetime, timedelta
import logging
from zoneinfo import ZoneInfo

from context import current_user_id, current_user_timezone
from reminder_service import (
    cancel_calendar_reminders,
    schedule_calendar_reminder,
)

# Ensure .env is loaded regardless of import order (safe to call multiple times)
load_dotenv()

logger = logging.getLogger(__name__)

# Lazy initialization - client created on first use (after .env is loaded by ADK)
_composio_client = None
_user_timezone_by_user: dict[str, str] = {}


def _get_entity():
    """Get Composio entity bound to the current authenticated user."""
    global _composio_client

    if _composio_client is None:
        api_key = os.environ.get("COMPOSIO_API_KEY")
        if not api_key:
            logger.error("COMPOSIO_API_KEY not found in environment!")
            raise ValueError("COMPOSIO_API_KEY environment variable is not set")

        logger.info("Initializing Composio client with API key prefix: %s", api_key[:10])
        _composio_client = Composio(api_key=api_key)
        logger.info("Composio client initialized successfully")

    try:
        user_id = current_user_id.get()
    except LookupError as exc:
        raise ValueError("missing_user_context_for_composio") from exc
    if not user_id:
        raise ValueError("missing_user_context_for_composio")
    return _composio_client.get_entity(user_id)


def _get_user_timezone() -> str:
    """
    Resolve timezone from request context/profile.
    Source priority:
    1) current_user_timezone ContextVar (set by websocket init/thinking mode)
    2) per-user cached timezone from previous resolved context
    3) no fallback (timezone required)
    """

    tz_from_context = current_user_timezone.get()
    if tz_from_context:
        try:
            ZoneInfo(tz_from_context)
            try:
                user_id_from_context = current_user_id.get()
            except LookupError:
                user_id_from_context = None
            if user_id_from_context:
                _user_timezone_by_user[user_id_from_context] = tz_from_context
            return tz_from_context
        except Exception:
            logger.warning(f"Invalid timezone in context: {tz_from_context}")

    try:
        user_id = current_user_id.get()
    except LookupError:
        user_id = None

    if not user_id:
        raise ValueError("missing_timezone")

    cached_timezone = _user_timezone_by_user.get(user_id)
    if cached_timezone:
        return cached_timezone

    raise ValueError("missing_timezone")


def require_user_timezone() -> str:
    """Require a valid timezone from user context/cache."""
    return _get_user_timezone()


def parse_iso_preserve_timezone(value: str) -> datetime:
    """Parse ISO datetime while preserving UTC markers and offsets."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid_datetime")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def today_in_user_timezone() -> str:
    """Return YYYY-MM-DD for the user's timezone."""
    tz_name = require_user_timezone()
    return datetime.now(ZoneInfo(tz_name)).date().isoformat()


def local_day_bounds(date_str: str, tz_name: str) -> tuple[datetime, datetime]:
    """Build start/end datetimes for a local calendar day (end is exclusive)."""
    tz = ZoneInfo(tz_name)
    start = datetime.fromisoformat(f"{date_str}T00:00:00").replace(tzinfo=tz)
    # Do not assume local days are always 24 hours (DST transitions).
    next_day = (start + timedelta(days=1)).date().isoformat()
    end_exclusive = datetime.fromisoformat(f"{next_day}T00:00:00").replace(tzinfo=tz)
    return start, end_exclusive


def _is_hhmm(value: str) -> bool:
    try:
        datetime.strptime(value, "%H:%M")
        return True
    except ValueError:
        return False


def _parse_user_datetime(
    value: str,
    tz_name: str,
    *,
    default_date: str | None = None,
) -> datetime:
    """
    Parse time input for user operations.

    - HH:MM is interpreted on default_date (or today's local user date)
    - ISO with offset/Z preserves instant and converts into user timezone
    - Naive ISO is interpreted as local user time
    """
    tz = ZoneInfo(tz_name)
    if _is_hhmm(value):
        date_str = default_date or datetime.now(tz).date().isoformat()
        parsed_local = datetime.fromisoformat(f"{date_str}T{value}:00")
        return parsed_local.replace(tzinfo=tz)

    parsed = parse_iso_preserve_timezone(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _run_async_task(coro, *, label: str) -> None:
    """Run coroutine from sync tool code in-loop or in a temporary loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.run(coro)
        except Exception as exc:
            logger.warning("[REMINDER] %s failed: %s", label, exc)
        return

    task = loop.create_task(coro)

    def _done_callback(done_task):
        try:
            done_task.result()
        except Exception as exc:
            logger.warning("[REMINDER] %s failed: %s", label, exc)

    task.add_done_callback(_done_callback)


# ========================================
# METADATA HELPERS (Task-Event Linking)
# ========================================

def extract_event_id(notes: str) -> str | None:
    """
    Extract event_id from task notes metadata.
    
    Args:
        notes: Task notes that may contain __INTENTIVE_META__:event_id=...
    
    Returns:
        Event ID string or None if not found
    """
    if not notes:
        return None
    
    # Look for __INTENTIVE_META__:event_id=...
    marker = "__INTENTIVE_META__:event_id="
    if marker in notes:
        start_idx = notes.index(marker) + len(marker)
        # Find end of event_id (whitespace or newline)
        end_idx = start_idx
        while end_idx < len(notes) and notes[end_idx] not in [' ', '\n', '\r', '\t']:
            end_idx += 1
        return notes[start_idx:end_idx]
    
    return None


def inject_event_id(notes: str, event_id: str) -> str:
    """
    Inject event_id link into task notes.
    
    Args:
        notes: Existing task notes
        event_id: Calendar event ID to link
    
    Returns:
        Updated notes with metadata appended
    """
    if not notes:
        notes = ""
    
    # Avoid duplicate injection
    if "__INTENTIVE_META__:event_id=" in notes:
        return notes
    
    # Append metadata on new line
    if notes and not notes.endswith("\n"):
        notes += "\n"
    
    return f"{notes}__INTENTIVE_META__:event_id={event_id}"


# ========================================
# CALENDAR TOOLS
# ========================================

def list_todays_events() -> dict:
    """
    Lists all calendar events for today.
    
    Returns:
        Structured dict with events data and human-readable message.
    """
    try:
        user_tz = require_user_timezone()
        today = today_in_user_timezone()
        day_start, day_end = local_day_bounds(today, user_tz)
        result = _get_entity().execute(
            action=Action.GOOGLECALENDAR_EVENTS_LIST,
            params={
                "calendar_id": "primary",
                "time_min": day_start.isoformat(),
                "time_max": day_end.isoformat(),
                "timezone": user_tz,
                "single_events": True,
                "order_by": "startTime"
            }
        )
        
        # Extract from nested data structure
        data = result.get("data", result)
        raw_events = data.get("items", [])
        
        # Transform to frontend format
        events = []
        message_parts = []
        for event in raw_events:
            start_raw = event.get("start", {}).get("dateTime", event.get("start", {}).get("date", ""))
            end_raw = event.get("end", {}).get("dateTime", event.get("end", {}).get("date", ""))
            
            events.append({
                "id": event.get("id", ""),
                "title": event.get("summary", "No title"),
                "start_time": start_raw,
                "end_time": end_raw,
                "description": event.get("description", "")
            })
            
            # Build human-readable message
            if "T" in start_raw:
                time_part = start_raw.split("T")[1][:5]
                message_parts.append(f"{time_part}: {event.get('summary', 'No title')}")
            else:
                message_parts.append(f"All day: {event.get('summary', 'No title')}")
        
        if not events:
            return {
                "success": True,
                "data": {"events": [], "tasks": []},
                "message": "Your calendar is clear for today!"
            }
        
        return {
            "success": True,
            "data": {"events": events, "tasks": []},
            "message": f"Here's your schedule: {', '.join(message_parts)}"
        }
    except Exception as e:
        logger.error(f"Error listing events: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "data": {"events": [], "tasks": []},
            "message": f"Couldn't fetch calendar. Error: {str(e)}"
        }


def create_calendar_event(title: str, start_time: str, duration_minutes: int = 60, description: str = "") -> dict:
    """
    Creates a new calendar event.
    
    Args:
        title: Event title
        start_time: Start time in format "HH:MM" (24hr) or "2024-01-15T14:00:00"
        duration_minutes: How long the event is (default 60 min)
        description: Optional description
    
    Returns:
        Structured dict with created event data.
    """
    try:
        user_tz = require_user_timezone()
        today = today_in_user_timezone()
        start_dt = _parse_user_datetime(start_time, user_tz, default_date=today)
        
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        
        # Calculate hours and minutes
        duration_hours = duration_minutes // 60
        duration_mins = duration_minutes % 60
        
        result = _get_entity().execute(
            action=Action.GOOGLECALENDAR_CREATE_EVENT,
            params={
                "summary": title,
                "start_datetime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "timezone": user_tz,
                "event_duration_hour": duration_hours,
                "event_duration_minutes": duration_mins,
                "description": description,
                "create_meeting_room": False
            }
        )
        
        # Check for errors
        if result.get("successful") == False:
            error = result.get("error", "Unknown error")
            logger.error(f"Calendar API error: {error}")
            return {
                "success": False,
                "error": error[:100],
                "message": f"Couldn't create event: {error[:100]}"
            }
        
        # Extract event ID from result
        event_data = result.get("data", result)
        event_id = event_data.get("id", "")
        
        created_event = {
            "id": event_id,
            "title": title,
            "start_time": start_dt.isoformat(),
            "end_time": end_dt.isoformat(),
            "description": description
        }
        
        return {
            "success": True,
            "data": {"event": created_event},
            "message": f"Created '{title}' at {start_dt.strftime('%I:%M %p')}"
        }
    except Exception as e:
        logger.error(f"Error creating event: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": f"Couldn't create event. Error: {str(e)}"
        }


def find_free_slots(duration_minutes: int = 30) -> dict:
    """
    Finds available time slots in today's calendar.
    
    Args:
        duration_minutes: Minimum duration needed (default 30 min)
    
    Returns:
        Structured dict with free time slots.
    """
    try:
        user_tz = require_user_timezone()
        today = today_in_user_timezone()
        local_start = datetime.fromisoformat(f"{today}T08:00:00").replace(
            tzinfo=ZoneInfo(user_tz)
        )
        local_end = datetime.fromisoformat(f"{today}T20:00:00").replace(
            tzinfo=ZoneInfo(user_tz)
        )
        result = _get_entity().execute(
            action=Action.GOOGLECALENDAR_FIND_FREE_SLOTS,
            params={
                "time_min": local_start.isoformat(),
                "time_max": local_end.isoformat(),
                "timezone": user_tz,
                "calendar_ids": ["primary"]
            }
        )
        
        raw_slots = result.get("free_slots", [])
        
        # Transform to frontend format
        slots = []
        message_parts = []
        for slot in raw_slots:
            start = slot.get("start", "")
            end = slot.get("end", "")
            
            # Calculate duration
            slot_duration = 0
            if "T" in start and "T" in end:
                try:
                    start_dt = parse_iso_preserve_timezone(start)
                    end_dt = parse_iso_preserve_timezone(end)
                    slot_duration = int((end_dt - start_dt).total_seconds() / 60)
                except Exception:
                    slot_duration = 0
            
            slots.append({
                "start": start,
                "end": end,
                "duration_minutes": slot_duration
            })
            
            if "T" in start and "T" in end:
                start_time = start.split("T")[1][:5]
                end_time = end.split("T")[1][:5]
                message_parts.append(f"{start_time}-{end_time}")
        
        if not slots:
            return {
                "success": True,
                "data": {"slots": []},
                "message": "No free slots found today. Your calendar is packed!"
            }
        
        return {
            "success": True,
            "data": {"slots": slots},
            "message": f"Free time slots: {', '.join(message_parts)}"
        }
    except Exception as e:
        logger.error(f"Error finding free slots: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "data": {"slots": []},
            "message": f"Couldn't find free slots. Error: {str(e)}"
        }


def delete_event(event_title: str) -> dict:
    """
    Deletes a calendar event by searching for its title.
    
    Args:
        event_title: The title (or part of it) of the event to delete
    
    Returns:
        Structured dict with deletion confirmation.
    """
    try:
        user_tz = require_user_timezone()
        today = today_in_user_timezone()
        day_start, _ = local_day_bounds(today, user_tz)
        # First find the event
        result = _get_entity().execute(
            action=Action.GOOGLECALENDAR_FIND_EVENT,
            params={
                "calendar_id": "primary",
                "query": event_title,
                "time_min": day_start.isoformat(),
                "timezone": user_tz,
            }
        )
        
        events = result.get("items", [])
        if not events:
            return {
                "success": False,
                "error": "not_found",
                "message": f"Couldn't find an event matching '{event_title}'"
            }
        
        # Delete the first matching event
        event = events[0]
        _get_entity().execute(
            action=Action.GOOGLECALENDAR_DELETE_EVENT,
            params={
                "calendar_id": "primary",
                "event_id": event.get("id")
            }
        )
        
        deleted_title = event.get('summary', event_title)
        return {
            "success": True,
            "data": {"deleted_event_id": event.get("id"), "deleted_title": deleted_title},
            "message": f"Deleted event: {deleted_title}"
        }
    except Exception as e:
        logger.error(f"Error deleting event: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": f"Couldn't delete event. Error: {str(e)}"
        }


def find_event(query: str) -> dict:
    """
    Searches for calendar events by text.
    
    Args:
        query: Search text to find in event titles/descriptions
    
    Returns:
        Structured dict with matching events.
    """
    try:
        result = _get_entity().execute(
            action=Action.GOOGLECALENDAR_FIND_EVENT,
            params={
                "calendar_id": "primary",
                "query": query
            }
        )
        
        raw_events = result.get("items", [])
        
        # Transform to frontend format (limit to 5)
        events = []
        message_parts = []
        for event in raw_events[:5]:
            start_raw = event.get("start", {}).get("dateTime", event.get("start", {}).get("date", ""))
            end_raw = event.get("end", {}).get("dateTime", event.get("end", {}).get("date", ""))
            
            events.append({
                "id": event.get("id", ""),
                "title": event.get("summary", "No title"),
                "start_time": start_raw,
                "end_time": end_raw,
                "description": event.get("description", "")
            })
            
            if "T" in start_raw:
                date_part = start_raw.split("T")[0]
                time_part = start_raw.split("T")[1][:5]
                message_parts.append(f"{date_part} {time_part}: {event.get('summary', 'No title')}")
            else:
                message_parts.append(f"{start_raw}: {event.get('summary', 'No title')}")
        
        if not events:
            return {
                "success": True,
                "data": {"events": []},
                "message": f"No events found matching '{query}'"
            }
        
        return {
            "success": True,
            "data": {"events": events},
            "message": f"Events matching '{query}': {'; '.join(message_parts)}"
        }
    except Exception as e:
        logger.error(f"Error finding events: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "data": {"events": []},
            "message": f"Couldn't search events. Error: {str(e)}"
        }


def modify_event(event_title: str, new_title: str = None, new_start_time: str = None, 
                  new_duration_minutes: int = None, new_description: str = None) -> dict:
    """
    Modifies an existing calendar event by searching for its title.
    
    Args:
        event_title: The title (or part of it) of the event to modify
        new_title: New title for the event (optional)
        new_start_time: New start time in format "HH:MM" or ISO format (optional)
        new_duration_minutes: New duration in minutes (optional)
        new_description: New description (optional)
    
    Returns:
        Structured dict with updated event data.
    """
    try:
        user_tz = require_user_timezone()
        today = today_in_user_timezone()
        day_start, _ = local_day_bounds(today, user_tz)
        # First find the event
        result = _get_entity().execute(
            action=Action.GOOGLECALENDAR_FIND_EVENT,
            params={
                "calendar_id": "primary",
                "query": event_title,
                "time_min": day_start.isoformat(),
                "timezone": user_tz,
            }
        )
        
        events = result.get("items", [])
        if not events:
            return {
                "success": False,
                "error": "not_found",
                "message": f"Couldn't find an event matching '{event_title}'"
            }
        
        event = events[0]
        event_id = event.get("id")
        
        # Build patch params with only provided fields
        patch_params = {
            "calendar_id": "primary",
            "event_id": event_id
        }
        
        if new_title:
            patch_params["summary"] = new_title
        
        if new_description is not None:
            patch_params["description"] = new_description
        
        # Track final start/end times for response
        final_start = event.get("start", {}).get("dateTime", "")
        final_end = event.get("end", {}).get("dateTime", "")
        
        if new_start_time:
            start_dt = _parse_user_datetime(new_start_time, user_tz, default_date=today)
            
            patch_params["start"] = {"dateTime": start_dt.isoformat(), "timeZone": user_tz}
            
            # Calculate end time
            duration = new_duration_minutes or 60
            end_dt = start_dt + timedelta(minutes=duration)
            patch_params["end"] = {"dateTime": end_dt.isoformat(), "timeZone": user_tz}
            
            final_start = start_dt.isoformat()
            final_end = end_dt.isoformat()
        elif new_duration_minutes:
            # Keep existing start, just update duration
            existing_start = event.get("start", {}).get("dateTime", "")
            if existing_start:
                start_dt = _parse_user_datetime(existing_start, user_tz, default_date=today)
                end_dt = start_dt + timedelta(minutes=new_duration_minutes)
                patch_params["end"] = {"dateTime": end_dt.isoformat(), "timeZone": user_tz}
                final_end = end_dt.isoformat()
        
        # Execute the patch
        _get_entity().execute(
            action=Action.GOOGLECALENDAR_PATCH_EVENT,
            params=patch_params
        )
        
        changes = []
        if new_title:
            changes.append(f"title → '{new_title}'")
        if new_start_time:
            changes.append(f"time → {new_start_time}")
        if new_duration_minutes:
            changes.append(f"duration → {new_duration_minutes}min")
        if new_description is not None:
            changes.append("description updated")
        
        original_title = event.get('summary', event_title)
        updated_event = {
            "id": event_id,
            "title": new_title or original_title,
            "start_time": final_start,
            "end_time": final_end,
            "description": new_description if new_description is not None else event.get("description", "")
        }
        
        return {
            "success": True,
            "data": {"event": updated_event},
            "message": f"Updated '{original_title}': {', '.join(changes)}"
        }
    except Exception as e:
        logger.error(f"Error modifying event: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": f"Couldn't modify event. Error: {str(e)}"
        }


# ========================================
# UNIFIED TASK-EVENT OPERATIONS
# ========================================

def timeblock_task(task_title: str, start_time: str, duration_minutes: int = 60, description: str = "") -> dict:
    """
    Schedule an existing task to the calendar (creates task-event link).
    
    Args:
        task_title: Title of the task to schedule
        start_time: Start time in format "HH:MM" (24hr) or "2024-01-15T14:00:00"
        duration_minutes: Duration in minutes (default 60)
        description: Optional description for the calendar event
    
    Returns:
        Structured dict with event_id, task_id, and status.
    """
    try:
        # 1. Find the task
        tasks = _fetch_all_tasks(show_completed=False)
        matching_task = None
        
        for task in tasks:
            if task_title.lower() in task.get("title", "").lower():
                matching_task = task
                break
        
        if not matching_task:
            return {
                "success": False,
                "error": "not_found",
                "message": f"Couldn't find a task matching '{task_title}'"
            }
        
        # 2. Create calendar event
        event_result = create_calendar_event(
            title=matching_task.get("title"),
            start_time=start_time,
            duration_minutes=duration_minutes,
            description=description or matching_task.get("notes", "")
        )
        
        if not event_result.get("success"):
            return event_result
        
        # 3. Extract event ID
        event_id = event_result["data"]["event"]["id"]
        
        # 4. Update task notes with metadata link
        existing_notes = matching_task.get("notes", "")
        updated_notes = inject_event_id(existing_notes, event_id)
        
        # Update the task
        try:
            _get_entity().execute(
                action=Action.GOOGLETASKS_PATCH_TASK,
                params={
                    "tasklist_id": matching_task.get("tasklist_id"),
                    "task_id": matching_task.get("id"),
                    "notes": updated_notes
                }
            )
        except Exception as e:
            logger.warning(f"Failed to update task notes with event link: {e}")
            # Event created but linking failed - not critical
        
        try:
            tool_user_id = current_user_id.get()
        except LookupError:
            tool_user_id = None

        if tool_user_id and event_id:
            try:
                user_timezone = _get_user_timezone()
                _run_async_task(
                    schedule_calendar_reminder(
                        user_id=tool_user_id,
                        calendar_event_id=event_id,
                        event_title=event_result["data"]["event"]["title"],
                        event_start_time=event_result["data"]["event"]["start_time"],
                        timezone_name=user_timezone,
                        lead_minutes=5,
                        source="agent_timeblock",
                    ),
                    label=f"schedule reminder calendar_event_id={event_id}",
                )
            except Exception as reminder_error:
                logger.warning(
                    "[REMINDER] Failed to enqueue reminder for event %s: %s",
                    event_id,
                    reminder_error,
                )

        return {
            "success": True,
            "data": {
                "event_id": event_id,
                "task_id": matching_task.get("id"),
                "status": "scheduled",
                "event": event_result["data"]["event"]
            },
            "message": f"Scheduled '{matching_task.get('title')}' at {start_time} for {duration_minutes} minutes"
        }
    
    except Exception as e:
        logger.error(f"Error timeblocking task: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": f"Couldn't timeblock task. Error: {str(e)}"
        }


def get_schedule(date: str = "today", after_time: str = None, include_tasks: bool = True) -> dict:
    """
    Query calendar events for a specific date with optional time filtering.
    
    Args:
        date: Date in format "YYYY-MM-DD" or "today" (default: "today")
        after_time: Optional time filter "HH:MM" - only return events starting after this time
        include_tasks: If True, include linked task metadata (default: True)
    
    Returns:
        Structured dict with filtered events list.
    """
    try:
        user_tz = require_user_timezone()
        # Parse date
        if date == "today":
            target_date = today_in_user_timezone()
        else:
            # Validate date format
            try:
                datetime.strptime(date, "%Y-%m-%d")
                target_date = date
            except ValueError:
                return {
                    "success": False,
                    "error": "invalid_date",
                    "message": f"Invalid date format: {date}. Use 'today' or 'YYYY-MM-DD'"
                }
        if after_time and not _is_hhmm(after_time):
            return {
                "success": False,
                "error": "invalid_after_time",
                "message": f"Invalid after_time format: {after_time}. Use 'HH:MM'"
            }

        day_start, day_end = local_day_bounds(target_date, user_tz)
        
        # Fetch events for the full day
        result = _get_entity().execute(
            action=Action.GOOGLECALENDAR_EVENTS_LIST,
            params={
                "calendar_id": "primary",
                "time_min": day_start.isoformat(),
                "time_max": day_end.isoformat(),
                "timezone": user_tz,
                "single_events": True,
                "order_by": "startTime"
            }
        )
        
        # Extract events
        data = result.get("data", result)
        raw_events = data.get("items", [])
        
        # Transform and filter events
        events = []
        for event in raw_events:
            start_raw = event.get("start", {}).get("dateTime", event.get("start", {}).get("date", ""))
            end_raw = event.get("end", {}).get("dateTime", event.get("end", {}).get("date", ""))
            
            # Apply after_time filter
            if after_time and "T" in start_raw:
                event_time = start_raw.split("T")[1][:5]  # Extract HH:MM
                if event_time < after_time:
                    continue
            
            event_obj = {
                "id": event.get("id", ""),
                "title": event.get("summary", "No title"),
                "start_time": start_raw,
                "end_time": end_raw,
                "description": event.get("description", "")
            }
            
            # Include linked task info if requested
            if include_tasks:
                # Check if this event is linked to a task (search tasks for this event_id)
                event_obj["linked_task"] = None  # TODO: Implement reverse lookup if needed
            
            events.append(event_obj)
        
        # Build message
        if not events:
            if after_time:
                message = f"No events found after {after_time} on {target_date}"
            else:
                message = f"No events found on {target_date}"
        else:
            if after_time:
                message = f"Found {len(events)} event(s) after {after_time}"
            else:
                message = f"Found {len(events)} event(s) on {target_date}"
        
        return {
            "success": True,
            "data": {"events": events},
            "message": message
        }
    
    except Exception as e:
        logger.error(f"Error getting schedule: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "data": {"events": []},
            "message": f"Couldn't fetch schedule. Error: {str(e)}"
        }


def check_conflicts(start_time: str, end_time: str) -> dict:
    """
    Check for calendar conflicts in a given time range.
    
    Args:
        start_time: Start time in format "HH:MM" or "YYYY-MM-DDTHH:MM:SS"
        end_time: End time in format "HH:MM" or "YYYY-MM-DDTHH:MM:SS"
    
    Returns:
        Structured dict with has_conflicts flag and list of conflicting events.
    """
    try:
        user_tz = require_user_timezone()
        today = today_in_user_timezone()
        start_dt = _parse_user_datetime(start_time, user_tz, default_date=today)
        end_dt = _parse_user_datetime(end_time, user_tz, default_date=today)
        if end_dt <= start_dt:
            return {
                "success": False,
                "error": "invalid_time_range",
                "data": {"has_conflicts": False, "conflicts": []},
                "message": "end_time must be after start_time"
            }
        
        # Query events in the time range
        result = _get_entity().execute(
            action=Action.GOOGLECALENDAR_EVENTS_LIST,
            params={
                "calendar_id": "primary",
                "time_min": start_dt.isoformat(),
                "time_max": end_dt.isoformat(),
                "timezone": user_tz,
                "single_events": True,
                "order_by": "startTime"
            }
        )
        
        # Extract events
        data = result.get("data", result)
        raw_events = data.get("items", [])
        
        # Check for overlaps (simple overlap: event exists in the range)
        conflicts = []
        for event in raw_events:
            event_start = event.get("start", {}).get("dateTime", "")
            event_end = event.get("end", {}).get("dateTime", "")
            
            if event_start and event_end:
                conflicts.append({
                    "id": event.get("id", ""),
                    "title": event.get("summary", "No title"),
                    "start_time": event_start,
                    "end_time": event_end
                })
        
        has_conflicts = len(conflicts) > 0
        
        if has_conflicts:
            conflict_titles = [c["title"] for c in conflicts]
            message = f"Found {len(conflicts)} conflict(s): {', '.join(conflict_titles)}"
        else:
            message = f"No conflicts found between {start_time} and {end_time}"
        
        return {
            "success": True,
            "data": {
                "has_conflicts": has_conflicts,
                "conflicts": conflicts
            },
            "message": message
        }
    
    except Exception as e:
        logger.error(f"Error checking conflicts: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "data": {"has_conflicts": False, "conflicts": []},
            "message": f"Couldn't check conflicts. Error: {str(e)}"
        }


# ========================================
# TASKS TOOLS
# ========================================

def _fetch_all_tasks(show_completed: bool = False) -> list:
    """Helper to fetch tasks from ALL task lists."""
    try:
        # 1. Get all task lists
        lists_result = _get_entity().execute(
            action=Action.GOOGLETASKS_LIST_TASK_LISTS,
            params={}
        )
        lists_data = lists_result.get("data", lists_result)
        task_lists = lists_data.get("items", [])
        
        if not task_lists:
            return []
            
        all_tasks = []
        
        # 2. Iterate each list
        for tl in task_lists:
            list_id = tl.get("id")
            
            try:
                tasks_result = _get_entity().execute(
                    action=Action.GOOGLETASKS_LIST_TASKS,
                    params={"tasklist_id": list_id, "showCompleted": show_completed}
                )
                t_data = tasks_result.get("data", tasks_result)
                # Handle both 'items' and 'tasks' keys
                tasks = t_data.get("items", t_data.get("tasks", []))
                
                # Add list context to tasks
                if tasks:
                    for t in tasks:
                        t["tasklist_id"] = list_id
                        all_tasks.append(t)
            except Exception as inner_e:
                logger.warning(f"Failed to fetch tasks for list {list_id}: {inner_e}")
                continue
                
        return all_tasks
    except Exception as e:
        logger.error(f"Error fetching all tasks: {e}")
        return []

def list_all_tasks() -> dict:
    """
    Lists all incomplete tasks across all task lists.
    
    Returns:
        Structured dict with tasks data and message.
    """
    try:
        raw_tasks = _fetch_all_tasks(show_completed=False)
        
        # Transform to frontend format
        tasks = []
        message_parts = []
        for task in raw_tasks:
            title = task.get("title", "No title")
            notes = task.get("notes", "")
            is_goal_linked = "[GOAL:" in notes
            # Clean notes by removing goal tag
            clean_notes = notes.replace("[GOAL:yearly]", "").strip() if is_goal_linked else notes
            
            tasks.append({
                "id": task.get("id", ""),
                "title": title,
                "notes": clean_notes,
                "due": task.get("due", ""),
                "status": "pending",
                "is_goal_linked": is_goal_linked,
                "tasklist_id": task.get("tasklist_id", "")
            })
            
            if is_goal_linked:
                message_parts.append(f"[goal] {title}")
            else:
                message_parts.append(title)
        
        if not tasks:
            return {
                "success": True,
                "data": {"tasks": []},
                "message": "You have no tasks! Your slate is clean."
            }
        
        return {
            "success": True,
            "data": {"tasks": tasks},
            "message": f"Your tasks: {', '.join(message_parts)}"
        }
    except Exception as e:
        logger.error(f"Error listing tasks: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "data": {"tasks": []},
            "message": f"Couldn't fetch tasks. Error: {str(e)}"
        }


def get_tasks_filtered(status: str = "pending") -> dict:
    """
    Query tasks with filtering by scheduling status.
    
    Args:
        status: Filter by status - "pending" (unscheduled), "scheduled" (has event link), 
                "completed", or "all"
    
    Returns:
        Structured dict with filtered tasks and counts.
    """
    try:
        # Fetch tasks based on status
        show_completed = status in ["completed", "all"]
        raw_tasks = _fetch_all_tasks(show_completed=show_completed)
        
        # Categorize tasks
        pending_tasks = []
        scheduled_tasks = []
        completed_tasks = []
        
        for task in raw_tasks:
            title = task.get("title", "No title")
            notes = task.get("notes", "")
            task_status = task.get("status", "needsAction")
            
            # Check if task is linked to event
            event_id = extract_event_id(notes)
            is_scheduled = event_id is not None
            
            # Check goal linking (existing feature)
            is_goal_linked = "[GOAL:" in notes
            # Clean notes by removing metadata
            clean_notes = notes.replace("[GOAL:yearly]", "").strip() if is_goal_linked else notes
            # Also remove event link metadata from display
            if event_id:
                clean_notes = clean_notes.replace(f"__INTENTIVE_META__:event_id={event_id}", "").strip()
            
            task_obj = {
                "id": task.get("id", ""),
                "title": title,
                "notes": clean_notes,
                "due": task.get("due", ""),
                "is_goal_linked": is_goal_linked,
                "tasklist_id": task.get("tasklist_id", ""),
                "event_id": event_id
            }
            
            # Categorize
            if task_status == "completed":
                task_obj["status"] = "completed"
                completed_tasks.append(task_obj)
            elif is_scheduled:
                task_obj["status"] = "scheduled"
                scheduled_tasks.append(task_obj)
            else:
                task_obj["status"] = "pending"
                pending_tasks.append(task_obj)
        
        # Filter by requested status
        if status == "pending":
            filtered_tasks = pending_tasks
        elif status == "scheduled":
            filtered_tasks = scheduled_tasks
        elif status == "completed":
            filtered_tasks = completed_tasks
        elif status == "all":
            filtered_tasks = pending_tasks + scheduled_tasks + completed_tasks
        else:
            return {
                "success": False,
                "error": "invalid_status",
                "message": f"Invalid status: {status}. Use 'pending', 'scheduled', 'completed', or 'all'"
            }
        
        # Build message
        counts = {
            "pending": len(pending_tasks),
            "scheduled": len(scheduled_tasks),
            "completed": len(completed_tasks)
        }
        
        if not filtered_tasks:
            message = f"No {status} tasks found"
        else:
            message = f"Found {len(filtered_tasks)} {status} task(s)"
        
        return {
            "success": True,
            "data": {
                "tasks": filtered_tasks,
                "counts": counts
            },
            "message": message
        }
    
    except Exception as e:
        logger.error(f"Error getting filtered tasks: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "data": {"tasks": [], "counts": {}},
            "message": f"Couldn't fetch tasks. Error: {str(e)}"
        }


def add_task(title: str, linked_to_goal: bool = False, notes: str = "") -> dict:
    """
    Adds a new task.
    
    Args:
        title: Task title
        linked_to_goal: If True, marks this task as contributing to the yearly goal
        notes: Optional notes
    
    Returns:
        Structured dict with created task data.
    """
    try:
        # First get the default task list
        lists_result = _get_entity().execute(
            action=Action.GOOGLETASKS_LIST_TASK_LISTS,
            params={}
        )
        
        data = lists_result.get("data", lists_result)
        task_lists = data.get("items", [])
        if not task_lists:
            return {
                "success": False,
                "error": "no_task_lists",
                "message": "No task lists found. Please create one in Google Tasks first."
            }
        
        default_list_id = task_lists[0]["id"]
        
        # Add goal tag if linked
        task_notes = notes
        if linked_to_goal:
            task_notes = f"[GOAL:yearly] {notes}".strip()
        
        result = _get_entity().execute(
            action=Action.GOOGLETASKS_INSERT_TASK,
            params={
                "tasklist_id": default_list_id,
                "title": title,
                "notes": task_notes
            }
        )
        
        # Extract task data from result
        task_data = result.get("data", result)
        actual_task = task_data.get("task", task_data)
        task_id = actual_task.get("id", "")
        
        created_task = {
            "id": task_id,
            "title": title,
            "notes": notes,
            "due": "",
            "status": "pending",
            "is_goal_linked": linked_to_goal
        }
        
        message = f"Added '{title}' linked to your yearly goal" if linked_to_goal else f"Added task: {title}"
        
        return {
            "success": True,
            "data": {"task": created_task},
            "message": message
        }
    except Exception as e:
        logger.error(f"Error adding task: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": f"Couldn't add task. Error: {str(e)}"
        }


def complete_task(task_title: str) -> dict:
    """
    Marks a task as complete by searching for its title.
    
    Args:
        task_title: The title (or part of it) of the task to complete
    
    Returns:
        Structured dict with completed task data.
    """
    try:
        # Get all tasks to find the matching one
        tasks = _fetch_all_tasks(show_completed=False)
        
        matching = None
        for task in tasks:
            if task_title.lower() in task.get("title", "").lower():
                matching = task
                break
        
        if not matching:
            return {
                "success": False,
                "error": "not_found",
                "message": f"Couldn't find a task matching '{task_title}'"
            }
        
        # Mark it complete
        _get_entity().execute(
            action=Action.GOOGLETASKS_PATCH_TASK,
            params={
                "tasklist_id": matching.get("tasklist_id"),
                "task_id": matching.get("id"),
                "status": "completed"
            }
        )
        
        completed_title = matching.get('title', task_title)
        notes = matching.get("notes", "")
        is_goal_linked = "[GOAL:" in notes
        clean_notes = notes.replace("[GOAL:yearly]", "").strip() if is_goal_linked else notes
        
        completed_task = {
            "id": matching.get("id", ""),
            "title": completed_title,
            "notes": clean_notes,
            "due": matching.get("due", ""),
            "status": "completed",
            "is_goal_linked": is_goal_linked
        }
        
        return {
            "success": True,
            "data": {"task": completed_task},
            "message": f"Completed: {completed_title}"
        }
    except Exception as e:
        logger.error(f"Error completing task: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": f"Couldn't complete task. Error: {str(e)}"
        }


def delete_task(task_title: str) -> dict:
    """
    Deletes a task by searching for its title.
    Also deletes linked calendar event if one exists.
    
    Args:
        task_title: The title (or part of it) of the task to delete
    
    Returns:
        Structured dict with deletion confirmation.
    """
    try:
        # Get all tasks to find the matching one
        tasks = _fetch_all_tasks(show_completed=False)
        
        matching = None
        for task in tasks:
            if task_title.lower() in task.get("title", "").lower():
                matching = task
                break
        
        if not matching:
            return {
                "success": False,
                "error": "not_found",
                "message": f"Couldn't find a task matching '{task_title}'"
            }
        
        # Check if task has linked calendar event
        notes = matching.get("notes", "")
        event_id = extract_event_id(notes)
        
        # Delete the task
        _get_entity().execute(
            action=Action.GOOGLETASKS_DELETE_TASK,
            params={
                "tasklist_id": matching.get("tasklist_id"),
                "task_id": matching.get("id")
            }
        )
        
        # Also delete linked calendar event if it exists
        removed_event = False
        if event_id:
            try:
                _get_entity().execute(
                    action=Action.GOOGLECALENDAR_DELETE_EVENT,
                    params={
                        "calendar_id": "primary",
                        "event_id": event_id
                    }
                )
                removed_event = True
                logger.info(f"Deleted linked calendar event {event_id} for task '{task_title}'")
                try:
                    tool_user_id = current_user_id.get()
                except LookupError:
                    tool_user_id = None
                if tool_user_id:
                    _run_async_task(
                        cancel_calendar_reminders(
                            user_id=tool_user_id,
                            calendar_event_id=event_id,
                            reason="calendar_event_deleted",
                        ),
                        label=f"cancel reminders calendar_event_id={event_id}",
                    )
            except Exception as e:
                logger.warning(f"Failed to delete linked event {event_id}: {e}")
                # Task is deleted, event deletion failure is not critical
        
        deleted_title = matching.get('title', task_title)
        message = f"Deleted task: {deleted_title}"
        if removed_event:
            message += " (and linked calendar event)"
        
        return {
            "success": True,
            "data": {
                "deleted_task_id": matching.get("id"), 
                "deleted_title": deleted_title,
                "removed_event": removed_event
            },
            "message": message
        }
    except Exception as e:
        logger.error(f"Error deleting task: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": f"Couldn't delete task. Error: {str(e)}"
        }


def modify_task(task_title: str, new_title: str = None, new_notes: str = None, 
                 due_date: str = None, linked_to_goal: bool = None) -> dict:
    """
    Modifies an existing task by searching for its title.
    
    Args:
        task_title: The title (or part of it) of the task to modify
        new_title: New title for the task (optional)
        new_notes: New notes for the task (optional)
        due_date: Due date like "2024-01-15" or "tomorrow" (optional)
        linked_to_goal: If True, marks task as goal-aligned; False removes tag (optional)
    
    Returns:
        Structured dict with updated task data.
    """
    try:
        # Get all tasks to find the matching one
        tasks = _fetch_all_tasks(show_completed=False)
        
        matching = None
        for task in tasks:
            if task_title.lower() in task.get("title", "").lower():
                matching = task
                break
        
        if not matching:
            return {
                "success": False,
                "error": "not_found",
                "message": f"Couldn't find a task matching '{task_title}'"
            }
        
        # Build patch params
        patch_params = {
            "tasklist_id": matching.get("tasklist_id"),
            "task_id": matching.get("id")
        }
        
        # Track final values
        final_notes = matching.get("notes", "")
        final_is_goal_linked = "[GOAL:" in final_notes
        
        if new_title:
            patch_params["title"] = new_title
        
        if new_notes is not None or linked_to_goal is not None:
            # Handle notes with goal tagging
            existing_notes = matching.get("notes", "")
            
            if new_notes is not None:
                # Replace the notes content (preserve goal tag if exists)
                if "[GOAL:" in existing_notes and linked_to_goal is not False:
                    patch_params["notes"] = f"[GOAL:yearly] {new_notes}".strip()
                    final_notes = new_notes
                    final_is_goal_linked = True
                elif linked_to_goal is True:
                    patch_params["notes"] = f"[GOAL:yearly] {new_notes}".strip()
                    final_notes = new_notes
                    final_is_goal_linked = True
                else:
                    patch_params["notes"] = new_notes
                    final_notes = new_notes
                    final_is_goal_linked = False
            elif linked_to_goal is True:
                # Add goal tag to existing notes
                if "[GOAL:" not in existing_notes:
                    patch_params["notes"] = f"[GOAL:yearly] {existing_notes}".strip()
                final_is_goal_linked = True
            elif linked_to_goal is False:
                # Remove goal tag from notes
                patch_params["notes"] = existing_notes.replace("[GOAL:yearly]", "").strip()
                final_notes = patch_params["notes"]
                final_is_goal_linked = False
        
        if due_date:
            patch_params["due"] = due_date
        
        # Execute the patch
        _get_entity().execute(
            action=Action.GOOGLETASKS_PATCH_TASK,
            params=patch_params
        )
        
        changes = []
        if new_title:
            changes.append(f"title → '{new_title}'")
        if new_notes:
            changes.append("notes updated")
        if due_date:
            changes.append(f"due → {due_date}")
        if linked_to_goal is True:
            changes.append("linked to goal")
        elif linked_to_goal is False:
            changes.append("unlinked from goal")
        
        original_title = matching.get('title', task_title)
        # Clean final_notes for response
        clean_notes = final_notes.replace("[GOAL:yearly]", "").strip()
        
        updated_task = {
            "id": matching.get("id", ""),
            "title": new_title or original_title,
            "notes": clean_notes,
            "due": due_date or matching.get("due", ""),
            "status": "pending",
            "is_goal_linked": final_is_goal_linked
        }
        
        return {
            "success": True,
            "data": {"task": updated_task},
            "message": f"Updated '{original_title}': {', '.join(changes)}"
        }
    except Exception as e:
        logger.error(f"Error modifying task: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": f"Couldn't modify task. Error: {str(e)}"
        }


# ========================================
# TASK LIST MANAGEMENT
# ========================================

def list_task_lists() -> dict:
    """
    Lists all task lists in Google Tasks.
    
    Returns:
        Structured dict with task lists data.
    """
    try:
        result = _get_entity().execute(
            action=Action.GOOGLETASKS_LIST_TASK_LISTS,
            params={}
        )
        
        data = result.get("data", result)
        raw_lists = data.get("items", [])
        
        task_lists = []
        message_parts = []
        for tl in raw_lists:
            title = tl.get("title", "Untitled")
            task_lists.append({
                "id": tl.get("id", ""),
                "title": title
            })
            message_parts.append(title)
        
        if not task_lists:
            return {
                "success": True,
                "data": {"task_lists": []},
                "message": "No task lists found. Create one with create_task_list()."
            }
        
        return {
            "success": True,
            "data": {"task_lists": task_lists},
            "message": f"Your task lists: {', '.join(message_parts)}"
        }
    except Exception as e:
        logger.error(f"Error listing task lists: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "data": {"task_lists": []},
            "message": f"Couldn't fetch task lists. Error: {str(e)}"
        }


def create_task_list(title: str) -> dict:
    """
    Creates a new task list.
    
    Args:
        title: Name of the new task list
    
    Returns:
        Structured dict with created task list data.
    """
    try:
        result = _get_entity().execute(
            action=Action.GOOGLETASKS_CREATE_TASK_LIST,
            params={"title": title}
        )
        
        list_data = result.get("data", result)
        list_id = list_data.get("id", "")
        
        return {
            "success": True,
            "data": {"task_list": {"id": list_id, "title": title}},
            "message": f"Created task list: '{title}'"
        }
    except Exception as e:
        logger.error(f"Error creating task list: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": f"Couldn't create task list. Error: {str(e)}"
        }


def delete_task_list(list_name: str) -> dict:
    """
    Deletes a task list by name.
    WARNING: This is destructive and deletes all tasks in the list!
    
    Args:
        list_name: Name of the task list to delete
    
    Returns:
        Structured dict with deletion confirmation.
    """
    try:
        # First find the list by name
        result = _get_entity().execute(
            action=Action.GOOGLETASKS_LIST_TASK_LISTS,
            params={}
        )
        
        data = result.get("data", result)
        task_lists = data.get("items", [])
        matching = None
        for tl in task_lists:
            if list_name.lower() in tl.get("title", "").lower():
                matching = tl
                break
        
        if not matching:
            return {
                "success": False,
                "error": "not_found",
                "message": f"Couldn't find a task list matching '{list_name}'"
            }
        
        _get_entity().execute(
            action=Action.GOOGLETASKS_DELETE_TASK_LIST,
            params={"tasklist_id": matching.get("id")}
        )
        
        deleted_title = matching.get('title', list_name)
        return {
            "success": True,
            "data": {"deleted_list_id": matching.get("id"), "deleted_title": deleted_title},
            "message": f"Deleted task list: '{deleted_title}'"
        }
    except Exception as e:
        logger.error(f"Error deleting task list: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": f"Couldn't delete task list. Error: {str(e)}"
        }


def get_task(task_title: str) -> dict:
    """
    Gets detailed info about a specific task.
    
    Args:
        task_title: Title (or part of it) of the task to find
    
    Returns:
        Structured dict with task details.
    """
    try:
        # Use list all tasks to find across all lists
        tasks = _fetch_all_tasks(show_completed=True)
        
        matching = None
        for task in tasks:
            if task_title.lower() in task.get("title", "").lower():
                matching = task
                break
        
        if not matching:
            return {
                "success": False,
                "error": "not_found",
                "message": f"Couldn't find a task matching '{task_title}'"
            }
        
        title = matching.get('title', 'No title')
        notes = matching.get("notes", "")
        is_goal_linked = "[GOAL:" in notes
        clean_notes = notes.replace("[GOAL:yearly]", "").strip() if is_goal_linked else notes
        status = "completed" if matching.get("status") == "completed" else "pending"
        due = matching.get("due", "")
        if due and "T" in due:
            due = due.split("T")[0]
        
        task_data = {
            "id": matching.get("id", ""),
            "title": title,
            "notes": clean_notes,
            "due": due,
            "status": status,
            "is_goal_linked": is_goal_linked
        }
        
        message_parts = [f"Task: {title}", f"Status: {status}"]
        if clean_notes:
            message_parts.append(f"Notes: {clean_notes}")
        if due:
            message_parts.append(f"Due: {due}")
        
        return {
            "success": True,
            "data": {"task": task_data},
            "message": "; ".join(message_parts)
        }
    except Exception as e:
        logger.error(f"Error getting task: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": f"Couldn't get task. Error: {str(e)}"
        }


def move_task(task_title: str, to_list_name: str) -> dict:
    """
    Moves a task to a different task list.
    
    Args:
        task_title: Title of the task to move
        to_list_name: Name of the destination task list
    
    Returns:
        Structured dict with move confirmation.
    """
    try:
        # Get all task lists to find destination
        lists_result = _get_entity().execute(
            action=Action.GOOGLETASKS_LIST_TASK_LISTS,
            params={}
        )
        
        data = lists_result.get("data", lists_result)
        task_lists = data.get("items", [])
        dest_list = None
        for tl in task_lists:
            if to_list_name.lower() in tl.get("title", "").lower():
                dest_list = tl
                break
        
        if not dest_list:
            return {
                "success": False,
                "error": "not_found",
                "message": f"Couldn't find destination list '{to_list_name}'"
            }
        
        # Find the task
        tasks = _fetch_all_tasks(show_completed=False)
        
        matching = None
        for task in tasks:
            if task_title.lower() in task.get("title", "").lower():
                matching = task
                break
        
        if not matching:
            return {
                "success": False,
                "error": "not_found",
                "message": f"Couldn't find task '{task_title}'"
            }
        
        # Move the task
        _get_entity().execute(
            action=Action.GOOGLETASKS_MOVE_TASK,
            params={
                "tasklist_id": matching.get("tasklist_id"),
                "task_id": matching.get("id"),
                "destination_tasklist_id": dest_list.get("id")
            }
        )
        
        task_title_found = matching.get('title', task_title)
        dest_title = dest_list.get('title', to_list_name)
        
        return {
            "success": True,
            "data": {"task_id": matching.get("id"), "new_list_id": dest_list.get("id")},
            "message": f"Moved '{task_title_found}' to '{dest_title}'"
        }
    except Exception as e:
        logger.error(f"Error moving task: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": f"Couldn't move task. Error: {str(e)}"
        }


def clear_completed_tasks(list_name: str = None) -> dict:
    """
    Clears all completed tasks from a task list.
    
    Args:
        list_name: Name of the list to clear (default: first/primary list)
    
    Returns:
        Structured dict with clear confirmation.
    """
    try:
        # Get task lists
        lists_result = _get_entity().execute(
            action=Action.GOOGLETASKS_LIST_TASK_LISTS,
            params={}
        )
        
        data = lists_result.get("data", lists_result)
        task_lists = data.get("items", [])
        if not task_lists:
            return {
                "success": False,
                "error": "no_task_lists",
                "message": "No task lists found."
            }
        
        target_list = task_lists[0]  # Default to first list
        if list_name:
            for tl in task_lists:
                if list_name.lower() in tl.get("title", "").lower():
                    target_list = tl
                    break
        
        _get_entity().execute(
            action=Action.GOOGLETASKS_CLEAR_TASKS,
            params={"tasklist_id": target_list.get("id")}
        )
        
        list_title = target_list.get('title', 'default')
        return {
            "success": True,
            "data": {"cleared_list_id": target_list.get("id"), "cleared_list_title": list_title},
            "message": f"Cleared completed tasks from '{list_title}'"
        }
    except Exception as e:
        logger.error(f"Error clearing tasks: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": f"Couldn't clear tasks. Error: {str(e)}"
        }


def bulk_add_tasks(tasks: list, list_name: str = None) -> dict:
    """
    Adds multiple tasks at once.
    
    Args:
        tasks: List of dicts with 'title' and optionally 'notes', 'due'
        list_name: Name of the list (default: first/primary list)
    
    Returns:
        Structured dict with bulk add confirmation.
    """
    try:
        # Get task lists
        lists_result = _get_entity().execute(
            action=Action.GOOGLETASKS_LIST_TASK_LISTS,
            params={}
        )
        
        data = lists_result.get("data", lists_result)
        task_lists = data.get("items", [])
        if not task_lists:
            return {
                "success": False,
                "error": "no_task_lists",
                "message": "No task lists found. Create one first with create_task_list()."
            }
        
        target_list = task_lists[0]
        if list_name:
            for tl in task_lists:
                if list_name.lower() in tl.get("title", "").lower():
                    target_list = tl
                    break
        
        _get_entity().execute(
            action=Action.GOOGLETASKS_BULK_INSERT_TASKS,
            params={
                "tasklist_id": target_list.get("id"),
                "tasks": tasks
            }
        )
        
        list_title = target_list.get('title', 'default')
        return {
            "success": True,
            "data": {"tasks_added": len(tasks), "list_title": list_title},
            "message": f"Added {len(tasks)} tasks to '{list_title}'"
        }
    except Exception as e:
        logger.error(f"Error bulk adding tasks: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": f"Couldn't bulk add tasks. Error: {str(e)}"
        }


# ========================================
# UNIFIED TOOL WRAPPERS
# These are the entry points imported by agent.py
# ========================================

def calendar_tool(operation: str, params = None):
    """
    Unified calendar tool router.
    
    Operations:
    - list_today: List today's events
    - create: Create event (params: title, start_time, duration_minutes, description)
    - update: Modify event (params: event_title, new_title, new_start_time, new_duration_minutes, new_description)
    - delete: Delete event (params: event_title)
    - find: Find event (params: query)
    - find_slots: Find free slots (params: duration_minutes)
    
    Args:
        operation: The operation to perform
        params: Parameters for the operation (optional)
    
    Returns:
        Structured dict with success status, data, and message.
    """
    if params is None:
        params = {}
    
    logger.info(f"[CALENDAR_TOOL] >>> operation={operation}, params={params}")
    
    if operation == "list_today":
        result = list_todays_events()
    elif operation == "create":
        result = create_calendar_event(**params)
    elif operation == "update":
        result = modify_event(**params)
    elif operation == "delete":
        result = delete_event(**params)
    elif operation == "find":
        result = find_event(**params)
    elif operation == "find_slots":
        result = find_free_slots(**params)
    else:
        result = {
            "success": False,
            "error": "invalid_operation",
            "message": f"Unknown calendar operation: {operation}. Valid: list_today, create, update, delete, find, find_slots"
        }
    
    logger.info(f"[CALENDAR_TOOL] <<< success={result.get('success')}, has_data={'data' in result}")
    return result


def tasks_tool(operation: str, params = None):
    """
    Unified tasks tool router.
    
    Operations:
    - list: List all tasks
    - add: Add task (params: title, linked_to_goal, notes)
    - complete: Complete task (params: task_title)
    - delete: Delete task (params: task_title)
    - update: Modify task (params: task_title, new_title, new_notes, due_date, linked_to_goal)
    - get: Get task details (params: task_title)
    - move: Move task (params: task_title, to_list_name)
    - bulk_add: Add multiple tasks (params: tasks, list_name)
    - create_list: Create task list (params: title)
    - delete_list: Delete task list (params: list_name)
    - clear_completed: Clear completed tasks (params: list_name)
    - list_lists: List all task lists
    
    Args:
        operation: The operation to perform
        params: Parameters for the operation (optional)
    
    Returns:
        Structured dict with success status, data, and message.
    """
    if params is None:
        params = {}
    
    logger.info(f"[TASKS_TOOL] >>> operation={operation}, params={params}")
    
    if operation == "list":
        result = list_all_tasks()
    elif operation == "add":
        result = add_task(**params)
    elif operation == "complete":
        result = complete_task(**params)
    elif operation == "delete":
        result = delete_task(**params)
    elif operation == "update":
        result = modify_task(**params)
    elif operation == "get":
        result = get_task(**params)
    elif operation == "move":
        result = move_task(**params)
    elif operation == "bulk_add":
        result = bulk_add_tasks(**params)
    elif operation == "create_list":
        result = create_task_list(**params)
    elif operation == "delete_list":
        result = delete_task_list(**params)
    elif operation == "clear_completed":
        result = clear_completed_tasks(**params)
    elif operation == "list_lists":
        result = list_task_lists()
    else:
        result = {
            "success": False,
            "error": "invalid_operation",
            "message": f"Unknown tasks operation: {operation}. Valid: list, add, complete, delete, update, get, move, bulk_add, create_list, delete_list, clear_completed, list_lists"
        }
    
    logger.info(f"[TASKS_TOOL] <<< success={result.get('success')}, has_data={'data' in result}")
    return result


# ========================================
# UNIFIED TASK MANAGEMENT (Agent-Facing Interface)
# ========================================

def task_management(operation: str, params=None) -> dict:
    """
    Unified task management tool (handles Google Tasks + Calendar linking).
    
    This is the main interface for the agent. It abstracts away the complexity
    of managing two separate Google services (Tasks and Calendar) and presents
    a unified task management system.
    
    Operations:
    - add_task: Create unscheduled task (params: title, notes, linked_to_goal)
    - timeblock_task: Schedule task to calendar (params: task_title, start_time, duration_minutes)
    - complete_task: Mark task done (params: task_title)
    - delete_task: Remove task and linked event (params: task_title)
    - get_tasks: Query tasks with filtering (params: status = "pending"|"scheduled"|"completed"|"all")
    - get_schedule: Query calendar events (params: date, after_time, include_tasks)
    - check_conflicts: Validate time slot (params: start_time, end_time)
    
    Args:
        operation: The operation to perform
        params: Parameters for the operation (optional)
    
    Returns:
        Structured dict with success status, data, and message.
    """
    if params is None:
        params = {}

    logger.info(f"[TASK_MGMT] >>> operation={operation}, params={params}")
    try:
        if operation == "add_task":
            result = add_task(**params)
        elif operation == "timeblock_task":
            result = timeblock_task(**params)
        elif operation == "complete_task":
            result = complete_task(**params)
        elif operation == "delete_task":
            result = delete_task(**params)
        elif operation == "get_tasks":
            result = get_tasks_filtered(**params)
        elif operation == "get_schedule":
            result = get_schedule(**params)
        elif operation == "check_conflicts":
            result = check_conflicts(**params)
        else:
            result = {
                "success": False,
                "error": "invalid_operation",
                "message": "Unknown operation: "
                f"{operation}. Valid: add_task, timeblock_task, complete_task, "
                "delete_task, get_tasks, get_schedule, check_conflicts",
            }
    except ValueError as exc:
        reason = str(exc)
        if reason == "missing_timezone":
            result = {
                "success": False,
                "error": "missing_timezone",
                "message": (
                    "Timezone is required before calendar/time operations can run. "
                    "Please send a valid IANA timezone from the client."
                ),
            }
        elif reason == "missing_user_context_for_composio":
            result = {
                "success": False,
                "error": "missing_user_context",
                "message": "User context is missing. Reconnect and try again.",
            }
        else:
            result = {"success": False, "error": reason, "message": reason}
    except Exception as exc:
        error_text = str(exc)
        lowered = error_text.lower()
        if "connected account" in lowered or "authentication" in lowered:
            result = {
                "success": False,
                "error": "composio_not_connected",
                "message": (
                    "Google integration is not connected for this user. "
                    "Please reconnect from app settings."
                ),
            }
        else:
            result = {"success": False, "error": "composio_error", "message": error_text}

    logger.info(f"[TASK_MGMT] <<< success={result.get('success')}, has_data={'data' in result}")
    return result
