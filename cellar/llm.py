import asyncio
import logging
import random
from typing import Any

import httpx

from cellar.models import LLMProfile

logger = logging.getLogger(__name__)
# Reasoning models spend hidden tokens before any visible text, and those
# count against max_tokens. When a small budget runs out mid-reasoning the
# reply is empty with finish_reason "length"; one retry with this much extra
# room lets the visible answer through.
REASONING_HEADROOM_TOKENS = 1024


async def _request(profile: LLMProfile, messages: list[dict[str, str]]) -> Any:
    headers = {"Authorization": f"Bearer {profile.api_key}"} if profile.api_key else {}
    payload: dict[str, object] = {
        "model": profile.model, "messages": messages, "temperature": profile.temperature,
        "max_tokens": profile.max_tokens,
    }
    # Only emit penalties when set, so existing default-config Bottles keep their
    # wire shape and providers that ignore these fields are not perturbed.
    if profile.frequency_penalty:
        payload["frequency_penalty"] = profile.frequency_penalty
    if profile.presence_penalty:
        payload["presence_penalty"] = profile.presence_penalty
    async with httpx.AsyncClient(timeout=60) as client:
        for attempt in range(3):
            try:
                response = await client.post(profile.endpoint, headers=headers, json=payload)
            except httpx.TransportError:
                # Connection resets and timeouts are as transient as a 503;
                # the final attempt lets the error propagate to the caller.
                if attempt == 2:
                    raise
            else:
                if response.status_code != 429 and response.status_code < 500:
                    break
                if attempt == 2:
                    break
            await asyncio.sleep((2 ** attempt) + random.uniform(0, 0.25))
        response.raise_for_status()
        return response.json()


def _choice(data: Any) -> tuple[Any, Any]:
    try:
        choice = data["choices"][0]
        return choice["message"]["content"], choice.get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError) as error:
        raise ValueError("LLM response did not contain message content") from error


async def complete(
    profile: LLMProfile, messages: list[dict[str, str]], *,
    reject_truncated: bool = False,
) -> str:
    content, finish_reason = _choice(await _request(profile, messages))
    if (not isinstance(content, str) or not content.strip()) and finish_reason == "length":
        logger.warning(
            "%s used its whole %d-token budget before answering; retrying with %d more",
            profile.model, profile.max_tokens, REASONING_HEADROOM_TOKENS,
        )
        roomier = profile.model_copy(
            update={"max_tokens": profile.max_tokens + REASONING_HEADROOM_TOKENS},
        )
        content, finish_reason = _choice(await _request(roomier, messages))
    if not isinstance(content, str) or not content.strip():
        raise ValueError(
            "LLM response content must be a non-empty string "
            f"(finish_reason={finish_reason!r})"
        )
    if reject_truncated and finish_reason == "length":
        raise ValueError("LLM response was truncated (finish_reason='length')")
    return content
