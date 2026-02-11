"""Timezone strictness regression tests for composio tool helpers."""

from freezegun import freeze_time

from context import current_user_timezone
from voice_agent import composio_tools


def test_parse_iso_preserve_timezone_handles_utc_z():
    parsed = composio_tools.parse_iso_preserve_timezone("2026-02-11T14:00:00Z")
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_parse_iso_preserve_timezone_keeps_offset():
    parsed = composio_tools.parse_iso_preserve_timezone("2026-02-11T14:00:00+05:30")
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 19800


def test_local_day_bounds_are_timezone_aware():
    start, end = composio_tools.local_day_bounds("2026-02-11", "America/New_York")
    assert start.tzinfo is not None
    assert end.tzinfo is not None
    assert start.isoformat().startswith("2026-02-11T00:00:00")
    assert end.isoformat().startswith("2026-02-12T00:00:00")


def test_parse_user_datetime_converts_utc_to_user_timezone():
    parsed = composio_tools._parse_user_datetime(
        "2026-02-11T14:00:00Z",
        "America/New_York",
    )
    assert parsed.hour == 9
    assert parsed.minute == 0


def test_today_in_user_timezone_uses_user_context():
    with freeze_time("2026-02-11 01:30:00+00:00"):
        token = current_user_timezone.set("America/Los_Angeles")
        try:
            assert composio_tools.today_in_user_timezone() == "2026-02-10"
        finally:
            current_user_timezone.reset(token)

        token = current_user_timezone.set("Asia/Tokyo")
        try:
            assert composio_tools.today_in_user_timezone() == "2026-02-11"
        finally:
            current_user_timezone.reset(token)
