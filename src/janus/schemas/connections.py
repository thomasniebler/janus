"""Schemas for the connected Google and Microsoft accounts (Settings -> Connections)."""

from typing import Literal

from pydantic import BaseModel, Field


class ConnectionStatus(BaseModel):
    """One provider's state for the current user."""

    provider: Literal["google", "microsoft"]
    #: The operator has put the OAuth client in settings.toml / the environment.
    configured: bool
    connected: bool
    account_email: str | None = None
    #: The provider can back a mailbox at all (Outlook mail is not offered).
    mail_supported: bool = True
    #: The sign-in grants mailbox access, so it can back a mailbox on the Mail page.
    mail_ready: bool = False
    #: Redirect URIs to register with the provider (shown while not configured or on a mismatch).
    redirect_uris: list[str]
    #: The settings.toml keys that configure the provider.
    config_keys: list[str]


class ConnectionsOverview(BaseModel):
    """Both providers' connection state."""

    google: ConnectionStatus
    microsoft: ConnectionStatus


class SyncResult(BaseModel):
    """Outcome of an on-demand planner sync."""

    synced_accounts: int
    errors: list[str]


class MailboxCreate(BaseModel):
    """Payload to add a mailbox on a connected account."""

    name: str | None = Field(default=None, max_length=100)
