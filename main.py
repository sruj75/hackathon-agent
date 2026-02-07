"""
FastAPI Backend for Intentive Voice Agent

This is the production FastAPI server that provides:
- WebSocket endpoint for real-time audio streaming with ADK
- Health check endpoint
"""
import asyncio
import json
import logging
import os
import warnings
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# Load environment variables BEFORE importing agent
load_dotenv(Path(__file__).parent / ".env")

# Add the current directory (agent/) to sys.path so that absolute imports work correctly
import sys
sys.path.insert(0, str(Path(__file__).parent))

# Import agent after loading env
from voice_agent.agent import conversation_agent as agent  # noqa: E402
from voice_agent.render_ui_tools import set_ui_event_queue, get_ui_event_queue  # noqa: E402

from google.adk.runners import Runner
from session_manager import ADKSessionManager
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.genai import types
from context import current_session_id, current_user_id

# New imports for Cron Endpoints
from fastapi import Header, HTTPException, Depends
from repos import event_repo, user_repo
from datetime import datetime, timedelta, time
import os
from agent_runtime import AgentRuntime
import cron_service

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Suppress Pydantic serialization warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

# Application name constant
APP_NAME = "intentive-coach"

# ========================================
# FastAPI App Setup
# ========================================

app = FastAPI(
    title="Intentive Voice Agent API",
    description="Real-time voice AI coaching with Gemini Live API",
    version="1.0.0",
)

# CORS middleware for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session and Runner setup
# Session and Runner setup
session_manager = ADKSessionManager()
runner = Runner(app_name=APP_NAME, agent=agent, session_service=session_manager.service)


# ========================================
# Endpoints
# ========================================

@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "app": APP_NAME, "agent": agent.name}


@app.get("/health")
async def health():
    """Health check for deployment monitoring."""
    return {"status": "healthy"}


# ========================================
# Cron Endpoints
# ========================================

@app.post("/api/execute-event/{event_id}")
async def execute_event(
    event_id: str
):
    """
    Execute a specific scheduled event.
    Called by cron-jobs.org at the scheduled time.
    
    Security: Event IDs are UUIDs (unguessable) and execution is idempotent.
    """
    event = await event_repo.get_by_id(event_id)
    
    # Null check: if event doesn't exist, return 404
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    
    # Idempotency check: if already executed, return success
    if event.executed:
        logger.info(f"Event {event_id} already executed, skipping")
        return {"status": "already_executed"}
    
    # Agent Logic (Hybrid Architecture)
    # UNIFIED ARCHITECTURE: Thinking Mode (Standard API) via AgentRuntime
    
# ========================================
# Agent Logic: Thinking Mode (Text)
# ========================================
    
    # 1. Trigger Prompt (Minimal - agent uses tools to understand context)
    trigger_prompt = "You just woke up."
    
    # 2. Run Turn via AgentRuntime
    logger.info(f"--- Calling AgentRuntime.run_thinking_mode for user {event.user_id} ---")
    try:
        async for agent_event in AgentRuntime.run_thinking_mode(
            user_id=event.user_id,
            trigger_context=trigger_prompt,
            session_manager=session_manager
        ):
            # Log significant events
            if hasattr(agent_event, "content") and agent_event.content and agent_event.content.parts:
                for part in agent_event.content.parts:
                    if hasattr(part, "function_call") and part.function_call:
                         logger.info(f"🤖 [THINKING] Tool Call: {part.function_call.name}")
                    if hasattr(part, "text") and part.text:
                         logger.info(f"🤖 [THINKING] Agent response: {part.text}")
        
    except Exception as e:
        logger.error(f"❌ [THINKING] Agent failed to run: {e}")
        # Don't re-raise, we still want to mark event as executed so we don't loop forever
    
    # Mark event as executed
    await event_repo.mark_executed(event_id)
    
    # Cleanup: Delete the cron job from cron-jobs.org
    if event.cron_job_id:
        try:
            await cron_service.delete_job(event.cron_job_id)
        except Exception as cleanup_error:
            logger.warning(f"Failed to cleanup cron job {event.cron_job_id}: {cleanup_error}")
            # Don't fail the request if cleanup fails
    
    return {"status": "executed", "agent_response": "processed"}

@app.post("/api/save-token")
async def save_push_token(
    payload: dict
):
    """
    Saves the user's Expo push token.
    Payload expected: {"user_id": "...", "token": "..."}
    """
    import re
    
    user_id = payload.get("user_id")
    token = payload.get("token")
    
    if not user_id or not token:
        raise HTTPException(status_code=400, detail="Missing user_id or token")
    
    # Validate Expo token format
    if not re.match(r'^ExponentPushToken\[.+\]$', token):
        raise HTTPException(status_code=400, detail="Invalid token format")
        
    await user_repo.save_push_token(user_id, token)
    return {"status": "saved", "user_id": user_id}


# @app.on_event("startup")
# async def schedule_morning_wakes():
#     """
#     Heartbeat Logic:
#     On server startup, ensure every user has a morning_wake event scheduled for tomorrow.
#     This guarantees the 'Agent Loop' restarts even if the server crashed overnight.
#     Idempotent: Checks for existence before creating.
#     """
#     # Commented out during Firestore migration
#     pass


