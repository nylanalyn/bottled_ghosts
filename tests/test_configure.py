import pytest

from cellar.configure import ask, ask_bool


def test_ask_uses_default(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    assert ask("Nickname", "ghost") == "ghost"


def test_ask_rejects_missing_required_value(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    with pytest.raises(ValueError, match="Bottle name is required"):
        ask("Bottle name")


def test_ask_bool_rejects_ambiguous_value(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "perhaps")
    with pytest.raises(ValueError, match="must be yes or no"):
        ask_bool("Use TLS", True)
