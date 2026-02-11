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
from httpx import ASGITransport, AsyncClient

import main
from auth import AuthUser, get_authenticated_user
from session_manager import ADKSessionManager
from voice_agent import set_timer as set_timer_module


async def _fake_auth_user():
    return AuthUser(user_id="user_test", email="test@example.com", claims={})


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
    def test_select_ws_session_id_uses_requested_when_valid(self):
        selected = main._select_ws_session_id(
            "user_test", "session_user_test_2026-02-06"
        )
        assert selected == "session_user_test_2026-02-06"

    def test_select_ws_session_id_rejects_invalid_or_wrong_user(self):
        fallback = ADKSessionManager.get_daily_session_id("user_test")
        assert (
            main._select_ws_session_id("user_test", "client_random_session")
            == fallback
        )
        assert (
            main._select_ws_session_id(
                "user_test", "session_other_user_2026-02-06"
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
            "get_conversation_mode_config",
            staticmethod(lambda: object()),
        )
        monkeypatch.setattr(
            main.AgentRuntime,
            "run_thinking_mode",
            staticmethod(_empty_thinking_mode),
        )
        monkeypatch.setattr(
            main.user_repo, "get_profile", AsyncMock(return_value={"timezone": "UTC"})
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

        ws = _FakeWebSocket(
            [
                {
                    "text": json.dumps(
                        {
                            "type": "init",
                            "access_token": "jwt_test",
                            "resume_session_id": "session_user_test_2026-02-06",
                            "trigger_type": "checkin",
                            "timezone": "America/New_York",
                        }
                    )
                },
                {"type": "websocket.disconnect"},
            ]
        )

        await main.websocket_endpoint(ws, "client_random_session")

        expected_session_id = "session_user_test_2026-02-06"
        assert ws.accepted is True
        assert fake_session.state["trigger_type"] == "checkin"
        assert fake_session.state["user_timezone"] == "America/New_York"
        get_or_create_mock.assert_awaited_with(
            app_name=main.APP_NAME, user_id="user_test", session_id=expected_session_id
        )

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
            "get_conversation_mode_config",
            staticmethod(lambda: object()),
        )
        monkeypatch.setattr(
            main.AgentRuntime,
            "run_thinking_mode",
            staticmethod(_empty_thinking_mode),
        )
        monkeypatch.setattr(
            main.user_repo, "get_profile", AsyncMock(return_value={"timezone": "UTC"})
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

        await main.websocket_endpoint(ws, "session_user_test_2026-02-06")

        get_or_create_mock.assert_awaited_with(
            app_name=main.APP_NAME,
            user_id="user_test",
            session_id="session_user_test_2026-02-06",
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
            main.user_repo, "get_profile", AsyncMock(return_value={"timezone": "UTC"})
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
            "get_conversation_mode_config",
            staticmethod(lambda: object()),
        )
        monkeypatch.setattr(
            main.AgentRuntime,
            "run_thinking_mode",
            staticmethod(_empty_thinking_mode),
        )
        monkeypatch.setattr(
            main.user_repo, "get_profile", AsyncMock(return_value={"timezone": "UTC"})
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


@pytest.mark.regression
class TestPreferencesEndpoints:
    @pytest.mark.asyncio
    async def test_get_preferences_not_found(self, monkeypatch):
        monkeypatch.setattr(main.user_repo, "get_profile", AsyncMock(return_value=None))
        main.app.dependency_overrides[get_authenticated_user] = _fake_auth_user

        async with AsyncClient(
            transport=ASGITransport(app=main.app),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/preferences/me")
        main.app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "not_found"
        assert body["user_id"] == "user_test"
        assert body["is_complete"] is False

    @pytest.mark.asyncio
    async def test_put_preferences_rejects_invalid_payload(self):
        main.app.dependency_overrides[get_authenticated_user] = _fake_auth_user
        async with AsyncClient(
            transport=ASGITransport(app=main.app),
            base_url="http://test",
        ) as client:
            response = await client.put(
                "/api/preferences/me",
                json={
                    "wake_time": "8am",
                    "bedtime": "99:00",
                    "timezone": "Not/A_Timezone",
                    "health_anchors": [],
                },
            )
        main.app.dependency_overrides.clear()

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "errors" in detail
        assert len(detail["errors"]) >= 1

    @pytest.mark.asyncio
    async def test_put_preferences_saves_and_resyncs_scheduler(self, monkeypatch):
        updated_profile = {
            "user_id": "user_test",
            "wake_time": "07:30",
            "bedtime": "22:15",
            "timezone": "America/New_York",
            "health_anchors": ["sleep", "lunch"],
        }
        update_profile_mock = AsyncMock(return_value=updated_profile)
        resync_mock = AsyncMock(return_value=None)

        monkeypatch.setattr(main.user_repo, "update_profile", update_profile_mock)
        monkeypatch.setattr(main, "_ensure_morning_wake_for_user", resync_mock)
        main.app.dependency_overrides[get_authenticated_user] = _fake_auth_user

        async with AsyncClient(
            transport=ASGITransport(app=main.app),
            base_url="http://test",
        ) as client:
            response = await client.put(
                "/api/preferences/me",
                json={
                    "wake_time": "07:30",
                    "bedtime": "22:15",
                    "timezone": "America/New_York",
                    "health_anchors": ["sleep", "lunch"],
                },
            )
        main.app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["scheduler"]["resynced"] is True
        assert body["preferences"]["wake_time"] == "07:30"
        update_profile_mock.assert_awaited_once()
        resync_mock.assert_awaited_once_with(updated_profile)


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
    async def test_agent_timer_payload_has_agent_ownership(self, monkeypatch):
        set_user_token = set_timer_module.current_user_id.set("user_test")
        set_session_token = set_timer_module.current_session_id.set("session_test")
        create_event_mock = AsyncMock(return_value={"id": "event_checkin_1"})
        create_cron_mock = AsyncMock(return_value=321)
        update_cron_job_id_mock = AsyncMock(return_value=True)
        try:
            monkeypatch.setattr(set_timer_module, "_get_user_timezone", lambda: "UTC")
            monkeypatch.setattr(set_timer_module.event_repo, "create_event", create_event_mock)
            monkeypatch.setattr(set_timer_module.cron_service, "create_one_time_job", create_cron_mock)
            monkeypatch.setattr(set_timer_module.event_repo, "update_cron_job_id", update_cron_job_id_mock)

            response = await set_timer_module.set_checkin_timer(30, "deep_work_end")
            assert "Timer set for" in response

            payload = create_event_mock.await_args.kwargs["payload"]
            assert payload["schedule_owner"] == "agent"
            assert payload["schedule_policy"] == "autonomous_checkin"
        finally:
            set_timer_module.current_user_id.reset(set_user_token)
            set_timer_module.current_session_id.reset(set_session_token)
