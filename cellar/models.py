from pathlib import Path

from typing import Literal

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

    @model_validator(mode="after")
    def validate_sasl_credentials(self) -> "IRCProfile":
        if bool(self.sasl_username) != bool(self.sasl_password):
            raise ValueError("SASL username and password must be provided together")
        return self


class LLMProfile(BaseModel):
    endpoint: str
    model: str
    api_key: str | None = None
    temperature: float = 0.7
    max_tokens: int = 160


class Bottle(BaseModel):
    id: int
    name: str
    soul_prompt_path: Path
    irc: IRCProfile
    llm: LLMProfile
    max_lines: int = Field(default=2, ge=1)
    max_chars: int = Field(default=400, ge=1, le=450)
    cooldown_seconds: float = Field(default=1.0, ge=0)
    extract_memories: bool = False


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


class UserMemory(BaseModel):
    id: int
    user_id: str
    memory_text: str
    memory_type: MemoryType
    confidence: float


class LogSearchResult(BaseModel):
    id: int
    timestamp: str
    network: str
    channel: str
    speaker: str
    body: str
    bot_id: int
