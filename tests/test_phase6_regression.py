"""
Phase 6 regression tests.

Ensures WebSocket session resumption/init handshake and generative UI
forwarding still work while keeping older behavior intact.
"""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import main
from auth import AuthUser
from session_manager import ADKSessionManager

COMPLETED_PROFILE = {
    "timezone": "UTC",
    "wake_time": "07:30",
    "bedtime": "22:30",
    "onboarding_status": "completed",
    "onboarding_completed_at": "2026-01-01T00:00:00+00:00",
}

PENDING_PROFILE = {
    "timezone": "UTC",
    "wake_time": None,
    "bedtime": None,
    "onboarding_status": "pending",
    "onboarding_completed_at": None,
}

class _FakeContent:
    def __init__(self, parts=None, role=None):
        self.parts = parts or []
        self.role = role


class _FakePart:
    def __init__(
        self, text=None, function_call=None, function_response=None, inline_data=None
    ):
        self.text = text
        self.function_call = function_call
        self.function_response = function_response
        self.inline_data = inline_data


class _FakeBlob:
    def __init__(self, mime_type=None, data=None):
        self.mime_type = mime_type
        self.data = data


class _FakeLiveRequestQueue:
    def __init__(self):
        self.closed = False
        self.contents = []
        self.realtime_blobs = []

    def send_content(self, _content):
        self.contents.append(_content)
        return None

    def send_realtime(self, _blob):
        self.realtime_blobs.append(_blob)
        return None

    def close(self):
        self.closed = True


class _FakeEvent:
    def __init__(self, parts=None, payload=None):
        self.content = SimpleNamespace(parts=parts or [])
        self.server_content = None
        self._payload = payload or {"type": "adk_event"}

    def model_dump_json(self, **_kwargs):
        return json.dumps(self._payload)


class _FakeWebSocket:
    def __init__(self, incoming_messages):
        self._messages = list(incoming_messages)
        self.sent_texts = []
        self.client_state = SimpleNamespace(name="CONNECTED")
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def receive(self):
        if not self._messages:
            self.client_state.name = "DISCONNECTED"
            return {"type": "websocket.disconnect"}

        next_message = self._messages.pop(0)
        if next_message.get("type") == "websocket.disconnect":
            self.client_state.name = "DISCONNECTED"
        return next_message

    async def send_text(self, text):
        self.sent_texts.append(text)


