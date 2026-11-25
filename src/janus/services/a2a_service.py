"""The owner-scoped registry of remote A2A agents (JAN-404).

A user registers a remote agent by its base URL and a label of their own;
Janus fetches its agent card (``a2a_client.fetch_card``) and keeps a bounded
copy. An agent is offered to chat only once its card was fetched and the user
switched it on (``enabled``, off by default). An optional bearer token is stored
Fernet-encrypted and never returned, and is only ever sent to the agent URL's own
origin: a card that names a JSON-RPC endpoint on another host gets no token.
Deleting an agent also drops its tool policy.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from janus import models, security, time_utils
from janus.services import a2a_client
from janus.services.mcp_metadata import REMOTE_AGENT_TOOL_ID_PREFIX as A2A_TOOL_ID_PREFIX

MAX_NAME_CHARS = 80
MAX_TOKEN_CHARS = 8192


def tool_id(agent_id: int) -> str:
    """Return the registry id of one agent's tool."""
    return f"{A2A_TOOL_ID_PREFIX}{agent_id}"


def _origin(url: str) -> str:
    """Scheme, host and port of ``url`` (both URLs are already normalized by ``validate_url``)."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def _clean_name(name: str) -> str:
    cleaned = " ".join(str(name or "").split())
    if not cleaned:
        raise a2a_client.A2AError("give the agent a name")
    if len(cleaned) > MAX_NAME_CHARS:
        raise a2a_client.A2AError(f"the name is longer than {MAX_NAME_CHARS} characters")
    return cleaned


def _clean_token(token: str) -> str:
    value = token.strip()
    if not value or len(value) > MAX_TOKEN_CHARS or any(not (32 < ord(c) < 127) for c in value):
        raise a2a_client.A2AError("the token must be one line of printable ASCII without spaces")
    return value


class A2AService:
    """CRUD, card refresh, and asking for one user's remote agents."""

    def __init__(self, db: Session):
        """Bind the service to a database session."""
        self.db = db

    def list_agents(self, user: models.User) -> list[models.A2AAgent]:
        """Return the user's agents, oldest first."""
        return (
            self.db.query(models.A2AAgent).filter(models.A2AAgent.user_id == user.id).order_by(models.A2AAgent.id).all()
        )

    def get_agent(self, user: models.User, agent_id: int) -> models.A2AAgent | None:
        """Return one of the user's agents, or ``None``."""
        return (
            self.db.query(models.A2AAgent)
            .filter(models.A2AAgent.user_id == user.id, models.A2AAgent.id == agent_id)
            .first()
        )

    def create_agent(self, user: models.User, *, name: str, base_url: str, token: str | None = None) -> models.A2AAgent:
        """Register an agent (disabled) and try to fetch its card; a failed fetch is recorded, not raised."""
        agent = models.A2AAgent(
            user_id=user.id,
            name=_clean_name(name),
            base_url=a2a_client.validate_url(base_url),
            enabled=False,
            encrypted_token=security.security_service.encrypt(_clean_token(token)) if token else None,
        )
        self.db.add(agent)
        self.db.flush()
        self.refresh(agent)
        return agent

    def update_agent(self, agent: models.A2AAgent, changes: dict[str, Any]) -> models.A2AAgent:
        """Apply a partial update: name, base URL (forgets the card), token, enabled."""
        if "name" in changes and changes["name"] is not None:
            agent.name = _clean_name(changes["name"])
        if changes.get("base_url"):
            new_url = a2a_client.validate_url(changes["base_url"])
            if new_url != agent.base_url:
                agent.base_url = new_url
                agent.card = agent.rpc_url = None
                agent.enabled = False
                # A token meant for the old host must not follow the URL to a new one.
                agent.encrypted_token = None
        if "token" in changes:
            token = changes["token"]
            agent.encrypted_token = security.security_service.encrypt(_clean_token(token)) if token else None
        if "enabled" in changes and changes["enabled"] is not None:
            if changes["enabled"] and not agent.rpc_url:
                raise a2a_client.A2AError("fetch the agent card successfully before switching the agent on")
            agent.enabled = bool(changes["enabled"])
        self.db.flush()
        return agent

    def refresh(self, agent: models.A2AAgent) -> models.A2AAgent:
        """Fetch the agent card again; on failure keep the agent but switch it off and record why."""
        agent.last_checked_at = time_utils.utcnow()
        try:
            card = a2a_client.fetch_card(agent.base_url)
        except a2a_client.A2AError as exc:
            agent.last_error = str(exc)
            agent.enabled = False
            agent.card = agent.rpc_url = None
        else:
            agent.card = card.as_json()
            agent.rpc_url = card.rpc_url
            agent.last_error = None
        self.db.flush()
        return agent

    def delete_agent(self, agent: models.A2AAgent) -> None:
        """Delete the agent and its tool policy."""
        self.db.query(models.ToolPolicy).filter(
            models.ToolPolicy.user_id == agent.user_id, models.ToolPolicy.tool_id == tool_id(agent.id)
        ).delete(synchronize_session=False)
        self.db.delete(agent)
        self.db.flush()

    def ask(self, agent: models.A2AAgent, prompt: str) -> a2a_client.AgentAnswer:
        """Ask the agent; raises :class:`a2a_client.A2AError` when it is off, unchecked, or fails."""
        if not agent.enabled or not agent.rpc_url:
            raise a2a_client.A2AError("the agent is switched off")
        token = security.security_service.decrypt(agent.encrypted_token) if agent.encrypted_token else None
        if token and _origin(agent.rpc_url) != _origin(agent.base_url):
            # The card chose the endpoint; the user's token was given for the agent URL's host only.
            raise a2a_client.A2AError(
                f"the agent card points to {_origin(agent.rpc_url)}, not the agent URL's host "
                f"{_origin(agent.base_url)}; Janus sends the saved token only to that host"
            )
        protocol_version = str((agent.card or {}).get("protocol_version") or "")
        return a2a_client.A2AClient(agent.rpc_url, protocol_version, token=token).ask(prompt)

    def offered_agents(self, user: models.User) -> list[models.A2AAgent]:
        """Agents chat may use: switched on and with a fetched card."""
        return [agent for agent in self.list_agents(user) if agent.enabled and agent.rpc_url]


__all__ = ["A2A_TOOL_ID_PREFIX", "A2AService", "tool_id"]
