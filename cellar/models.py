from pathlib import Path
import re

from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

MemoryType = Literal["preference", "project", "relationship", "identity", "temporary_state"]


class IRCProfile(BaseModel):
    network: str
    host: str
    port: int = 6697
    tls: bool = True
    nick: str
    username: str
    realname: str
    channels: list[str]
    password: str | None = None
    sasl_username: str | None = None
    sasl_password: str | None = None
    user_modes: str = ""
    alternate_nicks: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_sasl_credentials(self) -> "IRCProfile":
        if bool(self.sasl_username) != bool(self.sasl_password):
            raise ValueError("SASL username and password must be provided together")
        return self

    @model_validator(mode="after")
    def validate_user_modes(self) -> "IRCProfile":
        if self.user_modes and not re.fullmatch(r"[+-][A-Za-z]+(?:[+-][A-Za-z]+)*", self.user_modes):
            raise ValueError("IRC user modes must look like +B or +Bi-w")
        return self

    @model_validator(mode="after")
    def validate_alternate_nicks(self) -> "IRCProfile":
        nick_pattern = re.compile(r"^[A-Za-z0-9\-\[\]\\`_^{|}~]+$")
        seen = {self.nick.casefold()}
        for nick in self.alternate_nicks:
            if not nick_pattern.fullmatch(nick):
                raise ValueError("alternate nicks must use valid IRC nickname characters")
            folded = nick.casefold()
            if folded in seen:
                raise ValueError("alternate nicks must be unique and differ from the primary nick")
            seen.add(folded)
        return self


class LLMProfile(BaseModel):
    endpoint: str
    model: str
    api_key: str | None = None
    temperature: float = 0.7
    max_tokens: int = 160
    # OpenAI-compatible penalties. Higher values push the model away from
    # tokens and topics it has already produced, which is the cheapest lever
    # for breaking the "stuck on one phrase" attractor (e.g. repeating a
    # catchphrase, or noticing the same activity in the same words).
    frequency_penalty: float = Field(default=0.0, ge=-2.0, le=2.0)
    presence_penalty: float = Field(default=0.0, ge=-2.0, le=2.0)


class Bottle(BaseModel):
    id: int
    name: str
    soul_prompt_path: Path
    irc: IRCProfile
    llm: LLMProfile
    max_lines: int = Field(default=2, ge=1)
    max_chars: int = Field(default=400, ge=1, le=450)
    cooldown_seconds: float = Field(default=1.0, ge=0)
    listen_window_seconds: float = Field(default=8.0, gt=0)
    extract_memories: bool = False
    timezone: str = "UTC"
    aliases: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_timezone(self) -> "Bottle":
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"unknown IANA time zone: {self.timezone}") from error
        return self

    @property
    def address_names(self) -> tuple[str, ...]:
        # Names that may be used to address the bot. The runtime's active nick
        # must be used separately when attributing the bot's own speech: after a
        # nick collision, another user may legitimately hold one of these names.
        return (self.irc.nick, *self.irc.alternate_nicks, *self.aliases)


class IRCMessage(BaseModel):
    network: str
    channel: str
    speaker: str
    body: str
    bot_id: int
    user_id: str | None = None


class IncomingIRCMessage(BaseModel):
    nick: str
    hostmask: str | None
    account: str | None
    target: str
    body: str


class BottleSummary(BaseModel):
    id: int
    name: str
    enabled: bool
    network: str
    nick: str
    channels: list[str]
    extract_memories: bool


class ExtractedMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=500)
    type: MemoryType
    confidence: float = Field(ge=0, le=1)


class ExtractedMemories(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[ExtractedMemory] = Field(max_length=3)


class MemorySource(BaseModel):
    message_id: int
    body: str


class MemoryCandidateView(BaseModel):
    id: int
    user_id: str
    canonical_name: str
    source_message_id: int
    source_body: str
    candidate_text: str
    memory_type: MemoryType
    confidence: float
    status: Literal["pending", "approved", "rejected"]
    source_messages: list[MemorySource] = Field(default_factory=list)


class UserMemory(BaseModel):
    id: int
    user_id: str
    memory_text: str
    memory_type: MemoryType
    confidence: float
    expires_at: str | None = None


class UserMemoryView(UserMemory):
    canonical_name: str
    source_candidate_id: int | None
    source_body: str | None


class LogSearchResult(BaseModel):
    id: int
    timestamp: str
    network: str
    channel: str
    speaker: str
    body: str
    bot_id: int


class DreamSummary(BaseModel):
    id: int
    bot_id: int
    period_start: str
    period_end: str
    summary: str
    created_at: str


class IgnoreRule(BaseModel):
    id: int
    bot_id: int
    network: str
    match_type: Literal["account", "hostmask", "nick"]
    match_value: str
    action: Literal["drop", "no_response"]
    created_at: str
