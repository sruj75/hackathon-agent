"""API endpoint tests for current backend."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

import main
from auth import AuthUser, get_authenticated_user


pytestmark = pytest.mark.integration


@pytest.fixture
async def api_client():
    async def _fake_user():
        return AuthUser(user_id="user_test", email="test@example.com", claims={})

    main.app.dependency_overrides[get_authenticated_user] = _fake_user
    async with AsyncClient(
        transport=ASGITransport(app=main.app),
        base_url="http://test",
    ) as client:
        yield client
    main.app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_root_endpoint(api_client):
    response = await api_client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "app" in body
    assert "agent" in body


@pytest.mark.asyncio
async def test_health_endpoint(api_client):
    response = await api_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


@pytest.mark.asyncio
async def test_save_token_success(api_client, monkeypatch):
    save_push_token_mock = AsyncMock(return_value={"status": "saved"})
    monkeypatch.setattr(main.user_repo, "save_push_token", save_push_token_mock)

    response = await api_client.post(
        "/api/save-token",
        json={
            "token": "ExponentPushToken[abc123]",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "saved", "user_id": "user_test"}
    save_push_token_mock.assert_awaited_once_with("user_test", "ExponentPushToken[abc123]")


@pytest.mark.asyncio
async def test_save_token_rejects_invalid_payload(api_client):
    missing_field = await api_client.post("/api/save-token", json={})
    assert missing_field.status_code == 400
    assert missing_field.json()["detail"] == "Missing token"

    invalid_format = await api_client.post(
        "/api/save-token",
        json={"token": "not-an-expo-token"},
    )
    assert invalid_format.status_code == 400
    assert invalid_format.json()["detail"] == "Invalid token format"


@pytest.mark.asyncio
async def test_execute_event_not_found(api_client, monkeypatch):
    monkeypatch.setattr(main.event_repo, "get_by_id", AsyncMock(return_value=None))

    response = await api_client.post("/api/execute-event/event_missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "Event not found"


@pytest.mark.asyncio
async def test_execute_event_already_executed(api_client, monkeypatch):
    monkeypatch.setattr(
        main.event_repo,
        "get_by_id",
        AsyncMock(return_value={"id": "event_done", "executed": True}),
    )

    response = await api_client.post("/api/execute-event/event_done")

    assert response.status_code == 200
    assert response.json() == {"status": "already_executed"}


@pytest.mark.asyncio
async def test_execute_event_processes_and_cleans_up(api_client, monkeypatch):
    event = {
        "id": "event_123",
        "user_id": "user_test",
        "event_type": "checkin",
        "payload": {"reason": "deep_work"},
        "executed": False,
        "cron_job_id": 98765,
    }

    get_event_mock = AsyncMock(return_value=event)
    update_event_mock = AsyncMock(return_value=True)
    delete_job_mock = AsyncMock(return_value=True)
    send_push_mock = AsyncMock(return_value=True)

    monkeypatch.setattr(main.event_repo, "get_by_id", get_event_mock)
    monkeypatch.setattr(main.event_repo, "update_event", update_event_mock)
    monkeypatch.setattr(main.cron_service, "delete_job", delete_job_mock)
    monkeypatch.setattr(main, "send_push_notification", send_push_mock)

    response = await api_client.post("/api/execute-event/event_123")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "executed"
    assert body["push_sent"] is True
    update_event_mock.assert_awaited_once()
    update_kwargs = update_event_mock.await_args.kwargs
    assert update_kwargs["executed"] is True
    assert update_kwargs["last_error"] is None
    delete_job_mock.assert_awaited_once_with(98765)
    send_push_mock.assert_awaited_once()
    push_args = send_push_mock.await_args.args
    assert push_args[0] == "user_test"
    assert push_args[1] == "Check-in"
    assert push_args[3]["session_id"].startswith("session_user_test_")
    assert push_args[3]["type"] == "checkin"


@pytest.mark.asyncio
async def test_execute_event_failure_marks_last_error(api_client, monkeypatch):
    event = {
        "id": "event_retry",
        "user_id": "user_test",
        "event_type": "calendar_reminder",
        "payload": {
            "event_title": "Standup",
            "event_start_time": "2099-01-01T09:00:00+00:00",
            "timezone": "UTC",
            "calendar_event_id": "gcal_99",
        },
        "executed": False,
        "cron_job_id": 11111,
    }

    get_event_mock = AsyncMock(return_value=event)
    update_event_mock = AsyncMock(return_value=True)
    delete_job_mock = AsyncMock(return_value=True)
    send_push_mock = AsyncMock(return_value=False)

    monkeypatch.setattr(main.event_repo, "get_by_id", get_event_mock)
    monkeypatch.setattr(main.event_repo, "update_event", update_event_mock)
    monkeypatch.setattr(main.cron_service, "delete_job", delete_job_mock)
    monkeypatch.setattr(main, "send_push_notification", send_push_mock)

    response = await api_client.post("/api/execute-event/event_retry")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "executed"
    assert body["push_sent"] is False

    update_event_mock.assert_awaited_once()
    update_args = update_event_mock.await_args
    assert update_args.args[0] == "event_retry"
    assert update_args.kwargs["executed"] is True
    assert update_args.kwargs["last_error"] == "push_failed_or_missing_token"
    delete_job_mock.assert_awaited_once_with(11111)


@pytest.mark.asyncio
async def test_execute_calendar_reminder_missing_timezone_skips_push(api_client, monkeypatch):
    event = {
        "id": "event_missing_tz",
        "user_id": "user_test",
        "event_type": "calendar_reminder",
        "payload": {
            "event_title": "Standup",
            "event_start_time": "2099-01-01T09:00:00+00:00",
            "calendar_event_id": "gcal_99",
        },
        "executed": False,
        "cron_job_id": 22222,
    }

    get_event_mock = AsyncMock(return_value=event)
    get_profile_mock = AsyncMock(return_value=None)
    update_event_mock = AsyncMock(return_value=True)
    delete_job_mock = AsyncMock(return_value=True)
    send_push_mock = AsyncMock(return_value=True)

    monkeypatch.setattr(main.event_repo, "get_by_id", get_event_mock)
    monkeypatch.setattr(main.user_repo, "get_profile", get_profile_mock)
    monkeypatch.setattr(main.event_repo, "update_event", update_event_mock)
    monkeypatch.setattr(main.cron_service, "delete_job", delete_job_mock)
    monkeypatch.setattr(main, "send_push_notification", send_push_mock)

    response = await api_client.post("/api/execute-event/event_missing_tz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "executed"
    assert body["push_sent"] is False
    send_push_mock.assert_not_awaited()
    update_event_mock.assert_awaited_once()
    assert update_event_mock.await_args.kwargs["last_error"] == "missing_timezone"
    delete_job_mock.assert_awaited_once_with(22222)


@pytest.mark.asyncio
async def test_get_preferences_not_found(api_client, monkeypatch):
    monkeypatch.setattr(main.user_repo, "get_profile", AsyncMock(return_value=None))

    response = await api_client.get("/api/preferences/me")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "not_found"
    assert body["is_complete"] is False


@pytest.mark.asyncio
async def test_put_preferences_validates_input(api_client):
    response = await api_client.put(
        "/api/preferences/me",
        json={
            "wake_time": "bad",
            "bedtime": "bad",
            "timezone": "Not/A_Timezone",
            "health_anchors": [],
        },
    )

    assert response.status_code == 400
    errors = response.json()["detail"]["errors"]
    assert any("wake_time" in err for err in errors)


@pytest.mark.asyncio
async def test_put_preferences_updates_profile_and_resyncs_scheduler(api_client, monkeypatch):
    updated_profile = {
        "user_id": "user_test",
        "wake_time": "07:30",
        "bedtime": "22:15",
        "timezone": "America/New_York",
        "health_anchors": ["sleep"],
    }
    update_profile_mock = AsyncMock(return_value=updated_profile)
    resync_mock = AsyncMock(return_value=None)

    monkeypatch.setattr(main.user_repo, "update_profile", update_profile_mock)
    monkeypatch.setattr(main, "_ensure_morning_wake_for_user", resync_mock)

    response = await api_client.put(
        "/api/preferences/me",
        json={
            "wake_time": "07:30",
            "bedtime": "22:15",
            "timezone": "America/New_York",
            "health_anchors": ["sleep"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["scheduler"]["resynced"] is True
    update_profile_mock.assert_awaited_once()
    resync_mock.assert_awaited_once_with(updated_profile)


@pytest.mark.asyncio
async def test_put_preferences_does_not_overwrite_anchors_when_omitted(
    api_client, monkeypatch
):
    updated_profile = {
        "user_id": "user_test",
        "wake_time": "06:30",
        "bedtime": "22:00",
        "timezone": "UTC",
        "health_anchors": ["sleep", "exercise"],
    }
    update_profile_mock = AsyncMock(return_value=updated_profile)
    resync_mock = AsyncMock(return_value=None)

    monkeypatch.setattr(main.user_repo, "update_profile", update_profile_mock)
    monkeypatch.setattr(main, "_ensure_morning_wake_for_user", resync_mock)

    response = await api_client.put(
        "/api/preferences/me",
        json={
            "wake_time": "06:30",
            "bedtime": "22:00",
            "timezone": "UTC",
        },
    )

    assert response.status_code == 200
    update_profile_mock.assert_awaited_once()
    update_args = update_profile_mock.await_args
    assert update_args.args[0] == "user_test"
    assert "health_anchors" not in update_args.kwargs


@pytest.mark.asyncio
async def test_composio_connect_link_returns_all_integration_apps(api_client, monkeypatch):
    class _FakeEntity:
        def __init__(self):
            self.calls = []

        def initiate_connection(self, app_name, redirect_url):
            self.calls.append((app_name, redirect_url))
            return SimpleNamespace(
                connectionStatus="INITIATED",
                connectedAccountId=f"conn_{app_name}",
                redirectUrl=redirect_url,
            )

    class _FakeClient:
        def __init__(self, entity):
            self._entity = entity

        def get_entity(self, _user_id):
            return self._entity

    fake_entity = _FakeEntity()
    monkeypatch.setenv("COMPOSIO_REQUIRED_APPS", "not-used")
    monkeypatch.setattr(main, "_get_composio_client", lambda: _FakeClient(fake_entity))

    response = await api_client.post(
        "/api/integrations/composio/connect-link",
        json={"redirect_url": "https://example.com/callback"},
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["app"] for item in body["links"]] == list(main.COMPOSIO_INTEGRATION_APPS)
    assert fake_entity.calls == [
        ("googlecalendar", "https://example.com/callback"),
        ("googletasks", "https://example.com/callback"),
    ]


@pytest.mark.asyncio
async def test_composio_connect_link_rejects_app_selection(api_client, monkeypatch):
    def _unexpected_client_call():
        raise AssertionError("Composio client should not be used when app is provided")

    monkeypatch.setattr(main, "_get_composio_client", _unexpected_client_call)

    response = await api_client.post(
        "/api/integrations/composio/connect-link",
        json={"app": "googlecalendar"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "app selection is not supported"


@pytest.mark.asyncio
async def test_composio_status_reports_all_connected(api_client, monkeypatch):
    class _FakeEntity:
        def get_connections(self):
            return [
                SimpleNamespace(appName="googlecalendar", status="ACTIVE", id="calendar_1"),
                SimpleNamespace(appName="googletasks", status="ACTIVE", id="tasks_1"),
            ]

    class _FakeClient:
        def get_entity(self, _user_id):
            return _FakeEntity()

    monkeypatch.setattr(main, "_get_composio_client", lambda: _FakeClient())

    response = await api_client.get("/api/integrations/composio/status")

    assert response.status_code == 200
    body = response.json()
    assert [item["app"] for item in body["apps"]] == list(main.COMPOSIO_INTEGRATION_APPS)
    assert body["all_connected"] is True
    assert all(item["connected"] for item in body["apps"])


@pytest.mark.asyncio
async def test_composio_status_reports_missing_integration(api_client, monkeypatch):
    class _FakeEntity:
        def get_connections(self):
            return [
                SimpleNamespace(appName="googlecalendar", status="ACTIVE", id="calendar_1"),
            ]

    class _FakeClient:
        def get_entity(self, _user_id):
            return _FakeEntity()

    monkeypatch.setattr(main, "_get_composio_client", lambda: _FakeClient())

    response = await api_client.get("/api/integrations/composio/status")

    assert response.status_code == 200
    body = response.json()
    assert body["all_connected"] is False
    assert [item["app"] for item in body["apps"]] == list(main.COMPOSIO_INTEGRATION_APPS)
    assert body["apps"][0]["connected"] is True
    assert body["apps"][1]["connected"] is False
    assert body["apps"][1]["status"] == "NOT_CONNECTED"
    assert body["apps"][1]["connected_account_id"] is None


@pytest.mark.asyncio
async def test_onboarding_bootstrap_routes_to_assistant_when_complete(
    api_client, monkeypatch
):
    profile = {
        "user_id": "user_test",
        "wake_time": "07:00",
        "bedtime": "22:30",
        "timezone": "UTC",
        "onboarding_status": "completed",
        "onboarding_completed_at": "2026-01-01T00:00:00+00:00",
    }

    class _FakeEntity:
        def get_connections(self):
            return [
                SimpleNamespace(appName="googlecalendar", status="ACTIVE", id="calendar_1"),
                SimpleNamespace(appName="googletasks", status="ACTIVE", id="tasks_1"),
            ]

    class _FakeClient:
        def get_entity(self, _user_id):
            return _FakeEntity()

    monkeypatch.setattr(main.user_repo, "get_profile", AsyncMock(return_value=profile))
    monkeypatch.setattr(main, "_get_composio_client", lambda: _FakeClient())

    response = await api_client.get("/api/onboarding/bootstrap")

    assert response.status_code == 200
    body = response.json()
    assert body["all_connected"] is True
    assert body["onboarding_status"] == "completed"
    assert body["route_hint"] == "assistant"
    assert body["onboarding_session_id"] == "session_onboarding_user_test"
    assert body["profile_exists"] is True


@pytest.mark.asyncio
async def test_onboarding_bootstrap_creates_pending_profile_and_connect_flow(
    api_client, monkeypatch
):
    update_profile_mock = AsyncMock(
        return_value={
            "user_id": "user_test",
            "wake_time": None,
            "bedtime": None,
            "timezone": None,
            "onboarding_status": "pending",
            "onboarding_completed_at": None,
        }
    )

    class _FakeEntity:
        def get_connections(self):
            return []

    class _FakeClient:
        def get_entity(self, _user_id):
            return _FakeEntity()

    monkeypatch.setattr(main.user_repo, "get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(main.user_repo, "update_profile", update_profile_mock)
    monkeypatch.setattr(main, "_get_composio_client", lambda: _FakeClient())

    response = await api_client.get("/api/onboarding/bootstrap")

    assert response.status_code == 200
    body = response.json()
    assert body["profile_exists"] is False
    assert body["all_connected"] is False
    assert body["route_hint"] == "connect_flow"
    assert body["onboarding_status"] == "pending"
    assert body["onboarding_session_id"] == "session_onboarding_user_test"
    update_profile_mock.assert_awaited_once_with(
        "user_test",
        onboarding_status=main.ONBOARDING_STATUS_PENDING,
    )


@pytest.mark.asyncio
async def test_onboarding_bootstrap_routes_to_placeholder_when_pending(
    api_client, monkeypatch
):
    profile = {
        "user_id": "user_test",
        "wake_time": None,
        "bedtime": None,
        "timezone": None,
        "onboarding_status": "pending",
        "onboarding_completed_at": None,
    }

    class _FakeEntity:
        def get_connections(self):
            return [
                SimpleNamespace(appName="googlecalendar", status="ACTIVE", id="calendar_1"),
                SimpleNamespace(appName="googletasks", status="ACTIVE", id="tasks_1"),
            ]

    class _FakeClient:
        def get_entity(self, _user_id):
            return _FakeEntity()

    monkeypatch.setattr(main.user_repo, "get_profile", AsyncMock(return_value=profile))
    monkeypatch.setattr(main, "_get_composio_client", lambda: _FakeClient())

    response = await api_client.get("/api/onboarding/bootstrap")

    assert response.status_code == 200
    body = response.json()
    assert body["all_connected"] is True
    assert body["route_hint"] == "onboarding"
    assert body["onboarding_status"] == "pending"
    assert body["onboarding_session_id"] == "session_onboarding_user_test"


def test_onboarding_session_id_selection():
    assert (
        main._select_onboarding_session_id("user_test", None)
        == "session_onboarding_user_test"
    )
    assert (
        main._select_onboarding_session_id(
            "user_test",
            "session_onboarding_user_test",
        )
        == "session_onboarding_user_test"
    )
    # Wrong user should be forced to deterministic onboarding session id.
    assert (
        main._select_onboarding_session_id(
            "user_test",
            "session_onboarding_other_user",
        )
        == "session_onboarding_user_test"
    )


@pytest.mark.asyncio
async def test_complete_onboarding_marks_completed_and_resyncs(api_client, monkeypatch):
    completion_profile = {
        "user_id": "user_test",
        "wake_time": "07:30",
        "bedtime": "22:15",
        "timezone": "America/New_York",
        "health_anchors": ["sleep"],
        "onboarding_status": "completed",
        "onboarding_completed_at": "2026-01-01T00:00:00+00:00",
    }
    complete_onboarding_for_user_mock = AsyncMock(
        return_value=(completion_profile, datetime(2026, 1, 1, tzinfo=timezone.utc))
    )
    resync_mock = AsyncMock(return_value=None)
    composio_status_mock = AsyncMock(
        return_value={"apps": [], "all_connected": True}
    )

    monkeypatch.setattr(
        main,
        "complete_onboarding_for_user",
        complete_onboarding_for_user_mock,
    )
    monkeypatch.setattr(main, "_ensure_morning_wake_for_user", resync_mock)
    monkeypatch.setattr(main, "_get_composio_status_payload", composio_status_mock)

    response = await api_client.post(
        "/api/onboarding/complete",
        json={
            "wake_time": "07:30",
            "bedtime": "22:15",
            "timezone": "America/New_York",
            "health_anchors": ["sleep"],
            "playbook": {"cadence": "daily"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["onboarding_status"] == "completed"
    assert body["route_hint"] == "assistant"
    complete_onboarding_for_user_mock.assert_awaited_once_with(
        "user_test",
        wake_time="07:30",
        bedtime="22:15",
        timezone_name="America/New_York",
        playbook={"cadence": "daily"},
        health_anchors=["sleep"],
    )
    resync_mock.assert_awaited_once_with(completion_profile)


@pytest.mark.asyncio
async def test_complete_onboarding_validates_playbook(api_client):
    response = await api_client.post(
        "/api/onboarding/complete",
        json={
            "wake_time": "07:30",
            "bedtime": "22:15",
            "playbook": "not-a-json-object",
        },
    )

    assert response.status_code == 400
    errors = response.json()["detail"]["errors"]
    assert "playbook must be a JSON object" in errors
