import asyncio
import re
import time

from cellar.irc import truncate_utf8

THINK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"</?think\b[^>]*>", re.IGNORECASE)


def strip_private_reasoning(text: str) -> str:
    text = THINK_RE.sub("", text)
    unclosed = re.search(r"<think\b[^>]*>", text, re.IGNORECASE)
    if unclosed is not None:
        text = text[:unclosed.start()]
    return TAG_RE.sub("", text).strip()


def sanitize(text: str, *, max_lines: int, max_chars: int) -> list[str]:
    text = strip_private_reasoning(text)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    lines: list[str] = []
    for raw in text.splitlines():
        tokens = raw.strip().replace("\r", "").split()
        line = " ".join(
            token if token.startswith(("http://", "https://"))
            else re.sub(r"[*_`~]", "", token)
            for token in tokens
        )
        if line:
            lines.append(truncate_utf8(line[:max_chars], max_chars))
        if len(lines) == max_lines:
            break
    return lines


class Cooldown:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self._last_send = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            delay = self.seconds - (time.monotonic() - self._last_send)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_send = time.monotonic()
