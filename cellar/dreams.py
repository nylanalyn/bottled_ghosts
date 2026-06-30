import logging

import aiosqlite

from cellar.dream_store import dream_window, messages_for_dream, store_dream
from cellar.llm import complete
from cellar.models import Bottle, DreamSummary
from cellar.module_api import NightlyContext
from cellar.module_loader import load_modules
from cellar.prompt import read_soul
from cellar.safety import strip_private_reasoning

logger = logging.getLogger(__name__)


async def run_dream(
    db: aiosqlite.Connection, *, bottle: Bottle, hours: int = 24
) -> DreamSummary | None:
    if hours < 1:
        raise ValueError("dream period must be at least one hour")
    period_start, period_end = await dream_window(db, hours=hours)
    messages = await messages_for_dream(
        db, bot_id=bottle.id, period_start=period_start, period_end=period_end,
    )
    if not messages:
        logger.info("no messages to dream about for Bottle %d (%s)", bottle.id, bottle.name)
        return None
    transcript = "\n".join(
        f"[{timestamp}] {channel} <{speaker}> {body[:500]}"
        for timestamp, channel, speaker, body in messages
    )
    prompt = [
        {
            "role": "system",
            "content": (
                "Summarize this IRC period in the character's voice. Preserve notable events, "
                "ongoing projects, and useful continuity. Do not invent facts. Do not use "
                f"private reasoning outside <think> tags.\n\nCharacter:\n{read_soul(bottle.soul_prompt_path)}"
            ),
        },
        {"role": "user", "content": transcript},
    ]
    profile = bottle.llm.model_copy(update={"temperature": 0.3, "max_tokens": 500})
    summary_text = strip_private_reasoning(await complete(profile, prompt))
    if not summary_text:
        raise ValueError("dream summary was empty after removing private reasoning")
    summary = await store_dream(
        db, bot_id=bottle.id, period_start=period_start,
        period_end=period_end, summary=summary_text,
    )
    modules = await load_modules(db, bottle_id=bottle.id)
    await modules.nightly(
        NightlyContext(db=db, bottle=bottle, period_start=period_start,
                       period_end=period_end, summary=summary_text)
    )
    logger.info("stored dream %d for Bottle %d (%s)", summary.id, bottle.id, bottle.name)
    return summary
