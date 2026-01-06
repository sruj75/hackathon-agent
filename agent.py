from dotenv import load_dotenv  # For reading .env files
from livekit import agents  # Main LiveKit framework
from livekit.agents import AgentSession, Agent  # Specific classes we need
from livekit.plugins import google  # Google Gemini Live API plugin
import os  # Built-in module for environment variables
import logging  # For debugging and monitoring

# Configure logging - this helps us see what's happening
logging.basicConfig(
    level=logging.INFO,  # Show INFO level and above messages
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'  # Timestamp, logger name, level, message
)
logger = logging.getLogger(__name__)  # Create logger for this file

# Load environment variables from .env file
load_dotenv()  # Now we can use os.getenv("GOOGLE_API_KEY")


# ============================================================================
# AGENT CLASS - Define our AI accountability coach
# ============================================================================

class CoachAgent(Agent):
    """AI Accountability Coach Agent"""
    
    def __init__(self) -> None:  # Constructor method (runs when object is created)
        super().__init__(  # Call parent class constructor
            instructions="""You are an AI accountability coach helping users plan their day.
            You have a warm, encouraging personality and help users stay focused on their goals.
            Keep responses conversational and concise since they will be spoken aloud.
            When asked to test tool calling, use the hello_world tool."""
        )
        logger.info("CoachAgent initialized with Gemini Live API")

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


# ============================================================================
# ENTRY POINT - Main function that handles user connections
# ============================================================================

async def entrypoint(ctx: agents.JobContext):
    """
    This function runs when a user joins the room.
    It sets up the voice agent with Gemini Live API.
    
    Args:
        ctx: JobContext provided by LiveKit containing room information
    """
    logger.info(f"User joined room: {ctx.room.name} - Setting up Gemini voice agent")
    
    try:
        # Connect to the LiveKit room first (before creating session)
        await ctx.connect()
        logger.info("Successfully connected to LiveKit room")
        
        # Create agent session with Gemini Live API
        logger.info("Creating AgentSession with Gemini Live API (gemini-2.5-flash-native-audio-preview)")
        session = AgentSession(
            llm=google.realtime.RealtimeModel(
                model="gemini-live-2.5-flash-native-audio",
                voice="Puck",  # Gemini voice option
                temperature=0.8,  # Creativity level (0.0-1.0)
            ),
        )
        logger.info("AgentSession created successfully with Gemini Live API")
        
        # Start the session with our CoachAgent
        logger.info("Starting AgentSession with CoachAgent")
        await session.start(
            room=ctx.room,  # The room the user joined
            agent=CoachAgent(),  # Create an instance of our CoachAgent class
        )
        logger.info("AgentSession started successfully")
        
        # Send initial greeting to the user
        logger.info("Generating initial greeting for user")
        await session.generate_reply(
            instructions="""Greet the user warmly. Let them know you're their AI accountability 
            coach and you're ready to help them plan their day. Keep it brief and friendly."""
        )
        logger.info("Initial greeting sent to user")
        
    except Exception as e:
        logger.error(f"Error in entrypoint: {str(e)}", exc_info=True)
        raise  # Re-raise the exception so LiveKit knows something went wrong


# ============================================================================
# MAIN - Start the LiveKit agent worker
# ============================================================================

if __name__ == "__main__":
    logger.info("Starting Intentive Voice Agent with Gemini Live API")
    # Start the LiveKit agent worker
    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,  # Pass our entrypoint function
            name=os.getenv("AGENT_NAME", "intentive-coach"),
        )
    )
