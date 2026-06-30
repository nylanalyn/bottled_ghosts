from cellar.safety import sanitize


def test_sanitize_strips_thoughts_and_enforces_limits() -> None:
    text = "<think>secret\nreasoning</think>\n**hello** there\nsecond line\nthird"
    assert sanitize(text, max_lines=2, max_chars=8) == ["hello th", "second l"]


def test_sanitize_drops_fenced_content() -> None:
    assert sanitize("```python\nbad()\n```\nokay", max_lines=2, max_chars=20) == ["okay"]
