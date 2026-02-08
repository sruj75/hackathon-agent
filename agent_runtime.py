import logging
import asyncio
import datetime
import json
from typing import AsyncGenerator, Optional
from zoneinfo import ZoneInfo

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.genai import types

from context import current_user_id, current_session_id, current_user_timezone
from repos import user_repo
from session_manager import ADKSessionManager
from voice_agent.agent import thinking_agent, conversation_agent

logger = logging.getLogger(__name__)


def _is_retryable_model_error(error: Exception) -> bool:
    """Return True for transient model/API overload errors worth retrying."""
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int) and status_code in (429, 503):
        return True
    message = str(error).upper()
    return "UNAVAILABLE" in message or "OVERLOADED" in message

class AgentRuntime:
    """
    Manages the Unified Agent Thread.
    
    Architecture:
    - One Agent (root_agent)
    - One Session (session_{user_id}_{today})
    - Two Modes:
        1. Thinking Mode (Background/Cron) -> Text Model (Standard API)
        2. Conversation Mode (Foreground/WS) -> Audio Model (Live API)
    """

    @staticmethod
    async def run_thinking_mode(
        user_id: str, 
        trigger_context: str, 
        session_manager: ADKSessionManager,
        timezone: Optional[str] = None,
    ) -> AsyncGenerator[types.GenerateContentResponse, None]:
        """
        Executes a single turn of the agent in "Thinking Mode" (Text).
        Used by Cron/System triggers.
        """
        # 1. Deterministic Session ID
        session_id = ADKSessionManager.get_daily_session_id(user_id)
        logger.info(f"🧠 [THINKING] Waking agent. Session: {session_id}")
        
        # 2. Set Context
        current_user_id.set(user_id)
        current_session_id.set(session_id)
        resolved_timezone = timezone

        if resolved_timezone:
            try:
                ZoneInfo(resolved_timezone)
            except Exception:
                logger.warning(
                    f"[THINKING] Invalid timezone '{resolved_timezone}' for user {user_id}. Falling back."
                )
                resolved_timezone = None

        if not resolved_timezone:
            try:
                profile = await user_repo.get_profile(user_id)
                profile_timezone = (profile or {}).get("timezone")
                if profile_timezone:
                    ZoneInfo(profile_timezone)
                    resolved_timezone = profile_timezone
            except Exception as tz_error:
                logger.warning(f"[THINKING] Failed to resolve profile timezone for {user_id}: {tz_error}")

        current_user_timezone.set(resolved_timezone or "UTC")
        
        # 3. Initialize Session (Load RAM + DB)
        await session_manager.get_or_create_session(
            app_name="intentive-coach",
            user_id=user_id,
            session_id=session_id
        )
        
        runner = Runner(
            app_name="intentive-coach",
            agent=thinking_agent,
            session_service=session_manager.service
        )
        
        run_config = RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=["TEXT"], 
            # model field removed as it is not supported in RunConfig
        )
        
        # 5. Run the turn
        # We construct a system trigger message to wake the agent.
        trigger_content = types.Content(parts=[types.Part(text=trigger_context)])
        
        try:
            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                try:
                    async for event in runner.run_async(
                        user_id=user_id,
                        session_id=session_id,
                        new_message=trigger_content,
                        run_config=run_config
                    ):
                        yield event
                    break
                except Exception as e:
                    if _is_retryable_model_error(e) and attempt < max_attempts:
                        backoff_seconds = 2 ** (attempt - 1)
                        logger.warning(
                            f"⚠️ [THINKING] Attempt {attempt}/{max_attempts} failed with transient model error: {e}. "
                            f"Retrying in {backoff_seconds}s."
                        )
                        await asyncio.sleep(backoff_seconds)
                        continue

                    logger.error(f"❌ [THINKING] Failed: {e}")
                    raise e
        finally:
            # 6. Fetch LATEST session state from Service
            latest_session = await session_manager.get_or_create_session(
                app_name="intentive-coach",
                user_id=user_id,
                session_id=session_id
            )
            
            # Sync ADK events to state for persistence
            # We explicitly save the event history into the state dict so it persists in Firestore
            if hasattr(latest_session, 'events'):
                 latest_session.state['history'] = [
                     event.model_dump(mode='json') for event in latest_session.events
                 ]
            
            # 7. Save to Firestore
            await session_manager.save_agent_session_to_db(session_id, latest_session.state, user_id=user_id)

    @staticmethod
    def get_conversation_mode_config() -> RunConfig:
        """
        Returns the RunConfig for "Conversation Mode" (Audio/Live API).
        Used by WebSocket endpoint.
        """
        # Live API configuration
        return RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )
