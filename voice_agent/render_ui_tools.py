"""
Generative UI Tool - Explicit UI Rendering

This tool allows the agent to explicitly show UI components to the user.

The agent has full control over when and what UI to display.
There is no auto-rendering - the agent must call this tool to show UI.
"""
import logging
import asyncio
from typing import Optional

logger = logging.getLogger(__name__)

# Global queue for UI events - main.py will read from this
_ui_event_queue: Optional[asyncio.Queue] = None

def set_ui_event_queue(queue: asyncio.Queue):
    """Set the queue that UI events will be sent to."""
    global _ui_event_queue
    _ui_event_queue = queue
    logger.info("[GENERATIVE_UI] UI event queue registered")

def get_ui_event_queue() -> Optional[asyncio.Queue]:
    """Get the current UI event queue."""
    return _ui_event_queue


def generative_ui(component: str, props = None):
    """
    Render a UI component to the user.
    
    Components:
    - day_view: Unified view (tasks + events together)
    - stop_reflect_act: Emotional regulation wizard (STOP-REFLECT-ACT)
    
    Args:
        component: Component type (day_view, stop_reflect_act)
        props: Component properties with data to display
        
    Returns:
        {
            "success": bool,
            "message": str,
            "ui_payload": {
                "type": component,
                "props": props
            }
        }
    """
    if props is None:
        props = {}
    
    valid_components = [
        "day_view", "stop_reflect_act"
    ]
    
    if component not in valid_components:
        logger.warning(f"[GENERATIVE_UI] !!! Unknown component '{component}' - valid: {valid_components}")
        return {
            "success": False,
            "message": f"Unknown component: {component}. Valid: {', '.join(valid_components)}",
            "ui_payload": None
        }
    
    # Safety net for day_view: fetch data in-tool only when payload is missing/empty.
    # Agent can call day_view with no props, and safety net will populate data.
    if component == "day_view":
        events = props.get("events", []) if isinstance(props, dict) else []
        tasks = props.get("tasks", []) if isinstance(props, dict) else []
        needs_events = not isinstance(events, list) or len(events) == 0
        needs_tasks = not isinstance(tasks, list) or len(tasks) == 0

        if needs_events or needs_tasks:
            logger.info(
                f"[GENERATIVE_UI] day_view safety net triggered: events_missing={needs_events}, tasks_missing={needs_tasks}"
            )
            from .composio_tools import list_todays_events, list_all_tasks

            if needs_events:
                logger.info("[GENERATIVE_UI] Auto-fetching calendar events...")
                events_result = list_todays_events()
                if events_result.get("success"):
                    events = events_result.get("data", {}).get("events", [])
                    logger.info(f"[GENERATIVE_UI] Fetched {len(events)} events")
                else:
                    logger.warning(
                        f"[GENERATIVE_UI] Failed to fetch events: {events_result.get('message')}"
                    )
                    events = []

            if needs_tasks:
                logger.info("[GENERATIVE_UI] Auto-fetching tasks...")
                tasks_result = list_all_tasks()
                if tasks_result.get("success"):
                    tasks = tasks_result.get("data", {}).get("tasks", [])
                    logger.info(f"[GENERATIVE_UI] Fetched {len(tasks)} tasks")
                else:
                    logger.warning(
                        f"[GENERATIVE_UI] Failed to fetch tasks: {tasks_result.get('message')}"
                    )
                    tasks = []

            props = {"events": events, "tasks": tasks}
            logger.info(
                f"[GENERATIVE_UI] Safety net complete - rendering with {len(events)} events, {len(tasks)} tasks"
            )
        else:
            logger.info(
                f"[GENERATIVE_UI] day_view using provided payload - events={len(events)}, tasks={len(tasks)}"
            )
    
    # Log props summary (avoid logging huge data)
    props_keys = list(props.keys()) if props else []
    logger.info(f"[GENERATIVE_UI] >>> component={component}, props_keys={props_keys}")
    
    # Push UI event to queue for main.py to send via WebSocket
    if _ui_event_queue is not None:
        ui_event = {
            "type": "generative_ui",
            "component": component,
            "props": props
        }
        try:
            _ui_event_queue.put_nowait(ui_event)
            logger.info(f"[GENERATIVE_UI] <<< Queued UI event: {component}")
        except Exception as e:
            logger.error(f"[GENERATIVE_UI] Failed to queue UI event: {e}")
    else:
        logger.warning("[GENERATIVE_UI] No UI event queue registered - UI won't render!")
    
    return {
        "success": True,
        "message": f"Rendering {component}",
        "ui_payload": {
            "type": component,
            "props": props
        }
    }