@pytest.mark.regression
class TestPhase6WebSocketFlow:
    def test_select_ws_session_id_uses_requested_when_valid(self):
        today_session_id = ADKSessionManager.get_daily_session_id("user_test", "UTC")
        selected = main._select_ws_session_id(
            "user_test",
            today_session_id,
            timezone_name="UTC",
        )
        assert selected == today_session_id

    def test_select_ws_session_id_rejects_invalid_or_wrong_user(self):
        fallback = ADKSessionManager.get_daily_session_id("user_test", "UTC")
        assert (
            main._select_ws_session_id(
                "user_test", "client_random_session", timezone_name="UTC"
            )
            == fallback
        )
        assert (
            main._select_ws_session_id(
                "user_test", "session_other_user_2026-02-06", timezone_name="UTC"
            )
            == fallback
        )

    @pytest.mark.asyncio
    async def test_init_handshake_stores_trigger_type(self, monkeypatch):
        fake_session = SimpleNamespace(state={})
        get_or_create_mock = AsyncMock(return_value=fake_session)

        monkeypatch.setattr(main, "LiveRequestQueue", _FakeLiveRequestQueue)
        monkeypatch.setattr(main.types, "Content", _FakeContent)
        monkeypatch.setattr(main.types, "Part", _FakePart)
        monkeypatch.setattr(main.types, "Blob", _FakeBlob)
        monkeypatch.setattr(
            main.AgentRuntime,
            "get_realtime_run_config",
            staticmethod(lambda: object()),
        )
        monkeypatch.setattr(
            main.user_repo, "get_profile", AsyncMock(return_value=dict(COMPLETED_PROFILE))
        )
        monkeypatch.setattr(
            main.user_repo, "update_profile", AsyncMock(return_value={"timezone": "UTC"})
        )
        monkeypatch.setattr(main.session_manager, "get_or_create_session", get_or_create_mock)
        monkeypatch.setattr(
            main.session_manager, "save_agent_session_to_db", AsyncMock(return_value=True)
        )

        async def fake_run_live(**_kwargs):
            if False:  # pragma: no cover
                yield None

        monkeypatch.setattr(main.runner, "run_live", fake_run_live)
        monkeypatch.setattr(
            main,
            "verify_supabase_jwt",
            AsyncMock(
                return_value=AuthUser(
                    user_id="user_test", email="test@example.com", claims={}
                )
            ),
        )

        expected_session_id = ADKSessionManager.get_daily_session_id(
            "user_test", "America/New_York"
        )
        ws = _FakeWebSocket(
            [
                {
                    "text": json.dumps(
                        {
                            "type": "init",
                            "access_token": "jwt_test",
                            "resume_session_id": expected_session_id,
                            "trigger_type": "checkin",
                            "timezone": "America/New_York",
                        }
                    )
                },
                {"type": "websocket.disconnect"},
            ]
        )

        await main.websocket_endpoint(ws, "client_random_session")

        assert ws.accepted is True
        assert fake_session.state["trigger_type"] == "checkin"
        assert fake_session.state["user_timezone"] == "America/New_York"
        get_or_create_mock.assert_awaited_with(
            app_name=main.APP_NAME, user_id="user_test", session_id=expected_session_id
        )

    @pytest.mark.asyncio
    async def test_incomplete_onboarding_forces_onboarding_mode(self, monkeypatch):
        fake_session = SimpleNamespace(state={})
        get_or_create_mock = AsyncMock(return_value=fake_session)

        monkeypatch.setattr(main, "LiveRequestQueue", _FakeLiveRequestQueue)
        monkeypatch.setattr(main.types, "Content", _FakeContent)
        monkeypatch.setattr(main.types, "Part", _FakePart)
        monkeypatch.setattr(main.types, "Blob", _FakeBlob)
        monkeypatch.setattr(
            main.AgentRuntime,
            "get_realtime_run_config",
            staticmethod(lambda: object()),
        )
        monkeypatch.setattr(
            main.user_repo, "get_profile", AsyncMock(return_value=dict(PENDING_PROFILE))
        )
        monkeypatch.setattr(main.session_manager, "get_or_create_session", get_or_create_mock)
        monkeypatch.setattr(
            main.session_manager, "save_agent_session_to_db", AsyncMock(return_value=True)
        )

        async def fake_onboarding_run_live(**_kwargs):
            if False:  # pragma: no cover
                yield None

        async def fail_if_main_runner_used(**_kwargs):
            raise AssertionError("main runner should not be used for pending onboarding")
            if False:  # pragma: no cover
                yield None

        monkeypatch.setattr(main.onboarding_runner, "run_live", fake_onboarding_run_live)
        monkeypatch.setattr(main.main_runner, "run_live", fail_if_main_runner_used)
        monkeypatch.setattr(
            main,
            "verify_supabase_jwt",
            AsyncMock(
                return_value=AuthUser(
                    user_id="user_test", email="test@example.com", claims={}
                )
            ),
        )

        ws = _FakeWebSocket(
            [
                {
                    "text": json.dumps(
                        {
                            "type": "init",
                            "access_token": "jwt_test",
                            "trigger_type": "checkin",
                        }
                    )
                },
                {"type": "websocket.disconnect"},
            ]
        )

        await main.websocket_endpoint(ws, "client_random_session")

        assert ws.accepted is True
        assert fake_session.state["agent_mode"] == "onboarding"
        assert fake_session.state["trigger_type"] == "onboarding"
        assert fake_session.state["lifecycle_state"] == main.LIFECYCLE_NEEDS_ONBOARDING
        get_or_create_mock.assert_awaited_with(
            app_name=main.APP_NAME,
            user_id="user_test",
            session_id="session_onboarding_user_test",
        )

    @pytest.mark.asyncio
    async def test_onboarding_reconnect_does_not_clear_session_history(self, monkeypatch):
        fake_session = SimpleNamespace(state={})
        delete_session_mock = AsyncMock(return_value=None)

        monkeypatch.setattr(main, "LiveRequestQueue", _FakeLiveRequestQueue)
        monkeypatch.setattr(main.types, "Content", _FakeContent)
        monkeypatch.setattr(main.types, "Part", _FakePart)
        monkeypatch.setattr(main.types, "Blob", _FakeBlob)
        monkeypatch.setattr(
            main.AgentRuntime,
            "get_realtime_run_config",
            staticmethod(lambda: object()),
        )
        monkeypatch.setattr(
            main.user_repo, "get_profile", AsyncMock(return_value=dict(PENDING_PROFILE))
        )
        monkeypatch.setattr(
            main.session_manager, "get_or_create_session", AsyncMock(return_value=fake_session)
        )
        monkeypatch.setattr(
            main.session_manager, "save_agent_session_to_db", AsyncMock(return_value=True)
        )
        monkeypatch.setattr(main.session_manager.service, "delete_session", delete_session_mock)

        async def fake_onboarding_run_live(**_kwargs):
            if False:  # pragma: no cover
                yield None

        async def fail_if_main_runner_used(**_kwargs):
            raise AssertionError("main runner should not be used for pending onboarding")
            if False:  # pragma: no cover
                yield None

        monkeypatch.setattr(main.onboarding_runner, "run_live", fake_onboarding_run_live)
        monkeypatch.setattr(main.main_runner, "run_live", fail_if_main_runner_used)
        monkeypatch.setattr(
            main,
            "verify_supabase_jwt",
            AsyncMock(
                return_value=AuthUser(
                    user_id="user_test", email="test@example.com", claims={}
                )
            ),
        )

        ws = _FakeWebSocket(
            [
                {"text": json.dumps({"type": "init", "access_token": "jwt_test"})},
                {"type": "websocket.disconnect"},
            ]
        )

        await main.websocket_endpoint(ws, "client_random_session")

        delete_session_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_onboarding_audio_events_throttle_state_persistence(self, monkeypatch):
        fake_session = SimpleNamespace(state={})
        save_session_mock = AsyncMock(return_value=True)

        monkeypatch.setattr(main, "LiveRequestQueue", _FakeLiveRequestQueue)
        monkeypatch.setattr(main.types, "Content", _FakeContent)
        monkeypatch.setattr(main.types, "Part", _FakePart)
        monkeypatch.setattr(main.types, "Blob", _FakeBlob)
        monkeypatch.setattr(
            main.AgentRuntime,
            "get_realtime_run_config",
            staticmethod(lambda: object()),
        )
        monkeypatch.setattr(
            main.user_repo, "get_profile", AsyncMock(return_value=dict(PENDING_PROFILE))
        )
        monkeypatch.setattr(
            main.session_manager, "get_or_create_session", AsyncMock(return_value=fake_session)
        )
        monkeypatch.setattr(main.session_manager, "save_agent_session_to_db", save_session_mock)

        audio_inline = SimpleNamespace(mime_type="audio/pcm;rate=24000")

        async def fake_onboarding_run_live(**_kwargs):
            for _ in range(5):
                yield _FakeEvent(parts=[_FakePart(inline_data=audio_inline)])

        async def fail_if_main_runner_used(**_kwargs):
            raise AssertionError("main runner should not be used for pending onboarding")
            if False:  # pragma: no cover
                yield None

        monkeypatch.setattr(main.onboarding_runner, "run_live", fake_onboarding_run_live)
        monkeypatch.setattr(main.main_runner, "run_live", fail_if_main_runner_used)
        monkeypatch.setattr(
            main,
            "verify_supabase_jwt",
            AsyncMock(
                return_value=AuthUser(
                    user_id="user_test", email="test@example.com", claims={}
                )
            ),
        )

        ws = _FakeWebSocket(
            [
                {"text": json.dumps({"type": "init", "access_token": "jwt_test"})},
                {"type": "websocket.disconnect"},
            ]
        )

        await main.websocket_endpoint(ws, "client_random_session")

        # Audio chunks should still be forwarded, but DB writes should be throttled.
        assert len(ws.sent_texts) >= 5
        assert 1 <= save_session_mock.await_count <= 2

    @pytest.mark.asyncio
    async def test_onboarding_completion_emits_done_screen_event(self, monkeypatch):
        fake_session = SimpleNamespace(state={})
        save_session_mock = AsyncMock(return_value=True)

        monkeypatch.setattr(main, "LiveRequestQueue", _FakeLiveRequestQueue)
        monkeypatch.setattr(main.types, "Content", _FakeContent)
        monkeypatch.setattr(main.types, "Part", _FakePart)
        monkeypatch.setattr(main.types, "Blob", _FakeBlob)
        monkeypatch.setattr(
            main.AgentRuntime,
            "get_realtime_run_config",
            staticmethod(lambda: object()),
        )
        monkeypatch.setattr(
            main.user_repo, "get_profile", AsyncMock(return_value=dict(PENDING_PROFILE))
        )
        monkeypatch.setattr(
            main.session_manager, "get_or_create_session", AsyncMock(return_value=fake_session)
        )
        monkeypatch.setattr(main.session_manager, "save_agent_session_to_db", save_session_mock)

        function_response = SimpleNamespace(
            name="complete_onboarding",
            response={
                "status": "ok",
                "onboarding_status": "completed",
                "route_hint": "assistant",
            },
        )
        event = _FakeEvent(
            parts=[_FakePart(function_response=function_response)],
            payload={"content": {"parts": []}, "turnComplete": True},
        )

        async def fake_onboarding_run_live(**_kwargs):
            yield event

        async def fail_if_main_runner_used(**_kwargs):
            raise AssertionError("main runner should not be used for pending onboarding")
            if False:  # pragma: no cover
                yield None

        monkeypatch.setattr(main.onboarding_runner, "run_live", fake_onboarding_run_live)
        monkeypatch.setattr(main.main_runner, "run_live", fail_if_main_runner_used)
        monkeypatch.setattr(
            main,
            "verify_supabase_jwt",
            AsyncMock(
                return_value=AuthUser(
                    user_id="user_test", email="test@example.com", claims={}
                )
            ),
        )

        ws = _FakeWebSocket(
            [
                {"text": json.dumps({"type": "init", "access_token": "jwt_test"})},
                {"type": "websocket.disconnect"},
            ]
        )

        await main.websocket_endpoint(ws, "client_random_session")

        parsed_messages = [json.loads(message) for message in ws.sent_texts]
        completion_events = [
            message for message in parsed_messages if message.get("type") == "onboarding_completed"
        ]
        assert len(completion_events) == 1
        assert completion_events[0]["next_action"] == "show_done_screen"
        assert completion_events[0]["route_hint"] == "assistant"

    @pytest.mark.asyncio
    async def test_onboarding_completion_failure_emits_missing_fields_event(
        self, monkeypatch
    ):
        fake_session = SimpleNamespace(state={})
        save_session_mock = AsyncMock(return_value=True)

        monkeypatch.setattr(main, "LiveRequestQueue", _FakeLiveRequestQueue)
        monkeypatch.setattr(main.types, "Content", _FakeContent)
        monkeypatch.setattr(main.types, "Part", _FakePart)
        monkeypatch.setattr(main.types, "Blob", _FakeBlob)
        monkeypatch.setattr(
            main.AgentRuntime,
            "get_realtime_run_config",
            staticmethod(lambda: object()),
        )
        monkeypatch.setattr(
            main.user_repo, "get_profile", AsyncMock(return_value=dict(PENDING_PROFILE))
        )
        monkeypatch.setattr(
            main.session_manager, "get_or_create_session", AsyncMock(return_value=fake_session)
        )
        monkeypatch.setattr(main.session_manager, "save_agent_session_to_db", save_session_mock)

        function_response = SimpleNamespace(
            name="complete_onboarding",
            response={
                "status": "error",
                "error": "playbook.summary is required; playbook.goals must contain at least one item",
                "missing_fields": ["summary", "goals"],
                "onboarding_status": "pending",
                "route_hint": "onboarding",
            },
        )
        event = _FakeEvent(
            parts=[_FakePart(function_response=function_response)],
            payload={"content": {"parts": []}, "turnComplete": True},
        )

        async def fake_onboarding_run_live(**_kwargs):
            yield event

        async def fail_if_main_runner_used(**_kwargs):
            raise AssertionError("main runner should not be used for pending onboarding")
            if False:  # pragma: no cover
                yield None

        monkeypatch.setattr(main.onboarding_runner, "run_live", fake_onboarding_run_live)
        monkeypatch.setattr(main.main_runner, "run_live", fail_if_main_runner_used)
        monkeypatch.setattr(
            main,
            "verify_supabase_jwt",
            AsyncMock(
                return_value=AuthUser(
                    user_id="user_test", email="test@example.com", claims={}
                )
            ),
        )

        ws = _FakeWebSocket(
            [
                {"text": json.dumps({"type": "init", "access_token": "jwt_test"})},
                {"type": "websocket.disconnect"},
            ]
        )

        await main.websocket_endpoint(ws, "client_random_session")

        parsed_messages = [json.loads(message) for message in ws.sent_texts]
        failure_events = [
            message
            for message in parsed_messages
            if message.get("type") == "onboarding_completion_failed"
        ]
        assert len(failure_events) == 1
        assert failure_events[0]["missing_fields"] == ["summary", "goals"]
        assert "playbook.summary is required" in failure_events[0]["message"]

    @pytest.mark.asyncio
    async def test_websocket_uses_valid_requested_session_id(self, monkeypatch):
        fake_session = SimpleNamespace(state={})
        get_or_create_mock = AsyncMock(return_value=fake_session)

        monkeypatch.setattr(main, "LiveRequestQueue", _FakeLiveRequestQueue)
        monkeypatch.setattr(main.types, "Content", _FakeContent)
        monkeypatch.setattr(main.types, "Part", _FakePart)
        monkeypatch.setattr(main.types, "Blob", _FakeBlob)
        monkeypatch.setattr(
            main.AgentRuntime,
            "get_realtime_run_config",
            staticmethod(lambda: object()),
        )
        monkeypatch.setattr(
            main.user_repo, "get_profile", AsyncMock(return_value=dict(COMPLETED_PROFILE))
        )
        monkeypatch.setattr(
            main.user_repo, "update_profile", AsyncMock(return_value={"timezone": "UTC"})
        )
        monkeypatch.setattr(main.session_manager, "get_or_create_session", get_or_create_mock)
        monkeypatch.setattr(
            main.session_manager, "save_agent_session_to_db", AsyncMock(return_value=True)
        )

        async def fake_run_live(**_kwargs):
            if False:  # pragma: no cover
                yield None

        monkeypatch.setattr(main.runner, "run_live", fake_run_live)
        monkeypatch.setattr(
            main,
            "verify_supabase_jwt",
            AsyncMock(
                return_value=AuthUser(
                    user_id="user_test", email="test@example.com", claims={}
                )
            ),
        )

        expected_session_id = ADKSessionManager.get_daily_session_id("user_test", "UTC")
        ws = _FakeWebSocket(
            [
                {
                    "text": json.dumps(
                        {"type": "init", "access_token": "jwt_test"}
                    )
                },
                {"type": "websocket.disconnect"},
            ]
        )

        await main.websocket_endpoint(ws, expected_session_id)

        get_or_create_mock.assert_awaited_with(
            app_name=main.APP_NAME,
            user_id="user_test",
            session_id=expected_session_id,
        )

    @pytest.mark.asyncio
    async def test_only_first_init_message_is_applied(self, monkeypatch):
        fake_session = SimpleNamespace(state={})

        monkeypatch.setattr(main, "LiveRequestQueue", _FakeLiveRequestQueue)
        monkeypatch.setattr(main.types, "Content", _FakeContent)
        monkeypatch.setattr(main.types, "Part", _FakePart)
        monkeypatch.setattr(main.types, "Blob", _FakeBlob)
        monkeypatch.setattr(
            main.AgentRuntime,
            "get_realtime_run_config",
            staticmethod(lambda: object()),
        )
        monkeypatch.setattr(
            main.user_repo, "get_profile", AsyncMock(return_value=dict(COMPLETED_PROFILE))
        )
        monkeypatch.setattr(
            main.user_repo, "update_profile", AsyncMock(return_value={"timezone": "UTC"})
        )
        monkeypatch.setattr(
            main.session_manager, "get_or_create_session", AsyncMock(return_value=fake_session)
        )
        monkeypatch.setattr(
            main.session_manager, "save_agent_session_to_db", AsyncMock(return_value=True)
        )

        async def fake_run_live(**_kwargs):
            if False:  # pragma: no cover
                yield None

        monkeypatch.setattr(main.runner, "run_live", fake_run_live)
        monkeypatch.setattr(
            main,
            "verify_supabase_jwt",
            AsyncMock(
                return_value=AuthUser(
                    user_id="user_test", email="test@example.com", claims={}
                )
            ),
        )

        ws = _FakeWebSocket(
            [
                {
                    "text": json.dumps(
                        {
                            "type": "init",
                            "access_token": "jwt_test",
                            "resume_session_id": "session_a",
                            "trigger_type": "morning_wake",
                            "timezone": "America/Chicago",
                        }
                    )
                },
                {
                    "text": json.dumps(
                        {
                            "type": "init",
                            "access_token": "jwt_test_2",
                            "resume_session_id": "session_b",
                            "trigger_type": "checkin",
                            "timezone": "Asia/Kolkata",
                        }
                    )
                },
                {"type": "websocket.disconnect"},
            ]
        )

        await main.websocket_endpoint(ws, "client_random_session")

        # Regression check: a second init should be ignored.
        assert fake_session.state["trigger_type"] == "morning_wake"
        assert fake_session.state["user_timezone"] == "America/Chicago"

    @pytest.mark.asyncio
    async def test_generative_ui_function_response_is_forwarded_to_frontend(
        self, monkeypatch
    ):
        fake_session = SimpleNamespace(state={})
        save_session_mock = AsyncMock(return_value=True)

        monkeypatch.setattr(main, "LiveRequestQueue", _FakeLiveRequestQueue)
        monkeypatch.setattr(main.types, "Content", _FakeContent)
        monkeypatch.setattr(main.types, "Part", _FakePart)
        monkeypatch.setattr(main.types, "Blob", _FakeBlob)
        monkeypatch.setattr(
            main.AgentRuntime,
            "get_realtime_run_config",
            staticmethod(lambda: object()),
        )
        monkeypatch.setattr(
            main.user_repo, "get_profile", AsyncMock(return_value=dict(COMPLETED_PROFILE))
        )
        monkeypatch.setattr(
            main.user_repo, "update_profile", AsyncMock(return_value={"timezone": "UTC"})
        )
        monkeypatch.setattr(
            main.session_manager, "get_or_create_session", AsyncMock(return_value=fake_session)
        )
        monkeypatch.setattr(main.session_manager, "save_agent_session_to_db", save_session_mock)

        function_response = SimpleNamespace(
            name="generative_ui",
            response={
                "ui_payload": {"type": "day_view", "props": {"events": [], "tasks": [{"id": "t1"}], "display_mode": "planning"}}
            },
        )
        event = _FakeEvent(
            parts=[_FakePart(function_response=function_response)],
            payload={"content": {"parts": []}, "turnComplete": True},
        )

        async def fake_run_live(**_kwargs):
            yield event

        monkeypatch.setattr(main.runner, "run_live", fake_run_live)
        monkeypatch.setattr(
            main,
            "verify_supabase_jwt",
            AsyncMock(
                return_value=AuthUser(
                    user_id="user_test", email="test@example.com", claims={}
                )
            ),
        )

        ws = _FakeWebSocket(
            [
                {"text": json.dumps({"type": "init", "access_token": "jwt_test"})},
                {"type": "websocket.disconnect"},
            ]
        )

        await main.websocket_endpoint(ws, "client_random_session")

        parsed_messages = [json.loads(message) for message in ws.sent_texts]
        assert any(
            message.get("type") == "generative_ui"
            and message.get("component") == "day_view"
            and "tasks" in message.get("props", {})
            for message in parsed_messages
        )
        assert any(message.get("turnComplete") is True for message in parsed_messages)
        assert save_session_mock.await_count >= 1

    @pytest.mark.asyncio
    async def test_invalid_text_payload_is_ignored_without_breaking_session(self, monkeypatch):
        fake_session = SimpleNamespace(state={})
        queue_holder = {}

        class _CapturingQueue(_FakeLiveRequestQueue):
            def __init__(self):
                super().__init__()
                queue_holder["queue"] = self

        monkeypatch.setattr(main, "LiveRequestQueue", _CapturingQueue)
        monkeypatch.setattr(main.types, "Content", _FakeContent)
        monkeypatch.setattr(main.types, "Part", _FakePart)
        monkeypatch.setattr(main.types, "Blob", _FakeBlob)
        monkeypatch.setattr(
            main.AgentRuntime,
            "get_realtime_run_config",
            staticmethod(lambda: object()),
        )
        monkeypatch.setattr(
            main.user_repo, "get_profile", AsyncMock(return_value=dict(COMPLETED_PROFILE))
        )
        monkeypatch.setattr(
            main.user_repo, "update_profile", AsyncMock(return_value={"timezone": "UTC"})
        )
        monkeypatch.setattr(
            main.session_manager, "get_or_create_session", AsyncMock(return_value=fake_session)
        )
        monkeypatch.setattr(
            main.session_manager, "save_agent_session_to_db", AsyncMock(return_value=True)
        )

        async def fake_run_live(**_kwargs):
            if False:  # pragma: no cover
                yield None

        monkeypatch.setattr(main.runner, "run_live", fake_run_live)
        monkeypatch.setattr(
            main,
            "verify_supabase_jwt",
            AsyncMock(
                return_value=AuthUser(
                    user_id="user_test", email="test@example.com", claims={}
                )
            ),
        )

        ws = _FakeWebSocket(
            [
                {"text": json.dumps({"type": "init", "access_token": "jwt_test"})},
                {"text": json.dumps({"type": "text", "text": {"not": "a string"}})},
                {"type": "websocket.disconnect"},
            ]
        )

        await main.websocket_endpoint(ws, "client_random_session")

        queue = queue_holder["queue"]
        # Activation prompt is always the first content event.
        assert len(queue.contents) == 1


