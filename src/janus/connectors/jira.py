"""Jira connector for planner integrations."""

import logging

from atlassian import Jira

from janus import models
from janus.connectors import base

logger = logging.getLogger(__name__)

#: Jira's fixed status categories (``fields.status.statusCategory.key``) as Janus task statuses.
_STATUS_CATEGORY_TO_TASK_STATUS = {
    "new": models.TaskStatus.TODO,
    "indeterminate": models.TaskStatus.IN_PROGRESS,
    "done": models.TaskStatus.DONE,
}


def task_status_for(status: dict | None) -> models.TaskStatus:
    """Maps a Jira issue's ``fields.status`` onto a Janus ``TaskStatus``.

    Jira status names are workflow-specific ("To Do", "In Review", "Blocked"),
    so the fixed status category decides. Storing the raw name broke every
    later read of the imported tasks, since ``task_records.status`` only holds
    ``TaskStatus`` values (JAN-221).
    """
    status = status or {}
    category = (status.get("statusCategory") or {}).get("key")
    if category in _STATUS_CATEGORY_TO_TASK_STATUS:
        return _STATUS_CATEGORY_TO_TASK_STATUS[category]
    name = str(status.get("name") or "").strip().lower()
    for task_status in models.TaskStatus:
        if name == task_status.value.lower():
            return task_status
    return models.TaskStatus.TODO


class JiraConnector(base.Connector):
    """Connector for Jira tasks and issues."""

    def fetch_tasks(self) -> list[dict]:
        """Fetch active Jira issues assigned to the user.

        Returns:
            List[dict]: A list of dictionary representations of Jira issues.
        """
        try:
            # For Jira, token is often API Token and username might be in name or elsewhere
            # We'll assume for now token is "email:api_token" or just the PAT
            # This is a bit tricky, but we'll try to split it if it's formatted as email:token
            token = self.decrypted_token
            if ":" in token:
                username, api_token = token.split(":", 1)
            else:
                username = None  # PAT usage
                api_token = token

            jira = Jira(url=self.account.api_url, username=username, password=api_token, cloud=True)

            # JQL to find active issues assigned to the user
            jql = "assignee = currentUser() AND statusCategory != Done"
            issues = jira.jql(jql)["issues"]

            results = []
            for issue in issues:
                results.append(
                    {
                        "external_id": issue["key"],
                        "title": issue["fields"]["summary"],
                        "status": task_status_for(issue["fields"].get("status")),
                        "due_date": None,  # Could extract from fields if present
                    }
                )
            return results
        except Exception as e:
            logger.error(f"Failed to fetch tasks from Jira ({self.account.name}): {e}")
            return []

    def fetch_events(self, start_time, end_time):
        """Jira doesn't provide events like a calendar.

        Args:
            start_time (datetime): The start time for the range.
            end_time (datetime): The end time for the range.

        Returns:
            List[dict]: An empty list as Jira does not provide calendar events.
        """
        return []

    def test_connection(self) -> tuple[bool, str]:
        """Tests the connection to Jira."""
        try:
            token = self.decrypted_token
            if ":" in token:
                username, api_token = token.split(":", 1)
            else:
                username = None
                api_token = token

            jira = Jira(url=self.account.api_url, username=username, password=api_token, cloud=True)
            # Try to get current user info to verify connection
            user = jira.get_permissions()
            if user:
                return True, "Successfully connected to Jira."
            return False, "Failed to verify Jira credentials."
        except Exception as e:
            return False, f"Jira connection failed: {e}"
