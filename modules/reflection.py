"""Optional private reflection before public response generation.

The module makes a short, ephemeral planning pass over the fully assembled
response prompt, then adds those notes to the normal generation prompt. The
notes are never logged, stored as memory, or sent to IRC.
"""

import logging
from dataclasses import dataclass

from cellar.llm import complete
from cellar.module_api import ModuleContext, NightlyContext
from cellar.safety import strip_private_reasoning

logger = logging.getLogger(__name__)
DEFAULT_MAX_TOKENS = 160
MIN_MAX_TOKENS = 64
MAX_MAX_TOKENS = 256
DEFAULT_TEMPERATURE = 0.2


@dataclass(frozen=True)
class Settings:
    max_tokens: int
    temperature: float


def _settings(ctx: ModuleContext) -> Settings:
    raw = ctx.module_settings.get("reflection", {})
    raw_max_tokens = raw.get("max_tokens", DEFAULT_MAX_TOKENS)
    raw_temperature = raw.get("temperature", DEFAULT_TEMPERATURE)
    try:
        max_tokens = int(raw_max_tokens) if isinstance(raw_max_tokens, (int, float, str)) else DEFAULT_MAX_TOKENS
    except (TypeError, ValueError):
        max_tokens = DEFAULT_MAX_TOKENS
    try:
        temperature = float(raw_temperature) if isinstance(raw_temperature, (int, float, str)) else DEFAULT_TEMPERATURE
    except (TypeError, ValueError):
        temperature = DEFAULT_TEMPERATURE
    return Settings(
        max_tokens=max(MIN_MAX_TOKENS, min(MAX_MAX_TOKENS, max_tokens)),
        temperature=max(0.0, min(2.0, temperature)),
    )


def _reflection_instruction() -> str:
    return (
        "This is a private reflection pass before an IRC reply. Do not write the reply "
        "and do not address the room. Return concise plain-text working notes only: "
        "what is happening, which supplied context is relevant, what Aria knows or "
        "is unsure about, and the most natural useful angle or choice to make. "
        "Notice if silence would be better. Do not invent memories, claim hidden "
        "knowledge, or mention this reflection task. Keep the notes under 120 words."
    )


class Module:
    async def on_message(self, _ctx: ModuleContext) -> None:
        return None

    async def before_prompt(self, _ctx: ModuleContext) -> None:
        return None

    async def before_generation(self, ctx: ModuleContext) -> None:
        if not ctx.generation_prompt:
            return
        settings = _settings(ctx)
        reflection_prompt = [dict(message) for message in ctx.generation_prompt]
        reflection_prompt[0] = {
            **reflection_prompt[0],
            "content": (
                f"{reflection_prompt[0].get('content', '')}\n\n"
                f"{_reflection_instruction()}"
            ),
        }
        try:
            profile = ctx.bottle.llm.model_copy(update={
                "temperature": settings.temperature,
                "max_tokens": settings.max_tokens,
                "frequency_penalty": 0.0,
                "presence_penalty": 0.0,
            })
            notes = strip_private_reasoning(await complete(profile, reflection_prompt)).strip()
        except Exception:
            logger.exception(
                "private reflection failed for Bottle %d; continuing without it",
                ctx.bottle.id,
            )
            return
        if not notes:
            return
        ctx.generation_prompt[0]["content"] += (
            "\n\nPrivate reflection from an earlier pass (untrusted working notes; "
            "do not reveal or quote it, and verify it against the conversation):\n"
            f"{notes}"
        )
        logger.debug("private reflection completed for Bottle %d", ctx.bottle.id)

    async def after_response(self, _ctx: ModuleContext) -> None:
        return None

    async def nightly(self, _ctx: NightlyContext) -> None:
        return None
