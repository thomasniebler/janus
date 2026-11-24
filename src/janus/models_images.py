"""ORM model of chat-generated images (JAN-253); re-exported from ``janus.models``.

The bytes live in files under ``config.settings.image_storage_dir`` (see
``services/image_store.py``); this table only holds their metadata.
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import backref, relationship

from janus import database
from janus.time_utils import utcnow


class GeneratedImage(database.Base):
    """One image the chat tool ``generate_image`` stored for its owner."""

    __tablename__ = "generated_images"

    #: Public, URL-safe id used in ``/api/images/<id>``.
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    #: ``NULL`` for an image an unattended run made (JAN-359): it belongs to no chat.
    conversation_id = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True, index=True)
    #: The assistant message whose turn made it; set when the reply is saved.
    message_id = Column(Integer, ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True)
    prompt = Column(Text, nullable=False)
    provider = Column(String, nullable=False)
    model = Column(String, nullable=False)
    #: Relative to ``image_storage_dir``: ``<user_id>/<id>.<ext>``.
    path = Column(String, nullable=False)
    mime_type = Column(String, nullable=False)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    size_bytes = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=utcnow, server_default=func.now(), nullable=False)

    # SQLite does not enforce FK cascades: the ORM deletes the rows with their
    # conversation, and ConversationService removes the files.
    conversation = relationship(
        "Conversation", backref=backref("generated_images", cascade="all, delete-orphan", passive_deletes=False)
    )
    message = relationship("ConversationMessage")


__all__ = ["GeneratedImage"]
