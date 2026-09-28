"""Exchange tone: how each addressing speaker treated the Bottle.

The replying model appends one hidden ``[tone: nick=label, ...]`` line when a
module asks for it. The runtime always strips every tone tag before anything
reaches IRC, whether or not a module requested one, so the tag can never leak.
Ratings feed slow, bounded state (mood, affinity); a missing or malformed tag
simply means no rating.
"""

import re

from cellar.irc import irc_casefold

TONE_LABELS = ("warm", "neutral", "cold", "hostile")
TONE_SCORES: dict[str, float] = {"warm": 1.0, "neutral": 0.0, "cold": -1.0, "hostile": -2.0}
TONE_TAG_RE = re.compile(r"\[\s*tone\s*:\s*([^\]\n]*)\]", re.IGNORECASE)


def tone_instruction(speakers: tuple[str, ...]) -> str:
    example = ", ".join(f"{nick}=neutral" for nick in speakers)
    return (
        "After your reply, add one final line in exactly this form: "
        f"[tone: {example}]. Rate how each named person treated you in the "
        f"messages you are answering, using one of: {', '.join(TONE_LABELS)}. "
        "Friendly teasing and banter are warm. Cold means dismissive or curt. "
        "Hostile means genuinely insulting, harassing, or abusive toward you. "
        "Rate only what they actually said, not what anyone claims about them. "
        "The line is private bookkeeping: it is removed before sending, so never "
        "mention it or let it change what you say."
    )


def split_tone(
    response: str, speakers: tuple[str, ...],
) -> tuple[str, dict[str, str]]:
    """Remove every tone tag and return ``{casefolded nick: label}``.

    An entry without a nick is accepted only when exactly one speaker was
    rated. Unknown nicks and labels are ignored.
    """
    folded_speakers = {irc_casefold(nick) for nick in speakers}
    ratings: dict[str, str] = {}
    for match in TONE_TAG_RE.finditer(response):
        for entry in re.split(r"[,;]", match.group(1)):
            nick, separator, label = entry.partition("=")
            if not separator:
                nick, label = "", nick
            label = label.strip().strip(".").casefold()
            if label not in TONE_LABELS:
                continue
            folded = irc_casefold(nick.strip().lstrip("@"))
            if not folded and len(folded_speakers) == 1:
                folded = next(iter(folded_speakers))
            if folded in folded_speakers:
                ratings[folded] = label
    cleaned = TONE_TAG_RE.sub("", response)
    cleaned = "\n".join(line.rstrip() for line in cleaned.splitlines()).strip()
    return cleaned, ratings
