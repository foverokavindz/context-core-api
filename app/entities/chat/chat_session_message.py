from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Text, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.entities.base import Base, TimestampMixin, UUIDMixin
from app.entities.chat.message_role import MessageRole

if TYPE_CHECKING:
    from app.entities.chat.chat_session import ChatSession
    from app.entities.chat.citation import Citation


class ChatSessionMessage(UUIDMixin, TimestampMixin, Base):

    __tablename__ = "chat_session_messages"

    chat_session_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("chat_sessions.id"),
        nullable=False,
        index=True,
    ) # indexed rather than covered by a constraint: nothing about a message is unique within its session, and "replay this conversation" is the query

    role: Mapped[MessageRole] = mapped_column(
        SAEnum(
            MessageRole,
            name="message_role",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    ) # not indexed - a session holds a handful of rows and filtering them by role is not a query worth an index on the whole table

    content: Mapped[str] = mapped_column(Text, nullable=False) # one turn, and one turn only. A question and its answer are two rows, which is what lets citations point at the answer and not at the pair

    chat_session: Mapped["ChatSession"] = relationship(back_populates="messages")
    citations: Mapped[list["Citation"]] = relationship(back_populates="message") # empty on a USER row and on any ASSISTANT row that answered without retrieving anything, which is a real case rather than a missing one
