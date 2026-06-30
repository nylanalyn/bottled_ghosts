import json
import re

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
    extraction_profile = profile.model_copy(update={"temperature": 0.0, "max_tokens": 256})
    raw = await complete(extraction_profile, messages)
    cleaned = FENCE_RE.sub("", raw.strip())
    parsed = ExtractedMemories.model_validate(json.loads(cleaned))
    return parsed.candidates
