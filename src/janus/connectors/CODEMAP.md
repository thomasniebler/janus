# Planner Connector Code Map

Scope: planner provider connectors under `src/janus/connectors/`.

## Responsibilities

- define provider-specific planner fetch behavior
- normalize external planner data through shared abstractions
- support `PlannerService` when syncing calendars, tasks, and related context

## File map

| File | Responsibility |
| --- | --- |
| `__init__.py` | Package marker (JAN-263), so the import-linter contracts can see the connectors |
| `base.py` | Connector interfaces and shared planner provider abstractions |
| `google.py` | Google planner/calendar connector implementation; its API clients and token refreshes use the endpoints from `google_endpoints.py` |
| `google_endpoints.py` | The Google endpoints the backend calls (token exchange and refresh, userinfo, Calendar and Tasks API clients) for both the connectors and the OAuth flows in `services/google_connect_service.py` and `api/connections.py`, and the `JANUS_GOOGLE_API_BASE_URL` test seam that points them at the e2e fake Google (JAN-221) |
| `jira.py` | Jira task/planner connector implementation; `task_status_for` maps Jira statuses onto `TaskStatus` by status category (JAN-221) |
| `azure_arm.py` | Read-only Azure Resource Manager client (subscriptions, resource groups, resources) taking a token provider; not a planner `Connector` | Read directly |
| `microsoft_calendar.py` | Planner `Connector` for the selected Outlook calendars of a connected Microsoft account (injected Graph client factory; external ids `calendar_id:event_id`) |
| `microsoft_graph.py` | Microsoft Graph client (Outlook mail/calendar, Teams chats) taking a token provider; not a planner `Connector`, returns trimmed dicts, a leaf with no Janus imports |

## Read-next guidance

- Connector contract or factory expectations -> `base.py`, then `../planner_service.py`
- Google integration behavior -> `google.py`
- Which Google endpoints the backend calls, or the e2e fake Google -> `google_endpoints.py`
- Jira integration behavior -> `jira.py`
- Azure Resource Manager (read-only) request shaping -> `azure_arm.py`
- Outlook or Teams request/response shaping -> `microsoft_graph.py`
