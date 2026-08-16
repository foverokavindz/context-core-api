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
    ) # the assistant message this supported, never the session - a session has many answers and each one cited its own sources. No index of its own, the unique constraint below leads with this column

    chunk_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("chunks.id"),
        nullable=False,
        index=True,
    ) # which exact retrieved text the answer rested on

    resource_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("resources.id"),
        nullable=False,
        index=True,
    )
    citation_order: Mapped[int] = mapped_column(Integer, nullable=False) # the position this source takes in the answer's source list, so [1] and [2] survive a re-read rather than being whatever order the rows came back in

    __table_args__ = (
        UniqueConstraint("chat_message_id", "citation_order"),
    ) # one answer cannot hold two sources at the same position, the same rule chunks apply to chunk_index within a resource

    message: Mapped["ChatSessionMessage"] = relationship(back_populates="citations")
    chunk: Mapped["Chunk"] = relationship(back_populates="citations")
    resource: Mapped["Resource"] = relationship(back_populates="citations")
