from pathlib import Path


def read_soul(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def build_prompt(*, soul: str, history: list[tuple[str, str]], speaker: str, body: str) -> list[dict[str, str]]:
    rules = (
        "You are an IRC character. Reply with plain text in one or two short lines. "
        "Do not use Markdown. Put any private reasoning inside <think> tags."
    )
    transcript = "\n".join(f"<{name}> {text}" for name, text in history)
    user = f"Recent IRC conversation:\n{transcript}\n\nCurrent message from {speaker}:\n{body}"
    return [{"role": "system", "content": f"{rules}\n\n{soul}"}, {"role": "user", "content": user}]
