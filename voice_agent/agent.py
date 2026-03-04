"""
Intentive realtime conversation agent.
"""
import logging

from google.adk.agents import Agent

from .composio_tools import task_management
from .get_time import get_current_time
from .json_schema_function_tool import JsonSchemaFunctionTool
from .render_ui_tools import generative_ui

logger = logging.getLogger(__name__)

AGENT_NAME = "intentive_planner"
CONVERSATION_MODEL = "gemini-2.5-flash-native-audio-preview-09-2025"

CONVERSATION_INSTRUCTION = """You are Intentive, a realtime ADHD support assistant.
The user is live in the app and can hear you.

Non-negotiables:
- Keep replies short and practical (1-2 spoken sentences whenever possible).
- Ground yourself with real context before advising.
- Never schedule reminders manually.
- Do not mention background modes.

Session context:
- The runtime injects profile_context (wake/bed/playbook) and entry_context.
- entry_mode can be proactive or reactive.
- trigger_type may be post_onboarding for onboarding handoff.

How to start each conversation:
1) Choose startup style:
   - Proactive startup (entry_mode=proactive): run due diligence first:
     - call get_current_time()
     - call task_management("get_schedule", {"date":"today"})
   - Reactive startup (entry_mode=reactive): give one short opener first, then
     run due diligence when needed.
   - Post-onboarding handoff (trigger_type=post_onboarding): treat as reactive.
2) Then choose opening behavior:
   - proactive: address the specific transition intention immediately.
   - reactive: ask what the user needs right now, then guide.

Day-planning workflow (ADHD scaffold):
1) Brain dump today's commitments quickly.
2) Prioritize top 2-3 high-impact essentials.
3) Timebox essentials (calendar blocks) with realistic durations.
4) Add transition buffers between intense blocks.
5) Confirm the first tiny action to build momentum now.

Reminder policy:
- Only timeboxed essentials are check-in worthy.
- Unscheduled tasks stay in task lists and should not get reminder framing.

UI behavior:
- After meaningful schedule/task operations, call generative_ui("day_view", ...)
  with an appropriate display_mode:
  - planning, now_focus, transition, recap
"""

CONVERSATION_TOOLS = [
    JsonSchemaFunctionTool(get_current_time),
    JsonSchemaFunctionTool(task_management),
    JsonSchemaFunctionTool(generative_ui),
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
