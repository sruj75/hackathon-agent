"""
Composio tools for Google Calendar and Tasks.
Scrappy implementation - no fancy abstractions.
"""
import os
from composio import Composio, Action
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

# Use default entity (your connected account)
ENTITY_ID = "default"

# Lazy initialization - client created on first use (after .env is loaded by ADK)
_composio_client = None
_entity = None


def _get_entity():
    """Get or create the Composio entity. Lazy init ensures .env is loaded first."""
    global _composio_client, _entity
    
    if _entity is None:
        api_key = os.environ.get("COMPOSIO_API_KEY")
        if not api_key:
            logger.error("COMPOSIO_API_KEY not found in environment!")
            raise ValueError("COMPOSIO_API_KEY environment variable is not set")
        
        logger.info(f"Initializing Composio client with API key: {api_key[:10]}...")
        _composio_client = Composio(api_key=api_key)
        _entity = _composio_client.get_entity(ENTITY_ID)
        logger.info("Composio entity initialized successfully")
    
    return _entity


# ========================================
# CALENDAR TOOLS
# ========================================

def list_todays_events() -> str:
    """
    Lists all calendar events for today.
    
    Returns:
        Formatted list of today's events.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    
    try:
        result = _get_entity().execute(
            action=Action.GOOGLECALENDAR_EVENTS_LIST,
            params={
                "calendar_id": "primary",
                "time_min": f"{today}T00:00:00",
                "time_max": f"{today}T23:59:59",
                "timezone": "Asia/Kolkata",
                "single_events": True,
                "order_by": "startTime"
            }
        )
        
        # Extract from nested data structure
        data = result.get("data", result)
        events = data.get("items", [])
        if not events:
            return "Your calendar is clear for today!"
        
        output = "Here's your schedule:\n"
        for event in events:
            start = event.get("start", {}).get("dateTime", event.get("start", {}).get("date", ""))
            # Parse and format time nicely
            if "T" in start:
                time_part = start.split("T")[1][:5]
                output += f"• {time_part}: {event.get('summary', 'No title')}\n"
            else:
                output += f"• All day: {event.get('summary', 'No title')}\n"
        return output
    except Exception as e:
        logger.error(f"Error listing events: {e}", exc_info=True)
        return f"Couldn't fetch calendar. Error: {str(e)}"


def create_calendar_event(title: str, start_time: str, duration_minutes: int = 60, description: str = "") -> str:
    """
    Creates a new calendar event.
    
    Args:
        title: Event title
        start_time: Start time in format "HH:MM" (24hr) or "2024-01-15T14:00:00"
        duration_minutes: How long the event is (default 60 min)
        description: Optional description
    
    Returns:
        Confirmation message.
    """
    try:
        # Handle simple time format like "14:00"
        if len(start_time) <= 5 and ":" in start_time:
            today = datetime.now().strftime("%Y-%m-%d")
            start_dt = datetime.fromisoformat(f"{today}T{start_time}:00")
        else:
            start_dt = datetime.fromisoformat(start_time.replace("Z", ""))
        
        # Calculate hours and minutes
        duration_hours = duration_minutes // 60
        duration_mins = duration_minutes % 60
        
        result = _get_entity().execute(
            action=Action.GOOGLECALENDAR_CREATE_EVENT,
            params={
                "summary": title,
                "start_datetime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "timezone": "Asia/Kolkata",
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
            return f"Couldn't create event: {error[:100]}"
        
        return f"Created '{title}' at {start_dt.strftime('%I:%M %p')}"
    except Exception as e:
        logger.error(f"Error creating event: {e}", exc_info=True)
        return f"Couldn't create event. Error: {str(e)}"


def find_free_slots(duration_minutes: int = 30) -> str:
    """
    Finds available time slots in today's calendar.
    
    Args:
        duration_minutes: Minimum duration needed (default 30 min)
    
    Returns:
        List of free time slots.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    
    try:
        result = _get_entity().execute(
            action=Action.GOOGLECALENDAR_FIND_FREE_SLOTS,
            params={
                "time_min": f"{today}T08:00:00Z",
                "time_max": f"{today}T20:00:00Z",
                "calendar_ids": ["primary"]
            }
        )
        
        free_slots = result.get("free_slots", [])
        if not free_slots:
            return "No free slots found today. Your calendar is packed!"
        
        output = "Free time slots today:\n"
        for slot in free_slots:
            start = slot.get("start", "")
            end = slot.get("end", "")
            if "T" in start and "T" in end:
                start_time = start.split("T")[1][:5]
                end_time = end.split("T")[1][:5]
                output += f"• {start_time} - {end_time}\n"
        return output
    except Exception as e:
        logger.error(f"Error finding free slots: {e}", exc_info=True)
        return f"Couldn't find free slots. Error: {str(e)}"


