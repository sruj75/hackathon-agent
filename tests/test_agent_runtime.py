"""Unit tests for agent_runtime.py."""

from google.adk.agents.run_config import StreamingMode

from agent_runtime import AgentRuntime


class TestAgentRuntime:
    def test_get_realtime_run_config(self):
        config = AgentRuntime.get_realtime_run_config()
        assert config.response_modalities == ["AUDIO"]
        assert config.streaming_mode == StreamingMode.BIDI
        assert config.input_audio_transcription is not None
        assert config.output_audio_transcription is not None

    def test_get_conversation_mode_config_alias(self):
        primary = AgentRuntime.get_realtime_run_config()
        alias = AgentRuntime.get_conversation_mode_config()
        assert alias.response_modalities == primary.response_modalities
        assert alias.streaming_mode == primary.streaming_mode
