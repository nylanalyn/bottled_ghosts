import pytest

from cellar import safety


@pytest.fixture(autouse=True)
def _instant_typing_pace(monkeypatch):
    """Disable send pacing so runtime tests do not sleep for it.

    Tests that exercise pacing explicitly re-pin these constants themselves.
    """
    monkeypatch.setattr(safety, "TYPING_CAP_SECONDS", 0.0)
