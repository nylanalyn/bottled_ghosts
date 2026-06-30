from getpass import getpass
from pathlib import Path

from cellar.models import IRCProfile, LLMProfile


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{prompt}{suffix}: ").strip()
    if value:
        return value
    if default is not None:
        return default
    raise ValueError(f"{prompt} is required")


def ask_int(prompt: str, default: int) -> int:
    return int(ask(prompt, str(default)))


def ask_float(prompt: str, default: float) -> float:
    return float(ask(prompt, str(default)))


def ask_bool(prompt: str, default: bool) -> bool:
    default_text = "yes" if default else "no"
    value = ask(f"{prompt} (yes/no)", default_text).lower()
    if value in {"yes", "y"}:
        return True
    if value in {"no", "n"}:
        return False
    raise ValueError(f"{prompt} must be yes or no")


def collect_configuration() -> tuple[str, Path, IRCProfile, LLMProfile, int, int, float, bool]:
    print("Bottle identity")
    name = ask("Bottle name")
    soul_path = Path(ask("Soul prompt path"))
    if not soul_path.is_file():
        raise ValueError(f"soul prompt does not exist: {soul_path}")

    print("\nIRC connection")
    network = ask("Network name")
    host = ask("Server host")
    tls = ask_bool("Use TLS", True)
    port = ask_int("Server port", 6697 if tls else 6667)
    nick = ask("Nickname", name)
    username = ask("Username", nick)
    realname = ask("Real name", name)
    channels = [item.strip() for item in ask("Channels (comma-separated)").split(",") if item.strip()]
    if not channels:
        raise ValueError("at least one IRC channel is required")
    password = getpass("Server password (optional): ").strip() or None
    sasl_username = ask("SASL username (leave blank to disable)", "") or None
    sasl_password = getpass("SASL password: ").strip() if sasl_username else None

    print("\nLLM connection")
    endpoint = ask("Chat completions endpoint")
    model = ask("Model")
    api_key = getpass("API key (optional): ").strip() or None
    temperature = ask_float("Temperature", 0.7)
    max_tokens = ask_int("Maximum response tokens", 160)

    print("\nIRC output enforcement")
    max_lines = ask_int("Maximum reply lines", 2)
    max_chars = ask_int("Maximum characters per line", 400)
    cooldown = ask_float("Seconds between lines", 1.0)
    extract_memories = ask_bool("Extract pending memory candidates", False)

    irc = IRCProfile(network=network, host=host, port=port, tls=tls, nick=nick,
                     username=username, realname=realname, channels=channels, password=password,
                     sasl_username=sasl_username, sasl_password=sasl_password)
    llm = LLMProfile(endpoint=endpoint, model=model, api_key=api_key,
                     temperature=temperature, max_tokens=max_tokens)
    return name, soul_path, irc, llm, max_lines, max_chars, cooldown, extract_memories
