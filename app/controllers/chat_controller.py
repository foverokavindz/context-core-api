import logging
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db.dependencies import get_db
from app.models.chat.request import CreateChatRequest, SendQueryRequest
from app.models.chat.response import CreateChatResponse, SendQueryResponse
from app.services.chat_service import ChatService
from app.services.retrieval_service import RetrievalService, get_retrieval_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.post(
    "/chats",
    status_code=201,
    response_model=CreateChatResponse,
    summary="Open a chat session",
    response_description="The id of the conversation that was opened.",
)
def create_chat(
    request: CreateChatRequest,
    session: Session = Depends(get_db),
) -> CreateChatResponse:
    """Start a conversation for a user."""
    return ChatService(session).create_chat(request)


@router.post(
    "/chats/{chat_session_id}/query",
    status_code=200,
    response_model=SendQueryResponse,
    summary="Ask a question in a chat session",
    response_description=(
        "The answer, the sources it was written from, and a trace of what the "
        "retrieval pipeline did to arrive at it. The question and the answer "
        "are both stored on the session."
    ),
)
def send_query(
    chat_session_id: UUID,
    request: SendQueryRequest,
    session: Session = Depends(get_db),
    retrieval: RetrievalService = Depends(get_retrieval_service),
) -> SendQueryResponse:

    return ChatService(session, retrieval).send_query(chat_session_id, request)
