"""Base service class for Janus."""

from sqlalchemy.orm import Session

from janus import database


class BaseService:
    """Base class for all services, handling database sessions."""

    def __init__(self, db: Session = None):
        """Initializes the service.

        Args:
            db (Session, optional): A database session. Defaults to a new SessionLocal.
        """
        self.db = db or database.SessionLocal()
        self._external_db = db is not None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit, closing the session if it was created locally."""
        if not self._external_db:
            self.db.close()
