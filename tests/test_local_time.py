from datetime import datetime, timezone

from cellar.local_time import local_datetime_context


def test_local_datetime_context_uses_configured_timezone_and_date() -> None:
    instant = datetime(2026, 7, 7, 18, 30, tzinfo=timezone.utc)

    assert local_datetime_context("America/New_York", now=instant) == (
        "Tuesday, July 7, 2026 at 14:30 (EDT, UTC-04:00)"
    )


def test_local_datetime_context_observes_daylight_saving_time() -> None:
    winter = datetime(2026, 1, 7, 18, 30, tzinfo=timezone.utc)

    assert local_datetime_context("America/New_York", now=winter).endswith(
        "13:30 (EST, UTC-05:00)"
    )
