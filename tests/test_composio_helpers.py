"""Unit tests for composio helper behavior that does not require live APIs."""

from __future__ import annotations

import pytest

from context import current_user_id, current_user_timezone
from voice_agent import composio_tools


pytestmark = pytest.mark.unit


def test_parse_iso_preserve_timezone_rejects_blank_value():
    with pytest.raises(ValueError, match="invalid_datetime"):
        composio_tools.parse_iso_preserve_timezone(" ")


def test_extract_and_inject_event_id_round_trip():
    notes = "Focus block\nRemember to hydrate."
    injected = composio_tools.inject_event_id(notes, "event_123")
    assert "__INTENTIVE_META__:event_id=event_123" in injected
    assert composio_tools.extract_event_id(injected) == "event_123"


def test_inject_event_id_avoids_duplicate_metadata():
    notes = "Task notes\n__INTENTIVE_META__:event_id=event_abc"
    assert composio_tools.inject_event_id(notes, "event_other") == notes


def test_get_user_timezone_prefers_context_and_updates_cache(monkeypatch):
    monkeypatch.setattr(composio_tools, "_user_timezone_by_user", {})
    user_token = current_user_id.set("user_1")
    tz_token = current_user_timezone.set("America/New_York")
    try:
        resolved = composio_tools.require_user_timezone()
        assert resolved == "America/New_York"
        assert composio_tools._user_timezone_by_user["user_1"] == "America/New_York"
    finally:
        current_user_id.reset(user_token)
        current_user_timezone.reset(tz_token)


def test_get_user_timezone_uses_cache_when_context_timezone_missing(monkeypatch):
    monkeypatch.setattr(composio_tools, "_user_timezone_by_user", {"user_1": "UTC"})
    user_token = current_user_id.set("user_1")
    tz_token = current_user_timezone.set(None)
    try:
        assert composio_tools.require_user_timezone() == "UTC"
    finally:
        current_user_id.reset(user_token)
        current_user_timezone.reset(tz_token)


def test_get_user_timezone_raises_when_missing_context_and_cache(monkeypatch):
    monkeypatch.setattr(composio_tools, "_user_timezone_by_user", {})
    user_token = current_user_id.set("user_2")
    tz_token = current_user_timezone.set(None)
    try:
        with pytest.raises(ValueError, match="missing_timezone"):
            composio_tools.require_user_timezone()
    finally:
        current_user_id.reset(user_token)
        current_user_timezone.reset(tz_token)
