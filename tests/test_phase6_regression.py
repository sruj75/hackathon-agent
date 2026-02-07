"""
Phase 6 regression tests.

Ensures WebSocket session resumption/init handshake and generative UI
forwarding still work while keeping older behavior intact.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import main
from session_manager import ADKSessionManager


class _FakeContent:
    def __init__(self, parts=None):
        self.parts = parts or []


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

    def send_content(self, _content):
        return None

    def send_realtime(self, _blob):
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


async def _empty_thinking_mode(*_args, **_kwargs):
    if False:  # pragma: no cover - keeps this as an async generator
        yield None


@pytest.mark.regression
class TestPhase6WebSocketFlow:
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
            "get_conversation_mode_config",
            staticmethod(lambda: object()),
        )
        monkeypatch.setattr(
            main.AgentRuntime,
            "run_thinking_mode",
            staticmethod(_empty_thinking_mode),
        )
        monkeypatch.setattr(main.session_manager, "get_or_create_session", get_or_create_mock)
        monkeypatch.setattr(
            main.session_manager, "save_agent_session_to_db", AsyncMock(return_value=True)
        )

        async def fake_run_live(**_kwargs):
            if False:  # pragma: no cover
                yield None

        monkeypatch.setattr(main.runner, "run_live", fake_run_live)

        ws = _FakeWebSocket(
            [
                {
                    "text": json.dumps(
                        {
                            "type": "init",
                            "resume_session_id": "session_user_test_2026-02-06",
                            "trigger_type": "checkin",
                        }
                    )
                },
                {"type": "websocket.disconnect"},
            ]
        )

        await main.websocket_endpoint(ws, "user_test", "client_random_session")

        expected_session_id = ADKSessionManager.get_daily_session_id("user_test")
        assert ws.accepted is True
        assert fake_session.state["trigger_type"] == "checkin"
        get_or_create_mock.assert_awaited_with(
            app_name=main.APP_NAME, user_id="user_test", session_id=expected_session_id
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
            "get_conversation_mode_config",
            staticmethod(lambda: object()),
        )
        monkeypatch.setattr(
            main.AgentRuntime,
            "run_thinking_mode",
            staticmethod(_empty_thinking_mode),
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

        ws = _FakeWebSocket(
            [
                {
                    "text": json.dumps(
                        {
                            "type": "init",
                            "resume_session_id": "session_a",
                            "trigger_type": "morning_wake",
                        }
                    )
                },
                {
                    "text": json.dumps(
                        {
                            "type": "init",
                            "resume_session_id": "session_b",
                            "trigger_type": "checkin",
                        }
                    )
                },
                {"type": "websocket.disconnect"},
            ]
        )

        await main.websocket_endpoint(ws, "user_test", "client_random_session")

        # Regression check: a second init should be ignored.
        assert fake_session.state["trigger_type"] == "morning_wake"

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
            "get_conversation_mode_config",
            staticmethod(lambda: object()),
        )
        monkeypatch.setattr(
            main.AgentRuntime,
            "run_thinking_mode",
            staticmethod(_empty_thinking_mode),
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

        ws = _FakeWebSocket([{"type": "websocket.disconnect"}])

        await main.websocket_endpoint(ws, "user_test", "client_random_session")

        parsed_messages = [json.loads(message) for message in ws.sent_texts]
        assert any(
            message.get("type") == "generative_ui"
            and message.get("component") == "day_view"
            and "tasks" in message.get("props", {})
            for message in parsed_messages
        )
        assert any(message.get("turnComplete") is True for message in parsed_messages)
        assert save_session_mock.await_count >= 1
