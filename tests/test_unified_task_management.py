"""
Test script for unified task management system.
Tests the full workflow: add → timeblock → complete → delete

Run this from the agent directory:
    python3 tests/test_unified_task_management.py
"""
import sys
import os
from datetime import datetime

# Add the parent directory (agent/) to the path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from voice_agent.composio_tools import task_management

def print_result(operation, result):
    """Pretty print a result."""
    print(f"\n{'='*60}")
    print(f"Operation: {operation}")
    print(f"Success: {result.get('success')}")
    print(f"Message: {result.get('message')}")
    if result.get('data'):
        print(f"Data: {result.get('data')}")
    print(f"{'='*60}")

def test_full_workflow():
    """Test the complete unified task management workflow."""
    print("\n🧪 TESTING UNIFIED TASK MANAGEMENT SYSTEM")
    print("=" * 60)
    
    test_task_title = f"Test Task {datetime.now().strftime('%H:%M:%S')}"
    
    # 1. Add unscheduled task
    print("\n1️⃣  Adding unscheduled task...")
    result = task_management("add_task", {
        "title": test_task_title,
        "notes": "This is a test task for the unified system",
        "linked_to_goal": False
    })
    print_result("add_task", result)
    
    if not result.get("success"):
        print("❌ Failed to add task. Stopping test.")
        return
    
    # 2. Get pending tasks (should include our new task)
    print("\n2️⃣  Getting pending tasks...")
    result = task_management("get_tasks", {"status": "pending"})
    print_result("get_tasks (pending)", result)
    
    # 3. Timeblock the task
    print("\n3️⃣  Timeblocking task to calendar...")
    current_time = datetime.now()
    start_time = f"{current_time.hour:02d}:{current_time.minute:02d}"
    
    result = task_management("timeblock_task", {
        "task_title": test_task_title,
        "start_time": start_time,
        "duration_minutes": 30
    })
    print_result("timeblock_task", result)
    
    if not result.get("success"):
        print("❌ Failed to timeblock task. Continuing to test other operations...")
    
    # 4. Get scheduled tasks (should include our task now)
    print("\n4️⃣  Getting scheduled tasks...")
    result = task_management("get_tasks", {"status": "scheduled"})
    print_result("get_tasks (scheduled)", result)
    
    # 5. Get today's schedule
    print("\n5️⃣  Getting today's schedule...")
    result = task_management("get_schedule", {"date": "today"})
    print_result("get_schedule", result)
    
    # 6. Check for conflicts (should find our newly created event)
    print("\n6️⃣  Checking for conflicts...")
    end_time_hour = current_time.hour
    end_time_minute = current_time.minute + 30
    if end_time_minute >= 60:
        end_time_hour += 1
        end_time_minute -= 60
    end_time = f"{end_time_hour:02d}:{end_time_minute:02d}"
    
    result = task_management("check_conflicts", {
        "start_time": start_time,
        "end_time": end_time
    })
    print_result("check_conflicts", result)
    
    # 7. Complete the task
    print("\n7️⃣  Completing task...")
    result = task_management("complete_task", {
        "task_title": test_task_title
    })
    print_result("complete_task", result)
    
    if not result.get("success"):
        print("❌ Failed to complete task. Continuing to cleanup...")
    
    # 8. Get completed tasks
    print("\n8️⃣  Getting completed tasks...")
    result = task_management("get_tasks", {"status": "completed"})
    print_result("get_tasks (completed)", result)
    
    # 9. Delete the task (and linked event)
    print("\n9️⃣  Deleting task and linked event...")
    result = task_management("delete_task", {
        "task_title": test_task_title
    })
    print_result("delete_task", result)
    
    if not result.get("success"):
        print("❌ Failed to delete task.")
    
    print("\n" + "=" * 60)
    print("✅ WORKFLOW TEST COMPLETE")
    print("=" * 60)
    print("\nNext steps:")
    print("1. Check your Google Tasks - verify test task is deleted")
    print("2. Check your Google Calendar - verify test event is deleted")
    print("3. Review the logs above to ensure all operations succeeded")

if __name__ == "__main__":
    try:
        test_full_workflow()
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
