from cellar.safety import sanitize, strip_private_reasoning


def test_sanitize_strips_thoughts_and_enforces_limits() -> None:
    text = "<think>secret\nreasoning</think>\n**hello** there\nsecond line\nthird"
    assert sanitize(text, max_lines=2, max_chars=8) == ["hello th", "second l"]


def test_sanitize_drops_fenced_content() -> None:
    assert sanitize("```python\nbad()\n```\nokay", max_lines=2, max_chars=20) == ["okay"]


def test_sanitize_preserves_url_underscores_and_limits_utf8_bytes() -> None:
    assert sanitize(
        "see https://example.com/some_page", max_lines=1, max_chars=100,
    ) == ["see https://example.com/some_page"]
    result = sanitize("é" * 10, max_lines=1, max_chars=5)
    assert result == ["éé"]
    assert len(result[0].encode()) <= 5


def test_strip_private_reasoning_preserves_summary_layout() -> None:
    assert strip_private_reasoning("<think>private</think>\nUseful\nsummary") == "Useful\nsummary"


def test_strip_private_reasoning_drops_unclosed_thought_and_everything_after_it() -> None:
    assert strip_private_reasoning("Public answer\n<think>private reasoning") == "Public answer"
    assert strip_private_reasoning("<think>private reasoning") == ""
