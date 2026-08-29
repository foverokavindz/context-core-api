from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.entities.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.entities.chat.chat_session_message import ChatSessionMessage
    from app.entities.organization.user import User


class ChatSession(UUIDMixin, TimestampMixin, Base):

    __tablename__ = "chat_sessions"

    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    ) 

    title: Mapped[str | None] = mapped_column(String(255), nullable=True) 

    user: Mapped["User"] = relationship(back_populates="chat_sessions")
    messages: Mapped[list["ChatSessionMessage"]] = relationship(back_populates="chat_session") 
