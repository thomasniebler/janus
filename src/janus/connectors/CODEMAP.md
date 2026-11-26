# Planner Connector Code Map

Scope: provider connectors and API clients under `src/janus/connectors/`, used by `../planner_service.py`.
A leaf package: it imports no services, toolboxes, runtime, or workers.

## File map

| File | Responsibility |
| --- | --- |
| `base.py` | Connector interfaces and shared planner abstractions |
| `google.py` | Google Calendar/Tasks planner connector |
| `google_endpoints.py` | Google endpoint URLs for connectors and OAuth flows; e2e fake-Google base-URL seam |
| `jira.py` | Jira task connector and status mapping |
| `microsoft_calendar.py` | Planner connector for selected Outlook calendars |
| `microsoft_graph.py` | Microsoft Graph client (Outlook mail/calendar, Teams chats); not a planner connector |
| `azure_arm.py` | Read-only Azure Resource Manager client; not a planner connector |

## Read-next guidance

- Connector contract or factory -> `base.py`, then `../planner_service.py`
- Google OAuth flow -> `../services/google_connect_service.py`, `../api/connections.py`