@pytest.mark.regression
class TestMorningWakeBootstrap:
    def test_parse_wake_time_returns_none_for_missing_or_invalid(self):
        assert main._parse_wake_time(None) is None
        assert main._parse_wake_time("") is None
        assert main._parse_wake_time("not-a-time") is None
        assert main._parse_wake_time("24:00") is None
        assert main._parse_wake_time("08:70") is None
        assert main._parse_wake_time("08:30") == (8, 30)

    @pytest.mark.asyncio
    async def test_missing_wake_time_skips_morning_wake_creation(self, monkeypatch):
        find_pending_mock = AsyncMock()
        create_event_mock = AsyncMock()
        create_cron_mock = AsyncMock()

        monkeypatch.setattr(main.event_repo, "find_pending_morning_event", find_pending_mock)
        monkeypatch.setattr(main.event_repo, "create_event", create_event_mock)
        monkeypatch.setattr(main.cron_service, "create_one_time_job", create_cron_mock)

        await main._ensure_morning_wake_for_user(
            {"user_id": "user_test", "timezone": "America/New_York"}
        )

        find_pending_mock.assert_not_awaited()
        create_event_mock.assert_not_awaited()
        create_cron_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_wake_time_skips_morning_wake_creation(self, monkeypatch):
        find_pending_mock = AsyncMock()
        create_event_mock = AsyncMock()
        create_cron_mock = AsyncMock()

        monkeypatch.setattr(main.event_repo, "find_pending_morning_event", find_pending_mock)
        monkeypatch.setattr(main.event_repo, "create_event", create_event_mock)
        monkeypatch.setattr(main.cron_service, "create_one_time_job", create_cron_mock)

        await main._ensure_morning_wake_for_user(
            {
                "user_id": "user_test",
                "timezone": "America/New_York",
                "wake_time": "invalid",
            }
        )

        find_pending_mock.assert_not_awaited()
        create_event_mock.assert_not_awaited()
        create_cron_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_existing_event_with_string_payload_does_not_crash(self, monkeypatch):
        target_local = datetime(2026, 2, 8, 9, 0, tzinfo=timezone.utc)
        find_pending_mock = AsyncMock(
            return_value={
                "id": "event_existing",
                "scheduled_time": target_local,
                "payload": '{"timezone":"UTC"}',
                "cron_job_id": 321,
            }
        )
        create_cron_mock = AsyncMock(return_value=999)
        update_event_mock = AsyncMock(return_value=True)
        delete_job_mock = AsyncMock(return_value=True)

        monkeypatch.setattr(main, "_next_morning_wake_datetime", lambda *_args: target_local)
        monkeypatch.setattr(main.event_repo, "find_pending_morning_event", find_pending_mock)
        monkeypatch.setattr(main.cron_service, "create_one_time_job", create_cron_mock)
        monkeypatch.setattr(main.event_repo, "update_event", update_event_mock)
        monkeypatch.setattr(main.cron_service, "delete_job", delete_job_mock)

        await main._ensure_morning_wake_for_user(
            {"user_id": "user_test", "timezone": "UTC", "wake_time": "09:00"}
        )

        # Matching timezone and schedule means no reschedule required.
        create_cron_mock.assert_not_awaited()
        update_event_mock.assert_not_awaited()
        delete_job_mock.assert_not_awaited()


