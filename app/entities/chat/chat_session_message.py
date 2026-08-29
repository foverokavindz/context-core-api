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
    ) 

    role: Mapped[MessageRole] = mapped_column(
        SAEnum(
            MessageRole,
            name="message_role",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    ) 

    content: Mapped[str] = mapped_column(Text, nullable=False) 
    chat_session: Mapped["ChatSession"] = relationship(back_populates="messages")
    citations: Mapped[list["Citation"]] = relationship(back_populates="message") 
