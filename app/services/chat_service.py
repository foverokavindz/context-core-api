"""Opens chat sessions and takes in the questions asked in them.
"""

import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.entities import MessageRole
from app.models.chat.request import CreateChatRequest, SendQueryRequest
from app.models.chat.response import CreateChatResponse, SendQueryResponse
from app.repository.chat_message_repository import ChatMessageRepository
from app.repository.chat_session_repository import ChatSessionRepository
from app.repository.user_repository import UserRepository

logger = logging.getLogger(__name__)


class ChatService:

    def __init__(self, session: Session) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.chat_sessions = ChatSessionRepository(session)
        self.messages = ChatMessageRepository(session)

    def create_chat(self, request: CreateChatRequest) -> CreateChatResponse:
        
        if self.users.get_by_id(request.user_id) is None:
            raise HTTPException(
                status_code=404,
                detail="user_id does not match an existing user.",
            )

        try:
            chat_session = self.chat_sessions.create(request.user_id, request.title)
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            logger.exception("Could not create a chat session")
            raise HTTPException(
                status_code=500, detail="The chat session could not be created."
            )

        logger.info(
            "Chat session %s opened for user %s", chat_session.id, request.user_id
        )
        return CreateChatResponse(chat_session_id=chat_session.id)

    def send_query(
        self, chat_session_id: UUID, request: SendQueryRequest
    ) -> SendQueryResponse:
        chat_session = self.chat_sessions.get_by_id_and_user(chat_session_id, request.user_id )
 
        if chat_session is None:
            raise HTTPException(status_code=404, detail="Chat session not found.")

        try:
            message = self.messages.create(chat_session.id, MessageRole.USER, request.query)
            self.session.commit()

        except SQLAlchemyError:
            self.session.rollback()
            logger.exception(
                "Could not record a query for chat session %s", chat_session_id
            )
            raise HTTPException(
                status_code=500, detail="The query could not be recorded."
            )

        logger.info(
            "Query of %d characters recorded as message %s on chat session %s",
            len(request.query),
            message.id,
            chat_session.id,
        )
        return SendQueryResponse(
            chat_session_id=chat_session.id, message_id=message.id
        )