@pytest.mark.regression
class TestAutonomySchedulingBoundaries:
    @pytest.mark.asyncio
    async def test_morning_event_payload_has_system_ownership(self, monkeypatch):
        target_local = datetime(2026, 2, 8, 9, 5, tzinfo=timezone.utc)
        create_event_mock = AsyncMock(return_value={"id": "event_morning_1"})
        create_cron_mock = AsyncMock(return_value=123)
        update_cron_job_id_mock = AsyncMock(return_value=True)

        monkeypatch.setattr(main, "_next_morning_wake_datetime", lambda *_args: target_local)
        monkeypatch.setattr(main.event_repo, "find_pending_morning_event", AsyncMock(return_value=None))
        monkeypatch.setattr(main.event_repo, "create_event", create_event_mock)
        monkeypatch.setattr(main.cron_service, "create_one_time_job", create_cron_mock)
        monkeypatch.setattr(main.event_repo, "update_cron_job_id", update_cron_job_id_mock)

        await main._ensure_morning_wake_for_user(
            {"user_id": "user_test", "timezone": "UTC", "wake_time": "09:05"}
        )

        payload = create_event_mock.await_args.kwargs["payload"]
        assert payload["schedule_owner"] == "system"
        assert payload["schedule_policy"] == "morning_bootstrap"

    @pytest.mark.asyncio
    async def test_reconcile_only_backfills_system_morning_events(self, monkeypatch):
        scheduled_time = datetime(2026, 2, 9, 9, 0, tzinfo=timezone.utc)
        list_missing_mock = AsyncMock(
            return_value=[
                {
                    "id": "event_checkin_agent",
                    "event_type": "checkin",
                    "scheduled_time": scheduled_time,
                    "payload": {
                        "reason": "deep_work",
                        "timezone": "UTC",
                        "schedule_owner": "agent",
                        "schedule_policy": "autonomous_checkin",
                    },
                },
                {
                    "id": "event_morning_system",
                    "event_type": "morning_wake",
                    "scheduled_time": scheduled_time,
                    "payload": {
                        "reason": "daily_bootstrap",
                        "timezone": "UTC",
                    },
                },
            ]
        )
        create_cron_mock = AsyncMock(return_value=999)
        update_cron_job_id_mock = AsyncMock(return_value=True)
        update_event_mock = AsyncMock(return_value=True)

        monkeypatch.setattr(
            main.event_repo,
            "list_future_unexecuted_events_missing_cron",
            list_missing_mock,
        )
        monkeypatch.setattr(main.cron_service, "create_one_time_job", create_cron_mock)
        monkeypatch.setattr(main.event_repo, "update_cron_job_id", update_cron_job_id_mock)
        monkeypatch.setattr(main.event_repo, "update_event", update_event_mock)
        monkeypatch.setattr(main.user_repo, "get_profile", AsyncMock(return_value={"timezone": "UTC"}))

        await main._reconcile_missing_cron_jobs()

        assert create_cron_mock.await_count == 1
        assert create_cron_mock.await_args.kwargs["event_id"] == "event_morning_system"
        payload = update_event_mock.await_args.kwargs["payload"]
        assert payload["schedule_owner"] == "system"
        assert payload["schedule_policy"] == "morning_bootstrap"

    @pytest.mark.asyncio
    async def test_reconcile_handles_string_payload_rows(self, monkeypatch):
        scheduled_time = datetime(2026, 2, 9, 9, 0, tzinfo=timezone.utc)
        list_missing_mock = AsyncMock(
            return_value=[
                {
                    "id": "event_morning_system",
                    "event_type": "morning_wake",
                    "scheduled_time": scheduled_time,
                    "payload": '{"reason":"daily_bootstrap","timezone":"UTC"}',
                },
            ]
        )
        create_cron_mock = AsyncMock(return_value=111)
        update_cron_job_id_mock = AsyncMock(return_value=True)
        update_event_mock = AsyncMock(return_value=True)

        monkeypatch.setattr(
            main.event_repo,
            "list_future_unexecuted_events_missing_cron",
            list_missing_mock,
        )
        monkeypatch.setattr(main.cron_service, "create_one_time_job", create_cron_mock)
        monkeypatch.setattr(main.event_repo, "update_cron_job_id", update_cron_job_id_mock)
        monkeypatch.setattr(main.event_repo, "update_event", update_event_mock)

        await main._reconcile_missing_cron_jobs()

        create_cron_mock.assert_awaited_once()
        update_cron_job_id_mock.assert_awaited_once_with("event_morning_system", 111)
        update_event_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_startup_skips_reconcile_by_default(self, monkeypatch):
        users = [{"user_id": "u1", "timezone": "UTC", "wake_time": "08:00"}]
        get_all_users_mock = AsyncMock(return_value=users)
        ensure_wake_mock = AsyncMock(return_value=None)
        reconcile_mock = AsyncMock(return_value=None)

        monkeypatch.delenv("ENABLE_RELIABILITY_BOOTSTRAP", raising=False)
        monkeypatch.delenv("ENABLE_MORNING_CRON_RECONCILE", raising=False)
        monkeypatch.setattr(main.user_repo, "get_all_users", get_all_users_mock)
        monkeypatch.setattr(main, "_ensure_morning_wake_for_user", ensure_wake_mock)
        monkeypatch.setattr(main, "_reconcile_missing_cron_jobs", reconcile_mock)

        await main.reliability_bootstrap()

        ensure_wake_mock.assert_awaited_once()
        reconcile_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_startup_can_enable_morning_reconcile(self, monkeypatch):
        get_all_users_mock = AsyncMock(return_value=[])
        reconcile_mock = AsyncMock(return_value=None)

        monkeypatch.delenv("ENABLE_RELIABILITY_BOOTSTRAP", raising=False)
        monkeypatch.setenv("ENABLE_MORNING_CRON_RECONCILE", "true")
        monkeypatch.setattr(main.user_repo, "get_all_users", get_all_users_mock)
        monkeypatch.setattr(main, "_reconcile_missing_cron_jobs", reconcile_mock)

        await main.reliability_bootstrap()

        reconcile_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_startup_seeds_only_onboarding_complete_users(self, monkeypatch):
        pending_user = {
            "user_id": "u_pending",
            "onboarding_status": "pending",
            "timezone": "UTC",
            "wake_time": None,
        }
        completed_user = {
            "user_id": "u_complete",
            "onboarding_status": "completed",
            "timezone": "UTC",
            "wake_time": "08:00",
        }

        get_all_users_mock = AsyncMock(return_value=[pending_user, completed_user])
        ensure_wake_mock = AsyncMock(return_value=None)
        reconcile_mock = AsyncMock(return_value=None)

        monkeypatch.delenv("ENABLE_RELIABILITY_BOOTSTRAP", raising=False)
        monkeypatch.delenv("ENABLE_MORNING_CRON_RECONCILE", raising=False)
        monkeypatch.setattr(main.user_repo, "get_all_users", get_all_users_mock)
        monkeypatch.setattr(main, "_ensure_morning_wake_for_user", ensure_wake_mock)
        monkeypatch.setattr(main, "_reconcile_missing_cron_jobs", reconcile_mock)

        await main.reliability_bootstrap()

        ensure_wake_mock.assert_awaited_once_with(completed_user)
