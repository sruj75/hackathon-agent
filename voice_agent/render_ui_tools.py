"""
Generative UI Tool - HYBRID APPROACH

This tool gives the agent EXPLICIT CONTROL over UI rendering.

HYBRID BEHAVIOR:
- calendar_tool and tasks_tool auto-return ui_payload (render_mode: "auto")
- Use THIS tool to OVERRIDE auto-renders (render_mode: "explicit")

PRIORITY SYSTEM (when multiple tool calls happen):
- Explicit UI (from this tool) > Auto UI (from calendar/tasks)
- Within auto: day_view (3) > todo_list (2) > calendar_view (1)
- Frontend shows only the HIGHEST priority UI at end of agent's turn

USE THIS TOOL FOR:
1. OVERRIDING auto-renders when you want specific presentation
2. Forcing unified day_view when you called both calendar + tasks tools
3. Forcing specific view (tasks-only or calendar-only) despite what tools returned
"""
import logging

logger = logging.getLogger(__name__)


def generative_ui(component: str, props: dict = None, force_render: bool = False) -> dict:
    """
    Explicit UI control for agent - OVERRIDE auto-renders.
    
    COMPONENTS (must match frontend):
    - day_view: Unified view (tasks + events together)
    - todo_list: Task list only
    - calendar_view: Calendar only
    
    USE THIS TO:
    - Override auto-renders from calendar_tool/tasks_tool
    - Force specific UI when multiple tools called
    - Control presentation explicitly
    
    Args:
        component: Component type (day_view, todo_list, calendar_view)
        props: Component properties (leave empty {} - frontend fetches latest data)
        force_render: If True, immediately renders without waiting for turn end
        
    Returns:
        {
            "success": bool,
            "message": str,
            "ui_payload": {
                "type": component,
                "props": props,
                "render_mode": "explicit",
                "priority": 100,  # Always wins over auto-renders
                "force_render": bool
            }
        }
    """
    if props is None:
        props = {}
    
    valid_components = [
        "day_view", "todo_list", "calendar_view"
    ]
    
    if component not in valid_components:
        logger.warning(f"Generative UI: Unknown component '{component}'")
        return {
            "success": False,
            "message": f"Unknown component: {component}. Valid: {', '.join(valid_components)}",
            "ui_payload": None
        }
    
    logger.info(f"Generative UI (EXPLICIT): {component}, force={force_render}")
    
    return {
        "success": True,
        "message": f"Rendering {component} (explicit)",
        "ui_payload": {
            "type": component,
            "props": props,
            "render_mode": "explicit",
            "priority": 100,  # Explicit always wins
            "force_render": force_render
        }
    }
