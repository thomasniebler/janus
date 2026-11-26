# API Router Code Map

Thin FastAPI routers; registration and mount order live in `../app_factory.py`, request models in `../schemas/`.
Each router delegates to the like-named service in `../services/` (`memory.py` -> `memory_service.py`).

| Routers | Look next |
| --- | --- |
| `auth.py`, `character.py`, `conversations.py`, `dashboard.py`, `desktop.py`, `inbox.py`, `emails.py`, `errors.py`, `events.py`, `memory.py`, `topics.py`, `stats.py`, `docs.py`, `github.py`, `linear.py`, `web_search.py`, `self_evolution.py`, `telegram.py`, `whatsapp.py`, `webhooks.py`, `wiki.py`, `a2a.py`, `runtime_tasks.py` | like-named service; auth also `../security.py` |
| `planner*.py` | `../planner_service.py`, `../connectors/` |
| `connections.py` | `../services/connect_providers.py` (OAuth connect) |
| `runtime_triggers.py`, `trigger_definitions.py` | `../trigger_service.py`, `../trigger_definition_service.py` |
| `tools.py` | `../services/mcp_service.py`, `../tool_policy.py` |
| `knowledge.py` | `../services/knowledge_*.py` |
| `skills.py`, `skill_suggestions.py` | `../services/skill_*.py` |
| `llm_models.py`, `images.py` | `../services/llm_model_catalog.py`, `../services/image_store.py` |
| `status.py` | `../status_service.py` |
| `dev.py` | admin-only Storybook serving |

Mount order: `skill_suggestions.py` before `skills.py`; `a2a.py` before `tools.py`.
