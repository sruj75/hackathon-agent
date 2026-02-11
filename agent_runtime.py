import logging

from google.adk.agents.run_config import RunConfig, StreamingMode
from google.genai import types

logger = logging.getLogger(__name__)


class AgentRuntime:
    """Runtime helpers for realtime conversation execution."""

    @staticmethod
    def get_realtime_run_config() -> RunConfig:
        """RunConfig for Live API conversation streaming."""
        return RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )

    @staticmethod
    def get_conversation_mode_config() -> RunConfig:
        """Backward-compatible alias for realtime run config."""
        return AgentRuntime.get_realtime_run_config()
