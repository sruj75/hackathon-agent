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
    - todo_list: Task list only
    - calendar_view: Calendar only
    
    Args:
        component: Component type (day_view, todo_list, calendar_view)
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
        "day_view", "todo_list", "calendar_view"
    ]
    
    if component not in valid_components:
        logger.warning(f"[GENERATIVE_UI] !!! Unknown component '{component}' - valid: {valid_components}")
        return {
            "success": False,
            "message": f"Unknown component: {component}. Valid: {', '.join(valid_components)}",
            "ui_payload": None
        }
    
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
