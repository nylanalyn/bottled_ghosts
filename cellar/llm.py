import asyncio
import random

import httpx

from cellar.models import LLMProfile


async def complete(profile: LLMProfile, messages: list[dict[str, str]]) -> str:
    headers = {"Authorization": f"Bearer {profile.api_key}"} if profile.api_key else {}
    payload = {"model": profile.model, "messages": messages, "temperature": profile.temperature,
               "max_tokens": profile.max_tokens}
    async with httpx.AsyncClient(timeout=60) as client:
        for attempt in range(3):
            response = await client.post(profile.endpoint, headers=headers, json=payload)
            if response.status_code != 429 and response.status_code < 500:
                break
            if attempt == 2:
                break
            await asyncio.sleep((2 ** attempt) + random.uniform(0, 0.25))
        response.raise_for_status()
        data = response.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("LLM response did not contain message content") from error
    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM response content must be a non-empty string")
    return content
