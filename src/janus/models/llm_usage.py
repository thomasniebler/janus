"""Per-user LLM token usage, aggregated per day, surface and model (JAN-408)."""

from sqlalchemy import Column, Date, Integer, String, UniqueConstraint

from janus import database


class LlmUsageDay(database.Base):
    """One user's LLM calls on one UTC day for one surface and model.

    ``user_id`` is ``None`` for calls made with no owner bound (system work).
    Rows are upserted by ``janus.llm_usage``, never written per call.
    """

    __tablename__ = "llm_usage_days"
    __table_args__ = (UniqueConstraint("user_id", "day", "surface", "model", name="uq_llm_usage_days_key"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    day = Column(Date, nullable=False, index=True)
    #: chat, trigger, delegated, or other.
    surface = Column(String(32), nullable=False)
    #: ``provider/model`` as ``LLMService.model_label`` reports it.
    model = Column(String(200), nullable=False)
    calls = Column(Integer, nullable=False, default=0, server_default="0")
    input_tokens = Column(Integer, nullable=False, default=0, server_default="0")
    output_tokens = Column(Integer, nullable=False, default=0, server_default="0")
    #: Calls whose provider reported no usage (some streamed responses); counted, with 0 tokens.
    unreported_calls = Column(Integer, nullable=False, default=0, server_default="0")


__all__ = ["LlmUsageDay"]
