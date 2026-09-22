"""Keep short-lived game traffic in chat without feeding it to long-term summaries."""

_GAME_EVENTS = ("[fishing]", "[hunt]")


def is_archive_noise(body: str) -> bool:
    text = body.lstrip().casefold()
    return text.startswith(_GAME_EVENTS) or (
        len(text) > 1 and text[0] == "!" and text[1].isalnum()
    )
