"""Whether a model choice sends data off the local network (JAN-243, JAN-289).

Shared by every settings status that warns before outside text goes to a
cloud model: knowledge extraction, wiki synthesis, and the optional
prompt-injection classifier.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from janus import llm_service, models

#: Where ChatOllama connects when no base URL is stored.
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
#: Host name suffixes that only resolve on this machine or the local network.
_LOCAL_HOST_SUFFIXES = (".localhost", ".local", ".lan", ".internal", ".home.arpa")


def is_local_endpoint(base_url: object) -> bool:
    """Whether a base URL points at this machine or the local network.

    Local means loopback, a private or link-local address, ``localhost``, a
    single-label name (such as a Docker service name), or a LAN-only suffix like
    ``.local``. Every other host, hosted Ollama (``https://ollama.com/api``)
    included, counts as a cloud endpoint. No DNS lookup is made, so a public
    name that happens to resolve to a LAN address still counts as cloud.
    """
    if not isinstance(base_url, str) or not base_url.strip():
        return False
    raw = base_url.strip()
    host = (urlparse(raw if "://" in raw else f"http://{raw}").hostname or "").lower().rstrip(".")
    if not host:
        return False
    if host == "localhost" or host.endswith(_LOCAL_HOST_SUFFIXES):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback or address.is_private or address.is_link_local


def is_cloud_model(db: Session, choice: llm_service.ModelChoice | None) -> bool:
    """Whether a model choice sends data off the local network.

    Any provider but Ollama is cloud; Ollama is cloud too unless its stored base
    URL (the local default when none is stored) is a local endpoint.
    """
    provider = llm_service.describe_model(db, choice).split("/", 1)[0]
    if provider != models.LLMProvider.OLLAMA.value:
        return True
    settings = db.query(models.Settings).first()
    base_url = (settings.ollama_base_url if settings is not None else None) or DEFAULT_OLLAMA_BASE_URL
    return not is_local_endpoint(base_url)