@app.websocket("/ws/{user_id}/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: str,
    session_id: str,
) -> None:
    """
    WebSocket endpoint for bidirectional streaming with ADK.
    
    Args:
        websocket: The WebSocket connection
        user_id: User identifier
        session_id: Session identifier
    """
    logger.info(f"WebSocket connection request: user_id={user_id}, session_id={session_id}")
    await websocket.accept()
    logger.info("WebSocket connection accepted")

    # Override session_id with the deterministic daily ID
    # This aligns the WebSocket connection with the same session used by cron/background agent.
    # We ignore the client-provided session_id (which is often random or stale).
    unified_session_id = ADKSessionManager.get_daily_session_id(user_id)
    logger.info(f"Map WebSocket connection to Unified Session ID: {unified_session_id}")

    # Set context variables for this request/connection
    current_user_id.set(user_id)
    current_session_id.set(unified_session_id)

    # ========================================
    # Session Initialization
    # ========================================
    
    # Determine response modality based on Conversation Mode (Live API)
    # Conversation Mode: Audio/Video
    run_config = AgentRuntime.get_conversation_mode_config()
    logger.info(f"Using Conversation Mode (AUDIO) for session: {unified_session_id}")

    # Get or create session
    session = await session_manager.get_or_create_session(
        app_name=APP_NAME, user_id=user_id, session_id=unified_session_id
    )

    live_request_queue = LiveRequestQueue()
    
    # Create UI event queue for this connection
    ui_event_queue = asyncio.Queue()
    set_ui_event_queue(ui_event_queue)
    logger.info("UI event queue created for this connection")

    # Send simple activation message to initiate conversation and let agent greet naturally
    activation_message = types.Content(
        parts=[types.Part(text="Hello")]
    )
    live_request_queue.send_content(activation_message)
    logger.info("Sent activation message to start conversation")

    # ========================================
    # Bidirectional Streaming Tasks
    # ========================================

    async def upstream_task() -> None:
        """Receives messages from WebSocket and sends to LiveRequestQueue."""
        logger.debug("upstream_task started")
        init_handled = False
        
        try:
            while True:
                message = await websocket.receive()
                
                # Handle disconnect
                if message.get("type") == "websocket.disconnect":
                    logger.info("WebSocket disconnect received in upstream_task")
                    break

                # Handle binary frames (audio data)
                if "bytes" in message:
                    audio_data = message["bytes"]
                    logger.debug(f"Received audio chunk: {len(audio_data)} bytes")
                    audio_blob = types.Blob(
                        mime_type="audio/pcm;rate=16000", data=audio_data
                    )
                    live_request_queue.send_realtime(audio_blob)

                # Handle text frames (JSON messages)
                elif "text" in message:
                    text_data = message["text"]
                    logger.debug(f"Received text message: {text_data[:100]}...")
                    
                    try:
                        json_message = json.loads(text_data)
                        
                        # Handle init message (should be first message from client)
                        if json_message.get("type") == "init" and not init_handled:
                            init_handled = True
                            resume_session_id = json_message.get("resume_session_id")
                            trigger_type = json_message.get("trigger_type")
                            
                            logger.info(f"[WS-INIT] Received init handshake - resume_session_id: {resume_session_id}, trigger_type: {trigger_type}")
                            
                            # Store trigger_type in session state for agent context
                            if trigger_type:
                                session.state["trigger_type"] = trigger_type
                                logger.info(f"[WS-INIT] Stored trigger_type in session: {trigger_type}")
                            
                            # If explicit resume requested, update context variable
                            if resume_session_id and resume_session_id != unified_session_id:
                                logger.info(f"[WS-INIT] Client requested explicit session: {resume_session_id} (current: {unified_session_id})")
                                # Note: We keep using unified_session_id for consistency
                                # The session was already loaded/created with the correct ID
                            
                            continue  # Don't process init message further
                        
                        if json_message.get("type") == "text":
                            content = types.Content(
                                parts=[types.Part(text=json_message["text"])]
                            )
                            live_request_queue.send_content(content)
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON received: {text_data}")
        except Exception as e:
            logger.debug(f"upstream_task ended: {e}")

    async def downstream_task() -> None:
        """Receives Events from run_live() and sends to WebSocket."""
        logger.debug("downstream_task started")
        async for event in runner.run_live(
            user_id=user_id,
            session_id=unified_session_id,
            live_request_queue=live_request_queue,
            run_config=run_config,
        ):
            # ✅ NEW: Explicitly check and log transcriptions
            if hasattr(event, 'server_content') and event.server_content:
                if hasattr(event.server_content, 'input_transcription') and event.server_content.input_transcription:
                    logger.info(f"[TRANSCRIPTION-INPUT] User: {event.server_content.input_transcription.text}")
                
                if hasattr(event.server_content, 'output_transcription') and event.server_content.output_transcription:
                    logger.info(f"[TRANSCRIPTION-OUTPUT] Agent: {event.server_content.output_transcription.text}")
            
            # Log every event with content
            if event.content and event.content.parts:
                for i, part in enumerate(event.content.parts):
                    # Log what attributes this part has
                    part_attrs = [a for a in ['text', 'function_call', 'function_response', 'inline_data'] if getattr(part, a, None) is not None]
                    if part_attrs:
                        logger.info(f"[MAIN-EVENT] Part {i} has: {part_attrs}")
                    
                    # Check for function_response in the part
                    func_resp = getattr(part, 'function_response', None)
                    if func_resp is not None:
                        func_name = getattr(func_resp, 'name', 'unknown')
                        response_data = getattr(func_resp, 'response', None)
                        
                        logger.info(f"[MAIN-UI] Found function_response: {func_name}")
                        
                        if func_name == 'generative_ui' and isinstance(response_data, dict):
                            ui_payload = response_data.get("ui_payload")
                            if ui_payload:
                                logger.info(f"[MAIN-UI] >>> Detected ui_payload: component={ui_payload.get('type', 'unknown')}")
                                
                                # Emit custom generative_ui event to frontend
                                ui_event = {
                                    "type": "generative_ui",
                                    "component": ui_payload.get("type"),
                                    "props": ui_payload.get("props", {})
                                }
                                try:
                                    await websocket.send_text(json.dumps(ui_event))
                                    logger.info(f"[MAIN-UI] <<< SENT generative_ui WebSocket event: {ui_payload.get('type')}")
                                except (RuntimeError, WebSocketDisconnect):
                                    logger.warning("[MAIN-UI] WebSocket closed while sending UI event")
            
            # Send original event to client as well
            event_json = event.model_dump_json(exclude_none=True, by_alias=True)
            
            # ✅ NEW: Log a sample to verify field names in JSON
            if hasattr(event, 'server_content') and event.server_content:
                logger.debug(f"[TRANSCRIPTION-JSON-SAMPLE] Sending event with serverContent fields")
            
            logger.debug(f"Sending event to client")
            try:
                await websocket.send_text(event_json)
            except (RuntimeError, WebSocketDisconnect):
                logger.info("WebSocket connection closed, stopping downstream_task")
                break
            
            # Persist state after significant events
            # For robustness in v0, try saving periodically or after each event batch
            # Note: session object is the one we got from get_or_create_session
            try:
                # We save on every event for now to ensure we capture state changes.
                # In production, debouncing or checking event type is better.
                await session_manager.save_agent_session_to_db(unified_session_id, session.state, user_id=user_id)
            except Exception as e:
                logger.warning(f"Failed to persist session state: {e}")

    async def ui_event_task() -> None:
        """Reads UI events from queue and sends to WebSocket."""
        logger.info("ui_event_task started")
        while True:
            try:
                # Wait for UI event with timeout to allow checking for disconnect
                ui_event = await asyncio.wait_for(ui_event_queue.get(), timeout=1.0)
                logger.info(f"[UI-TASK] Got UI event from queue: {ui_event.get('component')}")
                try:
                    await websocket.send_text(json.dumps(ui_event))
                    logger.info(f"[UI-TASK] <<< SENT generative_ui WebSocket event: {ui_event.get('component')}")
                except (RuntimeError, WebSocketDisconnect):
                    logger.info("WebSocket closed, stopping ui_event_task")
                    break
            except asyncio.TimeoutError:
                # Check if we should stop
                if websocket.client_state.name != "CONNECTED":
                    logger.info("WebSocket no longer connected, stopping ui_event_task")
                    break
            except Exception as e:
                logger.error(f"Error in ui_event_task: {e}")
                break

    # Run all three tasks concurrently
    try:
        await asyncio.gather(upstream_task(), downstream_task(), ui_event_task())
    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"Error in streaming: {e}", exc_info=True)
    finally:
        logger.info("Closing live_request_queue")
        live_request_queue.close()
        set_ui_event_queue(None)  # Clear the queue reference
        
        # ========================================
        # POST-CONVERSATION THINKING TURN
        # ========================================
        logger.info("🧠 [POST-CONVERSATION] Triggering thinking mode after conversation ended")
        
        try:
            # Trigger thinking mode to:
            # 1. Review what happened in the conversation
            # 2. Check calendar for what's next
            # 3. Set appropriate timer for next intervention
            # Minimal trigger - agent uses tools to understand context
            trigger_context = "Conversation just ended."
            
            async for _ in AgentRuntime.run_thinking_mode(
                user_id=user_id,
                trigger_context=trigger_context,
                session_manager=session_manager
            ):
                pass  # We don't need to process the events, just let it run
            
            logger.info("✅ [POST-CONVERSATION] Thinking turn completed successfully")
            
        except Exception as thinking_error:
            logger.error(
                f"❌ [POST-CONVERSATION] Failed to run thinking turn: {thinking_error}",
                exc_info=True
            )
            # Don't raise - we don't want to fail the WebSocket close due to this


# ========================================
# Main Entry Point
# ========================================

if __name__ == "__main__":
    import uvicorn
    # Get host and port from environment variables
    host = os.getenv("BACKEND_HOST")
    port = int(os.getenv("BACKEND_PORT"))
    
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
