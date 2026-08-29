from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.entities.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.entities.chat.chat_session_message import ChatSessionMessage
    from app.entities.chunks.chunk import Chunk
    from app.entities.knowledge_sources.resource import Resource


class Citation(UUIDMixin, TimestampMixin, Base):

    __tablename__ = "citations"

    chat_message_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("chat_session_messages.id"),
        nullable=False,
    ) 

    chunk_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("chunks.id"),
        nullable=False,
        index=True,
    ) 

    resource_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("resources.id"),
        nullable=False,
        index=True,
    )
    citation_order: Mapped[int] = mapped_column(Integer, nullable=False) 

    __table_args__ = (
        UniqueConstraint("chat_message_id", "citation_order"),
    ) 

    message: Mapped["ChatSessionMessage"] = relationship(back_populates="citations")
    chunk: Mapped["Chunk"] = relationship(back_populates="citations")
    resource: Mapped["Resource"] = relationship(back_populates="citations")
