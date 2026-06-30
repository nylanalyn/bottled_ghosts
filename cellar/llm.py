import httpx

from cellar.models import LLMProfile


async def complete(profile: LLMProfile, messages: list[dict[str, str]]) -> str:
    headers = {"Authorization": f"Bearer {profile.api_key}"} if profile.api_key else {}
    payload = {"model": profile.model, "messages": messages, "temperature": profile.temperature,
               "max_tokens": profile.max_tokens}
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(profile.endpoint, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    return str(data["choices"][0]["message"]["content"])
