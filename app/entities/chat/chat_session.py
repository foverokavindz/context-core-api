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
    ) # a session belongs to one person and is not shared. Indexed because "my conversations" is the only way this table gets listed

    title: Mapped[str | None] = mapped_column(String(255), nullable=True) # nullable, because a session exists from the moment it is opened and nothing has named it yet. Whether the name is typed or derived from the first question is not this table's decision

    user: Mapped["User"] = relationship(back_populates="chat_sessions")
    messages: Mapped[list["ChatSessionMessage"]] = relationship(back_populates="chat_session") # the conversation in insertion order, which created_at recovers - the schema stores no sequence number of its own
