import json
import logging
import re

from pydantic import ValidationError

from cellar.irc import IRC_NICK_CHARACTERS, irc_casefold, mentions_any_nick
from cellar.llm import complete
from cellar.models import ExtractedMemories, ExtractedMemory, LLMProfile

FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
BOT_ADDRESS_MARKER = "[direct address to the bot]"
logger = logging.getLogger(__name__)


async def extract_candidates(
    profile: LLMProfile, *, speaker: str, body: str,
    bot_names: tuple[str, ...] = (),
) -> list[ExtractedMemory]:
    extraction_body = _mark_bot_addresses(body, bot_names)
    messages = [
        {
            "role": "system",
            "content": (
                "Extract up to 3 durable memory candidates from one IRC message. "
                "Allowed types: preference, project, relationship, identity, temporary_state. "
                "Do not infer sensitive traits. Do not treat guesses as facts. "
                f"The marker {BOT_ADDRESS_MARKER} replaces a name used to address the "
                "listening bot. Treat it only as a vocative, even when the speaker omitted "
                "punctuation. Never use it as the name of a person, animal, place, or thing, "
                "and never include the marker in a candidate. "
                "Return only JSON in this exact shape: "
                '{"candidates":[{"text":"...","type":"preference","confidence":0.8}]}. '
                "Use an empty candidates list when nothing should be remembered."
            ),
        },
        {"role": "user", "content": f"Speaker: {speaker}\nMessage: {extraction_body}"},
    ]
    extraction_profile = profile.model_copy(update={
        "temperature": 0.0, "max_tokens": 512,
        # extraction must stay deterministic; penalties are for chat variety
        "frequency_penalty": 0.0, "presence_penalty": 0.0,
    })
    raw = await complete(extraction_profile, messages)
    try:
        parsed = _parse_extraction(raw)
    except (json.JSONDecodeError, ValidationError):
        # Cloud and reasoning models occasionally truncate or decorate an otherwise
        # valid JSON response. Retry once with enough room to finish; the runtime
        # will log and skip the message if the second response is still invalid.
        retry_profile = extraction_profile.model_copy(update={"max_tokens": 1024})
        parsed = _parse_extraction(await complete(retry_profile, messages))
    # A model that ignores the vocative instruction must fail closed. Keeping a
    # smaller set of facts is preferable to teaching a Bottle that it, a pet, or
    # a relative has the Bottle's own name.
    candidates = [
        candidate for candidate in parsed.candidates
        if not mentions_any_nick(candidate.text, bot_names)
        and BOT_ADDRESS_MARKER.casefold() not in candidate.text.casefold()
    ]
    rejected_count = len(parsed.candidates) - len(candidates)
    if rejected_count:
        logger.warning(
            "discarded %d memory candidate(s) containing a bot address name",
            rejected_count,
        )
    return candidates


def _mark_bot_addresses(body: str, bot_names: tuple[str, ...]) -> str:
    """Replace standalone bot address names while retaining sentence structure."""
    folded_body = irc_casefold(body)
    spans: list[tuple[int, int]] = []
    unique_names = {
        irc_casefold(name.strip()) for name in bot_names if name.strip()
    }
    for name in sorted(unique_names, key=len, reverse=True):
        pattern = re.compile(
            rf"(?<![{IRC_NICK_CHARACTERS}]){re.escape(name)}"
            rf"(?![{IRC_NICK_CHARACTERS}])"
        )
        for match in pattern.finditer(folded_body):
            if not any(match.start() < end and match.end() > start for start, end in spans):
                spans.append(match.span())
    if not spans:
        return body
    marked = body
    for start, end in sorted(spans, reverse=True):
        marked = f"{marked[:start]}{BOT_ADDRESS_MARKER}{marked[end:]}"
    return marked


def _parse_extraction(raw: str) -> ExtractedMemories:
    cleaned = FENCE_RE.sub("", raw.strip())
    return ExtractedMemories.model_validate(json.loads(cleaned))
