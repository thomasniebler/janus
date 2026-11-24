"""JID formatting."""

from typing import Any


def Jid2String(jid: Any) -> str:  # noqa: N802 - mirrors the real Neonize name
    """Return `user@server` for a JID-like object."""
    return f"{jid.User}@{jid.Server}"
