"""Owner-scoped MCP tool registry: servers, discovered tools and credentials."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from janus import database
from janus.models.enums import MCPDiscoveryStatus, MCPTransport
from janus.time_utils import utcnow


class MCPServer(database.Base):
    """User-owned remote MCP server connection metadata."""

    __tablename__ = "mcp_servers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_name = Column(String, nullable=False)
    endpoint = Column(String, nullable=False)
    transport = Column(
        Enum(MCPTransport, native_enum=False, create_constraint=True, validate_strings=True),
        default=MCPTransport.STREAMABLE_HTTP,
        nullable=False,
    )
    enabled = Column(Boolean, default=False, server_default="0", nullable=False)
    discovery_status = Column(
        Enum(MCPDiscoveryStatus, native_enum=False, create_constraint=True, validate_strings=True),
        default=MCPDiscoveryStatus.NEVER,
        nullable=False,
        index=True,
    )
    last_health_status = Column(String, nullable=True)
    last_health_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    oauth_authorization_endpoint = Column(String, nullable=True)
    oauth_token_endpoint = Column(String, nullable=True)
    oauth_client_id = Column(String, nullable=True)
    oauth_scopes = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    discovered_tools = relationship("MCPDiscoveredTool", back_populates="server", cascade="all, delete-orphan")
    credentials = relationship("MCPCredential", back_populates="server", cascade="all, delete-orphan", uselist=False)


class MCPDiscoveredTool(database.Base):
    """Advisory metadata for one tool discovered from an MCP server."""

    __tablename__ = "mcp_discovered_tools"
    __table_args__ = (UniqueConstraint("server_id", "upstream_name", name="uq_mcp_tool_server_name"),)

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False, index=True)
    upstream_name = Column(String, nullable=False)
    namespaced_id = Column(String, nullable=False, unique=True, index=True)
    description = Column(Text, nullable=False, default="", server_default="")
    input_schema = Column(JSON, nullable=False, default=dict)
    #: The server's MCP tool annotations (``readOnlyHint`` etc., JAN-343): advisory, bounded by discovery.
    annotations = Column(JSON, nullable=True)
    #: Prompt-injection scan of the server-written description and schema text (JAN-343): ``None`` when clean,
    #: else ``{"rules": [...], "hash": ..., "acknowledged": bool}``. A flagged tool is offered only once acknowledged.
    description_scan = Column(JSON, nullable=True)
    enabled = Column(Boolean, default=True, server_default="1", nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    server = relationship("MCPServer", back_populates="discovered_tools")


class MCPCredential(database.Base):
    """Encrypted owner-scoped OAuth credentials for an MCP server."""

    __tablename__ = "mcp_credentials"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    server_id = Column(
        Integer,
        ForeignKey("mcp_servers.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    encrypted_access_token = Column(Text, nullable=False)
    encrypted_refresh_token = Column(Text, nullable=True)
    token_type = Column(String, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    scopes = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = relationship("User")
    server = relationship("MCPServer", back_populates="credentials")
