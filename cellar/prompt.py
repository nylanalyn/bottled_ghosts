from pathlib import Path

from cellar.irc import irc_casefold


def read_soul(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def build_prompt(
    *, soul: str, module_state: list[str], memories: list[str], dreams: list[str],
    relevant: list[tuple[str, str]], history: list[tuple[str, str]], speaker: str, body: str,
    bot_nicks: tuple[str, ...] = (),
) -> list[dict[str, str]]:
    """Assemble a chat-completions prompt from character state and IRC history.

    The bot's own prior lines are emitted as ``assistant`` turns so the model
    sees its own voice as dialogue rather than just-more-channel-text to imitate.
    Other speakers' lines stay in ``user`` turns as ``<nick> text``. Consecutive
    same-role turns are merged so the conversation alternates cleanly.
    """
    rules = (
        "You are an IRC character. Reply with plain text in one or two short lines. "
        "Use plain text only."
    )
    bot_identity = {irc_casefold(nick) for nick in bot_nicks}

    def is_bot(speaker: str) -> bool:
        return irc_casefold(speaker) in bot_identity

    # Replay history as alternating turns, merging consecutive same-role lines
    # so the final conversation alternates user/assistant cleanly.
    turns: list[tuple[str, list[str]]] = []
    for nick, text in history:
        role = "assistant" if is_bot(nick) else "user"
        line = text if role == "assistant" else f"<{nick}> {text}"
        if turns and turns[-1][0] == role:
            turns[-1][1].append(line)
        else:
            turns.append((role, [line]))

    module_context = "\n".join(module_state) or "(none)"
    trusted = "\n".join(f"- {memory}" for memory in memories) or "(none)"
    dream_context = "\n".join(f"- {dream}" for dream in dreams) or "(none)"
    retrieved = "\n".join(f"<{name}> {text}" for name, text in relevant) or "(none)"
    current_message = (
        f"Enabled module context:\n{module_context}\n\n"
        f"Approved memories about {speaker}:\n{trusted}\n\n"
        f"Recent dream summaries:\n{dream_context}\n\n"
        f"Relevant earlier IRC messages:\n{retrieved}\n\nCurrent message from {speaker}:\n{body}"
    )
    turns.append(("user", [current_message]))
    # If the current-message turn would sit next to a same-role history turn
    # (e.g. the most recent history line was also from a user), merge them so
    # the conversation alternates cleanly instead of producing adjacent
    # user/user messages.
    if len(turns) >= 2 and turns[-2][0] == turns[-1][0]:
        turns[-2][1].extend(turns[-1][1])
        turns.pop()

    messages: list[dict[str, str]] = [{"role": "system", "content": f"{rules}\n\n{soul}"}]
    for role, lines in turns:
        messages.append({"role": role, "content": "\n".join(lines)})
    return messages
