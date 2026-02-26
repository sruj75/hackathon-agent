"""Live smoke tests for unified task management.

These tests can create real Google Tasks/Calendar data for the authenticated user.
They are excluded from default runs by pytest marker selection (`-m "not live"`).
"""

from __future__ import annotations

import os
from datetime import datetime

import pytest

from context import current_user_id, current_user_timezone
from voice_agent.composio_tools import task_management


pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.live]


def _assert_success(operation: str, result: dict) -> None:
    assert isinstance(result, dict), f"{operation} did not return a result dictionary"
    assert result.get("success") is True, (
        f"{operation} failed: error={result.get('error')} message={result.get('message')}"
    )


def _contains_task(result: dict, task_title: str) -> bool:
    data = result.get("data")
    if isinstance(data, list):
        return any(isinstance(item, dict) and item.get("title") == task_title for item in data)
    if isinstance(data, dict):
        tasks = data.get("tasks")
        if isinstance(tasks, list):
            return any(isinstance(item, dict) and item.get("title") == task_title for item in tasks)
    return False


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_TASK_MANAGEMENT_TESTS") != "1",
    reason="Set RUN_LIVE_TASK_MANAGEMENT_TESTS=1 to run live task management smoke tests.",
)
def test_full_workflow_live_smoke():
    live_user_id = os.getenv("LIVE_TEST_USER_ID")
    live_timezone = os.getenv("LIVE_TEST_TIMEZONE")
    assert live_user_id, "Set LIVE_TEST_USER_ID to the authenticated user id for live tests."
    assert live_timezone, "Set LIVE_TEST_TIMEZONE to a valid IANA timezone for live tests."

    user_token = current_user_id.set(live_user_id)
    tz_token = current_user_timezone.set(live_timezone)
    test_task_title = f"Codex Live Task {datetime.now().strftime('%Y%m%d%H%M%S')}"
    task_created = False

    try:
        add_result = task_management(
            "add_task",
            {
                "title": test_task_title,
                "notes": "Live smoke test task",
                "linked_to_goal": False,
            },
        )
        _assert_success("add_task", add_result)
        task_created = True

        pending_result = task_management("get_tasks", {"status": "pending"})
        _assert_success("get_tasks[pending]", pending_result)
        assert _contains_task(
            pending_result, test_task_title
        ), "New task not found in pending tasks"

        now_local = datetime.now()
        start_time = f"{now_local.hour:02d}:{now_local.minute:02d}"
        timeblock_result = task_management(
            "timeblock_task",
            {
                "task_title": test_task_title,
                "start_time": start_time,
                "duration_minutes": 30,
            },
        )
        _assert_success("timeblock_task", timeblock_result)

        scheduled_result = task_management("get_tasks", {"status": "scheduled"})
        _assert_success("get_tasks[scheduled]", scheduled_result)
        assert _contains_task(
            scheduled_result, test_task_title
        ), "Timeblocked task not found in scheduled tasks"

        complete_result = task_management("complete_task", {"task_title": test_task_title})
        _assert_success("complete_task", complete_result)

    finally:
        if task_created:
            delete_result = task_management("delete_task", {"task_title": test_task_title})
            assert isinstance(delete_result, dict), "delete_task did not return a result dictionary"
            assert delete_result.get("success") is True, (
                "Cleanup failed; manual cleanup may be required: "
                f"error={delete_result.get('error')} message={delete_result.get('message')}"
            )
        current_user_timezone.reset(tz_token)
        current_user_id.reset(user_token)
