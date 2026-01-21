"""
FastAPI Backend for Intentive Voice Agent

This is the production FastAPI server that provides:
- WebSocket endpoint for real-time audio streaming with ADK
- Health check endpoint
"""
import asyncio
import json
import logging
import warnings
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# Load environment variables BEFORE importing agent
load_dotenv(Path(__file__).parent / ".env")

# Import agent after loading env
from voice_agent.agent import root_agent as agent  # noqa: E402
from voice_agent.render_ui_tools import set_ui_event_queue, get_ui_event_queue  # noqa: E402

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.genai import types

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
session_service = InMemorySessionService()
runner = Runner(app_name=APP_NAME, agent=agent, session_service=session_service)


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

    # ========================================
    # Session Initialization
    # ========================================
    
    # Determine response modality based on model
    model_name = agent.model
    is_native_audio = "native-audio" in model_name.lower() or "live" in model_name.lower()
    
    if is_native_audio:
        response_modalities = ["AUDIO"]
        run_config = RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=response_modalities,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )
        logger.info(f"Using AUDIO response modality for model: {model_name}")
    else:
        response_modalities = ["TEXT"]
        run_config = RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=response_modalities,
        )
        logger.info(f"Using TEXT response modality for model: {model_name}")

    # Get or create session
    session = await session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if not session:
        await session_service.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )

    live_request_queue = LiveRequestQueue()
    
    # Create UI event queue for this connection
    ui_event_queue = asyncio.Queue()
    set_ui_event_queue(ui_event_queue)
    logger.info("UI event queue created for this connection")

    # ========================================
    # Bidirectional Streaming Tasks
    # ========================================

    async def upstream_task() -> None:
        """Receives messages from WebSocket and sends to LiveRequestQueue."""
        logger.debug("upstream_task started")
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
            session_id=session_id,
            live_request_queue=live_request_queue,
            run_config=run_config,
        ):
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
            logger.debug(f"Sending event to client")
            try:
                await websocket.send_text(event_json)
            except (RuntimeError, WebSocketDisconnect):
                logger.info("WebSocket connection closed, stopping downstream_task")
                break

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
# Main Entry Point
# ========================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
