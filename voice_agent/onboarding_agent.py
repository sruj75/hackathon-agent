"""
Intentive onboarding voice agent.
"""
from google.adk.agents import Agent
import logging

from .onboarding_tools import complete_onboarding, get_onboarding_context

logger = logging.getLogger(__name__)

ONBOARDING_AGENT_NAME = "intentive_onboarding"
ONBOARDING_MODEL = "gemini-2.5-flash-native-audio-preview-09-2025"

ONBOARDING_INSTRUCTION = """You are Intentive's onboarding voice agent.
Your only goal is to onboard the user and capture setup context.

Behavior rules:
- Keep tone warm, short, and practical.
- Ask one clear question at a time.
- You are onboarding, not planning the user's day in detail yet.
- Do not call calendar or task tools.
- Use get_onboarding_context at the start to resume if onboarding is incomplete.
- Collect these required values before completion:
  - wake_time in HH:MM 24-hour format
  - bedtime in HH:MM 24-hour format
- Build a structured playbook JSON with:
  - schema_version
  - summary
  - struggles (string list)
  - goals (string list)
  - communication_style
- When required values are collected, serialize the playbook object to a JSON string
  and call complete_onboarding(wake_time, bedtime, playbook_json).
- After successful completion, tell the user onboarding is done and they can close the app.
"""

ONBOARDING_TOOLS = [
    get_onboarding_context,
    complete_onboarding,
]

onboarding_agent = Agent(
    name=ONBOARDING_AGENT_NAME,
    model=ONBOARDING_MODEL,
    description="Onboarding intake agent that captures user setup context",
    instruction=ONBOARDING_INSTRUCTION,
    tools=ONBOARDING_TOOLS,
)

logger.info(
    "Intentive Onboarding initialized with model %s (%s tools)",
    ONBOARDING_MODEL,
    len(ONBOARDING_TOOLS),
)
