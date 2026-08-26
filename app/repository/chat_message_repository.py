from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.entities import ChatSessionMessage, MessageRole


class ChatMessageRepository:
    """Writes `chat_session_messages` rows. Does not commit."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create( self, chat_session_id: UUID, role: MessageRole, content: str) -> ChatSessionMessage:
        """Record one turn. A question and its answer are two rows."""
        
        message = ChatSessionMessage(
            id=uuid4(),
            chat_session_id=chat_session_id,
            role=role,
            content=content,
        )
        self.session.add(message)
        self.session.flush()
        return message
