"""
Intentive realtime conversation agent.
"""
from google.adk.agents import Agent
import logging

from .composio_tools import task_management
from .render_ui_tools import generative_ui

logger = logging.getLogger(__name__)

AGENT_NAME = "intentive_planner"
CONVERSATION_MODEL = "gemini-2.5-flash-native-audio-preview-09-2025"

CONVERSATION_INSTRUCTION = """You are Intentive, a realtime voice planning assistant.
The user is present in the app and can hear you and see the UI.

Core behavior:
- Keep replies short and actionable (1-2 spoken sentences when possible).
- Use tool data before making claims.
- After task/calendar operations, call generative_ui with display_mode.
- Do not schedule reminders manually and do not mention background modes.

Available tool:
- task_management(operation, params): unified Google Tasks + Calendar operations.
- generative_ui(component, props): render day_view with contextual props.

display_mode values:
- planning: overview and planning moments
- now_focus: active work block support
- transition: between events
- recap: end-of-day review
"""

CONVERSATION_TOOLS = [
    task_management,
    generative_ui,
]

conversation_agent = Agent(
    name=AGENT_NAME,
    model=CONVERSATION_MODEL,
    description="Realtime voice assistant with calendar/task execution and UI feedback",
    instruction=CONVERSATION_INSTRUCTION,
    tools=CONVERSATION_TOOLS,
)

# Backward compatibility alias
root_agent = conversation_agent

logger.info(
    "Intentive Planner initialized with realtime model %s (%s tools)",
    CONVERSATION_MODEL,
    len(CONVERSATION_TOOLS),
)
