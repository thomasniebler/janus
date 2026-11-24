"""Tests for the Jira planner connector (JAN-221)."""

from unittest.mock import patch

import pytest

from janus import models, planner_service, security
from janus.connectors import jira as jira_connector
from janus.services.local_planner_service import LocalPlannerService


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ({"name": "To Do", "statusCategory": {"key": "new"}}, models.TaskStatus.TODO),
        ({"name": "In Review", "statusCategory": {"key": "indeterminate"}}, models.TaskStatus.IN_PROGRESS),
        ({"name": "Closed", "statusCategory": {"key": "done"}}, models.TaskStatus.DONE),
        ({"name": "In Progress"}, models.TaskStatus.IN_PROGRESS),
        ({"name": "Blocked"}, models.TaskStatus.TODO),
        (None, models.TaskStatus.TODO),
    ],
)
def test_task_status_for_maps_jira_statuses(status, expected):
    """Jira's workflow-specific status names map onto Janus statuses by status category."""
    assert jira_connector.task_status_for(status) is expected


def test_synced_jira_issues_stay_readable(db):
    """A synced "To Do" issue no longer breaks every later read of imported tasks (JAN-221)."""
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.flush()
    account = models.PlannerAccount(
        name="Jira",
        provider=models.Provider.JIRA,
        token=security.security_service.encrypt("me@example.com:api-token"),
        api_url="https://jira.test",
        user_id=user.id,
    )
    db.add(account)
    db.commit()
    issues = {
        "issues": [
            {
                "key": "J-1",
                "fields": {"summary": "Write notes", "status": {"name": "To Do", "statusCategory": {"key": "new"}}},
            },
            {
                "key": "J-2",
                "fields": {
                    "summary": "Review",
                    "status": {"name": "In Review", "statusCategory": {"key": "indeterminate"}},
                },
            },
        ]
    }

    with patch("janus.connectors.jira.Jira") as jira_client:
        jira_client.return_value.jql.return_value = issues
        planner_service.PlannerService(db).sync_all()

    jira_client.assert_called_once_with(
        url="https://jira.test", username="me@example.com", password="api-token", cloud=True
    )
    db.expire_all()
    assert account.last_sync_error is None
    tasks = LocalPlannerService(db).list_tasks(user.id, include_imported=True)
    assert sorted((task["title"], task["status"]) for task in tasks) == [
        ("Review", "In Progress"),
        ("Write notes", "Todo"),
    ]
