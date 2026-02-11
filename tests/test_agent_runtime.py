"""Unit tests for agent_runtime.py against current repository interfaces."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from freezegun import freeze_time
from google.adk.agents.run_config import StreamingMode

from agent_runtime import AgentRuntime
from context import current_session_id, current_user_id


@pytest.fixture
def mock_session_manager():
    session = MagicMock()
    session.state = {"test": "state"}
    session.events = []

    manager = MagicMock()
    manager.get_or_create_session = AsyncMock(return_value=session)
    manager.save_agent_session_to_db = AsyncMock(return_value=True)
    return manager


class TestAgentRuntime:
    @pytest.mark.asyncio
    async def test_thinking_mode_sets_context_and_persists(self, mock_session_manager):
        with patch("agent_runtime.Runner") as mock_runner_class:
            async def mock_run_async(*args, **kwargs):
                yield MagicMock()

            mock_runner = MagicMock()
            mock_runner.run_async = mock_run_async
            mock_runner_class.return_value = mock_runner

            with freeze_time("2026-02-08"):
                events = []
                async for event in AgentRuntime.run_thinking_mode(
                    user_id="user_test",
                    trigger_context="Test trigger",
                    session_manager=mock_session_manager,
                ):
                    events.append(event)

            assert len(events) == 1
            assert current_user_id.get() == "user_test"
            assert current_session_id.get() == "session_user_test_2026-02-08"
            assert mock_session_manager.get_or_create_session.await_count >= 2
            mock_session_manager.save_agent_session_to_db.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_thinking_mode_retries_transient_errors(self, mock_session_manager):
        attempts = {"count": 0}

        with patch("agent_runtime.Runner") as mock_runner_class:
            with patch("agent_runtime.asyncio.sleep", new=AsyncMock()) as mock_sleep:
                async def flaky_run_async(*args, **kwargs):
                    attempts["count"] += 1
                    if attempts["count"] == 1:
                        raise Exception("503 UNAVAILABLE")
                    yield MagicMock()

                mock_runner = MagicMock()
                mock_runner.run_async = flaky_run_async
                mock_runner_class.return_value = mock_runner

                events = []
                async for event in AgentRuntime.run_thinking_mode(
                    user_id="user_retry",
                    trigger_context="Retry trigger",
                    session_manager=mock_session_manager,
                ):
                    events.append(event)

                assert attempts["count"] == 2
                assert len(events) == 1
                mock_sleep.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_thinking_mode_raises_non_retryable_errors(self, mock_session_manager):
        with patch("agent_runtime.Runner") as mock_runner_class:
            with patch("agent_runtime.asyncio.sleep", new=AsyncMock()) as mock_sleep:
                async def bad_run_async(*args, **kwargs):
                    raise Exception("invalid argument")
                    yield

                mock_runner = MagicMock()
                mock_runner.run_async = bad_run_async
                mock_runner_class.return_value = mock_runner

                with pytest.raises(Exception, match="invalid argument"):
                    async for _ in AgentRuntime.run_thinking_mode(
                        user_id="user_fail",
                        trigger_context="Fail trigger",
                        session_manager=mock_session_manager,
                    ):
                        pass

                mock_sleep.assert_not_awaited()

    def test_get_conversation_mode_config(self):
        config = AgentRuntime.get_conversation_mode_config()
        assert config.response_modalities == ["AUDIO"]
        assert config.streaming_mode == StreamingMode.BIDI
        assert config.input_audio_transcription is not None
        assert config.output_audio_transcription is not None
