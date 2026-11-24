"""Azure resource tools (read-only, over Azure Resource Manager) using ``DefaultAzureCredential``.

Names and tags in the output are untrusted content (the registry entry sets
``reads_untrusted_content``). There are no write tools: changing Azure resources from
an agent is out of scope.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from janus.agent_toolboxes.shared import ToolContext
from janus.connectors.azure_arm import AzureArmClient, AzureError
from janus.services import azure_auth_service
from janus.tool_registry import ToolBuildContext, ToolRegistryEntry


def build_azure_tools(context: ToolContext) -> list[BaseTool]:
    """Build the read-only Azure tools. ``context`` is unused: the identity is the machine's Azure login."""
    del context

    def _call(action: Any) -> Any:
        client: AzureArmClient = azure_auth_service.build_arm_client()
        try:
            return action(client)
        except (AzureError, azure_auth_service.AzureAuthError) as exc:
            raise ValueError(str(exc)) from exc
        finally:
            client.close()

    @tool("azure_list_subscriptions")
    def azure_list_subscriptions() -> list[dict[str, Any]]:
        """Lists the Azure subscriptions the machine's Azure identity can see."""
        return _call(lambda client: client.list_subscriptions())

    @tool("azure_list_resource_groups")
    def azure_list_resource_groups(subscription_id: str) -> list[dict[str, Any]]:
        """Lists the resource groups of one Azure subscription (id from azure_list_subscriptions)."""
        return _call(lambda client: client.list_resource_groups(subscription_id))

    @tool("azure_list_resources")
    def azure_list_resources(
        subscription_id: str, resource_group: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Lists resources (id, name, type, location, tags) of an Azure subscription or one of its resource groups."""
        return _call(lambda client: client.list_resources(subscription_id, resource_group, top=limit))

    return [azure_list_subscriptions, azure_list_resource_groups, azure_list_resources]


def registry_entries() -> list[ToolRegistryEntry]:
    """The single ``azure.resources`` registry entry (read-only, admin-only, opt-in)."""

    def build(context: ToolBuildContext) -> list[BaseTool]:
        return build_azure_tools(context.tool_context)

    def enabled(context: ToolBuildContext) -> bool:
        return bool(context.user.is_admin and "azure" in context.tool_context.connected_capabilities)

    return [
        ToolRegistryEntry(
            id="azure.resources",
            reads_untrusted_content=True,
            display_name="Azure resources",
            description="List Azure subscriptions, resource groups and resources (read-only).",
            required_capabilities=("azure",),
            operation="read",
            risk_level="low",
            confirmation="never",
            docs_ref="tools.md#azure-resources",
            builder=build,
            enabled_when=enabled,
        )
    ]
