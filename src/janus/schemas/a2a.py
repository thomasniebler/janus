"""Pydantic contracts for the remote A2A agent registry API (JAN-404)."""

from datetime import datetime

from pydantic import BaseModel, Field, SecretStr

MAX_TOKEN_CHARS = 8192


class A2AAgentCreate(BaseModel):
    """Register a remote agent by its base URL; the bearer token is write-only."""

    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=1, max_length=2048)
    token: SecretStr | None = Field(default=None, max_length=MAX_TOKEN_CHARS)


class A2AAgentUpdate(BaseModel):
    """Partial update; a new base URL forgets the card and the token."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    enabled: bool | None = None
    token: SecretStr | None = Field(default=None, max_length=MAX_TOKEN_CHARS)
    clear_token: bool = False


class A2AAgentSkill(BaseModel):
    """One skill the agent card advertises (untrusted, display only)."""

    name: str
    description: str


class A2AAgentResponse(BaseModel):
    """A registered agent without its token."""

    id: int
    name: str
    base_url: str
    enabled: bool
    card_name: str | None = None
    card_description: str | None = None
    protocol_version: str | None = None
    skills: list[A2AAgentSkill] = []
    rpc_url: str | None = None
    token_configured: bool
    last_error: str | None = None
    last_checked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
