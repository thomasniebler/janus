"""Base connector module for planner integrations."""

from abc import ABC, abstractmethod
from datetime import datetime

from janus import models, security


class Connector(ABC):
    """Base class for all planner connectors."""

    def __init__(self, account: models.PlannerAccount):
        """Initializes a Connector with a planner account.

        Args:
            account (PlannerAccount): The planner account to use.
        """
        self.account = account

    @property
    def decrypted_token(self) -> str:
        """Returns the decrypted token for the account."""
        return security.security_service.decrypt(self.account.token)

    @abstractmethod
    def fetch_tasks(self) -> list[dict]:
        """Fetch active tasks from the provider."""
        pass

    @abstractmethod
    def fetch_events(self, start_time: datetime, end_time: datetime) -> list[dict]:
        """Fetch calendar events for the given time range."""
        pass

    @abstractmethod
    def test_connection(self) -> tuple[bool, str]:
        """Tests the connection to the provider.

        Returns:
            tuple[bool, str]: A tuple containing a success boolean and a message.
        """
        pass
