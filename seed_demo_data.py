"""
Demo Data Seeding Script
Cleans up existing Google Calendar events and Tasks, then adds fresh demo data.
"""
import sys
import os
from datetime import datetime, timedelta
import pytz

# Add parent directory to path to import composio_tools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from voice_agent.composio_tools import (
    list_todays_events,
    delete_event,
    create_calendar_event,
    list_all_tasks,
    delete_task,
    add_task,
    _get_entity,
    _get_user_timezone
)


def cleanup_calendar():
    """Delete all existing calendar events for today."""
    print("\n🗑️  Cleaning up existing calendar events...")
    
    # List all events
    result = list_todays_events()
    if not result.get("success"):
        print(f"   ❌ Error listing events: {result.get('message')}")
        return
    
    events = result.get("data", {}).get("events", [])
    if not events:
        print("   ✓ No existing events to clean up")
        return
    
    print(f"   Found {len(events)} events to delete")
    
    # Delete each event
    for event in events:
        title = event.get("title", "Unknown")
        event_id = event.get("id")
        print(f"   Deleting: {title}")
        
        # Use the event ID directly instead of searching by title
        try:
            from composio import Action
            _get_entity().execute(
                action=Action.GOOGLECALENDAR_DELETE_EVENT,
                params={
                    "calendar_id": "primary",
                    "event_id": event_id
                }
            )
            print(f"   ✓ Deleted: {title}")
        except Exception as e:
            print(f"   ❌ Failed to delete {title}: {e}")


def cleanup_tasks():
    """Delete all existing tasks."""
    print("\n🗑️  Cleaning up existing tasks...")
    
    # List all tasks
    result = list_all_tasks()
    if not result.get("success"):
        print(f"   ❌ Error listing tasks: {result.get('message')}")
        return
    
    tasks = result.get("data", {}).get("tasks", [])
    if not tasks:
        print("   ✓ No existing tasks to clean up")
        return
    
    print(f"   Found {len(tasks)} tasks to delete")
    
    # Delete each task
    for task in tasks:
        title = task.get("title", "Unknown")
        task_id = task.get("id")
        tasklist_id = task.get("tasklist_id")
        print(f"   Deleting: {title}")
        
        # Use the task ID directly
        try:
            from composio import Action
            _get_entity().execute(
                action=Action.GOOGLETASKS_DELETE_TASK,
                params={
                    "tasklist_id": tasklist_id,
                    "task_id": task_id
                }
            )
            print(f"   ✓ Deleted: {title}")
        except Exception as e:
            print(f"   ❌ Failed to delete {title}: {e}")


def seed_calendar_events():
    """Add the 3 demo calendar events in IST timezone."""
    print("\n📅 Adding demo calendar events...")
    
    # Use Indian Standard Time explicitly
    ist_tz = pytz.timezone('Asia/Kolkata')
    today_ist = datetime.now(ist_tz)
    today_str = today_ist.strftime("%Y-%m-%d")
    
    # Check what timezone is detected
    detected_tz = _get_user_timezone()
    print(f"   Detected Google Calendar timezone: {detected_tz}")
    print(f"   Using IST timezone for events")
    
    events = [
        {
            "title": "VP Meeting",
            "start_time": "11:00",
            "duration_minutes": 30,
            "description": "Quarterly review with VP"
        },
        {
            "title": "Product Launch Sync",
            "start_time": "14:00",
            "duration_minutes": 45,
            "description": "Discuss product launch timeline with dev team"
        },
        {
            "title": "Review Session",
            "start_time": "15:00",
            "duration_minutes": 30,
            "description": "Team review session"
        }
    ]
    
    for event_data in events:
        # Create full IST datetime string
        start_time_ist = f"{today_str}T{event_data['start_time']}:00"
        start_dt = ist_tz.localize(datetime.fromisoformat(start_time_ist))
        
        print(f"   Adding: {event_data['title']} at {event_data['start_time']} IST")
        
        # Use Composio directly to ensure correct timezone handling
        from composio import Action
        duration_hours = event_data["duration_minutes"] // 60
        duration_mins = event_data["duration_minutes"] % 60
        
        try:
            result = _get_entity().execute(
                action=Action.GOOGLECALENDAR_CREATE_EVENT,
                params={
                    "summary": event_data["title"],
                    "start_datetime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                    "timezone": "Asia/Kolkata",
                    "event_duration_hour": duration_hours,
                    "event_duration_minutes": duration_mins,
                    "description": event_data["description"],
                    "create_meeting_room": False
                }
            )
            print(f"   ✓ Created '{event_data['title']}' at {event_data['start_time']} IST")
        except Exception as e:
            print(f"   ❌ Failed: {e}")


def seed_tasks():
    """Add the 2 demo tasks."""
    print("\n✅ Adding demo tasks...")
    
    tasks = [
        {
            "title": "Refill prescription",
            "notes": "Pick up medication from pharmacy"
        },
        {
            "title": "Groceries shopping",
            "notes": "Weekly grocery run"
        }
    ]
    
    for task_data in tasks:
        print(f"   Adding: {task_data['title']}")
        result = add_task(
            title=task_data["title"],
            notes=task_data["notes"]
        )
        
        if result.get("success"):
            print(f"   ✓ {result.get('message')}")
        else:
            print(f"   ❌ Failed: {result.get('message')}")


def main():
    """Main execution flow."""
    print("=" * 60)
    print("INTENTIVE DEMO DATA SEEDING")
    print("=" * 60)
    
    try:
        # Initialize Composio entity
        print("\n🔧 Initializing Composio...")
        _get_entity()
        print("   ✓ Connected to Google Calendar & Tasks")
        
        # Step 1: Clean up existing data
        cleanup_calendar()
        cleanup_tasks()
        
        # Step 2: Add fresh demo data
        seed_calendar_events()
        seed_tasks()
        
        print("\n" + "=" * 60)
        print("✨ DEMO DATA SEEDING COMPLETE!")
        print("=" * 60)
        print("\nYour Google Calendar now has:")
        print("  • VP Meeting — 11:00-11:30 AM")
        print("  • Product Launch Sync — 2:00-2:45 PM")
        print("  • Review Session — 3:00-3:30 PM")
        print("\nYour Google Tasks now has:")
        print("  • Refill prescription")
        print("  • Groceries shopping")
        print("\n🎬 Ready to record your demo!")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
