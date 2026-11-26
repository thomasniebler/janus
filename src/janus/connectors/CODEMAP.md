# Connector Code Map

Provider clients used by `../planner_service.py`; a leaf package (imports no services or runtime).

| Files | Purpose |
| --- | --- |
| `base.py` | Connector interfaces |
| `google*.py`, `jira.py`, `microsoft_calendar.py` | Planner connectors |
| `microsoft_graph.py`, `azure_arm.py` | API clients, not connectors |
