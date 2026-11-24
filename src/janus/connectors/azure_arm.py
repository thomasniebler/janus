"""Read-only Azure Resource Manager (ARM) client: subscriptions, resource groups, resources.

A leaf module like ``microsoft_graph``: it knows nothing about Janus users or how a
token is obtained. The caller passes ``token_provider`` (an ARM access token) and
every request asks for a fresh one. Only GET requests exist here, on purpose.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

ARM_BASE_URL = "https://management.azure.com"
ARM_SCOPE = "https://management.azure.com/.default"
_MAX_ITEMS = 200


class AzureError(RuntimeError):
    """An ARM request failed. ``status_code`` is the HTTP status (0 for transport errors)."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        """Keep the HTTP status next to the one-line message."""
        super().__init__(message)
        self.status_code = status_code


def _segment(value: str, name: str) -> str:
    cleaned = value.strip()
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError(f"{name} must not be blank or a dot segment")
    return quote(cleaned, safe="")


def _error_message(response: httpx.Response) -> str:
    try:
        error = response.json().get("error") or {}
        detail = f"{error.get('code', 'error')}: {str(error.get('message', ''))[:300]}"
    except (ValueError, AttributeError):
        detail = "no error detail"
    hint = " (check `az login` and your Azure RBAC role)" if response.status_code in (401, 403) else ""
    return f"Azure request failed with HTTP {response.status_code}: {detail}{hint}"


class AzureArmClient:
    """Small synchronous, read-only ARM client."""

    def __init__(
        self,
        token_provider: Callable[[], str],
        *,
        base_url: str = ARM_BASE_URL,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Bind the token source; ``transport`` lets tests inject a mock."""
        self._token_provider = token_provider
        self._client = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)

    def close(self) -> None:
        """Release the HTTP connection pool."""
        self._client.close()

    def _get(self, path: str, api_version: str, top: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"api-version": api_version}
        if top is not None:
            params["$top"] = max(1, min(top, _MAX_ITEMS))
        try:
            response = self._client.get(
                path, params=params, headers={"Authorization": f"Bearer {self._token_provider()}"}
            )
        except httpx.HTTPError as exc:
            raise AzureError(f"Azure request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise AzureError(_error_message(response), response.status_code)
        try:
            value = response.json().get("value", [])
        except (ValueError, AttributeError) as exc:
            raise AzureError("Azure returned a response that is not JSON", response.status_code) from exc
        return [item for item in value if isinstance(item, dict)][: top or _MAX_ITEMS]

    def list_subscriptions(self) -> list[dict[str, Any]]:
        """Subscriptions the identity can see."""
        items = self._get("/subscriptions", "2022-12-01")
        return [{"id": i.get("subscriptionId"), "name": i.get("displayName"), "state": i.get("state")} for i in items]

    def list_resource_groups(self, subscription_id: str) -> list[dict[str, Any]]:
        """Resource groups in one subscription."""
        path = f"/subscriptions/{_segment(subscription_id, 'subscription_id')}/resourcegroups"
        return [
            {"name": i.get("name"), "location": i.get("location"), "tags": i.get("tags") or {}}
            for i in self._get(path, "2021-04-01")
        ]

    def list_resources(
        self, subscription_id: str, resource_group: str | None = None, top: int = 50
    ) -> list[dict[str, Any]]:
        """Resources in a subscription, or in one resource group of it."""
        base = f"/subscriptions/{_segment(subscription_id, 'subscription_id')}"
        if resource_group and resource_group.strip():
            base += f"/resourceGroups/{_segment(resource_group, 'resource_group')}"
        return [
            {
                "id": i.get("id"),
                "name": i.get("name"),
                "type": i.get("type"),
                "location": i.get("location"),
                "tags": i.get("tags") or {},
            }
            for i in self._get(f"{base}/resources", "2021-04-01", top=top)
        ]
