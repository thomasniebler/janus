"""ORM model of the brain-selected toolset of one plan step (JAN-300); re-exported from ``janus.models``."""

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint

from janus import database
from janus.time_utils import utcnow


class RuntimeToolSelection(database.Base):
    """The brain-selected toolset of one plan step, and why (JAN-300, ``janus.toolset_selection``).

    ``tool_ids`` ``NULL`` means every authorized toolbox was offered. A retried
    or resumed step of the same task reuses the stored choice; it is always
    intersected with the step's current authorization again.
    """

    __tablename__ = "runtime_tool_selections"
    __table_args__ = (UniqueConstraint("user_id", "task_id", "step_id", name="uq_runtime_tool_selection_step"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    task_id = Column(String, nullable=False, index=True)
    step_id = Column(String, nullable=False)
    task_mode = Column(String, nullable=False)
    source = Column(String, nullable=False)
    reason = Column(Text, nullable=True)
    tool_ids = Column(JSON, nullable=True)
    proposed_tool_ids = Column(JSON, nullable=False, default=list)
    trimmed_tool_ids = Column(JSON, nullable=False, default=list)
    widened_tool_ids = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
