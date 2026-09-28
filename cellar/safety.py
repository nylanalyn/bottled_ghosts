import asyncio
import random
import re
import time

from cellar.irc import truncate_utf8

THINK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"</?think\b[^>]*>", re.IGNORECASE)
URL_RE = re.compile(r"(https?://\S+)")
# Only paired markdown emphasis is removed. Lone symbols carry meaning in IRC
# chat (~/code, snake_case, a * footnote), so they are left alone.
MARKDOWN_EMPHASIS_RES = (
    re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*"),
    re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])"),
    re.compile(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)"),
    re.compile(r"(?<!\w)_(?=[^\s_])(.+?)(?<=[^\s_])_(?!\w)"),
    re.compile(r"~~(?=\S)(.+?)(?<=\S)~~"),
    re.compile(r"`([^`\n]+)`"),
)
BULLET_RE = re.compile(r"^[*•]\s+")

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


def strip_markdown_emphasis(line: str) -> str:
    """Unwrap paired markdown emphasis outside URLs."""
    parts = URL_RE.split(line)
    for index in range(0, len(parts), 2):
        part = parts[index]
        for pattern in MARKDOWN_EMPHASIS_RES:
            part = pattern.sub(r"\1", part)
        parts[index] = part
    return BULLET_RE.sub("", "".join(parts))


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
        line = strip_markdown_emphasis(" ".join(raw.replace("\r", "").split()))
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