def delete_event(event_title: str) -> str:
    """
    Deletes a calendar event by searching for its title.
    
    Args:
        event_title: The title (or part of it) of the event to delete
    
    Returns:
        Confirmation message.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    
    try:
        # First find the event
        result = _get_entity().execute(
            action=Action.GOOGLECALENDAR_FIND_EVENT,
            params={
                "calendar_id": "primary",
                "query": event_title,
                "time_min": f"{today}T00:00:00Z"
            }
        )
        
        events = result.get("items", [])
        if not events:
            return f"Couldn't find an event matching '{event_title}'"
        
        # Delete the first matching event
        event = events[0]
        _get_entity().execute(
            action=Action.GOOGLECALENDAR_DELETE_EVENT,
            params={
                "calendar_id": "primary",
                "event_id": event.get("id")
            }
        )
        
        return f"Deleted event: {event.get('summary', event_title)} ✓"
    except Exception as e:
        logger.error(f"Error deleting event: {e}", exc_info=True)
        return f"Couldn't delete event. Error: {str(e)}"


def find_event(query: str) -> str:
    """
    Searches for calendar events by text.
    
    Args:
        query: Search text to find in event titles/descriptions
    
    Returns:
        List of matching events.
    """
    try:
        result = _get_entity().execute(
            action=Action.GOOGLECALENDAR_FIND_EVENT,
            params={
                "calendar_id": "primary",
                "query": query
            }
        )
        
        events = result.get("items", [])
        if not events:
            return f"No events found matching '{query}'"
        
        output = f"Events matching '{query}':\n"
        for event in events[:5]:  # Limit to 5
            start = event.get("start", {}).get("dateTime", event.get("start", {}).get("date", ""))
            if "T" in start:
                date_part = start.split("T")[0]
                time_part = start.split("T")[1][:5]
                output += f"• {date_part} {time_part}: {event.get('summary', 'No title')}\n"
            else:
                output += f"• {start}: {event.get('summary', 'No title')}\n"
        return output
    except Exception as e:
        logger.error(f"Error finding events: {e}", exc_info=True)
        return f"Couldn't search events. Error: {str(e)}"


def modify_event(event_title: str, new_title: str = None, new_start_time: str = None, 
                  new_duration_minutes: int = None, new_description: str = None) -> str:
    """
    Modifies an existing calendar event by searching for its title.
    
    Args:
        event_title: The title (or part of it) of the event to modify
        new_title: New title for the event (optional)
        new_start_time: New start time in format "HH:MM" or ISO format (optional)
        new_duration_minutes: New duration in minutes (optional)
        new_description: New description (optional)
    
    Returns:
        Confirmation message.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    
    try:
        # First find the event
        result = _get_entity().execute(
            action=Action.GOOGLECALENDAR_FIND_EVENT,
            params={
                "calendar_id": "primary",
                "query": event_title,
                "time_min": f"{today}T00:00:00Z"
            }
        )
        
        events = result.get("items", [])
        if not events:
            return f"Couldn't find an event matching '{event_title}'"
        
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
        
        if new_start_time:
            # Handle simple time format like "14:00"
            if len(new_start_time) <= 5 and ":" in new_start_time:
                start_dt = datetime.fromisoformat(f"{today}T{new_start_time}:00")
            else:
                start_dt = datetime.fromisoformat(new_start_time.replace("Z", ""))
            
            patch_params["start"] = {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Kolkata"}
            
            # Calculate end time
            duration = new_duration_minutes or 60
            end_dt = start_dt + timedelta(minutes=duration)
            patch_params["end"] = {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Kolkata"}
        elif new_duration_minutes:
            # Keep existing start, just update duration
            existing_start = event.get("start", {}).get("dateTime", "")
            if existing_start:
                start_dt = datetime.fromisoformat(existing_start.replace("Z", ""))
                end_dt = start_dt + timedelta(minutes=new_duration_minutes)
                patch_params["end"] = {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Kolkata"}
        
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
        
        return f"Updated '{event.get('summary', event_title)}': {', '.join(changes)}"
    except Exception as e:
        logger.error(f"Error modifying event: {e}", exc_info=True)
        return f"Couldn't modify event. Error: {str(e)}"


# ========================================
# TASKS TOOLS
# ========================================

def list_all_tasks() -> str:
    """
    Lists all incomplete tasks across all task lists.
    
    Returns:
        Formatted list of tasks.
    """
    try:
        result = _get_entity().execute(
            action=Action.GOOGLETASKS_LIST_TASKS,
            params={"showCompleted": False}
        )
        
        tasks = result.get("tasks", [])
        if not tasks:
            return "You have no tasks! Your slate is clean."
        
        output = "Your tasks:\n"
        for i, task in enumerate(tasks, 1):
            title = task.get("title", "No title")
            notes = task.get("notes", "")
            # Check if task is linked to a goal (in notes)
            if "[GOAL:" in notes:
                output += f"{i}. 🎯 {title}\n"
            else:
                output += f"{i}. ○ {title}\n"
        return output
    except Exception as e:
        logger.error(f"Error listing tasks: {e}", exc_info=True)
        return f"Couldn't fetch tasks. Error: {str(e)}"


def add_task(title: str, linked_to_goal: bool = False, notes: str = "") -> str:
    """
    Adds a new task.
    
    Args:
        title: Task title
        linked_to_goal: If True, marks this task as contributing to the yearly goal
        notes: Optional notes
    
    Returns:
        Confirmation message.
    """
    try:
        # First get the default task list
        lists_result = _get_entity().execute(
            action=Action.GOOGLETASKS_LIST_TASK_LISTS,
            params={}
        )
        
        task_lists = lists_result.get("items", [])
        if not task_lists:
            return "No task lists found. Please create one in Google Tasks first."
        
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
        
        if linked_to_goal:
            return f"Added '{title}' linked to your yearly goal 🎯"
        return f"Added task: {title}"
    except Exception as e:
        logger.error(f"Error adding task: {e}", exc_info=True)
        return f"Couldn't add task. Error: {str(e)}"


def complete_task(task_title: str) -> str:
    """
    Marks a task as complete by searching for its title.
    
    Args:
        task_title: The title (or part of it) of the task to complete
    
    Returns:
        Confirmation message.
    """
    try:
        # Get all tasks to find the matching one
        result = _get_entity().execute(
            action=Action.GOOGLETASKS_LIST_TASKS,
            params={"showCompleted": False}
        )
        
        tasks = result.get("tasks", [])
        matching = None
        for task in tasks:
            if task_title.lower() in task.get("title", "").lower():
                matching = task
                break
        
        if not matching:
            return f"Couldn't find a task matching '{task_title}'"
        
        # Mark it complete
        _get_entity().execute(
            action=Action.GOOGLETASKS_PATCH_TASK,
            params={
                "tasklist_id": matching.get("tasklist_id"),
                "task_id": matching.get("id"),
                "status": "completed"
            }
        )
        
        return f"Completed: {matching.get('title')} ✓"
    except Exception as e:
        logger.error(f"Error completing task: {e}", exc_info=True)
        return f"Couldn't complete task. Error: {str(e)}"


def delete_task(task_title: str) -> str:
    """
    Deletes a task by searching for its title.
    
    Args:
        task_title: The title (or part of it) of the task to delete
    
    Returns:
        Confirmation message.
    """
    try:
        # Get all tasks to find the matching one
        result = _get_entity().execute(
            action=Action.GOOGLETASKS_LIST_TASKS,
            params={"showCompleted": False}
        )
        
        tasks = result.get("tasks", [])
        matching = None
        for task in tasks:
            if task_title.lower() in task.get("title", "").lower():
                matching = task
                break
        
        if not matching:
            return f"Couldn't find a task matching '{task_title}'"
        
        # Delete the task
        _get_entity().execute(
            action=Action.GOOGLETASKS_DELETE_TASK,
            params={
                "tasklist_id": matching.get("tasklist_id"),
                "task_id": matching.get("id")
            }
        )
        
        return f"Deleted task: {matching.get('title')} ✓"
    except Exception as e:
        logger.error(f"Error deleting task: {e}", exc_info=True)
        return f"Couldn't delete task. Error: {str(e)}"


def modify_task(task_title: str, new_title: str = None, new_notes: str = None, 
                 due_date: str = None, linked_to_goal: bool = None) -> str:
    """
    Modifies an existing task by searching for its title.
    
    Args:
        task_title: The title (or part of it) of the task to modify
        new_title: New title for the task (optional)
        new_notes: New notes for the task (optional)
        due_date: Due date like "2024-01-15" or "tomorrow" (optional)
        linked_to_goal: If True, marks task as goal-aligned; False removes tag (optional)
    
    Returns:
        Confirmation message.
    """
    try:
        # Get all tasks to find the matching one
        result = _get_entity().execute(
            action=Action.GOOGLETASKS_LIST_TASKS,
            params={"showCompleted": False}
        )
        
        tasks = result.get("tasks", [])
        matching = None
        for task in tasks:
            if task_title.lower() in task.get("title", "").lower():
                matching = task
                break
        
        if not matching:
            return f"Couldn't find a task matching '{task_title}'"
        
        # Build patch params
        patch_params = {
            "tasklist_id": matching.get("tasklist_id"),
            "task_id": matching.get("id")
        }
        
        if new_title:
            patch_params["title"] = new_title
        
        if new_notes is not None or linked_to_goal is not None:
            # Handle notes with goal tagging
            existing_notes = matching.get("notes", "")
            
            if new_notes is not None:
                # Replace the notes content (preserve goal tag if exists)
                if "[GOAL:" in existing_notes and linked_to_goal is not False:
                    patch_params["notes"] = f"[GOAL:yearly] {new_notes}".strip()
                elif linked_to_goal is True:
                    patch_params["notes"] = f"[GOAL:yearly] {new_notes}".strip()
                else:
                    patch_params["notes"] = new_notes
            elif linked_to_goal is True:
                # Add goal tag to existing notes
                if "[GOAL:" not in existing_notes:
                    patch_params["notes"] = f"[GOAL:yearly] {existing_notes}".strip()
            elif linked_to_goal is False:
                # Remove goal tag from notes
                patch_params["notes"] = existing_notes.replace("[GOAL:yearly]", "").strip()
        
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
            changes.append("linked to goal 🎯")
        elif linked_to_goal is False:
            changes.append("unlinked from goal")
        
        return f"Updated '{matching.get('title', task_title)}': {', '.join(changes)}"
    except Exception as e:
        logger.error(f"Error modifying task: {e}", exc_info=True)
        return f"Couldn't modify task. Error: {str(e)}"


# ========================================
# TASK LIST MANAGEMENT
# ========================================

def list_task_lists() -> str:
    """
    Lists all task lists in Google Tasks.
    
    Returns:
        Formatted list of task lists with their IDs.
    """
    try:
        result = _get_entity().execute(
            action=Action.GOOGLETASKS_LIST_TASK_LISTS,
            params={}
        )
        
        data = result.get("data", result)
        task_lists = data.get("items", [])
        if not task_lists:
            return "No task lists found. Create one with create_task_list()."
        
        output = "Your task lists:\n"
        for i, tl in enumerate(task_lists, 1):
            title = tl.get("title", "Untitled")
            list_id = tl.get("id", "")
            output += f"{i}. {title}\n"
        return output
    except Exception as e:
        logger.error(f"Error listing task lists: {e}", exc_info=True)
        return f"Couldn't fetch task lists. Error: {str(e)}"


def create_task_list(title: str) -> str:
    """
    Creates a new task list.
    
    Args:
        title: Name of the new task list
    
    Returns:
        Confirmation message.
    """
    try:
        result = _get_entity().execute(
            action=Action.GOOGLETASKS_CREATE_TASK_LIST,
            params={"title": title}
        )
        
        return f"Created task list: '{title}' ✓"
    except Exception as e:
        logger.error(f"Error creating task list: {e}", exc_info=True)
        return f"Couldn't create task list. Error: {str(e)}"


def delete_task_list(list_name: str) -> str:
    """
    Deletes a task list by name.
    WARNING: This is destructive and deletes all tasks in the list!
    
    Args:
        list_name: Name of the task list to delete
    
    Returns:
        Confirmation message.
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
            return f"Couldn't find a task list matching '{list_name}'"
        
        _get_entity().execute(
            action=Action.GOOGLETASKS_DELETE_TASK_LIST,
            params={"tasklist_id": matching.get("id")}
        )
        
        return f"Deleted task list: '{matching.get('title')}' ✓"
    except Exception as e:
        logger.error(f"Error deleting task list: {e}", exc_info=True)
        return f"Couldn't delete task list. Error: {str(e)}"


def get_task(task_title: str) -> str:
    """
    Gets detailed info about a specific task.
    
    Args:
        task_title: Title (or part of it) of the task to find
    
    Returns:
        Task details.
    """
    try:
        # Use list all tasks to find across all lists
        result = _get_entity().execute(
            action=Action.GOOGLETASKS_LIST_ALL_TASKS,
            params={"showCompleted": True}
        )
        
        data = result.get("data", result)
        tasks = data.get("tasks", data.get("items", []))
        matching = None
        for task in tasks:
            if task_title.lower() in task.get("title", "").lower():
                matching = task
                break
        
        if not matching:
            return f"Couldn't find a task matching '{task_title}'"
        
        output = f"Task: {matching.get('title', 'No title')}\n"
        output += f"Status: {matching.get('status', 'unknown')}\n"
        if matching.get("notes"):
            output += f"Notes: {matching.get('notes')}\n"
        if matching.get("due"):
            output += f"Due: {matching.get('due').split('T')[0]}\n"
        if matching.get("tasklist_title"):
            output += f"List: {matching.get('tasklist_title')}\n"
        
        return output
    except Exception as e:
        logger.error(f"Error getting task: {e}", exc_info=True)
        return f"Couldn't get task. Error: {str(e)}"


def move_task(task_title: str, to_list_name: str) -> str:
    """
    Moves a task to a different task list.
    
    Args:
        task_title: Title of the task to move
        to_list_name: Name of the destination task list
    
    Returns:
        Confirmation message.
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
            return f"Couldn't find destination list '{to_list_name}'"
        
        # Find the task
        tasks_result = _get_entity().execute(
            action=Action.GOOGLETASKS_LIST_ALL_TASKS,
            params={"showCompleted": False}
        )
        
        data = tasks_result.get("data", tasks_result)
        tasks = data.get("tasks", data.get("items", []))
        matching = None
        for task in tasks:
            if task_title.lower() in task.get("title", "").lower():
                matching = task
                break
        
        if not matching:
            return f"Couldn't find task '{task_title}'"
        
        # Move the task
        _get_entity().execute(
            action=Action.GOOGLETASKS_MOVE_TASK,
            params={
                "tasklist_id": matching.get("tasklist_id"),
                "task_id": matching.get("id"),
                "destination_tasklist_id": dest_list.get("id")
            }
        )
        
        return f"Moved '{matching.get('title')}' to '{dest_list.get('title')}' ✓"
    except Exception as e:
        logger.error(f"Error moving task: {e}", exc_info=True)
        return f"Couldn't move task. Error: {str(e)}"


def clear_completed_tasks(list_name: str = None) -> str:
    """
    Clears all completed tasks from a task list.
    
    Args:
        list_name: Name of the list to clear (default: first/primary list)
    
    Returns:
        Confirmation message.
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
            return "No task lists found."
        
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
        
        return f"Cleared completed tasks from '{target_list.get('title')}' ✓"
    except Exception as e:
        logger.error(f"Error clearing tasks: {e}", exc_info=True)
        return f"Couldn't clear tasks. Error: {str(e)}"


def bulk_add_tasks(tasks: list, list_name: str = None) -> str:
    """
    Adds multiple tasks at once.
    
    Args:
        tasks: List of dicts with 'title' and optionally 'notes', 'due'
        list_name: Name of the list (default: first/primary list)
    
    Returns:
        Confirmation message.
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
            return "No task lists found. Create one first with create_task_list()."
        
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
        
        return f"Added {len(tasks)} tasks to '{target_list.get('title')}' ✓"
    except Exception as e:
        logger.error(f"Error bulk adding tasks: {e}", exc_info=True)
        return f"Couldn't bulk add tasks. Error: {str(e)}"


# ========================================
# GOAL TRACKING
# ========================================

def get_goal_progress() -> str:
    """
    Retrieves all goal-linked tasks with full details for intelligent LLM analysis.
    The agent should use this data to assess REAL progress - not just count tasks.
    
    Returns:
        Detailed list of goal-linked tasks for the agent to analyze.
    """
    try:
        # Get all tasks including completed
        result = _get_entity().execute(
            action=Action.GOOGLETASKS_LIST_TASKS,
            params={"showCompleted": True}
        )
        
        tasks = result.get("tasks", [])
        goal_tasks = [t for t in tasks if "[GOAL:" in t.get("notes", "")]
        
        if not goal_tasks:
            return "No tasks linked to your goals yet. When adding tasks, I can link them to your yearly goal."
        
        # Separate completed and pending
        completed = [t for t in goal_tasks if t.get("status") == "completed"]
        pending = [t for t in goal_tasks if t.get("status") != "completed"]
        
        output = "GOAL-LINKED TASKS FOR ANALYSIS:\n\n"
        
        if completed:
            output += "✅ COMPLETED:\n"
            for task in completed:
                title = task.get("title", "No title")
                notes = task.get("notes", "").replace("[GOAL:yearly]", "").strip()
                output += f"  • {title}"
                if notes:
                    output += f" - {notes}"
                output += "\n"
        
        if pending:
            output += "\n⏳ IN PROGRESS:\n"
            for task in pending:
                title = task.get("title", "No title")
                notes = task.get("notes", "").replace("[GOAL:yearly]", "").strip()
                due = task.get("due", "")
                output += f"  • {title}"
                if notes:
                    output += f" - {notes}"
                if due:
                    output += f" (due: {due.split('T')[0]})"
                output += "\n"
        
        output += f"\nRAW COUNTS: {len(completed)} completed, {len(pending)} pending"
        output += "\n\n[Analyze the SUBSTANCE of these tasks to assess real goal progress]"
        
        return output
    except Exception as e:
        logger.error(f"Error getting goal progress: {e}", exc_info=True)
        return f"Couldn't calculate progress. Error: {str(e)}"
