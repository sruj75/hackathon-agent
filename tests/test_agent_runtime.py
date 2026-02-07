"""
Unit tests for agent_runtime.py

Tests:
- Thinking mode execution
- Context variable setting
- Session loading and persistence
- Configuration generation for conversation mode
"""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from freezegun import freeze_time

from agent_runtime import AgentRuntime
from context import current_user_id, current_session_id
from models import UserProfile


class TestAgentRuntime:
    """Test suite for AgentRuntime."""

    @pytest.mark.asyncio
    async def test_thinking_mode_sets_context_variables(self, test_db, test_user, mock_session_manager):
        """Test that thinking mode sets context variables correctly."""
        with patch('agent_runtime.Runner') as mock_runner_class:
            # Mock runner to return empty async generator
            async def mock_run(*args, **kwargs):
                yield MagicMock() # Yield at least one event
            
            mock_runner_instance = MagicMock()
            mock_runner_instance.run_async = mock_run
            mock_runner_class.return_value = mock_runner_instance
            
            # Run thinking mode
            async for _ in AgentRuntime.run_thinking_mode(
                user_id=test_user.user_id,
                trigger_context="Test trigger",
                session_manager=mock_session_manager,
                db=test_db
            ):
                pass
            
            # Verify context variables were set
            assert current_user_id.get() == test_user.user_id
            assert current_session_id.get() == f"session_{test_user.user_id}_{datetime.now().date().isoformat()}"

    @pytest.mark.asyncio
    async def test_thinking_mode_generates_session_id(self, test_db, test_user, mock_session_manager):
        """Test that thinking mode generates correct daily session ID."""
        with freeze_time("2026-02-04"):
            with patch('agent_runtime.Runner'):
                async for _ in AgentRuntime.run_thinking_mode(
                    user_id=test_user.user_id,
                    trigger_context="Test trigger",
                    session_manager=mock_session_manager,
                    db=test_db
                ):
                    pass
                
                # Verify session ID format
                session_id = current_session_id.get()
                assert session_id == "session_user_test_2026-02-04"

    @pytest.mark.asyncio
    async def test_thinking_mode_loads_or_creates_session(self, test_db, test_user, mock_session_manager):
        """Test that thinking mode loads or creates session."""
        with patch('agent_runtime.Runner') as mock_runner_class:
            # Mock empty run
            async def mock_run(*args, **kwargs):
                return
                yield
            
            mock_runner_instance = MagicMock()
            mock_runner_instance.run_async = mock_run
            mock_runner_class.return_value = mock_runner_instance
            
            # Run thinking mode
            async for _ in AgentRuntime.run_thinking_mode(
                user_id=test_user.user_id,
                trigger_context="Morning wake",
                session_manager=mock_session_manager,
                db=test_db
            ):
                pass
            
            # Verify get_or_create_session was called
            assert mock_session_manager.get_or_create_session.call_count >= 1
            call_args = mock_session_manager.get_or_create_session.call_args_list[0]
            assert call_args.kwargs['app_name'] == "intentive-coach"
            assert call_args.kwargs['user_id'] == test_user.user_id

    @pytest.mark.asyncio
    async def test_thinking_mode_saves_session_after_run(self, test_db, test_user, mock_session_manager):
        """Test that thinking mode saves session to DB after execution."""
        # Mock session with state
        mock_session = MagicMock()
        mock_session.state = {"test": "state", "history": []}
        mock_session.events = []
        mock_session_manager.get_or_create_session.return_value = mock_session
        
        with patch('agent_runtime.Runner') as mock_runner_class:
            # Mock empty run
            async def mock_run(*args, **kwargs):
                return
                yield
            
            mock_runner_instance = MagicMock()
            mock_runner_instance.run_async = mock_run
            mock_runner_class.return_value = mock_runner_instance
            
            # Run thinking mode
            async for _ in AgentRuntime.run_thinking_mode(
                user_id=test_user.user_id,
                trigger_context="Test trigger",
                session_manager=mock_session_manager,
                db=test_db
            ):
                pass
            
            # Verify save_agent_session_to_db was called
            mock_session_manager.save_agent_session_to_db.assert_called_once()
            call_args = mock_session_manager.save_agent_session_to_db.call_args
            assert test_user.user_id in str(call_args)

    @pytest.mark.asyncio
    async def test_thinking_mode_uses_text_model(self, test_db, test_user, mock_session_manager):
        """Test that thinking mode configures text response modalities."""
        with patch('agent_runtime.Runner') as mock_runner_class:
            # Mock empty run
            async def mock_run(*args, **kwargs):
                # Capture run_config
                run_config = kwargs.get('run_config')
                assert run_config is not None
                assert run_config.response_modalities == ["TEXT"]
                return
                yield
            
            mock_runner_instance = MagicMock()
            mock_runner_instance.run_async = mock_run
            mock_runner_class.return_value = mock_runner_instance
            
            # Run thinking mode
            async for _ in AgentRuntime.run_thinking_mode(
                user_id=test_user.user_id,
                trigger_context="Test trigger",
                session_manager=mock_session_manager,
                db=test_db
            ):
                pass

    @pytest.mark.asyncio
    async def test_thinking_mode_uses_thinking_agent(self, test_db, test_user, mock_session_manager):
        """Test that thinking mode uses the thinking_agent."""
        with patch('agent_runtime.Runner') as mock_runner_class:
            with patch('agent_runtime.thinking_agent') as mock_thinking_agent:
                # Mock empty run
                async def mock_run(*args, **kwargs):
                    return
                    yield
                
                mock_runner_instance = MagicMock()
                mock_runner_instance.run_async = mock_run
                mock_runner_class.return_value = mock_runner_instance
                
                # Run thinking mode
                async for _ in AgentRuntime.run_thinking_mode(
                    user_id=test_user.user_id,
                    trigger_context="Test trigger",
                    session_manager=mock_session_manager,
                    db=test_db
                ):
                    pass
                
                # Verify Runner was initialized with thinking_agent
                mock_runner_class.assert_called()
                call_kwargs = mock_runner_class.call_args.kwargs
                assert call_kwargs['agent'] == mock_thinking_agent

    @pytest.mark.asyncio
    async def test_thinking_mode_trigger_context_passed(self, test_db, test_user, mock_session_manager):
        """Test that trigger context is passed to agent."""
        trigger_text = "Morning wake: It's 8:00 AM"
        
        with patch('agent_runtime.Runner') as mock_runner_class:
            # Mock run to capture new_message
            async def mock_run(*args, **kwargs):
                new_message = kwargs.get('new_message')
                assert new_message is not None
                # Verify trigger context is in the message
                assert trigger_text in str(new_message.parts[0].text)
                return
                yield
            
            mock_runner_instance = MagicMock()
            mock_runner_instance.run_async = mock_run
            mock_runner_class.return_value = mock_runner_instance
            
            # Run thinking mode
            async for _ in AgentRuntime.run_thinking_mode(
                user_id=test_user.user_id,
                trigger_context=trigger_text,
                session_manager=mock_session_manager,
                db=test_db
            ):
                pass

    @pytest.mark.asyncio
    async def test_thinking_mode_handles_errors(self, test_db, test_user, mock_session_manager):
        """Test that thinking mode handles and logs errors."""
        with patch('agent_runtime.Runner') as mock_runner_class:
            # Mock run to raise error
            async def mock_run(*args, **kwargs):
                raise Exception("Agent failed")
                yield
            
            mock_runner_instance = MagicMock()
            mock_runner_instance.run_async = mock_run
            mock_runner_class.return_value = mock_runner_instance
            
            # Run thinking mode - should propagate error
            with pytest.raises(Exception, match="Agent failed"):
                async for _ in AgentRuntime.run_thinking_mode(
                    user_id=test_user.user_id,
                    trigger_context="Test trigger",
                    session_manager=mock_session_manager,
                    db=test_db
                ):
                    pass

    @pytest.mark.asyncio
    async def test_thinking_mode_syncs_events_to_history(self, test_db, test_user, mock_session_manager):
        """Test that thinking mode syncs ADK events to state history."""
        # Mock session with events
        mock_event = MagicMock()
        mock_event.model_dump = MagicMock(return_value={"type": "test_event"})
        
        mock_session = MagicMock()
        mock_session.state = {"test": "state"}
        mock_session.events = [mock_event]
        mock_session_manager.get_or_create_session.return_value = mock_session
        
        with patch('agent_runtime.Runner') as mock_runner_class:
            # Mock empty run
            async def mock_run(*args, **kwargs):
                return
                yield
            
            mock_runner_instance = MagicMock()
            mock_runner_instance.run_async = mock_run
            mock_runner_class.return_value = mock_runner_instance
            
            # Run thinking mode
            async for _ in AgentRuntime.run_thinking_mode(
                user_id=test_user.user_id,
                trigger_context="Test trigger",
                session_manager=mock_session_manager,
                db=test_db
            ):
                pass
            
            # Verify events were synced to history
            assert 'history' in mock_session.state
            assert len(mock_session.state['history']) == 1
            assert mock_session.state['history'][0]["type"] == "test_event"

    def test_get_conversation_mode_config(self):
        """Test conversation mode configuration generation."""
        config = AgentRuntime.get_conversation_mode_config()
        
        assert config is not None
        assert config.response_modalities == ["AUDIO"]
        assert config.input_audio_transcription is not None
        assert config.output_audio_transcription is not None

    def test_get_conversation_mode_config_uses_bidi_streaming(self):
        """Test that conversation mode uses bidirectional streaming."""
        from google.adk.agents.run_config import StreamingMode
        
        config = AgentRuntime.get_conversation_mode_config()
        
        assert config.streaming_mode == StreamingMode.BIDI

    @pytest.mark.asyncio
    async def test_thinking_mode_without_db_parameter(self, test_user, mock_session_manager):
        """Test that thinking mode works without db parameter."""
        with patch('agent_runtime.Runner') as mock_runner_class:
            # Mock empty run
            async def mock_run(*args, **kwargs):
                return
                yield
            
            mock_runner_instance = MagicMock()
            mock_runner_instance.run_async = mock_run
            mock_runner_class.return_value = mock_runner_instance
            
            # Run thinking mode without db
            async for _ in AgentRuntime.run_thinking_mode(
                user_id=test_user.user_id,
                trigger_context="Test trigger",
                session_manager=mock_session_manager,
                db=None
            ):
                pass
            
            # Should not crash, context_db should just not be set
            # (or set to None which is fine)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_thinking_mode_end_to_end_flow(self, test_db, test_user, mock_session_manager):
        """
        Integration test: Full thinking mode execution flow.
        
        1. Set context variables
        2. Load/create session
        3. Execute agent turn
        4. Save session to DB
        """
        # Mock complete session
        mock_session = MagicMock()
        mock_session.state = {
            "user_id": test_user.user_id,
            "date": "2026-02-04",
            "current_mode": "PLANNING"
        }
        mock_session.events = []
        mock_session_manager.get_or_create_session.return_value = mock_session
        
        with patch('agent_runtime.Runner') as mock_runner_class:
            # Mock agent response
            async def mock_run(*args, **kwargs):
                # Simulate agent response event
                mock_response = MagicMock()
                mock_response.text = "Good morning! Ready to plan your day?"
                yield mock_response
            
            mock_runner_instance = MagicMock()
            mock_runner_instance.run_async = mock_run
            mock_runner_class.return_value = mock_runner_instance
            
            # Run thinking mode
            responses = []
            async for response in AgentRuntime.run_thinking_mode(
                user_id=test_user.user_id,
                trigger_context="Morning wake: 8:00 AM",
                session_manager=mock_session_manager,
                db=test_db
            ):
                responses.append(response)
            
            # Verify flow
            assert len(responses) == 1
            assert responses[0].text == "Good morning! Ready to plan your day?"
            
            # Verify context was set
            assert current_user_id.get() == test_user.user_id
            
            # Verify session operations
            assert mock_session_manager.get_or_create_session.call_count >= 1
            assert mock_session_manager.save_agent_session_to_db.call_count == 1
