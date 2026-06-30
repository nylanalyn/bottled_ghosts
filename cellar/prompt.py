from pathlib import Path


def read_soul(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def build_prompt(
    *, soul: str, module_state: list[str], memories: list[str], dreams: list[str],
    relevant: list[tuple[str, str]], history: list[tuple[str, str]], speaker: str, body: str
) -> list[dict[str, str]]:
    rules = (
        "You are an IRC character. Reply with plain text in one or two short lines. "
        "Do not use Markdown. Put any private reasoning inside <think> tags."
    )
    module_context = "\n".join(module_state) or "(none)"
    trusted = "\n".join(f"- {memory}" for memory in memories) or "(none)"
    dream_context = "\n".join(f"- {dream}" for dream in dreams) or "(none)"
    retrieved = "\n".join(f"<{name}> {text}" for name, text in relevant) or "(none)"
    transcript = "\n".join(f"<{name}> {text}" for name, text in history)
    user = (
        f"Enabled module context:\n{module_context}\n\n"
        f"Approved memories about {speaker}:\n{trusted}\n\n"
        f"Recent dream summaries:\n{dream_context}\n\n"
        f"Relevant earlier IRC messages:\n{retrieved}\n\n"
        f"Recent IRC conversation:\n{transcript}\n\nCurrent message from {speaker}:\n{body}"
    )
    return [{"role": "system", "content": f"{rules}\n\n{soul}"}, {"role": "user", "content": user}]
