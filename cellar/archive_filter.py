"""Keep short-lived game traffic in chat without feeding it to long-term summaries."""

import re

_GAME_EVENTS = ("[fishing]", "[hunt]", "[banter]", "[karma]")
_PASTED_COMMAND_RE = re.compile(r"^(?:\[[^]]+\]\s+)?<[^>]+>\s+![a-z0-9]", re.IGNORECASE)


def is_archive_noise(body: str) -> bool:
    text = body.lstrip().casefold()
    return any(marker in text for marker in _GAME_EVENTS) or (
        len(text) > 1 and text[0] == "!" and text[1].isalnum()
    ) or bool(_PASTED_COMMAND_RE.match(text))
