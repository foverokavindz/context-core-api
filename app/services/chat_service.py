import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.entities import ChatSession, MessageRole
from app.models.chat.message import ChatMessage
from app.models.chat.request import CreateChatRequest, SendQueryRequest
from app.models.chat.response import (
    SNIPPET_CHARACTERS,
    AnswerSource,
    CreateChatResponse,
    RetrievalStepTrace,
    RetrievalTrace,
    SendQueryResponse,
)
from app.models.retrieval.access_context import AccessContext
from app.models.retrieval.answer import GeneratedAnswer
from app.models.retrieval.pipeline_result import RetrievalPipelineResult
from app.repository.chat_message_repository import ChatMessageRepository
from app.repository.chat_session_repository import ChatSessionRepository
from app.repository.user_repository import UserRepository
from app.services.retrieval_service import RetrievalService, get_retrieval_service


MAX_HISTORY_MESSAGES = 6

logger = logging.getLogger(__name__)


class ChatService:

    def __init__(
        self, session: Session, retrieval: RetrievalService | None = None
    ) -> None:
        self.session = session
        self.retrieval = retrieval
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

        history = _history(chat_session)

        message = self._record(
            chat_session.id, MessageRole.USER, request.query, "query"
        )
        logger.info(
            "Query of %d characters recorded as message %s on chat session %s",
            len(request.query),
            message.id,
            chat_session.id,
        )

        result = self._answer(request, history)
        answer = result.answer.answer if result.answer else ""

        answer_message = self._record(
            chat_session.id, MessageRole.ASSISTANT, answer, "answer"
        )
        logger.info(
            "Answered message %s with message %s on chat session %s",
            message.id,
            answer_message.id,
            chat_session.id,
        )

        return SendQueryResponse(
            chat_session_id=chat_session.id,
            message_id=message.id,
            answer_message_id=answer_message.id,
            answer=answer,
            sources=_sources(result.answer),
            retrieval=_trace(result),
        )

    def _answer(
        self, request: SendQueryRequest, history: list[ChatMessage]
    ) -> RetrievalPipelineResult:
        """Run the whole pipeline for one question.
        """
        retrieval = self.retrieval or get_retrieval_service()

        return retrieval.start(
            query=request.query,
            access=AccessContext(
                user_id=request.user_id,
                team_id=request.team_id,
                department_id=request.department_id,
            ),
            history=history,
        )

    def _record(
        self, chat_session_id: UUID, role: MessageRole, content: str, what: str
    ):
        """Save query or answer"""
        try:
            message = self.messages.create(chat_session_id, role, content)
            self.session.commit()

        except SQLAlchemyError:
            self.session.rollback()
            logger.exception(
                "Could not record a %s for chat session %s", what, chat_session_id
            )
            raise HTTPException(
                status_code=500, detail=f"The {what} could not be recorded."
            )

        return message


def _history(chat_session: ChatSession) -> list[ChatMessage]:
    """The conversation so far, oldest first, as the pipeline reads it."""

    turns = sorted(chat_session.messages, key=lambda message: message.created_at)

    return [
        ChatMessage(role=turn.role, content=turn.content)
        for turn in turns[-MAX_HISTORY_MESSAGES:]
    ]


def _sources(answer: GeneratedAnswer | None) -> list[AnswerSource]:
    """What the answer was written from, in the order its citations number."""
    if answer is None:
        return []

    return [
        AnswerSource(
            chunk_id=source.chunk_id,
            source=source.source,
            resource_type=source.resource_type,
            resource_title=source.resource_title,
            external_id=source.external_id,
            score=source.score,
            snippet=source.content[:SNIPPET_CHARACTERS],
        )
        for source in answer.sources
    ]


def _trace(result: RetrievalPipelineResult) -> RetrievalTrace:
    """How this answer was arrived at, for reading the pipeline from outside."""

    return RetrievalTrace(
        resolved_query=result.analysis.resolved_query,
        intent=result.analysis.intent,
        retrieval_required=result.analysis.retrieval_required,
        plan_goal=result.plan.goal if result.plan else None,
        steps=[
            RetrievalStepTrace(
                step_id=step.step_id,
                source=step.source,
                goal=step.goal,
                executed_query=step.executed_query,
                result_count=len(step.results),
            )
            for step in (result.execution.steps if result.execution else [])
        ],
    )
