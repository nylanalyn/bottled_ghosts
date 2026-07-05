import json
import re

from pydantic import ValidationError

from cellar.llm import complete
from cellar.models import ExtractedMemories, ExtractedMemory, LLMProfile

FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


async def extract_candidates(
    profile: LLMProfile, *, speaker: str, body: str
) -> list[ExtractedMemory]:
    messages = [
        {
            "role": "system",
            "content": (
                "Extract up to 3 durable memory candidates from one IRC message. "
                "Allowed types: preference, project, relationship, identity, temporary_state. "
                "Do not infer sensitive traits. Do not treat guesses as facts. "
                "Return only JSON in this exact shape: "
                '{"candidates":[{"text":"...","type":"preference","confidence":0.8}]}. '
                "Use an empty candidates list when nothing should be remembered."
            ),
        },
        {"role": "user", "content": f"Speaker: {speaker}\nMessage: {body}"},
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
    return parsed.candidates


def _parse_extraction(raw: str) -> ExtractedMemories:
    cleaned = FENCE_RE.sub("", raw.strip())
    return ExtractedMemories.model_validate(json.loads(cleaned))
