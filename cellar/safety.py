import asyncio
import random
import re
import time

from cellar.irc import truncate_utf8

THINK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"</?think\b[^>]*>", re.IGNORECASE)

# Length-proportional send pacing so replies do not land instantly after the
# listening window closes. Zero cap disables the pause; the test suite pins it
# to zero via an autouse fixture so runtime tests stay instant.
TYPING_BASE_SECONDS = 0.6
TYPING_CHARS_PER_SECOND = 45.0
TYPING_CAP_SECONDS = 3.0
TYPING_JITTER = 0.25


def typing_seconds(text: str) -> float:
    """Seconds a reply plausibly took to read and type. Zero when disabled."""
    estimate = min(
        TYPING_BASE_SECONDS + len(text) / TYPING_CHARS_PER_SECOND,
        TYPING_CAP_SECONDS,
    )
    return max(0.0, estimate) * random.uniform(1.0 - TYPING_JITTER, 1.0 + TYPING_JITTER)


async def typing_pause(text: str) -> None:
    seconds = typing_seconds(text)
    if seconds > 0:
        await asyncio.sleep(seconds)


def strip_private_reasoning(text: str) -> str:
    text = THINK_RE.sub("", text)
    unclosed = re.search(r"<think\b[^>]*>", text, re.IGNORECASE)
    if unclosed is not None:
        text = text[:unclosed.start()]
    return TAG_RE.sub("", text).strip()


def sanitize(
    text: str, *, max_lines: int, max_chars: int, bot_nick: str | None = None,
) -> list[str]:
    text = strip_private_reasoning(text)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    nick_prefix = (
        re.compile(rf"^\s*<{re.escape(bot_nick)}>\s*", re.IGNORECASE)
        if bot_nick else None
    )
    lines: list[str] = []
    for raw in text.splitlines():
        if nick_prefix is not None:
            raw = nick_prefix.sub("", raw, count=1)
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
