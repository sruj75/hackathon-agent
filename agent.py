"""
Intentive Voice Agent - AI Accountability Coach
Uses LiveKit + Gemini Live API for real-time voice interaction
"""

from livekit import agents
from livekit.agents import AgentSession, Agent
from livekit.plugins import google
from dotenv import load_dotenv
import logging
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()


class CoachAgent(Agent):
    """AI Accountability Coach Agent"""
    
    def __init__(self):
        super().__init__(
            instructions="""You are an AI accountability coach helping users plan their day.
            You have a warm, encouraging personality and help users stay focused on their goals.
            Keep responses conversational and concise since they will be spoken aloud.
            When asked to test tool calling, use the hello_world tool."""
        )
        logger.info("CoachAgent initialized")

    @agents.tool()
    async def hello_world(self, name: str) -> str:
        """
        A test tool that greets the user. Use this to verify tool calling works.
        
        Args:
            name: The name to greet
            
        Returns:
            A greeting message
        """
        logger.info(f"hello_world tool called with name: {name}")
        return f"Hello, {name}! 🎉 Tool calling is working perfectly!"


async def entrypoint(ctx: agents.JobContext):
    """
    Main entry point for the agent.
    Called when a user joins the room.
    """
    logger.info(f"User joined room: {ctx.room.name}")
    
    try:
        # Connect to the room
        await ctx.connect()
        logger.info("Connected to LiveKit room")
        
        # Create agent session with Gemini Live API
        session = AgentSession(
            llm=google.realtime.RealtimeModel(
                model="gemini-2.5-flash-native-audio-preview-12-2025",
                voice="Puck",
                temperature=0.8,
            ),
        )
        logger.info("AgentSession created with Gemini Live API")
        
        # Start the session
        await session.start(
            room=ctx.room,
            agent=CoachAgent(),
        )
        logger.info("AgentSession started")
        
        # Send initial greeting
        await session.generate_reply(
            instructions="""Greet the user warmly. Let them know you're their AI accountability 
            coach and you're ready to help them plan their day. Keep it brief and friendly."""
        )
        logger.info("Initial greeting sent")
        
    except Exception as e:
        logger.error(f"Error in entrypoint: {str(e)}", exc_info=True)
        raise


if __name__ == "__main__":
    logger.info("Starting Intentive Voice Agent")
    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
            name=os.getenv("AGENT_NAME", "intentive-coach"),
        )
    )
