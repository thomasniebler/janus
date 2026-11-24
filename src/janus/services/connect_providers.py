"""The providers behind Settings -> Connections, behind one interface.

Google and Microsoft both start with an auth URL, come back to ``/api/connections/<provider>/callback``
and then own a connection, calendar sources and optionally a mailbox. ``api/connections.py`` and
``api/planner_sources.py`` handle both through this registry instead of per-provider routes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from janus.services import (
    google_client,
    google_connect_service,
    microsoft_connect_service,
    planner_account_service,
)
from janus.services import oauth_connection_service as connections


@dataclass(frozen=True)
class ConnectProvider:
    """How to connect one provider; the callbacks never see HTTP objects."""

    name: str
    #: The ``settings.toml`` keys that configure the provider's OAuth client.
    config_keys: tuple[str, ...]
    #: The granted scope that proves the sign-in can back a mailbox; ``None`` when mail is not offered.
    mail_scope: str | None
    is_configured: Callable[[], bool]
    #: ``begin(db, user_id, redirect_uri)`` stores the state and returns the provider's sign-in URL.
    begin: Callable[[Session, int, str], str]
    #: ``complete(db, user_id, state, query, redirect_uri)`` stores the connection; raises ``ConnectionFlowError``.
    complete: Callable[[Session, int, connections.FlowState, dict[str, str], str], None]
    #: Whether a stored state row belongs to this provider's flow.
    owns_state: Callable[[connections.FlowState], bool]

    @property
    def callback_path(self) -> str:
        """The redirect URI path to register with the provider."""
        return f"/api/connections/{self.name}/callback"


def _begin_microsoft(db: Session, user_id: int, redirect_uri: str) -> str:
    return microsoft_connect_service.begin_login(db, user_id, redirect_uri)


def _complete_microsoft(
    db: Session, user_id: int, state: connections.FlowState, query: dict[str, str], redirect_uri: str
) -> None:
    del redirect_uri  # MSAL keeps it in the stored flow
    microsoft_connect_service.complete_login(db, user_id, state.payload or "", query)
    planner_account_service.upsert_microsoft_calendar_account_for_user(db, user_id)
    db.commit()


PROVIDERS: dict[str, ConnectProvider] = {
    connections.GOOGLE: ConnectProvider(
        name=connections.GOOGLE,
        config_keys=("google_client_id", "google_client_secret"),
        mail_scope=google_client.MAIL_SCOPE,
        is_configured=google_client.is_configured,
        begin=google_connect_service.begin_login,
        complete=google_connect_service.complete_login,
        owns_state=lambda state: state.payload is None,
    ),
    connections.MICROSOFT: ConnectProvider(
        name=connections.MICROSOFT,
        config_keys=("microsoft_client_id", "microsoft_tenant_id"),
        mail_scope=microsoft_connect_service.MAIL_SCOPE,
        is_configured=microsoft_connect_service.is_configured,
        begin=_begin_microsoft,
        complete=_complete_microsoft,
        owns_state=lambda state: state.payload is not None,
    ),
}
