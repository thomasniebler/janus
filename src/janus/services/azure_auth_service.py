"""Azure credential for Azure Resource Manager access (``DefaultAzureCredential``).

Unlike Microsoft 365 (see ``microsoft_auth_service``), Azure resource access uses the
standard azure-identity chain: environment service principal, workload/managed
identity, then the ``az login`` session of the machine Janus runs on. Nothing is
stored by Janus. The feature is off unless ``JANUS_AZURE_ENABLED`` is set, and the
tools are read-only and admin-only.
"""

from __future__ import annotations

import threading

from azure.core.exceptions import AzureError as AzureCoreError
from azure.identity import DefaultAzureCredential

from janus import config
from janus.connectors.azure_arm import ARM_SCOPE, AzureArmClient

_lock = threading.Lock()
_credential: DefaultAzureCredential | None = None


class AzureAuthError(RuntimeError):
    """Azure access is disabled or no credential in the chain could produce a token."""


def is_enabled(settings: config.Settings | None = None) -> bool:
    """Return whether Azure resource access is switched on."""
    return bool((settings or config.settings).azure_enabled)


def reset_credential() -> None:
    """Drop the cached credential (tests, and after changing the environment)."""
    global _credential
    with _lock:
        _credential = None


def _get_credential(settings: config.Settings) -> DefaultAzureCredential:
    global _credential
    with _lock:
        if _credential is None:
            tenant = settings.microsoft_tenant_id.strip()
            _credential = DefaultAzureCredential(
                exclude_interactive_browser_credential=True,
                **({"visual_studio_code_tenant_id": tenant} if tenant else {}),
            )
        return _credential


def get_arm_token(settings: config.Settings | None = None) -> str:
    """Return a current ARM access token; never prompts."""
    active = settings or config.settings
    if not is_enabled(active):
        raise AzureAuthError("Azure access is off. Set JANUS_AZURE_ENABLED=true.")
    try:
        return _get_credential(active).get_token(ARM_SCOPE).token
    except AzureCoreError as exc:
        raise AzureAuthError(
            "No Azure credential available. Run `az login` on this machine or set AZURE_CLIENT_ID/"
            "AZURE_TENANT_ID/AZURE_CLIENT_SECRET."
        ) from exc


def build_arm_client(settings: config.Settings | None = None) -> AzureArmClient:
    """Return an ARM client that asks this module for a fresh token on every request."""
    active = settings or config.settings
    return AzureArmClient(lambda: get_arm_token(active))
