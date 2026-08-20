from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.entities import ChatSession


class ChatSessionRepository:
    """Reads and writes `chat_sessions` rows. Does not commit."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, user_id: UUID, title: str | None = None) -> ChatSession:
        """Open a conversation. An unnamed one keeps a NULL title."""

        chat_session = ChatSession(id=uuid4(), user_id=user_id, title=title)
        self.session.add(chat_session)
        self.session.flush()
        return chat_session

    def get_by_id_and_user(
        self, chat_session_id: UUID, user_id: UUID
    ) -> ChatSession | None:
        """Fetch a session only if this user owns it. """
        
        chat_session = self.session.get(ChatSession, chat_session_id)
        if chat_session is None or chat_session.user_id != user_id:
            return None
        return chat_session
