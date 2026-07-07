from datetime import datetime
from zoneinfo import ZoneInfo


def local_datetime_context(timezone: str, *, now: datetime | None = None) -> str:
    """Format a fresh, DST-aware local date and time for prompt context."""
    local = (now or datetime.now(ZoneInfo(timezone))).astimezone(ZoneInfo(timezone))
    offset = local.strftime("%z")
    formatted_offset = f"{offset[:3]}:{offset[3:]}" if offset else ""
    return (
        f"{local.strftime('%A, %B')} {local.day}, {local.year} at "
        f"{local.strftime('%H:%M')} ({local.tzname()}, UTC{formatted_offset})"
    )
