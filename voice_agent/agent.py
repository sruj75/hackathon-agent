"""
AI Accountability Coach Agent - Google ADK Implementation

This agent uses Gemini Live API for real-time voice conversations.
"""
from google.adk.agents import Agent
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================================
# TOOLS - Define functions the agent can call
# ============================================================================

def get_password() -> str:
    """
    Retrieves the secret password.
    
    Returns:
        The secret password string.
    """
    logger.info("get_password tool called")
    return "I SEE DEAD PEOPLE"


# ============================================================================
# AGENT DEFINITION
# ============================================================================

root_agent = Agent(
    name="voice_agent",
    model="gemini-2.5-flash-native-audio-preview-09-2025",  # Gemini Live API model
    description="AI accountability coach helping users plan their day.",
    instruction="""You are an AI accountability coach helping users plan their day.
    You have a warm, encouraging personality and help users stay focused on their goals.
    Keep responses conversational and concise since they will be spoken aloud.
    When asked for the password, use the get_password tool.""",
    tools=[get_password],
)

logger.info("CoachAgent initialized with Gemini Live API via ADK")
